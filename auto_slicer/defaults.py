"""Checked-in printer defaults, bounds overrides, and presets.

config.ini keeps only machine-specific paths and secrets.
Any [DEFAULT_SETTINGS] or [BOUNDS_OVERRIDES] in config.ini are merged on top.
"""

DEFAULT_SETTINGS: dict[str, str] = {
    "layer_height": "0.2",
    "fill_density": "15",
    # All-metal heat break: keep retraction short to avoid jams
    "retraction_amount": "4",
    # Cooling fan always at 100%
    "cool_fan_speed": "100",
    "cool_fan_speed_min": "100",
    "cool_fan_speed_max": "100",
    "cool_fan_speed_0": "100",
}

# Settings that are always sent to CuraEngine even if they match the definition
# default. Use this for settings where CuraEngine's built-in default is wrong or
# where omitting the flag changes behavior.
FORCED_SETTINGS: dict[str, str] = {
    "roofing_layer_count": "0",
    "flooring_layer_count": "0",
}

BOUNDS_OVERRIDES: dict[str, float] = {
    # All-metal heat break: never retract more than 4 mm
    "retraction_amount.maximum_value": 4,
}

PRESETS: dict[str, dict] = {
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
