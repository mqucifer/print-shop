"""The print side decides what's read (mqucifer/print-shop#192).

A leg piece the assembly says prints on its side (`layers: x`, and `fit-bed` laid it on -x) had
its downward faces read lying on -z: it was flagged for faces that are walls on its side, and
its real overhang, a tail inset 3.2 to 5 mm on every side hanging over air along 12 mm, was
missed. And `load` read every strain against `layers` that disagreed with the side `fit-bed`
laid the part on, until a stand-in caught it.

No Docker: a scripted model drives each run, and a box answers each bench command and runs
nothing. The bench's laying helper is answered with a mesh made here, turned onto the side the
request names by the bench's own `ON_BED` turns, as `bench/refine.py`'s `mesh` turns a file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import manifold3d as m3
import numpy as np
import trimesh

from print_shop.bench.refine import MARK, ON_BED
from print_shop.rounds import downward, needs, print_sides
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool
from tests.test_holding import Answers, replies

LEG = """\
A side table's leg, split for the bed: its lower piece, printed with no supports.

## Kind
furniture

## Stops
- no plan
"""
ASSEMBLY = {
    "parts": [
        {"name": "lower", "file": "lower.step", "at": [0, 0, 0], "layers": "x"},
        {"name": "upper", "file": "upper.step", "at": [0, 0, 100], "layers": "z"},
    ],
    "joints": [],
}


def _box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Any:
    size = (x1 - x0, y1 - y0, z1 - z0)
    return m3.Manifold.cube(size).translate((x0, y0, z0))


def leg_piece() -> trimesh.Trimesh:
    """A bar 30 x 30 mm and 100 tall, standing as stored, with a tail 12 mm long on its top,
    inset 3.2 mm from its -x face, 5 from its +x face and 5 from each y face; and a slot 10 mm
    square through it along x, from z 40 to 50.

    Laid on -x, the tail's -x face is 3.2 mm over the bed with nothing under it along its 12 mm:
    20 x 12, 240 mm², to support. The slot runs up through it there, its faces walls. Stood on
    -z, the slot's roof, 30 x 10, 300 mm², is a ceiling, and the tail stands on the bar."""
    solid = _box(0, 30, 0, 30, 0, 100) + _box(3.2, 25, 5, 25, 100, 112)
    built = (solid - _box(-1, 31, 10, 20, 40, 50)).to_mesh()
    return trimesh.Trimesh(
        np.asarray(built.vert_properties)[:, :3], np.asarray(built.tri_verts), process=True
    )


PIECE = leg_piece()


def laid_as_the_bench_lays(args: list[str], where: Path | None) -> Ran:
    """The bench's `refine` helper answering a `mesh` request: the leg piece turned onto the side
    named, as `bench/refine.py` turns it, and set on the bed."""
    request = json.loads(args[0])
    assert request["request"] == "mesh" and where is not None, request
    out = where / request["out"]
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in request["files"]:
        side = request["on_bed"].get(name, "-z")
        axis, degrees = ON_BED[side]
        mesh = PIECE.copy()
        mesh.apply_transform(
            trimesh.transformations.rotation_matrix(np.radians(degrees), axis)  # type: ignore[no-untyped-call]
        )
        mesh.apply_translation((0, 0, -mesh.bounds[0][2]))
        target = out / (Path(name).stem + ".stl")
        mesh.export(str(target))
        files[name] = {"on_bed": side, "stl": f"{request['out']}/{target.name}"}
    return Ran(0, MARK + json.dumps({"files": files}), 0.1)


class Box(Answers):
    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "refine":
            return laid_as_the_bench_lays(args, where)
        return super().helper(module, args, timeout, where)


def fit_bed(**sides: str) -> tuple[str, dict[str, Any]]:
    """`fit-bed`'s answer laying each named part's file on the side given."""
    parts = {n: {"file": f"{n}.step", "side": side, "fits": True} for n, side in sides.items()}
    return "OK", {"parts": parts}


def started(root: Path, *turns: Any, answers: tuple[Any, ...] = ()) -> Run:
    model = Scripted(*[[t] for t in turns], [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", LEG, [], model, sleep=lambda _: None)
    (run.work / "assembly.json").write_text(json.dumps(ASSEMBLY))
    for name in ("lower.step", "upper.step", "make.py"):
        (run.work / name).write_text("x")
    box: Any = Box(run.work, *answers)
    run.box = box
    run.tools.box = box
    return run


ASK = tool(
    "ask", question="Is the lower piece right?", variants=[{"name": "A", "files": ["lower.step"]}]
)
SENT = tool("send_ask", says="The leg's lower piece, its tail on top.")


def downward_rows(run: Run) -> list[Any]:
    return run.ledger.rows("SELECT * FROM standing WHERE check_name='downward' ORDER BY id")


# Which side each file lies on


def test_the_side_is_on_bed_then_a_bed_need_then_layers_and_fit_bed_then_minus_z() -> None:
    (bed,), _ = needs.read(
        [
            {
                "id": "lid-up",
                "what": "the lid prints top down",
                "source": "chosen",
                "check": {"by": "bed", "file": "lid.step", "side": "+z"},
            }
        ],
        "A lid.\n",
    )
    files = ["lid.step", "lower.step", "upper.step", "rail.step", "foot.step", "box.step"]
    layers = {
        "lower.step": ("x", "assembly.json"),
        "upper.step": ("z", "assembly.json"),
        "rail.step": ("y", "assembly.json"),
        "foot.step": ("x", "assembly.json"),
    }
    fitted = {"lower.step": ("+x", 4), "rail.step": ("-z", 4), "foot.step": ("-x", 4)}
    laid = downward.laid_on(files, [bed], {"foot.step": "-z"}, layers, fitted)
    assert laid == {
        "lid.step": ("+z", "as the bed need `lid-up` names it"),
        "lower.step": (
            "+x",
            "as its layers run along x in `assembly.json`, and `fit-bed` laid it on +x at call 4",
        ),
        "upper.step": (
            "-z",
            "as its layers run along z in `assembly.json`, and no `fit-bed` has said which z "
            "side, so -z",
        ),
        "rail.step": (
            "-y",
            "as its layers run along y in `assembly.json`, so -y; `fit-bed` laid it on -z at call "
            "4, across its layers",
        ),
        "foot.step": (
            "-z",
            "as `done`'s `on_bed` names it, though `assembly.json` stacks its layers along x",
        ),
        "box.step": ("-z", downward.NOTHING_NAMES),
    }


def test_the_run_finds_its_assembly_and_the_latest_fit_bed(tmp_path: Path) -> None:
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "table.json").write_text(json.dumps(ASSEMBLY))
    (tmp_path / "notes.json").write_text(json.dumps({"parts": "not an assembly"}))
    def laid(file: str, side: str) -> dict[str, Any]:
        return {"parts": {"lower": {"file": file, "side": side}}}

    rows = [
        (3, ["fit-bed", "parts/lower.step"], laid("parts/lower.step", "-z")),
        (5, ["fit-bed", "parts/table.json"], laid("parts/lower.step", "-x")),
        (6, ["thin", "parts/lower.step"], laid("x", "+z")),
    ]
    named = print_sides.named_assemblies(rows)
    assert named == ["parts/table.json"]
    found = print_sides.layered(tmp_path, named)
    assert {f: (p.name, p.layers, p.assembly) for f, p in found.items()} == {
        "parts/lower.step": ("lower", "x", "parts/table.json"),
        "parts/upper.step": ("upper", "z", "parts/table.json"),
    }
    assert print_sides.layered(tmp_path, []) == found, "found in the folder where nothing named it"
    assert print_sides.fitted(rows) == {
        "parts/lower.step": print_sides.Fitted("parts/lower.step", "-x", 5)
    }


# The leg piece on its side


def test_a_part_printed_on_its_side_has_its_downward_faces_read_on_its_side(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        tool("bench", command="fit-bed assembly.json"),
        ASK,
        SENT,
        answers=(fit_bed(lower="-x", upper="-z"),),
    )
    assert run.drive().name == "ask", [r["reply"] for r in replies(run)]
    # An ask keeps a check's words, not its numbers: the tail's underside, 3.2 mm off the bed
    # along its 12 mm, in the file's own coordinates (the mesh's edges a fraction in), and
    # nothing at the slot, whose faces are walls on this side.
    (row,) = downward_rows(run)
    assert row["why"].startswith(
        "lying on its -x side, as its layers run along x in `assembly.json`, and `fit-bed` laid "
        "it on -x at call 1, 232.5 mm² looks down past 45° with nothing under it, and would need "
        "support: 232.5 mm² from (3.2, 5.1, 100.44) to (3.2, 24.9, 111.94), 232.5 mm² of it "
        "flat, a ceiling or a bridge, 90° past vertical at its worst, in the file's own "
        "coordinates."
    ), row["why"]
    assert row["passed"] == 0 and row["verdict"] == "NEEDS SUPPORT"
    stop = (run.folder / "asks" / "1" / "question.md").read_text()
    assert (
        "- lower.step, NEEDS SUPPORT: lying on its -x side, as its layers run along x in "
        "`assembly.json`, and `fit-bed` laid it on -x at call 1, "
    ) in stop


def test_the_same_part_with_nothing_naming_a_side_is_read_on_minus_z_and_says_so(
    tmp_path: Path,
) -> None:
    """The reading as it was for every file: the slot's roof, a wall on the part's real side, is
    what's flagged, and the tail isn't."""
    run = started(tmp_path, ASK, SENT)
    (run.work / "assembly.json").unlink()
    assert run.drive().name == "ask"
    (row,) = downward_rows(run)
    assert row["why"].startswith(f"lying on its -z side, {downward.NOTHING_NAMES}, "), row["why"]
    (where,) = re.findall(r"mm² from \(([^)]*)\) to \(([^)]*)\)", row["why"])
    assert [float(v.split(", ")[2]) for v in where] == [50.0, 50.0], "the slot's roof"
    assert "(3.2," not in row["why"], "and not the tail"


# `load` against `fit-bed`


def test_load_notes_layers_that_disagree_with_the_side_fit_bed_laid_the_part_on(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        tool("bench", command="fit-bed lower.step upper.step"),
        tool("bench", command="load assembly.json --weight 5 --at 15,15,212"),
        tool("bench", command="fit-bed assembly.json"),
        tool("bench", command="load assembly.json --weight 5 --at 15,15,212"),
        answers=(fit_bed(lower="-z", upper="-z"), "OK", fit_bed(lower="-x", upper="-z"), "OK"),
    )
    assert run.drive().name == "blocked"
    first, crossed, again, agreed = replies(run)[:4]
    assert "layers_against_fit_bed" not in (first.get("detail") or {})
    assert crossed["reply"].startswith(
        "`load` read the part lower with its layers stacked along x, as `assembly.json` says, "
        "but `fit-bed` laid `lower.step` on its -z side at call 1, where they'd stack along z: "
        "each strain across its layers here is read for x. Print it on a side square to x, or "
        'give it `"layers": "z"` and read `load` again.\n\n'
    ), crossed["reply"]
    assert crossed["detail"]["layers_against_fit_bed"] == [
        {
            "part": "lower",
            "file": "lower.step",
            "layers": "x",
            "assembly": "assembly.json",
            "fit_bed_side": "-z",
            "fit_bed_call": 1,
        }
    ]
    assert "`load` read the part" not in again["reply"], "only a `load` reply leads with it"
    assert "layers_against_fit_bed" not in (agreed.get("detail") or {}), agreed["reply"]


# `done` names every printed file's side


BROKEN_DOWN = tool(
    "breakdown",
    parts=[{"name": "lower", "what": "the leg's lower piece, its tail on top"}],
    joins=[],
    look=["a plain square leg"],
    **{"from": "the request"},
)
NEEDS = tool(
    "needs",
    needs=[
        {
            "id": "printable",
            "what": "it prints",
            "source": "derived",
            "check": {
                "by": "bench",
                "measures": "size",
                "command": "info lower.step",
                "number": "volume_mesh_mm3",
                "at_least": 1,
            },
        }
    ],
)
DONE = {"script": "make.py", "part": ["lower.step"], "fix_shape": "first", "closing": "Here."}


def test_done_refuses_a_printed_file_with_no_side_and_says_what_the_assembly_gives(
    tmp_path: Path,
) -> None:
    """A refinement pass printed a lid skirt-down because `done` named no side for it
    (design-runs:42), and a run chose a lid's side without reading what its first layer bridges
    (runs9-lid:3)."""
    run = started(
        tmp_path,
        tool("bench", command="fit-bed assembly.json"),
        BROKEN_DOWN,
        NEEDS,
        tool("done", **DONE),
        tool("done", on_bed={"lower.step": "-x"}, **DONE),
        answers=(fit_bed(lower="-x", upper="-z"),),
    )
    assert run.drive().name in ("met", "blocked")
    refused, sent = [r for r in replies(run) if r["name"] == "done"]
    assert refused["why"] == "no_bed_side"
    assert refused["reply"] == (
        "Nothing was checked: `on_bed` names no side for `lower.step`. Each printed file's side "
        "is data: the refinement pass lays it so, and its downward faces are read lying on it.\n"
        "- `lower.step`: -x, as its layers run along x in `assembly.json`, and `fit-bed` laid it "
        "on -x at call 1.\n"
        "Before you choose a side, read what its first layer lies on and what it bridges: "
        "`slice FILE --at AXIS=AT` a layer (0.2 mm) in from the face that goes down, as `info` "
        "gives its box, shows what's on the bed and what isn't. Then send `done` again with "
        '`on_bed` giving each file its side as your script stores it, `-z` too: {"lid.step": '
        '"+z"}.'
    )
    assert sent["outcome"] == "ok", sent["reply"]


def test_a_request_that_leaves_the_side_to_the_refinement_pass_isnt_refused_for_it(
    tmp_path: Path,
) -> None:
    line = "Which side each piece prints on is left to the refinement pass."
    assert print_sides.left_to_the_pass(f"A leg.\n\n- {line}\n") == line
    assert not print_sides.left_to_the_pass("A leg. The refinement pass reads gap fill.\n")
    assert not print_sides.left_to_the_pass("A leg that prints on its side.\n")
    model = Scripted(BROKEN_DOWN, NEEDS, [tool("done", **DONE)], [tool("blocked", reason="x")])
    model.turns = [[t] if isinstance(t, dict) else t for t in model.turns]
    request = LEG.replace("no supports.\n", f"no supports.\n{line}\n")
    run = Run.start(tmp_path, "r", request, [], model, sleep=lambda _: None)
    for name in ("lower.step", "make.py"):
        (run.work / name).write_text("x")
    box: Any = Box(run.work)
    run.box = box
    run.tools.box = box
    run.drive()
    (done,) = [r for r in replies(run) if r["name"] == "done"]
    assert done["outcome"] == "ok", done["reply"]
