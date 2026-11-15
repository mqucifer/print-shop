"""The end checks on planted parts, in the bench's real images (mqucifer/print-shop#150): the
arm found from a place, `fit-on --from clear`, `bend`'s "what would pass", and a scripted run
sent back for a clip that bends too far and stopped for approval with one that takes it. Needs
both images, `scripts/bench.py build` and `build cae`, and no model.

Everything is made from scratch by `cae_cases/make_fixtures.py`: the box with one snap clip and
its lid, the same box with the clip half as long, and a channel with a catch and the strip
pushed past it. Right answers come from arithmetic, or from `tests/test_cae.py`'s own readings
of the same clip.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import end_checks, holds, lessons, needs, transcript
from print_shop.rounds.box import Box
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool
from tests.test_cae import cae  # noqa: F401 - the session's planted parts, made once

#: `cae` alone: CI's bench shards have only the first image, and its `cae` job has both.
pytestmark = pytest.mark.cae

#: The place the lid's band passes the clip's hook, as `fit-on` reads it on the planted box.
HOOK = [[-4.0, -15.6, 24.0], [4.0, -15.0, 25.6]]
LID = holds.Held("lid", "", holds.PRINTED, "box")


def arm(work: Path, file: str, place: list[list[float]], across: str) -> dict[str, Any]:
    found: dict[str, Any] = end_checks._find_arm(Box(work), file, place, across, work)
    return found


# The arm, from the place and the part's shape


def test_the_clip_s_arm_is_found_from_the_place_its_hook_is_passed_at(cae: Any) -> None:  # noqa: F811
    """The arm stands from its foot's top at z 10 to z 26, 8 mm wide, its face at y -15.8 and
    its hook out to -15, with 1 mm clear behind it: the box `tests/test_cae.py` gives `bend` by
    hand, 0.1 mm clear of it all round but at its root."""
    found = arm(cae.work, "snap/box.step", HOOK, "y")
    assert found["found"] is True
    assert found["arm"] == [[-4.1, -17.1, 10.0], [4.1, -14.9, 26.1]]
    assert found["root"] == ["z", 10.0]
    assert found["toward"] == "-y", "away from the lid's band, which comes at it from +y"
    assert found["length_mm"] == pytest.approx(16.0)
    short = arm(cae.work, "snap/box-short.step", HOOK, "y")
    assert (short["root"], short["length_mm"]) == (["z", 18.0], pytest.approx(8.0))
    tight = arm(cae.work, "snap/box-tight.step", HOOK, "y")
    assert tight["found"] is True, "0.45 mm behind it is still clear"


def test_no_arm_is_found_where_what_s_there_is_held_all_round_or_isn_t_at_a_face(
    cae: Any,  # noqa: F811
) -> None:
    # The lid's band under its window, where it meets the hook: joined to the skirt at both
    # ends along x, and to the plate above.
    band = [[-4.0, -15.6, 22.0], [4.0, -15.0, 23.6]]
    found = arm(cae.work, "snap/lid.step", band, "y")
    assert found["found"] is False
    assert "nothing there stands free from a root to a tip" in found["why"]
    # A place in the middle of the box's floor is at no face of what it's in.
    inside = [[-1.0, -1.0, 0.5], [1.0, 1.0, 1.5]]
    found = arm(cae.work, "snap/box.step", inside, "z")
    assert found["found"] is False and "at no one face of it" in found["why"]
    nowhere = [[100.0, 100.0, 100.0], [101.0, 101.0, 101.0]]
    assert arm(cae.work, "snap/box.step", nowhere, "y")["why"] == "none of the file is at the place"


# `fit-on --from clear`


def test_from_clear_starts_where_the_boxes_are_apart_and_a_step_more(cae: Any) -> None:  # noqa: F811
    """The lid's lowest point is its skirt's foot at z 22, and the box's rim is at z 30: 8 mm
    apart along +z, and a step more is 8.25."""
    lid_on = ("fit-on", "snap/lid.step", "--onto", "snap/box.step", "--along", "+z")
    answer = cae(*lid_on, "--from", "clear")
    assert answer.verdict == "BLOCKED"
    assert answer.numbers["from_mm"] == pytest.approx(8.25)
    assert answer.numbers["from_clear"] is True
    assert answer.numbers["steps"][0]["pieces"] == 0, "nothing is in its way where it starts"
    assert "--from clear: 8.25 mm out" in answer.text
    given = cae(*lid_on, "--from", "6")
    assert given.numbers["from_clear"] is False
    assert given.numbers["depth_mm"] == pytest.approx(answer.numbers["depth_mm"], abs=1e-6)


# `bend`'s "what would pass"


def test_what_would_pass_is_the_strip_formula_s_and_the_push_it_gives_reads_the_limit(
    cae: Any,  # noqa: F811
) -> None:
    """The short clip, pushed 0.6 mm, is over PETG's 1%. By ε = 1.5·t·y/L² each of the three
    scales the reading's own peak to the limit; and the solve is linear, so the push it gives,
    run again, reads the limit itself."""
    clip = ("snap/box-short.step", "--arm", "-4.1,-17.1,18,4.1,-14.9,26.1", "--root", "z=18")
    over = cae("bend", *clip, "--push", "0.6", "--toward", "-y")
    assert over.verdict == "FAIL"
    numbers = over.numbers
    peak, limit = numbers["across_layers_pct"], numbers["limit_pct"]
    thick, length = numbers["thickness_mm"], numbers["pushes"][numbers["worst"]]["length_mm"]
    passing = numbers["would_pass"]

    def strip(t: float, y: float, long: float) -> float:
        return 1.5 * t * y / long**2

    scale = peak / strip(thick, 0.6, length)
    assert scale * strip(thick, passing["push_mm"], length) == pytest.approx(limit)
    assert scale * strip(passing["thickness_mm"], 0.6, length) == pytest.approx(limit)
    assert scale * strip(thick, 0.6, passing["length_mm"]) == pytest.approx(limit)
    assert passing["push_mm"] == pytest.approx(0.6 * limit / peak)
    assert f"a push of at most {passing['push_mm']:.3f} mm (it's 0.6)" in over.text
    again = cae("bend", *clip, "--push", f"{passing['push_mm']:.6f}", "--toward", "-y")
    assert again.numbers["across_layers_pct"] == pytest.approx(limit, rel=1e-3)
    assert again.verdict == "OK"


# The end checks, read for real


def ways(work: Path, slide: str, held: holds.Held) -> list[end_checks.Way]:
    need = Need("hold", "it's held", "chosen", "bench", command=slide)
    here = [p.relative_to(work).as_posix() for p in work.rglob("*.step")]
    found, unread = end_checks.ways([held], [need], [], here, [])
    assert unread == []
    return found


def laid(cae: Any, root: Path, box: str) -> Path:  # noqa: F811
    """A folder with one of the planted boxes as `box.step`, and the lid."""
    root.mkdir(parents=True)
    shutil.copy(cae.work / "snap" / box, root / "box.step")
    shutil.copy(cae.work / "snap" / "lid.step", root / "lid.step")
    return root


def test_a_clip_that_bends_too_far_is_over_and_the_same_longer_takes_it(
    cae: Any,  # noqa: F811
    tmp_path: Path,
) -> None:
    slide = "clearance box.step lid.step --slide +z"
    work = laid(cae, tmp_path / "short", "box-short.step")
    read = end_checks.read(Box(work), ways(work, slide, LID), {}, work, tmp_path / "kept-short")
    place = read[0].places[0]
    assert (read[0].verdict, place.status) == ("BLOCKED", end_checks.OVER)
    assert place.depth_mm == pytest.approx(0.6, abs=0.005) and place.across == "y"
    assert place.command == (
        "bend box.step --arm -4.1,-17.1,18,4.1,-14.9,26.1 --root z=18 --push 0.6 "
        "--toward -y --layers z"
    )
    assert place.verdict == "FAIL" and place.numbers["across_layers_pct"] > 1.0
    assert read[0].failing() == [place]
    said = "\n".join(end_checks.said(read))
    assert "against the 1% limit" in said and "What would pass: at this push" in said
    assert "with 1.000 mm of room behind it" in said
    work = laid(cae, tmp_path / "long", "box.step")
    read = end_checks.read(Box(work), ways(work, slide, LID), {}, work, tmp_path / "kept-long")
    place = read[0].places[0]
    assert place.status == end_checks.TAKES and place.verdict == "OK"
    assert place.numbers["across_layers_pct"] == pytest.approx(0.59, abs=0.02)
    assert read[0].failing() == []
    work = laid(cae, tmp_path / "back", "box-back.step")
    read = end_checks.read(Box(work), ways(work, slide, LID), {}, work, tmp_path / "kept-back")
    assert (read[0].verdict, read[0].places, read[0].unsure) == ("CLEAR", [], False)


def test_a_given_strip_pushed_past_a_printed_catch_is_for_a_person(
    cae: Any,  # noqa: F811
    tmp_path: Path,
) -> None:
    """The strip is 0.2 mm clear of each wall and the catch stands 0.5 mm in: 0.3 mm deep. The
    wall under the catch is 3 mm tall and 2 mm thick, no beam, so `bend` has no one answer for
    it. The strip is given, so nothing is failed as PETG: it has to give way by 0.3 mm."""
    work = tmp_path / "work"
    (work / "references").mkdir(parents=True)
    shutil.copy(cae.work / "snap" / "channel.step", work / "channel.step")
    shutil.copy(cae.work / "snap" / "strip.step", work / "references" / "strip.step")
    strip = holds.Held("references/strip.step", "+z", "fact", "the channel's catch")
    found = ways(work, "clearance channel.step references/strip.step --slide +z", strip)
    read = end_checks.read(Box(work), found, {}, work, tmp_path / "kept")
    place = read[0].places[0]
    assert read[0].verdict == "BLOCKED" and place.depth_mm == pytest.approx(0.3, abs=0.005)
    assert (place.status, place.given) == (end_checks.PERSON, "references/strip.step")
    assert place.arm_file == "channel.step" and place.verdict == "DISAGREE"
    assert read[0].failing() == []
    said = "\n".join(end_checks.said(read))
    assert "has to give way by 0.300 mm to pass, and whether it can is a person's to judge" in said


# A run, from `done` to the send-back or the stop

REQUEST = """\
A box and a lid, both printed.

## Requirements
- The lid snaps onto the box and stays on until pulled off.

## Stops
- no plan
"""
SLIDE = "clearance box.step lid.step --slide +z"
STAYS = Need(
    "stays",
    "the lid stays on the box until pulled off",
    "request",
    "bench",
    quote="The lid snaps onto the box and stays on until pulled off.",
    command=SLIDE,
    verdicts=["HELD"],
    measures="fit",
)
#: The model's script, standing in for one that builds: it copies the two planted parts it was
#: given under other names, so a clean run leaves a box and a lid of the model's own.
MAKE = (
    "import shutil\n"
    "shutil.copy('references/kit-a.step', 'box.step')\n"
    "shutil.copy('references/kit-b.step', 'lid.step')\n"
)


def driven(root: Path, cae: Any, box: str) -> tuple[Run, Scripted]:  # noqa: F811
    kit = root / "kit"
    kit.mkdir(parents=True)
    shutil.copy(cae.work / "snap" / box, kit / "kit-a.step")
    shutil.copy(cae.work / "snap" / "lid.step", kit / "kit-b.step")
    model = Scripted(
        [tool("write", path="make.py", content=MAKE)],
        [tool("run", path="make.py")],
        [
            tool(
                "done",
                script="make.py",
                part=["box.step", "lid.step"],
                fix_shape="first",
                closing="A box with a snap clip, and its lid.",
            )
        ],
        [tool("blocked", reason="Out of turns.")],
    )
    given = [kit / "kit-a.step", kit / "kit-b.step"]
    run = Run.start(root / "runs", "r", REQUEST, given, model, sleep=lambda _: None)
    (run.folder / "breakdowns").mkdir(exist_ok=True)
    broken = {
        "parts": [{"name": "box", "what": "an open box"}, {"name": "lid", "what": "its lid"}],
        "joins": [{"parts": ["box", "lid"], "how": "the lid's band snaps over a clip on the box"}],
    }
    (run.folder / "breakdowns" / "1.json").write_text(json.dumps(broken))
    run.state.breakdowns = 1
    run.state.needs = needs.dump([STAYS])
    run.state.needs_open = False
    run.state.needs_version = 1
    (run.folder / "needs").mkdir(exist_ok=True)
    (run.folder / "needs" / "1.json").write_text(json.dumps([STAYS.plain()]))
    run.state.stage = "rounds"
    run._start_round()
    run.save()
    return run, model


def test_a_run_is_sent_back_for_the_clip_that_bends_too_far_and_stops_for_the_one_that_takes_it(
    cae: Any,  # noqa: F811
    tmp_path: Path,
) -> None:
    run, model = driven(tmp_path / "short", cae, "box-short.step")
    assert run.drive().name == "blocked"
    done = [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"][2]
    assert done["name"] == "done" and done["detail"]["end_checks"]["sent_back"] is True
    assert "- stays, met: HELD" in done["reply"], "every need passed"
    assert not (run.folder / "approvals").exists()
    note = str(run.state.end_sent["note"])  # type: ignore[index]
    assert "`fit-on lid.step --onto box.step --along +z --from clear` reads BLOCKED" in note
    assert "`bend box.step --arm -4.1,-17.1,18,4.1,-14.9,26.1 --root z=18 --push 0.6" in note
    assert "reads FAIL" in note and "against the 1% limit" in note and "What would pass" in note
    after = model.bodies[-1]["messages"]
    assert [m["role"] for m in after] == ["system", "user", "user"] and after[2]["content"] == note
    written = [dict(r) for r in run.ledger.rows("SELECT written_by, kind, status FROM lessons")]
    assert written == [
        {"written_by": lessons.HARNESS, "kind": "verify", "status": lessons.CANDIDATE}
    ]
    assert run.state.restart_kinds == [{"why": "end_checks", "before_call": 4}]

    run, _ = driven(tmp_path / "long", cae, "box.step")
    assert run.drive().name == "met"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f"## {end_checks.HEADING}" in stop and ": passes. The arm is `box.step`'s" in stop
    assert "reads OK: 0.59% across its layers against the 1% limit" in stop
    assert not list(run.ledger.rows("SELECT 1 FROM lessons"))
    kept = run.folder / "rounds" / "001" / "end"
    assert sorted(p.name for p in kept.glob("*.json")) == ["bend-0-0.json", "way-on-0.json"]
