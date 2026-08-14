#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "market_lag_dashboard"
DASHBOARD_PATH = OUTPUT / "data" / "dashboard.json"
ANALYSIS_PATH = OUTPUT / "data" / "a-share-analysis.json"
ANALYSIS_JS_PATH = OUTPUT / "data" / "a-share-analysis.js"
SH_TZ = ZoneInfo("Asia/Shanghai")


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def as_float(value: object, default: float | None = None) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except Exception:
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def round_or_none(value: object, digits: int = 2) -> float | None:
    parsed = as_float(value)
    if parsed is None:
        return None
    return round(parsed, digits)


def avg(values: list[object], default: float | None = None) -> float | None:
    parsed = [as_float(value) for value in values]
    nums = [value for value in parsed if value is not None]
    if not nums:
        return default
    return mean(nums)


def med(values: list[object], default: float | None = None) -> float | None:
    parsed = [as_float(value) for value in values]
    nums = [value for value in parsed if value is not None]
    if not nums:
        return default
    return median(nums)


def stdev(values: list[object]) -> float:
    nums = [as_float(value) for value in values]
    nums = [value for value in nums if value is not None]
    if len(nums) < 2:
        return 0.0
    m = mean(nums)
    return math.sqrt(sum((value - m) ** 2 for value in nums) / (len(nums) - 1))


def pct_change(candles: list[dict], window: int) -> float | None:
    if len(candles) <= window:
        return None
    latest = as_float(candles[-1].get("close"))
    base = as_float(candles[-1 - window].get("close"))
    if latest is None or base is None or base <= 0:
        return None
    return (latest / base - 1) * 100


def moving_average(candles: list[dict], window: int) -> float | None:
    if len(candles) < window:
        return None
    values = [as_float(row.get("close")) for row in candles[-window:]]
    values = [value for value in values if value is not None]
    if len(values) < max(3, window // 2):
        return None
    return mean(values)


def daily_return_series(candles: list[dict], window: int = 20) -> list[float]:
    rows = candles[-(window + 1) :]
    out: list[float] = []
    for prev, cur in zip(rows, rows[1:]):
        prev_close = as_float(prev.get("close"))
        cur_close = as_float(cur.get("close"))
        if prev_close is None or cur_close is None or prev_close <= 0:
            continue
        out.append((cur_close / prev_close - 1) * 100)
    return out


def trend_metrics(company: dict) -> dict:
    candles = [row for row in company.get("candles") or company.get("spark") or [] if as_float(row.get("close"))]
    latest_close = as_float(company.get("price")) or (as_float(candles[-1].get("close")) if candles else None)
    ret_1d = as_float(company.get("change"))
    ret_5d = pct_change(candles, 5)
    ret_20d = pct_change(candles, 20)
    ret_60d = pct_change(candles, 60)
    ma20 = moving_average(candles, 20)
    ma60 = moving_average(candles, 60)
    ma20_prev = moving_average(candles[:-5], 20) if len(candles) >= 25 else None
    ma20_slope = ((ma20 / ma20_prev - 1) * 100) if ma20 and ma20_prev and ma20_prev > 0 else 0.0
    high20 = max([as_float(row.get("high")) or as_float(row.get("close")) or 0 for row in candles[-20:]] or [0])
    drawdown20 = ((latest_close / high20 - 1) * 100) if latest_close and high20 > 0 else None
    volumes = [as_float(row.get("volume")) for row in candles[-21:-1]]
    volumes = [value for value in volumes if value is not None and value > 0]
    latest_volume = as_float(candles[-1].get("volume")) if candles else None
    volume_ratio = (latest_volume / mean(volumes)) if latest_volume and volumes else None
    returns20 = daily_return_series(candles, 20)
    volatility20 = stdev(returns20)
    amount = as_float(company.get("amount")) or 0.0
    liquidity_confirm = as_float(company.get("liquidity_confirm_score")) or 0.0
    mapping_confidence = as_float(company.get("mapping_confidence")) or 45.0
    overheat = as_float(company.get("overheat_penalty")) or 0.0
    risk_flags = company.get("risk_flags") or []

    trend_score = clamp(
        50
        + (ret_5d or 0) * 2.8
        + (ret_20d or 0) * 1.1
        + (ret_60d or 0) * 0.35
        + (8 if latest_close and ma20 and latest_close >= ma20 else -8)
        + (6 if latest_close and ma60 and latest_close >= ma60 else -5)
        + ma20_slope * 1.2
        - max(-(drawdown20 or 0), 0) * 0.6,
    )
    amount_score = clamp((math.log10(max(amount, 1) / 50_000_000) + 1) * 26, 0, 100)
    volume_score = clamp(45 + ((volume_ratio or 1) - 1) * 38 + liquidity_confirm * 4.5, 0, 100)
    liquidity_score = clamp(amount_score * 0.48 + volume_score * 0.52)
    momentum_score = clamp(50 + (ret_1d or 0) * 4.5 + (ret_5d or 0) * 2.4 + (ret_20d or 0) * 0.85)
    position_score = clamp(72 + (drawdown20 or -8) * 1.1 - max((ret_5d or 0) - 18, 0) * 1.4)
    risk_penalty = (
        overheat * 0.38
        + (9 if any("涨停" in str(item) for item in risk_flags) else 0)
        + (7 if any("成交偏薄" in str(item) for item in risk_flags) else 0)
        + max(-((ret_1d or 0) + 3), 0) * 3.2
        + (12 if (ret_1d or 0) <= -8.5 else 0)
        + max((ret_5d or 0) - 28, 0) * 0.45
        + max(volatility20 - 6, 0) * 2.1
    )
    internal_score = clamp(
        trend_score * 0.31
        + liquidity_score * 0.23
        + momentum_score * 0.18
        + position_score * 0.13
        + mapping_confidence * 0.15
        - risk_penalty
    )
    return {
        "latestDate": candles[-1].get("date") if candles else company.get("traded_at"),
        "ret1d": round_or_none(ret_1d),
        "ret5d": round_or_none(ret_5d),
        "ret20d": round_or_none(ret_20d),
        "ret60d": round_or_none(ret_60d),
        "ma20": round_or_none(ma20),
        "ma60": round_or_none(ma60),
        "aboveMa20": bool(latest_close and ma20 and latest_close >= ma20),
        "aboveMa60": bool(latest_close and ma60 and latest_close >= ma60),
        "drawdown20": round_or_none(drawdown20),
        "volumeRatio": round_or_none(volume_ratio, 2),
        "volatility20": round_or_none(volatility20),
        "trendScore": round(trend_score, 1),
        "liquidityScore": round(liquidity_score, 1),
        "momentumScore": round(momentum_score, 1),
        "positionScore": round(position_score, 1),
        "riskPenalty": round(risk_penalty, 1),
        "internalScore": round(internal_score, 1),
        "spark": (company.get("spark") or candles[-20:])[-20:],
    }


def company_key_payload(company: dict, concepts: list[dict]) -> dict:
    metrics = trend_metrics(company)
    concept_names = [item["short_name"] for item in concepts[:4]]
    risk_flags = company.get("risk_flags") or []
    return {
        "code": company.get("code"),
        "name": company.get("name"),
        "market": company.get("market"),
        "role": company.get("role"),
        "concepts": concept_names,
        "conceptIds": [item["id"] for item in concepts],
        "price": round_or_none(company.get("price")),
        "amount": round_or_none(company.get("amount"), 0),
        "amountLabel": company.get("amount_label"),
        "mappingConfidence": round_or_none(company.get("mapping_confidence"), 0),
        "mappingQuality": company.get("mapping_quality"),
        "riskFlags": risk_flags,
        "reason": company.get("reason"),
        "tradability": company.get("tradability"),
        "chartRef": company.get("chart_ref"),
        "metrics": metrics,
    }


def concept_score(concept: dict, stock_rows: list[dict]) -> dict:
    metrics = [row["metrics"] for row in stock_rows]
    n = len(metrics)
    if not n:
        return {}
    up1 = sum(1 for item in metrics if (item.get("ret1d") or 0) > 0) / n
    up5 = sum(1 for item in metrics if (item.get("ret5d") or 0) > 0) / n
    above20 = sum(1 for item in metrics if item.get("aboveMa20")) / n
    above60 = sum(1 for item in metrics if item.get("aboveMa60")) / n
    ret1_values = [item.get("ret1d") for item in metrics]
    ret5_values = [item.get("ret5d") for item in metrics]
    ret20_values = [item.get("ret20d") for item in metrics]
    breadth_score = clamp(up1 * 26 + up5 * 28 + above20 * 32 + above60 * 14)
    momentum_score = clamp(50 + (avg(ret1_values, 0) or 0) * 4 + (avg(ret5_values, 0) or 0) * 2.2 + (med(ret20_values, 0) or 0) * 0.95)
    liquidity_score = avg([item.get("liquidityScore") for item in metrics], 42) or 42
    trend_score = avg([item.get("trendScore") for item in metrics], 42) or 42
    consistency_score = clamp(96 - stdev(ret1_values) * 5.8 - stdev(ret5_values) * 2.1)
    risk_penalty = avg([item.get("riskPenalty") for item in metrics], 0) or 0
    hot_count = sum(1 for row in stock_rows if any("涨停" in str(flag) for flag in row.get("riskFlags") or []))
    weak_count = sum(1 for item in metrics if (item.get("ret1d") or 0) < -2)
    risk_penalty += hot_count * 2.2 + weak_count * 1.5
    internal_score = clamp(
        breadth_score * 0.29
        + momentum_score * 0.23
        + liquidity_score * 0.19
        + trend_score * 0.16
        + consistency_score * 0.13
        - risk_penalty
    )
    if internal_score >= 75:
        state = "内生共振"
        action = "可优先复核"
    elif internal_score >= 62:
        state = "资金确认"
        action = "观察放量延续"
    elif internal_score >= 48:
        state = "结构分化"
        action = "只看龙头"
    else:
        state = "弱轮动"
        action = "等待修复"
    return {
        "score": round(internal_score, 1),
        "state": state,
        "action": action,
        "breadthScore": round(breadth_score, 1),
        "momentumScore": round(momentum_score, 1),
        "liquidityScore": round(liquidity_score, 1),
        "trendScore": round(trend_score, 1),
        "consistencyScore": round(consistency_score, 1),
        "riskPenalty": round(risk_penalty, 1),
        "up1dRatio": round(up1 * 100, 1),
        "up5dRatio": round(up5 * 100, 1),
        "aboveMa20Ratio": round(above20 * 100, 1),
        "aboveMa60Ratio": round(above60 * 100, 1),
        "avgRet1d": round_or_none(avg(ret1_values)),
        "avgRet5d": round_or_none(avg(ret5_values)),
        "medianRet20d": round_or_none(med(ret20_values)),
        "hotCount": hot_count,
        "weakCount": weak_count,
    }


def build_payload() -> dict:
    dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    stock_registry: dict[str, dict] = {}
    stock_concepts: dict[str, list[dict]] = defaultdict(list)
    concept_stock_codes: dict[str, list[str]] = defaultdict(list)

    for rank, concept in enumerate(dashboard.get("concepts") or [], 1):
        concept_meta = {
            "id": concept.get("id"),
            "name": concept.get("name"),
            "short_name": concept.get("short_name"),
            "rank": rank,
            "dynamic": bool(concept.get("dynamic")),
        }
        for company in concept.get("cn", {}).get("companies") or []:
            code = str(company.get("code") or "")
            if not code.startswith(("600", "601", "603", "605", "000", "001", "002")):
                continue
            current = stock_registry.get(code)
            if current is None or (as_float(company.get("mapping_confidence")) or 0) > (as_float(current.get("mapping_confidence")) or 0):
                stock_registry[code] = company
            if not any(item["id"] == concept_meta["id"] for item in stock_concepts[code]):
                stock_concepts[code].append(concept_meta)
            if code not in concept_stock_codes[concept_meta["id"]]:
                concept_stock_codes[concept_meta["id"]].append(code)

    stock_rows = {
        code: company_key_payload(company, stock_concepts[code])
        for code, company in stock_registry.items()
    }
    concepts: list[dict] = []
    for rank, concept in enumerate(dashboard.get("concepts") or [], 1):
        rows = [stock_rows[code] for code in concept_stock_codes.get(concept.get("id"), []) if code in stock_rows]
        if not rows:
            continue
        scored = concept_score(concept, rows)
        top_rows = sorted(rows, key=lambda row: row["metrics"].get("internalScore") or 0, reverse=True)
        concepts.append(
            {
                "id": concept.get("id"),
                "name": concept.get("name"),
                "shortName": concept.get("short_name"),
                "rankInCrossMarket": rank,
                "dynamic": bool(concept.get("dynamic")),
                "driver": concept.get("underlying_driver"),
                "trigger": concept.get("trigger"),
                "score": scored.get("score"),
                "state": scored.get("state"),
                "action": scored.get("action"),
                "metrics": scored,
                "companies": top_rows,
                "topCompanies": top_rows[:6],
            }
        )
    concepts.sort(key=lambda item: item.get("score") or 0, reverse=True)
    for index, item in enumerate(concepts, 1):
        item["rank"] = index

    stocks = sorted(stock_rows.values(), key=lambda row: row["metrics"].get("internalScore") or 0, reverse=True)
    for index, item in enumerate(stocks, 1):
        item["rank"] = index

    all_metrics = [item["metrics"] for item in stocks]
    stock_count = len(stocks)
    up1 = sum(1 for item in all_metrics if (item.get("ret1d") or 0) > 0)
    up5 = sum(1 for item in all_metrics if (item.get("ret5d") or 0) > 0)
    above20 = sum(1 for item in all_metrics if item.get("aboveMa20"))
    above60 = sum(1 for item in all_metrics if item.get("aboveMa60"))
    total_amount = sum(as_float(item.get("amount")) or 0 for item in stocks)
    hot_count = sum(1 for item in stocks if any("涨停" in str(flag) for flag in item.get("riskFlags") or []))
    weak_count = sum(1 for item in all_metrics if (item.get("ret1d") or 0) < -2)
    breadth = up1 / stock_count if stock_count else 0
    trend_breadth = above20 / stock_count if stock_count else 0
    if breadth >= 0.58 and trend_breadth >= 0.52:
        regime = "内生扩散"
    elif concepts and (concepts[0].get("score") or 0) >= 65 and breadth < 0.52:
        regime = "结构轮动"
    elif breadth <= 0.42 and trend_breadth <= 0.42:
        regime = "防守观察"
    else:
        regime = "震荡筛选"

    return {
        "schemaVersion": "a-share-only-v1",
        "modelVersion": "a-share-internal-rotation-v1-20260702",
        "builtAtShanghai": datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "sourceDashboardGeneratedAt": dashboard.get("generated_at_shanghai"),
        "marketClock": dashboard.get("market_clock") or {},
        "summary": {
            "title": "A股内生行业轮动分析",
            "thesis": f"本页不使用美股领先因子，改用A股内部的广度、成交、趋势、风险和行业一致性排序。当前样本池 {stock_count} 只沪深京全板块映射股，市场状态为“{regime}”。",
            "method": "先从现有细分供应链池抽取沪深京全板块公司，再对每只股票计算1/5/20/60日收益、20/60日均线、20日回撤、成交额/量能、波动和风险标签；板块得分由上涨广度、趋势质量、成交确认、行业一致性和过热惩罚组成。",
            "risk": "这是A股内部研究排序，不构成投资建议；高分代表更值得复核公告、成交和基本面，不代表必然上涨。",
        },
        "market": {
            "regime": regime,
            "stockCount": stock_count,
            "conceptCount": len(concepts),
            "up1dCount": up1,
            "up5dCount": up5,
            "aboveMa20Count": above20,
            "aboveMa60Count": above60,
            "up1dRatio": round(breadth * 100, 1),
            "up5dRatio": round((up5 / stock_count * 100) if stock_count else 0, 1),
            "aboveMa20Ratio": round(trend_breadth * 100, 1),
            "aboveMa60Ratio": round((above60 / stock_count * 100) if stock_count else 0, 1),
            "avgRet1d": round_or_none(avg([item.get("ret1d") for item in all_metrics])),
            "medianRet5d": round_or_none(med([item.get("ret5d") for item in all_metrics])),
            "totalAmount": round(total_amount, 0),
            "hotCount": hot_count,
            "weakCount": weak_count,
        },
        "modelCards": [
            {"name": "广度", "weight": "29%", "desc": "板块内上涨比例、5日上涨比例、站上20/60日均线比例。"},
            {"name": "动量", "weight": "23%", "desc": "1日、5日、20日收益的组合，避免只看单日异动。"},
            {"name": "成交", "weight": "19%", "desc": "成交额、量比和既有流动性确认，用于区分真资金和无量拉升。"},
            {"name": "趋势", "weight": "16%", "desc": "均线位置、20日回撤和中期趋势斜率。"},
            {"name": "一致性", "weight": "13%", "desc": "板块内个股涨跌分散度，分散过大说明轮动不纯。"},
            {"name": "风险惩罚", "weight": "扣分", "desc": "涨停追高、成交偏薄、短期过热和波动过大都会扣分。"},
        ],
        "concepts": concepts,
        "stocks": stocks[:80],
    }


def main() -> int:
    payload = build_payload()
    ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ANALYSIS_JS_PATH.write_text(
        "window.__A_SHARE_ONLY_ANALYSIS__ = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(ANALYSIS_PATH)
    print(f"a-share concepts={len(payload['concepts'])} stocks={payload['market']['stockCount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
