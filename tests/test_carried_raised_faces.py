"""A need carried to declared glass keeps every value of an option that takes several
(mqucifer/print-shop#327). `needs._split` read every option as taking one, so `capture
--raised-faces` over a flush panel's pane files kept the first and lost the rest, and the carried
need, the standing `capture` too, read one raised face for every pane. No Docker: a box answers
`capture` as the bench's own parser reads its words. The panel and its files are made up.
"""

from __future__ import annotations

import argparse
import copy
import shlex
from pathlib import Path

import pytest

from print_shop.bench import cli
from print_shop.rounds import judge, needs, standing
from print_shop.rounds.box import Ran
from print_shop.rounds.needs import Need

COLOURS = ("blue", "gold", "green", "red")
PANES = tuple(f"pane_{colour}.step" for colour in COLOURS)
LAID = tuple((pane, pane.replace(".step", "-laid.step")) for pane in PANES)
#: A flush panel, four colours declared, its pane files holding eight panes: the count changed
#: since its need was settled at four, so the need is carried over.
PANEL = standing.Glass(
    "frame.step",
    PANES,
    1.3,
    0.25,
    1.0,
    bodies=8,
    laid=LAID,
    face_thickness=0.8,
    face_clearance=0.25,
)
HOLD = Need(
    "held",
    "each pane held, its raised face level with the frame's",
    "request",
    "bench",
    quote="each pane held",
    command=(
        "capture frame.step --pocket-z 1.3 --clearance 0.25 --pane-thickness 1 --panes 4 "
        f"--raised-faces {' '.join(PANES)} --face-play 0.25"
    ),
    fixed=True,
    measures="pane-hold",
)


class _Capture:
    """A box that answers `capture` as the bench's parser reads its words: OK where every pane
    file of the panel is read as a raised face at the part's count, and FAIL saying how many it
    read. The standing `light` and `pause` are answered OK."""

    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    def bench(self, argv: list[str], where: Path) -> Ran:
        self.asked.append(argv)
        args = cli.parser().parse_args(argv)
        if args.command != "capture":
            return Ran(0, "verdict: OK: made up\n", 1.0)
        read = list(args.raised_faces or [])
        if read == list(PANES) and args.panes == PANEL.count():
            return Ran(0, f"verdict: OK: {len(read)} raised faces read, each level\n", 1.0)
        return Ran(1, f"verdict: FAIL: {len(read)} raised faces for {args.panes} panes\n", 1.0)


def test_a_flush_panels_carried_capture_keeps_all_four_raised_faces_and_passes(
    tmp_path: Path,
) -> None:
    carried, said = needs.on_part(HOLD, [PANEL])
    words = carried.argv()
    at = words.index("--raised-faces")
    assert words[at + 1 : at + 5] == list(PANES), "each pane file in place"
    assert words[at + 5] == "--face-play"
    assert "--panes 8" in carried.command, "the count follows the part"
    assert words[1] == "frame.step" and [w for w in words if w in PANES] == list(PANES)
    assert said.startswith(f"settled as `{HOLD.command}`")
    args = cli.parser().parse_args(words)
    assert args.raised_faces == list(PANES) and args.frame == "frame.step"
    for name in PANEL.files():
        (tmp_path / name).write_text("made up", encoding="utf-8")
    box = _Capture()
    reading = judge.read_need(box, carried, tmp_path, tmp_path / "kept")  # type: ignore[arg-type]
    assert reading.passed, reading.why
    found = standing.check(box, [PANEL], tmp_path, tmp_path / "kept", [(carried, reading)])
    held = next(one for one in found if one.check == standing.HOLD)
    assert held.passed and held.command == carried.command, held.why
    assert f"as the need `{HOLD.id}` reads it" in held.why


def _options() -> list[tuple[str, str, argparse.Action]]:
    """Every option of every bench command, by its `--` names."""
    top = cli.parser()
    sub = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))
    found = []
    for command, parser in sub.choices.items():
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            found += [(command, n, action) for n in action.option_strings if n.startswith("--")]
    return found


def _argparse_takes(action: argparse.Action, name: str, words: list[str]) -> int:
    """How many of `words` argparse gives the option `name`, read by a parser of it alone."""
    alone = copy.copy(action)
    alone.type, alone.choices, alone.required, alone.default = None, None, False, None
    parser = argparse.ArgumentParser(add_help=False)
    parser._add_action(alone)
    _, left = parser.parse_known_args([name, *words])
    return len(words) - len(left)


def _split_takes(command: str, name: str, words: list[str]) -> int:
    _, options = needs._split(command, [name, *words])
    return len(options[0][1])


def test_each_bench_options_value_count_in_split_is_its_parsers() -> None:
    """A new option that takes several values, or none, is read as its parser reads it."""
    options = _options()
    assert ("capture", "--raised-faces") in {(command, name) for command, name, _ in options}
    several = ["v1.step", "v2.step", "v3.step", "v4.step"]
    for command, name, action in options:
        wanted = _argparse_takes(action, name, several)
        assert _split_takes(command, name, several) == wanted, (command, name)
        least, most = needs.takes(command)[name]
        if least != most:
            # Where it may take more or fewer, it stops at the next option, as argparse does.
            stopped = ["v1.step", "v2.step", "--more", "v3.step"]
            wanted = _argparse_takes(action, name, stopped)
            assert _split_takes(command, name, stopped) == wanted, (command, name)
    assert _split_takes("capture", "--raised-faces", several) == 4
    assert _split_takes("capture", "--pane-thickness", several) == 1
    assert _split_takes("thin", "--one-body", several) == 0


@pytest.mark.parametrize(
    ("command", "words"),
    [
        ("capture", ["frame.step", "--pocket-z", "2", "--clearance", "0.2", "--panes", "3"]),
        ("capture", ["frame.step", "--pocket-z=2", "--min-lip", "-1", "--panes"]),
        ("light", ["frame.step", "p1.step", "p2.step", "--with", "cap.step", "--min-open", "60"]),
        ("thickness", ["p1.step", "--min", "1.5", "--max", "1.5"]),
        ("pause", ["frame.step", "--pocket-z", "2", "--unknown", "--pane-thickness", "1.5"]),
        ("nothing", ["a.step", "--x", "1", "--y", "--z"]),
    ],
)
def test_a_one_value_option_reads_as_on_main(command: str, words: list[str]) -> None:
    """Each option that takes one value takes the word after it, whatever it is, as on main."""
    assert needs._split(command, words) == _main_split(words)


def _main_split(words: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Main's `_split` (70d5c1f), each value as a list of one."""
    positional: list[str] = []
    options: list[tuple[str, list[str]]] = []
    k = 0
    while k < len(words):
        word = words[k]
        if word.startswith("--"):
            if "=" in word:
                name, value = word.split("=", 1)
                options.append((name, [value]))
            else:
                options.append((word, [words[k + 1] if k + 1 < len(words) else ""]))
                k += 1
        else:
            positional.append(word)
        k += 1
    return positional, options


def test_a_need_carried_today_reads_as_on_main() -> None:
    """A flat panel's needs, with no option of several values, carry over word for word as main
    carries them: their files, their count and their figures."""
    panes = ("cell_a.step", "cell_b.step")
    flat = standing.Glass("frame.step", panes, 2.0, 0.2, 1.5, ("cap.step",))
    cases: dict[str, str | None] = {
        "light old.step p.step --min-open 60 --with x.step": (
            "light frame.step cell_a.step cell_b.step --min-open 60 --with cap.step"
        ),
        "capture frame.step --pocket-z 2 --clearance 0.2 --pane-thickness 1.5 --panes 3": (
            "capture frame.step --pocket-z 2 --clearance 0.2 --pane-thickness 1.5 --panes 2"
        ),
        "thickness p1.step p2.step p3.step --min 1.5 --max=1.5": (
            "thickness cell_a.step cell_b.step --min 1.5 --max 1.5"
        ),
        "pause frame.step --pocket-z 2 --pane-thickness 1.5": None,
    }
    for command, wanted in cases.items():
        need = Need("n", "x", "chosen", "bench", command=command)
        carried, _ = needs.on_part(need, [flat])
        assert carried.command == (wanted or command), command
        assert shlex.split(carried.command)[0] == command.split()[0]
