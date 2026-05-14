from __future__ import annotations

import concurrent.futures
import io
import re
import zipfile
import csv
import ssl
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .config import GoogleConfig
from .models import Strategy, Tier

NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


class SheetReadError(Exception):
    pass


class LocalWorkbookSheetReader:
    def __init__(self, config: GoogleConfig, base_dir: str | Path) -> None:
        self.config = config
        self.base_dir = Path(base_dir)

    def read_strategies(self) -> list[Strategy]:
        workbook_path = Path(self.config.local_workbook_path)
        if str(workbook_path).strip() in {"", "."}:
            raise SheetReadError("no Google Sheet is connected yet. Open the console and save a sheet URL first.")
        if not workbook_path.is_absolute():
            workbook_path = (self.base_dir / workbook_path).resolve()
        sheets = _read_xlsx_cells(workbook_path)
        strategies: list[Strategy] = []
        for sheet_name, cells in sheets.items():
            if sheet_name in set(self.config.exclude_sheets):
                continue
            strategy = parse_strategy(sheet_name, cells)
            if strategy is not None:
                strategies.append(strategy)
        return strategies

    def read_strategy(self, sheet_name: str) -> Strategy | None:
        workbook_path = Path(self.config.local_workbook_path)
        if str(workbook_path).strip() in {"", "."}:
            raise SheetReadError("no Google Sheet is connected yet. Open the console and save a sheet URL first.")
        if not workbook_path.is_absolute():
            workbook_path = (self.base_dir / workbook_path).resolve()
        sheets = _read_xlsx_cells(workbook_path)
        cells = sheets.get(sheet_name)
        if cells is None or sheet_name in set(self.config.exclude_sheets):
            return None
        return parse_strategy(sheet_name, cells)


class PublicCsvSheetReader:
    """Reads published Google Sheet tabs through CSV export URLs.

    This supports public or link-accessible sheets. Private service-account
    access will be added later without changing the Strategy parser.
    """

    def __init__(self, config: GoogleConfig) -> None:
        self.config = config

    def read_strategies(self) -> list[Strategy]:
        strategies: list[Strategy] = []
        for sheet_name, url in self.config.public_csv_tabs.items():
            if sheet_name in set(self.config.exclude_sheets):
                continue
            cells = _read_csv_url_cells(url, allow_insecure_ssl=self.config.allow_insecure_ssl)
            strategy = parse_strategy(sheet_name, cells)
            if strategy is not None:
                strategies.append(strategy)
        return strategies

    def read_strategy(self, sheet_name: str) -> Strategy | None:
        if sheet_name in set(self.config.exclude_sheets):
            return None
        url = self.config.public_csv_tabs.get(sheet_name)
        if not url:
            return None
        cells = _read_csv_url_cells(url, allow_insecure_ssl=self.config.allow_insecure_ssl)
        return parse_strategy(sheet_name, cells)


class PublicXlsxSheetReader:
    """Reads all strategy tabs from a link-accessible Google Sheet xlsx export."""

    def __init__(self, config: GoogleConfig) -> None:
        self.config = config

    def read_strategies(self) -> list[Strategy]:
        sheets = _read_xlsx_url_cells(self.config.public_xlsx_url, self.config.allow_insecure_ssl)
        strategies: list[Strategy] = []
        for sheet_name, cells in sheets.items():
            if sheet_name in set(self.config.exclude_sheets):
                continue
            strategy = parse_strategy(sheet_name, cells)
            if strategy is not None:
                strategies.append(strategy)
        return strategies

    def read_strategy(self, sheet_name: str) -> Strategy | None:
        if sheet_name in set(self.config.exclude_sheets):
            return None
        sheets = _read_xlsx_url_cells(self.config.public_xlsx_url, self.config.allow_insecure_ssl)
        cells = sheets.get(sheet_name)
        if cells is None:
            return None
        return parse_strategy(sheet_name, cells)


def parse_strategy(sheet_name: str, cells: dict[str, Any]) -> Strategy | None:
    required = ["E6", "E8", "E10", "E12"]
    missing = [cell for cell in required if _blank(cells.get(cell))]
    if missing:
        return None

    try:
        account_no = _as_account(cells["E6"])
        symbol = str(cells["E8"]).strip().upper()
        investment_usd = _as_float(cells["E10"])
        total_tiers = int(_as_float(cells["E12"]))
        base_price = _as_float(cells.get("E14", 0))
        refresh_base_price = _as_bool(cells.get("E16", False))
        buy_blocked = _as_bool(cells.get("E18", False))
        sell_blocked = _as_bool(cells.get("E20", False))
        tiers = _parse_tiers(cells, total_tiers)
    except (TypeError, ValueError) as exc:
        raise SheetReadError(f"{sheet_name}: invalid strategy cells: {exc}") from exc

    _validate_tiers(sheet_name, tiers)
    return Strategy(
        sheet_name=sheet_name,
        enabled=True,
        account_no=account_no,
        symbol=symbol,
        investment_usd=investment_usd,
        total_tiers=total_tiers,
        base_price=base_price,
        refresh_base_price=refresh_base_price,
        buy_blocked=buy_blocked,
        sell_blocked=sell_blocked,
        tiers=tiers,
    )


def _read_xlsx_cells(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists() or not path.is_file():
        raise SheetReadError(f"workbook not found: {path}")
    with zipfile.ZipFile(path) as z:
        return _read_xlsx_zip_cells(z)


def _read_xlsx_url_cells(url: str, allow_insecure_ssl: bool = False) -> dict[str, dict[str, Any]]:
    content = _read_url_bytes(url, allow_insecure_ssl)
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return _read_xlsx_zip_cells(z)


def _read_xlsx_zip_cells(z: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
    shared = _read_shared_strings(z)
    workbook = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    result: dict[str, dict[str, Any]] = {}
    for sheet in workbook.find("a:sheets", NS):
        title = sheet.attrib["name"]
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = "xl/" + relmap[rid].lstrip("/")
        root = ET.fromstring(z.read(target))
        result[title] = _sheet_cells(root, shared)
    return result


def _read_csv_url_cells(url: str, allow_insecure_ssl: bool = False) -> dict[str, Any]:
    content = _read_url_text(url, allow_insecure_ssl)
    rows = list(csv.reader(content.splitlines()))
    cells: dict[str, Any] = {}
    for row_idx, row in enumerate(rows, start=1):
        for col_idx, value in enumerate(row, start=1):
            if value == "":
                continue
            cells[f"{_column_name(col_idx)}{row_idx}"] = _coerce_number(value)
    return cells


def _build_opener(allow_insecure_ssl: bool) -> urllib.request.OpenerDirector:
    """Build an opener that uses Windows system proxy settings."""
    # urllib reads http_proxy / https_proxy env vars, but an elevated (admin)
    # process may not inherit them. Read WinHTTP proxy from the registry instead.
    proxies: dict[str, str] = urllib.request.getproxies()
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if proxy_enable:
            proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if proxy_server and "=" not in proxy_server:
                proxies.setdefault("http", f"http://{proxy_server}")
                proxies.setdefault("https", f"http://{proxy_server}")
        winreg.CloseKey(key)
    except Exception:
        pass

    handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler(proxies)]
    if allow_insecure_ssl:
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    return urllib.request.build_opener(*handlers)


def _read_url_text(url: str, allow_insecure_ssl: bool) -> str:
    return _read_url_bytes(url, allow_insecure_ssl).decode("utf-8-sig")


def _read_url_bytes(url: str, allow_insecure_ssl: bool) -> bytes:
    # urlopen(timeout=N) is a *socket-level* timeout, not a total-request timeout.
    # Wrap in a thread so we can enforce a hard wall-clock limit.
    TOTAL_TIMEOUT = 20

    print(f"  [sheet] fetching {url[:60]}...", flush=True)

    def _fetch() -> bytes:
        opener = _build_opener(allow_insecure_ssl)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with opener.open(req, timeout=10) as response:
                return response.read()
        except urllib.error.URLError as exc:
            if not allow_insecure_ssl or "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                raise
            # already using unverified context via _build_opener; try plain urllib as fallback
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(url, timeout=10, context=context) as response:
                return response.read()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_fetch)
        try:
            result = future.result(timeout=TOTAL_TIMEOUT)
            print("  [sheet] ok", flush=True)
            return result
        except concurrent.futures.TimeoutError as exc:
            raise SheetReadError(
                f"Google Sheet fetch timed out after {TOTAL_TIMEOUT}s.\n"
                f"  URL: {url}\n"
                f"  Hint: 프로그램이 관리자 권한으로 실행 중이면 프록시 설정이 다를 수 있습니다."
            ) from exc


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _read_shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("a:si", NS):
        values.append("".join((t.text or "") for t in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    return values


def _sheet_cells(root: ET.Element, shared: list[str]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for cell in root.findall(".//a:sheetData/a:row/a:c", NS):
        ref = cell.attrib.get("r")
        if not ref:
            continue
        value_node = cell.find("a:v", NS)
        if value_node is None:
            continue
        value: Any = value_node.text or ""
        cell_type = cell.attrib.get("t")
        if cell_type == "s":
            value = shared[int(value)]
        elif cell_type == "b":
            value = value == "1"
        else:
            value = _coerce_number(value)
        cells[ref] = value
    return cells


def _parse_tiers(cells: dict[str, Any], total_tiers: int) -> list[Tier]:
    tiers: list[Tier] = []
    for row in range(5, 5 + total_tiers + 1):
        tier_no = _as_int(cells.get(f"V{row}", cells.get(f"O{row}", "")), default=None)
        if tier_no is None:
            continue
        tiers.append(
            Tier(
                tier_no=tier_no,
                target_qty=_as_int(cells.get(f"W{row}", 0)),
                tier_amount=_as_float(cells.get(f"X{row}", 0)),
                avg_price=_as_float(cells.get(f"Y{row}", 0)),
                buy_price=_as_float(cells.get(f"Z{row}", 0)),
                buy_qty=_as_int(cells.get(f"AA{row}", 0)),
                sell_price=_as_float(cells.get(f"AB{row}", 0)),
                sell_qty=_as_int(cells.get(f"AC{row}", 0)),
            )
        )
    return tiers


def _validate_tiers(sheet_name: str, tiers: list[Tier]) -> None:
    if not tiers:
        raise SheetReadError(f"{sheet_name}: no tiers")
    ordered = sorted(tiers, key=lambda t: t.tier_no)
    targets = [t.target_qty for t in ordered]
    if len(set(t.tier_no for t in ordered)) != len(ordered):
        raise SheetReadError(f"{sheet_name}: duplicate tier numbers")
    if any(b < a for a, b in zip(targets, targets[1:])):
        raise SheetReadError(f"{sheet_name}: target_qty must be non-decreasing")


def _coerce_number(value: str) -> Any:
    if value in {"", "None"}:
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return int(number)
    return number


def _as_account(value: Any) -> str:
    if isinstance(value, float):
        return str(int(value))
    return re.sub(r"\.0$", "", str(value).strip())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() == "TRUE"


def _as_float(value: Any) -> float:
    if _blank(value) or str(value).startswith("#"):
        return 0.0
    return float(value)


def _as_int(value: Any, default: int | None = 0) -> int | None:
    if _blank(value) or str(value).startswith("#"):
        return default
    return int(round(float(value)))


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""
