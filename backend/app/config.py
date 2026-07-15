"""
AIOps Agent Platform - Configuration Management

使用 pydantic-settings 管理所有配置，支持从环境变量和 .env 文件加载。
所有配置项均以 AIOPS_ 为前缀的环境变量可覆盖。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """LLM 模型配置"""

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: str = Field(default="openai", description="LLM 提供商")
    api_key: str = Field(default="", description="API 密钥")
    model: str = Field(default="gpt-4o-mini", description="模型名称")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0, description="采样温度")
    max_tokens: int = Field(default=4096, gt=0, description="最大生成 token 数")
    timeout_seconds: int = Field(default=60, gt=0, description="请求超时(秒)")
    max_retries: int = Field(default=3, ge=0, description="最大重试次数")

    base_url: str = Field(default="", description="自定义 API base_url,优先于 provider 预设")

    # 分模块模型选择:NLU/Judge 可用更便宜的模型,空 = 用主模型
    nlu_model: str = Field(default="", description="NLU 慢路径模型")
    judge_model: str = Field(default="", description="LLM-as-judge 模型")

    # 功能开关:默认全关,合入零风险,逐个打开验收
    enable_rca: bool = Field(default=False, description="RCA LLM 融合推理")
    enable_nlu: bool = Field(default=False, description="NLU LLM 慢路径")
    enable_judge: bool = Field(default=False, description="评测 LLM-as-judge")

    # NLU 快慢路径切换阈值
    nlu_fast_path_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class LangfuseConfig(BaseSettings):
    """Langfuse 可观测性配置"""

    model_config = SettingsConfigDict(
        env_prefix="LANGFUSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    public_key: str = Field(default="", description="Langfuse Public Key")
    secret_key: str = Field(default="", description="Langfuse Secret Key")
    host: str = Field(default="https://cloud.langfuse.com", description="Langfuse 服务地址")
    enabled: bool = Field(default=False, description="是否启用 Langfuse")
    release: str = Field(default="1.0.0", description="应用版本号")
    environment: str = Field(default="development", description="运行环境")


class DatabaseConfig(BaseSettings):
    """数据库配置"""

    model_config = SettingsConfigDict(
        env_prefix="DATABASE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: str = Field(
        default="sqlite:///./data/aiops.db",
        description="数据库连接 URL",
    )
    pool_size: int = Field(default=10, gt=0, description="连接池大小")
    max_overflow: int = Field(default=20, ge=0, description="最大溢出连接数")
    echo: bool = Field(default=False, description="是否打印 SQL 语句")


class AgentConfig(BaseSettings):
    """Agent 行为配置"""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    execution_timeout_seconds: int = Field(
        default=120, gt=0, description="Agent 执行超时(秒)"
    )
    max_iterations: int = Field(
        default=10, gt=0, description="最大迭代次数"
    )
    parallel_workers: int = Field(
        default=3, gt=0, description="并行工作线程数"
    )
    heal_dry_run: bool = Field(
        default=True, description="自愈操作是否仅模拟"
    )
    rca_min_confidence: float = Field(
        default=0.7, ge=0.0, le=1.0, description="根因分析最低置信度"
    )


class MemoryConfig(BaseSettings):
    """记忆系统配置"""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    short_term_max_items: int = Field(
        default=100, gt=0, description="短期记忆最大条目数"
    )
    long_term_max_items: int = Field(
        default=10000, gt=0, description="长期记忆最大条目数"
    )
    similarity_threshold: float = Field(
        default=0.85, ge=0.0, le=1.0, description="记忆相似度阈值"
    )
    retention_days: int = Field(
        default=90, gt=0, description="记忆保留天数"
    )


class ChromaConfig(BaseSettings):
    """ChromaDB 配置

    host 为空(默认)时使用嵌入式 PersistentClient 持久化到 db_path;
    设置 CHROMA_HOST(如 docker-compose 里的 chroma 容器)时切换为
    HttpClient 服务端模式。
    """

    model_config = SettingsConfigDict(
        env_prefix="CHROMA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str | None = Field(
        default=None, description="ChromaDB 服务地址;空 = 嵌入式模式"
    )
    port: int = Field(default=8000, gt=0, description="ChromaDB 服务端口")
    db_path: str = Field(
        default="./data/chromadb", description="嵌入式模式持久化目录"
    )
    collection_name: str = Field(
        default="aiops_memory", description="集合名称"
    )


class WebSocketConfig(BaseSettings):
    """WebSocket 配置"""

    model_config = SettingsConfigDict(
        env_prefix="WS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    heartbeat_interval_seconds: int = Field(
        default=30, gt=0, description="心跳间隔(秒)"
    )
    max_connections: int = Field(
        default=100, gt=0, description="最大连接数"
    )


class AppConfig(BaseSettings):
    """应用主配置 - 聚合所有子配置"""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "testing", "staging", "production"] = Field(
        default="development", description="运行环境"
    )
    host: str = Field(default="0.0.0.0", description="监听地址")
    port: int = Field(default=8000, gt=0, description="监听端口")
    log_level: str = Field(default="INFO", description="日志级别")
    debug: bool = Field(default=False, description="调试模式")

    # 故障处理管道引擎(APP_PIPELINE_ENGINE):
    # legacy = routes.py 顺序流水线(默认);langgraph = LangGraph 状态机编排器,
    # 依赖缺失时运行期自动回落 legacy。
    pipeline_engine: Literal["legacy", "langgraph"] = Field(
        default="legacy", description="故障处理管道引擎"
    )

    # 演示故障种子开关(APP_ENABLE_DEMO_SEED):
    # 默认关闭 — 新克隆/生产启动无任何预置数据;打开后启动时自动注入
    # 7 条演示事故,seed-demo 端点也随之可用(仅演示场景使用)。
    enable_demo_seed: bool = Field(
        default=False, description="演示故障种子(默认关)"
    )

    # CORS 配置
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="CORS 允许的源",
    )
    api_key_header: str = Field(
        default="X-API-Key", description="API Key 请求头名称"
    )

    # 子配置实例
    llm: LLMConfig = Field(default_factory=LLMConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    chroma: ChromaConfig = Field(default_factory=ChromaConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: str | list) -> list[str]:
        """从逗号分隔字符串解析 CORS 源列表"""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def is_development(self) -> bool:
        """是否为开发环境"""
        return self.env == "development"

    @property
    def is_production(self) -> bool:
        """是否为生产环境"""
        return self.env == "production"


# 全局配置单例
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """
    获取全局配置实例（懒加载单例模式）

    Returns:
        AppConfig: 应用配置对象
    """
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def reload_config() -> AppConfig:
    """
    强制重新加载配置（用于配置热更新）

    Returns:
        AppConfig: 重新加载的应用配置对象
    """
    global _config
    _config = AppConfig()
    return _config
