"""The refinement pass on an approved run (mqucifer/print-shop#51): `docs/rounds.md` says what.

The Sponsor, 2026-11-05: "It should use orca's cli to add pauses not raw gcode. Use what we can of
orca at any point. Same with color setting by object." And: "The goal of it is feedback that we
can learn from. Part of that feedback could be part changes that need approval first."

So, for each folder of the approved result:
1. Each file is meshed for Orca in the bench's image (`bench/refine.py`), as Orca's CLI won't read
   STEP, and the mesh is held to OCCT's volume and box.
2. The frame, the largest file, is its own plate. A file that sits inside the frame's box, above
   its underside, is set in at a pause: a pane. Every other file prints on a plate of its colour,
   with the panes, as this printer has one filament loaded at a time.
3. The bench's `pause` reads the layer to pause after, from the panes as they sit, at the
   process's own layers. Orca is given that pause (`--load-custom-gcodes`) and writes it itself.
   Each plate's objects get their filament, with its colour (`--load-filaments`,
   `--load-filament-ids`). Nothing edits G-code.
4. Orca's G-code and log are read: warnings, where the pause went, each pane's first layer over
   it, bridges and overhangs, gap fill and the narrowest line, time and filament. Then the pass's
   own reading of Orca's lines against the part: a stretch with no line, and walls laid over
   each other.
5. Each is a finding in the ledger. One that needs the part to change gets a proposal
   (`proposals.py`), never applied. Where none does, the print steps are the pass's own, written
   into the folder's README beside the model's words.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from print_shop.bench.refine import ANGULAR, LINEAR, MARK
from print_shop.refine import findings as fnd
from print_shop.refine.approved import Approved, File, Group, pause_inputs
from print_shop.refine.context import Context, layers_of
from print_shop.refine.findings import Finding
from print_shop.rounds import lessons as lessons_module
from print_shop.rounds.box import Box
from print_shop.rounds.ledger import DesignLedger, now
from print_shop.rounds.paths import content_hash
from print_shop.rounds.tools import recorded
from print_shop.slicer import gcode
from print_shop.slicer.orca import PauseAt, PlateObject, PlateSlice, slice_plate

#: How far a mesh's volume may differ from OCCT's: the chords at 0.003 mm on a curved face.
MESH_SHARE = 1e-3
#: How far a mesh's box may differ from OCCT's, in mm. OCCT pads its box by its tolerance.
MESH_BOX = 0.01
#: A plate's G-code is read for each object's lines only up to this many layers' worth.
READ_LIMIT = 2000

Slicer = Callable[..., PlateSlice]


@dataclass
class Plate:
    name: str
    group: str
    files: list[File]
    colours: list[str]
    frame: File | None = None
    panes: list[File] = field(default_factory=list)
    pauses: list[PauseAt] = field(default_factory=list)
    #: The bench's pause readings that set `pauses`.
    pause_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Outcome:
    refinement: int
    n: int
    folder: Path
    findings: list[Finding]
    proposals: list[dict[str, Any]]
    steps: dict[str, list[str]]


#: Who writes a refinement finding's lesson, as `lessons.writer` names it.
REFINED_BY = "the refinement pass"


def _part_kind(folder: Path) -> str | None:
    """The request's kind, from the run's `state.json`, if it has one."""
    try:
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    form = state.get("form") or {}
    kind = form.get("kind") if isinstance(form, dict) else None
    return str(kind) if kind else None


def _bench_answer(output: str) -> dict[str, Any]:
    if MARK not in output:
        raise RuntimeError(f"the bench's refine helper gave no answer: {output[-600:]}")
    answer: dict[str, Any] = json.loads(output.split(MARK, 1)[1].splitlines()[0])
    return answer


class Pass:
    """One refinement of one approved run."""

    def __init__(
        self,
        approved: Approved,
        ledger: DesignLedger,
        context: Context,
        box: Box | None = None,
        slicer: Slicer = slice_plate,
        orca: str = "",
        commit: str = "",
    ) -> None:
        self.approved = approved
        self.ledger = ledger
        self.context = context
        self.slicer = slicer
        self.orca = orca
        self.commit = commit
        done = ledger.rows("SELECT MAX(n) AS n FROM refinements WHERE run=?", approved.run)
        self.n = int(done[0]["n"] or 0) + 1
        self.out = approved.folder / "refinements" / str(self.n)
        self.box = box or Box(self.out)
        self.found: list[Finding] = []
        self.meshed: dict[str, dict[str, Any]] = {}
        self.sliced: dict[str, tuple[Plate, gcode.Sliced]] = {}
        #: Off while a proposal is checked: its slices aren't the approved folder's.
        self.recording = True
        self.id = 0
        #: The candidate lessons its findings made (mqucifer/print-shop#59).
        self.lessons: list[int] = []
        #: The request's kind, as `start` gives a kind's lessons by it.
        self.part_kind = _part_kind(approved.folder)

    # The pass

    def run(self) -> Outcome:
        from print_shop.refine import proposals  # noqa: PLC0415 - proposals uses this module

        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        self.id = self.ledger.insert(
            "refinements",
            run=self.approved.run,
            n=self.n,
            approval=self.approved.approval,
            decision=self.approved.decision,
            begun_at=now(),
            harness_commit=self.commit,
            orca=self.orca,
            context=json.dumps(self.context.plain(), sort_keys=True),
            folder=str(self.out),
        )
        ended = "refined"
        steps: dict[str, list[str]] = {}
        made: list[dict[str, Any]] = []
        try:
            for group in self.approved.groups:
                plates = self.plates(group)
                for plate in plates:
                    self.slice(plate)
            ids = self.record_findings()
            self.lessons = self.to_lessons(ids)
            made = proposals.propose(self, ids)
            for group in self.approved.groups:
                steps[group.folder] = self.print_steps(group)
            self.write_readme(steps, made)
        except Exception as exc:
            ended = f"fault: {type(exc).__name__}: {str(exc)[:300]}"
            raise
        finally:
            self.ledger.change(
                "UPDATE refinements SET ended=?, ended_at=? WHERE id=?", ended, now(), self.id
            )
        return Outcome(self.id, self.n, self.out, self.found, made, steps)

    # Meshing, roles and plates

    def mesh(self, files: list[File]) -> None:
        """Each file as an STL, by the bench's image at Orca's own STEP defaults (the seam)."""
        work = self.out / "meshing"
        work.mkdir(parents=True, exist_ok=True)
        names = []
        for f in files:
            target = work / f"{f.folder}__{f.name}"
            shutil.copyfile(f.path, target)
            names.append(target.name)
        ran = self.box.helper(
            "refine",
            [
                json.dumps(
                    {
                        "request": "mesh",
                        "files": names,
                        "out": "meshed",
                        "linear": LINEAR,
                        "angular": ANGULAR,
                    }
                )
            ],
            timeout=600,
            where=work,
        )
        answer = _bench_answer(ran.output)
        for f, name in zip(files, names, strict=True):
            row = answer["files"][name]
            if "unread" in row:
                raise RuntimeError(f"{f.folder}/{f.name} couldn't be meshed: {row['unread']}")
            row["stl_path"] = str(work / row["stl"])
            self.meshed[self._key(f)] = row
            self._mesh_finding(f, row)

    @staticmethod
    def _key(f: File) -> str:
        return f"{f.folder}/{f.name}"

    def _mesh_finding(self, f: File, row: dict[str, Any]) -> None:
        problems = []
        if not row.get("closed"):
            problems.append("the mesh isn't closed")
        occt = row.get("occt_volume_mm3")
        if occt is not None:
            share = abs(row["mesh_volume_mm3"] - occt) / max(abs(occt), 1e-9)
            if share > MESH_SHARE:
                problems.append(f"its volume is {row['mesh_volume_mm3']} mm³ to OCCT's {occt}")
            box = np.asarray(row["occt_box_mm"]) - np.asarray(row["mesh_box_mm"])
            if float(np.max(np.abs(box))) > MESH_BOX:
                problems.append(f"its box is {row['mesh_box_mm']} to OCCT's {row['occt_box_mm']}")
        if problems:
            self.found.append(
                Finding(
                    "mesh",
                    "pass",
                    f"meshed at {row['linear_mm']} mm / {row['angular_rad']} rad, "
                    f"{row['triangles']} triangles: " + "; ".join(problems),
                    "Orca slices the mesh, not the file: where they differ, so does the print",
                    file=self._key(f),
                    numbers={k: v for k, v in row.items() if k != "outline"},
                )
            )

    def plates(self, group: Group) -> list[Plate]:
        self.mesh(group.files)
        rows = {self._key(f): self.meshed[self._key(f)] for f in group.files}
        frame = max(group.files, key=lambda f: rows[self._key(f)]["mesh_volume_mm3"])
        (fx0, fy0, fz0), (fx1, fy1, fz1) = rows[self._key(frame)]["mesh_box_mm"]
        panes: list[File] = []
        others: list[File] = []
        for f in group.files:
            if f is frame:
                continue
            (x0, y0, z0), (x1, y1, z1) = rows[self._key(f)]["mesh_box_mm"]
            within = (
                x0 >= fx0 - 0.01
                and y0 >= fy0 - 0.01
                and x1 <= fx1 + 0.01
                and y1 <= fy1 + 0.01
                and z0 > fz0 + 0.01
                and z1 <= fz1 + 0.01
            )
            (panes if within else others).append(f)
        plates = [
            Plate(f"{group.folder}-frame", group.folder, [frame], [frame.colour], frame, panes)
        ]
        by_colour: dict[str, list[File]] = {}
        for f in panes + others:
            by_colour.setdefault(f.colour, []).append(f)
        for colour, files in by_colour.items():
            name = f"{group.folder}-{colour or 'no-colour'}".replace(" ", "-")
            plates.append(Plate(name, group.folder, files, [colour]))
        self._pause(plates[0], group)
        return plates

    # The pause

    def _pause(self, plate: Plate, group: Group) -> None:
        """The bench's `pause` on the frame, at the process's layers, for each height of pane."""
        assert plate.frame is not None
        process = self.context.composed([plate.frame.colour], self.out / "pause-profiles")[
            "process"
        ]
        first, height = layers_of(process)
        asked: list[tuple[float, float, str]] = []
        seen: set[tuple[float, float]] = set()
        for pane in plate.panes:
            (_, _, z0), (_, _, z1) = self.meshed[self._key(pane)]["mesh_box_mm"]
            key = (round((z0 + z1) / 2, 4), round(z1 - z0, 4))
            if key not in seen:
                seen.add(key)
                asked.append((*key, f"`{pane.name}` as it sits"))
        if not asked:
            calls = [c for c in self.approved.pause_calls if pause_inputs(c)]
            if calls:
                call = calls[-1]
                got = pause_inputs(call) or {}
                asked.append(
                    (
                        got["pocket_z"],
                        got["pane_thickness"],
                        f"the run's own last `pause` call (call {call['at_call']}), as no pane "
                        "file "
                        "is in the folder",
                    )
                )
        if not asked:
            self.found.append(
                Finding(
                    "pause",
                    "pass",
                    "no pane sits in the frame, and the run never ran `pause`",
                    "nothing says where a pane goes, so no pause is set: if one is set in at a "
                    "pause, the pass can't say when",
                    plate=plate.name,
                    file=self._key(plate.frame),
                )
            )
            return
        work = self.out / "bench" / plate.name
        work.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plate.frame.path, work / plate.frame.name)
        layers_at: dict[int, dict[str, Any]] = {}
        for pocket_z, thickness, whence in asked:
            argv = [
                "--out",
                "out",
                "pause",
                plate.frame.name,
                "--pocket-z",
                f"{pocket_z:g}",
                "--pane-thickness",
                f"{thickness:g}",
                "--layer",
                f"{height:g}",
                "--first-layer",
                f"{first:g}",
            ]
            ran = self.box.bench(argv, where=work)
            record = recorded(ran.output, work) or {}
            verdict = str(record.get("verdict") or "")
            why = str(record.get("why") or ran.output.strip().splitlines()[-1:] or "")
            rows = (record.get("numbers") or {}).get("openings") or []
            closing = [r for r in rows if r.get("pause_after_layer") is not None]
            numbers = {
                "argv": ["pause", *argv[2:]],
                "verdict": verdict,
                "openings": rows,
                "from": whence,
                "layer": height,
                "first_layer": first,
            }
            room = [
                r.get("room_over_pane_mm")
                for r in closing
                if r.get("room_over_pane_mm") is not None
            ]
            hits = any(r < 0 for r in room)
            self.found.append(
                Finding(
                    "pause",
                    "bench",
                    f"`pause` at {first:g} then {height:g} mm layers, the pocket at z {pocket_z:g} "
                    f"and a {thickness:g} mm pane ({whence}): {verdict}: {why}",
                    (
                        "the pane stands above the last layer before the pocket closes: the "
                        "nozzle would hit it. The part has to change for this pause to work"
                        if hits
                        else "the layer to pause after, by the part's own heights"
                        if verdict == "OK"
                        else "no pause the bench can stand behind: the print steps don't give one"
                    ),
                    needs_change=hits,
                    plate=plate.name,
                    file=self._key(plate.frame),
                    layer_from=closing[0]["pause_after_layer"] if closing else None,
                    z_from=closing[0].get("pause_top_z") if closing else None,
                    numbers=numbers,
                )
            )
            if verdict not in ("OK", "FAIL") or not closing:
                continue
            for row in closing:
                after = int(row["pause_after_layer"])
                layers_at.setdefault(
                    after, {**row, "pocket_z": pocket_z, "pane_thickness": thickness}
                )
        for after, row in sorted(layers_at.items()):
            # Orca runs a pause item before the layer whose top it names: after `after` layers.
            plate.pauses.append(
                PauseAt(
                    print_z=round(first + height * after, 4),
                    message=f"Set the panes in: paused after layer {after}",
                )
            )
            plate.pause_rows.append({"after": after, **row})

    # Slicing and reading

    def slice(self, plate: Plate) -> None:
        where = self.out / "plates" / plate.name
        composed = self.context.composed(plate.colours, where)
        objects = [
            PlateObject(
                stl=Path(self.meshed[self._key(f)]["stl_path"]),
                filament=plate.colours.index(f.colour) + 1,
            )
            for f in plate.files
        ]
        done = self.slicer(
            objects,
            composed["machine"],
            composed["process"],
            composed["filaments"],
            where,
            pauses=plate.pauses or None,
        )
        log = done.log.read_text(encoding="utf-8", errors="replace") if done.log.is_file() else ""
        warned = gcode.warnings(log)
        files = [self._key(f) for f in plate.files]
        for w in warned:
            self.found.append(
                Finding(
                    "orca_warning",
                    "orca",
                    f"Orca: {w.text}",
                    "Orca's own warning, in its words: it sliced, and says this may print badly"
                    if done.exit == 0
                    else "Orca's reason for not slicing this plate",
                    plate=plate.name,
                    numbers={"level": w.level, "files": files},
                )
            )
        row: dict[str, Any] = {
            "refinement": self.id,
            "plate": plate.name,
            "files": json.dumps(files),
            "filaments": json.dumps(composed["used"], sort_keys=True),
            "pauses": json.dumps([p.model_dump() for p in plate.pauses]) if plate.pauses else None,
            "command": json.dumps(done.command),
            "exit": done.exit,
            "warnings": json.dumps([w.__dict__ for w in warned]),
        }
        if done.gcode is None:
            self.found.append(
                Finding(
                    "orca_refused",
                    "orca",
                    f"Orca didn't slice this plate: {done.why}",
                    "no G-code, so nothing more of this plate can be read, and no print steps",
                    plate=plate.name,
                    numbers={"files": files, "exit": done.exit},
                )
            )
            if self.recording:
                self.ledger.add("refine_slices", **row)
            return
        kept = where / f"{plate.name}.gcode"
        shutil.move(str(done.gcode), kept)
        sliced = gcode.read(kept, keep_lines=True)
        self.sliced[plate.name] = (plate, sliced)
        names = [u["filament"] for u in composed["used"]]
        self.found.append(fnd.figures(sliced, plate.name, plate.colours, names))
        frame_key = self._key(plate.frame) if plate.frame else files[0]
        self.found += fnd.over_air(
            sliced, plate.name, frame_key if len(files) == 1 else ", ".join(files)
        )
        self.found += fnd.narrow_lines(sliced, plate.name, ", ".join(files), self.context.nozzle_mm)
        if plate.frame is not None:
            self._read_pause(plate, sliced)
        for f in plate.files:
            self._cover(plate, f, sliced, where)
        row.update(
            gcode=str(kept),
            gcode_sha=hashlib.sha256(kept.read_bytes()).hexdigest(),
            layers=len(sliced.layers),
            seconds=sliced.time_s,
            filament=json.dumps(
                {
                    "g": sliced.per_filament("filament used [g]"),
                    "mm": sliced.per_filament("filament used [mm]"),
                }
            ),
        )
        if self.recording:
            self.ledger.add("refine_slices", **row)

    def _offset(self, f: File, sliced: gcode.Sliced) -> tuple[float, float, float] | None:
        """Where Orca put the file: its outline on the plate against the mesh's box."""
        stem = Path(self.meshed[self._key(f)]["stl_path"]).name
        outline = next((v for k, v in sliced.outlines.items() if k.startswith(stem + "_id_")), None)
        if outline is None:
            return None
        (x0, y0, z0), _ = self.meshed[self._key(f)]["mesh_box_mm"]
        return (
            min(p[0] for p in outline) - x0,
            min(p[1] for p in outline) - y0,
            -z0,
        )

    def _object(self, f: File, sliced: gcode.Sliced) -> str:
        stem = Path(self.meshed[self._key(f)]["stl_path"]).name
        return next((k for k in sliced.outlines if k.startswith(stem + "_id_")), "")

    def _read_pause(self, plate: Plate, sliced: gcode.Sliced) -> None:
        """Where Orca wrote the pause, against the bench's layer, and each pane under Orca's
        lines: what Orca prints in its footprint before the pause, and its first layer over it."""
        assert plate.frame is not None
        placed = [p["before_layer"] - 1 for p in sliced.pauses]
        wanted = [r["after"] for r in plate.pause_rows]
        if plate.pauses:
            agree = sorted(placed) == sorted(wanted)
            self.found.append(
                Finding(
                    "pause_placed",
                    "orca",
                    f"Orca wrote its pause after layer(s) {placed or 'none'} (before z "
                    f"{', '.join(f'{p["z"]:g}' for p in sliced.pauses) or '-'}); the bench's "
                    f"`pause` gives after layer(s) {wanted}",
                    "the two agree: the pause is where the part's heights put it"
                    if agree
                    else "they disagree: Orca's layers aren't the bench's, so the pause isn't "
                    "where the bench read it. Both readings are here",
                    needs_change=False,
                    plate=plate.name,
                    file=self._key(plate.frame),
                    layer_from=placed[0] if placed else None,
                    z_from=sliced.pauses[0]["after_z"] if sliced.pauses else None,
                    numbers={
                        "orca_after_layers": placed,
                        "bench_after_layers": wanted,
                        "agree": agree,
                    },
                )
            )
        offset = self._offset(plate.frame, sliced)
        if offset is None or not plate.panes:
            return
        dx, dy, dz = offset
        obj = self._object(plate.frame, sliced)
        pause_layer = min(placed) + 1 if placed else None
        alike: dict[
            tuple[int, float, tuple[str, ...]], list[tuple[File, dict[str, float], float, float]]
        ] = {}
        for pane in plate.panes:
            row = self.meshed[self._key(pane)]
            (_, _, z0), (_, _, z1) = row["mesh_box_mm"]
            floor, top = z0 + dz, z1 + dz
            rings = fnd.shifted(row["outline"], dx, dy)
            early: list[int] = []
            first_over: tuple[gcode.Layer, dict[str, float]] | None = None
            for layer in sliced.layers:
                lines = [ln for ln in layer.lines if ln.obj == obj]
                hit = fnd.inside(fnd.midpoints(lines), rings)
                if not hit.any():
                    continue
                if layer.bottom >= floor - 1e-3 and (pause_layer is None or layer.n < pause_layer):
                    early.append(layer.n)
                if (
                    layer.bottom >= floor - 1e-3
                    and first_over is None
                    and (pause_layer is None or layer.n >= pause_layer)
                ):
                    labels: dict[str, float] = {}
                    for ln, h in zip(lines, hit, strict=True):
                        if h:
                            labels[ln.kind] = labels.get(ln.kind, 0.0) + ln.length
                    first_over = (layer, {k: round(v, 1) for k, v in labels.items()})
            if early:
                runs = fnd.runs_of(early)
                self.found.append(
                    Finding(
                        "closes_early",
                        "pass",
                        f"Orca lays lines inside `{pane.name}`'s outline above its floor (z "
                        f"{floor:g}) before the pause, on layers {runs}",
                        "the pocket is printed over before the pane can go in"
                        if pause_layer
                        else "with no pause, the pocket is printed over with nothing in it: the "
                        "pane can't be set in",
                        needs_change=pause_layer is not None,
                        plate=plate.name,
                        file=self._key(pane),
                        layer_from=early[0],
                        layer_to=early[-1],
                        numbers={"layers": early},
                    )
                )
            if first_over is None or pause_layer is None:
                continue
            layer, labels = first_over
            room = round(layer.bottom - top, 4)
            # Panes alike are one finding: rings' 17 panes read the same.
            key = (layer.n, room, tuple(sorted(labels)))
            alike.setdefault(key, []).append((pane, labels, top, floor))
        expected = min(placed) + 1 if placed else None
        for (n, room, _), panes in alike.items():
            layer = sliced.layers[n - 1]
            labels = panes[0][1]
            said = ", ".join(f"{k} {v:g} mm" for k, v in labels.items())
            air = {k: v for k, v in labels.items() if k in fnd.OVER_AIR}
            means = []
            if room < -1e-3:
                means.append(f"the nozzle lays it {-room:g} mm into the pane: it would hit it")
            else:
                means.append(f"it's laid {room:g} mm above the pane's top")
            if expected is not None and n > expected:
                ceiling = next(
                    (
                        r.get("pocket_ceiling_z")
                        for r in plate.pause_rows
                        if r.get("pocket_ceiling_z")
                    ),
                    None,
                )
                means.append(
                    f"Orca closes the pocket {n - expected} layer(s) after the pause, where the "
                    f"bench reads it closing at layer {expected}"
                    + (
                        f" (the ceiling, z {ceiling:g}, is on a layer's mid-height, which a slicer "
                        "may read either way)"
                        if ceiling is not None
                        else ""
                    )
                    + ": the pane rattles in that room, and the layer over it bridges that high"
                )
            if air:
                means.append(
                    "Orca reads it as laid over air (" + ", ".join(air) + "), as it doesn't know "
                    "the pane is there: bridge speed, flow and fan, over the pane"
                )
            names = ", ".join(f"`{p.name}`" for p, *_ in panes)
            self.found.append(
                Finding(
                    "over_pane",
                    "orca",
                    f"Orca's first layer over {names} after the pause is layer {n} "
                    f"(z {layer.bottom:g}-{layer.z:g})"
                    + (f", over each: {said}" if len(panes) > 1 else f": {said}"),
                    "; ".join(means),
                    needs_change=room < -1e-3,
                    plate=plate.name,
                    file=", ".join(self._key(p) for p, *_ in panes),
                    layer_from=n,
                    z_from=layer.z,
                    numbers={
                        "labels_mm": labels,
                        "room_mm": room,
                        "panes": [self._key(p) for p, *_ in panes],
                        "pane_top_z": round(panes[0][2], 4),
                        "pane_floor_z": round(panes[0][3], 4),
                        "bench_closes_at": expected,
                    },
                )
            )

    def _cover(self, plate: Plate, f: File, sliced: gcode.Sliced, where: Path) -> None:
        """The pass's own reading: where the part has no line, and walls laid over each other."""
        offset = self._offset(f, sliced)
        if offset is None:
            return
        obj = self._object(f, sliced)
        layers = []
        for layer in sliced.layers[:READ_LIMIT]:
            lines = [
                [ln.x0, ln.y0, ln.x1, ln.y1, ln.width, 1 if ln.kind in fnd.WALLS else 0]
                for ln in layer.lines
                if ln.obj == obj and ln.width > 0
            ]
            layers.append({"n": layer.n, "z": layer.z, "height": layer.height, "lines": lines})
        work = self.out / "cover" / plate.name
        work.mkdir(parents=True, exist_ok=True)
        stl = Path(self.meshed[self._key(f)]["stl_path"])
        shutil.copyfile(stl, work / stl.name)
        (work / "lines.json").write_text(json.dumps({"layers": layers}), encoding="utf-8")
        ran = self.box.helper(
            "refine",
            [
                json.dumps(
                    {
                        "request": "cover",
                        "stl": stl.name,
                        "offset": list(offset),
                        "lines": "lines.json",
                    }
                )
            ],
            timeout=600,
            where=work,
        )
        (work / "lines.json").unlink()
        answer = _bench_answer(ran.output)
        (work / "cover.json").write_text(json.dumps(answer, indent=1), encoding="utf-8")
        self.found += fnd.bare(answer, plate.name, self._key(f))
        self.found += fnd.overlapping(answer, plate.name, self._key(f))

    def check_frame(
        self, path: Path, frame: File, folder: Path, reading: dict[str, Any]
    ) -> dict[str, Any]:
        """A proposal's frame, as the pass reads the approved one: meshed, `pause` on it with the
        panes as they sit, and Orca's slice with that pause. Its findings are the proposal's, not
        the pass's, and none reaches the ledger as the approved folder's."""
        label = folder.name
        group = next(g for g in self.approved.groups if frame in g.files)
        changed = File(
            label,
            frame.name,
            frame.was,
            path,
            content_hash(frame.was, path.read_bytes()),
            frame.colour,
            frame.label,
        )
        others = [
            File(label, f.name, f.was, f.path, f.sha256, f.colour, f.label)
            for f in group.files
            if f is not frame
        ]
        saved = (self.out, self.found, self.recording)
        self.out, self.found, self.recording = folder, [], False
        try:
            folder.mkdir(parents=True, exist_ok=True)
            plates = self.plates(Group(label, group.name, [changed, *others]))
            self.slice(plates[0])
            found = self.found
        finally:
            self.out, self.found, self.recording = saved
        pause = next((f for f in found if f.kind == "pause" and f.source == "bench"), None)
        placed = next((f for f in found if f.kind == "pause_placed"), None)
        over = [f for f in found if f.kind == "over_pane"]
        rooms = [float(f.numbers["room_mm"]) for f in over if f.numbers.get("room_mm") is not None]
        verdict = (pause.numbers.get("verdict") if pause else None) or "none"
        result = {
            "verdict": verdict,
            "bench_after": pause.layer_from if pause else None,
            "orca_after": (placed.numbers.get("orca_after_layers") if placed else None),
            "agree": placed.numbers.get("agree") if placed else None,
            "room": min(float(r) for r in rooms) if rooms else None,
            "findings": [f.plain() for f in found],
        }
        result["pause_ok"] = bool(
            verdict == "OK" and result["agree"] and not any(f.needs_change for f in found)
        )
        (folder / "check.json").write_text(
            json.dumps(result, indent=1, default=str), encoding="utf-8"
        )
        return result

    # Records

    def record_findings(self) -> list[int]:
        ids = []
        for seq, f in enumerate(self.found, 1):
            ids.append(
                self.ledger.insert(
                    "refine_findings",
                    refinement=self.id,
                    seq=seq,
                    kind=f.kind,
                    source=f.source,
                    plate=f.plate,
                    file=f.file,
                    layer_from=f.layer_from,
                    layer_to=f.layer_to,
                    z_from=f.z_from,
                    z_to=f.z_to,
                    read=f.read,
                    means=f.means,
                    needs_change=int(f.needs_change),
                    numbers=json.dumps(f.numbers, sort_keys=True, default=str),
                )
            )
        for group in self.approved.groups:
            for one in group.files:
                row = self.meshed.get(self._key(one), {})
                role = "frame" if any(p.frame is one for p, _ in self.sliced.values()) else "file"
                if role == "file" and any(one in p.panes for p, _ in self.sliced.values()):
                    role = "pane"
                self.ledger.add(
                    "refine_files",
                    refinement=self.id,
                    folder=one.folder,
                    file=one.name,
                    sha256=one.sha256,
                    role=role,
                    colour=one.colour,
                    label=one.label,
                    meshed=json.dumps(
                        {k: v for k, v in row.items() if k not in ("outline", "stl_path")},
                        sort_keys=True,
                    ),
                )
        (self.out / "findings.json").write_text(
            json.dumps([f.plain() for f in self.found], indent=1, default=str), encoding="utf-8"
        )
        return ids

    def to_lessons(self, ids: list[int]) -> list[int]:
        """A candidate lesson for each finding the bench failed or that needs the part to change,
        for the design rounds, where the part is drawn (mqucifer/print-shop#59). On `eval-rings-3`
        `pause` failed on 0.5 mm of air over the pane, which the Sponsor calls an unacceptable
        overhang, and the pass needed no change and proposed nothing, so it reached no one. Each
        is a candidate like any lesson: a reviewer lets it through, or not."""
        made = []
        for finding, finding_id in zip(self.found, ids, strict=True):
            verdict = str(finding.numbers.get("verdict") or "")
            failed = finding.source == "bench" and verdict == "FAIL"
            if not (finding.needs_change or failed):
                continue
            argv = finding.numbers.get("argv") or []
            command = str(argv[0]) if argv else finding.kind
            text = f"{finding.read} ({finding.where()}). For the print: {finding.means}."
            made.append(
                lessons_module.write(
                    self.ledger,
                    self.approved.run,
                    written_by=REFINED_BY,
                    fix_shape=f"a refinement pass's finding, after approval ({finding.kind})",
                    kind="verify",
                    text=text,
                    held={"findings": [finding_id], "refinement": self.n},
                    routes=["model"],
                    keys=[{"command": command, "verdict": verdict}],
                    part_kind=self.part_kind,
                )
            )
        return made

    def print_steps(self, group: Group) -> list[str]:
        """The pass's own print steps, from Orca's slices, when no finding needs a change."""
        changes = [
            f for f in self.found if f.needs_change and (f.plate or "").startswith(group.folder)
        ]
        refused = [
            f
            for f in self.found
            if f.kind == "orca_refused" and (f.plate or "").startswith(group.folder)
        ]
        if changes or refused:
            return []
        steps: list[str] = []
        plates = [(p, s) for p, s in self.sliced.values() if p.group == group.folder]
        for plate, sliced in sorted(plates, key=lambda x: x[0].frame is not None):
            minutes = round((sliced.time_s or 0) / 60)
            grams = sum(sliced.per_filament("filament used [g]"))
            names = ", ".join(f"`{f.name}`" for f in plate.files)
            colour = plate.colours[0] or "no colour given"
            gfile = f"refinements/{self.n}/plates/{plate.name}/{plate.name}.gcode"
            if plate.frame is None:
                steps.append(
                    f"Print `{gfile}`: {names} in {colour}, about {minutes} min and {grams:g} g "
                    "by Orca's estimate."
                )
                continue
            line = (
                f"Print `{gfile}`: {names} in {colour}, about {minutes} min and {grams:g} g by "
                "Orca's estimate."
            )
            if sliced.pauses and plate.panes:
                p = sliced.pauses[0]
                over = [f for f in self.found if f.kind == "over_pane" and f.plate == plate.name]
                rooms = [float(f.numbers["room_mm"]) for f in over if "room_mm" in f.numbers]
                panes = ", ".join(f"`{f.name}` ({f.colour or 'no colour'})" for f in plate.panes)
                line += (
                    f" Orca pauses after layer {p['before_layer'] - 1} (z {p['after_z']:g}), "
                    f"before layer {p['before_layer']}: set {panes} in their pockets, each on its "
                    "floor, and resume."
                )
                if rooms:
                    line += (
                        f" The first layer over them is laid {min(rooms):g} mm above the panes' "
                        "tops, by Orca's lines."
                    )
            elif sliced.pauses:
                p = sliced.pauses[0]
                line += (
                    f" Orca pauses after layer {p['before_layer'] - 1} (z {p['after_z']:g}): set "
                    "the panes in, and resume."
                )
            steps.append(line)
        return steps

    def write_readme(self, steps: dict[str, list[str]], made: list[dict[str, Any]]) -> None:
        lines = [
            f"# {self.approved.run}: refinement {self.n}",
            "",
            f"After approval {self.approved.approval} (decision {self.approved.decision}), the "
            f"approved folder sliced by {self.orca or 'Orca'} at {self.commit[:12] or 'unknown'}. "
            "Nothing in the approved folder's files was changed. Each plate's G-code is Orca's "
            "own, with the pause and colours Orca wrote itself.",
            "",
            f"Context: a {self.context.nozzle_mm:g} mm nozzle; filaments "
            + ", ".join(f"{k or 'every colour'} = {v}" for k, v in self.context.filaments.items())
            + ".",
            "",
        ]
        for folder, said in steps.items():
            lines += [f"## Print steps: `{folder}`", ""]
            if said:
                lines += [f"{k}. {s}" for k, s in enumerate(said, 1)]
            else:
                lines.append(
                    "None: a finding below needs the part to change, or a plate didn't slice. "
                    "A proposal waits for approval."
                )
            lines.append("")
        lines += [
            "## Findings",
            "",
            "| # | Kind | Whose | Where | Read | Means | Change |",
            "|---|---|---|---|---|---|---|",
        ]
        for k, f in enumerate(self.found, 1):
            lines.append(
                f"| {k} | {f.kind} | {f.source} | {f.where()} | {_cell(f.read)} | "
                f"{_cell(f.means)} | {'needed' if f.needs_change else ''} |"
            )
        lines += ["", "## Proposals", ""]
        if not made:
            lines.append("None.")
        for p in made:
            lines += [
                f"- **Proposal {p['id']}** (finding {p['finding_seq']}): {p['change']}. Cost: "
                f"{p['cost']}. {p['checked_said']} It waits for the Sponsor's approval: "
                f"`scripts/rounds.py proposal {self.approved.run} {p['id']} --approve|--reject "
                "--by WHO --note FILE`.",
            ]
        (self.out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._into_review(steps)

    def _into_review(self, steps: dict[str, list[str]]) -> None:
        """The run's `review/README.md` takes the pass's print steps, beside the model's words,
        which stay marked as the model's. Run again, the section is replaced, not added to."""
        readme = self.approved.folder / "review" / "README.md"
        if not readme.is_file():
            return
        mark_start = "<!-- refinement: print steps -->"
        mark_end = "<!-- refinement: end -->"
        text = readme.read_text(encoding="utf-8")
        while mark_start in text and mark_end in text:
            before, _, rest = text.partition(mark_start)
            _, _, after = rest.partition(mark_end + "\n")
            text = before + after
        for folder, said in steps.items():
            heading = f"## {folder}\n\n"
            if heading not in text:
                continue
            block = [
                mark_start,
                f"**Print steps, from the refinement pass ({self.n}), from Orca's "
                f"slice** (`refinements/{self.n}/README.md` has its findings):",
                "",
            ]
            block += [f"{k}. {s}" for k, s in enumerate(said, 1)] or [
                "None yet: a finding needs the part to change, and a proposal waits for approval."
            ]
            block += [
                "",
                "The model's words below are its own, from before the pass:",
                "",
                mark_end,
                "",
            ]
            text = text.replace(heading, heading + "\n".join(block) + "\n", 1)
        readme.write_text(text, encoding="utf-8")


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")
