"""The printer's built-in camera, read-only: one JPEG at a time (mqucifer/print-shop#110).

The Centauri Carbon serves an MJPEG stream on `:3031/video`, with no auth and at most 4
streams at once (the operator, 2026-11-13, firmware V1.1.46). pycentauri's
`camera.snapshot` reads the first whole JPEG and closes the stream, so each grab holds one
of the four for about as long as a frame takes. It needs no control connection: no light,
no timelapse, nothing armed.

A frame is evidence beside the Sponsor's outcome, never the outcome itself, and no model
reads it here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from pycentauri import camera

__all__ = ["JPEG_START", "Camera", "CameraError", "CentauriCamera"]

#: A JPEG starts with SOI.
JPEG_START = b"\xff\xd8"


class CameraError(RuntimeError):
    """A grab that gave no frame: the camera off, busy, or answering with something else."""


class Camera(Protocol):
    def grab(self) -> bytes:
        """One JPEG frame.

        Raises:
            CameraError: If there's no frame to be had.
        """
        ...


@dataclass(frozen=True)
class CentauriCamera:
    """The CC1's camera at `host`. Each grab gives up after `timeout_s`."""

    host: str
    timeout_s: float = 5.0

    def grab(self) -> bytes:
        try:
            jpeg = asyncio.run(camera.snapshot(self.host, timeout=self.timeout_s))
        except (camera.SnapshotError, OSError, TimeoutError) as exc:
            raise CameraError(f"no frame from {self.host}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - httpx's own errors, unnamed here
            raise CameraError(f"no frame from {self.host}: {type(exc).__name__}: {exc}") from exc
        if not jpeg.startswith(JPEG_START):
            raise CameraError(f"what {self.host} sent isn't a JPEG")
        return jpeg
