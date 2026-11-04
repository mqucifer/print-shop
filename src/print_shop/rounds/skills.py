"""What a session reads on demand: CadQuery, engineering values, and the bench.

`skills/cadquery` and `skills/references` are mqucifer/model-performance's `cad_eval/skills` at
98b3c5b, byte for byte, in the Agent Skills layout: the measured text, held to its source by a test
on the Mac and by `SKILLS_SHA` everywhere. Each `SKILL.md` and `references/*.md` is a skill of its
own, so a topic is read only when it's needed. The mechanism is crew's in small
(`crew_org/session/skills.py`): the start names each in one line, and `skill` reads one.
`skills/bench` is new, and says what `docs/bench.md` says, for the session.

A run reads the skills of its toolset (`toolsets.py`): the core's three sets, and a kind's own,
such as `skills/designing-stained-glass`. Each read is in the ledger's `skill_reads`, with the
call it was made at and the file it read, so an analysis can see whether a run found the right one.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

SKILLS = Path(__file__).parent / "skills"
#: The core's sets, as every run had before toolsets.
SETS = ("bench", "cadquery", "references")
#: Every set there is, a kind's among them.
ALL = (*SETS, "designing-stained-glass")


def files(sets: tuple[str, ...] = SETS) -> list[Path]:
    found: list[Path] = []
    for name in sets:
        root = SKILLS / name
        found += [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]
    return found


def front(path: Path) -> tuple[dict[str, str], str]:
    """A skill file's front matter, as `key: value` lines, and the text after it."""
    text = path.read_text(encoding="utf-8")
    _, head, body = text.split("---\n", 2)
    fields = dict(line.split(": ", 1) for line in head.strip().splitlines())
    return fields, body.lstrip()


def registry(sets: tuple[str, ...] = SETS) -> dict[str, tuple[str, str]]:
    """Each skill's (description, body), by its name."""
    return {name: (about, body) for name, (about, body, _) in located(sets).items()}


def located(sets: tuple[str, ...] = SETS) -> dict[str, tuple[str, str, str]]:
    """Each skill's (description, body, file), the file as a path under `skills/`."""
    found: dict[str, tuple[str, str, str]] = {}
    for path in files(sets):
        fields, body = front(path)
        found[fields["name"]] = (fields["description"], body, str(path.relative_to(SKILLS)))
    return found


def start_line(sets: tuple[str, ...] = SETS) -> str:
    """The one line the start adds, as crew's start names its skills."""
    named = "; ".join(f"`{name}`, {about}" for name, (about, _) in registry(sets).items())
    return f"Skills for this work: {named}. Read one with `skill` when you need it."


def sha(names: tuple[str, ...] = SETS) -> str:
    """One hash over each set's files, in order: what a run's text was."""
    digest = hashlib.sha256()
    for path in files(names):
        digest.update(str(path.relative_to(SKILLS)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


#: Anthropic's skill-authoring rules (platform.claude.com/docs/en/agents-and-tools/agent-skills/
#: best-practices): a name's length and letters, a description's length, and a body's lines.
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_CHARS = 64
DESCRIPTION_CHARS = 1024
BODY_LINES = 500
#: A reference longer than this opens with a list of its contents.
CONTENTS_AFTER = 100
RESERVED = ("anthropic", "claude")
#: Words that put a description in the first or second person, not the third.
NOT_THIRD = re.compile(r"\b(?:I|I'm|me|my|we|our|you|your)\b")


def broken(path: Path) -> list[str]:
    """Each rule a skill file breaks, said plainly: none for a file that keeps them all."""
    fields, body = front(path)
    name = fields.get("name", "")
    about = fields.get("description", "")
    wrong = []
    if not NAME.fullmatch(name) or len(name) > NAME_CHARS:
        wrong.append(f"name {name!r} isn't lowercase letters, digits and hyphens, at most 64")
    if any(word in name.casefold() for word in RESERVED):
        wrong.append(f"name {name!r} holds a reserved word")
    if not name.split("-")[0].endswith("ing"):
        wrong.append(f"name {name!r} isn't in gerund form")
    if not about or len(about) > DESCRIPTION_CHARS:
        wrong.append(f"description is {len(about)} characters: 1 to {DESCRIPTION_CHARS}")
    if "<" in about and ">" in about:
        wrong.append("description holds an XML tag")
    if NOT_THIRD.search(about):
        wrong.append("description isn't in the third person")
    if "Use when" not in about:
        wrong.append("description doesn't say when to use it")
    lines = body.count("\n") + 1
    if path.name == "SKILL.md" and lines >= BODY_LINES:
        wrong.append(f"SKILL.md's body is {lines} lines: under {BODY_LINES}")
    if path.name != "SKILL.md" and lines > CONTENTS_AFTER and "## Contents" not in body[:2000]:
        wrong.append(f"a reference of {lines} lines doesn't open with its contents")
    if path.name == "SKILL.md":
        for linked in re.findall(r"`(references/[^`]+\.md)`", body):
            if not (path.parent / linked).is_file():
                wrong.append(f"SKILL.md names {linked}, which isn't there")
        for nested in (path.parent / "references").glob("*/**/*.md"):
            wrong.append(f"{nested.relative_to(path.parent)} is more than one level deep")
    return wrong
