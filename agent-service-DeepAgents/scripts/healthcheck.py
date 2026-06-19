#!/usr/bin/env python3
"""
健康检查脚本 - 部署验证工具
用法：
  docker exec theme-template-agent python scripts/healthcheck.py
  docker exec theme-template-agent python scripts/healthcheck.py --only neo4j
  docker exec theme-template-agent python scripts/healthcheck.py --verbose
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
# 路径修正：确保能 import agent_service
# ─────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent          # agent-service/scripts/
_SRC_DIR    = _SCRIPT_DIR.parent / "src"               # agent-service/src/
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ─────────────────────────────────────────────
# 屏蔽 agent_service 内部日志，保持输出干净
# ─────────────────────────────────────────────
import logging
logging.disable(logging.CRITICAL)


# ══════════════════════════════════════════════
# 输出样式
# ══════════════════════════════════════════════

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
GRAY   = "\033[90m"
WHITE  = "\033[97m"

def _c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"

STATUS_PASS = _c(GREEN,  "[PASS]")
STATUS_FAIL = _c(RED,    "[FAIL]")
STATUS_WARN = _c(YELLOW, "[WARN]")
STATUS_SKIP = _c(GRAY,   "[SKIP]")


# ══════════════════════════════════════════════
# 检查结果数据结构
# ══════════════════════════════════════════════

@dataclass
class CheckResult:
    name: str
    passed: bool
    warning: bool = False
    skipped: bool = False
    elapsed_ms: float = 0.0
    detail: str = ""
    error: str = ""
    fatal: bool = True          # False = 失败不影响总体 PASS/FAIL

    @property
    def status_icon(self) -> str:
        if self.skipped: return STATUS_SKIP
        if self.warning: return STATUS_WARN
        if self.passed:  return STATUS_PASS
        return STATUS_FAIL

    @property
    def elapsed_str(self) -> str:
        if self.skipped or self.elapsed_ms == 0:
            return _c(GRAY, "  --   ")
        if self.elapsed_ms < 1000:
            return _c(CYAN, f"{self.elapsed_ms:>6.0f}ms")
        return _c(CYAN, f"{self.elapsed_ms/1000:>6.2f}s ")


@dataclass
class CheckSuite:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult):
        self.results.append(r)

    @property
    def total(self) -> int:
        return len([r for r in self.results if not r.skipped])

    @property
    def passed_count(self) -> int:
        return len([r for r in self.results if r.passed and not r.skipped])

    @property
    def fatal_failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and not r.skipped and not r.warning and r.fatal]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.warning]

    @property
    def all_passed(self) -> bool:
        return len(self.fatal_failures) == 0


# ══════════════════════════════════════════════
# 计时工具
# ══════════════════════════════════════════════

class _Timer:
    elapsed_ms: float = 0.0          # ← 加这一行初始化
    def __enter__(self):
        self._start = time.time()
        return self
    def __exit__(self, *_):
        self.elapsed_ms = (time.time() - self._start) * 1000


# ══════════════════════════════════════════════
# 各检查项实现
# ══════════════════════════════════════════════

def check_env_vars() -> CheckResult:
    """CHECK-1: 环境变量完整性"""
    required = {
        "SILICONFLOW_EMBEDDING_API_KEY": "Embedding API Key",
        "SILICONFLOW_LLM_API_KEY":       "LLM API Key",
        "NEO4J_URI":                     "Neo4j 地址",
        "NEO4J_USER":                    "Neo4j 用户名",
        "NEO4J_PASSWORD":                "Neo4j 密码",
        "CHROMA_PATH":                   "Chroma 向量库路径",
        "EMBEDDING_MODEL":               "Embedding 模型名",
        "LLM_MODEL":                     "LLM 模型名",
    }

    with _Timer() as t:
        # 先触发 config 加载（会自动读取 .env）
        from agent_service import config as cfg

        missing = []
        present = []
        for env_key, desc in required.items():
            val = getattr(cfg, env_key, None) or os.getenv(env_key, "")
            if not val or val.strip() == "":
                missing.append(f"{env_key}（{desc}）")
            else:
                # API Key 脱敏显示
                if "KEY" in env_key or "PASSWORD" in env_key:
                    display = val[:8] + "****"
                else:
                    display = val
                present.append(f"{env_key}={display}")

    if missing:
        return CheckResult(
            name="环境变量",
            passed=False,
            elapsed_ms=t.elapsed_ms,
            detail=f"{len(present)}/{len(required)} 个 Key 就绪",
            error=f"缺失: {', '.join(missing)}",
        )
    return CheckResult(
        name="环境变量",
        passed=True,
        elapsed_ms=t.elapsed_ms,
        detail=f"{len(required)}/{len(required)} 个 Key 全部就绪",
    )


def check_embedding_model() -> CheckResult:
    """CHECK-2: Embedding 模型调用"""
    with _Timer() as t:
        try:
            from agent_service import config as cfg
            from agent_service.tools.vector_search import get_embedding

            test_text = "不良贷款率"
            vec = get_embedding(test_text)

            if not isinstance(vec, list) or len(vec) == 0:
                return CheckResult(
                    name="Embedding 模型",
                    passed=False,
                    elapsed_ms=t.elapsed_ms,
                    error=f"返回值异常: type={type(vec)}, len={len(vec) if isinstance(vec, list) else 'N/A'}",
                )

            dim = len(vec)
            expected_dim = cfg.EMBEDDING_DIM
            dim_ok = (dim == expected_dim)
            preview = f"[{vec[0]:.4f}, {vec[1]:.4f}, ...]"

            if not dim_ok:
                return CheckResult(
                    name="Embedding 模型",
                    passed=False,
                    elapsed_ms=t.elapsed_ms,
                    detail=f'"{test_text}" → dim={dim}（期望 {expected_dim}）{preview}',
                    error=f"向量维度不符: 实际={dim}, 期望={expected_dim}",
                )

            return CheckResult(
                name="Embedding 模型",
                passed=True,
                elapsed_ms=t.elapsed_ms,
                detail=f'"{test_text}" → dim={dim} ✓  {preview}  model={cfg.EMBEDDING_MODEL}',
            )

        except Exception as e:
            return CheckResult(
                name="Embedding 模型",
                passed=False,
                elapsed_ms=t.elapsed_ms,
                error=str(e),
            )


def check_llm_model() -> CheckResult:
    """CHECK-3: LLM 模型调用"""
    with _Timer() as t:
        try:
            from agent_service import config as cfg
            from agent_service.llm.client import get_llm_client
            from langchain_core.messages import HumanMessage, SystemMessage

            client = get_llm_client()
            messages = [
                SystemMessage(content="你是助手，请简短回答。"),
                HumanMessage(content="请回复：OK"),
            ]
            result = client.invoke(messages)
            reply = result.content.strip() if hasattr(result, "content") else str(result).strip()

            if not reply:
                return CheckResult(
                    name="LLM 模型",
                    passed=False,
                    elapsed_ms=t.elapsed_ms,
                    error="LLM 返回空内容",
                )

            # 截断过长回复用于展示
            display_reply = reply[:60] + "..." if len(reply) > 60 else reply

            return CheckResult(
                name="LLM 模型",
                passed=True,
                elapsed_ms=t.elapsed_ms,
                detail=f'"请回复：OK" → "{display_reply}"  model={cfg.LLM_MODEL}',
            )

        except Exception as e:
            return CheckResult(
                name="LLM 模型",
                passed=False,
                elapsed_ms=t.elapsed_ms,
                error=str(e),
            )


def check_neo4j_connection() -> CheckResult:
    """CHECK-4: Neo4j 连接验证"""
    with _Timer() as t:
        try:
            from agent_service import config as cfg
            from agent_service.tools.theme_tools import get_neo4j_driver

            driver = get_neo4j_driver()
            driver.verify_connectivity()

            return CheckResult(
                name="Neo4j 连接",
                passed=True,
                elapsed_ms=t.elapsed_ms,
                detail=f"uri={cfg.NEO4J_URI}  user={cfg.NEO4J_USER}",
            )

        except Exception as e:
            from agent_service import config as cfg
            return CheckResult(
                name="Neo4j 连接",
                passed=False,
                elapsed_ms=t.elapsed_ms,
                error=f"{type(e).__name__}: {str(e)[:120]}",
            )


def check_neo4j_data() -> CheckResult:
    """CHECK-5: Neo4j 业务数据完整性"""
    with _Timer() as t:
        try:
            from agent_service.tools.theme_tools import get_neo4j_driver

            driver = get_neo4j_driver()
            with driver.session() as session:
                # 查询各业务节点数量
                queries = {
                    "总节点":              "MATCH (n) RETURN count(n) as cnt",
                    "THEME 主题":          "MATCH (n:THEME) RETURN count(n) as cnt",
                    "INDICATOR 指标":      "MATCH (n:INDICATOR) RETURN count(n) as cnt",
                    "INSIGHT_TEMPLATE":    "MATCH (n:INSIGHT_TEMPLATE) RETURN count(n) as cnt",
                    "COMBINEDQUERY_TEMPLATE": "MATCH (n:COMBINEDQUERY_TEMPLATE) RETURN count(n) as cnt",
                }
                counts = {}
                for label, cypher in queries.items():
                    r = session.run(cypher).single()
                    counts[label] = r["cnt"] if r else 0

            # 核心数据必须 > 0
            critical_zero = [k for k in ["THEME 主题", "INDICATOR 指标"] if counts.get(k, 0) == 0]
            # 模板数据为 0 只是警告
            template_zero = [k for k in ["INSIGHT_TEMPLATE", "COMBINEDQUERY_TEMPLATE"] if counts.get(k, 0) == 0]

            detail_parts = [f"{k}: {v:,}" for k, v in counts.items()]
            detail = "  |  ".join(detail_parts)

            if critical_zero:
                return CheckResult(
                    name="Neo4j 数据",
                    passed=False,
                    elapsed_ms=t.elapsed_ms,
                    detail=detail,
                    error=f"核心数据为空: {', '.join(critical_zero)}，请执行 init_ontology.py",
                )

            if template_zero:
                return CheckResult(
                    name="Neo4j 数据",
                    passed=True,
                    warning=True,
                    elapsed_ms=t.elapsed_ms,
                    detail=detail,
                    error=f"⚠️  模板数据为空: {', '.join(template_zero)}，请执行 extract_templates.py",
                )

            return CheckResult(
                name="Neo4j 数据",
                passed=True,
                elapsed_ms=t.elapsed_ms,
                detail=detail,
            )

        except Exception as e:
            return CheckResult(
                name="Neo4j 数据",
                passed=False,
                elapsed_ms=t.elapsed_ms,
                error=f"{type(e).__name__}: {str(e)[:120]}",
            )


def check_chroma_mount() -> CheckResult:
    """CHECK-6: Chroma 向量库文件挂载"""
    with _Timer() as t:
        try:
            from agent_service import config as cfg

            chroma_path = Path(cfg.CHROMA_PATH)

            if not chroma_path.exists():
                return CheckResult(
                    name="Chroma 挂载",
                    passed=False,
                    elapsed_ms=t.elapsed_ms,
                    error=f"目录不存在: {chroma_path}，请检查 -v 挂载配置",
                )

            # 检查 SQLite 文件（Chroma 0.4.x 的核心文件）
            sqlite_file = chroma_path / "chroma.sqlite3"
            if not sqlite_file.exists():
                return CheckResult(
                    name="Chroma 挂载",
                    passed=False,
                    warning=True,
                    elapsed_ms=t.elapsed_ms,
                    detail=f"目录存在: {chroma_path}",
                    error="chroma.sqlite3 不存在，向量库未初始化，请执行 indicator_vectorizer.py --rebuild",
                )

            # 计算目录大小
            total_size = sum(f.stat().st_size for f in chroma_path.rglob("*") if f.is_file())
            size_mb = total_size / (1024 * 1024)

            return CheckResult(
                name="Chroma 挂载",
                passed=True,
                elapsed_ms=t.elapsed_ms,
                detail=f"path={chroma_path}  size={size_mb:.1f}MB  collection={cfg.COLLECTION_NAME}",
            )

        except Exception as e:
            return CheckResult(
                name="Chroma 挂载",
                passed=False,
                elapsed_ms=t.elapsed_ms,
                error=str(e),
            )


def check_chroma_data() -> CheckResult:
    """CHECK-7: Chroma 向量库数据量"""
    with _Timer() as t:
        try:
            from agent_service import config as cfg
            from agent_service.tools.vector_search import get_vector_stats

            stats = get_vector_stats()

            if not stats.get("success"):
                return CheckResult(
                    name="Chroma 数据",
                    passed=False,
                    elapsed_ms=t.elapsed_ms,
                    error=stats.get("error", "get_vector_stats 返回 success=False"),
                )

            count = stats.get("total_indicators", 0)

            if count == 0:
                return CheckResult(
                    name="Chroma 数据",
                    passed=False,
                    warning=True,
                    elapsed_ms=t.elapsed_ms,
                    detail=f"collection={cfg.COLLECTION_NAME}",
                    error="向量库为空 (count=0)，请执行 indicator_vectorizer.py --rebuild",
                )

            # 数据量偏少警告（少于100条视为异常）
            if count < 100:
                return CheckResult(
                    name="Chroma 数据",
                    passed=True,
                    warning=True,
                    elapsed_ms=t.elapsed_ms,
                    detail=f"向量总数: {count:,}  collection={cfg.COLLECTION_NAME}",
                    error=f"⚠️  向量数量偏少 ({count} < 100)，数据可能未完整导入",
                )

            return CheckResult(
                name="Chroma 数据",
                passed=True,
                elapsed_ms=t.elapsed_ms,
                detail=f"向量总数: {count:,}  collection={cfg.COLLECTION_NAME}  model={cfg.EMBEDDING_MODEL}",
            )

        except Exception as e:
            return CheckResult(
                name="Chroma 数据",
                passed=False,
                elapsed_ms=t.elapsed_ms,
                error=str(e),
            )


def check_vector_search() -> CheckResult:
    """CHECK-8: 向量检索端到端"""
    with _Timer() as t:
        try:
            from agent_service.tools.vector_search import search_indicators_by_vector

            test_query = "不良贷款率"
            result = search_indicators_by_vector(test_query, top_k=3)

            if not result.get("success"):
                return CheckResult(
                    name="向量检索",
                    passed=False,
                    elapsed_ms=t.elapsed_ms,
                    error=result.get("error", "search 返回 success=False"),
                )

            count = result.get("indicator_count", 0)
            indicators = result.get("indicators", [])

            if count == 0:
                return CheckResult(
                    name="向量检索",
                    passed=False,
                    warning=True,
                    elapsed_ms=t.elapsed_ms,
                    detail=f'query="{test_query}"',
                    error="搜索结果为空，向量库可能未初始化",
                )

            top1 = indicators[0]
            top1_info = (
                f'top1="{top1.get("alias", "?")} '
                f'(sim={top1.get("similarity_score", 0):.3f})'
                f'  theme={top1.get("theme_alias", "?")}"'
            )

            return CheckResult(
                name="向量检索",
                passed=True,
                elapsed_ms=t.elapsed_ms,
                detail=f'query="{test_query}" → {count} 条结果  {top1_info}',
            )

        except Exception as e:
            return CheckResult(
                name="向量检索",
                passed=False,
                elapsed_ms=t.elapsed_ms,
                error=str(e),
            )


_http_port: int = 8000


def check_http_health() -> CheckResult:
    """CHECK-9: HTTP /health 接口（非致命）"""
    with _Timer() as t:
        try:
            import urllib.request
            import json as _json

            url = f"http://localhost:{_http_port}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = _json.loads(resp.read().decode())
                status = body.get("status", "unknown")
                services = body.get("services", {})
                concurrency = body.get("concurrency", {})

                svc_str = "  ".join(
                    f"{k}={'✓' if v else '✗'}" for k, v in services.items()
                )
                conc_str = f"并发={concurrency.get('current',0)}/{concurrency.get('max','?')}"

                if status == "healthy":
                    return CheckResult(
                        name="HTTP /health",
                        passed=True,
                        fatal=False,
                        elapsed_ms=t.elapsed_ms,
                        detail=f"status={status}  {svc_str}  {conc_str}",
                    )
                else:
                    return CheckResult(
                        name="HTTP /health",
                        passed=False,
                        fatal=False,
                        warning=True,
                        elapsed_ms=t.elapsed_ms,
                        detail=f"status={status}  {svc_str}",
                        error=f"服务处于 degraded 状态",
                    )

        except Exception as e:
            err_msg = str(e)
            # 连接拒绝说明服务未启动，这是正常的预检场景
            if "Connection refused" in err_msg or "connection refused" in err_msg.lower():
                return CheckResult(
                    name="HTTP /health",
                    passed=False,
                    fatal=False,
                    skipped=False,
                    warning=True,
                    elapsed_ms=t.elapsed_ms,
                    detail="服务未启动（预检模式下可忽略）",
                    error=f"Connection refused: localhost:{_http_port}",
                )
            return CheckResult(
                name="HTTP /health",
                passed=False,
                fatal=False,
                elapsed_ms=t.elapsed_ms,
                error=f"{type(e).__name__}: {err_msg[:100]}",
            )


def check_http_memory() -> CheckResult:
    """CHECK-10: HTTP /health/memory 接口（非致命）"""
    with _Timer() as t:
        try:
            import urllib.request
            import json as _json

            url = f"http://localhost:{_http_port}/health/memory"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = _json.loads(resp.read().decode())
                status = body.get("status", "unknown")

                if status == "ok":
                    active  = body.get("active_threads", 0)
                    total   = body.get("total_threads", 0)
                    expired = body.get("expired_threads", 0)
                    ttl     = body.get("ttl_seconds", 0)
                    return CheckResult(
                        name="HTTP /health/memory",
                        passed=True,
                        fatal=False,
                        elapsed_ms=t.elapsed_ms,
                        detail=(
                            f"status=ok  active={active}  total={total}  "
                            f"expired={expired}  ttl={ttl}s"
                        ),
                    )
                else:
                    return CheckResult(
                        name="HTTP /health/memory",
                        passed=False,
                        fatal=False,
                        warning=True,
                        elapsed_ms=t.elapsed_ms,
                        error=f"status={status}  msg={body.get('message', '')}",
                    )

        except Exception as e:
            err_msg = str(e)
            if "Connection refused" in err_msg or "connection refused" in err_msg.lower():
                return CheckResult(
                    name="HTTP /health/memory",
                    passed=False,
                    fatal=False,
                    warning=True,
                    elapsed_ms=t.elapsed_ms,
                    detail="服务未启动（预检模式下可忽略）",
                    error=f"Connection refused: localhost:{_http_port}",
                )
            return CheckResult(
                name="HTTP /health/memory",
                passed=False,
                fatal=False,
                elapsed_ms=t.elapsed_ms,
                error=f"{type(e).__name__}: {err_msg[:100]}",
            )


# ══════════════════════════════════════════════
# 检查项注册表
# ══════════════════════════════════════════════

ALL_CHECKS = {
    "env":        ("环境变量",        check_env_vars),
    "embedding":  ("Embedding 模型",  check_embedding_model),
    "llm":        ("LLM 模型",        check_llm_model),
    "neo4j":      ("Neo4j 连接",      check_neo4j_connection),
    "neo4j_data": ("Neo4j 数据",      check_neo4j_data),
    "chroma":     ("Chroma 挂载",     check_chroma_mount),
    "chroma_data":("Chroma 数据",     check_chroma_data),
    "vector":     ("向量检索",         check_vector_search),
    "http":       ("HTTP /health",    check_http_health),
    "memory":     ("HTTP /memory",    check_http_memory),
}

# 依赖关系：key 失败时跳过 value 列表中的检查
SKIP_IF_FAILED = {
    "env":        ["embedding", "llm", "neo4j", "neo4j_data", "chroma", "chroma_data", "vector"],
    "neo4j":      ["neo4j_data"],
    "chroma":     ["chroma_data", "vector"],
    "embedding":  ["vector"],
}


# ══════════════════════════════════════════════
# 打印逻辑
# ══════════════════════════════════════════════

def _print_header():
    print()
    print(_c(BOLD + WHITE, "═" * 70))
    print(_c(BOLD + WHITE, "  🏥  Theme Template Agent — 健康检查"))
    print(_c(BOLD + WHITE, "═" * 70))
    print()

def _print_result(r: CheckResult, verbose: bool = False):
    # 主行：[STATUS]  检查名称    耗时    详情
    name_col  = f"{r.name:<22}"
    time_col  = r.elapsed_str
    detail    = r.detail if r.detail else ""

    print(f"  {r.status_icon}  {_c(BOLD, name_col)}  {time_col}  {detail}")

    # 错误行（缩进展示）
    if r.error and (not r.passed or r.warning):
        print(f"  {' ' * 8}{'':22}  {'':8}  {_c(RED if not r.passed and not r.warning else YELLOW, r.error)}")

def _print_skip(name: str, reason: str):
    name_col = f"{name:<22}"
    print(f"  {STATUS_SKIP}  {_c(GRAY, name_col)}  {'':8}  {_c(GRAY, f'跳过（{reason} 失败）')}")

def _print_footer(suite: CheckSuite, total_ms: float):
    print()
    print(_c(BOLD + WHITE, "─" * 70))

    passed  = suite.passed_count
    total   = suite.total
    warns   = len(suite.warnings)
    fatals  = len(suite.fatal_failures)

    total_s = total_ms / 1000

    if suite.all_passed:
        if warns > 0:
            icon = "⚠️ "
            color = YELLOW
            summary = f"通过（含 {warns} 个警告）  {passed}/{total}  总耗时 {total_s:.2f}s"
        else:
            icon = "✅"
            color = GREEN
            summary = f"全部通过  {passed}/{total}  总耗时 {total_s:.2f}s"
    else:
        icon = "❌"
        color = RED
        summary = f"存在问题  {passed}/{total} 通过  {fatals} 个致命失败  总耗时 {total_s:.2f}s"

    print(f"  {icon}  {_c(BOLD + color, summary)}")

    if suite.fatal_failures:
        print()
        print(_c(BOLD + RED, "  致命失败项："))
        for r in suite.fatal_failures:
            print(f"    → {_c(RED, r.name)}: {r.error}")

    if warns > 0:
        print()
        print(_c(BOLD + YELLOW, "  警告项（不影响服务启动，但需关注）："))
        for r in suite.warnings:
            print(f"    → {_c(YELLOW, r.name)}: {r.error}")

    print(_c(BOLD + WHITE, "─" * 70))
    print()


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════

def run_checks(only: Optional[str] = None, verbose: bool = False) -> int:
    """
    执行所有检查，返回退出码（0=全部通过，1=有致命失败）
    """
    _print_header()

    suite = CheckSuite()
    skipped_keys: set[str] = set()
    total_start = time.time()

    # 确定要执行的检查列表
    if only:
        keys_to_run = [k for k in ALL_CHECKS if k == only or ALL_CHECKS[k][0] == only]
        if not keys_to_run:
            print(_c(RED, f"  未知的检查项: '{only}'"))
            print(f"  可用项: {', '.join(ALL_CHECKS.keys())}")
            return 1
    else:
        keys_to_run = list(ALL_CHECKS.keys())

    for key in keys_to_run:
        name, check_fn = ALL_CHECKS[key]

        # 检查是否应跳过
        if key in skipped_keys:
            _print_skip(name, "前置依赖")
            r = CheckResult(name=name, passed=False, skipped=True)
            suite.add(r)
            continue

        # 执行检查
        result = check_fn()
        suite.add(result)
        _print_result(result, verbose)

        # 如果失败，标记需要跳过的后续检查
        if not result.passed and not result.warning and result.fatal:
            for dep_key in SKIP_IF_FAILED.get(key, []):
                skipped_keys.add(dep_key)

    total_ms = (time.time() - total_start) * 1000
    _print_footer(suite, total_ms)

    return 0 if suite.all_passed else 1


def main():
    parser = argparse.ArgumentParser(
        description="Theme Template Agent 健康检查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
检查项 (--only 可用值):
  env          环境变量完整性
  embedding    Embedding 模型调用
  llm          LLM 模型调用
  neo4j        Neo4j 连接
  neo4j_data   Neo4j 业务数据完整性
  chroma       Chroma 向量库文件挂载
  chroma_data  Chroma 向量库数据量
  vector       向量检索端到端
  http         HTTP /health 接口
  memory       HTTP /health/memory 接口

示例:
  docker exec theme-template-agent python scripts/healthcheck.py
  docker exec theme-template-agent python scripts/healthcheck.py --only neo4j
  docker exec theme-template-agent python scripts/healthcheck.py --only embedding
        """,
    )
    parser.add_argument("--only",    type=str, help="只执行指定检查项")
    parser.add_argument("--verbose", action="store_true", help="显示详细信息")
    parser.add_argument("--port",    type=int, default=8000, help="HTTP 服务端口（默认 8000）")
    args = parser.parse_args()

    global _http_port
    _http_port = args.port

    exit_code = run_checks(only=args.only, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
