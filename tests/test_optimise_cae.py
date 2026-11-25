"""`optimise` in the bench's second image, on planted parts (mqucifer/print-shop#286): BESO on
CalculiX, as a reference for a design round and never a part. Needs `scripts/bench.py build cae`.

Everything is made from scratch by `cae_cases/make_optimise.py`: the MBB beam, the standard test
of topology optimisation, whose answer is a known truss, and a cantilever with a block kept
solid. What's kept is read from the STL file the command writes, point by point, by a ray's
crossings of its triangles.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import host

pytestmark = pytest.mark.cae
CASES = Path(__file__).parent / "cae_cases"

#: The MBB beam, 1 kg on the middle of its top, kept to half: 120 x 20, standing on its feet.
MBB = ("mbb/assembly.json", "--weight", "1", "--at", "60,2,20", "--over", "3", "--volume", "0.5",
       "--mesh", "2", "--up", "+z")  # fmt: skip
#: The cantilever, 2 kg at the top of its free end, kept to 0.4, with a block at its middle kept.
BLOCK = (35.0, -1.0, 5.0, 45.0, 5.0, 15.0)
CANTILEVER = ("cantilever/assembly.json", "--weight", "2", "--at", "78,2,20", "--over", "3",
              "--volume", "0.4", "--mesh", "2",
              "--keep", ",".join(f"{v:g}" for v in BLOCK))  # fmt: skip


@dataclass
class Answer:
    code: int
    text: str
    record: dict[str, Any]

    @property
    def numbers(self) -> dict[str, Any]:
        return dict(self.record["numbers"])


@pytest.fixture(scope="module")
def cae(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id(host.CAE_IMAGE) is None:
        pytest.fail(f"{host.missing(host.CAE_IMAGE)} first")
    work = tmp_path_factory.mktemp("optimise")
    shutil.copy(CASES / "make_optimise.py", work / "make_optimise.py")
    made = host.run(["make_optimise.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr

    def ask(*args: str) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    return ask


@pytest.fixture(scope="module")
def mbb(cae: Any) -> Answer:
    return cae("optimise", *MBB)  # type: ignore[no-any-return]


def triangles(path: Path) -> np.ndarray:
    data = path.read_bytes()
    count = struct.unpack("<I", data[80:84])[0]
    rows = np.frombuffer(
        data[84 : 84 + 50 * count],
        dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
    )
    return rows["v"].astype(float)


def kept_at(found: np.ndarray, point: tuple[float, float, float]) -> bool:
    """Whether a point is inside the kept material: a ray from it crosses the outside an odd
    number of times (Möller and Trumbore's test, each triangle at once)."""
    origin = np.array(point, dtype=float)
    way = np.array([0.0, 1.0, 1e-4])
    a, b, c = found[:, 0], found[:, 1], found[:, 2]
    one, two = b - a, c - a
    h = np.cross(way, two)
    det = np.einsum("ij,ij->i", one, h)
    ok = np.abs(det) > 1e-12
    inverse = 1 / np.where(ok, det, 1)
    s = origin - a
    u = inverse * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, one)
    v = inverse * (q @ way)
    t = inverse * np.einsum("ij,ij->i", two, q)
    return int((ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)).sum()) % 2 == 1


def test_the_mbb_beam_keeps_the_known_truss_as_a_reference_never_a_part(
    cae: Any, mbb: Answer
) -> None:
    """The standard test: a simply supported beam loaded in the middle of its top, kept to half.
    Its answer is a truss: a chord along the top over the middle, one along the bottom, and
    diagonals from each support up to the top chord, with holes between them, and the top
    corners over the supports empty. One test, so the beam is solved once on any worker."""
    assert mbb.code == 0, mbb.text
    assert mbb.record["verdict"] == "OK"
    numbers = mbb.numbers
    assert numbers["volume_kept"] == pytest.approx(0.5, abs=0.005)
    found = triangles(cae.work / "optimised" / "beam-v0.5" / "kept.stl")
    kept = {
        "the top chord, a third of the way along": (40, 2, 18.5),
        "the top chord, two thirds of the way along": (80, 2, 18.5),
        "the bottom chord at the middle": (60, 2, 1.5),
        "the bottom chord, a third of the way along": (40, 2, 1.5),
        "the diagonal at the left support": (5, 2, 3.5),
    }
    empty = {
        "the top corner over the left support": (5, 2, 18),
        "the top corner over the right support": (115, 2, 18),
        "the hole under the load, half way up": (60, 2, 10),
        "the hole a fifth of the way along, half way up": (22, 2, 10.5),
        "the hole three quarters of the way along": (88, 2, 13.5),
    }
    assert [name for name, p in kept.items() if not kept_at(found, p)] == []
    assert [name for name, p in empty.items() if kept_at(found, p)] == []
    # The reply says it's a reference, never a part, in its verdict and its numbers; with the
    # volume kept, the peak stress before and after against the material's limits, the pictures
    # and the mesh file.
    text = mbb.text
    assert "verdict: OK: a reference, never a part" in text
    assert "never a part" in text.splitlines()[1] and mbb.numbers["reference_only"] is True
    numbers = mbb.numbers
    before, after = numbers["before"], numbers["after"]
    assert 0 < before["mises_mpa"] < after["mises_mpa"] < numbers["yield_mpa"] == 45.0
    assert 0 <= after["across_mpa"] < numbers["across_limit_mpa"] == 14.0
    assert "against the tensile yield's 45" in text and "interlayer adhesion's 14" in text
    files = mbb.record["files"]
    assert files == [f"optimised/beam-v0.5/{n}" for n in ("kept.stl", "above.png", "below.png")]
    for name in files[1:]:
        assert (cae.work / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # The second methods agree: BESO's own last solve with the void left in, and the STL file's
    # volume read back.
    assert numbers["beso_mises_after_mpa"] == pytest.approx(after["mises_mpa"], rel=0.05)
    assert numbers["stl_volume_mm3"] == pytest.approx(numbers["volume_kept_mm3"], rel=1e-4)
    assert len(mbb.record["second_check"]) == 4
    # The way up is read, not steered: where the kept material would need support.
    assert numbers["overhang"]["area_mm2"] > 0 and "read, not steered" in text
    # It ran sealed in the second image.
    ran = mbb.record["ran"]
    assert ran["network_up"] == ["lo"] and ran["network_routes"] == 0
    assert ran["cpu_max"].split()[0] == "200000", "2 CPUs"
    assert ran["image"] == host.CAE_IMAGE and ran["image_id"] == host.image_id(host.CAE_IMAGE)


def test_a_kept_block_stays_solid_and_the_same_inputs_give_the_same_result(cae: Any) -> None:
    """A cantilever with a block at its middle kept solid: every element of it is in the
    result. And run again, every figure is the same, the kept elements by their digest."""
    first = cae("optimise", *CANTILEVER)
    assert first.code == 0, first.text
    numbers = first.numbers
    assert numbers["kept_solid"]["box 1"] > 100
    assert numbers["kept_solid_kept"]["box 1"] == 1.0
    assert numbers["volume_kept"] == pytest.approx(0.4, abs=0.005)
    found = triangles(cae.work / "optimised" / "beam-v0.4" / "kept.stl")
    x0, _, z0, x1, _, z1 = BLOCK
    inside = [
        (x, 2.0, z) for x in np.linspace(x0 + 1, x1 - 1, 5) for z in np.linspace(z0 + 1, z1 - 1, 5)
    ]
    assert [p for p in inside if not kept_at(found, p)] == []
    again = cae("optimise", *CANTILEVER)
    assert again.code == 0, again.text
    for name in ("mesh_digest", "kept_digest", "volume_kept", "before", "after", "iterations",
                 "beso_mises_after_mpa", "stl_volume_mm3"):  # fmt: skip
        assert again.numbers[name] == numbers[name], name


@pytest.mark.parametrize(
    ("changed", "code", "said"),
    [
        (("--volume", "1.2"), 3, "more than 0 and less than 1"),
        (("--volume", "0"), 3, "more than 0 and less than 1"),
        (("--at", "60,2,40"), 2, "mm from the assembly's surface"),
        (("--up", "+x"), 2, "isn't along the part's layers"),
    ],
)
def test_a_volume_outside_0_to_1_or_a_load_off_the_part_is_refused(
    cae: Any, changed: tuple[str, ...], code: int, said: str
) -> None:
    args = list(MBB)
    for option, value in zip(changed[::2], changed[1::2], strict=True):
        args[args.index(option) + 1] = value
    done = host.run(["optimise", *args], cae.work)
    assert done.returncode == code, done.stdout + done.stderr
    assert said in done.stdout + done.stderr
