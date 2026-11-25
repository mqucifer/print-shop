"""Write the form each Orca setting's value takes, from Orca's own definitions.

mqucifer/print-shop#296. A correction's value is checked for its setting's form before anything
slices: a whole number, a number, a percentage, mm or a percentage, on or off. Nothing on the Mac
lists those forms (Orca's command line has no option table, and its bundle none either), and a
value as Orca writes it can't tell a bool's 0 from an int's, or a float of 200 from a whole
number. So they're read from where Orca defines them: `src/libslic3r/PrintConfig.cpp`, each
`def = this->add("wall_loops", coInt)`, of the version installed.

This reads that file from the path given, and never fetches it. Get it from Orca's public
repository at the installed version's tag, then run:

    uv run python scripts/orca_forms.py PrintConfig.cpp --version 2.4.2

It writes `src/print_shop/setup/orca_forms.json`. Run it again when Orca is updated:
`tests/test_setting_forms.py` fails while the installed Orca isn't the version it names.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "print_shop" / "setup" / "orca_forms.json"

#: `def = this->add("wall_loops", coInt);`, and its nullable twin.
_ADD = re.compile(r'this->add(?:_nullable)?\("([a-z_0-9]+)",\s*co([A-Za-z]+)\)')
#: The filament's overrides of the printer's retraction, each typed as the setting it overrides.
_OVERRIDES = re.compile(r"filament_extruder_override_keys\s*=\s*\{(.*?)\};", re.S)
#: An old name read as a new one: `} else if (opt_key == "chamber_temperatures") { opt_key =
#: "chamber_temperature";`.
_LEGACY = re.compile(r'opt_key == "([a-z_0-9]+)"\)\s*\{\s*opt_key = "([a-z_0-9]+)";')


def forms(source: str) -> dict[str, str]:
    """Each setting's form, by key: Orca's type, lower-cased without its `co` (`int`, `floats`,
    `floatorpercent`, `bool`, `enum` …)."""
    out = {key: kind.lower() for key, kind in _ADD.findall(source)}
    found = _OVERRIDES.search(source)
    if found is not None:
        for key in re.findall(r'"([a-z_0-9]+)"', found.group(1)):
            raw = key.removeprefix("filament_")
            if raw in out and key not in out:
                out[key] = out[raw]
    for old, new in _LEGACY.findall(source):
        if new in out and old not in out:
            out[old] = out[new]
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="Orca's src/libslic3r/PrintConfig.cpp")
    parser.add_argument("--version", required=True, help="the Orca version it's from, e.g. 2.4.2")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    found = forms(args.source.read_text())
    if len(found) < 500:
        print(f"only {len(found)} settings read: is this Orca's PrintConfig.cpp?", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(
        {"orca": args.version,
         "from": f"OrcaSlicer v{args.version}, src/libslic3r/PrintConfig.cpp",
         "forms": found}, indent=1) + "\n")
    print(f"{len(found)} settings' forms, from Orca {args.version}, to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
