from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from scripts import update_prices


def parquet_bytes(columns: dict[str, list[object]]) -> bytes:
    output = pa.BufferOutputStream()
    pq.write_table(pa.table(columns), output)
    return output.getvalue().to_pybytes()


def daily_parquet(values: list[tuple[date, float]]) -> bytes:
    return parquet_bytes(
        {
            "Date": [price_date.isoformat() for price_date, _ in values],
            "Open": [close for _, close in values],
            "High": [close for _, close in values],
            "Low": [close for _, close in values],
            "Close": [close for _, close in values],
            "Volume": [100 for _ in values],
        }
    )


class PriceSelectionTests(unittest.TestCase):
    def test_selects_median_of_five_recent_closes(self) -> None:
        today = date(2026, 8, 17)
        payload = daily_parquet(
            [
                (today - timedelta(days=7), 90.0),
                (today - timedelta(days=6), 100.0),
                (today - timedelta(days=5), 104.0),
                (today - timedelta(days=4), 102.0),
                (today - timedelta(days=3), 101.0),
                (today - timedelta(days=2), 99.0),
            ]
        )

        price, price_date, observations, reason = update_prices.select_price(
            payload, today
        )

        self.assertEqual(price, 101.0)
        self.assertEqual(price_date, today - timedelta(days=2))
        self.assertEqual(observations, 5)
        self.assertEqual(reason, "")

    def test_marks_stale_latest_price(self) -> None:
        today = date(2026, 8, 17)
        payload = daily_parquet([(today - timedelta(days=8), 100.0)])

        price, price_date, observations, reason = update_prices.select_price(
            payload, today
        )

        self.assertIsNone(price)
        self.assertEqual(price_date, today - timedelta(days=8))
        self.assertEqual(observations, 0)
        self.assertEqual(reason, "stale_price")

    def test_requires_three_recent_prices(self) -> None:
        today = date(2026, 8, 17)
        payload = daily_parquet(
            [
                (today - timedelta(days=3), 100.0),
                (today - timedelta(days=1), 102.0),
            ]
        )

        price, price_date, observations, reason = update_prices.select_price(
            payload, today
        )

        self.assertIsNone(price)
        self.assertEqual(price_date, today - timedelta(days=1))
        self.assertEqual(observations, 2)
        self.assertEqual(reason, "insufficient_recent_prices")

    def test_rejects_missing_columns_and_invalid_prices(self) -> None:
        today = date(2026, 8, 17)
        for payload in (
            parquet_bytes({"Date": ["2026-08-14"], "Open": [100.0]}),
            parquet_bytes({"Date": ["2026-08-14"], "Close": [-1.0]}),
        ):
            with self.subTest(payload=payload):
                price, price_date, observations, reason = (
                    update_prices.select_price(payload, today)
                )
                self.assertIsNone(price)
                self.assertIsNone(price_date)
                self.assertEqual(observations, 0)
                self.assertEqual(reason, "invalid_price_data")


class FakeHFHandler(BaseHTTPRequestHandler):
    prices: dict[str, bytes] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        if path == "/v1/symbols":
            self.send_json(
                {
                    "count": len(self.prices),
                    "symbols": [{"ticker": ticker} for ticker in self.prices],
                }
            )
            return

        if path.startswith("/v1/download-token/"):
            if self.headers.get("X-API-Key") != "test-key":
                self.send_error(401)
                return
            if "timeframe=daily" not in self.path or "format=parquet" not in self.path:
                self.send_error(400)
                return
            ticker = path.rsplit("/", 1)[-1]
            if ticker not in self.prices:
                self.send_error(404)
                return
            host, port = self.server.server_address
            self.send_json({"url": f"http://{host}:{port}/download/{ticker}"})
            return

        if path.startswith("/download/"):
            ticker = path.rsplit("/", 1)[-1]
            payload = self.prices.get(ticker)
            if payload is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_error(404)

    def send_json(self, value: object) -> None:
        payload = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class PriceUpdaterIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        today = date.today()
        FakeHFHandler.prices = {
            "AAPL": daily_parquet(
                [
                    (today - timedelta(days=6), 98.0),
                    (today - timedelta(days=5), 99.0),
                    (today - timedelta(days=4), 100.0),
                    (today - timedelta(days=3), 101.0),
                    (today - timedelta(days=2), 102.0),
                ]
            ),
            "JPM": daily_parquet(
                [
                    (today - timedelta(days=2), 200.0),
                    (today - timedelta(days=1), 201.0),
                ]
            ),
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHFHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_writes_all_rows_in_universe_order_with_exclusion_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            universe = directory / "universe.csv"
            output = directory / "latest_prices.csv"
            universe.write_text(
                "ticker,name\n"
                "AAPL,Apple Inc.\n"
                "META,Meta Platforms\n"
                "JPM,JPMorgan Chase\n",
                encoding="utf-8",
            )
            output.write_text("old snapshot\n", encoding="utf-8")
            host, port = self.server.server_address

            with patch.dict(
                os.environ,
                {
                    "HF_DATA_API_KEY": "test-key",
                    "HF_BASE_URL": f"http://{host}:{port}/v1",
                },
                clear=False,
            ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = update_prices.main(
                    [
                        "--universe",
                        str(universe),
                        "--output",
                        str(output),
                        "--request-delay",
                        "0",
                    ]
                )

            self.assertEqual(result, 0)
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual([row["ticker"] for row in rows], ["AAPL", "META", "JPM"])
            self.assertEqual(rows[0]["valuation_price"], "100")
            self.assertEqual(rows[0]["status"], "ok")
            self.assertEqual(rows[1]["status"], "excluded")
            self.assertEqual(rows[1]["reason"], "ticker_unavailable")
            self.assertEqual(rows[2]["status"], "excluded")
            self.assertEqual(rows[2]["reason"], "insufficient_recent_prices")
            self.assertEqual(rows[2]["observations"], "2")

    def test_missing_api_key_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            universe = directory / "universe.csv"
            output = directory / "latest_prices.csv"
            universe.write_text("ticker,name\nAAPL,Apple Inc.\n", encoding="utf-8")
            output.write_text("old snapshot\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True), redirect_stdout(
                io.StringIO()
            ), redirect_stderr(io.StringIO()):
                result = update_prices.main(
                    ["--universe", str(universe), "--output", str(output)]
                )

            self.assertEqual(result, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "old snapshot\n")


if __name__ == "__main__":
    unittest.main()
