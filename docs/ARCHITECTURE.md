# Architecture

```text
Public market endpoints / RSS
              |
              v
full_market_sector_discovery.py
  dynamic A-share board scan and deduplication
              |
              v
build_market_lag_dashboard.py
  concept mapping, chart cache, V7 lag research model
              |
              +-------------------------+
              |                         |
              v                         v
prediction_model_v6.py          forecasting/pipeline.py
cross-market lag evidence       dual-market stacked forecasts
              |                         |
              v                         v
prediction_forward_ledger.py    forecasting/ledger.py
              \                         /
               +-----------+-----------+
                           v
         output/market_lag_dashboard/data/
                           |
                           v
              static HTML/CSS/JavaScript UI
```

## Trust Boundaries

- Network responses are untrusted and may be missing, stale, malformed, or rate-limited.
- `.env` is local-only. Environment values must never be written into generated JSON.
- Runtime data and ledgers are local-only and ignored by Git.
- The committed demo is deterministic synthetic data and is physically separated under `demo/`.
- The static server binds only to `127.0.0.1` by default.

## Refresh Path

`scripts/refresh_market_lag_dashboard.py` serializes collection and forecasting with a lock. It first rebuilds dynamic concepts, then builds dual-market forecasts, writes an immutable forecast snapshot to the local ignored runtime directory, and records refresh status.

The frontend defaults to `demo/forecasts-v1.json`. Add `?source=live` to read local runtime output from `data/forecasts-v1.json`.
