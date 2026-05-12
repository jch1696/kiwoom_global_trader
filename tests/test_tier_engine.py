from __future__ import annotations

import unittest
from datetime import datetime

from src.config import TradingConfig
from src.models import Balance, Strategy, Tier
from src.tier_engine import TierEngine


class TierEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = TierEngine(TradingConfig())
        self.strategy = Strategy(
            sheet_name="TEST",
            enabled=True,
            account_no="123",
            symbol="SOXL",
            investment_usd=1000,
            total_tiers=3,
            base_price=10,
            refresh_base_price=True,
            buy_blocked=False,
            sell_blocked=False,
            tiers=[
                Tier(0, 0, 0, 0, 10, 1, 0, 0),
                Tier(1, 10, 100, 10, 9, 1, 11, 1),
                Tier(2, 20, 100, 9, 8, 1, 10, 1),
                Tier(3, 30, 100, 8, 0, 0, 9, 1),
            ],
        )

    def balance(self, qty: int) -> Balance:
        return Balance("123", "SOXL", qty, qty, 0, 0, 0, 0, datetime.now())

    def test_below_first_tier_buys_first_tier(self) -> None:
        decision = self.engine.decide(self.strategy, self.balance(0))
        self.assertEqual(decision.current_tier, 0)
        self.assertIsNotNone(decision.buy_order)
        self.assertEqual(decision.buy_order.tier_no, 1)
        self.assertIsNone(decision.sell_order)

    def test_middle_tie_uses_lower_tier(self) -> None:
        self.assertEqual(self.engine.current_tier(self.strategy.tiers, 15), 1)

    def test_highest_tier_has_no_buy(self) -> None:
        decision = self.engine.decide(self.strategy, self.balance(30))
        self.assertEqual(decision.current_tier, 3)
        self.assertIsNone(decision.buy_order)
        self.assertIsNotNone(decision.sell_order)


if __name__ == "__main__":
    unittest.main()

