from __future__ import annotations

import re
from pathlib import Path
from collections import defaultdict
from typing import Iterable

from .config import GoogleConfig
from .models import SettlementRecord


SETTLEMENT_HEADERS = [
    "업데이트",
    "날짜",
    "시트명",
    "종목",
    "티어",
    "총티어",
    "잔고량",
    "투자금",
    "1티어",
    "예수금",
    "평가금",
    "잔고수익",
    "매수예정",
    "인출가능",
    "아바타 수익",
    "매수",
    "매도",
]


def extract_spreadsheet_id(value: str) -> str:
    if not value:
        return ""
    if "/spreadsheets/d/" not in value:
        return value.strip()
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", value)
    return match.group(1) if match else ""


def resolve_spreadsheet_id(config: GoogleConfig) -> str:
    spreadsheet_id = extract_spreadsheet_id(config.spreadsheet_id)
    if spreadsheet_id:
        return spreadsheet_id
    for url in config.public_csv_tabs.values():
        spreadsheet_id = extract_spreadsheet_id(url)
        if spreadsheet_id:
            return spreadsheet_id
    return ""


def build_google_settlement_writer(config: GoogleConfig, base_dir: str | Path) -> GoogleSettlementSheetWriter | None:
    spreadsheet_id = resolve_spreadsheet_id(config)
    if not spreadsheet_id:
        return None

    credential_path = Path(config.credential_file)
    if not credential_path.is_absolute():
        credential_path = Path(base_dir) / credential_path
    if not credential_path.exists():
        return None

    return GoogleSettlementSheetWriter(
        spreadsheet_id=spreadsheet_id,
        credential_path=credential_path,
        sheet_name=config.settlement_sheet_name,
    )


class GoogleSettlementSheetWriter:
    def __init__(self, spreadsheet_id: str, credential_path: str | Path, sheet_name: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.credential_path = Path(credential_path)
        self.sheet_name = sheet_name
        self._service = None

    def append_records(self, records: Iterable[SettlementRecord]) -> None:
        grouped: dict[str, list[SettlementRecord]] = defaultdict(list)
        for record in records:
            grouped[self._sheet_name_for_record(record)].append(record)
        if not grouped:
            return

        service = self._get_service()
        for sheet_name, sheet_records in grouped.items():
            self._ensure_sheet(service, sheet_name)
            service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{_quote_sheet_name(sheet_name)}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [settlement_record_to_row(record) for record in sheet_records]},
            ).execute()

    def _get_service(self):
        if self._service is not None:
            return self._service

        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(str(self.credential_path), scopes=scopes)
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return self._service

    def _ensure_sheet(self, service, sheet_name: str) -> None:
        metadata = service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
        if sheet_name not in titles:
            service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            ).execute()

        header_range = f"{_quote_sheet_name(sheet_name)}!A1:Q1"
        values = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=header_range)
            .execute()
            .get("values", [])
        )
        if not values:
            service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=header_range,
                valueInputOption="USER_ENTERED",
                body={"values": [SETTLEMENT_HEADERS]},
            ).execute()

    def _sheet_name_for_record(self, record: SettlementRecord) -> str:
        if record.sheet_name:
            return f"{record.sheet_name}정산"
        return self.sheet_name


def settlement_record_to_row(record: SettlementRecord) -> list[object]:
    return [
        record.updated_at_kst.strftime("%Y-%m-%d %H:%M:%S"),
        _short_date(record.trade_date_et),
        record.sheet_name,
        record.symbol,
        record.final_tier,
        record.total_tiers,
        record.final_qty,
        record.investment_usd,
        record.base_price,
        "",
        record.valuation,
        record.pnl,
        record.next_buy,
        "",
        "",
        record.buy_summary,
        record.sell_summary,
    ]


def _quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def _short_date(value: str) -> str:
    match = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", value)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return value
