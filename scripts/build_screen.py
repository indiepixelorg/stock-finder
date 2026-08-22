#!/usr/bin/env python3

"""Join universe, SEC fundamentals, and HF prices into latest_screen.csv.

The output keeps one row per security in universe.csv. Calculations are left
blank when required inputs are missing or when a valuation multiple would have
an economically meaningless non-positive denominator.

Usage:

    python3 scripts/build_screen.py
    python3 scripts/build_screen.py --output /tmp/latest_screen.csv

Only the standard Python library is used.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
import sys
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Sequence


HISTORY_SLOTS = 5
MIN_MEDIAN_YEARS = 3

OUTPUT_FIELDS = [
    "ticker",
    "cik",
    "name",
    "sector",
    "subsector",
    "valuation_price",
    "price_date",
    "price_observations",
    "price_status",
    "price_reason",
    "filing_date",
    "period_end",
    "revenue_ttm",
    "operating_income_ttm",
    "net_income_ttm",
    "free_cash_flow_ttm",
    "cash_and_equivalents",
    "total_debt",
    "stockholders_equity",
    "shares_outstanding",
    "market_cap",
    "net_debt",
    "enterprise_value",
    "earnings_yield",
    "free_cash_flow_yield",
    "price_to_earnings",
    "price_to_fcf",
    "ev_to_operating_income",
    "operating_margin",
    "five_year_revenue_growth",
    "positive_fcf_years",
    "historical_fcf_years",
    "net_debt_to_fcf",
    "price_to_book",
    "five_year_median_fcf",
    "historical_net_income_years",
    "five_year_median_net_income",
    "calculation_status",
    "calculation_warnings",
    "snapshot_source_url",
    "price_source_url",
]

RAW_FINANCIAL_FIELDS = (
    "revenue_ttm",
    "operating_income_ttm",
    "net_income_ttm",
    "free_cash_flow_ttm",
    "cash_and_equivalents",
    "total_debt",
    "stockholders_equity",
    "shares_outstanding",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Build valuation metrics from the latest price and SEC snapshots."
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=project_dir / "generated" / "universe.csv",
        help="Universe CSV (default: ./generated/universe.csv).",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_snapshot.csv",
        help="SEC snapshot CSV (default: ./generated/data/latest_snapshot.csv).",
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_prices.csv",
        help="Price snapshot CSV (default: ./generated/data/latest_prices.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_screen.csv",
        help="Output CSV (default: ./generated/data/latest_screen.csv).",
    )
    return parser.parse_args(argv)


def load_csv(path: Path, required_fields: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"Input file does not exist: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = set(reader.fieldnames or [])
    if not rows or not required_fields.issubset(fields):
        missing = ", ".join(sorted(required_fields - fields))
        raise RuntimeError(f"{path} is empty or missing required columns: {missing}")
    return rows


def index_rows(rows: list[dict[str, str]], label: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        if not ticker:
            raise RuntimeError(f"{label} contains a row without a ticker.")
        if ticker in indexed:
            raise RuntimeError(f"{label} contains duplicate ticker: {ticker}")
        indexed[ticker] = row
    return indexed


def require_same_tickers(
    universe: list[dict[str, str]],
    indexed: dict[str, dict[str, str]],
    label: str,
) -> None:
    universe_tickers = {row["ticker"].strip() for row in universe}
    indexed_tickers = set(indexed)
    missing = sorted(universe_tickers - indexed_tickers)
    extra = sorted(indexed_tickers - universe_tickers)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {len(missing)} ({', '.join(missing[:5])})")
        if extra:
            details.append(f"extra {len(extra)} ({', '.join(extra[:5])})")
        raise RuntimeError(f"{label} ticker set does not match universe.csv: {'; '.join(details)}")


def as_decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def as_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def format_amount(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def format_ratio(value: Decimal | None) -> str:
    if value is None:
        return ""
    # Preserve significant digits for both ordinary ratios and extreme audit
    # cases caused by malformed source share counts (for example, 0.00000003x).
    with localcontext() as context:
        context.prec = 15
        text = format(+value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def divide(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 28
        return numerator / denominator


def positive_denominator_ratio(
    numerator: Decimal | None, denominator: Decimal | None
) -> Decimal | None:
    if denominator is None or denominator <= 0:
        return None
    return divide(numerator, denominator)


def annual_values(snapshot: dict[str, str], field: str) -> list[Decimal]:
    values: list[Decimal] = []
    for slot in range(HISTORY_SLOTS):
        value = as_decimal(snapshot.get(f"{field}_fy{slot}"))
        if value is not None:
            values.append(value)
    return values


def historical_median(values: list[Decimal]) -> Decimal | None:
    if len(values) < MIN_MEDIAN_YEARS:
        return None
    return statistics.median(values)


def revenue_cagr(snapshot: dict[str, str]) -> Decimal | None:
    newest = as_decimal(snapshot.get("revenue_fy0"))
    oldest = as_decimal(snapshot.get("revenue_fy4"))
    newest_date = as_date(snapshot.get("annual_period_end_0"))
    oldest_date = as_date(snapshot.get("annual_period_end_4"))
    if (
        newest is None
        or oldest is None
        or newest <= 0
        or oldest <= 0
        or newest_date is None
        or oldest_date is None
        or newest_date <= oldest_date
    ):
        return None
    years = (newest_date - oldest_date).days / 365.2425
    if years <= 0:
        return None
    growth = math.pow(float(newest / oldest), 1.0 / years) - 1.0
    if not math.isfinite(growth):
        return None
    return Decimal(str(growth))


def add_input_warning(
    warnings: list[str],
    field: str,
    value: Decimal | None,
    require_positive: bool,
) -> None:
    if value is None:
        warnings.append(f"missing_{field}")
    elif require_positive and value <= 0:
        warnings.append(f"nonpositive_{field}")


def build_row(
    universe: dict[str, str],
    snapshot: dict[str, str],
    price: dict[str, str],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": universe["ticker"].strip(),
        "cik": (universe.get("cik") or "").strip(),
        "name": (universe.get("name") or "").strip(),
        "sector": (universe.get("sector") or "").strip(),
        "subsector": (universe.get("subsector") or "").strip(),
        "valuation_price": (price.get("valuation_price") or "").strip(),
        "price_date": (price.get("price_date") or "").strip(),
        "price_observations": (price.get("observations") or "").strip(),
        "price_status": (price.get("status") or "").strip(),
        "price_reason": (price.get("reason") or "").strip(),
        "filing_date": (snapshot.get("filing_date") or "").strip(),
        "period_end": (snapshot.get("period_end") or "").strip(),
        "snapshot_source_url": (snapshot.get("source_url") or "").strip(),
        "price_source_url": (price.get("source_url") or "").strip(),
    }
    for field in RAW_FINANCIAL_FIELDS:
        row[field] = (snapshot.get(field) or "").strip()

    if row["price_status"] != "ok":
        row["calculation_status"] = "excluded"
        row["calculation_warnings"] = row["price_reason"] or "price_unavailable"
        return {field: row.get(field, "") for field in OUTPUT_FIELDS}

    valuation_price = as_decimal(row["valuation_price"])
    shares = as_decimal(row["shares_outstanding"])
    revenue = as_decimal(row["revenue_ttm"])
    operating_income = as_decimal(row["operating_income_ttm"])
    net_income = as_decimal(row["net_income_ttm"])
    free_cash_flow = as_decimal(row["free_cash_flow_ttm"])
    cash = as_decimal(row["cash_and_equivalents"])
    debt = as_decimal(row["total_debt"])
    equity = as_decimal(row["stockholders_equity"])

    warnings: list[str] = []
    add_input_warning(warnings, "valuation_price", valuation_price, True)
    add_input_warning(warnings, "shares_outstanding", shares, True)
    add_input_warning(warnings, "revenue_ttm", revenue, True)
    add_input_warning(warnings, "operating_income_ttm", operating_income, True)
    add_input_warning(warnings, "net_income_ttm", net_income, True)
    add_input_warning(warnings, "free_cash_flow_ttm", free_cash_flow, True)
    add_input_warning(warnings, "cash_and_equivalents", cash, False)
    add_input_warning(warnings, "total_debt", debt, False)
    add_input_warning(warnings, "stockholders_equity", equity, True)

    market_cap = (
        valuation_price * shares
        if valuation_price is not None
        and valuation_price > 0
        and shares is not None
        and shares > 0
        else None
    )
    net_debt = debt - cash if debt is not None and cash is not None else None
    enterprise_value = (
        market_cap + net_debt
        if market_cap is not None and net_debt is not None
        else None
    )

    earnings_yield = positive_denominator_ratio(net_income, market_cap)
    free_cash_flow_yield = positive_denominator_ratio(free_cash_flow, market_cap)
    price_to_earnings = positive_denominator_ratio(market_cap, net_income)
    price_to_fcf = positive_denominator_ratio(market_cap, free_cash_flow)
    ev_to_operating_income = (
        positive_denominator_ratio(enterprise_value, operating_income)
        if enterprise_value is not None and enterprise_value > 0
        else None
    )
    operating_margin = positive_denominator_ratio(operating_income, revenue)
    net_debt_to_fcf = positive_denominator_ratio(net_debt, free_cash_flow)
    price_to_book = positive_denominator_ratio(market_cap, equity)

    revenue_growth = revenue_cagr(snapshot)
    if revenue_growth is None:
        warnings.append("insufficient_revenue_history")

    fcf_history = annual_values(snapshot, "free_cash_flow")
    net_income_history = annual_values(snapshot, "net_income")
    median_fcf = historical_median(fcf_history)
    median_net_income = historical_median(net_income_history)
    if median_fcf is None:
        warnings.append("insufficient_fcf_history")
    if median_net_income is None:
        warnings.append("insufficient_net_income_history")

    row.update(
        {
            "market_cap": format_amount(market_cap),
            "net_debt": format_amount(net_debt),
            "enterprise_value": format_amount(enterprise_value),
            "earnings_yield": format_ratio(earnings_yield),
            "free_cash_flow_yield": format_ratio(free_cash_flow_yield),
            "price_to_earnings": format_ratio(price_to_earnings),
            "price_to_fcf": format_ratio(price_to_fcf),
            "ev_to_operating_income": format_ratio(ev_to_operating_income),
            "operating_margin": format_ratio(operating_margin),
            "five_year_revenue_growth": format_ratio(revenue_growth),
            "positive_fcf_years": (
                sum(value > 0 for value in fcf_history) if fcf_history else ""
            ),
            "historical_fcf_years": len(fcf_history),
            "net_debt_to_fcf": format_ratio(net_debt_to_fcf),
            "price_to_book": format_ratio(price_to_book),
            "five_year_median_fcf": format_amount(median_fcf),
            "historical_net_income_years": len(net_income_history),
            "five_year_median_net_income": format_amount(median_net_income),
            "calculation_status": "ok" if not warnings else "partial",
            "calculation_warnings": ";".join(dict.fromkeys(warnings)),
        }
    )
    return {field: row.get(field, "") for field in OUTPUT_FIELDS}


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
    try:
        universe = load_csv(
            args.universe.resolve(),
            {"ticker", "cik", "name", "sector", "subsector"},
        )
        snapshots = index_rows(
            load_csv(
                args.snapshot.resolve(),
                {"ticker", *RAW_FINANCIAL_FIELDS},
            ),
            "latest_snapshot.csv",
        )
        prices = index_rows(
            load_csv(
                args.prices.resolve(),
                {"ticker", "valuation_price", "status", "reason"},
            ),
            "latest_prices.csv",
        )
        require_same_tickers(universe, snapshots, "latest_snapshot.csv")
        require_same_tickers(universe, prices, "latest_prices.csv")

        rows = [
            build_row(row, snapshots[row["ticker"].strip()], prices[row["ticker"].strip()])
            for row in universe
        ]
        write_atomically(args.output.resolve(), rows)
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print("The existing screen file was not changed.", file=sys.stderr)
        return 1

    statuses: dict[str, int] = {}
    for row in rows:
        status = str(row["calculation_status"])
        statuses[status] = statuses.get(status, 0) + 1
    print(f"Wrote {len(rows)} rows to {args.output.resolve()}")
    for status in ("ok", "partial", "excluded"):
        if statuses.get(status):
            print(f"  {status}: {statuses[status]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
