# Data Sources

The live pipeline currently contains adapters or fallbacks for:

- A-share quotes and history through AKShare-compatible public endpoints and Sina/easyquotation fallbacks
- US daily charts through Yahoo-compatible chart responses with a Nasdaq recent-bar fallback
- Public news and research RSS, including Google News queries and public IBKR Campus feeds
- Optional X API discussion counts, disabled unless the user explicitly enables the paid API path

## Important

This repository does not bundle third-party live market data. Provider names identify adapters, not endorsements or guaranteed services. Endpoints, schemas, quotas, redistribution rights, and availability may change.

Users must review and comply with each provider's terms. For production deployment, replace public scraping-style adapters with licensed feeds and store raw observations with point-in-time metadata.
