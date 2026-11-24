"""A made-up job's questions (mqucifer/print-shop#266): the notes read back, print-shop's two, a
question of the model's with its options' settings and Orca's estimates, one added after the
first answers, and the model's closer choices on what the part is for. Every part, note and
answer here is invented."""

from __future__ import annotations

from print_shop.setup.intake import Added, Estimate, Option, Question
from print_shop.setup.questions import PURPOSE, SEEN

#: What it's for, with the part's own free words beside it.
FOR = PURPOSE.model_copy(update={
    "beside": "A made-up tray that holds 24 glass tiles on a shelf; lifted by its rim now and "
              "then to dust under it",
})

#: What's seen, read back from the notes, in a pose the notes lay it in.
SEEN_NOTED = SEEN.model_copy(update={
    "beside": "the bottom, as the file lays it", "noted": 3, "noted_as": "bottom",
    "noted_from": "sample-tray.part.json", "pose": (180.0, 0.0, 0.0),
    "posed": "laid top-down, as its notes say",
})

#: The model's own question, of a feature, with what each option sets and costs.
BRIDGE = Question(
    feature="bridge_ceiling",
    text=("The 1,204 mm² ceiling over the tile pockets is a flat downward face at 90°, printed "
          "at a 0.9 mm section. The span is unknown but the area is large. At this thickness the "
          "choice between bridging and supporting matters for both the print and the surface."),
    options=(
        Option(label="Bridge it", settings={"enable_support": "0"},
               impact="the ceiling stays clean, but at the thinnest section the bridge is the "
                      "weak point: a sag or a gap at the far edge, worse the longer the span",
               estimate=Estimate(print_time_s=4526, filament_g=15.88, filament_cm3=12.41)),
        Option(label="Support it", settings={"enable_support": "1", "support_type": "tree(auto)"},
               impact="the ceiling comes out flat whatever the span, but the marks on a large "
                      "surface need cleaning up",
               estimate=Estimate(print_time_s=5940, filament_g=19.2, filament_cm3=15.1),
               recommended=True,
               why="at a 0.9 mm section the bridge is too thin to trust at an unknown span, and "
                   "the guard turns supports on anyway if nobody decides"),
    ),
)

#: One the model added after the first answers, numbered on from them.
WALLS = Question(
    feature="thin_walls",
    text="The rim's walls are 1.2 mm: how many wall loops should they get?",
    options=(
        Option(label="2 (preset)", settings={"wall_loops": "2"},
               impact="the rim is two lines thick, with a thin gap fill between them",
               estimate=Estimate(print_time_s=4526, filament_g=15.88, filament_cm3=12.41),
               recommended=True, why="two lines fill 1.2 mm with a little gap fill, as the "
                                     "preset prints it"),
        Option(label="3", settings={"wall_loops": "3"},
               impact="the rim is all wall, with no gap fill touching its face",
               estimate=Estimate(print_time_s=4610, filament_g=16.02, filament_cm3=12.52)),
    ),
)

ADDED = Added((WALLS,), before=3)

#: The model's closer choices on what it's for, after the person's answer.
CLOSER = Question(
    feature="purpose",
    text="The tray is lifted by its rim: how much infill does its floor need to stay flat?",
    options=(
        Option(label="15% (preset)", settings={"sparse_infill_density": "15%"},
               impact="the floor can bow a few tenths of a millimetre when lifted full",
               estimate=Estimate(print_time_s=4526, filament_g=15.88, filament_cm3=12.41)),
        Option(label="25%", settings={"sparse_infill_density": "25%"}, recommended=True,
               why="the floor stays flat under a full tray for a few grams more",
               impact="the floor stays flat when lifted full; a few grams more",
               estimate=Estimate(print_time_s=4690, filament_g=17.3, filament_cm3=13.5)),
    ),
)

#: The job's questions as print_job.py asks them: the notes first, then the rest, then the
#: model's added one.
QUESTIONS = (FOR, SEEN_NOTED, BRIDGE)
