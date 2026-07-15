"""BusinessMonitorAgent 规则引擎测试。

历史缺陷:核心检查方法是占位实现,固定返回 (False, 0.0)——
该 Agent 永远不会产生任何业务告警,四个纯业务故障场景(fs_biz_007~010)
跑完也永远"未检测到异常"。
本文件锁定修复后的行为:规则按业务事件数据(context 注入的真实事件,
或 BUSINESS_EVENTS 静态数据集)做确定性检查。
"""

from __future__ import annotations

import pytest

from app.agents.business_monitor_agent import BusinessMetricInput, BusinessMonitorAgent
from app.models.agent import AgentExecutionContext


def _ctx() -> AgentExecutionContext:
    return AgentExecutionContext(incident_id="biz-test", input_data={})


def _scenario_input(scenario_id: str, rule_id: str, service: str) -> BusinessMetricInput:
    return BusinessMetricInput(
        service_name=service,
        business_domain="financial",
        check_rules=[rule_id],
        context={"scenario_id": scenario_id},
    )


SCENARIO_CASES = [
    ("fs_biz_007", "br_duplicate_charge", "payment-service"),
    ("fs_biz_008", "br_oversell", "inventory-service"),
    ("fs_biz_009", "br_amount_mismatch", "order-service"),
    ("fs_biz_010", "br_payment_callback_loss", "payment-service"),
]


class TestScenarioDetection:
    """四个纯业务故障场景均能被对应规则检出。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario_id,rule_id,service", SCENARIO_CASES)
    async def test_scenario_triggers_rule(
        self, scenario_id: str, rule_id: str, service: str
    ) -> None:
        agent = BusinessMonitorAgent()
        result = await agent.process(
            _scenario_input(scenario_id, rule_id, service), _ctx()
        )

        assert result.success
        anomaly = result.output_data["anomaly_result"]
        assert anomaly["is_anomaly"] is True
        assert anomaly["rules_matched"] == 1
        matched = [r for r in anomaly["results"] if r["matched"]]
        assert matched[0]["rule_id"] == rule_id
        assert matched[0]["confidence"] > 0.5
        assert result.output_data["alert_generated"] is True
        assert result.output_data["alerts"][0]["metric"] == f"business_{rule_id}"


class TestNormalData:
    @pytest.mark.asyncio
    async def test_normal_dataset_matches_nothing(self) -> None:
        """无场景上下文时走 normal 数据集,全部 8 条规则均不误报。"""
        agent = BusinessMonitorAgent()
        result = await agent.process(
            BusinessMetricInput(
                service_name="payment-service",
                business_domain="",
                check_rules=[],
            ),
            _ctx(),
        )

        assert result.success
        anomaly = result.output_data["anomaly_result"]
        assert anomaly["rules_checked"] == 8
        assert anomaly["is_anomaly"] is False
        assert result.output_data["alert_generated"] is False


class TestInjectedEvents:
    @pytest.mark.asyncio
    async def test_caller_supplied_events_take_priority(self) -> None:
        """调用方经 context['business_events'] 传入的真实事件优先于静态数据集。"""
        agent = BusinessMonitorAgent()
        events = {
            "coupon_usage": [
                {"coupon_id": "cp_777", "user_id": "u_9", "used_at": "2026-07-06T10:00:00+00:00"},
                {"coupon_id": "cp_777", "user_id": "u_9", "used_at": "2026-07-06T10:01:00+00:00"},
                {"coupon_id": "cp_777", "user_id": "u_9", "used_at": "2026-07-06T10:02:00+00:00"},
            ],
        }
        result = await agent.process(
            BusinessMetricInput(
                service_name="order-service",
                business_domain="financial",
                check_rules=["br_coupon_abuse"],
                context={"business_events": events},
            ),
            _ctx(),
        )

        anomaly = result.output_data["anomaly_result"]
        assert anomaly["is_anomaly"] is True
        assert anomaly["results"][0]["rule_id"] == "br_coupon_abuse"
        assert anomaly["results"][0]["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_missing_data_source_reports_no_match(self) -> None:
        """所需数据源缺失的规则(如 auth_logs)如实返回未命中,不伪造结论。"""
        agent = BusinessMonitorAgent()
        result = await agent.process(
            BusinessMetricInput(
                service_name="user-service",
                business_domain="user",
                check_rules=["br_user_permission_error"],
            ),
            _ctx(),
        )

        anomaly = result.output_data["anomaly_result"]
        assert anomaly["is_anomaly"] is False
        assert anomaly["results"][0]["confidence"] == 0.0
