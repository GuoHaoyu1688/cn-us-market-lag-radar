"""Dual-market forecasting framework.

The package deliberately keeps market collection, feature construction,
validation, model fitting, payload assembly and the forward ledger separate so
future challenger models can be added without changing the browser contract.
"""

from .market_specs import CN_SPEC, US_SPEC, MarketSpec, classify_cn_board
from .pipeline import build_dual_market_forecasts

__all__ = [
    "CN_SPEC",
    "US_SPEC",
    "MarketSpec",
    "classify_cn_board",
    "build_dual_market_forecasts",
]
