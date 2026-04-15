
from pathlib import Path
from dotenv import load_dotenv
import os

if os.getenv("AGENT_ENV_FILE"):
    load_dotenv(os.getenv("AGENT_ENV_FILE"))
else:
    _search_path = Path(__file__).parent
    _found = False
    for _ in range(5):
        _env_at_path = _search_path / ".env"
        if _env_at_path.exists():
            load_dotenv(_env_at_path)
            _found = True
            break
        _search_path = _search_path.parent
    if not _found:
        load_dotenv()


NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "yyzzc87275478")


SILICONFLOW_EMBEDDING_API_KEY: str = os.getenv("SILICONFLOW_EMBEDDING_API_KEY", "sk-qrumrpocxqhdbxywqpiibvsvgohruwvoktcywkjmuvoejtch")

SILICONFLOW_LLM_API_KEY: str = os.getenv("SILICONFLOW_LLM_API_KEY", "sk-qrumrpocxqhdbxywqpiibvsvgohruwvoktcywkjmuvoejtch")

SILICONFLOW_BASE_URL: str = os.getenv(
    "SILICONFLOW_BASE_URL",
    "https://api.siliconflow.cn/v1",
)
SILICONFLOW_EMBEDDING_URL: str = os.getenv(
    "SILICONFLOW_EMBEDDING_URL",
    "https://api.siliconflow.cn/v1/embeddings",
)

EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL",
    "Qwen/Qwen3-Embedding-8B",
)
EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "4096"))

LLM_MODEL: str = os.getenv(
    "LLM_MODEL",
    "Pro/zai-org/GLM-4.7",
)
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))


_CHROMA_DEFAULT = str(Path(__file__).resolve().parent.parent.parent.parent / "mcp-server" / "data" / "indicators_vector")
CHROMA_PATH: str = os.getenv("CHROMA_PATH", _CHROMA_DEFAULT)
COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "indicators")


MAX_ITERATION_ROUNDS: int = int(os.getenv("MAX_ITERATION_ROUNDS", "5"))

CONVERGENCE_SIMILARITY_THRESHOLD: float = float(
    os.getenv("CONVERGENCE_SIMILARITY_THRESHOLD", "0.80")
)

LOW_CONFIDENCE_THRESHOLD: float = CONVERGENCE_SIMILARITY_THRESHOLD

DEFAULT_TOP_K_THEMES: int = int(os.getenv("DEFAULT_TOP_K_THEMES", "3"))
DEFAULT_TOP_K_TEMPLATES: int = int(os.getenv("DEFAULT_TOP_K_TEMPLATES", "5"))

THEME_WEIGHTED_FREQUENCY_THRESHOLD: float = float(os.getenv("THEME_WEIGHTED_FREQUENCY_THRESHOLD", "0.6"))

TEMPLATE_COVERAGE_THRESHOLD: float = float(os.getenv("TEMPLATE_COVERAGE_THRESHOLD", "0.2"))

VECTOR_SEARCH_TOP_K: int = int(os.getenv("VECTOR_SEARCH_TOP_K", "50"))

JACCARD_SIMILARITY_THRESHOLD: float = float(os.getenv("JACCARD_SIMILARITY_THRESHOLD", "0.5"))


MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "10"))
CONCURRENT_TIMEOUT_SECONDS: float = float(os.getenv("CONCURRENT_TIMEOUT_SECONDS", "5.0"))


LLM_CALL_TIMEOUT_SECONDS: float = float(os.getenv("LLM_CALL_TIMEOUT_SECONDS", "60.0"))

LLM_BATCH_TIMEOUT_SECONDS: int = int(os.getenv("LLM_BATCH_TIMEOUT_SECONDS", "310"))

LLM_MAX_RETRIES_RATE_LIMIT:   int = int(os.getenv("LLM_MAX_RETRIES_RATE_LIMIT",   "3"))
LLM_MAX_RETRIES_TIMEOUT:      int = int(os.getenv("LLM_MAX_RETRIES_TIMEOUT",      "2"))
LLM_MAX_RETRIES_SERVER_ERROR: int = int(os.getenv("LLM_MAX_RETRIES_SERVER_ERROR", "2"))
LLM_MAX_RETRIES_SCHEMA_ERROR: int = int(os.getenv("LLM_MAX_RETRIES_SCHEMA_ERROR", "1"))
LLM_MAX_RETRIES_AUTH_ERROR:   int = int(os.getenv("LLM_MAX_RETRIES_AUTH_ERROR",   "0"))
LLM_MAX_RETRIES_UNKNOWN:      int = int(os.getenv("LLM_MAX_RETRIES_UNKNOWN",      "1"))

LLM_BASE_DELAY_RATE_LIMIT:   float = float(os.getenv("LLM_BASE_DELAY_RATE_LIMIT",   "5.0"))
LLM_BASE_DELAY_TIMEOUT:      float = float(os.getenv("LLM_BASE_DELAY_TIMEOUT",      "2.0"))
LLM_BASE_DELAY_SERVER_ERROR: float = float(os.getenv("LLM_BASE_DELAY_SERVER_ERROR", "1.0"))
LLM_BASE_DELAY_SCHEMA_ERROR: float = float(os.getenv("LLM_BASE_DELAY_SCHEMA_ERROR", "0.5"))
LLM_BASE_DELAY_AUTH_ERROR:   float = float(os.getenv("LLM_BASE_DELAY_AUTH_ERROR",  "0.0"))
LLM_BASE_DELAY_UNKNOWN:      float = float(os.getenv("LLM_BASE_DELAY_UNKNOWN",     "1.0"))

LLM_MAX_DELAY_RATE_LIMIT:   float = float(os.getenv("LLM_MAX_DELAY_RATE_LIMIT",   "60.0"))
LLM_MAX_DELAY_TIMEOUT:      float = float(os.getenv("LLM_MAX_DELAY_TIMEOUT",      "10.0"))
LLM_MAX_DELAY_SERVER_ERROR: float = float(os.getenv("LLM_MAX_DELAY_SERVER_ERROR", "8.0"))
LLM_MAX_DELAY_SCHEMA_ERROR: float = float(os.getenv("LLM_MAX_DELAY_SCHEMA_ERROR", "2.0"))
LLM_MAX_DELAY_AUTH_ERROR:   float = float(os.getenv("LLM_MAX_DELAY_AUTH_ERROR",   "0.0"))
LLM_MAX_DELAY_UNKNOWN:      float = float(os.getenv("LLM_MAX_DELAY_UNKNOWN",      "5.0"))


JUDGE_THEMES_BATCH_SIZE: int = int(os.getenv("JUDGE_THEMES_BATCH_SIZE", "5"))
JUDGE_THEMES_BATCH_TIMEOUT_SECONDS: float = float(os.getenv("JUDGE_THEMES_BATCH_TIMEOUT_SECONDS", "120.0"))
ANALYZE_TEMPLATES_BATCH_SIZE: int = int(os.getenv("ANALYZE_TEMPLATES_BATCH_SIZE", "5"))
ANALYZE_TEMPLATES_BATCH_TIMEOUT_SECONDS: float = float(os.getenv("ANALYZE_TEMPLATES_BATCH_TIMEOUT_SECONDS", "120.0"))
