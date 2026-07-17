"""Trade execution notification formatting and delivery entry point."""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable, Mapping

from . import dispatcher as _dispatcher
from .models import Clock, DeliveryResult, JsonTransport, Notification
from .transport import _sanitized_error


def _clean_trade_text(value: Any, max_chars: int = 120) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= max_chars else text[: max(1, max_chars - 1)].rstrip() + "…"


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _money(value: Any) -> str:
    number = _finite_float(value)
    return f"¥{number:,.2f}" if number is not None else "-"


def _price(value: Any) -> str:
    number = _finite_float(value)
    return f"¥{number:,.3f}" if number is not None else "-"


def _percentage(value: Any) -> str:
    number = _finite_float(value)
    return f"{number:.2f}%" if number is not None else "-"


def _trade_notification(trades: Iterable[Mapping[str, Any]]) -> Notification | None:
    normalized: list[Mapping[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, Mapping):
            continue
        if str(trade.get("action") or "").strip().upper() in {"BUY", "SELL"}:
            normalized.append(trade)
    if not normalized:
        return None

    lines = ["模拟成交，非实盘"]
    actions: list[str] = []
    for index, trade in enumerate(normalized, 1):
        action = str(trade.get("action") or "").strip().upper()
        actions.append(action)
        label = "买入" if action == "BUY" else "卖出"
        name = _clean_trade_text(trade.get("name"), 32) or "未知股票"
        code = _clean_trade_text(trade.get("code"), 24) or "-"
        try:
            shares = int(float(trade.get("shares") or 0))
        except (TypeError, ValueError, OverflowError):
            shares = 0
        details = [
            f"{index}. {label} {name}({code})",
            f"{shares}股 @ {_price(trade.get('price'))}",
            f"金额 {_money(trade.get('amount'))}",
        ]
        fee = _finite_float(trade.get("fee"))
        if fee is not None:
            details.append(f"费用 {_money(fee)}")
        if action == "BUY":
            position_pct = trade.get("position_after_trade_pct")
            if _finite_float(position_pct) is not None:
                details.append(f"成交后单票仓位 {_percentage(position_pct)}")
        else:
            pnl = _finite_float(trade.get("pnl"))
            pnl_pct = _finite_float(trade.get("pnl_pct"))
            if pnl is not None:
                pnl_text = f"盈亏 {_money(pnl)}"
                if pnl_pct is not None:
                    pnl_text += f" / {_percentage(pnl_pct)}"
                details.append(pnl_text)
        trade_time = _clean_trade_text(trade.get("time"), 32)
        if trade_time:
            details.append(f"时间 {trade_time}")
        lines.append("｜".join(details))

        strategy = _clean_trade_text(
            trade.get("exit_rule") if action == "SELL" else trade.get("buy_strategy"),
            60,
        )
        reason = _clean_trade_text(trade.get("reason"), 100)
        annotations = []
        if strategy:
            annotations.append(f"策略 {strategy}")
        if reason:
            annotations.append(f"原因 {reason}")
        if annotations:
            lines.append("   " + "；".join(annotations))

    count = len(normalized)
    return Notification(
        event_type="trade.executed",
        title=f"Jeff小助理模拟成交（{count}笔）",
        text="\n".join(lines),
        metadata={"trade_count": count, "actions": tuple(actions)},
    )


TradeDispatcher = Callable[..., list[DeliveryResult]]
_DISPATCH_UNSET = object()


def notify_trade_executions(
    trades: Iterable[Mapping[str, Any]],
    env: Mapping[str, Any] | None = None,
    *,
    transport: JsonTransport | None = None,
    clock: Clock | None = None,
    _dispatch: TradeDispatcher | object = _DISPATCH_UNSET,
) -> list[DeliveryResult]:
    """Format a persisted BUY/SELL batch and dispatch it to configured channels."""

    try:
        notification = _trade_notification(trades)
    except Exception as exc:
        return [DeliveryResult("notification", False, _sanitized_error(exc))]
    if notification is None:
        return []
    try:
        selected_dispatch = _dispatcher.dispatch if _dispatch is _DISPATCH_UNSET else _dispatch
        return selected_dispatch(notification, env, transport=transport, clock=clock)
    except Exception as exc:  # final safety boundary for callers in trading code
        return [DeliveryResult("notification", False, _sanitized_error(exc))]


__all__ = ["notify_trade_executions"]
