from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import Mock

from src.config import GoogleConfig
from src.google_sheet_writer import (
    GoogleProgramInfoWriter,
    ProgramInfoUpdate,
    extract_spreadsheet_id,
    resolve_spreadsheet_id,
    settlement_record_to_row,
)
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

    def test_program_info_writer_updates_template_cells(self) -> None:
        writer = GoogleProgramInfoWriter("sheet-id", "credentials.json")
        request = Mock()
        service = Mock()
        service.spreadsheets.return_value.values.return_value.batchUpdate.return_value = request
        writer._service = service

        writer.update_program_info(
            "ETHT55",
            ProgramInfoUpdate(
                updated_at=datetime(2026, 5, 20, 22, 31, 5),
                current_tier=9,
                current_price=14.72,
                balance_qty=617,
                qty_gap=2,
                buy_open_count=0,
                sell_open_count=1,
            ),
        )

        kwargs = service.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs
        data = {item["range"]: item["values"][0][0] for item in kwargs["body"]["data"]}
        self.assertEqual(data["'ETHT55'!K4"], "05-20 22:31:05")
        self.assertEqual(data["'ETHT55'!K6"], 9)
        self.assertEqual(data["'ETHT55'!K8"], 14.72)
        self.assertEqual(data["'ETHT55'!K10"], 617)
        self.assertEqual(data["'ETHT55'!K12"], 2)
        self.assertEqual(data["'ETHT55'!K14"], 0)
        self.assertEqual(data["'ETHT55'!K16"], 1)


if __name__ == "__main__":
    unittest.main()

