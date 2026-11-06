"""A kind's standing checks: for stained glass, `light` and `capture` on every pane at `done` and at
each stop, whatever the needs say, as `thin` reads every file (mqucifer/print-shop#52; the Sponsor,
2026-11-04).

On `eval-unnamed-1` the model set `capture` aside because "the request doesn't ask" about holding,
and planned a solid floor under the pane, so no light passed: the stand-in caught it by reading
the plan. The skill holds every stained-glass piece to both, so the harness runs them, and
`set_aside` refuses them on the model's files. On a given reference, as the "before" state, a
failing check can still be set aside.

**What they run on.** `thin` needs only a file. `capture` reads a pane in its pocket, so it needs
the pocket's height, the play and the pane's thickness, and `light` needs which files are the
frame and which the panes. So a stained-glass `done` declares them, in `glass`: each frame, its
panes and any other part of the frame (`with`), with the pocket's height, the play round a pane
and the pane's thickness. Every file of the part is in it, so no pane is left out. It's the
model's word, shown at the stop beside the readings; a need carries the request's own figures.

**Their limits.** Where a settled need runs the same check on that frame (and, for `light`, every
pane of it), the need's reading is the standing check's, at the request's own figures: a rim the
request sizes by share can be under the bench's 0.4 mm, and would fail every such design. Where
none does, `capture` runs at the bench's own least lip, 0.4 mm after play, a nozzle's width, with
`--panes` the panes declared, and `light` with no limit, failing a pane with nothing open straight
through: the kind's rule is that light passes through every pane.
"""

from __future__ import annotations

import math
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.rounds import judge
from print_shop.rounds.needs import UNSURE, Need, Reading

LIGHT = "light"
HOLD = "pane-hold"
#: The checks the harness runs for a kind, by the bench command each is.
CHECKS = {LIGHT: "light", HOLD: "capture"}
SHAPE = (
    "`glass` names each frame and its panes, for the harness's own `light` and `capture`: "
    '[{"frame": "frame.step", "panes": ["pane_1.step", "pane_2.step"], "with": [], '
    '"pocket_z": 2.0, "clearance": 0.25, "pane_thickness": 1.6}]. `pocket_z` is a height '
    "inside the pockets, `clearance` the play round a pane and `pane_thickness` the pane's, each "
    "in mm; `with` is any other part of the frame, such as a cap printed apart. Every file of "
    "`part` is a frame, a pane or `with`; a given file may be one too, as "
    "`references/frame.step`."
)
HEADING = "Every pane, by `light` and `capture`, whatever the needs say:"
KEYS = {"frame", "panes", "with", "pocket_z", "clearance", "pane_thickness"}


@dataclass(frozen=True)
class Glass:
    """One frame, as `done` or `ask` declares it."""

    frame: str
    panes: tuple[str, ...]
    pocket_z: float
    clearance: float
    pane_thickness: float
    others: tuple[str, ...] = field(default=())

    def files(self) -> list[str]:
        return [self.frame, *self.panes, *self.others]

    def plain(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "panes": list(self.panes),
            "with": list(self.others),
            "pocket_z": self.pocket_z,
            "clearance": self.clearance,
            "pane_thickness": self.pane_thickness,
        }

    def commands(self) -> dict[str, str]:
        """Each check's command, by its name."""
        q = shlex.quote
        others = "".join(f" --with {q(name)}" for name in self.others)
        return {
            LIGHT: f"light {q(self.frame)} {' '.join(q(p) for p in self.panes)}{others}",
            HOLD: (
                f"capture {q(self.frame)} --pocket-z {self.pocket_z:g} --clearance "
                f"{self.clearance:g} --pane-thickness {self.pane_thickness:g} --panes "
                f"{len(self.panes)}"
            ),
        }


def load(kept: list[dict[str, Any]] | None) -> list[Glass]:
    """A declaration as `state.json` keeps it."""
    return [
        Glass(
            d["frame"],
            tuple(d["panes"]),
            d["pocket_z"],
            d["clearance"],
            d["pane_thickness"],
            tuple(d.get("with") or ()),
        )
        for d in kept or []
    ]


def _figure(entry: dict[str, Any], key: str, positive: bool = True) -> float | None:
    value = entry.get(key)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    if positive and value <= 0:
        return None
    return float(value)


def read(given: Any, part: list[str], work: Path, whole: bool = True) -> list[Glass] | str:
    """The declaration, or what's wrong with it. Each file is of `part` or under `references/`,
    and with `whole`, as at `done`, every file of `part` is in it."""
    if not isinstance(given, list) or not given:
        return SHAPE
    found: list[Glass] = []
    named: list[str] = []
    for entry in given:
        if not isinstance(entry, dict) or not {"frame", "panes"} <= set(entry) <= KEYS:
            return SHAPE
        frame, panes, others = entry["frame"], entry["panes"], entry.get("with") or []
        if not isinstance(frame, str) or not frame.strip():
            return SHAPE
        if not (isinstance(panes, list) and panes and all(isinstance(p, str) for p in panes)):
            return f"Each frame's `panes` is the list of its pane files. {SHAPE}"
        if not (isinstance(others, list) and all(isinstance(o, str) for o in others)):
            return SHAPE
        figures = {
            "pocket_z": _figure(entry, "pocket_z", positive=False),
            "clearance": _figure(entry, "clearance"),
            "pane_thickness": _figure(entry, "pane_thickness"),
        }
        missing = [k for k, v in figures.items() if v is None]
        if missing:
            said = ", ".join(f"`{k}`" for k in missing)
            return (
                f"`{frame}`: {said} is a number in mm, and the play and thickness more than 0. "
                + SHAPE
            )
        for name in [frame, *panes, *others]:
            if name in named:
                return f"`{name}` is named twice in `glass`: each file is one frame, pane or `with`"
            if name not in part and not (
                name.startswith("references/") and (work / name).is_file()
            ):
                return f"`{name}` in `glass` isn't a file of `part` or a given reference. {SHAPE}"
            named.append(name)
        found.append(
            Glass(
                frame,
                tuple(panes),
                figures["pocket_z"] or 0.0,
                figures["clearance"] or 0.0,
                figures["pane_thickness"] or 0.0,
                tuple(others),
            )
        )
    left = [name for name in part if name not in named]
    if whole and left:
        said = ", ".join(f"`{name}`" for name in left)
        return (
            f"`glass` leaves out {said}: every file of `part` is a frame, a pane or `with`, so "
            f"`light` and `capture` read every pane. {SHAPE}"
        )
    return found


def _as_a_need(
    name: str, one: Glass, ran: list[tuple[Need, Reading]]
) -> tuple[Need, Reading] | None:
    """A settled need that runs this check on this frame, and on every pane of it: its reading is
    the standing check's, at the request's own figures. A rim sized by the request's share can be
    under the bench's 0.4 mm, which would fail every such design."""
    for need, reading in ran:
        files = need.files()
        words = need.argv()
        if not words or words[0] != CHECKS[name] or not files or files[0] != one.frame:
            continue
        if name == LIGHT and not set(one.panes) <= set(files[1:]):
            continue
        return need, reading
    return None


def check(
    box: Any,
    glass: list[Glass],
    where: Path,
    kept: Path,
    ran: list[tuple[Need, Reading]] | None = None,
) -> list[judge.Standing]:
    """`light` and `capture` on each frame and its panes, each in a fresh container, or as a
    settled need already read it (`ran`)."""
    found = []
    for one in glass:
        for name, command in one.commands().items():
            mine = _as_a_need(name, one, ran or [])
            if mine is not None:
                need, reading = mine
                unsure = reading.verdict in UNSURE or not reading.verdict
                found.append(
                    judge.Standing(
                        name,
                        one.frame,
                        need.command,
                        reading.passed,
                        unsure and not reading.passed,
                        reading.verdict,
                        f"as the need `{need.id}` reads it: {reading.why}",
                        dict((reading.record or {}).get("numbers") or {}),
                    )
                )
                continue
            need = Need(
                f"{name}-{judge.NOT_IN_A_NAME.sub('_', one.frame)}"[:80],
                "the kind's standing check",
                "derived",
                "bench",
                command=command,
                verdicts=["OK"],
            )
            reading = judge.read_need(box, need, where, kept)
            unsure = reading.verdict in UNSURE or reading.exit in (2, 3) or not reading.verdict
            said = str((reading.record or {}).get("why") or "")
            why = f"{reading.verdict}: {said}" if reading.verdict and said else reading.why
            numbers = dict((reading.record or {}).get("numbers") or {})
            passed = reading.passed
            if name == LIGHT and passed:
                least = numbers.get("least_open_pct")
                if isinstance(least, int | float) and least <= 0:
                    passed = False
                    pane = numbers.get("least_open_file") or "a pane"
                    why = (
                        f"no light passes through {pane}: nothing of it is open straight through "
                        "the frame"
                    )
            found.append(
                judge.Standing(
                    name,
                    one.frame,
                    command,
                    passed,
                    unsure and not passed,
                    reading.verdict,
                    why,
                    numbers,
                )
            )
    return found


def said(found: list[judge.Standing]) -> list[str]:
    """Each reading, a line each, as `done`'s reply and the stop give them."""
    lines = []
    for one in found:
        mark = "ok" if one.passed else "can't tell" if one.unsure else "FAIL"
        lines.append(f"- `{one.command}`, {mark}: {one.why}")
    return lines


# Curves stored as flat strips (mqucifer/print-shop#59)

#: The check's name in the ledger's `standing` table.
CURVES = "curves"
#: A file with this many flat faces under 1 mm across (`info`'s `small_flat_faces`) has curves,
#: stored as flat strips, and fails. A part's curves are read from its faces, not from its words:
#: built as curves they're curved faces, which `info` doesn't count, and built from a polygon
#: they're a run of thin flat faces, one for each segment. Measured with `info` in the bench's
#: image on copies (`~/print-shop/proofs/m18-stops/curves.json`): `suncatcher-3`'s frame has
#: 1,413 of 6,069 flat faces under 1 mm, and none curved, and two of its panes 33 and 153; batch
#: 3's arcs frame has 1 of 54, and its rings frame 0 of 134, each with true circles; all 50 STEP
#: files of batch 3's approved folders pass, the most having 4 (`curves_batch3_approved.json`).
#: One small round hole cut from a polygon is enough: at 16 segments a quarter, a 3 mm radius is
#: 64 faces of 0.29 mm. A part with no curves has a few, at a corner or a sliver, and its reason is said with
#: `set_aside`.
STRIPS_AT = 20
#: What a curved part's file can be read by: an STL has no faces of its own.
FACED = (".step", ".stp", ".brep")
CURVES_HEADING = "Curves stored as flat strips, by `info`'s flat faces under 1 mm, on every file:"


def curves(
    box: Any, files: list[str], where: Path, kept: Path, asides: dict[str, str]
) -> list[judge.Standing]:
    """`info` on each file, each in a fresh container, failing one with `STRIPS_AT` or more flat
    faces under 1 mm across, unless the model set it aside with its reason (`asides`, by file).
    On `suncatcher-3` `info` said so at five calls, naming the skill the model had read, and the
    frame stayed 23 MB of strips: a reading it can pass over was passed over every time."""
    found = []
    for name in files:
        command = f"info {shlex.quote(name)}"
        if not name.lower().endswith(FACED):
            found.append(
                judge.Standing(
                    CURVES, name, command, True, False, "", "an STL has no faces of its own"
                )
            )
            continue
        need = Need(
            f"curves-{judge.NOT_IN_A_NAME.sub('_', name)}"[:80],
            "curves built as curves",
            "derived",
            "bench",
            command=command,
        )
        reading = judge.read_need(box, need, where, kept)
        numbers = dict((reading.record or {}).get("numbers") or {})
        small, flat = numbers.get("small_flat_faces"), numbers.get("flat_faces")
        if isinstance(small, bool) or not isinstance(small, int):
            why = f"no count of small flat faces came back: {reading.why}"
            found.append(
                judge.Standing(CURVES, name, command, False, True, reading.verdict, why, numbers)
            )
            continue
        of = f" of {flat}" if isinstance(flat, int) else ""
        said = f"{small}{of} flat faces under 1 mm across"
        if small < STRIPS_AT:
            found.append(
                judge.Standing(CURVES, name, command, True, False, reading.verdict, said, numbers)
            )
            continue
        if name in asides:
            why = f"{said}, set aside by the model: {asides[name]}"
            found.append(
                judge.Standing(CURVES, name, command, True, False, reading.verdict, why, numbers)
            )
            continue
        why = (
            f"{said}: its curves are stored as flat strips, a face for each segment of a polygon, "
            f"at {STRIPS_AT} or more. Build each curve as a curve, with CadQuery's circles and "
            "arcs (the skill `building-curves-from-a-layout`), not `polyline` through a polygon's "
            "points. If the file has no curves, say so with `set_aside`, giving `curves` and "
            "`why`: the next stop shows your reason"
        )
        found.append(
            judge.Standing(CURVES, name, command, False, False, reading.verdict, why, numbers)
        )
    return found
