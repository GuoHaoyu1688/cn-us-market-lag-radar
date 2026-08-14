from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests


NY_TZ = ZoneInfo("America/New_York")
NASDAQ_ETFS = {"DIA", "IWM", "QQQ", "SPY"}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _existing_usable(
    path: Path,
    minimum_rows: int = 320,
    *,
    max_business_lag: int = 2,
    required_end: str | None = None,
) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) < minimum_rows:
        return False
    latest = pd.to_datetime(
        payload.get("end") or (rows[-1] if rows else {}).get("date"),
        errors="coerce",
    )
    if pd.isna(latest):
        return False
    if required_end:
        required = pd.to_datetime(required_end, errors="coerce")
        if pd.isna(required):
            return False
        return latest.normalize() >= required.normalize()
    today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
    business_lag = len(
        pd.bdate_range(
            latest.normalize(),
            today,
            inclusive="right",
        )
    )
    return business_lag <= max_business_lag


def ensure_cn_chart(
    root: Path,
    symbol: str,
    minimum_rows: int = 320,
    *,
    required_end: str | None = None,
) -> tuple[bool, str]:
    path = root / f"output/market_lag_dashboard/data/charts/cn/{symbol}.json"
    if _existing_usable(path, minimum_rows, required_end=required_end):
        return True, "cache"
    try:
        from a_stock_cli import fetch_history

        frame = fetch_history(symbol, days=1400, adjust="qfq")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if frame is None or len(frame) < minimum_rows:
        return False, f"insufficient rows: {0 if frame is None else len(frame)}"
    mapping = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
    }
    if not set(mapping).issubset(frame.columns):
        return False, "unexpected A-share history columns"
    output = frame[list(mapping)].rename(columns=mapping).copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume"):
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output = output.dropna(subset=list(mapping.values())).sort_values("date")
    rows = output.to_dict(orient="records")
    if len(rows) < minimum_rows:
        return False, f"usable rows below minimum: {len(rows)}"
    if required_end and rows[-1]["date"] < required_end:
        return False, f"latest session {rows[-1]['date']} before required {required_end}"
    source = str(getattr(frame, "attrs", {}).get("source") or "AKShare/Eastmoney")
    _atomic_write(
        path,
        {
            "kind": "cn",
            "symbol": symbol,
            "source": source,
            "period": "最长可用日K（首版上限1400）",
            "start": rows[0]["date"],
            "end": rows[-1]["date"],
            "rows": rows,
        },
    )
    return True, source


def _completed_us_session(
    date_text: str,
    *,
    now: datetime | None = None,
) -> bool:
    local_now = (now or datetime.now(timezone.utc)).astimezone(NY_TZ)
    try:
        session_date = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return False
    if session_date < local_now.date():
        return True
    if session_date > local_now.date():
        return False
    return local_now.timetz().replace(tzinfo=None) >= time(16, 0)


def _number(value: Any) -> float:
    cleaned = str(value or "").replace("$", "").replace(",", "").strip()
    return float(cleaned)


def _merge_chart_rows(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {
        str(row["date"]): row
        for row in primary
        if row.get("date")
    }
    for row in secondary:
        if row.get("date"):
            merged[str(row["date"])] = row
    return [merged[key] for key in sorted(merged)]


def _yahoo_rows(
    result: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    timezone_name = str((result.get("meta") or {}).get("exchangeTimezoneName") or "")
    try:
        exchange_tz = ZoneInfo(timezone_name) if timezone_name else NY_TZ
    except Exception:
        exchange_tz = NY_TZ
    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        try:
            date_text = datetime.fromtimestamp(
                int(timestamp),
                tz=timezone.utc,
            ).astimezone(exchange_tz).strftime("%Y-%m-%d")
            row = {
                "date": date_text,
                "open": float(quote["open"][index]),
                "high": float(quote["high"][index]),
                "low": float(quote["low"][index]),
                "close": float(quote["close"][index]),
                "volume": float(quote["volume"][index] or 0),
            }
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not _completed_us_session(date_text, now=now):
            continue
        if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
            continue
        rows.append(row)
    return _merge_chart_rows(rows, [])


def _nasdaq_recent_rows(
    session: requests.Session,
    symbol: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(NY_TZ)
    from_date = (local_now.date() - timedelta(days=21)).isoformat()
    asset_classes = (
        ("etf", "stocks")
        if symbol in NASDAQ_ETFS
        else ("stocks", "etf")
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    for asset_class in asset_classes:
        try:
            response = session.get(
                f"https://api.nasdaq.com/api/quote/{symbol}/historical",
                params={
                    "assetclass": asset_class,
                    "fromdate": from_date,
                    "limit": 50,
                },
                headers=headers,
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            raw_rows = (
                (((payload.get("data") or {}).get("tradesTable") or {}).get("rows"))
                or []
            )
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            continue
        rows: list[dict[str, Any]] = []
        for raw in raw_rows:
            try:
                date_text = datetime.strptime(
                    str(raw.get("date") or ""),
                    "%m/%d/%Y",
                ).strftime("%Y-%m-%d")
                row = {
                    "date": date_text,
                    "open": _number(raw.get("open")),
                    "high": _number(raw.get("high")),
                    "low": _number(raw.get("low")),
                    "close": _number(raw.get("close")),
                    "volume": _number(raw.get("volume")),
                }
            except (TypeError, ValueError):
                continue
            if not _completed_us_session(date_text, now=now):
                continue
            if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
                continue
            rows.append(row)
        if rows:
            return _merge_chart_rows(rows, [])
    return []


def ensure_us_chart(
    root: Path,
    symbol: str,
    minimum_rows: int = 320,
    *,
    required_end: str | None = None,
    force_refresh: bool = False,
) -> tuple[bool, str]:
    path = root / f"output/market_lag_dashboard/data/charts/us/{symbol}.json"
    if not force_refresh and _existing_usable(
        path,
        minimum_rows,
        required_end=required_end,
    ):
        return True, "cache"
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={
                "range": "5y",
                "interval": "1d",
                "includePrePost": "false",
                "events": "div,splits",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=25,
        )
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [None])[0]
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if not isinstance(result, dict):
        return False, "Yahoo result unavailable"
    rows = _yahoo_rows(result)
    yahoo_end = rows[-1]["date"] if rows else ""
    needs_secondary = force_refresh or (
        bool(required_end) and yahoo_end < str(required_end)
    )
    secondary_rows = (
        _nasdaq_recent_rows(session, symbol)
        if needs_secondary
        else []
    )
    if secondary_rows:
        rows = _merge_chart_rows(rows, secondary_rows)
    if len(rows) < minimum_rows:
        return False, f"usable rows below minimum: {len(rows)}"
    if required_end and rows[-1]["date"] < required_end:
        return False, f"latest session {rows[-1]['date']} before required {required_end}"
    source = (
        "Yahoo chart + Nasdaq historical fallback"
        if secondary_rows
        else "Yahoo chart"
    )
    _atomic_write(
        path,
        {
            "kind": "us",
            "symbol": symbol,
            "source": source,
            "period": "5年日K",
            "start": rows[0]["date"],
            "end": rows[-1]["date"],
            "rows": rows,
        },
    )
    return True, source


def ensure_histories(
    root: Path,
    *,
    cn_symbols: list[str],
    us_symbols: list[str],
    cn_required_end: str | None = None,
    us_required_end: str | None = None,
) -> dict[str, dict[str, str]]:
    status: dict[str, dict[str, str]] = {"cn": {}, "us": {}}
    for symbol in sorted(set(cn_symbols)):
        ok, detail = ensure_cn_chart(root, symbol, required_end=cn_required_end)
        status["cn"][symbol] = "ok" if ok else detail
    for symbol in sorted(set(us_symbols)):
        ok, detail = ensure_us_chart(root, symbol, required_end=us_required_end)
        status["us"][symbol] = "ok" if ok else detail
    return status


def ensure_core_histories(
    root: Path,
    *,
    cn_symbols: list[str],
    us_symbols: list[str],
) -> dict[str, dict[str, str]]:
    return ensure_histories(
        root,
        cn_symbols=cn_symbols,
        us_symbols=us_symbols,
    )
