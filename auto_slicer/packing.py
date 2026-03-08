"""Bin-pack model bounding boxes onto print beds using rectpack.

Computes XY bounding boxes from STL files, adds adhesion margin,
and packs into bed-sized bins. Returns per-bed lists of (path, offset_x, offset_y).

Offsets are relative to bed center (CuraEngine convention with center_object=true).
"""

from pathlib import Path

import rectpack
from stl import mesh


def get_xy_bounds(stl_path: Path) -> tuple[float, float]:
    """Return (width_mm, depth_mm) of an STL's XY bounding box."""
    m = mesh.Mesh.from_file(str(stl_path))
    min_x = m.vectors[:, :, 0].min()
    max_x = m.vectors[:, :, 0].max()
    min_y = m.vectors[:, :, 1].min()
    max_y = m.vectors[:, :, 1].max()
    return float(max_x - min_x), float(max_y - min_y)


def adhesion_margin(settings: dict[str, str]) -> float:
    """Return the extra XY margin (mm) caused by bed adhesion type."""
    adhesion = settings.get("adhesion_type", "skirt")
    if adhesion == "raft":
        return float(settings.get("raft_margin", 15.0))
    if adhesion == "brim":
        return float(settings.get("brim_width", 8.0))
    # skirt or none — skirt doesn't physically occupy space between models,
    # but we add skirt_distance as buffer to avoid overlap with skirt lines
    if adhesion == "skirt":
        return float(settings.get("skirt_distance", 3.0))
    return 0.0


# Minimum gap (mm) between models beyond adhesion margin
MODEL_GAP = 2.0

# rectpack works with integers; we scale mm to 0.1mm resolution
SCALE = 10


def pack_models(
    stl_paths: list[Path],
    bed_width: float,
    bed_depth: float,
    settings: dict[str, str],
) -> tuple[list[list[tuple[Path, float, float]]], list[tuple[Path, str]]]:
    """Pack models into bed-sized bins.

    Returns (beds, rejected) where:
    - beds: list of beds, each containing [(stl_path, offset_x, offset_y), ...]
    - rejected: list of (stl_path, reason) for models that couldn't be packed

    Offsets are relative to bed center (for use with center_object=true + mesh_position_x/y).
    """
    margin = adhesion_margin(settings) + MODEL_GAP

    # Compute padded sizes
    items = []
    for p in stl_paths:
        w, d = get_xy_bounds(p)
        pw = int((w + margin) * SCALE)
        pd = int((d + margin) * SCALE)
        items.append((p, pw, pd, w, d))

    bw = int(bed_width * SCALE)
    bd = int(bed_depth * SCALE)

    packer = rectpack.newPacker(rotation=False)
    for i, (_, pw, pd, _, _) in enumerate(items):
        packer.add_rect(pw, pd, rid=i)

    # Add enough bins for all models (worst case: one per model)
    for _ in range(len(items)):
        packer.add_bin(bw, bd)

    packer.pack()

    # Collect results per bin
    packed_rids = set()
    bins: dict[int, list[tuple[Path, float, float]]] = {}
    for bin_idx, x, y, pw, pd, rid in packer.rect_list():
        packed_rids.add(rid)
        path, _, _, orig_w, orig_d = items[rid]
        # rectpack places at (x, y) from bin corner (0,0)
        # Model center in bin coords: x + pw/2, y + pd/2
        center_x = (x + pw / 2) / SCALE
        center_y = (y + pd / 2) / SCALE
        # Convert to bed-center-relative offset
        offset_x = center_x - bed_width / 2
        offset_y = center_y - bed_depth / 2
        bins.setdefault(bin_idx, []).append((path, offset_x, offset_y))

    # Center each bin's model group on the bed
    for bin_idx in bins:
        entries = bins[bin_idx]
        xs = [ox for _, ox, _ in entries]
        ys = [oy for _, _, oy in entries]
        shift_x = (min(xs) + max(xs)) / 2
        shift_y = (min(ys) + max(ys)) / 2
        bins[bin_idx] = [(p, ox - shift_x, oy - shift_y) for p, ox, oy in entries]

    # Identify rejected models and determine reason
    rejected = []
    for i, (p, pw, pd, w, d) in enumerate(items):
        if i not in packed_rids:
            if pw > bw or pd > bd:
                rejected.append((p, "too large for bed"))
            else:
                rejected.append((p, "could not fit"))

    beds = [bins[k] for k in sorted(bins)]
    return beds, rejected
