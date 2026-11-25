"""`optimise` outside the images (mqucifer/print-shop#286): what's refused before anything runs,
which image it runs in and for how long, and the arithmetic it reads BESO's answer with. No
Docker, and no model. BESO itself, on the MBB beam and a cantilever, is in
`test_optimise_cae.py`.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from print_shop.bench import cli, host, optimise
from print_shop.bench.report import NoAnswer


def parse(*words: str) -> object:
    return cli.parser().parse_args(["optimise", "a.json", "--weight", "1", "--at", "0,0,0", *words])


# Refused before anything runs


@pytest.mark.parametrize("volume", ["0", "1", "1.5", "-0.2", "nan", "half"])
def test_a_target_volume_outside_0_to_1_is_refused(
    volume: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        parse("--volume", volume)
    assert stopped.value.code == 3, "the command itself was wrong"
    said = capsys.readouterr().err
    assert "--volume" in said and "more than 0 and less than 1" in said


def test_a_target_volume_is_a_share_and_the_rest_is_load_s() -> None:
    args = parse(
        "--volume", "0.4", "--keep", "-5,0,0,5,4,10", "--keep-face", "-1,2,3", "--up", "-z"
    )
    assert args.volume == 0.4  # type: ignore[attr-defined]
    assert args.keep == [([-5.0, 0.0, 0.0], [5.0, 4.0, 10.0])]  # type: ignore[attr-defined]
    assert args.keep_face == [[-1.0, 2.0, 3.0]]  # type: ignore[attr-defined]
    assert args.up == "-z" and args.toward == "-z" and args.over == 10.0  # type: ignore[attr-defined]


def test_a_target_volume_must_be_given(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        parse()
    assert stopped.value.code == 3 and "--volume" in capsys.readouterr().err


# Where it runs


def test_optimise_runs_in_the_second_image_with_a_limit_of_its_own() -> None:
    """Each of BESO's tens of iterations solves the whole part: an hour, where every other
    command has `TIMEOUT`."""
    assert host.image_for(["optimise", "a.json"]) == host.CAE_IMAGE
    assert host.image_for(["--out", "x", "optimise", "a.json"]) == host.CAE_IMAGE
    assert host.timeout_for(["--out=x", "optimise", "a.json"]) == 3600
    assert host.timeout_for(["load", "a.json"]) == host.TIMEOUT
    assert host.timeout_for(["optimise.step"]) == host.TIMEOUT, "a script, not the command"
    assert host.TIMEOUTS["optimise"] > optimise.BESO_LIMIT, "BESO is stopped first, and said"


# The input file

INP = """** made up
*NODE, NSET=Nall
1, 0, 0, 0
2, 1, 0, 0
3, 0, 1, 0
4, 0, 0, 1
5, 1, 1, 1
*ELEMENT, TYPE=C3D10, ELSET=Evolumes
1, 1, 2, 3, 4, 1, 1, 1, 1, 1, 1
2, 2, 3, 4, 5,
2, 2, 2, 2, 2, 2
*ELSET, ELSET=Listed
1, 2,
*STEP
*STATIC
*NODE FILE
U
*END STEP
"""


def test_the_mesh_is_read_from_calculix_s_input_lines_that_run_on_too() -> None:
    nodes, elements = optimise._mesh(INP)
    assert sorted(nodes) == [1, 2, 3, 4, 5] and list(nodes[5]) == [1.0, 1.0, 1.0]
    assert elements == {1: [1, 2, 3, 4, 1, 1, 1, 1, 1, 1], 2: [2, 3, 4, 5, 2, 2, 2, 2, 2, 2]}


def test_a_mesh_of_other_elements_is_no_answer() -> None:
    with pytest.raises(NoAnswer, match="BESO isn't given"):
        optimise._mesh(INP.replace("C3D10", "C3D4"))


def test_sets_go_in_before_the_step_and_prints_before_its_end() -> None:
    text = optimise._with_sets(INP, {"Kept": [2, 1]}, "*EL PRINT, ELSET=Kept\nS\n")
    assert text.index("*ELSET, ELSET=Kept\n2,1,\n") < text.index("*STEP")
    assert text.index("*EL PRINT, ELSET=Kept") < text.index("*END STEP") > text.index("*STATIC")


def test_elements_taken_away_leave_their_lines_and_every_set_that_lists_them() -> None:
    text = optimise._without(INP, {2})
    _, elements = optimise._mesh(text)
    assert list(elements) == [1]
    listed = text.split("*ELSET, ELSET=Listed\n")[1].splitlines()[0]
    assert listed == "1,"


# What BESO left


def cube(size: float = 2.0) -> tuple[dict[int, np.ndarray], dict[int, list[int]]]:
    """A cube as six tetrahedra round its long diagonal, each with mid-side nodes left at its
    corners (they don't count here)."""
    corners = [np.array([x, y, z]) * size for z in (0, 1) for y in (0, 1) for x in (0, 1)]
    nodes = {i + 1: p.astype(float) for i, p in enumerate(corners)}
    # The six around the diagonal from node 1 (0,0,0) to node 8 (1,1,1).
    ring = [2, 4, 3, 7, 5, 6]
    elements = {}
    for k in range(6):
        a, b = ring[k], ring[(k + 1) % 6]
        elements[k + 1] = [1, a, b, 8] + [1] * 6
    return nodes, elements


def test_the_kept_outside_reads_back_from_its_stl_file_as_the_elements_volume(
    tmp_path: Path,
) -> None:
    nodes, elements = cube()
    triangles = optimise._outward(nodes, elements, list(elements))
    assert len(triangles) == 12, "two to each side of the cube, none inside"
    path = tmp_path / "kept.stl"
    optimise.write_stl(path, triangles, "a cube")
    assert optimise.stl_volume(path) == pytest.approx(8.0, abs=1e-6)
    # Half of it: the outside still closes, and faces out.
    optimise.write_stl(path, optimise._outward(nodes, elements, [1, 2, 3]), "half")
    assert optimise.stl_volume(path) == pytest.approx(4.0, abs=1e-6)


def test_pieces_join_where_elements_share_a_face_and_not_at_an_edge() -> None:
    nodes, elements = cube()
    assert optimise._connected(elements, [1, 2, 3, 4, 5, 6]) == [[1, 2, 3, 4, 5, 6]]
    # Two opposite tetrahedra of the ring share only the diagonal.
    assert optimise._connected(elements, [1, 4]) == [[1], [4]]


def test_von_mises_is_the_pull_alone_and_three_halves_root_of_shear() -> None:
    rows = np.array([[10.0, 0, 0, 0, 0, 0], [0, 0, 0, 5, 0, 0], [7, 7, 7, 0, 0, 0]])
    assert optimise.von_mises(rows) == pytest.approx([10.0, 5 * math.sqrt(3), 0.0])


def test_the_peaks_are_the_largest_stress_and_the_largest_pull_across_the_layers() -> None:
    stresses = {
        1: np.array([[1.0, 0, 9, 0, 0, 0], [0, 0, 0, 0, 0, 0]]),
        2: np.array([[20.0, 0, -30, 0, 0, 0]]),
    }
    middles = {1: np.array([0.0, 0, 1]), 2: np.array([5.0, 0, 0])}
    found = optimise._peaks(stresses, middles, axis=2)
    assert found["mises_mpa"] == pytest.approx(math.sqrt(20**2 + 30**2 + 20 * 30))
    assert found["mises_at"] == [5.0, 0.0, 0.0]
    # A push across the layers parts nothing: the pull of 9 is the peak there.
    assert found["across_mpa"] == 9.0 and found["across_at"] == [0.0, 0.0, 1.0]


def test_an_overhang_is_what_faces_down_past_45_degrees_above_the_lowest_level() -> None:
    up = np.array([0.0, 0, 1])
    floor = [[0.0, 0, 0], [0, 1, 0], [1, 0, 0]]  # faces down, at the lowest level: the bed
    roof = [[0.0, 0, 5], [0, 2, 5], [2, 0, 5]]  # faces down, 5 up: hangs over air
    slope = [[0.0, 0, 3], [1, 0, 3], [0, 1, 4]]  # faces down at 45°: a print takes it
    triangles = np.array([floor, roof, slope], dtype=float)
    hangs = optimise.overhang(triangles, up, near=0.5)
    assert hangs["area_mm2"] == pytest.approx(2.0)
    assert hangs["low"] == [0.0, 0.0, 5.0] and hangs["high"] == [2.0, 2.0, 5.0]
    assert optimise.overhang(triangles[:1], up, near=0.5)["area_mm2"] == 0.0


LOG = """
iterations_limit        = 7

domain order:
0) Optimised
1) Kept

   i              mass    ener_dens_mean FI_violated_0)   1) all)          FI_mean    _without_state0         FI_max_0)                1)              all)
   0  9531.3  0.00094   0    0    0  0.04  0.04  0.24  0.10  0.24
   1  9054.5  0.00099   0    0    0  0.05  0.05  0.25  0.11  0.25
"""  # noqa: E501


def test_beso_s_table_is_read_a_row_an_iteration_with_its_peak_last() -> None:
    rows = optimise._rows(LOG)
    assert len(rows) == 2 and rows[-1][-1] == 0.25 and rows[0][1] == 9531.3
    assert "settled" in optimise._stopped(LOG, 2)
    assert "limit of 7" in optimise._stopped(LOG, 8)
    assert "turned back" in optimise._stopped(LOG + "OSCILLATION: model turns back", 2)
    with pytest.raises(NoAnswer, match="no table"):
        optimise._rows("nothing here")


def test_beso_s_settings_keep_the_target_volume_and_the_kept_set_solid(tmp_path: Path) -> None:
    from print_shop.bench.assembly import MATERIALS

    text = optimise._settings(tmp_path, 0.42, MATERIALS["petg"], 45.0, 2.0, 1000, 2)
    space: dict[str, object] = {
        "domain_optimized": {}, "domain_density": {}, "domain_thickness": {},
        "domain_offset": {}, "domain_orientation": {}, "domain_FI": {},
        "domain_material": {}, "domain_same_state": {},
    }  # fmt: skip
    exec(text, space)  # noqa: S102 - the settings are Python, as BESO runs them
    assert space["domain_optimized"] == {"Optimised": True, "Kept": False}
    assert space["mass_goal_ratio"] == 0.42 and space["cpu_cores"] == 2
    assert space["FI_violated_tolerance"] == 1001, "no count of stressed elements stops it"
    assert space["filter_list"] == [["simple", 4.0]], "no casting filter: it cut the load path"
    void, solid = space["domain_FI"]["Optimised"]  # type: ignore[index]
    assert solid == [("stress_von_Mises", 45.0)] and void[0][1] > 1e6, "a void is never the peak"


def test_no_need_is_checked_by_optimise() -> None:
    """A reference, never a part: a need that names `optimise` is refused, and isn't run."""
    from print_shop.rounds import needs, toolsets

    entry = {
        "id": "n",
        "what": "the brace keeps its strength at half its weight",
        "source": "chosen",
        "check": {
            "by": "bench",
            "command": "optimise brace.json --weight 5 --at 0,0,10 --volume 0.5",
            "number": "volume_kept",
            "at_most": 0.5,
        },
    }
    _, wrong = needs.read([entry], "", toolset=toolsets.EVERY)
    assert any("`optimise` is a reference for a design round, never a part" in w for w in wrong)
    need = needs.Need(id="n", what="x", source="chosen", by="bench", command="optimise b.json")
    assert not needs.readable(need), "and a need's check that names it isn't run"
