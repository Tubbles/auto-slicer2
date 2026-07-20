# auto-slicer2

Telegram bot that slices STL/3MF files using CuraEngine. Users send model files to the bot or upload via the Mini App, configure slicer settings, and receive notifications when slicing completes.

## Mini App live updates

The Upload tab preview reacts immediately to changes in the Model settings category, even before they are applied: scale and rotation update the 3D mesh client-side, and changing Scale, Repeat, or Batch Models recomputes the bed layout right away (the pending values are sent along with the layout request). All other settings, such as adhesion type or machine bed size, affect the layout preview only after they are applied.

Slicing always uses applied settings. Pending (unapplied) changes are never part of a slice job, so tap Apply before Slice.

Rotation is ignored in the packed bed layout and in batch/repeat slicing, since the layout is computed from unrotated model outlines.
