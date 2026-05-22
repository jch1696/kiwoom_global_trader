from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.brokers.kiwoom_hybrid import KiwoomHybridBroker
from src.models import OrderRequest, Side


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


class _FakeAccountCombo(_FakeCombo):
    def __init__(self, text: str) -> None:
        self.text = text

    def window_text(self) -> str:
        return self.text


class _FakeButton:
    def __init__(self, title: str, checked: int = 0, rect: _RectValue | None = None) -> None:
        self.title = title
        self.checked = checked
        self.clicked = False
        self.rect = rect or _Rect()

    def is_visible(self) -> bool:
        return True

    def rectangle(self):
        return self.rect

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

    def descendants(self, class_name=None):
        if class_name is not None:
            return [control for control in self.controls if getattr(control, "class_name", lambda: "")() == class_name]
        return self.controls


class _FakeEdit:
    def __init__(self, rect: _RectValue) -> None:
        self.rect = rect

    def is_visible(self) -> bool:
        return True

    def rectangle(self) -> _RectValue:
        return self.rect

    def class_name(self) -> str:
        return "Edit"

    def window_text(self) -> str:
        return ""


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

    def test_verify_order_created_fails_when_open_order_missing(self) -> None:
        broker = KiwoomHybridBroker()
        order = OrderRequest(
            account_no="12345678",
            symbol="LABU",
            side=Side.SELL,
            price=173.62,
            qty=2,
            order_type="limit",
        )

        with (
            patch.object(broker, "_handle_order_rejected_popup", return_value=None),
            patch.object(broker, "get_open_orders", return_value=[]),
        ):
            result = broker._verify_order_created(order)

        self.assertEqual(result["created"], "false")
        self.assertIn("matching open order", result["message"])

    def test_verify_order_created_fails_when_open_order_read_never_succeeds(self) -> None:
        broker = KiwoomHybridBroker()
        order = OrderRequest(
            account_no="12345678",
            symbol="LABU",
            side=Side.SELL,
            price=173.62,
            qty=2,
            order_type="limit",
        )

        with (
            patch.object(broker, "_handle_order_rejected_popup", return_value=None),
            patch.object(broker, "get_open_orders", side_effect=RuntimeError("HTS main window is not open")),
        ):
            result = broker._verify_order_created(order)

        self.assertEqual(result["created"], "false")
        self.assertIn("HTS main window is not open", result["message"])

    def test_handle_order_rejected_popup_treats_any_info_popup_as_failure(self) -> None:
        broker = KiwoomHybridBroker()

        with (
            patch.object(broker, "_handle_foreground_info_popup", return_value=None),
            patch.object(broker, "_find_info_popup_handle", return_value=1234),
            patch.object(broker, "_window_text_summary_by_handle", return_value="예수금이 부족합니다"),
            patch.object(broker, "_safe_handle_text", return_value="안내"),
            patch.object(broker, "_click_child_button_by_text", return_value=True),
            patch.object(broker, "_post_ok_to_window", return_value=False),
        ):
            result = broker._handle_order_rejected_popup()

        self.assertEqual(result, "예수금이 부족합니다")

    def test_place_order_fails_when_confirmed_but_open_order_missing(self) -> None:
        broker = KiwoomHybridBroker()
        order = OrderRequest(
            account_no="12345678",
            symbol="LABU",
            side=Side.SELL,
            price=173.62,
            qty=2,
            order_type="limit",
        )

        window = Mock()
        with (
            patch.object(broker, "_handle_order_rejected_popup", return_value=None),
            patch.object(broker, "_raise_if_order_unavailable_popup", return_value=None),
            patch.object(broker, "reset_mini_order_window", return_value=window),
            patch.object(broker, "_ensure_place_mode", return_value=None),
            patch.object(
                broker,
                "_locate_place_form",
                return_value={
                    "account_edit": object(),
                    "symbol_edit": object(),
                    "order_type_edit": object(),
                    "price_edit": object(),
                    "qty_edit": object(),
                    "buy_button": object(),
                    "sell_button": object(),
                },
            ),
            patch.object(broker, "_place_form_ready", return_value=True),
            patch.object(broker, "_ensure_auto_qty_unchecked", return_value="already_unchecked"),
            patch.object(broker, "_set_account_value", return_value=None),
            patch.object(broker, "_set_edit_value", return_value=None),
            patch.object(broker, "_verify_place_form_values", return_value=None),
            patch.object(broker, "_click_order_button_attempts", return_value=iter(["mouse"])),
            patch.object(broker, "_handle_place_confirmation", return_value={"found": "true", "clicked": "true", "message": "confirmed"}),
            patch.object(
                broker,
                "_verify_order_created",
                return_value={"created": "false", "message": "matching open order was not detected"},
            ),
            patch.object(broker, "close_window_by_re", return_value=None),
        ):
            result = broker.place_order(order)

        self.assertFalse(result.success)
        self.assertIn("order was not accepted", result.message)

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

    def test_click_order_button_prefers_mouse_coordinates(self) -> None:
        broker = KiwoomHybridBroker()
        button = _FakeButton("매수")

        with patch("pywinauto.mouse.click") as click:
            method = broker._click_order_button(button)

        self.assertEqual(method, "mouse")
        click.assert_called_once()
        self.assertFalse(button.clicked)

    def test_find_main_search_button_near_search_edit(self) -> None:
        broker = KiwoomHybridBroker()
        search_edit = _FakeEdit(_RectValue(100, 10, 140, 24))
        far_button = _FakeButton("\ud654\uba74\ucc3e\uae30", rect=_RectValue(500, 10, 520, 26))
        near_button = _FakeButton("\ud654\uba74\ucc3e\uae30", rect=_RectValue(145, 10, 165, 26))
        window = _FakeWindow([far_button, search_edit, near_button])

        self.assertIs(broker._find_main_search_button(window, search_edit), near_button)

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

    def test_account_dropdown_capture_includes_current_account_for_row_estimate(self) -> None:
        broker = KiwoomHybridBroker(["61116793", "61116859"])
        clicked: list[tuple[int, int]] = []

        with (
            patch("src.brokers.kiwoom_hybrid.win32gui.GetClassName", return_value="SysListView32"),
            patch.object(broker, "_hwnd_rect", return_value=(100, 100, 270, 160)),
            patch.object(broker, "_is_near_account_dropdown", return_value=True),
            patch.object(broker, "_capture_account_dropdown", return_value=(None, "")),
            patch.object(broker, "_mouse_click_screen", side_effect=lambda coords: clicked.append(coords)),
        ):
            selected = broker._select_account_from_dropdown_capture(
                1234,
                _FakeAccountCombo("6437-4443 설유라"),
                "6111-6859",
                [],
            )

        self.assertTrue(selected)
        self.assertEqual(clicked, [(142, 130)])


if __name__ == "__main__":
    unittest.main()
