from __future__ import annotations

import unittest
from datetime import datetime

from src.config import NotifyConfig, TradingConfig
from src.models import Balance, CancelResult, OpenOrder, OrderResult, Side, Strategy, Tier
from src.notifier import NullNotifier
from src.order_manager import OrderManager
from src.tier_engine import TierEngine


class FakeBroker:
    def __init__(
        self,
        open_orders: list[OpenOrder],
        place_result: OrderResult | None = None,
        balance_qty: int = 47,
        current_price: float = 173.62,
    ) -> None:
        self.open_orders = open_orders
        self.place_result = place_result
        self.balance_qty = balance_qty
        self.current_price = current_price
        self.cancelled: list[str] = []
        self.probed_orders: list = []

    def get_balance(self, account_no: str, symbol: str) -> Balance:
        return Balance(
            account_no=account_no,
            symbol=symbol,
            qty=self.balance_qty,
            available_qty=41,
            avg_price=183.6914,
            current_price=self.current_price,
            valuation=0,
            pnl=0,
            fetched_at=datetime.now(),
        )

    def get_open_orders(self, account_no: str, symbol: str) -> list[OpenOrder]:
        return self.open_orders

    def cancel_order(self, account_no: str, order_id: str):
        self.cancelled.append(order_id)
        return CancelResult(True, "cancelled")

    def place_order(self, order):
        return self.place_result or OrderResult(True, "TESTORDER", "accepted")

    def probe_place_order(self, order, execute=False):
        self.probed_orders.append(order)
        return {"ok": "true", "executed": "true" if execute else "false", "message": "form populated without clicking order button"}


class FakeLogger:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.errors: list[dict] = []

    def log_order(self, row: dict) -> None:
        self.orders.append(row)

    def log_error(self, row: dict) -> None:
        self.errors.append(row)


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str):
        self.messages.append(text)
        return type("NotifyResult", (), {"sent": True, "message": "ok"})()


class OrderManagerTest(unittest.TestCase):
    def test_keeps_matching_nearest_open_order_without_placing_other_side(self) -> None:
        broker = FakeBroker(
            [
                OpenOrder(
                    order_id="47860",
                    account_no="12345678",
                    symbol="LABU",
                    side=Side.SELL,
                    price=178.70,
                    original_qty=6,
                    remaining_qty=6,
                    status="?묒닔",
                    submitted_at=None,
                    fetched_at=datetime.now(),
                )
            ]
        )
        logger = FakeLogger()
        trading = TradingConfig(dry_run=True)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, NullNotifier())

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(result.current_tier, 9)
        self.assertEqual(result.actions, ["keep:sell"])
        self.assertEqual(broker.cancelled, [])
        self.assertEqual(logger.orders[0]["action"], "keep_open_order")
        self.assertEqual(len(logger.orders), 1)

    def test_places_only_nearest_order_when_no_open_order_exists(self) -> None:
        broker = FakeBroker([])
        logger = FakeLogger()
        trading = TradingConfig(dry_run=True)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, NullNotifier())

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(result.current_tier, 9)
        self.assertEqual(result.actions, ["place:sell:178.7:6"])
        self.assertEqual(len(logger.orders), 1)
        self.assertEqual(logger.orders[0]["action"], "dry_run_place")
        self.assertEqual(logger.orders[0]["side"], "sell")

    def test_dry_run_fill_order_populates_order_form_probe(self) -> None:
        broker = FakeBroker([])
        logger = FakeLogger()
        trading = TradingConfig(dry_run=True, dry_run_fill_order=True)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, NullNotifier())

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(result.actions, ["place:sell:178.7:6"])
        self.assertEqual(len(broker.probed_orders), 1)
        self.assertEqual(logger.orders[0]["action"], "dry_run_fill_order")
        self.assertEqual(logger.orders[0]["result"], "success")

    def test_dry_run_place_does_not_send_order_plan_notification(self) -> None:
        broker = FakeBroker([])
        logger = FakeLogger()
        notifier = RecordingNotifier()
        trading = TradingConfig(dry_run=True)
        notify = NotifyConfig(telegram_send_orders=True, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, notifier)

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(logger.orders[0]["action"], "dry_run_place")
        self.assertEqual(logger.orders[0]["telegram_sent"], False)
        self.assertEqual(notifier.messages, [])

    def test_dry_run_fill_order_does_not_send_validation_notification(self) -> None:
        broker = FakeBroker([])
        logger = FakeLogger()
        notifier = RecordingNotifier()
        trading = TradingConfig(dry_run=True, dry_run_fill_order=True)
        notify = NotifyConfig(telegram_send_orders=True, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, notifier)

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(logger.orders[0]["action"], "dry_run_fill_order")
        self.assertEqual(logger.orders[0]["telegram_sent"], False)
        self.assertEqual(notifier.messages, [])

    def test_dry_run_cancel_does_not_send_cancel_planned_notification(self) -> None:
        broker = FakeBroker(
            [
                OpenOrder(
                    order_id="OLD",
                    account_no="12345678",
                    symbol="LABU",
                    side=Side.BUY,
                    price=160.13,
                    original_qty=6,
                    remaining_qty=6,
                    status="accepted",
                    submitted_at=None,
                    fetched_at=datetime.now(),
                )
            ]
        )
        logger = FakeLogger()
        notifier = RecordingNotifier()
        trading = TradingConfig(dry_run=True)
        notify = NotifyConfig(telegram_send_orders=True, telegram_send_cancels=True)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, notifier)

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertIn("dry_run_cancel", [row["action"] for row in logger.orders])
        self.assertEqual(notifier.messages, [])

    def test_live_order_sends_compact_summary_only(self) -> None:
        broker = FakeBroker([], balance_qty=47, current_price=173.62)
        logger = FakeLogger()
        notifier = RecordingNotifier()
        trading = TradingConfig(dry_run=False)
        notify = NotifyConfig(telegram_send_orders=True, telegram_send_cancels=True)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, notifier)

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(
            notifier.messages,
            [
                "LABU 9/55L (Buy 0 / Sell 1)\n"
                "LABU 173.62 (160.13 / 178.70)\n"
                "status: ORDER 1/1 OK"
            ],
        )

    def test_live_cancel_success_does_not_send_cancel_notification(self) -> None:
        broker = FakeBroker(
            [
                OpenOrder(
                    order_id="OLD",
                    account_no="12345678",
                    symbol="LABU",
                    side=Side.BUY,
                    price=160.13,
                    original_qty=6,
                    remaining_qty=6,
                    status="accepted",
                    submitted_at=None,
                    fetched_at=datetime.now(),
                )
            ]
        )
        logger = FakeLogger()
        notifier = RecordingNotifier()
        trading = TradingConfig(dry_run=False)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=True)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, notifier)

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(notifier.messages, [])

    def test_order_unavailable_day_halts_all_trading(self) -> None:
        broker = FakeBroker([], OrderResult(False, None, "[571563] \uc8fc\ubb38\ubd88\uac00\ub2a5\ud55c\ub0a0\uc785\ub2c8\ub2e4"))
        logger = FakeLogger()
        trading = TradingConfig(dry_run=False)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=False, telegram_send_failures=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, NullNotifier())

        result = manager.sync_strategy(_strategy())

        self.assertFalse(result.success)
        self.assertTrue(result.skipped)
        self.assertTrue(result.halt_all)
        self.assertIn("571563", result.message)
        self.assertEqual(logger.orders[-1]["result"], "failed")

    def test_rebalance_places_qty_gap_before_normal_tier_order(self) -> None:
        broker = FakeBroker([], balance_qty=45, current_price=160.0)
        logger = FakeLogger()
        trading = TradingConfig(dry_run=True, rebalance_enabled=True)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, NullNotifier())

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(result.current_tier, 9)
        self.assertEqual(result.actions, ["rebalance:buy:45->47", "place:buy:165.08:2"])
        self.assertEqual(logger.orders[0]["side"], "buy")
        self.assertEqual(logger.orders[0]["price"], 165.08)
        self.assertEqual(logger.orders[0]["qty"], 2)

    def test_rebalance_cancels_existing_order_then_places_gap_order(self) -> None:
        broker = FakeBroker(
            [
                OpenOrder(
                    order_id="OLD",
                    account_no="12345678",
                    symbol="LABU",
                    side=Side.SELL,
                    price=178.70,
                    original_qty=6,
                    remaining_qty=6,
                    status="accepted",
                    submitted_at=None,
                    fetched_at=datetime.now(),
                )
            ],
            balance_qty=45,
            current_price=160.0,
        )
        logger = FakeLogger()
        trading = TradingConfig(dry_run=True, rebalance_enabled=True)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, NullNotifier())

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(result.actions, ["rebalance:buy:45->47", "cancel:sell:OLD", "place:buy:165.08:2"])
        self.assertEqual(broker.cancelled, [])
        self.assertEqual(logger.orders[0]["action"], "dry_run_cancel")
        self.assertEqual(logger.orders[1]["action"], "dry_run_place")

    def test_rebalance_uses_sell_side_when_buy_price_is_far_below_current_price(self) -> None:
        broker = FakeBroker([], balance_qty=45, current_price=173.62)
        logger = FakeLogger()
        trading = TradingConfig(dry_run=True, rebalance_enabled=True)
        notify = NotifyConfig(telegram_send_orders=False, telegram_send_cancels=False)
        manager = OrderManager(broker, TierEngine(trading), trading, notify, logger, NullNotifier())

        result = manager.sync_strategy(_strategy())

        self.assertTrue(result.success)
        self.assertEqual(result.current_tier, 9)
        self.assertEqual(result.actions, ["rebalance:sell:45->41", "place:sell:178.7:4"])
        self.assertEqual(logger.orders[0]["side"], "sell")
        self.assertEqual(logger.orders[0]["price"], 178.70)
        self.assertEqual(logger.orders[0]["qty"], 4)


def _strategy() -> Strategy:
    tiers = [
        Tier(8, 41, 1000, 175.45, 170.19, 6, 184.22, 6),
        Tier(9, 47, 1000, 170.19, 165.08, 6, 178.70, 6),
        Tier(10, 53, 1000, 165.08, 160.13, 6, 173.33, 6),
    ]
    return Strategy(
        sheet_name="LABU",
        enabled=True,
        account_no="12345678",
        symbol="LABU",
        investment_usd=50000,
        total_tiers=55,
        base_price=208.44,
        refresh_base_price=True,
        buy_blocked=False,
        sell_blocked=False,
        tiers=tiers,
    )


if __name__ == "__main__":
    unittest.main()

