"""
AIOps Agent Platform - Knowledge Tools

知识图谱工具，用于查询运维知识库和知识图谱。
提供基础查询工具和高级知识图谱操作工具。
"""

from __future__ import annotations

from collections import deque
from typing import Any

from app.data.knowledge_base import KNOWLEDGE_BASE, SERVICE_TOPOLOGY
try:
    from app.data.datasets import CHANGE_RECORDS
except ImportError:
    CHANGE_RECORDS = []

from app.tools.base import BaseTool, ToolParameter, ToolResult
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 基础知识查询工具（保留用于 LLM function calling）
# ============================================================


class QueryKnowledgeBaseTool(BaseTool):
    """查询知识库工具"""

    @property
    def name(self) -> str:
        return "query_knowledge_base"

    @property
    def description(self) -> str:
        return "查询运维知识库，获取故障处理经验和最佳实践"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                description="查询内容",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                description="返回结果数量",
                type="integer",
                required=False,
                default=5,
            ),
            ToolParameter(
                name="category",
                description=(
                    "知识类别，对应 KNOWLEDGE_BASE 条目的 category 字段"
                    "(如 deployment_issue/resource_exhaustion/dependency_failure)"
                ),
                type="string",
                required=False,
                default="",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """查询知识库"""
        query = kwargs.get("query", "")
        top_k = kwargs.get("top_k", 5)
        category = kwargs.get("category", "")

        logger.info("Querying knowledge base", query=query, category=category)

        # 先尝试 ChromaDB 向量检索
        try:
            from app.memory.storage import ChromaDBStorage
            storage = ChromaDBStorage()
            await storage._ensure_initialized()
            results = await storage.search(query, top_k=top_k)
            if results:
                return ToolResult.ok(
                    tool_name=self.name,
                    data={
                        "query": query,
                        "results": results,
                        "count": len(results),
                        "source": "chromadb",
                    },
                )
        except Exception as e:
            logger.debug("ChromaDB search unavailable, using keyword fallback", error=str(e))

        # Fallback: 在内存 KNOWLEDGE_BASE 中做关键词匹配
        # 条目键为 category/symptoms/root_causes/solutions(snake_case 词表),
        # 统一把下划线归一为空格后再匹配,使 "high cpu" 和 "high_cpu" 均可命中
        def _normalize(text: str) -> str:
            return text.lower().replace("_", " ")

        query_norm = _normalize(str(query))
        matched = []
        for entry in KNOWLEDGE_BASE:
            if category and entry.get("category") != category:
                continue
            symptoms_norm = _normalize(" ".join(entry.get("symptoms", [])))
            searchable = _normalize(
                " ".join(
                    [
                        str(entry.get("category", "")),
                        " ".join(entry.get("symptoms", [])),
                        " ".join(entry.get("root_causes", [])),
                        " ".join(entry.get("solutions", [])),
                    ]
                )
            )
            score = 0.0
            if query_norm and query_norm in searchable:
                score += 0.5
            for word in query_norm.split():
                if word in searchable:
                    score += 0.1
                if word in symptoms_norm:
                    score += 0.2
            if score > 0:
                # 与 RCAAgent._calculate_kb_match_score 一致:命中后叠加条目置信度加成
                score = min(score + float(entry.get("confidence_boost", 0.0)), 1.0)
                e_copy = dict(entry)
                e_copy["score"] = score
                matched.append(e_copy)
        matched.sort(key=lambda x: x["score"], reverse=True)
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "query": query,
                "results": matched[:top_k],
                "count": len(matched[:top_k]),
                "source": "keyword_fallback",
            },
        )


class QueryTopologyTool(BaseTool):
    """查询服务拓扑工具"""

    @property
    def name(self) -> str:
        return "query_topology"

    @property
    def description(self) -> str:
        return "查询服务依赖拓扑，获取服务的上下游依赖关系"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="service",
                description="服务名称",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="direction",
                description="查询方向(upstream/downstream/both)",
                type="string",
                required=False,
                default="both",
            ),
            ToolParameter(
                name="depth",
                description="查询深度",
                type="integer",
                required=False,
                default=3,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """查询拓扑"""
        service = kwargs.get("service", "")
        direction = kwargs.get("direction", "both")
        depth = kwargs.get("depth", 3)

        logger.info(
            "Querying topology",
            service=service,
            direction=direction,
        )

        target = str(service) if service else None
        if target:
            svc = SERVICE_TOPOLOGY.get(target, {})
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "service": target,
                    "dependencies": svc.get("dependencies", []),
                    "tier": svc.get("tier", "standard"),
                    "team": svc.get("team", ""),
                    "source": "service_topology",
                },
            )
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "services": list(SERVICE_TOPOLOGY.keys()),
                "topology": SERVICE_TOPOLOGY,
                "count": len(SERVICE_TOPOLOGY),
                "source": "service_topology",
            },
        )


class QueryChangeHistoryTool(BaseTool):
    """查询变更历史工具"""

    @property
    def name(self) -> str:
        return "query_change_history"

    @property
    def description(self) -> str:
        return "查询服务的近期变更历史，包括部署、配置变更等"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="service",
                description="服务名称",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="time_range",
                description="时间范围(如 24h, 7d)",
                type="string",
                required=False,
                default="24h",
            ),
            ToolParameter(
                name="change_type",
                description="变更类型(deployment/config/infra)",
                type="string",
                required=False,
                default="",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """查询变更历史"""
        service = kwargs.get("service", "")
        time_range = kwargs.get("time_range", "24h")
        change_type = kwargs.get("change_type", "")

        logger.info(
            "Querying change history",
            service=service,
            time_range=time_range,
        )

        svc = str(service) if service else None
        if not svc:
            return ToolResult.error(
                tool_name=self.name,
                error_message="service parameter required",
            )
        try:
            cutoff = kwargs.get("window_minutes") or 60
            results = [cr for cr in CHANGE_RECORDS if cr.get("service") == svc]
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "service": svc,
                    "window_minutes": cutoff,
                    "changes": results,
                    "count": len(results),
                    "source": "datasets_internal",
                    "note": "Real ArgoCD/GitLab API integration pending (see Phase 3 '已知遗留')",
                },
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"get_recent_changes failed: {e}",
            )


# ============================================================
# 高级知识图谱工具 - KnowledgeGraphTool
# ============================================================


class KnowledgeGraphTool(BaseTool):
    """
    高级知识图谱工具

    提供服务拓扑查询、依赖分析、历史故障检索等功能。
    集成知识库数据和拓扑数据，支持 AIOps 场景的深度分析。
    """

    @property
    def name(self) -> str:
        return "knowledge_graph"

    @property
    def description(self) -> str:
        return (
            "查询服务拓扑、依赖关系、历史故障知识。"
            "支持BFS获取依赖链、计算服务影响分数、查询最近变更等。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                description="操作类型(topology/dependencies/recent_changes/historical_incidents/impact_score)",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="service",
                description="服务名称",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="depth",
                description="依赖查询深度",
                type="integer",
                required=False,
                default=2,
            ),
            ToolParameter(
                name="symptom",
                description="故障症状（用于历史故障查询）",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="hours",
                description="最近N小时的变更",
                type="integer",
                required=False,
                default=24,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行知识图谱操作

        Args:
            operation: 操作类型
            service: 服务名称
            depth: 查询深度
            symptom: 故障症状
            hours: 时间范围（小时）

        Returns:
            ToolResult: 操作结果
        """
        operation = kwargs.get("operation", "topology")
        service = kwargs.get("service", "")

        logger.info(
            "Knowledge graph operation",
            operation=operation,
            service=service,
        )

        try:
            result: dict[str, Any] = {}

            if operation == "topology":
                result = await self.get_service_topology(service)
            elif operation == "dependencies":
                depth = int(kwargs.get("depth", 2))
                deps = await self.get_dependencies(service, depth)
                result = {"service": service, "dependencies": deps, "depth": depth}
            elif operation == "recent_changes":
                hours = int(kwargs.get("hours", 24))
                result = {"service": service, "changes": await self.get_recent_changes(service, hours)}
            elif operation == "historical_incidents":
                symptom = kwargs.get("symptom", "")
                result = {
                    "service": service,
                    "symptom": symptom,
                    "incidents": await self.query_historical_incidents(service, symptom),
                }
            elif operation == "impact_score":
                score = await self.calculate_impact_score(service)
                result = {"service": service, "impact_score": score}
            else:
                return ToolResult.error(
                    tool_name=self.name,
                    error_message=f"Unknown operation: {operation}",
                )

            return ToolResult.ok(
                tool_name=self.name,
                data=result,
            )

        except Exception as e:
            logger.error(
                "Knowledge graph operation failed",
                operation=operation,
                service=service,
                error=str(e),
            )
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Knowledge graph operation failed: {e}",
            )

    async def get_service_topology(self, service: str) -> dict[str, Any]:
        """
        获取服务的依赖拓扑

        返回服务的上下游依赖关系、层级信息。

        Args:
            service: 服务名称

        Returns:
            拓扑信息字典，包含 nodes 和 edges
        """
        from app.data.knowledge_base import SERVICE_TOPOLOGY

        if service not in SERVICE_TOPOLOGY:
            logger.warning("Service not found in topology", service=service)
            return {
                "service": service,
                "nodes": [{"id": service, "tier": "unknown", "namespace": "unknown"}],
                "edges": [],
            }

        topo = SERVICE_TOPOLOGY[service]
        nodes = [{"id": service, "tier": topo.get("tier", "unknown"), "namespace": topo.get("namespace", "production")}]
        edges = []

        # 添加下游依赖
        for dep in topo.get("dependencies", []):
            nodes.append({"id": dep, "relation": "dependency"})
            edges.append({"source": service, "target": dep, "type": "depends_on"})

        # 添加上游依赖
        for dependent in topo.get("dependents", []):
            nodes.append({"id": dependent, "relation": "dependent"})
            edges.append({"source": dependent, "target": service, "type": "depends_on"})

        return {
            "service": service,
            "namespace": topo.get("namespace", "production"),
            "tier": topo.get("tier", "standard"),
            "nodes": nodes,
            "edges": edges,
        }

    async def get_dependencies(
        self,
        service: str,
        depth: int = 2,
    ) -> list[str]:
        """
        BFS 获取服务的依赖链

        使用广度优先搜索获取指定深度内的所有依赖服务。

        Args:
            service: 服务名称
            depth: 搜索深度（默认2层）

        Returns:
            依赖服务名称列表
        """
        from app.data.knowledge_base import SERVICE_TOPOLOGY

        if service not in SERVICE_TOPOLOGY:
            return []

        visited = {service}
        queue = deque([(service, 0)])
        dependencies = []

        while queue:
            current, current_depth = queue.popleft()

            if current_depth >= depth:
                continue

            topo = SERVICE_TOPOLOGY.get(current, {})
            for dep in topo.get("dependencies", []):
                if dep not in visited:
                    visited.add(dep)
                    dependencies.append(dep)
                    queue.append((dep, current_depth + 1))

        return dependencies

    async def get_recent_changes(
        self,
        service: str,
        hours: int = 24,
    ) -> list[dict[str, Any]]:
        """
        获取服务最近的变更记录

        Args:
            service: 服务名称
            hours: 最近 N 小时

        Returns:
            变更记录列表
        """
        from app.data.datasets import get_change_records

        all_records = get_change_records(service)

        # 按时间过滤（简化实现：返回最近的记录）
        return all_records[: max(1, hours // 24 + 1)]

    async def query_historical_incidents(
        self,
        service: str,
        symptom: str,
    ) -> list[dict[str, Any]]:
        """
        查询历史相似故障

        基于服务和症状匹配历史故障场景。

        Args:
            service: 服务名称
            symptom: 故障症状描述

        Returns:
            相似故障列表
        """
        from app.data.datasets import FAULT_SCENARIOS

        matches = []
        symptom_lower = symptom.lower()

        for scenario in FAULT_SCENARIOS:
            # 服务匹配
            service_match = scenario.get("service", "") == service
            # 症状关键词匹配
            description = scenario.get("description", "").lower()
            name = scenario.get("name", "").lower()
            symptom_match = any(
                kw in description or kw in name
                for kw in symptom_lower.split()
            )

            if service_match and (symptom_match or not symptom):
                matches.append({
                    "id": scenario["id"],
                    "name": scenario["name"],
                    "description": scenario["description"],
                    "root_cause": scenario.get("root_cause", ""),
                    "expected_action": scenario.get("expected_action", {}),
                    "severity": scenario.get("severity", "unknown"),
                })

        logger.info(
            "Historical incidents queried",
            service=service,
            symptom=symptom,
            matches=len(matches),
        )

        return matches

    async def calculate_impact_score(self, service: str) -> float:
        """
        计算服务的影响分数

        基于依赖关系计算服务故障的影响范围:
        - 依赖越多，影响越大
        - 关键级别越高，影响越大

        Args:
            service: 服务名称

        Returns:
            影响分数 (0.0 - 1.0)
        """
        from app.data.knowledge_base import SERVICE_TOPOLOGY

        if service not in SERVICE_TOPOLOGY:
            return 0.0

        topo = SERVICE_TOPOLOGY[service]

        # 基于依赖数量计算
        dep_count = len(topo.get("dependencies", []))
        dependent_count = len(topo.get("dependents", []))

        # 关键级别权重
        tier_weights = {
            "critical": 0.4,
            "standard": 0.2,
            "low": 0.1,
        }
        tier_weight = tier_weights.get(topo.get("tier", "standard"), 0.2)

        # 影响分数 = 被依赖数 * 权重 + 依赖数 * 0.1
        score = min(
            dependent_count * tier_weight + dep_count * 0.05,
            1.0,
        )

        logger.info(
            "Impact score calculated",
            service=service,
            score=round(score, 3),
            dependents=dependent_count,
            dependencies=dep_count,
        )

        return round(score, 3)


def register_knowledge_tools() -> list[BaseTool]:
    """
    注册所有知识工具

    Returns:
        list[BaseTool]: 知识工具列表
    """
    return [
        QueryKnowledgeBaseTool(),
        QueryTopologyTool(),
        QueryChangeHistoryTool(),
        KnowledgeGraphTool(),
    ]
