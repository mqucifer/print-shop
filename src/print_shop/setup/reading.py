"""The slice, read at the review: how long each layer takes, what each feature costs, the seam.

mqucifer/print-shop#105. The review once saw only a slice's totals. On the first real prints,
reading the G-code by hand found three faults behind blobby walls, and none of them could be
corrected at the review: layers of about 8 s with the slow-down for layer cooling off, ironing
on faces nobody sees, and an aligned seam on walls that are the part's whole look.

So each slice is read layer by layer (`slicer/gcode.py`) into a `SliceReading`, kept with the
job in the ledger, and `flags` says what matters for this part. Which faces are seen comes
from the intake (`questions.SEEN`), never from which face is on top: settings depend on the
part, not the material. A flag proposes a change with its reason and the reading it rests on,
like every other change. It never applies one: the person takes it at the review, or doesn't.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from print_shop.setup.intake import Intake
from print_shop.setup.proposal import Change, Source
from print_shop.slicer import gcode

__all__ = [
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
)


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
        if self.seam.seams:
            where = (f", most at x {self.seam.at[0]:g} y {self.seam.at[1]:g} "
                     f"({self.seam.at_layers} layers)" if self.seam.at else "")
            shape = "a line up the wall" if self.seam.is_line else "scattered"
            out.append(f"seam ({self.settings.get('seam_position', '?')}): {self.seam.seams} "
                       f"loop starts, {self.seam.stacked:.0%} on the one below, so "
                       f"{shape}{where}")
        return out


class Seen(BaseModel):
    """Which faces of the part will be seen, from the intake's answer."""

    model_config = {"frozen": True}

    walls: bool
    top: bool  # the top as it prints
    bottom: bool  # the face on the plate
    said: str  # the answer, in its words


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
    return SliceReading(
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
    """Which faces the intake's answer says are seen, or None when it wasn't asked or said."""
    from print_shop.setup.questions import SEEN, SEEN_FACES  # noqa: PLC0415 - a cycle otherwise

    if intake is None:
        return None
    for question, answer in zip(intake.questions, intake.answers, strict=False):
        if question.feature != SEEN.feature or answer.choice is None:
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
) -> tuple[Flag, ...]:
    """What the reading says matters for this part, each with the change it proposes.

    `process` and `filament` are the presets' own values: a proposed change is shown against
    them, as every change is.
    """
    found = [*_layer_time(reading, filament), *_ironing(reading, faces, process),
             *_seam_flag(reading, faces, process)]
    return tuple(found)


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
    material = s.get("filament_type", "the filament")
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
            reason=f"you said '{faces.said}', so ironing costs {_s(ironing.time_s)} for "
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


def _seam_flag(reading: SliceReading, faces: Seen | None, process: dict[str, Any]) -> list[Flag]:
    seam = reading.seam
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
        reason=f"you said '{faces.said}', so the walls are seen, and the seam is a line on "
               f"them: {how}"))]


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
