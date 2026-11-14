"""A print's camera frames: one as it moves to a new layer, and one at the end (mqucifer/print-shop#110).

The outcome watcher already reads the printer's status (`outcome.Watcher`); when the layer it
reports has changed since the last frame, it asks for one here, and once more when the print
ends. So frames are a layer apart at most, and further apart when layers come faster than the
watcher looks: each is kept with the layer it was taken at.

Each frame is a JPEG under the job's folder, `jobs/<id>/frames/`, named by its layer (or
`end.jpg`), and a row of the ledger's `print_frames`. **A grab that fails is logged and skipped:
it never stops the print, or the watcher.** A frame is evidence beside the Sponsor's outcome,
never the outcome (`ledger.py`: "an outcome is never guessed"), and no model reads it here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from print_shop.ledger import Ledger
from print_shop.printer.camera import Camera

__all__ = ["FRAMES_DIR", "FrameKeeper"]

logger = logging.getLogger(__name__)

#: Each job's frames, inside its folder.
FRAMES_DIR = "frames"


class FrameKeeper:
    """Takes a print's frames through a read-only camera, and keeps them with the job."""

    def __init__(self, *, camera: Camera, ledger: Ledger, jobs: Path) -> None:
        self._camera = camera
        self._ledger = ledger
        self._jobs = jobs
        self._last: dict[str, int] = {}

    def folder(self, job_id: str) -> Path:
        return self._jobs / job_id / FRAMES_DIR

    def at_layer(self, job_id: str, layer: int | None, total: int | None) -> Path | None:
        """A frame if the print has moved to a layer it has none of: the saved file, or None."""
        if layer is None or layer < 1 or self._last.get(job_id) == layer:
            return None
        self._last[job_id] = layer
        return self._take(job_id, "layer", layer, total, f"layer-{layer:04d}.jpg")

    def at_end(self, job_id: str, layer: int | None, total: int | None) -> Path | None:
        """The print as it ended: the saved file, or None."""
        self._last.pop(job_id, None)
        return self._take(job_id, "end", layer, total, "end.jpg")

    def _take(
        self, job_id: str, kind: str, layer: int | None, total: int | None, name: str
    ) -> Path | None:
        path = self.folder(job_id) / name
        try:
            if path.exists():
                return None  # taken already, by this process or one before it
            jpeg = self._camera.grab()
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix(".part")
            part.write_bytes(jpeg)
            part.replace(path)
            self._ledger.record_frame(job_id, kind, layer, total, path, len(jpeg))
        except Exception:  # noqa: BLE001 - a frame is never worth stopping the watcher for
            logger.warning("no %s frame for job %s at layer %s; skipped", kind, job_id, layer,
                           exc_info=True)
            return None
        return path
