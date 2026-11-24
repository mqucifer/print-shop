"""A prepared job waits for the person with no deadline, and survives a restart.

mqucifer/print-shop#209. A job's preparation takes 12 to 44 minutes, and an hour's limit at the
gate threw it away when the person stepped away; a closed terminal threw it away too. Now nothing
that waits for the person has a deadline, a waiting job is taken up again after a restart where
it was, and Start, given when it's time to print, checks again what could have changed in place
of a clock. Every service is a fake, the shop is made up, and a restart is a second `Daemon` on
the same jobs folder, the first one closed with its job unfinished, as Ctrl-C leaves it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import command_topic, gate_topic
from print_shop.core.state import SliceResult
from print_shop.inventory import Inventory
from print_shop.ledger import Ledger
from print_shop.orchestrator import daemon as daemon_module
from print_shop.orchestrator.daemon import BED_AT_TERMINAL, BED_ON_CARD, Daemon
from print_shop.orchestrator.kept import KEPT_NAME, WaitingJobs, Waits
from print_shop.printer.centauri import PrinterSnapshot
from print_shop.setup import Proposer
from print_shop.setup.intake import Answer, Question
from print_shop.setup.proposal import Change, Source
from print_shop.setup.questions import PURPOSE
from print_shop.setup.review import Review, ReviewRequest
from print_shop.setup.waiting import Waiting
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.store import Store, choose
from tests.fakes import CI_PROFILES, STORE, FramePrinter, MemoryBridge, RecordedSlicer, frame
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_review import _model
from tests.test_waiting import Person

WAIT_S = 30.0  # for anything that should take milliseconds
DAYS_3 = 3 * 24 * 3600.0
WHITE, BLACK = 1, 2


class Gone(Exception):
    """The person left: the process this stood for has ended."""


class Leaves:
    """Answers each set of questions as scripted, then leaves at the next: it waits until the
    test ends, as a closed terminal's prompt would, and never answers."""

    def __init__(self, *sets: tuple[Answer, ...]) -> None:
        self.sets = list(sets)
        self.asked: list[Sequence[Question]] = []
        self.left = threading.Event()
        self.over = threading.Event()

    def __call__(self, questions: Sequence[Question]) -> tuple[Answer, ...]:
        self.asked.append(questions)
        if self.sets:
            return self.sets.pop(0)
        self.left.set()
        self.over.wait(WAIT_S)
        raise Gone


class Reviewer:
    """Answers each review as scripted, then leaves at the next."""

    def __init__(self, *answers: Review) -> None:
        self.answers = list(answers)
        self.seen: list[ReviewRequest] = []
        self.left = threading.Event()
        self.over = threading.Event()

    def __call__(self, request: ReviewRequest) -> Review:
        self.seen.append(request)
        if self.answers:
            return self.answers.pop(0)
        self.left.set()
        self.over.wait(WAIT_S)
        raise Gone


class Counted:
    """A model's reply, counting the calls."""

    def __init__(self, reply: Callable[..., dict[str, Any]]) -> None:
        self.reply = reply
        self.calls = 0

    def __call__(self, *args: Any) -> dict[str, Any]:
        self.calls += 1
        return self.reply(*args)


def _top_question(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The model's own question: one, past print-shop's two."""
    option = {"settings": [], "impact": "", "recommended": False, "why": ""}
    return {"questions": [{"feature": "flat_top", "text": "How smooth is its made-up top?",
                           "options": [{**option, "label": "as it comes", "recommended": True,
                                        "why": "it's a cube"},
                                       {**option, "label": "smoother", "impact": "slower"}]}]}


class Shop:
    """A made-up shop on one jobs folder: white and black PLA, white loaded, a 0.4 mm nozzle
    fitted, and the profiles' store copied so a test can change it."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.ledger_path = tmp_path / "ledger.sqlite"
        self.store = tmp_path / "store"
        shutil.copytree(STORE, self.store)
        self.stl = write_stl("cube", tmp_path)
        with Inventory(self.ledger_path) as stock:
            stock.add_printer("cc1", "Made-up printer")
            stock.fit(0.4)
            for colour in ("white", "black"):
                stock.add_spool(manufacturer="Elegoo", type="PLA", variety="Elegoo PLA",
                                colour=colour)
            stock.load(WHITE)
            material = stock.material()
        choice = choose(Store(self.store), nozzle_mm=0.4, filament=material.variety)
        self.profiles = CI_PROFILES.model_copy(update={
            "used": choice.used(self.store).model_copy(update={"material": material})})
        self.bridge = MemoryBridge()
        self.printer = FramePrinter()
        self.slicer = RecordedSlicer()

    def daemon(self, *, profiles: SlicerProfiles | None = None, asker: Any = None,
               reviewer: Any = None, questions: Any = None, model: Any = None,
               waiting: Waiting | None = None, printer: Any = None) -> Daemon:
        """A daemon, as a run of print_job.py makes one: the same jobs folder each time."""
        self.slicer = RecordedSlicer()
        return Daemon(
            cfg=CFG, profiles=profiles or self.profiles, workdir=self.work,
            printer=printer or self.printer, slicer=self.slicer,
            bridge=lambda cfg: self.bridge, ledger=Ledger(self.ledger_path),
            proposer=Proposer(ask=model or _model, ask_questions=questions or _top_question),
            asker=asker or (lambda questions: tuple(Answer(choice=0) for _ in questions)),
            reviewer=reviewer or (lambda request: Review(approved=True)),
            waiting=waiting, store_root=self.store,
        )

    def waiting(self) -> list[tuple[str, Waits]]:
        return [(j.job_id, j.waits) for j in WaitingJobs(self.work / "jobs.sqlite").all()]

    def gate_open(self) -> bool:
        return bool(self.bridge.retained_json(gate_topic(CFG)).get("pending"))

    def held(self) -> str:
        """What the card says of the last Start that was held."""
        wait_until(lambda: bool(self.bridge.retained_json(gate_topic(CFG)).get("held")))
        return str(self.bridge.retained_json(gate_topic(CFG))["held"])

    def press(self, decision: str) -> None:
        self.bridge.press(CFG, decision)


def _script() -> Any:
    """scripts/print_job.py, loaded as a module."""
    spec = importlib.util.spec_from_file_location(
        "print_job", Path(__file__).parents[1] / "scripts" / "print_job.py")
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def wait_until(ok: Callable[[], bool], within_s: float = WAIT_S) -> None:
    end = time.monotonic() + within_s
    while not ok():
        assert time.monotonic() < end, "it never happened"
        time.sleep(0.02)


@pytest.fixture
def shop(tmp_path: Path) -> Shop:
    return Shop(tmp_path)


@pytest.fixture
def three_days(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """A clock that jumps three days when told: every wait that reads it is three days on."""
    real = time.monotonic
    jumped = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + jumped[0])

    def jump() -> None:
        jumped[0] += DAYS_3

    return jump


# Ready, for days and through a restart
# --------------------------------------------------------------------------


def test_a_job_left_ready_for_three_days_and_through_a_restart_starts_without_slicing_again(
    shop: Shop, three_days: Callable[[], None],
) -> None:
    first = shop.daemon()
    with first:
        job_id = first.submit(shop.stl)
        assert first.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
        before = shop.bridge.button(CFG, "approve")  # the press as the card held it
        sliced = list(shop.slicer.calls)
        three_days()
        time.sleep(0.5)
        assert shop.gate_open() and first.pending_gate() is not None, "three days ended nothing"
        assert "answer_by" not in (first.pending_gate() or {})
    assert sliced, "it was sliced before the restart"
    assert shop.waiting() == [(job_id, Waits.READY)]
    assert not shop.gate_open(), "a closed process leaves no gate on the card"

    second = shop.daemon()
    with second:
        assert second.resume(job_id) == shop.stl.name
        gate = second.wait_for_gate(job_id, timeout_s=WAIT_S)
        assert gate is not None and gate["gate"] == "gate.before_print"
        # A press from before the restart names a word that's gone, and starts nothing.
        shop.bridge.deliver(command_topic(CFG), before)
        time.sleep(0.3)
        assert shop.gate_open() and shop.printer.verbs == []
        shop.press("approve")
        assert second.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    assert shop.slicer.calls == [], "taken up ready: nothing was sliced again"
    assert shop.printer.verbs[-2:] == ["upload", "start"]
    assert shop.waiting() == []


def test_a_restart_after_start_and_before_the_printer_leaves_it_ready_not_approved(
    shop: Shop,
) -> None:
    """An approval is never kept: a Start the restart cut short is asked for again."""
    leaving = threading.Event()
    over = threading.Event()

    class SlowUpload(FramePrinter):
        def upload(self, local_path: Path) -> str:
            leaving.set()
            over.wait(WAIT_S)
            raise Gone

    first = shop.daemon(printer=SlowUpload())
    with first:
        job_id = first.submit(shop.stl)
        first.wait_for_gate(job_id, timeout_s=WAIT_S)
        shop.press("approve")
        assert leaving.wait(WAIT_S), "it went on to the upload"
    try:
        second = shop.daemon()
        with second:
            second.resume(job_id)
            gate = second.wait_for_gate(job_id, timeout_s=WAIT_S)
            assert gate is not None and gate["gate"] == "gate.before_print"
            time.sleep(0.3)
            assert shop.printer.verbs == [], "nothing was sent: it waits for a new Start"
            shop.press("reject")
            assert not second.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    finally:
        over.set()
    with Ledger(shop.ledger_path) as ledger:
        assert ledger.get(job_id) is None


# At the questions, and at the review
# --------------------------------------------------------------------------


def test_a_restart_at_the_questions_comes_back_to_them_with_what_was_answered(
    shop: Shop,
) -> None:
    """print-shop's two are answered, then the person leaves at the model's. After the restart
    only the model's are asked."""
    first_person = Person("3", "1")
    leaves = Leaves()

    def first_asker(questions: Sequence[Question]) -> tuple[Answer, ...]:
        if first_person.lines:
            return first_person.asker()(questions)
        return leaves(questions)

    questions = Counted(_top_question)
    first = shop.daemon(asker=first_asker, waiting=first_person.waiting(), questions=questions)
    try:
        with first:
            job_id = first.submit(shop.stl)
            assert leaves.left.wait(WAIT_S), "it reached the model's question"
        assert shop.waiting() == [(job_id, Waits.QUESTIONS)]
        stock_asked = first_person.text

        second_person = Person("2")
        reviews = Reviewer(Review(approved=True))
        second = shop.daemon(asker=second_person.asker(), waiting=second_person.waiting(),
                             reviewer=reviews)
        with second:
            second.resume(job_id)
            assert second.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
            shop.press("reject")
            second.wait_for_finish(job_id, timeout_s=WAIT_S)
    finally:
        leaves.over.set()
    assert "made-up top" not in stock_asked and PURPOSE.text not in second_person.text
    assert "How smooth is its made-up top?" in second_person.text, "the model's is asked"
    assert len(reviews.seen) == 1
    kept = json.loads((shop.work / job_id / KEPT_NAME).read_text())
    assert [a["choice"] for a in kept["intake"]["answers"]] == [2, 0, 1], "all three answers"


def test_a_restart_at_the_review_comes_back_to_it_with_the_corrections_and_the_slice(
    shop: Shop,
) -> None:
    correction = Change(key="wall_loops", preset="", value="4", reason="I handle it",
                        source=Source.SPONSOR)
    first_review = Reviewer(Review(corrections=(correction,)))
    first_questions, first_model = Counted(_top_question), Counted(_model)
    first = shop.daemon(reviewer=first_review, questions=first_questions, model=first_model)
    try:
        with first:
            job_id = first.submit(shop.stl)
            assert first_review.left.wait(WAIT_S), "it reached the second review"
        assert shop.waiting() == [(job_id, Waits.REVIEW)]
        before = first_review.seen[-1]

        asked: list[Sequence[Question]] = []

        def asker(questions: Sequence[Question]) -> tuple[Answer, ...]:
            asked.append(questions)
            return tuple(Answer(choice=0) for _ in questions)

        second_review = Reviewer(Review(approved=True))
        second_questions, second_model = Counted(_top_question), Counted(_model)
        second = shop.daemon(reviewer=second_review, questions=second_questions,
                             model=second_model, asker=asker)
        with second:
            second.resume(job_id)
            assert second.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
            shop.press("reject")
            second.wait_for_finish(job_id, timeout_s=WAIT_S)
    finally:
        first_review.over.set()
    assert asked == [], "the questions weren't asked again"
    assert (second_questions.calls, second_model.calls) == (0, 0), "nor the model"
    [back] = second_review.seen
    changed = back.proposal.get("wall_loops")
    assert changed is not None and changed.value == "4" and changed.source is Source.SPONSOR
    assert back.slice.print_time_s == before.slice.print_time_s
    assert back.proposal.changes == before.proposal.changes, "the same slice, as corrected"


# Start checks again what could have changed
# --------------------------------------------------------------------------


@pytest.fixture
def ready(shop: Shop) -> Iterator[tuple[Shop, Daemon, str]]:
    daemon = shop.daemon()
    with daemon:
        job_id = daemon.submit(shop.stl)
        assert daemon.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
        yield shop, daemon, job_id
        if daemon._active_job_id == job_id:
            if shop.gate_open():
                shop.press("reject")
            daemon.wait_for_finish(job_id, timeout_s=WAIT_S)


def _held_says(ready: tuple[Shop, Daemon, str], start: Callable[[], None]) -> str:
    shop, daemon, job_id = ready
    start()
    said = shop.held()
    assert shop.gate_open(), "the gate stays open"
    assert "upload" not in shop.printer.verbs and "start" not in shop.printer.verbs
    return said


def test_a_changed_gcode_stops_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, daemon, job_id = ready
    gate = daemon.pending_gate()
    assert gate is not None
    with Path(str(gate["gcode_path"])).open("a") as gcode:
        gcode.write("; one more line\n")
    said = _held_says(ready, lambda: shop.press("approve"))
    assert "was changed after it was sliced. Reject and run the job again" in said


def test_a_changed_part_stops_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, _, _ = ready
    with shop.stl.open("ab") as part:
        part.write(b"\n")
    said = _held_says(ready, lambda: shop.press("approve"))
    assert f"the part's file, {shop.stl.name}, was changed after this job was prepared" in said


def test_a_changed_profile_stops_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, _, _ = ready
    used = shop.profiles.used
    assert used is not None
    filament = shop.store / used.filament.file
    filament.write_text(json.dumps({**json.loads(filament.read_text()), "made_up": "1"}))
    said = _held_says(ready, lambda: shop.press("approve"))
    assert f'the filament profile "{used.filament.name}" was changed' in said


def test_a_changed_spool_stops_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, _, _ = ready
    with Inventory(shop.ledger_path) as stock:
        stock.load(BLACK)
    said = _held_says(ready, lambda: shop.press("approve"))
    assert "this job was prepared for spool #1" in said and "black" in said


def test_a_changed_nozzle_stops_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, _, _ = ready
    with Inventory(shop.ledger_path) as stock:
        stock.fit(0.6)
    said = _held_says(ready, lambda: shop.press("approve"))
    assert ("this job was sliced for a 0.4 mm nozzle, and the stock has a 0.6 mm one fitted on "
            "cc1") in said


def test_a_busy_printer_stops_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, _, _ = ready
    shop.printer.before = "printing"
    said = _held_says(ready, lambda: shop.press("approve"))
    assert "the printer is not free to start a new job" in said
    assert said.endswith("Start again when it's free")


def test_a_bed_not_confirmed_clear_stops_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, daemon, job_id = ready

    def unsaid() -> None:
        assert daemon.start(job_id, bed_clear=False) == ""

    said = _held_says(ready, unsaid)
    assert "the bed isn't confirmed clear" in said


def test_a_check_that_names_what_to_do_lets_the_next_start_through(
    ready: tuple[Shop, Daemon, str],
) -> None:
    """Held, then mended, then started: from the terminal, the bed said clear there."""
    shop, daemon, job_id = ready
    told: list[str] = []
    shop.printer.before = "printing"
    assert daemon.start(job_id, bed_clear=True) == ""
    shop.held()
    shop.printer.before = "idle"
    assert daemon.start(job_id, bed_clear=True) == ""
    result = daemon.wait_for_finish(job_id, timeout_s=WAIT_S, tell=told.append)
    assert result.printed
    assert told[-1] == f"Start taken, the bed confirmed clear {BED_AT_TERMINAL}."
    assert daemon.start(job_id, bed_clear=True) == "it was started here"


def test_the_cards_start_says_how_the_bed_was_confirmed(ready: tuple[Shop, Daemon, str]) -> None:
    shop, daemon, job_id = ready
    told: list[str] = []
    shop.press("approve")
    assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S, tell=told.append).printed
    assert told == [f"Start taken, the bed confirmed clear {BED_ON_CARD}."]
    assert daemon.reject(job_id) == "it was started on the card"


# Presses
# --------------------------------------------------------------------------


def test_a_press_spent_on_a_held_start_starts_nothing_sent_again(
    ready: tuple[Shop, Daemon, str],
) -> None:
    shop, daemon, job_id = ready
    shop.printer.before = "printing"
    spent = shop.bridge.button(CFG, "approve")
    shop.press("approve")
    shop.held()
    assert shop.bridge.button(CFG, "approve") != spent, "the card has a new word for a press"
    shop.printer.before = "idle"
    shop.bridge.deliver(command_topic(CFG), spent)  # replayed, after what held it was mended
    time.sleep(0.3)
    assert shop.gate_open() and "start" not in shop.printer.verbs


def test_a_retained_press_starts_nothing(ready: tuple[Shop, Daemon, str]) -> None:
    shop, _, _ = ready
    shop.bridge.press(CFG, "approve", retain=True)
    time.sleep(0.3)
    assert shop.gate_open() and "start" not in shop.printer.verbs


def test_a_press_for_one_job_cant_start_another(shop: Shop) -> None:
    """Job A waits ready from before a restart; a press naming it, at B's gate, starts neither."""
    first = shop.daemon()
    with first:
        a_id = first.submit(shop.stl)
        first.wait_for_gate(a_id, timeout_s=WAIT_S)
        a_press = json.loads(shop.bridge.button(CFG, "approve"))
    second = shop.daemon()
    with second:
        b_id = second.submit(shop.stl)
        second.wait_for_gate(b_id, timeout_s=WAIT_S)
        b_word = json.loads(shop.bridge.button(CFG, "approve"))["press"]
        for named in (a_press, {**a_press, "press": b_word}):
            shop.bridge.deliver(command_topic(CFG), named)
        time.sleep(0.3)
        assert shop.gate_open() and shop.printer.verbs == []
        shop.press("reject")
        second.wait_for_finish(b_id, timeout_s=WAIT_S)
    assert [job for job, _ in shop.waiting()] == [a_id], "A still waits, ready"


# The terminal's Start
# --------------------------------------------------------------------------


def test_the_terminal_start_asks_the_bed_and_only_y_starts(
    ready: tuple[Shop, Daemon, str],
) -> None:
    shop, daemon, job_id = ready
    script = _script()
    typed = iter(["go", "start", "n", "start", "y"])
    shown: list[str] = []
    forgot: list[int] = []
    thread = threading.Thread(target=script._start_here, args=(
        daemon, job_id, lambda prompt: next(typed), shown.append,
        lambda: forgot.append(len(shown))), daemon=True)
    thread.start()
    told: list[str] = []
    assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S, tell=told.append).printed
    thread.join(WAIT_S)
    assert shown[0] == "  Type start, or reject."
    assert shown.count(script.BED_QUESTION) == 2
    assert forgot == [2, 3], "what was typed ahead is dropped once the question is drawn"
    assert told[0].startswith("Not started: the bed isn't confirmed clear")
    assert told[-1] == f"Start taken, the bed confirmed clear {BED_AT_TERMINAL}."


# The gate's race, the issue's last Done when
# --------------------------------------------------------------------------


class SlowLedger(Ledger):
    """A ledger whose row for a job that ended unprinted takes `slow_s` to write."""

    slow_s = 1.5

    def record_unprinted(self, *args: Any, **kwargs: Any) -> Any:
        time.sleep(self.slow_s)
        return super().record_unprinted(*args, **kwargs)


class SlowSlicer(RecordedSlicer):
    slow_s: float = 0.4

    def __call__(self, stl_path: Path | str, profiles: SlicerProfiles,
                 outdir: Path | str) -> SliceResult:
        time.sleep(self.slow_s)
        return super().__call__(stl_path, profiles, outdir)


def test_a_job_abandoned_as_its_slice_ends_never_opens_a_gate(tmp_path: Path) -> None:
    """The slice ends while the abandoned job's row is being written: no gate opens, the
    printer's one job isn't held, and the job ends with the sentence the person was told."""
    (tmp_path / "work").mkdir()
    bridge = MemoryBridge()
    printer = FramePrinter()
    daemon = Daemon(
        cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work", printer=printer,
        slicer=SlowSlicer(), bridge=lambda cfg: bridge,
        ledger=SlowLedger(tmp_path / "ledger.sqlite"),
        proposer=Proposer(ask=_model, ask_questions=_top_question),
        asker=lambda questions: tuple(Answer(choice=0) for _ in questions),
        reviewer=lambda request: Review(approved=True),
    )
    opened: list[Any] = []
    publish = bridge.publish

    def watched(topic: str, payload: Any, retain: bool = False, qos: int = 1) -> None:
        if topic == gate_topic(CFG) and isinstance(payload, dict) and payload.get("pending"):
            opened.append(payload)
        publish(topic, payload, retain, qos)

    bridge.publish = watched  # type: ignore[method-assign]
    with daemon:
        job_id = daemon.submit(write_stl("cube", tmp_path))
        with pytest.raises(TimeoutError) as ran_out:
            daemon.wait_for_gate(job_id, timeout_s=0.2)
        result = daemon.wait_for_finish(job_id, timeout_s=WAIT_S)
    assert opened == [], "no gate opened for a job the person was told had timed out"
    assert (result.printed, result.error) == (False, str(ran_out.value))
    assert printer.verbs == []
    assert WaitingJobs(tmp_path / "work" / "jobs.sqlite").all() == []


def test_a_snapshot_that_fails_holds_start(ready: tuple[Shop, Daemon, str]) -> None:
    shop, _, _ = ready

    def unreadable() -> PrinterSnapshot:
        raise OSError("no route to host")

    shop.printer.snapshot = unreadable  # type: ignore[method-assign]
    said = _held_says(ready, lambda: shop.press("approve"))
    assert "the printer couldn't be read (OSError: no route to host). Start again" in said


def test_the_fake_printer_frames_are_as_this_file_expects() -> None:
    assert not frame("printing").ready_to_start and frame("idle").ready_to_start


def test_the_daemon_module_has_no_gate_deadline_left() -> None:
    assert not hasattr(daemon_module, "GATE_WAIT_S")


# print_job.py --resume
# --------------------------------------------------------------------------


def test_print_job_resume_takes_up_the_only_waiting_job_at_its_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.test_gate_shown import Desk  # noqa: PLC0415 - print_job.py on fakes

    desk = Desk(tmp_path, monkeypatch)
    assert desk.script.main(["--resume", "--settings", str(desk.settings)]) == 0
    assert "Nothing is waiting for you." in capsys.readouterr().out
    assert desk.script.main(["--resume", "no-such-job", "--settings", str(desk.settings)]) == 2

    desk.part = write_stl("cube", tmp_path)
    earlier = desk._build_run()
    with earlier.daemon:  # a run of print_job.py, closed at its gate
        job_id = earlier.daemon.submit(desk.part)
        assert earlier.daemon.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
    earlier.ledger.close()
    assert desk.script.main(["--list", "--settings", str(desk.settings)]) == 0
    assert f"job {job_id}  cube.stl  ready to print" in capsys.readouterr().out

    def reject_once_shown() -> None:
        wait_until(lambda: len(desk._shown) > 1)
        desk.bridge.press(CFG, "reject")

    threading.Thread(target=reject_once_shown, daemon=True).start()
    assert desk.script.main(["--resume", "--settings", str(desk.settings)]) == 0
    out = capsys.readouterr().out
    assert f"Job {job_id} (cube.stl), ready to print, taken up again." in out
    assert "Type start here, or Yes on the card. It waits until you start or reject it." in out
    assert "Nothing was started" in out
    assert WaitingJobs(desk.jobs / "jobs.sqlite").all() == []
    log = (desk.jobs / job_id / "job.log").read_text()
    assert f"resumed: job {job_id} (cube.stl), ready to print" in log


_TYPED_AHEAD = """
import importlib.util, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("print_job", Path("scripts/print_job.py"))
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class Daemon:
    started = False

    def pending_gate(self):
        return None if self.started else {"job_id": "job-1"}

    def decided(self, job_id):
        return ""

    def start(self, job_id, *, bed_clear):
        self.started = True
        print("BED", bed_clear, flush=True)
        return ""


print("READY", flush=True)
script._start_here(Daemon(), "job-1", input, print)
print("DONE", flush=True)
"""


def test_on_a_terminal_a_y_typed_ahead_never_answers_the_beds_question() -> None:
    """`start` and `y` typed together: the y was typed before the question was drawn, so it's
    dropped, and only what's typed after it answers. On a real terminal, with input() itself."""
    ours, theirs = os.openpty()
    root = Path(__file__).parents[1]
    child = subprocess.Popen(
        [sys.executable, "-c", _TYPED_AHEAD], stdin=theirs, stdout=theirs, stderr=theirs,
        cwd=root, start_new_session=True,
        env={**os.environ, "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root}"},
    )
    os.close(theirs)
    out = b""

    def until(word: bytes) -> None:
        nonlocal out
        end = time.monotonic() + WAIT_S
        while word not in out and time.monotonic() < end:
            if not select.select([ours], [], [], 0.2)[0]:
                continue
            try:
                got = os.read(ours, 65536)
            except OSError:
                break
            if not got:
                break
            out += got
        assert word in out, out.decode(errors="replace")[-1500:]

    try:
        until(b"READY")
        os.write(ours, b"start\ny\n")
        until(b"bed clear> ")
        time.sleep(0.3)
        os.write(ours, b"\n")
        until(b"DONE")
    finally:
        child.kill()
        child.wait(timeout=10)
        os.close(ours)
    assert b"BED False" in out and b"BED True" not in out


def test_a_start_typed_after_the_card_decided_is_told_so_at_once(
    ready: tuple[Shop, Daemon, str],
) -> None:
    """print-shop-ux, on b81f925: the terminal's prompt was open as the card started the job, and
    `start` then asked the bed's question before saying it was taken. Now it says so at once."""
    shop, daemon, job_id = ready
    script = _script()
    shown: list[str] = []

    def card_then_start(prompt: str) -> str:
        """The prompt is open; the card starts the job; then `start` is typed."""
        shop.press("approve")
        wait_until(lambda: bool(daemon.decided(job_id)))
        return "start"

    script._start_here(daemon, job_id, card_then_start, shown.append, lambda: None)
    assert shown == ["  Not taken: it was started on the card."]
    assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed


def test_a_reject_says_where_it_was_given(ready: tuple[Shop, Daemon, str]) -> None:
    shop, daemon, job_id = ready
    told: list[str] = []
    assert daemon.reject(job_id) == ""
    assert not daemon.wait_for_finish(job_id, timeout_s=WAIT_S, tell=told.append).printed
    assert told == ["Rejected here: nothing printed, and the job has ended."]
    assert daemon.start(job_id, bed_clear=True) == "it was rejected here"
