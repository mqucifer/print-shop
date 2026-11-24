"""A setting answered for one pose doesn't outlive a turn.

mqucifer/print-shop#258. Every part, question and answer is made up.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.state import AppliedOrientation, MeshReport, PlatePart
from print_shop.ingest.orientation import AXIS_ALIGNED, LAID, write_rotated
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.plate_job import still_holds
from print_shop.setup import Proposer
from print_shop.setup import turned as turned_module
from print_shop.setup.features import Feature
from print_shop.setup.intake import Answer, Intake, Option, Question, cli_asker
from print_shop.setup.loop import PrintLoop, _State, big
from print_shop.setup.loop import _seconds as loop_slice
from print_shop.setup.proposal import ORIENTATION, Proposal
from print_shop.setup.questions import PURPOSE, SEEN
from print_shop.setup.review import Review, ReviewRequest, cli_reviewer
from print_shop.setup.review import render as render_review
from print_shop.setup.turned import as_posed, held
from tests.fakes import CI_PROFILES, STORE_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.reviewing import flat, takes
from tests.test_job_e2e import CFG
from tests.test_print_loop import Model, _part, _pose
from tests.test_send_back import AnyPart
from tests.test_use_these import printed as printed_before
from tests.test_use_these import yours

WAIT_S = 30.0
FLIPPED = (180.0, 0.0, 0.0)

#: The model's question on the overhang, as its fake gives it.
OVERHANG = {"feature": "overhang", "text": "Does the underside need support?", "options": [
    {"label": "No support", "settings": [{"key": "enable_support", "value": "0"}],
     "impact": "bridges it", "recommended": True, "why": "made up"},
    {"label": "Tree supports", "settings": [{"key": "enable_support", "value": "1"}],
     "impact": "marks the underside"},
]}


def slab_down(directory: Path) -> Path:
    """The tee turned over, so as its file lays it the slab is on the plate and nothing
    overhangs. Its file's own pose turned 180,0,0 is the tee: 286 mm2 over air."""
    return write_rotated(write_stl("tee", directory), FLIPPED, directory / "slab.stl")


class Job:
    """A job through the daemon on fakes, and what each review and settings call had."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.ledger = Ledger(tmp_path / "ledger.sqlite")
        self.requests: list[ReviewRequest] = []
        self.settings_prompts: list[str] = []
        self.shown: list[str] = []

    def settings(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.settings_prompts.append(user)
        return {"reasoning": "", "changes": [], "design_notes": []}

    def run(
        self, stl: Path, answers: list[str], review: list[str],
        loop: PrintLoop | None = None, as_laid: bool = True, reviewed: bool = True,
    ) -> Row:
        typed, at_review = list(answers), list(review)
        person = cli_reviewer(lambda prompt: at_review.pop(0), self.shown.append)

        def reviewer(request: ReviewRequest) -> Review:
            self.requests.append(request)
            return person(request)

        (self.tmp_path / "work").mkdir(exist_ok=True)
        bridge = MemoryBridge()
        daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=self.tmp_path / "work",
            printer=FramePrinter(), slicer=AnyPart(), bridge=lambda cfg: bridge,
            ledger=self.ledger,
            proposer=Proposer(ask=self.settings,
                              ask_questions=lambda *_: {"questions": [OVERHANG]}),
            asker=cli_asker(lambda prompt: typed.pop(0), self.shown.append, lambda: None),
            reviewer=takes(reviewer) if reviewed else None, loop=loop,
        )
        with daemon:
            job_id = daemon.submit(stl, as_laid=as_laid)
            assert daemon.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
            bridge.press(CFG, "approve")
            finished = daemon.wait_for_finish(job_id, timeout_s=WAIT_S)
            assert finished.printed, finished.error
        row = self.ledger.get(job_id)
        assert row is not None and row.intake is not None
        return row

    @property
    def last(self) -> Proposal:
        return self.requests[-1].proposal

    @property
    def last_review(self) -> str:
        return render_review(self.requests[-1])


@pytest.fixture
def job(tmp_path: Path) -> Iterator[Job]:
    made = Job(tmp_path)
    yield made
    made.ledger.close()


def sent_to(pose: tuple[float, float, float], then: list[str] | None = None) -> list[str]:
    """What's typed at the review to send the file's own pose to this one, and go on."""
    others = [each for each in AXIS_ALIGNED if each != LAID]
    return ["orientation", str(others.index(pose) + 1), "a made-up reason", *(then or ["ok"])]


#: The questions' answers: handled, "No support", and the walls and the top are seen.
NO_SUPPORT = ["2", "1", "1"]
ON_ITS_SIDE = (90.0, 0.0, 0.0)


def turned(to: str = "180,0,0") -> str:
    return f"TURNED SINCE YOU ANSWERED: turned to {to} from 0,0,0, where you answered."
SET_ASIDE = ("These were said of the part as it lay when asked, so they aren't used now: 'Does "
             "the underside need support?', you said 'No support'. Send it back with "
             "orientation, or say what you want with a correction")


def supports(proposal: Proposal) -> tuple[str, str] | None:
    """Supports as the proposal has them, and who decided: (value, source), or None."""
    found = proposal.get("enable_support") or next(
        (c for c in proposal.kept if c.key == "enable_support"), None)
    return (found.value, found.source.value) if found is not None else None


def assert_guarded_and_noted(job: Job, row: Row, to: str = "180,0,0") -> None:
    """The guard's supports over the overhang it was turned onto, and the note."""
    assert supports(job.last) == ("1", "guard")
    assert job.last.get("support_type") is not None
    assert not any(c.key == "enable_support" for c in job.last.kept), "no longer kept as yours"
    assert f"{turned(to)} {SET_ASIDE}" in flat(job.last_review)
    assert row.proposal is not None
    assert f"{turned(to)} {SET_ASIDE}" in row.proposal.notes
    # The record keeps every answer as given.
    assert row.intake is not None and row.intake.answers[1].choice == 0
    assert row.intake.questions[1].options[0].settings == {"enable_support": "0"}


def test_not_turned_no_support_is_kept_as_yours(tmp_path: Path, job: Job) -> None:
    row = job.run(slab_down(tmp_path), NO_SUPPORT, ["ok"])
    assert row.rotation_deg == LAID
    assert supports(job.last) == ("0", "answer")
    assert "TURNED SINCE" not in job.last_review
    assert "you said 'No support'" not in job.last_review
    assert "Does the underside need support? -> No support" in job.settings_prompts[-1]


def test_sent_back_onto_an_overhang_the_guard_decides_again_and_says_why(
    tmp_path: Path, job: Job
) -> None:
    row = job.run(slab_down(tmp_path), NO_SUPPORT, sent_to(FLIPPED))
    assert row.rotation_deg == FLIPPED
    assert supports(job.requests[0].proposal) == ("0", "answer"), "before the turn"
    assert_guarded_and_noted(job, row)
    first, after = job.settings_prompts
    assert "-> No support (enable_support=0)" in first
    assert "No support" not in after, "the settings call isn't told the old answer"
    assert "What is this part for?" in after, "what it's for holds in any pose"


def test_turned_back_its_no_support_again(tmp_path: Path, job: Job) -> None:
    others = [each for each in AXIS_ALIGNED if each != FLIPPED]
    back = ["orientation", str(others.index(LAID) + 1), "back again", "ok"]
    row = job.run(slab_down(tmp_path), NO_SUPPORT, sent_to(FLIPPED, back))
    assert row.rotation_deg == LAID
    assert supports(job.requests[1].proposal) == ("1", "guard"), "while it was turned"
    assert supports(job.last) == ("0", "answer")
    assert "TURNED SINCE" not in job.last_review
    # Its settings call in the file's own pose isn't made again: the one made there stands.
    [first, _] = job.settings_prompts
    assert "-> No support (enable_support=0)" in first


def test_a_pose_used_from_a_past_print_sets_it_aside_too(tmp_path: Path, job: Job) -> None:
    stl = slab_down(tmp_path)
    printed_before(job.ledger, "past-job", stl,
                   (yours(ORIENTATION, "180,0,0", "it prints cleaner so"),), rotation=FLIPPED)
    row = job.run(stl, NO_SUPPORT, ["use 1", "ok", "ok"])
    assert row.rotation_deg == FLIPPED
    assert any(c.key == ORIENTATION and c.used is not None for c in row.corrections or ())
    assert_guarded_and_noted(job, row)


def test_the_print_loops_turn_sets_it_aside(tmp_path: Path, job: Job) -> None:
    model = Model([("propose", {"rotation": [90, 0, 0], "settings": [],
                                "reasoning": "made up"})])
    row = job.run(slab_down(tmp_path), NO_SUPPORT, ["ok"],
                  PrintLoop(chat=model, person=lambda question: "yes"), as_laid=False)
    assert row.rotation_deg == ON_ITS_SIDE
    assert_guarded_and_noted(job, row, "90,0,0")
    weighed = model.shown[0][1]["content"]
    assert ("Said of the part as it lies at 0,0,0, so they hold in that pose only: 'Does the "
            "underside need support?', you said 'No support'. In any other pose they aren't "
            "decided") in weighed
    # The turn's supports are a big change, said on its proposal, as no answer decides them there.
    assert row.proposal is not None
    [turn] = [c for c in row.proposal.suggested[0].proposed if c.key == ORIENTATION]
    assert "supports on, for 96.9 mm2 that needs them" in turn.reading


def test_the_print_loops_settings_for_its_turn_arent_held_by_an_answer_from_before_it(
    tmp_path: Path, job: Job
) -> None:
    """Its own supports for its turn are its proposal there, not refused as already answered."""
    on = {"key": "enable_support", "value": "1", "reason": "the side overhangs",
          "feature": "overhang", "knowledge": "supports"}
    model = Model([("propose", {"rotation": [90, 0, 0], "settings": [on],
                                "reasoning": "made up"})])
    row = job.run(slab_down(tmp_path), NO_SUPPORT, ["ok"],
                  PrintLoop(chat=model, person=lambda question: "yes"), as_laid=False)
    assert row.proposal is not None
    [loops] = [s for s in row.proposal.suggested if s.loop]
    assert not any("you answered it at the intake" in r for r in loops.refused), loops.refused
    assert [c.key for c in loops.once_turned] == ["enable_support"]


def test_the_print_loops_turn_with_no_review_asks_about_the_supports_and_sets_it_aside(
    tmp_path: Path, job: Job
) -> None:
    """With no review the loop asks before a big change: turned, no answer decides the supports
    there, so they're one."""
    model = Model(
        [("ask", {"question": "Lay it on its side?", "reason": "made up",
                  "rotation": [90, 0, 0]})],
        [("propose", {"rotation": [90, 0, 0], "settings": [], "reasoning": "made up"})],
    )
    asked: list[str] = []

    def person(question: str) -> str:
        asked.append(question)
        return "yes"

    row = job.run(slab_down(tmp_path), NO_SUPPORT, [], PrintLoop(chat=model, person=person),
                  as_laid=False, reviewed=False)
    [question] = asked
    assert "supports on, for 96.9 mm2 that needs them" in question
    assert row.rotation_deg == ON_ITS_SIDE
    assert row.proposal is not None
    assert supports(row.proposal) == ("1", "guard")
    assert f"{turned('90,0,0')} {SET_ASIDE}" in row.proposal.notes


def test_the_loops_fallback_turn_sets_aside_an_answer_for_supports_too(
    tmp_path: Path, job: Job
) -> None:
    """The tee stands on its pillar as its file lays it, and "Tree supports" was said of it so.
    The loop can't be reached, and the single call turns it over, slab down: nothing overhangs
    there, so with the answer set aside the supports are the preset's, off."""

    def down(messages: Any, tools: Any) -> dict[str, Any]:
        raise RuntimeError("no model here")

    row = job.run(write_stl("tee", tmp_path), ["2", "2", "1"], [],
                  PrintLoop(chat=down, person=lambda question: "no"), as_laid=False,
                  reviewed=False)
    assert row.rotation_deg == FLIPPED, "the single call turned it"
    assert row.proposal is not None
    assert supports(row.proposal) is None, "no answer and no overhang: the preset's"
    assert ("TURNED SINCE YOU ANSWERED: turned to 180,0,0 from 0,0,0, where you answered. These "
            "were said of the part as it lay when asked, so they aren't used now: 'Does the "
            "underside need support?', you said 'Tree supports'.") in " ".join(row.proposal.notes)
    assert "Tree supports" not in job.settings_prompts[-1]


# --------------------------------------------------------------------------
# The rule, at its functions
# --------------------------------------------------------------------------


def no_support(*more: Question) -> Intake:
    """Purpose, "No support" on the overhang, and the faces seen, as the file lays it."""
    overhang = Question(feature="overhang", text="Support it?", options=(
        Option(label="No support", settings={"enable_support": "0"}, impact="made up"),
        Option(label="Supports", settings={"enable_support": "1"}, impact="made up"),
    ))
    questions = (PURPOSE, overhang, *more, SEEN.model_copy(update={"pose": LAID}))
    return Intake(questions=questions, answers=tuple(Answer(choice=0) for _ in questions))


def test_turned_what_its_for_and_whats_seen_stay_and_the_shape_answer_is_set_aside() -> None:
    given = no_support()
    kept = held(given)
    assert [q.feature for q in kept.intake.questions] == ["purpose", "seen"]
    assert kept.aside == ("'Support it?', you said 'No support'",)
    assert [q.feature for q in held(given, seen_kept=False).intake.questions] == ["purpose"]
    assert given.questions[1].feature == "overhang", "the record is whole"


def test_a_setting_for_a_pose_is_set_aside_from_an_answer_that_holds_in_any_pose() -> None:
    closer = Question(feature="volume", text="Solid or light?", options=(
        Option(label="solid", settings={"sparse_infill_density": "40%", "brim_type": "no_brim"},
               impact="made up"),
        Option(label="light", settings={}, impact="made up"),
    ))
    kept = held(no_support(closer))
    [volume] = [q for q in kept.intake.questions if q.feature == "volume"]
    assert volume.options[0].settings == {"sparse_infill_density": "40%"}
    assert "brim_type=no_brim, from 'solid'" in kept.aside


def test_words_read_back_and_confirmed_are_held_to_the_pose_as_an_option_is() -> None:
    """mqucifer/print-shop#276: confirmed words are a decision like a chosen option."""
    words = Answer(other="a hook: four walls and no supports",
                   sets={"wall_loops": "4", "enable_support": "0"})
    on_shape = Answer(other="leave it bare", sets={"enable_support": "0"})
    given = no_support()
    given = given.model_copy(update={"answers": (words, on_shape, given.answers[2])})
    kept = held(given)
    assert kept.intake.answers[0].sets == {"wall_loops": "4"}
    [(_, said)] = [c for c in kept.intake.chosen() if c[0].feature == "purpose"]
    assert said.settings == {"wall_loops": "4"}
    assert kept.aside == ("enable_support=0, from 'a hook: four walls and no supports'",
                          "'Support it?', you said 'leave it bare'")
    assert given.answers[0].sets["enable_support"] == "0", "the record is whole"


def test_a_plates_words_are_held_to_the_pose_too() -> None:
    words = Answer(other="a hook, no supports", sets={"enable_support": "0"})
    given = Intake(questions=(PURPOSE,), answers=(words,))
    mesh = MeshReport(is_manifold=True, volume_mm3=1, surface_area_mm2=1, bbox_mm=(1, 1, 1),
                      overhang_area_mm2=0)
    part = PlatePart(n=1, name="cap.stl", given="cap.stl", stl="cap.stl", sha256="0", mesh=mesh,
                     applied=AppliedOrientation(rotation_deg=FLIPPED))
    judged, note = still_holds(given, (part,), (LAID,))
    assert judged.answers[0].sets == {}
    assert "enable_support=0, from 'a hook, no supports'" in note


def test_not_turned_or_turned_back_every_answer_is_read_as_given() -> None:
    given = no_support()
    assert as_posed(given, LAID, LAID) == (given, "")
    assert as_posed(given, FLIPPED, FLIPPED) == (given, "")
    assert as_posed(given, None, FLIPPED) == (given, ""), "not known where it was asked"
    judged, note = as_posed(given, LAID, FLIPPED)
    assert note.startswith("TURNED SINCE YOU ANSWERED: turned to 180,0,0 from 0,0,0")
    assert [q.feature for q in judged.questions] == ["purpose", "seen"]


def test_the_guard_decides_again_once_the_answer_is_set_aside() -> None:
    profiles = CI_PROFILES
    features = (Feature(id="overhang", value=300.0, unit="mm2", says="made up"),)
    given = no_support()
    as_given = Proposer()(given, features, profiles)
    assert supports(as_given) == ("0", "answer")
    turned_over = Proposer()(as_posed(given, LAID, FLIPPED)[0], features, profiles)
    assert supports(turned_over) == ("1", "guard")


def test_seen_is_named_as_the_questions_name_it() -> None:
    assert SEEN.feature == turned_module.SEEN


class Settings(AnyPart):
    """The recorded slice, keeping the supports each slice was made with."""

    def __init__(self) -> None:
        super().__init__()
        self.supports: list[str] = []

    def __call__(self, stl_path: Path | str, profiles: Any, outdir: Path | str) -> Any:
        process = json.loads(Path(profiles.process).read_text())
        self.supports.append(str(process.get("enable_support", "")))
        return super().__call__(stl_path, profiles, outdir)


def test_the_loop_reads_an_answer_for_a_pose_in_that_pose_only(tmp_path: Path) -> None:
    """The tee as the file lays it has its slab over the air; the answer was said of it so."""
    given = no_support()
    supported = given.model_copy(update={"answers": (
        given.answers[0], Answer(choice=1), given.answers[2])})
    assert json.loads(STORE_PROFILES.process.read_text())["enable_support"] == "0", "the preset"
    slicer = Settings()
    part = dataclasses.replace(_part(tmp_path, "tee", intake=supported), slicer=slicer)
    over = next(c for c in part.candidates
                if c.rotation_deg != LAID and c.overhang_area_mm2 > 0)
    loop_slice(part, _State(), _pose(part, LAID), {})
    loop_slice(part, _State(), over, {})
    assert slicer.supports == ["1", "0"], "sliced with the answer only where it was given"

    part = _part(tmp_path, "tee", intake=no_support())
    assert big(part, _pose(part, LAID), {}, None, None) == [], "your answer decides it here"
    assert "supports" in [w for w, _ in big(part, over, {}, None, None)]
