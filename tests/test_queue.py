"""Many files queue, and preparing runs apart from printing (mqucifer/print-shop#210).

Every service is a fake and the shop is made up: three spools, white and black Elegoo PLA and grey
Elegoo PETG, white loaded, on a made-up printer. A print that "runs" is the fake printer's, started
by a Start at the card or the terminal, and ended by the ledger's `record_ended`, as the watcher
records it. A restart is a second queue on the same jobs folder, the first one put by.
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import gate_topic
from print_shop.core.state import SliceResult
from print_shop.inventory import Inventory
from print_shop.ledger import Ledger, Result
from print_shop.orchestrator import print_queue
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.graph import build_graph
from print_shop.orchestrator.kept import KEPT_NAME, Kept, WaitingJobs, Waits
from print_shop.orchestrator.print_queue import Adding, Asked, Need, Preparing, PrintQueue
from print_shop.runtime import job_profiles
from print_shop.setup import Proposer
from print_shop.setup.intake import Answer
from print_shop.setup.proposal import Change, Source
from print_shop.setup.review import Review
from print_shop.slicer.orca import BedType, SlicerProfiles
from print_shop.slicer.store import Store
from tests.fakes import STORE, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_review import _model
from tests.test_waits_for_you import _top_question

WAIT_S = 30.0  # for anything that should take a moment
WHITE, BLACK, GREY = 1, 2, 3


def wait_until(ok: Callable[[], bool], within_s: float = WAIT_S) -> None:
    end = time.monotonic() + within_s
    while not ok():
        assert time.monotonic() < end, "it never happened"
        time.sleep(0.02)


class Shop:
    """A made-up shop on one jobs folder, with a queue over a daemon, as `print_job.py --queue`
    builds them, and fakes for the printer, the broker, Orca and the model."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.ledger_path = tmp_path / "ledger.sqlite"
        self.store = tmp_path / "store"
        shutil.copytree(STORE, self.store)
        with Inventory(self.ledger_path) as stock:
            stock.add_printer("cc1", "Made-up printer")
            stock.fit(0.4)
            for variety, colour in (("Elegoo PLA", "white"), ("Elegoo PLA", "black"),
                                    ("Elegoo PETG", "grey")):
                stock.add_spool(manufacturer="Elegoo", type=variety.split()[-1], variety=variety,
                                colour=colour)
            stock.load(WHITE)
        self.bridge = MemoryBridge()
        self.printer = FramePrinter()
        self.slicer = RecordedSlicer()
        self.ledger = Ledger(self.ledger_path)

    def part(self, name: str) -> Path:
        """A made-up cube under its own name: the recorded slice is the cube's."""
        folder = self.tmp / "parts" / name
        folder.mkdir(parents=True, exist_ok=True)
        cube = write_stl("cube", folder)
        return cube.rename(folder / f"{name}.stl")

    def daemon(self) -> Daemon:
        return Daemon(cfg=CFG, profiles=None, workdir=self.work, printer=self.printer,
                      bridge=lambda cfg: self.bridge, ledger=self.ledger,
                      store_root=self.store)

    def queue(self, daemon: Daemon, at_once: int = 2) -> PrintQueue:
        return PrintQueue(
            daemon=daemon, workdir=self.work, ledger=self.ledger,
            profiles_for=lambda material, into: job_profiles(
                material, into, bed=BedType.TEXTURED_PEI, store_root=self.store),
            preparing=Preparing(
                slicer=self.slicer,
                proposer=Proposer(ask=_model, ask_questions=_top_question),
                store=Store(self.store)),
            at_once=at_once,
        )

    def gate(self) -> dict[str, Any]:
        found: dict[str, Any] = self.bridge.retained_json(gate_topic(CFG))
        return found

    def need(self, queue: PrintQueue, job_id: str, need: Need) -> Asked:
        def found() -> bool:
            asked = queue.asked(job_id)
            return asked is not None and asked.need is need

        wait_until(found)
        asked = queue.asked(job_id)
        assert asked is not None
        return asked

    def answer(self, queue: PrintQueue, job_id: str) -> None:
        """Its questions answered, each with its first option: print-shop's two at once, then
        the model's when they come (mqucifer/print-shop#208), until its review."""
        def asking() -> bool:
            asked = queue.asked(job_id)
            return asked is not None and asked.need in (Need.QUESTIONS, Need.REVIEW)

        while True:
            wait_until(asking)
            asked = queue.asked(job_id)
            assert asked is not None
            if asked.need is Need.REVIEW:
                return
            assert queue.give(job_id, [Answer(choice=0) for _ in asked.questions]) == ""
            wait_until(lambda a=asked: queue.asked(job_id) is not a)  # type: ignore[misc]

    def review(self, queue: PrintQueue, job_id: str, review: Review | None = None) -> Asked:
        asked = self.need(queue, job_id, Need.REVIEW)
        assert queue.give(job_id, review or Review(approved=True)) == ""
        return asked

    def ready(self, queue: PrintQueue, job_id: str) -> None:
        self.answer(queue, job_id)
        self.review(queue, job_id)
        wait_until(lambda: self.waits(job_id) is Waits.READY)

    def waits(self, job_id: str) -> Waits | None:
        job = WaitingJobs(self.work / "jobs.sqlite").get(job_id)
        return job.waits if job is not None else None

    def offered(self) -> str:
        gate = self.gate()
        return str(gate.get("job_id", "")) if gate.get("pending") else ""

    def end_print(self, job_id: str) -> None:
        """The print ends, as the watcher records it: its outcome is still to be asked. The fake
        printer is free again."""
        self.printer._started = None
        self.ledger.record_ended(job_id, 9)


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[Shop]:
    found = Shop(tmp_path)
    yield found
    found.ledger.close()


@pytest.fixture
def running(shop: Shop) -> Iterator[tuple[Daemon, PrintQueue]]:
    with ExitStack() as stack:
        daemon = stack.enter_context(shop.daemon())
        queue = stack.enter_context(shop.queue(daemon))
        yield daemon, queue


def test_one_file_is_prepared_offered_and_started_by_the_person(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    added, refused = queue.add([Adding(shop.part("bracket"))])
    assert refused == []
    job_id = added[0]
    shop.ready(queue, job_id)
    wait_until(lambda: shop.offered() == job_id)
    assert shop.printer.verbs.count("start") == 0, "nothing starts by itself"
    assert daemon.start(job_id, bed_clear=True) == ""
    wait_until(lambda: shop.ledger.get(job_id) is not None)
    assert shop.printer.verbs[-2:] == ["upload", "start"]
    wait_until(lambda: any("Started: bracket.stl" in s for s in queue.said()))
    assert shop.waits(job_id) is None


# Helpers for a print that runs, and its outcome
# --------------------------------------------------------------------------


def start_offered(shop: Shop, daemon: Daemon, job_id: str) -> None:
    """The offered job started on the card, and seen to start."""
    wait_until(lambda: shop.offered() == job_id)
    shop.bridge.press(CFG, "approve")
    wait_until(lambda: shop.ledger.get(job_id) is not None)
    wait_until(lambda: daemon.active() is None)


def record_outcome(shop: Shop, daemon: Daemon, job_id: str, result: str, symptom: str) -> None:
    """The outcome answered on the card, which shows the oldest unanswered print first."""
    from print_shop.core.mqtt import (  # noqa: PLC0415 - only these tests press the card
        outcome_result_topic,
        outcome_submit_topic,
        outcome_symptom_topic,
        outcome_topic,
    )

    assert daemon._desk is not None
    daemon._desk.refresh()
    wait_until(lambda: shop.bridge.retained_json(outcome_topic(CFG)).get("job_id") == job_id)
    shop.bridge.deliver(outcome_result_topic(CFG), result)
    shop.bridge.deliver(outcome_symptom_topic(CFG), symptom)
    shop.bridge.deliver(outcome_submit_topic(CFG), "PRESS")
    wait_until(lambda: (row := shop.ledger.get(job_id)) is not None and row.result is not None)


def needs(queue: PrintQueue) -> dict[str, Need]:
    return {asked.job_id: asked.need for asked in queue.needs()}


def a_print_runs(shop: Shop, daemon: Daemon, queue: PrintQueue) -> str:
    """One job prepared, offered and started: it prints until `end_print`."""
    job_id = queue.add([Adding(shop.part("running"))])[0][0]
    shop.ready(queue, job_id)
    start_offered(shop, daemon, job_id)
    assert [r.job_id for r in shop.ledger.running()] == [job_id]
    return job_id


# Done when: five files, prepared while a print runs, answered in any order, started in order
# --------------------------------------------------------------------------


def test_five_files_added_in_one_go_reach_the_person_while_a_print_runs(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    printing = a_print_runs(shop, daemon, queue)
    added, refused = queue.add([Adding(shop.part(f"part{n}")) for n in range(1, 6)])
    assert refused == [] and len(added) == 5
    # Two places for the machine's work, and all five reach the person: one waiting for them
    # gives its place up.
    wait_until(lambda: set(needs(queue)) == set(added))
    assert set(needs(queue).values()) == {Need.QUESTIONS}
    assert [r.job_id for r in shop.ledger.running()] == [printing]
    assert shop.offered() == "", "nothing is offered while a print runs"

    # Answered in any order: the fourth, the second, the fifth, the first, the third.
    for at in (3, 1, 4, 0, 2):
        shop.answer(queue, added[at])
        shop.review(queue, added[at])
    wait_until(lambda: all(shop.waits(j) is Waits.READY for j in added))
    assert shop.offered() == "" and shop.printer.verbs.count("start") == 1

    # Started one by one in the order chosen.
    chosen = [added[2], added[0], added[4], added[1], added[3]]
    assert queue.order(chosen) == ""
    shop.end_print(printing)
    for job_id in chosen:
        start_offered(shop, daemon, job_id)
        shop.end_print(job_id)
    started = [name for verb, name in shop.printer.calls if verb == "start"]
    assert [job_id_in(name) for name in started] == [printing, *chosen]
    assert queue.jobs() == []


def job_id_in(name: str) -> str:
    from print_shop.slicer.naming import job_id_from_filename  # noqa: PLC0415

    found = job_id_from_filename(name)
    assert found is not None
    return found


def test_one_answer_is_given_to_several_files_at_once(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    added, _ = queue.add([Adding(shop.part(f"lid{n}")) for n in range(1, 4)])
    said: list[str] = []
    # print-shop's two, then the model's own when they come: each set given to the others.
    for _ in range(2):
        asked = shop.need(queue, added[0], Need.QUESTIONS)
        answers = [Answer(choice=0) for _ in asked.questions]
        assert queue.give(added[0], answers) == ""
        said += queue.give_alike(added[1:], asked.questions, answers)
        wait_until(lambda a=asked: queue.asked(added[0]) is not a)  # type: ignore[misc]
    assert "lid2.stl: the same 2 answers" in said
    # The others' questions are the same, so nothing more is asked of them: on to their review.
    for job_id in added[1:]:

        def reviewing(j: str = job_id) -> bool:
            return needs(queue).get(j) is Need.REVIEW

        wait_until(reviewing)
    kept = Kept(shop.work / added[2])
    assert kept._record.intake is not None
    assert [a.answer for a in kept._record.answers] == [Answer(choice=0)] * 3


# Done when: a quantity, and outcomes each against its own job
# --------------------------------------------------------------------------


def test_a_quantity_is_that_many_prints_each_with_its_own_row_and_outcome(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    added, _ = queue.add([Adding(shop.part("knob"), copies=3)])
    first = added[0]
    copies = [j for j in queue.jobs() if j.job_id != first]
    assert [(j.copied, j.waits) for j in copies] == [("2 of 3", Waits.WITH_FIRST),
                                                     ("3 of 3", Waits.WITH_FIRST)]
    shop.ready(queue, first)  # answered and reviewed once
    wait_until(lambda: all(shop.waits(j.job_id) is Waits.READY for j in copies))
    assert not any((shop.work / j.job_id / "proposed").exists() for j in copies), \
        "the copies print the first's slice: none is sliced again"
    ids = [first, *(j.job_id for j in copies)]
    for job_id in ids:
        start_offered(shop, daemon, job_id)
        shop.end_print(job_id)
    rows = [shop.ledger.get(j) for j in ids]
    assert all(r is not None for r in rows)
    assert len({r.gcode_name for r in rows if r}) == 3, "each is uploaded under its own name"
    for job_id, result, symptom in zip(ids, ("success", "failed", "success"),
                                       ("none", "adhesion", "stringing"), strict=True):
        record_outcome(shop, daemon, job_id, result, symptom)
    got = [(r.result, r.symptom) for r in (shop.ledger.get(j) for j in ids) if r]
    assert [str(g[0]) for g in got] == ["success", "failed", "success"]


def test_an_open_outcome_doesnt_block_the_next_start(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    first = a_print_runs(shop, daemon, queue)
    second = queue.add([Adding(shop.part("second"))])[0][0]
    shop.ready(queue, second)
    shop.end_print(first)  # ended, and its outcome not recorded
    start_offered(shop, daemon, second)
    shop.end_print(second)
    assert {r.job_id for r in shop.ledger.awaiting_answer()} == {first, second}
    assert any("outcome not yet recorded" in s for s in queue.printing())
    record_outcome(shop, daemon, first, "success", "none")
    record_outcome(shop, daemon, second, "failed", "spaghetti")
    one, two = shop.ledger.get(first), shop.ledger.get(second)
    assert one is not None and one.result is Result.SUCCESS
    assert two is not None and two.result is Result.FAILED


# Done when: nothing armed while it waits, nothing starts by itself
# --------------------------------------------------------------------------


def test_nothing_is_armed_while_it_waits(
    shop: Shop, running: tuple[Daemon, PrintQueue], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The jobs prepare with no printer at all, and a press with no gate open starts nothing."""
    daemon, queue = running
    built: list[dict[str, Any]] = []
    real = build_graph

    def spied(*args: Any, **kwargs: Any) -> Any:
        built.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(print_queue, "build_graph", spied)
    printing = a_print_runs(shop, daemon, queue)
    added, _ = queue.add([Adding(shop.part(f"wait{n}")) for n in range(1, 3)])
    for job_id in added:
        shop.ready(queue, job_id)
    assert built and all(kwargs.get("printer", "absent") is None for kwargs in built)
    verbs = list(shop.printer.verbs)
    for _ in range(3):
        shop.bridge.press(CFG, "approve")
    time.sleep(0.3)
    assert shop.printer.verbs == verbs and shop.offered() == ""
    assert [shop.waits(j) for j in added] == [Waits.READY, Waits.READY]
    assert [r.job_id for r in shop.ledger.running()] == [printing]


def test_nothing_starts_by_itself(shop: Shop, running: tuple[Daemon, PrintQueue]) -> None:
    """When a print ends the next is offered, and waits there for a Start."""
    daemon, queue = running
    printing = a_print_runs(shop, daemon, queue)
    nxt = queue.add([Adding(shop.part("next"))])[0][0]
    shop.ready(queue, nxt)
    shop.end_print(printing)
    wait_until(lambda: shop.offered() == nxt)
    time.sleep(0.5)
    assert shop.printer.verbs.count("start") == 1
    assert shop.waits(nxt) is Waits.READY and shop.ledger.get(nxt) is None
    assert any("Offered to print: next.stl" in s for s in queue.said())


# Done when: a spool on the shelf
# --------------------------------------------------------------------------


def test_a_job_for_a_spool_on_the_shelf_prepares_with_its_profile_and_start_waits_for_it(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    printing = a_print_runs(shop, daemon, queue)
    job_id = queue.add([Adding(shop.part("petg"), spool_id=GREY)])[0][0]
    job = WaitingJobs(shop.work / "jobs.sqlite").get(job_id)
    assert job is not None and job.profiles is not None and job.profiles.used is not None
    assert job.profiles.used.material is not None
    assert job.profiles.used.material.variety == "Elegoo PETG"
    assert "PETG" in job.profiles.used.filament.name
    line = next(li for li in queue.lines() if li.job.job_id == job_id)
    assert "grey" in line.spool and not line.loaded and "on the shelf" in line.said()
    shop.ready(queue, job_id)  # prepared while the other prints
    shop.end_print(printing)
    wait_until(lambda: shop.offered() == job_id)
    shop.bridge.press(CFG, "approve")
    wait_until(lambda: "Load spool #3" in str(shop.gate().get("held", "")))
    assert shop.ledger.get(job_id) is None and shop.offered() == job_id
    with Inventory(shop.ledger_path) as stock:
        stock.load(GREY)
    start_offered(shop, daemon, job_id)


# Done when: the queue is the same after a restart
# --------------------------------------------------------------------------


def test_the_queue_is_the_same_after_a_restart(shop: Shop) -> None:
    def seen(queue: PrintQueue) -> list[tuple[str, Waits, bool, int | None, str]]:
        return [(li.job.job_id, li.job.waits, li.job.held, li.job.spool_id, li.job.copied)
                for li in queue.lines()]

    with shop.daemon() as daemon, shop.queue(daemon) as queue:
        printing = a_print_runs(shop, daemon, queue)
        added, _ = queue.add([
            Adding(shop.part("ready")), Adding(shop.part("review")),
            Adding(shop.part("questions"), spool_id=BLACK), Adding(shop.part("held")),
            Adding(shop.part("pair"), copies=2)])
        ready, review, questions, held, pair = added
        shop.ready(queue, ready)
        shop.ready(queue, held)
        shop.answer(queue, review)
        shop.need(queue, review, Need.REVIEW)
        shop.need(queue, questions, Need.QUESTIONS)
        shop.need(queue, pair, Need.QUESTIONS)
        queue.hold(held)
        queue.order([questions, held, ready])
        before = seen(queue)
    assert [w for _, w, _, _, _ in before] == [
        Waits.QUESTIONS, Waits.READY, Waits.READY, Waits.REVIEW, Waits.QUESTIONS,
        Waits.WITH_FIRST]

    with shop.daemon() as daemon, shop.queue(daemon) as queue:
        assert seen(queue) == before
        # Each taken up where it was: the review and the questions are asked again, and the
        # review's answers aren't.
        shop.need(queue, review, Need.REVIEW)
        shop.need(queue, questions, Need.QUESTIONS)
        shop.need(queue, pair, Need.QUESTIONS)
        assert shop.offered() == "", "a print still runs"
        shop.end_print(printing)
        wait_until(lambda: shop.offered() == ready)  # the held one is passed over
        assert seen(queue) == before


# Done when: a waiting job moved to another colour, or material
# --------------------------------------------------------------------------

WALLS = Change(key="wall_loops", preset="2", value="4", reason="I handle it",
               source=Source.SPONSOR)
HOT = Change(key="nozzle_temperature", preset="220", value="205", reason="less stringing",
             source=Source.SPONSOR)
FAN = Change(key="fan_max_speed", preset="100", value="60", reason="it curled",
             source=Source.SPONSOR)


def reviewed_ready(shop: Shop, queue: PrintQueue, name: str) -> tuple[str, int]:
    """A job answered, corrected at its review (walls, a temperature and a fan), and ready.
    Returns it and how many questions it was asked."""
    job_id = queue.add([Adding(shop.part(name))])[0][0]
    asked = shop.need(queue, job_id, Need.QUESTIONS)
    shop.answer(queue, job_id)
    shop.review(queue, job_id, Review(corrections=(WALLS, HOT, FAN)))
    shop.review(queue, job_id)
    wait_until(lambda: shop.waits(job_id) is Waits.READY)
    return job_id, len(asked.questions)


def keys(asked: Asked) -> dict[str, str]:
    assert asked.review is not None
    return {c.key: c.value for c in asked.review.proposal.changes}


def test_a_waiting_job_moved_to_another_colour_moves_at_once_its_slice_and_review_standing(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    """The same variety and tuning, only the colour different: nothing is prepared again, and
    the job names the new spool, which its Start asks for (the Sponsor, 2026-11-24, "A")."""
    daemon, queue = running
    job_id, _ = reviewed_ready(shop, queue, "colour")
    wait_until(lambda: shop.offered() == job_id)
    gcode = shop.gate()["gcode_path"]
    sliced = len(shop.slicer.calls)
    said = queue.move(job_id, BLACK)
    assert said == ["colour.stl: moved to spool #2, Elegoo PLA, black: the same filament, so its "
                    "slice and review stand, and nothing is prepared again"]
    job = WaitingJobs(shop.work / "jobs.sqlite").get(job_id)
    assert job is not None and job.spool_id == BLACK and job.waits is Waits.READY
    assert job.profiles is not None and job.profiles.used is not None
    assert job.profiles.used.material is not None
    assert job.profiles.used.material.colour == "black"
    # Offered again, with the same slice, and nothing asked.
    wait_until(lambda: shop.offered() == job_id)
    assert shop.gate()["gcode_path"] == gcode and len(shop.slicer.calls) == sliced
    assert queue.asked(job_id) is None
    line = next(li for li in queue.lines() if li.job.job_id == job_id)
    assert "black" in line.spool and not line.loaded
    # Its Start asks for the black spool, and starts once it's loaded.
    shop.bridge.press(CFG, "approve")
    wait_until(lambda: "Load spool #2" in str(shop.gate().get("held", "")))
    with Inventory(shop.ledger_path) as stock:
        stock.load(BLACK)
    start_offered(shop, daemon, job_id)
    row = shop.ledger.get(job_id)
    assert row is not None and row.material is not None and row.material.colour == "black"
    kept = Kept(shop.work / job_id)
    assert kept._record.reviews[-1].approved, "its review stands"


def test_a_waiting_job_moved_to_another_material_comes_back_to_the_review_with_its_answers(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    job_id, _ = reviewed_ready(shop, queue, "material")
    said = queue.move(job_id, GREY)
    assert isinstance(said, list), said
    asked = shop.need(queue, job_id, Need.REVIEW)
    assert queue.asked(job_id) is asked, "its answers are kept: no question is asked again"
    assert keys(asked).get("wall_loops") == "4", "its own change is kept"
    assert asked.review is not None and asked.review.material is not None
    assert asked.review.material.variety == "Elegoo PETG"
    assert asked.review.profiles.used is not None
    assert "PETG" in asked.review.profiles.used.filament.name
    shop.review(queue, job_id)
    wait_until(lambda: shop.waits(job_id) is Waits.READY)


def test_moved_to_another_variety_a_temperature_or_fan_comes_back_unticked(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    from print_shop.setup.offer import render  # noqa: PLC0415

    _, queue = running
    job_id, _ = reviewed_ready(shop, queue, "unticked")
    said = queue.move(job_id, GREY)
    assert isinstance(said, list)
    assert any("offered back unticked: nozzle_temperature=205, fan_max_speed=60" in s
               for s in said), said
    asked = shop.need(queue, job_id, Need.REVIEW)
    assert "nozzle_temperature" not in keys(asked) or keys(asked)["nozzle_temperature"] != "205"
    assert "fan_max_speed" not in keys(asked) or keys(asked)["fan_max_speed"] != "60"
    review = asked.review
    assert review is not None and review.offering is not None
    offer = review.offering
    moved = {i.key: i for i in offer.moved}
    assert set(moved) == {"nozzle_temperature", "fan_max_speed"}
    pose = "0,0,0"
    for item in moved.values():
        standing = offer.standing(item, review.proposal, pose)
        assert standing.mark == " ", standing
        assert "you set it for Elegoo PLA, and this job is now for Elegoo PETG" in standing.why
    shown = "\n".join(render(offer, review.proposal, pose))
    assert "YOURS, SET FOR THE SPOOL IT WAS MOVED FROM" in shown
    assert "[ ]" in shown and "nozzle_temperature" in shown
    # Used, it's theirs again; moved back, it's given back ticked as a change.
    n = next(n for n, i in enumerate(offer.items(), 1) if i.key == "nozzle_temperature")
    assert offer.use([n], review.proposal, pose) == [moved["nozzle_temperature"]]
    assert queue.give(job_id, Review(approved=True)) == ""
    wait_until(lambda: shop.waits(job_id) is Waits.READY)
    back = queue.move(job_id, WHITE)
    assert isinstance(back, list)
    assert any("given back, set for Elegoo PLA" in s for s in back), back
    again = shop.need(queue, job_id, Need.REVIEW)
    assert keys(again).get("nozzle_temperature") == "205"
    assert keys(again).get("fan_max_speed") == "60"


# The queue changed: hold, remove, together, a copy's first removed
# --------------------------------------------------------------------------


def test_held_and_removed_jobs_are_put_back_from_the_card(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    one, two = queue.add([Adding(shop.part("one")), Adding(shop.part("two"))])[0]
    shop.ready(queue, one)
    shop.ready(queue, two)
    wait_until(lambda: shop.offered() == one)
    assert queue.hold(one) == ""
    wait_until(lambda: shop.offered() == two)
    assert shop.waits(one) is Waits.READY
    assert queue.remove(two) == ""
    wait_until(lambda: shop.offered() == "")
    assert shop.waits(two) is None
    assert queue.hold(one, False) == ""
    wait_until(lambda: shop.offered() == one)
    assert shop.printer.verbs.count("start") == 0


def test_jobs_for_one_spool_are_taken_together(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    added, _ = queue.add([Adding(shop.part("a"), spool_id=BLACK), Adding(shop.part("b")),
                          Adding(shop.part("c"), spool_id=BLACK)])
    assert queue.together(BLACK) == ""
    assert [j.job_id for j in queue.jobs()] == [added[0], added[2], added[1]]


def test_a_copys_first_removed_hands_its_answers_to_the_next(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    first = queue.add([Adding(shop.part("hinge"), copies=2)])[0][0]
    shop.answer(queue, first)
    shop.need(queue, first, Need.REVIEW)
    heir = next(j.job_id for j in queue.jobs() if j.job_id != first)
    assert queue.remove(first) == ""
    # Its answers came with it: straight to the review.
    asked = shop.need(queue, heir, Need.REVIEW)
    assert asked is queue.asked(heir)
    job = WaitingJobs(shop.work / "jobs.sqlite").get(heir)
    assert job is not None and job.copy_of == "" and (shop.work / heir / KEPT_NAME).is_file()


def test_a_job_stopped_at_its_review_ends_and_says_so(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    job_id = queue.add([Adding(shop.part("stopped"))])[0][0]
    shop.answer(queue, job_id)
    shop.review(queue, job_id, Review(stop=True))
    wait_until(lambda: shop.waits(job_id) is None)
    assert any("Ended without printing: stopped.stl" in s for s in queue.said())


def test_a_file_that_isnt_there_is_refused_in_words(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    added, refused = queue.add([Adding(shop.tmp / "nope.stl"),
                                Adding(shop.part("fine"), spool_id=99)])
    assert added == []
    assert refused[0] == "nope.stl: not a file"
    assert "no spool #99" in refused[1]



# The terminal: one screen that drives the queue
# --------------------------------------------------------------------------


class Typist:
    """Types at each prompt: at `queue>` the next command, once what it needs is there; 1 at a
    question; `ok` at the review; and `y` at the bed's question."""

    def __init__(self, queue: PrintQueue, *commands: tuple[str, Callable[[], bool]]) -> None:
        self.queue = queue
        self.commands = list(commands)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        assert len(self.prompts) < 200, f"it keeps asking: {self.prompts[-5:]}"
        if prompt == "queue> ":
            if not self.commands:
                return "quit"
            typed, when = self.commands.pop(0)
            wait_until(when)
            return typed
        if prompt == "bed clear> ":
            return "y"
        if prompt == "> ":
            return "ok"
        return "1"  # a question's first option


def test_the_terminal_answers_reviews_and_starts_jobs_in_any_order(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    """At `queue>`: answers given alike, a Start that names its job and spool, and what comes of
    it said before the prompt is back."""
    from print_shop.queue_terminal import serve  # noqa: PLC0415

    daemon, queue = running
    one, two = (shop.part("first"), shop.part("second"))

    def asks(n: int, need: Need) -> Callable[[], bool]:
        return lambda: len(queue.jobs()) >= n and needs(queue).get(queue.jobs()[n - 1].job_id) \
            is need

    screen: list[str] = []

    class Shown(Typist):
        def __call__(self, prompt: str) -> str:
            typed = super().__call__(prompt)
            screen.append(f"{prompt}{typed}")
            return typed

    typist = Shown(
        queue,
        (f"add {one} {two} x2", lambda: True),
        # The second's questions, given to the first alike, the model's too as they come; then
        # the second's review, which it goes on to.
        ("answer 2 1", asks(2, Need.QUESTIONS)),
        ("review 1", asks(1, Need.REVIEW)),
        ("start 3", lambda: all(j.waits is Waits.READY for j in queue.jobs())),
    )
    assert serve(queue, daemon, read=typist, write=screen.append, forget=lambda: None) == 0
    shown = "\n".join(screen)
    assert "Added 2." in shown
    assert "first.stl: the same 2 answers" in shown
    assert "first.stl: the same answer" in shown
    third = next(j for j in WaitingJobs(shop.work / "jobs.sqlite").all() if j.copied == "1 of 2")
    started = [job_id_in(n) for v, n in shop.printer.calls if v == "start"]
    assert len(started) == 1 and started[0] != third.job_id
    # The bed's question names the job and its spool, and what came of the Start is said before
    # the prompt is back.
    asked_at = next(n for n, line in enumerate(screen) if "I can see the bed is clear" in line)
    assert screen[asked_at].startswith(f"  Start second.stl (2 of 2) (job {started[0][:8]}) on "
                                       "spool #1, Elegoo PLA, white? I can see the bed is clear")
    after = screen[asked_at:]
    prompt_back = next(n for n, line in enumerate(after) if line.startswith("queue> "))
    assert any(line.startswith("  * Started: second.stl") for line in after[:prompt_back])
    assert [j.part for j in queue.jobs()] == ["first.stl", "second.stl"]
    assert [j.copied for j in queue.jobs()] == ["", "1 of 2"]


def test_each_queued_job_keeps_its_own_log(shop: Shop, tmp_path: Path) -> None:
    """What a job's preparing logs, what the queue says of it, and its screens and answers at the
    terminal, in its own `job.log`, as a lone job's are."""
    import logging  # noqa: PLC0415

    from print_shop.joblog import LOG_NAME, JobLogs  # noqa: PLC0415
    from print_shop.queue_terminal import serve  # noqa: PLC0415

    logs = JobLogs(["a-made-up-secret-value"], shop.work)
    logging.getLogger().setLevel(logging.INFO)
    with shop.daemon() as daemon, logs.routing():
        queue = shop.queue(daemon)
        queue._logs = logs
        with queue:
            one, two = queue.add([Adding(shop.part("logged")), Adding(shop.part("other"))])[0]
            typist = Typist(queue, ("answer 1", lambda: needs(queue).get(one) is Need.QUESTIONS))
            assert serve(queue, daemon, read=typist, write=lambda _: None,
                         forget=lambda: None) == 0

            def ready_by_now() -> bool:
                # The terminal may have followed it through its review already: else it's done here.
                asked = queue.asked(one)
                if asked is not None and asked.need is Need.QUESTIONS:
                    queue.give(one, [Answer(choice=0)] * len(asked.questions))
                elif asked is not None and asked.need is Need.REVIEW:
                    queue.give(one, Review(approved=True))
                return shop.waits(one) is Waits.READY

            wait_until(ready_by_now)
    log = (shop.work / one / LOG_NAME).read_text()
    assert "What is this part for?" in log, "its screen at the terminal"
    assert "1> 1" in log, "what was typed"
    assert "queue: Ready: logged.stl" in log
    assert "INFO" in log, "what its preparing logged"
    assert "other.stl" not in log, "only its own"


def test_go_goes_on_without_the_models_questions(shop: Shop) -> None:
    """print-shop's two are asked at once; while the model still reads, the job's line says so,
    and `go` goes on without its questions (mqucifer/print-shop#208)."""
    held = threading.Event()

    def slow_questions(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        held.wait(WAIT_S)
        return _top_question(system, user, schema)

    with shop.daemon() as daemon:
        queue = shop.queue(daemon)
        queue._preparing = Preparing(
            slicer=shop.slicer, proposer=Proposer(ask=_model, ask_questions=slow_questions),
            store=Store(shop.store))
        with queue:
            job_id = queue.add([Adding(shop.part("slow"))])[0][0]
            asked = shop.need(queue, job_id, Need.QUESTIONS)
            assert len(asked.questions) == 2, "print-shop's two, at once"
            queue.give(job_id, [Answer(choice=0)] * 2)
            wait_until(lambda: "still reading" in queue.lines()[0].step)
            assert "`go 1` goes on without them" in queue.lines()[0].step
            assert queue.go(job_id) == ""
            review = shop.need(queue, job_id, Need.REVIEW)
            assert review.review is not None
            held.set()
    assert any("went on without the model's questions" in n
               for n in review.review.proposal.notes), review.review.proposal.notes


def test_a_slice_past_its_limit_stops_saying_so_and_is_retried(shop: Shop) -> None:
    """The machine's steps keep their limit: a slice that runs past it stops the job, saying so in
    its line, and `retry` prepares it again (mqucifer/print-shop#209's limits)."""
    slow = threading.Event()
    slow.set()
    real = shop.slicer

    def slicer(stl_path: Path | str, profiles: SlicerProfiles,
               outdir: Path | str) -> SliceResult:
        if slow.is_set():
            time.sleep(1.5)
        return real(stl_path, profiles, outdir)

    with shop.daemon() as daemon:
        queue = shop.queue(daemon)
        queue._slice_limit_s = 0.5
        queue._preparing = Preparing(slicer=slicer,
                                     proposer=Proposer(ask=_model, ask_questions=_top_question),
                                     store=Store(shop.store))
        with queue:
            job_id = queue.add([Adding(shop.part("hung"))])[0][0]
            for _ in range(2):  # print-shop's two, then the model's: then it slices
                asked = shop.need(queue, job_id, Need.QUESTIONS)
                queue.give(job_id, [Answer(choice=0)] * len(asked.questions))
                wait_until(lambda a=asked: queue.asked(job_id) is not a)  # type: ignore[misc]
            wait_until(lambda: shop.waits(job_id) is Waits.STOPPED)
            line = queue.lines()[0]
            assert line.step == ("STOPPED: its slice took over 0 s: `retry 1` prepares it again, "
                                 "with what you gave it, or `remove 1`")
            assert any(s.startswith("Stopped: hung.stl") for s in queue.said())
            slow.clear()
            queue._slice_limit_s = 3600.0
            assert queue.retry(job_id) == ""
            shop.ready(queue, job_id)
    assert queue.retry(job_id) == "it hasn't stopped"


def test_a_job_moved_in_colour_while_at_its_review_names_the_new_spool_once_ready(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    job_id = queue.add([Adding(shop.part("midway"))])[0][0]
    shop.answer(queue, job_id)
    review = shop.need(queue, job_id, Need.REVIEW)
    said = queue.move(job_id, BLACK)
    assert isinstance(said, list) and "nothing is prepared again" in said[0]
    assert queue.asked(job_id) is review, "its review stands"
    shop.review(queue, job_id)
    wait_until(lambda: shop.waits(job_id) is Waits.READY)
    wait_until(lambda: shop.offered() == job_id)
    shop.bridge.press(CFG, "approve")
    wait_until(lambda: "Load spool #2" in str(shop.gate().get("held", "")))


def test_print_job_queue_adds_files_and_takes_the_queue_up_again(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """`print_job.py --queue` on fakes: two files added, then quit; run again, the same queue."""
    import builtins  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from tests.test_waits_for_you import _script  # noqa: PLC0415

    script = _script()
    made_up = shop.tmp / "made-up.env"
    made_up.write_text(f"PRINT_SHOP_LEDGER={shop.ledger_path}\n")
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    monkeypatch.setattr(script, "WORK_DIR", shop.work)

    def build_run(**kwargs: Any) -> SimpleNamespace:
        assert kwargs["profiles"] is None and kwargs["printer"] is None
        return SimpleNamespace(daemon=shop.daemon(), ledger=Ledger(shop.ledger_path))

    def build_queue(run: Any, **kwargs: Any) -> PrintQueue:
        queue = shop.queue(run.daemon)
        queue._profiles_for = kwargs["profiles_for"]
        return queue

    monkeypatch.setattr(script, "build_run", build_run)
    monkeypatch.setattr(script, "build_queue", build_queue)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "quit")
    parts = [shop.part("lid"), shop.part("box")]
    assert script.main(["--queue", str(parts[0]), "x2", str(parts[1]), "spool=2",
                        "--settings", str(made_up)]) == 0
    out = capsys.readouterr().out
    assert "Added 2 to the queue." in out
    assert "Not armed" in out
    jobs = WaitingJobs(shop.work / "jobs.sqlite").all()
    assert [(j.part, j.copied, j.spool_id) for j in jobs] == [
        ("lid.stl", "1 of 2", WHITE), ("lid.stl", "2 of 2", WHITE), ("box.stl", "", BLACK)]
    assert script.main(["--queue", "--settings", str(made_up)]) == 0
    out = capsys.readouterr().out
    assert "lid.stl (1 of 2)" in out and "box.stl" in out and "lid.stl (2 of 2)" in out
    assert script.main(["--queue", "x2", "--settings", str(made_up)]) == 2
    assert "x2 goes after the file it's for" in capsys.readouterr().err


# A file sliced by hand, in the queue
# --------------------------------------------------------------------------


def test_a_hand_sliced_file_queues_with_copies_and_moves_only_in_colour(
    shop: Shop, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing asks the person for it: it's ready once it's taken, and its copies with it. It
    can move to another colour of its filament, never to another filament."""
    from print_shop import sliced  # noqa: PLC0415
    from tests.sliced_fixtures import write_gcode  # noqa: PLC0415

    with shop.daemon() as daemon:
        queue = shop.queue(daemon)
        queue._sliced_for = lambda path, material: sliced.SlicedJob.of(
            sliced.read(path), material=material)
        with queue:
            gcode = write_gcode(shop.tmp / "sliced")
            first = queue.add([Adding(gcode, copies=2)])[0][0]
            wait_until(lambda: all(j.waits is Waits.READY for j in queue.jobs()))
            assert queue.needs() == []
            copy = next(j for j in queue.jobs() if j.job_id != first)
            assert copy.copied == "2 of 2"
            assert any(p.name.endswith(f"_{copy.job_id}.gcode")
                       for p in (shop.work / copy.job_id).iterdir())
            assert queue.move(first, GREY) == (
                "a file sliced by hand prints with the filament it was sliced for: slice it "
                "again in Orca for the other, and add that")
            moved = queue.move(first, BLACK)
            assert isinstance(moved, list), moved
            wait_until(lambda: shop.waits(first) is Waits.READY
                       and (j := queue.find(first[:8])) is not None and j.spool_id == BLACK)
            for job_id in (copy.job_id, first):
                if job_id == first:
                    with Inventory(shop.ledger_path) as stock:
                        stock.load(BLACK)
                queue.first(job_id)
                start_offered(shop, daemon, job_id)
                shop.end_print(job_id)
    rows = [shop.ledger.get(j) for j in (first, copy.job_id)]
    assert all(r is not None for r in rows)



_LIVE = """
import tempfile, threading, time
from pathlib import Path

from tests.test_queue import Adding, Shop
from print_shop.queue_terminal import serve

shop = Shop(Path(tempfile.mkdtemp()))


def read(prompt):
    typed = input(prompt)
    print("TYPED", repr(typed), flush=True)
    return typed


with shop.daemon() as daemon, shop.queue(daemon) as queue:
    def later():
        time.sleep(1.0)  # once the prompt is open, with nothing typed
        queue.add([Adding(shop.part("bracket"))])

    threading.Thread(target=later, daemon=True).start()
    serve(queue, daemon, read=read, write=lambda s: print(s, flush=True), forget=lambda: None)
    print("DONE", flush=True)
"""


def test_on_a_terminal_what_needs_the_person_shows_while_queue_is_open_and_typing_is_kept() -> None:
    """print-shop-ux, on a real pty: with `queue>` open and nothing sent, a job that comes to need
    the person is said in the line above the prompt within seconds, and what's half typed is
    kept. On a real terminal, with input() itself."""
    import os  # noqa: PLC0415
    import select  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    ours, theirs = os.openpty()
    root = Path(__file__).parents[1]
    child = subprocess.Popen(
        [sys.executable, "-c", _LIVE], stdin=theirs, stdout=theirs, stderr=theirs, cwd=root,
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root}"},
    )
    os.close(theirs)
    out = b""

    def until(word: bytes, after: int = 0) -> int:
        nonlocal out
        end = time.monotonic() + WAIT_S
        while word not in out[after:] and time.monotonic() < end:
            if not select.select([ours], [], [], 0.2)[0]:
                continue
            try:
                got = os.read(ours, 65536)
            except OSError:
                break
            if not got:
                break
            out += got
        assert word in out[after:], out.decode(errors="replace")[-2000:]
        return out.index(word, after)

    try:
        opened = until(b"queue> ")
        os.write(ours, b"lis")  # half typed, and not sent
        # Said above the prompt, in place, with nothing sent.
        live = until(b"Needs you: answer 1 (bracket.stl, the questions)", opened)
        assert b"\x1b7\x1b[1A\r\x1b[2K" in out[opened:live], "rewritten in place"
        assert b"TYPED" not in out
        os.write(ours, b"t\n")
        until(b"TYPED 'list'")  # what was typed before it was drawn is kept
        os.write(ours, b"quit\n")
        until(b"DONE")
    finally:
        child.kill()
        child.wait(timeout=10)
        os.close(ours)
