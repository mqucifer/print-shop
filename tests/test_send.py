"""Files are sent to the queue with one command, by Claude or the person (mqucifer/print-shop#226).

The shop is `test_queue.py`'s, made up: three spools (white and black Elegoo PLA, grey Elegoo
PETG, white loaded), a fake printer and broker, the recorded slice and a scripted model. "Another
repository" is a folder of this test's own, and a restart is a second queue on the same jobs
folder, the first one put by. Nothing here reaches a printer, a broker, Orca or a model.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest

from print_shop.ledger import Answer as Outcome
from print_shop.ledger import PrintRecord, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.kept import Kept, WaitingJobs, Waits
from print_shop.orchestrator.print_queue import Need, PrintQueue
from print_shop.orchestrator.sending import Adding, Sending, loaded_from, send
from print_shop.queue_terminal import parse_adding, serve
from print_shop.runtime import job_profiles, sliced_reader
from print_shop.setup.context import KEPT, LOADS, read_context
from print_shop.setup.offer import render
from print_shop.setup.review import Review, _used_change
from print_shop.slicer.orca import BedType, SlicerProfiles, parse_config_block
from tests.sliced_fixtures import OBJECTS, project_file, write_gcode
from tests.test_queue import BLACK, GREY, WHITE, Shop, wait_until

REPO = Path(__file__).parents[1]
BY = "Claude, in a made-up cad session"


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[Shop]:
    found = Shop(tmp_path)
    yield found
    found.ledger.close()


def sent(shop: Shop, *adding: Adding, by: str = BY, note: str = "") -> Sending:
    """`adding` sent as `scripts/send.py` sends them, to the shop's jobs folder."""
    def profiles(material: Any, into: Path) -> SlicerProfiles:
        return job_profiles(material, into, bed=BedType.TEXTURED_PEI, store_root=shop.store)

    return send(list(adding), by=by, note=note, workdir=shop.work, ledger_path=shop.ledger_path,
                profiles_for=profiles,
                sliced_for=sliced_reader(shop.tmp / "no-settings.env", shop.store))


def elsewhere(shop: Shop) -> dict[str, Path]:
    """Three files in another repository: a part with its notes beside it, a part in a design run,
    and G-code with the Orca project it was sliced from beside it."""
    repo = shop.tmp / "another-repo"
    lid = shop.part("lid")
    (repo / "lid").mkdir(parents=True)
    lid = lid.rename(repo / "lid" / "lid.stl")
    (repo / "lid" / "lid.part.json").write_text(json.dumps({
        "fits": [{"name": "the rim", "mate": "box.stl", "play": 0.1, "z": [8, 10]}],
        "seen": ["top"]}))
    run = repo / "design-runs" / "made-up-run"
    (run / "parts").mkdir(parents=True)
    box = shop.part("box").rename(run / "parts" / "box.stl")
    (run / "needs.json").write_text(json.dumps(
        [{"id": "bed-1", "by": "bed", "file": "parts/box.stl", "side": "-z"}]))
    (run / "state.json").write_text(json.dumps(
        {"facts": {"f1": {"name": "box.stl", "text": "box is the made-up base"}}}))
    gcode = write_gcode(repo / "tiles")
    project_file(repo / "tiles")
    return {"lid": lid, "box": box, "gcode": gcode}


@pytest.fixture
def running(shop: Shop) -> Iterator[tuple[Daemon, PrintQueue]]:
    with ExitStack() as stack:
        daemon = stack.enter_context(shop.daemon())
        queue = shop.queue(daemon)
        queue._sliced_for = sliced_reader(shop.tmp / "no-settings.env", shop.store)
        stack.enter_context(queue)
        yield daemon, queue


def job_named(shop: Shop, part: str) -> str:
    return next(j.job_id for j in WaitingJobs(shop.work / "jobs.sqlite").all()
                if j.part == part and not j.copy_of)


# --------------------------------------------------------------------------
# Done when: three files sent from elsewhere wait, with their notes and overrides, after the
# originals move and the service restarts
# --------------------------------------------------------------------------


def test_three_files_sent_from_elsewhere_wait_with_their_notes_after_a_move_and_a_restart(
    shop: Shop,
) -> None:
    files = elsewhere(shop)
    done = sent(
        shop,
        Adding(files["lid"], purpose="the lid of a made-up box",
               loaded="handled, clipped or lightly loaded"),
        Adding(files["box"], spool_id=BLACK),
        Adding(files["gcode"], spool_id=GREY),
        note="the box set: lid first")
    said = "\n".join(done.lines())
    assert done.refused == [], said
    assert f"Queued 3 files for the person, as sent by {BY}, with the note 'the box set: lid " \
        "first'." in said
    assert "its notes, from the sidecar lid.part.json: 1 fit, which faces are seen" in said
    assert "what it's for, as Claude, in a made-up cad session said: the lid of a made-up box; " \
        "loaded as 'handled, clipped or lightly loaded'" in said
    assert "its notes, from the design run `made-up-run`, part `box`" in said
    assert "the side it's laid on (-z), 1 fact" in said
    assert ("its objects' overrides: from the project light-test.3mf, found beside it by its "
            f"objects' names ({', '.join(OBJECTS)})") in said
    assert "Nothing was prepared, approved, armed or started by sending." in said
    jobs = WaitingJobs(shop.work / "jobs.sqlite").all()
    assert [(j.part, j.waits, j.spool_id) for j in jobs] == [
        ("lid.stl", Waits.SENT, WHITE), ("box.stl", Waits.SENT, BLACK),
        ("light-test.gcode", Waits.SENT, GREY)]
    assert all(j.sent is not None and j.sent.by == BY and j.sent.note == "the box set: lid first"
               for j in jobs)

    # The originals move, or change, and their run is gone: the queue prints its own copies.
    shutil.move(shop.tmp / "another-repo", shop.tmp / "moved-away")
    lid, box, tiles = (j.job_id for j in jobs)
    with shop.daemon() as daemon, shop.queue(daemon) as queue:
        queue._sliced_for = sliced_reader(shop.tmp / "no-settings.env", shop.store)
        # G-code another session sliced is looked at once, as `--sliced` shows it, before it's
        # ready: never offered with only the bed's question for a screen (print-shop-ux).
        look = shop.need(queue, tiles, Need.LOOK)
        shown = "\n".join(look.shown)
        assert "per-object overrides (the project light-test.3mf" in shown, shown
        assert "The slice, read as it is" in shown
        assert shop.offered() == "", "nothing is offered before its look"
        assert queue.give(tiles, True) == ""
        wait_until(lambda: shop.waits(tiles) is Waits.READY)
        asked = shop.need(queue, lid, Need.QUESTIONS)
        assert any("the lid of a made-up box" in str(q) for q in asked.questions), \
            "what the sender said it's for is read back, as the notes' is"
        shop.need(queue, box, Need.QUESTIONS)
        assert [s.sent.by for s in queue.sent()] == [BY]
    # The service restarts: the same jobs, where they were, with their notes.
    with shop.daemon() as daemon, shop.queue(daemon) as queue:
        queue._sliced_for = sliced_reader(shop.tmp / "no-settings.env", shop.store)
        assert shop.waits(tiles) is Waits.READY, "its look is kept"
        for job_id in (lid, box):
            shop.answer(queue, job_id)
        review = shop.need(queue, lid, Need.REVIEW).review
        assert review is not None
        context = "\n".join(review.context)
        assert "fit" in context and "the rim" in context, context
        assert "kept from" in context and "another-repo" in context, context
        box_review = shop.need(queue, box, Need.REVIEW).review
        assert box_review is not None
        assert "box is the made-up base" in "\n".join(box_review.context)
        for job_id in (lid, box):
            shop.review(queue, job_id)
            wait_until(lambda j=job_id: shop.waits(j) is Waits.READY)  # type: ignore[misc]
    gcode_job = WaitingJobs(shop.work / "jobs.sqlite").get(tiles)
    assert gcode_job is not None
    file = gcode_job.state()["sliced"].file
    assert file.overrides and "the project light-test.3mf" in file.overrides_from
    assert Path(file.source).is_relative_to(shop.work / tiles)


# --------------------------------------------------------------------------
# Done when: the same files sent twice are said, not queued twice in silence
# --------------------------------------------------------------------------


def test_the_same_files_sent_twice_are_said_and_not_queued_twice(shop: Shop) -> None:
    lid = shop.part("lid")
    first = sent(shop, Adding(lid, copies=2))
    assert first.refused == []
    again = sent(shop, Adding(lid), Adding(lid, spool_id=BLACK), note="sent again by mistake")
    assert len(again.refused) == 1
    job_id = first.queued[0].job_id
    assert again.refused[0].startswith(
        f"lid.stl: the same bytes wait in the queue already for spool #{WHITE}, as job "
        f"{job_id[:8]} (lid.stl, 2 prints of it, sent by {BY}")
    assert "so it isn't queued twice. Give xN for more prints of one file" in again.refused[0]
    # For another spool it's another job, saying so.
    assert (f"the same bytes wait as job {job_id[:8]} (lid.stl) for spool #{WHITE}: this one is "
            f"another job, for spool #{BLACK}") in again.queued[0].came
    assert [j.spool_id for j in WaitingJobs(shop.work / "jobs.sqlite").all()] == [
        WHITE, WHITE, BLACK]


def test_the_same_bytes_as_a_past_print_are_queued_saying_how_it_came_out(shop: Shop) -> None:
    from print_shop.sliced import sha256_of  # noqa: PLC0415
    from tests.test_ledger import record  # noqa: PLC0415

    lid = shop.part("lid")
    past = record("past-job-1").model_copy(update={"stl_name": "old-lid.stl",
                                                    "stl_sha256": sha256_of(lid)})
    assert isinstance(past, PrintRecord)
    shop.ledger.record_start_requested(past)
    shop.ledger.record_started("past-job-1")
    shop.ledger.record_ended("past-job-1", 9)
    shop.ledger.record_answer("past-job-1", Outcome(result=Result.SUCCESS,
                                                    symptom=Symptom.STRINGING))
    done = sent(shop, Adding(lid))
    assert done.refused == []
    came = done.queued[0].came
    assert any(line.startswith("the same bytes printed before: old-lid.stl, job past-job, ")
               and line.endswith(": success, with stringing") for line in came), came


# --------------------------------------------------------------------------
# Done when: a suggested setting left untaken reaches neither the slice, the corrections nor a
# lesson
# --------------------------------------------------------------------------


def _slice_config(shop: Shop, queue: PrintQueue, job_id: str) -> dict[str, str]:
    graph = queue._graph(queue._jobs.get(job_id), 0)  # type: ignore[arg-type]
    values = graph.get_state({"configurable": {"thread_id": job_id}}).values
    return parse_config_block(Path(values["gcode_path"]))


def test_a_suggestion_untaken_reaches_neither_the_slice_the_corrections_nor_a_lesson(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    why = "the made-up rim is clipped on and off"
    done = sent(shop,
                Adding(shop.part("untaken"), suggested=(("wall_loops", "7", why),
                                                        ("wall_loops", "99", why),
                                                        ("pause_at", "3", why))),
                Adding(shop.part("taken"), spool_id=BLACK,
                       suggested=(("wall_loops", "7", why),)))
    assert done.refused == []
    came = done.queued[0].came
    assert f"suggested, offered at the review unticked: Walls (wall_loops) 7 ({why})" in came
    assert "not taken: the suggestion Walls (wall_loops) 99: outside 1-10" in came
    assert any(c.startswith("not taken: the suggestion Pause (pause_at) 3: a pause is set at "
                            "the review") for c in came)
    untaken, taken = (q.job_id for q in done.queued)
    wait_until(lambda: shop.waits(untaken) is not Waits.SENT and shop.waits(taken)
               is not Waits.SENT)
    for job_id in (untaken, taken):
        shop.answer(queue, job_id)

    # Offered as the sender's, unticked, with why.
    asked = shop.need(queue, untaken, Need.REVIEW)
    request = asked.review
    assert request is not None and request.offering is not None
    offer = request.offering
    assert [(i.key, i.value, i.suggested_by) for i in offer.suggested] == [
        ("wall_loops", "7", BY)]
    item = offer.suggested[0]
    pose = "0,0,0"
    standing = offer.standing(item, request.proposal, pose)
    assert standing.mark == " ", standing
    assert f"{BY} suggested it when sending this file: it's yours only if you use it" \
        in standing.why
    shown = "\n".join(render(offer, request.proposal, pose))
    assert f"SUGGESTED BY {BY.upper()}, WHO SENT IT" in shown
    assert why in shown and "[ ]" in shown
    assert request.proposal.get("wall_loops") is None or \
        request.proposal.get("wall_loops").value != "7"  # type: ignore[union-attr]
    # A bare use takes only what's ticked: never the suggestion.
    assert all(not i.suggested_by for i in offer.ticked(request.proposal, pose))
    assert queue.give(untaken, Review(approved=True)) == ""
    wait_until(lambda: shop.waits(untaken) is Waits.READY)
    assert _slice_config(shop, queue, untaken).get("wall_loops") != "7"

    # Taken, it's the person's correction, recorded as taken from the suggestion.
    other = shop.need(queue, taken, Need.REVIEW).review
    assert other is not None and other.offering is not None
    change = _used_change(other.offering.suggested[0], other)
    assert change.used is not None and change.used.suggested_by == BY
    assert change.used.line() == f"taken from what {BY} suggested when sending it"
    assert queue.give(taken, Review(corrections=(change,))) == ""
    again = shop.need(queue, taken, Need.REVIEW).review
    assert again is not None and again.proposal.get("wall_loops") is not None
    assert queue.give(taken, Review(approved=True)) == ""
    wait_until(lambda: shop.waits(taken) is Waits.READY)
    assert _slice_config(shop, queue, taken).get("wall_loops") == "7"

    # Printed: the untaken one's ledger row carries no correction, so no lesson can come of it.
    queue.first(untaken)
    wait_until(lambda: shop.offered() == untaken)
    shop.bridge.press(shop_cfg(), "approve")
    wait_until(lambda: shop.ledger.get(untaken) is not None)
    row = shop.ledger.get(untaken)
    assert row is not None
    assert not any(c.key == "wall_loops" for c in row.corrections or ())
    assert row.proposal is not None and (row.proposal.get("wall_loops") is None
                                         or row.proposal.get("wall_loops").value != "7")  # type: ignore[union-attr]
    assert Kept(shop.work / untaken).suggested_by_sender()[0].value == "7", \
        "kept as the sender's suggestion, never as an answer or a change"


def shop_cfg() -> Any:
    from tests.test_job_e2e import CFG  # noqa: PLC0415

    return CFG


# --------------------------------------------------------------------------
# Done when: sent with the arming flag set, nothing is armed, approved or started
# --------------------------------------------------------------------------


def _send_script() -> Any:
    spec = importlib.util.spec_from_file_location("send", REPO / "scripts" / "send.py")
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_sent_with_the_arming_flag_set_nothing_is_armed_approved_or_started(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from print_shop.orchestrator import daemon as daemon_module  # noqa: PLC0415
    from print_shop.printer import centauri  # noqa: PLC0415

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("sending reached for the printer or the daemon")

    monkeypatch.setenv("PRINT_AGENT_ALLOW_HARDWARE", "1")
    monkeypatch.setattr(centauri, "live_printer", never)
    monkeypatch.setattr(centauri.CentauriClient, "__init__", never)
    monkeypatch.setattr(daemon_module.Daemon, "__init__", never)
    script = _send_script()
    monkeypatch.setattr(script, "WORK_DIR", shop.work)
    monkeypatch.setattr(script, "STORE_DIR", shop.store)
    made_up = shop.tmp / "made-up.env"
    made_up.write_text(f"PRINT_SHOP_LEDGER={shop.ledger_path}\n")
    assert script.main([str(shop.part("armed")), "x2", "for=a made-up test", "loaded=prototype",
                        "--by", BY, "--settings", str(made_up)]) == 0
    out = capsys.readouterr().out
    assert "PRINT_AGENT_ALLOW_HARDWARE is set, and sending ignores it: nothing is armed" in out
    assert "Queued 1 file for the person" in out
    jobs = WaitingJobs(shop.work / "jobs.sqlite").all()
    assert [j.waits for j in jobs] == [Waits.SENT, Waits.WITH_FIRST]
    assert shop.ledger.rows() == [] and shop.printer.verbs == []
    import sqlite3  # noqa: PLC0415

    with sqlite3.connect(shop.work / "jobs.sqlite") as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"waiting_jobs"}, "no graph ran: nothing was prepared or checkpointed"
    # Nothing to answer, approve, arm or start with: only the files, who sent them and a note.
    assert _options(script) == {"-h", "--help", "--by", "--note", "--settings"}


def _options(script: Any) -> set[str]:
    import argparse  # noqa: PLC0415

    found: set[str] = set()
    real = argparse.ArgumentParser.parse_args

    def caught(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        for action in self._actions:
            found.update(action.option_strings)
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = caught  # type: ignore[method-assign]
    try:
        with pytest.raises(SystemExit):
            script.main(["x.stl"])
    finally:
        argparse.ArgumentParser.parse_args = real  # type: ignore[method-assign]
    return found


# --------------------------------------------------------------------------
# The person sees "Sent to you", and what came with each
# --------------------------------------------------------------------------


def test_the_person_sees_sent_to_you_with_who_when_the_note_and_what_each_needs(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    daemon, queue = running
    done = sent(shop, Adding(shop.part("lid"), suggested=(("wall_loops", "4", "it clips on"),)),
                Adding(shop.part("box"), spool_id=BLACK), note="the box set")
    lid = done.queued[0].job_id
    wait_until(lambda: shop.waits(lid) is not Waits.SENT)
    shop.need(queue, lid, Need.QUESTIONS)
    assert any(s.startswith(f"Sent to you: lid.stl, job {lid[:8]}, sent by {BY}")
               for s in queue.said()), queue.said()
    written: list[str] = []
    typed = iter(["sent 1", "quit"])
    assert serve(queue, daemon, read=lambda prompt: next(typed), write=written.append,
                 forget=lambda: None) == 0
    shown = "\n".join(written)
    assert "SENT TO YOU" in shown
    assert f"  Sent by {BY}, " in shown and " today: 'the box set': jobs 1, 2" in shown
    assert "lid.stl: sent by" in shown and "the queue prints its own copy" in shown
    assert "suggested, offered at the review unticked: Walls (wall_loops) 4 (it clips on)" in shown


# --------------------------------------------------------------------------
# What isn't taken, and why
# --------------------------------------------------------------------------


def test_what_isnt_taken_is_said_with_what_to_do_instead(shop: Shop) -> None:
    project = project_file(shop.tmp / "unsliced", "box.3mf")
    step = shop.tmp / "box.step"
    step.write_text("made-up")
    gcode = write_gcode(shop.tmp / "alone")
    done = sent(shop, Adding(project, spool_id=GREY), Adding(step),
                Adding(shop.tmp / "nope.stl"), Adding(gcode, spool_id=GREY, purpose="a test",
                                                     suggested=(("wall_loops", "3", "x"),)),
                Adding(shop.part("lid"), project=project))
    refused = "\n".join(done.refused)
    assert "box.3mf holds no G-code: it's a project, not a sliced plate. Slice it in Orca, then " \
        "export the plate's sliced file (.gcode.3mf) or its G-code" in refused
    assert "box.step: print-shop prints a part as an STL, or a file you sliced by hand" in refused
    assert "nope.stl: not a file" in refused
    assert "lid.stl: project= is for a file sliced by hand" in refused
    came = done.queued[0].came
    assert any(c.startswith("its objects' overrides: none (no Orca project was found beside it)")
               for c in came), came
    assert any(c.startswith("not taken: what it's for, what's seen and suggested settings: a "
                            "file sliced by hand is sent as it is") for c in came), came
    # Nothing is left behind of what wasn't queued.
    assert len([p for p in shop.work.iterdir() if p.is_dir()]) == 1


def test_a_sent_hand_sliced_file_rejected_at_its_look_is_out_of_the_queue_unprinted(
    shop: Shop, running: tuple[Daemon, PrintQueue],
) -> None:
    _, queue = running
    gcode = write_gcode(shop.tmp / "tiles")
    done = sent(shop, Adding(gcode, copies=2, spool_id=GREY))
    tiles = done.queued[0].job_id
    shop.need(queue, tiles, Need.LOOK)
    line = next(li for li in queue.lines() if li.job.job_id == tiles)
    assert line.step == "needs you: look at what it prints"
    assert queue.give(tiles, "yes") == "a look is answered ok (True) or reject (False)"
    assert queue.give(tiles, False) == ""
    wait_until(lambda: queue.jobs() == [])
    assert any(s == f"Removed: light-test.gcode, job {tiles[:8]}: you rejected what it prints. "
               "Nothing printed." for s in queue.said()), queue.said()
    assert shop.ledger.rows() == []
    # The person's own hand-sliced file, added at the queue, is theirs: no look.
    queue._sliced_for = sliced_reader(shop.tmp / "no-settings.env", shop.store)
    own = queue.add([Adding(gcode, spool_id=GREY)])[0][0]
    wait_until(lambda: shop.waits(own) is Waits.READY)


def test_when_is_said_in_the_macs_own_time() -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from print_shop.orchestrator.kept import local_when  # noqa: PLC0415

    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    local = now.astimezone()
    assert local_when(now.isoformat(), now) == f"{local:%H:%M} today"
    day = now - timedelta(days=1)
    assert local_when(day.isoformat(), now) == f"{day.astimezone():%H:%M} yesterday"
    old = (now - timedelta(days=40)).astimezone()
    assert local_when(old.isoformat(), now) == f"{old.day} {old:%b} {old:%H:%M}"
    last_year = (now - timedelta(days=400)).astimezone()
    assert local_when(last_year.isoformat(), now).endswith(f"{last_year.year} "
                                                           f"{last_year:%H:%M}")


def test_the_words_after_a_file_are_read(tmp_path: Path) -> None:
    found = parse_adding(["a.stl", "x2", "spool=3", "for=a made-up lid", "loaded=handled",
                          "seen=walls,top", "suggest=wall_loops=4 because it clips on",
                          "b.gcode", "project=b.3mf"])
    assert not isinstance(found, str)
    a, b = found
    assert (a.copies, a.spool_id, a.purpose, a.loaded, a.seen, a.suggested) == (
        2, 3, "a made-up lid", LOADS[1], ("walls", "top"), (("wall_loops", "4", "it clips on"),))
    assert b.project == Path("b.3mf")
    assert parse_adding(["for=x"]) == "for=x goes after the file it's for"
    assert "a suggestion is suggest=" in str(parse_adding(["a.stl", "suggest=wall_loops 4"]))
    assert "seen lists walls, top and bottom" in str(parse_adding(["a.stl", "seen=side"]))
    assert loaded_from("prototype") == LOADS[3] and loaded_from("decoration") == LOADS[0]
    with pytest.raises(ValueError, match="loaded is one of"):
        loaded_from("heavy")


def test_a_kept_context_is_read_whole_wherever_the_original_went(tmp_path: Path) -> None:
    from print_shop.setup.context import keep  # noqa: PLC0415

    folder = tmp_path / "part"
    folder.mkdir()
    stl = folder / "lid.stl"
    stl.write_text("solid made-up\nendsolid\n")
    (folder / "lid.part.json").write_text(json.dumps({"seen": ["walls"], "down": "+z"}))
    kept = keep(read_context(stl), tmp_path / f"lid{KEPT}", str(stl))
    shutil.rmtree(folder)
    again = read_context(tmp_path / "anywhere.stl", kept)
    assert again.down == "+z" and again.seen is not None and again.seen.walls
    assert again.read[-1] == f"kept from {stl}"


# --------------------------------------------------------------------------
# The skill, and the runbook
# --------------------------------------------------------------------------


def test_the_skill_says_how_to_send_and_that_sending_is_all_it_does() -> None:
    skill = (REPO / "skills" / "send-to-queue" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: send-to-queue\n")
    assert "scripts/send.py" in skill and "--by" in skill and "--note" in skill
    assert "for=" in skill and "loaded=" in skill, "it says to give each part's purpose"
    for load in LOADS:
        assert load in skill
    assert "Sending is all you do" in skill
    runbook = (REPO / "docs" / "runbook-first-print.md").read_text()
    assert "skills/send-to-queue" in runbook and "~/.claude/skills/send-to-queue" in runbook
    # The skill's own example is one the command reads.
    example = next(line for line in skill.splitlines() if "send.py" in line and ".stl" in line)
    import shlex  # noqa: PLC0415

    words = shlex.split(example.strip().strip("`"))
    files = words[words.index(next(w for w in words if w.endswith(".stl"))):]
    files = [w for w in files if not w.startswith("--")]
    assert not isinstance(parse_adding(files), str), parse_adding(files)
