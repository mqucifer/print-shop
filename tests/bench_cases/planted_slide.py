"""The slide's verdict when its two methods read differently (mqucifer/print-shop#84), planted on
`hold/hooks.step` and `hold/fan.step` by changing what one method returns, in this process only.
Prints each case's verdict, its why and the reply's first line, as JSON. It imports the bench from
where the container mounts it, and reads fixtures `make_fixtures.py` wrote."""

import contextlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, "/opt/bench")
import bench.cli as cli  # type: ignore[import-not-found]  # noqa: E402
import bench.slide as slide  # type: ignore[import-not-found]  # noqa: E402

HOOKS = ["clearance", "hold/hooks.step", "hold/fan.step", "--slide", "-z"]
#: A box round each hook, so the agreed reply names where both methods first met.
NAMED = [
    "--box", "east=13.5,-4,-1.5,15.5,4,0",
    "--box", "west=-15.5,-4,-1.5,-13.5,4,0",
    "--box", "north=-4,13.5,-1.5,4,15.5,0",
    "--box", "south=-4,-15.5,-1.5,4,-13.5,0",
]  # fmt: skip
meet, by_rays = slide._meet, slide._by_rays


def late(*args: Any) -> Any:
    """The step and halving reading 0.05 mm late, as it did on eval-pocket-2's lid."""
    first = meet(*args)
    return None if first is None else first + 0.05


def blind(*args: Any) -> Any:
    """Rays that meet nothing."""
    _, path, _, count = by_rays(*args)
    return None, path, np.empty((0, 3)), count


def elsewhere(*args: Any) -> Any:
    """Rays that first meet the hooks somewhere none of the step's contacts is."""
    first, path, _, count = by_rays(*args)
    return first, path, np.array([[0.0, 0.0, 7.5]]), count


def sliver() -> list[list[list[float]]]:
    """The first contacts of a block on four feet whose walls lie 1e-6 mm into its sides, as
    CI's x86 read the walls of `hold/feet`: an overlap the thickness of arithmetic, joined to
    each foot. `_contacts` reads the four feet alone."""
    import manifold3d as m3

    def cube(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Any:
        return m3.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))

    block = cube(-15, 15, -15, 15, 0, 7.5)
    walls = cube(-20, 20, -20, 20, 7.5, 9.5)
    inset = 1e-6
    for turn in (0, 90, 180, 270):
        wall = cube(15 - inset, 17, -17, 17, -1.1, 7.5) + cube(14.5, 15, -4, 4, -1.1, -0.1)
        walls = walls + wall.rotate((0, 0, turn))
    found = slide._contacts(block, walls, np.array([0.0, 0.0, -1.0]), "z", 0.1)
    return [
        [[round(float(v), 4) for v in low], [round(float(v), 4) for v in high]]
        for low, high in found
    ]


def ran(argv: list[str], **patched: Any) -> dict[str, Any]:
    slide._meet = patched.get("meet", meet)
    slide._by_rays = patched.get("rays", by_rays)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        code = cli.main(argv)
    text = said.getvalue()
    saved = re.search(r"saved: (\S+)\.txt and \.json", text)
    record = json.loads(Path(f"{saved.group(1)}.json").read_text()) if saved else {}
    found = record.get("numbers", {}).get("slide", {})
    return {
        "code": code,
        "verdict": record.get("verdict"),
        "first_mm": found.get("first_mm"),
        "first_rays_mm": found.get("first_rays_mm"),
        "notes": record.get("notes", []),
        "text": text,
    }


print(
    json.dumps(
        {
            "agreed": ran(HOOKS + NAMED, meet=late),
            "agreed_unnamed": ran(HOOKS, meet=late),
            "verdicts_differ": ran(HOOKS + ["--hold", "0.22"], meet=late),
            "rays_meet_nothing": ran(HOOKS, rays=blind),
            "rays_elsewhere": ran(HOOKS, meet=late, rays=elsewhere),
            "sliver_contacts": sliver(),
        }
    )
)
