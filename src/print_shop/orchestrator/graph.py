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

**A hand-sliced file takes the short way** (mqucifer/print-shop#164): `sliced` copies its bytes
into the job's folder and reads them, and goes straight to the same gate. Nothing between is
run for it: no ingest, orientation, question, proposal, lesson, slice or review.

The graph is the single source of truth for control flow. ``graph_edges``
exposes the compiled edge list so ``ungated_edges`` (CONTRACTS.md section 4)
can be asserted against the real graph rather than a synthetic list.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    PrintJobState,
    PrintStatus,
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
from print_shop.ingest.orientation import candidates, write_rotated
from print_shop.inventory import Inventory, Material, SpoolUse
from print_shop.ledger import Ledger, PrintRecord
from print_shop.orchestrator.fitness import scoring_profiles
from print_shop.printer.centauri import PreflightFailed, PrinterClient, preflight
from print_shop.setup import Proposer, pauses
from print_shop.setup import lessons as print_lessons
from print_shop.setup.context import PartContext, place, pose_for, read_context
from print_shop.setup.doubts import Doubting
from print_shop.setup.features import measure
from print_shop.setup.intake import Asker, Estimate, Intake, Question
from print_shop.setup.loop import Part, PrintLoop
from print_shop.setup.loop import run as run_loop
from print_shop.setup.offer import Offering, ThisPart
from print_shop.setup.offer import offering as offer_past_prints
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    Change,
    GivenLesson,
    Proposal,
    Source,
    apply,
    placeable,
    pose_value,
    reached,
    read_back,
)
from print_shop.setup.questions import estimate
from print_shop.setup.reading import SliceReading, flags, read_slice, seen
from print_shop.setup.review import Reviewer, ReviewRequest
from print_shop.setup.skill import groups_for, load
from print_shop.setup.traits import traits
from print_shop.setup.waiting import Waiting, ask_meanwhile
from print_shop.slicer.naming import job_id_from_filename, name_gcode
from print_shop.slicer.orca import (
    Slicer,
    SliceResult,
    SlicerProfiles,
    parse_config_block,
    slice_model,
)
from print_shop.slicer.store import Store

__all__ = ["build_graph", "graph_edges", "new_job"]

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
            then the print's settings over the presets from the answers
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
            calls, and says so. None keeps the single calls.
        show: Where a hand-sliced file's report is shown before the gate
            (mqucifer/print-shop#164): what its slice reads as, with nothing proposed. None
            shows it nowhere; the ledger keeps the reading either way.
        store: print-shop's profiles, by which a hand-sliced file's settings are put under the
            profile each belongs to. None keeps them all under "other".
        waiting: The person's side of the wait for the model's questions
            (mqucifer/print-shop#208): print-shop's two are asked at once, the model's are
            added when they arrive, and the person can go on without them. None asks them
            all together, once the model's call has ended.

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
        report = analyze(state["stl_path"])
        return {"mesh": report, "stl_sha256": report.stl_sha256}

    def orient_node(state: PrintJobState) -> dict[str, Any]:
        """Choose how the part sits on the plate: model proposes, validator disposes.

        The full ``Outcome`` (proposal, verdict history, accepted) is stored in
        state so a gate can show a human what was proposed and what changed.
        """
        verify_unchanged(state["stl_path"], state.get("stl_sha256", ""))
        if build_volume is None:
            raise RuntimeError(
                "this graph was built without profiles, so it cannot slice: give the "
                "fitted nozzle and filament (scripts/print_job.py --nozzle --filament)"
            )

        # The part's traits in the file's own pose, before any turn: what an orientation lesson
        # is keyed to, and looked up by (mqucifer/print-shop#128).
        laid = measure(state["stl_path"], state["mesh"].bed_contact_area_mm2)
        found = tuple(candidates(state["stl_path"]))
        poses: dict[str, Any] = {"orientation_candidates": found, "laid_traits": traits(laid)}
        request = OrientationRequest(
            candidates=found, build_volume_mm=build_volume, part_description="",
        )
        # The part's context, from its design run or a sidecar (mqucifer/print-shop#130).
        given_at = state.get("context_path")
        context = read_context(Path(state["stl_path"]), Path(given_at) if given_at else None)
        poses["part_context"] = context
        if state.get("as_laid"):
            return {"orientation": _as_laid(request), **poses}
        given = print_lessons.for_orienting(ledger, laid) if ledger is not None else ()
        request = request.model_copy(update={"lessons": tuple(g.line() for g in given)})
        kept = _kept(context, found, build_volume)
        if loop is not None and proposer is not None and asker is not None:
            # The print loop chooses the pose after the questions, which are asked of the
            # file's own, or of the side its context lays it on (mqucifer/print-shop#129, #130).
            own = kept or next(c for c in found if c.rotation_deg == (0.0, 0.0, 0.0))
            where = (f"on its {context.down} side, as {context.down_from} lays it" if kept
                     else "as the file lays it")
            pending = Outcome(proposal=Chosen(value=own, source=LOOP_PENDING, rationale=(
                f"{where}, for the questions: the print loop chooses the pose after them")),
                accepted=True, attempts=0)
            return {"orientation": pending, "orientation_lessons": given, **poses}
        if kept is not None:
            # The side its design run or sidecar lays it on is kept, as a pose laid by the
            # person is: no model is asked (mqucifer/print-shop#130).
            return {"orientation": Outcome(proposal=Chosen(
                value=kept, source=CONTEXT, rationale=(
                    f"kept on its {context.down} side, as {context.down_from} lays it: no "
                    "orientation model was asked")), accepted=True, attempts=0),
                "orientation_lessons": given, **poses}
        return {"orientation": orient_by_call(request), "orientation_lessons": given, **poses}

    def orient_by_call(request: OrientationRequest) -> Outcome[Orientation]:
        """The single orientation call: the model proposes, the validator disposes."""

        def chooses(req: OrientationRequest) -> Any:
            if llm is None:
                return propose_geometric(req, NO_MODEL_CONFIGURED)
            try:
                return propose_orientation(req, llm)
            except (RuntimeError, OSError):
                return propose_geometric(req)

        return _resolved(propose_validate(chooses, validate_orientation, request),
                         request.candidates)

    def slice_node(state: PrintJobState) -> dict[str, Any]:
        """Slice the model in the chosen orientation, writing into outdir_root/job_id.

        The STL is rotated to the accepted orientation before slicing. If that
        orientation still has overhang, supports are forced on: orientation
        minimises overhang but does not always eliminate it, and a part with
        unsupported overhang fails on the plate.

        If no orientation was accepted (e.g. non-manifold mesh routed directly
        from ingest), the part is sliced in its authored pose using the mesh's
        measured overhang area to determine support requirements.
        """
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
        proposed: dict[str, Any] = {}
        if proposer is not None and asker is not None:
            poses = state.get("orientation_candidates", ())
            features = measure(stl_to_slice, applied.bed_contact_area_mm2 or 0.0)
            first = stl_to_slice

            def priced(settings: dict[str, str], n: int) -> Estimate | None:
                return _estimate(slicer, first, profiles, settings, outdir / "options" / str(n))

            if waiting is not None and proposer.asks_model:
                measured = features
                intake = ask_meanwhile(
                    lambda stop: proposer.questions(measured, profiles, stop),
                    asker, waiting, lambda questions: _priced(questions, priced),
                )
            else:
                intake = _ask(proposer.questions(features, profiles), asker, priced)
            proposed["intake"] = intake
            context = state.get("part_context")
            earlier: list[Change] = []
            looped: Proposal | None = None
            fell_back: dict[str, Any] = {}
            if outcome is not None and outcome.proposal.source == LOOP_PENDING:
                assert loop is not None and build_volume is not None
                kept = _kept(context, poses, build_volume) if context is not None else None
                found_by = run_loop(loop, Part(
                    stl=Path(state["stl_path"]),
                    facts=proposer.facts(intake, features, profiles, context),
                    candidates=poses, build_volume_mm=build_volume,
                    skill=load(groups_for(intake, features)), profiles=profiles, slicer=slicer,
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
                    features = measure(stl_to_slice, applied.bed_contact_area_mm2 or 0.0)
                    reply = found_by.reply
                    made = dataclasses.replace(proposer, ask=lambda *_: reply, llm=None)(
                        intake, features, profiles, context)
                    looked = set(found_by.looked_up)
                    looped = made.model_copy(update={
                        **record, "model": f"loop:{loop.model}",
                        "lessons": tuple(g.model_copy(update={"looked_up": g.id in looked})
                                         for g in made.lessons),
                    })
            offer: Offering | None = None
            doubting: Doubting | None = None
            while True:
                features = measure(stl_to_slice, applied.bed_contact_area_mm2 or 0.0)
                if reviewer is not None and ledger is not None:
                    # Before the proposal's call: the offer waits for no model
                    # (mqucifer/print-shop#211).
                    built = _offering(ledger, state, stl_to_slice, features, applied, profiles,
                                      build_volume, earlier, intake)
                    if offer is None:
                        offer, doubting = built, Doubting.start(built, proposer.doubter())
                    else:
                        offer = offer.carried_to(built)
                if looped is not None:
                    proposal, looped = looped, None
                else:
                    proposal = proposer(intake, features, profiles, context)
                    if fell_back:
                        proposal = proposal.model_copy(update={
                            "loop": fell_back["loop"], "loop_ending": fell_back["loop_ending"],
                            "notes": proposal.notes + fell_back["notes"]})
                proposal = _with_orienting(proposal, state.get("orientation_lessons", ()))
                if intake.without:
                    # Said at the review, as well as when it happened (mqucifer/print-shop#208).
                    proposal = proposal.model_copy(
                        update={"notes": proposal.notes + (intake.without,)})
                carried = [c for c in earlier if c.key != ORIENTATION]
                if carried:
                    # Corrections made before the part was turned stand after it.
                    proposal = proposal.corrected(carried)
                try:
                    result, reviewed = _propose_and_review(
                        proposal, profiles,
                        lambda p, stl=stl_to_slice: slicer(stl, p, outdir),
                        reviewer, applied, outdir / "proposed", intake,
                        poses=poses, laid_traits=state.get("laid_traits", ()),
                        cost=_coster(slicer, stl_to_slice, profiles, proposal, outdir / "offers"),
                        lessons_for=_lessons_for(ledger, state["stl_path"], profiles,
                                                 outdir / "poses"),
                        earlier=earlier,
                        context=context, stl=state["stl_path"],
                        offering=offer, doubting=doubting,
                        material=_material_of(profiles), stock=_stock(ledger, profiles),
                    )
                except _Turned as turned:
                    # Sent back at the review: turned to the person's pose, and sliced again.
                    earlier, offer = turned.corrections, turned.offering
                    chosen = next(c for c in poses if pose_value(c.rotation_deg) == turned.to.value)
                    rotation_deg = chosen.rotation_deg
                    applied = AppliedOrientation(
                        rotation_deg=rotation_deg,
                        rationale=f"sent back at the review, by you: {turned.to.reason}",
                        height_mm=chosen.height_mm,
                        bed_contact_area_mm2=chosen.bed_contact_area_mm2,
                    )
                    proposed["orientation"] = Outcome(
                        proposal=Chosen(value=chosen, source="sponsor",
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
            proposed["features"] = features
            proposed.update(reviewed)
        else:
            active_profiles = (
                scoring_profiles(profiles, outdir) if overhang_area > 0 else profiles
            )
            result = slicer(stl_to_slice, active_profiles, outdir)
            reading = _read(result)
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
        decision = interrupt(
            {
                "gate": Gate.BEFORE_PRINT.value,
                "job_id": state["job_id"],
                "gcode_path": str(slice_result.gcode_path),
                "print_time_s": slice_result.print_time_s,
                "filament_cm3": slice_result.filament_cm3,
                "layer_count": slice_result.layer_count,
                "max_z_mm": slice_result.max_z_mm,
                "orientation_deg": list(applied_orientation.rotation_deg),
                "orientation_rationale": applied_orientation.rationale,
                # A pause the review asked for, where the slice put it, so the person is at
                # the printer for it (mqucifer/print-shop#119), what it's for, and the spool a
                # swap's print starts on (mqucifer/print-shop#218).
                "pause": _pauses_said(state.get("slice_reading")),
                "pause_for": _pause_for(state),
                "spools": _starts_said(state, ledger),
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
        3. **A swap's first spool is the one loaded** (mqucifer/print-shop#218).
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

        material = _material_prepared(state)
        spools: tuple[SpoolUse, ...] = ()
        if material is not None:
            with Inventory(ledger.path) as inventory:
                material = inventory.at_start(material)
                swap = _swap(state)
                if swap is not None and swap.start is not None and swap.to is not None:
                    material = inventory.at_swaps_start(swap.start)
                    to = swap.to.of(inventory.spool(swap.to.spool_id))
                    spools = pauses.uses(swap.model_copy(update={"start": material, "to": to}),
                                         state.get("slice_reading"))

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

        # Written before the printer is told anything, so a start that fails
        # halfway is still on record.
        ledger.record_start_requested(
            _print_record(state, remote_name, gcode, snapshot.machine, snapshot.firmware,
                          store, material, spools)
        )
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
        """Route to orient if manifold, else to the review gate."""
        if state["mesh"].is_manifold:
            return "orient"
        return Gate.REVIEW_PROPOSAL.value

    def route_after_orient(state: PrintJobState) -> str:
        """Route to slice if the orientation was accepted, else to the review gate."""
        if state["orientation"].accepted:
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


def _pauses_said(reading: Any) -> str:
    """Where the slice pauses and what for, in a sentence, or ""."""
    found = getattr(reading, "pauses", ()) or ()
    return "; ".join(f"this print {p.line()}" for p in found)


def _standing(state: PrintJobState) -> Change | None:
    """The pause that stood at the review, or None."""
    proposal = state.get("proposal")
    change = proposal.get(PAUSE) if proposal is not None else None
    return change if change is not None and change.value != "none" else None


def _pause_for(state: PrintJobState) -> str:
    """What the job's pause is for, or "" where it doesn't say (mqucifer/print-shop#218)."""
    change = _standing(state)
    return change.pause.kind.value if change is not None and change.pause is not None else ""


def _swap(state: PrintJobState) -> pauses.Pause | None:
    change = _standing(state)
    pause = change.pause if change is not None else None
    return pause if pause is not None and pause.kind is pauses.PauseFor.SWAP else None


def _starts_said(state: PrintJobState, ledger: Ledger | None) -> str:
    """The spool a swap's print starts on and whether it's loaded, for the gate, or ""."""
    swap = _swap(state)
    if swap is None or swap.start is None or ledger is None:
        return ""
    with Inventory(ledger.path) as inventory:
        return pauses.starting(swap, inventory.spools())


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
        rotation_deg=applied.rotation_deg,
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
    asked: Any, asker: Asker, slice_with: Any
) -> Intake:
    """The questions with Orca's estimate for each option, asked, with the answers."""
    questions = _priced(asked.questions, slice_with)
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


class _Turned(Exception):  # noqa: N818 - a signal, not an error
    """The pose was sent back at the review: turn the part to it, and propose and slice again."""

    def __init__(self, to: Change, corrections: list[Change], offering: Offering | None) -> None:
        super().__init__(f"turned to {to.value}")
        self.to = to
        self.corrections = corrections
        self.offering = offering


def _offering(
    ledger: Ledger, state: PrintJobState, posed: str | Path, features: Any,
    applied: AppliedOrientation, profiles: SlicerProfiles,
    build_volume: tuple[float, float, float] | None, earlier: list[Change], intake: Intake,
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
        if any(c.key == ORIENTATION for c in earlier):
            chosen = "your correction at this job"
        elif state.get("as_laid"):
            chosen = "kept as laid"
        on_hand = _stock(ledger, profiles)
        return offer_past_prints(ledger, ThisPart(
            stl=Path(posed), stl_name=Path(state["stl_path"]).name,
            sha256=state.get("stl_sha256", ""), features=features,
            rotation_deg=applied.rotation_deg, height_mm=applied.height_mm or 0.0,
            machine=json.loads(profiles.machine.read_text()), process=process,
            filament=filament, filament_names=tuple(dict.fromkeys(n for n in names if n)),
            candidates=state.get("orientation_candidates", ()), build_volume_mm=build_volume,
            context=state.get("part_context"), pose_chosen=chosen,
            material=_material_of(profiles), on_hand=on_hand() if on_hand else (),
            asked=intake.could_set(),
        ))
    except Exception as exc:  # noqa: BLE001 - a print is never stopped by what it might reuse
        logger.warning("the past prints couldn't be read for the offer: %s", exc)
        return Offering(failed=f"{type(exc).__name__}: {exc}"[:300])


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

    def found(pose: Orientation) -> tuple[GivenLesson, ...]:
        if ledger is None:
            return ()
        turned = stl if pose.rotation_deg == (0.0, 0.0, 0.0) else write_rotated(
            stl, pose.rotation_deg, outdir / f"{pose_value(pose.rotation_deg)}.stl")
        return print_lessons.for_part(ledger, measure(turned, pose.bed_contact_area_mm2), kind)

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
    poses: tuple[Orientation, ...] = (),
    laid_traits: tuple[str, ...] = (),
    cost: Any = None,
    lessons_for: Any = None,
    earlier: list[Change] | None = None,
    context: PartContext | None = None,
    stl: str | Path | None = None,
    offering: Offering | None = None,
    doubting: Doubting | None = None,
    material: Material | None = None,
    stock: pauses.Stock | None = None,
) -> tuple[SliceResult, dict[str, Any]]:
    """Slice with the proposal; with a reviewer, again after each correction until approved.

    Each slice is read layer by layer, and flagged for this part from which faces the intake
    says are seen (mqucifer/print-shop#105). A change a flag proposes is only applied when the
    Sponsor takes it at the review. `earlier` are the corrections made before the part was
    turned, kept with the job's.

    `offering` is what decided the past prints most like this one (mqucifer/print-shop#211):
    none of it is applied until it's used, and picking or clearing slices nothing.

    `material` is the spool the job was prepared with and `stock` the spools on hand: what a swap
    names its two from. Each slice's reading is marked with what its pause is for
    (mqucifer/print-shop#218).

    Each change is read back from each slice (mqucifer/print-shop#206): the review, and then
    the job, have what reached it as changed and the rest as not applied, the corrections
    among them.

    Raises:
        RuntimeError: If the Sponsor stops at the review. Nothing was sent to
            the printer, and the approval gate was never reached.
        _Turned: If the pose is sent back (mqucifer/print-shop#128), with every correction so
            far: the caller turns the part and calls again.
    """
    presets = tuple(
        str(json.loads(p.read_text()).get("name", p.stem)) if p else "-"
        for p in (profiles.machine, profiles.process, profiles.filament)
    )
    process, filament = _presets(profiles)
    faces = seen(intake)
    corrections: list[Change] = list(earlier or ())
    # The part's context in this pose (mqucifer/print-shop#130): its fits as heights, for the
    # reading's seam rule and elephant foot, and what the review says was read.
    placed = (place(context, stl, orientation.rotation_deg)
              if context is not None and context.found and stl is not None else None)
    said = tuple([*(context.lines() if context is not None else ()),
                  *(placed.lines() if placed is not None else ())])
    proposal = proposal.model_copy(update={
        "context": said, "context_from": context.path if context is not None else ""})
    asked = proposal
    while True:
        result: SliceResult = slice_with(
            apply(profiles, placeable(asked, process, filament), workdir)
        )
        config = _config(result)
        proposal = read_back(asked, process, filament, config)
        reading = pauses.marked(_read(result), proposal.get(PAUSE))
        found = (flags(reading, faces, process, filament, placed, proposal.changes)
                 if reading is not None else ())
        if reviewer is None:
            break
        while True:
            if offering is not None and doubting is not None:
                offering = doubting.added_to(offering)
            review = reviewer(ReviewRequest(
                presets=(presets[0], presets[1], presets[2]),
                proposal=proposal,
                slice=result,
                orientation=orientation,
                profiles=profiles,
                process=process,
                filament=filament,
                reading=reading,
                flags=found,
                candidates=poses,
                laid_traits=laid_traits,
                cost=cost,
                lessons_for=lessons_for,
                context=said,
                offering=offering,
                material=material,
                stock=stock,
            ))
            if review.offering is not None:
                offering = review.offering
            if not review.browsing:
                break
        if review.stop:
            raise RuntimeError("stopped at the settings review; nothing was sent to the printer")
        if review.approved:
            break
        turned = next((c for c in review.corrections if c.key == ORIENTATION), None)
        if turned is not None:
            raise _Turned(turned, [*corrections, *review.corrections], offering)
        corrections += review.corrections
        asked = asked.corrected([*review.corrections, *review.taken])
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
