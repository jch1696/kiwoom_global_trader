from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Tier:
    tier_no: int
    target_qty: int
    tier_amount: float
    avg_price: float
    buy_price: float
    buy_qty: int
    sell_price: float
    sell_qty: int


@dataclass
class Strategy:
    sheet_name: str
    enabled: bool
    account_no: str
    symbol: str
    investment_usd: float
    total_tiers: int
    base_price: float
    refresh_base_price: bool
    buy_blocked: bool
    sell_blocked: bool
    tiers: list[Tier] = field(default_factory=list)
    disabled_reason: str | None = None


@dataclass(frozen=True)
class Balance:
    account_no: str
    symbol: str
    qty: int
    available_qty: int
    avg_price: float
    current_price: float
    valuation: float
    pnl: float
    fetched_at: datetime


@dataclass(frozen=True)
class OrderRequest:
    account_no: str
    symbol: str
    side: Side
    price: float
    qty: int
    order_type: str
    sheet_name: str = ""
    tier_no: int | None = None


@dataclass(frozen=True)
class OpenOrder:
    order_id: str
    account_no: str
    symbol: str
    side: Side
    price: float
    original_qty: int
    remaining_qty: int
    status: str
    submitted_at: datetime | None
    fetched_at: datetime


@dataclass(frozen=True)
class Fill:
    order_id: str
    account_no: str
    symbol: str
    side: Side
    filled_price: float
    filled_qty: int
    filled_at: datetime


@dataclass(frozen=True)
class OrderResult:
    success: bool
    order_id: str | None
    message: str


@dataclass(frozen=True)
class CancelResult:
    success: bool
    message: str


@dataclass(frozen=True)
class TierDecision:
    current_tier: int
    buy_order: OrderRequest | None
    sell_order: OrderRequest | None


@dataclass(frozen=True)
class SettlementRecord:
    updated_at_kst: datetime
    trade_date_et: str
    sheet_name: str
    account_no: str
    symbol: str
    final_tier: int
    total_tiers: int
    final_qty: int
    investment_usd: float
    base_price: float
    valuation: float
    pnl: float
    next_buy: str
    next_sell: str
    buy_summary: str
    sell_summary: str
