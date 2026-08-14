# Security Policy

## Supported Version

Only the latest `main` branch is supported.

## Reporting

Please do not publish credentials, account identifiers, brokerage records, private datasets, or exploit details in a public issue. Use GitHub's private vulnerability reporting feature when it is enabled for the repository.

## Local Data Boundary

Runtime data under `output/market_lag_dashboard/data/`, `.env`, logs, connector caches, browser profiles, and brokerage exports are intentionally excluded from Git. The public build never requires an IBKR account and does not read personal positions.

Before every public release, run:

```bash
python scripts/sanitize_check.py
```
