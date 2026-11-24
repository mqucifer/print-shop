"""The print desk reads every job and copies it to run again, and changes nothing
(mqucifer/print-shop#139).

Everything it reads is made here: a ledger with two prints (one with questions, corrections, a
colour change, a slice reading and frames; one kept as laid and still printing), a job that
ended unprinted, a job with only its log, and an inventory. Nothing is the Sponsor's.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from types import ModuleType

import pytest

from print_shop import ledger as ledger_module
from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.desk import HOST, Desk, make_server
from print_shop.desk.again import again, correction_line
from print_shop.desk.read import PARTS_DIR, parts_dir
from print_shop.inventory import Inventory
from print_shop.ledger import Answer, JobEnding, Ledger, LedgerError, PrintRecord, Result, Symptom
from print_shop.setup.intake import Answer as IntakeAnswer
from print_shop.setup.intake import Intake, Option, Question, replay_asker
from print_shop.setup.proposal import ORIENTATION, PAUSE, Change, Proposal, Source
from print_shop.setup.reading import Flag, PauseReading, read_slice
from print_shop.setup.review import Review, ReviewRequest, parse_answer
from print_shop.setup.waiting import Waiting
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.store import STORE_DIR, Store, choose
from tests.fakes import DATA
from tests.test_runtime import settings  # noqa: F401  (the fixture)

TUNING = "Elegoo RAPID PETG 270Degree,MaxFanSpeed=50,AuxPartCoolingFan=80"
SIGN = "0a1b2c3d-0000-4000-8000-000000000001"  # questions, corrections, a swap, frames
CUBE = "0a1b2c3d-0000-4000-8000-000000000002"  # kept as laid, printing
HOOK = "0a1b2c3d-0000-4000-8000-000000000003"  # ended unprinted
CLIP = "0a1b2c3d-0000-4000-8000-000000000004"  # its log only
JPEG = b"\xff\xd8\xff\xe0 a frame \xff\xd9"
TYPED = "wall_loops=4 because it carries the load; pause_at=12.4 because white to black; " \
        "seam_position=back"


BRACKET = b"solid bracket\nendsolid bracket\n"
CUBE_BYTES = b"solid cube\nendsolid cube\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Shop:
    ledger: Path
    jobs: Path
    parts: Path
    part: Path  # the bracket, found by its bytes in a subfolder of the parts folder
    request: ReviewRequest
    store_profiles: SlicerProfiles


def _intake() -> Intake:
    questions = (
        Question(feature="purpose", text="What is the part for?", options=(
            Option(label="Decorative", settings={}, impact="faces matter"),
            Option(label="Carries a load", settings={"wall_loops": "3"},
                   impact="stronger walls", recommended=True, why="it's a bracket"),
        )),
        Question(feature="overhang", text="The 2 mm ledge overhangs: support it?", options=(
            Option(label="Tree supports", settings={"enable_support": "1"}, impact="clean"),
            Option(label="Bridge it", settings={"enable_support": "0"}, impact="may sag"),
        )),
    )
    return Intake(questions=questions,
                  answers=(IntakeAnswer(choice=1), IntakeAnswer(other="it's <b>hidden</b>")))


def _shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Shop:
    """The fixture shop, every row written as the print side writes it."""
    clock = count()
    monkeypatch.setattr(ledger_module, "_now",
                        lambda: f"2026-11-15T10:{next(clock):02d}:00+00:00")
    path = tmp_path / "ledger.sqlite"
    jobs = tmp_path / "jobs"
    with Inventory(path) as inventory:
        inventory.add_printer("cc1", "Elegoo Centauri Carbon")
        inventory.fit(0.4)
        loaded = inventory.add_spool(manufacturer="Elegoo", type="PETG",
                                     variety="Elegoo Rapid PETG", colour="black",
                                     grams_left=640, tuning=TUNING)
        inventory.add_spool(manufacturer="Elegoo", type="PLA", variety="Elegoo PLA",
                            colour="white", grams_left=1000)
        inventory.load(loaded.id)
        material = inventory.material()
    choice = choose(Store(STORE_DIR), nozzle_mm=0.4, filament=TUNING)
    composed = choice.compose(tmp_path / "composed")
    used = choice.used(STORE_DIR).model_copy(update={"material": material})
    profiles = SlicerProfiles(machine=composed["machine"], process=composed["process"],
                              filament=composed["filament"], used=used)

    # The review as it went: the proposal shown, then the line typed at it.
    intake = _intake()
    taken = Change(key="ironing_type", preset="top", value="no ironing",
                   reason="the top is hidden", source=Source.READING, knowledge="ironing",
                   reading="12 min of ironing on a hidden floor")
    shown = Proposal(
        reasoning="A bracket: the ledge carries the load.",
        changes=(
            Change(key="wall_loops", preset="2", value="3", source=Source.ANSWER,
                   reason="you chose 'Carries a load': stronger walls", feature="purpose"),
            Change(key="sparse_infill_density", preset="15%", value="25%",
                   source=Source.LESSON, lesson=4, reason="a bracket like this one cracked"),
        ),
        notes=("PETG strings: dry it",),
        model="crew-code-think",
    )
    request = ReviewRequest(
        presets=(choice.machine.name, choice.process.name, choice.filament.name),
        proposal=shown,
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=100, max_z_mm=40,
                          filament_mm=900, filament_cm3=2.9, filament_g=3.6, print_time_s=654),
        orientation=AppliedOrientation(rotation_deg=(0.0, 90.0, 0.0), rationale="flat face down"),
        profiles=profiles,
        process=json.loads(composed["process"].read_text()),
        filament=json.loads(composed["filament"].read_text()),
    )
    review = parse_answer(TYPED, request)
    assert isinstance(review, Review)
    turned = Change(key=ORIENTATION, preset="0,90,0", value="0,0,0", source=Source.SPONSOR,
                    reason="the face it lay on is seen")
    corrections = (turned, *review.corrections)
    proposal = shown.corrected([*review.corrections, taken])

    reading = read_slice(DATA / "recorded" / "cube.gcode")
    reading = reading.model_copy(update={"pauses": (
        PauseReading(before_layer=63, z=12.6, after_z=12.4, after_s=600.0),)})
    base = dict(
        orientation_accepted=True, layer_count=100,
        slicer_config={"nozzle_diameter": "0.4", "filament_settings_id": TUNING},
        printer_machine="Centauri Carbon", printer_firmware="V1.1.46", profiles=used,
    )
    with Ledger(path) as ledger:
        ledger.record_start_requested(PrintRecord(
            job_id=SIGN, stl_name="bracket.stl", stl_sha256=_sha(BRACKET),
            rotation_deg=(0.0, 0.0, 0.0),
            orientation_source="model:crew-code-think", orientation_rationale="flat face down",
            gcode_name="bracket.gcode", print_time_s=5400, filament_cm3=31.5, intake=intake,
            proposal=proposal, corrections=corrections, reading=reading,
            flags=(Flag(id="ironing_hidden", reading=taken.reading, change=taken),), **base,  # type: ignore[arg-type]
        ))
        ledger.record_started(SIGN)
        frames = jobs / SIGN / "frames"
        frames.mkdir(parents=True)
        for kind, layer in (("layer", 10), ("layer", 50), ("end", 100)):
            frame = frames / f"{kind}-{layer:04d}.jpg"
            frame.write_bytes(JPEG + str(layer).encode())
            ledger.record_frame(SIGN, kind, layer, 100, frame, frame.stat().st_size)
        ledger.record_ended(SIGN, 9)
        ledger.record_answer(SIGN, Answer(result=Result.FAILED, symptom=Symptom.STRINGING,
                                          note="hairs between the channels <b>"))
        ledger.record_start_requested(PrintRecord(
            job_id=CUBE, stl_name="cube.stl", stl_sha256=_sha(CUBE_BYTES),
            rotation_deg=(0.0, 0.0, 0.0),
            orientation_source="as_laid", orientation_rationale="kept as laid, by you",
            gcode_name="cube.gcode", print_time_s=1009, filament_cm3=3.3,
            intake=Intake(note="no questions: the part's features left nothing to ask"),
            proposal=Proposal(), corrections=(), **base,  # type: ignore[arg-type]
        ))
        ledger.record_started(CUBE)
        ledger.record_frames_missed(CUBE, "the camera is off")
        ledger.record_unprinted(HOOK, "hook.stl", JobEnding.TIMED_OUT,
                                "the approval gate waited an hour")
    # The log names where the bracket was; it's since moved into a subfolder of the parts
    # folder, renamed, beside a file that isn't it. The cube is only in its job's folder.
    parts = tmp_path / "parts"
    part = parts / "brackets" / "bracket-v2.stl"
    part.parent.mkdir(parents=True)
    part.write_bytes(BRACKET)
    (parts / "brackets" / "bracket-v3.STL").write_bytes(b"solid other\nendsolid other\n")
    (jobs / CUBE).mkdir()
    (jobs / CUBE / "cube.stl").write_bytes(CUBE_BYTES)
    (jobs / SIGN / "job.log").write_text(f"print_job.py, 2026-11-15T10:00:00+00:00\n"
                                        f"part: {tmp_path / 'desk' / 'bracket.stl'}\n"
                                        "command: ...\nAt the review.\n")
    (jobs / CLIP).mkdir()
    (jobs / CLIP / "job.log").write_text("print_job.py\npart: /parts/clip.stl\nStopped.\n")
    later = datetime(2026, 11, 15, 11, 0, tzinfo=UTC).timestamp()  # after every row's
    os.utime(jobs / CLIP / "job.log", (later, later))
    return Shop(ledger=path, jobs=jobs, parts=parts, part=part, request=request,
                store_profiles=profiles)


@pytest.fixture
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Shop:
    return _shop(tmp_path, monkeypatch)


@pytest.fixture
def served(shop: Shop) -> Iterator[str]:
    server = make_server(Desk(shop.ledger, shop.jobs, shop.parts), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str, method: str = "GET") -> tuple[int, str, bytes]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - localhost
            return response.status, response.headers.get_content_type(), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get_content_type(), error.read()


def _bytes(folder: Path) -> dict[str, str]:
    """Every file under `folder`, by its hash; SQLite's own -wal and -shm aside."""
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }


# --- it changes nothing ----------------------------------------------------------


VIEWS = ("/", f"/job/{SIGN}", f"/job/{CUBE}", f"/job/{HOOK}", f"/job/{CLIP}",
         f"/job/{SIGN}/answers.json", f"/job/{SIGN}/frame/0", f"/job/{SIGN}/frame/2",
         f"/job/{SIGN}/log", "/job/nothing", f"/job/{SIGN}/frame/9", "/job/..%2F..%2Fetc",
         "/elsewhere")


def test_every_view_leaves_the_ledger_and_the_jobs_folder_as_they_were(
    shop: Shop, served: str, tmp_path: Path
) -> None:
    before = _bytes(tmp_path)
    ledger = hashlib.sha256(shop.ledger.read_bytes()).hexdigest()
    for view in VIEWS:
        _get(served + view)
        assert hashlib.sha256(shop.ledger.read_bytes()).hexdigest() == ledger, view
        assert _bytes(tmp_path) == before, view
    wal = shop.ledger.with_name(shop.ledger.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0, "a reader writes nothing to the log"


def test_the_ledger_is_opened_read_only_and_refuses_a_write(shop: Shop) -> None:
    ledger = Ledger.read_only(shop.ledger)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ledger.record_frames_missed(SIGN, "a write from the desk")
    finally:
        ledger.close()
    with pytest.raises(LedgerError, match="no ledger"):
        Ledger.read_only(shop.ledger.with_name("missing.sqlite"))
    assert not shop.ledger.with_name("missing.sqlite").exists(), "nothing is made"


def test_with_no_ledger_yet_it_says_so_and_makes_none(tmp_path: Path) -> None:
    desk = Desk(tmp_path / "ledger.sqlite", tmp_path / "jobs")
    assert desk.on_hand().printers == () and desk.jobs() == []
    assert not (tmp_path / "ledger.sqlite").exists() and not (tmp_path / "jobs").exists()


def test_it_answers_only_get_and_listens_only_on_the_mac(shop: Shop, served: str) -> None:
    assert HOST == "127.0.0.1" and served.startswith("http://127.0.0.1:")
    for method in ("POST", "PUT", "DELETE"):
        assert _get(served + "/", method)[0] == 405
    with pytest.raises(SystemExit):  # no way to bind it elsewhere
        _script("print_desk").main(["--host", "0.0.0.0"])


# --- what it shows -----------------------------------------------------------------


def test_the_front_page_shows_whats_loaded_and_every_job_newest_first(
    shop: Shop, served: str
) -> None:
    code, kind, body = _get(served + "/")
    page = body.decode()
    assert (code, kind) == (200, "text/html")
    assert "a 0.4 mm nozzle" in page
    assert "black Elegoo Rapid PETG" in page and TUNING in page and "640 g left" in page
    assert "white" in page and "Elegoo PLA" in page, "the spools on hand"
    order = [page.index(f"/job/{j}\"") for j in (CLIP, HOOK, CUBE, SIGN)]
    assert order == sorted(order), "newest first: the log's, the unprinted, then the prints"
    assert "failed, stringing" in page and "printing" in page
    assert "ended unprinted: timed_out" in page and "its log only" in page
    assert "<td>pause</td>" in page, "a pause that doesn't say what for is only a pause"
    zoned = r"2026-11-1\d \d\d:\d\d (?:[A-Z]{2,5}|[+-]\d{2,4})<"
    assert re.search(zoned, page), "times say their zone"


def test_a_jobs_page_shows_its_whole_record(shop: Shop, served: str) -> None:
    code, _, body = _get(served + f"/job/{SIGN}")
    page = body.decode()
    assert code == 200
    # the part, its pose and why, the questions with the answers given
    assert "bracket.stl" in page and "flat face down" in page
    assert "What is the part for?" in page and "Carries a load" in page
    assert page.count("[recommended: it&#x27;s a bracket]") == 1
    assert "← your answer" in page and "Your own words: it&#x27;s &lt;b&gt;hidden&lt;/b&gt;" \
        in page, "escaped"
    # the settings with each reason and source, and the corrections
    assert "a bracket like this one cracked" in page and "lesson 4" in page
    assert "the slice&#x27;s reading" in page and "you, at the review" in page
    assert "it carries the load" in page and "the face it lay on is seen" in page
    # the reading's flags, the colour change, the time and plastic
    assert "The slice, read layer by layer" in page and "Flagged" in page
    assert "At 12.4 mm: white to black" in page and "pauses after layer 62" in page
    assert "1 h 30 min" in page and "31.5 cm³" in page
    # the outcome, its note and its frames
    assert "failed, stringing" in page and "hairs between the channels &lt;b&gt;" in page
    assert page.count(f"/job/{SIGN}/frame/") == 6 and "the end, layer 100 of 100" in page
    assert "At the review." in page, "the log"
    assert "<b>" not in page.replace("<b>hidden", "")


def test_a_success_with_a_flaw_reads_as_usable_on_the_desk(shop: Shop, served: str) -> None:
    with Ledger(shop.ledger) as ledger:
        ledger.record_ended(CUBE, 9)
        ledger.record_answer(CUBE, Answer(result=Result.SUCCESS, symptom=Symptom.STRINGING))
    [cube] = [j for j in Desk(shop.ledger, shop.jobs, shop.parts).jobs() if j.job_id == CUBE]
    assert (cube.status, cube.failed) == ("success, with stringing", False)
    assert "success, with stringing" in _get(served + "/")[2].decode()
    page = _get(served + f"/job/{CUBE}")[2].decode()
    assert '<li class="good"><strong>success, with stringing</strong>' in page


def test_frames_the_log_and_the_answers_are_served(shop: Shop, served: str) -> None:
    code, kind, frame = _get(served + f"/job/{SIGN}/frame/2")
    assert (code, kind, frame) == (200, "image/jpeg", JPEG + b"100")
    assert _get(served + f"/job/{SIGN}/log")[2].decode().startswith("print_job.py")
    assert _get(served + f"/job/{SIGN}/frame/3")[0] == 404
    assert _get(served + "/job/..%2F..%2Fetc%2Fpasswd")[0] == 404
    code, kind, body = _get(served + f"/job/{SIGN}/answers.json")
    assert (code, kind) == (200, "application/json") and json.loads(body)["answers"]


def test_a_print_without_frames_says_why(shop: Shop, served: str) -> None:
    page = _get(served + f"/job/{CUBE}")[2].decode()
    assert "No camera frame was kept for this print: the camera is off." in page
    assert "None: this job is from before print_job.py kept one." in page


# --- run it again -------------------------------------------------------------------


def _record(shop: Shop, job_id: str) -> tuple[ledger_module.Row, Path | None, int, bool]:
    record = Desk(shop.ledger, shop.jobs, shop.parts).job(job_id)
    assert record is not None and record.row is not None
    return record.row, record.part_path, record.printers, record.part_found


def test_run_it_again_reproduces_the_jobs_settings(shop: Shop, tmp_path: Path) -> None:
    row, part, printers, found = _record(shop, SIGN)
    a = again(row, part, printers, found)

    # The command: the same part, found by its bytes, and the same nozzle, spool and profiles.
    args = shlex.split(a.command.replace("~/Downloads", str(tmp_path / "Downloads")))
    assert args[:5] == ["uv", "run", "python", "scripts/print_job.py", str(shop.part)]
    assert _sha(Path(args[4]).read_bytes()) == row.stl_sha256
    assert not any("wasn't found" in n or "has changed" in n for n in a.notes)
    given = dict(zip(args[5::2], args[6::2], strict=False))
    assert "--as-laid" not in args, "the model chose its pose; it was sent back at the review"
    with Inventory(shop.ledger) as inventory:
        assert inventory.for_print(float(given["--nozzle"]), given["--filament"]) == row.material
    chosen = choose(Store(STORE_DIR), nozzle_mm=float(given["--nozzle"]),
                    filament=given["--filament"], machine=given["--machine"],
                    process=given["--process"])
    assert chosen.used(STORE_DIR) == shop.store_profiles.used.model_copy(  # type: ignore[union-attr]
        update={"material": None})

    # The answers file replays the questions' answers.
    answers = Path(given["--answers"])
    answers.parent.mkdir()
    answers.write_text(json.dumps(a.answers))
    assert answers.name == f"answers-{SIGN[:8]}.json" == a.answers_name
    assert row.intake is not None
    assert replay_asker(answers)(row.intake.questions) == row.intake.answers

    # The correction line, typed at the same review, makes the same corrections.
    review = parse_answer(a.corrections, shop.request)
    assert isinstance(review, Review)
    assert row.corrections is not None
    assert review.corrections == tuple(c for c in row.corrections if c.key != ORIENTATION)
    # What the line can't carry is said beside it.
    assert any("Type orientation at the review" in n for n in a.notes)
    assert any("ironing_type=no ironing" in n and "(take)" in n for n in a.notes)
    assert not any("PRINT_AGENT_ALLOW_HARDWARE" in x for x in (a.command, *a.notes))


def test_a_print_kept_as_laid_runs_again_as_laid_with_no_answers(shop: Shop) -> None:
    row, part, printers, found = _record(shop, CUBE)
    a = again(row, part, printers, found)
    assert a.command.split()[4] == str(shop.jobs / CUBE / "cube.stl"), "found in its folder"
    assert "--as-laid" in a.command
    assert "--answers" not in a.command and a.answers is None and a.corrections == ""
    assert a.notes == ()


def test_the_parts_folder_comes_from_the_settings(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"PRINT_SHOP_PARTS={tmp_path}/my-parts\n")
    assert parts_dir(env) == tmp_path / "my-parts"
    assert parts_dir(tmp_path / "none.env") == PARTS_DIR == Path("~/print-shop/parts").expanduser()


def test_a_part_nothing_hashes_to_is_not_found_and_says_so(shop: Shop) -> None:
    shop.part.write_bytes(b"solid bracket, edited\nendsolid\n")
    row, part, printers, found = _record(shop, SIGN)
    assert not found and part == shop.ledger.parent / "desk" / "bracket.stl", "the log's path"
    a = again(row, part, printers, found)
    assert any(n.startswith("bracket.stl wasn't found: nothing in the parts folder")
               for n in a.notes)
    (shop.jobs / CUBE / "cube.stl").write_bytes(b"changed")
    shop.part.parent.joinpath("cube.stl").write_bytes(b"changed too")
    row, part, printers, found = _record(shop, CUBE)
    assert (found, part) == (False, None)
    assert again(row, part, printers, found).command.split()[4] == "cube.stl"


def test_the_logs_path_is_kept_while_it_holds_the_same_bytes(shop: Shop) -> None:
    named = shop.ledger.parent / "desk" / "bracket.stl"
    named.parent.mkdir()
    named.write_bytes(BRACKET)
    _, part, _, found = _record(shop, SIGN)
    assert (part, found) == (named, True), "the log's own path first, over the parts folder"
    named.write_bytes(b"solid bracket, changed since\nendsolid\n")
    row, part, printers, found = _record(shop, SIGN)
    assert (part, found) == (shop.part, True), "then the parts folder"
    shop.part.unlink()
    row, part, printers, found = _record(shop, SIGN)
    assert (part, found) == (named, False)
    assert any(n.startswith(f"{named} has changed since this job")
               for n in again(row, part, printers, found).notes)


def test_a_reason_with_a_semicolon_doesnt_split_the_line(shop: Shop) -> None:
    row, _, _, _ = _record(shop, SIGN)
    split = Change(key="wall_loops", preset="2", value="4", source=Source.SPONSOR,
                   reason="strong; it hangs")
    line = correction_line(row.model_copy(update={"corrections": (split,)}))
    assert line == "wall_loops=4 because strong, it hangs"
    review = parse_answer(line, shop.request)
    assert isinstance(review, Review) and len(review.corrections) == 1


def test_a_pause_from_before_203_is_copied_at_the_height_that_pauses_where_it_did(
    shop: Shop,
) -> None:
    """mqucifer/print-shop#203: such a row's typed height was the first layer after the pause.
    Typed again as it was, the print would pause a layer later than it did."""
    row, part, printers, found = _record(shop, SIGN)
    assert row.reading is not None and "pause_at=12.4" in correction_line(row)
    written_then = PauseReading.model_validate({"before_layer": 62, "z": 12.4, "after_s": 600.0})
    older = row.model_copy(
        update={"reading": row.reading.model_copy(update={"pauses": (written_then,)})})
    assert written_then.line().startswith("pauses before layer 62, at z=12.4")

    a = again(older, part, printers, found)
    assert "pause_at=12.2 because white to black" in a.corrections
    assert "wall_loops=4 because it carries the load" in a.corrections, "and the rest as typed"
    [note] = [n for n in a.notes if "copied as" in n]
    assert "pause_at=12.4 is copied as pause_at=12.2" in note

    # A row from since is copied as typed, and an older one whose pause was taken away too.
    assert not any("copied as" in n for n in again(row, part, printers, found).notes)
    taken_away = Change(key=PAUSE, preset="12.4", value="none", reason="one colour after all",
                        source=Source.SPONSOR)
    assert row.corrections is not None
    gone = older.model_copy(update={"corrections": (*row.corrections, taken_away)})
    assert "pause_at=none because one colour after all" in correction_line(gone)
    assert "pause_at=12.4" not in correction_line(gone), "only the pause that stood"


@pytest.mark.parametrize("pauses", [None, 0, 2])
def test_a_pause_that_cant_be_converted_is_never_copied_raw(
    shop: Shop, pauses: int | None
) -> None:
    """Where the G-code doesn't say where a job paused, its height could mean either layer."""
    row, part, printers, found = _record(shop, SIGN)
    assert row.reading is not None
    reading = None if pauses is None else row.reading.model_copy(update={"pauses": tuple(
        PauseReading(before_layer=62 + n, z=12.4 + 0.2 * n, after_s=600.0) for n in range(pauses)
    )})
    a = again(row.model_copy(update={"reading": reading}), part, printers, found)
    assert "pause_at" not in a.corrections
    assert "wall_loops=4 because it carries the load" in a.corrections
    assert any("pause_at=12.4 isn't copied" in n for n in a.notes)


# --- the job's log --------------------------------------------------------------------


def _script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Daemon:
    """Submits a job that shows a secret every way the terminal can, then times out."""

    def __init__(self, asker: object, tokens: list[str]) -> None:
        self._asker = asker
        self._tokens = tokens

    def __enter__(self) -> _Daemon:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def waiting(self) -> list[object]:
        """No job waits from before (mqucifer/print-shop#209)."""
        return []

    def submit(self, stl: Path, as_laid: bool = False, context: Path | None = None) -> str:
        import logging
        import sys

        print(f"the key is {self._tokens[0]}, the password {self._tokens[1]}")
        print(f"the token is {self._tokens[2]}", file=sys.stderr)
        logging.getLogger("print_shop.test").warning("calling with %s", self._tokens[0])
        return "job-log-1"

    def wait_for_gate(self, job_id: str, timeout_s: float, shown: int = 0) -> None:
        assert callable(self._asker)
        self._asker([_intake().questions[0]])
        raise TimeoutError("The slice didn't reach the approval gate within the hour.")


def test_print_job_keeps_what_it_shows_in_the_jobs_folder_and_no_secret(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    script = _script("print_job")
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    monkeypatch.setenv("HA_TOKEN", "eyJhbGciOiJIUzI1NiJ9.ha-token")
    monkeypatch.setattr(script, "WORK_DIR", tmp_path / "jobs")
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    tokens = ["sk-test", "secret", "eyJhbGciOiJIUzI1NiJ9.ha-token"]  # the settings', the env's

    given: dict[str, object] = {}

    def build_run(**kwargs: object) -> SimpleNamespace:
        given.update(kwargs)
        return SimpleNamespace(daemon=_Daemon(kwargs["asker"], tokens),
                               ledger=SimpleNamespace(close=lambda: None))

    monkeypatch.setattr(script, "build_run", build_run)
    part = tmp_path / "part.stl"
    part.write_text("solid x\nendsolid x\n")
    code = script.main([str(part), "--nozzle", "0.4", "--filament", "Elegoo PLA",
                        "--settings", str(settings)])
    assert code == 1
    waiting = given["waiting"]
    assert isinstance(waiting, Waiting) and waiting.stock_first, "the stock questions at once"
    shown = capsys.readouterr()
    assert "the key is sk-test" in shown.out, "the terminal shows what it showed before"

    log = (tmp_path / "jobs" / "job-log-1" / "job.log").read_text()
    assert log.splitlines()[1] == f"part: {part.resolve()}"
    assert "The inventory isn't set up" in log, "what was shown before the job had a folder"
    assert "Fitted: a 0.4 mm nozzle and Elegoo PLA" in log
    assert "the key is [redacted], the password [redacted]" in log
    assert "the token is [redacted]" in log and "calling with [redacted]" in log
    assert "1. What is the part for?" in log and "1> 2\n" in log, "the question and its answer"
    assert "within the hour" in log
    for token in tokens:
        assert token not in log
    desk = Desk(tmp_path / "ledger.sqlite", tmp_path / "jobs")
    found = desk.job("job-log-1")
    assert found is not None and found.part_path == part.resolve()
