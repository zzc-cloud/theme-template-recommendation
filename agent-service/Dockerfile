# ─────────────────────────────────────────────
# Theme Template Recommendation Agent
# Dockerfile
#
# 特性：
# - NumPy 源码编译，自动适配目标平台
# - amd64：从源码编译，使用通用 x86-64 指令集，兼容旧 CPU
# - arm64：让编译器自动适配
# ─────────────────────────────────────────────

FROM python:3.11-slim

# ── 基础配置 ──
WORKDIR /app

# 安装系统依赖（包含编译 numpy 所需的库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    gfortran \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Python 环境 ──
# 先复制依赖文件，再安装依赖（利用 Docker 缓存）
COPY requirements.txt .

# ── 旧 CPU 兼容处理 ──
# 接收平台参数（buildx 自动注入：amd64 或 arm64）
ARG TARGETARCH

# amd64：从源码编译 NumPy，使用通用 x86-64 指令集（兼容所有 CPU）
# arm64：让 gcc 自动适配
RUN pip uninstall numpy -y \
    && if [ "$TARGETARCH" = "amd64" ]; then \
         echo ">>> 编译 x86-64 通用版 NumPy（兼容旧 CPU）" && \
         CFLAGS="-march=x86-64 -mtune=generic" \
         CXXFLAGS="-march=x86-64 -mtune=generic" \
         pip install --no-cache-dir numpy --no-binary numpy; \
       else \
         echo ">>> 编译 arm64 原生版 NumPy" && \
         pip install --no-cache-dir numpy --no-binary numpy; \
       fi \
    && rm -rf /root/.cache/pip

# 安装其他依赖
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip

# ── 应用代码 ──
COPY src/ ./src/

# ── 脚本工具（向量化等） ──
COPY scripts/ ./scripts/

# ── 环境变量 ──
# 生产环境应通过 docker run -e 或 docker-compose 环境变量覆盖
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# ── 端口 ──
EXPOSE 8000

# ── 健康检查 ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── 启动命令 ──
# 默认启动 uvicorn（生产模式）
# 也可通过 docker run 命令覆盖
CMD ["uvicorn", "agent_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
