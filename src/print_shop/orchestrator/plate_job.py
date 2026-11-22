"""What a job of several parts adds to the graph's steps: the parts, read together and apart.

mqucifer/print-shop#109. The job is one review, one gate and one print. Each part keeps its own
pose, features, context and ledger row. One process is sliced for the plate, so the proposal
reads the parts together: each feature at the most demanding part's value, with every part's
beside it, the fits of all of them, and the lessons kept for any of them.

Nothing here decides or prints: the graph's nodes call these.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from pathlib import Path
from typing import Any

from print_shop.core.contracts import Outcome
from print_shop.core.state import AppliedOrientation, Orientation, PlatePart, PrintJobState
from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import write_rotated
from print_shop.inventory import Material
from print_shop.ledger import PrintRecord
from print_shop.plate import BY_OBJECT, SEQUENCE, GivenPart, PlateRow, row_id
from print_shop.setup import Proposer
from print_shop.setup.context import SIDECAR, PartContext, Placed, Seen, place
from print_shop.setup.features import Feature, measure
from print_shop.setup.intake import Asker, Intake, OfPlate, Question
from print_shop.setup.proposal import ORIENTATION, Change, GivenLesson, Proposal, Source, pose_value
from print_shop.setup.questions import PLATE_WORDS, SEEN
from print_shop.setup.skill import WARPS_MM2
from print_shop.setup.traits import traits
from print_shop.slicer.arrange import ToPlace, object_file
from print_shop.slicer.orca import SlicerProfiles

__all__ = [
    "Together", "asked_about", "by_object", "keep_sidecar", "planned", "poses_said", "posed",
    "records", "smallest", "still_holds", "take", "together",
]

#: The features whose least value is the plate's risk. Every other's is the most.
_LEAST = frozenset({"bed_contact", "section"})
#: The features that are only true together: read from one part, or they make a part that isn't.
_OUTLINE = frozenset({"footprint", "length", "footprint_aspect"})


def take(given: tuple[str, ...], folder: Path) -> tuple[PlatePart, ...]:
    """The job's own copy of each file, measured. The copies are what's read from here on.

    Raises:
        RuntimeError: If a part's mesh isn't closed. A job of one part stops at the mesh gate for
            that; one of several is refused, as that gate shows one mesh.
    """
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    for n, path in enumerate(given, 1):
        source = Path(path)
        kept = folder / Path(object_file(n, source.name)).with_suffix(source.suffix.lower())
        shutil.copyfile(source, kept)
        mesh = analyze(kept)
        if not mesh.is_manifold:
            raise RuntimeError(
                f"part {n}, {source.name}, isn't a closed mesh, so it can't be trusted to slice. "
                "Nothing was sliced: mend it, or leave it out"
            )
        parts.append(PlatePart(n=n, name=source.name, given=str(source), stl=str(kept),
                               sha256=mesh.stl_sha256, mesh=mesh))
    return tuple(parts)


def keep_sidecar(part: PlatePart) -> None:
    """The part's sidecar, copied beside the job's copy of it, so that copy printed again reads
    the same context. A design run's isn't a file to copy: the desk says where it was."""
    context = part.context
    if context is not None and context.path and Path(context.path).is_file():
        kept = Path(part.stl)
        shutil.copyfile(context.path, kept.with_name(kept.stem + SIDECAR))


def posed(
    parts: tuple[PlatePart, ...], orientations: tuple[Outcome[Orientation], ...], folder: Path,
) -> tuple[tuple[PlatePart, ...], tuple[ToPlace, ...]]:
    """Each part in its accepted pose, with its features there, and its file to slice."""
    turned, files = [], []
    for part, outcome in zip(parts, orientations, strict=True):
        chosen = outcome.proposal.value
        out = folder / pose_value(chosen.rotation_deg).replace(",", "_") / object_file(part.n,
                                                                                         part.name)
        if chosen.rotation_deg == (0.0, 0.0, 0.0) and Path(part.stl).suffix.lower() == ".stl":
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(part.stl, out)
        else:
            write_rotated(part.stl, chosen.rotation_deg, out)
        turned.append(part.model_copy(update={
            "applied": AppliedOrientation(
                rotation_deg=chosen.rotation_deg, rationale=outcome.proposal.rationale,
                height_mm=chosen.height_mm, bed_contact_area_mm2=chosen.bed_contact_area_mm2,
            ),
            "features": measure(out, chosen.bed_contact_area_mm2),
        }))
        files.append(ToPlace(part.n, part.name, out, chosen.height_mm))
    return tuple(turned), tuple(files)


#: The questions an answer to which holds however the part is turned.
_ANY_POSE = frozenset({"purpose", "volume", "section"})
#: The settings that are for a pose, whichever question's option set them.
_FOR_A_POSE = re.compile(
    r"support|bridge|overhang|ironing|seam|brim|raft|elefant|^top_|^bottom_|initial_layer"
)


def asked_about(asker: Asker, parts: tuple[PlatePart, ...]) -> Asker:
    """`asker`, showing the questions as about these parts in the poses they lie in now: "top"
    means that, and a part can still be turned after they're answered."""
    turns = [(p, p.applied.rotation_deg if p.applied else (0.0, 0.0, 0.0)) for p in parts]
    if any(any(turn) for _, turn in turns):
        posed = "top and bottom as each lies now (" + "; ".join(
            f"part {p.n} " + (f"turned {pose_value(turn)}" if any(turn) else "as in its file")
            for p, turn in turns) + ")"
    else:
        posed = "top and bottom as each part lies in its file"

    def ask(questions: Any) -> Any:
        return asker(OfPlate(questions, posed, PLATE_WORDS) if questions else questions)

    return ask


def _as_shown(text: str) -> str:
    """A question's or an option's words as a plate's questions showed them."""
    for said, theirs in PLATE_WORDS.items():
        text = text.replace(said, theirs)
    return text


def still_holds(
    intake: Intake, parts: tuple[PlatePart, ...], asked_in: tuple[tuple[float, float, float], ...],
) -> tuple[Intake, str]:
    """The answers as this plate's reading and settings may use them, and what the review says
    of those they may not.

    An answer was given of the parts as they lay when it was asked. Where one has been turned
    since, what's for a pose isn't carried across: which faces are seen, the questions on
    features a turn moves, and any option's setting that's for a pose, such as no supports. The
    intake's record keeps every answer as it was given. Turned back, they hold again. Carrying
    them through a turn, for one part too, is mqucifer/print-shop#248's.
    """
    moved = [
        (part, part.applied.rotation_deg)
        for part, was in zip(parts, asked_in, strict=True)
        if part.applied is not None and tuple(part.applied.rotation_deg) != tuple(was)
    ]
    if not moved:
        return intake, ""
    questions: list[Question] = []
    answers = []
    dropped: list[str] = []
    for question, answer in zip(intake.questions, intake.answers, strict=True):
        option = question.options[answer.choice] if answer.choice is not None else None
        # As it was shown: "as it prints" would read as the pose it has been turned to. Their own
        # words are quoted as typed.
        label = _as_shown(option.label) if option is not None else answer.other
        if question.feature not in _ANY_POSE:
            what = ("which faces are seen" if question.feature == SEEN.feature
                    else f"'{_as_shown(question.text)}'")
            dropped.append(f"{what}, you said '{label}'")
            continue
        if option is not None and answer.choice is not None:
            held = {k: v for k, v in option.settings.items() if not _FOR_A_POSE.search(k)}
            if held != option.settings:
                lost = ", ".join(f"{k}={v}" for k, v in option.settings.items() if k not in held)
                dropped.append(f"{lost}, from '{label}'")
                options = list(question.options)
                options[answer.choice] = option.model_copy(update={"settings": held})
                question = question.model_copy(update={"options": tuple(options)})
        questions.append(question)
        answers.append(answer)
    if not dropped:
        return intake, ""
    who = "; ".join(f"part {part.n}, {part.name}, to {pose_value(turn)}" for part, turn in moved)
    note = (
        f"TURNED SINCE YOU ANSWERED: {who}. These were said of the parts as they lay when asked, "
        f"so they aren't judged or used now: {'; '.join(dropped)}. Send a part back with "
        "orientation, or say what you want with a correction"
    )
    held_to = intake.model_copy(update={"questions": tuple(questions), "answers": tuple(answers)})
    return held_to, note


def smallest(
    parts: tuple[PlatePart, ...], orientations: list[Outcome[Orientation]], undecided: list[int],
    build_volume: tuple[float, float, float],
) -> list[Outcome[Orientation]]:
    """The undecided parts each in the pose that takes least of the plate: whether any room can
    be made for them, before anything is asked."""
    chosen = list(orientations)
    for at in undecided:
        pose = min((c for c in parts[at].candidates if c.fits_in(build_volume)),
                   key=lambda c: c.bbox_mm[0] * c.bbox_mm[1])
        was = orientations[at]
        chosen[at] = dataclasses.replace(
            was, proposal=dataclasses.replace(was.proposal, value=pose))
    return chosen


@dataclasses.dataclass(frozen=True)
class Together:
    """The parts in their poses, as one review reads them."""

    features: tuple[Feature, ...]
    #: Every part's fits and holds, each named with its part. None where no part has any context.
    context: PartContext | None
    placed: Placed | None
    #: Each part's context, as it was read, in lines.
    said: tuple[str, ...]
    parts: tuple[PlatePart, ...]

    def proposer(self, proposer: Proposer) -> Proposer:
        """`proposer` given the lessons kept for any of these parts, each once."""
        kept = proposer.lessons
        if kept is None:
            return proposer

        def lessons(_: tuple[Feature, ...], filament_type: str) -> tuple[GivenLesson, ...]:
            found = {g.id: g for part in self.parts for g in kept(part.features, filament_type)}
            return tuple(found[i] for i in sorted(found))

        return dataclasses.replace(proposer, lessons=lessons)


def together(parts: tuple[PlatePart, ...]) -> Together:
    """The posed parts, read as one plate."""
    contexts: list[tuple[str, PartContext]] = []
    placings: list[tuple[str, Placed]] = []
    said: list[str] = []
    for part in parts:
        who = f"part {part.n}, {part.name}"
        context = part.context or PartContext()
        applied = part.applied or AppliedOrientation()
        lines = context.lines()
        if context.found:
            placed = place(context, part.stl, applied.rotation_deg)
            contexts.append((who, context))
            placings.append((who, placed))
            lines += placed.lines()
        said += [f"{who}: {line}" for line in lines]
    every = len(contexts) == len(parts)  # faces are only called hidden if every part says so
    return Together(
        features=_features(parts),
        context=_context(contexts, every) if contexts else None,
        placed=_placed(placings, every) if placings else None,
        said=tuple(said),
        parts=parts,
    )


def _features(parts: tuple[PlatePart, ...]) -> tuple[Feature, ...]:
    """Each feature at the part it's most demanding on, saying every part's."""
    found = []
    long_one = _longest(parts)
    for at, first in enumerate(parts[0].features):
        each = [(part.name, part.features[at]) for part in parts]
        least = first.id in _LEAST
        if first.id == "bed_contact":
            least = all(f.value < WARPS_MM2 for _, f in each)  # else the risk is the largest's
        measured = [pair for pair in each if pair[1].value > 0] if least else each
        pick = min if least else max
        name, chosen = pick(measured or each, key=lambda pair: pair[1].value)
        if first.id in _OUTLINE:
            name, chosen = long_one.name, long_one.features[at]
        every = ", ".join(f"{part_name} {f.value:g}" for part_name, f in each)
        found.append(chosen.model_copy(update={
            "says": f"{chosen.says}; this is {name}'s, of the plate's parts ({every})"}))
    return tuple(found)


def _longest(parts: tuple[PlatePart, ...]) -> PlatePart:
    """The part the plate's outline is read from: the longest of those long and narrow, if any,
    else the longest."""

    def length(part: PlatePart) -> float:
        return next((f.value for f in part.features if f.id == "length"), 0.0)

    narrow = [p for p in parts if "long_and_narrow" in traits(p.features)]
    return max(narrow or parts, key=length)


def _seen(each: list[Seen | None], every: bool) -> Seen | None:
    said = [s for s in each if s is not None]
    if not every or len(said) != len(each) or not said:
        return None
    return Seen(walls=any(s.walls for s in said), top=any(s.top for s in said),
                bottom=any(s.bottom for s in said),
                said="; ".join(dict.fromkeys(s.said for s in said)))


def _context(found: list[tuple[str, PartContext]], every: bool) -> PartContext:
    return PartContext(
        source="each part's own sidecar or design run",
        read=tuple(f"{who}: {r}" for who, c in found for r in c.read),
        fits=tuple(f.model_copy(update={"name": f"{who}: {f.name}"})
                   for who, c in found for f in c.fits),
        holds=tuple(h.model_copy(update={"name": f"{who}: {h.name}"})
                    for who, c in found for h in c.holds),
        seen=_seen([c.seen for _, c in found], every),
        facts=tuple(f"{who}: {f}" for who, c in found for f in c.facts),
        findings=tuple(f"{who}: {f}" for who, c in found for f in c.findings),
        notes=tuple(f"{who}: {n}" for who, c in found for n in c.notes),
    )


def _placed(found: list[tuple[str, Placed]], every: bool) -> Placed:
    # Heights are each part's own from the plate, which is where every part starts.
    return Placed(
        fits=tuple(
            b.model_copy(update={"fit": b.fit.model_copy(update={"name": f"{who}: {b.fit.name}"})})
            for who, p in found for b in p.fits),
        across=tuple(h.model_copy(update={"name": f"{who}: {h.name}"})
                     for who, p in found for h in p.across),
        seen=_seen([p.seen for _, p in found], every),
        off_side="; ".join(f"{who}: {p.off_side}" for who, p in found if p.off_side),
    )


def by_object(profiles: SlicerProfiles, folder: Path) -> SlicerProfiles:
    """`profiles` asking for by object: what's sliced before there's a proposal to say it."""
    process: dict[str, Any] = json.loads(profiles.process.read_text())
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "process_by_object.json"
    out.write_text(json.dumps({**process, SEQUENCE: BY_OBJECT}, indent=2))
    return profiles.model_copy(update={"process": out})


def planned(
    proposal: Proposal, process: dict[str, Any], count: int, *, without_loop: bool,
) -> Proposal:
    """The proposal with the plate's order among its changes, shown and corrected as any is."""
    changes, notes = proposal.changes, proposal.notes
    decided = {c.key for c in (*changes, *proposal.kept)}
    preset = str(process.get(SEQUENCE, ""))
    if SEQUENCE not in decided and preset != BY_OBJECT:
        changes += (Change(
            key=SEQUENCE, preset=preset, value=BY_OBJECT, source=Source.GUARD,
            reason=f"{count} parts in one job: each is finished before the next, so none strings "
                   "to another, where the head has the room",
        ),)
    if without_loop:
        notes += ("several parts: each pose came from the single orientation step and the "
                  "settings from the single call, as the print loop reads one part",)
    return proposal.model_copy(update={"changes": changes, "notes": notes})


def poses_said(parts: tuple[PlatePart, ...]) -> str:
    """Each part's pose, in a line for the gate."""
    said = []
    for part in parts:
        turn = part.applied.rotation_deg if part.applied else (0.0, 0.0, 0.0)
        how = "as the file lays it" if not any(turn) else f"turned {pose_value(turn)}"
        said.append(f"part {part.n} {part.name} {how}")
    return "; ".join(said)


def records(
    state: PrintJobState, remote_name: str, config: dict[str, str], machine: str, firmware: str,
    material: Material | None = None,
) -> tuple[PrintRecord, ...]:
    """The ledger's row for each object, the first's with the job's id. The review is every
    row's. Each has its own pose, features, send-backs and orientation lessons, and the same
    spool: `material`, as it is when the print starts (`Inventory.at_start`)."""
    job = state["job_id"]
    parts = tuple(state["parts"])
    orientations = tuple(state["orientations"])
    sliced = state["slice"]
    given = tuple(
        GivenPart(
            name=p.name, path=str(Path(p.given).resolve()), sha256=p.sha256,
            context=(str(Path(p.context.path).resolve())
                     if p.context is not None and p.context.path else ""),
        )
        for p in parts
    )
    reviewed = state.get("proposal")
    used = state.get("profiles_used")
    if used is not None:
        used = used.model_copy(update={"material": material})
    rows = []
    for part, outcome in zip(parts, orientations, strict=True):
        applied = part.applied or AppliedOrientation()
        proposal = reviewed
        if proposal is not None:
            own = {g.id for g in part.lessons}
            # And its own context, as one part's row has it: what a later part is matched on
            # (`similar.py`), where the plate's lines would each be another part's as well.
            context = part.context or PartContext()
            lines = context.lines()
            if context.found:
                lines += place(context, part.stl, applied.rotation_deg).lines()
            proposal = proposal.model_copy(update={
                "context": tuple(lines),
                "context_from": context.path,
                "lessons": tuple(
                    g for g in proposal.lessons if g.key != ORIENTATION or g.id in own),
            })
        rows.append(PrintRecord(
            job_id=row_id(job, part.n),
            stl_name=part.name,
            stl_sha256=part.sha256,
            rotation_deg=applied.rotation_deg,
            orientation_source=outcome.proposal.source,
            orientation_rationale=applied.rationale,
            orientation_accepted=outcome.accepted,
            gcode_name=remote_name,
            print_time_s=sliced.print_time_s,
            filament_cm3=sliced.filament_cm3,
            layer_count=sliced.layer_count,
            slicer_config=config,
            printer_machine=machine,
            printer_firmware=firmware,
            profiles=used,
            material=material,
            intake=state.get("intake"),
            proposal=proposal,
            corrections=(*part.sent_back, *state.get("corrections", ())),
            features=part.features,
            reading=state.get("slice_reading"),
            flags=tuple(state.get("slice_flags", ())),
            plate=PlateRow(job_id=job, n=part.n, parts=given, layout=state["plate"],
                           as_laid=bool(state.get("as_laid"))),
        ))
    return tuple(rows)
