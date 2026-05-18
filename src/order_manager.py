from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, TypeVar

from .brokers.base import BrokerAdapter
from .config import NotifyConfig, TradingConfig
from .csv_logger import CsvLogger
from .models import OpenOrder, OrderRequest, OrderResult, Side, Strategy, Tier
from .notifier import NullNotifier, TelegramNotifier
from .tier_engine import TierEngine

T = TypeVar("T")


@dataclass
class StrategySyncResult:
    sheet_name: str
    success: bool
    skipped: bool = False
    strategy_disabled: bool = False
    halt_all: bool = False
    message: str = ""
    current_tier: int | None = None
    actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TargetOrderPlan:
    order: OrderRequest | None
    action: str = ""
    block_reason: str = ""


class OrderManager:
    def __init__(
        self,
        broker: BrokerAdapter,
        tier_engine: TierEngine,
        trading_config: TradingConfig,
        notify_config: NotifyConfig,
        logger: CsvLogger,
        notifier: TelegramNotifier | NullNotifier,
    ) -> None:
        self.broker = broker
        self.tier_engine = tier_engine
        self.trading_config = trading_config
        self.notify_config = notify_config
        self.logger = logger
        self.notifier = notifier
        self.fail_counts: dict[str, int] = {}
        self.last_halt_reason: str | None = None

    def sync_strategy(self, strategy: Strategy) -> StrategySyncResult:
        self.last_halt_reason = None
        if hasattr(self.broker, "reset_trading_windows"):
            try:
                self.broker.reset_trading_windows()
            except Exception as exc:
                self._record_failure(strategy, "window reset failure", exc)
                return self._failure_result(strategy, exc)
        # 1. 실시간잔고 창 열기 → 잔고 읽기 → 창 닫기
        try:
            balance = self._retry(lambda: self.broker.get_balance(strategy.account_no, strategy.symbol), "get_balance")
        except Exception as exc:
            self._record_failure(strategy, "잔고 조회 실패", exc)
            return self._failure_result(strategy, exc)

        # 2. 티어 계산
        decision = self.tier_engine.decide(strategy, balance)
        result = StrategySyncResult(
            sheet_name=strategy.sheet_name,
            success=True,
            current_tier=decision.current_tier,
        )
        plan = self.target_order_plan(strategy, balance, decision.current_tier, decision.buy_order, decision.sell_order)
        if plan.action:
            result.actions.append(plan.action)
        if plan.block_reason:
            result.success = False
            result.skipped = True
            result.message = plan.block_reason
            return result

        # 3. 미체결 창 열기 → 기존 주문 목록 읽기 → 창 닫기
        try:
            open_orders = self._retry(lambda: self.broker.get_open_orders(strategy.account_no, strategy.symbol), "get_open_orders")
        except Exception as exc:
            self._record_failure(strategy, "미체결 조회 실패", exc)
            return self._failure_result(strategy, exc)

        target_order = plan.order

        # 4. 한 번에 하나의 주문만 관리한다.
        # 현재가에서 더 가까운 목표 주문과 같은 미체결은 유지하고, 나머지는 취소한다.
        keep_target = False
        failed = False
        for order in open_orders:
            if target_order is not None and not keep_target and self._same_order(target_order, order):
                keep_target = True
                result.actions.append(f"keep:{order.side.value}")
                self._log_existing_order(strategy, decision.current_tier, order, "keep_open_order", "existing order matches target")
                continue
            if order.remaining_qty < order.original_qty:
                result.actions.append(f"partial:{order.side.value}:{order.order_id}:{order.original_qty}->{order.remaining_qty}")
                self._log_existing_order(strategy, decision.current_tier, order, "partial_open_order", "partial fill inferred")
            result.actions.append(f"cancel:{order.side.value}:{order.order_id}")
            if not self._cancel(strategy, order):
                failed = True

        # 5. 유지한 주문이 없으면 더 가까운 목표 주문 하나만 접수한다.
        placed_order: OrderRequest | None = None
        if target_order is not None and not keep_target and not failed:
            result.actions.append(f"place:{target_order.side.value}:{target_order.price}:{target_order.qty}")
            if not self._place(strategy, decision.current_tier, target_order):
                failed = True
            else:
                placed_order = target_order

        if failed:
            result.success = False
            result.skipped = True
            if self.last_halt_reason:
                result.halt_all = True
                result.message = self.last_halt_reason
            else:
                result.message = "one or more order actions failed"
        else:
            self.fail_counts[strategy.sheet_name] = 0
            if placed_order is not None and not self.trading_config.dry_run and self.notify_config.telegram_send_orders:
                self._notify(
                    self._live_order_summary_message(
                        strategy,
                        balance.current_price,
                        decision.current_tier,
                        decision.buy_order,
                        decision.sell_order,
                        placed_order,
                    )
                )
        return result

    def target_order_plan(
        self,
        strategy: Strategy,
        balance,
        current_tier: int,
        buy_order: OrderRequest | None,
        sell_order: OrderRequest | None,
    ) -> TargetOrderPlan:
        rebalance = self._rebalance_order(strategy, balance, current_tier)
        if rebalance.block_reason or rebalance.order is not None:
            return rebalance
        return TargetOrderPlan(self._nearest_order(balance.current_price, buy_order, sell_order))

    def _rebalance_order(self, strategy: Strategy, balance, current_tier: int) -> TargetOrderPlan:
        if not self.trading_config.rebalance_enabled:
            return TargetOrderPlan(None)
        target = self._rebalance_target_tier(strategy, balance)
        if target is None:
            return TargetOrderPlan(None)
        tier, price_tier = target
        diff = tier.target_qty - balance.qty
        tolerance = max(0, int(self.trading_config.rebalance_qty_tolerance))
        if abs(diff) <= tolerance:
            return TargetOrderPlan(None)
        side = Side.BUY if diff > 0 else Side.SELL
        action = f"rebalance:{side.value}:{balance.qty}->{tier.target_qty}"
        if side == Side.BUY and strategy.buy_blocked:
            return TargetOrderPlan(None, action, "rebalance buy needed but buy is blocked")
        if side == Side.SELL and strategy.sell_blocked:
            return TargetOrderPlan(None, action, "rebalance sell needed but sell is blocked")
        price = price_tier.buy_price if side == Side.BUY else price_tier.sell_price
        if price <= 0:
            price = balance.current_price
        if price <= 0:
            return TargetOrderPlan(None, action, "rebalance needed but no usable price")
        return TargetOrderPlan(
            OrderRequest(
                account_no=strategy.account_no,
                symbol=strategy.symbol,
                side=side,
                price=price,
                qty=abs(diff),
                order_type=self.trading_config.order_mode,
                sheet_name=strategy.sheet_name,
                tier_no=tier.tier_no,
            ),
            action,
        )

    def _rebalance_target_tier(self, strategy: Strategy, balance) -> tuple[Tier, Tier] | None:
        tiers = sorted([tier for tier in strategy.tiers if tier.tier_no > 0], key=lambda tier: tier.target_qty)
        if not tiers:
            return None
        tolerance = max(0, int(self.trading_config.rebalance_qty_tolerance))
        for tier in tiers:
            if abs(tier.target_qty - balance.qty) <= tolerance:
                return None

        lower = None
        upper = None
        for tier in tiers:
            if tier.target_qty < balance.qty:
                lower = tier
            elif tier.target_qty > balance.qty:
                upper = tier
                break

        if lower is None:
            return (upper, upper) if upper is not None else None
        if upper is None:
            return lower, lower

        buy_gap = upper.target_qty - balance.qty
        sell_gap = balance.qty - lower.target_qty
        buy_price = upper.buy_price
        sell_price = upper.sell_price if upper.sell_price > 0 else lower.sell_price

        if buy_price > 0 and balance.current_price <= buy_price:
            return upper, upper
        if sell_price > 0 and balance.current_price >= sell_price:
            return lower, upper
        if buy_price <= 0:
            return lower, upper
        if sell_price <= 0:
            return upper, upper

        buy_distance = abs(balance.current_price - buy_price)
        sell_distance = abs(balance.current_price - sell_price)
        if sell_distance < buy_distance:
            return lower, upper
        if buy_distance < sell_distance:
            return upper, upper
        return (lower, upper) if sell_gap <= buy_gap else (upper, upper)

    def _place(self, strategy: Strategy, current_tier: int, order: OrderRequest) -> bool:
        if self.trading_config.dry_run:
            if self.trading_config.dry_run_fill_order:
                return self._fill_order_form_for_dry_run(strategy, current_tier, order)
            self._log_order(strategy, current_tier, order, "dry_run_place", "success", "dry-run", False)
            return True

        try:
            result = self._retry(lambda: self.broker.place_order(order), "place_order")
        except Exception as exc:
            self._record_failure(strategy, "주문 실패", exc)
            return False

        notify_sent = False
        if result.success and self.notify_config.telegram_send_orders:
            notify_sent = True
        elif not result.success and self.notify_config.telegram_send_failures:
            notify_sent = self._notify(f"[주문 실패]\n시트: {strategy.sheet_name}\n종목: {strategy.symbol}\n사유: {result.message}").sent
        self._log_order(strategy, current_tier, order, "place", "success" if result.success else "failed", result.message, notify_sent, result.order_id)
        if not result.success and self._is_order_unavailable_message(result.message):
            self.last_halt_reason = result.message
        return result.success

    def _fill_order_form_for_dry_run(self, strategy: Strategy, current_tier: int, order: OrderRequest) -> bool:
        if not hasattr(self.broker, "probe_place_order"):
            self._log_order(strategy, current_tier, order, "dry_run_fill_order", "failed", "broker does not support order form probe")
            return False
        try:
            result = self._retry(lambda: self.broker.probe_place_order(order, execute=True), "probe_place_order")
        except Exception as exc:
            self._record_failure(strategy, "order form dry-run fill failed", exc)
            return False

        ok = result.get("ok") == "true" and result.get("executed") == "true"
        message = result.get("message", "") or str(result)
        self._log_order(
            strategy,
            current_tier,
            order,
            "dry_run_fill_order",
            "success" if ok else "failed",
            message,
            False,
        )
        return ok

    @staticmethod
    def _is_order_unavailable_message(message: str) -> bool:
        return "\uC8FC\uBB38\uBD88\uAC00\uB2A5" in message or "571563" in message

    def _failure_result(self, strategy: Strategy, exc: Exception) -> StrategySyncResult:
        message = str(exc)
        return StrategySyncResult(
            strategy.sheet_name,
            success=False,
            skipped=True,
            halt_all=self._is_order_unavailable_message(message),
            message=message,
        )

    def _cancel(self, strategy: Strategy, order: OpenOrder) -> bool:
        if self.trading_config.dry_run:
            self.logger.log_order(
                {
                    "timestamp": datetime.now(),
                    "sheet_name": strategy.sheet_name,
                    "account_no": strategy.account_no,
                    "symbol": strategy.symbol,
                    "current_qty": "",
                    "current_tier": "",
                    "action": "dry_run_cancel",
                    "side": order.side.value,
                    "price": order.price,
                    "qty": order.remaining_qty,
                    "order_id": order.order_id,
                    "result": "success",
                    "message": "dry-run",
                    "telegram_sent": False,
                }
            )
            return True

        try:
            result = self._retry(lambda: self.broker.cancel_order(strategy.account_no, order.order_id), "cancel_order")
        except Exception as exc:
            self._record_failure(strategy, "취소 실패", exc, disable=True)
            return False

        notify_sent = False
        if result.success and self.notify_config.telegram_send_cancels:
            time.sleep(self.trading_config.post_cancel_confirm_wait_sec)
        elif not result.success and self.notify_config.telegram_send_failures:
            notify_sent = self._notify(f"[취소 실패]\n시트: {strategy.sheet_name}\n종목: {strategy.symbol}\n주문번호: {order.order_id}\n사유: {result.message}").sent
        self.logger.log_order(
            {
                "timestamp": datetime.now(),
                "sheet_name": strategy.sheet_name,
                "account_no": strategy.account_no,
                "symbol": strategy.symbol,
                "current_qty": "",
                "current_tier": "",
                "action": "cancel",
                "side": order.side.value,
                "price": order.price,
                "qty": order.remaining_qty,
                "order_id": order.order_id,
                "result": "success" if result.success else "failed",
                "message": result.message,
                "telegram_sent": notify_sent,
            }
        )
        return result.success

    def _retry(self, fn: Callable[[], T], operation: str) -> T:
        last_exc: Exception | None = None
        for attempt in range(1, self.trading_config.max_retry + 1):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if attempt < self.trading_config.max_retry:
                    time.sleep(0.5)
        raise RuntimeError(f"{operation} failed after {self.trading_config.max_retry} attempts: {last_exc}") from last_exc

    def _same_order(self, target: OrderRequest, existing: OpenOrder) -> bool:
        if target.account_no != existing.account_no or target.symbol != existing.symbol or target.side != existing.side:
            return False
        tolerance = self.tier_engine.price_tolerance(target.price)
        return abs(target.price - existing.price) <= tolerance and target.qty == existing.remaining_qty

    @staticmethod
    def _nearest_order(
        current_price: float,
        buy_order: OrderRequest | None,
        sell_order: OrderRequest | None,
    ) -> OrderRequest | None:
        candidates = [order for order in [buy_order, sell_order] if order is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda order: abs(order.price - current_price))

    def _record_failure(self, strategy: Strategy, label: str, exc: Exception, disable: bool = False) -> None:
        count = self.fail_counts.get(strategy.sheet_name, 0) + 1
        self.fail_counts[strategy.sheet_name] = count
        strategy_disabled = disable or count > self.trading_config.max_retry
        notify_sent = False
        if self.notify_config.telegram_send_failures:
            notify_sent = self._notify(f"[{label}]\n시트: {strategy.sheet_name}\n종목: {strategy.symbol}\n횟수: {count}\n사유: {exc}").sent
        self.logger.log_error(
            {
                "timestamp": datetime.now(),
                "module": "order_manager",
                "account_no": strategy.account_no,
                "symbol": strategy.symbol,
                "error_type": label,
                "error_message": str(exc),
                "traceback": "",
                "retry_count": count,
                "strategy_disabled": strategy_disabled,
                "telegram_sent": notify_sent,
            }
        )

    def _notify(self, text: str):
        return self.notifier.send(text)

    def _live_order_summary_message(
        self,
        strategy: Strategy,
        current_price: float,
        current_tier: int,
        buy_order: OrderRequest | None,
        sell_order: OrderRequest | None,
        placed_order: OrderRequest,
    ) -> str:
        buy_count = 1 if placed_order.side == Side.BUY else 0
        sell_count = 1 if placed_order.side == Side.SELL else 0
        buy_price = f"{buy_order.price:.2f}" if buy_order is not None else "-"
        sell_price = f"{sell_order.price:.2f}" if sell_order is not None else "-"
        return (
            f"{strategy.sheet_name} {current_tier}/{strategy.total_tiers}L (Buy {buy_count} / Sell {sell_count})\n"
            f"{strategy.symbol} {current_price:.2f} ({buy_price} / {sell_price})\n"
            f"status: ORDER 1/{self.trading_config.orders_per_side} OK"
        )

    def _order_message(self, title: str, strategy: Strategy, current_tier: int, order: OrderRequest, order_id: str) -> str:
        return (
            f"{title}\n"
            f"시트: {strategy.sheet_name}\n"
            f"계좌: {strategy.account_no}\n"
            f"종목: {strategy.symbol}\n"
            f"현재 티어: {current_tier}\n"
            f"주문 티어: {order.tier_no}\n"
            f"구분: {order.side.value}\n"
            f"가격: {order.price}\n"
            f"수량: {order.qty}\n"
            f"주문번호: {order_id}\n"
            f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST"
        )

    def _log_order(
        self,
        strategy: Strategy,
        current_tier: int,
        order: OrderRequest,
        action: str,
        result: str,
        message: str,
        telegram_sent: bool,
        order_id: str | None = None,
    ) -> None:
        self.logger.log_order(
            {
                "timestamp": datetime.now(),
                "sheet_name": strategy.sheet_name,
                "account_no": strategy.account_no,
                "symbol": strategy.symbol,
                "current_qty": "",
                "current_tier": current_tier,
                "action": action,
                "side": order.side.value,
                "price": order.price,
                "qty": order.qty,
                "order_id": order_id or "",
                "result": result,
                "message": message,
                "telegram_sent": telegram_sent,
            }
        )

    def _log_existing_order(
        self,
        strategy: Strategy,
        current_tier: int,
        order: OpenOrder,
        action: str,
        message: str,
    ) -> None:
        self.logger.log_order(
            {
                "timestamp": datetime.now(),
                "sheet_name": strategy.sheet_name,
                "account_no": strategy.account_no,
                "symbol": strategy.symbol,
                "current_qty": "",
                "current_tier": current_tier,
                "action": action,
                "side": order.side.value,
                "price": order.price,
                "qty": order.remaining_qty,
                "order_id": order.order_id,
                "result": "info",
                "message": f"{message}; original_qty={order.original_qty}; remaining_qty={order.remaining_qty}",
                "telegram_sent": False,
            }
        )
