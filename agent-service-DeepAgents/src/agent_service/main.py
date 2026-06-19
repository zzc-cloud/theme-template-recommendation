"""FastAPI 应用入口。

本模块只负责服务生命周期和 HTTP 应用装配：初始化并发控制、预热
DeepAgents agent、注册 API 路由，并在关闭时释放工具层持有的外部连接。
推荐业务流程不在这里编排，而是由 DeepAgents 加载的 Skill 和工具完成。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import get_current_concurrency, init_semaphore, router as api_router
from .api.schemas import HealthResponse
from .config import MAX_CONCURRENT_REQUESTS
from .deepagents.agent_factory import get_agent
from .tools import theme_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理服务级资源。

    semaphore 必须在请求进入前初始化；agent 预热用于提前暴露模型、Skill
    或工具注册问题；Neo4j driver 是工具层单例，随应用退出统一关闭。
    """
    init_semaphore()
    try:
        get_agent()
        logger.info("DeepAgents Agent 初始化完成")
    except Exception as exc:
        logger.warning("DeepAgents Agent 初始化失败: %s", exc)
    yield
    theme_tools.close_neo4j_driver()


app = FastAPI(
    title="Theme Template Recommendation Agent - DeepAgents",
    description="魔数师主题和模板推荐 API 服务，基于 DeepAgents 实现",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    """返回服务依赖和并发状态。

    健康检查只验证 API 运行所需的两类关键依赖：DeepAgents agent 能否创建，
    以及 Neo4j 是否可连接；Chroma/Embedding 等数据链路由脚本级健康检查覆盖。
    """
    services = {"neo4j": False, "deepagents": False}
    try:
        get_agent()
        services["deepagents"] = True
    except Exception as exc:
        logger.warning("DeepAgents 健康检查失败: %s", exc)
    try:
        driver = theme_tools.get_neo4j_driver()
        driver.verify_connectivity()
        services["neo4j"] = True
    except Exception as exc:
        logger.warning("Neo4j 健康检查失败: %s", exc)

    current = get_current_concurrency()
    return HealthResponse(
        status="healthy" if all(services.values()) else "degraded",
        version="1.0.0",
        services=services,
        concurrency={
            "current": current,
            "max": MAX_CONCURRENT_REQUESTS,
            "available": MAX_CONCURRENT_REQUESTS - current,
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("agent_service.main:app", host="0.0.0.0", port=8000, reload=True)
