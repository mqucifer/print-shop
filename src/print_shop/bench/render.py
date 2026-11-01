"""Renders from the standard views, offscreen with VTK and OSMesa, for the model and the Sponsor.

Each picture is checked against arithmetic that never touched the renderer: the parts' mesh is
projected along the view by NumPy, and the painted pixels must cover the same outline shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import trimesh
import vtk
from PIL import Image, ImageDraw
from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy

from .report import Report, slug
from .shapes import NoAnswer, Part, load, slice_at

COLOURS = [
    (0.55, 0.65, 0.80),
    (0.90, 0.65, 0.40),
    (0.55, 0.78, 0.55),
    (0.75, 0.60, 0.82),
    (0.85, 0.80, 0.45),
]
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


def _actors(data: Any, colour: tuple[float, float, float], renderer: Any) -> None:
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(data)
    mapper.SetResolveCoincidentTopologyToPolygonOffset()
    body = vtk.vtkActor()
    body.SetMapper(mapper)
    body.GetProperty().SetColor(*colour)
    body.GetProperty().SetInterpolationToFlat()
    renderer.AddActor(body)
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
    outline.GetProperty().SetColor(0, 0, 0)
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


def _render_view(parts: list[trimesh.Trimesh], view: str, size: int) -> np.ndarray:
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(1, 1, 1)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(size, size)
    window.AddRenderer(renderer)
    for k, mesh in enumerate(parts):
        _actors(_poly(mesh), COLOURS[k % len(COLOURS)], renderer)
    position, up = VIEWS[view]
    right, top, along = _axes(view)
    points = np.vstack([np.asarray(m.vertices) for m in parts])
    across, high = points @ right, points @ top
    # Aimed at the middle of the outline and scaled to it, so a long part isn't clipped.
    centre = (
        right * (across.min() + across.max()) / 2
        + top * (high.min() + high.max()) / 2
        + along * float((points @ along).mean())
    )
    camera = renderer.GetActiveCamera()
    camera.ParallelProjectionOn()
    camera.SetFocalPoint(*centre)
    camera.SetPosition(*(centre + 100 * np.array(position)))
    camera.SetViewUp(*up)
    renderer.ResetCamera()
    camera.SetFocalPoint(*centre)
    camera.SetParallelScale(FILL * max(float(np.ptp(across)), float(np.ptp(high))) / 2)
    renderer.ResetCameraClippingRange()
    window.Render()
    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(window)
    grab.Update()
    data = vtk_to_numpy(grab.GetOutput().GetPointData().GetScalars())
    pixels = data.reshape(size, size, -1)[::-1]
    window.Finalize()
    return np.asarray(pixels[:, :, :3], dtype=np.uint8)


def _section_picture(part: Part, z: float, size: int) -> np.ndarray:
    """A plane section drawn from Shapely's polygons, material dark on white."""
    section = slice_at(part.manifold(), z)
    low, high = np.asarray(section.bounds).reshape(2, 2)
    scale = 0.9 * size / float(max(high - low))
    margin = (size - (high - low) * scale) / 2

    def pixel(ring: Any) -> list[tuple[float, float]]:
        xy = (np.asarray(ring.coords) - low) * scale + margin
        return [(float(x), float(size - y)) for x, y in xy]

    picture = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(picture)
    for poly in section.geoms:
        draw.polygon(pixel(poly.exterior), fill=(70, 70, 80))
        for hole in poly.interiors:
            draw.polygon(pixel(hole), fill=(255, 255, 255))
    return np.asarray(picture)


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


def run_variants(args: Any, report: Report) -> None:
    """A sheet for choosing between variants: a labelled row of each one's views."""
    variants = _variants(args.variants)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    size = args.size
    rows = len(variants)
    sheet = Image.new("RGB", (len(args.views) * size, rows * size), "white")
    draw = ImageDraw.Draw(sheet)
    problems = []
    views: dict[str, Any] = {}
    for row, (name, files) in enumerate(variants):
        parts = [load(path) for path in files]
        meshes = [m for part in parts for m in (p.fine() for p in part.pieces())]
        for column, view in enumerate(args.views):
            pixels = _render_view(meshes, view, size)
            problem, facts = outline_problem(pixels, projected_shape(meshes, view), size)
            views[f"{name} {view}"] = facts
            sheet.paste(Image.fromarray(pixels), (column * size, row * size))
            draw.text((column * size + 8, row * size + 8), f"{name}: {view}", fill=(20, 20, 20))
            report.second(
                f"{name} {view}: the mesh projects to {facts['projected_mm'][0]}x"
                f"{facts['projected_mm'][1]} mm, the picture's outline within "
                f"{facts.get('outline_differs', 'n/a')} of that shape"
            )
            if problem:
                problems.append(f"{name} {view}: {problem}")
        report.line(f"variant {name}: {', '.join(files)}")
    path = out / f"variants-{slug('+'.join(n for n, _ in variants))[:60]}-sheet.png"
    sheet.save(path)
    report.wrote(str(path))
    report.line(f"sheet of the variants: {path}")
    report.numbers["views"] = views
    if problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        report.verdict("OK", f"{rows} variant(s), each picture the shape the mesh projects to")


def run(args: Any, report: Report) -> None:
    if args.variants:
        if args.files:
            raise NoAnswer("name the files by --variant, or give them plainly, not both")
        run_variants(args, report)
        return
    if not args.files:
        raise NoAnswer("name a file to draw")
    parts = [load(path) for path in args.files]
    for part in parts:
        report.notes += part.notes
    meshes = [m for part in parts for m in (p.fine() for p in part.pieces())]
    names = slug("+".join(Path(f).stem for f in args.files))[:60]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pictures: list[tuple[str, np.ndarray]] = []
    views: dict[str, Any] = {}
    problems = []
    for view in args.views:
        pixels = _render_view(meshes, view, args.size)
        problem, facts = outline_problem(pixels, projected_shape(meshes, view), args.size)
        path = out / f"{names}-{view}.png"
        Image.fromarray(pixels).save(path)
        report.wrote(str(path))
        views[view] = facts
        pictures.append((view, pixels))
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
        area = float(slice_at(parts[0].manifold(), z).area)
        low, high = np.asarray(slice_at(parts[0].manifold(), z).bounds).reshape(2, 2)
        scale = 0.9 * args.size / float(max(high - low))
        drawn = float((pixels[:, :, 0] < 250).sum()) / scale**2
        report.line(f"section z={z:g}: {path}, painted {share:.1%}")
        report.second(f"section z={z:g}: {area:.1f} mm² by the mesh, {drawn:.1f} mm² by pixels")
        if abs(drawn - area) > max(0.05 * area, 5 / scale**2):
            problems.append(f"section z={z:g}: {drawn:.1f} mm² drawn, {area:.1f} mm² in the mesh")
        pictures.append((f"z={z:g}", pixels))
    report.numbers["views"] = views
    if len(pictures) > 1:
        sheet = _sheet(pictures, args.size)
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
