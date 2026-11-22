"""The slice, read at the review: how long each layer takes, what each feature costs, the seam.

mqucifer/print-shop#105. The review once saw only a slice's totals. On the first real prints,
reading the G-code by hand found three faults behind blobby walls, and none of them could be
corrected at the review: layers of about 8 s with the slow-down for layer cooling off, ironing
on faces nobody sees, and an aligned seam on walls that are the part's whole look.

So each slice is read layer by layer (`slicer/gcode.py`) into a `SliceReading`, kept with the
job in the ledger, and `flags` says what matters for this part. Which faces are seen comes
from the intake (`questions.SEEN`), or the part's context where the person confirmed it
(mqucifer/print-shop#236) or the intake didn't say, never from which face is on top: settings
depend on the part, not the material. A flag proposes a change with its reason and the reading
it rests on, like every other change. It never applies one: the person takes it at the review,
or doesn't.

**A part's fits** (mqucifer/print-shop#130, `context.py`), placed as heights in the pose it
prints in: a part with one never gets a random seam proposed, and the reason names the fit; a fit
at the plate gets elephant foot compensation proposed, inside its bound. A hold that slides on
along the layers' stacking is said.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from print_shop.bench import pausing
from print_shop.materials import material_name
from print_shop.setup.context import FitBand, Placed, Seen
from print_shop.setup.intake import Intake
from print_shop.setup.proposal import Change, Source
from print_shop.slicer import gcode

__all__ = [
    "PAUSE_PAST_FIT",
    "PauseReading",
    "FeatureReading",
    "Flag",
    "LayerTime",
    "SeamReading",
    "Seen",
    "SliceReading",
    "flags",
    "read_slice",
    "seen",
]

#: Orca's label for ironing.
IRONING = "Ironing"
#: The flag for a pause a layer past a fit's top, which the review says beside where it landed.
PAUSE_PAST_FIT = "pause_past_fit"
#: A fit band's heights are kept to a hundredth of a millimetre (`context.place`).
BAND_MM = 0.005 + pausing.TOPS_OUT
#: The most room over what's dropped in for a fit's top to say where its pocket closes: the
#: bench's `pause --max-gap`. A band may be the dropped part's height, not the pocket's.
ROOM_MM = 0.4
#: Orca's labels for what it lays over air, as the first layer over a pocket is.
CLOSES_OVER = ("Bridge", "Overhang wall")
#: Two seams this close, in mm, on neighbouring layers, are one line up the wall.
SEAM_NEAR_MM = 1.0
#: The share of seams that sit on the one below them for the seam to read as a line.
SEAM_LINE = 0.8
#: The settings a reading judges, as the slice was made with them.
JUDGED = (
    "filament_type",
    "slow_down_for_layer_cooling",
    "slow_down_layer_time",
    "slow_down_min_speed",
    "ironing_type",
    "seam_position",
    "elefant_foot_compensation",
    "raft_layers",  # on one, a height in the G-code isn't that height up the part
)
#: What a fit at the plate is proposed for elephant foot, in mm: the Sponsor's own profile's
#: value (the skill's adhesion group), never past its bound.
FIT_ELEPHANT_FOOT_MM = 0.1


class LayerTime(BaseModel):
    """One layer's time, as the moves take it."""

    model_config = {"frozen": True}

    n: int
    z: float
    time_s: float


class FeatureReading(BaseModel):
    """What one feature costs over the whole slice: time, area, and where it lies."""

    model_config = {"frozen": True}

    kind: str  # Orca's label, or "travel"
    time_s: float
    area_mm2: float
    layers: int
    from_z: float
    to_z: float


class SeamReading(BaseModel):
    """Where the outer walls' loops start: their seams."""

    model_config = {"frozen": True}

    seams: int
    #: The share of seams with one within `SEAM_NEAR_MM` on the layer below: a line up the wall.
    stacked: float
    #: The spot most layers' seams are at, on the plate, and how many layers have one there.
    at: tuple[float, float] | None = None
    at_layers: int = 0

    @property
    def is_line(self) -> bool:
        return self.seams > 0 and self.stacked >= SEAM_LINE


class PauseReading(BaseModel):
    """A pause Orca wrote into the slice: the layer it comes before and that layer's top, the
    top of the layer it comes after, which is the pause's height (mqucifer/print-shop#203), and
    how far into the print it stops (mqucifer/print-shop#119)."""

    model_config = {"frozen": True}

    before_layer: int
    z: float
    after_s: float
    #: None before the first layer, and in a row from before mqucifer/print-shop#203.
    after_z: float | None = None

    def where(self) -> str:
        """Where it falls, in the words of the bench's `pause`."""
        if self.after_z is None:
            return f"before layer {self.before_layer}, at z={pausing.mm(self.z)}"
        return pausing.where(self.before_layer - 1, self.after_z, self.before_layer, self.z)

    def line(self) -> str:
        return f"pauses for a filament swap {self.where()}, about {_s(self.after_s)} in"


class SliceReading(BaseModel):
    """One slice, read layer by layer."""

    model_config = {"frozen": True}

    layers: tuple[LayerTime, ...]
    features: tuple[FeatureReading, ...]
    seam: SeamReading
    #: Each ironed layer's top, in mm, and its ironing time.
    ironed: tuple[LayerTime, ...] = ()
    #: The values of `JUDGED` the slice was made with, from its config block.
    settings: dict[str, str] = {}
    #: Orca's own estimate for the whole print, beside the layers' sum.
    orca_time_s: int | None = None
    #: Each pause Orca wrote, as asked for at the review or written in a file sliced by hand.
    pauses: tuple[PauseReading, ...] = ()
    #: Each layer that lays `CLOSES_OVER`: its number, and the area of it in mm².
    over_air: tuple[tuple[int, float], ...] = ()

    @property
    def time_s(self) -> float:
        return round(sum(layer.time_s for layer in self.layers), 1)

    def shortest(self) -> LayerTime | None:
        return min(self.layers, key=lambda layer: layer.time_s, default=None)

    def under(self, seconds: float) -> list[LayerTime]:
        return [layer for layer in self.layers if layer.time_s < seconds]

    def feature(self, kind: str) -> FeatureReading | None:
        return next((f for f in self.features if f.kind == kind), None)

    def lines(self) -> list[str]:
        """The reading as the review shows it."""
        out = []
        shortest = self.shortest()
        if shortest is not None:
            times = sorted(layer.time_s for layer in self.layers)
            out.append(
                f"layers: {len(self.layers)}, {_s(times[0])} to {_s(times[-1])} each, "
                f"{_s(times[len(times) // 2])} typical; the shortest is layer {shortest.n} "
                f"at z {shortest.z:g} mm"
            )
        kept = [f for f in self.features if f.kind != gcode.TRAVEL]
        for f in sorted(kept, key=lambda f: -f.time_s):
            out.append(
                f"{f.kind}: {_s(f.time_s)}, {f.area_mm2:.0f} mm2 on {f.layers} "
                f"layer{'s' if f.layers != 1 else ''}, z {f.from_z:g} to {f.to_z:g} mm"
            )
        out += [p.line() for p in self.pauses]
        if self.seam.seams:
            where = (f", most at x {self.seam.at[0]:g} y {self.seam.at[1]:g} "
                     f"({self.seam.at_layers} layers)" if self.seam.at else "")
            shape = "a line up the wall" if self.seam.is_line else "scattered"
            out.append(f"seam ({self.settings.get('seam_position', '?')}): {self.seam.seams} "
                       f"loop starts, {self.seam.stacked:.0%} on the one below, so "
                       f"{shape}{where}")
        return out


class Flag(BaseModel):
    """Something the reading says matters for this part, and the change it proposes, if any."""

    model_config = {"frozen": True}

    id: str  # "layer_time", "ironing_hidden", "ironing_inside", "seam_seen"
    reading: str  # what the slice shows, with its figures
    change: Change | None = None


def read_slice(path: Path) -> SliceReading:
    """The G-code at `path`, read into one structure for the job."""
    sliced = gcode.read(path)
    layers = tuple(
        LayerTime(n=layer.n, z=layer.z, time_s=round(layer.time_s, 2)) for layer in sliced.layers
    )
    kinds = sorted({k for layer in sliced.layers for k in (*layer.times, *layer.areas)})
    features = []
    for kind in kinds:
        on = [layer for layer in sliced.layers
              if layer.times.get(kind, 0) > 0 or layer.areas.get(kind, 0) > 0]
        features.append(FeatureReading(
            kind=kind,
            time_s=round(sum(layer.times.get(kind, 0.0) for layer in on), 1),
            area_mm2=round(sum(layer.areas.get(kind, 0.0) for layer in on), 1),
            layers=len(on),
            from_z=min(layer.z for layer in on),
            to_z=max(layer.z for layer in on),
        ))
    ironed = tuple(
        LayerTime(n=layer.n, z=layer.z, time_s=round(layer.times.get(IRONING, 0.0), 2))
        for layer in sliced.layers if layer.areas.get(IRONING, 0) > 0
    )
    pauses = tuple(
        PauseReading(
            before_layer=int(p["before_layer"]), z=float(p["z"]),
            # The layer before it as written: a support's layer may stand between two of the part's.
            after_z=layers[int(p["before_layer"]) - 2].z if int(p["before_layer"]) > 1 else None,
            after_s=round(sum(t.time_s for t in layers if t.n < int(p["before_layer"])), 1),
        )
        for p in sliced.pauses
    )
    return SliceReading(
        pauses=pauses,
        over_air=tuple(
            (layer.n, round(area, 1)) for layer in sliced.layers
            if (area := sum(layer.areas.get(kind, 0.0) for kind in CLOSES_OVER)) > 0
        ),
        layers=layers,
        features=tuple(features),
        seam=_seam(sliced.layers),
        ironed=ironed,
        settings={k: sliced.footer[k] for k in JUDGED if k in sliced.footer},
        orca_time_s=sliced.time_s,
    )


def _seam(layers: list[gcode.Layer]) -> SeamReading:
    total = stacked = 0
    below: list[tuple[float, float]] = []
    spots: Counter[tuple[float, float]] = Counter()
    for layer in layers:
        for x, y in layer.seams:
            total += 1
            if any(abs(x - bx) <= SEAM_NEAR_MM and abs(y - by) <= SEAM_NEAR_MM for bx, by in below):
                stacked += 1
        for spot in {(round(x), round(y)) for x, y in layer.seams}:
            spots[spot] += 1
        below = layer.seams
    if not total:
        return SeamReading(seams=0, stacked=0.0)
    (at, count), = spots.most_common(1)
    return SeamReading(seams=total, stacked=round(stacked / total, 3),
                       at=(float(at[0]), float(at[1])), at_layers=count)


def seen(intake: Intake | None) -> Seen | None:
    """Which faces the person's own answer says are seen, or None when they didn't say. What
    they confirmed from the notes is None here: that's read as the context places it in the pose
    printed (`flags`), so that it follows a turn."""
    from print_shop.setup.questions import SEEN, SEEN_FACES  # noqa: PLC0415 - a cycle otherwise

    if intake is None:
        return None
    for question, answer in zip(intake.questions, intake.answers, strict=False):
        if question.feature != SEEN.feature or answer.choice is None:
            continue
        if question.confirmed(answer):
            continue
        label = question.options[answer.choice].label
        faces = SEEN_FACES.get(label)
        if faces is not None:
            walls, top, bottom = faces
            return Seen(walls=walls, top=top, bottom=bottom, said=label)
    return None


def flags(
    reading: SliceReading,
    faces: Seen | None,
    process: dict[str, Any],
    filament: dict[str, Any],
    placed: Placed | None = None,
    changes: tuple[Change, ...] = (),
) -> tuple[Flag, ...]:
    """What the reading says matters for this part, each with the change it proposes.

    `process` and `filament` are the presets' own values: a proposed change is shown against
    them, as every change is. `placed` is the part's context in this pose (mqucifer/print-shop#130):
    its fits keep a random seam off them and propose elephant foot compensation at the plate, a
    hold pulled across the layers is said, and its seen faces stand where the intake didn't say.
    A pause a layer past a fit's top is said too (mqucifer/print-shop#203).
    `changes` are the proposal's, which the slice was made with: a value they set, such as a
    lesson's, is read as the one in effect and named.
    """
    if faces is None and placed is not None:
        faces = placed.seen
    found = [*_layer_time(reading, filament), *_ironing(reading, faces, process),
             *_seam_flag(reading, faces, process, placed),
             *_elephant_foot(reading, process, placed, changes), *_holds(placed),
             *_past_fit(reading, placed)]
    return tuple(found)


def _past_fit(reading: SliceReading, placed: Placed | None) -> list[Flag]:
    """A pause after the layer that closes over a fit, too late to drop parts into it: what a
    height typed a layer up, as it had to be before mqucifer/print-shop#203, now does.

    A fit's top isn't where its pocket closes: the band may be the dropped part's, with room
    planned over it, and then a pause a layer over the band is the right one. So the slice has
    to show it closing: the layer the pause comes after is the first since the band's foot to
    bridge or overhang, and lays more of it than the layer after it, within `ROOM_MM` of the
    band's top. Where it can't tell, it says nothing. Said, never refused, as a colour change
    may be meant there.
    """
    if placed is None or reading.settings.get("raft_layers", "0") not in ("", "0"):
        return []
    area = dict(reading.over_air)
    found: dict[float, Flag] = {}
    for pause in reading.pauses:
        n = pause.before_layer - 1  # the layer it comes after
        if pause.after_z is None or not 2 <= n < len(reading.layers):
            continue
        if area.get(n, 0.0) <= area.get(n + 1, 0.0):
            continue  # it lays no more over air than the layer after: that one may be what closes
        under = reading.layers[n - 2].z
        for band in placed.fits:
            if band.z_to >= (under + pause.after_z) / 2 or under - band.z_to > ROOM_MM + BAND_MM:
                continue  # under the band's top, or too far over it to be its pocket
            if any(area.get(la.n, 0.0) for la in reading.layers[:n - 1] if la.z > band.z_from):
                continue  # something under it is over air already, so which closes it isn't known
            found.setdefault(band.z_to, Flag(
                id=PAUSE_PAST_FIT,
                reading=f"the fit `{band.fit.name}` tops out at z={pausing.mm(band.z_to)}, and "
                        "the layer this pauses after is the first to bridge or overhang above "
                        f"it: for parts dropped in, pause_at={pausing.mm(under)}",
            ))
    return list(found.values())


def _layer_time(reading: SliceReading, filament: dict[str, Any]) -> list[Flag]:
    s = reading.settings
    try:
        threshold = float(s.get("slow_down_layer_time", "0"))
        floor = float(s.get("slow_down_min_speed", "0"))
    except ValueError:
        return []
    short = reading.under(threshold) if threshold > 0 else []
    shortest = reading.shortest()
    if not short or shortest is None:
        return []
    material = material_name(s.get("filament_type", "")) or "the filament"
    said = (
        f"{len(short)} of {len(reading.layers)} layers take under {threshold:g} s, the "
        f"filament's layer time for cooling; the shortest, layer {shortest.n} at z "
        f"{shortest.z:g} mm, takes {_s(shortest.time_s)} (short layers from z "
        f"{short[0].z:g} to {short[-1].z:g} mm)"
    )
    if s.get("slow_down_for_layer_cooling", "1") != "1":
        change = Change(
            key="slow_down_for_layer_cooling",
            preset=_first(filament, "slow_down_for_layer_cooling"), value="1",
            source=Source.READING, feature="layer_time", knowledge="speed-cooling",
            reason=f"each layer is laid on one that hasn't had {threshold:g} s to cool, too "
                   f"short for {material}: slow-down holds each layer to at least that",
            reading=said)
    else:
        if floor <= 1:
            return [Flag(id="layer_time", reading=said + "; slow-down is on and its floor is "
                         "as low as it goes: printing several copies at once lengthens a layer")]
        lower = f"{max(1.0, floor / 2):g}"
        change = Change(
            key="slow_down_min_speed", preset=_first(filament, "slow_down_min_speed"),
            value=lower, source=Source.READING, feature="layer_time", knowledge="speed-cooling",
            reason=f"slow-down is on, but Orca won't go under {floor:g} mm/s, so these layers "
                   f"still finish early; a lower floor lets them take the {threshold:g} s",
            reading=said)
    return [Flag(id="layer_time", reading=said, change=change)]


def _ironing(reading: SliceReading, faces: Seen | None, process: dict[str, Any]) -> list[Flag]:
    ironing = reading.feature(IRONING)
    if ironing is None or not reading.ironed:
        return []
    heights = sorted({layer.z for layer in reading.ironed})
    said = (
        f"ironing takes {_s(ironing.time_s)} over {ironing.area_mm2:.0f} mm2, on "
        f"{len(heights)} height{'s' if len(heights) != 1 else ''}: z "
        + ", ".join(f"{z:g}" for z in heights[:6]) + (" ..." if len(heights) > 6 else "")
        + " mm"
    )
    preset = _first(process, "ironing_type")
    if faces is None:
        return [Flag(id="ironing_unknown", reading=said + "; the intake didn't say which "
                     "faces are seen, so whether it's worth it isn't judged")]
    if not faces.top:
        return [Flag(id="ironing_hidden", reading=said, change=Change(
            key="ironing_type", preset=preset, value="no ironing", source=Source.READING,
            feature="seen", knowledge="surface", reading=said,
            reason=f"{faces.whose()} '{faces.said}', so ironing costs {_s(ironing.time_s)} for "
                   "faces nobody sees"))]
    below = [z for z in heights if z < heights[-1]]
    if below and reading.settings.get("ironing_type") in ("top", "solid"):
        return [Flag(id="ironing_inside", reading=said, change=Change(
            key="ironing_type", preset=preset, value="topmost", source=Source.READING,
            feature="seen", knowledge="surface", reading=said,
            reason=f"the top is seen, but ironing also smooths {len(below)} lower "
                   f"top{'s' if len(below) != 1 else ''} inside the part (z "
                   f"{', '.join(f'{z:g}' for z in below[:4])} mm): topmost irons only the "
                   "last top layer"))]
    return []


def _seam_flag(
    reading: SliceReading, faces: Seen | None, process: dict[str, Any], placed: Placed | None,
) -> list[Flag]:
    seam = reading.seam
    fits = placed.fits if placed is not None else ()
    if fits:
        return _seam_on_a_fit(reading, faces, process, fits)
    if not seam.is_line or faces is None or not faces.walls:
        return []
    where = f" at x {seam.at[0]:g} y {seam.at[1]:g}" if seam.at else ""
    said = (f"{seam.stacked:.0%} of {seam.seams} wall loops start on the one below: the seam "
            f"is a line up the wall{where}, over {seam.at_layers} layers")
    now = reading.settings.get("seam_position", "")
    if now in ("back", "aligned_back"):
        value, how = "random", "random scatters it, spreading the mark over the wall"
    else:
        value, how = "back", ("back puts the line on the part's back as it sits on the plate "
                              "(its largest y); random would scatter it instead")
    return [Flag(id="seam_seen", reading=said, change=Change(
        key="seam_position", preset=_first(process, "seam_position"), value=value,
        source=Source.READING, feature="seen", knowledge="surface", reading=said,
        reason=f"{faces.whose()} '{faces.said}', so the walls are seen, and the seam is a line on "
               f"them: {how}"))]


def fits_said(fits: tuple[FitBand, ...]) -> str:
    """The fits as a reason names them."""
    return "; ".join(band.line() for band in fits)


def _seam_on_a_fit(
    reading: SliceReading, faces: Seen | None, process: dict[str, Any],
    fits: tuple[FitBand, ...],
) -> list[Flag]:
    """A part with a fit never gets a random seam (mqucifer/print-shop#130): it would scatter seam
    bumps all round the fit. A line leaves one mark, at the back where the walls are seen."""
    seam = reading.seam
    now = reading.settings.get("seam_position", "")
    named = fits_said(fits)
    if now == "random":
        value = "back" if faces is not None and faces.walls else "aligned"
        said = (f"the seam is {now}: {seam.seams} wall loop starts scattered, "
                f"{seam.stacked:.0%} on the one below")
        where = ("back puts the line on the part's back (its largest y), as the walls are seen"
                 if value == "back" else "aligned lines it up at the least seen spots")
        return [Flag(id="seam_fit", reading=said, change=Change(
            key="seam_position", preset=_first(process, "seam_position"), value=value,
            source=Source.READING, feature="fit", knowledge="surface", reading=said,
            reason=f"a random seam scatters seam bumps all round the fit, {named}: one line "
                   f"leaves one mark; {where}"))]
    if not seam.is_line or faces is None or not faces.walls:
        return []
    where = f" at x {seam.at[0]:g} y {seam.at[1]:g}" if seam.at else ""
    said = (f"{seam.stacked:.0%} of {seam.seams} wall loops start on the one below: the seam "
            f"is a line up the wall{where}, over {seam.at_layers} layers")
    if now in ("back", "aligned_back"):
        return [Flag(id="seam_fit", reading=(
            f"{said}; the walls are seen, but random isn't proposed: it would scatter seam bumps "
            f"all round the fit, {named}. The line stays at the back"))]
    return [Flag(id="seam_fit", reading=said, change=Change(
        key="seam_position", preset=_first(process, "seam_position"), value="back",
        source=Source.READING, feature="fit", knowledge="surface", reading=said,
        reason=f"{faces.whose()} '{faces.said}', so the walls are seen, and the seam is a line "
               "on them: back puts the line on the part's back (its largest y). Not random: it "
               f"would scatter seam bumps all round the fit, {named}"))]


def _elephant_foot(
    reading: SliceReading, process: dict[str, Any], placed: Placed | None,
    changes: tuple[Change, ...],
) -> list[Flag]:
    """A fit at the plate wants elephant foot compensation: its bounded value is proposed, never
    applied (the operator, 2026-11-14). A value already set at least as high, by a lesson, the
    model or the person, stands, and the note says the fit is covered by it; one set lower is
    proposed up to the fit's, never down."""
    if placed is None or not reading.layers:
        return []
    first = reading.layers[0].z
    at_plate = tuple(b for b in placed.fits if b.z_from <= first + 1e-6)
    if not at_plate:
        return []
    preset = _first(process, "elefant_foot_compensation")
    setting = next((c for c in changes if c.key == "elefant_foot_compensation"), None)
    raw = setting.value if setting is not None else reading.settings.get(
        "elefant_foot_compensation", preset)
    try:
        now = float(raw or 0)
    except ValueError:
        return []
    width = _first(process, "initial_layer_line_width")
    try:
        # A share of the nozzle, which this reading doesn't know, leaves the default.
        ceiling = 0.0 if width.endswith("%") else float(width or 0)
    except ValueError:
        ceiling = 0.0
    wanted = min(FIT_ELEPHANT_FOOT_MM, ceiling) if ceiling > 0 else FIT_ELEPHANT_FOOT_MM
    by = f", set by {_set_by(setting)}" if setting is not None else ""
    said = (f"the first layer's top is z {first:g} mm and the fit reaches the plate: "
            f"{fits_said(at_plate)}; elefant_foot_compensation is {now:g}{by}")
    if now >= wanted:
        return [Flag(id="fit_elephant_foot", reading=(
            f"{said}: the plate fit is covered, as {now:g} mm is at least the {wanted:g} mm it "
            "wants, so nothing is proposed"))]
    raised = (f", raising the {now:g} mm {_set_by(setting)} set; that stands where it's larger"
              if setting is not None and now > 0 else "")
    return [Flag(id="fit_elephant_foot", reading=said, change=Change(
        key="elefant_foot_compensation", preset=preset, value=f"{wanted:g}",
        source=Source.READING, feature="fit", knowledge="adhesion",
        reading=said,
        reason="the first layers squash out wider than the rest (elephant foot), which closes "
               f"the {at_plate[0].fit.play()} fit at the plate: compensation shrinks the first "
               f"layer by {wanted:g} mm, inside its bound ({FIT_ELEPHANT_FOOT_MM:g} mm is your "
               f"profile's own){raised}. Proposed, not applied"))]


def _set_by(change: Change | None) -> str:
    """Who set a change, as the reading names them."""
    if change is None:
        return "the preset"
    if change.source is Source.LESSON:
        return f"lesson {change.lesson}" if change.lesson is not None else "a lesson"
    return {Source.MODEL: "the model", Source.ANSWER: "your answer", Source.SPONSOR: "you",
            Source.GUARD: "a guard", Source.READING: "the slice's proposal you took"}[change.source]


def _holds(placed: Placed | None) -> list[Flag]:
    """A hold whose way on or off is up and down as it prints, and a pose off the context's side:
    said, with no change (turning the part is the person's)."""
    if placed is None:
        return []
    found = [Flag(id="hold_layers", reading=(
        f"{h.line()}: it goes on and off along z as this prints, so its pull is across the layer "
        "lines, where a print is weakest; laid with the slide across the plate it pulls along "
        "them")) for h in placed.across]
    if placed.off_side:
        found.append(Flag(id="off_side", reading=placed.off_side))
    return found


def _first(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value)


def _s(seconds: float) -> str:
    """A time as a person reads it: 9.6 s, 4 min 12 s."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s"
