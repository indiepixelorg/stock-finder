#!/usr/bin/env bash

# Download the current S&P 500 constituents and overwrite universe.csv.
#
# The default source is a public GitHub dataset sourced from Wikipedia, so no
# API key is needed. FMP remains available as an optional source:
#
#   ./scripts/update_universe.sh
#   UNIVERSE_SOURCE=fmp FMP_API_KEY=your_key ./scripts/update_universe.sh
#   ./scripts/update_universe.sh /path/to/universe.csv

set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
OUTPUT_FILE="${1:-$PROJECT_DIR/universe.csv}"
UNIVERSE_SOURCE="${UNIVERSE_SOURCE:-github}"

GITHUB_DATASET_URL="${GITHUB_UNIVERSE_URL:-https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv}"
FMP_API_URL="${FMP_SP500_URL:-https://financialmodelingprep.com/stable/sp500-constituent}"

if ! command -v curl >/dev/null 2>&1; then
  echo "Error: required command not found: curl" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: required command not found: python3" >&2
  exit 1
fi

case "$UNIVERSE_SOURCE" in
  github)
    INPUT_URL="$GITHUB_DATASET_URL"
    INPUT_FORMAT="csv"
    ;;
  fmp)
    if [[ -z "${FMP_API_KEY:-}" ]]; then
      echo "Error: set FMP_API_KEY when UNIVERSE_SOURCE=fmp." >&2
      exit 1
    fi
    INPUT_URL="$FMP_API_URL"
    INPUT_FORMAT="json"
    ;;
  *)
    echo "Error: UNIVERSE_SOURCE must be github or fmp." >&2
    exit 1
    ;;
esac

OUTPUT_DIR="$(dirname -- "$OUTPUT_FILE")"
mkdir -p "$OUTPUT_DIR"

TEMP_INPUT="$(mktemp "${TMPDIR:-/tmp}/sp500-constituents.XXXXXX")"
TEMP_CSV="$(mktemp "${TMPDIR:-/tmp}/universe.XXXXXX.csv")"
trap 'rm -f "$TEMP_INPUT" "$TEMP_CSV"' EXIT

if [[ "$INPUT_FORMAT" == "json" ]]; then
  if ! curl \
    --fail \
    --silent \
    --show-error \
    --retry 3 \
    --retry-delay 2 \
    --get "$INPUT_URL" \
    --data-urlencode "apikey=$FMP_API_KEY" \
    --output "$TEMP_INPUT"; then
    echo "Error: FMP did not return the constituent list." >&2
    echo "The FMP S&P 500 endpoint may require a paid plan for your account." >&2
    echo "Try the default source instead: ./scripts/update_universe.sh" >&2
    exit 1
  fi
else
  curl \
    --fail \
    --silent \
    --show-error \
    --retry 3 \
    --retry-delay 2 \
    "$INPUT_URL" \
    --output "$TEMP_INPUT"
fi

python3 - "$TEMP_INPUT" "$TEMP_CSV" "$INPUT_FORMAT" <<'PY'
import csv
import json
import re
import sys

input_path, output_path, input_format = sys.argv[1:]


def value(row, *keys):
    for key in keys:
        item = row.get(key)
        if item is not None and str(item).strip():
            return str(item).strip()
    return ""


def normalize_ticker(ticker):
    # FMP uses BRK-B while the public CSV commonly uses BRK.B.
    return ticker.strip().replace(".", "-")


def normalize_cik(cik):
    digits = re.sub(r"\D", "", str(cik or ""))
    return digits.zfill(10) if digits else ""


if input_format == "csv":
    with open(input_path, newline="", encoding="utf-8") as handle:
        source_rows = list(csv.DictReader(handle))

    records = [
        {
            "ticker": normalize_ticker(value(row, "Symbol")),
            "cik": normalize_cik(value(row, "CIK")),
            "name": value(row, "Security"),
            "sector": value(row, "GICS Sector"),
            "subsector": value(row, "GICS Sub-Industry"),
        }
        for row in source_rows
    ]
else:
    with open(input_path, encoding="utf-8") as handle:
        source_rows = json.load(handle)

    records = [
        {
            "ticker": normalize_ticker(value(row, "symbol")),
            "cik": normalize_cik(value(row, "cik")),
            "name": value(row, "name"),
            "sector": value(row, "sector"),
            "subsector": value(row, "subSector", "subsector"),
        }
        for row in source_rows
    ] if isinstance(source_rows, list) else []

records = [record for record in records if record["ticker"] and record["name"]]
records.sort(key=lambda record: record["ticker"])

if len(records) < 450:
    raise SystemExit(
        f"Error: expected at least 450 constituents, received {len(records)}."
    )

with open(output_path, "w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(
        handle,
        fieldnames=["ticker", "cik", "name", "sector", "subsector"],
    )
    writer.writeheader()
    writer.writerows(records)

print(f"Prepared {len(records)} constituents")
PY

mv "$TEMP_CSV" "$OUTPUT_FILE"
echo "Wrote universe to $OUTPUT_FILE using $UNIVERSE_SOURCE source"
