from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DemoContractTests(unittest.TestCase):
    def test_demo_payload_and_charts_exist(self) -> None:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "generate_demo_data.py"), "--check"],
            cwd=ROOT,
            check=True,
        )
        path = ROOT / "output/market_lag_dashboard/demo/forecasts-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["demo_mode"])
        for market in ("cn", "us"):
            forecasts = payload["markets"][market]["forecasts"]
            self.assertGreaterEqual(len(forecasts), 4)
            for forecast in forecasts:
                self.assertEqual(forecast["decision_status"], "暂缓")
                chart = ROOT / "output/market_lag_dashboard" / forecast["chart_ref"].removeprefix("./")
                self.assertTrue(chart.exists())

    def test_public_tree_passes_sanitization(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "sanitize_check.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
