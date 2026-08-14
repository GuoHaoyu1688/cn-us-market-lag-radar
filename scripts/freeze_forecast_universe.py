#!/usr/bin/env python3
"""Freeze the forecast candidate universe for reproducible daily refreshes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forecasting.pipeline import (
    UNIVERSE_VERSION,
    _dashboard_universe,
    _load_json,
    _merge_core,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config" / "forecast_universe_v1.json"
DASHBOARD = ROOT / "output/market_lag_dashboard/data/dashboard.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a versioned candidate universe from the current dashboard."
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the existing manifest only after bumping universe/model versions.",
    )
    args = parser.parse_args()
    if OUTPUT.exists() and not args.replace:
        raise SystemExit(
            f"{OUTPUT} already exists; bump universe/model versions and use --replace"
        )
    dashboard = _load_json(DASHBOARD, {})
    universe = _merge_core(_dashboard_universe(dashboard))
    payload = {
        "schema_version": "1.0",
        "universe_version": UNIVERSE_VERSION,
        "frozen_from_dashboard": dashboard.get("generated_at_shanghai")
        or dashboard.get("generated_at"),
        "policy": (
            "候选池版本冻结；板块不作为排除条件。扩充、删除或重分类标的时，"
            "必须生成新universe版本并升级模型版本。"
        ),
        "markets": universe,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(OUTPUT)
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "version": UNIVERSE_VERSION,
                "cn": len(universe["cn"]),
                "us": len(universe["us"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
