#!/usr/bin/env python3
"""Audit the V1 dual-market forecast artifact without claiming model edge."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from build_dual_market_forecast import validate_payload
from forecasting.ledger import _prediction_id
from forecasting.pipeline import (
    _load_forecast_universe,
    _load_json,
    _source_snapshot_hash,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "market_lag_dashboard"
PAYLOAD_PATH = OUTPUT / "data" / "forecasts-v1.json"
REPORT_PATH = OUTPUT / "reports" / "forecast_v1_audit.md"
REQUIRED_CN_BOARDS = {"沪市主板", "深市主板", "创业板", "科创板", "北交所"}


def pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.{digits}f}%"


def number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def weights_text(weights: dict[str, Any]) -> str:
    return "；".join(f"{name} {pct(weight, 1)}" for name, weight in weights.items())


def _audit_ledger(
    payload: dict[str, Any],
    records: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    checks: list[str] = []
    errors: list[str] = []
    current_version = payload.get("model", {}).get("version")
    current_records = [
        record
        for record in records
        if record.get("model_version") == current_version
    ]
    records_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in current_records:
        derived_id = _prediction_id(record)
        stored_id = record.get("id")
        if stored_id != derived_id:
            errors.append(
                "账本记录ID与经济预测主键不一致："
                f"{record.get('market')}/{record.get('symbol')}/{record.get('as_of')}"
            )
        records_by_id[derived_id].append(record)
    duplicate_record_ids = sorted(
        prediction_id
        for prediction_id, grouped in records_by_id.items()
        if len(grouped) != 1
    )
    if duplicate_record_ids:
        errors.append(
            f"当前模型账本存在重复预测ID：{duplicate_record_ids[:5]}"
        )

    expected_by_id: dict[str, dict[str, Any]] = {}
    duplicate_expected_ids: set[str] = set()
    for market in ("cn", "us"):
        for forecast in payload.get("markets", {}).get(market, {}).get("forecasts", []):
            if not forecast.get("forward_eligible"):
                continue
            candidate = {
                "market": forecast.get("market"),
                "symbol": forecast.get("symbol"),
                "as_of": forecast.get("as_of"),
                "horizon": forecast.get("horizon"),
                "model_version": current_version,
            }
            prediction_id = _prediction_id(candidate)
            if prediction_id in expected_by_id:
                duplicate_expected_ids.add(prediction_id)
            expected_by_id[prediction_id] = forecast
    if duplicate_expected_ids:
        errors.append(
            f"当前发布载荷存在重复预测ID：{sorted(duplicate_expected_ids)[:5]}"
        )

    frozen_fields = (
        "probability_up",
        "expected_return",
        "q10",
        "q50",
        "q90",
        "decision_status",
    )
    matched = 0
    for prediction_id, forecast in expected_by_id.items():
        grouped = records_by_id.get(prediction_id) or []
        if len(grouped) != 1:
            if not grouped:
                errors.append(
                    f"{forecast.get('market')}/{forecast.get('symbol')} 缺少冻结账本记录"
                )
            continue
        record = grouped[0]
        if record.get("forward_evidence") != "verified_pre_entry":
            errors.append(
                f"{forecast.get('market')}/{forecast.get('symbol')} 缺少可验证的入场前冻结证据"
            )
            continue
        forecast_values = {
            "probability_up": forecast.get("probability_up"),
            "expected_return": forecast.get("expected_return"),
            "q10": (forecast.get("quantiles") or {}).get("q10"),
            "q50": (forecast.get("quantiles") or {}).get("q50"),
            "q90": (forecast.get("quantiles") or {}).get("q90"),
            "decision_status": forecast.get("decision_status"),
        }
        if any(record.get(field) != forecast_values[field] for field in frozen_fields):
            errors.append(
                f"{forecast.get('market')}/{forecast.get('symbol')} 发布值与冻结值漂移"
            )
            continue
        matched += 1
    if not errors:
        historical_count = max(len(current_records) - matched, 0)
        checks.append(
            f"当前可冻结发布记录匹配 {matched} 条；同版本历史记录保留 {historical_count} 条"
        )
    return checks, errors


def audit(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    checks: list[str] = []
    try:
        validate_payload(payload)
        checks.append("双市场 JSON 契约有效")
    except Exception as exc:  # pragma: no cover - diagnostic path
        errors.append(f"JSON 契约失败：{exc}")

    for market in ("cn", "us"):
        market_payload = payload.get("markets", {}).get(market, {})
        forecasts = market_payload.get("forecasts") or []
        validation = market_payload.get("validation") or {}
        if not forecasts:
            errors.append(f"{market.upper()} 无预测记录")
            continue
        if any(int(row.get("horizon") or 0) != 5 for row in forecasts):
            errors.append(f"{market.upper()} 存在非 5 交易日主终点")
        else:
            checks.append(f"{market.upper()} 全部记录固定为 5 交易日")
        if any(row.get("market") != market for row in forecasts):
            errors.append(f"{market.upper()} 存在跨市场记录")
        if not (
            str(validation.get("stacking_end") or "")
            < str(validation.get("calibration_start") or "")
            and str(validation.get("calibration_end") or "")
            < str(validation.get("holdout_start") or "")
        ):
            errors.append(f"{market.upper()} 定权、校准、验收时间窗没有严格分离")
        else:
            checks.append(f"{market.upper()} 定权、校准、验收按日期严格分离")
        for forecast in forecasts:
            if not forecast.get("forward_eligible"):
                continue
            created = datetime.fromisoformat(forecast["created_at"])
            signal_available = datetime.fromisoformat(
                forecast["signal_available_at"]
            )
            entry_deadline = datetime.fromisoformat(forecast["entry_deadline"])
            local_created = created.astimezone(entry_deadline.tzinfo)
            if not signal_available <= local_created < entry_deadline:
                errors.append(
                    f"{market.upper()}/{forecast.get('symbol')} 前向冻结时点越界"
                )
                break
        else:
            checks.append(f"{market.upper()} 前向记录均在收盘后、下一开盘前冻结")

    cn_boards = {
        row.get("board")
        for row in payload.get("markets", {}).get("cn", {}).get("forecasts", [])
    }
    missing = REQUIRED_CN_BOARDS - cn_boards
    if missing:
        errors.append(f"A股板块覆盖缺失：{sorted(missing)}")
    else:
        checks.append("A股主板、创业板、科创板、北交所均已进入预测宇宙")

    app_text = (OUTPUT / "assets" / "forecast-v1.js").read_text(encoding="utf-8")
    if "A股预测" not in app_text or "美股预测" not in app_text:
        errors.append("页面缺少 A股/美股切换入口")
    else:
        checks.append("页面只使用 A股/美股市场切换入口")

    dashboard = _load_json(OUTPUT / "data" / "dashboard.json", {})
    universe = _load_forecast_universe(ROOT, dashboard)
    expected_source_hash = _source_snapshot_hash(
        root=ROOT,
        dashboard=dashboard,
        universe=universe,
    )
    if payload.get("source_snapshot_hash") != expected_source_hash:
        errors.append("source_snapshot_hash 与当前冻结行情输入不一致")
    else:
        checks.append("source_snapshot_hash 已覆盖冻结候选池、市场配置与实际行情文件")

    ledger = _load_json(
        OUTPUT / "data" / "forecast-forward-ledger-v1.json",
        {"records": []},
    )
    ledger_checks, ledger_errors = _audit_ledger(
        payload,
        ledger.get("records", []),
    )
    checks.extend(ledger_checks)
    errors.extend(ledger_errors)
    return checks, errors


def build_report(
    payload: dict[str, Any],
    checks: list[str],
    errors: list[str],
) -> str:
    rows = []
    for market in ("cn", "us"):
        market_payload = payload["markets"][market]
        forecasts = market_payload["forecasts"]
        validation = market_payload["validation"]
        statuses = Counter(item["decision_status"] for item in forecasts)
        rows.append(
            "| {label} | {count} | {status} | {brier} | {calibration} | {return_skill} | "
            "{coverage} | {weights} |".format(
                label=market_payload["label"],
                count=len(forecasts),
                status="、".join(f"{key} {value}" for key, value in statuses.items()),
                brier=pct(validation.get("brier_skill")),
                calibration=pct(validation.get("calibration_error")),
                return_skill=pct(validation.get("return_skill")),
                coverage=pct(validation.get("empirical_interval_coverage")),
                weights=weights_text(validation.get("weights") or {}),
            )
        )

    cn_board_counts = Counter(
        item["board"] for item in payload["markets"]["cn"]["forecasts"]
    )
    ledger_lines = []
    for market in ("cn", "us"):
        ledger = payload["improvement"]["ledger_summary"][market]
        ledger_lines.append(
            f"- {payload['markets'][market]['label']}：总计 {ledger.get('total', 0)}，"
            f"待结算 {ledger.get('pending', 0)}，已结算 {ledger.get('resolved', 0)}，"
            f"作废 {ledger.get('void', 0)}，通过型已结算 {ledger.get('accepted_resolved', 0)}，"
            f"旧版不可验证 {ledger.get('legacy_unverifiable', 0)}。"
        )

    check_lines = [f"- PASS — {item}" for item in checks]
    check_lines.extend(f"- FAIL — {item}" for item in errors)
    integrity_status = "PASS" if not errors else "FAIL"
    return f"""# 双市场预测 V1 验收报告

生成时间：{payload.get("generated_at", "-")}<br>
模型版本：{payload.get("model", {}).get("version", "-")}<br>
工程完整性：**{integrity_status}**<br>
预测能力结论：**以各市场封存验收指标为准；工程 PASS 不等于模型具有稳定超额预测能力。**

## 结果概览

| 市场 | 标的数 | 当前决策 | Brier skill | 校准误差 | 收益 skill | 经验区间覆盖 | 生产权重 |
|---|---:|---|---:|---:|---:|---:|---|
{chr(10).join(rows)}

## A股覆盖

{json.dumps(dict(cn_board_counts), ensure_ascii=False)}

板块只作为特征和展示字段，不作为排除条件。

## 验证边界

- 固定主终点：下一真实交易日开盘进入，第 5 个完整交易日收盘评估。
- 切分方式：按日期分组的 purged walk-forward。
- 三段隔离：早段学习非负组合权重，中段拟合 sigmoid 校准器，晚段封存验收。
- 失败降权：挑战模型不能在历史定权段胜过市场先验时，权重自动归零。
- 拒绝预测：方向、收益、校准、区间、数据质量或模型一致性不达标时标记“暂缓”。

## 前向账本

{chr(10).join(ledger_lines)}

账本只在真实未来交易日到期后结算，未到期记录不参与命中率。

## 工程检查

{chr(10).join(check_lines)}

## 持续改进规则

{payload.get("improvement", {}).get("promotion_rule", "-")}

复核节奏：{payload.get("improvement", {}).get("review_cadence", "-")}
"""


def main() -> int:
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    checks, errors = audit(payload)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(payload, checks, errors),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH),
                "status": "PASS" if not errors else "FAIL",
                "checks": len(checks),
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
