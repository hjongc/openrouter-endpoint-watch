# openrouter-endpoint-watch — spec v0.1 (2026-10-15)

## Why this exists

Two incidents in a production LLM pipeline that routes through OpenRouter with
a strict provider requirement (fp8 + `seed` + `response_format` + `tools` +
`tool_choice`):

- **2026-09-28** — OpenRouter relabelled one provider's quantization to
  `unknown`. The fp8 filter silently dropped it and every run fell back to a
  single remaining provider. Nobody noticed until later.
- **2026-10-08** — For `z-ai/glm-5.3-flash`, Parasail switched from fp8 to
  fp4-only and NextBit disappeared from the endpoint list. The pre-run gate
  stopped with "endpoint check left no eligible providers; no LLM call was
  made". Production and evaluation were both blocked; the fix required
  re-benchmarking four providers and accepting a slower one. Two days earlier
  everything had worked.

In both cases no provider lied. The *set of endpoints satisfying our
requirements* changed without notice, and we learned at run time. OpenRouter
exposes the metadata needed to see this coming, but publishes no per-endpoint
history and no change feed. This tool is that history and that feed.

## Goal

A small CLI + library that

1. snapshots OpenRouter per-model endpoint metadata (daily, via GitHub
   Actions or cron),
2. diffs consecutive snapshots into named change classes,
3. evaluates user-defined **requirement profiles** so the report says, in one
   line, "your production profile went from 3 eligible endpoints to 1 — below
   your minimum", and
4. offers a pre-run `check` command that exits non-zero when a profile has
   too few eligible endpoints.

## Non-goals (v0.1)

- Measured quality, latency, or throughput benchmarks (metadata only; a
  measured layer is the v0.2 roadmap item).
- Providers outside OpenRouter.
- Web UI or hosted service.
- Alert channels beyond exit codes, markdown output, and a GitHub Actions
  issue step.

## Data source

`GET https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints` — public,
no API key. Response: `data.endpoints[]`, each with `name`, `tag`,
`provider_name`, `quantization`, `status`, `context_length`,
`max_completion_tokens`, `supported_parameters`, `supports_tool_choice`,
`pricing{prompt, completion, ...}`, `uptime_last_1d`, `uptime_last_30m`,
`uptime_last_5m`, `latency_last_30m`, `throughput_last_30m`, and more.

A real response is committed as
`tests/fixtures/glm-5.3-flash.endpoints.2026-10-15.json` (33 endpoints).
Observed facts that shape the design:

- `provider_name` repeats: Parasail ×2, BaseTen ×2, Sail Research ×2,
  Fireworks ×2. **Endpoint identity is `tag`** (e.g. `parasail/fp4`,
  `morph/fp8`, `sail-research/us`). If `tag` is missing or duplicated within
  one response, fall back to `provider_name#<index>` and emit a warning.
- `quantization` may be `null`; normalize to the string `"unknown"`.
- `status` is an int; `0` is normal and negative values mean degraded or
  deranked. Treat `status < 0` as unhealthy.
- Tags often embed the quantization (`parasail/fp4`). A quantization flip can
  therefore surface as one endpoint removed and another added; see the
  pairing rule under *Diff*.

## Normalized snapshot

Path: `<snapshot_dir>/<model-slug>/<YYYY-MM-DD>.json`, where `model-slug` is
the model id with `/` replaced by `__` (`z-ai__glm-5.3-flash`). Same date
overwrites (idempotent).

```json
{
  "schema_version": 1,
  "model_id": "z-ai/glm-5.3-flash",
  "fetched_at": "2026-10-15T09:12:03Z",
  "endpoints": {
    "parasail/fp4": {
      "tag": "parasail/fp4",
      "provider_name": "Parasail",
      "quantization": "fp4",
      "status": 0,
      "context_length": 1048576,
      "max_completion_tokens": 131072,
      "supported_parameters": ["frequency_penalty", "..."],
      "supports_tool_choice": true,
      "pricing": {"prompt": "0.000000036", "completion": "0.0000009"},
      "uptime_last_1d": 99.8
    }
  }
}
```

Rules: `supported_parameters` is stored sorted; pricing strings are kept
verbatim (no float conversion); volatile fields (`latency_*`,
`throughput_*`, `uptime_last_5m`, `uptime_last_30m`) are **not stored**;
`uptime_last_1d` is stored for context but **never diffed**.

## Config: `watch.toml`

```toml
[watch]
models = ["z-ai/glm-5.3-flash"]   # add more model ids here
snapshot_dir = "snapshots"

[profiles.production]
quantizations = ["fp8"]
required_parameters = ["seed", "response_format", "tools", "tool_choice"]
min_eligible = 2
preferred_providers = ["Near AI", "Inceptron"]   # optional, ordered
healthy_only = true                               # default true
```

An endpoint is **eligible** for a profile iff its `quantization` is in
`quantizations` (the literal `"unknown"` matches only if listed explicitly),
`required_parameters ⊆ supported_parameters`, and (`healthy_only` is false or
`status >= 0`). Profiles are evaluated per model.

## Commands (`orwatch`, argparse, no third-party deps)

### `orwatch snapshot [--config watch.toml] [--date YYYY-MM-DD]`

Fetch every configured model and write snapshots. One summary line per
model (`z-ai/glm-5.3-flash: 33 endpoints → snapshots/z-ai__glm-5.3-flash/2026-10-15.json`).
A network or parse failure for one model is reported and the others continue;
exit 1 at the end if any model failed, else 0.

### `orwatch diff [--config] [--model ID] [--from DATE] [--to DATE] [--format md|json] [--fail-on CLASS[,CLASS...]]`

Default compares the two most recent snapshots per model. Change classes:

| class | meaning |
| --- | --- |
| `endpoint_added` | tag present only in the newer snapshot |
| `endpoint_removed` | tag present only in the older snapshot |
| `endpoint_replaced` | pairing rule below: one removed + one added with the same `provider_name`; reported with field-level sub-changes |
| `quantization_changed` | same tag or replaced pair, different `quantization` |
| `status_changed` | healthy ↔ unhealthy transition (sign of `status` changed) |
| `parameter_added` / `parameter_removed` | `supported_parameters` set difference |
| `price_changed` | `pricing.prompt` or `pricing.completion` string differs |
| `context_changed` | `context_length` or `max_completion_tokens` differs |
| `profile_eligible_changed` | per profile: eligible tag set differs; carries `before`, `after`, `lost`, `gained`, and `below_min` (bool) |

**Pairing rule:** after computing added/removed by tag, for each
`provider_name` that has exactly one removed and exactly one added endpoint,
report `endpoint_replaced` (with nested `quantization_changed`,
`parameter_*`, etc.) instead of a bare add + remove. This is how
"Parasail fp8 → fp4" must appear.

Exit codes: 0 normally; **2** if any change matches `--fail-on`; 1 on error
(fewer than two snapshots, bad dates, bad config).

Markdown format (target, exact wording may vary slightly):

```
## z-ai/glm-5.3-flash — 2026-10-07 → 2026-10-08

- endpoint_replaced  Parasail: parasail/fp8 → parasail/fp4
  - quantization_changed  fp8 → fp4
- endpoint_removed   NextBit (nextbit/fp8)
- profile_eligible_changed  production: 3 → 1  (lost: Parasail, NextBit; kept: Near AI)  **below min_eligible=2**
```

JSON format: a list of change objects with `model_id`, `from`, `to`,
`class`, `tag`/`provider_name` where applicable, `before`, `after`, and
nested `changes` for `endpoint_replaced`.

### `orwatch check [--config] --profile NAME [--model ID] [--offline]`

Live fetch (or the latest snapshot with `--offline`), then print an eligible
endpoint table per model (provider, tag, quantization, status, price) and,
if `preferred_providers` is set, each preferred provider's eligibility in
order. Exit **0** if every checked model has `eligible >= min_eligible`,
otherwise exit **7** with a one-line reason
(`production: 1 eligible endpoint for z-ai/glm-5.3-flash, min_eligible=2`).

Ad-hoc form without a config file:
`orwatch check --model z-ai/glm-5.3-flash --quant fp8 --require seed,response_format,tools,tool_choice --min 2`.

### `orwatch history [--config] [--model ID] [--profile NAME] [--out FILE]`

Walk all snapshots for each model chronologically, run the diff engine on
each consecutive pair, and emit one markdown timeline (newest first). Writes
to stdout or `--out FILE`. Days with no changes are omitted.

## GitHub Actions

`.github/workflows/daily.yml`: cron `0 3 * * *` plus `workflow_dispatch`;
Python 3.11; steps: install the package, `orwatch snapshot`,
`orwatch history --out HISTORY.md`, commit `snapshots/` and `HISTORY.md` when
changed (bot identity), then run
`orwatch diff --format md --fail-on quantization_changed,endpoint_removed,endpoint_replaced,profile_eligible_changed`
and, on exit 2, open a GitHub issue whose body is the markdown diff (use
`gh issue create` with `GITHUB_TOKEN`; the issue step must not fail the
workflow when an issue cannot be created). Keep it simple and readable.

## Packaging and conventions

- Python ≥ 3.11. **Standard library only at runtime** (`urllib.request`,
  `tomllib`, `json`, `argparse`, `dataclasses`, `datetime`, `pathlib`).
  Dev dependency: `pytest` only.
- `pyproject.toml` (setuptools, src layout `src/orwatch/`), console script
  `orwatch = orwatch.cli:main`, `[tool.pytest.ini_options] pythonpath = ["src"]`
  so tests run without installation.
- `LICENSE` (MIT), English for all code, comments, docs, and messages.
- HTTP goes through an injectable fetch callable (`fetch_endpoints(model_id)
  -> dict`) so tests never touch the network. Tests build synthetic "before"
  snapshots by mutating the real fixture (e.g. flip `parasail/fp4` back to
  `parasail/fp8`, add a fake `nextbit/fp8` endpoint). Required tests, at
  minimum:
  - snapshot normalization (tag identity, `unknown` quantization, sorted
    params, volatile fields dropped, duplicate-tag fallback with warning);
  - each change class in isolation;
  - the pairing rule (`endpoint_replaced` vs bare add/remove, and the case
    where a provider has two removed → no pairing);
  - profile eligibility (unknown never matches unless listed; `healthy_only`);
  - `test_incident_2026_10_08_parasail_fp4_nextbit_gone`: before = synthetic
    snapshot with Parasail fp8 + NextBit fp8, after = the real fixture;
    expect `endpoint_replaced` for Parasail with `quantization_changed
    fp8→fp4`, `endpoint_removed` for NextBit, and
    `profile_eligible_changed` with `below_min` when `min_eligible` is set so
    that it trips;
  - CLI exit codes for `diff --fail-on` (2) and `check` (0 / 7);
  - config parsing and validation errors.
- `examples/snapshots/z-ai__glm-5.3-flash/2026-10-07.json` and
  `2026-10-08.json`: two synthetic snapshots reproducing the incident so that
  `orwatch diff --config examples/watch.toml` demonstrates the tool with no
  network. `examples/watch.toml` points `snapshot_dir` at `examples/snapshots`.
- `README.md`: what and why (the incident story in ~10 lines), install,
  quickstart using the examples, config reference, commands and exit codes,
  GitHub Actions setup, non-goals, roadmap (v0.2 measured layer).
- Write everything fresh. Do not copy code from any other repository.

## Done criteria (run from the repo root)

```bash
.venv/bin/python -m pytest                                   # exit 0
.venv/bin/python -m orwatch diff --config examples/watch.toml # exit 0, prints the incident diff
.venv/bin/python -m orwatch diff --config examples/watch.toml --fail-on quantization_changed; echo $?   # 2
.venv/bin/python -m orwatch check --config examples/watch.toml --profile production --offline; echo $?  # 7
```

`python -m orwatch` must work (add `src/orwatch/__main__.py`). The venv at
`.venv` already contains `pytest`; the package is importable via
`pythonpath = ["src"]` without installation.
