#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from forecasting.ledger import update_forward_ledger
from forecasting.pipeline import build_dual_market_forecasts


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output/market_lag_dashboard/data/forecasts-v1.json"


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def validate_payload(payload: dict) -> None:
    if payload.get("schema_version") != "1.0":
        raise ValueError("unexpected schema_version")
    markets = payload.get("markets") or {}
    for market in ("cn", "us"):
        block = markets.get(market) or {}
        forecasts = block.get("forecasts") or []
        if not forecasts:
            raise ValueError(f"{market}: no forecasts")
        if len({row.get("as_of") for row in forecasts}) != 1:
            raise ValueError(f"{market}: forecasts do not share one market snapshot")
        validation = block.get("validation") or {}
        if not (
            str(validation.get("stacking_end") or "")
            < str(validation.get("calibration_start") or "")
            and str(validation.get("calibration_end") or "")
            < str(validation.get("holdout_start") or "")
        ):
            raise ValueError(f"{market}: validation segments overlap")
        for row in forecasts:
            probability = row.get("probability_up")
            quantiles = row.get("quantiles") or {}
            weights = [item.get("weight") for item in row.get("component_predictions") or []]
            if probability is None or not 0 <= float(probability) <= 1:
                raise ValueError(f"{market}/{row.get('symbol')}: invalid probability")
            q10, q50, q90 = (quantiles.get("q10"), quantiles.get("q50"), quantiles.get("q90"))
            if None in (q10, q50, q90) or not float(q10) <= float(q50) <= float(q90):
                raise ValueError(f"{market}/{row.get('symbol')}: invalid quantiles")
            if abs(sum(float(value or 0) for value in weights) - 1) > 1e-5:
                raise ValueError(f"{market}/{row.get('symbol')}: ensemble weights do not sum to one")
            if row.get("forward_eligible") and not (
                row.get("created_at")
                and row.get("signal_available_at")
                and row.get("entry_deadline")
            ):
                raise ValueError(
                    f"{market}/{row.get('symbol')}: forward freeze evidence is missing"
                )
            if row.get("forward_eligible"):
                created = datetime.fromisoformat(row["created_at"])
                signal_available = datetime.fromisoformat(row["signal_available_at"])
                entry_deadline = datetime.fromisoformat(row["entry_deadline"])
                local_created = created.astimezone(entry_deadline.tzinfo)
                if not signal_available <= local_created < entry_deadline:
                    raise ValueError(
                        f"{market}/{row.get('symbol')}: invalid forward freeze window"
                    )
    cn_boards = {row.get("board") for row in markets["cn"]["forecasts"]}
    required = {"沪市主板", "深市主板", "创业板", "科创板", "北交所"}
    missing = required - cn_boards
    if missing:
        raise ValueError(f"A-share board coverage missing: {sorted(missing)}")


def commit_forward_ledger(payload: dict) -> None:
    markets = payload["markets"]
    summary = update_forward_ledger(
        root=ROOT,
        ledger_path=ROOT
        / "output/market_lag_dashboard/data/forecast-forward-ledger-v1.json",
        forecasts_by_market={
            market: markets[market]["forecasts"] for market in ("cn", "us")
        },
        model_version=payload["model"]["version"],
        snapshot_hash=payload["source_snapshot_hash"],
        round_trip_costs={
            market: float(markets[market]["session"]["round_trip_cost"])
            for market in ("cn", "us")
        },
        write=True,
    )
    for market in ("cn", "us"):
        markets[market]["forward_validation"] = summary[market]
    payload["improvement"]["ledger_summary"] = summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the dual-market forecast V1 payload.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_dual_market_forecasts(ROOT)
    validate_payload(payload)
    commit_forward_ledger(payload)
    validate_payload(payload)
    atomic_write_json(args.output, payload)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "generated_at": payload["generated_at"],
                "model": payload["model"]["version"],
                "cn_forecasts": len(payload["markets"]["cn"]["forecasts"]),
                "us_forecasts": len(payload["markets"]["us"]["forecasts"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
