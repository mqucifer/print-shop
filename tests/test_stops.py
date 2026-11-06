"""A fixed need follows the part's files, and every stop asks something new
(mqucifer/print-shop#59). No Docker: a scripted model drives each run, and a box answers each bench
command and runs nothing. The parts are made up: a frame given as a reference, panes the model
makes, and a 2D picture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.refine import proposals
from print_shop.rounds import figures, lessons, needs, request
from print_shop.rounds import standing as glass_checks
from print_shop.rounds.needs import Need
from print_shop.rounds.run import FOLLOWED, Run
from print_shop.rounds.standing import Glass
from tests.refine_fakes import pause_record
from tests.rounds_fakes import Scripted, tool
from tests.test_holding import Answers, replies, settled, started
from tests.test_intake import PLAN_ASK, SENT, again, pick, to_the_plan, told
from tests.test_needs_part import DONE, THICK, in_rounds
from tests.test_refine import _pass

FRAME = "references/frame.step"
#: The part as the plan saw it: three panes, `pane_1.step` to `pane_3.step`.
LIGHT = Need(
    "light",
    "light through every pane",
    "request",
    "bench",
    quote="at least 60% of each pane lets light through",
    command=f"light {FRAME} pane_1.step pane_2.step pane_3.step --min-open 60",
    fixed=True,
    measures="light",
)
HOLD = Need(
    "rims",
    "each pane held by a 1.2 mm rim",
    "request",
    "bench",
    quote="a 1.2 mm rim",
    command=(
        f"capture {FRAME} --pocket-z 2 --clearance 0.2 --pane-thickness 1.5 --full-rim 1.2 "
        "--min-visible 60 --panes 3"
    ),
    fixed=True,
    measures="pane-hold",
)
SIDES = Need(
    "sides",
    "each pane 1.5 mm",
    "request",
    "bench",
    quote="1.5 mm panes",
    command="thickness pane_1.step pane_2.step pane_3.step --min 1.5 --max 1.5",
    fixed=True,
    measures="pane-thickness",
)
#: The part as it is now: two panes with new names.
NOW = [Glass(FRAME, ("cell_a.step", "cell_b.step"), 2.0, 0.2, 1.5)]
GLASS_NOW = [
    {
        "frame": FRAME,
        "panes": ["cell_a.step", "cell_b.step"],
        **{"pocket_z": 2, "clearance": 0.2, "pane_thickness": 1.5},
    }
]


# Wanted 1: a fixed need holds its figures, not its file list


def test_a_settled_need_follows_the_parts_panes_and_count_and_keeps_every_figure() -> None:
    light, said = needs.on_part(LIGHT, NOW)
    assert light.command == f"light {FRAME} cell_a.step cell_b.step --min-open 60"
    assert said.startswith(f"settled as `{LIGHT.command}`")
    hold, _ = needs.on_part(HOLD, NOW)
    assert hold.command.endswith("--full-rim 1.2 --min-visible 60 --panes 2")
    assert "--pocket-z 2 --clearance 0.2 --pane-thickness 1.5" in hold.command
    sides, _ = needs.on_part(SIDES, NOW)
    assert sides.command == "thickness cell_a.step cell_b.step --min 1.5 --max 1.5"
    assert (light.fixed, light.quote, light.number) == (LIGHT.fixed, LIGHT.quote, LIGHT.number)


def test_a_need_already_on_the_part_or_on_a_given_before_state_stays_as_it_is() -> None:
    on_it = Need("x", "x", "chosen", "bench", command=f"light {FRAME} cell_a.step cell_b.step")
    assert needs.on_part(on_it, NOW) == (on_it, "")
    before = Need("b", "x", "chosen", "bench", command="capture references/old.step --panes 3")
    assert needs.on_part(before, NOW) == (before, ""), "a given frame `glass` doesn't name"
    other = Need("w", "x", "chosen", "bench", command="wall frame.step --hole 0,1,2")
    assert needs.on_part(other, NOW) == (other, "")
    assert needs.on_part(LIGHT, []) == (LIGHT, ""), "nothing declared, nothing to follow"
    renamed = Need("r", "x", "chosen", "bench", command="light old.step p.step")
    assert needs.on_part(renamed, NOW)[0].command == f"light {FRAME} cell_a.step cell_b.step"


def test_done_checks_a_fixed_need_on_the_parts_panes_where_their_names_and_count_changed(
    tmp_path: Path,
) -> None:
    """On `suncatcher-3` the needs were settled over 14 panes, the part grew to 27 with new
    names, and four needs failed on the count and the names alone."""
    run = started(
        tmp_path,
        tool("done", part=["cell_a.step", "cell_b.step"], glass=GLASS_NOW, **DONE),
    )
    in_rounds(run, LIGHT, HOLD, SIDES, makes=("cell_a.step", "cell_b.step"))
    assert run.drive().name == "met", "not refused as a need off the part"
    asked = [" ".join(a[2:]) for a in run.box.asked]  # type: ignore[attr-defined]
    assert f"light {FRAME} cell_a.step cell_b.step --min-open 60" in asked
    assert any(a.startswith(f"capture {FRAME}") and a.endswith("--panes 2") for a in asked)
    assert "thickness cell_a.step cell_b.step --min 1.5 --max 1.5" in asked
    done = replies(run)[0]
    assert FOLLOWED in done["reply"]
    assert f"- `rims`: settled as `{HOLD.command}`" in done["reply"]
    ran = json.loads((run.folder / "rounds" / "001" / "followed.json").read_text())
    assert ran["sides"] == "thickness cell_a.step cell_b.step --min 1.5 --max 1.5"
    assert run.state.needs[0]["command"] == LIGHT.command, "the needs as settled are kept"


def test_a_bench_call_at_the_parts_own_count_is_the_requirement(tmp_path: Path) -> None:
    run = started(tmp_path, tool("bench", command=needs.on_part(HOLD, NOW)[0].command))
    settled(run, HOLD)
    run.state.glass = GLASS_NOW
    run.save()
    run.drive()
    assert "not_the_requirement" not in (replies(run)[0].get("detail") or {})


def test_a_fixed_need_sent_again_over_the_parts_panes_is_kept_and_a_figure_isnt(
    tmp_path: Path,
) -> None:
    run = started(tmp_path)
    settled(run, LIGHT)
    run.state.glass = GLASS_NOW
    moved = Need(**{**LIGHT.__dict__, "command": f"light {FRAME} cell_b.step cell_a.step"})
    moved.command = f"light {FRAME} cell_a.step cell_b.step --min-open 60"
    assert run._fixed_kept([moved]) == []
    lower = Need(**{**LIGHT.__dict__, "command": f"light {FRAME} cell_a.step --min-open 50"})
    assert "changes only by the Sponsor" in run._fixed_kept([lower])[0]


# Wanted 4: a need whose figure is its command's own argument


ECHO = Need(
    "fit",
    "0.25 mm of play",
    "request",
    "bench",
    quote="0.25 mm of play",
    command="panes frame.step --pocket-z 2.1 --clearance 0.25 --pane-thickness 1.2",
    number="panes.0.gap_3d_mm",
    at_least=0.24,
    at_most=0.26,
    covers=["N1"],
    fixed=True,
    measures="pane-fit",
)


def test_a_number_that_reads_back_its_commands_own_argument_is_refused() -> None:
    """On `eval-fit-1` the fit need passed at 0.25 mm because `panes` built its pane at 0.25."""
    (said,) = needs.echoes_wrong([ECHO])
    assert "`panes.0.gap_3d_mm` is `panes`'s own `--clearance 0.25` read back" in said
    assert "clearance PANE.step FRAME.step" in said
    real = Need(**{**ECHO.__dict__, "command": "clearance pane.step frame.step"})
    real.number = "pairs.0.gap_occt_mm"
    assert needs.echoes_wrong([real]) == []


def test_a_build_input_carries_no_figure_and_says_what_reads_the_file() -> None:
    form = request.read("A frame.\n\n## Non-negotiables\n- Each pane has 0.25 mm of play.\n")
    plain = Need(**{**ECHO.__dict__, "number": "", "at_least": None, "at_most": None})
    assert all(c.value != 0.25 for c in figures.carried(plain))
    (said,) = figures.wrong([plain], form)
    assert said.startswith("`--clearance 0.25` is what `panes` builds from")
    assert "clearance PANE.step FRAME.step" in said


def test_an_unfixed_need_whose_lines_figure_is_its_build_input_is_refused_too() -> None:
    """On `eval-through-2` the fit need was `panes ... --clearance 0.25`, for "0.25 mm of play"."""
    form = request.read("A frame.\n\n## Requirements\n- The pane fits, with 0.25 mm of play.\n")
    plain = Need(
        "fit", "fits", "request", "bench", command="panes f.step --pocket-z 2 --clearance 0.25"
    )
    plain.covers = ["R1"]
    (said,) = figures.echoed([plain], form)
    assert said.startswith("the need 'fit' covers `R1`")
    assert "`--clearance 0.25` is what `panes` builds from" in said
    other = Need(**{**plain.__dict__, "command": "panes f.step --pocket-z 2 --clearance 0.3"})
    assert figures.echoed([other], form) == [], "a play other than the line's isn't its figure"


# Wanted 2: one stop for the eye needs on one picture, and nothing asked twice


def eye(nid: str) -> Need:
    return Need(nid, f"{nid} looks right", "derived", "eye", look_for=f"whether {nid} reads")


VARIANT = [{"name": "A", "files": ["pane.step"]}]


def test_eye_needs_on_one_sheet_go_in_one_ask_or_say_why_not(tmp_path: Path) -> None:
    (tmp_path / "w").mkdir()
    run = started(
        tmp_path,
        tool("ask", question="Right?", need=["crest"], pictures=["layout.png"]),
        tool("ask", question="Right?", need=["crest"], variants=VARIANT),
        tool(
            "ask",
            question="Right?",
            need=["crest"],
            later={"hole": "a close-up, once cut"},
            variants=VARIANT,
        ),
        tool("send_ask", says="One pane."),
    )
    (run.work / "layout.png").write_bytes(b"\x89PNG\r\n\x1a\nlayout")
    settled(run, THICK, eye("crest"), eye("hole"))
    assert run.drive().name == "ask"
    on_picture, apart, later = replies(run)[:3]
    assert on_picture["why"] == "eye_on_picture"
    assert "a picture you drew isn't the part" in on_picture["reply"]
    assert apart["why"] == "eyes_apart" and "`hole` is open too" in apart["reply"]
    assert later["outcome"] == "ok"
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "One pick answers each of these, judged by eye on the sheet:" in question
    assert "- **hole**: a close-up, once cut" in question


def test_one_pick_answers_every_eye_need_the_ask_named(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool("ask", question="Right?", need=["crest", "hole"], variants=VARIANT),
        tool("send_ask", says="One pane."),
    )
    settled(run, THICK, eye("crest"), eye("hole"))
    assert run.drive().name == "ask"
    pick(run, 1, "A", by="stand-in: a builder in a test")
    run.resume()
    assert set(run.state.picks) == {"crest", "hole"}
    assert run.state.picks["hole"]["ask"] == 1


def test_eye_needs_asked_again_on_the_files_a_pick_saw_are_already_picked(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool("ask", question="Right?", need=["crest"], variants=VARIANT),
        tool("send_ask", says="One pane."),
    )
    settled(run, THICK, eye("crest"))
    assert run.drive().name == "ask"
    pick(run, 1, "A", by="stand-in: a builder in a test")
    model = Scripted(
        [tool("ask", question="Still right?", need=["crest"], variants=VARIANT)],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.load(run.folder, model, sleep=lambda _: None)
    box: Any = Answers(run.work)
    run.box = run.tools.box = box
    run.resume()
    run.drive()
    refused = replies(run)[-2]
    assert refused["why"] == "already_picked"
    assert refused["reply"].startswith("Already picked; go on. Ask 1 showed `crest`")
    assert "a stand-in for the Sponsor (a builder in a test) picked A" in refused["reply"]


def test_a_stop_asked_again_on_its_picked_picture_is_refused_naming_the_pick(
    tmp_path: Path,
) -> None:
    """On `suncatcher-3` the layout stop was raised again on the picked picture, byte for byte,
    only to say the fix was understood."""
    s1 = tool("ask", question="Is the profile right?", stop="S1", pictures=["profile.png"])
    run = to_the_plan(tmp_path, [PLAN_ASK], SENT)
    assert run.drive().name == "ask"
    pick(run, 1, "yes")
    run = again(run, [s1], SENT)
    run.resume()
    assert run.drive().name == "ask"
    found = {"pick": "yes", "reason": "Right.", "by": "sponsor", "fix_first": "Round the tip."}
    (run.folder / "asks" / "2" / "pick.json").write_text(json.dumps(found))
    run = again(run, [s1], [tool("write", path="profile.png", content="changed")], [s1])
    run.resume()
    run.drive()
    refused, _, shown = told(run)[-4:-1]
    assert refused["why"] == "already_picked"
    assert "the stop `S1` on the same files and pictures" in refused["reply"]
    assert "the Sponsor picked yes: Right. Fix first: Round the tip." in refused["reply"]
    assert shown["outcome"] == "ok", "a changed picture is something new"


def test_the_replies_dont_say_the_sponsor_before_anyone_has_answered(tmp_path: Path) -> None:
    run = to_the_plan(tmp_path, [PLAN_ASK], SENT)
    run.drive()
    drawn, sent = told(run)[2:4]
    assert "nothing has gone out yet" in drawn["reply"]
    assert "to the Sponsor or someone standing in for them" in sent["reply"]
    assert "gone to the Sponsor" not in drawn["reply"] + sent["reply"]


# Wanted 3: curves stored as strips stop `done`


def test_a_file_with_curves_stored_as_strips_fails_unless_set_aside_with_its_reason(
    tmp_path: Path,
) -> None:
    for name in ("frame.step", "arcs.step", "mine.step", "pane.stl"):
        (tmp_path / name).write_text("x")
    box = Answers(
        tmp_path,
        ("OK", {"small_flat_faces": 1413, "flat_faces": 6069}),
        ("OK", {"small_flat_faces": 1, "flat_faces": 54}),
        ("DISAGREE", {"small_flat_faces": 40, "flat_faces": 60}),
    )
    files = ["frame.step", "arcs.step", "mine.step", "pane.stl"]
    found = glass_checks.curves(box, files, tmp_path, tmp_path / ".kept", {"mine.step": "Text."})
    assert [(one.passed, one.unsure) for one in found] == [
        (False, False),
        (True, False),
        (True, False),
        (True, False),
    ]
    assert "1413 of 6069 flat faces under 1 mm across" in found[0].why
    assert "`building-curves-from-a-layout`" in found[0].why
    assert found[2].why.endswith("set aside by the model: Text.")
    assert len(box.asked) == 3, "an STL has no faces to read"


def test_done_isnt_met_on_strips_and_a_set_aside_is_shown_at_the_next_stop(
    tmp_path: Path,
) -> None:
    strips = ("OK", {"small_flat_faces": 40, "flat_faces": 60})
    glass = [
        {
            "frame": FRAME,
            "panes": ["pane.step"],
            "pocket_z": 2,
            "clearance": 0.2,
            "pane_thickness": 1.5,
        }
    ]
    run = started(
        tmp_path,
        tool("set_aside", curves="pane.step", why="Not yet."),
        tool("done", part=["pane.step"], glass=glass, **DONE),
        tool("set_aside", curves="pane.step", why=""),
        tool("set_aside", curves="pane.step", why="Its small faces are the tab's chamfers."),
        tool("done", part=["pane.step"], glass=glass, **DONE),
        answers=("OK", "OK", "OK", "OK", strips, "OK", "OK", "OK", "OK", strips),
    )
    in_rounds(run, THICK)
    assert run.drive().name == "met"
    early, done, empty, aside, met = replies(run)[:5]
    assert early["why"] == "not_failing"
    assert done["detail"]["met"] is False
    assert glass_checks.CURVES_HEADING in done["reply"]
    assert empty["why"] == "empty" and aside["outcome"] == "ok"
    assert met["detail"]["met"] is True
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "## Small flat faces the model says aren't curves stored as strips" in stop
    assert "- `pane.step`, set aside at call 4: Its small faces are the tab's chamfers." in stop
    assert "set aside by the model: Its small faces are the tab's chamfers." in stop


# Wanted 5: the refinement pass's loose ends


def test_a_finding_the_bench_failed_reaches_the_lessons_as_a_candidate(tmp_path: Path) -> None:
    """On `eval-rings-3` `pause` failed on 0.5 mm of air over the pane, needed no change and
    proposed nothing, so it reached no one."""
    p = _pass(tmp_path, pause_record(13, 0.5, 2.6, "FAIL"))
    p.run()
    (lesson,) = [dict(r) for r in p.ledger.rows("SELECT * FROM lessons")]
    assert lesson["written_by"] == "the refinement pass" and lesson["status"] == "candidate"
    assert lesson["text"].startswith("`pause` at 0.2 then 0.2 mm layers")
    assert lessons.keys_of(p.ledger, lesson["id"]) == [{"command": "pause", "verdict": "FAIL"}]
    assert list(lessons.routes_of(p.ledger, lesson["id"])) == ["model"]
    lines = lessons.evidence_lines(p.ledger, lesson["run"], json.loads(lesson["evidence"]))
    assert lines[0].startswith("refinement 1, pause by bench: `pause` at 0.2")


def test_a_passing_pause_writes_no_lesson(tmp_path: Path) -> None:
    p = _pass(tmp_path, pause_record(13, 0.1, 2.6))
    p.run()
    assert not p.ledger.rows("SELECT * FROM lessons")


def test_a_proposal_tries_only_a_parameter_that_moves_the_ceiling_alone(tmp_path: Path) -> None:
    """On `eval-arcs-1` the pane's thickness was the pocket's depth, so the try thickened the
    pane, and failed its need."""
    before, kept = tmp_path / "before", tmp_path / "kept"
    for folder, pane in ((before, "1.0"), (kept, "1.1")):
        folder.mkdir()
        (folder / "frame.step").write_text(f"frame {pane}")
        (folder / "pane_1.step").write_text(f"pane {pane}")
    assert proposals.moved_besides(before, kept, "frame.step") == ["pane_1.step"]
    (kept / "pane_1.step").write_text("pane 1.0")
    assert proposals.moved_besides(before, kept, "frame.step") == []
    orca = {"findings": [{"kind": "pause", "source": "bench", **pause_record(13, 0.1, 2.6)}]}
    assert proposals.ceiling_now(orca) == 2.6
    tried = {"parameter": "PANE", "becomes": 1.1, "as": "the pocket's depth"}
    said = proposals._not_alone({**tried, "moved_too": ["pane_1.step"]})
    assert said == "PANE → 1.1 (the pocket's depth) changed `pane_1.step` too"
