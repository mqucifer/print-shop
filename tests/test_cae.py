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

import pytest

from print_shop.bench import host
from print_shop.rounds import needs, reports
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
    assert 0 < joint["nearest_share"] < 1 and "the plastic round the hole" in joint["gives_first"]
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
