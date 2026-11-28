"""A printed part's layers as Orca slices them, in 2D (mqucifer/print-shop#337,
mqucifer/print-shop#363): what `load` reads a printed part's mesh by (`classify.py`).

**Walls.** A part's walls are as many beads' spacing (`wall_thickness`), by Orca 2.4.2's own
flow. Each layer's section is offset in by them (`_inside_walls`), each face of it on its own,
OCCT's 2D offset, a face thinner than its walls gone and the rest kept; where OCCT's offset of a
section's curves fails, the face is offset as a polygon within a micron of them, and shrunk,
failing that, by its common with 32 copies of itself (`_offset_2d`). OCCT's 2D offset goes by a
face's own way round, and a section across a part's own y came the other way, so its walls were
offset out, not in: which way it went is read from its area.

**Too thin for infill.** Orca opens the area inside the walls before it lays sparse infill, by
half of 0.6 of a solid infill line's spacing (`open_radius`, `opened`): where it's narrower
than that, about 0.24 mm, it lays none, and the walls and gap fill make it solid.

**Shells.** What of a layer isn't there its top shells above, or its bottom shells below, is
its shells' (`_exposed`), grown across the layer by a line's spacing, as Orca grows a top or
bottom face's solid into the sparse infill beside it (`MARGIN`): it keeps the core off the line
where a flat face meets an upright one, a counterbore's floor its bore or a boss its pocket's
floor. A layer's core is what's left (`layer_core`). Only each point's end is tested along the
layers, so a slot narrower than the shells between a point and the one it tests is passed
over, and Orca's own rules for small areas, such as filling one under
`minimum_sparse_infill_area` solid, aren't read: each leaves the core a little larger, and the
part a little softer and weaker than printed.

**Regions** (`printed.Region`) give a piece of the part settings of their own, each later over
an earlier, a volume's over a height range's, as Orca 2.4.2 applies them (`printed.py`); a
volume with walls of its own is an island, one ring of walls each side of its faces where they
cut the part (`classify.Layered`). `region_solid` makes a region's shape a solid in the part's
own axes.

FreeCAD's Part module, in the second image (`cae.py`).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .printed import Cylinder, Layers, Mesh, Region
from .report import NoAnswer

#: Smaller than this, in mm³, a region's share of a part is nothing.
NOTHING = 1e-3

#: How far a printed part's mesh's core may be from Orca's, in each band of its layers, as a
#: share of the band's volume (`classify.exact_checked`, mqucifer/print-shop#363): a core too small
#: reads the part stronger than printed, the unsafe side, so `SHORT` is the tighter; one too big
#: reads it weaker.
SHORT = 0.02

AGREE = 0.05

#: How much of a solid infill line's spacing the area inside the walls is opened by, as Orca
#: 2.4.2 opens it before it lays sparse infill (`PerimeterGenerator.cpp:1658-1663`: "coord_t
#: min_perimeter_infill_spacing = coord_t(solid_infill_spacing * (1. -
#: INSET_OVERLAP_TOLERANCE));", then "offset2_ex(not_filled_exp, float(-inset -
#: min_perimeter_infill_spacing / 2.), float(min_perimeter_infill_spacing / 2.))", with
#: INSET_OVERLAP_TOLERANCE 0.4 in `libslic3r.h` on Orca's main): where it's narrower than that,
#: no infill is laid, and the walls and gap fill make it solid (mqucifer/print-shop#363). Read
#: at the walls' own line width, as `load` is given no other.
OPEN_SHARE = 0.6

#: How far a top or bottom face's solid shells are grown across its layer into the sparse infill
#: beside them (`_exposed`, mqucifer/print-shop#363), as a share of a wall line's spacing: one.
#: Orca grows them, in `LayerRegion.cpp`'s `process_external_surfaces` on its main, by about a
#: wall line's spacing into the sparse zone (`expansion_min`, "the perimeter flow's scaled
#: spacing"; the routine that grows them, `propagate_waves`, unread). It keeps the core off the
#: line where a flat face meets an upright one: without it a part's skin there was two pieces
#: meeting on a line.
MARGIN = 1.0

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

def open_radius(line_width: float, layer_height: float) -> float:
    """The radius Orca opens the area inside the walls by before it lays sparse infill, in mm:
    half of `OPEN_SHARE` of a line's spacing, by Orca's own flow (`wall_thickness`)."""
    return OPEN_SHARE * wall_thickness(1, line_width, layer_height) / 2

#: How many copies a face in a layer's plane is moved round, where OCCT's 2D offset of it fails
#: (`_offset_2d`): a wall between two is read at least cos(180°/32), 99.5%, as thick as it is.
ROUND_2D = 32

#: How far a face's outline in a layer's plane may be from its polygon, in mm, where OCCT's 2D
#: offset of its own curves fails (`_polygonal`).
POLYGON = 1e-3

def _polygonal(face: Any) -> Any:
    """A face in a layer's plane with its outline and holes as polygons, each `POLYGON` from
    its curves at most: OCCT's 2D offset of an offset curve, as a section of an offset face
    has, can fail where one of straight lines holds."""
    import Part

    wires = []
    for wire in face.Wires:
        points = wire.discretize(Deflection=POLYGON)
        if points[0].distanceToPoint(points[-1]) > 1e-9:
            points.append(points[0])
        wires.append(Part.makePolygon(points))
    return Part.makeFace(wires, "Part::FaceMakerBullseye").Faces[0]

def _offset_2d(face: Any, by: float, layers: int, join: int = 0) -> list[Any]:
    """A face in a layer's plane grown by `by`, or with `by` under naught shrunk, as faces:
    OCCT's 2D offset, rounded at its corners; where that fails, OCCT's 2D offset of the face's
    polygon (`_polygonal`); and where that fails too and it's shrunk, its common with
    `ROUND_2D` copies of it moved `by` round in its plane, flat faces' booleans, which hold.
    Nothing where it's thinner than twice `-by`.

    Raises:
        _Unread: Where none can be made.
    """
    import FreeCAD as App

    if by == 0:
        return [face]
    for shape in (face, None):
        try:
            given = face if shape is not None else _polygonal(face)
            made = given.makeOffset2D(by, join, False, False, join != 0)
            # OCCT offsets by the face's own way round: a section across a part's layers along
            # its own y comes the other way, and an offset in by `by` there grew it
            # (mqucifer/print-shop#363's probe of a real tile and leg). Which way it went is
            # read from its area, and where it's the wrong one it's offset the other way.
            if (made.Area > given.Area) != (by > 0):
                made = given.makeOffset2D(-by, join, False, False, join != 0)
                if (made.Area > given.Area) != (by > 0):
                    continue
            return [f for f in made.Faces if f.Area > 1e-9]
        except Exception:  # noqa: BLE001 - OCCT raises for a face it can't offset, or that's gone
            continue
    if by > 0:
        raise _Unread("OCCT couldn't grow a face in a layer's plane, nor its polygon")
    first, second = (a for a in range(3) if a != layers)
    try:
        left = face
        for k in range(ROUND_2D):
            move = [0.0, 0.0, 0.0]
            move[first] = -by * math.cos(2 * math.pi * k / ROUND_2D)
            move[second] = -by * math.sin(2 * math.pi * k / ROUND_2D)
            left = left.common(face.translated(App.Vector(*move)))
            if left.Area <= 1e-9:
                return []
        return [f for f in left.Faces if f.Area > 1e-9]
    except Exception as exc:  # noqa: BLE001
        raise _Unread(str(exc)) from exc

def _inside_walls(part: Any, layers: int, at: float, walls: float) -> tuple[list[Any], float]:
    """The area inside the part's walls at `at` along its layers: each face of its section
    there offset in by its walls on its own, OCCT's 2D offset, a face thinner than its walls
    gone and the rest kept (mqucifer/print-shop#356's review); and the section's outline's
    length, in mm. No faces, and 0, where it has no section there.

    Raises:
        _Unread: Where OCCT can't offset a face that isn't thinner than its walls.
    """
    own = _section(part, layers, at)
    if own is None:
        return [], 0.0
    inner = []
    for face in own.Faces:
        inner += _offset_2d(face, -walls, layers)
    return inner, float(sum(w.Length for w in own.Wires))

def opened(faces: list[Any], radius: float, layers: int) -> list[Any]:
    """Faces in a layer's plane opened by `radius`, as Orca opens the area inside the walls:
    shrunk by it and grown back (`_offset_2d`), so that what's narrower than twice it is gone,
    and the corners it can't reach rounded; what's grown back kept within the face. A face
    narrower everywhere is gone.

    Raises:
        _Unread: Where neither OCCT's offset nor the copies can be made: never read as gone
            (mqucifer/print-shop#356's review).
    """
    if radius <= 0:
        return list(faces)
    kept = []
    for face in faces:
        grown = [g for f in _offset_2d(face, -radius, layers)
                 for g in _offset_2d(f, radius, layers)]  # fmt: skip
        for g in grown:
            try:
                kept += [f for f in g.common(face).Faces if f.Area > 1e-9]
            except Exception as exc:  # noqa: BLE001
                raise _Unread(str(exc)) from exc
    return kept

def _exposed(
    part: Any, layers: int, at: float, top: float, bottom: float, up: float, margin: float
) -> list[Any]:
    """What of the part's section at `at` its top shells or its bottom shells take, as Orca
    reads a layer: what of it isn't there `top` above, or `bottom` below; grown `margin` across
    the layer, its corners rounded as the walls' are, as Orca grows a top or bottom face's solid
    into the sparse infill beside it (`MARGIN`). As faces in the plane at `at`.

    Raises:
        _Unread: Where a face can't be grown.
    """
    import Part

    own = _section(part, layers, at)
    if own is None:
        return []
    exposed = []
    for along in (top * up, -bottom * up):
        if along == 0:
            continue
        other = _section(part, layers, at + along)
        rest = own if other is None else own.cut(_moved_to(other, layers, at))
        exposed += [f for f in rest.Faces if f.Area > 1e-9]
    if not exposed:
        return []
    joined = exposed[0].fuse(exposed[1:]).removeSplitter() if len(exposed) > 1 else exposed[0]
    grown = ([g for f in joined.Faces for g in _offset_2d(f, margin, layers)] if margin
             else list(joined.Faces))  # fmt: skip
    return [Part.makeCompound(grown)] if grown else []

def layer_core(
    part: Any, layers: int, at: float, walls: float, top: float, bottom: float, up: float,
    open_by: float = 0.0, margin: float = 0.0,
) -> tuple[Any | None, float] | None:
    """The core's section at `at` along its layers as Orca slices it: the area inside its
    walls (`_inside_walls`), opened by `open_by` as Orca opens it before it lays sparse infill
    (`opened`, mqucifer/print-shop#363), and cut by the part's sections `top` above and
    `bottom` below it, which its shells need; with `margin`, what its shells take grown that
    far across the layer, as Orca grows them (`_exposed`). Its faces, or None where nothing's
    left, and its outline's length, the part's section's, in mm. None where the part has no
    section there.

    Raises:
        _Unread: Where OCCT can't offset a face that isn't thinner than its walls.
    """
    import Part

    inner, outline = _inside_walls(part, layers, at, walls)
    if outline == 0:
        return None
    inner = opened(inner, open_by, layers)
    left = Part.makeCompound(inner) if inner else None
    if margin > 0 and left is not None:
        # The shells' own, grown across the layer as Orca grows them (`_exposed`).
        for taken in _exposed(part, layers, at, top, bottom, up, margin):
            left = left.cut(taken)
        return (left if left.Area > 1e-9 else None), outline
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
    return left, outline

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
