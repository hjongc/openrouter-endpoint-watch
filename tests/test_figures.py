from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIGURES = (
    "incident-light.svg",
    "incident-dark.svg",
    "demo-light.svg",
    "demo-dark.svg",
    "architecture-light.svg",
    "architecture-dark.svg",
    "board-light.svg",
    "board-dark.svg",
)


def test_renderer_writes_deterministic_well_formed_figures(tmp_path: Path) -> None:
    output = tmp_path / "assets"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    command = [
        sys.executable,
        str(ROOT / "scripts" / "render_figures.py"),
        "--config",
        "examples/watch.toml",
        "--out",
        str(output),
    ]

    first = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    assert {path.name for path in output.glob("*.svg")} == set(FIGURES)
    first_bytes = {}
    for filename in FIGURES:
        path = output / filename
        data = path.read_bytes()
        first_bytes[filename] = data
        root = ET.fromstring(data)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert root.find("{http://www.w3.org/2000/svg}title") is not None
        assert root.find("{http://www.w3.org/2000/svg}desc") is not None
        if filename.startswith("board-"):
            assert len(root.findall(".//*[@data-row]")) == 13

    second = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert second.returncode == 0, second.stderr
    assert {filename: (output / filename).read_bytes() for filename in FIGURES} == first_bytes


def test_architecture_uses_latest_config_snapshot_counts(tmp_path: Path) -> None:
    config = tmp_path / "real-snapshot.toml"
    config.write_text(
        f'''[watch]\nmodels = ["z-ai/glm-5.3-flash"]\nsnapshot_dir = "{ROOT / "snapshots"}"\n\n'''
        '[profiles.production]\nquantizations = ["fp8"]\n'
        'required_parameters = ["seed", "response_format", "tools", "tool_choice"]\n'
        'min_eligible = 2\nhealthy_only = true\n',
        encoding="utf-8",
    )
    output = tmp_path / "real-assets"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    command = [
        sys.executable,
        str(ROOT / "scripts" / "render_figures.py"),
        "--config",
        str(config),
        "--out",
        str(output),
    ]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    root = ET.parse(output / "architecture-light.svg").getroot()
    rendered_text = "".join(root.itertext())
    assert "of 33 eligible today" in rendered_text
