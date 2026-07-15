"""RAG 语义指标测试:语义余弦主路径(桩向量)+ 词面回退契约

语义路径不依赖真实 sentence-transformers 模型——用 monkeypatch 桩掉
metrics._encode 返回手工构造的单位向量,验证映射(mean/max/cos)与
[0,1] 截断;回退路径断言与词面实现完全一致。
"""

from __future__ import annotations

import pytest

import app.evaluation.metrics as metrics
from app.evaluation.metrics import (
    answer_faithfulness,
    answer_relevance,
    context_relevance,
    context_sufficiency,
    rag_metric_mode,
)


class TestLexicalFallback:
    """EVAL_RAG_SEMANTIC=0(conftest 默认)下的词面精确值"""

    def test_lexical_exact_values(self) -> None:
        # Jaccard("a b", "a c") = 1/3
        assert answer_relevance("a b", "a c") == pytest.approx(1 / 3)
        # answer 词覆盖度:{a,b} 中 a 在 context → 0.5
        assert answer_faithfulness("a b", ["a x"]) == pytest.approx(0.5)
        # query 词覆盖度:{a,b} 中 a 在 context → 0.5
        assert context_sufficiency(["a x"], "a b") == pytest.approx(0.5)
        # Jaccard({a,b},{a,x}) = 1/3
        assert context_relevance(["a x"], "a b") == pytest.approx(1 / 3)

    def test_metric_mode_reports_lexical(self) -> None:
        assert rag_metric_mode() == "lexical"


class TestSemanticPath:
    """桩向量验证语义映射与截断,不触真实模型"""

    def test_mean_and_max_mappings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 首向量是 query/answer,后续是 context
        vectors = {
            "q": [1.0, 0.0],
            "ctx_same": [1.0, 0.0],
            "ctx_orth": [0.0, 1.0],
        }

        def fake_encode(texts: list[str]) -> list[list[float]]:
            return [vectors[t] for t in texts]

        monkeypatch.setattr(metrics, "_encode", fake_encode)

        # relevance = mean(cos) = (1.0 + 0.0) / 2
        assert context_relevance(["ctx_same", "ctx_orth"], "q") == pytest.approx(0.5)
        # sufficiency = max(cos)
        assert context_sufficiency(["ctx_same", "ctx_orth"], "q") == pytest.approx(1.0)
        # faithfulness = max(cos(answer, ctx))
        assert answer_faithfulness("q", ["ctx_orth", "ctx_same"]) == pytest.approx(1.0)
        # answer_relevance = cos(answer, query)
        assert answer_relevance("q", "ctx_same") == pytest.approx(1.0)

    def test_negative_cosine_clamped_to_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_encode(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0]] + [[-1.0, 0.0]] * (len(texts) - 1)

        monkeypatch.setattr(metrics, "_encode", fake_encode)
        assert context_relevance(["opposite"], "q") == 0.0
        assert answer_relevance("a", "b") == 0.0

    def test_encode_failure_falls_back_to_lexical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """语义能力不可用(_encode 返回 None)→ 与词面实现完全一致"""
        monkeypatch.setattr(metrics, "_encode", lambda texts: None)

        assert answer_relevance("a b", "a c") == pytest.approx(
            metrics._lexical_answer_relevance("a b", "a c")
        )
        assert answer_faithfulness("a b", ["a x"]) == pytest.approx(
            metrics._lexical_answer_faithfulness("a b", ["a x"])
        )
        assert context_relevance(["a x"], "a b") == pytest.approx(
            metrics._lexical_context_relevance(["a x"], "a b")
        )
        assert context_sufficiency(["a x"], "a b") == pytest.approx(
            metrics._lexical_context_sufficiency(["a x"], "a b")
        )


class TestEmptyInputs:
    """空输入在两种模式下都保持 0.0 早退语义"""

    def test_empty_inputs_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for mode_stub in (None, lambda texts: [[1.0, 0.0]] * len(texts)):
            if mode_stub is not None:
                monkeypatch.setattr(metrics, "_encode", mode_stub)
            assert context_relevance([], "q") == 0.0
            assert context_relevance(["ctx"], "") == 0.0
            assert answer_faithfulness("", ["ctx"]) == 0.0
            assert answer_faithfulness("ans", []) == 0.0
            assert answer_relevance("", "q") == 0.0
            assert context_sufficiency([], "q") == 0.0
