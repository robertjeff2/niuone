#!/usr/bin/env python3
"""Wait until the configured instant, then invoke the one-shot booking task."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".local-data" / "inline-booking.json"
TASK = ROOT / "scripts" / "inline_booking_task.py"
LOG = ROOT / ".local-data" / "runtime" / "logs" / "inline-booking-waiter.log"
PID = ROOT / ".local-data" / "runtime" / "inline-booking-waiter.pid"
CN_TZ = ZoneInfo("Asia/Shanghai")


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(CN_TZ).isoformat()} {message}\n")


def main() -> int:
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        execute_at = datetime.fromisoformat(str(config["execute_at"])).astimezone(CN_TZ)
    except Exception as exc:
        log(f"configuration error: {type(exc).__name__}")
        return 2

    PID.parent.mkdir(parents=True, exist_ok=True)
    PID.write_text(str(__import__("os").getpid()), encoding="ascii")
    log(f"waiter started for {execute_at.isoformat()}")
    try:
        while True:
            remaining = (execute_at - datetime.now(CN_TZ)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(30.0, max(0.1, remaining)))
        log("launching booking task")
        completed = subprocess.run(
            [sys.executable, str(TASK)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )
        stdout = (completed.stdout or "").strip().replace("\n", " ")[:1000]
        stderr = (completed.stderr or "").strip().replace("\n", " ")[:500]
        log(f"booking task exit={completed.returncode} stdout={stdout!r} stderr={stderr!r}")
        return completed.returncode
    except Exception as exc:
        log(f"waiter failure: {type(exc).__name__}")
        return 1
    finally:
        try:
            PID.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
