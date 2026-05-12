from __future__ import annotations

from .base import BrokerAdapter


class KiwoomUiaBroker(BrokerAdapter):
    """UI Automation adapter placeholder.

    Fill this after the 1st-stage HTS accessibility inspection decides which
    screens and controls are readable through UIA.
    """

    def __getattribute__(self, name: str):
        attr = super().__getattribute__(name)
        if name.startswith("_") or name in {"__class__", "__getattribute__"}:
            return attr
        return attr

    def _not_ready(self):
        raise NotImplementedError("Kiwoom UIA adapter requires HTS control inspection first.")

    def list_accounts(self):
        self._not_ready()

    def get_balance(self, account_no, symbol):
        self._not_ready()

    def get_open_orders(self, account_no, symbol):
        self._not_ready()

    def place_order(self, order):
        self._not_ready()

    def cancel_order(self, account_no, order_id):
        self._not_ready()

    def get_daily_fills(self, account_no, symbol, trade_date):
        self._not_ready()

