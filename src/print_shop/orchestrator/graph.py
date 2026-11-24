"""LangGraph spine for the print pipeline.

Wires the pipeline nodes (ingest -> slice -> gate -> print) into a compiled
LangGraph state machine. The graph enforces the safety invariant from
CONTRACTS.md section 4: every inbound edge to a hardware node (``print``)
originates at a ``Gate`` node.

Topology::

    START                --cond--> ingest | sliced
    sliced               --> gate.before_print
    ingest               --cond--> orient | gate.review_proposal
    orient               --cond--> slice  | gate.review_proposal
    gate.review_proposal --cond--> slice  | END
    slice                --> gate.before_print
    gate.before_print    --cond--> print  | END
    print                --> END

**A job of several parts takes the same way** (mqucifer/print-shop#109): `ingest`, `orient` and
`slice` each do every part, and it's one review, one gate and one print, with a ledger row for
each object. It has no mesh gate, as a part whose mesh isn't closed is refused.

**A hand-sliced file takes the short way** (mqucifer/print-shop#164): `sliced` copies its bytes
into the job's folder and reads them, and goes straight to the same gate. Nothing between is
run for it: no ingest, orientation, question, proposal, lesson, slice or review.

**The slice node's review waits for no model** (mqucifer/print-shop#242). It starts from the
settings with the model absent, and the settings call or the print loop works on a thread of its
own (`setup/suggest.py`), ended with the node: what it comes to is proposed, and only the person
applies it. With no reviewer there's nobody to take a proposal, so the node waits as it did.

The graph is the single source of truth for control flow. ``graph_edges``
exposes the compiled edge list so ``ungated_edges`` (CONTRACTS.md section 4)
can be asserted against the real graph rather than a synthetic list.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import trimesh
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from print_shop import sliced as hand_sliced
from print_shop.core.contracts import Gate, GateDecision, Outcome, approved_at, propose_validate
from print_shop.core.contracts import Proposal as Chosen
from print_shop.core.state import (
    AppliedOrientation,
    EntryPath,
    Orientation,
    PlatePart,
    PrintJobState,
    PrintStatus,
    turn_words,
)
from print_shop.expert.client import LLMConfig
from print_shop.expert.orientation import (
    NO_MODEL_CONFIGURED,
    OrientationRequest,
    propose_geometric,
    propose_orientation,
    validate_orientation,
)
from print_shop.ingest.analyzer import analyze, verify_unchanged
from print_shop.ingest.measuring import Measuring
from print_shop.ingest.orientation import LAID, candidate_in, candidates, write_rotated
from print_shop.inventory import Asked, Inventory, InventoryError, Material, SpoolUse
from print_shop.joblog import SAID
from print_shop.ledger import Ledger, PrintRecord
from print_shop.orchestrator import plate_job
from print_shop.orchestrator.fitness import scoring_profiles
from print_shop.orchestrator.fitting import Fitting, with_turns
from print_shop.plate import PlateLayout
from print_shop.printer.centauri import PreflightFailed, PrinterClient, preflight
from print_shop.setup import Proposer, pauses
from print_shop.setup import lessons as print_lessons
from print_shop.setup.context import PartContext, Seen, place, pose_for, printed, read_context
from print_shop.setup.doubts import Doubting
from print_shop.setup.features import Feature, measure, resting_on
from print_shop.setup.intake import Asker, Estimate, Intake, Question
from print_shop.setup.loop import Part, PrintLoop
from print_shop.setup.loop import run as run_loop
from print_shop.setup.offer import Offering, ThisPart
from print_shop.setup.offer import offering as offer_past_prints
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    Change,
    Ending,
    GivenLesson,
    LoopCall,
    Proposal,
    Source,
    Suggested,
    apply,
    placeable,
    pose_value,
    reached,
    read_back,
    tree_supports,
)
from print_shop.setup.questions import PURPOSE, SEEN, STOCK, estimate, stock
from print_shop.setup.reading import SliceReading, carried, flags, read_slice, seen, seen_said
from print_shop.setup.review import Reviewer, ReviewPart, ReviewRequest
from print_shop.setup.skill import groups_for, load
from print_shop.setup.suggest import (
    Beside,
    looked_up,
    once_turned,
    recorded,
    suggested,
    went_on,
)
from print_shop.setup.traits import traits
from print_shop.setup.waiting import Waiting, ask_meanwhile
from print_shop.slicer.arrange import DoesNotFit, SlicePlate, ToPlace, slice_parts
from print_shop.slicer.naming import job_id_from_filename, name_gcode
from print_shop.slicer.orca import (
    Slicer,
    SliceResult,
    SlicerProfiles,
    parse_config_block,
    slice_model,
    slice_plate,
)
from print_shop.slicer.store import Store

__all__ = ["build_graph", "graph_edges", "new_job", "why_not_start"]

logger = logging.getLogger(__name__)


def new_job(stl_path: str | Path, *, entry: EntryPath) -> PrintJobState:
    """Create a fresh ``PrintJobState`` for a new print job.

    Args:
        stl_path: Path to the input STL file.
        entry: Which entry point the job came from.

    Returns:
        A ``PrintJobState`` with identity fields populated.
    """
    job = PrintJobState(
        job_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        entry=entry,
        created_at=datetime.now(UTC).isoformat(),
        stl_path=str(stl_path),
    )
    return job


def build_graph(
    profiles: SlicerProfiles | None,
    outdir_root: Path,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    llm: LLMConfig | None = None,
    printer: PrinterClient | None = None,
    slicer: Slicer = slice_model,
    ledger: Ledger | None = None,
    proposer: Proposer | None = None,
    asker: Asker | None = None,
    reviewer: Reviewer | None = None,
    loop: PrintLoop | None = None,
    show: Callable[[str], None] | None = None,
    store: Store | None = None,
    waiting: Waiting | None = None,
    plate_slicer: SlicePlate = slice_plate,
) -> Any:
    """Build and compile the print pipeline graph.

    Args:
        profiles: Slicer profile paths, closed over by the slice node. None
            for a daemon that only serves the outcome gate (`--serve`): a
            job it is asked to slice fails at the orient node, saying so.
        outdir_root: Root directory for per-job output. Each job writes into
            ``outdir_root / job_id``.
        checkpointer: An already-constructed checkpointer. ``build_graph``
            passes it straight to ``compile()`` and never wraps it in a
            ``with`` block — the connection must stay open for the lifetime
            of the graph (CODE_REVIEW.md P0-3).
        llm: Endpoint config for the orientation proposer, from
            ``LLMConfig.from_settings()`` (LiteLLM :4000). **No default
            model:** None means no model is asked, and the geometric proposer
            chooses, saying so. Like ``printer``, nothing here reaches for
            configuration nobody passed.
        printer: The printer the ``print`` node commands. **No default.** A
            graph built without one cannot reach hardware — ``print_node``
            raises instead. Pass ``live_printer()`` to command the real
            machine; pass a double in tests. There is deliberately no
            build-it-from-the-environment fallback: that fallback existed for
            one afternoon and it started an unattended print from the test
            suite.

        slicer: What slices. The real Orca by default, a local process
            with no hardware behind it; tests replay a recorded slice.
        ledger: Where each print the agent starts is recorded. **Required
            to print:** ``print_node`` refuses to start a print the ledger
            cannot record, the same way it refuses one nobody approved.
        proposer: Proposes the intake's questions from the part's features,
            then the print's settings over the presets from the answers. With a reviewer those
            are proposals, worked out beside the review, which starts from the settings with
            the model absent (mqucifer/print-shop#242)
            (mqucifer/print-shop#7, #10). None, or no asker, slices with the
            presets as they are, supports forced on for an overhang as before.
        asker: Answers the intake's questions: on the command line, or from
            a recording. Each option's time and filament is Orca's estimate,
            sliced before it's asked.
        reviewer: The Sponsor's review of the proposed settings, after each
            slice and before the approval gate; a correction slices again.
            None skips the review, and the approval gate still stands.
        loop: The print loop (mqucifer/print-shop#129): with a proposer and an asker, one
            conversation with tools decides the pose and the settings after the intake's
            questions, in place of the orientation call and the settings call. The questions
            are asked of the file's own pose. Past its limit the job falls back to the single
            calls, and says so. With a reviewer it runs beside the review, which shows the part
            as the file or its context lays it, and its pose is a proposal like the rest
            (mqucifer/print-shop#242). None keeps the single calls.
        show: Where a hand-sliced file's report is shown before the gate
            (mqucifer/print-shop#164): what its slice reads as, with nothing proposed. None
            shows it nowhere; the ledger keeps the reading either way.
        store: print-shop's profiles, by which a hand-sliced file's settings are put under the
            profile each belongs to. None keeps them all under "other".
        waiting: The person's side of the wait for the model's questions
            (mqucifer/print-shop#208): print-shop's two are asked at once, the model's are
            added when they arrive, and the person can go on without them. None asks them
            all together, once the model's call has ended.
        plate_slicer: What slices a job of several parts (mqucifer/print-shop#109): the real
            Orca by default, as `slicer` is.

    Returns:
        A compiled LangGraph ``CompiledStateGraph``.
    """
    build_volume = build_volume_mm(profiles.machine) if profiles is not None else None

    def sliced_node(state: PrintJobState) -> dict[str, Any]:
        """Take a hand-sliced file as it is (mqucifer/print-shop#164).

        Its G-code is copied into the job's folder, the same bytes under a name carrying the
        job id, and read: Orca's own totals for the gate, and the slice's reading as a report.
        Nothing is asked, proposed, turned or sliced, and no lesson is looked up: none of the
        proposer, the asker, the reviewer, the loop or the slicer is touched here.

        Raises:
            SlicedChanged: If the file isn't the bytes that were read when it was given.
        """
        job = state["sliced"]
        result = hand_sliced.take(job, outdir_root / state["job_id"], state["job_id"])
        reading = _read(result)
        # The review's own readings, as a report: what a flag would propose is left out.
        found = tuple(
            f.model_copy(update={"change": None, "reading": f.reading.replace(
                "the intake didn't say which faces are seen",
                "a hand-sliced job isn't asked which faces are seen")})
            for f in (flags(reading, None, job.file.config, job.file.config) if reading else ())
        )
        if show is not None:
            show("\n".join(hand_sliced.report(job, reading, found)))
        return {
            "gcode_path": str(result.gcode_path),
            "slice": result,
            "applied_orientation": AppliedOrientation(rationale=hand_sliced.RATIONALE),
            "profiles_used": None,
            "slice_reading": reading,
            "slice_flags": found,
        }

    def ingest_node(state: PrintJobState) -> dict[str, Any]:
        """Analyze the STL and store the MeshReport in state."""
        if state.get("stl_paths"):
            return {"parts": plate_job.take(
                tuple(state["stl_paths"]), outdir_root / state["job_id"] / "given")}
        report = analyze(state["stl_path"])
        return {"mesh": report, "stl_sha256": report.stl_sha256}

    def orient_node(state: PrintJobState) -> dict[str, Any]:
        """Choose how the part sits on the plate: model proposes, validator disposes.

        The full ``Outcome`` (proposal, verdict history, accepted) is stored in
        state so a gate can show a human what was proposed and what changed.
        """
        if state.get("parts"):
            return orient_several(state)
        return orient_part(state)

    def orient_several(state: PrintJobState) -> dict[str, Any]:
        """Each part's pose by the single step (mqucifer/print-shop#109): its side from its
        context, as laid, or one orientation call. The print loop isn't run, as it reads one
        part.

        Where the questions show while a model works (mqucifer/print-shop#208), the calls wait
        till they're answered, as the loop's do for one part: a call a part came before them.

        Raises:
            RuntimeError: If a part has no accepted pose: the review gate shows one part.
        """
        later = (waiting is not None and asker is not None and proposer is not None
                 and proposer.asks_model)
        parts, decided = [], []
        for part in state["parts"]:
            own: PrintJobState = {**state, "stl_path": part.stl, "stl_sha256": part.sha256,
                                  "mesh": part.mesh}
            found = orient_part(own, came_from=Path(part.given), looped=False, later=later)
            outcome: Outcome[Orientation] = found["orientation"]
            if not outcome.accepted:
                said = "; ".join(v.detail for v in outcome.outstanding if v.detail)
                raise RuntimeError(
                    f"no pose of part {part.n}, {part.name}, was accepted"
                    + (f" ({said})" if said else "") + ": nothing was sliced")
            decided.append(outcome)
            read = part.model_copy(update={
                "candidates": found["orientation_candidates"],
                "laid_traits": found["laid_traits"],
                "context": found["part_context"],
                "lessons": tuple(found.get("orientation_lessons", ())),
            })
            plate_job.keep_sidecar(read)
            parts.append(read)
        return {"parts": tuple(parts), "orientations": tuple(decided)}

    def orient_part(
        state: PrintJobState, *, came_from: Path | None = None, looped: bool = True,
        later: bool = False,
    ) -> dict[str, Any]:
        """One part's pose. `came_from` is where it was given from, where that isn't the file
        that's read: its context is beside that. `looped` False never leaves the pose to the
        loop. `later` leaves a plate's part its orientation call for after the questions, which
        are asked of it as the file lays it, or the first pose that fits where that doesn't."""
        verify_unchanged(state["stl_path"], state.get("stl_sha256", ""))
        if build_volume is None:
            raise RuntimeError(
                "this graph was built without profiles, so it cannot slice: give the "
                "fitted nozzle and filament (scripts/print_job.py --nozzle --filament)"
            )

        # The part's traits in the file's own pose, before any turn: what an orientation lesson
        # is keyed to, and looked up by (mqucifer/print-shop#128).
        laid = measure(state["stl_path"], state["mesh"].bed_contact_area_mm2)
        # The part's context, from its design run or a sidecar (mqucifer/print-shop#130).
        given_at = state.get("context_path")
        context = read_context(came_from or Path(state["stl_path"]),
                               Path(given_at) if given_at else None)
        # A pose kept as laid, or on the side the context lays it on, is measured alone: the
        # questions don't wait for five it won't print in (mqucifer/print-shop#235). Those
        # are measured beside them, from the slice node. A plate's part has all six measured
        # here: its send-back, its fit on the plate and print-again read them, and nothing
        # measures them later (mqucifer/print-shop#109).
        as_laid = bool(state.get("as_laid"))
        alone = (None if came_from is not None
                 else LAID if as_laid else pose_for(context.down) if context.down else None)
        found: tuple[Orientation, ...] = ()
        # Each is marked where it fits only turned on the plate, or in no turn: a kept pose that
        # fits only turned is still kept, and offered the turn (mqucifer/print-shop#257).
        stl = state["stl_path"]
        if alone is not None:
            found = with_turns(stl, (candidate_in(stl, alone),), build_volume, alone)
        if came_from is not None:
            # A plate is sliced with no part turned on it (mqucifer/print-shop#109), so a pose
            # that fits only turned doesn't fit it.
            found = tuple(candidates(stl))
        elif alone is None or (not as_laid and not found[0].fits_in(build_volume)):
            # None is kept, so all six are weighed.
            found = with_turns(stl, tuple(candidates(stl)), build_volume, alone)
        poses: dict[str, Any] = {"orientation_candidates": found, "laid_traits": traits(laid),
                                 "laid_features": laid, "part_context": context}
        request = OrientationRequest(
            candidates=found, build_volume_mm=build_volume, part_description="",
        )
        if as_laid:
            return {"orientation": _as_laid(request), **poses}
        given = print_lessons.for_orienting(ledger, laid) if ledger is not None else ()
        request = request.model_copy(update={"lessons": tuple(g.line() for g in given)})
        kept = _kept(context, found, build_volume)
        if looped and loop is not None and proposer is not None and asker is not None:
            # The print loop chooses the pose after the questions, which are asked of the
            # file's own, or of the side its context lays it on (mqucifer/print-shop#129, #130).
            own = kept or next(c for c in found if c.rotation_deg == (0.0, 0.0, 0.0))
            where = (f"on its {context.down} side, as {context.down_from} lays it" if kept
                     else "as the file lays it")
            then = "for the questions: the print loop chooses the pose after them"
            if reviewer is not None:
                # The review doesn't wait for the loop, so this is the pose it shows
                # (mqucifer/print-shop#242).
                then = "the model can only propose a turn"
                if kept is None and not own.fits_in(build_volume):
                    # It can't be sliced as the file lays it: the choice a failed call makes.
                    fitted = _resolved(propose_validate(
                        lambda req: propose_geometric(req, "It doesn't fit as the file lays it, "
                                                           "and the review waits for no model"),
                        validate_orientation, request), found)
                    if not fitted.accepted:
                        return {"orientation": fitted, "orientation_lessons": given, **poses}
                    own = fitted.proposal.value
                    where = (f"it doesn't fit the plate as the file lays it, so it's shown at "
                             f"{pose_value(own.rotation_deg)}: {print_lessons.GEOMETRIC_SHOWN}")
                elif kept is None:
                    checked = validate_orientation(
                        Chosen(value=own, source=AS_FILED, rationale=""), request)
                    if checked.blockers:
                        then += "; the check on poses says " + "; ".join(
                            _objects(v, own, found) for v in checked.blockers)
            joined = "; " if reviewer is not None else ", "
            pending = Outcome(proposal=Chosen(value=own, source=LOOP_PENDING, rationale=(
                f"{where}{joined}{then}")), accepted=True, attempts=0)
            return {"orientation": pending, "orientation_lessons": given, **poses}
        if kept is not None:
            # The side its design run or sidecar lays it on is kept, as a pose laid by the
            # person is: no model is asked (mqucifer/print-shop#130).
            return {"orientation": Outcome(proposal=Chosen(
                value=kept, source=CONTEXT, rationale=(
                    f"kept on its {context.down} side, as {context.down_from} lays it: no "
                    "orientation model was asked")), accepted=True, attempts=0),
                "orientation_lessons": given, **poses}
        if later:
            fitting = sorted((c for c in found if c.fits_in(build_volume)),
                             key=lambda c: any(c.rotation_deg))  # as the file lays it, first
            if fitting:
                return {"orientation": Outcome(proposal=Chosen(
                    value=fitting[0], source=POSE_AFTER_QUESTIONS,
                    rationale="for the questions: its pose is chosen after them"),
                    accepted=True, attempts=0), "orientation_lessons": given, **poses}
            if came_from is not None:
                # Without asking a model, which the person would wait for to be told this.
                n = next(p.n for p in state["parts"] if p.stl == state["stl_path"])
                raise RuntimeError(
                    f"no pose of part {n}, {came_from.name}, fits the build volume "
                    f"{build_volume} mm: nothing was sliced")
        return {"orientation": orient_by_call(request), "orientation_lessons": given, **poses}

    def orient_by_call(
        request: OrientationRequest, stop: threading.Event | None = None,
    ) -> Outcome[Orientation]:
        """The single orientation call: the model proposes, the validator disposes. `stop` ends
        the call, beside a review."""

        def chooses(req: OrientationRequest) -> Any:
            if llm is None:
                return propose_geometric(req, NO_MODEL_CONFIGURED)
            try:
                if stop is not None:
                    return propose_orientation(req, llm, stop)
                return propose_orientation(req, llm)
            except (RuntimeError, OSError):
                return propose_geometric(req)

        return _resolved(propose_validate(chooses, validate_orientation, request),
                         request.candidates)

    def slice_node(state: PrintJobState) -> dict[str, Any]:
        """The slice, with the poses orient didn't wait for measured beside it
        (mqucifer/print-shop#235). They live no longer than this node: what's checkpointed is
        the poses measured, never the measuring.
        """
        found = state.get("orientation_candidates", ())

        def marked(mesh: trimesh.Trimesh, pose: Orientation) -> Orientation:
            # The kept pose is among those known, so none measured beside is the kept one.
            assert build_volume is not None
            return with_turns(mesh, (pose,), build_volume, None)[0]

        measuring = Measuring(state["stl_path"], found, marked) if found else None
        try:
            return sliced_in_pose(state, measuring)
        finally:
            if measuring is not None:
                measuring.stop()

    def sliced_in_pose(state: PrintJobState, measuring: Measuring | None) -> dict[str, Any]:
        """Slice the model in the chosen orientation, writing into outdir_root/job_id.

        The STL is rotated to the accepted orientation before slicing. If that
        orientation still has overhang, supports are forced on: orientation
        minimises overhang but does not always eliminate it, and a part with
        unsupported overhang fails on the plate.

        If no orientation was accepted (e.g. non-manifold mesh routed directly
        from ingest), the part is sliced in its authored pose using the mesh's
        measured overhang area to determine support requirements.
        """
        if state.get("parts"):
            return slice_several(state)
        outdir = outdir_root / state["job_id"]
        outcome = state.get("orientation")
        if outcome is not None and outcome.accepted:
            chosen = outcome.proposal.value
            rotation_deg = chosen.rotation_deg
            overhang_area = chosen.overhang_area_mm2
            rationale = outcome.proposal.rationale
            pose = {"height_mm": chosen.height_mm,
                    "bed_contact_area_mm2": chosen.bed_contact_area_mm2}
        else:
            rotation_deg = (0.0, 0.0, 0.0)
            overhang_area = state["mesh"].overhang_area_mm2
            rationale = "sliced in the authored orientation: no orientation was accepted"
            pose = {"height_mm": state["mesh"].bbox_mm[2],
                    "bed_contact_area_mm2": state["mesh"].bed_contact_area_mm2}
        applied = AppliedOrientation(rotation_deg=rotation_deg, rationale=rationale, **pose)

        if rotation_deg == (0.0, 0.0, 0.0):
            stl_to_slice: str | Path = state["stl_path"]
        else:
            stl_to_slice = write_rotated(state["stl_path"], rotation_deg, outdir / "rotated.stl")

        assert profiles is not None, "orient refuses a graph without profiles"
        assert build_volume is not None
        # As posed, or, only where that doesn't fit, in the turn the review offers
        # (mqucifer/print-shop#257).
        fitting = Fitting(slicer, build_volume)
        proposed: dict[str, Any] = {}

        def featured() -> tuple[Feature, ...]:
            """The features in the pose being sliced. The file's own was read at orient."""
            contact = applied.bed_contact_area_mm2 or 0.0
            laid = state.get("laid_features", ())
            if laid and rotation_deg == LAID:
                return resting_on(laid, contact)
            return measure(stl_to_slice, contact)

        if proposer is not None and asker is not None:
            features = featured()
            if measuring is not None and measuring.pending():
                # Only now, so the five never slow what the questions wait for.
                verify_unchanged(state["stl_path"], state.get("stl_sha256", ""))
                measuring.start()
            first = stl_to_slice

            def priced(settings: dict[str, str], n: int) -> Estimate | None:
                return _estimate(fitting, first, profiles, settings, outdir / "options" / str(n))

            context = state.get("part_context")
            # What the notes say is read back in the pose the questions are asked in, and the
            # model asks only what they leave open (mqucifer/print-shop#236).
            two = stock(context, rotation_deg)
            if waiting is not None and proposer.asks_model:
                measured = features
                intake = ask_meanwhile(
                    lambda stop: proposer.questions(measured, profiles, stop, context),
                    asker, waiting, lambda questions: _priced(questions, priced), two,
                )
            else:
                intake = _ask(proposer.questions(features, profiles, context=context), asker,
                              priced, two)
            proposed["intake"] = intake
            earlier: list[Change] = []
            looped: Proposal | None = None
            fell_back: dict[str, Any] = {}
            by_loop = outcome is not None and outcome.proposal.source == LOOP_PENDING
            # With a review, the model works beside it and proposes (mqucifer/print-shop#242).
            beside = Beside() if reviewer is not None else None
            questions_in = rotation_deg
            if by_loop and beside is not None:
                assert outcome is not None
                own = outcome.proposal.value
                kept_side = bool(
                    context is not None and context.down
                    and pose_for(context.down) == own.rotation_deg)
                proposed["orientation"] = Outcome(proposal=Chosen(
                    value=own, rationale=outcome.proposal.rationale,
                    source=(CONTEXT if kept_side else AS_FILED if own.rotation_deg == LAID
                            else "geometric"),
                ), accepted=True, attempts=0)
                assert measuring is not None, "a pose left to the print loop was oriented"
                asked_of = features
                progress: list[LoopCall] = []

                def by_the_loop(stop: threading.Event) -> Suggested:
                    return loop_proposes(
                        state, measuring, intake, asked_of, questions_in, kept_side, stop,
                        progress)

                beside.begin(by_the_loop, rotation_deg, progress)
            elif by_loop:
                assert outcome is not None
                assert loop is not None
                assert measuring is not None, "a pose left to the print loop was oriented"
                _say_waiting(measuring.pending(), "the print loop chooses among all six")
                poses = measuring.all_six()
                kept = _kept(context, poses, build_volume) if context is not None else None
                found_by = run_loop(loop, Part(
                    stl=Path(state["stl_path"]),
                    facts=proposer.facts(intake, features, profiles, context),
                    candidates=poses, build_volume_mm=build_volume,
                    skill=load(groups_for(intake, features)), profiles=profiles, slicer=fitting,
                    workdir=outdir / "loop", ledger=ledger,
                    orienting=state.get("orientation_lessons", ()),
                    lessons=proposer.lessons_for(features, profiles),
                    kept=kept.rotation_deg if kept is not None else (0.0, 0.0, 0.0),
                    kept_from=context.down_from if kept is not None and context else "",
                ))
                record = {"loop": found_by.calls, "loop_ending": (
                    f"{found_by.ended}, after {found_by.model_calls} calls in "
                    f"{found_by.seconds:g} s")}
                if found_by.proposed:
                    assert found_by.pose is not None
                    chosen = found_by.pose
                    why = found_by.reply.get("reasoning", "") or "the print loop chose it"
                    decided = Outcome(proposal=Chosen(
                        value=chosen, source=f"loop:{loop.model}",
                        rationale=f"the print loop: {why}"[:600]), accepted=True, attempts=1)
                    earlier = list(found_by.corrections)
                else:
                    # Past its limit, or the model unreachable: today's single calls, said so.
                    # The side the part's context lays it on is kept, as the single calls keep it.
                    decided = Outcome(proposal=Chosen(
                        value=kept, source=CONTEXT, rationale=(
                            f"kept on its {context.down} side, as {context.down_from} lays it: "
                            "no orientation model was asked")), accepted=True, attempts=0,
                    ) if kept is not None and context is not None else orient_by_call(
                        OrientationRequest(
                            candidates=poses, build_volume_mm=build_volume,
                            lessons=tuple(g.line() for g in state.get("orientation_lessons", ()))))
                    fell_back = {**record, "notes": (
                        f"the print loop stopped, {found_by.ended}, after "
                        f"{found_by.model_calls} calls in {found_by.seconds:g} s: the pose and "
                        "the settings came from the single calls",)}
                proposed["orientation"] = decided
                if decided.accepted:
                    chosen = decided.proposal.value
                    rotation_deg = chosen.rotation_deg
                    applied = AppliedOrientation(
                        rotation_deg=rotation_deg, rationale=decided.proposal.rationale,
                        height_mm=chosen.height_mm,
                        bed_contact_area_mm2=chosen.bed_contact_area_mm2)
                    if rotation_deg != (0.0, 0.0, 0.0):
                        stl_to_slice = write_rotated(
                            state["stl_path"], rotation_deg,
                            outdir / f"rotated-{pose_value(rotation_deg)}.stl")
                if found_by.proposed:
                    features = featured()
                    reply = found_by.reply
                    made = dataclasses.replace(proposer, ask=lambda *_: reply, llm=None)(
                        intake, features, profiles, context, rotation_deg)
                    looked = set(found_by.looked_up)
                    looped = made.model_copy(update={
                        **record, "model": f"loop:{loop.model}",
                        "lessons": tuple(g.model_copy(update={"looked_up": g.id in looked})
                                         for g in made.lessons),
                    })
            offer: Offering | None = None
            doubting: Doubting | None = None
            left_behind: list[str] = []
            chose = ""
            try:
                while True:
                    features = featured()
                    if reviewer is not None and ledger is not None:
                        # Before the proposal's call: the offer waits for no model
                        # (mqucifer/print-shop#211).
                        built = _offering(ledger, state, stl_to_slice, features, applied, profiles,
                                          build_volume, earlier, intake, measuring, chose)
                        if offer is None:
                            offer, doubting = built, Doubting.start(built, proposer.doubter())
                        else:
                            offer = offer.carried_to(built)
                    ask_again: Callable[[], None] | None = None
                    if beside is not None:
                        proposal = proposer.without_model(
                            intake, features, profiles, context, rotation_deg)
                        if proposer.suggests:
                            ask_again = settings_beside(
                                beside, intake, features, context, rotation_deg)
                            if beside.work is None:
                                ask_again()
                    elif looped is not None:
                        proposal, looped = looped, None
                    else:
                        proposal = proposer(intake, features, profiles, context, rotation_deg)
                        if fell_back:
                            proposal = proposal.model_copy(update={
                                "loop": fell_back["loop"], "loop_ending": fell_back["loop_ending"],
                                "notes": proposal.notes + fell_back["notes"]})
                    proposal = _with_orienting(proposal, state.get("orientation_lessons", ()))
                    if intake.without:
                        # Said at the review, as well as when it happened (mqucifer/print-shop#208).
                        proposal = proposal.model_copy(
                            update={"notes": proposal.notes + (intake.without,)})
                    if left_behind:
                        proposal = proposal.model_copy(
                            update={"notes": proposal.notes + tuple(left_behind)})
                    # A no to the model's is its own pose's, and sets nothing (#242).
                    carried = [c for c in earlier if c.key != ORIENTATION and not c.said_no_to]
                    if carried:
                        # Corrections made before the part was turned stand after it.
                        proposal = proposal.corrected(carried)
                    try:
                        result, reviewed = _propose_and_review(
                            proposal, profiles,
                            lambda p, stl=stl_to_slice: fitting(stl, p, outdir),
                            reviewer, applied, outdir / "proposed", intake,
                            measuring=measuring, laid_traits=state.get("laid_traits", ()),
                            cost=_coster(
                                fitting, stl_to_slice, profiles, proposal, outdir / "offers"),
                            lessons_for=_lessons_for(ledger, state["stl_path"], profiles,
                                                     outdir / "poses"),
                            earlier=earlier,
                            context=context, stl=state["stl_path"],
                            offering=offer, doubting=doubting,
                            material=_material_of(profiles), stock=_stock(ledger, profiles),
                            record_load=_recorder(ledger, profiles),
                            stands=_stands(ledger, profiles),
                            beside=beside, ask_again=ask_again,
                        )
                    except _Turned as turned:
                        # Sent back at the review: turned to the person's pose, and sliced again.
                        earlier, offer = turned.corrections, turned.offering
                        typed = {c.key for c in earlier}
                        dropped = tuple(dict.fromkeys(
                            c.key for c in turned.taken if c.key not in typed))
                        # What was taken of the model's is for the pose it was taken in.
                        left_behind += [
                            f"Taken at {pose_value(rotation_deg)} and left behind by the turn: "
                            f"{c.key}={c.value}. Take it again if it's proposed here, or type it."
                            for c in {c.key: c for c in turned.taken}.values() if c.key in dropped]
                        if turned.to.taken:
                            chose = "you took the model's turn at this job"
                        if beside is not None and beside.work is not None:
                            # The loop's settings follow its own turn. To any other pose, its call
                            # ends and is made again for that pose (mqucifer/print-shop#242).
                            followed = once_turned(beside.work.now(), turned.to.value)
                            beside.end(Ending.TURNED, dropped=dropped)
                            if followed is not None:
                                beside.carry(followed)
                        to = measuring.lying(turned.to.value) if measuring is not None else None
                        if to is None:
                            raise RuntimeError(
                                f"the pose {turned.to.value} it was sent back to wasn't measured, "
                                "so it isn't turned to it") from None
                        chosen = to
                        rotation_deg = chosen.rotation_deg
                        applied = AppliedOrientation(
                            rotation_deg=rotation_deg,
                            rationale=(
                                f"proposed by the model ({turned.to.knowledge}), taken by you at "
                                f"the review: {turned.to.reason}"[:600] if turned.to.taken
                                else f"sent back at the review, by you: {turned.to.reason}"),
                            height_mm=chosen.height_mm,
                            bed_contact_area_mm2=chosen.bed_contact_area_mm2,
                        )
                        proposed["orientation"] = Outcome(
                            proposal=Chosen(value=chosen,
                                            source=(turned.to.knowledge if turned.to.taken
                                                    else "sponsor"),
                                            rationale=applied.rationale),
                            accepted=True, attempts=0,
                        )
                        stl_to_slice = (
                            state["stl_path"] if rotation_deg == (0.0, 0.0, 0.0)
                            else write_rotated(state["stl_path"], rotation_deg,
                                               outdir / f"rotated-{pose_value(rotation_deg)}.stl")
                        )
                        continue
                    break
            finally:
                if beside is not None:
                    beside.close()  # however it ended, no call is left running
            proposed["features"] = features
            proposed.update(reviewed)
            if measuring is not None:
                proposed["orientation_candidates"] = measuring.ready()
        else:
            active_profiles = (
                scoring_profiles(profiles, outdir) if overhang_area > 0 else profiles
            )
            result = fitting(stl_to_slice, active_profiles, outdir)
            if result.turn_deg:
                raise _unoffered(result)
            reading = _read_as_sliced(result)
            proposed["slice_reading"] = reading
            context = state.get("part_context")
            placed = (place(context, state["stl_path"], rotation_deg)
                      if context is not None and context.found else None)
            proposed["slice_flags"] = (
                flags(reading, None, *_presets(active_profiles), placed) if reading else ()
            )
        # Orca always writes `plate_1.gcode`. The printer's storage is one flat
        # namespace and `start_print` takes a bare name, so the file is renamed
        # to carry the job id before anything can upload it.
        if result.turn_deg:
            # Only a turn taken at the review gets here (mqucifer/print-shop#257).
            applied = applied.model_copy(update={
                "turn_deg": result.turn_deg,
                "rationale": f"{applied.rationale}; turned {turn_words(result.turn_deg)}, on "
                             "the plate to fit, taken by you at the review",
            })
        result = name_gcode(
            result, job_id=state["job_id"], part=Path(state["stl_path"]).stem
        )
        return {
            "gcode_path": str(result.gcode_path),
            "slice": result,
            "applied_orientation": applied,
            "profiles_used": profiles.used,
            **proposed,
        }

    def posed_after_questions(
        parts: tuple[PlatePart, ...], asked_of: list[Outcome[Orientation]],
        undecided: list[int], fit: Callable[[list[Outcome[Orientation]]], SliceResult],
    ) -> list[Outcome[Orientation]]:
        """Each undecided part's orientation call, after the questions. The answers are never
        lost to it: a part with no accepted pose, or poses that don't fit one plate together,
        keep the pose the questions were asked of, saying so, to send back at the review."""
        assert build_volume is not None, "orient refuses a graph without profiles"

        def kept(at: int, why: str) -> Outcome[Orientation]:
            pose = asked_of[at].proposal.value
            return Outcome(proposal=Chosen(
                value=pose, source=POSE_KEPT,
                rationale=f"kept as the questions had it, {pose_value(pose.rotation_deg)}: {why}",
            ), accepted=True, attempts=0)

        chosen = list(asked_of)
        for at in undecided:
            found = orient_by_call(OrientationRequest(
                candidates=parts[at].candidates, build_volume_mm=build_volume,
                part_description="", lessons=tuple(g.line() for g in parts[at].lessons),
            ))
            chosen[at] = found if found.accepted else kept(at, "no pose passed its check")
        try:
            fit(chosen)
        except DoesNotFit:
            for at in undecided:
                wanted_pose = pose_value(chosen[at].proposal.value.rotation_deg)
                chosen[at] = kept(at, f"the poses chosen don't fit one plate together (this "
                                      f"part's was {wanted_pose})")
        return chosen

    def slice_several(state: PrintJobState) -> dict[str, Any]:
        """The plate of a job of several parts (mqucifer/print-shop#109): one intake, one
        proposal and one review for it, arranged, and by object where the head has the room.

        The model's questions are waited for as one part's are, and so are the poses left for
        after them (`orient_several`): print-shop's two questions wait for no model
        (mqucifer/print-shop#208).

        Raises:
            DoesNotFit: If the parts can't all be sliced on one plate.
        """
        assert profiles is not None, "orient refuses a graph without profiles"
        outdir = outdir_root / state["job_id"]
        parts = tuple(state["parts"])
        orientations = list(state["orientations"])
        turned, to_place = plate_job.posed(parts, tuple(orientations), outdir / "parts")
        read = plate_job.together(turned)
        done: dict[str, Any] = {}
        layouts: list[PlateLayout] = []

        def slicing(placing: tuple[ToPlace, ...]) -> Callable[[SlicerProfiles], SliceResult]:
            def sliced(at: SlicerProfiles) -> SliceResult:
                result, layout = slice_parts(placing, at, outdir, plate_slicer)
                layouts.append(layout)
                return result

            return sliced

        def estimating(placing: tuple[ToPlace, ...]) -> Slicer:
            # As the one part's estimates are made, of the plate: it's the plate that's paid for.
            def plated(
                stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str,
            ) -> SliceResult:
                return slice_parts(placing, profiles, Path(outdir), plate_slicer)[0]

            return plated

        if proposer is not None and asker is not None:
            wanted = plate_job.by_object(profiles, outdir / "plate")
            undecided = [n for n, o in enumerate(orientations)
                         if o.proposal.source == POSE_AFTER_QUESTIONS]
            # Before anything is asked: parts that don't fit one plate end the job here.
            try:
                slicing(to_place)(wanted)
            except DoesNotFit:
                if not undecided:
                    raise
                # As the files lay them they don't. Each in its smallest pose, they may.
                assert build_volume is not None, "orient refuses a graph without profiles"
                orientations = plate_job.smallest(parts, orientations, undecided, build_volume)
                turned, to_place = plate_job.posed(parts, tuple(orientations), outdir / "parts")
                read = plate_job.together(turned)
                slicing(to_place)(wanted)
            first = to_place
            measured = read.features
            # What the answers are of: the poses the parts lie in as they're asked.
            asked_in = tuple(o.proposal.value.rotation_deg for o in orientations)
            about_them = plate_job.asked_about(asker, turned)

            def priced(settings: dict[str, str], n: int) -> Estimate | None:
                return _estimate(estimating(first), first[0].stl, wanted, settings,
                                 outdir / "options" / str(n))

            if waiting is not None and proposer.asks_model:
                intake = ask_meanwhile(
                    lambda stop: proposer.questions(measured, profiles, stop),
                    about_them, waiting, lambda questions: _priced(questions, priced),
                )
            else:
                intake = _ask(proposer.questions(measured, profiles), about_them, priced)
            done["intake"] = intake
            # What each part's answer on faces is of, kept with it (mqucifer/print-shop#248).
            parts = tuple(p.model_copy(update={"asked_in": tuple(a)})
                          for p, a in zip(parts, asked_in, strict=True))
            if undecided:
                orientations = posed_after_questions(
                    parts, orientations, undecided,
                    lambda chosen: slicing(
                        plate_job.posed(parts, tuple(chosen), outdir / "parts")[1])(wanted),
                )
            given = tuple({g.id: g for part in parts for g in part.lessons}.values())
            process, _ = _presets(profiles)
            earlier: list[Change] = []
            #: The plate before the last send-back, for one it can't be sliced with.
            before: tuple[tuple[PlatePart, ...], list[Outcome[Orientation]], list[Change]] | None
            before = None
            undone = ""
            # The model's settings are proposals made beside the review, as one part's are
            # (mqucifer/print-shop#242): the plate's review doesn't wait for them.
            beside = Beside() if reviewer is not None else None
            try:
                while True:
                    turned, to_place = plate_job.posed(parts, tuple(orientations), outdir / "parts")
                    read = plate_job.together(turned)
                    judged, turned_since = plate_job.still_holds(intake, turned, asked_in)
                    # Carried from the answers as given, each part from the pose it was asked in.
                    faces, seen_lines = plate_job.seen_of(intake, turned, asked_in)
                    made = read.proposer(proposer)
                    ask_again: Callable[[], None] | None = None
                    if beside is not None:
                        proposal = made.without_model(judged, read.features, profiles,
                                                      read.context)
                        if made.suggests:
                            ask_again = plate_settings_beside(
                                beside, made, judged, read.features, read.context, faces)
                            if beside.work is None:
                                ask_again()
                    else:
                        proposal = made(judged, read.features, profiles, read.context,
                                        seen=faces)
                    if turned_since:
                        proposal = proposal.model_copy(
                            update={"notes": (turned_since, *proposal.notes)})
                    proposal = _with_orienting(
                        plate_job.planned(proposal, process, len(parts),
                                          without_loop=loop is not None),
                        given)
                    if intake.without:
                        # Said at the review, as one part's is (mqucifer/print-shop#208).
                        proposal = proposal.model_copy(
                            update={"notes": proposal.notes + (intake.without,)})
                    if earlier:
                        proposal = proposal.corrected(earlier)
                    plate = _PlateReview(
                        layout=lambda: layouts[-1],
                        read=read,
                        undone=undone,
                        seen=faces,
                        seen_lines=seen_lines,
                        unjudged=faces is None and any(
                            q.feature == SEEN.feature for q in intake.questions),
                        parts=tuple(
                            ReviewPart(
                                n=part.n, name=part.name,
                                orientation=part.applied or AppliedOrientation(),
                                candidates=part.candidates, laid_traits=part.laid_traits,
                                lessons_for=_lessons_for(ledger, part.stl, profiles,
                                                         outdir / "poses" / str(part.n)),
                            )
                            for part in turned
                        ),
                    )
                    try:
                        result, reviewed = _propose_and_review(
                            proposal, profiles, slicing(to_place), reviewer,
                            AppliedOrientation(rationale=plate_job.poses_said(turned)),
                            outdir / "proposed", intake,  # as given: faces are carried
                            cost=_coster(estimating(to_place), to_place[0].stl, profiles, proposal,
                                         outdir / "offers"),
                            earlier=earlier,
                            plate=plate,
                            # A plate's pause names its spools as one part's does
                            # (mqucifer/print-shop#218).
                            material=_material_of(profiles), stock=_stock(ledger, profiles),
                            record_load=_recorder(ledger, profiles),
                            stands=_stands(ledger, profiles),
                            beside=beside, ask_again=ask_again,
                        )
                    except DoesNotFit as spill:
                        # Raised only by a pose's first slice: a later one is taken back at the
                        # review. So what didn't fit is the send-back, and that's taken back.
                        if before is None:
                            raise
                        parts, orientations, earlier = before
                        before, undone = None, str(spill)
                        continue
                    except _Turned as back:
                        assert back.part is not None, "a plate's send-back names its part"
                        if beside is not None:
                            # Its settings were for the poses before: it's asked again.
                            typed = {c.key for c in back.corrections}
                            beside.end(Ending.TURNED, dropped=tuple(dict.fromkeys(
                                c.key for c in back.taken if c.key not in typed)))
                        # With what was corrected before it, less what came with the send-back.
                        before = (parts, list(orientations),
                                  [c for c in back.standing if c.key != ORIENTATION])
                        undone = ""
                        earlier = [c for c in back.corrections if c.key != ORIENTATION]
                        at = back.part - 1
                        part = parts[at]
                        chosen = next(c for c in part.candidates
                                      if pose_value(c.rotation_deg) == back.to.value)
                        orientations[at] = Outcome(
                            proposal=Chosen(value=chosen, source="sponsor", rationale=(
                                f"sent back at the review, by you: {back.to.reason}")),
                            accepted=True, attempts=0,
                        )
                        parts = (*parts[:at],
                                 part.model_copy(update={"sent_back": (*part.sent_back, back.to)}),
                                 *parts[at + 1:])
                        continue
                    break
            finally:
                if beside is not None:
                    beside.close()  # however it ended, no call is left running
            done.update(reviewed)
        else:
            overhangs = any(o.proposal.value.overhang_area_mm2 > 0 for o in orientations)
            active = plate_job.by_object(
                scoring_profiles(profiles, outdir) if overhangs else profiles, outdir / "plate")
            result = slicing(to_place)(active)
            reading = _read(result)
            done["slice_reading"] = reading
            done["slice_flags"] = (
                flags(reading, None, *_presets(active), read.placed, tops=_tops(layouts[-1]))
                if reading else ()
            )
        layout = layouts[-1]
        result = name_gcode(result, job_id=state["job_id"],
                            part=f"{len(parts)}parts-{Path(parts[0].name).stem}")
        return {
            "gcode_path": str(result.gcode_path),
            "slice": result,
            "applied_orientation": AppliedOrientation(
                rationale=f"printed {layout.sequence}; {plate_job.poses_said(turned)}"),
            "profiles_used": profiles.used,
            "parts": turned,
            "orientations": tuple(orientations),
            "plate": layout,
            **done,
        }

    def plate_settings_beside(
        beside: Beside, made: Proposer, intake: Intake, features: tuple[Feature, ...],
        context: PartContext | None, faces: Seen | None,
    ) -> Callable[[], None]:
        """Begins a plate's settings call beside its review (mqucifer/print-shop#109,
        mqucifer/print-shop#242). Its pose is the plate's, as laid out: each part's own is said
        at the review, and a send-back asks again."""
        assert profiles is not None

        def find(stop: threading.Event) -> Suggested:
            return suggested(made(intake, features, profiles, context, seen=faces, stop=stop),
                             PLATE_POSE)

        return lambda: beside.begin(find, PLATE_POSE)

    def settings_beside(
        beside: Beside, intake: Intake, features: tuple[Feature, ...],
        context: PartContext | None, pose: tuple[float, float, float],
    ) -> Callable[[], None]:
        """Begins the settings call for this pose beside its review."""
        assert proposer is not None and profiles is not None

        def find(stop: threading.Event) -> Suggested:
            return suggested(proposer(intake, features, profiles, context, pose, stop=stop), pose)

        return lambda: beside.begin(find, pose)

    def loop_proposes(
        state: PrintJobState, measuring: Measuring, intake: Intake,
        features: tuple[Feature, ...], asked_in: tuple[float, float, float], kept_side: bool,
        stop: threading.Event, calls: list[LoopCall],
    ) -> Suggested:
        """The print loop beside the review (mqucifer/print-shop#242): a turn and its settings,
        proposed. Past its limit, the single calls' pose and settings, proposed the same."""
        assert loop is not None and proposer is not None and profiles is not None
        assert build_volume is not None
        outdir = outdir_root / state["job_id"]
        context = state.get("part_context")
        given = state.get("orientation_lessons", ())
        _say_waiting(measuring.pending(), "the print loop chooses among all six, beside the review")
        poses = measuring.all_six()
        found = run_loop(loop, Part(
            stl=Path(state["stl_path"]),
            facts=proposer.facts(intake, features, profiles, context),
            candidates=poses, build_volume_mm=build_volume,
            skill=load(groups_for(intake, features)), profiles=profiles,
            slicer=Fitting(slicer, build_volume),
            workdir=outdir / "loop", ledger=ledger, orienting=given,
            lessons=proposer.lessons_for(features, profiles),
            kept=asked_in,
            kept_from=context.down_from if kept_side and context is not None else "",
            proposes=True,
        ), stop, calls)
        record: dict[str, Any] = {"loop": found.calls, "loop_ending": (
            f"{found.ended}, after {found.model_calls} calls in {found.seconds:g} s")}
        if stop.is_set():
            return Suggested(pose=asked_in, ending=Ending.WENT_ON, **record)
        by = f"loop:{loop.model}"
        pose: Orientation | None = None
        why = ""
        makes = proposer
        if found.proposed:
            assert found.pose is not None
            pose, why = found.pose, found.reply.get("reasoning", "") or "the print loop chose it"
            reply = found.reply
            makes = dataclasses.replace(proposer, ask=lambda *_: reply, llm=None)
        elif not kept_side:
            # Past its limit, or the model unreachable: the single orientation call's pose.
            decided = orient_by_call(OrientationRequest(
                candidates=poses, build_volume_mm=build_volume,
                lessons=tuple(g.line() for g in given)), stop)
            if stop.is_set():  # went on while it ran: nothing more is asked of the model
                return Suggested(pose=asked_in, ending=Ending.WENT_ON, **record)
            if decided.accepted:
                pose, by = decided.proposal.value, decided.proposal.source
                why = decided.proposal.rationale
        turn: Change | None = None
        measured, at = features, asked_in
        if pose is not None and pose.rotation_deg != asked_in:
            at = pose.rotation_deg
            value = pose_value(at)
            turn = Change(
                key=ORIENTATION, preset=pose_value(asked_in), value=value, reason=why[:600],
                source=Source.MODEL, knowledge=by, reading=found.big.get(ORIENTATION, ""),
                feature=", ".join(state.get("laid_traits", ())),
            )
            posed = (state["stl_path"] if at == LAID else write_rotated(
                state["stl_path"], at, outdir / "suggested" / f"{value}.stl"))
            measured = measure(posed, pose.bed_contact_area_mm2)
        if stop.is_set():
            return Suggested(pose=asked_in, ending=Ending.WENT_ON, **record)
        made = makes(intake, measured, profiles, context, at, stop=stop)
        if found.proposed:
            looked = set(found.looked_up)
            made = made.model_copy(update={
                "model": f"loop:{loop.model}",
                "lessons": tuple(g.model_copy(update={"looked_up": g.id in looked})
                                 for g in made.lessons),
            })
        out = suggested(made, asked_in, turn=turn, big=found.big)
        return out.model_copy(update={**record, "looked_up": found.looked_up})

    def review_proposal_node(state: PrintJobState) -> dict[str, Any]:
        """Gate for a non-manifold mesh, or no pose that passed its check: stop for a human
        decision.

        The payload carries why it stopped and the mesh facts a human needs to decide whether
        to retry (route back to slice) or abandon (END).
        """
        mesh = state["mesh"]
        reason = "Mesh is not manifold; cannot be trusted to slice."
        if mesh.is_manifold:
            # From the orientation's check, which gave the mesh's reason until
            # mqucifer/print-shop#200.
            outcome = state.get("orientation")
            found = outcome.outstanding if outcome is not None else ()
            why = "; ".join(v.detail for v in found if v.detail)
            reason = "No pose passed its check, so none was chosen" + (f": {why}" if why else "")
        decision = interrupt(
            {
                "gate": Gate.REVIEW_PROPOSAL.value,
                "job_id": state["job_id"],
                "is_manifold": mesh.is_manifold,
                "volume_mm3": mesh.volume_mm3,
                "surface_area_mm2": mesh.surface_area_mm2,
                "bbox_mm": list(mesh.bbox_mm),
                "overhang_area_mm2": mesh.overhang_area_mm2,
                "reason": reason,
            }
        )
        approved = (
            bool(decision.get("approved", False)) if isinstance(decision, dict) else bool(decision)
        )
        gate_decision = GateDecision(
            gate=Gate.REVIEW_PROPOSAL,
            approved=approved,
            at=datetime.now(UTC).isoformat(),
            note=str(decision.get("note", "")) if isinstance(decision, dict) else "",
        )
        return {"gate_decisions": (state.get("gate_decisions", ()) + (gate_decision,))}

    def before_print_node(state: PrintJobState) -> dict[str, Any]:
        """Gate before the hardware boundary: stop for a human decision.

        The payload carries the G-code path, print time, and filament volume —
        the numbers a human needs to decide whether to print.
        """
        slice_result: SliceResult = state["slice"]
        applied_orientation = state.get("applied_orientation", AppliedOrientation())
        plate = state.get("plate")
        several: dict[str, Any] = {}
        if plate is not None:
            # No one turn is the plate's: each part's is in the rationale.
            several = {"orientation_deg": None, "plate": plate.headline(),
                       "plate_lines": plate.lines()}
        decision = interrupt(
            {
                "gate": Gate.BEFORE_PRINT.value,
                "job_id": state["job_id"],
                "gcode_path": str(slice_result.gcode_path),
                "print_time_s": slice_result.print_time_s,
                "filament_cm3": slice_result.filament_cm3,
                "layer_count": slice_result.layer_count,
                "max_z_mm": slice_result.max_z_mm,
                "orientation_deg": list(applied_orientation.as_printed()),
                "orientation_rationale": applied_orientation.rationale,
                # A pause the review asked for, where the slice put it, so the person is at
                # the printer for it (mqucifer/print-shop#119), what it's for, and the spool a
                # swap's print starts on (mqucifer/print-shop#218).
                "pause": _pauses_said(_reading_now(state, ledger)),
                "pause_for": _pause_for(state),
                "spools": _starts_said(state, ledger),
                **several,
                # That the person went on without the model's settings, where they did
                # (mqucifer/print-shop#242).
                "without": _without_said(state.get("proposal")),
            }
        )
        approved = (
            bool(decision.get("approved", False)) if isinstance(decision, dict) else bool(decision)
        )
        gate_decision = GateDecision(
            gate=Gate.BEFORE_PRINT,
            approved=approved,
            at=datetime.now(UTC).isoformat(),
            note=str(decision.get("note", "")) if isinstance(decision, dict) else "",
        )
        return {"gate_decisions": (state.get("gate_decisions", ()) + (gate_decision,))}

    def print_node(state: PrintJobState) -> dict[str, Any]:
        """Hardware boundary: upload the G-code and start the print.

        Six refusals stand between this being called and the nozzle moving,
        and each one raises rather than continuing:

        1. **The gate decision is re-checked here.** ``ungated_edges`` proves
           no *edge* reaches this node without passing ``Gate.BEFORE_PRINT``,
           which is a property of the topology — not of the decision sitting in
           state. Commit d6374b9 fixed two ways a decision could reach a gate it
           was not made for; this is the same class of bug one node further on.
        2. **The spool's tuning is the one the job was prepared with**
           (mqucifer/print-shop#220), read again here, as it can be edited
           while the job waits. A colour corrected meanwhile is what's recorded.
        3. **What's loaded is what the job was prepared for, or a swap's first spool**
           (mqucifer/print-shop#218), and what's on is known. The gate holds an
           Approve for the same (`why_not_start`); this is the backstop, read again after the
           upload.
        4. **Preflight** — the printer must be free and have room, and the
           G-code must exist. A pure validator over a typed snapshot.
        5. **The uploaded name must carry this job's id.** ``upload_file``
           returns the name the *printer* chose, which need not be the name we
           sent. If the round-trip fails we are about to print a file we cannot
           account for, so we do not.
        6. **The start is confirmed by watching the printer's own status**,
           not by the command being acknowledged.

        Never returns a fake success: the previous implementation set
        ``print_status = "done"`` unconditionally, which made a pipeline that
        had never run look like it worked.
        """
        decisions = state.get("gate_decisions", ())
        if not approved_at(decisions, Gate.BEFORE_PRINT):
            last = decisions[-1] if decisions else None
            raise RuntimeError(
                f"print node reached without an approval at {Gate.BEFORE_PRINT.value}; "
                f"last decision was {last!r}. Refusing to command the printer."
            )

        if printer is None:
            raise RuntimeError(
                "this graph was built without a printer, so it cannot command "
                "one. Pass `printer=` to build_graph — `live_printer()` for the "
                "real machine. Refusing to arm hardware that nobody asked for."
            )
        client: PrinterClient = printer
        if ledger is None:
            raise RuntimeError(
                "this graph was built without a ledger. Every print the agent "
                "starts is recorded (mqucifer/print-shop#2); refusing to start "
                "one that would not be."
            )

        material = _as_started(state, ledger, "nothing was uploaded or printed")
        spools: tuple[SpoolUse, ...] = ()
        sent: PrintJobState = state

        gcode = Path(state["gcode_path"])
        snapshot = client.snapshot()
        verdict = preflight(snapshot, gcode)
        if not verdict.accepted:
            raise PreflightFailed(verdict)

        taken = state.get("sliced")
        if taken is not None:
            # A hand-sliced file is sent as it was read: hashed again, last thing before the
            # upload (mqucifer/print-shop#164).
            hand_sliced.verify(gcode, taken.file.sha256)
        remote_name = client.upload(gcode)
        if job_id_from_filename(remote_name) != state["job_id"]:
            raise RuntimeError(
                f"the printer stored the upload as {remote_name!r}, which does not "
                f"carry job {state['job_id']}. Refusing to start a print of a file "
                f"this job cannot account for."
            )

        # Again, as the upload takes a while and stock.py runs beside it.
        material = _as_started(state, ledger, "the file was uploaded, and not started")
        swap = _swap(state)
        if material is not None and swap is not None and swap.start and swap.to:
            with Inventory(ledger.path) as inventory:
                to = swap.to.of(inventory.spool(swap.to.spool_id))
            change = _standing(state)
            assert change is not None
            started = swap.model_copy(update={"start": material, "to": to})
            # The reading names the spools as the print starts, as the row's spools do.
            reading = pauses.marked(state.get("slice_reading"),
                                    change.model_copy(update={"pause": started}))
            spools = pauses.uses(started, reading)
            sent = {**state, "slice_reading": reading}

        # Written before the printer is told anything, so a start that fails
        # halfway is still on record.
        rows = (
            plate_job.records(sent, remote_name, parse_config_block(gcode), snapshot.machine,
                              snapshot.firmware, material, spools)
            if state.get("parts")
            else (_print_record(sent, remote_name, gcode, snapshot.machine, snapshot.firmware,
                                store, material, spools),)
        )
        ledger.record_start_requested(*rows, loaded=material)
        try:
            client.start(remote_name)
        except Exception as exc:
            ledger.record_start_failed(state["job_id"], f"{type(exc).__name__}: {exc}")
            raise
        ledger.record_started(state["job_id"])
        return {
            "print_status": PrintStatus.PRINTING,
            "print_attempts": state.get("print_attempts", 0) + 1,
        }

    # --- Routing functions ---

    def route_entry(state: PrintJobState) -> str:
        """A hand-sliced file goes straight to be taken; a part is ingested."""
        return "sliced" if state.get("sliced") is not None else "ingest"

    def route_after_ingest(state: PrintJobState) -> str:
        """Route to orient if manifold, else to the review gate. A job of several parts was
        refused at ingest if one isn't (mqucifer/print-shop#109)."""
        if state.get("parts") or state["mesh"].is_manifold:
            return "orient"
        return Gate.REVIEW_PROPOSAL.value

    def route_after_orient(state: PrintJobState) -> str:
        """Route to slice if the orientation was accepted, else to the review gate. A job of
        several parts ended at orient if one wasn't."""
        if state.get("parts") or state["orientation"].accepted:
            return "slice"
        return Gate.REVIEW_PROPOSAL.value

    def route_after_review(state: PrintJobState) -> str:
        """Route to slice if the human approved, else END."""
        if approved_at(state.get("gate_decisions", ()), Gate.REVIEW_PROPOSAL):
            return "slice"
        return END

    def route_after_before_print(state: PrintJobState) -> str:
        """Route to print only on an approval made at this gate, else END."""
        if approved_at(state.get("gate_decisions", ()), Gate.BEFORE_PRINT):
            return "print"
        return END

    # --- Build the graph ---

    graph = StateGraph(PrintJobState)

    graph.add_node("sliced", sliced_node)
    graph.add_node("ingest", ingest_node)
    graph.add_node("orient", orient_node)
    graph.add_node("slice", slice_node)
    graph.add_node(Gate.REVIEW_PROPOSAL.value, review_proposal_node)
    graph.add_node(Gate.BEFORE_PRINT.value, before_print_node)
    graph.add_node("print", print_node)

    graph.add_conditional_edges(START, route_entry, {"ingest": "ingest", "sliced": "sliced"})
    graph.add_edge("sliced", Gate.BEFORE_PRINT.value)
    graph.add_conditional_edges(
        "ingest",
        route_after_ingest,
        {"orient": "orient", Gate.REVIEW_PROPOSAL.value: Gate.REVIEW_PROPOSAL.value},
    )
    graph.add_conditional_edges(
        "orient",
        route_after_orient,
        {"slice": "slice", Gate.REVIEW_PROPOSAL.value: Gate.REVIEW_PROPOSAL.value},
    )
    graph.add_conditional_edges(
        Gate.REVIEW_PROPOSAL.value,
        route_after_review,
        {"slice": "slice", END: END},
    )
    graph.add_edge("slice", Gate.BEFORE_PRINT.value)
    graph.add_conditional_edges(
        Gate.BEFORE_PRINT.value,
        route_after_before_print,
        {"print": "print", END: END},
    )
    graph.add_edge("print", END)

    return graph.compile(checkpointer=checkpointer)


#: The settings the guard that supports are tree reads.
_SUPPORTS = ("enable_support", "support_type")


def _held_tree(asked: Proposal, process: dict[str, Any]) -> Proposal:
    """The guard that supports are always tree, over the model's settings the person took, at
    this review or by `use` (mqucifer/print-shop#242), as it holds over what the model set. A
    support type the person typed, or used from their own, stands."""
    of_model = any(c.key in _SUPPORTS and (c.taken or (c.used is not None and c.used.of_model))
                   for c in asked.changes)
    typed = any(c.key == "support_type" and c.source is Source.SPONSOR
                and (c.used is None or not c.used.of_model) for c in asked.changes)
    if not of_model or typed:
        return asked
    return asked.model_copy(update={"changes": tuple(tree_supports(
        list(asked.changes), lambda key: str(process.get(key, ""))))})


def _without_said(proposal: Any) -> str:
    """That the review went on without the model's settings, in a sentence, or ""."""
    found = getattr(proposal, "suggested", ()) or ()
    return went_on(found[-1]) if found else ""


def _objects(violation: Any, own: Orientation, found: tuple[Orientation, ...]) -> str:
    """What the check on poses has against the file's own, as the review says it."""
    if violation.rule != "orientation.overhang_regression":
        return str(violation.detail)
    less = next((c for c in found if c.rotation_deg == violation.suggested), None)
    if less is None:
        return str(violation.detail)
    return (f"another pose has less to support: {less.overhang_area_mm2:g} mm2, "
            f"where this has {own.overhang_area_mm2:g} mm2")


def _pauses_said(reading: Any) -> str:
    """Where the slice pauses and what for, in a sentence, or ""."""
    found = getattr(reading, "pauses", ()) or ()
    return "; ".join(f"this print {p.line()}" for p in found)


def _standing(state: PrintJobState) -> Change | None:
    """The pause that stood at the review, or None."""
    proposal = state.get("proposal")
    change = proposal.get(PAUSE) if proposal is not None else None
    return change if change is not None and change.value != "none" else None


def _reading_now(state: PrintJobState, ledger: Ledger | None) -> Any:
    """The slice's reading with the spool a swap is to named as the stock has it now: the person
    loads by those words."""
    reading = state.get("slice_reading")
    change, swap = _standing(state), _swap(state)
    if ledger is None or change is None or swap is None or swap.to is None:
        return reading
    try:
        with Inventory(ledger.path) as inventory:
            now = swap.model_copy(update={"to": swap.to.as_now(inventory.spools())})
    except sqlite3.Error as exc:
        logger.warning("the stock couldn't be read for the gate's pause: %s", exc)
        return reading
    return pauses.marked(reading, change.model_copy(update={"pause": now}))


def _pause_for(state: PrintJobState) -> str:
    """What the job's pause is for, or "" where it doesn't say (mqucifer/print-shop#218)."""
    change = _standing(state)
    return change.pause.kind.value if change is not None and change.pause is not None else ""


def _swap(state: PrintJobState) -> pauses.Pause | None:
    change = _standing(state)
    pause = change.pause if change is not None else None
    return pause if pause is not None and pause.kind is pauses.PauseFor.SWAP else None


def _first_spool(state: PrintJobState) -> tuple[Material | None, int | None]:
    """The spool a job starts on, as it was prepared, and the one it mustn't: a swap's second."""
    swap = _swap(state)
    if swap is not None and swap.start is not None and swap.to is not None:
        return swap.start, swap.to.spool_id
    return _material_prepared(state), None


def why_not_start(state: PrintJobState, ledger: Ledger | None) -> str:
    """Why an Approve mustn't start this job with the stock as it is, and what to do, or ""
    (mqucifer/print-shop#218). The daemon reads it before it resumes the gate; the print node
    refuses the same, as the backstop. A job with no stock set up has none."""
    first, not_on = _first_spool(state)
    if ledger is None:
        return ""
    if first is None:
        return _stock_since(state, ledger)
    with Inventory(ledger.path) as inventory:
        held = inventory.why_not_start(
            first, not_on=not_on, prepared=_material_prepared(state),
            by_hand=state.get("sliced") is not None,
        )
        if held is None:
            return ""
        needed = ""
        spools = inventory.spools()
        if inventory.unknown(first.printer_id) is not None and any(
            spool.id == first.spool_id for spool in spools
        ):
            # What's on may not be what this job needs: said now, not at a second hold.
            needed = (f". This job needs {first.as_now(spools).named()}: if another's on, put that "
                      f"on first and record it (`stock.py load {first.spool_id}`)")
    return held.at_gate() + needed


def _stock_since(state: PrintJobState, ledger: Ledger) -> str:
    """Why a swap prepared with no stock can't start once there is one: it could record neither
    its spools nor that what's on isn't known afterwards."""
    if _swap(state) is None:
        return ""
    with Inventory(ledger.path) as inventory:
        if not inventory.printers():
            return ""
    return ("the stock was set up after this job was prepared, and couldn't follow its spools: "
            "Reject and run the job again")


def _as_started(state: PrintJobState, ledger: Ledger, done: str) -> Material | None:
    """The spool the print starts on, as it starts, or None with no stock.

    Raises:
        InventoryError: As `why_not_start` says.
    """
    first, not_on = _first_spool(state)
    if first is None:
        if why := _stock_since(state, ledger):
            raise InventoryError(f"{why}, so {done}.")
        return None
    with Inventory(ledger.path) as inventory:
        return inventory.at_start(
            first, not_on=not_on, prepared=_material_prepared(state), done=done)


def _recorder(
    ledger: Ledger | None, profiles: SlicerProfiles
) -> Callable[[int, Asked], None] | None:
    """Records a load the person says, at the review (`review._loaded_first`), and the print
    they said is over in the same word."""
    material = _material_of(profiles)
    if ledger is None or material is None:
        return None
    path, printer = ledger.path, material.printer_id

    def record(spool_id: int, asked: Asked) -> None:
        with Inventory(path) as inventory:
            inventory.load(spool_id, printer, over=asked.not_known, asked=asked)

    return record


def _starts_said(state: PrintJobState, ledger: Ledger | None) -> str:
    """For the gate: the spool a swap's print starts on and whether it's loaded, and for any job
    that what's on isn't known, or ""."""
    if ledger is None:
        return ""
    first, _ = _first_spool(state)
    if first is None:
        return ""
    swap = _swap(state)
    try:
        with Inventory(ledger.path) as inventory:
            unknown = inventory.unknown(first.printer_id)
            if swap is not None and swap.start is not None:
                return pauses.starting(swap, inventory.spools(), unknown.why() if unknown else "")
    except sqlite3.Error as exc:
        # Only said here: the gate's check and the print node read it again, and refuse unread.
        logger.warning("the stock couldn't be read for the gate: %s", exc)
        return f"the stock couldn't be read ({exc}): an Approve checks it again"
    if unknown is None:
        return ""
    return f"{unknown.why()}. {unknown.hold().do}: an Approve before that starts nothing"


def _stands(ledger: Ledger | None, profiles: SlicerProfiles) -> Callable[[], Asked] | None:
    """The stock's changes and what isn't known, for the review, read as it's needed."""
    material = _material_of(profiles)
    if ledger is None or material is None:
        return None
    path, printer = ledger.path, material.printer_id

    def stands() -> Asked:
        with Inventory(path) as inventory:
            return inventory.asked(printer)

    return stands


def _material_of(profiles: SlicerProfiles) -> Material | None:
    return profiles.used.material if profiles.used is not None else None


def _stock(ledger: Ledger | None, profiles: SlicerProfiles) -> pauses.Stock | None:
    """The spools on hand, for a swap to name its two, read each time they're needed."""
    if ledger is None or _material_of(profiles) is None:
        return None
    path = ledger.path

    def on_hand() -> tuple[Any, ...]:
        with Inventory(path) as inventory:
            return tuple(inventory.spools())

    return on_hand


def _material_prepared(state: PrintJobState) -> Material | None:
    """The spool, nozzle and printer the job was prepared with, if the inventory gave them."""
    job = state.get("sliced")
    if job is not None:
        return job.material
    used = state.get("profiles_used")
    return used.material if used is not None else None


def _print_record(
    state: PrintJobState, remote_name: str, gcode: Path, machine: str, firmware: str,
    store: Store | None = None, material: Material | None = None,
    spools: tuple[SpoolUse, ...] = (),
) -> PrintRecord:
    """The ledger's row for this job, from state and the G-code Orca wrote.

    `material` is the spool as the print starts (`Inventory.at_start`), and `spools` a swap's,
    in order (mqucifer/print-shop#218).
    """
    slice_result: SliceResult = state["slice"]
    job = state.get("sliced")
    if job is not None:
        # A hand-sliced file: the part is the file, its hash the hash of what was sent, and
        # nothing of the loop's is in the row (mqucifer/print-shop#164).
        return PrintRecord(
            job_id=state["job_id"],
            stl_name=job.name,
            stl_sha256=job.file.sha256,
            rotation_deg=(0.0, 0.0, 0.0),
            orientation_source=hand_sliced.SOURCE,
            orientation_rationale=hand_sliced.RATIONALE,
            orientation_accepted=True,
            gcode_name=remote_name,
            print_time_s=slice_result.print_time_s,
            filament_cm3=slice_result.filament_cm3,
            layer_count=slice_result.layer_count,
            slicer_config=parse_config_block(gcode),
            printer_machine=machine,
            printer_firmware=firmware,
            reading=state.get("slice_reading"),
            flags=tuple(state.get("slice_flags", ())),
            sliced=job.record(store),
            material=material,
        )
    applied = state.get("applied_orientation", AppliedOrientation())
    outcome = state.get("orientation")
    accepted = outcome is not None and outcome.accepted
    used = state.get("profiles_used")
    if used is not None:
        # The row keeps the spool twice, here and beside the print, and they agree.
        used = used.model_copy(update={"material": material})
    return PrintRecord(
        job_id=state["job_id"],
        stl_name=Path(state["stl_path"]).name,
        stl_sha256=state.get("stl_sha256", ""),
        rotation_deg=applied.as_printed(),
        orientation_source=outcome.proposal.source if accepted and outcome else "authored",
        orientation_rationale=applied.rationale,
        orientation_accepted=accepted,
        gcode_name=remote_name,
        print_time_s=slice_result.print_time_s,
        filament_cm3=slice_result.filament_cm3,
        layer_count=slice_result.layer_count,
        slicer_config=parse_config_block(gcode),
        printer_machine=machine,
        printer_firmware=firmware,
        profiles=used,
        intake=state.get("intake"),
        proposal=state.get("proposal"),
        corrections=tuple(state.get("corrections", ())),
        features=tuple(state.get("features", ())),
        reading=state.get("slice_reading"),
        flags=tuple(state.get("slice_flags", ())),
        spools=spools,
    )


def _resolved(
    outcome: Outcome[Orientation], found: tuple[Orientation, ...]
) -> Outcome[Orientation]:
    """An accepted pose with its own figures, and, where the validator moved it, saying why.

    The corrector rewrites only the rotation (`contracts.default_corrector`), so a corrected pose
    carried the figures of the one it replaced: the review showed a lid laid flat as 100 mm tall,
    and its features were measured with the edge's area on the plate (mqucifer/print-shop#128).
    """
    if not outcome.accepted:
        return outcome
    chosen = next(c for c in found if c.rotation_deg == outcome.proposal.value.rotation_deg)
    rationale = outcome.proposal.rationale
    if outcome.proposal.source == "corrector" and outcome.history:
        moved = "; ".join(v.detail for v in outcome.history[0].blockers if v.detail)
        rationale = f"moved by the validator: {moved}. The model had said: {rationale}"
    return dataclasses.replace(
        outcome, proposal=Chosen(value=chosen, source=outcome.proposal.source,
                                 rationale=rationale, confidence=outcome.proposal.confidence),
    )


#: The source of an orientation the print loop will choose after the questions.
LOOP_PENDING = "loop:pending"

#: The source of a plate's part's pose whose orientation call waits till after the questions
#: (mqucifer/print-shop#208), and of one that kept the pose they were asked of.
POSE_AFTER_QUESTIONS = "plate:pending"
#: What a plate's settings are proposed for, beside its review: the plate as laid out.
PLATE_POSE = (0.0, 0.0, 0.0)
POSE_KEPT = "plate:kept"

#: Where the pose the review shows came from, where the file's own stood and the print loop's
#: was a proposal (mqucifer/print-shop#242).
AS_FILED = "as_filed"

#: Where an orientation kept as the person laid it came from, as the ledger keeps it.
AS_LAID = "as_laid"

#: Where an orientation kept on the side the part's context lays it on came from
#: (mqucifer/print-shop#130).
CONTEXT = "context"


def _kept(
    context: PartContext | None, found: tuple[Orientation, ...],
    build_volume: tuple[float, float, float] | None,
) -> Orientation | None:
    """The candidate that lays the part on the side its context gives, if it fits the plate."""
    if context is None or not context.down:
        return None
    rotation = pose_for(context.down)
    pose = next((c for c in found if c.rotation_deg == rotation), None)
    if pose is None or (build_volume is not None and not pose.fits_in(build_volume)):
        return None
    return pose


def _as_laid(request: OrientationRequest) -> Outcome[Orientation]:
    """The file's own pose, kept because the person laid it for printing (mqucifer/print-shop#128).

    No model is asked: the person decided. The pose must still fit the plate.

    Raises:
        RuntimeError: If, laid as it is, the part doesn't fit the build volume.
    """
    laid = next(c for c in request.candidates if c.rotation_deg == (0.0, 0.0, 0.0))
    if not laid.fits_in(request.build_volume_mm):
        raise RuntimeError(
            f"laid as it is, the part ({laid.bbox_mm} mm) doesn't fit the build volume "
            f"{request.build_volume_mm} mm; print it without --as-laid"
        )
    return Outcome(
        proposal=Chosen(
            value=laid, source=AS_LAID,
            rationale="kept as laid, by you (--as-laid): no orientation model was asked",
        ),
        accepted=True,
        attempts=0,
    )


def _ask(
    asked: Any, asker: Asker, slice_with: Any, two: tuple[Question, Question] = STOCK,
) -> Intake:
    """The questions with Orca's estimate for each option, asked, with the answers. print-shop's
    two are as the part's notes leave them (`questions.stock`, mqucifer/print-shop#236)."""
    purpose, seen_faces = two
    questions: tuple[Question, ...] = tuple(
        purpose if q == PURPOSE
        else seen_faces if q == SEEN
        # The model's own purpose question has the notes' words beside it as well.
        else q.model_copy(update={"beside": purpose.beside}) if q.feature == "purpose"
        else q
        for q in asked.questions
    )
    if not questions and any(q.beside or q.noted is not None for q in two):
        questions = two
    questions = _priced(questions, slice_with)
    return Intake(questions=questions, answers=asker(questions), note=asked.note,
                  call=asked.call)


def _priced(questions: tuple[Question, ...], slice_with: Any) -> tuple[Question, ...]:
    """The questions with Orca's estimate for each option."""
    counter = iter(range(1, 1000))
    return estimate(questions, lambda settings: slice_with(settings, next(counter)))


def _estimate(
    slicer: Slicer, stl: str | Path, profiles: SlicerProfiles, settings: dict[str, str],
    outdir: Path,
) -> Estimate | None:
    """Orca's estimate for the presets with `settings` over them, or None if it couldn't."""
    changes = tuple(Change(key=k, preset="", value=v, reason="", source=Source.ANSWER)
                    for k, v in settings.items())
    try:
        result = slicer(stl, apply(profiles, Proposal(changes=changes), outdir), outdir)
    except (RuntimeError, OSError, ValueError):
        return None
    return Estimate(print_time_s=result.print_time_s, filament_g=result.filament_g,
                    filament_cm3=result.filament_cm3)


def _say_waiting(waited: Iterable[tuple[float, float, float]], why: str) -> None:
    """A wait on poses still being measured, said on the terminal and in the job's log as it
    begins (mqucifer/print-shop#235). The terminal says it as `orientation`'s wait is said, among
    the screens, and the log keeps its line (mqucifer/print-shop#223)."""
    left = list(waited)
    if left:
        them = "pose" if len(left) == 1 else f"{len(left)} poses"
        names = "; ".join(map(pose_value, left))
        logger.info("waiting for the %s still being measured (%s): %s", them, names, why,
                    extra={SAID: f"Waiting for the {them} still being measured ({names}): "
                                 f"{why}."})


class _Turned(Exception):  # noqa: N818 - a signal, not an error
    """The pose was sent back at the review: turn the part to it, and propose and slice again."""

    def __init__(
        self, to: Change, corrections: list[Change], offering: Offering | None = None,
        taken: list[Change] | None = None, part: int | None = None,
        standing: tuple[Change, ...] = (),
    ) -> None:
        super().__init__(f"turned to {to.value}")
        self.to = to
        self.corrections = corrections
        self.offering = offering
        #: A plate's: whose pose it is (mqucifer/print-shop#109).
        self.part = part
        #: The corrections that stood before the answer that sent it back.
        self.standing = standing
        #: What was taken of the model's in the pose left, which isn't carried.
        self.taken = taken or []


def _offering(
    ledger: Ledger, state: PrintJobState, posed: str | Path, features: Any,
    applied: AppliedOrientation, profiles: SlicerProfiles,
    build_volume: tuple[float, float, float] | None, earlier: list[Change], intake: Intake,
    measuring: Measuring | None = None, chose: str = "",
) -> Offering:
    """What decided the past prints most like this part, in this pose (mqucifer/print-shop#211).
    Never in a print's way: what fails is said at the review, and nothing is offered."""
    try:
        process, filament = _presets(profiles)
        own = filament.get("filament_settings_id")
        # As a past print names it: the store's name, the preset's, or the one Orca writes.
        names = [profiles.used.filament.name if profiles.used is not None else "",
                 str(filament.get("name") or ""),
                 str(own[0] if isinstance(own, list) and own else own or "")]
        chosen = ""
        if chose:
            chosen = chose
        elif any(c.key == ORIENTATION and not c.said_no_to for c in earlier):
            chosen = "your correction at this job"
        elif state.get("as_laid"):
            chosen = "kept as laid"
        on_hand = _stock(ledger, profiles)

        def lying(value: str) -> Orientation | None:
            if measuring is None:
                return None
            waited = [r for r in measuring.pending() if pose_value(r) == value]
            _say_waiting(waited, "a past print was in it, and its offer compares them in it")
            return measuring.lying(value)

        return offer_past_prints(ledger, ThisPart(
            stl=Path(posed), stl_name=Path(state["stl_path"]).name,
            sha256=state.get("stl_sha256", ""), features=features,
            rotation_deg=applied.rotation_deg, height_mm=applied.height_mm or 0.0,
            machine=json.loads(profiles.machine.read_text()), process=process,
            filament=filament, filament_names=tuple(dict.fromkeys(n for n in names if n)),
            measured=lying, build_volume_mm=build_volume,
            context=state.get("part_context"), pose_chosen=chosen,
            material=_material_of(profiles), on_hand=on_hand() if on_hand else (),
            asked=intake.could_set(), unwaited=intake.unwaited,
            seen_in=lambda pose: carried(intake, state.get("part_context"), pose),
        ))
    except Exception as exc:  # noqa: BLE001 - a print is never stopped by what it might reuse
        logger.warning("the past prints couldn't be read for the offer: %s", exc)
        return Offering(failed=f"{type(exc).__name__}: {exc}"[:300])


def _tops(plate: PlateLayout) -> tuple[int, ...]:
    """The layers the plate's parts each have their own top on."""
    return tuple(o.top_layer for o in plate.objects)


@dataclasses.dataclass(frozen=True)
class _PlateReview:
    """What the review of a plate of several has that one part's doesn't
    (mqucifer/print-shop#109)."""

    layout: Callable[[], PlateLayout]  # the slice on show's
    read: plate_job.Together
    parts: tuple[ReviewPart, ...]
    undone: str = ""  # why the send-back before this review was taken back
    #: What's seen, each part's carried from the pose it was asked in and read as one, and the
    #: review's lines on it (mqucifer/print-shop#248).
    seen: Seen | None = None
    seen_lines: tuple[str, ...] = ()
    #: Which faces are seen was answered and can't be carried to how the parts lie now.
    unjudged: bool = False


def _with_orienting(proposal: Proposal, given: tuple[GivenLesson, ...]) -> Proposal:
    """The proposal with the lessons the orientation step was given listed beside its own, so
    the review says which it was given and the ledger keeps them (mqucifer/print-shop#128)."""
    if not given:
        return proposal
    marked = tuple(g.model_copy(update={"applied": False,
                                        "why": "given to the orientation step with its poses"})
                   for g in given)
    return proposal.model_copy(update={"lessons": proposal.lessons + marked})


def _lessons_for(
    ledger: Ledger | None, stl: str | Path, profiles: SlicerProfiles, outdir: Path,
) -> Any:
    """The kept lessons for the part in a pose, on the print's filament: what a way by settings
    offers at the review, read in the pose the person chose (mqucifer/print-shop#128)."""
    filament = _presets(profiles)[1].get("filament_type", "")
    kind = str(filament[0] if isinstance(filament, list) and filament else filament)
    material = _material_of(profiles)  # its variety, for a filament's lessons
    variety = material.variety if material is not None else ""

    def found(pose: Orientation) -> tuple[GivenLesson, ...]:
        if ledger is None:
            return ()
        turned = stl if pose.rotation_deg == (0.0, 0.0, 0.0) else write_rotated(
            stl, pose.rotation_deg, outdir / f"{pose_value(pose.rotation_deg)}.stl")
        return print_lessons.for_part(
            ledger, measure(turned, pose.bed_contact_area_mm2), kind, variety)

    return found


def _coster(
    slicer: Slicer, stl: str | Path, profiles: SlicerProfiles, proposal: Proposal, outdir: Path,
) -> Any:
    """Orca's estimate with some changes over the proposal: the price of a way by settings."""
    counter = iter(range(1, 1000))
    base = placeable(proposal, *_presets(profiles))

    def cost(changes: tuple[Change, ...]) -> Estimate | None:
        out = outdir / str(next(counter))
        try:
            result = slicer(stl, apply(profiles, base.corrected(list(changes)), out), out)
        except (RuntimeError, OSError, ValueError):
            return None
        return Estimate(print_time_s=result.print_time_s, filament_g=result.filament_g,
                        filament_cm3=result.filament_cm3)

    return cost


def _propose_and_review(
    proposal: Any,
    profiles: SlicerProfiles,
    slice_with: Any,
    reviewer: Reviewer | None,
    orientation: AppliedOrientation,
    workdir: Path,
    intake: Intake | None = None,
    *,
    measuring: Measuring | None = None,
    laid_traits: tuple[str, ...] = (),
    cost: Any = None,
    lessons_for: Any = None,
    earlier: list[Change] | None = None,
    context: PartContext | None = None,
    stl: str | Path | None = None,
    offering: Offering | None = None,
    doubting: Doubting | None = None,
    plate: _PlateReview | None = None,
    beside: Beside | None = None,
    ask_again: Callable[[], None] | None = None,
    material: Material | None = None,
    stock: pauses.Stock | None = None,
    stands: Callable[[], Asked] | None = None,
    record_load: Callable[[int, Asked], None] | None = None,
) -> tuple[SliceResult, dict[str, Any]]:
    """Slice with the proposal; with a reviewer, again after each correction until approved.

    Each slice is read layer by layer, and flagged for this part from which faces the intake
    says are seen (mqucifer/print-shop#105). A change a flag proposes is only applied when the
    Sponsor takes it at the review. `earlier` are the corrections made before the part was
    turned, kept with the job's. `plate` is a job of several parts', reviewed as one.

    `offering` is what decided the past prints most like this one (mqucifer/print-shop#211):
    none of it is applied until it's used, and picking or clearing slices nothing.

    `material` is the spool the job was prepared with and `stock` the spools on hand: what a swap
    names its two from. Each slice's reading is marked with what its pause is for
    (mqucifer/print-shop#218).

    A slice turned to fit is offered as that turn, and isn't approved until it's taken: taken,
    it stands while each slice after it needs the same turn, and a slice that fits as posed ends
    it (mqucifer/print-shop#257).

    Each change is read back from each slice (mqucifer/print-shop#206): the review, and then
    the job, have what reached it as changed and the rest as not applied, the corrections
    among them.

    `beside` is the model at work on proposals (mqucifer/print-shop#242). None is applied by
    arriving: each review has what had come as it was drawn, and an approval ends what's still
    running. A turn of the print loop's turned down is asked again for this pose, by `ask_again`.

    Raises:
        RuntimeError: If the Sponsor stops at the review, or the part fits only turned and the
            turn wasn't taken. Nothing was sent to the printer, and the approval gate was never
            reached.
        _Turned: If the pose is sent back (mqucifer/print-shop#128), with every correction so
            far: the caller turns the part and calls again.
    """
    presets = tuple(
        str(json.loads(p.read_text()).get("name", p.stem)) if p else "-"
        for p in (profiles.machine, profiles.process, profiles.filament)
    )
    process, filament = _presets(profiles)
    faces = plate.seen if plate is not None else seen(intake, orientation.rotation_deg)
    corrections: list[Change] = list(earlier or ())
    # The part's context in this pose (mqucifer/print-shop#130): its fits as heights, for the
    # reading's seam rule and elephant foot, and what the review says was read.
    placed = (place(context, stl, orientation.rotation_deg)
              if context is not None and context.found and stl is not None else None)
    instead = intake.set_aside() if intake is not None else {}
    if placed is not None:
        # One reading with every other reader's: the notes' faces only where no answer stands in
        # their place, as words do, naming no face (mqucifer/print-shop#236,
        # mqucifer/print-shop#248).
        placed = placed.model_copy(update={
            "seen": carried(intake, context, orientation.rotation_deg)})
    prints = ""
    if context is not None and context.seen is not None and any(orientation.rotation_deg):
        turned_to = printed(context.seen, orientation.rotation_deg)
        prints = turned_to.as_printed() if turned_to is not None else "not judged"
    # The faces as they print now and, after a turn, what they were called when answered
    # (mqucifer/print-shop#248). A note, so the review, the desk and the record all have it.
    seen_now: tuple[str, ...] = (
        plate.seen_lines if plate is not None
        else tuple(filter(None, [seen_said(intake, context, orientation.rotation_deg)])))
    if seen_now:
        proposal = proposal.model_copy(update={"notes": (*seen_now, *proposal.notes)})
    said = tuple([*(context.lines(instead, prints) if context is not None else ()),
                  *(placed.lines() if placed is not None else ())])
    undone = ""
    if plate is not None:
        placed, said, undone = plate.read.placed, plate.read.said, plate.undone
        if placed is not None:
            # The notes' faces only as carried with the answers, as one part's are.
            placed = placed.model_copy(update={"seen": plate.seen})
    proposal = proposal.model_copy(update={
        "context": said, "context_from": context.path if context is not None else ""})
    asked = _held_tree(proposal, process)
    took: list[Change] = []
    taken_turn = 0.0
    fitted: tuple[Proposal, list[Change]] | None = None
    while True:
        try:
            result: SliceResult = slice_with(
                apply(profiles, placeable(asked, process, filament), workdir)
            )
        except DoesNotFit as spill:
            # A plate of several's alone (mqucifer/print-shop#109): the answer that left it
            # unsliceable is taken back, and asked again with the last that fitted.
            if fitted is None:
                raise
            asked, corrections = fitted
            fitted, undone = None, str(spill)
            continue
        fitted = (asked, list(corrections))
        config = _config(result)
        proposal = read_back(asked, process, filament, config)
        reading = pauses.marked(_read_as_sliced(result), proposal.get(PAUSE))
        if not result.turn_deg:
            # It fits as posed now: a turn needed later is asked for again.
            taken_turn = 0.0
        found = (flags(reading, faces, process, filament, placed, proposal.changes,
                       _tops(plate.layout()) if plate is not None else ())
                 if reading is not None else ())
        if plate is not None and plate.unjudged:
            found = tuple(f.model_copy(update={"reading": f.reading.replace(
                "the intake didn't say which faces are seen",
                "which faces are seen, as you said it, can't be carried to how the parts lie "
                "now")}) for f in found)
        if reviewer is None:
            if result.turn_deg:
                raise _unoffered(result)
            break
        while True:
            if offering is not None and doubting is not None:
                offering = doubting.added_to(offering)
            work = beside.work if beside is not None else None
            screen = work.now() if work is not None else None
            shown = looked_up(proposal, screen)
            review = reviewer(ReviewRequest(
                presets=(presets[0], presets[1], presets[2]),
                proposal=shown,
                slice=result,
                orientation=orientation,
                profiles=profiles,
                process=process,
                filament=filament,
                reading=reading,
                flags=found,
                candidates=measuring.ready() if measuring is not None else (),
                measuring=measuring,
                laid_traits=laid_traits,
                cost=cost,
                lessons_for=lessons_for,
                context=said,
                offering=offering,
                plate=plate.layout() if plate is not None else None,
                parts=plate.parts if plate is not None else (),
                undone=undone,
                suggested=screen,
                suggesting=work,
                turned_down=tuple(c for c in corrections if c.said_no_to),
                turn_taken=bool(result.turn_deg) and taken_turn == result.turn_deg,
                material=material,
                stock=stock,
                stands=stands,
                record_load=record_load,
            ))
            if review.turn_taken:
                taken_turn = result.turn_deg
            if review.offering is not None:
                offering = review.offering
            if not review.browsing:
                break
        undone = ""
        if review.stop:
            raise RuntimeError("stopped at the settings review; nothing was sent to the printer")
        models = [c for c in review.taken if c.taken]
        took += models
        if beside is not None:
            beside.note([c.key for c in models], list(review.declined))
        if review.approved:
            if result.turn_deg and taken_turn != result.turn_deg:
                raise RuntimeError("approved without taking the turn it needs to fit the plate; "
                                   "nothing was sent to the printer")
            if beside is not None:
                beside.end(Ending.WENT_ON, review.saw if review.saw is not None else screen)
            break
        here = pose_value(orientation.rotation_deg)
        if plate is not None and review.part is not None:
            # A plate's send-back is one part's, from the pose that part is in.
            here = pose_value(plate.parts[review.part - 1].orientation.rotation_deg)
        turned = next((c for c in (*review.corrections, *review.taken)
                       if c.key == ORIENTATION and c.value != here), None)
        if turned is not None:
            raise _Turned(turned, [*corrections, *review.corrections], offering,
                          [c for c in took if c.key != ORIENTATION],
                          part=review.part, standing=tuple(corrections))
        corrections += review.corrections
        # A pose turned down, or any no to the model's, is the person's correction, and sets
        # nothing in the slice: what was here stands, a guard's included.
        asked = _held_tree(asked.corrected(
            [c for c in (*review.corrections, *review.taken)
             if c.key != ORIENTATION and not c.said_no_to]), process)
        if beside is not None and ORIENTATION in review.declined and ask_again is not None:
            beside.end(Ending.TURNED)  # its settings were for the turn
            ask_again()
    taken_again = {c.key for c in proposal.changes if c.taken}
    proposal = proposal.model_copy(update={"notes": tuple(
        n for n in proposal.notes
        if not (n.startswith("Taken at ") and any(f": {k}=" in n for k in taken_again)))})
    if beside is not None:
        found_by_model = beside.ended
        proposal = recorded(proposal, found_by_model)
        for each in found_by_model:
            proposal = looked_up(proposal, each)
    missed = {c.key for c in proposal.not_applied}
    said_so = tuple(reached(c, process, filament, config) if c.key in missed else c
                    for c in corrections)
    return result, {"proposal": proposal, "corrections": said_so,
                    "slice_reading": reading, "slice_flags": found}


def _presets(profiles: SlicerProfiles) -> tuple[dict[str, Any], dict[str, Any]]:
    """The process and filament presets' own values."""
    process = json.loads(profiles.process.read_text())
    filament = json.loads(profiles.filament.read_text()) if profiles.filament else {}
    return process, filament


def _read(result: SliceResult) -> SliceReading | None:
    """The slice, read layer by layer, or None (logged) if its G-code can't be read."""
    try:
        return read_slice(result.gcode_path)
    except (OSError, ValueError) as exc:
        logger.warning("the slice %s couldn't be read: %s", result.gcode_path, exc)
        return None


def _read_as_sliced(result: SliceResult) -> SliceReading | None:
    """The slice read, with how the part lay on the plate in it (mqucifer/print-shop#257)."""
    reading = _read(result)
    return reading.model_copy(update={"turn_deg": result.turn_deg}) if reading else None


def _unoffered(result: SliceResult) -> RuntimeError:
    """A turn with no review to offer it at: the part isn't turned."""
    return RuntimeError(
        f"it doesn't fit the plate as posed. It fits turned {turn_words(result.turn_deg)}, "
        "which is taken at the review, and this job has none; nothing was sent to the printer"
    )


def _config(result: SliceResult) -> dict[str, str] | None:
    """What Orca sliced with, from the G-code's config block, or None (logged) if it can't be
    read: then no change is said to have reached the slice."""
    try:
        return parse_config_block(result.gcode_path)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("the settings of %s couldn't be read: %s", result.gcode_path, exc)
        return None


def build_volume_mm(machine_profile: Path) -> tuple[float, float, float]:
    """The build volume from a machine profile, following ``inherits`` if it must.

    print-shop's own profiles are whole (`print_shop.slicer.store`), so the
    dimensions are in the file. A thin overlay, carrying a few keys and the
    name of its parent in ``inherits``, is resolved from its sibling files,
    child keys winning; Orca's own tree is resolved by
    `print_shop.slicer.catalog`, at import.

    Raises:
        ValueError: If the dimensions cannot be resolved (missing keys,
            unparseable values, or an unresolvable ``inherits`` chain).
        FileNotFoundError: If the profile file does not exist.
    """
    resolved = _resolve_profile(machine_profile)

    area = resolved.get("printable_area")
    height = resolved.get("printable_height")

    if area is None or height is None:
        missing = []
        if area is None:
            missing.append("printable_area")
        if height is None:
            missing.append("printable_height")
        raise ValueError(
            f"Cannot resolve build volume from '{machine_profile.name}': "
            f"missing {', '.join(missing)} after resolving inherits chain"
        )

    try:
        # printable_area is ["0x0", "257x0", "257x257", "0x257"] — the max
        # x/y extents are the last two entries' first components.
        x = max(float(e.split("x")[0]) for e in area[2:])
        y = max(float(e.split("x")[1]) for e in area[3:])
        z = float(height)
    except (ValueError, IndexError, AttributeError) as exc:
        raise ValueError(
            f"Cannot parse build volume from '{machine_profile.name}': {exc}"
        ) from exc

    return (x, y, z)


_DIMENSIONS = ("printable_area", "printable_height")


def _resolve_profile(profile_path: Path) -> dict[str, Any]:
    """Load a profile and resolve its ``inherits`` chain until the dimensions are known.

    Returns a merged dict where child keys override parent keys. A profile
    that already has the dimensions is not walked: a whole profile keeps
    ``inherits`` naming a system parent that is not beside it.
    """
    try:
        data: dict[str, Any] = json.loads(profile_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot read profile '{profile_path.name}': {exc}"
        ) from exc

    inherits = data.get("inherits")
    if not inherits or all(k in data for k in _DIMENSIONS):
        return data

    parent = _find_parent(profile_path, inherits)
    parent_data = _resolve_profile(parent)
    # Child keys win over parent keys.
    merged: dict[str, Any] = {**parent_data, **data}
    # Remove the 'inherits' key from the result; it is not a dimension.
    merged.pop("inherits", None)
    return merged


def _find_parent(profile_path: Path, name: str) -> Path:
    """Find the parent profile for an ``inherits`` reference among the profile's siblings."""
    for candidate in sorted(profile_path.parent.glob("*.json")):
        if candidate == profile_path:
            continue
        try:
            data = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("name") == name:
            return candidate
    raise ValueError(
        f"Cannot resolve inherits='{name}': no sibling profile with that name "
        f"found in '{profile_path.parent}'"
    )


def graph_edges(app: Any) -> list[tuple[str, str]]:
    """Return ``(source, target)`` pairs from the compiled graph's edge list.

    This is what feeds ``ungated_edges()`` from ``src/core/contracts.py``.
    """
    return [(edge.source, edge.target) for edge in app.get_graph().edges]
