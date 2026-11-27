"""
Test isolation: every test that touches config, the database or the reports
directory runs inside TempEnv, which points them at a throwaway directory.
Tests never read config.json or solar.db and never make network calls.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common
import database
import email_builder
import monthly_report
import report_builder
import weekly_report

ROOT = Path(__file__).resolve().parent.parent


def example_config(**overrides) -> dict:
    cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    cfg.update(latitude=None, longitude=None)   # no weather lookups
    cfg.update(overrides)
    return cfg


def reading(d: str, produced: float, consumed: float) -> dict:
    net = round(produced - consumed, 3)
    return {
        "date": d, "produced": produced, "consumed": consumed, "net": net,
        "exported": round(max(net, 0), 3), "imported": round(max(-net, 0), 3),
    }


class TempEnvTestCase(unittest.TestCase):
    """Base class: temp config.json, empty temp DB, temp reports dir."""

    config_overrides: dict = {}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = example_config(**self.config_overrides)
        self.config_path = self.tmp / "config.json"
        self.config_path.write_text(json.dumps(self.cfg), encoding="utf-8")
        reports = self.tmp / "reports"
        patches = [
            mock.patch.object(common, "CONFIG_PATH", self.config_path),
            mock.patch.object(database, "DB_PATH", self.tmp / "test.db"),
        ]
        for module in (common, report_builder, email_builder, weekly_report, monthly_report):
            patches.append(mock.patch.object(module, "REPORTS_DIR", reports))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        database.init_db()

    def save(self, d: str, produced: float, consumed: float) -> dict:
        return database.save_reading(reading(d, produced, consumed))

    def row(self, d: str) -> dict:
        return database.get_reading(d)
