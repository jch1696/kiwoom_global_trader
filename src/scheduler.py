from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from collections.abc import Iterable

from .config import AppConfig
from .csv_logger import CsvLogger
from .order_manager import OrderManager, StrategySyncResult
from .runtime_state import RuntimeState, StateStore
from .models import Strategy
from .sheet_reader import LocalWorkbookSheetReader


class Scheduler:
    def __init__(
        self,
        config: AppConfig,
        state_store: StateStore,
        sheet_reader: LocalWorkbookSheetReader,
        order_manager: OrderManager,
        logger: CsvLogger,
    ) -> None:
        self.config = config
        self.state_store = state_store
        self.sheet_reader = sheet_reader
        self.order_manager = order_manager
        self.logger = logger

    def run_once(self) -> list[StrategySyncResult]:
        state = self.state_store.load()
        if state.halted:
            raise RuntimeError(f"program is halted: {state.halt_reason}")

        disabled = state.disabled_strategies or {}
        results: list[StrategySyncResult] = []
        for strategy in self._read_strategies_for_cycle():
            if strategy.sheet_name in disabled:
                results.append(StrategySyncResult(strategy.sheet_name, success=False, skipped=True, strategy_disabled=True, message=disabled[strategy.sheet_name]))
                continue
            result = self._run_strategy_with_timeout(strategy)
            results.append(result)
            if result.halt_all:
                state.halted = True
                state.halt_reason = result.message or "halt requested"
                break
            if result.strategy_disabled:
                disabled[strategy.sheet_name] = result.message or "strategy disabled"

        state.disabled_strategies = disabled
        state.last_sheet_refresh_at = datetime.now().isoformat(timespec="seconds")
        state.mark_loop_success()
        self.state_store.save(state)
        return results

    def _read_strategies_for_cycle(self) -> Iterable[Strategy]:
        if not self.config.google.public_csv_tabs or not hasattr(self.sheet_reader, "read_strategy"):
            yield from self.sheet_reader.read_strategies()
            return

        excluded = set(self.config.google.exclude_sheets)
        for sheet_name in self.config.google.public_csv_tabs:
            if sheet_name in excluded:
                continue
            print(f"  [cycle] reading sheet {sheet_name}", flush=True)
            strategy = self.sheet_reader.read_strategy(sheet_name)
            if strategy is not None:
                yield strategy

    def run_forever(self) -> None:
        while True:
            results = self.run_once()
            if any(result.halt_all for result in results):
                return
            time.sleep(self.config.trading.loop_interval_sec)

    def _run_strategy_with_timeout(self, strategy) -> StrategySyncResult:
        result_queue: queue.Queue[StrategySyncResult | BaseException] = queue.Queue(maxsize=1)

        def _worker() -> None:
            try:
                result_queue.put(self.order_manager.sync_strategy(strategy))
            except BaseException as exc:
                result_queue.put(exc)

        worker = threading.Thread(target=_worker, name=f"strategy-{strategy.sheet_name}", daemon=True)
        worker.start()
        try:
            result = result_queue.get(timeout=self.config.trading.strategy_timeout_sec)
            if isinstance(result, BaseException):
                raise result
            return result
        except queue.Empty:
            message = f"strategy timeout after {self.config.trading.strategy_timeout_sec}s"
            self.logger.log_error(
                {
                    "timestamp": datetime.now(),
                    "module": "scheduler",
                    "account_no": strategy.account_no,
                    "symbol": strategy.symbol,
                    "error_type": "strategy_timeout",
                    "error_message": message,
                    "traceback": "",
                    "retry_count": "",
                    "strategy_disabled": False,
                    "telegram_sent": False,
                }
            )
            return StrategySyncResult(strategy.sheet_name, success=False, skipped=True, halt_all=True, message=message)
