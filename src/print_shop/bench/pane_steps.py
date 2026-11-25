"""Each pane file's level faces, inside the container, for the harness: what `standing.borne_out`
holds a flush frame's declared thicknesses to (mqucifer/print-shop#293).

A flush pane where it sits in its frame is a raised face under a body: its lowest point, then a
level face facing down at the body's underside, then its top. Laid for the bed it's the other way
up. So each file's lowest and highest points and its level faces, up and down, are enough to
read its body's and its face's thickness, with no figure the build gave.

Each argument is a file. It prints one line, `MARK` then JSON.
"""

from __future__ import annotations

import json
import sys
from typing import Any

MARK = "ROUNDS_PANE_STEPS "


def main(argv: list[str] | None = None) -> None:
    import numpy as np

    from .shapes import NoAnswer, flat_heights, load

    names = sys.argv[1:] if argv is None else argv
    found: dict[str, Any] = {}
    for name in names:
        try:
            mesh = load(name).fine()
            low, high = (float(v) for v in np.asarray(mesh.bounds)[:, 2])
            found[name] = {
                "low": round(low, 4),
                "high": round(high, 4),
                "level": flat_heights(mesh, least=0.01),
            }
        except (NoAnswer, OSError, ValueError) as exc:
            found[name] = {"unread": str(exc)[:300]}
    print(MARK + json.dumps(found))
