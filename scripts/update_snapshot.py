#!/usr/bin/env python3

"""Build generated/data/latest_snapshot.csv from SEC Company Facts.

The script keeps one output row per security in universe.csv. Financial values
come from the SEC's standard XBRL Company Facts API; TTM flow values are
calculated locally from annual and year-to-date filing facts. Price and market
capitalization are intentionally not included because they are not provided by
Company Facts. The annual history columns use slots 0 through 4, where slot 0
is the most recent annual filing and slot 4 is the fifth most recent.

Usage:

    python3 scripts/update_snapshot.py
    python3 scripts/update_snapshot.py --limit 5

Only the standard Python library is used.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts"
DEFAULT_USER_AGENT = "Value Stock Weekly/0.1 (contact: indiepixelorg@outlook.com)"
DEFAULT_WORKERS = 4
MAX_WORKERS = 8
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 3
HISTORY_SLOTS = 5

ANNUAL_HISTORY_FIELDS = (
    [f"annual_period_end_{slot}" for slot in range(HISTORY_SLOTS)]
    + [f"annual_filing_date_{slot}" for slot in range(HISTORY_SLOTS)]
    + [
        f"{metric}_fy{slot}"
        for metric in (
            "revenue",
            "operating_income",
            "net_income",
            "operating_cash_flow",
            "capital_expenditures",
            "free_cash_flow",
            "diluted_eps",
        )
        for slot in range(HISTORY_SLOTS)
    ]
)

OUTPUT_FIELDS = [
    "ticker",
    "cik",
    "name",
    "sector",
    "subsector",
    "revenue_ttm",
    "operating_income_ttm",
    "net_income_ttm",
    "operating_cash_flow_ttm",
    "capital_expenditures_ttm",
    "free_cash_flow_ttm",
    "diluted_eps_ttm",
    "cash_and_equivalents",
    "total_assets",
    "total_liabilities",
    "stockholders_equity",
    "total_debt",
    "shares_outstanding",
    "filing_date",
    "period_end",
    "data_source",
    "source_url",
] + ANNUAL_HISTORY_FIELDS + ["error"]

FLOW_FACTS: dict[str, tuple[str, ...]] = {
    "revenue_ttm": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
    ),
    "operating_income_ttm": ("OperatingIncomeLoss",),
    "net_income_ttm": (
        "ProfitLoss",
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
    "operating_cash_flow_ttm": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditures_ttm": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    ),
    "diluted_eps_ttm": ("EarningsPerShareDiluted",),
}

INSTANT_FACTS: dict[str, tuple[str, ...]] = {
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "stockholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "shares_outstanding": ("EntityCommonStockSharesOutstanding",),
}

DEBT_TOTAL_FACTS: tuple[str, ...] = (
    "LongTermDebtAndFinanceLeaseObligations",
    "LongTermDebt",
)
DEBT_CURRENT_FACTS: tuple[str, ...] = (
    "DebtCurrent",
    "LongTermDebtCurrent",
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
)
DEBT_NONCURRENT_FACTS: tuple[str, ...] = (
    "LongTermDebtNoncurrent",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
)


class SECError(RuntimeError):
    """An SEC Company Facts request or response failed."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def parse_args() -> argparse.Namespace:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Fetch an SEC Company Facts snapshot for the S&P 500 universe."
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
        default=project_dir / "generated" / "data" / "latest_snapshot.csv",
        help="Output CSV (default: ./generated/data/latest_snapshot.csv).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process only the first N securities; useful for testing.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Concurrent SEC requests (default: {DEFAULT_WORKERS}).",
    )
    return parser.parse_args()


def load_universe(path: Path, limit: int | None) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"Universe file does not exist: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    required_fields = {"ticker", "cik", "name", "sector"}
    if not rows or not required_fields.issubset(rows[0]):
        raise RuntimeError(
            f"Universe must contain these columns: {', '.join(sorted(required_fields))}"
        )

    securities: list[dict[str, str]] = []
    seen_tickers: set[str] = set()
    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        cik = "".join(character for character in (row.get("cik") or "") if character.isdigit())
        if not ticker:
            continue
        if ticker in seen_tickers:
            raise RuntimeError(f"Duplicate ticker in universe.csv: {ticker}")
        if not cik:
            raise RuntimeError(f"Missing CIK in universe.csv for ticker: {ticker}")
        seen_tickers.add(ticker)
        securities.append(
            {
                "ticker": ticker,
                "cik": cik.zfill(10),
                "name": (row.get("name") or "").strip(),
                "sector": (row.get("sector") or "").strip(),
                "subsector": (row.get("subsector") or "").strip(),
            }
        )

    if limit is not None:
        if limit < 1:
            raise RuntimeError("--limit must be greater than zero.")
        securities = securities[:limit]

    if not securities:
        raise RuntimeError("No securities found in the universe.")
    return securities


class SECClient:
    def __init__(
        self,
        user_agent: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.user_agent = user_agent
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        url = f"{self.base_url}/CIK{cik.zfill(10)}.json"
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )

        last_error: Exception | None = None
        for attempt in range(DEFAULT_RETRIES):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("facts"), dict
                ):
                    raise SECError("SEC returned an unexpected Company Facts payload.")
                return payload
            except HTTPError as error:
                if error.code in (400, 403, 404):
                    raise SECError(
                        f"SEC returned HTTP {error.code} for CIK {cik}.", error.code
                    ) from error
                last_error = SECError(
                    f"SEC returned HTTP {error.code} ({error.reason}).", error.code
                )
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = SECError(f"SEC request failed: {error}")
            except SECError:
                raise

            if attempt < DEFAULT_RETRIES - 1:
                time.sleep(2**attempt)

        raise last_error or SECError("SEC request failed.")


def as_float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def entry_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("start") or ""),
        str(entry.get("end") or ""),
        str(entry.get("form") or ""),
    )


def fact_entries(
    company_facts: dict[str, Any],
    taxonomy: str,
    tags: Iterable[str],
    preferred_units: Iterable[str],
) -> list[dict[str, Any]]:
    taxonomy_facts = company_facts.get("facts", {}).get(taxonomy, {})
    if not isinstance(taxonomy_facts, dict):
        return []

    preferred_units = tuple(preferred_units)
    for tag in tags:
        definition = taxonomy_facts.get(tag)
        if not isinstance(definition, dict):
            continue
        units = definition.get("units", {})
        if not isinstance(units, dict):
            continue

        unit_names = [unit for unit in preferred_units if unit in units]
        unit_names.extend(unit for unit in units if unit not in unit_names)
        for unit in unit_names:
            values = units.get(unit)
            if not isinstance(values, list):
                continue
            usable = [
                {**entry, "_unit": unit, "_tag": tag}
                for entry in values
                if isinstance(entry, dict) and as_float(entry.get("val")) is not None
            ]
            if usable:
                return usable
    return []


def fact_entries_all_tags(
    company_facts: dict[str, Any],
    taxonomy: str,
    tags: Iterable[str],
    preferred_units: Iterable[str],
) -> list[dict[str, Any]]:
    """Collect facts from every candidate tag for historical fallback coverage."""
    taxonomy_facts = company_facts.get("facts", {}).get(taxonomy, {})
    if not isinstance(taxonomy_facts, dict):
        return []

    preferred_units = tuple(preferred_units)
    entries: list[dict[str, Any]] = []
    for tag in tags:
        definition = taxonomy_facts.get(tag)
        if not isinstance(definition, dict):
            continue
        units = definition.get("units", {})
        if not isinstance(units, dict):
            continue

        unit_names = [unit for unit in preferred_units if unit in units]
        unit_names.extend(unit for unit in units if unit not in unit_names)
        for unit in unit_names:
            values = units.get(unit)
            if not isinstance(values, list):
                continue
            usable = [
                {**entry, "_unit": unit, "_tag": tag}
                for entry in values
                if isinstance(entry, dict) and as_float(entry.get("val")) is not None
            ]
            if usable:
                entries.extend(usable)
                break
    return entries


def filing_sort_key(entry: dict[str, Any]) -> tuple[date, date, str]:
    filed = parse_date(entry.get("filed")) or date.min
    end = parse_date(entry.get("end")) or date.min
    return filed, end, str(entry.get("accn") or "")


def latest_instant(entries: list[dict[str, Any]]) -> tuple[float | None, dict[str, Any]]:
    if not entries:
        return None, {}
    selected = max(entries, key=filing_sort_key)
    return as_float(selected.get("val")), selected


def deduplicate_periods(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest filing for each reporting period."""
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in entries:
        key = entry_key(entry)
        previous = selected.get(key)
        if previous is None or filing_sort_key(entry) > filing_sort_key(previous):
            selected[key] = entry
    return list(selected.values())


def duration_days(entry: dict[str, Any]) -> int:
    start = parse_date(entry.get("start"))
    end = parse_date(entry.get("end"))
    if not start or not end:
        return 0
    return (end - start).days + 1


def is_annual(entry: dict[str, Any]) -> bool:
    return entry.get("form") in {"10-K", "20-F"} and duration_days(entry) >= 300


def is_quarterly_or_ytd(entry: dict[str, Any]) -> bool:
    return entry.get("form") == "10-Q" and 45 <= duration_days(entry) <= 320


def prior_annual(
    annuals: list[dict[str, Any]], current_end: date, filed_by: date
) -> dict[str, Any] | None:
    candidates = [
        entry
        for entry in annuals
        if (parse_date(entry.get("end")) or date.max) < current_end
        and (parse_date(entry.get("filed")) or date.max) <= filed_by
    ]
    return max(candidates, key=filing_sort_key) if candidates else None


def previous_ytd(
    ytd_entries: list[dict[str, Any]], current: dict[str, Any], filed_by: date
) -> dict[str, Any] | None:
    current_end = parse_date(current.get("end"))
    if not current_end:
        return None

    current_duration = duration_days(current)
    current_fp = str(current.get("fp") or "")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for entry in ytd_entries:
        end = parse_date(entry.get("end"))
        filed = parse_date(entry.get("filed"))
        if not end or not filed or filed > filed_by or end >= current_end:
            continue
        if abs(duration_days(entry) - current_duration) > 45:
            continue
        if current_fp and entry.get("fp") and entry.get("fp") != current_fp:
            continue
        distance = abs((current_end - end).days - 365)
        candidates.append((distance, entry))

    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], -filing_sort_key(item[1])[0].toordinal()))[1]


def latest_filing_period(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [
        entry
        for entry in entries
        if entry.get("form") in {"10-Q", "10-K", "20-F"}
        and parse_date(entry.get("filed"))
    ]
    return max(usable, key=filing_sort_key) if usable else None


def ttm_flow(entries: list[dict[str, Any]]) -> tuple[float | None, dict[str, Any]]:
    """Return a TTM flow using an annual plus current/prior YTD when needed."""
    entries = deduplicate_periods(
        [entry for entry in entries if entry.get("form") in {"10-Q", "10-K", "20-F"}]
    )
    if not entries:
        return None, {}

    latest = latest_filing_period(entries)
    if not latest:
        return None, {}

    latest_filed = parse_date(latest.get("filed")) or date.max
    latest_end = parse_date(latest.get("end"))
    annuals = [entry for entry in entries if is_annual(entry)]
    ytd_entries = [entry for entry in entries if is_quarterly_or_ytd(entry)]

    if latest_end and is_annual(latest):
        return as_float(latest.get("val")), latest

    if latest_end and latest.get("form") == "10-Q":
        # Use the longest duration reported in the latest 10-Q filing: Q1 is
        # normally one quarter, while Q2/Q3 also contain 6/9-month YTD facts.
        same_filing = [
            entry
            for entry in ytd_entries
            if entry.get("accn") == latest.get("accn")
            and parse_date(entry.get("filed")) == latest_filed
        ]
        current_ytd = max(same_filing or [latest], key=duration_days)
        current_value = as_float(current_ytd.get("val"))
        current_end = parse_date(current_ytd.get("end"))
        if current_value is not None and current_end:
            prior_ytd = previous_ytd(ytd_entries, current_ytd, latest_filed)
            annual = prior_annual(annuals, current_end, latest_filed)
            prior_ytd_value = as_float(prior_ytd.get("val")) if prior_ytd else None
            annual_value = as_float(annual.get("val")) if annual else None
            if annual_value is not None and prior_ytd_value is not None:
                return annual_value + current_value - prior_ytd_value, current_ytd

    # Fallback: sum four standalone quarterly values where they are available.
    quarter_entries = [
        entry
        for entry in ytd_entries
        if duration_days(entry) <= 130 and entry.get("qtrs") in (None, 1)
    ]
    by_end: dict[date, dict[str, Any]] = {}
    for entry in quarter_entries:
        end = parse_date(entry.get("end"))
        if end and (end not in by_end or filing_sort_key(entry) > filing_sort_key(by_end[end])):
            by_end[end] = entry
    recent = sorted(by_end.values(), key=filing_sort_key)[-4:]
    if len(recent) == 4:
        values = [as_float(entry.get("val")) for entry in recent]
        if all(value is not None for value in values):
            return sum(value for value in values if value is not None), recent[-1]

    return None, latest


def annual_history(
    entries: list[dict[str, Any]], slots: int = HISTORY_SLOTS
) -> list[dict[str, Any]]:
    """Return the most recent annual filing facts, newest first."""
    annual_entries = [
        entry
        for entry in entries
        if entry.get("form") in {"10-K", "20-F"}
        and duration_days(entry) >= 300
        and parse_date(entry.get("end"))
    ]
    by_period_end: dict[date, dict[str, Any]] = {}
    for entry in annual_entries:
        period_end = parse_date(entry.get("end"))
        if not period_end:
            continue
        previous = by_period_end.get(period_end)
        entry_rank = (duration_days(entry), filing_sort_key(entry))
        previous_rank = (
            (duration_days(previous), filing_sort_key(previous))
            if previous
            else None
        )
        if previous is None or entry_rank > previous_rank:
            by_period_end[period_end] = entry

    return sorted(
        by_period_end.values(),
        key=lambda entry: parse_date(entry.get("end")) or date.min,
        reverse=True,
    )[:slots]


def selected_fact(
    company_facts: dict[str, Any],
    taxonomy: str,
    tags: Iterable[str],
    units: Iterable[str],
) -> tuple[float | None, dict[str, Any]]:
    return latest_instant(fact_entries(company_facts, taxonomy, tags, units))


def selected_ttm_fact(
    company_facts: dict[str, Any],
    tags: Iterable[str],
    units: Iterable[str],
) -> tuple[float | None, dict[str, Any]]:
    entries = fact_entries(company_facts, "us-gaap", tags, units)
    return ttm_flow(entries)


def selected_annual_fact_history(
    company_facts: dict[str, Any],
    tags: Iterable[str],
    units: Iterable[str],
) -> list[dict[str, Any]]:
    entries = fact_entries_all_tags(company_facts, "us-gaap", tags, units)
    return annual_history(entries)


def build_financials(company_facts: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    annual_histories: dict[str, list[dict[str, Any]]] = {}
    annual_periods: set[date] = set()

    # Build one shared set of SEC period-end dates for all annual metrics.
    # Individual metrics are not guaranteed to have a fact for every year, so
    # their values must be looked up by period end rather than by list index.
    for field, tags in FLOW_FACTS.items():
        units = ("USD/shares",) if field == "diluted_eps_ttm" else ("USD",)
        annual_field = field[:-4]
        history = selected_annual_fact_history(company_facts, tags, units)
        annual_histories[annual_field] = history
        for annual_entry in history:
            period_end = parse_date(annual_entry.get("end"))
            if period_end:
                annual_periods.add(period_end)

    ordered_periods = sorted(annual_periods, reverse=True)[:HISTORY_SLOTS]
    annual_slots: list[dict[str, Any]] = [
        {
            "period_end": period_end.isoformat(),
            "filing_date": "",
        }
        for period_end in ordered_periods
    ]
    annual_slots.extend(
        {"period_end": "", "filing_date": ""}
        for _ in range(HISTORY_SLOTS - len(annual_slots))
    )

    for field, tags in FLOW_FACTS.items():
        units = ("USD/shares",) if field == "diluted_eps_ttm" else ("USD",)
        value, entry = selected_ttm_fact(company_facts, tags, units)
        values[field] = value if value is not None else ""
        if entry:
            evidence.append(entry)

        annual_field = field[:-4]
        entries_by_period_end = {
            period_end: annual_entry
            for annual_entry in annual_histories[annual_field]
            if (period_end := parse_date(annual_entry.get("end")))
        }
        for slot, period_end in enumerate(ordered_periods):
            annual_entry = entries_by_period_end.get(period_end)
            if not annual_entry:
                continue
            annual_value = as_float(annual_entry.get("val"))
            if annual_field == "capital_expenditures" and annual_value is not None:
                annual_value = abs(annual_value)
            annual_slots[slot][annual_field] = (
                annual_value if annual_value is not None else ""
            )
            filing_date = parse_date(annual_entry.get("filed"))
            if filing_date:
                current_filing_date = parse_date(annual_slots[slot]["filing_date"])
                if not current_filing_date or filing_date > current_filing_date:
                    annual_slots[slot]["filing_date"] = filing_date.isoformat()

    for field, tags in INSTANT_FACTS.items():
        taxonomy = "dei" if field == "shares_outstanding" else "us-gaap"
        units = ("shares",) if field == "shares_outstanding" else ("USD",)
        value, entry = selected_fact(company_facts, taxonomy, tags, units)
        values[field] = value if value is not None else ""
        if entry:
            evidence.append(entry)

    # Debt may be reported as a total or as current/non-current components.
    current_debt, current_entry = selected_fact(
        company_facts, "us-gaap", DEBT_CURRENT_FACTS, ("USD",)
    )
    noncurrent_debt, noncurrent_entry = selected_fact(
        company_facts, "us-gaap", DEBT_NONCURRENT_FACTS, ("USD",)
    )
    if current_debt is not None and noncurrent_debt is not None:
        total_debt = current_debt + noncurrent_debt
        evidence.extend(entry for entry in (current_entry, noncurrent_entry) if entry)
    else:
        total_debt, debt_entry = selected_fact(
            company_facts, "us-gaap", DEBT_TOTAL_FACTS, ("USD",)
        )
        if total_debt is None:
            components = [
                value for value in (current_debt, noncurrent_debt) if value is not None
            ]
            total_debt = sum(components) if components else None
            evidence.extend(entry for entry in (current_entry, noncurrent_entry) if entry)
        elif debt_entry:
            evidence.append(debt_entry)
    values["total_debt"] = total_debt if total_debt is not None else ""

    # Some issuers report the balance-sheet equation rather than a standalone
    # Liabilities fact. Derive liabilities from total liabilities and equity
    # only when the direct fact is unavailable.
    if not values.get("total_liabilities"):
        total_liabilities_and_equity, liability_entry = selected_fact(
            company_facts,
            "us-gaap",
            ("LiabilitiesAndStockholdersEquity",),
            ("USD",),
        )
        equity = as_float(values.get("stockholders_equity"))
        if total_liabilities_and_equity is not None and equity is not None:
            values["total_liabilities"] = total_liabilities_and_equity - equity
            if liability_entry:
                evidence.append(liability_entry)

    operating_cash_flow = as_float(values.get("operating_cash_flow_ttm"))
    capex = as_float(values.get("capital_expenditures_ttm"))
    if capex is not None:
        # SEC cash-flow payment facts are commonly negative. Store capex as a
        # positive outflow so the FCF formula remains understandable.
        capex = abs(capex)
        values["capital_expenditures_ttm"] = capex
    if operating_cash_flow is not None and capex is not None:
        values["free_cash_flow_ttm"] = operating_cash_flow - capex
    else:
        values["free_cash_flow_ttm"] = ""

    for slot, annual_values in enumerate(annual_slots):
        annual_op_cash_flow = as_float(annual_values.get("operating_cash_flow"))
        annual_capex = as_float(annual_values.get("capital_expenditures"))
        if annual_op_cash_flow is not None and annual_capex is not None:
            annual_values["free_cash_flow"] = annual_op_cash_flow - annual_capex
        values[f"annual_period_end_{slot}"] = annual_values.get("period_end", "")
        values[f"annual_filing_date_{slot}"] = annual_values.get("filing_date", "")
        for metric in (
            "revenue",
            "operating_income",
            "net_income",
            "operating_cash_flow",
            "capital_expenditures",
            "free_cash_flow",
            "diluted_eps",
        ):
            values[f"{metric}_fy{slot}"] = annual_values.get(metric, "")

    evidence_dates = [
        (parse_date(entry.get("filed")), parse_date(entry.get("end")))
        for entry in evidence
        if entry
    ]
    filed_dates = [value for value, _ in evidence_dates if value]
    period_ends = [value for _, value in evidence_dates if value]
    values["filing_date"] = max(filed_dates).isoformat() if filed_dates else ""
    values["period_end"] = max(period_ends).isoformat() if period_ends else ""
    values["error"] = ""
    return values


def snapshot_row(
    security: dict[str, str],
    financials: dict[str, Any],
    source_url: str,
) -> dict[str, Any]:
    return {
        **security,
        **{field: financials.get(field, "") for field in OUTPUT_FIELDS if field not in security},
        "data_source": "SEC Company Facts",
        "source_url": source_url,
        "error": financials.get("error", ""),
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


def main() -> int:
    args = parse_args()
    if args.workers < 1 or args.workers > MAX_WORKERS:
        print(
            f"Error: --workers must be between 1 and {MAX_WORKERS}.",
            file=sys.stderr,
        )
        return 1

    user_agent = DEFAULT_USER_AGENT

    try:
        securities = load_universe(args.universe.resolve(), args.limit)
        client = SECClient(
            user_agent=user_agent,
            base_url=os.environ.get("SEC_BASE_URL", DEFAULT_BASE_URL),
        )
        financials: list[dict[str, Any] | None] = [None] * len(securities)

        print(f"Fetching SEC Company Facts for {len(securities)} securities...")
        failed_companies = 0
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(client.get_company_facts, security["cik"]): index
                for index, security in enumerate(securities)
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                index = futures[future]
                security = securities[index]
                try:
                    company_facts = future.result()
                    financials[index] = build_financials(company_facts)
                except SECError as error:
                    # A malformed or missing CIK belongs to one row. Rate
                    # limits, authentication-style failures, and network
                    # failures should abort so an old snapshot is preserved.
                    if error.status_code in (400, 404):
                        financials[index] = {"error": str(error)}
                        failed_companies += 1
                    else:
                        raise
                except Exception as error:  # Keep one malformed company from losing the snapshot.
                    financials[index] = {
                        "error": str(error),
                    }
                    failed_companies += 1
                if completed % 25 == 0 or completed == len(securities):
                    print(f"  companies: {completed}/{len(securities)}")

        rows: list[dict[str, Any]] = []
        for index, security in enumerate(securities):
            source_url = (
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{security['cik']}.json"
            )
            rows.append(snapshot_row(security, financials[index] or {}, source_url))

        write_atomically(args.output.resolve(), rows)
    except (OSError, RuntimeError, SECError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print("The existing snapshot file was not changed.", file=sys.stderr)
        return 1

    print(f"Wrote {len(rows)} rows to {args.output.resolve()}")
    if failed_companies:
        print(f"Warning: {failed_companies} securities had SEC data errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
