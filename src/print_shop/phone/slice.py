"""What the slice shows, on the phone (mqucifer/print-shop#221): print-shop-ux's approved screen.

Every figure is one the terminal's review already reads from the slice (`setup/screen.py`'s
"what the slice shows", mqucifer/print-shop#223): the layers' times, each feature's time, area,
layers and z range, what's worth a look, what the slice proposes, and the seam. Nothing is read
here that the review doesn't read, and nothing comes from the model.

With no script: the bars are drawn by the server, a layer a bar, the pause a gap between two,
and each bar is a link that shows that layer (`?layer=21`), as do the arrows and the number box.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from print_shop.bench.pausing import TOPS_OUT
from print_shop.phone.page import page
from print_shop.setup.names import named, switched
from print_shop.setup.reading import PAUSE_PAST_FIT, Flag, LayerTime, SliceReading
from print_shop.setup.screen import clock, layer_time

__all__ = ["features_html", "layers_said", "seam_said", "slice_page", "worth"]



def _e(value: object) -> str:
    return escape(str(value))


def _took(seconds: float) -> str:
    """"3 min 50 s", or "24 s"."""
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def _z(from_z: float, to_z: float) -> str:
    return f"z {from_z:g}" if from_z == to_z else f"z {from_z:g} to {to_z:g}"


def _bars(reading: SliceReading, picked: LayerTime | None, here: str) -> str:
    longest = max((layer.time_s for layer in reading.layers), default=0.0) or 1.0
    gaps = {p.before_layer for p in reading.pauses}
    out = []
    for layer in reading.layers:
        if layer.n in gaps:
            out.append("<span class=\"gap\" aria-hidden=\"true\"></span>")
        height = max(2, round(100 * layer.time_s / longest))
        on = " class=\"on\"" if picked is not None and layer.n == picked.n else ""
        out.append(f"<a{on} href=\"{_e(here)}?layer={layer.n}\" style=\"height: {height}%\" "
                   f"aria-label=\"Layer {layer.n}\" title=\"Layer {layer.n}\"></a>")
    pause = ""
    if reading.pauses:
        p = reading.pauses[0]
        more = f", and {len(reading.pauses) - 1} more" if len(reading.pauses) > 1 else ""
        pause = f"the gap is your pause, {_took(p.after_s)} in{more}"
    return (f"<div class=\"bars\">{''.join(out)}</div>"
            f"<div class=\"ends small\"><span>plate</span><span class=\"mid\">{_e(pause)}</span>"
            "<span>top</span></div>")


def _picked(reading: SliceReading, picked: LayerTime, here: str) -> str:
    after = next((p for p in reading.pauses if p.before_layer == picked.n), None)
    on = [f.kind for f in reading.features
          if f.kind != "travel" and f.from_z - TOPS_OUT <= picked.z <= f.to_z + TOPS_OUT]
    said = [f"Layer {picked.n}, z {picked.z:g} mm: {layer_time(picked.time_s)}."]
    if after is not None:
        said.append("The first layer after the pause.")
    if on:
        said.append(f"Its features: {', '.join(on)}.")
    numbers = [layer.n for layer in reading.layers]
    at = numbers.index(picked.n)
    prev = (f"<a href=\"{_e(here)}?layer={numbers[at - 1]}\">Layer {numbers[at - 1]}</a>"
            if at > 0 else "")
    nxt = (f"<a href=\"{_e(here)}?layer={numbers[at + 1]}\">Layer {numbers[at + 1]}</a>"
           if at + 1 < len(numbers) else "")
    return (f"<div class=\"box\">{_e(' '.join(said))}"
            f"<div class=\"ends small\"><span>{prev}</span><span>{nxt}</span></div></div>")


def features_html(reading: SliceReading) -> str:
    kept = sorted((f for f in reading.features if f.kind != "travel"), key=lambda f: -f.time_s)
    longest = max((f.time_s for f in kept), default=0.0) or 1.0
    rows = []
    for f in kept:
        width = max(1, round(100 * f.time_s / longest))
        rows.append(
            f"<div class=\"feat\"><div class=\"head\"><span><span class=\"part\">{_e(f.kind)}"
            f"</span><br><span class=\"small\">{f.area_mm2:,.0f} mm², {f.layers} layer"
            f"{'s' if f.layers != 1 else ''}, {_e(_z(f.from_z, f.to_z))}</span></span>"
            f"<span>{_e(clock(f.time_s))}</span></div>"
            f"<div class=\"bar\" style=\"width: {width}%\"></div></div>")
    return "".join(rows)


def worth(reading: SliceReading, flags: Sequence[Flag]) -> list[str]:
    """What's worth a look, as the review says it: the first layer after a pause, and each of
    the reading's flags that proposes nothing."""
    out = []
    for p in reading.pauses:
        for f in reading.features:
            if f.kind != "travel" and abs(f.from_z - p.z) <= TOPS_OUT:
                out.append(f"{f.kind} starts on layer {p.before_layer}, the first after the "
                           f"pause, at z {p.z:g}.")
    out += [flag.reading for flag in flags if flag.change is None and flag.id != PAUSE_PAST_FIT]
    return out


def _proposed(reading: SliceReading, flags: Sequence[Flag]) -> list[str]:
    """What the slice proposes, as the review numbers it: never applied here."""
    out = []
    for flag in flags:
        c = flag.change
        if c is None:
            continue
        sliced = reading.settings.get(c.key, c.preset)
        out.append(f"{named(c.key)}: {switched(c.key, sliced) or '-'} to "
                   f"{switched(c.key, c.value)}. {c.reason} Read: {flag.reading}")
    return out


def layers_said(reading: SliceReading) -> list[str]:
    out = []
    again = reading.starts_again()
    if again:
        out.append(f"By object: z starts again at layer{'s' if len(again) != 1 else ''} "
                   f"{', '.join(map(str, again))}, so a layer's z is up its own object.")
    shortest = reading.shortest()
    if shortest is not None:
        times = sorted(layer.time_s for layer in reading.layers)
        # The review's words for a layer's time, a tenth kept under a minute
        # (mqucifer/print-shop#298).
        out.append(f"{len(reading.layers)} layers, {layer_time(times[0])} to "
                   f"{layer_time(times[-1])} each, {layer_time(times[len(times) // 2])} typical. "
                   "The shortest is layer "
                   f"{shortest.n} at z {shortest.z:g} mm.")
    for p in reading.pauses:
        out.append(f"It {p.line()}.")
    return out


def seam_said(reading: SliceReading) -> str:
    seam = reading.seam
    if not seam.seams:
        return ""
    where = (f" Most at x {seam.at[0]:g} y {seam.at[1]:g} ({seam.at_layers} layers)."
             if seam.at else "")
    shape = "a line up the wall" if seam.is_line else "scattered"
    return (f"{reading.settings.get('seam_position', '?')}. {seam.seams} loop starts, "
            f"{seam.stacked:.0%} stacked: {shape}.{where}")


def slice_page(
    *, part: str, reading: SliceReading, flags: Sequence[Flag], print_time_s: int | None,
    grams: float | None, here: str, back: str, back_words: str, layer: int | None = None,
) -> str:
    """What the slice shows: `here` is this screen's own path, for its links, and `back` where
    "Back" goes."""
    picked = next((x for x in reading.layers if x.n == layer), None)
    facts = [part]
    if print_time_s:
        facts.append(f"{max(1, round(print_time_s / 60))} min")
    facts.append(f"{len(reading.layers)} layers")
    if grams:
        facts.append(f"{grams:g} g")
    body = [f"<a class=\"back\" href=\"{_e(back)}\">{_e(back_words)}</a>",
            "<h1>What the slice shows</h1>", f"<p class=\"small\">{_e('. '.join(facts))}.</p>",
            "<h2>Time on each layer</h2>", _bars(reading, picked, here)]
    if picked is not None:
        body.append(_picked(reading, picked, here))
    body.append(f"<form class=\"layer\" method=\"get\" action=\"{_e(here)}\">"
                "<label for=\"layer\">Layer</label><input id=\"layer\" type=\"number\" "
                f"name=\"layer\" min=\"1\" inputmode=\"numeric\" value=\"{_e(layer or '')}\">"
                "<button type=\"submit\">Show</button></form>")
    body += [f"<p class=\"small\">{_e(said)}</p>" for said in layers_said(reading)]
    body += ["<h2>Where the time goes</h2>", features_html(reading)]
    looks = worth(reading, flags)
    if looks:
        body.append("<h2>Worth a look</h2><ul class=\"rows\">")
        body += [f"<li><div class=\"row\"><span class=\"what amber\">{_e(w)}</span></div></li>"
                 for w in looks]
        body.append("</ul>")
    proposed = _proposed(reading, flags)
    if proposed:
        body.append("<h2>Proposed from the slice, not applied unless you take it</h2>"
                    "<ul class=\"rows\">")
        body += [f"<li><div class=\"row\"><span class=\"what\">{_e(p)}</span></div></li>"
                 for p in proposed]
        body.append("</ul>")
    seam = seam_said(reading)
    if seam:
        body += ["<h2>Seam</h2>", f"<p>{_e(seam)}</p>"]
    return page(f"What the slice shows: {part}", "".join(body))
