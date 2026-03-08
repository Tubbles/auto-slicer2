# auto-slicer2

Telegram bot that slices STL/3MF files using CuraEngine. Users send model files to the bot or upload via the Mini App, configure slicer settings, and receive notifications when slicing completes.

## Future Improvements

### Convex hull packing for multi-model bed layout

The current multi-model batch layout uses axis-aligned bounding box packing (`rectpack`). This can waste significant bed space for L-shaped, elongated, or irregular models. A better approach would project each model's XY footprint to a 2D convex hull (via `scipy.spatial.ConvexHull` or `trimesh`) and use a polygon nesting library like `pynest2d` (Ultimaker's libnest2d bindings — what Cura itself uses for auto-arrange) to pack the hulls more tightly. This would require a C++ dependency (`libnest2d`) but would substantially improve bed utilization.
