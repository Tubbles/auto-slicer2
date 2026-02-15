import math
import re
import time
import subprocess
import shutil
from pathlib import Path

from .config import Config
from .presets import load_presets
from .settings_eval import _SAFE_BUILTINS, evaluate_expressions
from .settings_registry import SettingsRegistry
from .stl_transform import needs_scaling, scale_stl
from .thumbnails import find_header_end, generate_thumbnails, inject_thumbnails

GCODE_SETTINGS = ("machine_start_gcode", "machine_end_gcode")
SCALE_KEYS = {"scale", "scale_x", "scale_y", "scale_z"}


def _eval_gcode_expr(expr: str, namespace: dict) -> str:
    """Evaluate a single gcode {expression} and return its string result."""
    eval_globals = {"__builtins__": {}, "math": math}
    eval_globals.update(_SAFE_BUILTINS)
    return str(eval(expr, eval_globals, namespace))  # noqa: S307


def expand_gcode_tokens(gcode: str, settings: dict[str, str]) -> str:
    """Evaluate {expressions} in gcode using setting values as the namespace.

    Handles both simple tokens like {layer_height} and arbitrary expressions
    like {machine_depth - 20}. CuraEngine does NOT evaluate these — we must
    resolve everything before sending.
    """
    namespace = {k: _try_number(v) for k, v in settings.items()}

    def replace(m: re.Match) -> str:
        expr = m.group(1)
        try:
            return _eval_gcode_expr(expr, namespace)
        except Exception:
            return m.group(0)  # leave unresolved on error

    return re.sub(r"\{([^}]+)\}", replace, gcode)


def _try_number(value: str) -> int | float | str:
    """Try to parse a string as int or float, returning the original on failure."""
    try:
        f = float(value)
        return int(f) if f == int(f) else f
    except (ValueError, OverflowError):
        return value


def find_unknown_gcode_tokens(settings: dict[str, str]) -> dict[str, list[str]]:
    """Return {gcode_key: [unknown_expressions]} for any unresolvable tokens."""
    namespace = {k: _try_number(v) for k, v in settings.items()}
    result = {}
    for key in GCODE_SETTINGS:
        if key not in settings:
            continue
        unknown = []
        for m in re.finditer(r"\{([^}]+)\}", settings[key]):
            expr = m.group(1)
            try:
                _eval_gcode_expr(expr, namespace)
            except Exception:
                unknown.append(expr)
        if unknown:
            result[key] = unknown
    return result


def merge_settings(defaults: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    """Merge default settings with user overrides."""
    result = defaults.copy()
    result.update(overrides)
    return result


def _resolve_scale(config_defaults: dict[str, str], overrides: dict[str, str]) -> tuple[float, float, float]:
    """Resolve master + per-axis scale factors from defaults and overrides."""
    merged = {**config_defaults, **overrides}
    master = float(merged.get("scale", "100"))
    sx = float(merged.get("scale_x", str(master)))
    sy = float(merged.get("scale_y", str(master)))
    sz = float(merged.get("scale_z", str(master)))
    return sx, sy, sz


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

    # Strip custom scale keys — they're handled before slicing, not by CuraEngine
    for key in SCALE_KEYS:
        resolved.pop(key, None)

    return resolved


def matching_presets(overrides: dict[str, str], presets: dict[str, dict]) -> list[str]:
    """Return preset names whose settings are all present in overrides with matching values."""
    return [
        name for name, preset in presets.items()
        if preset.get("settings")
        and all(overrides.get(k) == v for k, v in preset["settings"].items())
    ]


def format_settings_summary(
    overrides: dict[str, str], presets: dict[str, dict],
    registry: SettingsRegistry | None = None,
) -> str:
    """Format override settings and matching presets as plain text for settings.txt."""
    lines = []
    preset_names = matching_presets(overrides, presets)
    for name in preset_names:
        lines.append(f"preset: {name}")
    if preset_names:
        lines.append("")
    for key, value in sorted(overrides.items()):
        if "\n" in value or len(value) > 100:
            continue
        defn = registry.get(key) if registry else None
        label = defn.label if defn else key
        lines.append(f"{label} = {value}")
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


def format_duration(seconds: int) -> str:
    """Format seconds as human-readable duration like '1h 5m 30s'."""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if h or m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def extract_stats(header: dict[str, str]) -> dict:
    """Extract time and filament stats from a parsed gcode header.

    Returns {"time_seconds": int, "filament_meters": float} or empty dict.
    """
    time_val = header.get(";TIME")
    filament_val = header.get(";Filament used")
    if time_val is None or filament_val is None:
        return {}
    try:
        time_seconds = int(time_val)
        filament_meters = round(float(filament_val.strip().rstrip("m")), 2)
    except (ValueError, AttributeError):
        return {}
    return {"time_seconds": time_seconds, "filament_meters": filament_meters}


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


def slice_file(config: Config, stl_path: Path, overrides: dict, archive_folder: Path | None = None) -> tuple[bool, str, Path | None, dict]:
    """Slice an STL file and return (success, message, archive_path, stats).

    If archive_folder is provided, use it instead of creating a new timestamped folder.
    stats is a dict with time_seconds and filament_meters, or empty on failure.
    """
    scale_warning = ""
    sx, sy, sz = _resolve_scale(config.defaults, overrides)
    if needs_scaling(sx, sy, sz):
        if stl_path.suffix.lower() == ".stl":
            scale_stl(stl_path, sx, sy, sz)
            print(f"[Scale] Applied scaling: X={sx}% Y={sy}% Z={sz}%")
        else:
            scale_warning = f"Scaling skipped (not supported for {stl_path.suffix} files)"
            print(f"[Scale] {scale_warning}")

    active_settings = resolve_settings(config.registry, config.defaults, overrides, config.forced_keys)

    unknown = find_unknown_gcode_tokens(active_settings)
    if unknown:
        msgs = [f"{k}: {', '.join(tokens)}" for k, tokens in unknown.items()]
        error_msg = "Unknown gcode tokens: " + "; ".join(msgs)
        print(f"[Error] {error_msg}")
        return False, error_msg, None, {}

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
            stats = extract_stats(header)
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

            job_folder = archive_folder or config.archive_dir / stl_path.stem / time.strftime("%Y%m%d_%H%M%S")
            job_folder.mkdir(parents=True, exist_ok=True)

            model_folder = job_folder.parent
            shutil.move(str(stl_path), model_folder / stl_path.name)
            if gcode_path.exists():
                shutil.move(str(gcode_path), job_folder / gcode_path.name)

            summary = format_settings_summary(overrides, presets, registry=config.registry)
            if summary:
                (job_folder / "settings.txt").write_text(summary)

            print(f"[Success] Archived to {job_folder}")
            msg = "Slicing completed successfully"
            if scale_warning:
                msg += f"\n{scale_warning}"
            return True, msg, job_folder, stats
        else:
            error_dir = config.archive_dir / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stl_path), error_dir / stl_path.name)
            output = result.stdout + result.stderr
            error_msg = output.strip()[:500] if output.strip() else f"Exit code {result.returncode}"
            print(f"[Failed] {error_msg}")
            return False, f"CuraEngine error:\n{error_msg}", error_dir, {}

    except Exception as e:
        print(f"[Exception] {e}")
        return False, f"System error: {e}", None, {}
