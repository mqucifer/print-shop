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
        "strip_faces",
        "strip_runs",
        "triangles",
        "valid",
        "volume_mesh_mm3",
        "watertight",
    ),
    "clearance": ("bodies", "lift", "pairs", "placed", "places", "slide"),
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
        "face_play_least_mm",
        "face_z",
        "faces",
        "raised_faces",
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
        # A one-layer point at a hole's rim, noted and not thin (mqucifer/print-shop#238).
        "rim_points",
        "rim_points_at",
        "sharp_points",
        "thin_at",
        "thin_places",
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
        "hole_alone",
        "hole_fit_centre",
        "hole_fit_d_mm",
        "hole_from",
        "hole_mm2",
        "hole_opens_at",
        "hole_read",
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
        "stepped",
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
    "render": (
        "close_ups",
        "cuts",
        "files",
        "placed",
        "scratch",
        "sections",
        "seen_px",
        "test_pieces",
        "views",
    ),
    "faces": ("bosses", "flat_faces", "holes", "partial_cylinders"),
    "slice": ("cuts",),
    "fit-on": (
        "along",
        "depth_mm",
        "from_clear",
        "from_mm",
        "moving",
        "onto",
        "placed",
        "places",
        "rays",
        "readings",
        "step_mm",
        "steps",
    ),
    "bend": (
        "across_layers_pct",
        "arm_mm3",
        "back_moves_mm",
        "layers",
        "limit_from",
        "limit_pct",
        "mesh_mm",
        "pushes",
        "room_behind_mm",
        "thickness_mm",
        "worst",
        "would_pass",
    ),
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
    # Every `pieces` command's, as one: a map's readings, a fix's before and after, a build's
    # parts (mqucifer/print-shop#152, mqucifer/print-shop#153), a dome's map and its flat
    # slices (mqucifer/print-shop#154), and a slice's seams, the came strips and rings that
    # hold them, and two placed slices (mqucifer/print-shop#166), held from the inside
    # (mqucifer/print-shop#190).
    "pieces": (
        "alike",
        "bands",
        "bar_wide_mm",
        "bed_mm",
        "border_mm",
        "box_edge_step_mm",
        "box_mm",
        "changed",
        "colour",
        "colour_in_picture",
        "colour_nearest",
        "colour_off",
        "colour_rgb",
        "colour_unread",
        "colour_was_off",
        "colours",
        "corners_off_mm",
        "curve_off_outline_mm",
        "cuts_off_mm",
        "dark_under",
        "dead_ends",
        "dead_ends_at_default_dark",
        "dead_ends_at_patch_after",
        "dead_ends_at_patch_before",
        "dead_ends_count",
        "dead_ends_in_box_after",
        "dead_ends_in_box_before",
        "dome_mm",
        "edges_asked",
        "edges_held",
        "edges_in_rings",
        "edges_off_mm",
        "faces_apart_deg",
        "fits_bed",
        "flagged",
        "flags",
        "flange_wide_mm",
        "flats_off_mm",
        "folds",
        "frame",
        "frame_faces",
        # What would have been cut as a picture's frame, with the glass on it
        # (mqucifer/print-shop#240).
        "frame_cut",
        "frame_px",
        "frame_solids",
        "frame_volume_mm3",
        "frames",
        "gap_built_mm",
        "gap_least_mm",
        "gap_mm",
        "glass",
        "glass_added_by_pixels_mm2",
        "glass_added_mm2",
        "glass_left_as_lead_mm2",
        "glass_patches",
        "glass_share_after_pct",
        "glass_share_before_pct",
        "glass_share_by_pixels_pct",
        "glass_share_change_pct",
        "glass_share_on_dome_by_pixels_pct",
        "glass_share_on_dome_pct",
        "glass_share_pct",
        "glass_taken_by_pixels_mm2",
        "glass_taken_mm2",
        "grip_mm",
        "groove_deep_mm",
        "groove_mm",
        "groove_wall_mm",
        "grooves",
        "grooves_read",
        "hairs_pct",
        "hold",
        "hole_mm",
        "join_deg",
        "laid_over",
        "lead_bodies",
        "lead_flat_mm",
        "lead_mm",
        "lead_needed_mm",
        "lead_widest_after_mm",
        "lead_widest_at",
        "lead_widest_before_mm",
        "lead_widest_by_pixels_mm",
        "lead_widest_mm",
        "least_feature_mm",
        "least_lip_before_play_mm",
        "lengths",
        "lengths_count",
        "limits",
        "line_length_mm",
        "line_moved_mm",
        "line_width_mm",
        "lines_across_box_edge",
        "lines_at_default_dark",
        "lines_count",
        "lines_ending_at_box_edge",
        "lines_in_box_after",
        "lines_in_box_before",
        "lip_over_groove_mm",
        "lips",
        "lower_lip_mm",
        "map",
        "maps",
        "meridian_mm",
        "mouth_mm",
        "on_bed_mm2",
        "outlines",
        "over_inner_face_mm",
        "over_outer_face",
        "over_outer_face_mm",
        "overlap_mm3",
        "pane_files",
        "pane_top_z",
        "panel_mm",
        "panel_px",
        "panel_share_pct",
        "picture_px",
        "panes_count",
        "patch_at",
        "patch_left_as_lead_mm2",
        "patch_mm2",
        "patch_scraps_count",
        "patch_scraps_mm2",
        "patch_shares",
        "patch_widest_mm",
        "pause_z",
        "picture",
        "pieces",
        "pieces_after",
        "pieces_at_default_dark",
        "pieces_before",
        "pieces_count",
        "pieces_in_box_after",
        "pieces_in_box_before",
        "placed",
        "placed_slices",
        "play_off_mm",
        "pocket_floor_z",
        "pocket_moved_in_mm",
        "pocket_past_outline_mm",
        "pocket_top_z",
        "pocket_z",
        "press_mm",
        "profile",
        "px_per_mm",
        "ran_from",
        "ran_on_mm",
        "ran_to",
        "refitted",
        "refitted_lips",
        "repeats",
        "rib_mm",
        "rings",
        "rings_count",
        "room_over_pane_mm",
        "rows",
        "rows_choice",
        "rows_count",
        "rows_off_dome_at",
        "rows_off_dome_mm",
        "seam",
        "seam_between",
        "seam_faces_off_deg",
        "seam_gap_mm",
        "seam_refused",
        "seam_wall_mm",
        "slice_mm",
        "slice_would_be_mm",
        "slices",
        "slices_count",
        "slot_mm",
        "source_glass_share_pct",
        "specks",
        "specks_filled",
        "steps",
        "steps_given",
        "steps_made",
        "strips",
        "strips_count",
        "strips_holding",
        "strips_over_openings",
        "thickness_mm",
        "tolerance_mm",
        "top_mm",
        "unbuildable",
        "upper_lip_mm",
        "web_mm",
        "widened_along_mm",
        "widened_from_mm",
        "widened_to_mm",
        "body_mm",
        "face_mm",
        "face_play_mm",
        "face_reach_mm",
        "face_tips",
        "flush",
        "flush_read",
        "pause_after_layer",
        "section",
    ),
    # Joined parts under a weight, and each part against the bed (mqucifer/print-shop#156).
    "load": (
        "coarse_mesh_mm",
        "elements",
        "force_n",
        "held",
        "joints",
        # The part the assembly is lifted by, where that's the load case
        # (mqucifer/print-shop#170).
        "lift",
        "loads",
        # Where the whole couldn't be read, the parts read with their glass band left out,
        # and why (mqucifer/print-shop#295).
        "lower_bound",
        "mesh_mm",
        "mesh_s",
        "most_moves_at",
        "most_moves_mm",
        "moves_mm",
        "nodes",
        "parts",
        "reaction_n",
        "solve_s",
        "toward",
        # Each dovetail taken apart the way it went together, before anything is solved
        # (mqucifer/print-shop#184 and mqucifer/print-shop#185).
        "ways_in",
        "weight_kg",
    ),
    # `pockets`: each nut's pocket of a through joint that states one (mqucifer/print-shop#168).
    # `channels`: each bolt of a through joint that declares a drop-in channel
    # (mqucifer/print-shop#302).
    # Where a part's load goes, a reference for a design round and never a part
    # (mqucifer/print-shop#286): a need can't name it (`needs.REFERENCE`), and these are said.
    "optimise": (
        "across_limit_mpa",
        "after",
        "before",
        "beso_mises_after_mpa",
        "beso_mises_before_mpa",
        "beso_s",
        "elements",
        "elements_dropped",
        "elements_kept",
        "force_n",
        "islands_dropped",
        "iterations",
        "kept_digest",
        "kept_solid",
        "kept_solid_kept",
        "layers",
        "mesh_digest",
        "mesh_mm",
        "mesh_s",
        "nodes",
        "overhang",
        "part",
        "reference_only",
        "stl_volume_mm3",
        "straight",
        "toward",
        "up",
        "volume_kept",
        "volume_kept_mm3",
        "volume_kept_solid",
        "volume_part_mm3",
        "volume_target",
        "weight_kg",
        "yield_mpa",
    ),
    "fit-bed": ("bed_mm", "channels", "parts", "pockets"),
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
#: `clearance`'s gap at one place, for one pair (mqucifer/print-shop#72): the least gap and by which
#: method, the second method's figure, the points it's between, the place's verdict, and why it has
#: no figure where it catches no surface of one of the pair (None then).
PLACE = Row(
    {
        "gap_mm": NUMBER,
        "gap_by": TEXT,
        "gap_second_mm": NUMBER,
        "from": XYZ,
        "to": XYZ,
        "verdict": TEXT,
        "why": TEXT,
    },
    closed=True,
)

#: A piece of a slide's path (mqucifer/print-shop#100).
PATH_PIECE = Row(
    {
        "box": CORNERS,
        "reach_mm": NUMBER,
        "reach_rays_mm": NUMBER,
        "met_mm": NUMBER,
        "met_rays_mm": NUMBER,
    },
    closed=True,
)
#: A piece of a slide's path that reaches further in than the catch (mqucifer/print-shop#100).
PAST_THE_CATCH = Row({"part": TEXT, **PATH_PIECE.fields}, closed=True)
#: A first contact that's one of the bodies of a file of more than one: its file, how many bodies
#: that has, its nearest other body there, the gap to it, and the pair that reads that gap
#: (mqucifer/print-shop#122).
SEPARATE_BODY = Row(
    {
        "part": TEXT,
        "file": TEXT,
        "bodies": NUMBER,
        "nearest": TEXT,
        "gap_mm": NUMBER,
        "pair": NUMBER,
    },
    closed=True,
)

#: `clearance --slide`'s answer (mqucifer/print-shop#76). `first_mm` is None where it meets nothing.
SLIDE = Row(
    {
        "file": TEXT,
        "way": TEXT,
        "step_mm": NUMBER,
        "hold_mm": NUMBER,
        "clear_mm": NUMBER,
        "first_mm": NUMBER,
        "first_rays_mm": NUMBER,
        "meets": Rows(TEXT),
        "contacts": Rows(Row({"part": TEXT, "box": CORNERS, "in": Rows(TEXT)}, closed=True)),
        "overlaps": Rows(Row({"part": TEXT, "overlap_mm3": NUMBER, "box": CORNERS}, closed=True)),
        "path": Rows(
            Row(
                {
                    "part": TEXT,
                    "met_mm": NUMBER,
                    "met_rays_mm": NUMBER,
                    "box": CORNERS,
                    "reach_mm": NUMBER,
                    "reach_rays_mm": NUMBER,
                    # Each piece of it on the path, such as each hook a fan passes, its reach read
                    # again from the rays' points in its box, and how far the part slides before
                    # it meets it, by both methods (mqucifer/print-shop#100).
                    "pieces": Rows(PATH_PIECE),
                },
                closed=True,
            )
        ),
        # The moving part's length along the slide, how far the first contacts' pieces reach
        # into the path, each piece that isn't one of them and reaches further, met before the
        # part has gone its length past the catch: the way in, blocked; and each met after it,
        # with room to set the part in before it (mqucifer/print-shop#100).
        "length_mm": NUMBER,
        "catch_mm": NUMBER,
        "blocked": Rows(PAST_THE_CATCH),
        "room": Rows(PAST_THE_CATCH),
        # Each first contact that's a body joined to nothing else in its file: the hold is then
        # `HELD BY A SEPARATE BODY` (mqucifer/print-shop#122).
        "separate": Rows(SEPARATE_BODY),
        # The parts whose first contact closes round the moving part, seen along the slide, and
        # whether the methods' first contacts were read as one tie (mqucifer/print-shop#135).
        "round": Rows(TEXT),
        # How much of the way round each runs: 1 where it closes, or the share of one of the
        # moving part's outlines its stretches lie along (mqucifer/print-shop#143).
        "round_along": Keyed(NUMBER),
        "tie": FLAG,
        "verdict": TEXT,
    },
    closed=True,
)

#: A section's two sizes: along x then y on a level cut, y then z or x then z upright.
ACROSS = Vector(2, NUMBER, ("the plane's first axis", "its second"))
#: `slice`'s place: the section inside a `--box NAME=...`, at one level (mqucifer/print-shop#101).
SLICE_PLACE = Row(
    {
        "box": CORNERS,
        "pieces": NUMBER,
        "whole_pieces": NUMBER,
        "least_width_mm": NUMBER,
        "least_width_rays_mm": NUMBER,
        "size_mm": ACROSS,
    },
    closed=True,
)
#: `slice`'s answer at each `--at` (mqucifer/print-shop#77): each piece, largest first, with its
#: least width (mqucifer/print-shop#101), and how many pieces and holes there are.
#: A piece of a part solved by itself, as `load` reads a dovetail's neck, its socket's walls
#: and its latch's tab (mqucifer/print-shop#170).
_STRAINED = Row(
    {
        "across_at": XYZ,
        "across_layers_pct": NUMBER,
        "elements": NUMBER,
        "mesh_mm": NUMBER,
        "stretch_at": XYZ,
        "stretch_is": TEXT,
        "stretch_pct": NUMBER,
    }
)
SLICE = Rows(
    Row(
        {
            "at": TEXT,
            "pieces": Rows(
                Row(
                    {
                        "area_mm2": NUMBER,
                        "box": CORNERS,
                        "size_mm": ACROSS,
                        "least_width_mm": NUMBER,
                        "least_width_rays_mm": NUMBER,
                        "outline": TEXT,
                        "holes": Rows(Row({"centre": XYZ, "size_mm": ACROSS, "area_mm2": NUMBER})),
                    }
                )
            ),
            "pieces_count": NUMBER,
            "area_mm2": NUMBER,
            "holes": NUMBER,
            # By the names the command's own `--box NAME=...` give.
            "places": Keyed(SLICE_PLACE),
        }
    )
)

#: Each file `--placed` placed, by its name: the part entry it's from, its turns and its move
#: (mqucifer/print-shop#201).
PLACED = Keyed(
    Row({"part": TEXT, "assembly": TEXT, "turn": Rows(Rows(ANY)), "at": XYZ}, closed=True)
)

LOW_HIGH = Row({"min": NUMBER, "max": NUMBER}, closed=True)
#: A peaked bore's point (mqucifer/print-shop#237): which way, how far from the axis, and how
#: steeply its flat faces rise to it.
PEAK = Row({"toward": XYZ, "rises_deg": NUMBER, "point_mm": NUMBER}, closed=True)
#: A drop-in channel (mqucifer/print-shop#302): which way it's open, how far round its round goes,
#: and how wide it's open where the round leaves off.
CHANNEL = Row({"toward": XYZ, "round_deg": NUMBER, "open_mm": NUMBER}, closed=True)
#: One end of a through-bolt: what bears on its seat, and on its hole's wall.
THROUGH_END = Row(
    {
        "bears_mm2": NUMBER,
        # A drop-in channel's, and how hard the shank is pushed toward its open side, which
        # carries nothing (mqucifer/print-shop#302).
        "channel": CHANNEL,
        "toward_open_n": NUMBER,
        "hole_length_mm": NUMBER,
        "hole_mm": NUMBER,
        "part": TEXT,
        "peak": PEAK,
        "pressure_limit_mpa": NUMBER,
        "pressure_mpa": NUMBER,
        "ring_mm2": NUMBER,
        "seat_mm2": NUMBER,
        # The wall that took the shear, beside the stretch its pressure is read on
        # (mqucifer/print-shop#245).
        "sheared_mm2": NUMBER,
        "under": TEXT,
        "wall_bears_mm2": NUMBER,
        "wall_pressure_mpa": NUMBER,
    }
)

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
        # Flat faces in runs of strips, and each run's length (mqucifer/print-shop#85).
        "strip_faces": NUMBER,
        "strip_runs": Rows(NUMBER),
        "triangles": NUMBER,
        "valid": FLAG,
        "volume_mesh_mm3": NUMBER,
        "watertight": FLAG,
    },
    "clearance": {
        "placed": PLACED,
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
                    # Why a pair's figures can't be relied on (mqucifer/print-shop#244).
                    "untrusted": Rows(TEXT),
                    # By the names the need's own `--box NAME=...` and `--facing NAME=...` give.
                    "places": Keyed(PLACE),
                    "verdict": TEXT,
                }
            )
        ),
        # What each place named is: a box's two corners, or the way its faces face.
        "places": Keyed(Row({"box": CORNERS, "facing": TEXT})),
        # What holds the last file named, slid along its way out (mqucifer/print-shop#76): the
        # first distance it meets another, by the step and halving and by the rays, which parts
        # and where, an overlap where it lies, and every part on its path.
        "slide": SLIDE,
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
                    # A peaked bore's (mqucifer/print-shop#237): its diameter by every ray and
                    # by the section's outline, and which way its point is.
                    "bore_d_mm": LOW_HIGH,
                    "peak": PEAK,
                    "section_bore_d_mm": LOW_HIGH,
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
        "rim_points": NUMBER,
        "rim_points_at": Rows(
            Row(
                {
                    "box": CORNERS,
                    "mm2": NUMBER,
                    "rim": XYZ,
                    "rim_across_mm": NUMBER,
                    "wall_mm": NUMBER,
                    "width_mm": NUMBER,
                }
            )
        ),
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
        # Where the hole opens into a hollow, the stretch that reads it alone, and the stretch
        # `--z` asked for (mqucifer/print-shop#136).
        "hole_alone": Vector(2, NUMBER, ("from", "to")),
        "hole_opens_at": Rows(NUMBER),
        "hole_read": Vector(2, NUMBER, ("from", "to")),
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
    "render": {"placed": PLACED, "seen_px": Keyed(Keyed(NUMBER)), "views": Keyed(Row())},
    "slice": {"cuts": SLICE},
    # The second image's (mqucifer/print-shop#142). `fit-on`'s places are deepest first, and
    # `bend`'s pushes are by the edge pushed, `far` or `near`.
    "fit-on": {
        "along": TEXT,
        "depth_mm": NUMBER,
        "from_clear": FLAG,
        "from_mm": NUMBER,
        "moving": TEXT,
        "onto": Rows(TEXT),
        "placed": PLACED,
        "places": Rows(
            Row(
                {
                    "across": XYZ,
                    "along_mm": NUMBER,
                    "box": CORNERS,
                    "depth_mm": NUMBER,
                    "file": TEXT,
                    "first_mm": NUMBER,
                    "height_mm": NUMBER,
                    "last_mm": NUMBER,
                    "out_mm": NUMBER,
                    "readings": NUMBER,
                    "seated": FLAG,
                    "volume_mm3": NUMBER,
                }
            )
        ),
        "readings": NUMBER,
        "step_mm": NUMBER,
        "steps": Rows(
            Row(
                {"depth_mm": NUMBER, "out_mm": NUMBER, "overlap_mm3": NUMBER, "pieces": NUMBER},
                closed=True,
            )
        ),
    },
    "bend": {
        "across_layers_pct": NUMBER,
        "arm_mm3": NUMBER,
        "back_moves_mm": NUMBER,
        "layers": TEXT,
        "limit_from": TEXT,
        "limit_pct": NUMBER,
        "mesh_mm": NUMBER,
        "pushes": Keyed(
            Row(
                {
                    "back_moves_mm": NUMBER,
                    "beam_half_way_pct": NUMBER,
                    "beam_peak_pct": NUMBER,
                    "beam_root_pct": NUMBER,
                    "half_way_pct": NUMBER,
                    "length_mm": NUMBER,
                    "peak_at": XYZ,
                    "peak_pct": NUMBER,
                    "root_pct": NUMBER,
                    "strip_pct": NUMBER,
                }
            )
        ),
        "thickness_mm": NUMBER,
        "worst": TEXT,
        # What keeps the strain at the limit, from the reading's own peak
        # (mqucifer/print-shop#150).
        "would_pass": Row(
            {"length_mm": NUMBER, "push_mm": NUMBER, "thickness_mm": NUMBER}, closed=True
        ),
    },
    # A need names a part or a joint by its name in the assembly file, `parts.top.stretch_pct`
    # or `joints.top+leg1.bolt_pull_n` (mqucifer/print-shop#156).
    "load": {
        "coarse_mesh_mm": NUMBER,
        "elements": NUMBER,
        "force_n": NUMBER,
        "joints": Keyed(
            Row(
                {
                    "area_mm2": NUMBER,
                    "at": XYZ,
                    "bending_nmm": NUMBER,
                    "force_n": XYZ,
                    "kind": TEXT,
                    "moment_nmm": XYZ,
                    "pull_n": NUMBER,
                    "shear_n": NUMBER,
                    "twist_nmm": NUMBER,
                    # A through joint's (mqucifer/print-shop#168): which of its bolt, its
                    # washers, its nut and its plastic is nearest its limit, and by what share.
                    "bolts": Rows(Row({"ends": Rows(THROUGH_END)})),
                    "gives_first": TEXT,
                    "nearest_share": NUMBER,
                    # A dovetail's (mqucifer/print-shop#170): the way its tail slides in, how
                    # hard the load drives it that way, each face's push, what its faces leave
                    # held by nothing, its latch, and the tail's neck and the socket's walls.
                    "glue_unread": FLAG,
                    "slides": TEXT,
                    "drive_n": NUMBER,
                    "opens": FLAG,
                    "latched": FLAG,
                    "left_n": XYZ,
                    "left_nmm": XYZ,
                    "faces": Rows(
                        Row(
                            {
                                "area_mm2": NUMBER,
                                "at": XYZ,
                                "bears_mm2": NUMBER,
                                "kind": TEXT,
                                "normal": XYZ,
                                "pressed": TEXT,
                                "pressed_part": TEXT,
                                "pressure_limit_mpa": NUMBER,
                                "pressure_mpa": NUMBER,
                                "push_n": NUMBER,
                                # Its play as drawn, and how much of it the load closes
                                # (mqucifer/print-shop#188).
                                "play_mm": NUMBER,
                                "closed_mm": NUMBER,
                            },
                            closed=True,
                        )
                    ),
                    # How the tail shifts and turns as its play closes, and the most any of
                    # its faces moves (mqucifer/print-shop#188).
                    "shift_mm": XYZ,
                    "turn_deg": XYZ,
                    "faces_move_mm": NUMBER,
                    "latch": Row(
                        {
                            "catch_at": XYZ,
                            "catch_mm2": NUMBER,
                            "holds_n": NUMBER,
                            "on": TEXT,
                            "play_mm": NUMBER,
                            "pressure_limit_mpa": NUMBER,
                            "pressure_mpa": NUMBER,
                            "tab": _STRAINED,
                        }
                    ),
                    "neck": _STRAINED,
                    "walls": _STRAINED,
                }
            )
        ),
        "lift": TEXT,
        "loads": Rows(
            Row(
                {
                    "at": XYZ,
                    "grams": NUMBER,
                    "moved": XYZ,
                    "moves_mm": NUMBER,
                    "part": TEXT,
                    "patch_mm2": NUMBER,
                    "volume_mm3": NUMBER,
                }
            )
        ),
        "mesh_mm": NUMBER,
        "most_moves_at": XYZ,
        "most_moves_mm": NUMBER,
        "moves_mm": NUMBER,
        "nodes": NUMBER,
        "parts": Keyed(
            Row(
                {
                    "across_at": XYZ,
                    "across_layers_pct": NUMBER,
                    "across_limit_pct": NUMBER,
                    "layers": XYZ,
                    "material": TEXT,
                    "stretch_at": XYZ,
                    "stretch_from_layers_deg": NUMBER,
                    "stretch_limit_pct": NUMBER,
                    "stretch_pct": NUMBER,
                }
            )
        ),
        "reaction_n": XYZ,
        "toward": TEXT,
        "ways_in": Keyed(
            Row(
                {
                    "blocked": FLAG,
                    "comes_out": Rows(TEXT),
                    "goes_in": Rows(TEXT),
                    "latch": Row({"depth_mm": NUMBER, "passes": FLAG}),
                    "latched": FLAG,
                    "out": TEXT,
                    "slides": TEXT,
                }
            )
        ),
        "weight_kg": NUMBER,
    },
    "fit-bed": {
        "bed_mm": XYZ,
        # Each bolt of a through joint in a drop-in channel (mqucifer/print-shop#302), and each
        # part's stretch of it: its round, its wall's seat, its way in and which way it faces.
        "channels": Keyed(
            Rows(
                Row(
                    {
                        "at": XYZ,
                        "bolt": TEXT,
                        "drops_in": FLAG,
                        "ends": Rows(
                            Row(
                                {
                                    "bears_by_mesh_mm2": NUMBER,
                                    "bears_mm2": NUMBER,
                                    "faces_up": FLAG,
                                    "length_mm": NUMBER,
                                    "open_mm": NUMBER,
                                    "over_by_mesh_mm2": NUMBER,
                                    "over_mm3": NUMBER,
                                    "part": TEXT,
                                    "plane": TEXT,
                                    "play_mm": NUMBER,
                                    "ring_mm2": NUMBER,
                                    "round_deg": NUMBER,
                                    "round_mm": NUMBER,
                                    "seat_mm2": NUMBER,
                                    "seated": FLAG,
                                    "side": TEXT,
                                    "toward": XYZ,
                                    "wall": TEXT,
                                }
                            )
                        ),
                        "fits": FLAG,
                        "shank_mm": NUMBER,
                        "washer": TEXT,
                    },
                    closed=True,
                )
            )
        ),
        "parts": Keyed(Row({"box_mm": XYZ, "file": TEXT, "fits": FLAG})),
        "pockets": Keyed(
            Rows(
                Row(
                    {
                        "across_flats_mm": NUMBER,
                        "at": XYZ,
                        "depth_mm": NUMBER,
                        "fits": FLAG,
                        # Whether the nut's corners go between its walls in some turn
                        # (mqucifer/print-shop#183), at which turn, and how far off the axis.
                        "goes_in": FLAG,
                        # Each part's bore on the bolt, peaked or round (mqucifer/print-shop#237).
                        "bores": Rows(Row({"hole_mm": NUMBER, "part": TEXT, "peak": PEAK})),
                        "nut": TEXT,
                        "nut_flats_mm": NUMBER,
                        "off_axis_mm": NUMBER,
                        "part": TEXT,
                        "play_mm": NUMBER,
                        "stated_play_mm": NUMBER,
                        "turn_deg": NUMBER,
                    }
                )
            )
        ),
    },
}


VERDICTS: dict[str, tuple[str, ...]] = {
    "info": ("OK", "FAIL"),
    "clearance": (
        "CLEAR",
        "TOUCHING",
        "TOO CLOSE",
        "TOO LOOSE",
        "INTERFERE",
        "HELD",
        "NOT HELD",
        "HELD BY A SEPARATE BODY",
    ),
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
    # `PARTIAL` is `pieces fix`'s, stopped at a step that was refused (mqucifer/print-shop#161).
    "pieces": ("OK", "FAIL", "PARTIAL", "INTERFERE"),
    # Readings, with no limit: what's there, or DISAGREE where the two methods differ.
    "faces": ("OK",),
    "slice": ("OK",),
    # The second image's (mqucifer/print-shop#142). A snap fit is meant to be `BLOCKED`: a need
    # on one names the verdict, and holds its `depth_mm`.
    "fit-on": ("CLEAR", "BLOCKED"),
    "bend": ("OK", "FAIL"),
    "load": ("OK", "FAIL"),
    "optimise": ("OK", "FAIL"),
    "fit-bed": ("OK", "FAIL"),
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
    "`pairs.0.gap_mm` and the figure as `at_least` and `at_most`. Where the pane's faces have "
    "another play, read its edge alone with `--box edge=X0,Y0,Z0,X1,Y1,Z1` round the edge between "
    "its faces, and `number` `pairs.0.places.edge.gap_mm` (mqucifer/print-shop#72)"
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
    only for the `--box NAME=...` and `--facing NAME=...` it's given (mqucifer/print-shop#65). And a
    `clearance` need's `pairs.N.places.NAME` the same way (mqucifer/print-shop#72)."""
    if not words:
        return None
    parts = number.split(".")
    if words[0] == "clearance" and len(parts) >= 3 and parts[0] == "pairs" and parts[2] == "places":
        return _place_unnamed(words, number, parts[3] if len(parts) > 3 else "")
    if words[0] == "slice" and len(parts) >= 3 and parts[0] == "cuts" and parts[2] == "places":
        return _slice_place_unnamed(words, number, parts[3] if len(parts) > 3 else "")
    if words[0] != "edges" or not number.startswith("selected."):
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


def _given_places(words: list[str]) -> list[str]:
    """The names a command's own `--box NAME=...` and `--facing NAME=...` give."""
    given: list[str] = []
    for k, word in enumerate(words):
        value = None
        if word in ("--box", "--facing") and k + 1 < len(words):
            value = words[k + 1]
        elif word.startswith(("--box=", "--facing=")):
            value = word.split("=", 1)[1]
        if value is not None:
            given.append(value.split("=", 1)[0])
    return given


def _place_unnamed(words: list[str], number: str, name: str) -> str | None:
    given = _given_places(words)
    if name and name in given:
        return None
    named = ", ".join(f"`{g}`" for g in given) or "none"
    return (
        f"`{number}` isn't in `clearance`'s answer: a place is read only for the command's own "
        f"`--box NAME=X0,Y0,Z0,X1,Y1,Z1` or `--facing NAME=DIR`, and it names {named}. Add "
        f"`--box {name or 'NAME'}=...` round the faces whose gap the need holds, and name "
        f"`pairs.0.places.{name or 'NAME'}.gap_mm`"
    )


def _slice_place_unnamed(words: list[str], number: str, name: str) -> str | None:
    given = _given_places(words)
    if name and name in given:
        return None
    named = ", ".join(f"`{g}`" for g in given) or "none"
    return (
        f"`{number}` isn't in `slice`'s answer: a place is read only for the command's own "
        f"`--box NAME=X0,Y0,Z0,X1,Y1,Z1`, and it names {named}. Add `--box {name or 'NAME'}=...` "
        f"round the wall or tab the need reads, and name `cuts.0.places.{name or 'NAME'}.pieces` "
        "or its `least_width_mm`"
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
