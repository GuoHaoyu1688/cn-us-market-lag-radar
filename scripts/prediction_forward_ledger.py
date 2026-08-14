#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from path_safety import resolve_within


SH_TZ = ZoneInfo("Asia/Shanghai")
PRIMARY_HORIZON = 5
ROUND_TRIP_COST_PCT = 0.35
MAX_CN_DAILY_MOVE_PCT = 15.5
MAX_ENTRY_GAP_PCT = 9.65


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1) * 100


def load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def load_chart(item: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    chart_ref = str(item.get("chart_ref") or "")
    if not chart_ref:
        return []
    try:
        payload = json.loads(resolve_within(output_dir, chart_ref).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return []
    rows = payload.get("rows") or []
    return sorted(
        [row for row in rows if row.get("date") and safe_float(row.get("close")) is not None],
        key=lambda row: str(row.get("date")),
    )


def resolve_record(record: dict[str, Any], company: dict[str, Any], output_dir: Path) -> None:
    if record.get("resolved"):
        return
    signal_date = str(record.get("trigger_date") or "")
    rows = load_chart(company, output_dir)
    entry_idx = next((idx for idx, row in enumerate(rows) if str(row.get("date") or "") > signal_date), None)
    if entry_idx is None or entry_idx <= 0:
        return
    entry = rows[entry_idx]
    entry_open = safe_float(entry.get("open")) or safe_float(entry.get("close"))
    previous_close = safe_float(rows[entry_idx - 1].get("close"))
    entry_gap = pct_change(entry_open, previous_close)
    record["entry_date"] = entry.get("date")
    record["entry_price"] = entry_open
    if entry_gap is None or abs(entry_gap) >= MAX_ENTRY_GAP_PCT or (safe_float(entry.get("volume")) or 0) <= 0:
        record.update({"resolved": True, "outcome_status": "不可成交样本", "entry_gap_pct": entry_gap})
        return
    exit_idx = entry_idx + int(record.get("horizon_days") or PRIMARY_HORIZON)
    if exit_idx >= len(rows):
        record["outcome_status"] = "等待到期"
        return
    for idx in range(max(1, entry_idx), exit_idx + 1):
        daily_move = pct_change(safe_float(rows[idx].get("close")), safe_float(rows[idx - 1].get("close")))
        if daily_move is not None and abs(daily_move) > MAX_CN_DAILY_MOVE_PCT:
            record.update({"resolved": True, "outcome_status": "复权异常样本"})
            return
    exit_row = rows[exit_idx]
    gross = pct_change(safe_float(exit_row.get("close")), entry_open)
    if gross is None:
        return
    net = gross - ROUND_TRIP_COST_PCT
    record.update(
        {
            "resolved": True,
            "resolved_at": datetime.now(SH_TZ).isoformat(timespec="seconds"),
            "outcome_status": "已结算",
            "exit_date": exit_row.get("date"),
            "exit_price": safe_float(exit_row.get("close")),
            "gross_return_pct": gross,
            "net_return_pct": net,
            "profit_success": net > 0,
        }
    )


def brier(rows: list[dict[str, Any]]) -> float | None:
    pairs = [
        (safe_float(row.get("predicted_probability")), 1 if row.get("profit_success") else 0)
        for row in rows
        if row.get("outcome_status") == "已结算" and safe_float(row.get("predicted_probability")) is not None
    ]
    return sum((probability - label) ** 2 for probability, label in pairs) / len(pairs) if pairs else None


def ledger_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid_records = [row for row in records if not row.get("invalidated")]
    invalidated = [row for row in records if row.get("invalidated")]
    settled = [row for row in valid_records if row.get("outcome_status") == "已结算"]
    qualified = [row for row in settled if row.get("qualified_at_signal")]
    rejected = [row for row in settled if not row.get("qualified_at_signal")]

    def group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        returns = [safe_float(row.get("net_return_pct")) for row in rows]
        usable = [value for value in returns if value is not None]
        return {
            "samples": len(usable),
            "hit_rate": sum(1 for value in usable if value > 0) / len(usable) if usable else None,
            "avg_net_return_pct": sum(usable) / len(usable) if usable else None,
            "brier_score": brier(rows),
        }

    return {
        "status": "可评估" if len(qualified) >= 30 else "积累中",
        "minimum_qualified_samples": 30,
        "records": len(valid_records),
        "total_records": len(records),
        "invalidated": len(invalidated),
        "settled": len(settled),
        "pending": len([row for row in valid_records if not row.get("resolved")]),
        "qualified": group_summary(qualified),
        "rejected_control": group_summary(rejected),
        "note": "只有至少30个前向通过样本后，才用该账本评价实盘概率可信度；回测不能替代这项检验。",
    }


def update_prediction_ledger(dashboard: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    path = output_dir / "data" / "prediction-forward-ledger.json"
    records = load_rows(path)
    follow = dashboard.get("backtest", {}).get("follow_model") or {}
    latest_completed_signal = str((follow.get("sample_window") or {}).get("latest_date") or "")
    company_by_code = {
        str(company.get("code")): company
        for concept in dashboard.get("concepts") or []
        for company in concept.get("cn", {}).get("companies") or []
        if company.get("code")
    }
    for record in records:
        trigger_date = str(record.get("trigger_date") or "")
        if latest_completed_signal and trigger_date > latest_completed_signal:
            record["resolved"] = True
            record["invalidated"] = True
            record.setdefault("invalidated_at", datetime.now(SH_TZ).isoformat(timespec="seconds"))
            record["outcome_status"] = "已作废：使用未完成美股日线"
            continue
        company = company_by_code.get(str(record.get("code") or ""))
        if company:
            resolve_record(record, company, output_dir)

    model_version = str((follow.get("model_audit") or {}).get("version") or dashboard.get("model_version") or "")
    existing = {str(row.get("record_id") or "") for row in records}
    for row in follow.get("screened_candidates") or []:
        if not row.get("current_trigger") or not row.get("trigger_date"):
            continue
        record_id = "|".join(
            [model_version, str(row.get("trigger_date")), str(row.get("concept_id")), str(row.get("code"))]
        )
        if record_id in existing:
            continue
        status = str(row.get("decision_status") or "")
        records.append(
            {
                "record_id": record_id,
                "frozen_at": datetime.now(SH_TZ).isoformat(timespec="seconds"),
                "model_version": model_version,
                "trigger_date": row.get("trigger_date"),
                "horizon_days": PRIMARY_HORIZON,
                "code": row.get("code"),
                "name": row.get("name"),
                "concept_id": row.get("concept_id"),
                "concept_name": row.get("concept_short_name") or row.get("concept_name"),
                "decision_status": status,
                "qualified_at_signal": not row.get("abstain") and status in {"条件可观察", "较高证据"},
                "predicted_probability": row.get("calibrated_probability_5d"),
                "probability_lower": row.get("conservative_probability_5d"),
                "baseline_probability": row.get("baseline_probability_5d"),
                "predictive_lift": row.get("predictive_lift_5d"),
                "brier_skill_backtest": row.get("brier_skill_5d"),
                "abstain_reasons": row.get("abstain_reasons") or [],
                "resolved": False,
                "outcome_status": "等待入场",
            }
        )
        existing.add(record_id)

    records.sort(key=lambda row: (str(row.get("trigger_date") or ""), str(row.get("record_id") or "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return ledger_summary(records)
