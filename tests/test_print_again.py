"""A finished run prints again, as it was or with a small change (mqucifer/print-shop#216).

Every service is a fake and the shop is made up, as in `test_queue.py`: three spools, a made-up
printer, the recorded cube slice, whose config block echoes the profiles it's given
(`fakes.echo_settings`), so what a slice was given is read back from its G-code.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import trimesh

from print_shop import reprint, sliced
from print_shop.core.mqtt import gate_topic
from print_shop.core.state import SliceResult
from print_shop.inventory import Material
from print_shop.ledger import Answer, Ledger, PrintRecord, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.kept import Waits
from print_shop.orchestrator.print_queue import Adding, Preparing, PrintQueue
from print_shop.queue_terminal import Runs, serve
from print_shop.runtime import job_profiles, profiles_for
from print_shop.setup import Proposer
from print_shop.setup import lessons as print_lessons
from print_shop.setup.proposal import Change, Proposal, Source, apply
from print_shop.setup.review import Review
from print_shop.setup.traits import traits
from print_shop.slicer.naming import name_gcode
from print_shop.slicer.orca import (
    BedType,
    SlicerProfiles,
    parse_config_block,
    slice_model,
    with_bed_type,
)
from print_shop.slicer.store import Store, choose
from tests.fakes import STORE
from tests.sliced_fixtures import write_gcode
from tests.test_graph import needs_orca
from tests.test_job_e2e import CFG
from tests.test_queue import Shop, start_offered, wait_until
from tests.test_review import _model
from tests.test_runtime import settings  # noqa: F401  (the fixture)
from tests.test_sliced import _never, _script
from tests.test_waits_for_you import _top_question

PEI = BedType.TEXTURED_PEI


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sliced_for(path: Path, material: Material | None) -> sliced.SlicedJob:
    return sliced.SlicedJob.of(sliced.read(path), material=material)


class Again(Shop):
    """The queue's made-up shop, taking files sliced by hand and prints again too."""

    def queue(self, daemon: Daemon, at_once: int = 2) -> PrintQueue:
        return PrintQueue(
            daemon=daemon, workdir=self.work, ledger=self.ledger,
            profiles_for=lambda material, into: job_profiles(
                material, into, bed=PEI, store_root=self.store),
            sliced_for=_sliced_for,
            preparing=Preparing(slicer=self.slicer,
                                proposer=Proposer(ask=_model, ask_questions=_top_question),
                                store=Store(self.store)),
            at_once=at_once,
        )

    def find(self, job_id: str) -> reprint.Reprint:
        return reprint.find(self.ledger, self.work, job_id, store_root=self.store, bed=PEI,
                            parts=self.tmp / "parts")

    def uploaded(self) -> Path:
        return Path([path for verb, path in self.printer.calls if verb == "upload"][-1])

    def printed(self, daemon: Daemon, queue: PrintQueue, adding: Adding,
                result: Result = Result.SUCCESS, symptom: Symptom = Symptom.NONE,
                note: str = "") -> str:
        """`adding` through the queue to its outcome: its questions and review where it has
        them, Start on the card, the end, and the person's answer."""
        added, refused = queue.add([adding])
        assert refused == []
        job_id = added[0]
        if adding.again is None and not adding.path.name.endswith(".gcode"):
            self.ready(queue, job_id)
        wait_until(lambda: self.waits(job_id) is Waits.READY or self.offered() == job_id)
        start_offered(self, daemon, job_id)
        self.end_print(job_id)
        self.ledger.record_answer(job_id, Answer(result=result, symptom=symptom, note=note))
        return job_id


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[Again]:
    found = Again(tmp_path)
    yield found
    found.ledger.close()


@pytest.fixture
def running(shop: Again) -> Iterator[tuple[Daemon, PrintQueue]]:
    with ExitStack() as stack:
        daemon = stack.enter_context(shop.daemon())
        queue = stack.enter_context(shop.queue(daemon))
        yield daemon, queue


# --- print again, as it was ----------------------------------------------------------------------


def test_a_run_print_shop_sliced_prints_again_with_the_same_bytes_linked_to_it(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")), note="a good one")
    sent = shop.uploaded()
    row = shop.ledger.get(first)
    assert row is not None and row.gcode_sha256 == _sha(sent), "its G-code's hash is kept"
    kept = shop.work / first / reprint.KEPT_DIR
    assert {p.name for p in kept.iterdir()} >= {"part.stl", "machine.json", "process.json",
                                                "filament.json", reprint.KEPT_JSON}
    assert _sha(kept / "part.stl") == row.stl_sha256, "a copy of its part"
    sliced_before = len(shop.slicer.calls)

    run = shop.find(first[:8])
    assert run.gcode == sent and run.unchecked == ()
    found = run.as_it_was()
    second = shop.printed(daemon, queue, Adding(found.path, again=found))

    assert _sha(shop.uploaded()) == _sha(sent), "the same bytes are sent"
    assert shop.uploaded() != sent, "under its own job's name"
    assert len(shop.slicer.calls) == sliced_before, "nothing is sliced again"
    again = shop.ledger.get(second)
    assert again is not None and again.sliced is not None
    assert again.sliced.reprint_of == first and again.sliced.sliced == sliced.AGAIN
    assert again.sliced.reprinted is not None and again.sliced.reprinted.changes == ()
    assert shop.ledger.reprints(first) == [second], "the ledger links it to its original"
    assert again.stl_name == "bracket.stl" and again.rotation_deg == row.rotation_deg
    assert again.orientation_rationale.startswith(f"a print again of job {first}, as it was")
    assert again.result is Result.SUCCESS and again.note == "", "its own outcome"
    assert again.intake is None and again.proposal is None, "nothing asked or proposed"


def test_a_hand_sliced_run_prints_again_with_the_same_bytes(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(write_gcode(shop.tmp)))
    sent = shop.uploaded()

    run = shop.find(first)
    assert run.no_change.startswith("it was sliced by hand")
    found = run.as_it_was()
    assert found.sliced_as == sliced.BY_HAND
    second = shop.printed(daemon, queue, Adding(found.path, again=found))

    assert _sha(shop.uploaded()) == _sha(sent)
    again = shop.ledger.get(second)
    assert again is not None and again.sliced is not None
    assert again.sliced.reprint_of == first and again.sliced.sliced == sliced.BY_HAND
    assert again.gcode_sha256 == _sha(sent)
    assert shop.ledger.reprints(first) == [second]


@pytest.mark.parametrize("how", ["gone", "changed"])
def test_a_run_whose_kept_file_is_gone_or_changed_says_so_and_prints_nothing(
    how: str, shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")))
    sent = shop.uploaded()
    if how == "gone":
        sent.unlink()
    else:
        sent.write_bytes(sent.read_bytes() + b"; one more line\n")
    starts = shop.printer.verbs.count("start")

    run = shop.find(first)
    assert run.gcode is None
    assert ("isn't in its folder any more" if how == "gone" else "has changed since it printed") \
        in run.gone
    with pytest.raises(reprint.Refused, match="can't print as it was"):
        run.as_it_was()

    # At the queue's prompt: Enter is refused, saying why, and stop adds nothing.
    said: list[str] = []
    typed = iter([f"again {first[:8]}", "", "stop", "quit"])
    serve(queue, daemon, read=lambda prompt: next(typed), write=said.append,
          forget=lambda: None, runs=Runs(shop.ledger_path, shop.work, shop.store, PEI,
                                         shop.slicer))
    text = "\n".join(said)
    assert "It can't print as it was" in text and "can't print as it was" in text
    assert "Nothing added." in text
    assert queue.jobs() == [] and shop.printer.verbs.count("start") == starts


# --- change and print again ----------------------------------------------------------------------


def _edit_store(store: Path, key: str, value: str) -> None:
    """The store's process profiles, edited since the run."""
    for path in store.glob("*/process/*.json"):
        data = json.loads(path.read_text())
        data[key] = value
        path.write_text(json.dumps(data, indent=2))


def test_one_change_differs_from_the_run_in_that_setting_alone(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")))
    row = shop.ledger.get(first)
    assert row is not None and row.features
    was = parse_config_block(shop.uploaded())
    # The made-up cube has no trait a lesson is keyed to: here it touches the plate as a small
    # part does.
    small = tuple(f.model_copy(update={"value": 150.0}) if f.id == "bed_contact" else f
                  for f in row.features)
    with shop.ledger.writing() as db:
        db.execute("UPDATE prints SET features = ? WHERE job_id = ?",
                   (json.dumps([f.model_dump(mode="json") for f in small]), first))
    assert traits(small) == ("small_bed_contact",)
    # Since: the profiles are edited, and a lesson for this part is kept.
    _edit_store(shop.store, "wall_loops", "7")
    lesson = print_lessons.write(shop.ledger, first, "reviewer: test", "a made-up lesson",
                                 traits(small), setting="top_shell_layers=9")
    print_lessons.accept(shop.ledger, lesson, "reviewer: test")
    assert was["top_shell_layers"] != "9" and was["wall_loops"] != "7"
    infill = "35%" if was["sparse_infill_density"] != "35%" else "45%"

    run = shop.find(first)
    assert run.no_change == "" and run.part is not None
    (suggested,) = [s for s in run.suggestions if s.key == "top_shell_layers"]
    assert suggested.value == "9" and f"lesson {lesson}" in suggested.why
    assert any("top_shell_layers=9" in line for line in run.lines()), "the newer lesson is shown"
    changes = reprint.parse_changes(f"sparse_infill_density={infill}", run)
    assert isinstance(changes, list)
    made = run.change(changes, reprint.slice_folder(shop.work, row), shop.slicer)

    now = parse_config_block(made.gcode)
    assert {k for k in was.keys() | now.keys() if was.get(k) != now.get(k)} == {
        "sparse_infill_density"}, "read from both G-codes: that setting alone"
    assert now["sparse_infill_density"] == infill
    assert now["top_shell_layers"] == was["top_shell_layers"], "the lesson isn't applied"
    assert now["wall_loops"] == was["wall_loops"], "the run's own profiles, not today's"
    assert [d.key for d in made.differs] == ["sparse_infill_density"] and made.beyond == ()
    assert "  Nothing else differs." in made.lines()

    second = shop.printed(daemon, queue, Adding(made.gcode, again=run.changed(made)))
    assert _sha(shop.uploaded()) == _sha(made.gcode)
    again = shop.ledger.get(second)
    assert again is not None and again.sliced is not None
    assert again.sliced.reprint_of == first and again.sliced.sliced == sliced.AGAIN
    reprinted = again.sliced.reprinted
    assert reprinted is not None and reprinted.first == first
    assert reprinted.said_changes() == [f"sparse_infill_density={infill}"]
    assert reprinted.beyond == () and any("top_shell_layers=9" in s for s in reprinted.suggested)

    # A change of that print again starts from the first run, with its change kept.
    run = shop.find(second)
    assert run.first.job_id == first and [c.key for c in run.earlier] == ["sparse_infill_density"]
    made = run.change(reprint.parse_changes("take 1", run),  # type: ignore[arg-type]
                      reprint.slice_folder(shop.work, row), shop.slicer)
    now = parse_config_block(made.gcode)
    assert now["top_shell_layers"] == "9" and now["sparse_infill_density"] == infill
    assert {d.key for d in made.differs} == {"top_shell_layers"}, "against that print again"


def test_a_change_the_profiles_cant_take_is_refused_and_nothing_is_sliced(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")))
    run = shop.find(first)
    calls = len(shop.slicer.calls)
    with pytest.raises(reprint.Refused):
        run.change([Change(key="no_such_setting", preset="", value="1", reason="",
                           source=Source.SPONSOR)], shop.tmp / "x", shop.slicer)
    assert len(shop.slicer.calls) == calls
    assert reprint.parse_changes("orientation=x90", run) == (
        "the pose is the run's: a part in another pose is a new job")


def test_the_terminal_changes_a_run_and_queues_it_shown_against_the_run(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")), note="stringy at the top")
    said: list[str] = []
    typed = iter(["runs stringy", f"again {first[:8]}", "sparse_infill_density=35%", "",
                  "quit"])
    serve(queue, daemon, read=lambda prompt: next(typed), write=said.append,
          forget=lambda: None, runs=Runs(shop.ledger_path, shop.work, shop.store, PEI,
                                         shop.slicer))
    text = "\n".join(said)
    assert 'success: "stringy at the top"' in text and f"[{first[:8]}]" in text
    assert "Its file is kept" in text and "Sliced again. Against that run's G-code:" in text
    assert "changed      sparse_infill_density:" in text and "Nothing else differs." in text
    assert f"Added: a print again of job {first[:8]}." in text
    (job,) = queue.jobs()
    assert job.by_hand and job.part == "bracket.stl"


# --- the runs ------------------------------------------------------------------------------------


def test_finished_runs_are_listed_newest_first_and_found_by_part_colour_or_note(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    good = shop.printed(daemon, queue, Adding(shop.part("bracket")), note="crisp edges")
    bad = shop.printed(daemon, queue, Adding(shop.part("lid")), result=Result.FAILED,
                       symptom=Symptom.WARPING, note="lifted at a corner")
    found = reprint.runs(shop.ledger)
    assert [r.job_id for r in found] == [bad, good], "newest first, the failed one too"
    assert [r.job_id for r in reprint.runs(shop.ledger, "lid")] == [bad]
    assert [r.job_id for r in reprint.runs(shop.ledger, "crisp")] == [good]
    assert {r.job_id for r in reprint.runs(shop.ledger, "white")} == {good, bad}
    assert reprint.runs(shop.ledger, "black") == []
    lines = reprint.run_line(found[0])
    assert lines[1].strip() == 'failed, warping: "lifted at a corner"'
    assert "white" in lines[2]
    assert lines[3].strip() == "you changed nothing at its review: its settings as proposed"
    assert any("Enter prints it as it was (it failed: warping)." in line
               for line in shop.find(bad).lines())
    assert any(line.endswith("Enter prints it as it was.") for line in shop.find(good).lines())


def test_a_run_says_what_you_changed_at_its_review(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    added, _ = queue.add([Adding(shop.part("bracket"))])
    job = added[0]
    shop.answer(queue, job)
    asked = shop.review(queue, job, Review(corrections=(Change(
        key="sparse_infill_density", preset="15%", value="40%", reason="stiffer",
        source=Source.SPONSOR),)))
    wait_until(lambda: (a := queue.asked(job)) is not None and a is not asked)
    shop.review(queue, job)
    wait_until(lambda: shop.waits(job) is Waits.READY or shop.offered() == job)
    start_offered(shop, daemon, job)
    shop.end_print(job)
    (row,) = reprint.runs(shop.ledger)
    assert reprint.run_line(row)[3].strip() == "you changed: sparse_infill_density=40%"


# --- a run from before ---------------------------------------------------------------------------


def test_a_run_from_before_says_what_cant_be_checked_and_still_prints_again(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    part = shop.part("bracket")
    first = shop.printed(daemon, queue, Adding(part))
    # As a run from before kept it: no hash, no part, no profiles.
    with shop.ledger.writing() as db:
        db.execute("UPDATE prints SET gcode_sha256 = NULL WHERE job_id = ?", (first,))
    for kept in (shop.work / first / reprint.KEPT_DIR).iterdir():
        kept.unlink()

    run = shop.find(first)
    said = " ".join(run.unchecked)
    assert "hash wasn't kept then" in said and "bytes can't be checked" in said
    assert "its part wasn't kept then" in said and str(part) in said
    assert "its profiles weren't kept then" in said
    assert run.as_it_was().path == shop.uploaded()
    # Today's store sets wall_loops otherwise: kept at the run's value, from its G-code.
    was = parse_config_block(shop.uploaded())
    _edit_store(shop.store, "wall_loops", "7")
    run = shop.find(first)
    assert any("has been edited since" in u for u in run.unchecked)
    made = run.change(reprint.parse_changes("sparse_infill_density=35%", run),  # type: ignore[arg-type]
                      shop.tmp / "change", shop.slicer)
    now = parse_config_block(made.gcode)
    assert {k for k in was.keys() | now.keys() if was.get(k) != now.get(k)} == {
        "sparse_infill_density"}, "the change alone, though the profile was edited since"
    assert [(d.key, d.was, d.now) for d in made.pinned] == [("wall_loops", was["wall_loops"], "7")]
    assert made.beyond == ()
    assert (f"  KEPT         wall_loops at {was['wall_loops']}, as that run printed it: its "
            "profile says 7 now") in made.lines()
    assert "  Nothing else differs." in made.lines()
    sent = run.changed(made)
    assert sent.reprinted is not None and sent.reprinted.pinned and sent.reprinted.beyond == ()


def test_what_cant_be_kept_at_the_runs_value_is_named_and_enter_asks_once(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")))

    def stubborn(stl_path: Path | str, profiles: SlicerProfiles,
                 outdir: Path | str) -> SliceResult:
        """Orca as if it always wrote top_shell_layers its own way: no profile keeps it."""
        result = shop.slicer(stl_path, profiles, outdir)
        text = result.gcode_path.read_text()
        result.gcode_path.write_text(re.sub(r"(?m)^; top_shell_layers = .*$",
                                            "; top_shell_layers = 99", text))
        return result

    run = shop.find(first)
    said: list[str] = []
    typed = iter(["sparse_infill_density=35%", "", ""])
    sent = reprint.screen(run, slicer=stubborn, into=lambda: reprint.slice_folder(shop.work,
                          run.row), read=lambda prompt: next(typed), write=said.append)
    text = "\n".join(said)
    assert "ALSO DIFFERS top_shell_layers:" in text and "couldn't be kept" in text
    assert "This also changes top_shell_layers:" in text and "Enter again to print it so" in text
    assert text.count("This also changes") == 1, "asked once"
    assert sent is not None and sent.reprinted is not None
    assert sent.reprinted.beyond == ("top_shell_layers",)


# --- the run's settings --------------------------------------------------------------------------


def test_a_setting_that_didnt_reach_its_slice_is_suggested_not_carried(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")))
    row = shop.ledger.get(first)
    assert row is not None and row.proposal is not None
    missed = Change(key="nozzle_temperature", preset="200", value="230", reason="made up",
                    source=Source.MODEL, not_applied="asked 230, sliced 200")
    with shop.ledger.writing() as db:
        db.execute("UPDATE prints SET proposal = ? WHERE job_id = ?", (row.proposal.model_copy(
            update={"not_applied": (missed,)}).model_dump_json(), first))

    run = shop.find(first)
    (suggested,) = [s for s in run.suggestions if s.key == "nozzle_temperature"]
    assert "didn't reach its slice (asked 230, sliced 200)" in suggested.why
    assert all(c.key != "nozzle_temperature" for c in run.settings), "never applied by itself"


def test_a_pause_whose_g_code_doesnt_say_where_it_paused_is_left_out_saying_so(
    shop: Again, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = shop.printed(daemon, queue, Adding(shop.part("bracket")))
    row = shop.ledger.get(first)
    assert row is not None and row.proposal is not None
    pause = Change(key="pause_at", preset="", value="2.4", reason="for an insert",
                   source=Source.SPONSOR)
    proposal = Proposal(changes=(*row.proposal.changes, pause))
    with shop.ledger.writing() as db:
        db.execute("UPDATE prints SET proposal = ? WHERE job_id = ?",
                   (proposal.model_dump_json(), first))

    run = shop.find(first)
    assert all(c.key != "pause_at" for c in run.settings), "never copied raw"
    assert any("pause_at=2.4 is left out" in n for n in run.notes)


# --- the command ---------------------------------------------------------------------------------


def test_print_job_lists_runs_and_prints_one_again_with_a_change_unasked(
    tmp_path: Path, settings: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--runs` and `--again ID --change ...` on fakes: the run is sliced again from what it
    kept, shown against its G-code, and sent to the gate unarmed, where it's rejected."""
    shop = Again(tmp_path)
    try:
        with ExitStack() as stack:
            daemon = stack.enter_context(shop.daemon())
            queue = stack.enter_context(shop.queue(daemon))
            first = shop.printed(daemon, queue, Adding(shop.part("bracket")), note="crisp")
        script = _script("print_job")
        bridge = shop.bridge

        def build_run(**kwargs: Any) -> SimpleNamespace:
            ledger = Ledger(shop.ledger_path)
            return SimpleNamespace(ledger=ledger, daemon=Daemon(
                cfg=CFG, profiles=None, workdir=shop.work, printer=shop.printer,
                slicer=_never("the daemon's slicer"), bridge=lambda cfg: bridge, ledger=ledger,
                show=kwargs.get("show"), store=kwargs.get("store")))

        monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
        monkeypatch.setattr(script, "WORK_DIR", shop.work)
        monkeypatch.setattr(script, "STORE_DIR", shop.store)
        monkeypatch.setattr(script, "slice_model", shop.slicer)
        monkeypatch.setattr(script, "build_run", build_run)
        monkeypatch.setattr("builtins.input", _never("a question"))
        capsys.readouterr()

        assert script.main(["--runs", "crisp", "--settings", str(settings)]) == 0
        out = capsys.readouterr().out
        assert f"bracket.stl  [{first[:8]}]" in out and 'success: "crisp"' in out
        assert script.main(["--runs", "nothing-like-it", "--settings", str(settings)]) == 0
        assert "No finished run has 'nothing-like-it'." in capsys.readouterr().out

        starts = shop.printer.verbs.count("start")
        pressed = threading.Thread(target=_press_when_open, args=(bridge, "reject"), daemon=True)
        pressed.start()
        assert script.main(["--again", first[:8], "--change", "sparse_infill_density=35%",
                            "--settings", str(settings)]) == 0
        pressed.join(timeout=5)
        out = capsys.readouterr().out
        assert "Print again: " in out and "Its file is kept" in out
        assert "changed      sparse_infill_density: 15% -> 35%" in out
        assert "Nothing else differs." in out and "Not armed" in out and "Ready to print" in out
        assert f"A reprint of job {first} (bracket.stl), sliced again" in out
        assert shop.printer.verbs.count("start") == starts, "rejected at the gate: nothing printed"

        with pytest.raises(SystemExit):  # --change alone: it changes a run --again names
            script.main(["--change", "layer_height=0.2", "--settings", str(settings)])
    finally:
        shop.ledger.close()


def _press_when_open(bridge: Any, press: str) -> None:
    end = time.monotonic() + 30
    while time.monotonic() < end:
        raw = bridge.retained.get(gate_topic(CFG))
        if raw is not None and json.loads(raw).get("pending"):
            bridge.press(CFG, press)
            return
        time.sleep(0.05)


# --- with the real Orca --------------------------------------------------------------------------


@pytest.mark.slicer
@needs_orca
def test_with_orca_a_change_differs_from_the_run_in_that_setting_alone(tmp_path: Path) -> None:
    """Orca 2.4.2 on the Mac: a made-up run, sliced with the store's PLA and a setting of its
    own, then one change sliced again from what it kept. Read from both G-codes, that setting
    alone differs, and the run's own setting is kept."""
    jobs, job = tmp_path / "jobs", str(uuid.uuid4())
    choice = choose(Store(STORE), nozzle_mm=0.4, filament="Elegoo PLA")
    profiles = with_bed_type(profiles_for(choice, STORE, tmp_path / "composed"), PEI,
                             tmp_path / "profiles")
    part = tmp_path / "part.stl"
    trimesh.creation.box(extents=(20, 20, 6)).export(part)
    ran = Proposal(changes=(Change(key="wall_loops", preset="2", value="4", reason="made up",
                                   source=Source.SPONSOR),))
    result = name_gcode(slice_model(part, apply(profiles, ran, tmp_path / "proposed"),
                                    jobs / job), job_id=job, part="part")
    assert reprint.keep(jobs / job, part, _sha(part), profiles) == []
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        ledger.record_start_requested(PrintRecord(
            job_id=job, stl_name="part.stl", stl_sha256=_sha(part), rotation_deg=(0.0, 0.0, 0.0),
            orientation_source="as_laid", orientation_rationale="made up",
            orientation_accepted=True, gcode_name=result.gcode_path.name,
            print_time_s=result.print_time_s, filament_cm3=result.filament_cm3,
            layer_count=result.layer_count, slicer_config=parse_config_block(result.gcode_path),
            printer_machine="made-up", printer_firmware="0", profiles=profiles.used,
            proposal=ran, gcode_sha256=_sha(result.gcode_path)))
        ledger.record_started(job)
        ledger.record_ended(job, 9)
        run = reprint.find(ledger, jobs, job[:8], store_root=STORE, bed=PEI)

    assert run.as_it_was().path == result.gcode_path
    changes = reprint.parse_changes("sparse_infill_density=35%", run)
    assert isinstance(changes, list)
    made = run.change(changes, tmp_path / "again", slice_model)
    was, now = parse_config_block(result.gcode_path), parse_config_block(made.gcode)
    assert {k for k in was.keys() | now.keys() if was.get(k) != now.get(k)} == {
        "sparse_infill_density"}
    assert now["wall_loops"] == was["wall_loops"] == "4"
    assert made.beyond == ()
