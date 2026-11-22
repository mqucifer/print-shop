"""A stand-in for Orca slicing a plate of several parts (mqucifer/print-shop#109).

It lays made-up boxes out as Orca 2.4.2 was seen to (`~/print-shop/proofs/ux-109/`), and shares
what makes it unsafe to trust: by object it spaces outlines by the radius and counts no brim, it
puts what doesn't fit on `plate_2.gcode` with exit 0, and it writes no pause. `tests/test_plate_
orca.py` holds the real Orca to the same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import trimesh

from print_shop.plate import BY_OBJECT
from print_shop.slicer.orca import PauseAt, PlateObject, PlateSlice, SlicerProfiles
from tests.fakes import CI_PROFILES

BED = 257.0
EDGE = 4.0
LAYER = 0.2
WIDTH = 0.42
CLEARANCE = {
    "extruder_clearance_radius": "65", "extruder_clearance_max_radius": "68",
    "extruder_clearance_height_to_rod": "36", "extruder_clearance_height_to_lid": "90",
}


def profiles(
    folder: Path, process: dict[str, str] | None = None, machine: dict[str, Any] | None = None,
) -> SlicerProfiles:
    """The CI profiles with a made-up head's clearance, and `process` over the process."""
    folder.mkdir(parents=True, exist_ok=True)
    head = {**json.loads(CI_PROFILES.machine.read_text()),
            **(CLEARANCE if machine is None else machine)}
    settings = {**json.loads(CI_PROFILES.process.read_text()), "wall_loops": "2",
                "print_sequence": "by layer", "layer_height": "0.2",
                "initial_layer_print_height": "0.2", "brim_type": "no_brim", "brim_width": "5",
                "seam_position": "aligned", **(process or {})}
    (folder / "machine.json").write_text(json.dumps(head))
    (folder / "process.json").write_text(json.dumps(settings))
    return CI_PROFILES.model_copy(update={"machine": folder / "machine.json",
                                          "process": folder / "process.json"})


def box(path: Path, x: float, y: float, height: float) -> Path:
    """A made-up part: a box on the plate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.creation.box(extents=(x, y, height))
    mesh.apply_translation((0, 0, height / 2))
    mesh.export(path)
    return path


@dataclass(frozen=True)
class _Placed:
    name: str
    x: float
    y: float
    w: float
    d: float
    h: float


@dataclass
class PlateOrca:
    """A `SlicePlate`. `leave_out` drops objects from the G-code; `refuse` exits non-zero;
    `another_plate` always writes a second."""

    leave_out: int = 0
    another_plate: bool = False
    #: A setting and the value it won't slice with, whatever the order.
    wont: tuple[str, str] | None = None
    refuse: tuple[str, ...] = ()  # the sequences it won't slice
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self, objects: list[PlateObject], machine: Path, process: Path, filaments: list[Path],
        outdir: Path, pauses: list[PauseAt] | None = None,
    ) -> PlateSlice:
        head = json.loads(machine.read_text())
        settings = json.loads(process.read_text())
        for filament in filaments:
            settings |= json.loads(filament.read_text())
        self.calls.append({"objects": [o.stl.name for o in objects], "machine": head,
                           "process": settings, "pauses": list(pauses or ())})
        outdir.mkdir(parents=True, exist_ok=True)
        for stale in outdir.glob("plate_*.gcode"):
            stale.unlink()
        sequence = settings.get("print_sequence", "by layer")
        done = PlateSlice(command=["orca"], exit=0, gcode=None, log=outdir / "orca.log",
                          stdout="", stderr="")
        if sequence in self.refuse or (
            self.wont is not None and str(settings.get(self.wont[0])) == self.wont[1]
        ):
            return done.model_copy(update={"exit": 155})
        by_object = sequence == BY_OBJECT
        gap = float(head.get("extruder_clearance_radius", 65)) if by_object else 3.0
        sized = []
        for n, obj in enumerate(objects):
            w, d, h = trimesh.load_mesh(str(obj.stl), force="mesh").extents.tolist()
            sized.append((f"{obj.stl.name}_id_{n}_copy_0", w, d, round(h, 2)))
        if by_object:
            sized.sort(key=lambda o: o[3])
        placed: list[_Placed] = []
        spilled = 0
        x = y = EDGE
        deep = 0.0
        for name, w, d, h in sized:
            if x + w > BED - EDGE:
                x, y, deep = EDGE, y + deep + gap, 0.0
            if x + w > BED - EDGE or y + d > BED - EDGE:
                spilled += 1
                continue
            placed.append(_Placed(name, x + w / 2, y + d / 2, w, d, h))
            x, deep = x + w + gap, max(deep, d)
        placed = placed[: len(placed) - self.leave_out]
        out = outdir / "plate_1.gcode"
        out.write_text(_gcode(placed, {**head, **settings}, by_object, list(pauses or ())))
        if spilled or self.another_plate:
            (outdir / "plate_2.gcode").write_text("; what didn't fit\n")
        return done.model_copy(update={"gcode": out})


def _square(x: float, y: float, w: float, d: float, z: float) -> list[str]:
    x0, x1, y0, y1 = x - w / 2, x + w / 2, y - d / 2, y + d / 2
    lines = [f"G1 X{x0:.3f} Y{y0:.3f} Z{z:.2f} F30000", "G1 F3000"]
    return lines + [f"G1 X{a:.3f} Y{b:.3f} E.5"
                    for a, b in ((x1, y0), (x1, y1), (x0, y1), (x0, y0))]


def _gcode(
    placed: list[_Placed], config: dict[str, Any], by_object: bool, pauses: list[PauseAt],
) -> str:
    def layers(h: float) -> list[float]:
        return [round(LAYER * n, 2) for n in range(1, round(h / LAYER) + 1)]

    if by_object:
        laid = [(z, [p]) for p in placed for z in layers(p.h)]
    else:
        tops = sorted({z for p in placed for z in layers(p.h)})
        laid = [(z, [p for p in placed if z <= p.h + 1e-6]) for z in tops]
    tallest = max((p.h for p in placed), default=0.0)
    body = [f"; total layer number: {len(laid)}", f"; max_z_height: {tallest:.2f}", "M83", "G90",
            "G1 X128 Y-1 Z0.6 F20000"]
    for p in placed:
        a, b, c, d = p.x - p.w / 2, p.x + p.w / 2, p.y - p.d / 2, p.y + p.d / 2
        body.append(f"EXCLUDE_OBJECT_DEFINE NAME={p.name} CENTER={p.x},{p.y} "
                    f"POLYGON=[[{a},{c}],[{b},{c}],[{b},{d}],[{a},{d}],[{a},{c}]]")
    brim = float(config.get("brim_width", 0)) if config.get("brim_type") == "outer_only" else 0.0
    before: _Placed | None = None
    for z, here in laid:
        if by_object and before is not None and here[0] is not before:
            body += [f"G1 Z{before.h + 0.8:.2f} F30000",
                     f"G1 X{here[0].x:.3f} Y{here[0].y:.3f} F30000"]
        before = here[0]
        body += [";LAYER_CHANGE", f";Z:{z:g}", f";HEIGHT:{LAYER:g}"]
        if not by_object and any(abs(q.print_z - z) < LAYER / 2 for q in pauses):
            body += [";PAUSE_PRINT", "M600"]
        for p in here:
            if brim and z == LAYER:
                body += [";TYPE:Brim", f";WIDTH:{WIDTH}",
                         *_square(p.x, p.y, p.w + 2 * brim, p.d + 2 * brim, z)]
            body += [f"EXCLUDE_OBJECT_START NAME={p.name}", ";TYPE:Outer wall", f";WIDTH:{WIDTH}",
                     *_square(p.x, p.y, p.w - WIDTH, p.d - WIDTH, z),
                     f"EXCLUDE_OBJECT_END NAME={p.name}"]
    body += [";TYPE:Custom", f"G1 Z{max(100.0, tallest + 50):.2f} F20000", "G1 X202 F20000",
             "G1 Y250 F20000", "; filament used [mm] = 900.00",
             "; filament used [cm3] = 2.16", "; total filament used [g] = 2.70",
             f"; estimated printing time (normal mode) = {5 * len(placed)}m 0s",
             "; CONFIG_BLOCK_START"]
    for key, value in sorted(config.items()):
        said = ",".join(map(str, value)) if isinstance(value, list) else str(value)
        body.append(f"; {key} = " + said.replace("\n", "\\n"))
    body.append("; CONFIG_BLOCK_END")
    return "\n".join(body) + "\n"
