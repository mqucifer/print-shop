"""The intake's questions: proposed from the part's features, limited, with impacts.

mqucifer/print-shop#10, the Sponsor's ask of 2026-10-25: questions with a few
options and the impact of each, not an open form. The model's questions for
the three test parts were recorded once (`llm`, `PRINT_SHOP_RECORD=1`) and are
replayed here through the same bounds; the options' time and filament come
from the real Orca (`slicer`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from print_shop.expert.client import LLMConfig
from print_shop.ingest.analyzer import analyze
from print_shop.setup import Proposer
from print_shop.setup.bounds import check
from print_shop.setup.features import Feature, measure
from print_shop.setup.intake import Answer, Estimate, Intake, Question, parse, render, replay_asker
from print_shop.setup.model import Ask, llm_ask, setup_llm
from print_shop.setup.proposal import Change, PartFacts, Proposal, Source, apply
from print_shop.setup.questions import MAX_SLICES, estimate
from print_shop.slicer.orca import BedType, slice_model, with_bed_type
from tests.fakes import DATA, STORE_PROFILES
from tests.fixtures import write_stl
from tests.test_graph import needs_orca

PARTS = ("vase", "bracket", "snap")
ALIAS = os.environ.get("PRINT_SHOP_SETUP_MODEL", "crew-code-think")
RECORDED = DATA / "recorded" / "questions" / ALIAS
ANSWERS = DATA / "answers"


def features(part: str, tmp_path: Path) -> tuple[Feature, ...]:
    stl = write_stl(part, tmp_path)
    return measure(stl, analyze(stl).bed_contact_area_mm2)


def replay(path: Path) -> Ask:
    reply = json.loads(path.read_text())
    return lambda system, user, schema: reply


def questions(part: str, tmp_path: Path) -> tuple[Question, ...]:
    asked = Proposer(ask_questions=replay(RECORDED / f"{part}.json")).questions(
        features(part, tmp_path), STORE_PROFILES
    )
    return asked.questions


def intake(part: str, tmp_path: Path) -> Intake:
    """The part's recorded questions, answered as the Sponsor would (tests/data/answers/)."""
    qs = questions(part, tmp_path)
    return Intake(questions=qs, answers=replay_asker(ANSWERS / f"{part}.json")(qs))


def _presets() -> dict[str, dict[str, Any]]:
    return {k: json.loads(getattr(STORE_PROFILES, k).read_text())
            for k in ("machine", "process", "filament")}


# --------------------------------------------------------------------------
# The recorded questions
# --------------------------------------------------------------------------


@pytest.mark.parametrize("part", PARTS)
def test_the_questions_are_few_limited_and_each_has_one_recommendation(
    part: str, tmp_path: Path
) -> None:
    qs = questions(part, tmp_path)
    ids = {f.id for f in features(part, tmp_path)} | {"purpose"}
    assert len(qs) <= 4
    for q in qs:
        assert q.feature in ids, q
        assert 2 <= len(q.options) <= 4, q
        assert sum(o.recommended for o in q.options) == 1 and q.options[q.recommended].why
        for o in q.options:
            assert o.impact.strip(), o
            assert all(check(k, v, _presets()) == "" for k, v in o.settings.items()), o


def test_the_parts_get_different_questions_from_their_features(tmp_path: Path) -> None:
    asked = {part: questions(part, tmp_path) for part in PARTS}
    texts = {part: " ".join(q.text for q in qs) for part, qs in asked.items()}
    assert len(set(texts.values())) == 3
    snap = asked["snap"]
    assert any(q.feature in ("overhang", "steepest_overhang", "bridge") for q in snap) or any(
        "enable_support" in o.settings for q in snap for o in q.options
    ), "the hook's overhang is asked about"


# --------------------------------------------------------------------------
# Bounds, and when there's nothing to ask
# --------------------------------------------------------------------------


def _facts(part: str, tmp_path: Path) -> PartFacts:
    p = _presets()
    return PartFacts(intake=Intake(), features=features(part, tmp_path), machine=p["machine"],
                     process=p["process"], filament=p["filament"])


def _reply(*qs: dict[str, Any]) -> Ask:
    return lambda system, user, schema: {"questions": list(qs)}


def _option(label: str, recommended: bool = False, **settings: str) -> dict[str, Any]:
    return {"label": label, "settings": [{"key": k, "value": v} for k, v in settings.items()],
            "impact": f"{label} impact", "recommended": recommended, "why": "because"}


def test_an_option_out_of_bounds_is_dropped_and_so_is_a_question_left_with_one(
    tmp_path: Path,
) -> None:
    hot = {"feature": "purpose", "text": "How hot?", "options": [
        _option("cool", nozzle_temperature="200"), _option("molten", nozzle_temperature="300")]}
    walls = {"feature": "section", "text": "How strong?", "options": [
        _option("light", wall_loops="2"), _option("strong", True, wall_loops="4"),
        _option("silly", wall_loops="40")]}
    asked = Proposer(ask_questions=_reply(hot, walls)).questions(
        features("bracket", tmp_path), STORE_PROFILES
    )
    assert [q.text for q in asked.questions] == ["How strong?"]
    assert [o.label for o in asked.questions[0].options] == ["light", "strong"]
    assert "'molten' in 'How hot?': nozzle_temperature=300: outside 190-240" in asked.note
    assert "'How hot?': fewer than 2 options in bounds" in asked.note


def test_exactly_one_option_is_recommended_whatever_the_model_marks(tmp_path: Path) -> None:
    none = {"feature": "purpose", "text": "What is it for?",
            "options": [_option("show"), _option("use")]}
    asked = Proposer(ask_questions=_reply(none)).questions(
        features("vase", tmp_path), STORE_PROFILES
    )
    assert [o.recommended for o in asked.questions[0].options] == [True, False]


def test_a_part_may_get_no_questions_and_says_why(tmp_path: Path) -> None:
    asked = Proposer(ask_questions=_reply()).questions(features("vase", tmp_path), STORE_PROFILES)
    assert asked.questions == ()
    assert asked.note == "no questions: the part's features left nothing to ask"
    assert Proposer().questions(features("vase", tmp_path), STORE_PROFILES).note == (
        "no questions: no model was asked"
    )


def test_times_are_never_the_models_and_a_failed_slice_says_no_estimate() -> None:
    q = Question(feature="purpose", text="?", options=tuple(
        o for o in (_q_option("a"), _q_option("b"))))
    calls: list[dict[str, str]] = []

    def slicer(settings: dict[str, str]) -> Estimate | None:
        calls.append(settings)
        return None if settings.get("wall_loops") == "b" else Estimate(
            print_time_s=600, filament_g=3.0, filament_cm3=2.4)

    priced = estimate((q,) * 7, slicer)
    assert priced[0].options[0].estimate == Estimate(print_time_s=600, filament_g=3.0,
                                                     filament_cm3=2.4)
    assert priced[0].options[1].estimate is None
    assert "no estimate" in priced[0].options[1].line()
    assert len(calls) == MAX_SLICES, "at most MAX_SLICES slices per print"


def _q_option(name: str) -> Any:
    from print_shop.setup.intake import Option

    return Option(label=name, settings={"wall_loops": name}, impact="x",
                  recommended=name == "a", why="y" if name == "a" else "")


# --------------------------------------------------------------------------
# Asking and answering
# --------------------------------------------------------------------------


def test_the_questions_are_shown_numbered_with_impacts_and_other(tmp_path: Path) -> None:
    qs = questions("snap", tmp_path)
    text = render(qs)
    assert text.startswith("A few questions about this part, from its shape:\n1. ")
    assert "   1) " in text and "   o) other: say it in your own words" in text
    assert "[recommended: " in text


def test_an_answer_is_a_number_or_other_words(tmp_path: Path) -> None:
    q = questions("snap", tmp_path)[0]
    assert parse("2", q) == Answer(choice=1)
    assert parse("o it holds a towel", q) == Answer(other="it holds a towel")
    assert isinstance(parse("9", q), str) and isinstance(parse("o", q), str)


def test_a_recording_beyond_its_answers_takes_the_recommended(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [{"choice": 1}]}))
    qs = questions("snap", tmp_path)
    answers = replay_asker(path)(qs)
    assert answers[0] == Answer(choice=0)
    for q, a in zip(qs[1:], answers[1:], strict=True):
        assert a.choice == q.recommended and "not in the recording" in a.other


# --------------------------------------------------------------------------
# With the real Orca, and recording with the real model (on the Mac)
# --------------------------------------------------------------------------


@pytest.mark.slicer
@needs_orca
def test_orca_prices_each_option_and_they_differ(tmp_path: Path) -> None:
    stl = write_stl("snap", tmp_path)
    pei = with_bed_type(STORE_PROFILES, BedType.TEXTURED_PEI, tmp_path / "p")

    def slice_with(settings: dict[str, str]) -> Estimate | None:
        out = tmp_path / str(len(list(tmp_path.glob("o*"))))
        changes = tuple(Change(key=k, preset="", value=v, reason="", source=Source.ANSWER)
                        for k, v in settings.items())
        r = slice_model(stl, apply(pei, Proposal(changes=changes), out / "p"), out)
        return Estimate(print_time_s=r.print_time_s, filament_g=r.filament_g,
                        filament_cm3=r.filament_cm3)

    priced = estimate(questions("snap", tmp_path), slice_with)
    estimates = [o.estimate for q in priced for o in q.options]
    assert all(e is not None for e in estimates)
    assert len({e.print_time_s for e in estimates if e}) > 1, "the options cost differently"


@pytest.mark.llm
@pytest.mark.parametrize("part", PARTS)
def test_record_the_models_questions(part: str, tmp_path: Path) -> None:
    if os.environ.get("PRINT_SHOP_RECORD") != "1":
        pytest.skip("records only with PRINT_SHOP_RECORD=1")
    real = llm_ask(setup_llm(LLMConfig.from_settings(), {"PRINT_SHOP_SETUP_MODEL": ALIAS}))
    replies: list[dict[str, Any]] = []

    def recording(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        replies.append(real(system, user, schema))
        return replies[-1]

    asked = Proposer(ask_questions=recording).questions(features(part, tmp_path), STORE_PROFILES)
    RECORDED.mkdir(parents=True, exist_ok=True)
    if not replies:
        (RECORDED / f"{part}.failed.json").write_text(
            json.dumps({"note": asked.note, "call": asked.call}, indent=2) + "\n")
        pytest.fail(asked.note)
    (RECORDED / f"{part}.json").write_text(json.dumps(replies[0], indent=2) + "\n")
