"""A kind's standing checks: for stained glass, `light`, `capture` and `pause` on every frame at
`done` and at each stop, whatever the needs say, as `thin` reads every file (mqucifer/print-shop#52;
the Sponsor, 2026-11-04).

On `eval-unnamed-1` the model set `capture` aside because "the request doesn't ask" about holding,
and planned a solid floor under the pane, so no light passed: the stand-in caught it by reading
the plan. The skill holds every stained-glass piece to both, so the harness runs them, and
`set_aside` refuses them on the model's files. On a given reference, as the "before" state, a
failing check can still be set aside.

**What they run on.** `thin` needs only a file. `capture` reads a pane in its pocket, so it needs
the pocket's height, the play and the pane's thickness, and `light` needs which files are the
frame and which the panes. So a stained-glass `done` declares them, in `glass`: each frame, its
panes and any other part of the frame (`with`), with the pocket's height, the play round a pane
and the pane's thickness. Every file of the part is in it, so no pane is left out. It's the
model's word, shown at the stop beside the readings; a need carries the request's own figures.

**`pause` too** (mqucifer/print-shop#66). `eval-rims-4`'s and `eval-fit-2`'s closing words each put
the pause at the pocket's floor: neither run ran `pause`, and both are wrong, as the nozzle would
meet the pane. `eval-thick-2`
found the same in batch 3. So `pause` reads each frame at its declared pocket height and pane
thickness, at the bench's own layers (0.2 mm, as the run doesn't know the printer's process; the
refinement pass reads it again at the process's), and fails where the pane stands above the last
layer before its pocket closes. Where nothing closes over the pockets, it passes: a pane can go in
afterwards.

**A flush frame** (mqucifer/print-shop#293). `pieces build --flush` makes each pane stepped: its
body under the frame's lip, and a raised face down through its opening, level with the lead
(mqucifer/print-shop#287). It writes each pane twice: where it sits in the frame, and laid for
the bed, body down, which is how it prints. So `glass` declares a flush frame's panes where they
sit, in `panes`, and the file each prints from, in `laid`, with `face_thickness`, the raised
face's, beside `pane_thickness`, the body's, and `face_clearance`, the play round the face
(`clearance` unless given). Every reading of the pane in its frame, `light`, `capture` and
`pause`, reads `panes`; every reading of how it prints reads `laid` (`downward.py`, and the
refinement pass's plates). A declaration's thicknesses are read against its files before it's
taken (`borne_out`), and a stepped pane declared flat fails `light`: read alone, a flush pane as
it sits needs support, and the way out of that isn't to weaken the check.

**Their limits.** Where a settled need runs the same check on that frame (and, for `light`, every
pane of it), the need's reading is the standing check's, at the request's own figures: a rim the
request sizes by share can be under the bench's 0.4 mm, and would fail every such design. Where
none does, `capture` runs at the bench's own least lip, 0.4 mm after play, a nozzle's width,
with `--panes` the bodies in the pane files declared (`Glass.count`), and `light` with no
limit, failing a pane with nothing open straight through: the kind's rule is that light passes
through every pane.
"""

from __future__ import annotations

import math
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.rounds import judge, paths
from print_shop.rounds.needs import UNSURE, Need, Reading

LIGHT = "light"
HOLD = "pane-hold"
INSERT = "insert"
#: The checks the harness runs for a kind, by the bench command each is.
CHECKS = {LIGHT: "light", HOLD: "capture", INSERT: "pause"}
SHAPE = (
    "`glass` names each frame and its panes, for the harness's own `light`, `capture` and "
    "`pause`: "
    '[{"frame": "frame.step", "panes": ["pane_1.step", "pane_2.step"], "with": [], '
    '"pocket_z": 2.0, "clearance": 0.25, "pane_thickness": 1.6}]. `pocket_z` is a height '
    "inside the pockets, `clearance` the play round a pane and `pane_thickness` the pane's, each "
    "in mm; `with` is any other part of the frame, such as a cap printed apart. Every file of "
    "`part` is a frame, a pane or `with`; a given file may be one too, as "
    "`references/frame.step`."
)
FLUSH_SHAPE = (
    "A flush frame, as `pieces build --flush` writes it, declares each pane twice: where it sits "
    "in `panes`, and the file it prints from in `laid`, with its raised face's thickness: "
    '{"frame": "frame.step", "panes": ["pane_blue.step"], "laid": {"pane_blue.step": '
    '"pane_blue-laid.step"}, "pocket_z": 1.3, "clearance": 0.25, "pane_thickness": 1.0, '
    '"face_thickness": 0.8, "face_clearance": 0.25}. `pane_thickness` is then the body\'s, and '
    "`face_clearance` the play round the raised face, `clearance` unless given: `pieces build "
    "--flush` gives the whole of it as `glass` in its numbers."
)
HEADING = "Every pane, by `light`, `capture` and `pause`, whatever the needs say:"
KEYS = {"frame", "panes", "with", "pocket_z", "clearance", "pane_thickness"}
#: A flush frame's own keys (mqucifer/print-shop#293).
FLUSH_KEYS = {"laid", "face_thickness", "face_clearance"}
#: How far a declared thickness may be from what its files bear out, in mm: the meshes' level
#: faces, read to a thousandth of a mm.
THICK_TOL = 0.01


@dataclass(frozen=True)
class Glass:
    """One frame, as `done` or `ask` declares it."""

    frame: str
    panes: tuple[str, ...]
    pocket_z: float
    clearance: float
    pane_thickness: float
    others: tuple[str, ...] = field(default=())
    #: How many panes the pane files hold: their bodies, as the harness counted them when the
    #: frame was declared (`bodies_in`), or None where a file's couldn't be counted.
    bodies: int | None = None
    #: A flush frame's: each pane's file where it sits and the file it prints from, laid for the
    #: bed; the raised face's thickness; and the play round it (mqucifer/print-shop#293).
    laid: tuple[tuple[str, str], ...] = field(default=())
    face_thickness: float | None = None
    face_clearance: float | None = None

    @property
    def flush(self) -> bool:
        return bool(self.laid)

    def prints_from(self) -> dict[str, str]:
        """Each flush pane's file where it sits, by the file it prints from."""
        return dict(self.laid)

    def files(self) -> list[str]:
        return [self.frame, *self.panes, *self.others, *(laid for _, laid in self.laid)]

    def count(self) -> int:
        """How many panes the frame has: the bodies in its pane files, and where they couldn't
        be counted, the files (mqucifer/print-shop#153). A file may hold every pane of one
        colour: on the first traced scene six files held 52 panes, and `capture --panes 6`
        failed a frame that held all 52."""
        return self.bodies if self.bodies is not None else len(self.panes)

    def plain(self) -> dict[str, Any]:
        kept: dict[str, Any] = {
            "frame": self.frame,
            "panes": list(self.panes),
            "with": list(self.others),
            "pocket_z": self.pocket_z,
            "clearance": self.clearance,
            "pane_thickness": self.pane_thickness,
        }
        if self.bodies is not None:
            kept["pane_bodies"] = self.bodies
        if self.flush:
            kept["laid"] = dict(self.laid)
            kept["face_thickness"] = self.face_thickness
            kept["face_clearance"] = self.face_clearance
        return kept

    def commands(self) -> dict[str, str]:
        """Each check's command, by its name."""
        q = shlex.quote
        others = "".join(f" --with {q(name)}" for name in self.others)
        return {
            LIGHT: f"light {q(self.frame)} {' '.join(q(p) for p in self.panes)}{others}",
            HOLD: (
                f"capture {q(self.frame)} --pocket-z {self.pocket_z:g} --clearance "
                f"{self.clearance:g} --pane-thickness {self.pane_thickness:g} --panes "
                f"{self.count()}"
                + (
                    f" --raised-faces {' '.join(q(p) for p in self.panes)} --face-play "
                    f"{self.face_clearance:g}"
                    if self.flush and self.face_clearance is not None
                    else ""
                )
            ),
            INSERT: (
                f"pause {q(self.frame)} --pocket-z {self.pocket_z:g} --pane-thickness "
                f"{self.pane_thickness:g}"
            ),
        }


def load(kept: list[dict[str, Any]] | None) -> list[Glass]:
    """A declaration as `state.json` keeps it."""
    return [
        Glass(
            d["frame"],
            tuple(d["panes"]),
            d["pocket_z"],
            d["clearance"],
            d["pane_thickness"],
            tuple(d.get("with") or ()),
            d.get("pane_bodies"),
            tuple((d.get("laid") or {}).items()),
            d.get("face_thickness"),
            d.get("face_clearance"),
        )
        for d in kept or []
    ]


#: A solid in a STEP file, as ISO 10303-21 writes one: a pane file's bodies are these.
STEP_SOLID = re.compile(rb"=\s*(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS)\s*\(")


def bodies_in(work: Path, name: str) -> int | None:
    """How many bodies a pane file holds: a STEP file's solids, by the entities it lists, or
    an STL's closed shells where nothing joins them. None where the file can't be read so."""
    if paths.inside(work, name) is None:
        return None
    try:
        data = paths.read_bytes_inside(work, name)
    except OSError:
        return None
    ending = name.lower().rsplit(".", 1)[-1]
    if ending in ("step", "stp"):
        return len(STEP_SOLID.findall(data)) or None
    if ending == "stl":
        try:
            import io

            import numpy as np
            import trimesh

            mesh = trimesh.load_mesh(io.BytesIO(data), file_type="stl", process=True)
            faces = np.asarray(mesh.faces)
        except Exception:  # noqa: BLE001 - whatever it is, it isn't a mesh to count
            return None
        # Faces joined through the points they share, by union-find: no graph library here.
        root = list(range(len(mesh.vertices)))

        def find(k: int) -> int:
            while root[k] != k:
                root[k] = root[root[k]]
                k = root[k]
            return k

        for a, b, c in faces.tolist():
            root[find(a)] = find(b)
            root[find(c)] = find(b)
        return len({find(int(k)) for k in np.unique(faces)}) or None
    return None


def pane_bodies(work: Path, panes: list[str]) -> int | None:
    """The bodies in a frame's pane files, or None where any file's can't be counted."""
    counts = [bodies_in(work, name) for name in panes]
    return None if any(c is None for c in counts) else sum(c for c in counts if c is not None)


def _figure(entry: dict[str, Any], key: str, positive: bool = True) -> float | None:
    value = entry.get(key)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    if positive and value <= 0:
        return None
    return float(value)


def read(given: Any, part: list[str], work: Path, whole: bool = True) -> list[Glass] | str:
    """The declaration, or what's wrong with it. Each file is of `part` or under `references/`,
    and with `whole`, as at `done`, every file of `part` is in it."""
    if not isinstance(given, list) or not given:
        return SHAPE
    found: list[Glass] = []
    named: list[str] = []
    for entry in given:
        if not isinstance(entry, dict) or not {"frame", "panes"} <= set(entry) <= KEYS | FLUSH_KEYS:
            return SHAPE
        frame, panes, others = entry["frame"], entry["panes"], entry.get("with") or []
        if not isinstance(frame, str) or not frame.strip():
            return SHAPE
        if not (isinstance(panes, list) and panes and all(isinstance(p, str) for p in panes)):
            return f"Each frame's `panes` is the list of its pane files. {SHAPE}"
        if not (isinstance(others, list) and all(isinstance(o, str) for o in others)):
            return SHAPE
        figures = {
            "pocket_z": _figure(entry, "pocket_z", positive=False),
            "clearance": _figure(entry, "clearance"),
            "pane_thickness": _figure(entry, "pane_thickness"),
        }
        missing = [k for k, v in figures.items() if v is None]
        if missing:
            said = ", ".join(f"`{k}`" for k in missing)
            return (
                f"`{frame}`: {said} is a number in mm, and the play and thickness more than 0. "
                + SHAPE
            )
        flush = _flush_of(entry, frame, panes, figures["clearance"] or 0.0)
        if isinstance(flush, str):
            return flush
        laid, face, face_play = flush
        for name in [frame, *panes, *others, *(one for _, one in laid)]:
            if name in named:
                return f"`{name}` is named twice in `glass`: each file is one frame, pane or `with`"
            if name not in part and not (
                name.startswith("references/") and (work / name).is_file()
            ):
                return f"`{name}` in `glass` isn't a file of `part` or a given reference. {SHAPE}"
            named.append(name)
        found.append(
            Glass(
                frame,
                tuple(panes),
                figures["pocket_z"] or 0.0,
                figures["clearance"] or 0.0,
                figures["pane_thickness"] or 0.0,
                tuple(others),
                pane_bodies(work, panes),
                laid,
                face,
                face_play,
            )
        )
    left = [name for name in part if name not in named]
    if whole and left:
        said = ", ".join(f"`{name}`" for name in left)
        return (
            f"`glass` leaves out {said}: every file of `part` is a frame, a pane or `with`, so "
            f"`light`, `capture` and `pause` read every pane. {SHAPE}"
        )
    return found


def _flush_of(
    entry: dict[str, Any], frame: str, panes: list[str], clearance: float
) -> tuple[tuple[tuple[str, str], ...], float | None, float | None] | str:
    """A flush frame's own figures, or what's wrong with them: every pane with its laid file,
    and the raised face's thickness. Nothing for a flat frame, which has none of them."""
    if not FLUSH_KEYS & set(entry):
        return (), None, None
    laid = entry.get("laid")
    if not isinstance(laid, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in laid.items()
    ):
        return (
            f"`{frame}` is declared flush, and its `laid` isn't each pane's file with the file "
            f"it prints from. {FLUSH_SHAPE}"
        )
    missing = [p for p in panes if p not in laid]
    if missing:
        said = ", ".join(f"`{p}`" for p in missing)
        return (
            f"`{frame}` is declared flush, and `laid` names no file to print {said} from: a "
            f"flush pane prints from its laid file, body down, and every pane has one. "
            f"{FLUSH_SHAPE}"
        )
    stray = [p for p in laid if p not in panes]
    if stray:
        said = ", ".join(f"`{p}`" for p in stray)
        return f"`{frame}`'s `laid` names {said}, which isn't one of its `panes`. {FLUSH_SHAPE}"
    twice = [v for v in laid.values() if list(laid.values()).count(v) > 1 or v in panes]
    if twice:
        return (
            f"`{frame}`'s `laid` names `{twice[0]}` for more than one pane, or as a pane: each "
            f"pane prints from a file of its own. {FLUSH_SHAPE}"
        )
    given = [v for v in laid.values() if v.startswith("references/")]
    if given:
        return (
            f"`{frame}`'s `laid` names `{given[0]}`, a given file: a laid file is what prints, "
            f"so it's a file of `part`. {FLUSH_SHAPE}"
        )
    face = _figure(entry, "face_thickness")
    if face is None:
        return (
            f"`{frame}` is declared flush, and `face_thickness` isn't its raised face's "
            f"thickness, a number in mm over 0. {FLUSH_SHAPE}"
        )
    play = _figure(entry, "face_clearance") if "face_clearance" in entry else clearance
    if play is None:
        return f"`{frame}`'s `face_clearance` is the play round each raised face, in mm over 0"
    return tuple((p, laid[p]) for p in panes), face, play


def placed_panes(given: Any) -> set[str]:
    """The flush panes a `glass` as given names where they sit, by its `laid`, read before the
    declaration is: a flush pane where it sits isn't printed, its laid file is. Nothing where
    it isn't one."""
    found: set[str] = set()
    for entry in given if isinstance(given, list) else []:
        laid = entry.get("laid") if isinstance(entry, dict) else None
        if isinstance(laid, dict):
            found |= {name for name in laid if isinstance(name, str)}
    return found


def steps_wanted(glass: list[Glass]) -> list[str]:
    """The files whose level faces `borne_out` reads: each flush pane's, where it sits and laid."""
    return [name for one in glass if one.flush for pair in one.laid for name in pair]


def steps(box: Any, glass: list[Glass], where: Path) -> dict[str, Any] | str:
    """Each flush pane file's level faces, as the bench reads them in a fresh container
    (`bench/pane_steps.py`), or why they couldn't be read. Nothing for a flat declaration."""
    import json

    from print_shop.bench.pane_steps import MARK

    names = steps_wanted(glass)
    if not names:
        return {}
    done = box.helper("pane_steps", names, timeout=300, where=where)
    for line in reversed(done.output.splitlines()):
        if line.startswith(MARK):
            found: dict[str, Any] = json.loads(line[len(MARK) :])
            return found
    return (
        f"the flush panes' files couldn't be read for their steps, exit {done.code}: "
        f"{done.output[-300:]}"
    )


def borne_out(glass: list[Glass], steps: dict[str, Any]) -> str:
    """What's wrong with a flush declaration's thicknesses, by its files' level faces as the
    bench read them (`bench/pane_steps.py`), or nothing. Where a pane sits, its raised face runs
    from its lowest point to its body's underside, a level face facing down, and its body from
    there to its top; laid, its body runs from the bed to its top's level face, and its raised
    face on from there. A file that couldn't be read is said, and isn't taken."""
    wrong: list[str] = []
    for one in glass:
        if not one.flush or one.face_thickness is None:
            continue
        body, face = one.pane_thickness, one.face_thickness
        for pane, laid in one.laid:
            for name, placed in ((pane, True), (laid, False)):
                row = steps.get(name)
                if not isinstance(row, dict) or "low" not in row:
                    why = (row or {}).get("unread") if isinstance(row, dict) else None
                    wrong.append(f"`{name}` couldn't be read for its steps: {why or 'no answer'}")
                    continue
                low, high = float(row["low"]), float(row["high"])
                inside = [
                    f for f in row.get("level", [])
                    if low + THICK_TOL < float(f["z"]) < high - THICK_TOL
                ]  # fmt: skip
                key = "down" if placed else "up"
                steps_at = [float(f["z"]) for f in inside if float(f[key]) > 0]
                where = "where it sits in the frame" if placed else "laid for the bed"
                if not steps_at:
                    wrong.append(
                        f"`{name}`, {where}, has no step: it runs from z={low:g} to z={high:g} "
                        "as one thickness, where a flush pane is a body and a raised face"
                    )
                    continue
                step = steps_at[0] if placed else steps_at[-1]
                read_face = step - low if placed else high - step
                read_body = high - step if placed else step - low
                if abs(read_face - face) > THICK_TOL or abs(read_body - body) > THICK_TOL:
                    wrong.append(
                        f"`{name}`, {where}, is a body {read_body:.3f} mm thick and a raised face "
                        f"{read_face:.3f} mm, where `glass` says `pane_thickness` {body:g}, the "
                        f"body's, and `face_thickness` {face:g}"
                    )
    if not wrong:
        return ""
    return (
        "`glass` declares a flush frame its files don't bear out: "
        + "; ".join(wrong)
        + f". {FLUSH_SHAPE}"
    )


def _as_a_need(
    name: str, one: Glass, ran: list[tuple[Need, Reading]]
) -> tuple[Need, Reading] | None:
    """A settled need that runs this check on this frame, and on every pane of it: its reading is
    the standing check's, at the request's own figures. A rim sized by the request's share can be
    under the bench's 0.4 mm, which would fail every such design."""
    for need, reading in ran:
        files = need.files()
        words = need.argv()
        if not words or words[0] != CHECKS[name] or not files or files[0] != one.frame:
            continue
        if name == LIGHT and not set(one.panes) <= set(files[1:]):
            continue
        if name == HOLD and one.flush and "--raised-faces" not in words:
            # A flush frame's raised faces are read too: a need that doesn't read them isn't
            # the standing check (mqucifer/print-shop#293).
            continue
        return need, reading
    return None


def check(
    box: Any,
    glass: list[Glass],
    where: Path,
    kept: Path,
    ran: list[tuple[Need, Reading]] | None = None,
) -> list[judge.Standing]:
    """`light`, `capture` and `pause` on each frame and its panes, each in a fresh container, or as
    a settled need already read it (`ran`)."""
    found = []
    for one in glass:
        for name, command in one.commands().items():
            mine = _as_a_need(name, one, ran or [])
            if mine is not None:
                need, reading = mine
                unsure = reading.verdict in UNSURE or not reading.verdict
                numbers = dict((reading.record or {}).get("numbers") or {})
                passed, why = reading.passed, f"as the need `{need.id}` reads it: {reading.why}"
                flat_but_stepped = _stepped_flat(name, one, numbers)
                if flat_but_stepped:
                    passed, why = False, flat_but_stepped
                found.append(
                    judge.Standing(
                        name,
                        one.frame,
                        need.command,
                        passed,
                        unsure and not passed,
                        reading.verdict,
                        why,
                        numbers,
                    )
                )
                continue
            need = Need(
                f"{name}-{judge.NOT_IN_A_NAME.sub('_', one.frame)}"[:80],
                "the kind's standing check",
                "derived",
                "bench",
                command=command,
                verdicts=["OK"],
            )
            reading = judge.read_need(box, need, where, kept)
            unsure = reading.verdict in UNSURE or reading.exit in (2, 3) or not reading.verdict
            said = str((reading.record or {}).get("why") or "")
            why = f"{reading.verdict}: {said}" if reading.verdict and said else reading.why
            numbers = dict((reading.record or {}).get("numbers") or {})
            passed = reading.passed
            flat_but_stepped = _stepped_flat(name, one, numbers)
            if flat_but_stepped:
                passed, why = False, flat_but_stepped
            elif name == LIGHT and passed:
                least = numbers.get("least_open_pct")
                if isinstance(least, int | float) and least <= 0:
                    passed = False
                    pane = numbers.get("least_open_file") or "a pane"
                    why = (
                        f"no light passes through {pane}: nothing of it is open straight through "
                        "the frame"
                    )
            found.append(
                judge.Standing(
                    name,
                    one.frame,
                    command,
                    passed,
                    unsure and not passed,
                    reading.verdict,
                    why,
                    numbers,
                )
            )
    return found


def _stepped_flat(name: str, one: Glass, numbers: dict[str, Any]) -> str:
    """Where `light` read a pane of a frame declared flat as stepped, a flush pane's body over
    its raised face, the words that fail it (mqucifer/print-shop#293): read alone as it sits, it
    would need support, and it prints from a laid file `glass` doesn't name."""
    if name != LIGHT or one.flush:
        return ""
    stepped = [f for f in numbers.get("stepped") or [] if f in one.panes]
    if not stepped:
        return ""
    said = ", ".join(f"`{f}`" for f in stepped)
    return (
        f"{said} is stepped, a body over a raised face, as a flush pane is, and `glass` declares "
        f"`{one.frame}` flat, with no file for it to print from: declare it flush, each pane "
        f"with its laid file. {FLUSH_SHAPE}"
    )


def said(found: list[judge.Standing]) -> list[str]:
    """Each reading, a line each, as `done`'s reply and the stop give them."""
    lines = []
    for one in found:
        mark = "ok" if one.passed else "can't tell" if one.unsure else "FAIL"
        lines.append(f"- `{one.command}`, {mark}: {one.why}")
    return lines


# Curves stored as flat strips (mqucifer/print-shop#59)

#: The check's name in the ledger's `standing` table.
CURVES = "curves"
#: A file with this many flat faces in runs of strips (`info`'s `strip_faces`) has curves stored
#: as flat strips, and fails. A part's curves are read from its faces, not from its words: built
#: as curves they're curved faces, which `info` doesn't count, and built from a polygon they're a
#: run of thin flat faces, one for each segment, each turning a few degrees to the next. Measured
#: with `info` in the bench's image on copies (`~/print-shop/proofs/m18-stops/curves.json`):
#: `suncatcher-3`'s frame has 1,413 of 6,069 flat faces under 1 mm, and none curved, and two of its
#: panes 33 and 153; batch 3's arcs frame has 1 of 54, and its rings frame 0 of 134, each with true
#: circles; all 50 STEP files of batch 3's approved folders pass, the most having 4
#: (`curves_batch3_approved.json`). One small round hole cut from a polygon is enough: at 16
#: segments a quarter, a 3 mm radius is 64 faces of 0.29 mm. Until mqucifer/print-shop#85 every
#: small flat face counted, and a lid of boxes, a circle and fillets failed on its clip beams'
#: narrow sides; narrow faces that aren't in a run don't count now. The same copies read again
#: so (`~/print-shop/proofs/m18-bench8/curves.json`): the frame and both panes still fail, and
#: the 50 files still pass. Where a part still has strips it doesn't mean, its reason is said
#: with `set_aside`.
STRIPS_AT = 20
#: What a curved part's file can be read by: an STL has no faces of its own.
FACED = (".step", ".stp", ".brep")
CURVES_HEADING = "Curves stored as flat strips, by `info`'s runs of strips, on every file:"


def strips_counted(numbers: dict[str, Any]) -> tuple[int, str] | None:
    """How `curves` counts a file's strips, from `info`'s numbers: its flat faces in runs of strips
    (`strip_faces`, mqucifer/print-shop#85), and the words for it, or None where they didn't come
    back. A narrow flat face not in a run, a beam's side or a fillet's seam, isn't counted."""
    small, flat = numbers.get("small_flat_faces"), numbers.get("flat_faces")
    count, lengths = numbers.get("strip_faces"), numbers.get("strip_runs")
    if isinstance(count, bool) or not isinstance(count, int) or not isinstance(lengths, list):
        return None
    of = f" of {flat}" if isinstance(flat, int) and not isinstance(flat, bool) else ""
    if not lengths:
        if isinstance(small, int) and not isinstance(small, bool) and small:
            return 0, f"{small}{of} flat faces under 1 mm across: narrow flat faces only, no strips"
        return 0, "no strips"
    runs = "1 run" if len(lengths) == 1 else f"{len(lengths)} runs"
    longest = max(lengths)
    return count, (
        f"{runs} of strips (the longest {longest} faces), {count}{of} flat faces under 1 mm "
        "across in them"
    )


def curves(
    box: Any, files: list[str], where: Path, kept: Path, asides: dict[str, str]
) -> list[judge.Standing]:
    """`info` on each file, each in a fresh container, failing one with `STRIPS_AT` or more flat
    faces in runs of strips (`strips_counted`), unless the model set it aside with its reason
    (`asides`, by file). On `suncatcher-3` `info` said so at five calls, naming the skill the
    model had read, and the frame stayed 23 MB of strips: a reading it can pass over was passed
    over every time."""
    found = []
    for name in files:
        command = f"info {shlex.quote(name)}"
        if not name.lower().endswith(FACED):
            found.append(
                judge.Standing(
                    CURVES, name, command, True, False, "", "an STL has no faces of its own"
                )
            )
            continue
        need = Need(
            f"curves-{judge.NOT_IN_A_NAME.sub('_', name)}"[:80],
            "curves built as curves",
            "derived",
            "bench",
            command=command,
        )
        reading = judge.read_need(box, need, where, kept)
        numbers = dict((reading.record or {}).get("numbers") or {})
        counted = strips_counted(numbers)
        if counted is None:
            why = f"no count of strips came back: {reading.why}"
            found.append(
                judge.Standing(CURVES, name, command, False, True, reading.verdict, why, numbers)
            )
            continue
        count, said = counted
        if count < STRIPS_AT:
            found.append(
                judge.Standing(CURVES, name, command, True, False, reading.verdict, said, numbers)
            )
            continue
        if name in asides:
            why = f"{said}, set aside by the model: {asides[name]}"
            found.append(
                judge.Standing(CURVES, name, command, True, False, reading.verdict, why, numbers)
            )
            continue
        why = (
            f"{said}: its curves are stored as flat strips, a face for each segment of a polygon, "
            f"at {STRIPS_AT} faces in runs or more. Build each curve as a curve, with CadQuery's "
            "circles and arcs (the skill `building-curves-from-a-layout`), not `polyline` through "
            "a polygon's points. If the file has no curves, say so with `set_aside`, giving "
            "`curves` and `why`: the next stop shows your reason"
        )
        found.append(
            judge.Standing(CURVES, name, command, False, False, reading.verdict, why, numbers)
        )
    return found
