"""Convert 3MF files to STL format using lib3mf.

lib3mf handles all 3MF complexities (components, build-item transforms,
multiple objects) and its STL writer applies transforms automatically.
"""

from pathlib import Path

import lib3mf


def convert_3mf_to_stl(threemf_path: Path, stl_path: Path) -> None:
    """Convert a 3MF file to STL, preserving the build-item orientation.

    lib3mf's STL writer applies build-item transforms (rotation, translation)
    so the model comes out in the correct print orientation.
    """
    wrapper = lib3mf.Wrapper()
    model = wrapper.CreateModel()
    reader = model.QueryReader("3mf")
    reader.ReadFromFile(str(threemf_path))

    has_triangles = False
    it = model.GetMeshObjects()
    while it.MoveNext():
        if it.GetCurrentMeshObject().GetTriangleCount() > 0:
            has_triangles = True
            break
    if not has_triangles:
        raise ValueError("No mesh data found in 3MF file")

    model.QueryWriter("stl").WriteToFile(str(stl_path))
