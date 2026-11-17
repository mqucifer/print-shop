"""A pane built 0.03 mm further in than its play, planted on `rules.json` by changing what one
function returns, in this process only (mqucifer/print-shop#161). `pieces build` holds each
pane's edge to the play square to its pocket's sides, and a pocket of pixel steps made that
reading wrong. This is the control: a pane that really is off its play, along its sides, still
has no answer. Prints the verdict and why, as JSON. It imports the bench from where the
container mounts it, and reads a map `planted_pieces.py` wrote."""

import contextlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/bench")
import bench.cli as cli  # type: ignore[import-not-found]  # noqa: E402
import bench.pieces_build as build  # type: ignore[import-not-found]  # noqa: E402

curves_of = build.piece_curves
OFF = 0.03


def too_far_in(piece: Any, lip: float, play: float, fit: float) -> dict[str, Any]:
    """A piece's curves, its pane its pocket's sides moved in by the play and `OFF` more."""
    found = curves_of(piece, lip, play, fit)
    found["pane"] = [build.offset_ring(ring, play + OFF) for ring in found["pocket"]]
    found["pane_2d"] = build._ring_polygon(found["pane"])
    return dict(found)


build.piece_curves = too_far_in
said = io.StringIO()
with contextlib.redirect_stdout(said):
    code = cli.main(
        [
            "pieces", "build", "rules.json", "--thickness", "3", "--pane", "1", "--lip", "1.2",
            "--play", "0.25", "--build-out", "planted-play",
        ]  # fmt: skip
    )
saved = re.search(r"saved: (\S+)\.txt and \.json", said.getvalue())
record = json.loads(Path(f"{saved.group(1)}.json").read_text()) if saved else {}
print(json.dumps({"code": code, "verdict": record.get("verdict"), "why": record.get("why")}))
