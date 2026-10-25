"""The print-settings skill: SKILL.md's index, its references and the code's groups agree.

mqucifer/print-shop#7. The knowledge lives in `skills/print-settings/`; code
keeps only the bounds (`setup/bounds.py`) and which groups a part touches
(`setup/skill.py`). These tests keep the three in step, so an edit to the
skill by PR can't silently leave a setting unexplained or unbounded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from print_shop.setup.bounds import SETTINGS
from print_shop.setup.features import measure
from print_shop.setup.skill import GROUPS, SKILL_DIR, groups_for, load
from tests.fixtures import write_stl
from tests.test_proposal import PARTS
from tests.test_questions import intake

_ROW = re.compile(r"^\| `([a-z-]+)` \| `references/([a-z-]+)\.md` \| (.+?) \| (.+?) \|$", re.M)


def _index() -> dict[str, tuple[str, set[str]]]:
    text = (SKILL_DIR / "SKILL.md").read_text()
    return {
        m.group(1): (m.group(2), set(re.findall(r"`([a-z_]+)`", m.group(3))))
        for m in _ROW.finditer(text)
    }


def test_the_index_lists_the_codes_groups_and_their_settings() -> None:
    index = _index()
    assert list(index) == [g.id for g in GROUPS]
    for group in GROUPS:
        file, settings = index[group.id]
        assert file == group.id
        assert settings == set(group.settings), group.id


def test_every_bounded_setting_belongs_to_exactly_one_group() -> None:
    owned = [s for g in GROUPS for s in g.settings]
    assert sorted(owned) == sorted(SETTINGS)


@pytest.mark.parametrize("group", GROUPS, ids=lambda g: g.id)
def test_each_reference_explains_each_setting_and_links_the_wiki(group: object) -> None:
    text = group.path.read_text()  # type: ignore[attr-defined]
    for key in group.settings:  # type: ignore[attr-defined]
        section = text.split(f"(`{key}`)", 1)
        assert len(section) == 2, f"{key} has no section"
        body = section[1].split("\n## ", 1)[0]
        assert "](https://github.com/OrcaSlicer/OrcaSlicer/wiki/" in body, key
        for label in ("**Effect:**", "**Trade-off:**", "**Change it when:**", "**Bound:**"):
            assert label in body, (key, label)


def test_a_part_loads_the_groups_its_features_and_options_touch(tmp_path: Path) -> None:
    loaded = {}
    for part in PARTS:
        stl = write_stl(part, tmp_path)
        from print_shop.ingest.analyzer import analyze

        features = measure(stl, analyze(stl).bed_contact_area_mm2)
        loaded[part] = [g.id for g in groups_for(intake(part, tmp_path), features)]
    # The vase's purpose options set only walls, shells and infill; the bracket
    # was asked nothing; the snap fit's hook brings supports and cooling, and
    # its footprint adhesion.
    assert loaded == {
        "vase": ["walls-shells", "infill"],
        "bracket": ["walls-shells", "infill"],
        "snap": ["walls-shells", "infill", "supports", "adhesion", "speed-cooling"],
    }


def test_an_option_that_sets_a_setting_brings_its_group() -> None:
    from print_shop.setup.intake import Answer, Intake, Option, Question

    option = Option(label="hot", settings={"nozzle_temperature": "230"}, impact="",
                    recommended=True, why="")
    asked = Intake(questions=(Question(feature="purpose", text="?", options=(option, option)),),
                   answers=(Answer(choice=0),))
    assert "temperature" in [g.id for g in groups_for(asked, ())]


def test_what_was_read_is_recorded_by_hash() -> None:
    skill = load(GROUPS[:2])
    assert skill.ids == ("walls-shells", "infill")
    assert skill.text.startswith("---\nname: print-settings")
    assert "<!-- references/infill.md -->" in skill.text
    assert "references/supports.md -->" not in skill.text
    assert len(skill.sha256) == 64
