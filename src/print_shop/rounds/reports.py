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

from fnmatch import fnmatchcase

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
    "clearance": ("pairs",),
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
        "least_radius_at",
        "least_radius_mm",
        "least_rounded_at",
        "least_rounded_mm",
        "min_radius_mm",
        "not_read",
        "reach_mm",
        "rounded",
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
}


#: Options that limit one quantity from both ends, by command. An exact figure carried by one
#: needs the other too (`figures.py`); one with no partner, such as `capture --min-visible`, is
#: all a check can carry. `tests/test_reports.py` reads the pairs from the bench's parser.
BOTH_ENDS: dict[str, tuple[tuple[str, str], ...]] = {
    "clearance": (("--min-gap", "--max-gap"),),
    "thickness": (("--min", "--max"),),
}


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
