#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ServiceLauncherTests(unittest.TestCase):
    def test_unix_launcher_exposes_service_mode(self):
        text = (ROOT / "run.sh").read_text(encoding="utf-8")
        self.assertIn("--service", text)
        self.assertIn('"$ROOT/scripts/manage-long-running.sh" install', text)
        self.assertLess(text.index('if [[ "$SERVICE_MODE" == "1" ]]'), text.index('exec "$ROOT/run-dashboard.sh"'))

    def test_unix_manager_covers_macos_and_linux_processes(self):
        text = (ROOT / "scripts" / "manage-long-running.sh").read_text(encoding="utf-8")
        for value in (
            "ai.niuone.dashboard",
            "ai.niuone.cron-scheduler",
            "ai.niuone.x-watchlist",
            "niuone-dashboard.service",
            "niuone-cron-scheduler.service",
            "niuone-x-watchlist.service",
            "NIUONE_LOCAL_DATA_DIR",
            "DASHBOARD_ENV_FILE",
        ):
            self.assertIn(value, text)

    def test_windows_launcher_and_manager_cover_all_processes(self):
        launcher = (ROOT / "run.bat").read_text(encoding="utf-8")
        manager = (ROOT / "scripts" / "manage-long-running.ps1").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "run-windows-service.ps1").read_text(encoding="utf-8")
        self.assertIn("--service", launcher)
        self.assertIn("manage-long-running.ps1", launcher)
        for task_name in ("NiuOne Dashboard", "NiuOne Cron Scheduler", "NiuOne X Watchlist"):
            self.assertIn(task_name, manager)
        self.assertIn("Test-NiuOneAdministrator", manager)
        self.assertIn("-Verb RunAs", manager)
        self.assertIn("Administrator permission was not granted", manager)
        for service_name in ("dashboard", "cron-scheduler", "x-watchlist"):
            self.assertIn(service_name, runner)
        self.assertIn("NIUONE_LOCAL_DATA_DIR", runner)
        self.assertIn("DASHBOARD_ENV_FILE", runner)
        self.assertIn('set "%%A=%%~B"', launcher)


if __name__ == "__main__":
    unittest.main()
