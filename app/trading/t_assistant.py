"""Standalone implementation of the paper-trading T assistant.

The host trader module supplies market data, persistence, model transport, and
notification adapters through :func:`bind`. Keeping those integrations behind
this small boundary lets the T-assistant rules evolve without repeatedly
editing the main trading engine.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time as dtime
from typing import Any


_HOST: Any | None = None
_HOST_BINDINGS = (
    "MODEL",
    "PROVIDER_DISPLAY_NAME",
    "T_ASSISTANT_ENABLED",
    "T_ASSISTANT_MODEL_ENABLED",
    "T_ASSISTANT_MODEL_MAX_TOKENS",
    "T_ASSISTANT_MODEL_TIMEOUT_SECONDS",
    "T_ASSISTANT_MODEL_MIN_CONFIDENCE",
    "T_ASSISTANT_NOTIFY_COOLDOWN_SECONDS",
    "T_ASSISTANT_MIN_CHANGE_BUCKET_PCT",
    "_dispatch_notification_safely",
    "_safe_float",
    "_sync_decision_to_db",
    "available_to_sell",
    "check_market_environment",
    "check_market_sentiment",
    "compact_market_strategy_context",
    "compact_portfolio_for_decision",
    "current_market_strategy_context",
    "enrich_portfolio",
    "extract_json",
    "is_a_share_trading_day",
    "load_decision_model_config",
    "load_state",
    "normalize_code",
    "now_ts",
    "parse_ts",
    "portfolio_total_equity_for_limits",
    "position_qty",
    "refresh_position_intraday",
    "refresh_realtime_prices",
    "request_chat_content",
    "safe_decision_intelligence_context",
    "save_state",
)


def bind(host: Any) -> None:
    """Bind the currently loaded trader facade.

    The facade is resolved dynamically so existing tests and callers that
    monkeypatch trader helpers continue to work after this extraction.
    """
    global _HOST
    _HOST = host
    namespace = globals()
    for name in _HOST_BINDINGS:
        namespace[name] = getattr(host, name)


def _host_call(name: str, *args: Any, **kwargs: Any) -> Any:
    if _HOST is None:
        raise RuntimeError("T assistant host is not bound")
    return getattr(_HOST, name)(*args, **kwargs)


POSITION_MANAGEMENT_DEFAULT = "default_strategy"
POSITION_MANAGEMENT_T_ASSISTANT = "t_assistant"

def normalize_position_management_mode(value: Any, *, default: str = POSITION_MANAGEMENT_DEFAULT) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"t", "t_assistant", "t-assistant", "t助手", "仅t助手", "only_t_assistant"}:
        return POSITION_MANAGEMENT_T_ASSISTANT
    if raw in {"default", "default_strategy", "default-strategy", "默认", "默认策略", "strategy"}:
        return POSITION_MANAGEMENT_DEFAULT
    return POSITION_MANAGEMENT_T_ASSISTANT if str(default) == POSITION_MANAGEMENT_T_ASSISTANT else POSITION_MANAGEMENT_DEFAULT

def position_management_mode(pos: dict[str, Any] | None) -> str:
    position = pos if isinstance(pos, dict) else {}
    explicit = str(position.get("management_mode") or "").strip()
    if explicit:
        return normalize_position_management_mode(explicit)
    # Legacy manually imported positions had only import_source/manual_import markers.
    if (
        position.get("import_source") == "manual_holding_import"
        or str(position.get("buy_strategy") or "").strip().lower() == "manual_import"
    ):
        return POSITION_MANAGEMENT_T_ASSISTANT
    return POSITION_MANAGEMENT_DEFAULT

def is_t_assistant_managed(pos: dict[str, Any] | None) -> bool:
    return position_management_mode(pos) == POSITION_MANAGEMENT_T_ASSISTANT

def is_t_assistant_window(dt: datetime | None = None) -> tuple[bool, str]:
    """做T助手只在连续竞价和尾盘前观察；14:50后不提示新做T。"""
    dt = dt or datetime.now()
    if not is_a_share_trading_day(dt):
        return False, "非A股交易日"
    t = dt.time()
    if dtime(9, 30) <= t <= dtime(11, 30):
        return True, "上午连续竞价"
    if dtime(13, 0) <= t <= dtime(14, 50):
        return True, "下午连续竞价"
    if dtime(14, 50) < t <= dtime(15, 0):
        return False, "14:50后不建议新做T，只处理既有计划和风险"
    if dtime(9, 15) <= t < dtime(9, 30):
        return False, "集合竞价/静默期只观察，不做T判断"
    if dtime(11, 30) < t < dtime(13, 0):
        return False, "午间休市，等待下午连续竞价"
    return False, "非A股连续竞价时段"


def _pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return (numerator / denominator - 1.0) * 100.0


def _round_lot(value: int | float) -> int:
    try:
        return max(0, int(float(value)) // 100 * 100)
    except (TypeError, ValueError, OverflowError):
        return 0


def _price_text(value: Any) -> str:
    number = _safe_float(value, 0.0)
    return f"{number:.2f}" if number > 0 else "-"


def _t_assistant_position_item(pos: dict[str, Any], code: str, *, cash: float, total_equity: float, now: datetime) -> dict[str, Any]:
    qty = position_qty(pos)
    available = available_to_sell(pos, now.strftime("%Y-%m-%d"))
    price = _safe_float(pos.get("last_price") or pos.get("price") or pos.get("avg_cost"), 0.0)
    avg_cost = _safe_float(pos.get("avg_cost"), 0.0)
    prev_close = _safe_float(pos.get("prev_close"), 0.0)
    day_high = _safe_float(pos.get("day_high") if pos.get("day_high") is not None else pos.get("high"), 0.0)
    day_low = _safe_float(pos.get("day_low") if pos.get("day_low") is not None else pos.get("low"), 0.0)
    change_pct = pos.get("change_pct")
    change_pct_float = _safe_float(change_pct, 0.0) if change_pct is not None else (_pct(price, prev_close) if prev_close > 0 else None)
    high_pct = _pct(day_high, prev_close) if day_high > 0 and prev_close > 0 else None
    low_pct = _pct(day_low, prev_close) if day_low > 0 and prev_close > 0 else None
    near_high_gap_pct = (day_high - price) / day_high * 100.0 if day_high > 0 and price > 0 else None
    rebound_from_low_pct = (price / day_low - 1.0) * 100.0 if day_low > 0 and price > 0 else None
    amplitude_pct = (day_high / day_low - 1.0) * 100.0 if day_high > 0 and day_low > 0 else None
    pnl_pct = (price / avg_cost - 1.0) * 100.0 if price > 0 and avg_cost > 0 else None
    cash_buyable = _round_lot(cash / price) if price > 0 else 0
    base_t_shares = _round_lot(min(available, max(100, qty // 3))) if available >= 100 else 0
    buy_first_shares = _round_lot(min(available, cash_buyable, max(100, qty // 3))) if available >= 100 and cash_buyable >= 100 else 0

    blockers: list[str] = []
    if qty < 100:
        blockers.append("持仓不足100股")
    if available < 100:
        blockers.append("无可卖底仓，T+1锁仓或数量不足")
    if price <= 0:
        blockers.append("缺少有效现价")
    if prev_close <= 0:
        blockers.append("缺少昨收基准")

    item = {
        "code": code,
        "name": pos.get("name") or "",
        "management_mode": position_management_mode(pos),
        "management_mode_label": "仅T助手" if is_t_assistant_managed(pos) else "默认策略",
        "managed_by_t_assistant": is_t_assistant_managed(pos),
        "qty": qty,
        "available_qty": available,
        "last_price": round(price, 3) if price > 0 else None,
        'quote_time': str(pos.get('quote_time') or ''),
        'quote_source': str(pos.get('quote_source') or ''),
        "avg_cost": round(avg_cost, 3) if avg_cost > 0 else None,
        "prev_close": round(prev_close, 3) if prev_close > 0 else None,
        "change_pct": round(change_pct_float, 2) if change_pct_float is not None else None,
        "day_high": round(day_high, 3) if day_high > 0 else None,
        "day_low": round(day_low, 3) if day_low > 0 else None,
        "day_high_pct": round(high_pct, 2) if high_pct is not None else None,
        "day_low_pct": round(low_pct, 2) if low_pct is not None else None,
        "amplitude_pct": round(amplitude_pct, 2) if amplitude_pct is not None else None,
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "suggested_shares": 0,
        "can_t": False,
        "mode": "avoid",
        "mode_label": "暂不做T",
        "priority": 0,
        "blockers": blockers,
        "trigger": "",
        "plan": "不满足做T基础条件，等待更清晰的日内高低点和可卖底仓。",
        "invalid_if": "跌破日内低点或大盘/板块继续走弱。",
    }
    if blockers:
        return item

    change = float(change_pct_float or 0.0)
    near_high = near_high_gap_pct is not None and near_high_gap_pct <= 0.8
    near_low = rebound_from_low_pct is not None and rebound_from_low_pct <= 0.8
    enough_amplitude = amplitude_pct is None or amplitude_pct >= 1.6
    deep_weak = change <= -3.0 and (near_low or (low_pct is not None and low_pct <= -3.5))

    if deep_weak and base_t_shares >= 100:
        sell_ref = price
        buyback_high = sell_ref * 0.985
        buyback_low = sell_ref * 0.965
        item.update({
            "can_t": True,
            "mode": "sell_weak_watch_buyback",
            "mode_label": "弱势倒T观察",
            "priority": 3,
            "suggested_shares": base_t_shares,
            "trigger": f"弱势贴近日低且跌幅{change:+.2f}%时，可先卖{base_t_shares}股可卖底仓；只有跌到{buyback_low:.2f}-{buyback_high:.2f}或明显止跌承接后再看接回。",
            "plan": "倒T以先降风险为主：先卖旧仓锁出现金，等1.5%-3.5%价差或分时止跌再接；没有价差就不接回。",
            "invalid_if": "卖出后快速收回分时均线/昨收附近不追接；若继续放量下跌，接回动作取消，按原卖出风控处理。",
        })
    elif change >= 1.8 and near_high and enough_amplitude and base_t_shares >= 100:
        sell_ref = price
        buyback_high = sell_ref * 0.988
        buyback_low = sell_ref * 0.975
        item.update({
            "can_t": True,
            "mode": "sell_high_watch_buyback",
            "mode_label": "可观察冲高先卖",
            "priority": 4 if change >= 3.0 else 3,
            "suggested_shares": base_t_shares,
            "trigger": f"接近日高且涨幅{change:+.2f}%时，可考虑卖出{base_t_shares}股；回落至{buyback_low:.2f}-{buyback_high:.2f}再看接回。",
            "plan": "先卖可卖底仓，等待1.2%-2.5%回落或分时承接再接回；不回落就不追接。",
            "invalid_if": "放量突破后继续走强不急接回，或跌破均价线/昨收后取消接回。",
        })
    elif change <= -1.3 and near_low and buy_first_shares >= 100 and enough_amplitude:
        sell_zone = price * 1.012
        item.update({
            "can_t": True,
            "mode": "buy_low_watch_sellback",
            "mode_label": "可观察低吸后卖旧仓",
            "priority": 3,
            "suggested_shares": buy_first_shares,
            "trigger": f"贴近日低且跌幅{change:+.2f}%时，只在出现承接后低吸{buy_first_shares}股；反抽到{sell_zone:.2f}附近卖同等旧仓。",
            "plan": "用现金低吸，随后卖出同等可卖旧仓完成日内T；若只买不卖会增加隔夜风险。",
            "invalid_if": "买入后继续破日低或板块走弱，停止追加，优先保留现金。",
        })
    elif available >= 100:
        watch_sell = prev_close * 1.018 if prev_close > 0 else price * 1.018
        watch_buy = prev_close * 0.985 if prev_close > 0 else price * 0.985
        item.update({
            "mode": "watch",
            "mode_label": "只观察",
            "priority": 2 if abs(change) >= 1.0 else 1,
            "suggested_shares": min(base_t_shares, buy_first_shares) if base_t_shares and buy_first_shares else base_t_shares,
            "trigger": f"上冲{watch_sell:.2f}附近观察先卖；回踩{watch_buy:.2f}附近且有承接再考虑低吸。",
            "plan": "当前位置没有明显价差，先等分时拉开1.5%以上空间；不为了做T而做T。",
            "invalid_if": "振幅不足、量能萎缩或跌破日低。",
        })
    return item


def evaluate_t_opportunities(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    enabled = bool(T_ASSISTANT_ENABLED)
    in_window, window_reason = is_t_assistant_window(now)
    positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
    cash = _safe_float(state.get("cash"), 0.0)
    total_equity = portfolio_total_equity_for_limits(cash, positions or {})
    items = [
        _t_assistant_position_item(pos, normalize_code(code), cash=cash, total_equity=total_equity, now=now)
        for code, pos in (positions or {}).items()
        if isinstance(pos, dict) and position_qty(pos) > 0
    ]
    items.sort(
        key=lambda row: (
            bool(row.get("managed_by_t_assistant")),
            int(row.get("priority") or 0),
            bool(row.get("can_t")),
        ),
        reverse=True,
    )
    actionable = [item for item in items if item.get("can_t")]
    t_managed_count = sum(1 for item in items if item.get("managed_by_t_assistant"))
    summary = "暂无持仓" if not items else (
        f"{len(actionable)}只可做T观察，{len(items) - len(actionable)}只继续等待"
        if actionable else "当前没有明确做T窗口，继续观察持仓分时"
    )
    if not enabled:
        summary = "持仓T助手已关闭"
    elif not in_window:
        summary = f"{window_reason}；{summary}"
    return {
        "enabled": enabled,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "in_window": in_window,
        "window_reason": window_reason,
        "summary": summary,
        "cash": round(cash, 2),
        "total_equity": round(total_equity, 2),
        "items": items,
        "actionable_count": len(actionable),
        "t_managed_count": t_managed_count,
        "note": "仅辅助判断，非自动成交；A股做T必须依赖已有可卖底仓，买回的新股当日不可再卖。",
    }


def _t_model_text(value: Any, limit: int = 360) -> str:
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return text[:limit]


def normalize_t_model_analysis(raw: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    local_items = {
        normalize_code(item.get('code') or ''): item
        for item in (assessment.get('items') or [])
        if isinstance(item, dict) and normalize_code(item.get('code') or '')
    }
    normalized_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in (raw.get('items') or []):
        if not isinstance(row, dict):
            continue
        code = normalize_code(row.get('code') or '')
        local = local_items.get(code)
        if not local or code in seen:
            continue
        seen.add(code)
        verdict = str(row.get('verdict') or 'watch').strip().lower()
        if verdict not in {'act', 'watch', 'avoid'}:
            verdict = 'watch'
        action = str(row.get('action') or 'wait').strip().lower()
        if action not in {'sell_first', 'buy_first', 'wait'}:
            action = 'wait'
        try:
            confidence = max(0.0, min(1.0, float(row.get('confidence') or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0

        available = _round_lot(local.get('available_qty') or 0)
        local_cap = _round_lot(local.get('suggested_shares') or 0)
        requested = _round_lot(row.get('suggested_shares') or 0)
        cap = min(available, local_cap or available)
        if action == 'buy_first':
            price = _safe_float(local.get('last_price'), 0.0)
            cash = _safe_float(assessment.get('cash'), 0.0)
            affordable = _round_lot(cash / price) if price > 0 else 0
            cap = min(cap, affordable)
        suggested = min(requested or cap, cap)
        blockers = [str(value) for value in (local.get('blockers') or []) if str(value).strip()]
        rejected_reason = ''
        if verdict == 'act' and confidence < T_ASSISTANT_MODEL_MIN_CONFIDENCE:
            rejected_reason = f'模型置信度{confidence:.2f}低于阈值{T_ASSISTANT_MODEL_MIN_CONFIDENCE:.2f}'
        elif verdict == 'act' and blockers:
            rejected_reason = '；'.join(blockers)
        elif verdict == 'act' and (available < 100 or suggested < 100):
            rejected_reason = '可卖底仓或建议数量不足100股'
        elif verdict == 'act' and action == 'wait':
            rejected_reason = '模型没有给出可执行的做T方向'
        if rejected_reason:
            verdict = 'avoid' if blockers else 'watch'
            action = 'wait'
            suggested = 0

        normalized_items.append({
            'code': code,
            'verdict': verdict,
            'action': action,
            'confidence': round(confidence, 2),
            'suggested_shares': suggested if verdict == 'act' else 0,
            'analysis': _t_model_text(row.get('analysis') or row.get('reason')),
            'trigger': _t_model_text(row.get('trigger')),
            'plan': _t_model_text(row.get('plan')),
            'invalid_if': _t_model_text(row.get('invalid_if')),
            'rejected_reason': rejected_reason,
        })

    actionable = [row for row in normalized_items if row.get('verdict') == 'act']
    urgency = str(raw.get('urgency') or 'normal').strip().lower()
    if urgency not in {'normal', 'high'}:
        urgency = 'normal'
    return {
        'enabled': True,
        'status': 'ok',
        'model': MODEL,
        'provider': PROVIDER_DISPLAY_NAME,
        'checked_at': assessment.get('generated_at') or now_ts(),
        'should_notify': raw.get('should_notify') is True and bool(actionable),
        'urgency': urgency if actionable else 'normal',
        'summary': _t_model_text(raw.get('summary'), 500),
        'market_view': _t_model_text(raw.get('market_view'), 500),
        'items': normalized_items,
        'actionable_count': len(actionable),
    }


def apply_t_model_analysis(assessment: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    assessment['rule_summary'] = assessment.get('summary') or ''
    assessment['model_analysis'] = analysis
    model_by_code = {
        normalize_code(row.get('code') or ''): row
        for row in (analysis.get('items') or [])
        if isinstance(row, dict)
    }
    actionable_count = 0
    for item in (assessment.get('items') or []):
        if not isinstance(item, dict):
            continue
        item['rule_can_t'] = bool(item.get('can_t'))
        model_row = model_by_code.get(normalize_code(item.get('code') or '')) or {}
        item['model_verdict'] = model_row.get('verdict') or 'watch'
        item['model_action'] = model_row.get('action') or 'wait'
        item['model_confidence'] = model_row.get('confidence') or 0.0
        item['model_analysis'] = model_row.get('analysis') or ''
        item['model_trigger'] = model_row.get('trigger') or ''
        item['model_plan'] = model_row.get('plan') or ''
        item['model_invalid_if'] = model_row.get('invalid_if') or ''
        item['model_rejected_reason'] = model_row.get('rejected_reason') or ''
        item['can_t'] = (
            analysis.get('status') == 'ok'
            and bool(analysis.get('should_notify'))
            and item['model_verdict'] == 'act'
        )
        if item['can_t']:
            actionable_count += 1
            item['suggested_shares'] = int(model_row.get('suggested_shares') or item.get('suggested_shares') or 0)
            if item['model_action'] == 'sell_first':
                item['mode_label'] = '模型确认：先卖后接'
            elif item['model_action'] == 'buy_first':
                item['mode_label'] = '模型确认：先买后卖旧仓'
    assessment['actionable_count'] = actionable_count
    if analysis.get('status') == 'ok':
        assessment['summary'] = analysis.get('summary') or (
            f'模型确认{actionable_count}只已到做T观察时机'
            if actionable_count else '模型复核完成，当前没有需要操作的做T时机'
        )
    elif analysis.get('status') == 'error':
        assessment['summary'] = 'T助手模型复核失败，本轮保持静默，不使用规则模板代替模型判断'
    return assessment


def _t_assistant_signature(assessment: dict[str, Any]) -> str:
    model_analysis = assessment.get('model_analysis') if isinstance(assessment.get('model_analysis'), dict) else {}
    if model_analysis.get('status') == 'ok':
        signals = sorted(
            (
                str(row.get('code') or ''),
                str(row.get('action') or ''),
                str(row.get('verdict') or ''),
            )
            for row in (model_analysis.get('items') or [])
            if isinstance(row, dict) and row.get('verdict') == 'act'
        )
        return json.dumps({'model_signals': signals}, ensure_ascii=False, sort_keys=True)
    buckets = []
    bucket_size = T_ASSISTANT_MIN_CHANGE_BUCKET_PCT
    for item in (assessment.get("items") or [])[:6]:
        change = item.get("change_pct")
        try:
            change_bucket = round(float(change or 0.0) / bucket_size) * bucket_size
        except Exception:
            change_bucket = 0.0
        buckets.append({
            "code": item.get("code"),
            "mode": item.get("mode"),
            "shares": item.get("suggested_shares"),
            "change_bucket": round(change_bucket, 2),
            "can_t": bool(item.get("can_t")),
        })
    return json.dumps(
        {
            "in_window": bool(assessment.get("in_window")),
            "summary": assessment.get("summary"),
            "items": buckets,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def format_t_assistant_notification(assessment: dict[str, Any]) -> str:
    model_analysis = assessment.get('model_analysis') if isinstance(assessment.get('model_analysis'), dict) else {}
    if model_analysis.get('status') == 'ok':
        lines = [
            '大模型复核：' + str(model_analysis.get('summary') or assessment.get('summary') or '做T时机已确认'),
            '模型：' + str(model_analysis.get('model') or MODEL) + '（' + str(model_analysis.get('provider') or PROVIDER_DISPLAY_NAME) + '）',
        ]
        if model_analysis.get('market_view'):
            lines.append('最新盘面：' + str(model_analysis.get('market_view')))
        actionable_codes = {
            str(row.get('code') or '')
            for row in (model_analysis.get('items') or [])
            if isinstance(row, dict) and row.get('verdict') == 'act'
        }
        selected = [
            item for item in (assessment.get('items') or [])
            if isinstance(item, dict) and str(item.get('code') or '') in actionable_codes
        ]
        for item in selected[:5]:
            name = str(item.get('name') or '').strip() or '持仓'
            code = str(item.get('code') or '-')
            change = item.get('change_pct')
            change_text = f'{float(change):+.2f}%' if isinstance(change, (int, float)) else '-'
            model_confidence_value = float(item.get('model_confidence') or 0)
            lines.append(
                name + '(' + code + ')：' + str(item.get('mode_label') or '模型确认机会') + '，'
                + '现价' + _price_text(item.get('last_price')) + '，今日' + change_text + '，'
                + '建议' + str(int(item.get('suggested_shares') or 0)) + '股，'
                + '置信度' + f'{model_confidence_value:.0%}'
            )
            if item.get('model_analysis'):
                lines.append('  判断：' + str(item.get('model_analysis')))
            if item.get('model_trigger'):
                lines.append('  时机：' + str(item.get('model_trigger')))
            if item.get('model_plan'):
                lines.append('  计划：' + str(item.get('model_plan')))
            if item.get('model_invalid_if'):
                lines.append('  失效：' + str(item.get('model_invalid_if')))
        lines.append('仅为模型辅助判断，不自动成交；请结合盘口确认。')
        return '\n'.join(lines)
    lines = [
        str(assessment.get("summary") or "持仓T助手"),
        str(assessment.get("note") or "仅辅助判断，非自动成交。"),
    ]
    for item in (assessment.get("items") or [])[:5]:
        name = str(item.get("name") or "").strip() or "持仓"
        code = str(item.get("code") or "-")
        change = item.get("change_pct")
        change_text = f"{float(change):+.2f}%" if isinstance(change, (int, float)) else "-"
        line = (
            f"{name}({code})：{item.get('mode_label') or '-'}，"
            f"现价{_price_text(item.get('last_price'))}，今日{change_text}，"
            f"可卖{int(item.get('available_qty') or 0)}股"
        )
        if int(item.get("suggested_shares") or 0) > 0:
            line += f"，建议观察{int(item.get('suggested_shares') or 0)}股"
        lines.append(line)
        trigger = str(item.get("trigger") or "").strip()
        plan = str(item.get("plan") or "").strip()
        invalid_if = str(item.get("invalid_if") or "").strip()
        if trigger:
            lines.append(f"  触发：{trigger}")
        if plan:
            lines.append(f"  计划：{plan}")
        if invalid_if:
            lines.append(f"  失效：{invalid_if}")
    return "\n".join(lines)


def notify_t_assistant_if_needed(
    state: dict[str, Any],
    assessment: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    model_analysis = assessment.get('model_analysis') if isinstance(assessment.get('model_analysis'), dict) else {}
    if T_ASSISTANT_MODEL_ENABLED and not force:
        if model_analysis.get('status') != 'ok':
            return {'notified': False, 'reason': 'model_unavailable'}
        if not model_analysis.get('should_notify'):
            return {'notified': False, 'reason': 'model_no_action'}
    if not assessment.get("enabled") or not assessment.get("items"):
        return {"notified": False, "reason": "disabled_or_empty"}
    if not assessment.get("in_window") and not force:
        return {"notified": False, "reason": "outside_t_window"}
    signature = _t_assistant_signature(assessment)
    last = state.get("t_assistant_notify") if isinstance(state.get("t_assistant_notify"), dict) else {}
    last_dt = parse_ts(str(last.get("last_at") or ""))
    now_dt = parse_ts(str(assessment.get("generated_at") or "")) or datetime.now()
    elapsed = (now_dt - last_dt).total_seconds() if last_dt else None
    signature_changed = signature != str(last.get("signature") or "")
    cooldown_elapsed = elapsed is None or elapsed >= T_ASSISTANT_NOTIFY_COOLDOWN_SECONDS
    urgency = str(model_analysis.get('urgency') or 'normal')
    if not force and not signature_changed:
        return {'notified': False, 'reason': 'duplicate_signal', 'elapsed_seconds': elapsed}
    if not force and not cooldown_elapsed and urgency != 'high':
        return {'notified': False, 'reason': 'cooldown', 'elapsed_seconds': elapsed}
    if not force and not signature_changed and not cooldown_elapsed:
        return {"notified": False, "reason": "cooldown", "elapsed_seconds": elapsed}
    if not force and signature_changed and elapsed is not None and elapsed < 60:
        return {"notified": False, "reason": "changed_too_soon", "elapsed_seconds": elapsed}

    text = format_t_assistant_notification(assessment)
    results = _dispatch_notification_safely(
        "practice.t_assistant",
        "Jeff小助理持仓T助手",
        text,
        {"actionable_count": assessment.get("actionable_count"), "generated_at": assessment.get("generated_at")},
    )
    failed_count = sum(1 for result in results if not bool(getattr(result, "ok", False)))
    state["t_assistant_notify"] = {
        "last_at": assessment.get("generated_at") or now_ts(),
        "signature": signature,
        "failed_count": failed_count,
        "delivered_count": sum(1 for result in results if bool(getattr(result, "ok", False))),
    }
    return {"notified": True, "failed_count": failed_count, "results": results}


def run_t_assistant_once(
    dt: datetime | None = None,
    *,
    notify: bool = True,
    force_notify: bool = False,
) -> dict[str, Any]:
    """Refresh holdings and push advisory做T guidance. It never executes trades."""
    dt = dt or datetime.now()
    state = load_state()
    should_refresh = bool(T_ASSISTANT_ENABLED) and (force_notify or is_t_assistant_window(dt)[0])
    if should_refresh:
        try:
            refresh_realtime_prices(state)
            refresh_position_intraday(state)
        except Exception as exc:
            state["last_t_assistant_error"] = f"{type(exc).__name__}: {exc}"
    assessment = evaluate_t_opportunities(state, dt)
    if T_ASSISTANT_MODEL_ENABLED:
        if assessment.get('enabled') and assessment.get('in_window') and assessment.get('items'):
            try:
                raw_model_analysis = _host_call('call_model_t_assistant_analysis',
                    assessment,
                    state,
                    state.get('last_t_assistant') if isinstance(state.get('last_t_assistant'), dict) else {},
                )
                model_analysis = normalize_t_model_analysis(raw_model_analysis, assessment)
            except Exception as exc:
                model_analysis = {
                    'enabled': True,
                    'status': 'error',
                    'model': MODEL,
                    'provider': PROVIDER_DISPLAY_NAME,
                    'checked_at': assessment.get('generated_at') or now_ts(),
                    'should_notify': False,
                    'items': [],
                    'error': f'{type(exc).__name__}: {exc}',
                }
            apply_t_model_analysis(assessment, model_analysis)
        else:
            assessment['model_analysis'] = {
                'enabled': True,
                'status': 'skipped',
                'model': MODEL,
                'provider': PROVIDER_DISPLAY_NAME,
                'checked_at': assessment.get('generated_at') or now_ts(),
                'should_notify': False,
                'reason': 'outside_t_window_or_empty',
                'items': [],
            }
    else:
        assessment['model_analysis'] = {
            'enabled': False,
            'status': 'disabled',
            'should_notify': False,
            'items': [],
        }
    notify_result = {"notified": False, "reason": "notify_disabled"}
    if notify:
        notify_result = notify_t_assistant_if_needed(state, assessment, force=force_notify)
    assessment["notify"] = {
        key: value
        for key, value in notify_result.items()
        if key != "results"
    }
    state["last_t_assistant"] = assessment
    if notify_result.get("notified"):
        log_entry = {
            "time": assessment.get("generated_at") or now_ts(),
            "b1_generated_at": "",
            "trade_allowed": False,
            "trade_reason": "持仓T助手仅推送辅助判断，不自动成交",
            "decision": {
                "summary": assessment.get("summary") or "持仓T助手",
                "actions": [
                    {
                        "action": "HOLD",
                        "code": item.get("code"),
                        "shares": item.get("suggested_shares") or 0,
                        'reason': item.get('model_plan') or item.get('model_analysis') or item.get('mode_label') or '',
                    }
                    for item in (assessment.get('items') or [])[:5]
                    if item.get('can_t')
                ],
                'model': str((assessment.get('model_analysis') or {}).get('model') or MODEL),
                'provider': str((assessment.get('model_analysis') or {}).get('provider') or PROVIDER_DISPLAY_NAME),
                "t_assistant": assessment,
            },
            "executed": [],
        }
        state.setdefault("decision_log", []).append(log_entry)
        del state["decision_log"][:-50]
        _sync_decision_to_db(log_entry)
    save_state(state)
    return assessment

def _compact_previous_t_model_analysis(previous: dict[str, Any]) -> dict[str, Any]:
    model_analysis = previous.get('model_analysis') if isinstance(previous.get('model_analysis'), dict) else {}
    return {
        'generated_at': previous.get('generated_at') or '',
        'summary': model_analysis.get('summary') or previous.get('summary') or '',
        'should_notify': bool(model_analysis.get('should_notify')),
        'items': [
            {
                'code': row.get('code'),
                'verdict': row.get('verdict'),
                'action': row.get('action'),
                'confidence': row.get('confidence'),
                'analysis': row.get('analysis'),
                'trigger': row.get('trigger'),
                'invalid_if': row.get('invalid_if'),
            }
            for row in (model_analysis.get('items') or [])
            if isinstance(row, dict)
        ][:8],
    }


def call_model_t_assistant_analysis(
    assessment: dict[str, Any],
    state: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url, api_key = load_decision_model_config()
    portfolio = enrich_portfolio(state)
    compact_portfolio = compact_portfolio_for_decision(portfolio)
    market_env = check_market_environment()
    market_sentiment = check_market_sentiment()
    market_strategy_ctx = current_market_strategy_context()
    decision_intelligence = safe_decision_intelligence_context(portfolio, [], market_strategy_ctx, '')
    rule_items = [
        {
            key: item.get(key)
            for key in (
                'code', 'name', 'qty', 'available_qty', 'last_price', 'quote_time', 'quote_source', 'avg_cost', 'prev_close',
                'change_pct', 'day_high', 'day_low', 'day_high_pct', 'day_low_pct',
                'amplitude_pct', 'pnl_pct', 'suggested_shares', 'can_t', 'mode',
                'mode_label', 'blockers', 'trigger', 'plan', 'invalid_if',
            )
        }
        for item in (assessment.get('items') or [])
        if isinstance(item, dict)
    ]
    previous_compact = _compact_previous_t_model_analysis(previous or {})
    prompt = f'''你是A股持仓做T时机复核模型。系统每5分钟静默检查一次，但绝不能因此定时推送。

你的任务是结合最新行情、账户持仓、大盘与板块环境、本地规则候选以及上一次判断，判断此刻是否已经出现值得用户立即关注的做T时机。

核心原则：
- 只有现在已经满足操作观察条件，用户此刻需要看盘确认时，should_notify 才能为 true。
- 仅仅接近条件、等待回落、继续观察、没有实质变化时，should_notify 必须为 false。
- 同一机会相较上次没有动作方向变化时，不要为了更新价格而重复提醒。
- act 表示此刻已到观察执行窗口；watch 表示尚未到；avoid 表示不应做T。
- action 只能是 sell_first、buy_first、wait。
- 必须遵守A股T+1、100股整数倍、available_qty、现金和交易时段约束。
- suggested_shares 不得超过本地规则给出的建议上限与 available_qty。
- buy_first 还必须受当前现金可买数量约束；预期价差必须足以覆盖交易成本与滑点。
- 规则是硬风控参考，你可以否决规则候选；若本地规则仅为watch但无blockers，你可以基于综合盘面确认act。
- 信息不充分、行情矛盾或置信度不足时宁可静默。
- urgency 只有需要立即看盘且延迟可能明显改变时机时才用 high，否则用 normal。

返回严格JSON对象，字段为：should_notify布尔值、urgency、summary、market_view、items数组。
每个items元素必须包含：code、verdict、action、confidence（0到1）、suggested_shares、analysis、trigger、plan、invalid_if。
不要返回Markdown，不要解释JSON之外的内容。

检查时间：{assessment.get('generated_at')}
账户：{json.dumps(compact_portfolio, ensure_ascii=False)}
本地规则候选：{json.dumps(rule_items, ensure_ascii=False)}
大盘环境：{json.dumps(market_env, ensure_ascii=False)}
市场情绪：{json.dumps(market_sentiment, ensure_ascii=False)}
盘面策略上下文：{json.dumps(compact_market_strategy_context(market_strategy_ctx), ensure_ascii=False)}
最新综合参考：{json.dumps(decision_intelligence, ensure_ascii=False)}
上一次模型判断：{json.dumps(previous_compact, ensure_ascii=False)}
'''
    payload = {
        'model': MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': T_ASSISTANT_MODEL_MAX_TOKENS,
    }
    content = request_chat_content(
        base_url,
        api_key,
        payload,
        MODEL,
        max_retries=2,
        timeout=T_ASSISTANT_MODEL_TIMEOUT_SECONDS,
    )
    result = extract_json(content)
    if not isinstance(result, dict):
        raise RuntimeError('T assistant model did not return an object')
    return result

