"""What a session reads on demand: CadQuery, engineering values, and the bench.

`skills/cadquery` and `skills/references` are mqucifer/model-performance's `cad_eval/skills` at
98b3c5b, byte for byte, in the Agent Skills layout: the measured text, held to its source by a test
on the Mac and by `SKILLS_SHA` everywhere. Where the runs showed a gap (mqucifer/print-shop#54), a
measured file gains print-shop's additions after `ADDED`, a line of its own, and the text above it
is still the measured text, held as before (`measured`). A file of print-shop's own in those sets,
such as `cadquery/references/layout-to-arcs.md`, keeps every rule. Each `SKILL.md` and
`references/*.md` is a skill of its own, so a topic is read only when it's needed. The mechanism
is crew's in small (`crew_org/session/skills.py`): the start names each in one line, and `skill`
reads one. A kind's required skills are the exception (mqucifer/print-shop#61): the start gives
each whole (`start_skills`), and they stay through every compaction. `skills/bench` is new, and
says what `docs/bench.md` says, for the session.

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
ALL = (*SETS, "designing-stained-glass", "designing-enclosures")


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
    """The one line the start adds, as crew's start names its skills: a run from before
    required skills (mqucifer/print-shop#61)."""
    named = "; ".join(f"`{name}`, {about}" for name, (about, _) in registry(sets).items())
    return f"Skills for this work: {named}. Read one with `skill` when you need it."


#: How a required skill is set in the start, so its text reads as the skill's and nothing else's,
#: and a reply can point to it.
GIVEN = '<skill name="{name}">\n{body}\n</skill>'


def start_skills(sets: tuple[str, ...], required: tuple[str, ...]) -> str:
    """What the start says of a run's skills: each required one whole, in the order given, and
    the rest named in one line, to read with `skill` (mqucifer/print-shop#61). On `suncatcher-3`
    the start named all 15 in one sentence, and `cadquery` and `using-the-bench` were never read
    in 325 calls. A user message is never compacted (`session.compact`), so what's here stays."""
    found = registry(sets)
    missing = [name for name in required if name not in found]
    if missing:
        raise KeyError(f"required skills not in the sets {sets}: {missing}")
    if not required:
        return start_line(sets)
    names = ", ".join(f"`{name}`" for name in required)
    whole = "\n\n".join(GIVEN.format(name=n, body=found[n][1].strip()) for n in required)
    others = "; ".join(
        f"`{name}`, {about.rstrip('.')}"
        for name, (about, _) in found.items()
        if name not in required
    )
    rest = (
        f"\n\nThe other skills, to read with `skill` when you need one: {others}." if others else ""
    )
    return (
        f"The skills this work needs throughout are here whole: {names}. They stay in this "
        f"message for the whole run, through every compaction, so there's no need to read them "
        f"with `skill`.\n\n{whole}{rest}"
    )


def given(sets: tuple[str, ...], required: tuple[str, ...]) -> list[tuple[str, str]]:
    """Each required skill and its file under `skills/`, as the start gives them."""
    found = located(sets)
    return [(name, found[name][2]) for name in required]


#: The line after which a measured file holds print-shop's additions, and before which it's the
#: measured text, byte for byte.
ADDED = "<!-- print-shop's additions, after the measured text (mqucifer/print-shop#54) -->"
#: The measured copies' files, as `cad_eval/skills` at 98b3c5b has them: any other file in those
#: sets is print-shop's own.
MEASURED_FILES = (
    "cadquery/SKILL.md",
    "cadquery/references/assemblies-fits.md",
    "cadquery/references/errors.md",
    "cadquery/references/selectors.md",
    "cadquery/references/sketch-extrude.md",
    "cadquery/references/threads-helices.md",
    "references/SKILL.md",
    "references/references/fasteners.md",
    "references/references/fdm.md",
    "references/references/fits.md",
)


def measured(path: Path) -> bytes:
    """A measured file's measured text: all of it, or what's before print-shop's additions."""
    data = path.read_bytes()
    cut = data.find(b"\n\n" + ADDED.encode())
    return data if cut < 0 else data[: cut + 1]


def measured_sha() -> str:
    """One hash over the measured text of each measured file, as `sha` hashes a set."""
    digest = hashlib.sha256()
    for name in MEASURED_FILES:
        digest.update(name.encode())
        digest.update(measured(SKILLS / name))
    return digest.hexdigest()


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
