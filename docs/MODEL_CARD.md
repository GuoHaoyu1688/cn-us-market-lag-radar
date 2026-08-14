# Model Card

## Intended Use

The project is a research dashboard for inspecting short-horizon market signals, validation quality, and evidence gaps. It is not intended for unsupervised trading or return guarantees.

## Production Target

- Primary horizon: 5 trading sessions.
- Signal cutoff: completed local market close.
- Earliest execution assumption: next observable market open.
- A-share costs and US costs are modeled separately.
- Predictions outside the valid freeze window do not enter the forward ledger.

## Components

1. Market prior: a shrinkage anchor based on historical positive frequency.
2. Regularized logistic regression: a lower-variance linear challenger.
3. Histogram gradient boosting: a nonlinear challenger.
4. Robust trend model: a return-oriented component converted to directional probability.

Stacking weights are nonnegative and sum to one. Weak challengers may receive zero weight. A separate sigmoid calibrator is fitted after the stacking interval.

## Validation

Chronological purged walk-forward folds create out-of-fold predictions. The timeline is then divided into stacking, calibration, and sealed holdout intervals. The dashboard reports:

- Brier score and Brier skill against the market prior
- Log loss and calibration error
- Return MAE skill against a historical-median baseline
- Empirical prediction-interval coverage
- Sample counts, folds, data quality, and ensemble agreement
- Forward-ledger outcomes frozen before the eligible entry window

## Diagnostic Score

The 0-100 diagnostic score is a linear display metric, not a probability:

```text
D = model evidence (65) + data completeness (15)
  + ensemble agreement (15) + signal separation (5)
```

Model evidence combines direction skill, return skill, probability calibration, and interval coverage. A high score cannot override a failed execution window or a hard evidence gap.

## Cross-Market Lag Model

The lag research module maps concrete supply-chain or industry nodes rather than broad labels. It uses US residual moves relative to a benchmark, A-share residual confirmation, mapping quality, liquidity, costs, tail outcomes, time decay, recent relationship drift, and Benjamini-Hochberg multiple-testing control. Broad fallback mappings cannot trigger a buy-type result.

## Known Limitations

- Public endpoints can revise historical data and omit delisted instruments.
- Industry membership and US-to-CN mappings are imperfect and time-varying.
- Daily bars cannot reproduce intraday tradability, queue priority, or all price-limit effects.
- Corporate actions, suspensions, holidays, and timezone edges can invalidate naive windows.
- Calibration can decay after policy, liquidity, or market-regime changes.
- Multiple comparisons and repeated model revisions can still create research overfitting.

The correct response to weak evidence is abstention, not a more aggressive score transformation.
