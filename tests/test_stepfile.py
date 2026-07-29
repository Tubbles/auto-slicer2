"""Tests for STEP→STL conversion via gmsh."""

import math
import shutil
from pathlib import Path

import gmsh
import pytest
from stl import mesh

from auto_slicer.file_utils import convert_model_to_stl
from auto_slicer.stepfile import convert_step_to_stl


def _write_step(path: Path, build) -> None:
    """Create a STEP file from a gmsh OCC builder callback."""
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        build()
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()


def _make_step_box(path: Path, w: float, d: float, h: float) -> None:
    _write_step(path, lambda: gmsh.model.occ.addBox(0, 0, 0, w, d, h))


def _make_step_cylinder(path: Path, radius: float, height: float) -> None:
    _write_step(path, lambda: gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, height, radius))


class TestConvertStepToStl:
    def test_box_dimensions_and_volume(self, tmp_path):
        step = tmp_path / "box.step"
        _make_step_box(step, 20, 10, 5)
        stl = tmp_path / "box.stl"
        convert_step_to_stl(step, stl)
        m = mesh.Mesh.from_file(str(stl))
        low = m.vectors.min(axis=(0, 1))
        high = m.vectors.max(axis=(0, 1))
        assert abs((high - low)[0] - 20) < 0.01
        assert abs((high - low)[1] - 10) < 0.01
        assert abs((high - low)[2] - 5) < 0.01
        volume, _, _ = m.get_mass_properties()
        # Positive volume proves outward-facing orientation
        assert abs(float(volume) - 1000.0) < 1.0

    def test_cylinder_is_smooth(self, tmp_path):
        step = tmp_path / "cyl.step"
        _make_step_cylinder(step, radius=10, height=20)
        stl = tmp_path / "cyl.stl"
        convert_step_to_stl(step, stl)
        m = mesh.Mesh.from_file(str(stl))
        volume, _, _ = m.get_mass_properties()
        true_volume = math.pi * 10 * 10 * 20
        # Curvature-based meshing keeps the volume within 0.5% of the true value
        assert abs(float(volume) - true_volume) / true_volume < 0.005
        assert len(m.vectors) > 1000

    def test_stp_extension_alias(self, tmp_path):
        step = tmp_path / "box.step"
        _make_step_box(step, 5, 5, 5)
        stp = tmp_path / "box.stp"
        shutil.copy2(step, stp)
        stl = tmp_path / "box.stl"
        convert_step_to_stl(stp, stl)
        assert stl.exists()
        assert len(mesh.Mesh.from_file(str(stl)).vectors) > 0

    def test_invalid_step_raises(self, tmp_path):
        bad = tmp_path / "bad.step"
        bad.write_text("this is not a STEP file")
        with pytest.raises(Exception):
            convert_step_to_stl(bad, tmp_path / "out.stl")


class TestConvertModelToStl:
    def test_dispatches_step(self, tmp_path):
        step = tmp_path / "box.step"
        _make_step_box(step, 5, 5, 5)
        stl = tmp_path / "out.stl"
        convert_model_to_stl(step, stl)
        assert stl.exists()

    def test_unknown_extension_raises(self, tmp_path):
        with pytest.raises(ValueError):
            convert_model_to_stl(tmp_path / "model.obj", tmp_path / "out.stl")
