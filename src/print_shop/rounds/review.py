"""The delivered result: `review/`, written by the harness at the stop for approval, from records.

On both real runs the folder a person opened held `frame` beside `frame_base` and `cap`, its two
halves, and imported together one drew over the other in Orca. So a run's `done` names the ways it
prints (`groups`, each with its own files) and each file's colour and label (`looks`), and this
writes one folder a group: its own files, and every file in no group (the panes), each pane named
by its number, its label and its colour. A group's files were held not to take the same space
before the stop, and every file passed `thin`.

The README is from the records, not the model's words: the size, each requirement's reading against
its limit and the files it read, what `thin` read of each file, and what wasn't checked. The model's
closing words are under their own heading, marked as its.
"""

from __future__ import annotations

import re
import shutil
import string
from pathlib import Path
from typing import Any

from print_shop.rounds import paths
from print_shop.rounds.judge import Standing
from print_shop.rounds.needs import Need, Reading

NOT_IN_A_NAME = re.compile(r"[^a-z0-9]+")
TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


def _slug(text: str) -> str:
    return NOT_IN_A_NAME.sub("-", text.casefold()).strip("-")


def shared_names(shared: list[str], looks: dict[str, dict[str, str]]) -> dict[str, str]:
    """Each shared file's name in the folder: its number, padded so they sort, then its label and
    colour, as `pane_01-cup-blue.step`. A file with no look keeps its name."""
    numbers = [m for f in shared if (m := TRAILING_NUMBER.match(Path(f).stem))]
    width = max((len(m.group(2).lstrip("0") or "0") for m in numbers), default=1)
    found: dict[str, str] = {}
    for name in shared:
        path = Path(name)
        stem = path.stem
        matched = TRAILING_NUMBER.match(stem)
        if matched:
            stem = f"{matched.group(1)}{int(matched.group(2)):0{max(width, 2)}d}"
        look = looks.get(name)
        words = [stem] + ([_slug(look["label"])] if look and look.get("label") else [])
        words += [_slug(look["colour"])] if look else []
        found[name] = "-".join(w for w in words if w) + path.suffix.lower()
    return found


def _bounds(overlap: dict[str, Any], files: list[str]) -> str:
    boxes = [(overlap.get("files") or {}).get(f, {}).get("bounds_mm") for f in files]
    boxes = [b for b in boxes if b]
    if not boxes:
        return "not read"
    low = [min(b[0][k] for b in boxes) for k in range(3)]
    high = [max(b[1][k] for b in boxes) for k in range(3)]
    size = " x ".join(f"{high[k] - low[k]:g}" for k in range(3))
    return f"{size} mm (x, y, z), from {low} to {high}, by OCCT's box of each file"


def write(
    out: Path,
    *,
    run: str,
    round_n: int,
    approval: int,
    judged: Path,
    part: list[str],
    groups: list[dict[str, Any]],
    looks: dict[str, dict[str, str]],
    wanted: list[Need],
    readings: list[Reading],
    standing: list[Standing],
    overlap: dict[str, Any],
    swaps: list[dict[str, Any]],
    closing: str,
) -> dict[str, Any]:
    """The folder, and what it holds by group and file, for the ledger."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    owned = {f for g in groups for f in g["files"]}
    shared = [f for f in part if f not in owned]
    renamed = shared_names(shared, looks)
    held: dict[str, Any] = {"groups": [], "shared": renamed}
    letters = iter(string.ascii_uppercase)
    lines = [
        f"# {run}: the files to print (round {round_n})",
        "",
        f"Written by the harness at the stop for approval {approval}, from its records. It isn't "
        "approved until a person says so. Every file is as the clean run left it, in "
        f"`rounds/{round_n:03}/judged/`, which the checks below read.",
        "",
    ]
    if len(groups) > 1:
        lines += [
            "**Open one folder only.** Each is the whole part, printed one way, and the folders' "
            "own files take the same space as each other's.",
            "",
        ]
    lines += [f"**Size:** {_bounds(overlap, part)}.", ""]
    for group in groups:
        letter = next(letters, "Z")
        folder = f"{letter}-{_slug(group['name']) or 'part'}"
        (out / folder).mkdir()
        files: dict[str, str] = {}
        for name in group["files"]:
            target = Path(name).name
            (out / folder / target).write_bytes(paths.read_bytes_inside(judged, name))
            files[target] = paths.content_hash(name, (out / folder / target).read_bytes())
        for name, target in renamed.items():
            (out / folder / target).write_bytes(paths.read_bytes_inside(judged, name))
            files[target] = paths.content_hash(name, (out / folder / target).read_bytes())
        held["groups"].append({"folder": folder, "name": group["name"], "files": files})
        lines += [f"## {folder}", ""]
        if group.get("about"):
            lines += [f"{group['about']} (the model's words)", ""]
        for name in group["files"]:
            look = looks.get(name)
            said = f": {look['label']}, {look['colour']}" if look else ""
            lines.append(f"- `{Path(name).name}`{said}")
        if shared:
            lines.append(f"- and the {len(shared)} files below")
        lines.append("")
    clashing = [
        p for p in overlap.get("pairs") or [] if not ({p["files"][0], p["files"][1]} <= set(shared))
    ]
    if clashing:
        lines += ["These files take the same space, so they're never loaded together:", ""]
        for pair in clashing:
            occt = f", {pair['occt_mm3']:g} mm³ by OCCT" if pair.get("occt_mm3") is not None else ""
            lines.append(
                f"- `{pair['files'][0]}` and `{pair['files'][1]}`: {pair['mesh_mm3']:g} mm³ by "
                f"the mesh{occt}"
            )
        lines.append("")
    if shared:
        lines += [
            "## In every folder",
            "",
            "Each sits where it goes, so loading it with the frame shows it in place.",
            "",
            "| File | Was | What | Colour |",
            "|---|---|---|---|",
        ]
        for name in sorted(shared, key=lambda f: renamed[f]):
            look = looks.get(name) or {}
            lines.append(
                f"| `{renamed[name]}` | `{name}` | {look.get('label') or '(not given)'} | "
                f"{look.get('colour') or '(not given)'} |"
            )
        lines.append("")
    lines += [
        "## Each requirement, as the harness read it",
        "",
        "| Requirement | Reading | Limit | Against |",
        "|---|---|---|---|",
    ]
    named: set[str] = set()
    for need, reading in zip(wanted, readings, strict=True):
        if need.by == "bench":
            against = ", ".join(f"`{f}`" for f in need.files()) or "(no file named)"
            named |= set(need.files())
            limit = need.limit()
        else:
            against, limit = "the sheet", "a pick, by the Sponsor's eye"
        why = reading.why.replace("\n", " ").replace("|", "/")
        if len(why) > 300:
            why = why[:300] + "..."
        lines.append(f"| **{need.id}**: {need.what} | {why} | {limit} | {against} |")
    lines += [
        "",
        "## Every file, by `thin` at the nozzle's width",
        "",
        "| File | Verdict | Thinnest across (mm) |",
        "|---|---|---|",
    ]
    for one in standing:
        thinnest = one.numbers.get("thinnest_across_mm")
        lines.append(
            f"| `{one.file}` | {one.verdict or 'none'} | "
            f"{thinnest if thinnest is not None else 'nothing under the nozzle'} |"
        )
    lines += ["", "## What wasn't checked", ""]
    unnamed = [f for f in part if f not in named]
    if unnamed:
        lines.append(
            f"- No requirement's check named {', '.join(f'`{f}`' for f in unnamed)}: `thin` is "
            "the only check that read them."
        )
    for need, reading in zip(wanted, readings, strict=True):
        if need.by == "eye":
            lines.append(f"- **{need.id}** passed on a pick, not a bench: {reading.why[:300]}")
    for one in standing:
        if one.unsure:
            lines.append(f"- `thin` had no answer to rely on for `{one.file}`: {one.why}")
    for swap in swaps:
        new = swap.get("new") or {}
        lines.append(
            f"- **{swap['need']}** was changed while its check failed: it was "
            f"`{swap['old'].get('command')}` ({swap['old_reading']}), and is "
            f"{'`' + str(new.get('command')) + '`' if new.get('command') else 'gone'}. The model's "
            f"reason: {swap['reason']}"
        )
    lines += [
        "- Nothing here has been sliced or printed: the refinement pass comes after approval.",
        "",
        "## What the model said at the end",
        "",
        closing,
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return held
