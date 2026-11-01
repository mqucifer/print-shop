"""Any picture as a PNG the model can be shown, inside the container, so the Mac never parses one.

    picture.py <in> <out>    the longest side at most `LONGEST`, turned as its camera said

A reference photo can be many megabytes: this is what reaches the model. It prints one line,
`MARK` then JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MARK = "ROUNDS_PICTURE "
LONGEST = 1568


def main(argv: list[str] | None = None) -> None:
    # Here, so the host can read `MARK` with no image library.
    from PIL import Image, ImageOps

    source, target = sys.argv[1:] if argv is None else argv
    try:
        with Image.open(source) as image:
            shown = ImageOps.exif_transpose(image).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - whatever it is, it isn't a picture that opens
        print(MARK + json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]}))
        return
    size = shown.size
    shown.thumbnail((LONGEST, LONGEST))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    shown.save(target, "PNG")
    print(MARK + json.dumps({"size": list(size), "shown": list(shown.size), "out": target}))
