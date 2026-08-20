from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import rank_companies


def screen_row(
    ticker: str,
    sector: str = "Industrials",
    cik: str | None = None,
    value: float = 1.0,
) -> dict[str, str]:
    return {
        "ticker": ticker,
        "cik": cik or ticker,
        "name": f"{ticker} Company",
        "sector": sector,
        "subsector": "Test",
        "valuation_price": "10",
        "price_date": "2026-08-14",
        "free_cash_flow_yield": str(0.05 * value),
        "earnings_yield": str(0.04 * value),
        "ev_to_operating_income": str(20 / value),
        "operating_margin": str(0.1 * value),
        "five_year_revenue_growth": str(0.03 * value),
        "positive_fcf_years": "5",
        "historical_fcf_years": "5",
        "net_debt_to_fcf": str(5 / value),
        "five_year_median_fcf": "100",
        "five_year_median_net_income": "100",
        "calculation_status": "ok",
        "snapshot_source_url": "https://example.com/sec",
        "price_source_url": "https://example.com/prices",
    }


class RankingUnitTests(unittest.TestCase):
    def test_percentiles_average_ties_and_respect_direction(self) -> None:
        rows = [{"metric": 1.0}, {"metric": 2.0}, {"metric": 2.0}, {"metric": 4.0}]
        high = rank_companies.percentile_scores(rows, "metric", True)
        low = rank_companies.percentile_scores(rows, "metric", False)

        self.assertEqual(high[0], 0)
        self.assertEqual(high[1], 50)
        self.assertEqual(high[2], 50)
        self.assertEqual(high[3], 100)
        self.assertEqual(low[0], 100)
        self.assertEqual(low[3], 0)

    def test_eligibility_excludes_incomplete_specialized_and_unstable_rows(self) -> None:
        partial = screen_row("PART")
        partial["calculation_status"] = "partial"
        financial = screen_row("BANK", "Financials")
        unstable = screen_row("FCF")
        unstable["positive_fcf_years"] = "3"
        negative_median = screen_row("NEG")
        negative_median["five_year_median_fcf"] = "-1"

        self.assertFalse(rank_companies.is_eligible(partial))
        self.assertFalse(rank_companies.is_eligible(financial))
        self.assertFalse(rank_companies.is_eligible(unstable))
        self.assertFalse(rank_companies.is_eligible(negative_median))
        self.assertTrue(rank_companies.is_eligible(screen_row("GOOD")))

    def test_sector_scores_fall_back_to_global_for_small_groups(self) -> None:
        rows = [screen_row(f"I{i}", "Industrials", value=i + 1) for i in range(5)]
        rows += [screen_row("U1", "Utilities", value=0.5)]
        prepared: list[dict[str, object]] = []
        for source in rows:
            row: dict[str, object] = dict(source)
            for metric in rank_companies.METRICS:
                if metric != "fcf_consistency":
                    row[metric] = float(source[metric])
            row["positive_fcf_years"] = 5.0
            row["historical_fcf_years"] = 5.0
            prepared.append(row)

        rank_companies.add_metric_scores(prepared)
        global_scores = rank_companies.percentile_scores(
            prepared, "free_cash_flow_yield", True
        )

        self.assertEqual(prepared[5]["free_cash_flow_yield_score"], global_scores[5])
        self.assertEqual(prepared[0]["free_cash_flow_yield_score"], 0)
        self.assertEqual(prepared[4]["free_cash_flow_yield_score"], 100)

    def test_ranking_deduplicates_cik_and_caps_sector_at_two(self) -> None:
        rows = [screen_row("A", cik="1", value=10), screen_row("A2", cik="1", value=9)]
        rows += [screen_row("B", value=8), screen_row("C", value=7)]
        rows += [screen_row("D", "Technology", value=6)]
        rows += [screen_row("E", "Health Care", value=5)]

        ranked = rank_companies.rank_companies(rows, limit=4)

        self.assertEqual([row["ticker"] for row in ranked], ["A", "B", "D", "E"])
        self.assertEqual(len({row["cik"] for row in ranked}), 4)
        self.assertLessEqual(
            max([row["sector"] for row in ranked].count(sector) for sector in {row["sector"] for row in ranked}),
            2,
        )
        self.assertTrue(all(0 <= float(row["attractiveness_score"]) <= 100 for row in ranked))

    def test_extreme_yields_are_flagged_without_exclusion(self) -> None:
        row: dict[str, object] = screen_row("OUTLIER")
        row["free_cash_flow_yield"] = 0.30
        row["earnings_yield"] = 0.26

        self.assertEqual(
            rank_companies.review_flags(row),
            "extreme_fcf_yield_verify_inputs;extreme_earnings_yield_verify_inputs",
        )

    def test_output_cleans_source_name_delimiter(self) -> None:
        rows = [screen_row("A", "Industrials", value=2)]
        rows[0]["name"] = "Example Corp|"

        ranked = rank_companies.rank_companies(rows, limit=1)

        self.assertEqual(ranked[0]["name"], "Example Corp")


class RankingIntegrationTests(unittest.TestCase):
    def test_invalid_input_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            input_path = directory / "screen.csv"
            output_path = directory / "top10.csv"
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ticker"])
                writer.writeheader()
                writer.writerow({"ticker": "TEST"})
            output_path.write_text("old output\n", encoding="utf-8")

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = rank_companies.main(
                    ["--input", str(input_path), "--output", str(output_path)]
                )

            self.assertEqual(result, 1)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "old output\n")


if __name__ == "__main__":
    unittest.main()
