#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "market_lag_dashboard"
DASHBOARD_PATH = OUTPUT / "data" / "dashboard.json"
STATUS_PATH = OUTPUT / "data" / "latest_refresh_status.json"
MOBILE_JSON_PATH = OUTPUT / "data" / "mobile-summary.json"
MOBILE_JS_PATH = OUTPUT / "data" / "mobile-summary.js"
SH_TZ = ZoneInfo("Asia/Shanghai")


def as_float(value: object, default: float | None = None) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except Exception:
        return default
    return parsed


def round_or_none(value: object, digits: int = 2) -> float | None:
    parsed = as_float(value)
    if parsed is None:
        return None
    return round(parsed, digits)


def compact_quote(row: dict, kind: str) -> dict:
    code = row.get("symbol") if kind == "us" else row.get("code")
    return {
        "code": code,
        "name": row.get("name") or row.get("company") or row.get("symbol") or row.get("code"),
        "role": row.get("signal_role") or row.get("role") or "",
        "change1d": round_or_none(row.get("change_1d") if kind == "us" else row.get("change")),
        "change5d": round_or_none(row.get("change_5d")),
        "price": round_or_none(row.get("price")),
        "mappingConfidence": round_or_none(row.get("mapping_confidence"), 0),
        "relativeAmount": round_or_none(row.get("relative_amount"), 1),
    }


def normalized_series(items: list[dict], limit: int = 18) -> list[dict]:
    curves: list[list[tuple[str, float]]] = []
    for item in items:
        spark = item.get("spark") or []
        rows = [row for row in spark[-limit:] if as_float(row.get("close")) and row.get("date")]
        if len(rows) < 3:
            continue
        base = as_float(rows[0].get("close"))
        if not base:
            continue
        curves.append([(str(row.get("date")), ((as_float(row.get("close")) or base) - base) / base * 100) for row in rows])
    if not curves:
        return []
    max_len = max(len(curve) for curve in curves)
    output: list[dict] = []
    for idx in range(max_len):
        values: list[float] = []
        date = ""
        for curve in curves:
            if idx < len(curve):
                date = curve[idx][0]
                values.append(curve[idx][1])
        if values:
            output.append({"date": date, "value": round(mean(values), 2)})
    return output[-limit:]


def compact_concept(concept: dict, rank: int, follow_by_id: dict[str, dict]) -> dict:
    scores = concept.get("scores") or {}
    us = concept.get("us") or {}
    cn = concept.get("cn") or {}
    us_tickers = [item for item in us.get("tickers", []) if item.get("ok")]
    cn_companies = cn.get("companies", [])
    follow = follow_by_id.get(concept.get("id"), {})
    news = us.get("news") or []
    research = us.get("research") or []
    discovery = concept.get("discovery") or {}
    return {
        "id": concept.get("id"),
        "rank": rank,
        "name": concept.get("name"),
        "shortName": concept.get("short_name"),
        "isDynamic": bool(concept.get("dynamic")),
        "sourceType": concept.get("source_type") or "full_market_scan",
        "usMappingQuality": concept.get("us_mapping_quality"),
        "usMappingLabel": concept.get("us_mapping_label"),
        "discovery": {
            "activationScore": round_or_none(discovery.get("activation_score"), 1),
            "heatScore": round_or_none(discovery.get("heat_score"), 1),
            "universeRank": discovery.get("universe_rank"),
            "boardChangePct": round_or_none(discovery.get("board_change_pct"), 2),
            "breadth": round_or_none(discovery.get("breadth"), 4),
            "sourceLabel": discovery.get("source_label"),
            "matchedResearchCount": discovery.get("matched_research_count"),
            "topMovers": [
                {
                    "symbol": item.get("symbol"),
                    "change1d": round_or_none(item.get("change_1d")),
                    "change5d": round_or_none(item.get("change_5d")),
                    "relativeVolume": round_or_none(item.get("relative_volume"), 2),
                }
                for item in (discovery.get("top_movers") or [])[:4]
            ],
        },
        "driver": concept.get("underlying_driver"),
        "trigger": concept.get("trigger"),
        "keywords": concept.get("keywords", [])[:5],
        "scores": {
            "opportunity": round_or_none(scores.get("opportunity_score"), 1),
            "marketHeat": round_or_none(scores.get("market_heat_score"), 1),
            "marketHeatRank": scores.get("market_heat_rank"),
            "lag": round_or_none(scores.get("lag_score"), 1),
            "researchHeat": round_or_none(scores.get("research_heat_score"), 1),
            "mappingQuality": round_or_none(scores.get("mapping_quality_score"), 1),
            "tradeState": round_or_none(scores.get("trade_state_score"), 1),
            "confidence": round_or_none(scores.get("confidence"), 1),
            "usResidual1d": round_or_none(scores.get("us_residual_1d")),
            "cnResidual1d": round_or_none(scores.get("cn_residual_1d")),
            "lagGapNeutral": round_or_none(scores.get("lag_gap_neutral", scores.get("lag_gap"))),
            "cnConfirm": round_or_none(scores.get("cn_confirm_score"), 1),
            "overheatPenalty": round_or_none(scores.get("overheat_penalty"), 1),
        },
        "phase": scores.get("phase") or "观察",
        "action": scores.get("action") or "观察",
        "riskFlags": scores.get("risk_flags") or [],
        "usTop": [compact_quote(item, "us") for item in us_tickers[:4]],
        "cnTop": [compact_quote(item, "cn") for item in cn_companies[:6]],
        "trend": {
            "us": normalized_series(us_tickers[:4]),
            "cn": normalized_series(cn_companies[:6]),
        },
        "evidence": {
            "news": [{"title": item.get("title"), "source": item.get("source"), "url": item.get("url")} for item in news[:2]],
            "research": [
                {"title": item.get("title"), "source": item.get("source"), "url": item.get("url")}
                for item in research[:2]
            ],
        },
        "premiumPreview": {
            "certaintyScore": round_or_none(follow.get("certainty_score"), 0),
            "reliabilityGrade": follow.get("decision_status") or follow.get("reliability_grade"),
            "decisionStatus": follow.get("decision_status"),
            "bestHorizon": 5,
            "sampleCount": follow.get("samples"),
            "calibratedProbability": round_or_none((as_float(follow.get("calibrated_probability")) or 0) * 100, 1)
            if follow
            else None,
            "baselineProbability": round_or_none((as_float(follow.get("baseline_probability")) or 0) * 100, 1)
            if follow
            else None,
            "predictiveLift": round_or_none((as_float(follow.get("predictive_lift")) or 0) * 100, 1)
            if follow
            else None,
            "brierSkill": round_or_none((as_float(follow.get("brier_skill")) or 0) * 100, 1) if follow else None,
            "conservativeProbability": round_or_none((as_float(follow.get("conservative_probability")) or 0) * 100, 0)
            if follow
            else None,
            "validationConservativeProbability": round_or_none(
                (as_float(follow.get("validation_conservative_probability")) or 0) * 100, 0
            )
            if follow
            else None,
            "avgReturnAfterCost": round_or_none(follow.get("avg_return_after_cost")),
            "p10ReturnAfterCost": round_or_none(follow.get("p10_return_after_cost")),
            "verdict": follow.get("verdict") if follow else "待观察",
            "abstainReasons": (follow.get("abstain_reasons") or [])[:3],
        },
    }


def build_mobile_summary() -> dict:
    dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8")) if STATUS_PATH.exists() else {}
    follow_concepts = dashboard.get("backtest", {}).get("follow_model", {}).get("concepts") or []
    follow_by_id = {item.get("id"): item for item in follow_concepts if item.get("id")}
    concepts = [
        compact_concept(concept, idx + 1, follow_by_id)
        for idx, concept in enumerate(dashboard.get("concepts", []))
    ]
    free_limit = 3
    dynamic_discovery = dashboard.get("dynamic_discovery") or {}
    return {
        "schemaVersion": "mobile-v4-full-market-rescan",
        "builtAtShanghai": datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "generatedAtShanghai": dashboard.get("generated_at_shanghai"),
        "cacheVersion": status.get("cache_version"),
        "modelVersion": dashboard.get("model_version"),
        "freeLimit": free_limit,
        "dynamicDiscovery": {
            "enabled": bool(dynamic_discovery.get("enabled")),
            "mode": dynamic_discovery.get("mode"),
            "candidateRuleCount": dynamic_discovery.get("candidate_rule_count"),
            "universeCount": dynamic_discovery.get("universe_count"),
            "liveBoardCount": dynamic_discovery.get("live_board_count"),
            "liveEnrichedCount": dynamic_discovery.get("live_enriched_count"),
            "selectedCount": dynamic_discovery.get("selected_count"),
            "scanTimeShanghai": dynamic_discovery.get("scan_time_shanghai"),
            "selectionPolicy": dynamic_discovery.get("selection_policy"),
            "coverageExamples": dynamic_discovery.get("coverage_examples") or {},
            "minActivationScore": dynamic_discovery.get("min_activation_score"),
            "selected": dynamic_discovery.get("selected") or [],
        },
        "summary": {
            "thesis": dashboard.get("summary", {}).get("thesis"),
            "basis": dashboard.get("summary", {}).get("score_framework") or dashboard.get("summary", {}).get("basis"),
            "risk": dashboard.get("summary", {}).get("risk"),
        },
        "marketClock": dashboard.get("market_clock") or {},
        "connectors": dashboard.get("connectors") or {},
        "leaders": status.get("leaders") or [
            {
                "name": item.get("shortName"),
                "opportunity_score": item.get("scores", {}).get("opportunity"),
                "lag_score": item.get("scores", {}).get("lag"),
                "action": item.get("action"),
            }
            for item in concepts[:5]
        ],
        "concepts": concepts,
        "premium": {
            "locked": True,
            "title": "历史验证与模型观察",
            "modules": [
                {"name": "5日样本外概率", "desc": "固定主终点，展示校准概率、无条件基准和Brier增益。"},
                {"name": "门槛审计", "desc": "触发、超额收益、交易成本或可交易性不合格时明确拒绝预测。"},
                {"name": "前向验证", "desc": "冻结每次预测并在未来自动结算，避免只看回测成功路径。"},
            ],
        },
        "links": {
            "desktop": "./index.html",
            "prediction": "./prediction.html",
            "latestReport": "./reports/latest_market_heat_report.html",
        },
        "compliance": {
            "label": "研究工具",
            "text": "本页面只做主题研究、历史验证和风险提示，不构成投资建议或自动买卖依据。",
        },
    }


def main() -> int:
    payload = build_mobile_summary()
    MOBILE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOBILE_JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    MOBILE_JS_PATH.write_text(
        "window.__MOBILE_MARKET_SUMMARY__ = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(MOBILE_JSON_PATH)
    print(f"mobile concepts={len(payload['concepts'])} free={payload['freeLimit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
