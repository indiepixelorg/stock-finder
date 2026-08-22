#!/usr/bin/env python3

"""Rank an auditable weekly research shortlist from latest_screen.csv.

The script deliberately calls the result a research shortlist, not a list of
stocks proven to be undervalued. It applies explicit eligibility rules, scores
seven valuation and quality signals, and limits sector concentration.

Usage:

    python3 scripts/rank_companies.py
    python3 scripts/rank_companies.py --limit 10 --output /tmp/top10.csv

Only the standard Python library is used.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Sequence


EXCLUDED_SECTORS = {"Financials", "Real Estate"}
MIN_POSITIVE_FCF_YEARS = 4
MIN_SECTOR_SAMPLE = 5
MAX_PER_SECTOR = 2

QUALITY_WEIGHTS = {
    "operating_margin_score": 0.40,
    "fcf_consistency_score": 0.35,
    "revenue_growth_score": 0.25,
}

METRICS = {
    "free_cash_flow_yield": (0.25, True, True),
    "earnings_yield": (0.20, True, True),
    "ev_to_operating_income": (0.15, False, True),
    "operating_margin": (0.10, True, True),
    "five_year_revenue_growth": (0.10, True, False),
    "fcf_consistency": (0.10, True, False),
    "net_debt_to_fcf": (0.10, False, False),
}

REQUIRED_FIELDS = {
    "ticker",
    "cik",
    "name",
    "sector",
    "subsector",
    "valuation_price",
    "price_date",
    "free_cash_flow_yield",
    "earnings_yield",
    "ev_to_operating_income",
    "operating_margin",
    "five_year_revenue_growth",
    "positive_fcf_years",
    "historical_fcf_years",
    "net_debt_to_fcf",
    "five_year_median_fcf",
    "five_year_median_net_income",
    "calculation_status",
    "snapshot_source_url",
    "price_source_url",
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
    "attractiveness_score",
    "quality_score",
    "quality_display_score",
    "quality_label",
    "free_cash_flow_yield",
    "free_cash_flow_yield_score",
    "earnings_yield",
    "earnings_yield_score",
    "ev_to_operating_income",
    "ev_to_operating_income_score",
    "operating_margin",
    "operating_margin_score",
    "five_year_revenue_growth",
    "revenue_growth_score",
    "positive_fcf_years",
    "historical_fcf_years",
    "fcf_consistency_score",
    "net_debt_to_fcf",
    "net_debt_score",
    "selection_reasons",
    "review_flags",
    "snapshot_source_url",
    "price_source_url",
]

SCORE_FIELDS = {
    "free_cash_flow_yield": "free_cash_flow_yield_score",
    "earnings_yield": "earnings_yield_score",
    "ev_to_operating_income": "ev_to_operating_income_score",
    "operating_margin": "operating_margin_score",
    "five_year_revenue_growth": "revenue_growth_score",
    "fcf_consistency": "fcf_consistency_score",
    "net_debt_to_fcf": "net_debt_score",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Build a transparent weekly research shortlist."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=project_dir / "data" / "latest_screen.csv",
        help="Valuation screen CSV (default: ./data/latest_screen.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_dir / "data" / "latest_top10.csv",
        help="Output CSV (default: ./data/latest_top10.csv).",
    )
    parser.add_argument("--limit", type=int, default=10, help="Number of companies.")
    return parser.parse_args(argv)


def as_number(value: str | None) -> float | None:
    try:
        number = float((value or "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def load_screen(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"Input file does not exist: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = set(reader.fieldnames or [])
    missing = REQUIRED_FIELDS - fields
    if not rows or missing:
        raise RuntimeError(
            f"{path} is empty or missing required columns: {', '.join(sorted(missing))}"
        )
    return rows


def is_eligible(row: dict[str, str]) -> bool:
    if row.get("calculation_status") != "ok":
        return False
    if row.get("sector") in EXCLUDED_SECTORS:
        return False
    required_positive = (
        "five_year_median_fcf",
        "five_year_median_net_income",
    )
    if any((as_number(row.get(field)) or 0) <= 0 for field in required_positive):
        return False
    positive_years = as_number(row.get("positive_fcf_years"))
    historical_years = as_number(row.get("historical_fcf_years"))
    if (
        positive_years is None
        or historical_years is None
        or positive_years < MIN_POSITIVE_FCF_YEARS
        or historical_years <= 0
    ):
        return False
    score_inputs = [field for field in METRICS if field != "fcf_consistency"]
    return all(as_number(row.get(field)) is not None for field in score_inputs)


def percentile_scores(
    rows: list[dict[str, object]], metric: str, higher_is_better: bool
) -> dict[int, float]:
    """Return 0-100 percentile scores, assigning tied values an average rank."""
    ordered = sorted(
        ((index, float(row[metric])) for index, row in enumerate(rows)),
        key=lambda item: item[1],
    )
    if len(ordered) == 1:
        return {ordered[0][0]: 50.0}

    scores: dict[int, float] = {}
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][1] == ordered[position][1]:
            end += 1
        average_rank = (position + end - 1) / 2
        percentile = average_rank / (len(ordered) - 1) * 100
        if not higher_is_better:
            percentile = 100 - percentile
        for index, _ in ordered[position:end]:
            scores[index] = percentile
        position = end
    return scores


def add_metric_scores(rows: list[dict[str, object]]) -> None:
    sector_groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        sector_groups[str(row["sector"])].append(index)

    for metric, (_, higher_is_better, sector_relative) in METRICS.items():
        score_field = SCORE_FIELDS[metric]
        if metric == "fcf_consistency":
            for row in rows:
                row[score_field] = min(
                    100.0,
                    float(row["positive_fcf_years"])
                    / float(row["historical_fcf_years"])
                    * 100,
                )
            continue

        global_scores = percentile_scores(rows, metric, higher_is_better)
        for index, row in enumerate(rows):
            row[score_field] = global_scores[index]

        if sector_relative:
            for indexes in sector_groups.values():
                if len(indexes) < MIN_SECTOR_SAMPLE:
                    continue
                subset = [rows[index] for index in indexes]
                local_scores = percentile_scores(subset, metric, higher_is_better)
                for local_index, original_index in enumerate(indexes):
                    rows[original_index][score_field] = local_scores[local_index]


def format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def selection_reasons(row: dict[str, object]) -> str:
    labels = {
        "free_cash_flow_yield": f"FCF yield {format_percent(float(row['free_cash_flow_yield']))}",
        "earnings_yield": f"earnings yield {format_percent(float(row['earnings_yield']))}",
        "ev_to_operating_income": f"EV/operating income {float(row['ev_to_operating_income']):.1f}x",
        "operating_margin": f"operating margin {format_percent(float(row['operating_margin']))}",
        "five_year_revenue_growth": f"5-year revenue growth {format_percent(float(row['five_year_revenue_growth']))}",
        "fcf_consistency": f"positive FCF in {int(float(row['positive_fcf_years']))}/{int(float(row['historical_fcf_years']))} years",
        "net_debt_to_fcf": f"net debt/FCF {float(row['net_debt_to_fcf']):.1f}x",
    }
    contributions = sorted(
        (
            (float(row[SCORE_FIELDS[metric]]) * weight, metric)
            for metric, (weight, _, _) in METRICS.items()
        ),
        reverse=True,
    )
    return "; ".join(labels[metric] for _, metric in contributions[:3])


def review_flags(row: dict[str, object]) -> str:
    flags: list[str] = []
    if float(row["free_cash_flow_yield"]) > 0.25:
        flags.append("extreme_fcf_yield_verify_inputs")
    if float(row["earnings_yield"]) > 0.25:
        flags.append("extreme_earnings_yield_verify_inputs")
    return ";".join(flags)


def calculate_quality_score(row: dict[str, object]) -> float:
    return sum(float(row[field]) * weight for field, weight in QUALITY_WEIGHTS.items())


def quality_label(display_score: float) -> str:
    if display_score >= 8.5:
        return "Strong"
    if display_score >= 7.0:
        return "Good"
    if display_score >= 5.5:
        return "Fair"
    return "Weak"


def rank_companies(rows: list[dict[str, str]], limit: int = 10) -> list[dict[str, str]]:
    if limit <= 0:
        raise RuntimeError("--limit must be greater than zero.")

    eligible: list[dict[str, object]] = []
    for source in rows:
        if not is_eligible(source):
            continue
        row: dict[str, object] = dict(source)
        for metric in METRICS:
            if metric == "fcf_consistency":
                continue
            row[metric] = float(source[metric])
        row["positive_fcf_years"] = float(source["positive_fcf_years"])
        row["historical_fcf_years"] = float(source["historical_fcf_years"])
        eligible.append(row)

    if not eligible:
        raise RuntimeError("No companies satisfy the ranking eligibility rules.")

    add_metric_scores(eligible)
    for row in eligible:
        row["attractiveness_score"] = sum(
            float(row[SCORE_FIELDS[metric]]) * weight
            for metric, (weight, _, _) in METRICS.items()
        )
        row["quality_score"] = calculate_quality_score(row)
        row["quality_display_score"] = round(float(row["quality_score"]) / 10, 1)
        row["quality_label"] = quality_label(float(row["quality_display_score"]))

    ordered = sorted(
        eligible,
        key=lambda row: (-float(row["attractiveness_score"]), str(row["ticker"])),
    )
    deduplicated: list[dict[str, object]] = []
    seen_ciks: set[str] = set()
    for row in ordered:
        cik = str(row["cik"])
        if cik in seen_ciks:
            continue
        seen_ciks.add(cik)
        deduplicated.append(row)

    selected: list[dict[str, object]] = []
    sectors: Counter[str] = Counter()
    for row in deduplicated:
        sector = str(row["sector"])
        if sectors[sector] >= MAX_PER_SECTOR:
            continue
        selected.append(row)
        sectors[sector] += 1
        if len(selected) == limit:
            break

    if len(selected) < limit:
        raise RuntimeError(
            f"Only {len(selected)} companies can be selected with the sector cap; "
            f"requested {limit}."
        )

    output: list[dict[str, str]] = []
    for rank, row in enumerate(selected, start=1):
        rendered = {field: str(row.get(field, "")) for field in OUTPUT_FIELDS}
        rendered["rank"] = str(rank)
        rendered["name"] = rendered["name"].rstrip("|").strip()
        rendered["attractiveness_score"] = f"{float(row['attractiveness_score']):.2f}"
        rendered["quality_score"] = f"{float(row['quality_score']):.2f}"
        rendered["quality_display_score"] = f"{float(row['quality_display_score']):.1f}"
        for score_field in SCORE_FIELDS.values():
            rendered[score_field] = f"{float(row[score_field]):.2f}"
        rendered["positive_fcf_years"] = str(int(float(row["positive_fcf_years"])))
        rendered["historical_fcf_years"] = str(int(float(row["historical_fcf_years"])))
        rendered["selection_reasons"] = selection_reasons(row)
        rendered["review_flags"] = review_flags(row)
        output.append(rendered)
    return output


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
        source = load_screen(args.input)
        ranked = rank_companies(source, args.limit)
        write_atomic(args.output, ranked)
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {len(ranked)} ranked companies to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
