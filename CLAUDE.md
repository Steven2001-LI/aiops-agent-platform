# CLAUDE.md — aiops-agent-platform 工作规则

## 项目

多智能体运维故障定位系统(FastAPI + LangGraph + ChromaDB)。
当前主线:按仓库根目录《LLM集成方案.md》分阶段(D1-D7)把系统从纯规则引擎
升级为"LLM 推理 + 规则兜底"混合架构。动手前先读方案对应章节。

## 环境与命令

- Python 环境:`backend/.venv`(Python 3.11,uv 管理),不要新建其他虚拟环境
- 跑测试:`cd backend && .venv/bin/pytest -q`
- 装依赖:`uv pip install <pkg>`,并同步更新 `backend/requirements.txt`

## 测试纪律(最重要)

- 当前基线:**0 failed / 313 passed / 1 skipped**(2026-07 修复 4 个历史遗留失败后
  的实测值;历史演变:251 → LLM 集成后 309 → 基线归零 313)。**零失败是硬基线**,
  任何任务完成后全量 pytest 出现任何失败都算回归,不许绕过、不许 skip 掉。
- 测试禁止触碰真实云端:Langfuse 在 tests/conftest.py 强制禁用,勿移除该守卫
- 每次任务完成必须跑全量 pytest,报告与基线的对比
- 新功能的测试禁止调用真实 LLM API,用 fake client 按脚本返回
- 禁止用 mock 注入业务结果糊弄断言(仓库有 `test_*_no_mock*` 传统,延续它)

## LLM 集成全局原则

- `LLM_ENABLE_RCA` / `LLM_ENABLE_NLU` / `LLM_ENABLE_JUDGE` 三个开关默认 `false`
- 所有 LLM 输出必须经 `llm_service.structured_completion`:
  schema 校验 + 校验错误反馈自修复重试
- LLM 不可用必须降级到现有规则路径;`LLMUnavailableError` 不许冒泡到 API 层
- 闭集约束:根因 / 服务名 / 症状只能从系统给出的候选列表中选择

## 范围与安全

- 每次会话开头用户会给出本阶段文件白名单,白名单外的文件一律不改
- 禁止读取或打印 `backend/.env` 的内容(内含真实 API key)
- 禁止自行 `git commit` / `git push`;修改完成等用户审查后手动提交
- 不动 `.superpowers/`、`docs/`、`frontend/`(除非用户明确要求)

## 代码风格

- 沿用现有约定:pydantic v2、structlog(`app.utils.logging.get_logger`)、
  async 优先、中文 docstring
- 新增代码必须带完整类型注解
