from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path, PurePath
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .features import (
    FEATURE_COLUMNS,
    build_feature_frame,
    chart_frame,
    frame_data_quality,
    load_chart_rows,
)
from .collectors import ensure_histories, ensure_us_chart
from .ledger import update_forward_ledger
from .market_specs import CN_SPEC, US_SPEC, MarketSpec, classify_cn_board
from .models import MODEL_IDS, MODEL_LABELS, build_market_model
from path_safety import resolve_within


MODEL_VERSION = "v1.0.6-dual-market-stacked-20260729"
SCHEMA_VERSION = "1.0"
UNIVERSE_VERSION = "forecast-universe-v1-20260728"
SH_TZ = ZoneInfo("Asia/Shanghai")


CORE_CN: tuple[dict[str, str], ...] = (
    {"symbol": "600519", "name": "贵州茅台", "sector": "食品饮料"},
    {"symbol": "000858", "name": "五粮液", "sector": "食品饮料"},
    {"symbol": "300750", "name": "宁德时代", "sector": "新能源电池"},
    {"symbol": "301269", "name": "华大九天", "sector": "工业软件"},
    {"symbol": "688981", "name": "中芯国际", "sector": "半导体"},
    {"symbol": "688256", "name": "寒武纪", "sector": "AI芯片"},
    {"symbol": "920001", "name": "纬达光电", "sector": "光学材料"},
)

CORE_US: tuple[dict[str, str], ...] = (
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "sector": "ETF"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "sector": "ETF"},
    {"symbol": "AAPL", "name": "Apple", "sector": "科技"},
    {"symbol": "MSFT", "name": "Microsoft", "sector": "软件"},
    {"symbol": "NVDA", "name": "NVIDIA", "sector": "半导体"},
    {"symbol": "AMZN", "name": "Amazon", "sector": "可选消费"},
    {"symbol": "JPM", "name": "JPMorgan Chase", "sector": "金融"},
)


def _next_weekday(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _forward_window(
    spec: MarketSpec,
    *,
    as_of: str,
    generated_at: datetime,
) -> dict[str, Any]:
    signal_date = datetime.strptime(as_of, "%Y-%m-%d").date()
    entry_date = _next_weekday(signal_date)
    target_dates = [entry_date]
    while len(target_dates) < spec.primary_horizon:
        target_dates.append(_next_weekday(target_dates[-1]))
    local_timezone = ZoneInfo(spec.timezone)
    local_created = generated_at.astimezone(local_timezone)
    signal_close = time(15, 0) if spec.market == "cn" else time(16, 0)
    signal_available_at = datetime.combine(
        signal_date,
        signal_close,
        tzinfo=local_timezone,
    )
    entry_deadline = datetime.combine(entry_date, time(9, 30), tzinfo=local_timezone)
    eligible = signal_available_at <= local_created < entry_deadline
    if local_created < signal_available_at:
        window_status = "signal_incomplete"
    elif local_created >= entry_deadline:
        window_status = "missed"
    else:
        window_status = "open"
    return {
        "created_at": generated_at.isoformat(),
        "prediction_time": local_created.isoformat(),
        "target_start_estimate": entry_date.isoformat(),
        "target_end_estimate": target_dates[-1].isoformat(),
        "signal_available_at": signal_available_at.isoformat(),
        "entry_deadline": entry_deadline.isoformat(),
        "calendar_basis": "weekday estimate; settlement uses observed benchmark sessions",
        "forward_eligible": eligible,
        "entry_window_status": window_status,
    }


def _completed_benchmark_rows(
    rows: list[dict[str, Any]],
    *,
    spec: MarketSpec,
    generated_at: datetime,
) -> list[dict[str, Any]]:
    local_created = generated_at.astimezone(ZoneInfo(spec.timezone))
    signal_close = time(15, 0) if spec.market == "cn" else time(16, 0)
    completed: list[dict[str, Any]] = []
    for row in rows:
        parsed = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(parsed):
            continue
        available_at = datetime.combine(
            parsed.date(),
            signal_close,
            tzinfo=ZoneInfo(spec.timezone),
        )
        if available_at <= local_created:
            completed.append(row)
    return completed


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _clean_ref(value: str, *, market: str, symbol: str) -> str:
    ref = str(value or "").strip()
    if ref:
        normalized = ref if ref.startswith("./") else f"./{ref.lstrip('/')}"
        if normalized.startswith("./data/charts/") and ".." not in PurePath(normalized).parts:
            return normalized
    return f"./data/charts/{market}/{symbol}.json"


def _resolve_output_ref(root: Path, ref: str) -> Path:
    return resolve_within(root / "output/market_lag_dashboard", ref)


def _source_snapshot_hash(
    *,
    root: Path,
    dashboard: dict[str, Any],
    universe: dict[str, list[dict[str, Any]]],
) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {
                "model": MODEL_VERSION,
                "schema": SCHEMA_VERSION,
                "market_specs": [CN_SPEC.to_dict(), US_SPEC.to_dict()],
                "universe_version": UNIVERSE_VERSION,
                "universe": {
                    market: universe[market]
                    for market in ("cn", "us")
                },
            },
            sort_keys=True,
        ).encode("utf-8")
    )
    refs = {
        CN_SPEC.benchmark_chart_ref,
        US_SPEC.benchmark_chart_ref,
        *(
            str(item.get("chart_ref") or "")
            for market in ("cn", "us")
            for item in universe[market]
        ),
    }
    for ref in sorted(refs):
        path = _resolve_output_ref(root, ref)
        digest.update(ref.encode("utf-8"))
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:20]


def _dashboard_universe(dashboard: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    cn: dict[str, dict[str, Any]] = {}
    us: dict[str, dict[str, Any]] = {}
    sector_memberships: dict[str, set[str]] = defaultdict(set)
    for concept in dashboard.get("concepts", []):
        sector = str(concept.get("short_name") or concept.get("name") or "未分类")
        for company in ((concept.get("cn") or {}).get("companies") or []):
            symbol = str(company.get("code") or "")
            if not symbol:
                continue
            instrument = classify_cn_board(symbol)
            if not instrument.eligible:
                continue
            sector_memberships[symbol].add(sector)
            current = cn.setdefault(
                symbol,
                {
                    "market": "cn",
                    "symbol": symbol,
                    "name": str(company.get("name") or symbol),
                    "sector": sector,
                    "exchange": instrument.exchange,
                    "board": instrument.board,
                    "board_key": instrument.board_key,
                    "chart_ref": _clean_ref(company.get("chart_ref"), market="cn", symbol=symbol),
                },
            )
            if current.get("sector") == "未分类":
                current["sector"] = sector
        for ticker in ((concept.get("us") or {}).get("tickers") or []):
            symbol = str(ticker.get("symbol") or "").upper()
            if not symbol:
                continue
            sector_memberships[f"us:{symbol}"].add(sector)
            us.setdefault(
                symbol,
                {
                    "market": "us",
                    "symbol": symbol,
                    "name": str(ticker.get("name") or symbol),
                    "sector": sector,
                    "exchange": "US",
                    "board": str(ticker.get("exchange") or "US"),
                    "board_key": "us",
                    "chart_ref": _clean_ref(ticker.get("chart_ref"), market="us", symbol=symbol),
                },
            )
    for symbol, item in cn.items():
        memberships = sorted(sector_memberships.get(symbol) or [])
        if memberships:
            item["sector"] = " / ".join(memberships[:2])
    for symbol, item in us.items():
        memberships = sorted(sector_memberships.get(f"us:{symbol}") or [])
        if memberships:
            item["sector"] = " / ".join(memberships[:2])
    return {"cn": list(cn.values()), "us": list(us.values())}


def _merge_core(
    universe: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    for market, core in (("cn", CORE_CN), ("us", CORE_US)):
        by_symbol = {str(item.get("symbol") or ""): item for item in universe[market]}
        for seed in core:
            symbol = seed["symbol"]
            if symbol in by_symbol:
                if by_symbol[symbol].get("name") == symbol:
                    by_symbol[symbol]["name"] = seed["name"]
                continue
            if market == "cn":
                instrument = classify_cn_board(symbol)
                by_symbol[symbol] = {
                    "market": "cn",
                    **seed,
                    "exchange": instrument.exchange,
                    "board": instrument.board,
                    "board_key": instrument.board_key,
                    "chart_ref": f"./data/charts/cn/{symbol}.json",
                }
            else:
                by_symbol[symbol] = {
                    "market": "us",
                    **seed,
                    "exchange": "US",
                    "board": "US",
                    "board_key": "us",
                    "chart_ref": f"./data/charts/us/{symbol}.json",
                }
        universe[market] = sorted(by_symbol.values(), key=lambda item: item["symbol"])
    return universe


def _load_forecast_universe(
    root: Path,
    dashboard: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    frozen = _load_json(root / "config" / "forecast_universe_v1.json", {})
    markets = frozen.get("markets") if isinstance(frozen, dict) else None
    if isinstance(markets, dict) and all(
        isinstance(markets.get(market), list) and markets[market]
        for market in ("cn", "us")
    ):
        return {
            market: [dict(item) for item in markets[market]]
            for market in ("cn", "us")
        }
    return _merge_core(_dashboard_universe(dashboard))


def _load_market_frames(
    *,
    root: Path,
    instruments: list[dict[str, Any]],
    spec: MarketSpec,
    generated_at: datetime,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    benchmark_rows = _completed_benchmark_rows(
        load_chart_rows(_resolve_output_ref(root, spec.benchmark_chart_ref)),
        spec=spec,
        generated_at=generated_at,
    )
    benchmark_frame = chart_frame(benchmark_rows)
    if benchmark_frame.empty:
        raise RuntimeError(f"{spec.market}: benchmark calendar unavailable")
    reference_date = pd.Timestamp(benchmark_frame.iloc[-1]["date"]).normalize()
    eligible: list[tuple[dict[str, Any], pd.DataFrame, float]] = []
    rejected: list[dict[str, Any]] = []
    for instrument in instruments:
        chart_ref = str(instrument.get("chart_ref") or "")
        rows = load_chart_rows(_resolve_output_ref(root, chart_ref))
        frame = build_feature_frame(
            rows,
            benchmark_rows,
            spec,
            board_key=str(instrument.get("board_key") or "us"),
        )
        if frame.empty:
            rejected.append(
                {
                    "symbol": instrument.get("symbol"),
                    "name": instrument.get("name"),
                    "reason": "历史数据不足或缺失真实OHLCV",
                }
            )
            continue
        quality = frame_data_quality(frame, reference_date=reference_date)
        eligible.append((instrument, frame, quality))

    eligible.sort(key=lambda item: (item[2], len(item[1])), reverse=True)
    eligible = eligible[: spec.max_assets]
    training_parts: list[pd.DataFrame] = []
    current_parts: list[pd.DataFrame] = []
    metadata: list[dict[str, Any]] = []
    for instrument, frame, quality in eligible:
        training = frame.dropna(subset=FEATURE_COLUMNS + ["target_up", "target_return"]).copy()
        training = training.tail(900)
        current = frame.dropna(subset=FEATURE_COLUMNS).tail(1).copy()
        if len(training) < spec.minimum_history - spec.primary_horizon or current.empty:
            rejected.append(
                {
                    "symbol": instrument.get("symbol"),
                    "name": instrument.get("name"),
                    "reason": "可用于训练的完整样本不足",
                }
            )
            continue
        training["symbol"] = instrument["symbol"]
        training_parts.append(training)
        current_date = pd.Timestamp(current.iloc[-1]["date"]).normalize()
        if current_date != reference_date:
            rejected.append(
                {
                    "symbol": instrument.get("symbol"),
                    "name": instrument.get("name"),
                    "reason": (
                        f"行情截至 {current_date.strftime('%Y-%m-%d')}，"
                        f"未对齐市场截面 {reference_date.strftime('%Y-%m-%d')}"
                    ),
                }
            )
            continue
        current["symbol"] = instrument["symbol"]
        current_parts.append(current)
        metadata.append(
            {
                **instrument,
                "data_quality": quality,
                "history_rows": len(frame),
                "training_rows": len(training),
                "as_of": reference_date.strftime("%Y-%m-%d"),
                "current_price": float(current.iloc[-1]["current_price"]),
            }
        )
    if not training_parts or not current_parts:
        raise RuntimeError(f"{spec.market}: no usable instruments")
    training_frame = pd.concat(training_parts, ignore_index=True).sort_values(
        ["date", "symbol"]
    ).reset_index(drop=True)
    current_frame = pd.concat(current_parts, ignore_index=True).sort_values("symbol").reset_index(
        drop=True
    )
    metadata_by_symbol = {item["symbol"]: item for item in metadata}
    current_metadata = [metadata_by_symbol[symbol] for symbol in current_frame["symbol"]]
    return training_frame, current_frame, current_metadata, rejected


def _reliability(
    *,
    validation: dict[str, Any],
    data_quality: float,
    agreement: float,
    probability: float,
) -> tuple[float, str, list[str], dict[str, Any]]:
    reasons: list[str] = []
    brier_skill = float(validation.get("brier_skill") or 0)
    return_skill = float(validation.get("return_skill") or 0)
    calibration_error = float(validation.get("calibration_error") or 1)
    coverage = float(validation.get("empirical_interval_coverage") or 0)
    samples = int(validation.get("samples") or 0)
    if brier_skill <= 0:
        reasons.append("样本外Brier未优于市场先验")
    if return_skill <= 0:
        reasons.append("收益预测未优于历史中位数")
    if calibration_error > 0.06:
        reasons.append("概率校准误差偏高")
    if not 0.70 <= coverage <= 0.90:
        reasons.append("经验区间覆盖偏离目标")
    if agreement < 0.62:
        reasons.append("组件模型分歧较大")
    if data_quality < 0.78:
        reasons.append("数据质量不足")
    if 0.47 <= probability <= 0.53:
        reasons.append("方向概率接近随机基准")
    if samples < 500:
        reasons.append("独立样本外数量不足")
    direction_score = round(max(0.0, min(1.0, brier_skill / 0.10)) * 20, 1)
    return_score = round(max(0.0, min(1.0, return_skill / 0.10)) * 10, 1)
    calibration_score = round(max(0.0, 1 - calibration_error / 0.10) * 20, 1)
    coverage_score = round(
        max(0.0, 1 - abs(coverage - 0.80) / 0.20) * 15,
        1,
    )
    data_score = round(data_quality * 15, 1)
    agreement_score = round(agreement * 15, 1)
    signal_score = round(min(abs(probability - 0.5) / 0.15, 1.0) * 5, 1)
    model_evidence_score = round(
        direction_score + return_score + calibration_score + coverage_score,
        1,
    )
    score = round(
        model_evidence_score + data_score + agreement_score + signal_score,
        1,
    )
    grade = "A" if score >= 82 else "B" if score >= 70 else "C" if score >= 58 else "D"
    breakdown = {
        "formula_version": "linear-diagnostic-v1",
        "score": score,
        "max_score": 100.0,
        "note": "四维线性综合诊断分；模型证据、数据、组件一致性和信号区分度须分开判断",
        "dimensions": [
            {
                "id": "model_evidence",
                "label": "样本外模型证据",
                "score": model_evidence_score,
                "max_score": 65.0,
                "components": [
                    {
                        "id": "direction_skill",
                        "label": "方向增益",
                        "value": round(brier_skill, 6),
                        "score": direction_score,
                        "max_score": 20.0,
                    },
                    {
                        "id": "return_skill",
                        "label": "收益增益",
                        "value": round(return_skill, 6),
                        "score": return_score,
                        "max_score": 10.0,
                    },
                    {
                        "id": "calibration",
                        "label": "概率校准",
                        "value": round(calibration_error, 6),
                        "score": calibration_score,
                        "max_score": 20.0,
                    },
                    {
                        "id": "interval_coverage",
                        "label": "区间覆盖",
                        "value": round(coverage, 6),
                        "score": coverage_score,
                        "max_score": 15.0,
                    },
                ],
            },
            {
                "id": "data_quality",
                "label": "数据完整性",
                "score": data_score,
                "max_score": 15.0,
                "value": round(data_quality, 6),
            },
            {
                "id": "ensemble_agreement",
                "label": "组件一致性",
                "score": agreement_score,
                "max_score": 15.0,
                "value": round(agreement, 6),
            },
            {
                "id": "signal_separation",
                "label": "信号区分度",
                "score": signal_score,
                "max_score": 5.0,
                "value": round(abs(probability - 0.5), 6),
            },
        ],
    }
    return score, grade, reasons, breakdown


def _factor_contributions(row: pd.Series) -> list[dict[str, Any]]:
    definitions = [
        ("20日动量", float(row["ret_20"]) * 1.8),
        ("相对强弱", float(row["relative_20"]) * 1.8),
        ("5日动量", float(row["ret_5"]) * 1.2),
        ("量能变化", float(row["volume_ratio_20"]) * 0.06),
        ("波动约束", -float(row["vol_20"]) * 2.2),
        ("市场状态", (float(row["regime_up"]) - 0.5) * 0.08),
        ("回撤位置", float(row["drawdown_60"]) * 0.7),
    ]
    return [
        {"name": name, "value": round(max(-0.25, min(0.25, value)), 4)}
        for name, value in sorted(definitions, key=lambda item: abs(item[1]), reverse=True)
    ]


def _forecast_payloads(
    *,
    spec: MarketSpec,
    current: pd.DataFrame,
    metadata: list[dict[str, Any]],
    model: dict[str, Any],
    generated_at: datetime,
) -> list[dict[str, Any]]:
    forecasts: list[dict[str, Any]] = []
    components = model["current_component_probabilities"]
    validation = model["validation"]
    weights = model["weights"]
    for index, (row_tuple, meta) in enumerate(zip(current.itertuples(index=False), metadata, strict=False)):
        row = current.iloc[index]
        probabilities = [float(components[model_id][index]) for model_id in MODEL_IDS]
        agreement = max(0.0, 1 - float(np.std(probabilities)) / 0.18)
        probability = float(model["current_probability"][index])
        score, grade, evidence_gaps, readiness = _reliability(
            validation=validation,
            data_quality=float(meta["data_quality"]),
            agreement=agreement,
            probability=probability,
        )
        execution_restrictions: list[str] = []
        forward_window = _forward_window(
            spec,
            as_of=meta["as_of"],
            generated_at=generated_at,
        )
        if not forward_window["forward_eligible"]:
            execution_restrictions.append(
                "信号日尚未收盘；当前结果不进入前向账本"
                if forward_window["entry_window_status"] == "signal_incomplete"
                else "下一开盘冻结窗口已过；当前结果不进入前向账本"
            )
        reasons = [*evidence_gaps, *execution_restrictions]
        q10 = float(model["q10"][index])
        q50 = float(model["q50"][index])
        q90 = float(model["q90"][index])
        quantiles = sorted([q10, q50, q90])
        forecasts.append(
            {
                "market": spec.market,
                "symbol": meta["symbol"],
                "name": meta["name"],
                "exchange": meta["exchange"],
                "board": meta["board"],
                "board_key": meta.get("board_key"),
                "sector": meta["sector"],
                "as_of": meta["as_of"],
                "signal_cutoff": f"{meta['as_of']} 收盘后",
                "entry_rule": (
                    "下一交易日开盘；缺失真实开盘价则作废"
                    if forward_window["forward_eligible"]
                    else "下一开盘窗口已过；收盘后刷新后再冻结"
                ),
                **forward_window,
                "horizon": spec.primary_horizon,
                "current_price": round(float(meta["current_price"]), 4),
                "probability_up": round(probability, 6),
                "raw_probability_up": round(float(model["current_raw_probability"][index]), 6),
                "expected_return": round(float(model["expected_return"][index]), 6),
                "quantiles": {
                    "q10": round(quantiles[0], 6),
                    "q50": round(quantiles[1], 6),
                    "q90": round(quantiles[2], 6),
                    "label": "独立校准段残差区间，封存验收段评估覆盖",
                    "target_coverage": 0.80,
                },
                "reliability_score": score,
                "reliability_grade": grade,
                "diagnostic_score_breakdown": readiness,
                "evidence_gaps": evidence_gaps,
                "execution_restrictions": execution_restrictions,
                "decision_status": "可研究" if not reasons else "暂缓",
                "abstain_reasons": reasons,
                "sample_count": int(validation.get("samples") or 0),
                "data_quality": round(float(meta["data_quality"]), 4),
                "ensemble_agreement": round(agreement, 4),
                "component_predictions": [
                    {
                        "id": model_id,
                        "label": MODEL_LABELS[model_id],
                        "probability": round(float(components[model_id][index]), 6),
                        "weight": round(float(weights.get(model_id, 0)), 6),
                    }
                    for model_id in MODEL_IDS
                ],
                "factor_contributions": _factor_contributions(row),
                "validation": {
                    "brier": validation.get("brier"),
                    "baseline_brier": validation.get("baseline_brier"),
                    "brier_skill": validation.get("brier_skill"),
                    "return_skill": validation.get("return_skill"),
                    "calibration_error": validation.get("calibration_error"),
                    "empirical_interval_coverage": validation.get(
                        "empirical_interval_coverage"
                    ),
                    "holdout_start": validation.get("holdout_start"),
                    "holdout_end": validation.get("holdout_end"),
                },
                "chart_ref": meta["chart_ref"],
            }
        )
    return sorted(
        forecasts,
        key=lambda item: (
            item["decision_status"] == "可研究",
            item["reliability_score"],
            abs(item["probability_up"] - 0.5),
        ),
        reverse=True,
    )


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, (np.floating, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def build_dual_market_forecasts(root: Path) -> dict[str, Any]:
    data_dir = root / "output/market_lag_dashboard/data"
    dashboard = _load_json(data_dir / "dashboard.json", {})
    universe = _load_forecast_universe(root, dashboard)
    market_payloads: dict[str, Any] = {}
    forecasts_by_market: dict[str, list[dict[str, Any]]] = {}
    generated_at = datetime.now(SH_TZ)
    ensure_us_chart(
        root,
        US_SPEC.benchmark_symbol,
        minimum_rows=US_SPEC.minimum_history,
        force_refresh=True,
    )
    required_end_by_market: dict[str, str] = {}
    for spec in (CN_SPEC, US_SPEC):
        benchmark_rows = _completed_benchmark_rows(
            load_chart_rows(_resolve_output_ref(root, spec.benchmark_chart_ref)),
            spec=spec,
            generated_at=generated_at,
        )
        benchmark_frame = chart_frame(benchmark_rows)
        if benchmark_frame.empty:
            raise RuntimeError(f"{spec.market}: benchmark calendar unavailable")
        required_end_by_market[spec.market] = (
            pd.Timestamp(benchmark_frame.iloc[-1]["date"]).normalize().strftime("%Y-%m-%d")
        )
    collection_status = ensure_histories(
        root,
        cn_symbols=[item["symbol"] for item in universe["cn"]],
        us_symbols=[item["symbol"] for item in universe["us"]],
        cn_required_end=required_end_by_market["cn"],
        us_required_end=required_end_by_market["us"],
    )
    source_hash = _source_snapshot_hash(
        root=root,
        dashboard=dashboard,
        universe=universe,
    )

    for spec in (CN_SPEC, US_SPEC):
        training, current, metadata, rejected = _load_market_frames(
            root=root,
            instruments=universe[spec.market],
            spec=spec,
            generated_at=generated_at,
        )
        model = build_market_model(training, current, spec)
        forecasts = _forecast_payloads(
            spec=spec,
            current=current,
            metadata=metadata,
            model=model,
            generated_at=generated_at,
        )
        forecasts_by_market[spec.market] = forecasts
        boards = sorted({item["board"] for item in forecasts})
        market_payloads[spec.market] = {
            "label": spec.label,
            "theme": "light-warm" if spec.market == "cn" else "dark-cool",
            "session": {
                "timezone": spec.timezone,
                "signal_cutoff": "当日收盘后",
                "entry_rule": "下一交易日开盘",
                "data_as_of": forecasts[0]["as_of"] if forecasts else None,
                "primary_horizon": spec.primary_horizon,
                "round_trip_cost": spec.round_trip_cost,
                "benchmark": spec.benchmark_symbol,
            },
            "universe": {
                "policy": (
                    "沪深京主板、创业板、科创板、北交所均有资格；当前候选池按可用点时数据评估"
                    if spec.market == "cn"
                    else "美国主要交易所普通股与ETF；按数据质量和流动性评估"
                ),
                "eligible_count": len(universe[spec.market]),
                "evaluated_count": len(forecasts),
                "boards_present": boards,
                "rejected_data_count": len(rejected),
                "rejected_data_examples": rejected[:12],
            },
            "validation": {
                **model["validation"],
                "status": (
                    "通过初步双指标验收"
                    if (
                        float(model["validation"].get("brier_skill") or 0) >= 0.01
                        and float(model["validation"].get("return_skill") or 0) > 0
                        and float(model["validation"].get("calibration_error") or 1) <= 0.06
                    )
                    else "暂未通过双指标验收"
                ),
                "method": "按日期分组的 purged walk-forward；早段定权、中段独立校准、晚段封存验收",
                "component_metrics": model["component_metrics"],
                "weights": model["weights"],
                "calibrator": model["calibrator"],
                "training_rows": model["training_rows"],
                "oof_rows": model["oof_rows"],
            },
            "forecasts": forecasts,
        }

    cn_boards = {item.get("board") for item in forecasts_by_market.get("cn", [])}
    required_cn_boards = {"沪市主板", "深市主板", "创业板", "科创板", "北交所"}
    missing_cn_boards = required_cn_boards - cn_boards
    if missing_cn_boards:
        raise RuntimeError(
            f"A-share board coverage missing before ledger publish: {sorted(missing_cn_boards)}"
        )

    ledger_summary = update_forward_ledger(
        root=root,
        ledger_path=data_dir / "forecast-forward-ledger-v1.json",
        forecasts_by_market=forecasts_by_market,
        model_version=MODEL_VERSION,
        snapshot_hash=source_hash,
        round_trip_costs={"cn": CN_SPEC.round_trip_cost, "us": US_SPEC.round_trip_cost},
        write=False,
    )
    for market in ("cn", "us"):
        market_payloads[market]["forward_validation"] = ledger_summary[market]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S CST"),
        "source_snapshot_hash": source_hash,
        "model": {
            "version": MODEL_VERSION,
            "universe_version": UNIVERSE_VERSION,
            "primary_horizon": 5,
            "framework": "双市场独立 pooled panel + 非负凸集成 + sigmoid校准 + 经验残差区间",
            "components": [
                {"id": model_id, "label": MODEL_LABELS[model_id]} for model_id in MODEL_IDS
            ],
            "boundaries": [
                "A股与美股分别训练、定权、校准和维护残差库",
                "固定5个交易日为唯一生产终点；不事后挑选最佳周期",
                "没有样本外增益、数据质量不足或模型分歧时允许拒绝预测",
                "当前股票池仍受现有可用历史数据约束，持续前向账本是晋级依据",
                "首版候选池按版本冻结；调整候选池必须升级universe和模型版本",
            ],
        },
        "markets": market_payloads,
        "improvement": {
            "ledger_path": "./data/forecast-forward-ledger-v1.json",
            "review_cadence": "每次刷新先结算到期预测；每周复评，累计前向样本达到门槛后才允许模型晋级",
            "promotion_rule": "至少连续3个样本外窗口中方向与收益均优于基准，校准误差达标且经验区间覆盖稳定",
            "next_challengers": [
                "逐日冻结的全市场成分与退市样本",
                "点时公司行动与停牌状态",
                "按市场状态分层的自适应conformal残差库",
                "挑战模型仅在独立前向结果胜出后获得权重",
            ],
            "ledger_summary": ledger_summary,
        },
        "collection_status": collection_status,
        "disclaimer": "仅用于研究，不构成投资建议；概率和区间均存在不确定性。",
    }
    return _sanitize_json(payload)
