"""Which files of a print group take the same space, inside the container, for the harness.

On both real runs the delivered folder held `frame` beside `frame_base` and `cap`, its two halves,
so loading them together drew one frame on top of another. Files printed together can't overlap.
`clearance` reads every pair with a gap too, which over 15 files ran past 600 s, so this asks only
what grouping needs: how much each pair shares, by Manifold's intersection of their meshes, and
again by OCCT's `common` where the mesh says they share more than a sliver. Each file's box comes
too, for the size a delivered folder states.

Each argument is a file. It prints one line, `MARK` then JSON.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

MARK = "ROUNDS_OVERLAP "
#: Shared volume under this, in mm³, is faces touching, as a pane on its pocket's floor.
SLIVER_MM3 = 0.5


def main(argv: list[str] | None = None) -> None:
    from .shapes import NoAnswer, load

    began = time.monotonic()
    names = sys.argv[1:] if argv is None else argv
    found: dict[str, Any] = {"files": {}, "pairs": [], "unread": {}}
    loaded = {}
    for name in names:
        try:
            part = load(name)
            box = part.bounds()
            loaded[name] = (part, part.manifold(fine=False), box)
            found["files"][name] = {"bounds_mm": [[round(float(v), 3) for v in c] for c in box]}
        except (NoAnswer, OSError, ValueError) as exc:
            found["unread"][name] = str(exc)[:300]
    order = list(loaded)
    for i, a in enumerate(order):
        for b in order[i + 1 :]:
            (pa, ma, ba), (pb, mb, bb) = loaded[a], loaded[b]
            if (ba[1] < bb[0]).any() or (bb[1] < ba[0]).any():
                continue
            mesh = float((ma ^ mb).volume())
            if mesh <= SLIVER_MM3:
                continue
            pair: dict[str, Any] = {"files": [a, b], "mesh_mm3": round(mesh, 3)}
            if pa.is_brep and pb.is_brep:
                common = pa.shape.intersect(pb.shape)
                pair["occt_mm3"] = round(float(common.Volume()), 3)
            found["pairs"].append(pair)
    found["seconds"] = round(time.monotonic() - began, 2)
    print(MARK + json.dumps(found))
