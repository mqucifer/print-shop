"""Where a name the model used is, read from the lookup index, inside the container only.

Ported unchanged from mqucifer/model-performance `cad_eval/hint.py` at 22ea8b7 (its #40, A1), for
the rounds (mqucifer/print-shop#17). The host starts it by `host.helper`. The commonest v1 error
was a real name on the wrong class: 82 of 223 non-existent names, such as `Workplane.isValid` or
`Solid.union`. Python's own "Did you mean" looks only at the object at hand. So when
a run raises an AttributeError, `run`'s reply adds which CadQuery classes do have the
name, or the names nearest it when none does. It's read from the image's index
(`image/lookup_index.py`): what's installed, not a list anyone wrote. It prints one
line, `MARK` then JSON. Standard library only.
"""

from __future__ import annotations

import difflib
import json
import sys
from typing import Any

MARK = "ROUNDS_HINT "
INDEX = "/opt/cad-eval/lookup.json"
SHOWN = 8


def find(entries: list[dict[str, Any]], owner: str, name: str) -> dict[str, Any]:
    """The CadQuery classes or modules that have `name`, the defining one first; or,
    with none, the CadQuery names nearest it."""
    on: list[str] = []
    for entry in entries:
        where, _, own = entry["name"].rpartition(".")
        if own != name or not entry["name"].startswith("cq."):
            continue
        for found in [where, *sorted(entry.get("on") or [])]:
            if found not in on:
                on.append(found)
    if on:
        return {"owner": owner, "name": name, "on": on}
    # Nearest first among the names the class itself has, then among all of CadQuery's.
    own = sorted(
        {
            e["name"].rpartition(".")[2]
            for e in entries
            if f"cq.{owner}" in (e.get("on") or []) or e["name"].startswith(f"cq.{owner}.")
        }
    )
    every = sorted({e["name"].rpartition(".")[2] for e in entries if e["name"].startswith("cq.")})
    near = difflib.get_close_matches(name, own, n=4, cutoff=0.4)
    near += [n for n in difflib.get_close_matches(name, every, n=6, cutoff=0.6) if n not in near]
    return {"owner": owner, "name": name, "on": [], "near": near[:6]}


def said(found: dict[str, Any]) -> str:
    """The hint as `run`'s reply adds it."""
    owner, name = found["owner"], found["name"]
    if found["on"]:
        shown = ", ".join(found["on"][:SHOWN])
        more = f" and {len(found['on']) - SHOWN} more" if len(found["on"]) > SHOWN else ""
        return f"`{name}` isn't on {owner}. It's on: {shown}{more}."
    near = ", ".join(f"`{n}`" for n in found.get("near") or [])
    return f"No CadQuery class has `{name}`. Names near it: {near or 'none'}."


def main(argv: list[str] | None = None) -> None:
    owner, name = sys.argv[1:] if argv is None else argv
    try:
        with open(INDEX, encoding="utf-8") as fh:
            entries = json.load(fh)["entries"]
    except OSError:
        print(MARK + json.dumps({"error": "no index"}))
        return
    print(MARK + json.dumps(find(entries, owner, name)))
