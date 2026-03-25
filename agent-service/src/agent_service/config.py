"""
配置管理模块
从 .env 文件或环境变量加载所有配置
"""

from pathlib import Path
from dotenv import load_dotenv
import os

# ─────────────────────────────────────────────
# 加载 .env 文件
# 搜索顺序：环境变量指定路径 > agent_service/.env > 项目根目录/.env
# ─────────────────────────────────────────────
# 1. 环境变量指定路径（Docker 部署时使用）
if os.getenv("AGENT_ENV_FILE"):
    load_dotenv(os.getenv("AGENT_ENV_FILE"))
else:
    # 2. 从当前文件向上搜索 .env
    _search_path = Path(__file__).parent
    _found = False
    for _ in range(5):  # 最多向上搜索 5 层
        _env_at_path = _search_path / ".env"
        if _env_at_path.exists():
            load_dotenv(_env_at_path)
            _found = True
            break
        _search_path = _search_path.parent
    if not _found:
        # 3. 最后尝试当前工作目录
        load_dotenv()


# ─────────────────────────────────────────────
# Neo4j 配置
# ─────────────────────────────────────────────
NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "password")


# ─────────────────────────────────────────────
# SiliconFlow API 配置（用于向量嵌入 + LLM 推理）
# ─────────────────────────────────────────────
SILICONFLOW_API_KEY: str = os.getenv(
    "SILICONFLOW_API_KEY",
    os.getenv("OPENAI_API_KEY", ""),
)
SILICONFLOW_BASE_URL: str = os.getenv(
    "SILICONFLOW_BASE_URL",
    "https://api.siliconflow.cn/v1",
)
SILICONFLOW_EMBEDDING_URL: str = os.getenv(
    "SILICONFLOW_EMBEDDING_URL",
    "https://api.siliconflow.cn/v1/embeddings",
)

# Embedding 模型（SiliconFlow）
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL",
    "Qwen/Qwen3-Embedding-8B",
)
EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1024"))

# LLM 模型（用于推理，SiliconFlow）
LLM_MODEL: str = os.getenv(
    "LLM_MODEL",
    "Pro/zai-org/GLM-5",
)
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))


# ─────────────────────────────────────────────
# Chroma 向量库配置
# ─────────────────────────────────────────────
# 默认使用 mcp-server 中的向量库
# 项目结构：theme-template-recommendation/
#   ├── agent-service/src/agent_service/  ← config.py 在这里
#   │   └── config.py
#   └── mcp-server/data/indicators_vector/
# 因此需要向上 4 层到达项目根
_CHROMA_DEFAULT = str(Path(__file__).resolve().parent.parent.parent.parent / "mcp-server" / "data" / "indicators_vector")
CHROMA_PATH: str = os.getenv("CHROMA_PATH", _CHROMA_DEFAULT)
COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "indicators")


# ─────────────────────────────────────────────
# Agent 配置
# ─────────────────────────────────────────────
# 最大迭代精炼轮次
MAX_ITERATION_ROUNDS: int = int(os.getenv("MAX_ITERATION_ROUNDS", "3"))

# 收敛阈值：Top-1 相似度 > 此值则认为命中
CONVERGENCE_SIMILARITY_THRESHOLD: float = float(
    os.getenv("CONVERGENCE_SIMILARITY_THRESHOLD", "0.80")
)

# 低置信度阈值：超过此轮次仍未达标，进入低置信度流程
LOW_CONFIDENCE_THRESHOLD: float = float(
    os.getenv("LOW_CONFIDENCE_THRESHOLD", "0.60")
)

# 默认 top_k 参数
DEFAULT_TOP_K_THEMES: int = int(os.getenv("DEFAULT_TOP_K_THEMES", "3"))
DEFAULT_TOP_K_TEMPLATES: int = int(os.getenv("DEFAULT_TOP_K_TEMPLATES", "5"))

# 向量搜索 top_k
VECTOR_SEARCH_TOP_K: int = int(os.getenv("VECTOR_SEARCH_TOP_K", "20"))


# ─────────────────────────────────────────────
# 并发控制配置
# ─────────────────────────────────────────────
MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "10"))
CONCURRENT_TIMEOUT_SECONDS: float = float(os.getenv("CONCURRENT_TIMEOUT_SECONDS", "5.0"))


# ─────────────────────────────────────────────
# LLM 重试配置（集中化管理）
# ─────────────────────────────────────────────
LLM_CALL_TIMEOUT_SECONDS: float = float(os.getenv("LLM_CALL_TIMEOUT_SECONDS", "60.0"))

# 批量 LLM 任务超时（秒）
# 推算：单次60s × 最多4次调用 + RATE_LIMIT最大退避35s + 30s余量 ≈ 310s
LLM_BATCH_TIMEOUT_SECONDS: int = int(os.getenv("LLM_BATCH_TIMEOUT_SECONDS", "310"))

# 按错误类型的最大重试次数（0 = 不重试）
LLM_MAX_RETRIES_RATE_LIMIT:   int = int(os.getenv("LLM_MAX_RETRIES_RATE_LIMIT",   "3"))
LLM_MAX_RETRIES_TIMEOUT:      int = int(os.getenv("LLM_MAX_RETRIES_TIMEOUT",      "2"))
LLM_MAX_RETRIES_SERVER_ERROR: int = int(os.getenv("LLM_MAX_RETRIES_SERVER_ERROR", "2"))
LLM_MAX_RETRIES_SCHEMA_ERROR: int = int(os.getenv("LLM_MAX_RETRIES_SCHEMA_ERROR", "1"))
LLM_MAX_RETRIES_AUTH_ERROR:   int = int(os.getenv("LLM_MAX_RETRIES_AUTH_ERROR",   "0"))
LLM_MAX_RETRIES_UNKNOWN:      int = int(os.getenv("LLM_MAX_RETRIES_UNKNOWN",      "1"))

# 按错误类型的初始退避延迟（秒）
LLM_BASE_DELAY_RATE_LIMIT:   float = float(os.getenv("LLM_BASE_DELAY_RATE_LIMIT",   "5.0"))
LLM_BASE_DELAY_TIMEOUT:      float = float(os.getenv("LLM_BASE_DELAY_TIMEOUT",      "2.0"))
LLM_BASE_DELAY_SERVER_ERROR: float = float(os.getenv("LLM_BASE_DELAY_SERVER_ERROR", "1.0"))
LLM_BASE_DELAY_SCHEMA_ERROR: float = float(os.getenv("LLM_BASE_DELAY_SCHEMA_ERROR", "0.5"))
LLM_BASE_DELAY_AUTH_ERROR:   float = float(os.getenv("LLM_BASE_DELAY_AUTH_ERROR",  "0.0"))
LLM_BASE_DELAY_UNKNOWN:      float = float(os.getenv("LLM_BASE_DELAY_UNKNOWN",     "1.0"))

# 按错误类型的最大退避延迟（秒）
LLM_MAX_DELAY_RATE_LIMIT:   float = float(os.getenv("LLM_MAX_DELAY_RATE_LIMIT",   "60.0"))
LLM_MAX_DELAY_TIMEOUT:      float = float(os.getenv("LLM_MAX_DELAY_TIMEOUT",      "10.0"))
LLM_MAX_DELAY_SERVER_ERROR: float = float(os.getenv("LLM_MAX_DELAY_SERVER_ERROR", "8.0"))
LLM_MAX_DELAY_SCHEMA_ERROR: float = float(os.getenv("LLM_MAX_DELAY_SCHEMA_ERROR", "2.0"))
LLM_MAX_DELAY_AUTH_ERROR:   float = float(os.getenv("LLM_MAX_DELAY_AUTH_ERROR",   "0.0"))
LLM_MAX_DELAY_UNKNOWN:      float = float(os.getenv("LLM_MAX_DELAY_UNKNOWN",      "5.0"))
