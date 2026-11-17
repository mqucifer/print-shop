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
  file its layers are stacked along, the way up on the bed: it turns with the part.
- **A joint** is between two parts that meet face to face. `bonded` is glued or welded over the
  faces that meet. `bolted` names a bolt and an insert from the tables here, and where each bolt
  is, a point on its axis. `touching` is held by nothing: it can push, and can't pull.
- **`held`** is where the assembly stands or is fixed: the flat faces in a plane, `standing` on
  it (held along the plane's axis only, free to slide) or `fixed` to it. With none, it stands on
  its lowest level.

Plain Python and NumPy: `load` reads it in the second image and `fit-bed` in the first.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .bend import LIMITS
from .report import NoAnswer

AXES = ("x", "y", "z")
KINDS = ("bonded", "bolted", "touching")
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


#: Prusament PETG's datasheet (Prusa Polymers, version 1.1, 16-02-2022), as `bend` reads it: a
#: tensile modulus of 1.5 ± 0.1 GPa; "Interlayer Adhesion [MPa] 18 ± 4" over it is about 1.2%
#: strain, 0.9% at the low end of both, so 1% across the layers (`bend.LIMITS`); and 5.1 ± 0.1%
#: elongation at yield for a part printed flat, pulled in the plane of its layers. Poisson's ratio
#: is `bend`'s.
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
class PartAt:
    name: str
    file: str
    at: tuple[float, float, float]
    #: Each turn about one of the origin's axes, in order: its axis and its angle in degrees.
    turn: tuple[tuple[str, float], ...]
    material: Material
    #: The axis of its own file its layers are stacked along.
    layers: str

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
class Joint:
    between: tuple[str, str]
    kind: str
    bolt: Bolt | None = None
    insert: Insert | None = None
    at: tuple[tuple[float, float, float], ...] = ()
    #: The joint's own figure for what each bolt holds, in N, and where it's from.
    pull_n: float | None = None
    source: str = ""

    @property
    def name(self) -> str:
        return "+".join(self.between)

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
    )


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
    if kind != "bolted":
        extra = sorted({"bolt", "insert", "at", "pull_n"} & set(entry))
        if extra:
            raise NoAnswer(
                f"the joint {first}+{second} is {kind} and gives {', '.join(extra)}: only a "
                "bolted joint has a bolt"
            )
        return Joint((first, second), kind)
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
    joints = tuple(_joint(entry, names) for entry in data.get("joints", ()))
    pairs = [frozenset(j.between) for j in joints]
    again = next((j.name for j in joints if pairs.count(frozenset(j.between)) > 1), None)
    if again:
        raise NoAnswer(f"the joint {again} is given twice: one joint between two parts")
    held = tuple(_held(entry, names) for entry in data.get("held", ()))
    return Assembly(path, parts, joints, held)
