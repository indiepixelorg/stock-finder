#!/usr/bin/env python3

"""Run the complete Value Stock Weekly data-to-site pipeline.

Each stage remains an independently runnable script. This orchestrator provides
one command for local end-to-end builds and GitHub Pages deployments.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path


PIPELINE_STEPS = (
    ("Update S&P 500 universe", "update_universe.py"),
    ("Update SEC financial snapshot", "update_snapshot.py"),
    ("Update weekly prices", "update_prices.py"),
    ("Build valuation screen", "build_screen.py"),
    ("Rank companies", "rank_companies.py"),
    ("Build research notes", "build_research_notes.py"),
    ("Build static site", "build_site.py"),
)


def run_pipeline(
    *,
    environment: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    python: str = sys.executable,
    project_dir: Path | None = None,
) -> int:
    env = dict(os.environ if environment is None else environment)
    if not env.get("HF_DATA_API_KEY", "").strip():
        print(
            "Error: HF_DATA_API_KEY is not set. Export it locally or add it as "
            "a GitHub Actions repository secret.",
            file=sys.stderr,
        )
        return 1

    scripts_dir = Path(__file__).resolve().parent
    root = project_dir or scripts_dir.parent
    total = len(PIPELINE_STEPS)

    for index, (label, script_name) in enumerate(PIPELINE_STEPS, start=1):
        script_path = scripts_dir / script_name
        print(f"[{index}/{total}] {label}", flush=True)
        try:
            result = runner(
                [python, str(script_path)],
                cwd=root,
                env=env,
                check=False,
            )
        except OSError as error:
            print(f"Error: could not start {script_name}: {error}", file=sys.stderr)
            return 1

        if result.returncode:
            print(
                f"Error: {script_name} failed with exit code {result.returncode}.",
                file=sys.stderr,
            )
            return result.returncode

    print(f"Pipeline complete. Static site: {root / 'generated' / 'site'}")
    return 0


def main() -> int:
    try:
        return run_pipeline()
    except KeyboardInterrupt:
        print("\nPipeline cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
