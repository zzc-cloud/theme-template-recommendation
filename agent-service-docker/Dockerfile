# ─────────────────────────────────────────────
# Theme Template Recommendation Agent
# Dockerfile
#
# 优化点：
# - apt/pip 换国内阿里云源（完整替换 deb.debian.org + debian-security）
# - arm64 直接用 NumPy wheel，amd64 源码编译
# - NumPy 与 requirements.txt 解耦（NumPy 先编译，缓存最稳定）
# - 安装依赖时排除 numpy 避免重复安装
# ─────────────────────────────────────────────

FROM python:3.11-slim

# ── 基础配置 ──
WORKDIR /app

# ── 平台参数 ──
ARG TARGETARCH

# ── apt 换阿里云源（完整替换 deb.debian.org + debian-security）──
# debian.sources 格式：URIs: http://deb.debian.org/debian
RUN sed -i \
    -e 's|URIs: http://deb.debian.org/debian|URIs: https://mirrors.aliyun.com/debian|g' \
    -e 's|URIs: http://deb.debian.org/debian-security|URIs: https://mirrors.aliyun.com/debian-security|g' \
    /etc/apt/sources.list.d/debian.sources

# ── 安装系统依赖（amd64 额外安装编译工具链）──
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && if [ "$TARGETARCH" = "amd64" ]; then \
        echo ">>> [amd64] 安装编译工具链" && \
        apt-get install -y --no-install-recommends \
            build-essential gfortran libopenblas-dev; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# ── pip 换国内阿里云源 ──
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config set global.trusted-host mirrors.aliyun.com

# ── NumPy 编译/安装（与 requirements.txt 解耦，变动最少，缓存最稳定）──
# amd64：从源码编译，使用通用 x86-64 指令集（兼容旧 CPU）
# arm64：直接用预编译 wheel
RUN if [ "$TARGETARCH" = "amd64" ]; then \
        echo ">>> [amd64] 源码编译 NumPy（兼容旧 CPU）" && \
        CFLAGS="-march=x86-64 -mtune=generic" \
        CXXFLAGS="-march=x86-64 -mtune=generic" \
        pip install --no-cache-dir numpy==1.24.4 --no-binary numpy; \
    else \
        echo ">>> [arm64] 使用预编译 NumPy wheel" && \
        pip install --no-cache-dir numpy==1.24.4; \
    fi \
    && rm -rf /root/.cache/pip

# ── 安装其他依赖（排除 numpy 避免重复安装）──
COPY requirements.txt .
RUN grep -iv "^\s*numpy[>=<!~\\[]" requirements.txt > requirements_no_numpy.txt \
    && pip install --no-cache-dir -r requirements_no_numpy.txt \
    && rm -rf /root/.cache/pip requirements_no_numpy.txt

# ── 应用代码 ──
COPY src/ ./src/

# ── 脚本工具（向量化等）──
COPY scripts/ ./scripts/

# ── 环境变量 ──
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# ── 端口 ──
EXPOSE 8000

# ── 健康检查 ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── 启动命令 ──
CMD ["uvicorn", "agent_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
