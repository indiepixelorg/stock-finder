#!/usr/bin/env python3

"""Build generated/data/latest_prices.csv from HF Data Library daily bars.

Every security from universe.csv is retained in the output. Securities without
usable prices are marked as excluded with a machine-readable reason. For usable
securities, valuation_price is the median of up to five recent daily closes.

Usage:

    export HF_DATA_API_KEY="your-key"
    python3 scripts/update_prices.py
    python3 scripts/update_prices.py --limit 5

Only the standard Python library is used.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # Reported clearly by main(); keeps --help usable.
    pa = None
    pq = None


DEFAULT_BASE_URL = "https://api.hfdatalibrary.com/v1"
DEFAULT_USER_AGENT = "ValueStockWeekly/0.1 (contact: indiepixelorg@outlook.com)"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_RETRIES = 3
DEFAULT_REQUEST_DELAY_SECONDS = 0.65
LOOKBACK_CALENDAR_DAYS = 10
MAX_PRICE_AGE_DAYS = 7
MIN_OBSERVATIONS = 3
MAX_OBSERVATIONS = 5
DATA_SOURCE = "HF Data Library / IEX HIST"
SOURCE_URL = "https://hfdatalibrary.com/pages/data"

OUTPUT_FIELDS = [
    "ticker",
    "name",
    "valuation_price",
    "price_date",
    "observations",
    "status",
    "reason",
    "data_source",
    "source_url",
]


class HFError(RuntimeError):
    """An HF Data Library request or response failed."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Build an approximate price snapshot for the S&P 500 universe."
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=project_dir / "generated" / "universe.csv",
        help="Input universe CSV (default: ./generated/universe.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_prices.csv",
        help="Output CSV (default: ./generated/data/latest_prices.csv).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process only the first N securities; useful for testing.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help=(
            "Seconds to wait between ticker downloads "
            f"(default: {DEFAULT_REQUEST_DELAY_SECONDS})."
        ),
    )
    return parser.parse_args(argv)


def load_universe(path: Path, limit: int | None) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"Universe file does not exist: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    required_fields = {"ticker", "name"}
    if not rows or not required_fields.issubset(rows[0]):
        raise RuntimeError(
            f"Universe must contain these columns: {', '.join(sorted(required_fields))}"
        )

    securities: list[dict[str, str]] = []
    seen_tickers: set[str] = set()
    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        if not ticker:
            continue
        if ticker in seen_tickers:
            raise RuntimeError(f"Duplicate ticker in universe.csv: {ticker}")
        seen_tickers.add(ticker)
        securities.append(
            {
                "ticker": ticker,
                "name": (row.get("name") or "").strip(),
            }
        )

    if limit is not None:
        if limit < 1:
            raise RuntimeError("--limit must be greater than zero.")
        securities = securities[:limit]

    if not securities:
        raise RuntimeError("No securities found in the universe.")
    return securities


def retry_delay(error: HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                now = datetime.now(retry_at.tzinfo)
                return max(0.0, (retry_at - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return float(2**attempt)


class HFClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.sleep = sleep

    def request_bytes(self, url: str, authenticated: bool) -> bytes:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json,text/csv,*/*;q=0.8",
        }
        if authenticated:
            headers["X-API-Key"] = self.api_key
        request = Request(url, headers=headers)

        last_error: HFError | None = None
        for attempt in range(DEFAULT_RETRIES):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except HTTPError as error:
                safe_url = url.split("?", 1)[0]
                detail = ""
                try:
                    error_payload = json.loads(error.read().decode("utf-8"))
                    if isinstance(error_payload, dict) and error_payload.get("error"):
                        detail = f" {error_payload['error']}"
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
                message = (
                    f"HF Data Library returned HTTP {error.code} for {safe_url}."
                    f"{detail}"
                )
                if error.code in (400, 401, 403, 404):
                    raise HFError(message, error.code) from error
                last_error = HFError(message, error.code)
                if attempt < DEFAULT_RETRIES - 1:
                    self.sleep(retry_delay(error, attempt))
            except (URLError, TimeoutError, OSError) as error:
                last_error = HFError(f"HF Data Library request failed for {url}: {error}")
                if attempt < DEFAULT_RETRIES - 1:
                    self.sleep(float(2**attempt))

        raise last_error or HFError(f"HF Data Library request failed for {url}.")

    def request_json(self, url: str, authenticated: bool) -> dict[str, Any]:
        payload = self.request_bytes(url, authenticated)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HFError(f"HF Data Library returned invalid JSON for {url}.") from error
        if not isinstance(value, dict):
            raise HFError(f"HF Data Library returned an unexpected response for {url}.")
        return value

    def get_symbols(self) -> set[str]:
        payload = self.request_json(f"{self.base_url}/symbols", authenticated=False)
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise HFError("HF Data Library symbol response has no symbols list.")
        tickers = {
            str(item.get("ticker") or "").strip()
            for item in symbols
            if isinstance(item, dict)
        }
        tickers.discard("")
        if not tickers:
            raise HFError("HF Data Library returned an empty symbol list.")
        return tickers

    def download_daily_parquet(self, ticker: str) -> bytes:
        query = urlencode(
            {
                "timeframe": "daily",
                "format": "parquet",
                "version": "clean",
            }
        )
        token_url = (
            f"{self.base_url}/download-token/{quote(ticker, safe='')}?{query}"
        )
        payload = self.request_json(token_url, authenticated=True)
        download_url = payload.get("url")
        if not isinstance(download_url, str) or not download_url.startswith(("http://", "https://")):
            raise HFError(f"HF Data Library returned no download URL for {ticker}.")
        return self.request_bytes(download_url, authenticated=False)


def parse_price_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for format_string in ("%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], format_string).date()
        except ValueError:
            continue
    return None


def parse_close(value: Any) -> float | None:
    try:
        close = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(close) or close <= 0:
        return None
    return close


def select_price(
    payload: bytes,
    today: date,
) -> tuple[float | None, date | None, int, str]:
    if pa is None or pq is None:
        raise RuntimeError(
            "PyArrow is required to read HF daily prices. "
            "Run: python3 -m pip install -r requirements.txt"
        )
    try:
        table = pq.read_table(pa.BufferReader(payload))
    except Exception:
        return None, None, 0, "invalid_price_data"

    date_field = next(
        (
            field
            for field in ("Date", "date", "datetime", "Datetime", "timestamp")
            if field in table.column_names
        ),
        None,
    )
    close_field = next(
        (field for field in ("Close", "close") if field in table.column_names),
        None,
    )
    if not date_field or not close_field:
        return None, None, 0, "invalid_price_data"

    prices_by_date: dict[date, float] = {}
    dates = table.column(date_field).to_pylist()
    closes = table.column(close_field).to_pylist()
    for raw_date, raw_close in zip(dates, closes):
        price_date = parse_price_date(raw_date)
        close = parse_close(raw_close)
        if price_date and price_date <= today and close is not None:
            prices_by_date[price_date] = close

    if not prices_by_date:
        return None, None, 0, "invalid_price_data"

    newest_date = max(prices_by_date)
    if (today - newest_date).days > MAX_PRICE_AGE_DAYS:
        return None, newest_date, 0, "stale_price"

    window_start = today - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    recent = sorted(
        (
            (price_date, close)
            for price_date, close in prices_by_date.items()
            if price_date >= window_start
        ),
        key=lambda item: item[0],
        reverse=True,
    )[:MAX_OBSERVATIONS]
    if len(recent) < MIN_OBSERVATIONS:
        return None, newest_date, len(recent), "insufficient_recent_prices"

    valuation_price = statistics.median(close for _, close in recent)
    return valuation_price, newest_date, len(recent), ""


def format_price(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def excluded_row(
    security: dict[str, str],
    reason: str,
    price_date: date | None = None,
    observations: int = 0,
    sourced: bool = False,
) -> dict[str, Any]:
    return {
        **security,
        "valuation_price": "",
        "price_date": price_date.isoformat() if price_date else "",
        "observations": observations if observations else "",
        "status": "excluded",
        "reason": reason,
        "data_source": DATA_SOURCE if sourced else "",
        "source_url": SOURCE_URL if sourced else "",
    }


def priced_row(
    security: dict[str, str],
    valuation_price: float,
    price_date: date,
    observations: int,
) -> dict[str, Any]:
    return {
        **security,
        "valuation_price": format_price(valuation_price),
        "price_date": price_date.isoformat(),
        "observations": observations,
        "status": "ok",
        "reason": "",
        "data_source": DATA_SOURCE,
        "source_url": SOURCE_URL,
    }


def write_atomically(output_path: Path, rows: list[dict[str, Any]]) -> None:
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
                fieldnames=OUTPUT_FIELDS,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.request_delay < 0:
        print("Error: --request-delay cannot be negative.", file=sys.stderr)
        return 1

    api_key = os.environ.get("HF_DATA_API_KEY", "").strip()
    if not api_key:
        print("Error: HF_DATA_API_KEY is not set.", file=sys.stderr)
        print("The existing price snapshot was not changed.", file=sys.stderr)
        return 1
    if pa is None or pq is None:
        print(
            "Error: PyArrow is required. "
            "Run: python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        print("The existing price snapshot was not changed.", file=sys.stderr)
        return 1

    try:
        securities = load_universe(args.universe.resolve(), args.limit)
        client = HFClient(
            api_key=api_key,
            base_url=os.environ.get("HF_BASE_URL", DEFAULT_BASE_URL),
        )
        available_symbols = client.get_symbols()
        rows: list[dict[str, Any]] = []
        today = date.today()

        print(f"Fetching HF daily prices for {len(securities)} securities...")
        for index, security in enumerate(securities, start=1):
            ticker = security["ticker"]
            if ticker not in available_symbols:
                rows.append(excluded_row(security, "ticker_unavailable"))
            else:
                payload = client.download_daily_parquet(ticker)
                price, price_date, observations, reason = select_price(payload, today)
                if price is None or price_date is None:
                    rows.append(
                        excluded_row(
                            security,
                            reason or "invalid_price_data",
                            price_date=price_date,
                            observations=observations,
                            sourced=True,
                        )
                    )
                else:
                    rows.append(
                        priced_row(security, price, price_date, observations)
                    )
                if args.request_delay:
                    time.sleep(args.request_delay)

            if index % 25 == 0 or index == len(securities):
                print(f"  companies: {index}/{len(securities)}")

        write_atomically(args.output.resolve(), rows)
    except (OSError, RuntimeError, HFError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print("The existing price snapshot was not changed.", file=sys.stderr)
        return 1

    reasons: dict[str, int] = {}
    usable = 0
    for row in rows:
        if row["status"] == "ok":
            usable += 1
        else:
            reason = str(row["reason"])
            reasons[reason] = reasons.get(reason, 0) + 1

    print(f"Wrote {len(rows)} rows to {args.output.resolve()}")
    print(f"  usable prices: {usable}")
    for reason in (
        "ticker_unavailable",
        "stale_price",
        "insufficient_recent_prices",
        "invalid_price_data",
    ):
        if reasons.get(reason):
            print(f"  {reason}: {reasons[reason]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
