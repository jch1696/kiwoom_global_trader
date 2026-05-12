from __future__ import annotations

from .config import TradingConfig
from .models import Balance, OrderRequest, Side, Strategy, Tier, TierDecision


class TierEngine:
    def __init__(self, trading_config: TradingConfig) -> None:
        self.trading_config = trading_config

    def decide(self, strategy: Strategy, balance: Balance) -> TierDecision:
        tiers = sorted(strategy.tiers, key=lambda t: t.tier_no)
        current_tier = self.current_tier(tiers, balance.qty)
        buy_order = self._buy_candidate(strategy, tiers, current_tier)
        sell_order = self._sell_candidate(strategy, tiers, current_tier)
        return TierDecision(current_tier=current_tier, buy_order=buy_order, sell_order=sell_order)

    @staticmethod
    def current_tier(tiers: list[Tier], current_qty: int) -> int:
        ordered = sorted(tiers, key=lambda t: t.tier_no)
        positive = [t for t in ordered if t.tier_no > 0]
        if not positive:
            return 0
        if current_qty < positive[0].target_qty:
            return 0
        if current_qty >= positive[-1].target_qty:
            return positive[-1].tier_no

        best = positive[0]
        best_dist = abs(current_qty - best.target_qty)
        for tier in positive[1:]:
            dist = abs(current_qty - tier.target_qty)
            if dist < best_dist:
                best = tier
                best_dist = dist
            elif dist == best_dist and tier.tier_no < best.tier_no:
                best = tier
        return best.tier_no

    def price_tolerance(self, price: float) -> float:
        if price < 1.0:
            return self.trading_config.price_tolerance_sub_dollar
        return self.trading_config.price_tolerance

    def _buy_candidate(self, strategy: Strategy, tiers: list[Tier], current_tier: int) -> OrderRequest | None:
        if strategy.buy_blocked:
            return None
        candidate = self._tier_by_no(tiers, current_tier + 1)
        if candidate is None or candidate.buy_price <= 0 or candidate.buy_qty <= 0:
            return None
        return OrderRequest(
            account_no=strategy.account_no,
            symbol=strategy.symbol,
            side=Side.BUY,
            price=candidate.buy_price,
            qty=candidate.buy_qty,
            order_type=self.trading_config.order_mode,
            sheet_name=strategy.sheet_name,
            tier_no=candidate.tier_no,
        )

    def _sell_candidate(self, strategy: Strategy, tiers: list[Tier], current_tier: int) -> OrderRequest | None:
        if strategy.sell_blocked or current_tier <= 0:
            return None
        candidate = self._tier_by_no(tiers, current_tier)
        if candidate is None or candidate.sell_price <= 0 or candidate.sell_qty <= 0:
            return None
        return OrderRequest(
            account_no=strategy.account_no,
            symbol=strategy.symbol,
            side=Side.SELL,
            price=candidate.sell_price,
            qty=candidate.sell_qty,
            order_type=self.trading_config.order_mode,
            sheet_name=strategy.sheet_name,
            tier_no=candidate.tier_no,
        )

    @staticmethod
    def _tier_by_no(tiers: list[Tier], tier_no: int) -> Tier | None:
        for tier in tiers:
            if tier.tier_no == tier_no:
                return tier
        return None

    def tier_by_no(self, tiers: list[Tier], tier_no: int) -> Tier | None:
        return self._tier_by_no(tiers, tier_no)
