"""The bench's second image on planted parts: `fit-on` and `bend`, with FreeCAD, sealed
(mqucifer/print-shop#142). Needs `scripts/bench.py build cae`, and the first image for the slide.

A design run approved a snap-on lid that could never be fitted: nothing read its way on, or what
that did to its clips. Everything here is made from scratch by `cae_cases/make_fixtures.py`, a
box with one snap clip and a lid whose skirt passes over its hook, and right answers come from
arithmetic.
"""

from __future__ import annotations

import json
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
