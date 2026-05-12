from __future__ import annotations

import unittest
from unittest.mock import patch

from types import SimpleNamespace

from src.main import (
    _account_for_symbol_from_strategies,
    _parse_probe_place_order,
    _parse_simulate_current_prices,
    _parse_simulate_open_orders,
    parse_args,
)
from src.models import Side


class CliParsingTest(unittest.TestCase):
    def test_open_order_with_remaining_qty(self) -> None:
        orders = _parse_simulate_open_orders(["LABU:buy:92.23:13:8"], {"LABU": 34})
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].side, Side.BUY)
        self.assertEqual(orders[0].original_qty, 13)
        self.assertEqual(orders[0].remaining_qty, 8)

    def test_parse_simulate_current_price(self) -> None:
        prices = _parse_simulate_current_prices(["23456789:SOXL=130.40", "LABU=173.62"])
        self.assertEqual(prices["23456789:SOXL"], 130.40)
        self.assertEqual(prices["LABU"], 173.62)

    def test_rejects_remaining_greater_than_original(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_simulate_open_orders(["LABU:buy:92.23:8:13"], {"LABU": 34})

    def test_probe_balance_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-balance", "--only-sheet", "SOXL"]):
            args = parse_args()
        self.assertTrue(args.probe_balance)
        self.assertEqual(args.only_sheet, ["SOXL"])

    def test_probe_open_orders_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-open-orders", "--only-sheet", "LABU"]):
            args = parse_args()
        self.assertTrue(args.probe_open_orders)
        self.assertEqual(args.only_sheet, ["LABU"])

    def test_probe_cancel_controls_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-cancel-controls"]):
            args = parse_args()
        self.assertTrue(args.probe_cancel_controls)

    def test_probe_cancel_order_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-cancel-order", "13353"]):
            args = parse_args()
        self.assertEqual(args.probe_cancel_order, "13353")

    def test_probe_place_controls_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-place-controls"]):
            args = parse_args()
        self.assertTrue(args.probe_place_controls)

    def test_probe_place_order_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-place-order", "LABU:buy:180.88:6"]):
            args = parse_args()
        self.assertEqual(args.probe_place_order, "LABU:buy:180.88:6")

    def test_probe_place_order_fill_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-place-order-fill", "LABU:buy:180.88:6"]):
            args = parse_args()
        self.assertEqual(args.probe_place_order_fill, "LABU:buy:180.88:6")

    def test_probe_decision_order_fill_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-decision-order-fill", "buy"]):
            args = parse_args()
        self.assertEqual(args.probe_decision_order_fill, "buy")

    def test_reset_mini_order_window_flag(self) -> None:
        with patch("sys.argv", ["prog", "--reset-mini-order-window"]):
            args = parse_args()
        self.assertTrue(args.reset_mini_order_window)

    def test_dry_run_fill_order_flag(self) -> None:
        with patch("sys.argv", ["prog", "--once", "--dry-run-fill-order"]):
            args = parse_args()
        self.assertTrue(args.dry_run_fill_order)

    def test_probe_main_toolbar_flag(self) -> None:
        with patch("sys.argv", ["prog", "--probe-main-toolbar"]):
            args = parse_args()
        self.assertTrue(args.probe_main_toolbar)

    def test_mini_order_point_flag(self) -> None:
        with patch("sys.argv", ["prog", "--mini-order-point", "760,62"]):
            args = parse_args()
        self.assertEqual(args.mini_order_point, "760,62")

    def test_mouse_position_flag(self) -> None:
        with patch("sys.argv", ["prog", "--mouse-position"]):
            args = parse_args()
        self.assertTrue(args.mouse_position)

    def test_place_order_flag(self) -> None:
        with patch("sys.argv", ["prog", "--place-order", "LABU:buy:180.88:6"]):
            args = parse_args()
        self.assertEqual(args.place_order, "LABU:buy:180.88:6")

    def test_place_decision_order_flag(self) -> None:
        with patch("sys.argv", ["prog", "--place-decision-order", "sell"]):
            args = parse_args()
        self.assertEqual(args.place_decision_order, "sell")

    def test_cancel_order_flag(self) -> None:
        with patch("sys.argv", ["prog", "--cancel-order", "13353"]):
            args = parse_args()
        self.assertEqual(args.cancel_order, "13353")

    def test_parse_probe_place_order_defaults_order_type(self) -> None:
        order = _parse_probe_place_order("LABU:buy:180.88:6")
        self.assertEqual(order.symbol, "LABU")
        self.assertEqual(order.side, Side.BUY)
        self.assertEqual(order.price, 180.88)
        self.assertEqual(order.qty, 6)
        self.assertEqual(order.order_type, "지정가")

    def test_parse_probe_place_order_uses_strategy_account(self) -> None:
        strategies = [SimpleNamespace(account_no="12345678", symbol="LABU")]
        order = _parse_probe_place_order("LABU:buy:180.88:6", strategies)
        self.assertEqual(order.account_no, "12345678")

    def test_account_for_symbol_from_strategies_returns_none_on_multiple_matches(self) -> None:
        strategies = [
            SimpleNamespace(account_no="12345678", symbol="LABU"),
            SimpleNamespace(account_no="12345678", symbol="LABU"),
        ]
        self.assertIsNone(_account_for_symbol_from_strategies("LABU", strategies))


if __name__ == "__main__":
    unittest.main()

