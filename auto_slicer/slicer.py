import re
import time
import subprocess
import shutil
from pathlib import Path

from .config import Config
from .presets import load_presets
from .settings_eval import evaluate_expressions
from .settings_registry import SettingsRegistry
from .thumbnails import find_header_end, generate_thumbnails, inject_thumbnails

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

    # Gcode settings must always be present so we can expand {tokens}.
    # Pull from registry default if not already resolved.
    for key in GCODE_SETTINGS:
        if key not in resolved:
            defn = registry.get(key)
            if defn and defn.default_value is not None:
                resolved[key] = str(defn.default_value)

    # Build a complete lookup for gcode token expansion: registry defaults
    # as base, then resolved values on top. This ensures tokens like
    # {machine_depth} resolve even when the setting isn't in our overrides.
    token_lookup = {k: str(d.default_value) for k, d in registry.settings.items()
                    if d.default_value is not None}
    token_lookup.update(resolved)
    for key in GCODE_SETTINGS:
        if key in resolved:
            resolved[key] = expand_gcode_tokens(resolved[key], token_lookup)

    # Drop values that match the definition default — no need to send them
    for key in list(resolved):
        if key in GCODE_SETTINGS:
            continue  # always send gcode settings
        defn = registry.get(key)
        if defn and str(defn.default_value) == resolved[key] and key not in overrides and key not in forced_keys:
            del resolved[key]

    return resolved


def matching_presets(overrides: dict[str, str], presets: dict[str, dict]) -> list[str]:
    """Return preset names whose settings are all present in overrides with matching values."""
    return [
        name for name, preset in presets.items()
        if preset.get("settings")
        and all(overrides.get(k) == v for k, v in preset["settings"].items())
    ]


def format_settings_summary(overrides: dict[str, str], presets: dict[str, dict]) -> str:
    """Format override settings and matching presets as plain text for settings.txt."""
    lines = []
    for name in matching_presets(overrides, presets):
        lines.append(f"preset: {name}")
    for key, value in sorted(overrides.items()):
        if "\n" in value or len(value) > 100:
            continue
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n" if lines else ""


def format_metadata_comments(overrides: dict[str, str], presets: dict[str, dict]) -> str:
    """Format override settings and matching presets as gcode comment lines."""
    lines = []
    for name in matching_presets(overrides, presets):
        lines.append(f"; preset: {name}")
    for key, value in sorted(overrides.items()):
        if "\n" in value or len(value) > 100:
            continue
        lines.append(f"; override: {key} = {value}")
    return "\n".join(lines) + "\n" if lines else ""


def inject_metadata(gcode_path: Path, overrides: dict[str, str], presets: dict[str, dict]) -> None:
    """Inject override/preset metadata comments into a gcode file's header."""
    comments = format_metadata_comments(overrides, presets)
    if not comments:
        return
    lines = gcode_path.read_text().splitlines(keepends=True)
    pos = find_header_end(lines)
    header = "".join(lines[:pos])
    body = "".join(lines[pos:])
    gcode_path.write_text(header + ";\n" + comments + ";\n" + body)


def parse_gcode_header(stderr: str) -> dict[str, str]:
    """Parse the real gcode header values from CuraEngine's stderr.

    CuraEngine logs the correct header after slicing but doesn't update the
    output file. Format:
        [...] [info] Gcode header after slicing: ;FLAVOR:Marlin
        ;TIME:2659
        ;Filament used: 1.95583m
        ;Layer height: 0.2
        ...
    Returns {";TIME": "2659", ";Filament used": "1.95583m", ...}.
    """
    header = {}
    m = re.search(r"Gcode header after slicing:\s*(;.+)", stderr)
    if not m:
        return header
    # First header line is on the same line as the log message
    first_line = m.group(1).strip()
    # Collect all subsequent ;-prefixed lines
    rest = stderr[m.end():]
    lines = [first_line] + [
        line.strip() for line in rest.splitlines()
        if line.strip().startswith(";") and ":" in line.strip()
    ]
    for line in lines:
        # Stop if we hit a log line (e.g. from our own injection comments)
        if not line.startswith(";"):
            break
        key, _, value = line.partition(":")
        if value:
            header[key] = value
    return header


def patch_gcode_header(gcode_path: Path, header: dict[str, str]) -> None:
    """Replace placeholder header lines in a gcode file with real values."""
    if not header:
        return
    content = gcode_path.read_text()
    for key, value in header.items():
        # Match lines like ";TIME:6666" and replace with ";TIME:2659"
        content = re.sub(
            re.escape(key) + r":.*",
            f"{key}:{value}",
            content,
            count=1,
        )
    gcode_path.write_text(content)


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


def slice_file(config: Config, stl_path: Path, overrides: dict, archive_folder: Path | None = None) -> tuple[bool, str, Path | None]:
    """Slice an STL file and return (success, message, archive_path).

    If archive_folder is provided, use it instead of creating a new timestamped folder.
    """
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
            header = parse_gcode_header(result.stdout + "\n" + result.stderr)
            if header:
                patch_gcode_header(gcode_path, header)
                print(f"[Header] Patched gcode header with {len(header)} values")

            try:
                thumb_comments = generate_thumbnails(stl_path, stl_path.parent)
                if thumb_comments:
                    inject_thumbnails(gcode_path, thumb_comments)
                    print("[Thumbnail] Injected thumbnails into gcode")
            except Exception as e:
                print(f"[Thumbnail] Skipped: {e}")

            presets = load_presets()
            inject_metadata(gcode_path, overrides, presets)

            job_folder = archive_folder or config.archive_dir / f"{stl_path.stem}_{time.strftime('%Y%m%d_%H%M%S')}"
            job_folder.mkdir(parents=True, exist_ok=True)

            shutil.move(str(stl_path), job_folder / stl_path.name)
            if gcode_path.exists():
                shutil.move(str(gcode_path), job_folder / gcode_path.name)

            summary = format_settings_summary(overrides, presets)
            if summary:
                (job_folder / "settings.txt").write_text(summary)

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
