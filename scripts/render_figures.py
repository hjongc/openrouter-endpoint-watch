#!/usr/bin/env python3
"""Render deterministic README figures for OpenRouter endpoint history."""

from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orwatch.config import load_config  # noqa: E402
from orwatch.core import eligible_endpoints, list_snapshots, read_snapshot  # noqa: E402


FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
PALETTES = {
    "light": {
        "surface": "#fcfcfb", "border": "#e1e0d9", "ink": "#0b0b0b",
        "secondary": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
        "axis": "#c3c2b7", "blue": "#2a78d6", "serious": "#ec835a",
        "critical": "#d03b3b", "good": "#0ca30c",
        "bars": ("#86b6ef", "#5598e7", "#2a78d6", "#184f95"),
    },
    "dark": {
        "surface": "#1a1a19", "border": "#2c2c2a", "ink": "#f0efec",
        "secondary": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
        "axis": "#383835", "blue": "#3987e5", "serious": "#ec835a",
        "critical": "#d03b3b", "good": "#0ca30c",
        "bars": ("#184f95", "#256abf", "#3987e5", "#86b6ef"),
    },
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text(x: float, y: float, value: str, *, fill: str, size: int = 12,
         weight: int | str = 400, family: str = FONT, anchor: str = "start",
         extra: str = "") -> str:
    return (f'<text x="{x:g}" y="{y:g}" fill="{fill}" font-family="{family}" '
            f'font-size="{size}px" font-weight="{weight}" text-anchor="{anchor}" {extra}>'
            f'{esc(value)}</text>')


def multiline(x: float, y: float, lines: list[str], *, fill: str, size: int = 12,
              weight: int | str = 400, family: str = FONT, line_height: int = 17) -> str:
    body = "".join(f'<tspan x="{x:g}" dy="{0 if i == 0 else line_height}">{esc(line)}</tspan>'
                   for i, line in enumerate(lines))
    return (f'<text x="{x:g}" y="{y:g}" fill="{fill}" font-family="{family}" '
            f'font-size="{size}px" font-weight="{weight}">{body}</text>')


def svg_start(kind: str, mode: str, height: int, title: str, desc: str) -> list[str]:
    p = PALETTES[mode]
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="880" height="{height}" viewBox="0 0 880 {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{esc(title)}</title>',
        f'<desc id="desc">{esc(desc)}</desc>',
        f'<rect x="0.5" y="0.5" width="879" height="{height - 1}" rx="12" fill="{p["surface"]}" stroke="{p["border"]}"/>',
    ]


def svg_finish(parts: list[str]) -> str:
    return "\n".join(parts + ["</svg>", ""])


def incident(mode: str) -> str:
    p = PALETTES[mode]
    title = "My production pipeline's eligible OpenRouter providers"
    subtitle = "Pinned fp8 providers for z-ai/glm-5.3-flash that still met the profile (fp8 + seed, response_format, tools, tool_choice)"
    parts = svg_start("incident", mode, 544, title,
                      "A step chart shows eligible pinned providers falling from three to zero between September 3 and October 8, 2026.")
    parts += [text(42, 43, title, fill=p["ink"], size=18, weight=600),
              text(42, 69, subtitle, fill=p["secondary"], size=13)]
    left, right, top, bottom = 72.0, 832.0, 160.0, 432.0
    total_days = 41  # September 1 through October 12.
    def px(day: int) -> float:
        return left + (right - left) * day / total_days
    def py(value: int) -> float:
        return bottom - (bottom - top) * value / 3
    for value in range(4):
        y = py(value)
        parts.append(f'<line x1="{left:g}" y1="{y:g}" x2="{right:g}" y2="{y:g}" stroke="{p["grid"]}" stroke-width="1"/>')
        parts.append(text(left - 14, y + 4, str(value), fill=p["muted"], size=11, anchor="end"))
    x_ticks = [(0, "Sep 1"), (14, "Sep 15"), (30, "Oct 1"), (37, "Oct 8")]
    for day, label in x_ticks:
        x = px(day)
        parts.append(f'<line x1="{x:g}" y1="{bottom:g}" x2="{x:g}" y2="{bottom + 4:g}" stroke="{p["axis"]}" stroke-width="1"/>')
        parts.append(text(x, bottom + 22, label, fill=p["muted"], size=11, anchor="middle"))
    parts.append(f'<line x1="{left:g}" y1="{top:g}" x2="{left:g}" y2="{bottom:g}" stroke="{p["axis"]}" stroke-width="1"/>')
    parts.append(f'<line x1="{left:g}" y1="{bottom:g}" x2="{right:g}" y2="{bottom:g}" stroke="{p["axis"]}" stroke-width="1"/>')
    points = [(2, 3), (27, 2), (37, 0)]
    step = f"M {px(2):g} {py(3):g} H {px(27):g} V {py(2):g} H {px(37):g} V {py(0):g}"
    area = f"M {px(2):g} {bottom:g} L {px(2):g} {py(3):g} H {px(27):g} V {py(2):g} H {px(37):g} V {py(0):g} L {px(37):g} {bottom:g} Z"
    parts.append(f'<path d="{area}" fill="{p["blue"]}" fill-opacity="0.10"/>')
    parts.append(f'<path d="{step}" fill="none" stroke="{p["blue"]}" stroke-width="2" stroke-linejoin="round"/>')
    events = [(2, 3, p["blue"]), (27, 2, p["serious"]), (37, 0, p["critical"])]
    for day, value, color in events:
        parts.append(f'<circle cx="{px(day):g}" cy="{py(value):g}" r="5" fill="{color}" stroke="{p["surface"]}" stroke-width="2"/>')
    annotations = [
        ("Sep 3 — Pinned Reka, Parasail, NextBit", "(Reka fastest at 71 tok/s)", px(2), py(3), 91, 102, p["blue"], False),
        ('Sep 28 — Reka relabelled "unknown"', "fp8 filter dropped it silently. No error.", px(27), (py(3) + py(2)) / 2, p["serious"], True),
        ("Oct 8 — Parasail moved to fp4, NextBit delisted", "0 eligible → pipeline halted with exit 7", px(37), (py(2) + py(0)) / 2, p["critical"], True),
    ]
    for annotation in annotations:
        if not annotation[-1]:
            first, second, mx, my, ax, ay, color, _ = annotation
            parts.append(f'<line x1="{mx:g}" y1="{my:g}" x2="{ax + 5:g}" y2="{ay + 9:g}" stroke="{p["muted"]}" stroke-width="1"/>')
            parts.append(f'<circle cx="{ax + 5:g}" cy="{ay - 4:g}" r="5" fill="{color}"/>')
            tx = ax + 15
            parts.append(text(tx, ay, first, fill=p["ink"], size=12, weight=500))
            parts.append(text(tx, ay + 17, second, fill=p["ink"], size=12))
            continue
        first, second, segment_x, center_y, color, _ = annotation
        text_end = segment_x - 14
        first_y, second_y = center_y - 8.5, center_y + 8.5
        icon_x = segment_x - 6
        parts.append(f'<circle cx="{icon_x:g}" cy="{first_y - 4:g}" r="5" fill="{color}"/>')
        parts.append(text(icon_x, first_y, "!", fill="#ffffff", size=8, weight=700, anchor="middle"))
        parts.append(text(text_end, first_y, first, fill=p["ink"], size=13, weight=500, anchor="end"))
        parts.append(text(text_end, second_y, second, fill=p["ink"], size=13, anchor="end"))
    parts.append(multiline(42, 502, [
        "Same day: re-benchmarked 4 providers and switched to Near AI (13.9 tok/s, ~5× slower than Reka).",
        "Every drop was visible in OpenRouter metadata first.",
    ], fill=p["muted"], size=11, line_height=16))
    return svg_finish(parts)


def latest_snapshot(config: dict) -> tuple[dict, Path]:
    model = config["watch"]["models"][0]
    paths = list_snapshots(config["watch"]["snapshot_dir"], model)
    if not paths:
        raise FileNotFoundError(f"no snapshots found for {model}")
    path = paths[-1]
    return read_snapshot(path), path


def funnel(mode: str, config: dict, snapshot: dict, snapshot_path: Path) -> str:
    p = PALETTES[mode]
    profiles = config.get("profiles", {})
    profile_name = "production" if "production" in profiles else next(iter(profiles), None)
    if profile_name is None:
        raise ValueError("config requires at least one profile")
    profile = profiles[profile_name]
    endpoints = snapshot["endpoints"]
    quantized = {tag: ep for tag, ep in endpoints.items() if ep.get("quantization", "unknown") in profile.get("quantizations", [])}
    params = set(profile.get("required_parameters", []))
    with_params = {tag: ep for tag, ep in quantized.items() if params.issubset(ep.get("supported_parameters", []))}
    eligible = eligible_endpoints(snapshot, profile)
    counts = [len(endpoints), len(quantized), len(with_params), len(eligible)]
    model = snapshot["model_id"]
    date = snapshot_path.stem
    title = f"How many of {counts[0]} listed endpoints can serve my production profile?"
    subtitle = f"{model} · OpenRouter snapshot {date} · regenerated daily"
    parts = svg_start("funnel", mode, 430, title,
                      f"A four-stage horizontal bar chart filters {counts[0]} listed endpoints down to {counts[-1]} fully eligible endpoints.")
    parts += [text(42, 43, title, fill=p["ink"], size=18, weight=600),
              text(42, 69, subtitle, fill=p["secondary"], size=13)]
    labels = ["Listed endpoints", f"Quantization in profile ({', '.join(profile.get('quantizations', []))})",
              "+ required parameters", "+ healthy (status ≥ 0)"]
    base_x, label_right, max_width, start_y = 300.0, 276.0, 520.0, 138.0
    row_gap, bar_h = 56.0, 22.0
    parts.append(f'<line x1="{base_x:g}" y1="{start_y - 18:g}" x2="{base_x:g}" y2="{start_y + 3 * row_gap + bar_h + 4:g}" stroke="{p["axis"]}" stroke-width="1"/>')
    for i, (label, count) in enumerate(zip(labels, counts)):
        y = start_y + i * row_gap
        parts.append(text(label_right, y + 16, label, fill=p["secondary"], size=12, anchor="end"))
        width = max_width * count / counts[0] if counts[0] else 0
        right = base_x + width
        # A square left edge and softly rounded data end are drawn as a path.
        d = f"M {base_x:g} {y:g} H {max(base_x, right - 4):g} Q {right:g} {y:g} {right:g} {y + 4:g} V {y + bar_h - 4:g} Q {right:g} {y + bar_h:g} {max(base_x, right - 4):g} {y + bar_h:g} H {base_x:g} Z"
        parts.append(f'<path d="{d}" fill="{p["bars"][i]}"/>')
        value = f"{count} usable" if i == 3 else str(count)
        parts.append(text(right + 12, y + 16, value, fill=p["ink"], size=12, weight=600 if i == 3 else 400))
    quant_label = ", ".join(profile.get("quantizations", [])) or "none"
    required_label = ", ".join(profile.get("required_parameters", [])) or "none"
    rel_snapshot = Path(config.get("_snapshot_dir_label", config["watch"]["snapshot_dir"])) / model.replace("/", "__") / f"{date}.json"
    parts.append(multiline(42, 382, [
        f"Profile from {args_config_label(config)}: quantizations {quant_label}; requires {required_label}.",
        f"Source: {rel_snapshot.as_posix()}.",
    ], fill=p["muted"], size=11, line_height=16))
    return svg_finish(parts)


def args_config_label(config: dict) -> str:
    return config.get("_source_label", "watch.toml")


def run_cli(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run([sys.executable, "-m", "orwatch", *command], cwd=ROOT, env=env,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def demo(mode: str) -> str:
    p = PALETTES[mode]
    diff_cmd = ["diff", "--config", "examples/watch.toml"]
    check_cmd = ["check", "--config", "examples/watch.toml", "--profile", "production", "--offline"]
    diff = run_cli(diff_cmd)
    check = run_cli(check_cmd)
    diff_lines = diff.stdout.rstrip("\n").splitlines()
    check_first = check.stdout.splitlines()[0] if check.stdout.splitlines() else ""
    rows: list[tuple[str, str]] = []
    rows.append(("command", "$ orwatch diff --config examples/watch.toml"))
    for index, line in enumerate(diff_lines):
        line = line.replace("**", "")
        if index == 0 and line.startswith("## "):
            rows.append(("header", line[3:]))
        else:
            rows.append(("output", line))
    rows.append(("blank", ""))
    rows.append(("command", "$ orwatch check --config examples/watch.toml --profile production --offline"))
    rows.append(("output", check_first))
    rows.append(("critical", f"exit {check.returncode}"))
    title = "orwatch demo terminal"
    content: list[str] = []
    y = 78
    for kind, line in rows:
        if kind == "blank":
            y += 10
            continue
        fill = p["secondary"] if kind == "command" else p["ink"]
        weight = 600 if kind in {"header", "critical"} or (kind == "output" and "below min_eligible" in line) else 400
        if kind in {"critical"} or (kind == "output" and "below min_eligible" in line):
            content.append(f'<circle cx="47" cy="{y - 4}" r="4" fill="{p["critical"]}"/>')
            x = 60
        elif kind == "output" and ("endpoint_replaced" in line or "quantization_changed" in line):
            content.append(f'<circle cx="47" cy="{y - 4}" r="4" fill="{p["serious"]}"/>')
            x = 60
        else:
            x = 40
        if kind == "command":
            cmd = line[2:]
            content.append(text(x, y, "$", fill=p["muted"], size=13, family=MONO))
            content.append(text(x + 12, y, cmd, fill=p["secondary"], size=13, family=MONO))
            y += 20
        else:
            # SVG has no text wrapping. Break long terminal output at word boundaries.
            chunks: list[str] = []
            remaining = line
            while len(remaining) > 96:
                cut = remaining.rfind(" ", 0, 97)
                if cut <= 0:
                    cut = 96
                    chunks.append(remaining[:cut])
                    remaining = remaining[cut:]
                else:
                    chunks.append(remaining[:cut])
                    remaining = remaining[cut + 1:]
            chunks.append(remaining)
            for chunk in chunks:
                content.append(text(x, y, chunk, fill=fill, size=13, weight=weight, family=MONO,
                                    extra='xml:space="preserve"'))
                y += 20
    terminal_bottom = y - 20 + 28
    height = terminal_bottom + 24
    parts = svg_start("demo", mode, height, title,
                      "A terminal transcript shows an endpoint replacement, a profile falling below its minimum, and offline check exit code 7.")
    parts.append(f'<rect x="24" y="24" width="832" height="{terminal_bottom - 24}" rx="10" fill="{p["surface"]}" stroke="{p["border"]}"/>')
    parts.append(f'<path d="M 24 34 Q 24 24 34 24 H 846 Q 856 24 856 34 V 52 H 24 Z" fill="{p["surface"]}"/>')
    for cx in (43, 63, 83):
        parts.append(f'<circle cx="{cx}" cy="38" r="5" fill="{p["muted"]}" fill-opacity="0.35"/>')
    parts.append(text(440, 42, "orwatch — examples", fill=p["muted"], size=12, anchor="middle"))
    parts.extend(content)
    return svg_finish(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="watch.toml")
    parser.add_argument("--out", default="docs/assets")
    options = parser.parse_args()
    config_path = Path(options.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = load_config(config_path)
    config["_source_label"] = options.config
    config["_snapshot_dir_label"] = config["watch"]["snapshot_dir"]
    # Match the CLI: relative snapshot directories are resolved from the repository cwd.
    snapshot_dir = Path(config["watch"]["snapshot_dir"])
    if not snapshot_dir.is_absolute():
        config["watch"]["snapshot_dir"] = str((ROOT / snapshot_dir).resolve())
    snapshot, path = latest_snapshot(config)
    out = Path(options.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    for mode in ("light", "dark"):
        (out / f"incident-{mode}.svg").write_text(incident(mode), encoding="utf-8", newline="\n")
        (out / f"funnel-{mode}.svg").write_text(funnel(mode, config, snapshot, path), encoding="utf-8", newline="\n")
        (out / f"demo-{mode}.svg").write_text(demo(mode), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
