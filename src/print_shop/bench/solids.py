"""Each file's solids, read for what they are rather than how they're written: what a pick is held
to where a file's bytes aren't what was picked (mqucifer/print-shop#180).

A pick is held to the files on its sheet by their content hash. On eval-organic-3 the same map
built again wrote `pane_blue.step` and `pane_meadow.step` with another 17th digit in a line's
point, and the pick no longer held. `pieces build` now writes the same bytes, and this is the
second guard, for anything else that writes the same part two ways.

For each solid of each file: its volume, its area, its centre of mass and its box, and each of
its faces' kind, area and centre. Read by OpenCascade, as the bench reads any STEP file
(`shapes.load`); an STL is read by trimesh as one body, its faces its triangles'. The harness
compares two files' readings (`rounds/judge.py`), not this.

One JSON argument, `{"files": [...], "answer": FILE}`, answered as `answers.say` answers: a
frame's faces run to more than the harness keeps of a line.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import answers

MARK = "ROUNDS_SOLIDS "


def _box(shape: Any) -> list[list[float]]:
    """The tightest box round a shape, by its geometry, not its mesh or its tolerances."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return [[x0, y0, z0], [x1, y1, z1]]


def _brep(solid: Any) -> dict[str, Any]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    volume = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid.wrapped, volume)
    surface = GProp_GProps()
    BRepGProp.SurfaceProperties_s(solid.wrapped, surface)
    centre = volume.CentreOfMass()
    faces = []
    for face in solid.Faces():
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face.wrapped, props)
        at = props.CentreOfMass()
        faces.append([face.geomType(), props.Mass(), [at.X(), at.Y(), at.Z()]])
    return {
        "volume": volume.Mass(),
        "area": surface.Mass(),
        "centre": [centre.X(), centre.Y(), centre.Z()],
        "box": _box(solid),
        "faces": faces,
    }


def _mesh(mesh: Any) -> dict[str, Any]:
    low, high = mesh.bounds
    centres = mesh.triangles_center
    return {
        "volume": float(mesh.volume),
        "area": float(mesh.area),
        "centre": [float(v) for v in mesh.center_mass],
        "box": [[float(v) for v in low], [float(v) for v in high]],
        "faces": [
            ["TRIANGLE", float(a), [float(v) for v in c]]
            for a, c in zip(mesh.area_faces, centres, strict=True)
        ],
    }


def read(name: str) -> dict[str, Any]:
    from .shapes import NoAnswer, load

    try:
        part = load(name)
    except (NoAnswer, OSError, ValueError) as exc:
        return {"unread": str(exc)[:400]}
    if part.is_brep:
        return {"solids": [_brep(solid) for solid in part.shape.Solids()]}
    return {"solids": [_mesh(part.fine())]}


def main(argv: list[str] | None = None) -> None:
    request = json.loads((sys.argv[1:] if argv is None else argv)[0])
    answer = {"files": {name: read(name) for name in request["files"]}}
    answers.say(MARK, answer, request)
