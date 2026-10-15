<div align="center">

# orwatch

**Your LLM provider changed under you. Find out before your pipeline does.**

Daily snapshots, diffs, and pre-run checks for OpenRouter endpoint metadata:
quantization flips, vanished providers, and dropped parameters.

[![daily snapshot](https://github.com/hjongc/openrouter-endpoint-watch/actions/workflows/daily.yml/badge.svg)](https://github.com/hjongc/openrouter-endpoint-watch/actions/workflows/daily.yml)
![python](https://img.shields.io/badge/python-3.11%2B-2a78d6)
![dependencies](https://img.shields.io/badge/runtime%20deps-0-2a78d6)
[![license](https://img.shields.io/badge/license-MIT-2a78d6)](LICENSE)

[Quickstart](#quickstart) · [How it works](#how-it-works) · [Why I built this](#why-i-built-this) · [Live history](HISTORY.md)

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-dark.svg">
  <img alt="orwatch pipeline: OpenRouter's public endpoint list is snapshotted daily by GitHub Actions, diffed into named change classes, and checked against your profile; results go to HISTORY.md, a GitHub issue on breaking changes, and exit code 7 as a pre-run gate." src="docs/assets/architecture-light.svg" width="880">
</picture>

orwatch watches the public endpoint list of the OpenRouter models you depend on.
Every day it keeps a snapshot and tells you, by name, what changed: an fp8
endpoint that became fp4, a provider that disappeared, a parameter that stopped
being supported. Then it checks what is left against what your pipeline
actually needs, and fails fast, before you spend a single token.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/demo-dark.svg">
  <img alt="Terminal: orwatch diff reports Parasail fp8 replaced by fp4, NextBit removed, and the production profile falling from 3 eligible endpoints to 1; orwatch check exits 7." src="docs/assets/demo-light.svg" width="880">
</picture>

## What diff sees

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/board-dark.svg">
  <img alt="Endpoint board for z-ai/glm-5.3-flash between Oct 7 and Oct 8: every fp8 endpoint with its parameter support, status, and eligibility on both days. Parasail is replaced by an fp4 endpoint, NextBit is removed, and eligibility falls from 3 to 1, below the minimum of 2." src="docs/assets/board-light.svg" width="880">
</picture>

One bad day, endpoint by endpoint. A model can look abundantly served, with 33
endpoints listed, yet most fp8 endpoints were never usable for this profile
because they lack `seed` or `response_format`. The three that were usable
became one overnight: Parasail's fp8 endpoint was replaced by an fp4 one, and
NextBit disappeared. orwatch reports both as named changes and flags the
profile as below its minimum.

## Live in this repo

This repository runs orwatch on itself. Every day it snapshots
`z-ai/glm-5.3-flash`, updates the counts in the diagram at the top, and appends
anything that changed to [`HISTORY.md`](HISTORY.md). The margin really is that
thin and that mobile: on the day the repository went public (2026-10-15), the
number of endpoints that could serve the profile went from **5 to 3 of 33
within a few hours**.

## How it works

**Snapshot.** Once a day, orwatch stores the stable fields of every endpoint
(provider, tag, quantization, status, supported parameters, pricing, context)
and drops per-minute noise such as latency and throughput. Endpoints are
identified by their OpenRouter tag, because one provider can list the same
model more than once.

**Diff.** Consecutive snapshots are compared and every difference gets a name:

| Change class | What happened | Opens an issue in the daily workflow |
|---|---|:---:|
| `endpoint_replaced` | A provider's endpoint left and a new one arrived in its place, typically a quantization flip (`parasail/fp8` → `parasail/fp4`) | ● |
| `quantization_changed` | The quantization of an endpoint changed | ● |
| `endpoint_removed` | An endpoint is no longer listed | ● |
| `profile_eligible_changed` | The set of endpoints that meet a profile changed; flagged when it falls below `min_eligible` | ● |
| `endpoint_added` | A new endpoint is listed | |
| `status_changed` | An endpoint turned healthy or unhealthy | |
| `parameter_added` / `parameter_removed` | Support for a parameter such as `seed` or `tools` changed | |
| `price_changed` | Prompt or completion price changed | |
| `context_changed` | Context length or max completion tokens changed | |

OpenRouter tags embed the quantization, so a flip looks like one endpoint
leaving and another arriving. orwatch pairs them back into a single
`endpoint_replaced` so you read one change, not two.

**Profile check.** A profile encodes what your pipeline needs. An endpoint is
eligible only if its quantization is listed, every required parameter is
supported, and its status is healthy. `unknown` never matches unless you list
it. `orwatch check` exits 7 when fewer than `min_eligible` endpoints remain.

## Quickstart

```bash
git clone https://github.com/hjongc/openrouter-endpoint-watch
cd openrouter-endpoint-watch
python -m pip install .

# Replay the Oct 8 incident from bundled snapshots (no network):
orwatch diff  --config examples/watch.toml
orwatch check --config examples/watch.toml --profile production --offline; echo $?   # 7

# Your own models, live:
orwatch snapshot
orwatch check --profile production
```

Python 3.11+, standard library only, no API key: the endpoint metadata is
public.

## Use it as a pre-run gate

Put the check in front of anything expensive. It exits 7 when a profile has
fewer eligible endpoints than you require, before a single token is spent:

```bash
orwatch check --profile production || { echo "provider set changed, not running"; exit 1; }
python run_my_batch.py
```

No profile in a config file? The ad-hoc form works too:

```bash
orwatch check --model z-ai/glm-5.3-flash --quant fp8 \
  --require seed,response_format,tools,tool_choice --min 2
```

## Configuration

`watch.toml`:

```toml
[watch]
models = ["z-ai/glm-5.3-flash"]   # any OpenRouter model ids
snapshot_dir = "snapshots"

[profiles.production]
quantizations = ["fp8"]
required_parameters = ["seed", "response_format", "tools", "tool_choice"]
min_eligible = 2
preferred_providers = ["Near AI", "Inceptron"]   # optional, reported in order
healthy_only = true                               # default
```

Profiles are evaluated per model. Add as many as your pipelines need.

## Commands and exit codes

| Command | What it does | Exit codes |
|---|---|---|
| `orwatch snapshot [--date D]` | Fetch every configured model and write `snapshots/<model>/<date>.json` | 0 ok, 1 if any model failed |
| `orwatch diff [--from D --to D] [--format md\|json] [--fail-on CLASSES]` | Compare the two latest snapshots (or a date range) | 0 ok, **2** when a `--fail-on` class appears, 1 on error or no snapshots |
| `orwatch check --profile NAME [--offline]` | Evaluate a profile on live metadata or the latest snapshot | 0 ok, **7** below `min_eligible`, 1 on error |
| `orwatch history [--out FILE]` | Markdown history per model: tracking period and every day with changes, newest first | 0 ok, 1 on error |

All commands take `--config` (default `watch.toml`) and most take `--model ID`.
A model with only one snapshot is not an error: `diff` says there is nothing to
compare yet and exits 0, so a fresh daily job does not fail on its first run.

## Run it daily for your own models

This repository watches itself with
[`.github/workflows/daily.yml`](.github/workflows/daily.yml): every day at
03:00 UTC it snapshots, refreshes [`HISTORY.md`](HISTORY.md) and the figures,
commits them, and opens an issue when one of the marked change classes above
appears.

To watch your own models, fork the repository, edit `watch.toml`, and enable
Actions. The workflow only needs the default `GITHUB_TOKEN` with contents and
issues write permission. The figures are rendered by
[`scripts/render_figures.py`](scripts/render_figures.py) with the standard
library only.

## Why I built this

My company runs an LLM on its own servers inside a closed network. Developing
in there is slow, so I built my pipeline outside first and tested it outside
before moving it in. For those outside tests to say anything about the inside,
the model had to be served the way it is served internally: **the same model,
the same fp8 quantization**, plus the parameters the pipeline depends on,
**`seed`, `response_format`, `tools` and `tool_choice`.** OpenRouter was where
I could get that.

Matching the model and quantization was not enough. Even among fp8 providers,
stability and speed differed from one host to the next, so on 2026-09-03 I
benchmarked the ones that qualified and pinned three: Reka, Parasail and
NextBit. Fallbacks off, so a run could never drift to a different setup.

What I did not expect was the serving conditions themselves to change. They
changed twice, and neither time did anything raise an error.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/incident-dark.svg">
  <img alt="Step chart of pinned providers that met the profile: 3 from Sep 3, 2 from Sep 28 when Reka was relabelled unknown and silently filtered out, 0 on Oct 8 when Parasail moved to fp4 and NextBit was delisted, halting the pipeline with exit 7." src="docs/assets/incident-light.svg" width="880">
</picture>

- **Sep 28.** OpenRouter's metadata started listing Reka's quantization as
  `unknown`. My fp8 filter did exactly what it was told and dropped it. The
  fastest provider (71 tok/s) disappeared from every run, and the only trace was
  a call log that read *Parasail 230, NextBit 15, Reka 0*.
- **Oct 8.** Parasail switched the model to fp4 only and NextBit vanished from
  the list. My pre-run gate stopped with `exit 7: endpoint check left no
  eligible providers`, and every run, from quick tests to full evaluations,
  was blocked. That day I re-benchmarked the four providers that still
  qualified and moved to the best one that answered, about **5× slower**
  (13.9 tok/s) than what I started with.

No provider lied. **The set of endpoints that met my requirements changed
without notice, and I learned it at run time.** Every one of those changes was
already visible in OpenRouter's public `/endpoints` metadata, but that metadata
has no history and no change feed. orwatch is that history and that feed.

## FAQ

**OpenRouter already has a quantization filter. Why is that not enough?**
The filter is what dropped my fastest provider without a word. A filter decides
per request; it cannot tell you that the set it filters from changed yesterday.

**Why not turn fallbacks on?** Then a run can silently switch to a provider
with different weights or parameter support halfway through. For anything you
compare across runs, that is worse than stopping.

**Which models can I watch?** Any OpenRouter model id. List as many as you like
in `watch.toml`; profiles are evaluated per model.

**Does it measure quality or speed?** Not yet. v0.1 tracks what providers
declare. A measured layer (does the declared fp8 behave like fp8, how fast is
it under real load) is the next step on the roadmap.

## Roadmap

- Eligibility trend chart: the daily count of eligible endpoints per profile,
  drawn from the accumulated snapshots.
- v0.2: measured probes alongside metadata (latency, tok/s, and an output
  consistency check per endpoint).
- Slack and webhook alerts in addition to GitHub issues.
- Providers beyond OpenRouter.

## License

[MIT](LICENSE)
