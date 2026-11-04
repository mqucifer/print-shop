"""The evaluations' files, written under the current directory, one folder each, in the bench's
image: `scripts/bench.py python make_evals.py`. Every shape is made here, from scratch, with its
right answer from arithmetic: nothing comes from a Sponsor's design or a run's files.

Each frame is a plate 4 mm tall with pockets at z 1.5 to 2.5 for panes 1 mm thick, with 0.25 mm of
play round each pane, unless its folder says otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cadquery as cq

#: Where every pocket sits, and the play round a pane in it, in mm.
POCKET = (1.5, 2.5)
PLAY = 0.25


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Any:
    return (
        cq.Workplane("XY")
        .box(x1 - x0, y1 - y0, z1 - z0)
        .translate(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    )


def save(shape: Any, folder: str, name: str) -> None:
    Path(folder).mkdir(parents=True, exist_ok=True)
    cq.exporters.export(shape, f"{folder}/{name}.step")


def cell(frame: Any, x0: float, x1: float, y0: float, y1: float, lip: float, floor: bool) -> Any:
    """A square pocket, and its opening `lip` in from the pocket's wall on both faces, or only
    above. With 0.25 mm of play, the lip before play is `lip` less 0.25, and after play less 0.5."""
    frame = frame.cut(box(x0, x1, y0, y1, *POCKET))
    above = box(x0 + lip, x1 - lip, y0 + lip, y1 - lip, POCKET[1], 4)
    frame = frame.cut(above)
    if not floor:
        frame = frame.cut(box(x0 + lip, x1 - lip, y0 + lip, y1 - lip, 0, POCKET[0]))
    return frame


def pane(x0: float, x1: float, y0: float, y1: float) -> Any:
    return box(x0 + PLAY, x1 - PLAY, y0 + PLAY, y1 - PLAY, *POCKET)


def light() -> None:
    """Three cells, each opening 1.5 mm in from its pocket: lips of 1.25 mm before play and 1.0
    after. `pane_a` is 225 of 306.25 mm² open, 73.5%. `pane_b` has a
    floor under it, 0%. `pane_c` is a small cell, 25 of 56.25 mm², 44.4%: under the request's 50%
    by its lip, not by a floor."""
    frame = box(0, 60, 0, 24, 0, 4)
    cells = {"pane_a": (3, 21, 3, 21), "pane_b": (25, 43, 3, 21), "pane_c": (46, 54, 8, 16)}
    for name, (x0, x1, y0, y1) in cells.items():
        frame = cell(frame, x0, x1, y0, y1, 1.5, floor=name == "pane_b")
        save(pane(x0, x1, y0, y1), "light", name)
    save(frame, "light", "frame")


def lip_after_play() -> None:
    """One cell whose opening is 1.0 mm in from its pocket: a lip of 0.75 mm before play, and
    0.5 mm after its 0.25 mm of play."""
    save(cell(box(0, 30, 0, 30, 0, 4), 5, 25, 5, 25, 1.0, floor=False), "lip", "frame")
    save(pane(5, 25, 5, 25), "lip", "pane_1")


def hanging() -> None:
    """A 40 mm square frame of one cell, and a tab above it with a 3 mm hole at 20,43.5: 1.0 mm of
    wall above the hole to the tab's edge, 3.5 mm to each side. `info` reads the tab's top at 46."""
    frame = cell(box(0, 40, 0, 40, 0, 4), 5, 35, 5, 35, 1.5, floor=False)
    frame = frame.union(box(15, 25, 40, 46, 0, 4))
    frame = frame.cut(cq.Workplane("XY").center(20, 43.5).circle(1.5).extrude(4))
    save(frame, "hanging", "frame")
    save(pane(5, 35, 5, 35), "hanging", "pane_1")


def sharp_tip() -> None:
    """A triangular cell whose apex is 10 degrees: its pane tapers to a point no nozzle prints."""
    outline = [(5.0, 5.0), (45.0, 5.0), (45.0, 12.0)]
    frame = box(0, 50, 0, 17, 0, 4)
    pocket = cq.Workplane("XY").workplane(offset=POCKET[0]).polyline(outline).close()
    frame = frame.cut(pocket.extrude(POCKET[1] - POCKET[0]))
    inner = [(12.0, 6.0), (44.0, 6.0), (44.0, 10.6)]
    frame = frame.cut(cq.Workplane("XY").polyline(inner).close().extrude(4))
    save(frame, "tip", "frame")
    shrunk = cq.Workplane("XY").polyline(outline).close().extrude(1).val()
    tip = cq.Workplane(obj=shrunk).translate((0, 0, POCKET[0]))
    save(tip, "tip", "pane_tip")


def thick() -> None:
    """A pane 2.0 mm thick from an earlier round, in a pocket 2.0 mm tall."""
    frame = box(0, 30, 0, 30, 0, 5)
    frame = frame.cut(box(5, 25, 5, 25, 1.5, 3.5)).cut(box(6.5, 23.5, 6.5, 23.5, 0, 5))
    save(frame, "thick", "frame")
    save(box(5.25, 24.75, 5.25, 24.75, 1.5, 3.5), "thick", "pane_1")


if __name__ == "__main__":
    light()
    lip_after_play()
    hanging()
    sharp_tip()
    thick()
    print("made")
