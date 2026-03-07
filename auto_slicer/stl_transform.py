"""STL scaling via numpy-stl. Applied before slicing, then stripped from CuraEngine flags."""

import math

from stl import mesh


def needs_scaling(sx: float, sy: float, sz: float) -> bool:
    """Return True if any axis scale factor differs from 100%."""
    return sx != 100.0 or sy != 100.0 or sz != 100.0


def scale_stl(stl_path, sx: float, sy: float, sz: float) -> None:
    """Scale an STL file in place by per-axis percentages."""
    m = mesh.Mesh.from_file(str(stl_path))
    m.vectors *= [sx / 100, sy / 100, sz / 100]
    m.save(str(stl_path))


def needs_rotation(rx: float, ry: float, rz: float) -> bool:
    """Return True if any rotation angle is nonzero."""
    return rx != 0.0 or ry != 0.0 or rz != 0.0


def euler_to_rotation_matrix(rx_deg: float, ry_deg: float, rz_deg: float) -> str:
    """Convert Euler angles (degrees) to CuraEngine mesh_rotation_matrix string.

    Rotation order: Rz * Ry * Rx (extrinsic / fixed-axis convention).
    Returns the 3x3 matrix as "[[r00,r01,r02], [r10,r11,r12], [r20,r21,r22]]".
    """
    rx = math.radians(rx_deg)
    ry = math.radians(ry_deg)
    rz = math.radians(rz_deg)

    cx, sx_ = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    m = [
        [cz * cy, cz * sy * sx_ - sz * cx, cz * sy * cx + sz * sx_],
        [sz * cy, sz * sy * sx_ + cz * cx, sz * sy * cx - cz * sx_],
        [-sy,     cy * sx_,                cy * cx],
    ]

    def fmt(v: float) -> str:
        return f"{v:.6g}"

    rows = [f"[{fmt(m[r][0])},{fmt(m[r][1])},{fmt(m[r][2])}]" for r in range(3)]
    return f"[{', '.join(rows)}]"
