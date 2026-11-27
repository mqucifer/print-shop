"""A part as it's printed, as solids: a skin of walls and shells round an infill core, in cells
of their own settings (mqucifer/print-shop#337).

`load` reads a part the assembly file gives print settings for (`printed.py`) as a solid skin
round a core, each with its own figures (`infill.py`). This makes the core: whatever of the part
is further than its walls from its outline in its own layer, and further than its top shells
below each face it has above, and its bottom shells over each face below. What's left of the
part is the skin. The mesh then has a face where the skin meets the core, so each element is
skin or core, and none is half of each: a 5 mm element can't tell a 0.9 mm wall from the infill
behind it, and with the skin smeared over the elements it lies in, a bar's section is 19 to 38%
too soft by its own arithmetic.

**The erosion.** A point is in the core where the part holds the point its top shells above it,
the point its bottom shells below it, and each of 16 points its walls away round it in its
layer's plane: so the core is the part's common with each of 18 copies of itself, each moved by
one of those. It's OCCT's `common` of solids only, which holds on any shape where an offset
surface doesn't, and it errs one way: a wall between two of the 16 directions is read at least
cos(11.25°), 98%, as thick as it is, so the skin is never thicker than the slicer's. Only each
point's end is tested, so a slot narrower than the shells between a point and the one it tests
is passed over, and Orca's own rules for small areas, such as filling one under
`minimum_sparse_infill_area` solid, aren't read: each leaves the core a little larger, and the
part a little softer and weaker than printed.

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

    @property
    def walls_mm(self) -> float:
        s = self.settings
        return float(s.walls or 0) * float(s.line_width or 0)

    @property
    def top_mm(self) -> float:
        return float(self.settings.top or 0) * float(self.settings.layer_height or 0)

    @property
    def bottom_mm(self) -> float:
        return float(self.settings.bottom or 0) * float(self.settings.layer_height or 0)


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


def eroded_along(shape: Any, layers: int, top: float, bottom: float) -> Any | None:
    """What of `shape` is at least `top` below each face it has above, and `bottom` over each
    face below, along the `layers` axis."""
    left = shape
    if top > 0:
        left = left.common(shape.translated(_vector(layers, -top)))
    if bottom > 0 and left.Volume > NOTHING:
        left = left.common(shape.translated(_vector(layers, bottom)))
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
    inner = {r: eroded_in_plane(solid_of[r], layers, line) for r in seams}
    outer = {r: dilated_in_plane(solid_of[r], layers, line) for r in seams}
    found = []
    for labels, piece in pieces:
        own = printed.settings
        for label in labels:
            own = by_name[label].settings.over(own)
        walls = float(own.walls or 0) * line
        top = float(own.top or 0) * float(own.layer_height or 0)
        bottom = float(own.bottom or 0) * float(own.layer_height or 0)
        core: list[Any] = []
        inside = eroded_in_plane(part, layers, walls)
        shelled = eroded_along(part, layers, top, bottom) if inside is not None else None
        if inside is not None and shelled is not None:
            left = inside.common(shelled).common(piece)
            for seam in seams:
                if left.Volume <= NOTHING:
                    break
                if seam in labels:
                    ring = inner[seam]
                    left = left.common(ring) if ring is not None else left.cut(left)
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
            )
        )
    return found
