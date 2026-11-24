"""Several parts in one job, with a ledger row and an outcome for each (mqucifer/print-shop#109).

Made-up boxes, a stand-in Orca, and fakes for the broker, the printer, the models and the
camera: the script, the graph, the daemon, the gates, the ledger and the desk are the real ones.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from print_shop.core.checkpoint import open_checkpointer
from print_shop.core.mqtt import (
    gate_topic,
    outcome_note_topic,
    outcome_result_topic,
    outcome_submit_topic,
    outcome_symptom_topic,
    outcome_topic,
    state_topic,
)
from print_shop.desk import Desk
from print_shop.desk.again import again
from print_shop.desk.page import index_page, job_page
from print_shop.ledger import Answer, JobEnding, Ledger, LedgerError, PrintRecord, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.graph import build_graph, graph_edges
from print_shop.plate import (
    BY_LAYER,
    BY_OBJECT,
    SEQUENCE,
    GivenPart,
    OnPlate,
    PlateLayout,
    PlateRow,
    plate_job,
    row_id,
)
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup import lessons as print_lessons
from print_shop.setup.intake import Answer as IntakeAnswer
from print_shop.setup.proposal import ORIENTATION, PAUSE, Change, GivenLesson, Proposal, Source
from print_shop.setup.review import Review, ReviewRequest, cli_reviewer, parse_answer, render
from print_shop.slicer.orca import SlicerProfiles
from tests.fakes import FakeCamera, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.plates import PlateOrca, box, profiles
from tests.test_job_e2e import CFG
from tests.test_ledger import record
from tests.test_review import _model, _questions
from tests.test_runtime import settings  # noqa: F401  (the fixture)

JOB = "0a1b2c3d-0000-4000-8000-0000000000aa"
CAP_NAMES = ("cap-tight.stl", "cap-mid.stl", "cap-loose.stl")


def _never(*_: object, **__: object) -> Any:
    raise AssertionError("a job of several parts never uses the one part's slicer")


def _until(true: Callable[[], bool], what: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not true():
        assert time.monotonic() < deadline, f"waited for {what}"
        time.sleep(0.02)


def _caps(tmp_path: Path) -> list[Path]:
    """Three small made-up caps, each a little taller than the last."""
    return [box(tmp_path / "caps" / name, 18, 18 + n, 4.0 + 2 * n)
            for n, name in enumerate(CAP_NAMES)]


class Rig:
    """The daemon on the fakes, with a person who types the answers given."""

    def __init__(
        self, tmp_path: Path, *typed: str, use: SlicerProfiles | None = None,
        orca: Any = None, proposing: bool = True,
        lessons: Callable[..., Any] | None = None, asking: Any = None, waiting: Any = None,
        questions: Any = _questions, llm: Any = None,
    ) -> None:
        self.work = tmp_path / "jobs"
        self.work.mkdir(exist_ok=True)
        self.path = tmp_path / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.bridge = MemoryBridge()
        self.printer = FramePrinter(after_start=["printing", "printing", "completed"])
        self.orca = orca or PlateOrca()
        self.typed = list(typed or ("ok",))
        self.shown: list[str] = []
        self.reviews: list[ReviewRequest] = []
        self.asked = 0
        reviewing = cli_reviewer(read=lambda _: self.typed.pop(0), write=self.shown.append)

        def asker(questions: Any) -> tuple[IntakeAnswer, ...]:
            self.asked += 1
            return tuple(IntakeAnswer(choice=q.recommended) for q in questions)

        def reviewer(request: ReviewRequest) -> Review:
            self.reviews.append(request)
            return reviewing(request)

        setup: dict[str, Any] = {}
        if proposing:
            setup = {"proposer": Proposer(ask=_model, ask_questions=questions, lessons=lessons),
                     "asker": asking or asker, "waiting": waiting, "llm": llm,
                     "reviewer": reviewer}
        self.daemon = Daemon(
            cfg=CFG, profiles=use or profiles(tmp_path / "profiles"), workdir=self.work,
            printer=self.printer, watch_printer=self.printer, watch_poll_s=0.05,
            camera=FakeCamera(), slicer=_never, plate_slicer=self.orca,
            bridge=lambda cfg: self.bridge, ledger=self.ledger, **setup,
        )

    def gated(self, parts: list[Path], as_laid: bool = True) -> str:
        job = self.daemon.submit_several(parts, as_laid=as_laid)
        self.daemon.wait_for_gate(job, timeout_s=30)
        if self.daemon.pending_gate() is None:
            raise AssertionError(self.daemon.wait_for_finish(job, timeout_s=5).error)
        return job

    def press(self, decision: str, job: str) -> Any:
        # What the card's button sends now: the job, and the gate's word (mqucifer/print-shop#209).
        self.bridge.press(CFG, decision)
        return self.daemon.wait_for_finish(job, timeout_s=30)

    def card(self) -> dict[str, Any]:
        found: dict[str, Any] = self.bridge.retained_json(outcome_topic(CFG))
        return found

    def answer(self, result: str, symptom: str = "none", note: str = "") -> None:
        self.bridge.deliver(outcome_result_topic(CFG), result)
        self.bridge.deliver(outcome_symptom_topic(CFG), symptom)
        self.bridge.deliver(outcome_note_topic(CFG), note)
        self.bridge.deliver(outcome_submit_topic(CFG), "")


# Done when: three small parts print by object in one job, each with its own outcome
# --------------------------------------------------------------------------


def test_three_small_parts_print_by_object_in_one_job_each_with_its_own_outcome(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    caps = _caps(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        assert rig.printer.calls == [], "nothing touches the printer before the gate"
        assert (rig.asked, len(rig.reviews)) == (1, 1), "one set of questions and one review"
        gate = rig.bridge.retained_json(gate_topic(CFG))
        assert gate["job_id"] == job and gate["orientation_deg"] is None
        assert "printed BY OBJECT" in gate["plate"]
        assert sum(line.startswith("  part ") for line in gate["plate_lines"]) == 3
        assert "    front" in gate["plate_lines"], "with the map"
        assert rig.bridge.retained_json(state_topic(CFG))["plate"] == gate["plate"]
        assert len(rig.bridge.on(gate_topic(CFG))) == 2, "cleared at the start, then one gate"

        result = rig.press("approve", job)
        assert (result.approved, result.printed, result.error) == (True, True, None)
        assert rig.printer.verbs.count("upload") == rig.printer.verbs.count("start") == 1
        (sent,) = [Path(p) for verb, p in rig.printer.calls if verb == "upload"]
        assert "; print_sequence = by object" in sent.read_text()
        assert plate_job(sent.stem.rsplit("_", 1)[-1]) == job

        rows = rig.ledger.rows()
        assert [r.job_id for r in rows] == [job, f"{job}-2", f"{job}-3"]
        assert [r.stl_name for r in rows] == list(CAP_NAMES)
        assert len({r.stl_sha256 for r in rows}) == 3
        assert {r.gcode_name for r in rows} == {sent.name}
        for n, row in enumerate(rows, 1):
            assert row.plate is not None
            assert (row.plate.n, row.plate.job_id, row.plate.layout.sequence) == (n, job, BY_OBJECT)
            assert row.features is not None
            height = next(f.value for f in row.features if f.id == "height")
            assert height == 2.0 + 2 * n, "each part's own features"
            assert row.orientation_source == "as_laid"

        _until(lambda: all(r.ended_at for r in rig.ledger.rows()), "the print to be seen to end")
        rows = rig.ledger.rows()
        assert len({r.ended_at for r in rows}) == len({r.started_at for r in rows}) == 1

        answers = [("success", "none", "slides on"), ("failed", "stringing", "hairs inside"),
                   ("success", "none", "")]
        for n, given in enumerate(answers, 1):
            asking = row_id(job, n)
            _until(lambda: rig.card().get("job_id") == asking, f"part {n}'s question")  # noqa: B023
            card = rig.card()
            assert card["waiting"] == 4 - n
            assert card["stl_name"].startswith(f"{CAP_NAMES[n - 1]} (part {n} of 3), ")
            assert " x " in card["stl_name"], "where it is on the plate"
            assert (card["result"], card["note"]) == ("unset", ""), "each starts unanswered"
            assert card["frames"] > 0, "the plate's frames are beside each part's question"
            rig.answer(*given)
        _until(lambda: not rig.card()["pending"], "the card to clear")

    rows = rig.ledger.rows()
    assert [(r.result, r.symptom, r.note) for r in rows] == answers
    assert rig.ledger.running() == rig.ledger.awaiting_answer() == []


def test_the_frames_are_one_prints_kept_by_the_first_part(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.press("approve", job)
        _until(lambda: all(r.ended_at for r in rig.ledger.rows()), "the end")
    frames = rig.ledger.frames(job)
    assert frames and rig.ledger.frames(f"{job}-3") == frames
    assert {r["job_id"] for r in rig.ledger.query("SELECT job_id FROM print_frames")} == {job}


def test_a_rejected_plate_prints_nothing_and_leaves_no_row(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        result = rig.press("reject", job)
    assert (result.approved, result.printed) == (False, False)
    assert rig.printer.calls == [] and rig.ledger.rows() == []


def test_an_unarmed_plate_starts_nothing_when_approved(tmp_path: Path) -> None:
    """Held, so the ready plate waits for an armed run (mqucifer/print-shop#209)."""
    rig = Rig(tmp_path)
    bridge = MemoryBridge()
    unarmed = Daemon(cfg=CFG, profiles=profiles(tmp_path / "p"), workdir=rig.work, printer=None,
                     plate_slicer=PlateOrca(), bridge=lambda cfg: bridge, ledger=rig.ledger)
    with unarmed:
        job = unarmed.submit_several(_caps(tmp_path), as_laid=True)
        unarmed.wait_for_gate(job, timeout_s=30)
        bridge.press(CFG, "approve")
        _until(lambda: bool(bridge.retained_json(gate_topic(CFG)).get("held")), "the hold")
        held = str(bridge.retained_json(gate_topic(CFG))["held"])
        bridge.press(CFG, "reject")
        result = unarmed.wait_for_finish(job, timeout_s=30)
    assert "this run isn't armed" in held, held
    assert not result.printed and result.error is None and rig.ledger.rows() == []


def test_every_edge_to_the_printer_still_comes_from_the_gate(tmp_path: Path) -> None:
    from print_shop.core.contracts import ungated_edges

    app = build_graph(profiles(tmp_path / "p"), outdir_root=tmp_path, plate_slicer=PlateOrca())
    assert ungated_edges(graph_edges(app)) == []


def test_a_plate_a_wait_ran_out_on_is_one_unprinted_job(tmp_path: Path) -> None:
    """A gate has no deadline now (mqucifer/print-shop#209): a wait on the machine's steps that
    runs out ends the plate as one job."""
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.daemon.abandon(job, "waiting for the job to finish", 900)
        rig.press("reject", job)
    [ended] = rig.ledger.unprinted_jobs()
    assert (ended.job_id, ended.outcome) == (job, JobEnding.TIMED_OUT)
    assert ended.stl_name == ", ".join(CAP_NAMES)


def test_without_a_proposer_it_still_asks_for_by_object(tmp_path: Path) -> None:
    rig = Rig(tmp_path, proposing=False)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        gate = rig.bridge.retained_json(gate_topic(CFG))
        assert "printed BY OBJECT" in gate["plate"]
        rig.press("reject", job)


def test_the_same_file_twice_is_two_parts_told_apart(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    cap = _caps(tmp_path)[0]
    with rig.daemon:
        job = rig.gated([cap, cap])
        rig.press("approve", job)
    one, two = rig.ledger.rows()
    assert one.part_said == "cap-tight.stl (part 1 of 2)"
    assert two.part_said == "cap-tight.stl (part 2 of 2)"
    assert one.plate is not None and two.plate is not None
    assert one.plate.label() != two.plate.label()
    assert one.plate.label().split(", ", 1)[1] != two.plate.label().split(", ", 1)[1]


def test_one_part_isnt_a_plate(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.daemon, pytest.raises(ValueError, match="two or more"):
        rig.daemon.submit_several(_caps(tmp_path)[:1])


def test_what_prints_is_the_file_as_it_was_taken(tmp_path: Path) -> None:
    """Edited after it was given, a part prints as given: the job reads its own copies."""
    rig = Rig(tmp_path)
    caps = _caps(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        was = (rig.work / job / "given" / "part1_cap-tight.stl").read_bytes()
        assert was == caps[0].read_bytes()
        box(caps[0], 30, 30, 30)
        rig.press("approve", job)
    import hashlib

    assert rig.ledger.rows()[0].stl_sha256 == hashlib.sha256(was).hexdigest()


# What's refused, and where by object can't be done
# --------------------------------------------------------------------------


def test_a_mesh_that_isnt_closed_ends_the_job_before_anything_is_asked(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    caps = _caps(tmp_path)
    broken = write_stl("open_box", tmp_path)
    with rig.daemon:
        job = rig.daemon.submit_several([caps[0], broken, caps[1]], as_laid=True)
        rig.daemon.wait_for_gate(job, timeout_s=30)
        assert rig.daemon.pending_gate() is None, "no gate opens for it"
        result = rig.daemon.wait_for_finish(job, timeout_s=30)
    assert result.error is not None
    assert f"part 2, {broken.name}, isn't a closed mesh" in result.error
    assert rig.asked == 0 and rig.orca.calls == [] and rig.printer.calls == []


def test_parts_that_dont_fit_one_plate_end_the_job_before_anything_is_asked(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    big = [box(tmp_path / f"big-{n}.stl", 120, 120, 5) for n in range(5)]
    with rig.daemon:
        job = rig.daemon.submit_several(big, as_laid=True)
        rig.daemon.wait_for_gate(job, timeout_s=30)
        assert rig.daemon.pending_gate() is None
        result = rig.daemon.wait_for_finish(job, timeout_s=30)
    assert result.error is not None and "don't all fit on one plate" in result.error
    assert rig.asked == 0 and rig.printer.calls == []


def test_a_pose_that_doesnt_fit_names_its_part(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    tall = box(tmp_path / "pole.stl", 20, 20, 300)
    with rig.daemon:
        job = rig.daemon.submit_several([_caps(tmp_path)[0], tall], as_laid=False)
        rig.daemon.wait_for_gate(job, timeout_s=30)
        assert rig.daemon.pending_gate() is None
        result = rig.daemon.wait_for_finish(job, timeout_s=30)
    assert result.error is not None and "part 2, pole.stl" in result.error
    assert rig.printer.calls == []


def test_parts_over_the_rod_print_by_layer_and_the_review_and_gate_say_why(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    tall = [box(tmp_path / f"post-{n}.stl", 20, 20, 40) for n in (1, 2)]
    with rig.daemon:
        job = rig.gated(tall)
        gate = rig.bridge.retained_json(gate_topic(CFG))
        result = rig.press("approve", job)
    assert result.error is None
    said = rig.shown[0]
    assert "printed BY LAYER, all at once" in said and "over the 36 mm to the rod" in said
    assert "NOT APPLIED print_sequence: asked by object, sliced by layer" in said
    assert "over the 36 mm to the rod" in gate["plate"]
    row = rig.ledger.rows()[0]
    assert row.plate is not None and row.plate.layout.sequence == BY_LAYER
    assert row.proposal is not None
    assert SEQUENCE in {c.key for c in row.proposal.not_applied}, "never kept as applied"


# The review
# --------------------------------------------------------------------------


def test_the_review_shows_the_plate_each_part_and_the_order_as_a_change(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.press("reject", job)
    said = rig.shown[0]
    assert "3 parts on one plate, printed BY OBJECT, one after another" in said
    assert "print_sequence  by layer -> by object" in said
    assert "[guard]" in said and "none strings to another" in said
    for n, name in enumerate(CAP_NAMES, 1):
        assert f"Part {n}, {name}:" in said
        assert f"part {n}, {name}: at x " in said
    assert "for the whole plate" in said
    assert "send a part's orientation back (orientation)" in said
    assert "pause_at=12.4" not in said, "not offered where it would be refused"
    [request] = rig.reviews
    assert render(request) == said


def _by_object(tmp_path: Path, *changes: Change) -> ReviewRequest:
    rig = Rig(tmp_path)
    with rig.daemon:
        rig.press("reject", rig.gated(_caps(tmp_path)))
    [request] = rig.reviews
    from dataclasses import replace

    return replace(request, proposal=request.proposal.corrected(list(changes)))


@pytest.mark.parametrize("typed", [
    "pause_at=3", "colour_change=3 because white to black",
    "print_sequence=by object; pause_at=3",
])
def test_a_pause_by_object_is_refused_with_by_layer_offered(tmp_path: Path, typed: str) -> None:
    said = parse_answer(typed, _by_object(tmp_path))
    assert isinstance(said, str)
    assert "Orca writes no pause when it prints by object" in said
    assert "print_sequence=by layer; pause_at=3" in said


def test_a_pause_with_by_layer_is_taken_and_going_back_by_object_is_refused(
    tmp_path: Path,
) -> None:
    request = _by_object(tmp_path)
    assert isinstance(parse_answer("print_sequence=by layer; pause_at=3", request), Review)
    assert isinstance(parse_answer("pause_at=none", request), Review)
    pause = Change(key=PAUSE, preset="", value="3", reason="a colour change", source=Source.SPONSOR)
    from dataclasses import replace

    assert request.plate is not None
    layered = replace(request, plate=request.plate.model_copy(update={"sequence": BY_LAYER}),
                      proposal=request.proposal.corrected([pause]))
    assert isinstance(parse_answer("wall_loops=3", layered), Review)
    assert isinstance(parse_answer("print_sequence=by object", layered), str)
    assert isinstance(parse_answer("print_sequence=by object; pause_at=none", layered), Review)


def test_an_order_that_isnt_one_is_refused(tmp_path: Path) -> None:
    said = parse_answer("print_sequence=sequential", _by_object(tmp_path))
    assert said == "print_sequence is by object or by layer"


def test_by_layer_and_a_pause_corrected_at_the_review_pause_the_plate(tmp_path: Path) -> None:
    rig = Rig(tmp_path, "pause_at=3",
              "print_sequence=by layer; colour_change=3 because a swap", "ok")
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        gate = rig.bridge.retained_json(gate_topic(CFG))
        rig.press("reject", job)
    assert any("would never pause" in s for s in rig.shown)
    assert "printed BY LAYER" in gate["plate"] and "pauses for a swap" in gate["pause"]
    assert rig.orca.calls[-1]["pauses"], "Orca was given it"


def test_one_parts_pose_is_sent_back_and_only_its_row_has_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path, "wall_loops=4 because it clips", "orientation", "2", "2",
              "the mark goes on the side", "ok")
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.press("approve", job)
    one, two, three = rig.ledger.job_rows(job)
    assert any("Which part's orientation?" in s for s in rig.shown)
    assert (two.orientation_source, one.orientation_source) == ("sponsor", "as_laid")
    assert two.rotation_deg != (0.0, 0.0, 0.0)
    assert one.rotation_deg == three.rotation_deg == (0.0, 0.0, 0.0)
    assert "the mark goes on the side" in two.orientation_rationale

    def keys(row: Any) -> list[str]:
        return [c.key for c in row.corrections]

    assert keys(one) == keys(three) == ["wall_loops"], "the plate's own correction is each row's"
    assert sorted(keys(two)) == [ORIENTATION, "wall_loops"]
    for row in (one, two, three):
        assert row.proposal is not None
        walls = row.proposal.get("wall_loops")
        assert walls is not None and walls.value == "4", "it stood after the turn"
    assert "part2_cap-mid" in " ".join(rig.orca.calls[-1]["objects"])

    from print_shop.runtime import describe_all

    said = describe_all(rig.ledger.rows())
    assert said.count("corrected      wall_loops=4") == 1, "the plate's, once"
    (back_line,) = [line for line in said.splitlines() if line.startswith("sent back      ")]
    assert "the mark goes on the side" in back_line
    assert said.index("part 2 of 3") < said.index(back_line) < said.index("part 3 of 3")
    drawn = rig.ledger.query("SELECT job_id, setting FROM print_lessons ORDER BY id")
    assert sorted((d["job_id"], d["setting"]) for d in drawn) == [
        (job, "wall_loops"), (f"{job}-2", ORIENTATION)], "the review taught once"
    assert len(rig.ledger.query("SELECT * FROM print_lesson_evidence")) == 2


def test_each_part_reads_its_own_context_and_a_fit_on_any_holds(tmp_path: Path) -> None:
    caps = _caps(tmp_path)
    caps[1].with_name("cap-mid.part.json").write_text(json.dumps(
        {"fits": [{"name": "over the post", "play": 0.2, "z": [0, 3]}]}))
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        rig.press("reject", job)
    [request] = rig.reviews
    assert any(line.startswith("part 2, cap-mid.stl: fit:") for line in request.context)
    assert any(line.startswith("part 1, cap-tight.stl: none") for line in request.context)
    assert any(f.id == "fit_elephant_foot" for f in request.flags), "a fit on one part at z 0"


def test_the_lessons_for_any_part_are_given_once(tmp_path: Path) -> None:
    kept = GivenLesson(id=7, text="three walls on a cap", key="top_shell_layers", value="6",
                       job_id="job-0", stl_name="old.stl", traits=("small_bed_contact",),
                       reviewed_by="reviewer: test")
    read: list[float] = []

    def lessons(features: Any, filament: str) -> tuple[GivenLesson, ...]:
        read.append(next(f.value for f in features if f.id == "height"))
        return (kept,)

    rig = Rig(tmp_path, lessons=lessons)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.press("reject", job)
    assert sorted(set(read)) == [4.0, 6.0, 8.0], "looked up by each part's own shape"
    assert [g.id for g in rig.reviews[0].proposal.lessons] == [7]


# The script
# --------------------------------------------------------------------------


def _script() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "print_job.py"
    spec = importlib.util.spec_from_file_location("print_job", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_print_job_takes_three_files_and_shows_each_outcome(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _script()
    rig: list[Rig] = []

    def build_run(**given: Any) -> SimpleNamespace:
        assert given["waiting"] is not None, "a plate's questions wait as one part's do"
        made = Rig(tmp_path)
        rig.append(made)
        return SimpleNamespace(daemon=made.daemon, ledger=made.ledger)

    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    monkeypatch.setattr(script, "WORK_DIR", tmp_path / "jobs")
    monkeypatch.setattr(script, "build_run", build_run)
    monkeypatch.setattr(script, "time", SimpleNamespace(sleep=lambda _: time.sleep(0.02)))
    caps = _caps(tmp_path)
    done = threading.Event()

    def person() -> None:
        pressed = False
        answered: set[str] = set()
        while not done.is_set() and len(answered) < 3:
            if rig:
                bridge = rig[0].bridge
                gate = json.loads(bridge.retained.get(gate_topic(CFG), "{}"))
                if not pressed and gate.get("pending"):
                    time.sleep(0.3)  # the terminal has shown the gate
                    bridge.press(CFG, "approve")
                    pressed = True
                card = json.loads(bridge.retained.get(outcome_topic(CFG), "{}"))
                if card.get("pending") and card["job_id"] not in answered:
                    answered.add(card["job_id"])
                    rig[0].answer("success")
            time.sleep(0.02)

    thread = threading.Thread(target=person, daemon=True)
    thread.start()
    try:
        code = script.main([*map(str, caps), "--as-laid", "--nozzle", "0.4", "--filament",
                            "Elegoo PLA", "--settings", str(settings)])
    finally:
        done.set()
        thread.join(timeout=5)
    out = capsys.readouterr().out
    assert code == 0
    assert "3 parts, one job, on one plate:" in out and "part 2, cap-mid.stl. Its context:" in out
    assert "each kept as laid" in out
    assert "  plate       3 parts on one plate, printed BY OBJECT" in out
    assert "  turned      " not in out, "no one turn is the plate's"
    assert "It asks about each of the 3 parts in turn" in out
    assert out.count("outcome        success") == 3
    (log,) = (tmp_path / "jobs").glob("*/job.log")
    for cap in caps:
        assert f"part: {cap.resolve()}" in log.read_text()


def test_print_job_takes_context_for_one_part_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    caps = _caps(tmp_path)
    with pytest.raises(SystemExit):
        _script().main([str(caps[0]), str(caps[1]), "--context", str(tmp_path)])
    assert "--context is for one part" in capsys.readouterr().err


def test_print_job_refuses_several_parts_beside_a_sliced_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    caps = _caps(tmp_path)
    with pytest.raises(SystemExit):
        _script().main([str(caps[0]), str(caps[1]), "--sliced", str(caps[2])])
    assert "give one of a part" in capsys.readouterr().err


def test_print_job_names_the_file_that_isnt_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Path,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _script()
    monkeypatch.setattr(script, "WORK_DIR", tmp_path / "jobs")
    cap = _caps(tmp_path)[0]
    with pytest.raises(SystemExit):
        script.main([str(cap), str(tmp_path / "gone.stl"), "--settings", str(settings)])
    assert "gone.stl is not a file" in capsys.readouterr().err


def test_the_dashboards_card_says_how_a_plate_prints() -> None:
    import jinja2

    card = (Path(__file__).resolve().parents[1] / "ha-configs" / "lovelace_3dprint.yaml"
            ).read_text()
    start = card.index("{% set plate")
    shown = jinja2.Environment().from_string(card[start:card.index("{% endif %}", start) + 11])
    why = "3 parts on one plate, printed BY LAYER, all at once: they don't fit"
    assert shown.render(state_attr=lambda *_: why).strip() == f"**Plate:** {why}"
    assert shown.render(state_attr=lambda *_: None).strip() == "", "one part's card is as it was"


# Found by the review of mqucifer/print-shop#234
# --------------------------------------------------------------------------


def test_a_correction_the_plate_cant_be_sliced_with_is_taken_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path, "top_shell_layers=6 because flat", "wall_loops=99", "ok",
              orca=PlateOrca(wont=("wall_loops", "99")),
              use=profiles(tmp_path / "profiles", {"top_shell_layers": "4"}))
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.press("approve", job)
    assert rig.asked == 1, "the answers weren't lost"
    assert "NOT DONE, your last answer:" in rig.shown[2] and "exit 155" in rig.shown[2]
    assert "NOT DONE" not in rig.shown[1]
    row = rig.ledger.rows()[0]
    assert [(c.key, c.value) for c in row.corrections or ()] == [("top_shell_layers", "6")]
    assert rig.orca.calls[-1]["process"]["wall_loops"] != "99"


def test_a_pose_sent_back_that_doesnt_fit_is_taken_back(tmp_path: Path) -> None:
    lying = box(tmp_path / "lying.stl", 240, 130, 5)
    standing = box(tmp_path / "standing.stl", 240, 5, 130)
    rig = Rig(tmp_path, "wall_loops=4 because it's thin", "orientation", "2", "2",
              "lay it down", "ok")
    with rig.daemon:
        job = rig.gated([lying, standing])
        rig.press("approve", job)
    assert "NOT DONE, your last answer:" in rig.shown[-1]
    assert "don't all fit on one plate" in rig.shown[-1]
    one, two = rig.ledger.rows()
    assert two.rotation_deg == (0.0, 0.0, 0.0) and two.orientation_source == "as_laid"
    assert [c.key for c in two.corrections or ()] == ["wall_loops"], "what stood before it stands"
    assert one.corrections == two.corrections


def test_a_plates_lesson_reaches_the_same_plate_again(tmp_path: Path) -> None:
    pin = box(tmp_path / "pin.stl", 8, 8, 30)
    lid = box(tmp_path / "lid.stl", 80, 80, 4)
    rig = Rig(tmp_path, "wall_loops=3 because it's enough", "ok")
    with rig.daemon:
        job = rig.gated([pin, lid])
        rig.press("approve", job)
    [lesson] = rig.ledger.query("SELECT id, traits FROM print_lessons")
    print_lessons.accept(rig.ledger, lesson["id"], "reviewer: test")
    given = [g.id for row in rig.ledger.rows()
             for g in print_lessons.for_part(rig.ledger, row.features or ())]
    assert lesson["id"] in given, "a key no one part has would never be given"
    [evidence] = rig.ledger.query("SELECT detail FROM print_lesson_evidence")
    assert len(json.loads(evidence["detail"])["plate"]["traits"]) == 2


def test_the_plates_features_dont_make_a_part_thats_neither(tmp_path: Path) -> None:
    from print_shop.setup.traits import traits

    square = box(tmp_path / "square.stl", 120, 120, 10)
    bar = box(tmp_path / "bar.stl", 30, 10, 10)
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated([square, bar])
        state = rig.daemon._graph.get_state({"configurable": {"thread_id": job}}).values
        rig.press("reject", job)
    from print_shop.orchestrator.plate_job import together

    assert "long_and_narrow" not in traits(together(tuple(state["parts"])).features)


def test_a_long_and_narrow_part_is_still_the_plates_risk(tmp_path: Path) -> None:
    from print_shop.orchestrator.plate_job import together
    from print_shop.setup.traits import traits

    rail = box(tmp_path / "rail.stl", 150, 10, 10)
    square = box(tmp_path / "square.stl", 160, 100, 10)
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated([square, rail])
        state = rig.daemon._graph.get_state({"configurable": {"thread_id": job}}).values
        rig.press("reject", job)
    assert "long_and_narrow" in traits(together(tuple(state["parts"])).features)


def test_a_plate_again_is_one_recipe_from_any_parts_page(tmp_path: Path) -> None:
    rig = Rig(tmp_path, "orientation", "1", "2", "the mark goes on the side", "ok")
    caps = _caps(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        rig.press("approve", job)
    desk = Desk(rig.path, rig.work, tmp_path / "caps")
    recipes = []
    for n in (1, 2, 3):
        found = desk.job(row_id(job, n))
        assert found is not None and found.row is not None
        recipes.append(again(found.row, found.part_path, 1, found.part_found, found.plate_parts,
                             found.plate_rows))
    assert len({r.command for r in recipes}) == 1 and "--as-laid" in recipes[0].command
    assert len({r.notes for r in recipes}) == 1
    assert any("Part 1, cap-tight.stl: the pose was sent back" in n
               and "then 1 for the part" in n for n in recipes[1].notes)


def test_the_jobs_copy_keeps_its_parts_context(tmp_path: Path) -> None:
    from print_shop.setup.context import read_context

    caps = _caps(tmp_path)
    sidecar = caps[0].with_name("cap-tight.part.json")
    sidecar.write_text(json.dumps({"fits": [{"name": "over the post", "play": 0.1}]}))
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        rig.press("approve", job)
    copy = rig.work / job / "given" / "part1_cap-tight.stl"
    assert [f.name for f in read_context(copy).fits] == ["over the post"]

    caps[0].unlink()
    desk = Desk(rig.path, rig.work, tmp_path / "caps")
    found = desk.job(job)
    assert found is not None and found.row is not None
    assert found.plate_parts[0] == (copy, True)
    copy.with_name("part1_cap-tight.part.json").unlink()
    lost = again(found.row, found.part_path, 1, True, found.plate_parts, found.plate_rows)
    assert any("its fits, seen faces and side would be lost" in n for n in lost.notes)


def test_past_prints_show_a_plate_as_one_print(tmp_path: Path) -> None:
    from print_shop.setup.features import measure
    from print_shop.setup.loop import _past_prints

    features = measure(box(tmp_path / "cap.stl", 10, 10, 4), 100)
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(record().model_copy(update={"features": features}))
    fixed = Change(key="wall_loops", preset="2", value="3", reason="enough", source=Source.SPONSOR)
    ledger.record_start_requested(
        *_plate_records(count=8, features=features, corrections=(fixed,)))
    part: Any = SimpleNamespace(ledger=ledger, lessons=(),
                                facts=SimpleNamespace(features=features))
    state: Any = SimpleNamespace(looked_up=[])
    said = _past_prints(part, state, None)
    assert said.count("wall_loops=3") == 1, "one decision isn't eight confirmations"
    assert "a plate of 8, printed by object" in said and "job job-1" in said


def test_the_desks_list_counts_the_plates_frames_for_each_part(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.press("approve", job)
        _until(lambda: all(r.ended_at for r in rig.ledger.rows()), "the end")
    counts = {j.frames for j in Desk(rig.path, rig.work).jobs()}
    assert counts == {len(rig.ledger.frames(job))}


# The ledger
# --------------------------------------------------------------------------


def _layout(count: int) -> PlateLayout:
    return PlateLayout(sequence=BY_OBJECT, why="checked", bed=(256, 256), objects=tuple(
        OnPlate(n=n, name=f"cap-{n}.stl", x=50.0 * n, y=60, where="front left", height_mm=8,
                top_layer=40 * n, order=n, layers=(40 * n - 39, 40 * n))
        for n in range(1, count + 1)))


def _plate_records(job: str = JOB, count: int = 3, **update: Any) -> list[PrintRecord]:
    parts = tuple(GivenPart(name=f"cap-{n}.stl", path=f"/made-up/cap-{n}.stl", sha256=str(n) * 64)
                  for n in range(1, count + 1))
    return [
        record().model_copy(update={
            "job_id": row_id(job, n), "stl_name": parts[n - 1].name,
            "stl_sha256": parts[n - 1].sha256,
            "plate": PlateRow(job_id=job, n=n, parts=parts, layout=_layout(count)),
            **update,
        })
        for n in range(1, count + 1)
    ]


def test_a_rows_id_gives_back_its_job() -> None:
    assert [plate_job(row_id(JOB, n)) for n in (1, 2, 12)] == [JOB] * 3
    assert plate_job("job-1") == "job-1", "no uuid: an older id is its own"
    assert plate_job("job-log-1") == "job-log-1"


def test_a_plate_is_written_whole_or_not_at_all(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    first, second, third = _plate_records()
    with pytest.raises(LedgerError):
        ledger.record_start_requested(first, second, third.model_copy(
            update={"job_id": second.job_id}))
    assert ledger.rows() == []


def test_a_plate_starts_fails_and_ends_as_one_print(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(*_plate_records())
    ledger.record_start_failed(JOB, "TimeoutError: no start seen")
    assert all(r.start_error for r in ledger.job_rows(JOB))
    assert [r.job_id for r in ledger.unconfirmed_starts()] == [JOB]
    ledger.record_started(JOB)
    assert [r.job_id for r in ledger.running()] == [JOB]
    assert len({r.started_at for r in ledger.rows()}) == 1
    ledger.record_ended(JOB, 9)
    assert ledger.running() == []
    assert len(ledger.awaiting_answer()) == 3


def test_an_answer_is_one_parts_alone(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(*_plate_records())
    ledger.record_started(JOB)
    ledger.record_ended(JOB, 9)
    ledger.record_answer(f"{JOB}-2", Answer(result=Result.FAILED, symptom=Symptom.WARPING))
    assert [r.result for r in ledger.job_rows(JOB)] == [None, Result.FAILED, None]
    assert ledger.job_rows(f"{JOB}-3") == ledger.job_rows(JOB)


def test_another_job_is_untouched_by_a_plates_start_and_end(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(record())
    ledger.record_start_requested(*_plate_records())
    ledger.record_started(JOB)
    ledger.record_ended(JOB, 9)
    alone = ledger.get("job-1")
    assert alone is not None and alone.started_at is None and alone.ended_at is None
    assert alone.plate is None and alone.heads_job and alone.part_said == alone.stl_name


def test_a_ledger_from_before_reads_and_takes_a_plate(tmp_path: Path) -> None:
    """A made-up ledger as main writes it: no `plate_objects`."""
    path = tmp_path / "l.sqlite"
    before = Ledger(path)
    before.record_start_requested(record())
    before.close()
    db = sqlite3.connect(path)
    db.execute("DROP TABLE plate_objects")
    db.commit()
    version = db.execute("PRAGMA user_version").fetchone()[0]
    db.close()

    with Ledger.read_only(path) as reading:
        [row] = reading.rows()
        assert row.plate is None and reading.job_rows("job-1") == [row]
        assert reading.query("SELECT 1 FROM sqlite_master WHERE name='plate_objects'") == []
    with Ledger(path) as ledger:
        ledger.record_start_requested(*_plate_records())
        assert len(ledger.rows()) == 4
        assert ledger.query("PRAGMA user_version")[0]["user_version"] == version, "no bump"


def test_describe_says_the_figures_are_the_whole_plates(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(*_plate_records())
    row = ledger.get(f"{JOB}-2")
    assert row is not None
    text = describe(row)
    assert "part 2 of 3    cap-2.stl" in text and f"its row        {JOB}-2" in text
    assert "place          at x 100 y 60, front left" in text and "BY OBJECT" in text
    assert "the whole plate's" in text


# Lessons: a plate is one print
# --------------------------------------------------------------------------


def _with_a_lesson(tmp_path: Path) -> tuple[Ledger, int]:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(record())
    lesson = print_lessons.write(ledger, "job-1", "reviewer: test", "four walls on a cap",
                                 ["small_bed_contact"], setting="wall_loops=4")
    print_lessons.accept(ledger, lesson, "reviewer: test")
    return ledger, lesson


def _printed_with(
    ledger: Ledger, lesson: int, job: str, *results: Result, flaw: Symptom = Symptom.NONE,
) -> None:
    given = GivenLesson(id=lesson, text="four walls on a cap", key="wall_loops", value="4",
                        job_id="job-1", stl_name="cap.stl", traits=("small_bed_contact",),
                        reviewed_by="reviewer: test", applied=True)
    stood = Change(key="wall_loops", preset="2", value="4", reason="learned",
                   source=Source.LESSON, lesson=lesson)
    records = _plate_records(job, len(results),
                             proposal=Proposal(changes=(stood,), lessons=(given,)))
    ledger.record_start_requested(*records)
    ledger.record_started(job)
    ledger.record_ended(job, 9)
    for n, result in enumerate(results, 1):
        symptom = flaw if result is Result.SUCCESS else Symptom.STRINGING
        ledger.record_answer(row_id(job, n), Answer(result=result, symptom=symptom))


def test_a_lesson_on_a_plate_is_given_and_scored_once(tmp_path: Path) -> None:
    ledger, lesson = _with_a_lesson(tmp_path)
    _printed_with(ledger, lesson, JOB, *[Result.SUCCESS] * 3)
    assert [r["job_id"] for r in ledger.query("SELECT job_id FROM job_lessons")] == [JOB]
    assert list(print_lessons.latest_scores(ledger, lesson)) == [JOB]
    assert print_lessons.to_promote(ledger) == [], "one plate is one print, not three"


def test_a_plate_with_a_failed_part_is_no_print_toward_promoting(tmp_path: Path) -> None:
    ledger, lesson = _with_a_lesson(tmp_path)
    for k in range(print_lessons.USED_TO_PROMOTE):
        job = f"0a1b2c3d-0000-4000-8000-00000000000{k}"
        _printed_with(ledger, lesson, job, Result.SUCCESS, Result.FAILED, Result.SUCCESS)
    assert print_lessons.to_promote(ledger) == [], "the first part's success isn't the plate's"
    for k in range(print_lessons.USED_TO_PROMOTE):
        job = f"0a1b2c3d-0000-4000-8000-00000000001{k}"
        _printed_with(ledger, lesson, job, *[Result.SUCCESS] * 3)
    [(_, good)] = print_lessons.to_promote(ledger)
    assert len(good) == print_lessons.USED_TO_PROMOTE


def test_the_order_is_no_lesson_and_each_outcome_is_its_parts(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    order = Change(key=SEQUENCE, preset="by layer", value="by layer", reason="to pause",
                   source=Source.SPONSOR)
    ledger.record_start_requested(*_plate_records(corrections=(order,)))
    assert ledger.query("SELECT * FROM print_lessons") == []
    ledger.record_started(JOB)
    ledger.record_ended(JOB, 9)
    ledger.record_answer(f"{JOB}-3", Answer(result=Result.FAILED, symptom=Symptom.STRINGING))
    [drawn] = ledger.query("SELECT job_id FROM print_lessons")
    assert drawn["job_id"] == f"{JOB}-3"
    [evidence] = ledger.query("SELECT detail FROM print_lesson_evidence")
    detail = json.loads(evidence["detail"])
    assert detail["plate"]["n"] == 3 and detail["plate"]["sequence"] == BY_OBJECT


# The print desk
# --------------------------------------------------------------------------


def test_the_desk_lists_each_part_and_runs_the_whole_plate_again(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    caps = _caps(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        (rig.work / job / "job.log").write_text("the plate's log\n")
        rig.press("approve", job)
        _until(lambda: all(r.ended_at for r in rig.ledger.rows()), "the end")
    desk = Desk(rig.path, rig.work, tmp_path / "caps")

    listed = {j.job_id: j for j in desk.jobs()}
    assert listed[f"{job}-2"].part == "cap-mid.stl (part 2 of 3)"
    assert listed[job].print_time_s and listed[f"{job}-2"].print_time_s is None
    assert "cap-loose.stl (part 3 of 3)" in index_page(desk.on_hand(), desk.jobs(), rig.path)

    second = desk.job(f"{job}-2")
    assert second is not None and second.row is not None
    assert second.log == "the plate's log\n" and second.part_found
    assert desk.frame_path(f"{job}-2", 0) is not None
    page = job_page(second)
    assert "On a plate with others" in page and f'href="/job/{job}-3"' in page

    runs = again(second.row, second.part_path, 1, second.part_found, second.plate_parts)
    words = shlex.split(runs.command)
    assert [w for w in words if w.endswith(".stl")] == [str(c.resolve()) for c in caps]
    assert "--as-laid" in words and "--context" not in words

    caps[2].unlink()
    moved = desk.job(job)
    assert moved is not None
    assert all(found for _, found in moved.plate_parts), "the job's own copy has its bytes"


def test_a_plate_at_the_gate_reads_back_from_the_checkpoint_as_its_types(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        saver, conn = open_checkpointer(rig.work / "jobs.sqlite")
        app = build_graph(profiles(tmp_path / "p"), outdir_root=rig.work, checkpointer=saver,
                          plate_slicer=rig.orca)
        state = app.get_state({"configurable": {"thread_id": job}}).values
        conn.close()
        rig.press("reject", job)
    assert isinstance(state["plate"], PlateLayout)
    assert state["parts"][1].name == "cap-mid.stl" and state["parts"][1].features
    assert state["orientations"][2].proposal.value.height_mm == 8.0
    assert not [r for r in caplog.records if "unregistered type" in r.getMessage()
                or "Blocked deserialization" in r.getMessage()]


def test_a_plates_model_settings_are_proposals_and_not_applied_untaken(tmp_path: Path) -> None:
    """mqucifer/print-shop#242 on a plate: the settings call works beside the review, and what
    it comes to is proposed, never applied by arriving."""
    from tests.reviewing import WAIT_S

    rig = Rig(tmp_path, "ok", "ok")
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        assert rig.press("approve", job).printed
    [first, *_] = rig.reviews
    assert first.suggesting is not None, "beside the review, not before it"
    assert first.suggesting.wait(WAIT_S)
    assert first.proposal.get("wall_loops") is None, "the review starts without the model's"
    row = rig.ledger.get(job)
    assert row is not None and row.proposal is not None
    assert row.proposal.get("wall_loops") is None, "not taken, so not applied"
    proposed = [c.key for found in row.proposal.suggested for c in found.proposed]
    assert "wall_loops" in proposed, "what it proposed is kept"
