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
from orwatch.core import (  # noqa: E402
    diff_snapshots,
    eligible_endpoints,
    list_snapshots,
    read_snapshot,
)


FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
PALETTES = {
    "light": {
        "surface": "#fcfcfb", "border": "#e1e0d9", "ink": "#0b0b0b",
        "secondary": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
        "axis": "#c3c2b7", "blue": "#2a78d6", "serious": "#ec835a",
        "critical": "#d03b3b", "good": "#0ca30c",
    },
    "dark": {
        "surface": "#1a1a19", "border": "#2c2c2a", "ink": "#f0efec",
        "secondary": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
        "axis": "#383835", "blue": "#3987e5", "serious": "#ec835a",
        "critical": "#d03b3b", "good": "#0ca30c",
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
              weight: int | str = 400, family: str = FONT, line_height: int = 17,
              anchor: str = "start") -> str:
    body = "".join(f'<tspan x="{x:g}" dy="{0 if i == 0 else line_height}">{esc(line)}</tspan>'
                   for i, line in enumerate(lines))
    anchor_attr = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (f'<text x="{x:g}" y="{y:g}" fill="{fill}" font-family="{family}" '
            f'font-size="{size}px" font-weight="{weight}"{anchor_attr}>{body}</text>')


def svg_start(kind: str, mode: str, height: int, title: str, desc: str, width: int = 880) -> list[str]:
    p = PALETTES[mode]
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{esc(title)}</title>',
        f'<desc id="desc">{esc(desc)}</desc>',
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="{p["surface"]}" stroke="{p["border"]}"/>',
    ]


def svg_finish(parts: list[str]) -> str:
    return "\n".join(parts + ["</svg>", ""])


def incident(mode: str) -> str:
    p = PALETTES[mode]
    title = "My pipeline's eligible OpenRouter providers"
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


def latest_snapshot(config: dict) -> dict:
    model = config["watch"]["models"][0]
    paths = list_snapshots(config["watch"]["snapshot_dir"], model)
    if not paths:
        raise FileNotFoundError(f"no snapshots found for {model}")
    return read_snapshot(paths[-1])


def architecture(mode: str, count: int, eligible_count: int) -> str:
    p = PALETTES[mode]
    title = "orwatch: know when your provider set changes"
    parts = svg_start("architecture", mode, 262, title,
                      "OpenRouter endpoint metadata is snapshotted, diffed, checked against a profile, and sent to history, issues, and CI gates.", width=1200)
    parts.append(text(24, 35, title, fill=p["ink"], size=18, weight=600))
    surface = "#f4f3ef" if mode == "light" else "#232322"
    node_width, node_height = 207, 82
    node_y = 106
    nodes = [
        (24, "OpenRouter", "public endpoint list", "no API key"),
        (255, "snapshot", "daily, via GitHub Actions", f"{count} endpoints today"),
        (486, "diff", "10 named change classes", "fp8 → fp4 = replaced"),
        (717, "profile check", "fp8 + seed, tools, …", f"{eligible_count} of {count} eligible today"),
    ]
    for x, label, line1, line2 in nodes:
        parts.append(f'<rect x="{x}" y="{node_y}" width="{node_width}" height="{node_height}" rx="10" fill="{surface}" stroke="{p["grid"]}" stroke-width="1"/>')
        parts.append(text(x + 16, node_y + 29, label, fill=p["ink"], size=14, weight=600))
        parts.append(text(x + 16, node_y + 48, line1, fill=p["secondary"], size=12))
        parts.append(text(x + 16, node_y + 64, line2, fill=p["secondary"], size=12))

    for x1, x2 in ((231, 255), (462, 486), (693, 717)):
        parts.append(f'<line x1="{x1}" y1="{node_y + node_height // 2}" x2="{x2 - 4}" y2="{node_y + node_height // 2}" stroke="{p["muted"]}" stroke-width="1.5"/>')
        parts.append(f'<path d="M {x2 - 4} {node_y + 37} L {x2} {node_y + 41} L {x2 - 4} {node_y + 45} Z" fill="{p["muted"]}"/>')

    outputs = [
        (56, p["blue"], "HISTORY.md", "daily timeline"),
        (120, p["serious"], "GitHub issue", "on a breaking change"),
        (184, p["critical"], "exit 7", "pre-run gate"),
    ]
    # A shared trunk branches to vertically ordered outputs without crossing arrows.
    trunk_x, output_x, output_width, output_height = 940, 960, 220, 54
    center_y = node_y + node_height // 2
    output_centers = [y + output_height // 2 for y, *_ in outputs]
    parts.append(f'<path d="M 924 {center_y} H {trunk_x} M {trunk_x} {output_centers[0]} V {output_centers[-1]}" fill="none" stroke="{p["muted"]}" stroke-width="1.5"/>')
    for y, color, label, detail in outputs:
        cy = y + output_height // 2
        parts.append(f'<rect x="{output_x}" y="{y}" width="{output_width}" height="{output_height}" rx="8" fill="{surface}" stroke="{p["grid"]}" stroke-width="1"/>')
        parts.append(f'<line x1="{trunk_x}" y1="{cy}" x2="{output_x - 4}" y2="{cy}" stroke="{p["muted"]}" stroke-width="1.5"/>')
        parts.append(f'<path d="M {output_x - 4} {cy - 4} L {output_x} {cy} L {output_x - 4} {cy + 4} Z" fill="{p["muted"]}"/>')
        parts.append(f'<circle cx="{output_x + 12}" cy="{cy}" r="5" fill="{color}"/>')
        parts.append(text(output_x + 25, y + 23, label, fill=p["ink"], size=13, weight=600))
        parts.append(text(output_x + 25, y + 41, detail, fill=p["secondary"], size=12))
    return svg_finish(parts)


def board_data() -> tuple[list[dict], int, int, int, int]:
    config = load_config(ROOT / "examples" / "watch.toml")
    config["watch"]["snapshot_dir"] = str(ROOT / config["watch"]["snapshot_dir"])
    profile = config["profiles"]["production"]
    model = "z-ai/glm-5.3-flash"
    before = read_snapshot(ROOT / "examples/snapshots/z-ai__glm-5.3-flash/2026-10-07.json")
    after = read_snapshot(ROOT / "examples/snapshots/z-ai__glm-5.3-flash/2026-10-08.json")
    changes = diff_snapshots(before, after, {"production": profile}, "2026-10-07", "2026-10-08")
    before_eps, after_eps = before["endpoints"], after["endpoints"]
    replace_after: dict[str, dict] = {}
    changed: dict[str, dict] = {}
    for change in changes:
        kind = change["class"]
        if kind == "endpoint_replaced":
            replace_after[change["after"]] = change
            changed[change["after"]] = change
        elif kind in {"endpoint_removed", "quantization_changed"}:
            row_tag = change.get("tag") or change.get("before")
            changed[row_tag] = change
    rows_by_tag: dict[str, dict] = {}
    replaced_old = {change["before"] for change in replace_after.values()}
    for tag in sorted(set(before_eps) | set(after_eps)):
        if tag in replaced_old:
            continue
        old, new = before_eps.get(tag), after_eps.get(tag)
        if not any(ep and ep.get("quantization", "unknown") in profile["quantizations"] for ep in (old, new)):
            continue
        rows_by_tag[tag] = {"tag": tag, "old": old, "new": new, "change": changed.get(tag)}
    for new_tag, change in replace_after.items():
        old, new = before_eps[change["before"]], after_eps[new_tag]
        if any(ep.get("quantization", "unknown") in profile["quantizations"] for ep in (old, new)):
            rows_by_tag[new_tag] = {"tag": new_tag, "old": old, "new": new, "change": change}
    eligible_old = set(eligible_endpoints(before, profile))
    eligible_new = set(eligible_endpoints(after, profile))
    for row in rows_by_tag.values():
        if row["change"] and row["change"]["class"] == "endpoint_replaced":
            row["old_eligible"] = row["change"]["before"] in eligible_old
            row["new_eligible"] = row["tag"] in eligible_new
        else:
            row["old_eligible"] = row["tag"] in eligible_old
            row["new_eligible"] = row["tag"] in eligible_new
    rows = sorted(rows_by_tag.values(), key=lambda row: (not row["old_eligible"], (row["new"] or row["old"])["provider_name"].casefold(), row["tag"]))
    # Remaining identities are the listed endpoints outside the selected quantization set.
    represented = len(rows)
    other = max(0, len(set(before_eps) | set(after_eps)) - represented)
    return rows, len(eligible_old), len(eligible_new), int(profile.get("min_eligible", 1)), other


def board(mode: str) -> str:
    p = PALETTES[mode]
    rows, eligible_old, eligible_new, minimum, other = board_data()
    title = "What orwatch diff sees: one bad day, endpoint by endpoint"
    subtitle = "z-ai/glm-5.3-flash · Oct 7 → Oct 8 · endpoints whose quantization matches the production profile (fp8)"
    height = 576
    parts = svg_start("board", mode, height, title,
                      "An endpoint-by-endpoint board compares production-profile capabilities and eligibility across the October 7 and October 8 snapshots.")
    parts += [text(24, 34, title, fill=p["ink"], size=18, weight=600),
              text(24, 58, subtitle, fill=p["secondary"], size=13)]
    header_baseline = 121
    header_rule = header_baseline + 10
    date_xs = (650, 700)
    parts.append(text((date_xs[0] + date_xs[1]) / 2, 87, "Eligible", fill=p["muted"], size=11, weight=600, anchor="middle"))
    parts.append(f'<line x1="630" y1="93" x2="720" y2="93" stroke="{p["grid"]}" stroke-width="1"/>')
    headers = [
        (24, "Endpoint"), (185, "Quant."), (310, "seed"),
        (390, "response_format"), (470, "tools"), (550, "tool_choice"),
        (580, "Status"), (date_xs[0], "Oct 7"), (date_xs[1], "Oct 8"),
        (752, "Change"),
    ]
    for x, label in headers:
        if label == "response_format":
            parts.append(multiline(x, header_baseline - 13, ["response_", "format"], fill=p["muted"], size=11, weight=600, line_height=13, anchor="middle"))
        elif label == "tool_choice":
            parts.append(multiline(x, header_baseline - 13, ["tool_", "choice"], fill=p["muted"], size=11, weight=600, line_height=13, anchor="middle"))
        else:
            parts.append(text(x, header_baseline, label, fill=p["muted"], size=11, weight=600,
                              anchor="middle" if label in {"seed", "tools", "Oct 7", "Oct 8"} else "start"))
    parts.append(f'<line x1="24" y1="{header_rule}" x2="856" y2="{header_rule}" stroke="{p["grid"]}" stroke-width="1"/>')
    body_top = header_rule + 6
    for index, row in enumerate(rows):
        y = body_top + index * 28
        ep = row["new"] or row["old"]
        old, new, change = row["old"], row["new"], row["change"]
        if change:
            wash = "#fdf1ec" if mode == "light" else "#2a201c"
            parts.append(f'<rect data-row="{index}" x="24" y="{y}" width="832" height="28" fill="{wash}"/>')
        else:
            parts.append(f'<g data-row="{index}">')
        parts.append(text(30, y + 12, ep["provider_name"], fill=p["ink"], size=13, weight=500))
        parts.append(text(30, y + 24, row["tag"], fill=p["muted"], size=10))
        old_quant = old.get("quantization", "unknown") if old else None
        new_quant = new.get("quantization", "unknown") if new else None
        if old and new and old_quant != new_quant:
            parts.append(text(185, y + 18, f"{old_quant} → {new_quant}", fill=p["ink"], size=11))
        else:
            parts.append(text(185, y + 18, new_quant or old_quant or "", fill=p["ink"], size=11))
        caps = ep.get("supported_parameters", [])
        for x, param in zip((310, 390, 470, 550), ("seed", "response_format", "tools", "tool_choice")):
            parts.append(text(x, y + 18, "✓" if param in caps else "×", fill=p["secondary"] if param in caps else p["muted"], size=13, anchor="middle"))
        status_ep = new or old
        status = "ok" if status_ep.get("status", 0) >= 0 else str(status_ep["status"]).replace("-", "−")
        parts.append(text(580, y + 18, status, fill=p["secondary"], size=11))
        for x, exists, eligible in ((date_xs[0], old is not None, row["old_eligible"]), (date_xs[1], new is not None, row["new_eligible"])):
            if exists:
                if eligible:
                    parts.append(f'<circle cx="{x}" cy="{y + 14}" r="5" fill="{p["blue"]}"/>')
                else:
                    parts.append(f'<circle cx="{x}" cy="{y + 14}" r="5" fill="none" stroke="{p["muted"]}" stroke-width="1.5"/>')
        if change:
            kind = change["class"]
            label = "replaced" if kind in {"endpoint_replaced", "quantization_changed"} else "removed"
            color = p["critical"] if kind == "endpoint_removed" else p["serious"]
            parts.append(f'<circle cx="752" cy="{y + 14}" r="5" fill="{color}"/>')
            parts.append(text(764, y + 18, label, fill=p["ink"], size=12))
        parts.append(f'<line x1="24" y1="{y + 28}" x2="856" y2="{y + 28}" stroke="{p["grid"]}" stroke-width="1"/>')
        if not change:
            parts.append('</g>')
    footer_y = body_top + len(rows) * 28 + 26
    if eligible_new < minimum:
        parts.append(f'<circle cx="29" cy="{footer_y - 4}" r="5" fill="{p["critical"]}"/>')
        footer_x = 42
    else:
        footer_x = 24
    footer = f"Eligible: {eligible_old} → {eligible_new} · min_eligible {minimum} · check exits 7"
    parts.append(text(footer_x, footer_y, footer, fill=p["ink"], size=13, weight=600))
    footnote = f"The other {other} listed endpoints are never eligible for this profile (fp4, nvfp4, unknown)."
    parts.append(text(24, footer_y + 23, footnote, fill=p["muted"], size=11))
    return svg_finish(parts)


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
    # Match the CLI: relative snapshot directories are resolved from the repository cwd.
    snapshot_dir = Path(config["watch"]["snapshot_dir"])
    if not snapshot_dir.is_absolute():
        config["watch"]["snapshot_dir"] = str((ROOT / snapshot_dir).resolve())
    snapshot = latest_snapshot(config)
    out = Path(options.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    profiles = config.get("profiles", {})
    profile = profiles.get("production", next(iter(profiles.values()), {}))
    endpoint_count = len(snapshot["endpoints"])
    eligible_count = len(eligible_endpoints(snapshot, profile))
    for mode in ("light", "dark"):
        (out / f"incident-{mode}.svg").write_text(incident(mode), encoding="utf-8", newline="\n")
        (out / f"demo-{mode}.svg").write_text(demo(mode), encoding="utf-8", newline="\n")
        (out / f"architecture-{mode}.svg").write_text(architecture(mode, endpoint_count, eligible_count), encoding="utf-8", newline="\n")
        (out / f"board-{mode}.svg").write_text(board(mode), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
