"""Tests for STL scaling."""

import tempfile
from pathlib import Path

import numpy as np
from stl import mesh

from auto_slicer.stl_transform import needs_scaling, scale_stl


def _make_stl(path: Path) -> mesh.Mesh:
    """Create a minimal STL with one triangle and save it."""
    m = mesh.Mesh(np.zeros(1, dtype=mesh.Mesh.dtype))
    m.vectors[0] = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    m.save(str(path))
    return m


class TestNeedsScaling:
    def test_all_100_returns_false(self):
        assert needs_scaling(100.0, 100.0, 100.0) is False

    def test_x_different_returns_true(self):
        assert needs_scaling(150.0, 100.0, 100.0) is True

    def test_y_different_returns_true(self):
        assert needs_scaling(100.0, 50.0, 100.0) is True

    def test_z_different_returns_true(self):
        assert needs_scaling(100.0, 100.0, 200.0) is True

    def test_all_different_returns_true(self):
        assert needs_scaling(50.0, 75.0, 200.0) is True


class TestScaleStl:
    def test_uniform_scale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.stl"
            _make_stl(path)
            scale_stl(path, 200.0, 200.0, 200.0)
            m = mesh.Mesh.from_file(str(path))
            np.testing.assert_allclose(m.vectors[0][1], [2.0, 0.0, 0.0])
            np.testing.assert_allclose(m.vectors[0][2], [0.0, 2.0, 0.0])

    def test_nonuniform_scale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.stl"
            _make_stl(path)
            scale_stl(path, 200.0, 300.0, 100.0)
            m = mesh.Mesh.from_file(str(path))
            # vertex [1,0,0] -> [2,0,0]
            np.testing.assert_allclose(m.vectors[0][1], [2.0, 0.0, 0.0])
            # vertex [0,1,0] -> [0,3,0]
            np.testing.assert_allclose(m.vectors[0][2], [0.0, 3.0, 0.0])

    def test_identity_scale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.stl"
            _make_stl(path)
            scale_stl(path, 100.0, 100.0, 100.0)
            m = mesh.Mesh.from_file(str(path))
            np.testing.assert_allclose(m.vectors[0][1], [1.0, 0.0, 0.0])
            np.testing.assert_allclose(m.vectors[0][2], [0.0, 1.0, 0.0])

    def test_preserves_triangle_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.stl"
            _make_stl(path)
            scale_stl(path, 150.0, 150.0, 150.0)
            m = mesh.Mesh.from_file(str(path))
            assert len(m.vectors) == 1
