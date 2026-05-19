from __future__ import annotations

import csv
import ctypes
import io
import re
import time
from datetime import datetime
from typing import Any, Callable

import win32clipboard
import win32gui

from .base import BrokerAdapter
from ..models import Balance, CancelResult, Fill, OpenOrder, OrderRequest, OrderResult, Side


class KiwoomHybridBroker(BrokerAdapter):
    """Hybrid HTS adapter that prefers UIA plus clipboard extraction."""

    MAIN_WINDOW_TITLE = "\uC601\uC6C5\uBB38Global"
    MAIN_WINDOW_TITLES = ("\uC601\uC6C5\uBB38Global", "\uC870\uCC3D\uD638")
    BALANCE_WINDOW_CODE = "2150"
    OPEN_ORDERS_WINDOW_CODE = "2152"
    BALANCE_WINDOW_RE = r".*\[\s*2150\s*\].*" + "\uC2E4\uC2DC\uAC04\uC794\uACE0" + r".*"
    OPEN_ORDERS_WINDOW_RE = r".*\[\s*2152\s*\].*" + "\uC2E4\uC2DC\uAC04\uBBF8\uCCB4\uACB0" + r".*"
    MINI_ORDER_WINDOW_RE = r".*\[\s*2102\s*\].*"
    CANCEL_CONFIRM_WINDOW_RE = r".*" + "\uD574\uC678\uC8FC\uC2DD \uCDE8\uC18C \uC8FC\uBB38\uD655\uC778" + r".*"
    PLACE_CONFIRM_WINDOW_RE = r".*" + "\uD574\uC678\uC8FC\uC2DD.*\uC8FC\uBB38\uD655\uC778" + r".*"
    INFO_WINDOW_RE = r".*\uC548\uB0B4.*"
    COPY_MENU_RE = r"\uBCF5\uC0AC.*"

    HEADER_QTY = "\uBCF4\uC720\uB7C9"
    HEADER_AVAILABLE_QTY = "\uAC00\uB2A5\uC218\uB7C9"
    HEADER_AVG_PRICE = "\uB9E4\uC785\uAC00"
    HEADER_CURRENT_PRICE = "\uD604\uC7AC\uAC00"
    HEADER_ALT_CURRENT_PRICE = "\uD3C9\uAC00\uAC00\uACA9"
    HEADER_VALUATION = "\uD3C9\uAC00\uAE08\uC561"
    HEADER_PNL = "\uD3C9\uAC00\uC190\uC775"

    HEADER_ORDER_ID = "\uC8FC\uBB38"
    HEADER_SYMBOL = "\uC885\uBAA9\uCF54\uB4DC"
    HEADER_SIDE = "\uAD6C\uBD84"
    HEADER_STATUS = "\uC0C1\uD0DC"
    HEADER_ORDER_PRICE = "\uC8FC\uBB38\uAC00"
    HEADER_ORIGINAL_QTY = "\uC8FC\uBB38\uB7C9"
    HEADER_REMAINING_QTY = "\uBBF8\uCCB4\uACB0"
    HEADER_SUBMITTED_AT = "\uC8FC\uBB38\uC2DC\uAC04"

    SIDE_BUY = "\uB9E4\uC218"
    SIDE_SELL = "\uB9E4\uB3C4"
    CONFIRM_BUTTON_TEXT = "\uD655\uC778"
    CANCEL_BUTTON_TEXT = "\uCDE8\uC18C"
    ORDER_UNAVAILABLE_MARKERS = ("\uC8FC\uBB38\uBD88\uAC00\uB2A5", "571563")
    ORDER_TYPE_DEFAULT = "\uC9C0\uC815\uAC00"
    ACCOUNT_PATTERN = r"^\d{4}-\d{4}$"
    SYMBOL_LABEL_TEXT = "\uC885\uBAA9"
    QTY_LABEL_TEXT = "\uC218\uB7C9"
    ORDER_TYPE_LABEL_TEXT = "\uC885\uB958"
    USD_LABEL_TEXT = "(USD)"
    PRICE_LABEL_TEXTS = ("(USD)", "(HKD)", "(EUR)", "(JPY)", "(CNY)")
    BUY_BUTTON_LABEL = "\uB9E4\uC218"
    SELL_BUTTON_LABEL = "\uB9E4\uB3C4"
    MODIFY_CANCEL_BUTTON_LABEL = "\uC815\uC815/\uCDE8\uC18C"
    BUY_ACTION_BUTTON_LABEL = "\uB9E4\uC218(F9)"
    SELL_ACTION_BUTTON_LABEL = "\uB9E4\uB3C4(F12)"
    MODIFY_ACTION_BUTTON_LABEL = "\uC815\uC815(F5)"
    CANCEL_ACTION_BUTTON_LABEL = "\uCDE8\uC18C(F8)"
    ORDER_NO_LABEL_TEXT = "\uBC88\uD638"
    QUERY_BUTTON_TEXT = "\uC870\uD68C"
    AUTO_BALANCE_QTY_TEXT = "\uC790\uB3D9(\uC794\uACE0"
    MINI_ORDER_WINDOW_CODE = "2102"
    CLOSE_ALL_WINDOWS_MENU_TEXT = "\uBAA8\uB4E0 \uCC3D \uB2EB\uAE30"
    SEARCH_BOX_POINT = (0.028, 0.055)
    WINDOWS_MENU_POINT = (0.12, 0.07)

    BALANCE_ROW_X = 0.34
    BALANCE_ROW_Y = 0.50
    OPEN_ORDER_ROW_X = 0.20
    OPEN_ORDER_ROW_Y = 0.46

    CLIPBOARD_RETRY_COUNT = 5
    CLIPBOARD_RETRY_DELAY_SEC = 0.2
    POPUP_RETRY_COUNT = 10
    POPUP_RETRY_DELAY_SEC = 0.3
    POST_CANCEL_RETRY_COUNT = 5
    POST_CANCEL_RETRY_DELAY_SEC = 0.5
    POST_PLACE_RETRY_COUNT = 5
    POST_PLACE_RETRY_DELAY_SEC = 0.5
    CB_GETCOUNT = 0x0146
    CB_GETLBTEXT = 0x0148
    CB_GETLBTEXTLEN = 0x0149
    CB_SETCURSEL = 0x014E
    BM_GETCHECK = 0x00F0
    BST_UNCHECKED = 0
    LB_GETCOUNT = 0x018B
    LB_GETTEXT = 0x0189
    LB_GETTEXTLEN = 0x018A
    LB_GETITEMRECT = 0x0198
    LVM_GETITEMCOUNT = 0x1004
    LVM_GETITEMTEXTW = 0x1073
    LVM_GETITEMRECT = 0x100E
    CBN_SELCHANGE = 1
    CBN_CLOSEUP = 8
    CBN_SELENDOK = 9

    def __init__(self, account_dropdown_order: list[str] | None = None) -> None:
        self._accounts: set[str] = set()
        self._manual_mini_order_point: tuple[int, int] | None = None
        self._last_account_dropdown_items: list[str] = []
        self._configured_account_dropdown_items: list[str] = []
        self.set_account_dropdown_order(account_dropdown_order or [])

    def set_account_dropdown_order(self, account_dropdown_order: list[str]) -> None:
        normalized_items = [
            self._normalize_account_no(item)
            for item in account_dropdown_order
            if re.fullmatch(self.ACCOUNT_PATTERN, self._normalize_account_no(item))
        ]
        unique_items: list[str] = []
        for item in normalized_items:
            if item not in unique_items:
                unique_items.append(item)
        if unique_items:
            self._configured_account_dropdown_items = unique_items

    def set_manual_mini_order_point(self, raw_value: str) -> None:
        try:
            x_raw, y_raw = [part.strip() for part in raw_value.split(",", 1)]
            self._manual_mini_order_point = (int(x_raw), int(y_raw))
        except Exception as exc:
            raise RuntimeError(f"--mini-order-point must be X,Y: {raw_value}") from exc

    def list_accounts(self) -> list[str]:
        return sorted(self._accounts)

    def probe_account_select(self, account_no: str, window_kind: str = "balance") -> dict[str, str]:
        from pywinauto import Desktop

        desktop = Desktop(backend="win32")
        if window_kind == "open-orders":
            window_code = self.OPEN_ORDERS_WINDOW_CODE
            window_re = self.OPEN_ORDERS_WINDOW_RE
        else:
            window_code = self.BALANCE_WINDOW_CODE
            window_re = self.BALANCE_WINDOW_RE
        target = self._normalize_account_no(account_no)
        try:
            window = self._ensure_window_open(desktop, window_code, window_re)
            if window is None:
                return {"ok": "false", "message": f"HTS window {window_code} is not open"}
            self._ensure_window_account(window, account_no)
            combo = self._find_account_combo_in_window(window)
            current = self._clean_text_cell(self._safe_window_text(combo)) if combo is not None else ""
            normalized_current = self._normalize_account_no(current)
            return {
                "ok": "true" if normalized_current == target else "false",
                "target_account": target,
                "current_account": normalized_current,
                "message": "account selected" if normalized_current == target else "account did not change",
            }
        except Exception as exc:
            return {"ok": "false", "target_account": target, "message": str(exc)}
        finally:
            self.close_window_by_re(window_re)

    def reset_trading_windows(self) -> None:
        from pywinauto import Desktop

        self._raise_if_order_unavailable_popup()
        desktop = Desktop(backend="win32")
        self._close_window_by_re(desktop, self.BALANCE_WINDOW_RE)
        self._close_window_by_re(desktop, self.OPEN_ORDERS_WINDOW_RE)
        self._close_window_by_re(desktop, self.MINI_ORDER_WINDOW_RE)
        self._raise_if_order_unavailable_popup()

    def get_balance(self, account_no: str, symbol: str) -> Balance:
        from pywinauto import Desktop

        self._raise_if_order_unavailable_popup()
        self._accounts.add(account_no)
        self.reset_trading_windows()
        self.close_window_by_re(self.BALANCE_WINDOW_RE)
        desktop = Desktop(backend="win32")
        window = self._ensure_window_open(desktop, self.BALANCE_WINDOW_CODE, self.BALANCE_WINDOW_RE)
        if window is not None:
            print(f"  [balance] query account={account_no} symbol={symbol}", flush=True)
            self._ensure_window_account(window, account_no)
            time.sleep(1.5)  # account selection refreshes the grid automatically
        try:
            raw = self._copy_row_text(self.BALANCE_WINDOW_RE, self.BALANCE_ROW_X, self.BALANCE_ROW_Y)
            row = self._parse_balance_row(raw, symbol)
        finally:
            self.close_window_by_re(self.BALANCE_WINDOW_RE)
        return Balance(
            account_no=account_no,
            symbol=symbol,
            qty=int(row.get(self.HEADER_QTY, 0)),
            available_qty=int(row.get(self.HEADER_AVAILABLE_QTY, row.get(self.HEADER_QTY, 0))),
            avg_price=float(row.get(self.HEADER_AVG_PRICE, 0.0)),
            current_price=float(row.get(self.HEADER_CURRENT_PRICE, 0.0)),
            valuation=float(row.get(self.HEADER_VALUATION, 0.0)),
            pnl=float(row.get(self.HEADER_PNL, 0.0)),
            fetched_at=datetime.now(),
        )

    def get_open_orders(self, account_no: str, symbol: str) -> list[OpenOrder]:
        from pywinauto import Desktop

        self._raise_if_order_unavailable_popup()
        self._accounts.add(account_no)
        self.reset_trading_windows()
        self.close_window_by_re(self.BALANCE_WINDOW_RE)
        self.close_window_by_re(self.OPEN_ORDERS_WINDOW_RE)
        desktop = Desktop(backend="win32")
        window = self._ensure_window_open(desktop, self.OPEN_ORDERS_WINDOW_CODE, self.OPEN_ORDERS_WINDOW_RE)
        if window is not None:
            print(f"  [open_orders] query account={account_no} symbol={symbol}", flush=True)
            self._verify_window_account_selected(window, account_no)
            time.sleep(1.0)  # account selection refreshes the grid automatically
        orders: list[OpenOrder] = []
        try:
            raw = self._copy_row_text(self.OPEN_ORDERS_WINDOW_RE, self.OPEN_ORDER_ROW_X, self.OPEN_ORDER_ROW_Y)
            row = self._parse_open_order_row(raw, symbol)
            order_id = self._clean_text_cell(row[self.HEADER_ORDER_ID])
            orders.append(OpenOrder(
                order_id=order_id,
                account_no=account_no,
                symbol=symbol,
                side=self._parse_side_text(row[self.HEADER_SIDE]),
                price=float(row[self.HEADER_ORDER_PRICE]),
                original_qty=int(row[self.HEADER_ORIGINAL_QTY]),
                remaining_qty=int(row[self.HEADER_REMAINING_QTY]),
                status=row[self.HEADER_STATUS],
                submitted_at=self._parse_submitted_at(row.get(self.HEADER_SUBMITTED_AT, "")),
                fetched_at=datetime.now(),
            ))
        except RuntimeError:
            pass
        finally:
            self.close_window_by_re(self.OPEN_ORDERS_WINDOW_RE)
        return orders

    def place_order(self, order: OrderRequest) -> OrderResult:
        from pywinauto import Desktop

        desktop = Desktop(backend="win32")
        stale_popup = self._handle_order_rejected_popup()
        if stale_popup is not None:
            return OrderResult(False, None, stale_popup)
        self._raise_if_order_unavailable_popup()
        try:
            window = self.reset_mini_order_window(desktop=desktop)
        except Exception as exc:
            return OrderResult(False, None, f"failed to reset mini order window: {exc}")
        if window is None:
            return OrderResult(False, None, "HTS mini order window [2102] is not open")

        self._ensure_place_mode(window, order.side)
        form = self._locate_place_form(window)
        if not self._place_form_ready(form, order.side):
            return OrderResult(False, None, "one or more required order controls were not located")

        try:
            window.set_focus()
            auto_qty_status = self._ensure_auto_qty_unchecked(form)
            if auto_qty_status not in {"already_unchecked", "unchecked", "missing"}:
                return OrderResult(False, None, f"failed to disable auto balance quantity: {auto_qty_status}")
            self._set_account_value(form["account_edit"], order.account_no)
            self._set_edit_value(form["symbol_edit"], order.symbol)
            self._set_edit_value(form["order_type_edit"], order.order_type or self.ORDER_TYPE_DEFAULT)
            self._set_edit_value(form["qty_edit"], str(order.qty))
            self._set_edit_value(form["price_edit"], f"{order.price:.4f}")
            time.sleep(0.2)
            verification = self._verify_place_form_values(form, order)
            if verification is not None:
                return OrderResult(False, None, verification)
        except Exception as exc:
            return OrderResult(False, None, f"failed to fill order form: {exc}")

        action_button = form["buy_button"] if order.side == Side.BUY else form["sell_button"]
        button_label = self.BUY_BUTTON_LABEL if order.side == Side.BUY else self.SELL_BUTTON_LABEL
        if action_button is None:
            return OrderResult(False, None, f"{button_label} button was not found")

        popup_result = {"found": "false", "clicked": "false", "message": "order confirmation dialog was not detected"}
        click_methods: list[str] = []
        for click_method in self._click_order_button_attempts(action_button):
            click_methods.append(click_method)
            time.sleep(0.2)
            popup_result = self._handle_place_confirmation(confirm=True)
            if popup_result["found"] == "true":
                break
        click_method_text = ",".join(click_methods) or "-"
        if not click_methods:
            return OrderResult(False, None, f"failed to click {button_label} button")

        if popup_result["found"] != "true":
            return OrderResult(False, None, f"clicked {button_label} button via {click_method_text}; {popup_result['message']}")
        if popup_result["clicked"] != "true":
            return OrderResult(False, None, popup_result["message"])

        verification = self._verify_order_created(order)
        if verification["created"] == "true":
            return OrderResult(
                True,
                verification.get("order_id") or None,
                f"clicked {button_label} button via {click_method_text}, confirmed popup, and detected order in open orders",
            )
        if verification["created"] == "unknown":
            return OrderResult(
                True,
                None,
                f"clicked {button_label} button via {click_method_text} and confirmed popup; {verification['message']}",
            )
        return OrderResult(
            False,
            None,
            f"clicked {button_label} button via {click_method_text} and confirmed popup, but order was not accepted: {verification['message']}",
        )

    def close_window_by_re(self, window_re: str) -> None:
        from pywinauto import Desktop

        window = self._find_target_window(Desktop(backend="win32"), window_re)
        if window is None:
            return
        self._post_close_window(window)
        time.sleep(0.3)

    def reset_mini_order_window(self, desktop: Any | None = None) -> Any | None:
        from pywinauto import Desktop

        desktop = desktop or Desktop(backend="win32")
        try:
            self.reset_trading_windows()
        except Exception:
            # Some HTS child windows reject programmatic close unless the
            # automation process has the same desktop/elevation context.
            # Re-opening 2102 is still useful for probes, so continue.
            pass
        desktop = Desktop(backend="win32")
        self._open_window_by_code(desktop, self.MINI_ORDER_WINDOW_CODE, self.MINI_ORDER_WINDOW_RE)
        return self._find_target_window(desktop, self.MINI_ORDER_WINDOW_RE)

    def _ensure_window_open(self, desktop: Any, window_code: str, window_re: str) -> Any | None:
        existing = self._find_target_window(desktop, window_re)
        if existing is not None:
            return existing
        self._open_window_by_code(desktop, window_code, window_re)
        return self._find_target_window(desktop, window_re)

    def _ensure_window_account(self, window: Any, account_no: str) -> None:
        target = self._normalize_account_no(account_no)
        combo = self._find_account_combo_in_window(window)
        if combo is None:
            raise RuntimeError(f"account control not found for {target}")
        current = self._clean_text_cell(self._safe_window_text(combo))
        if self._normalize_account_no(current) == target:
            return
        self._raise_if_order_unavailable_popup()
        window.set_focus()
        print(f"  [account] dropdown select current={current or '-'} target={target}", flush=True)
        if not self._select_account_from_dropdown_first(window, combo, target):
            raise RuntimeError(f"failed to select account {target}")
        time.sleep(0.8)
        updated = self._clean_text_cell(self._safe_window_text(combo))
        if updated and self._normalize_account_no(updated) != target:
            raise RuntimeError(f"account field stayed on {updated} instead of {target}")

    def _verify_window_account_selected(self, window: Any, account_no: str) -> None:
        target = self._normalize_account_no(account_no)
        combo = self._find_account_combo_in_window(window)
        if combo is None:
            raise RuntimeError(f"account control not found for verification {target}")
        current = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
        if current != target:
            raise RuntimeError(f"account field mismatch before read: expected {target}, got {current or '-'}")

    def _ensure_window_symbol(self, window: Any, symbol: str) -> None:
        target = symbol.strip().upper()
        if not target:
            return
        edit = self._find_window_symbol_edit(window)
        if edit is None:
            print(f"  [symbol] symbol field not found; continuing with existing filter", flush=True)
            return
        current = self._clean_text_cell(self._safe_window_text(edit)).upper()
        if current == target:
            return
        self._set_edit_value(edit, target)
        time.sleep(0.2)
        updated = self._clean_text_cell(self._safe_window_text(edit)).upper()
        if updated and updated != target:
            raise RuntimeError(f"symbol field stayed on {updated} instead of {target}")

    def _find_window_symbol_edit(self, window: Any) -> Any | None:
        labeled = self._find_labeled_edit(window, self.SYMBOL_LABEL_TEXT, prefer_textual=True, allow_empty=True)
        if labeled is not None:
            return labeled
        win_rect = window.rectangle()
        candidates: list[tuple[int, int, Any]] = []
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            if self._safe_class_name(control) != "Edit":
                continue
            text = self._clean_text_cell(self._safe_window_text(control))
            rect = control.rectangle()
            rel_top = rect.top - win_rect.top
            rel_left = rect.left - win_rect.left
            if 20 <= rel_top <= 95 and 130 <= rel_left <= 360:
                score = 0 if re.fullmatch(r"^[A-Z]{1,8}$", text.upper()) else 1
                candidates.append((score, rect.left, control))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    def _select_account_from_combo(self, combo: Any, target: str) -> bool:
        try:
            items = combo.texts()
        except Exception:
            items = []
        for idx, item in enumerate(items):
            if self._normalize_account_no(self._clean_text_cell(str(item))) == target:
                try:
                    combo.select(idx)
                    return True
                except Exception:
                    pass
        for idx, item in enumerate(items):
            if target in self._clean_text_cell(str(item)):
                try:
                    combo.select(idx)
                    return True
                except Exception:
                    pass
        try:
            combo.select(target)
            return True
        except Exception:
            pass
        return False

    def _select_account_from_dropdown_first(self, window: Any, combo: Any, target: str) -> bool:
        if self._select_account_from_win32_combo(combo, target):
            return True
        if self._select_account_from_dropdown_keys(window, combo, target):
            return True
        if self._select_account_from_combo(combo, target):
            return True
        return False

    def _select_account_from_win32_combo(self, combo: Any, target: str) -> bool:
        combo_hwnd = self._combo_box_handle(combo)
        if not combo_hwnd:
            return False
        items = self._account_dropdown_items_from_handle(combo_hwnd)
        print(f"  [account] dropdown items={items or 'none'}", flush=True)
        target_index = self._account_index(items, target)
        if target_index < 0:
            return False
        try:
            win32gui.SendMessage(combo_hwnd, self.CB_SETCURSEL, target_index, 0)
            self._notify_combo_selection(combo_hwnd)
            time.sleep(0.4)
            updated = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
            if updated == target:
                return True
            selected = self._selected_combo_text(combo_hwnd)
            return selected == target
        except Exception:
            return False

    def _select_account_from_dropdown_keys(self, window: Any, combo: Any, target: str) -> bool:
        list_hwnd: int | None = None
        try:
            try:
                window.set_focus()
            except Exception:
                pass
            rect = combo.rectangle()
            self._mouse_click_screen((rect.right - 8, (rect.top + rect.bottom) // 2))
            time.sleep(0.35)
            list_hwnd = self._find_open_combo_list(combo)
            if list_hwnd:
                items = self._account_open_dropdown_items_from_handle(list_hwnd)
                if items:
                    self._last_account_dropdown_items = items
                elif self._last_account_dropdown_items:
                    items = self._last_account_dropdown_items
                    print(f"  [account] using cached dropdown items={items}", flush=True)
                print(f"  [account] open dropdown items={items or 'none'}", flush=True)
                target_index = self._account_index(items, target)
                if target_index >= 0 and self._select_open_dropdown_item(list_hwnd, target_index, combo):
                    time.sleep(0.5)
                    updated = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
                    if updated == target:
                        return True
                if self._select_account_from_dropdown_capture(list_hwnd, combo, target, items):
                    time.sleep(0.5)
                    updated = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
                    if updated == target:
                        return True
                if self._click_visible_account_item(list_hwnd, target):
                    time.sleep(0.5)
                    updated = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
                    if updated == target:
                        return True
                self._dismiss_account_dropdown(combo)
                if self._select_account_by_keyboard_cycle(window, combo, target):
                    return True
            else:
                print("  [account] open dropdown list not found", flush=True)

            self._dismiss_account_dropdown(combo)
            return False
        except Exception:
            self._dismiss_account_dropdown(combo)
            return False

    def _dismiss_account_dropdown(self, combo: Any) -> None:
        try:
            from pywinauto import keyboard

            keyboard.send_keys("{ESC}")
        except Exception:
            pass

    def _select_account_by_keyboard_cycle(self, window: Any, combo: Any, target: str, max_steps: int = 10) -> bool:
        try:
            from pywinauto import keyboard

            try:
                window.set_focus()
            except Exception:
                pass
            rect = combo.rectangle()
            seen: set[str] = set()
            for _step in range(max_steps):
                current = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
                if current == target:
                    return True
                if current:
                    seen.add(current)
                self._mouse_click_screen((rect.left + 18, (rect.top + rect.bottom) // 2))
                time.sleep(0.1)
                keyboard.send_keys("{DOWN}{ENTER}", pause=0.03)
                time.sleep(0.45)
                updated = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
                if updated == target:
                    print(f"  [account] keyboard cycle selected target={target}", flush=True)
                    return True
                if updated and updated in seen and len(seen) > 1:
                    break
            print(f"  [account] keyboard cycle failed target={target}", flush=True)
            return False
        except Exception as exc:
            print(f"  [account] keyboard cycle failed: {exc}", flush=True)
            return False

    def _find_open_combo_list(self, combo: Any | None = None) -> int | None:
        combo_rect = None
        if combo is not None:
            try:
                combo_rect = combo.rectangle()
            except Exception:
                combo_rect = None
        matches: list[tuple[int, int, str, tuple[int, int, int, int], str]] = []
        seen: set[int] = set()

        def _looks_like_dropdown(class_name: str, rect: tuple[int, int, int, int]) -> bool:
            if class_name in {"ComboLBox", "ListBox"}:
                return True
            if class_name == "#32769":
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]
                return 20 <= width <= 500 and 10 <= height <= 500
            return "List" in class_name or "Combo" in class_name

        def _collect(hwnd: int, _param: Any) -> None:
            try:
                if hwnd in seen:
                    return
                seen.add(hwnd)
                if not win32gui.IsWindowVisible(hwnd):
                    return
                class_name = win32gui.GetClassName(hwnd)
                rect = self._hwnd_rect(hwnd)
                if rect is None or not _looks_like_dropdown(class_name, rect):
                    return
                if combo_rect is not None and not self._is_near_account_dropdown(rect, combo_rect):
                    return
                title = self._clean_text_cell(self._safe_handle_text(hwnd))
                score = self._combo_dropdown_score(rect, combo_rect, class_name)
                matches.append((score, hwnd, class_name, rect, title))
            except Exception:
                return

        def _collect_tree(hwnd: int) -> None:
            _collect(hwnd, None)
            try:
                win32gui.EnumChildWindows(hwnd, _collect, None)
            except Exception:
                pass

        try:
            win32gui.EnumWindows(lambda hwnd, _param: _collect_tree(hwnd), None)
        except Exception:
            return None
        if not matches:
            return None
        matches.sort(key=lambda item: item[0])
        sample = [
            f"{class_name}:{title or '-'}@{rect}"
            for _score, _hwnd, class_name, rect, title in matches[:5]
        ]
        print(f"  [account] dropdown list candidates={sample}", flush=True)
        return matches[0][1]

    def _hwnd_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return None
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)

    def _is_near_combo_dropdown(self, rect: tuple[int, int, int, int], combo_rect: Any) -> bool:
        combo_left = int(combo_rect.left)
        combo_top = int(combo_rect.top)
        combo_right = int(combo_rect.right)
        combo_bottom = int(combo_rect.bottom)
        left, top, right, bottom = rect
        horizontally_related = right >= combo_left - 80 and left <= combo_right + 220
        vertically_related = combo_top - 20 <= top <= combo_bottom + 360 or combo_top - 20 <= bottom <= combo_bottom + 360
        return horizontally_related and vertically_related

    def _is_near_account_dropdown(self, rect: tuple[int, int, int, int], combo_rect: Any) -> bool:
        combo_left = int(combo_rect.left)
        combo_right = int(combo_rect.right)
        combo_bottom = int(combo_rect.bottom)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return False
        horizontally_aligned = abs(left - combo_left) <= 70 or (
            left <= combo_right + 20 and right >= combo_left - 20
        )
        vertically_below = combo_bottom - 12 <= top <= combo_bottom + 120
        plausible_size = 70 <= width <= 320 and 18 <= height <= 180
        return horizontally_aligned and vertically_below and plausible_size

    def _combo_dropdown_score(self, rect: tuple[int, int, int, int], combo_rect: Any | None, class_name: str) -> int:
        class_score = 0 if class_name == "ComboLBox" else 10 if class_name == "ListBox" else 20
        if combo_rect is None:
            return class_score
        left, top, right, _bottom = rect
        combo_left = int(combo_rect.left)
        combo_bottom = int(combo_rect.bottom)
        distance = abs(left - combo_left) + abs(top - combo_bottom)
        return class_score + distance

    def _account_listbox_items_from_handle(self, list_hwnd: int) -> list[str]:
        items: list[str] = []
        try:
            count = int(win32gui.SendMessage(list_hwnd, self.LB_GETCOUNT, 0, 0))
        except Exception:
            return items
        if count <= 0 or count > 50:
            return items
        user32 = ctypes.windll.user32
        for idx in range(count):
            try:
                length = int(user32.SendMessageW(list_hwnd, self.LB_GETTEXTLEN, idx, 0))
                if length <= 0 or length > 128:
                    continue
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.SendMessageW(list_hwnd, self.LB_GETTEXT, idx, ctypes.byref(buf))
                item = self._normalize_account_no(self._clean_text_cell(buf.value))
                if re.fullmatch(self.ACCOUNT_PATTERN, item):
                    items.append(item)
            except Exception:
                continue
        return items

    def _account_open_dropdown_items_from_handle(self, list_hwnd: int) -> list[str]:
        try:
            class_name = win32gui.GetClassName(list_hwnd)
        except Exception:
            class_name = ""
        if class_name == "SysListView32":
            # HTS account dropdown listviews are owner-drawn and can hang while
            # reading text through UIA/LVM messages. Use the known account order
            # fallback and click by row coordinate instead.
            print("  [account] skip SysListView32 text read", flush=True)
            return []
        return self._account_listbox_items_from_handle(list_hwnd)

    def _account_listview_items_from_handle(self, list_hwnd: int) -> list[str]:
        items = self._account_listview_items_from_uia(list_hwnd)
        if items:
            return items
        return self._account_listview_items_from_win32(list_hwnd)

    def _account_listview_items_from_uia(self, list_hwnd: int) -> list[str]:
        try:
            from pywinauto import Desktop

            wrapper = Desktop(backend="uia").window(handle=list_hwnd).wrapper_object()
            texts: list[str] = []
            try:
                texts.extend(str(text) for text in wrapper.texts())
            except Exception:
                pass
            try:
                for child in wrapper.descendants():
                    try:
                        texts.extend(str(text) for text in child.texts())
                    except Exception:
                        text = self._safe_window_text(child)
                        if text:
                            texts.append(text)
            except Exception:
                pass
            return self._account_items_from_texts(texts)
        except Exception:
            return []

    def _account_listview_items_from_win32(self, list_hwnd: int) -> list[str]:
        import struct as _struct

        MEM_COMMIT = 0x1000
        MEM_RELEASE = 0x8000
        PAGE_READWRITE = 0x04
        PROCESS_VM_OPERATION = 0x0008
        PROCESS_VM_READ = 0x0010
        PROCESS_VM_WRITE = 0x0020
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_ACCESS = PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION
        LVIF_TEXT = 0x0001
        WCHAR_SIZE = 2
        BUF_WCHARS = 128
        BUF_BYTES = BUF_WCHARS * WCHAR_SIZE
        STRUCT_AREA = 64
        SMTO_ABORTIFHUNG = 0x0002
        MSG_TIMEOUT_MS = 1000

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            result = ctypes.c_ssize_t(0)
            ok = user32.SendMessageTimeoutW(
                list_hwnd,
                self.LVM_GETITEMCOUNT,
                0,
                0,
                SMTO_ABORTIFHUNG,
                MSG_TIMEOUT_MS,
                ctypes.byref(result),
            )
            if not ok:
                return []
            count = int(result.value)
            if count <= 0 or count > 50:
                return []

            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(list_hwnd, ctypes.byref(pid))
            hproc = kernel32.OpenProcess(PROCESS_ACCESS, False, pid.value)
            if not hproc:
                return []

            try:
                remote = kernel32.VirtualAllocEx(hproc, None, STRUCT_AREA + BUF_BYTES, MEM_COMMIT, PAGE_READWRITE)
                if not remote:
                    return []
                remote_text = remote + STRUCT_AREA

                def _read_text() -> str:
                    local = ctypes.create_string_buffer(BUF_BYTES)
                    done = ctypes.c_size_t(0)
                    kernel32.ReadProcessMemory(hproc, remote_text, local, BUF_BYTES, ctypes.byref(done))
                    raw = bytes(local)[: done.value]
                    return raw.decode("utf-16-le", errors="replace").split("\x00")[0].strip()

                found: list[str] = []
                try:
                    for row in range(count):
                        for sub in range(4):
                            packed = _struct.pack(
                                "<IIIIIxxxxQI",
                                LVIF_TEXT,
                                row,
                                sub,
                                0,
                                0,
                                remote_text,
                                BUF_WCHARS,
                            )
                            done = ctypes.c_size_t(0)
                            local_buf = ctypes.create_string_buffer(packed)
                            kernel32.WriteProcessMemory(hproc, remote, local_buf, len(packed), ctypes.byref(done))
                            result = ctypes.c_ssize_t(0)
                            ok = user32.SendMessageTimeoutW(
                                list_hwnd,
                                self.LVM_GETITEMTEXTW,
                                row,
                                remote,
                                SMTO_ABORTIFHUNG,
                                MSG_TIMEOUT_MS,
                                ctypes.byref(result),
                            )
                            if ok:
                                found.extend(self._account_items_from_texts([_read_text()]))
                    return found
                finally:
                    kernel32.VirtualFreeEx(hproc, remote, 0, MEM_RELEASE)
            finally:
                kernel32.CloseHandle(hproc)
        except Exception:
            return []

    def _account_items_from_texts(self, texts: list[str]) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for text in texts:
            normalized = self._normalize_account_no(self._clean_text_cell(str(text)))
            candidates = [normalized]
            candidates.extend(self._normalize_account_no(match) for match in re.findall(r"\d{4}[- ]?\d{4}", str(text)))
            for item in candidates:
                if re.fullmatch(self.ACCOUNT_PATTERN, item) and item not in seen:
                    seen.add(item)
                    items.append(item)
        return items

    def _click_visible_account_item(self, list_hwnd: int, target: str) -> bool:
        candidates = self._visible_account_items_from_handle(list_hwnd)
        if candidates:
            print(f"  [account] visible dropdown items={[item[0] for item in candidates]}", flush=True)
        for item, rect in candidates:
            if item != target and target not in item:
                continue
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            try:
                self._mouse_click_screen((cx, cy))
                return True
            except Exception:
                return False
        return False

    def _visible_account_items_from_handle(self, list_hwnd: int) -> list[tuple[str, tuple[int, int, int, int]]]:
        items: list[tuple[str, tuple[int, int, int, int]]] = []
        seen: set[int] = set()

        def _collect(hwnd: int, _param: Any) -> None:
            if hwnd in seen:
                return
            seen.add(hwnd)
            rect = self._hwnd_rect(hwnd)
            if rect is None:
                return
            text = self._normalize_account_no(self._clean_text_cell(self._safe_handle_text(hwnd)))
            if re.fullmatch(self.ACCOUNT_PATTERN, text):
                items.append((text, rect))

        try:
            _collect(list_hwnd, None)
            win32gui.EnumChildWindows(list_hwnd, _collect, None)
        except Exception:
            pass
        return items

    def _click_listbox_item(self, list_hwnd: int, index: int) -> bool:
        import ctypes.wintypes as wt

        rect = wt.RECT()
        try:
            ok = ctypes.windll.user32.SendMessageW(list_hwnd, self.LB_GETITEMRECT, index, ctypes.byref(rect))
            if not ok:
                return False
            left_top = win32gui.ClientToScreen(list_hwnd, (rect.left, rect.top))
            right_bottom = win32gui.ClientToScreen(list_hwnd, (rect.right, rect.bottom))
            cx = left_top[0] + max(12, min(45, (right_bottom[0] - left_top[0]) // 4))
            cy = (left_top[1] + right_bottom[1]) // 2
            print(f"  [account] click ListBox index={index} at ({cx},{cy})", flush=True)
            self._mouse_click_screen((cx, cy))
            return True
        except Exception:
            return False

    def _select_account_from_dropdown_capture(
        self,
        list_hwnd: int,
        combo: Any,
        target: str,
        items: list[str],
    ) -> bool:
        try:
            class_name = win32gui.GetClassName(list_hwnd)
        except Exception:
            class_name = ""
        if class_name != "SysListView32":
            return False

        dropdown_rect = self._hwnd_rect(list_hwnd)
        if dropdown_rect is None:
            return False
        try:
            combo_rect = combo.rectangle()
        except Exception:
            return False
        if not self._is_near_account_dropdown(dropdown_rect, combo_rect):
            print(f"  [account] capture skip: dropdown not near account control rect={dropdown_rect}", flush=True)
            return False

        item_order = [item for item in items if re.fullmatch(self.ACCOUNT_PATTERN, item)]
        if not item_order:
            item_order = list(self._last_account_dropdown_items)
        if not item_order:
            item_order = list(self._configured_account_dropdown_items)
            print(f"  [account] capture using configured account order={item_order or 'none'}", flush=True)
        current = self._normalize_account_no(self._clean_text_cell(self._safe_window_text(combo)))
        if current and re.fullmatch(self.ACCOUNT_PATTERN, current) and current not in item_order:
            item_order.append(current)
            print(f"  [account] capture appended current account to order={item_order}", flush=True)

        target_index = self._account_index(item_order, target)
        if target_index < 0:
            print(f"  [account] capture skip: target {target} not in account order", flush=True)
            return False

        image, image_path = self._capture_account_dropdown(dropdown_rect)
        row_centers = self._account_row_centers_from_capture(image) if image is not None else []
        print(f"  [account] capture rows={row_centers or 'none'} path={image_path or '-'}", flush=True)
        if len(row_centers) <= target_index:
            row_centers = self._estimated_account_row_centers(dropdown_rect, len(item_order))
            print(f"  [account] capture estimated rows={row_centers or 'none'}", flush=True)
        if len(row_centers) <= target_index:
            print("  [account] capture skip: target row was not available", flush=True)
            return False

        left, top, right, bottom = dropdown_rect
        width = right - left
        height = bottom - top
        if width < 70 or height < 18:
            return False
        cx = left + max(12, min(45, width // 4))
        cy = top + row_centers[target_index]
        if not (left + 2 <= cx <= right - 2 and top + 2 <= cy <= bottom - 2):
            print(f"  [account] capture skip: click outside dropdown ({cx},{cy}) rect={dropdown_rect}", flush=True)
            return False

        print(f"  [account] capture click target={target} index={target_index} at ({cx},{cy})", flush=True)
        self._mouse_click_screen((cx, cy))
        return True

    def _capture_account_dropdown(self, rect: tuple[int, int, int, int]) -> tuple[Any | None, str]:
        try:
            from pathlib import Path
            from PIL import ImageGrab

            image = ImageGrab.grab(bbox=rect)
            out_dir = Path("image") / "account_dropdown"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"dropdown_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
            image.save(path)
            return image, str(path)
        except Exception as exc:
            print(f"  [account] capture failed: {exc}", flush=True)
            return None, ""

    @staticmethod
    def _estimated_account_row_centers(rect: tuple[int, int, int, int], item_count: int) -> list[int]:
        if item_count <= 0 or item_count > 10:
            return []
        _left, _top, _right, bottom = rect
        height = bottom - rect[1]
        if height < item_count * 8 or height > item_count * 32:
            return []
        return [int((idx + 0.5) * height / item_count) for idx in range(item_count)]

    @staticmethod
    def _account_row_centers_from_capture(image: Any) -> list[int]:
        try:
            import cv2
            import numpy as np

            arr = np.asarray(image.convert("RGB"))
            if arr.size == 0:
                return []
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            height, width = gray.shape[:2]
            if width < 40 or height < 10:
                return []

            dark = gray < 145
            if width > 12 and height > 6:
                dark[:, :2] = False
                dark[:, -2:] = False
                dark[:2, :] = False
                dark[-2:, :] = False
            projection = dark.sum(axis=1)
            threshold = max(3, int(width * 0.025))
            active = projection >= threshold

            bands: list[tuple[int, int]] = []
            start: int | None = None
            for y, is_active in enumerate(active.tolist()):
                if is_active and start is None:
                    start = y
                elif not is_active and start is not None:
                    if y - start >= 2:
                        bands.append((start, y - 1))
                    start = None
            if start is not None and height - start >= 2:
                bands.append((start, height - 1))

            merged: list[tuple[int, int]] = []
            for band in bands:
                if merged and band[0] - merged[-1][1] <= 3:
                    merged[-1] = (merged[-1][0], band[1])
                else:
                    merged.append(band)

            centers = [(top + bottom) // 2 for top, bottom in merged if 4 <= (bottom - top + 1) <= 24]
            return centers[:10]
        except Exception:
            return []

    def _select_open_dropdown_item(self, list_hwnd: int, index: int, combo: Any | None = None) -> bool:
        try:
            class_name = win32gui.GetClassName(list_hwnd)
        except Exception:
            class_name = ""
        if class_name in {"ComboLBox", "ListBox"} and self._click_listbox_item(list_hwnd, index):
            return True
        if class_name == "SysListView32":
            print("  [account] skip SysListView32 coordinate click", flush=True)
        return False

    @staticmethod
    def _mouse_click_screen(coords: tuple[int, int]) -> None:
        import win32api
        import win32con

        win32api.SetCursorPos(coords)
        time.sleep(0.03)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, coords[0], coords[1], 0, 0)
        time.sleep(0.03)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, coords[0], coords[1], 0, 0)

    def _account_dropdown_items(self, combo: Any) -> list[str]:
        try:
            raw_items = combo.texts()
        except Exception:
            raw_items = []
        items = [self._normalize_account_no(self._clean_text_cell(str(item))) for item in raw_items]
        items = [item for item in items if re.fullmatch(self.ACCOUNT_PATTERN, item)]
        if items:
            return items
        combo_hwnd = self._combo_box_handle(combo)
        if combo_hwnd:
            return self._account_dropdown_items_from_handle(combo_hwnd)
        return []

    def _combo_box_handle(self, control: Any) -> int | None:
        handle = self._safe_handle(control)
        for _ in range(4):
            if not handle:
                return None
            try:
                if win32gui.GetClassName(handle) == "ComboBox":
                    return handle
                handle = win32gui.GetParent(handle)
            except Exception:
                return None
        return None

    def _account_dropdown_items_from_handle(self, combo_hwnd: int) -> list[str]:
        items: list[str] = []
        try:
            count = int(win32gui.SendMessage(combo_hwnd, self.CB_GETCOUNT, 0, 0))
        except Exception:
            return items
        if count <= 0 or count > 50:
            return items
        user32 = ctypes.windll.user32
        for idx in range(count):
            try:
                length = int(user32.SendMessageW(combo_hwnd, self.CB_GETLBTEXTLEN, idx, 0))
                if length <= 0 or length > 128:
                    continue
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.SendMessageW(combo_hwnd, self.CB_GETLBTEXT, idx, ctypes.byref(buf))
                item = self._normalize_account_no(self._clean_text_cell(buf.value))
                if re.fullmatch(self.ACCOUNT_PATTERN, item):
                    items.append(item)
            except Exception:
                continue
        return items

    def _selected_combo_text(self, combo_hwnd: int) -> str:
        try:
            idx = int(win32gui.SendMessage(combo_hwnd, 0x0147, 0, 0))  # CB_GETCURSEL
        except Exception:
            return ""
        items = self._account_dropdown_items_from_handle(combo_hwnd)
        if 0 <= idx < len(items):
            return items[idx]
        return ""

    def _notify_combo_selection(self, combo_hwnd: int) -> None:
        try:
            import win32con
            parent = win32gui.GetParent(combo_hwnd)
            control_id = win32gui.GetDlgCtrlID(combo_hwnd)
            for code in (self.CBN_SELCHANGE, self.CBN_SELENDOK, self.CBN_CLOSEUP):
                win32gui.SendMessage(parent, win32con.WM_COMMAND, (code << 16) | control_id, combo_hwnd)
                win32gui.PostMessage(parent, win32con.WM_COMMAND, (code << 16) | control_id, combo_hwnd)
        except Exception:
            pass

    def _account_index(self, items: list[str], target: str) -> int:
        for idx, item in enumerate(items):
            if item == target or target in item:
                return idx
        return -1

    def _find_account_combo_in_window(self, window: Any) -> Any | None:
        win_rect = window.rectangle()
        best: tuple[int, int, Any] | None = None
        debug: list[str] = []
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            class_name = self._safe_class_name(control)
            if class_name not in {"ComboBox", "Edit"}:
                continue
            rect = control.rectangle()
            rel_top = rect.top - win_rect.top
            rel_left = rect.left - win_rect.left
            if rel_top > 90:
                continue
            text = self._clean_text_cell(self._safe_window_text(control))
            normalized = self._normalize_account_no(text)
            if not (
                re.fullmatch(self.ACCOUNT_PATTERN, normalized)
                or re.fullmatch(r"^\d{8}$", re.sub(r"\D", "", text))
            ):
                continue
            score = 0 if class_name == "ComboBox" else 10
            score += max(0, rel_top)
            debug.append(f"{class_name}:{text or '-'}@({rel_left},{rel_top})")
            if best is None or score < best[0]:
                best = (score, rel_left, control)
        if debug:
            print(f"  [account] account control candidates={debug[:5]}", flush=True)
        return best[2] if best else None

    def _close_mini_order_windows(self, desktop: Any) -> None:
        for window in self._collect_mini_order_windows(desktop):
            self._close_window(window)
            time.sleep(0.25)

    def _close_window_by_re(self, desktop: Any, window_re: str) -> None:
        for _ in range(3):
            window = self._find_target_window(desktop, window_re)
            if window is None:
                return
            self._post_close_window(window)
            time.sleep(0.25)

    def _close_window(self, window: Any) -> None:
        if self._post_close_window(window):
            return
        try:
            from pywinauto import mouse

            rect = window.rectangle()
            mouse.click(button="left", coords=(rect.right - 12, rect.top + 12))
        except Exception as exc:
            raise RuntimeError(f"failed to close HTS window without active mouse access: {exc}") from exc

    def _post_close_window(self, window: Any) -> bool:
        try:
            import win32con
            handle = self._safe_handle(window)
            if not handle:
                return False
            if self._is_main_hwnd(handle):
                print("  [close] skip HTS main window", flush=True)
                return False
            win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
            return True
        except Exception:
            return False

    @classmethod
    def _is_main_window_title(cls, title: str) -> bool:
        clean = title.strip()
        return any(candidate in clean for candidate in cls.MAIN_WINDOW_TITLES)

    @classmethod
    def _find_main_hwnd(cls) -> int:
        for title in cls.MAIN_WINDOW_TITLES:
            hwnd = win32gui.FindWindow(None, title)
            if hwnd and win32gui.IsWindowVisible(hwnd):
                return int(hwnd)

        matches: list[int] = []

        def _collect(hwnd: int, _param: Any) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = cls._safe_handle_text(hwnd)
            if title and cls._is_main_window_title(title):
                matches.append(int(hwnd))

        try:
            win32gui.EnumWindows(_collect, None)
        except Exception:
            pass
        return matches[0] if matches else 0

    def _find_main_window(self, desktop: Any) -> Any | None:
        hwnd = self._find_main_hwnd()
        if not hwnd:
            return None
        return desktop.window(handle=hwnd)

    @classmethod
    def _is_main_hwnd(cls, hwnd: int) -> bool:
        if not hwnd:
            return False
        try:
            title = cls._safe_handle_text(int(hwnd))
        except Exception:
            title = ""
        if title and cls._is_main_window_title(title):
            return True
        main_hwnd = cls._find_main_hwnd()
        return bool(main_hwnd and int(hwnd) == int(main_hwnd))

    def _collect_mini_order_windows(self, desktop: Any) -> list[Any]:
        seen: set[int] = set()
        results: list[Any] = []
        direct = desktop.window(title_re=self.MINI_ORDER_WINDOW_RE)
        if direct.exists(timeout=1):
            handle = self._safe_handle(direct)
            if handle not in seen:
                seen.add(handle)
                results.append(direct)
        main_window = self._find_main_window(desktop)
        if main_window is not None and main_window.exists(timeout=1):
            for control in main_window.descendants():
                if not self._is_control_visible(control):
                    continue
                title = self._safe_window_text(control)
                if not title or not re.search(self.MINI_ORDER_WINDOW_RE, title):
                    continue
                handle = self._safe_handle(control)
                if handle in seen:
                    continue
                seen.add(handle)
                results.append(control)
        return results

    def _open_window_by_code(self, desktop: Any, window_code: str, window_re: str) -> None:
        from pywinauto import keyboard, mouse

        existing = self._find_target_window(desktop, window_re)
        if existing is not None:
            return

        main_window = self._find_main_window(desktop)
        if main_window is None or not main_window.exists(timeout=2):
            raise RuntimeError("HTS main window is not open")

        rect = main_window.rectangle()
        search_coords = (
            int(rect.left + rect.width() * self.SEARCH_BOX_POINT[0]),
            int(rect.top + rect.height() * self.SEARCH_BOX_POINT[1]),
        )
        search_control = self._find_main_search_edit(main_window)
        search_button = self._find_main_search_button(main_window, search_control)

        last_exc: Exception | None = None
        for _ in range(2):
            try:
                try:
                    main_window.set_focus()
                except Exception:
                    pass
                keyboard.send_keys("{ESC}")
                time.sleep(0.1)
                if search_control is not None:
                    self._set_search_edit_value(search_control, window_code)
                    if search_button is not None and self._click_button_without_mouse(search_button):
                        time.sleep(1.8)
                        if self._find_target_window(desktop, window_re) is not None:
                            return
                    try:
                        keyboard.send_keys("{ENTER}")
                    except Exception:
                        pass
                    time.sleep(0.6)
                    if self._find_target_window(desktop, window_re) is not None:
                        return
                    try:
                        search_control.set_focus()
                    except Exception:
                        pass
                    try:
                        search_control.click_input()
                    except Exception:
                        center = self._control_center(search_control)
                        if center is None:
                            raise RuntimeError("failed to click detected HTS search edit")
                        try:
                            self._mouse_click_screen(center)
                        except Exception as exc:
                            if not self._click_screen_point(center):
                                raise RuntimeError("failed to click detected HTS search edit") from exc
                else:
                    try:
                        mouse.click(button="left", coords=search_coords)
                    except Exception:
                        if not self._click_screen_point(search_coords, main_window):
                            raise RuntimeError(f"failed to click HTS search box at {search_coords}")
                time.sleep(0.1)
                keyboard.send_keys("^a{BACKSPACE}")
                time.sleep(0.05)
                keyboard.send_keys(window_code, with_spaces=True)
                time.sleep(0.05)
                keyboard.send_keys("{ENTER}")
                time.sleep(1.8)
                if self._find_target_window(desktop, window_re) is not None:
                    return
            except Exception as exc:
                last_exc = exc
            time.sleep(0.4)
            if self._find_target_window(desktop, window_re) is not None:
                return
        if last_exc is not None:
            raise RuntimeError(f"failed while typing {window_code} into search box: {last_exc}") from last_exc

        raise RuntimeError(f"failed to open HTS window {window_code} via search box")

    def _find_main_search_edit(self, main_window: Any) -> Any | None:
        try:
            main_rect = main_window.rectangle()
            controls = main_window.descendants(class_name="Edit")
        except Exception:
            return None
        candidates: list[tuple[int, int, Any]] = []
        for control in controls:
            if not self._is_control_visible(control):
                continue
            try:
                rect = control.rectangle()
            except Exception:
                continue
            width = int(rect.width())
            height = int(rect.height())
            if width < 20 or width > 120 or height < 8 or height > 30:
                continue
            if int(rect.top) > int(main_rect.top) + 90:
                continue
            if int(rect.left) < int(main_rect.left) or int(rect.left) > int(main_rect.left) + 180:
                continue
            candidates.append((int(rect.top), int(rect.left), control))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    def _find_main_search_button(self, main_window: Any, search_control: Any | None = None) -> Any | None:
        try:
            controls = main_window.descendants(class_name="Button")
        except Exception:
            return None
        search_center = self._control_center(search_control) if search_control is not None else None
        candidates: list[tuple[int, int, Any]] = []
        for control in controls:
            if not self._is_control_visible(control):
                continue
            title = self._safe_window_text(control)
            if "화면찾기" not in title:
                continue
            try:
                rect = control.rectangle()
            except Exception:
                continue
            distance = 0
            if search_center is not None:
                center = self._control_center(control)
                if center is not None:
                    distance = abs(center[0] - search_center[0]) + abs(center[1] - search_center[1])
            candidates.append((distance, int(rect.left), control))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    @staticmethod
    def _click_button_without_mouse(control: Any) -> bool:
        try:
            handle = int(control.handle)
        except Exception:
            return False
        try:
            import win32con

            win32gui.SendMessage(handle, win32con.BM_CLICK, 0, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def _control_center(control: Any) -> tuple[int, int] | None:
        try:
            rect = control.rectangle()
            return ((int(rect.left) + int(rect.right)) // 2, (int(rect.top) + int(rect.bottom)) // 2)
        except Exception:
            return None

    def _click_screen_point(self, coords: tuple[int, int], target_window: Any | None = None) -> bool:
        try:
            import win32api
            import win32con

            handle = self._safe_handle(target_window) if target_window is not None else 0
            if handle:
                try:
                    win32gui.SetForegroundWindow(handle)
                except Exception:
                    pass
                x, y = win32gui.ScreenToClient(handle, coords)
                lparam = (y << 16) | (x & 0xFFFF)
                win32gui.PostMessage(handle, win32con.WM_MOUSEMOVE, 0, lparam)
                win32gui.PostMessage(handle, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
                win32gui.PostMessage(handle, win32con.WM_LBUTTONUP, 0, lparam)
                return True

            win32api.SetCursorPos(coords)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, coords[0], coords[1], 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, coords[0], coords[1], 0, 0)
            return True
        except Exception:
            return False

    def _close_all_windows_via_workspace_menu(self, desktop: Any) -> bool:
        from pywinauto import mouse

        main_window = self._find_main_window(desktop)
        if main_window is None or not main_window.exists(timeout=2):
            return False
        rect = main_window.rectangle()
        coords = (rect.right - 120, rect.bottom - 120)
        try:
            main_window.set_focus()
            mouse.click(button="right", coords=coords)
            time.sleep(0.2)
            return self._click_menu_item_by_text(self.CLOSE_ALL_WINDOWS_MENU_TEXT)
        except Exception:
            return False

    def _find_workspace_control(self, main_window: Any) -> Any | None:
        candidates: list[tuple[int, Any]] = []
        for control in main_window.descendants():
            if not self._is_control_visible(control):
                continue
            title = self._clean_text_cell(self._safe_window_text(control))
            class_name = self._safe_class_name(control)
            if title == "작업 영역":
                return control
            if class_name in {"MDIClient", "AfxMDIFrame140u"}:
                rect = control.rectangle()
                candidates.append((-(rect.width() * rect.height()), control))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _click_menu_item_by_text(self, text: str) -> bool:
        from pywinauto import Desktop

        try:
            desktop = Desktop(backend="uia")
            menu = desktop.window(control_type="Menu", found_index=0)
            if not menu.exists(timeout=1):
                return False
            exact = menu.child_window(title=text, control_type="MenuItem")
            if exact.exists(timeout=1):
                exact.click_input()
                return True
            partial = menu.child_window(title_re=rf".*{re.escape(text)}.*", control_type="MenuItem")
            if partial.exists(timeout=1):
                partial.click_input()
                return True
            return False
        except Exception:
            return False

    def probe_main_toolbar(self) -> list[dict[str, str]]:
        from pywinauto import Desktop

        desktop = Desktop(backend="win32")
        main_window = self._find_main_window(desktop)
        if main_window is None or not main_window.exists(timeout=2):
            raise RuntimeError("HTS main window is not open")
        top_limit = main_window.rectangle().top + 180
        controls: list[dict[str, str]] = []
        for idx, control in enumerate(main_window.descendants()[:400]):
            if not self._is_control_visible(control):
                continue
            rect = control.rectangle()
            if rect.top > top_limit:
                continue
            title = self._safe_window_text(control)
            class_name = self._safe_class_name(control)
            if not title and class_name not in {"Button", "ToolbarWindow32", "Static", "AfxWnd110"}:
                continue
            controls.append(
                {
                    "index": str(idx),
                    "title": self._clean_text_cell(title),
                    "class_name": class_name,
                    "rect": self._safe_rect_text(control),
                }
            )
        return controls

    def cancel_order(self, account_no: str, order_id: str) -> CancelResult:
        from pywinauto import Desktop

        desktop = Desktop(backend="win32")

        window = self._find_target_window(desktop, self.MINI_ORDER_WINDOW_RE)
        try:
            if window is None:
                window = self.reset_mini_order_window(desktop=desktop)
                if window is None:
                    return CancelResult(False, "HTS mini order window [2102] is not open")
        except Exception as exc:
            return CancelResult(False, f"failed to open mini order window: {exc}")
        form = self._locate_cancel_form(window)
        if not self._cancel_form_ready(form):
            try:
                self._ensure_modify_cancel_mode(window)
            except Exception as exc:
                return CancelResult(False, f"failed to switch mini order mode to 정정/취소: {exc}")
            time.sleep(0.3)
        form = self._locate_cancel_form(window)
        visible_order_ids = self._visible_order_ids(window)
        if form["cancel_button"] is None or (form["order_no_edit"] is None and order_id not in visible_order_ids):
            return CancelResult(False, "cancel form controls were not located")

        try:
            window.set_focus()
            if form["account_edit"] is not None and account_no:
                self._set_account_value(form["account_edit"], account_no)
            if form["order_no_edit"] is not None:
                self._set_edit_value(form["order_no_edit"], order_id)
            else:
                self._fill_cancel_order_id_by_label(window, order_id)
        except Exception as exc:
            return CancelResult(False, f"failed to fill cancel form: {exc}")
        if form["order_no_edit"] is not None:
            current_order_id = self._clean_text_cell(self._safe_window_text(form["order_no_edit"])).replace(",", "")
            if current_order_id != order_id:
                return CancelResult(False, f"cancel order number mismatch: expected {order_id}, got {current_order_id or '-'}")

        form["cancel_button"].click_input()
        popup_result = self._handle_cancel_confirmation(confirm=True)
        if popup_result["found"] != "true":
            return CancelResult(True, f"clicked cancel button for order {order_id}; confirmation popup not detected")
        if popup_result["clicked"] != "true":
            return CancelResult(False, popup_result["message"])
        self.close_window_by_re(self.MINI_ORDER_WINDOW_RE)
        verification = self._verify_order_removed(account_no, order_id)
        if verification["removed"] == "true":
            return CancelResult(True, f"clicked cancel button, confirmed popup, and order {order_id} disappeared from open orders")
        if verification["removed"] == "unknown":
            return CancelResult(True, f"clicked cancel button and confirmed popup for order {order_id}; post-cancel verification unavailable")
        return CancelResult(True, f"clicked cancel button and confirmed popup for order {order_id}; {verification['message']}")

    def probe_cancel_order(self, order_id: str, execute: bool = False) -> dict[str, str]:
        from pywinauto import Desktop

        try:
            desktop = Desktop(backend="win32")
        except Exception as exc:
            return {"ok": "false", "message": f"failed to connect desktop: {exc}"}

        window = self._find_target_window(desktop, self.MINI_ORDER_WINDOW_RE)
        if window is None:
            try:
                window = self.reset_mini_order_window(desktop=desktop)
            except Exception as exc:
                return {"ok": "false", "message": f"failed to open mini order window: {exc}"}
        if window is None:
            return {"ok": "false", "message": "HTS mini order window [2102] is not open"}
        form = self._locate_cancel_form(window)
        if not self._cancel_form_ready(form):
            try:
                self._ensure_modify_cancel_mode(window)
            except Exception as exc:
                return {"ok": "false", "message": f"failed to switch mini order mode to 정정/취소: {exc}"}
        form = self._locate_cancel_form(window)
        visible_order_ids = sorted(self._visible_order_ids(window))
        ready = form["cancel_button"] is not None and (form["order_no_edit"] is not None or order_id in visible_order_ids)
        result = {
            "ok": "true" if ready else "false",
            "order_id": order_id,
            "visible_ids": ",".join(visible_order_ids),
            "cancel_button": self._clean_text_cell(self._safe_window_text(form["cancel_button"])) if form["cancel_button"] else "",
            "order_no_field": self._describe_control(form["order_no_edit"]),
            "row_activated": "not_required",
            "executed": "false",
        }
        if not ready:
            result["message"] = "cancel form controls were not located"
            return result
        if form["order_no_edit"] is not None:
            self._set_edit_value(form["order_no_edit"], order_id)
        else:
            result["order_no_field"] = "coordinate-fallback"
        if execute:
            window.set_focus()
            if form["order_no_edit"] is None:
                self._fill_cancel_order_id_by_label(window, order_id)
            form["cancel_button"].click_input()
            popup = self._handle_cancel_confirmation(confirm=False)
            result["executed"] = "true"
            result["popup_found"] = popup["found"]
            result["popup_clicked"] = popup["clicked"]
            result["message"] = popup["message"] if popup["found"] == "true" else "clicked cancel button"
            return result
        result["message"] = "ready"
        return result

    def probe_place_controls(self) -> list[dict[str, str]]:
        from pywinauto import Desktop

        window = self._find_target_window(Desktop(backend="win32"), self.MINI_ORDER_WINDOW_RE)
        if window is None:
            raise RuntimeError("HTS mini order window [2102] is not open.")
        return self._collect_place_controls(window)

    def probe_place_order(self, order: OrderRequest, execute: bool = False) -> dict[str, str]:
        from pywinauto import Desktop

        desktop = Desktop(backend="win32")
        window = self._find_target_window(desktop, self.MINI_ORDER_WINDOW_RE)
        if window is None:
            try:
                window = self.reset_mini_order_window(desktop=desktop)
            except Exception as exc:
                return {"ok": "false", "message": f"failed to open mini order window: {exc}"}
        if window is None:
            return {"ok": "false", "message": "HTS mini order window [2102] is not open"}

        try:
            self._ensure_place_mode(window, order.side)
        except Exception as exc:
            return {"ok": "false", "message": f"failed to switch mini order mode: {exc}"}

        form = self._locate_place_form(window)
        normalized_account = self._normalize_account_no(order.account_no)
        current_account = self._clean_text_cell(self._safe_window_text(form.get("account_edit"))) if form.get("account_edit") else ""
        ready = self._place_form_ready(form, order.side)
        missing_fields = self._missing_form_fields(form, order.side)
        result = {
            "ok": "true" if ready else "false",
            "account_no": order.account_no,
            "normalized_account_no": normalized_account,
            "current_account_no": current_account,
            "account_matches": "true" if current_account == normalized_account else "false",
            "symbol": order.symbol,
            "side": order.side.value,
            "price": f"{order.price:.4f}",
            "qty": str(order.qty),
            "order_type": order.order_type or self.ORDER_TYPE_DEFAULT,
            "account_field": self._describe_control(form.get("account_edit")),
            "symbol_field": self._describe_control(form.get("symbol_edit")),
            "qty_field": self._describe_control(form.get("qty_edit")),
            "price_field": self._describe_control(form.get("price_edit")),
            "order_type_field": self._describe_control(form.get("order_type_edit")),
            "auto_qty_checkbox": self._describe_control(form.get("auto_qty_checkbox")),
            "auto_qty_unchecked": "not_checked",
            "buy_button": self._describe_control(form.get("buy_button")),
            "sell_button": self._describe_control(form.get("sell_button")),
            "missing_fields": ",".join(missing_fields) if missing_fields else "none",
            "executed": "false",
        }
        if execute:
            if result["ok"] != "true":
                result["message"] = f"missing controls: {','.join(missing_fields)}" if missing_fields else "one or more required controls were not located"
                return result
            try:
                try:
                    window.set_focus()
                except Exception:
                    pass
                auto_qty_status = self._ensure_auto_qty_unchecked(form)
                result["auto_qty_unchecked"] = auto_qty_status
                if auto_qty_status not in {"already_unchecked", "unchecked", "missing"}:
                    result["ok"] = "false"
                    result["message"] = f"failed to disable auto balance quantity: {auto_qty_status}"
                    return result
                steps = [
                    ("account", lambda: self._set_account_value(form["account_edit"], order.account_no)),
                    ("symbol", lambda: self._set_edit_value(form["symbol_edit"], order.symbol)),
                    ("order_type", lambda: self._set_edit_value(form["order_type_edit"], order.order_type or self.ORDER_TYPE_DEFAULT)),
                    ("qty", lambda: self._set_edit_value(form["qty_edit"], str(order.qty))),
                    ("price", lambda: self._set_edit_value(form["price_edit"], f"{order.price:.4f}")),
                ]
                for field_name, setter in steps:
                    try:
                        setter()
                    except Exception as exc:
                        raise RuntimeError(f"{field_name}: {exc}") from exc
                time.sleep(0.2)
            except Exception as exc:
                result["ok"] = "false"
                result["message"] = f"failed to populate order form: {exc}"
                return result
            verification = self._verify_place_form_values(form, order)
            if verification is not None:
                result["ok"] = "false"
                result["message"] = verification
                result["current_account_no"] = self._clean_text_cell(self._safe_window_text(form["account_edit"]))
                result["symbol_field"] = self._describe_control(form.get("symbol_edit"))
                result["qty_field"] = self._describe_control(form.get("qty_edit"))
                result["price_field"] = self._describe_control(form.get("price_edit"))
                result["order_type_field"] = self._describe_control(form.get("order_type_edit"))
                return result
            result["executed"] = "true"
            result["current_account_no"] = self._clean_text_cell(self._safe_window_text(form["account_edit"]))
            result["account_matches"] = "true" if result["current_account_no"] == normalized_account else "false"
            result["symbol_field"] = self._describe_control(form.get("symbol_edit"))
            result["qty_field"] = self._describe_control(form.get("qty_edit"))
            result["price_field"] = self._describe_control(form.get("price_edit"))
            result["order_type_field"] = self._describe_control(form.get("order_type_edit"))
            result["message"] = (
                "form populated without clicking order button; "
                f"auto_qty={result.get('auto_qty_unchecked', 'unknown')}"
            )
            return result
        if result["ok"] == "true":
            result["message"] = "ready"
        else:
            result["message"] = f"missing controls: {','.join(missing_fields)}" if missing_fields else "one or more required controls were not located"
        return result

    def get_daily_fills(self, account_no: str, symbol: str, trade_date: str) -> list[Fill]:
        return []

    def probe_cancel_controls(self) -> list[dict[str, str]]:
        from pywinauto import Desktop

        window = self._find_target_window(Desktop(backend="win32"), self.MINI_ORDER_WINDOW_RE)
        if window is None:
            raise RuntimeError("HTS mini order window [2102] is not open.")
        return self._collect_cancel_controls(window)

    def _collect_cancel_controls(self, window: Any) -> list[dict[str, str]]:
        controls: list[dict[str, str]] = []
        for idx, control in enumerate(window.descendants()[:200]):
            if not self._is_control_visible(control):
                continue
            title = self._safe_window_text(control)
            class_name = self._safe_class_name(control)
            if not title and class_name not in {"Edit", "Button"}:
                continue
            controls.append({"index": str(idx), "title": title, "class_name": class_name})
        return controls

    def _collect_place_controls(self, window: Any) -> list[dict[str, str]]:
        controls: list[dict[str, str]] = []
        for idx, control in enumerate(window.descendants()[:200]):
            if not self._is_control_visible(control):
                continue
            title = self._safe_window_text(control)
            class_name = self._safe_class_name(control)
            if not title and class_name not in {"Edit", "Button", "Static"}:
                continue
            controls.append(
                {
                    "index": str(idx),
                    "title": title,
                    "class_name": class_name,
                    "rect": self._safe_rect_text(control),
                }
            )
        return controls

    def _locate_cancel_form(self, window: Any) -> dict[str, Any]:
        return {
            "account_edit": self._find_account_edit(window),
            "order_no_edit": self._find_labeled_edit(
                window,
                self.ORDER_NO_LABEL_TEXT,
                prefer_numeric=True,
                integer_only=True,
                allow_empty=True,
            )
            or self._find_cancel_order_no_edit(window),
            "cancel_button": self._find_cancel_button(window),
        }

    def _find_cancel_order_no_edit(self, window: Any) -> Any | None:
        cancel_button = self._find_cancel_button(window)
        labels = self._find_anchors_by_text(window, self.ORDER_NO_LABEL_TEXT)
        candidates: list[tuple[int, int, int, Any]] = []
        win_rect = window.rectangle()
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            if self._safe_class_name(control) != "Edit":
                continue
            rect = control.rectangle()
            rel_top = rect.top - win_rect.top
            rel_left = rect.left - win_rect.left
            if rel_top < 70 or rel_top > 220 or rel_left < 25:
                continue
            text = self._clean_text_cell(self._safe_window_text(control))
            score = 0 if not text else 2
            if labels:
                label_rect = labels[0].rectangle()
                if rect.top < label_rect.top - 15 or rect.left < label_rect.left:
                    continue
                score += abs(rect.top - label_rect.top) + max(0, rect.left - label_rect.right)
            if cancel_button is not None:
                button_rect = cancel_button.rectangle()
                if rect.top > button_rect.top:
                    continue
                score += abs(rect.left - button_rect.left) // 10
            candidates.append((score, rect.top, rect.left, control))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def _fill_cancel_order_id_by_label(self, window: Any, order_id: str) -> None:
        from pywinauto import keyboard, mouse

        labels = self._find_anchors_by_text(window, self.ORDER_NO_LABEL_TEXT)
        if not labels:
            raise RuntimeError("cancel order number label was not located")
        labels.sort(key=lambda control: (control.rectangle().top, control.rectangle().left))
        label = labels[0]
        label_rect = label.rectangle()
        click_x = label_rect.right + 45
        click_y = (label_rect.top + label_rect.bottom) // 2
        window.set_focus()
        mouse.click(button="left", coords=(click_x, click_y))
        time.sleep(0.1)
        keyboard.send_keys("^a{BACKSPACE}")
        keyboard.send_keys(order_id, with_spaces=True)
        time.sleep(0.1)

    def _first_open_order_state(self, open_orders_window: Any, order_id: str) -> str:
        if open_orders_window is None:
            return "missing"
        try:
            raw = self._copy_row_text(self.OPEN_ORDERS_WINDOW_RE, self.OPEN_ORDER_ROW_X, self.OPEN_ORDER_ROW_Y)
        except Exception:
            raw = ""
        if not self._clean_text_cell(raw):
            return "empty"
        if order_id in raw:
            return "match"
        return "other"

    def _click_first_open_order_row(self, open_orders_window: Any) -> bool:
        from pywinauto import mouse

        if open_orders_window is None:
            return False
        try:
            open_orders_window.set_focus()
        except Exception:
            pass
        rect = open_orders_window.rectangle()
        row_point = (
            int(rect.left + rect.width() * 0.78),
            int(rect.top + rect.height() * self.OPEN_ORDER_ROW_Y),
        )
        mouse.click(button="left", coords=row_point)
        time.sleep(0.55)
        return True

    def _visible_order_ids(self, window: Any) -> set[str]:
        return {
            self._clean_text_cell(control["title"])
            for control in self._collect_cancel_controls(window)
            if self._clean_text_cell(control["title"]).isdigit()
        }

    def _find_cancel_button(self, window: Any) -> Any | None:
        exact_match = None
        fallback = None
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            if self._safe_class_name(control) != "Button":
                continue
            title = self._clean_text_cell(self._safe_window_text(control))
            if title == "\uCDE8\uC18C(F8)":
                exact_match = control
                break
            if title.startswith(self.CANCEL_BUTTON_TEXT):
                fallback = control
        return exact_match or fallback

    def _ensure_place_mode(self, window: Any, side: Side) -> None:
        target = self.BUY_BUTTON_LABEL if side == Side.BUY else self.SELL_BUTTON_LABEL
        expected_action = self.BUY_ACTION_BUTTON_LABEL if side == Side.BUY else self.SELL_ACTION_BUTTON_LABEL
        self._click_mode_button(window, target, expected_action, lambda: self._place_form_ready(self._locate_place_form(window), side))

    def _ensure_modify_cancel_mode(self, window: Any) -> None:
        self._click_mode_button(
            window,
            self.MODIFY_CANCEL_BUTTON_LABEL,
            self.CANCEL_ACTION_BUTTON_LABEL,
            lambda: self._cancel_form_ready(self._locate_cancel_form(window)),
        )

    def _click_mode_button(
        self,
        window: Any,
        label: str,
        expected_action_label: str,
        ready_check: Callable[[], bool] | None = None,
    ) -> None:
        from pywinauto import mouse

        if self._find_visible_button_by_exact_text(window, expected_action_label) is not None:
            return
        if ready_check is not None and ready_check():
            return

        window.set_focus()
        button = self._find_mode_button(window, label)
        if button is not None:
            try:
                button.click_input()
            except Exception:
                button = None
        if button is None or self._find_visible_button_by_exact_text(window, expected_action_label) is None:
            rect = window.rectangle()
            offset = {
                self.BUY_BUTTON_LABEL: (40, 64),
                self.SELL_BUTTON_LABEL: (105, 64),
                self.MODIFY_CANCEL_BUTTON_LABEL: (170, 64),
            }[label]
            coords = (rect.left + offset[0], rect.top + offset[1])
            mouse.click(button="left", coords=coords)
        time.sleep(0.25)
        if self._find_visible_button_by_exact_text(window, expected_action_label) is not None:
            return
        if ready_check is not None and ready_check():
            return
        if self._find_visible_button_by_exact_text(window, expected_action_label) is None:
            raise RuntimeError(f"failed to switch mini order mode to {label}")

    def _find_mode_button(self, window: Any, label: str) -> Any | None:
        exact_match = None
        fallback = None
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            rect = control.rectangle()
            if rect.top > window.rectangle().top + 120:
                continue
            title = self._clean_text_cell(self._safe_window_text(control))
            if not title:
                continue
            if title == label:
                exact_match = control
                break
            if label in title:
                fallback = control
        return exact_match or fallback

    def _locate_place_form(self, window: Any) -> dict[str, Any]:
        return {
            "account_edit": self._find_account_edit(window),
            "symbol_edit": self._find_labeled_edit(window, self.SYMBOL_LABEL_TEXT, prefer_textual=True, allow_empty=True),
            "qty_edit": self._find_labeled_edit(
                window,
                self.QTY_LABEL_TEXT,
                prefer_numeric=True,
                integer_only=True,
                allow_empty=True,
            ),
            "price_edit": self._find_price_edit(window),
            "order_type_edit": self._find_labeled_edit(window, self.ORDER_TYPE_LABEL_TEXT, prefer_textual=True, allow_empty=True),
            "auto_qty_checkbox": self._find_auto_qty_checkbox(window),
            "buy_button": self._find_button_by_text(window, self.BUY_BUTTON_LABEL),
            "sell_button": self._find_button_by_text(window, self.SELL_BUTTON_LABEL),
        }

    def _find_auto_qty_checkbox(self, window: Any) -> Any | None:
        fallback = None
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            if self._safe_class_name(control) != "Button":
                continue
            title = self._clean_text_cell(self._safe_window_text(control))
            if not title:
                continue
            if title.startswith(self.AUTO_BALANCE_QTY_TEXT):
                return control
            if "\uC790\uB3D9" in title and "\uC794\uACE0" in title:
                fallback = control
        return fallback

    def _ensure_auto_qty_unchecked(self, form: dict[str, Any]) -> str:
        checkbox = form.get("auto_qty_checkbox")
        if checkbox is None:
            return "missing"
        state = self._button_check_state(checkbox)
        if state == self.BST_UNCHECKED:
            return "already_unchecked"
        if state is None:
            return "unknown_state"
        try:
            checkbox.click_input()
        except Exception:
            if not self._click_control_center(checkbox):
                return "click_failed"
        time.sleep(0.15)
        updated = self._button_check_state(checkbox)
        if updated == self.BST_UNCHECKED:
            return "unchecked"
        return f"still_checked:{updated}"

    def _button_check_state(self, control: Any) -> int | None:
        try:
            return int(control.get_check_state())
        except Exception:
            pass
        try:
            handle = self._safe_handle(control)
            return int(win32gui.SendMessage(handle, self.BM_GETCHECK, 0, 0))
        except Exception:
            return None

    @staticmethod
    def _place_form_ready(form: dict[str, Any], side: Side) -> bool:
        required = ["account_edit", "symbol_edit", "qty_edit", "price_edit", "order_type_edit"]
        action_key = "buy_button" if side == Side.BUY else "sell_button"
        return all(form.get(key) is not None for key in required + [action_key])

    @staticmethod
    def _missing_form_fields(form: dict[str, Any], side: Side) -> list[str]:
        required = ["account_edit", "symbol_edit", "qty_edit", "price_edit", "order_type_edit"]
        action_key = "buy_button" if side == Side.BUY else "sell_button"
        return [key for key in required + [action_key] if form.get(key) is None]

    @staticmethod
    def _cancel_form_ready(form: dict[str, Any]) -> bool:
        return form.get("cancel_button") is not None

    def _find_account_edit(self, window: Any) -> Any | None:
        win_rect = window.rectangle()
        candidates: list[tuple[int, int, int, Any]] = []
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            class_name = self._safe_class_name(control)
            if class_name not in {"Edit", "ComboBox", "AfxWnd110"}:
                continue
            text = self._clean_text_cell(self._safe_window_text(control))
            rect = control.rectangle()
            rel_top = rect.top - win_rect.top
            rel_left = rect.left - win_rect.left
            if rel_top > 260:
                continue
            score = 2
            if re.fullmatch(self.ACCOUNT_PATTERN, text):
                score = 0
            elif re.fullmatch(r"^\d{8}$", re.sub(r"\D", "", text)):
                score = 1
            elif rel_left < 245 and rel_top < 235 and class_name in {"Edit", "ComboBox"}:
                score = 3
            else:
                continue
            candidates.append((score, rect.top, rect.left, control))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def _find_price_edit(self, window: Any) -> Any | None:
        for label_text in self.PRICE_LABEL_TEXTS:
            result = self._find_labeled_edit(window, label_text, prefer_numeric=True)
            if result is not None:
                return result
        return None

    def _find_labeled_edit(
        self,
        window: Any,
        label_text: str,
        prefer_numeric: bool = False,
        integer_only: bool = False,
        prefer_textual: bool = False,
        allow_empty: bool = False,
    ) -> Any | None:
        labels = self._find_anchors_by_text(window, label_text)
        if not labels:
            return None

        candidates: list[tuple[int, int, int, Any]] = []
        for label in labels:
            label_rect = label.rectangle()
            for control in window.descendants():
                if not self._is_control_visible(control):
                    continue
                if self._safe_class_name(control) != "Edit":
                    continue
                text = self._clean_text_cell(self._safe_window_text(control))
                if prefer_numeric and not (
                    self._looks_numeric_text(text, integer_only=integer_only) or (allow_empty and not text)
                ):
                    continue
                if prefer_textual and not text and not allow_empty:
                    continue
                rect = control.rectangle()
                if rect.left <= label_rect.left:
                    continue
                vertical_gap = abs(rect.top - label_rect.top)
                if vertical_gap > 40:
                    continue
                vertical_overlap = min(rect.bottom, label_rect.bottom) - max(rect.top, label_rect.top)
                if vertical_overlap <= 0:
                    continue
                horizontal_gap = rect.left - label_rect.right
                if horizontal_gap < -20 or horizontal_gap > 220:
                    continue
                center_gap = abs(((rect.top + rect.bottom) // 2) - ((label_rect.top + label_rect.bottom) // 2))
                candidates.append((center_gap, vertical_gap, max(horizontal_gap, 0), -vertical_overlap, control))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        return candidates[0][4]

    def _find_anchor_by_text(self, window: Any, text: str) -> Any | None:
        anchors = self._find_anchors_by_text(window, text)
        return anchors[0] if anchors else None

    def _find_anchors_by_text(self, window: Any, text: str) -> list[Any]:
        exact = None
        exact_matches: list[Any] = []
        fallback_matches: list[Any] = []
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            title = self._clean_text_cell(self._safe_window_text(control))
            if not title:
                continue
            if title == text:
                exact_matches.append(control)
                continue
            if text in title:
                fallback_matches.append(control)
        if exact_matches:
            exact_matches.sort(key=lambda control: (control.rectangle().top, control.rectangle().left))
            return exact_matches
        fallback_matches.sort(key=lambda control: (control.rectangle().top, control.rectangle().left))
        return fallback_matches

    @staticmethod
    def _looks_numeric_text(text: str, integer_only: bool = False) -> bool:
        cleaned = KiwoomHybridBroker._clean_text_cell(text).replace(",", "")
        if not cleaned:
            return False
        pattern = r"^-?\d+$" if integer_only else r"^-?\d+(?:\.\d+)?$"
        return re.fullmatch(pattern, cleaned) is not None

    def _describe_control(self, control: Any | None) -> str:
        if control is None:
            return ""
        title = self._clean_text_cell(self._safe_window_text(control))
        class_name = self._safe_class_name(control)
        rect = self._safe_rect_text(control)
        return f"{class_name}:{title}@{rect}"

    def _handle_cancel_confirmation(self, confirm: bool) -> dict[str, str]:
        return self._handle_confirmation_window(self.CANCEL_CONFIRM_WINDOW_RE, confirm)

    def _handle_place_confirmation(self, confirm: bool) -> dict[str, str]:
        from pywinauto import Desktop

        desktop = Desktop(backend="win32")
        for _ in range(self.POPUP_RETRY_COUNT):
            rejected = self._handle_order_rejected_popup()
            if rejected is not None:
                return {"found": "true", "clicked": "false", "message": rejected}

            popup = self._find_target_window(desktop, self.PLACE_CONFIRM_WINDOW_RE)
            if popup is not None:
                button = self._find_button_by_text(popup, self.CONFIRM_BUTTON_TEXT if confirm else self.CANCEL_BUTTON_TEXT)
                if button is None:
                    return {"found": "true", "clicked": "false", "message": "order confirmation dialog opened but target button was not found"}
                popup.set_focus()
                button.click_input()
                return {"found": "true", "clicked": "true", "message": "confirmed order popup"}

            info = self._find_info_popup(desktop)
            if info is not None:
                message = self._window_text_summary(info)
                button = self._find_button_by_text(info, self.CONFIRM_BUTTON_TEXT)
                if button is not None:
                    info.set_focus()
                    button.click_input()
                    return {"found": "true", "clicked": "false", "message": message or "order rejected by HTS 안내 popup"}
                return {"found": "true", "clicked": "false", "message": message or "HTS 안내 popup opened but 확인 button was not found"}

            time.sleep(self.POPUP_RETRY_DELAY_SEC)
        return {"found": "false", "clicked": "false", "message": "order confirmation dialog was not detected"}

    def _find_info_popup(self, desktop: Any) -> Any | None:
        hwnd = self._find_info_popup_handle()
        if hwnd is not None:
            return desktop.window(handle=hwnd)

        popup = self._find_target_window(desktop, self.INFO_WINDOW_RE)
        if popup is not None:
            return popup

        handles: list[int] = []

        def _collect_top(hwnd: int, _param: Any) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = self._safe_handle_text(hwnd)
            try:
                class_name = win32gui.GetClassName(hwnd)
            except Exception:
                class_name = ""
            if "\uC548\uB0B4" in title or class_name == "#32770":
                handles.append(hwnd)

        win32gui.EnumWindows(_collect_top, None)
        for hwnd in handles:
            try:
                window = desktop.window(handle=hwnd)
            except Exception:
                continue
            title = self._safe_handle_text(hwnd)
            message = self._window_text_summary(window)
            if "\uC548\uB0B4" in title or any(marker in message for marker in self.ORDER_UNAVAILABLE_MARKERS):
                return window
        return None

    def _handle_order_rejected_popup(self) -> str | None:
        foreground_message = self._handle_foreground_info_popup()
        if foreground_message is not None:
            return foreground_message

        info_hwnd = self._find_info_popup_handle()
        if info_hwnd is None:
            return None
        message = self._window_text_summary_by_handle(info_hwnd)
        if not any(marker in message for marker in self.ORDER_UNAVAILABLE_MARKERS):
            return None
        if self._click_child_button_by_text(info_hwnd, self.CONFIRM_BUTTON_TEXT) or self._post_ok_to_window(info_hwnd):
            return message or "order rejected by HTS info popup"
        return message or "HTS info popup opened but confirm button was not found"

    def _raise_if_order_unavailable_popup(self) -> None:
        message = self._handle_order_rejected_popup()
        if message is not None:
            raise RuntimeError(message)

    def _handle_foreground_info_popup(self) -> str | None:
        try:
            hwnd = win32gui.GetForegroundWindow()
        except Exception:
            return None
        if not hwnd:
            return None
        title = self._safe_handle_text(hwnd)
        message = self._window_text_summary_by_handle(hwnd)
        if "\uC548\uB0B4" not in title and not any(marker in message for marker in self.ORDER_UNAVAILABLE_MARKERS):
            return None
        self._click_child_button_by_text(hwnd, self.CONFIRM_BUTTON_TEXT)
        self._post_ok_to_window(hwnd)
        self._post_enter_to_window(hwnd)
        time.sleep(0.2)
        return message or title or "HTS info popup"

    def _find_info_popup_handle(self) -> int | None:
        handles: list[int] = []
        seen: set[int] = set()

        def _consider(hwnd: int) -> None:
            if hwnd in seen:
                return
            seen.add(hwnd)
            title = self._safe_handle_text(hwnd)
            try:
                class_name = win32gui.GetClassName(hwnd)
            except Exception:
                class_name = ""
            message = self._window_text_summary_by_handle(hwnd)
            if "\uC548\uB0B4" in title or class_name == "#32770" or any(marker in message for marker in self.ORDER_UNAVAILABLE_MARKERS):
                handles.append(hwnd)

        win32gui.EnumWindows(lambda hwnd, _param: _consider(hwnd), None)
        top_windows = list(handles)
        def _collect_top(hwnd: int, _param: Any) -> None:
            if hwnd not in top_windows:
                top_windows.append(hwnd)

        win32gui.EnumWindows(_collect_top, None)
        for top_hwnd in top_windows:
            try:
                win32gui.EnumChildWindows(top_hwnd, lambda hwnd, _param: _consider(hwnd), None)
            except Exception:
                pass
        for hwnd in handles:
            title = self._safe_handle_text(hwnd)
            message = self._window_text_summary_by_handle(hwnd)
            if "\uC548\uB0B4" in title or any(marker in message for marker in self.ORDER_UNAVAILABLE_MARKERS):
                return hwnd
        return None

    def _window_text_summary_by_handle(self, hwnd: int) -> str:
        parts: list[str] = []

        def _add(text: str) -> None:
            text = self._clean_text_cell(text)
            if text and text not in parts:
                parts.append(text)

        _add(self._safe_handle_text(hwnd))

        def _collect(child: int, _param: Any) -> None:
            _add(self._safe_handle_text(child))

        try:
            win32gui.EnumChildWindows(hwnd, _collect, None)
        except Exception:
            pass
        return " ".join(parts)

    def _click_child_button_by_text(self, hwnd: int, text: str) -> bool:
        matches: list[int] = []

        def _collect(child: int, _param: Any) -> None:
            if not win32gui.IsWindowVisible(child):
                return
            try:
                class_name = win32gui.GetClassName(child)
            except Exception:
                class_name = ""
            title = self._clean_text_cell(self._safe_handle_text(child))
            if class_name == "Button" and (title == text or text in title):
                matches.append(child)

        try:
            win32gui.EnumChildWindows(hwnd, _collect, None)
        except Exception:
            return False
        if not matches:
            return False
        try:
            win32gui.PostMessage(matches[0], 0x00F5, 0, 0)
            return True
        except Exception:
            return False

    def _post_ok_to_window(self, hwnd: int) -> bool:
        try:
            win32gui.PostMessage(hwnd, 0x0111, 1, 0)
            return True
        except Exception:
            return False

    def _post_enter_to_window(self, hwnd: int) -> bool:
        try:
            win32gui.PostMessage(hwnd, 0x0100, 0x0D, 0)
            win32gui.PostMessage(hwnd, 0x0101, 0x0D, 0)
            return True
        except Exception:
            return False

    def _click_control_center(self, control: Any) -> bool:
        from pywinauto import mouse

        try:
            rect = control.rectangle()
            mouse.click(button="left", coords=((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
            return True
        except Exception:
            return False

    def _click_order_button(self, control: Any) -> str:
        if self._click_control_center(control):
            return "mouse"
        if self._click_control_input(control):
            return "click_input"
        if self._click_control_by_message(control):
            return "bm_click"
        return ""

    def _click_order_button_attempts(self, control: Any):
        if self._click_control_center(control):
            yield "mouse"
        if self._click_control_input(control):
            yield "click_input"
        if self._click_control_by_message(control):
            yield "bm_click"

    @staticmethod
    def _click_control_input(control: Any) -> bool:
        try:
            control.click_input()
            return True
        except Exception:
            return False

    @staticmethod
    def _click_control_by_message(control: Any) -> bool:
        try:
            handle = int(control.handle)
        except Exception:
            handle = 0
        if not handle:
            return False
        try:
            win32gui.PostMessage(handle, 0x00F5, 0, 0)
            return True
        except Exception:
            return False

    def _handle_confirmation_window(self, window_re: str, confirm: bool) -> dict[str, str]:
        from pywinauto import Desktop

        for _ in range(self.POPUP_RETRY_COUNT):
            popup = self._find_target_window(Desktop(backend="win32"), window_re)
            if popup is not None:
                button = self._find_button_by_text(popup, self.CONFIRM_BUTTON_TEXT if confirm else self.CANCEL_BUTTON_TEXT)
                if button is None:
                    return {"found": "true", "clicked": "false", "message": "confirmation dialog opened but target button was not found"}
                popup.set_focus()
                button.click_input()
                return {
                    "found": "true",
                    "clicked": "true",
                    "message": "confirmed cancel popup" if confirm else "closed cancel popup",
                }
            time.sleep(self.POPUP_RETRY_DELAY_SEC)
        return {"found": "false", "clicked": "false", "message": "confirmation dialog was not detected"}

    def _find_button_by_text(self, window: Any, text: str) -> Any | None:
        fallback = None
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            if self._safe_class_name(control) != "Button":
                continue
            title = self._clean_text_cell(self._safe_window_text(control))
            if title == text:
                return control
            if text in title:
                fallback = control
        return fallback

    def _window_text_summary(self, window: Any) -> str:
        parts: list[str] = []
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            text = self._clean_text_cell(self._safe_window_text(control))
            if text and text not in parts:
                parts.append(text)
        return " ".join(parts)

    def _find_visible_button_by_exact_text(self, window: Any, text: str) -> Any | None:
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            if self._safe_class_name(control) != "Button":
                continue
            if self._clean_text_cell(self._safe_window_text(control)) == text:
                return control
        return None

    def _click_query_button(self, window: Any) -> bool:
        button = self._find_button_by_text(window, self.QUERY_BUTTON_TEXT)
        if button is None:
            return False
        try:
            window.set_focus()
        except Exception:
            pass
        try:
            button.click_input()
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def _find_visible_control_by_exact_text_any_class(self, window: Any, text: str) -> Any | None:
        target = self._clean_text_cell(text)
        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            if self._clean_text_cell(self._safe_window_text(control)) == target:
                return control
        return None

    def _verify_order_removed(self, account_no: str, order_id: str) -> dict[str, str]:
        from pywinauto import Desktop

        last_message = "post-cancel verification unavailable"
        for _ in range(self.POST_CANCEL_RETRY_COUNT):
            try:
                desktop = Desktop(backend="win32")
                window = self._ensure_window_open(desktop, self.OPEN_ORDERS_WINDOW_CODE, self.OPEN_ORDERS_WINDOW_RE)
                if window is not None and account_no:
                    self._verify_window_account_selected(window, account_no)
                time.sleep(1.0)
                row_state = self._first_open_order_state(window, order_id)
            except Exception as exc:
                time.sleep(self.POST_CANCEL_RETRY_DELAY_SEC)
                last_message = f"re-open-order copy failed: {exc}"
                continue
            finally:
                self.close_window_by_re(self.OPEN_ORDERS_WINDOW_RE)

            if row_state == "empty":
                return {"removed": "true", "message": "open orders grid is empty after cancel"}
            if row_state == "other":
                return {"removed": "true", "message": "first open-order row no longer matches cancelled order"}
            if row_state == "missing":
                last_message = "open orders window could not be located during post-cancel verification"
            else:
                last_message = "order id still present in first open-order row"
            time.sleep(self.POST_CANCEL_RETRY_DELAY_SEC)
        if "No text clipboard format available" in last_message:
            return {"removed": "unknown", "message": last_message}
        return {"removed": "false", "message": last_message}

    def _verify_order_created(self, order: OrderRequest) -> dict[str, str]:
        for _ in range(self.POST_PLACE_RETRY_COUNT):
            rejected = self._handle_order_rejected_popup()
            if rejected is not None:
                return {"created": "false", "message": rejected}
            try:
                open_orders = self.get_open_orders(order.account_no, order.symbol)
            except Exception as exc:
                last_message = f"re-open-order read failed: {exc}"
                time.sleep(self.POST_PLACE_RETRY_DELAY_SEC)
                continue
            for open_order in open_orders:
                if self._matches_created_order(order, open_order):
                    return {"created": "true", "order_id": open_order.order_id, "message": "matching open order detected"}
            last_message = "matching open order was not detected; order may have filled immediately"
            time.sleep(self.POST_PLACE_RETRY_DELAY_SEC)
        return {"created": "unknown", "message": last_message}

    @staticmethod
    def _matches_created_order(order: OrderRequest, open_order: OpenOrder) -> bool:
        if order.symbol.upper() != open_order.symbol.upper():
            return False
        if order.side != open_order.side:
            return False
        if open_order.remaining_qty != order.qty:
            return False
        return abs(open_order.price - order.price) <= 0.02

    def _set_edit_value(self, control: Any, value: str) -> None:
        from pywinauto import keyboard

        try:
            control.set_edit_text("")
            control.set_edit_text(value)
            time.sleep(0.1)
            if self._clean_text_cell(self._safe_window_text(control)) == value:
                return
        except Exception:
            pass

        try:
            handle = int(control.handle)
        except Exception:
            handle = 0
        if handle:
            try:
                import win32con

                win32gui.SendMessage(handle, win32con.WM_SETTEXT, 0, value)
                time.sleep(0.1)
                if self._clean_text_cell(self._safe_window_text(control)) == value:
                    return
            except Exception:
                pass

        control.set_focus()
        try:
            control.click_input()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            control.set_edit_text("")
            control.set_edit_text(value)
        except Exception:
            keyboard.send_keys("^a{BACKSPACE}")
            keyboard.send_keys(value, with_spaces=True)
        time.sleep(0.1)
        keyboard.send_keys("{TAB}")
        time.sleep(0.1)

    def _set_search_edit_value(self, control: Any, value: str) -> None:
        from pywinauto import keyboard

        try:
            handle = int(control.handle)
        except Exception:
            handle = 0
        if handle:
            try:
                import win32con

                win32gui.SendMessage(handle, win32con.WM_SETTEXT, 0, value)
                time.sleep(0.05)
                if self._clean_text_cell(self._safe_window_text(control)) == value:
                    return
            except Exception:
                pass

        try:
            control.set_edit_text("")
            control.set_edit_text(value)
            time.sleep(0.05)
            if self._clean_text_cell(self._safe_window_text(control)) == value:
                return
        except Exception:
            pass

        control.set_focus()
        try:
            control.click_input()
        except Exception:
            pass
        time.sleep(0.05)
        try:
            control.set_edit_text("")
            control.set_edit_text(value)
        except Exception:
            keyboard.send_keys("^a{BACKSPACE}")
            keyboard.send_keys(value, with_spaces=True)
        time.sleep(0.05)

    def _set_account_value(self, control: Any, account_no: str) -> None:
        normalized = self._normalize_account_no(account_no)
        current = self._clean_text_cell(self._safe_window_text(control))
        if current == normalized:
            return
        self._set_edit_value(control, normalized)
        updated = self._clean_text_cell(self._safe_window_text(control))
        if updated and updated != normalized:
            raise RuntimeError(f"account field stayed on {updated} instead of {normalized}")

    def _verify_place_form_values(self, form: dict[str, Any], order: OrderRequest) -> str | None:
        expected_account = self._normalize_account_no(order.account_no)
        current_account = self._clean_text_cell(self._safe_window_text(form["account_edit"]))
        current_symbol = self._clean_text_cell(self._safe_window_text(form["symbol_edit"])).upper()
        current_order_type = self._clean_text_cell(self._safe_window_text(form["order_type_edit"]))
        current_qty = self._clean_text_cell(self._safe_window_text(form["qty_edit"])).replace(",", "")
        current_price = self._clean_text_cell(self._safe_window_text(form["price_edit"])).replace(",", "")

        if current_account != expected_account:
            return f"account field mismatch: expected {expected_account}, got {current_account or '-'}"
        if current_symbol != order.symbol.upper():
            return f"symbol field mismatch: expected {order.symbol.upper()}, got {current_symbol or '-'}"
        if current_order_type != (order.order_type or self.ORDER_TYPE_DEFAULT):
            return f"order type mismatch: expected {order.order_type or self.ORDER_TYPE_DEFAULT}, got {current_order_type or '-'}"
        if current_qty != str(order.qty):
            return f"qty field mismatch: expected {order.qty}, got {current_qty or '-'}"
        try:
            if abs(float(current_price) - order.price) > 0.0001:
                return f"price field mismatch: expected {order.price:.4f}, got {current_price or '-'}"
        except Exception:
            return f"price field mismatch: expected {order.price:.4f}, got {current_price or '-'}"
        return None

    @staticmethod
    def _normalize_account_no(account_no: str) -> str:
        digits = re.sub(r"\D", "", account_no)
        if len(digits) == 8:
            return f"{digits[:4]}-{digits[4:]}"
        return account_no.strip()

    def _copy_row_text(self, window_re: str, row_x: float, row_y: float) -> str:
        from pywinauto import Desktop, keyboard, mouse

        self._raise_if_order_unavailable_popup()
        window = self._find_target_window(Desktop(backend="win32"), window_re)
        if window is None:
            raise RuntimeError(
                f"HTS target window is not open: {window_re}. "
                "If the HTS is running as administrator, run PowerShell as administrator too."
            )

        window.set_focus()
        rect = window.rectangle()
        row_point = (
            int(rect.left + rect.width() * row_x),
            int(rect.top + rect.height() * row_y),
        )
        mouse.click(button="left", coords=row_point)
        time.sleep(0.3)
        self._raise_if_order_unavailable_popup()

        tag = f"[copy row_y={row_y:.2f}]"

        # 1a) {DOWN} + Ctrl+C — activate first data row then copy
        self._clear_clipboard()
        self._raise_if_order_unavailable_popup()
        keyboard.send_keys("{DOWN}")
        time.sleep(0.1)
        self._raise_if_order_unavailable_popup()
        keyboard.send_keys("^c")
        copied = self._read_clipboard_text_optional()
        print(f"  {tag} step1a DOWN+Ctrl+C: {'ok' if copied else 'empty'}", flush=True)
        if copied:
            return copied

        # 1b) Ctrl+A + Ctrl+C
        self._clear_clipboard()
        self._raise_if_order_unavailable_popup()
        keyboard.send_keys("^a")
        time.sleep(0.05)
        self._raise_if_order_unavailable_popup()
        keyboard.send_keys("^c")
        copied = self._read_clipboard_text_optional()
        print(f"  {tag} step1b Ctrl+A+C: {'ok' if copied else 'empty'}", flush=True)
        if copied:
            return copied

        # 2) Right-click → find 복사 item in popup menu and click it
        self._clear_clipboard()
        self._raise_if_order_unavailable_popup()
        mouse.click(button="right", coords=row_point)
        time.sleep(0.4)
        self._raise_if_order_unavailable_popup()
        menu_visible = self._win32_popup_menu_visible()
        print(f"  {tag} step2 right-click: popup_menu={menu_visible}", flush=True)
        if menu_visible:
            clicked = self._click_copy_in_win32_popup_menu()
            print(f"  {tag} step2 click copy item: {clicked}", flush=True)
            if not clicked:
                keyboard.send_keys("{ESC}")
            time.sleep(0.2)
        copied = self._read_clipboard_text_optional()
        print(f"  {tag} step2 result: {'ok' if copied else 'empty'}", flush=True)
        if copied:
            return copied

        # 3) Shift+F10 context menu → same approach
        self._clear_clipboard()
        self._raise_if_order_unavailable_popup()
        mouse.click(button="left", coords=row_point)
        time.sleep(0.15)
        self._raise_if_order_unavailable_popup()
        keyboard.send_keys("+{F10}")
        time.sleep(0.3)
        self._raise_if_order_unavailable_popup()
        menu_visible = self._win32_popup_menu_visible()
        print(f"  {tag} step3 Shift+F10: popup_menu={menu_visible}", flush=True)
        if menu_visible:
            clicked = self._click_copy_in_win32_popup_menu()
            print(f"  {tag} step3 click copy item: {clicked}", flush=True)
            if not clicked:
                keyboard.send_keys("{ESC}")
            time.sleep(0.2)
        copied = self._read_clipboard_text_optional()
        print(f"  {tag} step3 result: {'ok' if copied else 'empty'}", flush=True)
        if copied:
            return copied

        # 4) Scrape via SysListView32 messages / visible controls
        self._raise_if_order_unavailable_popup()
        scraped = self._scrape_visible_row_text(window, row_y)
        print(f"  {tag} step4 scrape: {'ok len=' + str(len(scraped)) if scraped else 'empty'}", flush=True)
        if scraped:
            return scraped

        raise RuntimeError("failed to copy or scrape HTS grid row")

    def _scrape_visible_row_text(self, window: Any, row_y: float) -> str:
        # Try reading via SysListView32 Windows messages first (works for owner-drawn grids).
        lv_result = self._scrape_listview_row(window, row_y)
        if lv_result:
            return lv_result

        rect = window.rectangle()
        target_y = int(rect.top + rect.height() * row_y)
        bands: dict[int, list[tuple[int, str]]] = {}

        for control in window.descendants():
            if not self._is_control_visible(control):
                continue
            text = self._clean_text_cell(self._safe_window_text(control))
            if not text:
                continue
            control_rect = control.rectangle()
            if control_rect.width() <= 2 or control_rect.height() <= 2:
                continue
            if control_rect.left < rect.left or control_rect.right > rect.right + 4:
                continue
            center_y = int((control_rect.top + control_rect.bottom) / 2)
            center_x = int((control_rect.left + control_rect.right) / 2)
            bands.setdefault(center_y, []).append((center_x, text))

        if not bands:
            return ""

        merged = self._merge_text_bands(sorted(bands.items(), key=lambda item: item[0]))
        header_band = self._find_grid_header_band(merged)
        if header_band is None:
            return ""
        row_band = self._find_grid_value_band(merged, header_band, target_y)
        if row_band is None:
            return ""

        headers = [text for _, text in sorted(header_band["items"], key=lambda item: item[0])]
        values = [text for _, text in sorted(row_band["items"], key=lambda item: item[0])]
        if len(headers) < 2 or len(values) < 2:
            return ""
        return "\t".join(headers) + "\n" + "\t".join(values)

    @staticmethod
    def _scrape_listview_row(window: Any, row_y: float) -> str:
        """Read text from a SysListView32 in the HTS window via cross-process WinAPI.

        LVM_GETITEMTEXT requires the LVITEM struct and text buffer to reside inside
        the target process's address space, so we use VirtualAllocEx/WriteProcessMemory/
        ReadProcessMemory. Works even when UIA/pywinauto descendants() expose no cell text.
        """
        import ctypes
        import struct as _struct

        # ---- Win32 constants ----
        LVM_GETITEMCOUNT  = 0x1004
        LVM_GETITEMTEXTW  = 0x1073  # Unicode variant of LVM_GETITEMTEXT
        LVM_GETCOLUMNW    = 0x105F  # Unicode LVM_GETCOLUMN
        LVCF_TEXT         = 0x0004
        LVIF_TEXT         = 0x0001
        MEM_COMMIT        = 0x1000
        MEM_RELEASE       = 0x8000
        PAGE_READWRITE    = 0x04
        PROCESS_ALL_ACCESS = 0x1F0FFF
        WCHAR_SIZE        = 2
        BUF_WCHARS        = 256   # max chars for one cell
        BUF_BYTES         = BUF_WCHARS * WCHAR_SIZE
        SMTO_ABORTIFHUNG  = 0x0002  # return immediately if target is hung
        MSG_TIMEOUT_MS    = 1500    # per-message timeout

        # LVITEMW layout (64-bit, default packing):
        #   mask(4) iItem(4) iSubItem(4) state(4) stateMask(4) [pad4]
        #   pszText(8) cchTextMax(4) iImage(4) lParam(8) iIndent(4) iGroupId(4)
        #   cColumns(4) [pad4] puColumns(8) piColFmt(8) iGroup(4) [pad4]
        # Total: 80 bytes on 64-bit.
        # We only care about the first 48 bytes (up to lParam) for our purpose.
        LVITEMW_FIELDS_FMT  = "<IIIIIxxxxQI"  # mask,iItem,iSubItem,state,stateMask,pad,pszText,cchTextMax
        LVITEMW_FIELDS_SIZE = _struct.calcsize(LVITEMW_FIELDS_FMT)  # 32 bytes
        # Pad up to ensure alignment of pointer field (pszText at offset 24 → naturally aligned).

        # LVCOLUMNW layout (64-bit):
        #   mask(4) fmt(4) cx(4) [pad4] pszText(8) cchTextMax(4) iSubItem(4)
        #   iImage(4) iOrder(4) cxMin(4) cxDefault(4) cxIdeal(4)
        LVCOLUMNW_FIELDS_FMT  = "<IIIxxxxQI"  # mask,fmt,cx,pad,pszText,cchTextMax
        LVCOLUMNW_FIELDS_SIZE = _struct.calcsize(LVCOLUMNW_FIELDS_FMT)

        try:
            k32 = ctypes.windll.kernel32
            u32 = ctypes.windll.user32

            # ---- Locate a SysListView32 child overlapping target_y ----
            win_rect = window.rectangle()
            target_y_abs = int(win_rect.top + win_rect.height() * row_y)
            lv_hwnd: int = 0

            def _find_lv(hwnd: int, _: object) -> None:
                nonlocal lv_hwnd
                try:
                    if win32gui.GetClassName(hwnd) == "SysListView32" and win32gui.IsWindowVisible(hwnd):
                        r = win32gui.GetWindowRect(hwnd)
                        if r[1] <= target_y_abs <= r[3]:
                            lv_hwnd = hwnd
                except Exception:
                    pass

            raw_handle = getattr(window, "handle", None)
            if raw_handle:
                try:
                    win32gui.EnumChildWindows(int(raw_handle), _find_lv, None)
                except Exception:
                    pass
            if not lv_hwnd:
                main_hwnd = KiwoomHybridBroker._find_main_hwnd()
                if main_hwnd:
                    try:
                        win32gui.EnumChildWindows(main_hwnd, _find_lv, None)
                    except Exception:
                        pass
            if not lv_hwnd:
                print("  [scrape-lv] no SysListView32 found in window", flush=True)
                return ""
            print(f"  [scrape-lv] found SysListView32 hwnd=0x{lv_hwnd:X}", flush=True)

            smt_result = ctypes.c_ssize_t(0)
            ok = u32.SendMessageTimeoutW(
                lv_hwnd, LVM_GETITEMCOUNT, 0, 0,
                SMTO_ABORTIFHUNG, MSG_TIMEOUT_MS, ctypes.byref(smt_result),
            )
            if not ok:
                return ""
            item_count = smt_result.value
            if item_count <= 0:
                return ""

            # ---- Open target process ----
            pid = ctypes.c_ulong(0)
            u32.GetWindowThreadProcessId(lv_hwnd, ctypes.byref(pid))
            hproc = k32.OpenProcess(PROCESS_ALL_ACCESS, False, pid.value)
            if not hproc:
                return ""

            try:
                # Allocate: [struct_area (max 64 bytes)] + [text buffer (BUF_BYTES)]
                STRUCT_AREA = 64
                alloc_size = STRUCT_AREA + BUF_BYTES
                remote = k32.VirtualAllocEx(hproc, None, alloc_size, MEM_COMMIT, PAGE_READWRITE)
                if not remote:
                    return ""

                remote_text = remote + STRUCT_AREA  # text buffer in remote process

                def _decode(raw_bytes: bytes) -> str:
                    text = raw_bytes.decode("utf-16-le", errors="replace")
                    return text.split("\x00")[0].strip()

                def _read_remote_text() -> str:
                    local = ctypes.create_string_buffer(BUF_BYTES)
                    done = ctypes.c_size_t(0)
                    k32.ReadProcessMemory(hproc, remote_text, local, BUF_BYTES, ctypes.byref(done))
                    return _decode(bytes(local)[:done.value])

                def _get_item_text(item_idx: int, sub_idx: int) -> str:
                    # Build LVITEMW: mask, iItem, iSubItem, state, stateMask, [pad4], pszText, cchTextMax
                    packed = _struct.pack(
                        "<IIIIIxxxxQI",
                        LVIF_TEXT,    # mask
                        item_idx,     # iItem
                        sub_idx,      # iSubItem
                        0,            # state
                        0,            # stateMask
                        remote_text,  # pszText → remote text buffer
                        BUF_WCHARS,   # cchTextMax
                    )
                    done = ctypes.c_size_t(0)
                    local_buf = ctypes.create_string_buffer(packed)
                    k32.WriteProcessMemory(hproc, remote, local_buf, len(packed), ctypes.byref(done))
                    smt_result = ctypes.c_ssize_t(0)
                    ret = u32.SendMessageTimeoutW(
                        lv_hwnd, LVM_GETITEMTEXTW, item_idx, remote,
                        SMTO_ABORTIFHUNG, MSG_TIMEOUT_MS, ctypes.byref(smt_result),
                    )
                    if not ret:
                        return ""
                    return _read_remote_text()

                def _get_column_text(col_idx: int) -> str:
                    # Build LVCOLUMNW: mask, fmt, cx, [pad4], pszText, cchTextMax
                    packed = _struct.pack(
                        "<IIIxxxxQI",
                        LVCF_TEXT,    # mask
                        0,            # fmt
                        0,            # cx
                        remote_text,  # pszText
                        BUF_WCHARS,   # cchTextMax
                    )
                    done = ctypes.c_size_t(0)
                    local_buf = ctypes.create_string_buffer(packed)
                    k32.WriteProcessMemory(hproc, remote, local_buf, len(packed), ctypes.byref(done))
                    smt_result = ctypes.c_ssize_t(0)
                    ret = u32.SendMessageTimeoutW(
                        lv_hwnd, LVM_GETCOLUMNW, col_idx, remote,
                        SMTO_ABORTIFHUNG, MSG_TIMEOUT_MS, ctypes.byref(smt_result),
                    )
                    if not ret:
                        return ""
                    return _read_remote_text()

                try:
                    # Collect column headers
                    headers: list[str] = []
                    for col in range(64):
                        h = _get_column_text(col)
                        if not h:
                            break
                        headers.append(h)

                    col_count = len(headers) if headers else 32

                    # Pick target row index based on vertical position
                    target_row = 0
                    lv_r = win32gui.GetWindowRect(lv_hwnd)
                    lv_h = lv_r[3] - lv_r[1]
                    if lv_h > 0 and item_count > 1:
                        frac = max(0.0, (target_y_abs - lv_r[1]) / lv_h)
                        target_row = min(item_count - 1, int(frac * item_count))

                    # Collect values for the target row
                    values: list[str] = []
                    empty_streak = 0
                    for sub in range(col_count):
                        val = _get_item_text(target_row, sub)
                        if val:
                            values.append(val)
                            empty_streak = 0
                        else:
                            empty_streak += 1
                            if empty_streak >= 3 and sub >= 3:
                                break

                    if len(values) < 2:
                        return ""

                    if headers and len(headers) >= len(values):
                        return "\t".join(headers[:len(values)]) + "\n" + "\t".join(values)
                    return "\t".join(values)

                finally:
                    k32.VirtualFreeEx(hproc, remote, 0, MEM_RELEASE)
            finally:
                k32.CloseHandle(hproc)

        except Exception:
            return ""

    @staticmethod
    def _merge_text_bands(raw_bands: list[tuple[int, list[tuple[int, str]]]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for y, items in raw_bands:
            if merged and abs(merged[-1]["y"] - y) <= 8:
                merged[-1]["items"].extend(items)
                merged[-1]["y"] = int((merged[-1]["y"] + y) / 2)
            else:
                merged.append({"y": y, "items": list(items)})
        return merged

    @staticmethod
    def _select_band_near_y(bands: list[dict[str, Any]], target_y: int, tolerance: int) -> dict[str, Any] | None:
        candidates = [band for band in bands if abs(band["y"] - target_y) <= tolerance]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda band: (len(band["items"]), -abs(band["y"] - target_y)),
        )

    @classmethod
    def _find_grid_header_band(cls, bands: list[dict[str, Any]]) -> dict[str, Any] | None:
        expected_header_sets = [
            {cls.HEADER_QTY, cls.HEADER_CURRENT_PRICE},
            {cls.HEADER_ORDER_ID, cls.HEADER_SYMBOL, cls.HEADER_REMAINING_QTY},
        ]
        best: dict[str, Any] | None = None
        best_score = -1
        for band in bands:
            texts = {cls._clean_text_cell(text) for _, text in band["items"]}
            score = 0
            for header_set in expected_header_sets:
                score = max(score, len(texts & header_set))
            if score > best_score:
                best = band
                best_score = score
        if best_score >= 2:
            return best
        return None

    @classmethod
    def _find_grid_value_band(
        cls,
        bands: list[dict[str, Any]],
        header_band: dict[str, Any],
        target_y: int,
    ) -> dict[str, Any] | None:
        header_y = header_band["y"]
        header_texts = {cls._clean_text_cell(text) for _, text in header_band["items"]}
        candidates: list[dict[str, Any]] = []
        for band in bands:
            if band["y"] <= header_y + 6:
                continue
            texts = {cls._clean_text_cell(text) for _, text in band["items"]}
            if texts & header_texts:
                continue
            if len(texts) < 2:
                continue
            candidates.append(band)
        if not candidates:
            return None
        near_candidates = [band for band in candidates if abs(band["y"] - target_y) <= 28]
        pool = near_candidates or candidates
        return max(
            pool,
            key=lambda band: (len(band["items"]), -abs(band["y"] - target_y), -band["y"]),
        )

    def _click_copy_menu_item(self) -> bool:
        from pywinauto import Desktop

        try:
            desktop = Desktop(backend="uia")
            menu = desktop.window(control_type="Menu", found_index=0)
            if not menu.exists(timeout=0.4):
                return False
            copy_item = menu.child_window(title_re=self.COPY_MENU_RE, control_type="MenuItem")
            if not copy_item.exists(timeout=0.4):
                return False
            copy_item.click_input()
            return True
        except Exception:
            return False

    @staticmethod
    def _win32_popup_menu_visible() -> bool:
        """Return True if a Win32 popup menu window (#32768) is currently shown."""
        found: list[bool] = [False]

        def _check(hwnd: int, _param: object) -> None:
            try:
                import win32gui as _w32
                if _w32.IsWindowVisible(hwnd) and _w32.GetClassName(hwnd) == "#32768":
                    found[0] = True
            except Exception:
                pass

        try:
            win32gui.EnumWindows(_check, None)
        except Exception:
            pass
        return found[0]

    def _click_copy_in_win32_popup_menu(self) -> bool:
        """Read the open #32768 popup menu via MN_GETHMENU, find 복사, and mouse-click it.

        Returns True if the 복사 item was found and clicked.
        Also prints all found menu item texts for diagnostic purposes.
        """
        import ctypes
        import ctypes.wintypes as wt

        MN_GETHMENU  = 0x01E1
        MF_BYPOSITION = 0x0400
        MF_SEPARATOR  = 0x0800

        user32 = ctypes.windll.user32

        # 1. Find the popup menu HWND (#32768)
        menu_hwnd: int = 0

        def _find(hwnd: int, _: object) -> None:
            nonlocal menu_hwnd
            try:
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "#32768":
                    menu_hwnd = hwnd
            except Exception:
                pass

        try:
            win32gui.EnumWindows(_find, None)
        except Exception:
            pass

        if not menu_hwnd:
            return False

        # 2. Get the HMENU associated with this popup window
        hmenu = user32.SendMessageW(menu_hwnd, MN_GETHMENU, 0, 0)
        if not hmenu:
            print("  [menu] MN_GETHMENU returned NULL", flush=True)
            return False

        count = user32.GetMenuItemCount(hmenu)
        print(f"  [menu] popup has {count} items", flush=True)

        copy_index: int = -1
        for i in range(count):
            state = user32.GetMenuState(hmenu, i, MF_BYPOSITION)
            if state & MF_SEPARATOR:
                print(f"  [menu] item {i}: --- separator ---", flush=True)
                continue
            buf = ctypes.create_unicode_buffer(256)
            user32.GetMenuStringW(hmenu, i, buf, 255, MF_BYPOSITION)
            text = buf.value
            print(f"  [menu] item {i}: {repr(text)}", flush=True)
            if copy_index < 0 and ("복사" in text or "copy" in text.lower()):
                copy_index = i

        if copy_index < 0:
            print("  [menu] 복사 item not found", flush=True)
            return False

        # 3. Get the screen rect of the target item and mouse-click its center
        item_rect = wt.RECT()
        ok = user32.GetMenuItemRect(0, hmenu, copy_index, ctypes.byref(item_rect))
        if ok:
            cx = (item_rect.left + item_rect.right) // 2
            cy = (item_rect.top + item_rect.bottom) // 2
            print(f"  [menu] clicking item {copy_index} at ({cx},{cy})", flush=True)
            try:
                from pywinauto import mouse as _mouse
                _mouse.click(button="left", coords=(cx, cy))
                return True
            except Exception as exc:
                print(f"  [menu] mouse click failed: {exc}", flush=True)

        # Fallback: post WM_COMMAND with the menu item ID
        item_id = user32.GetMenuItemID(hmenu, copy_index)
        if item_id > 0:
            owner_hwnd = win32gui.GetWindow(menu_hwnd, 4)  # GW_OWNER = 4
            if owner_hwnd:
                user32.PostMessageW(owner_hwnd, 0x0111, item_id, 0)  # WM_COMMAND
                print(f"  [menu] posted WM_COMMAND id={item_id}", flush=True)
                return True

        return False

    def _clear_clipboard(self) -> None:
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.CloseClipboard()
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _find_target_window(self, desktop: Any, window_re: str) -> Any | None:
        expected_code = self._extract_window_code(window_re)
        pattern = re.compile(window_re)

        direct_handles: list[int] = []

        def _collect_top(hwnd: int, _param: Any) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = self._safe_handle_text(hwnd)
            if not title:
                return
            if self._is_main_hwnd(hwnd):
                return
            if pattern.search(title):
                direct_handles.append(hwnd)

        win32gui.EnumWindows(_collect_top, None)
        if direct_handles:
            return desktop.window(handle=direct_handles[0])

        main_hwnd = self._find_main_hwnd()
        if main_hwnd:
            nested_handles: list[int] = []

            def _collect_child(hwnd: int, _param: Any) -> None:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = self._safe_handle_text(hwnd)
                if not title:
                    return
                if self._is_main_hwnd(hwnd):
                    return
                if pattern.search(title):
                    nested_handles.append(hwnd)
                    return
                if expected_code and f"[{expected_code}]" in title:
                    nested_handles.append(hwnd)
                    return
                if expected_code and expected_code in title and "\uACC4\uC88C\uC815\uBCF4" in title:
                    nested_handles.append(hwnd)

            win32gui.EnumChildWindows(main_hwnd, _collect_child, None)
            if nested_handles:
                return desktop.window(handle=nested_handles[0])
        return None

    @staticmethod
    def _safe_handle_text(hwnd: int) -> str:
        try:
            return win32gui.GetWindowText(hwnd)
        except Exception:
            return ""

    def _read_clipboard_text(self) -> str:
        last_error: Exception | None = None
        for _ in range(self.CLIPBOARD_RETRY_COUNT):
            try:
                win32clipboard.OpenClipboard()
                text = self._get_text_from_open_clipboard()
                win32clipboard.CloseClipboard()
                if text and text.strip():
                    return text
            except Exception as exc:  # pragma: no cover
                last_error = exc
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
            time.sleep(self.CLIPBOARD_RETRY_DELAY_SEC)
        if last_error:
            raise RuntimeError(f"Failed to read clipboard text: {last_error}") from last_error
        raise RuntimeError("Clipboard text is empty after copy.")

    def _read_clipboard_text_optional(self) -> str:
        try:
            return self._read_clipboard_text()
        except Exception:
            return ""

    @staticmethod
    def _get_text_from_open_clipboard() -> str:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
            data = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
            if isinstance(data, bytes):
                for encoding in ("cp949", "utf-8", "mbcs"):
                    try:
                        return data.decode(encoding)
                    except Exception:
                        continue
            return str(data)
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_OEMTEXT):
            data = win32clipboard.GetClipboardData(win32clipboard.CF_OEMTEXT)
            if isinstance(data, bytes):
                return data.decode("cp949", errors="replace")
            return str(data)
        available: list[str] = []
        fmt = 0
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if not fmt:
                break
            available.append(str(fmt))
        raise RuntimeError(f"No text clipboard format available. formats={','.join(available) or 'none'}")

    @classmethod
    def _parse_balance_row(cls, raw: str, symbol: str) -> dict[str, Any]:
        row = cls._parse_single_row(raw)
        joined_values = " ".join(str(value) for value in row.values()).upper()
        if symbol.upper() not in joined_values:
            raise RuntimeError(f"Clipboard row does not match symbol {symbol}: {row}")

        parsed = {
            cls.HEADER_QTY: cls._parse_int_like(row.get(cls.HEADER_QTY, "")),
            cls.HEADER_AVAILABLE_QTY: cls._parse_int_like(row.get(cls.HEADER_AVAILABLE_QTY, "")),
            cls.HEADER_AVG_PRICE: cls._parse_float_like(row.get(cls.HEADER_AVG_PRICE, "")),
            cls.HEADER_CURRENT_PRICE: cls._parse_float_like(
                row.get(cls.HEADER_CURRENT_PRICE, row.get(cls.HEADER_ALT_CURRENT_PRICE, ""))
            ),
            cls.HEADER_VALUATION: cls._parse_float_like(row.get(cls.HEADER_VALUATION, "")),
            cls.HEADER_PNL: cls._parse_float_like(row.get(cls.HEADER_PNL, "")),
        }
        if parsed[cls.HEADER_QTY] <= 0:
            raise RuntimeError(f"Failed to parse quantity from clipboard row: {row}")
        return parsed

    @classmethod
    def _parse_open_order_row(cls, raw: str, symbol: str) -> dict[str, Any]:
        row = cls._parse_single_row(raw)
        parsed_symbol = cls._normalize_symbol_text(row.get(cls.HEADER_SYMBOL, ""))
        if parsed_symbol != symbol.upper():
            raise RuntimeError(f"Clipboard open-order row does not match symbol {symbol}: {row}")

        parsed = {
            cls.HEADER_ORDER_ID: cls._clean_text_cell(row.get(cls.HEADER_ORDER_ID, "")),
            cls.HEADER_SYMBOL: parsed_symbol,
            cls.HEADER_SIDE: cls._clean_text_cell(row.get(cls.HEADER_SIDE, "")),
            cls.HEADER_STATUS: cls._clean_text_cell(row.get(cls.HEADER_STATUS, "")),
            cls.HEADER_ORDER_PRICE: cls._parse_float_like(row.get(cls.HEADER_ORDER_PRICE, "")),
            cls.HEADER_ORIGINAL_QTY: cls._parse_int_like(row.get(cls.HEADER_ORIGINAL_QTY, "")),
            cls.HEADER_REMAINING_QTY: cls._parse_int_like(row.get(cls.HEADER_REMAINING_QTY, "")),
            cls.HEADER_SUBMITTED_AT: cls._clean_text_cell(row.get(cls.HEADER_SUBMITTED_AT, "")),
        }
        if not parsed[cls.HEADER_ORDER_ID]:
            raise RuntimeError(f"Failed to parse order id from clipboard row: {row}")
        return parsed

    @classmethod
    def _parse_single_row(cls, raw: str) -> dict[str, str]:
        rows = [line for line in raw.splitlines() if line.strip()]
        header_index = cls._find_header_index(rows)
        if header_index is None or header_index + 1 >= len(rows):
            raise RuntimeError(f"Unexpected clipboard format: {raw!r}")

        headers = cls._split_clipboard_row(rows[header_index])
        values = cls._split_clipboard_row(rows[header_index + 1])
        if len(values) < len(headers):
            values.extend([""] * (len(headers) - len(values)))
        elif len(values) > len(headers):
            headers.extend([f"__extra_{idx}" for idx in range(len(values) - len(headers))])
        return {header.strip(): value.strip() for header, value in zip(headers, values) if header.strip()}

    @classmethod
    def _find_header_index(cls, rows: list[str]) -> int | None:
        expected_header_sets = [
            {cls.HEADER_QTY, cls.HEADER_CURRENT_PRICE},
            {cls.HEADER_ORDER_ID, cls.HEADER_SYMBOL, cls.HEADER_REMAINING_QTY},
        ]
        for idx, row in enumerate(rows):
            columns = set(cls._split_clipboard_row(row))
            if any(header_set.issubset(columns) for header_set in expected_header_sets):
                return idx
        return None

    @staticmethod
    def _split_clipboard_row(line: str) -> list[str]:
        if "\t" in line:
            return next(csv.reader(io.StringIO(line), delimiter="\t"))
        return [part for part in re.split(r"\s{2,}", line.strip()) if part]

    @staticmethod
    def _parse_int_like(value: str) -> int:
        normalized = KiwoomHybridBroker._normalize_number(value)
        if not normalized:
            return 0
        return int(round(float(normalized)))

    @staticmethod
    def _parse_float_like(value: str) -> float:
        normalized = KiwoomHybridBroker._normalize_number(value)
        if not normalized:
            return 0.0
        return float(normalized)

    @staticmethod
    def _normalize_number(value: str) -> str:
        text = KiwoomHybridBroker._clean_text_cell(value).replace(",", "")
        return text.replace("%", "")

    @staticmethod
    def _clean_text_cell(value: str) -> str:
        return value.strip().strip('"').strip("'").strip()

    @classmethod
    def _normalize_symbol_text(cls, value: str) -> str:
        return cls._clean_text_cell(value).upper()

    @staticmethod
    def _safe_window_text(window: Any) -> str:
        try:
            return window.window_text()
        except Exception:
            return ""

    @staticmethod
    def _safe_class_name(window: Any) -> str:
        try:
            return window.class_name()
        except Exception:
            return ""

    @staticmethod
    def _safe_rect_text(window: Any) -> str:
        try:
            rect = window.rectangle()
            return f"{rect.left},{rect.top},{rect.right},{rect.bottom}"
        except Exception:
            return ""

    @staticmethod
    def _safe_handle(window: Any) -> int:
        try:
            return int(window.handle)
        except Exception:
            return id(window)

    @staticmethod
    def _is_control_visible(window: Any) -> bool:
        try:
            if hasattr(window, "is_visible") and not window.is_visible():
                return False
        except Exception:
            pass
        try:
            rect = window.rectangle()
            return rect.width() > 0 and rect.height() > 0
        except Exception:
            return False

    @staticmethod
    def _extract_window_code(window_re: str) -> str | None:
        match = re.search(r"\\\[\s\*(\d+)\s\*\\\]", window_re)
        if match:
            return match.group(1)
        match = re.search(r"(\d{4})", window_re)
        if match:
            return match.group(1)
        return None

    @classmethod
    def _parse_side_text(cls, value: str) -> Side:
        normalized = value.strip()
        if normalized == cls.SIDE_BUY:
            return Side.BUY
        if normalized == cls.SIDE_SELL:
            return Side.SELL
        raise RuntimeError(f"Unknown side text: {value}")

    @staticmethod
    def _parse_submitted_at(value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.strptime(value, "%H:%M:%S")
        except ValueError:
            return None
        now = datetime.now()
        return now.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0)
