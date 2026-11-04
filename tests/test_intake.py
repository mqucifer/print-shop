"""A run starts from the whole job and holds its stops, with no Docker (mqucifer/print-shop#36,
mqucifer/print-shop#38, and criterion 2 of mqucifer/print-shop#37).

The request's set form and every line covered, the breakdown before the needs, a fixed need held
to its own settings, and the request's stops, the plan first, each held until its ask has a pick.
Every request here is made up for the test, a shelf bracket and a desk lamp, so nothing in it is
stained glass, and a scripted model drives it. A bench command is answered by a box that runs
nothing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import picture as picture_module
from print_shop.bench import usage as usage_module
from print_shop.rounds import breakdown, needs, prompt, request, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Refused, Run, what_next
from print_shop.rounds.session import Ending
from tests.rounds_fakes import Scripted, tool

BRACKET = """\
A wall bracket for a small shelf, in PETG. It should look plain and sturdy.

## Requirements
- It holds a 6 kg shelf without bending.
- Two screw holes, 4.5 mm across, for the wall.
- It should look plain, with no sharp corners.

## Non-negotiables
- The arm is 120 mm long, from the wall to its tip.
- The plate is at least 4 mm thick, measured
  after the countersinks are cut.

## Kind
bracket

## Stops
- the 2D profile of the arm
- one bracket printed as a test, before the pair
"""

LAMP = """\
A desk lamp's shade.

## Requirements
1. Light passes through the shade's sides.
2. It sits on the socket ring without glue.

## Stops
- no plan
"""

FREE = "A small block with a tab beside it. Keep a 0.5 mm gap between them."

BROKEN_DOWN = tool(
    "breakdown",
    parts=[
        {"name": "plate", "what": "flat against the wall, with the screw holes"},
        {"name": "arm", "what": "out from the plate, under the shelf"},
        {"name": "gusset", "what": "the brace between them"},
    ],
    joins=[
        {"parts": ["plate", "arm"], "how": "one body, the arm grows out of the plate's top"},
        {"parts": ["gusset", "plate", "arm"], "how": "fused to both along its two edges"},
    ],
    look=["one clean L from the side", "no edge sharp to the hand"],
    **{"from": "the request"},
)


#: What each command measures, as a need says it (mqucifer/print-shop#37).
MEASURES = {"info": "size", "thin": "printable", "clearance": "fit", "wall": "wall"}


def measured(check: dict[str, Any]) -> dict[str, Any]:
    return {"measures": MEASURES[check["command"].split()[0]], **check}


def bench(nid: str, command: str, **more: Any) -> dict[str, Any]:
    check = measured({"by": "bench", "command": command, **more.pop("check", {})})
    return {"id": nid, "what": f"what {nid} holds", "check": check, **more}


def covered(**changed: Any) -> list[dict[str, Any]]:
    """The bracket's needs, every line covered."""
    found = [
        bench(
            "holds",
            "info bracket.step",
            source="derived",
            covers=["R1"],
            check={"number": "volume_mesh_mm3", "at_least": 1},
        ),
        bench(
            "holes",
            "wall bracket.step --hole-x 10",
            source="request",
            quote="Two screw holes, 4.5 mm across",
            covers=["R2"],
        ),
        {
            "id": "plain",
            "what": "plain, with no sharp corners",
            "source": "request",
            "quote": "It should look plain",
            "covers": ["R3"],
            "check": {
                "by": "eye",
                "look_for": "whether it reads plain",
                "no_bench": "a look, which no bench command reads",
            },
        },
        bench(
            "arm",
            "info bracket.step",
            source="request",
            quote="The arm is 120 mm long",
            covers=["N1"],
            fixed=True,
            figure="the arm's length from the wall face to its tip, as drawn",
            check={"number": "bounds_mm.0.1", "at_least": 120, "at_most": 120.5},
        ),
        bench(
            "plate",
            "thin bracket.step --min 4",
            source="request",
            quote="at least 4 mm thick, measured after the countersinks are cut",
            covers=["N2"],
            fixed=True,
            figure="the plate's thinnest, after the countersinks",
        ),
    ]
    for need in found:
        need.update(changed.get(need["id"], {}))
    return [n for n in found if n.get("id") is not None]


class Box:
    """Runs nothing. Each bench command gets the next exit, with a record as the bench keeps one;
    `usage` reads every command as well formed, and `picture` hands back the file as it is."""

    def __init__(self, work: Path, *codes: int) -> None:
        self.work = work
        self.codes = list(codes)
        self.asked: list[list[str]] = []

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        return Ran(0, "", 0.1)

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        self.asked.append(args)
        n = len(self.asked)
        code = self.codes.pop(0) if self.codes else 0
        verdict = {0: "OK", 2: "NO ANSWER"}.get(code, "FAIL")
        why = f"try {n}, {' '.join(args[1:])}"
        out = (where or self.work) / "bench-out"
        out.mkdir(exist_ok=True)
        (out / f"{n}.json").write_text(json.dumps({"verdict": verdict, "why": why}))
        return Ran(code, f"verdict: {verdict}: {why}\nsaved: bench-out/{n}.txt and .json\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "usage":
            return Ran(0, usage_module.MARK + json.dumps(["" for _ in args]), 0.1)
        if module == "picture":
            source, target = args
            (self.work / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.work / source, self.work / target)
            said = {"size": [8, 8], "shown": [8, 8], "out": target}
            return Ran(0, picture_module.MARK + json.dumps(said), 0.1)
        raise AssertionError(module)


def boxed(run: Run, *codes: int) -> Run:
    box: Any = Box(run.work, *codes)
    run.box = box
    run.tools.box = box
    return run


def begin(root: Path, text: str, *turns: Any, codes: tuple[int, ...] = ()) -> Run:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", text, [], model, sleep=lambda _: None)
    return boxed(run, *codes)


def again(run: Run, *turns: Any, codes: tuple[int, ...] = ()) -> Run:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    return boxed(Run.load(run.folder, model, sleep=lambda _: None), *codes)


def told(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def pick(run: Run, n: int, chosen: str, by: str = "sponsor") -> None:
    found = {"pick": chosen, "reason": "As I'd hoped.", "by": by}
    (run.folder / "asks" / str(n) / "pick.json").write_text(json.dumps(found))


def picture(run: Run, name: str = "profile.png") -> None:
    (run.work / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())


# The request's form


def test_a_request_in_the_set_form_gives_each_line_its_id() -> None:
    form = request.read(BRACKET)
    assert [(x.id, x.text) for x in form.lines()] == [
        ("R1", "It holds a 6 kg shelf without bending."),
        ("R2", "Two screw holes, 4.5 mm across, for the wall."),
        ("R3", "It should look plain, with no sharp corners."),
        ("N1", "The arm is 120 mm long, from the wall to its tip."),
        ("N2", "The plate is at least 4 mm thick, measured after the countersinks are cut."),
    ]
    assert form.kind == "bracket"
    assert [(s.id, s.text) for s in form.stops] == [
        ("plan", request.PLAN_WHAT),
        ("S1", "the 2D profile of the arm"),
        ("S2", "one bracket printed as a test, before the pair"),
    ]


def test_a_numbered_list_reads_the_same_and_no_plan_leaves_the_plan_out() -> None:
    form = request.read(LAMP)
    assert [x.id for x in form.requirements] == ["R1", "R2"]
    assert form.requirements[1].text == "It sits on the socket ring without glue."
    assert form.non_negotiables == [] and form.kind is None
    assert form.stops == [], "the request said `no plan`, and named no other stop"


def test_a_request_in_the_old_free_form_has_no_lines_and_the_plan_as_its_stop() -> None:
    form = request.read(FREE)
    assert form.lines() == [] and form.kind is None
    assert [s.id for s in form.stops] == ["plan"]
    assert "no line is held to a need" in request.said(form)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("## Requirements\n\n## Stops\n- a sketch", "`## Requirements` has no lines"),
        ("## Stops\n- a\n## Stops\n- b", "`## Stops` twice"),
        ("## Kind\nA Lamp", "`## Kind` is one line, one word"),
        ("## Kind\n- lamp\n- shade", "`## Kind` is one line, one word"),
    ],
)
def test_a_request_whose_form_is_wrong_is_refused_before_anything_is_made(
    tmp_path: Path, text: str, said: str
) -> None:
    with pytest.raises(Refused, match=said):
        Run.start(tmp_path, "r", text, [], Scripted())
    assert not (tmp_path / "r").exists()


def test_the_first_message_names_each_line_and_stop_and_the_ledger_keeps_them(
    tmp_path: Path,
) -> None:
    model = Scripted([tool("blocked", reason="Just looking.")])
    run = Run.start(tmp_path, "r", BRACKET, [], model, sleep=lambda _: None)
    assert run.drive().name == "blocked"
    first = model.bodies[0]["messages"][1]["content"]
    assert "- `N2`: The plate is at least 4 mm thick" in first
    assert "Its kind: `bracket`." in first
    assert "- `S1`: the 2D profile of the arm" in first
    assert first.index("- `plan`:") < first.index("- `S1`:")
    assert "`breakdown`" in model.bodies[0]["messages"][0]["content"]
    rows = run.ledger.rows("SELECT line, section, text FROM request_lines ORDER BY seq")
    assert [(r["line"], r["section"]) for r in rows] == [
        ("R1", "requirement"),
        ("R2", "requirement"),
        ("R3", "requirement"),
        ("N1", "non_negotiable"),
        ("N2", "non_negotiable"),
        ("plan", "stop"),
        ("S1", "stop"),
        ("S2", "stop"),
        ("kind", "kind"),
    ]


# The breakdown, before the needs


def test_a_breakdown_is_held_to_its_shape_not_its_words() -> None:
    _, wrong = breakdown.read(
        {
            "parts": [{"name": "shade", "what": "the cone"}, {"name": "ring", "what": "seat"}],
            "joins": [{"parts": ["shade", "socket"], "how": "it sits on it"}],
            "look": [],
        }
    )
    assert "a join names `socket`, which isn't one of `parts`" in wrong
    assert any(w.startswith("`ring` is in no join") for w in wrong), "a part held by nothing"
    assert "`look` says what the look rests on, a line each" in wrong
    found, wrong = breakdown.read(
        {"parts": [{"name": "shade", "what": "one piece"}], "look": "a soft glow"}
    )
    assert wrong == [] and found["joins"] == [], "one part needs no join"


def test_the_needs_wait_for_the_breakdown_which_is_kept_for_the_model_and_the_ledger(
    tmp_path: Path,
) -> None:
    run = begin(
        tmp_path,
        BRACKET,
        [tool("needs", needs=covered(), **{"try": True})],
        [tool("needs", needs=covered())],
        [BROKEN_DOWN],
        [tool("needs", needs=covered())],
    )
    assert run.drive().name == "blocked"
    replies = told(run)
    assert replies[0]["outcome"] == "ok", "a try settles nothing, so it needs no breakdown"
    assert replies[1]["outcome"] == "failed" and replies[1]["why"] == "no_breakdown"
    assert replies[2]["outcome"] == "ok" and replies[3]["outcome"] == "ok"
    kept = (run.work / "record" / "breakdown.md").read_text()
    assert "- gusset, plate, arm: fused to both along its two edges" in kept
    body = json.loads(run.ledger.rows("SELECT body FROM breakdowns")[0]["body"])
    assert body["look"] == ["one clean L from the side", "no edge sharp to the hand"]


# Every line covered, the non-negotiables fixed


def test_the_needs_are_refused_naming_each_line_left_over(tmp_path: Path) -> None:
    run = begin(
        tmp_path,
        BRACKET,
        [BROKEN_DOWN],
        [tool("needs", needs=covered(holes={"id": None}, plate={"covers": ["N1"]}))],
    )
    run.drive()
    reply = told(run)[1]["reply"]
    assert reply.startswith("The needs weren't taken:")
    assert '- `R2` isn\'t covered: "Two screw holes, 4.5 mm across, for the wall."' in reply
    assert "- `N2` isn't covered: \"The plate is at least 4 mm thick" in reply
    assert "R1" not in reply.replace("R2", ""), "a covered line isn't named"


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"holds": {"covers": ["R9"]}}, "covers `R9`, which isn't a line of the request"),
        ({"arm": {"fixed": False}}, "covers `N1`, a non-negotiable: a need that covers one is"),
        ({"arm": {"quote": "It holds a 6 kg shelf"}}, 'its quote, "It holds a 6 kg shelf", isn'),
        ({"plate": {"figure": ""}}, "covers `N2`, which holds a figure"),
        ({"plain": {"check": {"by": "eye", "look_for": "plain"}}}, "Say why none does in"),
        ({"holds": {"fixed": True}}, "is `fixed`, which is for the request's non-negotiables"),
        ({"plate": {"source": "derived", "quote": ""}}, "its `source` is `request`"),
    ],
)
def test_a_needs_ties_to_the_request_are_held(changed: dict[str, Any], said: str) -> None:
    found, wrong = needs.read(covered(**changed), BRACKET)
    assert wrong == []
    assert any(said in w for w in request.held(found, request.read(BRACKET))), said


def test_the_figure_is_said_with_what_the_request_leaves_open() -> None:
    found, _ = needs.read(covered(plate={"figure": ""}), BRACKET)
    wrong = request.held(found, request.read(BRACKET))
    assert any(
        "such as before or after play, which. If the request doesn't say, ask with "
        "`questions` before settling" in w
        for w in wrong
    )


def test_needs_that_cover_every_line_are_settled_with_their_marks(tmp_path: Path) -> None:
    run = begin(tmp_path, BRACKET, [BROKEN_DOWN], [tool("needs", needs=covered())])
    run.drive()
    assert told(run)[1]["outcome"] == "ok"
    marks = {
        r["need"]: (json.loads(r["covers"]), r["fixed"], r["figure"])
        for r in run.ledger.rows("SELECT * FROM need_marks")
    }
    assert marks["plate"] == (["N2"], 1, "the plate's thinnest, after the countersinks")
    assert marks["holds"] == (["R1"], 0, None)
    settled = json.loads((run.folder / "needs.json").read_text())
    assert "fixed" not in settled[0] and settled[3]["fixed"] is True


def test_a_request_in_the_old_form_still_runs_with_nothing_checked_for_coverage(
    tmp_path: Path,
) -> None:
    gap = bench("gap", "clearance block.step tab.step", source="request", quote="a 0.5 mm gap")
    run = begin(tmp_path, FREE, [BROKEN_DOWN], [tool("needs", needs=[gap])])
    run.drive()
    assert told(run)[1]["outcome"] == "ok"


def test_a_run_from_before_the_form_carries_on_as_it_began(tmp_path: Path) -> None:
    run = begin(tmp_path, BRACKET)
    run.drive()
    run.state.form = None
    run.save()
    run = again(run, [tool("needs", needs=covered(holes={"id": None}))])
    run.resume()
    run.drive()
    assert told(run)[-2]["outcome"] == "ok", "no breakdown asked for, no lines held"
    assert run._open_stop() is None


# A fixed need counts only at its own settings


def test_a_fixed_needs_check_at_other_settings_is_not_the_requirement_from_the_first_try(
    tmp_path: Path,
) -> None:
    run = begin(
        tmp_path,
        BRACKET,
        [BROKEN_DOWN],
        [tool("needs", needs=covered())],
        *[[tool("bench", command=f"thin bracket.step --min {m}")] for m in ("3", "4", "2.5")],
        [tool("bench", command="info bracket.step")],
        codes=(0, 1, 0, 0),
    )
    run.drive()
    looser, own, again_looser, other = told(run)[2:6]
    assert looser["reply"].startswith(
        'exit 0\nNOT THE REQUIREMENT: `plate` is fixed, a non-negotiable of the request, "at '
        'least 4 mm thick, measured after the countersinks are cut", and its check is `thin '
        "bracket.step --min 4`. This try has `--min 3`, the need says `4`. Whatever it reads "
        "below, it isn't the requirement and can't pass it"
    )
    assert looser["reply"].index("NOT THE REQUIREMENT") < looser["reply"].index("verdict: OK")
    assert looser["detail"]["not_the_requirement"] == ["plate"]
    assert "NOT THE REQUIREMENT" not in own["reply"], "its own settings"
    assert "not_the_requirement" in again_looser["detail"], "each try, not only after four"
    assert "NOT THE REQUIREMENT" not in other["reply"], "`arm`'s own command"


def test_a_pass_at_a_fixed_needs_other_settings_doesnt_start_the_count_again(
    tmp_path: Path,
) -> None:
    run = begin(
        tmp_path,
        BRACKET,
        [BROKEN_DOWN],
        [tool("needs", needs=covered())],
        *[[tool("bench", command="thin bracket.step --min 4")] for _ in range(3)],
        [tool("bench", command="thin bracket.step --min 1")],
        [tool("bench", command="thin bracket.step --min 4")],
        codes=(1, 1, 1, 0, 1),
    )
    run.drive()
    last = told(run)[6]
    assert "has failed 4 times without passing as a need checks it" in last["reply"]
    assert "OK, but not as a need checks it" in last["reply"]


def test_a_fixed_need_changes_only_by_the_sponsor_with_a_note(tmp_path: Path) -> None:
    run = begin(tmp_path, BRACKET, [BROKEN_DOWN], [tool("needs", needs=covered())])
    run.drive()
    looser = covered(
        plate={"check": measured({"by": "bench", "command": "thin bracket.step --min 3"})}
    )
    run = again(run, [tool("needs", needs=looser)])
    run.resume("Look at the plate again.", reopen_needs=True)
    run.drive()
    refused = told(run)[-2]
    assert refused["outcome"] == "failed"
    assert (
        "`plate` is fixed, a non-negotiable of the request, and changes only by the Sponsor, "
        "through `resume --reopen-needs` with their note. Send it as it was: `thin bracket.step "
        "--min 4` (exit 0)"
    ) in refused["reply"]
    reopened = [
        e for e in transcript.events_of(run.session.events) if e.get("relay") == "reopen_needs"
    ]
    assert "The fixed needs, `arm`, `plate`, stay as they are" in reopened[-1]["message"]
    run = again(run, [tool("needs", needs=looser)])
    run.resume("3 mm will do for the plate.", reopen_needs=True, note_from="sponsor")
    run.drive()
    assert told(run)[-2]["outcome"] == "ok", "the Sponsor's note opened the fixed needs"
    assert run.state.fixed_open is None, "for that one settling only"
    assert "--min 3" in run.state.needs[4]["command"]


# The stops, the plan first


def to_the_plan(tmp_path: Path, *after: Any) -> Run:
    """The bracket's run, its breakdown and needs settled, then whatever comes after."""
    run = begin(tmp_path, BRACKET, [BROKEN_DOWN], [tool("needs", needs=covered())], *after)
    picture(run)
    return run


PLAN_ASK = tool(
    "ask",
    question="Is this how to go about it?",
    stop="plan",
    plan="Profile in 2D first, then one bracket, then the pair.",
    pictures=["profile.png"],
)
SENT = [tool("send_ask", says="My 2D profile: an L with its brace, in black on white.")]
DONE = tool("done", script="make.py", part=["bracket.step"], fix_shape="first", closing="Here.")


def test_the_plan_needs_no_part_file_and_holds_done_and_the_next_stop(tmp_path: Path) -> None:
    run = to_the_plan(
        tmp_path,
        [tool("ask", question="Profile?", stop="S1", pictures=["profile.png"])],
        [tool("ask", question="Plan?", stop="plan", pictures=["profile.png"])],
        [DONE],
        [tool("write", path="make.py", content="print('still working')\n")],
        [PLAN_ASK],
        SENT,
    )
    assert run.drive().name == "ask"
    replies = told(run)
    skipped, no_plan, held, wrote, drawn, sent = replies[2:8]
    assert skipped["why"] == "stop_open"
    assert "The stop `plan`, the plan: your breakdown, the needs" in skipped["reply"]
    assert "until its `ask` has a pick, `S1` waits" in skipped["reply"]
    assert no_plan["why"] == "no_plan"
    assert held["why"] == "stop_open" and "`done` waits for the stop `plan`" in held["reply"]
    assert wrote["outcome"] == "ok", "writing stays open at a stop"
    assert drawn["outcome"] == "ok" and drawn["detail"]["stop"] == "plan"
    assert "Your pictures go as they are: `profile.png`." in drawn["reply"]
    assert sent["outcome"] == "ok"
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert question.startswith("# Stop `plan`: the plan:")
    assert "Profile in 2D first, then one bracket, then the pair." in question
    assert "- **plate**: flat against the wall, with the screw holes" in question
    assert "- **gusset**: the brace between them" in question
    assert "- **fixed** **plate** (what plate holds): covers N2." in question
    assert "  - its figure is read as: the plate's thinnest, after the countersinks" in question
    assert "- `N1`, covered by arm: The arm is 120 mm long, from the wall to its tip." in question
    assert "- `N1`: 120 isn't in the check" not in question, "the limit carries it"
    assert 'the pick is "yes" to go on from this' in question
    assert (run.folder / "asks" / "1" / "pictures" / "profile.png").is_file()
    assert not (run.folder / "asks" / "1" / "sheet.png").exists()
    said = what_next(run, Ending("ask", {"n": run.state.asks}))
    assert "the stop `plan`" in said and '"pick": "yes or none"' in said


def test_a_figure_no_fixed_need_carries_is_said_to_the_person(tmp_path: Path) -> None:
    run = begin(
        tmp_path,
        BRACKET,
        [BROKEN_DOWN],
        [
            tool(
                "needs",
                needs=covered(arm={"check": measured({"by": "bench", "command": "info x.step"})}),
            )
        ],
        [PLAN_ASK],
        SENT,
    )
    picture(run)
    assert run.drive().name == "ask"
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "- `N1`: 120 isn't in the check of a fixed need that covers it" in question


def test_a_pick_passes_a_stop_none_leaves_it_open_and_the_record_says_who_and_when(
    tmp_path: Path,
) -> None:
    run = to_the_plan(tmp_path, [PLAN_ASK], SENT)
    assert run.drive().name == "ask"
    pick(run, 1, "yes", by="stand-in: a builder in a test")
    run = again(
        run,
        [tool("ask", question="Is the profile right?", stop="S1", pictures=["profile.png"])],
        SENT,
    )
    run.resume()
    assert run.drive().name == "ask"
    passed = [e for e in transcript.events_of(run.session.events) if e.get("relay") == "pick"]
    assert passed[0]["message"].endswith(
        "The stop `plan`, the plan: your breakdown, the needs, and how you'll go about it, is "
        "passed. The next is `S1`, the 2D profile of the arm."
    )
    pick(run, 2, "none")
    run = again(
        run,
        [DONE],
        [tool("ask", question="Does the shelf look right?", pictures=["profile.png"])],
        SENT,
    )
    run.resume()
    assert run.drive().name == "ask"
    held, other = told(run)[-3:-1]
    assert held["why"] == "stop_open" and "`done` waits for the stop `S1`" in held["reply"]
    assert other["outcome"] == "ok", "an ask that isn't a stop works at any point"
    assert "The stop `S1`, the 2D profile of the arm, is still open" in other["reply"]
    stops = [dict(r) for r in run.ledger.rows("SELECT * FROM stops ORDER BY ask")]
    assert [(s["stop"], s["what"], s["pick"], s["picked_by"]) for s in stops] == [
        ("plan", request.PLAN_WHAT, "yes", "stand-in: a builder in a test"),
        ("S1", "the 2D profile of the arm", "none", "sponsor"),
    ]
    assert all(s["at_call"] and s["picked_at"] for s in stops)
    assert set(run.state.stops) == {"plan"}
    assert run.state.stops["plan"]["by"] == "stand-in: a builder in a test"


def test_a_pick_of_a_variant_that_isnt_there_is_refused_at_a_pictures_ask(tmp_path: Path) -> None:
    run = to_the_plan(tmp_path, [PLAN_ASK], SENT)
    run.drive()
    pick(run, 1, "A")
    with pytest.raises(Refused, match="the pick is one of yes, or none"):
        again(run).resume()


def test_a_request_with_no_plan_holds_only_its_own_stops(tmp_path: Path) -> None:
    lamp = bench("glow", "info shade.step", source="derived", covers=["R1", "R2"])
    run = begin(tmp_path, LAMP, [BROKEN_DOWN], [tool("needs", needs=[lamp])])
    run.drive()
    assert run._open_stop() is None
    assert "It names no stops." in run._start_text()


def test_the_prompt_says_the_stops_hold_what_is_handed_over_not_the_work() -> None:
    assert "Writing, running, the bench and any other `ask` stay open throughout" in prompt.SYSTEM
    assert "it needs no part file" in prompt.SYSTEM


def test_the_requests_kind_picks_the_lessons_for_that_kind_of_part(tmp_path: Path) -> None:
    from print_shop.rounds import lessons
    from tests.test_lessons import BY, lesson
    from tests.test_loop import written

    ledger, (general, bracket) = written(
        tmp_path, lesson("Name each height once."), lesson("Fillet the arm's root.")
    )
    for n in (general, bracket):
        lessons.accept(ledger, n, BY)
    lessons.edit(ledger, bracket, BY, part_kind="bracket", why="only for brackets")
    for name, text in (("bracket", BRACKET), ("lamp", LAMP)):
        Run.start(tmp_path, name, text, [], Scripted(), sleep=lambda _: None)
    given = {
        name: [r["lesson"] for r in ledger.rows("SELECT lesson FROM run_lessons WHERE run=?", name)]
        for name in ("bracket", "lamp")
    }
    assert given == {"bracket": [general, bracket], "lamp": [general]}
