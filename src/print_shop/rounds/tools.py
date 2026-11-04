"""The session's tools, and how each call is judged: well formed, then done as asked.

Ported from mqucifer/model-performance `cad_eval/tools.py` at 22ea8b7, whose base arm proved them.
`write`, `edit` (crew's `replace_text`, behaviour and all), `read`, `run` with its AttributeError
hint, `lookup`, `docs` and `skill` are as they were there, and the outcomes are BFCL's: `malformed`
and `ill_typed` for the call's form, `failed` for one that couldn't be done, `ok` otherwise. A
script that runs and raises is `ok`: the tool did what it was asked.

New: `bench`, which is any bench command, with its plain text going back and `render`'s pictures
reaching the model; `read` on a picture shows it, since a designer looks at their references; and
`needs`, `questions`, `ask` and `done`, which belong to the run (`run.py` holds them, `Desk` here).

What's not ported, and why:
- `measure`, `inspect` and `view`: `bench` answers those, each with a second check.
- `plan`: the needs say what's wanted and where it came from. A gate before code would be a fence
  on evidence from one-round parts.
- the reaper and the fixed binaries: each command is its own container (`box.py`).
"""

from __future__ import annotations

import ast
import base64
import contextlib
import difflib
import json
import re
import shlex
import time
from pathlib import Path
from typing import Any, Protocol

from print_shop.bench import hint as hint_module
from print_shop.bench import picture as picture_module
from print_shop.bench import search as search_module
from print_shop.rounds import paths, skills, toolsets
from print_shop.rounds.box import Box, Ran
from print_shop.rounds.needs import COMMANDS
from print_shop.rounds.paths import Outside, a_path, inside, read_bytes_inside, read_inside
from print_shop.rounds.session import Judged, WorkFull
from print_shop.rounds.toolsets import Toolset

MAX_CONTENT = 2_000_000
RUN_TAIL = 8000
BENCH_CHARS = 20_000
READ_LINES = 400
# What `edit` shows around a closest block, and how close it must be: crew's.
NEAR_CONTEXT = 5
NEAR_RATIO = 0.6
EDIT_CONTEXT = 4
EDIT_LINES = 80
BOOLEAN_TEXT = {"true": True, "false": False}
PICTURES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")
GEOMETRY = (".step", ".stp", ".stl", ".brep", ".3mf", ".obj", ".f3d")
TYPES: dict[str, Any] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}
# An AttributeError's owner and name, as Python 3.12 says them.
NO_ATTRIBUTE = re.compile(
    r"AttributeError: (?:'(\w+)' object|type object '(\w+)'|module '([\w.]+)') "
    r"has no attribute '(\w+)'"
)
SAVED = re.compile(r"saved: (\S+)\.txt and \.json")


def _spec(
    name: str, description: str, optional: dict[str, Any] | None = None, /, **properties: Any
) -> dict[str, Any]:
    """A tool's spec: its arguments, all required, a bare string meaning a string one, and any
    `optional` ones."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    **{
                        k: {"type": "string", "description": v} if isinstance(v, str) else v
                        for k, v in properties.items()
                    },
                    **(optional or {}),
                },
                "required": list(properties),
            },
        },
    }


RUN_AFTER = {
    "run": {
        "type": "boolean",
        "description": "true to run this file after, as `run` does. Optional.",
    },
    "run_script": {
        "type": "string",
        "description": "Another script to run after, such as `make.py` when this file is one it "
        "imports. Optional.",
    },
}
PAGE = {
    "page": {
        "type": "integer",
        "description": "Which page of the matches, from 1. Optional: the first.",
    }
}
LOOKS = {
    "looks": {
        "type": "object",
        "description": 'Each file\'s colour and a short label: {"pane_1.step": {"colour": "blue", '
        '"label": "cup"}, "frame.step": {"colour": "grey", "label": "frame"}}. A colour is a name '
        "such as blue, amber or clear, perhaps with light or dark, or #rrggbb. The sheet draws "
        "each file in its colour, numbered, with a legend; a file with none takes a colour no "
        "other file has. Optional.",
    }
}
NEED_SHAPE = (
    "Each need: `id`; `what` it must do; `source` (`request`, `answer` or `note` with `quote`, the "
    'words as you were sent them; `reference`, `derived` or `chosen`); and `check`, either {"by": '
    '"bench", "measures": "fit", "command": '
    '"clearance lid.step cup.step --min-gap 0.2", "verdicts": [...] (optional: which verdicts '
    'pass, exit 0 by default), "number": "pairs.0.gap_mesh_mm", "at_least": 0.2, "at_most": 0.6 '
    '(optional: a number in that answer, and its limit)} or {"by": "eye", "look_for": "what the '
    'Sponsor judges"}.'
)


def _measures_said(toolset: Toolset) -> str:
    if toolset.legacy:
        return ""
    named = "; ".join(f"`{m.name}`, {m.about} (`{m.usage}`)" for m in toolset.measures)
    return (
        " A bench need's `measures` says what it's about, and its command has to measure that: "
        f"{named}."
    )


def specs(toolset: Toolset = toolsets.EVERY) -> list[dict[str, Any]]:
    commands = ", ".join(f"`{c}`" for c in toolset.commands)
    return [
        _spec(
            "write",
            "Write a text file in your directory, replacing it if it exists. With `run`, a script "
            "is run after, as `run` does.",
            RUN_AFTER,
            path="The file's path, relative to your directory, such as `make.py`.",
            content="The whole file.",
        ),
        _spec(
            "edit",
            "Replace one or more whole lines that occur exactly once in a file, quoted as the "
            "file has them, with enough lines to be unique. Applied at once, unless it would stop "
            "a Python file parsing; the reply shows the edited lines, numbered. With `run`, a "
            "script is run after, as `run` does.",
            RUN_AFTER,
            path="The file's path, relative to your directory.",
            old="The whole lines to replace, exactly as the file has them.",
            new="The lines to put in their place (empty to delete them).",
        ),
        _spec(
            "read",
            f"Read a text file in your directory, its lines numbered, at most {READ_LINES} at a "
            "time, or see a picture (PNG, JPEG and the like), such as a reference or an earlier "
            "render. For a STEP or STL file, ask the bench.",
            {
                "start": {
                    "type": "integer",
                    "description": "The first line to show, from 1. Optional.",
                },
                "end": {"type": "integer", "description": "The last line to show. Optional."},
            },
            path="The file's path, relative to your directory.",
        ),
        _spec(
            "run",
            "Run a Python script from your directory, in a fresh container with no network. Each "
            "run is its own container: only what it writes in your directory stays. Returns its "
            "exit code and the end of its output.",
            path="The script's path, relative to your directory.",
        ),
        _spec(
            "bench",
            "Ask the bench about your files: any bench command, in a fresh container, with a "
            "second check by a different method behind each answer. The first line of the reply "
            f"is the verdict. {commands} (`render`'s picture comes to you). `<command> --help` "
            "gives its arguments, and the skills say what each answers.",
            command="The command and its arguments, such as `clearance lid.step cup.step "
            "--min-gap 0.2` or `render part.step --views iso top`.",
        ),
        _spec(
            "lookup",
            "Look up names in the installed CadQuery 2.8.0 and OCP 7.9: every name that holds the "
            "query, each with its signature, its docstring's start, and every class it's on "
            "(inherited members included). Nearest names when nothing matches; an OCP class named "
            "exactly lists its members.",
            PAGE,
            query="A name or a fragment of one, such as `Solid.fuse`, `polarArray` or "
            "`OCP.BRepExtrema`.",
        ),
        _spec(
            "docs",
            "Search CadQuery's own 2.8.0 documentation and examples by words, such as `fillet "
            "edges` or `workplane on a face`: the best sections, with their code.",
            PAGE,
            query="What to find, in words.",
        ),
        _spec(
            "skill",
            "Read a skill the start names: how to do one kind of thing, or a table of values.",
            name="The skill's name.",
        ),
        _spec(
            "questions",
            "At intake: put what you need to know to the Sponsor. The run stops here until their "
            "answers come back, in your next message. Ask as a designer would in conversation, "
            "only what you can't find in what you've been given.",
            questions="Your questions, as you'd say them.",
        ),
        _spec(
            "needs",
            "Settle what the part must do, when you know enough: this ends intake, and the first "
            f"round starts. The harness runs each check itself at `done`. A check can't pass on "
            "FAIL. Settled again after they're reopened, a need whose check fails on your files "
            "as they stand can't be dropped, or changed to one that passes, without a reason. "
            f"{NEED_SHAPE}{_measures_said(toolset)}",
            {
                "try": {
                    "type": "boolean",
                    "description": "true to read each bench check against your files as they "
                    "stand, and settle nothing: whether its command, number and limit work. "
                    "Optional.",
                },
                "why_changed": {
                    "type": "object",
                    "description": "When needs are settled again: for each need whose check "
                    "fails on your files and that this list drops or changes to a check that "
                    'passes, why, by its id: {"panes-held": "..."}. A person reads it at the next '
                    "stop, beside both checks. Optional.",
                },
            },
            needs={
                "type": "array",
                "description": "The needs.",
                "items": {"type": "object"},
            },
        ),
        _spec(
            "ask",
            "Where form is at stake, or to show what you have and ask what you need when you're "
            "stuck: name the variants, and `render` draws a sheet of them, which comes to you "
            "first. One variant of the files as they stand will do. Nothing goes to the Sponsor "
            "until `send_ask`, where you say what the sheet shows. The harness also runs the "
            "needs' checks and `thin` on these files, and puts any that fail beside the question. "
            "Everything else is yours to settle by the bench.",
            {
                **LOOKS,
                "cuts": {
                    "type": "string",
                    "description": "Cut-throughs added to each variant's row, by spaces: `z=3` "
                    "level at 3 mm, `x=50` upright through x=50, or `x=50,y=20:40` for that "
                    "stretch of it, as a pane sits in its pocket. Each file is cut in its colour. "
                    "Optional.",
                },
                "need": {
                    "type": "string",
                    "description": "The eye need this answers, if it answers one. Optional.",
                },
                "views": {
                    "type": "string",
                    "description": "The views each variant is drawn in, by spaces: `iso top front` "
                    "unless you say.",
                },
                "close": {
                    "type": "string",
                    "description": "Close-ups added to each variant's row, by spaces, each "
                    "`X,Y,Z,R` or `X,Y,Z,R,VIEW`: R mm all round that point, for what's too fine "
                    "to judge whole.",
                },
                "test_pieces": {
                    "type": "array",
                    "description": "Files to show with each variant that aren't the part. They're "
                    "drawn see-through in grey, and marked. Optional.",
                    "items": {"type": "string"},
                },
            },
            question="What you're asking them to judge.",
            variants={
                "type": "array",
                "description": 'Each variant: {"name": "A", "files": ["a.step"], "about": "what '
                "sets it apart\"}. `files` are what they'll take for the part.",
                "items": {"type": "object"},
            },
        ),
        _spec(
            "send_ask",
            "Send the sheet `ask` drew to the Sponsor, saying what it shows. The run waits for "
            "their pick and reason. Refused if a file on it changed since it was drawn.",
            says="What the sheet shows, picture by picture, as drawn: which files you can see, "
            "in which colours, and anything that isn't what you meant. It reaches the Sponsor "
            "with the question.",
        ),
        _spec(
            "done",
            "When you believe the needs are met. The script is run again from a clean start in a "
            "fresh container, with every STEP and STL outside `references/` taken away first, and "
            "the harness runs each need's check itself. Needs that fail come back to you, and the "
            "next round starts. Every file in `part` is also read by `thin`, at the nozzle's "
            "width, and files printed together mustn't take the same space. When all of that "
            "passes, the run stops for approval, and the harness writes the folder to print "
            "from, one folder for each of `groups`.",
            {
                **LOOKS,
                "groups": {
                    "type": "array",
                    "description": "The ways the part prints, each with its own files: "
                    '[{"name": "two-parts", "files": ["frame_base.step", "cap.step"], "about": '
                    '"how it prints"}, {"name": "one-frame", "files": ["frame.step"], "about": '
                    '"with the pause"}]. Files of `part` in no group print with every group, as '
                    "panes do. Optional: one group of all of `part`.",
                    "items": {"type": "object"},
                },
            },
            script="The script that writes the part.",
            part={
                "type": "array",
                "description": 'The files that are the part, such as `["stand.step"]`: it\'s what '
                "the sheet of the result draws, so a test piece your script writes isn't taken for "
                "the part. Each entry is one row of that sheet. Files seen together are one entry, "
                '{"name": "wide", "files": ["base.step", "lid.step"]}.',
                "items": {},
            },
            fix_shape="What kind of change this round was, in your own words. It's kept, marked as "
            "yours, and decides nothing.",
            closing="Your closing words to the person who'll look at the part, if every need "
            "passes: what it is, anything you couldn't do or check, and what they should look at. "
            "They reach that person with the sheet.",
        ),
        _spec(
            "blocked",
            "End the run without the part, saying what's missing. A person reads it.",
            reason="What's missing.",
        ),
    ]


#: The tools that belong to the run, which `Desk` answers.
DESK = ("questions", "needs", "ask", "send_ask", "done", "blocked", "after_action")


class Desk(Protocol):
    """The tools that belong to the run, not to the working directory."""

    def questions(self, args: dict[str, Any]) -> Judged: ...
    def needs(self, args: dict[str, Any]) -> Judged: ...
    def ask(self, args: dict[str, Any]) -> Judged: ...
    def send_ask(self, args: dict[str, Any]) -> Judged: ...
    def done(self, args: dict[str, Any]) -> Judged: ...
    def blocked(self, args: dict[str, Any]) -> Judged: ...
    def after_action(self, args: dict[str, Any]) -> Judged: ...
    def bench_said(
        self, argv: list[str], ran: Ran, record: dict[str, Any] | None
    ) -> dict[str, Any] | None: ...


def _trim(text: str) -> str:
    """One leading and one trailing newline off, so quoting whole lines is forgiving."""
    text = text[1:] if text.startswith("\n") else text
    return text[:-1] if text.endswith("\n") else text


def _parse_error(source: str) -> tuple[str, int | None] | None:
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return exc.msg, exc.lineno
    return None


def numbered(lines: list[str], lo: int, hi: int) -> list[str]:
    """Lines lo..hi (0-based, end excluded) with their 1-based numbers: crew's."""
    return [f"{n + 1:5}| {lines[n]}" for n in range(max(0, lo), min(len(lines), hi))]


def changed_lines(before: str, after: str, *, side: str = "new") -> str:
    """The lines an edit changed, numbered, with context either side: crew's."""
    old, new = before.splitlines(), after.splitlines()
    spans: list[list[int]] = []
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        span = [
            max(0, i1 - EDIT_CONTEXT),
            min(len(old), max(i2, i1 + 1) + EDIT_CONTEXT),
            max(0, j1 - EDIT_CONTEXT),
            min(len(new), max(j2, j1 + 1) + EDIT_CONTEXT),
        ]
        if spans and span[2] <= spans[-1][3]:
            spans[-1][1], spans[-1][3] = max(spans[-1][1], span[1]), max(spans[-1][3], span[3])
        else:
            spans.append(span)
    lines = new if side == "new" else old
    shown: list[str] = []
    for old_lo, old_hi, new_lo, new_hi in spans:
        lo, hi = (new_lo, new_hi) if side == "new" else (old_lo, old_hi)
        if shown:
            shown.append("  ...")
        shown += numbered(lines, lo, hi)
    if len(shown) > EDIT_LINES:
        shown = [*shown[:EDIT_LINES], f"  ... ({len(shown) - EDIT_LINES} more lines: read them)"]
    return "\n".join(shown)


def refusal(path: str, text: str, old: str, new: str) -> tuple[str, str] | None:
    """Why `old` can't be replaced in `text`, as crew's `replace_text` says it, or None."""
    if not old.strip():
        return "edit.empty", "Quote the lines to replace."
    if old == new:
        return "edit.same", "`old` and `new` are the same: nothing to change."
    lines = text.splitlines()
    want = old.split("\n")
    at = [i for i in range(len(lines) - len(want) + 1) if lines[i : i + len(want)] == want]
    if len(at) > 1:
        where = ", ".join(str(i + 1) for i in at)
        return "edit.twice", (
            f"Those lines occur {len(at)} times in `{path}`, at lines {where}: quote more lines "
            "around the one you mean."
        )
    if at:
        return None
    if old in text:
        first = old.split("\n")[0]
        where_found = [n for n, line in enumerate(lines) if first in line]
        shown = "; ".join(f"line {n + 1}: `{lines[n].strip()}`" for n in where_found[:3])
        return "edit.part_of_a_line", (
            f"`old` matches only part of a line, not whole lines: {shown}. Quote whole lines."
        )
    if new and new in text:
        return "edit.already", f"`old` isn't in `{path}`, and `new` already is: it may be made."
    size = len(want)
    best, best_at = 0.0, -1
    for i in range(max(1, len(lines) - size + 1)):
        ratio = difflib.SequenceMatcher(
            a=old, b="\n".join(lines[i : i + size]), autojunk=False
        ).ratio()
        if ratio > best:
            best, best_at = ratio, i
    if best < NEAR_RATIO:
        return (
            "edit.not_found",
            f"Those lines aren't in `{path}`, and nothing close is: read it with `read` first.",
        )
    shown = "\n".join(numbered(lines, best_at - NEAR_CONTEXT, best_at + size + NEAR_CONTEXT))
    return "edit.closest", (
        f"Those lines aren't in `{path}`. The closest block starts at line {best_at + 1}:\n{shown}"
    )


def parse(raw: Any) -> Any:
    """The call's arguments: an object from its JSON text, or what was sent if it isn't JSON."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return raw


def recorded(text: str, directory: Path) -> dict[str, Any] | None:
    """The JSON a bench answer says it saved, read from the host through the walk."""
    found = SAVED.search(text)
    if found is None:
        return None
    path = f"{found.group(1)}.json"
    if inside(directory, path) is None:
        return None
    try:
        record = json.loads(read_inside(directory, path))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


class WorkTools:
    """The tools over one run's working directory."""

    def __init__(
        self, work: Path, box: Box, desk: Desk, work_limit_bytes: int = 40 * 10**9
    ) -> None:
        self.work = work
        self.box = box
        self.desk = desk
        self.work_limit_bytes = work_limit_bytes
        self.use(toolsets.EVERY)

    def use(self, toolset: Toolset) -> None:
        """A run's toolset: its bench commands, what its needs may measure, and its skills."""
        self.toolset = toolset
        self.specs = specs(toolset)
        self.accepted = {s["function"]["name"]: s["function"]["parameters"] for s in self.specs}
        self.skills = skills.located(toolset.skills)

    def only(self, names: tuple[str, ...], *more: dict[str, Any]) -> None:
        """From here on, only these tools, and any `more` the run gives: after the approval, the
        after-action review reads and writes its review, and nothing changes the part."""
        chosen = [s for s in specs(self.toolset) if s["function"]["name"] in names]
        self.specs = chosen + list(more)
        self.accepted = {s["function"]["name"]: s["function"]["parameters"] for s in self.specs}

    def call(self, name: Any, raw: Any) -> Judged:
        args = parse(raw)
        for key in ("run", "try"):
            # Sent as text, as `cad_eval`'s V5 model sent `run`, it's read as the boolean it names.
            if isinstance(args, dict) and isinstance(args.get(key), str):
                said = args[key].strip().lower()
                if said in BOOLEAN_TEXT:
                    args = {**args, key: BOOLEAN_TEXT[said]}
        for key in ("needs", "variants", "groups", "looks", "why_changed", "lessons", "scores"):
            # A list sent as its JSON text is read as the list.
            if isinstance(args, dict) and isinstance(args.get(key), str):
                with contextlib.suppress(ValueError):
                    args = {**args, key: json.loads(args[key])}
        bad = self._well_formed(name, args)
        if bad is not None:
            return bad
        if name in DESK:
            found: Judged = getattr(self.desk, name)(args)
            return found
        answered: Judged = getattr(self, f"_{name}")(args)
        return answered

    def _well_formed(self, name: Any, args: Any) -> Judged | None:
        """The AST category: crew's `check_arguments` over these tools, then BFCL's type check."""
        shown = str(name)
        spec = self.accepted.get(name) if isinstance(name, str) else None
        if spec is None:
            tools = ", ".join(self.accepted)
            return Judged(shown, "malformed", f"There's no tool `{shown}`: {tools}.", "no_tool")
        if not isinstance(args, dict):
            said = f"`{shown}` wasn't run: its arguments aren't a JSON object."
            return Judged(shown, "malformed", said, "not_an_object")
        takes = ", ".join(f"`{a}`" for a in spec["properties"])
        unknown = [a for a in args if a not in spec["properties"]]
        if unknown:
            named = ", ".join(f"`{a}`" for a in unknown)
            said = f"`{shown}` wasn't run: it doesn't take {named}. It takes {takes}."
            return Judged(shown, "malformed", said, "unknown")
        missing = [a for a in spec.get("required", []) if a not in args]
        if missing:
            named = ", ".join(f"`{a}`" for a in missing)
            said = f"`{shown}` wasn't run: it needs {named}. It takes {takes}."
            return Judged(shown, "malformed", said, "missing")
        properties = spec["properties"]
        wrong = [
            k
            for k, v in args.items()
            if not isinstance(v, TYPES.get(properties[k].get("type", ""), object))
            or (properties[k].get("type") in ("number", "integer") and isinstance(v, bool))
        ]
        if wrong:
            said = ", ".join(f"`{k}` must be a {properties[k]['type']}" for k in wrong)
            return Judged(shown, "ill_typed", f"`{shown}` wasn't run: {said}.", "type")
        return None

    @staticmethod
    def _failed(name: str, why: str, reply: str) -> Judged:
        return Judged(name, "failed", reply, why)

    def _not_a_path(self, name: str, key: str) -> Judged:
        said = f"`{key}` is a file's path, of at most {paths.MAX_PATH} characters on one line."
        return self._failed(name, "not_a_path", said)

    def _existing(self, name: str, args: dict[str, Any], key: str = "path") -> Judged | None:
        if not a_path(args[key]):
            return self._not_a_path(name, key)
        target = inside(self.work, args[key])
        if target is None:
            reply = "Only files inside your directory, and never through a link."
            return self._failed(name, "outside", reply)
        if not target.is_file():
            return self._failed(name, "no_file", f"There's no file {args[key]}.")
        return None

    def _run_after(self, name: str, args: dict[str, Any]) -> tuple[str | None, Judged | None]:
        """The script a `write` or `edit` runs after: the file itself for `true`, a path as given,
        none otherwise; or why it can't be done, before anything is changed."""
        script = args.get("run_script")
        if script is not None:
            if script != args["path"]:
                missing = self._existing(name, args, "run_script")
                if missing is not None:
                    return None, missing
            return str(script), None
        return (args["path"], None) if args.get("run") is True else (None, None)

    def _then_run(self, name: str, said: str, script: str | None) -> Judged:
        if script is None:
            return Judged(name, "ok", said)
        ran = self._run({"path": script})
        return Judged(name, "ok", f"{said}\n\nRan {script}: {ran.reply}")

    def _write(self, args: dict[str, Any]) -> Judged:
        if not a_path(args["path"]):
            return self._not_a_path("write", "path")
        outside = "Write only inside your directory, and never through a link."
        if inside(self.work, args["path"]) is None:
            return self._failed("write", "outside", outside)
        if len(args["content"]) > MAX_CONTENT:
            said = f"A file is at most {MAX_CONTENT} characters."
            return self._failed("write", "too_large", said)
        script, wrong = self._run_after("write", args)
        if wrong is not None:
            return wrong
        try:
            paths.write_inside(self.work, args["path"], args["content"])
        except Outside:
            return self._failed("write", "outside", outside)
        except OSError as exc:
            return self._failed("write", "not_written", f"It couldn't be written: {exc}")
        said = f"Wrote {args['path']} ({len(args['content'])} characters)."
        return self._then_run("write", said, script)

    def _edit(self, args: dict[str, Any]) -> Judged:
        if not a_path(args["path"]):
            return self._not_a_path("edit", "path")
        target = inside(self.work, args["path"])
        if target is None or not target.is_file():
            reply = f"There's no file `{args['path']}`: `write` makes a new one."
            return self._failed("edit", "edit.no_file", reply)
        script, wrong = self._run_after("edit", args)
        if wrong is not None:
            return wrong
        outside = "Only files inside your directory, and never through a link."
        try:
            text = read_inside(self.work, args["path"])
        except Outside:
            return self._failed("edit", "outside", outside)
        except (OSError, UnicodeDecodeError):
            return self._failed("edit", "not_text", f"`{args['path']}` isn't a text file.")
        old, new = _trim(args["old"]), _trim(args["new"])
        refused = refusal(args["path"], text, old, new)
        if refused is not None:
            return self._failed("edit", refused[0], refused[1])
        lines = text.splitlines()
        want = old.split("\n")
        i = next(k for k in range(len(lines)) if lines[k : k + len(want)] == want)
        changed = lines[:i] + (new.split("\n") if new else []) + lines[i + len(want) :]
        after = "\n".join(changed) + ("\n" if text.endswith("\n") else "")
        broken = ""
        if args["path"].endswith(".py"):
            error = _parse_error(after)
            if error is not None and _parse_error(text) is None:
                reply = (
                    f"Your changes have NOT been applied: `{args['path']}` would stop parsing "
                    f"({error[0]}, line {error[1]}).\n\nThe lines as they would have been:\n"
                    f"{changed_lines(text, after)}\n\nThe lines as they are:\n"
                    f"{changed_lines(text, after, side='old')}"
                )
                return self._failed("edit", "edit.parse_guard", reply)
            if error is not None:
                broken = f" It doesn't parse: {error[0]} on line {error[1]}."
        try:
            paths.write_inside(self.work, args["path"], after)
        except Outside:
            return self._failed("edit", "outside", outside)
        except OSError as exc:
            return self._failed("edit", "not_written", f"It couldn't be written: {exc}")
        said = f"Replaced lines in `{args['path']}`.{broken}\nIt now reads:\n"
        return self._then_run("edit", said + changed_lines(text, after), script)

    def _read(self, args: dict[str, Any]) -> Judged:
        """A file's lines, numbered, or a picture: the model's own copy of its script, whatever
        compaction has done to the conversation."""
        missing = self._existing("read", args)
        if missing is not None:
            return missing
        path = str(args["path"])
        suffix = Path(path).suffix.lower()
        if suffix in PICTURES:
            return self._picture(path)
        if suffix in GEOMETRY:
            reply = f"`{path}` is geometry: `bench info` or `bench render` will answer about it."
            return self._failed("read", "not_text", reply)
        outside = "Only files inside your directory, and never through a link."
        try:
            text = read_inside(self.work, path)
        except Outside:
            return self._failed("read", "outside", outside)
        except (OSError, UnicodeDecodeError):
            return self._failed("read", "not_text", f"`{path}` isn't a text file.")
        lines = text.splitlines()
        start = max(1, int(args.get("start") or 1))
        end = min(
            len(lines), int(args.get("end") or start + READ_LINES - 1), start + READ_LINES - 1
        )
        shown = "\n".join(numbered(lines, start - 1, end))
        head = f"`{path}`, lines {start}-{end} of {len(lines)}:"
        more = f"\n(more: `start` {end + 1})" if end < len(lines) else ""
        whole = start == 1 and end >= len(lines)
        return Judged("read", "ok", f"{head}\n{shown}{more}", detail={"whole": whole})

    def _picture(self, path: str) -> Judged:
        """A picture to show the model, made ready in the container, so the Mac never parses it."""
        out = f".pictures/{time.monotonic_ns()}.png"
        done = self.box.helper("picture", [path, out])
        for line in reversed(done.output.splitlines()):
            if line.startswith(picture_module.MARK):
                said = json.loads(line[len(picture_module.MARK) :])
                if "error" in said:
                    reply = f"`{path}` didn't open as a picture: {said['error']}"
                    return self._failed("read", "not_a_picture", reply)
                try:
                    image = base64.b64encode(read_bytes_inside(self.work, out)).decode()
                except OSError:
                    break
                size = "x".join(str(n) for n in said["size"])
                shown = "x".join(str(n) for n in said["shown"])
                reply = f"`{path}` is {size} px, shown at {shown}, in a message of its own."
                return Judged("read", "ok", reply, images=[(path, image)])
        return self._failed("read", "unfinished", f"`{path}` couldn't be shown (exit {done.code}).")

    def _run(self, args: dict[str, Any]) -> Judged:
        missing = self._existing("run", args)
        if missing is not None:
            return missing
        done = self.box.python(args["path"])
        self.check_size()
        dropped = f"\n({done.dropped} bytes of its output before this were dropped)"
        reply = (
            f"exit {done.code} after {done.seconds}s\n{done.output[-RUN_TAIL:]}"
            f"{dropped if done.dropped else ''}"
        )
        if done.code != 0:
            hinted = self._hint(done.output)
            reply += f"\n\n{hinted}" if hinted else ""
        return Judged("run", "ok", reply, detail={"code": done.code, "seconds": done.seconds})

    def check_size(self) -> None:
        """/work is a folder on the Mac with no quota of its own."""
        size = paths.size_of(self.work)
        if size > self.work_limit_bytes:
            raise WorkFull(f"the working directory is {size} bytes: {self.work_limit_bytes} limit")

    def _hint(self, output: str) -> str:
        """For a run's last AttributeError, where the name is, from what's installed."""
        found = list(NO_ATTRIBUTE.finditer(output))
        if not found:
            return ""
        last = found[-1]
        owner = last.group(1) or last.group(2) or last.group(3)
        done = self.box.helper("hint", [owner, last.group(4)])
        for line in reversed(done.output.splitlines()):
            if line.startswith(hint_module.MARK):
                said = json.loads(line[len(hint_module.MARK) :])
                return "" if "error" in said else hint_module.said(said)
        return ""

    def _bench(self, args: dict[str, Any]) -> Judged:
        """Any bench command, its plain text going back. `render`'s picture reaches the model."""
        try:
            argv = shlex.split(args["command"])
        except ValueError as exc:
            return self._failed("bench", "not_a_command", f"`command` can't be read: {exc}")
        argv = argv[1:] if argv[:1] == ["bench"] else argv
        if not argv:
            return self._failed("bench", "not_a_command", "`command` names a bench command.")
        refused = self._not_in_toolset(argv[0])
        if refused is not None:
            return refused
        done = self.box.bench(argv)
        self.check_size()
        record = recorded(done.output, self.work)
        nudged = self.desk.bench_said(argv, done, record)
        text = done.output
        if len(text) > BENCH_CHARS:
            text = f"{text[:BENCH_CHARS]}\n(cut here: the whole answer is in the saved file)"
        images = self._rendered(record) if done.code in (0, 1) else []
        detail: dict[str, Any] = {"code": done.code, "seconds": done.seconds}
        reply = f"exit {done.code}\n{text}"
        if nudged:
            # A need's check run with other arguments, or one that keeps failing: lines in its
            # reply, never a limit (`nudge.py`).
            reply += f"\n\n{nudged['said']}"
            detail.update(nudged["detail"])
        return Judged("bench", "ok", reply, images=images, detail=detail)

    def _not_in_toolset(self, command: str) -> Judged | None:
        """A command of another kind's toolset, which this run hasn't loaded."""
        if command not in COMMANDS or command in self.toolset.commands:
            return None
        home = toolsets.where(command)
        kind = f"the {home.title} toolset" if home is not None else "no toolset of this run's"
        mine = ", ".join(f"`{c}`" for c in self.toolset.commands)
        reply = (
            f"`{command}` is in {kind}, and this run's is {self.toolset.title}: a request's "
            f"`## Kind` loads a kind's commands. This run's are {mine}."
        )
        return self._failed("bench", "not_in_toolset", reply)

    def _rendered(self, record: dict[str, Any] | None) -> list[tuple[str, str]]:
        """The picture a `render` wrote: its sheet, if it made one, or its one view."""
        if not record or record.get("command") != "render":
            return []
        files = [str(f) for f in record.get("files") or [] if str(f).endswith(".png")]
        sheets = [f for f in files if f.endswith("-sheet.png")]
        chosen = sheets[-1] if sheets else (files[0] if files else None)
        if chosen is None or inside(self.work, chosen) is None:
            return []
        try:
            return [(chosen, base64.b64encode(read_bytes_inside(self.work, chosen)).decode())]
        except OSError:
            return []

    def _skill(self, args: dict[str, Any]) -> Judged:
        found = self.skills.get(args["name"])
        if found is None:
            named = ", ".join(f"`{n}`" for n in self.skills)
            return self._failed("skill", "unknown", f"No skill `{args['name']}`: {named}.")
        # Which file it read, for the record of how a run finds its skills (`skill_reads`).
        return Judged("skill", "ok", found[1], detail={"file": found[2]})

    def _lookup(self, args: dict[str, Any]) -> Judged:
        return self._search("lookup", args)

    def _docs(self, args: dict[str, Any]) -> Judged:
        return self._search("docs", args)

    def _search(self, name: str, args: dict[str, Any]) -> Judged:
        if not args["query"].strip() or len(args["query"]) > paths.MAX_PATH:
            reply = f"`query` is a name or some words, of at most {paths.MAX_PATH} characters."
            return self._failed(name, "not_a_query", reply)
        page = args.get("page", 1)
        if page < 1:
            return self._failed(name, "not_a_page", "`page` is a page number, from 1.")
        request = {"tool": name, "query": args["query"], "page": page}
        done = self.box.helper("search", [json.dumps(request)])
        for line in reversed(done.output.splitlines()):
            if line.startswith(search_module.MARK):
                return Judged(name, "ok", line[len(search_module.MARK) :])
        return self._failed(name, "unfinished", f"The {name} didn't finish (exit {done.code}).")
