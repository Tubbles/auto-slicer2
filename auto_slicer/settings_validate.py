from dataclasses import dataclass

from .settings_registry import SettingDefinition


@dataclass
class ValidationResult:
    ok: bool
    coerced_value: str  # normalized value to store
    error: str = ""     # non-empty if ok is False
    warning: str = ""   # non-empty if value is in warning range


_BOOL_TRUE = {"true", "yes", "1", "on"}
_BOOL_FALSE = {"false", "no", "0", "off"}


def validate(defn: SettingDefinition, raw_value: str) -> ValidationResult:
    dispatch = {
        "float": _validate_float,
        "int": _validate_int,
        "bool": _validate_bool,
        "enum": _validate_enum,
        "str": _validate_str,
    }
    handler = dispatch.get(defn.setting_type, _validate_str)
    return handler(defn, raw_value)


def _validate_float(defn: SettingDefinition, raw: str) -> ValidationResult:
    try:
        val = float(raw)
    except ValueError:
        return ValidationResult(ok=False, coerced_value=raw,
                                error=f"Expected a number, got '{raw}'")
    return _check_bounds(defn, val, raw)


def _validate_int(defn: SettingDefinition, raw: str) -> ValidationResult:
    try:
        val = int(raw)
    except ValueError:
        # Allow "3.0" style input
        try:
            f = float(raw)
            if f != int(f):
                return ValidationResult(ok=False, coerced_value=raw,
                                        error=f"Expected an integer, got '{raw}'")
            val = int(f)
        except ValueError:
            return ValidationResult(ok=False, coerced_value=raw,
                                    error=f"Expected an integer, got '{raw}'")
    return _check_bounds(defn, val, str(val))


def _check_bounds(defn: SettingDefinition, val: float, coerced: str) -> ValidationResult:
    unit = f" {defn.unit}" if defn.unit else ""

    # Hard bounds → reject
    if defn.minimum_value is not None and val < defn.minimum_value:
        return ValidationResult(
            ok=False, coerced_value=coerced,
            error=f"Value {val}{unit} is below minimum ({defn.minimum_value}{unit})")
    if defn.maximum_value is not None and val > defn.maximum_value:
        return ValidationResult(
            ok=False, coerced_value=coerced,
            error=f"Value {val}{unit} is above maximum ({defn.maximum_value}{unit})")

    # Warning bounds → accept with warning
    warning = ""
    if defn.minimum_value_warning is not None and val < defn.minimum_value_warning:
        warning = f"Value {val}{unit} is below recommended minimum ({defn.minimum_value_warning}{unit})"
    elif defn.maximum_value_warning is not None and val > defn.maximum_value_warning:
        warning = f"Value {val}{unit} is above recommended maximum ({defn.maximum_value_warning}{unit})"

    return ValidationResult(ok=True, coerced_value=coerced, warning=warning)


def _validate_bool(defn: SettingDefinition, raw: str) -> ValidationResult:
    lower = raw.lower().strip()
    if lower in _BOOL_TRUE:
        return ValidationResult(ok=True, coerced_value="true")
    if lower in _BOOL_FALSE:
        return ValidationResult(ok=True, coerced_value="false")
    return ValidationResult(
        ok=False, coerced_value=raw,
        error=f"Expected true/false, got '{raw}'")


def _validate_enum(defn: SettingDefinition, raw: str) -> ValidationResult:
    # Check against option keys
    if raw in defn.options:
        return ValidationResult(ok=True, coerced_value=raw)

    # Try case-insensitive key match
    raw_lower = raw.lower()
    for opt_key in defn.options:
        if opt_key.lower() == raw_lower:
            return ValidationResult(ok=True, coerced_value=opt_key)

    # Try matching option labels
    for opt_key, opt_label in defn.options.items():
        if opt_label.lower() == raw_lower:
            return ValidationResult(ok=True, coerced_value=opt_key)

    valid = ", ".join(defn.options.keys())
    return ValidationResult(
        ok=False, coerced_value=raw,
        error=f"Invalid option '{raw}'. Valid options: {valid}")


def _validate_str(defn: SettingDefinition, raw: str) -> ValidationResult:
    return ValidationResult(ok=True, coerced_value=raw)
