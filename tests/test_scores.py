"""A score for each round against a scenario's right answer, with no Docker and no model
(mqucifer/print-shop#67): the form, the distance and improvement read from the ledger, the kind
of each round, and the phases view that splits first rounds from refinement rounds."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import needs, scores
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.needs import Need

SCRIPT = Path(__file__).parent.parent / "scripts" / "rounds.py"

#: A made-up part: a plate whose hole has a peg through it with 0.3 to 0.8 mm all round, and an
#: opening 8 mm or more across. Nothing here is from a run.
RIGHT: dict[str, Any] = {
    "scenario": "peg-through-a-plate",
    "ranges": [
        {
            "id": "gap",
            "what": "the least gap round the peg",
            "command": "clearance",
            "names": ["peg.step"],
            "number": "pairs.0.gap_mesh_mm",
            "at_least": 0.3,
            "at_most": 0.8,
        },
        {
            "id": "across",
            "what": "the opening's least width across",
            "command": "rays",
            "number": "levels.0.across_mm.min",
            "at_least": 8.0,
            "scale": 1.0,
        },
    ],
}
GAP = Need(
    "gap", "the peg's gap", "request", "bench",
    command="clearance plate.step references/peg.step --min-gap 0.3 --max-gap 0.8",
)


def clearance(gap: float) -> dict[str, Any]:
    return {"pairs": [{"a": "plate.step", "b": "peg.step", "gap_mesh_mm": gap}]}


def a_run(path: Path, gaps: list[float], phases: list[int], before: float | None) -> DesignLedger:
    """A run with a round for each gap, its last call in each phase, and the model's own
    `clearance` at call 2 reading `before`."""
    ledger = DesignLedger(path)
    ledger.run_started(
        "r", request_sha="s", events="e", harness_commit="c", skills_sha="k", image="i",
        image_id="d", alias="a", limits={},
    )
    ledger.needs_accepted("r", 1, [GAP], 3)
    if before is not None:
        ledger.bench_call(
            "r", 0, 2, ["clearance", "plate.step", "references/peg.step"], 0,
            {"verdict": "CLEAR", "numbers": clearance(before)}, {}, 1.0,
        )
    call = 4
    for n, (gap, phase) in enumerate(zip(gaps, phases, strict=True), 1):
        first, last = call, call + 2
        for k in range(first, last + 1):
            ledger.add("calls", run="r", n=k, round=n, phase=phase, segment=1)
        ledger.round_started("r", n, first, 1, {}, [])
        ledger.reading(
            "r", n, GAP,
            needs.Reading("gap", "bench", True, "", 0, "CLEAR", None, "",
                          {"numbers": clearance(gap)}),
        )
        ledger.round_ended("r", n, last, "make.py", "", {}, 0, 1.0, True, "wider")
        call = last + 1
    return ledger


def ranges() -> list[scores.Range]:
    return scores.read_right(RIGHT)[1]


@pytest.mark.parametrize(
    ("broken", "said"),
    [
        ({"ranges": RIGHT["ranges"]}, "names its `scenario`"),
        ({**RIGHT, "answer": 1}, "only"),
        ({**RIGHT, "ranges": []}, "at least one range"),
        ({**RIGHT, "ranges": [{**RIGHT["ranges"][0], "at_least": 1.0}]}, "is over its `at_most`"),
        ({**RIGHT, "ranges": [{**RIGHT["ranges"][1], "scale": None}]}, "give `scale`"),
        ({**RIGHT, "ranges": [{**RIGHT["ranges"][0], "command": "clearance a.step"}]}, "one bench"),
        ({**RIGHT, "ranges": [RIGHT["ranges"][0], RIGHT["ranges"][0]]}, "the same id"),
        ({**RIGHT, "ranges": [{**RIGHT["ranges"][0], "at_most": "0.8"}]}, "is a number"),
        ({**RIGHT, "ranges": [{**RIGHT["ranges"][0], "at_least": None, "at_most": None}]},
         "`at_least`, `at_most` or both"),
    ],
)
def test_a_right_answer_is_held_to_its_form(broken: dict[str, Any], said: str) -> None:
    with pytest.raises(scores.NotRight, match=said):
        scores.read_right(broken)


def test_a_range_is_its_distance_over_its_scale_and_none_inside_it() -> None:
    gap, across = ranges()
    assert gap.scale == pytest.approx(0.5)
    assert gap.distance(0.5) == 0
    assert gap.distance(1.0) == pytest.approx(0.4)
    assert gap.distance(0.1) == pytest.approx(0.4)
    assert across.distance(7.5) == pytest.approx(0.5)
    assert across.distance(30.0) == 0, "a range with one end has none on the other"


def test_a_range_reads_only_its_command_naming_its_files() -> None:
    gap = ranges()[0]
    assert gap.reads(["clearance", "plate.step", "references/peg.step", "--min-gap", "0.3"])
    assert not gap.reads(["clearance", "plate.step", "peg-lifted.step"]), "a copy isn't the file"
    assert not gap.reads(["info", "references/peg.step"])


def test_a_number_in_a_list_is_picked_by_its_file_wherever_it_comes() -> None:
    numbers = {"panes": [{"file": "a/big.step", "open_pct": 81.5}, {"file": "small.step",
                                                                    "open_pct": 60.1}]}
    assert scores.number_in(numbers, "panes[file=small.step].open_pct") == 60.1
    assert scores.number_in(numbers, "panes[file=big.step].open_pct") == 81.5
    assert scores.number_in(numbers, "panes.1.open_pct") == 60.1
    with pytest.raises(LookupError):
        scores.number_in(numbers, "panes[file=other.step].open_pct")


def test_each_round_is_scored_from_the_benchs_answers_with_where_each_came_from(
    tmp_path: Path,
) -> None:
    # Before the first round 1.2, then 1.0, 1.1 with no person between, and 0.5 after a note.
    ledger = a_run(tmp_path / "ledger.sqlite", [1.0, 1.1, 0.5], [0, 0, 1], before=1.2)
    found = scores.score(ledger.rows, "r", ranges())
    assert [one.kind for one in found] == ["first", "again", "refinement"]
    first, again, refined = found
    assert first.distance_before == pytest.approx(0.8)
    assert first.distance_after == pytest.approx(0.4)
    assert first.improvement == pytest.approx(0.5)
    assert first.ranges[0]["before"]["from"] == "the model's bench call at call 2"
    assert first.ranges[0]["after"]["from"] == "need `gap`'s reading at round 1's end"
    assert again.improvement == pytest.approx(-0.5)
    assert again.said == "further from the right answer"
    assert refined.improvement == 1.0 and refined.said == "right after it"
    # The opening was never read: it counts in no sum, and says so.
    assert (refined.read, refined.of, refined.compared) == (1, 2, 1)
    assert refined.ranges[1]["after"] is None


def test_where_two_answers_read_a_range_the_score_takes_the_further(tmp_path: Path) -> None:
    ledger = a_run(tmp_path / "ledger.sqlite", [0.5], [0], before=None)
    other = Need("gap-2", "again", "request", "bench", command="clearance plate.step peg.step")
    ledger.needs_accepted("r", 2, [GAP, other], 5)
    ledger.reading(
        "r", 1, other,
        needs.Reading(
            "gap-2", "bench", True, "", 0, "CLEAR", None, "", {"numbers": clearance(1.3)}
        ),
    )
    (one,) = scores.score(ledger.rows, "r", ranges())
    assert one.ranges[0]["after"]["value"] == 1.3
    assert one.improvement is None and one.said == "no range was read both before and after it"


def test_with_nothing_read_at_its_end_the_models_last_bench_call_in_it_counts(
    tmp_path: Path,
) -> None:
    ledger = a_run(tmp_path / "ledger.sqlite", [0.5], [0], before=None)
    ledger.bench_call(
        "r", 1, 5, ["rays", "plate.step"], 0,
        {"numbers": {"levels": [{"across_mm": {"min": 7.0}}]}}, {}, 1.0,
    )
    (one,) = scores.score(ledger.rows, "r", ranges())
    assert one.ranges[1]["after"] == {
        "value": 7.0, "distance": 1.0, "from": "the model's bench call at call 5"
    }
    assert one.distance == 1.0


def test_the_phases_view_splits_first_rounds_from_refinement_rounds(tmp_path: Path) -> None:
    ledger = a_run(tmp_path / "ledger.sqlite", [1.0, 1.1, 0.5], [0, 0, 1], before=1.2)
    kinds = ledger.rows("SELECT round, kind FROM round_kinds WHERE run='r' ORDER BY round")
    assert [(r["round"], r["kind"]) for r in kinds] == [(1, "first"), (2, "again"),
                                                        (3, "refinement")]
    found = scores.score(ledger.rows, "r", ranges())
    scores.keep(ledger, found, "sha")
    scores.keep(ledger, found, "sha")  # Kept again, nothing is added twice.
    assert ledger.rows("SELECT COUNT(*) AS n FROM round_scores")[0]["n"] == 3
    rows = ledger.rows("SELECT phase, rounds, kind, improvement FROM phases WHERE run='r'")
    assert [tuple(r) for r in rows] == [(0, 2, "first", 0.5), (1, 1, "refinement", 1.0)]
    assert scores.phases(found) == [
        {"phase": 0, "kind": "first", "rounds": [1, 2], "improvement": 0.5,
         "distance_after": pytest.approx(0.6)},
        {"phase": 1, "kind": "refinement", "rounds": [3], "improvement": 1.0, "distance_after": 0},
    ]


def test_an_older_ledgers_phases_view_is_made_again_with_its_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    DesignLedger(path)
    db = sqlite3.connect(path)
    db.executescript(
        "DROP VIEW phases; CREATE VIEW phases AS SELECT run, phase FROM calls GROUP BY run, phase;"
    )
    db.close()
    ledger = DesignLedger(path)
    held = {r["name"] for r in ledger.rows("PRAGMA table_info(phases)")}
    assert {"begun_by", "rounds", "kind", "improvement"} <= held


def test_a_ledger_read_only_is_left_as_it_was(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    a_run(path, [1.0], [0], before=1.2)
    held = hashlib.sha256(path.read_bytes()).hexdigest()
    (one,) = scores.score(scores.read_only(path), "r", ranges())
    assert one.improvement == pytest.approx(0.5)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == held
    with pytest.raises(FileNotFoundError):
        scores.read_only(tmp_path / "none.sqlite")


def test_a_ledger_from_before_standing_is_scored_from_what_it_has(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    a_run(path, [1.0], [0], before=1.2)
    db = sqlite3.connect(path)
    db.executescript("DROP TABLE standing;")
    db.close()
    (one,) = scores.score(scores.read_only(path), "r", ranges())
    assert one.distance == pytest.approx(0.4)


def test_a_runs_right_answer_is_kept_outside_its_work_and_scored_at_each_round(
    tmp_path: Path,
) -> None:
    ledger = a_run(tmp_path / "ledger.sqlite", [1.0], [0], before=1.2)
    folder = tmp_path / "r"
    (folder / "work").mkdir(parents=True)
    assert scores.after_round(ledger, folder) == [], "a run with no right answer has no score"
    source = tmp_path / "right.json"
    source.write_text(json.dumps(RIGHT), encoding="utf-8")
    scores.keep_right(folder, source, ledger)
    assert (folder / scores.RIGHT_FILE).is_file()
    assert not list((folder / "work").rglob("*.json")), "the model's directory never holds it"
    assert [r["id"] for r in ledger.rows("SELECT id FROM right_ranges ORDER BY id")] == [
        "across", "gap"
    ]
    (one,) = scores.after_round(ledger, folder)
    row = ledger.rows("SELECT * FROM round_scores")[0]
    assert (row["kind"], row["improvement"]) == ("first", pytest.approx(0.5))
    assert json.loads(row["ranges"])[0]["after"]["value"] == 1.0


def test_the_command_line_scores_a_run_read_only_unless_kept(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    a_run(path, [1.0, 0.5], [0, 1], before=1.2)
    right = tmp_path / "right.json"
    right.write_text(json.dumps(RIGHT), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if "KEY" not in k} | {"HOME": str(tmp_path)}

    def cli(*words: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(tmp_path), "scores", "r", *words],
            capture_output=True, text=True, env=env, check=False,
        )

    done = cli("--right", str(right))
    assert done.returncode == 0, done.stderr
    said = json.loads(done.stdout)
    assert [r["improvement"] for r in said["rounds"]] == [pytest.approx(0.5), 1.0]
    assert [p["kind"] for p in said["phases"]] == ["first", "refinement"]
    assert DesignLedger(path).rows("SELECT COUNT(*) AS n FROM round_scores")[0]["n"] == 0
    assert cli("--right", str(right), "--keep").returncode == 0
    assert DesignLedger(path).rows("SELECT COUNT(*) AS n FROM round_scores")[0]["n"] == 2
    refused = cli()
    assert refused.returncode == 3 and "there's no right answer" in refused.stderr


def test_each_scenarios_right_answer_is_in_its_form_and_read_by_its_kinds_commands() -> None:
    from print_shop.rounds import request, toolsets

    evals = Path(__file__).parent / "skill_evals" / "designing-stained-glass" / "evals.json"
    held = [one for one in json.loads(evals.read_text())["evals"] if "right" in one]
    assert held, "at least one scenario shows the form"
    for one in held:
        scenario, found = scores.read_right(one["right"])
        assert scenario == one["id"]
        commands = toolsets.named(request.read(one["request"]).kind).commands
        assert all(r.command in commands for r in found), one["id"]
