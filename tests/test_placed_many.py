"""A file an assembly names in several parts, read once for each part, where it sits
(mqucifer/print-shop#343).

The part is made here: a slice of a flat ring, 20 to 40 mm from its axis, 30° wide and 5 mm
thick, built centred in its own frame, its middle 30 mm out along x at the origin. A part entry
turns it about z to its place on the ring and moves it back out by its `at`, written to six
decimals, so the faces two neighbours meet by are off the axes and a hair apart, as a design
run's are (mqucifer/print-shop#334). On `ring.json`, the parts `s1`, `s2` and `s3` sit from 0° to
30°, 30° to 60° and 90° to 120°: `s1` and `s2` touch; `s2` and `s3` are 30° apart, 2 x 20 x
sin 15° = 10.3528 mm at their inner corners; `s1` and `s3` 60°, 20 mm. On `pair.json` two
neighbours each carry a small pane on top, in their `with`. Nothing is any Sponsor's design.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.bench import placed as placed_module
from print_shop.bench.cli import parser
from print_shop.bench.report import NoAnswer, Report
from print_shop.rounds import end_checks, reports, transcript
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool

PARTS = '''
import math

import cadquery as cq


def box(x0, x1, y0, y1, z0, z1):
    return (
        cq.Workplane("XY")
        .box(x1 - x0, y1 - y0, z1 - z0)
        .translate(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    )


# A 30° slice of a ring 20 to 40 mm out and 5 mm thick, from -15° to 15°, its middle moved to
# the origin.
ring = cq.Workplane("XY").circle(40).circle(20).extrude(5)
half, far = math.radians(15), 80
wedge = (
    cq.Workplane("XY")
    .polyline([(0, 0), (far * math.cos(half), -far * math.sin(half)),
               (far * math.cos(half), far * math.sin(half))])
    .close()
    .extrude(5)
)
cq.exporters.export(ring.intersect(wedge).translate((-30, 0, 0)), "slice.step")
# A pane on top of it, in its middle.
cq.exporters.export(box(-2, 2, -2, 2, 5, 6), "pane.step")
# A rib standing on it, along its middle.
cq.exporters.export(box(-8, 8, -1, 1, 5, 8), "rib.step")
'''

#: Each slice's middle, in degrees about z: from 0° to 30°, 30° to 60°, and 90° to 120°.
RING = {"s1": 15.0, "s2": 45.0, "s3": 105.0}
OUT = 30.0
INNER = 20.0


def entry(name: str, degrees: float, others: list[str] | None = None) -> dict[str, Any]:
    """A slice's part entry: turned to its place and moved back out, `at` to six decimals."""
    turned = math.radians(degrees)
    found: dict[str, Any] = {
        "name": name,
        "file": "slice.step",
        "layers": "z",
        "turn": [["z", degrees]],
        "at": [round(OUT * math.cos(turned), 6), round(OUT * math.sin(turned), 6), 0],
    }
    if others is not None:
        found["with"] = others
    return found


def write(work: Path, name: str, *parts: dict[str, Any]) -> None:
    (work / name).write_text(json.dumps({"parts": list(parts)}), encoding="utf-8")


def ring_files(where: Path) -> None:
    write(where, "ring.json", *(entry(n, d) for n, d in RING.items()))
    write(where, "pair.json", entry("s1", 15, ["pane.step"]), entry("s2", 45, ["pane.step"]))


def chord(degrees: float) -> float:
    return 2 * INNER * math.sin(math.radians(degrees) / 2)


# The placings, with no container.


@pytest.fixture
def flat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The files, empty, in the working directory, as the bench reads them in its `/work`."""
    for name in ("slice.step", "pane.step", "lid.step"):
        (tmp_path / name).write_text("")
    ring_files(tmp_path)
    monkeypatch.chdir(tmp_path)
    return Path()


def test_a_file_in_three_parts_is_read_once_as_each_by_its_part(flat: Path) -> None:
    assembly = "ring.json"
    found = placed_module.of(["slice.step", "lid.step"], assembly)
    assert list(found) == ["slice.step"]
    copies = found["slice.step"]
    assert [one.read_as for one in copies] == [f"slice.step as {n}" for n in RING]
    assert [one.part for one in copies] == list(RING)
    assert [one.turn for one in copies] == [(("z", d),) for d in RING.values()]
    # Each read where it's named, each once, though it's named twice; a file it doesn't place
    # as it's named.
    read = placed_module.each(["slice.step", "lid.step", "slice.step"], found)
    assert [(f, one.part if one else None) for f, one in read] == [
        ("slice.step", "s1"),
        ("slice.step", "s2"),
        ("slice.step", "s3"),
        ("lid.step", None),
    ]


def test_a_file_in_several_parts_with_is_read_as_each_and_a_file_in_one_keeps_its_name(
    flat: Path,
) -> None:
    found = placed_module.of(["slice.step", "pane.step"], "pair.json")
    assert [one.read_as for one in found["pane.step"]] == ["pane.step as s1", "pane.step as s2"]
    # Its pane goes with each slice: turned and moved as that slice is.
    assert [one.at for one in found["pane.step"]] == [one.at for one in found["slice.step"]]
    write(flat, "one.json", entry("s1", 15, ["pane.step"]))
    once = placed_module.of(["slice.step", "pane.step"], "one.json")
    assert [one.read_as for one in once["slice.step"]] == ["slice.step"]
    assert [one.read_as for one in once["pane.step"]] == ["pane.step"]


def test_a_reading_names_the_parts_it_reads_a_file_as(flat: Path) -> None:
    assembly = "ring.json"
    found = placed_module.of(["slice.step"], assembly, ["s1", "s3"])
    assert [one.read_as for one in found["slice.step"]] == ["slice.step as s1", "slice.step as s3"]
    # A file none of whose parts is named is read as each of them.
    found = placed_module.of(["slice.step", "pane.step"], "pair.json", ["s2"])
    assert [one.part for one in found["slice.step"]] == ["s2"]
    assert [one.part for one in found["pane.step"]] == ["s2"]
    with pytest.raises(NoAnswer, match=r"--part names s9.*the parts that do are s1, s2, s3"):
        placed_module.of(["slice.step"], assembly, ["s1", "s9"])
    with pytest.raises(NoAnswer, match="give the assembly with --placed"):
        placed_module.of(["slice.step"], None, ["s1"])


def test_the_reply_lists_every_placement_by_its_part_and_says_a_file_read_several_times(
    flat: Path,
) -> None:
    assembly = "ring.json"
    report = Report("clearance", [])
    found = placed_module.of(["slice.step"], assembly)
    placed_module.say_placed(report, found, assembly, ["slice.step"])
    assert set(report.numbers["placed"]) == {f"slice.step as {n}" for n in RING}
    assert report.numbers["placed"]["slice.step as s2"]["file"] == "slice.step"
    assert report.numbers["placed"]["slice.step as s2"]["turn"] == [["z", 45.0]]
    assert reports.unlike("clearance", report.numbers) == []
    said = "\n".join(report.facts)
    for name in RING:
        assert f"slice.step, as the part {name} in {assembly}" in said, said
    assert "named once here, is named in 3 parts" in said and "read 3 times" in said, said
    assert "name each with --part" in said, said
    # Named, it says which.
    report = Report("clearance", [])
    found = placed_module.of(["slice.step"], assembly, ["s1", "s3"])
    placed_module.say_placed(report, found, assembly, ["slice.step"], ["s1", "s3"])
    assert set(report.numbers["placed"]) == {"slice.step as s1", "slice.step as s3"}
    assert "read as the 2 of its parts" in "\n".join(report.facts)


def test_each_command_takes_the_parts_it_reads() -> None:
    top = parser()
    args = top.parse_args(["clearance", "slice.step", "--placed", "r.json", "--part", "s1", "s3"])
    assert args.parts == ["s1", "s3"]
    args = top.parse_args(["render", "slice.step", "--part", "s1", "--part", "s2"])
    assert args.parts == ["s1", "s2"]
    words = ["fit-on", "slice.step", "--onto", "slice.step", "--along", "+x", "--from", "clear"]
    args = top.parse_args([*words, "--as", "s3", "--placed", "r.json"])
    assert args.moving_as == "s3" and args.parts == []


def test_an_end_check_place_is_turned_back_by_the_placement_of_the_copy_it_s_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fit-on` read `s3` onto `s2`, two parts of one file: the place is looked for on the file
    turned back by `s2`'s placement, and on the moving file by `s3`'s, never by one for both."""
    asked: list[tuple[str, list[list[float]], str]] = []

    def find(_: Any, file: str, corners: list[list[float]], across: str, __: Path) -> Any:
        asked.append((file, corners, across))
        return {"found": False, "why": "none here"}

    monkeypatch.setattr(end_checks, "_find_arm", find)
    placed = {
        f"slice.step as {n}": {"file": "slice.step", "part": n, "turn": [["z", d]], "at": at}
        for n, d, at in (("s2", 90, [0, 30, 0]), ("s3", 180, [-30, 0, 0]))
    }
    way = end_checks.Way("s3", "slice.step", ("slice.step",), "+x", "a need")
    corners = [[1.0, 25.0, 0.0], [3.0, 28.0, 5.0]]
    place = end_checks.Place(
        0, "slice.step", corners, 0.5, "x", 4.0, False, read_as="slice.step as s2"
    )
    end_checks._place(
        None,  # type: ignore[arg-type]
        way,
        place,
        {},
        tmp_path,
        tmp_path,
        "bend-0-0",
        placed,
        "slice.step as s3",
    )
    on_s2 = end_checks.own_frame(corners, "x", placed["slice.step as s2"])
    back = [[corners[0][0] - 4, *corners[0][1:]], [corners[1][0] - 4, *corners[1][1:]]]
    on_s3 = end_checks.own_frame(back, "x", placed["slice.step as s3"])
    assert asked == [("slice.step", *on_s2), ("slice.step", *on_s3)]
    assert on_s2 != on_s3


# The bench, in its image.


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    found = tmp_path_factory.mktemp("placed-many")
    (found / "parts.py").write_text(PARTS, encoding="utf-8")
    made = host.run(["parts.py"], found, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    ring_files(found)
    return found


def ask(work: Path, *args: str) -> tuple[str, dict[str, Any]]:
    done = host.run(list(args), work)
    saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    assert saved, f"nothing saved: {done.stdout}{done.stderr}"
    return done.stdout, json.loads((work / f"{saved.group(1)}.json").read_text())


def pairs(record: dict[str, Any]) -> dict[frozenset[str], dict[str, Any]]:
    return {frozenset((p["a"], p["b"])): p for p in record["numbers"]["pairs"]}


@pytest.mark.bench
def test_three_alike_slices_are_read_pairwise_where_each_sits(work: Path) -> None:
    text, record = ask(work, "clearance", "slice.step", "--placed", "ring.json")
    found = pairs(record)
    s1, s2, s3 = (f"slice.step as {n}" for n in RING)
    assert set(found) == {frozenset((s1, s2)), frozenset((s2, s3)), frozenset((s1, s3))}, text
    # Neighbours turned off the axes, a hair apart by their `at`: touching.
    assert found[frozenset((s1, s2))]["verdict"] == "TOUCHING", text
    assert found[frozenset((s1, s2))]["gap_mm"] <= 1e-4
    for (a, b), degrees in (((s2, s3), 30), ((s1, s3), 60)):
        pair = found[frozenset((a, b))]
        assert pair["verdict"] == "CLEAR", text
        assert pair["gap_mm"] == pytest.approx(chord(degrees), abs=1e-3)
    assert set(record["numbers"]["placed"]) == {s1, s2, s3}
    assert "is named in 3 parts of ring.json, so it's read 3 times" in text, text
    assert reports.unlike("clearance", record["numbers"]) == []


@pytest.mark.bench
def test_a_reading_of_some_of_the_parts_reads_only_those(work: Path) -> None:
    args = ["clearance", "slice.step", "--placed", "ring.json", "--part", "s1", "s3"]
    text, record = ask(work, *args)
    assert record["verdict"] == "CLEAR", text
    [pair] = record["numbers"]["pairs"]
    assert {pair["a"], pair["b"]} == {"slice.step as s1", "slice.step as s3"}
    assert pair["gap_mm"] == pytest.approx(chord(60), abs=1e-3)


@pytest.mark.bench
def test_two_neighbours_from_one_file_touch_and_each_carries_its_pane(work: Path) -> None:
    text, record = ask(work, "clearance", "slice.step", "pane.step", "--placed", "pair.json")
    found = pairs(record)
    assert len(found) == 6, text
    touching = {
        frozenset(("slice.step as s1", "slice.step as s2")),
        frozenset(("slice.step as s1", "pane.step as s1")),
        frozenset(("slice.step as s2", "pane.step as s2")),
    }
    for both, pair in found.items():
        assert pair["verdict"] == ("TOUCHING" if both in touching else "CLEAR"), (both, text)
    assert set(record["numbers"]["placed"]) == {
        "slice.step as s1",
        "slice.step as s2",
        "pane.step as s1",
        "pane.step as s2",
    }


@pytest.mark.bench
def test_the_sheet_draws_each_slice_where_it_sits_by_its_part(work: Path) -> None:
    args = ["render", "slice.step", "--views", "top", "--size", "256", "--renders-out", "ring"]
    text, record = ask(work, *args, "--placed", "ring.json")
    assert record["verdict"] == "OK", text
    names = {f"slice.step as {n}" for n in RING}
    assert set(record["numbers"]["placed"]) == names
    seen = record["numbers"]["seen_px"]["top"]
    assert set(seen) == names and all(px > 0 for px in seen.values()), seen
    # Each its own colour, marked by its part.
    files = record["numbers"]["files"]
    assert {f["mark"] for f in files} == set(RING)
    assert len({tuple(f["rgb"]) for f in files}) == 3
    assert reports.unlike("render", {"placed": record["numbers"]["placed"]}) == []


@pytest.mark.cae
def test_fit_on_moves_one_slice_onto_the_others_made_from_its_file(work: Path) -> None:
    """`s3`, 90° to 120°, slid in along -x from out past +x passes through `s2` and `s1`, its
    own file's other parts, and never through itself."""
    if host.image_id(host.CAE_IMAGE) is None:
        pytest.fail(f"no image {host.CAE_IMAGE}: run `scripts/bench.py build cae` first")
    args = ["fit-on", "slice.step", "--onto", "slice.step", "--along", "+x"]
    args += ["--from", "clear", "--step", "4", "--placed", "ring.json"]
    text, record = ask(work, *args)
    assert record["verdict"] == "NO ANSWER" and "--as PART" in record["why"], text
    text, record = ask(work, *args, "--as", "s3")
    assert record["verdict"] == "BLOCKED", text
    numbers = record["numbers"]
    assert numbers["moving"] == "slice.step as s3"
    assert numbers["onto"] == ["slice.step as s1", "slice.step as s2"]
    met = {p["read_as"] for p in numbers["places"]}
    assert "slice.step as s2" in met and "slice.step as s3" not in met, text
    assert {p["file"] for p in numbers["places"]} == {"slice.step"}
    assert not any(p["seated"] for p in numbers["places"]), text
    assert set(numbers["placed"]) == {f"slice.step as {n}" for n in RING}
    assert reports.unlike("fit-on", numbers) == []


#: An assembly run of two given files, a slice and its rib, each named in three parts: the shape
#: of a design run's whole-assembly need at `done` (mqucifer/print-shop#343).
WHOLE = "clearance references/slice.step references/rib.step --placed table.json"
ASSEMBLY_REQUEST = """\
Stand a rib on each of three given ring slices, as one assembly. They're approved: don't change
them.

## Requirements
- No part passes into another.

## Stops
- no plan
"""


@pytest.mark.bench
def test_done_takes_a_whole_assembly_need_and_reads_it_once_a_part(
    work: Path, tmp_path: Path
) -> None:
    """`done` on an assembly of two given files each in three parts: it's an assembly, not too few
    parts; the need and the harness's own placed reading each read the six parts, 15 pairs."""
    given = tmp_path / "given"
    given.mkdir()
    for name in ("slice.step", "rib.step"):
        given.joinpath(name).write_bytes((work / name).read_bytes())
    need = {
        "id": "whole",
        "what": "no part passes into another",
        "source": "request",
        "quote": "No part passes into another.",
        "covers": ["R1"],
        "check": {"by": "bench", "measures": "fit", "command": WHOLE, "verdicts": ["TOUCHING"]},
    }
    model = Scripted(
        [
            tool(
                "breakdown",
                parts=[{"name": n, "what": "a given slice or rib"} for n in ("slice", "rib")],
                joins=[{"parts": ["slice", "rib"], "how": "the rib stands on its slice"}],
                look=["three slices of a ring, each with a rib"],
                **{"from": "the request"},
            )
        ],
        [tool("needs", needs=[need])],
        [tool("done", script="make.py", part=["table.json"], fix_shape="first", closing="Here.")],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(
        tmp_path / "runs",
        "r",
        ASSEMBLY_REQUEST,
        [given / "slice.step", given / "rib.step"],
        model,
        sleep=lambda _: None,
        assembly_run=True,
    )
    (run.work / "make.py").write_text("print('placed')\n")
    parts = []
    for name, degrees in RING.items():
        for file, part in (("slice.step", name), ("rib.step", f"r{name[1:]}")):
            one = entry(part, degrees)
            one["file"] = f"references/{file}"
            parts.append(one)
    (run.work / "table.json").write_text(json.dumps({"parts": parts}))
    ending = run.drive()
    events = [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]
    done = [e for e in events if e["name"] == "done"][-1]
    assert ending.name == "met", [(e["name"], e["reply"][:600]) for e in events]
    assert done["detail"]["met"] is True, done["reply"]
    reply = done["reply"]
    assert "The result is an assembly of given parts: `table.json`" in reply, reply
    # The harness's own placed reading names the copies by their parts.
    slices, ribs = "references/slice.step as s", "references/rib.step as r"
    assert f"`{slices}1` and `{slices}2` touch" in reply, reply
    assert f"`{ribs}3` and `{slices}3` touch" in reply, reply
    assert "nothing passes into anything" in reply, reply
    # Each reading read every part: the need's and the harness's, 15 pairs each.
    kept = run.folder / "rounds" / "001"
    placed = [
        json.loads((kept / name).read_text())
        for name in ("readings/whole.json", "standing/placed-table_json.json")
    ]
    for record in placed:
        assert record["argv"][-2:] == ["--placed", "table.json"], record["argv"]
        assert len(record["numbers"]["placed"]) == 6 and len(record["numbers"]["pairs"]) == 15
    touching = {
        frozenset((p["a"], p["b"])) for p in placed[0]["numbers"]["pairs"]
        if p["verdict"] == "TOUCHING"
    }
    assert touching == {
        frozenset((f"{slices}1", f"{slices}2")),
        *(frozenset((f"{slices}{k}", f"{ribs}{k}")) for k in (1, 2, 3)),
    }
