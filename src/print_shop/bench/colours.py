"""A file's colour and label on a sheet, as the one asking gives them, read the same on both sides.

The bench draws with these, and the harness checks them before it asks: both import this, so no
image library is needed to read a colour. On `cup-1` the sheets gave each file a colour from a list
of 5, starting again at the 6th, so a pane took the frame's colour and didn't show as glass. A file
with no colour given now takes one no other file on the sheet has.
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass

#: Names a person might give a filament, and what each is drawn in. `clear` is drawn pale, since a
#: white background would hide it.
NAMED: dict[str, tuple[int, int, int]] = {
    "amber": (255, 176, 0),
    "aqua": (0, 200, 200),
    "black": (30, 30, 30),
    "blue": (40, 90, 220),
    "bronze": (160, 110, 50),
    "brown": (130, 80, 40),
    "clear": (215, 225, 230),
    "copper": (184, 115, 51),
    "cream": (240, 230, 190),
    "crimson": (200, 20, 60),
    "cyan": (0, 190, 230),
    "gold": (230, 180, 30),
    "gray": (140, 140, 140),
    "green": (40, 160, 60),
    "grey": (140, 140, 140),
    "indigo": (75, 40, 150),
    "lavender": (180, 160, 230),
    "lilac": (200, 160, 210),
    "lime": (150, 220, 40),
    "magenta": (210, 40, 170),
    "maroon": (120, 20, 40),
    "mint": (150, 230, 190),
    "navy": (20, 40, 120),
    "olive": (120, 120, 30),
    "orange": (245, 130, 30),
    "peach": (250, 190, 150),
    "pink": (245, 140, 180),
    "purple": (130, 60, 170),
    "red": (210, 40, 40),
    "rose": (230, 100, 130),
    "ruby": (170, 20, 50),
    "salmon": (240, 128, 114),
    "silver": (190, 190, 195),
    "sky": (120, 190, 240),
    "tan": (210, 180, 140),
    "teal": (0, 128, 128),
    "turquoise": (60, 210, 200),
    "violet": (140, 80, 210),
    "white": (245, 245, 245),
    "yellow": (240, 210, 40),
}
HEX = re.compile(r"#[0-9a-fA-F]{6}")
#: A colour's words: a name, perhaps with `light` or `dark`, or #rrggbb.
SHADES = {"light": 0.45, "dark": -0.4, "pale": 0.6, "deep": -0.25}
LABEL = re.compile(r"[^,=:\n]{1,32}")


class NotAColour(ValueError):
    """A colour this can't draw, said in words the one asking can act on."""


def rgb(colour: str) -> tuple[int, int, int]:
    """`blue`, `light green`, `dark-red` or `#2a5bd7`, as red, green and blue from 0 to 255."""
    text = colour.strip().casefold().replace("-", " ").replace("_", " ")
    if HEX.fullmatch(text):
        return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
    words = text.split()
    shade = 0.0
    if len(words) == 2 and words[0] in SHADES:
        shade = SHADES[words[0]]
        words = words[1:]
    if len(words) != 1 or words[0] not in NAMED:
        names = ", ".join(sorted(NAMED))
        raise NotAColour(
            f"{colour!r} isn't a colour the sheet can draw: one of {names}, perhaps with light, "
            "dark, pale or deep before it, or #rrggbb"
        )
    base = NAMED[words[0]]
    if shade > 0:
        return tuple(round(c + (255 - c) * shade) for c in base)  # type: ignore[return-value]
    return tuple(round(c * (1 + shade)) for c in base)  # type: ignore[return-value]


@dataclass
class Look:
    """How one file is drawn: its colour as given, and a short label."""

    file: str
    colour: str
    label: str = ""

    @property
    def rgb(self) -> tuple[int, int, int]:
        return rgb(self.colour)


def parse(text: str) -> Look:
    """`FILE=COLOUR` or `FILE=COLOUR:LABEL`, as `render --look` takes it."""
    name, _, rest = text.partition("=")
    colour, _, label = rest.partition(":")
    if not name or not colour:
        raise NotAColour(f"a look is FILE=COLOUR or FILE=COLOUR:LABEL, not {text!r}")
    rgb(colour)
    if label and not LABEL.fullmatch(label):
        raise NotAColour(f"a label is a few words, of at most 32 characters: not {label!r}")
    return Look(name, colour.strip(), label.strip())


def argument(file: str, colour: str, label: str = "") -> str:
    """The `--look` word for a file, as `parse` reads it back."""
    return f"{file}={colour}:{label}" if label else f"{file}={colour}"


def _pool() -> list[tuple[int, int, int]]:
    """Clear colours to choose from: hues round the circle, at three lightnesses and two
    saturations, none near white, which is the background, or black, which is the outline."""
    found = []
    for step in range(48):
        for light in (0.35, 0.5, 0.65):
            for saturation in (0.55, 0.85):
                r, g, b = colorsys.hls_to_rgb(step / 48, light, saturation)
                found.append((round(r * 255), round(g * 255), round(b * 255)))
    return found


def _apart(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True))


def distinct(n: int, taken: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """`n` colours, each as far as it can be from the others and from those `taken`: what a file
    with no colour given is drawn in, so no two files on a sheet look the same. Each is the one
    of the pool furthest from every colour so far, so the same files get the same colours."""
    pool = _pool()
    found: list[tuple[int, int, int]] = []
    for _ in range(n):
        near = [*taken, *found]
        if not near:
            found.append(pool[0])
            continue
        found.append(max(pool, key=lambda c: min(_apart(c, o) for o in near)))
    return found
