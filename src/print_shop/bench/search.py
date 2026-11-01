"""`lookup` and `docs` over the image's indexes, inside the container only.

Ported unchanged from mqucifer/model-performance `cad_eval/search.py` at 22ea8b7 (its #40, A2),
for the rounds (mqucifer/print-shop#17). The host starts it by `host.helper`. Both read what the
image was built with (`image/lookup_index.py`, `image/docs_index.py`), never anyone's memory:

- **`lookup`:** names in the installed CadQuery and OCP that hold the query. A member
  matches by any class it's on, so `Solid.fuse` finds `Shape.fuse` (defined there, on
  Solid too). Each function is listed once with every class it's on. Exact names come
  first, `PAGE` to a page. With nothing found, the nearest names. An OCP class named
  exactly also lists its members, read live, since the index holds only the classes.
- **`docs`:** CadQuery's own 2.8.0 documentation and examples, ranked by the query's
  words. Each section's code that calls CQ-editor's `show_object`, or that didn't run
  on its own at the build, is said to be so.

It prints one line, `MARK` then JSON. Standard library only, and OCP for a class's
members.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import sys
from typing import Any

MARK = "ROUNDS_SEARCH "
LOOKUP_INDEX = "/opt/cad-eval/lookup.json"
DOCS_INDEX = "/opt/cad-eval/docs.json"
PAGE = 15
DOCS_PAGE = 3
DOC_CHARS = 3000
SHOW_OBJECT = (
    "Its code calls show_object, CQ-editor's viewer, which the sandbox doesn't have: "
    "export the part instead."
)
DIDNT_RUN = "Some of its code didn't run on its own in this image: it may continue earlier code."


def _shown(entry: dict[str, Any]) -> list[str]:
    """Every name an entry answers to: its own, and its member's on each class it's on."""
    own = entry["name"]
    member = own.rpartition(".")[2]
    return [own, *(f"{c}.{member}" for c in entry.get("on") or [])]


def lookup(entries: list[dict[str, Any]], query: str, page: int = 1) -> dict[str, Any]:
    fragment = query.strip().removeprefix("cadquery.").removeprefix("cq.").casefold()
    found = [e for e in entries if any(fragment in n.casefold() for n in _shown(e))]
    member = fragment.rpartition(".")[2]

    def rank(entry: dict[str, Any]) -> tuple[bool, bool, int]:
        exact = any(n.casefold().endswith(fragment) for n in _shown(entry))
        return (
            not exact,
            entry["name"].rpartition(".")[2].casefold() != member,
            len(entry["name"]),
        )

    found.sort(key=rank)
    pages = max(1, math.ceil(len(found) / PAGE))
    shown = found[(page - 1) * PAGE : page * PAGE]
    result: dict[str, Any] = {
        "query": query,
        "matches": len(found),
        "page": page,
        "pages": pages,
        "found": shown,
    }
    if not found:
        every = sorted({n for e in entries for n in _shown(e)})
        bare = {n.removeprefix("cq."): n for n in every}
        near = difflib.get_close_matches(fragment, [k.casefold() for k in bare], n=8, cutoff=0.6)
        by_fold = {k.casefold(): v for k, v in bare.items()}
        result["near"] = [by_fold[n] for n in near]
    return result


WORD = re.compile(r"[a-z][a-z0-9_]+")


def _words(text: str) -> list[str]:
    return WORD.findall(text.casefold())


def docs(chunks: list[dict[str, Any]], query: str, page: int = 1) -> dict[str, Any]:
    """The chunks ranked by the query's words (tf-idf, the title counted twice)."""
    terms = set(_words(query))
    bags = []
    for chunk in chunks:
        code = " ".join(b["code"] for b in chunk.get("blocks") or [])
        bags.append(_words(f"{chunk['title']} {chunk['title']} {chunk['text']} {code}"))
    n = len(chunks)
    df = {t: sum(1 for bag in bags if t in bag) for t in terms}
    scored = []
    for chunk, bag in zip(chunks, bags, strict=True):
        score = sum(
            bag.count(t) / (1 + len(bag) / 400) * math.log(1 + n / (df[t] or 1))
            for t in terms
            if t in bag
        )
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda s: -s[0])
    pages = max(1, math.ceil(len(scored) / DOCS_PAGE))
    shown = []
    for _, chunk in scored[(page - 1) * DOCS_PAGE : page * DOCS_PAGE]:
        notes = []
        if chunk.get("show_object"):
            notes.append(SHOW_OBJECT)
        if any(b.get("runs") is False for b in chunk.get("blocks") or []):
            notes.append(DIDNT_RUN)
        shown.append(
            {
                "title": chunk["title"],
                "source": chunk["source"],
                "text": chunk["text"][:DOC_CHARS],
                "note": " ".join(notes),
            }
        )
    return {"query": query, "matches": len(scored), "page": page, "pages": pages, "found": shown}


def members(name: str) -> list[str]:
    """An OCP class's public members, each with its first overload, read live."""
    import importlib  # noqa: PLC0415

    module, _, cls = name.rpartition(".")
    obj = getattr(importlib.import_module(module), cls)
    found = []
    for attr in sorted(a for a in dir(obj) if not a.startswith("_")):
        doc = (getattr(getattr(obj, attr), "__doc__", None) or "").strip().splitlines()
        found.append(doc[0][:160] if doc and "(" in doc[0] else attr)
    return found


def main(argv: list[str] | None = None) -> None:
    request = json.loads((sys.argv[1:] if argv is None else argv)[0])
    query, page = str(request.get("query", "")), int(request.get("page") or 1)
    if request.get("tool") == "docs":
        with open(DOCS_INDEX, encoding="utf-8") as fh:
            found = docs(json.load(fh)["chunks"], query, page)
    else:
        with open(LOOKUP_INDEX, encoding="utf-8") as fh:
            entries = json.load(fh)["entries"]
        found = lookup(entries, query, page)
        exact = [e for e in entries if e["name"] == query.strip() and e["kind"] == "class"]
        if exact and query.strip().startswith("OCP."):
            found["members"] = members(query.strip())
    print(MARK + json.dumps(found))
