"""`load` whose first of two passes meets the container's memory meshing the whole, in the
bench's second image (mqucifer/print-shop#375): the control for the band's read after two
passes asked for with `--parts`. Nothing here changes how the bench reads anything else.

`python mesh_memory_forced.py load ASSEMBLY ... --parts` runs `load` as the bench does, with the
mesher of the first pass's whole, at both its meshes, a program that takes memory until the
kernel stops it: so it meets the container's memory there, really, on any machine. It's started
as gmsh is, by `bench.cae.gmsh`, first to go, and the command is left to say so. Every other
mesh, the second pass's parts and each glass band's read, is gmsh's own.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402

# The bench as the container mounts it, `bench`, not as this repository installs it.
load_parts: Any = importlib.import_module("bench.load_parts")
main = importlib.import_module("bench.cli").main

#: What stands in for gmsh: a program that takes 64 MB at a time, and writes to each, until
#: it's stopped. In /work, beside the assembly, as /tmp is mounted without running programs.
HOG = Path.cwd() / "mesh_memory_hog"
HOG.write_text(
    "#!/opt/conda/bin/python\n"
    "held = []\n"
    "while True:\n"
    "    held.append(b'\\1' * (64 << 20))\n"
)
os.chmod(HOG, 0o755)
FIRST = load_parts.first
GMSH = "User parameter:BaseApp/Preferences/Mod/Fem/Gmsh"


def hogged(*args: Any, **given: Any) -> Any:
    preferences = App.ParamGet(GMSH)
    preferences.SetString("gmshBinaryPath", str(HOG))
    try:
        return FIRST(*args, **given)
    finally:
        preferences.SetString("gmshBinaryPath", "")


load_parts.first = hogged
sys.exit(main(sys.argv[1:]))
