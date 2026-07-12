# Phase 5 Report — 测试补全 (BLOCKED)

## Status: BLOCKED

Step 7 有 2 个新测试失败,按约束停止,未运行 Step 8。

## 创建的 6 个文件

1. `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend/tests/test_main_lifespan.py`
2. `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend/tests/test_ready_endpoint.py`
3. `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend/tests/test_metrics_endpoint.py`
4. `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend/tests/test_eval_no_mock.py`
5. `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend/tests/test_business_no_mock_injection.py`
6. `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend/tests/test_routes_no_seed.py`

## Step 7: 新测试统计

**PASSED**: 13 / **FAILED**: 2 / **TOTAL**: 15

### 失败 #1: `tests/test_metrics_endpoint.py::test_metrics_counts_http_requests`

- 断言:
  `assert 'http_requests_total{path="/health"' in text`
- 原因: prometheus 客户端输出 label 顺序为 `method="GET",path="/health",status="200"`,`path` 不是第一个 label,所以测试断言的子串 `http_requests_total{path="/health"` 不出现。指标实际被记录,只是 label 顺序不同。
- 节选实际输出:
  - `http_requests_total{method="GET",path="/ready",status="200...`
  - `http_requests_total{method="GET",path="/health",status="200...`
- 同文件其它 2 个测试 (`test_metrics_contains_app_up`, `test_metrics_skips_self`) PASSED。

### 失败 #2: `tests/test_routes_no_seed.py::test_import_routes_no_seed`

- 断言:
  `assert len(module_level_calls) == 0, ...`,其中 `module_level_calls` 收集所有 `line.strip() == "_seed_incidents()"` 的行。
- 原因: `backend/app/api/routes.py` 源码中存在 1 行 `    _seed_incidents()`(strip 后等于 `_seed_incidents()`),通常位于函数体或 `if __name__ == "__main__":` 块内,不是真正的模块顶层执行,但因 strip 后字符串完全相等,被测试视为“模块级调用”。
- 错误信息:
  - `AssertionError: Found 1 module-level _seed_incidents() calls; expected 0`
  - `assert 1 == 0`
  - `+ where 1 = len(['    _seed_incidents()'])`
- 同文件另 1 个测试 (`test_seed_demo_endpoint_exists`) PASSED。

## Step 8: 未执行

按约束 "If Step 7 fails on any test, STOP and report BLOCKED with the exact failure",未运行 Step 8 全量回归。

## 备注

- 6 个新文件严格按 brief 提供的代码写入,未做任何修改。
- 所有失败都是断言逻辑与现有源码实际行为不完全对齐,需要 brief 提供者决定:
  1. 是否接受 metrics label 顺序差异(放宽/修正断言),或修复源码 label 顺序;
  2. `_seed_incidents()` 是否应从 routes.py 中完全删除,或调整断言以忽略非顶层调用。