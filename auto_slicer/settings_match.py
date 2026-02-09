import difflib

from .settings_registry import SettingsRegistry, SettingDefinition


def _match_exact_key(settings: dict, normalized: str) -> tuple[str | None, list[SettingDefinition]]:
    """Try exact key match (case-insensitive, spaces→underscores)."""
    for key in settings:
        if key.lower() == normalized:
            return key, [settings[key]]
    return None, []


def _match_exact_label(settings: dict, label_map: dict, query_lower: str) -> tuple[str | None, list[SettingDefinition]]:
    """Try exact label match (case-insensitive)."""
    if query_lower in label_map:
        key = label_map[query_lower]
        return key, [settings[key]]
    return None, []


def _match_substring(settings: dict, query_lower: str) -> tuple[str | None, list[SettingDefinition]]:
    """Try substring match in key or label."""
    matches = []
    for key, defn in settings.items():
        if query_lower in key.lower() or query_lower in defn.label.lower():
            matches.append(defn)
    if len(matches) == 1:
        return matches[0].key, matches
    if matches:
        return None, matches[:10]
    return None, []


def _match_fuzzy(settings: dict, label_map: dict, query_lower: str, normalized: str) -> tuple[str | None, list[SettingDefinition]]:
    """Try fuzzy match on labels then keys via difflib."""
    # Try labels first
    close_labels = difflib.get_close_matches(query_lower, list(label_map.keys()), n=5, cutoff=0.6)
    if close_labels:
        candidates = [settings[label_map[lbl]] for lbl in close_labels]
        if len(candidates) == 1:
            return candidates[0].key, candidates
        return None, candidates

    # Then try keys
    close_keys = difflib.get_close_matches(normalized, list(settings.keys()), n=5, cutoff=0.6)
    if close_keys:
        candidates = [settings[k] for k in close_keys]
        if len(candidates) == 1:
            return candidates[0].key, candidates
        return None, candidates

    return None, []


def resolve_setting(registry: SettingsRegistry, query: str) -> tuple[str | None, list[SettingDefinition]]:
    """Resolve a user query to a setting key.

    Returns (exact_key, candidates):
    - Single match: exact_key is set, candidates has one entry
    - Multiple matches: exact_key is None, candidates has the options
    - No match: exact_key is None, candidates is empty
    """
    settings = registry.all_settings()
    label_map = registry.label_to_key()
    normalized = query.replace(" ", "_").lower()
    query_lower = query.lower()

    for match_fn in [
        lambda: _match_exact_key(settings, normalized),
        lambda: _match_exact_label(settings, label_map, query_lower),
        lambda: _match_substring(settings, query_lower),
        lambda: _match_fuzzy(settings, label_map, query_lower, normalized),
    ]:
        key, candidates = match_fn()
        if key is not None or candidates:
            return key, candidates

    return None, []
