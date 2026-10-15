"""TOML configuration loading and validation."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(str(exc)) from exc
    watch = config.get("watch")
    if not isinstance(watch, dict):
        raise ConfigError("config requires [watch]")
    models = watch.get("models")
    if not isinstance(models, list) or not models or not all(isinstance(m, str) and m for m in models):
        raise ConfigError("watch.models must be a non-empty list of model ids")
    if not isinstance(watch.get("snapshot_dir"), str) or not watch["snapshot_dir"]:
        raise ConfigError("watch.snapshot_dir must be a non-empty path")
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ConfigError("profiles must be tables")
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ConfigError(f"profile {name} must be a table")
        if not isinstance(profile.get("quantizations"), list) or not all(isinstance(v, str) for v in profile["quantizations"]):
            raise ConfigError(f"profile {name}.quantizations must be a list of strings")
        for key in ("required_parameters", "preferred_providers"):
            if key in profile and (not isinstance(profile[key], list) or not all(isinstance(v, str) for v in profile[key])):
                raise ConfigError(f"profile {name}.{key} must be a list of strings")
        if "min_eligible" in profile and (not isinstance(profile["min_eligible"], int) or profile["min_eligible"] < 0):
            raise ConfigError(f"profile {name}.min_eligible must be a non-negative integer")
        if "healthy_only" in profile and not isinstance(profile["healthy_only"], bool):
            raise ConfigError(f"profile {name}.healthy_only must be a boolean")
    return config
