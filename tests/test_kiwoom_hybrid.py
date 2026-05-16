from __future__ import annotations

import unittest
from unittest.mock import patch

from src.brokers.kiwoom_hybrid import KiwoomHybridBroker


class _Rect:
    left = 0
    top = 0
    right = 100
    bottom = 30

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top


class _RectValue:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top


class _FakeCombo:
    def rectangle(self) -> _RectValue:
        return _RectValue(100, 80, 220, 100)


class _FakeButton:
    def __init__(self, title: str, checked: int = 0) -> None:
        self.title = title
        self.checked = checked
        self.clicked = False

    def is_visible(self) -> bool:
        return True

    def rectangle(self) -> _Rect:
        return _Rect()

    def class_name(self) -> str:
        return "Button"

    def window_text(self) -> str:
        return self.title

    def get_check_state(self) -> int:
        return self.checked

    def click_input(self) -> None:
        self.clicked = True
        self.checked = 0


class _FakeWindow:
    def __init__(self, controls) -> None:
        self.controls = controls

    def descendants(self):
        return self.controls


class KiwoomHybridBrokerParseTest(unittest.TestCase):
    def test_parse_balance_row_from_tab_delimited_clipboard(self) -> None:
        headers = [
            "종목명",
            KiwoomHybridBroker.HEADER_PNL,
            "평가손익률",
            KiwoomHybridBroker.HEADER_AVG_PRICE,
            KiwoomHybridBroker.HEADER_QTY,
            KiwoomHybridBroker.HEADER_AVAILABLE_QTY,
            KiwoomHybridBroker.HEADER_CURRENT_PRICE,
            "전일",
            "금일",
            "매입금액",
            KiwoomHybridBroker.HEADER_VALUATION,
            "국가",
            "거래소",
            KiwoomHybridBroker.HEADER_SYMBOL,
        ]
        row = [
            "미국 반도체 3배 ETF",
            "381.0999",
            "11.84%",
            "114.9489",
            "28",
            "28",
            "128.7974",
            "0",
            "4.77",
            "3218.5701",
            "3606.3272",
            "미국",
            "미국",
            "NYSOXL",
        ]
        raw = "\t".join(headers) + "\n" + "\t".join(row)

        parsed = KiwoomHybridBroker._parse_balance_row(raw, "SOXL")

        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_QTY], 28)
        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_AVAILABLE_QTY], 28)
        self.assertAlmostEqual(parsed[KiwoomHybridBroker.HEADER_AVG_PRICE], 114.9489)
        self.assertAlmostEqual(parsed[KiwoomHybridBroker.HEADER_CURRENT_PRICE], 128.7974)
        self.assertAlmostEqual(parsed[KiwoomHybridBroker.HEADER_VALUATION], 3606.3272)
        self.assertAlmostEqual(parsed[KiwoomHybridBroker.HEADER_PNL], 381.0999)

    def test_parse_balance_row_rejects_wrong_symbol(self) -> None:
        raw = "종목명\t보유량\t현재가\n미국 반도체 3배 ETF\t28\t128.7974"

        with self.assertRaises(RuntimeError):
            KiwoomHybridBroker._parse_balance_row(raw, "LABU")

    def test_parse_open_order_row_from_tab_delimited_clipboard(self) -> None:
        headers = [
            KiwoomHybridBroker.HEADER_ORDER_ID,
            KiwoomHybridBroker.HEADER_SYMBOL,
            "종목명",
            KiwoomHybridBroker.HEADER_SIDE,
            KiwoomHybridBroker.HEADER_STATUS,
            "현재가",
            KiwoomHybridBroker.HEADER_ORDER_PRICE,
            KiwoomHybridBroker.HEADER_ORIGINAL_QTY,
            "STOP",
            KiwoomHybridBroker.HEADER_REMAINING_QTY,
            "통화",
            "주문종류",
            KiwoomHybridBroker.HEADER_SUBMITTED_AT,
            "국가",
            "거래소",
            "취소주문",
            "매매구분",
        ]
        row = [
            "13353",
            "LABU",
            "S&P 바이오 3배 ETF",
            KiwoomHybridBroker.SIDE_BUY,
            "접수",
            "190.3700",
            "180.8800",
            "6",
            "",
            "6",
            "USD",
            "지정가",
            "17:14:25",
            "미국",
            "미국",
            "",
            "00",
        ]
        raw = "\t".join(headers) + "\n" + "\t".join(row)

        parsed = KiwoomHybridBroker._parse_open_order_row(raw, "LABU")

        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_ORDER_ID], "13353")
        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_SYMBOL], "LABU")
        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_SIDE], KiwoomHybridBroker.SIDE_BUY)
        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_STATUS], "접수")
        self.assertAlmostEqual(parsed[KiwoomHybridBroker.HEADER_ORDER_PRICE], 180.88)
        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_ORIGINAL_QTY], 6)
        self.assertEqual(parsed[KiwoomHybridBroker.HEADER_REMAINING_QTY], 6)

    def test_verify_order_removed_treats_empty_grid_as_removed(self) -> None:
        broker = KiwoomHybridBroker()

        with (
            patch.object(broker, "_ensure_window_open", return_value=object()),
            patch.object(broker, "_verify_window_account_selected", return_value=None),
            patch.object(broker, "_first_open_order_state", return_value="empty"),
            patch.object(broker, "close_window_by_re", return_value=None),
        ):
            result = broker._verify_order_removed("12345678", "53248")

        self.assertEqual(result["removed"], "true")
        self.assertIn("empty", result["message"])

    def test_verify_order_removed_treats_non_matching_first_row_as_removed(self) -> None:
        broker = KiwoomHybridBroker()

        with (
            patch.object(broker, "_ensure_window_open", return_value=object()),
            patch.object(broker, "_verify_window_account_selected", return_value=None),
            patch.object(broker, "_first_open_order_state", return_value="other"),
            patch.object(broker, "close_window_by_re", return_value=None),
        ):
            result = broker._verify_order_removed("12345678", "53248")

        self.assertEqual(result["removed"], "true")
        self.assertIn("no longer matches", result["message"])

    def test_find_auto_qty_checkbox_by_balance_auto_text(self) -> None:
        broker = KiwoomHybridBroker()
        button = _FakeButton("자동(잔고 100%)")
        window = _FakeWindow([_FakeButton("자동(현재가)"), button])

        self.assertIs(broker._find_auto_qty_checkbox(window), button)

    def test_ensure_auto_qty_unchecked_clicks_checked_button(self) -> None:
        broker = KiwoomHybridBroker()
        button = _FakeButton("자동(잔고 100%)", checked=1)

        status = broker._ensure_auto_qty_unchecked({"auto_qty_checkbox": button})

        self.assertEqual(status, "unchecked")
        self.assertTrue(button.clicked)
        self.assertEqual(button.checked, 0)

    def test_ensure_auto_qty_unchecked_keeps_unchecked_button(self) -> None:
        broker = KiwoomHybridBroker()
        button = _FakeButton("자동(잔고 100%)", checked=0)

        status = broker._ensure_auto_qty_unchecked({"auto_qty_checkbox": button})

        self.assertEqual(status, "already_unchecked")
        self.assertFalse(button.clicked)

    def test_set_account_dropdown_order_normalizes_and_deduplicates(self) -> None:
        broker = KiwoomHybridBroker()

        broker.set_account_dropdown_order(["61078617", "6107-8617", "bad", "61520174"])

        self.assertEqual(broker._configured_account_dropdown_items, ["6107-8617", "6152-0174"])

    def test_account_dropdown_capture_uses_estimated_rows_when_pil_capture_fails(self) -> None:
        broker = KiwoomHybridBroker(["61078617", "61078631", "61520174", "63766487"])
        clicked: list[tuple[int, int]] = []

        with (
            patch("src.brokers.kiwoom_hybrid.win32gui.GetClassName", return_value="SysListView32"),
            patch.object(broker, "_hwnd_rect", return_value=(100, 100, 270, 156)),
            patch.object(broker, "_is_near_account_dropdown", return_value=True),
            patch.object(broker, "_capture_account_dropdown", return_value=(None, "")),
            patch.object(broker, "_mouse_click_screen", side_effect=lambda coords: clicked.append(coords)),
        ):
            selected = broker._select_account_from_dropdown_capture(1234, _FakeCombo(), "6152-0174", [])

        self.assertTrue(selected)
        self.assertEqual(clicked, [(142, 135)])


if __name__ == "__main__":
    unittest.main()
