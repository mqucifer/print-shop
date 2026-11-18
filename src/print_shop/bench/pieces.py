"""The `pieces` commands: a stained-glass panel as a piece map, read from a picture, checked
against the rules glass cutting follows, fixed as a glass cutter would, and built into a frame
and panes (mqucifer/print-shop#152, mqucifer/print-shop#153). A dome's map comes from rules, and
is cut into flat slices (mqucifer/print-shop#154), which join by bevelled, pinned seams
(mqucifer/print-shop#166).

    pieces trace IMAGE --width MM       a picture's pieces, as a piece map
    pieces check MAP                    the map against glass-cutting rules, each a figure
    pieces merge MAP A B                piece A joins its neighbour B: the line between goes
    pieces split MAP ID --line ...      a new lead line across a piece, from edge to edge
    pieces extend MAP --at X,Y          a dead-end line runs on to the next line or edge
    pieces drop-line MAP --at X,Y       a line is taken out, and the glass each side joins
    pieces widen MAP A B                the line between A and B, where it's under a nozzle's
                                        width, becomes that wide, and keeps its path
    pieces fix MAP STEPS                a file of those fixes, in order, in one call
    pieces build MAP --thickness T ...  the frame and a pane file for each colour
    pieces dome --diameter D --height H --repeats N --bands FILE
                                        a dome shade's pieces from rules, as a piece map
    pieces flatten MAP --slices N       a dome's map cut into flat slices, each its own map,
                                        with the bend at each fold
    pieces build MAP ... --seam LEFT,RIGHT
                                        a slice's two long edges as seams: bevelled at each
                                        straight length, with pins on one and holes on the other
    pieces seam A B --frames FA FB      two built neighbours placed round the dome: how their
                                        faces meet, and each pin in its hole

On the first test from a traced scene a run spent about 250 calls writing its own geometry code
before its plan, then ran one morphological opening over every piece in place of a merge, and
15 to 22% of the glass in one band became lead. So the bench does the geometry and the readings,
and whoever asks chooses each piece's fix. `piecemap.py` is the format each reads and writes.
"""

from __future__ import annotations

from typing import Any

from .pieces_build import run_build
from .pieces_check import run_check
from .pieces_dome import run_dome
from .pieces_fix import (
    run_drop_line,
    run_extend,
    run_fix,
    run_merge,
    run_split,
    run_widen,
)
from .pieces_flatten import run_flatten
from .pieces_seam import run_seam
from .pieces_trace import run_trace
from .report import Report


def run(args: Any, report: Report) -> None:
    if args.action == "trace":
        run_trace(args, report)
    elif args.action == "check":
        run_check(args, report)
    elif args.action == "merge":
        run_merge(args, report)
    elif args.action == "split":
        run_split(args, report)
    elif args.action == "extend":
        run_extend(args, report)
    elif args.action == "drop-line":
        run_drop_line(args, report)
    elif args.action == "widen":
        run_widen(args, report)
    elif args.action == "fix":
        run_fix(args, report)
    elif args.action == "dome":
        run_dome(args, report)
    elif args.action == "flatten":
        run_flatten(args, report)
    elif args.action == "seam":
        run_seam(args, report)
    else:
        run_build(args, report)
