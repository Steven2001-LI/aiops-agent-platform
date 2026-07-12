# AIOps Agent Platform

多智能体智能化运维故障定位系统 - 使用 Python + FastAPI + LangGraph 构建。

## 项目概述

AIOps Agent Platform 是一个基于多智能体架构的智能化运维故障定位系统。系统通过多个
专业 Agent 协作，实现从告警接收、根因分析到故障自愈的全流程自动化处理。

### 核心架构

```
                    +------------------+
                    |   Alert Source   |
                    | (Prometheus/...) |
                    +--------+---------+
                             |
                             v
+--------------------------------------------------+
|              Orchestrator (LangGraph)             |
|  [receive_alert] -> [triage] -> [rca] -> [heal] |
+--+-----------+-----------+-----------+-----------+
   |           |           |           |
   v           v           v           v
+------+  +--------+  +---------+  +---------+
|Monitor|  |  RCA   |  |  Heal   |  | Change |
| Agent |  | Agent  |  | Agent   |  | Agent  |
+-------+  +--------+  +---------+  +---------+
                             |
                             v
                    +------------------+
                    |  Memory Agent    |
                    |  Eval Agent      |
                    +------------------+
```

### Agent 职责

| Agent | 职责 |
|-------|------|
| **Monitor Agent** | 告警接收、去重、分级 |
| **RCA Agent** | 根因分析、影响链路构建 |
| **Heal Agent** | 故障自愈操作执行 |
| **Change Agent** | 变更风险评估和审批 |
| **Memory Agent** | 记忆存储和检索 |
| **Eval Agent** | 效果评估和持续优化 |

## 技术栈

- **Web 框架**: FastAPI + Uvicorn
- **Agent 框架**: LangGraph + LangChain
- **LLM**: OpenAI GPT-4o / DeepSeek / 其他
- **向量数据库**: ChromaDB
- **可观测性**: Langfuse
- **数据科学**: NumPy, scikit-learn, Pandas
- **测试**: pytest, pytest-asyncio
- **前端**: React 18 + TypeScript + Vite + Tailwind CSS
- **部署**: Docker + Docker Compose

## 项目结构

```
aiops-agent-platform/
├── backend/                        # FastAPI 后端
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口
│   │   ├── config.py               # 配置管理 (pydantic-settings)
│   │   ├── dependencies.py         # 依赖注入
│   │   ├── models/                 # 数据模型
│   │   ├── agents/                 # Agent 实现
│   │   │   ├── base.py             # Agent 抽象基类
│   │   │   ├── orchestrator.py     # LangGraph 编排器
│   │   │   ├── monitor_agent.py    # 监控告警 Agent
│   │   │   ├── rca_agent.py        # 根因分析 Agent
│   │   │   ├── heal_agent.py       # 故障自愈 Agent
│   │   │   ├── change_agent.py     # 变更审批 Agent
│   │   │   ├── memory_agent.py     # 记忆管理 Agent
│   │   │   └── eval_agent.py       # 评估 Agent
│   │   ├── tools/                  # 工具集
│   │   ├── memory/                 # 记忆系统
│   │   ├── evaluation/             # 评估框架
│   │   ├── services/               # 业务服务
│   │   ├── data/                   # 数据集和知识库
│   │   ├── utils/                  # 工具函数
│   │   └── api/                    # API 路由
│   ├── requirements.txt
│   ├── Dockerfile
│   └── start.sh
├── frontend/                       # React 前端
│   ├── src/                        # 源代码
│   ├── public/                     # 静态资源
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   ├── nginx.conf                  # Nginx 配置
│   └── Dockerfile
├── docker-compose.yml              # 生产环境编排
├── docker-compose.dev.yml          # 开发环境编排
├── .env.example                    # 环境变量模板
├── deploy.sh                       # 一键部署脚本
├── .gitignore
└── README.md                       # 项目说明
```

## 快速开始

### 环境要求

- Python 3.11+ (本地开发)
- Node.js 20+ (本地开发)
- Docker 24.0+ & Docker Compose v2+ (推荐)
- 至少 4GB 可用内存

---

## Docker 部署（推荐）

### 前置要求

- 安装 [Docker](https://docs.docker.com/get-docker/)
- 安装 [Docker Compose](https://docs.docker.com/compose/install/)
- 获取 LLM API Key（OpenAI 或 DeepSeek）

### 1. 克隆项目

```bash
git clone <repository-url>
cd aiops-agent-platform
```

### 2. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入你的 API Key
vi .env
```

### 3. 一键部署

```bash
# 赋予执行权限并运行部署脚本
chmod +x deploy.sh
./deploy.sh
```

部署脚本支持以下命令：

| 命令 | 说明 |
|------|------|
| `./deploy.sh` | 生产环境部署（默认） |
| `./deploy.sh dev` | 开发环境部署（支持热重载） |
| `./deploy.sh stop` | 停止生产环境服务 |
| `./deploy.sh stop-dev` | 停止开发环境服务 |
| `./deploy.sh restart` | 重启服务 |
| `./deploy.sh logs` | 查看实时日志 |
| `./deploy.sh logs-dev` | 查看开发环境日志 |
| `./deploy.sh status` | 查看服务状态和资源使用 |
| `./deploy.sh health` | 健康检查 |
| `./deploy.sh cleanup` | 清理所有容器和数据（谨慎使用） |
| `./deploy.sh help` | 显示帮助信息 |

### 4. 服务访问

部署完成后，可通过以下地址访问服务：

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端界面 | http://localhost:8080 | React SPA（Nginx） |
| 后端 API | http://localhost:8000 | FastAPI + Uvicorn |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| ReDoc | http://localhost:8000/redoc | 替代 API 文档 |
| ChromaDB | http://localhost:8001 | 向量数据库 |

### 5. Docker Compose 手动部署

如需手动控制部署过程：

```bash
# 生产环境
docker-compose -f docker-compose.yml up -d --build

# 开发环境（热重载）
docker-compose -f docker-compose.dev.yml up -d --build

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down

# 停止并清理数据卷（谨慎）
docker-compose down -v
```

### 6. 验证部署

```bash
# 检查服务健康状态
curl http://localhost:8000/health

# 检查后端就绪状态
curl http://localhost:8000/ready

# 查看API文档（如果配置了OPEN API）
# 浏览器访问 http://localhost:8000/docs
```

---

## 环境变量说明

所有环境变量可通过 `.env` 文件配置，以下为主要配置项：

### LLM 配置（必填）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | 主 LLM API 密钥 | - |
| `LLM_PROVIDER` | LLM 提供商：`openai` / `deepseek` / `azure-openai` | `openai` |
| `LLM_MODEL` | 模型名称 | `gpt-4o-mini` |

### 特定提供商配置（可选）

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | OpenAI 专用 API Key |
| `DEEPSEEK_API_KEY` | DeepSeek 专用 API Key |

### Langfuse 配置（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LANGFUSE_PUBLIC_KEY` | Langfuse Public Key | - |
| `LANGFUSE_SECRET_KEY` | Langfuse Secret Key | - |
| `LANGFUSE_HOST` | Langfuse 服务地址 | `https://cloud.langfuse.com` |
| `LANGFUSE_ENABLED` | 是否启用 Langfuse | `false` |

### 应用配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `APP_ENV` | 运行环境：`development` / `production` | `development` |
| `APP_LOG_LEVEL` | 日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |

### 高级配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENT_EXECUTION_TIMEOUT_SECONDS` | Agent 执行超时 | `120` |
| `AGENT_MAX_ITERATIONS` | 最大迭代次数 | `10` |
| `AGENT_HEAL_DRY_RUN` | 自愈操作仅模拟 | `true` |
| `DATABASE_URL` | 数据库连接 URL | `sqlite:///./data/aiops.db` |
| `CHROMA_DB_HOST` | ChromaDB 主机 | `chroma` |
| `WS_MAX_CONNECTIONS` | WebSocket 最大连接数 | `100` |

---

## 本地开发

### 后端开发

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key

# 启动服务（热重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端开发

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 前端服务默认运行在 http://localhost:3000
```

### 开发环境特性

- **后端热重载**：修改代码后自动重启服务
- **前端热更新**：修改代码后浏览器自动刷新
- **API 代理**：开发服务器自动代理 `/api` 和 `/ws` 到后端
- **完整 API 文档**：访问 http://localhost:8000/docs

---

## API 端点

### 故障管理

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/incidents/trigger` | 触发故障处理 |
| GET | `/api/v1/incidents/{id}` | 查询故障状态 |
| GET | `/api/v1/incidents` | 故障列表 |

### Agent 管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/agents` | Agent 列表 |
| GET | `/api/v1/agents/{id}/status` | Agent 状态 |

### 评估

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/evaluations` | 评估结果 |
| POST | `/api/v1/evaluations/run` | 执行评估 |

### 记忆

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/memory/search` | 记忆搜索 |
| POST | `/api/v1/memory/store` | 存储记忆 |

### WebSocket

| 端点 | 说明 |
|------|------|
| `/ws` | 通用 WebSocket（支持订阅） |
| `/ws/incidents/{id}` | 故障实时更新 |

### 健康检查

| 端点 | 说明 |
|------|------|
| `/health` | 健康状态 |
| `/ready` | 就绪状态 |

---

## 开发指南

### 添加新 Agent

1. 在 `app/agents/` 下创建新的 Agent 文件
2. 继承 `BaseAgent` 抽象基类
3. 实现 `process()`, `get_name()`, `get_description()` 方法
4. 在 `app/agents/__init__.py` 中导出

```python
from app.agents.base import AgentResult, BaseAgent
from app.models.agent import AgentExecutionContext

class MyAgent(BaseAgent[MyInput, MyOutput]):
    def get_name(self) -> str:
        return "my_agent"

    def get_description(self) -> str:
        return "My agent description"

    async def process(self, input_data, context) -> AgentResult:
        # 实现处理逻辑
        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={"result": "success"},
        )
```

### 添加新工具

1. 在 `app/tools/` 下创建新的工具文件
2. 继承 `BaseTool` 抽象基类
3. 实现 `name`, `description`, `execute` 属性/方法
4. 在 `app/tools/__init__.py` 中导出

---

## 测试

```bash
cd backend

# 运行所有测试
pytest

# 运行特定模块测试
pytest tests/test_agents/

# 带覆盖率报告
pytest --cov=app --cov-report=html

# 在Docker中运行测试
docker-compose -f docker-compose.dev.yml run --rm backend-dev pytest
```

---

## Docker 架构说明

### 服务架构

```
┌─────────────────────────────────────────────┐
│              Docker Network                  │
│         (aiops-network / bridge)            │
│                                             │
│  ┌──────────────┐    ┌──────────────┐      │
│  │   Frontend   │    │   Backend    │      │
│  │  (Nginx:80)  │◄──►│(Uvicorn:8000)│      │
│  │   :8080      │    │   :8000      │      │
│  └──────────────┘    └──────┬───────┘      │
│                             │               │
│                             ▼               │
│                      ┌──────────────┐      │
│                      │   ChromaDB   │      │
│                      │   :8000      │      │
│                      └──────────────┘      │
│                         :8001               │
└─────────────────────────────────────────────┘
```

### 镜像说明

| 镜像 | 基础 | 说明 |
|------|------|------|
| aiops-backend | `python:3.11-slim` | 多阶段构建，非root运行 |
| aiops-frontend | `node:20-alpine` + `nginx:alpine` | 多阶段构建，SPA路由支持 |
| aiops-chroma | `chromadb/chroma:latest` | 向量数据库 |

### 数据持久化

| 卷名 | 用途 | 挂载路径 |
|------|------|----------|
| `aiops-data` | SQLite数据库、日志 | `/app/data` |
| `chroma-data` | 向量数据 | `/chroma/chroma` |

---

## 常见问题

### Q: 部署后无法访问服务？

检查容器状态：
```bash
docker-compose ps
docker-compose logs
```

确保端口未被占用：
```bash
lsof -i :8000  # 检查8000端口
lsof -i :8080  # 检查8080端口
```

### Q: API Key 如何配置？

编辑项目根目录的 `.env` 文件：
```bash
LLM_API_KEY=sk-your-actual-api-key
```

修改后重启服务：
```bash
./deploy.sh restart
```

### Q: 如何查看详细日志？

```bash
# 查看所有服务日志
./deploy.sh logs

# 查看特定服务日志
./deploy.sh logs backend
./deploy.sh logs frontend

# 查看最后100行日志
docker-compose logs --tail=100
```

### Q: 如何更新到最新版本？

```bash
# 拉取最新代码
git pull origin main

# 重新构建并部署
./deploy.sh
```

---

## 路线图

- [x] 项目脚手架搭建
- [x] Agent 框架设计
- [x] LangGraph 编排器
- [x] 记忆系统框架
- [x] 评估框架
- [x] RESTful API
- [x] WebSocket 实时通信
- [x] Docker 化部署
- [ ] LLM 集成实现
- [ ] 知识库向量化
- [ ] 前端界面开发
- [ ] Kubernetes 部署支持
- [ ] CI/CD 流水线

## License

MIT License
