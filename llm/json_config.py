from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def deep_merge_dicts(
    base: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in overrides.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            result[key] = deep_merge_dicts(current, value)
        else:
            result[key] = deepcopy(value)
    return result


def get_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def validate_protected_paths(
    merged: Mapping[str, Any],
    standard: Mapping[str, Any],
    protected_paths: set[str] | frozenset[str],
) -> None:
    for path in protected_paths:
        if get_path(merged, path) != get_path(standard, path):
            raise ValueError(f"json_config changed protected request field: {path}")
