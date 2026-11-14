"""Reading parts, inside the image: a STEP or STL file as a shape, meshes and polygons.

A STEP part has two meshes, one five times finer than the other. How far a figure can rest on
the mesh's chords is bounded by the chord tolerance times the curved surface's area, since no
surface is further from its mesh than that, and flat faces are exact. On a real part the two
meshes alone aren't a bound: BRepMesh isn't monotonic, and a coarser mesh can land closer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cadquery as cq
import manifold3d as m3
import numpy as np
import shapely
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.ShapeFix import ShapeFix_Solid
from OCP.TopoDS import TopoDS

#: (chord tolerance in mm, angle tolerance in radians).
COARSE = (0.01, 0.1)
FINE = (0.002, 0.05)
#: The least a volume is resolved to, in mm³, however well two figures agree.
VOLUME_FLOOR = 1e-3
#: The share of a volume two methods may differ by, whatever the mesh's band: arithmetic's
#: noise, kept small, or a real cut in a large part would fall inside it.
VOLUME_SHARE = 1e-5


class NoAnswer(Exception):
    """A method couldn't run, so the command can't say. Its message is the reason."""


def _tessellated(shape: Any, tolerance: tuple[float, float]) -> trimesh.Trimesh:
    # Clean, or a mesh already on the shape is reused and the two meshes come out the same.
    BRepTools.Clean_s(shape.wrapped)
    # Absolute tolerances, by OCCT directly: CadQuery's `tessellate` meshes with isRelative=True,
    # so its tolerance is a share of each edge's size and not the mm it looks like. On a real
    # part that read as a 0.03 mm false gap. A huge tolerance here makes it reuse this mesh.
    BRepMesh_IncrementalMesh(shape.wrapped, tolerance[0], False, tolerance[1], True)
    vertices, triangles = shape.tessellate(1e9)
    return trimesh.Trimesh(
        np.array([v.toTuple() for v in vertices], dtype=float),
        np.array(triangles, dtype=np.int64),
        process=True,
    )


def to_manifold(mesh: trimesh.Trimesh, name: str = "the mesh") -> Any:
    built = m3.Manifold(
        m3.Mesh64(
            vert_properties=np.asarray(mesh.vertices, dtype=np.float64),
            tri_verts=np.asarray(mesh.faces, dtype=np.uint64),
        )
    )
    if built.status() != m3.Error.NoError or built.is_empty():
        raise NoAnswer(
            f"{name} isn't a closed surface ({built.status()}, watertight "
            f"{bool(mesh.is_watertight)}, {len(mesh.faces)} triangles), so no volume or "
            "overlap can be read from it"
        )
    return built


class Part:
    """A file's geometry, or one solid of it."""

    def __init__(
        self,
        name: str,
        shape: Any = None,
        mesh: trimesh.Trimesh | None = None,
        notes: list[str] | None = None,
    ) -> None:
        self.name = name
        self.shape = shape
        self.notes = notes or []
        self._mesh = mesh
        self._coarse: trimesh.Trimesh | None = None
        self._fine: trimesh.Trimesh | None = mesh
        self._manifolds: dict[bool, Any] = {}

    @property
    def is_brep(self) -> bool:
        return self.shape is not None

    def meshes(self) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
        """(coarse, fine). An STL has one mesh: its own."""
        if self._mesh is not None:
            return self._mesh, self._mesh
        if self._fine is None or self._coarse is None:
            self._coarse = _tessellated(self.shape, COARSE)
            self._fine = _tessellated(self.shape, FINE)
        return self._coarse, self._fine

    def fine(self) -> trimesh.Trimesh:
        return self.meshes()[1]

    def chord_band(self) -> float:
        """The most a volume can differ from its fine mesh's, in mm³: flat faces are exact."""
        if not self.is_brep:
            return 0.0
        curved = sum(f.Area() for f in self.shape.Faces() if f.geomType() != "PLANE")
        return FINE[0] * float(curved)

    def manifold(self, fine: bool = True) -> Any:
        if fine not in self._manifolds:
            mesh = self.meshes()[1 if fine else 0]
            self._manifolds[fine] = to_manifold(mesh, self.name)
        return self._manifolds[fine]

    def pieces(self) -> list[Part]:
        """Each solid of a STEP file, or each closed body of an STL, as its own part."""
        if self.is_brep:
            solids = self.shape.Solids()
            if len(solids) <= 1:
                return [self]
            return [Part(f"{self.name}#{i + 1}", shape=s) for i, s in enumerate(solids)]
        assert self._mesh is not None
        bodies = self._mesh.split(only_watertight=False)
        if len(bodies) <= 1:
            return [self]
        # trimesh fills a body's small holes as it splits, with networkx (mqucifer/print-shop#122):
        # each body is then read filled, which the file isn't, so the reply says so.
        filled = (
            f"{self.name}'s mesh has edges with one face, an open seam: split into its "
            f"{len(bodies)} bodies, each small hole trimesh could fill was filled, and each body "
            "is read so. The file is as it was"
        )
        if not self._mesh.is_watertight and filled not in self.notes:
            self.notes.append(filled)
        return [Part(f"{self.name}#{i + 1}", mesh=b) for i, b in enumerate(bodies)]

    def extent(self) -> np.ndarray:
        """Where the part is, to report: the fine mesh's box, closer than OCCT's padded one."""
        return np.asarray(self.fine().bounds)

    def bounds(self) -> np.ndarray:
        """A box the part is surely inside, to rule out a pair: OCCT's, which may be padded."""
        if self.is_brep:
            box = self.shape.BoundingBox()
            return np.array([[box.xmin, box.ymin, box.zmin], [box.xmax, box.ymax, box.zmax]])
        assert self._mesh is not None
        return np.asarray(self._mesh.bounds)


def _solids_from_shells(shape: Any) -> tuple[Any, int]:
    """A solid from each closed shell: CadQuery 2.8 exports a shelled solid as its bare shell."""
    if shape.Solids():
        return shape, 0
    closed = [s for s in shape.Shells() if BRep_Tool.IsClosed_s(s.wrapped)]
    if not closed:
        return shape, 0
    made = [
        cq.Shape.cast(ShapeFix_Solid().SolidFromShell(TopoDS.Shell_s(s.wrapped))) for s in closed
    ]
    return (made[0] if len(made) == 1 else cq.Compound.makeCompound(made)), len(made)


def load(path: str) -> Part:
    found = Path(path)
    if not found.is_file():
        raise NoAnswer(f"no file {path} in the working directory")
    suffix = found.suffix.lower()
    if suffix in (".step", ".stp"):
        values = cq.importers.importStep(str(found)).vals()
        if not values:
            raise NoAnswer(f"{path} read as nothing")
        shape = values[0] if len(values) == 1 else cq.Compound.makeCompound(values)
        shape, made = _solids_from_shells(shape)
        notes = [f"{path} holds {made} closed shell(s) and no solid: read as solids"] * bool(made)
        return Part(found.name, shape=shape, notes=notes)
    if suffix == ".stl":
        mesh: Any = trimesh.load(str(found), force="mesh", process=True)
        return Part(found.name, mesh=mesh)
    raise NoAnswer(f"reads STEP or STL, not {suffix or 'a file with no suffix'}")


def polygons(contours: list[Any]) -> Any:
    """Manifold's contours as Shapely: counter-clockwise is material, clockwise a hole in it."""
    outers: list[Any] = []
    holes: list[Any] = []
    for contour in contours:
        points = np.asarray(contour, dtype=float)
        if len(points) < 3:
            continue
        ring = shapely.LinearRing(points)
        (outers if ring.is_ccw else holes).append(points)
    shells = sorted((shapely.Polygon(o) for o in outers), key=lambda p: p.area)
    owned: list[list[Any]] = [[] for _ in shells]
    for hole in holes:
        where = shapely.Point(hole[0])
        for i, shell in enumerate(shells):
            if shell.contains(where):
                owned[i].append(hole)
                break
    built = [
        shapely.Polygon(shell.exterior.coords, holes=inner)
        for shell, inner in zip(shells, owned, strict=True)
    ]
    return shapely.MultiPolygon(built)


def slice_at(manifold: Any, z: float) -> Any:
    """The mesh's material on the plane at height z, as Shapely polygons."""
    return polygons(manifold.slice(z).to_polygons())


def occt_section_area(shape: Any, z: float) -> float | None:
    """The exact surface's area on that plane, by OCCT, or None if it returned no faces."""
    box = shape.BoundingBox()
    size = 2 * max(box.xlen, box.ylen) + 10
    centre = cq.Vector((box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2, z)
    plane = cq.Face.makePlane(size, size, centre, cq.Vector(0, 0, 1))
    faces = shape.intersect(plane).Faces()
    return sum(f.Area() for f in faces) if faces else None


def section_checked(part: Part, z: float) -> tuple[Any, float, float | None, str]:
    """A section by the mesh, and its area by a different method: (polygons, area, other, how)."""
    found = slice_at(part.manifold(), z)
    if part.is_brep:
        return found, float(found.area), occt_section_area(part.shape, z), "OCCT section"
    # An STL has no exact surface, so its triangles are cut one by one instead.
    lines = trimesh.intersections.mesh_plane(  # type: ignore[no-untyped-call]
        part.fine(), (0, 0, 1), (0, 0, z)
    )
    # Rounded, or two triangles' ends of one edge may not meet to the last digit.
    faces = list(
        shapely.polygonize([shapely.LineString(np.round(s[:, :2], 7)) for s in lines]).geoms
    )
    # polygonize gives each region once, a hole's inside as well as the material round it, so
    # a region is material if an odd number of outlines, its own among them, enclose it.
    outlines = [shapely.Polygon(f.exterior) for f in faces]
    area = 0.0
    for region in faces:
        inside = region.representative_point()
        if sum(o.contains(inside) for o in outlines) % 2 == 1:
            area += float(region.area)
    return found, float(found.area), area, "triangles cut one by one"


def flat_heights(mesh: trimesh.Trimesh, least: float = 0.5) -> list[dict[str, float]]:
    """Each height with level faces, and their area facing up and facing down, in mm²."""
    normals = np.asarray(mesh.face_normals)
    level = np.abs(normals[:, 2]) > 0.9999
    heights = np.round(np.asarray(mesh.triangles_center)[level, 2], 3)
    areas = np.asarray(mesh.area_faces)[level]
    facing = normals[level, 2]
    found = []
    for z in np.unique(heights):
        here = heights == z
        up = float(areas[here & (facing > 0)].sum())
        down = float(areas[here & (facing < 0)].sum())
        if up + down >= least:
            found.append({"z": float(z), "up": round(up, 2), "down": round(down, 2)})
    return found


def hits(manifold: Any, origin: np.ndarray, direction: np.ndarray, length: float) -> list[float]:
    """Each surface a ray from `origin` meets, in mm, nearest first.

    `RayHit.distance` is a fraction of the segment, not a length: the second check caught
    that the first time it ran. So the distance here is measured from the hit's position.
    """
    start = np.asarray(origin, dtype=float)
    end = start + np.asarray(direction, dtype=float) * length
    found = manifold.ray_cast(tuple(map(float, start)), tuple(map(float, end)))
    return [float(np.linalg.norm(np.asarray(h.position) - start)) for h in found]


def diagonal(mesh: trimesh.Trimesh) -> float:
    low, high = np.asarray(mesh.bounds)
    return float(np.linalg.norm(high - low))
