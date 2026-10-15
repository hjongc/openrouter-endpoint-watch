# OpenRouter Endpoint Watch

OpenRouter endpoint metadata changes over time, but its public endpoint list has no history or change feed. In late September 2026, a provider's quantization became `unknown`, silently reducing the providers that met a strict production profile. On October 8, Parasail changed GLM 5.3 Flash from fp8 to fp4 only and NextBit disappeared. The pre-run gate correctly stopped, but the change was discovered only when production and evaluation could no longer run. This project keeps dated snapshots, names meaningful changes, and checks profiles before a run.

## Install

Python 3.11 or newer is required. Runtime code uses only the standard library.

```bash
python -m pip install .
```

## Quickstart

The examples are self-contained and need no network:

```bash
python -m orwatch diff --config examples/watch.toml
python -m orwatch check --config examples/watch.toml --profile production --offline
```

The first command displays the Parasail quantization replacement, the removed NextBit endpoint, and the production profile's eligibility change from 3 eligible endpoints to 1. The offline check exits with status 7 because the current example has fewer eligible endpoints than its configured minimum.

## Configuration

```toml
[watch]
models = ["z-ai/glm-5.3-flash"]
snapshot_dir = "snapshots"

[profiles.production]
quantizations = ["fp8"]
required_parameters = ["seed", "response_format", "tools", "tool_choice"]
min_eligible = 2
preferred_providers = ["Near AI", "Inceptron"]
healthy_only = true
```

A provider is eligible when its quantization is explicitly allowed, all required parameters are supported, and (by default) its status is non-negative. The literal quantization `unknown` matches only when listed. Profiles are evaluated independently for each model.

## Commands and exit codes

- `orwatch snapshot [--config watch.toml] [--date YYYY-MM-DD]` fetches configured models and writes normalized daily snapshots. It continues after per-model failures and exits 1 if any failed.
- `orwatch diff [--config watch.toml] [--model ID] [--from DATE --to DATE] [--format md|json] [--fail-on CLASS[,CLASS...]]` compares the latest two snapshots by default. With exactly one snapshot it prints a note and exits 0. It exits 2 when a selected change class appears, 1 for zero snapshots, bad dates, or bad config, and 0 otherwise.
- `orwatch check --profile NAME [--model ID] [--offline]` checks a configured profile using live metadata or the latest snapshot. The ad-hoc form accepts `--model ID --quant fp8 --require seed,response_format,tools,tool_choice --min 2`. It exits 7 below the minimum, 1 on errors, and 0 on success.
- `orwatch history [--config watch.toml] [--model ID] [--profile NAME] [--out FILE]` writes a newest-first Markdown timeline of days with changes.

Snapshot identity is the OpenRouter endpoint tag. Missing or duplicate tags receive a provider and index fallback, with a warning. Snapshot records keep stable metadata, sorted parameters, pricing strings, and daily uptime; short-window uptime, latency, and throughput are omitted.

## GitHub Actions

The included `.github/workflows/daily.yml` runs the daily snapshot, refreshes `HISTORY.md`, commits changed history, and opens an issue for configured breaking changes. Enable Actions write access and issue creation in repository settings. The workflow uses the repository `GITHUB_TOKEN`.

## Non-goals and roadmap

Version 0.1 tracks metadata only. It does not measure quality, latency, or throughput, and it does not cover providers outside OpenRouter or provide a hosted UI. Version 0.2 is planned to add measured endpoint quality and performance alongside metadata history.
