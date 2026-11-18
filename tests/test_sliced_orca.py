"""What Orca itself writes for a project with per-object overrides, read back
(mqucifer/print-shop#164).

`tests/sliced_fixtures.py` builds a sliced plate file in the shape Orca wrote one on the Mac on
2026-11-17. This runs the real Orca (`slicer`), so that shape is held to what Orca writes now:
two tiles go on a plate, each gets an override in the project as Orca's object list writes
them (`Metadata/model_settings.config`), and Orca slices the project and exports the plate's
sliced file. The G-code must show the override was used and name none of it; the plate file
must hold the same G-code and both objects' overrides.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest
import trimesh

from print_shop import sliced
from print_shop.slicer.orca import BedType, SlicerProfiles, with_bed_type
from print_shop.slicer.store import Store, choose
from tests.fakes import STORE
from tests.test_graph import ORCA, needs_orca

_SETTINGS = "Metadata/model_settings.config"
_OVERRIDES = {
    "tileA.stl": {"layer_height": "0.12"},
    "tileB.stl": {"wall_loops": "5", "sparse_infill_density": "40%"},
}


def _orca(cwd: Path, *args: str) -> None:
    with tempfile.TemporaryDirectory(prefix="orca-data-") as data:
        done = subprocess.run([str(ORCA), "--datadir", data, *args], cwd=cwd, timeout=600,
                              capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]


@pytest.mark.slicer
@needs_orca
def test_orcas_plate_file_carries_each_objects_overrides_and_its_gcode_none(
    tmp_path: Path,
) -> None:
    choice = choose(Store(STORE), nozzle_mm=0.4, filament="Elegoo PETG")
    composed = choice.compose(tmp_path / "profiles")
    profiles = with_bed_type(
        SlicerProfiles(machine=composed["machine"], process=composed["process"],
                       filament=composed["filament"]),
        BedType.TEXTURED_PEI, tmp_path / "profiles")
    for name in _OVERRIDES:
        trimesh.creation.box(extents=(20, 20, 2)).export(tmp_path / name)

    # The two tiles on a plate, as a project: Orca writes it, with no override yet.
    plain = tmp_path / "plain"
    plain.mkdir()
    _orca(plain, "--load-settings", f"{profiles.machine};{profiles.process}",
          "--load-filaments", str(profiles.filament), "--allow-rotations=0", "--slice", "0",
          "--outputdir", str(plain), "--export-3mf", "plain.3mf",
          *(str(tmp_path / name) for name in _OVERRIDES))
    plain_layers = sliced.read(plain / "plate_1.gcode").layer_count

    # Each object's override, written where Orca's object list writes one.
    project = tmp_path / "project.3mf"
    with zipfile.ZipFile(plain / "plain.3mf") as src, zipfile.ZipFile(project, "w") as dst:
        for item in src.infolist():
            if re.match(r"Metadata/(plate_\d+\.(gcode|gcode\.md5|json)|slice_info\.config)$",
                        item.filename):
                continue  # unsliced again
            data = src.read(item.filename)
            if item.filename == _SETTINGS:
                text = data.decode()
                for name, settings in _OVERRIDES.items():
                    held = "".join(f'\n    <metadata key="{k}" value="{v}"/>'
                                   for k, v in settings.items())
                    at = f'<metadata key="name" value="{name}"/>'
                    text = text.replace(at, at + held, 1)
                data = text.encode()
            dst.writestr(item, data)

    out = tmp_path / "out"
    out.mkdir()
    _orca(out, "--slice", "0", "--outputdir", str(out), "--export-3mf", "tiles.gcode.3mf",
          str(project))

    gcode = sliced.read(out / "plate_1.gcode")
    assert gcode.layer_count > plain_layers, "tile A's 0.12 mm layers were sliced"
    assert gcode.config["layer_height"] == "0.2", "the block says the plate's, not tile A's"
    assert gcode.config["wall_loops"] == "2"
    assert gcode.overrides is None

    plate = sliced.read(out / "tiles.gcode.3mf")
    assert plate.kind == "plate" and plate.sha256 == gcode.sha256, "the same G-code inside"
    assert plate.objects == gcode.objects and len(plate.objects) == 2
    assert plate.overrides is not None
    found = {sliced._COPY.sub("", o.name): o.settings for o in plate.overrides}
    assert found == _OVERRIDES
    assert {o.name for o in plate.overrides} == set(plate.objects), "named as the G-code does"
