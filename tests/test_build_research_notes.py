from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import build_research_notes


def top_row(ticker: str = "TEST") -> dict[str, str]:
    return {
        "rank": "1",
        "ticker": ticker,
        "cik": "0000000001",
        "name": "Test Company",
        "sector": "Information Technology",
        "subsector": "Software",
        "valuation_price": "100",
        "price_date": "2026-08-14",
        "attractiveness_score": "88.25",
        "quality_score": "87.50",
        "quality_display_score": "8.8",
        "quality_label": "Strong",
        "free_cash_flow_yield": "0.09",
        "free_cash_flow_yield_score": "90",
        "earnings_yield": "0.06",
        "earnings_yield_score": "85",
        "ev_to_operating_income": "12.5",
        "operating_margin": "0.35",
        "operating_margin_score": "90",
        "five_year_revenue_growth": "0.10",
        "positive_fcf_years": "5",
        "historical_fcf_years": "5",
        "net_debt_to_fcf": "-0.5",
        "selection_reasons": "FCF yield 9.0%; earnings yield 6.0%; margin 35.0%",
        "review_flags": "",
        "snapshot_source_url": "https://example.com/sec",
        "price_source_url": "https://example.com/prices",
    }


def screen_row(ticker: str = "TEST") -> dict[str, str]:
    return {
        "ticker": ticker,
        "cik": "0000000001",
        "filing_date": "2026-08-10",
        "period_end": "2026-06-30",
        "net_income_ttm": "120",
        "free_cash_flow_ttm": "150",
        "total_debt": "50",
        "stockholders_equity": "100",
        "five_year_median_fcf": "100",
        "five_year_median_net_income": "100",
    }


class ResearchNoteTests(unittest.TestCase):
    def test_builds_traceable_positive_summary(self) -> None:
        note = build_research_notes.build_note(top_row(), screen_row())

        self.assertIn("88.25/100", note["why_selected"])
        self.assertIn("9.0% free-cash-flow yield", note["valuation_summary"])
        self.assertIn("Operating margin is 35.0%", note["business_quality_summary"])
        self.assertIn("8.8/10 (Strong)", note["business_quality_summary"])
        self.assertIn("positive in 5 of 5", note["business_quality_summary"])
        self.assertIn("10.0% annually", note["growth_summary"])
        self.assertIn("more cash than debt", note["balance_sheet_summary"])
        self.assertIn("net cash equal to 0.5x", note["balance_sheet_summary"])
        self.assertEqual(note["review_status"], "standard_review")
        self.assertEqual(note["generation_method"], "deterministic_rules_v1")
        self.assertEqual(note["quality_score"], "87.50")
        self.assertEqual(note["quality_display_score"], "8.8")
        self.assertEqual(note["quality_label"], "Strong")

    def test_flags_extreme_and_non_recurring_looking_results(self) -> None:
        top = top_row()
        top["review_flags"] = "extreme_fcf_yield_verify_inputs"
        screen = screen_row()
        screen["net_income_ttm"] = "300"
        screen["free_cash_flow_ttm"] = "250"

        note = build_research_notes.build_note(top, screen)

        self.assertEqual(note["review_status"], "priority_review")
        self.assertIn("share count", note["warning_summary"])
        self.assertIn("TTM net income is 3.0x", note["warning_summary"])
        self.assertIn("TTM free cash flow is 2.5x", note["warning_summary"])
        self.assertIn("shares outstanding", note["what_to_verify"])

    def test_adds_sector_specific_verification(self) -> None:
        note = build_research_notes.build_note(top_row(), screen_row())

        self.assertIn("product transitions", note["what_to_verify"])
        self.assertIn("latest 10-K or 10-Q", note["what_to_verify"])

    def test_flags_suspiciously_low_reported_debt(self) -> None:
        screen = screen_row()
        screen["total_debt"] = "5"

        note = build_research_notes.build_note(top_row(), screen)

        self.assertEqual(note["review_status"], "priority_review")
        self.assertIn("less than 10%", note["warning_summary"])
        self.assertIn("lease obligations", note["what_to_verify"])

    def test_rejects_cik_mismatch(self) -> None:
        screen = screen_row()
        screen["cik"] = "0000000002"

        with self.assertRaisesRegex(RuntimeError, "CIK mismatch"):
            build_research_notes.build_note(top_row(), screen)


class ResearchNoteIntegrationTests(unittest.TestCase):
    def write_csv(
        self, path: Path, fieldnames: list[str], rows: list[dict[str, str]]
    ) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def test_invalid_input_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            top_path = directory / "top.csv"
            screen_path = directory / "screen.csv"
            output_path = directory / "research.csv"
            self.write_csv(top_path, ["ticker"], [{"ticker": "TEST"}])
            self.write_csv(screen_path, list(screen_row()), [screen_row()])
            output_path.write_text("old output\n", encoding="utf-8")

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = build_research_notes.main(
                    [
                        "--top10",
                        str(top_path),
                        "--screen",
                        str(screen_path),
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(result, 1)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "old output\n")


if __name__ == "__main__":
    unittest.main()
