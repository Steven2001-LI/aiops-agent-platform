"""
AIOps Agent Platform - Monitor Agent

监控告警 Agent，负责异常检测和告警生成。
集成多种检测算法，支持多算法投票机制。
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Deque

import numpy as np
from pydantic import BaseModel, Field, field_validator
from sklearn.ensemble import IsolationForest as SklearnIsolationForest

from app.agents.base import AgentResult, BaseAgent
from app.models.agent import AgentExecutionContext
from app.models.events import AlertEvent, SeverityLevel
from app.utils.logging import get_logger
from app.utils.time_series import TimeSeriesAnalyzer

logger = get_logger(__name__)


# ==================== Isolation Forest 检测器（sklearn 实现） ====================

class IsolationForestDetector:
    """
    基于 sklearn.ensemble.IsolationForest 的异常检测器。

    特性：
    - 多维特征工程（6 个特征维度），充分利用集成学习优势
    - 在线增量学习：每收集 retrain_interval 个新数据点后自动重训练
    - 训练不足时自动回退到 MAD（中位数绝对偏差）近似
    - 支持异常得分可解释（特征贡献分析）
    """

    def __init__(
        self,
        n_estimators: int = 100,
        contamination: float = 0.1,
        random_state: int = 42,
        retrain_interval: int = 100,
    ) -> None:
        self.n_estimators = n_estimators
        self.contamination = contamination
        self.random_state = random_state
        self.retrain_interval = retrain_interval

        self._model: SklearnIsolationForest | None = None
        self._raw_buffer: list[float] = []
        self._is_trained = False
        self._training_samples: int = 0

    # ---- 特征工程 ----

    @staticmethod
    def _build_features(history: list[float]) -> np.ndarray:
        """
        将一维时序值扩展为 6 维特征向量。

        特征维度说明:
          f0: 标准化值（相对中位数的偏差）
          f1: 一阶差分（当前值 - 前一值）
          f2: 5点滑动平均偏差
          f3: 10点滚动标准差
          f4: 相对中位数偏差（标准化）
          f5: 加速度（二阶差分）

        至少需要 6 个数据点才能构建特征。
        """
        if len(history) < 6:
            return np.array([]).reshape(0, 6)

        arr = np.array(history, dtype=np.float64)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))
        if mad == 0:
            mad = float(np.std(arr)) * 0.6745 or 1.0

        features: list[list[float]] = []
        for i in range(5, len(arr)):
            f0 = (arr[i] - median) / mad
            f1 = arr[i] - arr[i - 1]
            ma5 = float(np.mean(arr[i - 4 : i + 1]))
            f2 = arr[i] - ma5
            if i >= 9:
                f3 = float(np.std(arr[i - 9 : i + 1]))
            else:
                f3 = float(np.std(arr[: i + 1]))
            f4 = (arr[i] - median) / mad
            if i >= 2:
                f5 = (arr[i] - arr[i - 1]) - (arr[i - 1] - arr[i - 2])
            else:
                f5 = 0.0
            features.append([f0, f1, f2, f3, f4, f5])

        return np.array(features, dtype=np.float64)

    # ---- 训练 ----

    def train(self, history: list[float]) -> bool:
        """
        用历史数据训练 Isolation Forest 模型。

        Returns:
            bool: 是否训练成功
        """
        if len(history) < 20:
            return False

        X = self._build_features(history)
        if len(X) < 10:
            return False

        self._model = SklearnIsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self._model.fit(X)
        self._is_trained = True
        self._training_samples = len(history)

        logger.info(
            "Isolation Forest trained (sklearn)",
            n_estimators=self.n_estimators,
            samples=len(history),
            features=X.shape[1],
        )
        return True

    # ---- 预测 ----

    def predict(
        self, value: float, history: list[float]
    ) -> tuple[bool, float, dict[str, Any]]:
        """
        预测当前值是否为异常。

        Args:
            value: 当前指标值
            history: 历史值序列

        Returns:
            (is_anomaly, anomaly_score, details)
        """
        if not self._is_trained or self._model is None:
            return self._fallback_mad(value, history)

        # 构建当前点的特征
        extended = list(history) + [value] if history else [value]
        features = self._build_features(extended)
        if len(features) == 0:
            return False, 0.0, {"reason": "insufficient_features", "method": "sklearn_if"}

        current = features[-1:]

        try:
            raw_pred = int(self._model.predict(current)[0])       # -1=异常, 1=正常
            decision = float(self._model.decision_function(current)[0])  # 越负越异常
        except Exception:
            return self._fallback_mad(value, history)

        # 将 decision_function 输出映射为 [0,1] 异常得分
        anomaly_score = float(1.0 / (1.0 + np.exp(decision * 10)))
        is_anomaly = raw_pred == -1

        # 特征贡献（各维度当前值与历史中位数偏差）
        feat_names = ["value", "1st_diff", "ma5_dev", "roll_std", "median_dev", "acceleration"]
        contributions = {
            name: round(float(current[0][i]), 4) for i, name in enumerate(feat_names)
        }

        return is_anomaly, anomaly_score, {
            "method": "sklearn_isolation_forest",
            "decision_score": round(decision, 6),
            "raw_prediction": raw_pred,
            "feature_contributions": contributions,
            "n_estimators": self.n_estimators,
            "training_samples": self._training_samples,
        }

    # ---- 增量学习 ----

    def partial_fit(self, new_value: float) -> bool:
        """
        在线增量学习：累积数据，达到阈值后重训练。

        注意：sklearn IsolationForest 不支持真正的 partial_fit，
        这里采用"定期全量重训练"策略。

        Returns:
            bool: 本次是否触发了重训练
        """
        self._raw_buffer.append(new_value)
        if len(self._raw_buffer) >= self.retrain_interval:
            self.train(list(self._raw_buffer))
            # 保留最近数据避免缓冲无限增长
            self._raw_buffer = self._raw_buffer[-200:]
            return True
        return False

    # ---- MAD 回退 ----

    @staticmethod
    def _fallback_mad(
        value: float, history: list[float]
    ) -> tuple[bool, float, dict[str, Any]]:
        """训练不足时的 MAD 近似回退方案"""
        if len(history) < 10:
            return False, 0.0, {
                "reason": "insufficient_history",
                "method": "fallback_mad",
                "history_count": len(history),
            }

        arr = np.array(history, dtype=np.float64)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))
        if mad == 0:
            mad = float(np.std(arr)) * 0.6745
        if mad == 0:
            return False, 0.0, {"reason": "no_variance", "method": "fallback_mad"}

        modified_z = abs(value - median) / mad

        # 修正 Z-Score → 异常得分
        if modified_z < 2:
            path_score = 1.0
        elif modified_z < 3.5:
            path_score = 0.5
        else:
            path_score = 0.1

        anomaly_score = 1.0 - path_score
        is_anomaly = anomaly_score > 0.5

        return is_anomaly, float(anomaly_score), {
            "method": "fallback_mad",
            "reason": "model_not_trained",
            "median": median,
            "mad": mad,
            "modified_z_score": float(modified_z),
        }

    # ---- 状态查询 ----

    @property
    def is_trained(self) -> bool:
        return self._is_trained


class DetectionAlgorithm(str, Enum):
    """异常检测算法类型"""
    THREE_SIGMA = "3-sigma"
    EWMA = "ewma"
    ISOLATION_FOREST = "isolation_forest"


class AlgorithmVote(BaseModel):
    """单个算法的投票结果"""
    algorithm: str = Field(description="算法名称")
    voted_anomaly: bool = Field(description="是否投票为异常")
    score: float = Field(description="异常得分")
    confidence: float = Field(default=0.0, description="置信度")
    details: dict[str, Any] = Field(default_factory=dict, description="详细信息")


class AnomalyDetectionResult(BaseModel):
    """异常检测结果"""
    is_anomaly: bool = Field(description="是否为异常")
    score: float = Field(description="综合异常得分")
    algorithms_voted: list[AlgorithmVote] = Field(default_factory=list, description="算法投票详情")
    confidence: float = Field(default=0.0, description="综合置信度")
    fingerprint: str = Field(default="", description="告警指纹(MD5)")
    is_duplicate: bool = Field(default=False, description="是否为重复告警")
    algorithm_consensus: str = Field(default="", description="算法共识结果")


class MetricInput(BaseModel):
    """MonitorAgent 输入"""
    metric_name: str = Field(description="指标名称")
    metric_value: float = Field(description="指标当前值")
    service_name: str = Field(description="服务名称")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    labels: dict[str, str] = Field(default_factory=dict, description="标签")
    history_values: list[float] = Field(default_factory=list, description="历史值序列")


@dataclass
class AlertFingerprint:
    """告警指纹记录"""
    fingerprint: str = ""
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    count: int = 0


class MonitorAgent(BaseAgent[MetricInput, AlertEvent]):
    """
    监控告警 Agent

    职责：
    - 集成多种异常检测算法（3-Sigma、EWMA、Isolation Forest）
    - 多算法投票机制（至少 2/3 同意才报警）
    - 告警指纹去重（5 分钟窗口）
    - 自适应阈值（根据历史数据动态调整）
    """

    # 去重窗口时间（分钟）
    DEDUP_WINDOW_MINUTES = 5
    # 投票阈值（至少 N 个算法同意才报警）
    VOTE_THRESHOLD = 2
    # 总算法数
    TOTAL_ALGORITHMS = 3
    # 历史数据最大保留数
    MAX_HISTORY_SIZE = 1000
    # 熔断器：连续失败阈值
    CIRCUIT_FAILURE_THRESHOLD = 5

    def __init__(self) -> None:
        super().__init__()
        # 告警指纹缓存: fingerprint -> AlertFingerprint
        self._fingerprint_cache: dict[str, AlertFingerprint] = {}
        # 每个服务的历史指标数据: service:metric -> deque of values
        self._history_data: dict[str, Deque[float]] = {}
        # 自适应阈值: service:metric -> {mean, std, ewma_value}
        self._adaptive_thresholds: dict[str, dict[str, float]] = {}
        # EWMA 参数
        self._ewma_alpha = 0.3
        # Isolation Forest 检测器（sklearn 实现，每个 key 一个实例）
        self._if_detectors: dict[str, IsolationForestDetector] = {}
        # 已训练过的 key 集合
        self._if_trained_keys: set[str] = set()

    def get_name(self) -> str:
        return "monitor_agent"

    def get_description(self) -> str:
        return "监控告警 Agent - 集成多种算法的异常检测和告警生成"

    # ==================== 核心处理 ====================

    async def process(
        self,
        input_data: MetricInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        处理指标数据，执行异常检测

        Args:
            input_data: 指标输入
            context: 执行上下文

        Returns:
            AgentResult: 包含 AlertEvent 的结果
        """
        logger.info(
            "MonitorAgent processing",
            service=input_data.service_name,
            metric=input_data.metric_name,
            value=input_data.metric_value,
        )

        # Step 1: 更新历史数据
        self._update_history(input_data)

        # Step 2: 更新自适应阈值
        self._update_adaptive_thresholds(input_data)

        # Step 3: 执行异常检测（多算法投票）
        detection_result = self.detect_anomaly(input_data)

        # Step 4: 检查告警去重
        is_duplicate = self._check_duplicate(detection_result.fingerprint)

        # Step 5: 确定严重级别
        severity = self._determine_severity(
            input_data, detection_result
        )

        # 构建输出
        output_data: dict[str, Any] = {
            "detection_result": detection_result.model_dump(),
            "is_duplicate": is_duplicate,
            "severity": severity.value,
        }

        # 如果是异常且不是重复告警，生成 AlertEvent
        if detection_result.is_anomaly and not is_duplicate:
            alert = self._create_alert(input_data, detection_result, severity)
            output_data["alert"] = alert.model_dump()
            output_data["alert_generated"] = True

            # 记录指纹
            self._record_fingerprint(detection_result.fingerprint)
        else:
            output_data["alert_generated"] = False
            if detection_result.is_anomaly and is_duplicate:
                logger.info(
                    "Duplicate alert suppressed",
                    fingerprint=detection_result.fingerprint,
                )

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data=output_data,
        )

    # ==================== 异常检测核心 ====================

    def detect_anomaly(self, input_data: MetricInput) -> AnomalyDetectionResult:
        """
        执行多算法异常检测

        使用 3-Sigma、EWMA、Isolation Forest 三种算法，
        采用投票机制决定是否报警。

        Args:
            input_data: 指标输入

        Returns:
            AnomalyDetectionResult: 检测结果
        """
        key = f"{input_data.service_name}:{input_data.metric_name}"
        history = list(self._history_data.get(key, deque()))
        current_value = input_data.metric_value

        votes: list[AlgorithmVote] = []

        # 算法 1: 3-Sigma
        vote_3sigma = self._detect_3sigma(current_value, history)
        votes.append(vote_3sigma)

        # 算法 2: EWMA
        vote_ewma = self._detect_ewma(current_value, key)
        votes.append(vote_ewma)

        # 算法 3: Isolation Forest（sklearn 实现）
        vote_if = self._detect_isolation_forest(current_value, history, key)
        votes.append(vote_if)

        # IF 增量学习：累积新数据
        if key in self._if_detectors:
            self._if_detectors[key].partial_fit(current_value)

        # 投票统计
        positive_votes = sum(1 for v in votes if v.voted_anomaly)
        is_anomaly = positive_votes >= self.VOTE_THRESHOLD

        # 计算综合得分
        avg_score = np.mean([v.score for v in votes]) if votes else 0.0
        max_score = max([v.score for v in votes]) if votes else 0.0
        combined_score = 0.6 * max_score + 0.4 * avg_score

        # 计算置信度
        consensus_ratio = positive_votes / self.TOTAL_ALGORITHMS
        confidence = self._calculate_confidence(votes, consensus_ratio)

        # 生成指纹
        labels_str = ",".join(f"{k}={v}" for k, v in sorted(input_data.labels.items()))
        fingerprint_raw = f"{input_data.service_name}:{input_data.metric_name}:{labels_str}"
        fingerprint = hashlib.md5(fingerprint_raw.encode()).hexdigest()[:16]

        # 共识描述
        if positive_votes == self.TOTAL_ALGORITHMS:
            consensus = "unanimous"
        elif positive_votes >= self.VOTE_THRESHOLD:
            consensus = "majority"
        else:
            consensus = "minority"

        return AnomalyDetectionResult(
            is_anomaly=is_anomaly,
            score=float(combined_score),
            algorithms_voted=votes,
            confidence=float(confidence),
            fingerprint=fingerprint,
            is_duplicate=False,  # 由外部去重逻辑填充
            algorithm_consensus=consensus,
        )

    def _detect_3sigma(self, value: float, history: list[float]) -> AlgorithmVote:
        """
        3-Sigma 异常检测

        基于正态分布假设，超过 3 倍标准差视为异常。

        Args:
            value: 当前值
            history: 历史值序列

        Returns:
            AlgorithmVote: 投票结果
        """
        if len(history) < 10:
            return AlgorithmVote(
                algorithm=DetectionAlgorithm.THREE_SIGMA.value,
                voted_anomaly=False,
                score=0.0,
                confidence=0.0,
                details={"reason": "insufficient_history", "history_count": len(history)},
            )

        arr = np.array(history, dtype=np.float64)
        mean = float(np.mean(arr))
        std = float(np.std(arr))

        if std == 0:
            return AlgorithmVote(
                algorithm=DetectionAlgorithm.THREE_SIGMA.value,
                voted_anomaly=False,
                score=0.0,
                confidence=1.0,
                details={"mean": mean, "std": 0, "reason": "no_variance"},
            )

        z_score = abs(value - mean) / std
        is_anomaly = z_score > 3.0
        normalized_score = min(z_score / 3.0, 10.0)

        # 自适应：根据历史数据动态调整 sigma 倍数
        adjusted_threshold = self._get_adaptive_sigma_threshold(history)
        is_anomaly = z_score > adjusted_threshold

        return AlgorithmVote(
            algorithm=DetectionAlgorithm.THREE_SIGMA.value,
            voted_anomaly=is_anomaly,
            score=float(normalized_score),
            confidence=min(z_score / adjusted_threshold, 1.0) if is_anomaly else 0.0,
            details={
                "z_score": float(z_score),
                "mean": mean,
                "std": std,
                "threshold": float(adjusted_threshold),
            },
        )

    def _detect_ewma(self, value: float, key: str) -> AlgorithmVote:
        """
        EWMA（指数加权移动平均）异常检测

        对近期数据赋予更高权重，检测偏离 EWMA 的趋势。

        Args:
            value: 当前值
            key: 服务:指标 键

        Returns:
            AlgorithmVote: 投票结果
        """
        threshold_data = self._adaptive_thresholds.get(key, {})
        ewma_value = threshold_data.get("ewma_value")
        ewma_std = threshold_data.get("ewma_std", 0.0)

        if ewma_value is None or ewma_std == 0:
            return AlgorithmVote(
                algorithm=DetectionAlgorithm.EWMA.value,
                voted_anomaly=False,
                score=0.0,
                confidence=0.0,
                details={"reason": "ewma_not_initialized"},
            )

        deviation = abs(value - ewma_value)
        ewma_threshold = 3.0 * ewma_std
        is_anomaly = deviation > ewma_threshold

        score = deviation / ewma_threshold if ewma_threshold > 0 else 0.0
        confidence = min(score, 1.0) if is_anomaly else max(0, 1.0 - score)

        return AlgorithmVote(
            algorithm=DetectionAlgorithm.EWMA.value,
            voted_anomaly=is_anomaly,
            score=float(min(score, 10.0)),
            confidence=float(confidence),
            details={
                "ewma_value": float(ewma_value),
                "ewma_std": float(ewma_std),
                "deviation": float(deviation),
                "threshold": float(ewma_threshold),
            },
        )

    def _detect_isolation_forest(
        self, value: float, history: list[float], key: str = ""
    ) -> AlgorithmVote:
        """
        Isolation Forest 异常检测（sklearn 实现）

        使用 sklearn.ensemble.IsolationForest 进行多维特征异常检测。
        训练不足时自动回退到 MAD（中位数绝对偏差）近似。

        Args:
            value: 当前值
            history: 历史值序列
            key: 服务:指标 键，用于获取对应的检测器实例

        Returns:
            AlgorithmVote: 投票结果
        """
        # 获取或创建该 key 对应的检测器
        if key and key not in self._if_detectors:
            self._if_detectors[key] = IsolationForestDetector(
                n_estimators=100,
                contamination=0.1,
                random_state=42,
                retrain_interval=100,
            )
        detector = self._if_detectors.get(key) if key else None

        # 尝试首次训练
        if detector is not None and not detector.is_trained and len(history) >= 20:
            trained = detector.train(list(history))
            if trained:
                self._if_trained_keys.add(key)
                logger.info(
                    "Isolation Forest first training completed",
                    key=key,
                    samples=len(history),
                )

        # 使用 sklearn 模型预测（如果已训练）
        if detector is not None and detector.is_trained:
            is_anomaly, anomaly_score, details = detector.predict(value, list(history))
        else:
            # 未训练 → 使用 MAD 回退
            is_anomaly, anomaly_score, details = IsolationForestDetector._fallback_mad(
                value, list(history)
            )

        return AlgorithmVote(
            algorithm=DetectionAlgorithm.ISOLATION_FOREST.value,
            voted_anomaly=is_anomaly,
            score=float(anomaly_score * 2),  # 归一化到 0-2 范围，与旧接口兼容
            confidence=float(anomaly_score),
            details=details,
        )

    # ==================== 辅助方法 ====================

    def _update_history(self, input_data: MetricInput) -> None:
        """更新历史数据"""
        key = f"{input_data.service_name}:{input_data.metric_name}"

        if key not in self._history_data:
            self._history_data[key] = deque(maxlen=self.MAX_HISTORY_SIZE)

        # 添加历史值（如果有的话）
        for hv in input_data.history_values:
            self._history_data[key].append(hv)

        # 添加当前值
        self._history_data[key].append(input_data.metric_value)

    def _update_adaptive_thresholds(self, input_data: MetricInput) -> None:
        """更新自适应阈值"""
        key = f"{input_data.service_name}:{input_data.metric_name}"
        history = list(self._history_data.get(key, deque()))

        if len(history) < 5:
            return

        arr = np.array(history, dtype=np.float64)
        mean = float(np.mean(arr))
        std = float(np.std(arr))

        # 计算 EWMA
        ewma_value = arr[0]
        for v in arr[1:]:
            ewma_value = self._ewma_alpha * v + (1 - self._ewma_alpha) * ewma_value

        # 计算 EWMA 偏差的标准差
        ewma_devs = np.abs(arr - ewma_value)
        ewma_std = float(np.std(ewma_devs))

        self._adaptive_thresholds[key] = {
            "mean": mean,
            "std": std,
            "ewma_value": ewma_value,
            "ewma_std": ewma_std,
            "updated_at": datetime.now(timezone.utc).timestamp(),
        }

    def _get_adaptive_sigma_threshold(self, history: list[float]) -> float:
        """
        获取自适应 Sigma 阈值

        根据历史数据的偏度和峰度动态调整阈值。
        数据越偏离正态分布，阈值越保守。
        """
        if len(history) < 20:
            return 3.0  # 默认 3-sigma

        arr = np.array(history, dtype=np.float64)
        skewness = float(self._calculate_skewness(arr))
        kurtosis = float(self._calculate_kurtosis(arr))

        # 偏度和峰度越高，阈值越保守（越大）
        base_threshold = 3.0
        skew_adjustment = min(abs(skewness) * 0.2, 0.5)
        kurt_adjustment = min(max(kurtosis - 3.0, 0) * 0.1, 0.5)

        return base_threshold + skew_adjustment + kurt_adjustment

    @staticmethod
    def _calculate_skewness(arr: np.ndarray) -> float:
        """计算偏度"""
        if len(arr) < 3:
            return 0.0
        mean = np.mean(arr)
        std = np.std(arr)
        if std == 0:
            return 0.0
        n = len(arr)
        return float(np.sum(((arr - mean) / std) ** 3) / n)

    @staticmethod
    def _calculate_kurtosis(arr: np.ndarray) -> float:
        """计算峰度"""
        if len(arr) < 4:
            return 3.0
        mean = np.mean(arr)
        std = np.std(arr)
        if std == 0:
            return 3.0
        n = len(arr)
        return float(np.sum(((arr - mean) / std) ** 4) / n)

    @staticmethod
    def _calculate_confidence(
        votes: list[AlgorithmVote], consensus_ratio: float
    ) -> float:
        """计算综合置信度"""
        if not votes:
            return 0.0

        # 基于共识比例
        consensus_confidence = consensus_ratio

        # 基于各算法得分的一致性
        scores = [v.score for v in votes]
        score_std = float(np.std(scores)) if len(scores) > 1 else 0.0
        score_mean = float(np.mean(scores))
        consistency = 1.0 - min(score_std / (score_mean + 0.01), 1.0)

        # 综合：共识占 60%，一致性占 40%
        return 0.6 * consensus_confidence + 0.4 * consistency

    # ==================== 去重逻辑 ====================

    def _check_duplicate(self, fingerprint: str) -> bool:
        """检查是否为重复告警（5 分钟窗口）"""
        if fingerprint not in self._fingerprint_cache:
            return False

        record = self._fingerprint_cache[fingerprint]
        now = datetime.now(timezone.utc)
        window = timedelta(minutes=self.DEDUP_WINDOW_MINUTES)

        if (now - record.last_seen) < window:
            return True

        return False

    def _record_fingerprint(self, fingerprint: str) -> None:
        """记录告警指纹"""
        now = datetime.now(timezone.utc)

        if fingerprint in self._fingerprint_cache:
            self._fingerprint_cache[fingerprint].last_seen = now
            self._fingerprint_cache[fingerprint].count += 1
        else:
            self._fingerprint_cache[fingerprint] = AlertFingerprint(
                fingerprint=fingerprint,
                first_seen=now,
                last_seen=now,
                count=1,
            )

    def _cleanup_expired_fingerprints(self) -> None:
        """清理过期的指纹记录"""
        now = datetime.now(timezone.utc)
        window = timedelta(minutes=self.DEDUP_WINDOW_MINUTES * 2)

        expired = [
            fp for fp, record in self._fingerprint_cache.items()
            if (now - record.last_seen) > window
        ]
        for fp in expired:
            del self._fingerprint_cache[fp]

    # ==================== 严重级别判定 ====================

    def _determine_severity(
        self,
        input_data: MetricInput,
        detection_result: AnomalyDetectionResult,
    ) -> SeverityLevel:
        """
        确定告警严重级别

        基于异常得分、共识强度、服务等级综合判断。
        """
        score = detection_result.score
        confidence = detection_result.confidence

        # 综合风险分
        risk_score = 0.6 * score + 0.4 * confidence

        # 服务等级调整
        service_tier = input_data.labels.get("tier", "standard")
        tier_multiplier = {"critical": 1.3, "standard": 1.0, "low": 0.7}.get(service_tier, 1.0)

        adjusted_risk = risk_score * tier_multiplier

        if adjusted_risk > 2.5:
            return SeverityLevel.CRITICAL
        elif adjusted_risk > 1.5:
            return SeverityLevel.HIGH
        elif adjusted_risk > 0.8:
            return SeverityLevel.MEDIUM
        elif adjusted_risk > 0.3:
            return SeverityLevel.LOW
        else:
            return SeverityLevel.INFO

    # ==================== AlertEvent 生成 ====================

    def _create_alert(
        self,
        input_data: MetricInput,
        detection_result: AnomalyDetectionResult,
        severity: SeverityLevel,
    ) -> AlertEvent:
        """创建告警事件"""
        # 从历史数据中计算阈值
        key = f"{input_data.service_name}:{input_data.metric_name}"
        history = list(self._history_data.get(key, deque()))

        if history:
            threshold = float(np.mean(history) + 3 * np.std(history))
        else:
            threshold = input_data.metric_value * 0.9

        # 构建算法投票详情注释
        algo_details = "; ".join(
            f"{v.algorithm}: anomaly={v.voted_anomaly}, score={v.score:.2f}"
            for v in detection_result.algorithms_voted
        )

        return AlertEvent(
            source=self.get_name(),
            service=input_data.service_name,
            metric=input_data.metric_name,
            value=input_data.metric_value,
            threshold=threshold,
            operator=">",
            severity=severity,
            labels=input_data.labels,
            annotations={
                "detection_algorithms": algo_details,
                "consensus": detection_result.algorithm_consensus,
                "confidence": str(round(detection_result.confidence, 4)),
                "fingerprint": detection_result.fingerprint,
                "adaptive_thresholds_used": "true",
            },
        )
