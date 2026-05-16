from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.console import (
    SheetValidationState,
    action_updates_sheet_rows,
    build_cli_command,
    build_public_csv_url,
    console_notification_text,
    ensure_config_file,
    extract_spreadsheet_id,
    latest_order_summaries,
    line_indicates_failure,
    line_indicates_hts_missing,
    live_unlock_status,
    load_sheet_tabs,
    next_window_start,
    extract_decision_action,
    extract_decision_has_cancel,
    extract_decision_side,
    is_time_in_window,
    parse_live_order_result_line,
    parse_hhmm,
    parse_google_sheet_tabs_from_html,
    parse_google_sheet_tab_names_from_html,
    parse_strategy_result_line,
    read_console_settings,
    remaining_time_text,
    resolve_credential_path,
    save_public_csv_tabs_to_config,
    save_public_xlsx_to_config,
    save_account_dropdown_order_from_config,
    save_service_account_key_to_config,
    service_account_email_from_file,
    hidden_console_subprocess_kwargs,
    should_display_command_output_line,
    should_run_daily_time,
    should_auto_fill_after_dry_run,
    should_auto_live_after_fill_order,
    write_console_settings,
)
from datetime import datetime, timedelta


class ConsoleTest(unittest.TestCase):
    def test_ensure_config_file_creates_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.live.json"
            created = ensure_config_file(path)
            data = json.loads(created.read_text(encoding="utf-8"))

        self.assertEqual(created, path)
        self.assertIn("google", data)
        self.assertIn("trading", data)

    def test_load_sheet_tabs_from_public_csv_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "google": {
                            "public_csv_tabs": {
                                "LABU55": "https://example.test/labu.csv",
                                "SOXL55": "https://example.test/soxl.csv",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            sheets = load_sheet_tabs(path)

        self.assertEqual([sheet.name for sheet in sheets], ["LABU55", "SOXL55"])
        self.assertEqual(sheets[0].source, "https://example.test/labu.csv")

    def test_extract_spreadsheet_id_from_url_or_id(self) -> None:
        self.assertEqual(
            extract_spreadsheet_id("https://docs.google.com/spreadsheets/d/abc_123-XYZ/edit#gid=11"),
            "abc_123-XYZ",
        )
        self.assertEqual(extract_spreadsheet_id("abc_123-XYZ_45678901234567890"), "abc_123-XYZ_45678901234567890")

    def test_parse_google_sheet_tabs_from_html(self) -> None:
        html = '''
        {"sheetId":2140122702,"title":"LABU55"}
        {"title":"SOXL55","sheetId":864771651}
        '''
        tabs = parse_google_sheet_tabs_from_html("spreadsheet-id", html)

        self.assertEqual(tabs["LABU55"], build_public_csv_url("spreadsheet-id", "2140122702"))
        self.assertEqual(tabs["SOXL55"], build_public_csv_url("spreadsheet-id", "864771651"))

    def test_parse_google_sheet_tab_names_from_public_html(self) -> None:
        html = """
        <div class="docs-sheet-tab-caption">TQQQ50</div>
        <div class="goog-inline-block docs-sheet-tab-caption">BITU55</div>
        <div class="docs-sheet-tab-caption">Template_reselt</div>
        """

        self.assertEqual(parse_google_sheet_tab_names_from_html(html), ["TQQQ50", "BITU55", "Template_reselt"])

    def test_save_public_csv_tabs_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"google": {"allow_insecure_ssl": True}, "trading": {"dry_run": True}}), encoding="utf-8")

            save_public_csv_tabs_to_config(
                path,
                "https://docs.google.com/spreadsheets/d/test-sheet-id/edit",
                {"LABU55": "https://example.test/labu.csv"},
            )
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["google"]["spreadsheet_id"], "test-sheet-id")
        self.assertEqual(data["google"]["public_csv_tabs"]["LABU55"], "https://example.test/labu.csv")
        self.assertTrue(data["trading"]["dry_run"])

    def test_save_public_xlsx_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"google": {"public_csv_tabs": {"OLD": "https://example.test/old.csv"}}}),
                encoding="utf-8",
            )

            save_public_xlsx_to_config(
                path,
                "https://docs.google.com/spreadsheets/d/test-sheet-id/edit",
                "https://docs.google.com/spreadsheets/d/test-sheet-id/export?format=xlsx",
            )
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["google"]["spreadsheet_id"], "test-sheet-id")
        self.assertEqual(data["google"]["public_csv_tabs"], {})
        self.assertEqual(data["google"]["public_xlsx_url"], "https://docs.google.com/spreadsheets/d/test-sheet-id/export?format=xlsx")

    def test_save_account_dropdown_order_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"google": {"public_xlsx_url": "https://example.test/book.xlsx"}}),
                encoding="utf-8",
            )
            strategies = [
                SimpleNamespace(account_no="61078617"),
                SimpleNamespace(account_no="61078631"),
                SimpleNamespace(account_no="61078617"),
            ]

            with patch("src.console.PublicXlsxSheetReader") as reader_cls:
                reader_cls.return_value.read_strategies.return_value = strategies
                accounts = save_account_dropdown_order_from_config(path)
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(accounts, ["61078617", "61078631"])
        self.assertEqual(data["broker"]["account_dropdown_order"], ["61078617", "61078631"])

    def test_service_account_email_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.json"
            path.write_text(json.dumps({"client_email": "kiwoom@example.iam.gserviceaccount.com"}), encoding="utf-8")

            self.assertEqual(service_account_email_from_file(path), "kiwoom@example.iam.gserviceaccount.com")

    def test_save_service_account_key_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            key_path = root / "downloaded-key.json"
            config_path.write_text(json.dumps({"google": {"spreadsheet_id": "sheet-id"}}), encoding="utf-8")
            key_path.write_text(json.dumps({"client_email": "kiwoom@example.iam.gserviceaccount.com"}), encoding="utf-8")

            destination, email = save_service_account_key_to_config(config_path, key_path)
            data = json.loads(config_path.read_text(encoding="utf-8"))
            copied_email = service_account_email_from_file(destination)

        self.assertEqual(email, "kiwoom@example.iam.gserviceaccount.com")
        self.assertEqual(data["google"]["credential_file"], "data/credentials.json")
        self.assertEqual(destination.name, "credentials.json")
        self.assertEqual(copied_email, "kiwoom@example.iam.gserviceaccount.com")

    def test_resolve_credential_path_relative_to_config(self) -> None:
        path = resolve_credential_path("C:/project/config.live.json", "data/credentials.json")

        self.assertEqual(path.as_posix(), "C:/project/data/credentials.json")

    def test_build_dry_run_command_for_one_sheet(self) -> None:
        command = build_cli_command("config.live.json", "dry_run", "LABU55")
        self.assertEqual(
            command,
            [
                sys.executable,
                "-m",
                "src.main",
                "--config",
                "config.live.json",
                "--once",
                "--dry-run",
                "--only-sheet",
                "LABU55",
            ],
        )

    def test_build_command_uses_packaged_exe_in_frozen_mode(self) -> None:
        with patch("src.console.is_frozen_app", return_value=True), patch("pathlib.Path.exists", return_value=False):
            command = build_cli_command("config.live.json", "dry_run", "LABU55")

        self.assertEqual(command[:3], [sys.executable, "--cli", "--config"])
        self.assertIn("--dry-run", command)
        self.assertEqual(command[-2:], ["--only-sheet", "LABU55"])

    def test_hidden_console_subprocess_kwargs_hides_windows_console(self) -> None:
        kwargs = hidden_console_subprocess_kwargs()

        if sys.platform == "win32":
            self.assertIn("creationflags", kwargs)
            self.assertIn("startupinfo", kwargs)
        else:
            self.assertEqual(kwargs, {})

    def test_build_fill_order_command(self) -> None:
        command = build_cli_command("config.live.json", "fill_order", "SOXL55")
        self.assertIn("--dry-run-fill-order", command)
        self.assertEqual(command[-2:], ["--only-sheet", "SOXL55"])

    def test_build_hts_check_command(self) -> None:
        command = build_cli_command("config.live.json", "hts_check")
        self.assertEqual(command[-1], "--probe-main-toolbar")

    def test_build_live_order_command(self) -> None:
        command = build_cli_command("config.live.json", "live_order", "LABU55", "sell")
        self.assertEqual(command[-4:], ["--place-decision-order", "sell", "--only-sheet", "LABU55"])

    def test_line_indicates_failure_for_skip_fail(self) -> None:
        self.assertTrue(line_indicates_failure("SKIP/FAIL LABU55: get_balance failed after 1 attempts"))
        self.assertTrue(line_indicates_failure("RuntimeError: HTS main window is not open"))
        self.assertFalse(line_indicates_failure("OK LABU55: tier=6 actions=place:sell:195.79:5"))

    def test_line_indicates_hts_missing(self) -> None:
        self.assertTrue(line_indicates_hts_missing("SKIP/FAIL LABU55: HTS main window is not open"))
        self.assertTrue(line_indicates_hts_missing("failed while typing 2150 into search box"))
        self.assertFalse(line_indicates_hts_missing("OK LABU55: tier=6 actions=place:sell:195.79:5"))

    def test_should_display_command_output_line_hides_noisy_debug(self) -> None:
        self.assertFalse(should_display_command_output_line("  [menu] item 10: '복사(&Z)'"))
        self.assertFalse(should_display_command_output_line("  [copy row_y=0.46] step2 result: ok"))
        self.assertTrue(should_display_command_output_line("OK LABU55: tier=6 actions=place:sell:195.79:5"))
        self.assertTrue(should_display_command_output_line("SKIP/FAIL LABU55: get_balance failed after 1 attempts"))

    def test_parse_strategy_result_line_ok(self) -> None:
        parsed = parse_strategy_result_line("OK LABU55: tier=6 actions=place:sell:195.79:5")

        self.assertEqual(parsed, ("OK", "LABU55", "tier=6 actions=place:sell:195.79:5"))

    def test_extract_decision_side(self) -> None:
        self.assertEqual(extract_decision_side("tier=6 actions=place:sell:195.79:5"), "sell")
        self.assertEqual(extract_decision_side("tier=3 actions=place:buy:17.71:68"), "buy")
        self.assertEqual(extract_decision_side("tier=3 actions=keep:buy"), "buy")
        self.assertIsNone(extract_decision_side("tier=6 actions="))

    def test_extract_decision_action(self) -> None:
        self.assertEqual(extract_decision_action("tier=6 actions=place:sell:195.79:5"), "place")
        self.assertEqual(extract_decision_action("tier=3 actions=keep:buy"), "keep")
        self.assertEqual(extract_decision_action("tier=49 actions=cancel:sell:18027"), "cancel")
        self.assertIsNone(extract_decision_action("tier=6 actions="))

    def test_extract_decision_has_cancel(self) -> None:
        self.assertTrue(extract_decision_has_cancel("tier=6 actions=cancel:buy:15864,place:sell:195.79:5"))
        self.assertFalse(extract_decision_has_cancel("tier=3 actions=keep:buy"))

    def test_parse_hhmm(self) -> None:
        self.assertEqual(parse_hhmm("23:30").hour, 23)
        self.assertEqual(parse_hhmm("23:30").minute, 30)

    def test_is_time_in_window_same_day(self) -> None:
        self.assertTrue(is_time_in_window(parse_hhmm("10:00"), parse_hhmm("09:00"), parse_hhmm("15:30")))
        self.assertFalse(is_time_in_window(parse_hhmm("16:00"), parse_hhmm("09:00"), parse_hhmm("15:30")))

    def test_is_time_in_window_cross_midnight(self) -> None:
        self.assertTrue(is_time_in_window(parse_hhmm("23:30"), parse_hhmm("22:30"), parse_hhmm("05:55")))
        self.assertTrue(is_time_in_window(parse_hhmm("04:00"), parse_hhmm("22:30"), parse_hhmm("05:55")))
        self.assertFalse(is_time_in_window(parse_hhmm("12:00"), parse_hhmm("22:30"), parse_hhmm("05:55")))

    def test_next_window_start_same_day_window(self) -> None:
        now = datetime(2026, 5, 11, 22, 30, 0)

        next_start = next_window_start(now, parse_hhmm("06:30"), parse_hhmm("22:00"))

        self.assertEqual(next_start, datetime(2026, 5, 12, 6, 30, 0))

    def test_next_window_start_cross_midnight_window(self) -> None:
        now = datetime(2026, 5, 11, 12, 0, 0)

        next_start = next_window_start(now, parse_hhmm("22:30"), parse_hhmm("05:55"))

        self.assertEqual(next_start, datetime(2026, 5, 11, 22, 30, 0))

    def test_remaining_time_text(self) -> None:
        now = datetime(2026, 5, 10, 10, 0, 0)

        self.assertEqual(remaining_time_text(now + timedelta(seconds=75), now), "00:01:15")
        self.assertEqual(remaining_time_text(now - timedelta(seconds=1), now), "00:00:00")
        self.assertEqual(remaining_time_text(None, now), "-")

    def test_should_auto_fill_after_dry_run_requires_order_side(self) -> None:
        self.assertTrue(should_auto_fill_after_dry_run(True, SheetValidationState(decision_side="sell", decision_action="place")))
        self.assertTrue(should_auto_fill_after_dry_run(True, SheetValidationState(decision_side="buy", decision_action="place")))
        self.assertFalse(should_auto_fill_after_dry_run(True, SheetValidationState(decision_side="buy", decision_action="keep")))
        self.assertFalse(should_auto_fill_after_dry_run(True, SheetValidationState()))
        self.assertFalse(should_auto_fill_after_dry_run(False, SheetValidationState(decision_side="sell", decision_action="place")))

    def test_should_auto_live_after_fill_order_requires_flag_and_place_decision(self) -> None:
        state = SheetValidationState(decision_side="sell", decision_action="place")

        self.assertTrue(should_auto_live_after_fill_order(True, True, state))
        self.assertFalse(should_auto_live_after_fill_order(True, False, state))
        self.assertFalse(should_auto_live_after_fill_order(False, True, state))
        self.assertFalse(should_auto_live_after_fill_order(True, True, SheetValidationState(decision_side="sell", decision_action="keep")))

    def test_console_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "console_settings.json"
            write_console_settings(
                path,
                {
                    "auto_schedule": {
                        "start": "22:30",
                        "end": "05:55",
                        "interval_sec": 90,
                    }
                },
            )

            settings = read_console_settings(path)

        self.assertEqual(settings["auto_schedule"]["start"], "22:30")
        self.assertEqual(settings["auto_schedule"]["end"], "05:55")
        self.assertEqual(settings["auto_schedule"]["interval_sec"], 90)
        self.assertFalse(settings["auto_schedule"]["live_order_enabled"])
        self.assertFalse(settings["auto_schedule"]["auto_start_enabled"])
        self.assertEqual(settings["auto_sheets"]["enabled"], [])
        self.assertFalse(settings["hts_auto"]["enabled"])
        self.assertEqual(settings["hts_auto"]["launch_time"], "22:40")

    def test_console_settings_falls_back_for_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "console_settings.json"
            path.write_text(
                json.dumps({"auto_schedule": {"start": "bad", "end": "99:99", "interval_sec": 0}}),
                encoding="utf-8",
            )

            settings = read_console_settings(path)

        self.assertEqual(settings["auto_schedule"]["start"], "00:00")
        self.assertEqual(settings["auto_schedule"]["end"], "23:59")
        self.assertEqual(settings["auto_schedule"]["interval_sec"], 120)
        self.assertEqual(settings["auto_sheets"]["enabled"], [])
        self.assertNotIn("login_time", settings["hts_auto"])

    def test_console_settings_persists_auto_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "console_settings.json"
            write_console_settings(
                path,
                {
                    "auto_schedule": {"start": "22:30", "end": "05:55", "interval_sec": 90},
                    "auto_sheets": {"enabled": ["LABU55", "SOXL55"]},
                },
            )

            settings = read_console_settings(path)

        self.assertEqual(settings["auto_sheets"]["enabled"], ["LABU55", "SOXL55"])

    def test_console_settings_persists_auto_live_order_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "console_settings.json"
            write_console_settings(
                path,
                {
                    "auto_schedule": {
                        "start": "22:30",
                        "end": "05:55",
                        "interval_sec": 90,
                        "live_order_enabled": True,
                    },
                },
            )

            settings = read_console_settings(path)

        self.assertTrue(settings["auto_schedule"]["live_order_enabled"])

    def test_console_settings_persists_auto_start_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "console_settings.json"
            write_console_settings(
                path,
                {
                    "auto_schedule": {
                        "start": "22:30",
                        "end": "05:55",
                        "interval_sec": 90,
                        "auto_start_enabled": True,
                    },
                },
            )

            settings = read_console_settings(path)

        self.assertTrue(settings["auto_schedule"]["auto_start_enabled"])

    def test_console_settings_persists_hts_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "console_settings.json"
            write_console_settings(
                path,
                {
                    "auto_schedule": {"start": "22:30", "end": "05:55", "interval_sec": 90},
                    "hts_auto": {
                        "enabled": True,
                        "exe_path": "C:/Kiwoom/Global/bin/hero.exe",
                        "launch_time": "22:40",
                        "simple_pin": "123456",
                    },
                },
            )

            settings = read_console_settings(path)

        self.assertTrue(settings["hts_auto"]["enabled"])
        self.assertEqual(settings["hts_auto"]["exe_path"], "C:/Kiwoom/Global/bin/hero.exe")
        self.assertEqual(settings["hts_auto"]["simple_pin"], "123456")

    def test_should_run_daily_time_runs_once_per_day_after_time(self) -> None:
        now = datetime(2026, 5, 10, 22, 41, 0)

        self.assertTrue(should_run_daily_time(now, parse_hhmm("22:40"), None))
        self.assertFalse(should_run_daily_time(now, parse_hhmm("22:40"), "2026-05-10"))
        self.assertFalse(should_run_daily_time(now, parse_hhmm("22:50"), None))
        self.assertFalse(should_run_daily_time(datetime(2026, 5, 10, 23, 30, 0), parse_hhmm("22:40"), None))

    def test_parse_strategy_result_line_failure(self) -> None:
        parsed = parse_strategy_result_line("SKIP/FAIL SOXL55: get_balance failed after 1 attempts")

        self.assertEqual(parsed, ("ERROR", "SOXL55", "get_balance failed after 1 attempts"))

    def test_parse_live_order_result_line(self) -> None:
        parsed = parse_live_order_result_line(
            "PLACE_DECISION_ORDER success=True side=sell tier=6 price=195.7900 qty=5 order-id= message=clicked 留ㅻ룄 button"
        )

        self.assertIsNotNone(parsed)
        status, message = parsed
        self.assertEqual(status, "OK")
        self.assertIn("실주문 매도 195.7900 x 5", message)
        self.assertIn("tier=6", message)

    def test_parse_live_order_result_line_accepts_order_id_underscore(self) -> None:
        parsed = parse_live_order_result_line(
            "PLACE_DECISION_ORDER success=True side=sell tier=6 price=195.7900 qty=5 order_id=43079 message=accepted"
        )

        self.assertIsNotNone(parsed)
        status, message = parsed
        self.assertEqual(status, "OK")
        self.assertIn("주문번호=43079", message)

    def test_line_indicates_failure_for_live_order_failure(self) -> None:
        self.assertTrue(line_indicates_failure("PLACE_DECISION_ORDER success=False side=sell tier=6 price=195.7900 qty=5 order-id= message=failed"))

    def test_latest_order_summaries_filters_sheet_and_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            (logs / "orders_20260509.csv").write_text(
                "\n".join(
                    [
                        "timestamp,sheet_name,account_no,symbol,current_qty,current_tier,action,side,price,qty,order_id,result,message,telegram_sent",
                        "2026-05-09T15:40:00,LABU55,12345678,LABU,,6,dry_run_place,sell,195.79,5,,success,dry-run,True",
                        "2026-05-09T15:41:02,LABU55,12345678,LABU,,6,dry_run_fill_order,sell,195.79,5,,success,form populated without clicking order button,False",
                    ]
                ),
                encoding="utf-8",
            )

            summaries = latest_order_summaries(tmp, sheet_name="LABU55", action="dry_run_fill_order")

        self.assertEqual(len(summaries), 1)
        self.assertIn("LABU55", summaries[0])
        self.assertIn("form populated without clicking order button", summaries[0])

    def test_latest_order_summaries_sorts_across_log_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            header = "timestamp,sheet_name,account_no,symbol,current_qty,current_tier,action,side,price,qty,order_id,result,message,telegram_sent"
            (logs / "orders_20260511.csv").write_text(
                "\n".join(
                    [
                        header,
                        "2026-05-11T21:07:20,BITU55,87654321,BITU,,49,dry_run_fill_order,sell,16.45,57,,success,current,False",
                    ]
                ),
                encoding="utf-8",
            )
            (logs / "orders_20260509.csv").write_text(
                "\n".join(
                    [
                        header,
                        "2026-05-09T18:00:20,BITU55,87654321,BITU,,49,dry_run_fill_order,sell,15.96,59,,success,old,False",
                    ]
                ),
                encoding="utf-8",
            )

            summaries = latest_order_summaries(tmp, sheet_name="BITU55", action="dry_run_fill_order", limit=1)

        self.assertEqual(len(summaries), 1)
        self.assertIn("2026-05-11T21:07:20", summaries[0])
        self.assertIn("16.45 x 57", summaries[0])

    def test_live_unlock_status_requires_all_safety_conditions(self) -> None:
        now = datetime(2026, 5, 9, 17, 10, 0)
        state = SheetValidationState(
            dry_run_at=now - timedelta(minutes=2),
            fill_order_at=now - timedelta(minutes=1),
            decision_side="sell",
        )

        ready, reason = live_unlock_status(False, now - timedelta(minutes=3), state, now=now)

        self.assertTrue(ready)
        self.assertIn("실주문 조건 충족", reason)

    def test_live_unlock_status_rejects_missing_fill_order_validation(self) -> None:
        now = datetime(2026, 5, 9, 17, 10, 0)
        state = SheetValidationState(dry_run_at=now - timedelta(minutes=2), decision_side="sell")

        ready, reason = live_unlock_status(True, now - timedelta(minutes=3), state, now=now)

        self.assertFalse(ready)
        self.assertIn("주문창 검증 필요", reason)

    def test_live_unlock_status_rejects_expired_validation(self) -> None:
        now = datetime(2026, 5, 9, 17, 10, 0)
        state = SheetValidationState(
            dry_run_at=now - timedelta(minutes=31),
            fill_order_at=now - timedelta(minutes=1),
            decision_side="sell",
        )

        ready, reason = live_unlock_status(True, now - timedelta(minutes=3), state, now=now)

        self.assertFalse(ready)
        self.assertIn("dry-run 검증 만료", reason)

    def test_live_unlock_status_rejects_missing_decision_side(self) -> None:
        now = datetime(2026, 5, 9, 17, 10, 0)
        state = SheetValidationState(
            dry_run_at=now - timedelta(minutes=2),
            fill_order_at=now - timedelta(minutes=1),
        )

        ready, reason = live_unlock_status(False, now - timedelta(minutes=3), state, now=now)

        self.assertFalse(ready)
        self.assertIn("주문 방향 확인 필요", reason)

    def test_live_unlock_status_rejects_keep_action(self) -> None:
        now = datetime(2026, 5, 9, 17, 10, 0)
        state = SheetValidationState(
            dry_run_at=now - timedelta(minutes=2),
            fill_order_at=now - timedelta(minutes=1),
            decision_action="keep",
        )

        ready, reason = live_unlock_status(False, now - timedelta(minutes=3), state, now=now)

        self.assertFalse(ready)
        self.assertIn("신규 주문 없음", reason)

    def test_live_unlock_status_allows_keep_with_cancel_cleanup(self) -> None:
        now = datetime(2026, 5, 9, 17, 10, 0)
        state = SheetValidationState(
            dry_run_at=now - timedelta(minutes=2),
            decision_action="keep",
            decision_side="sell",
            decision_has_cancel=True,
        )

        ready, reason = live_unlock_status(False, now - timedelta(minutes=3), state, now=now)

        self.assertTrue(ready)
        self.assertIn("기존 주문 정리", reason)

    def test_only_order_cycle_actions_update_sheet_rows(self) -> None:
        self.assertTrue(action_updates_sheet_rows("dry_run"))
        self.assertTrue(action_updates_sheet_rows("fill_order"))
        self.assertFalse(action_updates_sheet_rows("hts_check"))
        self.assertFalse(action_updates_sheet_rows("list"))

    def test_console_notification_text(self) -> None:
        text = console_notification_text("fill_order", "LABU55", False, "failed")

        self.assertIn("콘솔 주문창 검증 실패", text)
        self.assertIn("시트: LABU55", text)
        self.assertIn("내용: failed", text)


if __name__ == "__main__":
    unittest.main()

