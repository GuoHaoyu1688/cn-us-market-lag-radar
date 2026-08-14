from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .features import load_chart_rows, market_aligned_chart_frame
from .market_specs import board_standard_limit_fraction
from path_safety import resolve_within


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "1.0", "records": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        return {"schema_version": "1.0", "records": []}
    return payload


def _prediction_id(record: dict[str, Any]) -> str:
    """Identify one economic forecast, independent of refresh snapshots."""
    key = "|".join(
        [
            str(record.get("market") or ""),
            str(record.get("symbol") or ""),
            str(record.get("as_of") or ""),
            str(record.get("horizon") or ""),
            str(record.get("model_version") or ""),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Migrate snapshot-level duplicates while keeping the first frozen forecast."""
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        migrated = dict(record)
        if (
            not migrated.get("created_at")
            or not migrated.get("signal_available_at")
            or not migrated.get("entry_deadline")
        ):
            migrated["forward_evidence"] = "legacy_unverifiable"
        else:
            migrated.setdefault("forward_evidence", "verified_pre_entry")
        prediction_id = _prediction_id(migrated)
        if prediction_id in seen:
            continue
        unique.append({**migrated, "id": prediction_id})
        seen.add(prediction_id)
    return unique


def _settle_record(record: dict[str, Any], root: Path) -> dict[str, Any]:
    if record.get("status") in {"resolved", "void"}:
        return record
    ref = str(record.get("chart_ref") or "").removeprefix("./")
    benchmark_ref = (
        "data/charts/us/000300.SS.json"
        if record.get("market") == "cn"
        else "data/charts/us/SPY.json"
    )
    output = root / "output/market_lag_dashboard"
    try:
        chart_path = resolve_within(output, ref)
        benchmark_path = resolve_within(output, benchmark_ref)
    except ValueError:
        return {**record, "status": "void", "void_reason": "unsafe chart reference"}
    frame = market_aligned_chart_frame(
        load_chart_rows(chart_path),
        load_chart_rows(benchmark_path),
    )
    if frame.empty:
        return record
    matches = frame.index[frame["date"].dt.strftime("%Y-%m-%d") == str(record.get("as_of") or "")]
    if len(matches) != 1:
        return record
    signal_index = int(matches[0])
    horizon = int(record.get("horizon") or 5)
    entry_index = signal_index + 1
    exit_index = signal_index + horizon
    if exit_index >= len(frame):
        return record
    signal_row = frame.iloc[signal_index]
    entry_row = frame.iloc[entry_index]
    exit_row = frame.iloc[exit_index]
    for stage, row in (
        ("signal", signal_row),
        ("entry", entry_row),
        ("exit", exit_row),
    ):
        if not bool(row.get("tradeable")):
            return {
                **record,
                "status": "void",
                "void_reason": f"{stage}_session_not_tradeable",
                "entry_date": entry_row["date"].strftime("%Y-%m-%d"),
                "exit_date": exit_row["date"].strftime("%Y-%m-%d"),
            }
    if record.get("market") == "cn":
        limit_fraction = board_standard_limit_fraction(str(record.get("board_key") or ""))
        if limit_fraction is not None:
            threshold = limit_fraction * 0.965
            entry_gap = float(entry_row["open"]) / float(signal_row["close"]) - 1
            exit_previous_row = frame.iloc[exit_index - 1]
            if not bool(exit_previous_row.get("tradeable")):
                return {
                    **record,
                    "status": "void",
                    "void_reason": "exit_reference_session_not_tradeable",
                    "entry_date": entry_row["date"].strftime("%Y-%m-%d"),
                    "exit_date": exit_row["date"].strftime("%Y-%m-%d"),
                }
            exit_move = (
                float(exit_row["close"]) / float(exit_previous_row["close"]) - 1
            )
            if entry_gap >= threshold or exit_move <= -threshold:
                return {
                    **record,
                    "status": "void",
                    "void_reason": (
                        "entry_near_upper_price_limit"
                        if entry_gap >= threshold
                        else "exit_near_lower_price_limit"
                    ),
                    "entry_date": entry_row["date"].strftime("%Y-%m-%d"),
                    "exit_date": exit_row["date"].strftime("%Y-%m-%d"),
                }
    entry = float(entry_row["open"])
    exit_price = float(exit_row["close"])
    if not (math.isfinite(entry) and math.isfinite(exit_price) and entry > 0 and exit_price > 0):
        return record
    cost = float(record.get("round_trip_cost") or 0)
    realized = exit_price / entry - 1 - cost
    probability = float(record.get("probability_up") or 0.5)
    return {
        **record,
        "status": "resolved",
        "entry_date": entry_row["date"].strftime("%Y-%m-%d"),
        "exit_date": exit_row["date"].strftime("%Y-%m-%d"),
        "entry_price": entry,
        "exit_price": exit_price,
        "realized_return": realized,
        "realized_up": realized > 0,
        "brier_loss": (probability - float(realized > 0)) ** 2,
        "interval_hit": float(record.get("q10") or -999) <= realized <= float(record.get("q90") or 999),
    }


def _summary(
    records: list[dict[str, Any]],
    market: str,
    *,
    model_version: str | None = None,
) -> dict[str, Any]:
    all_selected = [
        item
        for item in records
        if item.get("market") == market
        and (model_version is None or item.get("model_version") == model_version)
    ]
    selected = [
        item
        for item in all_selected
        if item.get("forward_evidence") == "verified_pre_entry"
    ]
    resolved = [item for item in selected if item.get("status") == "resolved"]
    accepted = [item for item in resolved if item.get("decision_status") == "可研究"]
    return {
        "total": len(selected),
        "legacy_unverifiable": sum(
            1
            for item in all_selected
            if item.get("forward_evidence") == "legacy_unverifiable"
        ),
        "pending": sum(1 for item in selected if item.get("status") == "pending"),
        "resolved": len(resolved),
        "void": sum(1 for item in selected if item.get("status") == "void"),
        "accepted_resolved": len(accepted),
        "brier": (
            sum(float(item.get("brier_loss") or 0) for item in resolved) / len(resolved)
            if resolved
            else None
        ),
        "accepted_brier": (
            sum(float(item.get("brier_loss") or 0) for item in accepted) / len(accepted)
            if accepted
            else None
        ),
        "interval_coverage": (
            sum(1 for item in resolved if item.get("interval_hit")) / len(resolved)
            if resolved
            else None
        ),
    }


def update_forward_ledger(
    *,
    root: Path,
    ledger_path: Path,
    forecasts_by_market: dict[str, list[dict[str, Any]]],
    model_version: str,
    snapshot_hash: str,
    round_trip_costs: dict[str, float],
    write: bool = True,
) -> dict[str, Any]:
    payload = _load(ledger_path)
    records = _deduplicate(
        [_settle_record(item, root) for item in payload.get("records", [])]
    )
    existing_by_id = {str(item.get("id") or ""): item for item in records}
    for market, forecasts in forecasts_by_market.items():
        for forecast in forecasts:
            if not forecast.get("forward_eligible"):
                continue
            try:
                created_at = datetime.fromisoformat(str(forecast.get("created_at") or ""))
                signal_available_at = datetime.fromisoformat(
                    str(forecast.get("signal_available_at") or "")
                )
                entry_deadline = datetime.fromisoformat(
                    str(forecast.get("entry_deadline") or "")
                )
            except ValueError as exc:
                raise RuntimeError("forward forecast lacks a verifiable freeze time") from exc
            local_created = created_at.astimezone(entry_deadline.tzinfo)
            if not signal_available_at <= local_created < entry_deadline:
                raise RuntimeError(
                    "forecast was not frozen after signal close and before entry: "
                    f"{market}:{forecast.get('symbol')}"
                )
            record = {
                "market": market,
                "symbol": forecast.get("symbol"),
                "name": forecast.get("name"),
                "board": forecast.get("board"),
                "board_key": forecast.get("board_key"),
                "as_of": forecast.get("as_of"),
                "signal_cutoff": forecast.get("signal_cutoff"),
                "created_at": forecast.get("created_at"),
                "prediction_time": forecast.get("prediction_time"),
                "target_start_estimate": forecast.get("target_start_estimate"),
                "target_end_estimate": forecast.get("target_end_estimate"),
                "signal_available_at": forecast.get("signal_available_at"),
                "entry_deadline": forecast.get("entry_deadline"),
                "calendar_basis": forecast.get("calendar_basis"),
                "forward_evidence": "verified_pre_entry",
                "horizon": 5,
                "model_version": model_version,
                "feature_version": "market-calendar-aligned-panel-v1.2",
                "calibration_version": "independent-sigmoid-v1",
                "snapshot_hash": snapshot_hash,
                "probability_up": forecast.get("probability_up"),
                "expected_return": forecast.get("expected_return"),
                "q10": (forecast.get("quantiles") or {}).get("q10"),
                "q50": (forecast.get("quantiles") or {}).get("q50"),
                "q90": (forecast.get("quantiles") or {}).get("q90"),
                "decision_status": forecast.get("decision_status"),
                "abstain_reasons": forecast.get("abstain_reasons") or [],
                "component_predictions": forecast.get("component_predictions") or [],
                "chart_ref": forecast.get("chart_ref"),
                "round_trip_cost": round_trip_costs.get(market, 0),
                "status": "pending",
            }
            record["id"] = _prediction_id(record)
            existing = existing_by_id.get(record["id"])
            if existing is not None:
                frozen_fields = (
                    "probability_up",
                    "expected_return",
                    "q10",
                    "q50",
                    "q90",
                    "decision_status",
                )
                if any(existing.get(field) != record.get(field) for field in frozen_fields):
                    raise RuntimeError(
                        "frozen forecast drift for "
                        f"{market}:{forecast.get('symbol')}:{forecast.get('as_of')}; "
                        "bump model_version before publishing changed predictions"
                    )
                for field in (
                    "created_at",
                    "prediction_time",
                    "target_start_estimate",
                    "target_end_estimate",
                    "signal_available_at",
                    "entry_deadline",
                    "calendar_basis",
                ):
                    forecast[field] = existing.get(field)
            else:
                records.append(record)
                existing_by_id[record["id"]] = record
    records = sorted(
        records,
        key=lambda item: (
            str(item.get("as_of") or ""),
            str(item.get("market") or ""),
            str(item.get("symbol") or ""),
        ),
    )
    output = {
        "schema_version": "1.1",
        "model_version": model_version,
        "records": records,
        "summary": {
            "cn": _summary(records, "cn", model_version=model_version),
            "us": _summary(records, "us", model_version=model_version),
        },
        "all_versions_summary": {
            "cn": _summary(records, "cn"),
            "us": _summary(records, "us"),
        },
    }
    if write:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = ledger_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(ledger_path)
    return output["summary"]
