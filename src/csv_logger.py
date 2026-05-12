from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class CsvLogger:
    def __init__(self, log_dir: str | Path = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_order(self, row: dict[str, Any]) -> None:
        self._append("orders", row)

    def log_error(self, row: dict[str, Any]) -> None:
        self._append("errors", row)

    def log_settlement(self, row: dict[str, Any]) -> None:
        self._append("settlement", row)

    def _append(self, kind: str, row: dict[str, Any]) -> None:
        date = datetime.now().strftime("%Y%m%d")
        path = self.log_dir / f"{kind}_{date}.csv"
        clean = {k: self._format(v) for k, v in row.items()}
        write_header = not path.exists()
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(clean.keys()))
            if write_header:
                writer.writeheader()
            writer.writerow(clean)

    @staticmethod
    def _format(value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        return value

