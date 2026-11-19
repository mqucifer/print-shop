"""A helper's answer, however long it is (mqucifer/print-shop#181).

A helper answers on one line, its mark then JSON, and the harness keeps only the end of a
container's output (`rounds/box.py`'s `KEEP`, 256,000 bytes). On a traced suncatcher, a frame
and 12 pane files, the `mesh` request's answer was one line of 1,459,288 bytes, mostly each
pane's outline points, and its start, with the mark, was dropped: every file then read "can't
tell", though the bench had laid and meshed each one.

So a request that names `answer`, a file in the folder the helper runs in, has its answer
written there, and the line says only where, `{"answer_file": ..., "bytes": ...}`, which the
harness reads back. A request with none is answered on the line, as before. Standard library
only: the harness imports it outside the image.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The key of the line that says where an answer was written.
FILE = "answer_file"


def say(mark: str, answer: dict[str, Any], request: dict[str, Any]) -> None:
    """The answer, on its line, or written to the file the request names and the line saying
    where."""
    text = json.dumps(answer)
    where = request.get("answer")
    if isinstance(where, str) and where:
        Path(where).write_text(text, encoding="utf-8")
        print(mark + json.dumps({FILE: where, "bytes": len(text.encode("utf-8"))}))
    else:
        print(mark + text)
