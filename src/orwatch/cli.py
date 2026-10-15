"""Command line interface for orwatch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .config import ConfigError, load_config
from .core import (
    _provider_lists,
    _provider_selection,
    decide,
    diff_snapshots,
    eligible_endpoints,
    list_snapshots,
    normalize_response,
    read_snapshot,
    snapshot_label,
    utc_now,
    write_snapshot,
)


def fetch_endpoints(model_id: str) -> dict[str, Any]:
    from urllib.parse import quote

    author, sep, slug = model_id.partition("/")
    if not sep or not author or not slug:
        raise ValueError(f"invalid model id: {model_id}")
    url = f"https://openrouter.ai/api/v1/models/{quote(author, safe='')}/{quote(slug, safe='')}/endpoints"
    with urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid date: {value}") from exc


def _snapshot_selector(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) == 10:
        return _date(value)
    try:
        normalized = datetime.strptime(value, "%Y-%m-%dT%H%MZ").strftime("%Y-%m-%dT%H%MZ")
        if normalized == value:
            return normalized
        raise ValueError
    except ValueError as exc:
        raise ValueError(f"invalid snapshot selector: {value}") from exc


def _load_optional_config(path: str) -> dict[str, Any] | None:
    if Path(path).exists():
        return load_config(path)
    if path == "watch.toml":
        return None
    raise ConfigError(f"config file not found: {path}")


def _snapshot_dir(config: dict[str, Any] | None) -> str:
    return config["watch"]["snapshot_dir"] if config else "snapshots"


def _models(config: dict[str, Any] | None, requested: str | None) -> list[str]:
    if requested:
        return [requested]
    if config:
        return list(config["watch"]["models"])
    raise ConfigError("--model is required when no config file is available")


def _profiles(config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return config.get("profiles", {}) if config else {}


def _render_markdown(model_id: str, from_day: str, to_day: str, changes: list[dict[str, Any]], snapshots: tuple[dict[str, Any], dict[str, Any]] | None = None) -> str:
    lines = [f"## {model_id} — {snapshot_label(from_day)} → {snapshot_label(to_day)}", ""]
    for item in changes:
        kind = item["class"]
        if kind == "endpoint_replaced":
            lines.append(f"- endpoint_replaced  {item['provider_name']}: {item['before']} → {item['after']}")
            for sub in item.get("changes", []):
                if sub["class"] == "quantization_changed":
                    lines.append(f"  - quantization_changed  {sub.get('before')} → {sub.get('after')}")
                elif sub["class"].startswith("parameter_"):
                    lines.append(f"  - {sub['class']}  {sub.get('parameter')}")
                else:
                    lines.append(f"  - {sub['class']}")
        elif kind in {"endpoint_added", "endpoint_removed"}:
            lines.append(f"- {kind}   {item.get('provider_name')} ({item.get('tag')})")
        elif kind == "profile_eligible_changed":
            labels = {}
            if snapshots:
                before_eps, after_eps = snapshots[0]["endpoints"], snapshots[1]["endpoints"]
                for tag in item["lost"]:
                    labels[tag] = before_eps[tag].get("provider_name", tag)
                for tag in item["gained"]:
                    labels[tag] = after_eps[tag].get("provider_name", tag)
            lost = ", ".join(labels.get(tag, tag) for tag in item["lost"]) or "none"
            kept = ", ".join(snapshots[1]["endpoints"][tag].get("provider_name", tag) for tag in item.get("after_tags", []) if snapshots) or "none"
            line = f"- profile_eligible_changed  {item['profile']}: {item['before']} → {item['after']}  (lost: {lost}; kept: {kept})"
            if item["below_min"]:
                minimum = str(item.get("min_eligible", "below minimum"))
                line += f"  **below min_eligible={minimum}**"
            lines.append(line)
        elif kind == "pinned_dropped":
            lines.append(f"- pinned_dropped  {item['profile']}: {item['provider']} ({item['reason']})")
        elif kind == "pinned_restored":
            lines.append(f"- pinned_restored  {item['profile']}: {item['provider']}")
        else:
            lines.append(f"- {kind}  {item.get('tag', item.get('profile', ''))}")
    return "\n".join(lines).rstrip() + "\n"


def _changes_for_pair(model: str, from_path: Path, to_path: Path, profiles: dict[str, Any]):
    before, after = read_snapshot(from_path), read_snapshot(to_path)
    changes = diff_snapshots(before, after, profiles, from_path.stem, to_path.stem)
    return before, after, changes


def cmd_snapshot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.date and args.at:
        raise ValueError("--date and --at cannot be used together")
    legacy_date = _date(args.date)
    if args.at:
        try:
            stamp = datetime.strptime(args.at, "%Y-%m-%dT%H%MZ").strftime("%Y-%m-%dT%H%MZ")
            if stamp != args.at:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"invalid timestamp: {args.at}; expected YYYY-MM-DDTHHMMZ") from exc
    else:
        stamp = legacy_date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    failures = 0
    changed = False
    for model in config["watch"]["models"]:
        try:
            response = fetch_endpoints(model)
            snapshot = normalize_response(model, response, utc_now())
            paths = list_snapshots(config["watch"]["snapshot_dir"], model)
            previous = read_snapshot(paths[-1]) if paths else None
            changes = diff_snapshots(previous, snapshot) if previous is not None else []
            if previous is not None and not changes and not args.always:
                print(f"{model}: no change since {snapshot_label(paths[-1].stem)}")
                continue
            path = write_snapshot(config["watch"]["snapshot_dir"], snapshot, stamp)
            changed = True
            if previous is None:
                print(f"{model}: first snapshot → {path}")
            else:
                print(f"{model}: {len(changes)} change(s) → {path}")
        except Exception as exc:
            failures += 1
            print(f"{model}: snapshot failed: {exc}", file=sys.stderr)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")
    return 1 if failures else 0


def _select_snapshot(paths: list[Path], selector: str) -> Path | None:
    if len(selector) == 10:
        day = _date(selector)
        if day:
            matching = [path for path in paths if path.stem.startswith(f"{day}T")]
            return matching[-1] if matching else next((p for p in paths if p.stem == day), None)
    return next((path for path in paths if path.stem == selector), None)


def cmd_diff(args: argparse.Namespace) -> int:
    args.from_date = _snapshot_selector(args.from_date)
    args.to_date = _snapshot_selector(args.to_date)
    config = _load_optional_config(args.config)
    models = _models(config, args.model)
    profiles = _profiles(config)
    snapshot_dir = _snapshot_dir(config)
    fail_classes = set(filter(None, (args.fail_on or "").split(",")))
    all_changes: list[dict[str, Any]] = []
    rendered: list[str] = []
    for model in models:
        paths = list_snapshots(snapshot_dir, model)
        if args.from_date or args.to_date:
            if not args.from_date or not args.to_date:
                raise ValueError("--from and --to must be supplied together")
            first = _select_snapshot(paths, args.from_date)
            second = _select_snapshot(paths, args.to_date)
            if first is None or second is None:
                raise ValueError(f"snapshot not found for {model} and requested dates")
        else:
            if not paths:
                raise ValueError(f"no snapshots for {model}")
            if len(paths) == 1:
                print(f"{model}: only one snapshot ({snapshot_label(paths[0].stem)}); nothing to compare")
                continue
            first, second = paths[-2:]
        before, after, changes = _changes_for_pair(model, first, second, profiles)
        all_changes.extend(changes)
        rendered.append(_render_markdown(model, first.stem, second.stem, changes, (before, after)))
    if args.format == "json":
        print(json.dumps(all_changes, indent=2, ensure_ascii=False))
    else:
        print("\n".join(rendered), end="")
    return 2 if any(item["class"] in fail_classes or (item["class"] == "endpoint_replaced" and any(sub["class"] in fail_classes for sub in item.get("changes", []))) for item in all_changes) else 0


def _profile_for_check(args: argparse.Namespace, config: dict[str, Any] | None) -> dict[str, Any]:
    adhoc_given = any(value is not None for value in (args.quant, args.require, args.min, args.pinned, args.backup))
    if args.profile and adhoc_given:
        raise ConfigError("use either --profile or the ad-hoc flags")
    if args.profile:
        if config is None:
            raise ConfigError(f"profile requires a config file: {args.config}")
        profiles = config.get("profiles", {})
        if args.profile not in profiles:
            raise ConfigError(f"unknown profile: {args.profile}")
        return profiles[args.profile]
    if args.quant is None and args.min is None:
        raise ConfigError("use either --profile or the ad-hoc flags (--quant and --min)")
    if args.quant is None or args.min is None:
        raise ConfigError("ad-hoc check requires both --quant and --min")
    return {
        "quantizations": [v for v in args.quant.split(",") if v],
        "required_parameters": [v for v in (args.require or "").split(",") if v],
        "min_eligible": args.min,
        "healthy_only": True,
        "pinned": [v for v in (args.pinned or "").split(",") if v],
        "backup": [v for v in (args.backup or "").split(",") if v],
    }


def _decision_clock(decision) -> str:
    if not decision.checked_at:
        return ""
    try:
        value = datetime.fromisoformat(decision.checked_at.replace("Z", "+00:00"))
    except ValueError:
        return decision.checked_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _decision_source(decision) -> str:
    if decision.source == "live":
        stamp = _decision_clock(decision)
        return f"live {stamp}" if stamp else "live"
    return f"snapshot {decision.source}"


def _pinned_status_rows(snapshot: dict[str, Any], profile: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    pinned, backups = _provider_lists(profile)
    rows = []
    for kind, selectors in (("pinned", pinned), ("backup", backups)):
        for selector in selectors:
            selection = _provider_selection(snapshot, profile, selector)
            if selection["usable"]:
                tags = ", ".join(tag for tag, _ in selection["usable_endpoints"])
                rows.append((kind, selection["provider"], "usable", tags))
            else:
                rows.append((kind, selection["provider"], "dropped", selection["reason"] or "not listed"))
    return rows


def _render_check_text(model: str, snapshot: dict[str, Any], profile: dict[str, Any], decision) -> str:
    pinned, _ = _provider_lists(profile)
    lines: list[str] = []
    minimum = decision.min_eligible
    if pinned:
        lines.append(f"{model}: {decision.status.upper()} · {_decision_source(decision)}")
        rows = _pinned_status_rows(snapshot, profile)
        name_width = max((len(provider) for _, provider, _, _ in rows), default=0)
        for kind, provider, state, detail in rows:
            lines.append(f"  {kind:<6}  {provider:<{name_width}}  {state:<7}  {detail}")
        if decision.status == "blocked":
            provider_word = "provider" if len(decision.providers) == 1 else "providers"
            lines.append(f"  → {len(decision.providers)} usable {provider_word}, need {minimum} · not running")
        elif decision.status == "degraded":
            dropped = [row[1] for row in rows if row[0] == "pinned" and row[2] == "dropped"]
            suffix = f" ({', '.join(dropped)} dropped)" if dropped else ""
            if decision.backup_used:
                suffix = f" (backup: {', '.join(decision.backup_used)}{'; ' + ', '.join(dropped) + ' dropped' if dropped else ''})"
            lines.append(f"  → running on {', '.join(decision.providers)}{suffix}")
        else:
            lines.append(f"  → running on {', '.join(decision.providers)}")
    else:
        status = decision.status.upper()
        suffix = (
            f" · {status} · live {_decision_clock(decision)}"
            if decision.source == "live"
            else f" (snapshot {decision.source}) · {status}"
        )
        lines.append(f"{model}: {decision.eligible_endpoints} eligible endpoint(s) (minimum {minimum}){suffix}")
        eligible = eligible_endpoints(snapshot, profile)
        for tag, endpoint in eligible.items():
            lines.append(
                f"  {endpoint.get('provider_name', 'unknown')}  {tag}  "
                f"{endpoint.get('quantization', 'unknown')}  status {endpoint.get('status', 0)}"
            )
    return "\n".join(lines)


def cmd_check(args: argparse.Namespace) -> int:
    config = _load_optional_config(args.config)
    profile = _profile_for_check(args, config)
    adhoc = args.profile is None
    if adhoc and not args.model:
        raise ConfigError("--model is required for an ad-hoc check")
    if adhoc and args.offline and config is None:
        raise ConfigError("offline ad-hoc check requires a config file with snapshot_dir")
    models = [args.model] if adhoc else _models(config, args.model)
    snapshot_dir = _snapshot_dir(config)
    decisions = []
    for model in models:
        if args.offline:
            paths = list_snapshots(snapshot_dir, model)
            if not paths:
                raise ValueError(f"no snapshots for {model}")
            snapshot = read_snapshot(paths[-1])
            source_label = snapshot_label(paths[-1].stem)
        else:
            snapshot = normalize_response(model, fetch_endpoints(model), utc_now())
            source_label = "live"
        decisions.append((snapshot, decide(snapshot, profile, source_label=source_label)))

    if args.record:
        record_path = Path(args.record)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        with record_path.open("a", encoding="utf-8") as stream:
            for _, decision in decisions:
                stream.write(json.dumps(decision.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")

    if args.format == "json":
        print(json.dumps([decision.to_dict() for _, decision in decisions], indent=2, ensure_ascii=False))
    else:
        for model, (snapshot, decision) in zip(models, decisions):
            print(_render_check_text(model, snapshot, profile, decision))

    degraded = [(model, decision) for model, (_, decision) in zip(models, decisions) if decision.status == "degraded"]
    blocked = any(decision.status == "blocked" for _, decision in decisions)
    for model, decision in degraded:
        print(f"warning: {model} is degraded; routing through {', '.join(decision.providers)}", file=sys.stderr)
    if blocked:
        return 7
    if degraded and args.strict:
        return 3
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    config = _load_optional_config(args.config)
    models = _models(config, args.model)
    profiles = _profiles(config)
    if args.profile:
        if args.profile not in profiles:
            raise ConfigError(f"unknown profile: {args.profile}")
        profiles = {args.profile: profiles[args.profile]}
    snapshot_dir = _snapshot_dir(config)
    chunks = [
        "# OpenRouter endpoint history",
        "",
        f"Generated by orwatch from the snapshots in `{snapshot_dir}/`. Newest first.",
        "",
    ]
    for model in models:
        paths = list_snapshots(snapshot_dir, model)
        chunks.extend([f"## {model}", ""])
        if not paths:
            chunks.extend(["No snapshots yet.", ""])
            continue
        chunks.extend([
            f"Tracking since {snapshot_label(paths[0].stem)} · {len(paths)} change snapshot{'s' if len(paths) != 1 else ''} · latest {snapshot_label(paths[-1].stem)}",
            "",
        ])
        pairs = []
        for left, right in zip(paths, paths[1:]):
            before, after, changes = _changes_for_pair(model, left, right, profiles)
            if changes:
                rendered = _render_markdown(model, left.stem, right.stem, changes, (before, after))
                body = rendered.split("\n", 1)[1]
                pairs.append(f"### {snapshot_label(left.stem)} → {snapshot_label(right.stem)}\n{body}")
        if pairs:
            chunks.extend(["\n".join(reversed(pairs)), ""])
        else:
            chunks.extend(["No changes recorded yet.", ""])
    output = "\n".join(chunks).rstrip() + "\n"
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orwatch", description="Track OpenRouter endpoint metadata")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--config", default="watch.toml")
    snapshot.add_argument("--date")
    snapshot.add_argument("--at")
    snapshot.add_argument("--always", action="store_true")
    snapshot.set_defaults(func=cmd_snapshot)
    diff = sub.add_parser("diff")
    diff.add_argument("--config", default="watch.toml")
    diff.add_argument("--model")
    diff.add_argument("--from", dest="from_date")
    diff.add_argument("--to", dest="to_date")
    diff.add_argument("--format", choices=("md", "json"), default="md")
    diff.add_argument("--fail-on", default="")
    diff.set_defaults(func=cmd_diff)
    check = sub.add_parser("check")
    check.add_argument("--config", default="watch.toml")
    check.add_argument("--profile")
    check.add_argument("--model")
    check.add_argument("--offline", action="store_true")
    check.add_argument("--quant")
    check.add_argument("--require")
    check.add_argument("--min", type=int)
    check.add_argument("--pinned")
    check.add_argument("--backup")
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument("--strict", action="store_true")
    check.add_argument("--record")
    check.set_defaults(func=cmd_check)
    history = sub.add_parser("history")
    history.add_argument("--config", default="watch.toml")
    history.add_argument("--model")
    history.add_argument("--profile")
    history.add_argument("--out")
    history.set_defaults(func=cmd_history)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ValueError, OSError, URLError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
