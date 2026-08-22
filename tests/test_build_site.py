import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "build_site", PROJECT_DIR / "scripts" / "build_site.py"
)
build_site = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(build_site)


class BuildSiteTests(unittest.TestCase):
    def make_research(self, path: Path, name: str = "Company 1") -> None:
        fields = sorted(build_site.REQUIRED_FIELDS)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for rank in range(1, 11):
                writer.writerow(
                    {
                        "rank": rank,
                        "ticker": f"T{rank}",
                        "name": name if rank == 1 else f"Company {rank}",
                        "sector": "Industrials",
                        "subsector": "Research & Testing",
                        "valuation_price": "123.456",
                        "price_date": "2026-08-14",
                        "attractiveness_score": "91.63" if rank == 1 else "80.0",
                        "quality_display_score": "9.2",
                        "quality_label": "Strong",
                        "why_selected": "Selected by disclosed rules.",
                        "valuation_summary": "Valuation explanation.",
                        "business_quality_summary": "Quality explanation.",
                        "growth_summary": "Growth explanation.",
                        "balance_sheet_summary": "Balance sheet explanation.",
                        "warning_summary": "Review the numbers.",
                        "what_to_verify": "Read the filing.",
                        "review_status": "priority_review" if rank == 1 else "standard_review",
                        "snapshot_source_url": "https://data.sec.gov/example",
                        "price_source_url": "https://hfdatalibrary.com/pages/data",
                    }
                )

    def args(self, input_path: Path, output: Path, published: str):
        return build_site.parse_args(
            [
                "--input",
                str(input_path),
                "--templates",
                str(PROJECT_DIR / "templates"),
                "--assets",
                str(PROJECT_DIR / "assets"),
                "--output",
                str(output),
                "--published-at",
                published,
            ]
        )

    def test_builds_complete_github_pages_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research = root / "research.csv"
            output = root / "docs"
            self.make_research(research)

            build_site.build(self.args(research, output, "2026-08-22T17:47:00+02:00"))

            generated = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("Saturday, August 22, 2026 · 17:00 CEST", generated)
            self.assertNotIn("Price data:", generated)
            self.assertIn("9.2 <small>Excellent</small>", generated)
            self.assertIn("9.2 <small>Strong</small>", generated)
            self.assertEqual(generated.count('class="company-row"'), 10)
            self.assertEqual(generated.count('class="company-card"'), 10)
            self.assertNotIn(">Risk<", generated)
            self.assertTrue((output / "assets" / "styles.css").is_file())
            self.assertTrue((output / "assets" / "site.js").is_file())
            self.assertTrue((output / ".nojekyll").is_file())

    def test_escapes_research_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research = root / "research.csv"
            output = root / "docs"
            self.make_research(research, '<script>alert("x")</script>')

            build_site.build(self.args(research, output, "2026-08-22T12:00:00Z"))

            generated = (output / "index.html").read_text(encoding="utf-8")
            self.assertNotIn('<script>alert("x")</script>', generated)
            self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", generated)

    def test_rejects_incomplete_shortlist_before_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research = root / "research.csv"
            output = root / "docs"
            self.make_research(research)
            with research.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))[:9]
            with research.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            output.mkdir()
            (output / "index.html").write_text("existing", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Expected 10"):
                build_site.build(self.args(research, output, "2026-08-22T17:00:00+02:00"))

            self.assertEqual((output / "index.html").read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
