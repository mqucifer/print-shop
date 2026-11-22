"""Several parts arranged on one plate and sliced, by object where the machine's room allows it.

mqucifer/print-shop#109. Orca arranges and slices; what it writes is then read, as its exit 0
doesn't cover three things (Orca 2.4.2, `~/print-shop/proofs/ux-109/`):
- **parts that don't fit go on a second plate,** `plate_2.gcode`, without a word. A job prints
  one file, so every part has to be in `plate_1.gcode`, and there can be no other;
- **by object it spaces the parts by their outlines alone,** so the head's moves are checked
  against the machine's clearances (`clearance.py`), and the parts are spaced wider by what
  they fell short;
- **by object it writes no pause,** so a plate with one prints by layer.

Where by object can't be done, the plate prints by layer and says why. Where it doesn't fit one
plate by layer either, there's no slice.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from print_shop.core.state import SliceResult
from print_shop.plate import BY_LAYER, BY_OBJECT, SEQUENCE, OnPlate, PlateLayout
from print_shop.slicer import gcode
from print_shop.slicer.clearance import Clearance, Hit, Limit, Run, first_hit, runs
from print_shop.slicer.orca import (
    PauseAt,
    PlateObject,
    PlateSlice,
    SlicerProfiles,
    parse_gcode_header,
)

__all__ = ["DoesNotFit", "SlicePlate", "ToPlace", "object_file", "slice_parts"]

#: Over the head's radius between two outlines, in mm: a z-hop's spiral swings the nozzle about a
#: millimetre outside the part it's printing.
SWING_MM = 2.0
#: How many times the parts are spaced wider, and the most one shortfall is answered by spacing,
#: in mm. Past that it's the head's path that's in the way, and no spacing mends it.
TRIES = 4
WIDEN_MOST_MM = 25.0

_NUMBERED = re.compile(r"^part(\d+)_")


class SlicePlate(Protocol):
    """What slices a plate of several files. `orca.slice_plate` is the real one."""

    def __call__(
        self, objects: list[PlateObject], machine: Path, process: Path, filaments: list[Path],
        outdir: Path, pauses: list[PauseAt] | None = None,
    ) -> PlateSlice: ...


class DoesNotFit(RuntimeError):  # noqa: N818 - a refusal, said as what happened
    """The parts can't all be sliced on one plate."""


@dataclass(frozen=True)
class ToPlace:
    """One part as it's sliced: `stl` is named by `object_file`, which is how it's known in the
    G-code."""

    n: int
    name: str
    stl: Path
    height_mm: float


def object_file(n: int, name: str) -> str:
    """A part's file name on the plate. Two files can share a name, and one file come twice, so
    it starts with the part's number."""
    stem = re.sub(r"[^A-Za-z0-9-]+", "-", Path(name).stem).strip("-")[:32]
    return f"part{n}_{stem or 'part'}.stl"


def slice_parts(
    parts: tuple[ToPlace, ...], profiles: SlicerProfiles, outdir: Path, slice_objects: SlicePlate,
) -> tuple[SliceResult, PlateLayout]:
    """The parts on one plate, by object if the process asks for it and it checks out, else by
    layer, with the plate as sliced and why.

    Raises:
        DoesNotFit: If they can't all be sliced on one plate, even by layer.
    """
    machine: dict[str, Any] = json.loads(profiles.machine.read_text())
    process: dict[str, Any] = json.loads(profiles.process.read_text())
    outdir.mkdir(parents=True, exist_ok=True)
    # `gcode_path` is renamed after a slice, so an older one can't be mistaken for this one.
    plate = _Plate(parts, profiles, outdir, slice_objects, machine, process)
    if process.get(SEQUENCE) != BY_OBJECT:
        return plate.by_layer(f"{SEQUENCE} is {process.get(SEQUENCE) or BY_LAYER}")
    if profiles.pauses:
        return plate.by_layer("it pauses, and Orca writes no pause when it prints by object")
    made = plate.by_object()
    return plate.by_layer(made) if isinstance(made, str) else made


class _Plate:
    def __init__(
        self, parts: tuple[ToPlace, ...], profiles: SlicerProfiles, outdir: Path,
        slice_objects: SlicePlate, machine: dict[str, Any], process: dict[str, Any],
    ) -> None:
        self.parts = {p.n: p for p in parts}
        self.profiles = profiles
        self.outdir = outdir
        self.slice_objects = slice_objects
        self.machine = machine
        self.process = process

    def _slice(self, machine: Path, sequence: str) -> PlateSlice:
        process = self.outdir / "process_arranged.json"
        # The objects are told apart in the G-code by these marks, so they're always written.
        process.write_text(json.dumps(
            {**self.process, SEQUENCE: sequence, "exclude_object": "1"}, indent=2))
        filaments = [self.profiles.filament] if self.profiles.filament is not None else []
        return self.slice_objects(
            [PlateObject(stl=p.stl) for p in self.parts.values()], machine, process, filaments,
            self.outdir, list(self.profiles.pauses),
        )

    def _spilled(self) -> bool:
        return any(p.name != "plate_1.gcode" for p in self.outdir.glob("plate_*.gcode"))

    def _numbers(self, names: list[str]) -> dict[str, int] | None:
        """Each object's part number, or None unless each part is there exactly once."""
        found = {}
        for name in names:
            numbered = _NUMBERED.match(name)
            if numbered is None:
                return None
            found[name] = int(numbered.group(1))
        return found if sorted(found.values()) == sorted(self.parts) else None

    def by_object(self) -> tuple[SliceResult, PlateLayout] | str:
        """The plate by object, or why not."""
        room = Clearance.of(self.machine)
        if room is None:
            return ("the machine profile doesn't give the head's clearance (its radius, and the "
                    "heights to the rod and the lid), so by object can't be checked")
        tall = [p for p in self.parts.values() if p.height_mm > room.lid]
        if len(tall) > 1:
            return (f"{' and '.join(p.name for p in tall)} are each over the {room.lid:g} mm to "
                    "the lid, and only the last part printed may be")
        spacing = room.radius + SWING_MM
        why = ""
        for _ in range(TRIES):
            machine = self.outdir / "machine_arranged.json"
            machine.write_text(json.dumps(
                {**self.machine, "extruder_clearance_radius": f"{spacing:g}"}, indent=2))
            done = self._slice(machine, BY_OBJECT)
            if done.gcode is None:
                return f"Orca didn't slice it by object ({done.why})"
            if self._spilled():
                over = [p.name for p in self.parts.values() if p.height_mm > room.rod]
                return (f"they don't fit on one plate with the {spacing:g} mm the head needs "
                        "between them"
                        + (f" ({', '.join(over)}: over the {room.rod:g} mm to the rod, and a part "
                           "that tall needs a row of the plate to itself)" if over else ""))
            sliced = gcode.read(done.gcode, keep_moves=True)
            order = runs(sliced)
            numbers = self._numbers([r.name for r in order]) if order is not None else None
            if order is None or numbers is None:
                return ("the slice doesn't read as each part printed once, one after another, so "
                        "its clearance can't be checked")
            try:
                hit = first_hit(sliced, order, room)
            except ValueError as exc:
                return f"its clearance can't be checked: {exc}"
            if hit is None:
                checked = (
                    f"each part is finished before the next. The head's moves in the G-code "
                    f"were checked against the machine's clearance: {room.radius:g} mm round "
                    f"it, {room.rod:g} mm to the rod, {room.lid:g} mm to the lid"
                )
                return (parse_gcode_header(done.gcode),
                        self._layout(BY_OBJECT, checked, sliced, numbers, order))
            why = self._said(hit, room, numbers)
            if hit.limit is Limit.LID or hit.short > WIDEN_MOST_MM:
                return why
            spacing += math.ceil(hit.short + 0.5)
        return why

    def _said(self, hit: Hit, room: Clearance, numbers: dict[str, int]) -> str:
        part = f"{self.parts[numbers[hit.passed]].name}, {hit.height:g} mm tall"
        if hit.limit is Limit.LID:
            return f"{part}, is over the {room.lid:g} mm to the lid, and isn't the last printed"
        if hit.limit is Limit.ROD:
            return (f"{part}, is over the {room.rod:g} mm to the rod, and what prints after it "
                    f"comes {hit.short:.1f} mm inside the {room.radius:g} mm it needs from it, "
                    "front to back")
        return (f"printing after {part}, the head comes {hit.short:.1f} mm inside the "
                f"{room.radius:g} mm it needs round it")

    def by_layer(self, why: str) -> tuple[SliceResult, PlateLayout]:
        done = self._slice(self.profiles.machine, BY_LAYER)
        if done.gcode is None:
            raise DoesNotFit(
                f"the {len(self.parts)} parts weren't sliced on one plate, by layer either "
                f"({done.why}); by object: {why}"
            )
        sliced = gcode.read(done.gcode)
        laid = sorted({name for layer in sliced.layers
                       for name, length in layer.objects.items() if length > 0})
        numbers = self._numbers(laid)
        if self._spilled() or numbers is None:
            raise DoesNotFit(
                f"the {len(self.parts)} parts don't all fit on one plate, even printed by layer, "
                "and a job prints one plate: give it fewer"
            )
        return parse_gcode_header(done.gcode), self._layout(BY_LAYER, why, sliced, numbers)

    def _layout(
        self, sequence: str, why: str, sliced: gcode.Sliced, numbers: dict[str, int],
        order: tuple[Run, ...] = (),
    ) -> PlateLayout:
        width, depth = self._bed()
        placed = {run.name: (at, run) for at, run in enumerate(order, 1)}
        objects = []
        for name, n in numbers.items():
            outline = sliced.outlines.get(name) or [[0.0, 0.0]]
            xs, ys = [p[0] for p in outline], [p[1] for p in outline]
            x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            at, run = placed.get(name, (None, None))
            objects.append(OnPlate(
                n=n, name=self.parts[n].name, x=round(x, 1), y=round(y, 1),
                where=_where(x / width, y / depth), height_mm=round(self.parts[n].height_mm, 2),
                order=at, layers=(run.first, run.last) if run is not None else None,
            ))
        return PlateLayout(sequence=sequence, why=why,
                           objects=tuple(sorted(objects, key=lambda o: o.n)))

    def _bed(self) -> tuple[float, float]:
        corners = [str(c).split("x") for c in self.machine.get("printable_area", ())]
        width = max((float(c[0]) for c in corners), default=0.0)
        depth = max((float(c[1]) for c in corners), default=0.0)
        return width or 1.0, depth or 1.0


def _where(across: float, back: float) -> str:
    """A place on the plate in words, from the front left: "back left", "centre"."""
    side = "left" if across < 1 / 3 else "right" if across > 2 / 3 else "centre"
    row = "front" if back < 1 / 3 else "back" if back > 2 / 3 else "middle"
    return "centre" if (row, side) == ("middle", "centre") else f"{row} {side}"
