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
figure to its limit; and the case's own figure, where the need it came from gives one, judged
exactly as the design run's need is (`rounds.needs`). Each is a share of its limit, and its margin
is what's left. **A region Orca didn't build as asked** (the reader's `not as asked`, or `as the
part round it`) would be read by `load` as asked, stronger than printed: its candidate fails as
not as sliced, and isn't read. **What rests on the infill model says so:** a
core's figures are its published tests' (`bench/infill.py`), none checked by a print from this
printer yet (mqucifer/print-shop#340), and below the density they reach, checked by none. What
`load` solves again round a joint is read solid, stronger than printed, and the reply says so.
"""

from __future__ import annotations

import json
import math
import re
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from print_shop.bench import infill
from print_shop.bench.as_printed import wall_thickness
from print_shop.bench.job import BenchJob, Runner, run_here, stop_here
from print_shop.bench.printed import Printed, Settings, as_json
from print_shop.rounds.needs import Need, _judge_number
from print_shop.rounds.needs import Reading as NeedReading
from print_shop.slicer import gcode
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.regions import (
    Pose,
    settings_of,
    slice_project,
    slice_regions,
    stop_slicing,
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
    "check_names",
    "figures",
    "margin_said",
    "unchecked",
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


def check_names(cases: list[Case]) -> None:
    """Each case's name is its own, and a folder's name: the table's columns and each case's
    `load` replies (`candidates/NAME/CASE/`) are keyed by it.

    Raises:
        ValueError: Naming each name given twice, or that can't be a folder's.
    """
    names = [c.name for c in cases]
    if twice := sorted({n for n in names if names.count(n) > 1}):
        raise ValueError(f"each load case has a name of its own: {', '.join(twice)} is given "
                         "twice, between --case and the needs' ids. Name them apart")
    if bad := [n for n in names if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", n)]:
        raise ValueError(f"a case's name is letters, digits, '.', '_' and '-': not "
                         f"{', '.join(repr(n) for n in bad)}")


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
    #: Whether it holds, where that's judged as a design run's need is (`rounds.needs`), and
    #: not by its share alone: a limit of 0, a figure of 0 or less, or no figure at all.
    held: bool | None = None
    #: The need's own words for it, where it's judged so.
    words: str = ""

    @property
    def breaks(self) -> bool:
        """Whether it's past its limit."""
        return self.held is False or (self.held is None and self.share > 1.0)

    @property
    def margin(self) -> float:
        return 1.0 - self.share

    def said(self) -> str:
        if self.words:
            return f"{self.what}: {self.words}" + ("" if self.held else ", so it fails")
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
                "way": self.way, "infill": self.infill, "unchecked": self.unchecked,
                "held": self.held, "words": self.words}


@dataclass
class CaseRead:
    """One case's reply: its verdict and this part's figures."""

    case: str
    verdict: str
    why: str
    figures: list[Figure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float | None = None
    #: The host its `load` ran on, as its record names it (mqucifer/print-shop#373): "here", or
    #: a remote's name; None where it doesn't say.
    host: str | None = None

    @property
    def worst(self) -> Figure | None:
        return max(self.figures, key=lambda f: f.share, default=None)

    @property
    def passes(self) -> bool:
        return self.verdict == "OK" and not any(f.breaks for f in self.figures)

    def plain(self) -> dict[str, Any]:
        return {"case": self.case, "verdict": self.verdict, "why": self.why,
                "figures": [f.plain() for f in self.figures], "notes": self.notes,
                "seconds": self.seconds, "host": self.host}


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
    #: What of its regions Orca didn't build as asked, each the reader's line
    #: (`regions.Said`): `load` would read the region as asked, stronger than printed. And
    #: what couldn't be read from the layers, which `load` takes as asked.
    unbuilt: list[str] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)

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
                "bands": self.band_said(), "unbuilt": self.unbuilt, "unread": self.unread,
                "project": str(self.project) if self.project else None}


@dataclass
class Reading:
    """A candidate, as sliced and read under every case."""

    candidate: Candidate
    sliced: Sliced | None = None
    cases: list[CaseRead] = field(default_factory=list)
    #: Why it couldn't be sliced, where it couldn't.
    error: str = ""
    #: What of the Sponsor's limits it's past as sliced, where it is (`study.limits`): it
    #: can't be recommended, and isn't read further.
    ruled_out: str = ""

    @property
    def passes(self) -> bool:
        """Sliced, within the Sponsor's limits, and passing every case it's read under: with
        none, as a study of time, grams and finish is, on its slice alone."""
        return (not self.error and not self.ruled_out and self.sliced is not None
                and all(c.passes for c in self.cases))

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
        if self.ruled_out:
            return f"ruled out by the Sponsor's limits: {self.ruled_out}"
        said = []
        for case in self.cases:
            if case.verdict not in ("OK", "FAIL"):
                said.append(f"{case.case}: {case.verdict}, {case.why}"[:300])
                continue
            over = [f for f in case.figures if f.breaks]
            said += [f"{case.case}: {f.said()}" for f in over]
            if case.verdict == "FAIL" and not over:
                said.append(f"{case.case}: FAIL, {case.why}"[:300])
        return "; ".join(said)

    def plain(self) -> dict[str, Any]:
        worst = self.worst
        return {"candidate": self.candidate.plain(),
                "sliced": self.sliced.plain() if self.sliced else None,
                "cases": [c.plain() for c in self.cases], "error": self.error,
                "ruled_out": self.ruled_out,
                "passes": self.passes, "worst": worst.plain() if worst else None}


def margin_said(margin: float) -> str:
    """A worst figure's margin in words: what's left under its limit, or how far past it."""
    if not math.isfinite(margin):
        return "past its limit"
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


def _need(record: dict[str, Any], case: Case) -> Figure:
    """The case's own need's figure, judged exactly as the design run's need is
    (`rounds.needs`, `number_at` and `_judge_number`): a list's index followed, a figure at its
    limit to arithmetic's noise passing, and no figure at all failing. Its share is said where
    it means something, a figure of 0 or more against a limit over 0."""
    need = Need(id=case.name, what=case.name, source="derived", by="bench", command="load",
                number=case.number, at_least=case.at_least, at_most=case.at_most)
    judged = _judge_number(need, NeedReading(need=case.name, by="bench", passed=False, why="",
                                             verdict=str(record.get("verdict", ""))), record)
    value = judged.value
    share: float | None = None
    if value is not None:
        if case.at_most is not None and case.at_most > 0 and value >= 0:
            share = value / case.at_most
        elif case.at_most is None and case.at_least is not None and case.at_least > 0 \
                and value > 0:
            share = case.at_least / value
    if share is None:
        share = 0.0 if judged.passed else math.inf
    elif judged.passed:
        share = min(share, 1.0)
    else:
        share = max(share, math.nextafter(1.0, 2.0))
    limit = case.at_most if case.at_most is not None else case.at_least
    return Figure(f"{case.number} (the need's own)", math.nan if value is None else value,
                  math.nan if limit is None else float(limit), "", share, held=judged.passed,
                  words=judged.why)


def unchecked(printed: dict[str, Any]) -> list[str]:
    """Each cell of a part's `print` whose core is below the density any published test of its
    pattern reaches (`infill.checked_from`, mqucifer/print-shop#342), in words: the part's
    own, and each region's that gives its infill or pattern."""
    part = {k: v for k, v in printed.items() if k != "regions"}
    cells = [("the part", part)] + [
        (f"region {r.get('name')}", {**part, **{k: r[k] for k in ("infill", "pattern")
                                                if k in r}})
        for r in printed.get("regions") or () if "infill" in r or "pattern" in r]
    found = []
    for name, cell in cells:
        pattern, density = cell.get("pattern"), cell.get("infill")
        if pattern in infill.LINES and isinstance(density, int | float):
            start = infill.checked_from(str(pattern))
            if density < start:
                found.append(f"{name}: {pattern} at {density:g}%, which the published tests of "
                             f"{pattern} reach only from {start:g}%")
    return found


def figures(record: dict[str, Any], part: str, case: Case) -> CaseRead:
    """This part's figures in one `load` reply (its saved record), each against its limit."""
    numbers = record.get("numbers") or {}
    ran = record.get("ran")
    on = ran.get("host") if isinstance(ran, dict) else None
    read = CaseRead(case.name, str(record.get("verdict", "NO ANSWER")),
                    str(record.get("why", "")), seconds=record.get("seconds"),
                    host=str(on) if on else None)
    one = (numbers.get("parts") or {}).get(part)
    if isinstance(one, dict):
        printed = bool(one.get("printed"))
        # The skin's figures rest on the core's stiffness too: what it carries is what the core
        # doesn't. So a core no test checks leaves the skin's unchecked too.
        loose = any(bool(c.get("unchecked")) for c in one.get("cores") or ())
        read.figures += _strains(one, "its skin" if printed else "the part (read solid)",
                                 printed, loose)
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
        read.figures.append(_need(record, case))
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

    def stop(self) -> None:
        """Stop every slice running: a study that's interrupted leaves none."""
        stop_slicing()

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
            unbuilt = [f"{r.region.name}'s {s.line()}" for r in result.regions for s in r.said
                       if s.verdict in ("not as asked", "as the part round it")]
            unread = [f"{r.region.name}'s {s.line()}" for r in result.regions for s in r.said
                      if s.verdict == "not read"]
            return Sliced(result.time_s, result.grams, settings, result.for_load(),
                          result.base_time_s, result.base_grams, result.band_mm,
                          _walls_mm(settings), regions, bands, result.project, result.gcode,
                          unbuilt, unread)
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
    """Reads each candidate with `load`, in the bench's second image, under one case: each read
    a bench job (`bench.job`), its files in its own folder, run here or by `runner` on any host
    with the same image."""

    assembly: Path
    part: str
    runner: Runner = run_here

    def job(self, candidate: Candidate, sliced: Sliced, case: Case, out: Path) -> BenchJob:
        """The `load` of one candidate under one case, as a job over its files in `out/load`."""
        work = out / "load"
        work.mkdir(parents=True, exist_ok=True)
        raw = json.loads(self.assembly.read_text())
        files = []
        for name in _files(raw):
            target = work / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.assembly.parent / name, target)
            files.append(name)
        for path in candidate.meshes.values():
            shutil.copy2(path, work / path.name)
            files.append(path.name)
        layers = (sliced.settings.up or "").lstrip("+-") or None
        for part in raw.get("parts") or ():
            if isinstance(part, dict) and part.get("name") == self.part:
                part["print"] = sliced.printed
                if layers:
                    part["layers"] = layers
        name = "study-assembly.json"
        (work / name).write_text(json.dumps(raw, indent=1))
        return BenchJob.of(["load", name, *case.args], work, [name, *files])

    def stop(self) -> None:
        """Stop every read this host is running for it: a study that's interrupted leaves none.
        A runner for another host stops its own."""
        if self.runner is run_here:
            stop_here()

    def __call__(self, candidate: Candidate, sliced: Sliced, case: Case,
                 out: Path) -> dict[str, Any]:
        job = self.job(candidate, sliced, case, out)
        done = self.runner(job)
        found = _SAVED.search(done.stdout)
        if not found:
            tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
            return {"verdict": "NO ANSWER", "why": "the bench saved no reply: " + " / ".join(tail),
                    "numbers": {}}
        record: dict[str, Any] = json.loads(
            (job.folder / f"{found.group(1)}.json").read_text())
        return record
