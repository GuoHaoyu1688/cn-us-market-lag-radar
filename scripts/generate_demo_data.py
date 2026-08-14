#!/usr/bin/env python3
"""Generate deterministic synthetic data for the public dashboard demo."""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "output" / "market_lag_dashboard" / "demo"
MODEL_IDS = ("prior", "elastic_net", "gradient_boosting", "robust_trend")
MODEL_LABELS = {
    "prior": "市场先验",
    "elastic_net": "正则逻辑回归",
    "gradient_boosting": "梯度提升树",
    "robust_trend": "稳健趋势模型",
}


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def business_days(end: date, count: int) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return list(reversed(days))


def chart_rows(symbol: str, base: float, phase: float) -> list[dict[str, Any]]:
    seed = sum(ord(character) for character in symbol)
    randomizer = random.Random(seed)
    close = base
    rows: list[dict[str, Any]] = []
    for index, current in enumerate(business_days(date(2026, 8, 13), 120)):
        drift = 0.0007 + math.sin(index / 8 + phase) * 0.0024
        shock = randomizer.uniform(-0.012, 0.012)
        open_price = close * (1 + randomizer.uniform(-0.004, 0.004))
        close = max(1, open_price * (1 + drift + shock))
        high = max(open_price, close) * (1 + randomizer.uniform(0.002, 0.012))
        low = min(open_price, close) * (1 - randomizer.uniform(0.002, 0.012))
        rows.append(
            {
                "date": current.isoformat(),
                "open": round(open_price, 3),
                "high": round(high, 3),
                "low": round(low, 3),
                "close": round(close, 3),
                "volume": int(1_000_000 * (1.1 + randomizer.random() * 2.7)),
            }
        )
    return rows


def component_metrics(samples: int, offset: float) -> dict[str, dict[str, float | int]]:
    values: dict[str, dict[str, float | int]] = {}
    for index, model_id in enumerate(MODEL_IDS):
        brier = 0.244 - offset + index * 0.0027
        values[model_id] = {
            "samples": samples,
            "positive_rate": 0.51 + offset,
            "brier": round(brier, 6),
            "baseline_brier": round(0.248 - offset / 2, 6),
            "brier_skill": round((0.248 - offset / 2 - brier) / (0.248 - offset / 2), 6),
            "log_loss": round(0.684 + index * 0.006 - offset, 6),
            "calibration_error": round(0.024 + index * 0.009, 6),
        }
    return values


def validation(market: str) -> dict[str, Any]:
    samples = 2400 if market == "cn" else 1800
    offset = 0.003 if market == "cn" else 0.006
    return {
        "samples": samples,
        "positive_rate": 0.51 + offset,
        "brier": 0.242 - offset,
        "baseline_brier": 0.248,
        "brier_skill": 0.024 + offset,
        "log_loss": 0.681 - offset,
        "calibration_error": 0.031,
        "return_mae": 0.037,
        "return_baseline_mae": 0.039,
        "return_skill": 0.051,
        "empirical_interval_coverage": 0.79,
        "interval_target": 0.8,
        "folds": 4,
        "stacking_start": "2024-01-02",
        "stacking_end": "2024-12-31",
        "calibration_start": "2025-01-02",
        "calibration_end": "2025-06-30",
        "holdout_start": "2025-07-01",
        "holdout_end": "2026-06-30",
        "status": "合成演示，未作真实验收",
        "method": "演示结构：purged walk-forward + 独立校准 + 封存验收",
        "component_metrics": component_metrics(samples, offset),
        "weights": {
            "prior": 0.25,
            "elastic_net": 0.35,
            "gradient_boosting": 0.25,
            "robust_trend": 0.15,
        },
        "calibrator": {"intercept": -0.08, "coefficient": 0.92},
        "training_rows": samples * 4,
        "oof_rows": samples * 2,
        "next_review": "实时模式下每次刷新复评",
        "next_review_detail": "演示数据不会进入前向账本",
    }


def diagnostic(probability: float, quality: float, agreement: float) -> dict[str, Any]:
    model_evidence = 48.0
    data_score = round(quality * 15, 1)
    agreement_score = round(agreement * 15, 1)
    separation_score = round(min(abs(probability - 0.5) / 0.15, 1) * 5, 1)
    dimensions = [
        {
            "id": "model_evidence",
            "label": "样本外模型证据",
            "score": model_evidence,
            "max_score": 65.0,
            "components": [
                {"id": "direction_skill", "label": "方向增益", "value": 0.03, "score": 6.0, "max_score": 20.0},
                {"id": "return_skill", "label": "收益增益", "value": 0.05, "score": 5.0, "max_score": 10.0},
                {"id": "calibration", "label": "概率校准", "value": 0.031, "score": 13.8, "max_score": 20.0},
                {"id": "interval_coverage", "label": "区间覆盖", "value": 0.79, "score": 14.2, "max_score": 15.0},
            ],
        },
        {"id": "data_quality", "label": "数据完整性", "score": data_score, "max_score": 15.0, "value": quality},
        {"id": "ensemble_agreement", "label": "组件一致性", "score": agreement_score, "max_score": 15.0, "value": agreement},
        {"id": "signal_separation", "label": "信号区分度", "score": separation_score, "max_score": 5.0, "value": abs(probability - 0.5)},
    ]
    score = round(sum(float(item["score"]) for item in dimensions), 1)
    return {
        "formula_version": "linear-diagnostic-v1",
        "score": score,
        "max_score": 100.0,
        "note": "演示用四维线性诊断分；分数不等于上涨概率",
        "dimensions": dimensions,
    }


def forecast(
    *,
    market: str,
    symbol: str,
    name: str,
    sector: str,
    price: float,
    probability: float,
    expected_return: float,
    phase: float,
) -> dict[str, Any]:
    quality = 0.92 - phase * 0.015
    agreement = 0.86 - phase * 0.02
    breakdown = diagnostic(probability, quality, agreement)
    chart_path = DEMO_DIR / "charts" / market / f"{symbol}.json"
    rows = chart_rows(symbol, price * 0.86, phase)
    atomic_json(
        chart_path,
        {
            "kind": market,
            "symbol": symbol,
            "source": "deterministic synthetic demo",
            "period": "120 synthetic sessions",
            "start": rows[0]["date"],
            "end": rows[-1]["date"],
            "rows": rows,
        },
    )
    gaps = ["合成数据不具备真实市场证据", "未进入真实前向账本"]
    return {
        "market": market,
        "symbol": symbol,
        "name": name,
        "exchange": "DEMO-CN" if market == "cn" else "DEMO-US",
        "board": "演示板块",
        "board_key": "demo",
        "sector": sector,
        "as_of": "2026-08-13",
        "signal_cutoff": "合成数据",
        "entry_rule": "仅展示结构，不可交易",
        "created_at": "2026-08-14T09:00:00+08:00",
        "prediction_time": "2026-08-14T09:00:00+08:00",
        "target_start_estimate": "2026-08-14",
        "target_end_estimate": "2026-08-20",
        "signal_available_at": "2026-08-13T15:00:00+08:00",
        "entry_deadline": "2026-08-14T09:30:00+08:00",
        "calendar_basis": "synthetic weekday demo",
        "forward_eligible": False,
        "entry_window_status": "demo",
        "horizon": 5,
        "current_price": round(rows[-1]["close"], 3),
        "probability_up": probability,
        "raw_probability_up": round(probability + 0.012, 6),
        "expected_return": expected_return,
        "quantiles": {
            "q10": expected_return - 0.055,
            "q50": expected_return,
            "q90": expected_return + 0.061,
            "label": "合成演示区间",
            "target_coverage": 0.8,
        },
        "reliability_score": breakdown["score"],
        "reliability_grade": "DEMO",
        "diagnostic_score_breakdown": breakdown,
        "evidence_gaps": gaps,
        "execution_restrictions": ["合成演示数据禁止用于交易"],
        "decision_status": "暂缓",
        "abstain_reasons": gaps + ["合成演示数据禁止用于交易"],
        "sample_count": 0,
        "data_quality": quality,
        "ensemble_agreement": agreement,
        "component_predictions": [
            {
                "id": model_id,
                "label": MODEL_LABELS[model_id],
                "probability": round(probability + (index - 1.5) * 0.008, 6),
                "weight": weight,
            }
            for index, (model_id, weight) in enumerate(
                zip(MODEL_IDS, (0.25, 0.35, 0.25, 0.15))
            )
        ],
        "factor_contributions": [
            {"name": "相对强弱", "value": 0.18 - phase * 0.01},
            {"name": "20日动量", "value": 0.12 + phase * 0.01},
            {"name": "量能变化", "value": 0.07},
            {"name": "波动约束", "value": -0.05},
        ],
        "validation": {
            "brier": 0.242,
            "baseline_brier": 0.248,
            "brier_skill": 0.024,
            "return_skill": 0.051,
            "calibration_error": 0.031,
            "empirical_interval_coverage": 0.79,
            "holdout_start": "2025-07-01",
            "holdout_end": "2026-06-30",
        },
        "chart_ref": f"./demo/charts/{market}/{symbol}.json",
    }


def build_payload() -> dict[str, Any]:
    definitions = {
        "cn": [
            ("CN-DEMO-01", "演示公司甲", "光通信", 24.0, 0.61, 0.024),
            ("CN-DEMO-02", "演示公司乙", "存储", 18.0, 0.57, 0.015),
            ("CN-DEMO-03", "演示公司丙", "消费", 13.0, 0.53, 0.007),
            ("CN-DEMO-04", "演示公司丁", "银行", 9.0, 0.49, -0.003),
        ],
        "us": [
            ("US-DEMO-01", "Demo Systems", "Optical Interconnect", 82.0, 0.59, 0.019),
            ("US-DEMO-02", "Demo Memory", "Memory", 64.0, 0.56, 0.012),
            ("US-DEMO-03", "Demo Retail", "Consumer", 47.0, 0.52, 0.005),
            ("US-DEMO-04", "Demo Bank", "Banking", 38.0, 0.48, -0.004),
        ],
    }
    markets: dict[str, Any] = {}
    for market, items in definitions.items():
        forecasts = [
            forecast(
                market=market,
                symbol=symbol,
                name=name,
                sector=sector,
                price=price,
                probability=probability,
                expected_return=expected_return,
                phase=float(index),
            )
            for index, (symbol, name, sector, price, probability, expected_return) in enumerate(items)
        ]
        markets[market] = {
            "label": "A股预测" if market == "cn" else "美股预测",
            "theme": "light-warm" if market == "cn" else "dark-cool",
            "session": {
                "timezone": "Asia/Shanghai" if market == "cn" else "America/New_York",
                "signal_cutoff": "合成演示",
                "entry_rule": "不可交易",
                "data_as_of": "2026-08-13",
                "primary_horizon": 5,
                "round_trip_cost": 0.0035 if market == "cn" else 0.0015,
                "benchmark": "DEMO",
            },
            "universe": {
                "policy": "公开仓库的合成演示池",
                "eligible_count": len(forecasts),
                "evaluated_count": len(forecasts),
                "boards_present": ["DEMO"],
                "rejected_data_count": 0,
            },
            "validation": validation(market),
            "forecasts": forecasts,
            "forward_validation": {
                "total": 0,
                "legacy_unverifiable": 0,
                "pending": 0,
                "resolved": 0,
                "void": 0,
                "accepted_resolved": 0,
                "brier": None,
                "accepted_brier": None,
                "interval_coverage": None,
            },
        }
    return {
        "schema_version": "1.0",
        "generated_at": "2026-08-14 09:00:00 CST",
        "source_snapshot_hash": "synthetic-demo-v1",
        "demo_mode": True,
        "model": {
            "version": "public-demo-v1",
            "universe_version": "synthetic-universe-v1",
            "primary_horizon": 5,
            "framework": "双市场独立面板 + 非负凸集成 + sigmoid校准 + 经验残差区间",
            "components": [
                {"id": model_id, "label": MODEL_LABELS[model_id]}
                for model_id in MODEL_IDS
            ],
            "boundaries": [
                "演示数据全部为确定性合成数据",
                "真实模式固定5个交易日为生产终点",
                "证据不足时必须拒绝输出买入型结论",
            ],
        },
        "markets": markets,
        "improvement": {
            "review_cadence": "真实模式下每次刷新结算到期样本并复评",
            "next_review": "合成演示不复评",
        },
        "collection_status": {"mode": "synthetic_demo", "personal_data": False},
        "disclaimer": "Synthetic demonstration only. Not investment advice.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify committed demo data is reproducible")
    args = parser.parse_args()
    target = DEMO_DIR / "forecasts-v1.json"
    before = target.read_text(encoding="utf-8") if target.exists() else None
    atomic_json(target, build_payload())
    after = target.read_text(encoding="utf-8")
    if args.check and before is not None and before != after:
        raise SystemExit("demo data was not reproducible")
    print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
