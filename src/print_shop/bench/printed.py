"""A part as it's printed: its walls, shells and infill, and regions of their own
(mqucifer/print-shop#337, mqucifer/print-shop#338).

One format, read by both sides, so what's sliced and what's read for strength are one thing:
`load` reads a part's stiffness and strength with it, and the print side slices it through
Orca's own modifiers (`slicer.regions`). In an assembly file each part may carry it as
`print` (`docs/bench.md`); a part without it reads solid, as before.

```
"print": {"walls": 3, "line_width": 0.45, "top": 5, "bottom": 4, "infill": 15,
          "pattern": "gyroid", "infill_angle": 45, "layer_height": 0.2,
          "regions": [{"name": "bolt-1", "box": [[0, 0, 0], [20, 12, 8]],
                       "walls": 6, "infill": 60, "why": "round the bolt hole, 3 mm margin"}]}
```

- **The part's settings** are each optional: one not given is the profile's own, which the
  slice says (Orca's config block) and `load` takes from the slice where it has one.
- **A region** is a box, two corners in mm in the part's own file's axes (as `layers` is), so it
  turns with the part, with only the settings that differ from the part's. Whatever of the part
  lies inside the box takes them. Where two boxes overlap, the later one wins, as Orca applies
  its modifiers in order.
- **The infill's direction** (`infill_angle`) is Orca's `infill_direction`: degrees on the
  plate from its +x, as the part is posed to print. A pattern strengthens a part most along its
  lines, so a region can turn it as well as thicken it.
- **Line width and layer height are the whole part's.** Orca sets a part's layer height for the
  object, never for a modifier, and one line width keeps a wall count a thickness both readers
  can check.

What Orca 2.4.2 builds of each, read from its slices (`slicer.regions`): a region's infill,
pattern, direction and shells are its own inside the box, and the part round it is unchanged.
**A region with walls of its own is printed as an island:** Orca draws its walls round the box's
own faces where they cut the part, as well as round the part's outline and holes inside it, so
the part is walled through at each such face. `load` reads that seam; a region that wants only
denser infill leaves `walls` out.

Plain Python, no NumPy and no Pydantic: the cae image's `load`, the bench's image and the
print side each import it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace
from typing import Any

__all__ = [
    "ORCA",
    "PART_ONLY",
    "PATTERNS",
    "Box",
    "Printed",
    "Region",
    "Settings",
    "as_json",
    "read",
]

Point = tuple[float, float, float]
Box = tuple[Point, Point]

#: Each setting's key in Orca's profiles and modifiers, and how Orca writes its value.
ORCA: dict[str, str] = {
    "walls": "wall_loops",
    "line_width": "line_width",
    "top": "top_shell_layers",
    "bottom": "bottom_shell_layers",
    "infill": "sparse_infill_density",
    "pattern": "sparse_infill_pattern",
    "infill_angle": "infill_direction",
    "layer_height": "layer_height",
}
#: The settings a region can't carry: the whole part's only.
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
_REGION_KEYS = frozenset({"name", "box", "why"})


@dataclass(frozen=True)
class Settings:
    """How a part, or a region of it, is printed. None is the part's own, or the profile's."""

    #: Wall loops round each outline (Orca's `wall_loops`).
    walls: int | None = None
    #: The line width, in mm (Orca's `line_width`).
    line_width: float | None = None
    #: Solid layers under each top face and over each bottom face.
    top: int | None = None
    bottom: int | None = None
    #: Sparse infill density, in percent of the volume inside the walls and shells.
    infill: float | None = None
    #: Orca's own name for the infill pattern: gyroid, grid, rectilinear, cubic, ...
    pattern: str | None = None
    #: The way the sparse infill's lines run, in degrees on the plate from its +x, as the part
    #: is posed to print (Orca's `infill_direction`): the way a pattern strengthens it most.
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

    def orca(self) -> dict[str, str]:
        """The settings given, as Orca's keys and values: a density as Orca writes it, `15%`."""
        out: dict[str, str] = {}
        for name, value in self.given().items():
            if name == "infill":
                out[ORCA[name]] = f"{value:g}%"
            else:
                out[ORCA[name]] = f"{value:g}" if isinstance(value, float) else str(value)
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
class Region:
    """A box of the part, in its own file's axes, with settings of its own."""

    name: str
    box: Box
    settings: Settings
    #: Why it's there, in words: what it's round, and by what margin.
    why: str = ""

    def contains(self, point: Point, by: float = 0.0) -> bool:
        low, high = self.box
        return all(low[a] - by <= point[a] <= high[a] + by for a in range(3))

    def said(self) -> str:
        low, high = self.box
        where = ("from (" + ", ".join(f"{v:g}" for v in low) + ") to ("
                 + ", ".join(f"{v:g}" for v in high) + ")")
        why = f" ({self.why})" if self.why else ""
        return f"{self.name}{why}: {where}, {self.settings.said()}"


@dataclass(frozen=True)
class Printed:
    """A part's print settings and its regions."""

    settings: Settings = field(default_factory=Settings)
    regions: tuple[Region, ...] = ()

    def at(self, point: Point) -> Settings:
        """The settings a point of the part, in its own axes, is printed with: the last region
        whose box holds it, over the part's."""
        found = self.settings
        for region in self.regions:
            if region.contains(point):
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


def _settings(raw: dict[str, Any], where: str, region: bool) -> Settings:
    known = {f.name for f in fields(Settings)}
    extra = set(raw) - known - (_REGION_KEYS if region else {"regions"})
    if extra:
        raise ValueError(f"{where} has no setting {', '.join(sorted(extra))}: it takes "
                         f"{', '.join(sorted(known - (PART_ONLY if region else set())))}")
    if region and (part_only := PART_ONLY & set(raw)):
        raise ValueError(f"{where} can't set {', '.join(sorted(part_only))}: it's the whole "
                         "part's, as Orca sets it for the object, never for a modifier")
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
        if "box" not in item:
            raise ValueError(f"{here} has a box: two corners in the part's own axes")
        why = item.get("why", "")
        if not isinstance(why, str):
            raise ValueError(f"{here}'s why is words")
        own = _settings(item, here, region=True)
        if not own.given():
            raise ValueError(f"{here} sets nothing of its own")
        found.append(Region(name.strip(), _box(item["box"], here), own, why))
    names = [r.name for r in found]
    if twice := sorted({n for n in names if names.count(n) > 1}):
        raise ValueError(f"{where}'s regions each have a name of their own: {', '.join(twice)}")
    return Printed(settings, tuple(found))


def as_json(printed: Printed) -> dict[str, Any]:
    """`printed` as `read` takes it."""
    out: dict[str, Any] = dict(printed.settings.given())
    if printed.regions:
        out["regions"] = [
            {"name": r.name, "box": [list(r.box[0]), list(r.box[1])], **r.settings.given(),
             **({"why": r.why} if r.why else {})}
            for r in printed.regions
        ]
    return out
