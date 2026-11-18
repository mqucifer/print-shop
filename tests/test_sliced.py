"""A hand-sliced file prints as it is, is kept in the ledger, and prints again
(mqucifer/print-shop#164).

Everything is made here (`tests/sliced_fixtures.py`): the G-code, the plate file, the ledger and
the inventory. The printer is `FramePrinter`, the broker a list, and the slicer, the questions,
the proposal, the review and the print loop each fail the test if the sliced path reaches them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from print_shop import sliced
from print_shop.core.contracts import ungated_edges
from print_shop.core.mqtt import command_topic, gate_topic
from print_shop.desk import Desk
from print_shop.desk.page import job_page
from print_shop.inventory import Inventory, Material
from print_shop.ledger import SCHEMA_VERSION, Answer, Ledger, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.graph import build_graph, graph_edges
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup import lessons as print_lessons
from print_shop.setup.loop import PrintLoop
from print_shop.slicer.naming import job_id_from_filename
from print_shop.slicer.store import Store, choose
from tests.fakes import STORE, FramePrinter, MemoryBridge
from tests.sliced_fixtures import (
    BY_HAND,
    FILAMENT,
    OBJECTS,
    gcode_text,
    plate_file,
    project_file,
    write_gcode,
)
from tests.test_job_e2e import CFG
from tests.test_preset_slices import CASES as PRESET_CASES
from tests.test_preset_slices import RECORDED
from tests.test_runtime import settings  # noqa: F401  (the fixture)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _never(what: str) -> Any:
    def reached(*args: object, **kwargs: object) -> Any:
        raise AssertionError(f"the sliced path reached {what}")

    return reached


def _material(
    path: Path, *, type: str = "PETG", variety: str = FILAMENT, tuning: str | None = None,
) -> Material:
    """An inventory in the ledger's file with one printer, a 0.4 mm nozzle and a loaded spool."""
    with Inventory(path) as inventory:
        if not inventory.printers():
            inventory.add_printer("cc1", "Elegoo Centauri Carbon")
            inventory.fit(0.4)
        spool = inventory.add_spool(manufacturer="Elegoo", type=type, variety=variety,
                                    colour="clear", grams_left=900, tuning=tuning)
        inventory.load(spool.id)
        return inventory.material()


# --- the file, read ----------------------------------------------------------------------------


def test_a_gcodes_config_block_is_read_to_settings_by_profile(tmp_path: Path) -> None:
    path = write_gcode(tmp_path)
    file = sliced.read(path)

    assert file.kind == "gcode" and file.plate is None
    assert file.sha256 == _sha(path.read_bytes())
    assert file.process == BY_HAND["print_settings_id"], "the name Orca wrote"
    assert file.filament == "Elegoo PETG @ECC"
    assert file.machine.startswith("Elegoo Centauri Carbon 0.4 nozzle")
    assert (file.filament_type, file.nozzle_mm) == ("PETG", 0.4)
    assert (file.print_time_s, file.filament_g, file.layer_count) == (439, 1.85, 6)
    assert file.objects == OBJECTS

    by_kind = sliced.by_kind(file.config, Store(STORE))
    assert by_kind["process"]["sparse_infill_density"] == "40%"
    assert by_kind["filament"]["nozzle_temperature"] == "255"
    assert by_kind["machine"]["nozzle_diameter"] == "0.4"
    assert sum(len(v) for v in by_kind.values()) == len(file.config), "every setting is kept"


def test_a_file_with_no_config_block_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bare.gcode"
    path.write_text("G28\nG1 X10 Y10 F3000\n")
    with pytest.raises(sliced.SlicedError, match="config block"):
        sliced.read(path)


def test_a_plain_gcode_carries_no_per_object_overrides_and_says_so(tmp_path: Path) -> None:
    file = sliced.read(write_gcode(tmp_path))
    assert file.overrides is None, "not an empty list: the file can't carry them"


def test_a_plate_file_gives_the_same_gcode_and_each_objects_overrides(tmp_path: Path) -> None:
    gcode = write_gcode(tmp_path)
    plate = plate_file(tmp_path)
    file = sliced.read(plate)

    assert file.kind == "plate" and file.plate == 1
    assert file.sha256 == _sha(gcode.read_bytes()), "the G-code inside, byte for byte"
    assert file.container_sha256 == _sha(plate.read_bytes())
    assert file.gcode_bytes() == gcode.read_bytes()
    assert file.overrides is not None
    found = {o.name: o.settings for o in file.overrides}
    assert found == {
        OBJECTS[0]: {"layer_height": "0.12"},
        OBJECTS[1]: {"sparse_infill_density": "25%", "wall_loops": "5"},
    }, "each named as the G-code names it, with only what was overridden"


def test_a_gcodes_overrides_can_be_read_from_the_project_it_was_sliced_from(
    tmp_path: Path,
) -> None:
    gcode = write_gcode(tmp_path)
    project = project_file(tmp_path)
    file = sliced.read(gcode, project=project)

    assert file.kind == "gcode" and file.sha256 == _sha(gcode.read_bytes())
    assert file.overrides is not None
    assert {o.name: o.settings for o in file.overrides} == {
        OBJECTS[0]: {"layer_height": "0.12"},
        OBJECTS[1]: {"sparse_infill_density": "25%", "wall_loops": "5"},
    }
    assert project.name in file.overrides_from and "by name" in file.overrides_from

    other = project_file(tmp_path, "other.3mf", overrides={"bracket.stl": {"wall_loops": "6"}})
    with pytest.raises(sliced.SlicedError, match="is it the project this was sliced from"):
        sliced.read(gcode, project=other)
    with pytest.raises(sliced.SlicedError, match="isn't an Orca project"):
        sliced.read(gcode, project=gcode)


def test_a_plate_file_with_several_plates_needs_one_named(tmp_path: Path) -> None:
    plate = plate_file(tmp_path, plates=2)
    with pytest.raises(sliced.SlicedError, match="--plate"):
        sliced.read(plate)
    assert sliced.read(plate, plate=2).plate == 2


def test_a_project_with_no_gcode_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "project.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
    with pytest.raises(sliced.SlicedError, match="holds no G-code"):
        sliced.read(path)


def test_settings_that_differ_from_the_filaments_tuning_are_listed(tmp_path: Path) -> None:
    file = sliced.read(write_gcode(tmp_path))
    material = _material(tmp_path / "ledger.sqlite")
    compared = sliced.compare(file.config, material, Store(STORE), bed="Textured PEI Plate")

    assert compared.filament == "Elegoo PETG @ECC"
    differs = {d.key: (d.kind, d.tuning, d.file) for d in compared.differs}
    assert differs["sparse_infill_density"] == ("process", "15%", "40%")
    assert differs["nozzle_temperature"][0] == "filament"
    assert differs["nozzle_temperature"][2] == "255"
    assert differs["print_settings_id"][2] == BY_HAND["print_settings_id"]
    assert set(differs) == set(BY_HAND), "and nothing the person didn't change"


@pytest.mark.parametrize(("label", "filament"), [(c[0], c[2]) for c in PRESET_CASES])
def test_a_slice_orca_made_with_exactly_the_tuning_differs_from_it_in_nothing(
    label: str, filament: str, tmp_path: Path
) -> None:
    """The four slices the real Orca made with the store's own profiles, standard and tuned
    (`tests/data/recorded/presets/`): nothing was changed by hand, so nothing is listed. What
    Orca rewrites in a config block isn't taken for a change."""
    store = Store(STORE)
    profile = choose(store, nozzle_mm=0.4, filament=filament).filament
    tuning = profile.name if profile.standard is not None else None
    material = _material(tmp_path / "ledger.sqlite", type=profile.filament_type,
                         variety=profile.variety, tuning=tuning)
    file = sliced.read(RECORDED / f"cube-{label}.gcode")
    compared = sliced.compare(file.config, material, store, bed="Textured PEI Plate")

    assert compared.filament == file.filament
    assert [d.line() for d in compared.differs] == []
    assert sliced.loaded_mismatch(file, material, store) == []


def test_a_setting_the_tuning_doesnt_state_is_listed_when_orca_says_it_was_changed(
    tmp_path: Path,
) -> None:
    by_hand = {"fuzzy_skin": "external", "wall_loops": "4",
               "different_settings_to_system": '"fuzzy_skin;wall_loops";"";""'}
    file = sliced.read(write_gcode(tmp_path, by_hand=by_hand))
    compared = sliced.compare(file.config, _material(tmp_path / "ledger.sqlite"),
                              Store(STORE), bed="Textured PEI Plate")

    differs = {d.key: (d.tuning, d.file) for d in compared.differs}
    assert differs == {"fuzzy_skin": ("Orca's default", "external"), "wall_loops": ("2", "4")}


def test_the_loaded_filament_is_checked_against_the_one_the_file_names(tmp_path: Path) -> None:
    file = sliced.read(write_gcode(tmp_path))
    petg = _material(tmp_path / "a.sqlite")
    pla = _material(tmp_path / "b.sqlite", type="PLA", variety="Elegoo PLA")
    rapid = _material(tmp_path / "c.sqlite", variety="Elegoo Rapid PETG")
    store = Store(STORE)

    assert sliced.loaded_mismatch(file, petg, store) == []
    (said,) = sliced.loaded_mismatch(file, pla, store)
    assert "PLA" in said and "PETG" in said
    (said,) = sliced.loaded_mismatch(file, rapid, store)
    assert "Elegoo Rapid PETG" in said and "Elegoo PETG" in said, "the same type, another variety"

    wide = sliced.read(write_gcode(tmp_path, "wide.gcode",
                                   by_hand={"nozzle_diameter": "0.6"}))
    (said,) = sliced.loaded_mismatch(wide, petg, store)
    assert "0.6" in said and "0.4" in said


# --- the job, end to end against the fakes -----------------------------------------------------


class Rig:
    """A daemon with a fake printer and a fixture ledger, whose slicer, questions, proposal,
    review and print loop fail the test if they're reached."""

    def __init__(self, tmp_path: Path) -> None:
        self.bridge = MemoryBridge()
        self.printer = FramePrinter()
        self.jobs = tmp_path / "jobs"
        self.jobs.mkdir()
        self.ledger_path = tmp_path / "ledger.sqlite"
        self.ledger = Ledger(self.ledger_path)
        self.material = _material(self.ledger_path)
        self.shown: list[str] = []
        self.daemon = Daemon(
            cfg=CFG,
            profiles=None,
            workdir=self.jobs,
            printer=self.printer,
            slicer=_never("the slicer"),
            bridge=lambda cfg: self.bridge,
            ledger=self.ledger,
            proposer=Proposer(ask=_never("the proposal"), ask_questions=_never("the questions"),
                              lessons=_never("the lessons")),
            asker=_never("the intake"),
            reviewer=_never("the review"),
            loop=PrintLoop(chat=_never("the print loop"), person=_never("the loop's question")),
            show=self.shown.append,
            store=Store(STORE),
        )

    def job(self, path: Path, **kwargs: Any) -> sliced.SlicedJob:
        file = sliced.read(path)
        compared = sliced.compare(file.config, self.material, Store(STORE),
                                  bed="Textured PEI Plate")
        return sliced.SlicedJob.of(file, material=self.material, compared=compared, **kwargs)

    def press(self, decision: str) -> None:
        self.bridge.deliver(command_topic(CFG), {"decision": decision})

    def printed(self, job: sliced.SlicedJob) -> str:
        """The job, approved and started; its id."""
        job_id = self.daemon.submit_sliced(job)
        self.daemon.wait_for_gate(job_id, timeout_s=30)
        self.press("approve")
        result = self.daemon.wait_for_finish(job_id, timeout_s=30)
        assert (result.approved, result.printed, result.error) == (True, True, None)
        return job_id


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    with r.daemon:
        yield r
    r.ledger.close()


def test_a_sliced_job_is_sent_as_it_is_with_no_question_or_proposal(
    rig: Rig, tmp_path: Path
) -> None:
    source = plate_file(tmp_path)
    sent = sliced.read(source).gcode_bytes()
    job_id = rig.daemon.submit_sliced(rig.job(source))
    rig.daemon.wait_for_gate(job_id, timeout_s=30)

    gate = rig.bridge.retained_json(gate_topic(CFG))
    assert gate["pending"] is True and gate["job_id"] == job_id
    assert gate["print_time_s"] == 439, "Orca's own estimate, from the file"
    assert "sliced by hand" in gate["orientation_rationale"]
    assert "before layer 4" in gate["pause"], "the pause written in the file"
    assert rig.printer.calls == [], "nothing touches the printer before the gate"
    report = "\n".join(rig.shown)
    assert "layers: 6" in report and "nothing is proposed" in report

    rig.press("approve")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)
    assert (result.approved, result.printed, result.error) == (True, True, None)
    assert rig.printer.verbs == ["snapshot", "upload", "start"]
    uploaded = Path(rig.printer.calls[1][1])
    assert uploaded.read_bytes() == sent, "the file's bytes are what's sent"
    assert job_id_from_filename(uploaded.name) == job_id
    assert uploaded.parent == rig.jobs / job_id

    (row,) = rig.ledger.rows()
    assert row.job_id == job_id and row.started_at is not None
    assert row.stl_name == source.name and row.stl_sha256 == _sha(sent)
    assert row.orientation_source == "sliced"
    assert (row.intake, row.proposal, row.corrections, row.features) == (None, None, (), None)
    assert row.material == rig.material
    assert row.filament == "Elegoo PETG @ECC"
    kept = row.sliced
    assert kept is not None and kept.sliced == "by hand" and kept.reprint_of is None
    assert kept.gcode_sha256 == _sha(sent) and kept.container_sha256 == _sha(source.read_bytes())
    assert kept.settings["process"]["sparse_infill_density"] == "40%"
    assert kept.compared is not None
    assert {d.key for d in kept.compared.differs} == set(BY_HAND)
    assert kept.overrides is not None
    assert {o.name: o.settings for o in kept.overrides}[OBJECTS[0]] == {"layer_height": "0.12"}
    assert (kept.print_time_s, kept.filament_g) == (439, 1.85)
    assert row.reading is not None and len(row.reading.layers) == 6
    assert all(f.change is None for f in row.flags or ()), "a reading, never a proposal"
    assert rig.ledger.query("SELECT * FROM job_lessons") == [], "no lesson was given to it"


def test_a_file_changed_after_it_was_taken_is_refused_before_upload(
    rig: Rig, tmp_path: Path
) -> None:
    job_id = rig.daemon.submit_sliced(rig.job(write_gcode(tmp_path)))
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    (taken,) = (rig.jobs / job_id).glob("*.gcode")
    with taken.open("ab") as f:
        f.write(b"G1 X0 Y0 ; one more move\n")

    rig.press("approve")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert result.printed is False
    assert result.error is not None and "changed since it was taken" in result.error
    assert "upload" not in rig.printer.verbs and "start" not in rig.printer.verbs
    assert rig.ledger.rows() == []


def test_the_file_is_taken_at_submit_so_a_later_edit_of_the_source_isnt_sent(
    rig: Rig, tmp_path: Path
) -> None:
    source = write_gcode(tmp_path)
    before = source.read_bytes()
    job_id = rig.daemon.submit_sliced(rig.job(source))
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    source.write_bytes(before + b"; edited in Orca again\n")
    rig.press("approve")
    rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert Path(rig.printer.calls[1][1]).read_bytes() == before, "what the gate showed"


def test_a_source_changed_between_reading_and_taking_is_refused(
    rig: Rig, tmp_path: Path
) -> None:
    source = write_gcode(tmp_path)
    job = rig.job(source)
    source.write_bytes(source.read_bytes() + b"; changed\n")
    job_id = rig.daemon.submit_sliced(job)
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert result.error is not None and "changed since it was read" in result.error
    assert rig.printer.calls == [] and rig.daemon.pending_gate() is None


def test_a_reprint_sends_the_same_bytes_and_is_recorded_against_its_original(
    rig: Rig, tmp_path: Path
) -> None:
    source = plate_file(tmp_path)
    first = rig.printed(rig.job(source))
    rig.printer.calls.clear()
    rig.printer._started = None  # the first print is over; the bed is clear

    again = sliced.again(rig.ledger, rig.jobs, first)
    assert again.reprint_of == first and again.name == source.name
    second = rig.printed(sliced.SlicedJob.of(
        sliced.read(again.path), material=rig.material, name=again.name,
        reprint_of=again.reprint_of, overrides=again.overrides,
        overrides_from=again.overrides_from,
    ))

    one, two = rig.ledger.rows()
    assert (one.job_id, two.job_id) == (first, second)
    assert two.sliced is not None and one.sliced is not None
    assert two.sliced.reprint_of == first, "a new job, marked as a reprint of the old one"
    assert two.sliced.gcode_sha256 == one.sliced.gcode_sha256
    assert Path(rig.printer.calls[1][1]).read_bytes() == sliced.read(source).gcode_bytes()
    assert two.sliced.overrides == one.sliced.overrides, "kept from the original's plate file"
    assert f"job {first}" in two.sliced.overrides_from
    assert two.stl_name == one.stl_name
    assert rig.ledger.reprints(first) == [second]


def test_a_reprint_is_refused_once_the_kept_file_has_changed_or_gone(
    rig: Rig, tmp_path: Path
) -> None:
    first = rig.printed(rig.job(write_gcode(tmp_path)))
    (kept,) = (rig.jobs / first).glob("*.gcode")
    kept.write_bytes(kept.read_bytes() + b"; touched\n")
    with pytest.raises(sliced.SlicedError, match="has changed since it printed"):
        sliced.again(rig.ledger, rig.jobs, first)
    kept.unlink()
    with pytest.raises(sliced.SlicedError, match="isn't there"):
        sliced.again(rig.ledger, rig.jobs, first)


def test_only_a_hand_sliced_job_prints_again_this_way(rig: Rig) -> None:
    with pytest.raises(sliced.SlicedError, match="isn't in the ledger"):
        sliced.again(rig.ledger, rig.jobs, "no-such-job")


def test_a_sliced_jobs_real_time_is_kept_beside_orcas_estimate(
    rig: Rig, tmp_path: Path
) -> None:
    job_id = rig.printed(rig.job(write_gcode(tmp_path)))
    rig.ledger.record_ended(job_id, 9)
    with rig.ledger.writing() as db:  # the watcher's two sightings, nine minutes apart
        db.execute("UPDATE prints SET started_at = ?, ended_at = ? WHERE job_id = ?",
                   ("2026-11-17T10:01:00+00:00", "2026-11-17T10:10:00+00:00", job_id))
    row = rig.ledger.get(job_id)
    assert row is not None and row.real_time_s == 540, "from the start seen to the end seen"

    said = describe(row)
    assert "sliced         by hand" in said
    assert "7 min estimated by Orca" in said and "9 min from its start to its end" in said
    assert "the printer doesn't report" in said, "the filament used is never guessed"
    assert "sparse_infill_density" in said and "15% -> 40%" in said


# --- lessons ------------------------------------------------------------------------------------


def test_an_outcome_from_a_sliced_job_is_a_candidate_keyed_by_the_files_own_settings(
    rig: Rig, tmp_path: Path
) -> None:
    job_id = rig.printed(rig.job(plate_file(tmp_path)))
    rig.ledger.record_ended(job_id, 9)
    rig.ledger.record_answer(job_id, Answer(result=Result.FAILED, symptom=Symptom.STRINGING,
                                            note="tile B strung between the walls"))

    (lesson,) = rig.ledger.query("SELECT * FROM print_lessons")
    assert lesson["status"] == "candidate" and lesson["drawn_from"] == print_lessons.OUTCOME
    (evidence,) = rig.ledger.query("SELECT detail FROM print_lesson_evidence")
    detail = json.loads(evidence["detail"])
    assert detail["sliced"] == "by hand"
    assert detail["settings"]["differs"]["sparse_infill_density"] == "40%"
    assert detail["settings"]["objects"][OBJECTS[1]]["wall_loops"] == "5"
    assert detail["traits"] == [], "no part was measured, so it's keyed to no shape"
    assert rig.ledger.query("SELECT * FROM job_lessons") == []


# --- the ledger from before ---------------------------------------------------------------------


def test_a_ledger_from_before_gains_the_table_on_open_and_reads_without_it(
    tmp_path: Path,
) -> None:
    from tests.test_ledger import record

    path = tmp_path / "ledger.sqlite"
    with Ledger(path) as ledger:
        ledger.record_start_requested(record())
        with ledger.writing() as db:
            db.execute("DROP TABLE sliced_prints")  # as a file written before this was

    old = Ledger.read_only(path)
    try:
        (row,) = old.rows()
        assert row.sliced is None, "read as it is, with nothing made"
        assert old.reprints("job-1") == []
    finally:
        old.close()

    with Ledger(path) as ledger:
        assert ledger.query("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
        assert ledger.query("SELECT count(*) AS n FROM sliced_prints")[0]["n"] == 0
        (row,) = ledger.rows()
        assert row.sliced is None and row.stl_name == "cube.stl"


# --- the graph ----------------------------------------------------------------------------------


def test_the_sliced_path_reaches_the_printer_only_through_the_gate(tmp_path: Path) -> None:
    app = build_graph(None, outdir_root=tmp_path)
    edges = graph_edges(app)
    assert ungated_edges(edges) == []
    assert ("sliced", "gate.before_print") in edges
    assert {dst for src, dst in edges if src == "sliced"} == {"gate.before_print"}


# --- the desk -----------------------------------------------------------------------------------


def test_the_desk_shows_a_sliced_job_and_the_command_that_prints_it_again(
    rig: Rig, tmp_path: Path
) -> None:
    first = rig.printed(rig.job(plate_file(tmp_path)))
    desk = Desk(rig.ledger_path, rig.jobs)
    record = desk.job(first)
    assert record is not None
    page = job_page(record)

    assert f"uv run python scripts/print_job.py --again {first}" in page
    assert "Sliced by hand" in page and "--answers" not in page
    assert "sparse_infill_density" in page and "tileB.stl_id_1_copy_0" in page
    assert "It doesn&#x27;t arm the printer" in page or "It doesn't arm the printer" in page
    assert "<h2>Pose</h2>" not in page, "nothing was turned"


# --- the command --------------------------------------------------------------------------------


def _script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Shop:
    """`print_job.py` with its run built on the fakes: the real daemon, a fake printer."""

    def __init__(self, tmp_path: Path, settings: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
        self.script = _script("print_job")
        self.settings = settings
        self.jobs = tmp_path / "jobs"
        self.jobs.mkdir()
        self.ledger_path = tmp_path / "ledger.sqlite"
        self.bridge = MemoryBridge()
        self.printer = FramePrinter()
        self.built: list[dict[str, Any]] = []
        monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
        monkeypatch.setattr(self.script, "WORK_DIR", self.jobs)
        monkeypatch.setattr(self.script, "build_run", self._build_run)
        monkeypatch.setattr("builtins.input", _never("a question"))

    def _build_run(self, **kwargs: Any) -> SimpleNamespace:
        self.built.append(kwargs)
        ledger = Ledger(self.ledger_path)
        daemon = Daemon(cfg=CFG, profiles=None, workdir=self.jobs, printer=self.printer,
                        slicer=_never("the slicer"), bridge=lambda cfg: self.bridge,
                        ledger=ledger, show=kwargs.get("show"), store=kwargs.get("store"))
        return SimpleNamespace(daemon=daemon, ledger=ledger)

    def run(self, *args: str, press: str = "reject") -> int:
        """The command, with the gate pressed once it opens."""
        done = threading.Event()

        def presser() -> None:
            while not done.is_set():
                raw = self.bridge.retained.get(gate_topic(CFG))  # absent until the daemon's up
                if raw is not None and json.loads(raw).get("pending"):
                    self.bridge.deliver(command_topic(CFG), {"decision": press})
                    return
                time.sleep(0.05)

        thread = threading.Thread(target=presser, daemon=True)
        thread.start()
        try:
            return int(self.script.main([*args, "--settings", str(self.settings)]))
        finally:
            done.set()
            thread.join(timeout=5)


@pytest.fixture
def shop(tmp_path: Path, settings: Path, monkeypatch: pytest.MonkeyPatch) -> Shop:  # noqa: F811
    return Shop(tmp_path, settings, monkeypatch)


def test_print_job_sends_a_sliced_file_unarmed_to_the_gate_and_asks_nothing(
    shop: Shop, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _material(shop.ledger_path)
    source = plate_file(tmp_path)
    assert shop.run("--sliced", str(source)) == 0

    out = capsys.readouterr().out
    assert "Sliced by hand" in out and "Not armed" in out
    assert "No questions, no proposal, no lesson applied, nothing sliced again" in out
    assert "From the inventory: cc1 with a 0.4 mm nozzle" in out
    assert "sparse_infill_density" in out and "15% -> 40%" in out, "what differs from the tuning"
    assert f"{OBJECTS[0]}: layer_height=0.12" in out, "each object's overrides"
    assert "layers: 6" in out and "nothing is proposed" in out, "the slice's report"
    assert "At the approval gate" in out and "SWAP" in out
    (built,) = shop.built
    assert built["printer"] is None and built["profiles"] is None
    assert built["asker"] is None and built["reviewer"] is None
    assert shop.printer.calls == []
    (log,) = shop.jobs.glob("*/job.log")
    assert f"part: {source.resolve()}" in log.read_text()


def test_print_job_refuses_a_sliced_file_for_another_filament_unless_told(
    shop: Shop, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _material(shop.ledger_path, type="PLA", variety="Elegoo PLA")
    source = write_gcode(tmp_path)

    assert shop.run("--sliced", str(source)) == 2
    err = capsys.readouterr().err
    assert "PLA" in err and "PETG" in err and "--loaded-ok" in err
    assert shop.built == [], "refused before anything was built or sent"

    assert shop.run("--sliced", str(source), "--loaded-ok") == 0
    assert "printing with what's loaded, as you said (--loaded-ok)" in capsys.readouterr().out


def test_print_job_prints_a_past_sliced_job_again_and_refuses_another_filament(
    shop: Shop, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _material(shop.ledger_path)
    source = plate_file(tmp_path)
    monkeypatch.setattr(shop.script, "_watch", lambda run, job_id: 0)  # not until its outcome
    assert shop.run("--sliced", str(source), press="approve") == 0
    with Ledger(shop.ledger_path) as ledger:
        (first,) = ledger.rows()
        ledger.record_ended(first.job_id, 9)
    shop.printer._started = None
    capsys.readouterr()

    assert shop.run("--again", first.job_id, press="approve") == 0
    out = capsys.readouterr().out
    assert f"A reprint of job {first.job_id}" in out
    with Ledger(shop.ledger_path) as ledger:
        _, second = ledger.rows()
    assert second.sliced is not None and second.sliced.reprint_of == first.job_id
    assert second.sliced.overrides == first.sliced.overrides  # type: ignore[union-attr]
    assert second.stl_name == source.name

    _material(shop.ledger_path, type="PLA", variety="Elegoo PLA")  # another spool is loaded
    built = len(shop.built)
    assert shop.run("--again", first.job_id) == 2
    err = capsys.readouterr().err
    assert "PLA" in err and "--loaded-ok" in err
    assert len(shop.built) == built
    assert shop.run("--again", first.job_id, "--loaded-ok", press="reject") == 0

    assert shop.run("--again", "no-such-job") == 2
    assert "isn't in the ledger" in capsys.readouterr().err


def test_print_job_takes_one_of_a_part_a_sliced_file_or_a_past_job(
    shop: Shop, tmp_path: Path
) -> None:
    source = write_gcode(tmp_path)
    with pytest.raises(SystemExit):
        shop.run(str(tmp_path / "part.stl"), "--sliced", str(source))
    with pytest.raises(SystemExit):
        shop.run("--sliced", str(source), "--again", "some-job")
    with pytest.raises(SystemExit):
        shop.run("--sliced", str(tmp_path / "missing.gcode"))


def test_the_fixture_is_the_shape_the_readers_read() -> None:
    """The fixture's own check: Orca's header, totals and config block, as the parsers want."""
    text = gcode_text()
    assert text.startswith("; HEADER_BLOCK_START")
    assert "; CONFIG_BLOCK_START" in text and text.rstrip().endswith("; CONFIG_BLOCK_END")
