from __future__ import annotations

import tempfile
import unittest
from datetime import datetime

from src.brokers.simulated import SimulatedBroker
from src.config import TradingConfig
from src.csv_logger import CsvLogger
from src.models import Fill, Side, Strategy, Tier
from src.notifier import NullNotifier
from src.settlement_writer import SettlementWriter
from src.tier_engine import TierEngine


def _strategy() -> Strategy:
    return Strategy(
        sheet_name="LABU55",
        enabled=True,
        account_no="123",
        symbol="LABU",
        investment_usd=1000,
        total_tiers=2,
        base_price=10,
        refresh_base_price=True,
        buy_blocked=False,
        sell_blocked=False,
        tiers=[
            Tier(0, 0, 0, 0, 10, 1, 0, 0),
            Tier(1, 10, 100, 10, 9, 1, 11, 1),
            Tier(2, 20, 100, 9, 0, 0, 10, 1),
        ],
    )


class SettlementWriterTest(unittest.TestCase):
    def test_creates_record(self) -> None:
        broker = SimulatedBroker(
            {"LABU": 10},
            fills=[
                Fill("1", "123", "LABU", Side.BUY, 9.0, 1, datetime(2026, 4, 27, 10, 0, 0)),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            writer = SettlementWriter(broker, TierEngine(TradingConfig()), CsvLogger(tmp), NullNotifier())
            records = writer.settle([_strategy()], trade_date_et="2026-04-27", send_telegram=False)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].final_qty, 10)
        self.assertIn("1", records[0].buy_summary)

    def test_appends_settlement_records_to_sheet_writer(self) -> None:
        class FakeSheetWriter:
            def __init__(self) -> None:
                self.records = []

            def append_records(self, records):
                self.records = list(records)

        sheet_writer = FakeSheetWriter()
        broker = SimulatedBroker({"LABU": 10})
        with tempfile.TemporaryDirectory() as tmp:
            writer = SettlementWriter(
                broker,
                TierEngine(TradingConfig()),
                CsvLogger(tmp),
                NullNotifier(),
                sheet_writer,
            )
            records = writer.settle([_strategy()], trade_date_et="2026-04-27", send_telegram=False)

        self.assertEqual(sheet_writer.records, records)


if __name__ == "__main__":
    unittest.main()
