from __future__ import annotations

import unittest
from datetime import datetime

from src.brokers.simulated import SimulatedBroker
from src.models import OpenOrder, Side


class SimulatedBrokerTest(unittest.TestCase):
    def test_symbol_balance(self) -> None:
        broker = SimulatedBroker({"LABU": 29})
        balance = broker.get_balance("123", "LABU")
        self.assertEqual(balance.qty, 29)

    def test_account_symbol_balance(self) -> None:
        broker = SimulatedBroker({"123:LABU": 86})
        balance = broker.get_balance("123", "LABU")
        self.assertEqual(balance.qty, 86)

    def test_open_orders_filtered(self) -> None:
        order = OpenOrder("1", "123", "LABU", Side.BUY, 10.0, 1, 1, "open", None, datetime.now())
        broker = SimulatedBroker(open_orders=[order])
        self.assertEqual(broker.get_open_orders("123", "LABU"), [order])
        self.assertEqual(broker.get_open_orders("123", "SOXL"), [])


if __name__ == "__main__":
    unittest.main()
