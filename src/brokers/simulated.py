from __future__ import annotations

from datetime import datetime

from .base import BrokerAdapter
from ..models import Balance, CancelResult, Fill, OpenOrder, OrderRequest, OrderResult


class SimulatedBroker(BrokerAdapter):
    def __init__(
        self,
        balances: dict[str, int] | None = None,
        current_prices: dict[str, float] | None = None,
        open_orders: list[OpenOrder] | None = None,
        fills: list[Fill] | None = None,
    ) -> None:
        self.balances = {key.upper(): value for key, value in (balances or {}).items()}
        self.current_prices = {key.upper(): value for key, value in (current_prices or {}).items()}
        self.open_orders = open_orders or []
        self.fills = fills or []
        self.order_seq = 1

    def list_accounts(self) -> list[str]:
        return []

    def get_balance(self, account_no: str, symbol: str) -> Balance:
        qty = self.balances.get(symbol.upper(), self.balances.get(f"{account_no}:{symbol}".upper(), 0))
        current_price = self.current_prices.get(symbol.upper(), self.current_prices.get(f"{account_no}:{symbol}".upper(), 0.0))
        return Balance(
            account_no=account_no,
            symbol=symbol,
            qty=qty,
            available_qty=qty,
            avg_price=0.0,
            current_price=current_price,
            valuation=0.0,
            pnl=0.0,
            fetched_at=datetime.now(),
        )

    def get_open_orders(self, account_no: str, symbol: str) -> list[OpenOrder]:
        return [
            order
            for order in self.open_orders
            if order.account_no == account_no and order.symbol.upper() == symbol.upper()
        ]

    def place_order(self, order: OrderRequest) -> OrderResult:
        order_id = f"SIM{self.order_seq:06d}"
        self.order_seq += 1
        return OrderResult(True, order_id, "simulated order accepted")

    def cancel_order(self, account_no: str, order_id: str) -> CancelResult:
        before = len(self.open_orders)
        self.open_orders = [order for order in self.open_orders if not (order.account_no == account_no and order.order_id == order_id)]
        if len(self.open_orders) == before:
            return CancelResult(False, "simulated order not found")
        return CancelResult(True, "simulated order canceled")

    def get_daily_fills(self, account_no: str, symbol: str, trade_date: str) -> list[Fill]:
        return [
            fill
            for fill in self.fills
            if fill.account_no == account_no
            and fill.symbol.upper() == symbol.upper()
            and fill.filled_at.strftime("%Y-%m-%d") == trade_date
        ]
