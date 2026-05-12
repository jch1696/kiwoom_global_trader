from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .brokers.base import BrokerAdapter
from .csv_logger import CsvLogger
from .models import Fill, OrderRequest, SettlementRecord, Side, Strategy
from .notifier import NullNotifier, TelegramNotifier
from .tier_engine import TierEngine


class SettlementSheetWriter(Protocol):
    def append_records(self, records: list[SettlementRecord]) -> None:
        ...


class SettlementWriter:
    def __init__(
        self,
        broker: BrokerAdapter,
        tier_engine: TierEngine,
        logger: CsvLogger,
        notifier: TelegramNotifier | NullNotifier,
        sheet_writer: SettlementSheetWriter | None = None,
    ) -> None:
        self.broker = broker
        self.tier_engine = tier_engine
        self.logger = logger
        self.notifier = notifier
        self.sheet_writer = sheet_writer

    def settle(self, strategies: list[Strategy], trade_date_et: str | None = None, send_telegram: bool = True) -> list[SettlementRecord]:
        trade_date = trade_date_et or _today_et()
        records: list[SettlementRecord] = []
        for strategy in strategies:
            balance = self.broker.get_balance(strategy.account_no, strategy.symbol)
            fills = self.broker.get_daily_fills(strategy.account_no, strategy.symbol, trade_date)
            decision = self.tier_engine.decide(strategy, balance)
            record = SettlementRecord(
                updated_at_kst=datetime.now(),
                trade_date_et=trade_date,
                sheet_name=strategy.sheet_name,
                account_no=strategy.account_no,
                symbol=strategy.symbol,
                final_tier=decision.current_tier,
                total_tiers=strategy.total_tiers,
                final_qty=balance.qty,
                investment_usd=strategy.investment_usd,
                base_price=strategy.base_price,
                valuation=balance.valuation,
                pnl=balance.pnl,
                next_buy=_format_order(decision.buy_order),
                next_sell=_format_order(decision.sell_order),
                buy_summary=_format_fills(fills, Side.BUY),
                sell_summary=_format_fills(fills, Side.SELL),
            )
            self.logger.log_settlement({**asdict(record), "telegram_sent": False})
            records.append(record)

        if self.sheet_writer is not None:
            try:
                self.sheet_writer.append_records(records)
            except Exception as exc:
                self.logger.log_error(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "module": "settlement_sheet",
                        "error": str(exc),
                    }
                )
                if send_telegram:
                    self.notifier.send(f"[정산 구글시트 기록 실패]\n{exc}")

        if send_telegram:
            result = self.notifier.send(_settlement_message(records, trade_date))
            if result.sent:
                for record in records:
                    self.logger.log_settlement({**asdict(record), "telegram_sent": True})
        return records


def _format_order(order: OrderRequest | None) -> str:
    if order is None:
        return ""
    return f"{order.side.value} {order.price} x {order.qty}"


def _format_fills(fills: list[Fill], side: Side) -> str:
    selected = [fill for fill in fills if fill.side == side]
    if not selected:
        return "0건"
    qty = sum(fill.filled_qty for fill in selected)
    amount = sum(fill.filled_price * fill.filled_qty for fill in selected)
    avg = amount / qty if qty else 0
    return f"{len(selected)}건 / {qty}주 / 평균 {avg:.2f}"


def _settlement_message(records: list[SettlementRecord], trade_date: str) -> str:
    lines = [f"[일일 정산 완료]", f"거래일(ET): {trade_date}", f"전략 수: {len(records)}"]
    for record in records:
        lines.append(
            f"{record.sheet_name}: 티어 {record.final_tier}/{record.total_tiers}, "
            f"잔고 {record.final_qty}, 다음매수 {record.next_buy or '-'}, 다음매도 {record.next_sell or '-'}"
        )
    return "\n".join(lines)


def _today_et() -> str:
    # ET offset handling is intentionally simple for this offline stage.
    # HTS verification stage can replace this with zoneinfo('America/New_York').
    et_now = datetime.now(UTC) - timedelta(hours=5)
    return et_now.strftime("%Y-%m-%d")
