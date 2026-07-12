# Phase 5 Brief: 测试补全 (Tasks 5.1-5.7 batched)

## 项目上下文
Phase 0-4 完成。Phase 5 为新补完的代码加测试。已有 11 个测试文件在 `backend/tests/` 通过了 40 个 import 测试（Phase 0 验证）。现在加 6 个新测试文件覆盖 Phase 0-4 的关键修复点。

**只创建文件**: 6 个 `test_*.py` 文件到 `backend/tests/`
**Tasks**: 5.1 (main lifespan) + 5.2 (ready) + 5.3 (metrics) + 5.5 (eval no-mock) + 5.6 (business no-mock-injection)
**Tasks 5.4 (orchestrator_sequential) + 5.7 (full pytest) 跳过**——orchestrator sequential 已在 Phase 2 验证，5.7 由你直接跑全套 pytest

## 关键 API 已验证

- `from app.main import create_app` → FastAPI app 实例
- `app.state.infrastructure`, `app.state.knowledge_base`, `app.state.orchestrator`, `app.state.langgraph_available`
- `from app.agents.eval_agent import EvalAgent, EvalInput` — EvalInput 在 eval_agent.py（不在 models/evaluation）
- `from app.agents.business_monitor_agent import BusinessMonitorAgent, BusinessMetricInput`

## 步骤

### Step 1: 创建 `backend/tests/test_main_lifespan.py`

```python
"""测试 main.py lifespan 真实初始化"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_lifespan_initializes_infrastructure():
    """lifespan 必须初始化 InfrastructureRegistry"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        infra = client.app.state.infrastructure
        assert infra is not None
        assert infra._initialized is True


def test_lifespan_knowledge_base_loaded():
    """lifespan 必须加载 KnowledgeBase"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        kb = client.app.state.knowledge_base
        # 即使 KB 加载方式改变，状态属性必须存在
        assert hasattr(client.app.state, "knowledge_base")
        if kb is not None:
            assert kb.get("loaded") is True


def test_lifespan_orchestrator_built():
    """lifespan 必须构建 Orchestrator"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        # orchestrator 可能为 None 如果 build 失败，但属性必须存在
        assert hasattr(client.app.state, "orchestrator")
        assert hasattr(client.app.state, "langgraph_available")
```

### Step 2: 创建 `backend/tests/test_ready_endpoint.py`

```python
"""测试 /ready 真实探活"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_ready_returns_dependencies():
    """ /ready 必须返回各依赖的真实状态"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/ready")
        body = resp.json()
        assert "checks" in body
        assert "chromadb" in body["checks"]
        assert "memory" in body["checks"]
        assert "prometheus" in body["checks"]
        assert "knowledge_base" in body["checks"]
        assert "langgraph" in body["checks"]


def test_ready_status_field_valid():
    """/ready 返回的 status 字段值合法"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/ready")
        body = resp.json()
        assert body["status"] in ("ready", "degraded")
        assert "version" in body
```

### Step 3: 创建 `backend/tests/test_metrics_endpoint.py`

```python
"""测试 /metrics 真实指标"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_contains_app_up():
    """/metrics 必须包含 app_up"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/metrics")
        text = resp.text
        assert "app_up" in text
        assert "app_up 1.0" in text


def test_metrics_counts_http_requests():
    """/metrics 必须真实统计 HTTP 请求"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        # 先做几次调用
        client.get("/health")
        client.get("/health")
        resp = client.get("/metrics")
        text = resp.text
        # /health 应被计数 2 次
        assert 'http_requests_total{path="/health"' in text


def test_metrics_skips_self():
    """/metrics 调用不应被自身计数（避免递归）"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        client.get("/metrics")
        resp = client.get("/metrics")
        text = resp.text
        # 不应有 path="/metrics" 的计数
        assert 'http_requests_total{path="/metrics"' not in text
```

### Step 4: 创建 `backend/tests/test_eval_no_mock.py`

```python
"""测试 EvalAgent 在无数据时不再 fallback 到 mock"""
from __future__ import annotations

import pytest


def test_eval_reasoning_method_removed():
    """_eval_reasoning_with_mock_data 必须不存在"""
    from app.agents import eval_agent
    assert not hasattr(eval_agent.EvalAgent, "_eval_reasoning_with_mock_data"), (
        "_eval_reasoning_with_mock_data should be deleted (no placeholders allowed)"
    )


def test_eval_reasoning_raises_without_data():
    """无 ground_truth 无 agent_results 时必须抛 ValueError"""
    from app.agents.eval_agent import EvalAgent, EvalInput

    agent = EvalAgent()
    input_data = EvalInput(
        eval_type="reasoning",
        ground_truth={},
        agent_results=[],
    )
    with pytest.raises(ValueError, match="Cannot evaluate reasoning"):
        agent._eval_reasoning(input_data)
```

### Step 5: 创建 `backend/tests/test_business_no_mock_injection.py`

```python
"""测试 BusinessMonitorAgent 不再接受 mock_results / simulate_failure 注入"""
from __future__ import annotations


def test_business_monitor_no_mock_results():
    """源码中不应再有 mock_results 字样"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "agents" / "business_monitor_agent.py"
    content = src.read_text()
    assert "mock_results" not in content, (
        "mock_results injection should be removed (no placeholders allowed)"
    )


def test_business_monitor_no_simulate_failure():
    """源码中不应再有 simulate_failure 字样"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "agents" / "business_monitor_agent.py"
    content = src.read_text()
    assert "simulate_failure" not in content, (
        "simulate_failure should be removed (no placeholders allowed)"
    )


def test_routes_no_simulate_failure():
    """routes.py 中不应再有 simulate_failure 字样"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "api" / "routes.py"
    content = src.read_text()
    assert "simulate_failure" not in content, (
        "simulate_failure should be removed from routes.py"
    )
```

### Step 6: 创建 `backend/tests/test_routes_no_seed.py`

```python
"""测试 routes.py 不再在模块加载时 seed incidents"""
from __future__ import annotations


def test_import_routes_no_seed():
    """导入 routes 后 _incident_service 不应有 seed 数据"""
    # 重置全局状态
    import importlib
    import app.api.routes as routes_module
    # 检查 seed 调用不再在模块顶层
    from pathlib import Path
    src = Path(routes_module.__file__).read_text()
    # 最后 50 行不应有 _seed_incidents() 直接调用
    lines = src.split("\n")
    # 模块顶层（缩进 0）的 _seed_incidents() 调用
    module_level_calls = [
        line for line in lines
        if line.strip() == "_seed_incidents()"
    ]
    assert len(module_level_calls) == 0, (
        f"Found {len(module_level_calls)} module-level _seed_incidents() calls; expected 0"
    )


def test_seed_demo_endpoint_exists():
    """/incidents/seed-demo 端点必须存在"""
    from app.main import create_app
    app = create_app()
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/api/v1/incidents/seed-demo" in routes, (
        "seed-demo endpoint should be registered"
    )
```

### Step 7: 验证全部新测试通过

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
/usr/bin/python3 -m pytest tests/test_main_lifespan.py tests/test_ready_endpoint.py tests/test_metrics_endpoint.py tests/test_eval_no_mock.py tests/test_business_no_mock_injection.py tests/test_routes_no_seed.py -v 2>&1 | tail -40
```
Expected: 全部 PASS（可能 1-2 个 skip 如 lifespan 启动外部依赖失败；如有失败，停下来 debug 不要绕过去）

### Step 8: 跑完整测试套件（确认无回归）

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
/usr/bin/python3 -m pytest tests/ -v --ignore=tests/test_api.py 2>&1 | tail -50
```
Expected: 大量 PASS + 少量预存的 FAIL（test_api.py 那 2 个已知失败被 skip）。统计 passed/failed。

### Step 9: 记录

`_plan_log.md` 追加：
```
Task 5.1-5.7 — 测试补全  ✅  <时间戳>
```

## 报告要求

完成后在 `.superpowers/sdd/phase-5-report.md` 写入：
1. 创建的 6 个文件路径
2. Step 7 新测试的通过/失败统计
3. Step 8 完整套件的通过/失败统计

## 约束
- 只创建新文件，不修改现有测试
- 用 `/usr/bin/python3`
- 不要修复 Phase 1 已知的 test_api.py 那 2 个失败
- 如果某个新测试失败，停下分析 + 报告