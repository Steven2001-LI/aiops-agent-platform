"""
AIOps Agent Platform - RCA Agent

根因分析 Agent，负责故障根因分析。
集成知识图谱查询、贝叶斯推理、BFS 遍历和 RAG 增强。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from app.agents.base import AgentResult, BaseAgent
from app.data.knowledge_base import KNOWLEDGE_BASE, SERVICE_TOPOLOGY
from app.models.agent import AgentExecutionContext
from app.models.events import AlertEvent, RCAEvent, SeverityLevel
from app.models.memory import MemoryType
from app.utils.logging import get_logger

logger = get_logger(__name__)


class BayesianNode(BaseModel):
    """贝叶斯网络节点"""
    name: str = Field(description="节点名称")
    prior: float = Field(default=0.5, ge=0.0, le=1.0, description="先验概率 P(根因)")
    likelihood: float = Field(default=0.5, ge=0.0, le=1.0, description="似然 P(症状|根因)")
    posterior: float = Field(default=0.0, ge=0.0, le=1.0, description="后验概率 P(根因|症状)")
    evidence_strength: float = Field(default=0.5, ge=0.0, le=1.0, description="证据强度")


class TopologyEdge(BaseModel):
    """拓扑依赖边"""
    source: str = Field(description="源服务")
    target: str = Field(description="目标服务")
    relation: str = Field(default="depends_on", description="关系类型")
    weight: float = Field(default=1.0, description="依赖权重")


class ServiceImpact(BaseModel):
    """服务影响分析结果"""
    service: str = Field(description="服务名称")
    impact_level: str = Field(default="unknown", description="影响级别")
    hop_distance: int = Field(default=-1, description="距离告警服务的跳数")
    tier: str = Field(default="standard", description="服务等级")
    related_changes: list[dict[str, Any]] = Field(default_factory=list, description="相关变更")


class RCAInput(BaseModel):
    """RCA Agent 输入"""
    alert: AlertEvent = Field(description="告警事件")
    incident_id: str = Field(default="", description="关联故障ID")
    lookback_minutes: int = Field(default=60, description="回溯时间窗口(分钟)")
    max_hops: int = Field(default=3, description="BFS最大跳数")


class RCAAgent(BaseAgent[RCAInput, RCAEvent]):
    """
    根因分析 Agent

    职责：
    - 知识图谱查询（服务拓扑依赖关系）
    - 贝叶斯推理（P(根因|症状) = P(症状|根因) * P(根因) / P(症状)）
    - BFS 遍历依赖链（从告警服务出发）
    - RAG 增强（从知识库检索历史相似故障案例）
    """

    # 贝叶斯推理先验概率表
    PRIOR_PROBABILITIES: dict[str, float] = {
        "recent_deployment": 0.15,
        "configuration_change": 0.10,
        "resource_exhaustion": 0.20,
        "dependency_failure": 0.25,
        "network_issue": 0.15,
        "database_issue": 0.20,
        "code_bug": 0.12,
        "traffic_spike": 0.18,
        "hardware_failure": 0.05,
        "third_party_issue": 0.08,
    }

    # 似然概率表 P(症状|根因)
    LIKELIHOODS: dict[str, dict[str, float]] = {
        "recent_deployment": {
            "high_cpu": 0.7,
            "high_memory": 0.5,
            "high_error_rate": 0.6,
            "increased_latency": 0.8,
            "timeout": 0.4,
        },
        "configuration_change": {
            "high_cpu": 0.4,
            "high_error_rate": 0.7,
            "increased_latency": 0.6,
            "timeout": 0.5,
        },
        "resource_exhaustion": {
            "high_cpu": 0.8,
            "high_memory": 0.9,
            "oom_killed": 0.85,
            "timeout": 0.5,
        },
        "dependency_failure": {
            "high_error_rate": 0.9,
            "timeout": 0.85,
            "increased_latency": 0.8,
        },
        "network_issue": {
            "timeout": 0.9,
            "high_error_rate": 0.6,
            "increased_latency": 0.85,
        },
        "database_issue": {
            "timeout": 0.8,
            "high_error_rate": 0.7,
            "increased_latency": 0.9,
        },
        "code_bug": {
            "high_cpu": 0.5,
            "high_memory": 0.6,
            "high_error_rate": 0.8,
        },
        "traffic_spike": {
            "high_cpu": 0.9,
            "high_memory": 0.6,
            "increased_latency": 0.7,
        },
        "hardware_failure": {
            "high_cpu": 0.4,
            "high_error_rate": 0.6,
            "timeout": 0.5,
        },
        "third_party_issue": {
            "high_error_rate": 0.7,
            "timeout": 0.6,
            "increased_latency": 0.5,
        },
    }

    def __init__(self) -> None:
        super().__init__()
        self._service_topology = SERVICE_TOPOLOGY
        self._knowledge_base = KNOWLEDGE_BASE
        self._memory_system = None
        self._memory_init_attempted = False

    async def _get_memory_system(self):
        """惰性初始化 MemorySystem，失败后不再重试。"""
        if self._memory_system is not None:
            return self._memory_system
        if self._memory_init_attempted:
            return None
        try:
            from app.memory.core import MemorySystem as MS
            self._memory_system = await MS.get_instance()
            logger.info("MemorySystem initialized for RCA agent")
        except Exception as e:
            self._memory_init_attempted = True
            logger.warning("MemorySystem unavailable for RCA agent, using static knowledge base only", error=str(e))
        return self._memory_system

    def get_name(self) -> str:
        return "rca_agent"

    def get_description(self) -> str:
        return "根因分析 Agent - 知识图谱+贝叶斯推理+BFS遍历+RAG增强"

    # ==================== 核心处理 ====================

    async def process(
        self,
        input_data: RCAInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        执行根因分析

        Args:
            input_data: RCA 输入
            context: 执行上下文

        Returns:
            AgentResult: 包含 RCAEvent 的结果
        """
        alert = input_data.alert
        logger.info(
            "RCAAgent processing",
            service=alert.service,
            metric=alert.metric,
            incident_id=input_data.incident_id,
        )

        # Step 1: BFS 遍历服务依赖链
        impact_chain = self.bfs_traverse(
            alert.service,
            max_hops=input_data.max_hops,
        )

        # Step 2: 贝叶斯推理
        symptoms = self._extract_symptoms(alert)
        bayesian_results = self.bayesian_inference(symptoms)

        # Step 3: RAG 增强 - 从静态知识库检索
        rag_results = self._rag_retrieve(alert, symptoms)

        # Step 3a: 记忆增强 - 从 MemorySystem 检索历史相似故障
        memory_results = await self._search_historical_incidents(alert, symptoms)

        # Step 4: 综合分析 - 合并贝叶斯 + RAG + 历史记忆
        root_cause, confidence, evidence = self._synthesize_analysis(
            bayesian_results, rag_results, impact_chain, alert,
            memory_results=memory_results,
        )

        # Step 5: 生成建议操作
        suggested_actions = self._generate_suggested_actions(
            root_cause, impact_chain, rag_results
        )

        # 构建 RCAEvent
        rca_event = RCAEvent(
            correlation_id=alert.correlation_id,
            source=self.get_name(),
            incident_id=input_data.incident_id,
            root_cause=root_cause,
            confidence=confidence,
            impact_chain=[i.service for i in impact_chain],
            contributing_factors=[r.name for r in bayesian_results[:3]],
            evidence={
                "bayesian_top_causes": [
                    {"cause": r.name, "posterior": round(r.posterior, 4)}
                    for r in bayesian_results[:5]
                ],
                "rag_matches": [
                    {"id": m["id"], "category": m["category"], "confidence_boost": m.get("confidence_boost", 0)}
                    for m in rag_results[:3]
                ],
                "impact_chain_detail": [
                    {
                        "service": i.service,
                        "hop": i.hop_distance,
                        "tier": i.tier,
                        "impact_level": i.impact_level,
                    }
                    for i in impact_chain
                ],
                "alert_metric": alert.metric,
                "alert_value": alert.value,
                "alert_threshold": alert.threshold,
            },
            recommended_actions=suggested_actions,
            time_range_start=datetime.now(timezone.utc) - timedelta(minutes=input_data.lookback_minutes),
            time_range_end=datetime.now(timezone.utc),
        )

        output_data = {
            "rca_event": rca_event.model_dump(),
            "root_cause": root_cause,
            "confidence": confidence,
            "bayesian_results": [
                {"name": r.name, "prior": r.prior, "likelihood": r.likelihood, "posterior": r.posterior}
                for r in bayesian_results[:5]
            ],
            "impact_services_count": len(impact_chain),
            "rag_match_count": len(rag_results),
        }

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data=output_data,
        )

    # ==================== BFS 依赖遍历 ====================

    def bfs_traverse(
        self,
        start_service: str,
        max_hops: int = 3,
    ) -> list[ServiceImpact]:
        """
        BFS 遍历服务依赖链

        从告警服务出发，查找上游依赖和下游影响。

        Args:
            start_service: 起始服务名称
            max_hops: 最大跳数

        Returns:
            list[ServiceImpact]: 影响链路中的服务列表
        """
        if start_service not in self._service_topology:
            logger.warning(
                "Service not found in topology",
                service=start_service,
            )
            return [self._create_impact_entry(start_service, 0)]

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        results: list[ServiceImpact] = []

        queue.append((start_service, 0))
        visited.add(start_service)

        while queue:
            service, hop = queue.popleft()

            if hop > max_hops:
                continue

            impact = self._create_impact_entry(service, hop)

            # 查找相关变更（模拟）
            impact.related_changes = self._find_recent_changes(service)

            # 根据跳数确定影响级别
            if hop == 0:
                impact.impact_level = "direct"
            elif hop <= 2:
                impact.impact_level = "indirect"
            else:
                impact.impact_level = "peripheral"

            results.append(impact)

            # 遍历上游依赖（可能导致问题的服务）
            svc_info = self._service_topology.get(service, {})
            for dep in svc_info.get("dependencies", []):
                if dep not in visited:
                    visited.add(dep)
                    queue.append((dep, hop + 1))

            # 遍历下游依赖（被影响的服务）
            for dep in svc_info.get("dependents", []):
                if dep not in visited:
                    visited.add(dep)
                    queue.append((dep, hop + 1))

        # 按跳数排序
        results.sort(key=lambda x: x.hop_distance)

        logger.info(
            "BFS traversal completed",
            start_service=start_service,
            services_found=len(results),
            max_hops=max_hops,
        )

        return results

    def _create_impact_entry(self, service: str, hop: int) -> ServiceImpact:
        """创建影响条目"""
        svc_info = self._service_topology.get(service, {})
        return ServiceImpact(
            service=service,
            hop_distance=hop,
            tier=svc_info.get("tier", "standard"),
        )

    def _find_recent_changes(self, service: str) -> list[dict[str, Any]]:
        """
        查找服务的近期变更（模拟数据）

        在生产环境中，这应查询 CMDB/发布系统。
        """
        # 模拟变更数据
        changes: dict[str, list[dict[str, Any]]] = {
            "order-service": [
                {"type": "deployment", "time": "2024-01-15T10:30:00Z", "version": "v2.3.1"},
            ],
            "payment-service": [
                {"type": "config_change", "time": "2024-01-15T09:00:00Z", "description": "connection pool size"},
            ],
            "mysql-primary": [
                {"type": "maintenance", "time": "2024-01-14T22:00:00Z", "description": "index optimization"},
            ],
        }
        return changes.get(service, [])

    # ==================== 贝叶斯推理 ====================

    def bayesian_inference(
        self,
        symptoms: list[str],
    ) -> list[BayesianNode]:
        """
        贝叶斯推理

        计算 P(根因|症状) = P(症状|根因) * P(根因) / P(症状)

        Args:
            symptoms: 症状列表

        Returns:
            list[BayesianNode]: 按后验概率排序的根因列表
        """
        nodes: list[BayesianNode] = []

        for cause_name in self.PRIOR_PROBABILITIES:
            prior = self.PRIOR_PROBABILITIES[cause_name]

            # 计算似然 P(症状|根因)
            symptom_likelihoods = self.LIKELIHOODS.get(cause_name, {})
            if not symptoms:
                likelihood = 0.5
            else:
                # 取各症状似然的平均值
                likelihoods = [
                    symptom_likelihoods.get(s, 0.1)
                    for s in symptoms
                ]
                likelihood = float(np.mean(likelihoods))

            # 计算 P(症状) - 归一化常数
            p_symptoms = self._calculate_p_symptoms(symptoms)

            # 计算后验概率
            if p_symptoms > 0:
                posterior = (likelihood * prior) / p_symptoms
            else:
                posterior = prior

            # 限制在合理范围
            posterior = min(posterior, 0.99)

            # 证据强度
            evidence_strength = min(likelihood * len(symptoms) / 3, 1.0)

            nodes.append(BayesianNode(
                name=cause_name,
                prior=prior,
                likelihood=likelihood,
                posterior=posterior,
                evidence_strength=evidence_strength,
            ))

        # 按后验概率降序排序
        nodes.sort(key=lambda x: x.posterior, reverse=True)

        logger.info(
            "Bayesian inference completed",
            symptoms=symptoms,
            top_cause=nodes[0].name if nodes else "unknown",
            top_posterior=round(nodes[0].posterior, 4) if nodes else 0,
        )

        return nodes

    def _calculate_p_symptoms(self, symptoms: list[str]) -> float:
        """
        计算 P(症状) - 归一化常数

        P(症状) = sum_cause(P(症状|cause) * P(cause))
        """
        if not symptoms:
            return 1.0

        total = 0.0
        for cause_name, prior in self.PRIOR_PROBABILITIES.items():
            symptom_likelihoods = self.LIKELIHOODS.get(cause_name, {})
            likelihoods = [
                symptom_likelihoods.get(s, 0.1)
                for s in symptoms
            ]
            avg_likelihood = float(np.mean(likelihoods))
            total += avg_likelihood * prior

        # 避免除零
        return max(total, 0.01)

    # ==================== 症状提取 ====================

    def _extract_symptoms(self, alert: AlertEvent) -> list[str]:
        """从告警中提取症状关键词"""
        symptoms: list[str] = []

        # 基于指标名称映射
        metric_symptom_map: dict[str, list[str]] = {
            "cpu_usage_percent": ["high_cpu"],
            "memory_usage_percent": ["high_memory"],
            "disk_usage_percent": ["disk_full"],
            "p99_latency_ms": ["increased_latency"],
            "error_rate_percent": ["high_error_rate"],
            "request_timeout_rate": ["timeout"],
            "oom_killed_count": ["oom_killed"],
        }

        mapped = metric_symptom_map.get(alert.metric, [])
        symptoms.extend(mapped)

        # 基于阈值和值判断
        if alert.value > alert.threshold * 1.5:
            symptoms.append("severe_threshold_breach")

        # 从注释中提取
        for key in ["symptom", "symptoms"]:
            if key in alert.annotations:
                val = alert.annotations[key]
                if isinstance(val, str):
                    symptoms.extend(val.split(","))

        # 去重
        return list(set(s.strip() for s in symptoms if s.strip()))

    # ==================== RAG 检索 ====================

    def _rag_retrieve(
        self,
        alert: AlertEvent,
        symptoms: list[str],
    ) -> list[dict[str, Any]]:
        """
        RAG 增强 - 从知识库检索历史相似故障案例

        使用关键词匹配 + 类别匹配的综合检索策略。

        Args:
            alert: 告警事件
            symptoms: 症状列表

        Returns:
            list[dict]: 匹配的知识库条目
        """
        matches: list[tuple[dict[str, Any], float]] = []

        for kb_entry in self._knowledge_base:
            score = self._calculate_kb_match_score(kb_entry, alert, symptoms)
            if score > 0.3:  # 最低匹配阈值
                entry_copy = kb_entry.copy()
                entry_copy["match_score"] = round(score, 4)
                matches.append((entry_copy, score))

        # 按匹配得分降序排序
        matches.sort(key=lambda x: x[1], reverse=True)

        results = [m[0] for m in matches]

        logger.info(
            "RAG retrieval completed",
            symptoms=symptoms,
            matches_found=len(results),
            top_match_score=round(matches[0][1], 4) if matches else 0,
        )

        return results

    # ==================== 历史记忆检索 ====================

    async def _search_historical_incidents(
        self,
        alert: AlertEvent,
        symptoms: list[str],
    ) -> list[dict[str, Any]]:
        """
        从 MemorySystem 检索历史相似故障案例。

        使用语义搜索（ChromaDB 向量相似度）匹配历史故障记忆，
        返回相似度最高的历史根因和解决方案。

        Args:
            alert: 当前告警事件
            symptoms: 症状列表

        Returns:
            list[dict]: 匹配的历史记忆条目
        """
        ms = await self._get_memory_system()
        if ms is None:
            return []

        try:
            # 构建查询：服务名 + 指标名 + 症状
            query_text = f"{alert.service} {alert.metric} {' '.join(symptoms)}"

            result = await ms.search(
                query_text=query_text,
                top_k=5,
                memory_type=MemoryType.EPISODIC,  # 优先搜索事件记忆
            )

            if not result or result.total_found == 0:
                # 尝试搜索过程性记忆（playbook/自愈方案）
                result = await ms.search(
                    query_text=query_text,
                    top_k=3,
                    memory_type=MemoryType.PROCEDURAL,
                )

            if not result or result.total_found == 0:
                return []

            historical = []
            for entry, similarity in zip(result.results, result.similarities):
                historical.append({
                    "memory_id": entry.memory_id,
                    "content": entry.content[:500],
                    "summary": entry.summary,
                    "source_agent": entry.source_agent,
                    "source_incident": entry.source_incident_id,
                    "importance": entry.importance_score,
                    "retrieval_score": entry.retrieval_score,
                    "semantic_similarity": round(similarity, 4),
                    "tags": entry.tags,
                })

            logger.info(
                "Historical incident search completed",
                service=alert.service,
                query=query_text[:80],
                matches=len(historical),
                top_score=round(historical[0]["retrieval_score"], 4) if historical else 0,
            )

            return historical

        except Exception as e:
            logger.warning("Historical memory search failed, continuing without it", error=str(e))
            return []

    def _calculate_kb_match_score(
        self,
        kb_entry: dict[str, Any],
        alert: AlertEvent,
        symptoms: list[str],
    ) -> float:
        """计算知识库条目与当前告警的匹配得分"""
        score = 0.0

        # 症状匹配 (0-0.5)
        kb_symptoms = set(kb_entry.get("symptoms", []))
        if kb_symptoms:
            matched = len(kb_symptoms & set(symptoms))
            symptom_score = matched / max(len(kb_symptoms), len(symptoms))
            score += 0.5 * symptom_score

        # 指标类别匹配 (0-0.3)
        metric_category_map: dict[str, str] = {
            "cpu_usage_percent": "resource_exhaustion",
            "memory_usage_percent": "resource_exhaustion",
            "disk_usage_percent": "resource_exhaustion",
            "p99_latency_ms": "dependency_failure",
            "error_rate_percent": "dependency_failure",
            "request_timeout_rate": "dependency_failure",
        }
        alert_category = metric_category_map.get(alert.metric, "")
        if alert_category and kb_entry.get("category") == alert_category:
            score += 0.3

        # 服务等级匹配 (0-0.2)
        if kb_entry.get("category") == "deployment_issue":
            # 检查是否有近期部署
            service_changes = self._find_recent_changes(alert.service)
            if any(c.get("type") == "deployment" for c in service_changes):
                score += 0.2

        # 置信度加成
        score += kb_entry.get("confidence_boost", 0.0)

        return min(score, 1.0)

    # ==================== 综合分析 ====================

    def _synthesize_analysis(
        self,
        bayesian_results: list[BayesianNode],
        rag_results: list[dict[str, Any]],
        impact_chain: list[ServiceImpact],
        alert: AlertEvent,
        memory_results: list[dict[str, Any]] | None = None,
    ) -> tuple[str, float, dict[str, Any]]:
        """
        综合分析 - 合并贝叶斯推理、RAG 结果和历史记忆

        Returns:
            tuple: (根因, 置信度, 证据)
        """
        # 检查是否有知识库高匹配
        top_rag = rag_results[0] if rag_results else None

        # 贝叶斯最高后验
        top_bayesian = bayesian_results[0] if bayesian_results else None

        # 历史记忆最高匹配
        top_memory = memory_results[0] if memory_results else None
        memory_boost = 0.0
        historical_root_cause = None
        if top_memory:
            # 从记忆内容中提取历史根因（简化：匹配 "Root cause:" 后的文本）
            mem_content = top_memory.get("content", "")
            if "Root cause:" in mem_content or "root_cause" in str(top_memory.get("tags", [])):
                historical_root_cause = mem_content
            # 记忆检索得分作为置信度加成
            memory_similarity = top_memory.get("retrieval_score", 0)
            memory_boost = min(0.15, memory_similarity * 0.2)

        # 综合根因（历史记忆优先级最高）
        if top_memory and historical_root_cause and top_memory.get("retrieval_score", 0) > 0.5:
            # 有高度相似的历史故障，优先采纳历史根因
            if top_bayesian:
                root_cause = f"{top_bayesian.name} (historical_match: {top_memory.get('source_incident', 'unknown')})"
            else:
                root_cause = f"similar_to_{top_memory.get('source_incident', 'unknown')}"
        elif top_rag and top_rag.get("match_score", 0) > 0.7 and top_bayesian:
            # RAG 高匹配 + 贝叶斯支持
            rag_causes = top_rag.get("root_causes", [])
            if rag_causes and top_bayesian.name in rag_causes:
                root_cause = top_bayesian.name
            else:
                root_cause = rag_causes[0] if rag_causes else top_bayesian.name
        elif top_bayesian:
            root_cause = top_bayesian.name
        else:
            root_cause = "unknown"

        # 综合置信度（加上记忆加成）
        memory_confidence_boost = memory_boost
        if top_bayesian and top_rag:
            confidence = min(
                0.95,
                0.55 * top_bayesian.posterior
                + 0.25 * top_rag.get("match_score", 0)
                + 0.1 * top_bayesian.evidence_strength
                + 0.1 * memory_confidence_boost * 10,  # 记忆权重
            )
        elif top_bayesian:
            confidence = min(0.9, top_bayesian.posterior * 0.8 + memory_confidence_boost)
        else:
            confidence = 0.3 + memory_confidence_boost

        # 影响范围
        critical_services = [s.service for s in impact_chain if s.tier == "critical"]

        evidence = {
            "bayesian_top": top_bayesian.name if top_bayesian else "unknown",
            "bayesian_posterior": round(top_bayesian.posterior, 4) if top_bayesian else 0,
            "rag_top_match": top_rag["id"] if top_rag else "none",
            "rag_match_score": round(top_rag.get("match_score", 0), 4) if top_rag else 0,
            "historical_memory_matches": len(memory_results) if memory_results else 0,
            "memory_top_score": round(top_memory.get("retrieval_score", 0), 4) if top_memory else 0,
            "memory_confidence_boost": round(memory_confidence_boost, 4),
            "affected_services": [s.service for s in impact_chain],
            "critical_services_affected": critical_services,
            "total_impact_hops": max((s.hop_distance for s in impact_chain), default=0),
        }

        return root_cause, round(confidence, 4), evidence

    def _generate_suggested_actions(
        self,
        root_cause: str,
        impact_chain: list[ServiceImpact],
        rag_results: list[dict[str, Any]],
    ) -> list[str]:
        """生成建议操作"""
        actions: list[str] = []

        # 从知识库获取建议
        for rag in rag_results[:2]:
            solutions = rag.get("solutions", [])
            for sol in solutions:
                if sol not in actions:
                    actions.append(sol)

        # 基于根因类型的默认建议
        default_actions: dict[str, list[str]] = {
            "recent_deployment": ["rollback_deployment", "check_deployment_logs"],
            "configuration_change": ["rollback_configuration", "check_config_diff"],
            "resource_exhaustion": ["scale_up_resources", "check_resource_limits"],
            "dependency_failure": ["check_dependencies", "enable_circuit_breaker"],
            "network_issue": ["check_network_connectivity", "check_dns_resolution"],
            "database_issue": ["check_database_performance", "check_connection_pool"],
            "code_bug": ["check_application_logs", "enable_debug_logging"],
            "traffic_spike": ["scale_up_instances", "enable_rate_limiting"],
            "hardware_failure": ["drain_node", "migrate_workloads"],
            "third_party_issue": ["check_third_party_status", "enable_fallback"],
        }

        for action in default_actions.get(root_cause, ["investigate_manually"]):
            if action not in actions:
                actions.append(action)

        # 如果影响关键服务，添加额外建议
        critical_affected = any(s.tier == "critical" and s.hop_distance > 0 for s in impact_chain)
        if critical_affected:
            if "notify_oncall" not in actions:
                actions.append("notify_oncall")
            if "prepare_rollback" not in actions:
                actions.append("prepare_rollback")

        return actions
