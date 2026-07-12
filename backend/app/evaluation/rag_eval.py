"""
AIOps Agent Platform - RAG Evaluation

RAG 评估，评估检索增强生成的效果。
涵盖检索质量、生成质量、上下文充分性三个维度。

评估维度:
- 检索精确率 (Retrieval Precision)
- 检索召回率 (Retrieval Recall)
- 上下文相关性 (Context Relevance)
- 上下文充分性 (Context Sufficiency)
- 回答忠实度 (Answer Faithfulness)
- 回答相关性 (Answer Relevance)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.evaluation.metrics import (
    answer_faithfulness,
    answer_relevance,
    context_relevance,
    context_sufficiency,
    retrieval_precision,
    retrieval_recall,
)
from app.models.evaluation import (
    EvaluationResult,
    EvaluationStatus,
    EvaluationType,
    MetricScore,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


# 默认 RAG 测试数据集
DEFAULT_RAG_DATASET: list[dict[str, Any]] = [
    {
        "id": "rag_cpu_troubleshooting",
        "name": "CPU Troubleshooting",
        "query": "How to troubleshoot high CPU usage on Kubernetes pods?",
        "retrieved_docs": [
            {
                "id": "doc_001",
                "content": "High CPU usage on Kubernetes pods can be caused by traffic spikes, inefficient code, or resource limits. Check request rate correlation first.",
                "score": 0.92,
            },
            {
                "id": "doc_005",
                "content": "To scale pods in Kubernetes, use kubectl scale deployment or HPA based on CPU metrics.",
                "score": 0.85,
            },
            {
                "id": "doc_012",
                "content": "Resource limits in Kubernetes can cause CPU throttling. Check limit settings in pod spec.",
                "score": 0.78,
            },
        ],
        "relevant_docs": ["doc_001", "doc_005", "doc_012"],
        "generated_answer": "High CPU usage can be troubleshooted by checking request rates, reviewing resource limits, and scaling if needed. Use HPA for auto-scaling based on CPU metrics.",
    },
    {
        "id": "rag_memory_leak",
        "name": "Memory Leak Diagnosis",
        "query": "What are common causes of memory leaks in Java applications?",
        "retrieved_docs": [
            {
                "id": "doc_023",
                "content": "Java memory leaks often occur from unclosed database connections, static collections growing indefinitely, or incorrect cache implementations.",
                "score": 0.95,
            },
            {
                "id": "doc_031",
                "content": "Use heap dumps and profiling tools like JProfiler or VisualVM to diagnose memory leaks in Java.",
                "score": 0.88,
            },
            {
                "id": "doc_045",
                "content": "Kubernetes memory limits can cause OOMKilled events. Monitor memory usage with Prometheus.",
                "score": 0.65,
            },
        ],
        "relevant_docs": ["doc_023", "doc_031"],
        "generated_answer": "Common Java memory leak causes include unclosed database connections, growing static collections, and improper caching. Use heap dumps and JProfiler for diagnosis.",
    },
    {
        "id": "rag_db_performance",
        "name": "Database Performance",
        "query": "How to fix slow database queries?",
        "retrieved_docs": [
            {
                "id": "doc_067",
                "content": "Slow queries can be fixed by adding indexes, optimizing query structure, or scaling the database.",
                "score": 0.90,
            },
            {
                "id": "doc_071",
                "content": "Connection pool exhaustion causes slow response times. Monitor active connections and pool size.",
                "score": 0.82,
            },
            {
                "id": "doc_089",
                "content": "Database sharding can distribute load across multiple instances for better performance.",
                "score": 0.75,
            },
        ],
        "relevant_docs": ["doc_067", "doc_071", "doc_089"],
        "generated_answer": "Fix slow queries by adding indexes, optimizing query structure, monitoring connection pools, and considering database sharding for high load scenarios.",
    },
    {
        "id": "rag_network_issues",
        "name": "Network Issues",
        "query": "How to diagnose network partition in distributed systems?",
        "retrieved_docs": [
            {
                "id": "doc_092",
                "content": "Network partitions can be detected using heartbeat mechanisms and consensus protocols like Raft.",
                "score": 0.93,
            },
            {
                "id": "doc_103",
                "content": "Circuit breaker pattern helps handle network failures gracefully in microservices.",
                "score": 0.87,
            },
        ],
        "relevant_docs": ["doc_092", "doc_103"],
        "generated_answer": "Diagnose network partitions using heartbeat mechanisms and Raft consensus. Use circuit breakers to handle failures gracefully in microservices.",
    },
]


class RAGEvaluator:
    """
    RAG 评估器

    评估检索增强生成的效果，包括检索和生成两个环节。

    支持:
    - 检索质量评估 (evaluate_retrieval)
    - 生成质量评估 (evaluate_generation)
    - 上下文充分性评估 (evaluate_context_sufficiency)
    - 综合批量评估 (evaluate)
    """

    def __init__(self) -> None:
        self._eval_history: list[EvaluationResult] = []

    async def evaluate(
        self,
        target_agent: str = "",
        dataset_name: str = "",
        samples: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        """
        执行 RAG 评估

        Args:
            target_agent: 目标 Agent 名称
            dataset_name: 数据集名称
            samples: 自定义测试样本

        Returns:
            EvaluationResult: 评估结果
        """
        result = EvaluationResult(
            evaluation_type=EvaluationType.RAG,
            eval_name=f"rag_{target_agent or 'all'}",
            description="评估检索增强生成的效果",
            agent_type=target_agent,
            status=EvaluationStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        try:
            test_samples = samples or await self._load_default_dataset()
            result.total_samples = len(test_samples)

            if not test_samples:
                result.status = EvaluationStatus.FAILED
                result.errors.append("No test samples available")
                result.completed_at = datetime.now(timezone.utc)
                return result

            # 分别评估检索和生成
            retrieval_results = await self.evaluate_retrieval(test_samples)
            generation_results = await self.evaluate_generation(test_samples)
            context_results = await self.evaluate_context_sufficiency(test_samples)

            # 汇总指标
            avg_precision = retrieval_results.get("avg_precision", 0.0)
            avg_recall = retrieval_results.get("avg_recall", 0.0)
            avg_context_relevance = context_results.get("avg_relevance", 0.0)
            avg_context_sufficiency = context_results.get("avg_sufficiency", 0.0)
            avg_faithfulness = generation_results.get("avg_faithfulness", 0.0)
            avg_answer_relevance = generation_results.get("avg_relevance", 0.0)

            # F1 分数 (检索)
            retrieval_f1 = (
                2 * avg_precision * avg_recall / (avg_precision + avg_recall)
                if (avg_precision + avg_recall) > 0
                else 0.0
            )

            result.metric_scores = [
                MetricScore(
                    metric_name="retrieval_precision",
                    score=avg_precision,
                    weight=0.20,
                    details={
                        "description": "检索精确率",
                        "per_sample": retrieval_results.get("per_sample", []),
                    },
                ),
                MetricScore(
                    metric_name="retrieval_recall",
                    score=avg_recall,
                    weight=0.20,
                    details={
                        "description": "检索召回率",
                        "retrieval_f1": round(retrieval_f1, 4),
                    },
                ),
                MetricScore(
                    metric_name="context_relevance",
                    score=avg_context_relevance,
                    weight=0.15,
                    details={"description": "上下文相关性"},
                ),
                MetricScore(
                    metric_name="context_sufficiency",
                    score=avg_context_sufficiency,
                    weight=0.15,
                    details={"description": "上下文充分性"},
                ),
                MetricScore(
                    metric_name="answer_faithfulness",
                    score=avg_faithfulness,
                    weight=0.15,
                    details={"description": "回答忠实度"},
                ),
                MetricScore(
                    metric_name="answer_relevance",
                    score=avg_answer_relevance,
                    weight=0.15,
                    details={"description": "回答相关性"},
                ),
            ]

            result.overall_score = sum(s.weighted_score for s in result.metric_scores)
            result.passed_samples = sum(
                1
                for s in test_samples
                if self._is_sample_passed(s)
            )
            result.failed_samples = result.total_samples - result.passed_samples
            result.sample_results = [
                {
                    "sample_id": s.get("id", ""),
                    "retrieval": {
                        "precision": retrieval_results.get("per_sample", [{}])[i].get(
                            "precision", 0.0
                        )
                        if i < len(retrieval_results.get("per_sample", []))
                        else 0.0,
                        "recall": retrieval_results.get("per_sample", [{}])[i].get(
                            "recall", 0.0
                        )
                        if i < len(retrieval_results.get("per_sample", []))
                        else 0.0,
                    },
                    "generation": {
                        "faithfulness": generation_results.get("per_sample", [{}])[
                            i
                        ].get("faithfulness", 0.0)
                        if i < len(generation_results.get("per_sample", []))
                        else 0.0,
                        "relevance": generation_results.get("per_sample", [{}])[
                            i
                        ].get("relevance", 0.0)
                        if i < len(generation_results.get("per_sample", []))
                        else 0.0,
                    },
                }
                for i, s in enumerate(test_samples)
            ]

            result.status = EvaluationStatus.COMPLETED

            logger.info(
                "RAG evaluation completed",
                overall_score=result.overall_score,
                retrieval_precision=avg_precision,
                retrieval_recall=avg_recall,
            )

        except Exception as e:
            logger.error("RAG evaluation failed", error=str(e), exc_info=True)
            result.status = EvaluationStatus.FAILED
            result.errors.append(str(e))

        finally:
            result.completed_at = datetime.now(timezone.utc)
            self._eval_history.append(result)

        return result

    async def evaluate_retrieval(
        self, samples: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        评估检索质量

        计算检索精确率和召回率。

        Args:
            samples: 测试样本列表，每个样本包含 retrieved_docs 和 relevant_docs

        Returns:
            dict: 检索质量评估结果
        """
        try:
            per_sample_results: list[dict[str, Any]] = []
            precisions: list[float] = []
            recalls: list[float] = []

            for sample in samples:
                retrieved = [
                    doc.get("id", "") for doc in sample.get("retrieved_docs", [])
                ]
                relevant = sample.get("relevant_docs", [])

                if not retrieved:
                    continue

                prec = retrieval_precision(retrieved, relevant)
                rec = retrieval_recall(retrieved, relevant)

                precisions.append(prec)
                recalls.append(rec)

                per_sample_results.append(
                    {
                        "sample_id": sample.get("id", ""),
                        "precision": round(prec, 4),
                        "recall": round(rec, 4),
                        "retrieved_count": len(retrieved),
                        "relevant_count": len(relevant),
                    }
                )

            avg_precision = float(np.mean(precisions)) if precisions else 0.0
            avg_recall = float(np.mean(recalls)) if recalls else 0.0

            # 检索 F1
            retrieval_f1 = (
                2 * avg_precision * avg_recall / (avg_precision + avg_recall)
                if (avg_precision + avg_recall) > 0
                else 0.0
            )

            return {
                "avg_precision": round(avg_precision, 4),
                "avg_recall": round(avg_recall, 4),
                "retrieval_f1": round(retrieval_f1, 4),
                "per_sample": per_sample_results,
                "total_samples": len(samples),
            }

        except Exception as e:
            logger.error("Retrieval evaluation failed", error=str(e))
            return {"avg_precision": 0.0, "avg_recall": 0.0, "error": str(e)}

    async def evaluate_generation(
        self, samples: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        评估生成质量

        计算回答忠实度和相关性。

        Args:
            samples: 测试样本列表，每个样本包含 generated_answer 和 retrieved_docs

        Returns:
            dict: 生成质量评估结果
        """
        try:
            per_sample_results: list[dict[str, Any]] = []
            faithfulness_scores: list[float] = []
            relevance_scores: list[float] = []

            for sample in samples:
                answer = sample.get("generated_answer", "")
                retrieved_docs = sample.get("retrieved_docs", [])
                query = sample.get("query", "")

                # 提取文档内容
                contexts = [
                    doc.get("content", "") for doc in retrieved_docs if doc.get("content")
                ]

                if not answer or not contexts:
                    continue

                # 忠实度
                faith = answer_faithfulness(answer, contexts)
                faithfulness_scores.append(faith)

                # 相关性
                rel = answer_relevance(answer, query)
                relevance_scores.append(rel)

                per_sample_results.append(
                    {
                        "sample_id": sample.get("id", ""),
                        "faithfulness": round(faith, 4),
                        "relevance": round(rel, 4),
                        "answer_length": len(answer),
                    }
                )

            avg_faithfulness = (
                float(np.mean(faithfulness_scores)) if faithfulness_scores else 0.0
            )
            avg_relevance = float(np.mean(relevance_scores)) if relevance_scores else 0.0

            return {
                "avg_faithfulness": round(avg_faithfulness, 4),
                "avg_relevance": round(avg_relevance, 4),
                "per_sample": per_sample_results,
                "total_samples": len(samples),
            }

        except Exception as e:
            logger.error("Generation evaluation failed", error=str(e))
            return {"avg_faithfulness": 0.0, "avg_relevance": 0.0, "error": str(e)}

    async def evaluate_context_sufficiency(
        self, samples: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        评估上下文充分性

        检查检索到的上下文是否足以回答查询。

        Args:
            samples: 测试样本列表

        Returns:
            dict: 上下文充分性评估结果
        """
        try:
            relevance_scores: list[float] = []
            sufficiency_scores: list[float] = []
            per_sample_results: list[dict[str, Any]] = []

            for sample in samples:
                query = sample.get("query", "")
                retrieved_docs = sample.get("retrieved_docs", [])

                contexts = [
                    doc.get("content", "") for doc in retrieved_docs if doc.get("content")
                ]

                if not query or not contexts:
                    continue

                # 相关性
                rel = context_relevance(contexts, query)
                relevance_scores.append(rel)

                # 充分性
                suff = context_sufficiency(contexts, query)
                sufficiency_scores.append(suff)

                per_sample_results.append(
                    {
                        "sample_id": sample.get("id", ""),
                        "relevance": round(rel, 4),
                        "sufficiency": round(suff, 4),
                        "context_count": len(contexts),
                    }
                )

            avg_relevance = float(np.mean(relevance_scores)) if relevance_scores else 0.0
            avg_sufficiency = (
                float(np.mean(sufficiency_scores)) if sufficiency_scores else 0.0
            )

            return {
                "avg_relevance": round(avg_relevance, 4),
                "avg_sufficiency": round(avg_sufficiency, 4),
                "per_sample": per_sample_results,
                "total_samples": len(samples),
            }

        except Exception as e:
            logger.error("Context sufficiency evaluation failed", error=str(e))
            return {"avg_relevance": 0.0, "avg_sufficiency": 0.0, "error": str(e)}

    @staticmethod
    def _is_sample_passed(sample: dict[str, Any]) -> bool:
        """判断单个样本是否通过评估"""
        retrieved = [doc.get("id", "") for doc in sample.get("retrieved_docs", [])]
        relevant = sample.get("relevant_docs", [])

        if not retrieved or not relevant:
            return False

        prec = retrieval_precision(retrieved, relevant)
        rec = retrieval_recall(retrieved, relevant)

        # 通过标准: precision >= 0.5 且 recall >= 0.5
        return prec >= 0.5 and rec >= 0.5

    async def _load_default_dataset(self) -> list[dict[str, Any]]:
        """加载默认 RAG 测试数据集"""
        return DEFAULT_RAG_DATASET.copy()

    async def save_results(
        self, result: EvaluationResult, output_dir: str = "/tmp/eval_results"
    ) -> str:
        """
        保存评估结果到文件

        Args:
            result: 评估结果
            output_dir: 输出目录

        Returns:
            str: 保存的文件路径
        """
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            filename = f"rag_{result.started_at.strftime('%Y%m%d_%H%M%S')}.json"
            filepath = output_path / filename

            data = {
                "result_id": result.result_id,
                "evaluation_type": result.evaluation_type.value,
                "overall_score": result.overall_score,
                "metric_scores": [
                    {
                        "metric_name": m.metric_name,
                        "score": m.score,
                        "weight": m.weight,
                        "details": m.details,
                    }
                    for m in result.metric_scores
                ],
                "total_samples": result.total_samples,
                "passed_samples": result.passed_samples,
                "failed_samples": result.failed_samples,
                "duration_seconds": result.duration_seconds,
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info("RAG evaluation results saved", filepath=str(filepath))
            return str(filepath)

        except Exception as e:
            logger.error("Failed to save RAG results", error=str(e))
            return ""
