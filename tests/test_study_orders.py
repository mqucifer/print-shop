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
from dataclasses import replace
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


def test_a_setting_a_candidate_leaves_unset_is_held_to_the_limits_as_the_profile_gives_it(
    part: StudyPart,  # noqa: F811
) -> None:
    """mqucifer/print-shop#372's review: with walls at most 1 and gyroid only, a candidate asking
    only infill would be sliced with the profile's 2 walls and rectilinear, and recommended."""
    limits = Limits.of({"most_walls": 1, "patterns": ["gyroid"]})
    with pytest.raises(OutOfLimits, match="rectilinear infill, the profile's, is past"):
        limits.check(Candidate("c1", Settings(infill=30.0)), part.defaults)
    with pytest.raises(OutOfLimits, match="2 walls, the profile's, are past the Sponsor's most"):
        limits.check(Candidate("c1", Settings(pattern="gyroid", infill=30.0)), part.defaults)
    limits.check(Candidate("c1", Settings(pattern="gyroid", walls=1)), part.defaults)
    with pytest.raises(OutOfLimits, match="walls laid inner wall/outer wall, the profile's,"):
        Limits.of({"wall_sequences": ["outer wall/inner wall"]}).check(
            Candidate("c1", Settings()), part.defaults, "inner wall/outer wall")
    part.limits = Limits.of({"patterns": ["gyroid"]})
    model = Model([("read", {"why": "w", "settings": {"infill": 30}})])
    result = run(StudyLoop(chat=model, max_calls=1), part)
    assert result.calls[0].result.startswith("refused: the part's rectilinear infill, the "
                                             "profile's, is past")


def _bar(path: Path, length: float, depth: float = 20.0) -> None:
    body = trimesh.creation.box((length, depth, 10))
    body.apply_translation((length / 2, depth / 2, 5))
    body.export(path)


def _furniture_run(root: Path, name: str = "table-1", load: bool = True) -> tuple[Path, Any]:
    """An approved run delivering two parts of a made-up table, an upper and a lower leg, each a
    bar in its own file, with an assembly in its work folder naming a third part, a band and a
    `with` beside them, its load needs read on it, and a bed need laying the lower one on its +z
    face."""
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
    for f in ("post.stl", "skin.stl", "bolt.stl"):
        (work / f).write_text(f"solid {f}\nendsolid {f}\n")
    (work / "assembly.json").write_text(json.dumps({"parts": [
        {"name": "upper", "file": "leg_upper.stl", "at": [0, 0, 140], "layers": "z",
         "material": "petg", "with": ["bolt.stl"]},
        {"name": "lower", "file": "leg_lower.stl", "at": [0, 0, 0], "layers": "z",
         "material": "petg", "band": "skin.stl"},
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


ENV = {"STUDY_NOZZLE": "0.4", "STUDY_FILAMENTS": "petg=Elegoo PETG; pla=Elegoo PLA"}


def test_an_approved_run_with_a_load_need_has_each_part_it_delivers_queued(
    tmp_path: Path,
) -> None:
    folder, ledger = _furniture_run(tmp_path)
    made, said = orders.on_approval(folder, ledger, "on approval by sponsor",
                                    Limits.of({"most_grams": 90}))
    assert len(made) == 2 and said == "order 1 queued for upper; order 2 queued for lower"
    queued = orders.orders(ledger)
    assert [(o.part, o.kind, o.side, o.status, o.material) for o in queued] == [
        ("upper", "load", "", "queued", "petg"), ("lower", "load", "+z", "queued", "petg")]
    upper = queued[0]
    assert upper.file == folder / "approvals" / "1" / "review" / "A-with-pause" / "leg_upper.stl"
    assert upper.assembly == Path("assembly.json") and upper.needs == folder / "needs.json"
    assert upper.limits == Limits.of({"most_grams": 90})
    again, said = orders.on_approval(folder, ledger, "on approval by sponsor")
    assert again == [] and len(orders.orders(ledger)) == 2, "never queued twice"
    assert said == "order 1 is queued already, for upper; order 2 is queued already, for lower"


def test_what_a_study_reads_is_copied_as_its_queued_with_every_file_its_assembly_names(
    tmp_path: Path,
) -> None:
    """mqucifer/print-shop#372's review: hours later the work folder may have changed, and a
    part's `with` and band files were never copied."""
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    lower = orders.orders(ledger)[1]
    assert lower.inputs == folder / "studies" / "2-lower" / "inputs"
    assert set(lower.manifest or {}) == {"leg_lower.stl", "assembly.json", "leg_upper.stl",
                                         "bolt.stl", "skin.stl", "post.stl"}
    assert (lower.inputs / "leg_lower.stl").read_bytes() == lower.file.read_bytes()
    _bar(folder / "work" / "leg_lower.stl", 150.0)
    (folder / "work" / "skin.stl").write_text("changed")
    assert (lower.inputs / "skin.stl").read_text().startswith("solid skin.stl")
    (folder / "work" / "bolt.stl").unlink()
    other, other_ledger = _furniture_run(tmp_path, "table-2")
    (other / "work" / "bolt.stl").unlink()
    with pytest.raises(ValueError, match="bolt.stl, which assembly.json names, isn't in"):
        orders.on_approval(other, other_ledger, "on approval by sponsor")
    assert [o for o in orders.orders(other_ledger) if o.run == "table-2"] == [], \
        "refused, never queued without it"


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
    ((made, said),) = orders.flag(folder, ledger, "Sponsor: M", files=["leg_lower"])
    (lower,) = orders.orders(ledger)
    # With no load need, no assembly is named: the part is its file's.
    assert made == lower.id and said == f"order {made} queued for leg_lower"
    assert (lower.kind, lower.part, lower.cases, lower.material) == (
        "flagged", "leg_lower", (), None)
    ((named, _),) = orders.flag(folder, ledger, "Sponsor: M", files=["leg_upper.stl"],
                                cases=["hang=--weight 5 --at 0,0,200"],
                                assembly=Path("assembly.json"))
    upper = orders.orders(ledger)[-1]
    assert named == upper.id and upper.cases == ("hang=--weight 5 --at 0,0,200",)
    assert (upper.part, upper.material, upper.assembly) == ("upper", "petg",
                                                            Path("assembly.json"))
    with pytest.raises(ValueError, match="not among table-1's approved files: shelf"):
        orders.flag(folder, ledger, "Sponsor: M", files=["shelf"])


def test_named_cases_need_an_assembly(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path, load=False)
    (folder / "work" / "assembly.json").unlink()
    with pytest.raises(ValueError, match="cases are read on an assembly"):
        orders.flag(folder, ledger, "Sponsor: M", cases=["hang=--weight 5"])


def test_a_failed_or_done_order_is_queued_again_as_its_next_attempt(tmp_path: Path) -> None:
    """mqucifer/print-shop#372's review: queued again, a failed order stayed failed, and new
    cases and limits were dropped, while it said it was queued."""
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    upper, lower = orders.orders(ledger)
    orders.finish(ledger, orders.claim(ledger) or upper, "failed", "Orca refused it")
    orders.finish(ledger, orders.claim(ledger) or lower, "done", "its table is x")
    made, said = orders.on_approval(folder, ledger, "on approval by sponsor")
    assert said == "order 3 queued for upper, its attempt 2; order 2 studied lower already"
    assert made == [3] and orders.orders(ledger)[-1].attempt == 2
    ((again, words),) = orders.flag(folder, ledger, "Sponsor: M", files=["leg_lower"],
                                    cases=["hang=--weight 5"], limits=Limits.of({"most_grams": 50}))
    assert words == f"order {again} queued for lower" and again == 4
    newest = orders.orders(ledger)[-1]
    assert newest.cases == ("hang=--weight 5",) and newest.limits == Limits.of({"most_grams": 50})
    ((none, words),) = orders.flag(folder, ledger, "Sponsor: M", files=["leg_lower"])
    assert none is None and words == "order 4 is queued already, for lower"


def test_a_file_shared_or_renamed_at_review_is_one_part_matched_by_its_own_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mqucifer/print-shop#372's review: a file shared by two groups is renamed at review, and
    was skipped as no part of the assembly, or queued and said twice."""
    from print_shop.refine import approved as approved_mod  # noqa: PLC0415

    folder, ledger = _furniture_run(tmp_path)
    real = approved_mod.read(folder, ledger)
    group = real.groups[0]
    renamed = [replace(f, name=f"01-{f.name}") for f in group.files]
    for f, was in zip(renamed, group.files, strict=True):
        shutil.copyfile(was.path, was.path.with_name(f.name))
    renamed = [replace(f, path=f.path.with_name(f.name)) for f in renamed]
    shared = replace(real, groups=[replace(group, files=renamed),
                                   replace(group, folder="B", files=renamed[:1])])
    monkeypatch.setattr(approved_mod, "read", lambda _folder, _ledger: shared)
    made, said = orders.on_approval(folder, ledger, "on approval by sponsor")
    assert said == "order 1 queued for upper; order 2 queued for lower", "once each"
    lower = orders.orders(ledger)[1]
    assert lower.side == "+z", "its bed need names its own name"
    assert lower.inputs is not None
    assert (lower.inputs / "leg_lower.stl").read_bytes() == lower.file.read_bytes()


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


def test_a_queued_study_is_made_of_what_was_copied_as_it_was_queued(tmp_path: Path) -> None:
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor", Limits.of({"most_grams": 90}))
    order = orders.orders(ledger)[1]
    words = orders.prepare(order, folder, ENV)
    inputs = folder / "studies" / f"{order.id}-lower" / "inputs"
    assert words[:2] == ["run", str(inputs / "mesh" / "leg_lower.stl")]
    pairs = dict(zip(words[2::2], words[3::2], strict=False))
    assert pairs["--part"] == "lower" and pairs["--turn"] == "180,0,fit"
    assert pairs["--filament"] == "Elegoo PETG" and pairs["--nozzle"] == "0.4"
    assert pairs["--assembly"] == str(inputs / "assembly.json")
    assert pairs["--needs"] == str(folder / "needs.json")
    assert pairs["--out"] == str(inputs.parent)
    assert json.loads((inputs / "limits.json").read_text()) == {"most_grams": 90.0}
    (inputs / "skin.stl").write_text("tampered")
    with pytest.raises(ValueError, match="skin.stl has changed since it was queued"):
        orders.prepare(order, folder, ENV)


def test_no_nozzle_or_filament_is_refused_before_anything_is_meshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    meshed: list[Path] = []

    def mesh(step: Path, work: Path) -> Path:
        meshed.append(step)
        return step

    monkeypatch.setattr(orders, "mesh", mesh)
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    with pytest.raises(ValueError, match="no nozzle or filament for upper"):
        orders.prepare(orders.orders(ledger)[0], folder, {})
    assert meshed == []


def test_a_flagged_part_of_no_material_takes_the_settings_default_filament(
    tmp_path: Path,
) -> None:
    """mqucifer/print-shop#372's review: with STUDY_FILAMENTS by material, a flagged part with
    no assembly has none, and every such study failed."""
    folder, ledger = _furniture_run(tmp_path, load=False)
    orders.flag(folder, ledger, "Sponsor: M", files=["leg_upper"])
    order = orders.orders(ledger)[0]
    with pytest.raises(ValueError, match="for a part of no material \\(`\\*=NAME`"):
        orders.prepare(order, folder, ENV)
    words = orders.prepare(order, folder, {**ENV, "STUDY_FILAMENTS": ENV["STUDY_FILAMENTS"]
                                           + "; *=Elegoo PLA"})
    assert words[words.index("--filament") + 1] == "Elegoo PLA"
    assert words[-1] == "--no-cases" and "--needs" not in words and "--assembly" not in words


def test_a_mesh_past_its_time_has_its_container_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess  # noqa: PLC0415

    ran: list[list[str]] = []

    def docker(argv: list[str], **kwargs: Any) -> Any:
        ran.append(argv)
        if argv[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", docker)
    from print_shop.bench import host  # noqa: PLC0415

    monkeypatch.setattr(host, "image_id", lambda image=None: "sha256:x")
    (tmp_path / "part.step").write_text("ISO-10303-21;")
    with pytest.raises(RuntimeError, match="meshing part.step took past 600 s"):
        orders.mesh(tmp_path / "part.step", tmp_path / "work")
    started = next(argv for argv in ran if argv[:2] == ["docker", "run"])
    assert ran[-1] == ["docker", "kill", started[started.index("--name") + 1]]


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


def _worker(monkeypatch: pytest.MonkeyPatch) -> Any:
    script = _script("settings_study.py")
    monkeypatch.setattr(script, "load_env", lambda _path=None: dict(ENV))
    return script


def test_the_worker_runs_each_order_one_at_a_time_and_says_what_came_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    script = _worker(monkeypatch)
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


def test_an_order_whose_worker_died_is_failed_by_the_next_and_an_interrupt_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mqucifer/print-shop#372's review: an order whose worker died stayed running for ever,
    and an interrupt left its order running."""
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    orders.claim(ledger)  # a worker that died with it
    script = _worker(monkeypatch)

    def stopped(words: list[str]) -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        script.work(tmp_path, DesignLedger(tmp_path / "ledger.sqlite"), run_study=stopped)
    died, interrupted = orders.orders(ledger)
    assert (died.status, died.said) == ("failed", "its worker ended before it did: "
                                                  "`settings_study.py retry 1 --root ROOT --by "
                                                  "WHO` queues it again")
    assert (interrupted.status, interrupted.said) == (
        "failed", "stopped by KeyboardInterrupt as it was studied")
    assert not orders.worker_running(tmp_path), "its lock let go"


def test_an_order_queued_as_the_worker_ends_isnt_stranded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mqucifer/print-shop#372's review: queued as the worker found nothing, an approval saw its
    lock held and started no worker, and the worker ended."""
    folder, ledger = _furniture_run(tmp_path)
    script = _worker(monkeypatch)
    real = orders.claim
    queued_late = {"done": False}

    def claim(design: DesignLedger) -> Any:
        found = real(design)
        if found is None and not queued_late["done"]:
            queued_late["done"] = True
            orders.on_approval(folder, ledger, "on approval by sponsor")
        return found

    monkeypatch.setattr(script.study_orders, "claim", claim)
    ran: list[list[str]] = []

    def study(words: list[str]) -> int:
        ran.append(words)
        return 0

    script.work(tmp_path, DesignLedger(tmp_path / "ledger.sqlite"), run_study=study)
    assert len(ran) == 2 and [o.status for o in orders.orders(ledger)] == ["done", "done"]


def test_only_one_worker_runs_a_roots_studies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl  # noqa: PLC0415

    script = _worker(monkeypatch)
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
    """What goes wrong with a study is said, and the run stays approved. A worker is started for
    what's queued, and a study queued with no limits says so."""
    import argparse  # noqa: PLC0415

    started: list[Path] = []

    def start(root: Path, env: dict[str, str] | None = None) -> str:
        started.append(root)
        return f"a worker started for them, in {root.name}"

    monkeypatch.setattr(orders, "start_worker", start)
    import print_shop.env  # noqa: PLC0415

    monkeypatch.setattr(print_shop.env, "load_env", lambda _path=None: dict(ENV))
    folder, _ = _furniture_run(tmp_path)
    rounds = _script("rounds.py")
    said = rounds._studies(folder, tmp_path, argparse.Namespace(by="sponsor",
                                                                 study_limits=None))
    assert said == ("order 1 queued for upper; order 2 queued for lower; with no limits of the "
                    "Sponsor's (--study-limits FILE gives them); a worker started for them, in "
                    + tmp_path.name)
    assert started == [tmp_path]
    (tmp_path / "limits.json").write_text('{"most_grams": 80}')
    other, _ = _furniture_run(tmp_path, "table-3")
    said = rounds._studies(other, tmp_path, argparse.Namespace(
        by="sponsor", study_limits=tmp_path / "limits.json"))
    assert "with no limits" not in said

    def broken(root: Path, env: dict[str, str] | None = None) -> str:
        raise OSError("no room")

    monkeypatch.setattr(orders, "start_worker", broken)
    another, _ = _furniture_run(tmp_path, "table-4")
    said = rounds._studies(another, tmp_path, argparse.Namespace(by="sponsor",
                                                                  study_limits=None))
    assert "no worker could start (OSError: no room)" in said and "work --root" in said
    (tmp_path / "bad.json").write_text('{"fastest": true}')
    said = rounds._studies(folder, tmp_path, argparse.Namespace(
        by="sponsor", study_limits=tmp_path / "bad.json"))
    assert said.startswith("no settings study queued: ValueError: the study's limits are")
    gone, _ = _furniture_run(tmp_path, "table-2")
    (gone / "work" / "assembly.json").unlink()
    said = rounds._studies(gone, tmp_path, argparse.Namespace(by="sponsor", study_limits=None))
    assert said.startswith("no settings study queued: ValueError: the assembly ")


def test_a_relative_root_is_stored_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder, ledger = _furniture_run(tmp_path)
    monkeypatch.chdir(tmp_path)
    orders.on_approval(Path(folder.name), ledger, "on approval by sponsor")
    order = orders.orders(ledger)[0]
    assert order.file.is_absolute() and order.inputs is not None and order.inputs.is_absolute()
    assert order.needs is not None and order.needs.is_absolute()


def test_a_worker_starts_detached_from_a_stated_checkout_never_a_worktree(
    tmp_path: Path,
) -> None:
    import fcntl  # noqa: PLC0415

    spawned: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(argv: list[str], **kwargs: Any) -> None:
        spawned.append((argv, kwargs))

    home = tmp_path / "checkout"
    (home / "scripts").mkdir(parents=True)
    (home / "scripts" / "settings_study.py").write_text("")
    (home / "src" / "print_shop" / "study").mkdir(parents=True)
    (home / "src" / "print_shop" / "study" / "orders.py").write_text(
        f"ORDERS_FORMAT = {orders.ORDERS_FORMAT}\n")
    (home / ".venv" / "bin").mkdir(parents=True)
    (home / ".venv" / "bin" / "python").write_text("")
    (home / ".git").mkdir()
    root = tmp_path / "root"
    root.mkdir()
    said = orders.start_worker(root, {"STUDY_CHECKOUT": str(home)}, spawn)
    ((argv, kwargs),) = spawned
    assert argv == [str(home / ".venv" / "bin" / "python"),
                    str(home / "scripts" / "settings_study.py"), "work", "--root", str(root)]
    assert kwargs["start_new_session"] is True and kwargs["cwd"] == str(home)
    assert said.endswith(f"it writes to {root / 'studies.log'}")
    held = (root / "studies.lock").open("a")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert orders.start_worker(root, {"STUDY_CHECKOUT": str(home)}, spawn).startswith(
            "a worker already runs")
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
    worktree = tmp_path / "worktree"
    shutil.copytree(home, worktree)
    shutil.rmtree(worktree / ".git")
    (worktree / ".git").write_text("gitdir: elsewhere\n")
    said = orders.start_worker(root, {"STUDY_CHECKOUT": str(worktree)}, spawn)
    assert said.startswith(f"no worker started: {worktree} is a worktree") and len(spawned) == 1
    assert orders.checkout({}).joinpath("scripts", "settings_study.py").is_file()
    assert not (orders.checkout({}) / ".git").is_file(), "this checkout's main one"


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


# --------------------------------------------------------------------------
# mqucifer/print-shop#372's re-review
# --------------------------------------------------------------------------


def test_an_order_cant_be_claimed_until_its_inputs_are_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder, ledger = _furniture_run(tmp_path)
    real = orders._snapshot
    seen: list[Any] = []

    def snapshot(inputs: Path, f: Any, assembly: Any) -> dict[str, str]:
        seen.append(orders.claim(ledger))  # a worker rechecking as it's copied
        return real(inputs, f, assembly)

    monkeypatch.setattr(orders, "_snapshot", snapshot)
    orders.on_approval(folder, ledger, "on approval by sponsor")
    assert seen[0] is None, "copying, not claimable"
    claimed = orders.claim(ledger)
    assert claimed is not None and claimed.inputs is not None and claimed.manifest


def _checkout(where: Path, orders_format: int | None) -> Path:
    (where / "scripts").mkdir(parents=True)
    (where / "scripts" / "settings_study.py").write_text("")
    (where / ".venv" / "bin").mkdir(parents=True)
    (where / ".venv" / "bin" / "python").write_text("")
    (where / ".git").mkdir()
    if orders_format is not None:
        (where / "src" / "print_shop" / "study").mkdir(parents=True)
        (where / "src" / "print_shop" / "study" / "orders.py").write_text(
            f"ORDERS_FORMAT = {orders_format}\n")
    return where


class _Child:
    def __init__(self, code: int | None) -> None:
        self.code = code

    def poll(self) -> int | None:
        return self.code


def test_no_worker_is_said_to_start_from_a_checkout_older_than_the_orders_or_that_ends_at_once(
    tmp_path: Path,
) -> None:
    """mqucifer/print-shop#372's re-review: main's settings_study.py had no `work`, so the child
    exited 2 into the log while the approval said a worker started."""
    root = tmp_path / "root"
    root.mkdir()
    spawned: list[list[str]] = []

    def spawn(code: int | None) -> Any:
        def made(argv: list[str], **_: Any) -> _Child:
            spawned.append(argv)
            return _Child(code)
        return made

    old = _checkout(tmp_path / "old", None)
    said = orders.start_worker(root, {"STUDY_CHECKOUT": str(old)}, spawn(None), watch=0.2)
    assert said.startswith(f"no worker started: the checkout at {old} is older than these "
                           "orders (it reads none") and "pull it" in said and spawned == []
    behind = _checkout(tmp_path / "behind", orders.ORDERS_FORMAT - 1)
    said = orders.start_worker(root, {"STUDY_CHECKOUT": str(behind)}, spawn(None), watch=0.2)
    assert f"reads format {orders.ORDERS_FORMAT - 1}" in said and spawned == []
    home = _checkout(tmp_path / "home", orders.ORDERS_FORMAT)
    said = orders.start_worker(root, {"STUDY_CHECKOUT": str(home)}, spawn(2), watch=0.2)
    assert said == (f"no worker started: the one started from {home} ended at once, exit 2: "
                    f"see {root / 'studies.log'}")
    said = orders.start_worker(root, {"STUDY_CHECKOUT": str(home)}, spawn(3), watch=0.2)
    assert said.startswith("a worker already runs")
    said = orders.start_worker(root, {"STUDY_CHECKOUT": str(home)}, spawn(None), watch=0.2)
    assert said.startswith(f"a worker started for them from {home}")
    assert orders.format_of(Path(__file__).resolve().parents[1]) == orders.ORDERS_FORMAT


def test_a_flagged_order_with_no_cases_takes_its_material_from_the_assembly(
    tmp_path: Path,
) -> None:
    """mqucifer/print-shop#372's re-review: a PETG part flagged with no cases was studied in
    `*`'s filament."""
    folder, ledger = _furniture_run(tmp_path)
    orders.flag(folder, ledger, "Sponsor: M", files=["leg_lower"])
    order = orders.orders(ledger)[-1]
    assert (order.kind, order.material, order.cases) == ("flagged", "petg", ())
    words = orders.prepare(order, folder, {**ENV, "STUDY_FILAMENTS": "petg=Elegoo PETG; "
                                                                      "*=Elegoo PLA"})
    assert words[words.index("--filament") + 1] == "Elegoo PETG"


def test_an_order_is_retried_as_it_was(tmp_path: Path) -> None:
    """mqucifer/print-shop#372's re-review: a failed load order could be queued again only as
    flagged, without its load cases."""
    folder, ledger = _furniture_run(tmp_path)
    orders.on_approval(folder, ledger, "on approval by sponsor", Limits.of({"most_grams": 90}))
    first = orders.claim(ledger)
    assert first is not None
    orders.finish(ledger, first, "failed", "Orca refused it")
    made, said = orders.retry(ledger, first.id, "operator: a test")
    assert made == 3 and said == "order 3 queued for upper as order 1 was, its attempt 2"
    again = orders.orders(ledger)[-1]
    assert (again.kind, again.needs, again.cases, again.limits, again.attempt) == (
        "load", first.needs, first.cases, first.limits, 2)
    assert again.manifest == first.manifest and again.inputs != first.inputs
    assert again.inputs is not None and (again.inputs / "skin.stl").is_file()
    none, said = orders.retry(ledger, again.id, "operator: a test")
    assert none is None and said == "order 3 is queued already, for upper"
    assert first.inputs is not None
    (first.inputs / "bolt.stl").write_text("changed")
    orders.finish(ledger, again, "failed", "again")
    with pytest.raises(ValueError, match="bolt.stl has changed since order 1 was queued"):
        orders.retry(ledger, first.id, "operator: a test")
    with pytest.raises(ValueError, match="no order 99"):
        orders.retry(ledger, 99, "operator: a test")
    assert "settings_study.py retry" in orders.listing(ledger)


def test_two_queuings_at_once_take_attempts_of_their_own(tmp_path: Path) -> None:
    import threading  # noqa: PLC0415

    folder, ledger = _furniture_run(tmp_path, load=False)
    found: list[Any] = []
    errors: list[BaseException] = []

    def flag() -> None:
        try:
            found.extend(orders.flag(folder, ledger, "Sponsor: M", files=["leg_upper"]))
        except BaseException as exc:  # noqa: BLE001 - what one raised, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=flag) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    queued = [o for o in orders.orders(ledger) if o.status == "queued"]
    assert len(queued) == 1, "one queued; the others said it's queued already"


def test_an_integrity_clash_is_said_never_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3  # noqa: PLC0415

    folder, ledger = _furniture_run(tmp_path, load=False)

    def clash(*_: Any, **__: Any) -> Any:
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(orders, "_insert", clash)
    ((made, said),) = orders.flag(folder, ledger, "Sponsor: M", files=["leg_upper"])
    assert made is None
    assert said == "leg_upper was queued meanwhile, by another: `queued` lists it"


def test_the_queue_command_starts_no_worker_on_another_ledger_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    folder, _ = _furniture_run(tmp_path, load=False)
    script = _worker(monkeypatch)
    elsewhere = tmp_path / "elsewhere.sqlite"
    shutil.copyfile(tmp_path / "ledger.sqlite", elsewhere)
    assert script.main(["queue", str(folder), "--by", "Sponsor: M", "--file", "leg_upper",
                        "--design-ledger", str(elsewhere)]) == 0
    assert "no worker started, as these orders are in another ledger" in capsys.readouterr().out

    def broken(root: Path, env: Any = None) -> str:
        raise OSError("no room")

    monkeypatch.setattr(script.study_orders, "start_worker", broken)
    assert script.main(["queue", str(folder), "--by", "Sponsor: M", "--file", "leg_lower"]) == 0
    assert "no worker could start (OSError: no room)" in capsys.readouterr().out


def test_parts_whose_files_share_a_name_are_told_apart_by_their_paths(tmp_path: Path) -> None:
    from print_shop.refine.approved import File  # noqa: PLC0415

    assembly_path = tmp_path / "assembly.json"
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "leg.stl").write_text("solid\nendsolid\n")
    assembly_path.write_text(json.dumps({"parts": [
        {"name": "front", "file": "a/leg.stl"}, {"name": "back", "file": "b/leg.stl"}]}))
    read = orders._Assembly.read(assembly_path)
    back = File("G", "leg.stl", "b/leg.stl", tmp_path / "b" / "leg.stl", "x")
    alone = File("G", "leg.stl", "leg.stl", tmp_path / "b" / "leg.stl", "x")
    found = read.part_of(back)
    assert found is not None and found["name"] == "back"
    assert read.part_of(alone) is None, "two parts' files share the name: not guessed"


def test_the_copy_is_held_to_the_approvals_own_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from print_shop.refine import approved as approved_mod  # noqa: PLC0415

    folder, ledger = _furniture_run(tmp_path)
    real = approved_mod.read(folder, ledger)
    group = real.groups[0]
    wrong = replace(real, groups=[replace(group, files=[replace(group.files[0], sha256="0" * 64),
                                                        *group.files[1:]])])
    monkeypatch.setattr(approved_mod, "read", lambda _folder, _ledger: wrong)
    _, said = orders.on_approval(folder, ledger, "on approval by sponsor")
    assert "its inputs couldn't be copied:" in said and "isn't as it was approved" in said
    tampered = next(o for o in orders.orders(ledger) if o.file == group.files[0].path)
    assert tampered.status == "failed"
