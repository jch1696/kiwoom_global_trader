from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GoogleConfig:
    spreadsheet_id: str = ""
    credential_file: str = "credentials.json"
    order_sheet_mode: str = "all_tabs"
    exclude_sheets: list[str] = field(default_factory=lambda: ["정산", "README", "설명", "템플릿", "TEMPLATE"])
    settlement_sheet_name: str = "정산"
    refresh_interval_sec: int = 600
    local_workbook_path: str = "../LINED/program.xlsx"
    public_csv_tabs: dict[str, str] = field(default_factory=dict)
    public_xlsx_url: str = ""
    allow_insecure_ssl: bool = False


@dataclass(frozen=True)
class TradingConfig:
    loop_interval_sec: int = 120
    max_retry: int = 3
    strategy_timeout_sec: int = 30
    dry_run: bool = True
    dry_run_fill_order: bool = False
    order_mode: str = "limit"
    orders_per_side: int = 1
    price_tolerance: float = 0.01
    price_tolerance_sub_dollar: float = 0.0001
    partial_fill_cooldown_sec: int = 10
    post_order_confirm_wait_sec: int = 2
    post_cancel_confirm_wait_sec: int = 2
    rebalance_enabled: bool = False
    rebalance_qty_tolerance: int = 0


@dataclass(frozen=True)
class SettlementConfig:
    enabled: bool = True
    run_time_kst: str = "06:10"
    session_mode: str = "regular_only"
    once_per_day: bool = True
    state_file: str = "data/state.json"


@dataclass(frozen=True)
class BrokerConfig:
    adapter: str = "kiwoom_hybrid"
    account_check: bool = True
    account_dropdown_order: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NotifyConfig:
    telegram_enabled: bool = False
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str = "TELEGRAM_CHAT_ID"
    telegram_send_orders: bool = True
    telegram_send_cancels: bool = True
    telegram_send_failures: bool = True
    telegram_send_keepalive: bool = False
    telegram_force_ipv4: bool = True
    telegram_allow_insecure_ssl: bool = False


@dataclass(frozen=True)
class AppConfig:
    google: GoogleConfig = field(default_factory=GoogleConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    settlement: SettlementConfig = field(default_factory=SettlementConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def _section(cls: type, data: dict[str, Any]) -> Any:
    allowed = cls.__dataclass_fields__.keys()
    return cls(**{k: v for k, v in data.items() if k in allowed})


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return AppConfig(
        google=_section(GoogleConfig, data.get("google", {})),
        trading=_section(TradingConfig, data.get("trading", {})),
        settlement=_section(SettlementConfig, data.get("settlement", {})),
        broker=_section(BrokerConfig, data.get("broker", {})),
        notify=_section(NotifyConfig, data.get("notify", {})),
    )
