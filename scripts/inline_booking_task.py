#!/usr/bin/env python3
"""Bounded no-cookie Inline booking with strict response handling."""
from __future__ import annotations

import importlib.util
import json
import secrets
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
for candidate in (str(ROOT), str(APP), str(COMPAT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

IMPLEMENTATION_PATH = ROOT / ".local-data" / "scratch" / "inline_booking_task_cookie_legacy.py"
SPEC = importlib.util.spec_from_file_location("_inline_booking_task_implementation", IMPLEMENTATION_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load Inline booking implementation")
_implementation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = _implementation
SPEC.loader.exec_module(_implementation)

_implementation.ROOT = ROOT
_implementation.APP = APP
_implementation.COMPAT = COMPAT
_implementation.CONFIG_PATH = ROOT / ".local-data" / "inline-booking.json"
_implementation.DASHBOARD_ENV_PATH = ROOT / ".local-data" / "dashboard.env"
_implementation.DEFAULT_STATE_PATH = ROOT / ".local-data" / "runtime" / "cron" / "state" / "inline-booking.json"

_original_build_request = _implementation.build_request
_original_load_dashboard_env = _implementation.load_dashboard_env
_original_load_config = _implementation.load_config
_original_explicit_failure = _implementation.explicit_failure
_original_response_detail = _implementation.response_detail
_original_perform_booking = _implementation.perform_booking
_original_run = _implementation.run
_original_preflight = _implementation.preflight


def load_dashboard_env(path: Path = _implementation.DASHBOARD_ENV_PATH) -> None:
    _original_load_dashboard_env(path)


def load_config(path: Path = _implementation.CONFIG_PATH) -> dict[str, Any]:
    return _original_load_config(path)


def build_request(config: dict[str, Any]) -> urllib.request.Request:
    """Build a request with fresh random client IDs and no Cookie."""
    compatible = dict(config)
    compatible.setdefault("cookie", "unused-and-never-sent")
    compatible.setdefault("client_fingerprint", "unused-and-never-sent")
    compatible.setdefault("client_session_id", "unused-and-never-sent")
    base = _original_build_request(compatible)
    headers = {
        key: value
        for key, value in base.header_items()
        if key.lower() not in {"cookie", "x-client-fingerprint", "x-client-session-id"}
    }
    headers["X-Client-Fingerprint"] = secrets.token_hex(16)
    headers["X-Client-Session-Id"] = str(uuid.uuid4())
    return urllib.request.Request(
        base.full_url,
        data=base.data,
        headers=headers,
        method=base.get_method(),
    )


def reservation_link(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("reservationLink")
    return value.strip() if isinstance(value, str) else ""


def explicit_failure(body: bytes) -> bool:
    """Only a non-empty top-level reservationLink represents success."""
    return _original_explicit_failure(body) or not reservation_link(body)


def response_detail(body: bytes) -> str:
    detail = _original_response_detail(body)
    link = reservation_link(body)
    if link:
        return f"{detail}；reservationLink={link[:1200]}"
    return f"{detail}；缺少 reservationLink"


def perform_booking(config: dict[str, Any], opener: Any = urllib.request) -> dict[str, Any]:
    """Retry only when no HTTP response exists; stop on the first response."""
    max_attempts = max(1, min(5, int(config.get("max_attempts", 5))))
    retry_interval = max(0.0, min(5.0, float(config.get("retry_interval_seconds", 0.2))))
    last_result: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        result = dict(_original_perform_booking(config, opener=opener))
        result["detail"] = f"发包次数={attempt}/{max_attempts}；{result.get('detail') or '-'}"
        last_result = result
        # HTTP status means the server (or edge) responded. Stop regardless of
        # whether that response contains reservationLink.
        if result.get("http_status") is not None:
            return result
        if attempt < max_attempts and retry_interval:
            time.sleep(retry_interval)
    assert last_result is not None
    last_result["detail"] += "；达到无响应重试上限"
    return last_result


def configured_phones(config: dict[str, Any]) -> list[str]:
    payload = config.get("payload")
    if not isinstance(payload, dict):
        raise _implementation.ConfigError("payload must be a JSON object")

    raw_phones = payload.get("phones")
    if raw_phones is None:
        raw_phones = config.get("phones")
    if raw_phones is None:
        raw_phones = [payload.get("phone")]

    if isinstance(raw_phones, str):
        candidates = [raw_phones]
    elif isinstance(raw_phones, list):
        candidates = raw_phones
    else:
        raise _implementation.ConfigError("phones must be a JSON array")

    phones: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        phone = str(candidate or "").strip()
        if phone and phone not in seen:
            phones.append(phone)
            seen.add(phone)
    if not phones:
        raise _implementation.ConfigError("missing payload.phone or payload.phones")
    return phones


def has_multi_phone_config(config: dict[str, Any]) -> bool:
    payload = config.get("payload")
    return (isinstance(payload, dict) and payload.get("phones") is not None) or config.get("phones") is not None


def config_for_phone(config: dict[str, Any], phone: str) -> dict[str, Any]:
    clone = deepcopy(config)
    payload = clone["payload"]
    payload["phone"] = phone
    payload.pop("phones", None)
    clone.pop("phones", None)
    return clone


def aggregate_results(results: list[dict[str, Any]], attempted: bool) -> dict[str, Any]:
    if not results:
        return {"ok": False, "status": "configuration_error", "http_status": None, "detail": "no phones configured"}

    if all(item.get("status") == "duplicate_blocked" for item in results):
        status = "duplicate_blocked"
    elif all(item.get("ok") for item in results):
        status = "success"
    elif any(item.get("ok") for item in results):
        status = "partial_success"
    else:
        status = "failed" if attempted else "duplicate_blocked"

    details = []
    for item in results:
        http_status = item.get("http_status")
        http_text = str(http_status) if http_status is not None else "-"
        details.append(
            f"phone={item.get('phone', '-')} status={item.get('status', '-')} "
            f"HTTP={http_text} detail={item.get('detail') or '-'}"
        )
    return {
        "ok": all(item.get("ok") for item in results),
        "status": status,
        "http_status": None,
        "detail": " | ".join(details),
        "results": results,
    }


def run(config: dict[str, Any], now: Any = None, opener: Any = urllib.request) -> tuple[dict[str, Any], bool]:
    phones = configured_phones(config)
    if len(phones) == 1 and not has_multi_phone_config(config):
        return _original_run(config, now=now, opener=opener)

    current = (now or _implementation.datetime.now(_implementation.CN_TZ)).astimezone(_implementation.CN_TZ)
    _implementation.validate_timing(config, current)
    path = _implementation.state_path(config)
    state = _implementation.read_state(path)
    phone_states = state.get("phones")
    if not isinstance(phone_states, dict):
        phone_states = {}

    execute_at = _implementation.execution_time(config).isoformat()
    state.update({"started_at": state.get("started_at") or current.isoformat(), "execute_at": execute_at, "status": "started"})
    state["phones"] = phone_states

    pending: list[str] = []
    results: list[dict[str, Any]] = []
    for phone in phones:
        phone_state = phone_states.get(phone)
        if isinstance(phone_state, dict) and phone_state.get("started_at"):
            results.append({
                "phone": phone,
                "ok": False,
                "status": "duplicate_blocked",
                "http_status": None,
                "detail": "already has an execution record for this phone",
            })
            continue
        phone_states[phone] = {"started_at": current.isoformat(), "status": "started"}
        pending.append(phone)

    _implementation.write_state(path, state)

    def book(phone: str) -> dict[str, Any]:
        try:
            result = dict(perform_booking(config_for_phone(config, phone), opener=opener))
        except Exception as exc:
            result = {"ok": False, "status": "internal_error", "http_status": None, "detail": type(exc).__name__}
        result["phone"] = phone
        return result

    if pending:
        max_workers = max(1, min(len(pending), int(config.get("phone_concurrency", len(pending)))))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(book, phone): phone for phone in pending}
            for future in as_completed(futures):
                result = future.result()
                phone = str(result.get("phone") or futures[future])
                phone_states[phone] = dict(result)
                phone_states[phone]["finished_at"] = _implementation.datetime.now(_implementation.CN_TZ).isoformat()
                results.append(result)

    order = {phone: index for index, phone in enumerate(phones)}
    results.sort(key=lambda item: order.get(str(item.get("phone")), len(order)))
    aggregate = aggregate_results(results, bool(pending))
    state.update({key: value for key, value in aggregate.items() if key != "results"})
    state["finished_at"] = _implementation.datetime.now(_implementation.CN_TZ).isoformat()
    _implementation.write_state(path, state)
    return aggregate, bool(pending)


def preflight(config: dict[str, Any]) -> dict[str, Any]:
    phones = configured_phones(config)
    if len(phones) == 1 and not has_multi_phone_config(config):
        return _original_preflight(config)

    execute_at = _implementation.execution_time(config)
    payload = config["payload"]
    state = _implementation.read_state(_implementation.state_path(config))
    phone_states = state.get("phones") if isinstance(state.get("phones"), dict) else {}
    first_request = build_request(config_for_phone(config, phones[0]))
    return {
        "ok": True,
        "execute_at": execute_at.isoformat(),
        "dining_at": f"{payload.get('date')} {payload.get('time')}",
        "method": first_request.get_method(),
        "url": first_request.full_url,
        "phones": phones,
        "request_count": len(phones),
        "has_cookie": bool(first_request.get_header("Cookie")),
        "has_fingerprint": bool(first_request.get_header("X-client-fingerprint")),
        "state_exists": any(isinstance(phone_states.get(phone), dict) and phone_states[phone].get("started_at") for phone in phones),
    }

_implementation.build_request = build_request
_implementation.load_dashboard_env = load_dashboard_env
_implementation.load_config = load_config
_implementation.explicit_failure = explicit_failure
_implementation.response_detail = response_detail
_implementation.perform_booking = perform_booking
_implementation.run = run
_implementation.preflight = preflight

for _name, _value in vars(_implementation).items():
    if not _name.startswith("_") and _name not in {
        "build_request",
        "configured_phones",
        "explicit_failure",
        "load_config",
        "load_dashboard_env",
        "perform_booking",
        "preflight",
        "reservation_link",
        "response_detail",
        "run",
    }:
        globals().setdefault(_name, _value)

main = _implementation.main


if __name__ == "__main__":
    sys.exit(main())
