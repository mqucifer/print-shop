"""What a session reads on demand: CadQuery, engineering values, and the bench.

`skills/cadquery` and `skills/references` are mqucifer/model-performance's `cad_eval/skills` at
98b3c5b, byte for byte, in the Agent Skills layout: the measured text, held to its source by a test
on the Mac and by `SKILLS_SHA` everywhere. Each `SKILL.md` and `references/*.md` is a skill of its
own, so a topic is read only when it's needed. The mechanism is crew's in small
(`crew_org/session/skills.py`): the start names each in one line, and `skill` reads one.
`skills/bench` is new, and says what `docs/bench.md` says, for the session.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SKILLS = Path(__file__).parent / "skills"
SETS = ("bench", "cadquery", "references")


def files() -> list[Path]:
    found: list[Path] = []
    for name in SETS:
        root = SKILLS / name
        found += [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]
    return found


def front(path: Path) -> tuple[dict[str, str], str]:
    """A skill file's front matter, as `key: value` lines, and the text after it."""
    text = path.read_text(encoding="utf-8")
    _, head, body = text.split("---\n", 2)
    fields = dict(line.split(": ", 1) for line in head.strip().splitlines())
    return fields, body.lstrip()


def registry() -> dict[str, tuple[str, str]]:
    """Each skill's (description, body), by its name."""
    found: dict[str, tuple[str, str]] = {}
    for path in files():
        fields, body = front(path)
        found[fields["name"]] = (fields["description"], body)
    return found


def start_line() -> str:
    """The one line the start adds, as crew's start names its skills."""
    named = "; ".join(f"`{name}`, {about}" for name, (about, _) in registry().items())
    return f"Skills for this work: {named}. Read one with `skill` when you need it."


def sha(names: tuple[str, ...] = SETS) -> str:
    """One hash over each set's files, in order: what a run's text was."""
    digest = hashlib.sha256()
    for path in files():
        if path.relative_to(SKILLS).parts[0] in names:
            digest.update(str(path.relative_to(SKILLS)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()
