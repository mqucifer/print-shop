"""The printer's camera, read through Home Assistant: one GET for each frame.

mqucifer/print-shop#110 kept a print's frames; mqucifer/print-shop#123 moved where they come
from. The Centauri Carbon's own camera server, on `:3031/video`, has very few connection slots
and releases them badly: a closed connection lingers in FIN-WAIT-2 and keeps its slot, so a
fresh connection for each frame "exhausts those slots and starves the stream" (pycentauri
0.9.1, `mjpeg_broadcast.py`). On the first print after mqucifer/print-shop#121, every grab got
`200 OK` and then no bytes.

So print-shop never opens the printer's stream (the Sponsor, 2026-11-14, on
mqucifer/print-shop#123). The Sponsor's Home Assistant integration for the printer already
holds its one camera connection and serves the latest image, so a frame is one
`GET /api/camera_proxy/<entity>` on Home Assistant's REST API. It needs no control connection
to the printer: no light, no timelapse, nothing armed.

Its settings are in the Mac's settings file, read inside the process (`load_env`):
- `HA_CAMERA_ENTITY`, the camera entity, such as `camera.centauri_carbon_chamber_camera`;
- `HA_URL`, Home Assistant's address, `http://<MQTT_HOST>:8123` when it isn't set, as the
  live dashboard checks read it;
- `HA_TOKEN`, a long-lived access token, never put on a command line or in any output.

Without the token or the entity, frames are off (`CameraOff`), and that's said once for each
print. A frame is evidence beside the Sponsor's outcome, never the outcome itself, and no model
reads it here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from print_shop.env import SETTINGS_PATH, load_env

__all__ = [
    "ENTITY_KEY",
    "JPEG_START",
    "TOKEN_KEY",
    "URL_KEY",
    "Camera",
    "CameraError",
    "CameraOff",
    "HomeAssistantCamera",
    "camera_from_env",
]

#: A JPEG starts with SOI.
JPEG_START = b"\xff\xd8"

#: The settings' keys.
ENTITY_KEY = "HA_CAMERA_ENTITY"
URL_KEY = "HA_URL"
TOKEN_KEY = "HA_TOKEN"


class CameraError(RuntimeError):
    """A grab that gave no frame, and why, in words fit for one line of the log."""


class Camera(Protocol):
    def grab(self) -> bytes:
        """One JPEG frame.

        Raises:
            CameraError: If there's no frame to be had.
        """
        ...


@dataclass(frozen=True)
class CameraOff:
    """No camera to read, and why: the frame keeper says so once for each print."""

    why: str

    def grab(self) -> bytes:
        raise CameraError(self.why)


@dataclass(frozen=True)
class HomeAssistantCamera:
    """Home Assistant's camera entity for the printer: its current image, one GET for each
    frame, given up after `timeout_s`.

    `transport` is a test's `httpx.MockTransport`; None is the network.
    """

    url: str
    entity_id: str
    token: str = field(repr=False)
    timeout_s: float = 10.0
    transport: httpx.BaseTransport | None = field(default=None, repr=False, compare=False)

    def grab(self) -> bytes:
        endpoint = f"{self.url.rstrip('/')}/api/camera_proxy/{self.entity_id}"
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.get(endpoint, headers={"Authorization": f"Bearer {self.token}"})
        except httpx.TimeoutException:
            raise CameraError(
                f"Home Assistant gave no image of {self.entity_id} in {self.timeout_s:g} s"
            ) from None
        except httpx.HTTPError as exc:
            # The exception names the address and the cause; the token is a header, never in it.
            raise CameraError(
                f"Home Assistant couldn't be reached at {self.url}: {type(exc).__name__}: {exc}"
            ) from None
        status = response.status_code
        if status == 401:
            raise CameraError(f"Home Assistant refused {TOKEN_KEY} (401)")
        if status == 404:
            raise CameraError(f"Home Assistant has no camera {self.entity_id} (404)")
        if status != 200:
            raise CameraError(
                f"Home Assistant answered {status} {response.reason_phrase} for {self.entity_id}"
            )
        jpeg = response.content
        if not jpeg:
            raise CameraError(f"Home Assistant sent an empty image of {self.entity_id}")
        if not jpeg.startswith(JPEG_START):
            kind = response.headers.get("content-type", "no content type")
            raise CameraError(f"what Home Assistant sent of {self.entity_id} isn't a JPEG ({kind})")
        return jpeg


def camera_from_env(path: Path | None = None) -> HomeAssistantCamera | CameraOff:
    """The camera the Mac's settings name, or frames off, saying which setting is missing."""
    where = path or SETTINGS_PATH
    env = load_env(where)
    entity = env.get(ENTITY_KEY, "")
    token = env.get(TOKEN_KEY, "")
    url = env.get(URL_KEY, "") or (
        f"http://{env['MQTT_HOST']}:8123" if env.get("MQTT_HOST") else ""
    )
    missing = [key for key, value in ((ENTITY_KEY, entity), (TOKEN_KEY, token), (URL_KEY, url))
               if not value]
    if missing:
        return CameraOff(f"frames are off: {' and '.join(missing)} not set in {where}")
    return HomeAssistantCamera(url=url, entity_id=entity, token=token)
