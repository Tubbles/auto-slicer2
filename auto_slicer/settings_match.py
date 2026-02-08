import difflib

from .settings_registry import SettingsRegistry, SettingDefinition


class SettingsMatcher:
    def __init__(self, registry: SettingsRegistry):
        self._registry = registry

    def resolve(self, query: str) -> tuple[str | None, list[SettingDefinition]]:
        """Resolve a user query to a setting key.

        Returns (exact_key, candidates):
        - Single match: exact_key is set, candidates has one entry
        - Multiple matches: exact_key is None, candidates has the options
        - No match: exact_key is None, candidates is empty
        """
        settings = self._registry.all_settings()

        # 1. Exact key match (also try spaces→underscores)
        normalized = query.replace(" ", "_").lower()
        for key in settings:
            if key.lower() == normalized:
                return key, [settings[key]]

        # 2. Exact label match (case-insensitive)
        label_map = self._registry.label_to_key()
        query_lower = query.lower()
        if query_lower in label_map:
            key = label_map[query_lower]
            return key, [settings[key]]

        # 3. Substring match in key or label
        substring_matches = []
        for key, defn in settings.items():
            if query_lower in key.lower() or query_lower in defn.label.lower():
                substring_matches.append(defn)

        if len(substring_matches) == 1:
            return substring_matches[0].key, substring_matches
        if substring_matches:
            return None, substring_matches[:10]

        # 4. Fuzzy match on labels then keys
        all_labels = list(label_map.keys())
        close_labels = difflib.get_close_matches(query_lower, all_labels, n=5, cutoff=0.6)
        if close_labels:
            candidates = [settings[label_map[lbl]] for lbl in close_labels]
            if len(candidates) == 1:
                return candidates[0].key, candidates
            return None, candidates

        all_keys = list(settings.keys())
        close_keys = difflib.get_close_matches(normalized, all_keys, n=5, cutoff=0.6)
        if close_keys:
            candidates = [settings[k] for k in close_keys]
            if len(candidates) == 1:
                return candidates[0].key, candidates
            return None, candidates

        return None, []
