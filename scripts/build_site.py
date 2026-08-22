#!/usr/bin/env python3

"""Build the static Value Stock Weekly site from latest_research.csv.

The generated ``generated/site`` directory can be published with GitHub Pages.
HTML lives in editable templates, while this script validates and formats the
data. Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


TIME_ZONE = ZoneInfo("Europe/Ljubljana")
EXPECTED_COMPANIES = 10
REQUIRED_FIELDS = {
    "rank",
    "ticker",
    "name",
    "sector",
    "subsector",
    "valuation_price",
    "price_date",
    "attractiveness_score",
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
    "snapshot_source_url",
    "price_source_url",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Build the static weekly shortlist site.")
    parser.add_argument(
        "--input",
        type=Path,
        default=project_dir / "generated" / "data" / "latest_research.csv",
        help="Research CSV (default: ./generated/data/latest_research.csv).",
    )
    parser.add_argument(
        "--templates",
        type=Path,
        default=project_dir / "templates",
        help="Template directory (default: ./templates).",
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=project_dir / "assets",
        help="Source asset directory (default: ./assets).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_dir / "generated" / "site",
        help="GitHub Pages output directory (default: ./generated/site).",
    )
    parser.add_argument(
        "--published-at",
        help="Optional ISO-8601 timestamp for reproducible builds; defaults to now.",
    )
    return parser.parse_args(argv)


def load_template(path: Path) -> Template:
    try:
        return Template(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"Cannot read template: {path}") from error


def number(row: dict[str, str], field: str) -> float:
    try:
        value = float((row.get(field) or "").strip())
    except ValueError as error:
        raise RuntimeError(f"{row.get('ticker', 'row')} has invalid {field}.") from error
    if not math.isfinite(value):
        raise RuntimeError(f"{row.get('ticker', 'row')} has invalid {field}.")
    return value


def safe_url(row: dict[str, str], field: str) -> str:
    value = (row.get(field) or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{row.get('ticker', 'row')} has invalid {field}.")
    return html.escape(value, quote=True)


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"Input file does not exist: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        missing = REQUIRED_FIELDS - set(reader.fieldnames or [])
    if missing:
        raise RuntimeError(f"Input is missing required columns: {', '.join(sorted(missing))}")
    if len(rows) != EXPECTED_COMPANIES:
        raise RuntimeError(
            f"Expected {EXPECTED_COMPANIES} research rows, found {len(rows)}."
        )

    ranks: list[int] = []
    tickers: set[str] = set()
    for row in rows:
        try:
            rank = int((row.get("rank") or "").strip())
        except ValueError as error:
            raise RuntimeError("Every research row must have an integer rank.") from error
        ticker = (row.get("ticker") or "").strip()
        if not ticker or ticker in tickers:
            raise RuntimeError(f"Ticker is empty or duplicated: {ticker or '(empty)'}")
        ranks.append(rank)
        tickers.add(ticker)
        number(row, "valuation_price")
        attractiveness = number(row, "attractiveness_score")
        quality = number(row, "quality_display_score")
        if not 0 <= attractiveness <= 100 or not 0 <= quality <= 10:
            raise RuntimeError(f"{ticker} has a score outside its expected range.")
        safe_url(row, "snapshot_source_url")
        safe_url(row, "price_source_url")
    if sorted(ranks) != list(range(1, EXPECTED_COMPANIES + 1)):
        raise RuntimeError("Research ranks must be unique and consecutive from 1 to 10.")
    return sorted(rows, key=lambda row: int(row["rank"]))


def publication_time(value: str | None) -> datetime:
    if value:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            moment = datetime.fromisoformat(normalized)
        except ValueError as error:
            raise RuntimeError("--published-at must be a valid ISO-8601 timestamp.") from error
        if moment.tzinfo is None:
            raise RuntimeError("--published-at must include a timezone offset.")
        moment = moment.astimezone(TIME_ZONE)
    else:
        moment = datetime.now(TIME_ZONE)
    return moment.replace(minute=0, second=0, microsecond=0)


def attractiveness_label(score: float) -> str:
    display = score / 10
    if display >= 9.0:
        return "Excellent"
    if display >= 8.5:
        return "Strong"
    if display >= 8.0:
        return "Attractive"
    if display >= 7.0:
        return "Good"
    return "Fair"


def score_tone(label: str) -> str:
    if label in {"Excellent", "Strong"}:
        return "strong"
    if label in {"Attractive", "Good"}:
        return "good"
    if label == "Fair":
        return "fair"
    return "weak"


def escaped(value: str | None) -> str:
    return html.escape((value or "").strip())


def ticker_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise RuntimeError(f"Cannot create an HTML id from ticker: {value}")
    return slug


def template_values(row: dict[str, str]) -> dict[str, str]:
    attractiveness = number(row, "attractiveness_score")
    attr_label = attractiveness_label(attractiveness)
    quality_label = (row.get("quality_label") or "").strip()
    return {
        "rank": escaped(row["rank"]),
        "ticker": escaped(row["ticker"]),
        "ticker_slug": ticker_slug(row["ticker"]),
        "name": escaped(row["name"]),
        "sector": escaped(row["sector"]),
        "subsector": escaped(row["subsector"] or row["sector"]),
        "price": f"${number(row, 'valuation_price'):,.2f}",
        "attractiveness_display": f"{attractiveness / 10:.1f}",
        "attractiveness_label": attr_label,
        "attractiveness_tone": score_tone(attr_label),
        "quality_display": f"{number(row, 'quality_display_score'):.1f}",
        "quality_label": escaped(quality_label),
        "quality_tone": score_tone(quality_label),
        "why_selected": escaped(row["why_selected"]),
        "valuation_summary": escaped(row["valuation_summary"]),
        "business_quality_summary": escaped(row["business_quality_summary"]),
        "growth_summary": escaped(row["growth_summary"]),
        "balance_sheet_summary": escaped(row["balance_sheet_summary"]),
        "warning_summary": escaped(row["warning_summary"]),
        "what_to_verify": escaped(row["what_to_verify"]),
        "review_class": (
            "priority-review" if row["review_status"].strip() == "priority_review" else ""
        ),
        "snapshot_source_url": safe_url(row, "snapshot_source_url"),
        "price_source_url": safe_url(row, "price_source_url"),
    }


def render_site(
    rows: list[dict[str, str]], templates_dir: Path, published: datetime
) -> str:
    page = load_template(templates_dir / "index.html")
    desktop_row = load_template(templates_dir / "partials" / "desktop_row.html")
    mobile_card = load_template(templates_dir / "partials" / "mobile_card.html")
    analysis = load_template(templates_dir / "partials" / "analysis.html")

    desktop_parts: list[str] = []
    mobile_parts: list[str] = []
    for row in rows:
        values = template_values(row)
        analysis_html = analysis.substitute(values)
        values["analysis_content"] = analysis_html
        desktop_parts.append(desktop_row.substitute(values).rstrip())
        mobile_parts.append(mobile_card.substitute(values).rstrip())

    published_display = (
        f"{published.strftime('%A, %B')} {published.day}, {published.year} · "
        f"{published.strftime('%H:00 %Z')}"
    )
    return page.substitute(
        company_count=str(len(rows)),
        desktop_rows="\n".join(desktop_parts),
        mobile_cards="\n".join(mobile_parts),
        published_iso=published.isoformat(),
        published_display=published_display,
        publication_year=str(published.year),
    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def build(args: argparse.Namespace) -> None:
    rows = load_rows(args.input)
    published = publication_time(args.published_at)
    rendered = render_site(rows, args.templates, published)

    style_source = args.assets / "styles.css"
    script_source = args.assets / "site.js"
    try:
        styles = style_source.read_text(encoding="utf-8")
        script = script_source.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Cannot read site assets from {args.assets}") from error

    atomic_write(args.output / "index.html", rendered)
    atomic_write(args.output / "assets" / "styles.css", styles)
    atomic_write(args.output / "assets" / "site.js", script)
    print(
        f"Wrote {args.output / 'index.html'} with {len(rows)} companies "
        f"(published {published.isoformat()})."
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        build(parse_args(argv))
    except (OSError, RuntimeError, KeyError) as error:
        print(f"error: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
