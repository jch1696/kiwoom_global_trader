from __future__ import annotations

from .base import BrokerAdapter


class KiwoomScreenBroker(BrokerAdapter):
    """Restricted screen-automation adapter placeholder."""

    def _not_ready(self):
        raise NotImplementedError("Screen automation adapter requires region/template verification first.")

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

