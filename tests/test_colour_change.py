"""A colour change at a height can be asked for at the review, and the print pauses there.

mqucifer/print-shop#119. `pause_at=Z` (or `colour_change=Z`) at the review is Orca's own pause,
from the machine's pause G-code, at the first layer at or above Z. The review, the gate and the
ledger say where it fell, and the outcome step asks how the swap went. Without a printer, and,
but for the one test marked `slicer`, without Orca: a recorded slice has the pause put in where
Orca puts it.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from print_shop.core.mqtt import outcome_topic
from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.intake import replay_asker
from print_shop.setup.proposal import PAUSE, Change, Proposal, Source, apply, pause_z
from print_shop.setup.reading import read_slice
from print_shop.setup.review import Review, ReviewRequest, parse_answer
from print_shop.slicer import gcode
from print_shop.slicer.orca import PauseAt, SlicerProfiles, slice_model
from tests.fakes import (
    CI_PROFILES,
    DATA,
    STORE_PROFILES,
    FramePrinter,
    MemoryBridge,
    RecordedSlicer,
)
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_outcome import wait_until
from tests.test_review import ANSWERS, Sponsor, _model, _questions

CUBE_GCODE = DATA / "recorded" / "cube.gcode"
LAYERS = {"initial_layer_print_height": "0.2", "layer_height": "0.2"}


def with_pause(path: Path, print_z: float) -> None:
    """Put a pause in a recorded slice where Orca puts its own: at the start of the layer whose
    top is nearest `print_z`, a tie to the lower, before that layer's lines."""
    lines = path.read_text().splitlines(keepends=True)
    tops = [(i, float(line[3:])) for i, line in enumerate(lines) if line.startswith(";Z:")]
    at, _ = min(tops, key=lambda t: (abs(t[1] - print_z), t[1]))
    after = at + 1 if lines[at + 1].startswith(";HEIGHT:") else at
    lines[after + 1:after + 1] = [";PAUSE_PRINT\n", "M600\n"]
    path.write_text("".join(lines))


class PausingSlicer:
    """The recorded cube's slice, with each pause the profiles ask for put in as Orca would."""

    def __init__(self) -> None:
        self.recorded = RecordedSlicer()
        self.pauses: list[tuple[PauseAt, ...]] = []

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        result = self.recorded(stl_path, profiles, outdir)
        self.pauses.append(profiles.pauses)
        for pause in profiles.pauses:
            with_pause(Path(result.gcode_path), pause.print_z)
        return result


# Where a height lands, and what apply makes of it
# --------------------------------------------------------------------------


def test_a_height_lands_on_the_first_layer_at_or_above_it() -> None:
    assert [pause_z(LAYERS, z) for z in (0.1, 2.7, 2.8, 2.81, 12.5)] == [0.2, 2.8, 2.8, 3.0, 12.6]
    assert pause_z({"initial_layer_print_height": "0.3", "layer_height": "0.2"}, 1.0) == 1.1


def test_apply_makes_it_orcas_pause_not_a_process_setting(tmp_path: Path) -> None:
    process = tmp_path / "process.json"
    process.write_text(json.dumps({**json.loads(CI_PROFILES.process.read_text()), **LAYERS}))
    profiles = CI_PROFILES.model_copy(update={"process": process})
    change = Change(key=PAUSE, preset="", value="10.1", reason="white to black",
                    source=Source.SPONSOR)
    applied = apply(profiles, Proposal(changes=(change,)), tmp_path / "out")
    assert applied.pauses == (PauseAt(print_z=10.2, message="white to black"),)
    assert PAUSE not in json.loads(applied.process.read_text())
    none = change.model_copy(update={"value": "none"})
    assert apply(profiles, Proposal(changes=(none,)), tmp_path / "out").pauses == ()


def test_orca_is_given_its_own_pause_file(tmp_path: Path) -> None:
    """A stand-in for the Orca binary records what it was told and writes a recorded slice."""
    told = tmp_path / "told.txt"
    fake = tmp_path / "orca"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{told}"\n'
        'while [ "$#" -gt 0 ]; do case "$1" in --outputdir) out="$2";; '
        '--load-custom-gcodes) cp "$2" "' + str(tmp_path / "items.json") + '";; esac; shift; done\n'
        f'cp "{CUBE_GCODE}" "$out/plate_1.gcode"\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    profiles = CI_PROFILES.model_copy(update={"pauses": (PauseAt(print_z=10.0, message="swap"),)})
    slice_model(write_stl("cube", tmp_path), profiles, tmp_path / "out", binary=fake)
    assert "--load-custom-gcodes" in told.read_text().split("\n")
    items = json.loads((tmp_path / "items.json").read_text())
    assert items["gcodes"] == [
        {"type": "PausePrint", "print_z": 10.0, "color": "", "extruder": 1, "extra": "swap"}
    ]


# At the review
# --------------------------------------------------------------------------


def _request(max_z: float = 25.0) -> ReviewRequest:
    return ReviewRequest(
        presets=("m", "p", "f"), proposal=Proposal(),
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=100, max_z_mm=max_z,
                          filament_mm=1, filament_cm3=1, filament_g=1, print_time_s=600),
        orientation=AppliedOrientation(rotation_deg=(0.0, 0.0, 0.0), rationale="flat"),
        profiles=CI_PROFILES, process={"wall_loops": "2"},
    )


@pytest.mark.parametrize("answer", ["pause_at=2.0 because a white floor under black walls",
                                    "colour_change=2mm because a white floor under black walls"])
def test_a_colour_change_is_asked_for_by_its_height(answer: str) -> None:
    """The Sponsor's first: a swap at z 2.0 on a 25 mm part."""
    review = parse_answer(answer, _request())
    assert isinstance(review, Review)
    [change] = review.corrections
    assert (change.key, change.value, change.reason, change.source) == (
        PAUSE, "2", "a white floor under black walls", Source.SPONSOR
    )


@pytest.mark.parametrize(("answer", "why"), [
    ("pause_at=25", "below its top, 25 mm"),
    ("pause_at=0", "above 0"),
    ("pause_at=high", "isn't a height"),
])
def test_a_height_outside_the_part_is_refused_saying_why(answer: str, why: str) -> None:
    said = parse_answer(answer, _request())
    assert isinstance(said, str) and why in said


# Read back from the slice
# --------------------------------------------------------------------------


def test_the_reading_says_where_it_paused_and_how_far_in(tmp_path: Path) -> None:
    sliced = tmp_path / "plate_1.gcode"
    sliced.write_text(CUBE_GCODE.read_text())
    with_pause(sliced, 10.0)
    reading = read_slice(sliced)
    [pause] = reading.pauses
    assert (pause.before_layer, pause.z) == (50, 10.0)
    assert pause.after_s == round(sum(t.time_s for t in reading.layers if t.n < 50), 1)
    assert any(line.startswith("pauses for a filament swap before layer 50 (z 10 mm), about ")
               for line in reading.lines())


@pytest.mark.slicer
def test_orca_itself_pauses_at_the_layer_asked_for(tmp_path: Path) -> None:
    """With the real Orca and the store's own profiles, on the Mac: one pause, before the layer
    whose top is 2 mm, the Sponsor's white floor under black walls."""
    process = tmp_path / "process.json"
    process.write_text(json.dumps({**json.loads(STORE_PROFILES.process.read_text()), **LAYERS}))
    profiles = STORE_PROFILES.model_copy(
        update={"process": process, "pauses": (PauseAt(print_z=pause_z(LAYERS, 2.0)),)}
    )
    result = slice_model(write_stl("cube", tmp_path), profiles, tmp_path / "out")
    [pause] = gcode.read(Path(result.gcode_path)).pauses
    assert (pause["before_layer"], pause["z"]) == (10, 2.0)


# Through the daemon: the review, the gate, the ledger and the outcome step
# --------------------------------------------------------------------------


def test_a_colour_change_asked_for_at_the_review_reaches_the_gcode_the_gate_and_the_ledger(
    tmp_path: Path,
) -> None:
    (tmp_path / "work").mkdir()
    process = json.loads(CI_PROFILES.process.read_text())
    (tmp_path / "process.json").write_text(json.dumps({**process, "wall_loops": "2", **LAYERS}))
    profiles = CI_PROFILES.model_copy(update={"process": tmp_path / "process.json"})
    sponsor = Sponsor("pause_at=2.0 because a white floor under black walls", "ok")
    slicer = PausingSlicer()
    ledger = Ledger(tmp_path / "ledger.sqlite")
    bridge = MemoryBridge()
    printer = FramePrinter(after_start=["printing", "completed"])
    daemon = Daemon(cfg=CFG, profiles=profiles, workdir=tmp_path / "work", printer=printer,
                    slicer=slicer, bridge=lambda cfg: bridge, ledger=ledger,
                    proposer=Proposer(ask=_model, ask_questions=_questions),
                    asker=replay_asker(ANSWERS), reviewer=sponsor,
                    watch_printer=printer, watch_poll_s=0.01)
    from print_shop.core.mqtt import command_topic  # noqa: PLC0415

    with daemon:
        job_id = daemon.submit(write_stl("cube", tmp_path))
        daemon.wait_for_gate(job_id, timeout_s=30)

        # The second review shows where it fell, read from the slice.
        shown = sponsor.shown[-1]
        assert shown.reading is not None
        [pause] = shown.reading.pauses
        assert (pause.before_layer, pause.z) == (10, 2.0)
        assert slicer.pauses[-1] == (
            PauseAt(print_z=2.0, message="a white floor under black walls"),
        )

        # The gate says it, so the person is at the printer for it.
        gate = daemon.pending_gate() or {}
        assert gate["pause"].startswith(
            "this print pauses for a filament swap before layer 10 (z 2 mm), about "
        )
        bridge.deliver(command_topic(CFG), {"decision": "approve"})
        assert daemon.wait_for_finish(job_id, timeout_s=30).printed

        # One pause in what went to the printer, before layer 10.
        row = ledger.get(job_id)
        assert row is not None
        sent = Path(tmp_path / "work" / job_id / row.gcode_name)
        assert [(p["before_layer"], p["z"]) for p in gcode.read(sent).pauses] == [(10, 2.0)]

        # The ledger keeps it with the corrections, and in the reading.
        assert [(c.key, c.value) for c in row.corrections or ()] == [(PAUSE, "2")]
        assert row.reading is not None and row.reading.pauses[0].before_layer == 10
        assert "swap           pauses for a filament swap before layer 10" in describe(row)
        # A choice for that one part, not a lesson: none is drawn from it.
        assert ledger.query("SELECT * FROM print_lessons WHERE setting=?", PAUSE) == []

        # The outcome step asks how the swap went.
        wait_until(lambda: bridge.retained_json(outcome_topic(CFG)).get("pending") is True)
        asked = bridge.retained_json(outcome_topic(CFG))
        assert asked["swap"].startswith("pauses for a filament swap before layer 10")




def test_a_colour_change_draws_no_lesson(tmp_path: Path) -> None:
    """A swap at z 2.0 is a choice for that one part (the operator, on mqucifer/print-shop#120):
    it stays in the job's corrections, and no lesson is drawn from it. The brim beside it is."""
    from tests.test_ledger import record  # noqa: PLC0415

    pause = Change(key=PAUSE, preset="", value="2", reason="a white floor under black walls",
                   source=Source.SPONSOR)
    brim = Change(key="brim_type", preset="no_brim", value="outer_only", reason="the ends lift",
                  source=Source.SPONSOR)
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        ledger.record_start_requested(
            record("job-1").model_copy(update={"corrections": (pause, brim)})
        )
        row = ledger.get("job-1")
        assert row is not None and [c.key for c in row.corrections or ()] == [PAUSE, "brim_type"]
        assert [r["setting"] for r in ledger.query("SELECT setting FROM print_lessons")] == [
            "brim_type"
        ]
