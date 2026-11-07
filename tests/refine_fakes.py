"""A bench and an Orca that answer as the real ones do, for the refinement pass's unit tests.

The bench's `refine` helper meshes on the host (an STL copied through, as the image does), its
`pause` answers with a record given by the test, and `cover` finds nothing. The slicer writes a
G-code in Orca 2.4.2's own form (`slicer/gcode.py` lists what's held to it): layers of 0.2 mm, a
wall round each object, a pause where it's asked for, bridge lines over the frame's window from
the layer `closes_at`, and the footer's figures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from print_shop.bench.refine import MARK, ON_BED, bed_contact
from print_shop.rounds.box import Ran
from print_shop.slicer.orca import PauseAt, PlateObject, PlateSlice

#: Where the fake Orca puts every object's lowest corner on the plate.
AT = (100.0, 100.0)


@dataclass
class FakeBox:
    """`pause` answers with `pause_record`; `refine` meshes on the host."""

    pause_record: dict[str, Any] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        assert module == "refine" and where is not None
        request = json.loads(args[0])
        self.calls.append([module, request["request"]])
        if request["request"] == "cover":
            return Ran(0, MARK + json.dumps({"bare": [], "overlaps": [], "layers": 0}), 0.1)
        files = {}
        (where / request["out"]).mkdir(exist_ok=True)
        laid = request.get("on_bed") or {}
        for name in request["files"]:
            mesh: Any = trimesh.load(where / name, force="mesh")
            # Turned as the image turns it, onto the side named (mqucifer/print-shop#66).
            axis, degrees = ON_BED[laid.get(name, "-z")]
            if degrees:
                mesh.apply_transform(
                    trimesh.transformations.rotation_matrix(  # type: ignore[no-untyped-call]
                        np.radians(degrees), axis
                    )
                )
            target = Path(request["out"]) / (Path(name).stem + ".stl")
            mesh.export(where / target)
            low, high = mesh.bounds
            outline = [
                [float(low[0]), float(low[1])],
                [float(high[0]), float(low[1])],
                [float(high[0]), float(high[1])],
                [float(low[0]), float(high[1])],
                [float(low[0]), float(low[1])],
            ]
            files[name] = {
                "linear_mm": request["linear"],
                "angular_rad": request["angular"],
                "meshed": "an STL, copied as it is",
                "stl": str(target),
                "triangles": len(mesh.faces),
                "closed": True,
                "mesh_volume_mm3": float(mesh.volume),
                "mesh_box_mm": [[float(v) for v in low], [float(v) for v in high]],
                "outline_z": float((low[2] + high[2]) / 2),
                "on_bed": laid.get(name, "-z"),
                "bed_contact_mm2": bed_contact(mesh),
                "outline": [{"outer": outline, "holes": [], "area": 1.0}],
            }
        return Ran(0, MARK + json.dumps({"files": files}), 0.1)

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        assert where is not None
        self.calls.append(args)
        out = where / args[1]
        out.mkdir(exist_ok=True)
        (out / "pause-1.json").write_text(json.dumps(self.pause_record), encoding="utf-8")
        (out / "pause-1.txt").write_text("", encoding="utf-8")
        said = f"verdict: {self.pause_record.get('verdict')}: {self.pause_record.get('why')}"
        return Ran(0, f"{said}\nsaved: {args[1]}/pause-1.txt and .json\n", 0.1)


def pause_record(after: int, room: float, ceiling: float, verdict: str = "OK") -> dict[str, Any]:
    """`pause`'s record for one opening, as `fabrication.run_pause` writes it."""
    top = round(0.2 + 0.2 * (after - 1), 4)
    pane_top = round(top - room, 4)
    return {
        "verdict": verdict,
        "why": f"pause after layer {after}, at z={top}",
        "numbers": {
            "openings": [
                {
                    "opening": "1",
                    "islands": 0,
                    "pocket_floor_z": 1.5,
                    "pocket_ceiling_z": ceiling,
                    "pause_after_layer": after,
                    "pause_after_layer_faces": after,
                    "pause_top_z": top,
                    "pane_top_z": pane_top,
                    "room_over_pane_mm": room,
                    "nozzle_over_pane_mm": round(room + 0.2, 4),
                    "pocket_height_mm": round(ceiling - 1.5, 4),
                }
            ],
        },
    }


@dataclass
class FakeOrca:
    """Orca's CLI, writing G-code in its form. The frame's window is closed from `closes_at`."""

    closes_at: int = 14
    exit: int = 0
    plates: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        objects: list[PlateObject],
        machine: Path,
        process: Path,
        filaments: list[Path],
        outdir: Path,
        pauses: list[PauseAt] | None = None,
        **rest: Any,
    ) -> PlateSlice:
        outdir.mkdir(parents=True, exist_ok=True)
        log = outdir / "orca.log"
        log.write_text("plate 1: found NON_CRITICAL slicing warnings: a made-up warning\n")
        self.plates.append({"objects": objects, "pauses": pauses, "filaments": filaments})
        if self.exit:
            return PlateSlice(
                command=["orca"], exit=self.exit, gcode=None, log=log, stdout="", stderr="refused"
            )
        meshes = [(o, trimesh.load(o.stl, force="mesh")) for o in objects]
        top = max(float(m.bounds[1][2]) for _, m in meshes)
        lines = ["; HEADER_BLOCK_START", "; HEADER_BLOCK_END", "M83"]
        names = []
        for k, (o, mesh) in enumerate(meshes):
            name = f"{o.stl.name}_id_{k}_copy_0"
            names.append(name)
            (x0, y0, _), (x1, y1, _) = mesh.bounds
            w, h = x1 - x0, y1 - y0
            poly = [
                [AT[0], AT[1]],
                [AT[0] + w, AT[1]],
                [AT[0] + w, AT[1] + h],
                [AT[0], AT[1] + h],
                [AT[0], AT[1]],
            ]
            lines.append(f"EXCLUDE_OBJECT_DEFINE NAME={name} CENTER=0,0 POLYGON={json.dumps(poly)}")
        layers = round(top / 0.2)
        before = {round(p.print_z / 0.2) for p in pauses or []}
        for n in range(1, layers + 1):
            lines += [";LAYER_CHANGE", f";Z:{0.2 * n:.1f}", ";HEIGHT:0.2"]
            if n in before:
                lines += [";PAUSE_PRINT", "M600"]
            for k, (_, mesh) in enumerate(meshes):
                (x0, y0, _), (x1, y1, _) = mesh.bounds
                ax, ay = AT[0] + 0.21, AT[1] + 0.21
                bx, by = AT[0] + (x1 - x0) - 0.21, AT[1] + (y1 - y0) - 0.21
                lines += [
                    f"EXCLUDE_OBJECT_START NAME={names[k]}",
                    ";TYPE:Outer wall",
                    ";WIDTH:0.42",
                    f"G1 X{ax:.3f} Y{ay:.3f}",
                    f"G1 X{bx:.3f} Y{ay:.3f} E1",
                    f"G1 X{bx:.3f} Y{by:.3f} E1",
                    f"G1 X{ax:.3f} Y{by:.3f} E1",
                    f"G1 X{ax:.3f} Y{ay:.3f} E1",
                ]
                if k == 0 and n >= self.closes_at and len(objects) == 1:
                    mid = AT[1] + (y1 - y0) / 2
                    lines += [
                        ";TYPE:Bridge",
                        ";WIDTH:0.45",
                        f"G1 X{AT[0] + 2:.3f} Y{mid:.3f}",
                        f"G1 X{AT[0] + (x1 - x0) - 2:.3f} Y{mid:.3f} E2",
                    ]
                if k == 0 and n == 3:
                    lines += [
                        ";TYPE:Gap infill",
                        ";WIDTH:0.23",
                        f"G1 X{ax:.3f} Y{ay + 1:.3f}",
                        f"G1 X{ax + 5:.3f} Y{ay + 1:.3f} E0.1",
                    ]
                lines.append(f"EXCLUDE_OBJECT_END NAME={names[k]}")
        grams = ", ".join("3.3" for _ in filaments)
        lines += [
            "; filament used [mm] = " + ", ".join("1100" for _ in filaments),
            "; filament used [g] = " + grams,
            "; total filament change = 0",
            "; estimated printing time (normal mode) = 11m 30s",
            "; CONFIG_BLOCK_START",
            "; layer_height = 0.2",
            "; CONFIG_BLOCK_END",
        ]
        gcode = outdir / "plate_1.gcode"
        gcode.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return PlateSlice(command=["orca"], exit=0, gcode=gcode, log=log, stdout="", stderr="")
