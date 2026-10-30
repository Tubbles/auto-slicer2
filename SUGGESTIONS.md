# Suggestions

Undecided ideas, one per line. From the GitHub slicing review of 2026-10-30 unless noted.

- A failed GitHub slice moves the model copy into the regular archive's `errors` folder (`slice_file`), which Syncthing shows on the printer; a GitHub errors folder would keep the two archives apart. `auto_slicer/github_jobs.py`.
- Identical slice requests are not merged, so a double tap runs CuraEngine twice and leaves two result folders. `auto_slicer/github_jobs.py`.
- The quality presets' `speed_print` never applies because every material preset sets it and the material merges later. Decide whether speed belongs to the material or the quality. `auto_slicer/matrix.py`.
- Thumbnails and slicing assume STL; a STEP or 3MF model in an artifact fails at thumbnail time. `auto_slicer/github_catalog.py`.
- Failed thumbnails are not cached, so an STL that always hits OpenSCAD's 30 s timeout holds the render lock for 30 s on every request. `auto_slicer/github_catalog.py`.
- The artifact cache is never pruned; the zip stays beside the extracted models. `auto_slicer/github_catalog.py`.
- With several repos, one repo failing fails the whole projects listing instead of being skipped, and there is no stale fallback when GitHub is down. `auto_slicer/github_catalog.py`.
- `sliced` and `slice` need GitHub reachable for the first rules fetch per commit, although the results are local. `auto_slicer/github_service.py`.
- Two models with the same stem in different artifact subfolders share one archive model folder. `auto_slicer/github_jobs.py`.
- Check rule setting keys against the settings registry. `auto_slicer/github_catalog.py`.
- Add slice stats (time, filament) to finished jobs, and an HTTP endpoint listing all jobs. `auto_slicer/github_jobs.py`, `auto_slicer/web_api.py`.
- Unused imports from before: `web_api.py` (`_resolve_rotation`, `needs_rotation`) and `handlers.py` (`_resolve_rotation`, `euler_to_rotation_matrix`, `needs_rotation`).
