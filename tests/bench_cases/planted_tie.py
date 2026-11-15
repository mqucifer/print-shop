"""A slide whose first contact is a tie round a ring (mqucifer/print-shop#135), planted on
`ring/channel.step` and `ring/strip.step` by changing what one method returns, in this process
only. In an eval the step and halving read, beside the ring, a 0.03 mm patch of it that no ray's
first point fell in, and it was `DISAGREE`. A clean ring reads one box, so the patch is added to the
step's first contacts here, as the eval read it. Prints each case's verdict, its why and the reply,
as JSON. It imports the bench from where the container mounts it, and reads fixtures
`make_fixtures.py` wrote."""

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

RING = ["clearance", "ring/channel.step", "ring/strip.step", "--slide", "+z"]
CATCH = ["clearance", "catch/channel.step", "catch/strip.stl", "--slide", "+z"]
contacts, meet = slide._contacts, slide._meet


def patched(low: list[float], high: list[float]) -> Any:
    """The step and halving's first contacts, and one patch more: a box 0.03 mm across."""

    def read(*args: Any) -> Any:
        return [*contacts(*args), (np.array(low), np.array(high))]

    return read


#: On the ring, on the slope's -x side where the strip's edge meets it at z 4.2, between two of
#: the points the rays go from along that edge, 11.6 / 231 mm apart, at y 0.0251 and 0.0753: no
#: ray's first point is in it.
ON_RING = patched([-5.81, 0.035, 4.2], [-5.78, 0.065, 4.225])
#: Up the same slope, z 4.4 to 4.6: the strip's top meets it only after 1.0 to 1.2 mm, far past
#: the ring.
UP_THE_SLOPE = patched([-5.6, -0.5, 4.4], [-5.4, 0.5, 4.6])


def late(*args: Any) -> Any:
    """The step and halving reading 0.05 mm late: past the tie band from the rays."""
    first = meet(*args)
    return None if first is None else first + 0.05


def as_the_eval(*args: Any) -> Any:
    """The step and halving reading 0.0094 mm late, inside the tie band, as the eval read its
    ring: 0.8094 against the rays' 0.8000. It read this ring's slope 0.005 mm late itself until
    a widening contact was followed back by its curve (mqucifer/print-shop#143), and the two
    methods' places are compared only where their distances differ, so the eval's is planted."""
    first = meet(*args)
    return None if first is None else first + 0.0094


def ran(argv: list[str], **planted: Any) -> dict[str, Any]:
    slide._contacts = planted.get("contacts", contacts)
    slide._meet = planted.get("meet", meet)
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
        "contacts": len(found.get("contacts", [])),
        "tie": found.get("tie"),
        "round": found.get("round"),
        "text": text,
    }


print(
    json.dumps(
        {
            "patch_on_the_ring": ran(RING, contacts=ON_RING, meet=as_the_eval),
            "patch_up_the_slope": ran(RING, contacts=UP_THE_SLOPE, meet=as_the_eval),
            "patch_read_late": ran(RING, contacts=ON_RING, meet=late),
            # A catch round a thin band (mqucifer/print-shop#143), with nothing planted on its
            # contacts: its teeth are first contacts no ray first meets. Read 0.05 mm late, the
            # two methods' distances are past the tie band, and it's a disagreement still.
            "catch_read_late": ran(CATCH, meet=late),
        }
    )
)
