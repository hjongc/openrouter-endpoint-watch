import copy
import json

import pytest

from orwatch.cli import main
from orwatch import ProviderSetBlocked, check
from orwatch.config import ConfigError, load_config
from orwatch.core import CHANGE_CLASSES, decide, diff_snapshots, eligible_endpoints, list_snapshots, normalize_response, read_snapshot

FIXTURE = "tests/fixtures/glm-5.3-flash.endpoints.2026-10-15.json"
MODEL = "z-ai/glm-5.3-flash"


def snapshot(endpoints):
    return {"schema_version": 1, "model_id": MODEL, "fetched_at": "2026-10-15T00:00:00Z", "endpoints": endpoints}


def endpoint(tag, provider="A", quant="fp8", status=0, params=None, **extra):
    return {"tag": tag, "provider_name": provider, "quantization": quant, "status": status,
            "supported_parameters": sorted(params or []), "pricing": {"prompt": "1", "completion": "2"},
            "context_length": 100, "max_completion_tokens": 10, **extra}


def test_snapshot_normalization_and_duplicate_fallback():
    warnings = []
    result = normalize_response("a/b", {"data": {"endpoints": [
        {"tag": "dup", "provider_name": "P", "quantization": None, "supported_parameters": ["z", "a"], "latency_last_30m": 2, "uptime_last_5m": 99},
        {"tag": "dup", "provider_name": "Q", "supported_parameters": []},
        {"provider_name": "R", "supported_parameters": []},
    ]}}, "2026-01-01T00:00:00Z", warnings.append)
    assert set(result["endpoints"]) == {"P#1", "Q#2", "R#3"}
    assert result["endpoints"]["P#1"]["quantization"] == "unknown"
    assert result["endpoints"]["P#1"]["supported_parameters"] == ["a", "z"]
    assert "latency_last_30m" not in result["endpoints"]["P#1"]
    assert "uptime_last_5m" not in result["endpoints"]["P#1"]
    assert len(warnings) == 3


@pytest.mark.parametrize(("before", "after", "expected"), [
    ({}, {"x": endpoint("x")}, "endpoint_added"),
    ({"x": endpoint("x")}, {}, "endpoint_removed"),
    ({"x": endpoint("x", quant="fp4")}, {"x": endpoint("x", quant="fp8")}, "quantization_changed"),
    ({"x": endpoint("x", status=0)}, {"x": endpoint("x", status=-1)}, "status_changed"),
    ({"x": endpoint("x", params=[])}, {"x": endpoint("x", params=["a"])}, "parameter_added"),
    ({"x": endpoint("x", params=["a"])}, {"x": endpoint("x", params=[])}, "parameter_removed"),
    ({"x": endpoint("x")}, {"x": endpoint("x", pricing={"prompt": "3", "completion": "2"})}, "price_changed"),
    ({"x": endpoint("x")}, {"x": endpoint("x", context_length=101)}, "context_changed"),
])
def test_diff_change_classes(before, after, expected):
    classes = [c["class"] for c in diff_snapshots(snapshot(before), snapshot(after))]
    assert expected in classes


def test_replacement_and_ambiguous_provider_not_paired():
    before = snapshot({"a/fp8": endpoint("a/fp8", "A", "fp8"), "b/fp8": endpoint("b/fp8", "B")})
    after = snapshot({"a/fp4": endpoint("a/fp4", "A", "fp4"), "b/fp4": endpoint("b/fp4", "B")})
    changes = diff_snapshots(before, after)
    replacement = next(c for c in changes if c["class"] == "endpoint_replaced")
    assert replacement["changes"][0]["class"] == "quantization_changed"
    ambiguous = snapshot({"a/fp4": endpoint("a/fp4", "A"), "a/other": endpoint("a/other", "A")})
    changes = diff_snapshots(snapshot({"a/fp8": endpoint("a/fp8", "A")}), ambiguous)
    assert not any(c["class"] == "endpoint_replaced" for c in changes)
    assert sum(c["class"] == "endpoint_added" for c in changes) == 2
    assert sum(c["class"] == "endpoint_removed" for c in changes) == 1


def test_profile_unknown_and_health():
    snap = snapshot({"u": endpoint("u", quant="unknown"), "bad": endpoint("bad", status=-1)})
    profile = {"quantizations": ["fp8"], "required_parameters": [], "healthy_only": True}
    assert not eligible_endpoints(snap, profile)
    profile["quantizations"] = ["unknown"]
    assert list(eligible_endpoints(snap, profile)) == ["u"]
    profile["quantizations"] = ["fp8", "unknown"]
    profile["healthy_only"] = False
    assert set(eligible_endpoints(snap, profile)) == {"u", "bad"}


def test_incident_2026_10_08_parasail_fp4_nextbit_gone():
    raw = json.load(open(FIXTURE, encoding="utf-8"))["data"]["endpoints"]
    after = normalize_response(MODEL, {"data": {"endpoints": copy.deepcopy(raw)}})
    before_endpoints = copy.deepcopy(after["endpoints"])
    parasail = before_endpoints.pop("parasail/fp4")
    parasail["tag"] = "parasail/fp8"
    parasail["quantization"] = "fp8"
    before_endpoints["parasail/fp8"] = parasail
    before_endpoints["nextbit/fp8"] = endpoint("nextbit/fp8", "NextBit", "fp8", params=["seed", "response_format", "tools", "tool_choice"])
    before = snapshot(before_endpoints)
    profile = {"quantizations": ["fp8"], "required_parameters": ["seed", "response_format", "tools", "tool_choice"], "min_eligible": 5}
    changes = diff_snapshots(before, after, {"production": profile})
    replacement = next(c for c in changes if c["class"] == "endpoint_replaced")
    assert replacement["provider_name"] == "Parasail"
    assert replacement["changes"][0]["class"] == "quantization_changed"
    assert replacement["changes"][0]["before"] == "fp8" and replacement["changes"][0]["after"] == "fp4"
    assert any(c["class"] == "endpoint_removed" and c["tag"] == "nextbit/fp8" for c in changes)
    eligibility = next(c for c in changes if c["class"] == "profile_eligible_changed")
    assert eligibility["below_min"] is True


def test_cli_exit_codes_and_config_validation(tmp_path, monkeypatch):
    before = snapshot({"old": endpoint("old", "A")})
    after = snapshot({"new": endpoint("new", "B")})
    snapdir = tmp_path / "snaps" / "a__b"
    snapdir.mkdir(parents=True)
    (snapdir / "2026-01-01.json").write_text(json.dumps(before))
    (snapdir / "2026-01-02.json").write_text(json.dumps(after))
    config = tmp_path / "watch.toml"
    config.write_text(f'''[watch]\nmodels=["a/b"]\nsnapshot_dir="{tmp_path / "snaps"}"\n[profiles.p]\nquantizations=["fp8"]\nrequired_parameters=[]\nmin_eligible=2\n''')
    assert main(["diff", "--config", str(config), "--fail-on", "endpoint_added"]) == 2
    monkeypatch.setattr("orwatch.cli.fetch_endpoints", lambda model: {"data": {"endpoints": [endpoint("good", "A"), endpoint("good2", "B")]}})
    assert main(["check", "--config", str(config), "--profile", "p"]) == 0
    assert main(["check", "--config", str(config), "--profile", "p", "--offline"]) == 7
    bad = tmp_path / "bad.toml"
    bad.write_text('[watch]\nmodels=[]\nsnapshot_dir="x"\n')
    with pytest.raises(ConfigError):
        load_config(bad)


def test_adhoc_check_with_default_config_uses_flags_and_returns_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watch.toml").write_text(
        '[watch]\nmodels=["configured/model"]\nsnapshot_dir="snaps"\n', encoding="utf-8"
    )
    fetched = []
    monkeypatch.setattr(
        "orwatch.cli.fetch_endpoints",
        lambda model: fetched.append(model) or {"data": {"endpoints": [
            endpoint("one", params=["seed", "response_format", "tools", "tool_choice"]),
            endpoint("two", provider="B", params=["seed", "response_format", "tools", "tool_choice"]),
        ]}},
    )

    result = main(["check", "--model", MODEL, "--quant", "fp8", "--require",
                   "seed,response_format,tools,tool_choice", "--min", "2"])

    assert result == 0
    assert fetched == [MODEL]
    assert f"{MODEL}: 2 eligible endpoint(s) (minimum 2)" in capsys.readouterr().out


def test_adhoc_check_with_default_config_returns_seven_below_minimum(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watch.toml").write_text(
        '[watch]\nmodels=["configured/model"]\nsnapshot_dir="snaps"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        "orwatch.cli.fetch_endpoints",
        lambda model: {"data": {"endpoints": [endpoint("one", params=["seed"])]}},
    )

    result = main(["check", "--model", MODEL, "--quant", "fp8", "--require", "seed", "--min", "2"])

    assert result == 7
    output = capsys.readouterr()
    assert "1 eligible endpoint(s) (minimum 2)" in output.out


def test_adhoc_check_without_config_uses_explicit_model(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fetched = []
    monkeypatch.setattr(
        "orwatch.cli.fetch_endpoints",
        lambda model: fetched.append(model) or {"data": {"endpoints": [endpoint("one")]}},
    )

    result = main(["check", "--model", MODEL, "--quant", "fp8", "--min", "1"])

    assert result == 0
    assert fetched == [MODEL]
    assert f"{MODEL}: 1 eligible endpoint" in capsys.readouterr().out


def test_adhoc_check_requires_model_even_when_config_has_models(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watch.toml").write_text(
        '[watch]\nmodels=["configured/model"]\nsnapshot_dir="snaps"\n', encoding="utf-8"
    )

    result = main(["check", "--quant", "fp8", "--min", "1"])

    assert result == 1
    assert "--model is required for an ad-hoc check" in capsys.readouterr().err


def test_check_rejects_profile_and_adhoc_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watch.toml").write_text(
        '[watch]\nmodels=["a/b"]\nsnapshot_dir="snaps"\n[profiles.p]\nquantizations=["fp8"]\n',
        encoding="utf-8",
    )

    result = main(["check", "--profile", "p", "--quant", "fp8", "--min", "1"])

    assert result == 1
    assert "use either --profile or the ad-hoc flags" in capsys.readouterr().err


def test_check_requires_profile_or_adhoc_form(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "watch.toml").write_text(
        '[watch]\nmodels=["a/b"]\nsnapshot_dir="snaps"\n', encoding="utf-8"
    )

    result = main(["check"])

    assert result == 1
    assert "either --profile or the ad-hoc flags" in capsys.readouterr().err


def test_offline_adhoc_check_uses_config_snapshot_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    snapshot_dir = tmp_path / "saved"
    snapdir = snapshot_dir / MODEL.replace("/", "__")
    snapdir.mkdir(parents=True)
    (snapdir / "2026-10-15.json").write_text(
        json.dumps(snapshot({"ok": endpoint("ok")})), encoding="utf-8"
    )
    (tmp_path / "watch.toml").write_text(
        f'[watch]\nmodels=["configured/model"]\nsnapshot_dir="{snapshot_dir}"\n', encoding="utf-8"
    )

    result = main(["check", "--model", MODEL, "--quant", "fp8", "--min", "1", "--offline"])

    assert result == 0
    output = capsys.readouterr().out
    assert f"{MODEL}: 1 eligible endpoint" in output
    assert "(snapshot 2026-10-15)" in output


def test_diff_and_history_with_one_snapshot(tmp_path, capsys):
    snapdir = tmp_path / "snaps" / "a__b"
    snapdir.mkdir(parents=True)
    (snapdir / "2026-10-15.json").write_text(json.dumps(snapshot({"x": endpoint("x")})))
    config = tmp_path / "watch.toml"
    config.write_text(f'''[watch]\nmodels=["a/b"]\nsnapshot_dir="{tmp_path / "snaps"}"\n''')
    history_file = tmp_path / "HISTORY.md"

    assert main(["diff", "--config", str(config)]) == 0
    assert capsys.readouterr().out.strip() == "a/b: only one snapshot (2026-10-15); nothing to compare"
    assert main(["history", "--config", str(config), "--out", str(history_file)]) == 0
    assert capsys.readouterr().out == ""
    output = history_file.read_text(encoding="utf-8")
    assert "# OpenRouter endpoint history" in output
    assert f"Generated by orwatch from the snapshots in `{tmp_path / 'snaps'}/`. Newest first." in output
    assert "## a/b\n\nTracking since 2026-10-15 · 1 change snapshot · latest 2026-10-15\n\nNo changes recorded yet." in output


def test_history_examples_uses_model_and_day_headings(capsys):
    assert main(["history", "--config", "examples/watch.toml"]) == 0
    output = capsys.readouterr().out
    assert "Generated by orwatch from the snapshots in `examples/snapshots/`. Newest first." in output
    assert "## z-ai/glm-5.3-flash\n\nTracking since 2026-10-07 · 2 change snapshots · latest 2026-10-08" in output
    assert "### 2026-10-07 → 2026-10-08\n\n- endpoint_replaced  Parasail: parasail/fp8 → parasail/fp4" in output
    assert "  - quantization_changed  fp8 → fp4" in output
    assert "- endpoint_removed   NextBit (nextbit/fp8)" in output
    assert "- profile_eligible_changed  production: 3 → 1" in output


def test_history_with_zero_snapshots_emits_section_and_succeeds(tmp_path, capsys):
    config = tmp_path / "watch.toml"
    config.write_text(
        f'''[watch]\nmodels=["a/b"]\nsnapshot_dir="{tmp_path / "empty"}"\n''',
        encoding="utf-8",
    )

    assert main(["history", "--config", str(config)]) == 0
    output = capsys.readouterr().out
    assert "## a/b\n\nNo snapshots yet." in output


def _snapshot_cli_config(tmp_path):
    snaproot = tmp_path / "snapshots"
    config = tmp_path / "watch.toml"
    config.write_text(
        f'[watch]\nmodels=["{MODEL}"]\nsnapshot_dir="{snaproot}"\n',
        encoding="utf-8",
    )
    return config, snaproot


def test_snapshot_skips_unchanged_and_uptime_only_changes(tmp_path, monkeypatch, capsys):
    config, snaproot = _snapshot_cli_config(tmp_path)
    folder = snaproot / MODEL.replace("/", "__")
    folder.mkdir(parents=True)
    stored = snapshot({"one": endpoint("one", uptime_last_1d=90)})
    (folder / "2026-10-15T0200Z.json").write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(
        "orwatch.cli.fetch_endpoints",
        lambda model: {"data": {"endpoints": [endpoint("one", uptime_last_1d=99)]}},
    )
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert main(["snapshot", "--config", str(config), "--at", "2026-10-15T0317Z"]) == 0
    assert capsys.readouterr().out.strip() == f"{MODEL}: no change since 2026-10-15 02:00 UTC"
    assert [path.name for path in list_snapshots(snaproot, MODEL)] == ["2026-10-15T0200Z.json"]
    assert output.read_text(encoding="utf-8") == "changed=false\n"


def test_snapshot_changed_timestamp_always_and_github_output(tmp_path, monkeypatch, capsys):
    config, snaproot = _snapshot_cli_config(tmp_path)
    folder = snaproot / MODEL.replace("/", "__")
    folder.mkdir(parents=True)
    (folder / "2026-10-15.json").write_text(
        json.dumps(snapshot({"one": endpoint("one")})), encoding="utf-8"
    )
    monkeypatch.setattr(
        "orwatch.cli.fetch_endpoints",
        lambda model: {"data": {"endpoints": [endpoint("one", quant="fp4")]}},
    )
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert main(["snapshot", "--config", str(config), "--at", "2026-10-15T0317Z"]) == 0
    message = capsys.readouterr().out.strip()
    assert "1 change(s)" in message and message.endswith("2026-10-15T0317Z.json")
    assert [path.name for path in list_snapshots(snaproot, MODEL)] == [
        "2026-10-15.json", "2026-10-15T0317Z.json"
    ]
    assert output.read_text(encoding="utf-8") == "changed=true\n"

    assert main(["snapshot", "--config", str(config), "--at", "2026-10-15T0417Z", "--always"]) == 0
    assert "0 change(s)" in capsys.readouterr().out
    assert output.read_text(encoding="utf-8") == "changed=true\nchanged=true\n"


def test_snapshot_first_write_and_legacy_date_option(tmp_path, monkeypatch, capsys):
    config, snaproot = _snapshot_cli_config(tmp_path)
    monkeypatch.setattr(
        "orwatch.cli.fetch_endpoints",
        lambda model: {"data": {"endpoints": [endpoint("one")]}},
    )

    assert main(["snapshot", "--config", str(config), "--date", "2026-10-15"]) == 0
    assert capsys.readouterr().out.strip().endswith("first snapshot → " + str(
        snaproot / MODEL.replace("/", "__") / "2026-10-15.json"
    ))


def test_date_selector_uses_last_snapshot_of_day_and_history_labels(tmp_path, capsys):
    config, snaproot = _snapshot_cli_config(tmp_path)
    folder = snaproot / MODEL.replace("/", "__")
    folder.mkdir(parents=True)
    for stem, ep in (
        ("2026-10-14", endpoint("a")),
        ("2026-10-15T0317Z", endpoint("b")),
        ("2026-10-15T0917Z", endpoint("c")),
        ("2026-10-16T0317Z", endpoint("d")),
    ):
        (folder / f"{stem}.json").write_text(json.dumps(snapshot({ep["tag"]: ep})), encoding="utf-8")

    assert main(["diff", "--config", str(config), "--from", "2026-10-15", "--to", "2026-10-16"]) == 0
    output = capsys.readouterr().out
    assert "2026-10-15 09:17 UTC → 2026-10-16 03:17 UTC" in output

    assert main(["diff", "--config", str(config), "--from", "2026-10-14", "--to", "2026-10-15"]) == 0
    assert "2026-10-14 → 2026-10-15 09:17 UTC" in capsys.readouterr().out

    assert main(["history", "--config", str(config)]) == 0
    history = capsys.readouterr().out
    assert "Tracking since 2026-10-14 · 4 change snapshots · latest 2026-10-16 03:17 UTC" in history
    assert "### 2026-10-15 03:17 UTC → 2026-10-15 09:17 UTC" in history


def test_decide_all_pinned_and_unpinned_branches():
    profile = {"quantizations": ["fp8"], "required_parameters": [], "pinned": ["A", "B"], "min_eligible": 2}
    all_pinned = snapshot({"a/fp8": endpoint("a/fp8", "A"), "b/fp8": endpoint("b/fp8", "B")})
    decision = decide(all_pinned, profile, source_label="live")
    assert decision.status == "ok"
    assert decision.providers == ["A", "B"]
    assert decision.route == ["a/fp8", "b/fp8"]
    assert decision.source == "live"
    assert decision.checked_at == "2026-10-15T00:00:00Z"
    expected_preferences = {
        "order": ["a/fp8", "b/fp8"], "allow_fallbacks": False,
        "require_parameters": True, "quantizations": ["fp8"],
    }
    assert decision.provider_preferences() == expected_preferences
    assert decision.provider_preferences(profile) == expected_preferences
    assert decision.provider_preferences({"quantizations": ["fp4"]})["quantizations"] == ["fp4"]
    assert decision.to_dict()["quantizations"] == ["fp8"]

    degraded = snapshot({"a/fp8": endpoint("a/fp8", "A"), "b/fp4": endpoint("b/fp4", "B", quant="fp4")})
    decision = decide(degraded, {**profile, "min_eligible": 1}, source_label="2026-10-15")
    assert decision.status == "degraded"
    assert decision.providers == ["A"]
    assert decision.dropped == [{"provider": "B", "reason": "quantization fp4 not in fp8"}]

    with_backup = snapshot({
        "a/fp8": endpoint("a/fp8", "A"),
        "b/fp4": endpoint("b/fp4", "B", quant="fp4"),
        "c/fp8": endpoint("c/fp8", "C"),
    })
    backup_profile = {**profile, "pinned": ["A", "B"], "backup": ["C"]}
    decision = decide(with_backup, backup_profile, source_label="x")
    assert decision.status == "degraded"
    assert decision.providers == ["A", "C"]
    assert decision.route == ["a/fp8", "c/fp8"]
    assert decision.backup_used == ["C"]

    blocked = snapshot({"b/fp4": endpoint("b/fp4", "B", quant="fp4"), "c/fp8": endpoint("c/fp8", "C")})
    decision = decide(blocked, backup_profile, source_label="x")
    assert decision.status == "blocked"
    assert decision.providers == ["C"]
    assert decision.route == ["c/fp8"]
    assert decision.backup_used == ["C"]

    no_pins = snapshot({"b/fp8": endpoint("b/fp8", "Beta"), "a/fp8": endpoint("a/fp8", "Alpha")})
    decision = decide(no_pins, {"quantizations": ["fp8"], "min_eligible": 2}, source_label="x")
    assert decision.status == "ok"
    assert decision.providers == ["Alpha", "Beta"]
    assert decision.route == ["b/fp8", "a/fp8"]
    assert decision.eligible_endpoints == 2


def test_decide_matches_provider_name_full_tag_and_base_slug():
    snap = snapshot({
        "base/fp8": endpoint("base/fp8", "Case Provider", "fp8"),
        "base/fp4": endpoint("base/fp4", "Case Provider", "fp4"),
        "other/fp8": endpoint("other/fp8", "Other", "fp8"),
    })
    profile = {"quantizations": ["fp8"], "pinned": ["case provider", "other/fp8", "base"], "min_eligible": 1}
    decision = decide(snap, profile, source_label="live")
    assert decision.status == "ok"
    assert decision.providers == ["Case Provider", "Other"]
    assert decision.route == ["base/fp8", "other/fp8"]
    assert all("/" in tag for tag in decision.route)


def test_decide_reason_precedence_and_json_shape():
    snap = snapshot({
        "q/fp4": endpoint("q/fp4", "Quant", "fp4"),
        "m/fp8": endpoint("m/fp8", "Missing", "fp8", params=["a", "b"]),
        "m/fp8/us": endpoint("m/fp8/us", "Missing", "fp8", params=["b"]),
        "h/fp8": endpoint("h/fp8", "Unhealthy", "fp8", status=-2, params=["a", "b", "c"]),
    })
    profile = {"quantizations": ["fp8"], "required_parameters": ["a", "b", "c"],
               "pinned": ["Absent", "Quant", "Missing", "Unhealthy"], "min_eligible": 1}
    decision = decide(snap, profile, source_label="2026-10-15")
    assert decision.dropped == [
        {"provider": "Absent", "reason": "not listed"},
        {"provider": "Quant", "reason": "quantization fp4 not in fp8"},
        {"provider": "Missing", "reason": "missing c"},
        {"provider": "Unhealthy", "reason": "unhealthy (status -2)"},
    ]
    payload = decision.to_dict()
    assert payload["source"] == "2026-10-15"
    assert payload["dropped"] == decision.dropped
    assert CHANGE_CLASSES[-2:] == ("pinned_dropped", "pinned_restored")


def test_example_routing_decisions_for_october_7_and_8():
    config = load_config("examples/watch.toml")
    profile = config["profiles"]["production"]
    base = "examples/snapshots/z-ai__glm-5.3-flash"
    october_7 = read_snapshot(f"{base}/2026-10-07.json")
    decision = decide(october_7, profile, source_label="2026-10-07")
    assert decision.status == "degraded"
    assert decision.providers == ["Parasail", "NextBit"]
    assert decision.route == ["parasail/fp8", "nextbit/fp8"]
    assert {item["provider"]: item["reason"] for item in decision.dropped} == {
        "Reka": "quantization unknown not in fp8",
        "Inceptron": "missing seed",
    }

    october_8 = read_snapshot(f"{base}/2026-10-08.json")
    decision = decide(october_8, profile, source_label="2026-10-08")
    assert decision.status == "blocked"
    assert decision.providers == ["Near AI"]
    assert decision.route == ["near-ai/fp8"]
    assert decision.backup_used == ["Near AI"]
    assert {item["provider"]: item["reason"] for item in decision.dropped} == {
        "Reka": "quantization unknown not in fp8",
        "Parasail": "quantization fp4 not in fp8",
        "NextBit": "not listed",
        "Inceptron": "missing seed",
    }


def test_python_api_fetch_cache_and_blocked_exception(tmp_path):
    from orwatch import _CHECK_CACHE

    _CHECK_CACHE.clear()
    profile = {"quantizations": ["fp8"], "pinned": ["A"], "min_eligible": 1}
    calls = []

    def fake_fetch(model):
        calls.append(model)
        return {"data": {"endpoints": [endpoint("a/fp8", "A")]}}

    first = check(MODEL, profile, config=None, fetch=fake_fetch, max_age=60)
    first.route.clear()
    second = check(MODEL, profile, config=None, fetch=fake_fetch, max_age=60)
    assert calls == [MODEL]
    assert second.route == ["a/fp8"]

    check(MODEL, profile, config=None, fetch=fake_fetch, max_age=0)
    check(MODEL, profile, config=None, fetch=fake_fetch, max_age=0)
    assert calls == [MODEL, MODEL, MODEL]

    with pytest.raises(ProviderSetBlocked) as caught:
        check(MODEL, {"quantizations": ["fp8"], "pinned": ["Missing"], "min_eligible": 1},
              config=None, fetch=lambda model: {"data": {"endpoints": []}}, max_age=0,
              raise_on_blocked=True)
    assert caught.value.decision.status == "blocked"


def test_python_api_defaults_to_first_configured_model_offline(tmp_path):
    from orwatch.core import write_snapshot

    snapshot_dir = tmp_path / "snapshots"
    write_snapshot(snapshot_dir, snapshot({"a/fp8": endpoint("a/fp8", "A")}), "2026-10-15")
    config = tmp_path / "watch.toml"
    config.write_text(
        f'[watch]\nmodels=["{MODEL}","other/model"]\nsnapshot_dir="{snapshot_dir}"\n'
        '[profiles.production]\nquantizations=["fp8"]\npinned=["A"]\nmin_eligible=1\n',
        encoding="utf-8",
    )
    decision = check(profile="production", config=config, offline=True, max_age=0)
    assert decision.model_id == MODEL
    assert decision.source == "2026-10-15"


def test_check_cli_text_json_strict_record_and_exit_codes(tmp_path, capsys):
    snapdir = tmp_path / "snaps"
    folder = snapdir / "a__b"
    folder.mkdir(parents=True)
    (folder / "2026-10-15.json").write_text(json.dumps(snapshot({
        "a/fp8": endpoint("a/fp8", "A"),
        "b/fp4": endpoint("b/fp4", "B", quant="fp4"),
    })), encoding="utf-8")
    config = tmp_path / "watch.toml"
    config.write_text(
        f'[watch]\nmodels=["a/b"]\nsnapshot_dir="{snapdir}"\n'
        '[profiles.p]\nquantizations=["fp8"]\npinned=["A","B"]\nmin_eligible=1\n',
        encoding="utf-8",
    )
    record = tmp_path / "runs.jsonl"

    assert main(["check", "--config", str(config), "--profile", "p", "--offline"]) == 0
    text = capsys.readouterr()
    assert "a/b: DEGRADED" in text.out
    assert "pinned  B  dropped  quantization fp4 not in fp8" in text.out
    assert "warning: a/b is degraded" in text.err

    assert main(["check", "--config", str(config), "--profile", "p", "--offline", "--strict", "--format", "json", "--record", str(record)]) == 3
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert len(payload) == 1 and payload[0]["status"] == "degraded"
    assert payload[0]["quantizations"] == ["fp8"]
    assert len(record.read_text(encoding="utf-8").splitlines()) == 1

    blocked_profile = tmp_path / "blocked.toml"
    blocked_profile.write_text(
        f'[watch]\nmodels=["a/b"]\nsnapshot_dir="{snapdir}"\n'
        '[profiles.p]\nquantizations=["fp8"]\npinned=["B"]\nmin_eligible=1\n',
        encoding="utf-8",
    )
    assert main(["check", "--config", str(blocked_profile), "--profile", "p", "--offline"]) == 7
    assert "BLOCKED" in capsys.readouterr().out


def test_adhoc_check_pinned_and_backup_flags(monkeypatch, capsys):
    monkeypatch.setattr(
        "orwatch.cli.fetch_endpoints",
        lambda model: {"data": {"endpoints": [
            endpoint("a/fp8", "A"),
            endpoint("b/fp4", "B", quant="fp4"),
            endpoint("c/fp8", "C"),
        ]}},
    )
    result = main([
        "check", "--model", MODEL, "--quant", "fp8", "--min", "2",
        "--pinned", "A,B", "--backup", "C",
    ])
    output = capsys.readouterr()
    assert result == 0
    assert "DEGRADED" in output.out
    assert "backup: C; B dropped" in output.out
    assert "warning:" in output.err


def test_pinned_diff_classes_and_preferred_alias(tmp_path):
    before = snapshot({"p/fp8": endpoint("p/fp8", "P")})
    after = snapshot({"p/fp4": endpoint("p/fp4", "P", quant="fp4")})
    profile = {"quantizations": ["fp8"], "pinned": ["P"], "backup": ["B"]}
    dropped = diff_snapshots(before, after, {"production": profile})
    item = next(change for change in dropped if change["class"] == "pinned_dropped")
    assert item["provider"] == "P" and item["reason"] == "quantization fp4 not in fp8"
    from orwatch.cli import _render_markdown

    markdown = _render_markdown(MODEL, "2026-10-14", "2026-10-15", dropped)
    assert f"- pinned_dropped  production: P (quantization fp4 not in fp8)" in markdown
    restored = diff_snapshots(after, before, {"production": profile})
    assert any(change["class"] == "pinned_restored" and change["provider"] == "P" for change in restored)

    config = tmp_path / "alias.toml"
    config.write_text(
        '[watch]\nmodels=["a/b"]\nsnapshot_dir="snaps"\n'
        '[profiles.p]\nquantizations=["fp8"]\npreferred_providers=["P"]\n',
        encoding="utf-8",
    )
    assert load_config(config)["profiles"]["p"]["pinned"] == ["P"]


@pytest.mark.parametrize("entry", [
    'pinned = "P"',
    'backup = [1]',
    'min_eligible = true',
])
def test_config_validates_routing_profile_types(tmp_path, entry):
    config = tmp_path / "bad-routing.toml"
    config.write_text(
        f'[watch]\nmodels=["a/b"]\nsnapshot_dir="snaps"\n'
        f'[profiles.p]\nquantizations=["fp8"]\n{entry}\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config)
