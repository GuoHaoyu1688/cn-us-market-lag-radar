#!/usr/bin/env python3
from __future__ import annotations

import unittest
import json
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from prediction_forward_ledger import update_prediction_ledger
from prediction_model_v6 import (
    PRIMARY_HORIZON,
    PriceSeries,
    benjamini_hochberg,
    evaluate_events,
    is_trigger,
    stock_trade_paths,
)
from path_safety import resolve_within


def rows(count: int = 45, limit_gap_index: int | None = None) -> list[dict]:
    start = date(2025, 1, 1)
    result = []
    close = 10.0
    for idx in range(count):
        current = start + timedelta(days=idx)
        open_price = close * (1.099 if idx == limit_gap_index else 1.002)
        close = open_price * 1.004
        result.append(
            {
                "date": current.isoformat(),
                "open": open_price,
                "high": close * 1.01,
                "low": open_price * 0.99,
                "close": close,
                "volume": 1_000_000,
            }
        )
    return result


class PointInTimeExecutionTests(unittest.TestCase):
    def test_chart_reference_cannot_escape_output_root(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                resolve_within(Path(directory), "../../.env")

    def test_signal_enters_only_after_us_close_and_t_plus_one(self) -> None:
        series = PriceSeries.from_rows(rows())
        self.assertIsNotNone(series)
        signal_date = series.dates[10]
        paths = stock_trade_paths(
            {"name": "测试公司"},
            series,
            {"000300.SS": series},
            signal_date,
        )
        self.assertEqual(paths[1]["entry_date"], series.dates[11])
        self.assertEqual(paths[1]["exit_date"], series.dates[12])
        self.assertEqual(paths[PRIMARY_HORIZON]["exit_date"], series.dates[11 + PRIMARY_HORIZON])

    def test_limit_gap_entry_is_excluded(self) -> None:
        series = PriceSeries.from_rows(rows(limit_gap_index=11))
        self.assertIsNotNone(series)
        paths = stock_trade_paths(
            {"name": "测试公司"},
            series,
            {"000300.SS": series},
            series.dates[10],
        )
        self.assertEqual(paths, {})

    def test_probability_without_skill_reverts_to_baseline(self) -> None:
        events = []
        start = date(2020, 1, 1)
        for idx in range(60):
            events.append(
                {
                    "signal_date": (start + timedelta(days=idx * 12)).isoformat(),
                    "exit_date": (start + timedelta(days=idx * 12 + 5)).isoformat(),
                    "bucket": "A",
                    "profit_success": idx % 2 == 0,
                    "net_return": 1.0 if idx % 2 == 0 else -1.0,
                }
            )
        result = evaluate_events(events, "A", "profit_success", "net_return")
        self.assertAlmostEqual(result["calibrated_probability"], result["baseline_probability"], places=2)

    def test_primary_endpoint_is_fixed(self) -> None:
        self.assertEqual(PRIMARY_HORIZON, 5)

    def test_recent_breakdown_triggers_relationship_drift_gate(self) -> None:
        events = []
        start = date(2020, 1, 1)
        for idx in range(40):
            success = idx < 28 and idx % 4 != 0
            events.append(
                {
                    "signal_date": (start + timedelta(days=idx * 12)).isoformat(),
                    "exit_date": (start + timedelta(days=idx * 12 + 5)).isoformat(),
                    "bucket": "A",
                    "profit_success": success,
                    "net_return": 1.0 if success else -1.0,
                }
            )
        result = evaluate_events(events, "A", "profit_success", "net_return")
        self.assertTrue(result["relationship_drift"])
        self.assertLess(result["recent_probability"], result["prior_probability"])
        self.assertLess(result["relationship_stability_score"], 50)

    def test_benjamini_hochberg_controls_contemporaneous_family(self) -> None:
        adjusted = benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.20})
        self.assertAlmostEqual(adjusted["a"], 0.04)
        self.assertAlmostEqual(adjusted["b"], 0.0533333333)
        self.assertAlmostEqual(adjusted["c"], 0.0533333333)
        self.assertAlmostEqual(adjusted["d"], 0.20)

    def test_broad_market_fallback_cannot_trigger_prediction(self) -> None:
        state = {
            "us_mapping_quality": "broad_fallback",
            "us_residual_1d": 3.0,
            "us_residual_z": 2.0,
            "lag_gap_neutral": 2.0,
            "us_coverage": 8,
            "cn_coverage": 8,
            "cn_overheat": False,
        }
        self.assertFalse(is_trigger(state))
        state["us_mapping_quality"] = "sector_proxy"
        self.assertTrue(is_trigger(state))

    def test_incomplete_us_session_record_is_invalidated(self) -> None:
        dashboard = {
            "concepts": [],
            "backtest": {
                "follow_model": {
                    "sample_window": {"latest_date": "2026-07-16"},
                    "model_audit": {"version": "test-v6"},
                    "screened_candidates": [],
                }
            },
        }
        record = {
            "record_id": "test-v6|2026-07-17|concept|000001",
            "trigger_date": "2026-07-17",
            "resolved": False,
            "outcome_status": "等待入场",
        }
        with TemporaryDirectory() as directory:
            output = Path(directory)
            path = output / "data" / "prediction-forward-ledger.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([record], ensure_ascii=False), encoding="utf-8")
            summary = update_prediction_ledger(dashboard, output)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(saved[0]["invalidated"])
        self.assertEqual(summary["records"], 0)
        self.assertEqual(summary["invalidated"], 1)
        self.assertEqual(summary["pending"], 0)


if __name__ == "__main__":
    unittest.main()
