from __future__ import annotations

import unittest
from datetime import datetime

from src.config import GoogleConfig
from src.google_sheet_writer import extract_spreadsheet_id, resolve_spreadsheet_id, settlement_record_to_row
from src.models import SettlementRecord


class GoogleSheetWriterTest(unittest.TestCase):
    def test_extracts_spreadsheet_id_from_export_url(self) -> None:
        url = "https://docs.google.com/spreadsheets/d/abc-123_DEF/export?format=csv&gid=1"
        self.assertEqual(extract_spreadsheet_id(url), "abc-123_DEF")

    def test_resolves_spreadsheet_id_from_public_tabs(self) -> None:
        config = GoogleConfig(
            public_csv_tabs={"LABU55": "https://docs.google.com/spreadsheets/d/sheet-id/export?format=csv&gid=1"}
        )
        self.assertEqual(resolve_spreadsheet_id(config), "sheet-id")

    def test_settlement_record_to_row(self) -> None:
        record = SettlementRecord(
            updated_at_kst=datetime(2026, 5, 10, 7, 10, 0),
            trade_date_et="2026-05-09",
            sheet_name="LABU55",
            account_no="12345678",
            symbol="LABU",
            final_tier=6,
            total_tiers=56,
            final_qty=5,
            investment_usd=10000,
            base_price=100,
            valuation=1000,
            pnl=12.5,
            next_buy="buy 180 x 1",
            next_sell="sell 195 x 5",
            buy_summary="0",
            sell_summary="1",
        )
        row = settlement_record_to_row(record)
        self.assertEqual(row[:5], ["2026-05-10 07:10:00", "05-09", "LABU55", "LABU", 6])
        self.assertEqual(row[-1], "1")
        self.assertEqual(len(row), 17)


if __name__ == "__main__":
    unittest.main()

