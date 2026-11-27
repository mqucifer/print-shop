"""A part as it's printed, as solids: a skin of walls and shells round an infill core, in cells
of their own settings (mqucifer/print-shop#337).

`load` reads a part the assembly file gives print settings for (`printed.py`) as a solid skin
round a core, each with its own figures (`infill.py`). This makes the core: whatever of the part
is further than its walls from its outline in its own layer, and further than its top shells
below each face that looks the way it stands as it prints (`Settings.up`), and its bottom
shells over each that looks the other way. What's left of the
part is the skin. The mesh then has a face where the skin meets the core, so each element is
skin or core, and none is half of each: a 5 mm element can't tell a 0.9 mm wall from the infill
behind it, and with the skin smeared over the elements it lies in, a bar's section is 19 to 38%
too soft by its own arithmetic.

**The erosion.** A point is in the core where the part holds the point its top shells above it,
the point its bottom shells below it, and every point its walls away round it in its layer's
plane. The shells are the part's common with 2 copies of itself moved along its layers
(`eroded_along`). The walls (mqucifer/print-shop#356, `cut_core`) are OCCT's offset of the part
stretched ten times along its layers, the stretch undone (`offset_in_plane`), round at a
concave edge. Where OCCT can't offset a part, as on a real curved leg, they're its common with
16 copies of itself moved its walls round (`eroded_in_plane`); that common is stepped at each
concave edge, a face of each copy a hundredth to three tenths of a millimetre across, and gmsh
turned elements inside out by them, so where two flat faces meet there the steps are cut out
to one mitred corner (`mitres`), the two moved faces' line.

Each errs a little, and which way matters: a core too small, its skin too thick, reads the
part stronger and stiffer than printed, the unsafe side; one too big reads it weaker. The
offset errs toward a smaller core, by at most about 1.4% of the walls on a face they set, where
its shells are no thinner than a third of them; and so does the mitre, a square for a quarter
disc at a concave corner, 21% of the walls' square at a right angle. The 16 copies err the
other way, a wall between two of them read at least cos(11.25°), 98%, as thick as it is.

Each cut is held to the core each layer has as Orca slices it (`checked`): the part's section,
each face of it offset in by its walls with OCCT's 2D offset on its own, and cut by its
sections its shells away, at 7 of its layers' planes spread over its height and at its own
features' heights, just off each face in its layers' plane and at each concave edge's and
cylinder's middle (`heights`). It's compared as shapes, so a miss isn't made up by an excess
beside it: the core may be short of a layer's by no more than 2% of the walls along the
outline (`SHORT`), and over it by no more than 5% (`AGREE`), with 80% of the planes, and at
least 3, read. It may leave no face of the core narrower than a bead by a curved face
(`slivers`), unless OCCT can merge it into the faces round it (`healed`), when it's held to the
part's shells and checked again. Where neither way holds, each is tried on the part with its
own faces under a bead merged first (`simplified`), its core kept within the part's own shells;
and where none holds, `load` says so, NO ANSWER, and never shows CalculiX's inside-out elements
for a geometry the bench made. Only each point's end is tested along the layers, so a slot
narrower than the shells between a point and the one it tests is passed over, and Orca's own
rules for small areas, such as filling one under `minimum_sparse_infill_area` solid, aren't
read: each leaves the core a little larger, and the part a little softer and weaker than
printed.

**Regions** (`printed.Region`) cut the part into cells, each with the settings every region round
it gives: a height range's first, then each volume's over it in order, the later over the
earlier, as Orca 2.4.2 applies them (`printed.py`). Each cell's walls follow the part's own
outline and holes, as many as its settings give. A volume with walls of its own is printed as an
island, as the print side's slices of Orca 2.4.2 show (mqucifer/print-shop#338): one ring of
wall each side of its shape's faces where they cut the part, besides its own count round the
outline and holes inside it. A height range adds no walls. Every cell's shells are from the
part's own faces, which no region's shape makes.

FreeCAD's Part module, in the second image (`cae.py`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .printed import Cylinder, Layers, Mesh, Printed, Region, Settings
from .report import NoAnswer

#: The directions round each point its walls are tested in: a wall between two of them is read
#: at least cos(180°/16), 98%, as thick as it is.
ROUND = 16
#: Smaller than this, in mm³, a piece of a cut is nothing.
NOTHING = 1e-3
#: How many times a part is stretched along its layers before OCCT offsets it by its walls,
#: and the stretch undone (`offset_in_plane`): the offset, a ball's, is then an ellipsoid's,
#: its walls' thickness across the layers' plane and a tenth of it along them.
STRETCH = 10.0
#: How far the core may be from each layer's own, the part's section offset in by its walls
#: and cut by its shells (`layer_core`), as a share of the walls' thickness along the outline
#: (`checked`, mqucifer/print-shop#356): the area of the core that isn't the layer's, or of
#: the layer's that isn't the core, over the outline's length and the walls. A core too small
#: reads the part stronger than printed, the unsafe side, so `SHORT` is the tighter: the offset
#: leaves at most 1.4% of the walls on a face they set, and a mitre a square for a quarter
#: disc, 21% of the walls' square at a right angle. A core too big reads it weaker: the 16
#: copies leave up to 2% of the walls more.
SHORT = 0.02
AGREE = 0.05
#: How many of the part's layers' planes the cut is checked at, spread over its height, as
#: well as just off each of its own faces that lies in its layers' plane, at each concave
#: edge's middle and each cylinder's (`heights`); at most `PLANES` in all.
HEIGHTS = 7
PLANES = 40
#: How far off a face in the layers' plane its planes are, in mm.
OFF_FACE = 0.05
#: Of the planes where the part has a section, how many must be read, and at least how many.
READ_SHARE, READ_LEAST = 0.8, 3
#: A concave corner further from its edge than this many times its walls isn't mitred: a
#: sharp one's mitre runs far into the part.
MITRE_LIMIT = 3.0


@dataclass(frozen=True)
class Cell:
    """A piece of a part with one set of settings: its name, the settings, and its core."""

    #: The part's own name for the cell: "the part", or the regions round it, in order.
    name: str
    settings: Settings
    #: The regions round it, in order, by name.
    regions: tuple[str, ...]
    #: The core's solids, in the part's own axes: none where the walls and shells fill it.
    core: tuple[Any, ...]
    #: The cell's volume, and its core's, in mm³.
    volume_mm3: float
    core_mm3: float
    #: The core's box in the part's own axes, its least and most corners, or None for none.
    core_box: tuple[tuple[float, ...], tuple[float, ...]] | None = None
    #: How its walls were cut, in words, and its check against each layer's own (`Check`).
    cut: str = ""
    check: Check | None = None

    @property
    def walls_mm(self) -> float:
        s = self.settings
        return wall_thickness(int(s.walls or 0), float(s.line_width or 0),
                              float(s.layer_height or 0))

    @property
    def top_mm(self) -> float:
        return float(self.settings.top or 0) * float(self.settings.layer_height or 0)

    @property
    def bottom_mm(self) -> float:
        return float(self.settings.bottom or 0) * float(self.settings.layer_height or 0)


def wall_thickness(walls: int, width: float, height: float) -> float:
    """How thick a part's walls are, in mm: as many beads' spacing, by Orca 2.4.2's own flow,
    `Flow.cpp`'s "auto out = width - height * float(1. - 0.25 * PI);". That's a bead's plastic
    over its height ("float(m_height * (m_width - m_height * (1. - 0.25 * PI)))", its mm³ per
    mm): its loops are laid that far apart (`PerimeterGenerator.cpp`, "coord_t distance = (i
    == 1) ? ext_perimeter_spacing2 : perimeter_spacing;") and the infill is cut half of it
    inside the last ("perimeter_spacing / 2"). So the walls are no thicker than their plastic,
    and the overlap Orca gives the infill onto them adds infill, not wall."""
    if walls <= 0:
        return 0.0
    return walls * (width - height * (1.0 - math.pi / 4))


def _vector(axis: int, size: float) -> Any:
    import FreeCAD as App

    way = [0.0, 0.0, 0.0]
    way[axis] = size
    return App.Vector(*way)


def _solids(shape: Any) -> list[Any]:
    return [s for s in shape.Solids if s.Volume > NOTHING]


def _one(solids: list[Any]) -> Any | None:
    """Several solids as one shape, or None for none."""
    import Part

    if not solids:
        return None
    return solids[0] if len(solids) == 1 else Part.makeCompound(solids)


def eroded_in_plane(shape: Any, layers: int, by: float) -> Any | None:
    """What of `shape` is at least `by` from its outline in its own layer's plane, square to
    the `layers` axis: its common with each of `ROUND` copies of itself, moved `by` round."""
    import FreeCAD as App

    if by <= 0:
        return shape
    first, second = (a for a in range(3) if a != layers)
    left = shape
    for k in range(ROUND):
        angle = 2 * math.pi * k / ROUND
        move = [0.0, 0.0, 0.0]
        move[first] = by * math.cos(angle)
        move[second] = by * math.sin(angle)
        left = left.common(shape.translated(App.Vector(*move)))
        if left.Volume <= NOTHING:
            return None
    return left


def offset_in_plane(shape: Any, layers: int, by: float) -> Any | None:
    """`shape` grown by `by`, or with `by` under naught eroded, in its layers' plane: OCCT's
    offset of it stretched `STRETCH` times along its `layers` axis, its concave edges rounded
    (mqucifer/print-shop#356), and the stretch undone. A ball's offset of the stretched shape
    is an ellipsoid's of the shape, `by` across the layers' plane and `by / STRETCH` along it:
    a face whose normal is a share a of the plane is moved `by` × √(a² + (1 - a²) / STRETCH²),
    where the walls in that plane move it `by` × a. On a face its shells move further, top and
    bottom, the difference is under theirs; on any other, under 1.4% of `by` where its shells
    are no thinner than a third of its walls: eroding, it errs toward a smaller core, a part
    read a little stronger than printed (`SHORT`). Unlike the 16 copies, it leaves no step at a
    concave edge, and no strip where two copies of a curved face cross. Its flat faces, made
    B-spline by the stretch, are flat again. None where nothing's left; it raises where OCCT
    can't offset the shape."""
    import FreeCAD as App

    scale = [1.0, 1.0, 1.0]
    scale[layers] = STRETCH
    there, back = App.Matrix(), App.Matrix()
    there.scale(*scale)
    back.scale(*(1 / s for s in scale))
    made = shape.transformGeometry(there).makeOffsetShape(by, 1e-4, False, False, 0, 0, False)
    if made.isNull() or not made.Solids:
        return None
    left = _flattened(made.transformGeometry(back))
    if not left.isValid() or left.Volume <= NOTHING:
        return None
    return left


def _flattened(shape: Any) -> Any:
    """`shape` with each face that lies in a plane made on that plane, one solid at a time, and
    its faces in one order, by where each is: OCCT's offset gives them in an order of its own
    that changes from run to run, and gmsh, meshing them in that order, made another mesh each
    time."""
    import Part

    solids = []
    for solid in shape.Solids:
        faces = []
        for face in solid.Faces:
            if type(face.Surface).__name__ != "Plane":
                plane = face.findPlane(1e-7)
                if plane is not None:
                    flat = Part.Face(plane, face.Wires)
                    if flat.isValid():
                        faces.append(flat)
                        continue
            faces.append(face)
        faces.sort(key=_where)
        made = Part.Solid(Part.Shell(faces))
        if made.Volume < 0:
            made.reverse()
        solids.append(made if made.isValid() else solid)
    solids.sort(key=_where)
    return _one(solids) if len(solids) != 1 else solids[0]


def _where(shape: Any) -> tuple[float, ...]:
    """Where a face or solid is, to order them by: its centre and its size, to a micron."""
    centre = shape.CenterOfMass
    size = shape.Volume if shape.ShapeType == "Solid" else shape.Area
    return (round(centre.x, 3), round(centre.y, 3), round(centre.z, 3), round(size, 3))


def width(face: Any) -> float:
    """How wide a face is across, in mm: twice its area over its outline's length, as `load`
    reads a face narrow."""
    length = sum(e.Length for e in face.Edges)
    return 2 * face.Area / length if length > 0 else 0.0


def _span(shape: Any, layers: int) -> tuple[float, float]:
    box = shape.BoundBox
    return ((box.XMin, box.XMax), (box.YMin, box.YMax), (box.ZMin, box.ZMax))[layers]


def _section(shape: Any, layers: int, at: float) -> Any | None:
    """`shape`'s section in its layers' plane at `at` along them, as faces, or None for none."""
    import Part

    wires = shape.slice(_vector(layers, 1.0), at)
    if not wires:
        return None
    return Part.makeFace(wires, "Part::FaceMakerBullseye")


def _moved_to(face: Any, layers: int, at: float) -> Any:
    """A section moved along the layers to `at`."""
    return face.translated(_vector(layers, at - _span(face, layers)[0]))


class _Unread(Exception):
    """A plane OCCT can't offset a face of, though the face isn't thinner than its walls."""


def _collapses(face: Any, layers: int, walls: float) -> bool:
    """Whether a face in the layers' plane is gone, offset in by its walls: its common with
    `ROUND` copies of itself moved its walls round is nothing, as OCCT's 2D offset of a face
    thinner than its walls raises."""
    import FreeCAD as App

    first, second = (a for a in range(3) if a != layers)
    left = face
    for k in range(ROUND):
        move = [0.0, 0.0, 0.0]
        move[first] = walls * math.cos(2 * math.pi * k / ROUND)
        move[second] = walls * math.sin(2 * math.pi * k / ROUND)
        left = left.common(face.translated(App.Vector(*move)))
        if left.Area <= 1e-6:
            return True
    return False


def layer_core(
    part: Any, layers: int, at: float, walls: float, top: float, bottom: float, up: float
) -> tuple[Any | None, float] | None:
    """The core's section at `at` along its layers as Orca slices it: each face of the part's
    own section there offset in by its walls in its plane, OCCT's 2D offset, rounded at its
    concave corners, and cut by the part's sections `top` above and `bottom` below it, which
    its shells need. Each face is offset on its own: OCCT's offset of several fails for all
    where one is thinner than its walls, and that one alone is then gone (mqucifer/print-shop
    #356's review). Its faces, or None where nothing's left, and its outline's length, the
    part's section's, in mm. None where the part has no section there.

    Raises:
        _Unread: Where OCCT can't offset a face that isn't thinner than its walls.
    """
    import Part

    own = _section(part, layers, at)
    if own is None:
        return None
    outline = sum(w.Length for w in own.Wires)
    inner = []
    for face in own.Faces:
        if walls <= 0:
            inner.append(face)
            continue
        try:
            made = face.makeOffset2D(-walls, 0, False, False, False)
        except Exception as exc:  # noqa: BLE001 - OCCT raises where nothing's left of a face
            if _collapses(face, layers, walls):
                continue
            raise _Unread(str(exc)) from exc
        inner.extend(f for f in made.Faces if f.Area > 1e-9)
    left = Part.makeCompound(inner) if inner else None
    for along in (top * up, -bottom * up):
        if along == 0 or left is None:
            continue
        other = _section(part, layers, at + along)
        if other is None:
            left = None
            break
        left = left.common(_moved_to(other, layers, at))
        if left.Area <= 1e-9:
            left = None
    return left, float(outline)


def heights(part: Any, layers: int) -> list[float]:
    """The planes the cut is checked at, along the part's layers (`checked`): `HEIGHTS` spread
    over its height, a little off each even step, so that none is one of a stepped part's
    faces; and `OFF_FACE` either side of each of its faces that lies in its layers' plane, at
    the middle of each concave edge and each cylinder, where a feature lies between them; at
    most `PLANES`, the spread ones first."""
    low, high = _span(part, layers)
    spread = [low + (high - low) * (k + 0.5 + 0.0137) / HEIGHTS for k in range(HEIGHTS)]
    way = _vector(layers, 1.0)
    flat, middles = [], []
    for face in part.Faces:
        kind = type(face.Surface).__name__
        if kind == "Plane":
            normal = face.normalAt(*face.Surface.parameter(face.CenterOfMass))
            if abs(abs(normal.dot(way)) - 1) < 1e-6:
                flat.append(_span(face, layers)[0])
        elif kind == "Cylinder":
            middles.append(sum(_span(face, layers)) / 2)
    for edge, _ in _concave(part):
        lowest, highest = _span(edge, layers)
        # An edge in the layers' plane is a flat face's: the planes off that face read it.
        if highest - lowest > 2 * OFF_FACE:
            middles.append((lowest + highest) / 2)
    features = [h + side for h in flat for side in (-OFF_FACE, OFF_FACE)]
    # None on a flat face itself, where the part's section and the core's are its edge.
    features += [h for h in middles if all(abs(h - f) > OFF_FACE / 2 for f in flat)]
    inside = sorted({round(h, 4) for h in features if low + OFF_FACE / 2 < h < high - OFF_FACE / 2}
                    - {round(h, 4) for h in spread})  # fmt: skip
    room = PLANES - len(spread)
    if len(inside) > room:
        inside = [inside[round(k * (len(inside) - 1) / max(room - 1, 1))] for k in range(room)]
    return sorted(spread + inside)


@dataclass(frozen=True)
class Check:
    """The cut held to each layer's own (`checked`): the planes where the part has a section,
    how many were read, and the most the core was short of a layer's, and over it, each as a
    share of the walls along the outline."""

    planes: int
    read: int
    short: float
    over: float

    def fails(self) -> str | None:
        """Why it doesn't hold, in words, or None where it does."""
        if self.read < max(READ_LEAST, math.ceil(READ_SHARE * self.planes)):
            return (f"only {self.read} of {self.planes} of its layers' planes could be read to "
                    f"check it, under {100 * READ_SHARE:.0f}% or {READ_LEAST}")
        if self.short > SHORT:
            return (f"its core is short of a layer's own outline offset in by its walls by "
                    f"{100 * self.short:.0f}% of them, past {100 * SHORT:.0f}%: the part would "
                    "read stronger than printed")
        if self.over > AGREE:
            return (f"its core is over a layer's own outline offset in by its walls by "
                    f"{100 * self.over:.0f}% of them, past {100 * AGREE:.0f}%")
        return None


def checked(
    part: Any, core: Any | None, layers: int, walls: float, top: float, bottom: float,
    up: float,
) -> Check:
    """The cut held to the core each layer has as Orca slices it (`layer_core`), at each of
    `heights`: the area of the layer's core the cut's doesn't have, and of the cut's the
    layer's doesn't, so that a miss is never made up by an excess beside it."""
    planes = read = 0
    short = over = 0.0
    for at in heights(part, layers):
        try:
            want = layer_core(part, layers, at, walls, top, bottom, up)
        except Exception:  # noqa: BLE001 - a plane OCCT can't section or offset isn't read
            planes += 1
            continue
        if want is None:
            continue
        planes += 1
        shape, outline = want
        try:
            got = _section(core, layers, at) if core is not None else None
            missed = (shape.cut(got).Area if got is not None else shape.Area) if shape else 0.0
            extra = (got.cut(shape).Area if shape is not None else got.Area) if got else 0.0
        except Exception:  # noqa: BLE001
            continue
        read += 1
        if outline > 0 and walls > 0:
            short = max(short, missed / (outline * walls))
            over = max(over, extra / (outline * walls))
    return Check(planes, read, short, over)


def _curved(face: Any) -> bool:
    return type(face.Surface).__name__ != "Plane"


def slivers(core: Any, bead: float) -> list[Any]:
    """The faces of `core` narrower than a bead's width (`width`) that touch a curved face of
    it, or are curved themselves: where gmsh's elements on a real part, a curved furniture
    leg's, were turned inside out (mqucifer/print-shop#356)."""
    found = []
    for face in core.Faces:
        if width(face) >= bead:
            continue
        if _curved(face) or any(
            _curved(other) and not other.isSame(face)
            for edge in face.Edges
            for other in core.ancestorsOfType(edge, type(face))
        ):
            found.append(face)
    return found


def _merged(shape: Any, faces: list[Any]) -> Any | None:
    """`shape` with `faces` merged into the faces round them, OCCT's defeaturing, where it
    leaves one valid solid; or None."""
    try:
        merged = shape.defeaturing(faces)
    except Exception:  # noqa: BLE001 - OCCT raises where it can't merge them
        return None
    if merged.isNull() or not merged.isValid() or len(_solids(merged)) != len(_solids(shape)):
        return None
    return merged


def healed(core: Any, bead: float) -> Any | None:
    """`core` with its slivers (`slivers`) merged into the faces round them, all at once, or
    else one at a time, where OCCT can merge some and not others: where that leaves none, and
    moves it by under `AGREE` of a bead over its faces; or None."""
    narrow = slivers(core, bead)
    if not narrow:
        return core
    merged = _merged(core, narrow)
    if merged is None or slivers(merged, bead):
        # One pass, each sliver tried once, by where it is: a merge makes the faces anew.
        merged, tried = core, set()
        for _ in range(len(narrow)):
            left = [f for f in slivers(merged, bead) if _where(f) not in tried]
            if not left:
                break
            tried.add(_where(left[0]))
            fewer = _merged(merged, [left[0]])
            if fewer is not None and len(slivers(fewer, bead)) < len(slivers(merged, bead)):
                merged = fewer
        if slivers(merged, bead):
            return None
    moved = abs(merged.Volume - core.Volume) / max(core.Area, 1e-9)
    return merged if moved <= AGREE * bead else None


def simplified(part: Any, bead: float) -> tuple[Any, int] | None:
    """`part` with its own faces narrower than a bead merged into the faces round them, to cut
    its core from (mqucifer/print-shop#356): a chamfer or a step under a bead's width, which no
    print lays a wall round, and which OCCT can't offset and the 16 copies make slivers of. The
    shape, and how many faces it merged; None where it has none, or OCCT can't merge them, or
    they move it by more than `AGREE` of a bead over its faces."""
    narrow = [f for f in part.Faces if width(f) < bead]
    if not narrow:
        return None
    merged = _merged(part, narrow)
    if merged is None:
        return None
    moved = abs(merged.Volume - part.Volume) / max(part.Area, 1e-9)
    if moved > AGREE * bead:
        return None
    return merged, len(narrow)


def _concave(shape: Any) -> list[tuple[Any, list[Any]]]:
    """Each edge of `shape` where its two faces meet concave, the part round it more than half
    a turn, with those faces: where a step out of one face and half a step into the other is
    inside it. Smooth edges, their faces tangent, aren't."""
    import Part

    found = []
    for edge in shape.Edges:
        faces = shape.ancestorsOfType(edge, Part.Face)
        if len(faces) != 2:
            continue
        middle = edge.valueAt(sum(edge.ParameterRange) / 2)
        normals = [f.normalAt(*f.Surface.parameter(middle)) for f in faces]
        if normals[0].getAngle(normals[1]) < 0.02:
            continue
        step = 0.01
        if any(shape.isInside(middle + a * step - b * (step / 2), 1e-6, False)
               for a, b in (normals, normals[::-1])):  # fmt: skip
            found.append((edge, faces))
    return found


def mitres(part: Any, layers: int, by: float) -> tuple[list[Any], int]:
    """What the 16 copies leave at each of `part`'s concave edges where two flat faces meet
    across its layers, to take out of their core (mqucifer/print-shop#356). The copies' common
    is stepped there, a face from each copy, a hundredth to three tenths of a millimetre
    across, and gmsh turned elements inside out by them on a real part. Each face moves in by
    its walls as the copies move it, the most any copy reaches along its normal; and the
    prism between the two faces and the two moved in, along the edge and a wall's width past
    each end, is all of the steps, so the core minus it meets at one mitred corner, the two
    moved faces' line: the skin there no thinner than its walls, and thicker than the round
    one by a mitre's corner, 21% of the walls' square at a right angle, so a little stronger
    than printed (`SHORT`). The solids,
    and how many concave edges aren't of two flat faces, or are mitred past `MITRE_LIMIT`."""
    import FreeCAD as App
    import numpy as np
    import Part

    first, second = (a for a in range(3) if a != layers)
    moves = []
    for k in range(ROUND):
        move = np.zeros(3)
        move[first] = by * math.cos(2 * math.pi * k / ROUND)
        move[second] = by * math.sin(2 * math.pi * k / ROUND)
        moves.append(move)
    found, left = [], 0
    for edge, faces in _concave(part):
        if type(edge.Curve).__name__ != "Line" or any(_curved(f) for f in faces):
            left += 1
            continue
        start, end = (np.array(v.Point) for v in (edge.Vertexes[0], edge.Vertexes[-1]))
        along = end - start
        length = float(np.linalg.norm(along))
        along /= length
        normals, levels, moved = [], [], []
        for face in faces:
            normal = np.array(face.normalAt(*face.Surface.parameter(edge.Vertexes[0].Point)))
            reach = max(float(m @ normal) for m in moves)
            normals.append(normal)
            levels.append(float(normal @ start))
            moved.append(reach)
        if min(moved) <= 1e-9:
            # A face in the layers' plane isn't moved in it: the edge lies in the plane too,
            # and the copies leave no steps there.
            continue
        base = start - along * by
        corners = [
            np.linalg.solve(np.array([normals[0], normals[1], along]),
                            np.array([levels[0] - a, levels[1] - b, float(along @ base)]))
            for a, b in ((0, 0), (0, moved[1]), (moved[0], moved[1]), (moved[0], 0))
        ]  # fmt: skip
        if float(np.linalg.norm(corners[2] - corners[0])) > MITRE_LIMIT * by:
            left += 1
            continue
        outline = Part.makePolygon([App.Vector(*c) for c in [*corners, corners[0]]])
        found.append(Part.Face(outline).extrude(App.Vector(*(along * (length + 2 * by)))))
    return found, left


@dataclass(frozen=True)
class Cut:
    """A part's core where its walls and shells are given: what's left of it, or None for
    nothing; how it was cut, in words; and its check against each layer's own (`Check`)."""

    core: Any | None
    how: str
    check: Check


def _cut_from(
    part: Any, source: Any, whence: str, layers: int, walls: float, top: float, bottom: float,
    up: float, bead: float, said: list[str], shells: Any | None = None,
) -> Cut | None:
    """The core cut from `source`, the part or a simpler shape of it (`simplified`), by each
    way in turn: the first that holds, or None, with why each didn't in `said`. Each is held
    to `part` itself: kept within its shells (`eroded_along`), `shells` where they're given,
    and checked against its own layers (`checked`) and for slivers (`slivers`) as it's cut,
    and again once they're merged (`healed`)."""
    import Part

    if shells is None:
        shells = eroded_along(part, layers, top, bottom, up)
    own = shells if source is part else eroded_along(source, layers, top, bottom, up)
    for how in ("offset", "copies"):
        try:
            if how == "offset":
                inside = offset_in_plane(source, layers, -walls) if walls > 0 else source
                words = f"OCCT's offset of it stretched along its layers{whence}"
            else:
                inside = eroded_in_plane(source, layers, walls)
                prisms, _ = mitres(source, layers, walls) if inside is not None else ([], 0)
                if prisms and inside is not None:
                    inside = _one(_solids(inside.cut(Part.makeCompound(prisms))))
                if inside is not None:
                    inside = inside.removeSplitter()
                words = (f"its common with {ROUND} copies of itself"
                         + (f", mitred at {len(prisms)} concave edge(s)" if prisms else "")
                         + whence)
            core = None
            if inside is not None and own is not None:
                core = _one(_solids(inside.common(own)))
            if core is not None and source is not part:
                core = _within(core, shells)
        except Exception as exc:  # noqa: BLE001 - OCCT raises where it can't make it
            what = "its offset" if how == "offset" else "its copies"
            said.append(f"OCCT couldn't make {what}{whence} ({type(exc).__name__})")
            continue
        check = checked(part, core, layers, walls, top, bottom, up)
        wrong = check.fails()
        if wrong is not None:
            said.append(f"{words}: {wrong}")
            continue
        if core is not None and slivers(core, bead):
            merged = healed(core, bead)
            if merged is None:
                said.append(f"{words}: {len(slivers(core, bead))} face(s) of its core "
                            f"narrower than a bead, {bead:g} mm, by a curved face, that no "
                            "merge took out")
                continue
            words = f"{words}, its slivers merged into the faces round them"
            try:
                core = _within(merged, shells)
            except Exception as exc:  # noqa: BLE001
                said.append(f"{words}: OCCT couldn't keep it within the part "
                            f"({type(exc).__name__})")
                continue
            check = checked(part, core, layers, walls, top, bottom, up)
            wrong = check.fails() or (
                f"{len(slivers(core, bead))} face(s) of its core narrower than a bead by a "
                "curved face, kept within the part" if core is not None
                and slivers(core, bead) else None)  # fmt: skip
            if wrong is not None:
                said.append(f"{words}: {wrong}")
                continue
        return Cut(core, words, check)
    return None


def _within(core: Any, shells: Any | None) -> Any | None:
    """`core` within the part's shells, where it's been cut from a shape other than the part
    or merged: none of it outside the part, nor nearer its faces along its layers than its
    shells (mqucifer/print-shop#356's review)."""
    if shells is None:
        return None
    if core.cut(shells).Volume <= NOTHING:
        return core
    return _one(_solids(core.common(shells)))


def cut_core(
    part: Any, layers: int, walls: float, top: float, bottom: float, up: float, bead: float,
    name: str,
) -> Cut:
    """The part's core, at least its walls from its outline in its own layer's plane and its
    shells from its faces along its layers (mqucifer/print-shop#356). The walls are cut by
    OCCT's offset where it can (`offset_in_plane`), else by the 16 copies, their steps at
    concave edges mitred (`mitres`); each is held to the core each layer has as Orca slices
    it (`checked`), and may leave no face narrower than a bead by a curved face (`slivers`),
    or have them merged (`healed`). The first that holds is the cut; where none does, each is
    tried again on the part with its own faces under a bead merged (`simplified`), held to
    the part itself still.

    Raises:
        NoAnswer: Where none holds, saying why: never a mesh's elements turned inside out by a
            geometry the bench made itself.
    """
    said: list[str] = []
    shells = eroded_along(part, layers, top, bottom, up)
    found = _cut_from(part, part, "", layers, walls, top, bottom, up, bead, said, shells)
    if found is not None:
        return found
    simple = simplified(part, bead)
    if simple is not None:
        source, count = simple
        found = _cut_from(
            part, source, f", from it with its {count} face(s) under a bead merged first",
            layers, walls, top, bottom, up, bead, said, shells,
        )
        if found is not None:
            return found
    curved = any(_curved(f) for f in part.Faces)
    raise NoAnswer(
        f"the skin couldn't be cut round {name}'s {'curved ' if curved else ''}faces: "
        + "; ".join(said)
        + f". Its walls are {walls:.2f} mm. Leave out its `print` and it's read solid"
    )


def eroded_along(
    shape: Any, layers: int, top: float, bottom: float, up: float = 1.0
) -> Any | None:
    """What of `shape` is at least `top` below each face it has above, and `bottom` over each
    face below, along the `layers` axis: above is its +, or with `up` -1, its - (the part
    printed turned over)."""
    left = shape
    if top > 0:
        left = left.common(shape.translated(_vector(layers, -top * up)))
    if bottom > 0 and left.Volume > NOTHING:
        left = left.common(shape.translated(_vector(layers, bottom * up)))
    return left if left.Volume > NOTHING else None


def region_solid(region: Region, part: Any, layers: int, folder: Path) -> Any:
    """A region's shape as a solid, in the part's own axes. A `layers` region is a slab across
    the whole part, from its low to its high along the part's layers, in its own coordinates."""
    import FreeCAD as App
    import Part

    shape = region.shape
    if isinstance(shape, Cylinder):
        return Part.makeCylinder(
            shape.radius, shape.length, App.Vector(*shape.at), App.Vector(*shape.axis)
        )
    if isinstance(shape, Layers):
        box = part.BoundBox
        start = [box.XMin - 1.0, box.YMin - 1.0, box.ZMin - 1.0]
        end = [box.XMax + 1.0, box.YMax + 1.0, box.ZMax + 1.0]
        start[layers], end[layers] = shape.low, shape.high
        return Part.makeBox(*(b - a for a, b in zip(start, end, strict=True)), App.Vector(*start))
    if isinstance(shape, Mesh):
        import Mesh as Meshes

        path = folder / shape.file
        if not path.is_file():
            raise NoAnswer(f"the region {region.name}'s mesh, {shape.file}, isn't beside the "
                           "assembly file")
        mesh = Meshes.Mesh(str(path))
        if not mesh.isSolid() or mesh.hasNonManifolds():
            raise NoAnswer(f"the region {region.name}'s mesh, {shape.file}, isn't one closed "
                           "solid: a region's shape is")
        skin = Part.Shape()
        skin.makeShapeFromMesh(mesh.Topology, 0.01)
        solid = Part.makeSolid(Part.makeShell(skin.Faces))
        if solid.Volume < 0:
            solid.reverse()
        return solid
    low, high = shape
    return Part.makeBox(
        high[0] - low[0], high[1] - low[1], high[2] - low[2], App.Vector(*low)
    )


def dilated_in_plane(shape: Any, layers: int, by: float) -> Any:
    """`shape` grown by `by` round its outline in each layer's plane: it with each of `ROUND`
    copies of itself, moved `by` round. Between two of them it's grown at least 98% of `by`."""
    import FreeCAD as App

    first, second = (a for a in range(3) if a != layers)
    grown = shape
    for k in range(ROUND):
        angle = 2 * math.pi * k / ROUND
        move = [0.0, 0.0, 0.0]
        move[first] = by * math.cos(angle)
        move[second] = by * math.sin(angle)
        grown = grown.fuse(shape.translated(App.Vector(*move)))
    return grown


def _box(solids: list[Any]) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    """Solids' box together, its least and most corners, or None for none: each one's own,
    not OCCT's quick box, which takes in a B-spline face's poles."""
    if not solids:
        return None
    boxes = [s.optimalBoundingBox() for s in solids]
    return (tuple(round(min(getattr(b, f"{a}Min") for b in boxes), 4) for a in "XYZ"),
            tuple(round(max(getattr(b, f"{a}Max") for b in boxes), 4) for a in "XYZ"))


def _fragments(part: Any, shapes: list[tuple[str, Any]]) -> list[tuple[tuple[str, ...], Any]]:
    """The part cut by each region's shape, in order: each piece with the regions round it."""
    pieces: list[tuple[tuple[str, ...], Any]] = [((), part)]
    for name, shape in shapes:
        cut: list[tuple[tuple[str, ...], Any]] = []
        for labels, piece in pieces:
            if not piece.BoundBox.intersect(shape.BoundBox):
                cut.append((labels, piece))
                continue
            inside = _solids(piece.common(shape))
            outside = _solids(piece.cut(shape))
            if inside:
                cut.append(((*labels, name), _one(inside)))
            if outside:
                cut.append((labels, _one(outside)))
        pieces = cut
    return pieces


def cells(part: Any, printed: Printed, layers: int, folder: Path, name: str) -> list[Cell]:
    """The part's cells, in its own axes, each with its settings and its core.

    Raises:
        NoAnswer: Where a setting `load` reads isn't given, or a region holds none of the part.
    """
    left_out = printed.settings.missing()
    if left_out:
        raise NoAnswer(
            f"the part {name} is given print settings without {', '.join(left_out)}: `load` "
            "reads a printed part from the settings it's sliced with, all of them given at the "
            "part's own level, as the slice's profile has them, and guesses none"
        )
    up = printed.settings.up
    if up is not None and up[1] != "xyz"[layers]:
        raise NoAnswer(
            f"the part {name}'s print says it stands own {up} up, and its layers are along its "
            f"own {'xyz'[layers]}: `up` is its layers axis with its sign as it prints"
        )
    sign = -1.0 if up is not None and up[0] == "-" else 1.0
    by_name = {r.name: r for r in printed.regions}
    # A volume, a box, a cylinder or a mesh, wins over a height range whatever their order, as
    # Orca applies a height range to the object and its modifiers over it; the volumes, in order.
    ordered = [r for r in printed.regions if isinstance(r.shape, Layers)]
    ordered += [r for r in printed.regions if not isinstance(r.shape, Layers)]
    shapes = []
    for region in ordered:
        shape = region_solid(region, part, layers, folder)
        if not part.BoundBox.intersect(shape.BoundBox) or part.common(shape).Volume <= NOTHING:
            raise NoAnswer(
                f"the part {name}'s region {region.said()} holds none of the part, in the "
                "part's own axes as its file has them"
            )
        shapes.append((region.name, shape))
    solid_of = dict(shapes)
    pieces = _fragments(part, shapes)
    # Each region with walls of its own, but a height range's, is an island: one ring of walls
    # each side of its shape's faces where they cut the part.
    seams = [r.name for r in ordered
             if r.settings.walls is not None and not isinstance(r.shape, Layers)]
    line = float(printed.settings.line_width or 0)
    # One ring of wall: one bead's spacing, as any wall (`wall_thickness`).
    ring = wall_thickness(1, line, float(printed.settings.layer_height or 0))
    inner = {r: eroded_in_plane(solid_of[r], layers, ring) for r in seams}
    outer = {r: dilated_in_plane(solid_of[r], layers, ring) for r in seams}
    found = []
    #: Each part's core by its walls and shells, cut once (`cut_core`).
    made: dict[tuple[float, float, float], Cut] = {}
    for labels, piece in pieces:
        own = printed.settings
        for label in labels:
            own = by_name[label].settings.over(own)
        where = f"the part {name}" + (f"'s {' and '.join(labels)}" if labels else "")
        for field in ("walls", "top", "bottom"):
            if not getattr(own, field):
                raise NoAnswer(
                    f"{where} is given {field} 0: its core would reach its outside, which "
                    "`load` doesn't read yet. Give it 1 or more, or leave out its `print` and "
                    "it's read solid"
                )
        if own.top != own.bottom and up is None:
            raise NoAnswer(
                f"{where} has {own.top} top layers and {own.bottom} bottom, and its print "
                "doesn't say which way it stands: give `up`, its layers axis with its sign as "
                f"it prints, such as \"+{'xyz'[layers]}\" or \"-{'xyz'[layers]}\""
            )
        walls = wall_thickness(int(own.walls or 0), line, float(own.layer_height or 0))
        top = float(own.top or 0) * float(own.layer_height or 0)
        bottom = float(own.bottom or 0) * float(own.layer_height or 0)
        key = (walls, top, bottom)
        if key not in made:
            made[key] = cut_core(part, layers, walls, top, bottom, sign, line, f"the part {name}")
        whole = made[key]
        core: list[Any] = []
        if whole.core is not None:
            left = whole.core.common(piece) if len(pieces) > 1 else whole.core
            for seam in seams:
                if left.Volume <= NOTHING:
                    break
                if seam in labels:
                    within = inner[seam]
                    left = left.common(within) if within is not None else left.cut(left)
                else:
                    left = left.cut(outer[seam])
            core = _solids(left)
        found.append(
            Cell(
                " and ".join(labels) if labels else "the part",
                own,
                labels,
                tuple(core),
                float(piece.Volume),
                float(sum(s.Volume for s in core)),
                _box(core),
                whole.how,
                whole.check,
            )
        )
    return found
