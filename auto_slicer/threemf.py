"""Convert 3MF files to STL format using lib3mf.

lib3mf handles all 3MF complexities (components, transforms, multiple objects)
and we write the combined mesh to binary STL via numpy-stl.
"""

from pathlib import Path

import lib3mf
import numpy as np
from stl import mesh as stl_mesh


def convert_3mf_to_stl(threemf_path: Path, stl_path: Path) -> None:
    """Convert a 3MF file to binary STL.

    Uses lib3mf to read the 3MF (resolving components and transforms),
    then writes a single combined STL file via numpy-stl.
    """
    wrapper = lib3mf.Wrapper()
    model = wrapper.CreateModel()
    reader = model.QueryReader("3mf")
    reader.ReadFromFile(str(threemf_path))

    all_triangles = []
    it = model.GetMeshObjects()
    while it.MoveNext():
        mesh = it.GetCurrentMeshObject()
        verts = mesh.GetVertices()
        tris = mesh.GetTriangleIndices()
        if not tris:
            continue

        vert_array = np.array([list(v.Coordinates) for v in verts])
        for tri in tris:
            idx = list(tri.Indices)
            all_triangles.append(vert_array[idx])

    if not all_triangles:
        raise ValueError("No mesh data found in 3MF file")

    combined = np.array(all_triangles)
    out = stl_mesh.Mesh(np.zeros(len(combined), dtype=stl_mesh.Mesh.dtype))
    out.vectors = combined
    out.save(str(stl_path))
