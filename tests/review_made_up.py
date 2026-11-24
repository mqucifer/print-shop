"""A made-up review the size of 2026-11-21's (mqucifer/print-shop#223): about ten changes, nine
lessons, six poses, a pause, a context with two fits, a slice reading with flags. Every part,
profile and lesson here is invented."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from print_shop.core.state import AppliedOrientation, Orientation, SliceResult
from print_shop.setup.offer import Item, Offering, Past
from print_shop.setup.pauses import Pause, PauseFor
from print_shop.setup.proposal import (
    Change,
    Ending,
    GivenLesson,
    Proposal,
    Source,
    Suggested,
    Used,
)
from print_shop.setup.reading import (
    FeatureReading,
    Flag,
    LayerTime,
    PauseReading,
    SeamReading,
    SliceReading,
    _layer_time,
)
from print_shop.setup.review import ReviewRequest
from tests.fakes import STORE_PROFILES

FIT_POCKETS = ("a 0.2 mm fit with the 24 glass tiles, 2.0 mm thick (`each tile drops into its "
               "pocket at the pause`), at z 1.2 to 3.2 mm as the file lies, as the sidecar "
               "gives it")
FIT_RAIL = ("a -0.05 mm fit with the lid's rail: a rib 1.2 mm wide in a groove 1.1 mm wide and "
            "0.6 deep (`the lid's rib presses into the groove along the long edge`), at z 3.6 "
            "to 4.2 mm as the file lies, as the sidecar gives it")


def _lesson(n: int, key: str, value: str, text: str, traits: tuple[str, ...],
            applied: bool = True, material: str = "PETG", why: str = "") -> GivenLesson:
    return GivenLesson(id=n, text=text, key=key, value=value, job_id=f"job-{n:04d}",
                       stl_name="sample-tray-a.stl" if n < 30 else "sample-lid-b.stl",
                       traits=traits, reviewed_by="operator test-op", applied=applied,
                       material=material if key else "", why=why)


LESSONS = (
    _lesson(11, "textured_plate_temp", "75", "corners of a wide tray lifted at 70; at 75 they "
            "held, with the first layer a touch wide.", ("large_bed_contact",)),
    _lesson(12, "textured_plate_temp_initial_layer", "75", "a wide PETG footprint keeps 75 on "
            "its first layer too: at 70 then 65 the corners of the 5,400 mm2 base lifted.",
            ("large_bed_contact",)),
    _lesson(13, "during_print_exhaust_fan_speed", "15", "the exhaust at the profile's 70 "
            "cooled the chamber and the corners lifted; at 15 the tray stayed down.",
            ("large_bed_contact",)),
    _lesson(14, "additional_cooling_fan_speed", "25", "the side fan at 70 lifted a corner of "
            "the tray; 25 held it.", ("large_bed_contact",)),
    _lesson(15, "bridge_flow", "0.9", "less sag over the 8 mm slot in the lid.", ("bridges",),
            material=""),
    _lesson(16, "bridge_speed", "35", "less sag over the 8 mm slot in the lid.", ("bridges",),
            material=""),
    _lesson(17, "elefant_foot_compensation", "0.15", "a slight foot on the tray at bed 75 with "
            "0.1.", ("large_bed_contact",)),
    _lesson(31, "", "", "Wipe the plate with dish soap before a wide PETG footprint: a tray's "
            "first layer tore at one corner on a greasy plate, and laid down clean once it was "
            "washed.", ("large_bed_contact", "thin_walls"), applied=False,
            why="read by the model"),
    _lesson(32, "", "", "An older textured plate of the same make held nothing but the start "
            "strip and grew a blob on the nozzle; the newer plate of the same type printed "
            "the same file clean. Use the newer plate for wide PETG.", ("large_bed_contact",),
            applied=False, why="read by the model"),
)


def _from(g: GivenLesson, preset: str, standard: str = "") -> Change:
    return Change(key=g.key, preset=preset, standard=standard, value=g.value,
                  reason=f"learned on {g.stl_name}: {g.text}", source=Source.LESSON,
                  feature=", ".join(g.traits), lesson=g.id)


CHANGES = (
    Change(key="sparse_infill_density", standard="15%", preset="15%", value="25%",
           source=Source.ANSWER, feature="purpose",
           reason="you chose 'A tray that's handled': the floor carries the tiles and is "
                  "lifted by its rim, so a little more fill than a display piece."),
    _from(LESSONS[0], "70"),
    _from(LESSONS[1], "70"),
    _from(LESSONS[2], "70"),
    _from(LESSONS[3], "-"),
    _from(LESSONS[4], "1", "1"),
    _from(LESSONS[5], "50", "50"),
    _from(LESSONS[6], "0.1", "0.1"),
    Change(key="ironing_type", standard="no ironing", preset="top", value="no ironing",
           source=Source.MODEL, feature="flat_top", knowledge="surface",
           reason="Only the floor's underside, on the plate, is seen; the top holds the tile "
                  "pockets, which ironing would smooth for nothing but time."),
    Change(key="support_type", standard="normal(auto)", preset="normal(auto)",
           value="tree(auto)", source=Source.GUARD, feature="overhang", knowledge="supports",
           reason="tree supports, as you use them"),
    Change(key="enable_support", standard="0", preset="0", value="1", source=Source.GUARD,
           feature="overhang", knowledge="supports",
           reason="the 1,204 mm2 overhang would print into the air, and nothing decided "
                  "otherwise"),
    Change(key="pause_at", preset="", value="3.2", source=Source.SPONSOR,
           reason="drop the tiles in", pause=Pause(kind=PauseFor.INSERT)),
)

PROPOSAL = Proposal(
    reasoning=(
        "A flat tray for 24 glass tiles (4.2 mm tall, 120 x 160 mm), handled by its rim. The "
        "tiles drop into their pockets at a pause at 3.2 mm and the lid's rail presses into "
        "the groove along the long edge. The underside is the face seen; the top carries the "
        "pockets. The 5,400 mm2 footprint in PETG wants the plate warm and the chamber still: "
        "the lessons set the plate at 75, the exhaust at 15 and the side fan at 25. The slot "
        "in the floor bridges 8 mm, which the lessons slow and thin. Ironing goes, as the top "
        "isn't seen."
    ),
    changes=CHANGES,
    not_applied=(Change(key="top_surface_pattern", preset="monotonicline", value="concentric",
                        source=Source.MODEL, feature="flat_top", knowledge="surface",
                        reason="concentric tops follow the rim",
                        not_applied="asked concentric, sliced monotonicline"),),
    kept=(Change(key="wall_loops", standard="2", preset="3", value="3", source=Source.MODEL,
                 reason="three walls already hold the rim", feature="thin_walls",
                 knowledge="walls"),
          Change(key="brim_type", preset="no_brim", value="no_brim", source=Source.ANSWER,
                 reason="you said the corners are trimmed by hand", feature="purpose")),
    design_notes=(
        "1,204 mm2 overhangs, up to 90 degrees past vertical: a chamfer to 45 degrees or a "
        "support designed into the part would print without Orca's supports. That's a design "
        "change, for the CAD side.",
        "The slot in the floor could be a row of 45-degree teeth instead of a flat bridge. "
        "That's a design change, not a setting.",
    ),
    notes=("Seen as it prints: the bottom, on the plate, as the notes say",
           "warping risk: 5,400 mm2 on the plate. The plate's temperature (textured_plate_temp), "
           "a brim (brim_type), the chamber's exhaust (during_print_exhaust_fan_speed) and the "
           "side fan (additional_cooling_fan_speed) answer it, not turning the part",),
    refused=("sparse_infill_density=20%: you answered it at the intake",),
    model="crew-code-think",
    lessons=LESSONS,
)

POSES = (
    Orientation(rotation_deg=(0.0, 0.0, 0.0), overhang_area_mm2=1204.3,
                bed_contact_area_mm2=5401.7, height_mm=4.2, bbox_mm=(262.0, 160.0, 4.2)),
    Orientation(rotation_deg=(180.0, 0.0, 0.0), overhang_area_mm2=2310.8,
                bed_contact_area_mm2=4120.2, height_mm=4.2),
    Orientation(rotation_deg=(90.0, 0.0, 0.0), overhang_area_mm2=1402.1,
                bed_contact_area_mm2=4.1, height_mm=160.0),
    Orientation(rotation_deg=(270.0, 0.0, 0.0), overhang_area_mm2=1311.6,
                bed_contact_area_mm2=3.9, height_mm=160.0),
    Orientation(rotation_deg=(0.0, 90.0, 0.0), overhang_area_mm2=2077.4,
                bed_contact_area_mm2=0.0, height_mm=120.0),
    Orientation(rotation_deg=(0.0, 270.0, 0.0), overhang_area_mm2=2140.9, flare_area_mm2=64.2,
                bed_contact_area_mm2=71.3, height_mm=120.0),
)

LAYERS = tuple(LayerTime(n=n, z=round(0.2 * n, 2), time_s=t) for n, t in enumerate(
    (774.0, 221.0, 236.0, 240.0, 251.0, 229.0, 233.0, 219.0, 221.0, 190.0, 180.0, 175.0, 170.0,
     168.0, 160.0, 63.0, 310.0, 298.0, 145.0, 140.0, 130.0), 1))

READING = SliceReading(
    layers=LAYERS,
    features=(
        FeatureReading(kind="Support", time_s=531.0, area_mm2=7810, layers=8, from_z=0.2,
                       to_z=1.6),
        FeatureReading(kind="Outer wall", time_s=452.0, area_mm2=20411, layers=21, from_z=0.2,
                       to_z=4.2),
        FeatureReading(kind="Top surface", time_s=431.0, area_mm2=7102, layers=3, from_z=1.2,
                       to_z=4.2),
        FeatureReading(kind="Ironing", time_s=739.0, area_mm2=6890, layers=2, from_z=1.2,
                       to_z=4.2),
        FeatureReading(kind="Internal solid infill", time_s=268.0, area_mm2=12480, layers=16,
                       from_z=0.4, to_z=4.0),
        FeatureReading(kind="Bridge", time_s=264.0, area_mm2=2402, layers=1, from_z=3.4,
                       to_z=3.4),
        FeatureReading(kind="Inner wall", time_s=234.0, area_mm2=10011, layers=20, from_z=0.2,
                       to_z=4.0),
        FeatureReading(kind="Bottom surface", time_s=206.0, area_mm2=1760, layers=1, from_z=0.2,
                       to_z=0.2),
        FeatureReading(kind="Gap infill", time_s=95.0, area_mm2=4712, layers=21, from_z=0.2,
                       to_z=4.2),
        FeatureReading(kind="travel", time_s=301.0, area_mm2=0, layers=21, from_z=0.2,
                       to_z=4.2),
    ),
    seam=SeamReading(seams=702, stacked=0.71, at=(188.0, 96.0), at_layers=14),
    settings={"seam_position": "aligned", "ironing_type": "top", "enable_support": "1"},
    pauses=(PauseReading(before_layer=17, z=3.4, after_s=1763.0, after_z=3.2,
                         kind=PauseFor.INSERT),),
)

FLAGS = (
    # As main words it (`reading._layer_time`), for a reading whose floor is as low as it goes.
    *_layer_time(READING.model_copy(update={"settings": {
        **READING.settings, "slow_down_layer_time": "70", "slow_down_min_speed": "1"}}), {}),
    Flag(id="ironing_hidden", reading="Ironing takes 12 min 19 s on 2 layers, z 1.2 to 4.2 mm, "
         "on the top, which isn't seen",
         change=Change(key="ironing_type", preset="top", value="no ironing", source=Source.READING,
                       reason="the top isn't seen, so ironing it is time for nothing",
                       knowledge="ironing_hidden")),
    Flag(id="fit_elephant_foot", reading=f"z 1.2 to 3.2 mm as it prints: {FIT_POCKETS}; "
         "elefant_foot_compensation is 0.15, from lesson 17",
         change=Change(key="elefant_foot_compensation", preset="0.15", value="0.2",
                       source=Source.READING, knowledge="fit_elephant_foot",
                       reason="a fit starts 1.2 mm up, so a wider first layer narrows no pocket; "
                              "0.2 keeps the base's edge true")),
)

CONTEXT = (
    "from the sidecar sample-tray.part.json",
    "read /tmp/made-up/parts/sample-tray/a-rather-long-folder-name-for-a-made-up-part/"
    "sample-tray.part.json",
    f"fit: {FIT_POCKETS}",
    f"fit: {FIT_RAIL}",
    "seen: bottom, as the part's context says",
    "laid on its -z side (the sidecar)",
    "note: A made-up tray of 24 tiles, laid on its flat underside; pause after layer 16, at z "
    "3.2, to drop the tiles in, then the lip prints over them.",
    f"fit at z 1.2 to 3.2 mm as it prints: {FIT_POCKETS}",
    f"fit at z 3.6 to 4.2 mm as it prints: {FIT_RAIL}",
)


def big_request() -> ReviewRequest:
    return ReviewRequest(
        presets=("Made-up Printer 0.4 nozzle - Copy", "0.20mm Standard @Made-up 0.4 - Copy",
                 "Made-up PETG HF @System"),
        proposal=PROPOSAL,
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=21, max_z_mm=4.2,
                          filament_mm=5100, filament_cm3=12.41, filament_g=15.88,
                          print_time_s=4526, turn_deg=90.0),
        orientation=AppliedOrientation(rotation_deg=(0.0, 0.0, 0.0),
                                       rationale="kept as laid, by you (--as-laid): no "
                                                 "orientation model was asked",
                                       height_mm=4.2, bed_contact_area_mm2=5401.7),
        profiles=STORE_PROFILES,
        process={"wall_loops": "3", "ironing_type": "top"},
        filament={"textured_plate_temp": ["70"]},
        reading=READING,
        flags=FLAGS,
        candidates=POSES,
        laid_traits=("large_bed_contact", "thin_walls"),
        context=CONTEXT,
    )


def big_offering() -> Offering:
    def used(by: Source, lesson: int | None = None) -> Used:
        return Used(job_id="0f3c2a1b-made-up", stl_name="sample-tray-old.stl", by=by,
                    lesson=lesson)

    items = (
        Item(key="pause_at", value="3.2", preset="", reason="drop the tiles in",
             fits="the pockets are at the same height", used=used(Source.SPONSOR)),
        Item(key="enable_support", value="0", preset="0",
             reason="the pockets are filled at the pause", fits="", used=used(Source.SPONSOR)),
        Item(key="bridge_flow", value="1", preset="1",
             reason="the lip over the tiles prints over 0.2 mm of air", fits="",
             used=used(Source.SPONSOR), doubts=("this tray's slot is 2 mm wider",)),
        Item(key="textured_plate_temp", value="75", preset="70", reason="", fits="",
             used=used(Source.LESSON, 11)),
    )
    past = Past(job_id="0f3c2a1b-made-up", stl_name="sample-tray-old.stl", on="21 Nov",
                came_out="success", outcome="success",
                why="same shape, height and pockets; came out well",
                measures=(("height", 4.0, 4.2, ""),), differs=("12% less on the plate",),
                items=items)
    other = Past(job_id="9a8b7c6d-made-up", stl_name="sample-coaster.stl", on="18 Nov",
                 came_out="success, with stringing", outcome="success, with stringing",
                 why="flat and wide", measures=(), differs=(), items=items[:2])
    return Offering(options=(past, other))


#: The model's proposals, as #260 lists them, once they've come.
SUGGESTED = Suggested(pose=(0.0, 0.0, 0.0), ending=Ending.CAME, seconds=412.0, proposed=(
    Change(key="outer_wall_speed", preset="200", value="120", source=Source.MODEL,
           reason="the rim is what's handled and seen from the side", feature="thin_walls",
           knowledge="walls"),
    Change(key="bridge_flow", preset="1", value="0.85", source=Source.MODEL,
           reason="the slot bridges 8 mm", feature="bridges", knowledge="bridges"),
    Change(key="top_shell_layers", preset="4", value="5", source=Source.MODEL,
           reason="the pocket floors are thin", feature="flat_top", knowledge="surface"),
), refused=("sparse_infill_density=20%: you answered it at the intake",),
    reasoning=PROPOSAL.reasoning)


def made_up() -> ReviewRequest:
    """The whole review: the offer and the model's proposals with the rest."""
    return replace(big_request(), offering=big_offering(), suggested=SUGGESTED)
