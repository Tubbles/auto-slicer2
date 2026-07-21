"""Tests for STL scaling and rotation."""

import math
import tempfile
from pathlib import Path

import numpy as np
from stl import mesh

from auto_slicer.stl_transform import (
    euler_to_rotation_matrix, needs_rotation, needs_scaling, scale_stl,
)


def _make_stl(path: Path) -> mesh.Mesh:
    """Create a minimal STL with one triangle and save it."""
    m = mesh.Mesh(np.zeros(1, dtype=mesh.Mesh.dtype))
    m.vectors[0] = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    m.save(str(path))
    return m


def _make_tetra_stl(path: Path, apex_x: float = 1.0) -> None:
    """Closed tetrahedron with outward-facing winding (positive volume)."""
    a, b, c, d = [0, 0, 0], [apex_x, 0, 0], [0, 1, 0], [0, 0, 1]
    faces = [[a, c, b], [a, b, d], [a, d, c], [b, c, d]]
    m = mesh.Mesh(np.zeros(4, dtype=mesh.Mesh.dtype))
    for i, f in enumerate(faces):
        m.vectors[i] = f
    m.save(str(path))


def _volume(path: Path) -> float:
    """Signed volume — negative means the winding is inside-out."""
    volume, _, _ = mesh.Mesh.from_file(str(path)).get_mass_properties()
    return float(volume)


def _bbox(path: Path) -> tuple[np.ndarray, np.ndarray]:
    m = mesh.Mesh.from_file(str(path))
    return m.vectors.min(axis=(0, 1)), m.vectors.max(axis=(0, 1))


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


class TestMirrorScale:
    def test_negative_scale_needs_scaling(self):
        assert needs_scaling(-100.0, 100.0, 100.0) is True

    def test_mirror_keeps_winding_outward(self, tmp_path):
        stl = tmp_path / "tetra.stl"
        _make_tetra_stl(stl)
        original_volume = _volume(stl)
        assert original_volume > 0  # sanity: fixture is wound outward
        scale_stl(stl, -100.0, 100.0, 100.0)
        assert abs(_volume(stl) - original_volume) < 1e-9

    def test_double_mirror_keeps_winding_outward(self, tmp_path):
        stl = tmp_path / "tetra.stl"
        _make_tetra_stl(stl)
        original_volume = _volume(stl)
        scale_stl(stl, -100.0, -100.0, 100.0)
        assert abs(_volume(stl) - original_volume) < 1e-9

    def test_mirror_x_reflects_about_bbox_center(self, tmp_path):
        stl = tmp_path / "tetra.stl"
        _make_tetra_stl(stl, apex_x=4.0)
        scale_stl(stl, -100.0, 100.0, 100.0)
        # Bbox is unchanged (mirror in place)...
        low, high = _bbox(stl)
        np.testing.assert_allclose(low, [0.0, 0.0, 0.0], atol=1e-9)
        np.testing.assert_allclose(high, [4.0, 1.0, 1.0], atol=1e-9)
        # ...but the geometry flipped: 3 of 4 unique vertices sat at x=0,
        # after mirroring they sit at x=4
        m = mesh.Mesh.from_file(str(stl))
        xs = m.vectors[:, :, 0].flatten()
        assert (xs > 3.9).sum() > (xs < 0.1).sum()

    def test_mirror_z_keeps_bottom_on_bed(self, tmp_path):
        stl = tmp_path / "tetra.stl"
        _make_tetra_stl(stl)
        scale_stl(stl, 100.0, 100.0, -100.0)
        low, high = _bbox(stl)
        assert abs(low[2]) < 1e-9  # z-min stays at 0
        assert abs(high[2] - 1.0) < 1e-9
        assert _volume(stl) > 0

    def test_mirror_with_resize_keeps_center_and_bottom(self, tmp_path):
        stl = tmp_path / "tetra.stl"
        _make_tetra_stl(stl, apex_x=4.0)  # x in [0, 4], center 2
        scale_stl(stl, -200.0, 100.0, 100.0)
        low, high = _bbox(stl)
        # Width doubled to 8, mirrored about the old center x=2
        assert abs(low[0] - (-2.0)) < 1e-9
        assert abs(high[0] - 6.0) < 1e-9
        assert abs(low[2]) < 1e-9  # bottom unchanged


class TestNeedsRotation:
    def test_all_zero_returns_false(self):
        assert needs_rotation(0.0, 0.0, 0.0) is False

    def test_x_nonzero_returns_true(self):
        assert needs_rotation(45.0, 0.0, 0.0) is True

    def test_y_nonzero_returns_true(self):
        assert needs_rotation(0.0, 90.0, 0.0) is True

    def test_z_nonzero_returns_true(self):
        assert needs_rotation(0.0, 0.0, 30.0) is True

    def test_all_nonzero_returns_true(self):
        assert needs_rotation(10.0, 20.0, 30.0) is True


class TestEulerToRotationMatrix:
    def _parse_matrix(self, s: str) -> list[list[float]]:
        """Parse CuraEngine matrix string into 3x3 list."""
        import ast
        return ast.literal_eval(s)

    def test_identity(self):
        result = euler_to_rotation_matrix(0, 0, 0)
        m = self._parse_matrix(result)
        expected = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        for r in range(3):
            for c in range(3):
                assert abs(m[r][c] - expected[r][c]) < 1e-6

    def test_90_deg_z_rotation(self):
        result = euler_to_rotation_matrix(0, 0, 90)
        m = self._parse_matrix(result)
        # Rz(90): x -> y, y -> -x
        assert abs(m[0][0] - 0) < 1e-6  # cos(90) = 0
        assert abs(m[0][1] - (-1)) < 1e-6  # -sin(90)
        assert abs(m[1][0] - 1) < 1e-6  # sin(90)
        assert abs(m[1][1] - 0) < 1e-6

    def test_90_deg_x_rotation(self):
        result = euler_to_rotation_matrix(90, 0, 0)
        m = self._parse_matrix(result)
        # Rx(90): y -> z, z -> -y
        assert abs(m[1][1] - 0) < 1e-6
        assert abs(m[1][2] - (-1)) < 1e-6
        assert abs(m[2][1] - 1) < 1e-6
        assert abs(m[2][2] - 0) < 1e-6

    def test_90_deg_y_rotation(self):
        result = euler_to_rotation_matrix(0, 90, 0)
        m = self._parse_matrix(result)
        # Ry(90): x -> -z, z -> x
        assert abs(m[0][0] - 0) < 1e-6
        assert abs(m[0][2] - 1) < 1e-6
        assert abs(m[2][0] - (-1)) < 1e-6
        assert abs(m[2][2] - 0) < 1e-6

    def test_format_is_parseable(self):
        result = euler_to_rotation_matrix(30, 45, 60)
        m = self._parse_matrix(result)
        assert len(m) == 3
        for row in m:
            assert len(row) == 3

    def test_combined_rotation_orthogonal(self):
        """Result matrix should be orthogonal (R^T * R = I)."""
        result = euler_to_rotation_matrix(30, 45, 60)
        m = self._parse_matrix(result)
        # Check R^T * R = I
        for i in range(3):
            for j in range(3):
                dot = sum(m[k][i] * m[k][j] for k in range(3))
                expected = 1.0 if i == j else 0.0
                assert abs(dot - expected) < 1e-6
