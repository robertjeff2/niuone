#!/usr/bin/env python3
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(COMPAT))

_tmp_home = tempfile.TemporaryDirectory()
os.environ.setdefault("DASHBOARD_HOME", _tmp_home.name)

import niuniu_practice_trader as trader  # noqa: E402


@contextlib.contextmanager
def patched(**updates):
    originals = {name: getattr(trader, name) for name in updates}
    try:
        for name, value in updates.items():
            setattr(trader, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(trader, name, value)


def sample_sell() -> dict:
    return {
        "time": "2026-07-11 10:00:00",
        "action": "SELL",
        "code": "600000",
        "name": "浦发银行",
        "shares": 100,
        "price": 10.5,
        "amount": 1050.0,
        "fee": 0.54,
        "pnl": 49.46,
        "pnl_pct": 4.95,
        "reason": "测试卖出",
    }


class TradeNotificationHookTests(unittest.TestCase):
    def test_auto_exit_notifies_only_after_state_is_saved(self):
        events = []
        executed = [sample_sell()]
        with patched(
            load_state=lambda: {"positions": {}, "trade_log": [], "cash": 1000.0},
            refresh_realtime_prices=lambda state: None,
            refresh_position_intraday=lambda state: None,
            _refresh_position_bbi=lambda state, dt=None: None,
            check_auto_exits=lambda state, dt: executed,
            record_equity=lambda state: None,
            save_state=lambda state: events.append("save"),
            _notify_trade_executions_safely=lambda trades: events.append(("notify", trades)),
            enrich_portfolio=lambda state: {},
        ):
            result = trader.run_auto_exits_once(datetime(2026, 7, 11, 10, 0))

        self.assertEqual(result["executed"], executed)
        self.assertEqual(events, ["save", ("notify", executed)])

    def test_auto_exit_with_no_fill_does_not_notify(self):
        events = []
        with patched(
            load_state=lambda: {"positions": {}, "trade_log": [], "cash": 1000.0},
            refresh_realtime_prices=lambda state: None,
            refresh_position_intraday=lambda state: None,
            _refresh_position_bbi=lambda state, dt=None: None,
            check_auto_exits=lambda state, dt: [],
            record_equity=lambda state: None,
            save_state=lambda state: events.append("save"),
            _notify_trade_executions_safely=lambda trades: events.append("notify"),
            enrich_portfolio=lambda state: {},
        ):
            trader.run_auto_exits_once(datetime(2026, 7, 11, 10, 0))

        self.assertEqual(events, ["save"])

    def test_deferred_fill_notifies_once_after_state_is_saved(self):
        events = []
        executed = [sample_sell()]
        state = {
            "cash": 1000.0,
            "positions": {},
            "trade_log": [],
            "decision_log": [],
            "pending_decisions": [{
                "id": "pending-1",
                "status": "pending",
                "due_at": "",
                "decision": {"summary": "延迟测试", "actions": []},
                "candidates": [],
                "schedule_slot": "2026-07-11 09:25",
            }],
        }
        with patched(
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            load_state=lambda: state,
            current_market_strategy_context=lambda: {},
            refine_overlimit_buy_actions=lambda *args, **kwargs: {},
            execute_actions=lambda *args, **kwargs: executed,
            enrich_portfolio=lambda value: {},
            _sync_decision_to_db=lambda entry: None,
            _sync_trades_to_db=lambda trades: None,
            _sync_positions_to_db=lambda value: None,
            record_equity=lambda value: None,
            save_state=lambda value: events.append("save"),
            _notify_trade_executions_safely=lambda trades: events.append(("notify", trades)),
        ):
            result = trader.execute_due_pending_decisions(datetime(2026, 7, 11, 13, 0))

        self.assertEqual(result["executed"], executed)
        self.assertEqual(events, ["save", ("notify", executed)])

    def test_model_fill_notifies_once_after_state_is_saved(self):
        events = []
        executed = [sample_sell()]
        state = {"cash": 1000.0, "positions": {}, "trade_log": [], "decision_log": [], "equity_history": []}
        decision = {"summary": "测试决策", "actions": [{"action": "SELL", "code": "600000", "shares": 100}]}
        market_context = {
            "tone_label": "中性",
            "max_open_positions": 6,
            "max_new_buys_per_decision": 2,
            "allow_new_buys": True,
        }
        with patched(
            load_state=lambda: state,
            market_strategy_context_for_b1=lambda payload: market_context,
            compact_market_strategy_context=lambda value: value,
            check_daily_loss_budget=lambda value: (False, 0.0),
            get_adaptive_params=lambda: {},
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            check_market_environment=lambda: {"bullish": True},
            check_market_sentiment=lambda: {"sentiment": "neutral", "detail": ""},
            enrich_portfolio=lambda value: {},
            call_model_decision=lambda *args, **kwargs: decision,
            refine_overlimit_buy_actions=lambda *args, **kwargs: {},
            execute_actions=lambda *args, **kwargs: executed,
            _sync_decision_to_db=lambda entry: None,
            _sync_trades_to_db=lambda trades: None,
            _sync_positions_to_db=lambda value: None,
            record_equity=lambda value: None,
            save_state=lambda value: events.append("save"),
            _notify_trade_executions_safely=lambda trades: events.append(("notify", trades)),
            run_t_assistant_once=lambda notify=True: {},
        ):
            result = trader.run_decision_after_b1({"generated_at": "2026-07-11 10:00:00"}, force=True)

        self.assertEqual(result["executed"], executed)
        self.assertEqual(events, ["save", ("notify", executed)])

    def test_dispatcher_exception_is_isolated_without_echoing_secret(self):
        original = sys.modules.get("notifications")
        secret = "private-webhook-token"
        sys.modules["notifications"] = types.SimpleNamespace(
            notify_trade_executions=lambda trades: (_ for _ in ()).throw(RuntimeError(secret))
        )
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                trader._notify_trade_executions_safely([sample_sell()])
        finally:
            if original is None:
                sys.modules.pop("notifications", None)
            else:
                sys.modules["notifications"] = original

        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn(secret, stderr.getvalue())

    def test_failed_delivery_log_does_not_echo_channel_or_error_secrets(self):
        original = sys.modules.get("notifications")
        secret = "private-provider-error"
        sys.modules["notifications"] = types.SimpleNamespace(
            notify_trade_executions=lambda trades: [
                types.SimpleNamespace(channel="feishu", ok=False, error=secret),
                types.SimpleNamespace(channel=f"telegram-{secret}", ok=False, error=secret),
            ]
        )
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                trader._notify_trade_executions_safely([sample_sell()])
        finally:
            if original is None:
                sys.modules.pop("notifications", None)
            else:
                sys.modules["notifications"] = original

        self.assertIn("2 个渠道", stderr.getvalue())
        self.assertNotIn(secret, stderr.getvalue())

    def test_manual_cycle_no_fill_sends_result_receipt(self):
        calls = []
        original = sys.modules.get("notifications")

        class FakeNotification:
            def __init__(self, event_type, title, text, metadata=None):
                self.event_type = event_type
                self.title = title
                self.text = text
                self.metadata = metadata or {}

        sys.modules["notifications"] = types.SimpleNamespace(
            Notification=FakeNotification,
            dispatch=lambda notification: calls.append(notification) or [types.SimpleNamespace(ok=True)],
        )
        try:
            results = trader.notify_manual_practice_cycle_result_safely({
                "decision": {
                    "summary": "非A股可成交时段，本轮只记录候选，不执行买卖",
                    "actions": [],
                },
                "executed": [],
                "portfolio": {"cash": 50000.0, "total_equity": 50000.0, "positions": []},
            })
        finally:
            if original is None:
                sys.modules.pop("notifications", None)
            else:
                sys.modules["notifications"] = original

        self.assertEqual(len(results), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].event_type, "practice.manual_cycle.no_fill")
        self.assertIn("未产生 BUY/SELL 模拟成交", calls[0].text)

    def test_manual_cycle_with_fill_does_not_duplicate_trade_notification(self):
        calls = []
        original = sys.modules.get("notifications")
        sys.modules["notifications"] = types.SimpleNamespace(
            Notification=lambda *args, **kwargs: calls.append(args),
            dispatch=lambda notification: calls.append(notification) or [types.SimpleNamespace(ok=True)],
        )
        try:
            results = trader.notify_manual_practice_cycle_result_safely({
                "decision": {"summary": "已成交"},
                "executed": [sample_sell()],
            })
        finally:
            if original is None:
                sys.modules.pop("notifications", None)
            else:
                sys.modules["notifications"] = original

        self.assertEqual(results, [])
        self.assertEqual(calls, [])

    def test_holdings_analysis_pushes_result_and_notifies_trade_after_save(self):
        events = []
        executed = [sample_sell()]
        state = {
            "cash": 1000.0,
            "positions": {"600000": {"code": "600000", "name": "浦发银行", "qty": 100, "avg_cost": 10.0}},
            "trade_log": [],
            "decision_log": [],
        }
        portfolio = {
            "cash": 1000.0,
            "total_equity": 2050.0,
            "market_value": 1050.0,
            "positions": [{
                "code": "600000",
                "name": "浦发银行",
                "qty": 100,
                "available_qty": 100,
                "last_price": 10.5,
                "avg_cost": 10.0,
                "position_pct": 51.22,
                "pnl_pct": 5.0,
            }],
        }
        t_assistant = {
            "generated_at": "2026-07-11 10:00:00",
            "summary": "持仓分析完成",
            "items": [],
        }
        decision = {
            "summary": "持仓偏强但先兑现",
            "actions": [{"action": "SELL", "code": "600000", "name": "浦发银行", "shares": 100, "reason": "测试减仓"}],
        }
        with patched(
            load_state=lambda: state,
            refresh_realtime_prices=lambda value: None,
            refresh_position_intraday=lambda value: None,
            _refresh_position_bbi=lambda value, dt=None: None,
            enrich_portfolio=lambda value: portfolio,
            evaluate_t_opportunities=lambda value, dt=None: t_assistant,
            current_market_strategy_context=lambda: {},
            compact_market_strategy_context=lambda value: value,
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            call_model_holdings_decision=lambda *args, **kwargs: decision,
            execute_actions=lambda *args, **kwargs: executed,
            _sync_decision_to_db=lambda entry: None,
            _sync_trades_to_db=lambda trades: None,
            _sync_positions_to_db=lambda value: None,
            record_equity=lambda value: None,
            save_state=lambda value: events.append("save"),
            _notify_holdings_analysis_safely=lambda result: events.append(("holdings_notify", result["executed"])) or [types.SimpleNamespace(ok=True)],
            _persist_holdings_analysis_message=lambda result, delivery_results=None: events.append(("history", result["decision"]["summary"])) or "1",
            _notify_trade_executions_safely=lambda trades: events.append(("trade_notify", trades)),
        ):
            result = trader.run_holdings_analysis_once(datetime(2026, 7, 11, 10, 0), notify=True, simulate_decision=True)

        self.assertEqual(result["executed"], executed)
        self.assertEqual(
            events,
            [
                "save",
                ("holdings_notify", executed),
                ("history", "持仓偏强但先兑现"),
                ("trade_notify", executed),
            ],
        )

    def test_holdings_analysis_without_simulation_does_not_call_model_or_execute(self):
        events = []
        state = {
            "cash": 1000.0,
            "positions": {"600000": {"code": "600000", "name": "浦发银行", "qty": 100, "avg_cost": 10.0}},
            "trade_log": [],
            "decision_log": [],
        }
        portfolio = {
            "cash": 1000.0,
            "total_equity": 2050.0,
            "market_value": 1050.0,
            "positions": [{"code": "600000", "name": "浦发银行", "qty": 100, "available_qty": 100}],
        }
        with patched(
            load_state=lambda: state,
            refresh_realtime_prices=lambda value: None,
            refresh_position_intraday=lambda value: None,
            _refresh_position_bbi=lambda value, dt=None: None,
            enrich_portfolio=lambda value: portfolio,
            evaluate_t_opportunities=lambda value, dt=None: {"summary": "仅观察", "items": []},
            current_market_strategy_context=lambda: {},
            compact_market_strategy_context=lambda value: value,
            is_a_share_execution_time=lambda now=None: (True, "连续竞价交易时段"),
            call_model_holdings_decision=lambda *args, **kwargs: events.append("model"),
            execute_actions=lambda *args, **kwargs: events.append("execute"),
            _sync_decision_to_db=lambda entry: None,
            record_equity=lambda value: None,
            save_state=lambda value: events.append("save"),
            _notify_holdings_analysis_safely=lambda result: [],
            _persist_holdings_analysis_message=lambda result, delivery_results=None: "1",
        ):
            result = trader.run_holdings_analysis_once(datetime(2026, 7, 11, 10, 0), notify=True, simulate_decision=False)

        self.assertFalse(result["simulate_decision"])
        self.assertEqual(result["executed"], [])
        self.assertEqual(events, ["save"])


if __name__ == "__main__":
    unittest.main()
