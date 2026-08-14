#!/usr/bin/env python3
from __future__ import annotations

import bisect
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from path_safety import resolve_within


HORIZONS = tuple(range(1, 11))
PRIMARY_HORIZON = 5
ROUND_TRIP_COST_PCT = 0.35
STRESS_COST_PCT = 0.65
EVENT_COOLDOWN_SESSIONS = 10
TRAIN_SHARE = 0.70
PRIOR_STRENGTH = 12.0
DECAY_HALF_LIFE_EVENTS = 36.0
RECENT_RELATIONSHIP_WINDOW = 12
RELATIONSHIP_EDGE_DROP_LIMIT = -0.12
FDR_THRESHOLD = 0.10
MIN_US_NAMES = 3
MIN_CN_NAMES = 2
MAX_CN_DAILY_MOVE_PCT = 15.5
MAX_ENTRY_GAP_PCT = 9.65

US_PROXY_WEIGHTS = {"QQQ": 0.45, "SOXX": 0.35, "IWM": 0.20}
CN_PROXY_WEIGHTS = {"000300.SS": 0.50, "000852.SS": 0.30, "399006.SZ": 0.20}
ROLE_WEIGHTS = {"leader": 1.25, "core_supplier": 1.0, "peripheral": 0.82, "speculative": 0.62}
_CHART_ROW_CACHE: dict[str, list[dict[str, Any]]] = {}
_PRICE_SERIES_CACHE: dict[str, "PriceSeries | None"] = {}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def average(values: list[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(usable) / len(usable) if usable else None


def quantile(values: list[float | None], q: float) -> float | None:
    usable = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    position = (len(usable) - 1) * clamp(q, 0, 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return usable[lower]
    return usable[lower] * (upper - position) + usable[upper] * (position - lower)


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1) * 100


def wilson_interval(successes: float, trials: float, z: float = 1.64) -> tuple[float | None, float | None]:
    if trials <= 0:
        return None, None
    proportion = clamp(successes / trials, 0, 1)
    z2 = z * z
    denominator = 1 + z2 / trials
    centre = proportion + z2 / (2 * trials)
    margin = z * math.sqrt((proportion * (1 - proportion) + z2 / (4 * trials)) / trials)
    return (
        clamp((centre - margin) / denominator, 0, 1),
        clamp((centre + margin) / denominator, 0, 1),
    )


def days_between(left: str, right: str) -> int:
    try:
        return (date.fromisoformat(right) - date.fromisoformat(left)).days
    except (TypeError, ValueError):
        return 999


@dataclass(frozen=True)
class PriceSeries:
    rows: tuple[dict[str, Any], ...]
    dates: tuple[str, ...]
    exact: dict[str, int]

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]] | None) -> "PriceSeries | None":
        by_date: dict[str, dict[str, Any]] = {}
        for raw in rows or []:
            row_date = str(raw.get("date") or "")[:10]
            close = safe_float(raw.get("close"))
            if not row_date or close is None or close <= 0:
                continue
            open_price = safe_float(raw.get("open")) or close
            high = safe_float(raw.get("high")) or max(open_price, close)
            low = safe_float(raw.get("low")) or min(open_price, close)
            by_date[row_date] = {
                "date": row_date,
                "open": open_price,
                "high": max(high, open_price, close),
                "low": min(low, open_price, close),
                "close": close,
                "volume": max(safe_float(raw.get("volume")) or 0, 0),
            }
        ordered = tuple(by_date[key] for key in sorted(by_date))
        if len(ordered) < 30:
            return None
        dates = tuple(str(row["date"]) for row in ordered)
        return cls(rows=ordered, dates=dates, exact={value: idx for idx, value in enumerate(dates)})

    def index_at_or_before(self, target: str) -> int | None:
        idx = bisect.bisect_right(self.dates, target) - 1
        return idx if idx >= 0 else None

    def index_after(self, target: str) -> int | None:
        idx = bisect.bisect_right(self.dates, target)
        return idx if idx < len(self.rows) else None


def chart_rows(item: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    chart_ref = str(item.get("chart_ref") or "")
    if chart_ref:
        try:
            path = resolve_within(output_dir, chart_ref)
        except ValueError:
            return item.get("candles") or item.get("spark") or []
        cache_key = str(path.resolve())
        if cache_key in _CHART_ROW_CACHE:
            return _CHART_ROW_CACHE[cache_key]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("rows") or []
            if rows:
                _CHART_ROW_CACHE[cache_key] = rows
                return rows
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return item.get("candles") or item.get("spark") or []


def build_series_map(items: list[dict[str, Any]], key: str, output_dir: Path) -> dict[str, PriceSeries]:
    result: dict[str, PriceSeries] = {}
    for item in items:
        identifier = str(item.get(key) or "")
        chart_ref = str(item.get("chart_ref") or "")
        try:
            cache_key = str(resolve_within(output_dir, chart_ref)) if chart_ref else f"{key}:{identifier}"
        except ValueError:
            cache_key = f"{key}:{identifier}:unsafe-ref"
        if cache_key not in _PRICE_SERIES_CACHE:
            _PRICE_SERIES_CACHE[cache_key] = PriceSeries.from_rows(chart_rows(item, output_dir))
        series = _PRICE_SERIES_CACHE[cache_key]
        if identifier and series is not None:
            result[identifier] = series
    return result


def return_at(series: PriceSeries, idx: int, lookback: int, cap: float | None = None) -> float | None:
    if idx < lookback:
        return None
    current = safe_float(series.rows[idx].get("close"))
    previous = safe_float(series.rows[idx - lookback].get("close"))
    value = pct_change(current, previous)
    if value is None:
        return None
    return clamp(value, -cap, cap) if cap is not None else value


def daily_volatility(series: PriceSeries, idx: int, window: int = 60) -> float | None:
    start = max(1, idx - window + 1)
    values = [return_at(series, current, 1, 30) for current in range(start, idx + 1)]
    usable = [value for value in values if value is not None]
    if len(usable) < 10:
        return None
    mean = sum(usable) / len(usable)
    variance = sum((value - mean) ** 2 for value in usable) / max(len(usable) - 1, 1)
    return math.sqrt(max(variance, 0))


def role_weight(item: dict[str, Any]) -> float:
    return ROLE_WEIGHTS.get(str(item.get("signal_role") or ""), 0.82)


def weighted_exact_return(
    items: list[dict[str, Any]],
    series_by_id: dict[str, PriceSeries],
    identifier_key: str,
    target_date: str,
    lookback: int,
) -> tuple[float | None, int]:
    weighted_sum = 0.0
    total_weight = 0.0
    coverage = 0
    cap = 35.0 if lookback == 1 else 70.0
    for item in items:
        identifier = str(item.get(identifier_key) or "")
        series = series_by_id.get(identifier)
        idx = series.exact.get(target_date) if series else None
        if series is None or idx is None:
            continue
        value = return_at(series, idx, lookback, cap)
        if value is None:
            continue
        weight = role_weight(item)
        weighted_sum += value * weight
        total_weight += weight
        coverage += 1
    return (weighted_sum / total_weight if total_weight else None), coverage


def proxy_return_exact(
    series_by_symbol: dict[str, PriceSeries],
    weights: dict[str, float],
    target_date: str,
    lookback: int,
) -> float | None:
    values: list[tuple[float, float]] = []
    cap = 20.0 if lookback == 1 else 45.0
    for symbol, weight in weights.items():
        series = series_by_symbol.get(symbol)
        idx = series.exact.get(target_date) if series else None
        if series is None or idx is None:
            continue
        value = return_at(series, idx, lookback, cap)
        if value is not None:
            values.append((value, weight))
    denominator = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / denominator if denominator else None


def proxy_return_at_or_before(
    series_by_symbol: dict[str, PriceSeries],
    weights: dict[str, float],
    target_date: str,
    lookback: int,
) -> tuple[float | None, str | None]:
    values: list[tuple[float, float, str]] = []
    for symbol, weight in weights.items():
        series = series_by_symbol.get(symbol)
        idx = series.index_at_or_before(target_date) if series else None
        if series is None or idx is None:
            continue
        value = return_at(series, idx, lookback, 45.0)
        if value is not None:
            values.append((value, weight, series.dates[idx]))
    denominator = sum(weight for _, weight, _ in values)
    weighted = sum(value * weight for value, weight, _ in values) / denominator if denominator else None
    latest_date = max((item[2] for item in values), default=None)
    return weighted, latest_date


def relative_volume(series: PriceSeries, idx: int, window: int = 20) -> float | None:
    if idx < 1:
        return None
    current = safe_float(series.rows[idx].get("volume"))
    history = [safe_float(row.get("volume")) for row in series.rows[max(0, idx - window) : idx]]
    usable = [value for value in history if value is not None and value > 0]
    if current is None or current <= 0 or len(usable) < 8:
        return None
    return current / (sum(usable) / len(usable))


def cn_market_regime(cn_proxy_series: dict[str, PriceSeries], target_date: str) -> tuple[str, float | None, float | None]:
    trend, proxy_date = proxy_return_at_or_before(cn_proxy_series, CN_PROXY_WEIGHTS, target_date, 20)
    primary = cn_proxy_series.get("000300.SS") or next(iter(cn_proxy_series.values()), None)
    idx = primary.index_at_or_before(proxy_date or target_date) if primary else None
    volatility = daily_volatility(primary, idx, 20) if primary is not None and idx is not None else None
    if trend is not None and trend >= 4:
        regime = "上行"
    elif trend is not None and trend <= -4:
        regime = "下行"
    else:
        regime = "震荡"
    if volatility is not None and volatility >= 2.0:
        regime += "高波动"
    return regime, trend, volatility


def cn_state(
    companies: list[dict[str, Any]],
    series_by_code: dict[str, PriceSeries],
    cn_proxy_series: dict[str, PriceSeries],
    signal_date: str,
) -> dict[str, Any]:
    changes: list[float] = []
    relative_volumes: list[float] = []
    available_dates: list[str] = []
    for company in companies:
        code = str(company.get("code") or "")
        series = series_by_code.get(code)
        idx = series.index_at_or_before(signal_date) if series else None
        if series is None or idx is None:
            continue
        value = return_at(series, idx, 1)
        if value is None or abs(value) > MAX_CN_DAILY_MOVE_PCT:
            continue
        changes.append(value)
        rv = relative_volume(series, idx)
        if rv is not None:
            relative_volumes.append(rv)
        available_dates.append(series.dates[idx])
    cn_raw = average(changes)
    cn_proxy, proxy_date = proxy_return_at_or_before(cn_proxy_series, CN_PROXY_WEIGHTS, signal_date, 1)
    cn_residual = (cn_raw - cn_proxy) if cn_raw is not None and cn_proxy is not None else cn_raw
    median_volume = quantile(relative_volumes, 0.5)
    regime, trend_20d, volatility_20d = cn_market_regime(cn_proxy_series, signal_date)
    overheat = bool((cn_raw is not None and cn_raw >= 4.5) or (median_volume is not None and median_volume >= 2.8))
    return {
        "cn_raw_1d": cn_raw,
        "cn_proxy_1d": cn_proxy,
        "cn_residual_1d": cn_residual,
        "cn_relative_volume": median_volume,
        "cn_coverage": len(changes),
        "cn_data_date": max(available_dates, default=proxy_date),
        "cn_regime": regime,
        "cn_trend_20d": trend_20d,
        "cn_volatility_20d": volatility_20d,
        "cn_overheat": overheat,
    }


def trigger_bucket(state: dict[str, Any]) -> str:
    z_score = safe_float(state.get("us_residual_z")) or 0
    lag_gap = safe_float(state.get("lag_gap_neutral")) or 0
    strength = "强冲击" if z_score >= 1.5 else "普通冲击"
    lag = "大滞后" if lag_gap >= 1.5 else "小滞后"
    return f"{strength}|{lag}|{state.get('cn_regime') or '未知'}"


def is_trigger(state: dict[str, Any]) -> bool:
    us_residual = safe_float(state.get("us_residual_1d"))
    z_score = safe_float(state.get("us_residual_z"))
    lag_gap = safe_float(state.get("lag_gap_neutral"))
    return bool(
        state.get("us_mapping_quality") in {"direct", "sector_proxy"}
        and us_residual is not None
        and z_score is not None
        and lag_gap is not None
        and us_residual >= 0.40
        and z_score >= 0.75
        and lag_gap > 0
        and int(state.get("us_coverage") or 0) >= MIN_US_NAMES
        and int(state.get("cn_coverage") or 0) >= MIN_CN_NAMES
        and not state.get("cn_overheat")
    )


def current_activation_score(state: dict[str, Any]) -> float:
    us_residual = max(safe_float(state.get("us_residual_1d")) or 0, 0)
    z_score = max(safe_float(state.get("us_residual_z")) or 0, 0)
    lag_gap = max(safe_float(state.get("lag_gap_neutral")) or 0, 0)
    volume = safe_float(state.get("cn_relative_volume"))
    volume_confirm = 8 if volume is not None and 0.8 <= volume <= 2.4 else 0
    overheat_penalty = 24 if state.get("cn_overheat") else 0
    coverage_penalty = 18 if int(state.get("us_coverage") or 0) < MIN_US_NAMES else 0
    return clamp(us_residual * 10 + z_score * 18 + lag_gap * 8 + volume_confirm - overheat_penalty - coverage_penalty, 0, 100)


def path_has_cn_adjustment(series: PriceSeries, start_idx: int, end_idx: int) -> bool:
    for idx in range(max(1, start_idx), min(end_idx + 1, len(series.rows))):
        value = return_at(series, idx, 1)
        if value is not None and abs(value) > MAX_CN_DAILY_MOVE_PCT:
            return True
    return False


def stock_signal_state(
    series: PriceSeries,
    cn_proxy_series: dict[str, PriceSeries],
    signal_date: str,
) -> dict[str, Any]:
    idx = series.index_at_or_before(signal_date)
    if idx is None:
        return {"available": False, "bucket": "未知"}
    stock_5d = return_at(series, idx, 5, 40.0)
    stock_20d = return_at(series, idx, 20, 80.0)
    benchmark_5d, _ = proxy_return_at_or_before(cn_proxy_series, CN_PROXY_WEIGHTS, signal_date, 5)
    residual_5d = stock_5d - benchmark_5d if stock_5d is not None and benchmark_5d is not None else stock_5d
    rv = relative_volume(series, idx)
    volatility = daily_volatility(series, idx, 20)
    if residual_5d is None:
        position = "位置未知"
    elif residual_5d <= -2.0:
        position = "相对滞后"
    elif residual_5d <= 3.5:
        position = "温和确认"
    else:
        position = "相对过热"
    liquidity = "量能确认" if rv is not None and rv >= 1.15 else "量能一般"
    risk_ok = not (
        residual_5d is None
        or position == "相对过热"
        or (volatility is not None and volatility >= 4.5)
    )
    return {
        "available": residual_5d is not None,
        "data_date": series.dates[idx],
        "stock_return_5d": stock_5d,
        "stock_return_20d": stock_20d,
        "benchmark_return_5d": benchmark_5d,
        "residual_5d": residual_5d,
        "relative_volume": rv,
        "daily_volatility_20d": volatility,
        "position_state": position,
        "liquidity_state": liquidity,
        "bucket": f"{position}|{liquidity}",
        "risk_ok": risk_ok,
    }


def benchmark_path_return(
    cn_proxy_series: dict[str, PriceSeries],
    entry_date: str,
    exit_date: str,
) -> float | None:
    values: list[tuple[float, float]] = []
    for symbol, weight in CN_PROXY_WEIGHTS.items():
        series = cn_proxy_series.get(symbol)
        entry_idx = series.exact.get(entry_date) if series else None
        exit_idx = series.exact.get(exit_date) if series else None
        if series is None or entry_idx is None or exit_idx is None:
            continue
        entry = safe_float(series.rows[entry_idx].get("open"))
        exit_price = safe_float(series.rows[exit_idx].get("close"))
        value = pct_change(exit_price, entry)
        if value is not None:
            values.append((value, weight))
    denominator = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / denominator if denominator else None


def stock_trade_paths(
    company: dict[str, Any],
    series: PriceSeries,
    cn_proxy_series: dict[str, PriceSeries],
    signal_date: str,
) -> dict[int, dict[str, Any]]:
    name = str(company.get("name") or "")
    if "ST" in name.upper():
        return {}
    entry_idx = series.index_after(signal_date)
    if entry_idx is None or entry_idx <= 0:
        return {}
    entry_row = series.rows[entry_idx]
    previous_close = safe_float(series.rows[entry_idx - 1].get("close"))
    entry_open = safe_float(entry_row.get("open"))
    volume = safe_float(entry_row.get("volume"))
    entry_gap = pct_change(entry_open, previous_close)
    if entry_open is None or previous_close is None or volume is None or volume <= 0:
        return {}
    if entry_gap is None or abs(entry_gap) >= MAX_ENTRY_GAP_PCT:
        return {}

    result: dict[int, dict[str, Any]] = {}
    for horizon in HORIZONS:
        exit_idx = entry_idx + horizon
        if exit_idx >= len(series.rows) or path_has_cn_adjustment(series, entry_idx, exit_idx):
            continue
        exit_row = series.rows[exit_idx]
        exit_close = safe_float(exit_row.get("close"))
        gross = pct_change(exit_close, entry_open)
        if gross is None:
            continue
        benchmark = benchmark_path_return(cn_proxy_series, str(entry_row["date"]), str(exit_row["date"]))
        net = gross - ROUND_TRIP_COST_PCT
        stress_net = gross - STRESS_COST_PCT
        excess = net - (benchmark or 0)
        path_rows = series.rows[entry_idx + 1 : exit_idx + 1]
        highs = [safe_float(row.get("high")) for row in path_rows]
        lows = [safe_float(row.get("low")) for row in path_rows]
        usable_highs = [value for value in highs if value is not None]
        usable_lows = [value for value in lows if value is not None]
        result[horizon] = {
            "signal_date": signal_date,
            "entry_date": entry_row["date"],
            "exit_date": exit_row["date"],
            "gross_return": gross,
            "net_return": net,
            "stress_net_return": stress_net,
            "benchmark_return": benchmark,
            "excess_return": excess,
            "profit_success": net > 0,
            "alpha_success": excess > 0,
            "mfe": pct_change(max(usable_highs), entry_open) if usable_highs else None,
            "mae": pct_change(min(usable_lows), entry_open) if usable_lows else None,
        }
    return result


def event_record(
    signal_state: dict[str, Any],
    horizon: int,
    stock_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(stock_rows) < MIN_CN_NAMES:
        return None
    net_values = [safe_float(row.get("net_return")) for row in stock_rows]
    stress_values = [safe_float(row.get("stress_net_return")) for row in stock_rows]
    excess_values = [safe_float(row.get("excess_return")) for row in stock_rows]
    net = average(net_values)
    stress_net = average(stress_values)
    excess = average(excess_values)
    if net is None or stress_net is None or excess is None:
        return None
    profit_breadth = len([value for value in net_values if value is not None and value > 0]) / len(net_values)
    alpha_breadth = len([value for value in excess_values if value is not None and value > 0]) / len(excess_values)
    return {
        **signal_state,
        "horizon": horizon,
        "entry_date": min(str(row.get("entry_date") or "") for row in stock_rows),
        "exit_date": max(str(row.get("exit_date") or "") for row in stock_rows),
        "net_return": net,
        "stress_net_return": stress_net,
        "excess_return": excess,
        "profit_breadth": profit_breadth,
        "alpha_breadth": alpha_breadth,
        "profit_success": net > 0 and profit_breadth >= 0.50,
        "alpha_success": excess > 0 and alpha_breadth >= 0.50,
        "mfe": average([safe_float(row.get("mfe")) for row in stock_rows]),
        "mae": average([safe_float(row.get("mae")) for row in stock_rows]),
        "stock_count": len(stock_rows),
    }


def calibration_error(predictions: list[float], labels: list[int], bins: int = 5) -> float | None:
    if not predictions:
        return None
    paired = sorted(zip(predictions, labels), key=lambda item: item[0])
    total = len(paired)
    error = 0.0
    for start in range(0, total, max(1, math.ceil(total / bins))):
        chunk = paired[start : start + max(1, math.ceil(total / bins))]
        if not chunk:
            continue
        predicted = sum(item[0] for item in chunk) / len(chunk)
        observed = sum(item[1] for item in chunk) / len(chunk)
        error += abs(predicted - observed) * len(chunk) / total
    return error


def conditioned_history(history: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    exact = [item for item in history if item.get("bucket") == bucket]
    if len(exact) < 5:
        regime = bucket.split("|")[-1]
        exact = [item for item in history if str(item.get("bucket") or "").endswith(regime)]
    return exact


def posterior_from_history(history: list[dict[str, Any]], bucket: str, label_key: str) -> tuple[float, int, float]:
    labels = [1 if item.get(label_key) else 0 for item in history]
    base = (sum(labels) + PRIOR_STRENGTH * 0.5) / (len(labels) + PRIOR_STRENGTH)
    exact = conditioned_history(history, bucket)
    if len(exact) < 5:
        return base, len(exact), base
    successes = sum(1 for item in exact if item.get(label_key))
    posterior = (successes + PRIOR_STRENGTH * base) / (len(exact) + PRIOR_STRENGTH)
    return posterior, len(exact), base


def decayed_posterior_from_history(
    history: list[dict[str, Any]],
    bucket: str,
    label_key: str,
) -> tuple[float, float, float]:
    ordered = sorted(history, key=lambda item: (str(item.get("signal_date") or ""), str(item.get("exit_date") or "")))

    def weighted_rate(rows: list[dict[str, Any]], prior_mean: float) -> tuple[float, float]:
        weights = [0.5 ** (age / DECAY_HALF_LIFE_EVENTS) for age in range(len(rows) - 1, -1, -1)]
        weight_sum = sum(weights)
        weighted_success = sum(weight * (1 if row.get(label_key) else 0) for row, weight in zip(rows, weights))
        posterior = (weighted_success + PRIOR_STRENGTH * prior_mean) / (weight_sum + PRIOR_STRENGTH)
        squared_weight_sum = sum(weight * weight for weight in weights)
        effective_samples = weight_sum * weight_sum / squared_weight_sum if squared_weight_sum > 0 else 0
        return posterior, effective_samples

    base, _ = weighted_rate(ordered, 0.5) if ordered else (0.5, 0)
    conditioned = conditioned_history(ordered, bucket)
    if len(conditioned) < 5:
        return base, float(len(conditioned)), base
    posterior, effective_samples = weighted_rate(conditioned, base)
    return posterior, effective_samples, base


def relationship_drift(
    history: list[dict[str, Any]],
    bucket: str,
    label_key: str,
    baseline_probability: float,
) -> dict[str, Any]:
    conditioned = conditioned_history(history, bucket)
    recent = conditioned[-RECENT_RELATIONSHIP_WINDOW:]
    older = conditioned[:-RECENT_RELATIONSHIP_WINDOW]
    if not recent:
        return {
            "recent_probability": None,
            "prior_probability": None,
            "recent_edge": None,
            "edge_change": None,
            "relationship_drift": False,
            "relationship_stability_score": 50.0,
        }

    recent_probability = (
        sum(1 for item in recent if item.get(label_key)) + 4 * baseline_probability
    ) / (len(recent) + 4)
    prior_probability = (
        (sum(1 for item in older if item.get(label_key)) + 8 * baseline_probability) / (len(older) + 8)
        if older
        else baseline_probability
    )
    recent_edge = recent_probability - baseline_probability
    edge_change = recent_probability - prior_probability
    drift = len(recent) >= 8 and (
        edge_change <= RELATIONSHIP_EDGE_DROP_LIMIT or recent_edge <= -0.05
    )
    stability = clamp(78 + edge_change * 220 + recent_edge * 120, 0, 100)
    return {
        "recent_probability": recent_probability,
        "prior_probability": prior_probability,
        "recent_edge": recent_edge,
        "edge_change": edge_change,
        "relationship_drift": drift,
        "relationship_stability_score": stability,
    }


def one_sided_binomial_tail(successes: int, trials: int, null_probability: float | None) -> float | None:
    if trials <= 0 or null_probability is None:
        return None
    probability = clamp(null_probability, 1e-6, 1 - 1e-6)
    return clamp(
        math.fsum(
            math.comb(trials, value)
            * probability**value
            * (1 - probability) ** (trials - value)
            for value in range(max(0, successes), trials + 1)
        ),
        0,
        1,
    )


def benjamini_hochberg(p_values: dict[str, float | None]) -> dict[str, float]:
    usable = sorted(
        ((key, clamp(float(value), 0, 1)) for key, value in p_values.items() if value is not None),
        key=lambda item: item[1],
    )
    if not usable:
        return {}
    count = len(usable)
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank in range(count, 0, -1):
        key, p_value = usable[rank - 1]
        running = min(running, p_value * count / rank)
        adjusted[key] = clamp(running, 0, 1)
    return adjusted


def apply_fdr_gate(
    rows: list[dict[str, Any]],
    key_field: str,
    p_value_field: str,
    q_value_field: str,
) -> int:
    family = {
        str(row.get(key_field) or ""): safe_float(row.get(p_value_field))
        for row in rows
        if row.get(key_field) and row.get("current_trigger")
    }
    adjusted = benjamini_hochberg(family)
    rejected = 0
    for row in rows:
        key = str(row.get(key_field) or "")
        q_value = adjusted.get(key)
        row[q_value_field] = q_value
        row["fdr_family_size"] = len(family)
        row["multiple_test_pass"] = q_value is not None and q_value <= FDR_THRESHOLD
        if not row.get("current_trigger") or row["multiple_test_pass"]:
            continue
        reasons = list(row.get("abstain_reasons") or [])
        reason = "多重检验校正未通过"
        if reason not in reasons:
            reasons.append(reason)
        row["abstain_reasons"] = reasons
        row["abstain"] = True
        row["decision_status"] = "拒绝预测"
        row["reliability_grade"] = "拒绝预测"
        if "reliability_grade_5d" in row:
            row["reliability_grade_5d"] = "拒绝预测"
        row["certainty_score"] = min(safe_float(row.get("certainty_score")) or 0, 49)
        rejected += 1
    return rejected


def evaluate_events(
    events: list[dict[str, Any]],
    current_bucket: str,
    label_key: str,
    return_key: str,
) -> dict[str, Any]:
    ordered = sorted(events, key=lambda item: (str(item.get("signal_date") or ""), str(item.get("exit_date") or "")))
    samples = len(ordered)
    successes = sum(1 for item in ordered if item.get(label_key))
    raw_probability = successes / samples if samples else None
    if samples < 12:
        split_idx = samples
    else:
        split_idx = min(max(8, math.floor(samples * TRAIN_SHARE)), max(samples - 6, 0))
    test_start = str(ordered[split_idx].get("signal_date") or "") if split_idx < samples else "9999-12-31"
    history = [item for item in ordered[:split_idx] if str(item.get("exit_date") or "") < test_start]
    oos = ordered[split_idx:]
    predictions: list[float] = []
    naive_predictions: list[float] = []
    labels: list[int] = []
    for item in oos:
        signal_date = str(item.get("signal_date") or "")
        eligible = [row for row in history if str(row.get("exit_date") or "") < signal_date]
        long_probability, _, baseline = posterior_from_history(eligible, str(item.get("bucket") or ""), label_key)
        decayed_probability, _, _ = decayed_posterior_from_history(
            eligible,
            str(item.get("bucket") or ""),
            label_key,
        )
        prediction = long_probability * 0.65 + decayed_probability * 0.35
        predictions.append(prediction)
        naive_predictions.append(baseline)
        labels.append(1 if item.get(label_key) else 0)
        history.append(item)

    brier = average([(prediction - label) ** 2 for prediction, label in zip(predictions, labels)])
    baseline_brier = average([(prediction - label) ** 2 for prediction, label in zip(naive_predictions, labels)])
    brier_skill = 1 - brier / baseline_brier if brier is not None and baseline_brier and baseline_brier > 0 else None
    ece = calibration_error(predictions, labels)
    long_probability, regime_samples, baseline_probability = posterior_from_history(ordered, current_bucket, label_key)
    decayed_probability, decayed_effective_samples, decayed_baseline = decayed_posterior_from_history(
        ordered,
        current_bucket,
        label_key,
    )
    drift = relationship_drift(ordered, current_bucket, label_key, decayed_baseline)
    current_probability = long_probability * 0.65 + decayed_probability * 0.35
    if drift.get("relationship_drift"):
        current_probability = baseline_probability + (current_probability - baseline_probability) * 0.25
    calibration_bias = average([label - prediction for prediction, label in zip(predictions, labels)]) or 0
    adjusted_probability = clamp(current_probability + calibration_bias, 0.05, 0.95)
    oos_factor = clamp((len(oos) - 8) / 40, 0, 1)
    skill_factor = clamp((brier_skill or 0) / 0.12, 0, 1)
    calibration_factor = clamp(1 - (ece or 0.20) / 0.18, 0, 1)
    trust = oos_factor * skill_factor * calibration_factor
    calibrated_probability = baseline_probability + (adjusted_probability - baseline_probability) * trust

    oos_successes = sum(labels)
    evidence_p_value = one_sided_binomial_tail(
        oos_successes,
        len(labels),
        average(naive_predictions),
    )
    oos_lower, oos_upper = wilson_interval(oos_successes, len(labels))
    effective_trials = regime_samples + PRIOR_STRENGTH
    effective_successes = current_probability * effective_trials
    posterior_lower, posterior_upper = wilson_interval(effective_successes, effective_trials)
    lower_candidates = [value for value in (oos_lower, posterior_lower, calibrated_probability) if value is not None]
    upper_candidates = [value for value in (oos_upper, posterior_upper, calibrated_probability) if value is not None]
    conservative_probability = min(lower_candidates) if lower_candidates else None
    probability_upper = max(upper_candidates) if upper_candidates else None

    evaluation_rows = oos if len(oos) >= 6 else ordered
    returns = [safe_float(item.get(return_key)) for item in evaluation_rows]
    avg_return = average(returns)
    median_return = quantile(returns, 0.5)
    p10_return = quantile(returns, 0.10)
    p25_return = quantile(returns, 0.25)
    p75_return = quantile(returns, 0.75)
    p90_return = quantile(returns, 0.90)
    return {
        "samples": samples,
        "successes": successes,
        "raw_probability": raw_probability,
        "oos_samples": len(oos),
        "oos_successes": oos_successes,
        "oos_probability": oos_successes / len(oos) if oos else None,
        "posterior_probability": current_probability,
        "long_run_posterior_probability": long_probability,
        "decayed_probability": decayed_probability,
        "decayed_effective_samples": decayed_effective_samples,
        "calibrated_probability": calibrated_probability,
        "conservative_probability": conservative_probability,
        "probability_upper": probability_upper,
        "baseline_probability": baseline_probability,
        "predictive_lift": calibrated_probability - baseline_probability,
        "regime_samples": regime_samples,
        "brier_score": brier,
        "baseline_brier_score": baseline_brier,
        "brier_skill": brier_skill,
        "calibration_error": ece,
        "calibration_bias": calibration_bias,
        "calibration_trust": trust,
        "evidence_p_value": evidence_p_value,
        **drift,
        "avg_return": avg_return,
        "median_return": median_return,
        "p10_return": p10_return,
        "p25_return": p25_return,
        "p75_return": p75_return,
        "p90_return": p90_return,
        "avg_mae": average([safe_float(item.get("mae")) for item in evaluation_rows]),
        "avg_mfe": average([safe_float(item.get("mfe")) for item in evaluation_rows]),
        "test_start": test_start if oos else None,
    }


def decision_assessment(
    profit: dict[str, Any],
    alpha: dict[str, Any],
    stress_avg_return: float | None,
    current_state: dict[str, Any],
    mapping_ok: bool = True,
) -> tuple[float, str, list[str]]:
    reasons: list[str] = []
    signal_date = str(current_state.get("signal_date") or "")
    if current_state.get("us_mapping_quality") not in {"direct", "sector_proxy"}:
        reasons.append("缺少可验证的美股行业映射")
    if not current_state.get("trigger_active"):
        reasons.append("当前未形成点时触发")
    if signal_date and days_between(signal_date, date.today().isoformat()) > 5:
        reasons.append("美股信号数据过期")
    if int(profit.get("oos_samples") or 0) < 18:
        reasons.append("独立样本不足18个")
    if (safe_float(profit.get("calibrated_probability")) or 0) < 0.52:
        reasons.append("校准后盈利概率不足52%")
    if (safe_float(profit.get("predictive_lift")) or 0) < 0.01:
        reasons.append("条件概率未显著优于无条件基准")
    if (safe_float(profit.get("conservative_probability")) or 0) < 0.45:
        reasons.append("概率下界偏低")
    if safe_float(profit.get("brier_skill")) is None or float(profit.get("brier_skill") or 0) <= 0:
        reasons.append("样本外概率未优于朴素基线")
    if (safe_float(profit.get("calibration_error")) or 1) > 0.12:
        reasons.append("概率校准误差偏高")
    if profit.get("relationship_drift"):
        reasons.append("近期关系漂移，历史优势已衰减")
    if (safe_float(alpha.get("calibrated_probability")) or 0) < 0.50:
        reasons.append("超额收益概率不足50%")
    if stress_avg_return is None or stress_avg_return <= 0:
        reasons.append("高成本压力测试未通过")
    if (safe_float(profit.get("p10_return")) or -99) < -10:
        reasons.append("10分位尾部亏损过深")
    if not mapping_ok:
        reasons.append("映射或可交易性未通过")

    calibrated = safe_float(profit.get("calibrated_probability")) or 0.5
    lower = safe_float(profit.get("conservative_probability")) or 0
    skill = safe_float(profit.get("brier_skill")) or -0.05
    ece = safe_float(profit.get("calibration_error")) or 0.20
    oos_samples = int(profit.get("oos_samples") or 0)
    alpha_probability = safe_float(alpha.get("calibrated_probability")) or 0.5
    tail = safe_float(profit.get("p10_return")) or -12
    activation = safe_float(current_state.get("activation_score")) or 0
    stability = safe_float(profit.get("relationship_stability_score")) or 50
    score = (
        32
        + (calibrated - 0.5) * 120
        + (lower - 0.4) * 55
        + clamp(skill, -0.1, 0.2) * 70
        + clamp(oos_samples / 50, 0, 1) * 12
        + (alpha_probability - 0.5) * 55
        + clamp(1 - ece / 0.18, 0, 1) * 8
        + clamp(activation / 100, 0, 1) * 8
        + (stability - 50) * 0.12
        + clamp((stress_avg_return or -3) * 0.9, -8, 8)
        + clamp(tail + 6, -8, 5)
    )
    hard_reasons = {
        "当前未形成点时触发",
        "美股信号数据过期",
        "独立样本不足18个",
        "校准后盈利概率不足52%",
        "样本外概率未优于朴素基线",
        "近期关系漂移，历史优势已衰减",
        "条件概率未显著优于无条件基准",
        "超额收益概率不足50%",
        "高成本压力测试未通过",
        "映射或可交易性未通过",
        "缺少可验证的美股行业映射",
    }
    hard_fail = any(reason in hard_reasons for reason in reasons)
    if hard_fail:
        score = min(score, 49)
        status = "拒绝预测"
    elif reasons:
        score = min(score, 64)
        status = "低证据观察"
    elif score >= 78 and lower >= 0.52 and oos_samples >= 30:
        status = "较高证据"
    else:
        status = "条件可观察"
    return round(clamp(score, 0, 100), 1), status, reasons


def build_concept_dataset(
    concept: dict[str, Any],
    us_proxy_series: dict[str, PriceSeries],
    cn_proxy_series: dict[str, PriceSeries],
    output_dir: Path,
) -> dict[str, Any]:
    us_items = concept.get("us", {}).get("tickers") or []
    cn_companies = concept.get("cn", {}).get("companies") or []
    us_series = build_series_map(us_items, "symbol", output_dir)
    cn_series = build_series_map(cn_companies, "code", output_dir)
    primary_calendar = us_proxy_series.get("QQQ") or next(iter(us_series.values()), None)
    if primary_calendar is None:
        return {"available": False, "reason": "缺少美股日历"}

    events_by_horizon: dict[int, list[dict[str, Any]]] = {horizon: [] for horizon in HORIZONS}
    stock_events: dict[str, dict[int, list[dict[str, Any]]]] = {
        str(company.get("code")): {horizon: [] for horizon in HORIZONS}
        for company in cn_companies
        if company.get("code")
    }
    residual_history: list[float] = []
    last_trigger_idx = -EVENT_COOLDOWN_SESSIONS - 1
    latest_state: dict[str, Any] | None = None
    raw_trigger_count = 0
    accepted_trigger_count = 0
    us_mapping_quality = str(concept.get("us_mapping_quality") or "broad_fallback")

    for calendar_idx, signal_date in enumerate(primary_calendar.dates):
        us_1d, us_coverage = weighted_exact_return(us_items, us_series, "symbol", signal_date, 1)
        us_5d, _ = weighted_exact_return(us_items, us_series, "symbol", signal_date, 5)
        us_proxy_1d = proxy_return_exact(us_proxy_series, US_PROXY_WEIGHTS, signal_date, 1)
        us_proxy_5d = proxy_return_exact(us_proxy_series, US_PROXY_WEIGHTS, signal_date, 5)
        if us_1d is None or us_proxy_1d is None:
            continue
        us_residual_1d = us_1d - us_proxy_1d
        us_residual_5d = (us_5d - us_proxy_5d) if us_5d is not None and us_proxy_5d is not None else None
        prior_residuals = residual_history[-60:]
        if len(prior_residuals) >= 20:
            mean = sum(prior_residuals) / len(prior_residuals)
            variance = sum((value - mean) ** 2 for value in prior_residuals) / max(len(prior_residuals) - 1, 1)
            residual_vol = max(math.sqrt(max(variance, 0)), 0.45)
        else:
            residual_vol = 0.75
        z_score = us_residual_1d / residual_vol
        cn = cn_state(cn_companies, cn_series, cn_proxy_series, signal_date)
        lag_gap = us_residual_1d - (safe_float(cn.get("cn_residual_1d")) or 0)
        state = {
            "signal_date": signal_date,
            "us_avg_1d": us_1d,
            "us_avg_5d": us_5d,
            "us_proxy_1d": us_proxy_1d,
            "us_proxy_5d": us_proxy_5d,
            "us_residual_1d": us_residual_1d,
            "us_residual_5d": us_residual_5d,
            "us_residual_vol": residual_vol,
            "us_residual_z": z_score,
            "us_coverage": us_coverage,
            "us_mapping_quality": us_mapping_quality,
            "us_mapping_label": concept.get("us_mapping_label"),
            "lag_gap_neutral": lag_gap,
            **cn,
        }
        state["bucket"] = trigger_bucket(state)
        state["trigger_active"] = is_trigger(state)
        state["activation_score"] = round(current_activation_score(state), 1)
        latest_state = state
        residual_history.append(us_residual_1d)
        if not state["trigger_active"]:
            continue
        raw_trigger_count += 1
        if calendar_idx - last_trigger_idx <= EVENT_COOLDOWN_SESSIONS:
            continue
        last_trigger_idx = calendar_idx

        stock_paths: dict[str, dict[int, dict[str, Any]]] = {}
        for company in cn_companies:
            code = str(company.get("code") or "")
            series = cn_series.get(code)
            if not code or series is None:
                continue
            factor_state = stock_signal_state(series, cn_proxy_series, signal_date)
            paths = stock_trade_paths(company, series, cn_proxy_series, signal_date)
            if paths:
                stock_paths[code] = paths
                for horizon, row in paths.items():
                    stock_events.setdefault(code, {value: [] for value in HORIZONS})[horizon].append(
                        {
                            **row,
                            "bucket": factor_state.get("bucket"),
                            "concept_bucket": state["bucket"],
                            "cn_regime": state.get("cn_regime"),
                            "us_residual_z": state.get("us_residual_z"),
                            "lag_gap_neutral": state.get("lag_gap_neutral"),
                            "stock_residual_5d": factor_state.get("residual_5d"),
                            "stock_relative_volume": factor_state.get("relative_volume"),
                            "stock_volatility_20d": factor_state.get("daily_volatility_20d"),
                        }
                    )
        event_added = False
        for horizon in HORIZONS:
            rows = [paths[horizon] for paths in stock_paths.values() if horizon in paths]
            aggregate = event_record(state, horizon, rows)
            if aggregate is not None:
                events_by_horizon[horizon].append(aggregate)
                event_added = True
        if event_added:
            accepted_trigger_count += 1

    latest_company_states = {
        str(company.get("code")): stock_signal_state(series, cn_proxy_series, str((latest_state or {}).get("signal_date") or ""))
        for company in cn_companies
        for series in [cn_series.get(str(company.get("code") or ""))]
        if company.get("code") and series is not None
    }
    return {
        "available": latest_state is not None,
        "latest_state": latest_state or {},
        "events_by_horizon": events_by_horizon,
        "stock_events": stock_events,
        "latest_company_states": latest_company_states,
        "us_series_count": len(us_series),
        "cn_series_count": len(cn_series),
        "raw_trigger_count": raw_trigger_count,
        "accepted_trigger_count": accepted_trigger_count,
        "first_date": primary_calendar.dates[0],
        "last_date": primary_calendar.dates[-1],
    }


def horizon_payload(events: list[dict[str, Any]], current_state: dict[str, Any], horizon: int) -> dict[str, Any]:
    current_bucket = str(current_state.get("bucket") or "")
    profit = evaluate_events(events, current_bucket, "profit_success", "net_return")
    alpha = evaluate_events(events, current_bucket, "alpha_success", "excess_return")
    ordered = sorted(events, key=lambda item: str(item.get("signal_date") or ""))
    split_idx = min(max(8, math.floor(len(ordered) * TRAIN_SHARE)), max(len(ordered) - 6, 0)) if len(ordered) >= 12 else len(ordered)
    evaluation_rows = ordered[split_idx:] if len(ordered[split_idx:]) >= 6 else ordered
    stress_avg = average([safe_float(item.get("stress_net_return")) for item in evaluation_rows])
    score, status, reasons = decision_assessment(profit, alpha, stress_avg, current_state)
    return {
        "horizon": horizon,
        "samples": profit.get("oos_samples"),
        "successes": profit.get("oos_successes"),
        "full_samples": profit.get("samples"),
        "full_successes": profit.get("successes"),
        "raw_probability": profit.get("oos_probability"),
        "historical_probability": profit.get("raw_probability"),
        "posterior_probability": profit.get("posterior_probability"),
        "long_run_posterior_probability": profit.get("long_run_posterior_probability"),
        "decayed_probability": profit.get("decayed_probability"),
        "decayed_effective_samples": profit.get("decayed_effective_samples"),
        "calibrated_probability": profit.get("calibrated_probability"),
        "conservative_probability": profit.get("conservative_probability"),
        "probability_upper": profit.get("probability_upper"),
        "validation_probability": profit.get("oos_probability"),
        "validation_conservative_probability": profit.get("conservative_probability"),
        "validation_samples": profit.get("oos_samples"),
        "alpha_probability": alpha.get("calibrated_probability"),
        "alpha_conservative_probability": alpha.get("conservative_probability"),
        "brier_score": profit.get("brier_score"),
        "baseline_brier_score": profit.get("baseline_brier_score"),
        "brier_skill": profit.get("brier_skill"),
        "baseline_probability": profit.get("baseline_probability"),
        "predictive_lift": profit.get("predictive_lift"),
        "calibration_error": profit.get("calibration_error"),
        "calibration_trust": profit.get("calibration_trust"),
        "evidence_p_value": profit.get("evidence_p_value"),
        "recent_probability": profit.get("recent_probability"),
        "prior_probability": profit.get("prior_probability"),
        "recent_edge": profit.get("recent_edge"),
        "edge_change": profit.get("edge_change"),
        "relationship_drift": profit.get("relationship_drift"),
        "relationship_stability_score": profit.get("relationship_stability_score"),
        "regime_samples": profit.get("regime_samples"),
        "avg_return": profit.get("avg_return"),
        "avg_return_after_cost": profit.get("avg_return"),
        "median_return": profit.get("median_return"),
        "p10_return": profit.get("p10_return"),
        "p25_return": profit.get("p25_return"),
        "p75_return": profit.get("p75_return"),
        "p90_return": profit.get("p90_return"),
        "p10_return_after_cost": profit.get("p10_return"),
        "avg_mae": profit.get("avg_mae"),
        "avg_mfe": profit.get("avg_mfe"),
        "stress_avg_return": stress_avg,
        "certainty_score": score,
        "reliability_score": score,
        "reliability_grade": status,
        "decision_status": status,
        "abstain": status == "拒绝预测",
        "abstain_reasons": reasons,
        "test_start": profit.get("test_start"),
    }


def stock_payloads(
    concept: dict[str, Any],
    dataset: dict[str, Any],
    current_state: dict[str, Any],
    target_horizon: int,
) -> list[dict[str, Any]]:
    company_by_code = {
        str(company.get("code")): company for company in (concept.get("cn", {}).get("companies") or []) if company.get("code")
    }
    rows: list[dict[str, Any]] = []
    for code, per_horizon_events in (dataset.get("stock_events") or {}).items():
        company = company_by_code.get(code, {"code": code})
        factor_state = (dataset.get("latest_company_states") or {}).get(code) or {"bucket": "未知", "risk_ok": False}
        stock_current_state = {**current_state, "bucket": factor_state.get("bucket")}
        horizon_stats: list[dict[str, Any]] = []
        for horizon in HORIZONS:
            stats = horizon_payload(per_horizon_events.get(horizon) or [], stock_current_state, horizon)
            if int(stats.get("full_samples") or 0) >= 8:
                horizon_stats.append(stats)
        if not horizon_stats:
            continue
        best = max(
            horizon_stats,
            key=lambda item: (
                0 if item.get("abstain") else 1,
                safe_float(item.get("certainty_score")) or 0,
                safe_float(item.get("calibrated_probability")) or 0,
                -int(item.get("horizon") or 99),
            ),
        )
        target = next((item for item in horizon_stats if int(item.get("horizon") or 0) == target_horizon), best)
        mapping_confidence = safe_float(company.get("mapping_confidence")) or 0
        mapping_ok = bool(
            mapping_confidence >= 55
            and company.get("tradability") == "normal"
            and (safe_float(company.get("change")) is None or float(company.get("change") or 0) < 7)
            and factor_state.get("risk_ok")
        )
        target_events = per_horizon_events.get(int(target.get("horizon") or target_horizon)) or []
        ordered = sorted(target_events, key=lambda item: str(item.get("signal_date") or ""))
        split_idx = min(max(8, math.floor(len(ordered) * TRAIN_SHARE)), max(len(ordered) - 6, 0)) if len(ordered) >= 12 else len(ordered)
        evaluation_rows = ordered[split_idx:] if len(ordered[split_idx:]) >= 6 else ordered
        stress_avg = average([safe_float(item.get("stress_net_return")) for item in evaluation_rows])
        profit = evaluate_events(target_events, str(factor_state.get("bucket") or ""), "profit_success", "net_return")
        alpha = evaluate_events(target_events, str(factor_state.get("bucket") or ""), "alpha_success", "excess_return")
        score, status, reasons = decision_assessment(profit, alpha, stress_avg, stock_current_state, mapping_ok=mapping_ok)
        rows.append(
            {
                "code": code,
                "name": company.get("name"),
                "market": company.get("market"),
                "role": company.get("role"),
                "reason": company.get("reason"),
                "concept_id": concept.get("id"),
                "concept_name": concept.get("name"),
                "concept_short_name": concept.get("short_name"),
                "samples_5d": target.get("samples"),
                "successes_5d": target.get("successes"),
                "raw_probability_5d": target.get("raw_probability"),
                "long_run_posterior_probability_5d": target.get("long_run_posterior_probability"),
                "decayed_probability_5d": target.get("decayed_probability"),
                "decayed_effective_samples_5d": target.get("decayed_effective_samples"),
                "calibrated_probability_5d": target.get("calibrated_probability"),
                "conservative_probability_5d": target.get("conservative_probability"),
                "probability_upper_5d": target.get("probability_upper"),
                "validation_probability_5d": target.get("validation_probability"),
                "validation_conservative_probability_5d": target.get("validation_conservative_probability"),
                "validation_samples_5d": target.get("validation_samples"),
                "alpha_probability_5d": target.get("alpha_probability"),
                "brier_skill_5d": target.get("brier_skill"),
                "baseline_probability_5d": target.get("baseline_probability"),
                "predictive_lift_5d": target.get("predictive_lift"),
                "calibration_error_5d": target.get("calibration_error"),
                "evidence_p_value_5d": target.get("evidence_p_value"),
                "recent_probability_5d": target.get("recent_probability"),
                "prior_probability_5d": target.get("prior_probability"),
                "recent_edge_5d": target.get("recent_edge"),
                "edge_change_5d": target.get("edge_change"),
                "relationship_drift_5d": target.get("relationship_drift"),
                "relationship_stability_score_5d": target.get("relationship_stability_score"),
                "avg_return_5d": target.get("avg_return"),
                "avg_return_after_cost_5d": target.get("avg_return_after_cost"),
                "median_return_5d": target.get("median_return"),
                "p10_return_5d": target.get("p10_return"),
                "p10_return_after_cost_5d": target.get("p10_return_after_cost"),
                "avg_mae_5d": target.get("avg_mae"),
                "stress_avg_return_5d": target.get("stress_avg_return"),
                "recommended_horizon_days": PRIMARY_HORIZON,
                "best_horizon": best.get("horizon"),
                "exploratory_best_horizon": best.get("horizon"),
                "best_conservative_probability": best.get("conservative_probability"),
                "best_validation_conservative_probability": best.get("validation_conservative_probability"),
                "best_samples": best.get("samples"),
                "best_return": best.get("avg_return"),
                "best_return_after_cost": best.get("avg_return_after_cost"),
                "best_reliability_score": best.get("certainty_score"),
                "best_reliability_grade": best.get("decision_status"),
                "certainty_score": score,
                "reliability_score_5d": target.get("certainty_score"),
                "reliability_grade_5d": target.get("decision_status"),
                "reliability_grade": status,
                "decision_status": status,
                "abstain": status == "拒绝预测",
                "abstain_reasons": reasons,
                "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
                "stress_cost_pct": STRESS_COST_PCT,
                "horizon_stats": horizon_stats,
                "trigger_date": current_state.get("signal_date"),
                "current_trigger": current_state.get("trigger_active"),
                "current_regime": current_state.get("cn_regime"),
                "factor_state": factor_state,
                "mapping_confidence": mapping_confidence,
                "tradability": company.get("tradability"),
            }
        )
    return rows


def build_prediction_model_v6(
    concepts: list[dict[str, Any]],
    proxy_quotes: dict[str, dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    proxy_items = [{"symbol": symbol, **quote} for symbol, quote in proxy_quotes.items()]
    proxy_series = build_series_map(proxy_items, "symbol", output_dir)
    us_proxy_series = {symbol: series for symbol, series in proxy_series.items() if symbol in US_PROXY_WEIGHTS}
    cn_proxy_series = {symbol: series for symbol, series in proxy_series.items() if symbol in CN_PROXY_WEIGHTS}
    if len(us_proxy_series) < 2 or len(cn_proxy_series) < 2:
        return {"available": False, "message": "缺少完整的中美指数历史，V7点时模型拒绝生成概率。"}

    concept_rows: list[dict[str, Any]] = []
    stock_candidates: list[dict[str, Any]] = []
    rejected_reasons: Counter[str] = Counter()
    earliest: str | None = None
    latest: str | None = None
    accepted_events = 0

    for concept in concepts:
        dataset = build_concept_dataset(concept, us_proxy_series, cn_proxy_series, output_dir)
        if not dataset.get("available"):
            continue
        current_state = dataset.get("latest_state") or {}
        horizon_stats = [
            horizon_payload((dataset.get("events_by_horizon") or {}).get(horizon) or [], current_state, horizon)
            for horizon in HORIZONS
        ]
        usable = [item for item in horizon_stats if int(item.get("full_samples") or 0) >= 8]
        if not usable:
            continue
        exploratory_best = max(
            usable,
            key=lambda item: (
                0 if item.get("abstain") else 1,
                safe_float(item.get("certainty_score")) or 0,
                safe_float(item.get("calibrated_probability")) or 0,
                -int(item.get("horizon") or 99),
            ),
        )
        primary = next(
            (item for item in usable if int(item.get("horizon") or 0) == PRIMARY_HORIZON),
            exploratory_best,
        )
        for reason in primary.get("abstain_reasons") or []:
            rejected_reasons[reason] += 1
        current_activation = safe_float(current_state.get("activation_score")) or 0
        decision_score = safe_float(primary.get("certainty_score")) or 0
        future_lock_score = clamp(decision_score * 0.85 + current_activation * 0.15, 0, 100)
        stock_stats_all = stock_payloads(concept, dataset, current_state, 5)
        best_horizon = PRIMARY_HORIZON
        stock_stats = []
        for row in stock_stats_all:
            selected = next((item for item in row.get("horizon_stats") or [] if int(item.get("horizon") or 0) == best_horizon), None)
            if selected:
                stock_stats.append(
                    {
                        "code": row.get("code"),
                        "name": row.get("name"),
                        "market": row.get("market"),
                        "role": row.get("role"),
                        "reason": row.get("reason"),
                        "samples": selected.get("samples"),
                        "successes": selected.get("successes"),
                        "raw_probability": selected.get("raw_probability"),
                        "decayed_probability": selected.get("decayed_probability"),
                        "calibrated_probability": selected.get("calibrated_probability"),
                        "baseline_probability": selected.get("baseline_probability"),
                        "predictive_lift": selected.get("predictive_lift"),
                        "conservative_probability": selected.get("conservative_probability"),
                        "validation_conservative_probability": selected.get("validation_conservative_probability"),
                        "validation_samples": selected.get("validation_samples"),
                        "alpha_probability": selected.get("alpha_probability"),
                        "brier_skill": selected.get("brier_skill"),
                        "calibration_error": selected.get("calibration_error"),
                        "evidence_p_value": selected.get("evidence_p_value"),
                        "recent_probability": selected.get("recent_probability"),
                        "prior_probability": selected.get("prior_probability"),
                        "recent_edge": selected.get("recent_edge"),
                        "edge_change": selected.get("edge_change"),
                        "relationship_drift": selected.get("relationship_drift"),
                        "relationship_stability_score": selected.get("relationship_stability_score"),
                        "avg_return": selected.get("avg_return"),
                        "avg_return_after_cost": selected.get("avg_return_after_cost"),
                        "median_return": selected.get("median_return"),
                        "p10_return": selected.get("p10_return"),
                        "p25_return": selected.get("p25_return"),
                        "p75_return": selected.get("p75_return"),
                        "p10_return_after_cost": selected.get("p10_return_after_cost"),
                        "avg_mae": selected.get("avg_mae"),
                        "reliability_grade": row.get("decision_status"),
                        "decision_status": row.get("decision_status"),
                        "certainty_score": row.get("certainty_score"),
                        "abstain": row.get("abstain"),
                        "abstain_reasons": row.get("abstain_reasons"),
                        "factor_state": row.get("factor_state"),
                        "horizon_stats": row.get("horizon_stats"),
                    }
                )
        stock_stats.sort(
            key=lambda item: (
                0 if item.get("abstain") else 1,
                safe_float(item.get("certainty_score")) or 0,
                safe_float(item.get("calibrated_probability")) or 0,
            ),
            reverse=True,
        )
        concept_rows.append(
            {
                "id": concept.get("id"),
                "name": concept.get("name"),
                "short_name": concept.get("short_name"),
                "underlying_driver": concept.get("underlying_driver"),
                "trigger": concept.get("trigger"),
                "current_activation_score": round(current_activation, 1),
                "current_trigger": bool(current_state.get("trigger_active")),
                "current_signal_date": current_state.get("signal_date"),
                "current_regime": current_state.get("cn_regime"),
                "current_state": current_state,
                "future_lock_score": round(future_lock_score, 1),
                "best_horizon": PRIMARY_HORIZON,
                "decision_horizon": PRIMARY_HORIZON,
                "exploratory_best_horizon": exploratory_best.get("horizon"),
                "historical_probability": primary.get("historical_probability"),
                "posterior_probability": primary.get("posterior_probability"),
                "long_run_posterior_probability": primary.get("long_run_posterior_probability"),
                "decayed_probability": primary.get("decayed_probability"),
                "decayed_effective_samples": primary.get("decayed_effective_samples"),
                "calibrated_probability": primary.get("calibrated_probability"),
                "conservative_probability": primary.get("conservative_probability"),
                "probability_upper": primary.get("probability_upper"),
                "baseline_probability": primary.get("baseline_probability"),
                "predictive_lift": primary.get("predictive_lift"),
                "validation_conservative_probability": primary.get("validation_conservative_probability"),
                "validation_samples": primary.get("validation_samples"),
                "alpha_probability": primary.get("alpha_probability"),
                "brier_skill": primary.get("brier_skill"),
                "calibration_error": primary.get("calibration_error"),
                "evidence_p_value": primary.get("evidence_p_value"),
                "recent_probability": primary.get("recent_probability"),
                "prior_probability": primary.get("prior_probability"),
                "recent_edge": primary.get("recent_edge"),
                "edge_change": primary.get("edge_change"),
                "relationship_drift": primary.get("relationship_drift"),
                "relationship_stability_score": primary.get("relationship_stability_score"),
                "certainty_score": primary.get("certainty_score"),
                "reliability_grade": primary.get("decision_status"),
                "decision_status": primary.get("decision_status"),
                "abstain": primary.get("abstain"),
                "abstain_reasons": primary.get("abstain_reasons"),
                "samples": primary.get("samples"),
                "successes": primary.get("successes"),
                "full_samples": primary.get("full_samples"),
                "avg_return": primary.get("avg_return"),
                "avg_return_after_cost": primary.get("avg_return_after_cost"),
                "median_return": primary.get("median_return"),
                "p10_return_after_cost": primary.get("p10_return_after_cost"),
                "avg_mae": primary.get("avg_mae"),
                "verdict": primary.get("decision_status"),
                "horizon_stats": horizon_stats,
                "future_cone": [
                    {
                        "day": item.get("horizon"),
                        "median_return": item.get("median_return"),
                        "p10_return": item.get("p10_return"),
                        "p25_return": item.get("p25_return"),
                        "p75_return": item.get("p75_return"),
                        "p90_return": item.get("p90_return"),
                        "raw_probability": item.get("raw_probability"),
                        "calibrated_probability": item.get("calibrated_probability"),
                        "conservative_probability": item.get("conservative_probability"),
                        "validation_conservative_probability": item.get("validation_conservative_probability"),
                        "avg_return_after_cost": item.get("avg_return_after_cost"),
                        "p10_return_after_cost": item.get("p10_return_after_cost"),
                        "samples": item.get("samples"),
                    }
                    for item in horizon_stats
                ],
                "stock_stats": stock_stats[:20],
                "dataset_audit": {
                    "us_series": dataset.get("us_series_count"),
                    "cn_series": dataset.get("cn_series_count"),
                    "raw_triggers": dataset.get("raw_trigger_count"),
                    "independent_triggers": dataset.get("accepted_trigger_count"),
                    "cooldown_sessions": EVENT_COOLDOWN_SESSIONS,
                },
            }
        )
        stock_candidates.extend(stock_stats_all)
        earliest = min(value for value in (earliest, dataset.get("first_date")) if value)
        latest = max(value for value in (latest, dataset.get("last_date")) if value)
        accepted_events += int(dataset.get("accepted_trigger_count") or 0)

    concept_fdr_rejections = apply_fdr_gate(
        concept_rows,
        "id",
        "evidence_p_value",
        "fdr_q_value",
    )
    if concept_fdr_rejections:
        rejected_reasons["多重检验校正未通过"] += concept_fdr_rejections
    concept_rows.sort(
        key=lambda item: (
            0 if item.get("abstain") else 1,
            safe_float(item.get("certainty_score")) or 0,
            safe_float(item.get("calibrated_probability")) or 0,
            safe_float(item.get("conservative_probability")) or 0,
        ),
        reverse=True,
    )
    deduped: dict[str, dict[str, Any]] = {}
    for row in stock_candidates:
        code = str(row.get("code") or "")
        if not code:
            continue
        current = deduped.get(code)
        candidate_key = (
            0 if row.get("abstain") else 1,
            1 if row.get("current_trigger") else 0,
            safe_float(row.get("certainty_score")) or 0,
            safe_float(row.get("calibrated_probability_5d")) or 0,
        )
        current_key = (
            0 if current and current.get("abstain") else 1,
            1 if current and current.get("current_trigger") else 0,
            (safe_float(current.get("certainty_score")) or -1) if current else -1,
            (safe_float(current.get("calibrated_probability_5d")) or -1) if current else -1,
        )
        if current is None or candidate_key > current_key:
            deduped[code] = row
    apply_fdr_gate(
        list(deduped.values()),
        "code",
        "evidence_p_value_5d",
        "fdr_q_value_5d",
    )
    for concept in concept_rows:
        for stock in concept.get("stock_stats") or []:
            selected = deduped.get(str(stock.get("code") or ""))
            if not selected or selected.get("concept_id") != concept.get("id"):
                continue
            stock["fdr_q_value"] = selected.get("fdr_q_value_5d")
            stock["fdr_family_size"] = selected.get("fdr_family_size")
            stock["multiple_test_pass"] = selected.get("multiple_test_pass")
            stock["decision_status"] = selected.get("decision_status")
            stock["reliability_grade"] = selected.get("reliability_grade")
            stock["abstain"] = selected.get("abstain")
            stock["abstain_reasons"] = selected.get("abstain_reasons")
    qualified = [row for row in deduped.values() if not row.get("abstain") and row.get("decision_status") in {"条件可观察", "较高证据"}]
    qualified.sort(
        key=lambda row: (
            safe_float(row.get("certainty_score")) or 0,
            safe_float(row.get("calibrated_probability_5d")) or 0,
            safe_float(row.get("conservative_probability_5d")) or 0,
            safe_float(row.get("avg_return_after_cost_5d")) or -999,
        ),
        reverse=True,
    )
    rejected = [row for row in deduped.values() if row.get("abstain")]
    screened = sorted(
        deduped.values(),
        key=lambda row: (
            0 if row.get("abstain") else 1,
            1 if row.get("current_trigger") else 0,
            safe_float(row.get("certainty_score")) or 0,
            safe_float(row.get("calibrated_probability_5d")) or 0,
            safe_float(row.get("predictive_lift_5d")) or -1,
        ),
        reverse=True,
    )
    for row in rejected:
        for reason in row.get("abstain_reasons") or []:
            rejected_reasons[reason] += 1

    return {
        "available": bool(concept_rows),
        "method": "V7点时概率模型：美股收盘后形成信号，A股仅允许下一交易日开盘进入并遵守T+1；五年日K按美股残差冲击、A股残差滞后和市场状态形成至少间隔10个美股交易日的独立事件。历史关系同时计算长期后验与36事件半衰期的时间衰减后验，最近12次事件若显示关系失效则拒绝预测。",
        "rank_basis": "先判断是否允许预测，再排序。固定5日为主终点；只有当前触发、时间顺序样本外Brier优于朴素基线、盈利和超额收益概率、成本压力、近期关系稳定性均通过，并在当期候选中通过Benjamini-Hochberg多重检验校正，个股才进入自动观察池。",
        "horizons": list(HORIZONS),
        "model_audit": {
            "version": "v7.0-decay-drift-fdr-20260727",
            "information_cutoff": "US close t -> next available A-share open t+1",
            "entry_model": "next A-share open; limit-gap/ST/zero-volume observations excluded",
            "exit_model": "T+1 compliant close after 1-10 full trading days",
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "stress_cost_pct": STRESS_COST_PCT,
            "train_share": TRAIN_SHARE,
            "event_cooldown_sessions": EVENT_COOLDOWN_SESSIONS,
            "primary_horizon_days": PRIMARY_HORIZON,
            "multiplicity_control": "5-day primary endpoint fixed before evaluation; Benjamini-Hochberg FDR <= 10% across contemporaneously triggered concepts and stocks; 1-10 day horizon profile is diagnostic only",
            "probability_method": "hierarchical beta-binomial shrinkage + 36-event half-life decay + chronological walk-forward calibration",
            "relationship_drift_gate": {
                "recent_window_events": RECENT_RELATIONSHIP_WINDOW,
                "edge_drop_limit": RELATIONSHIP_EDGE_DROP_LIMIT,
                "decay_half_life_events": DECAY_HALF_LIFE_EVENTS,
            },
            "validation_metrics": ["Brier skill vs expanding naive baseline", "adaptive-bin calibration error", "profit probability", "benchmark excess probability", "recent-vs-prior relationship edge", "BH-adjusted q-value"],
            "abstention_enabled": True,
            "benchmark_coverage": {
                "us_proxy_count": len(us_proxy_series),
                "cn_proxy_count": len(cn_proxy_series),
                "cn_proxy_degraded": len(cn_proxy_series) < len(CN_PROXY_WEIGHTS),
            },
            "known_limitations": [
                "板块与股票池现在每次刷新动态生成，但历史检验仍使用当前入选成分，存在选择偏差；不能替代真正的点时板块成分库",
                "历史新闻与基本面证据尚不能完整点时回放，因此不进入历史概率特征",
                "日K只能近似开盘成交，不能重建集合竞价排队和盘中冲击成本",
                "部分A股风格指数历史不可用时退化为沪深300基准，超额收益判定对小盘股会更保守或失真",
                "BH校正控制同一时点候选的预期虚假发现比例，但事件并非完全独立，显著性仍应视为近似证据",
                "概率是条件频率估计，不是收益承诺；市场结构变化会使历史关系失效",
                "尚未把LightGBM等非线性模型并入生产概率；当前已解决事件样本仍不足以可靠训练高维黑箱模型",
            ],
        },
        "sample_window": {
            "from": earliest,
            "to": latest,
            "trading_days": None,
            "independent_events": accepted_events,
            "latest_date": latest,
        },
        "concepts": concept_rows,
        "auto_recommendations": qualified[:30],
        "screened_candidates": screened,
        "auto_recommendation_horizon": 5,
        "screened_stock_count": len(deduped),
        "qualified_stock_count": len(qualified),
        "rejected_stock_count": len(rejected),
        "rejection_summary": [{"reason": reason, "count": count} for reason, count in rejected_reasons.most_common()],
        "top_concept_id": concept_rows[0].get("id") if concept_rows else None,
    }
