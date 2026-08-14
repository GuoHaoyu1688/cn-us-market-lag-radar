#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from dotenv import load_dotenv
from tabulate import tabulate

import akshare as ak
import easyquotation
import requests


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"


def normalize_symbol(raw: str) -> tuple[str, str, str]:
    value = raw.strip().lower().replace(".", "")
    if value.startswith(("sh", "sz", "bj")):
        prefix = value[:2]
        code = value[2:]
    else:
        code = "".join(ch for ch in value if ch.isdigit())
        if code.startswith(("600", "601", "603", "605", "688", "900")):
            prefix = "sh"
        elif code.startswith(("000", "001", "002", "003", "300", "301", "200")):
            prefix = "sz"
        elif code.startswith(("4", "8", "9")):
            prefix = "bj"
        else:
            prefix = "sh"
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"Invalid stock symbol: {raw}")
    return code, f"{prefix}{code}", prefix


def now_shanghai() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None


def fmt_num(value: Any, digits: int = 2) -> str:
    val = safe_float(value)
    if val is None:
        return "-"
    return f"{val:.{digits}f}"


def fmt_pct(value: Any) -> str:
    val = safe_float(value)
    if val is None:
        return "-"
    return f"{val:.2f}%"


def fmt_amount(value: Any) -> str:
    val = safe_float(value)
    if val is None:
        return "-"
    if abs(val) >= 100_000_000:
        return f"{val / 100_000_000:.2f}亿"
    if abs(val) >= 10_000:
        return f"{val / 10_000:.2f}万"
    return f"{val:.2f}"


def quotation_client():
    return easyquotation.use("sina")


def _normalized_quote_map(data: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {str(code).zfill(6): quote for code, quote in (data or {}).items()}


def _usable_quote_count(quotes: dict[str, dict[str, Any]]) -> int:
    count = 0
    for quote in quotes.values():
        amount = safe_float(quote.get("成交额(万)"))
        if amount is None:
            amount = safe_float(quote.get("volume"))
        if (
            (safe_float(quote.get("now")) or 0) > 0
            and (safe_float(quote.get("close")) or 0) > 0
            and (amount or 0) > 0
        ):
            count += 1
    return count


def _fetch_provider(provider: str, normalized: list[str]) -> dict[str, dict[str, Any]]:
    client = easyquotation.use(provider)
    if provider == "tencent":
        return _normalized_quote_map(client.stocks(normalized, prefix=False))
    return _normalized_quote_map(client.real(normalized))


def fetch_realtime_with_source(symbols: Iterable[str]) -> tuple[dict[str, dict[str, Any]], str]:
    normalized = [normalize_symbol(symbol)[1] for symbol in symbols]
    if not normalized:
        return {}, "unavailable"

    # Sina clears the current-session price and amount fields before the A-share
    # opening auction. Tencent continues to expose the completed prior session,
    # which makes a pre-open refresh internally consistent.
    before_open = datetime.now().hour < 9 or (datetime.now().hour == 9 and datetime.now().minute < 25)
    providers = ("tencent", "sina") if before_open else ("sina", "tencent")
    best_quotes: dict[str, dict[str, Any]] = {}
    best_source = "unavailable"
    best_count = -1
    target = max(1, math.ceil(len(normalized) * 0.7))
    for provider in providers:
        try:
            quotes = _fetch_provider(provider, normalized)
        except Exception:
            continue
        usable = _usable_quote_count(quotes)
        if usable > best_count:
            best_quotes = quotes
            best_source = f"{provider.title()}/easyquotation"
            best_count = usable
        if usable >= target:
            break
    if best_count < target:
        return {}, "unavailable"
    return best_quotes, best_source


def fetch_realtime(symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
    quotes, _ = fetch_realtime_with_source(symbols)
    return quotes


def quote_rows(quotes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, q in quotes.items():
        now = safe_float(q.get("now"))
        prev_close = safe_float(q.get("close"))
        pct = None
        if now is not None and prev_close not in (None, 0):
            pct = (now - prev_close) / prev_close * 100
        bid1 = safe_float(q.get("bid1"))
        ask1 = safe_float(q.get("ask1"))
        spread = None
        if bid1 is not None and ask1 is not None:
            spread = ask1 - bid1
        rows.append(
            {
                "代码": code,
                "名称": q.get("name", ""),
                "时间": f"{q.get('date', '')} {q.get('time', '')}".strip(),
                "现价": fmt_num(now),
                "涨跌幅": fmt_pct(pct),
                "最高": fmt_num(q.get("high")),
                "最低": fmt_num(q.get("low")),
                "买一": fmt_num(bid1),
                "卖一": fmt_num(ask1),
                "价差": fmt_num(spread, 3),
                "成交量": fmt_amount(q.get("turnover")),
                "成交额": fmt_amount(q.get("volume")),
            }
        )
    return rows


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No data.")
        return
    print(tabulate(rows, headers="keys", tablefmt="github", showindex=False))


def command_quote(args: argparse.Namespace) -> None:
    quotes = fetch_realtime(args.symbols)
    print_table(quote_rows(quotes))


def bid_ask_from_easyquotation(symbol: str) -> dict[str, Any]:
    code, _, _ = normalize_symbol(symbol)
    quote = fetch_realtime([symbol]).get(code, {})
    return quote


def command_book(args: argparse.Namespace) -> None:
    code, _, _ = normalize_symbol(args.symbol)
    print(f"{code} 五档盘口")
    try:
        df = ak.stock_bid_ask_em(symbol=code)
        print(tabulate(df, headers="keys", tablefmt="github", showindex=False))
        return
    except Exception as exc:
        print(f"AKShare/Eastmoney 盘口失败，回退到 Sina: {type(exc).__name__}: {exc}", file=sys.stderr)

    quote = bid_ask_from_easyquotation(args.symbol)
    if not quote:
        print("No bid/ask data.")
        return
    rows = []
    for level in range(5, 0, -1):
        rows.append({"档位": f"卖{level}", "价格": fmt_num(quote.get(f"ask{level}")), "量": fmt_amount(quote.get(f"ask{level}_volume"))})
    for level in range(1, 6):
        rows.append({"档位": f"买{level}", "价格": fmt_num(quote.get(f"bid{level}")), "量": fmt_amount(quote.get(f"bid{level}_volume"))})
    print_table(rows)


def fetch_history(symbol: str, days: int = 160, adjust: str = "qfq") -> pd.DataFrame:
    code, _, _ = normalize_symbol(symbol)
    end = datetime.now()
    start = end - timedelta(days=max(days * 3, 220))
    if os.getenv("A_STOCK_KLINE_SOURCE", "").strip().lower() == "sina":
        df = fetch_history_sina(symbol, days)
        df.attrs["source"] = "sina_unadjusted"
        return df
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adjust,
        )
        df.attrs["source"] = f"eastmoney_{adjust or 'none'}"
        return df.tail(days).reset_index(drop=True)
    except Exception as exc:
        print(f"AKShare/Eastmoney K线失败，回退到 Sina 未复权K线: {type(exc).__name__}: {exc}", file=sys.stderr)
        df = fetch_history_sina(symbol, days)
        df.attrs["source"] = "sina_unadjusted"
        return df


def fetch_history_sina(symbol: str, days: int) -> pd.DataFrame:
    code, easy_symbol, _ = normalize_symbol(symbol)
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData"
    params = {"symbol": easy_symbol, "scale": "240", "ma": "no", "datalen": str(max(days, 180))}
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    match = re.search(r"\((\[.*\])\)", response.text, re.S)
    if not match:
        raise RuntimeError("Sina K-line response did not contain JSON data")
    raw = json.loads(match.group(1))
    df = pd.DataFrame(raw)
    if df.empty:
        return df
    out = pd.DataFrame(
        {
            "日期": df["day"],
            "股票代码": code,
            "开盘": pd.to_numeric(df["open"], errors="coerce"),
            "收盘": pd.to_numeric(df["close"], errors="coerce"),
            "最高": pd.to_numeric(df["high"], errors="coerce"),
            "最低": pd.to_numeric(df["low"], errors="coerce"),
            "成交量": pd.to_numeric(df["volume"], errors="coerce"),
        }
    )
    out["成交额"] = None
    out["振幅"] = (out["最高"] - out["最低"]) / out["收盘"].shift(1) * 100
    out["涨跌额"] = out["收盘"].diff()
    out["涨跌幅"] = out["收盘"].pct_change() * 100
    out["换手率"] = None
    out = out.tail(days).reset_index(drop=True)
    out.attrs["source"] = "sina_unadjusted"
    return out


def command_history(args: argparse.Namespace) -> None:
    df = fetch_history(args.symbol, args.days, args.adjust)
    if args.csv:
        path = Path(args.csv).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        print(f"Saved CSV: {path}")
    print(tabulate(df.tail(args.tail), headers="keys", tablefmt="github", showindex=False))


def add_technical_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = pd.to_numeric(out["收盘"], errors="coerce")
    high = pd.to_numeric(out["最高"], errors="coerce")
    low = pd.to_numeric(out["最低"], errors="coerce")
    out["MA5"] = close.rolling(5).mean()
    out["MA10"] = close.rolling(10).mean()
    out["MA20"] = close.rolling(20).mean()
    out["MA60"] = close.rolling(60).mean()
    try:
        from ta.momentum import RSIIndicator
        from ta.trend import MACD
        from ta.volatility import BollingerBands

        out["RSI14"] = RSIIndicator(close=close, window=14).rsi()
        macd = MACD(close=close)
        out["MACD"] = macd.macd()
        out["MACD_SIGNAL"] = macd.macd_signal()
        out["MACD_DIFF"] = macd.macd_diff()
        boll = BollingerBands(close=close, window=20, window_dev=2)
        out["BOLL_UP"] = boll.bollinger_hband()
        out["BOLL_MID"] = boll.bollinger_mavg()
        out["BOLL_LOW"] = boll.bollinger_lband()
        out["ATR_PROXY"] = (high - low).rolling(14).mean()
    except Exception:
        pass
    return out


def command_technical(args: argparse.Namespace) -> None:
    df = add_technical_columns(fetch_history(args.symbol, args.days, args.adjust))
    columns = [
        "日期",
        "收盘",
        "涨跌幅",
        "成交量",
        "MA5",
        "MA10",
        "MA20",
        "MA60",
        "RSI14",
        "MACD_DIFF",
        "BOLL_UP",
        "BOLL_LOW",
    ]
    columns = [col for col in columns if col in df.columns]
    print(tabulate(df[columns].tail(args.tail), headers="keys", tablefmt="github", showindex=False, floatfmt=".2f"))


def command_market(args: argparse.Namespace) -> None:
    df = ak.stock_zh_a_spot()
    if args.main_board:
        codes = df["代码"].astype(str).str.extract(r"(\d{6})", expand=False).fillna("")
        df = df[codes.str.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))].copy()
    sort_map = {"pct": "涨跌幅", "amount": "成交额", "volume": "成交量"}
    sort_col = sort_map[args.by]
    df[sort_col] = pd.to_numeric(df[sort_col], errors="coerce")
    cols = ["代码", "名称", "最新价", "涨跌幅", "最高", "最低", "成交量", "成交额", "时间戳"]
    cols = [col for col in cols if col in df.columns]
    top = df.sort_values(sort_col, ascending=False).head(args.limit)[cols]
    bottom = df.sort_values(sort_col, ascending=True).head(args.limit)[cols]
    print("涨幅/成交排序靠前")
    print(tabulate(top, headers="keys", tablefmt="github", showindex=False))
    print("\n排序靠后")
    print(tabulate(bottom, headers="keys", tablefmt="github", showindex=False))


def fetch_fund_flow(symbol: str) -> pd.DataFrame:
    code, _, prefix = normalize_symbol(symbol)
    market = "sh" if prefix == "sh" else "sz"
    return ak.stock_individual_fund_flow(stock=code, market=market)


def command_fund_flow(args: argparse.Namespace) -> None:
    df = fetch_fund_flow(args.symbol)
    print(tabulate(df.tail(args.tail), headers="keys", tablefmt="github", showindex=False, floatfmt=".2f"))


def command_news(args: argparse.Namespace) -> None:
    code, _, _ = normalize_symbol(args.symbol)
    df = ak.stock_news_em(symbol=code)
    cols = ["发布时间", "文章来源", "新闻标题", "新闻链接"]
    cols = [col for col in cols if col in df.columns]
    print(tabulate(df[cols].head(args.limit), headers="keys", tablefmt="github", showindex=False))


def latest_technical_summary(symbol: str) -> dict[str, Any]:
    df = add_technical_columns(fetch_history(symbol, 180, "qfq"))
    latest = df.iloc[-1].to_dict() if not df.empty else {}
    previous = df.iloc[-2].to_dict() if len(df) > 1 else {}
    return {"latest": latest, "previous": previous}


def book_metrics(quote: dict[str, Any]) -> dict[str, Any]:
    bid_vol = sum(safe_float(quote.get(f"bid{i}_volume")) or 0 for i in range(1, 6))
    ask_vol = sum(safe_float(quote.get(f"ask{i}_volume")) or 0 for i in range(1, 6))
    bid1 = safe_float(quote.get("bid1"))
    ask1 = safe_float(quote.get("ask1"))
    now = safe_float(quote.get("now"))
    spread = (ask1 - bid1) if bid1 is not None and ask1 is not None else None
    spread_pct = (spread / now * 100) if spread is not None and now not in (None, 0) else None
    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) else None
    return {
        "bid_vol_5": bid_vol,
        "ask_vol_5": ask_vol,
        "spread": spread,
        "spread_pct": spread_pct,
        "book_imbalance": imbalance,
    }


def local_analysis(symbol: str) -> tuple[str, dict[str, Any]]:
    code, _, _ = normalize_symbol(symbol)
    quote = fetch_realtime([symbol]).get(code, {})
    tech = latest_technical_summary(symbol)
    latest = tech["latest"]
    metrics = book_metrics(quote)
    lines = [f"# {code} {quote.get('name', '')} 盘口与技术分析", "", f"- 生成时间: {now_shanghai()}"]

    now_price = safe_float(quote.get("now"))
    prev_close = safe_float(quote.get("close"))
    pct = (now_price - prev_close) / prev_close * 100 if now_price is not None and prev_close not in (None, 0) else None
    lines.append(f"- 当前价: {fmt_num(now_price)}，日内涨跌幅: {fmt_pct(pct)}，成交额: {fmt_amount(quote.get('volume'))}")
    lines.append(
        f"- 五档盘口: 买盘量 {fmt_amount(metrics['bid_vol_5'])}，卖盘量 {fmt_amount(metrics['ask_vol_5'])}，"
        f"价差 {fmt_num(metrics['spread'], 3)}，盘口不平衡 {fmt_pct((metrics['book_imbalance'] or 0) * 100 if metrics['book_imbalance'] is not None else None)}"
    )

    close = safe_float(latest.get("收盘"))
    ma5 = safe_float(latest.get("MA5"))
    ma20 = safe_float(latest.get("MA20"))
    ma60 = safe_float(latest.get("MA60"))
    rsi = safe_float(latest.get("RSI14"))
    macd_diff = safe_float(latest.get("MACD_DIFF"))
    boll_up = safe_float(latest.get("BOLL_UP"))
    boll_low = safe_float(latest.get("BOLL_LOW"))
    trend_notes: list[str] = []
    if close is not None and ma5 is not None and ma20 is not None:
        if close > ma5 > ma20:
            trend_notes.append("短线价格站上 MA5/MA20，趋势偏强")
        elif close < ma5 < ma20:
            trend_notes.append("短线价格跌破 MA5/MA20，趋势偏弱")
        else:
            trend_notes.append("均线结构混合，趋势未形成单边共振")
    if ma20 is not None and ma60 is not None:
        trend_notes.append("MA20 高于 MA60" if ma20 > ma60 else "MA20 低于 MA60")
    if rsi is not None:
        if rsi >= 70:
            trend_notes.append("RSI14 高于 70，短线过热")
        elif rsi <= 30:
            trend_notes.append("RSI14 低于 30，短线超卖")
        else:
            trend_notes.append(f"RSI14={rsi:.1f}，未到极端区")
    if macd_diff is not None:
        trend_notes.append("MACD 柱为正" if macd_diff > 0 else "MACD 柱为负")
    if close is not None and boll_up is not None and boll_low is not None:
        if close > boll_up:
            trend_notes.append("收盘价突破布林上轨")
        elif close < boll_low:
            trend_notes.append("收盘价跌破布林下轨")
    lines.extend(["", "## 本地规则判断"])
    lines.extend(f"- {item}" for item in trend_notes)

    try:
        fund = fetch_fund_flow(symbol).tail(1)
        if not fund.empty:
            row = fund.iloc[-1].to_dict()
            lines.append(
                f"- 最近资金流: {row.get('日期')} 主力净流入 {fmt_amount(row.get('主力净流入-净额'))}，"
                f"净占比 {fmt_pct(row.get('主力净流入-净占比'))}"
            )
    except Exception as exc:
        lines.append(f"- 资金流读取失败: {type(exc).__name__}: {exc}")

    try:
        news = ak.stock_news_em(symbol=code).head(3)
        if not news.empty:
            lines.extend(["", "## 近三条新闻"])
            for _, row in news.iterrows():
                lines.append(f"- {row.get('发布时间', '')} {row.get('新闻标题', '')} ({row.get('文章来源', '')})")
    except Exception as exc:
        lines.append(f"- 新闻读取失败: {type(exc).__name__}: {exc}")

    lines.extend(["", "> 仅用于行情监控和研究，不构成投资建议。"])
    payload = {"quote": quote, "technical": latest, "book": metrics}
    return "\n".join(lines), payload


def llm_analysis(local_report: str, payload: dict[str, Any]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "LLM 未启用：请在 .env 中设置 OPENAI_API_KEY 后加 --llm。"
    from openai import OpenAI

    kwargs: dict[str, Any] = {"api_key": api_key}
    if os.getenv("OPENAI_BASE_URL"):
        kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**kwargs)
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = (
        "你是A股交易研究助手。基于给定盘口、技术指标、资金流和新闻，输出严格分段分析。"
        "要求：1) 不给保证性结论；2) 标出多空证据；3) 给出需要继续监控的价位/信号；"
        "4) 明确这不是投资建议。\n\n"
        f"本地分析:\n{local_report}\n\n结构化数据:\n{json.dumps(payload, ensure_ascii=False, default=str)[:12000]}"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def command_analyze(args: argparse.Namespace) -> None:
    local_report, payload = local_analysis(args.symbol)
    if args.output:
        path = Path(args.output).expanduser().resolve()
    else:
        code, _, _ = normalize_symbol(args.symbol)
        path = OUTPUT_DIR / f"analysis_{code}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = local_report
    if args.llm:
        report += "\n\n## LLM 分析\n\n" + llm_analysis(local_report, payload)
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {path}")


def watch_once(symbols: list[str], previous: dict[str, float], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, float]]:
    quotes = fetch_realtime(symbols)
    rows = []
    next_previous = previous.copy()
    for code, q in quotes.items():
        now_price = safe_float(q.get("now"))
        prev_close = safe_float(q.get("close"))
        pct_day = (now_price - prev_close) / prev_close * 100 if now_price is not None and prev_close not in (None, 0) else None
        prev_tick = previous.get(code)
        pct_tick = (now_price - prev_tick) / prev_tick * 100 if now_price is not None and prev_tick not in (None, 0) else None
        metrics = book_metrics(q)
        alerts = []
        if pct_day is not None and abs(pct_day) >= args.day_pct_alert:
            alerts.append(f"日内涨跌 {pct_day:.2f}%")
        if pct_tick is not None and abs(pct_tick) >= args.tick_pct_alert:
            alerts.append(f"较上次 {pct_tick:.2f}%")
        if metrics["book_imbalance"] is not None and abs(metrics["book_imbalance"]) >= args.imbalance_alert:
            side = "买盘强" if metrics["book_imbalance"] > 0 else "卖盘强"
            alerts.append(f"{side} {metrics['book_imbalance']:.2f}")
        rows.append(
            {
                "代码": code,
                "名称": q.get("name", ""),
                "时间": f"{q.get('date', '')} {q.get('time', '')}".strip(),
                "现价": fmt_num(now_price),
                "日涨跌": fmt_pct(pct_day),
                "tick涨跌": fmt_pct(pct_tick),
                "买一": fmt_num(q.get("bid1")),
                "卖一": fmt_num(q.get("ask1")),
                "五档买量": fmt_amount(metrics["bid_vol_5"]),
                "五档卖量": fmt_amount(metrics["ask_vol_5"]),
                "盘口差": fmt_pct(metrics["book_imbalance"] * 100 if metrics["book_imbalance"] is not None else None),
                "警报": "; ".join(alerts),
            }
        )
        if now_price is not None:
            next_previous[code] = now_price
    return rows, next_previous


def write_watch_outputs(rows: list[dict[str, Any]], path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": now_shanghai(), "rows": rows}
    with (path / "watch_ticks.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    md = ["# A股实时盘口监控", "", f"- 更新时间: {payload['timestamp']}", ""]
    md.append(tabulate(rows, headers="keys", tablefmt="github", showindex=False))
    md.append("")
    md.append("> 数据来自 Sina/easyquotation；用于监控和研究，不构成投资建议。")
    (path / "latest_watch.md").write_text("\n".join(md), encoding="utf-8")


def command_watch(args: argparse.Namespace) -> None:
    if args.watchlist:
        symbols = [line.strip() for line in Path(args.watchlist).read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    else:
        symbols = args.symbols
    if not symbols:
        raise SystemExit("No symbols. Provide symbols or --watchlist.")
    iterations = args.iterations
    previous: dict[str, float] = {}
    out = Path(args.output_dir).expanduser().resolve()
    count = 0
    while iterations == 0 or count < iterations:
        rows, previous = watch_once(symbols, previous, args)
        write_watch_outputs(rows, out)
        print(f"\n[{now_shanghai()}] watch iteration {count + 1}")
        print_table(rows)
        count += 1
        if iterations != 0 and count >= iterations:
            break
        time.sleep(args.interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share realtime quote, order book, monitoring, and analysis CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    quote = sub.add_parser("quote", help="Realtime quote for one or more symbols.")
    quote.add_argument("symbols", nargs="+")
    quote.set_defaults(func=command_quote)

    book = sub.add_parser("book", help="Five-level bid/ask order book.")
    book.add_argument("symbol")
    book.set_defaults(func=command_book)

    history = sub.add_parser("history", help="Daily historical K data.")
    history.add_argument("symbol")
    history.add_argument("--days", type=int, default=60)
    history.add_argument("--tail", type=int, default=10)
    history.add_argument("--adjust", choices=["", "qfq", "hfq"], default="qfq")
    history.add_argument("--csv")
    history.set_defaults(func=command_history)

    technical = sub.add_parser("technical", help="Technical indicators.")
    technical.add_argument("symbol")
    technical.add_argument("--days", type=int, default=180)
    technical.add_argument("--tail", type=int, default=12)
    technical.add_argument("--adjust", choices=["", "qfq", "hfq"], default="qfq")
    technical.set_defaults(func=command_technical)

    market = sub.add_parser("market", help="Whole-market snapshot from Sina.")
    market.add_argument("--limit", type=int, default=10)
    market.add_argument("--by", choices=["pct", "amount", "volume"], default="pct")
    market.add_argument("--main-board", action="store_true")
    market.set_defaults(func=command_market)

    fund = sub.add_parser("fund-flow", help="Individual stock fund flow.")
    fund.add_argument("symbol")
    fund.add_argument("--tail", type=int, default=8)
    fund.set_defaults(func=command_fund_flow)

    news = sub.add_parser("news", help="Recent Eastmoney stock news.")
    news.add_argument("symbol")
    news.add_argument("--limit", type=int, default=8)
    news.set_defaults(func=command_news)

    analyze = sub.add_parser("analyze", help="Local technical/order-book/news/fund-flow analysis; optional LLM.")
    analyze.add_argument("symbol")
    analyze.add_argument("--llm", action="store_true")
    analyze.add_argument("--output")
    analyze.set_defaults(func=command_analyze)

    watch = sub.add_parser("watch", help="Loop realtime order-book monitoring.")
    watch.add_argument("symbols", nargs="*")
    watch.add_argument("--watchlist")
    watch.add_argument("--interval", type=int, default=10)
    watch.add_argument("--iterations", type=int, default=1, help="0 means run forever.")
    watch.add_argument("--output-dir", default=str(OUTPUT_DIR))
    watch.add_argument("--day-pct-alert", type=float, default=3.0)
    watch.add_argument("--tick-pct-alert", type=float, default=0.5)
    watch.add_argument("--imbalance-alert", type=float, default=0.55)
    watch.set_defaults(func=command_watch)
    return parser


def main() -> None:
    load_dotenv(ROOT / ".env")
    OUTPUT_DIR.mkdir(exist_ok=True)
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
