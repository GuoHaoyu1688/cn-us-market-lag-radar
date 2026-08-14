#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "market_lag_dashboard"
INDEX_PATH = OUTPUT / "index.html"
DATA_PATH = OUTPUT / "data" / "dashboard.json"
FORECAST_PATH = OUTPUT / "data" / "forecasts-v1.json"
STATUS_PATH = OUTPUT / "data" / "latest_refresh_status.json"
SNAPSHOT_DIR = OUTPUT / "data" / "snapshots"
LOCK_PATH = OUTPUT / ".refresh.lock"
SH_TZ = ZoneInfo("Asia/Shanghai")


def acquire_lock(wait_seconds: int = 45 * 60) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            age = datetime.now().timestamp() - LOCK_PATH.stat().st_mtime
            if age > 60 * 60:
                LOCK_PATH.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise SystemExit("refresh already running")
            time.sleep(15)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))


def release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def python_bin() -> str:
    candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate if candidate.exists() else sys.executable)


def bump_cache_version() -> str:
    # JSON is fetched with cache: no-store. Keeping HTML immutable avoids
    # changing a source checkout after every local refresh.
    return datetime.now(SH_TZ).strftime("%Y%m%d-%H%M%S")


def write_snapshot(cache_version: str, forecast: dict) -> str:
    token = cache_version or datetime.now(SH_TZ).strftime("%Y%m%d-%H%M%S")
    path = SNAPSHOT_DIR / f"forecast_snapshot_{token}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forecast, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = SNAPSHOT_DIR / "latest_forecast_snapshot.json"
    latest_path.write_text(json.dumps(forecast, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(OUTPUT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh market data, dual-market forecasts, and page cache."
    )
    parser.add_argument(
        "--reuse-dashboard",
        action="store_true",
        help="Reuse an already successful dashboard collection and resume from forecasting.",
    )
    args = parser.parse_args()
    acquire_lock()
    try:
        if args.reuse_dashboard:
            if not DATA_PATH.exists():
                print("cannot reuse dashboard: data/dashboard.json is missing")
                return 2
            dashboard_for_resume = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            collection_result = subprocess.CompletedProcess(
                args=["reuse-dashboard"],
                returncode=0,
                stdout=(
                    "reused successful dashboard collection "
                    f"generated={dashboard_for_resume.get('generated_at_shanghai')}"
                ),
            )
        else:
            collection_result = subprocess.run(
                [python_bin(), str(ROOT / "scripts" / "build_market_lag_dashboard.py")],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=35 * 60,
            )
        if collection_result.returncode != 0:
            print(collection_result.stdout)
            return collection_result.returncode

        forecast_result = subprocess.run(
            [python_bin(), str(ROOT / "scripts" / "build_dual_market_forecast.py")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20 * 60,
        )
        if forecast_result.returncode != 0:
            print(forecast_result.stdout)
            return forecast_result.returncode

        cache_version = bump_cache_version()
        dashboard = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        forecast = json.loads(FORECAST_PATH.read_text(encoding="utf-8"))
        snapshot_path = write_snapshot(cache_version, forecast)
        status = {
            "refreshed_at_shanghai": datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
            "generated_at_shanghai": forecast.get("generated_at"),
            "cache_version": cache_version,
            "model_version": (forecast.get("model") or {}).get("version"),
            "snapshot_path": snapshot_path,
            "source_dashboard_generated_at": dashboard.get("generated_at_shanghai"),
            "markets": {
                market: {
                    "forecast_count": len(((forecast.get("markets") or {}).get(market) or {}).get("forecasts") or []),
                    "validation_status": (
                        ((forecast.get("markets") or {}).get(market) or {}).get("validation") or {}
                    ).get("status"),
                    "brier_skill": (
                        ((forecast.get("markets") or {}).get(market) or {}).get("validation") or {}
                    ).get("brier_skill"),
                    "forward_validation": (
                        (forecast.get("markets") or {}).get(market) or {}
                    ).get("forward_validation"),
                }
                for market in ("cn", "us")
            },
            "collection_stdout_tail": "\n".join(collection_result.stdout.splitlines()[-8:]),
            "forecast_stdout_tail": "\n".join(forecast_result.stdout.splitlines()[-8:]),
        }
        STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"refreshed {forecast.get('generated_at')} cache={cache_version}")
        print(
            "forecasts "
            + " / ".join(
                f"{market.upper()}={len(((forecast.get('markets') or {}).get(market) or {}).get('forecasts') or [])}"
                for market in ("cn", "us")
            )
        )
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
