"""Faces closer than the mesh's sag, and gmsh's local size there (mqucifer/print-shop#346): the
arithmetic, without FreeCAD. `test_cae.py` meshes a cap in its counterbore with them."""

from __future__ import annotations

import math

import numpy as np
import pytest

from print_shop.bench import close_faces
from print_shop.bench.close_faces import LONGEST, Close, fields, meeting, sag, size_for, stepped


def test_a_chord_sags_as_the_circle_s_own_arithmetic_says() -> None:
    """On a circle of radius 5, a side of 1.5 × 2 mm sags r − √(r² − c²/4); none on a flat."""
    chord = LONGEST * 2.0
    assert sag(1 / 5, 2.0) == pytest.approx(5 - math.sqrt(25 - chord**2 / 4))
    assert sag(0.0, 2.0) == 0.0


def test_gmsh_s_curvature_sizing_holds_a_coarse_mesh_s_sag() -> None:
    """gmsh leaves 12 elements a turn at the most, so a coarser mesh sags no more than that."""
    capped = sag(1 / 5, 2 * math.pi * 5 / 12)
    assert sag(1 / 5, 50.0) == pytest.approx(capped)
    assert sag(1 / 5, 1.0) < capped


def test_the_size_for_a_sag_sags_that_much() -> None:
    for radius in (0.5, 5.0, 80.0):
        for most in (1e-4, 0.01, 0.1):
            size = size_for(1 / radius, most)
            # Off gmsh's own curvature sizing, which would hold it finer still.
            assert sag(1 / radius, size, per_turn=1) == pytest.approx(most, rel=1e-6)
    assert size_for(0.0, 0.1) == math.inf


def test_a_size_steps_down_by_a_quarter_of_a_doubling() -> None:
    assert stepped(1.0) == 1.0
    assert stepped(0.99) == pytest.approx(2 ** (-1 / 4))
    assert stepped(0.42) <= 0.42 < stepped(0.42) * 2 ** (1 / 4)


def test_boxes_meet_where_their_gap_is_within_their_two_reaches() -> None:
    """Two boxes 0.1 apart along x meet only where their reaches add to 0.1 or more, and two
    flat faces with straight edges, no reach, never do."""
    low = np.array([[0.0, 0, 0], [1.1, 0, 0], [0.0, 5, 0]])
    high = np.array([[1.0, 1, 1], [2.0, 1, 1], [1.0, 6, 1]])
    assert list(meeting(low, high, np.array([0.06, 0.05, 0.0]))) == [(0, 1)]
    assert list(meeting(low, high, np.array([0.04, 0.05, 0.0]))) == []
    assert list(meeting(low, high, np.zeros(3))) == []
    # Overlapping boxes meet, either way round in the sort.
    low2 = np.array([[0.5, 0, 0], [0.0, 0, 0]])
    high2 = np.array([[2.0, 1, 1], [1.0, 1, 1]])
    assert list(meeting(low2, high2, np.array([0.0, 0.01]))) == [(0, 1)]


def test_each_face_and_edge_has_its_least_size_and_nothing_where_none_is_close() -> None:
    assert fields([], 3.0) == ""
    one = Close(7, 11, 0.15, faces={7: 0.7, 11: 0.72}, edges={20: 0.9})
    two = Close(7, 12, 0.10, faces={7: 0.55}, edges={})
    text = fields([one, two], 3.0)
    assert "Field[1] = Constant;\nField[1].SurfacesList = {7};" in text
    assert f"Field[1].VIn = {stepped(0.55):.6g};" in text
    assert "SurfacesList = {11};" in text and "CurvesList = {20};" in text
    assert text.count("= Constant;") == 3
    assert "Field[4] = Min;\nField[4].FieldsList = {1, 2, 3};\nBackground Field = 4;" in text
    # A size the mesh already meets isn't given, and none is finer than LEAST.
    assert fields([Close(1, 2, 1.0, faces={1: 5.0})], 3.0) == ""
    assert f"VIn = {close_faces.LEAST:.6g};" in fields([Close(1, 2, 0.0, faces={1: 0.0})], 3.0)


def test_a_pair_that_would_need_elements_finer_than_the_least_is_the_parts_to_fix() -> None:
    fine = Close(1, 2, 0.15, faces={1: 0.7})
    hair = Close(3, 4, 2e-4, faces={3: 0.03})
    assert close_faces.too_close([fine, hair]) == [hair]
