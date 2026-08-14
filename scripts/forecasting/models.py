from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import HuberRegressor, LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler, StandardScaler

from .features import FEATURE_COLUMNS
from .market_specs import MarketSpec


EPSILON = 1e-6
MODEL_IDS = ("prior", "elastic_net", "gradient_boosting", "robust_trend")
MODEL_LABELS = {
    "prior": "市场先验",
    "elastic_net": "正则逻辑回归",
    "gradient_boosting": "梯度提升树",
    "robust_trend": "稳健趋势模型",
}


def clip_probability(values: np.ndarray | float) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), 0.01, 0.99)


def expected_calibration_error(labels: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    if not len(labels):
        return float("nan")
    edges = np.linspace(0, 1, bins + 1)
    total = len(labels)
    error = 0.0
    for left, right in zip(edges[:-1], edges[1:], strict=False):
        mask = (probabilities >= left) & (
            probabilities <= right if right == 1 else probabilities < right
        )
        if not mask.any():
            continue
        error += mask.sum() / total * abs(float(labels[mask].mean()) - float(probabilities[mask].mean()))
    return float(error)


def metric_bundle(labels: np.ndarray, probabilities: np.ndarray, baseline: np.ndarray) -> dict[str, float]:
    probabilities = clip_probability(probabilities)
    baseline = clip_probability(baseline)
    brier = float(brier_score_loss(labels, probabilities))
    baseline_brier = float(brier_score_loss(labels, baseline))
    return {
        "samples": int(len(labels)),
        "positive_rate": float(labels.mean()) if len(labels) else float("nan"),
        "brier": brier,
        "baseline_brier": baseline_brier,
        "brier_skill": float(1 - brier / baseline_brier) if baseline_brier > 0 else float("nan"),
        "log_loss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "calibration_error": expected_calibration_error(labels, probabilities),
    }


def _safe_training_sample(frame: pd.DataFrame, max_rows: int = 100_000) -> pd.DataFrame:
    if len(frame) <= max_rows:
        return frame
    return frame.sample(max_rows, random_state=42).sort_values(["date", "symbol"])


class RobustTrendModel:
    def __init__(self, prior_strength: float = 30.0) -> None:
        self.prior_strength = prior_strength
        self.edges: np.ndarray | None = None
        self.global_rate = 0.5
        self.lookup: dict[tuple[int, int, int], float] = {}

    @staticmethod
    def _score(frame: pd.DataFrame) -> np.ndarray:
        return (
            frame["trend_strength"].to_numpy(dtype=float) * 0.55
            + frame["relative_20"].to_numpy(dtype=float) * 4.0
            + frame["relative_5"].to_numpy(dtype=float) * 2.0
        )

    def fit(self, frame: pd.DataFrame, labels: np.ndarray) -> "RobustTrendModel":
        scores = self._score(frame)
        self.global_rate = float(np.mean(labels)) if len(labels) else 0.5
        self.edges = np.unique(np.quantile(scores, [0.2, 0.4, 0.6, 0.8]))
        buckets = np.digitize(scores, self.edges)
        regimes = frame["regime_up"].fillna(0).astype(int).to_numpy()
        high_vol = frame["regime_high_vol"].fillna(0).astype(int).to_numpy()
        for bucket in range(5):
            for regime in (0, 1):
                for volatile in (0, 1):
                    mask = (buckets == bucket) & (regimes == regime) & (high_vol == volatile)
                    successes = float(labels[mask].sum())
                    trials = int(mask.sum())
                    posterior = (
                        successes + self.global_rate * self.prior_strength
                    ) / (trials + self.prior_strength)
                    self.lookup[(bucket, regime, volatile)] = float(posterior)
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        if self.edges is None:
            return np.full(len(frame), self.global_rate)
        buckets = np.digitize(self._score(frame), self.edges)
        regimes = frame["regime_up"].fillna(0).astype(int).to_numpy()
        high_vol = frame["regime_high_vol"].fillna(0).astype(int).to_numpy()
        values = [
            self.lookup.get((int(bucket), int(regime), int(volatile)), self.global_rate)
            for bucket, regime, volatile in zip(buckets, regimes, high_vol, strict=False)
        ]
        return clip_probability(np.asarray(values))


def build_elastic_net() -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    solver="saga",
                    l1_ratio=0.15,
                    C=0.25,
                    max_iter=350,
                    tol=1e-3,
                    random_state=42,
                ),
            ),
        ]
    )


def build_gradient_boosting() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        loss="log_loss",
        learning_rate=0.045,
        max_iter=120,
        max_leaf_nodes=15,
        min_samples_leaf=180,
        l2_regularization=3.0,
        # sklearn's automatic early-stopping split is row-random and can split
        # one market date's cross-section. Keep the iteration budget fixed so
        # every validation boundary remains explicitly date-based.
        early_stopping=False,
        random_state=42,
    )


def date_walk_forward_splits(
    frame: pd.DataFrame,
    *,
    n_splits: int = 4,
    purge_sessions: int = 6,
) -> list[tuple[np.ndarray, np.ndarray]]:
    dates = np.array(sorted(pd.to_datetime(frame["date"]).dt.normalize().unique()))
    if len(dates) < 500:
        return []
    start_fraction = 0.52
    boundaries = np.linspace(start_fraction, 1.0, n_splits + 1)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    normalized = pd.to_datetime(frame["date"]).dt.normalize().to_numpy()
    for fold in range(n_splits):
        test_start_idx = int(len(dates) * boundaries[fold])
        test_end_idx = int(len(dates) * boundaries[fold + 1])
        if test_end_idx <= test_start_idx:
            continue
        train_end_idx = test_start_idx - purge_sessions
        if train_end_idx < 260:
            continue
        train_end_date = dates[train_end_idx]
        test_start_date = dates[test_start_idx]
        test_end_date = dates[min(test_end_idx - 1, len(dates) - 1)]
        train_idx = np.flatnonzero(normalized <= train_end_date)
        test_idx = np.flatnonzero((normalized >= test_start_date) & (normalized <= test_end_date))
        if len(train_idx) and len(test_idx):
            splits.append((train_idx, test_idx))
    return splits


def _fit_base_models(
    train: pd.DataFrame,
) -> tuple[float, Pipeline, HistGradientBoostingClassifier, RobustTrendModel]:
    sampled = _safe_training_sample(train)
    labels = sampled["target_up"].astype(int).to_numpy()
    prior = float((labels.sum() + 20) / (len(labels) + 40))
    elastic = build_elastic_net()
    gradient = build_gradient_boosting()
    trend = RobustTrendModel()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=ConvergenceWarning)
        warnings.simplefilter("ignore", category=FutureWarning)
        elastic.fit(sampled[FEATURE_COLUMNS], labels)
    gradient.fit(sampled[FEATURE_COLUMNS], labels)
    trend.fit(sampled, labels)
    return prior, elastic, gradient, trend


def _predict_base_models(
    frame: pd.DataFrame,
    fitted: tuple[float, Pipeline, HistGradientBoostingClassifier, RobustTrendModel],
) -> dict[str, np.ndarray]:
    prior, elastic, gradient, trend = fitted
    return {
        "prior": np.full(len(frame), prior),
        "elastic_net": elastic.predict_proba(frame[FEATURE_COLUMNS])[:, 1],
        "gradient_boosting": gradient.predict_proba(frame[FEATURE_COLUMNS])[:, 1],
        "robust_trend": trend.predict_proba(frame),
    }


def fit_stacking_weights(predictions: pd.DataFrame, labels: np.ndarray) -> dict[str, float]:
    matrix = np.column_stack([clip_probability(predictions[key].to_numpy()) for key in MODEL_IDS])
    initial = np.full(len(MODEL_IDS), 1 / len(MODEL_IDS))

    def objective(weights: np.ndarray) -> float:
        combined = clip_probability(matrix @ weights)
        return float(log_loss(labels, combined, labels=[0, 1])) + float(np.sum(weights**2)) * 0.002

    bounds = [(0.10, 0.80)] + [(0.0, 0.75)] * (len(MODEL_IDS) - 1)
    result = minimize(
        objective,
        initial,
        method="SLSQP",
        bounds=bounds,
        constraints={"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1)},
        options={"maxiter": 300, "ftol": 1e-10},
    )
    weights = result.x if result.success else initial

    # A challenger that cannot beat the prior on the stacking segment gets no
    # weight.  The prior keeps at least 10% as a shrinkage anchor.
    prior_brier = float(brier_score_loss(labels, matrix[:, 0]))
    for index in range(1, len(MODEL_IDS)):
        challenger_brier = float(brier_score_loss(labels, matrix[:, index]))
        if challenger_brier >= prior_brier:
            weights[index] = 0.0
    weights[0] = max(weights[0], 0.10)
    # Failed challengers donate their released mass to the shrinkage anchor.
    # Renormalising every survivor would let a challenger exceed its 75% cap.
    weights[0] += max(0.0, 1.0 - float(weights.sum()))
    if weights[0] > 0.80 and np.any(weights[1:] > 0):
        overflow = weights[0] - 0.80
        weights[0] = 0.80
        active = np.flatnonzero(weights[1:] > 0) + 1
        for index in active:
            room = max(0.0, 0.75 - weights[index])
            addition = min(room, overflow)
            weights[index] += addition
            overflow -= addition
            if overflow <= 1e-12:
                break
        weights[0] += max(0.0, overflow)
    weights = weights / weights.sum()
    return {model_id: float(weight) for model_id, weight in zip(MODEL_IDS, weights, strict=False)}


def weighted_predictions(predictions: pd.DataFrame, weights: dict[str, float]) -> np.ndarray:
    return clip_probability(
        sum(predictions[model_id].to_numpy(dtype=float) * weights.get(model_id, 0) for model_id in MODEL_IDS)
    )


@dataclass
class SigmoidCalibrator:
    intercept: float = 0.0
    coefficient: float = 1.0

    def fit(self, raw: np.ndarray, labels: np.ndarray) -> "SigmoidCalibrator":
        logits = np.log(clip_probability(raw) / (1 - clip_probability(raw))).reshape(-1, 1)
        model = LogisticRegression(C=0.3, solver="lbfgs", max_iter=300, random_state=42)
        model.fit(logits, labels)
        self.intercept = float(model.intercept_[0])
        self.coefficient = float(model.coef_[0][0])
        return self

    def predict(self, raw: np.ndarray) -> np.ndarray:
        logits = np.log(clip_probability(raw) / (1 - clip_probability(raw)))
        calibrated = 1 / (1 + np.exp(-(self.intercept + self.coefficient * logits)))
        return clip_probability(calibrated)

    def to_dict(self) -> dict[str, float]:
        return {"intercept": self.intercept, "coefficient": self.coefficient}


def fit_return_model(train: pd.DataFrame, max_rows: int = 90_000) -> Pipeline:
    sampled = _safe_training_sample(train, max_rows=max_rows)
    pipeline = Pipeline(
        [
            ("scale", RobustScaler(quantile_range=(10, 90))),
            ("model", HuberRegressor(epsilon=1.5, alpha=0.001, max_iter=400)),
        ]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=ConvergenceWarning)
        warnings.simplefilter("ignore", category=FutureWarning)
        pipeline.fit(sampled[FEATURE_COLUMNS], sampled["target_return"].to_numpy(dtype=float))
    return pipeline


def empirical_interval(
    expected_returns: np.ndarray,
    current_volatility: np.ndarray,
    residuals: np.ndarray,
    residual_volatility: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    q10, q50, q90 = residual_interval_quantiles(residuals, residual_volatility)
    scale = np.clip(current_volatility, 0.005, 0.35)
    return (
        expected_returns + q10 * scale,
        expected_returns + q50 * scale,
        expected_returns + q90 * scale,
    )


def residual_interval_quantiles(
    residuals: np.ndarray,
    residual_volatility: np.ndarray,
) -> tuple[float, float, float]:
    usable = np.isfinite(residuals) & np.isfinite(residual_volatility) & (residual_volatility > 0)
    standardized = residuals[usable] / residual_volatility[usable]
    if len(standardized) < 200:
        standardized = residuals[np.isfinite(residuals)]
    if not len(standardized):
        standardized = np.array([-1.28, 0.0, 1.28])
    q10, q50, q90 = np.quantile(standardized, [0.10, 0.50, 0.90])
    return float(q10), float(q50), float(q90)


def build_market_model(
    training_frame: pd.DataFrame,
    current_frame: pd.DataFrame,
    market_spec: MarketSpec,
) -> dict[str, Any]:
    splits = date_walk_forward_splits(
        training_frame,
        purge_sessions=market_spec.purge_sessions,
    )
    if len(splits) < 3:
        raise RuntimeError(f"{market_spec.market}: insufficient dates for purged walk-forward")

    oof_rows: list[pd.DataFrame] = []
    for fold, (train_idx, test_idx) in enumerate(splits, 1):
        train = training_frame.iloc[train_idx]
        test = training_frame.iloc[test_idx]
        fitted = _fit_base_models(train)
        predicted = _predict_base_models(test, fitted)
        return_model = fit_return_model(train)
        fold_frame = test[["date", "symbol", "target_up", "target_return", "current_volatility"]].copy()
        for model_id, values in predicted.items():
            fold_frame[model_id] = clip_probability(values)
        fold_frame["return_prediction"] = return_model.predict(test[FEATURE_COLUMNS])
        fold_frame["fold"] = fold
        oof_rows.append(fold_frame)

    oof = pd.concat(oof_rows, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
    # Keep stacking, probability calibration and final validation on strictly
    # ordered, non-overlapping OOF segments. This prevents the calibrator from
    # being scored on observations that helped fit either it or the weights.
    oof_dates = pd.Index(oof["date"].drop_duplicates().sort_values())
    stacking_date_index = max(int(len(oof_dates) * 0.45), 1)
    calibration_date_index = max(int(len(oof_dates) * 0.68), stacking_date_index + 1)
    calibration_date_index = min(calibration_date_index, len(oof_dates) - 1)
    stacking_cutoff = oof_dates[stacking_date_index - 1]
    calibration_cutoff = oof_dates[calibration_date_index - 1]
    stacking = oof[oof["date"] <= stacking_cutoff].copy()
    calibration = oof[
        (oof["date"] > stacking_cutoff) & (oof["date"] <= calibration_cutoff)
    ].copy()
    holdout = oof[oof["date"] > calibration_cutoff].copy()
    weights = fit_stacking_weights(
        stacking[list(MODEL_IDS)],
        stacking["target_up"].astype(int).to_numpy(),
    )
    calibration_raw = weighted_predictions(calibration[list(MODEL_IDS)], weights)
    calibrator = SigmoidCalibrator().fit(
        calibration_raw,
        calibration["target_up"].astype(int).to_numpy(),
    )
    holdout_raw = weighted_predictions(holdout[list(MODEL_IDS)], weights)
    holdout_calibrated = calibrator.predict(holdout_raw)
    validation = metric_bundle(
        holdout["target_up"].astype(int).to_numpy(),
        holdout_calibrated,
        holdout["prior"].to_numpy(dtype=float),
    )

    return_residuals = holdout["target_return"].to_numpy(dtype=float) - holdout[
        "return_prediction"
    ].to_numpy(dtype=float)
    calibration_return_residuals = (
        calibration["target_return"].to_numpy(dtype=float)
        - calibration["return_prediction"].to_numpy(dtype=float)
    )
    return_mae = float(np.nanmean(np.abs(return_residuals)))
    baseline_return = float(stacking["target_return"].median())
    baseline_mae = float(
        np.nanmean(np.abs(holdout["target_return"].to_numpy(dtype=float) - baseline_return))
    )
    validation["return_mae"] = return_mae
    validation["return_baseline_mae"] = baseline_mae
    validation["return_skill"] = 1 - return_mae / baseline_mae if baseline_mae > 0 else float("nan")

    full_models = _fit_base_models(training_frame)
    current_base = _predict_base_models(current_frame, full_models)
    current_prediction_frame = pd.DataFrame(current_base)
    current_raw = weighted_predictions(current_prediction_frame, weights)
    current_calibrated = calibrator.predict(current_raw)
    return_model = fit_return_model(training_frame)
    expected_return = return_model.predict(current_frame[FEATURE_COLUMNS])
    q10, q50, q90 = empirical_interval(
        expected_return,
        current_frame["current_volatility"].to_numpy(dtype=float),
        calibration_return_residuals,
        calibration["current_volatility"].to_numpy(dtype=float),
    )
    interval_q10, _, interval_q90 = residual_interval_quantiles(
        calibration_return_residuals,
        calibration["current_volatility"].to_numpy(dtype=float),
    )
    holdout_scale = np.clip(
        holdout["current_volatility"].to_numpy(dtype=float),
        0.005,
        0.35,
    )
    holdout_return_prediction = holdout["return_prediction"].to_numpy(dtype=float)
    holdout_target_return = holdout["target_return"].to_numpy(dtype=float)
    coverage = float(
        np.mean(
            (
                holdout_target_return
                >= holdout_return_prediction + interval_q10 * holdout_scale
            )
            & (
                holdout_target_return
                <= holdout_return_prediction + interval_q90 * holdout_scale
            )
        )
    )
    validation["empirical_interval_coverage"] = coverage
    validation["interval_target"] = 0.80
    validation["folds"] = len(splits)
    validation["stacking_start"] = stacking["date"].min().strftime("%Y-%m-%d")
    validation["stacking_end"] = stacking["date"].max().strftime("%Y-%m-%d")
    validation["calibration_start"] = calibration["date"].min().strftime("%Y-%m-%d")
    validation["calibration_end"] = calibration["date"].max().strftime("%Y-%m-%d")
    validation["holdout_start"] = holdout["date"].min().strftime("%Y-%m-%d")
    validation["holdout_end"] = holdout["date"].max().strftime("%Y-%m-%d")

    component_metrics = {}
    holdout_labels = holdout["target_up"].astype(int).to_numpy()
    holdout_prior = holdout["prior"].to_numpy(dtype=float)
    for model_id in MODEL_IDS:
        component_metrics[model_id] = metric_bundle(
            holdout_labels,
            holdout[model_id].to_numpy(dtype=float),
            holdout_prior,
        )

    return {
        "weights": weights,
        "calibrator": calibrator.to_dict(),
        "validation": validation,
        "component_metrics": component_metrics,
        "current_probability": current_calibrated,
        "current_raw_probability": current_raw,
        "current_component_probabilities": current_base,
        "expected_return": expected_return,
        "q10": q10,
        "q50": q50,
        "q90": q90,
        "oof_rows": len(oof),
        "training_rows": len(training_frame),
    }
