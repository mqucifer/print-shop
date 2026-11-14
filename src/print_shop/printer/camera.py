"""The printer's camera, read-only, behind one interface: a frame, or why there's none.

mqucifer/print-shop#110 kept a print's frames, each a fresh grab of the printer's own MJPEG
stream on `:3031/video`. mqucifer/print-shop#123 found why none was ever saved: the Centauri
Carbon's camera server has very few connection slots and releases them badly. A closed
connection lingers in FIN-WAIT-2 and keeps its slot, so a fresh connection for each frame
"exhausts those slots and starves the stream" (pycentauri 0.9.1, `mjpeg_broadcast.py`). On the
first print after mqucifer/print-shop#121, every grab got `200 OK` and then no bytes.

**So print-shop never opens the printer's stream.** Frames will come through Home Assistant,
whose integration already holds the printer's one camera connection (the Sponsor, 2026-11-14,
on mqucifer/print-shop#123). How they reach print-shop from there is the Sponsor's to choose,
so until it's built, the camera is off (`HELD`), said once for each print, and nothing else
about a print changes.

A frame is evidence beside the Sponsor's outcome, never the outcome itself, and no model reads
it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = ["HELD", "JPEG_START", "Camera", "CameraError", "CameraOff", "camera_for_prints"]

#: A JPEG starts with SOI.
JPEG_START = b"\xff\xd8"


class CameraError(RuntimeError):
    """A grab that gave no frame, and why, in words fit for one line of the log."""


class Camera(Protocol):
    def grab(self) -> bytes:
        """One JPEG frame.

        Raises:
            CameraError: If there's no frame to be had, saying why in one line.
        """
        ...


@dataclass(frozen=True)
class CameraOff:
    """No camera to read, and why: the frame keeper says so once for each print, and grabs
    nothing."""

    why: str

    def grab(self) -> bytes:
        raise CameraError(self.why)


#: Why prints have no frames until the source is built (mqucifer/print-shop#123).
HELD = CameraOff(
    "frames are off: they'll come through Home Assistant, and how is still being chosen "
    "(mqucifer/print-shop#123)"
)


def camera_for_prints() -> Camera:
    """The camera a print's frames come from. Never the printer's own stream."""
    return HELD
