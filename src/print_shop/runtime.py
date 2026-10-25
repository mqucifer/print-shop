"""The pieces a real run is built from, read from the Mac's settings.

Everything here is unarmed. `scripts/print_job.py` adds the one armed piece,
`live_printer()`, and only when its own invocation says so; nothing in
`src/` calls it (`tests/test_print_node.py` asserts that). So a mistake here
can at worst fail to print, never print.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from print_shop.core.mqtt import MqttConfig
from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import LLMConfig
from print_shop.inventory import Material
from print_shop.ledger import Ledger, Row, ledger_path
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.outcome import end_name
from print_shop.printer.centauri import (
    CentauriClient,
    FrameRecorder,
    PrinterClient,
    PrinterConfig,
)
from print_shop.setup import Proposer
from print_shop.setup.intake import Asker
from print_shop.setup.model import setup_llm
from print_shop.setup.review import Reviewer
from print_shop.slicer.orca import BedType, SlicerProfiles, with_bed_type
from print_shop.slicer.store import Choice

__all__ = ["Run", "bed_type", "build_run", "describe", "profiles_for", "recording_path"]

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


def build_run(
    *,
    printer: PrinterClient | None,
    profiles: SlicerProfiles | None,
    recorder: FrameRecorder | None = None,
    settings: Path | None = None,
    workdir: Path | None = None,
    asker: Asker | None = None,
    reviewer: Reviewer | None = None,
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
    `crew-code-think`).

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
        proposer=Proposer(llm=setup_llm(llm, env)),
        asker=asker,
        reviewer=reviewer,
        ledger=ledger,
        # Read-only: watching needs a snapshot and nothing else.
        watch_printer=CentauriClient(PrinterConfig.from_env(path), recorder=recorder),
    )
    return Run(daemon=daemon, ledger=ledger, recorder=recorder)


def describe(row: Row) -> str:
    """One print as the runbook's last step reads it back."""
    lines = [
        f"job            {row.job_id}",
        f"part           {row.stl_name}  (sha256 {row.stl_sha256[:12]}…)",
        f"orientation    {row.rotation_deg} by {row.orientation_source}"
        f"{'' if row.orientation_accepted else ' (not accepted; authored pose)'}",
        f"g-code         {row.gcode_name}  ~{row.print_time_s // 60} min,"
        f" {row.filament_cm3} cm3, {row.layer_count} layers",
        f"profiles       {row.slicer_settings.get('printer_settings_id', '?')} /"
        f" {row.slicer_settings.get('print_settings_id', '?')} / {row.filament}",
        *_store_lines(row),
        *_setup_lines(row),
        *([f"material       {row.material.line()}"] if row.material else []),
        f"plate          {row.slicer_settings.get('curr_bed_type', '?')},"
        f" bed {row.slicer_settings.get('first_layer_bed_temperature', '?')} C",
        f"printer        {row.printer_machine} {row.printer_firmware}",
        f"asked to start {row.start_requested_at}",
        f"started        {row.started_at or '-'}",
    ]
    if row.start_error:
        lines.append(f"start error    {row.start_error}")
    lines += [
        f"ended          {row.ended_at or '-'}"
        + (f"  (printer: {end_name(row.printer_end_status)})" if row.ended_at else ""),
        f"outcome        {row.result or 'open: no answer yet'}"
        + (f", {row.symptom}" if row.symptom else "")
        + (f" — {row.note}" if row.note else ""),
    ]
    return "\n".join(lines)


def _store_lines(row: Row) -> list[str]:
    """Where the profiles came from: print-shop's store, by file, hash and commit."""
    used = row.profiles
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


def _setup_lines(row: Row) -> list[str]:
    """What the part was for, and each setting changed from the presets, by whom."""
    if row.intake is None:
        return []
    lines = [f"intake         {row.intake.summary()}"]
    if row.intake.note:
        lines.append(f"               ({row.intake.note})")
    changes = row.proposal.changes if row.proposal else ()
    for c in changes:
        lines.append(f"  {c.key}: {c.preset or '-'} -> {c.value} ({c.source.value}) {c.reason}")
    if row.corrections:
        lines.append(f"corrected      {', '.join(f'{c.key}={c.value}' for c in row.corrections)}")
    return lines
