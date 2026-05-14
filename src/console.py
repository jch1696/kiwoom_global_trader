from __future__ import annotations

import argparse
import csv
import html as html_module
import json
import locale
import re
import shutil
import subprocess
import sys
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from .config import GoogleConfig, NotifyConfig, load_config
from .env_loader import load_env
from .notifier import TelegramNotifier
from .sheet_reader import PublicXlsxSheetReader, _read_url_text
from .updater import maybe_auto_update


MAX_LOG_LINES = 500
LIVE_VALIDATION_WINDOW_MIN = 30
DEFAULT_CONSOLE_SETTINGS = {
    "auto_schedule": {
        "start": "00:00",
        "end": "23:59",
        "interval_sec": 120,
        "live_order_enabled": False,
        "auto_start_enabled": False,
    },
    "auto_sheets": {
        "enabled": [],
    },
    "hts_auto": {
        "enabled": False,
        "exe_path": "",
        "launch_time": "22:40",
        "simple_pin": "",
    },
}


def app_base_dir() -> Path:
    """Return the directory that should hold user config/data files."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_config_file(config_path: str | Path) -> Path:
    path = Path(config_path)
    if path.is_absolute():
        return path
    return app_base_dir() / path


def ensure_config_file(config_path: str | Path) -> Path:
    path = resolve_config_file(config_path)
    if path.exists():
        return path
    example = app_base_dir() / "config.example.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if example.exists():
        shutil.copy2(example, path)
    else:
        path.write_text(
            json.dumps(
                {
                    "google": {
                        "spreadsheet_id": "",
                        "credential_file": "data/credentials.json",
                        "order_sheet_mode": "all_tabs",
                        "exclude_sheets": ["정산", "README", "설명", "템플릿", "TEMPLATE"],
                        "settlement_sheet_name": "정산",
                        "refresh_interval_sec": 600,
                        "local_workbook_path": "",
                        "allow_insecure_ssl": True,
                        "public_csv_tabs": {},
                        "public_xlsx_url": "",
                    },
                    "trading": {
                        "loop_interval_sec": 120,
                        "max_retry": 3,
                        "strategy_timeout_sec": 90,
                        "dry_run": True,
                        "order_mode": "limit",
                        "orders_per_side": 1,
                        "price_tolerance": 0.01,
                        "price_tolerance_sub_dollar": 0.0001,
                        "partial_fill_cooldown_sec": 10,
                        "post_order_confirm_wait_sec": 2,
                        "post_cancel_confirm_wait_sec": 2,
                        "rebalance_enabled": True,
                        "rebalance_qty_tolerance": 0,
                    },
                    "settlement": {
                        "enabled": True,
                        "run_time_kst": "07:10",
                        "session_mode": "regular_only",
                        "once_per_day": True,
                        "state_file": "data/state.json",
                    },
                    "broker": {
                        "adapter": "kiwoom_hybrid",
                        "account_check": True,
                        "account_dropdown_order": [],
                    },
                    "notify": {
                        "telegram_enabled": False,
                        "telegram_token_env": "TELEGRAM_BOT_TOKEN",
                        "telegram_chat_id_env": "TELEGRAM_CHAT_ID",
                        "telegram_send_orders": True,
                        "telegram_send_cancels": True,
                        "telegram_send_failures": True,
                        "telegram_send_keepalive": False,
                        "telegram_force_ipv4": True,
                        "telegram_allow_insecure_ssl": False,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return path


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def _console_encoding() -> str:
    return locale.getpreferredencoding(False) or "utf-8"


def console_notification_text(action: str, sheet_name: str | None, ok: bool, detail: str = "") -> str:
    status = "정상" if ok else "실패"
    sheet = sheet_name or "전체"
    label = action_label(action)
    lines = [f"[콘솔 {label} {status}]", f"시트: {sheet}"]
    if detail:
        lines.append(f"내용: {detail}")
    return "\n".join(lines)


@dataclass(frozen=True)
class ConsoleSheet:
    name: str
    source: str


@dataclass
class SheetValidationState:
    dry_run_at: datetime | None = None
    fill_order_at: datetime | None = None
    decision_side: str | None = None
    decision_action: str | None = None
    decision_has_cancel: bool = False


@dataclass
class SheetScheduleState:
    enabled: bool = True
    next_run_at: datetime | None = None


def live_unlock_status(
    today_open: bool,
    hts_connected_at: datetime | None,
    sheet_state: SheetValidationState | None,
    now: datetime | None = None,
    max_age_min: int = LIVE_VALIDATION_WINDOW_MIN,
) -> tuple[bool, str]:
    current = now or datetime.now()
    max_age = timedelta(minutes=max_age_min)
    if hts_connected_at is None:
        return False, "HTS 연결 확인 필요"
    if current - hts_connected_at > max_age:
        return False, "HTS 연결 확인 만료"
    if sheet_state is None or sheet_state.dry_run_at is None:
        return False, "dry-run 성공 필요"
    if current - sheet_state.dry_run_at > max_age:
        return False, "dry-run 검증 만료"
    if (
        sheet_state.decision_action == "keep"
        and sheet_state.decision_has_cancel
        and sheet_state.decision_side in {"buy", "sell"}
    ):
        return True, "실주문 조건 충족 / 기존 주문 정리"
    if sheet_state.fill_order_at is None:
        return False, "주문창 검증 필요"
    if current - sheet_state.fill_order_at > max_age:
        return False, "주문창 검증 만료"
    if sheet_state.decision_action == "keep":
        return False, "신규 주문 없음 / 기존 미체결 유지"
    if sheet_state.decision_action == "cancel":
        return False, "신규 주문 없음 / 취소만 필요"
    if sheet_state.decision_side not in {"buy", "sell"}:
        return False, "주문 방향 확인 필요"
    return True, "실주문 조건 충족"


def load_sheet_tabs(config_path: str | Path) -> list[ConsoleSheet]:
    path = resolve_config_file(config_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    tabs = data.get("google", {}).get("public_csv_tabs", {})
    if not isinstance(tabs, dict):
        return []
    if tabs:
        return [ConsoleSheet(name=str(name), source=str(url)) for name, url in tabs.items()]
    google = data.get("google", {})
    public_xlsx_url = str(google.get("public_xlsx_url", "")).strip()
    if public_xlsx_url:
        try:
            config = load_config(path)
            strategies = PublicXlsxSheetReader(config.google).read_strategies()
        except Exception:
            return []
        return [ConsoleSheet(name=str(strategy.sheet_name), source=public_xlsx_url) for strategy in strategies]
    return []


def extract_spreadsheet_id(value: str) -> str:
    text = value.strip()
    match = re.search(r"/spreadsheets/d/([^/?#]+)", text)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([^&#]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return text
    raise ValueError("구글시트 URL 또는 스프레드시트 ID를 확인할 수 없습니다")


def build_public_csv_url(spreadsheet_id: str, gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"


def build_public_xlsx_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"


def parse_google_sheet_tabs_from_html(spreadsheet_id: str, html: str) -> dict[str, str]:
    pairs: list[tuple[str, str]] = []
    patterns = [
        r'"sheetId"\s*:\s*"?(\d+)"?\s*,\s*"title"\s*:\s*"((?:\\.|[^"\\])*)"',
        r'"title"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"sheetId"\s*:\s*"?(\d+)"?',
        r'\[\s*"((?:\\.|[^"\\])*)"\s*,\s*(\d+)\s*,\s*\d+\s*,\s*"GRID"',
    ]
    sources = [html]
    unescaped_quotes = html.replace('\\"', '"')
    if unescaped_quotes != html:
        sources.append(unescaped_quotes)
    for source in sources:
        for idx, pattern in enumerate(patterns):
            for match in re.finditer(pattern, source):
                if idx == 0:
                    gid, name = match.groups()
                else:
                    name, gid = match.groups()
                try:
                    decoded_name = json.loads(f'"{name}"')
                except json.JSONDecodeError:
                    decoded_name = name
                if decoded_name and gid:
                    pairs.append((decoded_name, gid))

    result: dict[str, str] = {}
    for name, gid in pairs:
        if name not in result:
            result[name] = build_public_csv_url(spreadsheet_id, gid)
    return result


def parse_google_sheet_tab_names_from_html(html: str) -> list[str]:
    names: list[str] = []
    pattern = r'<div[^>]*class="[^"]*docs-sheet-tab-caption[^"]*"[^>]*>(.*?)</div>'
    for match in re.finditer(pattern, html, flags=re.DOTALL):
        name = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        name = html_module.unescape(name)
        if name and name not in names:
            names.append(name)
    return names


def discover_public_csv_tabs(sheet_url: str, allow_insecure_ssl: bool = False) -> dict[str, str]:
    spreadsheet_id = extract_spreadsheet_id(sheet_url)
    html = _read_url_text(f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit", allow_insecure_ssl)
    tabs = parse_google_sheet_tabs_from_html(spreadsheet_id, html)
    if not tabs:
        raise ValueError("시트 탭을 찾지 못했습니다. 시트 공유 설정이 '링크가 있는 사용자 보기 가능'인지 확인하세요.")
    return tabs


def discover_public_sheet_source(sheet_url: str, allow_insecure_ssl: bool = False) -> tuple[str, dict[str, str], str]:
    spreadsheet_id = extract_spreadsheet_id(sheet_url)
    html = _read_url_text(f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit", allow_insecure_ssl)
    tabs = parse_google_sheet_tabs_from_html(spreadsheet_id, html)
    if tabs:
        return "csv", tabs, ""
    tab_names = parse_google_sheet_tab_names_from_html(html)
    xlsx_url = build_public_xlsx_url(spreadsheet_id)
    strategies = PublicXlsxSheetReader(google_source_config(xlsx_url, allow_insecure_ssl)).read_strategies()
    valid_names = [strategy.sheet_name for strategy in strategies]
    if not valid_names:
        visible = ", ".join(tab_names) if tab_names else "없음"
        raise ValueError(
            "주문시트 탭을 읽었지만 유효한 주문시트를 찾지 못했습니다. "
            f"보이는 탭: {visible}. 템플릿 셀(E6/E8/E10/E12)과 프로그램 영역을 확인하세요."
        )
    return "xlsx", {}, xlsx_url


def google_source_config(public_xlsx_url: str, allow_insecure_ssl: bool) -> GoogleConfig:
    return GoogleConfig(public_xlsx_url=public_xlsx_url, public_csv_tabs={}, allow_insecure_ssl=allow_insecure_ssl)


def save_public_csv_tabs_to_config(config_path: str | Path, sheet_url: str, tabs: dict[str, str]) -> Path:
    path = ensure_config_file(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    google = data.setdefault("google", {})
    google["spreadsheet_id"] = extract_spreadsheet_id(sheet_url)
    google["public_csv_tabs"] = tabs
    google["public_xlsx_url"] = ""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def save_public_xlsx_to_config(config_path: str | Path, sheet_url: str, public_xlsx_url: str) -> Path:
    path = ensure_config_file(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    google = data.setdefault("google", {})
    google["spreadsheet_id"] = extract_spreadsheet_id(sheet_url)
    google["public_csv_tabs"] = {}
    google["public_xlsx_url"] = public_xlsx_url
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def service_account_email_from_file(credential_path: str | Path) -> str:
    path = Path(credential_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    email = str(data.get("client_email", "")).strip()
    if not email:
        raise ValueError("서비스 계정 JSON에서 client_email을 찾지 못했습니다")
    return email


def resolve_credential_path(config_path: str | Path, credential_file: str) -> Path:
    path = Path(credential_file)
    if path.is_absolute():
        return path
    return Path(config_path).resolve().parent / path


def save_service_account_key_to_config(
    config_path: str | Path,
    key_path: str | Path,
    credential_file: str = "data/credentials.json",
) -> tuple[Path, str]:
    config_file = ensure_config_file(config_path)
    source = Path(key_path)
    if not source.exists():
        raise FileNotFoundError(f"서비스 계정 키 파일을 찾지 못했습니다: {source}")
    email = service_account_email_from_file(source)

    destination = resolve_credential_path(config_file, credential_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)

    data = json.loads(config_file.read_text(encoding="utf-8"))
    google = data.setdefault("google", {})
    google["credential_file"] = credential_file
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination, email


def build_cli_command(config_path: str, action: str, sheet_name: str | None = None, side: str | None = None) -> list[str]:
    if is_frozen_app():
        cli_exe = Path(sys.executable).with_name("KiwoomGlobalTraderCli.exe")
        executable = cli_exe if cli_exe.exists() else Path(sys.executable)
        command = [str(executable), "--cli", "--config", config_path]
    else:
        command = [sys.executable, "-m", "src.main", "--config", config_path]
    if action == "list":
        command.append("--list-strategies")
    elif action == "hts_check":
        command.append("--probe-main-toolbar")
    elif action == "dry_run":
        command.extend(["--once", "--dry-run"])
    elif action == "fill_order":
        command.extend(["--once", "--dry-run-fill-order"])
    elif action == "telegram":
        command.append("--test-telegram")
    elif action == "settle":
        command.extend(["--settle", "--dry-run"])
    elif action == "live_order":
        if side not in {"buy", "sell"}:
            raise ValueError("live_order requires side=buy or side=sell")
        command.extend(["--place-decision-order", side])
    else:
        raise ValueError(f"unknown console action: {action}")
    if sheet_name:
        command.extend(["--only-sheet", sheet_name])
    return command


def line_indicates_failure(line: str) -> bool:
    normalized = line.strip()
    lower = normalized.lower()
    if normalized.startswith("SKIP/FAIL"):
        return True
    if normalized.startswith("ERROR:"):
        return True
    if normalized.startswith("Traceback"):
        return True
    if normalized.startswith("PLACE_DECISION_ORDER success=False"):
        return True
    failure_markers = [
        "failed after",
        "hts main window is not open",
        "telegram test failed",
        "runtimeerror:",
        "modulenotfounderror:",
    ]
    return any(marker in lower for marker in failure_markers)


def line_indicates_hts_missing(line: str) -> bool:
    lower = line.strip().lower()
    markers = [
        "hts main window is not open",
        "failed to click hts search box",
        "failed while typing",
        "no active desktop required",
    ]
    return any(marker in lower for marker in markers)


def parse_strategy_result_line(line: str) -> tuple[str, str, str] | None:
    match = re.match(r"^(OK|SKIP/FAIL)\s+([^:]+):\s*(.*)$", line.strip())
    if not match:
        return None
    raw_status, sheet_name, message = match.groups()
    status = "OK" if raw_status == "OK" else "ERROR"
    return status, sheet_name.strip(), message.strip() or "-"


def parse_live_order_result_line(line: str) -> tuple[str, str] | None:
    match = re.match(
        r"^PLACE_DECISION_ORDER\s+success=(True|False)\s+side=(buy|sell)\s+tier=([^\s]+)\s+price=([^\s]+)\s+qty=([^\s]+)\s+order[-_]id=([^\s]*)\s+message=(.*)$",
        line.strip(),
    )
    if not match:
        return None
    success, side, tier, price, qty, order_id, message = match.groups()
    status = "OK" if success == "True" else "ERROR"
    side_text = {"buy": "매수", "sell": "매도"}.get(side, side)
    summary = f"실주문 {side_text} {price} x {qty} tier={tier}"
    if order_id:
        summary += f" 주문번호={order_id}"
    if message:
        summary += f" / {message}"
    return status, summary


def extract_decision_side(message: str) -> str | None:
    match = re.search(r"actions=.*?place:(buy|sell):", message)
    if match:
        return match.group(1)
    match = re.search(r"actions=.*?keep:(buy|sell)", message)
    if match:
        return match.group(1)
    return None


def extract_decision_action(message: str) -> str | None:
    if re.search(r"actions=.*?place:(buy|sell):", message):
        return "place"
    if re.search(r"actions=.*?keep:(buy|sell)", message):
        return "keep"
    if re.search(r"actions=.*?cancel:(buy|sell):", message):
        return "cancel"
    return None


def extract_decision_has_cancel(message: str) -> bool:
    return bool(re.search(r"actions=.*?cancel:(buy|sell):", message))


def now_time_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


def parse_hhmm(value: str) -> time:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError as exc:
        raise ValueError("시간은 HH:MM 형식으로 입력하세요") from exc
    return parsed.time()


def is_time_in_window(current: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def next_window_start(now: datetime, start: time, end: time) -> datetime:
    today_start = datetime.combine(now.date(), start)
    if is_time_in_window(now.time(), start, end):
        return now
    if start <= end:
        if now.time() < start:
            return today_start
        return today_start + timedelta(days=1)
    if end < now.time() < start:
        return today_start
    return today_start + timedelta(days=1)


def next_run_text(value: datetime | None) -> str:
    return value.strftime("%H:%M:%S") if value else "-"


def remaining_time_text(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "-"
    current = now or datetime.now()
    seconds = max(0, int((value - current).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def should_auto_fill_after_dry_run(ok: bool, sheet_state: SheetValidationState | None) -> bool:
    return ok and sheet_state is not None and sheet_state.decision_action == "place" and sheet_state.decision_side in {"buy", "sell"}


def should_auto_live_after_fill_order(ok: bool, live_enabled: bool, sheet_state: SheetValidationState | None) -> bool:
    return ok and live_enabled and sheet_state is not None and sheet_state.decision_action == "place" and sheet_state.decision_side in {"buy", "sell"}


def normalize_console_settings(data: dict[str, object] | None) -> dict[str, object]:
    source = data if isinstance(data, dict) else {}
    schedule = source.get("auto_schedule")
    schedule = schedule if isinstance(schedule, dict) else {}
    start = str(schedule.get("start", DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["start"]))
    end = str(schedule.get("end", DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["end"]))
    try:
        parse_hhmm(start)
    except ValueError:
        start = DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["start"]
    try:
        parse_hhmm(end)
    except ValueError:
        end = DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["end"]
    try:
        interval_sec = int(schedule.get("interval_sec", DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["interval_sec"]))
        if interval_sec <= 0:
            raise ValueError
    except (TypeError, ValueError):
        interval_sec = DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["interval_sec"]
    live_order_enabled = bool(schedule.get("live_order_enabled", DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["live_order_enabled"]))
    auto_start_enabled = bool(schedule.get("auto_start_enabled", DEFAULT_CONSOLE_SETTINGS["auto_schedule"]["auto_start_enabled"]))
    auto_sheets = source.get("auto_sheets")
    auto_sheets = auto_sheets if isinstance(auto_sheets, dict) else {}
    enabled_sheets_raw = auto_sheets.get("enabled", DEFAULT_CONSOLE_SETTINGS["auto_sheets"]["enabled"])
    enabled_sheets = [str(name) for name in enabled_sheets_raw] if isinstance(enabled_sheets_raw, list) else []
    hts_auto = source.get("hts_auto")
    hts_auto = hts_auto if isinstance(hts_auto, dict) else {}
    hts_enabled = bool(hts_auto.get("enabled", DEFAULT_CONSOLE_SETTINGS["hts_auto"]["enabled"]))
    hts_exe_path = str(hts_auto.get("exe_path", DEFAULT_CONSOLE_SETTINGS["hts_auto"]["exe_path"]))
    hts_launch_time = str(hts_auto.get("launch_time", DEFAULT_CONSOLE_SETTINGS["hts_auto"]["launch_time"]))
    hts_simple_pin = str(hts_auto.get("simple_pin", DEFAULT_CONSOLE_SETTINGS["hts_auto"]["simple_pin"]))
    try:
        parse_hhmm(hts_launch_time)
    except ValueError:
        hts_launch_time = DEFAULT_CONSOLE_SETTINGS["hts_auto"]["launch_time"]
    return {
        "auto_schedule": {
            "start": start,
            "end": end,
            "interval_sec": interval_sec,
            "live_order_enabled": live_order_enabled,
            "auto_start_enabled": auto_start_enabled,
        },
        "auto_sheets": {
            "enabled": enabled_sheets,
        },
        "hts_auto": {
            "enabled": hts_enabled,
            "exe_path": hts_exe_path,
            "launch_time": hts_launch_time,
            "simple_pin": hts_simple_pin,
        },
    }


def should_run_daily_time(now: datetime, target_time: time, last_run_date: str | None, max_late_sec: int = 1800) -> bool:
    today = now.strftime("%Y-%m-%d")
    if last_run_date == today:
        return False
    target = datetime.combine(now.date(), target_time)
    return target <= now <= target + timedelta(seconds=max_late_sec)


def launch_hts_process(exe_path: str) -> subprocess.Popen:
    path = Path(exe_path)
    if not path.exists():
        raise FileNotFoundError(f"HTS 실행파일을 찾을 수 없습니다: {exe_path}")
    return subprocess.Popen([str(path)], cwd=str(path.parent))


def login_simple_certificate(pin: str, timeout_sec: int = 20) -> str:
    pin = "".join(ch for ch in str(pin) if ch.isdigit())
    if not pin:
        raise ValueError("간편인증 PIN이 비어 있습니다")
    if len(pin) != 6:
        raise ValueError("간편인증 PIN은 숫자 6자리여야 합니다")
    from pywinauto import Desktop, keyboard, mouse

    deadline = datetime.now() + timedelta(seconds=timeout_sec)
    last_error = "간편인증 창을 찾지 못했습니다"
    while datetime.now() < deadline:
        desktop = Desktop(backend="win32")
        windows = desktop.windows()
        candidates = []
        for window in windows:
            title = window.window_text()
            if "간편인증" in title or "인증서 선택" in title:
                candidates.append(window)
        if not candidates:
            time_module.sleep(0.2)
            continue
        window = candidates[0]
        try:
            window.set_focus()
            rect = window.rectangle()
            x = rect.left + int((rect.right - rect.left) * 0.62)
            y = rect.top + int((rect.bottom - rect.top) * 0.73)
            mouse.click(coords=(x, y))
            time_module.sleep(0.3)
            keyboard.send_keys("^a{BACKSPACE}", pause=0.03)
            keyboard.send_keys(pin, pause=0.03)
            time_module.sleep(0.1)
            keyboard.send_keys("{ENTER}", pause=0.03)
            return f"간편인증 PIN 좌표 입력 및 Enter 완료 ({x},{y})"
        except Exception as exc:
            last_error = str(exc)
        time_module.sleep(0.2)
    raise RuntimeError(last_error)


def set_windows_clipboard_text(text: str) -> None:
    try:
        import win32clipboard
        import win32con

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return
    except Exception:
        pass

    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
    finally:
        root.destroy()


def read_console_settings(path: str | Path) -> dict[str, object]:
    settings_path = Path(path)
    if not settings_path.exists():
        return normalize_console_settings(None)
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return normalize_console_settings(None)
    return normalize_console_settings(data)


def write_console_settings(path: str | Path, settings: dict[str, object]) -> None:
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_console_settings(settings)
    settings_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def action_updates_sheet_rows(action: str) -> bool:
    return action in {"dry_run", "fill_order"}


def action_label(action: str) -> str:
    return {
        "list": "시트 확인",
        "hts_check": "HTS 연결 확인",
        "dry_run": "dry-run",
        "fill_order": "주문창 검증",
        "telegram": "텔레그램 테스트",
        "settle": "정산 dry-run",
        "live_order": "실주문",
    }.get(action, action)


def display_status(status: str) -> str:
    return {
        "WAITING": "대기",
        "RUNNING": "실행중",
        "OK": "정상",
        "ERROR": "오류",
    }.get(status, status)


def display_auto_enabled(enabled: bool) -> str:
    return "포함" if enabled else "제외"


def latest_order_summaries(
    project_dir: str | Path,
    sheet_name: str | None = None,
    action: str | None = None,
    limit: int = 4,
) -> list[str]:
    logs_dir = Path(project_dir) / "logs"
    files = sorted(logs_dir.glob("orders_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    rows: list[dict[str, str]] = []
    for path in files[:3]:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows.extend(reader)
        except OSError:
            continue
    if sheet_name:
        rows = [row for row in rows if row.get("sheet_name") == sheet_name]
    if action:
        rows = [row for row in rows if row.get("action") == action]
    rows.sort(key=lambda row: row.get("timestamp", ""))
    rows = rows[-limit:]
    summaries = []
    for row in rows:
        summaries.append(
            " | ".join(
                [
                    row.get("timestamp", ""),
                    row.get("sheet_name", ""),
                    row.get("action", ""),
                    row.get("side", ""),
                    f"{row.get('price', '')} x {row.get('qty', '')}",
                    row.get("result", ""),
                    row.get("message", ""),
                ]
            )
        )
    return summaries


def _format_command(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def _run_qt_app(config_path: str) -> int:
    return _run_tk_app(config_path)
def _run_tk_app(config_path: str) -> int:
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    project_dir = app_base_dir()
    updated, update_message = maybe_auto_update(project_dir)
    if updated:
        return 0

    class TkConsole:
        def __init__(self) -> None:
            self.root = tk.Tk()
            self.root.title("키움글로벌 자동매매 콘솔")
            self.root.geometry("1180x760")
            self.console_settings_path = project_dir / "data" / "console_settings.json"
            self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
            self.running = False
            self.log_lines: list[str] = []
            self.current_action: str | None = None
            self.current_running_sheet: str | None = None
            self.seen_strategy_result = False
            self.hts_connected_at: datetime | None = None
            self.sheet_states: dict[str, SheetValidationState] = {}
            self.schedule_states: dict[str, SheetScheduleState] = {}
            self.schedule_batch: list[str] = []
            self.next_batch_at: datetime | None = None
            self.auto_fill_pending_sheets: set[str] = set()
            self.auto_fill_waiting = False
            self.live_order_notification_sent = False
            self.scheduler_active = False
            self.hts_launch_done_date: str | None = None
            self.hts_login_done_date: str | None = None
            self.hts_login_after_launch_job: str | None = None
            self.hts_auto_ready_at: datetime | None = None
            self.saved_auto_enabled_sheets: set[str] | None = None
            self.auto_start_pending = False
            self.notify_config = NotifyConfig()
            self.notifier = TelegramNotifier(self.notify_config)

            self.config_var = tk.StringVar(value=config_path)
            self.sheet_var = tk.StringVar()
            self.today_open_var = tk.BooleanVar(value=False)
            self.status_var = tk.StringVar(value="대기")
            self.mode_var = tk.StringVar(value="실행 모드: dry-run")
            self.telegram_var = tk.StringVar(value="텔레그램: 미확인")
            self.schedule_start_var = tk.StringVar(value="00:00")
            self.schedule_end_var = tk.StringVar(value="23:59")
            self.schedule_interval_var = tk.StringVar(value="120")
            self.auto_live_order_var = tk.BooleanVar(value=False)
            self.auto_start_var = tk.BooleanVar(value=False)
            self.sheet_url_var = tk.StringVar(value="")
            self.credential_status_var = tk.StringVar(value="정산 쓰기: 미설정")
            self.hts_auto_enabled_var = tk.BooleanVar(value=False)
            self.hts_exe_path_var = tk.StringVar(value="")
            self.hts_launch_time_var = tk.StringVar(value="22:40")
            self.hts_simple_pin_var = tk.StringVar(value="")

            self._build()
            if update_message:
                self.append_log(f"[console] 업데이트 확인: {update_message}")
            self.reload_notifier()
            self.load_console_settings()
            self.reload_config()
            self.refresh_live_button()
            self.hts_auto_ready_at = datetime.now() + timedelta(seconds=15)
            self.root.after(100, self.poll_queue)
            self.root.after(1000, self.scheduler_tick)
            if self.auto_start_var.get():
                self.root.after(1200, self.start_scheduler_from_autostart)

        def _build(self) -> None:
            top = ttk.Frame(self.root, padding=8)
            top.pack(fill=tk.X)
            ttk.Label(top, text="설정").pack(side=tk.LEFT)
            ttk.Entry(top, textvariable=self.config_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
            ttk.Button(top, text="찾기", command=self.browse_config).pack(side=tk.LEFT, padx=2)
            ttk.Button(top, text="설정 불러오기", command=self.reload_config).pack(side=tk.LEFT, padx=2)
            ttk.Label(top, textvariable=self.mode_var).pack(side=tk.LEFT, padx=8)
            ttk.Label(top, textvariable=self.telegram_var).pack(side=tk.LEFT, padx=8)

            notebook = ttk.Notebook(self.root)
            notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

            today = ttk.Frame(notebook, padding=8)
            notebook.add(today, text="오늘 운영")
            self.buttons: list[ttk.Button] = []

            schedule_box = ttk.LabelFrame(today, text="자동 운영", padding=8)
            schedule_box.pack(fill=tk.X)
            ttk.Label(schedule_box, text="시작").pack(side=tk.LEFT)
            ttk.Entry(schedule_box, textvariable=self.schedule_start_var, width=7).pack(side=tk.LEFT, padx=4)
            ttk.Label(schedule_box, text="종료").pack(side=tk.LEFT)
            ttk.Entry(schedule_box, textvariable=self.schedule_end_var, width=7).pack(side=tk.LEFT, padx=4)
            ttk.Label(schedule_box, text="주기(초)").pack(side=tk.LEFT)
            ttk.Entry(schedule_box, textvariable=self.schedule_interval_var, width=7).pack(side=tk.LEFT, padx=4)
            ttk.Label(schedule_box, text="작업: dry-run + 주문창 검증").pack(side=tk.LEFT, padx=12)
            ttk.Checkbutton(schedule_box, text="자동 실주문", variable=self.auto_live_order_var).pack(side=tk.LEFT, padx=4)
            ttk.Checkbutton(schedule_box, text="콘솔 시작 시 예약", variable=self.auto_start_var).pack(side=tk.LEFT, padx=4)
            self.save_auto_button = ttk.Button(schedule_box, text="저장", command=self.save_console_settings)
            self.save_auto_button.pack(side=tk.LEFT, padx=2)
            self.start_auto_button = ttk.Button(schedule_box, text="자동 운영 시작", command=self.start_scheduler)
            self.start_auto_button.pack(side=tk.LEFT, padx=2)
            self.stop_auto_button = ttk.Button(schedule_box, text="자동 운영 중지", command=self.stop_scheduler, state=tk.DISABLED)
            self.stop_auto_button.pack(side=tk.LEFT, padx=2)

            status_box = ttk.LabelFrame(today, text="운영 상태", padding=8)
            status_box.pack(fill=tk.X, pady=(8, 0))
            ttk.Label(status_box, textvariable=self.status_var).pack(side=tk.LEFT)
            ttk.Label(status_box, text="실주문: HTS 확인 + dry-run + 주문창 검증 후 가능").pack(side=tk.RIGHT)

            sheet_box = ttk.LabelFrame(today, text="시트 조작", padding=8)
            sheet_box.pack(fill=tk.X, pady=(8, 0))
            ttk.Label(sheet_box, text="시트").pack(side=tk.LEFT)
            self.sheet_combo = ttk.Combobox(sheet_box, textvariable=self.sheet_var, state="readonly", width=18)
            self.sheet_combo.pack(side=tk.LEFT, padx=6)
            for text, command in [
                ("포함", lambda: self.set_current_sheet_auto_enabled(True)),
                ("제외", lambda: self.set_current_sheet_auto_enabled(False)),
                ("전체 포함", self.enable_all_auto_sheets),
            ]:
                ttk.Button(sheet_box, text=text, command=command).pack(side=tk.LEFT, padx=2)
            for text, command in [
                ("선택 dry-run", lambda: self.run_action("dry_run", self.current_sheet())),
                ("주문창 검증", lambda: self.run_action("fill_order", self.current_sheet())),
            ]:
                button = ttk.Button(sheet_box, text=text, command=command)
                button.pack(side=tk.LEFT, padx=(12 if text == "선택 dry-run" else 2, 2))
                self.buttons.append(button)
            self.live_button = ttk.Button(sheet_box, text="실주문 잠금", state=tk.DISABLED, command=self.on_live_unlock_clicked)
            self.live_button.pack(side=tk.LEFT, padx=(8, 2))
            self.sheet_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_live_button())

            check_box = ttk.LabelFrame(today, text="점검", padding=8)
            check_box.pack(fill=tk.X, pady=(8, 0))
            for text, command in [
                ("HTS 연결 확인", lambda: self.run_action("hts_check")),
                ("시트 확인", lambda: self.run_action("list")),
                ("전체 dry-run", lambda: self.run_action("dry_run")),
            ]:
                button = ttk.Button(check_box, text=text, command=command)
                button.pack(side=tk.LEFT, padx=2)
                self.buttons.append(button)

            columns = ("sheet", "auto", "status", "dry_run", "fill_order", "next_run", "remaining", "result")
            self.table = ttk.Treeview(today, columns=columns, show="headings", height=10)
            for key, text in [
                ("sheet", "시트"),
                ("auto", "자동"),
                ("status", "상태"),
                ("dry_run", "dry-run"),
                ("fill_order", "검증"),
                ("next_run", "다음"),
                ("remaining", "남은"),
                ("result", "최근 결과"),
            ]:
                self.table.heading(key, text=text)
            self.table.column("sheet", width=110, anchor=tk.W)
            self.table.column("auto", width=60, anchor=tk.CENTER)
            self.table.column("status", width=80, anchor=tk.CENTER)
            self.table.column("dry_run", width=80, anchor=tk.CENTER)
            self.table.column("fill_order", width=80, anchor=tk.CENTER)
            self.table.column("next_run", width=80, anchor=tk.CENTER)
            self.table.column("remaining", width=80, anchor=tk.CENTER)
            self.table.column("result", width=420, anchor=tk.W)
            self.table.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

            sheet_tab = ttk.Frame(notebook, padding=12)
            notebook.add(sheet_tab, text="시트 설정")
            sheet_link_box = ttk.LabelFrame(sheet_tab, text="주문시트 연결", padding=8)
            sheet_link_box.pack(fill=tk.X)
            ttk.Label(sheet_link_box, text="구글시트 URL").grid(row=0, column=0, sticky=tk.W, padx=4, pady=3)
            ttk.Entry(sheet_link_box, textvariable=self.sheet_url_var, width=90).grid(row=0, column=1, sticky=tk.EW, padx=4, pady=3)
            ttk.Button(sheet_link_box, text="시트 연결/저장", command=self.connect_google_sheet).grid(row=0, column=2, padx=4, pady=3)
            sheet_link_box.columnconfigure(1, weight=1)
            ttk.Label(sheet_tab, text="주문시트를 사본으로 만든 뒤 URL을 붙여넣으면 탭 목록을 자동으로 읽어 설정에 저장합니다.").pack(anchor=tk.W, pady=(10, 3))
            ttk.Label(sheet_tab, text="시트는 '링크가 있는 사용자 보기 가능'으로 공유되어 있어야 합니다. 계좌, 티커, 층 정보는 각 탭에서 읽습니다.").pack(anchor=tk.W, pady=3)

            hts_tab = ttk.Frame(notebook, padding=12)
            notebook.add(hts_tab, text="HTS/계좌 설정")
            auto_hts_box = ttk.LabelFrame(hts_tab, text="HTS 자동 실행/간편인증", padding=8)
            auto_hts_box.pack(fill=tk.X)
            ttk.Checkbutton(auto_hts_box, text="HTS 자동 실행 사용", variable=self.hts_auto_enabled_var).grid(row=0, column=0, sticky=tk.W, padx=4, pady=3)
            ttk.Label(auto_hts_box, text="실행파일").grid(row=1, column=0, sticky=tk.W, padx=4, pady=3)
            ttk.Entry(auto_hts_box, textvariable=self.hts_exe_path_var, width=80).grid(row=1, column=1, columnspan=4, sticky=tk.EW, padx=4, pady=3)
            ttk.Button(auto_hts_box, text="찾기", command=self.browse_hts_exe).grid(row=1, column=5, padx=4, pady=3)
            ttk.Label(auto_hts_box, text="HTS 실행").grid(row=2, column=0, sticky=tk.W, padx=4, pady=3)
            ttk.Entry(auto_hts_box, textvariable=self.hts_launch_time_var, width=8).grid(row=2, column=1, sticky=tk.W, padx=4, pady=3)
            ttk.Label(auto_hts_box, text="PIN").grid(row=2, column=2, sticky=tk.W, padx=4, pady=3)
            ttk.Entry(auto_hts_box, textvariable=self.hts_simple_pin_var, width=12, show="*").grid(row=2, column=3, sticky=tk.W, padx=4, pady=3)
            ttk.Label(auto_hts_box, text="HTS 실행 10초 뒤 PIN 자동 입력").grid(row=2, column=4, columnspan=2, sticky=tk.W, padx=8, pady=3)
            ttk.Button(auto_hts_box, text="HTS 실행 테스트", command=self.launch_hts_now).grid(row=3, column=0, padx=4, pady=6, sticky=tk.W)
            ttk.Button(auto_hts_box, text="HTS 설정 저장", command=self.save_console_settings).grid(row=3, column=1, padx=4, pady=6, sticky=tk.W)
            auto_hts_box.columnconfigure(1, weight=1)
            ttk.Label(hts_tab, text="콘솔이 켜져 있으면 설정된 시각에 HTS를 실행하고, 10초 뒤 PIN을 자동 입력합니다.").pack(anchor=tk.W, pady=8)
            ttk.Label(hts_tab, text="PIN은 data/console_settings.json에 저장됩니다. 개인 PC에서만 사용하세요.").pack(anchor=tk.W, pady=3)

            log_tab = ttk.Frame(notebook, padding=8)
            notebook.add(log_tab, text="로그/정산")
            notify_box = ttk.LabelFrame(log_tab, text="알림", padding=8)
            notify_box.pack(fill=tk.X)
            telegram_button = ttk.Button(notify_box, text="텔레그램 테스트", command=lambda: self.run_action("telegram"))
            telegram_button.pack(side=tk.LEFT, padx=2)
            self.buttons.append(telegram_button)

            settlement_box = ttk.LabelFrame(log_tab, text="정산 시트 쓰기", padding=8)
            settlement_box.pack(fill=tk.X, pady=(8, 0))
            ttk.Button(settlement_box, text="서비스 계정 키 등록", command=self.browse_service_account_key).pack(side=tk.LEFT, padx=2)
            settle_button = ttk.Button(settlement_box, text="정산 실행/시트쓰기", command=lambda: self.run_action("settle"))
            settle_button.pack(side=tk.LEFT, padx=2)
            self.buttons.append(settle_button)
            ttk.Label(settlement_box, textvariable=self.credential_status_var).pack(side=tk.LEFT, padx=10)
            ttk.Label(log_tab, text="정산을 Google 시트에 쓰려면 서비스 계정 이메일을 주문시트 공유 화면에서 편집자로 추가하세요.").pack(anchor=tk.W, pady=(8, 0))
            ttk.Label(log_tab, text="상세 로그는 logs 폴더에 저장됩니다. 화면 로그는 최근 500줄만 유지합니다.").pack(anchor=tk.W, pady=8)

            log_frame = ttk.Frame(self.root, padding=(8, 0, 8, 8))
            log_frame.pack(fill=tk.BOTH, expand=True)
            ttk.Label(log_frame, text="로그").pack(anchor=tk.W)
            self.log_text = tk.Text(log_frame, height=9, wrap=tk.NONE)
            self.log_text.pack(fill=tk.BOTH, expand=True)

        def browse_config(self) -> None:
            filename = filedialog.askopenfilename(
                title="설정 파일 선택",
                initialdir=str(project_dir),
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            )
            if filename:
                self.config_var.set(filename)
                self.reload_config()

        def browse_hts_exe(self) -> None:
            filename = filedialog.askopenfilename(
                title="HTS 실행파일 선택",
                filetypes=[("Executable Files", "*.exe"), ("All Files", "*.*")],
            )
            if filename:
                self.hts_exe_path_var.set(filename)
                self.save_console_settings()

        def browse_service_account_key(self) -> None:
            filename = filedialog.askopenfilename(
                title="구글 서비스 계정 JSON 선택",
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            )
            if not filename:
                return
            config_path = self.config_var.get().strip() or "config.live.json"
            try:
                destination, email = save_service_account_key_to_config(config_path, filename)
            except Exception as exc:
                self.append_log(f"[console] 서비스 계정 키 등록 실패: {exc}")
                messagebox.showerror("서비스 계정 키 오류", str(exc))
                return
            self.credential_status_var.set(f"정산 쓰기: 키 등록됨 ({email})")
            self.append_log(f"[console] 서비스 계정 키 등록 완료 path={destination} email={email}")
            messagebox.showinfo(
                "서비스 계정 키 등록 완료",
                "정산 시트 쓰기 키를 등록했습니다.\n\n"
                "주문시트의 공유 버튼을 누른 뒤 아래 이메일을 편집자로 추가하세요.\n\n"
                f"{email}",
            )
            self.refresh_credential_status()

        def connect_google_sheet(self) -> None:
            sheet_url = self.sheet_url_var.get().strip()
            if not sheet_url:
                messagebox.showerror("시트 연결 오류", "구글시트 URL을 입력하세요.")
                return
            config_path = self.config_var.get().strip() or "config.live.json"
            try:
                config_file = ensure_config_file(config_path)
                config = load_config(config_file)
                source_type, tabs, xlsx_url = discover_public_sheet_source(
                    sheet_url,
                    allow_insecure_ssl=config.google.allow_insecure_ssl,
                )
                if source_type == "csv":
                    save_public_csv_tabs_to_config(config_path, sheet_url, tabs)
                    detail = f"{len(tabs)}개 탭을 CSV 방식으로 설정에 저장했습니다."
                    log_detail = f"mode=csv tabs={','.join(tabs.keys())}"
                else:
                    save_public_xlsx_to_config(config_path, sheet_url, xlsx_url)
                    sheets = load_sheet_tabs(config_path)
                    detail = f"{len(sheets)}개 주문시트를 XLSX 방식으로 설정에 저장했습니다."
                    log_detail = f"mode=xlsx tabs={','.join(sheet.name for sheet in sheets)}"
            except Exception as exc:
                self.append_log(f"[console] 시트 연결 실패: {exc}")
                messagebox.showerror("시트 연결 오류", str(exc))
                return
            self.append_log(f"[console] 주문시트 연결 완료 {log_detail}")
            messagebox.showinfo("시트 연결 완료", detail)
            self.reload_config()

        def reload_config(self) -> None:
            self.reload_notifier()
            self.refresh_credential_status()
            sheets = load_sheet_tabs(self.config_var.get().strip() or "config.live.json")
            self.table.delete(*self.table.get_children())
            values = []
            self.sheet_states = {}
            self.schedule_states = {}
            self.auto_fill_pending_sheets = set()
            for sheet in sheets:
                values.append(sheet.name)
                self.sheet_states[sheet.name] = SheetValidationState()
                enabled = True if self.saved_auto_enabled_sheets is None else sheet.name in self.saved_auto_enabled_sheets
                self.schedule_states[sheet.name] = SheetScheduleState(enabled=enabled)
                self.table.insert(
                    "",
                    tk.END,
                    iid=sheet.name,
                    values=(sheet.name, display_auto_enabled(enabled), display_status("WAITING"), "-", "-", "-", "-", "-"),
                )
            self.sheet_combo["values"] = values
            self.sheet_var.set(values[0] if values else "")
            self.append_log(f"[console] 시트 {len(sheets)}개 불러옴")
            self.refresh_live_button()

        def refresh_credential_status(self) -> None:
            config_path = self.config_var.get().strip() or "config.live.json"
            try:
                config_file = resolve_config_file(config_path)
                config = load_config(config_file)
                credential_path = resolve_credential_path(config_file, config.google.credential_file)
                if not credential_path.exists():
                    self.credential_status_var.set("정산 쓰기: 키 미등록")
                    return
                email = service_account_email_from_file(credential_path)
            except Exception:
                self.credential_status_var.set("정산 쓰기: 키 확인 필요")
                return
            self.credential_status_var.set(f"정산 쓰기: 키 등록됨 ({email})")

        def set_current_sheet_auto_enabled(self, enabled: bool) -> None:
            sheet_name = self.current_sheet()
            if not sheet_name or sheet_name not in self.schedule_states:
                return
            self.schedule_states[sheet_name].enabled = enabled
            self.update_sheet_auto_enabled(sheet_name)
            self.save_console_settings()
            self.append_log(f"[console] 자동 대상 {'포함' if enabled else '제외'} sheet={sheet_name}")

        def enable_all_auto_sheets(self) -> None:
            for sheet_name, state in self.schedule_states.items():
                state.enabled = True
                self.update_sheet_auto_enabled(sheet_name)
            self.save_console_settings()
            self.append_log("[console] 자동 대상 전체 포함")

        def current_sheet(self) -> str | None:
            value = self.sheet_var.get().strip()
            return value or None

        def reload_notifier(self) -> None:
            try:
                load_env(project_dir / ".env")
                config = load_config(resolve_config_file(self.config_var.get().strip() or "config.live.json"))
                self.notify_config = config.notify
                self.notifier = TelegramNotifier(config.notify)
                self.telegram_var.set("텔레그램: 설정됨" if config.notify.telegram_enabled else "텔레그램: 비활성")
            except Exception as exc:
                self.notify_config = NotifyConfig()
                self.notifier = TelegramNotifier(self.notify_config)
                self.telegram_var.set("텔레그램: 설정 오류")
                self.append_log(f"[console] 텔레그램 설정 오류: {exc}")

        def send_console_telegram(self, text: str, *, failure: bool = False, keepalive: bool = False, order: bool = False) -> None:
            if not self.notify_config.telegram_enabled:
                return
            if failure and not self.notify_config.telegram_send_failures:
                return
            if keepalive and not self.notify_config.telegram_send_keepalive:
                return
            if order and not self.notify_config.telegram_send_orders:
                return

            def worker() -> None:
                result = self.notifier.send(text)
                self.queue.put(("telegram", result.sent))
                if not result.sent:
                    self.queue.put(("line", f"[console] 텔레그램 전송 실패: {result.message}"))

            self.telegram_var.set("텔레그램: 전송중")
            threading.Thread(target=worker, daemon=True).start()

        def load_console_settings(self) -> None:
            settings = read_console_settings(self.console_settings_path)
            schedule = settings.get("auto_schedule", {})
            if isinstance(schedule, dict):
                self.schedule_start_var.set(str(schedule.get("start", "00:00")))
                self.schedule_end_var.set(str(schedule.get("end", "23:59")))
                self.schedule_interval_var.set(str(schedule.get("interval_sec", 120)))
                self.auto_live_order_var.set(bool(schedule.get("live_order_enabled", False)))
                self.auto_start_var.set(bool(schedule.get("auto_start_enabled", False)))
            auto_sheets = settings.get("auto_sheets", {})
            if isinstance(auto_sheets, dict):
                enabled = auto_sheets.get("enabled", [])
                self.saved_auto_enabled_sheets = set(str(name) for name in enabled) if isinstance(enabled, list) and enabled else None
            hts_auto = settings.get("hts_auto", {})
            if isinstance(hts_auto, dict):
                self.hts_auto_enabled_var.set(bool(hts_auto.get("enabled", False)))
                self.hts_exe_path_var.set(str(hts_auto.get("exe_path", "")))
                self.hts_launch_time_var.set(str(hts_auto.get("launch_time", "22:40")))
                self.hts_simple_pin_var.set(str(hts_auto.get("simple_pin", "")))
            self.append_log(f"[console] 자동 설정 불러옴 path={self.console_settings_path}")

        def current_console_settings(self) -> dict[str, object]:
            start = self.schedule_start_var.get().strip()
            end = self.schedule_end_var.get().strip()
            interval = int(self.schedule_interval_var.get().strip())
            enabled_sheets = [sheet_name for sheet_name, state in self.schedule_states.items() if state.enabled]
            return {
                "auto_schedule": {
                    "start": start,
                    "end": end,
                    "interval_sec": interval,
                    "live_order_enabled": self.auto_live_order_var.get(),
                    "auto_start_enabled": self.auto_start_var.get(),
                },
                "auto_sheets": {
                    "enabled": enabled_sheets,
                },
                "hts_auto": {
                    "enabled": self.hts_auto_enabled_var.get(),
                    "exe_path": self.hts_exe_path_var.get().strip(),
                    "launch_time": self.hts_launch_time_var.get().strip(),
                    "simple_pin": self.hts_simple_pin_var.get().strip(),
                },
            }

        def save_console_settings(self) -> None:
            try:
                settings = normalize_console_settings(self.current_console_settings())
                parse_hhmm(str(settings["auto_schedule"]["start"]))
                parse_hhmm(str(settings["auto_schedule"]["end"]))
                parse_hhmm(str(settings["hts_auto"]["launch_time"]))
                interval = int(settings["auto_schedule"]["interval_sec"])
                if interval <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                messagebox.showerror("설정 오류", "시간은 HH:MM, 주기는 1 이상의 초 단위로 입력하세요")
                return
            write_console_settings(self.console_settings_path, settings)
            self.append_log(f"[console] 자동 설정 저장 path={self.console_settings_path}")

        def launch_hts_now(self) -> None:
            self.save_console_settings()
            self.hts_connected_at = None
            try:
                launch_hts_process(self.hts_exe_path_var.get().strip())
            except Exception as exc:
                self.append_log(f"[console] HTS 실행 실패: {exc}")
                self.send_console_telegram(f"[HTS 실행 실패]\n{exc}", failure=True)
                return
            self.hts_launch_done_date = datetime.now().strftime("%Y-%m-%d")
            self.append_log("[console] HTS 실행 요청 완료")
            self.send_console_telegram("[HTS 실행 요청 완료]")
            self.schedule_login_after_hts_launch()

        def schedule_login_after_hts_launch(self) -> None:
            if self.hts_login_after_launch_job is not None:
                try:
                    self.root.after_cancel(self.hts_login_after_launch_job)
                except Exception:
                    pass
            self.append_log("[console] HTS 실행 후 10초 뒤 간편인증을 시도합니다")
            self.hts_login_after_launch_job = self.root.after(10000, self.login_hts_after_launch)

        def login_hts_after_launch(self) -> None:
            self.hts_login_after_launch_job = None
            try:
                self.login_hts_now()
            except Exception as exc:
                self.append_log(f"[console] 예약 간편인증 오류: {exc}")
                self.send_console_telegram(f"[HTS 예약 간편인증 오류]\n{exc}", failure=True)

        def login_hts_now(self) -> None:
            self.save_console_settings()
            try:
                message = login_simple_certificate(self.hts_simple_pin_var.get().strip())
            except Exception as exc:
                self.append_log(f"[console] 간편인증 실패: {exc}")
                self.send_console_telegram(f"[HTS 간편인증 실패]\n{exc}", failure=True)
                return
            self.hts_login_done_date = datetime.now().strftime("%Y-%m-%d")
            self.append_log(f"[console] {message}")
            self.send_console_telegram(f"[HTS 간편인증 완료]\n{message}")
            self.root.after(12000, self.check_hts_after_login)

        def check_hts_after_login(self) -> None:
            if self.running:
                self.root.after(1000, self.check_hts_after_login)
                return
            self.append_log("[console] 간편인증 후 HTS 연결 확인을 실행합니다")
            self.run_action("hts_check")

        def toggle_today_open(self) -> None:
            self.append_log(f"[console] 오늘 장 운영 {'ON' if self.today_open_var.get() else 'OFF'}")
            self.refresh_live_button()

        def on_live_unlock_clicked(self) -> None:
            ready, reason = self.current_live_unlock_status()
            sheet_name = self.current_sheet()
            state = self.sheet_states.get(sheet_name or "")
            side = state.decision_side if state else None
            self.append_log(f"[console] 실주문 요청 ready={ready} sheet={sheet_name} side={side} reason={reason}")
            if not ready or not sheet_name or side not in {"buy", "sell"}:
                return
            if not messagebox.askyesno("실주문 확인", f"{sheet_name} {side.upper()} 실주문을 실행할까요?"):
                self.append_log("[console] 실주문 1차 확인에서 취소됨")
                return
            if not messagebox.askyesno("실주문 최종 확인", "주문 버튼이 실제로 눌립니다. 계속할까요?"):
                self.append_log("[console] 실주문 최종 확인에서 취소됨")
                return
            self.run_action("live_order", sheet_name, side)

        def current_live_unlock_status(self) -> tuple[bool, str]:
            sheet_name = self.current_sheet()
            state = self.sheet_states.get(sheet_name or "")
            return live_unlock_status(self.today_open_var.get(), self.hts_connected_at, state)

        def hts_ready_for_auto_scheduler(self) -> bool:
            return (not self.hts_auto_enabled_var.get()) or self.hts_connected_at is not None

        def defer_scheduler_until_hts_ready(self, source: str) -> None:
            self.auto_start_pending = True
            self.scheduler_active = False
            self.schedule_batch = []
            self.next_batch_at = None
            for sheet_name, state in self.schedule_states.items():
                state.next_run_at = None
                self.update_sheet_next_run(sheet_name)
            self.start_auto_button.configure(state=tk.NORMAL)
            self.stop_auto_button.configure(state=tk.DISABLED)
            self.append_log(f"[console] 자동 운영 대기: HTS 로그인/연결 확인 필요 ({source})")
            self.status_var.set("자동 운영 대기 / HTS 로그인 대기")

        def refresh_live_button(self) -> None:
            if not hasattr(self, "live_button"):
                return
            ready, reason = self.current_live_unlock_status()
            self.live_button.configure(
                state=tk.NORMAL if ready and not self.running else tk.DISABLED,
                text="실주문 실행 가능" if ready else "실주문 잠금",
            )
            self.status_var.set(f"대기 / {reason}" if not self.running else self.status_var.get())

        def run_action(self, action: str, sheet_name: str | None = None, side: str | None = None) -> bool:
            if self.running:
                self.append_log("[console] 다른 명령이 실행 중입니다")
                return False
            config = self.config_var.get().strip() or "config.live.json"
            try:
                command = build_cli_command(config, action, sheet_name, side)
            except ValueError as exc:
                messagebox.showerror("실행 오류", str(exc))
                return False
            self.current_action = action
            self.current_running_sheet = sheet_name
            self.seen_strategy_result = False
            if action == "live_order":
                self.live_order_notification_sent = False
            self.set_running(True, action, sheet_name)
            threading.Thread(target=self._worker, args=(command, action, sheet_name), daemon=True).start()
            return True

        def start_scheduler(self) -> None:
            try:
                start = parse_hhmm(self.schedule_start_var.get())
                end = parse_hhmm(self.schedule_end_var.get())
                interval = int(self.schedule_interval_var.get().strip())
                if interval <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("설정 오류", "시작/종료는 HH:MM, 주기는 1 이상의 초 단위로 입력하세요")
                return
            write_console_settings(
                self.console_settings_path,
                self.current_console_settings(),
            )
            if not self.hts_ready_for_auto_scheduler():
                self.defer_scheduler_until_hts_ready("시작 요청")
                return
            now = datetime.now()
            self.scheduler_active = True
            in_window = is_time_in_window(now.time(), start, end)
            enabled_sheets = [sheet_name for sheet_name, state in self.schedule_states.items() if state.enabled]
            if not enabled_sheets:
                messagebox.showerror("자동 운영 오류", "자동 운영 대상 시트가 없습니다. 시트를 포함한 뒤 다시 시작하세요.")
                self.scheduler_active = False
                return
            self.schedule_batch = enabled_sheets if in_window else []
            self.next_batch_at = now + timedelta(seconds=interval) if in_window else next_window_start(now, start, end)
            for sheet_name, state in self.schedule_states.items():
                if sheet_name in self.schedule_batch:
                    state.next_run_at = now
                elif state.enabled and not in_window:
                    state.next_run_at = self.next_batch_at
                else:
                    state.next_run_at = None
                self.update_sheet_next_run(sheet_name)
            self.start_auto_button.configure(state=tk.DISABLED)
            self.stop_auto_button.configure(state=tk.NORMAL)
            self.append_log(
                f"[console] 자동 운영 시작 start={start.strftime('%H:%M')} end={end.strftime('%H:%M')} interval={interval}s "
                f"action=dry-run sheets={','.join(enabled_sheets)} live_order={self.auto_live_order_var.get()}"
            )
            self.send_console_telegram(
                f"[콘솔 자동 운영 시작]\n시작: {start.strftime('%H:%M')}\n종료: {end.strftime('%H:%M')}\n주기: {interval}초\n"
                f"대상: {', '.join(enabled_sheets)}\n작업: dry-run + 주문창 검증"
                f"{' + 실주문' if self.auto_live_order_var.get() else ''}"
            )
            if in_window:
                self.status_var.set("자동 운영 대기")
            else:
                self.status_var.set(f"자동 운영 대기 / 다음 시작 {next_run_text(self.next_batch_at)}")

        def start_scheduler_from_autostart(self) -> None:
            if self.scheduler_active:
                return
            if not self.hts_ready_for_auto_scheduler():
                self.defer_scheduler_until_hts_ready("콘솔 시작 예약")
                return
            self.append_log("[console] 콘솔 시작 설정에 따라 자동 운영 예약을 활성화합니다")
            self.start_scheduler()

        def stop_scheduler(self) -> None:
            self.scheduler_active = False
            self.schedule_batch = []
            self.next_batch_at = None
            self.auto_fill_pending_sheets = set()
            self.auto_fill_waiting = False
            for sheet_name, state in self.schedule_states.items():
                state.next_run_at = None
                self.update_sheet_next_run(sheet_name)
            self.start_auto_button.configure(state=tk.NORMAL)
            self.stop_auto_button.configure(state=tk.DISABLED)
            self.append_log("[console] 자동 운영 중지")
            self.send_console_telegram("[콘솔 자동 운영 중지]")
            self.refresh_live_button()

        def scheduler_tick(self) -> None:
            try:
                self._scheduler_tick_once()
                self.refresh_remaining_times()
            finally:
                self.root.after(1000, self.scheduler_tick)

        def _scheduler_tick_once(self) -> None:
            try:
                self._hts_auto_tick_once()
            except Exception as exc:
                self.append_log(f"[console] HTS 자동 실행 오류: {exc}")
                self.send_console_telegram(f"[HTS 자동 실행 오류]\n{exc}", failure=True)
            if not self.scheduler_active or self.running or self.auto_fill_waiting:
                return
            if not self.hts_ready_for_auto_scheduler():
                self.status_var.set("자동 운영 대기 / HTS 연결 확인 대기")
                self.auto_start_pending = True
                return
            try:
                start = parse_hhmm(self.schedule_start_var.get())
                end = parse_hhmm(self.schedule_end_var.get())
                interval = int(self.schedule_interval_var.get().strip())
            except ValueError:
                self.stop_scheduler()
                self.append_log("[console] 자동 운영 설정 오류로 중지됨")
                return
            now = datetime.now()
            if not self.schedule_batch:
                if not is_time_in_window(now.time(), start, end):
                    next_start = next_window_start(now, start, end)
                    for sheet_name, state in self.schedule_states.items():
                        if state.enabled:
                            state.next_run_at = next_start
                            self.update_sheet_next_run(sheet_name)
                        elif state.next_run_at is not None:
                            state.next_run_at = None
                            self.update_sheet_next_run(sheet_name)
                    self.next_batch_at = next_start
                    self.status_var.set(f"자동 운영 대기 / 다음 시작 {next_run_text(next_start)}")
                    return
                if self.next_batch_at is not None and now < self.next_batch_at:
                    self.status_var.set(f"자동 운영 대기 / 다음 묶음 {next_run_text(self.next_batch_at)}")
                    return
                self.schedule_batch = [sheet_name for sheet_name, state in self.schedule_states.items() if state.enabled]
                if not self.schedule_batch:
                    self.status_var.set("자동 운영 대기 / 대상 시트 없음")
                    return
                self.next_batch_at = now + timedelta(seconds=interval)
                for sheet_name, state in self.schedule_states.items():
                    state.next_run_at = now if sheet_name in self.schedule_batch else None
                    self.update_sheet_next_run(sheet_name)
            sheet_name = self.schedule_batch.pop(0)
            state = self.schedule_states.get(sheet_name)
            if state is not None:
                state.next_run_at = self.next_batch_at
                self.update_sheet_next_run(sheet_name)
            self.append_log(
                f"[console] 자동 dry-run 실행 sheet={sheet_name} remaining={len(self.schedule_batch)} next_batch={next_run_text(self.next_batch_at)}"
            )
            self.auto_fill_pending_sheets.add(sheet_name)
            self.run_action("dry_run", sheet_name)

        def _hts_auto_tick_once(self) -> None:
            if not self.hts_auto_enabled_var.get():
                return
            if self.hts_auto_ready_at is not None and datetime.now() < self.hts_auto_ready_at:
                return
            now = datetime.now()
            try:
                launch_time = parse_hhmm(self.hts_launch_time_var.get())
            except ValueError:
                self.append_log("[console] HTS 자동 실행 시간 설정 오류")
                return
            if should_run_daily_time(now, launch_time, self.hts_launch_done_date):
                self.launch_hts_now()

        def _worker(self, command: list[str], action: str, sheet_name: str | None) -> None:
            self.queue.put(("line", f"$ {_format_command(command)}"))
            command_failed = False
            hts_missing = False
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(project_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding=_console_encoding(),
                    errors="replace",
                )
                assert process.stdout is not None
                for raw_line in process.stdout:
                    line = raw_line.rstrip()
                    if line_indicates_failure(line):
                        command_failed = True
                    if line_indicates_hts_missing(line):
                        hts_missing = True
                    self.queue.put(("line", line))
                code = process.wait()
            except Exception as exc:
                self.queue.put(("line", f"ERROR: {exc}"))
                code = 1
                command_failed = True
            self.queue.put(("finished", (code, action, sheet_name, command_failed, hts_missing)))

        def poll_queue(self) -> None:
            try:
                while True:
                    kind, payload = self.queue.get_nowait()
                    if kind == "line":
                        self.append_log(str(payload))
                    elif kind == "finished":
                        code, action, sheet_name, command_failed, hts_missing = payload
                        self.on_command_finished(
                            int(code),
                            bool(command_failed),
                            str(action),
                            sheet_name if isinstance(sheet_name, str) else None,
                            bool(hts_missing),
                        )
                    elif kind == "telegram":
                        self.telegram_var.set("텔레그램: 정상" if bool(payload) else "텔레그램: 실패")
            except queue.Empty:
                pass
            self.root.after(100, self.poll_queue)

        def on_command_finished(self, code: int, command_failed: bool, action: str, sheet_name: str | None, hts_missing: bool = False) -> None:
            ok = code == 0 and not command_failed
            self.append_log(f"[console] 명령 완료 code={code} status={'정상' if ok else '실패'}")
            self.append_order_summary(action, sheet_name)
            self.set_running(False, action, sheet_name)
            if action == "hts_check":
                self.hts_connected_at = datetime.now() if ok else None
                if ok and self.auto_start_pending and not self.scheduler_active:
                    self.auto_start_pending = False
                    self.append_log("[console] HTS 연결 확인 완료: 대기 중이던 자동 운영을 시작합니다")
                    self.root.after(500, self.start_scheduler)
                elif not ok and self.auto_start_pending:
                    self.status_var.set("자동 운영 대기 / HTS 연결 확인 실패")
            if ok and action in {"dry_run", "fill_order", "live_order"}:
                self.hts_connected_at = datetime.now()
            if sheet_name is not None and not self.seen_strategy_result:
                self.mark_sheet(sheet_name, "OK" if ok else "ERROR", action)
            if action == "telegram":
                self.telegram_var.set("텔레그램: 정상" if ok else "텔레그램: 실패")
            if action == "live_order":
                self.mode_var.set("실행 모드: 실주문 실행 완료" if ok else "실행 모드: 실주문 실패")
            elif action != "live_order":
                self.mode_var.set("실행 모드: dry-run")
            if not ok and not (action == "live_order" and self.live_order_notification_sent):
                self.send_console_telegram(
                    console_notification_text(action, sheet_name, ok, "콘솔 명령 실패 또는 오류 로그 감지"),
                    failure=True,
                )
                if hts_missing and self.hts_auto_enabled_var.get():
                    self.append_log("[console] HTS 미감지 실패: HTS 재실행을 시도합니다")
                    self.hts_connected_at = None
                    self.launch_hts_now()
            elif action == "live_order" and not self.live_order_notification_sent:
                self.send_console_telegram(
                    console_notification_text(action, sheet_name, ok, "실주문 명령 완료"),
                    order=True,
                )
            self.current_action = None
            self.current_running_sheet = None
            self.refresh_live_button()
            self.maybe_run_auto_fill_order(ok, action, sheet_name)
            if action == "fill_order":
                self.auto_fill_waiting = False
                self.maybe_run_auto_live_order(ok, sheet_name)

        def maybe_run_auto_fill_order(self, ok: bool, action: str, sheet_name: str | None) -> None:
            if action != "dry_run" or not sheet_name:
                return
            if sheet_name not in self.auto_fill_pending_sheets:
                return
            self.auto_fill_pending_sheets.discard(sheet_name)
            state = self.sheet_states.get(sheet_name)
            if not should_auto_fill_after_dry_run(ok, state):
                self.auto_fill_waiting = False
                if not ok:
                    reason = "dry-run 실패"
                elif state is not None and state.decision_action == "keep":
                    reason = "신규 주문 없음 / 기존 미체결 유지"
                elif state is not None and state.decision_action == "cancel":
                    reason = "신규 주문 없음 / 취소만 필요"
                else:
                    reason = "주문 판단 없음"
                self.append_log(f"[console] 자동 주문창 검증 생략 sheet={sheet_name} reason={reason}")
                return
            self.append_log(f"[console] 자동 주문창 검증 실행 sheet={sheet_name} side={state.decision_side}")
            self.auto_fill_waiting = True
            self.root.after(100, lambda: self.run_auto_fill_order(sheet_name))

        def run_auto_fill_order(self, sheet_name: str) -> None:
            if self.running:
                self.root.after(500, lambda: self.run_auto_fill_order(sheet_name))
                return
            started = self.run_action("fill_order", sheet_name)
            if not started:
                self.auto_fill_waiting = False

        def maybe_run_auto_live_order(self, ok: bool, sheet_name: str | None) -> None:
            if not self.scheduler_active or not sheet_name:
                return
            state = self.sheet_states.get(sheet_name)
            if not should_auto_live_after_fill_order(ok, self.auto_live_order_var.get(), state):
                return
            ready, reason = live_unlock_status(True, self.hts_connected_at, state)
            side = state.decision_side if state else None
            self.append_log(f"[console] 자동 실주문 판단 ready={ready} sheet={sheet_name} side={side} reason={reason}")
            if not ready or side not in {"buy", "sell"}:
                self.send_console_telegram(
                    f"[자동 실주문 보류]\n시트: {sheet_name}\n사유: {reason}",
                    failure=True,
                )
                return
            self.root.after(100, lambda: self.run_auto_live_order(sheet_name, side))

        def run_auto_live_order(self, sheet_name: str, side: str) -> None:
            if self.running:
                self.root.after(500, lambda: self.run_auto_live_order(sheet_name, side))
                return
            self.append_log(f"[console] 자동 실주문 실행 sheet={sheet_name} side={side}")
            self.run_action("live_order", sheet_name, side)

        def append_order_summary(self, action: str, sheet_name: str | None) -> None:
            order_action = {"dry_run": "dry_run_place", "fill_order": "dry_run_fill_order"}.get(action)
            if order_action is None:
                return
            summaries = latest_order_summaries(project_dir, sheet_name=sheet_name, action=order_action, limit=4)
            if not summaries:
                self.append_log(f"[console] 최근 {order_action} 주문 로그가 없습니다")
                return
            self.append_log(f"[console] 최근 {order_action} 로그:")
            for summary in summaries:
                self.append_log(f"  {summary}")

        def set_running(self, running: bool, action: str, sheet_name: str | None) -> None:
            self.running = running
            state = tk.DISABLED if running else tk.NORMAL
            for button in self.buttons:
                button.configure(state=state)
            self.live_button.configure(state=tk.DISABLED)
            if running:
                target = sheet_name or "전체"
                self.status_var.set(f"실행 중: {action_label(action)} / {target}")
                if action == "live_order":
                    self.mode_var.set("실행 모드: 실주문 실행 중")
                if action_updates_sheet_rows(action):
                    self.mark_sheet(sheet_name, "RUNNING", action)
            else:
                self.status_var.set("대기")
                self.refresh_live_button()

        def update_sheet_next_run(self, sheet_name: str) -> None:
            if not sheet_name or not self.table.exists(sheet_name):
                return
            values = list(self.table.item(sheet_name, "values"))
            state = self.schedule_states.get(sheet_name)
            if len(values) >= 8 and state is not None:
                values[5] = next_run_text(state.next_run_at)
                values[6] = remaining_time_text(state.next_run_at)
                self.table.item(sheet_name, values=values)

        def refresh_remaining_times(self) -> None:
            for sheet_name in self.schedule_states:
                self.update_sheet_next_run(sheet_name)

        def update_sheet_auto_enabled(self, sheet_name: str) -> None:
            if not sheet_name or not self.table.exists(sheet_name):
                return
            values = list(self.table.item(sheet_name, "values"))
            state = self.schedule_states.get(sheet_name)
            if len(values) >= 8 and state is not None:
                values[1] = display_auto_enabled(state.enabled)
                if not state.enabled:
                    state.next_run_at = None
                    values[5] = "-"
                    values[6] = "-"
                self.table.item(sheet_name, values=values)

        def mark_sheet(self, sheet_name: str | None, status: str, message: str, update_action: str | None = None) -> None:
            targets = [sheet_name] if sheet_name else list(self.table.get_children())
            for target in targets:
                if not target or not self.table.exists(target):
                    continue
                values = list(self.table.item(target, "values"))
                if len(values) >= 8:
                    values[2] = display_status(status)
                    if status == "OK" and update_action == "dry_run":
                        values[3] = now_time_text()
                        self._update_sheet_validation_time(str(target), "dry_run", message)
                    if status == "OK" and update_action == "fill_order":
                        if extract_decision_action(message) == "place":
                            values[4] = now_time_text()
                            self._update_sheet_validation_time(str(target), "fill_order")
                        else:
                            self._clear_sheet_validation_time(str(target), "fill_order")
                    if status == "ERROR" and update_action in {"dry_run", "fill_order"}:
                        self._clear_sheet_validation_time(str(target), update_action)
                    values[7] = message
                    self.table.item(target, values=values)
            self.refresh_live_button()

        def _update_sheet_validation_time(self, sheet_name: str, action: str, message: str = "") -> None:
            state = self.sheet_states.setdefault(sheet_name, SheetValidationState())
            if action == "dry_run":
                state.dry_run_at = datetime.now()
                state.decision_side = extract_decision_side(message)
                state.decision_action = extract_decision_action(message)
                state.decision_has_cancel = extract_decision_has_cancel(message)
                if state.decision_action != "place":
                    state.fill_order_at = None
            elif action == "fill_order":
                state.fill_order_at = datetime.now()

        def _clear_sheet_validation_time(self, sheet_name: str, action: str) -> None:
            state = self.sheet_states.setdefault(sheet_name, SheetValidationState())
            if action == "dry_run":
                state.dry_run_at = None
                state.decision_side = None
                state.decision_action = None
                state.decision_has_cancel = False
                state.fill_order_at = None
            elif action == "fill_order":
                state.fill_order_at = None

        def append_log(self, line: str) -> None:
            live_order = parse_live_order_result_line(line)
            if live_order is not None and self.current_action == "live_order":
                status, message = live_order
                target_sheet = self.current_running_sheet or self.current_sheet()
                self.seen_strategy_result = True
                self.mark_sheet(target_sheet, status, message, self.current_action)
                self.live_order_notification_sent = True
                self.send_console_telegram(
                    console_notification_text("live_order", target_sheet, status == "OK", message),
                    order=status == "OK",
                    failure=status != "OK",
                )
            parsed = parse_strategy_result_line(line)
            if parsed is not None:
                status, sheet_name, message = parsed
                self.seen_strategy_result = True
                self.mark_sheet(sheet_name, status, message, self.current_action)
            self.log_lines.append(line)
            if len(self.log_lines) > MAX_LOG_LINES:
                self.log_lines = self.log_lines[-MAX_LOG_LINES:]
                self.log_text.delete("1.0", tk.END)
                self.log_text.insert(tk.END, "\n".join(self.log_lines) + "\n")
            else:
                self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)

        def run(self) -> int:
            self.root.mainloop()
            return 0

    return TkConsole().run()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kiwoom Global Trader Console")
    parser.add_argument("--config", default="config.live.json")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    raise SystemExit(_run_qt_app(args.config))


if __name__ == "__main__":
    main()
