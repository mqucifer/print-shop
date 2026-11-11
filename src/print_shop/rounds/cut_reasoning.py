"""What a call cut at the answer's limit had settled, kept where the model can read it
(mqucifer/print-shop#99).

`eval-pocket-4` and `eval-fan-5` each settled a design partway through a call, then wrote their
CadQuery script inside the reasoning, and were cut off in the middle of it. The reasoning isn't
sent back (`Session._withhold_reasoning`), so the next call started cold. Now the harness writes
the reasoning's last drafted script and its last stated design to
`record/cut-reasoning-<call>.md`. Nothing here runs anything: it's text the model can `read`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Where it's kept, inside the run's working directory, as the model names it.
KEPT = "record/cut-reasoning-{n}.md"
#: A fenced python block's opening line.
OPENS = re.compile(r"^[ \t]*```[ \t]*(?:python3?|py)[ \t]*$", re.MULTILINE | re.IGNORECASE)
#: Any fence: a python block ends at the next one, or at the end where it was cut off.
FENCE = re.compile(r"^[ \t]*```", re.MULTILINE)
#: Words that open a stated design, where the reasoning holds no script.
DESIGN = re.compile(
    r"final design|design spec|final plan|(?:my|the) plan\b|\bplan:|here's the design"
    r"|here is the design|the design is",
    re.IGNORECASE,
)
#: A line of a list.
LISTED = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
#: The most kept of a design passage: about 1,500 tokens.
DESIGN_CHARS = 5000


@dataclass(frozen=True)
class Kept:
    """What a cut reasoning had settled: its last script, if it drafted one, and its design."""

    script: str | None
    design: str
    #: Whether the script's block was still open where the call was cut.
    unfinished: bool = False


def settled(reasoning: str) -> Kept:
    """The last fenced python block, even an unfinished one, and the design before it. With no
    block, the last passage that states a design or a plan, or else the reasoning's last
    paragraph."""
    opened = list(OPENS.finditer(reasoning))
    if opened:
        start = opened[-1].end() + 1
        closing = FENCE.search(reasoning, start)
        end = closing.start() if closing else len(reasoning)
        script = reasoning[start:end].rstrip()
        design = _before(reasoning[: opened[-1].start()])
        if script.strip():
            return Kept(script + "\n", design, closing is None)
        if closing is None:
            # Cut as it opened the block: nothing drafted yet, but the design before it stands.
            return Kept(None, design)
    found = list(DESIGN.finditer(reasoning))
    if found:
        return Kept(None, _from(reasoning, found[-1].start()))
    return Kept(None, _before(reasoning))


def _chunks(text: str) -> list[str]:
    return [c.strip("\n") for c in re.split(r"\n[ \t]*\n", text) if c.strip()]


def _is_list(chunk: str) -> bool:
    lines = [line for line in chunk.splitlines() if line.strip()]
    return bool(lines) and all(LISTED.match(line) or line.startswith((" ", "\t")) for line in lines)


def _before(text: str) -> str:
    """The paragraph or list just before where `text` ends. A last paragraph that only leads in
    (`Now the script:`) takes in the one before it, a list takes in the paragraph that leads
    into it, and a paragraph ending `:` is taken with what it leads into."""
    chunks = _chunks(text)
    if not chunks:
        return ""
    taken = [chunks.pop()]
    leads_in = _leads_in(taken[0])
    while chunks:
        before = chunks[-1]
        if not (leads_in or _is_list(taken[0]) or _leads_in(before)):
            break
        if sum(map(len, taken)) + len(before) > DESIGN_CHARS:
            break
        taken.insert(0, chunks.pop())
        leads_in = False
    return _capped("\n\n".join(taken))


def _leads_in(chunk: str) -> bool:
    return chunk.rstrip().endswith(":")


def _from(text: str, at: int) -> str:
    """The passage that starts at the paragraph holding `at`: it, and what follows while it's a
    list or the paragraph before it leads into it."""
    start = text.rfind("\n\n", 0, at)
    chunks = _chunks(text[start + 2 if start >= 0 else 0 :])
    taken = chunks[:1]
    for chunk in chunks[1:]:
        if not (taken[-1].rstrip().endswith(":") or _is_list(chunk)):
            break
        if sum(map(len, taken)) + len(chunk) > DESIGN_CHARS:
            break
        taken.append(chunk)
    return _capped("\n\n".join(taken))


def _capped(text: str) -> str:
    if len(text) <= DESIGN_CHARS:
        return text
    return "(its start left out)\n" + text[-DESIGN_CHARS:]


def write(work: Path, n: int, reasoning: str) -> tuple[str, Kept]:
    """Writes what call `n`'s cut reasoning settled, and gives its path as the model names it."""
    kept = settled(reasoning)
    path = KEPT.format(n=n)
    lines = [
        f"# Call {n}, cut off at the answer's limit",
        "",
        "Kept by the harness from the call's reasoning, which isn't sent back. Nothing here was "
        "run.",
        "",
        "## The last design it stated",
        "",
        kept.design or "(none stated)",
        "",
    ]
    if kept.script is not None:
        state = "cut off in the middle" if kept.unfinished else "as it was drafted"
        lines += [
            f"## The last script it drafted, {state}",
            "",
            "```python",
            kept.script.rstrip("\n"),
            "```",
            "",
        ]
    target = work / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return path, kept
