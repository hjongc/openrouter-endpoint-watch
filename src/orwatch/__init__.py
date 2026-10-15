"""OpenRouter endpoint metadata history and checks."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from .config import ConfigError, load_config, validate_profile
from .core import Decision, decide, list_snapshots, normalize_response, read_snapshot, snapshot_label, utc_now

__version__ = "0.1.0"


class ProviderSetBlocked(RuntimeError):
    def __init__(self, decision: Decision):
        self.decision = decision
        super().__init__(f"provider set blocked for {decision.model_id}: {len(decision.providers)} usable, need {decision.min_eligible}")


_CHECK_CACHE: dict[tuple[str, str, bool], tuple[float, Decision]] = {}
_CHECK_CACHE_LOCK = RLock()


def _copy_decision(decision: Decision) -> Decision:
    return Decision(
        model_id=decision.model_id,
        status=decision.status,
        providers=list(decision.providers),
        route=list(decision.route),
        quantizations=tuple(decision.quantizations),
        dropped=[dict(item) for item in decision.dropped],
        backup_used=list(decision.backup_used),
        min_eligible=decision.min_eligible,
        eligible_endpoints=decision.eligible_endpoints,
        source=decision.source,
        checked_at=decision.checked_at,
    )


def check(
    model_id: str | None = None,
    profile: str | dict[str, Any] = "production",
    *,
    config: str | Path | None = "watch.toml",
    offline: bool = False,
    max_age: float = 300,
    raise_on_blocked: bool = False,
    fetch: Callable[[str], dict[str, Any]] | None = None,
) -> Decision:
    """Return a routing decision using a live response or the latest snapshot."""
    if max_age < 0:
        raise ValueError("max_age must be non-negative")
    loaded_config: dict[str, Any] | None = None
    if isinstance(profile, dict):
        selected_profile = validate_profile("profile", dict(profile))
        if offline or model_id is None:
            if config is None:
                raise ConfigError("a config file is required to choose a model or read offline snapshots")
            if not Path(config).exists():
                raise ConfigError(f"config file not found: {config}")
            loaded_config = load_config(config)
    elif isinstance(profile, str):
        if config is None:
            raise ConfigError("a config file is required when profile is a name")
        loaded_config = load_config(config)
        profiles = loaded_config.get("profiles", {})
        if profile not in profiles:
            raise ConfigError(f"unknown profile: {profile}")
        selected_profile = profiles[profile]
    else:
        raise ConfigError("profile must be a profile name or a dict")

    if model_id is None:
        if not loaded_config:
            raise ConfigError("model_id is required when no config file is available")
        models = loaded_config["watch"]["models"]
        if not models:
            raise ConfigError("config has no models")
        model_id = models[0]

    profile_key = json.dumps(selected_profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    cache_key = (model_id, profile_key, bool(offline))
    now = time.monotonic()
    if max_age > 0:
        with _CHECK_CACHE_LOCK:
            cached = _CHECK_CACHE.get(cache_key)
            if cached and now - cached[0] < max_age:
                decision = _copy_decision(cached[1])
                if raise_on_blocked and decision.status == "blocked":
                    raise ProviderSetBlocked(decision)
                return decision

    if offline:
        if loaded_config is None:
            raise ConfigError("offline checks require a config file with snapshot_dir")
        snapshot_dir = loaded_config["watch"]["snapshot_dir"]
        paths = list_snapshots(snapshot_dir, model_id)
        if not paths:
            raise ValueError(f"no snapshots for {model_id}")
        path = paths[-1]
        snapshot = read_snapshot(path)
        source_label = snapshot_label(path.stem)
    else:
        if fetch is None:
            from .cli import fetch_endpoints

            fetch = fetch_endpoints
        snapshot = normalize_response(model_id, fetch(model_id), utc_now())
        source_label = "live"

    decision = decide(snapshot, selected_profile, source_label=source_label)
    if max_age > 0:
        with _CHECK_CACHE_LOCK:
            _CHECK_CACHE[cache_key] = (time.monotonic(), _copy_decision(decision))
    if raise_on_blocked and decision.status == "blocked":
        raise ProviderSetBlocked(decision)
    return decision


__all__ = ["Decision", "ProviderSetBlocked", "check", "decide"]
