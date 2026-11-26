"""A part as it's printed: its walls, shells and infill, and regions of their own
(mqucifer/print-shop#337, mqucifer/print-shop#338).

One format, read by both sides, so what's sliced and what's read for strength are one thing:
`load` reads a part's stiffness and strength with it, and the print side slices it through
Orca's own modifiers (`slicer.regions`). In an assembly file each part may carry it as
`print` (`docs/bench.md`); a part without it reads solid, as before.

```
"print": {"walls": 3, "line_width": 0.45, "top": 5, "bottom": 4, "infill": 15,
          "pattern": "gyroid", "infill_angle": 45, "layer_height": 0.2,
          "regions": [
            {"name": "bolt-1", "cylinder": {"at": [20, 10, 0], "axis": "z", "radius": 6,
                                            "length": 10}, "walls": 6, "why": "round the hole"},
            {"name": "end", "box": [[0, 0, 0], [12, 20, 10]], "infill": 60},
            {"name": "face", "mesh": "leg-face.stl", "infill": 40, "top": 8},
            {"name": "tab", "layers": [3, 6], "walls": 4, "layer_height": 0.1}]}
```

- **The part's settings.** On the print side each may be left out, for the profile's own;
  `full` says which are missing, and the job resolves each from the profile it slices with
  (`slicer.regions.settings_of`) before the part goes to `load`, which runs with no profile and
  takes a part only with every one given.
- **A region** has one shape and only the settings that differ from the part's. Whatever of the
  part lies inside the shape takes them. Where two of the volume shapes (box, cylinder, mesh)
  overlap, the later one wins, as Orca applies its modifiers in order; any of them wins over a
  `layers` region, whatever the order, as Orca applies a height range to the object and its
  modifiers over it; and two `layers` regions don't overlap, as Orca's height ranges can't.
  Each shape but `layers` is in the part's own file's axes, so it turns with the part:
  - `box`: two corners, the least first;
  - `cylinder`: `at`, the centre of one end, `axis`, the way it runs from there (a letter with an
    optional sign, `"z"`, `"-x"`, or a vector), its `radius` and `length`;
  - `mesh`: an STL file, named relative to the assembly or job file, already the shape it is:
    made from the part's own faces offset by a margin, so its walls follow the part. Nothing
    reading it offsets it again;
  - `layers`: `[low, high]`, heights in mm above the bed as the part prints: along its up way
    (the assembly's `layers` axis), from its lowest point that way. Orca's height range modifier,
    which holds whole layers. It alone may set `layer_height`.
- **The infill's direction** (`infill_angle`) is in the part's own axes, so `load` needs
  nothing of the pose: degrees in the plane of its layers, with its layers along its own axis
  `a`, from its own axis `a+1` toward `a+2`, cyclic (`own_way`). Layers along z: from +x toward
  +y; along x: from +y toward +z; along y: from +z toward +x. The slice turns it into Orca's
  `infill_direction`, degrees on the plate from its +x, by the pose it slices with, and the
  reader confirms from the layers that the sparse lines run that way. A pattern strengthens a
  part most along its lines, so a region can turn it as well as thicken it.
- **Line width is the whole part's**: the walls' line width, Orca's `outer_wall_line_width` and
  `inner_wall_line_width`, so a wall count is a thickness both readers can check.
- **Shells are layers, exactly**: a `top` or `bottom` given is written with Orca's shell
  thickness at 0, which would otherwise add layers to reach it.

**What Orca 2.4.2 builds of each,** read from its slices (`slicer.regions`, a bar 80 x 20 x 10 mm
with a 6 mm hole, the store's profiles):
- A region's infill, pattern, direction and shells are its own inside the shape, and the part
  round it is unchanged. No walls are added.
- **A region with walls of its own is printed as an island:** Orca walls it round its own
  shape's faces where they cut the part, as well as round the outline and holes inside it. A
  box round the hole walls the bar right through at both its faces; a cylinder round the hole
  gives rings round it, out to its radius, and one ring of walls each side of that radius.
  `load` reads that seam; a region that wants only denser infill leaves `walls` out.
- A `layers` region's walls and infill are the whole layer's, with no walls added.

Plain Python, no NumPy and no Pydantic: the cae image's `load`, the bench's image and the
print side each import it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from typing import Any

__all__ = [
    "ORCA",
    "PART_ONLY",
    "PATTERNS",
    "SHAPES",
    "Box",
    "Cylinder",
    "Layers",
    "Mesh",
    "Printed",
    "Region",
    "Settings",
    "as_json",
    "own_way",
    "read",
]

Point = tuple[float, float, float]
Box = tuple[Point, Point]
#: A region's shapes, as its key names each.
SHAPES = ("box", "cylinder", "mesh", "layers")

#: Each setting's key in Orca's profiles and modifiers, and how Orca writes its value.
ORCA: dict[str, str] = {
    "walls": "wall_loops",
    "line_width": "inner_wall_line_width",
    "top": "top_shell_layers",
    "bottom": "bottom_shell_layers",
    "infill": "sparse_infill_density",
    "pattern": "sparse_infill_pattern",
    "infill_angle": "infill_direction",
    "layer_height": "layer_height",
}
#: What else is written with a setting, so Orca builds exactly what it says: both walls' line
#: widths, and no shell thickness to add layers past the count.
ALSO: dict[str, dict[str, str | None]] = {
    "line_width": {"outer_wall_line_width": None, "inner_wall_line_width": None},
    "top": {"top_shell_thickness": "0"},
    "bottom": {"bottom_shell_thickness": "0"},
}
#: The settings a region can't carry: the whole part's only. A `layers` region may set
#: `layer_height`, as Orca's height range does.
PART_ONLY = frozenset({"line_width", "layer_height"})
#: Orca 2.4.2's names for a sparse infill pattern (`sparse_infill_pattern`): each one Orca
#: sliced a bar with, from the store's profiles, and wrote back in its config block as given.
#: A name Orca doesn't know isn't refused: it slices as `crosshatch`, so a name is checked here.
#: `zig-zag`, the store's own, Orca reads as `rectilinear`; `monotonic` and `monotonicline` are
#: for solid infill only, and Orca refuses them as sparse (exit 238).
PATTERNS = frozenset({
    "3dhoneycomb", "adaptivecubic", "alignedrectilinear", "archimedeanchords", "concentric",
    "crosshatch", "crosszag", "cubic", "grid", "gyroid", "hilbertcurve", "honeycomb",
    "lateral-honeycomb", "lateral-lattice", "lightning", "line", "lockedzag", "octagramspiral",
    "quartercubic", "rectilinear", "supportcubic", "tpmsd", "tpmsfk", "tri-hexagon", "triangles",
    "zigzag",
})
#: A region's keys that aren't settings.
_REGION_KEYS = frozenset({"name", "why", *SHAPES})


@dataclass(frozen=True)
class Settings:
    """How a part, or a region of it, is printed. None is the part's own, or the profile's."""

    #: Wall loops round each outline (Orca's `wall_loops`).
    walls: int | None = None
    #: The walls' line width, in mm (Orca's `outer_wall_line_width` and
    #: `inner_wall_line_width`).
    line_width: float | None = None
    #: Solid layers under each top face and over each bottom face.
    top: int | None = None
    bottom: int | None = None
    #: Sparse infill density, in percent of the volume inside the walls and shells.
    infill: float | None = None
    #: Orca's own name for the infill pattern: gyroid, grid, rectilinear, cubic, ...
    pattern: str | None = None
    #: The way the sparse infill's lines run, in degrees in the plane of the layers, in the
    #: part's own axes (`own_way`): the way a pattern strengthens it most. Orca's
    #: `infill_direction` is this turned onto the plate by the pose, so `orca()` leaves it out.
    infill_angle: float | None = None
    #: The layer height, in mm.
    layer_height: float | None = None

    def given(self) -> dict[str, Any]:
        """The settings given, by name."""
        return {f.name: getattr(self, f.name) for f in fields(self)
                if getattr(self, f.name) is not None}

    def over(self, base: Settings) -> Settings:
        """These settings, with `base`'s where these say nothing."""
        return replace(base, **self.given())

    def missing(self) -> list[str]:
        """The settings not given: each one `load` needs and can't take from a profile."""
        return [f.name for f in fields(self) if getattr(self, f.name) is None]

    def orca(self) -> dict[str, str]:
        """The settings given, as Orca's keys and values, with what's written with each
        (`ALSO`): a density as Orca writes it, `15%`. Not `infill_angle`: it's the part's own,
        and only the pose turns it into Orca's plate angle (`slicer.regions`)."""
        out: dict[str, str] = {}
        for name, value in self.given().items():
            if name == "infill_angle":
                continue
            if name == "infill":
                said = f"{value:g}%"
            else:
                said = f"{value:g}" if isinstance(value, float) else str(value)
            out[ORCA[name]] = said
            for key, also in ALSO.get(name, {}).items():
                out[key] = said if also is None else also
        return out

    def said(self) -> str:
        """The settings given, in a few words."""
        words = {
            "walls": lambda v: f"{v} walls",
            "line_width": lambda v: f"{v:g} mm lines",
            "top": lambda v: f"{v} top layers",
            "bottom": lambda v: f"{v} bottom layers",
            "infill": lambda v: f"{v:g}% infill",
            "pattern": lambda v: f"{v} pattern",
            "infill_angle": lambda v: f"infill at {v:g} degrees",
            "layer_height": lambda v: f"{v:g} mm layers",
        }
        given = self.given()
        return ", ".join(words[k](v) for k, v in given.items()) if given else "nothing of its own"


@dataclass(frozen=True)
class Cylinder:
    """A cylinder in the part's own file's axes: the centre of one end, the way it runs from
    there as a unit vector, its radius and its length, in mm."""

    at: Point
    axis: Point
    radius: float
    length: float

    def contains(self, point: Point) -> bool:
        rel = [point[a] - self.at[a] for a in range(3)]
        along = sum(rel[a] * self.axis[a] for a in range(3))
        if not 0.0 <= along <= self.length:
            return False
        off = [rel[a] - along * self.axis[a] for a in range(3)]
        return math.sqrt(sum(v * v for v in off)) <= self.radius

    def said(self) -> str:
        return (f"a cylinder {self.radius:g} mm in radius and {self.length:g} long from "
                f"({_points(self.at)}) along ({_points(self.axis)})")


@dataclass(frozen=True)
class Mesh:
    """An STL file, named relative to the assembly or job file, in the part's own file's axes."""

    file: str

    def said(self) -> str:
        return f"the mesh {self.file}"


@dataclass(frozen=True)
class Layers:
    """Whole layers: heights in mm above the bed as the part prints, along its up way from its
    lowest point that way."""

    low: float
    high: float

    def said(self) -> str:
        return f"the layers from {self.low:g} to {self.high:g} mm above the bed"


Shape = Box | Cylinder | Mesh | Layers


def own_way(angle: float, layers: str) -> Point:
    """`infill_angle` as a unit vector in the part's own axes, with its layers along own axis
    `layers` (x, y or z): from own axis a+1 toward a+2, cyclic."""
    a = "xyz".index(layers)
    way = [0.0, 0.0, 0.0]
    way[(a + 1) % 3] = math.cos(math.radians(angle))
    way[(a + 2) % 3] = math.sin(math.radians(angle))
    return way[0], way[1], way[2]


def _points(values: Point) -> str:
    return ", ".join(f"{v:g}" for v in values)


@dataclass(frozen=True)
class Region:
    """A shape of the part, with settings of its own."""

    name: str
    shape: Shape
    settings: Settings
    #: Why it's there, in words: what it's round, and by what margin.
    why: str = ""

    @property
    def kind(self) -> str:
        """Which shape it is: one of `SHAPES`."""
        if isinstance(self.shape, Cylinder):
            return "cylinder"
        if isinstance(self.shape, Mesh):
            return "mesh"
        if isinstance(self.shape, Layers):
            return "layers"
        return "box"

    def contains(self, point: Point, height: float | None = None,
                 in_mesh: Callable[[Point], bool] | None = None) -> bool:
        """Whether a point of the part, in its own axes, is inside. A `layers` region needs the
        point's `height` above the bed as it prints, and a `mesh` one a test of its mesh."""
        shape = self.shape
        if isinstance(shape, Cylinder):
            return shape.contains(point)
        if isinstance(shape, Layers):
            if height is None:
                raise ValueError(f"the region {self.name} is layers: a point's height above the "
                                 "bed is needed to place it")
            return shape.low <= height <= shape.high
        if isinstance(shape, Mesh):
            if in_mesh is None:
                raise ValueError(f"the region {self.name} is the mesh {shape.file}: it's read "
                                 "with its mesh")
            return in_mesh(point)
        low, high = shape
        return all(low[a] <= point[a] <= high[a] for a in range(3))

    def said(self) -> str:
        shape = self.shape
        where = (f"from ({_points(shape[0])}) to ({_points(shape[1])})"
                 if isinstance(shape, tuple) else shape.said())
        why = f" ({self.why})" if self.why else ""
        return f"{self.name}{why}: {where}, {self.settings.said()}"


@dataclass(frozen=True)
class Printed:
    """A part's print settings and its regions."""

    settings: Settings = field(default_factory=Settings)
    regions: tuple[Region, ...] = ()

    def at(self, point: Point, height: float | None = None,
           in_mesh: Mapping[str, Callable[[Point], bool]] | None = None) -> Settings:
        """The settings a point of the part, in its own axes, is printed with: the last region
        that holds it, over the part's. `height` and `in_mesh` are as `Region.contains` takes
        them, a mesh's test by its region's name."""
        found = self.settings
        for region in self.regions:
            test = (in_mesh or {}).get(region.name)
            if region.contains(point, height, test):
                found = region.settings.over(found)
        return found


def _number(where: str, value: Any, *, whole: bool = False, least: float = 0.0,
            most: float | None = None) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{where} is a number, not {value!r}")
    if whole and value != int(value):
        raise ValueError(f"{where} is a whole number, not {value!r}")
    if value < least or (most is not None and value > most):
        top = f" to {most:g}" if most is not None else " or more"
        raise ValueError(f"{where} is {least:g}{top}, not {value!r}")
    return int(value) if whole else float(value)


def _settings(raw: dict[str, Any], where: str, region: bool, layers: bool = False) -> Settings:
    known = {f.name for f in fields(Settings)}
    barred = (PART_ONLY - ({"layer_height"} if layers else set())) if region else set()
    extra = set(raw) - known - (_REGION_KEYS if region else {"regions"})
    if extra:
        raise ValueError(f"{where} has no setting {', '.join(sorted(extra))}: it takes "
                         f"{', '.join(sorted(known - barred))}")
    if part_only := barred & set(raw):
        raise ValueError(f"{where} can't set {', '.join(sorted(part_only))}: it's the whole "
                         "part's, as Orca sets it for the object, never for a modifier (a "
                         "layers region may set layer_height)")
    got: dict[str, Any] = {}
    if "walls" in raw:
        got["walls"] = _number(f"{where}'s walls", raw["walls"], whole=True)
    if "top" in raw:
        got["top"] = _number(f"{where}'s top", raw["top"], whole=True)
    if "bottom" in raw:
        got["bottom"] = _number(f"{where}'s bottom", raw["bottom"], whole=True)
    if "infill" in raw:
        got["infill"] = _number(f"{where}'s infill", raw["infill"], most=100.0)
    if "infill_angle" in raw:
        got["infill_angle"] = _number(f"{where}'s infill_angle", raw["infill_angle"],
                                      least=-math.inf)
    if "line_width" in raw:
        got["line_width"] = _number(f"{where}'s line_width", raw["line_width"], least=0.05,
                                    most=2.0)
    if "layer_height" in raw:
        got["layer_height"] = _number(f"{where}'s layer_height", raw["layer_height"],
                                      least=0.01, most=1.0)
    if "pattern" in raw:
        pattern = raw["pattern"]
        if not isinstance(pattern, str) or pattern not in PATTERNS:
            raise ValueError(f"{where}'s pattern is one of Orca's own names, "
                             f"{', '.join(sorted(PATTERNS))}: not {pattern!r}")
        got["pattern"] = pattern
    return Settings(**got)


def _box(raw: Any, where: str) -> Box:
    shape = f"{where}'s box is two corners, [[x, y, z], [x, y, z]], the least first"
    try:
        low, high = (tuple(float(v) for v in corner) for corner in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(shape) from exc
    if len(low) != 3 or len(high) != 3:
        raise ValueError(shape)
    if not all(math.isfinite(v) for v in (*low, *high)):
        raise ValueError(f"{where}'s box has a corner that isn't a finite number")
    for a, axis in enumerate("xyz"):
        if not low[a] < high[a]:
            raise ValueError(f"{where}'s box runs from {low[a]:g} to {high[a]:g} along {axis}: "
                             "its first corner is the least, below the second on each axis")
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def _finite(raw: Any, where: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"{where} is a finite number, not {raw!r}")
    return float(raw)


def _point(raw: Any, where: str) -> Point:
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise ValueError(f"{where} is a point, [x, y, z]")
    x, y, z = (_finite(v, where) for v in raw)
    return x, y, z


def _cylinder(raw: Any, where: str) -> Cylinder:
    here = f"{where}'s cylinder"
    if not isinstance(raw, dict) or set(raw) != {"at", "axis", "radius", "length"}:
        raise ValueError(f"{here} is {{\"at\": [x, y, z], \"axis\": \"z\", \"radius\": r, "
                         "\"length\": l}: the centre of one end, the way it runs from there, "
                         "its radius and its length")
    at = _point(raw["at"], f"{here}'s at")
    axis = raw["axis"]
    if isinstance(axis, str):
        word = axis.strip().lower()
        sign = -1.0 if word.startswith("-") else 1.0
        letter = word.lstrip("+-")
        if letter not in ("x", "y", "z"):
            raise ValueError(f"{here}'s axis is x, y or z, with a sign if it runs the other "
                             f"way, or a vector: not {axis!r}")
        way = [0.0, 0.0, 0.0]
        way["xyz".index(letter)] = sign
        unit = (way[0], way[1], way[2])
    else:
        vector = _point(axis, f"{here}'s axis")
        size = math.sqrt(sum(v * v for v in vector))
        if size <= 0:
            raise ValueError(f"{here}'s axis has no length: it's the way the cylinder runs")
        unit = (vector[0] / size, vector[1] / size, vector[2] / size)
    radius = _finite(raw["radius"], f"{here}'s radius")
    length = _finite(raw["length"], f"{here}'s length")
    if radius <= 0 or length <= 0:
        raise ValueError(f"{here}'s radius and length are each more than nothing")
    return Cylinder(at, unit, radius, length)


def _mesh(raw: Any, where: str) -> Mesh:
    if not isinstance(raw, str) or not raw.strip().lower().endswith(".stl"):
        raise ValueError(f"{where}'s mesh is an STL file's name, relative to the assembly or "
                         f"job file: not {raw!r}")
    name = raw.strip()
    if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
        raise ValueError(f"{where}'s mesh is named relative to the assembly or job file, "
                         f"inside its folder: not {raw!r}")
    return Mesh(name)


def _layers(raw: Any, where: str) -> Layers:
    here = f"{where}'s layers"
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ValueError(f"{here} are [low, high], heights in mm above the bed as it prints")
    low, high = (_finite(v, here) for v in raw)
    if not 0 <= low < high:
        raise ValueError(f"{here} run from {low:g} to {high:g}: from 0 or more, the lower first")
    return Layers(low, high)


def _shape(item: dict[str, Any], where: str) -> Shape:
    given = [k for k in SHAPES if k in item]
    if len(given) != 1:
        raise ValueError(f"{where} has one shape, one of {', '.join(SHAPES)}"
                         + (f": not {' and '.join(given)}" if given else ""))
    kind = given[0]
    if kind == "cylinder":
        return _cylinder(item[kind], where)
    if kind == "mesh":
        return _mesh(item[kind], where)
    if kind == "layers":
        return _layers(item[kind], where)
    return _box(item[kind], where)


def _shape_json(shape: Shape) -> dict[str, Any]:
    if isinstance(shape, Cylinder):
        return {"cylinder": {"at": list(shape.at), "axis": list(shape.axis),
                             "radius": shape.radius, "length": shape.length}}
    if isinstance(shape, Mesh):
        return {"mesh": shape.file}
    if isinstance(shape, Layers):
        return {"layers": [shape.low, shape.high]}
    return {"box": [list(shape[0]), list(shape[1])]}


def read(raw: Any, where: str = "print") -> Printed:
    """A part's `print`, as JSON gives it.

    Raises:
        ValueError: Naming the field that can't be read, and what it takes.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{where} is an object of settings, as {{\"walls\": 3, \"infill\": 15}}")
    settings = _settings(raw, where, region=False)
    regions = raw.get("regions", [])
    if not isinstance(regions, list):
        raise ValueError(f"{where}'s regions are a list")
    found: list[Region] = []
    for k, item in enumerate(regions, 1):
        if not isinstance(item, dict):
            raise ValueError(f"{where}'s region {k} is an object")
        name = item.get("name", f"region-{k}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}'s region {k} has a name, in words")
        here = f"{where}'s region {name}"
        shape = _shape(item, here)
        why = item.get("why", "")
        if not isinstance(why, str):
            raise ValueError(f"{here}'s why is words")
        own = _settings(item, here, region=True, layers=isinstance(shape, Layers))
        if not own.given():
            raise ValueError(f"{here} sets nothing of its own")
        found.append(Region(name.strip(), shape, own, why))
    ranges = sorted((r.shape.low, r.shape.high, r.name) for r in found
                    if isinstance(r.shape, Layers))
    for (_, high, first), (low, _, second) in zip(ranges, ranges[1:], strict=False):
        if low < high:
            raise ValueError(f"{where}'s layers regions {first} and {second} overlap: Orca's "
                             "height ranges can't, so each holds layers of its own")
    names = [r.name for r in found]
    if twice := sorted({n for n in names if names.count(n) > 1}):
        raise ValueError(f"{where}'s regions each have a name of their own: {', '.join(twice)}")
    return Printed(settings, tuple(found))


def as_json(printed: Printed) -> dict[str, Any]:
    """`printed` as `read` takes it."""
    out: dict[str, Any] = dict(printed.settings.given())
    if printed.regions:
        out["regions"] = [
            {"name": r.name, **_shape_json(r.shape), **r.settings.given(),
             **({"why": r.why} if r.why else {})}
            for r in printed.regions
        ]
    return out
