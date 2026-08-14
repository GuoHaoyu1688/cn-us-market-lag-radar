from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MarketSpec:
    market: str
    label: str
    timezone: str
    benchmark_symbol: str
    benchmark_chart_ref: str
    round_trip_cost: float
    primary_horizon: int = 5
    purge_sessions: int = 6
    minimum_history: int = 320
    minimum_oos_samples: int = 500
    max_assets: int = 140

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InstrumentSpec:
    code: str
    exchange: str
    board: str
    board_key: str
    eligible: bool
    standard_limit_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CN_SPEC = MarketSpec(
    market="cn",
    label="A股预测",
    timezone="Asia/Shanghai",
    benchmark_symbol="000300.SS",
    benchmark_chart_ref="./data/charts/us/000300.SS.json",
    round_trip_cost=0.0035,
    max_assets=220,
)

US_SPEC = MarketSpec(
    market="us",
    label="美股预测",
    timezone="America/New_York",
    benchmark_symbol="SPY",
    benchmark_chart_ref="./data/charts/us/SPY.json",
    round_trip_cost=0.0015,
    max_assets=120,
)


def classify_cn_board(raw_code: str) -> InstrumentSpec:
    """Classify all currently supported A-share listing boards.

    Board membership is metadata, never an eligibility exclusion.  ST,
    suspension, IPO-age and liquidity checks are intentionally handled as
    point-in-time instrument states elsewhere.
    """

    code = "".join(ch for ch in str(raw_code) if ch.isdigit()).zfill(6)
    if code.startswith(("688", "689")):
        return InstrumentSpec(code, "SSE", "科创板", "star", True, 20.0)
    if code.startswith(("600", "601", "603", "605")):
        return InstrumentSpec(code, "SSE", "沪市主板", "sh_main", True, 10.0)
    if code.startswith(("300", "301")):
        return InstrumentSpec(code, "SZSE", "创业板", "chinext", True, 20.0)
    if code.startswith(("000", "001", "002", "003")):
        return InstrumentSpec(code, "SZSE", "深市主板", "sz_main", True, 10.0)
    if code.startswith(("4", "8", "920")):
        return InstrumentSpec(code, "BSE", "北交所", "bse", True, 30.0)
    return InstrumentSpec(code, "UNKNOWN", "待识别", "unknown", False, None)


def daily_price_limit_pct(
    instrument: InstrumentSpec,
    *,
    is_st: bool = False,
    listing_sessions: int | None = None,
) -> float | None:
    """Return a conservative point-in-time daily limit for validation.

    New listings can have a no-limit phase, so unknown listing age must never
    be used to reject a large move.  This helper is an audit guard, not a trade
    execution engine.
    """

    if not instrument.eligible:
        return None
    if listing_sessions is not None and listing_sessions <= 5:
        return None
    if is_st and instrument.board_key in {"sh_main", "sz_main"}:
        return 5.0
    return instrument.standard_limit_pct


def board_feature_values(board_key: str) -> dict[str, float]:
    return {
        "board_main": float(board_key in {"sh_main", "sz_main"}),
        "board_chinext": float(board_key == "chinext"),
        "board_star": float(board_key == "star"),
        "board_bse": float(board_key == "bse"),
    }


def board_standard_limit_fraction(board_key: str) -> float | None:
    return {
        "sh_main": 0.10,
        "sz_main": 0.10,
        "chinext": 0.20,
        "star": 0.20,
        "bse": 0.30,
    }.get(board_key)
