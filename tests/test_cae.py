"""The bench's second image on planted parts: `fit-on` and `bend`, with FreeCAD, sealed
(mqucifer/print-shop#142). Needs `scripts/bench.py build cae`, and the first image for the slide.

A design run approved a snap-on lid that could never be fitted: nothing read its way on, or what
that did to its clips. Everything here is made from scratch by `cae_cases/make_fixtures.py`, a
box with one snap clip and a lid whose skirt passes over its hook, and right answers come from
arithmetic.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import host
from print_shop.rounds import end_checks, needs, reports
from print_shop.rounds.box import Box
from print_shop.rounds.needs import Need

pytestmark = pytest.mark.cae
CASES = Path(__file__).parent / "cae_cases"

#: The clip, as `make_fixtures.py` draws it: 1.2 mm thick, free for 16 mm from its root at z 10
#: to its top, its hook 2 mm tall and 0.8 mm out, the skirt 0.2 mm clear of its face.
THICK, ROOT, TOP, HOOK_FOOT = 1.2, 10.0, 26.0, 24.0
DEPTH = 0.8 - 0.2
#: A box round the arm, hook and all: its low side on the root, and clear of the wall at y -18.
ARM = "-4.1,-17.1,10,4.1,-14.9,26.1"
CLIP = ("--arm", ARM, "--root", "z=10", "--toward", "-y")


def strip(push: float, length: float) -> float:
    """A plain strip's strain at its root, in percent: ε = 1.5·t·y/L²."""
    return 100 * 1.5 * THICK * push / length**2


@dataclass
class Answer:
    code: int
    text: str
    record: dict[str, Any]

    @property
    def verdict(self) -> str:
        return str(self.record["verdict"])

    @property
    def numbers(self) -> dict[str, Any]:
        return dict(self.record["numbers"])


@pytest.fixture(scope="session")
def cae(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id(host.CAE_IMAGE) is None:
        pytest.fail(f"{host.missing(host.CAE_IMAGE)} first")
    work = tmp_path_factory.mktemp("cae")
    shutil.copy(CASES / "make_fixtures.py", work / "make_fixtures.py")
    made = host.run(["make_fixtures.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr

    def ask(*args: str) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    return ask


LID_ON = ("--along", "+z", "--from", "6")


# How they run


def test_both_run_with_no_network_inside_the_bench_s_limits_in_the_second_image(cae: Any) -> None:
    """The container's own reading of its routes, its CPUs and its memory, as the first image's
    commands give it, and the image is the second."""
    for answer in (
        cae("fit-on", "snap/lid.step", "--onto", "snap/box-back.step", *LID_ON),
        cae("bend", "snap/clip.step", *CLIP, "--push", "0.6", "--at", "far"),
    ):
        ran = answer.record["ran"]
        assert ran["network_up"] == ["lo"]
        assert ran["network_routes"] == 0
        assert ran["cpu_max"].split()[0] == "200000", "2 CPUs"
        assert int(ran["memory_max"]) == 3 * 1024**3
        assert ran["user"] != 0
        assert ran["image"] == host.CAE_IMAGE and ran["image_id"].startswith("sha256:")
        assert ran["image_id"] == host.image_id(host.CAE_IMAGE)


def test_a_script_in_the_second_image_reaches_nothing_and_cant_write_the_bench(cae: Any) -> None:
    script = cae.work / "reach.py"
    script.write_text(
        "import socket\n"
        "import FreeCAD, Part\n"
        "s = socket.socket()\ns.settimeout(3)\n"
        "try:\n    s.connect(('example.com', 80))\n    print('REACHED')\n"
        "except OSError as e:\n    print('refused', e)\n"
        "try:\n    open('/opt/bench/bench/cli.py', 'a')\n    print('WROTE')\n"
        "except OSError as e:\n    print('read-only', e)\n"
        "try:\n    open('/opt/conda/x', 'w')\n    print('WROTE')\n"
        "except OSError as e:\n    print('read-only', e)\n"
        "print('volume', Part.makeBox(1, 2, 3).Volume)\n"
    )
    done = host.run(["reach.py"], cae.work, python=True, image=host.CAE_IMAGE)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "REACHED" not in done.stdout and "WROTE" not in done.stdout
    assert done.stdout.count("read-only") == 2 and "volume 6.0" in done.stdout


def test_the_first_image_says_these_two_arent_its_own(cae: Any) -> None:
    """A script in the first image that calls `fit-on` is told where it runs, not left with an
    import's traceback."""
    script = cae.work / "call.py"
    script.write_text(
        "import sys\nsys.path.insert(0, '/opt/bench')\nfrom bench.cli import main\n"
        "sys.exit(main(['fit-on', 'snap/lid.step', '--onto', 'snap/box.step', '--along', '+z', "
        "'--from', '6']))\n"
    )
    done = host.run(["call.py"], cae.work, python=True)
    assert done.returncode == 2
    assert "verdict: NO ANSWER: `fit-on` runs in the bench's second image" in done.stdout


# The way on


def test_a_lid_s_band_passing_over_a_hook_is_blocked_by_the_hook_s_depth(cae: Any) -> None:
    """The skirt's band under its window is 0.2 mm clear of the arm's face, and the hook stands
    0.8 mm out: 0.6 mm deep, 8 mm along the wall, while the band (z 22 to 23.6 seated) is level
    with the hook (z 24 to 26), which is from 4 mm out down to 0.4, both ends bare. Read every
    0.25 mm, that's 3.75 down to 0.5."""
    answer = cae("fit-on", "snap/lid.step", "--onto", "snap/box.step", *LID_ON)
    assert answer.verdict == "BLOCKED" and answer.code == 1, answer.text
    numbers = answer.numbers
    (place,) = numbers["places"]
    assert numbers["depth_mm"] == place["depth_mm"] == pytest.approx(DEPTH, abs=1e-6)
    assert place["depth_rays_mm"] == pytest.approx(DEPTH, abs=2 * place["rays_mm"] + 0.01)
    assert place["along_mm"] == pytest.approx(8.0, abs=1e-6)
    assert [abs(c) for c in place["across"]] == [0, 1, 0], "across the front wall"
    assert place["file"] == "snap/box.step" and not place["seated"]
    assert (place["first_mm"], place["last_mm"], place["readings"]) == (3.75, 0.5, 14)
    low, high = place["box"]
    assert low[:2] == pytest.approx([-4, -15.6]) and high[:2] == pytest.approx([4, -15.0])
    assert low[2] >= HOOK_FOOT - 1e-6 and high[2] <= TOP + 1e-6, "on the hook"
    assert numbers["readings"] == 25 and len(numbers["steps"]) == 25
    blocked = [s["out_mm"] for s in numbers["steps"] if s["pieces"]]
    assert blocked == [3.75 - 0.25 * k for k in range(14)]
    assert answer.text.startswith(
        "verdict: BLOCKED: snap/lid.step passes through snap/box.step on its way on, from 6 mm "
        "out along +z to where it sits, read every 0.25 mm, at 1 place(s): the deepest is 0.600 "
        "mm deep across y, as places.0.depth_mm, and 8.000 mm along, at (-4, -15.6, 24) to "
    ), answer.text
    assert "--push 0.6 --toward DIR" in answer.text, "it says what `bend` takes"
    assert len(answer.record["second_check"]) == 2
    assert "0.600 mm by OCCT's common and 0.600 by rays" in answer.record["second_check"][0]
    assert numbers["rays"]["strays"] == [] and numbers["rays"]["count"] > 100_000
    assert not reports.unlike("fit-on", numbers), reports.unlike("fit-on", numbers)


def test_with_the_clip_moved_back_the_way_on_is_clear(cae: Any) -> None:
    """The same clip 0.8 mm further back: its hook stops 0.2 mm short of the skirt."""
    answer = cae("fit-on", "snap/lid.step", "--onto", "snap/box-back.step", *LID_ON)
    assert answer.verdict == "CLEAR" and answer.code == 0, answer.text
    assert answer.numbers["places"] == [] and answer.numbers["depth_mm"] == 0
    assert all(s["pieces"] == 0 for s in answer.numbers["steps"])
    assert "rays 0.1 mm apart over the whole outline, at each of the 25 readings" in answer.text
    assert answer.record["second_check"][-1].endswith("didn't find: none")
    assert not reports.unlike("fit-on", answer.numbers)


def test_the_way_is_given_as_the_way_out_and_a_bare_axis_is_its_plus(cae: Any) -> None:
    """`--along z` is `+z`. Along `-z` the lid would come up through the box's floor: that's
    another way, and it reads what's in it."""
    bare = cae("fit-on", "snap/lid.step", "--onto", "snap/box.step", "--along", "z", "--from", "6")
    assert bare.numbers["along"] == "+z" and bare.numbers["depth_mm"] == pytest.approx(DEPTH)
    under = cae(
        "fit-on", "snap/lid.step", "--onto", "snap/box.step", "--along", "-z", "--from", "6"
    )
    assert under.verdict == "BLOCKED" and under.numbers["along"] == "-z"
    assert under.numbers["depth_mm"] > 30, "the lid's plate through the box's walls"


def test_a_place_s_deepest_step_is_found_between_the_steps(cae: Any) -> None:
    """The hook as a wedge is 0.6 mm deep only while the band is level with its foot, from 2 mm
    out down to 0.4. Read at 5, 2.5 and 0 mm out, only 2.5 meets it, 0.4 mm deep on the slope:
    the deepest is found between the steps."""
    answer = cae(
        "fit-on", "snap/lid.step", "--onto", "snap/box-ramp.step", "--along", "+z",
        "--from", "5", "--step", "2.5",
    )  # fmt: skip
    assert answer.verdict == "BLOCKED", answer.text
    (place,) = answer.numbers["places"]
    (step,) = [s for s in answer.numbers["steps"] if s["pieces"]]
    assert step["out_mm"] == 2.5 and step["depth_mm"] == pytest.approx(0.4, abs=1e-6)
    assert place["depth_mm"] == pytest.approx(DEPTH, abs=1e-6)
    assert 0.4 <= place["out_mm"] <= 2.0 and place["out_mm"] not in (5, 2.5, 0)
    assert place["depth_rays_mm"] == pytest.approx(DEPTH, abs=2 * place["rays_mm"] + 0.01)


def test_an_overlap_where_the_part_sits_is_said_as_one_no_bend_clears(cae: Any) -> None:
    """The box's walls 0.5 mm taller than the lid's seat: the lid's plate is in the rim where it
    sits, and from 0.25 mm out. Nothing else is in its way, and the reply names no push."""
    answer = cae(
        "fit-on", "snap/lid.step", "--onto", "snap/box-tall.step", "--along", "+z", "--from", "3"
    )
    assert answer.verdict == "BLOCKED", answer.text
    (place,) = answer.numbers["places"]
    assert place["seated"] and (place["first_mm"], place["last_mm"]) == (0.25, 0)
    # The rim, 60 x 40 outside and 56 x 36 inside, 0.5 mm of it.
    assert place["volume_mm3"] == pytest.approx((60 * 40 - 56 * 36) * 0.5, abs=1e-3)
    assert "overlaps snap/box-tall.step where it sits" in answer.record["why"]
    assert "no bend clears an overlap where a part sits" in answer.record["why"]
    assert "--push" not in answer.text


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (
            ("snap/lid.step", "--onto", "snap/box.step", "--along", "+z", "--from", "0"),
            "more than 0",
        ),
        (("snap/lid.step", "--onto", "nope.stl", "--along", "+z", "--from", "6"), "STEP files"),
        (
            ("snap/lid.step", "--onto", "snap/box.step", "--along", "+z", "--from", "600",
             "--step", "0.01"),
            "over 2000 readings",
        ),
    ],
)  # fmt: skip
def test_a_way_on_that_cant_be_read_says_why(cae: Any, args: tuple[str, ...], says: str) -> None:
    answer = cae("fit-on", *args)
    assert answer.verdict == "NO ANSWER" and says in answer.record["why"], answer.text


def test_a_way_thats_no_axis_is_a_usage_error(cae: Any) -> None:
    done = host.run(
        ["fit-on", "snap/lid.step", "--onto", "snap/box.step", "--along", "up", "--from", "6"],
        cae.work,
    )
    assert done.returncode == 3 and "one of +x -x +y -y +z -z" in done.stderr


# The bend


def test_a_clip_pushed_at_its_hook_s_top_edge_reads_the_strip_formula_within_15_percent(
    cae: Any,
) -> None:
    """The arm is 1.2 mm thick and 16 mm from its root to the hook's top edge, pushed 0.6 mm:
    ε = 1.5·t·y/L² = 0.42%. The solve reads it within 15%, at the default mesh, half the arm's
    thickness, in under 30 s, and it's within PETG's 1% across its layers."""
    answer = cae("bend", "snap/clip.step", *CLIP, "--push", "0.6", "--at", "far")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    far = numbers["pushes"]["far"]
    formula = strip(0.6, TOP - ROOT)
    assert formula == pytest.approx(0.4219, abs=1e-4)
    assert far["peak_pct"] == pytest.approx(formula, rel=0.15), answer.text
    assert far["strip_pct"] == pytest.approx(formula, rel=1e-6), "t and L read off the arm"
    assert far["length_mm"] == pytest.approx(16.0) and numbers["thickness_mm"] == pytest.approx(1.2)
    assert numbers["mesh_mm"] == pytest.approx(0.6), "coarse: half the arm's thickness"
    assert answer.record["seconds"] < 30
    assert ROOT <= far["peak_at"][2] <= ROOT + THICK, "the peak is at the root"
    assert numbers["across_layers_pct"] == far["peak_pct"] and numbers["worst"] == "far"
    assert (numbers["limit_pct"], numbers["limit_from"], numbers["layers"]) == (1.0, "petg", "z")
    assert far["back_moves_mm"] == pytest.approx(0.6, abs=0.01), "the tip goes with the push"
    assert numbers["room_behind_mm"] == pytest.approx(1.0, abs=1e-6)
    assert "within the 1% limit (PETG across its layers)" in answer.record["why"]
    # The second method: the arm as a beam, its own sections, half way along where neither has
    # the root's corner in it.
    assert far["beam_root_pct"] == pytest.approx(formula, rel=0.01)
    assert far["differ_share"] < 0.2
    assert "the arm as a beam, 40 slabs from the root" in answer.record["second_check"][0]
    assert not reports.unlike("bend", numbers), reports.unlike("bend", numbers)


def test_both_edges_are_pushed_and_the_nearer_the_shorter_arm_is_judged(cae: Any) -> None:
    """With no `--at`, the hook's top edge and its foot are each pushed. The foot is 14 mm from
    the root, so the same push strains the root (16/14)² as much, and that's the one judged."""
    answer = cae("bend", "snap/clip.step", *CLIP, "--push", "0.6")
    assert answer.verdict == "OK", answer.text
    far, near = answer.numbers["pushes"]["far"], answer.numbers["pushes"]["near"]
    assert near["length_mm"] == pytest.approx(HOOK_FOOT - ROOT)
    assert near["peak_pct"] == pytest.approx(strip(0.6, 14.0), rel=0.15)
    assert near["peak_pct"] / far["peak_pct"] == pytest.approx((16 / 14) ** 2, rel=0.05)
    assert answer.numbers["worst"] == "near"
    assert answer.numbers["across_layers_pct"] == near["peak_pct"]
    assert "at its near edge" in answer.record["why"]
    assert len(answer.record["second_check"]) == 2


def test_a_push_past_what_the_layers_take_is_over_the_limit(cae: Any) -> None:
    """The strain is in proportion to the push: at 1.6 mm the strip formula gives 1.125% at the
    top edge, past PETG's 1%, and the solve reads 8/3 of what it read at 0.6."""
    at_06 = cae("bend", "snap/clip.step", *CLIP, "--push", "0.6", "--at", "far")
    answer = cae("bend", "snap/clip.step", *CLIP, "--push", "1.6", "--at", "far")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    far = answer.numbers["pushes"]["far"]
    assert far["peak_pct"] == pytest.approx(strip(1.6, 16.0), rel=0.15)
    assert far["peak_pct"] == pytest.approx(
        at_06.numbers["pushes"]["far"]["peak_pct"] * 1.6 / 0.6, rel=1e-4
    ), "the same mesh each time, and a linear solve"
    assert far["peak_pct"] > 1.0
    assert "over the 1% limit (PETG across its layers)" in answer.record["why"]


def test_a_shorter_arm_takes_more_and_the_limit_can_be_the_request_s(cae: Any) -> None:
    """Half the free length is four times the strain: 1.69% by the formula at 0.6 mm, `FAIL`.
    The long clip is `FAIL` too against a limit of 0.3%, and says whose limit it is."""
    short = cae(
        "bend", "snap/clip-short.step", "--arm", "-4.1,-17.1,18,4.1,-14.9,26.1", "--root",
        "z=18", "--toward", "-y", "--push", "0.6", "--at", "far",
    )  # fmt: skip
    assert short.verdict == "FAIL", short.text
    assert short.numbers["pushes"]["far"]["peak_pct"] == pytest.approx(strip(0.6, 8.0), rel=0.15)
    asked = cae("bend", "snap/clip.step", *CLIP, "--push", "0.6", "--at", "far", "--limit", "0.3")
    assert asked.verdict == "FAIL" and "over the 0.3% limit (--limit)" in asked.record["why"]
    assert asked.numbers["limit_from"] == "--limit"


def test_a_hook_with_one_crest_is_pushed_there(cae: Any) -> None:
    """A wedge has no flat face: its crest, at the hook's foot, is the one edge a push meets."""
    answer = cae("bend", "snap/clip-ramp.step", *CLIP, "--push", "0.6")
    assert answer.verdict == "OK", answer.text
    assert list(answer.numbers["pushes"]) == ["far"]
    assert answer.numbers["pushes"]["far"]["length_mm"] == pytest.approx(HOOK_FOOT - ROOT)
    assert answer.numbers["pushes"]["far"]["peak_pct"] == pytest.approx(strip(0.6, 14.0), rel=0.15)


def test_an_arm_with_less_room_behind_it_than_it_moves_fails_on_the_room(cae: Any) -> None:
    """In the box the clip has 1 mm behind it, and pushed 0.6 mm its back moves 0.6: `OK`. With
    the wall 0.55 mm thicker there's 0.45 mm, and it meets the wall first: `FAIL`, though the
    strain is the same and within the limit."""
    roomy = cae("bend", "snap/box.step", *CLIP, "--push", "0.6", "--at", "far")
    assert roomy.verdict == "OK", roomy.text
    assert roomy.numbers["room_behind_mm"] == pytest.approx(1.0, abs=1e-6)
    tight = cae("bend", "snap/box-tight.step", *CLIP, "--push", "0.6", "--at", "far")
    assert tight.verdict == "FAIL" and tight.code == 1, tight.text
    assert tight.numbers["room_behind_mm"] == pytest.approx(0.45, abs=1e-6)
    assert tight.numbers["back_moves_mm"] == pytest.approx(0.6, abs=0.01)
    assert tight.numbers["across_layers_pct"] == pytest.approx(
        roomy.numbers["across_layers_pct"], rel=0.02
    )
    assert tight.numbers["across_layers_pct"] < 1.0
    assert "there's 0.450 mm behind it, so it meets what's behind it" in tight.record["why"]
    assert "over the" not in tight.record["why"]


def test_the_strain_judged_is_the_one_across_the_layers(cae: Any) -> None:
    """The arm stands along z. Printed on its side, its layers along x, a bend about x stretches
    nothing across them to speak of."""
    answer = cae("bend", "snap/clip.step", *CLIP, "--push", "0.6", "--at", "far", "--layers", "x")
    assert answer.verdict == "OK"
    assert answer.numbers["across_layers_pct"] < 0.5 * strip(0.6, 16.0)
    assert answer.numbers["layers"] == "x"


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (("--arm", ARM, "--root", "z=12", "--toward", "-y", "--push", "0.6"), "no end on --root"),
        (("--arm", ARM, "--root", "z=10", "--toward", "+z", "--push", "0.6"), "along the arm"),
        (("--arm", ARM, "--root", "z=10", "--toward", "-y", "--push", "0"), "more than 0"),
        (("--arm", "40,40,40,50,50,50", "--root", "z=40", "--toward", "-y", "--push", "1"),
         "nothing of snap/clip.step is inside --arm"),
        (("--arm", "-4.1,-16.9,10,4.1,-14.9,26.1", "--root", "z=10", "--toward", "-y", "--push",
          "0.6"), "--arm cuts through snap/clip.step along the arm's back, at y=-16.9"),
    ],
)  # fmt: skip
def test_an_arm_that_cant_be_read_says_what_to_give(
    cae: Any, args: tuple[str, ...], says: str
) -> None:
    answer = cae("bend", "snap/clip.step", *args)
    assert answer.verdict == "NO ANSWER" and says in answer.record["why"], answer.text


def test_an_arm_too_stubby_to_bend_as_a_beam_is_a_disagreement(cae: Any) -> None:
    """Pushed sideways the clip is 8 mm thick and 16 long. The solve and the beam are a quarter
    and more apart half way along it, so there's no answer to rely on, and the reply has both."""
    answer = cae("bend", "snap/clip.step", "--arm", ARM, "--root", "z=10", "--toward", "+x",
                 "--push", "0.6")  # fmt: skip
    assert answer.verdict == "DISAGREE" and answer.code == 2, answer.text
    assert answer.numbers["thickness_mm"] == pytest.approx(8.0)
    assert all(p["differ_share"] > 0.2 for p in answer.numbers["pushes"].values())
    assert "too short for its thickness to bend as a beam" in answer.record["why"]


# From the hold to the way on to the bend


def test_the_slide_names_fit_on_and_its_depth_is_the_push_bend_takes(cae: Any) -> None:
    """`clearance --slide` reads the lid held by the hook, and its path is what the lid passes on
    its way in. Its reply gives the `fit-on` command for that way, with the files as given; run
    as given, it reads the hook 0.6 mm deep, and `bend` takes that as its push."""
    if host.image_id() is None:
        pytest.fail(f"{host.missing(host.IMAGE)} first")
    slide = cae("clearance", "snap/box.step", "snap/lid.step", "--slide", "+z")
    assert slide.verdict == "HELD", slide.text
    found = re.search(r"`(fit-on [^`]+)`", slide.text)
    assert found, slide.text
    command = found.group(1).split()
    assert command[:6] == ["fit-on", "snap/lid.step", "--onto", "snap/box.step", "--along", "+z"]
    on = cae(*command)
    assert on.verdict == "BLOCKED" and on.numbers["depth_mm"] == pytest.approx(DEPTH, abs=1e-6)
    push = re.search(r"--push (\S+) --toward DIR", on.text)
    assert push and float(push.group(1)) == pytest.approx(DEPTH)
    bent = cae("bend", "snap/box.step", *CLIP, "--push", push.group(1))
    assert bent.verdict == "OK", bent.text
    assert bent.numbers["across_layers_pct"] == pytest.approx(strip(DEPTH, 14.0), rel=0.15)


def test_a_slide_that_meets_nothing_names_no_fit_on(cae: Any) -> None:
    if host.image_id() is None:
        pytest.fail(f"{host.missing(host.IMAGE)} first")
    slide = cae("clearance", "snap/box-back.step", "snap/lid.step", "--slide", "+z")
    assert "fit-on" not in slide.text, slide.text


# A need on them


def test_a_need_on_a_snap_s_depth_and_on_its_bend_is_judged_by_the_answers(cae: Any) -> None:
    """A snap fit is meant to be `BLOCKED`: its need names the verdict and holds the depth. One
    that asks for more bend than the request allows fails on the number, and a need on `bend`
    passes on `OK`."""
    on = cae("fit-on", "snap/lid.step", "--onto", "snap/box.step", *LID_ON)

    def need(command: str, **more: Any) -> Need:
        return Need("snap", "the lid snaps on", "request", "it snaps on", "bench", command, **more)

    way = "fit-on snap/lid.step --onto snap/box.step --along +z --from 6"
    held = need(way, verdicts=["BLOCKED"], number="depth_mm", at_most=0.6)
    assert needs.judge(held, on.code, on.text, on.record).passed
    tighter = need(way, verdicts=["BLOCKED"], number="depth_mm", at_most=0.5)
    read = needs.judge(tighter, on.code, on.text, on.record)
    assert not read.passed and read.value == pytest.approx(DEPTH)
    assert not needs.judge(need(way), on.code, on.text, on.record).passed, "BLOCKED isn't exit 0"
    bent = cae("bend", "snap/clip.step", *CLIP, "--push", "0.6")
    arm = need(f"bend snap/clip.step --arm {ARM} --root z=10 --toward -y --push 0.6")
    assert needs.judge(arm, bent.code, bent.text, bent.record).passed


# Joined parts under a weight (mqucifer/print-shop#156)

#: The beam, as `make_fixtures.py` draws it: 120 x 20 x 10 along x, held at x 0, of PETG at
#: 1500 MPa, loaded 115 mm out, on its top.
BEAM_L, BEAM_W, BEAM_T, MODULUS, G = 115.0, 20.0, 10.0, 1500.0, 9.80665
TIP = ("--at", "115,10,10")
#: How far a two-part beam's figures may be from the one-piece beam's: the same shape and the
#: same joined mesh but for the triangles on the joint's face.
SAME = 0.02


def beam_strain(kg: float, at: float = 0.0) -> float:
    """A cantilever's strain on its top and bottom faces, `at` mm from its root, in percent:
    ε = 6·F·(L - at)/(E·b·h²)."""
    return 100 * 6 * kg * G * (BEAM_L - at) / (MODULUS * BEAM_W * BEAM_T**2)


def test_load_runs_sealed_in_the_second_image(cae: Any) -> None:
    ran = cae("load", "load/one.json", "--weight", "1", *TIP).record["ran"]
    assert ran["network_up"] == ["lo"] and ran["network_routes"] == 0
    assert ran["image"] == host.CAE_IMAGE and ran["user"] != 0


def test_a_one_piece_beam_reads_as_the_beam_formula_says(cae: Any) -> None:
    """Held at one end with 1 kg 115 mm out: 0.226% at the root by ε = 6FL/(Ebh²), and the
    loaded place moves FL³/(3EI) = 1.99 mm, less a little as the weight is spread over a patch
    whose middle is nearer the root. The held face's reaction is the weight."""
    answer = cae("load", "load/one.json", "--weight", "1", *TIP, "--over", "5")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    beam = answer.numbers["parts"]["beam"]
    assert beam["stretch_pct"] == pytest.approx(beam_strain(1), rel=0.1)
    assert beam["stretch_at"][0] < 10, "at the root"
    assert beam["stretch_from_layers_deg"] < 10, "along its length, in its layers' plane"
    inertia = BEAM_W * BEAM_T**3 / 12
    assert answer.numbers["moves_mm"] == pytest.approx(
        G * BEAM_L**3 / (3 * MODULUS * inertia), rel=0.06
    )
    assert answer.numbers["reaction_n"] == pytest.approx([0, 0, G], abs=1e-3)
    assert answer.numbers["force_n"] == pytest.approx(G)
    assert "second check: the same solve on a mesh of at most 7.5 mm" in answer.text
    assert "Prusament PETG's datasheet" in answer.text, "the limits' source"
    assert answer.record["seconds"] < 30


def test_a_beam_bolted_at_its_middle_matches_the_one_piece_beam(cae: Any) -> None:
    """The same beam as two halves, bolted where they meet at x 60: its worst strain and how far
    it moves are the one-piece beam's within 2%, and so is it bonded. The joint carries the
    weight as shear and, by statics, the weight times its distance to the load as a moment,
    which the solve's own balance gives within 0.2%. Its one bolt at the face's middle holds
    that moment about the face's bottom edge, 5 mm away: about 522 / 5 = 104 N."""
    one = cae("load", "load/one.json", "--weight", "1", *TIP)
    two = cae("load", "load/two.json", "--weight", "1", *TIP)
    assert two.verdict == "OK", two.text
    whole, root = one.numbers["parts"]["beam"], two.numbers["parts"]["root"]
    assert root["stretch_pct"] == pytest.approx(whole["stretch_pct"], rel=SAME)
    assert root["across_layers_pct"] == pytest.approx(whole["across_layers_pct"], rel=SAME)
    assert two.numbers["moves_mm"] == pytest.approx(one.numbers["moves_mm"], rel=SAME)
    assert two.numbers["most_moves_mm"] == pytest.approx(one.numbers["most_moves_mm"], rel=SAME)
    # The half past the joint is strained as the whole beam is there, 60 mm from the root.
    assert two.numbers["parts"]["tip"]["stretch_pct"] == pytest.approx(beam_strain(1, 60), rel=0.1)
    joint = two.numbers["joints"]["root+tip"]
    # The weight acts at the middle of its patch, which the beam's end cuts off: a little
    # nearer the root than the place named.
    middle = two.numbers["loads"][0]["middle"][0]
    assert BEAM_L - 4 < middle < BEAM_L
    moment = G * (middle - 60)
    assert joint["statics_force_n"] == pytest.approx([0, 0, -G], abs=1e-4)
    # The patch's middle is within a tenth of a millimetre of the beam's centre line.
    assert joint["statics_moment_nmm"] == pytest.approx([0, moment, 0], abs=1.0)
    assert joint["statics_moment_nmm"][1] == pytest.approx(moment, abs=0.01)
    # The solve's own balance at the joint's nodes is statics, to what its figures are saved at.
    assert joint["force_n"] == pytest.approx(joint["statics_force_n"], abs=0.01)
    assert joint["moment_nmm"] == pytest.approx(joint["statics_moment_nmm"], abs=1.0)
    assert joint["shear_n"] == pytest.approx(G, rel=1e-3)
    assert joint["bending_nmm"] == pytest.approx(moment, rel=2e-3)
    assert joint["bolt_pull_statics_n"] == pytest.approx(moment / (BEAM_T / 2), rel=1e-4)
    assert joint["bolt_pull_n"] == pytest.approx(moment / (BEAM_T / 2), rel=2e-3)
    assert reports.unlike("load", two.numbers) == []
    assert joint["holds_n"] == 500 and joint["holds_from"].startswith("the joint's own figure")
    bonded = cae("load", "load/two-bonded.json", "--weight", "1", *TIP)
    assert bonded.numbers["moves_mm"] == pytest.approx(two.numbers["moves_mm"], rel=1e-6)
    assert "bolts" not in bonded.numbers["joints"]["root+tip"]
    # Glue is a second hold only (mqucifer/print-shop#170): what a glued joint carries has
    # no figure to be read against, so it's unread, never `OK`, and no need passes on it.
    assert bonded.verdict == "UNREAD" and bonded.code == 2, bonded.text
    assert bonded.numbers["joints"]["root+tip"]["glue_unread"] is True
    assert "the bonded joint(s) root+tip carry load" in bonded.record["why"]
    assert "glue is a second hold only" in bonded.record["why"]
    holds = Need("holds", "it holds 1 kg", "request", "holds 1 kg", "bench",
                 "load load/two-bonded.json --weight 1 --at 115,10,10")  # fmt: skip
    assert not needs.judge(holds, bonded.code, bonded.text, bonded.record).passed


def test_a_part_laid_across_its_layers_fails_where_the_same_part_laid_along_them_passes(
    cae: Any,
) -> None:
    """The same beam under 5 kg bends to 1.13% at its root, along its length. Printed flat, its
    layers run along that pull, and 1.13% is within the 5.1% PETG takes in their plane. Printed
    standing on its end, the pull is across its layers, over the 1% they take."""
    along = cae("load", "load/one.json", "--weight", "5", *TIP)
    across = cae("load", "load/across.json", "--weight", "5", *TIP)
    assert along.verdict == "OK" and along.code == 0, along.text
    assert across.verdict == "FAIL" and across.code == 1, across.text
    flat, standing = along.numbers["parts"]["beam"], across.numbers["parts"]["beam"]
    assert flat["stretch_pct"] == pytest.approx(standing["stretch_pct"]), "the same solve"
    assert standing["across_layers_pct"] == pytest.approx(beam_strain(5), rel=0.1)
    assert standing["across_layers_pct"] > standing["across_limit_pct"] == 1.0
    assert flat["across_layers_pct"] < 1.0 < flat["stretch_pct"] < flat["stretch_limit_pct"] == 5.1
    assert standing["stretch_from_layers_deg"] > 80 > 10 > flat["stretch_from_layers_deg"]
    assert "pulled across its layers by" in across.record["why"]
    assert "lay it on the bed so its layers run along this pull" in across.record["why"]
    # Under 1 kg it's 0.23% either way, and both pass.
    assert cae("load", "load/across.json", "--weight", "1", *TIP).verdict == "OK"


def test_a_bolted_joint_with_no_figure_for_its_insert_is_unread_never_ok(cae: Any) -> None:
    """In printed PETG the insert pulls out long before the bolt gives, and no insert has a
    sourced figure. So the same two halves with no `pull_n` on their joint aren't `OK` on the
    bolt's 5092 N: the answer is `UNREAD`, naming the joint and the insert, and a need that
    asks for `OK` fails until the joint gives a figure. Every reading is still there."""
    answer = cae("load", "load/two-unread.json", "--weight", "1", *TIP)
    assert answer.verdict == "UNREAD" and answer.code == 2, answer.text
    why = answer.record["why"]
    assert "the bolted joint(s) root+tip have a pull to hold, 104.4 N a bolt" in why
    assert "the insert m4-heat-set has no pull-out figure" in why and "`pull_n`" in why
    joint = answer.numbers["joints"]["root+tip"]
    assert joint["insert_unread"] is True
    assert joint["holds_n"] == pytest.approx(580 * 8.78) and "ISO 898-1" in joint["holds_from"]
    read = cae("load", "load/two.json", "--weight", "1", *TIP)
    assert read.verdict == "OK" and read.numbers["joints"]["root+tip"]["insert_unread"] is False
    assert joint["bolt_pull_n"] == pytest.approx(read.numbers["joints"]["root+tip"]["bolt_pull_n"])
    command = "load load/two-unread.json --weight 1 --at 115,10,10"
    need = Need("holds", "it holds 1 kg", "request", "holds 1 kg", "bench", command)
    assert not needs.judge(need, answer.code, answer.text, answer.record).passed
    named = Need(
        "holds", "it holds 1 kg", "request", "holds 1 kg", "bench", command, verdicts=["UNREAD"]
    )
    assert not needs.judge(named, answer.code, answer.text, answer.record).passed, "never a pass"


def test_a_bolt_asked_to_hold_more_than_its_joint_s_figure_fails(cae: Any) -> None:
    """The joint's own figure, 60 N a bolt, is under the 108 N its bolt has to hold."""
    answer = cae("load", "load/two-weak.json", "--weight", "1", *TIP)
    assert answer.verdict == "FAIL", answer.text
    assert "worst bolt has to hold" in answer.record["why"]
    assert "over its 60 N" in answer.record["why"]
    assert answer.numbers["joints"]["root+tip"]["holds_from"].startswith("the joint's own figure")


def test_parts_that_dont_meet_face_to_face_are_said_and_nothing_is_solved(cae: Any) -> None:
    for name, said in (
        ("unnamed", "root and tip meet over 200.0 mm²"),
        ("gap", "they're 0.500 mm apart"),
        ("through", "pass through each other where the assembly puts them, by 200.000 mm³"),
    ):
        answer = cae("load", f"load/{name}.json", "--weight", "1", *TIP)
        assert answer.verdict == "NO ANSWER" and said in answer.record["why"], answer.text
    off = cae("load", "load/one.json", "--weight", "1", "--at", "115,10,15")
    assert off.verdict == "NO ANSWER"
    assert "5.000 mm from the assembly's surface" in off.record["why"]
    slides = cae("load", "load/rest.json", "--weight", "1", "--at", "40,30,25", "--toward", "+x")
    assert slides.verdict == "NO ANSWER" and "it would slide" in slides.record["why"]


def test_a_touching_joint_pushes_and_fails_where_its_pulled_or_tipped(cae: Any) -> None:
    """A block standing on a slab, held by nothing. A weight on its top pushes it down through
    the middle of the faces that meet: it holds, needing no friction to speak of. Pulled up, or
    pushed sideways with nothing pressing it down, it comes away."""
    rests = cae("load", "load/rest.json", "--weight", "2", "--at", "30,30,30")
    assert rests.verdict == "OK", rests.text
    joint = rests.numbers["joints"]["slab+block"]
    assert joint["pull_n"] == pytest.approx(-2 * G, rel=1e-3), "a push of the weight"
    assert joint["statics_force_n"] == pytest.approx([0, 0, -2 * G], abs=1e-4)
    assert joint["push_off_faces_mm"] == pytest.approx(0, abs=0.01)
    assert joint["friction_needed"] < 0.01
    assert "which has no limit unless --friction gives one" in rests.text
    up = ("--at", "30,30,30", "--toward", "+z")
    lifted = cae("load", "load/rest-fixed.json", "--weight", "2", *up)
    assert lifted.verdict == "FAIL" and "is pulled apart by" in lifted.record["why"], lifted.text
    side = ("--at", "40,30,25", "--toward", "+x")
    pushed = cae("load", "load/rest-fixed.json", "--weight", "2", *side)
    assert pushed.verdict == "FAIL", pushed.text
    assert "tips open" in pushed.record["why"] or "slides or tips" in pushed.record["why"]


def test_a_table_standing_on_four_legs_shares_its_weight_between_them(cae: Any) -> None:
    """A top on four legs, each bolted, standing free on the floor, with 3 kg at its middle:
    each joint pushes down with about a quarter of the weight, the four together with all of
    it, and statics alone can't say which leg takes what. A leg printed lying down has its
    layers turned with it."""
    answer = cae("load", "load/table.json", "--weight", "3", "--at", "50,50,66")
    assert answer.verdict == "OK", answer.text
    joints = answer.numbers["joints"]
    assert len(joints) == 4 and all("statics_force_n" not in j for j in joints.values())
    pushes = [-j["pull_n"] for j in joints.values()]
    assert sum(pushes) == pytest.approx(3 * G, rel=1e-3), "the four carry all of it"
    assert max(pushes) - min(pushes) < 0.02 * 3 * G / 4, "a quarter each, by symmetry"
    assert answer.numbers["held"] == ["standing on z=0, 4 face(s), 400.0 mm²"]
    assert answer.numbers["reaction_n"] == pytest.approx([0, 0, 3 * G], abs=1e-3)
    assert answer.record["seconds"] < 120
    lying = cae("load", "load/table-lying.json", "--weight", "3", "--at", "50,50,66")
    assert lying.numbers["parts"]["leg1"]["layers"] == pytest.approx([1, 0, 0])
    assert lying.numbers["parts"]["leg2"]["layers"] == pytest.approx([0, 0, 1])


def test_a_need_on_a_load_is_judged_by_its_verdict_and_how_far_it_moves(cae: Any) -> None:
    answer = cae("load", "load/two.json", "--weight", "1", *TIP)

    def need(**more: Any) -> Need:
        command = "load load/two.json --weight 1 --at 115,10,10"
        return Need("holds", "it holds 1 kg", "request", "holds 1 kg", "bench", command, **more)

    assert needs.judge(need(), answer.code, answer.text, answer.record).passed
    stiff = need(number="moves_mm", at_most=1.0)
    read = needs.judge(stiff, answer.code, answer.text, answer.record)
    assert not read.passed and read.value == pytest.approx(answer.numbers["moves_mm"])


# A through-bolt, its washers, its nut and the plastic round it (mqucifer/print-shop#168)

#: The plates, as `make_fixtures.py` draws them: 8 mm thick, a 5.5 mm hole, and an M5 washer
#: 10 mm across (ISO 7089) or an M5 nut bearing on a face 6.9 mm across (ISO 4032).
HOLE, WASHER, NUT_FACE = 5.5, 10.0, 6.9
RING = math.pi / 4 * (WASHER**2 - HOLE**2)
NUT_RING = math.pi / 4 * (NUT_FACE**2 - HOLE**2)
#: Pulled straight up, either side of the bolt, so the weight acts on its axis.
UP = ("--at", "40,20,16", "--at", "60,20,16", "--toward", "+z")
UP_POCKET = ("--at", "40,20,20", "--at", "60,20,20", "--toward", "+z")
ALONG = ("--at", "100,20,12", "--toward", "+x")


def test_a_through_bolt_s_washers_bear_as_the_hand_figure_says(cae: Any) -> None:
    """Two plates joined by one M5 through-bolt, the upper pulled straight up off the lower
    with 20 kg: the bolt holds the weight, and each washer presses it on the ring between the
    5.5 mm hole and its own 10 mm, π/4·(10² − 5.5²) = 54.78 mm²: 3.58 MPa by hand. The joint
    is read from the bolt, the washers and the plastic round each hole, and says which is
    nearest its limit: never from the bolt alone."""
    answer = cae("load", "through/pull.json", "--weight", "20", *UP)
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    joint = answer.numbers["joints"]["lower+upper"]
    assert joint["kind"] == "through"
    assert joint["statics_force_n"] == pytest.approx([0, 0, 20 * G], abs=1e-3)
    # The weight, and a little from the bending the joint's own stiffness leaves in it.
    assert joint["bolt_pull_n"] == pytest.approx(20 * G, rel=0.02)
    assert joint["holds_n"] == pytest.approx(580 * 14.2) and "ISO 898-1" in joint["holds_from"]
    head, nut = joint["bolts"][0]["ends"]
    assert (head["part"], nut["part"]) == ("lower", "upper")
    assert head["under"] == nut["under"] == "washer"
    for end in (head, nut):
        assert end["bears_mm2"] == pytest.approx(RING, rel=1e-4)
        assert end["pressure_mpa"] == pytest.approx(joint["bolt_pull_n"] / RING, rel=1e-4)
        assert end["pressure_mpa"] == pytest.approx(20 * G / RING, rel=0.02), "the hand figure"
        assert (end["pressure_limit_mpa"], end["pressed"]) == (45.0, "across its layers")
        assert (end["hole_mm"], end["hole_length_mm"]) == pytest.approx((HOLE, 8.0))
        assert "wall_pressure_mpa" not in end, "nothing leans on the hole: it's a straight pull"
        # The plastic round the hole, read as any part is, on two meshes.
        around = end["round"]
        assert 0 < around["across_layers_pct"] < 1.0 and 0 < around["stretch_pct"] < 5.1
        assert around["coarse"]["stretch_pct"] == pytest.approx(around["stretch_pct"], rel=0.1)
        assert around["held_mm"] == 2 * WASHER and around["coarse"]["mesh_mm"] > around["mesh_mm"]
    # The washers' pressure and the plastic round a hole are each about 8% of their limits here,
    # and which is the nearer depends on the mesh: the share is the larger of them, and named.
    pressed = head["pressure_mpa"] / 45
    assert pressed <= joint["nearest_share"] < 0.12
    assert joint["gives_first"].startswith(("the plastic round the hole", "the pressure under"))
    assert "the nearest its limit is" in answer.text
    assert "no compressive figure" in answer.text, "what stands in for it is said"
    assert "worst bolt by statics" in answer.text and "the plastic round" in answer.text
    assert reports.unlike("load", answer.numbers) == []
    assert answer.record["seconds"] < 90


def test_a_nut_in_a_pocket_bears_on_its_own_face_and_fails_where_the_bolt_alone_would_pass(
    cae: Any,
) -> None:
    """The same pull with the nut in a hex pocket and no washer under it: it bears on the ring
    between the hole and its 6.9 mm bearing face, 13.63 mm², a quarter of a washer's. Under
    20 kg that's 14.4 MPa, within the 45 PETG takes pressed across its layers, and it's what's
    named as nearest its limit. Under 70 kg it's 50 MPa: the joint fails on the plastic under
    the nut, where the bolt has a twelfth of its 8236 N to hold."""
    answer = cae("load", "through/pull-pocket.json", "--weight", "20", *UP_POCKET)
    assert answer.verdict == "OK", answer.text
    joint = answer.numbers["joints"]["lower+upper"]
    head, nut = joint["bolts"][0]["ends"]
    assert (head["under"], nut["under"]) == ("washer", "nut")
    assert nut["bears_mm2"] == pytest.approx(NUT_RING, rel=1e-3)
    assert nut["pressure_mpa"] == pytest.approx(20 * G / NUT_RING, rel=0.02)
    assert head["pressure_mpa"] == pytest.approx(20 * G / RING, rel=0.02)
    assert "the pressure under the nut m5-nut in its pocket on upper" in joint["gives_first"]
    assert joint["nearest_share"] == pytest.approx(nut["pressure_mpa"] / 45, rel=1e-6)
    heavy = cae("load", "through/pull-pocket.json", "--weight", "70", *UP_POCKET)
    assert heavy.verdict == "FAIL" and heavy.code == 1, heavy.text
    assert "the plastic under the nut m5-nut in its pocket is pressed by" in heavy.record["why"]
    assert "over its 45 MPa" in heavy.record["why"]
    crushed = heavy.numbers["joints"]["lower+upper"]
    assert crushed["bolt_pull_n"] < crushed["holds_n"] / 10, "the bolt alone would pass"
    assert crushed["nearest_share"] > 1
    need = Need(
        "holds", "it holds 70 kg", "request", "holds 70 kg", "bench",
        "load through/pull-pocket.json --weight 70 --at 40,20,20 --at 60,20,20 --toward +z",
    )  # fmt: skip
    assert not needs.judge(need, heavy.code, heavy.text, heavy.record).passed


def test_a_bolt_near_an_edge_tears_out_where_the_same_bolt_further_in_holds(cae: Any) -> None:
    """A lap pulled along with 50 kg, the upper plate printed on its edge: the bolt's shank
    leans on each hole with the whole weight, 12.3 MPa over its 5 mm by the 8 mm of hole.
    With the upper plate's hole 20 mm from its end, the plastic round it holds. With the same
    bolt 5.5 mm from the end, where its washer still sits wholly on the plate, the 2.75 mm of
    plastic between the hole and the edge is pulled across its layers past the 1% it takes:
    it tears out, and the reply says so. Everything else about the two is the same."""
    inner = cae("load", "through/lap-in.json", "--weight", "50", *ALONG)
    edge = cae("load", "through/lap-edge.json", "--weight", "50", *ALONG)
    assert inner.verdict == "OK" and inner.code == 0, inner.text
    assert edge.verdict == "FAIL" and edge.code == 1, edge.text
    why = edge.record["why"]
    assert "in upper: the plastic round the hole is pulled across its layers by" in why
    assert "it tears out there" in why and "further from the edge" in why
    assert "; and " not in why, "nothing else fails"
    held, torn = (a.numbers["joints"]["lower+upper"] for a in (inner, edge))
    for joint in (held, torn):
        assert joint["shear_n"] == pytest.approx(50 * G, rel=1e-3)
        for end in joint["bolts"][0]["ends"]:
            assert end["wall_pressure_mpa"] == pytest.approx(50 * G / (5 * 8), rel=1e-3)
            assert (end["wall_limit_mpa"], end["wall_pressed"]) == (14.0, "along its layers")
            assert end["pressure_mpa"] < 2, "its washers carry next to nothing"
    far, near = held["bolts"][0]["ends"][1]["round"], torn["bolts"][0]["ends"][1]["round"]
    assert near["across_layers_pct"] > 1.0 > far["across_layers_pct"]
    assert near["coarse"]["across_layers_pct"] > 1.0, "on both meshes"
    assert near["across_at"][0] == pytest.approx(40, abs=1.5), "at the plate's end"
    assert near["across_layers_pct"] > 2 * far["across_layers_pct"]
    # The lower plate's hole is far from any edge in both, and reads the same.
    below = [j["bolts"][0]["ends"][0]["round"]["across_layers_pct"] for j in (held, torn)]
    assert below[0] == pytest.approx(below[1], rel=0.05) and max(below) < 1.0
    assert "the plastic round the hole in upper" in torn["gives_first"]
    assert torn["nearest_share"] > 1 > held["nearest_share"]


def test_a_through_bolt_with_no_hole_to_pass_through_is_said_and_nothing_is_solved(
    cae: Any,
) -> None:
    answer = cae("load", "through/pull-no-hole.json", "--weight", "20", "--at", "40,20,16")
    assert answer.verdict == "NO ANSWER" and answer.code == 2, answer.text
    assert "in upper, no round hole has its axis through that point" in answer.record["why"]
    assert "passes right through both parts" in answer.record["why"]
    assert answer.record["seconds"] < 10


# A bore that lies level on the bed (mqucifer/print-shop#237)

#: Its half gap, 47°, and the peak's point that puts.
RISES = math.radians(47)
POINT = HOLE / 2 / math.cos(RISES)
LEVEL = ("--at", "10,15,20", "--mesh", "6")


def peak_takes(outer: float) -> float:
    """What the peak takes from the ring out to `outer` across, by summing it in thin rings."""
    edges = np.linspace(HOLE / 2, min(outer / 2, POINT), 20_001)
    r = (edges[:-1] + edges[1:]) / 2
    open_either_side = RISES - np.arccos(HOLE / 2 / r)
    return float((2 * r * open_either_side * np.diff(edges)).sum())


def test_a_peaked_bore_s_washers_and_shank_bear_on_what_s_there(cae: Any) -> None:
    """`load` answered `NO ANSWER`, a slot's end. Each washer's seat is its ring less the peak's
    cut, and the shank that leans at the peak's point has 1 - sin 47° of the round's bearing."""
    answer = cae("load", "peak/peaked.json", "--weight", "5", *LEVEL)
    assert answer.verdict == "OK", answer.text
    joint = answer.numbers["joints"]["one+two"]
    seat = RING - peak_takes(WASHER)
    assert seat < RING - 1
    head, nut = joint["bolts"][0]["ends"]
    for end in (head, nut):
        assert end["bears_mm2"] == pytest.approx(seat, rel=1e-4)
        assert (end["ring_mm2"], end["seat_mm2"]) == pytest.approx((RING, seat), rel=1e-5)
        assert end["pressure_mpa"] == pytest.approx(joint["bolt_pull_n"] / seat, rel=1e-4)
        assert end["pressure_limit_mpa"] == 14.0, "the limit it always had"
        assert end["peak"]["toward"] == pytest.approx([0, 0, 1], abs=1e-6)
    toward, away = 5 * 20 * (1 - math.sin(RISES)), 5 * 20
    assert head["wall_bears_mm2"] == pytest.approx(toward, rel=1e-3)
    assert nut["wall_bears_mm2"] == pytest.approx(away, rel=1e-3)
    assert head["wall_pressure_mpa"] == pytest.approx(5 * G / toward, rel=2e-3)
    assert head["round"]["wall_share"] == pytest.approx(1 - math.sin(RISES), rel=0.08)
    # The shear is on the stretch's half less the peak (mqucifer/print-shop#245).
    assert head["sheared_mm2"] == pytest.approx(HOLE / 2 * 20 * (math.pi - 2 * RISES), rel=0.08)
    assert nut["sheared_mm2"] == pytest.approx(math.pi * HOLE / 2 * 20, rel=0.06)
    assert "the half leant on, less the peak, of the 20.00 mm" in answer.text
    assert answer.text.count("less the peak, of the") == 1, "the lean away from it loses nothing"
    assert "peaked, 5.500 mm across" in answer.text
    assert "for a lean 0° from its point" in answer.text
    assert reports.unlike("load", answer.numbers) == []


def test_a_nut_s_seat_cut_by_the_peak_is_pressed_over_what_s_left(cae: Any) -> None:
    """It read round, then failed: the nut on 12.1 of its ring's 13.6 mm². That area is its
    seat, and its pressure is held to the same 14 MPa: 4.0 under 5 kg, and over under 20."""
    seat = NUT_RING - peak_takes(NUT_FACE)
    answer = cae("load", "peak/slot.json", "--weight", "5", *LEVEL)
    assert answer.verdict == "OK", answer.text
    head, nut = answer.numbers["joints"]["one+two"]["bolts"][0]["ends"]
    assert "peak" not in head and head["bears_mm2"] == pytest.approx(RING, rel=1e-3)
    assert nut["bears_mm2"] == pytest.approx(seat, rel=1e-3)
    assert seat == pytest.approx(12.1, abs=0.1)
    assert nut["hole_length_mm"] == pytest.approx(10), "the stretch as far as the slot"
    assert nut["round"]["wall_share"] == pytest.approx(1, rel=0.05), "the same stretch"
    assert nut["sheared_mm2"] == pytest.approx(half_wall(10), rel=0.06), "none past the slot"
    heavy = cae("load", "peak/slot.json", "--weight", "20", *LEVEL)
    assert heavy.verdict == "FAIL", heavy.text
    assert "the plastic under the nut m5-nut in its pocket is pressed by" in heavy.record["why"]
    assert "over its 14 MPa" in heavy.record["why"] and "overhangs" not in heavy.record["why"]


@pytest.mark.parametrize("name", ["end", "groove", "d", "flat", "short", "split"])
def test_what_is_no_peaked_bore_is_still_refused_by_load(cae: Any, name: str) -> None:
    """A slot's end, a half-round groove, a D, a peak at 44°, one short of its point, and a D
    cut in two whose arcs add up past 360°."""
    answer = cae("load", f"peak/{name}.json", "--weight", "5", *LEVEL)
    assert answer.verdict == "NO ANSWER" and answer.code == 2, answer.text
    assert "in two, the round face there doesn't go all the way round" in answer.record["why"]
    if name == "flat":
        assert "flatter than 45°" in answer.record["why"]


@pytest.mark.parametrize("name", ["edge", "notched"])
def test_a_peaked_bore_s_seat_cut_by_anything_else_still_fails(cae: Any, name: str) -> None:
    answer = cae("load", f"peak/{name}.json", "--weight", "5", *LEVEL)
    assert answer.verdict == "FAIL", answer.text
    assert "in two: the washer m5-washer under the nut sits on" in answer.record["why"]
    assert "less the peak's cut: it overhangs an edge or a step" in answer.record["why"]


# The shank's shear, on its own stretch of the bore (mqucifer/print-shop#245)

#: Beside where the blocks meet, so the bolt has the shear and little pull.
NEAR_MEET = ("--weight", "20", "--at", "18,15,20", "--over", "1.5", "--mesh", "6")


def half_wall(length: float) -> float:
    """The half of a hole's round wall a shank leans on."""
    return math.pi * HOLE / 2 * length


def test_a_bolt_s_shear_is_only_on_its_own_stretch_of_the_bore(cae: Any) -> None:
    """The second block's bore runs x 20 to 30 to the nut's slot and on from 34.9 for the bolt's
    tip. The shear was on both, and the plastic round it read 0.228% across its layers at the
    seat. On the stretch alone it's 0.284%, in the stretch, and lengthening what's past the
    slot doesn't move the wall that takes it."""
    answer = cae("load", "shear/round-slot.json", *NEAR_MEET)
    assert answer.verdict == "OK", answer.text
    one, two = answer.numbers["joints"]["one+two"]["bolts"][0]["ends"]
    assert two["round"]["across_layers_pct"] == pytest.approx(0.284, rel=0.1)
    assert two["round"]["across_layers_pct"] > 0.26, "0.228 with the far stretch loaded"
    assert 20 < two["round"]["across_at"][0] < 30
    assert two["hole_length_mm"] == pytest.approx(10)
    assert two["sheared_mm2"] == pytest.approx(half_wall(10), rel=0.06)
    assert one["sheared_mm2"] == pytest.approx(half_wall(20), rel=0.06)
    assert two["wall_pressure_mpa"] == pytest.approx(20 * G / (5 * 10), rel=1e-3), "as it was"
    assert "and none past them, as joints.one+two.bolts.0.ends.1.sheared_mm2" in answer.text
    assert reports.unlike("load", answer.numbers) == []
    longer = cae("load", "shear/longer.json", *NEAR_MEET)
    further = longer.numbers["joints"]["one+two"]["bolts"][0]["ends"][1]
    assert further["sheared_mm2"] == pytest.approx(two["sheared_mm2"], rel=0.02)


def test_where_shear_rules_a_bore_past_the_slot_doesn_t_hide_the_tear(cae: Any) -> None:
    """4 mm of bore to a washer's slot, 11 mm past it, 5.2 mm above the bottom. With 20 kg the
    plastic round it was 0.31% across its layers and `OK`, the nearest limit all the same. With
    the shear on the 4 mm it's 1.19% of 1%: it tears out."""
    at = ("--at", "19,15,20", "--over", "1.5", "--mesh", "6")
    answer = cae("load", "shear/tears.json", "--weight", "20", *at)
    assert answer.verdict == "FAIL", answer.text
    why = answer.record["why"]
    assert "in two: the plastic round the hole is pulled across its layers by" in why
    assert "; and " not in why, "nothing else fails"
    joint = answer.numbers["joints"]["one+two"]
    assert "the plastic round the hole in two" in joint["gives_first"]
    near = joint["bolts"][0]["ends"][1]["round"]
    assert near["across_layers_pct"] == pytest.approx(1.19, rel=0.1)
    assert near["coarse"]["across_layers_pct"] > 1.0, "on both meshes"
    assert 20 < near["across_at"][0] < 24


def test_a_counterbore_at_the_head_still_isn_t_wall(cae: Any) -> None:
    """With no far stretch, and the head sunk 4 mm: 16 mm of bore, and no more."""
    answer = cae("load", "shear/sunk.json", *NEAR_MEET)
    assert answer.verdict == "OK", answer.text
    one, two = answer.numbers["joints"]["one+two"]["bolts"][0]["ends"]
    assert one["hole_length_mm"] == pytest.approx(16)
    assert one["sheared_mm2"] == pytest.approx(half_wall(16), rel=0.06)
    assert two["sheared_mm2"] == pytest.approx(half_wall(20), rel=0.06)


# A tapered dovetail, its latch and the lift (mqucifer/print-shop#170)

#: The bars, as `make_fixtures.py` draws them: the lower 30 x 20 x 70 with the socket in it, the
#: upper 30 x 12 from z 30 to 110 with the tail on it, lapped on the plane y 0 from z 30 to 70.
#: The tail is 6 mm proud, 6 mm wide at its neck at its tip and 10 at its top, and 2 mm wider a
#: side at its far face. A flank leans 2 mm in 40 down the slide and 2 in 6 into the socket, so
#: its share along the slide is 12 / |(240, 80, 12)|.
TAPER = 12 / math.hypot(240, 80, 12)
JOINT = "upper+lower"
ON_TOP = ("--at", "0,6,110")
UPWARD = (*ON_TOP, "--toward", "+z")
ON_ARM = ("--at", "0,62,110")
#: The latch's tab: 5 mm wide and 1.2 thick, its hook 0.6 into the window, its face 0.4 off
#: the lower bar. What it holds acts on the catch's middle, 1.3 mm off the tab's own.
TAB_W, TAB_T, HOOK_IN, TAB_OFF = 5.0, 1.2, 0.6, 0.4 + 1.2 / 2 + 0.6 / 2
#: The lower bar with its socket and its latch's window, in mm³: the socket is 6 deep and
#: its width at the neck and at the far face average 10 between them over its 40 mm.
LOWER_MM3 = 30 * 20 * 70 - 6 * 10 * 40 - 7 * 1.2 * 2.7
DENSITY = 1.27


def driven_out(why: str) -> tuple[float, str]:
    """The force a reply says drives a tail out of its seat, in N, and which way."""
    found = re.search(r"drives the tail out of its seat by (\S+) N, ([+-][xyz]),", why)
    assert found, why
    return float(found.group(1)), found.group(2)


def flanks(joint: dict[str, Any]) -> list[dict[str, Any]]:
    """A dovetail's two leaning flanks, of its faces."""
    return [f for f in joint["faces"] if abs(f["normal"][0]) > 0.9]


def test_a_weight_that_seats_a_tapered_dovetail_holds_with_every_flank_pushing(cae: Any) -> None:
    """5 kg on the upper bar drives its tail down the taper into its seat, by the weight. Its
    faces only push, and together they're what the joint carries: the seat under the tail's
    tip takes most of the weight, being square to the slide, and both flanks, the socket's
    floor and the lap faces push too, holding the bars' offset. The tail's neck and the
    socket's walls are then read under those pushes, each part by itself."""
    answer = cae("load", "dovetail/seat.json", "--weight", "5", *ON_TOP)
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    joint = answer.numbers["joints"][JOINT]
    assert (joint["kind"], joint["slides"]) == ("dovetail", "-z")
    assert joint["statics_force_n"] == pytest.approx([0, 0, 5 * G], abs=1e-3)
    assert joint["drive_n"] == pytest.approx(5 * G, rel=1e-3), "into its seat, by the weight"
    assert (joint["opens"], joint["latched"]) == (False, False)
    faces = joint["faces"]
    assert sorted(f["kind"] for f in faces) == ["flank"] * 5 + ["seat"]
    assert all(f["push_n"] > 0 and f["pressure_mpa"] > 0 for f in faces), "every one pushes"
    assert len(flanks(joint)) == 2 and all(f["kind"] == "flank" for f in flanks(joint))
    # The pushes, each square to its face and into the tail, are what the joint carries.
    carried = -np.sum([np.array(f["normal"]) * f["push_n"] for f in faces], axis=0)
    assert carried == pytest.approx(joint["force_n"], abs=0.05)
    (seat,) = (f for f in faces if f["kind"] == "seat")
    assert seat["normal"] == pytest.approx([0, 0, -1], abs=1e-6)
    assert seat["area_mm2"] == pytest.approx(48.0, rel=1e-3), "(6 + 10) / 2 x 6 at its tip"
    assert 0.85 * 5 * G < seat["push_n"] < 5 * G
    for piece, part in ((joint["neck"], "upper"), (joint["walls"], "lower")):
        assert piece["part"] == part
        assert 0 < piece["across_layers_pct"] < 1.0 and 0 < piece["stretch_pct"] < 5.1
        assert piece["coarse"]["mesh_mm"] > piece["mesh_mm"]
    assert 0 < joint["nearest_share"] < 0.2
    assert "the nearest its limit is" in answer.text
    assert "no glue and no friction" in answer.text, "what it leaves out is said"
    assert "faces on the coarser mesh: they balance" in answer.text
    assert "faces by statics: they balance" in answer.text
    assert reports.unlike("load", answer.numbers) == []
    assert answer.record["seconds"] < 120


def test_the_reverse_load_on_a_dovetail_with_no_latch_fails_naming_the_unseating_force(
    cae: Any,
) -> None:
    """The same joint with the upper bar pulled up: that's the way its tail came in, and no
    face of it pushes back that way. With no latch it fails, naming the 49 N, and says only
    friction or glue would hold it: neither is counted. Nothing is read under a load the
    joint doesn't hold."""
    answer = cae("load", "dovetail/seat.json", "--weight", "5", *UPWARD)
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    why = answer.record["why"]
    newtons, way = driven_out(why)
    assert newtons == pytest.approx(5 * G, abs=0.02) and way == "+z"
    assert "it has no latch: only friction or glue would hold it" in why
    assert "neither is counted" in why
    joint = answer.numbers["joints"][JOINT]
    assert joint["drive_n"] == pytest.approx(-5 * G, rel=1e-3)
    assert joint["opens"] is True and joint["latched"] is False
    assert np.linalg.norm(joint["left_n"]) == pytest.approx(5 * G, rel=0.05)
    assert "neck" not in joint and "walls" not in joint and "latch" not in joint
    assert reports.unlike("load", answer.numbers) == []


def test_a_latch_is_read_against_the_unseating_force_and_holds(cae: Any) -> None:
    """The same pull, 0.5 kg, with a tab on the upper bar whose hook sits 0.2 mm under a
    ledge in the lower: the catch is found from the two parts' own faces, 5 x 0.6 mm. The
    latch holds what draws the tail out, and a little more that the flanks add as the bars'
    offset pushes them. Its tab is then solved with that pull on its hook: pulled along, and
    bent by the hook's 1.3 mm offset, ε = F/(E·w·t) + F·e·(t/2)/(E·w·t³/12)."""
    answer = cae("load", "dovetail/latch.json", "--weight", "0.5", *UPWARD)
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    joint = answer.numbers["joints"][JOINT]
    drawn = 0.5 * G
    assert joint["drive_n"] == pytest.approx(-drawn, rel=1e-3)
    assert (joint["opens"], joint["latched"]) == (False, True)
    latch = joint["latch"]
    assert latch["on"] == "upper"
    assert latch["catch_mm2"] == pytest.approx(TAB_W * HOOK_IN, rel=1e-6)
    assert latch["catch_at"] == pytest.approx([0, -HOOK_IN / 2, 12], abs=1e-6)
    assert latch["play_mm"] == pytest.approx(0.2, abs=1e-6)
    assert drawn <= latch["holds_n"] < 1.1 * drawn
    assert latch["pressure_mpa"] == pytest.approx(latch["holds_n"] / (TAB_W * HOOK_IN))
    tab = latch["tab"]
    section, inertia = TAB_W * TAB_T, TAB_W * TAB_T**3 / 12
    by_hand = 100 * latch["holds_n"] / MODULUS * (1 / section + TAB_OFF * TAB_T / 2 / inertia)
    assert tab["stretch_pct"] == pytest.approx(by_hand, rel=0.3) and tab["stretch_is"] == "pull"
    assert tab["coarse"]["stretch_pct"] == pytest.approx(by_hand, rel=0.3)
    assert tab["across_layers_pct"] < 1.0 and tab["thickness_mm"] == pytest.approx(TAB_T)
    assert "its latch, a tab on upper, catches lower over 3.00 mm²" in answer.text
    assert "its latch holding" in answer.text, "on the coarser mesh and by statics too"
    assert reports.unlike("load", answer.numbers) == []
    assert answer.record["seconds"] < 120


def test_a_latch_s_tab_is_read_across_its_layers(cae: Any) -> None:
    """Under 1.5 kg the tab is stretched 1.5% along its length. With its bar printed lap
    face down, that's along its layers, and it holds. With the bar printed standing, the
    tab's length is across its layers, over the 1% they take: it fails, on both meshes."""
    lying = cae("load", "dovetail/latch.json", "--weight", "1.5", *UPWARD)
    standing = cae("load", "dovetail/latch-standing.json", "--weight", "1.5", *UPWARD)
    assert lying.verdict == "OK", lying.text
    assert standing.verdict == "FAIL" and standing.code == 1, standing.text
    assert "'s tab is pulled across its layers by" in standing.record["why"]
    assert "the tab's length runs along its layers" in standing.record["why"]
    flat, upright = (a.numbers["joints"][JOINT]["latch"]["tab"] for a in (lying, standing))
    assert flat["stretch_pct"] == pytest.approx(upright["stretch_pct"]), "the same solve"
    assert upright["across_layers_pct"] > 1.0 and upright["coarse"]["across_layers_pct"] > 1.0
    assert flat["across_layers_pct"] < 1.0 < flat["stretch_pct"] < 5.1
    assert "the latch's tab" in standing.numbers["joints"][JOINT]["gives_first"]


def test_a_taper_pointing_the_wrong_way_for_its_load_fails(cae: Any) -> None:
    """The same bars with the tail widest at its foot: it goes in upward, so the weight on
    its bar drives it back out, and nothing holds it. And a file that says a tail goes in a
    way its own faces lean against is refused before anything is solved."""
    answer = cae("load", "dovetail/wrong.json", "--weight", "5", *ON_TOP)
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    newtons, way = driven_out(answer.record["why"])
    assert newtons == pytest.approx(5 * G, abs=0.02) and way == "-z"
    assert "point the taper so that this load seats it" in answer.record["why"]
    joint = answer.numbers["joints"][JOINT]
    assert joint["slides"] == "+z" and joint["drive_n"] == pytest.approx(-5 * G, rel=1e-3)
    lied = cae("load", "dovetail/lied.json", "--weight", "5", *ON_TOP)
    assert lied.verdict == "NO ANSWER" and lied.code == 2, lied.text
    assert "against the way the file says the tail slides in, +z" in lied.record["why"]
    assert "the tail could never have gone in that way" in lied.record["why"]
    assert lied.record["seconds"] < 10


def test_a_tail_seated_on_its_flanks_alone_pushes_them_as_a_wedge_does(cae: Any) -> None:
    """With the socket 1 mm deeper than the tail is long, nothing is under the tail's tip:
    it's seated on its two flanks, and they alone have a share along the slide. So their
    pushes add up to the weight over the taper's slope, about 21 times it, whatever else
    the joint carries: 2 kg puts 207 N on each."""
    answer = cae("load", "dovetail/wedge.json", "--weight", "2", *ON_TOP)
    assert answer.verdict == "OK", answer.text
    joint = answer.numbers["joints"][JOINT]
    # The tip is 1 mm clear of the socket's floor: a seat with that much play
    # (mqucifer/print-shop#188). The tail slides a quarter of a mm down its taper as the
    # flanks are pressed, short of closing it, so nothing under it bears.
    seats = [f for f in joint["faces"] if f["kind"] == "seat"]
    assert all(f["play_mm"] == pytest.approx(1.0, abs=1e-6) for f in seats)
    assert all(f["push_n"] == 0 and f["closed_mm"] < 0.5 for f in seats), "no seat bears"
    assert all(f["play_mm"] == 0 for f in joint["faces"] if f["kind"] == "flank")
    left, right = flanks(joint)
    assert left["push_n"] + right["push_n"] == pytest.approx(joint["drive_n"] / TAPER, rel=1e-3)
    assert left["push_n"] == pytest.approx(2 * G / (2 * TAPER), rel=0.01)
    assert left["push_n"] == pytest.approx(right["push_n"], rel=0.01)
    assert abs(left["normal"][2]) == pytest.approx(TAPER, rel=1e-3)
    # The wedge spreads the socket: its walls are what's nearest their limit.
    assert joint["walls"]["across_layers_pct"] > joint["neck"]["across_layers_pct"]


def test_a_tail_s_neck_printed_the_wrong_way_fails_across_its_layers(cae: Any) -> None:
    """30 kg at the end of an arm on the upper bar seats the tail and levers its top off the
    lap face, so the tail hangs by its neck there, pulled off its bar. Printed lap face down,
    the tail is built up from that face layer on layer, and the pull is across them: over
    1%, on both meshes, and nothing else fails. Printed on its side, the same pull runs
    along its layers, and the same joint holds."""
    across = cae("load", "dovetail/neck-across.json", "--weight", "30", *ON_ARM)
    along = cae("load", "dovetail/neck-along.json", "--weight", "30", *ON_ARM)
    assert across.verdict == "FAIL" and across.code == 1, across.text
    assert along.verdict == "OK" and along.code == 0, along.text
    why = across.record["why"]
    assert "the tail's neck, in upper is pulled across its layers by" in why
    assert "lay the part so its layers run along this pull" in why
    assert "; and " not in why, "nothing else fails"
    wrong, right = (a.numbers["joints"][JOINT] for a in (across, along))
    assert wrong["neck"]["across_layers_pct"] > 1.0
    assert wrong["neck"]["coarse"]["across_layers_pct"] > 1.0, "on both meshes"
    assert wrong["neck"]["across_at"][2] > 60, "at the tail's top, where it's levered off"
    assert right["neck"]["across_layers_pct"] < 1.0
    assert right["neck"]["stretch_pct"] == pytest.approx(wrong["neck"]["stretch_pct"])
    assert 1.0 < right["neck"]["stretch_pct"] < 5.1, "the same pull, along its layers"
    assert "the tail's neck, in upper, pulled across its layers" in wrong["gives_first"]
    assert wrong["nearest_share"] > 1 > right["nearest_share"]
    # The weight seats it harder than the levered flanks squeeze it out, so its faces hold.
    assert wrong["drive_n"] == pytest.approx(30 * G, rel=1e-3) and wrong["opens"] is False
    assert all(f["pressure_mpa"] < f["pressure_limit_mpa"] for f in right["faces"])


def test_lifted_by_its_upper_part_the_lower_hangs_by_its_own_weight_on_the_latch(
    cae: Any,
) -> None:
    """Lifted by the upper bar, the lower one hangs from its dovetail, pulled the way the
    tail slides out, by its own weight: its solid volume, 39,577 mm³ by hand, at Prusament
    PETG's 1.27 g/cm³, so 50.3 g and 0.493 N. The latch holds that, and the same joint
    with no latch drops it."""
    answer = cae("load", "dovetail/latch.json", "--lift", "upper")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    grams = LOWER_MM3 * DENSITY / 1000
    weight = grams / 1000 * G
    assert answer.numbers["lift"] == "upper"
    (hangs,) = answer.numbers["loads"]
    assert hangs["part"] == "lower"
    assert hangs["volume_mm3"] == pytest.approx(LOWER_MM3, rel=1e-5)
    assert hangs["grams"] == pytest.approx(grams, rel=1e-5)
    assert answer.numbers["force_n"] == pytest.approx(weight, rel=1e-5)
    assert answer.numbers["weight_kg"] == pytest.approx(grams / 1000, rel=1e-5)
    assert answer.numbers["reaction_n"] == pytest.approx([0, 0, weight], abs=1e-4)
    assert answer.numbers["held"][0].startswith("fixed to z=110"), "the upper bar's top"
    joint = answer.numbers["joints"][JOINT]
    assert joint["statics_force_n"] == pytest.approx([0, 0, -weight], abs=1e-4)
    assert joint["drive_n"] == pytest.approx(-weight, rel=1e-3), "the way the tail slides out"
    assert joint["latched"] is True
    assert weight <= joint["latch"]["holds_n"] < 1.1 * weight
    assert "lifted by upper" in answer.text and "the assembly's own `held` set aside" in answer.text
    assert '"Density [g/cm3] 1.27", by ISO 1183' in answer.text, "the density's source"
    assert "hangs by its own weight" in answer.text
    assert reports.unlike("load", answer.numbers) == []
    dropped = cae("load", "dovetail/seat.json", "--lift", "upper")
    assert dropped.verdict == "FAIL" and "it has no latch" in dropped.record["why"]
    assert dropped.numbers["joints"][JOINT]["drive_n"] < 0


# A dovetail with play (mqucifer/print-shop#188)

#: The blocks, as `make_fixtures.py` draws them: a square tail 20 x 20 and 20 long under a
#: block 40 x 40 x 20, in a socket in a block 40 x 40 x 80 whose walls are `SLIP` clear of it,
#: or `LOOSE`; a slender tab, its hook `LATCH_PLAY` under its ledge, near the +x edge or at the
#: middle. A push at the upper block's -x side near its top.
SLIP, LOOSE, LATCH_PLAY = 0.15, 1.5, 0.2
AT_SIDE = ("--at", "-20,0,98", "--toward", "+x")


def played(joint: dict[str, Any]) -> list[dict[str, Any]]:
    """A dovetail's faces with play, of its faces."""
    return [f for f in joint["faces"] if f["play_mm"] > 0]


def test_lifted_by_a_latch_off_its_middle_the_lower_tilts_till_its_flanks_close(cae: Any) -> None:
    """mqucifer/print-shop#188's lift. The lower block hangs from a slender latch 14 mm and
    15 mm off the joint's middle, the tail's flanks 0.15 mm clear of its socket. Read with only
    the faces drawn touching, the seat beside the latch levered against it and the latch held
    9.5 N of a 1.5 N block: its tab failed across its layers. Read as a print takes it, the
    lower drops by the latch's own 0.2 mm of play, its seats come away, and it tilts on the
    latch until its flanks close: the latch holds the block's weight, and its flanks the turn,
    each of their 0.15 mm closed."""
    answer = cae("load", "play/edge.json", "--lift", "upper")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    weight = answer.numbers["force_n"]
    joint = answer.numbers["joints"][JOINT]
    assert (joint["latched"], joint["opens"]) == (True, False)
    assert joint["latch"]["holds_n"] == pytest.approx(weight, rel=0.02)
    assert joint["latch"]["tab"]["across_layers_pct"] < 0.5
    clear = played(joint)
    assert len(clear) == 4 and all(f["kind"] == "flank" for f in clear)
    assert all(f["play_mm"] == pytest.approx(SLIP, abs=1e-6) for f in clear)
    closed = [f for f in clear if f["push_n"] > 0]
    assert closed and all(f["closed_mm"] == pytest.approx(SLIP, abs=1e-6) for f in closed)
    assert all(f["push_n"] == 0 for f in joint["faces"] if f["kind"] == "seat"), "they come away"
    assert joint["shift_mm"][2] > LATCH_PLAY, "the lower drops by the latch's play, and more"
    assert 0 < np.linalg.norm(joint["turn_deg"]) < 2
    assert "it's read the way a print takes this load" in answer.text
    assert "of play closed" in answer.text
    assert "times the" not in answer.text, "no lever"
    assert "its latch holding" in answer.text, "on the coarser mesh and by statics too"
    assert reports.unlike("load", answer.numbers) == []


def test_pushed_sideways_the_flanks_close_and_the_latch_s_faces_carry_nothing(cae: Any) -> None:
    """mqucifer/print-shop#188's sideways push: 2 kg at the upper block's side near its top,
    toward +x, the latch at the middle with the window's sides drawn touching its hook's. Read
    with only the faces drawn touching, those two small faces, 1.2 mm² each, took the push at
    over 2000 MPa and the joint opened. The latch's tab is the latch's, and none of its faces
    bears for the tail: the tail shifts and turns until its flanks close, and they carry it."""
    answer = cae("load", "play/middle.json", "--weight", "2", *AT_SIDE)
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    joint = answer.numbers["joints"][JOINT]
    assert (joint["latched"], joint["opens"]) == (False, False)
    assert joint["latch"]["holds_n"] == 0
    assert all(f["area_mm2"] > 10 for f in joint["faces"]), "no face of the hook's"
    closed = [f for f in played(joint) if f["push_n"] > 0]
    assert closed and all(f["closed_mm"] == pytest.approx(SLIP, abs=1e-6) for f in closed)
    pushing = [f for f in joint["faces"] if f["push_n"] > 0]
    carried = -np.sum([np.array(f["normal"]) * f["push_n"] for f in pushing], axis=0)
    assert carried == pytest.approx(joint["force_n"], abs=0.1)
    # The -x flank of the tail is pushed +x, and the socket's wall it closes on pushes back.
    assert max(closed, key=lambda f: f["push_n"])["normal"][0] == pytest.approx(1, abs=1e-6)
    assert joint["shift_mm"][0] > 0, "the tail moves the way it's pushed"
    assert all(f["pressure_mpa"] < f["pressure_limit_mpa"] for f in joint["faces"])
    assert joint["walls"]["across_layers_pct"] < 1.0
    assert reports.unlike("load", answer.numbers) == []


def test_with_more_play_than_the_tilt_closes_the_latch_is_levered_and_fails(cae: Any) -> None:
    """The same lift with the socket 1.5 mm clear of the tail: the tilt on the latch closes
    the seat beside it before any flank, and the latch and that seat pull against each other,
    as a lever's ends do. The latch holds several times the block's weight, and its slender
    tab is pulled past what its layers take: it fails, and says it's levered."""
    answer = cae("load", "play/loose.json", "--lift", "upper")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    weight = answer.numbers["force_n"]
    joint = answer.numbers["joints"][JOINT]
    assert joint["latch"]["holds_n"] > 3 * weight
    assert "'s tab is pulled across its layers by" in answer.record["why"]
    assert "times the" in answer.text and "as a lever's ends do" in answer.text
    clear = played(joint)
    assert clear and all(f["play_mm"] == pytest.approx(LOOSE, abs=1e-6) for f in clear)
    assert all(f["push_n"] == 0 and f["closed_mm"] < LOOSE for f in clear), "none closes"
    assert any(f["kind"] == "seat" and f["push_n"] > weight for f in joint["faces"])
    assert "the nearest to closing came" in answer.text
    assert "more than the 2 mm of play read" in answer.text, "it turns far, and says so"


def test_with_more_play_than_is_read_a_sideways_push_opens_the_joint(cae: Any) -> None:
    """The middle latch's push with the socket 2.5 mm clear, more than the 2 mm `load` reads
    as able to close: no flank is counted, the seats only push down the slide, and the latch's
    faces are the latch's, so nothing holds the push. It fails, saying it opens and how much
    play was read: wider play is never read as holding."""
    answer = cae("load", "play/wide.json", "--weight", "2", *AT_SIDE)
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    joint = answer.numbers["joints"][JOINT]
    assert joint["opens"] is True and played(joint) == []
    why = answer.record["why"]
    assert "opens" in why and "Faces up to 2 mm apart are counted" in why
    assert np.linalg.norm(joint["left_n"]) > 10


def test_straight_down_a_slip_fit_taper_keeps_its_weight_on_its_seat(cae: Any) -> None:
    """The tapered tail seated on its tip, its socket 0.15 mm clear of its every other face,
    5 kg on the upper bar, 9 mm off the joint's middle. Read with only the faces drawn
    touching, nothing held the weight's turn and the joint opened. The seat under the tail's
    tip stops it sliding down within hundredths of a mm, far short of the flanks' play, so
    the weight stays on the seat; the weight's turn closes the flanks, whose undercut holds it,
    and their slope takes a few percent of the weight with it."""
    answer = cae("load", "dovetail/slip.json", "--weight", "5", *ON_TOP)
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    joint = answer.numbers["joints"][JOINT]
    assert joint["opens"] is False
    (seat,) = (f for f in joint["faces"] if f["kind"] == "seat")
    assert seat["play_mm"] == 0 and 0.95 * 5 * G < seat["push_n"] < 5 * G, "on the seat"
    # How far the seat itself moves: the tail's shift at the joint's middle and its turn.
    there = np.array(seat["at"]) - np.array(joint["at"])
    moved = np.array(joint["shift_mm"]) + np.cross(np.radians(joint["turn_deg"]), there)
    assert -0.2 * SLIP < moved[2] < 0, "it slides down a hundredth or two, far short of the play"
    leaning = flanks(joint)
    assert len(leaning) == 2
    assert all(f["play_mm"] == pytest.approx(SLIP, abs=1e-6) for f in leaning)
    assert all(f["closed_mm"] == pytest.approx(SLIP, abs=1e-6) for f in leaning), "turned shut"
    assert abs(joint["turn_deg"][0]) > 0.1, "about x, the weight's turn"
    assert reports.unlike("load", answer.numbers) == []


# Whether a dovetail can be put together (mqucifer/print-shop#184 and mqucifer/print-shop#185)


def test_a_latch_is_bent_as_far_as_it_has_to_go_before_it_s_counted_as_holding(cae: Any) -> None:
    """Before anything is solved, the latched tail is slid out of its socket the way it came
    in, +z: it passes nothing but its latch, and the latch's hook passes the lower bar 0.6 mm
    deep, across y, by the sweep and by rays. `bend` reads the tab pushed that far, away from
    the bar, +y, at its own layers: within PETG's 1%. Lifted, the latch then holds the lower
    bar's weight as before."""
    answer = cae("load", "dovetail/latch.json", "--lift", "upper")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    found = answer.numbers["ways_in"][JOINT]
    assert (found["slides"], found["out"], found["blocked"]) == ("-z", "+z", False)
    latch = found["latch"]
    assert latch["passes"] is True
    assert latch["depth_mm"] == pytest.approx(HOOK_IN, abs=1e-6)
    assert latch["depth_rays_mm"] == pytest.approx(HOOK_IN, abs=2 * latch["rays_mm"] + 0.01)
    assert np.abs(latch["across"]) == pytest.approx([0, 1, 0], abs=1e-6)
    read = latch["bend"]
    assert read["verdict"] == "OK" and read["toward"] == "+y", read
    assert read["push_mm"] == pytest.approx(HOOK_IN, abs=1e-6)
    assert "--root z=30" in read["command"] and "--layers y" in read["command"]
    assert 0 < read["across_layers_pct"] < read["limit_pct"] == 1.0
    assert found["rays"]["blocked"] == []
    weight = LOWER_MM3 * DENSITY / 1e6 * G
    assert weight <= answer.numbers["joints"][JOINT]["latch"]["holds_n"] < 1.1 * weight
    assert "it passes nothing but its latch" in answer.text
    assert "the arm as a beam" in answer.text, "bend's own second method"
    assert reports.unlike("load", answer.numbers) == []


def test_a_solid_stub_named_as_a_latch_can_t_be_fitted_and_holds_nothing(cae: Any) -> None:
    """eval-table-1's latch was a solid stub, rooted where it joined its part and named as
    the latch's arm, and `load --lift` read it as holding though it couldn't get past the
    socket's wall. Here the hook is the whole arm, on a stiff post, rooted on the post's face:
    it catches the window's ledge as the tab does, and on the way in it has to move 0.6 mm
    across y, along itself. `bend` can't push an arm along itself, so the joint can't be put
    together, the latch is credited with nothing, lifted or pulled, and nothing is solved."""
    for case in (("--lift", "upper"), ("--weight", "0.5", *UPWARD)):
        answer = cae("load", "dovetail/stub.json", *case)
        assert answer.verdict == "FAIL" and answer.code == 1, answer.text
        why = answer.record["why"]
        assert "upper+lower's latch, a tab on upper, has to move 0.600 mm +y" in why
        assert "the tab as the file names it can't bend that way" in why
        assert "--toward +y is along the arm, square to its root at y=0.4" in why
        assert "The latch is credited with nothing" in why and "Nothing is solved" in why
        assert "joints" not in answer.numbers and "loads" not in answer.numbers
        found = answer.numbers["ways_in"][JOINT]
        assert found["blocked"] is False and found["latch"]["bend"]["verdict"] == "NO ANSWER"
        assert answer.record["seconds"] < 30


def test_a_dovetail_the_parts_can_t_take_the_way_the_file_says_fails_naming_the_way_in(
    cae: Any,
) -> None:
    """eval-table-1's leg split read `OK` slid in along z, but its socket's mouth was narrower
    than its tail's tip, and the only way in was sideways. Here a straight key 8 mm wide sits
    in a pocket whose mouth at the top of the bar is 6 mm wide: slid out +z, the way the file
    says it went in backwards, it passes through the mouth's two lips, 1 x 6 x 5 mm each. Of
    the six ways it comes out only +y, off the lap face, so it goes in -y; named so, the same
    parts go together."""
    answer = cae("load", "dovetail/mouth.json", "--weight", "5", *ON_TOP)
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    why = answer.record["why"]
    assert "can't be put together the way the file says its tail slides in, -z" in why
    assert "slid out of its socket +z, the tail's part, upper, passes through lower" in why
    assert "30.0 mm³ from (-4, -6, 65) to (-3, 0, 70), and 1 more piece(s)" in why
    assert "come apart past nothing only +y, so the tail goes in -y" in why
    found = answer.numbers["ways_in"][JOINT]
    assert found["blocked"] is True and found["comes_out"] == ["+y"]
    assert found["goes_in"] == ["-y"]
    assert len(found["rays"]["blocked"]) > 0, "the rays find it too"
    assert "joints" not in answer.numbers, "nothing is solved"
    assert reports.unlike("load", answer.numbers) == []
    sideways = cae("load", "dovetail/mouth-sideways.json", "--weight", "5", *ON_TOP)
    assert sideways.numbers["ways_in"][JOINT]["blocked"] is False, sideways.text
    assert "joints" in sideways.numbers, "it's solved"


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (("--lift", "upper", "--weight", "5", "--at", "0,6,110"), "takes no --weight"),
        (("--lift", "top"), "the parts are upper, lower"),
        ((), "or --lift PART"),
        (("--weight", "5"), "or --lift PART"),
    ],
)
def test_a_lift_is_a_load_case_of_its_own(cae: Any, args: tuple[str, ...], says: str) -> None:
    answer = cae("load", "dovetail/latch.json", *args)
    assert answer.verdict == "NO ANSWER" and says in answer.record["why"], answer.text


def test_the_slide_reads_a_tapered_tail_going_in_free_until_it_seats(cae: Any) -> None:
    """The tail drawn 4 mm short of its seat: its neck narrows 2 mm a side over its 40 mm,
    so each flank has 4 x 2 / 40 = 0.2 mm of play there. Slid the way it goes in, it meets
    nothing for those 4 mm, and then its tip's seat and both flanks at once, by both
    methods: the flanks closing after exactly 4 mm is the 0.2 mm of play, read."""
    if host.image_id() is None:
        pytest.fail(f"{host.missing(host.IMAGE)} first")
    pair = ("clearance", "dovetail/socket.step", "dovetail/tail-out.step")
    answer = cae(*pair, "--slide", "-z", "--hold", "4.5")
    assert answer.verdict == "HELD", answer.text
    slide = answer.numbers["slide"]
    assert slide["first_mm"] == pytest.approx(4.0, abs=2 * 0.002 + 1e-4)
    assert slide["first_rays_mm"] == pytest.approx(4.0, abs=1e-3)
    assert slide["meets"] == ["socket.step"] and slide["overlaps"] == []
    # What it meets is the socket, from its end under the tail's tip up its flanks.
    low, high = slide["contacts"][0]["box"]
    assert low[2] == pytest.approx(30, abs=0.03) and high[2] > 60
    # Once home it slides back out the way it came, held by nothing: that's the latch's job.
    out = cae("clearance", "dovetail/socket.step", "dovetail/tail.step", "--slide", "+z")
    assert out.verdict == "NOT HELD" and out.numbers["slide"]["first_mm"] is None


def test_the_snap_on_check_and_bend_read_a_latch_on_a_dovetail(cae: Any) -> None:
    """The latched tail is held on its way out by the hook, 0.2 mm under its ledge. The
    slide's reply gives the `fit-on` command; run as given, the hook passes the lower bar
    0.6 mm deep, across y, on the way in. The harness's arm finder reads the tab from that
    place, the same box the assembly file gives the latch, and `bend` reads the tab pushed
    aside by that depth: the strip formula for its nearer edge, 18 mm from its root."""
    if host.image_id() is None:
        pytest.fail(f"{host.missing(host.IMAGE)} first")
    files = ("dovetail/socket-latch.step", "dovetail/tail-latch.step")
    slide = cae("clearance", *files, "--slide", "+z")
    assert slide.verdict == "HELD", slide.text
    assert slide.numbers["slide"]["first_mm"] == pytest.approx(0.2, abs=2 * 0.002 + 1e-4)
    found = re.search(r"`(fit-on [^`]+)`", slide.text)
    assert found, slide.text
    command = found.group(1).split()
    assert command[:6] == ["fit-on", files[1], "--onto", files[0], "--along", "+z"]
    on = cae(*command)
    assert on.verdict == "BLOCKED" and on.numbers["depth_mm"] == pytest.approx(HOOK_IN, abs=1e-6)
    place = on.numbers["places"][0]
    assert np.abs(place["across"]) == pytest.approx([0, 1, 0], abs=1e-6)
    # The hook where the tail's part sits: the place, back along its way out.
    hook = [[-2.5, -HOOK_IN, 10.0], [2.5, 0.0, 12.0]]
    arm = end_checks._find_arm(Box(cae.work), files[1], hook, "y", cae.work)
    assert arm["found"] is True
    assert arm["arm"] == [[-2.6, -0.7, 9.9], [2.6, 1.7, 30.0]], "the assembly file's `arm`"
    assert (arm["root"], arm["toward"], arm["length_mm"]) == (["z", 30.0], "+y", 20.0)
    latch = json.loads((cae.work / "dovetail" / "latch.json").read_text())["joints"][0]["latch"]
    assert np.array(latch["arm"]) == pytest.approx(np.array(arm["arm"])) and latch["root"] == "z=30"
    bent = cae(*end_checks.bend_command(files[1], arm, HOOK_IN, "z").split())
    assert bent.verdict == "OK", bent.text
    by_strip = 100 * 1.5 * TAB_T * HOOK_IN / 18.0**2
    assert bent.numbers["across_layers_pct"] == pytest.approx(by_strip, rel=0.15)
    assert bent.numbers["worst"] == "near"
