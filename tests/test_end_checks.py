"""When a run says it's done, the harness reads each held part's way on and what bends on the
way, records a failure as a lesson, and sends the run back in a new conversation from the record
(mqucifer/print-shop#150).

No Docker and no model: a scripted model drives each run, and a box answers each bench command
by its name and runs nothing. The parts are made up: a box and a lid held by a clip, a channel
and a strip given with it. The readings the box answers with are the ones the second image
gives on the planted fixtures (`tests/cae_cases/make_fixtures.py`), which
`tests/test_end_checks_cae.py` reads for real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import arm as arm_module
from print_shop.bench import bend as bend_module
from print_shop.bench import overlap as overlap_module
from print_shop.rounds import end_checks, holds, lessons, needs, prompt, records, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run, summarise
from tests.rounds_fakes import Scripted, flat, tool
from tests.test_holding import Answers
from tests.test_intake import pick

REQUEST = """\
A box and a lid, both printed.

## Requirements
- The lid snaps onto the box and stays on until pulled off.

## Stops
- no plan
"""
STRIP_REQUEST = """\
A channel for the LED strip given.

## Requirements
- The strip is held in the channel by a catch it's pushed past.

## Stops
- no plan
"""
SLIDE = "clearance box.step lid.step --slide +z"
STAYS = Need(
    "stays",
    "the lid stays on the box until pulled off",
    "request",
    "bench",
    quote="The lid snaps onto the box and stays on until pulled off.",
    command=SLIDE,
    verdicts=["HELD"],
    measures="fit",
)
STRIP = "references/strip.step"
STRIP_SLIDE = f"clearance channel.step {STRIP} --slide +z"
CAUGHT = Need(
    "caught",
    "the strip is held in the channel",
    "request",
    "bench",
    quote="The strip is held in the channel by a catch it's pushed past.",
    command=STRIP_SLIDE,
    verdicts=["HELD"],
    measures="fit",
)
DONE = {"script": "make.py", "fix_shape": "first", "closing": "Here."}
FIT = "fit-on lid.step --onto box.step --along +z --from clear"
#: `fit-on`'s place on the planted box: the lid's band over the clip's hook, 0.6 mm deep.
HOOK = {
    "file": "box.step",
    "box": [[-4, -15.6, 24], [4, -15, 25.6]],
    "depth_mm": 0.6,
    "across": [0.0, 1.0, 0.0],
    "out_mm": 2.0,
    "seated": False,
}
BLOCKED = ("BLOCKED", {"places": [HOOK], "depth_mm": 0.6, "from_mm": 8.25, "readings": 34})
#: The clip as `bench/arm.py` finds it, short, from z 18, and long, from z 10.
SHORT_ARM = {
    "found": True,
    "arm": [[-4.1, -17.1, 18.0], [4.1, -14.9, 26.1]],
    "root": ["z", 18.0],
    "toward": "-y",
}
LONG_ARM = {**SHORT_ARM, "arm": [[-4.1, -17.1, 10.0], [4.1, -14.9, 26.1]], "root": ["z", 10.0]}
NO_ARM = {"found": False, "why": "nothing there stands free from a root to a tip"}
SHORT_BEND = "bend box.step --arm -4.1,-17.1,18,4.1,-14.9,26.1 --root z=18 --push 0.6 --toward -y"
#: `bend`'s readings of the two: 2.98% over the 1% limit, and 0.59% within it.
TOO_FAR = (
    "FAIL",
    {
        "across_layers_pct": 2.98,
        "limit_pct": 1.0,
        "room_behind_mm": 1.0,
        "back_moves_mm": 0.6,
        "would_pass": {"thickness_mm": 0.403, "length_mm": 10.354, "push_mm": 0.201},
    },
)
TAKES = (
    "OK",
    {
        "across_layers_pct": 0.59,
        "limit_pct": 1.0,
        "room_behind_mm": 1.0,
        "back_moves_mm": 0.6,
        "would_pass": {"thickness_mm": 2.034, "length_mm": 10.752, "push_mm": 1.017},
    },
)


class Bench(Answers):
    """Each bench command answered by its name: `fit-on` and `bend` by the file they read,
    `clearance` as `HELD`, anything else `OK`; and `bench/arm.py` by the file it looks on."""

    def __init__(
        self,
        work: Path,
        fit: dict[str, Any],
        bends: dict[str, Any] | None = None,
        arms: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(work)
        self.fit, self.bends, self.arms = fit, bends or {}, arms or {}
        self.helped: list[list[str]] = []
        self.makes: tuple[str, ...] = ()

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        out = args.index("--out") if "--out" in args else -2
        words = [a for i, a in enumerate(args) if i not in (out, out + 1)]
        if words[0] == "fit-on":
            self.answers = [self.fit[words[1]]]
        elif words[0] == "bend":
            self.answers = [self.bends[words[1]]]
        elif words[0] == "clearance":
            self.answers = ["HELD"]
        else:
            self.answers = []
        return super().bench(args, where, timeout)

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        for name in self.makes:
            ((where or self.work) / name).write_text("x")
        return Ran(0, "ran\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "arm":
            self.helped.append(args)
            return Ran(0, arm_module.MARK + json.dumps(self.arms.get(args[0], NO_ARM)), 0.1)
        if module == "overlap":
            said = {"files": {name: {} for name in args}, "pairs": [], "unread": {}}
            return Ran(0, overlap_module.MARK + json.dumps(said), 0.1)
        return super().helper(module, args, timeout, where)


def started(
    root: Path,
    *turns: Any,
    fit: Any = BLOCKED,
    bends: dict[str, Any] | None = None,
    arms: dict[str, Any] | None = None,
) -> tuple[Run, Scripted]:
    """A run in its first round, its needs settled and its breakdown kept: a lid held by its
    box, both printed, read by the slide a need runs."""
    model = Scripted(
        *[t if isinstance(t, list) else [t] for t in turns],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(root, "r", REQUEST, [], model, sleep=lambda _: None)
    box = Bench(run.work, {"lid.step": fit}, bends, arms)
    box.makes = ("box.step", "lid.step")
    _in_rounds(run, box, STAYS, ("box", "lid"), "the lid's band snaps over a clip on the box")
    return run, model


def _in_rounds(run: Run, box: Bench, need: Need, parts: tuple[str, str], how: str) -> None:
    run.box = box  # type: ignore[assignment]
    run.tools.box = box  # type: ignore[assignment]
    for name in (*box.makes, "make.py"):
        (run.work / name).write_text("x")
    (run.folder / "breakdowns").mkdir(exist_ok=True)
    broken = {
        "parts": [{"name": name, "what": name} for name in parts],
        "joins": [{"parts": list(parts), "how": how}],
    }
    (run.folder / "breakdowns" / "1.json").write_text(json.dumps(broken))
    run.state.breakdowns = 1
    run.state.needs = needs.dump([need])
    run.state.needs_open = False
    run.state.needs_version = 1
    (run.folder / "needs").mkdir(exist_ok=True)
    (run.folder / "needs" / "1.json").write_text(json.dumps([need.plain()]))
    run.state.stage = "rounds"
    run._start_round()
    run.save()


def replies(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def rows(run: Run, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in run.ledger.rows(sql)]


FACT = tool(
    "fact",
    name="clip",
    text="The clip's hook stands 0.8 mm out, and the lid's band is 0.2 mm clear of the arm.",
    **{"from": "call 1, my own script"},
)
LID_DONE = tool("done", part=["box.step", "lid.step"], on_bed=flat("box.step", "lid.step"), **DONE)


# The way on, from the holds


def test_a_held_part_s_way_on_is_its_slide_s_a_need_s_first_then_the_latest_run() -> None:
    lid = holds.Held("lid", "", holds.PRINTED, "box")
    here = ["box.step", "lid.step", "references/fan.step"]
    found, unread = end_checks.ways([lid], [STAYS], [], here, here[:2])
    assert unread == []
    assert found == [
        end_checks.Way("lid", "lid.step", ("box.step",), "+z", "the need `stays`'s slide")
    ]
    assert found[0].command == FIT
    ran = [
        {"at_call": 3, "argv": json.dumps(["clearance", "box.step", "lid.step", "--slide", "+y"])},
        {"at_call": 7, "argv": json.dumps(["clearance", "box.step", "lid.step", "--slide", "-z"])},
        {"at_call": 9, "argv": json.dumps(["info", "lid.step"])},
    ]
    found, _ = end_checks.ways([lid], [], ran, here, here[:2])
    assert (found[0].along, found[0].source) == ("-z", "call 7's slide")


def test_a_bought_part_s_way_on_is_its_fact_s_and_one_with_none_is_said() -> None:
    fan = holds.Held("references/fan.step", "-z", "fact", "the lid's clips")
    here = ["lid.step", "references/fan.step"]
    found, unread = end_checks.ways([fan], [], [], here, ["lid.step"])
    assert unread == []
    assert found[0].command == "fit-on references/fan.step --onto lid.step --along -z --from clear"
    assert found[0].source == "its fact's way in"
    unknown = holds.Held("references/fan.step", "", "breakdown", "lid")
    found, unread = end_checks.ways([unknown], [], [], here, ["lid.step"])
    assert found == []
    assert "its way on wasn't read" in unread[0] and "no fact gives its way in" in unread[0]
    mesh = Need("s", "x", "chosen", "bench", command="clearance box.stl lid.step --slide +z")
    lid = holds.Held("lid", "", holds.PRINTED, "box")
    found, unread = end_checks.ways([lid], [mesh], [], ["box.stl", "lid.step"], [])
    assert found == [] and "`fit-on` reads STEP files, and `box.stl` isn't one" in unread[0]


# Wanted: sent back for a clip that bends too far, and to the stop for one that takes it


def test_a_run_whose_needs_all_pass_is_sent_back_for_a_clip_that_bends_too_far(
    tmp_path: Path,
) -> None:
    run, model = started(
        tmp_path, FACT, LID_DONE, bends={"box.step": TOO_FAR}, arms={"box.step": SHORT_ARM}
    )
    assert run.drive().name == "blocked"
    done = replies(run)[1]
    assert done["name"] == "done" and done["detail"]["met"] is False
    assert done["detail"]["end_checks"]["sent_back"] is True
    assert done["detail"]["end_checks"]["by"] == "harness"
    assert "Round 1: not met yet. Every need passed" in done["reply"]
    assert not (run.folder / "approvals").exists(), "the stop for approval isn't asked"
    assert run.state.stop is None and run.state.round == 2
    # The readings, in the note: where, how deep, which arm, its strain against the limit, the
    # room behind it and what would pass, each with the command that read it.
    note = run.state.end_sent["note"]  # type: ignore[index]
    assert note.startswith("The harness's checks at the end sent the part back.")
    assert "not the Sponsor's" in note
    assert f"`{FIT}` reads BLOCKED" in note
    assert "place 0, at (-4, -15.6, 24) to (4, -15, 25.6), 0.600 mm deep across y" in note
    assert "The arm is `box.step`'s, in the box (-4.1, -17.1, 18) to (4.1, -14.9, 26.1)" in note
    assert "from its root at z=18, pushed 0.6 mm -y" in note
    assert f"`{SHORT_BEND} --layers z` reads FAIL: 2.98% across its layers" in note
    assert "2.98% across its layers against the 1% limit" in note
    assert "its back moving 0.600 mm with 1.000 mm of room behind it" in note
    assert (
        "What would pass: at this push, an arm at most 0.403 mm thick at its root, or at least "
        "10.354 mm from its root to the pushed edge; at this arm, a push of at most 0.201 mm"
    ) in note
    assert note in done["reply"]
    # The arm was looked for at the place, on the box, across y.
    assert run.box.helped == [  # type: ignore[attr-defined]
        ["box.step", "--place", "-4,-15.6,24,4,-15,25.6", "--across", "y"]
    ]
    # The lesson: a candidate by the harness, a `verify` lesson, its evidence the commands.
    lesson = rows(run, "SELECT * FROM lessons")
    assert len(lesson) == 1
    assert (lesson[0]["written_by"], lesson[0]["kind"], lesson[0]["status"]) == (
        lessons.HARNESS,
        "verify",
        lessons.CANDIDATE,
    )
    assert f"`{FIT}` read BLOCKED" in lesson[0]["text"] and SHORT_BEND in lesson[0]["text"]
    held = json.loads(lesson[0]["evidence"])
    assert [c["command"] for c in held["end_checks"]] == [FIT, f"{SHORT_BEND} --layers z"]
    assert [c["verdict"] for c in held["end_checks"]] == ["BLOCKED", "FAIL"]
    assert lessons.keys_of(run.ledger, lesson[0]["id"]) == [
        {"command": "bend", "verdict": "FAIL"},
        {"command": "fit-on", "verdict": "BLOCKED"},
    ]
    listed = lessons.listing(run.ledger, "r")
    assert f"end check `{FIT}`: BLOCKED" in listed
    assert f"end check `{SHORT_BEND} --layers z`: FAIL" in listed
    assert f"kept as lesson {lesson[0]['id']} in the ledger" in note
    # The send-back is the harness's own decision, on no stop for approval.
    decided = rows(run, "SELECT approval, decision, by, note FROM decisions")
    assert decided == [{"approval": None, "decision": "sent_back", "by": "harness", "note": note}]
    stood = rows(run, "SELECT check_name, file, passed, verdict FROM standing ORDER BY id")
    assert {"check_name": "way-on", "file": "lid.step", "passed": 0, "verdict": "BLOCKED"} in stood
    assert {"check_name": "bend", "file": "box.step", "passed": 0, "verdict": "FAIL"} in stood
    assert rows(run, "SELECT met FROM rounds WHERE round=1") == [{"met": 0}]


def test_the_send_back_starts_the_conversation_again_from_the_record(tmp_path: Path) -> None:
    """The earlier turns aren't sent, and the request, the needs, the facts and the files are:
    the readings are the first message after the start."""
    run, model = started(
        tmp_path, FACT, LID_DONE, bends={"box.step": TOO_FAR}, arms={"box.step": SHORT_ARM}
    )
    assert run.drive().name == "blocked"
    before, after = model.bodies[1]["messages"], model.bodies[2]["messages"]
    assert any(m["role"] == "assistant" for m in before), "call 2 carried call 1's turn"
    assert [m["role"] for m in after] == ["system", "user", "user"]
    assert "thinking" not in json.dumps(after), "none of the earlier calls' reasoning"
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in after)
    start, note = after[1]["content"], after[2]["content"]
    assert "The lid snaps onto the box and stays on until pulled off." in start
    assert "The needs, as settled:" in start and SLIDE in start
    assert "The clip's hook stands 0.8 mm out" in start, "the facts, whole"
    assert prompt.AGAIN_WHY["end_checks"] in start
    assert "ran out of room" not in start
    assert note == run.state.end_sent["note"]  # type: ignore[index]
    assert sorted(p.name for p in run.work.glob("*.step")) == ["box.step", "lid.step"]
    assert (run.work / "make.py").is_file()
    # Its own kind of restart, apart from a `context_full` one, in the state and the ledger.
    assert run.state.restarts == 0
    assert run.state.restart_kinds == [{"why": "end_checks", "before_call": 3}]
    line = rows(run, "SELECT kind, name, at_call FROM timeline WHERE kind IN ('stop', 'restart')")
    assert line[:2] == [
        {"kind": "stop", "name": "sent_back", "at_call": 2},
        {"kind": "restart", "name": "end_checks", "at_call": 3},
    ]
    relayed = rows(run, "SELECT kind, from_who, at_call FROM interventions")
    assert relayed == [{"kind": "end_checks", "from_who": "harness", "at_call": 3}]
    assert run.state.last_said is None, "never taken for what a person last said"
    assert rows(run, "SELECT COUNT(*) AS n FROM timeline WHERE kind='resume'")[0]["n"] == 0


def test_a_clip_that_takes_it_goes_to_the_stop_for_approval_with_the_readings(
    tmp_path: Path,
) -> None:
    run, _ = started(tmp_path, LID_DONE, bends={"box.step": TAKES}, arms={"box.step": LONG_ARM})
    assert run.drive().name == "met"
    done = replies(run)[0]
    assert "end_checks" not in done["detail"] and done["detail"]["met"] is True
    assert f"{end_checks.HEADING}:" in done["reply"]
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f"## {end_checks.HEADING}" in stop
    assert f"its way out +z from the need `stays`'s slide, ok: `{FIT}` reads BLOCKED" in stop
    assert "0.600 mm deep across y, with it 2 mm out: passes." in stop
    assert "reads OK: 0.59% across its layers against the 1% limit" in stop
    assert rows(run, "SELECT COUNT(*) AS n FROM lessons")[0]["n"] == 0
    assert rows(run, "SELECT COUNT(*) AS n FROM decisions")[0]["n"] == 0
    stood = rows(
        run, "SELECT check_name, passed FROM standing WHERE check_name IN ('way-on', 'bend')"
    )
    assert stood == [{"check_name": "way-on", "passed": 1}, {"check_name": "bend", "passed": 1}]
    assert run.state.end_sent is None and run.state.restart_kinds == []
    kept = json.loads((run.folder / "rounds" / "001" / "end.json").read_text())
    assert kept["ways"][0]["places"][0]["status"] == end_checks.TAKES


def test_a_way_on_with_nothing_in_it_passes_and_a_run_that_holds_nothing_is_read_for_none(
    tmp_path: Path,
) -> None:
    run, _ = started(tmp_path / "a", LID_DONE, fit="CLEAR")
    assert run.drive().name == "met"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f", ok: `{FIT}` reads CLEAR" in stop
    assert run.box.helped == []  # type: ignore[attr-defined]
    run, _ = started(tmp_path / "b", LID_DONE)
    (run.folder / "breakdowns" / "1.json").write_text(json.dumps({"parts": [], "joins": []}))
    plain = Need("stays", "the box is 30 mm tall", "chosen", "bench", command=SLIDE)
    plain.verdicts = ["HELD"]
    run.state.needs = needs.dump([plain])
    run.save()
    assert run.drive().name == "met"
    asked = [a for a in run.box.asked if "fit-on" in a]  # type: ignore[attr-defined]
    assert asked == [], "nothing is held, so no way on is read"
    assert end_checks.HEADING not in (run.folder / "approvals" / "1" / "stop.md").read_text()


def test_a_place_with_no_arm_found_stands_as_blocked_and_says_why(tmp_path: Path) -> None:
    run, _ = started(tmp_path, LID_DONE)
    assert run.drive().name == "blocked"
    note = run.state.end_sent["note"]  # type: ignore[index]
    assert "DOESN'T PASS: it stands as BLOCKED. No arm that could move aside was found" in note
    assert "on `box.step`: nothing there stands free from a root to a tip" in note
    assert "on `lid.step`: nothing there stands free from a root to a tip" in note
    # On the lid, the place is where the lid was 2 mm out: 2 mm back along its way out.
    assert run.box.helped[1] == [  # type: ignore[attr-defined]
        "lid.step", "--place", "-4,-15.6,22,4,-15,23.6", "--across", "y"
    ]
    assert not [a for a in run.box.asked if "bend" in a]  # type: ignore[attr-defined]
    text = rows(run, "SELECT text FROM lessons")[0]["text"]
    assert "nothing there was read as an arm that could move aside" in text


def test_a_way_on_the_bench_has_no_answer_for_is_cant_tell_and_goes_to_the_stop(
    tmp_path: Path,
) -> None:
    run, _ = started(tmp_path, LID_DONE, fit="DISAGREE")
    assert run.drive().name == "met"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f"can't tell: `{FIT}` reads DISAGREE" in stop
    stood = rows(run, "SELECT passed, unsure FROM standing WHERE check_name='way-on'")
    assert stood == [{"passed": 0, "unsure": 1}]


def test_a_second_send_back_for_the_same_place_writes_no_second_lesson(tmp_path: Path) -> None:
    run, model = started(
        tmp_path, LID_DONE, LID_DONE, bends={"box.step": TOO_FAR}, arms={"box.step": SHORT_ARM}
    )
    assert run.drive().name == "blocked"
    assert rows(run, "SELECT COUNT(*) AS n FROM lessons")[0]["n"] == 1
    assert rows(run, "SELECT COUNT(*) AS n FROM decisions WHERE by='harness'")[0]["n"] == 2
    assert [k["why"] for k in run.state.restart_kinds] == ["end_checks", "end_checks"]
    assert run.state.round == 3


# Wanted: a given part that has to give way is named for a person, not failed as PETG


def test_a_given_strip_pushed_past_a_printed_catch_is_named_for_a_person(tmp_path: Path) -> None:
    given = tmp_path / "given" / "strip.step"
    given.parent.mkdir()
    given.write_text("a strip, made up")
    model = Scripted(
        [tool("done", part=["channel.step"], on_bed=flat("channel.step"), **DONE)],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(tmp_path / "runs", "r", STRIP_REQUEST, [given], model, sleep=lambda _: None)
    catch = {
        "file": "channel.step",
        "box": [[4.7, -20, 4], [5, 20, 5]],
        "depth_mm": 0.3,
        "across": [1.0, 0.0, 0.0],
        "out_mm": 2.0,
        "seated": False,
    }
    wall = {
        "found": True,
        "arm": [[4.6, -20.1, 2.0], [7.3, 20.1, 5.1]],
        "root": ["z", 2.0],
        "toward": "+x",
    }
    box = Bench(
        run.work,
        {STRIP: ("BLOCKED", {"places": [catch], "depth_mm": 0.3})},
        {"channel.step": "DISAGREE"},
        {"channel.step": wall},
    )
    box.makes = ("channel.step",)
    _in_rounds(run, box, CAUGHT, ("channel", "strip"), "a catch on the channel's wall")
    assert [h.part for h in run._holds()] == [STRIP]
    assert run.drive().name == "met", "read, and not failed as PETG"
    assert [a[0] for a in box.helped] == ["channel.step"], "no arm is looked for on a given part"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "for a person to judge: `fit-on references/strip.step --onto channel.step" in stop
    assert (
        "`references/strip.step` is a given part, not printed PETG, so it isn't bent as PETG: "
        "`references/strip.step` has to give way by 0.300 mm to pass, and whether it can is a "
        "person's to judge."
    ) in stop
    assert "What's printed there wasn't read as an arm that bends" in stop
    assert "reads DISAGREE" in stop
    assert rows(run, "SELECT COUNT(*) AS n FROM lessons")[0]["n"] == 0
    assert rows(run, "SELECT COUNT(*) AS n FROM decisions")[0]["n"] == 0
    kept = json.loads((run.folder / "rounds" / "001" / "end.json").read_text())
    assert kept["ways"][0]["places"][0]["status"] == end_checks.PERSON


def test_a_printed_clip_over_its_limit_sends_the_run_back_though_a_given_part_pushes_it(
    tmp_path: Path,
) -> None:
    """A rigid bought part forced past a printed clip that's over its limit is the run's design
    to fix: only where no printed arm is read is the given part what has to give way."""
    given = tmp_path / "given" / "fan.step"
    given.parent.mkdir()
    given.write_text("a fan, made up")
    model = Scripted(
        [tool("done", part=["pocket.step"], on_bed=flat("pocket.step"), **DONE)],
        [tool("blocked", reason="Out of turns.")],
    )
    request = STRIP_REQUEST.replace("LED strip", "fan").replace("strip", "fan")
    run = Run.start(tmp_path / "runs", "r", request, [given], model, sleep=lambda _: None)
    fan = "references/fan.step"
    clip = {**HOOK, "file": "pocket.step"}
    box = Bench(
        run.work,
        {fan: ("BLOCKED", {"places": [clip], "depth_mm": 0.6})},
        {"pocket.step": TOO_FAR},
        {"pocket.step": SHORT_ARM},
    )
    box.makes = ("pocket.step",)
    held = Need(
        "caught",
        "the fan is held in the pocket",
        "request",
        "bench",
        quote="The fan is held in the channel by a catch it's pushed past.",
        command=f"clearance pocket.step {fan} --slide +z",
        verdicts=["HELD"],
        measures="fit",
    )
    _in_rounds(run, box, held, ("pocket", "fan"), "a clip on the pocket's wall")
    assert run.drive().name == "blocked"
    note = run.state.end_sent["note"]  # type: ignore[index]
    assert "DOESN'T PASS. The arm is `pocket.step`'s" in note
    assert "2.98% across its layers against the 1% limit" in note
    assert "for a person to judge" not in note
    assert not (run.folder / "approvals").exists()
    assert rows(run, "SELECT by FROM decisions") == [{"by": "harness"}]
    assert rows(run, "SELECT COUNT(*) AS n FROM lessons")[0]["n"] == 1
    kept = json.loads((run.folder / "rounds" / "001" / "end.json").read_text())
    assert kept["ways"][0]["places"][0]["status"] == end_checks.OVER


# The approval stop isn't asked until the checks pass, or the run asks a person about them


def test_a_person_asked_about_a_failing_place_lets_it_by_and_its_shown_as_theirs(
    tmp_path: Path,
) -> None:
    ask = tool(
        "ask",
        question="The clip is short on purpose: it's a one-time fit. Is that right?",
        variants=[{"name": "A", "files": ["box.step", "lid.step"]}],
        end_checks=True,
    )
    sent = tool("send_ask", says="The box with its clip, and the lid over it.")
    run, model = started(
        tmp_path, LID_DONE, ask, sent, bends={"box.step": TOO_FAR}, arms={"box.step": SHORT_ARM}
    )
    assert run.drive().name == "ask"
    drawn = replies(run)[1]
    assert drawn["outcome"] == "ok", drawn["reply"]
    assert "go beside the question, for a person to accept or not" in drawn["reply"]
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "## The harness's end checks that don't pass, for you to accept or not" in question
    assert "reads FAIL: 2.98% across its layers against the 1% limit" in question
    pick(run, 1, "A", by="stand-in: a tester")
    box = run.box
    model = Scripted([LID_DONE], [tool("blocked", reason="Out of turns.")])
    run = Run.load(run.folder, model, sleep=lambda _: None)
    run.box = box
    run.tools.box = box
    run.resume()
    assert run.state.end_accepted == [
        {
            "key": "lid.step onto box.step",
            "ask": 1,
            "by": "stand-in: a tester",
            "reason": "As I'd hoped.",
        }
    ]
    assert "accepted the end checks that don't pass (lid.step onto box.step)" in json.dumps(
        run.session.messages
    )
    assert run.drive().name == "met"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert ", accepted by a person: `fit-on lid.step" in stop
    assert "DOESN'T PASS." in stop and "2.98% across its layers" in stop
    assert "Accepted at ask 1 by stand-in: a tester: As I'd hoped." in stop
    assert rows(run, "SELECT COUNT(*) AS n FROM decisions WHERE by='harness'")[0]["n"] == 1


def test_asking_about_end_checks_that_all_pass_is_refused(tmp_path: Path) -> None:
    ask = tool(
        "ask",
        question="Is the clip right?",
        variants=[{"name": "A", "files": ["box.step", "lid.step"]}],
        end_checks=True,
    )
    run, _ = started(tmp_path, ask, bends={"box.step": TAKES}, arms={"box.step": LONG_ARM})
    assert run.drive().name == "blocked"
    assert replies(run)[0]["why"] == "no_end_check_fails"


# `resume --send-back --fresh`: a person's send-back can start again from the record too


def test_a_person_s_send_back_starts_again_from_the_record_only_with_fresh(tmp_path: Path) -> None:
    def sent_back(root: Path, fresh: bool) -> tuple[Run, Scripted]:
        run, _ = started(root, LID_DONE, fit="CLEAR")
        assert run.drive().name == "met"
        box = run.box
        model = Scripted([tool("blocked", reason="Read.")])
        run = Run.load(run.folder, model, sleep=lambda _: None)
        run.box = box
        run.tools.box = box
        run.send_back("sponsor", "The lid rattles.", fresh=fresh)
        assert run.drive().name == "blocked"
        return run, model

    run, model = sent_back(tmp_path / "fresh", True)
    after = model.bodies[0]["messages"]
    assert [m["role"] for m in after] == ["system", "user", "user"]
    assert prompt.AGAIN_WHY["send_back"] in after[1]["content"]
    assert "The lid rattles." not in after[1]["content"], "the note is said once"
    assert after[2]["content"].startswith("The Sponsor looked at the final part and sent it back")
    assert "The lid rattles." in after[2]["content"]
    assert run.state.restart_kinds == [{"why": "send_back", "before_call": 2}]
    assert run.state.restarts == 0
    line = rows(run, "SELECT name FROM timeline WHERE kind='restart'")
    assert line == [{"name": "send_back"}]
    assert run.state.last_said["kind"] == "send_back"  # type: ignore[index]

    run, model = sent_back(tmp_path / "whole", False)
    after = model.bodies[0]["messages"]
    assert any(m["role"] == "assistant" for m in after), "the whole history, as before"
    assert run.state.restart_kinds == []
    assert rows(run, "SELECT COUNT(*) AS n FROM timeline WHERE kind='restart'")[0]["n"] == 0


# `bend`'s "what would pass"


@pytest.mark.parametrize(
    ("push", "thick", "length", "peak", "limit"),
    [(0.6, 1.2, 6.0, 2.98, 1.0), (0.6, 1.2, 14.0, 0.59, 1.0), (1.6, 0.8, 9.5, 3.3, 2.5)],
)
def test_what_would_pass_keeps_the_strip_s_strain_at_the_limit(
    push: float, thick: float, length: float, peak: float, limit: float
) -> None:
    """A strip's strain is ε = 1.5·t·y/L². Whatever the reading's peak is to that formula's own
    figure, each of the three, with the other two as they are, brings the peak to the limit."""

    def strip(t: float, y: float, long: float) -> float:
        return 1.5 * t * y / long**2

    passing = bend_module.would_pass(push, thick, length, peak, limit)
    scale = peak / strip(thick, push, length)
    assert scale * strip(thick, passing["push_mm"], length) == pytest.approx(limit)
    assert scale * strip(passing["thickness_mm"], push, length) == pytest.approx(limit)
    assert scale * strip(thick, push, passing["length_mm"]) == pytest.approx(limit)
    over = peak > limit
    assert (passing["push_mm"] < push) == over and (passing["length_mm"] > length) == over


# A send-back after approval withdraws that approval


def test_a_send_back_after_approval_records_the_approval_as_withdrawn(tmp_path: Path) -> None:
    """The Sponsor, 2026-11-15: "I don't want a success in a ledger when new information
    contradicts it." The approval's own row says who withdrew it and why, and so do the run's
    summary and its read-back."""
    run, _ = started(tmp_path, LID_DONE, fit="CLEAR")
    assert run.drive().name == "met"
    assert run.approve("sponsor", "Print it.").name == "approved"
    stands = rows(run, "SELECT decision, withdrawn_at, withdrawn_by FROM decisions")
    assert stands == [{"decision": "approved", "withdrawn_at": None, "withdrawn_by": None}]
    assert summarise(run.folder)["approvals_withdrawn"] == []
    box = run.box
    model = Scripted([tool("blocked", reason="Read.")])
    run = Run.load(run.folder, model, sleep=lambda _: None)
    run.box = box
    run.tools.box = box
    why = "`fit-on` reads the lid BLOCKED, and `bend` its clip FAIL."
    run.send_back("stand-in: the operator", why, fresh=True)
    decided = rows(run, "SELECT * FROM decisions ORDER BY id")
    approved, sent = decided
    assert (approved["decision"], sent["decision"]) == ("approved", "sent_back")
    assert approved["withdrawn_by"] == "stand-in: the operator"
    assert approved["withdrawn_why"] == why
    assert approved["withdrawn_decision"] == sent["id"] and approved["withdrawn_at"]
    assert sent["withdrawn_at"] is None, "only the approval is withdrawn"
    kept = run.state.withdrawn
    assert kept == [
        {
            "approval": 1,
            "decision": approved["id"],
            "by": "stand-in: the operator",
            "why": why,
            "at": approved["withdrawn_at"],
            "sent_back": sent["id"],
        }
    ]
    assert rows(run, "SELECT ending FROM runs") == [{"ending": None}], "it isn't a success"
    assert summarise(run.folder)["approvals_withdrawn"] == kept
    # Read back into a ledger whose row doesn't say so, it's written again, and only once.
    run.ledger.change(
        "UPDATE decisions SET withdrawn_at=NULL, withdrawn_by=NULL, withdrawn_why=NULL, "
        "withdrawn_decision=NULL"
    )
    records.read_back(run.folder, run.ledger)
    assert rows(run, "SELECT * FROM decisions ORDER BY id")[0] == approved
    records.read_back(run.folder, run.ledger)
    assert rows(run, "SELECT * FROM decisions ORDER BY id") == decided
