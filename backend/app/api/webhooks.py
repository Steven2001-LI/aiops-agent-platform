"""
AIOps Agent Platform — Webhook Receivers

接收外部系统的告警推送，转换为内部告警事件并触发 Agent 处理管道。

支持的 webhook 来源：
- AlertManager (Prometheus 生态)
- 可扩展：PagerDuty / Grafana / 自定义 webhook
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.models.events import AlertEvent, SeverityLevel
from app.models.incident import Incident, IncidentState
from app.models.memory import MemoryType
from app.services.incident_service import IncidentService
from app.utils.logging import get_logger

logger = get_logger(__name__)

webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])

# 全局 IncidentService（与 routes.py 共享存储）
_incident_service = IncidentService()


# =============================================================================
# 记忆系统（惰性初始化，失败不影响管道）
# =============================================================================

async def _get_ms():
    """获取 MemorySystem 单例。"""
    try:
        from app.memory.core import MemorySystem as MS
        return await MS.get_instance()
    except Exception:
        return None


async def _store_memory(
    content: str,
    agent_name: str,
    incident_id: str,
    importance: float = 0.5,
    memory_type: MemoryType = MemoryType.OBSERVATION,
    tags: list[str] | None = None,
) -> None:
    """存储 Agent 执行结果到记忆系统，失败静默忽略。"""
    try:
        ms = await _get_ms()
        if ms is None:
            return
        await ms.store(
            content=content,
            memory_type=memory_type,
            source_agent=agent_name,
            source_incident_id=incident_id,
            importance=importance,
            tags=tags or [],
            session_id=incident_id,
        )
    except Exception:
        pass


async def _store_wm(incident_id: str, key: str, value: Any) -> None:
    """存储工作记忆。"""
    try:
        ms = await _get_ms()
        if ms is None:
            return
        await ms.working_set(key=key, value=value, incident_id=incident_id)
    except Exception:
        pass


# =============================================================================
# 内联 Agent 管道（与 routes.py 保持一致的逻辑）
# =============================================================================

async def _run_agent_pipeline(incident: Incident) -> None:
    """
    执行 Agent 处理管道，每步完成后存入记忆系统。

    与 routes.py 中的 _process_incident_pipeline 逻辑一致。
    """
    from app.agents.base import AgentExecutionContext
    from app.agents.change_agent import ChangeAgent, ChangeInput
    from app.agents.heal_agent import HealAgent, HealInput
    from app.agents.monitor_agent import MetricInput, MonitorAgent
    from app.agents.rca_agent import RCAAgent, RCAInput
    from app.api.websocket import manager as ws_manager

    ctx = AgentExecutionContext(
        incident_id=incident.incident_id,
        input_data={"source": "alertmanager-webhook"},
    )

    try:
        alert = incident.alert_event
        if not alert:
            return

        # Step 1: Monitor
        monitor = MonitorAgent()
        metric = MetricInput(
            metric_name=alert.metric,
            metric_value=alert.value,
            service_name=alert.service,
            labels=alert.labels,
        )
        incident.transition_to(IncidentState.ANALYZING, actor="webhook")
        monitor_result = await monitor.process(metric, ctx)

        # → 记忆：异常检测结果
        await _store_memory(
            content=(
                f"[MonitorAgent] {alert.service} {alert.metric}={alert.value}. "
                f"Severity: {alert.severity.value}. "
                f"Source: alertmanager-webhook."
            ),
            agent_name="monitor_agent",
            incident_id=incident.incident_id,
            importance=0.85,
            memory_type=MemoryType.OBSERVATION,
            tags=["monitor", "alertmanager", alert.service, alert.metric],
        )

        # Step 2: RCA
        incident.transition_to(IncidentState.RCA_IN_PROGRESS, actor="webhook")
        rca = RCAAgent()
        rca_input = RCAInput(alert=alert, incident_id=incident.incident_id)
        rca_result = await rca.process(rca_input, ctx)

        # → 记忆：根因分析
        if rca_result.success:
            root_cause = rca_result.output_data.get("root_cause", "unknown")
            await _store_memory(
                content=(
                    f"[RCAAgent] Root cause: {root_cause}. "
                    f"Confidence: {rca_result.output_data.get('confidence', 0):.2f}. "
                    f"Impact: {rca_result.output_data.get('impact_services_count', 0)} services."
                ),
                agent_name="rca_agent",
                incident_id=incident.incident_id,
                importance=0.85,
                memory_type=MemoryType.EPISODIC,
                tags=["rca", "root_cause", alert.service],
            )
            await _store_wm(incident.incident_id, "rca.result", {
                "root_cause": root_cause,
                "confidence": rca_result.output_data.get("confidence", 0),
            })

        # Step 3: Heal
        if rca_result.success:
            from app.models.events import RCAEvent
            rca_event_data = rca_result.output_data.get("rca_event", {})
            rca_event = RCAEvent(**rca_event_data) if rca_event_data else None
            if rca_event:
                incident.transition_to(IncidentState.HEALING, actor="webhook")
                heal = HealAgent()
                heal_input = HealInput(rca_event=rca_event, incident_id=incident.incident_id)
                heal_result = await heal.process(heal_input, ctx)

                # → 记忆：自愈方案
                if heal_result.success:
                    await _store_memory(
                        content=(
                            f"[HealAgent] Playbook: {heal_result.output_data.get('playbook_matched', 'none')}. "
                            f"Level: {heal_result.output_data.get('heal_level', 'L0')}. "
                            f"Actions: {heal_result.output_data.get('actions_count', 0)}."
                        ),
                        agent_name="heal_agent",
                        incident_id=incident.incident_id,
                        importance=0.7,
                        memory_type=MemoryType.PROCEDURAL,
                        tags=["heal", "playbook", alert.service],
                    )

                # Step 4: Change
                if heal_result.success:
                    from app.models.events import HealEvent
                    heal_event_data = heal_result.output_data.get("heal_event", {})
                    heal_event = HealEvent(**heal_event_data) if heal_event_data else None
                    if heal_event:
                        incident.transition_to(IncidentState.AWAITING_APPROVAL, actor="webhook")
                        change = ChangeAgent()
                        change_input = ChangeInput(heal_event=heal_event, incident_id=incident.incident_id)
                        change_result = await change.process(change_input, ctx)

                        # → 记忆：审批决策
                        if change_result.success:
                            await _store_memory(
                                content=(
                                    f"[ChangeAgent] Risk: {change_result.output_data.get('risk_score', 0):.3f} "
                                    f"({change_result.output_data.get('risk_level', 'low')}). "
                                    f"Approval: {change_result.output_data.get('approval_status', 'pending')}."
                                ),
                                agent_name="change_agent",
                                incident_id=incident.incident_id,
                                importance=0.6,
                                memory_type=MemoryType.EPISODIC,
                                tags=["change", "approval", alert.service],
                            )

        incident.transition_to(IncidentState.RESOLVED, actor="webhook")

        # → 归档工作记忆
        try:
            ms = await _get_ms()
            if ms is not None:
                await ms.flow_wm_to_ltm(incident.incident_id)
        except Exception:
            pass

        await ws_manager.broadcast({
            "type": "incident_update",
            "payload": {
                "incident_id": incident.incident_id,
                "state": incident.state.value,
                "title": incident.title,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        logger.error("Webhook pipeline error", incident_id=incident.incident_id, error=str(e))
        incident.transition_to(IncidentState.ESCALATED, actor="webhook-error")


# =============================================================================
# AlertManager Webhook
# =============================================================================

@webhook_router.post("/alertmanager")
async def alertmanager_webhook(request: Request) -> dict[str, Any]:
    """
    接收 AlertManager 告警推送。

    AlertManager 发送的 JSON 格式:
    {
      "version": "4",
      "groupKey": "...",
      "status": "firing" | "resolved",
      "receiver": "aiops-webhook",
      "alerts": [
        {
          "status": "firing",
          "labels": {
            "alertname": "HighCPUUsage",
            "severity": "critical",
            "instance": "host-machine",
            "category": "infrastructure"
          },
          "annotations": {
            "summary": "CPU 使用率过高",
            "description": "CPU 使用率已超过 95%...",
            "service": "host-machine",
            "metric": "cpu_usage_percent",
            "runbook_url": "http://..."
          },
          "startsAt": "2024-01-15T10:30:00Z",
          "endsAt": "0001-01-01T00:00:00Z",
          "generatorURL": "http://prometheus:9090/..."
        }
      ]
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        )

    alerts = body.get("alerts", [])
    if not alerts:
        logger.info("AlertManager webhook received with no alerts")
        return {"status": "ok", "alerts_processed": 0}

    processed = 0
    incidents_created: list[str] = []

    for alert in alerts:
        alert_status = alert.get("status", "firing")
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        # 解析严重级别
        severity_str = labels.get("severity", "warning")
        severity_map = {
            "critical": SeverityLevel.CRITICAL,
            "high": SeverityLevel.HIGH,
            "warning": SeverityLevel.MEDIUM,
            "medium": SeverityLevel.MEDIUM,
            "low": SeverityLevel.LOW,
            "info": SeverityLevel.INFO,
        }
        severity = severity_map.get(severity_str, SeverityLevel.MEDIUM)

        # 提取服务名和指标名
        service = annotations.get("service", labels.get("instance", "unknown"))
        metric = annotations.get("metric", labels.get("alertname", "unknown"))

        # 提取指标值（从 description 中解析百分比或数值）
        description = annotations.get("description", "")
        alert_value = _extract_value_from_description(description)

        # 仅处理 firing 状态的告警
        if alert_status == "firing":
            try:
                alert_event = AlertEvent(
                    source="alertmanager",
                    service=service,
                    metric=metric,
                    value=alert_value,
                    threshold=80.0,  # 默认阈值
                    operator=">",
                    severity=severity,
                    labels={
                        "alertname": labels.get("alertname", ""),
                        "instance": labels.get("instance", ""),
                        "category": labels.get("category", ""),
                        "tier": "critical" if severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH) else "standard",
                    },
                    annotations={
                        "summary": annotations.get("summary", ""),
                        "description": description,
                        "generator_url": alert.get("generatorURL", ""),
                        "alertmanager_group": body.get("groupKey", ""),
                        "source": "prometheus-alertmanager",
                    },
                )

                # 创建 Incident 并触发 Agent 管道
                incident = Incident.from_alert(alert_event)
                incident.transition_to(IncidentState.ACKNOWLEDGED, actor="alertmanager-webhook")
                _incident_service._incidents[incident.incident_id] = incident

                # 异步后台处理（不阻塞 webhook 响应）
                asyncio.create_task(_run_agent_pipeline(incident))

                incidents_created.append(incident.incident_id)
                processed += 1

                logger.info(
                    "AlertManager alert converted to incident",
                    alertname=labels.get("alertname"),
                    severity=severity_str,
                    incident_id=incident.incident_id,
                )

            except Exception as e:
                logger.error(
                    "Failed to process AlertManager alert",
                    alertname=labels.get("alertname"),
                    error=str(e),
                )

        elif alert_status == "resolved":
            logger.info(
                "Alert resolved",
                alertname=labels.get("alertname"),
                instance=labels.get("instance"),
            )

    return {
        "status": "ok",
        "alerts_received": len(alerts),
        "alerts_processed": processed,
        "incidents_created": incidents_created,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@webhook_router.get("/alertmanager/health")
async def alertmanager_health() -> dict[str, str]:
    """AlertManager webhook 健康检查"""
    return {"status": "healthy", "receiver": "aiops-webhook"}


# =============================================================================
# Helper
# =============================================================================

def _extract_value_from_description(description: str) -> float:
    """
    从告警描述中提取数值。

    支持格式：
    - "当前值 95.2%"
    - "has a value of 3.5"
    - "超过 85%"
    """
    import re

    # 尝试匹配百分比
    pct_match = re.search(r"(\d+\.?\d*)\s*%", description)
    if pct_match:
        return float(pct_match.group(1))

    # 尝试匹配 "value X"
    val_match = re.search(r"value\s+(\d+\.?\d*)", description)
    if val_match:
        return float(val_match.group(1))

    # 尝试匹配任意数字
    num_match = re.search(r"(\d+\.?\d+)", description)
    if num_match:
        return float(num_match.group(1))

    return 0.0
