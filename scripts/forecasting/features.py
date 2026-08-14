from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .market_specs import (
    MarketSpec,
    board_feature_values,
    board_standard_limit_fraction,
)


FEATURE_COLUMNS = [
    "ret_1",
    "ret_5",
    "ret_20",
    "ret_60",
    "vol_20",
    "vol_60",
    "atr_14",
    "volume_ratio_20",
    "ma_gap_20",
    "ma_gap_60",
    "drawdown_60",
    "rsi_14",
    "trend_strength",
    "benchmark_ret_1",
    "benchmark_ret_5",
    "benchmark_ret_20",
    "benchmark_vol_20",
    "relative_5",
    "relative_20",
    "regime_up",
    "regime_high_vol",
    "weekday_sin",
    "weekday_cos",
    "board_main",
    "board_chinext",
    "board_star",
    "board_bse",
]


def load_chart_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def chart_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame = frame[list(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )
    frame = frame[
        (frame["open"] > 0)
        & (frame["high"] > 0)
        & (frame["low"] > 0)
        & (frame["close"] > 0)
        & (frame["high"] >= frame["low"])
    ].reset_index(drop=True)
    return frame


def market_aligned_chart_frame(
    rows: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]],
) -> pd.DataFrame:
    """Align one instrument to market sessions and blank non-tradeable bars."""
    instrument = chart_frame(rows)
    benchmark = chart_frame(benchmark_rows)
    if instrument.empty or benchmark.empty:
        return pd.DataFrame()
    frame = benchmark[["date"]].merge(instrument, on="date", how="left")
    frame = frame[frame["date"] >= instrument["date"].min()].reset_index(drop=True)
    numeric = frame[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric,
        errors="coerce",
    )
    tradeable = (
        numeric[["open", "high", "low", "close"]].gt(0).all(axis=1)
        & numeric["volume"].gt(0)
    )
    frame["tradeable"] = tradeable
    frame.loc[~tradeable, ["open", "high", "low", "close", "volume"]] = np.nan
    return frame


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    relative = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + relative)).fillna(50) / 100


def _benchmark_features(benchmark: pd.DataFrame) -> pd.DataFrame:
    close = benchmark["close"]
    ret_1 = close.pct_change(fill_method=None)
    output = pd.DataFrame({"date": benchmark["date"]})
    output["benchmark_ret_1"] = ret_1
    output["benchmark_ret_5"] = close.pct_change(5, fill_method=None)
    output["benchmark_ret_20"] = close.pct_change(20, fill_method=None)
    output["benchmark_vol_20"] = ret_1.rolling(20).std()
    output["regime_up"] = (close > close.rolling(60).mean()).astype(float)
    trailing_vol_median = output["benchmark_vol_20"].rolling(252, min_periods=80).median()
    output["regime_high_vol"] = (output["benchmark_vol_20"] > trailing_vol_median).astype(float)
    return output


def build_feature_frame(
    rows: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]],
    market_spec: MarketSpec,
    *,
    board_key: str,
) -> pd.DataFrame:
    frame = market_aligned_chart_frame(rows, benchmark_rows)
    benchmark = chart_frame(benchmark_rows)
    if (
        frame.empty
        or benchmark.empty
        or "close" not in frame
        or frame["close"].notna().sum() < market_spec.minimum_history
        or len(benchmark) < market_spec.minimum_history
    ):
        return pd.DataFrame()

    close = frame["close"]
    open_price = frame["open"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"].where(frame["volume"] > 0)
    ret_1 = close.pct_change(fill_method=None)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [(high - low).abs(), (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)

    features = pd.DataFrame({"date": frame["date"]})
    features["ret_1"] = ret_1
    features["ret_5"] = close.pct_change(5, fill_method=None)
    features["ret_20"] = close.pct_change(20, fill_method=None)
    features["ret_60"] = close.pct_change(60, fill_method=None)
    features["vol_20"] = ret_1.rolling(20).std()
    features["vol_60"] = ret_1.rolling(60).std()
    features["atr_14"] = true_range.rolling(14).mean() / previous_close
    volume_median = volume.rolling(20).median()
    features["volume_ratio_20"] = np.log(
        (volume / volume_median.replace(0, np.nan)).clip(lower=0.05, upper=20)
    )
    features["ma_gap_20"] = close / close.rolling(20).mean() - 1
    features["ma_gap_60"] = close / close.rolling(60).mean() - 1
    features["drawdown_60"] = close / close.rolling(60).max() - 1
    features["rsi_14"] = _rsi(close)
    features["trend_strength"] = features["ret_20"] / (
        features["vol_20"].clip(lower=0.002) * math.sqrt(20)
    )

    features = features.merge(_benchmark_features(benchmark), on="date", how="left")
    features["relative_5"] = features["ret_5"] - features["benchmark_ret_5"]
    features["relative_20"] = features["ret_20"] - features["benchmark_ret_20"]
    weekday = features["date"].dt.weekday.astype(float)
    features["weekday_sin"] = np.sin(weekday / 5 * 2 * np.pi)
    features["weekday_cos"] = np.cos(weekday / 5 * 2 * np.pi)
    for key, value in board_feature_values(board_key).items():
        features[key] = value

    horizon = market_spec.primary_horizon
    entry_open = open_price.shift(-1)
    exit_close = close.shift(-horizon)
    entry_tradeable = entry_open.notna()
    exit_tradeable = exit_close.notna()
    limit_fraction = (
        board_standard_limit_fraction(board_key)
        if market_spec.market == "cn"
        else None
    )
    if limit_fraction is not None:
        # Conservatively reject an opening buy near the upper price limit and
        # an exit near the lower limit. Listing-age/ST exceptions are not
        # guessed; uncertain cases remain outside production labels.
        entry_gap = entry_open / close - 1
        exit_previous_close = close.shift(-(horizon - 1))
        exit_move = exit_close / exit_previous_close - 1
        threshold = limit_fraction * 0.965
        entry_tradeable &= entry_gap < threshold
        exit_tradeable &= exit_move > -threshold
    gross_return = exit_close / entry_open - 1
    features["target_return"] = (gross_return - market_spec.round_trip_cost).where(
        entry_tradeable & exit_tradeable
    )
    features["target_up"] = (features["target_return"] > 0).astype(float)
    features.loc[features["target_return"].isna(), "target_up"] = np.nan
    features["entry_open"] = entry_open
    features["exit_close"] = exit_close
    features["current_price"] = close
    features["current_open"] = open_price
    features["current_volatility"] = features["vol_20"] * math.sqrt(horizon)

    numeric_columns = FEATURE_COLUMNS + [
        "target_return",
        "target_up",
        "entry_open",
        "exit_close",
        "current_price",
        "current_open",
        "current_volatility",
    ]
    for column in numeric_columns:
        features[column] = pd.to_numeric(features[column], errors="coerce")
        features.loc[~np.isfinite(features[column]), column] = np.nan

    # Winsorization is fixed and point-in-time safe. Fold-specific scaling is
    # still fitted inside each sklearn Pipeline.
    for column in (
        "ret_1",
        "ret_5",
        "ret_20",
        "ret_60",
        "relative_5",
        "relative_20",
        "ma_gap_20",
        "ma_gap_60",
        "drawdown_60",
    ):
        features[column] = features[column].clip(-0.8, 0.8)
    features["trend_strength"] = features["trend_strength"].clip(-8, 8)
    features["volume_ratio_20"] = features["volume_ratio_20"].clip(-3, 3)
    return features


def frame_data_quality(
    frame: pd.DataFrame,
    *,
    reference_date: pd.Timestamp | None = None,
) -> float:
    if frame.empty:
        return 0.0
    latest = frame.iloc[-1]
    history_score = min(len(frame) / 900, 1.0)
    latest_date = pd.to_datetime(latest.get("date"), errors="coerce")
    if pd.isna(latest_date):
        freshness_score = 0.0
    elif reference_date is None:
        freshness_score = 1.0
    else:
        session_lag = int(
            np.busday_count(
                latest_date.normalize().date(),
                pd.Timestamp(reference_date).normalize().date(),
            )
        )
        freshness_score = 1.0 if session_lag <= 0 else 0.0
    finite_ratio = float(np.isfinite(latest[FEATURE_COLUMNS].astype(float)).mean())
    return max(0.0, min(1.0, history_score * 0.45 + freshness_score * 0.20 + finite_ratio * 0.35))
