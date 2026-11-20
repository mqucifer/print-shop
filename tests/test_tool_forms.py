"""Each tool states its form the first time (mqucifer/print-shop#191).

The largest group of accepted lessons not yet delivered was runs spending calls learning a tool's
form from its refusals. For each form the lessons name, the first call made as the tool's
description says is taken, and each wrong shape the lessons name is refused naming the right one,
with an example to copy. A form an earlier change already delivered is held here too, with the
issue that delivered it. No Docker: a scripted model drives each run, a box answers each bench
command and runs nothing, and the bench's own arguments are read by its parser. Every request,
part and file here is made up for the test: a shelf bracket, and a lamp's shade on its ring.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import cli
from print_shop.rounds import breakdown, facts, needs, tools, toolsets
from print_shop.rounds.run import CLOSE_VIEWS, Run
from tests.rounds_fakes import Scripted, flat, tool
from tests.test_friction import Drawing
from tests.test_intake import (
    BRACKET,
    BROKEN_DOWN,
    LAMP,
    PLAN_ASK,
    begin,
    boxed,
    covered,
    measured,
    picture,
    told,
)

#: Each toolset's whole tool list on main before this, as the model is sent it, in characters:
#: each form is said within it, and where it wouldn't fit, the refusal carries it.
SPECS_BEFORE = {
    "every": 25966,
    "core": 27681,
    "stained-glass": 29373,
    "enclosure": 27843,
    "furniture": 28210,
}
TOOLSETS: dict[str, toolsets.Toolset] = {
    "every": toolsets.EVERY,
    "core": toolsets.CORE,
    **{str(kind): one for kind, one in toolsets.KINDS.items()},
}


def spec(name: str, toolset: toolsets.Toolset = toolsets.CORE) -> dict[str, Any]:
    found: dict[str, Any] = next(
        s["function"] for s in tools.specs(toolset) if s["function"]["name"] == name
    )
    return found


def argument(tool_name: str, name: str) -> str:
    return str(spec(tool_name)["parameters"]["properties"][name]["description"])


@pytest.mark.parametrize("name", sorted(TOOLSETS))
def test_the_tools_say_their_forms_within_what_they_were(name: str) -> None:
    assert len(json.dumps(tools.specs(TOOLSETS[name]))) <= SPECS_BEFORE[name]


def test_each_form_is_said_where_the_model_reads_it() -> None:
    assert "without ( ) / or :" in argument("breakdown", "parts")
    need = spec("needs")["parameters"]["properties"]["needs"]["items"]["properties"]
    assert "`covers` has its N-id, `quote` its line word for word" in need["fixed"]["description"]
    assert set(need["check"]["properties"]) >= {"at_least", "at_most"}, "limits inside `check`"
    assert argument("done", "part").startswith(
        "A flat list of the files you made to print, not the given references"
    )
    assert "named without spaces" in argument("done", "groups")
    assert argument("ask", "plan").startswith("Required at the `plan` stop")
    assert "not in `question`" in argument("ask", "plan")
    assert "named by a letter" in argument("ask", "variants")
    assert "They go with `variants`, the part's own files" in argument("ask", "need")
    looks = argument("ask", "looks")
    assert "a label under 32 characters" in looks and "none for pictures alone" in looks
    assert "`X,Y,Z,R` or `X,Y,Z,R,VIEW`" in argument("ask", "close")
    assert "signed: +x, -x, +y, -y, +z or -z" in argument("fact", "way_in")
    assert "can't" not in argument("fact", "fixed") and "in place of `way_in`" in argument(
        "fact", "fixed"
    )
    assert "Words only" in argument("restate", "descriptions")
    command = argument("bench", "command")
    assert "--slide +z`, a way signed" in command and "--axis z`, an axis not" in command


# `needs`


def needs_reply(tmp_path: Path, **changed: Any) -> dict[str, Any]:
    """The bracket's needs, every line covered, with `changed`: what `needs` said."""
    run = begin(tmp_path, BRACKET, [BROKEN_DOWN], [tool("needs", needs=covered(**changed))])
    run.drive()
    return told(run)[1]


def test_needs_as_the_schema_says_are_settled(tmp_path: Path) -> None:
    said = needs_reply(tmp_path)
    assert said["outcome"] == "ok", said["reply"]


def test_a_fixed_need_quoting_the_requirement_is_given_its_non_negotiables_line(
    tmp_path: Path,
) -> None:
    said = needs_reply(tmp_path, arm={"quote": "It holds a 6 kg shelf"})
    assert said["why"] == "misses"
    assert (
        "the need 'arm' is fixed, and its quote, \"It holds a 6 kg shelf\", isn't from `N1`: "
        "that's `R1`'s, a requirement. A fixed need quotes its non-negotiable's line word for "
        'word, as `"quote": "The arm is 120 mm long, from the wall to its tip."`'
    ) in said["reply"]


def test_a_fixed_need_without_its_n_id_is_told_which_to_cover(tmp_path: Path) -> None:
    said = needs_reply(tmp_path, arm={"covers": []})
    assert said["why"] == "misses"
    assert (
        "the need 'arm' is `fixed`, and quotes `N1`, a non-negotiable: name it in `covers`, as "
        '`"covers": ["N1"]`'
    ) in said["reply"]


def test_a_fixed_need_quoting_no_line_is_shown_the_covers_form(tmp_path: Path) -> None:
    said = needs_reply(tmp_path, arm={"covers": [], "quote": "It should look plain"})
    assert (
        "it names the one it covers in `covers`, as `\"covers\": [\"N1\"]`, and quotes its "
        "line. They are: `N1`, `N2`"
    ) in said["reply"]


def test_a_fixed_need_without_its_figure_is_told_how_to_say_it(tmp_path: Path) -> None:
    """Delivered by mqucifer/print-shop#36: held here so it stays so."""
    said = needs_reply(tmp_path, arm={"figure": ""})
    assert "Say in `figure` how it's read" in said["reply"]


def test_a_limit_beside_check_is_told_it_goes_inside(tmp_path: Path) -> None:
    """Delivered by mqucifer/print-shop#145."""
    check = measured({"by": "bench", "command": "info bracket.step", "number": "bounds_mm.0.1"})
    said = needs_reply(tmp_path, arm={"check": check, "at_least": 120, "at_most": 120})
    assert (
        "the need 'arm': `at_least` and `at_most` go inside `check`, beside `by`, not beside "
        f"`check`, as {needs.LIMIT_SHAPE}"
    ) in said["reply"]


EDGES = "A box for a sensor.\n\nIts outside edges are rounded to 1.5 mm, so it's soft in the hand."


def edges_need(quote: str) -> dict[str, Any]:
    return {
        "id": "soft",
        "what": "every outside edge rounded",
        "source": "request",
        "quote": quote,
        "check": {
            "by": "bench",
            "measures": "edges",
            "command": "edges box.step --min-radius 1.5",
        },
    }


def test_a_checks_figure_missing_from_its_quote_is_given_the_words_that_hold_it() -> None:
    ok, wrong = needs.read([edges_need("rounded to 1.5 mm")], EDGES, toolset=toolsets.ENCLOSURE)
    assert wrong == [] and ok[0].quote == "rounded to 1.5 mm"
    _, wrong = needs.read([edges_need("rounded")], EDGES, toolset=toolsets.ENCLOSURE)
    assert wrong == [
        "the need 'soft': its limit 1.5 isn't in its quote. A `edges` limit is the request's: "
        "quote the words that give it, such as `\"quote\": \"Its outside edges are rounded to "
        "1.5 mm, so it's soft in the hand.\"`, or ask for it with `questions`"
    ]
    suggested = re.search(r'"quote": "([^"]+)"', wrong[0])
    assert suggested is not None
    ok, wrong = needs.read([edges_need(suggested[1])], EDGES, toolset=toolsets.ENCLOSURE)
    assert wrong == [], "the quote offered is taken"


# `done`

LAMP_BROKEN = tool(
    "breakdown",
    parts=[{"name": "shade", "what": "the shade"}, {"name": "ring", "what": "the socket ring"}],
    joins=[{"parts": ["shade", "ring"], "how": "the shade sits on the ring"}],
    look=["light through the sides"],
)
GLOW = {
    "id": "glow",
    "what": "an even glow",
    "source": "chosen",
    "check": {"by": "eye", "look_for": "whether the glow is even"},
}


def lamp_needs() -> list[dict[str, Any]]:
    return [
        {
            "id": "light",
            "what": "light passes through the sides",
            "source": "request",
            "quote": "Light passes through the shade's sides",
            "covers": ["R1"],
            "check": {"by": "bench", "measures": "size", "command": "info shade.step"},
        },
        {
            "id": "sits",
            "what": "it sits on the ring",
            "source": "request",
            "quote": "It sits on the socket ring without glue",
            "covers": ["R2"],
            "check": {
                "by": "bench",
                "measures": "fit",
                "command": "clearance ring.step shade.step",
            },
        },
        GLOW,
    ]


def lamp(root: Path, *turns: Any, given: tuple[str, ...] = (), drawing: bool = False) -> Run:
    """The lamp's run, its breakdown and needs settled, its files made, then `turns`."""
    refs = []
    for name in given:
        (root / name).write_text("ISO-10303-21; made up for the test\n")
        refs.append(root / name)
    model = Scripted(
        [LAMP_BROKEN],
        [tool("needs", needs=lamp_needs())],
        *turns,
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(root / "runs", "r", LAMP, refs, model, sleep=lambda _: None)
    if drawing:
        box: Any = Drawing(run.work)
        run.box = box
        run.tools.box = box
    else:
        boxed(run)
    for name in ("shade.step", "ring.step"):
        (run.work / name).write_text("ISO-10303-21; made up for the test\n")
    (run.work / "make.py").write_text("print('the lamp')\n")
    picture(run)
    return run


def third(root: Path, call: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """What the lamp's run said to `call`, after its breakdown and needs."""
    run = lamp(root, [call], **kw)
    run.drive()
    return told(run)[2]


ENDING = {"script": "make.py", "fix_shape": "first", "closing": "Here."}
FORMS = (
    "no_part",
    "not_geometry",
    "outside",
    "not_a_group",
    "not_on_bed",
    "not_a_look",
    "json_text",
    "unknown",
)


def test_done_as_its_description_says_is_taken_and_checked(tmp_path: Path) -> None:
    said = third(
        tmp_path,
        tool(
            "done",
            part=["shade.step", "ring.step"],
            on_bed=flat("shade.step", "ring.step"),
            groups=[{"name": "both", "files": ["shade.step", "ring.step"]}],
            **ENDING,
        ),
    )
    assert said["why"] not in FORMS, said["reply"]
    assert said["reply"].startswith("Round 1"), "it went on to the checks"


def test_a_given_reference_in_part_is_refused_with_the_files_made(tmp_path: Path) -> None:
    said = third(
        tmp_path,
        tool("done", part=["references/bulb.step", "shade.step", "ring.step"], **ENDING),
        given=("bulb.step",),
    )
    assert said["why"] == "no_part"
    assert said["reply"] == (
        "`part` names `references/bulb.step`, given to you, which isn't printed: a need's check "
        "may still read it. `part` is a flat list of the files you made to print, without the "
        'given references: `["ring.step", "shade.step"]`. Files seen together may be one entry, '
        '{"name": "wide", "files": ["base.step", "lid.step"]}.'
    )


def test_part_as_a_nested_list_is_told_its_flat(tmp_path: Path) -> None:
    said = third(tmp_path, tool("done", part=[["shade.step", "ring.step"]], **ENDING))
    assert said["why"] == "no_part"
    assert said["reply"].startswith("`part` is a flat list of the files you made to print")
    assert '`["ring.step", "shade.step"]`' in said["reply"]


def test_on_bed_and_groups_naming_files_outside_part_are_refused(tmp_path: Path) -> None:
    """Delivered by mqucifer/print-shop#66 (`on_bed`) and mqucifer/print-shop#26 (`groups`)."""
    on_bed = third(
        tmp_path / "a", tool("done", part=["shade.step"], on_bed={"ring.step": "-z"}, **ENDING)
    )
    assert on_bed["why"] == "not_on_bed"
    assert "`on_bed` names `ring.step`, which isn't a file of `part` (`shade.step`)" in (
        on_bed["reply"]
    )
    groups = [{"name": "a", "files": ["ring.step"]}]
    grouped = third(tmp_path / "b", tool("done", part=["shade.step"], groups=groups, **ENDING))
    assert grouped["why"] == "not_a_group"
    assert "The group `a` names `ring.step`, which isn't in `part`." in grouped["reply"]


@pytest.mark.parametrize(
    ("groups", "said"),
    [
        (
            [{"name": "the shade", "files": ["shade.step"]}],
            "`the shade` won't do as a group's name: a group's name is short, of letters, "
            "digits, `-` or `_`, with no spaces, such as `the-shade`.",
        ),
        ([{"name": "a", "parts": ["shade.step"]}], '{"name": <short, no spaces>, "files": [...]}'),
        ([["shade.step"]], '{"name": <short, no spaces>, "files": [...]}'),
    ],
)
def test_a_group_of_the_wrong_shape_is_shown_the_right_one(
    tmp_path: Path, groups: list[Any], said: str
) -> None:
    refused = third(tmp_path, tool("done", part=["shade.step"], groups=groups, **ENDING))
    assert refused["why"] == "not_a_group"
    assert said in refused["reply"]


# `ask`


def test_the_plan_stop_without_its_plan_is_shown_where_the_method_goes(tmp_path: Path) -> None:
    method = "Plan: profile in 2D first, then one bracket, then the pair."
    run = begin(
        tmp_path,
        BRACKET,
        [BROKEN_DOWN],
        [tool("needs", needs=covered())],
        [tool("ask", question=method, stop="plan", pictures=["profile.png"])],
        [PLAN_ASK],
    )
    picture(run)
    assert run.drive().name == "blocked"
    refused, drawn = told(run)[2:4]
    assert refused["why"] == "no_plan"
    assert (
        '{"stop": "plan", "plan": "Profile in 2D first, then one test piece, then the whole.", '
        '"question": "Is this how to go about it?"}. Your method goes in `plan`'
    ) in refused["reply"]
    assert drawn["outcome"] == "ok", "the plan stop as its description says is drawn"


def test_a_variants_long_name_is_told_to_be_a_letter(tmp_path: Path) -> None:
    variants = [{"name": "wide frame", "files": ["shade.step"]}]
    said = third(tmp_path, tool("ask", question="Which?", variants=variants))
    assert said["why"] == "not_a_variant"
    assert said["reply"].startswith(
        "`wide frame` won't do as a variant's name: it's a short name, a letter such as `A`, "
        "and what sets it apart goes in `about`."
    )


def test_an_eye_need_on_pictures_is_shown_the_variants_to_send(tmp_path: Path) -> None:
    """Refused since mqucifer/print-shop#59; the refusal now has the call to copy."""
    said = third(tmp_path, tool("ask", question="Even?", need=["glow"], pictures=["profile.png"]))
    assert said["why"] == "eye_on_picture"
    assert (
        '"variants": [{"name": "A", "files": ["ring.step", "shade.step"]}], "need": ["glow"].'
    ) in said["reply"]


def test_looks_on_a_sheet_of_pictures_alone_is_told_to_leave_it_out(tmp_path: Path) -> None:
    looks = {"shade.step": {"colour": "blue", "label": "shade"}}
    asked = tool("ask", question="Like this?", pictures=["profile.png"], looks=looks)
    said = third(tmp_path, asked)
    assert said["why"] == "not_a_look"
    assert "this one draws none of yours, only your pictures: leave `looks` out" in said["reply"]


def test_an_ask_as_its_description_says_draws_its_sheet(tmp_path: Path) -> None:
    said = third(
        tmp_path,
        tool(
            "ask",
            question="Which reads better?",
            variants=[{"name": "A", "files": ["shade.step", "ring.step"]}],
            need=["glow"],
            looks={"shade.step": {"colour": "amber", "label": "shade"}},
            close="0,0,5,4 0,0,5,4,front",
        ),
        drawing=True,
    )
    assert said["outcome"] == "ok", said["reply"]


@pytest.mark.parametrize(
    "close", ["10,10,5", "x=10,y=10,z=5,r=4", "10,10,5,4,underneath", "1,2,3,0"]
)
def test_a_close_up_of_the_wrong_shape_is_refused_before_the_sheet(
    tmp_path: Path, close: str
) -> None:
    variants = [{"name": "A", "files": ["shade.step"]}]
    run = lamp(tmp_path, [tool("ask", question="Close?", variants=variants, close=close)])
    run.drive()
    said = told(run)[2]
    assert said["why"] == "not_a_close"
    assert f"`{close}`" in said["reply"]
    assert '`"close": "12,0,3,4 20,5,3,2,front"`' in said["reply"]
    box: Any = run.box
    assert box.asked == [], "nothing was drawn"


def test_the_close_up_views_are_renders() -> None:
    """`ask` reads a close-up's view by `render`'s own names, which it can't import here."""
    source = (Path(cli.__file__).parent / "render.py").read_text()
    views = next(
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "VIEWS" for t in node.targets)
    )
    assert isinstance(views, ast.Dict)
    assert tuple(ast.literal_eval(k) for k in views.keys if k is not None) == CLOSE_VIEWS


@pytest.mark.parametrize(
    ("look", "said"),
    [
        (
            {"colour": "blue", "label": "the shade, frosted, seen from its side"},
            "a label is a few words, of at most 32 characters",
        ),
        (
            {"colour": "frosted white", "label": "shade"},
            "isn't a colour the sheet can draw: one of",
        ),
    ],
)
def test_a_long_label_or_a_colour_off_the_palette_is_refused(
    tmp_path: Path, look: dict[str, str], said: str
) -> None:
    """Delivered by mqucifer/print-shop#26."""
    variants = [{"name": "A", "files": ["shade.step"]}]
    refused = third(
        tmp_path, tool("ask", question="Which?", variants=variants, looks={"shade.step": look})
    )
    assert refused["why"] == "not_a_look" and said in refused["reply"]


# `breakdown`


def test_a_breakdown_as_described_is_kept() -> None:
    found, wrong = breakdown.read(BROKEN_DOWN["arguments"])
    assert wrong == [] and [p["name"] for p in found["parts"]] == ["plate", "arm", "gusset"]


@pytest.mark.parametrize(
    ("name", "copy"),
    [
        ("frame (leading)", "frame leading"),
        ("box (reference)", "box reference"),
        ("pane/1", "pane-1"),
        ("frame: outer", "frame outer"),
    ],
)
def test_a_part_name_with_brackets_slashes_or_colons_is_given_one_to_copy(
    name: str, copy: str
) -> None:
    _, wrong = breakdown.read(
        {
            "parts": [{"name": name, "what": "x"}, {"name": "pane", "what": "y"}],
            "joins": [{"parts": [name, "pane"], "how": "held"}],
            "look": ["a"],
        }
    )
    assert len(wrong) == 1
    assert wrong[0].startswith(
        "a part's `name` is short, of letters, digits, spaces, `.`, `_` and `-`, with no "
        f"parentheses, slashes or colons: not {name!r}"
    )
    assert wrong[0].endswith(f"name it `{copy}`")
    found, wrong = breakdown.read(
        {
            "parts": [{"name": copy, "what": "x"}, {"name": "pane", "what": "y"}],
            "joins": [{"parts": [copy, "pane"], "how": "held"}],
            "look": ["a"],
        }
    )
    assert wrong == [], "the name offered is taken"


def test_a_given_part_named_as_offered_still_reads_as_given() -> None:
    """`box reference` says it's given, as `box (reference)` did (mqucifer/print-shop#97)."""
    from print_shop.rounds import holds

    names = {"parts": [{"name": "lid"}, {"name": "box reference"}]}
    base = {"references/base.step"}
    assert holds.given_names(names, base) == {"box reference": base}


def test_joins_of_one_part_or_another_name_are_shown_what_a_join_is() -> None:
    """Delivered by mqucifer/print-shop#54 (one part, and a part's own property in its `what`)
    and mqucifer/print-shop#145 (a part named otherwise)."""
    _, wrong = breakdown.read(
        {
            "parts": [{"name": "frame", "what": "x"}, {"name": "pane", "what": "y"}],
            "joins": [
                {"parts": ["frame"], "how": "the frame is 3 mm thick"},
                {"parts": ["frames", "pane"], "how": "held"},
            ],
            "look": ["a"],
        }
    )
    assert wrong[0].startswith("a join names the two or more parts it holds together")
    assert "What a part is on its own goes in its `what`" in wrong[0]
    assert "so name it `frame` in the join, or `frames` in `parts`" in wrong[1]


# `fact`

FAN = {"name": "references/fan.step", "text": "the fan", "from": "call 3, bench info"}
REFS = {"references/fan.step"}


def test_a_bought_parts_fact_as_described_is_kept() -> None:
    said = facts.problem(
        {**FAN, "box": "x 0-30, y 0-30, z 0-10", "way_in": "+z", "held_by": "the lid's clips"},
        REFS,
    )
    assert said == ""


def test_a_bought_parts_fact_missing_a_field_is_shown_a_whole_one() -> None:
    said = facts.problem({**FAN, "box": "x 0-30, y 0-30, z 0-10", "way_in": "+z"}, REFS)
    assert "`held_by` missing" in said
    assert facts.bought_shape("references/fan.step") in said
    shown = json.loads(facts.bought_shape("references/fan.step"))
    assert shown["way_in"] == "+z" and facts.problem({**shown, "from": "x"}, REFS) == ""


@pytest.mark.parametrize(("way", "signed"), [("z", "+z"), ("Y", "+y"), ("from the top", "+z")])
def test_an_unsigned_way_in_is_shown_signed(way: str, signed: str) -> None:
    said = facts.problem({**FAN, "box": "x 0-30", "way_in": way, "held_by": "clips"}, REFS)
    assert said.startswith("`way_in` is one of +x, -x, +y, -y, +z, -z, the way it goes in, signed")
    assert f'`"way_in": "{signed}"`: not {way!r}' in said


def test_fixed_with_a_way_in_is_refused() -> None:
    """Delivered by mqucifer/print-shop#86."""
    said = facts.problem({**FAN, "box": "x 0-30", "way_in": "+z", "fixed": True}, REFS)
    assert said.startswith("`fixed` says it's in place and never fitted, and `way_in` says")


# `restate`


def restated(root: Path, raw: list[dict[str, Any]]) -> dict[str, Any]:
    return third(root, tool("restate", descriptions=raw))


def test_a_needs_what_restated_in_words_is_kept(tmp_path: Path) -> None:
    said = restated(tmp_path, [{"of": "light", "now": "light passes through the sides"}])
    assert said["outcome"] == "ok", said["reply"]


def test_a_whole_need_restated_is_refused_and_nothing_is_kept(tmp_path: Path) -> None:
    whole = {"what": "light passes", "check": {"by": "eye", "look_for": "light"}}
    run = lamp(tmp_path, [tool("restate", descriptions=[{"of": "light", "now": whole}])])
    run.drive()
    said = told(run)[2]
    assert said["why"] == "misses"
    assert (
        "light: `now` is words, not an object. `restate` keeps the breakdown's and each need's "
        '`what` only, as {"of": "N1", "now": "the need\'s `what` in new words"}: a need\'s check '
        "or quote changes with `needs`"
    ) in said["reply"]
    assert run.state.restated == []
    assert next(n for n in run._needs() if n.id == "light").what == "light passes through the sides"


def test_a_request_line_restated_is_told_to_name_its_need(tmp_path: Path) -> None:
    said = restated(tmp_path, [{"of": "R1", "holds": "it still holds"}])
    assert said["why"] == "misses"
    assert (
        "`R1` isn't a description kept. They are: `breakdown`, `light`, `sits`, `glow`. `R1` is a "
        "line of the request: name the need that covers it. `restate` keeps the breakdown, each "
        "need's `what` by the need's id, and the plan."
    ) in said["reply"]


# The bench's arguments


def parsed(words: str, capsys: pytest.CaptureFixture[str]) -> str:
    """What the bench's parser said of a command: nothing where it read, else its error."""
    import shlex

    try:
        cli.parser().parse_args(shlex.split(words))
    except SystemExit as exc:
        assert exc.code == 3, "a usage error"
        return capsys.readouterr().err.splitlines()[0]
    return ""


@pytest.mark.parametrize(
    "words",
    [
        "clearance lid.step fan.step --slide +z",
        "clearance lid.step fan.step --slide -z",
        "rays cup.step --centre 0,0,5 --axis z",
        "wall cup.step --hole 0,0,5 --axis x",
        "render cup.step --look cup.step=blue:cup --views top",
        "render cup.step --cut z=3,x=0:10",
    ],
)
def test_the_bench_takes_its_arguments_as_said(
    words: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert parsed(words, capsys) == ""


@pytest.mark.parametrize(
    ("words", "said"),
    [
        (
            "clearance lid.step fan.step --slide z",
            "usage error: argument --slide: the way the last file moves out, signed and lower "
            "case, one of +x -x +y -y +z -z, such as `--slide +z`: not 'z'",
        ),
        ("clearance lid.step fan.step --slide +Z", "such as `--slide +z`: not '+Z'"),
        (
            "rays cup.step --centre 0,0,5 --axis Z",
            "usage error: argument --axis: an axis, lower case and unsigned, one of x y z, such "
            "as `z`: not 'Z'",
        ),
        (
            "rays cup.step --centre 0,0,5 --axis +z",
            "such as `z`: not '+z': a sign is a way's, as `--slide +z` takes it",
        ),
        ("clearance a.step b.step --lift 1 --along Z", "argument --along: an axis, lower case"),
        (
            "render cup.step --look cup.step=blue:the cup --views top",
            "usage error: render: a --look label is one word: `--look "
            "cup.step=blue:the-cup`, not `the cup`, which the command line splits",
        ),
    ],
)
def test_the_bench_refuses_a_wrong_argument_naming_the_right_one(
    words: str, said: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert said in parsed(words, capsys)
