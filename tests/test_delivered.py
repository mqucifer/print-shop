"""What a person is given, with no Docker: a file's colour read the same on both sides, and the
folder to print from that the harness writes at the stop for approval (mqucifer/print-shop#26)."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_shop.bench import colours
from print_shop.rounds import review
from print_shop.rounds.judge import PRINTABLE, Standing
from print_shop.rounds.needs import Need, Reading

# Colours


def test_a_colour_is_a_name_a_shade_of_one_or_hex() -> None:
    assert colours.rgb("blue") == colours.NAMED["blue"]
    assert colours.rgb("Light-Green") > colours.rgb("green")
    assert colours.rgb("dark red")[0] < colours.rgb("red")[0]
    assert colours.rgb("#2a5bd7") == (0x2A, 0x5B, 0xD7)
    with pytest.raises(colours.NotAColour, match="isn't a colour the sheet can draw"):
        colours.rgb("sparkly")
    look = colours.parse("pane_1.step=amber:the cup")
    assert (look.file, look.colour, look.label) == ("pane_1.step", "amber", "the cup")
    assert colours.parse(colours.argument("a.step", "clear")) == colours.Look("a.step", "clear")
    with pytest.raises(colours.NotAColour):
        colours.parse("a.step=blue:" + "x" * 40)


def test_colours_for_files_with_none_given_are_apart_from_each_other_and_those_given() -> None:
    """A cycle of 5 drew `cup-1`'s 6th file in the 1st's colour."""
    given = [colours.rgb("grey"), colours.rgb("blue")]
    found = colours.distinct(16, given)
    assert len(set(found)) == 16
    everyone = [*given, *found]
    for i, one in enumerate(everyone):
        for other in everyone[i + 1 :]:
            assert sum(abs(a - b) for a, b in zip(one, other, strict=True)) > 60, (one, other)


# The folder to print from


def panes_named() -> dict[str, dict[str, str]]:
    return {
        "pane_1.step": {"colour": "blue", "label": "cup"},
        "pane_2.step": {"colour": "red", "label": "coffee band"},
        "pane_10.step": {"colour": "green", "label": "background, middle left"},
        "frame.step": {"colour": "grey", "label": "frame"},
    }


def test_each_pane_is_named_by_its_number_its_label_and_its_colour() -> None:
    names = review.shared_names(
        ["pane_1.step", "pane_2.step", "pane_10.step", "x.stl"], panes_named()
    )
    assert names == {
        "pane_1.step": "pane_01-cup-blue.step",
        "pane_2.step": "pane_02-coffee-band-red.step",
        "pane_10.step": "pane_10-background-middle-left-green.step",
        "x.stl": "x.stl",
    }


def made_up_run(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    """A frame made two ways and two panes, as plain text standing in for STEP files."""
    judged = tmp_path / "judged"
    judged.mkdir()
    for name in ("frame.step", "frame_base.step", "cap.step", "pane_1.step", "pane_2.step"):
        (judged / name).write_text(f"ISO-10303-21;\nHEADER;\nDATA;\n{name}\nENDSEC;\n")
    need = Need(
        "held", "each pane is held", "derived", "bench", command="capture frame.step --panes 2"
    )
    eye = Need("look", "it reads as a cup", "request", "eye", look_for="a cup")
    readings = [
        Reading("held", "bench", True, "OK"),
        Reading("look", "eye", True, "the Sponsor picked A: it reads"),
    ]
    standing = [
        Standing(PRINTABLE, f, f"thin {f}", True, False, "OK", "OK", {})
        for f in ("frame_base.step", "cap.step", "frame.step", "pane_1.step", "pane_2.step")
    ]
    standing[-1] = Standing(
        PRINTABLE, "pane_2.step", "thin pane_2.step", False, True, "DISAGREE", "the two differ", {}
    )
    overlap = {
        "files": {
            "frame.step": {"bounds_mm": [[0, 0, 0], [100, 125, 5.8]]},
            "pane_1.step": {"bounds_mm": [[10, 10, 2], [20, 20, 4]]},
        },
        "pairs": [
            {"files": ["frame.step", "frame_base.step"], "mesh_mm3": 20662.0, "occt_mm3": 20662.0}
        ],
    }
    facts: dict[str, object] = {
        "run": "made-up",
        "round_n": 2,
        "approval": 1,
        "judged": judged,
        "part": ["frame_base.step", "cap.step", "frame.step", "pane_1.step", "pane_2.step"],
        "groups": [
            {
                "name": "two-parts",
                "about": "base and cap",
                "files": ["frame_base.step", "cap.step"],
            },
            {"name": "one frame with pause", "about": "", "files": ["frame.step"]},
        ],
        "looks": panes_named(),
        "wanted": [need, eye],
        "readings": readings,
        "standing": standing,
        "overlap": overlap,
        "swaps": [
            {
                "need": "pause-room",
                "old": {"command": "pause frame.step --min-room 0.2"},
                "old_reading": "FAIL, 0.0 mm over the pane",
                "new": None,
                "reason": "the bench reads the island as a ceiling",
            }
        ],
        "closing": "It's the cup.",
    }
    return tmp_path / "review", facts


def test_the_folder_has_a_group_a_folder_and_the_frame_never_beside_its_halves(
    tmp_path: Path,
) -> None:
    out, facts = made_up_run(tmp_path)
    held = review.write(out, **facts)  # type: ignore[arg-type]
    a, b = out / "A-two-parts", out / "B-one-frame-with-pause"
    assert sorted(p.name for p in a.iterdir()) == [
        "cap.step",
        "frame_base.step",
        "pane_01-cup-blue.step",
        "pane_02-coffee-band-red.step",
    ]
    assert sorted(p.name for p in b.iterdir()) == [
        "frame.step",
        "pane_01-cup-blue.step",
        "pane_02-coffee-band-red.step",
    ]
    assert (a / "pane_01-cup-blue.step").read_bytes() == (
        tmp_path / "judged" / "pane_1.step"
    ).read_bytes()
    assert [g["folder"] for g in held["groups"]] == ["A-two-parts", "B-one-frame-with-pause"]
    readme = (out / "README.md").read_text()
    assert "**Open one folder only.**" in readme
    assert "`frame.step` and `frame_base.step`: 20662 mm³" in readme
    assert "| `pane_02-coffee-band-red.step` | `pane_2.step` | coffee band | red |" in readme
    assert "| **held**: each pane is held | OK | exit 0 | `frame.step` |" in readme
    # What wasn't checked, from the records.
    assert "No requirement's check named `frame_base.step`, `cap.step`, `pane_1.step`" in readme
    assert "**look** passed on a pick, not a bench" in readme
    assert "`thin` had no answer to rely on for `pane_2.step`" in readme
    assert "**pause-room** was changed while its check failed" in readme
    assert "the bench reads the island as a ceiling" in readme
    assert "Nothing here has been sliced or printed" in readme
    assert "It's the cup." in readme.split("## What the model said at the end")[1]


def test_writing_the_folder_again_replaces_it(tmp_path: Path) -> None:
    out, facts = made_up_run(tmp_path)
    review.write(out, **facts)  # type: ignore[arg-type]
    (out / "stale.txt").write_text("old")
    review.write(out, **facts)  # type: ignore[arg-type]
    assert not (out / "stale.txt").exists()
