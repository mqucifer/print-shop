"""Every skill a run reads, held to Anthropic's skill-authoring rules, and the stained-glass skill's
evaluations, written before its text (mqucifer/print-shop#37). No Docker, and no model.

The rules: platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices. The
`cadquery` and `references` sets are measured copies from mqucifer/model-performance, held to
their source by a hash: where they break a rule it's listed here, as the PR lists it, and they
aren't edited. print-shop's own, `bench` and `designing-stained-glass`, keep every rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from print_shop.rounds import skills, toolsets

EVALS = Path(__file__).parent / "skill_evals" / "designing-stained-glass"
OURS = ("bench", "designing-stained-glass")
MEASURED = ("cadquery", "references")
#: What the measured copies break, by file: their names aren't gerunds, and their descriptions
#: say what they hold but not when to read them. Listed, not edited.
MEASURED_BREAKS = {
    path: ["isn't in gerund form", "doesn't say when to use it"]
    for path in (
        "cadquery/SKILL.md",
        "cadquery/references/assemblies-fits.md",
        "cadquery/references/errors.md",
        "cadquery/references/selectors.md",
        "cadquery/references/sketch-extrude.md",
        "cadquery/references/threads-helices.md",
        "references/SKILL.md",
        "references/references/fasteners.md",
        "references/references/fdm.md",
        "references/references/fits.md",
    )
}


@pytest.mark.parametrize(
    "path", skills.files(OURS), ids=lambda p: str(p.relative_to(skills.SKILLS))
)
def test_print_shops_own_skills_keep_every_rule(path: Path) -> None:
    assert skills.broken(path) == []


def test_the_measured_copies_break_only_what_is_listed() -> None:
    for path in skills.files(MEASURED):
        shown = str(path.relative_to(skills.SKILLS))
        said = skills.broken(path)
        listed = MEASURED_BREAKS[shown]
        assert len(said) == len(listed) and all(
            rule in one for rule, one in zip(listed, said, strict=True)
        ), (shown, said)


def test_a_rule_breaker_is_caught(tmp_path: Path) -> None:
    """The checker itself: each rule, on a file made to break it."""
    folder = tmp_path / "x"
    (folder / "references" / "deeper").mkdir(parents=True)
    (folder / "references" / "deeper" / "y.md").write_text("---\nname: a\ndescription: b\n---\n")
    body = "\n".join(["line"] * 500) + "\nSee `references/gone.md`.\n"
    (folder / "SKILL.md").write_text(
        f"---\nname: Claude_Helper\ndescription: I help you <b>a lot</b>\n---\n{body}"
    )
    said = " | ".join(skills.broken(folder / "SKILL.md"))
    for rule in (
        "isn't lowercase", "reserved word", "gerund", "XML tag", "third person",
        "when to use", "under 500", "references/gone.md", "one level deep",
    ):  # fmt: skip
        assert rule in said, rule
    long = folder / "references" / "long.md"
    long.write_text("---\nname: reading-x\ndescription: Says x. Use when x.\n---\n" + "a\n" * 120)
    assert any("contents" in w for w in skills.broken(long))


def test_the_stained_glass_toolset_offers_its_skill_and_the_core_doesnt() -> None:
    assert "designing-stained-glass" in skills.registry(toolsets.STAINED_GLASS.skills)
    assert "checking-stained-glass" in skills.registry(toolsets.STAINED_GLASS.skills)
    assert "designing-stained-glass" not in skills.registry(toolsets.CORE.skills)


def test_the_stained_glass_skill_defines_each_term_once_and_folds_in_its_lessons() -> None:
    body = skills.registry(skills.ALL)["designing-stained-glass"][1]
    for term in ("cell", "pane", "leading", "pocket", "play", "opening", "lip"):
        assert len(re.findall(rf"^- \*\*{term}\*\*:", body, re.MULTILINE)) == 1, term
    for term in ("lip before play", "lip after play"):
        assert f"- **{term}**:" in body, term
    # Lessons 1, 2, 17 and 20, in general terms: no one part's names.
    for said in (
        'geom_type == "Polygon"',  # 1: the 2D layout, and the leading in one piece
        "Name each height once",  # 2
        "A lip per cell",  # 17
        "No sharp tips",  # 20
    ):
        assert said in body, said
    for name in ("Triforce", "suncatcher", "cup", "bird", "crest"):
        assert name.lower() not in body.lower(), name


def test_the_skill_gives_each_check_its_exact_command() -> None:
    """Freedom matches the risk: a check's command and settings are exact, the design open."""
    body = skills.registry(skills.ALL)["designing-stained-glass"][1]
    for measure in toolsets.STAINED_GLASS.measures:
        if measure.name in ("light", "pane-thickness", "pane-hold", "one-piece", "wall"):
            assert f"| `{measure.name}` |" in body, measure.name
    assert "Never change the check's limit" in body


def test_the_evaluations_came_first_and_each_has_a_task_files_and_expected_behaviour() -> None:
    found = json.loads((EVALS / "evals.json").read_text())
    assert found["skill"] == "designing-stained-glass"
    assert len(found["evals"]) >= 3
    gaps = set(re.findall(r"^\| (G\d+) \|", (EVALS / "gaps.md").read_text(), re.MULTILINE))
    made = (EVALS / "make_evals.py").read_text()
    for one in found["evals"]:
        assert one["request"].strip() and one["baseline"].strip(), one["id"]
        assert "## Kind\n\nstained glass" in one["request"], one["id"]
        assert len(one["expected_behavior"]) >= 3, one["id"]
        assert set(one["gaps"]) <= gaps, one["id"]
        for name in one["files"]:
            folder, file = name.split("/")
            assert f'"{folder}", "{file.removesuffix(".step")}"' in made or (
                f'"{folder}", name' in made
            ), name
    covered = {gap for one in found["evals"] for gap in one["gaps"]}
    assert gaps - covered == {"G12"}, "every gap but one is evaluated, and gaps.md says why not"
