"""A part sliced with regions of their own settings, through Orca's modifiers, and read back
(mqucifer/print-shop#338).

The Sponsor, 2026-11-26, of Orca's modifiers: "these would be good probably where connections
are compared to just a regular part of the leg". A part's regions (`bench.printed`, the format
`load` reads too) are sliced by Orca itself: never by editing G-code (the Sponsor, 2026-11-05:
"Use what we can of orca at any point"). Then the G-code is read, region by region, to confirm
what Orca built in each, against the same part sliced without them.

**How it's sliced,** each step Orca's own:
1. The part is turned into the pose it prints in and seated on the plate, as
   `ingest.orientation` does, and its own settings are written into a whole copy of the process
   profile, as `with_bed_type` does.
2. Orca writes the project itself: `--export-3mf` of the posed part, with the job's profiles.
   Orca 2.4.2 won't slice a project without its own `Metadata/project_settings.config` (it
   crashed, exit 139, on one written by hand), and refuses one whose `Application` is a later
   BambuStudio without its `OrcaSlicer` version (exit 232), so the project is Orca's.
3. Each region is added to that project, as Orca stores a modifier (Orca 2.4.2's
   BambuStudio-format 3MF, read from its own export): a volume shape is a mesh object in
   `3D/Objects/regions.model`, a component of the part's object in `3D/3dmodel.model`, and a
   `<part subtype="modifier_part">` in `Metadata/model_settings.config`, its settings as
   `<metadata key=... value=...>`; a `layers` region is a `<range min_z max_z>` of
   `<option opt_key>`s in `Metadata/layer_config_ranges.xml`.
4. Orca slices the project (`--slice 0`), and slices it again without the regions: what differs
   is theirs.

**Tested facts about Orca 2.4.2's modifiers,** from a bar 80 x 20 x 10 mm with a 6 mm hole,
sliced with the store's profiles (the module's tests slice it again):
- A height range is keyed by the object's index from 1, never its 3MF id: keyed by the id, Orca
  ignored it and said nothing. A range without its own `layer_height` crashed Orca (exit 139),
  so the part's is written into each.
- A range's heights are the posed object's z: above the bed, as the part sits on it.
- A modifier volume wins over a height range, whatever their order; between modifiers, the later.
- A pattern name Orca doesn't know isn't refused: it slices as `crosshatch` (`printed.PATTERNS`).
- A region with walls of its own is printed as an island, walled round its shape's own faces
  where they cut the part (`bench.printed`).

**How a region is read,** layer by layer, from the lines Orca laid inside its shape's section on
that layer (`gcode.built`), with what later regions hold left out: the walls round each outline
in it, the sparse infill's plastic as a share of the space it fills, the way its lines run, how
many solid layers lie under a top face and over a bottom face in it, and the plastic laid. Each
setting the region gives is said as built, not built, or as the part round it, never assumed.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import trimesh

from print_shop.bench.printed import (
    Cylinder,
    Layers,
    Mesh,
    Printed,
    Region,
    Settings,
    as_json,
    own_way,
)
from print_shop.ingest.orientation import AXIS_ALIGNED
from print_shop.slicer import gcode
from print_shop.slicer.orca import (
    _DEFAULT_BINARY,
    EXITS,
    NO_ROOM,
    DoesntFit,
    SlicerProfiles,
    parse_config_block,
    pause_items,
    sliced_by,
)

__all__ = [
    "ANGLE_DEGREES",
    "INFILL_POINTS",
    "Pose",
    "Project",
    "RegionRead",
    "RegionsSlice",
    "Said",
    "settings_of",
    "slice_regions",
    "write_project",
]

#: The Orca whose project format and modifiers this was written and tested against.
ORCA_VERSION = "2.4.2"
#: A region's infill reads as asked within this many points, or `INFILL_SHARE` of what was
#: asked, whichever is more. The reading is the plastic's share of the space, a line's width clear
#: of walls and edges (`gcode.built`): on uniform bars sliced at 10 to 80% in 0.2 mm layers, read
#: over 40 and 12 mm of the bar, grid and rectilinear read within 0.1 point of the setting, cubic
#: within 2, and gyroid's layers within 7 either side, their mean within 2.
INFILL_POINTS = 3.0
INFILL_SHARE = 0.08
#: The sparse lines run as asked where most of their length is within this of the angle. Each
#: line's way is read to the whole degree (`gcode.built`), so a line is within it to half a
#: degree more.
ANGLE_DEGREES = 8.0
#: Patterns whose lines run straight, at Orca's angle and at right angles to it: `grid` both on
#: each layer, `rectilinear` each in turn, layer by layer (Orca 2.4.2, read on the bar). Their
#: direction is read; other patterns' isn't.
STRAIGHT = frozenset({"rectilinear", "grid"})
#: Walls fill a region where less than this share of its area is left inside them for
#: infill, past the gap fill Orca lays between them.
FILLED = 0.1
#: A layer is read in a region where the part's area in it is at least this, mm2.
LEAST_MM2 = 2.0
#: The orca CLI's object index, from 1, for the one part in each project.
_OBJECT = "1"
_REGIONS_MODEL = "3D/Objects/regions.model"
#: A setting a region writes on the plate that isn't one of `Settings`'s.
_PLATE_ANGLE = "infill_direction"


# --------------------------------------------------------------------------
# The part's own settings, from the profile it slices with
# --------------------------------------------------------------------------


def _value(raw: Any) -> str:
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return re.split(r"[;,]", str(raw).strip())[0].strip().strip('"')


def settings_of(process: Path, machine: Path | None = None) -> Settings:
    """Every one of the part's settings, as Orca will slice with them from a whole process
    profile: what `load` takes, which reads no profile (mqucifer/print-shop#337).

    Shells are Orca's count: the layers given, or more where the profile's shell thickness needs
    them (`top_shell_thickness`). `infill_angle` is the profile's `infill_direction`, on the
    plate: `write_project` turns it into the part's own by the pose. The line width is the
    walls': the inner walls', which most of a wall is. A width Orca gives as a share of the
    nozzle is read against `machine`'s nozzle.

    Raises:
        ValueError: Naming a setting the profile doesn't give as a number Orca would use.
    """
    data = json.loads(process.read_text())

    def number(key: str) -> float:
        raw = _value(data.get(key, ""))
        try:
            return float(raw.rstrip("%"))
        except ValueError as exc:
            raise ValueError(f"{process.name} gives {key} as {raw!r}, not a number") from exc

    height = number("layer_height")

    def shells(layers_key: str, thickness_key: str) -> int:
        count = int(number(layers_key))
        thick = number(thickness_key) if data.get(thickness_key) not in (None, "") else 0.0
        if count > 0 and thick > 0:
            count = max(count, math.ceil(thick / height - 1e-9))
        return count

    width_raw = _value(data.get("inner_wall_line_width", ""))
    if width_raw in ("", "0"):
        width_raw = _value(data.get("line_width", ""))
    if width_raw.endswith("%"):
        if machine is None:
            raise ValueError(f"{process.name} gives the walls' width as {width_raw} of the "
                             "nozzle: the machine profile is needed to read it")
        nozzle = float(_value(json.loads(machine.read_text()).get("nozzle_diameter", "")))
        width = nozzle * float(width_raw.rstrip("%")) / 100
    else:
        try:
            width = float(width_raw)
        except ValueError as exc:
            raise ValueError(f"{process.name} gives the walls' width as {width_raw!r}") from exc
    if width <= 0:
        raise ValueError(f"{process.name} gives no walls' width Orca would use")
    pattern = _value(data.get("sparse_infill_pattern", ""))
    return Settings(
        walls=int(number("wall_loops")),
        line_width=width,
        top=shells("top_shell_layers", "top_shell_thickness"),
        bottom=shells("bottom_shell_layers", "bottom_shell_thickness"),
        infill=number("sparse_infill_density"),
        pattern={"zig-zag": "rectilinear"}.get(pattern, pattern),
        infill_angle=number("infill_direction"),
        layer_height=height,
    )


def _with_settings(profiles: SlicerProfiles, own: dict[str, str], workdir: Path) -> SlicerProfiles:
    """`profiles` with the part's own settings written into a whole copy of the process."""
    if not own:
        return profiles
    base = json.loads(Path(profiles.process).read_text())
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "process_part.json"
    out.write_text(json.dumps({**base, **own}, indent=2))
    return profiles.model_copy(update={"process": out})


# --------------------------------------------------------------------------
# The pose, and each region's shape in it
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Pose:
    """How the part's own axes become the plate's: turned by `rotation_deg` (Euler XYZ, as
    `ingest.orientation`), seated on the plate, then placed where Orca put it (`shift`)."""

    rotation_deg: tuple[float, float, float]
    #: The part's own file to the posed file Orca was given, 4 x 4.
    matrix: np.ndarray
    #: Where Orca placed the posed file on the plate, x and y.
    shift: tuple[float, float] = (0.0, 0.0)

    @classmethod
    def of(cls, mesh: trimesh.Trimesh, rotation_deg: tuple[float, float, float]) -> Pose:
        rx, ry, rz = (math.radians(v) for v in rotation_deg)
        about = trimesh.transformations.rotation_matrix
        turn = (np.asarray(about(rz, (0, 0, 1)))  # type: ignore[no-untyped-call]
                @ np.asarray(about(ry, (0, 1, 0)))  # type: ignore[no-untyped-call]
                @ np.asarray(about(rx, (1, 0, 0))))  # type: ignore[no-untyped-call]
        turned = mesh.copy()
        turned.apply_transform(turn)
        seat = np.eye(4)
        seat[2, 3] = -float(turned.bounds[0][2])
        return cls(rotation_deg, np.asarray(seat @ turn))

    def layers(self) -> str | None:
        """The part's own axis its layers are stacked along: the one the pose stands up."""
        up = np.asarray(self.matrix)[2, :3]
        a = int(np.argmax(np.abs(up)))
        return "xyz"[a] if abs(abs(up[a]) - 1.0) < 1e-6 else None

    def plate_angle(self, own_angle: float) -> float | None:
        """`infill_angle`, the part's own, as Orca's `infill_direction` on the plate."""
        layers = self.layers()
        if layers is None:
            return None
        way = np.asarray(self.matrix)[:3, :3] @ np.asarray(own_way(own_angle, layers))
        return round(math.degrees(math.atan2(way[1], way[0])), 6) % 360

    def own_angle(self, plate_angle: float) -> float | None:
        """Orca's `infill_direction` on the plate as the part's own `infill_angle`."""
        layers = self.layers()
        if layers is None:
            return None
        a = "xyz".index(layers)
        way = np.array([math.cos(math.radians(plate_angle)),
                        math.sin(math.radians(plate_angle)), 0.0])
        own = np.asarray(self.matrix)[:3, :3].T @ way
        return round(math.degrees(math.atan2(own[(a + 2) % 3], own[(a + 1) % 3])), 6) % 360

    def heights(self, band: Layers) -> tuple[float, float]:
        """A `layers` region, in the part's own coordinates along its layers axis, as heights
        above the bed in this pose, either way up."""
        layers = self.layers()
        if layers is None:
            raise ValueError("the pose doesn't stand one of the part's own axes up, so a band "
                             "of its layers isn't whole layers")
        a = "xyz".index(layers)
        matrix = np.asarray(self.matrix)
        ends = sorted(float(matrix[2, a] * v + matrix[2, 3]) for v in (band.low, band.high))
        return round(ends[0], 6), round(ends[1], 6)

    def to_plate(self, points: np.ndarray) -> np.ndarray:
        """Points of the part's own file on the plate."""
        at = np.asarray(points, dtype=float)
        placed = at @ np.asarray(self.matrix)[:3, :3].T + np.asarray(self.matrix)[:3, 3]
        return np.asarray(placed + np.array([self.shift[0], self.shift[1], 0.0]))


def _shape_mesh(region: Region, base: Path | None) -> trimesh.Trimesh:
    """A volume region's shape, in the part's own axes."""
    shape = region.shape
    if isinstance(shape, Cylinder):
        mesh = trimesh.creation.cylinder(radius=shape.radius, height=shape.length, sections=72)
        align = trimesh.geometry.align_vectors(  # type: ignore[no-untyped-call]
            [0.0, 0.0, 1.0], list(shape.axis))
        mesh.apply_transform(align)
        centre = np.asarray(shape.at) + np.asarray(shape.axis) * shape.length / 2
        mesh.apply_translation(centre)
        return cast(trimesh.Trimesh, mesh)
    if isinstance(shape, Mesh):
        path = (base or Path.cwd()) / shape.file
        if not path.exists():
            raise FileNotFoundError(f"the region {region.name}'s mesh {shape.file} isn't at "
                                    f"{path}")
        loaded = trimesh.load(path, force="mesh")
        if not isinstance(loaded, trimesh.Trimesh) or not loaded.is_watertight:
            raise ValueError(f"the region {region.name}'s mesh {shape.file} isn't one closed "
                             "solid: Orca can't tell its inside")
        return loaded
    assert isinstance(shape, tuple)
    low, high = np.asarray(shape[0]), np.asarray(shape[1])
    box = trimesh.creation.box(extents=high - low)
    box.apply_translation((low + high) / 2)
    return cast(trimesh.Trimesh, box)


# --------------------------------------------------------------------------
# The project: Orca's own, with the regions added
# --------------------------------------------------------------------------


@dataclass
class Project:
    """An Orca project of one part with its regions, and how it's posed on the plate."""

    path: Path
    pose: Pose
    #: Each volume region's shape, posed: in the file Orca was given, before it placed it.
    shapes: dict[str, trimesh.Trimesh]
    #: The part's settings as sliced, and the profiles they were written into.
    settings: Settings
    profiles: SlicerProfiles
    #: The posed part.
    part: trimesh.Trimesh
    #: The regions, in order: in the project, or only read where they are (the part alone).
    regions: tuple[Region, ...] = ()
    #: Each region's settings on the plate, as written into the project.
    written: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Each `layers` region's heights above the bed, in this pose.
    bands: dict[str, tuple[float, float]] = field(default_factory=dict)


def _run(cmd: list[str], cwd: str, timeout_s: float) -> tuple[int, str, str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True, cwd=cwd)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
        raise RuntimeError(f"OrcaSlicer timeout after {timeout_s}s") from exc
    return proc.returncode, stdout, stderr


def _orca(binary: Path, profiles: SlicerProfiles, args: list[str], log: Path,
          timeout_s: float) -> tuple[int, str, str]:
    """Orca's CLI with the job's profiles, in a throwaway data directory: never the Sponsor's
    own Orca folders (mqucifer/print-shop#6)."""
    if not binary.exists():
        raise FileNotFoundError(f"OrcaSlicer binary not found: {binary}")
    with tempfile.TemporaryDirectory(prefix="orca-data-") as scratch:
        cmd = [str(binary), "--datadir", scratch, "--debug", "3", "--logfile", str(log),
               "--load-settings", f"{profiles.machine};{profiles.process}"]
        if profiles.filament is not None:
            cmd += ["--load-filaments", str(profiles.filament)]
        cmd += ["--allow-rotations=0", *args]
        return _run(cmd, scratch, timeout_s)


def _refused(what: str, code: int, stdout: str, stderr: str) -> Exception:
    known = EXITS.get(code, "")
    said = (stderr.strip() or stdout.strip())[-400:]
    text = "; ".join(x for x in (f"{what}: Orca exited {code}", known, said) if x)
    return DoesntFit(text) if code == NO_ROOM else RuntimeError(text)


def _mesh_xml(oid: int, mesh: trimesh.Trimesh) -> str:
    vertices = "".join(f'<vertex x="{x:.6g}" y="{y:.6g}" z="{z:.6g}"/>'
                       for x, y, z in mesh.vertices)
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
    return (f'<object id="{oid}" type="model"><mesh><vertices>{vertices}</vertices>'
            f"<triangles>{triangles}</triangles></mesh></object>")


def _attr(value: str) -> str:
    return (value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _item_shift(model: str) -> tuple[float, float]:
    """Where Orca placed the part: its build item's move. Refused where Orca turned it."""
    found = re.search(r'<item\b[^>]*\btransform="([^"]+)"', model)
    if not found:
        raise RuntimeError("Orca's project has no build item to place the part by")
    values = [float(v) for v in found.group(1).split()]
    if len(values) != 12:
        raise RuntimeError(f"Orca's build item transform isn't 3 x 4: {found.group(1)}")
    turn = np.array(values[:9]).reshape(3, 3)
    if not np.allclose(turn, np.eye(3), atol=1e-6):
        raise RuntimeError("Orca turned the part on the plate as it wrote the project: its "
                           "regions and infill angle aren't read against a turn it chose")
    if abs(values[11]) > 1e-6:
        raise RuntimeError(f"Orca placed the part {values[11]:g} mm above the plate: its bands "
                           "and shells aren't read where it isn't")
    return values[9], values[10]


def _add_regions(src: Path, out: Path, volumes: list[tuple[Region, trimesh.Trimesh,
                 dict[str, str]]],
                 ranges: list[tuple[Region, dict[str, str], tuple[float, float]]]) -> None:
    """Orca's project `src` with the regions added, as Orca 2.4.2 stores them, written to
    `out`. Only the project's own XML is added to: nothing Orca wrote is rewritten."""
    with zipfile.ZipFile(src) as z:
        files = {n: z.read(n) for n in z.namelist()}
    model = files["3D/3dmodel.model"].decode()
    settings = files["Metadata/model_settings.config"].decode()
    rels = files.get("3D/_rels/3dmodel.model.rels", b"").decode()
    if model.count("</components>") != 1 or settings.count("</object>") != 1:
        raise RuntimeError("Orca's project doesn't hold one object of components, as Orca "
                           f"{ORCA_VERSION} writes one part: its regions can't be added")
    if volumes:
        ids = [int(i) for i in re.findall(r'\b(?:objectid|id)="(\d+)"', model)]
        first = max(ids, default=0) + 1
        objects, components, parts = [], [], []
        for k, (region, mesh, own) in enumerate(volumes):
            oid = first + k
            centre = mesh.bounds.mean(axis=0)
            local = mesh.copy()
            local.apply_translation(-centre)
            objects.append(_mesh_xml(oid, local))
            move = " ".join(f"{v:.6g}" for v in centre)
            components.append(f'<component p:path="/{_REGIONS_MODEL}" objectid="{oid}" '
                              f'transform="1 0 0 0 1 0 0 0 1 {move}"/>')
            cx, cy, cz = (f"{v:.6g}" for v in centre)
            meta = "".join(f'<metadata key="{_attr(k_)}" value="{_attr(v)}"/>'
                           for k_, v in own.items())
            parts.append(
                f'<part id="{oid}" subtype="modifier_part">'
                f'<metadata key="name" value="{_attr(region.name)}"/>'
                f'<metadata key="matrix" value="1 0 0 {cx} 0 1 0 {cy} 0 0 1 {cz} 0 0 0 1"/>'
                f"{meta}</part>")
        model = model.replace("</components>", "".join(components) + "</components>")
        settings = settings.replace("</object>", "".join(parts) + "</object>")
        # The regions' model file and its relationship are written as Orca wrote the part's:
        # its own header, namespaces and all, and its own relationship, pointed at them.
        part_path = re.search(r'<component\b[^>]*\bp:path="/([^"]+)"', model)
        relation = re.search(r"<Relationship\b[^>]*/>", rels)
        if part_path is None or part_path.group(1) not in files or relation is None:
            raise RuntimeError("Orca's project keeps the part's mesh in no model file of its "
                               f"own, as Orca {ORCA_VERSION} does: its regions can't be added")
        part_model = files[part_path.group(1)].decode()
        header = part_model[:part_model.index("<resources>") + len("<resources>")]
        files[_REGIONS_MODEL] = (header + "".join(objects) + "</resources></model>").encode()
        ours = re.sub(r'\bId="[^"]*"', 'Id="rel-regions"',
                      re.sub(r'\bTarget="[^"]*"', f'Target="/{_REGIONS_MODEL}"',
                             relation.group(0)))
        files["3D/_rels/3dmodel.model.rels"] = rels.replace(
            "</Relationships>", ours + "</Relationships>").encode()
    if ranges:
        lines = []
        for _, own, (low, high) in ranges:
            options = "".join(f'<option opt_key="{_attr(k)}">{_attr(v)}</option>'
                              for k, v in own.items())
            lines.append(f' <range min_z="{low:g}" max_z="{high:g}">{options}</range>')
        files["Metadata/layer_config_ranges.xml"] = (
            '<?xml version="1.0" encoding="utf-8"?>\n<objects>\n'
            f'<object id="{_OBJECT}">\n' + "\n".join(lines) + "\n</object>\n</objects>\n"
        ).encode()
    files["3D/3dmodel.model"] = model.encode()
    files["Metadata/model_settings.config"] = settings.encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def write_project(
    stl: Path, printed: Printed, profiles: SlicerProfiles, outdir: Path,
    rotation_deg: tuple[float, float, float] = AXIS_ALIGNED[0], base: Path | None = None,
    binary: Path | str | None = None, timeout_s: float = 600.0, layers: str | None = None,
) -> tuple[Project, Project]:
    """The part posed, as Orca's own project with its regions, and the same without them.

    `stl` is the part in its own file's axes, as the regions are; `base` is where a mesh region's
    file is named from (the assembly or job file's folder). `layers`, where the part's assembly
    gives it, is the own axis its layers run along: a pose that stands another up is refused, as
    its infill angle and bands would mean other ways to the slice than to `load`.

    Raises:
        ValueError: If the pose doesn't stand `layers` up, or a region can't be placed.
    """
    binary_path = Path(binary) if binary is not None else _DEFAULT_BINARY
    outdir.mkdir(parents=True, exist_ok=True)
    loaded = trimesh.load(stl, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh):
        raise ValueError(f"{stl.name} isn't one mesh")
    pose = Pose.of(loaded, rotation_deg)
    if layers is not None and pose.layers() != layers:
        raise ValueError(f"the pose {rotation_deg} stands its own {pose.layers() or 'none'} up, "
                         f"but its layers are along {layers}: its infill angle and bands would "
                         "mean other ways to the slice than to load")
    bands = {r.name: pose.heights(r.shape) for r in printed.regions
             if isinstance(r.shape, Layers)}
    posed = loaded.copy()
    posed.apply_transform(pose.matrix)
    posed_path = outdir / f"{stl.stem}_posed.stl"
    posed.export(posed_path)

    own = printed.settings.orca()
    if printed.settings.infill_angle is not None:
        plate = pose.plate_angle(printed.settings.infill_angle)
        if plate is None:
            raise ValueError("the pose doesn't stand one of the part's own axes up, so its "
                             "infill angle can't be turned onto the plate")
        own[_PLATE_ANGLE] = f"{plate:g}"
    # The part's shells are written as counts with Orca's shell thickness at 0, as a region's
    # are: a band of thinner layers would otherwise get more of them than `load` is told.
    shells = settings_of(Path(profiles.process), Path(profiles.machine))
    own.update(Settings(top=printed.settings.top if printed.settings.top is not None
                        else shells.top,
                        bottom=printed.settings.bottom if printed.settings.bottom is not None
                        else shells.bottom).orca())
    part_profiles = _with_settings(profiles, own, outdir)
    resolved = settings_of(Path(part_profiles.process), Path(part_profiles.machine))
    if printed.settings.infill_angle is None and resolved.infill_angle is not None:
        resolved = replace(resolved, infill_angle=pose.own_angle(resolved.infill_angle))
    settings = printed.settings.over(resolved)

    exported = outdir / "exported"
    exported.mkdir(exist_ok=True)
    code, stdout, stderr = _orca(binary_path, part_profiles,
                                 ["--export-3mf", "part.3mf", "--outputdir", str(exported),
                                  str(posed_path)], outdir / "export.log", timeout_s)
    orca_own = exported / "part.3mf"
    if code != 0 or not orca_own.exists():
        raise _refused("writing the project", code, stdout, stderr)
    with zipfile.ZipFile(orca_own) as z:
        shift = _item_shift(z.read("3D/3dmodel.model").decode())
    pose = Pose(pose.rotation_deg, pose.matrix, shift)

    volumes: list[tuple[Region, trimesh.Trimesh, dict[str, str]]] = []
    ranges: list[tuple[Region, dict[str, str], tuple[float, float]]] = []
    shapes: dict[str, trimesh.Trimesh] = {}
    written: dict[str, dict[str, str]] = {}
    for region in printed.regions:
        keys = region.settings.orca()
        if region.settings.infill_angle is not None:
            plate = pose.plate_angle(region.settings.infill_angle)
            if plate is None:
                raise ValueError(f"the region {region.name}'s infill angle can't be turned onto "
                                 "the plate: the pose doesn't stand one of the part's axes up")
            keys[_PLATE_ANGLE] = f"{plate:g}"
        if isinstance(region.shape, Layers):
            # Orca 2.4.2 crashes on a range without its own layer height (exit 139).
            keys = {"extruder": "0", "layer_height": f"{settings.layer_height:g}", **keys}
            ranges.append((region, keys, bands[region.name]))
        else:
            mesh = _shape_mesh(region, base)
            mesh.apply_transform(pose.matrix)
            shapes[region.name] = mesh
            volumes.append((region, mesh, keys))
        written[region.name] = keys

    with_regions = outdir / "regions.3mf"
    _add_regions(orca_own, with_regions, volumes, ranges)
    without = outdir / "part.3mf"
    without.write_bytes(orca_own.read_bytes())
    return (Project(with_regions, pose, shapes, settings, part_profiles, posed,
                    printed.regions, written, bands),
            Project(without, pose, shapes, settings, part_profiles, posed, printed.regions,
                    bands=bands))


def slice_project(project: Project, outdir: Path, binary: Path | str | None = None,
                  timeout_s: float = 600.0) -> Path:
    """Orca's slice of a project: its G-code.

    Raises:
        DoesntFit: If the part has no room on the plate as it's posed.
        RuntimeError: If Orca exits otherwise than 0 or writes no G-code.
    """
    binary_path = Path(binary) if binary is not None else _DEFAULT_BINARY
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in outdir.glob("plate_*.gcode"):
        stale.unlink()
    args = []
    if project.profiles.pauses:
        items = outdir / "pauses.json"
        items.write_text(json.dumps(pause_items(list(project.profiles.pauses)), indent=1))
        args += ["--load-custom-gcodes", str(items)]
    args += ["--slice", "0", "--outputdir", str(outdir), str(project.path)]
    code, stdout, stderr = _orca(binary_path, project.profiles, args, outdir / "orca.log",
                                 timeout_s)
    gcode_path = outdir / "plate_1.gcode"
    if code != 0 or not gcode_path.exists():
        raise _refused(f"slicing {project.path.name}", code, stdout, stderr)
    return gcode_path


# --------------------------------------------------------------------------
# Reading what was built in each
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Said:
    """One setting of a region, as asked and as read."""

    name: str
    asked: str
    read: str
    #: "as asked", "not as asked", "as the part round it" (Orca didn't apply it) or "not read".
    verdict: str
    why: str = ""
    #: What the part sliced without its regions reads there.
    base: str = ""

    def line(self) -> str:
        why = f" ({self.why})" if self.why else ""
        base = f", without the regions {self.base}" if self.base not in ("", "-") else ""
        return f"{self.name}: asked {self.asked}, read {self.read}{base}: {self.verdict}{why}"


@dataclass
class RegionRead:
    """What Orca built inside one region, read from the layers, against the part without it."""

    region: Region
    #: Its settings over the part's: what it asks, wherever it gives none of its own.
    asked: Settings
    layers: list[gcode.Built]
    base_layers: list[gcode.Built]
    said: list[Said]
    #: The plastic laid inside it, with its regions and without, mm3, and the grams between.
    volume_mm3: float
    base_volume_mm3: float
    grams_added: float | None
    notes: list[str] = field(default_factory=list)

    @property
    def built(self) -> bool:
        """Whether everything it asks that could be read reads as asked."""
        read = [s for s in self.said if s.verdict != "not read"]
        return bool(read) and all(s.verdict == "as asked" for s in read)

    @property
    def ignored(self) -> bool:
        """Whether Orca built the part's own in it, for everything it asks that was read."""
        read = [s for s in self.said if s.verdict != "not read"]
        return bool(read) and all(s.verdict == "as the part round it" for s in read)

    def lines(self) -> list[str]:
        state = ("built as asked" if self.built else "IGNORED by Orca" if self.ignored
                 else "not all as asked")
        head = f"{self.region.name}: {state} ({self.region.said()})"
        grams = (f"; {self.grams_added:+.2f} g of plastic inside it"
                 if self.grams_added is not None else "")
        out = [head, f"  read on {len(self.layers)} layers{grams}"]
        out += [f"  {s.line()}" for s in self.said]
        out += [f"  note: {n}" for n in self.notes]
        return out


@dataclass
class RegionsSlice:
    """A part sliced with its regions and without, and each region read."""

    gcode: Path
    base_gcode: Path
    orca: str
    project: Path
    time_s: int | None
    base_time_s: int | None
    grams: float | None
    base_grams: float | None
    regions: list[RegionRead]
    settings: Settings

    @property
    def time_added_s(self) -> int | None:
        if self.time_s is None or self.base_time_s is None:
            return None
        return self.time_s - self.base_time_s

    @property
    def grams_added(self) -> float | None:
        if self.grams is None or self.base_grams is None:
            return None
        return round(self.grams - self.base_grams, 2)

    def for_load(self) -> dict[str, Any]:
        """The part's `print`, whole, as `load` takes it: every setting the part was sliced
        with, resolved from the profile, and its regions (mqucifer/print-shop#337)."""
        return as_json(Printed(self.settings, tuple(r.region for r in self.regions)))

    def lines(self) -> list[str]:
        out = [f"sliced by OrcaSlicer {self.orca} from the project {self.project.name}, with "
               "its regions as Orca's own modifiers, and again without them"]
        if self.time_added_s is not None and self.grams_added is not None:
            out.append(f"the regions add {self.time_added_s:+d} s and {self.grams_added:+.2f} g: "
                       f"{self.base_time_s} s and {self.base_grams} g without them, "
                       f"{self.time_s} s and {self.grams} g with")
        for region in self.regions:
            out += region.lines()
        return out


def _footer_grams(sliced: gcode.Sliced) -> float | None:
    found = sliced.per_filament("total filament used [g]")
    return round(sum(found), 2) if found else None


def _section_mask(mesh: trimesh.Trimesh, z: float, grid: gcode.Grid,
                  shift: tuple[float, float]) -> np.ndarray:
    """What of `grid` a shape holds at height `z`: its section there, filled even-odd."""
    low, high = mesh.bounds[:, 2]
    if not low < z < high:
        return grid.empty()
    lines = trimesh.intersections.mesh_plane(  # type: ignore[no-untyped-call]
        mesh, [0.0, 0.0, 1.0], [0.0, 0.0, z])
    if len(lines) == 0:
        return grid.empty()
    segments = np.column_stack([lines[:, 0, 0] + shift[0], lines[:, 0, 1] + shift[1],
                                lines[:, 1, 0] + shift[0], lines[:, 1, 1] + shift[1]])
    return grid.fill(segments)


def _masks(project: Project, regions: tuple[Region, ...], k: int, layer: gcode.Layer,
           grid: gcode.Grid) -> np.ndarray | None:
    """Region `k`'s mask on `layer`, less what regions that win over it hold there; None where
    it holds nothing of the layer."""
    mid = layer.z - layer.height / 2
    region = regions[k]

    def holds(r: Region) -> np.ndarray:
        if isinstance(r.shape, Layers):
            mask = grid.empty()
            low, high = project.bands[r.name]
            if low <= mid <= high:
                mask[:] = True
            return mask
        return _section_mask(project.shapes[r.name], mid, grid, project.pose.shift)

    mask = holds(region)
    if not mask.any():
        return None
    for j, other in enumerate(regions):
        if j == k:
            continue
        wins = (not isinstance(other.shape, Layers)
                and (isinstance(region.shape, Layers) or j > k))
        if wins:
            mask &= ~holds(other)
    return mask if mask.any() else None


def _grid(project: Project, region: Region) -> gcode.Grid:
    bounds = project.part.bounds.copy()
    if not isinstance(region.shape, Layers):
        shape = project.shapes[region.name].bounds
        bounds = np.array([np.maximum(bounds[0], shape[0]), np.minimum(bounds[1], shape[1])])
    sx, sy = project.pose.shift
    return gcode.Grid.round(float(bounds[0][0] + sx), float(bounds[0][1] + sy),
                            float(bounds[1][0] + sx), float(bounds[1][1] + sy))


def _read_layers(sliced: gcode.Sliced, project: Project, regions: tuple[Region, ...],
                 k: int) -> list[gcode.Built]:
    grid = _grid(project, regions[k])
    found = []
    for layer in sliced.layers:
        mask = _masks(project, regions, k, layer, grid)
        if mask is None:
            continue
        built = gcode.built(layer, grid, mask)
        if built.area_mm2 >= LEAST_MM2:
            found.append(built)
    return found


def _infill(layers: list[gcode.Built]) -> float | None:
    """The sparse infill's share over the layers that have room for it, weighted by that room."""
    room = [(b.infill, b.core_mm2 * (1 - b.solid)) for b in layers
            if b.infill is not None and b.solid < 0.5]
    total = sum(w for _, w in room)
    return sum(v * w for v, w in room) / total if total > 0 else None


def _walls(layers: list[gcode.Built]) -> tuple[int | None, float]:
    counts = Counter(b.walls for b in layers if b.walls is not None)
    if not counts:
        return None, 0.0
    meets = [b.walls_meet for b in layers if b.walls is not None]
    return counts.most_common(1)[0][0], sum(meets) / len(meets)


def _shells(layers: list[gcode.Built], part_top: int, top: bool) -> int | None:
    """Solid layers in a row from the part's top layer down (or from its first layer up), where
    the region holds that layer; None where it doesn't."""
    ordered = sorted(layers, key=lambda b: b.n, reverse=top)
    if not ordered or ordered[0].n != (part_top if top else 1):
        return None
    count = 0
    for b in ordered:
        if b.solid < gcode.SOLID_SHARE:
            break
        count += 1
    return count


def _angle_off(found: float, asked: float) -> float:
    off = abs(found - asked) % 180
    return min(off, 180 - off)


def _angles(layers: list[gcode.Built]) -> dict[int, float]:
    total: dict[int, float] = {}
    for b in layers:
        for k, v in b.angles.items():
            total[k] = total.get(k, 0.0) + v
    return total


def _shown(value: Any) -> str:
    return "-" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def _near(angles: dict[int, float], plate: float) -> float:
    """The share of the sparse lines' length within `ANGLE_DEGREES` of `plate` or at right
    angles to it."""
    total = sum(angles.values())
    if total <= 0:
        return 0.0
    near = sum(v for a, v in angles.items()
               if min(_angle_off(a, plate), _angle_off(a, plate + 90)) <= ANGLE_DEGREES)
    return near / total


def _angle_said(project: Project, asked: Settings, layers: list[gcode.Built],
                base_layers: list[gcode.Built], pattern: str) -> Said:
    own = asked.infill_angle
    plate = project.pose.plate_angle(own) if own is not None else None
    angles = _angles(layers)
    if pattern not in STRAIGHT:
        return Said("infill_angle", _shown(own), "-", "not read",
                    f"{pattern or 'its'} lines don't run one way and at right angles to it")
    if plate is None or not angles:
        return Said("infill_angle", _shown(own), "-", "not read", "no sparse lines inside it")
    share = _near(angles, plate)
    main = max(angles, key=lambda a: angles[a])
    read = f"lines mostly at {main} on the plate, {share:.0%} within {ANGLE_DEGREES:g} of it"
    shown = f"{_shown(own)} (on the plate {plate:g}, and {(plate + 90) % 360:g})"
    part = project.settings.infill_angle
    base_plate = project.pose.plate_angle(part) if part is not None else None
    if base_plate is not None:
        # Lines are read at the angle and at right angles to it, so two angles are told apart
        # only where neither's window, a degree wider for the reading's, reaches the other's.
        off = min(_angle_off(plate, base_plate), _angle_off(plate, base_plate + 90))
        if off <= 2 * (ANGLE_DEGREES + 0.5):
            return Said("infill_angle", shown, read, "not read",
                        f"can't tell from the part's own {base_plate:g} on the plate: a "
                        "reading of one is a reading of the other")
    if share >= 0.8:
        return Said("infill_angle", shown, read, "as asked")
    if base_plate is not None and _near(angles, base_plate) >= 0.8:
        return Said("infill_angle", shown, read, "as the part round it",
                    f"its lines run at the part's own {base_plate:g} on the plate")
    return Said("infill_angle", shown, read, "not as asked")


def _said(name: str, asked: Any, part: Any, read: Any, base: Any,
          close: Callable[[Any, Any], bool], apart: Callable[[Any, Any], bool]) -> Said:
    """One setting as asked and as read, against the part's own: `close` says a reading is
    what was asked, and `apart` that two settings can be told apart by a reading."""
    shown = _shown
    was = shown(base)
    if asked is not None and part is not None and not apart(asked, part):
        return Said(name, shown(asked), shown(read), "not read",
                    f"can't tell from the part's own {shown(part)}: a reading of one is a "
                    "reading of the other", was)
    if read is None:
        return Said(name, shown(asked), "-", "not read", "nothing of it inside the region", was)
    if close(read, asked):
        return Said(name, shown(asked), shown(read), "as asked", "", was)
    if base is not None and close(read, base):
        return Said(name, shown(asked), shown(read), "as the part round it",
                    "Orca built the part's own here", was)
    return Said(name, shown(asked), shown(read), "not as asked", "", was)


def _infill_within(asked: float) -> float:
    return max(INFILL_POINTS, INFILL_SHARE * asked)


def _infill_close(read: float, asked: float) -> bool:
    return abs(read - asked) <= _infill_within(asked)


def _infill_apart(asked: float, part: float) -> bool:
    """Whether readings of two densities can't be mistaken for each other."""
    return abs(asked - part) > _infill_within(asked) + _infill_within(part)


def _same(read: Any, asked: Any) -> bool:
    return bool(read == asked)


def _differ(asked: Any, part: Any) -> bool:
    return bool(asked != part)


def read_region(sliced: gcode.Sliced, base: gcode.Sliced, project: Project, base_project: Project,
                k: int, density: float | None) -> RegionRead:
    """Region `k` of the project, read from both slices."""
    regions = project.regions
    region = regions[k]
    asked = region.settings.over(project.settings)
    layers = _read_layers(sliced, project, regions, k)
    base_layers = _read_layers(base, base_project, regions, k)
    notes: list[str] = []
    said: list[Said] = []
    own = region.settings.given()
    part_top, base_top = len(sliced.layers), len(base.layers)
    part = project.settings

    if not layers:
        notes.append("the region holds none of the part: nothing of it is printed")
    coarse = _coarse(_grid(project, region), part.line_width or 0.45)
    if coarse:
        notes.append(coarse)
    if not isinstance(region.shape, Layers):
        shape = project.shapes[region.name].bounds
        extent = project.part.bounds
        past = [f"{'-+'[side]}{'xyz'[a]}" for side in (0, 1) for a in range(3)
                if (shape[side][a] < extent[side][a] - 1e-6 if side == 0
                    else shape[side][a] > extent[side][a] + 1e-6)]
        if past:
            notes.append("it reaches past the part on " + ", ".join(past) + " as it prints: "
                         "only what of the part lies inside it is read")

    if "infill" in own:
        said.append(_said("infill", asked.infill, part.infill, _round(_infill(layers)),
                          _round(_infill(base_layers)), _infill_close, _infill_apart))
    if "walls" in own:
        walls, meet = _walls(layers)
        base_walls, _ = _walls(base_layers)
        found = _said("walls", asked.walls, part.walls, walls, base_walls, _same, _differ)
        # What's left inside the walls for infill, past the gap fill between them.
        shares = sorted(b.core_mm2 * (1 - b.solid) / b.area_mm2 for b in layers
                        if b.area_mm2 > 0)
        filled = bool(shares) and shares[len(shares) // 2] < FILLED
        if (found.verdict != "not read" and walls is not None and asked.walls is not None
                and walls < asked.walls and (filled or meet > 0.5)):
            found = Said(found.name, found.asked, found.read, "not as asked",
                         f"the walls fill it: only {walls} fit in its width, and nothing is left "
                         "inside them", found.base)
        said.append(found)
    if "infill_angle" in own or "pattern" in own:
        pattern = asked.pattern or ""
        if "infill_angle" in own:
            said.append(_angle_said(project, asked, layers, base_layers, pattern))
        if "pattern" in own:
            said.append(Said("pattern", pattern, "-", "not read",
                             "a pattern isn't read from its lines; Orca was given it by name"))
    for name, top in (("top", True), ("bottom", False)):
        if name not in own:
            continue
        found_shells = _shells(layers, part_top, top)
        base_shells = _shells(base_layers, base_top, top)
        if found_shells is None:
            said.append(Said(name, str(getattr(asked, name)), "-", "not read",
                             f"the region doesn't hold the part's {name} layer"))
        else:
            said.append(_said(name, getattr(asked, name), getattr(part, name), found_shells,
                              base_shells, _same, _differ))
    if "layer_height" in own and isinstance(region.shape, Layers):
        heights = sorted({round(b.height, 3) for b in layers})
        read = heights[0] if len(heights) == 1 else None
        said.append(Said("layer_height", f"{asked.layer_height:g}",
                         ", ".join(f"{h:g}" for h in heights) or "-",
                         "as asked" if read is not None and asked.layer_height is not None
                         and abs(read - asked.layer_height) < 1e-3 else
                         "not read" if not heights else "not as asked"))

    volume = sum(b.volume_mm3 for b in layers)
    base_volume = sum(b.volume_mm3 for b in base_layers)
    grams = None if density is None else round((volume - base_volume) * density / 1000, 3)
    return RegionRead(region, asked, layers, base_layers, said, round(volume, 2),
                      round(base_volume, 2), grams, notes)


def _coarse(grid: gcode.Grid, width: float) -> str:
    """What's said of a region read on a raster too coarse for its lines: a big one, whose
    cells `gcode.CELLS` caps the count of."""
    if grid.cell <= width / 2:
        return ""
    return (f"it's read on a raster of {grid.cell:.2f} mm cells, coarser than half a line's "
            f"width ({width / 2:g} mm): its solid layers and infill read less surely")


def _lifted(config: dict[str, str]) -> str:
    """Why the part doesn't sit on the plate where its project put it, from the slice's
    settings: a raft under it, or a z offset. "" where it does."""
    why = []
    try:
        rafts = int(float(_value(config.get("raft_layers", "0")) or 0))
        offset = float(_value(config.get("z_offset", "0")) or 0)
    except ValueError:
        return "the slice's raft_layers or z_offset can't be read"
    if rafts:
        why.append(f"on {rafts} raft layers")
    if offset:
        why.append(f"with a z offset of {offset:g} mm")
    return " and ".join(why)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def slice_regions(
    stl: Path, printed: Printed, profiles: SlicerProfiles, outdir: Path,
    rotation_deg: tuple[float, float, float] = AXIS_ALIGNED[0], base: Path | None = None,
    binary: Path | str | None = None, timeout_s: float = 600.0, layers: str | None = None,
) -> RegionsSlice:
    """The part sliced by Orca with its regions as Orca's own modifiers, and without them, and
    each region read from the layers.

    Raises:
        FileNotFoundError: If Orca, the part or a region's mesh isn't there.
        DoesntFit: If the part has no room on the plate as it's posed.
        RuntimeError: If Orca refuses the project or writes no G-code.
        ValueError: If a region can't be put into the project, saying which and why.
    """
    with_regions, without = write_project(stl, printed, profiles, outdir, rotation_deg, base,
                                          binary, timeout_s, layers)
    sliced_path = slice_project(with_regions, outdir / "with", binary, timeout_s)
    base_path = slice_project(without, outdir / "without", binary, timeout_s)
    sliced = gcode.read(sliced_path, keep_lines=True)
    base_sliced = gcode.read(base_path, keep_lines=True)
    _check_placed(sliced, with_regions)
    config = parse_config_block(sliced_path)
    if lifted := _lifted(config):
        raise RuntimeError(f"the part was sliced {lifted}: its bands and shells would be read "
                           "where it doesn't print")
    try:
        density: float | None = float(_value(config.get("filament_density", "")))
    except ValueError:
        density = None
    reads = [read_region(sliced, base_sliced, with_regions, without, k, density)
             for k in range(len(printed.regions))]
    return RegionsSlice(
        gcode=sliced_path, base_gcode=base_path, orca=sliced_by(sliced_path) or "unknown",
        project=with_regions.path, time_s=sliced.time_s, base_time_s=base_sliced.time_s,
        grams=_footer_grams(sliced), base_grams=_footer_grams(base_sliced), regions=reads,
        settings=with_regions.settings,
    )


def _check_placed(sliced: gcode.Sliced, project: Project) -> None:
    """The part lies on the plate where the project placed it, by Orca's own outline of it."""
    if not sliced.outlines:
        raise RuntimeError("the G-code names no object's outline (EXCLUDE_OBJECT_DEFINE): "
                           "where the part lies can't be checked")
    outline = np.asarray(next(iter(sliced.outlines.values())))
    low, high = project.part.bounds[:, :2]
    sx, sy = project.pose.shift
    expected = np.array([[low[0] + sx, low[1] + sy], [high[0] + sx, high[1] + sy]])
    found = np.array([outline.min(axis=0), outline.max(axis=0)])
    if not np.allclose(found, expected, atol=0.1):
        raise RuntimeError(f"Orca placed the part at {found.round(2).tolist()}, not where its "
                           f"project put it, {expected.round(2).tolist()}: its regions can't "
                           "be read against it")
