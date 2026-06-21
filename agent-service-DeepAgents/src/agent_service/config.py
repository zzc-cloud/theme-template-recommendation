"""服务运行配置。

本模块是 API 服务、DeepAgents runtime 和工具层共享的配置入口。加载顺序为：
1. 如果设置 `AGENT_ENV_FILE`，优先加载该文件。
2. 否则从当前文件所在目录向上搜索第一个 `.env`。
3. 如果没有找到项目 `.env`，回退到进程环境变量。

这里仅集中读取配置，不做连接初始化；Neo4j、Chroma 和模型客户端分别在使用方
延迟创建。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

if os.getenv("AGENT_ENV_FILE"):
    load_dotenv(os.getenv("AGENT_ENV_FILE"))
else:
    search_path = Path(__file__).resolve().parent
    for _ in range(5):
        env_path = search_path / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            break
        search_path = search_path.parent
    else:
        load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT_DIR / "skills"

# Mysql
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "yyzzc87275478!"),
    "database": os.getenv("MYSQL_DATABASE", "chatbi_metadata"),
    "charset": "utf8mb4",
}

# 图谱连接配置：主题、指标、模板工具都会通过该连接访问 Neo4j。
NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "yyzzc87275478")

# SiliconFlow 同时承担 LLM 推理和 embedding 生成；两个 key 分开配置，便于部署隔离。
SILICONFLOW_EMBEDDING_API_KEY: str = os.getenv("SILICONFLOW_EMBEDDING_API_KEY", "sk-kofniyrmrzpvvwmnmdoiuncnvwbfqkcorikbufmirkotdovx")
SILICONFLOW_EMBEDDING_URL: str = os.getenv("SILICONFLOW_EMBEDDING_URL", "https://api.siliconflow.cn/v1/embeddings")
# SILICONFLOW_LLM_API_KEY: str = os.getenv("SILICONFLOW_LLM_API_KEY", "sk-kofniyrmrzpvvwmnmdoiuncnvwbfqkcorikbufmirkotdovx")
# SILICONFLOW_BASE_URL: str = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_LLM_API_KEY: str = os.getenv("SILICONFLOW_LLM_API_KEY", "sk-N0o2SRWj6eza344Ok7g2H0YkZE30U4PIV1aFP8dXnsScJgGJ")
SILICONFLOW_BASE_URL: str = os.getenv("SILICONFLOW_BASE_URL", "https://vip.aipro.love/v1")

# 模型与向量库配置。
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B")
EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "4096"))
# LLM_MODEL: str = os.getenv("LLM_MODEL", "Pro/deepseek-ai/DeepSeek-R1")
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-opus-4-8")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "16096"))

_CHROMA_DEFAULT = str(ROOT_DIR.parent / "indicators_vector")
CHROMA_PATH: str = os.getenv("CHROMA_PATH", _CHROMA_DEFAULT)
COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "indicators")

# 推荐结果默认规模与模板达标阈值，供 API 默认值和工具层共同使用。
DEFAULT_TOP_K_THEMES: int = int(os.getenv("DEFAULT_TOP_K_THEMES", "3"))
DEFAULT_TOP_K_TEMPLATES: int = int(os.getenv("DEFAULT_TOP_K_TEMPLATES", "5"))
TEMPLATE_COVERAGE_THRESHOLD: float = float(os.getenv("TEMPLATE_COVERAGE_THRESHOLD", "0.2"))

# API 层并发保护参数；实际 semaphore 在 main.py lifespan 中初始化。
MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))
CONCURRENT_TIMEOUT_SECONDS: float = float(os.getenv("CONCURRENT_TIMEOUT_SECONDS", "5.0"))
