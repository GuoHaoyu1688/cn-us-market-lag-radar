#!/usr/bin/env python3
from __future__ import annotations

import bisect
import hashlib
import json
import math
import re
import time
from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from forecasting.market_specs import classify_cn_board


SH_TZ = ZoneInfo("Asia/Shanghai")
MAX_SELECTED_CONCEPTS = 24
MAX_TEMPLATE_CONCEPTS = 8
MIN_INDUSTRY_CONCEPTS = 8
MAX_COMPANIES_PER_CONCEPT = 8

SINA_BOARD_SOURCES = (
    {
        "key": "sina_concept",
        "label": "新浪概念",
        "url": "http://money.finance.sina.com.cn/q/view/newFLJK.php",
        "params": {"param": "class"},
    },
    {
        "key": "sina_standard_industry",
        "label": "证监会行业",
        "url": "http://money.finance.sina.com.cn/q/view/newFLJK.php",
        "params": {"param": "industry"},
    },
    {
        "key": "sina_broad_industry",
        "label": "新浪行业",
        "url": "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
        "params": {},
    },
)

EXCLUDED_BOARD_TERMS = (
    "ST板块",
    "三板精选",
    "融资融券",
    "基金重仓",
    "保险重仓",
    "QFII重仓",
    "含H股",
    "含B股",
    "含GDR",
    "科创50",
    "央企50",
    "超大盘",
    "次新股",
    "退市",
)

US_MAPPING_RULES: tuple[dict[str, Any], ...] = (
    {
        "patterns": ("存储", "内存", "NAND", "DRAM", "半导体", "芯片"),
        "tickers": ("MU", "WDC", "STX", "SNDK", "PSTG", "NTAP", "AMAT", "LRCX"),
        "label": "美股存储与半导体链",
        "keywords": ("memory", "storage", "semiconductor", "NAND", "DRAM"),
    },
    {
        "patterns": ("银行", "货币金融", "金融行业", "证券", "资本市场", "保险", "其他金融"),
        "tickers": ("JPM", "BAC", "WFC", "C", "GS", "MS", "AIG", "MET"),
        "label": "美股金融条件代理",
        "keywords": ("banks", "credit", "yield curve", "financial conditions"),
    },
    {
        "patterns": ("白酒", "酿酒", "酒、饮料", "饮料"),
        "tickers": ("DEO", "BUD", "STZ", "KO", "PEP", "KDP"),
        "label": "全球酒饮与消费品代理",
        "keywords": ("spirits", "beverage", "consumer staples", "premium consumption"),
    },
    {
        "patterns": ("食品", "消费", "商业百货", "零售", "奢侈", "家电", "家具", "服装"),
        "tickers": ("WMT", "COST", "TGT", "PG", "MDLZ", "NKE", "LOW", "HD"),
        "label": "美股消费与零售代理",
        "keywords": ("consumer", "retail", "staples", "discretionary"),
    },
    {
        "patterns": ("酒店", "旅游", "住宿", "餐饮", "航空运输"),
        "tickers": ("MAR", "HLT", "BKNG", "EXPE", "RCL", "CCL", "DAL", "UAL"),
        "label": "美股出行与酒店代理",
        "keywords": ("travel", "hotel", "airline", "leisure"),
    },
    {
        "patterns": ("电力", "发电", "智能电网", "水力", "火力", "燃气生产", "供水供气"),
        "tickers": ("NEE", "SO", "DUK", "CEG", "VST", "AES", "GEV", "ETN"),
        "label": "美股公用事业与电力设备链",
        "keywords": ("utilities", "power generation", "grid", "electricity"),
    },
    {
        "patterns": ("煤炭",),
        "tickers": ("BTU", "CEIX", "HCC", "ARLP", "AMR", "NRP"),
        "label": "美股煤炭与燃料链",
        "keywords": ("coal", "fuel", "power demand"),
    },
    {
        "patterns": ("石油", "天然气", "页岩气", "油气"),
        "tickers": ("XOM", "CVX", "COP", "OXY", "SLB", "EQT", "LNG", "WMB"),
        "label": "美股油气链",
        "keywords": ("oil", "natural gas", "LNG", "energy"),
    },
    {
        "patterns": ("有色", "金属", "钢铁", "铜", "黄金", "矿"),
        "tickers": ("FCX", "SCCO", "NEM", "BHP", "RIO", "NUE", "CLF", "AA"),
        "label": "美股金属与矿业链",
        "keywords": ("metals", "mining", "copper", "steel", "gold"),
    },
    {
        "patterns": ("化工", "化学", "化纤", "农药化肥", "塑料", "橡胶"),
        "tickers": ("DOW", "LYB", "ECL", "APD", "SHW", "MOS", "CF", "CTVA"),
        "label": "美股化工与材料链",
        "keywords": ("chemicals", "materials", "fertilizer", "specialty chemicals"),
    },
    {
        "patterns": ("医药", "生物", "创新药", "制药", "疫苗", "CXO", "CRO"),
        "tickers": ("LLY", "JNJ", "PFE", "MRK", "ABBV", "AMGN", "GILD", "REGN"),
        "label": "美股医药与生物科技链",
        "keywords": ("pharma", "biotech", "drug development", "healthcare"),
    },
    {
        "patterns": ("医疗器械", "医疗设备", "卫生",),
        "tickers": ("ISRG", "MDT", "SYK", "BSX", "ABT", "EW", "BDX"),
        "label": "美股医疗器械链",
        "keywords": ("medical devices", "surgery", "diagnostics", "healthcare"),
    },
    {
        "patterns": ("汽车", "摩托车", "高压快充", "华为汽车", "固态电池", "锂电池"),
        "tickers": ("TSLA", "GM", "F", "RIVN", "APTV", "BWA", "ALB", "QS"),
        "label": "美股汽车与电动化链",
        "keywords": ("autos", "EV", "battery", "charging"),
    },
    {
        "patterns": ("机械", "仪器仪表", "机器人", "设备制造", "电器行业", "发电设备"),
        "tickers": ("CAT", "DE", "HON", "ROK", "ETN", "EMR", "GEV", "PH"),
        "label": "美股工业设备与自动化链",
        "keywords": ("industrials", "automation", "machinery", "equipment"),
    },
    {
        "patterns": ("军工", "航空航天", "飞机", "船舶制造", "卫星", "海工装备"),
        "tickers": ("RTX", "LMT", "NOC", "GD", "BA", "HII", "KTOS", "AVAV"),
        "label": "美股国防与航空航天链",
        "keywords": ("defense", "aerospace", "drone", "shipbuilding"),
    },
    {
        "patterns": ("航运", "运输", "港口", "物流", "仓储", "邮政", "水上运输"),
        "tickers": ("UPS", "FDX", "UNP", "CSX", "MATX", "ZIM", "DAC", "SBLK"),
        "label": "美股运输与物流链",
        "keywords": ("transportation", "logistics", "shipping", "freight"),
    },
    {
        "patterns": ("房地产", "房屋建筑", "建筑装饰", "建筑建材", "水泥"),
        "tickers": ("PLD", "O", "SPG", "CBRE", "VMC", "MLM", "LEN", "DHI"),
        "label": "美股地产与建筑材料代理",
        "keywords": ("real estate", "construction", "building materials"),
    },
    {
        "patterns": ("农业", "农林牧渔", "种业", "鸡肉", "水产品", "乡村振兴"),
        "tickers": ("ADM", "BG", "DE", "CTVA", "MOS", "TSN", "CALM"),
        "label": "美股农业与食品原料链",
        "keywords": ("agriculture", "grain", "fertilizer", "protein"),
    },
    {
        "patterns": ("传媒", "影视", "新闻", "文化", "出版", "娱乐"),
        "tickers": ("DIS", "NFLX", "WBD", "PARA", "SPOT", "FOXA", "NWSA"),
        "label": "美股传媒与娱乐代理",
        "keywords": ("media", "streaming", "entertainment", "advertising"),
    },
    {
        "patterns": ("软件", "互联网", "电子信息", "计算机", "通信", "物联网", "鸿蒙"),
        "tickers": ("MSFT", "ORCL", "CRM", "NOW", "CSCO", "ANET", "IBM", "GOOG"),
        "label": "美股软件、云与通信代理",
        "keywords": ("software", "cloud", "networking", "internet"),
    },
    {
        "patterns": ("环保", "生态保护", "水利", "垃圾分类", "低碳", "碳中和"),
        "tickers": ("WM", "RSG", "AWK", "WTRG", "XYL", "ECL", "NEE"),
        "label": "美股环保与水务代理",
        "keywords": ("environmental services", "water", "waste", "decarbonization"),
    },
    {
        "patterns": ("造纸", "纸制品", "印刷包装", "包装"),
        "tickers": ("IP", "PKG", "GPK", "SEE", "BALL", "AVY"),
        "label": "美股纸业与包装链",
        "keywords": ("paper", "packaging", "containers"),
    },
)


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def average(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None and math.isfinite(value)]
    return sum(usable) / len(usable) if usable else None


def quote_change(quote: dict[str, Any]) -> float | None:
    current = safe_float(quote.get("now") or quote.get("trade"))
    previous = safe_float(quote.get("close") or quote.get("settlement"))
    if current is None or previous is None or previous <= 0:
        return safe_float(quote.get("changepercent") or quote.get("涨跌(%)") or quote.get("涨跌幅"))
    return (current / previous - 1) * 100


def quote_amount(quote: dict[str, Any]) -> float:
    explicit = safe_float(quote.get("成交额(万)") or quote.get("成交额") or quote.get("amount"))
    if explicit is not None and explicit > 0:
        return explicit
    # Sina names turnover as share volume and volume as traded value.
    if quote.get("date") is not None or quote.get("time") is not None:
        value = safe_float(quote.get("volume"))
        return value if value is not None and value > 0 else 0
    current = safe_float(quote.get("now") or quote.get("trade"))
    share_volume = safe_float(quote.get("volume"))
    if current is not None and current > 0 and share_volume is not None and share_volume > 0:
        return current * share_volume
    return 0


def request_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
    retries: int = 3,
) -> Any:
    error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=18)
            response.raise_for_status()
            text = response.text.strip()
            if not text:
                raise ValueError("empty response")
            if text[0] not in "[{":
                start = min(value for value in (text.find("{"), text.find("[")) if value >= 0)
                text = text[start:]
            return json.loads(text)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            error = exc
            if attempt < retries - 1:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"sector source unavailable: {error}")


def board_is_usable(name: str, company_count: int) -> bool:
    if company_count < 4 or not name.strip():
        return False
    return not any(term in name for term in EXCLUDED_BOARD_TERMS)


def parse_board_payload(payload: dict[str, str], source: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, raw in payload.items():
        values = str(raw).split(",")
        if len(values) < 13:
            continue
        # Sina repeats the board node in column 0; the display name and member
        # count are columns 1 and 2 respectively.
        board_label = values[0].strip() or str(label)
        company_count = int(safe_float(values[2]) or 0)
        name = values[1].strip()
        if not board_is_usable(name, company_count):
            continue
        rows.append(
            {
                "board_label": board_label,
                "board_name": name,
                "source_type": source["key"],
                "source_label": source["label"],
                "company_count": company_count,
                "board_change_pct": safe_float(values[5]),
                "total_volume": safe_float(values[6]),
                "total_amount": safe_float(values[7]),
                "leader_symbol": values[8],
                "leader_change_pct": safe_float(values[9]),
                "leader_name": values[12].strip(),
            }
        )
    return rows


def fetch_board_universe(session: requests.Session) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for source in SINA_BOARD_SOURCES:
        try:
            payload = request_json(session, source["url"], source["params"])
            if not isinstance(payload, dict):
                raise ValueError("unexpected board payload")
            rows.extend(parse_board_payload(payload, source))
        except (RuntimeError, ValueError) as exc:
            errors.append(f"{source['label']}: {exc}")
    return rows, errors


def percentile(value: float | None, ordered: list[float]) -> float:
    if value is None or not ordered:
        return 0.0
    return bisect.bisect_right(ordered, value) / len(ordered)


def assign_preliminary_scores(rows: list[dict[str, Any]]) -> None:
    changes = sorted(value for value in (safe_float(row.get("board_change_pct")) for row in rows) if value is not None)
    leaders = sorted(value for value in (safe_float(row.get("leader_change_pct")) for row in rows) if value is not None)
    liquidity = sorted(
        math.log1p(value)
        for value in (
            (safe_float(row.get("total_amount")) or 0) / max(int(row.get("company_count") or 1), 1)
            for row in rows
        )
        if value > 0
    )
    for row in rows:
        amount_per_company = (safe_float(row.get("total_amount")) or 0) / max(int(row.get("company_count") or 1), 1)
        row["amount_per_company"] = amount_per_company
        row["preliminary_score"] = round(
            100
            * (
                percentile(safe_float(row.get("board_change_pct")), changes) * 0.62
                + percentile(safe_float(row.get("leader_change_pct")), leaders) * 0.18
                + percentile(math.log1p(amount_per_company) if amount_per_company > 0 else None, liquidity) * 0.20
            ),
            2,
        )


def fetch_board_constituents(session: requests.Session, board_label: str) -> list[dict[str, Any]]:
    url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    payload = request_json(
        session,
        url,
        {
            "page": "1",
            "num": "80",
            "sort": "amount",
            "asc": "0",
            "node": board_label,
            "symbol": "",
            "_s_r_a": "page",
        },
    )
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        code = re.sub(r"\D", "", str(item.get("code") or ""))
        name = str(item.get("name") or "").strip()
        trade = safe_float(item.get("trade"))
        amount = safe_float(item.get("amount")) or 0
        if len(code) != 6 or not classify_cn_board(code).eligible:
            continue
        if not name or "ST" in name.upper() or "退市" in name:
            continue
        rows.append(
            {
                "code": code,
                "name": name.replace(" ", ""),
                "trade": trade,
                "settlement": safe_float(item.get("settlement")),
                "change_pct": safe_float(item.get("changepercent")),
                "amount": amount,
                "turnover_ratio": safe_float(item.get("turnoverratio")),
                "market_cap": safe_float(item.get("mktcap")),
            }
        )
    return rows


def candidate_from_constituents(row: dict[str, Any], constituents: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [
        item
        for item in constituents
        if safe_float(item.get("change_pct")) is not None and (safe_float(item.get("amount")) or 0) > 0
    ]
    if len(usable) < 3:
        return None
    usable_changes = [float(item["change_pct"]) for item in usable]
    positives = sum(1 for value in usable_changes if value > 0)
    amounts = [safe_float(item.get("amount")) or 0 for item in usable]
    weighted_denominator = sum(amounts)
    weighted_change = (
        sum(value * amount for value, amount in zip(usable_changes, amounts, strict=False)) / weighted_denominator
        if weighted_denominator > 0
        else average(usable_changes)
    )
    return {
        **row,
        "constituents": usable,
        "valid_company_count": len(usable_changes),
        "breadth": positives / len(usable_changes),
        "median_change_pct": median(usable_changes),
        "weighted_change_pct": weighted_change,
        "leader_change_pct": max(usable_changes),
        "median_turnover": median(
            [value for value in (safe_float(item.get("turnover_ratio")) for item in usable) if value is not None] or [0]
        ),
        "amount_per_company": sum(amounts) / len(amounts),
    }


def merge_constituent_quote(item: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    if not quote:
        return item
    change = quote_change(quote)
    amount = quote_amount(quote)
    turnover_ratio = safe_float(quote.get("turnover")) if quote.get("成交额(万)") is not None else None
    return {
        **item,
        "name": str(quote.get("name") or item.get("name") or ""),
        "trade": safe_float(quote.get("now")) or item.get("trade"),
        "settlement": safe_float(quote.get("close")) or item.get("settlement"),
        "change_pct": change if change is not None else item.get("change_pct"),
        "amount": amount if amount > 0 else item.get("amount"),
        "turnover_ratio": turnover_ratio if turnover_ratio is not None else item.get("turnover_ratio"),
    }


def enrich_live_candidates(
    session: requests.Session,
    universe: list[dict[str, Any]],
    quote_fetcher: Callable[[list[str]], tuple[dict[str, dict[str, Any]], str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    memberships: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    # Recompute every board from all currently eligible A-share constituents. The Sina
    # aggregate can be distorted by corporate actions, so using it as a hard
    # prefilter can hide an otherwise hot sector.
    for row in universe:
        try:
            constituents = fetch_board_constituents(session, str(row.get("board_label") or ""))
        except RuntimeError:
            continue
        if constituents:
            memberships.append((row, constituents))

    direct = [
        candidate
        for row, constituents in memberships
        if (candidate := candidate_from_constituents(row, constituents)) is not None
    ]
    direct_ratio = len(direct) / len(universe) if universe else 0
    quote_source = "Sina board constituents"
    quote_as_of = None
    if direct_ratio < 0.70 and quote_fetcher is not None:
        codes = sorted({str(item.get("code") or "") for _, members in memberships for item in members if item.get("code")})
        try:
            quote_map, quote_source = quote_fetcher(codes)
        except Exception:
            quote_map, quote_source = {}, "unavailable"
        if quote_map:
            quote_as_of = next(
                (
                    str(quote.get("datetime") or f"{quote.get('date', '')} {quote.get('time', '')}").strip()
                    for quote in quote_map.values()
                    if quote.get("datetime") or quote.get("date")
                ),
                None,
            )
            memberships = [
                (row, [merge_constituent_quote(item, quote_map.get(str(item.get("code") or "")) or {}) for item in members])
                for row, members in memberships
            ]

    enriched = [
        candidate
        for row, constituents in memberships
        if (candidate := candidate_from_constituents(row, constituents)) is not None
    ]
    return enriched, {
        "membership_board_count": len(memberships),
        "direct_enriched_count": len(direct),
        "direct_enrichment_ratio": direct_ratio,
        "constituent_quote_source": quote_source,
        "constituent_quote_as_of": quote_as_of,
        "final_enrichment_ratio": len(enriched) / len(universe) if universe else 0,
    }


def template_candidates(templates: list[dict[str, Any]], quote_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for template in templates:
        constituents: list[dict[str, Any]] = []
        for company in template.get("cn_companies", []):
            code = str(getattr(company, "code", "") or "")
            name = str(getattr(company, "name", "") or "")
            quote = quote_map.get(code) or {}
            change = quote_change(quote)
            current = safe_float(quote.get("now"))
            amount = quote_amount(quote)
            if not classify_cn_board(code).eligible or change is None or current is None or current <= 0 or amount <= 0:
                continue
            if "ST" in name.upper() or "退市" in name:
                continue
            constituents.append(
                {
                    "code": code,
                    "name": name,
                    "change_pct": change,
                    "amount": amount,
                    "turnover_ratio": safe_float(quote.get("turnover")) if quote.get("成交额(万)") is not None else None,
                    "market_cap": None,
                }
            )
        if len(constituents) < 3:
            continue
        changes = [safe_float(item.get("change_pct")) for item in constituents]
        usable = [value for value in changes if value is not None]
        amounts = [safe_float(item.get("amount")) or 0 for item in constituents]
        weighted_denominator = sum(amounts)
        weighted = sum(value * amount for value, amount in zip(usable, amounts, strict=False)) / weighted_denominator
        candidates.append(
            {
                "board_label": str(template.get("id") or ""),
                "board_name": str(template.get("short_name") or template.get("name") or ""),
                "source_type": "supply_chain_template",
                "source_label": "细分供应链候选",
                "company_count": len(constituents),
                "valid_company_count": len(constituents),
                "board_change_pct": average(usable),
                "weighted_change_pct": weighted,
                "median_change_pct": median(usable),
                "leader_change_pct": max(usable),
                "breadth": sum(1 for value in usable if value > 0) / len(usable),
                "median_turnover": 0,
                "amount_per_company": sum(amounts) / len(amounts),
                "constituents": constituents,
                "template": template,
            }
        )
    return candidates


def assign_heat_scores(candidates: list[dict[str, Any]]) -> None:
    change_values = sorted(
        value
        for value in (
            safe_float(row.get("weighted_change_pct")) or safe_float(row.get("board_change_pct")) for row in candidates
        )
        if value is not None
    )
    median_values = sorted(value for value in (safe_float(row.get("median_change_pct")) for row in candidates) if value is not None)
    breadth_values = sorted(value for value in (safe_float(row.get("breadth")) for row in candidates) if value is not None)
    leader_values = sorted(value for value in (safe_float(row.get("leader_change_pct")) for row in candidates) if value is not None)
    liquidity_values = sorted(
        math.log1p(value)
        for value in (safe_float(row.get("amount_per_company")) for row in candidates)
        if value is not None and value > 0
    )
    turnover_values = sorted(value for value in (safe_float(row.get("median_turnover")) for row in candidates) if value is not None)
    for row in candidates:
        change = safe_float(row.get("weighted_change_pct")) or safe_float(row.get("board_change_pct"))
        amount = safe_float(row.get("amount_per_company"))
        score = 100 * (
            percentile(change, change_values) * 0.38
            + percentile(safe_float(row.get("median_change_pct")), median_values) * 0.17
            + percentile(safe_float(row.get("breadth")), breadth_values) * 0.22
            + percentile(safe_float(row.get("leader_change_pct")), leader_values) * 0.08
            + percentile(math.log1p(amount) if amount and amount > 0 else None, liquidity_values) * 0.10
            + percentile(safe_float(row.get("median_turnover")), turnover_values) * 0.05
        )
        row["heat_score"] = round(max(0, min(score, 100)), 2)

    candidates.sort(key=lambda row: (row.get("heat_score", 0), row.get("board_change_pct") or -999), reverse=True)
    for rank, row in enumerate(candidates, 1):
        row["universe_rank"] = rank


def constituent_codes(candidate: dict[str, Any]) -> set[str]:
    return {str(item.get("code") or "") for item in candidate.get("constituents", []) if item.get("code")}


def normalized_name(value: str) -> str:
    return re.sub(r"(概念|行业|制造业|服务业|生产和供应业|相关服务)$", "", value.replace(" ", ""))


def overlaps_selected(candidate: dict[str, Any], selected: list[dict[str, Any]]) -> bool:
    candidate_codes = constituent_codes(candidate)
    candidate_name = normalized_name(str(candidate.get("board_name") or ""))
    for current in selected:
        current_codes = constituent_codes(current)
        overlap = len(candidate_codes & current_codes) / max(min(len(candidate_codes), len(current_codes)), 1)
        current_name = normalized_name(str(current.get("board_name") or ""))
        if overlap >= 0.68:
            return True
        if candidate_name and current_name and candidate_name == current_name:
            return True
    return False


def select_candidates(candidates: list[dict[str, Any]], max_concepts: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    industries = [
        row
        for row in candidates
        if row.get("source_type") in {"sina_standard_industry", "sina_broad_industry"}
    ]
    for row in industries:
        if len(selected) >= MIN_INDUSTRY_CONCEPTS:
            break
        if not overlaps_selected(row, selected):
            selected.append(row)

    template_count = 0
    for row in candidates:
        if len(selected) >= max_concepts:
            break
        if row in selected or overlaps_selected(row, selected):
            continue
        if row.get("source_type") == "supply_chain_template":
            if template_count >= MAX_TEMPLATE_CONCEPTS:
                continue
            template_count += 1
        selected.append(row)
    return sorted(selected, key=lambda row: row.get("heat_score", 0), reverse=True)


def short_board_name(name: str) -> str:
    replacements = {
        "酒、饮料和精制茶制造业": "酒饮制造",
        "计算机、通信和其他电子设备制造业": "电子设备制造",
        "铁路、船舶、航空航天和其他运输设备制造业": "高端运输装备",
        "电力、热力生产和供应业": "电力热力",
        "石油加工、炼焦和核燃料加工业": "石化炼焦",
        "化学原料和化学制品制造业": "化学制品",
        "非金属矿物制品业": "非金属材料",
        "有色金属冶炼和压延加工业": "有色冶炼",
        "黑色金属冶炼和压延加工业": "钢铁冶炼",
    }
    shortened = replacements.get(name, name)
    return shortened[:12]


def us_mapping(board_name: str) -> dict[str, Any]:
    for rule in US_MAPPING_RULES:
        if any(pattern in board_name for pattern in rule["patterns"]):
            return {
                "tickers": list(rule["tickers"]),
                "label": rule["label"],
                "quality": "sector_proxy",
                "keywords": list(rule["keywords"]),
            }
    return {
        "tickers": ["SPY", "QQQ", "IWM", "DIA"],
        "label": "无直接美股产业映射，仅使用大盘背景",
        "quality": "broad_fallback",
        "keywords": ["market regime", "risk appetite", "broad market"],
    }


def dynamic_id(candidate: dict[str, Any]) -> str:
    raw = f"{candidate.get('source_type')}|{candidate.get('board_label')}|{candidate.get('board_name')}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"market-scan-{digest}"


def candidate_to_template(candidate: dict[str, Any], company_factory: Callable[[str, str, str, str], Any]) -> dict[str, Any]:
    discovery = {
        "activation_score": candidate.get("heat_score"),
        "heat_score": candidate.get("heat_score"),
        "universe_rank": candidate.get("universe_rank"),
        "board_change_pct": candidate.get("board_change_pct"),
        "weighted_change_pct": candidate.get("weighted_change_pct"),
        "median_change_pct": candidate.get("median_change_pct"),
        "breadth": candidate.get("breadth"),
        "amount_per_company": candidate.get("amount_per_company"),
        "source_label": candidate.get("source_label"),
        "board_label": candidate.get("board_label"),
        "valid_company_count": candidate.get("valid_company_count"),
        "method": "每次刷新全量扫描A股行业/概念，并按涨跌、广度、中位数表现、成交活跃和龙头强度重新排名",
        "activated_at_shanghai": datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
    }
    existing = candidate.get("template")
    if isinstance(existing, dict):
        template = dict(existing)
        template["dynamic"] = True
        template["source_type"] = "full_market_template"
        template["us_mapping_quality"] = "direct"
        template["us_mapping_label"] = "细分供应链直接映射"
        template["discovery"] = discovery
        return template

    name = str(candidate.get("board_name") or "未命名板块")
    mapping = us_mapping(name)
    companies = sorted(candidate.get("constituents", []), key=lambda row: safe_float(row.get("amount")) or 0, reverse=True)
    cn_companies = [
        company_factory(
            str(item.get("code") or ""),
            str(item.get("name") or ""),
            f"{name}活跃成分",
            f"本次全市场扫描中进入{name}成交活跃成分池；板块当日涨跌{safe_float(candidate.get('board_change_pct')) or 0:+.2f}%。",
        )
        for item in companies[:MAX_COMPANIES_PER_CONCEPT]
    ]
    return {
        "id": dynamic_id(candidate),
        "name": f"{name} / 当期全市场热度",
        "short_name": short_board_name(name),
        "trigger": (
            f"本次刷新从全市场板块中动态入选：当日涨跌{safe_float(candidate.get('board_change_pct')) or 0:+.2f}%，"
            f"成分上涨占比{(safe_float(candidate.get('breadth')) or 0) * 100:.1f}%。"
        ),
        "us_tickers": mapping["tickers"],
        "keywords": [name, *mapping["keywords"]],
        "news_query": f"{name} A股 行业 景气 订单 政策",
        "x_query": f'("{name}" OR {" OR ".join(mapping["keywords"][:3])})',
        "sources": [
            {"label": "新浪A股全市场板块行情", "url": "https://finance.sina.com.cn/stock/sl/"},
        ],
        "cn_companies": cn_companies,
        "driver": f"{candidate.get('source_label')} / A股实时轮动",
        "dynamic": True,
        "source_type": "full_market_scan",
        "us_mapping_quality": mapping["quality"],
        "us_mapping_label": mapping["label"],
        "discovery": discovery,
    }


def coverage_examples(universe: list[dict[str, Any]], candidates: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    selected_labels = {str(row.get("board_label") or "") for row in selected}
    candidate_by_label = {str(row.get("board_label") or ""): row for row in candidates}
    definitions = {
        "banking": ("银行", "货币金融", "金融行业"),
        "storage": ("存储", "内存", "半导体", "电子设备"),
        "consumer": ("消费", "食品", "零售", "商业百货", "家电", "旅游"),
        "liquor": ("白酒", "酿酒", "酒、饮料"),
    }
    output: dict[str, Any] = {}
    for key, patterns in definitions.items():
        matches = [row for row in universe if any(pattern in str(row.get("board_name") or "") for pattern in patterns)]
        matches.extend(
            row
            for row in candidates
            if row.get("source_type") == "supply_chain_template"
            and any(pattern in str(row.get("board_name") or "") for pattern in patterns)
        )
        if not matches:
            output[key] = {"scanned": False}
            continue
        best = max(
            matches,
            key=lambda row: safe_float(row.get("heat_score")) or safe_float(row.get("preliminary_score")) or 0,
        )
        enriched = candidate_by_label.get(str(best.get("board_label") or ""), best)
        output[key] = {
            "scanned": True,
            "name": best.get("board_name"),
            "source": best.get("source_label"),
            "selected": str(best.get("board_label") or "") in selected_labels,
            "universe_rank": enriched.get("universe_rank"),
            "heat_score": enriched.get("heat_score"),
            "preliminary_score": best.get("preliminary_score"),
            "change_pct": best.get("board_change_pct"),
        }
    return output


def discover_full_market_concepts(
    session: requests.Session,
    templates: list[dict[str, Any]],
    template_quote_map: dict[str, dict[str, Any]],
    company_factory: Callable[[str, str, str, str], Any],
    max_concepts: int = MAX_SELECTED_CONCEPTS,
    quote_fetcher: Callable[[list[str]], tuple[dict[str, dict[str, Any]], str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    universe, errors = fetch_board_universe(session)
    if not universe:
        return [], {
            "enabled": False,
            "mode": "full_market_rescan_failed",
            "errors": errors,
            "selected_count": 0,
        }

    assign_preliminary_scores(universe)
    live_candidates, enrichment = enrich_live_candidates(session, universe, quote_fetcher=quote_fetcher)
    if (safe_float(enrichment.get("final_enrichment_ratio")) or 0) < 0.70:
        errors.append(
            "板块成分有效覆盖不足70%，拒绝用残缺样本重排；"
            f"有效{len(live_candidates)}/{len(universe)}，行情源{enrichment.get('constituent_quote_source') or 'unknown'}"
        )
        return [], {
            "enabled": False,
            "mode": "full_market_rescan_degraded",
            "scan_time_shanghai": datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
            "universe_count": len(universe),
            "live_board_count": len(universe),
            "live_enriched_count": len(live_candidates),
            "selected_count": 0,
            "errors": errors,
            **enrichment,
        }
    fixed_candidates = template_candidates(templates, template_quote_map)
    candidates = [*live_candidates, *fixed_candidates]
    assign_heat_scores(candidates)
    selected = select_candidates(candidates, max_concepts)
    concepts = [candidate_to_template(row, company_factory) for row in selected]
    source_counts = Counter(str(row.get("source_type") or "unknown") for row in universe)
    selected_source_counts = Counter(str(row.get("source_type") or "unknown") for row in selected)
    metadata = {
        "enabled": True,
        "mode": "full_market_rescan",
        "scan_time_shanghai": datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M:%S CST"),
        "universe_count": len(universe) + len(fixed_candidates),
        "live_board_count": len(universe),
        "live_enriched_count": len(live_candidates),
        **enrichment,
        "template_candidate_count": len(fixed_candidates),
        "candidate_rule_count": len(universe) + len(fixed_candidates),
        "selected_count": len(selected),
        "max_dynamic_concepts": max_concepts,
        "min_activation_score": None,
        "source_counts": dict(source_counts),
        "selected_source_counts": dict(selected_source_counts),
        "errors": errors,
        "selection_policy": "全市场实时重扫并逐板块重算沪深京全部上市板块成分；行业至少8席、细分供应链模板最多8席；成分重合度达到68%自动去重",
        "coverage_examples": coverage_examples(universe, candidates, selected),
        "selected": [
            {
                "id": concept.get("id"),
                "short_name": concept.get("short_name"),
                "activation_score": concept.get("discovery", {}).get("heat_score"),
                "universe_rank": concept.get("discovery", {}).get("universe_rank"),
                "source": concept.get("discovery", {}).get("source_label"),
                "board_change_pct": concept.get("discovery", {}).get("board_change_pct"),
                "breadth": concept.get("discovery", {}).get("breadth"),
                "us_mapping_quality": concept.get("us_mapping_quality"),
            }
            for concept in concepts
        ],
        "candidate_scores": [
            {
                "name": row.get("board_name"),
                "source": row.get("source_label"),
                "source_type": row.get("source_type"),
                "universe_rank": row.get("universe_rank"),
                "heat_score": row.get("heat_score"),
                "change_pct": row.get("board_change_pct"),
                "breadth": row.get("breadth"),
                "selected": row in selected,
            }
            for row in candidates
        ],
    }
    return concepts, metadata
