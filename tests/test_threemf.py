"""Tests for 3MF to STL conversion."""

import tempfile
from pathlib import Path

import lib3mf
import pytest
from stl import mesh as stl_mesh

from auto_slicer.threemf import convert_3mf_to_stl


def _pos(x, y, z):
    p = lib3mf.Position()
    p.Coordinates[0] = float(x)
    p.Coordinates[1] = float(y)
    p.Coordinates[2] = float(z)
    return p


def _tri(v1, v2, v3):
    t = lib3mf.Triangle()
    t.Indices[0] = v1
    t.Indices[1] = v2
    t.Indices[2] = v3
    return t


def _write_simple_3mf(path: Path) -> None:
    """Write a 3MF with one triangle (right triangle in XY plane)."""
    wrapper = lib3mf.Wrapper()
    model = wrapper.CreateModel()
    mesh = model.AddMeshObject()
    mesh.AddVertex(_pos(0, 0, 0))
    mesh.AddVertex(_pos(1, 0, 0))
    mesh.AddVertex(_pos(0, 1, 0))
    mesh.AddTriangle(_tri(0, 1, 2))
    model.AddBuildItem(mesh, wrapper.GetIdentityTransform())
    model.QueryWriter("3mf").WriteToFile(str(path))


class TestConvert3mfToStl:
    def test_basic_conversion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            _write_simple_3mf(threemf)

            convert_3mf_to_stl(threemf, stl_out)

            assert stl_out.exists()
            m = stl_mesh.Mesh.from_file(str(stl_out))
            assert len(m.vectors) == 1

    def test_vertex_values_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            _write_simple_3mf(threemf)

            convert_3mf_to_stl(threemf, stl_out)

            m = stl_mesh.Mesh.from_file(str(stl_out))
            tri = m.vectors[0]
            xs = sorted(v[0] for v in tri)
            ys = sorted(v[1] for v in tri)
            assert xs[0] == pytest.approx(0, abs=1e-5)
            assert xs[-1] == pytest.approx(1, abs=1e-5)
            assert ys[0] == pytest.approx(0, abs=1e-5)
            assert ys[-1] == pytest.approx(1, abs=1e-5)

    def test_build_transform_applied(self):
        """Build-item transforms (orientation) are applied in the output STL."""
        wrapper = lib3mf.Wrapper()
        model = wrapper.CreateModel()
        mesh = model.AddMeshObject()
        # Triangle at z=0
        mesh.AddVertex(_pos(0, 0, 0))
        mesh.AddVertex(_pos(1, 0, 0))
        mesh.AddVertex(_pos(0, 1, 0))
        mesh.AddTriangle(_tri(0, 1, 2))

        # Translate by (10, 0, 0)
        t = wrapper.GetIdentityTransform()
        t.Fields[3][0] = 10.0
        model.AddBuildItem(mesh, t)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            model.QueryWriter("3mf").WriteToFile(str(threemf))

            convert_3mf_to_stl(threemf, stl_out)

            m = stl_mesh.Mesh.from_file(str(stl_out))
            min_x = m.vectors[:, :, 0].min()
            assert min_x == pytest.approx(10.0, abs=1e-5)

    def test_multiple_objects(self):
        """A 3MF with two mesh objects produces a combined STL."""
        wrapper = lib3mf.Wrapper()
        model = wrapper.CreateModel()

        mesh1 = model.AddMeshObject()
        mesh1.AddVertex(_pos(0, 0, 0))
        mesh1.AddVertex(_pos(1, 0, 0))
        mesh1.AddVertex(_pos(0, 1, 0))
        mesh1.AddTriangle(_tri(0, 1, 2))

        mesh2 = model.AddMeshObject()
        mesh2.AddVertex(_pos(5, 5, 5))
        mesh2.AddVertex(_pos(6, 5, 5))
        mesh2.AddVertex(_pos(5, 6, 5))
        mesh2.AddTriangle(_tri(0, 1, 2))

        identity = wrapper.GetIdentityTransform()
        model.AddBuildItem(mesh1, identity)
        model.AddBuildItem(mesh2, identity)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            model.QueryWriter("3mf").WriteToFile(str(threemf))

            convert_3mf_to_stl(threemf, stl_out)

            m = stl_mesh.Mesh.from_file(str(stl_out))
            assert len(m.vectors) == 2

    def test_component_reference(self):
        """A build item referencing a component object resolves the mesh."""
        wrapper = lib3mf.Wrapper()
        model = wrapper.CreateModel()

        mesh = model.AddMeshObject()
        mesh.AddVertex(_pos(0, 0, 0))
        mesh.AddVertex(_pos(1, 0, 0))
        mesh.AddVertex(_pos(0, 1, 0))
        mesh.AddTriangle(_tri(0, 1, 2))

        comp = model.AddComponentsObject()
        comp.AddComponent(mesh, wrapper.GetIdentityTransform())
        model.AddBuildItem(comp, wrapper.GetIdentityTransform())

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            model.QueryWriter("3mf").WriteToFile(str(threemf))

            convert_3mf_to_stl(threemf, stl_out)

            m = stl_mesh.Mesh.from_file(str(stl_out))
            assert len(m.vectors) == 1

    def test_empty_mesh_raises(self):
        wrapper = lib3mf.Wrapper()
        model = wrapper.CreateModel()
        mesh = model.AddMeshObject()
        model.AddBuildItem(mesh, wrapper.GetIdentityTransform())

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            model.QueryWriter("3mf").WriteToFile(str(threemf))

            with pytest.raises(ValueError, match="No mesh data"):
                convert_3mf_to_stl(threemf, tmpdir / "out.stl")

    def test_invalid_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            bad_file = tmpdir / "bad.3mf"
            bad_file.write_bytes(b"not a zip")

            with pytest.raises(Exception):
                convert_3mf_to_stl(bad_file, tmpdir / "out.stl")
