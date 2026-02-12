import json
from pathlib import Path

from .defaults import PRESETS

BUILTIN_PRESETS = PRESETS


def load_presets(custom_presets_path: Path | None = None) -> dict[str, dict]:
    """Load built-in presets, optionally merging custom presets from a JSON file."""
    presets = dict(BUILTIN_PRESETS)
    if custom_presets_path and custom_presets_path.exists():
        with open(custom_presets_path) as f:
            presets.update(json.load(f))
    return presets
