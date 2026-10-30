# auto-slicer2

Telegram bot that slices STL/3MF/STEP files using CuraEngine. Users send model files to the bot or upload via the Mini App, configure slicer settings, and receive notifications when slicing completes.

## Mini App preview updates

The Upload tab preview always reflects applied settings, so it matches exactly what Slice will produce. Editing a setting does not touch the preview; tapping Apply refreshes it once when the change affects the model transforms or bed layout (Scale, Rotation, Repeat, Batch Models).

Slicing always uses applied settings. Pending (unapplied) changes are never part of a slice job, so tap Apply before Slice.

Rotation is ignored in the packed bed layout and in batch/repeat slicing, since the layout is computed from unrotated model outlines.

## GitHub slicing

The bot can slice the STLs that a repo's CI publishes as GitHub Actions artifacts, taken from the latest successful run on the default branch. Pick a model and a combination of material (PLA, PETG, TPU), support (none, supports, vase), adhesion (skirt, brim) and quality (draft, standard, fine, strong), or one of the repo's own rules from `.autoslice.json`. Results go to the GitHub archive directory as `<artifact>/<model>/<choice>_<timestamp>/`, so a combination sliced once can be printed again without slicing. KlipperScreen reaches it through Moonraker (agent `autoslice`), the Mini App through `/api/github`. Configure `[GITHUB]`, `[PATHS] github_archive_directory` and `[MOONRAKER]` in config.ini (see config.template.ini).
