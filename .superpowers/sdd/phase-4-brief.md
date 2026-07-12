# Phase 4 Brief: 评测与业务检测去 mock (Tasks 4.1 + 4.2 + 4.3)

## 项目上下文
Phase 0-3 完成。Phase 4 删除所有 mock 占位，让"评测需要真实数据"和"业务检测不能被注入"成为硬约束。

**只改文件**:
- `backend/app/agents/eval_agent.py`（Task 4.1 — 删 _eval_reasoning_with_mock_data）
- `backend/app/agents/business_monitor_agent.py`（Task 4.2 — 删 mock_results 注入 + simulate_failure）
- `backend/app/api/routes.py`（Task 4.3 — 删 simulate_failure 字段的端点传递）

**Tasks**: 4.1 + 4.2 + 4.3

## 已验证的当前位置

- `eval_agent.py:499-500` — `_eval_reasoning_with_mock_data()` 调用点
- `eval_agent.py:608-628` — `_eval_reasoning_with_mock_data()` 方法定义
- `business_monitor_agent.py:338-351` — `mock_results` 注入分支（17 行）
- `business_monitor_agent.py:372-393` — `_simulate_check`（含 simulate_failure 逻辑）
- `routes.py:367-371` — `/incidents/trigger-business` docstring 含 `"simulate_failure": true`
- `routes.py:445-450` — `/incidents/trigger-business-scenario/{scenario_id}` 实际 context 传 simulate_failure
- `routes.py:1475-1479` — 另一个端点 context 传 simulate_failure

## 步骤

### Step 1: 删除 `_eval_reasoning_with_mock_data` 的调用点

定位 `eval_agent.py:499-500`：
```python
        if not ground_truth and not agent_results:
            return self._eval_reasoning_with_mock_data()
```

替换为：
```python
        if not ground_truth and not agent_results:
            raise ValueError(
                "Cannot evaluate reasoning: both ground_truth and agent_results are empty. "
                "Provide real agent_results from a completed incident, or pass ground_truth for offline evaluation."
            )
```

### Step 2: 删除 `_eval_reasoning_with_mock_data` 方法定义

定位 `eval_agent.py:608-628`（从 `def _eval_reasoning_with_mock_data(self) -> ReasoningMetrics:` 开始到 `return ReasoningMetrics(...)` 结束），**整段删除**（包括方法定义 + docstring + 函数体）。

### Step 3: 删除 `business_monitor_agent.py` 的 `mock_results` 注入分支

定位 `business_monitor_agent.py:338-351`（从 `# 从上下文获取模拟数据` 注释到 `suggested_actions=rule["suggested_actions"],` 行结束的 `return BusinessRuleCheckResult(...)`），整段替换为：

```python
        # 真实业务规则检测（已无 mock 注入路径）
        # 当 context 中存在 'simulate_failure' 时仍走旧的随机逻辑（已弃用，Phase 4 删除）
        # 当前：matched = False 表示规则未命中；如需触发，调用方应提供真实业务事件数据
        matched, confidence = self._simulate_check(rule, input_data)
```

### Step 4: 修改 `_simulate_check` 让 simulate_failure 不再生效

定位 `business_monitor_agent.py:372-393` 的 `_simulate_check` 方法，整段替换为：

```python
    @staticmethod
    def _simulate_check(
        rule: dict[str, Any],
        input_data: BusinessMetricInput,
    ) -> tuple[bool, float]:
        """
        规则检查占位实现（生产环境需对接业务数据库/日志/消息队列）。

        当前为简化实现：默认返回未命中（matched=False）。
        未来对接真实数据源后，此方法将执行 rule.get("check_sql") 中的 SQL 查询
        或调用对应的业务事件 stream 读取接口。
        """
        # Phase 4 删除 simulate_failure 注入路径
        # 历史行为（已移除）：
        #   - context.simulate_failure=true → 随机命中部分规则
        #   - context.simulate_failure=false → 全部正常
        # 当前统一返回未命中，等待真实业务数据源接入
        return False, 0.0
```

### Step 5: 删除 `routes.py` `/incidents/trigger-business` docstring 中的 simulate_failure

定位 `routes.py:367-371` 附近的：
```
      "context": {"simulate_failure": true}
```

替换为：
```
      "context": {}
```

（仅修改 docstring 示例，不影响实际代码逻辑）

### Step 6: 删除 `routes.py` `/incidents/trigger-business-scenario` 的 simulate_failure

定位 `routes.py:445-450` 附近（约 line 445-450）：
```python
        context={
            "simulate_failure": True,
            "scenario_id": scenario_id,
            "scenario_name": scenario["name"],
        },
```

替换为：
```python
        context={
            "scenario_id": scenario_id,
            "scenario_name": scenario["name"],
        },
```

### Step 7: 找到 line 1475-1479 的第三个 simulate_failure 引用

定位 `routes.py:1475-1479`：
```python
            context={"simulate_failure": request.context.get("simulate_failure", True)},
```

替换为：
```python
            context={},
```

（如果 request 有 context dict 中其他字段，保留它们）

### Step 8: 验证

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
/usr/bin/python3 -c "
from app.agents.eval_agent import EvalAgent
from app.agents.business_monitor_agent import BusinessMonitorAgent, BusinessMetricInput
from app.agents import eval_agent as eval_module

# 1. _eval_reasoning_with_mock_data 必须不存在
assert not hasattr(EvalAgent, '_eval_reasoning_with_mock_data'), 'mock data method still present'
print('PASS: _eval_reasoning_with_mock_data removed')

# 2. EvalAgent._eval_reasoning 在无数据时抛 ValueError
from app.models.evaluation import EvalInput
agent = EvalAgent()
try:
    agent._eval_reasoning(EvalInput(eval_type='reasoning', ground_truth={}, agent_results=[]))
    print('FAIL: should have raised ValueError')
except ValueError as e:
    print(f'PASS: raised ValueError as expected: {e}')

# 3. business_monitor_agent 没有 mock_results 引用
import inspect
src = inspect.getsource(BusinessMonitorAgent)
assert 'mock_results' not in src, 'mock_results still present in business_monitor_agent'
print('PASS: no mock_results in business_monitor_agent')

# 4. business_monitor_agent 没有 simulate_failure
assert 'simulate_failure' not in src, 'simulate_failure still present'
print('PASS: no simulate_failure in business_monitor_agent')

# 5. routes.py 没有 simulate_failure
with open('app/api/routes.py') as f:
    routes_src = f.read()
assert 'simulate_failure' not in routes_src, 'simulate_failure still present in routes.py'
print('PASS: no simulate_failure in routes.py')

print('ALL CHECKS PASS')
"
```
Expected: 5 个 PASS，最终输出 `ALL CHECKS PASS`

### Step 9: 记录

`_plan_log.md` 追加：
```
Task 4.1-4.3 — 评测/业务去 mock  ✅  <时间戳>
```

## 报告要求

完成后在 `.superpowers/sdd/phase-4-report.md` 写入：
1. 3 个文件 diff 摘要
2. Step 8 验证输出
3. 任何问题

## 约束
- 只改 3 个文件
- 用 `/usr/bin/python3` 验证
- 不能新增任何 mock / simulate 路径
- 不要修改 _check_rule 的 `affected_entities` 字段结构