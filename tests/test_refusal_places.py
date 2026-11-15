"""A refused tool call says where the field goes, and a field sent as a string holding JSON is
read or named as one (mqucifer/print-shop#145).

The largest group of lessons, `tool-form-slips`, is calls spent learning a tool's form from
refusals that say what's wrong and not where it goes. Each case here is one a lesson names, sent
through the tools' own refusal paths: a scripted model drives each run, with no model call and no
Docker. The request is the made-up shelf bracket of `test_intake.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import breakdown, needs
from tests.rounds_fakes import raw, tool
from tests.test_intake import BRACKET, BROKEN_DOWN, begin, covered, told

# A need's key in the wrong place


def moved(need: dict[str, Any], *keys: str) -> dict[str, Any]:
    """A need with some of its check's keys beside `check`, as the eval's were."""
    check = {k: v for k, v in need["check"].items() if k not in keys}
    return {**need, "check": check, **{k: need["check"][k] for k in keys}}


def test_a_number_and_its_limits_beside_check_are_told_to_go_inside_it(tmp_path: Path) -> None:
    """runs14-neon-mount:12. Seven needs had `number`, `at_least` and `at_most` beside `check`,
    and were refused with the need's keys; sent again with `number` moved and the limits left,
    they were refused the same way. Now the first refusal says where all three go."""
    sent = covered()
    sent[0] = moved(sent[0], "number", "at_least")
    run = begin(tmp_path, BRACKET, [BROKEN_DOWN], [tool("needs", needs=sent)])
    assert run.drive().name == "blocked"
    refused = told(run)[1]
    assert refused["outcome"] == "failed" and refused["why"] == "misses", refused["reply"]
    said = (
        "the need 'holds': `at_least` and `number` go inside `check`, beside `by`, not beside "
        '`check`, as "check": {"by": "bench", "measures": "...", "command": "...", "number": '
        '"pairs.0.gap_mm", "at_least": 0.2, "at_most": 0.4}'
    )
    assert said in refused["reply"], refused["reply"]
    assert "it has only id, what" not in refused["reply"]


@pytest.mark.parametrize(
    ("change", "said"),
    [
        # Each of a check's keys, beside it.
        (lambda n: moved(n, "at_most"), "`at_most` goes inside `check`, beside `by`"),
        (lambda n: moved(n, "measures"), "`measures` goes inside `check`, beside `by`"),
        (lambda n: moved(n, "command", "by"), "`by` and `command` go inside `check`, beside `by`"),
        (lambda n: {**n, "verdicts": ["OK"]}, "`verdicts` goes inside `check`, beside `by`"),
        (lambda n: {**n, "look_for": "x"}, "`look_for` goes inside `check`, beside `by`"),
        (lambda n: {**n, "side": "-z"}, "`side` goes inside `check`, beside `by`"),
        # A need's own key, inside its check.
        (
            lambda n: {**n, "check": {**n["check"], "covers": ["R1"], "quote": "x"}},
            "`covers` and `quote` go beside `check`, at the need's top level, not inside it",
        ),
        (
            lambda n: {**n, "check": {**n["check"], "fixed": True}},
            "`fixed` goes beside `check`, at the need's top level, not inside it",
        ),
        # The tool's own argument, in a need.
        (
            lambda n: {**n, "try": True},
            "`try` is the `needs` tool's own argument: it goes beside the `needs` list, in no "
            "need",
        ),
        # A key that's nobody's still gets the list.
        (
            lambda n: {**n, "tolerance": 0.1},
            "it has only id, what, source, quote, check, covers, fixed and figure: not "
            "`tolerance`",
        ),
    ],
)
def test_each_misplaced_key_of_a_need_is_refused_with_its_place(change: Any, said: str) -> None:
    need = {
        "id": "gap",
        "what": "the lid's play",
        "source": "derived",
        "check": {
            "by": "bench",
            "measures": "fit",
            "command": "clearance a.step b.step",
            "number": "pairs.0.gap_mm",
            "at_least": 0.2,
            "at_most": 0.4,
        },
    }
    _, wrong = needs.read([change(need)], "")
    assert any(w == f"the need 'gap': {said}" or w.startswith(f"the need 'gap': {said}") for w in (
        wrong
    )), wrong


def test_a_needs_field_sent_beside_the_list_says_it_goes_in_a_need(tmp_path: Path) -> None:
    turn = [tool("needs", needs=covered(), check={"by": "eye"}, at_least=1)]
    run = begin(tmp_path, BRACKET, [BROKEN_DOWN], turn)
    assert run.drive().name == "blocked"
    refused = told(run)[1]
    assert refused["outcome"] == "malformed" and refused["why"] == "unknown"
    assert "`check` is a need's: each need is an object in the `needs` list" in refused["reply"]
    assert "`at_least` goes inside a need's `check`, in the `needs` list" in refused["reply"]


# A breakdown is checked whole

#: The eval's breakdown, in its shape: a name with a `+`, joins naming `channel` and `board`
#: where `parts` has `channel (the M)` and `board + LED strip`, and `cap` in no join.
MOUNT = {
    "parts": [
        {"name": "channel (the M)", "what": "the given channel letter"},
        {"name": "screw spacers (3)", "what": "pads under each screw hole"},
        {"name": "cap", "what": "the given cap, unchanged"},
        {"name": "board + LED strip", "what": "what the letter is screwed to"},
    ],
    "joins": [
        {"parts": ["channel", "screw spacers (3)"], "how": "the floor bears on the spacers"},
        {"parts": ["screw spacers (3)", "board"], "how": "each stands on the board"},
        {"parts": ["channel", "board"], "how": "screws through the floor into the board"},
    ],
    "look": ["the letter floats 12 mm off the board"],
}


def test_a_breakdown_names_every_fault_once_in_one_refusal() -> None:
    """runs14-neon-mount:17. The refusal named `channel` four times and said `channel (the M)`
    was in no join beside it, and the name's fault as "letters, digits and spaces", with
    parentheses allowed and `+` not. Each fault is now said once, with the part a join means."""
    found, wrong = breakdown.read(MOUNT)
    assert found == {}
    assert wrong == [
        "a part's `name` is short, of letters, digits and spaces: not 'board + LED strip', as "
        "`+` isn't in a name, so say it in words, as `and` for `+`",
        "a join names `channel`, in 2 joins, and `parts` names it `channel (the M)`: a join "
        "names each part exactly as `parts` does, so name it `channel (the M)` in the join, or "
        "`channel` in `parts`",
        "a join names `board`, in 2 joins, and `parts` names it `board + LED strip`: a join "
        "names each part exactly as `parts` does, so name it `board + LED strip` in the join, "
        "or `board` in `parts`",
        "`cap` is in no join: say what holds it to the rest, or, if nothing does, that's for "
        "the design to answer before the needs",
    ]


def test_a_breakdowns_field_of_the_wrong_kind_is_named_and_the_rest_is_still_read() -> None:
    _, wrong = breakdown.read(
        {
            "parts": ["plate", {"name": "arm", "what": "out from the plate", "how": "fused"}],
            "joins": {"parts": ["plate", "arm"], "how": "one body"},
            "name": "bracket",
            "look": ["one clean L"],
        }
    )
    assert wrong == [
        '`name` go in each part, in `parts`: {"name": "...", "what": "..."}',
        'each part is {"name": "...", "what": "..."} and nothing else: not "plate"',
        "each part is {\"name\": \"...\", \"what\": \"...\"} and nothing else: not 'arm', which "
        "also has ['how']",
        "`parts` names at least one part, each with what it is and does",
        '`joins` is a list, `"joins": [{"parts": ["a", "b"], "how": "..."}, ...]`: not an '
        "object",
    ]


def test_a_breakdown_sent_through_the_tool_is_refused_whole(tmp_path: Path) -> None:
    run = begin(tmp_path, BRACKET, [tool("breakdown", **MOUNT)])
    assert run.drive().name == "blocked"
    refused = told(run)[0]
    assert refused["outcome"] == "failed" and refused["why"] == "misses"
    reply = refused["reply"]
    assert reply.startswith("The breakdown wasn't kept:\n- a part's `name`")
    assert reply.count("\n- ") == 4 and reply.count("a join names `channel`") == 1, reply


# A field sent as a string holding JSON

PARTS = BROKEN_DOWN["arguments"]["parts"]
JOINS = BROKEN_DOWN["arguments"]["joins"]
LOOK = BROKEN_DOWN["arguments"]["look"]


def test_a_list_sent_as_its_json_text_is_read_as_the_list(tmp_path: Path) -> None:
    sent = tool("breakdown", parts=json.dumps(PARTS), joins=json.dumps(JOINS), look=LOOK)
    run = begin(tmp_path, BRACKET, [sent])
    assert run.drive().name == "blocked"
    kept = told(run)[0]
    assert kept["outcome"] == "ok", kept["reply"]
    assert json.loads((run.folder / "breakdowns" / "1.json").read_text())["parts"] == PARTS


def test_a_string_holding_its_field_and_the_calls_other_fields_is_read_as_them(
    tmp_path: Path,
) -> None:
    """eval-neon-strip-2, call 97. `parts` arrived as a string: its list, then the call's other
    fields, `"[...], \\"joins\\": [...], \\"look\\": [...]}"`. The refusal read "it needs `joins`,
    `look`", which the call had sent. It's read as the fields it holds."""
    spilled = f'{json.dumps(PARTS)}, "joins": {json.dumps(JOINS)}, "look": {json.dumps(LOOK)}}}'
    run = begin(tmp_path, BRACKET, [raw("breakdown", json.dumps({"parts": spilled}))])
    assert run.drive().name == "blocked"
    kept = told(run)[0]
    assert kept["outcome"] == "ok", kept["reply"]
    found = json.loads((run.folder / "breakdowns" / "1.json").read_text())
    assert found["parts"] == PARTS and found["joins"] == JOINS and found["look"] == LOOK


@pytest.mark.parametrize(
    ("arguments", "said"),
    [
        # A string that isn't the list: cut short.
        (
            {"parts": json.dumps(PARTS)[:-2], "joins": JOINS, "look": LOOK},
            '`breakdown` wasn\'t run: `parts` is a string holding JSON, and it takes a list. Send '
            'the value itself, `"parts": [...]`, not its text in quotes, `"parts": "[...]"`.',
        ),
        # A string holding other fields the call also sent: which is meant isn't plain.
        (
            {"parts": f'{json.dumps(PARTS)}, "joins": {json.dumps(JOINS)}}}', "joins": JOINS,
             "look": LOOK},  # fmt: skip
            "`breakdown` wasn't run: `parts` is a string holding JSON, and it takes a list.",
        ),
        # Words where a list goes.
        (
            {"parts": "plate, arm and gusset", "joins": JOINS, "look": LOOK},
            "`breakdown` wasn't run: `parts` is a string, and it takes a list.",
        ),
    ],
)
def test_a_string_that_cant_be_read_as_its_field_is_refused_by_its_name(
    tmp_path: Path, arguments: dict[str, Any], said: str
) -> None:
    run = begin(tmp_path, BRACKET, [raw("breakdown", json.dumps(arguments))])
    assert run.drive().name == "blocked"
    refused = told(run)[0]
    assert refused["outcome"] == "ill_typed" and refused["why"] == "json_text", refused
    assert refused["reply"].startswith(said), refused["reply"]
    assert "it needs" not in refused["reply"]


def test_an_object_sent_as_its_json_text_is_read_and_one_that_isnt_is_named(
    tmp_path: Path,
) -> None:
    why = json.dumps({"holds": "the arm is now one body with the plate"})
    turns = [
        [BROKEN_DOWN],
        [tool("needs", needs=json.dumps(covered()), why_changed=why, **{"try": True})],
        [tool("needs", needs=covered(), why_changed="the arm changed", **{"try": True})],
    ]
    run = begin(tmp_path, BRACKET, *turns)
    (run.work / "bracket.step").write_text("x")
    assert run.drive().name == "blocked"
    _, tried, refused = told(run)[:3]
    assert tried["why"] != "json_text" and tried["outcome"] != "ill_typed", tried["reply"]
    assert refused["outcome"] == "ill_typed" and refused["why"] == "json_text"
    assert refused["reply"].startswith(
        "`needs` wasn't run: `why_changed` is a string, and it takes an object."
    )
