# Docker 镜像打包 Skill

## 触发条件

当用户说"打包 Docker 镜像"、"构建镜像"、"导出镜像"、"docker 打包"等关键词时激活本 Skill。

## 概述

本 Skill 将 agent-service 源码同步到一个独立的 docker 打包模块（agent-service-docker），清除所有 Python 注释后构建 Docker 镜像并导出为压缩包。整个过程是幂等的——每次执行都会清空旧文件重新同步。

## 执行前确认

**强制步骤**：在开始任何操作前，必须向用户确认目标架构。

使用 AskUserQuestion 工具询问：

- 问题："请选择目标架构"
- 选项：
  - AMD64 (linux/amd64) — 兼容旧 CPU，导出文件名 agent-service.tar.gz
  - ARM64 (linux/arm64) — ARM 原生，导出文件名 agent-service-arm64.tar.gz

在用户选择之前，不得执行任何后续步骤。

## 执行流程

### 阶段 1：创建/清空打包目录

1. 在项目根目录（`theme-template-recommendation/`）下确认 `agent-service-docker/` 目录存在，不存在则创建
2. 如果目录已存在且非空，用 `find agent-service-docker -mindepth 1 -delete` 清空目录（确保隐藏文件也被清除）
3. 报告目录状态

### 阶段 2：最小文件集同步

从 `agent-service/` 同步以下白名单文件到 `agent-service-docker/`：

| 类别 | 源路径 | 目标路径 | 说明 |
|------|--------|----------|------|
| 核心源码 | `agent-service/src/` | `agent-service-docker/src/` | 递归复制整个 src 目录 |
| 脚本工具 | `agent-service/scripts/` | `agent-service-docker/scripts/` | 向量化等脚本（Dockerfile 中 COPY 引用） |
| 依赖声明 | `agent-service/requirements.txt` | `agent-service-docker/requirements.txt` | pip 依赖 |
| 项目配置 | `agent-service/pyproject.toml` | `agent-service-docker/pyproject.toml` | 包配置 |
| 容器配置 | `agent-service/Dockerfile` | `agent-service-docker/Dockerfile` | 镜像构建文件 |
| 构建忽略 | `agent-service/.dockerignore` | `agent-service-docker/.dockerignore` | Docker 上下文控制 |

**特殊处理**：检查 `agent-service/Dockerfile` 中是否包含 `COPY` 指令引用了 `docker/` 目录下的文件（如 supervisord.conf）。如果 Dockerfile 引用了该文件，则同步 `agent-service/docker/` 目录；否则不同步。

**明确排除**：tests/、.claude/、*.md、deploy/、.env*、.git/、venv/、__pycache__/、IDE 配置文件。

同步完成后，报告已同步的文件列表和总大小。

### 阶段 3：注释清除

使用 Python 标准库 `tokenize` 模块对 `agent-service-docker/src/` 和 `agent-service-docker/scripts/` 下所有 `.py` 文件执行注释清除。

#### 清除规则

**必须清除**：
- `#` 开头的单行注释（包括 `# ────` 分隔线注释）
- 模块级文档字符串（文件顶部的 `"""..."""`）
- 函数/类的文档字符串（`"""..."""` 形式的 docstring）
- `__init__.py` 中的模块 docstring

**必须保留**：
- `# type: ignore` — 类型检查器指令
- `# noqa` — Linter 指令
- `# pragma: no cover` — 覆盖率指令
- 字符串字面量中的 `#`（如 Prompt 模板中的 `# 标题`）
- 空行（超过 2 个连续空行时压缩为最多 2 个，符合 PEP 8 规范）

#### 清除脚本

脚本已固定存放在 `.claude/skills/docker-package/_strip_comments.py`，直接执行即可：

```bash
python3 .claude/skills/docker-package/_strip_comments.py agent-service-docker
```

该脚本接受一个参数（目标目录），自动扫描其下的 `src/` 和 `scripts/` 目录中所有 `.py` 文件，执行注释和 docstring 清除。

**语法检查已内置**：脚本在清除完成后自动验证所有 .py 文件语法，若有失败则汇总报告并以 exit code 1 退出。整个流程在该阶段失败时立即停止，不进入阶段 4。

### 阶段 4：镜像构建与导出

#### 前置检查

确认 `docker buildx` 可用：

```bash
docker buildx version
```

如果不可用，停止并提示用户安装 Docker Buildx。

#### 构建命令

根据用户在执行前确认的架构，使用对应的命令：

**AMD64**：
```bash
cd agent-service-docker && docker buildx build --platform linux/amd64 -t theme-template-agent:latest --load .
```

**ARM64**：
```bash
cd agent-service-docker && docker buildx build --platform linux/arm64 -t theme-template-agent:arm64 --load .
```

#### 导出命令

**AMD64**：
```bash
docker save theme-template-agent:latest | gzip > ~/Desktop/agent-service.tar.gz
```

**ARM64**：
```bash
docker save theme-template-agent:arm64 | gzip > ~/Desktop/agent-service-arm64.tar.gz
```

#### 最终报告

导出完成后，汇总以下信息：

```
Docker 打包完成
─────────────────────────────
架构：{AMD64 | ARM64}
处理 .py 文件数：N 个
移除注释/docstring 行数：N 行
语法验证：全部通过
镜像 Tag：{tag}
导出路径：{~/Desktop/agent-service*.tar.gz}
导出大小：{ls -lh 输出的实际大小}
```

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| 用户未选择架构 | 停止，等待用户选择 |
| `docker buildx` 不可用 | 停止，提示安装 |
| 注释清除后语法检查失败 | 停止，汇总报告所有失败文件和错误 |
| Docker 构建失败 | 报告 Docker 错误日志 |
| 导出失败 | 报告磁盘空间等信息 |

## 清理

`agent-service-docker/` 目录保留不删除，方便用户排查问题或重新构建。
