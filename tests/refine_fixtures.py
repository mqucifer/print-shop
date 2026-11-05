"""Approved runs made up for the refinement pass's tests (mqucifer/print-shop#51).

Each is a run folder and its ledger rows, as a run that reached `met` and was approved leaves
them: `state.json`, `needs.json`, `events.jsonl` with the `done` that gave each file's colour,
`approvals/1/review/` and the approved round's `judged/`, with the script that made it. The
geometry is ours, plain boxes from trimesh, written by a script whose numbers are at its top, so
`params` can change them. Nothing here is a Sponsor's design.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.paths import content_hash

#: A frame 40 mm square and 4 mm tall, with a 26 mm window and a 30 mm pocket over it whose
#: floor is at z 1.5; a pane 29.5 mm square and 1 mm thick sits on that floor. A second window
#: and pane beside it when `PANES` is 2, and a bar across the first window when `BAR` isn't 0.
SCRIPT = """\
import trimesh

CEILING = {ceiling}
FLOOR = 1.5
PANE = {pane}
PANES = {panes}
BAR = {bar}


def box(x0, y0, z0, x1, y1, z1):
    made = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
    made.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    return made


frame = box(0, 0, 0, 40 * PANES, 40, 4.0)
for k in range(PANES):
    x = 40 * k
    frame = frame.difference(box(x + 7, 7, -1, x + 33, 33, 5))
    frame = frame.difference(box(x + 5, 5, FLOOR, x + 35, 35, CEILING))
    box(x + 5.25, 5.25, FLOOR, x + 34.75, 34.75, FLOOR + PANE).export(f"pane_{{k + 1}}.stl")
if BAR:
    frame = frame.union(box(20 - BAR / 2, 7, 0, 20 + BAR / 2, 33, FLOOR))
frame.export("frame.stl")
"""

COLOURS = ("amber", "blue")


def make_run(
    root: Path,
    name: str,
    *,
    ceiling: float = 2.6,
    pane: float = 1.0,
    panes: int = 1,
    bar: float = 0.0,
    pane_colours: tuple[str, ...] = COLOURS,
) -> tuple[Path, DesignLedger]:
    """An approved run under `root`, and the ledger beside it."""
    folder = root / name
    judged = folder / "rounds" / "001" / "judged"
    judged.mkdir(parents=True)
    (judged / "make_frame.py").write_text(
        SCRIPT.format(ceiling=ceiling, pane=pane, panes=panes, bar=bar), encoding="utf-8"
    )
    subprocess.run([sys.executable, "-I", "make_frame.py"], cwd=judged, check=True)
    files = ["frame.stl", *(f"pane_{k + 1}.stl" for k in range(panes))]
    looks: dict[str, dict[str, str]] = {"frame.stl": {"colour": "grey", "label": "frame"}}
    for k in range(panes):
        looks[f"pane_{k + 1}.stl"] = {"colour": pane_colours[k], "label": "pane"}
    group = folder / "approvals" / "1" / "review" / "A-with-pause"
    group.mkdir(parents=True)
    held: dict[str, str] = {}
    for f in files:
        shutil.copyfile(judged / f, group / f)
        held[f] = content_hash(f, (judged / f).read_bytes())
    (group.parent / "README.md").write_text(
        "# made up\n\n## A-with-pause\n\nPrint it, pause, drop the panes in. (the model's words)\n",
        encoding="utf-8",
    )
    shutil.copytree(group.parent, folder / "review")
    needs = [
        {
            "id": "pane-thin",
            "what": "the pane prints",
            "source": "request",
            "by": "bench",
            "command": "thin pane_1.stl",
            "verdicts": ["OK"],
        },
    ]
    (folder / "needs.json").write_text(json.dumps(needs), encoding="utf-8")
    state = {
        "name": name,
        "stage": "rounds",
        "round": 1,
        "ending": {"name": "approved", "said": {"approval": 1}},
    }
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    done_args = {
        "script": "make_frame.py",
        "part": [{"name": "frame", "files": files}],
        "looks": looks,
    }
    events = [
        {"kind": "tool", "name": "done", "outcome": "ok", "arguments": json.dumps(done_args)},
        {
            "kind": "ended",
            "name": "met",
            "said": {"round": 1, "script": "make_frame.py", "approval": 1},
        },
        {"kind": "ended", "name": "approved", "said": {"approval": 1}},
    ]
    (folder / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    ledger = DesignLedger(root / "ledger.sqlite")
    ledger.run_started(
        name,
        request_sha="0",
        events=str(folder / "events.jsonl"),
        harness_commit="0",
        skills_sha="0",
        image="none",
        image_id=None,
        alias="none",
        limits={},
    )
    review: dict[str, Any] = {
        "folder": str(folder / "review"),
        "groups": [{"folder": "A-with-pause", "name": "with pause", "files": held}],
        "shared": {},
    }
    ledger.add(
        "rounds",
        run=name,
        round=1,
        started_at="2026-11-05T00:00:00+00:00",
        first_call=1,
        needs_version=1,
        start_files="{}",
        failing_at_start="[]",
    )
    ledger.approval_asked(
        name,
        1,
        round=1,
        at_call=1,
        sheet=None,
        sheet_sha=None,
        part_rows=[],
        part_hashes=held,
        closing="",
        review=review,
    )
    ledger.decided(name, 1, "approved", "stand-in: a test", "")
    return folder, ledger
