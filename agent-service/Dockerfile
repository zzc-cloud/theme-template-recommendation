# ─────────────────────────────────────────────
# Theme Template Recommendation Agent
# Dockerfile
# ─────────────────────────────────────────────

FROM python:3.11-slim

# ── 基础配置 ──
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── Python 环境 ──
# 先复制依赖文件，再安装依赖（利用 Docker 缓存）
COPY src/agent_service/pyproject.toml .
COPY src/agent_service/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# ── 应用代码 ──
COPY src/ ./src/

# ── 环境变量 ──
# 生产环境应通过 docker run -e 或 docker-compose 环境变量覆盖
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# ── 端口 ──
EXPOSE 8000

# ── 健康检查 ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── 启动命令 ──
# 默认启动 uvicorn（生产模式）
# 也可通过 docker run 命令覆盖
CMD ["uvicorn", "agent_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
