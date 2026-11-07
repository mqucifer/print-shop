"""The numbers and verdicts each bench command gives, so `needs` refuses a `number` its command
never reports (mqucifer/print-shop#45), or a verdict it never says (mqucifer/print-shop#52).

On `eval-rings-1`, `needs` took `number: min_wall_mm` for `wall`, which reports `least_wall_mm`.
Nothing said so until the harness's clean run at the end. A need's `number` is a path into the
answer's `numbers` (`pairs.0.gap_mesh_mm`), and its first part is one of the keys below. What's
under a key, a row of a list or a dict, isn't listed: it depends on the part.

Each command's keys are read from its JSON: `report.fact(name, ...)` and `report.numbers[name]`
in the bench's code. `tests/test_reports.py` reads them from the code again, through each
command's own functions, so a number added to the bench and not here fails a test. A `*` stands
for a part of the name the code fills in, such as `pocket_section_mm2`.

**Verdicts.** On `eval-through-1` a fit need on `clearance` asked for `verdict OK`. `clearance`
says CLEAR, TOUCHING, TOO CLOSE or INTERFERE, and never OK, so the need could never pass: three
`done`s and four refused `needs` before the model asked. Each command's verdicts below are read
from its code the same way (`report.verdict(...)`, and a pair's `"verdict"` where the worst of
them is the answer). DISAGREE and NO ANSWER aren't listed: any command can say them, and they
never pass a need (`needs.UNSURE`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

REPORTS: dict[str, tuple[str, ...]] = {
    "info": (
        "bodies_mesh",
        "bounds_mm",
        "faces",
        "file_kb",
        "flat_faces",
        "flat_heights",
        "size_mm",
        "small_flat_faces",
        "solids",
        "triangles",
        "valid",
        "volume_mesh_mm3",
        "watertight",
    ),
    "clearance": ("lift", "pairs"),
    "readback": (
        "after_mm3",
        "before_mm3",
        "bodies_before_after",
        "bounds_after_mm",
        "change_mm3",
        "change_pct",
        "cutter_in_body_mm3",
        "cutter_in_body_share",
        "cutter_mm3",
        "expected_change_mm3",
    ),
    "rays": ("axes", "floor_mm", "levels"),
    "panes": ("*_section_mm2", "openings", "panes", "plain_holes"),
    "capture": (
        "*_section_mm2",
        "least_lip_mm",
        "openings",
        "panes",
        "panes_read",
        "plain_holes",
        "plays_differ",
    ),
    "thin": (
        "bodies",
        "body_volumes_mm3",
        "layers_read",
        "narrower_than_nozzle_mm3",
        "sharp_points",
        "thin_at",
        "thin_rays_at",
        "thin_rays_not_at_a_point",
        "thin_spots",
        "thin_wall_area_mm2",
        "thinnest_across_mm",
        "thinnest_flat_mm",
        "thinnest_wall_mm",
        "too_small_at",
    ),
    "wall": (
        "hole_fit_centre",
        "hole_fit_d_mm",
        "hole_from",
        "hole_mm2",
        "hole_runs",
        "hole_to",
        "least_wall_from",
        "least_wall_mm",
        "least_wall_rays_mm",
        "least_wall_to",
        "least_wall_toward",
    ),
    "edges": (
        "edges",
        "faceted_edges",
        "grid_mm",
        "groups",
        "least_radius_at",
        "least_radius_mm",
        "least_rounded_at",
        "least_rounded_mm",
        "min_radius_mm",
        "not_read",
        "reach_mm",
        "rounded",
        "selected",
        "sharp_edges",
    ),
    "light": (
        "least_open_file",
        "least_open_pct",
        "narrowest_band_from",
        "narrowest_band_mm",
        "narrowest_band_to",
        "panes",
    ),
    "thickness": ("files", "thickest_file", "thickest_mm", "thinnest_file", "thinnest_mm"),
    "pause": ("layers", "least_room_over_pane_mm", "openings", "openings_closing"),
    "params": (
        "files",
        "moved_on_their_own",
        "parameter",
        "pieces_left",
        "pieces_moved",
        "pieces_not_moved",
    ),
    "render": ("close_ups", "cuts", "files", "sections", "seen_px", "test_pieces", "views"),
    "layout": (
        "least_width_mm",
        "least_width_piece",
        "overlaps",
        "picture",
        "pieces",
        "pieces_count",
        "step_mm",
        "tips_count",
    ),
}


#: What's under each number's name, where it's the same whatever the part (mqucifer/print-shop#66).
#: On `eval-sleeve-1` two needs were settled on `info`'s `bounds_mm.1.z` and `bounds_mm.1.x`, before
#: the file existed: `bounds_mm` is a list of two corners, each a list of three, so neither path is
#: there, and the run ended `blocked` with the part right. A path is walked through these when its
#: need is settled, with no file to run on. What isn't here, such as a row's field a part may or may
#: not have, is let through as before. `tests/test_bench.py` holds every command's answer on its
#: fixtures to these, so a shape changed in the bench and not here fails a test.
NUMBER, TEXT, FLAG, ANY = "number", "text", "true or false", "any"


@dataclass(frozen=True)
class Vector:
    """A list of `length`, each `of`: a point or a size is three numbers, x, y and z."""

    length: int
    of: Any = NUMBER
    #: What each place is, such as x, y and z.
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rows:
    """A list as long as the part makes it, each row `of`."""

    of: Any


@dataclass(frozen=True)
class Row:
    """A row's fields. A closed row has only these; an open one may have others the part makes."""

    fields: dict[str, Any] = field(default_factory=dict)
    closed: bool = False


@dataclass(frozen=True)
class Keyed:
    """A table by names the part gives, such as a file's or a view's, each `of`."""

    of: Any


XYZ = Vector(3, NUMBER, ("x", "y", "z"))
XY = Vector(2, NUMBER, ("x", "y"))
CORNERS = Vector(2, XYZ, ("the low corner", "the high corner"))
SPREAD = Row({"min": NUMBER, "mean": NUMBER, "max": NUMBER}, closed=True)
#: `edges`' least of a group or a selection (mqucifer/print-shop#65): its counts and its least
#: radius, 0 at a sharp edge, where it is and in which file, or None where nothing is read.
LEAST = Row(
    {
        "sharp": NUMBER,
        "rounded": NUMBER,
        "least_radius_mm": NUMBER,
        "least_at": XYZ,
        "file": TEXT,
    },
    closed=True,
)
#: The groups `edges` reads every edge into, by the file's axes, as `bench/edges.py` names them.
EDGE_GROUPS = ("upright", "top", "bottom", "side")

SHAPES: dict[str, dict[str, Any]] = {
    "info": {
        "bodies_mesh": NUMBER,
        "bounds_mm": CORNERS,
        "faces": NUMBER,
        "file_kb": NUMBER,
        "flat_faces": NUMBER,
        "flat_heights": Rows(Row({"z": NUMBER, "up": NUMBER, "down": NUMBER}, closed=True)),
        "size_mm": XYZ,
        "small_flat_faces": NUMBER,
        "solids": NUMBER,
        "triangles": NUMBER,
        "valid": FLAG,
        "volume_mesh_mm3": NUMBER,
        "watertight": FLAG,
    },
    "clearance": {
        # The file `--lift` moved, along which axis and how far (mqucifer/print-shop#65).
        "lift": Row({"file": TEXT, "along": TEXT, "mm": NUMBER}, closed=True),
        "pairs": Rows(
            Row(
                {
                    "a": TEXT,
                    "b": TEXT,
                    # The verdict's gap: OCCT's on a STEP pair, else the mesh's.
                    "gap_mm": NUMBER,
                    "gap_by": TEXT,
                    "gap_mesh_mm": NUMBER,
                    "gap_occt_mm": NUMBER,
                    "overlap_band_mm3": NUMBER,
                    "overlap_mesh_mm3": NUMBER,
                    "overlap_occt_common_mm3": NUMBER,
                    "overlap_occt_cut_mm3": NUMBER,
                    "verdict": TEXT,
                }
            )
        ),
    },
    "readback": {
        "after_mm3": NUMBER,
        "before_mm3": NUMBER,
        "bodies_before_after": Vector(2, NUMBER, ("before", "after")),
        "bounds_after_mm": CORNERS,
        "change_mm3": NUMBER,
        "change_pct": NUMBER,
    },
    "rays": {
        "axes": TEXT,
        "levels": Rows(
            Row(
                {
                    "across_mm": SPREAD,
                    "at": NUMBER,
                    "closed_loop_around_centre": FLAG,
                    "missed": NUMBER,
                    "rays": NUMBER,
                    "ray_vs_section_worst_mm": NUMBER,
                    "section_area_mm2": NUMBER,
                    "section_fit_d_mm": NUMBER,
                    "section_fit_offset_mm": NUMBER,
                    "start_in_material": NUMBER,
                    "wall_beyond_min_mm": NUMBER,
                }
            )
        ),
    },
    "panes": {
        "*_section_mm2": NUMBER,
        "openings": NUMBER,
        "panes": Rows(
            Row(
                {
                    "centre": XY,
                    "gap_2d_mm": NUMBER,
                    "gap_3d_mm": NUMBER,
                    "opening_mm2": NUMBER,
                    "pane": TEXT,
                    "pane_mm2": NUMBER,
                    "pocket_ceiling_z": NUMBER,
                    "pocket_floor_z": NUMBER,
                }
            )
        ),
    },
    "capture": {
        "*_section_mm2": NUMBER,
        "least_lip_mm": NUMBER,
        "openings": NUMBER,
        "panes": Rows(Row({"centre": XY, "pane": TEXT, "size_mm": XY})),
        "panes_read": NUMBER,
    },
    "thin": {
        "bodies": NUMBER,
        "body_volumes_mm3": Rows(NUMBER),
        "narrower_than_nozzle_mm3": NUMBER,
        "sharp_points": NUMBER,
        "thin_rays_not_at_a_point": NUMBER,
        "thin_spots": NUMBER,
        "thin_wall_area_mm2": NUMBER,
        "thinnest_flat_mm": NUMBER,
        "thinnest_wall_mm": NUMBER,
    },
    "pause": {
        "layers": NUMBER,
        "least_room_over_pane_mm": NUMBER,
        "openings": Rows(
            Row(
                {
                    "opening": TEXT,
                    "pane_top_z": NUMBER,
                    "pause_after_layer": NUMBER,
                    "pause_top_z": NUMBER,
                    "pocket_ceiling_z": NUMBER,
                    "pocket_floor_z": NUMBER,
                    "room_over_pane_mm": NUMBER,
                }
            )
        ),
        "openings_closing": NUMBER,
    },
    "wall": {
        "hole_fit_centre": XYZ,
        "hole_fit_d_mm": NUMBER,
        "hole_mm2": NUMBER,
        "least_wall_from": XYZ,
        "least_wall_mm": NUMBER,
        "least_wall_rays_mm": NUMBER,
        "least_wall_to": XYZ,
        "least_wall_toward": TEXT,
    },
    "thickness": {
        "files": Rows(Row({"file": TEXT, "thickest_mm": NUMBER})),
        "thickest_file": TEXT,
        "thickest_mm": NUMBER,
        "thinnest_file": TEXT,
        "thinnest_mm": NUMBER,
    },
    "light": {
        "least_open_file": TEXT,
        "least_open_pct": NUMBER,
        "panes": Rows(Row({"file": TEXT, "open_mm2": NUMBER, "open_pct": NUMBER})),
    },
    "edges": {
        "grid_mm": NUMBER,
        "groups": Row({name: LEAST for name in EDGE_GROUPS}, closed=True),
        "least_radius_at": XYZ,
        "least_radius_mm": NUMBER,
        "reach_mm": NUMBER,
        # By the names the need's own `--box NAME=...` and `--facing NAME=...` give.
        "selected": Keyed(LEAST),
    },
    "render": {"seen_px": Keyed(Keyed(NUMBER)), "views": Keyed(Row())},
}


VERDICTS: dict[str, tuple[str, ...]] = {
    "info": ("OK", "FAIL"),
    "clearance": ("CLEAR", "TOUCHING", "TOO CLOSE", "TOO LOOSE", "INTERFERE"),
    "readback": ("OK", "FAIL", "NO CHANGE"),
    "rays": ("OK", "FAIL"),
    "panes": ("OK", "FAIL"),
    "capture": ("OK", "FAIL"),
    "thin": ("OK", "FAIL"),
    "wall": ("OK", "FAIL"),
    "edges": ("OK", "FAIL"),
    "light": ("OK", "FAIL"),
    "thickness": ("OK", "FAIL"),
    "pause": ("OK", "FAIL"),
    "params": ("OK", "NO CHANGE", "PARTIAL"),
    "render": ("OK", "FAIL"),
    "layout": ("OK", "FAIL"),
}


#: Options that limit one quantity from both ends, by command. An exact figure carried by one
#: needs the other too (`figures.py`); one with no partner, such as `capture --min-visible`, is
#: all a check can carry. `tests/test_reports.py` reads the pairs from the bench's parser.
BOTH_ENDS: dict[str, tuple[tuple[str, str], ...]] = {
    "clearance": (("--min-gap", "--max-gap"),),
    "thickness": (("--min", "--max"),),
}


#: What each of a command's arguments is in the part, by its name in the bench's parser: the
#: frame, its panes, the frame's other parts (`--with`), or how many panes there are
#: (mqucifer/print-shop#59).
#: A settled need's files and counts in these roles follow the part, as `done` or `ask` declares
#: it in `glass`; its figures don't. On `suncatcher-3` the rim needs were settled at `--panes 14`
#: over `pane_1.step` to `pane_14.step`, the part grew to 27 panes with new names, and four needs
#: failed on the count and the names alone. `tests/test_reports.py` reads each from the parser.
FRAME, PANES, WITH, COUNT = "frame", "panes", "with", "count"
ROLES: dict[str, dict[str, str]] = {
    "light": {"frame": FRAME, "panes": PANES, "--with": WITH},
    "capture": {"frame": FRAME, "--panes": COUNT},
    "thickness": {"panes": PANES},
    "panes": {"frame": FRAME},
    "pause": {"frame": FRAME},
}
#: Each command's positional arguments, in order, by the parser's names: the last of them takes
#: every word left (`nargs="+"`) where it's `panes`.
POSITIONAL: dict[str, tuple[str, ...]] = {
    "light": ("frame", "panes"),
    "capture": ("frame",),
    "thickness": ("panes",),
    "panes": ("frame",),
    "pause": ("frame",),
}

#: Options a command builds what it measures from, and the numbers that read them back
#: (mqucifer/print-shop#59). `panes` cuts its own pane from each opening, `--clearance` in from
#: its edge, so its gaps are that clearance whatever the part's pane is: on `eval-fit-1` the fixed
#: fit need passed at 0.25 mm because 0.25 was what `panes` was told to build. Such an option
#: carries no figure, and such a number checks nothing. `tests/test_reports.py` reads the build
#: from the bench's code.
#: `capture` does the same with no number to read it back (mqucifer/print-shop#66): it cuts its own
#: pane `--clearance` in from the pocket and reads the lips past that play. On `eval-fit-2` the
#: fixed play need was exit-only, on `capture --clearance`, its figure carried by that option
#: alone: the play came back by construction and the given pane was never read.
INPUTS: dict[str, dict[str, tuple[str, ...]]] = {
    "panes": {"--clearance": ("gap_2d_mm", "gap_3d_mm")},
    "capture": {"--clearance": ()},
}
#: The command that reads the part's own file instead, as a need would run it.
_PANE_ON_FRAME = (
    "`clearance PANE.step FRAME.step`, the part's own pane against its frame, with `number` "
    "`pairs.0.gap_occt_mm` and the figure as `at_least` and `at_most`"
)
READS_THE_FILE: dict[str, str] = {"panes": _PANE_ON_FRAME, "capture": _PANE_ON_FRAME}


def echoed(command: str, number: str) -> str | None:
    """The option a need's `number` reads back, if its command builds what it measures from it."""
    last = number.split(".")[-1]
    for option, numbers in INPUTS.get(command, {}).items():
        if last in numbers:
            return option
    return None


def other_end(command: str, option: str) -> str | None:
    """The option that limits the other end of what `option` limits, if its command has one."""
    for low, high in BOTH_ENDS.get(command, ()):
        if option == low:
            return high
        if option == high:
            return low
    return None


def reports(command: str, number: str) -> bool | None:
    """Whether `command` reports `number`'s first part: None for a command not listed here."""
    keys = REPORTS.get(command)
    if keys is None:
        return None
    head = number.split(".", 1)[0]
    return any(fnmatchcase(head, key) for key in keys)


def wrong_number(command: str, number: str) -> str | None:
    """What's wrong with a need's `number` for its command, naming what it does report."""
    if not number or reports(command, number) is not False:
        return None
    named = ", ".join(f"`{key}`" for key in REPORTS[command])
    return (
        f"`{command}` never reports `{number.split('.', 1)[0]}`: its numbers are {named}. A "
        "`number` is a path into them, such as `pairs.0.gap_mesh_mm`"
    )


def selected_unnamed(words: list[str], number: str) -> str | None:
    """An `edges` need's `selected.NAME` that its own command never names: a selection is read
    only for the `--box NAME=...` and `--facing NAME=...` it's given (mqucifer/print-shop#65)."""
    if not words or words[0] != "edges" or not number.startswith("selected."):
        return None
    name = number.split(".")[1] if number.count(".") else ""
    given: list[str] = []
    for k, word in enumerate(words):
        value = None
        if word in ("--box", "--facing") and k + 1 < len(words):
            value = words[k + 1]
        elif word.startswith(("--box=", "--facing=")):
            value = word.split("=", 1)[1]
        if value is not None:
            given.append(value.split("=", 1)[0])
    if name in given:
        return None
    named = ", ".join(f"`{g}`" for g in given) or "none"
    return (
        f"`{number}` isn't in `edges`' answer: a selection is read only for the command's own "
        f"`--box NAME=X0,Y0,Z0,X1,Y1,Z1` or `--facing NAME=DIR`, and it names {named}. Name "
        f"`{name}` in one, or read a group, such as `groups.upright.least_radius_mm`"
    )


def wrong_verdict(command: str, verdict: str) -> str | None:
    """What's wrong with a need's verdict for its command, naming the verdicts it does give."""
    given = VERDICTS.get(command)
    if given is None or verdict in given:
        return None
    named = ", ".join(given)
    return (
        f"`{command}` never says {verdict}: its verdicts are {named}, so a need asking for "
        f"{verdict} could never pass"
    )


def _named(path: str) -> str:
    return f"`{path}`"


def _what(shape: Any) -> str:
    """A shape in words, as a refusal says it."""
    if isinstance(shape, Vector):
        names = f" ({', '.join(shape.names)})" if shape.names else ""
        return f"a list of {shape.length}{names}"
    if isinstance(shape, Rows):
        return "a list of rows, as many as the part makes"
    if isinstance(shape, Row):
        return "a row of " + (", ".join(f"`{k}`" for k in shape.fields) or "fields")
    if isinstance(shape, Keyed):
        return "a table by name"
    return "a number" if shape == NUMBER else str(shape)


def _under(shape: Any, path: str) -> str:
    """How to name a number under a list or a row, as a refusal suggests it."""
    if isinstance(shape, Vector):
        first = f"{path}.0"
        if shape.names:
            ends = ", ".join(f"`{path}.{k}` {n}" for k, n in enumerate(shape.names))
            return f"name one place by its number: {ends}"
        return f"name one place by its number, `{first}` to `{path}.{shape.length - 1}`"
    if isinstance(shape, Rows):
        return f"name a row by its number and then its number, such as `{path}.0`..."
    if isinstance(shape, Row) and shape.fields:
        numbers = [k for k, v in shape.fields.items() if v == NUMBER]
        if numbers:
            return "name one of its numbers: " + ", ".join(f"`{path}.{k}`" for k in numbers)
        under = ", ".join(f"`{path}.{k}`" for k in shape.fields)
        return f"each of {under} holds numbers: name one of them, and then its number"
    return f"name one of its numbers, `{path}.<name>`"


def wrong_path(command: str, number: str) -> str | None:
    """What's wrong with the way a `number` walks into its command's answer, by the shapes the
    answer always has (`SHAPES`), or None where it's right or the shape depends on the part."""
    table = SHAPES.get(command)
    if not table or not number:
        return None
    parts = number.split(".")
    shape = next((v for k, v in table.items() if fnmatchcase(parts[0], k)), None)
    if shape is None:
        return None
    walked = parts[0]
    for part in parts[1:]:
        if shape == ANY:
            return None
        if isinstance(shape, Vector | Rows):
            if not part.isdigit():
                hint = ""
                if isinstance(shape, Vector) and part in shape.names:
                    place = f"{walked}.{shape.names.index(part)}"
                    hint = f": its {part} is {_named(place)}"
                return (
                    f"{_named(number)} isn't in `{command}`'s answer: {_named(walked)} is "
                    f"{_what(shape)}, each named by its place, counted from 0{hint}"
                )
            if isinstance(shape, Vector) and int(part) >= shape.length:
                return (
                    f"{_named(number)} isn't in `{command}`'s answer: {_named(walked)} is "
                    f"{_what(shape)}, so its places are 0 to {shape.length - 1}"
                )
            shape = shape.of
        elif isinstance(shape, Row):
            if part not in shape.fields:
                if shape.closed:
                    return (
                        f"{_named(number)} isn't in `{command}`'s answer: {_named(walked)} is "
                        f"{_what(shape)}"
                    )
                return None
            shape = shape.fields[part]
        elif isinstance(shape, Keyed):
            shape = shape.of
        else:
            return (
                f"{_named(number)} isn't in `{command}`'s answer: {_named(walked)} is "
                f"{_what(shape)}, with nothing under it"
            )
        walked += "." + part
    if shape in (NUMBER, ANY):
        return None
    if isinstance(shape, str):
        return (
            f"{_named(number)} is {shape} in `{command}`'s answer, not a number, so no limit "
            "holds it"
        )
    return (
        f"{_named(number)} is {_what(shape)} in `{command}`'s answer, not one number: "
        f"{_under(shape, number)}"
    )


def unlike(command: str, numbers: dict[str, Any]) -> list[str]:
    """Where an answer's numbers don't have the shape `SHAPES` says: a test reads the bench's
    answers through this, so the shapes are the bench's own."""
    table = SHAPES.get(command) or {}
    wrong: list[str] = []

    def walk(value: Any, shape: Any, path: str) -> None:
        if shape == ANY:
            return
        if shape == NUMBER:
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int | float)
            ):
                wrong.append(f"{path} is {value!r}, not a number")
        elif shape == TEXT:
            if value is not None and not isinstance(value, str):
                wrong.append(f"{path} is {value!r}, not text")
        elif shape == FLAG:
            if not isinstance(value, bool):
                wrong.append(f"{path} is {value!r}, not true or false")
        elif isinstance(shape, Vector | Rows):
            if value is None:
                return
            if not isinstance(value, list):
                wrong.append(f"{path} is {value!r}, not a list")
                return
            if isinstance(shape, Vector) and len(value) != shape.length:
                wrong.append(f"{path} has {len(value)}, not {shape.length}")
            for k, one in enumerate(value):
                walk(one, shape.of, f"{path}.{k}")
        elif isinstance(shape, Row):
            if not isinstance(value, dict):
                wrong.append(f"{path} is {value!r}, not a row")
                return
            if shape.closed and set(value) != set(shape.fields):
                wrong.append(f"{path} has {sorted(value)}, not {sorted(shape.fields)}")
            for name, one in shape.fields.items():
                if name in value:
                    walk(value[name], one, f"{path}.{name}")
        elif isinstance(shape, Keyed):
            if not isinstance(value, dict):
                wrong.append(f"{path} is {value!r}, not a table")
                return
            for name, one in value.items():
                walk(one, shape.of, f"{path}.{name}")

    for key, value in numbers.items():
        shape = next((v for k, v in table.items() if fnmatchcase(key, k)), None)
        if shape is not None:
            walk(value, shape, key)
    return wrong
