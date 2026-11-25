"""Moving a job to another spool asks whether that spool is on the printer now, and the queue can
load (mqucifer/print-shop#305).

All on `test_queue`'s made-up shop: white, black and grey PLA/PETG, white loaded on a made-up
printer, a fake printer and broker. Never the Sponsor's stock: their queue is live.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from print_shop.inventory import Inventory, SpoolUse
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.print_queue import Adding, PrintQueue
from print_shop.orchestrator.spools import load_said
from print_shop.queue_terminal import serve
from tests.test_ledger import record
from tests.test_queue import BLACK, GREY, WHITE, Shop, wait_until


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


def loaded(shop: Shop) -> int | None:
    with Inventory(shop.ledger_path) as stock:
        spool = stock.loaded("cc1")
    return spool.id if spool is not None else None


def not_known(shop: Shop) -> bool:
    with Inventory(shop.ledger_path) as stock:
        return stock.unknown("cc1") is not None


class At:
    """The person at `queue>`: each command in turn, and at each other prompt what's given for
    it, or the step's own answer (1 at a question, ok at the review, y at the bed)."""

    def __init__(self, *commands: str, answers: dict[str, list[str]] | None = None,
                 before: dict[str, Callable[[], None]] | None = None) -> None:
        self.commands = list(commands)
        self.answers = answers or {}
        self.before = before or {}
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        assert len(self.prompts) < 100, self.prompts[-5:]
        if prompt in self.before:
            self.before.pop(prompt)()
        if prompt in self.answers and self.answers[prompt]:
            return self.answers[prompt].pop(0)
        if prompt == "queue> ":
            return self.commands.pop(0) if self.commands else "quit"
        return {"bed clear> ": "y", "> ": "ok"}.get(prompt, "1")


def ready_job(shop: Shop, queue: PrintQueue, name: str = "bracket") -> str:
    job_id = queue.add([Adding(shop.part(name))])[0][0]
    shop.ready(queue, job_id)
    return job_id


def at_queue(queue: PrintQueue, daemon: Daemon, person: At, *, phone: bool = False) -> str:
    said: list[str] = []
    serve(queue, daemon, read=person, write=said.append, forget=lambda: None,
          screen=lambda: None, phone=phone)
    return "\n".join(said)


# --------------------------------------------------------------------------
# Done when: spool N S, then y, records S loaded, says what came off, and Start isn't refused
# --------------------------------------------------------------------------


def test_a_move_then_y_records_it_loaded_and_the_job_starts(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    ready_job(shop, queue)
    said = at_queue(queue, daemon, At("spool 1 2", "start 1", answers={"loaded> ": ["y"]}),
                    phone=True)
    assert ("Is spool #2, Elegoo PLA, black, on the printer now? y records it loaded "
            "(#1, Elegoo PLA, white, comes off). Enter leaves the stock as it is.") in said
    assert "Recorded: spool #2, Elegoo PLA, black is loaded on cc1; #1, Elegoo PLA, white, " \
           "came off." in said
    assert loaded(shop) == BLACK
    assert "Job 1 is ready to start: `start 1`, or Start on your phone." in said
    wait_until(lambda: any(s.startswith("Started:") for s in queue.said()))
    assert "Not started" not in said, "Start isn't refused for its spool"


def test_a_move_then_enter_leaves_the_stock_and_start_names_load(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    """Done when: Enter leaves the stock as it was, Start is refused as now, and the refusal
    names the queue's own load."""
    daemon, queue = running
    job_id = ready_job(shop, queue)
    said = at_queue(queue, daemon, At("spool 1 2", answers={"loaded> ": [""]}))
    assert "Nothing recorded: the stock is as it was." in said and loaded(shop) == WHITE
    wait_until(lambda: shop.offered() == job_id)
    assert daemon.start(job_id, bed_clear=True) == ""
    wait_until(lambda: any(s.startswith("Not started") for s in queue.said()))
    told = at_queue(queue, daemon, At())
    assert "`stock.py load 2`" in told and "Here, type `load 2`." in told
    assert loaded(shop) == WHITE


# --------------------------------------------------------------------------
# Done when: after a swap print, both halves, nothing pre-selected, and a stale answer refused
# --------------------------------------------------------------------------


def swap_sent(shop: Shop) -> None:
    """A made-up white-to-black print, sent, started and ended: what's on isn't known after."""
    with Inventory(shop.ledger_path) as stock:
        first = stock.material()
        to = first.of(stock.spool(BLACK))
    shop.ledger.record_start_requested(record("swap").model_copy(update={
        "material": first, "spools": (SpoolUse(material=first, from_layer=1),
                                      SpoolUse(material=to, from_layer=11, from_z=2.0))}))
    shop.ledger.record_started("swap")
    shop.ledger.record_ended("swap", 9)


def test_after_a_swap_print_the_move_asks_both_halves(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    ready_job(shop, queue)
    swap_sent(shop)
    said = at_queue(queue, daemon, At("spool 1 3", answers={"loaded> ": [""]}))
    assert "and is spool #3, Elegoo PETG, grey, on the printer now? y says both. Enter leaves " \
           "the stock as it is." in said
    assert not_known(shop), "nothing pre-selected: Enter says nothing of either half"
    said = at_queue(queue, daemon, At("spool 1 2", answers={"loaded> ": ["y"]}))
    assert "Recorded: spool #2, Elegoo PLA, black is loaded on cc1" in said
    assert loaded(shop) == BLACK and not not_known(shop), "y says both"


def test_a_stale_answer_to_the_move_is_refused(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    ready_job(shop, queue)

    def loaded_elsewhere() -> None:
        with Inventory(shop.ledger_path) as stock:
            stock.load(GREY)  # the phone, or stock.py, said another meanwhile

    said = at_queue(queue, daemon, At("spool 1 2", answers={"loaded> ": ["y"]},
                                      before={"loaded> ": loaded_elsewhere}))
    assert "Not recorded:" in said and "changed" in said
    assert loaded(shop) == GREY, "the word since stands"


def test_nothing_is_asked_or_recorded_while_a_print_runs(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    ready_job(shop, queue)
    shop.ledger.record_start_requested(record("printing"))
    shop.ledger.record_started("printing")
    person = At("spool 1 2")
    said = at_queue(queue, daemon, person)
    assert "A print is running now: once it's off and #2 is on, type `load 2`." in said
    assert "loaded> " not in person.prompts and loaded(shop) == WHITE


# --------------------------------------------------------------------------
# Done when: `load S` does what `stock.py load S` does
# --------------------------------------------------------------------------


def test_load_at_the_queue_is_the_stocks_own(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    said = at_queue(queue, daemon, At("load 2"))
    assert loaded(shop) == BLACK
    with Inventory(shop.ledger_path) as stock:
        line = stock.spool(BLACK).line()
    assert line in said, "the same line stock.py load says"


def test_load_after_a_swap_asks_the_stocks_question(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    swap_sent(shop)
    said = at_queue(queue, daemon, At("load 2", answers={"over> ": ["y"]}))
    assert "y says both. Anything else records the load alone, and Start still holds." in said
    assert loaded(shop) == BLACK and not not_known(shop)


def test_stock_py_and_the_queue_load_by_one_path(shop: Shop) -> None:
    said: list[str] = []
    with Inventory(shop.ledger_path) as stock:
        lines = load_said(stock, GREY, read=lambda p: "", write=said.append,
                          forget=lambda: None, asks=False)
    assert lines and "spool #3" in lines[0] and loaded(shop) == GREY


def test_load_without_a_number_says_how(shop: Shop, running: tuple[Daemon, PrintQueue]) -> None:
    daemon, queue = running
    assert "`load S` records spool S" in at_queue(queue, daemon, At("load"))


# --------------------------------------------------------------------------
# Done when: the phone's "I loaded it" after a move records the load through the same path
# --------------------------------------------------------------------------


def test_the_phones_i_loaded_it_after_a_move_is_the_stocks_load(
    shop: Shop, running: tuple[Daemon, PrintQueue], tmp_path: Path,
) -> None:
    import re  # noqa: PLC0415

    from print_shop.phone.phones import Phones  # noqa: PLC0415
    from print_shop.phone.server import handler_for  # noqa: PLC0415
    from print_shop.phone.shop import Shop as PhoneShop  # noqa: PLC0415
    from tests.test_phone_start import FakeStarts, home, signed_in  # noqa: PLC0415

    daemon, queue = running
    job_id = ready_job(shop, queue)
    phones = Phones(tmp_path / "phones.sqlite")
    page = PhoneShop(shop.ledger_path, queue=queue, daemon=daemon)
    phone = signed_in(handler_for(home=home(), phones=phones, starts=FakeStarts(), shop=page),
                      phones)
    moved = phone.post(f"/job/{job_id}/spool", {"spool": str(BLACK)})
    assert moved.status == 303
    shown = phone.get(moved.header("Location"))
    assert "I loaded it" in shown.body
    form = re.search(r'<form method="post" action="/filaments/load">.*?</form>', shown.raw)
    assert form is not None and f'name="spool" value="{BLACK}"' in form.group(0)
    changes = re.search(r'name="changes" value="(\d+)"', form.group(0))
    assert changes is not None
    done = phone.post("/filaments/load", {"spool": str(BLACK), "changes": changes.group(1)})
    assert done.status == 303 and loaded(shop) == BLACK
    again = phone.get(f"/job/{job_id}?said=moved")
    assert "I loaded it" not in again.body, "loaded already: nothing to say"


def test_the_phone_offers_no_load_while_a_print_runs(
    shop: Shop, running: tuple[Daemon, PrintQueue], tmp_path: Path,
) -> None:
    from print_shop.phone.phones import Phones  # noqa: PLC0415
    from print_shop.phone.server import handler_for  # noqa: PLC0415
    from print_shop.phone.shop import Shop as PhoneShop  # noqa: PLC0415
    from tests.test_phone_start import FakeStarts, home, signed_in  # noqa: PLC0415

    daemon, queue = running
    job_id = ready_job(shop, queue)
    shop.ledger.record_start_requested(record("printing"))
    shop.ledger.record_started("printing")
    phones = Phones(tmp_path / "phones.sqlite")
    page = PhoneShop(shop.ledger_path, queue=queue, daemon=daemon)
    phone = signed_in(handler_for(home=home(), phones=phones, starts=FakeStarts(), shop=page),
                      phones)
    moved = phone.post(f"/job/{job_id}/spool", {"spool": str(BLACK)})
    assert "I loaded it" not in phone.get(moved.header("Location")).body


# --------------------------------------------------------------------------
# Done when: a y typed before the question is drawn answers nothing, on a pty
# --------------------------------------------------------------------------

_CHILD = '''
import sys
from pathlib import Path

from print_shop.inventory import Inventory
from print_shop.queue_terminal import serve

folder = Path(sys.argv[1])
path = folder / "ledger.sqlite"
with Inventory(path) as stock:
    stock.add_printer("cc1", "Made-up printer")
    stock.fit(0.4)
    for colour in ("white", "black"):
        stock.add_spool(manufacturer="Elegoo", type="PLA", variety="Elegoo PLA", colour=colour)
    stock.load(1)


class Job:
    job_id = "job-0001-made-up"


class Queue:
    """The queue's reads and its move, over one made-up job and a made-up stock."""

    stock_path = path

    def said(self, since=0):
        return []

    def printing(self):
        return []

    def lines(self):
        return []

    def sent(self):
        return []

    def find(self, named):
        return Job()

    def move(self, job_id, spool_id):
        return [f"moved to spool #{spool_id}: another colour, so at once"]


class Daemon:
    def pending_gate(self):
        return None


serve(Queue(), Daemon(), read=input)
with Inventory(path) as stock:
    print("LOADED", stock.loaded("cc1").id, flush=True)
'''


def test_a_y_typed_before_the_question_answers_nothing_on_a_pty(tmp_path: Path) -> None:
    """print-shop-ux too: the live line above `queue>` neither eats the y typed at the question
    nor shows it twice."""
    import os  # noqa: PLC0415
    import pty  # noqa: PLC0415
    import select  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import time  # noqa: PLC0415

    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    master, other = pty.openpty()
    child = subprocess.Popen([sys.executable, str(script), str(tmp_path)], stdin=other,
                             stdout=other, stderr=other, start_new_session=True)
    os.close(other)
    seen = ""

    def until(text: str, after: int = 0) -> int:
        nonlocal seen
        end = time.monotonic() + 30
        while text not in seen[after:]:
            assert time.monotonic() < end, f"{text!r} never showed in {seen[after:]!r}"
            if select.select([master], [], [], 0.2)[0]:
                seen += os.read(master, 65536).decode(errors="replace")
        return seen.index(text, after) + len(text)

    try:
        until("queue> ")
        os.write(master, b"spool 1 2\ny\n")  # the y typed ahead, before the question's drawn
        at = until("loaded> ")
        time.sleep(1.0)  # the live line's second redraw would have come by now
        while select.select([master], [], [], 0.2)[0]:  # all it has said meanwhile
            seen += os.read(master, 65536).decode(errors="replace")
        assert "Recorded" not in seen and "Nothing recorded" not in seen, \
            "the y typed before it was drawn answered nothing"
        os.write(master, b"y\n")
        until("Recorded: spool #2, Elegoo PLA, black is loaded on cc1", at)
        until("queue> ", at)
        os.write(master, b"quit\n")
        until("LOADED 2")
        shown = seen[at:].replace("\r", "")
        assert shown.count("Recorded:") == 1
        assert shown.lstrip().startswith("y\n"), "the y typed at the question, shown once"
    finally:
        child.kill()
        child.wait(30)
        os.close(master)
