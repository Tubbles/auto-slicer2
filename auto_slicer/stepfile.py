"""Convert STEP files to STL using gmsh's OpenCASCADE kernel.

STEP is BREP CAD geometry, not a mesh — gmsh tessellates it. Mesh size
follows surface curvature so curved faces come out smooth while flat
faces stay coarse. gmsh keeps global state in the C library, so
conversions are serialized with a module lock.
"""

import threading
from pathlib import Path

import gmsh

_GMSH_LOCK = threading.Lock()

# Mesh elements per full turn of curvature; 64 keeps the chord error
# around 0.01 mm on a 10 mm radius
CURVATURE_SEGMENTS = 64
MESH_SIZE_MIN_MM = 0.2
MESH_SIZE_MAX_MM = 10.0


def convert_step_to_stl(step_path: Path, stl_path: Path) -> None:
    """Tessellate a STEP (or STP) file into a binary STL, dimensions in mm."""
    with _GMSH_LOCK:
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", CURVATURE_SEGMENTS)
            gmsh.option.setNumber("Mesh.MeshSizeMin", MESH_SIZE_MIN_MM)
            gmsh.option.setNumber("Mesh.MeshSizeMax", MESH_SIZE_MAX_MM)
            gmsh.open(str(step_path))
            if not gmsh.model.getEntities(2):
                raise ValueError("No surfaces found in STEP file")
            gmsh.model.mesh.generate(2)
            gmsh.option.setNumber("Mesh.Binary", 1)
            gmsh.write(str(stl_path))
        finally:
            gmsh.finalize()
