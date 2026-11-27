"""A settings study (mqucifer/print-shop#339): the print loop's model studies a part's settings
for strength, filament and time, with tools, and recommends one against the profile's own.

Every part here is made up: the bar of `test_slice_regions.py`, 80 x 20 x 10 mm with a 6 mm hole,
bolted through it and latched at its end. Every model is scripted, every slice and every `load`
reply a fake that stiffens with walls and infill as the real ones do, and every ledger a
temporary one: nothing reaches LiteLLM, Orca, the bench, the printer or the Mac's ledger. The
real Orca's slices are `test_settings_study_slicer.py`'s.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import trimesh

from print_shop.bench import host
from print_shop.bench.assembly import MATERIALS
from print_shop.bench.printed import Cylinder, Mesh, Printed, Settings, as_json
from print_shop.ingest.orientation import AXIS_ALIGNED, read_part
from print_shop.ledger import Ledger
from print_shop.rounds.lessons import Refused
from print_shop.setup.features import Feature
from print_shop.slicer.gcode import Built
from print_shop.study import lessons
from print_shop.study.candidate import (
    DEFAULTS,
    EDGE_MM,
    PROUD,
    Candidate,
    Shaper,
    faces_mesh,
)
from print_shop.study.loop import StudyLoop, StudyPart, front, read_candidate, run
from print_shop.study.published import published
from print_shop.study.reading import (
    BenchLoad,
    Case,
    Reading,
    Sliced,
    _sequenced,
    cases_from_needs,
    check_names,
    figures,
    margin_said,
    unchecked,
)
from print_shop.study.table import as_plain, table
from tests.fakes import STORE_PROFILES
from tests.test_slice_regions import _assembly, bar

#: The profile's own, as the store's 0.20 mm process gives them (`test_slice_regions.py`).
OWN = Settings(walls=2, line_width=0.45, top=5, bottom=3, infill=15.0, pattern="rectilinear",
               infill_angle=45.0, layer_height=0.2, up="+z")
CASES = (Case("standing", ("--weight", "20", "--at", "75,10,10")),
         Case("lifted", ("--lift", "post"), "moves_mm", at_most=2.0))
PETG = MATERIALS["petg"]

Step = tuple[str, dict[str, Any]]


class Model:
    """The study's model, from a script: each call takes the next turn. Keeps what it was shown."""

    def __init__(self, *turns: Sequence[Step] | Callable[[str], Sequence[Step]]) -> None:
        self.turns = list(turns)
        self.shown: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.shown.append(list(messages))
        assert self.turns, "the script ran out"
        turn = self.turns.pop(0)
        last = next((m["content"] for m in reversed(messages) if m["role"] == "tool"), "")
        steps = turn(last) if callable(turn) else turn
        return {"content": "", "tool_calls": [
            {"id": f"c{len(self.shown)}-{n}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for n, (name, args) in enumerate(steps)]}


def _resolved(candidate: Candidate) -> Settings:
    return candidate.settings.over(OWN)


def fake_slicing(seen: list[Candidate]) -> Callable[[Candidate, Path], Sliced]:
    """Orca, made up: each wall a minute and a gram, each point of infill 6 s and 0.1 g, each
    region a minute and half a gram."""

    def slicing(candidate: Candidate, out: Path) -> Sliced:
        seen.append(candidate)
        s = _resolved(candidate)
        time_s = int(1800 + 60 * (s.walls or 0) + 6 * (s.infill or 0) + 60 * len(candidate.regions))
        grams = round(20 + (s.walls or 0) + 0.1 * (s.infill or 0) + 0.5 * len(candidate.regions),
                      2)
        printed = as_json(Printed(s, candidate.regions))
        return Sliced(time_s, grams, s, printed,
                      time_s - 60 * len(candidate.regions) if candidate.regions else None,
                      band_mm=0.9, walls_mm=0.814)

    return slicing


def fake_loading(seen: list[tuple[str, str]]) -> Callable[..., dict[str, Any]]:
    """`load`, made up: the skin pulled across its layers at 1.6% over its walls and its core,
    against PETG's 1%; its core less as it's denser; the bolt's joint at 0.5 of its limit,
    less with a region round it."""

    def loading(candidate: Candidate, sliced: Sliced, case: Case, out: Path) -> dict[str, Any]:
        seen.append((candidate.name, case.name))
        s = sliced.settings
        lifted = case.name == "lifted"
        across = (0.8 if lifted else 1.6) / ((s.walls or 1) * (1 + (s.infill or 0) / 50))
        core_across = 0.2 * 40 / (s.infill or 1)
        bolt = 0.5 if not candidate.regions else 0.3
        over = across > 1.0 or core_across > 0.481
        record: dict[str, Any] = {
            "verdict": "FAIL" if over else "OK", "why": "made up",
            "numbers": {
                "parts": {"bar": {
                    "printed": True, "across_layers_pct": across, "across_at": [70, 10, 0],
                    "across_limit_pct": 1.0, "stretch_pct": 1.0, "stretch_is": "pull",
                    "stretch_at": [75, 10, 10], "stretch_from_layers_deg": 4.0,
                    "stretch_limit_pct": 5.1,
                    "cores": [{"cell": "the part", "across_layers_pct": core_across,
                               "across_limit_pct": 0.481, "stretch_pct": 0.4,
                               "stretch_limit_pct": 1.51, "stretch_from_lines_deg": 45.0,
                               "unchecked": (s.infill or 0) < 20, "across_at": [40, 10, 5]}],
                }},
                "joints": {"bar+post": {"nearest_share": bolt, "gives_first": "the bolt"}},
                "moves_mm": 3.0 / (s.walls or 1),
            },
            "notes": ["what's solved again round bar+post, the plastic round each bolt, is read "
                      "solid in bar, not as printed: stronger than a printed part's, so read it "
                      "with a margin", "the infill's figures are published tests"],
        }
        return record

    return loading


@pytest.fixture
def part(tmp_path: Path) -> StudyPart:
    stl = bar(tmp_path)
    shaper = Shaper(read_part(stl), tmp_path / "meshes", None, _assembly(tmp_path), "bar")
    return StudyPart(
        name="bar", stl=stl, turn=AXIS_ALIGNED[0], cases=CASES, shaper=shaper,
        slicing=fake_slicing([]), loading=fake_loading([]), defaults=OWN, material=PETG,
        workdir=tmp_path / "study", keys=("long_and_narrow", "joint:bolted"),
        wall_sequence="inner wall/outer wall", filament="PETG",
    )


THREE_WALLS = ("read", {"why": "the skin is pulled across its layers: more walls",
                        "settings": {"walls": 3, "infill": 25}})
FOUR_WALLS = ("read", {"why": "between", "settings": {"walls": 4, "infill": 30,
                                                       "pattern": "grid", "infill_angle": 0}})
BOLT = ("read", {"why": "a ring round the hole", "settings": {"walls": 3, "infill": 25},
                 "regions": [{"joints": ["bar+post-bolt-1"], "margin": 2, "walls": 2}]})


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def test_the_defaults_are_read_first_and_two_of_its_own_before_it_recommends(
    part: StudyPart,
) -> None:
    model = Model(
        [("recommend", {"candidate": DEFAULTS, "reason": "they pass"})],
        [THREE_WALLS], [FOUR_WALLS],
        [("recommend", {"candidate": "c2", "reason": "the least margin it needs, for 1 min",
                        "lessons": [{"text": "a bar pulled across its layers needs 3 walls",
                                     "shown_by": ["defaults", "c1"]}]})],
    )
    result = run(StudyLoop(chat=model), part)
    assert result.ended == "recommended" and result.recommended == "c2"
    assert [r.candidate.name for r in result.readings] == [DEFAULTS, "c1", "c2"]
    # The defaults were read before the model's first turn, and shown to it.
    first = model.shown[0][1]["content"]
    assert "The defaults, read" in first and "defaults: the profile's own" in first
    assert "bar+post-bolt-1" in first, "the joints a region can be put round"
    refused = result.calls[0]
    assert refused.tool == "recommend" and refused.result.startswith("refused: read at least 2")
    # The defaults fail across their layers; the model's own pass.
    base, c1, c2 = result.readings
    assert not base.passes and c1.passes and c2.passes
    assert "pulled across its layers" in base.why_not()
    assert result.lessons == [{"text": "a bar pulled across its layers needs 3 walls",
                               "shown_by": ["defaults", "c1"]}]


def test_a_candidate_that_fails_a_case_or_was_never_read_isnt_recommended(
    part: StudyPart,
) -> None:
    model = Model([THREE_WALLS, FOUR_WALLS],
                  [("recommend", {"candidate": "defaults", "reason": "cheapest"})],
                  [("recommend", {"candidate": "c9", "reason": "?"})],
                  [("recommend", {"candidate": "none", "reason": "?"})],
                  [("recommend", {"candidate": "c1", "reason": "the least time that passes"})])
    result = run(StudyLoop(chat=model), part)
    said = [c.result for c in result.calls if c.tool == "recommend"]
    assert said[0].startswith("refused: defaults doesn't pass every case: standing: its core "
                              "in the part")
    assert said[1].startswith("refused: no candidate 'c9' was read")
    assert said[2] == "refused: c1, c2 pass every case: recommend one of them"
    assert result.recommended == "c1"


def test_each_reading_says_where_the_worst_figure_is_which_way_and_the_trade_off(
    part: StudyPart,
) -> None:
    model = Model([THREE_WALLS], [FOUR_WALLS],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    first, second = (c.result for c in result.calls if c.tool == "read")
    assert first.startswith("c1: 3 walls, 25% infill\n  Orca: 35 min 30 s, 25.5 g "
                            "(+2.0 min, +2.0 g against the defaults)")
    assert "standing: OK" in first
    assert "its skin pulled across its layers 0.356% of 1%, 36% of its limit at (70, 10, 0), " \
           "across the layers [infill model]; a margin of 64%" in first
    assert "the part's walls lie 0.90 mm wide in its layers, where load takes 0.81 mm: as " \
           "load reads them" in first
    assert "The trade-off so far, the candidates no other passing one beats on time, grams and " \
           "margin at once: c1 (35 min 30 s, 25.5 g, a margin of 33%); c2 (37 min 0 s, 27.0 g, " \
           "a margin of 45%)" in second
    assert second.endswith("It's open between them.")


def test_the_same_candidate_is_read_once(part: StudyPart) -> None:
    seen: list[Candidate] = []
    part.slicing = fake_slicing(seen)
    model = Model([THREE_WALLS], [THREE_WALLS], [FOUR_WALLS],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    assert [c.name for c in seen] == [DEFAULTS, "c1", "c2"]
    again = [c.result for c in result.calls if c.tool == "read"][1]
    assert again.startswith("that's c1, read already")


@pytest.mark.parametrize("asked,refusal", [
    ({"settings": {"infill": 80}}, "infill is 10 to 60"),
    ({"settings": {"pattern": "cubic"}}, "pattern is one load reads as printed"),
    ({"settings": {"walls": 0}}, "walls is 1 to 10"),
    ({"settings": {"layer_height": 0.1}}, "settings are walls, top, bottom"),
    ({"turn": [45, 0, 0]}, "isn't one of the poses"),
    ({"wall_sequence": "outside in"}, "wall_sequence is one of"),
    ({"regions": [{"joints": ["nothing"], "walls": 4}]}, "no joint nothing"),
    ({"regions": [{"box": [[0, 0, 0], [5, 5, 5]]}]}, "sets nothing of its own"),
])
def test_what_neither_side_reads_is_refused_with_its_bound(
    part: StudyPart, asked: dict[str, Any], refusal: str,
) -> None:
    model = Model([("read", {"why": "w", **asked})], [THREE_WALLS], [FOUR_WALLS],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    assert result.calls[0].result.startswith("refused:")
    assert refusal in result.calls[0].result
    assert result.recommended == "c1"


def test_arguments_not_of_the_tools_shape_are_refused_never_a_crash(part: StudyPart) -> None:
    """The first real run's model gave its lessons as words alone, and the study crashed."""
    model = Model([THREE_WALLS], [FOUR_WALLS],
                  [("read", {"why": "w", "regions": ["round the hole"]})],
                  [("recommend", {"candidate": "c1", "reason": "r", "lessons": [7]})],
                  [("recommend", {"candidate": "c1", "reason": "r",
                                  "lessons": ["3 walls carry it across its layers"]})])
    result = run(StudyLoop(chat=model), part)
    said = [c.result for c in result.calls]
    assert said[2] == "refused: region 1 is an object"
    assert said[3].startswith("refused: each lesson is")
    assert result.recommended == "c1"
    assert result.lessons == [{"text": "3 walls carry it across its layers", "shown_by": []}]


def test_past_its_limits_it_ends_with_the_table_and_no_recommendation(part: StudyPart) -> None:
    model = Model([THREE_WALLS], [FOUR_WALLS])
    result = run(StudyLoop(chat=model, max_calls=2), part)
    assert result.ended == "past its limit of 2 calls, with no recommendation"
    assert result.recommended is None and len(result.readings) == 3
    text = table(result, "bar", [c.name for c in CASES])
    assert "**No recommendation**: past its limit of 2 calls" in text


def test_it_reads_no_more_than_its_most_candidates(part: StudyPart) -> None:
    model = Model([THREE_WALLS], [FOUR_WALLS], [BOLT],
                  [("recommend", {"candidate": "c2", "reason": "r"})])
    result = run(StudyLoop(chat=model, max_candidates=2), part)
    assert [c.result for c in result.calls if c.tool == "read"][2].startswith(
        "refused: 2 candidates of your own is the most")
    assert result.recommended == "c2"


def test_a_model_that_cant_be_reached_is_an_ending_with_what_was_read(part: StudyPart) -> None:
    def broken(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        raise ConnectionError("no route")

    result = run(StudyLoop(chat=broken), part)
    assert result.ended.startswith("the model couldn't be reached: ConnectionError")
    assert [r.candidate.name for r in result.readings] == [DEFAULTS]


def test_published_says_which_way_each_pattern_runs_and_what_the_tests_give_it() -> None:
    text = published(None, PETG, 0.45, 0.2)
    assert "grid: 50% of its lines at infill_angle + 0°, 50% of its lines at infill_angle + " \
           "90°; the same lines every layer, so they build walls up through the layers" in text
    assert "rectilinear: " in text and "turned 90° each layer" in text
    assert "gyroid: its wave turns with the height and isn't lines" in text
    assert "Checked by a published test from 20% (set by ben-hadj-hassine-2024" in text
    assert "2: 0.81 mm" in text and "4: 1.63 mm" in text
    assert "Use Inner/Outer/Inner for the best external surface finish" in text
    assert published("cubic", PETG, 0.45, 0.2).startswith("load reads no pattern cubic")


# --------------------------------------------------------------------------
# A reading's figures
# --------------------------------------------------------------------------


def test_each_figure_is_a_share_of_its_limit_and_says_what_rests_on_the_infill_model() -> None:
    record = fake_loading([])(
        Candidate("c1", Settings(infill=15.0)), Sliced(1, 1.0, OWN, {}), CASES[1], Path())
    read = figures(record, "bar", CASES[1])
    shares = {f.what: round(f.share, 3) for f in read.figures}
    assert shares == {
        "its skin pulled across its layers": round(0.8 / (2 * 1.3), 3),
        "its skin stretched": round(1.0 / 5.1, 3),
        "its core in the part pulled across its layers": round(0.2 * 40 / 15 / 0.481, 3),
        "its core in the part stretched": round(0.4 / 1.51, 3),
        "joint bar+post, the bolt": 0.5,
        "moves_mm (the need's own)": 0.75,
    }
    core = next(f for f in read.figures if f.what.startswith("its core"))
    assert core.infill and core.unchecked
    assert "[infill model, unchecked: below its tests' densities]" in core.said()
    joint = next(f for f in read.figures if f.what.startswith("joint"))
    assert not joint.infill
    assert read.worst is not None and read.worst.what.startswith("its core")
    assert not read.passes, "the core is past its limit"
    assert any("read solid in bar" in n for n in read.notes)


def test_a_case_with_no_answer_fails_saying_why(part: StudyPart) -> None:
    part.loading = lambda candidate, sliced, case, out: {
        "verdict": "NO ANSWER", "why": "a printed part read in two passes isn't read yet"}
    reading = read_candidate(part, Candidate("c1", Settings(walls=3)))
    assert not reading.passes and not reading.answered
    assert reading.why_not().startswith("standing: NO ANSWER, a printed part read in two passes")


def test_a_candidate_orca_couldnt_slice_is_read_as_that(part: StudyPart) -> None:
    def refuses(candidate: Candidate, out: Path) -> Sliced:
        raise RuntimeError("Orca refused the project (exit 232)")

    part.slicing = refuses
    reading = read_candidate(part, Candidate("c1", Settings(walls=3)))
    assert reading.error == "Orca couldn't slice it: RuntimeError: Orca refused the project " \
                            "(exit 232)"
    assert not reading.cases and not reading.passes


def test_where_orca_cant_slice_the_defaults_nothing_is_studied(part: StudyPart) -> None:
    """The first real run's lesson: the process's own Cool Plate refused PETG, and so every
    candidate after it."""

    def refuses(candidate: Candidate, out: Path) -> Sliced:
        raise RuntimeError("Plate 1: Cool Plate does not support filament 1")

    part.slicing = refuses
    model = Model()
    result = run(StudyLoop(chat=model), part)
    assert result.ended.startswith("the profile's own settings couldn't be sliced, so nothing "
                                   "was studied: Orca couldn't slice it: RuntimeError: Plate 1")
    assert not model.shown and result.model_calls == 0


def test_the_front_is_what_no_other_passing_candidate_beats_on_all_three() -> None:
    def reading(name: str, time_s: int, grams: float, share: float) -> Reading:
        found = Reading(Candidate(name, Settings()), Sliced(time_s, grams, OWN, {}))
        record = {"verdict": "OK", "numbers": {"parts": {"bar": {
            "across_layers_pct": share, "across_limit_pct": 1.0}}}}
        found.cases = [figures(record, "bar", CASES[0])]
        return found

    rows = [reading("a", 100, 10, 0.5), reading("b", 120, 12, 0.3), reading("c", 130, 13, 0.4),
            reading("d", 90, 9, 1.2)]
    assert [r.candidate.name for r in front(rows)] == ["a", "b"]


def test_the_walls_band_is_read_against_loads_thickness() -> None:
    layer = Built(n=1, z=0.2, height=0.2, area_mm2=100.0, core_mm2=82.0, solid=0.0,
                  infill=15.0, walls=2, walls_meet=0.0, angles={}, volume_mm3=1.0,
                  outline_mm=20.0)
    assert layer.band_mm == pytest.approx(0.9)
    assert replace(layer, outline_mm=0.0).band_mm is None
    agrees = Sliced(1, 1.0, OWN, {}, band_mm=0.90, walls_mm=0.814)
    assert agrees.band_said() == ["the part's walls lie 0.90 mm wide in its layers, where load "
                                  "takes 0.81 mm: as load reads them"]
    apart = Sliced(1, 1.0, OWN, {}, band_mm=1.4, walls_mm=0.814,
                   region_bands={"ring": (1.2, 2.44)})
    said = apart.band_said()
    assert said[0].endswith("+0.51 mm past a bead's width over load's, so the settings alone "
                            "don't give the skin load reads there")
    assert said[1].startswith("region ring's walls lie 1.20 mm wide")


# --------------------------------------------------------------------------
# Regions round the joints
# --------------------------------------------------------------------------


def test_regions_round_the_joints_are_cylinders_round_holes_and_meshes_of_their_faces(
    part: StudyPart,
) -> None:
    candidate = part.shaper.candidate("c1", {"why": "w", "regions": [
        {"joints": "all", "margin": 2, "faces": True, "walls": 2, "infill": 40,
         "why": "the joints carry it"},
        {"name": "root", "layers": [0, 2], "top": 6},
    ]})
    bolt, latch, band = candidate.regions
    assert isinstance(bolt.shape, Cylinder) and bolt.name == "bar+post-bolt-1"
    assert bolt.shape.radius == pytest.approx(5.0, abs=0.05)
    assert isinstance(latch.shape, Mesh) and latch.name == "bar+lid-latch"
    assert "a mesh of the part's own faces there, 2 mm deep" in latch.why
    made = trimesh.load(candidate.meshes["bar+lid-latch"], force="mesh")
    assert isinstance(made, trimesh.Trimesh) and made.is_watertight and made.volume > 0
    # It follows the part's faces in the latch's box: the end of the bar, 58 to 72 mm along it,
    # its top, bottom and sides, PROUD out of them and 2 mm in.
    low, high = made.bounds
    assert low[0] >= 58 - EDGE_MM - PROUD and high[0] <= 72 + EDGE_MM + PROUD
    assert low[2] == pytest.approx(-PROUD, abs=0.05) and high[2] == pytest.approx(10 + PROUD,
                                                                                  abs=0.05)
    assert band.kind == "layers" and band.settings == Settings(top=6)
    assert candidate.said().startswith("the profile's own; region bar+post-bolt-1 (round the")


def test_a_face_mesh_follows_the_faces_where_a_box_holds_the_corners() -> None:
    block = trimesh.creation.box((20, 20, 20))
    block.apply_translation((10, 10, 10))
    made = faces_mesh(block.subdivide().subdivide(), ((-1, -1, 15), (21, 21, 21)), 2.0)
    assert made is not None and made.is_watertight
    # The block's top and the top 5 mm of its sides, 2 mm deep: far less than the box's 2,420 mm3
    # of the part.
    assert made.volume < 0.8 * 22 * 22 * 6
    assert faces_mesh(block, ((100, 100, 100), (101, 101, 101)), 2.0) is None


def test_two_regions_of_one_name_are_refused(part: StudyPart) -> None:
    with pytest.raises(ValueError, match="each have a name of their own"):
        part.shaper.candidate("c1", {"regions": [
            {"name": "a", "box": [[0, 0, 0], [5, 5, 5]], "walls": 3},
            {"name": "a", "box": [[10, 0, 0], [15, 5, 5]], "walls": 3}]})


# --------------------------------------------------------------------------
# Orca's and load's sides, without either
# --------------------------------------------------------------------------


def test_the_wall_order_is_written_into_a_whole_copy_of_the_process(tmp_path: Path) -> None:
    assert _sequenced(STORE_PROFILES, None, tmp_path) is STORE_PROFILES
    found = _sequenced(STORE_PROFILES, "inner-outer-inner wall", tmp_path)
    data = json.loads(Path(found.process).read_text())
    assert data["wall_sequence"] == "inner-outer-inner wall"
    assert {k: v for k, v in data.items() if k != "wall_sequence"} == {
        k: v for k, v in json.loads(Path(STORE_PROFILES.process).read_text()).items()
        if k != "wall_sequence"}


def test_load_reads_a_copy_of_the_assembly_with_the_part_as_sliced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "run"
    folder.mkdir()
    _assembly(folder)
    mesh = tmp_path / "ring-faces-1.stl"
    trimesh.creation.box((1, 1, 1)).export(mesh)
    asked: list[list[str]] = []

    def bench(args: list[str], work: Path, **_: Any) -> Any:
        asked.append(args)
        saved = work / "bench-out" / "load-1"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.with_suffix(".json").write_text(json.dumps({"verdict": "OK", "numbers": {}}))
        import subprocess
        return subprocess.CompletedProcess(args, 0, "verdict: OK\nsaved: bench-out/load-1.txt "
                                                    "and .json\n", "")

    monkeypatch.setattr(host, "run", bench)
    sliced = Sliced(1, 1.0, replace(OWN, up="-x"), {"walls": 3, "up": "-x"})
    candidate = Candidate("c1", Settings(walls=3), meshes={"ring": mesh})
    record = BenchLoad(folder / "assembly.json", "bar")(candidate, sliced, CASES[0],
                                                         tmp_path / "out")
    assert record == {"verdict": "OK", "numbers": {}}
    assert asked == [["load", "study-assembly.json", "--weight", "20", "--at", "75,10,10"]]
    work = tmp_path / "out" / "load"
    copy = json.loads((work / "study-assembly.json").read_text())
    bar_part = next(p for p in copy["parts"] if p["name"] == "bar")
    assert bar_part["print"] == {"walls": 3, "up": "-x"}
    assert bar_part["layers"] == "x", "the layers' axis the pose stands up"
    assert next(p for p in copy["parts"] if p["name"] == "post").get("print") is None
    assert {p.name for p in work.iterdir()} >= {"bar.step", "post.step", "lid.step",
                                                "ring-faces-1.stl"}


def test_a_design_runs_load_needs_are_its_cases(tmp_path: Path) -> None:
    needs = tmp_path / "needs.json"
    needs.write_text(json.dumps([
        {"id": "holds", "command": "load assembly.json --weight 30 --at 0,0,700 --out x",
         "number": "moves_mm", "at_most": 3},
        {"id": "lift", "command": "load assembly.json --lift top"},
        {"id": "fits", "command": "fit-bed assembly.json"},
    ]))
    found = cases_from_needs(needs, [])
    assert found == [Case("holds", ("--weight", "30", "--at", "0,0,700"), "moves_mm", 3),
                     Case("lift", ("--lift", "top"))]
    assert cases_from_needs(needs, ["lift"]) == [Case("lift", ("--lift", "top"))]


# --------------------------------------------------------------------------
# The table, and the ledger
# --------------------------------------------------------------------------


def _studied(part: StudyPart) -> Any:
    model = Model([THREE_WALLS], [FOUR_WALLS], [BOLT],
                  [("recommend", {"candidate": "c1", "reason": "it passes for the least time",
                                  "lessons": [{"text": "3 walls carry a bar across its layers",
                                               "shown_by": ["defaults", "c1"]}]})])
    return run(StudyLoop(chat=model, model="scripted"), part)


def test_the_table_compares_each_with_the_defaults_and_says_what_it_rests_on(
    part: StudyPart,
) -> None:
    text = table(_studied(part), "bar", [c.name for c in CASES])
    assert "| Candidate | Settings | Time | Grams | standing | lifted | Passes |" in text
    assert "| c1 | 3 walls, 25% infill | 35.5 min (+2.0) | 25.5 g (+2.0) |" in text
    assert "**Recommended: c1**, 3 walls, 25% infill." in text
    assert "Against the profile's defaults: +2.0 min, +2.0 g, its worst figure at a margin of " \
           "33%, where the defaults' is at 11% past its limit, so they fail." in text
    assert "## Those that fail, and why\n\n- defaults: standing: its core in the part " \
           "pulled across its layers" in text
    assert "mqucifer/print-shop#340" in text and "Round the joints, read solid" in text
    assert "**Below the densities the tests reach:** defaults" in text
    assert "## The walls as built, a second reading" in text
    assert "Nothing was printed or armed." in text
    plain = as_plain(_studied(part), "bar")
    assert plain["recommended"] == "c1" and plain["readings"][0]["candidate"]["name"] == DEFAULTS


FEATURES = (Feature(id="length", value=120, unit="mm", says=""),
            Feature(id="footprint_aspect", value=6, unit="x", says=""))


def test_the_study_and_its_lessons_go_to_the_ledger_keyed_by_its_traits_and_joints(
    tmp_path: Path, part: StudyPart,
) -> None:
    keyed = lessons.keys(FEATURES, _assembly(tmp_path), "bar")
    assert keyed == ("long_and_narrow", "joint:bolted", "joint:dovetail", "joint:latch")
    result = _studied(part)
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        study, made = lessons.record(
            ledger, part="bar", stl_name="bar.stl", stl_sha256="0" * 64, features=FEATURES,
            part_keys=keyed, filament="PETG", cases=[{"name": "standing"}], result=result,
            started_at="2026-11-26T00:00:00+00:00")
        found = [lessons.one(ledger, n) for n in made]
        assert [f["drawn_from"] for f in found] == ["finding", "recommendation"]
        assert found[0]["text"] == "3 walls carry a bar across its layers"
        assert [e["candidate"] for e in found[0]["evidence"]] == ["defaults", "c1"]
        assert found[1]["text"].startswith("Studied: 3 walls, 25% infill over the profile's "
                                           "defaults, +2.0 min, +2.0 g, its worst figure at a "
                                           "margin of 33%, the defaults' at 11% past its limit")
        assert all(f["status"] == "candidate" and f["keys"] == list(keyed) for f in found)
        # Nothing reaches a later study until a reviewer lets it through.
        assert lessons.for_part(ledger, keyed) == []
        with pytest.raises(Refused):
            lessons.accept(ledger, made[0], "someone")
        lessons.accept(ledger, made[0], "reviewer: the operator")
        lessons.edit(ledger, made[1], "reviewer: the operator", keyed=["long_and_narrow"],
                     why="the joints don't decide it")
        assert [r["id"] for r in lessons.for_part(ledger, keyed)] == made
        # A later part reached only by the edited one: it has the trait, not the joints.
        assert [r["id"] for r in lessons.for_part(ledger, ("long_and_narrow",))] == [made[1]]
        assert lessons.for_part(ledger, ("flat_and_wide",)) == []
        with pytest.raises(Refused, match="not a trait or a joint"):
            lessons.edit(ledger, made[1], "reviewer: x", keyed=["wobbly"], why="w")
        assert lessons.earlier(ledger, keyed)[0].startswith(
            f"bar (bar.stl), study {study}: recommended 3 walls, 25% infill")
        listing = lessons.listing(ledger, study)
        assert f"- Lesson {made[0]} [accepted], finding of {study}" in listing
        stored = ledger.query("SELECT recommended, ended FROM settings_studies")
        assert stored == [{"recommended": "c1", "ended": "recommended"}]


def test_past_studies_gives_the_kept_lessons_and_what_earlier_studies_recommended(
    part: StudyPart,
) -> None:
    part.lessons = ("lesson 4: 3 walls carry a bar across its layers",)
    part.earlier = ("rail (rail.stl), study s: recommended 4 walls",)
    model = Model([("past_studies", {})], [THREE_WALLS], [FOUR_WALLS],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    said = result.calls[0].result
    assert "Kept lessons for parts with these keys (long_and_narrow, joint:bolted):" in said
    assert "- lesson 4: 3 walls carry a bar across its layers" in said
    assert "- rail (rail.stl), study s: recommended 4 walls" in said
    assert "Kept lessons from studies of parts like this one" in model.shown[0][1]["content"]


# --------------------------------------------------------------------------
# mqucifer/print-shop#345's cold review
# --------------------------------------------------------------------------


UNBUILT = ("ring's walls: asked 6, read 2, without the regions 2: not as asked (the walls fill "
           "it: only 2 fit in its width, and nothing is left inside them)")


def test_a_region_orca_didnt_build_as_asked_fails_as_not_as_sliced_and_isnt_read(
    part: StudyPart,
) -> None:
    """`load` reads a region as asked: a 6-wall ring where Orca laid 2 would read stronger than
    printed, so it's failed, never read, and never recommended."""
    slicing = fake_slicing([])
    loaded: list[tuple[str, str]] = []

    def thinner(candidate: Candidate, out: Path) -> Sliced:
        sliced = slicing(candidate, out)
        if candidate.regions:
            sliced.unbuilt = [UNBUILT]
        return sliced

    part.slicing, part.loading = thinner, fake_loading(loaded)
    model = Model([THREE_WALLS], [FOUR_WALLS], [BOLT],
                  [("recommend", {"candidate": "c3", "reason": "the ring holds the bolt"})],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    ring = result.readings[3]
    assert not ring.passes and not ring.cases
    assert ring.error.startswith("not as sliced: Orca didn't build what its regions ask, and "
                                 "load would read them as asked, stronger than printed: ring's")
    assert ("c3", "standing") not in loaded, "it isn't read by load"
    assert [c.result for c in result.calls if c.tool == "recommend"][0].startswith(
        "refused: c3 doesn't pass every case: not as sliced")
    assert result.recommended == "c1"
    text = table(result, "bar", [c.name for c in CASES])
    assert "| not as sliced | not as sliced | no |" in text
    assert "- c3: not as sliced: Orca didn't build what its regions ask" in text
    read = [c.result for c in result.calls if c.tool == "read"][2]
    assert "Orca: 36 min 30 s" in read and "not as sliced" in read


def _need(number: str, numbers: dict[str, Any], at_least: float | None = None,
          at_most: float | None = None) -> Any:
    case = Case("holds", (), number, at_most=at_most, at_least=at_least)
    return figures({"verdict": "OK", "numbers": numbers}, "bar", case)


@pytest.mark.parametrize("number,numbers,at_least,at_most,passes", [
    # A list's index is followed, as a design run's need follows it.
    ("holds.0.n", {"holds": [{"n": 10}]}, 200, None, False),
    ("holds.0.n", {"holds": [{"n": 250}]}, 200, None, True),
    # No figure at all fails, as the need does.
    ("moves_mm", {}, None, 3, False),
    # A limit of 0, and a figure of 0 or less.
    ("gap.mm", {"gap": {"mm": -0.3}}, 0.2, None, False),
    ("gap.mm", {"gap": {"mm": -0.3}}, 0, None, False),
    ("gap.mm", {"gap": {"mm": 0.0}}, 0, None, True),
    ("moves_mm", {"moves_mm": 0.0}, None, 0, True),
    ("moves_mm", {"moves_mm": 0.1}, None, 0, False),
    # At its limit, to arithmetic's noise, passes.
    ("moves_mm", {"moves_mm": 3.0 + 1e-12}, None, 3, True),
    ("moves_mm", {"moves_mm": 3.5}, None, 3, False),
])
def test_a_cases_need_passes_exactly_where_the_design_runs_need_would(
    number: str, numbers: dict[str, Any], at_least: float | None, at_most: float | None,
    passes: bool,
) -> None:
    from print_shop.rounds.needs import Need, judge

    read = _need(number, numbers, at_least, at_most)
    need = Need(id="holds", what="it holds", source="derived", by="bench", command="load a.json",
                number=number, at_least=at_least, at_most=at_most)
    assert judge(need, 0, "verdict: OK", {"verdict": "OK", "numbers": numbers}).passed is passes
    assert read.passes is passes
    (figure,) = read.figures
    assert figure.what == f"{number} (the need's own)"
    if not passes:
        assert read.worst is figure and figure.breaks


def test_a_missing_need_figure_says_so_in_the_needs_own_words() -> None:
    (figure,) = _need("moves_mm", {"loads": []}, at_most=3).figures
    assert figure.said() == ("moves_mm (the need's own): this answer has no number `moves_mm`: "
                             "it has loads, so it fails")
    assert margin_said(figure.margin) == "past its limit"


def test_the_skin_resting_on_an_unchecked_core_is_unchecked_too() -> None:
    record = fake_loading([])(
        Candidate("c1", Settings(infill=15.0)), Sliced(1, 1.0, OWN, {}), CASES[0], Path())
    read = figures(record, "bar", CASES[0])
    skin = [f for f in read.figures if f.what.startswith("its skin")]
    assert skin and all(f.unchecked for f in skin)
    record = fake_loading([])(
        Candidate("c1", Settings(infill=25.0)), Sliced(1, 1.0, replace(OWN, infill=25.0), {}),
        CASES[0], Path())
    assert not any(f.unchecked for f in figures(record, "bar", CASES[0]).figures)


def test_unchecked_is_by_pattern_and_density_as_load_reads_it() -> None:
    printed = {**as_json(Printed(replace(OWN, pattern="gyroid", infill=30.0))), "regions": [
        {"name": "ring", "cylinder": {}, "walls": 5},
        {"name": "dense", "box": [[0, 0, 0], [1, 1, 1]], "infill": 45},
        {"name": "grid", "box": [[0, 0, 0], [1, 1, 1]], "pattern": "grid"}]}
    assert unchecked(printed) == [
        "the part: gyroid at 30%, which the published tests of gyroid reach only from 40%",
    ]
    assert unchecked(as_json(Printed(replace(OWN, pattern="grid", infill=15.0)))) == [
        "the part: grid at 15%, which the published tests of grid reach only from 20%"]
    assert unchecked(as_json(Printed(replace(OWN, pattern="grid", infill=20.0)))) == []


def test_the_recommendation_says_what_no_test_checks_and_the_star_marks_only_that(
    part: StudyPart,
) -> None:
    model = Model([THREE_WALLS], [("read", {"why": "low", "settings": {"walls": 4,
                                                                        "infill": 18}})],
                  [("recommend", {"candidate": "c2", "reason": "the least plastic"})])
    result = run(StudyLoop(chat=model), part)
    text = table(result, "bar", [c.name for c in CASES])
    para = text.split("**Recommended: c2**", 1)[1].split("## ", 1)[0]
    assert ("some are checked by no test at all, below the densities they reach: the part: "
            "rectilinear at 18%, which the published tests of rectilinear reach only from 20%"
            in para)
    rows = {line.split(" | ")[0]: line for line in text.splitlines() if line.startswith("| c")}
    assert "*" not in rows["| c1"], "25% rectilinear is within its tests"
    assert "*" in rows["| c2"]
    other = Model([THREE_WALLS], [FOUR_WALLS],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    text = table(run(StudyLoop(chat=other), part), "bar", [c.name for c in CASES])
    assert "every core's density is within what the tests of its pattern reach" in text


@pytest.mark.parametrize("names,refusal", [
    (["holds", "lift", "holds"], "holds is given twice"),
    (["need-1", "need-1"], "need-1 is given twice"),
    (["a/b"], "letters, digits"),
    ([".."], "letters, digits"),
])
def test_two_cases_of_one_name_or_a_name_no_folder_takes_are_refused(
    names: list[str], refusal: str,
) -> None:
    with pytest.raises(ValueError, match=refusal):
        check_names([Case(n, ()) for n in names])
    check_names([Case("holds", ()), Case("lift", ())])


def test_a_need_named_like_the_one_auto_named_before_it_is_refused(tmp_path: Path) -> None:
    needs = tmp_path / "needs.json"
    needs.write_text(json.dumps([{"command": "load a.json --lift top"},
                                 {"id": "need-1", "command": "load a.json --weight 3 --at 0,0,1"}]))
    with pytest.raises(ValueError, match="need-1 is given twice"):
        check_names(cases_from_needs(needs, []))
