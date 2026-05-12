from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RuntimeState:
    last_settlement_date: str | None = None
    last_sheet_refresh_at: str | None = None
    last_successful_loop_at: str | None = None
    halted: bool = False
    halt_reason: str | None = None
    disabled_strategies: dict[str, str] | None = None

    def mark_loop_success(self) -> None:
        self.last_successful_loop_at = datetime.now().isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> RuntimeState:
        if not self.path.exists():
            state = RuntimeState(disabled_strategies={})
            self.save(state)
            return state
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("disabled_strategies") is None:
            data["disabled_strategies"] = {}
        return RuntimeState(**data)

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")

    def reset_halt(self) -> RuntimeState:
        state = self.load()
        state.halted = False
        state.halt_reason = None
        self.save(state)
        return state

    def reset_strategy(self, sheet_name: str) -> RuntimeState:
        state = self.load()
        disabled = state.disabled_strategies or {}
        disabled.pop(sheet_name, None)
        state.disabled_strategies = disabled
        self.save(state)
        return state

