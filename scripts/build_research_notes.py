#!/usr/bin/env python3

"""Generate deterministic, human-readable notes for the weekly shortlist.

The output translates existing screen metrics into traceable prose. It does
not invent company developments or qualitative risks that are absent from the
source data, and every company remains subject to a filing review.

Usage:

    python3 scripts/build_research_notes.py
    python3 scripts/build_research_notes.py --output /tmp/latest_research.csv

Only the standard Python library is used.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Sequence


TOP_REQUIRED_FIELDS = {
    "rank",
    "ticker",
    "cik",
    "name",
    "sector",
    "subsector",
    "valuation_price",
    "price_date",
    "attractiveness_score",
    "quality_score",
    "quality_display_score",
    "quality_label",
    "free_cash_flow_yield",
    "free_cash_flow_yield_score",
    "earnings_yield",
    "earnings_yield_score",
    "ev_to_operating_income",
    "operating_margin",
    "operating_margin_score",
    "five_year_revenue_growth",
    "positive_fcf_years",
    "historical_fcf_years",
    "net_debt_to_fcf",
    "selection_reasons",
    "review_flags",
    "snapshot_source_url",
    "price_source_url",
}

SCREEN_REQUIRED_FIELDS = {
    "ticker",
    "cik",
    "filing_date",
    "period_end",
    "net_income_ttm",
    "free_cash_flow_ttm",
    "total_debt",
    "stockholders_equity",
    "five_year_median_fcf",
    "five_year_median_net_income",
}

OUTPUT_FIELDS = [
    "rank",
    "ticker",
    "cik",
    "name",
    "sector",
    "subsector",
    "valuation_price",
    "price_date",
    "filing_date",
    "period_end",
    "attractiveness_score",
    "quality_score",
    "quality_display_score",
    "quality_label",
    "why_selected",
    "valuation_summary",
    "business_quality_summary",
    "growth_summary",
    "balance_sheet_summary",
    "warning_summary",
    "what_to_verify",
    "review_status",
    "generation_method",
    "snapshot_source_url",
    "price_source_url",
]

SECTOR_CHECKS = {
    "Communication Services": (
        "capital spending, debt obligations, subscriber or audience trends, "
        "and competitive pressure"
    ),
    "Consumer Discretionary": (
        "demand cyclicality, pricing power, financing exposure, and inventory trends"
    ),
    "Consumer Staples": "volume trends, pricing power, and input-cost pressure",
    "Energy": (
        "cash-flow durability across the commodity cycle and the assumptions behind "
        "current capital spending"
    ),
    "Health Care": (
        "product concentration, reimbursement, regulatory exposure, and the pipeline"
    ),
    "Industrials": (
        "backlog quality, customer concentration, cyclicality, and margin durability"
    ),
    "Information Technology": (
        "competitive pressure, product transitions, customer retention, and the "
        "durability of margins"
    ),
    "Materials": (
        "cash-flow durability across the commodity cycle, input costs, and capacity"
    ),
    "Utilities": "regulatory outcomes, capital spending, and refinancing needs",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Generate traceable prose for the latest ranked shortlist."
    )
    parser.add_argument(
        "--top10",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_top10.csv",
        help="Ranked shortlist CSV (default: ./generated/data/latest_top10.csv).",
    )
    parser.add_argument(
        "--screen",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_screen.csv",
        help="Valuation screen CSV (default: ./generated/data/latest_screen.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_research.csv",
        help="Output CSV (default: ./generated/data/latest_research.csv).",
    )
    return parser.parse_args(argv)


def load_csv(
    path: Path, required_fields: set[str], label: str
) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"Input file does not exist: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = set(reader.fieldnames or [])
    missing = required_fields - fields
    if not rows or missing:
        raise RuntimeError(
            f"{label} is empty or missing required columns: {', '.join(sorted(missing))}"
        )
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


def number(row: dict[str, str], field: str) -> float:
    try:
        value = float((row.get(field) or "").strip())
    except ValueError as error:
        raise RuntimeError(f"{row.get('ticker', 'row')} has invalid {field}.") from error
    if not math.isfinite(value):
        raise RuntimeError(f"{row.get('ticker', 'row')} has invalid {field}.")
    return value


def percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def multiple(value: float) -> str:
    return f"{value:.1f}x"


def comparison_description(score: float) -> str:
    if score >= 80:
        return "in the top fifth of its comparison group"
    if score >= 60:
        return "above the comparison-group median"
    if score >= 40:
        return "near the comparison-group median"
    return "below the comparison-group median"


def warning_codes(top: dict[str, str], screen: dict[str, str]) -> list[str]:
    warnings = [item for item in top["review_flags"].split(";") if item]
    median_net_income = number(screen, "five_year_median_net_income")
    median_fcf = number(screen, "five_year_median_fcf")
    net_income_ratio = number(screen, "net_income_ttm") / median_net_income
    fcf_ratio = number(screen, "free_cash_flow_ttm") / median_fcf
    if net_income_ratio > 1.75:
        warnings.append("ttm_net_income_well_above_five_year_median")
    if fcf_ratio > 1.75:
        warnings.append("ttm_fcf_well_above_five_year_median")
    if number(top, "net_debt_to_fcf") > 4:
        warnings.append("elevated_net_debt_to_fcf")
    if number(screen, "total_debt") < number(screen, "stockholders_equity") * 0.10:
        warnings.append("unusually_low_reported_debt_verify_xbrl_coverage")
    if number(top, "five_year_revenue_growth") < 0:
        warnings.append("negative_five_year_revenue_growth")
    return list(dict.fromkeys(warnings))


def warning_text(
    codes: list[str], top: dict[str, str], screen: dict[str, str]
) -> str:
    messages: list[str] = []
    if any(code.startswith("extreme_") for code in codes):
        messages.append(
            "The reported yields are unusually high, so the share count, implied "
            "market capitalization, and possible one-off items require verification."
        )
    if "ttm_net_income_well_above_five_year_median" in codes:
        ratio = number(screen, "net_income_ttm") / number(
            screen, "five_year_median_net_income"
        )
        messages.append(
            f"TTM net income is {ratio:.1f}x the five-year median; confirm how much "
            "is recurring."
        )
    if "ttm_fcf_well_above_five_year_median" in codes:
        ratio = number(screen, "free_cash_flow_ttm") / number(
            screen, "five_year_median_fcf"
        )
        messages.append(
            f"TTM free cash flow is {ratio:.1f}x the five-year median; confirm whether "
            "working-capital timing or another temporary factor contributed."
        )
    if "elevated_net_debt_to_fcf" in codes:
        messages.append(
            f"Net debt is {multiple(number(top, 'net_debt_to_fcf'))} trailing free "
            "cash flow, which warrants a closer debt review."
        )
    if "unusually_low_reported_debt_verify_xbrl_coverage" in codes:
        messages.append(
            "Reported debt is less than 10% of stockholders' equity; verify that the "
            "selected SEC XBRL facts capture all material borrowings and leases."
        )
    if "negative_five_year_revenue_growth" in codes:
        messages.append("Revenue contracted over the measured five-year period.")
    if not messages:
        messages.append(
            "No numerical anomaly crossed the current rule-based thresholds. This "
            "does not cover qualitative business risks."
        )
    return " ".join(messages)


def build_note(top: dict[str, str], screen: dict[str, str]) -> dict[str, str]:
    if top["cik"].strip() != screen["cik"].strip():
        raise RuntimeError(f"CIK mismatch for {top['ticker']}.")

    fcf_yield = number(top, "free_cash_flow_yield")
    earnings_yield = number(top, "earnings_yield")
    ev_to_operating_income = number(top, "ev_to_operating_income")
    operating_margin = number(top, "operating_margin")
    revenue_growth = number(top, "five_year_revenue_growth")
    net_debt_to_fcf = number(top, "net_debt_to_fcf")
    positive_fcf_years = int(number(top, "positive_fcf_years"))
    historical_fcf_years = int(number(top, "historical_fcf_years"))
    warnings = warning_codes(top, screen)

    selected_reasons = top["selection_reasons"].rstrip(" .")
    why_selected = (
        f"Ranked #{int(number(top, 'rank'))} with an attractiveness score of "
        f"{number(top, 'attractiveness_score'):.2f}/100. Its largest quantitative "
        f"contributors were {selected_reasons}."
    )

    fcf_comparison = comparison_description(number(top, "free_cash_flow_yield_score"))
    earnings_comparison = comparison_description(number(top, "earnings_yield_score"))
    valuation_summary = (
        f"The screen reports a {percent(fcf_yield)} free-cash-flow yield, which is "
        f"{fcf_comparison}, and a {percent(earnings_yield)} earnings yield, which is "
        f"{earnings_comparison}. Enterprise value is "
        f"{multiple(ev_to_operating_income)} operating income."
    )

    margin_comparison = comparison_description(number(top, "operating_margin_score"))
    business_quality_summary = (
        f"The quantitative quality score is "
        f"{number(top, 'quality_display_score'):.1f}/10 ({top['quality_label']}). "
        f"Operating margin is {percent(operating_margin)}, {margin_comparison}. "
        f"Free cash flow was positive in {positive_fcf_years} of "
        f"{historical_fcf_years} reported fiscal years."
    )

    if revenue_growth >= 0.08:
        growth_label = "solid"
    elif revenue_growth >= 0.02:
        growth_label = "moderate"
    elif revenue_growth >= 0:
        growth_label = "modest"
    else:
        growth_label = "negative"
    growth_summary = (
        f"Revenue changed at approximately {percent(revenue_growth)} annually across "
        f"the measured five-year period, representing {growth_label} historical growth."
    )

    if net_debt_to_fcf < 0:
        balance_sheet_summary = (
            f"The SEC-derived inputs imply more cash than debt, with net cash equal "
            f"to {multiple(abs(net_debt_to_fcf))} trailing free cash flow."
        )
    elif net_debt_to_fcf <= 2:
        balance_sheet_summary = (
            f"Net debt is {multiple(net_debt_to_fcf)} trailing free cash flow, a "
            "moderate level under this screen."
        )
    elif net_debt_to_fcf <= 4:
        balance_sheet_summary = (
            f"Net debt is {multiple(net_debt_to_fcf)} trailing free cash flow, making "
            "debt service an important review item."
        )
    else:
        balance_sheet_summary = (
            f"Net debt is {multiple(net_debt_to_fcf)} trailing free cash flow, an "
            "elevated level requiring detailed review."
        )

    verification_items: list[str] = []
    if any(code.startswith("extreme_") for code in warnings):
        verification_items.append(
            "shares outstanding and the removal of non-recurring earnings or "
            "cash-flow items"
        )
    if any("well_above_five_year_median" in code for code in warnings):
        verification_items.append("the reason for the gap between TTM results and normalized history")
    if "elevated_net_debt_to_fcf" in warnings:
        verification_items.append("debt maturities, interest expense, and refinancing needs")
    if "unusually_low_reported_debt_verify_xbrl_coverage" in warnings:
        verification_items.append(
            "reported debt and lease obligations against the balance sheet notes"
        )
    if "negative_five_year_revenue_growth" in warnings:
        verification_items.append("the causes of the historical revenue decline")
    verification_items.append(
        SECTOR_CHECKS.get(
            top["sector"],
            "competitive position, customer concentration, and margin durability",
        )
    )
    what_to_verify = (
        "Before publication, read the latest 10-K or 10-Q and verify "
        + "; verify ".join(verification_items)
        + "."
    )

    return {
        "rank": str(int(number(top, "rank"))),
        "ticker": top["ticker"].strip(),
        "cik": top["cik"].strip(),
        "name": top["name"].strip(),
        "sector": top["sector"].strip(),
        "subsector": top["subsector"].strip(),
        "valuation_price": top["valuation_price"].strip(),
        "price_date": top["price_date"].strip(),
        "filing_date": screen["filing_date"].strip(),
        "period_end": screen["period_end"].strip(),
        "attractiveness_score": f"{number(top, 'attractiveness_score'):.2f}",
        "quality_score": f"{number(top, 'quality_score'):.2f}",
        "quality_display_score": f"{number(top, 'quality_display_score'):.1f}",
        "quality_label": top["quality_label"].strip(),
        "why_selected": why_selected,
        "valuation_summary": valuation_summary,
        "business_quality_summary": business_quality_summary,
        "growth_summary": growth_summary,
        "balance_sheet_summary": balance_sheet_summary,
        "warning_summary": warning_text(warnings, top, screen),
        "what_to_verify": what_to_verify,
        "review_status": "priority_review" if warnings else "standard_review",
        "generation_method": "deterministic_rules_v1",
        "snapshot_source_url": top["snapshot_source_url"].strip(),
        "price_source_url": top["price_source_url"].strip(),
    }


def build_research_notes(
    top_rows: list[dict[str, str]], screen_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    top_index = index_rows(top_rows, "latest_top10.csv")
    screen_index = index_rows(screen_rows, "latest_screen.csv")
    missing = sorted(set(top_index) - set(screen_index))
    if missing:
        raise RuntimeError(
            f"latest_screen.csv is missing shortlisted tickers: {', '.join(missing)}"
        )
    ordered = sorted(top_rows, key=lambda row: number(row, "rank"))
    expected_ranks = list(range(1, len(ordered) + 1))
    actual_ranks = [int(number(row, "rank")) for row in ordered]
    if actual_ranks != expected_ranks:
        raise RuntimeError("latest_top10.csv ranks must be unique and consecutive from 1.")
    return [build_note(row, screen_index[row["ticker"]]) for row in ordered]


def write_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        top_rows = load_csv(args.top10, TOP_REQUIRED_FIELDS, "latest_top10.csv")
        screen_rows = load_csv(args.screen, SCREEN_REQUIRED_FIELDS, "latest_screen.csv")
        notes = build_research_notes(top_rows, screen_rows)
        write_atomic(args.output, notes)
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print("The existing research file was not changed.", file=sys.stderr)
        return 1
    print(f"Wrote {len(notes)} research notes to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
