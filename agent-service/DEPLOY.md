# 部署指南

本文档详细说明如何将 Theme Template Recommendation Agent Service 部署到生产环境。

---

## 目录

- [部署模式概述](#部署模式概述)
- [方式一：Docker Compose 一键部署](#方式一docker-compose-一键部署)
- [方式二：手动 Docker 部署](#方式二手动-docker-部署)
- [方式三：传统服务器部署](#方式三传统服务器部署)
- [生产环境配置](#生产环境配置)
- [运维指南](#运维指南)
- [常见问题](#常见问题)

---

## 部署模式概述

本服务支持三种部署方式：

| 方式 | 适用场景 | 复杂度 |
|------|---------|--------|
| Docker Compose | 本地开发、快速验证 | 低 |
| 手动 Docker | 生产 Linux 服务器 | 中 |
| 传统部署 | 无 Docker 环境的服务器 | 高 |

### 基础设施依赖

部署前需确认以下依赖已就绪：

| 依赖 | 版本 | 说明 | 必需 |
|------|------|------|------|
| Neo4j | 5.x | 存储魔数师主题/模板/指标本体 | 必需 |
| Chroma | 0.5.x | 存储指标向量（用于语义搜索） | 必需 |
| SiliconFlow API | - | LLM 推理 + 向量嵌入 | 必需 |
| Docker | 24.x+ | 容器化部署 | 方式二、三 |
| Docker Compose | 2.x+ | 编排多容器 | 方式一 |

### 网络架构

```
                    ┌─────────────────────────────────────┐
                    │          负载均衡 / Nginx           │
                    │         (可选，外部访问)             │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │         Agent Service               │
                    │     (FastAPI + LangGraph)           │
                    │         Port: 8000                 │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    ┌─────────▼─────────┐  ┌──────▼───────┐  ┌───────▼────────┐
    │      Neo4j         │  │    Chroma    │  │  SiliconFlow   │
    │   bolt://:7687    │  │  (向量库)    │  │   (LLM API)    │
    └───────────────────┘  └──────────────┘  └────────────────┘
```

---

## 方式一：Docker Compose 一键部署

适合本地开发、快速验证。

### 前置条件

- Docker 24.0+
- Docker Compose 2.20+
- SiliconFlow API Key

### 部署步骤

#### 1. 准备环境变量

```bash
cd agent-service

# 创建 .env 文件
cat > .env << 'EOF'
# Neo4j（默认连接宿主机上的 Neo4j，bolt://host.docker.internal:7687）
# 如需独立容器，请自行取消 docker-compose.yml 中 neo4j 服务的注释
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_secure_password_here

# SiliconFlow API（必需）
SILICONFLOW_API_KEY=sk-your-api-key-here

# LLM 模型（代码默认值：Pro/zai-org/
# 可根据实际需要修改
LLM_MODEL=Pro/zai-org/GLM-5
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=4096

# Chroma 向量库路径（如需挂载本地数据）
CHROMA_PATH=../mcp-server/data/indicators_vector
EOF
```

#### 2. 启动服务

```bash
# 构建并启动所有服务（Agent + Neo4j）
docker-compose up -d --build

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f agent-service
```

#### 3. 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 测试推荐
curl -s -N -X POST http://localhost:8000/api/v1/recommend \
  -H "Content-Type: application/json" \
  -d "{\"thread_id\": \"test-$(date +%s)\", \"question\": \"我想分析南京分行的小微企业贷款风险\"}"
```

#### 4. 停止服务

```bash
docker-compose down      # 停止服务（保留数据卷）
docker-compose down -v    # 停止服务并删除数据卷
```

---

## 方式二：手动 Docker 部署

适合生产 Linux 服务器，使用已有的 Neo4j 服务。

### 前置条件

- Docker 24.0+ 已安装
- Neo4j 服务已就绪（bolt://neo4j-host:7687）
- SiliconFlow API Key
- Chroma 向量库已准备好

### 部署步骤

#### 1. 打包 Docker 镜像

```bash
cd agent-service

# 构建镜像
docker build -t theme-template-agent:latest .
```

#### 2. 准备配置文件

```bash
mkdir -p /opt/agent-service
cd /opt/agent-service

# 创建 .env 配置文件
cat > .env << 'EOF'
# Neo4j（替换为实际的服务器地址）
NEO4J_URI=bolt://your-neo4j-host:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password

# SiliconFlow API（必需）
SILICONFLOW_API_KEY=sk-your-api-key-here

# LLM 模型（代码默认值：Pro/zai-org/
# 可根据实际需要修改
LLM_MODEL=Pro/zai-org/GLM-5
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=4096

# Embedding（可选，使用 SiliconFlow 内置）
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
EMBEDDING_DIM=1024

# Chroma 向量库路径
# 本地开发：代码自动推断为 mcp-server/data/indicators_vector（可省略此行）
# 容器部署：通过 -v 挂载到此路径
CHROMA_PATH=/app/chroma

# Agent 行为参数
MAX_ITERATION_ROUNDS=3
VECTOR_SEARCH_TOP_K=20
EOF
```

#### 3. 准备向量库数据（可选）

如果 Chroma 数据在本地：

```bash
# 复制向量库到服务器
scp -r /path/to/indicators_vector user@server:/opt/agent-service/
```

#### 4. 启动容器

```bash
# 启动服务
docker run -d \
  --name theme-template-agent \
  --restart unless-stopped \
  -p 8000:8000 \
  --env-file /opt/agent-service/.env \
  -v /opt/agent-service/indicators_vector:/app/chroma:ro \
  theme-template-agent:latest

# 查看日志
docker logs -f theme-template-agent

# 查看健康状态
curl http://localhost:8000/health
```

#### 5. 配置 Systemd（可选，推荐用于生产环境）

```bash
# 创建 systemd 服务文件
sudo tee /etc/systemd/system/agent-service.service << 'EOF'
[Unit]
Description=Theme Template Recommendation Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/agent-service
EnvironmentFile=/opt/agent-service/.env
ExecStart=/usr/bin/docker start -a theme-template-agent
ExecStop=/usr/bin/docker stop theme-template-agent
Restart=unless-stopped

[Install]
WantedBy=multi-user.target
EOF

# 启用并启动服务
sudo systemctl daemon-reload
sudo systemctl enable agent-service
sudo systemctl start agent-service

# 检查状态
sudo systemctl status agent-service
```

---

## 方式三：传统服务器部署

适合没有 Docker 环境的服务器。

### 前置条件

- Python 3.11+
- Neo4j 5.x 已运行
- SiliconFlow API Key

### 部署步骤

#### 1. 安装 Python 环境

```bash
# 安装 Python 3.11（Ubuntu/Debian）
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# 创建虚拟环境
python3.11 -m venv /opt/agent-service/venv
source /opt/agent-service/venv/bin/activate

# 升级 pip
pip install --upgrade pip
```

#### 2. 部署代码

```bash
# 创建目录
mkdir -p /opt/agent-service
cd /opt/agent-service

# 复制代码（通过 scp/rsync/git 等方式）
# 示例：git clone
git clone <your-repo> .

# 安装依赖
pip install -e .

# 安装系统依赖（如需要）
# apt install -y libgl1-mesa-glx libglib2.0-0
```

#### 3. 配置环境变量

```bash
# 创建 .env 文件（参考方式二）
cp .env.example .env
vim .env  # 填写实际配置
```

#### 4. 配置 Nginx 反向代理（可选，推荐）

```nginx
# /etc/nginx/sites-available/agent-service
upstream agent_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name your-domain.com;

    # ── 通用代理配置（适用于所有接口） ──
    location / {
        proxy_pass http://agent_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 超时配置
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        proxy_send_timeout 300s;
    }

    # ── SSE 接口（/recommend + /resume） ──
    # ⚠️ 注意：健康检查 /health 不在 /api/v1 前缀下
    location ~ ^/api/v1/(recommend|resume)$ {
        proxy_pass http://agent_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        proxy_send_timeout 300s;

        # SSE 必需：禁用 Nginx 缓冲
        proxy_set_header X-Accel-Buffering no;

        # 标准代理头
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
# 启用站点
sudo ln -s /etc/nginx/sites-available/agent-service /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 5. 使用 Supervisor 管理进程

```bash
# 安装 Supervisor
sudo apt install -y supervisor

# 创建配置
sudo tee /etc/supervisor/conf.d/agent-service.conf << 'EOF'
[program:agent-service]
command=/opt/agent-service/venv/bin/uvicorn agent_service.main:app --host 0.0.0.0 --port 8000 --workers 1
directory=/opt/agent-service
user=root
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
environment=PATH="/opt/agent-service/venv/bin"
stdout_logfile=/var/log/supervisor/agent-service.log
stderr_logfile=/var/log/supervisor/agent-service-error.log
EOF

# 重新加载配置
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start agent-service
```

---

## 生产环境配置

### 必需配置

```bash
# .env 生产配置示例
# ─────────────────────────────────────────────

# Neo4j（生产环境使用高可用地址）
NEO4J_URI=bolt://neo4j-primary:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<生产密码>

# SiliconFlow（必需）
SILICONFLOW_API_KEY=sk-<your-production-key>

# LLM（⚠️ 必填，不可依赖默认值）
LLM_MODEL=Pro/zai-org/GLM-5
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=4096

# 向量库
# 容器部署时：挂载向量库数据到此路径
CHROMA_PATH=/app/chroma

# Agent 参数
MAX_ITERATION_ROUNDS=3
VECTOR_SEARCH_TOP_K=20
```

### 健康检查端点

> **注意**：健康检查端点挂载在根路径 `/health`，**不在** `/api/v1` 前缀下。

```bash
# 正确
curl http://localhost:8000/health

# 错误（该路径不存在）
curl http://localhost:8000/api/v1/health
```

### 安全建议

| 项目 | 建议 |
|------|------|
| Neo4j 密码 | 使用强密码，不要使用默认密码 |
| API Key | 通过环境变量注入，不要写在代码中 |
| 防火墙 | 仅开放必要端口（8000 HTTP / 443 HTTPS） |
| HTTPS | 生产环境必须启用，配置 Nginx SSL 证书 |
| 日志 | 配置日志轮转，避免磁盘占满 |
| 监控 | 接入 Prometheus + Grafana 监控 |

### 状态持久化说明（重要）

> 当前服务使用 `InMemorySaver` 作为 LangGraph Checkpointer，具有以下生产环境限制：

| 限制 | 影响 | 建议 |
|------|------|------|
| 进程内存储，服务重启即失效 | 重启后所有进行中的会话（interrupt 状态）丢失 | 维护窗口前通知用户 |
| 不支持多实例共享 | 多 worker 时同一 thread_id 可能路由到不同实例 | **生产环境必须使用单 worker 或粘性会话** |
| 内存随会话数增长 | 长期运行可能 OOM | 定期重启或限制并发会话数 |

**生产部署要求**：
- **必须**使用单实例部署（`--workers 1`）
- 或配置 Nginx 粘性会话（`ip_hash`）确保同一 session 路由到同一实例
- 如需多实例，需将 `InMemorySaver` 替换为 `AsyncRedisSaver`（需修改代码）

### 性能调优

```bash
# ⚠️ 由于使用 InMemorySaver，必须使用单 worker
# 如需多实例，需先将 Checkpointer 替换为 Redis 实现
uvicorn agent_service.main:app --workers 1 --host 0.0.0.0 --port 8000

# 如果使用 Docker（不支持多 worker）
docker run ... theme-template-agent:latest
```

### 资源规划

| 规模 | CPU | 内存 | 存储 |
|------|-----|------|------|
| 开发/测试 | 1 核 | 1 GB | 5 GB |
| 小规模生产 | 2 核 | 2 GB | 10 GB |
| 中等规模 | 4 核 | 4 GB | 20 GB |

---

## 运维指南

### 常用运维命令

```bash
# ── Docker 部署 ──
docker stop theme-template-agent          # 停止
docker start theme-template-agent         # 启动
docker restart theme-template-agent       # 重启
docker logs -f theme-template-agent       # 查看日志
docker exec -it theme-template-agent sh  # 进入容器

# ── Systemd 部署 ──
sudo systemctl status agent-service
sudo systemctl restart agent-service
sudo journalctl -u agent-service -f     # 查看日志

# ── Supervisor 部署 ──
sudo supervisorctl status agent-service
sudo supervisorctl restart agent-service
sudo tail -f /var/log/supervisor/agent-service.log

# ── 健康检查 ──
curl http://localhost:8000/health
curl -s -N -X POST http://localhost:8000/api/v1/recommend \
  -H "Content-Type: application/json" \
  -d "{\"thread_id\": \"health-check-$(date +%s)\", \"question\": \"健康检查测试\"}"
```

### 日志管理

```bash
# Docker 日志轮转配置
docker run \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  ...

# 服务器日志轮转（传统部署）
sudo tee /etc/logrotate.d/agent-service << 'EOF'
/var/log/agent-service/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
    sharedscripts
    postrotate
        supervisorctl restart agent-service > /dev/null 2>&1 || true
    endscript
}
EOF
```

### 升级流程

```bash
# 1. 备份当前配置
cp /opt/agent-service/.env /opt/agent-service/.env.bak

# 2. 拉取新代码
cd /opt/agent-service
git pull

# 3. 重新构建/安装
# Docker 部署
docker build -t theme-template-agent:latest .
docker stop theme-template-agent
docker rm theme-template-agent
docker run -d ... theme-template-agent:latest

# 或传统部署
source venv/bin/activate
pip install -e . --force-reinstall
sudo systemctl restart agent-service

# 4. 验证
curl http://localhost:8000/health
```

---

## 常见问题

### Q1: 健康检查失败，Neo4j 连接不上

**原因**：Neo4j 地址/密码配置错误

**排查**：
```bash
# 测试 Neo4j 连接
docker exec theme-template-agent \
  python -c "from neo4j import GraphDatabase; \
    d = GraphDatabase.driver('${NEO4J_URI}', auth=('${NEO4J_USER}', '${NEO4J_PASSWORD}')); \
    d.verify_connectivity(); print('OK')"
```

**解决**：检查 `.env` 中的 `NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD` 配置

### Q2: LLM 调用返回 401 Unauthorized

**原因**：SiliconFlow API Key 错误或过期

**排查**：
```bash
curl -H "Authorization: Bearer ${SILICONFLOW_API_KEY}" \
  https://api.siliconflow.cn/v1/models
```

**解决**：更新 `.env` 中的 `SILICONFLOW_API_KEY`

### Q3: 流式接口连接超时

**原因**：推理耗时长，Nginx 超时配置不足

**解决**：调整 Nginx 配置中的 `proxy_read_timeout` 和 `proxy_send_timeout`

### Q4: 请求响应慢

**原因**：LLM 调用次数多，或 Neo4j 查询慢

**优化建议**：
- 减少 `MAX_ITERATION_ROUNDS`（默认 3）
- 减少 `VECTOR_SEARCH_TOP_K`（默认 20）
- 检查 Neo4j 是否有适当索引（为 theme_id、indicator_id 建立索引）
- 优化 Chroma 向量库（确保数据已持久化到磁盘，避免每次重建）
- 如需提升并发，需先将 Checkpointer 替换为 Redis 实现后再考虑多实例部署

### Q5: Docker 容器启动失败

**排查**：
```bash
# 查看完整日志
docker logs --tail 100 theme-template-agent

# 检查端口占用
netstat -tlnp | grep 8000

# 检查磁盘空间
df -h
```

### Q6: /resume 返回"找不到会话"

**原因**：服务在 /recommend 和 /resume 之间重启，InMemorySaver 数据丢失

**排查**：
```bash
# 检查服务是否在两次请求之间重启过
docker logs --since 10m theme-template-agent | grep "服务启动"
```

**解决**：引导用户重新提交问题（使用新的 thread_id 调用 /recommend）

### Q7: 多实例部署时 /resume 随机失败

**原因**：InMemorySaver 不支持多实例共享，/resume 请求路由到了不同实例

**解决**：
1. 改为单实例部署（`--workers 1`）
2. 或配置 Nginx ip_hash 粘性会话
3. 或将 Checkpointer 替换为 Redis 实现（需修改代码）

---

## 联系方式

如有问题，请联系开发团队或提交 Issue。
