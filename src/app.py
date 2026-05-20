from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .brokers.kiwoom_hybrid import KiwoomHybridBroker
from .brokers.simulated import SimulatedBroker
from .config import AppConfig
from .csv_logger import CsvLogger
from .google_sheet_writer import build_google_program_info_writer, build_google_settlement_writer
from .models import OpenOrder
from .notifier import NullNotifier, TelegramNotifier
from .order_manager import OrderManager
from .runtime_state import StateStore
from .scheduler import Scheduler
from .sheet_reader import LocalWorkbookSheetReader, PublicCsvSheetReader, PublicXlsxSheetReader
from .settlement_writer import SettlementWriter
from .tier_engine import TierEngine


def build_scheduler(
    config: AppConfig,
    config_path: str | Path,
    force_dry_run: bool = False,
    dry_run_fill_order: bool = False,
    simulate_balances: dict[str, int] | None = None,
    simulate_current_prices: dict[str, float] | None = None,
    simulate_open_orders: list[OpenOrder] | None = None,
) -> Scheduler:
    if force_dry_run:
        config = replace(config, trading=replace(config.trading, dry_run=True))
    if dry_run_fill_order:
        config = replace(config, trading=replace(config.trading, dry_run=True, dry_run_fill_order=True))
    base_dir = Path(config_path).resolve().parent
    logger = CsvLogger(base_dir / "logs")
    notifier = TelegramNotifier(config.notify) if config.notify.telegram_enabled else NullNotifier()
    broker = (
        SimulatedBroker(simulate_balances, current_prices=simulate_current_prices, open_orders=simulate_open_orders)
        if simulate_balances is not None or simulate_current_prices is not None or simulate_open_orders is not None
        else KiwoomHybridBroker(config.broker.account_dropdown_order)
    )
    tier_engine = TierEngine(config.trading)
    program_info_writer = build_google_program_info_writer(config.google, base_dir)
    order_manager = OrderManager(broker, tier_engine, config.trading, config.notify, logger, notifier, program_info_writer)
    if config.google.public_csv_tabs:
        sheet_reader = PublicCsvSheetReader(config.google)
    elif config.google.public_xlsx_url:
        sheet_reader = PublicXlsxSheetReader(config.google)
    else:
        sheet_reader = LocalWorkbookSheetReader(config.google, base_dir)
    state_store = StateStore(base_dir / config.settlement.state_file)
    scheduler = Scheduler(config, state_store, sheet_reader, order_manager, logger)
    scheduler.base_dir = base_dir
    return scheduler


def build_settlement_writer(scheduler: Scheduler) -> SettlementWriter:
    base_dir = getattr(scheduler, "base_dir", Path("."))
    sheet_writer = build_google_settlement_writer(scheduler.config.google, base_dir)
    return SettlementWriter(
        scheduler.order_manager.broker,
        scheduler.order_manager.tier_engine,
        scheduler.logger,
        scheduler.order_manager.notifier,
        sheet_writer,
    )


def startup_validation(scheduler: Scheduler) -> None:
    strategies = scheduler.sheet_reader.read_strategies()
    if not strategies:
        raise RuntimeError("no valid strategy sheets found")
    for strategy in strategies:
        if not strategy.account_no or not strategy.symbol:
            raise RuntimeError(f"{strategy.sheet_name}: missing account or symbol")
        if not strategy.tiers:
            raise RuntimeError(f"{strategy.sheet_name}: no tiers")
