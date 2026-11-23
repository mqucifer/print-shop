"""What a plate of several parts gives the review's "use these" (mqucifer/print-shop#109,
mqucifer/print-shop#211).

The plates are printed through the real daemon on fakes, so each row is as the print side writes
it, and the offer is then made to one part. Everything is made up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.plate import SEQUENCE, plate_job, row_id
from print_shop.setup.offer import Item, Offering, offering
from print_shop.setup.proposal import PAUSE, Proposal
from tests.test_plate_job import Rig, _caps
from tests.test_use_these import this, yours


def _plate(tmp_path: Path, *typed: str, count: int = 3) -> tuple[Rig, str, list[Path]]:
    rig = Rig(tmp_path, *typed)
    caps = _caps(tmp_path)[:count]
    with rig.daemon:
        job = rig.gated(caps)
        assert rig.press("approve", job).printed
    return rig, job, caps


def _answered(ledger: Ledger, job: str, *failed: int, symptom: Symptom = Symptom.OTHER) -> None:
    rows = ledger.job_rows(job)
    if rows[0].ended_at is None:
        ledger.record_ended(job, 9)
    for n in range(1, len(rows) + 1):
        ledger.record_answer(row_id(job, n), Answer(
            result=Result.FAILED if n in failed else Result.SUCCESS,
            symptom=symptom if n in failed else Symptom.NONE,
            note="made up" if n in failed else "",
        ))


def _items(found: Offering) -> dict[str, Item]:
    return {item.key: item for item in found.past.items} if found.past is not None else {}


def _ticked(found: Offering) -> list[str]:
    return [item.key for item in found.ticked(Proposal(), "0,0,0")]


def test_a_plate_is_one_print_shown_by_the_part_most_like_this_one(tmp_path: Path) -> None:
    rig, job, caps = _plate(tmp_path, "wall_loops=3 because it clips", "ok")
    _answered(rig.ledger, job)
    found = offering(rig.ledger, this(caps[1]))
    assert len(found.options) == 1, "three rows, one print"
    past = found.past
    assert past is not None and (past.job_id, past.same_file) == (f"{job}-2", True)
    assert any("part 2 of 3 on one plate, printed by object" in d for d in past.differs)
    assert _ticked(found) == ["wall_loops"]


@pytest.mark.parametrize("typed", [
    "print_sequence=by layer", "print_sequence=by object because they string",
])
def test_a_plates_order_is_never_offered(tmp_path: Path, typed: str) -> None:
    """By object, used on one part with a pause, would lose the pause."""
    rig, job, caps = _plate(tmp_path, f"wall_loops=3; {typed}", "ok")
    _answered(rig.ledger, job)
    items = _items(offering(rig.ledger, this(caps[0])))
    assert "wall_loops" in items and SEQUENCE not in items


def test_a_plate_with_only_its_order_gives_nothing_to_use(tmp_path: Path) -> None:
    rig, job, caps = _plate(tmp_path, "print_sequence=by layer", "ok")
    _answered(rig.ledger, job)
    assert offering(rig.ledger, this(caps[0])).options == ()


def test_a_plates_pause_isnt_ticked_and_can_be_used_by_number(tmp_path: Path) -> None:
    rig, job, caps = _plate(
        tmp_path, "print_sequence=by layer; colour_change=3 because a swap", "ok")
    _answered(rig.ledger, job)
    found = offering(rig.ledger, this(caps[1]))
    pause = _items(found)[PAUSE]
    assert pause.value == "3" and not pause.blocked, "the height its G-code paused after"
    assert any("may have been another part's" in why for why in pause.doubts)
    assert PAUSE not in _ticked(found)
    used = found.use([1], Proposal(), "0,0,0")
    assert not isinstance(used, str) and [i.key for i in used] == [PAUSE]


def test_a_failed_part_unticks_the_settings_it_shared(tmp_path: Path) -> None:
    rig, job, caps = _plate(tmp_path, "brim_type=outer_only because it lifted", "ok")
    _answered(rig.ledger, job, 2, symptom=Symptom.ADHESION)
    found = offering(rig.ledger, this(caps[0]))
    assert found.past is not None and found.past.job_id == job, "this file's own row succeeded"
    doubts = _items(found)["brim_type"].doubts
    assert any("part 2 of 3) of that plate failed with adhesion" in why for why in doubts)
    assert _ticked(found) == []


def test_a_flawed_part_beside_it_unticks_nothing(tmp_path: Path) -> None:
    rig, job, caps = _plate(tmp_path, "brim_type=outer_only because it lifted", "ok")
    if rig.ledger.job_rows(job)[0].ended_at is None:
        rig.ledger.record_ended(job, 9)
    for n, symptom in enumerate((Symptom.NONE, Symptom.ADHESION, Symptom.NONE), 1):
        rig.ledger.record_answer(row_id(job, n), Answer(result=Result.SUCCESS, symptom=symptom))
    assert _ticked(offering(rig.ledger, this(caps[0]))) == ["brim_type"], "failed is by result"


def test_a_part_turned_on_the_plate_isnt_a_later_correction_of_its_twin(tmp_path: Path) -> None:
    cap = _caps(tmp_path)[0]
    rig = Rig(tmp_path, "orientation", "2", "2", "the mark goes on the side", "ok")
    with rig.daemon:
        job = rig.gated([cap, cap])
        assert rig.press("approve", job).printed
    found = offering(rig.ledger, this(cap))
    assert len(found.options) == 1
    for item in _items(found).values():
        assert not any("corrected since" in why for why in item.doubts)


def test_each_rows_context_is_its_parts_own(tmp_path: Path) -> None:
    caps = _caps(tmp_path)
    caps[1].with_name("cap-mid.part.json").write_text(
        '{"fits": [{"name": "over the post", "play": 0.2}]}')
    rig = Rig(tmp_path, "wall_loops=3 because it clips", "ok")
    with rig.daemon:
        job = rig.gated(caps)
        assert rig.press("approve", job).printed
    one, two, _ = rig.ledger.job_rows(job)
    assert one.proposal is not None and two.proposal is not None
    assert one.proposal.context[0].startswith("none:") and one.proposal.context_from == ""
    assert any(line.startswith("fit: ") for line in two.proposal.context)
    assert two.proposal.context_from.endswith("cap-mid.part.json")

    from print_shop.setup.context import read_context

    found = offering(rig.ledger, this(caps[1], context=read_context(caps[1])))
    assert found.past is not None and not any("weren't these" in d for d in found.past.differs)


def test_what_was_cleared_stays_cleared_whichever_part_stands_for_the_plate(
    tmp_path: Path,
) -> None:
    rig, job, caps = _plate(tmp_path, "wall_loops=3 because it clips", "ok")
    first = offering(rig.ledger, this(caps[0])).clear([1])
    assert not isinstance(first, str)
    carried = first.carried_to(offering(rig.ledger, this(caps[2])))
    assert carried.past is not None
    assert plate_job(carried.past.job_id) == job and carried.past.job_id != job
    assert _ticked(carried) == []


def test_a_plates_review_says_it_isnt_offered_use_these(tmp_path: Path) -> None:
    rig, _, caps = _plate(tmp_path, "use", "wall_loops=3 because it clips", "ok")
    said = " ".join(rig.shown[0].split())
    assert "USE THESE: not offered for a plate of several parts yet" in said
    assert "use what decided that print" not in said
    assert "not offered for a plate of several" in rig.shown[1], "typed anyway, it says why"


@pytest.mark.parametrize("typed", ["use", "use 1", "use; wall_loops=4"])
def test_use_doesnt_get_round_the_pause_refusal(tmp_path: Path, typed: str) -> None:
    """No plate is given an offer today. If one ever is, a pause used is refused as one typed."""
    from dataclasses import replace

    from print_shop.setup.offer import Past
    from print_shop.setup.proposal import Source, Used
    from print_shop.setup.review import parse_answer

    rig, job, _ = _plate(tmp_path)
    [request] = rig.reviews
    assert request.plate is not None and request.plate.by_object
    pause = Item(key=PAUSE, value="3", preset="", reason="a swap", fits="",
                 used=Used(job_id=job, stl_name="cap.stl", by=Source.SPONSOR))
    past = Past(job_id=job, stl_name="cap.stl", on="22 Nov 2026", came_out="success",
                outcome="success", why="made up", measures=(), differs=(), items=(pause,))
    said = parse_answer(typed, replace(request, offering=Offering(options=(past,))))
    assert isinstance(said, str) and "Orca writes no pause when it prints by object" in said


def test_two_parts_turned_on_one_plate_dont_correct_each_other(tmp_path: Path) -> None:
    cap = _caps(tmp_path)[0]
    rig = Rig(tmp_path, "orientation", "1", "2", "the mark goes on the side",
              "orientation", "2", "4", "the other mark goes on the other side", "ok")
    with rig.daemon:
        job = rig.gated([cap, cap])
        assert rig.press("approve", job).printed
    _answered(rig.ledger, job, 2, symptom=Symptom.STRINGING)
    found = offering(rig.ledger, this(cap))
    assert found.past is not None and found.past.job_id == job, "the one that didn't fail"
    for item in found.past.items:
        assert not any("corrected since" in why for why in item.doubts)


def test_one_part_is_offered_a_plates_settings_at_its_own_review(tmp_path: Path) -> None:
    from print_shop.setup import Proposer
    from print_shop.setup.intake import Answer as IntakeAnswer
    from print_shop.setup.review import Review, ReviewRequest
    from tests.fakes import MemoryBridge
    from tests.plates import profiles
    from tests.test_job_e2e import CFG
    from tests.test_review import _model, _questions

    rig, job, caps = _plate(tmp_path, "top_shell_layers=6 because it's seen", "ok")
    seen: list[ReviewRequest] = []

    def reviewer(request: ReviewRequest) -> Review:
        seen.append(request)
        return Review(stop=True)

    def one_slice(stl: object, at: object, out: object) -> object:
        from print_shop.slicer.arrange import ToPlace, slice_parts

        part = ToPlace(1, "cap.stl", Path(str(stl)), 4.0)
        named = part.stl.with_name("part1_cap.stl")
        named.write_bytes(part.stl.read_bytes())
        result, _ = slice_parts((ToPlace(1, "cap.stl", named, 4.0),), at,  # type: ignore[arg-type]
                                Path(str(out)), rig.orca)
        return result

    bridge = MemoryBridge()
    alone = Daemon(cfg=CFG, profiles=profiles(tmp_path / "alone"), workdir=rig.work, printer=None,
                   slicer=one_slice, bridge=lambda cfg: bridge,  # type: ignore[arg-type]
                   ledger=rig.ledger, proposer=Proposer(ask=_model, ask_questions=_questions),
                   asker=lambda qs: tuple(IntakeAnswer(choice=q.recommended) for q in qs),
                   reviewer=reviewer)
    with alone:
        run = alone.submit(caps[1], as_laid=True)
        alone.wait_for_gate(run, timeout_s=30)
        alone.wait_for_finish(run, timeout_s=30)
    assert seen and seen[0].offering is not None and seen[0].offering.past is not None
    assert seen[0].offering.past.job_id == f"{job}-2"
    assert "top_shell_layers" in [i.key for i in seen[0].offering.past.items]


def test_a_later_print_of_one_part_still_counts_as_corrected_since(tmp_path: Path) -> None:
    from tests.test_use_these import printed

    rig, job, caps = _plate(tmp_path, "wall_loops=3 because it clips", "ok")
    printed(rig.ledger, "later", caps[0], (yours("wall_loops", "4"),))
    found = offering(rig.ledger, this(caps[0]))
    plate = next(n for n, p in enumerate(found.options, 1) if plate_job(p.job_id) == job)
    picked = found.pick(plate)
    assert not isinstance(picked, str)
    assert any("corrected since, to 4" in why for why in _items(picked)["wall_loops"].doubts)
