#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import threading
import urllib.error
import urllib.parse
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import notifications  # noqa: E402


FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/12345678-abcd-efgh"
DINGTALK_URL = "https://oapi.dingtalk.com/robot/send?access_token=dingtalk-access-token"
WECOM_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=12345678-abcd-efgh-ijkl"
TELEGRAM_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
TELEGRAM_CHAT_ID = "-1001234567890"
FIXED_TIME = 1_700_000_000.25


def sample_trades() -> list[dict]:
    return [
        {
            "time": "2026-07-11 10:00:01",
            "action": "BUY",
            "code": "000001",
            "name": "平安银行",
            "shares": 100,
            "price": 10.123,
            "amount": 1012.3,
            "fee": 0.11,
            "position_after_trade_pct": 3.21,
            "buy_strategy": "trend_pullback",
            "reason": "趋势回踩确认",
        },
        {
            "time": "2026-07-11 14:45:02",
            "action": "SELL",
            "code": "600000",
            "name": "浦发银行",
            "shares": 200,
            "price": 12.5,
            "amount": 2500,
            "fee": 1.27,
            "pnl": 215.5,
            "pnl_pct": 9.43,
            "exit_rule": "time_stop",
            "reason": "尾盘时间止损检查",
        },
    ]


def all_channels_env(*, signed: bool = True) -> dict[str, str]:
    env = {
        notifications.GLOBAL_ENABLED_ENV: "1",
        notifications.TIMEOUT_ENV: "7",
        notifications.FEISHU_ENABLED_ENV: "1",
        notifications.FEISHU_WEBHOOK_ENV: FEISHU_URL,
        notifications.DINGTALK_ENABLED_ENV: "1",
        notifications.DINGTALK_WEBHOOK_ENV: DINGTALK_URL,
        notifications.WECOM_ENABLED_ENV: "1",
        notifications.WECOM_WEBHOOK_ENV: WECOM_URL,
        notifications.TELEGRAM_ENABLED_ENV: "1",
        notifications.TELEGRAM_TOKEN_ENV: TELEGRAM_TOKEN,
        notifications.TELEGRAM_CHAT_ID_ENV: TELEGRAM_CHAT_ID,
    }
    if signed:
        env[notifications.FEISHU_SECRET_ENV] = "feishu-signing-secret"
        env[notifications.DINGTALK_SECRET_ENV] = "SEC-dingtalk-signing-secret"
    return env


def single_channel_env(channel: str) -> dict[str, str]:
    env = {notifications.GLOBAL_ENABLED_ENV: "1"}
    if channel == "feishu":
        env.update({
            notifications.FEISHU_ENABLED_ENV: "1",
            notifications.FEISHU_WEBHOOK_ENV: FEISHU_URL,
        })
    elif channel == "dingtalk":
        env.update({
            notifications.DINGTALK_ENABLED_ENV: "1",
            notifications.DINGTALK_WEBHOOK_ENV: DINGTALK_URL,
        })
    elif channel == "wecom":
        env.update({
            notifications.WECOM_ENABLED_ENV: "1",
            notifications.WECOM_WEBHOOK_ENV: WECOM_URL,
        })
    elif channel == "telegram":
        env.update({
            notifications.TELEGRAM_ENABLED_ENV: "1",
            notifications.TELEGRAM_TOKEN_ENV: TELEGRAM_TOKEN,
            notifications.TELEGRAM_CHAT_ID_ENV: TELEGRAM_CHAT_ID,
        })
    else:
        raise AssertionError(channel)
    return env


def channel_for_url(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname
    if host in {"open.feishu.cn", "open.larksuite.com"}:
        return "feishu"
    if host == "oapi.dingtalk.com":
        return "dingtalk"
    if host == "qyapi.weixin.qq.com":
        return "wecom"
    if host == "api.telegram.org":
        return "telegram"
    raise AssertionError(f"unexpected URL host: {host}")


SUCCESS_RESPONSES = {
    "feishu": {"code": 0, "msg": "success"},
    "dingtalk": {"errcode": 0, "errmsg": "ok"},
    "wecom": {"errcode": 0, "errmsg": "ok"},
    "telegram": {"ok": True, "result": {"message_id": 1}},
}


class RecordingTransport:
    def __init__(self, responses=None):
        self.responses = responses or SUCCESS_RESPONSES
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, url, payload, timeout):
        channel = channel_for_url(url)
        with self.lock:
            self.calls.append({
                "channel": channel,
                "url": url,
                "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
                "timeout": timeout,
            })
        response = self.responses[channel]
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(url, payload, timeout)
        return response


class NotificationTests(unittest.TestCase):
    def test_builtin_registry_has_stable_order(self):
        self.assertEqual(
            notifications.registered_channels()[:4],
            ("feishu", "dingtalk", "wecom", "telegram"),
        )

    def test_trade_batch_builds_all_payloads_and_signatures(self):
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            sample_trades(),
            all_channels_env(),
            transport=transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertEqual([result.channel for result in results], ["feishu", "dingtalk", "wecom", "telegram"])
        self.assertTrue(all(result.ok for result in results), results)
        self.assertEqual(len(transport.calls), 4)
        calls = {call["channel"]: call for call in transport.calls}
        self.assertTrue(all(call["timeout"] == 7 for call in transport.calls))

        message = calls["feishu"]["payload"]["content"]["text"]
        self.assertIn("Jeff小助理模拟成交（2笔）", message)
        self.assertIn("模拟成交，非实盘", message)
        self.assertIn("买入 平安银行(000001)", message)
        self.assertIn("卖出 浦发银行(600000)", message)
        self.assertIn("盈亏 ¥215.50 / 9.43%", message)
        self.assertEqual(calls["dingtalk"]["payload"]["text"]["content"], message)
        self.assertEqual(calls["wecom"]["payload"]["text"]["content"], message)
        self.assertEqual(calls["telegram"]["payload"]["text"], message)

        feishu_payload = calls["feishu"]["payload"]
        feishu_timestamp = str(int(FIXED_TIME))
        feishu_key = f"{feishu_timestamp}\nfeishu-signing-secret".encode()
        expected_feishu_sign = base64.b64encode(
            hmac.new(feishu_key, digestmod=hashlib.sha256).digest()
        ).decode()
        self.assertEqual(feishu_payload["timestamp"], feishu_timestamp)
        self.assertEqual(feishu_payload["sign"], expected_feishu_sign)

        ding_query = urllib.parse.parse_qs(urllib.parse.urlsplit(calls["dingtalk"]["url"]).query)
        ding_timestamp = str(int(FIXED_TIME * 1000))
        expected_ding_sign = base64.b64encode(
            hmac.new(
                b"SEC-dingtalk-signing-secret",
                f"{ding_timestamp}\nSEC-dingtalk-signing-secret".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        self.assertEqual(ding_query["access_token"], ["dingtalk-access-token"])
        self.assertEqual(ding_query["timestamp"], [ding_timestamp])
        self.assertEqual(ding_query["sign"], [expected_ding_sign])
        self.assertEqual(calls["dingtalk"]["payload"]["at"], {"isAtAll": False})

        telegram_call = calls["telegram"]
        self.assertEqual(
            telegram_call["url"],
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        )
        self.assertEqual(telegram_call["payload"]["chat_id"], TELEGRAM_CHAT_ID)
        self.assertTrue(telegram_call["payload"]["disable_web_page_preview"])

    def test_signing_fields_are_omitted_when_secrets_are_blank(self):
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            all_channels_env(signed=False),
            transport=transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertTrue(all(result.ok for result in results), results)
        calls = {call["channel"]: call for call in transport.calls}
        self.assertNotIn("timestamp", calls["feishu"]["payload"])
        self.assertNotIn("sign", calls["feishu"]["payload"])
        self.assertEqual(
            urllib.parse.parse_qs(urllib.parse.urlsplit(calls["dingtalk"]["url"]).query),
            {"access_token": ["dingtalk-access-token"]},
        )

    def test_feishu_legacy_success_response_is_supported(self):
        transport = RecordingTransport({"feishu": {"StatusCode": 0, "StatusMessage": "success"}})

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            single_channel_env("feishu"),
            transport=transport,
        )

        self.assertEqual(results, [notifications.DeliveryResult("feishu", True, "")])

    def test_disabled_and_non_trade_batches_make_no_requests(self):
        transport = RecordingTransport()
        enabled = single_channel_env("wecom")

        self.assertEqual(
            notifications.notify_trade_executions(sample_trades(), {}, transport=transport),
            [],
        )
        self.assertEqual(
            notifications.notify_trade_executions([], enabled, transport=transport),
            [],
        )
        self.assertEqual(
            notifications.notify_trade_executions([{"action": "HOLD"}], enabled, transport=transport),
            [],
        )
        self.assertEqual(transport.calls, [])

    def test_only_enabled_channels_are_constructed_and_sent(self):
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            single_channel_env("wecom"),
            transport=transport,
        )

        self.assertEqual(results, [notifications.DeliveryResult("wecom", True, "")])
        self.assertEqual([call["channel"] for call in transport.calls], ["wecom"])

    def test_dispatch_to_channel_ignores_switches_and_sends_only_selected_channel_once(self):
        env = all_channels_env()
        env[notifications.GLOBAL_ENABLED_ENV] = "0"
        env[notifications.DINGTALK_ENABLED_ENV] = "0"
        transport = RecordingTransport()

        result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "这是一条测试消息"),
            "dingtalk",
            env,
            transport=transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertEqual(result, notifications.DeliveryResult("dingtalk", True, ""))
        self.assertEqual([call["channel"] for call in transport.calls], ["dingtalk"])

    def test_dispatch_to_channel_rejects_unknown_channel_without_request(self):
        transport = RecordingTransport()

        result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "未知渠道"),
            "not-registered",
            all_channels_env(),
            transport=transport,
        )

        self.assertEqual(result.channel, "not-registered")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unknown notification channel")
        self.assertEqual(transport.calls, [])

    def test_dispatch_to_channel_timeout_and_config_errors_are_safe(self):
        timeout_env = single_channel_env("wecom")
        timeout_env[notifications.TIMEOUT_ENV] = "not-a-number"
        timeout_transport = RecordingTransport()

        timeout_result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "超时配置"),
            "wecom",
            timeout_env,
            transport=timeout_transport,
        )

        self.assertEqual(timeout_result.channel, "wecom")
        self.assertFalse(timeout_result.ok)
        self.assertTrue(timeout_result.error)
        self.assertEqual(timeout_transport.calls, [])

        leaked_secret = "CONFIG-SECRET-123"
        config_env = single_channel_env("feishu")
        config_env[notifications.FEISHU_WEBHOOK_ENV] = (
            f"https://evil.example/open-apis/bot/v2/hook/{leaked_secret}"
        )
        config_transport = RecordingTransport()

        config_result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "渠道配置"),
            "feishu",
            config_env,
            transport=config_transport,
        )

        self.assertFalse(config_result.ok)
        self.assertNotIn(leaked_secret, config_result.error)
        self.assertNotIn(config_env[notifications.FEISHU_WEBHOOK_ENV], config_result.error)
        self.assertEqual(config_transport.calls, [])

    def test_dispatch_to_channel_transport_exception_does_not_leak_secret(self):
        env = single_channel_env("telegram")
        calls = []

        def failing_transport(url, payload, timeout):
            calls.append((url, payload, timeout))
            raise RuntimeError(f"request failed for {TELEGRAM_TOKEN}")

        result = notifications.dispatch_to_channel(
            notifications.Notification("notification.test", "通知测试", "传输失败"),
            "telegram",
            env,
            transport=failing_transport,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "RuntimeError")
        self.assertNotIn(TELEGRAM_TOKEN, result.error)
        self.assertEqual(len(calls), 1)

    def test_invalid_timeout_fails_enabled_channels_without_requests(self):
        for timeout in ("not-a-number", "0", "31", "nan"):
            with self.subTest(timeout=timeout):
                env = all_channels_env()
                env[notifications.TIMEOUT_ENV] = timeout
                transport = RecordingTransport()

                results = notifications.notify_trade_executions(
                    sample_trades()[:1],
                    env,
                    transport=transport,
                )

                self.assertEqual([item.channel for item in results], ["feishu", "dingtalk", "wecom", "telegram"])
                self.assertTrue(all(not item.ok for item in results))
                self.assertEqual(transport.calls, [])

    def test_invalid_urls_and_credentials_never_reach_transport(self):
        cases = []

        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "http://open.feishu.cn/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_http", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "https://evil.example/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_host", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "https://user:pass@open.feishu.cn/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_url_credentials", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = "https://open.feishu.cn:444/open-apis/bot/v2/hook/SUPERSECRET"
        cases.append(("feishu_port", env, "SUPERSECRET"))
        env = single_channel_env("feishu")
        env[notifications.FEISHU_WEBHOOK_ENV] = FEISHU_URL + "#SUPERSECRET"
        cases.append(("feishu_fragment", env, "SUPERSECRET"))

        env = single_channel_env("dingtalk")
        env[notifications.DINGTALK_WEBHOOK_ENV] = "https://oapi.dingtalk.com/robot/send"
        cases.append(("dingtalk_missing_token", env, "dingtalk-access-token"))
        env = single_channel_env("dingtalk")
        env[notifications.DINGTALK_WEBHOOK_ENV] = DINGTALK_URL + "&extra=SUPERSECRET"
        cases.append(("dingtalk_extra_query", env, "SUPERSECRET"))

        env = single_channel_env("wecom")
        env[notifications.WECOM_WEBHOOK_ENV] = "https://qyapi.weixin.qq.com/cgi-bin/not-webhook/send?key=SUPERSECRET"
        cases.append(("wecom_path", env, "SUPERSECRET"))
        env = single_channel_env("wecom")
        env[notifications.WECOM_WEBHOOK_ENV] = WECOM_URL + "%0Asecret"
        cases.append(("wecom_control", env, "secret"))

        env = single_channel_env("telegram")
        env[notifications.TELEGRAM_TOKEN_ENV] = "SUPERSECRET"
        cases.append(("telegram_token", env, "SUPERSECRET"))
        env = single_channel_env("telegram")
        env[notifications.TELEGRAM_CHAT_ID_ENV] = "bad chat SUPERSECRET"
        cases.append(("telegram_chat", env, "SUPERSECRET"))
        env = single_channel_env("telegram")
        env[notifications.TELEGRAM_TOKEN_ENV] = TELEGRAM_TOKEN + "\nSUPERSECRET"
        cases.append(("telegram_control", env, "SUPERSECRET"))

        for label, env, secret in cases:
            with self.subTest(label=label):
                transport = RecordingTransport()
                results = notifications.notify_trade_executions(
                    sample_trades()[:1],
                    env,
                    transport=transport,
                )
                self.assertEqual(len(results), 1)
                self.assertFalse(results[0].ok)
                self.assertNotIn(secret, results[0].error)
                self.assertEqual(transport.calls, [])

    def test_provider_business_errors_are_isolated_and_sanitized(self):
        responses = {
            "feishu": {"code": 19001, "msg": "feishu-signing-secret"},
            "dingtalk": {"errcode": 310000, "errmsg": "SEC-dingtalk-signing-secret"},
            "wecom": {"errcode": 93000, "errmsg": "12345678-abcd-efgh-ijkl"},
            "telegram": {"ok": False, "description": TELEGRAM_TOKEN},
        }
        transport = RecordingTransport(responses)

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            all_channels_env(),
            transport=transport,
        )

        self.assertEqual([item.channel for item in results], ["feishu", "dingtalk", "wecom", "telegram"])
        self.assertTrue(all(not item.ok for item in results))
        combined_errors = "\n".join(item.error for item in results)
        for secret in (
            "feishu-signing-secret",
            "SEC-dingtalk-signing-secret",
            "12345678-abcd-efgh-ijkl",
            TELEGRAM_TOKEN,
        ):
            self.assertNotIn(secret, combined_errors)

    def test_four_channels_run_concurrently_once_and_keep_result_order(self):
        barrier = threading.Barrier(4)
        counts = {name: 0 for name in ("feishu", "dingtalk", "wecom", "telegram")}
        lock = threading.Lock()

        def concurrent_transport(url, _payload, _timeout):
            channel = channel_for_url(url)
            with lock:
                counts[channel] += 1
            barrier.wait(timeout=3)
            if channel == "dingtalk":
                raise RuntimeError("transport leaked SEC-dingtalk-signing-secret")
            return SUCCESS_RESPONSES[channel]

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            all_channels_env(),
            transport=concurrent_transport,
            clock=lambda: FIXED_TIME,
        )

        self.assertEqual([item.channel for item in results], ["feishu", "dingtalk", "wecom", "telegram"])
        self.assertEqual([item.ok for item in results], [True, False, True, True])
        self.assertEqual(results[1].error, "RuntimeError")
        self.assertEqual(counts, {"feishu": 1, "dingtalk": 1, "wecom": 1, "telegram": 1})

    def test_http_and_network_errors_do_not_expose_request_urls(self):
        env = single_channel_env("telegram")

        def http_failure(url, _payload, _timeout):
            raise urllib.error.HTTPError(url, 500, TELEGRAM_TOKEN, {}, None)

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            env,
            transport=http_failure,
        )
        self.assertEqual(results[0].error, "HTTP error 500")
        self.assertNotIn(TELEGRAM_TOKEN, results[0].error)

        def network_failure(_url, _payload, _timeout):
            raise urllib.error.URLError(f"network failed for {TELEGRAM_TOKEN}")

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            env,
            transport=network_failure,
        )
        self.assertEqual(results[0].error, "network error")
        self.assertNotIn(TELEGRAM_TOKEN, results[0].error)

        def nominally_sanitized_failure(_url, _payload, _timeout):
            raise notifications.NotificationDeliveryError(f"provider echoed {TELEGRAM_TOKEN}")

        results = notifications.notify_trade_executions(
            sample_trades()[:1],
            env,
            transport=nominally_sanitized_failure,
        )
        self.assertEqual(results[0].error, "provider echoed [redacted]")
        self.assertNotIn(TELEGRAM_TOKEN, results[0].error)

    def test_long_unicode_message_is_truncated_on_a_character_boundary(self):
        trade = sample_trades()[0]
        trade["reason"] = "很长的交易原因" * 1000
        transport = RecordingTransport()

        results = notifications.notify_trade_executions(
            [trade],
            single_channel_env("wecom"),
            transport=transport,
        )

        self.assertTrue(results[0].ok)
        text = transport.calls[0]["payload"]["text"]["content"]
        self.assertLessEqual(len(text.encode("utf-8")), notifications.MAX_MESSAGE_BYTES)
        text.encode("utf-8").decode("utf-8")

    def test_registry_can_extend_dispatch_without_changing_core(self):
        class CustomChannel:
            name = "custom_test"

            def send(self, notification, *, timeout, transport, clock):
                transport("https://custom.invalid/send", {"text": notification.plain_text()}, timeout)

        notifications.register_channel(
            "custom_test",
            lambda _env: CustomChannel(),
            enabled_env="DASHBOARD_CUSTOM_TEST_NOTIFICATION_ENABLED",
            replace=True,
        )
        calls = []

        def transport(url, payload, timeout):
            calls.append((url, payload, timeout))
            return {"ok": True}

        result = notifications.dispatch(
            notifications.Notification("test", "Title", "Body"),
            {
                notifications.GLOBAL_ENABLED_ENV: "1",
                "DASHBOARD_CUSTOM_TEST_NOTIFICATION_ENABLED": "1",
            },
            transport=transport,
        )

        self.assertEqual(result, [notifications.DeliveryResult("custom_test", True, "")])
        self.assertEqual(calls[0][0], "https://custom.invalid/send")
        self.assertEqual(calls[0][1], {"text": "Title\nBody"})


class HttpTransportTests(unittest.TestCase):
    class FakeResponse:
        status = 200

        def __init__(self, body):
            self.body = body
            self.read_limit = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            self.read_limit = limit
            return self.body

    class FakeOpener:
        def __init__(self, response):
            self.response = response
            self.request = None
            self.timeout = None

        def open(self, request, timeout=0):
            self.request = request
            self.timeout = timeout
            return self.response

    def with_fake_opener(self, body, callback):
        response = self.FakeResponse(body)
        opener = self.FakeOpener(response)
        captured_handlers = []
        original = notifications.urllib.request.build_opener
        try:
            def fake_build_opener(*handlers):
                captured_handlers.extend(handlers)
                return opener

            notifications.urllib.request.build_opener = fake_build_opener
            return callback(opener, response, captured_handlers)
        finally:
            notifications.urllib.request.build_opener = original

    def test_post_json_sets_headers_timeout_and_disables_redirects(self):
        def scenario(opener, response, handlers):
            result = notifications._post_json(
                "https://example.invalid/hook",
                {"message": "中文"},
                6.5,
            )
            self.assertEqual(result, {"ok": True})
            self.assertEqual(opener.timeout, 6.5)
            self.assertEqual(opener.request.get_method(), "POST")
            self.assertEqual(json.loads(opener.request.data.decode()), {"message": "中文"})
            headers = dict(opener.request.header_items())
            self.assertEqual(headers["Content-type"], "application/json; charset=utf-8")
            self.assertEqual(headers["Accept"], "application/json")
            self.assertEqual(headers["User-agent"], "NiuOne/1.0")
            self.assertEqual(response.read_limit, notifications.MAX_RESPONSE_BYTES + 1)
            self.assertEqual(len(handlers), 1)
            self.assertIsInstance(handlers[0], notifications._NoRedirectHandler)
            self.assertIsNone(handlers[0].redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid"))

        self.with_fake_opener(b'{"ok":true}', scenario)

    def test_post_json_rejects_invalid_or_oversized_responses(self):
        bad_bodies = [
            b"not-json",
            b"[]",
            b"x" * (notifications.MAX_RESPONSE_BYTES + 1),
        ]
        for body in bad_bodies:
            with self.subTest(body_length=len(body)):
                def scenario(_opener, _response, _handlers):
                    with self.assertRaises(notifications.NotificationDeliveryError):
                        notifications._post_json("https://example.invalid/hook", {"ok": True}, 5)

                self.with_fake_opener(body, scenario)


if __name__ == "__main__":
    unittest.main()
