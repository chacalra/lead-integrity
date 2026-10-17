"""Run every example scenario and write its report.html, exceptions.csv and summary.json.

Usage: uv run python scripts/build_reports.py [scenarios_dir] [out_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from lead_integrity.config import execute, load_run
from lead_integrity.report import write_outputs

ROOT = Path(__file__).resolve().parents[1]


def build(scenarios_dir: Path, out_dir: Path) -> list[Path]:
    written = []
    for run_file in sorted(scenarios_dir.glob("*/run.toml")):
        run = load_run(run_file)
        target = out_dir / run_file.parent.name
        write_outputs(execute(run), run.settings, target, run.title, run.description)
        written.append(target / "report.html")
    return written


if __name__ == "__main__":
    scenarios = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "scenarios"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "reports"
    for path in build(scenarios, out):
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
