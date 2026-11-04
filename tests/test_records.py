"""A run's records with no Docker: an older run read back, the ledger's migration, and what counts
as repeating. Every run here is made up in the test."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import paths, records
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.needs import Need, read
from print_shop.rounds.run import Run
from print_shop.rounds.session import Limits
from tests.rounds_fakes import Scripted, tool


def test_a_repeat_counts_whole_sentences_of_some_length_word_for_word() -> None:
    seen: set[str] = set()
    first = "The block sits flat on the shelf here. Short one. Then a fillet goes along its edge."
    # "Short one." is too plain to count: 38 and 34 characters are the two that do.
    assert records.repeated(first, seen) == (0, 38 + 34)
    again, total = records.repeated(first, seen)
    assert again == total > 0, "the same sentences, the second time"
    assert records.repeated("The block sits flat on the shelf now, then.", seen)[0] == 0
    # Within one call too: it's the same loop.
    fresh: set[str] = set()
    said = "The same sentence goes round once more.\n" * 3
    again, total = records.repeated(said, fresh)
    assert again == pytest.approx(total * 2 / 3)


def test_a_step_file_is_held_by_what_it_holds_and_not_the_second_it_was_written() -> None:
    head = (
        b"ISO-10303-21;\nHEADER;\nFILE_NAME('x','2026-11-02T05:20:%02d',('A'));\nENDSEC;\nDATA;\n"
    )
    one = head % 34 + b"#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\n"
    two = head % 37 + b"#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\n"
    moved = head % 37 + b"#1=CARTESIAN_POINT('',(1.,0.,0.));\nENDSEC;\n"
    assert one != two
    assert paths.content_hash("a.step", one) == paths.content_hash("b.STEP", two)
    assert paths.content_hash("a.step", one) != paths.content_hash("a.step", moved)
    # Anything else is held by every byte.
    assert paths.content_hash("a.stl", one) != paths.content_hash("a.stl", two)


def test_a_note_is_its_own_source_and_a_quote_keeps_to_where_it_says() -> None:
    entry = {
        "id": "a",
        "what": "x",
        "source": "note",
        "quote": "no floor",
        "check": {"by": "eye", "look_for": "y"},
    }
    found, wrong = read([entry], "the request", "there's no floor here")
    assert not wrong and isinstance(found[0], Need) and found[0].source == "note"
    assert "isn't in a note" in read([entry], "no floor, the request said", "")[1][0]


# A run from before these records, made up in its old shape


def old_run(root: Path) -> Path:
    """As the first real runs left theirs: no `relayed` or `resumed`, and no `t` on any event."""
    folder = root / "old"
    (folder / "asks" / "1" / "variants" / "A").mkdir(parents=True)
    (folder / "asks" / "1" / "variants" / "A" / "a.step").write_text("HEADER;\nDATA;\n#1;\n")
    (folder / "asks" / "1" / "sheet.png").write_bytes(b"a sheet")
    (folder / "rounds" / "001" / "judged").mkdir(parents=True)
    (folder / "rounds" / "001" / "judged" / "a.step").write_text("HEADER;\nDATA;\n#1;\n")
    state = {
        "name": "old",
        "request_sha": "s",
        "stage": "rounds",
        "round": 1,
        "needs_version": 1,
        "needs_open": False,
        "needs": [],
        "picks": {"look": {"pick": "A", "reason": "r", "ask": 1, "by": "sponsor"}},
        "asks": 1,
        "questions": 0,
        "stop": None,
        "ending": {"name": "met", "said": {"round": 1, "script": "m.py"}},
        "restarts": 0,
        "unchanged": 0,
        "last_files": {},
        "last_readings": {},
        "said": [],
        "harness": ["commit-a", "commit-b", "commit-c"],
    }
    (folder / "state.json").write_text(json.dumps(state))

    def call(n: int, at: str, **more: Any) -> dict[str, Any]:
        message = {"role": "assistant", "content": "", "reasoning_content": more.pop("thought", "")}
        message.update(more.pop("message", {}))
        return {
            "kind": "call",
            "n": n,
            "at": at,
            "seconds": 10.0 * n,
            "sent_chars": 100,
            "reasoning_chars": len(message["reasoning_content"]),
            "served": {},
            "finish": more.pop("finish", "tool_calls"),
            "message": message,
            "usage": {
                "prompt_tokens": 1000 * n,
                "completion_tokens": 10 * n,
                "reasoning_tokens": 5 * n,
                **more.pop("usage", {}),
            },
        }

    loop = " ".join(f"Looking over the block again, in sentence {i}." for i in range(80))
    events = [
        {"kind": "request", "n": 1, "from": 0, "append": [{"role": "system", "content": "s"}]},
        call(1, "2026-11-01T01:00:00+00:00"),
        {
            "kind": "tool",
            "index": 2,
            "info": {},
            "name": "needs",
            "outcome": "ok",
            "why": "",
            "arguments": "{}",
            "reply": "Nothing is settled.\n- gap, WOULDN'T PASS: no such thing. Limit: exit 0.\n"
            "- wall, would pass: OK. Limit: exit 0.",
            "detail": {"tried": True},
        },
        {"kind": "request", "n": 2, "from": 3, "append": []},
        call(2, "2026-11-01T01:01:00+00:00"),
        {"kind": "ended", "name": "ask", "said": {"n": 1}},
        {
            "kind": "request",
            "n": 3,
            "from": 4,
            "append": [
                {"role": "user", "content": "The Sponsor picked A. Their reason: r"},
                {"role": "user", "content": "A note from the people running this:\n\nA fillet."},
            ],
        },
        call(3, "2026-11-01T02:00:00+00:00", thought=loop),
        {"kind": "compacted", "estimated": 130_000, "estimated_after": 40_000},
        {"kind": "request", "n": 4, "from": 6, "append": []},
        call(
            4,
            "2026-11-01T02:01:00+00:00",
            thought=loop,
            finish="length",
            usage={"completion_tokens": 65536, "reasoning_tokens": 2000},
            message={
                "content": "a plan",
                "tool_calls": [
                    {
                        "id": "c",
                        "function": {"name": "write", "arguments": ""},
                    }
                ],
            },
        ),
        {"kind": "request", "n": 5, "from": 8, "append": []},
        {"kind": "ended", "name": "call_failed", "said": {"status": 400}},
        {
            "kind": "request",
            "n": 6,
            "from": 8,
            "append": [{"role": "user", "content": "A note from the people running this:\n\nGo."}],
        },
        call(6, "2026-11-01T03:00:00+00:00"),
        {"kind": "ended", "name": "met", "said": {"round": 1, "script": "m.py"}},
    ]
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return folder


def old_ledger(root: Path) -> DesignLedger:
    """The ledger's shape before these tables, with a row in each, which all have to stay."""
    path = root / "ledger.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE runs (run TEXT PRIMARY KEY, started_at TEXT NOT NULL,
            request_sha TEXT NOT NULL, events TEXT NOT NULL, harness_commit TEXT NOT NULL,
            skills_sha TEXT NOT NULL,
            image TEXT NOT NULL, image_id TEXT, alias TEXT NOT NULL, limits TEXT NOT NULL,
            ending TEXT, ending_said TEXT, ended_at TEXT, calls INTEGER, usage TEXT, outcomes TEXT);
        INSERT INTO runs VALUES ('old','t','s','e','commit-a','k','i','d','a','{}','met',NULL,'t',5,
            NULL,NULL);
        CREATE TABLE rounds (run TEXT, round INTEGER, started_at TEXT NOT NULL,
            first_call INTEGER NOT NULL, needs_version INTEGER NOT NULL, start_files TEXT NOT NULL,
            failing_at_start TEXT NOT NULL, ended_at TEXT, last_call INTEGER, script TEXT,
            diff TEXT, end_files TEXT, clean_run_exit INTEGER, clean_run_seconds REAL, met INTEGER,
            fix_shape_model_said TEXT, part TEXT, PRIMARY KEY (run, round));
        INSERT INTO rounds (run, round, started_at, first_call, needs_version, start_files,
            failing_at_start, met) VALUES ('old', 1, 't', 2, 1, '{}', '[]', 1);
        CREATE TABLE picks (run TEXT, ask INTEGER, round INTEGER NOT NULL, need TEXT,
            question TEXT NOT NULL, variants TEXT NOT NULL, sheet TEXT NOT NULL,
            asked_at TEXT NOT NULL, at_call INTEGER NOT NULL, pick TEXT, reason TEXT,
            picked_at TEXT, picked_by TEXT, PRIMARY KEY (run, ask));
        INSERT INTO picks VALUES ('old',1,1,'look','Which?','[]','s','t',2,'A','r','t','sponsor');
        """
    )
    db.commit()
    db.close()
    return DesignLedger(path)


def hashes_of(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_an_old_run_reads_back_through_the_new_tables_and_its_folder_isnt_touched(
    tmp_path: Path,
) -> None:
    folder = old_run(tmp_path)
    before = hashes_of(folder)
    ledger = old_ledger(tmp_path)
    counts = records.read_back(folder, ledger)
    assert hashes_of(folder) == before, "it only reads"
    assert counts == {
        "calls": 5,
        "interventions": 3,
        "timeline": 7,
        "runaways": 1,
        "tries": 2,
        "nudges": 0,
        "refusals": 0,
    }

    # What a person sent, known by its words, at the call it reached and that call's time.
    said = [dict(r) for r in ledger.rows("SELECT * FROM interventions ORDER BY seq")]
    assert [(r["kind"], r["from_who"], r["at_call"], r["phase"]) for r in said] == [
        ("pick", "sponsor", 3, 1),
        ("note", "the people running this", 3, 1),
        ("note", "the people running this", 6, 2),
    ]
    assert said[0]["at"] == "2026-11-01T02:00:00+00:00"
    assert said[1]["text"].endswith("A fillet.")

    # Each stop and each process, under the commit it ran on.
    line = [dict(r) for r in ledger.rows("SELECT * FROM timeline ORDER BY seq")]
    assert [(r["kind"], r["name"], r["harness_commit"]) for r in line] == [
        ("start", None, "commit-a"),
        ("stop", "ask", None),
        ("resume", None, "commit-b"),
        ("compaction", None, None),
        ("stop", "call_failed", None),
        ("resume", None, "commit-c"),
        ("stop", "met", None),
    ]
    assert [r["segment"] for r in line] == [1, 1, 2, 2, 2, 3, 3]
    compaction = next(r for r in line if r["kind"] == "compaction")
    assert (compaction["estimated_before"], compaction["estimated_after"]) == (130_000, 40_000)
    assert (compaction["prompt_before"], compaction["prompt_after"]) == (3000, 1000 * 4)

    # Tokens and seconds, split each way. The failed request made no call.
    phases = [dict(r) for r in ledger.rows("SELECT * FROM phases ORDER BY phase")]
    assert [(p["first_call"], p["last_call"], p["calls"]) for p in phases] == [
        (1, 2, 2),
        (3, 4, 2),
        (6, 6, 1),
    ]
    assert phases[1]["begun_by"].startswith("pick from sponsor")
    rounds = [dict(r) for r in ledger.rows("SELECT * FROM round_costs ORDER BY round")]
    assert [(r["round"], r["calls"]) for r in rounds] == [(0, 1), (1, 4)]

    # The runaway, for review. Call 3 said it first, and call 4 said it again and hit the limit.
    runaways = [dict(r) for r in ledger.rows("SELECT * FROM runaways ORDER BY n")]
    assert [(r["n"], r["why"]) for r in runaways] == [(4, "limit,repeats")]
    limit = runaways[0]
    assert limit["tokens_unaccounted"] == 65536 - 2000 - 2
    assert json.loads(limit["doing"])["tool_calls"][0]["name"] == "write"

    # A `try`, from its reply, and what a pick saw, from the copies its folder kept.
    tried = [dict(r) for r in ledger.rows("SELECT * FROM tries ORDER BY seq")]
    assert [(t["need"], t["passed"]) for t in tried] == [("gap", 0), ("wall", 1)]
    seen = json.loads(ledger.rows("SELECT seen FROM picks")[0]["seen"])
    assert seen["variants"]["A"]["a.step"] == paths.content_hash("a.step", b"HEADER;\nDATA;\n#1;\n")
    assert seen["sheet"] == hashlib.sha256(b"a sheet").hexdigest()
    judged = json.loads(ledger.rows("SELECT judged_files FROM rounds")[0]["judged_files"])
    assert set(judged) == {"a.step"}


@pytest.mark.parametrize(
    ("picked_by", "said"),
    [
        ("stand-in: a Claude builder", "stand-in: a Claude builder"),
        (None, "unknown"),
    ],
)
def test_who_made_an_old_pick_is_the_ledgers_word_and_never_the_messages(
    tmp_path: Path, picked_by: str | None, said: str
) -> None:
    """A stand-in's pick once went to the model as the Sponsor's, and its events stay as sent, so
    reading them back must not make it the Sponsor's taste."""
    folder = old_run(tmp_path)
    ledger = old_ledger(tmp_path)
    ledger.change("UPDATE picks SET picked_by=?", picked_by)
    records.read_back(folder, ledger)
    row = ledger.rows("SELECT from_who, text FROM interventions WHERE kind='pick'")[0]
    assert row["text"].startswith("The Sponsor picked")
    assert row["from_who"] == said


def test_reading_a_run_back_twice_adds_nothing_and_keeps_every_row(tmp_path: Path) -> None:
    folder = old_run(tmp_path)
    ledger = old_ledger(tmp_path)
    first = records.read_back(folder, ledger)
    second = records.read_back(folder, ledger)
    assert first == second
    assert ledger.rows("SELECT pick, reason, picked_by FROM picks")[0]["picked_by"] == "sponsor"
    run = ledger.rows("SELECT ending, harness_commit, calls FROM runs")[0]
    assert tuple(run) == ("met", "commit-a", 5), "what it was before"
    assert ledger.rows("SELECT met FROM rounds")[0]["met"] == 1


def test_a_run_not_in_the_ledger_is_refused(tmp_path: Path) -> None:
    folder = old_run(tmp_path)
    with pytest.raises(LookupError, match="isn't in this ledger"):
        records.read_back(folder, DesignLedger(tmp_path / "other.sqlite"))


def test_an_old_run_ended_at_met_still_loads_and_stays_ended(tmp_path: Path) -> None:
    folder = old_run(tmp_path)
    old_ledger(tmp_path)
    run = Run.load(folder, Scripted(), Limits(), sleep=lambda _: None)
    assert run.state.notes == []
    assert run.state.approvals == 0
    with pytest.raises(Exception, match="from before approval was a stop"):
        run.resume()
    # Loading it caught the ledger up.
    assert run.ledger.rows("SELECT COUNT(*) AS n FROM calls")[0]["n"] == 5


def test_a_pick_from_before_picks_held_what_they_saw_holds_for_nothing() -> None:
    from print_shop.rounds.judge import eye_reading

    need = Need("look", "x", "request", "eye", look_for="y")
    old = {"look": {"pick": "A", "reason": "r", "ask": 1, "by": "sponsor"}}
    reading = eye_reading(need, old, {"a.step": "abc"}, ["a.step"])
    assert not reading.passed
    assert "holds no record of the files on its sheet" in reading.why


def test_a_killed_processs_events_reach_the_ledger_when_it_next_loads(tmp_path: Path) -> None:
    model = Scripted([tool("blocked", reason="x")])
    run = Run.start(tmp_path, "r", "A request.", [], model, sleep=lambda _: None)
    run.session.heard = None  # As if the process died with these events on the disk.
    run.session._record(
        "relayed", relay="note", text="Late.", message="m", at_call=1, round=0, **{"from": "x"}
    )
    fresh = Run.load(run.folder, Scripted(), sleep=lambda _: None)
    found = fresh.ledger.rows("SELECT text FROM interventions")
    assert [r["text"] for r in found] == ["Late."]


def test_the_command_line_reads_a_run_back_into_the_ledger_it_names(tmp_path: Path) -> None:
    folder = old_run(tmp_path)
    old_ledger(tmp_path)
    other = tmp_path / "copy.sqlite"
    other.write_bytes((tmp_path / "ledger.sqlite").read_bytes())
    before = (tmp_path / "ledger.sqlite").read_bytes()
    script = Path(__file__).parent.parent / "scripts" / "rounds.py"
    done = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "read-back", "old"]
        + ["--ledger", str(other)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["calls"] == 5
    assert (tmp_path / "ledger.sqlite").read_bytes() == before, "only the ledger it was told"
    assert folder.is_dir()
    gone = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "read-back", "old"]
        + ["--ledger", str(tmp_path / "empty.sqlite")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert gone.returncode == 3 and "isn't in this ledger" in gone.stderr
