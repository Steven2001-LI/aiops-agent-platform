# Task 0.1 Brief: Fix conftest.py 硬编码路径

## 项目上下文
aiops-agent-platform 后端的占位实现清理优化任务。共 21 个 Task，本任务是第 1 个（Phase 0 启动底座的先决条件）。

**项目根目录**: `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/`
**当前 Task 文件**: `backend/tests/conftest.py:13`

## 任务

把硬编码的 Linux 路径 `/mnt/agents/output/aiops-agent-platform/backend` 改成相对路径，让 pytest 在 macOS/Linux 本机都能跑。

## 文件
- Modify: `backend/tests/conftest.py:13`

## 接口
- Consumes: 无（独立任务）
- Produces: 任何 `from app.* import ...` 在 pytest 中可用

## 步骤

### Step 1: 修改 sys.path 为相对路径

打开 `backend/tests/conftest.py`，找到第 13 行：
```python
sys.path.insert(0, "/mnt/agents/output/aiops-agent-platform/backend")
```

替换为：
```python
import pathlib
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))
```

### Step 2: 验证 import 可用

Run:
```bash
cd backend && python -c "import sys; sys.path.insert(0, '.'); from app.config import get_config; print('OK')"
```
Expected: `OK`（无 ImportError）

### Step 3: 跑现有测试套件确认无回归

Run:
```bash
cd backend && python -m pytest tests/test_imports.py -v 2>&1 | tail -20
```
Expected: 全部 PASS 或 SKIPPED（无 ImportError / Collection Error）

### Step 4: 在 `_plan_log.md` 记录完成时间戳

如项目无 git，在项目根目录创建 `_plan_log.md`，追加：
```
Task 0.1 — conftest.py 修路径  ✅  <时间戳>
```

## 报告要求

实现完成后，在 `.superpowers/sdd/task-0.1-report.md` 写入：
1. 修改前/后 conftest.py 第 13 行代码 diff
2. Step 2 验证命令的实际输出（首末 5 行）
3. Step 3 pytest 输出的汇总（passed/failed/error 各多少）
4. Step 4 _plan_log.md 内容
5. 任何遇到的问题和你的判断

## 约束
- 不要修改 conftest.py 的其他部分（fixtures 等保留不变）
- 不要运行除 task 步骤外的额外测试（节省时间）
- 如果遇到 import 错误无法解决，在报告里说明并停止