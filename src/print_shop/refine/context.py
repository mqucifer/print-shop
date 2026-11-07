"""What the pass slices with: the fitted nozzle, a filament for each colour, the Sponsor's presets.

As `scripts/print_job.py` does before the inventory is set up, it's said on the command line:
`--nozzle` and `--filament`, a filament by variety ("Elegoo PETG Translucent") or by the name of
one of the Sponsor's tunings, for every colour, or `COLOUR=NAME` for one. The store
(mqucifer/print-shop#6) then picks the machine, process and filament by Orca's rule, the
Sponsor's tuning where there is one. The plate is the Sponsor's textured PEI, as a print takes it
(`runtime.bed_type`): on Orca's default Cool Plate, PETG is refused (exit 195). The inventory
isn't read: opening it writes the print ledger's tables, and this pass writes nothing outside
the run's records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.bench import colours
from print_shop.slicer.orca import BedType, SlicerProfiles, with_bed_type, with_colour
from print_shop.slicer.store import STORE_DIR, Choice, NoChoice, Store, choose


@dataclass
class Context:
    nozzle_mm: float
    #: The filament for a colour, by the colour's words; "" is every colour not named.
    filaments: dict[str, str]
    machine: str | None = None
    process: str | None = None
    #: The build plate, as a print takes it (`runtime.bed_type`): the Sponsor's textured PEI.
    bed: BedType = BedType.TEXTURED_PEI
    store: Path = STORE_DIR
    _choices: dict[str, Choice] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def parse(cls, nozzle_mm: float, filaments: list[str], **kw: Any) -> Context:
        found: dict[str, str] = {}
        for given in filaments:
            colour, sep, name = given.partition("=")
            # A tuning's name can hold `=` ("...MaxFanSpeed=50"); a colour has no space or comma.
            if sep and colour.strip() and not set(colour.strip()) & {" ", ","}:
                found[colour.strip().casefold()] = name.strip()
            else:
                found[""] = given.strip()
        return cls(nozzle_mm, found, **kw)

    def filament_for(self, colour: str) -> str:
        name = self.filaments.get(colour.casefold()) or self.filaments.get("")
        if not name:
            raise NoChoice(
                f"no filament for {colour or 'a file with no colour'}: `--filament NAME` for "
                f"every colour, or `--filament {colour or 'COLOUR'}=NAME`"
            )
        return name

    def choice(self, colour: str) -> Choice:
        name = self.filament_for(colour)
        if name not in self._choices:
            self._choices[name] = choose(
                Store(self.store),
                nozzle_mm=self.nozzle_mm,
                filament=name,
                machine=self.machine,
                process=self.process,
            )
        return self._choices[name]

    def composed(self, colours_on_plate: list[str], workdir: Path) -> dict[str, Any]:
        """The plate's machine and process, and a filament for each colour, each whole and with
        its colour set: what `slice_plate` takes, and what the ledger keeps of it."""
        first = self.choice(colours_on_plate[0])
        written = first.compose(workdir / "profiles")
        filaments: list[Path] = []
        used: list[dict[str, Any]] = []
        for k, colour in enumerate(colours_on_plate, 1):
            choice = self.choice(colour)
            if (choice.machine.name, choice.process.name) != (
                first.machine.name,
                first.process.name,
            ):
                raise NoChoice(
                    f"'{choice.filament.name}' and '{first.filament.name}' pick different "
                    "machine or process profiles, and one plate slices with one of each"
                )
            own = choice.compose(workdir / f"profiles-{k}")["filament"]
            filaments.append(with_colour(own, hex_of(colour), workdir / f"filament-{k}.json"))
            used.append(
                {
                    "colour": colour,
                    "hex": hex_of(colour),
                    "filament": choice.filament.name,
                    "used": json.loads(choice.used(self.store).model_dump_json()),
                    "notes": list(choice.notes),
                }
            )
        process = with_bed_type(
            SlicerProfiles(machine=written["machine"], process=written["process"]),
            self.bed,
            workdir / "profiles",
        ).process
        return {
            "machine": written["machine"],
            "process": process,
            "filaments": filaments,
            "used": used,
        }

    def plain(self) -> dict[str, Any]:
        return {
            "nozzle_mm": self.nozzle_mm,
            "filaments": self.filaments,
            "machine": self.machine,
            "process": self.process,
            "bed": self.bed.value,
        }


def hex_of(colour: str) -> str:
    """A colour's words as Orca's `#RRGGBB`, read as the bench reads them: grey if none."""
    try:
        r, g, b = colours.rgb(colour) if colour else colours.NAMED["grey"]
    except ValueError:
        r, g, b = colours.NAMED["grey"]
    return f"#{r:02X}{g:02X}{b:02X}"


def layers_of(process: Path) -> tuple[float, float]:
    """The process profile's first layer and layer height, in mm, as `pause` takes them."""
    data = json.loads(process.read_text())

    def first(key: str, default: float) -> float:
        value = data.get(key, default)
        if isinstance(value, list):
            value = value[0] if value else default
        return float(value)

    return first("initial_layer_print_height", 0.2), first("layer_height", 0.2)


def line_width_of(process: Path, nozzle_mm: float) -> tuple[float, int, str] | None:
    """The process profile's inner wall line, in mm, its walls a side, and where the width came
    from: what a wall's width is a whole number of (mqucifer/print-shop#66). A width Orca gives as
    a share of the nozzle (`110%`) is read so; 0 is Orca's "auto", read as `line_width`. With
    neither, None: the pass doesn't guess Orca's own."""
    data = json.loads(process.read_text())

    def one(key: str) -> object:
        value = data.get(key)
        return value[0] if isinstance(value, list) and value else value

    def mm(value: object) -> float:
        text = str(value or "0").strip()
        if text.endswith("%"):
            return nozzle_mm * float(text[:-1]) / 100
        return float(text)

    loops = int(float(str(one("wall_loops") or 2)))
    for key in ("inner_wall_line_width", "line_width"):
        width = mm(one(key))
        if width > 0:
            return width, loops, f"the profile's `{key}`"
    return None
