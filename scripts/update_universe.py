#!/usr/bin/env python3

"""Download the current S&P 500 constituents and overwrite universe.csv.

The default source is a public GitHub dataset sourced from Wikipedia, so no
API key is needed. FMP remains available as an optional source:

    python3 scripts/update_universe.py
    UNIVERSE_SOURCE=fmp FMP_API_KEY=your_key \
        python3 scripts/update_universe.py
    python3 scripts/update_universe.py /path/to/universe.csv

Only the standard Python library is used.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


MINIMUM_CONSTITUENTS = 450
DEFAULT_GITHUB_URL = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/main/data/constituents.csv"
)
DEFAULT_FMP_URL = "https://financialmodelingprep.com/stable/sp500-constituent"


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


def fmp_url_with_key(url: str, api_key: str) -> str:
    """Add or replace the FMP API-key query parameter without printing it."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["apikey"] = api_key
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def fetch(url: str, source: str) -> bytes:
    """Fetch a source with a small retry policy and useful error messages."""
    request = Request(
        url,
        headers={
            "User-Agent": "ValueStockWeekly/0.1 universe updater",
            "Accept": "application/json,text/csv;q=0.9,*/*;q=0.8",
        },
    )

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if error.code == 402 and source == "fmp":
                raise RuntimeError(
                    "FMP returned HTTP 402. The FMP S&P 500 endpoint may require "
                    "a paid plan for this account. Try the default source instead: "
                    "python3 scripts/update_universe.py"
                ) from error
            last_error = RuntimeError(
                f"{source} returned HTTP {error.code} ({error.reason})."
            )
        except URLError as error:
            last_error = RuntimeError(f"Could not reach {source}: {error.reason}")

        if attempt < 3:
            time.sleep(2)

    raise RuntimeError(str(last_error or f"Could not download {source}."))


def load_records(payload: bytes, source: str) -> list[dict[str, str]]:
    """Parse either the public CSV or the FMP JSON response."""
    records: list[dict[str, str]] = []

    if source == "github":
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
    else:
        try:
            rows = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError("FMP returned invalid JSON.") from error

        if not isinstance(rows, list):
            raise RuntimeError("FMP did not return a JSON array of constituents.")

        for row in rows:
            if not isinstance(row, dict):
                continue
            records.append(
                {
                    "ticker": normalize_ticker(value(row, "symbol")),
                    "cik": normalize_cik(value(row, "cik")),
                    "name": value(row, "name"),
                    "sector": value(row, "sector"),
                    "subsector": value(row, "subSector", "subsector"),
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
    parser.add_argument(
        "--source",
        choices=("github", "fmp"),
        default=os.environ.get("UNIVERSE_SOURCE", "github"),
        help="Data source (default: UNIVERSE_SOURCE or github).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source
    output_path = args.output.resolve()

    if source == "github":
        url = os.environ.get("GITHUB_UNIVERSE_URL", DEFAULT_GITHUB_URL)
    else:
        api_key = os.environ.get("FMP_API_KEY", "")
        if not api_key:
            print(
                "Error: set FMP_API_KEY when using --source fmp or "
                "UNIVERSE_SOURCE=fmp.",
                file=sys.stderr,
            )
            return 1
        fmp_url = os.environ.get("FMP_SP500_URL", DEFAULT_FMP_URL)
        url = fmp_url_with_key(fmp_url, api_key)

    try:
        payload = fetch(url, source)
        records = load_records(payload, source)
        write_atomically(output_path, records)
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print("The existing universe file was not changed.", file=sys.stderr)
        return 1

    print(f"Wrote {len(records)} constituents to {output_path} using {source} source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
