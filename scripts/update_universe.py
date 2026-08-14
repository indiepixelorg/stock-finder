#!/usr/bin/env python3

"""Download the current S&P 500 constituents and overwrite universe.csv.

The source is the public ``constituents.csv`` file from the datasets GitHub
repository, so no API key is needed:

    python3 scripts/update_universe.py
    python3 scripts/update_universe.py /path/to/universe.csv

Only the standard Python library is used.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MINIMUM_CONSTITUENTS = 450
DEFAULT_CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/main/data/constituents.csv"
)


def value(row: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty value for the supplied keys."""
    for key in keys:
        item = row.get(key)
        if item is not None and str(item).strip():
            return str(item).strip()
    return ""


def normalize_ticker(ticker: str) -> str:
    """Use the hyphen form commonly expected by US market-data providers."""
    return ticker.strip().replace(".", "-")


def normalize_cik(cik: Any) -> str:
    """Return a zero-padded ten-digit SEC CIK when one is available."""
    digits = re.sub(r"\D", "", str(cik or ""))
    return digits.zfill(10) if digits else ""


def fetch(url: str) -> bytes:
    """Fetch a source with a small retry policy and useful error messages."""
    request = Request(
        url,
        headers={
            "User-Agent": "ValueStockWeekly/0.1 universe updater",
            "Accept": "text/csv,*/*;q=0.8",
        },
    )

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            last_error = RuntimeError(
                f"constituents.csv returned HTTP {error.code} ({error.reason})."
            )
        except URLError as error:
            last_error = RuntimeError(
                f"Could not reach constituents.csv: {error.reason}"
            )

        if attempt < 3:
            time.sleep(2)

    raise RuntimeError(str(last_error or "Could not download constituents.csv."))


def load_records(payload: bytes) -> list[dict[str, str]]:
    """Parse the public constituents.csv response."""
    records: list[dict[str, str]] = []

    text = payload.decode("utf-8-sig")
    rows = csv.DictReader(text.splitlines())
    for row in rows:
        records.append(
            {
                "ticker": normalize_ticker(value(row, "Symbol")),
                "cik": normalize_cik(value(row, "CIK")),
                "name": value(row, "Security"),
                "sector": value(row, "GICS Sector"),
                "subsector": value(row, "GICS Sub-Industry"),
            }
        )

    records = [record for record in records if record["ticker"] and record["name"]]
    records.sort(key=lambda record: record["ticker"])

    if len(records) < MINIMUM_CONSTITUENTS:
        raise RuntimeError(
            f"Expected at least {MINIMUM_CONSTITUENTS} constituents, "
            f"received {len(records)}."
        )

    duplicate_tickers = len(records) - len({record["ticker"] for record in records})
    if duplicate_tickers:
        raise RuntimeError(f"The source contains {duplicate_tickers} duplicate tickers.")

    return records


def write_atomically(output_path: Path, records: list[dict[str, str]]) -> None:
    """Write beside the target and replace it only after successful generation."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            writer = csv.DictWriter(
                temporary_file,
                fieldnames=["ticker", "cik", "name", "sector", "subsector"],
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(records)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Download S&P 500 constituents into a normalized universe CSV."
    )
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=project_dir / "universe.csv",
        help="Output CSV path (default: ./universe.csv).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output.resolve()
    url = DEFAULT_CONSTITUENTS_URL

    try:
        payload = fetch(url)
        records = load_records(payload)
        write_atomically(output_path, records)
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print("The existing universe file was not changed.", file=sys.stderr)
        return 1

    print(f"Wrote {len(records)} constituents to {output_path} from constituents.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
