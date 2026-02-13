import re
import time
import subprocess
import shutil
from pathlib import Path

from .config import Config
from .settings_eval import evaluate_expressions
from .settings_registry import SettingsRegistry
from .thumbnails import generate_thumbnails, inject_thumbnails

GCODE_SETTINGS = ("machine_start_gcode", "machine_end_gcode")


def expand_gcode_tokens(gcode: str, settings: dict[str, str]) -> str:
    """Replace {setting_name} tokens in gcode with their resolved values."""
    return re.sub(r"\{(\w+)\}", lambda m: settings.get(m.group(1), m.group(0)), gcode)


def find_unknown_gcode_tokens(settings: dict[str, str]) -> dict[str, list[str]]:
    """Return {gcode_key: [unknown_tokens]} for any unexpanded tokens."""
    result = {}
    for key in GCODE_SETTINGS:
        if key in settings:
            unknown = re.findall(r"\{(\w+)\}", settings[key])
            if unknown:
                result[key] = unknown
    return result


def merge_settings(defaults: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    """Merge default settings with user overrides."""
    result = defaults.copy()
    result.update(overrides)
    return result


def resolve_settings(
    registry: SettingsRegistry,
    config_defaults: dict[str, str],
    overrides: dict[str, str],
    forced_keys: set[str] = frozenset(),
) -> dict[str, str]:
    """Evaluate all expressions and return a flat string dict for CuraEngine.

    Merges config defaults, user overrides, and computed values.
    User overrides take highest priority; computed values fill in the rest.
    Keys in forced_keys are always sent even if they match the definition default.
    """
    pinned = merge_settings(config_defaults, overrides)
    result = evaluate_expressions(registry, pinned, config_defaults)

    # Start with computed values (as strings)
    resolved = {k: str(v) for k, v in result.values.items()}
    # Layer pinned values on top (they always win)
    resolved.update(pinned)

    # Drop values that match the definition default — no need to send them
    for key in list(resolved):
        defn = registry.get(key)
        if defn and str(defn.default_value) == resolved[key] and key not in overrides and key not in forced_keys:
            del resolved[key]

    # Expand {setting_name} tokens in gcode strings (CuraEngine doesn't do this)
    for key in GCODE_SETTINGS:
        if key in resolved:
            resolved[key] = expand_gcode_tokens(resolved[key], resolved)

    return resolved


def build_cura_command(
    cura_bin: Path, def_dir: Path, printer_def: str,
    stl_path: Path, gcode_path: Path, settings: dict[str, str],
) -> list[str]:
    """Build the CuraEngine command line (pure)."""
    extruders_dir = def_dir.parent / "extruders"
    cmd = [
        str(cura_bin),
        "slice",
        "-d", str(def_dir),
        "-d", str(extruders_dir),
        "-j", printer_def,
    ]

    for key, val in settings.items():
        cmd.extend(["-s", f"{key}={val}"])

    cmd.extend(
        [
            "-l", str(stl_path),
            "-o", str(gcode_path),
        ]
    )
    return cmd


def slice_file(config: Config, stl_path: Path, overrides: dict) -> tuple[bool, str, Path | None]:
    """Slice an STL file and return (success, message, archive_path)."""
    active_settings = resolve_settings(config.registry, config.defaults, overrides, config.forced_keys)

    unknown = find_unknown_gcode_tokens(active_settings)
    if unknown:
        msgs = [f"{k}: {', '.join(tokens)}" for k, tokens in unknown.items()]
        error_msg = "Unknown gcode tokens: " + "; ".join(msgs)
        print(f"[Error] {error_msg}")
        return False, error_msg, None

    gcode_path = stl_path.with_suffix(".gcode")

    cmd = build_cura_command(
        config.cura_bin, config.def_dir, config.printer_def,
        stl_path, gcode_path, active_settings,
    )

    print(f"[Slicing] {stl_path.name}")
    print(f"[Command] {' '.join(cmd)}")
    print(f"[Settings] {active_settings}")

    try:
        result = subprocess.run(cmd, cwd=str(config.def_dir), capture_output=True, text=True)

        if result.stdout:
            print(f"[stdout] {result.stdout}")
        if result.stderr:
            print(f"[stderr] {result.stderr}")
        print(f"[Exit code] {result.returncode}")

        if result.returncode == 0:
            try:
                thumb_comments = generate_thumbnails(stl_path, stl_path.parent)
                if thumb_comments:
                    inject_thumbnails(gcode_path, thumb_comments)
                    print("[Thumbnail] Injected thumbnails into gcode")
            except Exception as e:
                print(f"[Thumbnail] Skipped: {e}")

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            job_folder = config.archive_dir / f"{stl_path.stem}_{timestamp}"
            job_folder.mkdir(parents=True, exist_ok=True)

            shutil.move(str(stl_path), job_folder / stl_path.name)
            if gcode_path.exists():
                shutil.move(str(gcode_path), job_folder / gcode_path.name)

            print(f"[Success] Archived to {job_folder}")
            return True, "Slicing completed successfully", job_folder
        else:
            error_dir = config.archive_dir / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stl_path), error_dir / stl_path.name)
            output = result.stdout + result.stderr
            error_msg = output.strip()[:500] if output.strip() else f"Exit code {result.returncode}"
            print(f"[Failed] {error_msg}")
            return False, f"CuraEngine error:\n{error_msg}", error_dir

    except Exception as e:
        print(f"[Exception] {e}")
        return False, f"System error: {e}", None
