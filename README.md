# auto-slicer2

Telegram bot that slices STL/3MF files using CuraEngine. Users send model files to the bot or upload via the Mini App, configure slicer settings, and receive notifications when slicing completes.

## Mini App preview updates

The Upload tab preview always reflects applied settings, so it matches exactly what Slice will produce. Editing a setting does not touch the preview; tapping Apply refreshes it once when the change affects the model transforms or bed layout (Scale, Rotation, Repeat, Batch Models).

Slicing always uses applied settings. Pending (unapplied) changes are never part of a slice job, so tap Apply before Slice.

Rotation is ignored in the packed bed layout and in batch/repeat slicing, since the layout is computed from unrotated model outlines.
