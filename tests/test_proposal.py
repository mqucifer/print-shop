"""The settings proposed for three parts with different purposes, and why they differ.

mqucifer/print-shop#7, part 6: a cosmetic vase, a load-bearing bracket and a
snap fit. Their features differ (`setup.features`), and so must the
proposals, each change naming the feature or purpose behind it.

The model's replies were recorded once from the real model through LiteLLM
(`llm`, with `PRINT_SHOP_RECORD=1`) in `tests/data/recorded/proposals/`; CI
replays them through the same validation, bounds and guards.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from print_shop.expert.client import CallInfo, CutOff, LLMConfig
from print_shop.ingest.analyzer import analyze
from print_shop.setup import Proposer
from print_shop.setup.bounds import check
from print_shop.setup.features import Feature, measure
from print_shop.setup.intake import Answer, Intake, Option, Question
from print_shop.setup.model import Ask, llm_ask, prompt, setup_llm
from print_shop.setup.proposal import PartFacts, Proposal, Source, apply
from print_shop.setup.skill import groups_for, load
from print_shop.slicer.orca import (
    BedType,
    SlicerProfiles,
    parse_config_block,
    slice_model,
    with_bed_type,
)
from tests.fakes import DATA, STORE_PROFILES
from tests.fixtures import write_stl
from tests.test_graph import needs_orca
from tests.test_questions import intake

#: The alias the PR's proposals are recorded with; `crew-code`, without
#: thinking, is kept beside it for comparison.
ALIAS = os.environ.get("PRINT_SHOP_SETUP_MODEL", "crew-code-think")
RECORDED = DATA / "recorded" / "proposals"

#: The three parts, each with its recorded questions and the Sponsor's answers
#: (`tests/test_questions.py`, `tests/data/answers/`).
PARTS = ("vase", "bracket", "snap")


def _features(part: str, tmp_path: Path) -> tuple[Feature, ...]:
    stl = write_stl(part, tmp_path)
    return measure(stl, analyze(stl).bed_contact_area_mm2)


def _replay(part: str) -> Ask:
    reply = json.loads((RECORDED / ALIAS / f"{part}.json").read_text())
    return lambda system, user, schema: reply


def _proposal(
    part: str, tmp_path: Path, ask: Ask | None = None, asked: Intake | None = None
) -> Proposal:
    """The part's proposal: on its recorded intake, or on `asked` to isolate one rule."""
    proposer = Proposer(ask=ask or _replay(part))
    given = asked if asked is not None else intake(part, tmp_path)
    return proposer(given, _features(part, tmp_path), STORE_PROFILES)


def offering(*keys: str) -> Intake:
    """An intake that offered these settings and was answered in words, so nothing is set.

    Offering a setting loads its skill group; answering in words leaves it to
    the model, which is what a test of a bound or guard needs.
    """
    option = Option(label="x", settings=dict.fromkeys(keys, "1"), impact="", recommended=True,
                    why="")
    q = Question(feature="purpose", text="?", options=(option, option))
    return Intake(questions=(q,), answers=(Answer(other="see the test"),))


def _facts(part: str, tmp_path: Path, profiles: SlicerProfiles = STORE_PROFILES) -> PartFacts:
    return PartFacts(
        intake=intake(part, tmp_path),
        features=_features(part, tmp_path),
        machine=json.loads(profiles.machine.read_text()),
        process=json.loads(profiles.process.read_text()),
        filament=json.loads(profiles.filament.read_text()) if profiles.filament else {},
    )


# --------------------------------------------------------------------------
# The parts' features differ where it matters
# --------------------------------------------------------------------------


def test_the_three_parts_differ_in_the_features_a_setting_reads(tmp_path: Path) -> None:
    f = {part: {x.id: x.value for x in _features(part, tmp_path)} for part in PARTS}
    assert f["vase"]["height"] == 120 and f["vase"]["section"] == pytest.approx(2, abs=0.1)
    assert f["bracket"]["section"] > 4, "thick: a load path, not a skin"
    assert f["snap"]["overhang"] > 0 and f["snap"]["steepest_overhang"] == 90, "the hook"
    assert f["snap"]["bed_contact"] < 200, "a small footprint"
    assert f["vase"]["overhang"] == f["bracket"]["overhang"] == 0


# --------------------------------------------------------------------------
# The recorded proposals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("part", list(PARTS))
def test_every_change_names_its_feature_and_knowledge_and_stays_in_bounds(
    part: str, tmp_path: Path
) -> None:
    proposal = _proposal(part, tmp_path)
    facts = _facts(part, tmp_path)
    ids = {f.id for f in facts.features} | {"purpose"}
    assert proposal.reasoning, "the model says how it read the part"
    assert len(proposal.skill_groups) >= 2 and len(proposal.skill_sha256) == 64
    for c in proposal.changes:
        assert c.feature in ids, c
        if c.source is Source.ANSWER:
            assert c.reason.startswith("you chose '"), c
        else:
            assert c.knowledge in proposal.skill_groups, c
        assert c.reason.strip(), c
        assert check(c.key, c.value, facts.presets) == "", c
        assert c.preset == facts.preset(c.key), "shown against the preset's own value"


def test_the_proposals_differ_for_reasons_traced_to_the_parts(tmp_path: Path) -> None:
    """What the recordings of 2026-10-25 show, findings included (mqucifer/print-shop#10).

    The vase was asked its purpose and the Sponsor answered "display", whose
    option keeps the presets, so nothing changes. The bracket was asked
    nothing: the model judged its shape needed no question, so its 2 kg shelf
    was never asked about and nothing changed. That's a finding on the PR
    (on an earlier run it did ask), not a pass. The snap fit was asked its
    purpose (answered in words) and about its hook's overhang (tree support).
    """
    p = {part: _proposal(part, tmp_path) for part in PARTS}
    keys = {part: {c.key: c for c in prop.changes} for part, prop in p.items()}
    assert keys["vase"] == {} and keys["bracket"] == {}
    assert intake("bracket", tmp_path).questions == ()

    snap = keys["snap"]
    assert (snap["enable_support"].value, snap["enable_support"].source) == ("1", Source.ANSWER)
    assert snap["support_type"].value == "tree(auto)"
    assert snap["brim_type"].feature == "bed_contact", "128 mm2 on the plate"
    assert any("chamfer" in n for n in p["snap"].design_notes)
    model = [c for c in snap.values() if c.source is Source.MODEL]
    assert model and all(c.feature != "purpose" or "clip" in c.reason for c in model)


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize("part", list(PARTS))
def test_orca_slices_each_proposal_with_its_values(part: str, tmp_path: Path) -> None:
    """The proposed values are ones Orca takes, and the G-code says it used them."""
    proposal = _proposal(part, tmp_path)
    pei = with_bed_type(STORE_PROFILES, BedType.TEXTURED_PEI, tmp_path / "p")
    result = slice_model(
        write_stl(part, tmp_path), apply(pei, proposal, tmp_path / "proposed"), tmp_path / "o"
    )
    config = parse_config_block(result.gcode_path)
    for c in proposal.changes:
        assert config[c.key] == c.value, c


# --------------------------------------------------------------------------
# Bounds, refusals and the guards, without a model
# --------------------------------------------------------------------------


def _reply(*changes: dict[str, str], reasoning: str = "read") -> Ask:
    reply = {"reasoning": reasoning, "changes": list(changes), "design_notes": []}
    return lambda system, user, schema: reply


def test_a_change_shows_the_standard_under_the_sponsors_tuning(tmp_path: Path) -> None:
    """Elegoo PLA's standard is 210 C; the Sponsor tuned it to 220 (mqucifer/print-shop#8)."""
    hotter = {"key": "nozzle_temperature", "value": "230", "feature": "section",
              "knowledge": "temperature", "reason": "the flange's layer bonding"}
    change = _proposal("bracket", tmp_path, _reply(hotter), offering("nozzle_temperature")).get(
        "nozzle_temperature"
    )
    assert change is not None
    assert (change.standard, change.preset, change.value) == ("210", "220", "230")


def test_a_value_outside_its_bound_is_refused_not_clamped(tmp_path: Path) -> None:
    too_hot = {"key": "nozzle_temperature", "value": "260", "feature": "purpose",
               "knowledge": "temperature", "reason": "stronger"}
    fast = {"key": "outer_wall_speed", "value": "400", "feature": "height",
            "knowledge": "speed", "reason": "faster"}
    proposal = _proposal("bracket", tmp_path, _reply(too_hot, fast),
                         offering("nozzle_temperature", "outer_wall_speed"))
    assert proposal.get("nozzle_temperature") is None and proposal.get("outer_wall_speed") is None
    assert proposal.refused == (
        "nozzle_temperature=260: outside 190-240",
        "outer_wall_speed=400: outside 1-160",
    ), "the maker's range, and the Sponsor's tuned speed as the ceiling"


def test_normal_supports_are_replaced_by_tree(tmp_path: Path) -> None:
    normal = {"key": "support_type", "value": "normal(auto)", "feature": "overhang",
              "knowledge": "supports", "reason": "flat underside"}
    on = {"key": "enable_support", "value": "1", "feature": "overhang",
          "knowledge": "supports", "reason": "the hook"}
    proposal = _proposal("snap", tmp_path, _reply(on, normal), Intake())
    assert "support_type=normal(auto): not one of tree(auto)" in proposal.refused
    tree = proposal.get("support_type")
    assert tree is not None and (tree.value, tree.source) == ("tree(auto)", Source.GUARD)


def test_without_the_model_an_overhang_still_gets_tree_supports(tmp_path: Path) -> None:
    def down(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("LiteLLM is down")

    proposal = _proposal("snap", tmp_path, down, Intake())
    assert proposal.model.startswith("unavailable: LiteLLM is down")
    assert [(c.key, c.value, c.source) for c in proposal.changes] == [
        ("enable_support", "1", Source.GUARD),
        ("support_type", "tree(auto)", Source.GUARD),
    ]


def test_the_model_may_decide_an_overhang_needs_no_support(tmp_path: Path) -> None:
    off = {"key": "enable_support", "value": "0", "feature": "overhang",
           "knowledge": "supports", "reason": "the 1.2 mm hook bridges"}
    proposal = _proposal("snap", tmp_path, _reply(off), Intake())
    assert proposal.get("enable_support") is None, "the preset is already off: no change"
    assert [c.key for c in proposal.kept] == ["enable_support"], "and the decision is kept"


def test_the_prompt_carries_the_features_and_the_loaded_groups_bounds(tmp_path: Path) -> None:
    facts = _facts("snap", tmp_path)
    text = prompt(facts, load(groups_for(facts.intake, facts.features)))
    assert "overhang: 10 mm2" in text
    assert "support_type: preset normal(auto); allowed tree(auto)" in text
    assert "a clip: the hook snaps into a 1.2 mm ledge" in text, "the Sponsor's own words"
    assert "-> Tree support (enable_support=1, support_type=tree(auto))" in text
    assert "Skill groups read for this part: walls-shells, infill, supports" in text


def test_a_setting_outside_the_groups_read_is_refused(tmp_path: Path) -> None:
    """The vase has no overhang, so the supports group isn't read, nor its settings changed."""
    on = {"key": "enable_support", "value": "1", "feature": "purpose",
          "knowledge": "supports", "reason": "just in case"}
    proposal = _proposal("vase", tmp_path, _reply(on), Intake())
    assert proposal.refused == ("enable_support=1: its skill group wasn't read for this part",)


# --------------------------------------------------------------------------
# Recording, with the real model (on the Mac, once)
# --------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.parametrize("part", list(PARTS))
def test_record_the_models_proposal(part: str, tmp_path: Path) -> None:
    if os.environ.get("PRINT_SHOP_RECORD") != "1":
        pytest.skip("records only with PRINT_SHOP_RECORD=1")
    real = llm_ask(setup_llm(LLMConfig.from_settings(), {"PRINT_SHOP_SETUP_MODEL": ALIAS}))
    replies: list[dict[str, Any]] = []

    def recording(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        replies.append(real(system, user, schema))
        return replies[-1]

    proposal = _proposal(part, tmp_path, recording)
    out = RECORDED / ALIAS
    out.mkdir(parents=True, exist_ok=True)
    if proposal.model.startswith(("unavailable", "cut off")):
        # Kept beside the recordings, so a cut-off or an empty answer is on record.
        failed = {"model": proposal.model, "call": proposal.call}
        (out / f"{part}.failed.json").write_text(json.dumps(failed, indent=2) + "\n")
        pytest.fail(proposal.model)
    (out / f"{part}.json").write_text(json.dumps(replies[0], indent=2) + "\n")


def test_a_cut_off_reply_is_refused_and_its_cost_recorded(tmp_path: Path) -> None:
    """The Sponsor's rule for a new limit: record every signal; never retry silently."""
    info = CallInfo(model="crew-code-think", finish_reason="length", prompt_tokens=1417,
                    completion_tokens=16000, reasoning_tokens=15200, max_tokens=16000,
                    seconds=580.0)
    calls: list[str] = []

    def cut(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        calls.append(user)
        raise CutOff("cut off at max_tokens=16000 after 16000 tokens", info)

    proposal = _proposal("snap", tmp_path, cut, Intake())
    assert len(calls) == 1, "no retry"
    assert proposal.model == "cut off: cut off at max_tokens=16000 after 16000 tokens"
    assert proposal.call["finish_reason"] == "length"
    assert proposal.call["completion_tokens"] == 16000
    assert [c.source for c in proposal.changes] == [Source.GUARD, Source.GUARD]


def test_the_default_limit_follows_the_sponsors_rule() -> None:
    base = LLMConfig(api_key="sk-test")  # type: ignore[arg-type]
    config = setup_llm(base, {})
    assert (config.model, config.max_tokens) == ("crew-code-think", 16000)
    assert setup_llm(base, {"PRINT_SHOP_SETUP_MAX_TOKENS": "9000"}).max_tokens == 9000
