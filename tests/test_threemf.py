"""Tests for 3MF to STL conversion."""

import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest
from stl import mesh as stl_mesh

from auto_slicer.threemf import (
    _apply_transform, _extract_mesh, _find_model_file, _parse_transform,
    convert_3mf_to_stl,
)

# A simple triangle: three vertices forming a right triangle in the XY plane
SIMPLE_VERTS = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
SIMPLE_TRIS = [(0, 1, 2)]


def _model_xml(vertices, triangles, transform=None, obj_id="1"):
    """Build a minimal 3MF model XML string."""
    vert_lines = "\n".join(
        f'          <vertex x="{v[0]}" y="{v[1]}" z="{v[2]}" />'
        for v in vertices
    )
    tri_lines = "\n".join(
        f'          <triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}" />'
        for t in triangles
    )
    transform_attr = f' transform="{transform}"' if transform else ""
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="{obj_id}" type="model">
      <mesh>
        <vertices>
{vert_lines}
        </vertices>
        <triangles>
{tri_lines}
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="{obj_id}"{transform_attr} />
  </build>
</model>"""


def _write_3mf(path: Path, model_xml: str) -> None:
    """Write a minimal 3MF file (ZIP with model XML inside)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", model_xml)


class TestParseTransform:
    def test_identity_values(self):
        m = _parse_transform("1 0 0 0 1 0 0 0 1 0 0 0")
        np.testing.assert_array_almost_equal(m, np.eye(4))

    def test_translation(self):
        m = _parse_transform("1 0 0 0 1 0 0 0 1 10 20 30")
        assert m[3, 0] == 10
        assert m[3, 1] == 20
        assert m[3, 2] == 30

    def test_wrong_count_returns_identity(self):
        m = _parse_transform("1 0 0")
        np.testing.assert_array_equal(m, np.eye(4))


class TestApplyTransform:
    def test_identity(self):
        verts = np.array([[1.0, 2.0, 3.0]])
        result = _apply_transform(verts, np.eye(4))
        np.testing.assert_array_almost_equal(result, verts)

    def test_translation(self):
        verts = np.array([[0.0, 0.0, 0.0]])
        m = np.eye(4)
        m[3, :3] = [10, 20, 30]
        result = _apply_transform(verts, m)
        np.testing.assert_array_almost_equal(result, [[10, 20, 30]])

    def test_scale(self):
        verts = np.array([[1.0, 2.0, 3.0]])
        m = np.eye(4)
        m[0, 0] = 2
        m[1, 1] = 3
        m[2, 2] = 4
        result = _apply_transform(verts, m)
        np.testing.assert_array_almost_equal(result, [[2, 6, 12]])


class TestExtractMesh:
    def test_simple_mesh(self):
        import xml.etree.ElementTree as ET
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        xml = _model_xml(SIMPLE_VERTS, SIMPLE_TRIS)
        root = ET.fromstring(xml)
        obj = root.find(".//m:resources/m:object", ns)
        verts, tris = _extract_mesh(obj)
        assert len(verts) == 3
        assert len(tris) == 1
        assert list(tris[0]) == [0, 1, 2]

    def test_no_mesh_element(self):
        import xml.etree.ElementTree as ET
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model" />
  </resources>
</model>"""
        root = ET.fromstring(xml)
        obj = root.find(".//m:resources/m:object", ns)
        verts, tris = _extract_mesh(obj)
        assert len(verts) == 0
        assert len(tris) == 0


class TestConvert3mfToStl:
    def test_basic_conversion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            _write_3mf(threemf, _model_xml(SIMPLE_VERTS, SIMPLE_TRIS))

            convert_3mf_to_stl(threemf, stl_out)

            assert stl_out.exists()
            m = stl_mesh.Mesh.from_file(str(stl_out))
            assert len(m.vectors) == 1

    def test_vertex_values_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            _write_3mf(threemf, _model_xml(SIMPLE_VERTS, SIMPLE_TRIS))

            convert_3mf_to_stl(threemf, stl_out)

            m = stl_mesh.Mesh.from_file(str(stl_out))
            tri = m.vectors[0]
            np.testing.assert_array_almost_equal(tri[0], [0, 0, 0])
            np.testing.assert_array_almost_equal(tri[1], [1, 0, 0])
            np.testing.assert_array_almost_equal(tri[2], [0, 1, 0])

    def test_transform_applied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            # Translate by (10, 0, 0)
            transform = "1 0 0 0 1 0 0 0 1 10 0 0"
            _write_3mf(threemf, _model_xml(SIMPLE_VERTS, SIMPLE_TRIS, transform=transform))

            convert_3mf_to_stl(threemf, stl_out)

            m = stl_mesh.Mesh.from_file(str(stl_out))
            # First vertex (0,0,0) should now be at (10,0,0)
            np.testing.assert_array_almost_equal(m.vectors[0][0], [10, 0, 0])

    def test_multiple_objects(self):
        """A 3MF with two objects produces a combined STL."""
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0" />
          <vertex x="1" y="0" z="0" />
          <vertex x="0" y="1" z="0" />
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" />
        </triangles>
      </mesh>
    </object>
    <object id="2" type="model">
      <mesh>
        <vertices>
          <vertex x="5" y="5" z="5" />
          <vertex x="6" y="5" z="5" />
          <vertex x="5" y="6" z="5" />
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" />
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="1" />
    <item objectid="2" />
  </build>
</model>"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            stl_out = tmpdir / "model.stl"
            _write_3mf(threemf, xml)

            convert_3mf_to_stl(threemf, stl_out)

            m = stl_mesh.Mesh.from_file(str(stl_out))
            assert len(m.vectors) == 2

    def test_empty_mesh_raises(self):
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model" />
  </resources>
  <build>
    <item objectid="1" />
  </build>
</model>"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "model.3mf"
            _write_3mf(threemf, xml)

            with pytest.raises(ValueError, match="No mesh data"):
                convert_3mf_to_stl(threemf, tmpdir / "out.stl")

    def test_no_model_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            threemf = tmpdir / "bad.3mf"
            with zipfile.ZipFile(threemf, "w") as zf:
                zf.writestr("readme.txt", "not a model")

            with pytest.raises(ValueError, match="No .model file"):
                convert_3mf_to_stl(threemf, tmpdir / "out.stl")
