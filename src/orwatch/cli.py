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
from .core import diff_snapshots, eligible_endpoints, list_snapshots, normalize_response, read_snapshot, snapshot_label, utc_now, write_snapshot


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
    adhoc_given = args.quant is not None or args.require is not None or args.min is not None
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
    return {"quantizations": [v for v in args.quant.split(",") if v], "required_parameters": [v for v in (args.require or "").split(",") if v], "min_eligible": args.min, "healthy_only": True}


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
    failures = []
    for model in models:
        if args.offline:
            paths = list_snapshots(snapshot_dir, model)
            if not paths:
                raise ValueError(f"no snapshots for {model}")
            snapshot = read_snapshot(paths[-1])
            snapshot_stamp = snapshot_label(paths[-1].stem)
        else:
            snapshot = normalize_response(model, fetch_endpoints(model), utc_now())
            snapshot_stamp = None
        eligible = eligible_endpoints(snapshot, profile)
        suffix = f" (snapshot {snapshot_stamp})" if snapshot_stamp else ""
        print(f"{model}: {len(eligible)} eligible endpoint(s) (minimum {profile.get('min_eligible', 1)}){suffix}")
        print("provider\ttag\tquantization\tstatus\tprompt\tcompletion")
        for tag, ep in sorted(eligible.items()):
            pricing = ep.get("pricing", {})
            print(f"{ep.get('provider_name', 'unknown')}\t{tag}\t{ep.get('quantization', 'unknown')}\t{ep.get('status', 0)}\t{pricing.get('prompt', '')}\t{pricing.get('completion', '')}")
        for provider in profile.get("preferred_providers", []):
            available = any(ep.get("provider_name") == provider for ep in eligible.values())
            print(f"preferred {provider}: {'eligible' if available else 'not eligible'}")
        minimum = int(profile.get("min_eligible", 1))
        if len(eligible) < minimum:
            failures.append(f"{args.profile or 'adhoc'}: {len(eligible)} eligible endpoint for {model}, min_eligible={minimum}")
    if failures:
        for reason in failures:
            print(reason, file=sys.stderr)
        return 7
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
