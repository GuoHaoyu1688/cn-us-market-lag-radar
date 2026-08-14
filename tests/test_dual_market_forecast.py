#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from build_dual_market_forecast import validate_payload
from audit_dual_market_forecast import _audit_ledger
from forecasting.collectors import (
    _completed_us_session,
    _existing_usable,
    _merge_chart_rows,
)
from forecasting.features import build_feature_frame
from forecasting.ledger import _prediction_id, _settle_record
from forecasting.market_specs import classify_cn_board, daily_price_limit_pct
from forecasting.market_specs import CN_SPEC, US_SPEC
from forecasting.models import date_walk_forward_splits, fit_stacking_weights
from forecasting.pipeline import _completed_benchmark_rows, _forward_window, _reliability


class MarketSpecTests(unittest.TestCase):
    def test_all_a_share_boards_are_eligible(self) -> None:
        expected = {
            "600519": ("沪市主板", 10.0),
            "000858": ("深市主板", 10.0),
            "300750": ("创业板", 20.0),
            "301269": ("创业板", 20.0),
            "688981": ("科创板", 20.0),
            "689009": ("科创板", 20.0),
            "835368": ("北交所", 30.0),
            "430047": ("北交所", 30.0),
            "920001": ("北交所", 30.0),
        }
        for code, (board, limit) in expected.items():
            with self.subTest(code=code):
                instrument = classify_cn_board(code)
                self.assertTrue(instrument.eligible)
                self.assertEqual(instrument.board, board)
                self.assertEqual(daily_price_limit_pct(instrument), limit)

    def test_unknown_code_is_not_silently_mapped(self) -> None:
        instrument = classify_cn_board("777777")
        self.assertFalse(instrument.eligible)
        self.assertIsNone(daily_price_limit_pct(instrument))

    def test_new_listing_has_no_assumed_limit(self) -> None:
        instrument = classify_cn_board("300750")
        self.assertIsNone(daily_price_limit_pct(instrument, listing_sessions=3))


class PayloadContractTests(unittest.TestCase):
    def test_existing_payload_contract_when_available(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "output/market_lag_dashboard/data/forecasts-v1.json"
        )
        if not path.exists():
            self.skipTest("forecast payload has not been built yet")
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_payload(payload)
        for market in ("cn", "us"):
            validation = payload["markets"][market]["validation"]
            self.assertGreaterEqual(validation["folds"], 3)
            self.assertGreater(validation["samples"], 0)
            self.assertLess(validation["stacking_end"], validation["calibration_start"])
            self.assertLess(validation["calibration_end"], validation["holdout_start"])
            as_of_dates = {
                forecast["as_of"]
                for forecast in payload["markets"][market]["forecasts"]
            }
            self.assertEqual(len(as_of_dates), 1)
            for forecast in payload["markets"][market]["forecasts"]:
                self.assertTrue(math.isfinite(forecast["probability_up"]))
                self.assertIn(forecast["decision_status"], {"可研究", "暂缓"})
                self.assertEqual(forecast["horizon"], 5)
                self.assertTrue(forecast["chart_ref"].startswith("./data/charts/"))
                breakdown = forecast["diagnostic_score_breakdown"]
                self.assertEqual(breakdown["formula_version"], "linear-diagnostic-v1")
                self.assertEqual(len(breakdown["dimensions"]), 4)
                self.assertAlmostEqual(
                    forecast["reliability_score"],
                    sum(item["score"] for item in breakdown["dimensions"]),
                    places=1,
                )
                self.assertIsInstance(forecast["evidence_gaps"], list)
                self.assertIsInstance(forecast["execution_restrictions"], list)

    def test_linear_diagnostic_breakdown_matches_total(self) -> None:
        score, grade, reasons, breakdown = _reliability(
            validation={
                "brier_skill": 0.02,
                "return_skill": 0.01,
                "calibration_error": 0.04,
                "empirical_interval_coverage": 0.78,
                "samples": 1200,
            },
            data_quality=0.9,
            agreement=0.8,
            probability=0.56,
        )
        dimension_total = sum(
            dimension["score"] for dimension in breakdown["dimensions"]
        )
        self.assertAlmostEqual(score, dimension_total, places=1)
        self.assertEqual(breakdown["max_score"], 100.0)
        self.assertEqual(breakdown["formula_version"], "linear-diagnostic-v1")
        self.assertIn(grade, {"A", "B", "C", "D"})
        self.assertEqual(reasons, [])


class CollectorFreshnessTests(unittest.TestCase):
    def test_required_market_session_overrides_loose_business_lag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "chart.json"
            path.write_text(
                json.dumps(
                    {
                        "end": "2026-07-28",
                        "rows": [
                            {"date": f"2025-01-{(index % 28) + 1:02d}"}
                            for index in range(320)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                _existing_usable(path, required_end="2026-07-28")
            )
            self.assertFalse(
                _existing_usable(path, required_end="2026-07-29")
            )

    def test_intraday_us_bar_is_not_a_completed_signal(self) -> None:
        intraday = datetime(
            2026,
            7,
            29,
            9,
            45,
            tzinfo=ZoneInfo("America/New_York"),
        )
        self.assertFalse(
            _completed_us_session("2026-07-29", now=intraday)
        )
        self.assertTrue(
            _completed_us_session("2026-07-28", now=intraday)
        )

    def test_secondary_daily_bar_fills_primary_source_gap(self) -> None:
        primary = [
            {"date": "2026-07-27", "close": 100.0},
            {"date": "2026-07-29", "close": 999.0},
        ]
        secondary = [{"date": "2026-07-28", "close": 101.0}]
        merged = _merge_chart_rows(primary, secondary)
        self.assertEqual(
            [row["date"] for row in merged],
            ["2026-07-27", "2026-07-28", "2026-07-29"],
        )
        self.assertEqual(merged[1]["close"], 101.0)


class LedgerAuditTests(unittest.TestCase):
    @staticmethod
    def forecast(
        *,
        market: str = "cn",
        symbol: str = "600519",
        as_of: str = "2026-07-29",
        eligible: bool = True,
    ) -> dict:
        return {
            "market": market,
            "symbol": symbol,
            "as_of": as_of,
            "horizon": 5,
            "forward_eligible": eligible,
            "probability_up": 0.56,
            "expected_return": 0.012,
            "quantiles": {"q10": -0.03, "q50": 0.01, "q90": 0.05},
            "decision_status": "可研究",
        }

    @classmethod
    def record(cls, forecast: dict, *, version: str = "test-v1") -> dict:
        record = {
            "market": forecast["market"],
            "symbol": forecast["symbol"],
            "as_of": forecast["as_of"],
            "horizon": forecast["horizon"],
            "model_version": version,
            "forward_evidence": "verified_pre_entry",
            "status": "pending",
            "probability_up": forecast["probability_up"],
            "expected_return": forecast["expected_return"],
            "q10": forecast["quantiles"]["q10"],
            "q50": forecast["quantiles"]["q50"],
            "q90": forecast["quantiles"]["q90"],
            "decision_status": forecast["decision_status"],
        }
        record["id"] = _prediction_id(record)
        return record

    def test_historical_pending_records_are_retained_not_counted_as_current(self) -> None:
        current = self.forecast()
        historical = self.forecast(
            market="us",
            symbol="SPY",
            as_of="2026-07-27",
            eligible=False,
        )
        payload = {
            "model": {"version": "test-v1"},
            "markets": {
                "cn": {"forecasts": [current]},
                "us": {"forecasts": [historical]},
            },
        }
        checks, errors = _audit_ledger(
            payload,
            [self.record(current), self.record(historical)],
        )
        self.assertEqual(errors, [])
        self.assertIn("匹配 1 条", checks[0])
        self.assertIn("历史记录保留 1 条", checks[0])

    def test_duplicate_ledger_prediction_id_fails(self) -> None:
        current = self.forecast()
        record = self.record(current)
        payload = {
            "model": {"version": "test-v1"},
            "markets": {
                "cn": {"forecasts": [current]},
                "us": {"forecasts": []},
            },
        }
        _, errors = _audit_ledger(payload, [record, dict(record)])
        self.assertTrue(any("重复预测ID" in error for error in errors))

    def test_frozen_field_drift_fails(self) -> None:
        current = self.forecast()
        record = self.record(current)
        record["probability_up"] = 0.57
        payload = {
            "model": {"version": "test-v1"},
            "markets": {
                "cn": {"forecasts": [current]},
                "us": {"forecasts": []},
            },
        }
        _, errors = _audit_ledger(payload, [record])
        self.assertTrue(any("漂移" in error for error in errors))


class PointInTimeTests(unittest.TestCase):
    @staticmethod
    def synthetic_rows(count: int = 760) -> list[dict]:
        rows = []
        current = date(2022, 1, 3)
        index = 0
        while len(rows) < count:
            if current.weekday() < 5:
                close = 100 + index * 0.08 + math.sin(index / 13)
                rows.append(
                    {
                        "date": current.isoformat(),
                        "open": close - 0.15,
                        "high": close + 0.6,
                        "low": close - 0.7,
                        "close": close,
                        "volume": 1_000_000 + index * 100,
                    }
                )
                index += 1
            current += timedelta(days=1)
        return rows

    def test_fixed_five_session_label_uses_next_real_open(self) -> None:
        rows = self.synthetic_rows()
        frame = build_feature_frame(rows, rows, CN_SPEC, board_key="chinext")
        target_date = rows[300]["date"]
        selected = frame.loc[frame["date"].dt.strftime("%Y-%m-%d") == target_date].iloc[0]
        expected = rows[305]["close"] / rows[301]["open"] - 1 - CN_SPEC.round_trip_cost
        self.assertAlmostEqual(float(selected["target_return"]), expected, places=12)
        self.assertTrue(frame.tail(5)["target_return"].isna().all())

    def test_suspended_entry_session_voids_training_label(self) -> None:
        benchmark_rows = self.synthetic_rows()
        stock_rows = [dict(row) for row in benchmark_rows]
        for index in range(301, 306):
            stock_rows[index].update(
                {
                    "open": stock_rows[300]["close"],
                    "high": stock_rows[300]["close"],
                    "low": stock_rows[300]["close"],
                    "close": stock_rows[300]["close"],
                    "volume": 0,
                }
            )
        frame = build_feature_frame(
            stock_rows,
            benchmark_rows,
            CN_SPEC,
            board_key="sh_main",
        )
        target_date = stock_rows[300]["date"]
        selected = frame.loc[
            frame["date"].dt.strftime("%Y-%m-%d") == target_date
        ].iloc[0]
        self.assertTrue(math.isnan(float(selected["target_return"])))

    def test_limit_up_entry_voids_training_label(self) -> None:
        benchmark_rows = self.synthetic_rows()
        stock_rows = [dict(row) for row in benchmark_rows]
        limit_price = stock_rows[300]["close"] * 1.10
        stock_rows[301].update(
            {
                "open": limit_price,
                "high": limit_price,
                "low": limit_price,
                "close": limit_price,
                "volume": 1_500_000,
            }
        )
        frame = build_feature_frame(
            stock_rows,
            benchmark_rows,
            CN_SPEC,
            board_key="sh_main",
        )
        selected = frame.loc[
            frame["date"].dt.strftime("%Y-%m-%d") == stock_rows[300]["date"]
        ].iloc[0]
        self.assertTrue(math.isnan(float(selected["target_return"])))

    def test_forward_window_rejects_missed_a_share_open(self) -> None:
        generated_at = datetime(
            2026,
            7,
            28,
            10,
            59,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        self.assertFalse(
            _forward_window(
                CN_SPEC,
                as_of="2026-07-27",
                generated_at=generated_at,
            )["forward_eligible"]
        )
        self.assertTrue(
            _forward_window(
                US_SPEC,
                as_of="2026-07-27",
                generated_at=generated_at,
            )["forward_eligible"]
        )

    def test_forward_window_rejects_incomplete_signal_session(self) -> None:
        cn_intraday = datetime(
            2026,
            7,
            28,
            10,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        us_intraday = datetime(
            2026,
            7,
            28,
            22,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        self.assertEqual(
            _forward_window(
                CN_SPEC,
                as_of="2026-07-28",
                generated_at=cn_intraday,
            )["entry_window_status"],
            "signal_incomplete",
        )
        self.assertEqual(
            _forward_window(
                US_SPEC,
                as_of="2026-07-28",
                generated_at=us_intraday,
            )["entry_window_status"],
            "signal_incomplete",
        )
        rows = [
            {"date": "2026-07-27"},
            {"date": "2026-07-28"},
        ]
        self.assertEqual(
            [row["date"] for row in _completed_benchmark_rows(
                rows,
                spec=CN_SPEC,
                generated_at=cn_intraday,
            )],
            ["2026-07-27"],
        )

    def test_walk_forward_is_grouped_by_date_and_purged(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=720)
        frame = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "symbol": ["AAA"] * len(dates) + ["BBB"] * len(dates),
            }
        ).sort_values(["date", "symbol"]).reset_index(drop=True)
        splits = date_walk_forward_splits(frame, n_splits=4, purge_sessions=6)
        self.assertGreaterEqual(len(splits), 3)
        unique_dates = list(sorted(frame["date"].dt.normalize().unique()))
        positions = {value: index for index, value in enumerate(unique_dates)}
        for train_idx, test_idx in splits:
            train_max = frame.iloc[train_idx]["date"].max().to_datetime64()
            test_min = frame.iloc[test_idx]["date"].min().to_datetime64()
            self.assertGreaterEqual(positions[test_min] - positions[train_max], 6)
            test_dates = set(frame.iloc[test_idx]["date"])
            for test_date in test_dates:
                self.assertEqual(
                    set(frame.loc[frame["date"] == test_date, "symbol"]),
                    {"AAA", "BBB"},
                )

    def test_forward_prediction_id_ignores_refresh_snapshot(self) -> None:
        base = {
            "market": "cn",
            "symbol": "300750",
            "as_of": "2026-07-28",
            "horizon": 5,
            "model_version": "v1",
        }
        self.assertEqual(
            _prediction_id({**base, "snapshot_hash": "first"}),
            _prediction_id({**base, "snapshot_hash": "second"}),
        )

    def test_challenger_weight_cap_survives_failed_model_removal(self) -> None:
        labels = pd.Series(([0, 1] * 200), dtype=int).to_numpy()
        predictions = pd.DataFrame(
            {
                "prior": [0.5] * len(labels),
                "elastic_net": [0.1 if label == 0 else 0.9 for label in labels],
                "gradient_boosting": [0.9 if label == 0 else 0.1 for label in labels],
                "robust_trend": [0.5] * len(labels),
            }
        )
        weights = fit_stacking_weights(predictions, labels)
        self.assertLessEqual(weights["elastic_net"], 0.75 + 1e-12)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=12)

    def test_forward_ledger_voids_zero_volume_entry(self) -> None:
        benchmark_rows = self.synthetic_rows()
        stock_rows = [dict(row) for row in benchmark_rows]
        stock_rows[301]["volume"] = 0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart_dir = root / "output/market_lag_dashboard/data/charts"
            (chart_dir / "cn").mkdir(parents=True)
            (chart_dir / "us").mkdir(parents=True)
            (chart_dir / "cn/600000.json").write_text(
                json.dumps({"rows": stock_rows}),
                encoding="utf-8",
            )
            (chart_dir / "us/000300.SS.json").write_text(
                json.dumps({"rows": benchmark_rows}),
                encoding="utf-8",
            )
            record = {
                "market": "cn",
                "symbol": "600000",
                "as_of": stock_rows[300]["date"],
                "horizon": 5,
                "chart_ref": "./data/charts/cn/600000.json",
                "status": "pending",
                "round_trip_cost": 0.0035,
                "probability_up": 0.55,
            }
            settled = _settle_record(record, root)
            self.assertEqual(settled["status"], "void")
            self.assertEqual(settled["void_reason"], "entry_session_not_tradeable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
