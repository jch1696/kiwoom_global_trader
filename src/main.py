from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .app import build_scheduler, build_settlement_writer, startup_validation
from .config import load_config
from .env_loader import load_env
from .models import OpenOrder, OrderRequest, Side
from .notifier import TelegramNotifier
from .runtime_state import StateStore
from .sheet_reader import SheetReadError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kiwoom Global Trader")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--probe-balance", action="store_true")
    parser.add_argument("--probe-decision", action="store_true")
    parser.add_argument("--probe-open-orders", action="store_true")
    parser.add_argument("--probe-account-select", choices=["balance", "open-orders"])
    parser.add_argument("--probe-cancel-controls", action="store_true")
    parser.add_argument("--probe-cancel-order")
    parser.add_argument("--probe-place-controls", action="store_true")
    parser.add_argument("--probe-place-order")
    parser.add_argument("--probe-place-order-fill")
    parser.add_argument("--probe-decision-order-fill", choices=["buy", "sell"])
    parser.add_argument("--reset-mini-order-window", action="store_true")
    parser.add_argument("--probe-main-toolbar", action="store_true")
    parser.add_argument("--mini-order-point")
    parser.add_argument("--mouse-position", action="store_true")
    parser.add_argument("--place-order")
    parser.add_argument("--place-decision-order", choices=["buy", "sell"])
    parser.add_argument("--cancel-order")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-run-fill-order", action="store_true")
    parser.add_argument("--reset-halt", action="store_true")
    parser.add_argument("--reset-strategy")
    parser.add_argument("--list-strategies", action="store_true")
    parser.add_argument("--only-sheet", action="append", default=[], metavar="SHEET")
    parser.add_argument("--simulate-balance", action="append", default=[], metavar="SYMBOL=QTY")
    parser.add_argument("--simulate-current-price", action="append", default=[], metavar="SYMBOL=PRICE")
    parser.add_argument("--simulate-open-order", action="append", default=[], metavar="SYMBOL:SIDE:PRICE:QTY[:REMAINING_QTY]")
    parser.add_argument("--settle", action="store_true")
    parser.add_argument("--trade-date-et")
    parser.add_argument("--test-telegram", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _load_runtime_env(args.config)
    original_config = load_config(args.config)
    account_dropdown_order = _strategy_account_order(original_config, args.config)
    config = original_config
    if args.only_sheet:
        config = _config_for_only_sheets(config, args.only_sheet)
    if args.test_telegram:
        result = TelegramNotifier(config.notify).send("[텔레그램 테스트]\nKiwoom Global Trader 알림 연결 성공")
        print("Telegram test sent." if result.sent else f"Telegram test failed: {result.message}")
        return
    simulate_balances = _parse_simulate_balances(args.simulate_balance)
    simulate_current_prices = _parse_simulate_current_prices(args.simulate_current_price)
    simulate_open_orders = _parse_simulate_open_orders(args.simulate_open_order, simulate_balances)
    scheduler = build_scheduler(
        config,
        args.config,
        force_dry_run=args.dry_run,
        dry_run_fill_order=args.dry_run_fill_order,
        simulate_balances=simulate_balances if args.simulate_balance else None,
        simulate_current_prices=simulate_current_prices if args.simulate_current_price else None,
        simulate_open_orders=simulate_open_orders if args.simulate_open_order else None,
    )
    broker = scheduler.order_manager.broker
    if account_dropdown_order and hasattr(broker, "set_account_dropdown_order"):
        broker.set_account_dropdown_order(account_dropdown_order)
    if args.mini_order_point and hasattr(broker, "set_manual_mini_order_point"):
        broker.set_manual_mini_order_point(args.mini_order_point)

    if args.reset_halt or args.reset_strategy:
        store = StateStore(scheduler.state_store.path)
        if args.reset_halt:
            store.reset_halt()
        if args.reset_strategy:
            store.reset_strategy(args.reset_strategy)
        has_follow_up_action = any(
            [
                args.list_strategies,
                args.probe_balance,
                args.probe_decision,
                args.probe_open_orders,
                args.probe_account_select,
                args.probe_place_order,
                args.probe_place_order_fill,
                args.probe_decision_order_fill,
                args.place_order,
                args.once,
                args.settle,
            ]
        )
        if not has_follow_up_action:
            print("Runtime state reset complete.")
            return

    if args.list_strategies:
        try:
            strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        except SheetReadError as exc:
            print(f"Cannot read strategy sheets: {exc}")
            return
        if not strategies:
            print("No valid strategy sheets found. Check required cells: E6(account), E8(symbol), E10(investment), E12(total tiers).")
            return
        for strategy in strategies:
            print(
                f"{strategy.sheet_name}: account={strategy.account_no} "
                f"symbol={strategy.symbol} tiers={len(strategy.tiers)} "
                f"buy_blocked={strategy.buy_blocked} sell_blocked={strategy.sell_blocked}"
            )
        return
    if args.probe_balance:
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        if not strategies:
            raise SystemExit("--probe-balance requires at least one strategy. Use --only-sheet if needed.")
        for strategy in strategies:
            balance = scheduler.order_manager.broker.get_balance(strategy.account_no, strategy.symbol)
            print(
                f"BALANCE {strategy.sheet_name}: "
                f"account={balance.account_no} symbol={balance.symbol} qty={balance.qty} "
                f"available={balance.available_qty} avg_price={balance.avg_price:.4f} "
                f"current_price={balance.current_price:.4f} valuation={balance.valuation:.4f} "
                f"pnl={balance.pnl:.4f}"
            )
        return
    if args.probe_decision:
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        if not strategies:
            raise SystemExit("--probe-decision requires at least one strategy. Use --only-sheet if needed.")
        for strategy in strategies:
            balance = scheduler.order_manager.broker.get_balance(strategy.account_no, strategy.symbol)
            decision = scheduler.order_manager.tier_engine.decide(strategy, balance)
            buy = decision.buy_order
            sell = decision.sell_order
            print(
                f"DECISION {strategy.sheet_name}: "
                f"account={strategy.account_no} symbol={strategy.symbol} "
                f"balance_qty={balance.qty} available_qty={balance.available_qty} "
                f"current_price={balance.current_price:.4f} avg_price={balance.avg_price:.4f} "
                f"current_tier={decision.current_tier} "
                f"buy={'-' if buy is None else f'{buy.price:.4f}x{buy.qty}@tier{buy.tier_no}'} "
                f"sell={'-' if sell is None else f'{sell.price:.4f}x{sell.qty}@tier{sell.tier_no}'}"
            )
        return
    if args.probe_open_orders:
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        if not strategies:
            raise SystemExit("--probe-open-orders requires at least one strategy. Use --only-sheet if needed.")
        for strategy in strategies:
            orders = scheduler.order_manager.broker.get_open_orders(strategy.account_no, strategy.symbol)
            if not orders:
                print(f"OPEN_ORDERS {strategy.sheet_name}: none")
                continue
            for order in orders:
                print(
                    f"OPEN_ORDER {strategy.sheet_name}: "
                    f"account={order.account_no} symbol={order.symbol} order_id={order.order_id} "
                    f"side={order.side.value} price={order.price:.4f} "
                    f"original_qty={order.original_qty} remaining_qty={order.remaining_qty} "
                    f"status={order.status} submitted_at={order.submitted_at.strftime('%H:%M:%S') if order.submitted_at else '-'}"
                )
        return
    if args.probe_account_select:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_account_select"):
            raise SystemExit("Current broker does not support --probe-account-select.")
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        if len(strategies) != 1:
            raise SystemExit("--probe-account-select requires exactly one --only-sheet.")
        strategy = strategies[0]
        result = broker.probe_account_select(strategy.account_no, args.probe_account_select)
        print(
            f"PROBE_ACCOUNT_SELECT ok={result.get('ok','false')} "
            f"sheet={strategy.sheet_name} window={args.probe_account_select} "
            f"target_account={result.get('target_account','')} "
            f"current_account={result.get('current_account','')} "
            f"message={result.get('message','')}"
        )
        return
    if args.probe_cancel_controls:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_cancel_controls"):
            raise SystemExit("Current broker does not support --probe-cancel-controls.")
        controls = broker.probe_cancel_controls()
        for control in controls:
            print(
                f"CANCEL_CONTROL index={control['index']} "
                f"class={control['class_name']} title={control['title']!r}"
            )
        return
    if args.probe_cancel_order:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_cancel_order"):
            raise SystemExit("Current broker does not support --probe-cancel-order.")
        result = broker.probe_cancel_order(args.probe_cancel_order, execute=False)
        print(
            f"PROBE_CANCEL_ORDER ok={result['ok']} order_id={result.get('order_id','')} "
            f"visible_ids={result.get('visible_ids','')} cancel_button={result.get('cancel_button','')} "
                f"executed={result.get('executed','false')} message={result.get('message','')}"
        )
        return
    if args.probe_place_controls:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_place_controls"):
            raise SystemExit("Current broker does not support --probe-place-controls.")
        controls = broker.probe_place_controls()
        for control in controls:
            print(
                f"PLACE_CONTROL index={control['index']} "
                f"class={control['class_name']} title={control['title']!r} rect={control['rect']}"
            )
        return
    if args.probe_place_order:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_place_order"):
            raise SystemExit("Current broker does not support --probe-place-order.")
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        order = _parse_probe_place_order(args.probe_place_order, strategies)
        result = broker.probe_place_order(order, execute=False)
        print(
            f"PROBE_PLACE_ORDER ok={result.get('ok','false')} "
            f"missing_fields={result.get('missing_fields','?')} "
            f"account_no={result.get('account_no','')} "
            f"normalized_account_no={result.get('normalized_account_no','')} "
            f"current_account_no={result.get('current_account_no','')} "
            f"account_matches={result.get('account_matches','false')} "
            f"account_field={result.get('account_field','')} "
            f"symbol={result.get('symbol','')} side={result.get('side','')} "
            f"price={result.get('price','')} qty={result.get('qty','')} "
            f"order_type={result.get('order_type','')} "
            f"symbol_field={result.get('symbol_field','')} "
            f"qty_field={result.get('qty_field','')} "
            f"price_field={result.get('price_field','')} "
            f"order_type_field={result.get('order_type_field','')} "
            f"buy_button={result.get('buy_button','')} "
            f"sell_button={result.get('sell_button','')} "
            f"message={result.get('message','')}"
        )
        return
    if args.probe_place_order_fill:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_place_order"):
            raise SystemExit("Current broker does not support --probe-place-order-fill.")
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        order = _parse_probe_place_order(args.probe_place_order_fill, strategies)
        result = broker.probe_place_order(order, execute=True)
        print(
            f"PROBE_PLACE_ORDER_FILL ok={result.get('ok','false')} "
            f"missing_fields={result.get('missing_fields','?')} "
            f"account_no={result.get('account_no','')} "
            f"normalized_account_no={result.get('normalized_account_no','')} "
            f"current_account_no={result.get('current_account_no','')} "
            f"account_matches={result.get('account_matches','false')} "
            f"account_field={result.get('account_field','')} "
            f"symbol={result.get('symbol','')} side={result.get('side','')} "
            f"price={result.get('price','')} qty={result.get('qty','')} "
            f"order_type={result.get('order_type','')} "
            f"symbol_field={result.get('symbol_field','')} "
            f"qty_field={result.get('qty_field','')} "
            f"price_field={result.get('price_field','')} "
            f"order_type_field={result.get('order_type_field','')} "
            f"buy_button={result.get('buy_button','')} "
            f"sell_button={result.get('sell_button','')} "
            f"executed={result.get('executed','false')} "
            f"message={result.get('message','')}"
        )
        return
    if args.probe_decision_order_fill:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_place_order"):
            raise SystemExit("Current broker does not support --probe-decision-order-fill.")
        order = _resolve_decision_order(scheduler, args.only_sheet, args.probe_decision_order_fill)
        result = broker.probe_place_order(order, execute=True)
        print(
            f"PROBE_DECISION_ORDER_FILL ok={result.get('ok','false')} "
            f"missing_fields={result.get('missing_fields','?')} "
            f"account_no={result.get('account_no','')} "
            f"normalized_account_no={result.get('normalized_account_no','')} "
            f"current_account_no={result.get('current_account_no','')} "
            f"account_matches={result.get('account_matches','false')} "
            f"symbol={result.get('symbol','')} side={result.get('side','')} "
            f"price={result.get('price','')} qty={result.get('qty','')} "
            f"order_type={result.get('order_type','')} "
            f"executed={result.get('executed','false')} "
            f"message={result.get('message','')}"
        )
        return
    if args.reset_mini_order_window:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "reset_mini_order_window"):
            raise SystemExit("Current broker does not support --reset-mini-order-window.")
        broker.reset_mini_order_window()
        print("RESET_MINI_ORDER_WINDOW ok=true")
        return
    if args.mouse_position:
        x, y = _get_mouse_position()
        print(f"MOUSE_POSITION x={x} y={y}")
        return
    if args.probe_main_toolbar:
        broker = scheduler.order_manager.broker
        if not hasattr(broker, "probe_main_toolbar"):
            raise SystemExit("Current broker does not support --probe-main-toolbar.")
        controls = broker.probe_main_toolbar()
        for control in controls:
            print(
                f"MAIN_TOOLBAR index={control['index']} "
                f"class={control['class_name']} title={control['title']!r} rect={control['rect']}"
            )
        return
    if args.place_order:
        broker = scheduler.order_manager.broker
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        order = _parse_probe_place_order(args.place_order, strategies)
        result = broker.place_order(order)
        print(
            f"PLACE_ORDER success={result.success} "
            f"order_id={result.order_id or ''} message={result.message}"
        )
        return
    if args.place_decision_order:
        strategy, order = _resolve_nearest_decision_order(scheduler, args.only_sheet, args.place_decision_order)
        result = scheduler.order_manager.sync_strategy(strategy, preferred_side=Side(args.place_decision_order))
        placed_order = result.placed_order or order
        placed_success = result.success and not result.skipped and result.placed_order is not None
        print(
            f"PLACE_DECISION_ORDER success={placed_success} "
            f"side={placed_order.side.value} tier={placed_order.tier_no or ''} "
            f"price={placed_order.price:.4f} qty={placed_order.qty} "
            f"order_id= message=actions={','.join(result.actions)} {result.message}"
        )
        return
    if args.cancel_order:
        broker = scheduler.order_manager.broker
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        account_no = strategies[0].account_no if len(strategies) == 1 else ""
        result = broker.cancel_order(account_no, args.cancel_order)
        print(f"CANCEL_ORDER success={result.success} message={result.message}")
        return
    if args.settle:
        strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), args.only_sheet)
        writer = build_settlement_writer(scheduler)
        records = writer.settle(strategies, trade_date_et=args.trade_date_et, send_telegram=True)
        for record in records:
            print(
                f"SETTLED {record.sheet_name}: trade_date={record.trade_date_et} "
                f"tier={record.final_tier}/{record.total_tiers} qty={record.final_qty} "
                f"next_buy={record.next_buy or '-'} next_sell={record.next_sell or '-'}"
            )
        return
    if args.once:
        if args.only_sheet:
            original_read = scheduler.sheet_reader.read_strategies
            scheduler.sheet_reader.read_strategies = lambda: _filter_strategies(original_read(), args.only_sheet)
        results = scheduler.run_once()
        for result in results:
            status = "OK" if result.success else "SKIP/FAIL"
            print(f"{status} {result.sheet_name}: tier={result.current_tier} actions={','.join(result.actions)} {result.message}")
    else:
        startup_validation(scheduler)
        scheduler.run_forever()


def _load_runtime_env(config_path: str) -> None:
    candidates = [
        Path.cwd() / ".env",
        _resolve_config_path(config_path).parent / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        load_env(resolved)


def _resolve_config_path(config_path: str) -> Path:
    path = Path(config_path).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _parse_simulate_balances(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--simulate-balance must be SYMBOL=QTY: {value}")
        key, qty = value.split("=", 1)
        result[key.strip().upper()] = int(qty)
    return result


def _parse_simulate_current_prices(values: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--simulate-current-price must be SYMBOL=PRICE: {value}")
        key, price = value.split("=", 1)
        result[key.strip().upper()] = float(price)
    return result


def _filter_strategies(strategies, only_sheets: list[str]):
    if not only_sheets:
        return strategies
    allowed = {sheet.upper() for sheet in only_sheets}
    return [strategy for strategy in strategies if strategy.sheet_name.upper() in allowed]


def _strategy_account_order(config, config_path: str) -> list[str]:
    configured = list(config.broker.account_dropdown_order or [])
    if configured:
        return configured
    try:
        scheduler = build_scheduler(config, config_path)
        strategies = scheduler.sheet_reader.read_strategies()
    except Exception:
        return configured
    for strategy in strategies:
        account_no = str(getattr(strategy, "account_no", "")).strip()
        if account_no and account_no not in configured:
            configured.append(account_no)
    return configured


def _config_for_only_sheets(config, only_sheets: list[str]):
    if not config.google.public_csv_tabs:
        return config
    allowed = {sheet.upper() for sheet in only_sheets}
    tabs = {
        sheet_name: url
        for sheet_name, url in config.google.public_csv_tabs.items()
        if sheet_name.upper() in allowed
    }
    return replace(config, google=replace(config.google, public_csv_tabs=tabs))


def _resolve_decision_order(scheduler, only_sheets: list[str], side_name: str) -> OrderRequest:
    strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), only_sheets)
    if len(strategies) != 1:
        raise SystemExit("Decision-based order helpers require exactly one strategy. Use --only-sheet.")
    strategy = strategies[0]
    balance = scheduler.order_manager.broker.get_balance(strategy.account_no, strategy.symbol)
    decision = scheduler.order_manager.tier_engine.decide(strategy, balance)
    order = decision.buy_order if side_name == "buy" else decision.sell_order
    if order is None:
        raise SystemExit(f"No {side_name} order is available for {strategy.sheet_name} at current tier {decision.current_tier}.")
    return order


def _resolve_nearest_decision_order(scheduler, only_sheets: list[str], side_name: str):
    strategies = _filter_strategies(scheduler.sheet_reader.read_strategies(), only_sheets)
    if len(strategies) != 1:
        raise SystemExit("Decision-based order helpers require exactly one strategy. Use --only-sheet.")
    strategy = strategies[0]
    balance = scheduler.order_manager.broker.get_balance(strategy.account_no, strategy.symbol)
    decision = scheduler.order_manager.tier_engine.decide(strategy, balance)
    plan = scheduler.order_manager.target_order_plan(strategy, balance, decision.current_tier, decision.buy_order, decision.sell_order)
    if plan.block_reason:
        raise SystemExit(plan.block_reason)
    order = plan.order
    if order is None:
        raise SystemExit(f"No decision order is available for {strategy.sheet_name} at current tier {decision.current_tier}.")
    if order.side.value != side_name:
        raise SystemExit(
            f"Current decision side is {order.side.value}, not {side_name}. "
            "Run dry-run again and retry the live order."
        )
    return strategy, order


def _parse_simulate_open_orders(values: list[str], balances: dict[str, int]) -> list[OpenOrder]:
    result: list[OpenOrder] = []
    for idx, value in enumerate(values, start=1):
        parts = value.split(":")
        if len(parts) not in {4, 5}:
            raise SystemExit(f"--simulate-open-order must be SYMBOL:SIDE:PRICE:QTY[:REMAINING_QTY]: {value}")
        symbol, side_raw, price_raw, qty_raw = [part.strip() for part in parts[:4]]
        remaining_raw = parts[4].strip() if len(parts) == 5 else qty_raw
        side = _parse_side(side_raw)
        account_no = _account_for_symbol(symbol.upper(), balances)
        original_qty = int(qty_raw)
        remaining_qty = int(remaining_raw)
        if remaining_qty < 0 or original_qty <= 0 or remaining_qty > original_qty:
            raise SystemExit(f"invalid quantities for --simulate-open-order: {value}")
        result.append(
            OpenOrder(
                order_id=f"SIMOPEN{idx:04d}",
                account_no=account_no,
                symbol=symbol.upper(),
                side=side,
                price=float(price_raw),
                original_qty=original_qty,
                remaining_qty=remaining_qty,
                status="open",
                submitted_at=None,
                fetched_at=datetime.now(),
            )
        )
    return result


def _parse_side(value: str) -> Side:
    normalized = value.strip().lower()
    if normalized in {"buy", "b", "매수"}:
        return Side.BUY
    if normalized in {"sell", "s", "매도"}:
        return Side.SELL
    raise SystemExit(f"unknown side: {value}")


def _account_for_symbol(symbol: str, balances: dict[str, int]) -> str:
    for key in balances:
        if ":" in key:
            account_no, key_symbol = key.split(":", 1)
            if key_symbol.upper() == symbol.upper():
                return account_no
    # Matches the current public sheet test accounts; HTS adapter will provide
    # real account routing later.
    defaults = {"LABU": "12345678", "SOXL": "87654321"}
    return defaults.get(symbol.upper(), "SIMACCOUNT")


def _parse_probe_place_order(value: str, strategies=None):
    parts = [part.strip() for part in value.split(":")]
    if len(parts) not in {4, 5}:
        raise SystemExit("--probe-place-order must be SYMBOL:SIDE:PRICE:QTY[:ORDER_TYPE]")
    symbol, side_raw, price_raw, qty_raw = parts[:4]
    order_type = parts[4] if len(parts) == 5 else "지정가"
    account_no = _account_for_symbol_from_strategies(symbol.upper(), strategies) or _account_for_symbol(symbol.upper(), {})
    return OrderRequest(
        account_no=account_no,
        symbol=symbol.upper(),
        side=_parse_side(side_raw),
        price=float(price_raw),
        qty=int(qty_raw),
        order_type=order_type,
        sheet_name="probe",
    )


def _account_for_symbol_from_strategies(symbol: str, strategies) -> str | None:
    if not strategies:
        return None
    matches = [strategy for strategy in strategies if strategy.symbol.upper() == symbol.upper()]
    if len(matches) == 1:
        return matches[0].account_no
    return None


def _get_mouse_position() -> tuple[int, int]:
    import win32api

    return win32api.GetCursorPos()


if __name__ == "__main__":
    main()
