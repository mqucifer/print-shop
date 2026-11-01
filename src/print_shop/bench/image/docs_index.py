"""Build the `docs` tool's index from CadQuery's own documentation, at the image's tag.

mqucifer/model-performance#40, A2. The model can search CadQuery's 2.8.0 docs and
examples, not anyone's memory of them, and not the master branch's (Read the Docs has
no 2.8.0 build). The source is CadQuery's repository at the `v2.8.0` tag, fetched by
commit when the image is built, under its Apache-2.0 licence (checked here, and the
build refused without it). Standard library only: it runs at the image's build, and
in tests on the host.

    python docs_index.py <source> <commit> <tag> <out.json> <repository>

- **What's read:** `doc/*.rst` (the reStructuredText the docs are built from) and
  `examples/*.py`. Not `conf.py`, the docs' images, or the notebook.
- **A chunk:** one section of a page, by its heading path ("Sketch > Modes"), as plain
  text with its code fenced; cut where it runs past `CHUNK_CHARS`, never inside code.
  Each example file is one chunk.
- **Its code blocks:** `.. cadquery::`, `.. code-block:: python` and `::` literal
  blocks, each kept apart. A block that calls `show_object` is flagged: it's CQ-editor's
  viewer, and the sandbox has none. `runs` is whether the block ran in the image, set
  at the build by `run_blocks` (none until then), so a block that fails is marked,
  not dropped.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

CHUNK_CHARS = 2400
UNDERLINE = re.compile(r"^([=\-~*^#\"'+`:.])\1{2,}\s*$")
CODE_DIRECTIVE = re.compile(r"^\.\. (cadquery|code-block|code|sourcecode)::\s*(\w*)\s*$")
DIRECTIVE = re.compile(r"^\.\. ([\w:-]+)::\s*(.*)$")
RUN_TIMEOUT = 120


class NotLicensed(ValueError):
    """The source's licence isn't the Apache-2.0 the index relies on."""


def plain(text: str) -> str:
    """reStructuredText's inline markup as plain text."""
    text = re.sub(r":[\w:]+:`~?([^`<]+?)(?:\s*<[^>]+>)?`", r"\1", text)  # :py:meth:`X`
    text = re.sub(r"`([^`<]+?)\s*<([^>]+)>`_+", r"\1 (\2)", text)  # `text <url>`_
    text = re.sub(r"``([^`]+)``", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*", r"\1", text)
    return text


def _headings(lines: list[str]) -> dict[int, tuple[int, str, int]]:
    """Each heading's title line: (its level, its title, the lines it takes)."""
    styles: list[tuple[str, bool]] = []
    found: dict[int, tuple[int, str, int]] = {}
    i = 0
    while i < len(lines) - 1:
        title, under = lines[i], lines[i + 1]
        over = i > 0 and UNDERLINE.match(lines[i - 1]) is not None
        if (
            title.strip()
            and not title.startswith((" ", ".."))
            and UNDERLINE.match(under)
            and len(under.strip()) >= len(title.strip())
        ):
            style = (under.strip()[0], over)
            if style not in styles:
                styles.append(style)
            start = i - 1 if over else i
            found[start] = (styles.index(style), title.strip(), (i + 2) - start)
            i += 2
            continue
        i += 1
    return found


def _block(lines: list[str], i: int) -> tuple[str, int]:
    """The indented block from line `i` (after its directive or `::`), dedented, with a
    directive's option lines dropped; and the line after it."""
    body: list[str] = []
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
        body.append(lines[i])
        i += 1
    text = textwrap.dedent("\n".join(body))
    kept = text.split("\n")
    while kept and (not kept[0].strip() or re.match(r"^:[\w-]+:", kept[0])):
        kept.pop(0)
    return "\n".join(kept).strip("\n").rstrip(), i


def _section(lines: list[str]) -> tuple[str, list[dict[str, Any]]]:
    """A section's lines as plain text with its code fenced, and its code blocks."""
    out: list[str] = []
    blocks: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        code = CODE_DIRECTIVE.match(line)
        if code and (code.group(1) == "cadquery" or code.group(2) in ("", "python", "py")):
            found, i = _block(lines, i + 1)
            if found:
                blocks.append({"code": found, "show_object": "show_object" in found, "runs": None})
                out += ["", "```python", found, "```", ""]
            continue
        directive = DIRECTIVE.match(line)
        if directive:
            name, rest = directive.groups()
            body, i = _block(lines, i + 1)
            if name in ("note", "warning", "tip", "important"):
                out.append(f"{name.capitalize()}: {plain(rest)} {plain(body)}".strip())
            elif name.startswith("auto"):
                named = [rest, *(n.strip() for n in body.splitlines() if n.strip())]
                out.append(f"(API reference: {', '.join(n for n in named if n)})")
            elif name not in ("contents", "currentmodule", "image", "raw", "toctree"):
                out.append(plain(body)) if body else None
            continue
        if line.startswith(".. _") or line.startswith(".. |"):
            i += 1
            continue
        if line.rstrip().endswith("::") and not line.startswith(".."):
            out.append(plain(line.rstrip()[:-1]))
            found, i = _block(lines, i + 1)
            if found:
                blocks.append({"code": found, "show_object": "show_object" in found, "runs": None})
                out += ["", "```python", found, "```", ""]
            continue
        out.append(plain(line))
        i += 1
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text, blocks


def _units(text: str) -> list[str]:
    """`text`'s paragraphs, each fenced code block whole, and a paragraph longer than
    `CHUNK_CHARS` cut at its sentences."""
    units: list[str] = []
    fence: list[str] | None = None
    for para in text.split("\n\n"):
        if fence is not None:
            fence.append(para)
            if para.rstrip().endswith("```"):
                units.append("\n\n".join(fence))
                fence = None
            continue
        if para.lstrip().startswith("```") and not para.rstrip().endswith("```", 3):
            fence = [para]
            continue
        if len(para) <= CHUNK_CHARS or para.lstrip().startswith("```"):
            units.append(para)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            if current and len(current) + len(sentence) > CHUNK_CHARS:
                units.append(current)
                current = ""
            current = f"{current} {sentence}" if current else sentence
        units.append(current)
    if fence is not None:
        units.append("\n\n".join(fence))
    return units


def _cut(text: str) -> list[str]:
    """`text` in pieces of at most about `CHUNK_CHARS`, at paragraph ends, code whole."""
    pieces: list[str] = []
    current = ""
    for unit in _units(text):
        if current and len(current) + len(unit) > CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{unit}" if current else unit
    if current:
        pieces.append(current)
    return pieces


def _rst(path: Path, rel: str) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    heads = _headings(lines)
    starts = sorted(heads)
    found = []
    stack: list[tuple[int, str]] = []
    bounds = [*starts, len(lines)]
    if not starts or starts[0] > 0:
        starts = [0, *starts]
        bounds = [*starts, len(lines)]
    for k, start in enumerate(starts):
        end = bounds[k + 1]
        if start in heads:
            level, title, size = heads[start]
            stack = [s for s in stack if s[0] < level] + [(level, title)]
            body = lines[start + size : end]
        else:
            body = lines[start:end]
        text, blocks = _section(body)
        if not text:
            continue
        title_path = " > ".join(t for _, t in stack) or rel
        pieces = _cut(text)
        for piece in pieces:
            own = [b for b in blocks if b["code"] in piece]
            found.append(
                {
                    "source": rel,
                    "title": title_path,
                    "text": piece,
                    "blocks": own,
                    "show_object": any(b["show_object"] for b in own),
                }
            )
    return found


def _example(path: Path, rel: str) -> dict[str, Any]:
    code = path.read_text(encoding="utf-8").strip()
    name = re.sub(r"^Ex\d+_", "", path.stem).replace("_", " ")
    block = {"code": code, "show_object": "show_object" in code, "runs": None}
    return {
        "source": rel,
        "title": f"Example: {name}",
        "text": f"```python\n{code}\n```",
        "blocks": [block],
        "show_object": block["show_object"],
    }


def chunks(root: Path) -> list[dict[str, Any]]:
    """Every chunk of the docs' text and the examples, in file order."""
    found: list[dict[str, Any]] = []
    for path in sorted((root / "doc").glob("*.rst")):
        found += _rst(path, f"doc/{path.name}")
    for path in sorted((root / "examples").glob("*.py")) if (root / "examples").is_dir() else []:
        found.append(_example(path, f"examples/{path.name}"))
    return found


def licence(root: Path) -> str:
    """The source's licence, as the index records it: Apache-2.0, or refused."""
    text = (root / "LICENSE").read_text(encoding="utf-8") if (root / "LICENSE").is_file() else ""
    if "Apache License" in text and ("Version 2.0" in text or "v 2.0" in text):
        return "Apache-2.0"
    raise NotLicensed(f"{root / 'LICENSE'} isn't the Apache License 2.0")


def build(root: Path, commit: str, tag: str, repository: str) -> dict[str, Any]:
    """The index: where it came from, and its chunks."""
    source = {"repository": repository, "tag": tag, "commit": commit, "licence": licence(root)}
    return {"source": source, "chunks": chunks(root)}


def run_blocks(index: dict[str, Any], python: str = sys.executable) -> None:
    """Run each code block, as written, in a fresh interpreter with CadQuery imported
    both ways the docs use it; mark `runs`. Only at the image's build, in the image."""
    prelude = "import cadquery\nimport cadquery as cq\n"
    for chunk in index["chunks"]:
        for block in chunk["blocks"]:
            try:
                done = subprocess.run(
                    [python, "-I", "-c", prelude + block["code"]],
                    capture_output=True,
                    timeout=RUN_TIMEOUT,
                    cwd="/tmp",
                )
                block["runs"] = done.returncode == 0
            except subprocess.TimeoutExpired:
                block["runs"] = False


def main() -> None:
    root, commit, tag, out, repository = sys.argv[1:6]
    index = build(Path(root), commit, tag, repository)
    run_blocks(index)
    Path(out).write_text(json.dumps(index), encoding="utf-8")
    blocks = [b for c in index["chunks"] for b in c["blocks"]]
    print(
        f"docs index: {len(index['chunks'])} chunks, {len(blocks)} code blocks, "
        f"{sum(b['runs'] for b in blocks)} run, {sum(b['show_object'] for b in blocks)} "
        "call show_object"
    )


if __name__ == "__main__":
    main()
