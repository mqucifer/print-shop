"""Renders from the standard views, offscreen with VTK and OSMesa, for the model and the Sponsor.

Each picture is checked against arithmetic that never touched the renderer: the parts' mesh is
projected along the view by NumPy, and the painted pixels must cover the same outline shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely
import trimesh
import vtk
from PIL import Image, ImageDraw, ImageFilter
from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy

from . import colours as colours_module
from . import cut as cut_module
from .report import Report, slug
from .shapes import NoAnswer, Part, load, slice_at

#: A sheet's legend: one line of it, and the room an entry takes along it. An entry's words wrap
#: within it, and are never cut (mqucifer/print-shop#54: "none was giv").
LEGEND_LINE = 18
LEGEND_ENTRY = 440
#: Where an entry's words start, after its colour's swatch, and the room kept at its right.
LEGEND_TEXT = 18
LEGEND_PAD = 10
#: A file's mark on a picture, its name, drawn after the picture is checked.
MARK_PAD = 2
#: Where the camera sits from the centre, and the picture's up.
VIEWS = {
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "side": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "iso": ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
}
FEATURE_ANGLE = 30.0
#: The picture's outline and the arithmetic's may differ by this share of its longer side.
OUTLINE_SHARE = 0.03
#: The picture's margin: its side over the parts' longer side.
FILL = 1.08
#: A close-up and the arithmetic's picture of it may differ by this share of its pixels, away from
#: an outline's edge.
CLOSE_SHARE = 0.005
#: How far from the arithmetic's outline, in pixels, a painted line can stand: VTK's edges.
CLOSE_EDGE = 2
#: A test piece's grey, and how much of what's behind it shows through.
TEST_PIECE_COLOUR = (0.6, 0.6, 0.6)
TEST_PIECE_OPACITY = 0.35
TEST_PIECE_MARK = "grey: a test piece, not the part"


@dataclass
class Close:
    """A close-up: the point it's centred on, how far it reaches each way, and its view."""

    centre: tuple[float, float, float]
    reach: float
    view: str

    @property
    def label(self) -> str:
        x, y, z = self.centre
        return f"close {x:g},{y:g},{z:g}, {2 * self.reach:g} mm across, {self.view}"


def _close(text: str) -> Close:
    """`X,Y,Z,R` or `X,Y,Z,R,VIEW`."""
    words = text.split(",")
    try:
        if len(words) not in (4, 5):
            raise ValueError
        x, y, z, reach = (float(w) for w in words[:4])
    except ValueError:
        raise NoAnswer(f"a close-up is X,Y,Z,R or X,Y,Z,R,VIEW, not {text}") from None
    view = words[4] if len(words) == 5 else "top"
    if reach <= 0:
        raise NoAnswer(f"a close-up's reach is over nothing: {text}")
    if view not in VIEWS:
        raise NoAnswer(f"no view {view}: they're {', '.join(VIEWS)}")
    return Close((x, y, z), reach, view)


def _poly(mesh: trimesh.Trimesh) -> Any:
    points = vtk.vtkPoints()
    points.SetData(numpy_to_vtk(np.asarray(mesh.vertices, dtype=float), deep=True))
    faces = np.asarray(mesh.faces, dtype=np.int64)
    cells = vtk.vtkCellArray()
    stacked = np.c_[np.full(len(faces), 3, dtype=np.int64), faces].ravel()
    cells.SetCells(len(faces), numpy_to_vtkIdTypeArray(stacked, deep=True))
    data = vtk.vtkPolyData()
    data.SetPoints(points)
    data.SetPolys(cells)
    return data


def _actors(
    data: Any,
    colour: tuple[float, float, float],
    renderer: Any,
    opacity: float = 1.0,
    flat: bool = False,
) -> None:
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(data)
    mapper.SetResolveCoincidentTopologyToPolygonOffset()
    body = vtk.vtkActor()
    body.SetMapper(mapper)
    body.GetProperty().SetColor(*colour)
    body.GetProperty().SetInterpolationToFlat()
    body.GetProperty().SetOpacity(opacity)
    renderer.AddActor(body)
    if flat:
        # Unlit and with no outline: each pixel is exactly its file's colour, to count.
        body.GetProperty().LightingOff()
        return
    edges = vtk.vtkFeatureEdges()
    edges.SetInputData(data)
    edges.SetFeatureAngle(FEATURE_ANGLE)
    edges.BoundaryEdgesOn()
    edges.FeatureEdgesOn()
    edges.ManifoldEdgesOff()
    edges.NonManifoldEdgesOn()
    edges.ColoringOff()
    lines = vtk.vtkPolyDataMapper()
    lines.SetInputConnection(edges.GetOutputPort())
    outline = vtk.vtkActor()
    outline.SetMapper(lines)
    outline.GetProperty().SetColor(*((0, 0, 0) if opacity == 1.0 else colour))
    outline.GetProperty().SetLineWidth(1.5)
    renderer.AddActor(outline)


def _axes(view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The picture's right and up, and the way the camera looks back along, as unit vectors."""
    position, up = (np.array(v, dtype=float) for v in VIEWS[view])
    along = position / np.linalg.norm(position)
    right = np.cross(up, along)
    right /= np.linalg.norm(right)
    return right, np.cross(along, right), along


def projected_shape(meshes: list[trimesh.Trimesh], view: str) -> tuple[float, float]:
    """The parts' outline along a view, by arithmetic: (width, height) in mm."""
    right, top, _ = _axes(view)
    points = np.vstack([np.asarray(m.vertices) for m in meshes])
    return float(np.ptp(points @ right)), float(np.ptp(points @ top))


def painted(pixels: np.ndarray) -> tuple[float, tuple[int, int]]:
    """The share of a picture that isn't background, and the painted area's (width, height)."""
    marked = (pixels[:, :, :3] < 250).any(axis=2)
    if not marked.any():
        return 0.0, (0, 0)
    columns = np.nonzero(marked.any(axis=0))[0]
    rows = np.nonzero(marked.any(axis=1))[0]
    return float(marked.mean()), (
        int(columns[-1] - columns[0] + 1),
        int(rows[-1] - rows[0] + 1),
    )


def outline_problem(
    pixels: np.ndarray, shape_mm: tuple[float, float], size: int
) -> tuple[str | None, dict[str, Any]]:
    """Whether a picture's painted outline is the shape the arithmetic says it should be."""
    share, (width, height) = painted(pixels)
    facts: dict[str, Any] = {
        "painted_share": round(share, 4),
        "painted_px": [width, height],
        "projected_mm": [round(shape_mm[0], 3), round(shape_mm[1], 3)],
    }
    if width == 0:
        return "nothing was drawn", facts
    if width >= size or height >= size:
        return "the parts run off the edge of the picture", facts
    longest = max(shape_mm)
    expected = np.array(shape_mm) / longest
    drawn = np.array([width, height]) / max(width, height)
    worst = float(np.abs(expected - drawn).max())
    facts["outline_differs"] = round(worst, 4)
    if worst > OUTLINE_SHARE:
        return (
            f"the picture's outline is {width}x{height} px, and the parts project to "
            f"{shape_mm[0]:.1f}x{shape_mm[1]:.1f} mm",
            facts,
        )
    return None, facts


def projected_mask(meshes: list[trimesh.Trimesh], close: Close, size: int) -> np.ndarray:
    """What a close-up should cover, by arithmetic: each triangle projected and filled, with no
    renderer anywhere near it."""
    right, top, _ = _axes(close.view)
    centre = np.array(close.centre)
    scale = size / (2 * close.reach)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    for mesh in meshes:
        corners = np.asarray(mesh.vertices)[np.asarray(mesh.faces)] - centre
        across = corners @ right * scale + size / 2
        down = size / 2 - corners @ top * scale
        seen = (
            (across.max(axis=1) >= 0)
            & (across.min(axis=1) <= size)
            & (down.max(axis=1) >= 0)
            & (down.min(axis=1) <= size)
        )
        for xs, ys in zip(across[seen].tolist(), down[seen].tolist(), strict=True):
            draw.polygon(list(zip(xs, ys, strict=True)), fill=1)
    return np.asarray(mask) > 0


def close_problem(
    pixels: np.ndarray, expected: np.ndarray, reach: float
) -> tuple[str | None, dict[str, Any]]:
    """Whether a close-up shows what the arithmetic says lies in it, away from an outline's edge,
    where a line's width and a pixel's rounding aren't an answer either way."""
    size = pixels.shape[0]
    marked = (pixels[:, :, :3] < 250).any(axis=2)
    facts: dict[str, Any] = {
        "painted_share": round(float(marked.mean()), 4),
        "projected_share": round(float(expected.mean()), 4),
        "across_mm": round(2 * reach, 4),
        "px_per_mm": round(size / (2 * reach), 2),
    }
    if not marked.any() and not expected.any():
        return "nothing of the parts lies in it", facts
    side = 2 * CLOSE_EDGE + 1
    mask = Image.fromarray(expected.astype(np.uint8) * 255)
    grown = np.asarray(mask.filter(ImageFilter.MaxFilter(side))) > 0
    shrunk = np.asarray(mask.filter(ImageFilter.MinFilter(side))) > 0
    worst = float((marked & ~grown).mean() + (~marked & shrunk).mean())
    facts["differs"] = round(worst, 4)
    if worst > CLOSE_SHARE:
        return (
            f"{worst:.1%} of the picture differs from what the mesh projects to, away from any "
            "edge",
            facts,
        )
    return None, facts


def _stamped(pixels: np.ndarray, *lines: str) -> np.ndarray:
    """A picture with words along its foot, once it's been checked: the check reads none of them."""
    picture = Image.fromarray(pixels.copy())
    draw = ImageDraw.Draw(picture)
    for k, line in enumerate(reversed(lines)):
        draw.text((8, picture.height - 16 * (k + 1)), line, fill=(20, 20, 20))
    return np.asarray(picture)


def _aim(points: np.ndarray, view: str, close: Close | None = None) -> tuple[np.ndarray, float]:
    """Where a picture looks and how far it reaches from there, in mm: the middle of the outline
    and half its longer side, so a long part isn't clipped, or a close-up's own."""
    right, top, along = _axes(view)
    across, high = points @ right, points @ top
    centre = (
        right * (across.min() + across.max()) / 2
        + top * (high.min() + high.max()) / 2
        + along * float((points @ along).mean())
    )
    scale = FILL * max(float(np.ptp(across)), float(np.ptp(high))) / 2
    if close is not None:
        # The depth isn't what a picture is aimed by: it stays the parts' own.
        target = np.array(close.centre)
        centre = right * (target @ right) + top * (target @ top) + along * (centre @ along)
        scale = close.reach
    return centre, scale


def _render_view(
    parts: list[trimesh.Trimesh],
    view: str,
    size: int,
    test_pieces: list[trimesh.Trimesh] | None = None,
    close: Close | None = None,
    colours: list[tuple[float, float, float]] | None = None,
    flat: bool = False,
) -> np.ndarray:
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(1, 1, 1)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(size, size)
    window.AddRenderer(renderer)
    shades = colours or [
        (r / 255, g / 255, b / 255) for r, g, b in colours_module.distinct(len(parts), [])
    ]
    for mesh, colour in zip(parts, shades, strict=True):
        _actors(_poly(mesh), colour, renderer, flat=flat)
    # After the parts, so a see-through piece blends with what's behind it.
    for mesh in test_pieces or []:
        _actors(_poly(mesh), TEST_PIECE_COLOUR, renderer, TEST_PIECE_OPACITY)
    position, up = VIEWS[view]
    right, top, along = _axes(view)
    points = np.vstack([np.asarray(m.vertices) for m in [*parts, *(test_pieces or [])]])
    centre, scale = _aim(points, view)
    camera = renderer.GetActiveCamera()
    camera.ParallelProjectionOn()
    camera.SetFocalPoint(*centre)
    camera.SetPosition(*(centre + 100 * np.array(position)))
    camera.SetViewUp(*up)
    renderer.ResetCamera()
    centre, scale = _aim(points, view, close)
    # ResetCamera aimed it at the parts' box: back to our centre, looking the same way.
    distance = camera.GetDistance()
    camera.SetFocalPoint(*centre)
    camera.SetPosition(*(centre + along * distance))
    camera.SetParallelScale(scale)
    renderer.ResetCameraClippingRange()
    window.Render()
    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(window)
    grab.Update()
    data = vtk_to_numpy(grab.GetOutput().GetPointData().GetScalars())
    pixels = data.reshape(size, size, -1)[::-1]
    window.Finalize()
    return np.asarray(pixels[:, :, :3], dtype=np.uint8)


def islands(section: Any) -> list[Any]:
    """The pieces of a section that lie inside another's hole: frame joined to the rest only above
    or below, as the walls round a pane with holes are."""
    holes = [shapely.Polygon(r) for poly in section.geoms for r in poly.interiors]
    return [
        poly
        for poly in section.geoms
        if any(h.contains(poly.representative_point()) for h in holes)
    ]


def _section_picture(part: Part, z: float, size: int) -> np.ndarray:
    """A plane section drawn from Shapely's polygons, material dark on white.

    Each piece is drawn on its own and laid over the rest, so an island inside another piece's
    hole isn't painted over by that hole: on `cup-1` the drawing dropped the islands, and the
    operator read the picture as a frame with no walls.
    """
    section = slice_at(part.manifold(), z)
    low, high = np.asarray(section.bounds).reshape(2, 2)
    scale = 0.9 * size / float(max(high - low))
    margin = (size - (high - low) * scale) / 2

    def pixel(ring: Any) -> list[tuple[float, float]]:
        xy = (np.asarray(ring.coords) - low) * scale + margin
        return [(float(x), float(size - y)) for x, y in xy]

    material = np.zeros((size, size), dtype=bool)
    for poly in section.geoms:
        mask = Image.new("1", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.polygon(pixel(poly.exterior), fill=1)
        for hole in poly.interiors:
            draw.polygon(pixel(hole), fill=0)
        material |= np.asarray(mask, dtype=bool)
    picture = np.full((size, size, 3), 255, dtype=np.uint8)
    picture[material] = (70, 70, 80)
    return picture


def _pieces_missing(section: Any, pixels: np.ndarray, size: int) -> list[str]:
    """Which pieces of a section the picture doesn't show, by each one's own pixels."""
    low, high = np.asarray(section.bounds).reshape(2, 2)
    scale = 0.9 * size / float(max(high - low))
    margin = (size - (high - low) * scale) / 2
    dark = pixels[:, :, 0] < 250
    missing = []
    for poly in section.geoms:
        mask = Image.new("1", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        xy = (np.asarray(poly.exterior.coords) - low) * scale + margin
        draw.polygon([(float(x), float(size - y)) for x, y in xy], fill=1)
        for hole in poly.interiors:
            xy = (np.asarray(hole.coords) - low) * scale + margin
            draw.polygon([(float(x), float(size - y)) for x, y in xy], fill=0)
        inside = np.asarray(mask, dtype=bool)
        drawn = float((dark & inside).sum()) / scale**2
        if poly.area > 5 / scale**2 and drawn < 0.5 * poly.area:
            c = poly.representative_point()
            missing.append(f"a {poly.area:.1f} mm² piece at {c.x:.2f},{c.y:.2f}")
    return missing


def _close_second(name: str, facts: dict[str, Any]) -> str:
    return (
        f"{name}: {facts['painted_share']:.1%} painted, {facts['projected_share']:.1%} by the "
        f"mesh's triangles projected and filled, {facts.get('differs', 'n/a')} of the picture "
        "differing away from an edge"
    )


def _not_both(files: list[str], pieces: list[str]) -> None:
    both = sorted(set(files) & set(pieces))
    if both:
        raise NoAnswer(f"{both[0]} is named as the part and as a test piece")


def _test_pieces(given: list[str], report: Report) -> list[trimesh.Trimesh]:
    """What's drawn beside the part and marked as not being it. A test piece is whatever the one
    asking says it is: nothing here can tell by looking."""
    meshes = [m for path in given for m in (p.fine() for p in load(path).pieces())]
    if given:
        report.line(f"test pieces, grey, not the part: {', '.join(given)}")
        report.numbers["test_pieces"] = list(given)
    return meshes


def _variants(given: list[str]) -> list[tuple[str, list[str]]]:
    """`NAME=a.step,b.step`, as (name, files), each name once."""
    found: list[tuple[str, list[str]]] = []
    for text in given:
        name, _, files = text.partition("=")
        if not name or not files:
            raise NoAnswer(f"a variant is NAME=FILE[,FILE...], not {text}")
        if name in (n for n, _ in found):
            raise NoAnswer(f"two variants are named {name}")
        found.append((name, files.split(",")))
    return found


@dataclass
class Shown:
    """One file on a sheet: its geometry, how it's drawn, its number in order, and its mark on the
    pictures: its own name, so `pane_1.step` is marked `pane_1`, as the model numbered it, and not
    by the sheet's count (mqucifer/print-shop#54)."""

    name: str
    whole: Part
    parts: list[Part]
    rgb: tuple[int, int, int]
    colour: str
    label: str
    number: int
    anchor: np.ndarray
    mark: str = ""

    @property
    def meshes(self) -> list[trimesh.Trimesh]:
        return [p.fine() for p in self.parts]

    @property
    def shade(self) -> tuple[float, float, float]:
        return (self.rgb[0] / 255, self.rgb[1] / 255, self.rgb[2] / 255)

    @property
    def said(self) -> str:
        """How the legend names it: its mark, where that isn't its file's name, its file, label
        and colour."""
        label = f": {self.label}" if self.label else ""
        colour = self.colour or "a colour of the sheet's, none was given"
        mark = "" if self.mark == Path(self.name).stem else f"{self.mark} "
        return f"{mark}{self.name}{label} ({colour})"


def _looks(given: list[str]) -> dict[str, colours_module.Look]:
    found: dict[str, colours_module.Look] = {}
    for text in given:
        try:
            look = colours_module.parse(text)
        except colours_module.NotAColour as exc:
            raise NoAnswer(str(exc)) from None
        if look.file in found:
            raise NoAnswer(f"two looks name {look.file}")
        found[look.file] = look
    return found


def _anchor(parts: list[Part]) -> np.ndarray:
    """A point inside a file, where its number goes: in its largest piece's section halfway up,
    so it lands on the pane and not in its opening, or its box's middle if that misses it."""
    biggest = max(parts, key=lambda p: float(np.prod(np.ptp(p.extent(), axis=0)[:2])))
    low, high = biggest.extent()
    middle: np.ndarray = (low + high) / 2
    try:
        cut = slice_at(biggest.manifold(), float(middle[2]))
    except NoAnswer:
        return middle
    if cut.is_empty:
        return middle
    spot = max(cut.geoms, key=lambda g: g.area).representative_point()
    return np.array([spot.x, spot.y, middle[2]])


def _shown(names: list[str], looks: dict[str, colours_module.Look]) -> list[Shown]:
    """Each file once, numbered in the order it's first named. A file with no look takes a colour
    no other file here has, so none is drawn in another's colour."""
    order = list(dict.fromkeys(names))
    unknown = sorted(set(looks) - set(order))
    if unknown:
        raise NoAnswer(f"a look names {unknown[0]}, which isn't drawn here")
    given = [looks[n].rgb for n in order if n in looks]
    spare = iter(colours_module.distinct(sum(1 for n in order if n not in looks), given))
    found = []
    stems = [Path(n).stem for n in order]
    for k, name in enumerate(order):
        stem = Path(name).stem
        mark = stem if stems.count(stem) == 1 else str(Path(name).with_suffix(""))
        whole = load(name)
        parts = whole.pieces()
        look = looks.get(name)
        rgb = look.rgb if look else next(spare)
        found.append(
            Shown(
                name,
                whole,
                parts,
                rgb,
                look.colour if look else "",
                look.label if look else "",
                k + 1,
                _anchor(parts),
                mark,
            )
        )
    return found


def _numbered(pixels: np.ndarray, marks: list[tuple[str, float, float]]) -> np.ndarray:
    """Each file's mark where it is, once the picture's been checked: the check reads none."""
    picture = Image.fromarray(pixels.copy())
    draw = ImageDraw.Draw(picture)
    size = picture.width
    for text, x, y in marks:
        if not (0 <= x < size and 0 <= y < picture.height):
            continue
        left, top, right, bottom = draw.textbbox((x, y), text, anchor="mm")
        draw.rectangle(
            (left - MARK_PAD, top - MARK_PAD, right + MARK_PAD, bottom + MARK_PAD),
            fill=(255, 255, 255),
            outline=(20, 20, 20),
        )
        draw.text((x, y), text, fill=(20, 20, 20), anchor="mm")
    return np.asarray(picture)


def _marks(
    shown: list[Shown],
    view: str,
    size: int,
    points: np.ndarray,
    close: Close | None = None,
) -> list[tuple[str, float, float]]:
    """Where each file's mark lands in a view, by the same arithmetic that aims the camera."""
    right, top, _ = _axes(view)
    centre, scale = _aim(points, view, close)
    found = []
    for one in shown:
        across = float((one.anchor - centre) @ right)
        up = float((one.anchor - centre) @ top)
        per_mm = size / (2 * scale)
        found.append((one.mark, size / 2 + across * per_mm, size / 2 - up * per_mm))
    return found


def _id_shade(k: int) -> tuple[float, float, float]:
    """The k-th file's colour in the counting pass: steps of 8 that rounding can't confuse."""
    return (8 * (k % 30 + 1) / 255, 8 * (k // 30) / 255, 0.0)


def _seen(
    shown: list[Shown],
    view: str,
    size: int,
    pieces: list[trimesh.Trimesh],
    close: Close | None = None,
) -> dict[str, int]:
    """How many pixels of each file a view shows: drawn again unlit, one exact colour a file, and
    counted. A file at 0 is hidden in that view, inside or behind another."""
    meshes = [m for one in shown for m in one.meshes]
    owner = [k for k, one in enumerate(shown) for _ in one.meshes]
    shades = [_id_shade(k) for k in owner]
    pixels = _render_view(meshes, view, size, pieces, close, shades, flat=True).astype(int)
    found = {}
    for k, one in enumerate(shown):
        want = np.array([round(c * 255) for c in _id_shade(k)])
        found[one.name] = int((np.abs(pixels - want).max(axis=2) <= 3).sum())
    return found


def wrapped(draw: Any, text: str, room: float) -> list[str]:
    """The text in lines no wider than `room` px, broken between words, or within a word longer
    than a line. Nothing is cut."""
    lines: list[str] = []
    line = ""
    for word in text.split(" "):
        tried = f"{line} {word}" if line else word
        if draw.textlength(tried) <= room:
            line = tried
            continue
        if line:
            lines.append(line)
        line = word
        while draw.textlength(line) > room and len(line) > 1:
            fits = [k for k in range(len(line), 0, -1) if draw.textlength(line[:k]) <= room]
            cut = fits[0] if fits else 1
            lines.append(line[:cut])
            line = line[cut:]
    return [*lines, line] if line else lines


def _legend(shown: list[Shown], width: int) -> Image.Image:
    """What each mark and colour is: a strip under the sheet, its entries in rows, each entry's
    words wrapped within it."""
    across = max(1, width // LEGEND_ENTRY)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    room = min(LEGEND_ENTRY, width - 8) - LEGEND_TEXT - LEGEND_PAD
    entries = [wrapped(measure, one.said, room) for one in shown]
    rows = [entries[i : i + across] for i in range(0, len(entries), across)]
    heights = [LEGEND_LINE * max(len(e) for e in row) for row in rows]
    strip = Image.new("RGB", (width, sum(heights) + 8), "white")
    draw = ImageDraw.Draw(strip)
    for i, (one, lines) in enumerate(zip(shown, entries, strict=True)):
        x = 8 + (i % across) * LEGEND_ENTRY
        y = 4 + sum(heights[: i // across])
        draw.rectangle((x, y + 2, x + 12, y + 14), fill=one.rgb, outline=(20, 20, 20))
        for k, line in enumerate(lines):
            draw.text((x + LEGEND_TEXT, y + 2 + k * LEGEND_LINE), line, fill=(20, 20, 20))
    return strip


def _with_legend(sheet: Image.Image, shown: list[Shown]) -> Image.Image:
    strip = _legend(shown, sheet.width)
    whole = Image.new("RGB", (sheet.width, sheet.height + strip.height), "white")
    whole.paste(sheet, (0, 0))
    whole.paste(strip, (0, sheet.height))
    return whole


def _cuts(given: list[str]) -> list[cut_module.Cut]:
    return [cut_module.parse(text) for text in given]


def _say_seen(report: Report, name: str, seen: dict[str, dict[str, int]]) -> None:
    """Which files a row's views don't show at all, so a claim about them can't rest on it."""
    names = next(iter(seen.values()), {})
    hidden = [f for f in names if all(v.get(f, 0) == 0 for v in seen.values())]
    if hidden:
        report.line(
            f"{name}not seen in any view drawn: {', '.join(hidden)} (inside or behind another file)"
        )


def _cut_column(
    shown: list[Shown], cut: cut_module.Cut, size: int, report: Report, name: str
) -> tuple[np.ndarray, list[str]]:
    drawn = cut_module.draw([(s.name, s.parts, s.rgb) for s in shown], cut, size)
    for line in drawn.seconds:
        report.second(f"{name}{line}")
    for line in drawn.notes:
        report.line(f"{name}{line}")
    report.numbers.setdefault("cuts", {})[f"{name}{cut.label}"] = drawn.numbers
    marks = [(shown[k].mark, x, y) for k, x, y in drawn.marks]
    return _numbered(drawn.pixels, marks), [f"{name}{p}" for p in drawn.problems]


def run_variants(args: Any, report: Report) -> None:
    """A sheet for choosing between variants: a labelled row of each one's views, each file in its
    colour, numbered, and named in the legend under it."""
    variants = _variants(args.variants)
    closes = [_close(text) for text in args.close]
    cuts = _cuts(args.cuts)
    for _, files in variants:
        _not_both(files, args.test_pieces)
    pieces = _test_pieces(args.test_pieces, report)
    looks = _looks(args.looks)
    everyone = _shown([f for _, files in variants for f in files], looks)
    by_name = {s.name: s for s in everyone}
    marks = (TEST_PIECE_MARK,) if pieces else ()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    size = args.size
    rows = len(variants)
    columns = len(args.views) + len(closes) + len(cuts)
    sheet = Image.new("RGB", (columns * size, rows * size), "white")
    draw = ImageDraw.Draw(sheet)
    problems = []
    views: dict[str, Any] = {}
    seen_all: dict[str, Any] = {}
    for row, (name, files) in enumerate(variants):
        shown = [by_name[f] for f in dict.fromkeys(files)]
        meshes = [m for one in shown for m in one.meshes]
        shades = [one.shade for one in shown for _ in one.meshes]
        points = np.vstack([np.asarray(m.vertices) for m in [*meshes, *pieces]])
        seen: dict[str, dict[str, int]] = {}
        for k, close in enumerate(closes):
            column = len(args.views) + k
            drawn = _render_view(meshes, close.view, size, pieces, close, shades)
            problem, facts = close_problem(
                drawn, projected_mask([*meshes, *pieces], close, size), close.reach
            )
            views[f"{name} close {k + 1}"] = facts
            where = _marks(shown, close.view, size, points, close)
            numbered = _numbered(_stamped(drawn, *marks), where)
            sheet.paste(Image.fromarray(numbered), (column * size, row * size))
            draw.text((column * size + 8, row * size + 8), f"{name}: {close.label}", fill=(20,) * 3)
            report.second(_close_second(f"{name} close {k + 1}", facts))
            if problem:
                problems.append(f"{name} close {k + 1}: {problem}")
        for column, view in enumerate(args.views):
            pixels = _render_view(meshes, view, size, pieces, None, shades)
            problem, facts = outline_problem(
                pixels, projected_shape([*meshes, *pieces], view), size
            )
            views[f"{name} {view}"] = facts
            seen[view] = _seen(shown, view, size, pieces)
            numbered = _numbered(_stamped(pixels, *marks), _marks(shown, view, size, points))
            sheet.paste(Image.fromarray(numbered), (column * size, row * size))
            draw.text((column * size + 8, row * size + 8), f"{name}: {view}", fill=(20, 20, 20))
            report.second(
                f"{name} {view}: the mesh projects to {facts['projected_mm'][0]}x"
                f"{facts['projected_mm'][1]} mm, the picture's outline within "
                f"{facts.get('outline_differs', 'n/a')} of that shape"
            )
            if problem:
                problems.append(f"{name} {view}: {problem}")
        for k, cut in enumerate(cuts):
            column = len(args.views) + len(closes) + k
            pixels, found = _cut_column(shown, cut, size, report, f"{name} ")
            problems += found
            sheet.paste(Image.fromarray(pixels), (column * size, row * size))
            draw.text((column * size + 8, row * size + 8), f"{name}: {cut.label}", fill=(20,) * 3)
        seen_all[name] = seen
        _say_seen(report, f"{name}: ", seen)
        report.line(f"variant {name}: {', '.join(files)}")
    sheet = _with_legend(sheet, everyone)
    path = out / f"variants-{slug('+'.join(n for n, _ in variants))[:60]}-sheet.png"
    sheet.save(path)
    report.wrote(str(path))
    report.line(f"sheet of the variants: {path}")
    for one in everyone:
        report.line(f"drawn: {one.said}")
    report.numbers["views"] = views
    report.numbers["seen_px"] = seen_all
    report.numbers["files"] = [_file_numbers(one) for one in everyone]
    if problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        report.verdict("OK", f"{rows} variant(s), each picture the shape the mesh projects to")


def _file_numbers(one: Shown) -> dict[str, Any]:
    return {
        "number": one.number,
        "mark": one.mark,
        "file": one.name,
        "label": one.label,
        "colour": one.colour,
        "rgb": list(one.rgb),
    }


def run(args: Any, report: Report) -> None:
    if args.variants:
        if args.files:
            raise NoAnswer("name the files by --variant, or give them plainly, not both")
        run_variants(args, report)
        return
    if not args.files:
        raise NoAnswer("name a file to draw")
    _not_both(args.files, args.test_pieces)
    closes = [_close(text) for text in args.close]
    cuts = _cuts(args.cuts)
    shown = _shown(args.files, _looks(args.looks))
    parts = [one.whole for one in shown]
    for part in parts:
        report.notes += part.notes
    meshes = [m for one in shown for m in one.meshes]
    shades = [one.shade for one in shown for _ in one.meshes]
    pieces = _test_pieces(args.test_pieces, report)
    drawn_all = [*meshes, *pieces]
    points = np.vstack([np.asarray(m.vertices) for m in drawn_all])
    seen: dict[str, dict[str, int]] = {}
    marks = (TEST_PIECE_MARK,) if pieces else ()
    names = slug("+".join(Path(f).stem for f in args.files))[:60]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pictures: list[tuple[str, np.ndarray]] = []
    views: dict[str, Any] = {}
    problems = []
    for view in args.views:
        pixels = _render_view(meshes, view, args.size, pieces, None, shades)
        problem, facts = outline_problem(pixels, projected_shape(drawn_all, view), args.size)
        seen[view] = _seen(shown, view, args.size, pieces)
        pixels_marked = _numbered(_stamped(pixels, *marks), _marks(shown, view, args.size, points))
        path = out / f"{names}-{view}.png"
        Image.fromarray(pixels_marked).save(path)
        report.wrote(str(path))
        views[view] = facts
        pictures.append((view, pixels_marked))
        report.line(
            f"{view}: {path}, painted {facts['painted_share']:.1%}, "
            f"{facts['painted_px'][0]}x{facts['painted_px'][1]} px"
        )
        report.second(
            f"{view}: the mesh projects to {facts['projected_mm'][0]}x{facts['projected_mm'][1]} "
            f"mm, the picture's outline within {facts.get('outline_differs', 'n/a')} of that shape"
        )
        if problem:
            problems.append(f"{view}: {problem}")
    for z in args.section:
        pixels = _section_picture(parts[0], z, args.size)
        path = out / f"{names}-section-z{z:g}.png"
        Image.fromarray(pixels).save(path)
        report.wrote(str(path))
        share, _ = painted(pixels)
        section = slice_at(parts[0].manifold(), z)
        area = float(section.area)
        low, high = np.asarray(section.bounds).reshape(2, 2)
        scale = 0.9 * args.size / float(max(high - low))
        drawn = float((pixels[:, :, 0] < 250).sum()) / scale**2
        inner = islands(section)
        text = f"section z={z:g}: {path}, painted {share:.1%}, {len(section.geoms)} piece(s)"
        if inner:
            text += f", {len(inner)} of them island(s) inside an opening, drawn: " + ", ".join(
                f"{i.area:.1f} mm² at {i.representative_point().x:.2f},"
                f"{i.representative_point().y:.2f}"
                for i in inner[:5]
            )
        report.line(text)
        report.numbers.setdefault("sections", []).append(
            {"z": z, "pieces": len(section.geoms), "islands": len(inner), "mm2": round(area, 3)}
        )
        report.second(f"section z={z:g}: {area:.1f} mm² by the mesh, {drawn:.1f} mm² by pixels")
        if abs(drawn - area) > max(0.05 * area, 5 / scale**2):
            missing = _pieces_missing(section, pixels, args.size)
            problems.append(
                f"section z={z:g}: {drawn:.1f} mm² drawn, {area:.1f} mm² in the mesh"
                + (f": not drawn, {'; '.join(missing[:5])}" if missing else "")
            )
        pictures.append((f"z={z:g}", pixels))
    close_ups = []
    for k, close in enumerate(closes):
        pixels = _render_view(meshes, close.view, args.size, pieces, close, shades)
        problem, facts = close_problem(
            pixels, projected_mask(drawn_all, close, args.size), close.reach
        )
        marked = _numbered(
            _stamped(pixels, close.label, *marks),
            _marks(shown, close.view, args.size, points, close),
        )
        path = out / f"{names}-close-{k + 1}.png"
        Image.fromarray(marked).save(path)
        report.wrote(str(path))
        close_ups.append({"at": list(close.centre), "view": close.view, "file": str(path), **facts})
        pictures.append((close.label, marked))
        report.line(f"close {k + 1}: {path}, {close.label}, {facts['px_per_mm']} px per mm")
        report.second(_close_second(f"close {k + 1}", facts))
        if problem:
            problems.append(f"close {k + 1}: {problem}")
    for cut in cuts:
        pixels, found = _cut_column(shown, cut, args.size, report, "")
        problems += found
        path = out / f"{names}-{slug(cut.name)}.png"
        Image.fromarray(pixels).save(path)
        report.wrote(str(path))
        pictures.append((cut.label, pixels))
        report.line(f"{cut.label}: {path}")
    report.numbers["close_ups"] = close_ups
    report.numbers["views"] = views
    report.numbers["seen_px"] = seen
    report.numbers["files"] = [_file_numbers(one) for one in shown]
    for one in shown:
        report.line(f"drawn: {one.said}")
    _say_seen(report, "", seen)
    # More than one file needs its legend, which only a sheet carries.
    if len(pictures) > 1 or len(shown) > 1:
        sheet = _with_legend(_sheet(pictures, args.size), shown)
        path = out / f"{names}-sheet.png"
        sheet.save(path)
        report.wrote(str(path))
        report.line(f"sheet of all views: {path}")
    if problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        report.verdict("OK", f"{len(pictures)} picture(s), each the shape the mesh projects to")


def _sheet(pictures: list[tuple[str, np.ndarray]], size: int) -> Any:
    across = 2 if len(pictures) <= 4 else 3
    down = int(np.ceil(len(pictures) / across))
    sheet = Image.new("RGB", (across * size, down * size), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, pixels) in enumerate(pictures):
        x, y = (i % across) * size, (i // across) * size
        sheet.paste(Image.fromarray(pixels), (x, y))
        draw.text((x + 8, y + 8), label, fill=(20, 20, 20))
    return sheet
