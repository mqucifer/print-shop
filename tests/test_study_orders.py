"""Specification for settings studies queued by themselves from approved design runs, within
limits the Sponsor gives up front (mqucifer/print-shop#359).

Every run here is made up (`tests/refine_fixtures.py`'s approved run, its files plain bars and
plates from trimesh), with a temporary design ledger beside it. Every model is scripted, and
every slice and `load` too (`tests/test_settings_study.py`'s fakes); no Orca, Docker, printer or
real ledger is touched.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from print_shop.bench.printed import Settings
from print_shop.ingest.orientation import read_part
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import Refused
from print_shop.slicer.regions import Pose
from print_shop.study import orders
from print_shop.study.candidate import Candidate, Shaper
from print_shop.study.limits import Limits, OutOfLimits
from print_shop.study.loop import StudyLoop, StudyPart, run
from print_shop.study.reading import OrcaSlicing, defaults
from print_shop.study.table import table
from tests.fakes import STORE_PROFILES
from tests.refine_fixtures import approve
from tests.test_settings_study import (  # noqa: F401 - `part` is a fixture
    CASES,
    FOUR_WALLS,
    THREE_WALLS,
    WAIT,
    Model,
    fake_loading,
    fake_slicing,
    part,
)
from tests.test_slice_regions import bar, needs_orca

# --------------------------------------------------------------------------
# The Sponsor's limits
# --------------------------------------------------------------------------


def test_limits_are_read_and_what_isnt_one_is_refused() -> None:
    found = Limits.of({"most_minutes": 120, "most_grams": 80, "patterns": ["gyroid", "grid"],
                       "wall_sequences": ["inner-outer-inner wall"], "most_walls": 4,
                       "least_infill": 15, "most_infill": 40, "filament": "Elegoo PETG"})
    assert found.said() == ("at most 120 min; at most 80 g; infill gyroid or grid; infill from "
                            "15 to 40%; at most 4 walls; walls laid inner-outer-inner wall")
    assert Limits.of(found.plain()) == found
    for raw, refusal in (({"fastest": True}, "not fastest"),
                         ({"most_grams": -1}, "a number above 0"),
                         ({"patterns": ["honeycomb"]}, "not honeycomb"),
                         ({"most_walls": 2.5}, "a whole number"),
                         ({"least_infill": 50, "most_infill": 20}, "above most_infill")):
        with pytest.raises(ValueError, match=refusal):
            Limits.of(raw)


@pytest.mark.parametrize("candidate,refusal", [
    (Candidate("c1", Settings(pattern="rectilinear")), "rectilinear infill is past"),
    (Candidate("c1", Settings(infill=60.0)), "60% infill is past the Sponsor's most, 40%"),
    (Candidate("c1", Settings(walls=6)), "6 walls are past the Sponsor's most, 4"),
    (Candidate("c1", Settings(), wall_sequence="outer wall/inner wall"), "walls laid outer"),
])
def test_a_candidate_past_the_limits_is_refused_before_its_sliced(
    candidate: Candidate, refusal: str,
) -> None:
    limits = Limits.of({"patterns": ["gyroid", "grid"], "most_infill": 40, "most_walls": 4,
                        "wall_sequences": ["inner-outer-inner wall"]})
    with pytest.raises(OutOfLimits, match=refusal):
        limits.check(candidate)
    limits.check(Candidate("c2", Settings(pattern="grid", infill=30.0, walls=3)))


def test_a_slice_past_the_time_or_grams_is_said() -> None:
    limits = Limits.of({"most_minutes": 30, "most_grams": 20})
    assert limits.sliced_past(1800, 20.0) == ""
    assert limits.sliced_past(2130, 25.5) == ("35.5 min, past the Sponsor's most of 30 min; "
                                              "25.5 g, past the Sponsor's most of 20 g")


# --------------------------------------------------------------------------
# A study within them, and one of time, grams and finish only
# --------------------------------------------------------------------------


def test_the_study_keeps_to_the_limits_and_the_table_says_what_they_ruled_out(
    part: StudyPart,  # noqa: F811
) -> None:
    """The fakes' defaults take 33.5 min and 23.5 g, 3 walls 35.5 min, 4 walls 37 min: at most
    36 min rules out 4 walls, sliced, and gyroid is refused before it's sliced."""
    seen: list[Candidate] = []
    part.slicing = fake_slicing(seen)
    part.limits = Limits.of({"most_minutes": 36, "patterns": ["rectilinear", "grid"]})
    gyroid = ("read", {"why": "w", "settings": {"pattern": "gyroid", "infill": 30}})
    model = Model([gyroid], [THREE_WALLS, WAIT], [FOUR_WALLS, WAIT],
                  [("recommend", {"candidate": "c2", "reason": "r"})],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    said = [c.result for c in result.calls]
    assert said[0].startswith("refused: the part's gyroid infill is past the Sponsor's limit")
    assert "The Sponsor's limits, binding: at most 36 min; infill rectilinear or grid" in \
        model.shown[0][1]["content"]
    assert [c.name for c in seen] == ["defaults", "c1", "c2"], "gyroid was never sliced"
    c2 = result.readings[2]
    assert c2.ruled_out == "37.0 min, past the Sponsor's most of 36 min" and not c2.cases
    assert next(c.result for c in result.calls if c.tool == "recommend").startswith(
        "refused: c2 doesn't pass every case: ruled out by the Sponsor's limits")
    assert result.recommended == "c1"
    text = table(result, "bar", [c.name for c in CASES])
    assert "The Sponsor's limits, binding on it: at most 36 min; infill rectilinear or grid." \
        in text
    ruled = text.split("## Ruled out by the Sponsor's limits", 1)[1]
    assert "- c2: 37.0 min, past the Sponsor's most of 36 min" in ruled
    assert "- asked, never sliced: 30% infill, gyroid pattern: the part's gyroid infill is " \
           "past the Sponsor's limit" in ruled
    row = next(line for line in text.splitlines() if line.startswith("| c2 |"))
    assert "| ruled out | ruled out |" in row
    failing = text.split("## Those that fail, and why", 1)[1].split("## ", 1)[0]
    assert "- c2" not in failing


def test_the_defaults_past_a_limit_are_read_but_never_recommended(
    part: StudyPart,  # noqa: F811
) -> None:
    part.limits = Limits.of({"patterns": ["grid"]})
    model = Model([("read", {"why": "w", "settings": {"pattern": "grid", "walls": 3,
                                                      "infill": 25}}), WAIT],
                  [("read", {"why": "w", "settings": {"pattern": "grid", "walls": 4,
                                                      "infill": 30}}), WAIT],
                  [("recommend", {"candidate": "defaults", "reason": "r"})],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    base = result.readings[0]
    assert base.cases, "read under every case, as the baseline"
    assert base.ruled_out == ("the profile's own: the part's rectilinear infill is past the "
                              "Sponsor's limit: grid only")
    assert result.recommended == "c1"


def test_a_study_with_no_load_cases_is_of_time_grams_and_finish(
    part: StudyPart,  # noqa: F811
) -> None:
    """A flagged design with no load: each candidate passes on its slice, the trade-off is time
    against grams, and nothing is read by load."""
    loaded: list[tuple[str, str]] = []
    part.cases = ()
    part.loading = fake_loading(loaded)
    finish = ("read", {"why": "a cleaner face", "wall_sequence": "inner-outer-inner wall",
                       "settings": {"walls": 3}})
    model = Model([finish, WAIT], [FOUR_WALLS, WAIT],
                  [("recommend", {"candidate": "c1", "reason": "the finish, for 2 min"})])
    result = run(StudyLoop(chat=model), part)
    assert "It has no load cases: it's studied for time, grams and finish only" in \
        model.shown[0][1]["content"]
    assert loaded == [] and result.recommended == "c1"
    assert all(r.passes for r in result.readings)
    text = table(result, "bar", [])
    assert "| Candidate | Settings | Time | Grams | Passes | Orca project |" in text
    assert "with no load cases, for time, grams and finish only" in text
    assert "Each case's cell" not in text
    waited = [c.result for c in result.calls if c.tool == "wait"][-1]
    assert "The trade-off so far" in waited


# --------------------------------------------------------------------------
# Queued on approval, or flagged
# --------------------------------------------------------------------------


def _bar(path: Path, length: float, depth: float = 20.0) -> None:
    body = trimesh.creation.box((length, depth, 10))
    body.apply_translation((length / 2, depth / 2, 5))
    body.export(path)


def _furniture_run(root: Path, name: str = "table-1", load: bool = True) -> tuple[Path, Any]:
    """An approved run delivering two parts of a made-up table, an upper and a lower leg, each a
    bar in its own file, with an assembly in its work folder, its load needs read on it, and a
    bed need laying the lower one on its +z face."""
    folder = root / name
    judged = folder / "rounds" / "001" / "judged"
    judged.mkdir(parents=True)
    for part_name, length in (("leg_upper.stl", 120.0), ("leg_lower.stl", 140.0)):
        _bar(judged / part_name, length)
    (judged / "make.py").write_text("# made up\n")
    looks = {f: {"colour": "black", "label": "leg"} for f in ("leg_upper.stl", "leg_lower.stl")}
    folder, ledger = approve(root, name, folder, judged, ["leg_upper.stl", "leg_lower.stl"],
                             looks, "make.py", {"form": {"kind": "furniture"}})
    work = folder / "work"
    work.mkdir(exist_ok=True)
    for f in ("leg_upper.stl", "leg_lower.stl"):
        shutil.copyfile(judged / f, work / f)
    (work / "post.stl").write_text("solid post\nendsolid post\n")
    (work / "assembly.json").write_text(json.dumps({"parts": [
        {"name": "upper", "file": "leg_upper.stl", "at": [0, 0, 140], "layers": "z",
         "material": "petg"},
        {"name": "lower", "file": "leg_lower.stl", "at": [0, 0, 0], "layers": "z",
         "material": "petg"},
        {"name": "post", "file": "post.stl", "at": [0, 0, -50], "layers": "z"},
    ]}))
    needs: list[dict[str, Any]] = [
        {"id": "lower-on-bed", "what": "w", "source": "request", "by": "bed",
         "file": "leg_lower.stl", "side": "+z"},
    ]
    if load:
        needs += [{"id": "load-10kg", "what": "w", "source": "request", "by": "bench",
                   "command": "load assembly.json --weight 10 --at 0,0,260",
                   "number": "moves_mm", "at_most": 3},
                  {"id": "lift", "what": "w", "source": "request", "by": "bench",
                   "command": "load assembly.json --lift upper --out x"}]
    (folder / "needs.json").write_text(json.dumps(needs))
    return folder, ledger


def test_an_approved_run_with_a_load_need_has_each_part_it_delivers_queued(
    tmp_path: Path,
) -> None:
    folder, ledger = _furniture_run(tmp_path)
    made, said = orders.on_approval(folder, ledger, "on approval by sponsor",
                                    Limits.of({"most_grams": 90}))
    assert len(made) == 2 and said.startswith("a settings study of each part queued, 2")
    queued = orders.orders(ledger)
    assert [(o.part, o.kind, o.side, o.status) for o in queued] == [
        ("upper", "load", "", "queued"), ("lower", "load", "+z", "queued")]
    upper = queued[0]
    assert upper.file == folder / "approvals" / "1" / "review" / "A-with-pause" / "leg_upper.stl"
    assert upper.assembly == folder / "work" / "assembly.json"
    assert upper.needs == folder / "needs.json"
    assert upper.limits == Limits.of({"most_grams": 90})
    again, _ = orders.on_approval(folder, ledger, "on approval by sponsor")
    assert again == made and len(orders.orders(ledger)) == 2, "never queued twice"


def test_a_run_with_no_load_need_queues_nothing_by_itself(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path, load=False)
    made, said = orders.on_approval(folder, ledger, "on approval by sponsor")
    assert made == [] and "has no load need" in said and "settings_study.py queue" in said


def test_a_run_that_isnt_approved_queues_nothing(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path)
    state = json.loads((folder / "state.json").read_text())
    state["ending"] = {"name": "closed"}
    (folder / "state.json").write_text(json.dumps(state))
    with pytest.raises(Refused, match="isn't approved"):
        orders.on_approval(folder, ledger, "on approval by sponsor")


def test_a_flagged_design_is_studied_with_no_cases_or_those_named(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path, load=False)
    made = orders.flag(folder, ledger, "Sponsor: M", files=["leg_lower"])
    (lower,) = orders.orders(ledger)
    # With no load need, no assembly is named: the part is its file's.
    assert made == [lower.id] and (lower.kind, lower.part, lower.cases) == (
        "flagged", "leg_lower", ())
    named = orders.flag(folder, ledger, "Sponsor: M", files=["leg_upper.stl"],
                        cases=["hang=--weight 5 --at 0,0,200"], assembly=Path("assembly.json"))
    upper = orders.orders(ledger)[-1]
    assert named == [upper.id] and upper.cases == ("hang=--weight 5 --at 0,0,200",)
    assert upper.part == "upper", "named in the assembly the Sponsor gave"
    assert upper.assembly == folder / "work" / "assembly.json"
    with pytest.raises(ValueError, match="not among table-1's approved files: shelf"):
        orders.flag(folder, ledger, "Sponsor: M", files=["shelf"])


def test_named_cases_need_an_assembly(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path, load=False)
    (folder / "work" / "assembly.json").unlink()
    with pytest.raises(ValueError, match="cases are read on an assembly"):
        orders.flag(folder, ledger, "Sponsor: M", cases=["hang=--weight 5"])


def test_orders_are_claimed_one_at_a_time_oldest_first(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    first, second = orders.claim(ledger), orders.claim(ledger)
    assert first is not None and second is not None and orders.claim(ledger) is None
    assert (first.part, first.status, second.part) == ("upper", "running", "lower")
    orders.finish(ledger, first, "done", "its table is x/study.md", tmp_path / "x")
    listed = orders.listing(ledger)
    assert "order 1: table-1 upper (load), done, in " in listed
    assert "order 2: table-1 lower (load), running" in listed


@pytest.mark.parametrize("side", ["-z", "+z", "-x", "+x", "-y", "+y"])
def test_each_side_on_the_bed_is_a_pose_that_turns_it_straight_down(side: str) -> None:
    rx, ry = orders.laid_for(side)
    normal = np.zeros(3)
    normal["xyz".index(side[1])] = 1 if side[0] == "+" else -1
    turned = Pose.of(trimesh.creation.box((1, 2, 3)), (rx, ry, 0.0)).matrix[:3, :3] @ normal
    assert turned == pytest.approx([0, 0, -1])
    assert orders.laid_for("") == (0.0, 0.0)
    with pytest.raises(ValueError, match="-x to \\+z"):
        orders.laid_for("up")


def test_a_queued_study_is_made_of_the_runs_own_files(tmp_path: Path) -> None:
    """The approved file in the assembly's place, its own mesh, the run's needs as its cases,
    the side its bed need lays it on, turned by fit-bed's own turn, and the Sponsor's limits."""
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor", Limits.of({"most_grams": 90}))
    order = orders.orders(ledger)[1]
    # The work folder's lower leg has changed since it was approved: the approved one is read.
    _bar(folder / "work" / "leg_lower.stl", 150.0)
    words = orders.prepare(order, folder, {"STUDY_NOZZLE": "0.4",
                                           "STUDY_FILAMENTS": "petg=Elegoo PETG; pla=Elegoo PLA"})
    out = folder / "studies" / f"{order.id}-lower"
    inputs = out / "inputs"
    assert words[:2] == ["run", str(inputs / "mesh" / "leg_lower.stl")]
    pairs = dict(zip(words[2::2], words[3::2], strict=False))
    assert pairs["--part"] == "lower" and pairs["--turn"] == "180,0,fit"
    assert pairs["--filament"] == "Elegoo PETG" and pairs["--nozzle"] == "0.4"
    assert pairs["--assembly"] == str(inputs / "assembly.json")
    assert pairs["--needs"] == str(folder / "needs.json") and pairs["--out"] == str(out)
    assert json.loads((inputs / "limits.json").read_text()) == {"most_grams": 90.0}
    assert (inputs / "leg_lower.stl").read_bytes() == order.file.read_bytes()
    assert (inputs / "post.stl").is_file(), "the assembly's other parts are beside it"


def test_a_queued_study_with_no_filament_or_a_changed_file_is_refused(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    order = orders.orders(ledger)[0]
    with pytest.raises(ValueError, match="no nozzle or filament for upper"):
        orders.prepare(order, folder, {})
    _bar(order.file, 99.0)
    with pytest.raises(ValueError, match="has changed since it was approved"):
        orders.prepare(order, folder, {"STUDY_NOZZLE": "0.4", "STUDY_FILAMENTS": "Elegoo PETG"})


def test_a_flagged_study_with_no_cases_says_so(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path, load=False)
    orders.flag(folder, ledger, "Sponsor: M", files=["leg_upper"],
                limits=Limits.of({"nozzle": 0.4, "filament": "Elegoo PLA"}))
    words = orders.prepare(orders.orders(ledger)[0], folder, {})
    assert words[-1] == "--no-cases" and "--needs" not in words and "--assembly" not in words
    assert words[words.index("--filament") + 1] == "Elegoo PLA"


# --------------------------------------------------------------------------
# The worker, and the approval it never holds up
# --------------------------------------------------------------------------


def _script(name: str) -> Any:
    import importlib.util  # noqa: PLC0415

    path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_worker_runs_each_order_one_at_a_time_and_says_what_came_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    script = _script("settings_study.py")
    monkeypatch.setattr(script, "load_env", lambda _path=None: {
        "STUDY_NOZZLE": "0.4", "STUDY_FILAMENTS": "petg=Elegoo PETG"})
    ran: list[list[str]] = []

    def study(words: list[str]) -> int:
        ran.append(words)
        return 0 if len(ran) == 1 else 2

    assert script.work(tmp_path, DesignLedger(tmp_path / "ledger.sqlite"),
                       ledger=tmp_path / "shop.sqlite", run_study=study) == 0
    assert [w[w.index("--part") + 1] for w in ran] == ["upper", "lower"]
    assert all(w[-2:] == ["--ledger", str(tmp_path / "shop.sqlite")] for w in ran)
    done, failed = orders.orders(ledger)
    assert (done.status, failed.status) == ("done", "failed")
    assert done.said == f"its table is {folder / 'studies' / '1-upper' / 'study.md'}"
    assert failed.said.startswith("the study exited 2")


def test_only_one_worker_runs_a_roots_studies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl  # noqa: PLC0415

    script = _script("settings_study.py")
    monkeypatch.setattr(script, "load_env", lambda _path=None: {})
    held = (tmp_path / "studies.lock").open("w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert script.work(tmp_path, DesignLedger(tmp_path / "ledger.sqlite"),
                           run_study=lambda w: 0) == 3
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()


def test_queueing_on_approval_never_holds_it_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What goes wrong with a study is said, and the run stays approved: here its assembly is
    gone, and the limits given aren't limits. A worker is started for what's queued."""
    import argparse  # noqa: PLC0415

    started: list[Path] = []

    def start(root: Path) -> str:
        started.append(root)
        return f"a worker started for them, in {root.name}"

    monkeypatch.setattr(orders, "start_worker", start)
    folder, _ = _furniture_run(tmp_path)
    rounds = _script("rounds.py")
    said = rounds._studies(folder, tmp_path, argparse.Namespace(by="sponsor",
                                                                 study_limits=None))
    assert said.startswith("a settings study of each part queued, 2")
    assert said.endswith("; a worker started for them, in " + tmp_path.name)
    assert started == [tmp_path]

    def broken(root: Path) -> str:
        raise OSError("no room")

    monkeypatch.setattr(orders, "start_worker", broken)
    other, _ = _furniture_run(tmp_path, "table-3")
    said = rounds._studies(other, tmp_path, argparse.Namespace(by="sponsor", study_limits=None))
    assert "no worker could start (OSError: no room)" in said and "work --root" in said
    (tmp_path / "bad.json").write_text('{"fastest": true}')
    said = rounds._studies(folder, tmp_path, argparse.Namespace(
        by="sponsor", study_limits=tmp_path / "bad.json"))
    assert said.startswith("no settings study queued: ValueError: the study's limits are")
    (folder / "work" / "assembly.json").unlink()
    other, _ = _furniture_run(tmp_path, "table-2")
    (other / "work" / "assembly.json").unlink()
    said = rounds._studies(other, tmp_path, argparse.Namespace(by="sponsor", study_limits=None))
    assert said.startswith("no settings study queued: ValueError: table-2's load needs read no "
                           "assembly file there is")


def test_a_study_needs_cases_or_says_it_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    script = _script("settings_study.py")
    stl = tmp_path / "bar.stl"
    _bar(stl, 80.0)
    assert script.main(["run", str(stl), "--nozzle", "0.4", "--filament", "Elegoo PLA"]) == 2
    assert "--no-cases for a study of time, grams and finish only" in capsys.readouterr().err
    assert script.main(["run", str(stl), "--nozzle", "0.4", "--filament", "Elegoo PLA",
                        "--case", "hang=--weight 5"]) == 2
    assert "load cases are read on an assembly: give --assembly" in capsys.readouterr().err
    (tmp_path / "limits.json").write_text('{"patterns": ["honeycomb"]}')
    assert script.main(["run", str(stl), "--no-cases", "--limits",
                        str(tmp_path / "limits.json")]) == 2
    assert "--limits: the limit patterns takes" in capsys.readouterr().err


def test_published_says_what_it_can_with_no_material_load_knows() -> None:
    from print_shop.study.published import published  # noqa: PLC0415

    said = published("gyroid", None, 0.45, 0.2)
    assert "load knows no figures for this part's plastic" in said
    assert "MPa along its angle" not in said


@pytest.mark.slicer
@needs_orca
def test_a_flagged_study_with_real_slices_keeps_to_the_grams_allowed(tmp_path: Path) -> None:
    """The bar sliced by Orca, with no load cases: the heavier candidate is ruled out by the
    Sponsor's most grams, never read further, and the lighter one recommended."""
    stl = bar(tmp_path)
    slicing = OrcaSlicing(stl, STORE_PROFILES, tmp_path / "meshes")
    base = slicing(Candidate("probe", Settings()), tmp_path / "probe")
    assert base.grams is not None
    shaper = Shaper(read_part(stl), tmp_path / "meshes")
    flagged = StudyPart(
        name="bar", stl=stl, turn=(0.0, 0.0, 0.0), cases=(), shaper=shaper, slicing=slicing,
        loading=fake_loading([]), defaults=defaults(STORE_PROFILES, stl, (0.0, 0.0, 0.0)),
        material=None, workdir=tmp_path / "study",
        limits=Limits.of({"most_grams": round(base.grams + 1.0, 2)}))
    heavy = ("read", {"why": "w", "settings": {"walls": 6, "infill": 60}})
    light = ("read", {"why": "w", "settings": {"walls": 3}})
    model = Model([heavy, WAIT], [light, WAIT],
                  [("recommend", {"candidate": "c2", "reason": "within the grams"})])
    result = run(StudyLoop(chat=model), flagged)
    c1, c2 = result.readings[1:]
    assert c1.ruled_out.endswith(f"past the Sponsor's most of {base.grams + 1.0:g} g"), c1
    assert c2.passes and result.recommended == "c2"
    text = table(result, "bar", [])
    assert "## Ruled out by the Sponsor's limits" in text and "- c1:" in text


def test_a_worker_starts_detached_where_none_runs(tmp_path: Path) -> None:
    import fcntl  # noqa: PLC0415
    import sys  # noqa: PLC0415

    spawned: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(argv: list[str], **kwargs: Any) -> None:
        spawned.append((argv, kwargs))

    said = orders.start_worker(tmp_path, spawn)
    ((argv, kwargs),) = spawned
    assert argv == [sys.executable, str(orders.SCRIPT), "work", "--root", str(tmp_path)]
    assert kwargs["start_new_session"] is True and kwargs["stdin"] is not None
    assert said.endswith(f"it writes to {tmp_path / 'studies.log'}")
    held = (tmp_path / "studies.lock").open("a")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert orders.start_worker(tmp_path, spawn).startswith("a worker already runs")
        assert len(spawned) == 1
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
    assert orders.SCRIPT.is_file()
