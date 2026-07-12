"""
AIOps Agent Platform - Comprehensive Evaluation Metrics Library

评估指标库，提供四个维度的所有基础评估指标计算：
- 通用指标 (General)
- 端到端指标 (End-to-End)
- 推理指标 (Reasoning)
- 工具调用指标 (Tool Call)
- RAG指标 (Retrieval-Augmented Generation)

参考论文:
- Agent-World: 持续自进化评估
- VitaBench: 推理复杂度评估
- DoVer: 干预验证评估
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.utils.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# 通用指标 (General Metrics)
# =============================================================================


def accuracy(predicted: list[Any], actual: list[Any]) -> float:
    """
    计算准确率

    Args:
        predicted: 预测结果列表
        actual: 实际标签列表

    Returns:
        float: 准确率 (0.0 - 1.0)
    """
    if len(predicted) != len(actual) or len(predicted) == 0:
        return 0.0

    correct = sum(1 for p, a in zip(predicted, actual) if p == a)
    return correct / len(predicted)


def precision_recall_f1(predicted: list[Any], actual: list[Any]) -> dict[str, float]:
    """
    计算精确率、召回率和 F1 分数

    Args:
        predicted: 预测结果列表 (1/0 或 True/False)
        actual: 实际标签列表 (1/0 或 True/False)

    Returns:
        dict: {precision, recall, f1}
    """
    if len(predicted) != len(actual) or len(predicted) == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    tp = sum(1 for p, a in zip(predicted, actual) if p and a)
    fp = sum(1 for p, a in zip(predicted, actual) if p and not a)
    fn = sum(1 for p, a in zip(predicted, actual) if not p and a)

    precision_val = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall_val = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1_val = (
        2 * precision_val * recall_val / (precision_val + recall_val)
        if (precision_val + recall_val) > 0
        else 0.0
    )

    return {"precision": precision_val, "recall": recall_val, "f1": f1_val}


def mse(predicted: list[float], actual: list[float]) -> float:
    """
    计算均方误差 (Mean Squared Error)

    Args:
        predicted: 预测值列表
        actual: 实际值列表

    Returns:
        float: MSE 值
    """
    if len(predicted) != len(actual) or len(predicted) == 0:
        return 0.0

    return sum((p - a) ** 2 for p, a in zip(predicted, actual)) / len(predicted)


def mae(predicted: list[float], actual: list[float]) -> float:
    """
    计算平均绝对误差 (Mean Absolute Error)

    Args:
        predicted: 预测值列表
        actual: 实际值列表

    Returns:
        float: MAE 值
    """
    if len(predicted) != len(actual) or len(predicted) == 0:
        return 0.0

    return sum(abs(p - a) for p, a in zip(predicted, actual)) / len(predicted)


# =============================================================================
# 端到端指标 (End-to-End Metrics)
# =============================================================================


def task_success_rate(results: list[dict[str, Any]]) -> float:
    """
    计算任务成功率

    Args:
        results: 每次处理的结果列表，每个结果包含 "success" 字段

    Returns:
        float: 任务成功率 (0.0 - 1.0)
    """
    if not results:
        return 0.0

    successes = sum(1 for r in results if r.get("success", False))
    return successes / len(results)


def mttr_simulated(start_times: list[float], end_times: list[float]) -> float:
    """
    计算模拟平均修复时间 (Mean Time To Repair)

    Args:
        start_times: 各次处理开始时间戳列表
        end_times: 各次处理结束时间戳列表

    Returns:
        float: 平均修复时间(秒)
    """
    if len(start_times) != len(end_times) or len(start_times) == 0:
        return 0.0

    repair_times = [
        max(0.0, end - start)
        for start, end in zip(start_times, end_times)
    ]
    return float(np.mean(repair_times))


def automation_rate(results: list[dict[str, Any]]) -> float:
    """
    计算自动化率（无需人工介入的处理比例）

    Args:
        results: 每次处理的结果列表，包含 "automated" 字段

    Returns:
        float: 自动化率 (0.0 - 1.0)
    """
    if not results:
        return 0.0

    automated = sum(1 for r in results if r.get("automated", False))
    return automated / len(results)


def escalation_rate(results: list[dict[str, Any]]) -> float:
    """
    计算升级率（需要人工介入的处理比例）

    Args:
        results: 每次处理的结果列表，包含 "escalated" 字段

    Returns:
        float: 升级率 (0.0 - 1.0)
    """
    if not results:
        return 0.0

    escalated = sum(1 for r in results if r.get("escalated", False))
    return escalated / len(results)


def false_positive_rate(predicted: list[bool], actual: list[bool]) -> float:
    """
    计算误报率

    Args:
        predicted: 预测是否为异常列表
        actual: 实际是否为异常列表

    Returns:
        float: 误报率 (0.0 - 1.0)
    """
    if len(predicted) != len(actual) or len(predicted) == 0:
        return 0.0

    actual_normals = sum(1 for a in actual if not a)
    if actual_normals == 0:
        return 0.0

    false_positives = sum(
        1 for p, a in zip(predicted, actual) if p and not a
    )
    return false_positives / actual_normals


def detection_accuracy(predicted: list[bool], actual: list[bool]) -> float:
    """
    计算检测准确率（异常/正常分类的准确率）

    Args:
        predicted: 预测结果列表
        actual: 实际标签列表

    Returns:
        float: 检测准确率 (0.0 - 1.0)
    """
    return accuracy(predicted, actual)


def resolution_rate(results: list[dict[str, Any]]) -> float:
    """
    计算解决率

    Args:
        results: 处理结果列表，包含 "resolved" 字段

    Returns:
        float: 解决率 (0.0 - 1.0)
    """
    if not results:
        return 0.0

    resolved = sum(1 for r in results if r.get("resolved", False))
    return resolved / len(results)


# =============================================================================
# 推理指标 (Reasoning Metrics)
# =============================================================================


def root_cause_accuracy(predicted_rc: list[str], actual_rc: list[str]) -> float:
    """
    计算根因准确率

    Args:
        predicted_rc: 预测根因列表
        actual_rc: 实际根因列表

    Returns:
        float: 根因准确率 (0.0 - 1.0)
    """
    return accuracy(predicted_rc, actual_rc)


def confidence_calibration(confidence: list[float], correct: list[bool]) -> float:
    """
    计算置信度校准误差 (Expected Calibration Error)

    使用等宽分桶法计算 ECE。置信度应与实际准确率匹配。
    返回校准误差，值越小表示校准越好。

    Args:
        confidence: 置信度列表 (0.0 - 1.0)
        correct: 是否正确列表 (True/False 或 1/0)

    Returns:
        float: ECE 校准误差 (0.0 - 1.0)，越小越好
    """
    if not confidence or not correct or len(confidence) != len(correct):
        return 0.0

    n_bins = 5
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    bin_lowers = bin_boundaries[:-1]
    bin_uppers = bin_boundaries[1:]

    ece = 0.0
    total_samples = len(confidence)
    accuracies = [1.0 if c else 0.0 for c in correct]

    for lower, upper in zip(bin_lowers, bin_uppers):
        in_bin = [
            i
            for i, c in enumerate(confidence)
            if lower <= c < upper or (upper == 1.0 and c == 1.0)
        ]

        if not in_bin:
            continue

        avg_confidence = float(np.mean([confidence[i] for i in in_bin]))
        avg_accuracy = float(np.mean([accuracies[i] for i in in_bin]))
        bin_weight = len(in_bin) / total_samples

        ece += bin_weight * abs(avg_confidence - avg_accuracy)

    return ece


def reasoning_steps_quality(
    steps: list[dict[str, Any]], expected_steps: list[dict[str, Any]]
) -> float:
    """
    评估推理步骤质量

    比较实际推理步骤与期望步骤的匹配度，考虑步骤的：
    - 完整性（是否覆盖了所有期望步骤）
    - 顺序正确性
    - 逻辑正确性

    Args:
        steps: 实际推理步骤列表
        expected_steps: 期望推理步骤列表

    Returns:
        float: 推理步骤质量分数 (0.0 - 1.0)
    """
    if not steps and not expected_steps:
        return 1.0
    if not steps or not expected_steps:
        return 0.0

    # 计算步骤覆盖度
    step_descriptions = {s.get("description", "") for s in steps}
    expected_descriptions = {s.get("description", "") for s in expected_steps}

    if not expected_descriptions:
        return 1.0 if not step_descriptions else 0.0

    overlap = step_descriptions & expected_descriptions
    coverage = len(overlap) / len(expected_descriptions)

    # 计算顺序正确性（最长公共子序列）
    step_order = [s.get("description", "") for s in steps]
    expected_order = [s.get("description", "") for s in expected_steps]
    lcs_length = _longest_common_subsequence(step_order, expected_order)
    order_score = lcs_length / max(len(expected_order), 1)

    # 加权综合 (覆盖度60% + 顺序40%)
    return coverage * 0.6 + order_score * 0.4


def evidence_completeness(
    evidence: dict[str, Any], expected_evidence: dict[str, Any]
) -> float:
    """
    评估证据完整性

    检查提供的证据是否覆盖了所有期望的证据类型和内容。

    Args:
        evidence: 实际提供的证据字典
        expected_evidence: 期望的证据字典

    Returns:
        float: 证据完整性分数 (0.0 - 1.0)
    """
    if not expected_evidence:
        return 1.0
    if not evidence:
        return 0.0

    # 检查证据类型的覆盖
    expected_keys = set(expected_evidence.keys())
    actual_keys = set(evidence.keys())

    if not expected_keys:
        return 1.0

    covered = len(expected_keys & actual_keys)
    type_coverage = covered / len(expected_keys)

    # 检查证据内容的质量（简单版本：检查是否有非空值）
    content_score = 0.0
    for key in expected_keys & actual_keys:
        val = evidence.get(key)
        if val is not None and val != "" and val != []:
            content_score += 1.0

    content_quality = content_score / max(len(expected_keys), 1)

    return type_coverage * 0.5 + content_quality * 0.5


def impact_analysis_accuracy(
    predicted_impact: list[str], actual_impact: list[str]
) -> dict[str, float]:
    """
    评估影响范围分析的准确性

    Args:
        predicted_impact: 预测的影响范围列表
        actual_impact: 实际的影响范围列表

    Returns:
        dict: {precision, recall, f1}
    """
    if not predicted_impact and not actual_impact:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not predicted_impact:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    if not actual_impact:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    pred_set = set(predicted_impact)
    actual_set = set(actual_impact)

    intersection = len(pred_set & actual_set)
    precision_val = intersection / len(pred_set) if pred_set else 0.0
    recall_val = intersection / len(actual_set) if actual_set else 0.0
    f1_val = (
        2 * precision_val * recall_val / (precision_val + recall_val)
        if (precision_val + recall_val) > 0
        else 0.0
    )

    return {"precision": precision_val, "recall": recall_val, "f1": f1_val}


# =============================================================================
# 工具调用指标 (Tool Call Metrics)
# =============================================================================


def tool_selection_accuracy(selected: list[str], expected: list[str]) -> float:
    """
    计算工具选择准确率

    Args:
        selected: 实际选择的工具列表
        expected: 期望选择的工具列表

    Returns:
        float: 工具选择准确率 (0.0 - 1.0)
    """
    if not selected and not expected:
        return 1.0
    if not selected or not expected:
        return 0.0

    # 使用集合比较（不考虑顺序）
    selected_set = set(selected)
    expected_set = set(expected)

    intersection = len(selected_set & expected_set)
    union = len(selected_set | expected_set)

    return intersection / union if union > 0 else 0.0


def parameter_accuracy(params: list[dict], expected_params: list[dict]) -> float:
    """
    计算参数正确率

    比较实际传递的参数与期望参数的匹配度。

    Args:
        params: 实际参数字典列表
        expected_params: 期望参数字典列表

    Returns:
        float: 参数正确率 (0.0 - 1.0)
    """
    if not params and not expected_params:
        return 1.0
    if not params or not expected_params:
        return 0.0

    if len(params) != len(expected_params):
        # 长度不一致时，取较短的进行比较
        min_len = min(len(params), len(expected_params))
        params = params[:min_len]
        expected_params = expected_params[:min_len]

    total_score = 0.0
    for actual, expected in zip(params, expected_params):
        expected_keys = set(expected.keys())
        if not expected_keys:
            total_score += 1.0
            continue

        correct_values = 0
        for key in expected_keys:
            if key in actual and actual[key] == expected[key]:
                correct_values += 1

        total_score += correct_values / len(expected_keys)

    return total_score / len(params) if params else 0.0


def tool_execution_success(results: list[dict[str, Any]]) -> float:
    """
    计算工具执行成功率

    Args:
        results: 工具执行结果列表，包含 "success" 字段

    Returns:
        float: 执行成功率 (0.0 - 1.0)
    """
    if not results:
        return 0.0

    successes = sum(1 for r in results if r.get("success", False))
    return successes / len(results)


def tool_call_efficiency(calls: list[Any], minimum_calls: list[Any]) -> float:
    """
    计算工具调用效率

    评估是否使用了最小必要的工具调用次数。
    效率 = min(1.0, minimum_calls / actual_calls)
    如果实际调用次数接近最少必要次数，效率接近1.0。

    Args:
        calls: 实际工具调用列表
        minimum_calls: 最少必要工具调用列表

    Returns:
        float: 调用效率 (0.0 - 1.0)
    """
    actual = len(calls)
    minimum = len(minimum_calls)

    if actual == 0:
        return 1.0 if minimum == 0 else 0.0
    if minimum == 0:
        return 0.0

    return min(1.0, minimum / actual)


def tool_call_sequence_correctness(
    actual_sequence: list[str], expected_sequence: list[str]
) -> float:
    """
    计算工具调用序列正确性

    使用最长公共子序列来衡量调用顺序的正确性。

    Args:
        actual_sequence: 实际调用序列
        expected_sequence: 期望调用序列

    Returns:
        float: 序列正确性 (0.0 - 1.0)
    """
    if not expected_sequence:
        return 1.0 if not actual_sequence else 0.0
    if not actual_sequence:
        return 0.0

    lcs_length = _longest_common_subsequence(actual_sequence, expected_sequence)
    return lcs_length / len(expected_sequence)


# =============================================================================
# RAG 指标 (Retrieval-Augmented Generation Metrics)
# =============================================================================


def retrieval_precision(retrieved: list[str], relevant: list[str]) -> float:
    """
    计算检索精确率

    Args:
        retrieved: 检索到的文档ID列表
        relevant: 相关文档ID列表

    Returns:
        float: 检索精确率 (0.0 - 1.0)
    """
    if not retrieved:
        return 0.0

    retrieved_set = set(retrieved)
    relevant_set = set(relevant)

    intersection = len(retrieved_set & relevant_set)
    return intersection / len(retrieved_set)


def retrieval_recall(retrieved: list[str], relevant: list[str]) -> float:
    """
    计算检索召回率

    Args:
        retrieved: 检索到的文档ID列表
        relevant: 相关文档ID列表

    Returns:
        float: 检索召回率 (0.0 - 1.0)
    """
    if not relevant:
        return 1.0 if not retrieved else 0.0

    retrieved_set = set(retrieved)
    relevant_set = set(relevant)

    intersection = len(retrieved_set & relevant_set)
    return intersection / len(relevant_set)


def context_relevance(context: list[str], query: str) -> float:
    """
    评估上下文相关性

    使用简单的关键词重叠来计算上下文与查询的相关性。
    实际应用中应使用 Embedding 模型计算语义相似度。

    Args:
        context: 检索到的上下文文本列表
        query: 查询文本

    Returns:
        float: 上下文相关性分数 (0.0 - 1.0)
    """
    if not context or not query:
        return 0.0

    query_words = set(query.lower().split())
    if not query_words:
        return 0.0

    relevance_scores = []
    for ctx in context:
        ctx_words = set(ctx.lower().split())
        if not ctx_words:
            relevance_scores.append(0.0)
            continue

        intersection = len(query_words & ctx_words)
        # Jaccard 相似度
        union = len(query_words | ctx_words)
        score = intersection / union if union > 0 else 0.0
        relevance_scores.append(score)

    return float(np.mean(relevance_scores)) if relevance_scores else 0.0


def answer_faithfulness(answer: str, context: list[str]) -> float:
    """
    评估回答忠实度

    检查回答中的内容是否能在上下文中找到依据。
    使用关键词覆盖度作为代理指标。

    Args:
        answer: 生成的回答文本
        context: 检索到的上下文文本列表

    Returns:
        float: 忠实度分数 (0.0 - 1.0)
    """
    if not answer or not context:
        return 0.0

    answer_words = set(answer.lower().split())
    if not answer_words:
        return 0.0

    # 合并所有上下文
    all_context = " ".join(context)
    context_words = set(all_context.lower().split())

    if not context_words:
        return 0.0

    # 计算回答中词汇在上下文中的覆盖度
    covered = len(answer_words & context_words)
    return covered / len(answer_words)


def answer_relevance(answer: str, query: str) -> float:
    """
    评估回答相关性

    计算回答与查询之间的相关性。

    Args:
        answer: 生成的回答文本
        query: 查询文本

    Returns:
        float: 相关性分数 (0.0 - 1.0)
    """
    if not answer or not query:
        return 0.0

    answer_words = set(answer.lower().split())
    query_words = set(query.lower().split())

    if not answer_words or not query_words:
        return 0.0

    intersection = len(answer_words & query_words)
    union = len(answer_words | query_words)

    return intersection / union if union > 0 else 0.0


def context_sufficiency(context: list[str], query: str) -> float:
    """
    评估上下文充分性

    检查检索到的上下文是否足以回答查询。

    Args:
        context: 检索到的上下文文本列表
        query: 查询文本

    Returns:
        float: 充分性分数 (0.0 - 1.0)
    """
    if not context or not query:
        return 0.0

    # 查询中的关键词
    query_words = set(query.lower().split())
    if not query_words:
        return 0.0

    # 合并所有上下文
    all_context = " ".join(context)
    context_words = set(all_context.lower().split())

    if not context_words:
        return 0.0

    # 查询关键词在上下文中的覆盖度
    covered = len(query_words & context_words)
    return covered / len(query_words)


# =============================================================================
# 辅助函数
# =============================================================================


def _longest_common_subsequence(a: list[str], b: list[str]) -> int:
    """
    计算两个列表的最长公共子序列长度

    Args:
        a: 列表A
        b: 列表B

    Returns:
        int: LCS长度
    """
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    return dp[m][n]


def weighted_average(scores: list[float], weights: list[float] | None = None) -> float:
    """
    计算加权平均值

    Args:
        scores: 分数列表
        weights: 权重列表，None时使用等权重

    Returns:
        float: 加权平均值
    """
    if not scores:
        return 0.0

    if weights is None:
        weights = [1.0] * len(scores)

    if len(scores) != len(weights):
        logger.warning("Scores and weights length mismatch, using equal weights")
        weights = [1.0] * len(scores)

    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0

    return sum(s * w for s, w in zip(scores, weights)) / total_weight


# =============================================================================
# 保留向后兼容的 EvaluationMetrics 类
# =============================================================================


class EvaluationMetrics:
    """
    评估指标计算工具 (向后兼容)

    提供常用的评估指标计算方法。
    """

    @staticmethod
    def accuracy(predictions: list[Any], ground_truth: list[Any]) -> float:
        """计算准确率"""
        return accuracy(predictions, ground_truth)

    @staticmethod
    def precision_recall_f1(
        predictions: list[Any], ground_truth: list[Any]
    ) -> dict[str, float]:
        """计算精确率、召回率和 F1"""
        return precision_recall_f1(predictions, ground_truth)

    @staticmethod
    def mean_reciprocal_rank(
        predictions: list[list[Any]], ground_truth: list[Any]
    ) -> float:
        """
        计算平均倒数排名(MRR)

        Args:
            predictions: 排序后的预测列表
            ground_truth: 真实答案

        Returns:
            float: MRR 得分
        """
        if len(predictions) != len(ground_truth) or len(predictions) == 0:
            return 0.0

        rr_sum = 0.0
        for pred_list, gt in zip(predictions, ground_truth):
            for rank, pred in enumerate(pred_list, start=1):
                if pred == gt:
                    rr_sum += 1.0 / rank
                    break

        return rr_sum / len(predictions)

    @staticmethod
    def semantic_similarity(text1: str, text2: str) -> float:
        """
        计算语义相似度

        使用简单的关键词重叠作为占位实现。
        实际应使用 Embedding 模型。

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            float: 相似度 (0-1)
        """
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union)
