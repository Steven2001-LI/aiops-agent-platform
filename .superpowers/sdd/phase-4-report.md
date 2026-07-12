# Phase 4 Report — 评测/业务去 mock (Tasks 4.1 + 4.2 + 4.3)

完成时间: 2026-07-10 18:33 CST

## 1. 三个文件 diff 摘要

### `backend/app/agents/eval_agent.py`
- **Step 1** — `_eval_reasoning()` 在 `ground_truth` 与 `agent_results` 均为空时不再回退到 mock：改为 `raise ValueError(...)`，强制要求真实数据。
- **Step 2** — 整段删除 `_eval_reasoning_with_mock_data(self)` 方法定义（约 21 行，含硬编码假数据与固定 `ReasoningMetrics` 返回值）。

### `backend/app/agents/business_monitor_agent.py`
- **Step 3** — `_check_rule()` 删除 `mock_results` 注入分支（约 14 行）：去掉 `mock_data = input_data.context.get("mock_results", {}).get(rule["id"])` 及对应的 `BusinessRuleCheckResult` 提前返回。保留注释说明当前为占位实现，调用方需传入真实业务事件数据。
- **Step 4** — `_simulate_check()` 重写为占位实现：去掉 `simulate_failure` 命中判定、`hashlib` 随机种子散列与 40% 命中率逻辑；统一返回 `(False, 0.0)`。`_check_rule` 中的 `affected_entities` 结构保持不变。

### `backend/app/api/routes.py`
- **Step 5** — `/incidents/trigger-business` 端点 docstring 中的示例 JSON 由 `"context": {"simulate_failure": true}` 改为 `"context": {}`。
- **Step 6** — `/incidents/trigger-business-scenario/{scenario_id}` 实际请求构造中删除 `"simulate_failure": True`，保留 `scenario_id` / `scenario_name` 字段。
- **Step 7** — 自然语言意图路由 `intent == "business_check"` 分支中的 `BusinessMetricInput` 构造：把 `context={"simulate_failure": request.context.get("simulate_failure", True)}` 替换为 `context={}`。

## 2. Step 8 验证输出（修正后）

brief 原脚本中的 `from app.models.evaluation import EvalInput` 是无效 import（`EvalInput` 定义在 `app/agents/eval_agent.py`，`app/models/evaluation.py` 未导出该符号）。这是 brief 自带的脚本缺陷，不属于可改动的 3 个文件，因此校正 import 路径后重跑 5 条断言（语义与 brief 完全一致）：

```
PASS: _eval_reasoning_with_mock_data removed
2026-07-10 18:33:37 [info     ] EvaluationFramework initialized
PASS: raised ValueError as expected: Cannot evaluate reasoning: both ground_truth and agent_results are empty. Provide real agent_results from a completed incident, or pass ground_truth for offline evaluation.
PASS: no mock_results in business_monitor_agent
PASS: no simulate_failure in business_monitor_agent
PASS: no simulate_failure in routes.py
ALL CHECKS PASS
```

附加静态校验：

```
$ grep -c "simulate_failure" app/agents/business_monitor_agent.py app/api/routes.py app/agents/eval_agent.py
app/agents/business_monitor_agent.py:0
app/api/routes.py:0
app/agents/eval_agent.py:0

$ grep -c "mock_results" app/agents/eval_agent.py app/api/routes.py app/agents/business_monitor_agent.py
app/agents/business_monitor_agent.py:0
app/agents/eval_agent.py:0
app/api/routes.py:0
```

## 3. 问题与偏离

1. **brief 验证脚本 import bug**：Step 8 脚本中 `from app.models.evaluation import EvalInput` 路径错误。修正为 `from app.agents.eval_agent import EvalInput`，并与同一行的 `EvalAgent` 合并 import。5 条断言的语义、输入、断言体均未改动。

2. **brief 替换模板自相矛盾**：Step 3 / Step 4 给出的替换模板中包含若干注释文本（"当 context 中存在 'simulate_failure' 时仍走旧的随机逻辑……"、"context.simulate_failure=true → 随机命中部分规则" 等），而 Step 8 断言 #4 同时要求 `business_monitor_agent` 源码中不出现 `simulate_failure` 子串。若严格照贴 brief 字面文本，第 4 条断言必失败。为同时满足"不引入新 mock 行为"与"验证断言通过"两条硬约束，将上述提及历史命名的注释改写为语义描述（仍说明 Phase 4 起不再响应任何外部注入以触发命中；如需触发由调用方传入真实业务事件数据），源码中已不存在 `simulate_failure` 字串。

3. **`routes.py` Step 7 无其他字段需保留**：原 `context` 仅含 `simulate_failure` 一个字段，按 brief 字面替换为 `context={}`，无需保留其他键。

## 4. 约束遵守情况

- 仅修改 3 个目标文件 ✓
- 使用 `/usr/bin/python3` 验证 ✓
- 没有新增任何 mock / simulate 路径（行为面） ✓
- 未修改 `_check_rule` 返回结构中的 `affected_entities` 字段 ✓
- `_plan_log.md` 已追加 `Task 4.1-4.3 — 评测/业务去 mock  ✅  2026-07-10 18:33 CST` ✓
