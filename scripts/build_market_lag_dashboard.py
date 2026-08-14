#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import importlib.util
import json
import math
import os
import re
import ssl
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from full_market_sector_discovery import discover_full_market_concepts
from prediction_forward_ledger import update_prediction_ledger
from prediction_model_v6 import build_prediction_model_v6
from forecasting.market_specs import classify_cn_board


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "market_lag_dashboard"
DATA_PATH = OUTPUT / "data" / "dashboard.json"
DATA_JS_PATH = OUTPUT / "data" / "dashboard-data.js"
CHART_DATA_DIR = OUTPUT / "data" / "charts"
INLINE_CANDLE_DAYS = 252
LONG_CHART_DAYS = 1400
SH_TZ = ZoneInfo("Asia/Shanghai")
NY_TZ = ZoneInfo("America/New_York")
US_MARKET_PROXY_WEIGHTS = {"QQQ": 0.45, "SOXX": 0.35, "IWM": 0.20}
CN_MARKET_PROXY_WEIGHTS = {"000300.SS": 0.50, "000852.SS": 0.30, "399006.SZ": 0.20}
FOLLOW_HORIZONS = tuple(range(1, 11))
EVENT_HORIZONS = FOLLOW_HORIZONS + (15,)
ROUND_TRIP_COST_PCT = 0.35
VALIDATION_SHARE = 0.30
VALIDATION_MIN_SAMPLES = 6
DYNAMIC_DISCOVERY_MAX_CONCEPTS = 6
DYNAMIC_DISCOVERY_MIN_SCORE = 18
FULL_MARKET_SELECTED_CONCEPTS = 24
LEADER_TICKERS = {
    "NVDA",
    "AVGO",
    "TSM",
    "AMD",
    "MU",
    "ANET",
    "VRT",
    "ETN",
    "GEV",
    "DELL",
    "SMCI",
    "PLTR",
    "TSLA",
}
CORE_SUPPLIER_TICKERS = {
    "MRVL",
    "COHR",
    "LITE",
    "FN",
    "CRDO",
    "ALAB",
    "WDC",
    "STX",
    "SNDK",
    "AMKR",
    "ASML",
    "KLAC",
    "AMAT",
    "LRCX",
    "APH",
    "TEL",
    "GLW",
    "POWL",
    "HUBB",
    "BWXT",
    "CEG",
}
SPECULATIVE_TICKERS = {"OKLO", "SMR", "LEU", "UEC", "UUUU", "NXE", "NVTS", "AAOI", "AVAV", "KTOS"}
CONCEPT_DRIVERS = {
    "ai-storage-essd-hdd": "AI capex / 存储",
    "800vdc-ai-power": "数据中心电力",
    "hbm-cowos-packaging": "先进封装",
    "defense-drone-cuas": "军工无人系统",
    "optical-cpo-16t": "高速互联",
    "grid-transformer-ai": "数据中心电力",
    "gas-turbine-backup-power": "能源安全",
    "nuclear-smr-ai-power": "能源安全",
    "copper-pcb-highspeed": "高速互联",
    "glass-substrate-packaging": "先进封装",
    "ethernet-switch-asic": "高速互联",
    "liquid-cooling-cdu": "AI机柜基础设施",
    "robot-actuator-sensor": "机器人",
    "ai-server-odm-rack": "AI capex / 整机",
    "connector-backplane-aec": "高速互联",
    "hvlp-ccl-copperfoil": "高速互联",
}
PUBLIC_RESEARCH_FEEDS = [
    {
        "label": "IBKR Insights",
        "source": "IBKR Campus",
        "url": "https://www.interactivebrokers.com/campus/category/traders-insight/ibkr-market-insights/feed/",
    },
    {
        "label": "IBKR Economic Landscape",
        "source": "IBKR Campus",
        "url": "https://www.interactivebrokers.com/campus/category/traders-insight/ibkr-economic-landscape/feed/",
    },
    {
        "label": "Finimize on IBKR",
        "source": "IBKR Campus contributor",
        "url": "https://www.interactivebrokers.com/campus/contributors-categories/finimize/feed/",
    },
]


@dataclass(frozen=True)
class CnCompany:
    code: str
    name: str
    role: str
    reason: str


def cn(code: str, name: str, role: str, reason: str) -> CnCompany:
    return CnCompany(code=code, name=name, role=role, reason=reason)


def supported_a_share(code: str) -> bool:
    return classify_cn_board(code).eligible


CONCEPTS: list[dict[str, Any]] = [
    {
        "id": "optical-cpo-16t",
        "name": "光通信 / CPO / 1.6T",
        "short_name": "光通信",
        "trigger": "AI 集群通信瓶颈从 GPU 转向 800G/1.6T 光模块、硅光、CPO 与光交换。",
        "us_tickers": ["AVGO", "COHR", "LITE", "FN", "CRDO", "ALAB", "MRVL", "ANET"],
        "keywords": ["optical", "CPO", "1.6T", "3.2T", "silicon photonics", "OCI", "interconnect"],
        "news_query": "AI data center optical interconnect CPO 1.6T 3.2T",
        "x_query": "(CPO OR 1.6T OR 3.2T OR silicon photonics OR optical interconnect) (AI OR datacenter)",
        "sources": [
            {
                "label": "Broadcom OCI MSA",
                "url": "https://www.broadcom.com/company/news/product-releases/optical-scale-up-consortium-established-to-create-an-open-specification-for-ai-infrastructure",
            },
            {
                "label": "OFC 2026 1.6T/3.2T",
                "url": "https://blog.viavisolutions.com/2026/04/23/ofc-2026-1-6t-going-mainstream-the-emergence-of-3-2t/",
            },
        ],
        "cn_companies": [
            cn("600487", "亨通光电", "光纤光缆/光通信", "主板光通信底座，适合跟踪光纤、海缆、数据中心连接需求外溢。"),
            cn("600522", "中天科技", "光纤光缆/电力通信", "通信线缆与电力线缆交叉受益，适合放在光通信与电网升级的交集里观察。"),
            cn("002281", "光迅科技", "光器件/光模块", "主板稀缺光器件标的，比多数 CPO/高速光模块龙头更符合主板限制。"),
            cn("603083", "剑桥科技", "高速光模块/数通", "数通光模块弹性高，容易跟随美股光互联叙事波动。"),
            cn("600498", "烽火通信", "通信设备/光网络", "光网络设备与运营商侧链条，可作为低位补涨观察池。"),
        ],
    },
    {
        "id": "800vdc-ai-power",
        "name": "800VDC / AI 电源架构",
        "short_name": "800VDC",
        "trigger": "机柜功率向百千瓦到兆瓦级迁移，AC/DC 多级转换被 800VDC 架构挑战。",
        "us_tickers": ["VRT", "ETN", "POWL", "GEV", "HUBB", "NVTS", "TXN", "ON"],
        "keywords": ["800 VDC", "HVDC", "power shelf", "AI factory", "power architecture", "GaN", "SiC"],
        "news_query": "NVIDIA 800 VDC AI data center power architecture",
        "x_query": "(800VDC OR HVDC OR \"AI factory\" OR \"power shelf\") (NVIDIA OR datacenter)",
        "sources": [
            {"label": "NVIDIA 800 VDC", "url": "https://www.nvidia.com/en-gb/data-center/technologies/800-vdc-architecture/"},
            {
                "label": "NVIDIA 800 VDC technical blog",
                "url": "https://developer.nvidia.com/blog/nvidia-800-v-hvdc-architecture-will-power-the-next-generation-of-ai-factories/",
            },
        ],
        "cn_companies": [
            cn("002518", "科士达", "UPS/数据中心电源", "电源、UPS 与数据中心供配电链条，适合跟踪 800VDC 国产替代叙事。"),
            cn("002335", "科华数据", "数据中心电源/UPS", "数据中心供配电和电源系统，和高功率机柜升级相关。"),
            cn("002364", "中恒电气", "高压直流/通信电源", "通信电源与高压直流供电标签更直接，对 800VDC 概念敏感。"),
            cn("000400", "许继电气", "电力自动化/换流", "柔直、换流和电网装备能力，可映射中压到直流母线升级。"),
            cn("002851", "麦格米特", "电力电子/服务器电源", "电力电子与电源模块属性，适合作为 AI 机柜电源跟踪候选。"),
        ],
    },
    {
        "id": "liquid-cooling-cdu",
        "name": "液冷 CDU / 高密度散热",
        "short_name": "液冷CDU",
        "trigger": "GB200/GB300、Rubin 等机柜级平台把液冷从可选项推成基础设施。",
        "us_tickers": ["VRT", "MOD", "NVT", "ETN", "CARR", "JCI", "DELL", "SMCI"],
        "keywords": ["liquid cooling", "CDU", "cold plate", "rack cooling", "GB300", "NVL72"],
        "news_query": "GB300 NVL72 liquid cooling CDU AI data center",
        "x_query": "(CDU OR \"liquid cooling\" OR \"cold plate\") (GB300 OR NVL72 OR datacenter)",
        "sources": [
            {"label": "NVIDIA GB300 NVL72", "url": "https://www.nvidia.com/en-us/data-center/gb300-nvl72/"},
            {"label": "NVIDIA liquid-cooling readiness", "url": "https://www.nvidia.com/en-us/on-demand/session/gtc26-ex82328/"},
        ],
        "cn_companies": [
            cn("002837", "英维克", "精密温控/液冷", "数据中心温控和液冷标签清晰，是 A 股液冷主板核心观察点。"),
            cn("002050", "三花智控", "热管理部件", "热管理部件能力强，可映射冷板、阀件和系统级热管理。"),
            cn("000811", "冰轮环境", "工业制冷/温控", "工业制冷基础扎实，适合作为数据中心冷却外溢候选。"),
            cn("600481", "双良节能", "节能换热/冷却", "节能换热设备和系统集成，映射高功率数据中心冷却。"),
            cn("002011", "盾安环境", "热管理/制冷部件", "制冷部件与热管理属性，适合观察液冷扩散行情。"),
        ],
    },
    {
        "id": "hbm-cowos-packaging",
        "name": "HBM / CoWoS / 先进封装",
        "short_name": "HBM封装",
        "trigger": "AI 芯片供给由先进制程、HBM、CoWoS/2.5D 封装、载板与测试共同约束。",
        "us_tickers": ["TSM", "MU", "AMKR", "ASX", "TER", "KLAC", "AMAT", "LRCX"],
        "keywords": ["HBM", "CoWoS", "advanced packaging", "interposer", "ABF", "OSAT"],
        "news_query": "HBM CoWoS advanced packaging AI chip bottleneck",
        "x_query": "(HBM OR CoWoS OR \"advanced packaging\" OR interposer) AI",
        "sources": [
            {"label": "TSMC annual report", "url": "https://investor.tsmc.com/static/annualReports/2025/english/index.html"},
        ],
        "cn_companies": [
            cn("600584", "长电科技", "封测/先进封装", "主板封测龙头，直接映射 OSAT 和先进封装产能扩张。"),
            cn("002156", "通富微电", "封测/Chiplet", "封测与高性能计算客户链条明确，适合跟踪先进封装景气。"),
            cn("002185", "华天科技", "封测", "封测主板标的，可作为先进封装扩散候选。"),
            cn("002371", "北方华创", "半导体设备", "刻蚀、薄膜等设备能力，映射先进制程与先进封装资本开支。"),
            cn("002409", "雅克科技", "电子材料", "半导体材料属性，适合作为 HBM/封装材料外溢观察。"),
        ],
    },
    {
        "id": "copper-pcb-highspeed",
        "name": "高速铜缆 / PCB / AEC",
        "short_name": "高速铜缆PCB",
        "trigger": "短距互联里 DAC/AEC、PCB、连接器和 SerDes 仍是 AI 机柜降本关键。",
        "us_tickers": ["CRDO", "ALAB", "AVGO", "MRVL", "APH", "TEL", "GLW", "JBL"],
        "keywords": ["AEC", "DAC", "copper cable", "SerDes", "PCB", "backplane", "retimer"],
        "news_query": "AI data center AEC DAC copper cable retimer PCB",
        "x_query": "(AEC OR DAC OR retimer OR SerDes OR \"copper cable\") (AI OR datacenter)",
        "sources": [
            {"label": "NVIDIA Spectrum-X", "url": "https://www.nvidia.com/en-us/networking/products/ethernet/"},
        ],
        "cn_companies": [
            cn("002463", "沪电股份", "AI服务器PCB", "AI 服务器与高速 PCB 主板核心标的，适合跟美股互联链条联动。"),
            cn("002916", "深南电路", "PCB/封装基板", "PCB 与封装基板兼具，映射高速板和先进封装。"),
            cn("002938", "鹏鼎控股", "PCB", "PCB 制造规模大，可作为高速互联外溢候选。"),
            cn("002384", "东山精密", "PCB/精密制造", "PCB 与电子制造属性，适合跟踪 AI 服务器链条扩散。"),
            cn("603920", "世运电路", "PCB", "主板 PCB 标的，适合做低位补涨池观察。"),
        ],
    },
    {
        "id": "grid-transformer-ai",
        "name": "变压器 / 开关柜 / 电网接入",
        "short_name": "电网变压器",
        "trigger": "AI 数据中心排队上电，瓶颈外溢到变压器、开关柜、柔直与电网工程。",
        "us_tickers": ["ETN", "GEV", "POWL", "HUBB", "EMR", "NVT", "VST", "CEG"],
        "keywords": ["transformer", "switchgear", "grid capacity", "substation", "AI factory", "power grid"],
        "news_query": "AI data center transformer switchgear grid capacity",
        "x_query": "(transformer OR switchgear OR substation OR \"grid capacity\") \"AI data center\"",
        "sources": [
            {
                "label": "NVIDIA energy/grid partners",
                "url": "https://nvidianews.nvidia.com/news/nvidia-and-emerald-ai-join-leading-energy-companies-to-pioneer-flexible-ai-factories-as-grid-assets",
            }
        ],
        "cn_companies": [
            cn("600089", "特变电工", "变压器/输变电", "变压器和输变电设备标签清晰，是数据中心上电约束映射核心。"),
            cn("601179", "中国西电", "高压开关/输变电", "高压电气设备和工程链条，可映射数据中心并网。"),
            cn("600312", "平高电气", "高压开关", "开关设备属性直接，对变电站扩容敏感。"),
            cn("002028", "思源电气", "输配电设备", "输配电设备和海外订单弹性，适合观察 AI 电力外溢。"),
            cn("002270", "华明装备", "分接开关", "变压器关键部件，适合做更细的供应链映射。"),
        ],
    },
    {
        "id": "nuclear-smr-ai-power",
        "name": "核电 / SMR / 算力电源",
        "short_name": "核电SMR",
        "trigger": "云厂和 AI 工厂寻找长期稳定电力，核电、SMR 与电力 PPA 被重新定价。",
        "us_tickers": ["CEG", "VST", "OKLO", "CCJ", "BWXT", "NEE", "GEV", "SMR"],
        "keywords": ["nuclear", "SMR", "AI data center power", "PPA", "uranium", "grid"],
        "news_query": "AI data center nuclear power SMR PPA",
        "x_query": "(nuclear OR SMR OR uranium OR PPA) \"AI data center\"",
        "sources": [
            {
                "label": "NVIDIA flexible AI factories",
                "url": "https://nvidianews.nvidia.com/news/nvidia-and-emerald-ai-join-leading-energy-companies-to-pioneer-flexible-ai-factories-as-grid-assets",
            }
        ],
        "cn_companies": [
            cn("601985", "中国核电", "核电运营", "核电运营核心主板标的，映射稳定基荷电力重估。"),
            cn("003816", "中国广核", "核电运营", "核电运营主板标的，适合和海外核电 PPA 逻辑对照。"),
            cn("601611", "中国核建", "核电工程", "核电工程建设链条，映射 SMR/核电建设周期。"),
            cn("000777", "中核科技", "核电阀门", "核电设备细分件，适合做核电供应链二级映射。"),
            cn("002266", "浙富控股", "水电/核电设备", "发电设备与核电设备属性，适合放入电力设备观察池。"),
        ],
    },
]


CONCEPT_EXTENSIONS: dict[str, dict[str, Any]] = {
    "optical-cpo-16t": {
        "us_tickers": ["CIEN", "NOK", "AAOI", "KEYS"],
        "cn_companies": [
            cn("000063", "中兴通讯", "光网络/交换设备", "通信设备与光网络底座，适合观察光互联从器件扩散到系统侧。"),
            cn("600105", "永鼎股份", "光通信/线缆", "光通信线缆主板标的，可作为光模块之外的低位映射。"),
            cn("002446", "盛路通信", "通信天线/微波", "通信器件属性，适合放在光互联外围供应链观察。"),
            cn("002115", "三维通信", "通信网络服务", "通信网络与系统集成属性，适合观察光网络景气外溢。"),
        ],
    },
    "800vdc-ai-power": {
        "us_tickers": ["MPWR", "MCHP", "ADI", "STM"],
        "cn_companies": [
            cn("600406", "国电南瑞", "电力自动化", "电网自动化与柔直能力强，映射中压直流和园区级电力调度。"),
            cn("601877", "正泰电器", "低压电器/配电", "低压配电和电气设备底座，适合观察机房供配电升级。"),
            cn("002169", "智光电气", "电力电子/储能", "电力电子和电能管理属性，映射直流化供电和储能耦合。"),
            cn("002121", "科陆电子", "配用电/储能", "配用电和储能设备属性，适合观察数据中心电力弹性需求。"),
        ],
    },
    "liquid-cooling-cdu": {
        "us_tickers": ["TT", "LII", "IR", "GEV"],
        "cn_companies": [
            cn("603912", "佳力图", "机房环境/温控", "数据中心环境控制主板标的，适合跟踪机房液冷改造外溢。"),
            cn("002126", "银轮股份", "热管理", "换热和热管理部件属性，可映射冷板、换热器和系统级散热。"),
            cn("600619", "海立股份", "制冷压缩机", "制冷压缩机和热管理底座，适合观察高密度制冷需求扩散。"),
            cn("600160", "巨化股份", "制冷剂/氟化工", "制冷剂和氟化工材料属性，映射冷却系统耗材和工质链条。"),
        ],
    },
    "hbm-cowos-packaging": {
        "us_tickers": ["NVDA", "AMD", "ASML", "ONTO"],
        "cn_companies": [
            cn("603986", "兆易创新", "存储/控制芯片", "存储芯片和控制器属性，适合观察 HBM 景气向国产存储外溢。"),
            cn("002436", "兴森科技", "封装基板/PCB", "封装基板与样板能力，映射先进封装载板约束。"),
            cn("600667", "太极实业", "半导体工程/封测", "半导体工程与封测服务属性，映射封装产能建设。"),
            cn("600206", "有研新材", "半导体材料", "靶材和电子材料属性，适合观察先进封装材料侧扩散。"),
        ],
    },
    "copper-pcb-highspeed": {
        "us_tickers": ["TTMI", "MTSI", "VSH"],
        "cn_companies": [
            cn("600183", "生益科技", "高速覆铜板", "高频高速覆铜板核心主板标的，直接映射 AI 服务器 PCB 材料。"),
            cn("603186", "华正新材", "覆铜板/复合材料", "覆铜板和电子材料属性，适合观察高速板材料景气。"),
            cn("002636", "金安国纪", "覆铜板", "覆铜板主板标的，可作为高频高速材料扩散观察池。"),
            cn("002130", "沃尔核材", "高速线缆/材料", "线缆材料和高速互联标签清晰，适合映射铜缆/AEC 方向。"),
            cn("002475", "立讯精密", "连接器/线束", "连接器与线束能力强，可映射短距铜互联和机柜内部连接。"),
        ],
    },
    "grid-transformer-ai": {
        "us_tickers": ["PWR", "DY", "FIX", "PRIM"],
        "cn_companies": [
            cn("600406", "国电南瑞", "电网自动化", "二次设备和电网调度能力强，映射数据中心并网调度。"),
            cn("601877", "正泰电器", "低压电器/配电", "低压电器和配电箱柜属性，适合观察园区配电扩容。"),
            cn("002358", "森源电气", "开关柜/配电", "开关柜和输配电装备属性，映射数据中心接入侧扩容。"),
            cn("002276", "万马股份", "电缆/充电与电力", "电缆和电力连接属性，适合放在数据中心上电链条观察。"),
            cn("002706", "良信股份", "低压电器", "低压电器细分标的，映射机柜和园区配电升级。"),
        ],
    },
    "nuclear-smr-ai-power": {
        "us_tickers": ["LEU", "UEC", "UUUU", "NXE"],
        "cn_companies": [
            cn("601727", "上海电气", "核电/电力设备", "电力设备和核电设备属性，映射核电建设与算力电源扩张。"),
            cn("600875", "东方电气", "发电设备", "发电设备龙头，映射核电、燃机和大型电源建设周期。"),
            cn("002318", "久立特材", "核电管材", "核电用管材细分供应链，适合做核电设备二级映射。"),
            cn("002438", "江苏神通", "核电阀门", "核级阀门标签清晰，适合跟踪核电建设弹性。"),
            cn("600202", "哈空调", "核电空冷/换热", "核电换热与空冷设备属性，映射核电工程配套。"),
        ],
    },
}


CONCEPTS.extend(
    [
        {
            "id": "ai-storage-essd-hdd",
            "name": "AI存储 / 企业SSD / 近线HDD",
            "short_name": "AI存储",
            "trigger": "推理、向量库、数据湖和多模态训练把瓶颈从 GPU 扩散到企业 SSD、QLC NAND、近线 HDD 和存储服务器。",
            "us_tickers": ["MU", "WDC", "STX", "SNDK", "PSTG", "NTAP", "DELL", "HPE", "RMBS", "MRVL", "SMCI", "JBL"],
            "keywords": [
                "enterprise SSD",
                "NAND",
                "QLC",
                "nearline HDD",
                "HBM",
                "DRAM",
                "storage",
                "data lake",
                "AI storage",
                "memory",
            ],
            "news_query": "AI data center enterprise SSD NAND nearline HDD memory storage shortage",
            "x_query": "(enterprise SSD OR NAND OR nearline HDD OR QLC OR AI storage OR memory shortage) (AI OR datacenter)",
            "sources": [
                {
                    "label": "TrendForce 2Q26 memory",
                    "url": "https://www.trendforce.com/presscenter/news/20260331-12995.html",
                },
                {
                    "label": "Micron 6600 ION",
                    "url": "https://investors.micron.com/news-releases/news-release-details/industry-leading-245tb-micron-6600-ion-data-center-ssd-now",
                },
                {
                    "label": "Seagate Mozaic 4+",
                    "url": "https://investors.seagate.com/news/news-details/2026/Seagate-Delivers-Industrys-Highest-Capacity-Hard-Drives-with-Next-Generation-Mozaic-4/default.aspx",
                },
            ],
            "cn_companies": [
                cn("001309", "德明利", "SSD主控/存储模组", "主板存储模组和主控链条，直接映射 NAND/企业 SSD 紧缺与涨价。"),
                cn("000021", "深科技", "存储封测/制造", "存储封测和电子制造属性，适合观察 DRAM/NAND 供应链外溢。"),
                cn("603986", "兆易创新", "存储芯片/NOR/DRAM", "存储芯片主板核心，映射内存涨价和国产存储替代。"),
                cn("000938", "紫光股份", "服务器/存储设备", "新华三服务器、存储和网络设备能力，映射企业存储系统需求。"),
                cn("000977", "浪潮信息", "AI服务器/存储服务器", "AI 服务器核心主板标的，企业 SSD/HDD 需求最终落在整机配置。"),
                cn("600584", "长电科技", "存储封测/先进封装", "封测能力映射存储芯片和 HBM/DRAM 封装需求。"),
                cn("002156", "通富微电", "存储/高性能封测", "封测和高性能计算客户链条明确，适合跟踪存储封装景气。"),
                cn("002371", "北方华创", "存储设备/半导体设备", "半导体设备底座，映射 DRAM/NAND 产能转移和扩产。"),
                cn("002409", "雅克科技", "存储材料/电子材料", "电子材料属性，适合观察 DRAM/NAND 和先进封装材料外溢。"),
                cn("600206", "有研新材", "靶材/电子材料", "溅射靶材和电子材料属性，映射存储芯片制造材料。"),
                cn("000034", "神州数码", "企业存储/算力集成", "ICT 集成和企业存储渠道属性，适合观察存储服务器订单传导。"),
            ],
        },
        {
            "id": "ethernet-switch-asic",
            "name": "以太网交换ASIC / Spectrum-X",
            "short_name": "交换ASIC",
            "trigger": "AI 集群从 InfiniBand 向以太网方案扩散，交换 ASIC、网卡、光电协同和网络测试被重新定价。",
            "us_tickers": ["AVGO", "MRVL", "ANET", "CSCO", "HPE", "EXTR", "CIEN", "KEYS", "ALAB", "CRDO", "NVDA"],
            "keywords": ["Ethernet", "switch ASIC", "Spectrum-X", "Tomahawk", "networking", "NIC", "DPUs"],
            "news_query": "AI data center Ethernet switch ASIC Spectrum-X Tomahawk networking",
            "x_query": "(Spectrum-X OR Ethernet OR Tomahawk OR switch ASIC OR NIC) (AI OR datacenter)",
            "sources": [
                {"label": "NVIDIA Spectrum-X", "url": "https://www.nvidia.com/en-us/networking/products/ethernet/"},
                {"label": "Broadcom AI networking", "url": "https://www.broadcom.com/products/ethernet-connectivity/switching"},
            ],
            "cn_companies": [
                cn("000938", "紫光股份", "交换机/ICT设备", "交换机和企业网络设备主板标的，映射以太网 AI 网络扩散。"),
                cn("000063", "中兴通讯", "交换设备/数据通信", "数据通信和交换设备能力强，适合跟踪 AI 网络国产替代。"),
                cn("000977", "浪潮信息", "AI服务器/网络集成", "AI 服务器与整机方案，映射网络和服务器协同采购。"),
                cn("601138", "工业富联", "服务器/网络设备制造", "服务器和网络设备制造属性，映射云厂资本开支。"),
                cn("603019", "中科曙光", "服务器/HPC", "HPC 和服务器主板标的，适合观察 AI 集群建设。"),
                cn("002396", "星网锐捷", "企业网络/交换设备", "企业网络设备属性，映射以太网交换机国产链条。"),
                cn("603118", "共进股份", "通信终端/网络设备", "网络通信设备制造属性，适合作为配套供应链观察。"),
                cn("600498", "烽火通信", "光网络/通信设备", "光网络与通信设备属性，映射网络侧扩容。"),
                cn("000034", "神州数码", "ICT分销/集成", "ICT 供应链和算力集成属性，适合观察订单扩散。"),
            ],
        },
        {
            "id": "ai-server-odm-rack",
            "name": "AI服务器ODM / 整机机柜",
            "short_name": "服务器ODM",
            "trigger": "GB200/GB300 机柜级交付把价值从单卡扩散到整机、机柜、电源、网络和液冷集成。",
            "us_tickers": ["DELL", "SMCI", "HPE", "VRT", "JBL", "FLEX", "CLS", "NTAP", "PSTG"],
            "keywords": ["AI server", "ODM", "rack-scale", "GB200", "GB300", "NVL72", "AI factory"],
            "news_query": "GB200 GB300 AI server ODM rack scale NVL72",
            "x_query": "(GB200 OR GB300 OR NVL72 OR rack-scale OR ODM) (AI server OR datacenter)",
            "sources": [
                {"label": "NVIDIA GB300 NVL72", "url": "https://www.nvidia.com/en-us/data-center/gb300-nvl72/"},
                {"label": "Dell AI factory", "url": "https://www.dell.com/en-us/dt/solutions/ai/index.htm"},
            ],
            "cn_companies": [
                cn("601138", "工业富联", "AI服务器制造", "服务器和云基础设施制造核心主板标的，映射整机机柜交付。"),
                cn("000977", "浪潮信息", "AI服务器", "AI 服务器主板核心，适合观察整机订单和国产算力扩散。"),
                cn("603019", "中科曙光", "HPC/服务器", "HPC 和服务器属性，映射高性能算力集群建设。"),
                cn("000938", "紫光股份", "ICT设备/服务器", "服务器、网络和 ICT 设备组合，适合观察集成订单。"),
                cn("000034", "神州数码", "算力集成/分销", "算力集成和渠道属性，映射服务器订单传导。"),
                cn("600100", "同方股份", "服务器/系统集成", "服务器和系统集成属性，适合做低位观察池。"),
                cn("600839", "四川长虹", "服务器电源/制造链", "电子制造和电源链条属性，映射整机配套扩散。"),
                cn("002368", "太极股份", "算力集成/政企IT", "系统集成属性，适合观察政企算力项目扩散。"),
            ],
        },
        {
            "id": "connector-backplane-aec",
            "name": "高速连接器 / 背板 / AEC线束",
            "short_name": "高速连接器",
            "trigger": "AI 机柜短距互联密度提升，连接器、背板、线束、屏蔽和测试从配套件变成瓶颈件。",
            "us_tickers": ["APH", "TEL", "JBL", "FLEX", "GLW", "TTMI", "CRDO", "ALAB", "AVGO"],
            "keywords": ["connector", "backplane", "AEC", "DAC", "cable assembly", "high-speed interconnect"],
            "news_query": "AI rack high speed connector backplane AEC DAC cable assembly",
            "x_query": "(connector OR backplane OR AEC OR DAC OR cable assembly) (AI rack OR datacenter)",
            "sources": [
                {"label": "Amphenol AI interconnect", "url": "https://www.amphenol-cs.com/product-series/high-speed.html"},
            ],
            "cn_companies": [
                cn("002475", "立讯精密", "连接器/线束", "连接器和高速线束能力强，适合映射 AI 机柜内部互联。"),
                cn("002025", "航天电器", "高可靠连接器", "连接器细分龙头，适合观察高可靠互联和军工电子外溢。"),
                cn("002130", "沃尔核材", "高速线缆/材料", "高速线缆材料和连接组件标签，映射 AEC/DAC 方向。"),
                cn("002055", "得润电子", "连接器/线束", "连接器和线束主板标的，可作为低位扩散观察。"),
                cn("002897", "意华股份", "高速连接器", "高速连接器标签清晰，适合观察 AI 网络互联弹性。"),
                cn("603328", "依顿电子", "PCB/背板", "PCB 和背板属性，映射机柜级高速互联。"),
                cn("002463", "沪电股份", "高速PCB", "高速 PCB 核心主板标的，和背板、交换机板卡高度相关。"),
                cn("002916", "深南电路", "PCB/封装基板", "PCB 与封装基板兼具，映射高速互联底座。"),
            ],
        },
        {
            "id": "glass-substrate-packaging",
            "name": "玻璃基板 / 先进载板",
            "short_name": "玻璃基板",
            "trigger": "大尺寸 AI 芯片封装需要更高平整度和布线密度，玻璃基板与先进载板成为潜在下一代封装材料。",
            "us_tickers": ["GLW", "INTC", "AMAT", "KLAC", "LRCX", "TSM", "AMKR", "COHR"],
            "keywords": ["glass substrate", "advanced substrate", "panel-level packaging", "interposer", "advanced packaging"],
            "news_query": "glass substrate advanced packaging AI chip substrate",
            "x_query": "(glass substrate OR advanced substrate OR panel-level packaging OR interposer) AI chip",
            "sources": [
                {"label": "Intel glass substrate", "url": "https://www.intel.com/content/www/us/en/newsroom/news/intel-unveils-industry-leading-glass-substrates.html"},
            ],
            "cn_companies": [
                cn("603773", "沃格光电", "玻璃基板/显示材料", "玻璃精加工和基板属性，适合观察玻璃基板主题弹性。"),
                cn("600707", "彩虹股份", "玻璃基板", "显示玻璃基板主板标的，适合做材料迁移映射。"),
                cn("600552", "凯盛科技", "电子玻璃/材料", "电子玻璃和新材料属性，映射玻璃基板产业化。"),
                cn("600183", "生益科技", "覆铜板/载板材料", "高速材料底座，适合与玻璃基板主题交叉观察。"),
                cn("002916", "深南电路", "封装基板/PCB", "封装基板能力，映射先进载板和高密度互联。"),
                cn("002436", "兴森科技", "封装基板", "封装基板与样板业务，适合跟踪载板扩产。"),
                cn("000012", "南玻A", "电子玻璃", "玻璃材料底座，适合观察玻璃基板扩散行情。"),
                cn("002643", "万润股份", "电子材料", "电子材料属性，适合放在先进封装材料观察池。"),
            ],
        },
        {
            "id": "hvlp-ccl-copperfoil",
            "name": "HVLP铜箔 / 高频覆铜板",
            "short_name": "高频覆铜板",
            "trigger": "800G/1.6T 交换机和 AI 服务器推动低损耗材料、HVLP 铜箔、高频高速覆铜板需求上行。",
            "us_tickers": ["TTMI", "APH", "TEL", "GLW", "AVGO", "CRDO", "ALAB", "MTSI"],
            "keywords": ["HVLP", "copper foil", "CCL", "low loss", "high frequency", "PCB material"],
            "news_query": "AI server high frequency CCL HVLP copper foil low loss PCB",
            "x_query": "(HVLP OR CCL OR copper foil OR low loss PCB) (AI server OR switch)",
            "sources": [
                {"label": "TTM AI data center PCB", "url": "https://www.ttm.com/markets/data-center"},
            ],
            "cn_companies": [
                cn("600183", "生益科技", "高速覆铜板", "高频高速覆铜板核心标的，直接映射高速板材料。"),
                cn("603186", "华正新材", "覆铜板/复合材料", "覆铜板和电子材料属性，适合观察高速材料扩散。"),
                cn("002636", "金安国纪", "覆铜板", "覆铜板主板标的，可作为低位补涨观察。"),
                cn("000823", "超声电子", "PCB/覆铜板", "PCB 与覆铜板属性，映射高速板需求扩张。"),
                cn("002463", "沪电股份", "高速PCB", "AI 服务器 PCB 核心，和高速材料景气强相关。"),
                cn("002916", "深南电路", "PCB/封装基板", "高速 PCB 和封装基板双线映射。"),
                cn("002938", "鹏鼎控股", "PCB", "PCB 制造规模大，可作为高速材料外溢候选。"),
                cn("002384", "东山精密", "PCB/精密制造", "PCB 与电子制造属性，适合跟踪 AI 服务器链条。"),
            ],
        },
        {
            "id": "robot-actuator-sensor",
            "name": "机器人执行器 / 传感器 / 丝杠",
            "short_name": "机器人执行器",
            "trigger": "人形机器人从展示进入供应链验证，执行器、减速器、丝杠、传感器和热管理先被定价。",
            "us_tickers": ["TSLA", "NVDA", "ISRG", "SYM", "TER", "ROK", "HON", "CGNX"],
            "keywords": ["humanoid robot", "actuator", "linear actuator", "reducer", "sensor", "robotics"],
            "news_query": "humanoid robot actuator reducer sensor supply chain",
            "x_query": "(humanoid robot OR actuator OR reducer OR linear actuator OR robot sensor)",
            "sources": [
                {"label": "Tesla Optimus", "url": "https://www.tesla.com/AI"},
            ],
            "cn_companies": [
                cn("002050", "三花智控", "机器人热管理/执行部件", "热管理和执行部件能力强，适合映射人形机器人供应链。"),
                cn("601689", "拓普集团", "执行器/汽车零部件", "汽车零部件和执行器属性，适合观察机器人部件迁移。"),
                cn("603728", "鸣志电器", "电机/控制", "电机和运动控制属性，映射机器人关节。"),
                cn("002747", "埃斯顿", "工业机器人", "机器人本体和伺服控制属性，适合观察国产机器人链。"),
                cn("000837", "秦川机床", "减速器/机床", "减速器和精密制造属性，映射机器人关节。"),
                cn("002896", "中大力德", "减速器/电机", "减速器和电机标签清晰，适合做执行器细分映射。"),
                cn("002472", "双环传动", "齿轮/减速器", "齿轮传动能力强，映射机器人减速器需求。"),
                cn("603667", "五洲新春", "轴承/丝杠", "轴承和丝杠相关属性，适合观察线性执行器。"),
                cn("603662", "柯力传感", "传感器", "传感器主板标的，映射机器人力控和感知环节。"),
            ],
        },
        {
            "id": "defense-drone-cuas",
            "name": "防务无人机 / 反无人机",
            "short_name": "防务无人机",
            "trigger": "美股防务科技、无人系统和反无人机热度上升，A股可映射航天电子、红外、导航和航空装备链。",
            "us_tickers": ["AVAV", "KTOS", "PLTR", "RTX", "LMT", "NOC", "HWM", "TDG", "HON"],
            "keywords": ["drone", "counter-drone", "UAS", "defense tech", "loitering munition", "autonomy"],
            "news_query": "defense drone counter UAS autonomy loitering munition",
            "x_query": "(drone OR counter-drone OR UAS OR loitering munition OR defense tech)",
            "sources": [
                {"label": "AeroVironment UAS", "url": "https://www.avinc.com/uas"},
            ],
            "cn_companies": [
                cn("002389", "航天彩虹", "无人机", "无人机主板标的，直接映射美股无人系统热度。"),
                cn("600879", "航天电子", "航天电子/无人系统", "航天电子和无人系统配套属性，适合观察防务科技扩散。"),
                cn("000901", "航天科技", "航天电子/车联网", "航天系电子设备属性，映射无人装备配套。"),
                cn("600435", "北方导航", "导航控制", "导航控制和军工电子属性，适合观察无人平台配套。"),
                cn("600967", "内蒙一机", "地面装备", "地面装备主板标的，映射防务装备景气。"),
                cn("600893", "航发动力", "航空发动机", "航空发动机核心标的，映射航空装备链。"),
                cn("000768", "中航西飞", "军机制造", "军机制造核心主板，映射航空装备资本开支。"),
                cn("600760", "中航沈飞", "军机制造", "军机制造核心标的，适合观察防务主线扩散。"),
                cn("002414", "高德红外", "红外/导引", "红外成像和导引属性，映射无人机感知和反制。"),
            ],
        },
        {
            "id": "gas-turbine-backup-power",
            "name": "燃气轮机 / 备用电源 / 微电网",
            "short_name": "燃机备用电",
            "trigger": "AI 数据中心电力缺口推高燃机、备用电源、微电网和电力工程设备关注度。",
            "us_tickers": ["GEV", "ETN", "CAT", "CMI", "VST", "CEG", "POWL", "HUBB"],
            "keywords": ["gas turbine", "backup power", "microgrid", "data center power", "generator", "grid asset"],
            "news_query": "AI data center gas turbine backup power microgrid generator",
            "x_query": "(gas turbine OR backup power OR microgrid OR generator) \"data center\"",
            "sources": [
                {
                    "label": "NVIDIA flexible AI factories",
                    "url": "https://nvidianews.nvidia.com/news/nvidia-and-emerald-ai-join-leading-energy-companies-to-pioneer-flexible-ai-factories-as-grid-assets",
                }
            ],
            "cn_companies": [
                cn("600875", "东方电气", "发电设备/燃机", "发电设备龙头，映射燃机和大型电源建设。"),
                cn("601727", "上海电气", "燃机/电力设备", "燃机和电力设备属性，适合观察数据中心备用电源扩散。"),
                cn("601369", "陕鼓动力", "压缩机/能源装备", "能源装备和压缩机属性，映射燃机与工业能源系统。"),
                cn("000338", "潍柴动力", "发动机/发电机组", "发动机和动力系统能力，适合观察备用电源链条。"),
                cn("600482", "中国动力", "动力装备", "动力装备平台，映射大型动力和应急电源需求。"),
                cn("600841", "动力新科", "发动机/动力系统", "动力系统属性，适合做备用电源低位观察。"),
                cn("600590", "泰豪科技", "军工电源/应急电源", "应急电源和电力装备属性，映射备用电源场景。"),
                cn("600406", "国电南瑞", "微电网/电力自动化", "电力自动化能力强，映射微电网和数据中心调度。"),
            ],
        },
    ]
)


for concept in CONCEPTS:
    extension = CONCEPT_EXTENSIONS.get(concept["id"])
    if extension:
        concept["us_tickers"] = [*concept.get("us_tickers", []), *extension.get("us_tickers", [])]
        concept["cn_companies"] = [*concept.get("cn_companies", []), *extension.get("cn_companies", [])]
    seen_tickers: set[str] = set()
    unique_tickers: list[str] = []
    for ticker in concept.get("us_tickers", []):
        ticker_text = str(ticker).strip().upper()
        if ticker_text and ticker_text not in seen_tickers:
            seen_tickers.add(ticker_text)
            unique_tickers.append(ticker_text)
    concept["us_tickers"] = unique_tickers

    seen_codes: set[str] = set()
    unique_companies: list[CnCompany] = []
    for company in concept.get("cn_companies", []):
        if company.code not in seen_codes:
            seen_codes.add(company.code)
            unique_companies.append(company)
    concept["cn_companies"] = unique_companies


DYNAMIC_DISCOVERY_RULES: list[dict[str, Any]] = [
    {
        "id": "dyn-copper-grid-commodity",
        "name": "铜矿 / 电缆 / 算力电气化",
        "short_name": "铜电气化",
        "trigger": "AI数据中心、输配电和电气化建设推高铜、线缆、母线和导体需求，先看美股铜矿与电气化链条异动。",
        "us_tickers": ["FCX", "SCCO", "TECK", "BHP", "RIO", "ETN", "PWR", "HUBB"],
        "keywords": ["copper", "electrification", "power cable", "grid investment", "data center power", "busbar"],
        "news_query": "copper electrification data center power cable grid investment",
        "x_query": "(copper OR electrification OR power cable OR busbar) (datacenter OR AI OR grid)",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("601899", "紫金矿业", "铜矿/金属资源", "铜矿资源龙头，映射AI电气化与全球铜价重估。"),
            cn("600362", "江西铜业", "铜冶炼/铜矿", "铜产业链主板核心，适合观察铜价和电气化需求外溢。"),
            cn("000878", "云南铜业", "铜冶炼", "铜冶炼主板标的，映射铜价与电力投资链条。"),
            cn("000630", "铜陵有色", "铜冶炼/铜箔", "铜冶炼与铜加工属性，适合跟踪导体和材料需求。"),
            cn("002203", "海亮股份", "铜管/铜加工", "铜加工能力强，可映射数据中心制冷和电气连接用铜需求。"),
            cn("600522", "中天科技", "电缆/电力通信", "电力线缆和通信线缆交叉，映射电网与数据中心接入建设。"),
        ],
        "driver": "上游资源 / 电气化",
    },
    {
        "id": "dyn-natural-gas-lng-power",
        "name": "天然气 / LNG / 算力电源",
        "short_name": "天然气电源",
        "trigger": "AI数据中心用电瓶颈推动燃机、LNG、管网和长期气源合同重新定价。",
        "us_tickers": ["LNG", "WMB", "KMI", "ET", "TRGP", "EQT", "VST", "GEV"],
        "keywords": ["natural gas", "LNG", "gas power", "pipeline", "data center power", "power demand"],
        "news_query": "natural gas LNG data center power demand AI",
        "x_query": "(natural gas OR LNG OR pipeline OR gas power) (data center OR AI power)",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("601857", "中国石油", "天然气/油气", "天然气资源与管网底座，映射海外算力电源对气源的重估。"),
            cn("600028", "中国石化", "天然气/炼化", "天然气和能源综合属性，适合观察能源价格外溢。"),
            cn("600938", "中国海油", "油气资源", "上游油气资源主板标的，映射全球气价与能源安全。"),
            cn("600256", "广汇能源", "LNG/煤气化", "LNG和能源贸易属性，适合观察气源紧张叙事。"),
            cn("600803", "新奥股份", "燃气运营/LNG", "天然气运营与LNG链条，映射算力电力需求外溢。"),
            cn("600875", "东方电气", "发电设备/燃机", "燃机和发电设备龙头，映射气电建设周期。"),
        ],
        "driver": "能源 / 算力电源",
    },
    {
        "id": "dyn-cybersecurity-ai-infra",
        "name": "AI网络安全 / 云安全",
        "short_name": "AI安全",
        "trigger": "AI应用、云基础设施和企业数据暴露面扩大，美股网络安全公司异动时关注A股安全主板映射。",
        "us_tickers": ["CRWD", "PANW", "ZS", "NET", "FTNT", "OKTA", "S", "CYBR"],
        "keywords": ["cybersecurity", "cloud security", "AI security", "zero trust", "data security", "identity security"],
        "news_query": "AI cloud cybersecurity zero trust data security earnings",
        "x_query": "(cybersecurity OR cloud security OR zero trust OR AI security)",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("002268", "电科网安", "网络安全/数据安全", "网络安全主板核心，映射云安全和数据安全需求。"),
            cn("002439", "启明星辰", "安全运营/网络安全", "安全运营和政企网络安全标的，映射AI安全预算。"),
            cn("600845", "宝信软件", "工业软件/数据中心", "工业软件和IDC能力，适合观察数据安全与工业安全外溢。"),
            cn("000066", "中国长城", "安全计算/信创", "安全计算和信创硬件属性，映射国产安全底座。"),
            cn("000938", "紫光股份", "云网设备/安全", "云网设备和企业IT入口，适合观察网络安全扩散。"),
        ],
        "driver": "软件安全 / 云基础设施",
    },
    {
        "id": "dyn-ai-pc-edge-device",
        "name": "AI PC / 端侧AI / 设备链",
        "short_name": "端侧AI",
        "trigger": "AI模型从云端向PC、手机和可穿戴迁移，先观察美股CPU、ARM、终端和代工链条异动。",
        "us_tickers": ["AAPL", "QCOM", "ARM", "AMD", "INTC", "MSFT", "HPQ", "DELL"],
        "keywords": ["AI PC", "on-device AI", "edge AI", "NPU", "Copilot PC", "ARM PC"],
        "news_query": "AI PC on-device AI NPU Copilot PC ARM",
        "x_query": "(AI PC OR on-device AI OR NPU OR Copilot PC OR edge AI)",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("000725", "京东方A", "显示面板/终端", "终端显示核心主板标的，映射AI PC和设备更新。"),
            cn("002475", "立讯精密", "消费电子/连接器", "终端制造和连接器能力，映射端侧AI设备链。"),
            cn("002241", "歌尔股份", "声学/智能硬件", "智能硬件和声学链条，适合观察端侧AI设备创新。"),
            cn("002938", "鹏鼎控股", "消费电子PCB", "终端PCB龙头，映射AI PC和设备升级。"),
            cn("000977", "浪潮信息", "AI服务器/边缘算力", "边缘算力与服务器属性，映射端云协同。"),
        ],
        "driver": "端侧设备 / 消费电子",
    },
    {
        "id": "dyn-rare-earth-magnet-robot",
        "name": "稀土磁材 / 机器人电机",
        "short_name": "稀土磁材",
        "trigger": "机器人、无人机、电机和高效电驱需求升温时，上游稀土和钕铁硼磁材可能先被重估。",
        "us_tickers": ["MP", "TSLA", "ISRG", "SYM", "ROK", "HON", "GE", "GM"],
        "keywords": ["rare earth", "magnet", "NdFeB", "motor", "humanoid robot", "actuator"],
        "news_query": "rare earth magnet humanoid robot motor actuator",
        "x_query": "(rare earth OR magnet OR NdFeB OR motor) (robot OR EV OR drone)",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("600111", "北方稀土", "稀土资源/磁材", "稀土资源核心主板，映射磁材和电机需求。"),
            cn("000831", "中国稀土", "稀土资源", "稀土资源主板标的，适合观察资源端重估。"),
            cn("600392", "盛和资源", "稀土资源", "稀土资源与海外矿属性，映射供需紧张。"),
            cn("000970", "中科三环", "钕铁硼磁材", "磁材细分主板标的，映射机器人和高效电机需求。"),
            cn("002056", "横店东磁", "磁材/新能源", "磁材和器件属性，适合观察磁材需求扩散。"),
        ],
        "driver": "材料 / 机器人电机",
    },
    {
        "id": "dyn-cxl-memory-pooling",
        "name": "CXL / 内存池化 / 互连芯片",
        "short_name": "CXL内存池",
        "trigger": "AI推理和内存墙推动CXL、内存池化、RCD/PMIC和高速互连芯片关注度提升。",
        "us_tickers": ["RMBS", "MRVL", "MU", "AVGO", "AMD", "INTC", "SNPS", "CDNS"],
        "keywords": ["CXL", "memory pooling", "memory wall", "RCD", "PMIC", "interconnect chip"],
        "news_query": "CXL memory pooling AI memory wall interconnect chip",
        "x_query": "(CXL OR memory pooling OR memory wall OR RCD OR PMIC) AI",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("603986", "兆易创新", "存储芯片", "存储芯片主板核心，映射内存墙和存储控制需求。"),
            cn("000021", "深科技", "存储封测/制造", "存储封测和制造属性，适合观察内存链条外溢。"),
            cn("001309", "德明利", "存储模组/主控", "存储模组和主控链条，映射企业SSD和内存扩展。"),
            cn("002156", "通富微电", "高性能封测", "高性能芯片封测属性，映射互连芯片封装需求。"),
            cn("002409", "雅克科技", "电子材料/存储材料", "电子材料属性，适合观察存储和互连芯片材料需求。"),
        ],
        "driver": "内存墙 / 互连芯片",
    },
    {
        "id": "dyn-quantum-computing",
        "name": "量子计算 / 低温控制",
        "short_name": "量子计算",
        "trigger": "美股量子计算与低温控制公司出现连续异动时，观察A股通信、低温、控制和科研设备映射。",
        "us_tickers": ["IONQ", "RGTI", "QBTS", "QUBT", "ARQQ", "HON", "IBM", "GOOG"],
        "keywords": ["quantum computing", "qubit", "cryogenic", "quantum control", "post quantum"],
        "news_query": "quantum computing qubit cryogenic quantum control",
        "x_query": "(quantum computing OR qubit OR cryogenic OR quantum control)",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("000988", "华工科技", "激光/传感/量子通信外溢", "激光和传感链条，适合观察量子科研设备外溢。"),
            cn("600100", "同方股份", "科研设备/信息系统", "科研和信息系统属性，适合观察量子产业化配套。"),
            cn("000063", "中兴通讯", "通信设备", "通信设备底座，映射量子通信和网络安全外溢。"),
            cn("600522", "中天科技", "通信线缆/光纤", "光纤光缆与通信基础设施，映射量子通信网络建设。"),
            cn("000977", "浪潮信息", "高性能计算", "HPC和算力设备属性，适合观察量子/经典混合计算需求。"),
        ],
        "driver": "前沿计算 / 科研设备",
    },
    {
        "id": "dyn-data-center-construction",
        "name": "数据中心工程 / 电力施工",
        "short_name": "算力工程",
        "trigger": "云厂资本开支扩张会先反映在美股电力工程、机电建设和数据中心REIT链条。",
        "us_tickers": ["PWR", "EME", "FIX", "DY", "PRIM", "DLR", "EQIX", "IRM"],
        "keywords": ["data center construction", "power infrastructure", "electrical contractor", "AI capex", "grid connection"],
        "news_query": "AI data center construction electrical contractor power infrastructure",
        "x_query": "(data center construction OR electrical contractor OR AI capex OR grid connection)",
        "sources": [{"label": "Dynamic discovery", "url": "https://news.google.com/"}],
        "cn_companies": [
            cn("601669", "中国电建", "电力工程/基础设施", "电力工程和基础设施龙头，映射算力园区建设。"),
            cn("601390", "中国中铁", "基础设施工程", "基础设施工程属性，适合观察大型园区建设。"),
            cn("601186", "中国铁建", "基础设施工程", "工程建设主板标的，映射数据中心土建扩张。"),
            cn("002060", "粤水电", "工程建设/新能源", "工程建设和新能源属性，映射园区电力施工。"),
            cn("600406", "国电南瑞", "电力自动化/调度", "电力自动化能力强，映射并网和园区调度。"),
        ],
        "driver": "工程建设 / 电力施工",
    },
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def shanghai_now() -> datetime:
    return datetime.now(SH_TZ)


def request_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "Mozilla/5.0 market-lag-dashboard/0.1"})
    return session


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return None
        return parsed
    except Exception:
        return None


def positive_float(value: Any) -> float | None:
    parsed = safe_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def pct_change(latest: float | None, previous: float | None) -> float | None:
    if latest is None or previous in (None, 0):
        return None
    return (latest / previous - 1) * 100


def latest_candle_close(candles: list[dict[str, Any]] | None, offset: int = 0) -> float | None:
    if not candles:
        return None
    found = 0
    for row in reversed(candles):
        close = positive_float(row.get("close"))
        if close is None:
            continue
        if found == offset:
            return close
        found += 1
    return None


def compact_amount(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.2f}万"
    return f"{value:.0f}"


def clamp(value: float | int | None, lower: float = 0, upper: float = 100) -> float:
    parsed = safe_float(value)
    if parsed is None:
        parsed = 0
    return max(lower, min(upper, parsed))


def weighted_average(values: list[tuple[float | None, float | None]]) -> float | None:
    total = 0.0
    weight_total = 0.0
    for value, weight in values:
        parsed = safe_float(value)
        parsed_weight = safe_float(weight)
        if parsed is None or parsed_weight is None or parsed_weight <= 0:
            continue
        total += parsed * parsed_weight
        weight_total += parsed_weight
    return total / weight_total if weight_total else None


def winsorized_mean(values: list[float | None], lower: float = 0.1, upper: float = 0.9) -> float | None:
    parsed = sorted(value for value in (safe_float(item) for item in values) if value is not None)
    if not parsed:
        return None
    if len(parsed) < 5:
        return sum(parsed) / len(parsed)
    low_idx = int((len(parsed) - 1) * lower)
    high_idx = int((len(parsed) - 1) * upper)
    clipped = parsed[low_idx : high_idx + 1] or parsed
    return sum(clipped) / len(clipped)


def role_for_us_ticker(symbol: str | None) -> str:
    text = str(symbol or "").upper()
    if text in LEADER_TICKERS:
        return "leader"
    if text in CORE_SUPPLIER_TICKERS:
        return "core_supplier"
    if text in SPECULATIVE_TICKERS:
        return "speculative"
    return "peripheral"


def us_signal_weight(quote: dict[str, Any]) -> float:
    if not quote.get("ok"):
        return 0.0
    role = quote.get("signal_role") or role_for_us_ticker(quote.get("symbol"))
    base = {"leader": 1.25, "core_supplier": 1.0, "peripheral": 0.82, "speculative": 0.62}.get(role, 0.8)
    relative_volume = safe_float(quote.get("relative_volume"))
    if relative_volume is not None:
        base += clamp(relative_volume - 1, 0, 2.2) * 0.08
    if safe_float(quote.get("change_1d")) is not None and (quote.get("change_1d") or 0) > 0:
        base += 0.05
    return clamp(base, 0.25, 1.55)


def enhance_us_quote(quote: dict[str, Any]) -> dict[str, Any]:
    symbol = str(quote.get("symbol") or "").upper()
    enhanced = dict(quote)
    enhanced["signal_role"] = role_for_us_ticker(symbol)
    enhanced["signal_weight"] = us_signal_weight(enhanced)
    return enhanced


def candle_amount(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    close = positive_float(row.get("close"))
    volume = safe_float(row.get("volume"))
    if close is None or volume is None or volume <= 0:
        return None
    return close * volume


def avg_recent_amount(rows: list[dict[str, Any]], window: int = 20) -> float | None:
    values = [candle_amount(row) for row in rows[-window:]]
    usable = [value for value in values if value is not None and value > 0]
    return sum(usable) / len(usable) if usable else None


def relative_amount_score(relative_amount: float | None, change: float | None) -> tuple[float, float]:
    relative = safe_float(relative_amount)
    change_value = safe_float(change) or 0
    if relative is None:
        return 5.0, 4.0
    confirm = 6 + min(relative, 3.0) * 4.6 + max(change_value, 0) * 0.25
    penalty = 0.0
    if relative > 3.2:
        penalty += min(18, (relative - 3.2) * 4.2)
    if change_value >= 7:
        penalty += min(16, (change_value - 6) * 2.2)
    if change_value >= 9.3:
        penalty += 8
    if relative < 0.55 and change_value > 2:
        penalty += 5
    return clamp(confirm, 0, 22), clamp(penalty, 0, 32)


def mapping_confidence_for_company(
    company: CnCompany,
    amount: float | None,
    change: float | None,
    candles: list[dict[str, Any]],
    relative_amount: float | None,
) -> tuple[float, list[str]]:
    text = f"{company.role} {company.reason}".lower()
    confidence = 48.0
    flags: list[str] = []
    direct_terms = [
        "ai",
        "服务器",
        "光模块",
        "光通信",
        "电源",
        "液冷",
        "封装",
        "存储",
        "pcb",
        "覆铜板",
        "变压器",
        "核电",
        "连接器",
        "无人机",
        "机器人",
    ]
    if any(term in text for term in direct_terms):
        confidence += 14
    if any(term in text for term in ["外溢", "低位", "候选", "观察池"]):
        confidence -= 6
        flags.append("映射偏二级")
    parsed_amount = safe_float(amount)
    if parsed_amount is None or parsed_amount <= 0:
        confidence -= 8
        flags.append("成交额缺失")
    elif parsed_amount >= 800_000_000:
        confidence += 10
    elif parsed_amount >= 200_000_000:
        confidence += 7
    elif parsed_amount >= 50_000_000:
        confidence += 4
    if safe_float(relative_amount) is not None and 0.8 <= float(relative_amount or 0) <= 2.8:
        confidence += 5
    if safe_float(change) is not None and float(change or 0) >= 9:
        flags.append("接近涨停")
    if len(candles) >= 120:
        confidence += 8
    elif len(candles) < 35:
        confidence -= 10
        flags.append("K线不足")
    return clamp(confidence, 12, 92), flags


def atr_pct(rows: list[dict[str, Any]], idx: int, window: int = 14) -> float | None:
    if idx <= 0 or not rows:
        return None
    start = max(1, idx - window + 1)
    ranges: list[float] = []
    for current_idx in range(start, idx + 1):
        row = rows[current_idx]
        prev = rows[current_idx - 1]
        high = positive_float(row.get("high"))
        low = positive_float(row.get("low"))
        prev_close = positive_float(prev.get("close"))
        close = positive_float(row.get("close"))
        if high is None or low is None or prev_close is None or close is None or close <= 0:
            continue
        true_range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        ranges.append(true_range / close * 100)
    return sum(ranges) / len(ranges) if ranges else None


def event_window_returns(rows: list[dict[str, Any]], idx: int, buy_price: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if buy_price is None or buy_price <= 0:
        for horizon in EVENT_HORIZONS:
            out[f"return_{horizon}d"] = None
        out.update({"mfe_5d": None, "mae_5d": None, "mfe_10d": None, "mae_10d": None, "dynamic_threshold": None})
        return out
    for horizon in EVENT_HORIZONS:
        exit_idx = idx + horizon
        exit_price = positive_float(rows[exit_idx].get("close")) if exit_idx < len(rows) else None
        out[f"return_{horizon}d"] = pct_change(exit_price, buy_price) if exit_price is not None else None
    for horizon in (5, 10):
        window_rows = rows[idx + 1 : idx + horizon + 1]
        if len(window_rows) >= horizon:
            highs = [positive_float(row.get("high")) for row in window_rows]
            lows = [positive_float(row.get("low")) for row in window_rows]
            usable_highs = [value for value in highs if value is not None]
            usable_lows = [value for value in lows if value is not None]
            out[f"mfe_{horizon}d"] = pct_change(max(usable_highs), buy_price) if usable_highs else None
            out[f"mae_{horizon}d"] = pct_change(min(usable_lows), buy_price) if usable_lows else None
        else:
            out[f"mfe_{horizon}d"] = None
            out[f"mae_{horizon}d"] = None
    atr = atr_pct(rows, idx)
    out["dynamic_threshold"] = clamp(max(1.0, (atr or 0) * 0.55), 1.0, 4.5)
    return out


def market_proxy_context(proxy_quotes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    us_1d = weighted_average(
        [(proxy_quotes.get(symbol, {}).get("change_1d"), weight) for symbol, weight in US_MARKET_PROXY_WEIGHTS.items()]
    )
    us_5d = weighted_average(
        [(proxy_quotes.get(symbol, {}).get("change_5d"), weight) for symbol, weight in US_MARKET_PROXY_WEIGHTS.items()]
    )
    cn_1d = weighted_average(
        [(proxy_quotes.get(symbol, {}).get("change_1d"), weight) for symbol, weight in CN_MARKET_PROXY_WEIGHTS.items()]
    )
    cn_5d = weighted_average(
        [(proxy_quotes.get(symbol, {}).get("change_5d"), weight) for symbol, weight in CN_MARKET_PROXY_WEIGHTS.items()]
    )
    return {
        "us_proxy_1d": us_1d,
        "us_proxy_5d": us_5d,
        "cn_proxy_1d": cn_1d,
        "cn_proxy_5d": cn_5d,
        "proxy_source": "Yahoo: QQQ/SOXX/IWM + CSI300/CSI1000/创业板 proxy",
    }


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_chart_file(kind: str, symbol: str, rows: list[dict[str, Any]], source: str, period: str = "5年日K") -> str | None:
    if not rows:
        return None
    safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)
    path = CHART_DATA_DIR / kind / f"{safe_symbol}.json"
    write_json(
        path,
        {
            "kind": kind,
            "symbol": symbol,
            "source": source,
            "period": period,
            "start": rows[0].get("date"),
            "end": rows[-1].get("date"),
            "rows": rows,
        },
    )
    return f"./data/charts/{kind}/{safe_symbol}.json"


def yahoo_chart_entries(symbol: str, session: requests.Session, range_value: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = session.get(
        url,
        params={"range": range_value, "interval": "1d", "includePrePost": "false"},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError((data.get("chart") or {}).get("error") or "empty chart result")
    item = result[0]
    meta = item.get("meta") or {}
    timezone_name = str(meta.get("exchangeTimezoneName") or "")
    try:
        exchange_tz = ZoneInfo(timezone_name) if timezone_name else NY_TZ
    except Exception:
        exchange_tz = SH_TZ if symbol.endswith((".SS", ".SZ")) else NY_TZ
    quote = ((item.get("indicators") or {}).get("quote") or [{}])[0]
    timestamps = item.get("timestamp") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    entries: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        close = safe_float(closes[idx] if idx < len(closes) else None)
        if close is None:
            continue
        open_ = safe_float(opens[idx] if idx < len(opens) else None) or close
        high = safe_float(highs[idx] if idx < len(highs) else None) or max(open_, close)
        low = safe_float(lows[idx] if idx < len(lows) else None) or min(open_, close)
        volume = safe_float(volumes[idx] if idx < len(volumes) else None) or 0
        dt = datetime.fromtimestamp(int(ts), UTC).astimezone(exchange_tz)
        entries.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    if not entries:
        raise RuntimeError("no close data")
    regular_period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    regular_end = safe_float(regular_period.get("end"))
    if regular_end is not None and datetime.now(UTC).timestamp() < regular_end:
        session_date = datetime.fromtimestamp(int(regular_end), UTC).astimezone(exchange_tz).strftime("%Y-%m-%d")
        if entries and entries[-1].get("date") == session_date:
            entries.pop()
    if not entries:
        raise RuntimeError("no completed close data")
    return entries, meta


def merge_yahoo_long_entries(daily_entries: list[dict[str, Any]], max_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not daily_entries:
        return max_entries
    if not max_entries:
        return daily_entries
    cutoff = daily_entries[0]["date"]
    merged = [row for row in max_entries if row.get("date") and row["date"] < cutoff] + daily_entries
    deduped: dict[str, dict[str, Any]] = {}
    for row in merged:
        deduped[row["date"]] = row
    return [deduped[key] for key in sorted(deduped)]


def yahoo_chart(symbol: str, session: requests.Session) -> dict[str, Any]:
    try:
        entries, meta = yahoo_chart_entries(symbol, session, "5y")
        latest = entries[-1]
        previous = entries[-2] if len(entries) >= 2 else None
        ref_5d = entries[-6] if len(entries) >= 6 else entries[0]
        avg_volume = sum(row["volume"] for row in entries[-20:]) / max(len(entries[-20:]), 1)
        chart_ref = write_chart_file("us", symbol, entries, "Yahoo 5y daily", "5年日K")
        return {
            "symbol": symbol,
            "source": "Yahoo chart",
            "ok": True,
            "price": latest["close"],
            "currency": meta.get("currency") or "USD",
            "date": latest["date"],
            "change_1d": pct_change(latest["close"], previous["close"] if previous else None),
            "change_5d": pct_change(latest["close"], ref_5d["close"]),
            "volume": latest["volume"],
            "avg_volume": avg_volume,
            "relative_volume": latest["volume"] / avg_volume if avg_volume else None,
            "spark": entries[-22:],
            "candles": entries[-INLINE_CANDLE_DAYS:],
            "chart_ref": chart_ref,
            "candle_count": len(entries),
            "candle_start": entries[0]["date"],
            "chart_period": "1个月日线",
            "candle_period": "5年日K",
        }
    except Exception as exc:
        return {"symbol": symbol, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def quote_from_index_rows(symbol: str, rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    if len(rows) < 30:
        return {"symbol": symbol, "ok": False, "error": f"{source}: insufficient index history"}
    entries = rows[-LONG_CHART_DAYS:]
    latest = entries[-1]
    previous = entries[-2]
    ref_5d = entries[-6] if len(entries) >= 6 else entries[0]
    avg_volume = sum(float(row.get("volume") or 0) for row in entries[-20:]) / max(len(entries[-20:]), 1)
    chart_ref = write_chart_file("us", symbol, entries, source, "5年日K")
    return {
        "symbol": symbol,
        "source": source,
        "ok": True,
        "price": latest["close"],
        "currency": "CNY",
        "date": latest["date"],
        "change_1d": pct_change(latest["close"], previous["close"]),
        "change_5d": pct_change(latest["close"], ref_5d["close"]),
        "volume": latest["volume"],
        "avg_volume": avg_volume,
        "relative_volume": latest["volume"] / avg_volume if avg_volume else None,
        "spark": entries[-22:],
        "candles": entries[-INLINE_CANDLE_DAYS:],
        "chart_ref": chart_ref,
        "candle_count": len(entries),
        "candle_start": entries[0]["date"],
        "chart_period": "1个月日线",
        "candle_period": "5年日K",
    }


def fetch_cn_index_proxy_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    try:
        import baostock as bs  # type: ignore
    except Exception:
        return {}
    baostock_codes = {
        "000300.SS": "sh.000300",
        "000852.SS": "sh.000852",
        "399006.SZ": "sz.399006",
    }
    result: dict[str, dict[str, Any]] = {}
    login = bs.login()
    if getattr(login, "error_code", "1") != "0":
        return result
    try:
        end_dt = shanghai_now()
        end_date = end_dt.strftime("%Y-%m-%d")
        start_date = (end_dt - timedelta(days=LONG_CHART_DAYS * 2)).strftime("%Y-%m-%d")
        for symbol in symbols:
            bs_code = baostock_codes.get(symbol)
            if not bs_code:
                continue
            try:
                query = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3",
                )
            except Exception:
                continue
            if getattr(query, "error_code", "1") != "0":
                continue
            rows: list[dict[str, Any]] = []
            while query.next():
                raw = query.get_row_data()
                if len(raw) < 6:
                    continue
                close = safe_float(raw[4])
                if close is None:
                    continue
                open_ = safe_float(raw[1]) or close
                high = safe_float(raw[2]) or max(open_, close)
                low = safe_float(raw[3]) or min(open_, close)
                rows.append(
                    {
                        "date": raw[0],
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": safe_float(raw[5]) or 0,
                    }
                )
            quote = quote_from_index_rows(symbol, rows, "Baostock index daily")
            if quote.get("ok"):
                result[symbol] = quote
    finally:
        try:
            bs.logout()
        except Exception:
            pass
    return result


def fetch_news(concept: dict[str, Any], session: requests.Session) -> list[dict[str, Any]]:
    query = concept["news_query"]
    url = "https://news.google.com/rss/search"
    params = {"q": f"{query} when:45d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        response = session.get(url, params=params, timeout=12)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception:
        return []

    keywords = [str(k).lower() for k in concept.get("keywords", [])]
    tickers = [str(t).lower() for t in concept.get("us_tickers", [])]
    out: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        title_l = title.lower()
        if keywords and not any(k.lower() in title_l for k in keywords + tickers):
            # Keep the first few high-query results even when Google rewrites titles.
            if len(out) >= 2:
                continue
        published_iso = None
        if published:
            try:
                published_iso = parsedate_to_datetime(published).astimezone(SH_TZ).isoformat()
            except Exception:
                published_iso = published
        out.append({"title": title, "url": link, "published_at": published_iso})
        if len(out) >= 5:
            break
    return out


def clean_text(value: str | None, max_len: int = 260) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def fetch_public_research_items(session: requests.Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for feed in PUBLIC_RESEARCH_FEEDS:
        try:
            response = session.get(feed["url"], timeout=12)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception:
            continue
        for item in root.findall(".//item"):
            title = clean_text(item.findtext("title"), 180)
            link = (item.findtext("link") or "").strip()
            description = clean_text(item.findtext("description"), 260)
            published = (item.findtext("pubDate") or "").strip()
            published_iso = None
            if published:
                try:
                    published_iso = parsedate_to_datetime(published).astimezone(SH_TZ).isoformat()
                except Exception:
                    published_iso = published
            if title and link:
                items.append(
                    {
                        "title": title,
                        "url": link,
                        "summary": description,
                        "published_at": published_iso,
                        "source": feed["source"],
                        "feed": feed["label"],
                    }
                )
    return items


def term_in_text(term: str, text_lower: str, text_raw: str, *, ticker: bool = False) -> bool:
    if not term:
        return False
    if ticker:
        if len(term) <= 2:
            return False
        return re.search(rf"(?<![A-Z0-9$])\$?{re.escape(term.upper())}(?![A-Z0-9])", text_raw) is not None
    term_l = term.lower()
    if " " in term_l or "-" in term_l or "." in term_l:
        return term_l in text_lower
    return re.search(rf"(?<![a-z0-9]){re.escape(term_l)}(?![a-z0-9])", text_lower) is not None


def concept_text_score(concept: dict[str, Any], text_raw: str) -> int:
    text_lower = text_raw.lower()
    score = 0
    for term in concept.get("keywords", []):
        if term_in_text(str(term), text_lower, text_raw):
            score += 3 if len(str(term)) >= 4 else 2
    for ticker in concept.get("us_tickers", []):
        if term_in_text(str(ticker), text_lower, text_raw, ticker=True):
            score += 2
    return score


def filter_public_research(concept: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        score = concept_text_score(concept, text)
        ranked.append((score, item))
    selected = [item for score, item in sorted(ranked, key=lambda row: row[0], reverse=True) if score > 0]
    return selected[:5]


def fetch_public_research_search(concept: dict[str, Any], session: requests.Session) -> list[dict[str, Any]]:
    providers = '"Interactive Brokers" OR IBKR OR Finimize OR Morningstar OR Invesco OR "S&P Global" OR "CME Group"'
    query = f'{concept["news_query"]} ({providers})'
    params = {"q": f"{query} when:90d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        response = session.get("https://news.google.com/rss/search", params=params, timeout=12)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"), 180)
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        published_iso = None
        if published:
            try:
                published_iso = parsedate_to_datetime(published).astimezone(SH_TZ).isoformat()
            except Exception:
                published_iso = published
        if title and link and concept_text_score(concept, title) > 0:
            source = title.rsplit(" - ", 1)[-1] if " - " in title else "Public research"
            out.append(
                {
                    "title": title,
                    "url": link,
                    "summary": "主题定向公开研究/机构观点搜索结果。",
                    "published_at": published_iso,
                    "source": source,
                    "feed": "Public research search",
                }
            )
        if len(out) >= 4:
            break
    return out


def merge_research_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for group in groups:
        for item in group:
            key = item.get("url") or item.get("title")
            if not key or key in seen:
                continue
            seen.add(str(key))
            merged.append(item)
    return merged[:6]


def normalize_concept_template(concept: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(concept)
    seen_tickers: set[str] = set()
    tickers: list[str] = []
    for ticker in normalized.get("us_tickers", []):
        symbol = str(ticker).strip().upper()
        if symbol and symbol not in seen_tickers:
            seen_tickers.add(symbol)
            tickers.append(symbol)
    seen_codes: set[str] = set()
    companies: list[CnCompany] = []
    for company in normalized.get("cn_companies", []):
        if not isinstance(company, CnCompany):
            continue
        if not supported_a_share(company.code):
            continue
        if company.code in seen_codes:
            continue
        seen_codes.add(company.code)
        companies.append(company)
    normalized["us_tickers"] = tickers
    normalized["cn_companies"] = companies
    normalized["dynamic"] = True
    normalized["source_type"] = "dynamic_discovery"
    return normalized


def discovery_quote_signal(quote: dict[str, Any], market_context: dict[str, Any]) -> float:
    if not quote.get("ok"):
        return 0.0
    change_1d = safe_float(quote.get("change_1d")) or 0
    change_5d = safe_float(quote.get("change_5d")) or 0
    relative_volume = safe_float(quote.get("relative_volume")) or 1
    us_proxy_1d = safe_float(market_context.get("us_proxy_1d")) or 0
    us_proxy_5d = safe_float(market_context.get("us_proxy_5d")) or 0
    residual_1d = change_1d - us_proxy_1d
    residual_5d = change_5d - us_proxy_5d
    return clamp(max(residual_1d, 0) * 5.5 + max(residual_5d, 0) * 1.6 + max(relative_volume - 1.15, 0) * 5.0, 0, 38)


def discovery_activation_score(
    rule: dict[str, Any],
    us_quote_map: dict[str, dict[str, Any]],
    public_research_items: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    tickers = [str(ticker).upper() for ticker in rule.get("us_tickers", [])]
    quotes = [us_quote_map.get(ticker, {"symbol": ticker, "ok": False}) for ticker in tickers]
    ok_quotes = [quote for quote in quotes if quote.get("ok")]
    quote_scores = [(discovery_quote_signal(quote, market_context), quote) for quote in ok_quotes]
    top_movers = [
        quote
        for score, quote in sorted(quote_scores, key=lambda item: item[0], reverse=True)
        if score >= 7 or (safe_float(quote.get("change_1d")) or 0) >= 2.2 or (safe_float(quote.get("change_5d")) or 0) >= 5
    ][:4]
    research_hits = filter_public_research(rule, public_research_items)
    keyword_hits = sum(concept_text_score(rule, f"{item.get('title', '')} {item.get('summary', '')}") for item in research_hits[:5])
    us_avg = winsorized_mean([quote.get("change_1d") for quote in ok_quotes])
    us_avg_5d = winsorized_mean([quote.get("change_5d") for quote in ok_quotes])
    max_quote_score = max([score for score, _ in quote_scores] or [0])
    mover_score = min(len(top_movers), 4) * 4.5 + max_quote_score * 0.55
    evidence_score = min(len(research_hits), 5) * 6.5 + min(keyword_hits, 18) * 0.9
    breadth_score = min(len(ok_quotes), 8) * 0.9
    momentum_score = clamp(max(us_avg or 0, 0) * 2.2 + max(us_avg_5d or 0, 0) * 0.65, 0, 22)
    activation = clamp(mover_score + evidence_score + breadth_score + momentum_score, 0, 100)
    return {
        "activation_score": round(activation, 1),
        "us_avg_1d": us_avg,
        "us_avg_5d": us_avg_5d,
        "top_movers": [
            {
                "symbol": quote.get("symbol"),
                "change_1d": quote.get("change_1d"),
                "change_5d": quote.get("change_5d"),
                "relative_volume": quote.get("relative_volume"),
            }
            for quote in top_movers
        ],
        "research_hits": research_hits[:3],
        "matched_research_count": len(research_hits),
        "keyword_hit_score": keyword_hits,
        "ok_us_tickers": len(ok_quotes),
    }


def discover_dynamic_concepts(
    us_quote_map: dict[str, dict[str, Any]],
    public_research_items: list[dict[str, Any]],
    market_context: dict[str, Any],
    existing_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranked: list[tuple[float, dict[str, Any]]] = []
    candidates: list[dict[str, Any]] = []
    for rule in DYNAMIC_DISCOVERY_RULES:
        scorecard = discovery_activation_score(rule, us_quote_map, public_research_items, market_context)
        candidate = normalize_concept_template(rule)
        candidate["discovery"] = {
            **scorecard,
            "method": "美股残差动量 + 相对成交 + 公开研究标题关键词 + 沪深京全部上市板块映射可用性",
            "activated_at_shanghai": shanghai_now().strftime("%Y-%m-%d %H:%M:%S CST"),
        }
        candidates.append(candidate)
        if candidate["id"] in existing_ids:
            continue
        if scorecard["activation_score"] < DYNAMIC_DISCOVERY_MIN_SCORE:
            continue
        if scorecard["ok_us_tickers"] < 3 or len(candidate.get("cn_companies", [])) < 3:
            continue
        if not scorecard["top_movers"] and scorecard["matched_research_count"] < 1:
            continue
        ranked.append((float(scorecard["activation_score"]), candidate))
    selected = [candidate for _, candidate in sorted(ranked, key=lambda row: row[0], reverse=True)[:DYNAMIC_DISCOVERY_MAX_CONCEPTS]]
    metadata = {
        "enabled": True,
        "mode": "core_pool_plus_dynamic_candidates",
        "core_pool_size": len(CONCEPTS),
        "candidate_rule_count": len(DYNAMIC_DISCOVERY_RULES),
        "min_activation_score": DYNAMIC_DISCOVERY_MIN_SCORE,
        "max_dynamic_concepts": DYNAMIC_DISCOVERY_MAX_CONCEPTS,
        "selected_count": len(selected),
        "selected": [
            {
                "id": item.get("id"),
                "short_name": item.get("short_name"),
                "activation_score": item.get("discovery", {}).get("activation_score"),
                "top_movers": item.get("discovery", {}).get("top_movers", [])[:3],
                "matched_research_count": item.get("discovery", {}).get("matched_research_count"),
            }
            for item in selected
        ],
        "candidate_scores": sorted(
            [
                {
                    "id": item.get("id"),
                    "short_name": item.get("short_name"),
                    "activation_score": item.get("discovery", {}).get("activation_score"),
                    "matched_research_count": item.get("discovery", {}).get("matched_research_count"),
                    "top_mover_count": len(item.get("discovery", {}).get("top_movers", [])),
                }
                for item in candidates
            ],
            key=lambda item: safe_float(item.get("activation_score")) or 0,
            reverse=True,
        ),
    }
    return selected, metadata


def fetch_x_discussion(concept: dict[str, Any], session: requests.Session) -> dict[str, Any]:
    if os.getenv("X_ENABLE_PAID_API", "").strip().lower() not in {"1", "true", "yes"}:
        return {
            "status": "disabled_free_mode",
            "items": [],
            "message": "已禁用付费 X API；仅保留公开网页/新闻源研究。",
        }
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if not token:
        return {"status": "not_configured", "items": [], "message": "未配置 X_BEARER_TOKEN"}
    x_session = request_session()
    x_session.headers.update({"Authorization": f"Bearer {token}"})
    params = {
        "query": concept["x_query"] + " lang:en -is:retweet",
        "max_results": "10",
        "tweet.fields": "created_at,public_metrics,lang",
    }
    try:
        response = x_session.get("https://api.x.com/2/tweets/search/recent", params=params, timeout=12)
        if response.status_code == 429:
            return {"status": "rate_limited", "items": [], "message": "X API rate limited"}
        response.raise_for_status()
        data = response.json()
        items = []
        for tweet in data.get("data") or []:
            metrics = tweet.get("public_metrics") or {}
            items.append(
                {
                    "id": str(tweet.get("id") or ""),
                    "created_at": tweet.get("created_at"),
                    "text": str(tweet.get("text") or "")[:280],
                    "score": int(metrics.get("like_count") or 0)
                    + int(metrics.get("retweet_count") or 0) * 2
                    + int(metrics.get("reply_count") or 0),
                }
            )
        return {"status": "connected", "items": sorted(items, key=lambda x: x["score"], reverse=True)}
    except Exception as exc:
        return {"status": "failed", "items": [], "message": f"{type(exc).__name__}: {exc}"}


def load_a_stock_cli() -> Any | None:
    path = ROOT / "scripts" / "a_stock_cli.py"
    spec = importlib.util.spec_from_file_location("a_stock_cli", path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def fallback_spot_rows() -> dict[str, dict[str, Any]]:
    candidates = sorted((ROOT / "output").glob("all_a_spot_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {}
    if time.time() - candidates[0].stat().st_mtime > 72 * 60 * 60:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    try:
        with candidates[0].open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = re.sub(r"\D", "", str(row.get("代码") or row.get("code") or ""))
                if len(code) != 6:
                    continue
                rows[code] = row
    except Exception:
        return {}
    return rows


def fetch_cn_quotes(codes: list[str]) -> tuple[dict[str, dict[str, Any]], str]:
    quotes: dict[str, dict[str, Any]] = {}
    source = "unavailable"
    cli = load_a_stock_cli()
    if cli is not None:
        try:
            if hasattr(cli, "fetch_realtime_with_source"):
                quotes, source = cli.fetch_realtime_with_source(codes)
            else:
                quotes = cli.fetch_realtime(codes)
                source = "Sina/easyquotation"
        except Exception:
            quotes = {}
    if quotes:
        return quotes, source
    fallback = fallback_spot_rows()
    if fallback:
        return fallback, "cached all_a_spot"
    return {}, "static mapping"


def baostock_symbol(code: str) -> str:
    return f"sh.{code}" if code.startswith("6") else f"sz.{code}"


def yahoo_cn_symbol(code: str) -> str:
    return f"{code}.SS" if code.startswith("6") else f"{code}.SZ"


def rows_from_yfinance_frame(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    rows: list[dict[str, Any]] = []
    for idx, row in frame.dropna(how="all").iterrows():
        close = safe_float(row.get("Close"))
        if close is None:
            continue
        open_ = safe_float(row.get("Open")) or close
        high = safe_float(row.get("High")) or max(open_, close)
        low = safe_float(row.get("Low")) or min(open_, close)
        date_text = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        rows.append(
            {
                "date": date_text,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": safe_float(row.get("Volume")) or 0,
            }
        )
    return rows


def fetch_cn_sparks_yfinance(codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return {}
    if not codes:
        return {}
    symbols = [yahoo_cn_symbol(code) for code in codes]
    try:
        data = yf.download(
            symbols,
            period="5y",
            interval="1d",
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
    except Exception:
        return {}
    sparks: dict[str, list[dict[str, Any]]] = {}
    for code, symbol in zip(codes, symbols, strict=False):
        try:
            frame = data[symbol] if hasattr(data, "columns") and symbol in data.columns.get_level_values(0) else data
        except Exception:
            continue
        rows = rows_from_yfinance_frame(frame)
        if len(rows) >= 2:
            sparks[code] = rows[-LONG_CHART_DAYS:]
    return sparks


def fetch_cn_sparks_baostock(codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    try:
        import baostock as bs  # type: ignore
    except Exception:
        return {}

    sparks: dict[str, list[dict[str, Any]]] = {}
    login = bs.login()
    if getattr(login, "error_code", "1") != "0":
        return {}
    try:
        end_dt = shanghai_now()
        end_date = end_dt.strftime("%Y-%m-%d")
        start_date = (end_dt - timedelta(days=LONG_CHART_DAYS * 2)).strftime("%Y-%m-%d")
        for code in codes:
            try:
                rs = bs.query_history_k_data_plus(
                    baostock_symbol(code),
                    "date,open,high,low,close,volume",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2",
                )
            except Exception:
                continue
            if getattr(rs, "error_code", "1") != "0":
                continue
            rows: list[dict[str, Any]] = []
            while rs.next():
                raw = rs.get_row_data()
                if len(raw) < 6:
                    continue
                close = safe_float(raw[4])
                if close is None:
                    continue
                open_ = safe_float(raw[1]) or close
                high = safe_float(raw[2]) or max(open_, close)
                low = safe_float(raw[3]) or min(open_, close)
                rows.append(
                    {
                        "date": raw[0],
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": safe_float(raw[5]) or 0,
                    }
                )
            if len(rows) >= 2:
                sparks[code] = rows[-LONG_CHART_DAYS:]
    finally:
        try:
            bs.logout()
        except Exception:
            pass
    return sparks


def fetch_cn_sparks(codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    if os.getenv("MARKET_LAG_REUSE_CN_CANDLES", "").strip().lower() in {"1", "true", "yes"}:
        cached = load_json(DATA_PATH, {})
        cached_sparks: dict[str, list[dict[str, Any]]] = {}
        for concept in cached.get("concepts", []) if isinstance(cached, dict) else []:
            for company in (concept.get("cn") or {}).get("companies", []):
                code = str(company.get("code") or "")
                rows = company.get("candles") or company.get("spark") or []
                if code in codes and len(rows) >= 2 and code not in cached_sparks:
                    cached_sparks[code] = rows[-LONG_CHART_DAYS:]
        if cached_sparks:
            return cached_sparks
    sparks = fetch_cn_sparks_yfinance(codes)
    missing_codes = [code for code in codes if code not in sparks]
    if not missing_codes:
        return sparks
    baostock_sparks = fetch_cn_sparks_baostock(missing_codes)
    sparks.update(baostock_sparks)
    missing_codes = [code for code in missing_codes if code not in sparks]
    if not missing_codes:
        return sparks

    cli = load_a_stock_cli()
    if cli is None:
        return sparks
    use_sina_direct = os.getenv("A_STOCK_KLINE_SOURCE", "").strip().lower() == "sina"
    for code in missing_codes:
        try:
            if use_sina_direct and hasattr(cli, "fetch_history_sina"):
                df = cli.fetch_history_sina(code, days=LONG_CHART_DAYS)
            else:
                df = cli.fetch_history(code, days=LONG_CHART_DAYS, adjust="qfq")
        except Exception:
            continue
        if df is None or getattr(df, "empty", True):
            continue
        if getattr(df, "attrs", {}).get("source") == "sina_unadjusted":
            use_sina_direct = True
        rows: list[dict[str, Any]] = []
        for _, row in df.tail(LONG_CHART_DAYS).iterrows():
            close = safe_float(row.get("收盘"))
            if close is None:
                continue
            open_ = safe_float(row.get("开盘")) or close
            high = safe_float(row.get("最高")) or max(open_, close)
            low = safe_float(row.get("最低")) or min(open_, close)
            rows.append(
                {
                    "date": str(row.get("日期") or "")[:10],
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": safe_float(row.get("成交量")) or 0,
                }
            )
        if len(rows) >= 2:
            sparks[code] = rows
    return sparks


def cn_quote_payload(
    company: CnCompany,
    quote: dict[str, Any],
    source: str,
    spark: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    code = company.code
    candles = spark or []
    candle_latest = latest_candle_close(candles)
    candle_previous = latest_candle_close(candles, 1)
    if "easyquotation" in source:
        quoted_price = positive_float(quote.get("now"))
        price = quoted_price or candle_latest or positive_float(quote.get("close"))
        prev = positive_float(quote.get("close")) if quoted_price else candle_previous or positive_float(quote.get("close"))
        high = positive_float(quote.get("high")) or price
        if source.startswith("Tencent"):
            amount = safe_float(quote.get("成交额(万)") or quote.get("amount"))
            if amount is None:
                share_volume = safe_float(quote.get("volume"))
                amount = share_volume * quoted_price if share_volume is not None and quoted_price is not None else None
        else:
            amount = safe_float(quote.get("amount") or quote.get("volume"))
        traded_at = (
            str(quote.get("datetime") or "").strip()
            or f"{quote.get('date', '')} {quote.get('time', '')}".strip()
            or None
        )
        name = quote.get("name") or company.name
    else:
        quoted_price = positive_float(quote.get("最新价") or quote.get("现价") or quote.get("price"))
        price = quoted_price or candle_latest
        prev = candle_previous
        high = positive_float(quote.get("最高")) or price
        amount = safe_float(quote.get("成交额"))
        traded_at = quote.get("时间") or quote.get("日期") or None
        name = quote.get("名称") or quote.get("name") or company.name
    change = pct_change(price, prev)
    if change is None:
        change = safe_float(quote.get("涨跌幅"))
    if (change is None or change <= -99) and candle_latest and candle_previous:
        price = candle_latest
        change = pct_change(candle_latest, candle_previous)
    if change is not None and change <= -99:
        change = None
    if amount is None and candles:
        amount = candle_amount(candles[-1])
    avg_20d_amount = avg_recent_amount(candles, 20) if candles else None
    if amount is not None and avg_20d_amount is not None and avg_20d_amount > 0 and amount / avg_20d_amount > 25:
        avg_20d_amount *= 100
    relative_amount = amount / avg_20d_amount if amount is not None and avg_20d_amount else None
    liquidity_confirm_score, overheat_penalty = relative_amount_score(relative_amount, change)
    mapping_confidence, risk_flags = mapping_confidence_for_company(company, amount, change, candles, relative_amount)
    tradability = "normal"
    if change is not None and change >= 9.3:
        tradability = "limit_watch"
    elif amount is None or amount <= 0:
        tradability = "liquidity_unknown"
    elif amount < 30_000_000:
        tradability = "thin_liquidity"
        risk_flags.append("成交偏薄")
    chart_ref = write_chart_file("cn", code, candles, source, "5年日K") if candles else None
    return {
        "code": code,
        "name": name,
        "market": "SH" if code.startswith("6") else "SZ",
        "role": company.role,
        "reason": company.reason,
        "price": price,
        "change": change,
        "high": high,
        "amount": amount,
        "amount_label": compact_amount(amount),
        "avg_20d_amount": avg_20d_amount,
        "relative_amount": relative_amount,
        "liquidity_confirm_score": liquidity_confirm_score,
        "overheat_penalty": overheat_penalty,
        "mapping_confidence": mapping_confidence,
        "mapping_quality": "高" if mapping_confidence >= 72 else "中" if mapping_confidence >= 55 else "低",
        "tradability": tradability,
        "risk_flags": risk_flags,
        "traded_at": traded_at,
        "source": source,
        "spark": candles[-20:],
        "candles": candles[-INLINE_CANDLE_DAYS:],
        "chart_ref": chart_ref,
        "candle_count": len(candles),
        "candle_start": candles[0]["date"] if candles else None,
        "chart_period": "1个月日线",
        "candle_period": "5年日K",
    }


def load_ibkr_public_status(research_items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "public_only",
        "source": "IBKR Campus / Traders' Insight RSS",
        "message": f"不连接个人账户；已读取 {len(research_items)} 条公开研究/观点候选。",
    }


def manual_import_status() -> dict[str, Any]:
    return {
        "status": "disabled_in_open_source_build",
        "message": "开源版不读取券商账户、持仓、成交记录或本机个人文件。",
        "artifacts": [],
    }


def concept_scores(
    us_quotes: list[dict[str, Any]],
    cn_quotes: list[dict[str, Any]],
    news_count: int,
    research_count: int,
    x_count: int,
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = market_context or {}
    us_changes_raw = [q["change_1d"] for q in us_quotes if q.get("ok") and q.get("change_1d") is not None]
    us_5d_raw = [q["change_5d"] for q in us_quotes if q.get("ok") and q.get("change_5d") is not None]
    cn_changes_raw = [q["change"] for q in cn_quotes if q.get("change") is not None]
    cn_amounts = [q["amount"] for q in cn_quotes if q.get("amount") is not None]
    us_avg_raw = winsorized_mean(us_changes_raw)
    us_avg_5d_raw = winsorized_mean(us_5d_raw)
    cn_avg_raw = winsorized_mean(cn_changes_raw)
    us_avg = weighted_average([(q.get("change_1d"), q.get("signal_weight") or us_signal_weight(q)) for q in us_quotes]) or us_avg_raw
    us_avg_5d = weighted_average([(q.get("change_5d"), q.get("signal_weight") or us_signal_weight(q)) for q in us_quotes]) or us_avg_5d_raw
    cn_avg = weighted_average([(q.get("change"), q.get("mapping_confidence") or 50) for q in cn_quotes]) or cn_avg_raw
    lag_gap_raw = (us_avg_raw or 0) - (cn_avg_raw or 0)
    lag_gap = (us_avg or 0) - (cn_avg or 0)
    us_residual_1d = (us_avg or 0) - (safe_float(context.get("us_proxy_1d")) or 0)
    us_residual_5d = (us_avg_5d or 0) - (safe_float(context.get("us_proxy_5d")) or 0)
    cn_residual_1d = (cn_avg or 0) - (safe_float(context.get("cn_proxy_1d")) or 0)
    lag_gap_neutral = us_residual_1d - cn_residual_1d

    evidence_score = min(news_count, 5) * 0.9 + min(research_count, 5) * 0.75 + min(x_count, 5) * 0.3
    evidence_quality_score = clamp(evidence_score * 6.2 + min(research_count, 6) * 2.0 + min(news_count, 6) * 1.15, 0, 38)
    signal_coverage_score = min(24, evidence_score + min(len(us_changes_raw), 12) * 0.62 + min(len(cn_changes_raw), 12) * 0.42)
    cn_liquidity_score = min(sum(cn_amounts) / 1_000_000_000, 18) if cn_amounts else 0
    cn_confirm_score = winsorized_mean([q.get("liquidity_confirm_score") for q in cn_quotes]) or 0
    mapping_quality_score = winsorized_mean([q.get("mapping_confidence") for q in cn_quotes]) or 42
    overheat_penalty = winsorized_mean([q.get("overheat_penalty") for q in cn_quotes]) or 0
    limit_up_count = len([q for q in cn_quotes if safe_float(q.get("change")) is not None and float(q.get("change") or 0) >= 9.3])
    limit_up_ratio = limit_up_count / len(cn_quotes) if cn_quotes else 0
    if limit_up_ratio >= 0.22:
        overheat_penalty += min(16, limit_up_ratio * 36)
    reversal_penalty = 0.0
    if us_residual_1d <= -1.3 and (us_avg_5d or 0) <= 0:
        reversal_penalty = 14
    elif us_residual_1d <= -1.0:
        reversal_penalty = 8
    data_quality_penalty = 0.0
    if len(us_changes_raw) < 3:
        data_quality_penalty += 8
    if len(cn_changes_raw) < 3:
        data_quality_penalty += 8
    if mapping_quality_score < 52:
        data_quality_penalty += 6

    lag_component = clamp(max(lag_gap_neutral, 0) * 7.2, 0, 26)
    us_momentum = clamp(max(us_residual_1d, 0) * 5.4 + max(us_residual_5d, 0) * 1.25, 0, 24)
    research_heat_score = clamp(22 + us_momentum * 1.25 + evidence_quality_score * 1.05 + min(len(us_changes_raw), 12) * 1.15, 0, 100)
    trade_state_score = clamp(42 + cn_confirm_score * 2.0 + max(cn_residual_1d, 0) * 2.2 - overheat_penalty - reversal_penalty - data_quality_penalty, 0, 100)
    risk_flags: list[str] = []
    if overheat_penalty >= 14:
        risk_flags.append("A股交易过热")
    if reversal_penalty >= 10:
        risk_flags.append("美股转弱")
    if data_quality_penalty >= 12:
        risk_flags.append("样本/数据不足")
    if mapping_quality_score < 55:
        risk_flags.append("映射质量偏弱")

    no_trade = reversal_penalty >= 14 or data_quality_penalty >= 20 or overheat_penalty >= 28
    score_cap = 100.0
    if no_trade:
        score_cap = 54.0
    elif overheat_penalty >= 18:
        score_cap = 72.0
    elif data_quality_penalty >= 12:
        score_cap = 78.0
    elif mapping_quality_score < 58:
        score_cap = 82.0
    base_opportunity = (
        lag_component
        + us_momentum
        + evidence_quality_score * 0.34
        + mapping_quality_score * 0.18
        + cn_confirm_score * 0.75
        + signal_coverage_score * 0.22
        - overheat_penalty * 0.68
        - reversal_penalty * 0.9
        - data_quality_penalty * 0.65
    )
    opportunity_score = clamp(base_opportunity, 0, score_cap)
    lag_score = clamp(lag_component + us_momentum + evidence_quality_score * 0.25 + signal_coverage_score * 0.25, 0, 100)

    if no_trade and reversal_penalty >= 14:
        phase = "美股降温，A股回避追高"
        action = "回避/只观察"
    elif overheat_penalty >= 18:
        phase = "A股交易拥挤，等待回落确认"
        action = "不追高"
    elif lag_gap_neutral >= 2.2 and us_residual_1d > 0:
        phase = "美股先动，A股待确认"
        action = "等A股放量确认"
    elif us_residual_1d >= 0.8 and cn_residual_1d >= 0.8:
        phase = "两边同步发酵"
        action = "确认强弱"
    elif data_quality_penalty >= 12:
        phase = "数据不足，人工复核"
        action = "复核数据"
    else:
        phase = "观察中"
        action = "观察"

    legacy_us_momentum = max(us_avg_raw or 0, 0) * 0.8 + max(us_avg_5d_raw or 0, 0) * 0.35
    legacy_global_factor_score = min(20, evidence_score + min(len(us_changes_raw), 10) * 0.55 + min(len(cn_changes_raw), 10) * 0.35)
    legacy_lag_score = max(lag_gap_raw, 0) * 2.2 + legacy_us_momentum + evidence_score
    legacy_opportunity_score = (
        max(lag_gap_raw, 0) * 1.7
        + legacy_us_momentum * 0.75
        + evidence_score
        + cn_liquidity_score * 0.35
        + legacy_global_factor_score * 0.25
    )
    confidence = clamp(
        30
        + research_heat_score * 0.24
        + mapping_quality_score * 0.23
        + trade_state_score * 0.18
        - overheat_penalty * 0.18
        - reversal_penalty * 0.28
        - data_quality_penalty * 0.2,
        0,
        96,
    )
    return {
        "us_avg_1d": us_avg,
        "us_avg_5d": us_avg_5d,
        "cn_avg_1d": cn_avg,
        "lag_gap": lag_gap,
        "lag_gap_raw": lag_gap_raw,
        "lag_gap_neutral": lag_gap_neutral,
        "us_residual_1d": us_residual_1d,
        "us_residual_5d": us_residual_5d,
        "cn_residual_1d": cn_residual_1d,
        "lag_score": lag_score,
        "opportunity_score": opportunity_score,
        "legacy_lag_score": legacy_lag_score,
        "legacy_opportunity_score": legacy_opportunity_score,
        "evidence_score": evidence_score,
        "evidence_quality_score": evidence_quality_score,
        "signal_coverage_score": signal_coverage_score,
        "global_factor_score": signal_coverage_score,
        "legacy_global_factor_score": legacy_global_factor_score,
        "cn_liquidity_score": cn_liquidity_score,
        "research_heat_score": research_heat_score,
        "mapping_quality_score": mapping_quality_score,
        "trade_state_score": trade_state_score,
        "cn_confirm_score": cn_confirm_score,
        "overheat_penalty": overheat_penalty,
        "reversal_penalty": reversal_penalty,
        "data_quality_penalty": data_quality_penalty,
        "score_cap": score_cap,
        "no_trade": no_trade,
        "risk_flags": risk_flags,
        "action": action,
        "market_proxy": {
            "us_1d": context.get("us_proxy_1d"),
            "us_5d": context.get("us_proxy_5d"),
            "cn_1d": context.get("cn_proxy_1d"),
            "cn_5d": context.get("cn_proxy_5d"),
        },
        "score_components": {
            "lag_gap": max(lag_gap_neutral, 0),
            "us_momentum": us_momentum,
            "evidence": evidence_quality_score,
            "mapping_quality": mapping_quality_score,
            "cn_confirm": cn_confirm_score,
            "cn_liquidity": cn_liquidity_score,
            "signal_coverage": signal_coverage_score,
            "overheat_penalty": overheat_penalty,
            "reversal_penalty": reversal_penalty,
            "data_quality_penalty": data_quality_penalty,
            "global_factor": signal_coverage_score,
        },
        "phase": phase,
        "confidence": confidence,
    }


def candle_at_or_before(rows: list[dict[str, Any]] | None, target_date: str) -> tuple[int, dict[str, Any]] | None:
    if not rows:
        return None
    for idx in range(len(rows) - 1, -1, -1):
        date = str(rows[idx].get("date") or "")
        if date and date <= target_date and positive_float(rows[idx].get("close")) is not None:
            return idx, rows[idx]
    return None


def historical_pct(rows: list[dict[str, Any]], idx: int, lookback: int = 1) -> float | None:
    if idx < 0 or idx - lookback < 0:
        return None
    latest = positive_float(rows[idx].get("close"))
    previous = positive_float(rows[idx - lookback].get("close"))
    return pct_change(latest, previous)


def historical_us_quote(quote: dict[str, Any], target_date: str) -> dict[str, Any] | None:
    rows = quote.get("candles") or quote.get("spark") or []
    found = candle_at_or_before(rows, target_date)
    if not found:
        return None
    idx, row = found
    return {
        "symbol": quote.get("symbol"),
        "price": positive_float(row.get("close")),
        "date": row.get("date"),
        "change_1d": historical_pct(rows, idx, 1),
        "change_5d": historical_pct(rows, idx, 5),
        "signal_role": quote.get("signal_role"),
        "signal_weight": quote.get("signal_weight"),
        "ok": True,
    }


def historical_cn_quote(company: dict[str, Any], target_date: str) -> dict[str, Any] | None:
    rows = company.get("candles") or company.get("spark") or []
    found = candle_at_or_before(rows, target_date)
    latest = candle_at_or_before(rows, "9999-12-31")
    if not found or not latest:
        return None
    idx, row = found
    latest_idx, latest_row = latest
    buy_price = positive_float(row.get("close"))
    latest_price = positive_float(latest_row.get("close"))
    event_returns = event_window_returns(rows, idx, buy_price)
    payload = {
        "code": company.get("code"),
        "name": company.get("name"),
        "market": company.get("market"),
        "role": company.get("role"),
        "reason": company.get("reason"),
        "buy_date": row.get("date"),
        "buy_price": buy_price,
        "latest_date": latest_row.get("date"),
        "latest_price": latest_price,
        "buy_day_change": historical_pct(rows, idx, 1),
        "return_since": pct_change(latest_price, buy_price),
        "holding_days": max(latest_idx - idx, 0),
        "mapping_confidence": company.get("mapping_confidence"),
        "mapping_quality": company.get("mapping_quality"),
        "relative_amount": company.get("relative_amount"),
        "liquidity_confirm_score": company.get("liquidity_confirm_score"),
        "risk_flags": company.get("risk_flags") or [],
        "entry_model": "next available close / fixed event windows",
    }
    payload.update(event_returns)
    return payload


def average(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def quantile(values: list[float | None], q: float) -> float | None:
    usable = sorted(value for value in (safe_float(item) for item in values) if value is not None)
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    position = (len(usable) - 1) * clamp(q, 0, 1)
    lower_idx = math.floor(position)
    upper_idx = math.ceil(position)
    if lower_idx == upper_idx:
        return usable[lower_idx]
    lower_weight = upper_idx - position
    upper_weight = position - lower_idx
    return usable[lower_idx] * lower_weight + usable[upper_idx] * upper_weight


def wilson_lower_bound(successes: int, trials: int, z: float = 1.64) -> float | None:
    if trials <= 0:
        return None
    proportion = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    centre = proportion + z2 / (2 * trials)
    margin = z * math.sqrt((proportion * (1 - proportion) + z2 / (4 * trials)) / trials)
    return clamp((centre - margin) / denominator, 0, 1)


def split_train_validation(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(items, key=lambda item: str(item.get("date") or ""))
    if len(ordered) < 12:
        return ordered, []
    validation_count = max(VALIDATION_MIN_SAMPLES, math.ceil(len(ordered) * VALIDATION_SHARE))
    validation_count = min(validation_count, max(0, len(ordered) - 6))
    if validation_count <= 0:
        return ordered, []
    return ordered[:-validation_count], ordered[-validation_count:]


def probability_for_returns(returns: list[float | None]) -> tuple[int, int, float | None, float | None]:
    usable = [safe_float(value) for value in returns if safe_float(value) is not None]
    samples = len(usable)
    successes = len([value for value in usable if float(value or 0) > 0])
    raw = successes / samples if samples else None
    lower = wilson_lower_bound(successes, samples)
    return samples, successes, raw, lower


def reliability_grade(score: float, lower: float | None, validation_lower: float | None, samples: int, p10_net: float | None) -> str:
    lower_value = safe_float(lower) or 0
    validation_value = safe_float(validation_lower) if validation_lower is not None else lower_value
    p10_value = safe_float(p10_net)
    if samples < 18:
        return "样本不足"
    if score >= 82 and lower_value >= 0.68 and validation_value >= 0.56 and (p10_value is None or p10_value > -4):
        return "高可靠观察"
    if score >= 70 and lower_value >= 0.58 and validation_value >= 0.48:
        return "可观察"
    if score >= 56 and lower_value >= 0.50:
        return "低仓观察"
    return "仅研究"


def auto_recommendation_score(best: dict[str, Any], day_5: dict[str, Any]) -> float:
    best_score = safe_float(best.get("certainty_score")) or safe_float(best.get("reliability_score")) or 0
    day5_score = safe_float(day_5.get("reliability_score")) or safe_float(day_5.get("certainty_score")) or 0
    lower_5 = safe_float(day_5.get("conservative_probability")) or 0
    validation_5_raw = safe_float(day_5.get("validation_conservative_probability"))
    validation_5 = validation_5_raw if validation_5_raw is not None else lower_5 * 0.82
    p10_5 = safe_float(day_5.get("p10_return_after_cost"))
    avg_net_5 = safe_float(day_5.get("avg_return_after_cost")) or 0
    samples_5 = int(day_5.get("samples") or 0)
    best_validation = safe_float(best.get("validation_conservative_probability"))
    best_p10 = safe_float(best.get("p10_return_after_cost"))

    score = best_score * 0.52 + day5_score * 0.48
    score_cap = 96
    if samples_5 < 12:
        score_cap = min(score_cap, 58)
    elif samples_5 < 18:
        score_cap = min(score_cap, 68)
    elif samples_5 < 24:
        score_cap = min(score_cap, 76)
    if lower_5 < 0.50:
        score_cap = min(score_cap, 60)
    elif lower_5 < 0.56:
        score_cap = min(score_cap, 68)
    elif lower_5 < 0.62:
        score_cap = min(score_cap, 80)
    if validation_5 < 0.44:
        score_cap = min(score_cap, 58)
    elif validation_5 < 0.50:
        score_cap = min(score_cap, 68)
    elif validation_5 < 0.56:
        score_cap = min(score_cap, 78)
    if avg_net_5 <= 0:
        score_cap = min(score_cap, 62)
    if p10_5 is not None and p10_5 < -9:
        score_cap = min(score_cap, 58)
    elif p10_5 is not None and p10_5 < -6:
        score_cap = min(score_cap, 70)
    elif p10_5 is not None and p10_5 < -4:
        score_cap = min(score_cap, 82)
    if best_validation is not None and best_validation < 0.48:
        score_cap = min(score_cap, 78)
    if best_p10 is not None and best_p10 < -6:
        score_cap = min(score_cap, 82)
    return round(clamp(score, 0, score_cap), 1)


def reliability_metrics(events: list[dict[str, Any]], return_key: str) -> dict[str, Any]:
    raw_returns = [safe_float(item.get(return_key)) for item in events]
    usable_returns = [float(value) for value in raw_returns if value is not None]
    net_returns = [value - ROUND_TRIP_COST_PCT for value in usable_returns]
    samples, successes, raw_probability, conservative_probability = probability_for_returns(net_returns)
    weighted_total = sum(float(item.get("weight") or 0) for item in events if safe_float(item.get(return_key)) is not None)
    weighted_success = sum(
        float(item.get("weight") or 0)
        for item in events
        if safe_float(item.get(return_key)) is not None and float(safe_float(item.get(return_key)) or 0) - ROUND_TRIP_COST_PCT > 0
    )
    recency_probability = weighted_success / weighted_total if weighted_total else None
    train_events, validation_events = split_train_validation([item for item in events if safe_float(item.get(return_key)) is not None])
    train_returns = [(safe_float(item.get(return_key)) or 0) - ROUND_TRIP_COST_PCT for item in train_events]
    validation_returns = [(safe_float(item.get(return_key)) or 0) - ROUND_TRIP_COST_PCT for item in validation_events]
    train_samples, train_successes, train_probability, train_lower = probability_for_returns(train_returns)
    validation_samples, validation_successes, validation_probability, validation_lower = probability_for_returns(validation_returns)
    avg_return = average(usable_returns)
    median_return = quantile(usable_returns, 0.5)
    avg_return_net = average(net_returns)
    median_return_net = quantile(net_returns, 0.5)
    p10_return = quantile(usable_returns, 0.10)
    p25_return = quantile(usable_returns, 0.25)
    p75_return = quantile(usable_returns, 0.75)
    p90_return = quantile(usable_returns, 0.90)
    p10_return_net = quantile(net_returns, 0.10)
    p25_return_net = quantile(net_returns, 0.25)
    avg_mae = average([item.get("mae") if "mae" in item else item.get("avg_mae") for item in events])
    lower_value = safe_float(conservative_probability) or 0
    validation_value = safe_float(validation_lower)
    validation_for_score = validation_value if validation_value is not None else lower_value * 0.82
    recency_value = safe_float(recency_probability) or lower_value
    prob_score = clamp((lower_value - 0.48) * 128, 0, 42)
    validation_score = clamp((validation_for_score - 0.46) * 118, 0, 30)
    sample_score = clamp(math.log1p(samples) * 4.1, 0, 16)
    recency_score = clamp((recency_value - 0.50) * 28, -7, 9)
    edge_score = clamp((avg_return_net or 0) * 1.65, -12, 20)
    tail_score = clamp((p10_return_net or -8) + 2.0, -13, 9)
    drawdown_penalty = clamp(abs(min(avg_mae or 0, 0)) * 1.2, 0, 16)
    validation_gap = lower_value - validation_for_score
    degradation_penalty = clamp(validation_gap * 95, 0, 18)
    low_validation_penalty = 10 if validation_samples and validation_for_score < 0.44 else 0
    score_cap = 58
    if samples >= 18:
        score_cap = 68
    if samples >= 24 and lower_value >= 0.54:
        score_cap = 76
    if samples >= 30 and lower_value >= 0.60 and validation_for_score >= 0.50:
        score_cap = 84
    if samples >= 35 and lower_value >= 0.68 and validation_for_score >= 0.56 and (p10_return_net or -99) > -4:
        score_cap = 90
    if lower_value >= 0.78 and validation_for_score >= 0.66 and (p10_return_net or -99) > 0:
        score_cap = 96
    if avg_return_net is not None and avg_return_net <= 0:
        score_cap = min(score_cap, 58)
    if p10_return_net is not None and p10_return_net < -9:
        score_cap = min(score_cap, 58)
    elif p10_return_net is not None and p10_return_net < -6:
        score_cap = min(score_cap, 74)
    elif p10_return_net is not None and p10_return_net < -4:
        score_cap = min(score_cap, 84)
    if validation_samples and validation_for_score < 0.44:
        score_cap = min(score_cap, 60)
    elif validation_samples and validation_for_score < 0.50:
        score_cap = min(score_cap, 70)
    elif validation_samples and validation_for_score < 0.56:
        score_cap = min(score_cap, 82)
    score = clamp(
        prob_score
        + validation_score
        + sample_score
        + recency_score
        + edge_score
        + tail_score
        - drawdown_penalty
        - degradation_penalty
        - low_validation_penalty,
        0,
        score_cap,
    )
    return {
        "samples": samples,
        "successes": successes,
        "raw_probability": raw_probability,
        "conservative_probability": conservative_probability,
        "recency_probability": recency_probability,
        "avg_return": avg_return,
        "median_return": median_return,
        "avg_return_after_cost": avg_return_net,
        "median_return_after_cost": median_return_net,
        "p10_return": p10_return,
        "p25_return": p25_return,
        "p75_return": p75_return,
        "p90_return": p90_return,
        "p10_return_after_cost": p10_return_net,
        "p25_return_after_cost": p25_return_net,
        "avg_mae": avg_mae,
        "train_samples": train_samples,
        "train_successes": train_successes,
        "train_probability": train_probability,
        "train_conservative_probability": train_lower,
        "validation_samples": validation_samples,
        "validation_successes": validation_successes,
        "validation_probability": validation_probability,
        "validation_conservative_probability": validation_lower,
        "validation_gap": validation_gap,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "degradation_penalty": degradation_penalty + low_validation_penalty,
        "reliability_score": round(score, 1),
        "reliability_grade": reliability_grade(score, conservative_probability, validation_lower, samples, p10_return_net),
    }


def follow_trigger_score(scores: dict[str, Any]) -> float:
    us_1d = safe_float(scores.get("us_avg_1d")) or 0
    us_5d = safe_float(scores.get("us_avg_5d")) or 0
    lag_gap = safe_float(scores.get("lag_gap")) or 0
    opportunity = safe_float(scores.get("opportunity_score")) or 0
    if us_1d <= 0 and us_5d <= 0:
        return 0
    return clamp(max(us_1d, 0) * 18 + max(us_5d, 0) * 4.5 + max(lag_gap, 0) * 9 + max(opportunity - 20, 0) * 0.32, 0, 100)


def is_follow_trigger(scores: dict[str, Any]) -> bool:
    us_1d = safe_float(scores.get("us_avg_1d")) or 0
    us_5d = safe_float(scores.get("us_avg_5d")) or 0
    lag_gap = safe_float(scores.get("lag_gap")) or 0
    opportunity = safe_float(scores.get("opportunity_score")) or 0
    return us_1d > 0 and (lag_gap > 0 or us_5d > 1 or opportunity >= 25)


def current_follow_activation(scores: dict[str, Any]) -> float:
    us_residual = safe_float(scores.get("us_residual_1d"))
    if us_residual is None:
        us_residual = safe_float(scores.get("us_avg_1d")) or 0
    lag_gap = safe_float(scores.get("lag_gap_neutral"))
    if lag_gap is None:
        lag_gap = safe_float(scores.get("lag_gap")) or 0
    opportunity = safe_float(scores.get("opportunity_score")) or 0
    mapping_quality = safe_float(scores.get("mapping_quality_score")) or 50
    overheat = safe_float(scores.get("overheat_penalty")) or 0
    reversal = safe_float(scores.get("reversal_penalty")) or 0
    no_trade_penalty = 18 if scores.get("no_trade") else 0
    activation = (
        max(us_residual, 0) * 13
        + max(lag_gap, 0) * 11
        + opportunity * 0.32
        + max(mapping_quality - 50, 0) * 0.18
        - overheat * 0.78
        - reversal * 1.1
        - no_trade_penalty
    )
    return clamp(activation, 0, 100)


def follow_verdict(best: dict[str, Any] | None, activation: float) -> str:
    if not best or not best.get("samples"):
        return "样本不足"
    conservative = safe_float(best.get("conservative_probability")) or 0
    validation = safe_float(best.get("validation_conservative_probability"))
    validation_value = validation if validation is not None else conservative * 0.82
    p10_net = safe_float(best.get("p10_return_after_cost"))
    samples = int(best.get("samples") or 0)
    if samples < 18:
        return "样本偏少"
    if conservative >= 0.68 and validation_value >= 0.56 and activation >= 55 and (p10_net is None or p10_net > -4):
        return "当前可观察"
    if conservative >= 0.58 and validation_value >= 0.48:
        return "候选观察"
    return "历史不稳定"


def historical_concept_scores(
    us_quotes: list[dict[str, Any]],
    cn_quotes: list[dict[str, Any]],
    current_scores: dict[str, Any],
) -> dict[str, Any]:
    us_avg = weighted_average([(q.get("change_1d"), q.get("signal_weight") or 1) for q in us_quotes]) or average(
        [q.get("change_1d") for q in us_quotes]
    )
    us_avg_5d = weighted_average([(q.get("change_5d"), q.get("signal_weight") or 1) for q in us_quotes]) or average(
        [q.get("change_5d") for q in us_quotes]
    )
    cn_avg = weighted_average([(q.get("buy_day_change"), q.get("mapping_confidence") or 50) for q in cn_quotes]) or average(
        [q.get("buy_day_change") for q in cn_quotes]
    )
    lag_gap = (us_avg or 0) - (cn_avg or 0)
    evidence_score = safe_float(current_scores.get("evidence_score")) or 0
    evidence_quality_score = safe_float(current_scores.get("evidence_quality_score")) or evidence_score * 6
    signal_coverage_score = safe_float(current_scores.get("signal_coverage_score")) or safe_float(current_scores.get("global_factor_score")) or 0
    cn_liquidity_score = safe_float(current_scores.get("cn_liquidity_score")) or 0
    mapping_quality_score = safe_float(current_scores.get("mapping_quality_score")) or 50
    cn_confirm_score = safe_float(current_scores.get("cn_confirm_score")) or 0
    overheat_penalty = safe_float(current_scores.get("overheat_penalty")) or 0
    reversal_penalty = 10 if (us_avg or 0) <= -1.5 else 0
    data_quality_penalty = safe_float(current_scores.get("data_quality_penalty")) or 0
    us_momentum = clamp(max(us_avg or 0, 0) * 5.2 + max(us_avg_5d or 0, 0) * 1.1, 0, 24)
    lag_component = clamp(max(lag_gap, 0) * 6.8, 0, 26)
    lag_score = clamp(lag_component + us_momentum + evidence_quality_score * 0.24 + signal_coverage_score * 0.22, 0, 100)
    score_cap = safe_float(current_scores.get("score_cap")) or 100
    opportunity_score = clamp(
        lag_component
        + us_momentum
        + evidence_quality_score * 0.3
        + mapping_quality_score * 0.17
        + cn_confirm_score * 0.62
        + signal_coverage_score * 0.18
        - overheat_penalty * 0.55
        - reversal_penalty * 0.85
        - data_quality_penalty * 0.5,
        0,
        score_cap,
    )
    if current_scores.get("no_trade") or reversal_penalty >= 10:
        phase = "美股降温，A股回避追高"
        action = "回避/只观察"
    elif overheat_penalty >= 18:
        phase = "A股交易拥挤，等待回落确认"
        action = "不追高"
    elif us_avg is not None and cn_avg is not None and lag_gap >= 2:
        phase = "美股先动，A股待确认"
        action = "等A股放量确认"
    elif us_avg is not None and cn_avg is not None and us_avg >= 1 and cn_avg >= 1:
        phase = "两边同步发酵"
        action = "确认强弱"
    else:
        phase = "观察中"
        action = "观察"
    return {
        "us_avg_1d": us_avg,
        "us_avg_5d": us_avg_5d,
        "cn_avg_1d": cn_avg,
        "lag_gap": lag_gap,
        "lag_gap_neutral": lag_gap,
        "lag_score": lag_score,
        "opportunity_score": opportunity_score,
        "legacy_lag_score": current_scores.get("legacy_lag_score"),
        "legacy_opportunity_score": current_scores.get("legacy_opportunity_score"),
        "evidence_score": evidence_score,
        "evidence_quality_score": evidence_quality_score,
        "signal_coverage_score": signal_coverage_score,
        "global_factor_score": signal_coverage_score,
        "cn_liquidity_score": cn_liquidity_score,
        "research_heat_score": current_scores.get("research_heat_score"),
        "mapping_quality_score": mapping_quality_score,
        "trade_state_score": current_scores.get("trade_state_score"),
        "cn_confirm_score": cn_confirm_score,
        "overheat_penalty": overheat_penalty,
        "reversal_penalty": reversal_penalty,
        "data_quality_penalty": data_quality_penalty,
        "score_cap": score_cap,
        "no_trade": current_scores.get("no_trade"),
        "risk_flags": current_scores.get("risk_flags") or [],
        "action": action,
        "score_components": {
            "lag_gap": max(lag_gap, 0),
            "us_momentum": us_momentum,
            "evidence": evidence_quality_score,
            "mapping_quality": mapping_quality_score,
            "cn_confirm": cn_confirm_score,
            "signal_coverage": signal_coverage_score,
            "overheat_penalty": overheat_penalty,
        },
        "phase": phase,
        "confidence": current_scores.get("confidence"),
    }


def build_follow_model(
    snapshots: dict[str, Any],
    date_options: list[dict[str, Any]],
    latest_date: str,
    live_concepts: list[dict[str, Any]],
) -> dict[str, Any]:
    sorted_dates = sorted(snapshots)
    if not sorted_dates:
        return {"available": False, "message": "暂无回测快照，无法计算10日跟随概率。"}

    date_index = {date: idx for idx, date in enumerate(sorted_dates)}
    latest_index = date_index.get(latest_date, len(sorted_dates) - 1)
    live_by_id = {str(concept.get("id")): concept for concept in live_concepts}
    observations: dict[str, dict[int, list[dict[str, Any]]]] = {
        str(concept.get("id")): {horizon: [] for horizon in FOLLOW_HORIZONS} for concept in live_concepts
    }
    stock_observations: dict[str, dict[str, Any]] = {}

    concept_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for date in sorted_dates:
        for concept in snapshots.get(date, {}).get("concepts", []):
            concept_id = str(concept.get("id") or "")
            if concept_id:
                concept_lookup[(concept_id, date)] = concept
            scores = concept.get("scores") or {}
            if not concept_id or not is_follow_trigger(scores):
                continue
            age = max(latest_index - date_index.get(date, latest_index), 0)
            weight = 0.5 ** (age / 20)
            cn = concept.get("cn") or {}
            avg_event_returns = cn.get("avg_event_returns") or {}
            win_rates = cn.get("win_rates") or {}
            companies = cn.get("companies") or []
            avg_threshold = average([safe_float(company.get("dynamic_threshold")) for company in companies]) or 1.0
            live_concept = live_by_id.get(concept_id, {})
            concept_name = live_concept.get("name")
            concept_short_name = live_concept.get("short_name")
            for horizon in FOLLOW_HORIZONS:
                avg_return = safe_float(avg_event_returns.get(f"return_{horizon}d"))
                win_rate = safe_float(win_rates.get(f"win_rate_{horizon}d"))
                if avg_return is None or win_rate is None:
                    continue
                observations.setdefault(concept_id, {h: [] for h in FOLLOW_HORIZONS})
                observations[concept_id][horizon].append(
                    {
                        "date": date,
                        "age": age,
                        "weight": weight,
                        "trigger_score": follow_trigger_score(scores),
                        "us_avg_1d": safe_float(scores.get("us_avg_1d")),
                        "cn_avg_1d": safe_float(scores.get("cn_avg_1d")),
                        "lag_gap": safe_float(scores.get("lag_gap")),
                        "avg_return": avg_return,
                        "win_rate": win_rate,
                        "avg_mfe": safe_float(cn.get("avg_mfe_10d" if horizon > 5 else "avg_mfe_5d")),
                        "avg_mae": safe_float(cn.get("avg_mae_10d" if horizon > 5 else "avg_mae_5d")),
                        "confirm_threshold": max(1.0, avg_threshold),
                        "success": avg_return > 0 and win_rate >= 50,
                        "confirmed": avg_return >= max(1.0, avg_threshold) and win_rate >= 50,
                    }
                )

            for company in companies:
                code = str(company.get("code") or "")
                if not code:
                    continue
                stock_key = f"{concept_id}:{code}"
                bucket = stock_observations.setdefault(
                    stock_key,
                    {
                        "code": code,
                        "name": company.get("name"),
                        "market": company.get("market"),
                        "role": company.get("role"),
                        "reason": company.get("reason"),
                        "concept_id": concept_id,
                        "concept_name": concept_name,
                        "concept_short_name": concept_short_name,
                        "latest_trigger_date": date,
                        "horizon_rows": {horizon: [] for horizon in FOLLOW_HORIZONS},
                    },
                )
                if date > (bucket.get("latest_trigger_date") or ""):
                    bucket["latest_trigger_date"] = date
                for horizon in FOLLOW_HORIZONS:
                    stock_return = safe_float(company.get(f"return_{horizon}d"))
                    if stock_return is None:
                        continue
                    bucket["horizon_rows"].setdefault(horizon, []).append(
                        {
                            "horizon": horizon,
                            "date": date,
                            "weight": weight,
                            "trigger_score": follow_trigger_score(scores),
                            "us_avg_1d": safe_float(scores.get("us_avg_1d")),
                            "cn_avg_1d": safe_float(scores.get("cn_avg_1d")),
                            "lag_gap": safe_float(scores.get("lag_gap")),
                            "avg_threshold": max(1.0, avg_threshold),
                            "return": stock_return,
                            "mfe": safe_float(company.get("mfe_10d" if horizon > 5 else "mfe_5d")),
                            "mae": safe_float(company.get("mae_10d" if horizon > 5 else "mae_5d")),
                            "success": stock_return > 0,
                            "confirmed": stock_return >= max(1.0, avg_threshold),
                        }
                    )

    def stock_follow_stats(concept_id: str, horizon: int | None) -> list[dict[str, Any]]:
        if not horizon:
            return []
        groups: dict[str, list[dict[str, Any]]] = {}
        meta: dict[str, dict[str, Any]] = {}
        for date in sorted_dates:
            concept = concept_lookup.get((concept_id, date))
            if not concept or not is_follow_trigger(concept.get("scores") or {}):
                continue
            age = max(latest_index - date_index.get(date, latest_index), 0)
            weight = 0.5 ** (age / 20)
            for company in (concept.get("cn") or {}).get("companies", []):
                value = safe_float(company.get(f"return_{horizon}d"))
                if value is None:
                    continue
                code = str(company.get("code") or "")
                if not code:
                    continue
                groups.setdefault(code, []).append(
                    {
                        "date": date,
                        "age": age,
                        "weight": weight,
                        "return": value,
                        "mfe": safe_float(company.get("mfe_10d" if horizon > 5 else "mfe_5d")),
                        "mae": safe_float(company.get("mae_10d" if horizon > 5 else "mae_5d")),
                    }
                )
                meta[code] = {
                    "code": code,
                    "name": company.get("name"),
                    "market": company.get("market"),
                    "role": company.get("role"),
                    "reason": company.get("reason"),
                }
        rows: list[dict[str, Any]] = []
        for code, items in groups.items():
            metrics = reliability_metrics(items, "return")
            rows.append(
                {
                    **meta.get(code, {"code": code}),
                    "samples": metrics.get("samples"),
                    "successes": metrics.get("successes"),
                    "raw_probability": metrics.get("raw_probability"),
                    "conservative_probability": metrics.get("conservative_probability"),
                    "validation_conservative_probability": metrics.get("validation_conservative_probability"),
                    "validation_samples": metrics.get("validation_samples"),
                    "avg_return": metrics.get("avg_return"),
                    "avg_return_after_cost": metrics.get("avg_return_after_cost"),
                    "median_return": metrics.get("median_return"),
                    "p10_return": metrics.get("p10_return"),
                    "p25_return": metrics.get("p25_return"),
                    "p75_return": metrics.get("p75_return"),
                    "p10_return_after_cost": metrics.get("p10_return_after_cost"),
                    "avg_mae": metrics.get("avg_mae"),
                    "reliability_grade": metrics.get("reliability_grade"),
                    "certainty_score": metrics.get("reliability_score"),
                }
            )
        return sorted(
            rows,
            key=lambda item: (
                safe_float(item.get("certainty_score")) or 0,
                safe_float(item.get("conservative_probability")) or 0,
                safe_float(item.get("avg_return")) or -999,
                int(item.get("samples") or 0),
            ),
            reverse=True,
        )

    def stock_recommendations(top_n: int = 30, target_horizon: int = 5) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for stock in stock_observations.values():
            code = stock.get("code")
            if not code:
                continue
            per_horizon: list[dict[str, Any]] = []
            horizon_rows = stock.get("horizon_rows") or {}
            for horizon in sorted(horizon_rows):
                events = horizon_rows.get(horizon) or []
                metrics = reliability_metrics(events, "return")
                samples = int(metrics.get("samples") or 0)
                if samples < 8:
                    continue
                per_horizon.append(
                    {
                        "horizon": horizon,
                        **metrics,
                        "certainty_score": metrics.get("reliability_score"),
                    }
                )

            if not per_horizon:
                continue
            best = sorted(
                per_horizon,
                key=lambda item: (
                    safe_float(item.get("certainty_score")) or 0,
                    safe_float(item.get("conservative_probability")) or 0,
                    safe_float(item.get("avg_return")) or -999,
                    int(item.get("samples") or 0),
                    -int(item.get("horizon") or 99),
                ),
                reverse=True,
            )[0]
            day_5 = next((item for item in per_horizon if int(item.get("horizon") or 0) == 5), None)
            if day_5 is None:
                continue
            deploy_horizon = int(best.get("horizon") or target_horizon)
            deploy_score = auto_recommendation_score(best, day_5)
            deploy_grade = reliability_grade(
                deploy_score,
                day_5.get("conservative_probability"),
                day_5.get("validation_conservative_probability"),
                int(day_5.get("samples") or 0),
                day_5.get("p10_return_after_cost"),
            )
            rows.append(
                {
                    "code": stock.get("code"),
                    "name": stock.get("name"),
                    "market": stock.get("market"),
                    "role": stock.get("role"),
                    "reason": stock.get("reason"),
                    "concept_id": stock.get("concept_id"),
                    "concept_name": stock.get("concept_name"),
                    "concept_short_name": stock.get("concept_short_name"),
                    "samples_5d": day_5.get("samples"),
                    "successes_5d": day_5.get("successes"),
                    "raw_probability_5d": day_5.get("raw_probability"),
                    "conservative_probability_5d": day_5.get("conservative_probability"),
                    "validation_probability_5d": day_5.get("validation_probability"),
                    "validation_conservative_probability_5d": day_5.get("validation_conservative_probability"),
                    "validation_samples_5d": day_5.get("validation_samples"),
                    "avg_return_5d": day_5.get("avg_return"),
                    "avg_return_after_cost_5d": day_5.get("avg_return_after_cost"),
                    "median_return_5d": day_5.get("median_return"),
                    "p10_return_5d": day_5.get("p10_return"),
                    "p10_return_after_cost_5d": day_5.get("p10_return_after_cost"),
                    "avg_mae_5d": day_5.get("avg_mae"),
                    "recommended_horizon_days": deploy_horizon,
                    "best_horizon": best.get("horizon"),
                    "best_conservative_probability": best.get("conservative_probability"),
                    "best_validation_conservative_probability": best.get("validation_conservative_probability"),
                    "best_samples": best.get("samples"),
                    "best_return": best.get("avg_return"),
                    "best_return_after_cost": best.get("avg_return_after_cost"),
                    "best_reliability_score": best.get("certainty_score"),
                    "best_reliability_grade": best.get("reliability_grade"),
                    "certainty_score": deploy_score,
                    "reliability_score_5d": day_5.get("reliability_score"),
                    "reliability_grade_5d": day_5.get("reliability_grade"),
                    "reliability_grade": deploy_grade,
                    "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
                    "horizon_stats": per_horizon,
                    "trigger_date": stock.get("latest_trigger_date") or sorted_dates[-1],
                }
            )

        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("code") or "")
            if not code:
                continue
            current = deduped.get(code)
            if current is None:
                deduped[code] = row
                continue
            current_key = (
                safe_float(current.get("certainty_score")) or 0,
                safe_float(current.get("conservative_probability_5d")) or 0,
                safe_float(current.get("validation_conservative_probability_5d")) or 0,
                safe_float(current.get("p10_return_after_cost_5d")) or -999,
                safe_float(current.get("samples_5d")) or 0,
                safe_float(current.get("avg_return_after_cost_5d")) or -999,
            )
            new_key = (
                safe_float(row.get("certainty_score")) or 0,
                safe_float(row.get("conservative_probability_5d")) or 0,
                safe_float(row.get("validation_conservative_probability_5d")) or 0,
                safe_float(row.get("p10_return_after_cost_5d")) or -999,
                safe_float(row.get("samples_5d")) or 0,
                safe_float(row.get("avg_return_after_cost_5d")) or -999,
            )
            if new_key > current_key:
                deduped[code] = row

        return sorted(
            deduped.values(),
            key=lambda row: (
                safe_float(row.get("certainty_score")) or 0,
                safe_float(row.get("conservative_probability_5d")) or 0,
                safe_float(row.get("validation_conservative_probability_5d")) or 0,
                safe_float(row.get("p10_return_after_cost_5d")) or -999,
                safe_float(row.get("samples_5d")) or 0,
                safe_float(row.get("avg_return_after_cost_5d")) or -999,
            ),
            reverse=True,
        )[:top_n]

    model_concepts: list[dict[str, Any]] = []
    for live in live_concepts:
        concept_id = str(live.get("id") or "")
        current_scores = live.get("scores") or {}
        current_activation = current_follow_activation(current_scores)
        horizon_stats: list[dict[str, Any]] = []
        for horizon in FOLLOW_HORIZONS:
            items = observations.get(concept_id, {}).get(horizon, [])
            confirmed = len([item for item in items if item.get("confirmed")])
            metrics = reliability_metrics(items, "avg_return")
            trials = int(metrics.get("samples") or 0)
            bayes_probability = (int(metrics.get("successes") or 0) + 1) / (trials + 2) if trials else None
            horizon_stats.append(
                {
                    "horizon": horizon,
                    "samples": trials,
                    "successes": metrics.get("successes"),
                    "confirmed": confirmed,
                    "raw_probability": metrics.get("raw_probability"),
                    "bayes_probability": bayes_probability,
                    "recency_probability": metrics.get("recency_probability"),
                    "conservative_probability": metrics.get("conservative_probability"),
                    "confirm_probability": confirmed / trials if trials else None,
                    "avg_return": metrics.get("avg_return"),
                    "median_return": metrics.get("median_return"),
                    "avg_return_after_cost": metrics.get("avg_return_after_cost"),
                    "p10_return": metrics.get("p10_return"),
                    "p25_return": metrics.get("p25_return"),
                    "p75_return": metrics.get("p75_return"),
                    "p90_return": metrics.get("p90_return"),
                    "p10_return_after_cost": metrics.get("p10_return_after_cost"),
                    "validation_samples": metrics.get("validation_samples"),
                    "validation_probability": metrics.get("validation_probability"),
                    "validation_conservative_probability": metrics.get("validation_conservative_probability"),
                    "train_conservative_probability": metrics.get("train_conservative_probability"),
                    "degradation_penalty": metrics.get("degradation_penalty"),
                    "reliability_grade": metrics.get("reliability_grade"),
                    "avg_win_rate": average([item.get("win_rate") for item in items]),
                    "avg_mfe": average([item.get("avg_mfe") for item in items]),
                    "avg_mae": metrics.get("avg_mae"),
                    "certainty_score": metrics.get("reliability_score"),
                }
            )
        usable_stats = [item for item in horizon_stats if int(item.get("samples") or 0) >= 3]
        best = (
            sorted(
                usable_stats,
                key=lambda item: (
                    safe_float(item.get("certainty_score")) or 0,
                    safe_float(item.get("conservative_probability")) or 0,
                    safe_float(item.get("avg_return")) or -999,
                    -int(item.get("horizon") or 99),
                ),
                reverse=True,
            )[0]
            if usable_stats
            else None
        )
        future_lock_score = (
            clamp((safe_float(best.get("certainty_score")) or 0) * 0.78 + current_activation * 0.22, 0, 100)
            if best
            else clamp(current_activation * 0.18, 0, 30)
        )
        stock_stats = stock_follow_stats(concept_id, int(best.get("horizon")) if best else None)
        model_concepts.append(
            {
                "id": concept_id,
                "name": live.get("name"),
                "short_name": live.get("short_name"),
                "underlying_driver": live.get("underlying_driver"),
                "trigger": live.get("trigger"),
                "current_activation_score": round(current_activation, 1),
                "future_lock_score": round(future_lock_score, 1),
                "best_horizon": best.get("horizon") if best else None,
                "historical_probability": best.get("raw_probability") if best else None,
                "conservative_probability": best.get("conservative_probability") if best else None,
                "validation_conservative_probability": best.get("validation_conservative_probability") if best else None,
                "validation_samples": best.get("validation_samples") if best else 0,
                "recency_probability": best.get("recency_probability") if best else None,
                "certainty_score": best.get("certainty_score") if best else 0,
                "reliability_grade": best.get("reliability_grade") if best else "样本不足",
                "samples": best.get("samples") if best else 0,
                "successes": best.get("successes") if best else 0,
                "avg_return": best.get("avg_return") if best else None,
                "avg_return_after_cost": best.get("avg_return_after_cost") if best else None,
                "median_return": best.get("median_return") if best else None,
                "p10_return_after_cost": best.get("p10_return_after_cost") if best else None,
                "avg_mae": best.get("avg_mae") if best else None,
                "avg_win_rate": best.get("avg_win_rate") if best else None,
                "verdict": follow_verdict(best, current_activation),
                "horizon_stats": horizon_stats,
                "future_cone": [
                    {
                        "day": item.get("horizon"),
                        "median_return": item.get("median_return"),
                        "p10_return": item.get("p10_return"),
                        "p25_return": item.get("p25_return"),
                        "p75_return": item.get("p75_return"),
                        "p90_return": item.get("p90_return"),
                        "raw_probability": item.get("raw_probability"),
                        "conservative_probability": item.get("conservative_probability"),
                        "validation_conservative_probability": item.get("validation_conservative_probability"),
                        "avg_return_after_cost": item.get("avg_return_after_cost"),
                        "p10_return_after_cost": item.get("p10_return_after_cost"),
                        "samples": item.get("samples"),
                    }
                    for item in horizon_stats
                ],
                "stock_stats": stock_stats[:20],
            }
        )
    ranked = sorted(
        model_concepts,
        key=lambda item: (
            safe_float(item.get("certainty_score")) or 0,
            safe_float(item.get("conservative_probability")) or 0,
            safe_float(item.get("future_lock_score")) or 0,
            int(item.get("samples") or 0),
        ),
        reverse=True,
    )
    return {
        "available": True,
        "method": "V4稳健预测：只在美股同细分板块当日上涨且存在滞后/热度时形成一次试验；收益先扣除0.35%往返交易摩擦，再对1-10日逐日计算Wilson保守下界、最近30%样本验证下界、近期权重、平均净收益、10分位尾部收益和最大不利波动。自动推荐分同时受最佳持有窗口和5日窗口约束，5日验证弱、扣费后均值不正或尾部亏损过深都会被硬性压分。",
        "rank_basis": "默认按可靠度分排序；可靠度不是上涨保证，而是样本量、保守概率、最近验证、净收益、尾部风险和当前触发强度共同通过后的研究优先级。预测名单是观察优先级，不是买入指令。",
        "horizons": list(FOLLOW_HORIZONS),
        "model_audit": {
            "version": "v4-robust-calibrated-follow",
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "validation_share": VALIDATION_SHARE,
            "validation_min_samples": VALIDATION_MIN_SAMPLES,
            "fixes": [
                "将成功定义改为扣除交易摩擦后的净收益为正",
                "加入最近样本验证下界，惩罚训练/验证退化",
                "加入10分位净收益和最大不利波动，降低高波动高收益样本的过拟合分",
                "限制分数上限，避免个股预测分大面积饱和到100",
                "自动推荐使用最佳窗口分与5日分的折中，并用5日验证下界/尾部净收益设置硬上限",
            ],
        },
        "sample_window": {
            "from": sorted_dates[0],
            "to": latest_date,
            "trading_days": len(sorted_dates),
            "latest_date": latest_date,
        },
        "concepts": ranked,
        "auto_recommendations": stock_recommendations(top_n=30, target_horizon=5),
        "auto_recommendation_horizon": 5,
        "top_concept_id": ranked[0].get("id") if ranked else None,
        "date_options": date_options,
    }


def build_backtest_payload(
    concepts: list[dict[str, Any]], proxy_quotes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    all_cn_dates = sorted(
        {
            str(row.get("date"))
            for concept in concepts
            for company in concept.get("cn", {}).get("companies", [])
            for row in company.get("candles", [])
            if row.get("date")
        }
    )
    if not all_cn_dates:
        return {"available": False, "message": "暂无A股历史K线，无法生成回测。"}

    recent_dates = all_cn_dates[-60:]
    latest_date = recent_dates[-1]
    descending_dates = list(reversed(recent_dates))
    date_options = [
        {
            "date": date,
            "label": date,
            "trading_days_ago": max(len(recent_dates) - 1 - recent_dates.index(date), 0),
        }
        for date in descending_dates
    ]
    default_date = next((item["date"] for item in date_options if item["trading_days_ago"] >= 10), date_options[0]["date"])
    snapshots: dict[str, Any] = {}
    for date in recent_dates:
        snapshot_concepts: list[dict[str, Any]] = []
        for concept in concepts:
            us_quotes = [
                quote
                for quote in (historical_us_quote(item, date) for item in concept.get("us", {}).get("tickers", []))
                if quote is not None
            ]
            cn_quotes = [
                quote
                for quote in (historical_cn_quote(item, date) for item in concept.get("cn", {}).get("companies", []))
                if quote is not None
            ]
            if not us_quotes and not cn_quotes:
                continue
            us_quotes_sorted = sorted(
                us_quotes,
                key=lambda item: item.get("change_1d") if item.get("change_1d") is not None else -999,
                reverse=True,
            )
            cn_quotes_sorted = sorted(
                cn_quotes,
                key=lambda item: item.get("buy_day_change") if item.get("buy_day_change") is not None else -999,
                reverse=True,
            )
            scores = historical_concept_scores(us_quotes_sorted, cn_quotes_sorted, concept.get("scores", {}))
            returns = [item.get("return_since") for item in cn_quotes_sorted if item.get("return_since") is not None]
            avg_event_returns = {
                f"return_{horizon}d": average([item.get(f"return_{horizon}d") for item in cn_quotes_sorted])
                for horizon in EVENT_HORIZONS
            }
            win_rates = {
                f"win_rate_{horizon}d": (
                    len([item for item in cn_quotes_sorted if safe_float(item.get(f"return_{horizon}d")) is not None and float(item.get(f"return_{horizon}d") or 0) > 0])
                    / len([item for item in cn_quotes_sorted if safe_float(item.get(f"return_{horizon}d")) is not None])
                    * 100
                    if len([item for item in cn_quotes_sorted if safe_float(item.get(f"return_{horizon}d")) is not None])
                    else None
                )
                for horizon in EVENT_HORIZONS
            }
            snapshot_concepts.append(
                {
                    "id": concept.get("id"),
                    "name": concept.get("name"),
                    "short_name": concept.get("short_name"),
                    "trigger": concept.get("trigger"),
                    "underlying_driver": concept.get("underlying_driver"),
                    "scores": scores,
                    "us": {"top_tickers": us_quotes_sorted[:5]},
                    "cn": {
                        "companies": cn_quotes_sorted,
                        "avg_return_since": average(returns),
                        "avg_event_returns": avg_event_returns,
                        "win_rates": win_rates,
                        "avg_mfe_5d": average([item.get("mfe_5d") for item in cn_quotes_sorted]),
                        "avg_mae_5d": average([item.get("mae_5d") for item in cn_quotes_sorted]),
                        "avg_mfe_10d": average([item.get("mfe_10d") for item in cn_quotes_sorted]),
                        "avg_mae_10d": average([item.get("mae_10d") for item in cn_quotes_sorted]),
                        "winners": len([value for value in returns if value is not None and value > 0]),
                        "losers": len([value for value in returns if value is not None and value < 0]),
                    },
                }
            )
        snapshots[date] = {
            "date": date,
            "latest_date": latest_date,
            "trading_days_ago": max(len(recent_dates) - 1 - recent_dates.index(date), 0),
            "concepts": sorted(snapshot_concepts, key=lambda item: item["scores"]["opportunity_score"], reverse=True),
        }
    follow_model = build_prediction_model_v6(concepts, proxy_quotes, OUTPUT)
    return {
        "available": True,
        "method": "日期回放用于查看价格路径；预测统计独立使用V7严格时点模型：美股收盘形成事件，A股下一交易日开盘才允许入场；固定5日为主终点，1-10日仅作诊断；概率采用时间顺序样本外校准、时间衰减、近期关系漂移门控和同批候选多重检验校正。历史新闻与研究快照无法回放的日期不参与证据强度重建。",
        "event_horizons": list(EVENT_HORIZONS),
        "date_options": date_options,
        "default_date": default_date,
        "latest_date": latest_date,
        "snapshots": snapshots,
        "follow_model": follow_model,
    }


def previous_scan_templates() -> list[dict[str, Any]]:
    cached = load_json(DATA_PATH, {})
    templates: list[dict[str, Any]] = []
    for concept in cached.get("concepts") or []:
        us_tickers = [
            str(item.get("symbol") or "").upper()
            for item in (concept.get("us") or {}).get("tickers") or []
            if item.get("symbol")
        ]
        cn_companies = [
            cn(
                str(item.get("code") or ""),
                str(item.get("name") or ""),
                str(item.get("role") or "上次扫描活跃成分"),
                str(item.get("reason") or "全市场板块数据源暂不可用，沿用上次成功扫描结果。"),
            )
            for item in (concept.get("cn") or {}).get("companies") or []
            if supported_a_share(str(item.get("code") or ""))
        ]
        if not us_tickers or not cn_companies:
            continue
        templates.append(
            {
                "id": concept.get("id"),
                "name": concept.get("name"),
                "short_name": concept.get("short_name"),
                "trigger": concept.get("trigger") or "沿用上次成功的全市场扫描结果。",
                "us_tickers": us_tickers,
                "keywords": concept.get("keywords") or [concept.get("short_name")],
                "news_query": concept.get("name") or concept.get("short_name") or "A股 行业热度",
                "x_query": concept.get("short_name") or "A股",
                "sources": (concept.get("us") or {}).get("sources") or [],
                "cn_companies": cn_companies,
                "driver": concept.get("underlying_driver") or "上次全市场扫描",
                "dynamic": True,
                "source_type": "full_market_previous_fallback",
                "us_mapping_quality": concept.get("us_mapping_quality") or "broad_fallback",
                "us_mapping_label": concept.get("us_mapping_label") or "沿用上次映射",
                "discovery": concept.get("discovery") or {},
            }
        )
    return templates[:FULL_MARKET_SELECTED_CONCEPTS]


def emergency_fixed_templates() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for concept in CONCEPTS:
        fallback = dict(concept)
        fallback["dynamic"] = True
        fallback["source_type"] = "full_market_emergency_fallback"
        fallback["us_mapping_quality"] = "direct"
        fallback["us_mapping_label"] = "固定供应链映射（全市场数据源失败）"
        fallback["discovery"] = {
            "method": "全市场板块数据源失败且无上次成功快照，临时显示基础研究池",
            "activated_at_shanghai": shanghai_now().strftime("%Y-%m-%d %H:%M:%S CST"),
        }
        templates.append(fallback)
    return templates


def build_dashboard() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    session = request_session()
    mapping_templates = [*CONCEPTS, *DYNAMIC_DISCOVERY_RULES]
    template_cn_codes = sorted(
        {
            company.code
            for concept in mapping_templates
            for company in concept["cn_companies"]
            if supported_a_share(company.code)
        }
    )
    template_quote_map, _ = fetch_cn_quotes(template_cn_codes)
    concept_templates, dynamic_discovery = discover_full_market_concepts(
        session,
        mapping_templates,
        template_quote_map,
        cn,
        max_concepts=FULL_MARKET_SELECTED_CONCEPTS,
        quote_fetcher=fetch_cn_quotes,
    )
    if not concept_templates:
        concept_templates = previous_scan_templates()
        dynamic_discovery["fallback"] = "previous_successful_scan"
        dynamic_discovery["selected_count"] = len(concept_templates)
    if not concept_templates:
        concept_templates = emergency_fixed_templates()
        dynamic_discovery["fallback"] = "emergency_fixed_research_pool"
        dynamic_discovery["selected_count"] = len(concept_templates)

    all_us = sorted({ticker for concept in concept_templates for ticker in concept["us_tickers"]})

    proxy_symbols = sorted(set(US_MARKET_PROXY_WEIGHTS) | set(CN_MARKET_PROXY_WEIGHTS))
    cn_index_quotes = fetch_cn_index_proxy_quotes(sorted(CN_MARKET_PROXY_WEIGHTS))
    proxy_quote_map = {
        symbol: cn_index_quotes.get(symbol) or yahoo_chart(symbol, session)
        for symbol in proxy_symbols
    }
    market_context = market_proxy_context(proxy_quote_map)
    us_quote_map = {symbol: enhance_us_quote(yahoo_chart(symbol, session)) for symbol in all_us}
    public_research_items = fetch_public_research_items(session)
    all_cn = sorted(
        {
            company.code
            for concept in concept_templates
            for company in concept["cn_companies"]
            if supported_a_share(company.code)
        }
    )
    cn_quote_map, cn_source = fetch_cn_quotes(all_cn)
    cn_spark_map = fetch_cn_sparks(all_cn)
    ibkr_public_status = load_ibkr_public_status(public_research_items)
    manual_status = manual_import_status()
    x_global_status = (
        "paid_api_enabled"
        if os.getenv("X_ENABLE_PAID_API", "").strip().lower() in {"1", "true", "yes"}
        else "disabled_free_mode"
    )

    concepts = []
    for concept in concept_templates:
        us_quotes = [us_quote_map.get(symbol, {"symbol": symbol, "ok": False, "error": "not fetched"}) for symbol in concept["us_tickers"]]
        us_quotes_sorted = sorted(
            us_quotes,
            key=lambda q: q.get("change_1d") if q.get("change_1d") is not None else -999,
            reverse=True,
        )
        cn_companies = [company for company in concept["cn_companies"] if supported_a_share(company.code)]
        cn_payloads = [
            cn_quote_payload(company, cn_quote_map.get(company.code, {}), cn_source, cn_spark_map.get(company.code))
            for company in cn_companies
        ]
        cn_payloads_sorted = sorted(cn_payloads, key=lambda q: q.get("change") if q.get("change") is not None else -999, reverse=True)
        news = fetch_news(concept, session)
        public_research = merge_research_items(
            fetch_public_research_search(concept, session),
            filter_public_research(concept, public_research_items),
        )
        x_discussion = fetch_x_discussion(concept, session)
        scores = concept_scores(
            us_quotes,
            cn_payloads,
            len(news),
            len(public_research),
            len(x_discussion.get("items") or []),
            market_context,
        )
        discovery = concept.get("discovery") or {}
        scores["market_heat_score"] = safe_float(discovery.get("heat_score"))
        scores["market_heat_rank"] = discovery.get("universe_rank")
        concepts.append(
            {
                "id": concept["id"],
                "name": concept["name"],
                "short_name": concept["short_name"],
                "trigger": concept["trigger"],
                "underlying_driver": concept.get("driver") or CONCEPT_DRIVERS.get(concept["id"], "细分供应链"),
                "keywords": concept["keywords"],
                "dynamic": bool(concept.get("dynamic")),
                "source_type": concept.get("source_type") or "full_market_scan",
                "us_mapping_quality": concept.get("us_mapping_quality") or "direct",
                "us_mapping_label": concept.get("us_mapping_label") or "细分供应链直接映射",
                "discovery": discovery,
                "scores": scores,
                "us": {
                    "tickers": us_quotes_sorted,
                    "leaders": us_quotes_sorted[:4],
                    "news": news,
                    "research": public_research,
                    "sources": concept["sources"],
                    "x_discussion": x_discussion,
                },
                "cn": {
                    "companies": cn_payloads_sorted,
                    "source": cn_source,
                    "mainboard_only": False,
                    "all_listed_boards_eligible": True,
                },
            }
        )

    concepts = sorted(concepts, key=lambda c: c["scores"]["opportunity_score"], reverse=True)
    heat_ranked = sorted(
        concepts,
        key=lambda c: safe_float(c["scores"].get("market_heat_score")) or -1,
        reverse=True,
    )
    leader_names = "、".join(c["short_name"] for c in heat_ranked[:3])
    summary = {
        "title": "美股到A股细分供应链研究预警",
        "thesis": f"本轮全市场实时热度集中在 {leader_names}。看板定位为跨市场主题预警，不把热度或机会分直接等同于买入信号；需要继续核验公告、订单、估值和成交持续性。",
        "method": "每次刷新先重扫A股全市场概念、证监会行业和新浪行业，再按当期涨跌、上涨广度、成交活跃、龙头强度和成分去重选出24个板块；A股成分不再按主板、创业板、科创板或北交所做板块排除。",
        "basis": "板块名单不沿用固定展示池。全市场候选每次重新排名，行业至少保留8席，固定细分供应链模板最多8席；成分重合达到68%的近似板块自动去重。跨市场预测只允许直接映射或行业代理，缺少明确美股对应关系的A股热点仅展示A股轮动，不生成买入型预测。",
        "score_framework": "V7把跨市场研究热度与可交易预测彻底分离：只允许使用美股收盘时已知信息，并在下一A股交易日开盘执行；完整历史按时间顺序检验，概率经先验收缩、时间衰减和样本外可靠性校准，同时检查近期关系漂移、相对大盘超额收益、交易成本、尾部风险与数据质量。当期多股票筛选再经过多重检验校正，不满足任一硬门槛时必须拒绝买入型预测。",
        "risk": "研究用途，不构成投资建议；A股映射需要继续核验公告、订单、估值和流动性。",
    }
    return {
        "model_version": "v7.0-full-market-decay-drift-fdr-20260727",
        "generated_at": now_iso(),
        "generated_at_shanghai": shanghai_now().strftime("%Y-%m-%d %H:%M:%S CST"),
        "market_clock": {
            "shanghai": shanghai_now().strftime("%Y-%m-%d %H:%M:%S"),
            "new_york": datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        },
        "market_context": market_context,
        "dynamic_discovery": dynamic_discovery,
        "audit_notes": [
            "本系统是研究预警系统，不是自动买卖预测器。",
            "V7继续严格执行跨市场时区点时约束：美股收盘后的信息只能在下一A股交易日开盘执行。",
            "V7概率结合长期后验与时间衰减后验，并显示样本外Brier增益、近期关系稳定性、交易成本与尾部风险。",
            "同一刷新时点的候选股票采用Benjamini-Hochberg校正；未通过多重检验的高分候选同样拒绝进入自动观察池。",
            "当样本、校准、超额收益、数据质量或市场状态不合格时，系统会明确拒绝预测，而不是强行生成高分股票。",
            "历史新闻、研究、社媒证据只有刷新后的快照才可严格回放；旧数据仅能复现价格路径。",
            "每次刷新都会重扫约300个A股行业与概念并重建展示名单；银行、存储、消费、白酒等均参与同一排名，不因未入选当期前24名而从扫描池消失。",
            "全市场扫描源失败时优先沿用上次成功快照并显式标记降级；只有首次运行且无快照时才使用基础研究池兜底。",
        ],
        "connectors": {
            "ibkr": ibkr_public_status,
            "manual_import": manual_status,
            "us_quotes": {"status": "connected", "source": "Yahoo chart API with direct no-proxy session"},
            "us_news": {"status": "connected", "source": "Google News RSS + IBKR Campus public RSS"},
            "x": {"status": x_global_status},
            "cn_quotes": {"status": "connected" if cn_quote_map else "fallback", "source": cn_source},
        },
        "summary": summary,
        "concepts": concepts,
        "backtest": build_backtest_payload(concepts, proxy_quote_map),
    }


def main() -> int:
    payload = build_dashboard()
    follow_model = payload.get("backtest", {}).get("follow_model") or {}
    if follow_model.get("available"):
        follow_model["forward_validation"] = update_prediction_ledger(payload, OUTPUT)
    write_json(DATA_PATH, payload)
    DATA_JS_PATH.write_text(
        "window.__MARKET_LAG_DASHBOARD__ = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(DATA_PATH)
    print(payload["summary"]["thesis"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
