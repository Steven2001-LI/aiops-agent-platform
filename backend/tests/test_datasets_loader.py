"""Golden 数据文件加载器测试:外置 JSON 与模块常量语义等价"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.data.datasets import (
    BUSINESS_EVENTS,
    CHANGE_RECORDS,
    FAULT_SCENARIOS,
    METRICS_DATASETS,
    add_fault_scenario,
    get_business_events,
    get_change_records,
    get_fault_scenario,
    get_metric_data,
)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "golden"


class TestGoldenFiles:
    def test_golden_files_exist_and_loadable(self) -> None:
        """4 个 golden 文件存在、可解析、顶层类型正确"""
        expected_types = {
            "metrics_datasets": dict,
            "fault_scenarios": list,
            "change_records": list,
            "business_events": dict,
        }
        for name, expected_type in expected_types.items():
            path = GOLDEN_DIR / f"{name}.json"
            assert path.exists(), f"missing {path}"
            with open(path, encoding="utf-8") as f:
                assert isinstance(json.load(f), expected_type)

    def test_metrics_shape(self) -> None:
        assert len(METRICS_DATASETS) == 5
        for service, metrics in METRICS_DATASETS.items():
            assert "normal" in metrics.get("cpu_usage_percent", {}), service
            for scenarios in metrics.values():
                for values in scenarios.values():
                    assert all(isinstance(v, (int, float)) for v in values)

    def test_fault_scenarios_shape(self) -> None:
        assert len(FAULT_SCENARIOS) == 15
        ids = [s["id"] for s in FAULT_SCENARIOS]
        assert len(set(ids)) == 15
        for scenario in FAULT_SCENARIOS:
            for key in ("id", "service", "category", "root_cause", "expected_action", "severity"):
                assert key in scenario, f"{scenario['id']} missing {key}"

    def test_business_events_fs_biz_008_expanded(self) -> None:
        """fs_biz_008 的列表推导已展开为静态记录:10 条订单,偶数 paid 奇数 processing"""
        events = BUSINESS_EVENTS["fs_biz_008"]
        orders = events["orders"]
        assert len(orders) == 10
        for i, order in enumerate(orders):
            expected_status = "paid" if i % 2 == 0 else "processing"
            assert order["status"] == expected_status, order
        products = events["products"]
        assert any(p.get("stock") == 3 for p in products)

    def test_change_records_relative_timestamps(self) -> None:
        """minutes_ago 偏移换算成相对当前的时间戳;order-service deployment 在 60 分钟窗内"""
        assert len(CHANGE_RECORDS) == 5
        now = datetime.now(timezone.utc)
        for record in CHANGE_RECORDS:
            assert "minutes_ago" not in record
            ts = datetime.fromisoformat(record["timestamp"])
            assert ts <= now
        order_deploys = [
            r for r in get_change_records("order-service") if r["type"] == "deployment"
        ]
        assert order_deploys
        ts = datetime.fromisoformat(order_deploys[0]["timestamp"])
        assert now - ts < timedelta(minutes=60)


class TestAccessors:
    def test_accessor_fallbacks(self) -> None:
        assert get_metric_data("no-such-service", "cpu_usage_percent") == [50.0] * 10
        assert get_metric_data("order-service", "cpu_usage_percent", "no-such-scenario") == \
            METRICS_DATASETS["order-service"]["cpu_usage_percent"]["normal"]
        assert get_business_events("no-such-scenario") == BUSINESS_EVENTS["normal"]
        assert get_fault_scenario("no-such-id") is None

    def test_constant_identity_and_mutability(self) -> None:
        """from-import 绑定同一共享对象;add_* 的进程内可变语义保持"""
        from app.data import datasets

        assert datasets.FAULT_SCENARIOS is FAULT_SCENARIOS
        marker = {"id": "fs_test_identity", "service": "t", "category": "t",
                  "root_cause": "t", "expected_action": {"type": "t"}, "severity": "low"}
        add_fault_scenario(marker)
        try:
            assert get_fault_scenario("fs_test_identity") is marker
        finally:
            FAULT_SCENARIOS.remove(marker)
