"""app/tests 共享配置(与 tests/conftest.py 的确定性口径保持一致)"""

from __future__ import annotations

import os

# 测试确定性:RAG 语义指标固定走词面回退(理由见 tests/conftest.py)
os.environ.setdefault("EVAL_RAG_SEMANTIC", "0")
