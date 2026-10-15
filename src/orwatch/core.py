"""Normalization, snapshot storage, profile evaluation, and endpoint diffs."""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

VOLATILE_FIELDS = {"latency_last_30m", "throughput_last_30m", "uptime_last_5m", "uptime_last_30m"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def snapshot_label(stem: str) -> str:
    """Return the display label for a legacy date or minute-precision UTC stem."""
    if len(stem) == 16 and stem[10] == "T" and stem.endswith("Z"):
        try:
            parsed = datetime.strptime(stem, "%Y-%m-%dT%H%MZ")
        except ValueError:
            pass
        else:
            return parsed.strftime("%Y-%m-%d %H:%M UTC")
    return stem


def normalize_response(
    model_id: str,
    response: dict[str, Any],
    fetched_at: str | None = None,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    emit = warn or (lambda message: warnings.warn(message, RuntimeWarning, stacklevel=2))
    rows = response.get("data", {}).get("endpoints")
    if not isinstance(rows, list):
        raise ValueError("response must contain data.endpoints list")
    tags = [row.get("tag") for row in rows]
    duplicate_tags = {tag for tag in tags if tag and tags.count(tag) > 1}
    endpoints: dict[str, Any] = {}
    for index, row in enumerate(rows, start=1):
        tag = row.get("tag")
        if not tag or tag in duplicate_tags:
            tag = f"{row.get('provider_name') or 'unknown'}#{index}"
            emit(f"missing or duplicate tag at endpoint {index}; using {tag}")
        if tag in endpoints:
            raise ValueError(f"endpoint identity collision after fallback: {tag}")
        item = {key: value for key, value in row.items() if key not in VOLATILE_FIELDS}
        item["tag"] = tag
        item["provider_name"] = row.get("provider_name") or "unknown"
        item["quantization"] = row.get("quantization") or "unknown"
        item["status"] = row.get("status", 0)
        item["supported_parameters"] = sorted(set(row.get("supported_parameters") or []))
        endpoints[tag] = item
    return {
        "schema_version": 1,
        "model_id": model_id,
        "fetched_at": fetched_at or utc_now(),
        "endpoints": dict(sorted(endpoints.items())),
    }


def snapshot_path(snapshot_dir: str | Path, model_id: str, date: str) -> Path:
    return Path(snapshot_dir) / model_id.replace("/", "__") / f"{date}.json"


def write_snapshot(snapshot_dir: str | Path, snapshot: dict[str, Any], date: str) -> Path:
    path = snapshot_path(snapshot_dir, snapshot["model_id"], date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_snapshot(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(value.get("endpoints"), dict):
        raise ValueError(f"invalid snapshot: {path}")
    return value


def list_snapshots(snapshot_dir: str | Path, model_id: str) -> list[Path]:
    folder = Path(snapshot_dir) / model_id.replace("/", "__")
    return sorted(folder.glob("*.json")) if folder.exists() else []


def endpoint_eligible(endpoint: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    quantizations = profile.get("quantizations", [])
    if endpoint.get("quantization", "unknown") not in quantizations:
        return False
    if not set(profile.get("required_parameters", [])).issubset(endpoint.get("supported_parameters", [])):
        return False
    if profile.get("healthy_only", True) and endpoint.get("status", 0) < 0:
        return False
    return True


def eligible_endpoints(snapshot: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {tag: ep for tag, ep in snapshot.get("endpoints", {}).items() if endpoint_eligible(ep, profile)}


def _field_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if before.get("quantization", "unknown") != after.get("quantization", "unknown"):
        changes.append({"class": "quantization_changed", "before": before.get("quantization", "unknown"), "after": after.get("quantization", "unknown")})
    if (before.get("status", 0) < 0) != (after.get("status", 0) < 0):
        changes.append({"class": "status_changed", "before": before.get("status", 0), "after": after.get("status", 0)})
    bp, ap = set(before.get("supported_parameters", [])), set(after.get("supported_parameters", []))
    for parameter in sorted(ap - bp):
        changes.append({"class": "parameter_added", "parameter": parameter})
    for parameter in sorted(bp - ap):
        changes.append({"class": "parameter_removed", "parameter": parameter})
    bprice, aprice = before.get("pricing", {}), after.get("pricing", {})
    if any(bprice.get(key) != aprice.get(key) for key in ("prompt", "completion")):
        changes.append({"class": "price_changed", "before": {k: bprice.get(k) for k in ("prompt", "completion")}, "after": {k: aprice.get(k) for k in ("prompt", "completion")}})
    if any(before.get(key) != after.get(key) for key in ("context_length", "max_completion_tokens")):
        changes.append({"class": "context_changed", "before": {k: before.get(k) for k in ("context_length", "max_completion_tokens")}, "after": {k: after.get(k) for k in ("context_length", "max_completion_tokens")}})
    return changes


def diff_snapshots(
    before: Mapping[str, Any], after: Mapping[str, Any],
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
    from_date: str | None = None, to_date: str | None = None,
) -> list[dict[str, Any]]:
    model_id = after.get("model_id", before.get("model_id", "unknown"))
    old, new = before.get("endpoints", {}), after.get("endpoints", {})
    common = old.keys() & new.keys()
    changes: list[dict[str, Any]] = []
    for tag in sorted(common):
        for sub in _field_changes(old[tag], new[tag]):
            changes.append({"model_id": model_id, "from": from_date, "to": to_date, "tag": tag, **sub})
    removed = {tag: old[tag] for tag in old.keys() - new.keys()}
    added = {tag: new[tag] for tag in new.keys() - old.keys()}
    removed_by: dict[str, list[str]] = {}
    added_by: dict[str, list[str]] = {}
    for tag, ep in removed.items():
        removed_by.setdefault(ep.get("provider_name", "unknown"), []).append(tag)
    for tag, ep in added.items():
        added_by.setdefault(ep.get("provider_name", "unknown"), []).append(tag)
    paired_removed: set[str] = set()
    paired_added: set[str] = set()
    for provider in sorted(removed_by.keys() & added_by.keys()):
        old_tags, new_tags = removed_by[provider], added_by[provider]
        if len(old_tags) == len(new_tags) == 1:
            old_tag, new_tag = old_tags[0], new_tags[0]
            paired_removed.add(old_tag)
            paired_added.add(new_tag)
            changes.append({"model_id": model_id, "from": from_date, "to": to_date, "class": "endpoint_replaced", "provider_name": provider, "before": old_tag, "after": new_tag, "changes": _field_changes(removed[old_tag], added[new_tag])})
    for tag in sorted(set(removed) - paired_removed):
        changes.append({"model_id": model_id, "from": from_date, "to": to_date, "class": "endpoint_removed", "tag": tag, "provider_name": removed[tag].get("provider_name"), "before": removed[tag], "after": None})
    for tag in sorted(set(added) - paired_added):
        changes.append({"model_id": model_id, "from": from_date, "to": to_date, "class": "endpoint_added", "tag": tag, "provider_name": added[tag].get("provider_name"), "before": None, "after": added[tag]})
    for name, profile in (profiles or {}).items():
        bset, aset = set(eligible_endpoints(before, profile)), set(eligible_endpoints(after, profile))
        if bset != aset:
            lost, gained = sorted(bset - aset), sorted(aset - bset)
            minimum = int(profile.get("min_eligible", 1))
            changes.append({"model_id": model_id, "from": from_date, "to": to_date, "class": "profile_eligible_changed", "profile": name, "before": len(bset), "after": len(aset), "before_tags": sorted(bset), "after_tags": sorted(aset), "lost": lost, "gained": gained, "below_min": len(aset) < minimum, "min_eligible": minimum})
    rank = {"endpoint_replaced": 0, "endpoint_removed": 1, "endpoint_added": 2, "quantization_changed": 3, "status_changed": 4, "parameter_added": 5, "parameter_removed": 6, "price_changed": 7, "context_changed": 8, "profile_eligible_changed": 9}
    changes.sort(key=lambda c: (rank.get(c["class"], 20), c.get("provider_name", ""), c.get("tag", ""), c.get("profile", "")))
    return changes
