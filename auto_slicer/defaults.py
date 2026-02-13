"""Checked-in printer defaults, bounds overrides, and presets.

config.ini keeps only machine-specific paths and secrets.
Any [DEFAULT_SETTINGS] or [BOUNDS_OVERRIDES] in config.ini are merged on top.

SETTINGS uses Cura-style subkeys:
  default_value  — value to send to CuraEngine
  forced         — always send even if it matches the definition default
  maximum_value, minimum_value, etc. — bounds overrides
"""

SETTINGS: dict[str, dict] = {
    "layer_height": {
        "default_value": "0.2",
    },
    "layer_height_0": {
        "value_expression": "layer_height",
    },
    "infill_sparse_density": {
        "default_value": "15",
    },
    "material_print_temperature": {
        "default_value": "220",
    },
    "material_bed_temperature": {
        "default_value": "60",
    },
    "support_structure": {
        "default_value": "tree",
    },
    "support_type": {
        "default_value": "buildplate",
    },
    "adhesion_type": {
        "default_value": "skirt",
    },
    "skirt_line_count": {
        "default_value": "2",
    },
    "skirt_height": {
        "default_value": "2",
    },
    "center_object": {
        "default_value": "true",
    },
    # Suppress CuraEngine's auto-heating so start gcode handles it
    "material_print_temp_prepend": {
        "default_value": "false",
    },
    "material_bed_temp_prepend": {
        "default_value": "false",
    },
    # Start gcode: heat both, home during heat-up, park high to avoid ooze
    "machine_start_gcode": {
        "forced": True,
        "default_value": (
            "M140 S{material_bed_temperature} ;Start bed heating\n"
            "M104 S{material_print_temperature} ;Start nozzle heating\n"
            "G28 ;Home all axes\n"
            "G1 X0 Y0 Z100 F3000 ;Park away from bed\n"
            "M190 S{material_bed_temperature} ;Wait for bed\n"
            "M109 S{material_print_temperature} ;Wait for nozzle\n"
            "G92 E0 ;Reset Extruder\n"
            "G1 Z2.0 F3000 ;Move Z Axis up\n"
            "G1 X0.1 Y20 Z0.3 F5000.0 ;Move to start position\n"
            "G1 X0.1 Y200.0 Z0.3 F1500.0 E15 ;Draw the first line\n"
            "G1 X0.4 Y200.0 Z0.3 F5000.0 ;Move to side a little\n"
            "G1 X0.4 Y20 Z0.3 F1500.0 E30 ;Draw the second line\n"
            "G92 E0 ;Reset Extruder\n"
            "G1 Z2.0 F3000 ;Move Z Axis up\n"
            "G1 X5 Y20 Z0.3 F5000.0 ;Move over to prevent blob squish"
        ),
    },
    # All-metal heat break: keep retraction short to avoid jams
    "retraction_amount": {
        "default_value": "4",
        "maximum_value": 4,
    },
    # Cooling fan always at 100%
    "cool_fan_speed": {
        "default_value": "100",
    },
    "cool_fan_speed_min": {
        "default_value": "100",
    },
    "cool_fan_speed_max": {
        "default_value": "100",
    },
    "cool_fan_speed_0": {
        "default_value": "100",
    },
    # Ensure roofing/flooring layers are always passed to CuraEngine
    "roofing_layer_count": {
        "default_value": "0",
        "forced": True,
    },
    "flooring_layer_count": {
        "default_value": "0",
        "forced": True,
    },
}

BOUNDS_FIELDS = (
    "minimum_value", "maximum_value",
    "minimum_value_warning", "maximum_value_warning",
)


def extract_defaults(settings: dict[str, dict]) -> dict[str, str]:
    """Extract {key: default_value} for all settings with a default_value."""
    return {k: v["default_value"] for k, v in settings.items() if "default_value" in v}


def extract_forced_keys(settings: dict[str, dict]) -> set[str]:
    """Extract the set of keys marked as forced."""
    return {k for k, v in settings.items() if v.get("forced")}


def extract_expression_overrides(settings: dict[str, dict]) -> dict[str, str]:
    """Extract {key: expression} for settings with value_expression overrides."""
    return {k: v["value_expression"] for k, v in settings.items() if "value_expression" in v}


def extract_bounds_overrides(settings: dict[str, dict]) -> dict[str, dict[str, float]]:
    """Extract {key: {field: value}} for settings with bounds overrides."""
    result = {}
    for key, v in settings.items():
        bounds = {f: v[f] for f in BOUNDS_FIELDS if f in v}
        if bounds:
            result[key] = bounds
    return result


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
    "PLA": {
        "description": "Temperatures and settings for PLA filament",
        "settings": {
            "material_print_temperature": "220",
            "material_bed_temperature": "60",
            "cool_fan_speed": "100",
            "cool_fan_speed_min": "100",
            "cool_fan_speed_max": "100",
            "speed_print": "60",
        },
    },
    "PETG": {
        "description": "Temperatures and settings for PETG filament",
        "settings": {
            "material_print_temperature": "235",
            "material_bed_temperature": "75",
            "cool_fan_speed": "50",
            "cool_fan_speed_min": "50",
            "cool_fan_speed_max": "50",
            "speed_print": "45",
        },
    },
}
