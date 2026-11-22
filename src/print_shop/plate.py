"""Several parts printed as one job: what each is called, and where it went on the plate.

mqucifer/print-shop#109. A job of several STLs is one review, one gate and one print. The ledger
has a row for each object, so each part's outcome is its own. The first part's row has the job's
id, which is what the G-code's name, the job's folder, its log and its frames go by; the others'
add their number to it (`row_id`).

These are the facts of a plate, kept in the job's state and beside the ledger's rows. Arranging
and checking one is `slicer/arrange.py`'s.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

__all__ = [
    "BY_LAYER", "BY_OBJECT", "SEQUENCE", "SEQUENCES", "GivenPart", "OnPlate", "PlateLayout",
    "PlateRow", "plate_job", "row_id",
]

#: Orca's setting for the order a plate prints in, and its two values.
SEQUENCE = "print_sequence"
BY_OBJECT = "by object"
BY_LAYER = "by layer"
SEQUENCES = (BY_OBJECT, BY_LAYER)

#: The map's size, in cells of three characters by a line: near enough square on a terminal.
_COLS, _ROWS = 10, 7

#: A later object's row: the job's id, a uuid, and the part's number.
_LATER = re.compile(r"^([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})-(\d+)$")


def row_id(job_id: str, n: int) -> str:
    """The ledger's id for the job's `n`th part, from 1: the job's own for the first."""
    return job_id if n == 1 else f"{job_id}-{n}"


def plate_job(job_id: str) -> str:
    """The job a row is of: the one whose folder, log, G-code and frames it shares."""
    found = _LATER.match(job_id)
    return found.group(1) if found else job_id


class OnPlate(BaseModel):
    """One object as the slice has it."""

    model_config = {"frozen": True}

    n: int  # the part's number, from 1, as it was given
    name: str
    x: float  # its middle on the plate
    y: float
    where: str  # that in words: "back left"
    height_mm: float
    #: The layer its own top is on, counted through the whole print: no other height's is.
    top_layer: int
    #: When it prints, from 1, and its layers counted through the whole print. None by layer.
    order: int | None = None
    layers: tuple[int, int] | None = None

    def placed(self) -> str:
        """Where it is and when it prints."""
        said = f"at x {self.x:.0f} y {self.y:.0f}, {self.where}"
        if self.order is not None and self.layers is not None:
            said += f"; prints number {self.order}, layers {self.layers[0]} to {self.layers[1]}"
        return f"{said}; {self.height_mm:g} mm tall"

    def line(self) -> str:
        return f"part {self.n}, {self.name}: {self.placed()}"


class PlateLayout(BaseModel):
    """The plate as it was sliced: its order, why, and each object."""

    model_config = {"frozen": True}

    sequence: str
    #: By object, what was checked; by layer, why it isn't by object.
    why: str
    objects: tuple[OnPlate, ...]
    #: The plate's width and depth, in mm, x and y counted from its front left.
    bed: tuple[float, float]

    @property
    def by_object(self) -> bool:
        return self.sequence == BY_OBJECT

    def headline(self) -> str:
        how = "BY OBJECT, one after another" if self.by_object else "BY LAYER, all at once"
        return f"{len(self.objects)} parts on one plate, printed {how}: {self.why}"

    def lines(self) -> list[str]:
        shown = sorted(self.objects, key=lambda o: (o.order or 0, o.n))
        return [self.headline(), *(f"  {o.line()}" for o in shown),
                *(f"    {row}" for row in self.drawn())]

    def drawn(self) -> list[str]:
        """The parts' numbers where they are, seen from above with the front nearest: nothing
        is written on the parts. Where two would share a spot it's drawn closer up, saying so;
        where they still would, there's no map, rather than a wrong one."""
        back = "back"
        cells = self._cells(0.0, 0.0, *self.bed)
        if cells is None:
            xs, ys = [o.x for o in self.objects], [o.y for o in self.objects]
            cells = self._cells(min(xs) - 1, min(ys) - 1,
                                max(xs) - min(xs) + 2, max(ys) - min(ys) + 2)
            back += " (closer up, not to the plate's edges)"
        if cells is None:
            return []
        edge = "+" + "-" * (3 * _COLS) + "+"
        rows = [
            "|" + "".join(f"{cells.get((col, row), ''):^3}" for col in range(_COLS)) + "|"
            for row in reversed(range(_ROWS))
        ]
        return [back, edge, *rows, edge, "front"]

    def _cells(
        self, left: float, front: float, width: float, depth: float,
    ) -> dict[tuple[int, int], int] | None:
        """Each part's number by its cell, or None where two share one."""
        found: dict[tuple[int, int], int] = {}
        for o in self.objects:
            col = int((o.x - left) / width * _COLS)
            row = int((o.y - front) / depth * _ROWS)
            at = (min(max(col, 0), _COLS - 1), min(max(row, 0), _ROWS - 1))
            if at in found:
                return None
            found[at] = o.n
        return found


class GivenPart(BaseModel):
    """One of the files a job was given."""

    model_config = {"frozen": True}

    name: str
    path: str  # where it was given from
    sha256: str
    #: The sidecar or design run its context was read from, or "".
    context: str = ""


class PlateRow(BaseModel):
    """What a ledger row keeps of the plate its part was printed on."""

    model_config = {"frozen": True}

    job_id: str  # the job's: the first part's row
    n: int
    parts: tuple[GivenPart, ...]
    layout: PlateLayout
    #: The job was given with `--as-laid`, whatever pose a part was then sent back to.
    as_laid: bool = False

    @property
    def first(self) -> bool:
        return self.n == 1

    def short(self, most: int = 200) -> str:
        """The part as a list names it: its file can't tell two copies apart, its number can."""
        return f"{self.parts[self.n - 1].name[:most]} (part {self.n} of {len(self.parts)})"

    def label(self) -> str:
        """The part as the outcome asks about it, so it's known on the plate by its place."""
        here = next(o for o in self.layout.objects if o.n == self.n)
        # Home Assistant holds 255 characters, and the part's place mustn't be what's cut.
        return f"{self.short(48)}, {here.where}, x {here.x:.0f} y {here.y:.0f}"
