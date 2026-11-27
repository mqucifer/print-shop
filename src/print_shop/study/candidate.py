"""A candidate a settings study reads: a part's walls, shells and infill, its regions, its pose
and its wall order, as the model asks for them and as code bounds them (mqucifer/print-shop#339).

**What may be asked**, each only as far as both sides read it:
- the part's own `walls`, `top`, `bottom`, `infill`, `pattern` and `infill_angle`
  (`bench.printed.Settings`), over the profile's own. The infill is from `infill.LEAST` to
  `infill.MOST`, and the pattern one `load` reads as printed (`infill.LINES`): past those,
  `load` has no answer, so a candidate there would read nothing. The line width and the layer
  height are the profile's: they're the whole part's, and the time they cost isn't the study's
  to trade;
- its wall order, Orca's `wall_sequence`, for finish only: `load` reads walls by their count,
  whatever order they're laid in;
- its pose: one of the study's poses, where that changes which way its layers run against its
  loads: each of `ingest.orientation.AXIS_ALIGNED`'s that fits the bed, turned about its up axis
  where it must by `fit-bed`'s own turn (mqucifer/print-shop#351). `load` is told the layers'
  axis the pose gives;
- regions of their own (`bench.printed.Region`): a box, a cylinder or a band of layers as
  `printed.read` takes them, or round the part's own joints (`slicer.joints.round_joints`): a
  cylinder round each bolt's hole, and round each fit and latch a box, or, with `faces`, a mesh
  made from the part's own faces in that box, so the region follows them (`faces_mesh`).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from print_shop.bench import infill
from print_shop.bench.assembly import Assembly
from print_shop.bench.printed import Box, Mesh, Printed, Region, Settings, as_json, read
from print_shop.ingest.orientation import AXIS_ALIGNED
from print_shop.setup.context import PartContext
from print_shop.slicer.joints import round_joints
from print_shop.slicer.regions import (
    Bed,
    Pose,
    degrees_said,
    fit_of,
    regions_meet,
    turned_to_fit,
    walls_depth,
    walls_held,
)

__all__ = [
    "DEFAULTS",
    "PATTERNS",
    "WALL_SEQUENCES",
    "Candidate",
    "Shaper",
    "faces_mesh",
    "own_settings",
    "poses_said",
    "study_poses",
    "unfit",
]

#: The candidate the study reads first, by code: the profile's own settings, in the part's pose.
DEFAULTS = "defaults"
#: The patterns `load` reads as printed: each as Orca lays its lines, set by a published test.
PATTERNS = tuple(sorted(infill.LINES))
#: Orca 2.4.2's wall orders (`wall_sequence`), with its own tooltip for each, read from the
#: installed binary's strings on 2026-11-26.
WALL_SEQUENCES = {
    "inner wall/outer wall": (
        "Use Inner/Outer for best overhangs. This is because the overhanging walls can adhere "
        "to a neighbouring perimeter while printing. However, this option results in slightly "
        "reduced surface quality as the external perimeter is deformed by being squashed to the "
        "internal perimeter."),
    "inner-outer-inner wall": (
        "Use Inner/Outer/Inner for the best external surface finish and dimensional accuracy as "
        "the external wall is printed undisturbed from an internal perimeter. However, overhang "
        "performance will reduce as there is no internal perimeter to print the external wall "
        "against. This option requires a minimum of 3 walls to be effective"),
    "outer wall/inner wall": (
        "Use Outer/Inner for the same external wall quality and dimensional accuracy benefits "
        "of Inner/Outer/Inner option. However, the Z seams will appear less consistent as the "
        "first extrusion of a new layer starts on a visible surface."),
}
#: The counts a candidate may give, as `setup.bounds` holds the print loop's (walls 1 to 10,
#: shells 3 to 15). The study keeps under them: every wall and shell is plastic `load` reads.
WALLS = (1, 10)
SHELLS = (3, 15)
#: The settings a candidate gives the part, and the keys of a region's own besides its shape.
OWN = ("walls", "top", "bottom", "infill", "pattern", "infill_angle")
_REGION = frozenset({"name", "why", "box", "cylinder", "layers", *OWN})
_JOINT = frozenset({"joints", "margin", "faces", "why", *OWN})
#: A face mesh reaches this far out of the part's face, so the face's own layer is inside it,
#: in mm. Only what of the part lies inside a region takes its settings.
PROUD = 0.5
#: A face mesh's faces are cut to this size, in mm, so its edge follows the box's to within it.
EDGE_MM = 1.0
#: A turn asked is one of the poses where each of its angles is within this of the pose's, in
#: degrees: the poses are said to a millionth of a degree, and a turn about the up axis that
#: fits the bed needn't be a round one.
SAME_TURN = 1e-4


@dataclass(frozen=True)
class Candidate:
    """One set of settings a study reads."""

    name: str
    #: What it gives the part over the profile's own: only the settings it changes.
    settings: Settings
    regions: tuple[Region, ...] = ()
    turn: tuple[float, float, float] = AXIS_ALIGNED[0]
    #: Orca's `wall_sequence`, or None for the profile's.
    wall_sequence: str | None = None
    why: str = ""
    #: Each mesh region's file, by its region's name: made in the study's folder.
    meshes: dict[str, Path] = field(default_factory=dict)
    #: What was found before slicing: each region with walls of its own, how deep it lies in
    #: the part and the walls that holds (`regions.walls_held`).
    notes: tuple[str, ...] = ()

    def key(self) -> str:
        """What it asks, as one string: two candidates that ask the same are read once."""
        return json.dumps({
            "print": as_json(Printed(self.settings, self.regions)),
            "turn": list(self.turn), "wall_sequence": self.wall_sequence,
        }, sort_keys=True)

    def printed(self) -> Printed:
        return Printed(self.settings, self.regions)

    def said(self) -> str:
        """What it asks, in a few words."""
        words = [self.settings.said() if self.settings.given() else "the profile's own"]
        if self.wall_sequence:
            words.append(f"walls laid {self.wall_sequence}")
        if self.turn != AXIS_ALIGNED[0]:
            words.append("turned " + degrees_said(self.turn))
        words += [f"region {r.said()}" for r in self.regions]
        return "; ".join(words)

    def plain(self) -> dict[str, Any]:
        return {"name": self.name, "why": self.why, "said": self.said(),
                "print": as_json(self.printed()), "turn": list(self.turn),
                "wall_sequence": self.wall_sequence, "notes": list(self.notes)}


def _number(raw: Any, where: str, low: float, high: float, whole: bool = False) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"{where} is a number, not {raw!r}")
    if whole and raw != int(raw):
        raise ValueError(f"{where} is a whole number, not {raw!r}")
    if not low <= raw <= high:
        raise ValueError(f"{where} is {low:g} to {high:g}, not {raw!r}")
    return float(raw)


def own_settings(raw: dict[str, Any], where: str) -> Settings:
    """The settings a candidate or a region asks for, within what both sides read.

    Raises:
        ValueError: Naming the setting that's out of bounds, and the bound.
    """
    got: dict[str, Any] = {}
    if "walls" in raw:
        got["walls"] = int(_number(raw["walls"], f"{where}'s walls", *WALLS, whole=True))
    for name in ("top", "bottom"):
        if name in raw:
            got[name] = int(_number(raw[name], f"{where}'s {name}", *SHELLS, whole=True))
    if "infill" in raw:
        got["infill"] = _number(raw["infill"], f"{where}'s infill", infill.LEAST, infill.MOST)
    if "pattern" in raw:
        if raw["pattern"] not in PATTERNS:
            raise ValueError(f"{where}'s pattern is one load reads as printed, "
                             f"{', '.join(PATTERNS)}: not {raw['pattern']!r}")
        got["pattern"] = str(raw["pattern"])
    if "infill_angle" in raw:
        got["infill_angle"] = _number(raw["infill_angle"], f"{where}'s infill_angle", -360, 360)
    return Settings(**got)


def faces_mesh(mesh: trimesh.Trimesh, box: Box, depth: float) -> trimesh.Trimesh | None:
    """A closed mesh made from the part's own faces inside `box`: the faces cut to the box, each
    moved `PROUD` out of the part along its corners' normals and `depth` into it, and joined
    round its edge. So a region round a joint's faces follows them, where a box round them
    holds whatever of the part is in its corners. None where no face lies in the box, or the
    faces there don't make a closed mesh."""
    low, high = np.asarray(box[0], dtype=float), np.asarray(box[1], dtype=float)
    corners = mesh.vertices[mesh.faces]
    meets = np.all((corners.max(axis=1) >= low) & (corners.min(axis=1) <= high), axis=1)
    if not meets.any():
        return None
    # The faces that meet the box, cut small, and those of them whose middle is in it: so the
    # mesh's edge lies within `EDGE_MM` of the box's.
    vertices, faces = trimesh.remesh.subdivide_to_size(  # type: ignore[no-untyped-call]
        mesh.vertices, mesh.faces[meets], max_edge=EDGE_MM, max_iter=12)
    middles = vertices[faces].mean(axis=1)
    held = np.all((middles >= low) & (middles <= high), axis=1)
    if not held.any():
        return None
    patch = trimesh.Trimesh(vertices, faces[held], process=True)
    # Each corner's normal, the mean of its faces' by their area: numpy only.
    normals = np.zeros_like(patch.vertices)
    weighted = patch.face_normals * patch.area_faces[:, None]
    for k in range(3):
        np.add.at(normals, patch.faces[:, k], weighted)
    normals /= np.maximum(np.linalg.norm(normals, axis=1), 1e-12)[:, None]
    count = len(patch.vertices)
    outer = patch.vertices + PROUD * normals
    inner = patch.vertices - depth * normals
    faces = [patch.faces, patch.faces[:, ::-1] + count]
    unique, counts = np.unique(patch.edges_sorted, axis=0, return_counts=True)
    if np.any(counts > 2):
        return None
    edge = set(map(tuple, unique[counts == 1].tolist()))
    sides = [step for a, b in patch.edges.tolist() if (min(a, b), max(a, b)) in edge
             for step in ((b, a, a + count), (b, a + count, b + count))]
    if sides:
        faces.append(np.array(sides))
    solid = trimesh.Trimesh(np.vstack([outer, inner]), np.vstack(faces), process=True)
    if not solid.is_watertight:
        return None
    if solid.volume < 0:
        solid.invert()
    return solid


@dataclass
class Shaper:
    """Makes a candidate from what the model asks: the part's mesh and joints, the poses it may
    take, and the folder its regions' meshes are written in."""

    mesh: trimesh.Trimesh
    folder: Path
    context: PartContext | None = None
    assembly: Assembly | None = None
    part: str | None = None
    poses: tuple[tuple[float, float, float], ...] = AXIS_ALIGNED
    #: The profile's line width and layer height, which set a bead's spacing.
    line_width: float = 0.45
    layer_height: float = 0.2
    made: int = 0

    def joints(self) -> list[tuple[str, str]]:
        """The part's joints a region can be put round, as their regions' names and in words,
        at no margin (`round_joints`), and what couldn't be placed."""
        found = round_joints(Settings(walls=1), 0.0, self.mesh, self.context, self.assembly,
                             self.part)
        return ([(r.name, r.why or r.said()) for r in found.regions]
                + [("", note) for note in found.notes])

    def candidate(self, name: str, args: dict[str, Any]) -> Candidate:
        """A candidate from the model's arguments to `read`.

        Raises:
            ValueError: Naming what can't be read, and what it takes.
        """
        settings = own_settings(args.get("settings") or {}, name)
        if extra := set(args.get("settings") or {}) - set(OWN):
            raise ValueError(f"{name}'s settings are {', '.join(OWN)}: not {', '.join(extra)}")
        sequence = args.get("wall_sequence")
        if sequence is not None and sequence not in WALL_SEQUENCES:
            raise ValueError(f"wall_sequence is one of {', '.join(WALL_SEQUENCES)}: not "
                             f"{sequence!r}")
        raw_turn = args.get("turn") or list(self.poses[0])
        asked = tuple(float(v) for v in raw_turn)
        turn = next((p for p in self.poses if len(asked) == 3 and all(
            abs(a - b) <= SAME_TURN for a, b in zip(asked, p, strict=True))), None)
        if turn is None:
            raise ValueError(f"turn {raw_turn!r} isn't one of the poses: "
                             + poses_said(self.poses))
        regions: list[Region] = []
        meshes: dict[str, Path] = {}
        for k, item in enumerate(args.get("regions") or (), 1):
            if not isinstance(item, dict):
                raise ValueError(f"region {k} is an object")
            if "joints" in item:
                made, files = self._round_joints(item, k)
                regions += made
                meshes.update(files)
            else:
                regions.append(self._region(item, k))
        # Read again whole, so the names are each a region's own and the bands don't overlap.
        whole = read(as_json(Printed(Settings(), tuple(regions))), f"{name}'s regions")
        notes = self._depths(whole.regions, (turn[0], turn[1], turn[2]))
        return Candidate(name, settings, whole.regions, (turn[0], turn[1], turn[2]),
                         sequence, str(args.get("why", "")), meshes, notes)

    def _depths(self, regions: Sequence[Region], turn: tuple[float, float, float]
                ) -> tuple[str, ...]:
        """Each region with walls of its own, how deep it lies in the part and the walls that
        holds, before Orca slices it (mqucifer/print-shop#357).

        Raises:
            ValueError: Where a region asks more walls than its depth holds: Orca would lay
                fewer, the reader would find it, and the candidate would fail as not as sliced,
                so no slice is spent on it. The refusal says what would fit.
        """
        layers = Pose.of(self.mesh, turn).layers()
        if layers is None:
            return ()
        notes, refused = [], []
        for region in regions:
            asked = region.settings.walls
            if asked is None:
                continue
            need = walls_depth(asked, self.line_width, self.layer_height)
            depth = walls_held(self.mesh, region, layers, self.folder, self.line_width,
                               self.layer_height)
            if depth is None:
                continue
            # Orca doesn't wall the border between two regions, so one that meets another
            # holds walls its own section doesn't say: it's noted, and the reader judges it.
            meets = [r.name for r in regions if r is not region
                     and regions_meet(region, r, layers, self.folder)]
            if asked > depth.holds and meets:
                notes.append(f"{depth.said()}, but it meets {', '.join(meets)}, whose border "
                             "with it Orca doesn't wall: it may hold more, so it's sliced and "
                             "the layers say")
            elif asked > depth.holds:
                fewer = (f"ask {depth.holds} or fewer" if depth.holds else
                         "give it no walls of its own")
                refused.append(
                    f"region {region.name} asks {asked} walls, but it {depth.held()}: {fewer}, "
                    f"or make it at least {need:.2f} mm deep in the part (a joint region's "
                    "margin, a cylinder's radius past its hole)")
            else:
                notes.append(depth.said())
        if refused:
            raise ValueError(
                "; ".join(refused) + ". A region with walls of its own is walled round its own "
                "outline from every side, one wall for each two bead spacings of its depth: "
                "Orca would lay fewer, and load would read them as asked, so it isn't sliced")
        return tuple(notes)

    def _region(self, item: dict[str, Any], k: int) -> Region:
        if extra := set(item) - _REGION:
            raise ValueError(f"region {k} takes a shape (box, cylinder, layers, or joints) and "
                             f"{', '.join(OWN)}: not {', '.join(sorted(extra))}")
        own = own_settings(item, f"region {k}")
        if not own.given():
            raise ValueError(f"region {k} sets nothing of its own")
        shape = {s: item[s] for s in ("box", "cylinder", "layers") if s in item}
        region = read({"regions": [{"name": item.get("name", f"region-{k}"), **shape,
                                    **own.given(), "why": str(item.get("why", ""))}]},
                      f"region {k}").regions[0]
        return region

    def _round_joints(self, item: dict[str, Any], k: int) -> tuple[list[Region], dict[str, Path]]:
        if extra := set(item) - _JOINT:
            raise ValueError(f"region {k} round joints takes joints, margin, faces, why and "
                             f"{', '.join(OWN)}: not {', '.join(sorted(extra))}")
        own = own_settings(item, f"region {k}")
        if not own.given():
            raise ValueError(f"region {k} sets nothing of its own")
        margin = _number(item.get("margin", 3.0), f"region {k}'s margin", 0.0, 20.0)
        found = round_joints(own, margin, self.mesh, self.context, self.assembly, self.part)
        wanted = item["joints"]
        names = [r.name for r in found.regions]
        if wanted != "all":
            if not isinstance(wanted, list) or not all(isinstance(w, str) for w in wanted):
                raise ValueError(f"region {k}'s joints are \"all\" or a list of their names")
            missing = [w for w in wanted if w not in names]
            if missing:
                raise ValueError(f"region {k}: no joint {', '.join(missing)}: the part's are "
                                 f"{', '.join(names) or 'none'}")
        chosen = [r for r in found.regions if wanted == "all" or r.name in wanted]
        if not chosen:
            raise ValueError(f"region {k}: the part has no joint a region can be put round"
                             + (f" ({'; '.join(found.notes)})" if found.notes else ""))
        why = str(item.get("why", ""))
        out: list[Region] = []
        files: dict[str, Path] = {}
        for region in chosen:
            region = replace(region, why="; ".join(w for w in (region.why, why) if w))
            if item.get("faces") and isinstance(region.shape, tuple):
                solid = faces_mesh(self.mesh, region.shape, max(margin, 1.0))
                if solid is not None:
                    self.made += 1
                    self.folder.mkdir(parents=True, exist_ok=True)
                    path = self.folder / f"{region.name}-faces-{self.made}.stl"
                    solid.export(path)
                    files[region.name] = path
                    region = replace(region, shape=Mesh(path.name),
                                     why=f"{region.why}; a mesh of the part's own faces there, "
                                         f"{max(margin, 1.0):g} mm deep")
            out.append(region)
        return out, files


def _up(mesh: trimesh.Trimesh, turn: tuple[float, float, float]) -> tuple[float, ...]:
    """Which way each of the part's own axes points up in a pose: two poses with the same lay
    the part alike, one turned about its up axis from the other, as (0,180,0) and (180,0,180)
    do. The turn's rows are its own axes on the plate, and the third is up."""
    return tuple(round(float(v), 6) + 0.0 for v in np.asarray(Pose.of(mesh, turn).matrix)[2, :3])


def study_poses(mesh: trimesh.Trimesh, turn: tuple[float, float, float],
                bed: Bed) -> tuple[tuple[float, float, float], ...]:
    """The poses a study may read the part in: its own `turn` first, then each other of
    `AXIS_ALIGNED`'s that fits the bed, turned about its up axis by `fit-bed`'s own turn where
    it must (`regions.turned_to_fit`: none where it fits as laid). One that fits no turn isn't
    offered: Orca would refuse it."""
    found = [turn]
    laid_so = {_up(mesh, turn)}
    for laid in AXIS_ALIGNED:
        if _up(mesh, laid) in laid_so:
            continue
        laid_so.add(_up(mesh, laid))
        fit = turned_to_fit(mesh, (laid[0], laid[1]), bed)
        if fit.fits:
            found.append(fit.turn)
    return tuple(found)


def unfit(mesh: trimesh.Trimesh, turn: tuple[float, float, float], bed: Bed,
          layers: str | None = None) -> str:
    """Why the part, posed by `turn`, can't be sliced on the bed, before Orca runs: the turn
    that fits it about its up axis, where one does, or that no pose fits it, naming the least
    footprint of any. "" where it fits as posed. `layers` is the own axis the part's layers
    must run along, where its assembly gives one."""
    fit = fit_of(mesh, turn, bed)
    if fit.fits:
        return ""
    head = f"--turn {degrees_said(turn)}: {fit.said()}, past the bed's {bed.said()}"
    best = turned_to_fit(mesh, (turn[0], turn[1]), bed)
    if best.fits:
        return (f"{head}. Turned about its up axis by fit-bed's own turn it fits, "
                f"{best.said()}: give --turn {degrees_said(best.turn)}, or "
                f"{degrees_said([turn[0], turn[1]])},fit")
    fits = [turned_to_fit(mesh, (p[0], p[1]), bed) for p in AXIS_ALIGNED]
    others = [f for f in fits if f.fits]
    tall = fit.too_tall(bed)
    if not others:
        # The least footprint laid as asked: the pose whose layers are the part's.
        return (f"{head}" + (f": {tall}" if tall else "") + ". It fits the bed in no pose, "
                f"turned any way about its up axis: laid so, its least footprint is "
                f"{best.said()}")
    said = "; ".join(f"{f.said()}, its layers along its own "
                     f"{Pose.of(mesh, f.turn).layers()}" for f in others)
    return (f"{head}, and " + (tall or f"no turn about its up axis fits it ({best.said()})")
            + f". It fits only laid otherwise: {said}"
            + (f"; its layers are along {layers}" if layers else ""))


def poses_said(poses: Sequence[tuple[float, float, float]]) -> str:
    return "; ".join(degrees_said(p) for p in poses)
