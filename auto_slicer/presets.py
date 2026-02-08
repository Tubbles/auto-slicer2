import json
from pathlib import Path


BUILTIN_PRESETS: dict[str, dict] = {
    "draft": {
        "description": "Fast printing, lower quality",
        "settings": {
            "layer_height": "0.3",
            "infill_sparse_density": "10",
            "wall_line_count": "2",
            "top_layers": "3",
            "bottom_layers": "3",
            "speed_print": "80",
        },
    },
    "standard": {
        "description": "Balanced quality and speed",
        "settings": {
            "layer_height": "0.2",
            "infill_sparse_density": "20",
            "wall_line_count": "3",
            "top_layers": "4",
            "bottom_layers": "4",
            "speed_print": "60",
        },
    },
    "fine": {
        "description": "High quality, slower printing",
        "settings": {
            "layer_height": "0.12",
            "infill_sparse_density": "20",
            "wall_line_count": "3",
            "top_layers": "5",
            "bottom_layers": "5",
            "speed_print": "40",
        },
    },
    "strong": {
        "description": "Maximum strength for functional parts",
        "settings": {
            "layer_height": "0.2",
            "infill_sparse_density": "60",
            "wall_line_count": "4",
            "top_layers": "6",
            "bottom_layers": "6",
            "speed_print": "50",
        },
    },
}


class PresetManager:
    def __init__(self, custom_presets_path: Path | None = None):
        self._presets = dict(BUILTIN_PRESETS)
        if custom_presets_path and custom_presets_path.exists():
            with open(custom_presets_path) as f:
                custom = json.load(f)
            self._presets.update(custom)

    def list_presets(self) -> dict[str, dict]:
        return self._presets

    def get(self, name: str) -> dict | None:
        return self._presets.get(name.lower())

    def names(self) -> list[str]:
        return list(self._presets.keys())
