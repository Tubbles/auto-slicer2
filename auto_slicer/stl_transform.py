"""STL scaling via numpy-stl. Applied before slicing, then stripped from CuraEngine flags."""

from stl import mesh


def needs_scaling(sx: float, sy: float, sz: float) -> bool:
    """Return True if any axis scale factor differs from 100%."""
    return sx != 100.0 or sy != 100.0 or sz != 100.0


def scale_stl(stl_path, sx: float, sy: float, sz: float) -> None:
    """Scale an STL file in place by per-axis percentages."""
    m = mesh.Mesh.from_file(str(stl_path))
    m.vectors *= [sx / 100, sy / 100, sz / 100]
    m.save(str(stl_path))
