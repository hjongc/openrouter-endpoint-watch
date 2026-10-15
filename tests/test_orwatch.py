import copy
import json

import pytest

from orwatch.cli import main
from orwatch.config import ConfigError, load_config
from orwatch.core import diff_snapshots, eligible_endpoints, normalize_response

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
    assert "adhoc: 1 eligible endpoint" in capsys.readouterr().err


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
    assert f"{MODEL}: 1 eligible endpoint" in capsys.readouterr().out


def test_diff_and_history_with_one_snapshot(tmp_path, capsys):
    snapdir = tmp_path / "snaps" / "a__b"
    snapdir.mkdir(parents=True)
    (snapdir / "2026-10-15.json").write_text(json.dumps(snapshot({"x": endpoint("x")})))
    config = tmp_path / "watch.toml"
    config.write_text(f'''[watch]\nmodels=["a/b"]\nsnapshot_dir="{tmp_path / "snaps"}"\n''')

    assert main(["diff", "--config", str(config)]) == 0
    assert capsys.readouterr().out.strip() == "a/b: only one snapshot (2026-10-15); nothing to compare"
    assert main(["history", "--config", str(config)]) == 0
    assert capsys.readouterr().out == ""
