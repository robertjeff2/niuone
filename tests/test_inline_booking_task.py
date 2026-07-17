#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inline_booking_task_under_test", ROOT / "scripts" / "inline_booking_task.py")
booking = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(booking)
CN_TZ = ZoneInfo("Asia/Shanghai")


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, _limit):
        return self.body


class SequenceOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def urlopen(self, request, timeout):
        self.calls.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class RecordingOpener:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def urlopen(self, request, timeout):
        with self.lock:
            self.calls.append((request, timeout))
        return FakeResponse({"reservationLink": "https://inline.app/reservations/booking-123"})

def config(state_path: Path):
    return {
        "execute_at": "2026-07-18T12:01:00+08:00",
        "timeout_seconds": 20,
        "max_attempts": 5,
        "retry_interval_seconds": 0,
        "user_agent": "test-agent",
        "state_path": str(state_path),
        "payload": {
            "company": "company",
            "branch": "branch",
            "date": "2026-07-18",
            "time": "13:00",
            "phone": "+886000000000",
        },
    }


class InlineBookingTaskTests(unittest.TestCase):
    def test_no_response_retries_until_first_http_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = SequenceOpener([
                urllib.error.URLError("no response 1"),
                urllib.error.URLError("no response 2"),
                {"reservationLink": "https://inline.app/reservations/booking-123"},
            ])
            result, attempted = booking.run(
                config(Path(tmp) / "state.json"),
                datetime(2026, 7, 18, 12, 1, tzinfo=CN_TZ),
                opener,
            )
            self.assertTrue(attempted)
            self.assertTrue(result["ok"])
            self.assertEqual(len(opener.calls), 3)
            self.assertIn("发包次数=3/5", result["detail"])
            self.assertIn("reservationLink=", result["detail"])

    def test_any_http_response_stops_even_without_reservation_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = SequenceOpener([
                {"success": True, "reservationId": "no-link"},
                {"reservationLink": "must-not-be-sent"},
            ])
            result, attempted = booking.run(
                config(Path(tmp) / "state.json"),
                datetime(2026, 7, 18, 12, 1, tzinfo=CN_TZ),
                opener,
            )
            self.assertTrue(attempted)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(len(opener.calls), 1)
            self.assertIn("缺少 reservationLink", result["detail"])

    def test_five_no_response_attempts_are_the_hard_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = SequenceOpener([urllib.error.URLError("no response")] * 5)
            result, _attempted = booking.run(
                config(Path(tmp) / "state.json"),
                datetime(2026, 7, 18, 12, 1, tzinfo=CN_TZ),
                opener,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(len(opener.calls), 5)
            self.assertIn("达到无响应重试上限", result["detail"])

    def test_multi_phone_config_sends_one_request_per_phone(self):
        phones = ["+886910057093", "+886968103680", "+886960843244"]
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            settings = config(state_path)
            settings["payload"].pop("phone")
            settings["payload"]["phones"] = phones
            opener = RecordingOpener()

            result, attempted = booking.run(
                settings,
                datetime(2026, 7, 18, 12, 1, tzinfo=CN_TZ),
                opener,
            )

            self.assertTrue(attempted)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "success")
            self.assertEqual(len(opener.calls), 3)
            sent_phones = sorted(json.loads(request.data.decode("utf-8"))["phone"] for request, _timeout in opener.calls)
            self.assertEqual(sent_phones, sorted(phones))
            self.assertTrue(all(request.get_header("Cookie") is None for request, _timeout in opener.calls))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(state["phones"].keys()), sorted(phones))
    def test_requests_have_fresh_random_ids_and_no_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = config(Path(tmp) / "state.json")
            first = booking.build_request(settings)
            second = booking.build_request(settings)
            self.assertIsNone(first.get_header("Cookie"))
            self.assertRegex(first.get_header("X-client-fingerprint"), r"^[0-9a-f]{32}$")
            uuid.UUID(first.get_header("X-client-session-id"))
            self.assertNotEqual(first.get_header("X-client-fingerprint"), second.get_header("X-client-fingerprint"))
            self.assertNotEqual(first.get_header("X-client-session-id"), second.get_header("X-client-session-id"))


if __name__ == "__main__":
    unittest.main()
