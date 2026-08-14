#!/usr/bin/env python3
from __future__ import annotations

import unittest

from full_market_sector_discovery import (
    candidate_from_constituents,
    overlaps_selected,
    parse_board_payload,
    quote_amount,
    us_mapping,
)


class FullMarketSectorDiscoveryTests(unittest.TestCase):
    def test_sina_board_columns_are_parsed_by_schema(self) -> None:
        payload = {
            "gn_memory": "gn_memory,存储芯片,35,20.1,0.8,4.15,123456,987654321,sh600000,9.9,12.0,1.1,测试龙头"
        }
        rows = parse_board_payload(
            payload,
            {"key": "sina_concept", "label": "新浪概念"},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["board_label"], "gn_memory")
        self.assertEqual(rows[0]["board_name"], "存储芯片")
        self.assertEqual(rows[0]["company_count"], 35)
        self.assertEqual(rows[0]["board_change_pct"], 4.15)

    def test_near_duplicate_constituent_sets_are_removed(self) -> None:
        selected = [
            {
                "board_name": "货币金融服务",
                "constituents": [{"code": f"60000{idx}"} for idx in range(6)],
            }
        ]
        candidate = {
            "board_name": "银行行业",
            "constituents": [{"code": f"60000{idx}"} for idx in range(5)] + [{"code": "601999"}],
        }
        self.assertTrue(overlaps_selected(candidate, selected))

    def test_expected_sectors_have_explicit_us_proxies(self) -> None:
        for name in ("货币金融服务", "白酒概念", "存储芯片", "商业百货"):
            with self.subTest(name=name):
                self.assertEqual(us_mapping(name)["quality"], "sector_proxy")
        self.assertEqual(us_mapping("海上丝路")["quality"], "broad_fallback")

    def test_tencent_turnover_ratio_is_not_mistaken_for_traded_value(self) -> None:
        quote = {"now": 10, "volume": 1_000_000, "成交额(万)": 10_000_000, "turnover": 0.35}
        self.assertEqual(quote_amount(quote), 10_000_000)

    def test_board_requires_three_constituents_with_real_traded_value(self) -> None:
        row = {"board_name": "测试行业"}
        incomplete = [
            {"code": f"60000{idx}", "change_pct": 1.0, "amount": 0}
            for idx in range(4)
        ]
        complete = [
            {"code": f"60000{idx}", "change_pct": float(idx - 1), "amount": 1_000_000}
            for idx in range(4)
        ]
        self.assertIsNone(candidate_from_constituents(row, incomplete))
        self.assertIsNotNone(candidate_from_constituents(row, complete))


if __name__ == "__main__":
    unittest.main()
