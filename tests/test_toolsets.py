"""Toolsets, and a need's check measuring what it names (mqucifer/print-shop#37). No Docker.

In all three stained-glass runs the hanging requirement was settled on a stand-in: the frame's
bounds by `info`, or the whole part's `thin`. Each case here is made up in the test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import needs, toolsets, transcript
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool
from tests.test_intake import BROKEN_DOWN

CORE = toolsets.CORE
GLASS = toolsets.STAINED_GLASS
REQUEST = (
    "A frame of three panes. At least 2 mm of wall round the hanging hole. At least 50% of each "
    "pane open to the light. Panes no thicker than 1.5 mm."
)


def need(command: str, measures: str | None = None, **more: Any) -> dict[str, Any]:
    check: dict[str, Any] = {"by": "bench", "command": command, **more}
    if measures is not None:
        check["measures"] = measures
    return {
        "id": "it",
        "what": "what it must do",
        "source": "request",
        "quote": more.pop("quote", "At least 2 mm of wall round the hanging hole"),
        "check": check,
    }


def read(entry: dict[str, Any], toolset: toolsets.Toolset = GLASS) -> list[str]:
    return needs.read([entry], REQUEST, "", toolset)[1]


def test_a_hanging_wall_settled_on_a_stand_in_is_refused_naming_the_command_that_measures_it() -> (
    None
):
    """Lesson 22, `suncatcher-2`: the frame's bounds by `info`, where `wall` reads the hole."""
    for stand_in in ("info frame.step", "thin frame.step"):
        said = read(need(stand_in, "wall"))
        assert len(said) == 1
        assert "is about `wall`, the wall round one hole" in said[0]
        assert "`wall` does, as `wall FILE --hole X,Y,Z --min-wall W`" in said[0], said[0]
    assert read(need("wall frame.step --hole 20,43.5,2 --min-wall 2", "wall")) == []


def test_a_bench_need_says_what_it_measures_from_its_toolsets_list() -> None:
    said = read(need("wall frame.step --hole 1,2,3", None))
    assert "`check.measures` says what this need is about, one of: `size`" in said[0]
    assert "`light`, the share of each pane open" in said[0], "the kind's own are listed"
    assert "`light`" not in read(need("wall frame.step --hole 1,2,3", None), CORE)[0]
    wrong = read(need("info frame.step", "outline"))
    assert "`outline` isn't a measure of this run's toolset" in wrong[0]


def test_a_kinds_command_or_measure_in_a_run_without_that_kind_says_where_it_is() -> None:
    said = read(need("capture frame.step --pocket-z 2 --clearance 0.25 --pane-thickness 1",
                     "pane-hold"), CORE)  # fmt: skip
    assert "`capture` is in the stained glass toolset, and this run's is the core" in said[0]
    said = read(need("info frame.step", "light"), CORE)
    assert "`light` isn't a measure of this run's toolset: it's in the stained glass" in said[0]


def test_one_piece_is_read_with_its_flag() -> None:
    assert "with `--one-body`" in read(need("thin frame.step", "one-piece"))[0]
    assert read(need("thin frame.step --one-body", "one-piece")) == []
    assert read(need("thin frame.step", "printable")) == []


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        (need("light frame.step pane_a.step", "light", quote="each pane open to the light"),
         "gives its limit, `--min-open`, or a `number`"),
        (need("light frame.step pane_a.step --min-open 40", "light",
              quote="At least 50% of each pane open to the light"),
         "its limit 40 isn't in its quote"),
        (need("thickness pane_a.step", "pane-thickness", number="thickest_mm", at_most=1.2,
              quote="Panes no thicker than 1.5 mm"),
         "its limit 1.2 isn't in its quote"),
    ],
)  # fmt: skip
def test_a_limit_is_the_requests_never_a_default(entry: dict[str, Any], says: str) -> None:
    said = read(entry)
    assert len(said) == 1 and says in said[0], said


def test_a_limit_from_the_request_is_taken_as_an_option_or_a_number() -> None:
    light = need("light frame.step pane_a.step --min-open 50", "light",
                 quote="At least 50% of each pane open to the light")  # fmt: skip
    assert read(light) == []
    thick = need("thickness pane_a.step", "pane-thickness", number="thickest_mm", at_most=1.5,
                 quote="Panes no thicker than 1.5 mm")  # fmt: skip
    assert read(thick) == []
    chosen = {**light, "source": "chosen"}
    chosen.pop("quote")
    assert "its `source` is `request`, `answer`, `note` or `pick`" in read(chosen)[0]


def test_render_measures_nothing_and_old_needs_read_as_they_were() -> None:
    assert "doesn't measure that" in read(need("render frame.step", "size"))[0]
    # A run from before toolsets: no `measures`, any command, as it was.
    found, wrong = needs.read([need("info frame.step")], REQUEST, "", toolsets.EVERY)
    assert wrong == [] and found[0].measures == ""
    assert needs.load([{"id": "x", "what": "w", "source": "chosen", "by": "bench",
                        "command": "capture f.step"}])[0].measures == ""  # fmt: skip


def test_a_kind_is_named_as_a_request_writes_it() -> None:
    assert toolsets.named("Stained glass") is GLASS
    assert toolsets.named("stained_glass") is GLASS
    assert toolsets.named(None) is CORE and toolsets.named("  ") is CORE
    assert toolsets.named("ceramics") is CORE
    assert "kind, 'ceramics', has no toolset of its own" in CORE.said("ceramics")
    assert toolsets.of_run("stained-glass", toolsets=False) is toolsets.EVERY


#: A request in the set form whose `## Kind` loads the stained-glass toolset.
GLASS_REQUEST = f"""{REQUEST}

## Requirements

- At least 2 mm of wall round the hanging hole.

## Kind

stained-glass
"""


def started(root: Path, *turns: Any, request: str = REQUEST) -> tuple[Run, Scripted]:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", request, [], model, sleep=lambda _: None)
    return run, model


def replies(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def test_a_run_with_no_kind_has_the_core_only_and_says_so(tmp_path: Path) -> None:
    run, model = started(
        tmp_path,
        [tool("bench", command="capture frame.step --pocket-z 2")],
        [tool("skill", name="designing-stained-glass")],
        [tool("skill", name="using-the-bench")],
    )
    assert run.drive().name == "blocked"
    start = model.bodies[0]["messages"][1]["content"]
    assert "This run's toolset is the core: the bench commands `info`" in start
    assert "designing-stained-glass" not in start
    told = replies(run)
    assert told[0]["outcome"] == "failed" and told[0]["why"] == "not_in_toolset"
    assert "`capture` is in the stained glass toolset" in told[0]["reply"]
    assert told[1]["why"] == "unknown"
    kept = run.ledger.rows("SELECT seq, at_call, name, file, found FROM skill_reads ORDER BY seq")
    assert [dict(r) for r in kept] == [
        {"seq": 1, "at_call": 2, "name": "designing-stained-glass", "file": None, "found": 0},
        {"seq": 2, "at_call": 3, "name": "using-the-bench", "file": "bench/SKILL.md", "found": 1},
    ]
    toolset = json.loads(run.ledger.rows("SELECT toolset FROM runs")[0]["toolset"])
    assert toolset["kind"] is None and "capture" not in toolset["commands"]


def test_a_stained_glass_run_reads_its_skill_and_its_needs_measure_what_they_name(
    tmp_path: Path,
) -> None:
    """The request's `## Kind` loads the toolset: no stand-in sets it."""
    hang = {**need("info frame.step", "wall"), "id": "hang", "covers": ["R1"]}
    run, model = started(
        tmp_path,
        [tool("skill", name="designing-stained-glass")],
        [BROKEN_DOWN],
        [tool("needs", needs=[hang])],
        request=GLASS_REQUEST,
    )
    assert run.drive().name == "blocked"
    assert run.toolset is toolsets.STAINED_GLASS and run.state.kind == "stained-glass"
    start = model.bodies[0]["messages"][1]["content"]
    assert "This run's toolset is stained glass" in start and "`designing-stained-glass`" in start
    told = [e for e in replies(run) if e["name"] in ("skill", "needs")]
    assert told[0]["outcome"] == "ok" and "# Designing stained glass" in told[0]["reply"]
    assert told[1]["why"] == "misses", told[1]["reply"]
    assert "`wall` does, as `wall FILE --hole X,Y,Z --min-wall W`" in told[1]["reply"]
    kept = run.ledger.rows("SELECT file FROM skill_reads")
    assert kept[0]["file"] == "designing-stained-glass/SKILL.md"


def test_a_run_from_before_toolsets_keeps_every_command(tmp_path: Path) -> None:
    run, _ = started(tmp_path, [tool("questions", questions="Anything?")])
    assert run.drive().name == "questions"
    state = json.loads((run.folder / "state.json").read_text())
    del state["kind"], state["toolsets"]
    (run.folder / "state.json").write_text(json.dumps(state))
    again = Run.load(run.folder, Scripted(), sleep=lambda _: None)
    assert again.toolset is toolsets.EVERY
    assert again.tools._not_in_toolset("capture") is None
    bench = next(s for s in again.tools.specs if s["function"]["name"] == "bench")
    assert "`capture`" in bench["function"]["description"]


# The enclosure (mqucifer/print-shop#60)

ENCLOSURE = toolsets.ENCLOSURE
CASE_REQUEST = """A case for a board, its lid not loose, with its edges softened to 2 mm.

## Requirements

- Edges, corners and lid softened to at least 2 mm.

## Kind

enclosure
"""


def test_an_edge_need_takes_its_radius_from_the_request() -> None:
    def read_case(entry: dict[str, Any]) -> list[str]:
        return needs.read([entry], CASE_REQUEST, "", ENCLOSURE)[1]

    quote = "Edges, corners and lid softened to at least 2 mm"
    assert read_case(need("edges base.step lid.step --min-radius 2", "edges", quote=quote)) == []
    assert "its limit 1 isn't in its quote" in read_case(
        need("edges base.step lid.step --min-radius 1", "edges", quote=quote)
    )[0]
    assert "gives its limit, `--min-radius`" in read_case(
        need("edges base.step lid.step", "edges", quote=quote)
    )[0]
    assert "`edges` doesn't measure that: `clearance` does" in read_case(
        need("edges base.step --min-radius 2", "fit", quote=quote)
    )[0]
    # In a run without the kind, the command and the measure say where they are.
    said = needs.read([need("edges base.step --min-radius 2", "edges", quote=quote)],
                      CASE_REQUEST, "", CORE)[1]  # fmt: skip
    assert "`edges` is in the enclosure toolset, and this run's is the core" in said[0]


def test_an_enclosure_run_loads_its_toolset_and_skill(tmp_path: Path) -> None:
    """The request's `## Kind` loads `edges` and `designing-enclosures`; a core run refuses
    `edges`, naming the toolset it's in."""
    run, model = started(
        tmp_path,
        [tool("skill", name="designing-enclosures")],
        [tool("skill", name="softening-edges")],
        request=CASE_REQUEST,
    )
    assert run.drive().name == "blocked"
    assert run.toolset is ENCLOSURE and run.state.kind == "enclosure"
    start = model.bodies[0]["messages"][1]["content"]
    assert "This run's toolset is enclosure" in start and "`designing-enclosures`" in start
    told = [e for e in replies(run) if e["name"] == "skill"]
    assert told[0]["outcome"] == "ok" and "# Designing enclosures" in told[0]["reply"]
    assert told[1]["outcome"] == "ok" and "# Softening edges" in told[1]["reply"]
    bench = next(s for s in run.tools.specs if s["function"]["name"] == "bench")
    assert "`edges`" in bench["function"]["description"]
    core, _ = started(tmp_path / "core", [tool("bench", command="edges base.step")])
    assert core.drive().name == "blocked"
    refused = replies(core)[0]
    assert refused["why"] == "not_in_toolset"
    assert "`edges` is in the enclosure toolset" in refused["reply"]
