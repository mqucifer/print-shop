"""The pieces a real run is built from, read from the Mac's settings.

Everything here is unarmed. `scripts/print_job.py` adds the one armed piece,
`live_printer()`, and only when its own invocation says so; nothing in
`src/` calls it (`tests/test_print_node.py` asserts that). So a mistake here
can at worst fail to print, never print.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from print_shop.core.mqtt import MqttConfig
from print_shop.core.state import turn_words
from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import LLMConfig
from print_shop.inventory import Material, spool_lines
from print_shop.ledger import Ledger, Row, ledger_path
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.live import Step
from print_shop.orchestrator.outcome import end_name
from print_shop.outcome_words import outcome_words
from print_shop.printer.camera import camera_for_prints
from print_shop.printer.centauri import (
    CentauriClient,
    FrameRecorder,
    PrinterClient,
    PrinterConfig,
)
from print_shop.setup import Proposer, lessons, pauses
from print_shop.setup.intake import Asker
from print_shop.setup.loop import PrintLoop, cli_person, llm_chat
from print_shop.setup.model import setup_llm
from print_shop.setup.proposal import ORIENTATION
from print_shop.setup.reading import NOW_PRINTS, PRINTS, TURNED_SINCE
from print_shop.setup.review import Reviewer
from print_shop.setup.waiting import Waiting
from print_shop.slicer.orca import BedType, SlicerProfiles, with_bed_type
from print_shop.slicer.store import Choice, Store

__all__ = [
    "Run", "bed_type", "build_run", "describe", "describe_all", "describe_part", "describe_plate",
    "profiles_for", "recording_path",
]

#: Where a run's per-job files go (rotated STLs, G-code, the job checkpoint).
WORK_DIR = Path("~/print-shop/jobs").expanduser()

#: Where the first print's recorder writes. Outside every checkout.
RECORDINGS_DIR = Path("~/print-shop/recordings").expanduser()

def profiles_for(
    choice: Choice, store_root: Path, workdir: Path, material: Material | None = None
) -> SlicerProfiles:
    """The chosen profiles composed whole into `workdir`, with what the ledger keeps.

    `material` is the printer, nozzle and spool from the inventory, when the
    print took them from it.
    """
    paths = choice.compose(workdir)
    return SlicerProfiles(
        machine=paths["machine"],
        process=paths["process"],
        filament=paths["filament"],
        used=choice.used(store_root).model_copy(update={"material": material}),
    )


def bed_type(env: dict[str, str]) -> BedType:
    """The plate on the printer: `ORCA_BED_TYPE` in the settings, else textured PEI.

    Textured PEI is the Sponsor's answer (2026-10-25, on mqucifer/print-shop#3).
    The Elegoo process profile's own default, the Cool Plate, heats the bed to
    only 35 C for PLA, and Orca won't slice PETG for it.

    Raises:
        ValueError: If the setting names a plate this code has not read back.
    """
    value = env.get("ORCA_BED_TYPE") or BedType.TEXTURED_PEI.value
    try:
        return BedType(value)
    except ValueError:
        known = ", ".join(repr(b.value) for b in BedType)
        raise ValueError(f"ORCA_BED_TYPE={value!r} is not one of {known}") from None


def recording_path() -> Path:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return RECORDINGS_DIR / f"sdcp-{stamp}.jsonl"


@dataclass
class Run:
    daemon: Daemon
    ledger: Ledger
    recorder: FrameRecorder | None


def print_loop(config: LLMConfig) -> PrintLoop:
    """The print loop on the setup model. Its calls can be ended, as going on at the review ends
    them (mqucifer/print-shop#242)."""
    return PrintLoop(chat=llm_chat(config), person=cli_person(), model=config.model,
                     stoppable=lambda stop: llm_chat(config, stop))


def build_run(
    *,
    printer: PrinterClient | None,
    profiles: SlicerProfiles | None,
    recorder: FrameRecorder | None = None,
    settings: Path | None = None,
    workdir: Path | None = None,
    asker: Asker | None = None,
    reviewer: Reviewer | None = None,
    show: Callable[[str], None] | None = None,
    store: Store | None = None,
    on_step: Callable[[str, str, Step], None] | None = None,
    waiting: Waiting | None = None,
) -> Run:
    """A daemon wired to the real broker, Orca, LiteLLM, ledger and a read-only watcher.

    `printer` is what an approval commands: `live_printer()` when the caller
    armed it, or None, in which case an approval fails at the print node.
    `profiles` are the store's, chosen for the fitted nozzle and filament
    (`profiles_for`); None only serves the outcome gate, and slices nothing.
    `asker` answers the intake's questions, proposed from the part's
    features (`cli_asker`, or `replay_asker` for recorded answers), and
    `reviewer` is the Sponsor's review of the proposed settings on the
    command (`cli_reviewer`). Both use `PRINT_SHOP_SETUP_MODEL` (default
    `crew-code-think`), as does the print loop, which decides the pose and the settings after
    the questions (mqucifer/print-shop#129).

    A hand-sliced file (`daemon.submit_sliced`, mqucifer/print-shop#164) uses none of those:
    `show` is where its slice's report is shown before the gate, and `store` the profiles its
    settings are sorted by.

    `on_step` is where a job's step goes for a second start to be told (`orchestrator/live.py`).

    `waiting` is what the person sees and does while the model works on its questions
    (`cli_waiting`, mqucifer/print-shop#208).

    Raises:
        RuntimeError, FileNotFoundError: On missing settings, before a job starts.
    """
    path = settings or SETTINGS_PATH
    env = load_env(path)
    cfg = MqttConfig.from_env(path)
    if not cfg.user:
        raise RuntimeError(f"MQTT_USER is not set in {path}; the gates need the broker")
    ledger = Ledger(ledger_path(path))
    work = workdir or WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    llm = LLMConfig.from_settings(path)
    printer_config = PrinterConfig.from_env(path)
    daemon = Daemon(
        cfg=cfg,
        profiles=(
            with_bed_type(profiles, bed_type(env), work / "profiles")
            if profiles is not None
            else None
        ),
        workdir=work,
        printer=printer,
        llm=llm,
        # The reviewed lessons from earlier prints, for a part with their traits
        # (mqucifer/print-shop#106).
        proposer=Proposer(
            llm=setup_llm(llm, env),
            lessons=lambda features, filament: lessons.for_part(ledger, features, filament),
        ),
        asker=asker,
        reviewer=reviewer,
        # One loop with tools decides the pose and the settings after the questions, and asks
        # the person on the command line only before a big change (mqucifer/print-shop#129).
        loop=print_loop(setup_llm(llm, env)),
        ledger=ledger,
        # Read-only: watching needs a snapshot and nothing else.
        watch_printer=CentauriClient(printer_config, recorder=recorder),
        # Read-only too, and never the printer's own stream, whose few slots a grab for each
        # layer starved (mqucifer/print-shop#123): off, and said so, until frames come through
        # Home Assistant.
        camera=camera_for_prints(),
        show=show,
        store=store,
        on_step=on_step,
        waiting=waiting,
    )
    return Run(daemon=daemon, ledger=ledger, recorder=recorder)


def describe(row: Row) -> str:
    """One print as the runbook's last step reads it back: an object of a plate as the plate's
    block, then its own."""
    if row.plate is not None:
        return f"{describe_plate(row)}\n\n{describe_part(row)}"
    return "\n".join([
        f"job            {row.job_id}",
        f"part           {row.stl_name}  (sha256 {row.stl_sha256[:12]}…)",
        _orientation_line(row),
        *_turned_lines(row),
        *_print_lines(row),
        _outcome_line(row),
    ])


def describe_all(rows: list[Row]) -> str:
    """Every print, a plate of several once with a block for each part (`--list`)."""
    blocks = []
    plates: set[str] = set()
    for row in rows:
        if row.plate is None:
            blocks.append(describe(row))
        elif row.plate.job_id not in plates:
            plates.add(row.plate.job_id)
            own = [describe_part(r) for r in rows
                   if r.plate is not None and r.plate.job_id == row.plate.job_id]
            blocks.append("\n\n".join([describe_plate(row), *own]))
    return "\n\n".join(blocks)


def describe_plate(row: Row) -> str:
    """What a plate's parts share, said once (mqucifer/print-shop#109): the print, its settings,
    its slice and the one review, from any of its rows."""
    plate = row.plate
    assert plate is not None
    proposal = row.proposal
    if proposal is not None:
        proposal = proposal.model_copy(update={
            "lessons": tuple(g for g in proposal.lessons if g.key != ORIENTATION)})
    shared = row.model_copy(update={
        "proposal": proposal,
        "corrections": tuple(c for c in row.corrections or () if c.key != ORIENTATION),
    })
    return "\n".join([
        f"job            {plate.job_id}",
        f"on a plate     {plate.layout.headline()}",
        *(f"               {line}" for line in plate.layout.drawn()),
        "               the time, plastic and layers below are the whole plate's",
        *_print_lines(shared),
    ])


def describe_part(row: Row) -> str:
    """What's one part's own on its plate: its file, its place, its pose and its outcome."""
    plate = row.plate
    assert plate is not None
    here = next(o for o in plate.layout.objects if o.n == plate.n)
    which = f"part {plate.n} of {len(plate.parts)}"
    lines = [
        f"{which:<15}{row.stl_name}  (sha256 {row.stl_sha256[:12]}…)",
        f"its row        {row.job_id}",
        f"place          {here.placed()}",
        _orientation_line(row),
    ]
    for c in row.corrections or ():
        if c.key == ORIENTATION:
            lines.append(f"sent back      {c.preset} -> {c.value}, by you: {c.reason}")
    for g in row.proposal.lessons if row.proposal else ():
        if g.key == ORIENTATION:
            lines.append(f"lesson         {g.id}, from {g.stl_name}: {g.why or 'read'}")
    return "\n".join([*lines, _outcome_line(row)])


def _orientation_line(row: Row) -> str:
    if row.sliced is not None:
        return "orientation    as the file lays it: nothing was turned"
    return (f"orientation    {row.rotation_deg} by {row.orientation_source}"
            f"{'' if row.orientation_accepted else ' (not accepted; authored pose)'}")


def _turned_lines(row: Row) -> list[str]:
    """A turn on the plate taken at the review (mqucifer/print-shop#242)."""
    return ([f"turned         {turn_words(row.rotation_deg[2])}, on the plate to fit,"
             " as taken at the review"] if row.rotation_deg[2] else [])


def _outcome_line(row: Row) -> str:
    return ("outcome        "
            + (outcome_words(row.result, row.symptom) if row.result else "open: no answer yet")
            + (f" — {row.note}" if row.note else ""))


def _print_lines(row: Row) -> list[str]:
    """The print, from its G-code to when it ended."""
    lines = [
        f"g-code         {row.gcode_name}  ~{row.print_time_s // 60} min,"
        f" {row.filament_cm3} cm3, {row.layer_count} layers",
        *_sliced_lines(row),
        f"profiles       {row.slicer_settings.get('printer_settings_id', '?')} /"
        f" {row.slicer_settings.get('print_settings_id', '?')} / {row.filament}",
        *_store_lines(row),
        *_setup_lines(row),
        *_reading_lines(row),
        *([f"material       {row.material.line()}"] if row.material else []),
        *[f"{'' if n else 'spools':<15}{line}"
          for n, line in enumerate(spool_lines(row.spools, row.ended_with))],
        f"plate          {row.slicer_settings.get('curr_bed_type', '?')},"
        f" bed {row.slicer_settings.get('first_layer_bed_temperature', '?')} C",
        f"printer        {row.printer_machine} {row.printer_firmware}",
        f"asked to start {row.start_requested_at}",
        f"started        {row.started_at or '-'}",
    ]
    if row.start_error:
        lines.append(f"start error    {row.start_error}")
    lines.append(
        f"ended          {row.ended_at or '-'}"
        + (f"  (printer: {end_name(row.printer_end_status)})" if row.ended_at else "")
    )
    return lines


def _store_lines(row: Row) -> list[str]:
    """Where the profiles came from: print-shop's store, by file, hash and commit."""
    used = row.profiles
    if row.sliced is not None:
        return ["store          - (sliced by hand in Orca, not from profiles/)"]
    if used is None:
        return ["store          - (sliced before print-shop kept its own profiles)"]
    commit = used.commit[:12] or "no commit"
    if used.store_dirty:
        commit += " + uncommitted changes"
    lines = [f"store          profiles/ at {commit}"]
    for ref in (used.machine, used.process, used.filament):
        lines.append(f"  {ref.file}  (sha256 {ref.sha256[:12]}…)")
        if ref.standard_file:
            lines.append(f"    over {ref.standard_file}  (sha256 {ref.standard_sha256[:12]}…)")
    return lines


def _minutes(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"


def _sliced_lines(row: Row) -> list[str]:
    """A hand-sliced file's own record (mqucifer/print-shop#164): where it came from, what it
    was sliced with, and Orca's estimate beside the real time."""
    kept = row.sliced
    if kept is None:
        return []
    lines = [f"sliced         {kept.sliced}: {kept.source}"
             + (f", plate {kept.plate} of Orca's sliced plate file" if kept.kind == "plate"
                else "") + f"  (sent sha256 {kept.gcode_sha256[:12]}…)"]
    if kept.reprint_of:
        lines.append(f"reprint of     job {kept.reprint_of}")
    lines += [f"  {line}" for line in kept.lines()]
    real = row.real_time_s
    lines.append(
        f"time           {_minutes(kept.print_time_s)} estimated by Orca; "
        + (f"{_minutes(real)} from its start to its end, heating included" if real is not None
           else "not ended yet"))
    grams = f"{kept.filament_g:g} g, " if kept.filament_g is not None else ""
    lines.append(f"filament       {grams}{kept.filament_cm3:g} cm3 estimated by Orca; the "
                 "printer doesn't report what it used")
    return lines


def _reading_lines(row: Row) -> list[str]:
    """The slice as it was read, and what it flagged (mqucifer/print-shop#105)."""
    if row.reading is None:
        return []
    shown = row.reading.lines()
    lines = [f"slice read     {shown[0]}" if shown else "slice read     -"]
    lines += [f"               {line}" for line in shown[1:]]
    lines += [f"  flagged      {f.id}: {f.reading}" for f in row.flags or ()]
    return lines


def _setup_lines(row: Row) -> list[str]:
    """What the part was for, and each setting changed from the presets, by whom."""
    if row.intake is None:
        return []
    lines = [f"intake         {row.intake.summary()}"]
    if row.intake.note:
        lines.append(f"               ({row.intake.note})")
    for note in row.proposal.notes if row.proposal else ():
        if note.startswith((PRINTS, NOW_PRINTS, TURNED_SINCE)):
            lines.append(f"               {note}")  # the faces as they printed
    changes = row.proposal.changes if row.proposal else ()
    for c in changes:
        by = f"{c.source.value}, taken by you" if c.taken else c.source.value
        lines.append(f"  {c.key}: {c.preset or '-'} -> {c.value} ({by}) "
                     f"{pauses.reason_of(c)}")
    for c in row.proposal.not_applied if row.proposal else ():
        lines.append(f"  {c.key}: not applied, {c.not_applied} ({c.source.value}) {c.reason}")
    if row.corrections:
        said = ", ".join(
            f"{c.key}={c.value}" + (f" ({c.used.line()})" if c.used else "")
            + (f" (not applied: {c.not_applied})" if c.not_applied else "")
            + (f" (saying no to the model's {c.said_no_to})" if c.said_no_to else "")
            for c in row.corrections
        )
        lines.append(f"corrected      {said}")
    for found in row.proposal.suggested if row.proposal else ():
        offered = ", ".join(
            f"{c.key}={c.value}" + (" (taken, then left behind by a turn)"
                                    if c.key in found.dropped
                                    else " (taken)" if c.key in found.taken
                                    else " (turned down)" if c.key in found.declined else "")
            for c in found.proposed
        )
        said = f"{found.ending.value} after {found.seconds:g} s"
        if found.unseen:
            said += ", and you went on without seeing them"
        lines.append(f"model proposed {offered or 'nothing'}: {said}")
    for pause in row.reading.pauses if row.reading else ():
        lines.append(f"{pause.kind or 'pause':<15}{pause.line()}")
    for g in row.proposal.lessons if row.proposal else ():
        said = f"set {g.key}={g.value}" if g.applied else (g.why or "read")
        lines.append(f"lesson         {g.id}, from {g.stl_name}: {said}")
    return lines
