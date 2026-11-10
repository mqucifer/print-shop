"""The `needs` tool shows each need's fields as a schema, and a try reads its checks even when a
need's form is wrong (mqucifer/print-shop#93).

`needs` refused for a mistake was the largest refusal class, and most of its lines were a `quote`
left out: the fields were only in the tool's words. No Docker: a scripted model drives each run,
and a box answers each bench command and runs nothing. The request is the made-up shelf bracket
of `test_intake.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import needs, tools, toolsets
from print_shop.rounds.run import Run
from tests.rounds_fakes import tool
from tests.test_intake import BRACKET, BROKEN_DOWN, begin, covered, told

#: The `needs` tool's description on main before this, by toolset: the schema says the fields
#: now, so the words are no longer than they were.
DESCRIBED_BEFORE = {"every": 1804, "core": 2816, "stained-glass": 3306, "enclosure": 2960}
TOOLSETS: dict[str, toolsets.Toolset] = {
    "every": toolsets.EVERY,
    "core": toolsets.CORE,
    **{str(kind): one for kind, one in toolsets.KINDS.items()},
}
MISSING = "the need 'holes' comes from the request: give its `quote`, their own words"


def needs_spec(toolset: toolsets.Toolset) -> dict[str, Any]:
    spec: dict[str, Any] = next(
        s["function"] for s in tools.specs(toolset) if s["function"]["name"] == "needs"
    )
    return spec


def unquoted() -> list[dict[str, Any]]:
    """The bracket's needs, every line covered, with one from the request left unquoted."""
    return covered(holes={"quote": ""})


def with_part(run: Run) -> Run:
    (run.work / "bracket.step").write_text("x")
    return run


@pytest.mark.parametrize("name", sorted(TOOLSETS))
def test_the_needs_items_are_a_schema_of_each_field(name: str) -> None:
    toolset = TOOLSETS[name]
    spec = needs_spec(toolset)
    item = spec["parameters"]["properties"]["needs"]["items"]
    assert item["type"] == "object" and item["additionalProperties"] is False
    assert set(item["properties"]) == {
        "id",
        "what",
        "source",
        "quote",
        "check",
        "covers",
        "fixed",
        "figure",
    }
    assert item["required"] == ["id", "what", "source", "check"]
    assert item["properties"]["source"]["enum"] == list(needs.SOURCES)
    quote = item["properties"]["quote"]["description"]
    assert quote.startswith("Required where `source` is `request`, `answer`, `note` or `pick`")
    assert "word for word" in quote and "figure" in quote
    check = item["properties"]["check"]
    assert set(check["properties"]) == {
        "by",
        "measures",
        "command",
        "verdicts",
        "number",
        "at_least",
        "at_most",
        "look_for",
        "no_bench",
        "file",
        "side",
    }
    assert check["properties"]["by"]["enum"] == ["bench", "eye", "bed"]
    assert check["properties"]["side"]["enum"] == list(needs.SIDES)
    # One level: no field of `check` has fields of its own.
    assert all("properties" not in f for f in check["properties"].values())
    measures = check["properties"]["measures"]
    if toolset.legacy:
        assert "enum" not in measures, "a run from before toolsets has no measures to name"
    else:
        assert measures["enum"] == [m.name for m in toolset.measures]
    # Every key a need may have is in the schema, and nothing else.
    assert set(item["properties"]) == {"id", "what", "source", "quote", "check", *needs.MARKS}


@pytest.mark.parametrize("name", sorted(TOOLSETS))
def test_the_needs_description_is_no_longer_than_it_was(name: str) -> None:
    described = needs_spec(TOOLSETS[name])["description"]
    assert len(described) <= DESCRIBED_BEFORE[name]
    assert "never a hold, a bought part's or one of your parts' on another" in described
    assert "never an eye need" in described


def test_needs_with_every_field_right_fit_the_schema_they_are_shown() -> None:
    """Each need the bracket settles with has only the schema's fields, of its types: the
    schema shows what `needs.read` takes."""
    item = needs_spec(toolsets.CORE)["parameters"]["properties"]["needs"]["items"]
    types: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "boolean": bool,
        "array": list,
        "object": dict,
        "number": (int, float),
    }
    for need in covered():
        assert set(need) <= set(item["properties"]) and set(item["required"]) <= set(need)
        for key, value in need.items():
            assert isinstance(value, types[item["properties"][key]["type"]]), key
        fields = item["properties"]["check"]["properties"]
        for key, value in need["check"].items():
            assert isinstance(value, types[fields[key]["type"]]), key
            assert value in fields[key].get("enum", [value]), key


def test_a_try_with_a_need_missing_its_quote_gets_that_line_and_its_checks_reading(
    tmp_path: Path,
) -> None:
    run = with_part(begin(tmp_path, BRACKET, [tool("needs", needs=unquoted(), **{"try": True})]))
    assert run.drive().name == "blocked"
    tried = told(run)[0]
    assert tried["outcome"] == "failed" and tried["why"] == "misses"
    reply = tried["reply"]
    assert reply.startswith("Nothing is settled, and the needs as sent wouldn't be taken:")
    assert f"- {MISSING}" in reply
    assert "- holes, would pass: OK. Limit: exit 0." in reply, "its own check is read"
    assert "- plain: for the Sponsor's eye, so there's nothing to read." in reply
    assert [r["need"] for r in tried["detail"]["readings"]] == ["holds", "holes", "arm", "plate"]
    assert tried["detail"]["wrong"] == [MISSING]
    kept = run.ledger.rows("SELECT need, passed FROM tries ORDER BY seq")
    assert [r["need"] for r in kept] == ["holds", "holes", "arm", "plate"]
    assert kept[1]["passed"] == 1, "the tries table keeps the readings"
    assert run.state.needs == [] and run.state.needs_open, "a try settles nothing"


def test_the_same_needs_settled_are_refused_naming_the_quote(tmp_path: Path) -> None:
    run = with_part(begin(tmp_path, BRACKET, [BROKEN_DOWN], [tool("needs", needs=unquoted())]))
    assert run.drive().name == "blocked"
    settled = told(run)[1]
    assert settled["outcome"] == "failed" and settled["why"] == "misses"
    assert settled["reply"] == f"The needs weren't taken:\n- {MISSING}"
    assert run.box.asked == [], "a settle reads no check before its form is right"  # type: ignore[attr-defined]
    assert run.state.needs == []


def test_needs_with_every_field_right_are_tried_and_taken_as_before(tmp_path: Path) -> None:
    run = with_part(
        begin(
            tmp_path,
            BRACKET,
            [tool("needs", needs=covered(), **{"try": True})],
            [BROKEN_DOWN],
            [tool("needs", needs=covered())],
        )
    )
    assert run.drive().name == "blocked"
    tried, _, settled = told(run)[:3]
    assert tried["outcome"] == "ok" and "wrong" not in tried["detail"]
    assert tried["reply"].startswith(
        "Nothing is settled. Each check, against your files as they stand:\n- holds, "
    )
    assert "- holes, would pass: OK. Limit: exit 0." in tried["reply"]
    assert settled["outcome"] == "ok"
    assert [n["id"] for n in run.state.needs] == ["holds", "holes", "plain", "arm", "plate"]


def test_a_try_skips_only_a_check_whose_command_cant_be_run(tmp_path: Path) -> None:
    """A malformed need is never run in a way that could fail the harness: a command that doesn't
    parse, isn't a bench command, or isn't the run's is left out, and a mistaken id is read under
    a name of the harness's own."""
    sent = [
        *covered(
            holds={"check": {"by": "bench", "measures": "size", "command": 'info "bracket.step'}}
        ),
        {
            "id": "nope",
            "what": "x",
            "source": "derived",
            "check": {"by": "bench", "command": "drill x.step"},
        },
        {
            "id": "glass",
            "what": "x",
            "source": "derived",
            "check": {"by": "bench", "measures": "light", "command": "light bracket.step"},
        },
        {
            "id": "../up",
            "what": "x",
            "source": "derived",
            "check": {"by": "bench", "measures": "size", "command": "info bracket.step"},
        },
        {"id": "loose", "what": "x", "source": "derived", "check": "info bracket.step"},
        {"id": "side", "what": "x", "source": "derived", "check": {"by": "bed"}},
        {
            "id": "where",
            "what": "x",
            "source": "derived",
            "measures": "size",
            "check": {"by": "bench", "command": "info bracket.step"},
        },
    ]
    run = with_part(begin(tmp_path, BRACKET, [tool("needs", needs=sent, **{"try": True})]))
    assert run.drive().name == "blocked"
    tried = told(run)[0]
    assert tried["outcome"] == "failed" and tried["why"] == "misses"
    reply = tried["reply"]
    for skipped in ("holds", "nope", "glass"):
        assert f"- {skipped}: not run, as its check can't be run as it stands." in reply
    assert "the need 'holds': `command` can't be read: No closing quotation" in reply
    assert "the need 'where': it has only id, what, source, quote, check" in reply
    assert "- side: checked at `done`, against `on_bed`." in reply
    read = [r["need"] for r in tried["detail"]["readings"]]
    assert read == ["holes", "arm", "plate", "../up", "where"]
    asked = run.box.asked  # type: ignore[attr-defined]
    assert all("drill" not in a and "light" not in a for a in asked)


def test_a_try_with_nothing_that_can_be_run_says_so(tmp_path: Path) -> None:
    sent = json.dumps([{"id": "a"}])
    run = begin(tmp_path, BRACKET, [tool("needs", needs=sent, **{"try": True})])
    assert run.drive().name == "blocked"
    tried = told(run)[0]
    assert tried["outcome"] == "failed"
    assert "No check can be run as it stands, so none was read:" in tried["reply"]
    assert tried["detail"]["readings"] == []
