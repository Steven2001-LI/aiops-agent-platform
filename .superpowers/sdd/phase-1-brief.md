# Phase 1 Brief: 主链路去 seed (Tasks 1.1 + 1.2)

## 项目上下文
Phase 0 完成。当前 Phase 1 让"用户提交告警"路径不依赖启动时塞的假 incident——把 `_seed_incidents()` 从模块加载时调用改成仅 dev 环境可显式触发的 API 端点。

**项目根目录**: `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/`
**Tasks**: 1.1（删除自动 seed）+ 1.2（加 demo 端点）
**只改文件**: `backend/app/api/routes.py`

## 关键定位（已验证）

- `_seed_incidents()` 函数定义：`routes.py:174-269`
- 模块加载时调用 `_seed_incidents()`：`routes.py:270`
- `/incidents/trigger-business` docstring 含 `"simulate_failure": true`：`routes.py:350`（**Phase 4 才删，本 Phase 不动**）
- `/incidents/trigger-business-scenario` 含 `"simulate_failure": True`：`routes.py:429`（**Phase 4 才删，本 Phase 不动**）
- 第 1458 行的 `simulate_failure` 也在 `business_monitor_agent` 入口，**Phase 4 才动**

## 步骤

### Step 1: 删除模块加载时的 `_seed_incidents()` 自动调用

定位 `routes.py:270` 的：
```python
_seed_incidents()
```

整行删除（含前面的空行）。函数定义 `_seed_incidents()`（第 174-269 行）保留，仅删除这一行调用。

### Step 2: 添加 /incidents/seed-demo 端点

定位 `routes.py` 第 277-328 行的 `trigger_incident` 函数定义结尾（`return {` ... `}` 结束处），紧跟在它**之后**、在下一个 `@api_router.post(...)` 之前插入：

```python
@api_router.post(
    "/incidents/seed-demo",
    response_model=dict[str, Any],
    tags=["Incidents"],
    summary="注入演示故障数据（仅 dev 环境）",
)
async def seed_demo_incidents() -> dict[str, Any]:
    """显式注入 7 个演示 incident。仅在 APP_ENV=development 时可用。"""
    config = get_config()
    if config.env != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="seed-demo endpoint is only available in development environment",
        )
    _seed_incidents()
    return {
        "status": "seeded",
        "count": len(_incident_service._incidents),
        "incident_ids": list(_incident_service._incidents.keys()),
    }
```

### Step 3: 验证 import + import 时不再自动 seed

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
python -c "
from app.api.routes import _incident_service, api_router
print('incidents after import:', len(_incident_service._incidents))
"
```
Expected: `incidents after import: 0`（关键 — 证明 import 时不再 seed）

### Step 4: 验证 dev 环境可调 seed-demo

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
pkill -f "uvicorn.*8001" 2>/dev/null; sleep 1
APP_ENV=development /usr/bin/python3 -m uvicorn app.main:app --port 8001 > /tmp/uvicorn_phase1.log 2>&1 &
sleep 5
curl -s -X POST localhost:8001/api/v1/incidents/seed-demo | python -m json.tool
```
Expected: 返回 7 个 incident_id（注意是 dev 环境所以允许）

```bash
curl -s -X POST -w "\nHTTP %{http_code}\n" localhost:8001/api/v1/incidents/seed-demo  # 二次调用也 OK
```
Expected: HTTP 200，count=7

### Step 5: 验证 prod 环境被拒

```bash
pkill -f "uvicorn.*8001" 2>/dev/null; sleep 1
APP_ENV=production /usr/bin/python3 -m uvicorn app.main:app --port 8001 > /tmp/uvicorn_phase1_prod.log 2>&1 &
sleep 5
curl -s -X POST -w "\nHTTP %{http_code}\n" localhost:8001/api/v1/incidents/seed-demo
```
Expected: HTTP 403 + "seed-demo endpoint is only available in development environment"

```bash
pkill -f "uvicorn.*8001" 2>/dev/null; sleep 1
echo "cleanup done"
```

### Step 6: 跑现有测试看是否有回归

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
python -m pytest tests/test_api.py -v 2>&1 | tail -30
```
记录：通过/失败/skipped 各多少。**不要修复测试失败**——列出失败用例即可，Phase 5 统一处理。

### Step 7: 记录

在项目根目录 `_plan_log.md` 追加：
```
Task 1.1-1.2 — routes.py seed demo 端点  ✅  <时间戳>
```

## 报告要求

完成后在 `.superpowers/sdd/phase-1-report.md` 写入：
1. 修改 diff（routes.py:270 删除 + 新端点插入位置）
2. Step 3-5 三个验证命令的实际输出
3. Step 6 pytest 结果摘要

## 约束
- 只改 `routes.py`，不要碰其他文件
- `simulate_failure` 字段（line 350/429/1458）**不要动**——Phase 4 才删
- 不要修测试失败——列出即可
- 用 `/usr/bin/python3` 启动 uvicorn（避免 Python 3.14 venv 的 import 问题）
- 用端口 8001（8000 可能被前次进程占用）