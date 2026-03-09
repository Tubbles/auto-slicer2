"""Bin-pack model convex hulls onto print beds using pynest2d (libnest2d).

Computes 2D convex hulls from STL XY projections, then uses NFP-based
nesting for tight packing. Returns per-bed lists of (path, offset_x, offset_y).

Offsets are relative to bed center (CuraEngine convention with center_object=true).
"""

from pathlib import Path

from pynest2d import Box, Item, NfpConfig, Point, nest
from stl import mesh


# Minimum gap (mm) between models beyond adhesion margin
MODEL_GAP = 2.0

# pynest2d uses integers internally; we scale mm to micrometers
SCALE = 1000


def _cross(o: tuple, a: tuple, b: tuple) -> float:
    """Cross product of vectors OA and OB."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull_2d(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain convex hull algorithm (CCW order)."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def get_xy_bounds(stl_path: Path) -> tuple[float, float]:
    """Return (width_mm, depth_mm) of an STL's XY bounding box."""
    m = mesh.Mesh.from_file(str(stl_path))
    min_x = m.vectors[:, :, 0].min()
    max_x = m.vectors[:, :, 0].max()
    min_y = m.vectors[:, :, 1].min()
    max_y = m.vectors[:, :, 1].max()
    return float(max_x - min_x), float(max_y - min_y)


def get_xy_hull(stl_path: Path) -> list[tuple[float, float]]:
    """Return the 2D convex hull of an STL's XY projection, centered at origin."""
    m = mesh.Mesh.from_file(str(stl_path))
    xs = m.vectors[:, :, 0].flatten()
    ys = m.vectors[:, :, 1].flatten()
    raw = [(float(x), float(y)) for x, y in zip(xs, ys)]
    hull = convex_hull_2d(raw)
    # Center at origin
    hx = [p[0] for p in hull]
    hy = [p[1] for p in hull]
    cx = (min(hx) + max(hx)) / 2
    cy = (min(hy) + max(hy)) / 2
    return [(x - cx, y - cy) for x, y in hull]


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


def pack_models(
    stl_paths: list[Path],
    bed_width: float,
    bed_depth: float,
    settings: dict[str, str],
) -> list[list[tuple[Path, float, float]]]:
    """Pack models into bed-sized bins using convex hull nesting.

    Returns a list of beds, each containing [(stl_path, offset_x, offset_y), ...].
    Models that cannot be packed get their own bed at (0, 0) so they remain visible.
    Offsets are relative to bed center (for use with center_object=true + mesh_position_x/y).
    """
    margin = adhesion_margin(settings) + MODEL_GAP

    # Build pynest2d items from convex hulls
    hulls = []
    nest_items = []
    for p in stl_paths:
        hull = get_xy_hull(p)
        points = [Point(int(x * SCALE), int(y * SCALE)) for x, y in hull]
        nest_items.append(Item(points))
        hulls.append(p)

    # Configure nesting: no rotation, center alignment
    cfg = NfpConfig()
    cfg.rotations = [0.0]
    cfg.alignment = NfpConfig.Alignment.CENTER
    cfg.starting_point = NfpConfig.Alignment.CENTER

    # Shrink bin by margin on each side for edge clearance
    bin_w = int((bed_width - margin) * SCALE)
    bin_d = int((bed_depth - margin) * SCALE)
    bed_box = Box(bin_w, bin_d)

    # Inter-item distance = margin (adhesion + gap)
    distance = int(margin * SCALE)

    nest(nest_items, bed_box, distance, cfg)

    # Collect placed models per bin
    bins: dict[int, list[tuple[Path, float, float]]] = {}
    unplaced = []
    for i, item in enumerate(nest_items):
        bid = item.binId()
        if bid < 0:
            unplaced.append(hulls[i])
            continue
        tr = item.translation()
        offset_x = tr.x() / SCALE
        offset_y = tr.y() / SCALE
        bins.setdefault(bid, []).append((hulls[i], offset_x, offset_y))

    # Give each unplaced model its own bed at (0, 0) so it's still visible
    next_bin = (max(bins) + 1) if bins else 0
    for p in unplaced:
        bins[next_bin] = [(p, 0.0, 0.0)]
        next_bin += 1

    return [bins[k] for k in sorted(bins)]
