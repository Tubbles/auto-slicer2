"""Convert 3MF files to STL format.

3MF is a ZIP archive containing XML mesh data. We extract vertices and
triangles, apply any item transforms, and write a combined STL using numpy-stl.
No extra dependencies beyond numpy-stl (already required for scaling).
"""

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from stl import mesh as stl_mesh

_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def _parse_transform(text: str) -> np.ndarray:
    """Parse a 3MF 12-value transform string into a 4x4 matrix.

    3MF uses row-vector convention (p' = p · M). The 12 values are:
    m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32
    where (m30, m31, m32) is the translation.
    """
    vals = [float(v) for v in text.split()]
    if len(vals) != 12:
        return np.eye(4)
    m = np.eye(4)
    m[0, :3] = vals[0:3]
    m[1, :3] = vals[3:6]
    m[2, :3] = vals[6:9]
    m[3, :3] = vals[9:12]
    return m


def _apply_transform(vertices: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Apply a 4x4 affine transform to an Nx3 vertex array (row-vector convention)."""
    ones = np.ones((len(vertices), 1))
    homo = np.hstack([vertices, ones])
    return (homo @ matrix)[:, :3]


def _extract_mesh(obj_elem: ET.Element) -> tuple[np.ndarray, np.ndarray]:
    """Extract (vertices, triangle_indices) from a 3MF <object> element."""
    mesh_elem = obj_elem.find("m:mesh", _NS)
    if mesh_elem is None:
        return np.empty((0, 3)), np.empty((0, 3), dtype=int)

    verts = np.array([
        (float(v.get("x")), float(v.get("y")), float(v.get("z")))
        for v in mesh_elem.findall("m:vertices/m:vertex", _NS)
    ])
    tris = np.array([
        (int(t.get("v1")), int(t.get("v2")), int(t.get("v3")))
        for t in mesh_elem.findall("m:triangles/m:triangle", _NS)
    ], dtype=int)
    return verts, tris


def _find_model_file(zf: zipfile.ZipFile) -> str:
    """Find the .model file path inside a 3MF archive."""
    for name in zf.namelist():
        if name.lower().endswith(".model"):
            return name
    raise ValueError("No .model file found in 3MF archive")


def convert_3mf_to_stl(threemf_path: Path, stl_path: Path) -> None:
    """Convert a 3MF file to binary STL.

    Extracts all mesh objects, applies build-item transforms, and writes
    a single combined STL file.
    """
    with zipfile.ZipFile(threemf_path) as zf:
        model_path = _find_model_file(zf)
        root = ET.fromstring(zf.read(model_path))

    objects = {}
    for obj in root.findall(".//m:resources/m:object", _NS):
        objects[obj.get("id")] = obj

    all_triangles = []
    build_items = root.findall(".//m:build/m:item", _NS)

    if build_items:
        for item in build_items:
            obj = objects.get(item.get("objectid"))
            if obj is None:
                continue
            verts, tris = _extract_mesh(obj)
            if len(tris) == 0:
                continue
            transform_str = item.get("transform")
            if transform_str:
                verts = _apply_transform(verts, _parse_transform(transform_str))
            all_triangles.append(verts[tris])
    else:
        for obj in objects.values():
            verts, tris = _extract_mesh(obj)
            if len(tris) == 0:
                continue
            all_triangles.append(verts[tris])

    if not all_triangles:
        raise ValueError("No mesh data found in 3MF file")

    combined = np.concatenate(all_triangles)
    out = stl_mesh.Mesh(np.zeros(len(combined), dtype=stl_mesh.Mesh.dtype))
    out.vectors = combined
    out.save(str(stl_path))
