"""
FastAPI 应用入口
主题模板推荐 Agent 服务
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.schemas import HealthResponse
from .api.routes import router as api_router, init_semaphore, get_current_concurrency
from .config import MAX_CONCURRENT_REQUESTS
from .graph import graph as agent_graph
from .tools import theme_tools

CLEANUP_INTERVAL_SECONDS = 600  # 每10分钟清理一次过期 thread

# ─────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 生命周期管理
# ─────────────────────────────────────────────

async def _cleanup_loop():
    """后台定时清理过期 thread 的协程"""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            saver = agent_graph.get_checkpointer()
            count = saver.cleanup_expired()
            stats = saver.stats()
            logger.info(
                f"[Cleanup] 本次清理 {count} 个过期thread | "
                f"当前活跃: {stats['active_threads']} | "
                f"总计: {stats['total_threads']}"
            )
        except asyncio.CancelledError:
            logger.info("[Cleanup] 清理任务已停止")
            break
        except Exception as e:
            # 清理任务异常不能影响主服务
            logger.error(f"[Cleanup] 清理任务异常: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("=" * 60)
    logger.info("主题模板推荐 Agent 服务启动中...")
    logger.info("=" * 60)

    # 预热 Agent（图编译）
    try:
        agent_graph.get_agent()
        logger.info("LangGraph Agent 编译完成（TTLMemorySaver 已启用，TTL=1天）")
    except Exception as e:
        logger.warning(f"Agent 预热失败: {e}")

    # 初始化并发信号量
    try:
        init_semaphore()
        logger.info("并发信号量初始化完成")
    except Exception as e:
        logger.warning(f"并发信号量初始化失败: {e}")

    # 预热 Neo4j 连接
    try:
        driver = theme_tools.get_neo4j_driver()
        driver.verify_connectivity()
        logger.info("Neo4j 连接验证成功")
    except Exception as e:
        logger.warning(f"Neo4j 连接验证失败: {e}")

    # 启动 TTL 清理任务
    logger.info("服务启动：启动 TTL 清理任务...")
    cleanup_task = asyncio.create_task(_cleanup_loop())

    logger.info("服务启动完成")
    logger.info("=" * 60)

    yield

    # 关闭时
    logger.info("正在关闭服务...")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        theme_tools.close_neo4j_driver()
        logger.info("Neo4j 连接已关闭")
    except Exception:
        pass

    logger.info("服务已关闭")


# ─────────────────────────────────────────────
# FastAPI 应用
# ─────────────────────────────────────────────
app = FastAPI(
    title="Theme Template Recommendation Agent",
    description="魔数师主题和模板推荐 API 服务，基于 LangChain/LangGraph 实现",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(api_router)


# ─────────────────────────────────────────────
# 路由
# ─────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health():
    """健康检查接口"""
    services = {
        "neo4j": False,
    }

    # 检查 Neo4j
    try:
        driver = theme_tools.get_neo4j_driver()
        driver.verify_connectivity()
        services["neo4j"] = True
    except Exception as e:
        logger.warning(f"Neo4j 健康检查失败: {e}")

    # 并发状态
    concurrency = {
        "current": get_current_concurrency(),
        "max": MAX_CONCURRENT_REQUESTS,
        "available": MAX_CONCURRENT_REQUESTS - get_current_concurrency(),
    }

    all_healthy = all(services.values())

    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        version="1.0.0",
        services=services,
        concurrency=concurrency,
    )


@app.get("/health/memory", tags=["system"])
async def health_memory():
    """内存状态检查接口"""
    try:
        saver = agent_graph.get_checkpointer()
        stats = saver.stats()
        return {
            "status": "ok",
            "ttl_seconds": stats["ttl_seconds"],
            "total_threads": stats["total_threads"],
            "active_threads": stats["active_threads"],
            "expired_threads": stats["expired_threads"],
        }
    except Exception as e:
        logger.error(f"内存状态检查失败: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/", tags=["system"])
async def root():
    """根路径"""
    return {
        "service": "Theme Template Recommendation Agent",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agent_service.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
