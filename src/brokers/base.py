from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Balance, CancelResult, Fill, OpenOrder, OrderRequest, OrderResult


class BrokerAdapter(ABC):
    @abstractmethod
    def list_accounts(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def get_balance(self, account_no: str, symbol: str) -> Balance:
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self, account_no: str, symbol: str) -> list[OpenOrder]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, account_no: str, order_id: str) -> CancelResult:
        raise NotImplementedError

    @abstractmethod
    def get_daily_fills(self, account_no: str, symbol: str, trade_date: str) -> list[Fill]:
        raise NotImplementedError

