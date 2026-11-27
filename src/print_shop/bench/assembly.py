"""An assembly file: parts, where each is, how each is printed, and what joins them
(mqucifer/print-shop#156).

A part bigger than the bed is printed as several and joined. `load` reads the joined parts under
a weight, and `fit-bed` reads each against the bed, so both read one file, JSON, written by
whoever designs the parts (`docs/bench.md` has it in full):

```
{"parts": [{"name": "top", "file": "top.step", "at": [0, 0, 500], "turn": [["x", 90]],
            "material": "petg", "layers": "z"}, ...],
 "joints": [{"between": ["top", "leg"], "kind": "bolted", "bolt": "m4-8.8",
             "insert": "m4-heat-set", "at": [[20, 20, 500]]}, ...],
 "held": [{"plane": "z=0", "how": "standing"}]}
```

- **A part** is one solid in a STEP file. `turn` turns it about the origin's axes, in order, in
  degrees, and `at` then moves it: where it sits in the assembly. `layers` is the axis of its own
  file its layers are stacked along, the way up on the bed: it turns with the part. `with`
  names other files placed by its `turn` and `at`, such as a frame's panes, which carry no load
  (mqucifer/print-shop#201): `clearance`, `fit-on` and `render` read them where the part sits
  with `--placed` (`placed.py`), and `load` and `fit-bed` read the part alone.
- **A joint** is between two parts that meet face to face. `bonded` is glued or welded over the
  faces that meet. `bolted` names a bolt and an insert from the tables here, and where each bolt
  is, a point on its axis. `through` (mqucifer/print-shop#168) names a bolt that passes right
  through both parts, its nut and the washer under each side, its head on the first part named
  and its nut on the second; with `pocket`, the nut sits in a pocket there that holds it by its
  flats, at that play, with no washer under it; with `channel` (mqucifer/print-shop#302), the
  bolt lies in a drop-in channel open on one side, and its head and its nut bear on the walls
  the channel names. `dovetail` (mqucifer/print-shop#170) is a
  tail on the first part named in a socket in the second: `slides` is the way the tail goes in,
  toward its seat, in the assembly's own axes, and `latch`, where it has one, is the flexing
  tab that holds it there: the part it's `on`, a box round it, `arm`, and the plane of its
  `root`, as `bend` takes an arm. It takes nothing else: glue channels are the parts' own
  shape, and no field gives glue a figure. `touching` is held by nothing: it can push, and
  can't pull.
- **`held`** is where the assembly stands or is fixed: the flat faces in a plane, `standing` on
  it (held along the plane's axis only, free to slide) or `fixed` to it. With none, it stands on
  its lowest level.

- **`print`**, on a part, is how it's printed (`printed.py`, mqucifer/print-shop#337): its walls,
  shells and infill, and regions of their own. `load` reads a part that has it as a skin round
  an infill core (`as_printed.py`); one without it is solid.

Plain Python and NumPy: `load` reads it in the second image and `fit-bed` in the first.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from . import printed as printing
from .bend import LIMITS
from .report import NoAnswer

AXES = ("x", "y", "z")
KINDS = ("bonded", "bolted", "through", "dovetail", "touching")
#: The ways a dovetail's tail can slide in: along one of the assembly's axes.
WAYS = ("+x", "-x", "+y", "-y", "+z", "-z")
HOW = ("standing", "fixed")


@dataclass(frozen=True)
class Material:
    """A printed material as `load` takes it. `docs/bench.md` gives each figure's source."""

    name: str
    #: Its stiffness, in MPa, and Poisson's ratio.
    modulus_mpa: float
    poisson: float
    #: The most strain it takes pulled across its layers, and any way in their plane, in percent.
    across_pct: float
    along_pct: float
    source: str
    #: The most pressure it takes under a washer, a nut or a bolt's shank, in MPa: pressed
    #: across its layers, and pressed along them. `PRESS` says where each is from.
    press_across_mpa: float = 0.0
    press_along_mpa: float = 0.0
    press_source: str = ""
    #: Its density, in g/cm³, which gives a solid part its weight, and where it's from.
    density_g_cm3: float = 0.0
    density_source: str = ""

    def press(self, way: np.ndarray, layers: np.ndarray) -> tuple[float, str]:
        """The most pressure it takes pushed along `way`, with its layers stacked along
        `layers`, and which figure that is: across them where the push is nearer their
        stacking than their plane."""
        if abs(float(way @ layers)) >= math.cos(math.radians(45)):
            return self.press_across_mpa, "across its layers"
        return self.press_along_mpa, "along its layers"


#: Prusament PETG's datasheet (Prusa Polymers, version 1.1, 16-02-2022), as `bend` reads it: a
#: tensile modulus of 1.5 ± 0.1 GPa; "Interlayer Adhesion [MPa] 18 ± 4" over it is about 1.2%
#: strain, 0.9% at the low end of both, so 1% across the layers (`bend.LIMITS`); and 5.1 ± 0.1%
#: elongation at yield for a part printed flat, pulled in the plane of its layers. Poisson's ratio
#: is `bend`'s.
#:
#: **Under a washer, a nut or a bolt's shank** (mqucifer/print-shop#168) the plastic is pressed,
#: and the same sheet has no compressive figure: the word isn't on it. So a pressure is held to
#: the sheet's own figures for what the plastic takes pulled, which stand in for it and are
#: said as that. Pressed across its layers, which pushes them together, it's the solid
#: plastic's "Tensile Yield Strength [MPa] 47 ± 2" for a specimen printed flat (ISO 527-1), at
#: its low end, 45. Pressed along its layers it spreads sideways, which pulls one layer off the
#: next, and all the sheet gives for that is "Interlayer Adhesion [MPa] 18 ± 4", at its low
#: end, 14.
#: A pull's figure stands in on the safe side (the operator's ruling on
#: mqucifer/print-shop#169): a thermoplastic yields pressed at no less than it yields pulled.
PRESS = (
    45.0,
    14.0,
    "the same sheet, which has no compressive figure, so its figures for a pull stand in, on the "
    "safe side as a thermoplastic yields pressed at no less than it does pulled: "
    "pressed across its layers, the low end of its tensile yield strength printed flat, "
    "47 ± 2 MPa (ISO 527-1); pressed along them, the low end of its interlayer adhesion, "
    "18 ± 4 MPa",
)
#: The same sheet's "Typical material properties": "Density [g/cm3] 1.27", by ISO 1183. A part's
#: own weight (mqucifer/print-shop#170) is its solid volume at this: a printed part has walls
#: and infill, so it's lighter than that, and the solid weight is the careful one to hang.
DENSITY = (
    1.27,
    "Prusament PETG's datasheet (Prusa Polymers, version 1.1, 16-02-2022), \"Typical material "
    "properties\": \"Density [g/cm3] 1.27\", by ISO 1183",
)
MATERIALS = {
    "petg": Material(
        "petg",
        1500.0,
        0.38,
        LIMITS["petg"],
        5.1,
        "Prusament PETG's datasheet (Prusa Polymers, version 1.1, 16-02-2022): a tensile "
        "modulus of 1.5 GPa, 18 MPa of interlayer adhesion over it for about 1% across the "
        "layers, and 5.1% at yield in their plane",
        *PRESS,
        *DENSITY,
    )
}


@dataclass(frozen=True)
class Bolt:
    name: str
    thread: str
    #: The most it's pulled before it stretches for good, in N: its proof load.
    pull_n: float
    source: str


@dataclass(frozen=True)
class Insert:
    name: str
    thread: str
    #: The pull it leaves the plastic at, in N, where a source gives one for printed plastic.
    pull_n: float | None
    source: str


#: A coarse thread's stress area, in mm² (ISO 898-1, the nominal stress area of each size).
STRESS_AREA = {"m3": 5.03, "m4": 8.78, "m5": 14.2, "m6": 20.1, "m8": 36.6}
#: The stress a bolt takes with no lasting stretch, in MPa: ISO 898-1's proof stress for a steel
#: bolt of class 8.8 up to M16, and ISO 3506-1's 0.2% proof stress for stainless A2-70.
PROOF = {
    "8.8": (580.0, "ISO 898-1's proof stress for class 8.8, 580 MPa"),
    "a2-70": (450.0, "ISO 3506-1's 0.2% proof stress for A2-70, 450 MPa"),
}
BOLTS = {
    f"{thread}-{grade}": Bolt(
        f"{thread}-{grade}",
        thread,
        stress * area,
        f"{said}, over {thread.upper()}'s stress area, {area:g} mm²",
    )
    for thread, area in STRESS_AREA.items()
    for grade, (stress, said) in PROOF.items()
}
#: A brass insert melted into a printed hole. None has a pull-out figure here: the ones found
#: (2026-11-17) were for other plastics or gave no method, so a joint gives its own, `pull_n`
#: with its `source`, from the request or a pull test, and until then only its bolt is judged.
INSERTS = {
    f"{thread}-heat-set": Insert(
        f"{thread}-heat-set",
        thread,
        None,
        "no pull-out figure for printed PETG has a source yet",
    )
    for thread in STRESS_AREA
}


@dataclass(frozen=True)
class Nut:
    name: str
    thread: str
    #: Across its flats at most, its nominal size, and across its corners at least, in mm.
    flats_mm: float
    corners_mm: float
    #: Its height at most, and the least diameter of the face it bears on, in mm.
    height_mm: float
    bears_mm: float
    source: str


@dataclass(frozen=True)
class Washer:
    name: str
    thread: str
    #: Its hole, its outside diameter and its thickness, in mm, each its nominal size.
    hole_mm: float
    outer_mm: float
    thick_mm: float
    source: str


#: A thread's nominal diameter, in mm: what its name says (ISO 261's designation, M5 is 5 mm).
DIAMETER = {"m3": 3.0, "m4": 4.0, "m5": 5.0, "m6": 6.0, "m8": 8.0}
#: A hexagon regular nut, style 1, product grades A and B (NUT_FROM): across its flats s at
#: most, across its corners e at least, its height m at most and its bearing face dw at least.
NUT_FROM = "ISO 4032:2012, hexagon regular nuts (style 1), table 1"
NUT_SIZES = {
    "m3": (5.5, 6.01, 2.4, 4.6),
    "m4": (7.0, 7.66, 3.2, 5.9),
    "m5": (8.0, 8.79, 4.7, 6.9),
    "m6": (10.0, 11.05, 5.2, 8.9),
    "m8": (13.0, 14.38, 6.8, 11.6),
}
NUTS = {
    f"{thread}-nut": Nut(
        f"{thread}-nut",
        thread,
        flats,
        corners,
        height,
        bears,
        f"{NUT_FROM}: {thread.upper()} is {flats:g} mm across its flats at most, {corners:g} "
        f"across its corners at least, {height:g} tall at most, and bears on a face at least "
        f"{bears:g} across",
    )
    for thread, (flats, corners, height, bears) in NUT_SIZES.items()
}
#: A plain washer, normal series, product grade A (WASHER_FROM): its hole d1, its outside
#: diameter d2 and its thickness h, each nominal.
WASHER_FROM = "ISO 7089:2000, plain washers, normal series, product grade A"
WASHER_SIZES = {
    "m3": (3.2, 7.0, 0.5),
    "m4": (4.3, 9.0, 0.8),
    "m5": (5.3, 10.0, 1.0),
    "m6": (6.4, 12.0, 1.6),
    "m8": (8.4, 16.0, 1.6),
}
WASHERS = {
    f"{thread}-washer": Washer(
        f"{thread}-washer",
        thread,
        hole,
        outer,
        thick,
        f"{WASHER_FROM}: for {thread.upper()}, a hole of {hole:g} mm, {outer:g} across and "
        f"{thick:g} thick",
    )
    for thread, (hole, outer, thick) in WASHER_SIZES.items()
}


@dataclass(frozen=True)
class PartAt:
    name: str
    file: str
    at: tuple[float, float, float]
    #: Each turn about one of the origin's axes, in order: its axis and its angle in degrees.
    turn: tuple[tuple[str, float], ...]
    material: Material
    #: The axis of its own file its layers are stacked along.
    layers: str
    #: Other files placed with it by the same `turn` and `at`, such as a frame's panes: each as
    #: the assembly names it, beside the assembly file.
    others: tuple[str, ...] = ()
    #: A glass frame's lead band, a STEP file beside the assembly file in the part's own axes:
    #: what of the part `load` leaves out to read it as a lower bound, where the whole can't be
    #: read (mqucifer/print-shop#295).
    band: str | None = None
    #: How it's printed, where the file says (`printed.py`, mqucifer/print-shop#337): `load`
    #: reads it as a skin of walls and shells round an infill core. None reads it solid.
    printed: printing.Printed | None = None

    def matrix(self) -> np.ndarray:
        """Its turns as one rotation."""
        total = np.eye(3)
        for axis, degrees in self.turn:
            total = _about(AXES.index(axis), math.radians(degrees)) @ total
        return total

    def layers_way(self) -> np.ndarray:
        """The way its layers are stacked, in the assembly."""
        unit = np.zeros(3)
        unit[AXES.index(self.layers)] = 1.0
        return np.asarray(self.matrix() @ unit)


@dataclass(frozen=True)
class Latch:
    """A dovetail's click latch: a flexing tab on one of its two parts that snaps over a ledge
    on the other when the tail is home."""

    #: The part the tab is on.
    on: str
    #: A box round the tab, hook and all, one side of it on the tab's root: two corners, in the
    #: assembly's coordinates.
    arm: tuple[tuple[float, float, float], tuple[float, float, float]]
    #: The plane the tab is held at, where it joins the rest of its part: its axis and where.
    root: tuple[str, float]
    #: Where `arm` and `root` are given (mqucifer/print-shop#295): the assembly's own axes, or,
    #: with `own`, the tab's part's own file's, carried with that part by its `turn` and `at`:
    #: `turn`, as one rotation, and `shift`, its `at`. A point of the assembly is in the box
    #: where `turn`'s inverse takes it, less `shift`, inside `arm`.
    own: bool = False
    turn: tuple[tuple[float, float, float], ...] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    shift: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def placed(self) -> bool:
        """Whether its box is anywhere but where `arm` says, in the assembly's axes."""
        return bool(np.any(np.asarray(self.turn) != np.eye(3)) or any(self.shift))

    def unplaced(self, points: Any) -> np.ndarray:
        """Points of the assembly in the box's own axes, a row each: themselves where it isn't
        placed."""
        at = np.asarray(points, dtype=float)
        if not self.placed:
            return at
        return np.asarray((at - np.asarray(self.shift)) @ np.asarray(self.turn))

    def way(self, vector: Any) -> np.ndarray:
        """A way in the assembly, in the box's own axes."""
        return np.asarray(np.asarray(vector, dtype=float) @ np.asarray(self.turn))

    def inside(self, point: Any, by: float = 1e-6) -> bool:
        """Whether a point of the assembly is in the box, within `by`."""
        low, high = (np.asarray(c, dtype=float) for c in self.arm)
        at = self.unplaced(point)
        return bool(np.all(at >= low - by) and np.all(at <= high + by))

    def corners(self) -> np.ndarray:
        """The box's eight corners, in the assembly."""
        low, high = (np.asarray(c, dtype=float) for c in self.arm)
        found = np.array([[(low, high)[(k >> a) & 1][a] for a in range(3)] for k in range(8)])
        if not self.placed:
            return found
        return np.asarray(found @ np.asarray(self.turn).T + np.asarray(self.shift))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """The least box of the assembly's axes round it: itself where it isn't placed."""
        if not self.placed:
            return np.array(self.arm[0], dtype=float), np.array(self.arm[1], dtype=float)
        corners = self.corners()
        return corners.min(axis=0), corners.max(axis=0)

    def where(self) -> str:
        """The words for where its `arm` and `root` are given."""
        return f"in {self.on}'s own file's axes" if self.own else "in the assembly's axes"


@dataclass(frozen=True)
class Joint:
    between: tuple[str, str]
    kind: str
    bolt: Bolt | None = None
    insert: Insert | None = None
    at: tuple[tuple[float, float, float], ...] = ()
    #: The joint's own figure for what each bolt holds, in N, and where it's from.
    pull_n: float | None = None
    source: str = ""
    #: A through-bolt's nut and the washer under each side. Its head is on the first part.
    nut: Nut | None = None
    washer: Washer | None = None
    #: The play between each of the nut's flats and the wall of the pocket it sits in, in mm,
    #: where it sits in one, with no washer under it.
    pocket: float | None = None
    #: Where a through-bolt lies in a drop-in channel open on one side (mqucifer/print-shop#302).
    channel: Channel | None = None
    #: A dovetail's: the way its tail, on the first part, slides into its socket, in the second,
    #: toward its seat, as a sign and an axis of the assembly's, and its latch where it has one.
    slides: str | None = None
    latch: Latch | None = None

    @property
    def name(self) -> str:
        return "+".join(self.between)

    def slides_way(self) -> np.ndarray:
        """The way a dovetail's tail slides in, as a unit vector."""
        assert self.slides is not None
        way = np.zeros(3)
        way[AXES.index(self.slides[1])] = 1.0 if self.slides[0] == "+" else -1.0
        return way

    def holds(self) -> tuple[float, str] | None:
        """The most pull each bolt of it takes, and whose figure that is: the least of the
        bolt's, the insert's where it has one, and the joint's own."""
        if self.bolt is None:
            return None
        found = [(self.bolt.pull_n, f"the bolt {self.bolt.name}: {self.bolt.source}")]
        if self.insert is not None and self.insert.pull_n is not None:
            found.append(
                (self.insert.pull_n, f"the insert {self.insert.name}: {self.insert.source}")
            )
        if self.pull_n is not None:
            found.append((self.pull_n, f"the joint's own figure: {self.source}"))
        return min(found)


@dataclass(frozen=True)
class Channel:
    """A through-bolt's seat as a drop-in channel (mqucifer/print-shop#302): a U with a round
    bottom, open on one side, running across the join, that the bolt drops into from that side.
    Its head bears on one wall and its nut on another, each square to the bolt."""

    #: The axis of the assembly's the bolt runs along.
    axis: str
    #: The way the channel is open, square to it: one of `WAYS`.
    open: str
    #: Where the walls the head and the nut bear on are along `axis`, in the assembly.
    head_wall: float
    nut_wall: float

    def axis_way(self) -> np.ndarray:
        way = np.zeros(3)
        way[AXES.index(self.axis)] = 1.0
        return way

    def open_way(self) -> np.ndarray:
        way = np.zeros(3)
        way[AXES.index(self.open[1])] = 1.0 if self.open[0] == "+" else -1.0
        return way

    def wall_said(self, wall: str) -> str:
        """`the head's wall at x=-6`: a wall by whose it is and its plane."""
        level = self.head_wall if wall == "head" else self.nut_wall
        return f"the {wall}'s wall at {self.axis}={level:g}"


#: What a channel gives, and nothing else.
CHANNEL_FIELDS = {"axis", "open", "head_wall", "nut_wall"}


@dataclass(frozen=True)
class Held:
    axis: str
    at: float
    how: str
    part: str | None = None


@dataclass(frozen=True)
class Assembly:
    path: str
    parts: tuple[PartAt, ...]
    joints: tuple[Joint, ...] = ()
    held: tuple[Held, ...] = field(default=())

    def part(self, name: str) -> PartAt:
        return next(p for p in self.parts if p.name == name)


def _about(axis: int, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    i, j = (axis + 1) % 3, (axis + 2) % 3
    turned = np.eye(3)
    turned[i, i], turned[i, j], turned[j, i], turned[j, j] = c, -s, s, c
    return turned


def _point(value: Any, what: str) -> tuple[float, float, float]:
    try:
        x, y, z = (float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise NoAnswer(f"{what} is three numbers, [x, y, z], not {value!r}") from exc
    return (x, y, z)


def _one_of(value: Any, choices: Any, what: str) -> Any:
    if value not in choices:
        raise NoAnswer(f"{what} is one of {', '.join(sorted(choices))}, not {value!r}")
    return value


def _part(entry: Any, folder: Path) -> PartAt:
    if not isinstance(entry, dict) or not entry.get("name") or not entry.get("file"):
        raise NoAnswer(f"each part has a `name` and a `file`: {entry!r}")
    name = str(entry["name"])
    file = str(entry["file"])
    if not file.lower().endswith((".step", ".stp")):
        raise NoAnswer(f"the part {name} is a STEP file, one solid: {file} isn't")
    if not (folder / file).is_file():
        raise NoAnswer(f"the part {name}'s file, {file}, isn't beside the assembly file")
    if "layers" not in entry:
        raise NoAnswer(
            f"the part {name} has no `layers`: the axis of its own file its layers are stacked "
            "along, the way up on the bed, one of x, y, z. A printed part is weaker across them"
        )
    turns = []
    for turn in entry.get("turn", []):
        try:
            axis, degrees = turn
            turns.append((str(_one_of(axis, AXES, f"{name}'s turn axis")), float(degrees)))
        except (TypeError, ValueError) as exc:
            raise NoAnswer(f"{name}'s `turn` is a list of [axis, degrees]: {turn!r}") from exc
    material = _one_of(entry.get("material", "petg"), MATERIALS, f"{name}'s material")
    return PartAt(
        name,
        file,
        _point(entry.get("at", (0, 0, 0)), f"{name}'s `at`"),
        tuple(turns),
        MATERIALS[material],
        str(_one_of(entry["layers"], AXES, f"{name}'s `layers`")),
        _with(entry.get("with", []), name, folder),
        _band(entry.get("band"), name, folder),
        _printed(entry.get("print"), name),
    )


def _printed(given: Any, name: str) -> printing.Printed | None:
    """A part's `print`: its walls, shells and infill, and its regions, or None for solid."""
    if given is None:
        return None
    try:
        return printing.read(given, f"the part {name}'s print")
    except ValueError as exc:
        raise NoAnswer(str(exc)) from exc


def _band(given: Any, name: str, folder: Path) -> str | None:
    """A part's glass band: a STEP file beside the assembly file, in the part's own axes."""
    if given is None:
        return None
    if not isinstance(given, str) or not given.lower().endswith((".step", ".stp")):
        raise NoAnswer(
            f"the part {name}'s `band` is a STEP file, in the part's own axes, holding what of "
            f"it is the glass band, its leads and lips: {given!r}"
        )
    if not (folder / given).is_file():
        raise NoAnswer(f"the part {name}'s `band`, {given}, isn't beside the assembly file")
    return given


def _with(given: Any, name: str, folder: Path) -> tuple[str, ...]:
    """The files placed with a part by its `turn` and `at`, each a STEP or STL file beside the
    assembly file."""
    if not isinstance(given, list) or not all(isinstance(f, str) and f for f in given):
        raise NoAnswer(
            f"the part {name}'s `with` is a list of files placed with it, such as its panes: "
            f"{given!r}"
        )
    for file in given:
        if not file.lower().endswith((".step", ".stp", ".stl")):
            raise NoAnswer(f"the part {name}'s `with` names {file}, which isn't a STEP or STL file")
        if not (folder / file).is_file():
            raise NoAnswer(
                f"the part {name}'s `with` names {file}, which isn't beside the assembly file"
            )
    return tuple(given)


def _joint(entry: Any, names: list[str]) -> Joint:
    if not isinstance(entry, dict) or len(entry.get("between", ())) != 2:
        raise NoAnswer(f"each joint is `between` two parts, with its `kind`: {entry!r}")
    first, second = (str(n) for n in entry["between"])
    for name in (first, second):
        if name not in names:
            raise NoAnswer(f"a joint names the part {name}, and the parts are {', '.join(names)}")
    if first == second:
        raise NoAnswer(f"a joint is between two parts, not {first} and itself")
    kind = _one_of(entry.get("kind"), KINDS, f"the joint {first}+{second}'s `kind`")
    if kind == "through":
        return _through(entry, first, second)
    if kind == "dovetail":
        return _dovetail(entry, first, second)
    if kind != "bolted":
        extra = sorted(
            {"bolt", "insert", "at", "pull_n", "nut", "washer", "pocket", "channel"} & set(entry)
        )
        if extra:
            raise NoAnswer(
                f"the joint {first}+{second} is {kind} and gives {', '.join(extra)}: only a "
                "bolted or a through joint has a bolt"
            )
        extra = sorted({"slides", "latch"} & set(entry))
        if extra:
            raise NoAnswer(
                f"the joint {first}+{second} is {kind} and gives {', '.join(extra)}: only a "
                "dovetail slides in, and a dovetail is never read as "
                + ("glued" if kind == "bonded" else kind)
            )
        return Joint((first, second), kind)
    extra = sorted({"nut", "washer", "pocket", "channel"} & set(entry))
    if extra:
        raise NoAnswer(
            f"the joint {first}+{second} is bolted, into an insert, and gives "
            f"{', '.join(extra)}: a bolt with a nut is a `through` joint"
        )
    what = f"the bolted joint {first}+{second}"
    bolt = BOLTS[_one_of(entry.get("bolt"), BOLTS, f"{what}'s `bolt`")]
    insert = INSERTS[_one_of(entry.get("insert"), INSERTS, f"{what}'s `insert`")]
    if bolt.thread != insert.thread:
        raise NoAnswer(f"{what}: the bolt {bolt.name} doesn't go into the insert {insert.name}")
    points = tuple(_point(p, f"{what}'s `at`") for p in entry.get("at", ()))
    if not points:
        raise NoAnswer(f"{what} says where each bolt is, in `at`: a point on its axis, [x, y, z]")
    pull = entry.get("pull_n")
    if pull is not None and (not str(entry.get("source", "")).strip() or float(pull) <= 0):
        raise NoAnswer(
            f"{what}'s `pull_n` is what each bolt holds, in N, more than 0, with its `source`: "
            "the words that say where the figure is from"
        )
    return Joint(
        (first, second),
        kind,
        bolt,
        insert,
        points,
        None if pull is None else float(pull),
        str(entry.get("source", "")).strip(),
    )


def _through(entry: dict[str, Any], first: str, second: str) -> Joint:
    """A bolt right through both parts: its head and a washer on the first, its nut on the
    second, on a washer or in a pocket."""
    what = f"the through joint {first}+{second}"
    extra = sorted({"insert", "pull_n", "source"} & set(entry))
    if extra:
        raise NoAnswer(
            f"{what} gives {', '.join(extra)}: a through-bolt goes into a nut, not an insert, "
            "and what it holds is read from the bolt, the washers and the plastic"
        )
    bolt = BOLTS[_one_of(entry.get("bolt"), BOLTS, f"{what}'s `bolt`")]
    nut = NUTS[_one_of(entry.get("nut"), NUTS, f"{what}'s `nut`")]
    washer = WASHERS[_one_of(entry.get("washer"), WASHERS, f"{what}'s `washer`")]
    if nut.thread != bolt.thread:
        raise NoAnswer(f"{what}: the nut {nut.name} doesn't go on the bolt {bolt.name}")
    if washer.thread != bolt.thread:
        raise NoAnswer(f"{what}: the washer {washer.name} isn't the bolt {bolt.name}'s size")
    points = tuple(_point(p, f"{what}'s `at`") for p in entry.get("at", ()))
    if not points:
        raise NoAnswer(f"{what} says where each bolt is, in `at`: a point on its axis, [x, y, z]")
    play = entry.get("pocket")
    if play is not None:
        try:
            play = float(play)
        except (TypeError, ValueError):
            play = -1.0
        if isinstance(entry["pocket"], bool) or play < 0:
            raise NoAnswer(
                f"{what}'s `pocket` is the play between each of the nut's flats and its "
                f"pocket's wall, in mm, 0 or more, not {entry['pocket']!r}"
            )
    channel = None if entry.get("channel") is None else _channel(entry["channel"], what)
    if channel is not None and play is not None:
        raise NoAnswer(
            f"{what} gives a `pocket` and a `channel`: in a channel the nut bears on its wall "
            "through a washer, as the head does, and isn't held in a pocket"
        )
    return Joint(
        (first, second),
        "through",
        bolt,
        None,
        points,
        nut=nut,
        washer=washer,
        pocket=play,
        channel=channel,
    )


def _channel(given: Any, what: str) -> Channel:
    """A through-bolt's drop-in channel: the axis it runs along, the way it's open, and the
    planes of the walls its head and its nut bear on, each square to that axis."""
    shape = (
        f'{what}\'s `channel` is {{"axis": "x", "open": "+z", "head_wall": "x=-6", '
        '"nut_wall": "x=6"}: the axis the bolt runs along, the way the channel is open, '
        "square to it, and the planes of the walls the head and the nut bear on"
    )
    if not isinstance(given, dict) or set(given) != CHANNEL_FIELDS:
        raise NoAnswer(f"{shape}, not {given!r}")
    axis = given["axis"]
    if axis not in AXES:
        raise NoAnswer(f"{shape}: its `axis` is one of {', '.join(AXES)}, not {axis!r}")
    opened = given["open"]
    if opened not in WAYS or opened[1] == axis:
        raise NoAnswer(
            f"{shape}: its `open` is one of {', '.join(w for w in WAYS if w[1] != axis)}, "
            f"square to its axis, not {opened!r}"
        )
    levels = []
    for key in ("head_wall", "nut_wall"):
        on, _, at = str(given[key]).partition("=")
        try:
            level = float(at)
        except ValueError:
            level = math.nan
        if on.strip() != axis or not math.isfinite(level):
            raise NoAnswer(
                f"{shape}: its `{key}` is a plane square to its axis, {axis}=NUMBER, not "
                f"{given[key]!r}"
            )
        levels.append(level)
    if abs(levels[0] - levels[1]) <= 1e-3:
        raise NoAnswer(
            f"{shape}: its head's wall and its nut's are one plane, {axis}={levels[0]:g}"
        )
    return Channel(axis, opened, levels[0], levels[1])


#: What a dovetail joint gives, and nothing else.
DOVETAIL_FIELDS = {"between", "kind", "slides", "latch"}
LATCH_FIELDS = {"on", "arm", "root"}
#: Where a latch's `arm` and `root` are given: the assembly's axes unless said, or its tab's
#: part's own file's (mqucifer/print-shop#295).
LATCH_FRAMES = ("assembly", "part")


def _dovetail(entry: dict[str, Any], first: str, second: str) -> Joint:
    """A tail on the first part in a socket in the second: the way it slides in, and its latch.
    Nothing here gives glue a figure, or a hold of any other kind."""
    what = f"the dovetail joint {first}+{second}"
    extra = sorted(set(entry) - DOVETAIL_FIELDS)
    if extra:
        raise NoAnswer(
            f"{what} gives {', '.join(extra)}: a dovetail gives `slides`, the way its tail goes "
            "in, and its `latch` where it has one, and nothing else. It's held by its own "
            "flanks, its seat and its latch: glue channels are the parts' shape and count as "
            "nothing, and no field gives glue, a bolt or the joint a figure"
        )
    slides = entry.get("slides")
    if not isinstance(slides, str) or slides not in WAYS:
        raise NoAnswer(
            f"{what}'s `slides` is the way the tail, on {first}, goes into its socket, in "
            f"{second}, toward its seat, one of {', '.join(WAYS)} in the assembly's axes, not "
            f"{slides!r}"
        )
    latch = entry.get("latch")
    if latch is None:
        return Joint((first, second), "dovetail", slides=slides)
    shape = (
        '{"on": PART, "arm": [[x0, y0, z0], [x1, y1, z1]], "root": "z=10"}: the part its tab is '
        "on, a box round the tab, hook and all, with one side on the tab's root, and the "
        "root's plane, in the assembly's coordinates; or, with \"frame\": \"part\", in the "
        "tab's part's own file's, carried with it by its `turn` and `at`"
    )
    if (
        not isinstance(latch, dict)
        or not set(latch) >= LATCH_FIELDS
        or set(latch) - LATCH_FIELDS - {"frame"}
    ):
        raise NoAnswer(f"{what}'s `latch` is {shape}, not {latch!r}")
    frame = _one_of(latch.get("frame", "assembly"), LATCH_FRAMES, f"{what}'s latch's `frame`")
    on = str(latch["on"])
    if on not in (first, second):
        raise NoAnswer(f"{what}'s latch is `on` {first} or {second}, not {on}")
    try:
        first_corner, second_corner = (_point(c, f"{what}'s latch's `arm`") for c in latch["arm"])
        axis, _, at = str(latch["root"]).partition("=")
        root = (str(_one_of(axis.strip(), AXES, f"{what}'s latch's `root` axis")), float(at))
    except (TypeError, ValueError) as exc:
        raise NoAnswer(f"{what}'s `latch` is {shape}, not {latch!r}") from exc
    pairs = list(zip(first_corner, second_corner, strict=True))
    box = (_point([min(p) for p in pairs], "a corner"), _point([max(p) for p in pairs], "a corner"))
    if min(b - a for a, b in zip(*box, strict=True)) <= 0:
        raise NoAnswer(
            f"{what}'s latch's `arm` has no room inside it: two corners apart on x, y and z"
        )
    k = AXES.index(root[0])
    if min(abs(root[1] - box[0][k]), abs(root[1] - box[1][k])) > 1e-6:
        raise NoAnswer(
            f"{what}'s latch's `root`, {root[0]}={root[1]:g}, isn't a side of its `arm`, which "
            f"runs from {box[0][k]:g} to {box[1][k]:g} along {root[0]}: one side of the box is "
            "on the tab's root, where it joins the rest of its part"
        )
    # A latch in its part's own axes is placed with that part once the parts are read.
    return Joint(
        (first, second), "dovetail", slides=slides, latch=Latch(on, box, root, frame == "part")
    )


def _carried(joint: Joint, parts: tuple[PartAt, ...]) -> Joint:
    """A dovetail whose latch is given in its tab's part's own axes, with that latch carried
    where the part's `turn` and `at` put it (mqucifer/print-shop#295)."""
    if joint.latch is None or not joint.latch.own:
        return joint
    part = next(p for p in parts if p.name == joint.latch.on)
    turn = tuple(tuple(float(v) for v in row) for row in part.matrix())
    latch = replace(joint.latch, turn=turn, shift=part.at)  # type: ignore[arg-type]
    return replace(joint, latch=latch)


def _held(entry: Any, names: list[str]) -> Held:
    try:
        axis, _, at = str(entry["plane"]).partition("=")
        found = Held(
            str(_one_of(axis.strip(), AXES, "a held plane's axis")),
            float(at),
            str(_one_of(entry.get("how", "standing"), HOW, "`how` it's held")),
            None if entry.get("part") is None else str(entry["part"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NoAnswer(
            f'each of `held` is a plane and how, {{"plane": "z=0", "how": "standing"}}: {entry!r}'
        ) from exc
    if found.part is not None and found.part not in names:
        raise NoAnswer(f"`held` names the part {found.part}, and the parts are {', '.join(names)}")
    return found


def read(path: str) -> Assembly:
    """The assembly file at `path`, or why it can't be read."""
    file = Path(path)
    if not file.is_file():
        raise NoAnswer(f"no file {path} in the working directory")
    try:
        data = json.loads(file.read_text())
    except ValueError as exc:
        raise NoAnswer(f"{path} isn't JSON: {exc}") from exc
    if not isinstance(data, dict) or not data.get("parts"):
        raise NoAnswer(f"{path} has no `parts`: an assembly is its parts, joints and where held")
    parts = tuple(_part(entry, file.parent) for entry in data["parts"])
    names = [p.name for p in parts]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise NoAnswer(f"each part has a name of its own: {', '.join(twice)}")
    joints = tuple(_carried(_joint(entry, names), parts) for entry in data.get("joints", ()))
    pairs = [frozenset(j.between) for j in joints]
    again = next((j.name for j in joints if pairs.count(frozenset(j.between)) > 1), None)
    if again:
        raise NoAnswer(f"the joint {again} is given twice: one joint between two parts")
    held = tuple(_held(entry, names) for entry in data.get("held", ()))
    return Assembly(path, parts, joints, held)
