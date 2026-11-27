"""A candidate read: sliced through Orca for its time and grams, and read by `load` under each of
the part's load cases, each figure against its limit (mqucifer/print-shop#339).

**Sliced** (`OrcaSlicing`), each step Orca's own (`slicer.regions`): the part posed, its settings
written into a whole copy of the process profile with its wall order, and its regions as Orca's
own modifiers. A candidate with regions is sliced again without them, so what they cost is said.
The walls' band is read from the layers of the part without its regions (`regions.wall_band`),
and from each region's own where it gives walls: a second reading of `load`'s wall thickness,
n of Orca's bead spacings (`as_printed.wall_thickness`). It lies a bead's width past its spacing
less a little, 0.07 to 0.09 mm on a bar; a gap of more than `BAND_GAP` of a line width says the
settings alone don't give the skin `load` reads there.

**Read** (`BenchLoad`), in the bench's second image: a copy of the part's assembly file with the
part's `print` as it was sliced, every setting resolved from the profile (`for_load`), and its
layers along the axis its pose stands up, beside copies of the files it names and each region's
mesh. Each load case is one `load` of it. Of the reply, this part's figures are read
(`figures`): its skin's, or the whole part's where it's read solid, pulled across its layers and
stretched any way, and each core's, against their own limits; each joint it's in, by its nearest
figure to its limit; and the case's own figure, where the need it came from gives one. Each is a
share of its limit, and its margin is what's left. **What rests on the infill model says so:** a
core's figures are its published tests' (`bench/infill.py`), none checked by a print from this
printer yet (mqucifer/print-shop#340), and below the density they reach, checked by none. What
`load` solves again round a joint is read solid, stronger than printed, and the reply says so.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from print_shop.bench import host
from print_shop.bench.as_printed import wall_thickness
from print_shop.bench.printed import Printed, Settings, as_json
from print_shop.slicer import gcode
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.regions import (
    Pose,
    settings_of,
    slice_project,
    slice_regions,
    wall_band,
    write_project,
)
from print_shop.study.candidate import Candidate

__all__ = [
    "BAND_GAP",
    "BenchLoad",
    "Case",
    "CaseRead",
    "Figure",
    "OrcaSlicing",
    "Reading",
    "Sliced",
    "cases_from_needs",
    "figures",
    "margin_said",
]

#: The walls' band and `load`'s wall thickness differ by more than this share of a line's width,
#: past the 0.07 to 0.09 mm a bead's width lies past its spacing: the settings alone don't give
#: the skin `load` reads.
BAND_GAP = 0.5
#: What the band lies past `load`'s wall thickness, in mm, as read on the bar
#: (`regions.band`): its middle.
BAND_PAST = 0.08


@dataclass(frozen=True)
class Case:
    """One load case: `load`'s words after the assembly file, and the need's own figure, where
    the need it came from gives one (a path into the reply's numbers, as `moves_mm`)."""

    name: str
    args: tuple[str, ...]
    number: str = ""
    at_most: float | None = None
    at_least: float | None = None

    def said(self) -> str:
        own = ""
        if self.number:
            bound = (f" at most {self.at_most:g}" if self.at_most is not None else "") + (
                f" at least {self.at_least:g}" if self.at_least is not None else "")
            own = f", and {self.number}{bound}"
        return f"{self.name}: load {' '.join(self.args)}{own}"


def cases_from_needs(path: Path, only: list[str]) -> list[Case]:
    """Each `load` need of a design run's needs, as a case: its words after the assembly file,
    with its own figure and limit. `--out` is the bench's, and is left out."""
    found: list[Case] = []
    for need in json.loads(path.read_text()):
        argv = shlex.split(str(need.get("command", "")))
        if "load" not in argv or (only and need.get("id") not in only):
            continue
        rest = argv[argv.index("load") + 1:]
        if rest and not rest[0].startswith("-"):
            rest = rest[1:]  # the run's assembly file: the study's is read in its place
        if "--out" in rest:
            k = rest.index("--out")
            rest = rest[:k] + rest[k + 2:]
        found.append(Case(str(need.get("id") or f"need-{len(found) + 1}"), tuple(rest),
                          str(need.get("number") or ""), need.get("at_most"),
                          need.get("at_least")))
    return found


@dataclass(frozen=True)
class Figure:
    """One figure of a case against its limit."""

    what: str
    value: float
    limit: float
    unit: str
    share: float
    where: tuple[float, ...] | None = None
    way: str = ""
    #: It rests on the infill model's published tests, which no print has checked.
    infill: bool = False
    #: Below the density any published test of its pattern reaches.
    unchecked: bool = False
    #: A joint's nearest figure to its limit, said as its share alone.
    joint: bool = False

    @property
    def margin(self) -> float:
        return 1.0 - self.share

    def said(self) -> str:
        at = f" at ({', '.join(f'{v:g}' for v in self.where)})" if self.where else ""
        way = f", {self.way}" if self.way else ""
        rests = (" [infill model, unchecked: below its tests' densities]" if self.unchecked
                 else " [infill model]" if self.infill else "")
        if self.joint:
            return f"{self.what}, at {100 * self.share:.0f}% of its limit"
        return (f"{self.what} {self.value:.3g}{self.unit} of {self.limit:.3g}{self.unit}, "
                f"{100 * self.share:.0f}% of its limit{at}{way}{rests}")

    def plain(self) -> dict[str, Any]:
        return {"what": self.what, "value": self.value, "limit": self.limit, "unit": self.unit,
                "share": round(self.share, 4), "where": list(self.where or ()),
                "way": self.way, "infill": self.infill, "unchecked": self.unchecked}


@dataclass
class CaseRead:
    """One case's reply: its verdict and this part's figures."""

    case: str
    verdict: str
    why: str
    figures: list[Figure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float | None = None

    @property
    def worst(self) -> Figure | None:
        return max(self.figures, key=lambda f: f.share, default=None)

    @property
    def passes(self) -> bool:
        return self.verdict == "OK" and all(f.share <= 1.0 for f in self.figures)

    def plain(self) -> dict[str, Any]:
        return {"case": self.case, "verdict": self.verdict, "why": self.why,
                "figures": [f.plain() for f in self.figures], "notes": self.notes,
                "seconds": self.seconds}


@dataclass
class Sliced:
    """A candidate as Orca sliced it."""

    time_s: int | None
    grams: float | None
    #: Every setting it was sliced with, resolved from the profile, with the way it stood.
    settings: Settings
    #: The part's `print`, whole, as `load` takes it.
    printed: dict[str, Any]
    #: Without its regions, where it has some.
    base_time_s: int | None = None
    base_grams: float | None = None
    #: How wide its walls lie as built, and what `load` takes them as, in mm.
    band_mm: float | None = None
    walls_mm: float | None = None
    #: Each region's reading from the layers (`RegionRead.lines`), and its walls' band where it
    #: gives walls of its own, against `load`'s.
    regions: list[str] = field(default_factory=list)
    region_bands: dict[str, tuple[float | None, float]] = field(default_factory=dict)
    project: Path | None = None
    gcode: Path | None = None

    def band_said(self) -> list[str]:
        """The walls' band against `load`'s thickness, where it was read."""
        width = float(self.settings.line_width or 0.45)
        out = []
        pairs = [("the part", self.band_mm, self.walls_mm)] + [
            (f"region {name}", band, walls) for name, (band, walls) in self.region_bands.items()]
        for who, band, walls in pairs:
            if band is None or walls is None:
                continue
            gap = band - walls - BAND_PAST
            agrees = abs(gap) <= BAND_GAP * width
            out.append(
                f"{who}'s walls lie {band:.2f} mm wide in its layers, where load takes "
                f"{walls:.2f} mm: " + ("as load reads them" if agrees else
                                       f"{gap:+.2f} mm past a bead's width over load's, so the "
                                       "settings alone don't give the skin load reads there"))
        return out

    def plain(self) -> dict[str, Any]:
        return {"time_s": self.time_s, "grams": self.grams, "base_time_s": self.base_time_s,
                "base_grams": self.base_grams, "band_mm": self.band_mm,
                "walls_mm": self.walls_mm, "print": self.printed, "regions": self.regions,
                "bands": self.band_said(),
                "project": str(self.project) if self.project else None}


@dataclass
class Reading:
    """A candidate, as sliced and read under every case."""

    candidate: Candidate
    sliced: Sliced | None = None
    cases: list[CaseRead] = field(default_factory=list)
    #: Why it couldn't be sliced, where it couldn't.
    error: str = ""

    @property
    def passes(self) -> bool:
        return not self.error and bool(self.cases) and all(c.passes for c in self.cases)

    @property
    def answered(self) -> bool:
        """Whether every case had an answer: OK or FAIL, never NO ANSWER, UNREAD or DISAGREE."""
        return not self.error and bool(self.cases) and all(
            c.verdict in ("OK", "FAIL") for c in self.cases)

    @property
    def worst(self) -> Figure | None:
        return max((c.worst for c in self.cases if c.worst is not None),
                   key=lambda f: f.share, default=None)

    @property
    def margin(self) -> float | None:
        worst = self.worst
        return None if worst is None else worst.margin

    def why_not(self) -> str:
        """Why it fails, in words; "" where it passes."""
        if self.error:
            return self.error
        said = []
        for case in self.cases:
            if case.verdict not in ("OK", "FAIL"):
                said.append(f"{case.case}: {case.verdict}, {case.why}"[:300])
                continue
            over = [f for f in case.figures if f.share > 1.0]
            said += [f"{case.case}: {f.said()}" for f in over]
            if case.verdict == "FAIL" and not over:
                said.append(f"{case.case}: FAIL, {case.why}"[:300])
        return "; ".join(said)

    def plain(self) -> dict[str, Any]:
        worst = self.worst
        return {"candidate": self.candidate.plain(),
                "sliced": self.sliced.plain() if self.sliced else None,
                "cases": [c.plain() for c in self.cases], "error": self.error,
                "passes": self.passes, "worst": worst.plain() if worst else None}


def margin_said(margin: float) -> str:
    """A worst figure's margin in words: what's left under its limit, or how far past it."""
    if margin >= 0:
        return f"a margin of {100 * margin:.0f}%"
    return f"{100 * -margin:.0f}% past its limit"


# --------------------------------------------------------------------------
# A reply's figures
# --------------------------------------------------------------------------


def _at(raw: Any) -> tuple[float, ...] | None:
    if isinstance(raw, list | tuple) and raw and all(isinstance(v, int | float) for v in raw):
        return tuple(round(float(v), 1) for v in raw)
    return None


def _strains(one: dict[str, Any], who: str, infill_model: bool,
             unchecked: bool = False) -> list[Figure]:
    found = []
    across, across_limit = one.get("across_layers_pct"), one.get("across_limit_pct")
    if isinstance(across, int | float) and across_limit:
        found.append(Figure(f"{who} pulled across its layers", float(across),
                            float(across_limit), "%", float(across) / float(across_limit),
                            _at(one.get("across_at")), "across the layers", infill_model,
                            unchecked))
    stretch, stretch_limit = one.get("stretch_pct"), one.get("stretch_limit_pct")
    if isinstance(stretch, int | float) and stretch_limit:
        way = f"{one.get('stretch_is', 'stretch')}"
        if isinstance(one.get("stretch_from_layers_deg"), int | float):
            way += f", {one['stretch_from_layers_deg']:.0f}° out of the layers' plane"
        if isinstance(one.get("stretch_from_lines_deg"), int | float):
            way += f", {one['stretch_from_lines_deg']:.0f}° off its lines"
        found.append(Figure(f"{who} stretched", float(stretch), float(stretch_limit), "%",
                            float(stretch) / float(stretch_limit), _at(one.get("stretch_at")),
                            way, infill_model, unchecked))
    return found


def _joint_share(joint: dict[str, Any]) -> float | None:
    """A joint's nearest figure to its limit, as a share of it, where it has one, as
    `bench.load_parts.joint_share` reads it."""
    if isinstance(joint.get("nearest_share"), int | float):
        return float(joint["nearest_share"])
    if joint.get("holds_n") and isinstance(joint.get("bolt_pull_n"), int | float):
        return float(joint["bolt_pull_n"]) / float(joint["holds_n"])
    return None


def _number_at(numbers: dict[str, Any], path: str) -> float | None:
    found: Any = numbers
    for key in path.split("."):
        if not isinstance(found, dict) or key not in found:
            return None
        found = found[key]
    return float(found) if isinstance(found, int | float) and not isinstance(found, bool) else None


def figures(record: dict[str, Any], part: str, case: Case) -> CaseRead:
    """This part's figures in one `load` reply (its saved record), each against its limit."""
    numbers = record.get("numbers") or {}
    read = CaseRead(case.name, str(record.get("verdict", "NO ANSWER")),
                    str(record.get("why", "")), seconds=record.get("seconds"))
    one = (numbers.get("parts") or {}).get(part)
    if isinstance(one, dict):
        printed = bool(one.get("printed"))
        # The skin's figures rest on the core's stiffness too: what it carries is what the core
        # doesn't.
        read.figures += _strains(one, "its skin" if printed else "the part (read solid)",
                                 printed)
        for core in one.get("cores") or ():
            read.figures += _strains(core, f"its core in {core.get('cell', 'the part')}", True,
                                     bool(core.get("unchecked")))
    for name, joint in (numbers.get("joints") or {}).items():
        if part not in str(name).split("+") or not isinstance(joint, dict):
            continue
        share = _joint_share(joint)
        if share is not None:
            gives = joint.get("gives_first") or joint.get("holds_from") or "its nearest figure"
            read.figures.append(Figure(f"joint {name}, {gives}", round(100 * share, 2), 100.0,
                                       "%", share, joint=True))
    if case.number:
        value = _number_at(numbers, case.number)
        if value is not None and case.at_most:
            read.figures.append(Figure(f"{case.number} (the need's own)", value, case.at_most,
                                       "", value / case.at_most))
        if value is not None and case.at_least and value > 0:
            read.figures.append(Figure(f"{case.number} (the need's own, at least)", value,
                                       case.at_least, "", case.at_least / value))
    read.notes = [str(n) for n in record.get("notes") or ()
                  if part in str(n) or "infill" in str(n)]
    return read


# --------------------------------------------------------------------------
# The real slicer and the real `load`
# --------------------------------------------------------------------------


#: How a candidate is sliced: it, and the folder its slice is written in.
Slicing = Callable[[Candidate, Path], Sliced]
#: How it's read under one case: it, its slice, the case and a folder; `load`'s saved record.
Loading = Callable[[Candidate, Sliced, Case, Path], dict[str, Any]]


def _sequenced(profiles: SlicerProfiles, sequence: str | None, out: Path) -> SlicerProfiles:
    """`profiles` with the wall order written into a whole copy of the process."""
    if sequence is None:
        return profiles
    data = json.loads(Path(profiles.process).read_text())
    data["wall_sequence"] = sequence
    out.mkdir(parents=True, exist_ok=True)
    path = out / "process_walls.json"
    path.write_text(json.dumps(data, indent=2))
    return profiles.model_copy(update={"process": path})


@dataclass
class OrcaSlicing:
    """Slices each candidate through Orca with the job's profiles (`slicer.regions`)."""

    stl: Path
    profiles: SlicerProfiles
    #: Where each region's mesh is named from.
    meshes: Path
    binary: Path | None = None
    timeout_s: float = 900.0

    def __call__(self, candidate: Candidate, out: Path) -> Sliced:
        profiles = _sequenced(self.profiles, candidate.wall_sequence, out)
        printed = candidate.printed()
        if candidate.regions:
            result = slice_regions(self.stl, printed, profiles, out, candidate.turn,
                                   self.meshes, self.binary, self.timeout_s)
            settings = replace(result.settings, up=result.up)
            bands = {r.region.name: (r.band_mm, wall_thickness(
                int(r.asked.walls or 0), float(r.asked.line_width or 0.45),
                float(r.asked.layer_height or 0.2)))
                for r in result.regions if r.region.settings.walls is not None}
            regions = [line for r in result.regions for line in r.lines()]
            return Sliced(result.time_s, result.grams, settings, result.for_load(),
                          result.base_time_s, result.base_grams, result.band_mm,
                          _walls_mm(settings), regions, bands, result.project, result.gcode)
        _, without = write_project(self.stl, Printed(candidate.settings), profiles, out,
                                   candidate.turn, self.meshes, self.binary, self.timeout_s)
        path = slice_project(without, out / "without", self.binary, self.timeout_s)
        sliced = gcode.read(path, keep_lines=True)
        found = sliced.per_filament("total filament used [g]")
        settings = replace(without.settings, up=without.pose.up())
        return Sliced(sliced.time_s, round(sum(found), 2) if found else None, settings,
                      as_json(Printed(settings)), band_mm=wall_band(sliced, without),
                      walls_mm=_walls_mm(settings), project=without.path, gcode=path)


def _walls_mm(settings: Settings) -> float:
    return wall_thickness(int(settings.walls or 0), float(settings.line_width or 0.45),
                          float(settings.layer_height or 0.2))


def defaults(profiles: SlicerProfiles, stl: Path, turn: tuple[float, float, float]) -> Settings:
    """The profile's own settings, as the part would print with them in its pose: the infill's
    angle in the part's own axes (`regions.settings_of`, `Pose.own_angle`)."""
    import trimesh

    found = settings_of(Path(profiles.process), Path(profiles.machine))
    mesh = trimesh.load(stl, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{stl.name} isn't one mesh")
    pose = Pose.of(mesh, turn)
    if found.infill_angle is not None:
        own = pose.own_angle(found.infill_angle)
        if own is not None:
            found = replace(found, infill_angle=own)
    return replace(found, up=pose.up())


#: Where the bench says it saved a reply.
_SAVED = re.compile(r"saved: (\S+)\.txt and \.json")


def _files(raw: dict[str, Any]) -> list[str]:
    """The files an assembly file names beside it: each part's, its `with`s and its band."""
    named = []
    for part in raw.get("parts") or ():
        if not isinstance(part, dict):
            continue
        named.append(part.get("file"))
        named += list(part.get("with") or ())
        named.append(part.get("band"))
    return [n for n in named if isinstance(n, str) and n]


@dataclass
class BenchLoad:
    """Reads each candidate with `load`, in the bench's second image, under one case."""

    assembly: Path
    part: str

    def __call__(self, candidate: Candidate, sliced: Sliced, case: Case,
                 out: Path) -> dict[str, Any]:
        work = out / "load"
        work.mkdir(parents=True, exist_ok=True)
        raw = json.loads(self.assembly.read_text())
        for name in _files(raw):
            target = work / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.assembly.parent / name, target)
        for path in candidate.meshes.values():
            shutil.copy2(path, work / path.name)
        layers = (sliced.settings.up or "").lstrip("+-") or None
        for part in raw.get("parts") or ():
            if isinstance(part, dict) and part.get("name") == self.part:
                part["print"] = sliced.printed
                if layers:
                    part["layers"] = layers
        name = "study-assembly.json"
        (work / name).write_text(json.dumps(raw, indent=1))
        done = host.run(["load", name, *case.args], work)
        found = _SAVED.search(done.stdout)
        if not found:
            tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
            return {"verdict": "NO ANSWER", "why": "the bench saved no reply: " + " / ".join(tail),
                    "numbers": {}}
        record: dict[str, Any] = json.loads((work / f"{found.group(1)}.json").read_text())
        return record
