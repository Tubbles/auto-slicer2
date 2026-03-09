"""Tests for auto_slicer.packing — model bed packing."""

import tempfile
from pathlib import Path

import numpy as np
import pytest
from stl import mesh

from auto_slicer.packing import MODEL_GAP, adhesion_margin, convex_hull_2d, get_xy_bounds, get_xy_hull, pack_models


def _make_box_stl(path: Path, w: float, d: float, h: float) -> None:
    """Create a minimal STL box at origin with given dimensions."""
    vertices = np.array([
        [0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0],
        [0, 0, h], [w, 0, h], [w, d, h], [0, d, h],
    ])
    faces = np.array([
        [0, 1, 2], [0, 2, 3],  # bottom
        [4, 6, 5], [4, 7, 6],  # top
        [0, 4, 5], [0, 5, 1],  # front
        [2, 6, 7], [2, 7, 3],  # back
        [0, 3, 7], [0, 7, 4],  # left
        [1, 5, 6], [1, 6, 2],  # right
    ])
    m = mesh.Mesh(np.zeros(len(faces), dtype=mesh.Mesh.dtype))
    for i, f in enumerate(faces):
        for j in range(3):
            m.vectors[i][j] = vertices[f[j]]
    m.save(str(path))


class TestConvexHull2d:
    def test_square(self):
        pts = [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)]
        hull = convex_hull_2d(pts)
        assert len(hull) == 4  # interior point excluded

    def test_triangle(self):
        pts = [(0, 0), (4, 0), (2, 3)]
        hull = convex_hull_2d(pts)
        assert len(hull) == 3

    def test_collinear(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        hull = convex_hull_2d(pts)
        assert len(hull) == 2


class TestGetXyHull:
    def test_box_hull_centered(self, tmp_path):
        stl = tmp_path / "box.stl"
        _make_box_stl(stl, 30, 50, 10)
        hull = get_xy_hull(stl)
        # Hull of a box is 4 corners, centered at origin
        assert len(hull) == 4
        xs = [p[0] for p in hull]
        ys = [p[1] for p in hull]
        assert abs(sum(xs) / len(xs)) < 0.01
        assert abs(sum(ys) / len(ys)) < 0.01


class TestGetXyBounds:
    def test_basic_box(self, tmp_path):
        stl = tmp_path / "box.stl"
        _make_box_stl(stl, 30, 50, 10)
        w, d = get_xy_bounds(stl)
        assert abs(w - 30) < 0.01
        assert abs(d - 50) < 0.01


class TestAdhesionMargin:
    def test_skirt(self):
        m = adhesion_margin({"adhesion_type": "skirt", "skirt_distance": "5"})
        assert m == 5.0

    def test_brim(self):
        m = adhesion_margin({"adhesion_type": "brim", "brim_width": "10"})
        assert m == 10.0

    def test_raft(self):
        m = adhesion_margin({"adhesion_type": "raft", "raft_margin": "12"})
        assert m == 12.0

    def test_none(self):
        m = adhesion_margin({"adhesion_type": "none"})
        assert m == 0.0

    def test_defaults(self):
        m = adhesion_margin({})
        assert m == 3.0  # default skirt_distance


class TestPackModels:
    def test_single_model(self, tmp_path):
        stl = tmp_path / "a.stl"
        _make_box_stl(stl, 50, 50, 10)
        beds = pack_models([stl], 235, 235, {"adhesion_type": "skirt", "skirt_distance": "3"})
        assert len(beds) == 1
        assert len(beds[0]) == 1

    def test_two_models_fit_one_bed(self, tmp_path):
        a = tmp_path / "a.stl"
        b = tmp_path / "b.stl"
        _make_box_stl(a, 50, 50, 10)
        _make_box_stl(b, 50, 50, 10)
        beds = pack_models([a, b], 235, 235, {"adhesion_type": "skirt", "skirt_distance": "3"})
        assert len(beds) == 1
        assert len(beds[0]) == 2

    def test_models_overflow_to_second_bed(self, tmp_path):
        models = []
        for i in range(5):
            p = tmp_path / f"m{i}.stl"
            _make_box_stl(p, 100, 100, 10)
            models.append(p)
        # 100mm + margin ~ 105mm each, bed 235mm → max 2x2=4 per bed
        beds = pack_models(models, 235, 235, {"adhesion_type": "skirt", "skirt_distance": "3"})
        assert len(beds) >= 2
        total = sum(len(b) for b in beds)
        assert total == 5

    def test_single_model_centered(self, tmp_path):
        stl = tmp_path / "a.stl"
        _make_box_stl(stl, 50, 50, 10)
        beds = pack_models([stl], 200, 200, {"adhesion_type": "none"})
        _, ox, oy = beds[0][0]
        # Single model should be centered at (0, 0)
        assert abs(ox) < 1.0
        assert abs(oy) < 1.0

    def test_group_centered_on_bed(self, tmp_path):
        models = []
        for i in range(3):
            p = tmp_path / f"m{i}.stl"
            _make_box_stl(p, 40, 40, 10)
            models.append(p)
        beds = pack_models(models, 235, 235, {"adhesion_type": "none"})
        assert len(beds) == 1
        xs = [ox for _, ox, _ in beds[0]]
        ys = [oy for _, _, oy in beds[0]]
        # Group center should be near (0, 0)
        assert abs((min(xs) + max(xs)) / 2) < 1.0
        assert abs((min(ys) + max(ys)) / 2) < 1.0

    def test_brim_increases_effective_size(self, tmp_path):
        models = []
        for i in range(4):
            p = tmp_path / f"m{i}.stl"
            _make_box_stl(p, 100, 100, 10)
            models.append(p)
        # With no adhesion: 100+2mm gap = 102mm each, 2 fit in 210mm → 4 models in 1 bed
        beds_none = pack_models(models, 210, 210, {"adhesion_type": "none"})
        # With 20mm brim: 100+40+2mm = 142mm each, only 1 fits per row → needs more beds
        beds_brim = pack_models(models, 210, 210, {"adhesion_type": "brim", "brim_width": "20"})
        assert len(beds_brim) >= len(beds_none)

    def test_oversized_model_gets_own_bed(self, tmp_path):
        small = tmp_path / "small.stl"
        huge = tmp_path / "huge.stl"
        _make_box_stl(small, 50, 50, 10)
        _make_box_stl(huge, 300, 300, 10)  # larger than 235mm bed
        beds = pack_models([small, huge], 235, 235, {"adhesion_type": "none"})
        assert len(beds) == 2
        assert len(beds[0]) == 1  # small one packed normally
        # Oversized model gets its own bed at (0, 0)
        assert len(beds[1]) == 1
        assert beds[1][0][0] == huge
        assert beds[1][0][1] == 0.0
        assert beds[1][0][2] == 0.0

    def test_scale_xy_affects_packing(self, tmp_path):
        models = []
        for i in range(4):
            p = tmp_path / f"m{i}.stl"
            _make_box_stl(p, 50, 50, 10)
            models.append(p)
        settings = {"adhesion_type": "none"}
        # At 100% scale: 4 × 52mm fits on 120mm bed
        beds_normal = pack_models(models, 120, 120, settings)
        assert len(beds_normal) == 1
        # At 200% scale: 4 × 102mm won't fit on 120mm bed
        beds_scaled = pack_models(models, 120, 120, settings, scale_xy=(2.0, 2.0))
        assert len(beds_scaled) > 1
