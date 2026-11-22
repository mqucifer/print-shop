"""A part's context: its fits, seen faces, purpose, the side it's laid on and its holds, from its
design run or from a sidecar beside it.

mqucifer/print-shop#130. When a part comes from a design run, the run knows things the print side
had to guess: which faces fit another part and how closely, which side it was laid on, and how
it's held. On 2026-11-14 the slice reading proposed a random seam on a part whose top 5 mm is a
0.10 mm fit, which would have scattered seam bumps all round the fit. It had no way to know.

**Where it comes from,** first found wins:

1. **A sidecar** (`SIDECAR`): `PART.part.json` beside `PART.stl`, or the file `--context` names. A
   part made or edited by hand has one, as it won't match the run's file. Its shape is in
   `docs/runbook-first-print.md` ("A part's context"): a few lines, in the STL's own coordinates.
2. **The design run** the STL sits in: the nearest folder above it with the run's `state.json`
   and `needs.json` (`~/print-shop/design-runs/<run>/`). Its settled needs, its facts
   (`state.json`'s `facts`) and its latest refinement's findings
   (`refinements/<n>/findings.json`) are read, for the part the STL is: named by its stem, as
   the refinement pass names its meshes (`<folder>__<file>.stl`).
3. **Neither:** the print goes on as before, and the review says so.

**A fit** is a run's `clearance` need on this part (never a `--slide`, which reads a hold) whose
play is at most `FIT_MM`: two parts meant to mate. Its play is the need's own limits. **Its
place is derived from geometry** (the operator, 2026-11-14): the need's own `--box` place, else
where this part's box meets its mate's, from the mate's mesh in the run, else from the mate's fact
(its `box`). Fields on the CAD side's clearance needs come later, only if the derived band proves
wrong on real parts. A fit whose place can't be derived holds the whole wall as the fit, and says
so. The review says the band is derived, and from which need.

**What it's for and what's seen are read back at the questions** (mqucifer/print-shop#236): a
sidecar's `purpose` says what the part is for in words (`for`) and how it's loaded (`loaded`,
one of `LOADS`), and its `seen` any of the faces. Neither counts as the person's until they
confirm it. Words alone, and a sidecar's `about`, are shown and never read as a purpose. A
purpose that can't be read is said and asked, and costs the part none of the rest of its notes.

Placed in a pose (`place`), a fit is a band of heights as it prints, which the slice reading's
seam rule and elephant foot proposal read (`reading.flags`), and a hold's way on or off is read
against the layers. Both paths, today's single calls and the print loop, read it the same way.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

__all__ = [
    "FIT_MM",
    "AS_THE_FILE_LIES",
    "LOADS",
    "SET_ASIDE",
    "SIDECAR",
    "SIDES",
    "Fit",
    "FitBand",
    "Hold",
    "PartContext",
    "Placed",
    "Seen",
    "own_sides",
    "pose_for",
    "place",
    "plain_line",
    "printed",
    "read_context",
    "refuses",
    "seen_from",
]

#: A sidecar beside `PART.stl`: `PART.part.json`.
SIDECAR = ".part.json"
#: A `clearance` need whose play is at most this, in mm, is a fit: two parts meant to mate. A
#: larger gap is room to keep apart, never a fit.
FIT_MM = 0.5
#: The sides a part can lie on, as the design run names them (`rounds.needs.SIDES`).
SIDES = ("-z", "+z", "-x", "+x", "-y", "+y")
#: The pose the file lies in, as a rotation.
AS_THE_FILE_LIES = (0.0, 0.0, 0.0)
#: How a part is loaded, as a sidecar's purpose says it: the purpose question's options
#: (`questions.PURPOSE`, which asserts they're the same).
LOADS = (
    "decoration or display",
    "handled, clipped or lightly loaded",
    "carries a real load",
    "a prototype or test fit",
)
#: What follows a line of the notes that the person's answer stands in place of
#: (mqucifer/print-shop#236). Past prints' contexts are compared without it (`similar`).
SET_ASIDE = " (set aside: you said "
#: What follows the notes' seen faces once the part is turned from the file's pose: those faces
#: as they print (mqucifer/print-shop#248). Compared without it, as `SET_ASIDE` is.
AS_PRINTED = " (as it prints: "
#: How the bench lays a run's file on a side (`bench.refine.ON_BED`, kept the same here so the
#: print side never imports the bench's): about an axis through the origin, in degrees.
ON_BED: dict[str, tuple[tuple[float, float, float], float]] = {
    "-z": ((1.0, 0.0, 0.0), 0.0),
    "+z": ((1.0, 0.0, 0.0), 180.0),
    "+x": ((0.0, 1.0, 0.0), 90.0),
    "-x": ((0.0, 1.0, 0.0), -90.0),
    "+y": ((1.0, 0.0, 0.0), -90.0),
    "-y": ((1.0, 0.0, 0.0), 90.0),
}
#: The folder the refinement pass writes a file laid on its `on_bed` side into.
LAID = "meshing-laid"
#: Mesh suffixes a mate's geometry may be read from on the Mac, without the bench's image.
MESHES = (".stl",)
#: A run's folders never searched for a mate's mesh: the pass's own plates and test walls.
NOT_MATES = ("plates", "walls", LAID, "record", "rounds", "approvals")
#: At most this many facts and findings are carried, each cut to `LINE_CHARS`.
CARRIED = 12
LINE_CHARS = 300

Box = tuple[tuple[float, float, float], tuple[float, float, float]]


class Seen(BaseModel):
    """Which faces of the part will be seen: from the intake's answer, or a part's context."""

    model_config = {"frozen": True}

    walls: bool
    top: bool  # the top as it prints
    bottom: bool  # the face on the plate
    said: str  # the answer, in its words
    noted: bool = False  # from the part's notes, and not the person's own answer
    #: Only some of the walls: a side that was the top or the plate's before a turn
    #: (mqucifer/print-shop#248).
    part_walls: bool = False
    #: What they were called, where they're carried from another pose.
    called: str = ""
    #: The faces ("walls", "top", "bottom") that take in one the answer didn't speak of: never
    #: judged hidden, nor different from another print's.
    unsaid: tuple[str, ...] = ()
    #: Several parts' faces, whose `said` already says whose each is.
    of_several: bool = False

    @property
    def top_unsaid(self) -> bool:
        """The top is a face the answer didn't speak of, and nothing says it's seen."""
        return "top" in self.unsaid and not self.top

    def unsaid_words(self) -> str:
        """What the answer didn't speak of that could be read as hidden, or ""."""
        if self.top_unsaid:
            return "the top wasn't asked about"
        return "one of the walls wasn't asked about" if "walls" in self.unsaid else ""

    def same_as(self, other: Seen) -> bool:
        """The same faces seen, counting none that either didn't speak of as a difference."""
        skip = {*self.unsaid, *other.unsaid}
        return all(getattr(self, face) == getattr(other, face)
                   for face in ("walls", "top", "bottom") if face not in skip)

    def because(self) -> str:
        """Who said it and what, as a reason opens: after a turn, what that is as it prints."""
        if self.of_several:
            return self.said
        if self.called:
            return f"{self.whose()} {self.called}; as it prints that's {self.as_printed()}"
        if self.noted:
            return f"{self.whose()} {self.in_words()}, as the file lays it"
        return f"{self.whose()} '{self.said}'"

    def whose(self) -> str:
        """Who said it, as a reason opens."""
        return "the part's notes say" if self.noted else "you said"

    def faces(self) -> str:
        """The faces, as a sidecar lists them."""
        listed = (("walls", self.walls), ("top", self.top), ("bottom", self.bottom))
        return ", ".join(name for name, shown in listed if shown) or "nothing"

    def in_words(self) -> str:
        """The same faces as a person reads them: "the top and the bottom"."""
        names = ["part of the walls" if self.part_walls and name == "walls" else f"the {name}"
                 for name in self.faces().split(", ")]
        if names == ["the nothing"]:
            return "none of its faces"
        *most, last = names
        return f"{', '.join(most)} and {last}" if most else last

    def as_printed(self) -> str:
        """The same, where the bottom is as the questions name it: "the top and the face on
        the plate"."""
        return self.in_words().replace("the bottom", "the face on the plate")


class Fit(BaseModel):
    """Faces of this part that fit another, with their play, and where they are."""

    model_config = {"frozen": True}

    name: str  # the need's id, or the sidecar's name for it
    mate: str = ""  # what it fits: the other file, or the sidecar's words
    play_min: float | None = None
    play_max: float | None = None
    #: Where the fit is, as a box in the STL's own coordinates. None: not derived, so the whole
    #: wall is held as the fit.
    box: Box | None = None
    #: How its place was found, in words: what the review says.
    derived: str = ""

    def play(self) -> str:
        low, high = self.play_min, self.play_max
        if low is not None and high is not None and low != high:
            return f"{low:g} to {high:g} mm"
        one = high if high is not None else low
        return f"{one:g} mm" if one is not None else "play not given"

    def line(self) -> str:
        mate = f" with {self.mate}" if self.mate else ""
        return f"a {self.play()} fit{mate} (`{self.name}`), {self.derived}"


class Hold(BaseModel):
    """A hold on this part: how it slides on or off, in the STL's own coordinates."""

    model_config = {"frozen": True}

    name: str
    mate: str = ""
    way: str = ""  # one of `SIDES`, the way the bench's slide reads it

    def line(self) -> str:
        mate = f" with {self.mate}" if self.mate else ""
        way = f", sliding along {self.way}" if self.way else ""
        return f"a hold{mate} (`{self.name}`){way}"


class PartContext(BaseModel):
    """What a part's design run or sidecar says about it, and what was read to say it."""

    model_config = {"frozen": True}

    #: Where it came from, in words: "" for none.
    source: str = ""
    #: The sidecar or the run's folder it was read from, which `--context` takes again.
    path: str = ""
    #: What was read, each in a line: the files, and how many needs, facts and findings.
    read: tuple[str, ...] = ()
    fits: tuple[Fit, ...] = ()
    holds: tuple[Hold, ...] = ()
    #: Which faces are seen, as the file lies. None: not said.
    seen: Seen | None = None
    #: What it's for, in the notes' words, and how it's loaded, one of `LOADS`. "": not said.
    purpose: str = ""
    loaded: str = ""
    #: A sidecar's free text, which is shown and never read as a purpose.
    about: str = ""
    #: The side it's laid on, in the STL's own coordinates, and where that was read.
    down: str = ""
    down_from: str = ""
    #: The run's facts and its refinement's findings on this part, as lines.
    facts: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    #: What couldn't be read, said.
    notes: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.source)

    def lines(self, set_aside: Mapping[str, str] | None = None, prints: str = "") -> list[str]:
        """The context as the review, the model and the ledger read it, in the file's own pose.
        `set_aside` has the person's answer by what it replaced ("purpose", "seen"): that line
        says so, so that it doesn't read as what's being judged. `prints` is the notes' seen
        faces as they print, for a pose the file doesn't lie in."""
        instead = set_aside or {}

        def said(line: str, kind: str) -> str:
            return f"{line}{SET_ASIDE}'{instead[kind]}')" if kind in instead else line

        if not self.found:
            return ["none: no design run above the file and no sidecar beside it, so it's read "
                    "as before", *self.notes]
        out = [f"from {self.source}", *(f"read {r}" for r in self.read)]
        out += [f"fit: {f.line()}" for f in self.fits]
        out += [f"hold: {h.line()}" for h in self.holds]
        if self.loaded:
            words = f"{self.purpose}; " if self.purpose else ""
            out.append(said(f"purpose: {words}loaded as '{self.loaded}'", "purpose"))
        elif self.purpose:
            out.append(f"purpose, in words only: {self.purpose}")
        if self.about:
            out.append(f"about: {self.about}")
        turned = f"{AS_PRINTED}{prints})" if prints and "seen" not in instead else ""
        out.append(said(f"seen: {self.seen.said}{turned}", "seen") if self.seen is not None
                   else "seen: not said here, so the intake's answer stands")
        if self.down:
            out.append(f"laid on its {self.down} side ({self.down_from})")
        out += [f"fact: {f}" for f in self.facts]
        out += [f"finding: {f}" for f in self.findings]
        out += [f"note: {n}" for n in self.notes]
        return out


class FitBand(BaseModel):
    """A fit placed in a pose: the heights it spans as the part prints."""

    model_config = {"frozen": True}

    fit: Fit
    z_from: float
    z_to: float

    def line(self) -> str:
        return f"z {self.z_from:g} to {self.z_to:g} mm as it prints: {self.fit.line()}"


class Placed(BaseModel):
    """A part's context in the pose it prints in."""

    model_config = {"frozen": True}

    fits: tuple[FitBand, ...] = ()
    #: Holds whose way on or off is up and down as it prints: their pull is across the layers.
    across: tuple[Hold, ...] = ()
    #: Which faces are seen, turned into this pose. None: the context didn't say.
    seen: Seen | None = None
    #: The pose turns it off the side its context lays it on: said, the side and where from.
    off_side: str = ""

    def lines(self) -> list[str]:
        out = [f"fit at {b.line()}" for b in self.fits]
        out += [f"hold across the layers: {h.line()}: its pull is across the layer lines, where "
                "a print is weakest" for h in self.across]
        if self.off_side:
            out.append(self.off_side)
        return out


# --------------------------------------------------------------------------
# Reading it
# --------------------------------------------------------------------------


def read_context(stl: Path, given: Path | None = None) -> PartContext:
    """The part's context: from `given` (a sidecar, or a run's folder), a sidecar beside the STL,
    or the design run the STL sits in. Never raises for what it reads: what can't be read is said
    in `notes`, and the print goes on as before."""
    try:
        if given is not None:
            if given.is_dir():
                return _from_run(given, stl)
            return _from_sidecar(given)
        beside = stl.with_name(stl.stem + SIDECAR)
        if beside.is_file():
            return _from_sidecar(beside)
        run = run_folder(stl)
        if run is not None:
            return _from_run(run, stl)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        where = given or stl
        return PartContext(notes=(f"{where} couldn't be read as a part's context: "
                                  f"{type(exc).__name__}: {exc}"[:LINE_CHARS],))
    return PartContext()


def refuses(context: PartContext | None, key: str, value: str) -> str:
    """Why a proposed `key=value` is refused for this part's context, or "": a part with a fit
    never gets a random seam, whichever path proposes it (the single call or the print loop)."""
    if context is None or not context.fits:
        return ""
    if key == "seam_position" and value.strip() == "random":
        return ("the part has a fit, and a random seam scatters seam bumps all round it: "
                + "; ".join(f.line() for f in context.fits))
    return ""


def run_folder(stl: Path) -> Path | None:
    """The design run folder the STL sits in: the nearest above it with `state.json` and
    `needs.json`, or None."""
    for folder in stl.resolve().parents:
        if (folder / "state.json").is_file() and (folder / "needs.json").is_file():
            return folder
    return None


def _from_sidecar(path: Path) -> PartContext:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("a sidecar is a JSON object: fits, seen, purpose, down, holds")
    unknown = sorted(set(data) - {"fits", "seen", "purpose", "down", "holds", "about"})
    if unknown:
        raise ValueError(
            f"a sidecar has fits, seen, purpose, down, holds and about; not {unknown}")
    purpose, loaded, unread = _purpose(data.get("purpose"))
    fits = []
    for n, entry in enumerate(data.get("fits") or (), 1):
        name = str(entry.get("name") or f"fit {n}")
        low, high = _play(entry.get("play"))
        box: Box | None = None
        if "box" in entry:
            box = _box(entry["box"])
            derived = f"at the box the sidecar gives ({_box_said(box)})"
        elif "z" in entry:
            z0, z1 = sorted(float(v) for v in entry["z"])
            box = ((-1e9, -1e9, z0), (1e9, 1e9, z1))
            derived = f"at z {z0:g} to {z1:g} mm as the file lies, as the sidecar gives it"
        else:
            derived = "the sidecar gives no place, so the whole wall is held as the fit"
        fits.append(Fit(name=name, mate=str(entry.get("mate") or ""), play_min=low,
                        play_max=high, box=box, derived=derived))
    holds = []
    for n, entry in enumerate(data.get("holds") or (), 1):
        way = str(entry.get("way") or "")
        if way and way not in SIDES:
            raise ValueError(f"a hold's way is one of {', '.join(SIDES)}, not {way!r}")
        holds.append(Hold(name=str(entry.get("name") or f"hold {n}"),
                          mate=str(entry.get("mate") or ""), way=way))
    down = str(data.get("down") or "")
    if down and down not in SIDES:
        raise ValueError(f"down is one of {', '.join(SIDES)}, not {down!r}")
    return PartContext(
        source=f"the sidecar {path.name}", path=str(path.resolve()), read=(str(path),),
        fits=tuple(fits),
        holds=tuple(holds), seen=_seen(data.get("seen")), down=down,
        down_from="the sidecar" if down else "",
        purpose=purpose, loaded=loaded,
        about=plain_line(data.get("about") or ""),
        notes=(unread,) if unread else (),
    )


def _purpose(value: Any) -> tuple[str, str, str]:
    """The sidecar's purpose: its words, how it's loaded, and why that couldn't be read. Words
    alone are never read as a load (mqucifer/print-shop#236). One that can't be read raises
    nothing: the fits and the side beside it hold guards of their own."""
    if value is None:
        return "", "", ""
    if isinstance(value, str):
        return plain_line(value), "", ""
    if not isinstance(value, dict):
        return (plain_line(json.dumps(value)), "",
                'its purpose isn\'t {"for": ..., "loaded": ...}, so purpose is asked')
    words, loaded = plain_line(value.get("for") or ""), plain_line(value.get("loaded") or "")
    known = next((load for load in LOADS if load.casefold() == loaded.casefold()), "")
    strange = sorted(str(key) for key in set(value) - {"for", "loaded"})
    if not strange and (known or not loaded):
        return words, known, ""
    as_given = "; ".join(f"{plain_line(key)}: {plain_line(said)}" for key, said in value.items())
    why = (f"a purpose has for and loaded, not {strange}" if strange
           else "a purpose's loaded is one of " + "; ".join(f"'{load}'" for load in LOADS)
                + f": not '{loaded}'")
    return as_given, "", f"{why}, so purpose is asked"


def plain_line(value: Any) -> str:
    """A sender's words as one printable line: they're shown where the person confirms, so
    they can't draw a line of their own there."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(value)).split())


def _play(value: Any) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        return float(value), float(value)
    low, high = sorted(float(v) for v in value)
    return low, high


def _seen(value: Any) -> Seen | None:
    """The sidecar's seen faces: a list of walls, top and bottom, as the file lies."""
    if value is None:
        return None
    faces = [value] if isinstance(value, str) else list(value)
    wrong = [f for f in faces if f not in ("walls", "top", "bottom")]
    if wrong:
        raise ValueError(f"seen lists walls, top and bottom, not {wrong}")
    said = ", ".join(faces) if faces else "nothing"
    return Seen(walls="walls" in faces, top="top" in faces, bottom="bottom" in faces,
                said=f"{said}, as the part's context says", noted=True)


def _box(value: Any) -> Box:
    flat = [float(v) for v in np.asarray(value, dtype=float).ravel()]
    if len(flat) != 6:
        raise ValueError(f"a box is two corners, six numbers: {value!r}")
    low = tuple(min(a, b) for a, b in zip(flat[:3], flat[3:], strict=True))
    high = tuple(max(a, b) for a, b in zip(flat[:3], flat[3:], strict=True))
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def _box_said(box: Box) -> str:
    def axis(i: int, name: str) -> str:
        lo, hi = box[0][i], box[1][i]
        return "" if lo <= -1e8 and hi >= 1e8 else f"{name} {lo:g} to {hi:g}"

    return ", ".join(s for s in (axis(0, "x"), axis(1, "y"), axis(2, "z")) if s) + " mm"


def stem_of(name: str) -> str:
    """A file's stem, as the run names its parts: `group__lid.stl` and `parts/lid.step` are
    `lid`."""
    return Path(Path(name).name.rsplit("__", 1)[-1]).stem


def _from_run(run: Path, stl: Path) -> PartContext:
    part = stem_of(stl.name)
    laid = LAID in stl.resolve().parts
    raw = json.loads((run / "needs.json").read_text(encoding="utf-8"))
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    read = [f"{run / 'needs.json'}: {len(entries)} settled needs"]
    notes: list[str] = []

    own_box = _mesh_box(stl)
    down, down_from = "", ""
    for need in entries:
        if need.get("by") == "bed" and stem_of(str(need.get("file") or "")) == part:
            side = str(need.get("side") or "")
            if side in SIDES:
                down, down_from = side, f"the run's need `{need.get('id')}`"
    fits: list[Fit] = []
    holds: list[Hold] = []
    for need in entries:
        files = _files(need)
        if need.get("by") == "bed" or part not in (stem_of(f) for f in files):
            continue
        argv = _argv(need)
        if argv[:1] != ["clearance"]:
            continue
        mates = [f for f in files if stem_of(f) != part]
        mate = mates[0] if mates else ""
        if "--slide" in argv:
            way = _flag(argv, "--slide")
            holds.append(Hold(name=str(need.get("id") or ""), mate=mate,
                              way=way if way in SIDES else ""))
            continue
        low, high = _need_play(need, argv)
        if (high if high is not None else low) is None or (high or low or 0) > FIT_MM:
            continue  # room to keep apart, never a fit
        fits.append(_fit(run, need, argv, mate, low, high, own_box, laid, state, down))

    if laid:
        # The refinement pass laid this file on its side: the run's coordinates are turned.
        fits = [f.model_copy(update={"box": _turned_box(f.box, down)}) for f in fits]
        holds = [h.model_copy(update={"way": _turned_way(h.way, down)}) for h in holds]
        if down:
            down_from = f"{down_from}, and the file is already laid on it"
            down = "-z"
    facts_read = _facts(state, part, [f.mate for f in fits] + [h.mate for h in holds])
    read.append(f"{run / 'state.json'}: {len(state.get('facts') or {})} facts, "
                f"{len(facts_read)} on this part or its mates")
    findings, findings_from = _findings(run, part)
    if findings_from is not None:
        read.append(f"{findings_from}: {len(findings)} findings on this part")
    else:
        notes.append("the run has no refinement findings")
    if not fits and not holds and not down:
        notes.append(f"no need of the run names `{part}`: its fits, holds and side aren't known")
    return PartContext(
        source=f"the design run `{run.name}`, part `{part}`", path=str(run.resolve()),
        read=tuple(read),
        fits=tuple(fits), holds=tuple(holds), down=down, down_from=down_from,
        facts=tuple(facts_read), findings=tuple(findings), notes=tuple(notes),
    )


def _argv(need: dict[str, Any]) -> list[str]:
    try:
        return shlex.split(str(need.get("command") or ""))
    except ValueError:
        return []


def _files(need: dict[str, Any]) -> list[str]:
    geometry = (".step", ".stp", ".stl", ".brep")
    return [w for w in _argv(need) if not w.startswith("-") and w.lower().endswith(geometry)]


def _flag(argv: list[str], flag: str) -> str:
    for n, word in enumerate(argv):
        if word == flag and n + 1 < len(argv):
            return argv[n + 1]
        if word.startswith(flag + "="):
            return word.split("=", 1)[1]
    return ""


def _need_play(need: dict[str, Any], argv: list[str]) -> tuple[float | None, float | None]:
    """The need's play: its own limits on a gap, else the command's `--min-gap` and `--max-gap`."""

    def num(value: Any) -> float | None:
        try:
            return None if value in (None, "") else float(value)
        except (TypeError, ValueError):
            return None

    if "gap" in str(need.get("number") or ""):
        low, high = num(need.get("at_least")), num(need.get("at_most"))
        if low is not None or high is not None:
            return low, high
    return num(_flag(argv, "--min-gap")), num(_flag(argv, "--max-gap"))


def _fit(
    run: Path, need: dict[str, Any], argv: list[str], mate: str, low: float | None,
    high: float | None, own: Box | None, laid: bool, state: dict[str, Any], down: str,
) -> Fit:
    """A fit need, its place derived from geometry, in the run's coordinates."""
    name = str(need.get("id") or "fit")
    margin = max(v for v in (high, low, 0.0) if v is not None) + 0.05
    # The part's own box in the run's coordinates: a laid file is turned back first.
    part = _turned_box(own, down, back=True) if laid and own is not None else own
    place = _flag(argv, "--box")
    if place:
        try:
            box = _box(place.split("=", 1)[-1].split(","))
        except ValueError:
            box = None
        if box is not None:
            return Fit(name=name, mate=mate, play_min=low, play_max=high,
                       box=_meet(part, box, 0.0) or box,
                       derived=f"derived from the need's place `{place}`")
    mate_box, where = _mate_box(run, mate, state)
    if part is not None and mate_box is not None:
        met = _meet(part, mate_box, margin)
        if met is not None:
            return Fit(name=name, mate=mate, play_min=low, play_max=high, box=met,
                       derived=f"derived from where this part's box meets {where}")
    why = ("no mesh or fact gives the mate's box" if mate_box is None
           else "the part's box and its mate's don't meet")
    return Fit(name=name, mate=mate, play_min=low, play_max=high, box=None,
               derived=f"its place couldn't be derived ({why}), so the whole wall is held as "
                       "the fit")


def _meet(a: Box | None, b: Box, margin: float) -> Box | None:
    """Where two boxes meet, each grown by `margin` (parts that fit stand the play apart)."""
    if a is None:
        return None
    low = [max(a[0][i], b[0][i] - margin) for i in range(3)]
    high = [min(a[1][i], b[1][i] + margin) for i in range(3)]
    if any(lo >= hi for lo, hi in zip(low, high, strict=True)):
        return None
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def _mesh_box(path: Path) -> Box | None:
    try:
        import trimesh  # noqa: PLC0415 - only a run's part is measured

        mesh = trimesh.load(path, force="mesh")
        low, high = np.asarray(mesh.bounds, dtype=float)
    except (OSError, ValueError, TypeError, AttributeError, IndexError):
        return None
    return (float(low[0]), float(low[1]), float(low[2])), (
        float(high[0]), float(high[1]), float(high[2]))


def _mate_box(run: Path, mate: str, state: dict[str, Any]) -> tuple[Box | None, str]:
    """The mate's box in the run's coordinates: its mesh in the run (the newest), else its fact's
    `box`. With where it came from."""
    if not mate:
        return None, ""
    want = stem_of(mate)
    meshes = []
    for root, folders, files in os.walk(run, followlinks=False):
        folders[:] = [f for f in folders if f not in NOT_MATES]
        meshes += [Path(root) / f for f in files
                   if Path(f).suffix.lower() in MESHES and stem_of(f) == want]
    found = sorted(meshes, key=lambda p: p.stat().st_mtime, reverse=True)
    for path in found:
        box = _mesh_box(path)
        if box is not None:
            return box, f"its mate's, from {path.relative_to(run)}"
    fact = (state.get("facts") or {}).get(mate) or next(
        (f for f in (state.get("facts") or {}).values()
         if isinstance(f, dict) and stem_of(str(f.get("name") or "")) == want), None)
    if isinstance(fact, dict) and fact.get("box"):
        numbers = re.findall(r"-?\d+(?:\.\d+)?", str(fact["box"]))
        if len(numbers) == 6:
            return _box(numbers), f"its mate's, from the fact `{fact.get('name', mate)}`'s box"
    return None, ""


def _facts(state: dict[str, Any], part: str, mates: Iterable[str]) -> list[str]:
    """The run's facts on this part or its mates, each a line."""
    names = {part, *(stem_of(m) for m in mates if m)}
    found = []
    for fact in (state.get("facts") or {}).values():
        if not isinstance(fact, dict):
            continue
        name = str(fact.get("name") or "")
        text = str(fact.get("text") or "")
        if stem_of(name) in names or any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
            box = f" box {fact['box']};" if fact.get("box") else ""
            found.append(f"`{name}`:{box} {text}"[:LINE_CHARS])
    return found[:CARRIED]


def _findings(run: Path, part: str) -> tuple[list[str], Path | None]:
    """The latest refinement's findings on this part, each a line, and the file read."""
    folder = run / "refinements"
    passes = sorted((p for p in folder.iterdir() if p.name.isdigit()),
                    key=lambda p: int(p.name)) if folder.is_dir() else []
    for one in reversed(passes):
        path = one / "findings.json"
        if not path.is_file():
            continue
        found = []
        for f in json.loads(path.read_text(encoding="utf-8")):
            if not isinstance(f, dict) or stem_of(str(f.get("file") or "")) != part:
                continue
            where = ", ".join(s for s in (
                f"z {f['z_from']:g}-{f['z_to']:g}" if f.get("z_from") is not None
                and f.get("z_to") is not None else "",
                f"plate `{f['plate']}`" if f.get("plate") else "") if s)
            change = "; needs a change to the part" if f.get("needs_change") else ""
            found.append(f"{f.get('kind')}: {f.get('read')} ({where}). For the print: "
                         f"{f.get('means')}{change}"[:LINE_CHARS])
        return found[:CARRIED], path
    return [], None


# --------------------------------------------------------------------------
# Turning it
# --------------------------------------------------------------------------


def _laying(side: str) -> np.ndarray:
    """The turn the bench lays a file on `side` with (`ON_BED`), as a 3x3 matrix."""
    axis, degrees = ON_BED.get(side, ON_BED["-z"])
    return _axis_angle(np.asarray(axis, dtype=float), degrees)


def _axis_angle(axis: np.ndarray, degrees: float) -> np.ndarray:
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    x, y, z = axis
    return np.array([
        [x * x * (1 - c) + c, x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
        [y * x * (1 - c) + z * s, y * y * (1 - c) + c, y * z * (1 - c) - x * s],
        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, z * z * (1 - c) + c],
    ])


def _corners(box: Box) -> np.ndarray:
    return np.array([[x, y, z] for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1])
                     for z in (box[0][2], box[1][2])], dtype=float)


def _turned_box(box: Box | None, side: str, back: bool = False) -> Box | None:
    if box is None or not side:
        return box
    turn = _laying(side)
    turned = _corners(box) @ (turn if back else turn.T)
    low, high = turned.min(axis=0), turned.max(axis=0)
    return (float(low[0]), float(low[1]), float(low[2])), (
        float(high[0]), float(high[1]), float(high[2]))


def _vector(side: str) -> np.ndarray:
    sign = -1.0 if side[0] == "-" else 1.0
    return np.asarray(sign * np.eye(3)["xyz".index(side[1])], dtype=float)


def _side(vector: np.ndarray) -> str:
    i = int(np.argmax(np.abs(vector)))
    return ("+" if vector[i] > 0 else "-") + "xyz"[i]


def _turned_way(way: str, side: str) -> str:
    if not way or not side:
        return way
    return _side(_laying(side) @ _vector(way))


def _rotation(rotation_deg: tuple[float, float, float]) -> np.ndarray:
    """The print side's own turn (`ingest.orientation`): about X, then Y, then Z."""
    from print_shop.ingest.orientation import _rotation_matrix  # noqa: PLC0415 - one convention

    return np.asarray(_rotation_matrix(rotation_deg))[:3, :3]


def pose_for(side: str) -> tuple[float, float, float] | None:
    """The candidate pose that lays the part on `side` of the file as it lies, or None."""
    from print_shop.ingest.orientation import AXIS_ALIGNED  # noqa: PLC0415

    if side not in SIDES:
        return None
    for rotation in AXIS_ALIGNED:
        if np.allclose(_rotation(rotation) @ _vector(side), (0.0, 0.0, -1.0), atol=1e-6):
            return rotation
    return None


def place(
    context: PartContext, stl: Path | str, rotation_deg: tuple[float, float, float],
) -> Placed:
    """The context in a pose: each fit's heights as it prints, the holds pulled across the
    layers, the seen faces turned, and whether the pose is off the context's side."""
    if not context.found:
        return Placed()
    turn = _rotation(rotation_deg)
    own = _mesh_box(Path(stl))
    if own is not None:
        z = (_corners(own) @ turn.T)[:, 2]
        bottom, height = float(z.min()), float(z.max() - z.min())
    else:
        bottom, height = 0.0, 0.0
    bands = []
    for fit in context.fits:
        box = _meet(own, fit.box, 0.0) if fit.box is not None else None
        if box is None or height <= 0:
            bands.append(FitBand(fit=fit, z_from=0.0, z_to=round(height, 2)))
            continue
        heights = (_corners(box) @ turn.T)[:, 2] - bottom
        z0 = max(0.0, float(heights.min()))
        z1 = min(height, float(heights.max()))
        bands.append(FitBand(fit=fit, z_from=round(z0, 2), z_to=round(max(z0, z1), 2)))
    up = turn.T @ np.array([0.0, 0.0, 1.0])
    across = tuple(h for h in context.holds
                   if h.way and abs(float(_vector(h.way) @ up)) > 0.99)
    off = ""
    kept = pose_for(context.down) if context.down else None
    if kept is not None and not np.allclose(rotation_deg, kept):
        off = (f"the pose turns it off its {context.down} side, which {context.down_from} lays "
               "it on")
    seen = printed(context.seen, rotation_deg) if context.seen is not None else None
    return Placed(fits=tuple(bands), across=across, seen=seen, off_side=off)


def own_sides(seen: Seen, rotation_deg: tuple[float, float, float]) -> tuple[str, ...] | None:
    """The part's own sides (`SIDES`, in the STL's coordinates) that faces named in that pose
    are: the top is the one that's up, the bottom the one on the plate, the walls the other four
    (mqucifer/print-shop#248). None where the pose isn't square on, so they can't be carried."""
    up = _up_side(rotation_deg)
    if up is None:
        return None
    down = _opposite(up)
    return tuple(
        side for side in SIDES
        if (seen.top if side == up else seen.bottom if side == down else seen.walls)
    )


def seen_from(
    sides: tuple[str, ...], rotation_deg: tuple[float, float, float], said: str,
    noted: bool = False, called: str = "", unsaid: tuple[str, ...] = (),
) -> Seen | None:
    """Those sides as the faces they are printed in that pose. A wall is seen if any of the four
    is, and `part_walls` says when it isn't all of them. A side in `unsaid` is never read as
    hidden. None: the pose isn't square on."""
    up = _up_side(rotation_deg)
    if up is None:
        return None
    down = _opposite(up)
    walls = [side in sides for side in SIDES if side not in (up, down)]
    part = any(walls) and not all(walls)
    where = {up: "top", down: "bottom"}
    return Seen(walls=any(walls), top=up in sides, bottom=down in sides, said=said, noted=noted,
                part_walls=part, called=called,
                unsaid=tuple(dict.fromkeys(where.get(side, "walls") for side in unsaid)))


def printed(seen: Seen, rotation_deg: tuple[float, float, float]) -> Seen | None:
    """The faces seen as the file lies, carried into the pose by the part's own sides. None: the
    pose isn't square on, so none is judged."""
    up = _up_side(rotation_deg)
    sides = own_sides(seen, AS_THE_FILE_LIES)
    if up is None or sides is None:
        return None
    if up == "+z":
        return seen
    how = " (turned over)" if up == "-z" else " (on its side)"
    return seen_from(sides, rotation_deg, f"{seen.said}{how}", seen.noted,
                     f"{seen.in_words()}, as the file lays it")


def _up_side(rotation_deg: tuple[float, float, float]) -> str | None:
    """The part's side that's up in that pose, or None where none is square on to the plate."""
    up = _rotation((rotation_deg[0], rotation_deg[1], rotation_deg[2])).T @ np.array(
        [0.0, 0.0, 1.0])
    return _side(up) if float(np.max(np.abs(up))) > 0.999 else None


def _opposite(side: str) -> str:
    return ("-" if side[0] == "+" else "+") + side[1]
