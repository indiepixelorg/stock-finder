from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import build_screen


def universe_row() -> dict[str, str]:
    return {
        "ticker": "TEST",
        "cik": "0000000001",
        "name": "Test Company",
        "sector": "Industrials",
        "subsector": "Test Equipment",
    }


def snapshot_row() -> dict[str, str]:
    row = {
        "ticker": "TEST",
        "revenue_ttm": "500",
        "operating_income_ttm": "150",
        "net_income_ttm": "100",
        "free_cash_flow_ttm": "80",
        "cash_and_equivalents": "100",
        "total_debt": "300",
        "stockholders_equity": "400",
        "shares_outstanding": "100",
        "filing_date": "2026-05-01",
        "period_end": "2026-03-31",
        "source_url": "https://example.com/sec",
    }
    annual_revenue = [200, 170, 145, 120, 100]
    annual_fcf = [10, 0, -1, 5, 20]
    annual_net_income = [5, 4, 3, 2, 1]
    for slot in range(5):
        row[f"annual_period_end_{slot}"] = f"{2025 - slot}-12-31"
        row[f"revenue_fy{slot}"] = str(annual_revenue[slot])
        row[f"free_cash_flow_fy{slot}"] = str(annual_fcf[slot])
        row[f"net_income_fy{slot}"] = str(annual_net_income[slot])
    return row


def price_row(status: str = "ok", reason: str = "") -> dict[str, str]:
    return {
        "ticker": "TEST",
        "valuation_price": "10",
        "price_date": "2026-08-14",
        "observations": "5",
        "status": status,
        "reason": reason,
        "source_url": "https://example.com/prices",
    }


class ScreenFormulaTests(unittest.TestCase):
    def test_calculates_requested_metrics(self) -> None:
        row = build_screen.build_row(universe_row(), snapshot_row(), price_row())

        self.assertEqual(row["market_cap"], "1000")
        self.assertEqual(row["net_debt"], "200")
        self.assertEqual(row["enterprise_value"], "1200")
        self.assertEqual(row["earnings_yield"], "0.1")
        self.assertEqual(row["free_cash_flow_yield"], "0.08")
        self.assertEqual(row["price_to_earnings"], "10")
        self.assertEqual(row["price_to_fcf"], "12.5")
        self.assertEqual(row["ev_to_operating_income"], "8")
        self.assertEqual(row["operating_margin"], "0.3")
        self.assertAlmostEqual(float(row["five_year_revenue_growth"]), 0.1892, places=3)
        self.assertEqual(row["positive_fcf_years"], 3)
        self.assertEqual(row["historical_fcf_years"], 5)
        self.assertEqual(row["net_debt_to_fcf"], "2.5")
        self.assertEqual(row["price_to_book"], "2.5")
        self.assertEqual(row["five_year_median_fcf"], "5")
        self.assertEqual(row["historical_net_income_years"], 5)
        self.assertEqual(row["five_year_median_net_income"], "3")
        self.assertEqual(row["calculation_status"], "ok")
        self.assertEqual(row["calculation_warnings"], "")

    def test_negative_flows_keep_yields_but_blank_multiples(self) -> None:
        snapshot = snapshot_row()
        snapshot["net_income_ttm"] = "-20"
        snapshot["free_cash_flow_ttm"] = "-10"
        snapshot["operating_income_ttm"] = "-5"

        row = build_screen.build_row(universe_row(), snapshot, price_row())

        self.assertEqual(row["earnings_yield"], "-0.02")
        self.assertEqual(row["free_cash_flow_yield"], "-0.01")
        self.assertEqual(row["operating_margin"], "-0.01")
        self.assertEqual(row["price_to_earnings"], "")
        self.assertEqual(row["price_to_fcf"], "")
        self.assertEqual(row["ev_to_operating_income"], "")
        self.assertEqual(row["net_debt_to_fcf"], "")
        self.assertEqual(row["calculation_status"], "partial")
        self.assertIn("nonpositive_net_income_ttm", row["calculation_warnings"])
        self.assertIn("nonpositive_free_cash_flow_ttm", row["calculation_warnings"])

    def test_missing_balance_sheet_values_are_not_treated_as_zero(self) -> None:
        snapshot = snapshot_row()
        snapshot["total_debt"] = ""

        row = build_screen.build_row(universe_row(), snapshot, price_row())

        self.assertEqual(row["market_cap"], "1000")
        self.assertEqual(row["net_debt"], "")
        self.assertEqual(row["enterprise_value"], "")
        self.assertEqual(row["ev_to_operating_income"], "")
        self.assertEqual(row["calculation_status"], "partial")
        self.assertIn("missing_total_debt", row["calculation_warnings"])

    def test_excluded_price_keeps_company_without_calculations(self) -> None:
        row = build_screen.build_row(
            universe_row(),
            snapshot_row(),
            price_row("excluded", "ticker_unavailable"),
        )

        self.assertEqual(row["ticker"], "TEST")
        self.assertEqual(row["market_cap"], "")
        self.assertEqual(row["calculation_status"], "excluded")
        self.assertEqual(row["calculation_warnings"], "ticker_unavailable")


class ScreenBuildIntegrationTests(unittest.TestCase):
    def write_csv(
        self, path: Path, fieldnames: list[str], rows: list[dict[str, str]]
    ) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def test_mismatched_inputs_preserve_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            universe_path = directory / "universe.csv"
            snapshot_path = directory / "snapshot.csv"
            prices_path = directory / "prices.csv"
            output_path = directory / "screen.csv"

            self.write_csv(universe_path, list(universe_row()), [universe_row()])
            snapshot = snapshot_row()
            snapshot["ticker"] = "OTHER"
            self.write_csv(snapshot_path, list(snapshot), [snapshot])
            self.write_csv(prices_path, list(price_row()), [price_row()])
            output_path.write_text("old output\n", encoding="utf-8")

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = build_screen.main(
                    [
                        "--universe",
                        str(universe_path),
                        "--snapshot",
                        str(snapshot_path),
                        "--prices",
                        str(prices_path),
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(result, 1)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "old output\n")


if __name__ == "__main__":
    unittest.main()
