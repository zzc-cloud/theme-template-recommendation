"""LLM trace 事件的 MySQL 存取。

存储层保持“哑存储”职责：它只把调用方传入的事件字段落到 `llm_trace_events` 表，
不重新解释、不裁剪、不改写 provider payload 的语义。input/output 到底是
OpenAI-compatible request/response，还是错误信息，由上游采集点决定。
"""

from __future__ import annotations

import json
from typing import Any

from ..config import MYSQL_CONFIG

# 查询接口只允许返回这三类 LLM trace 事件，避免误把未来新增的内部事件暴露出去。
# 写入侧不在这里做白名单校验，由调用方保证 event_type 语义正确。
ALLOWED_EVENT_TYPES = ("llm_input", "llm_output", "llm_error")


def _connect():
    """创建 MySQL 连接。

    使用 DictCursor 是为了让查询结果直接按字段名访问，方便 API 层 JSON 返回。
    """
    import pymysql

    return pymysql.connect(**MYSQL_CONFIG, cursorclass=pymysql.cursors.DictCursor)


def _json_dumps(value: Any) -> str:
    """统一 JSON 序列化入口，保证中文不转义，未知对象兜底转字符串。

    payload/token_usage 入库前必须变成 JSON 文本；这里不做字段筛选，避免破坏
    provider 原始 request/response 的排障价值。
    """
    return json.dumps(value, ensure_ascii=False, default=str)


def save_trace_event(event: dict[str, Any]) -> None:
    """保存单条 LLM trace 事件。

    调用方负责生成 `event_id` 并填充关联维度；存储层只做 JSON 字段序列化和落库。
    `payload` 保存 provider request/response 或错误信息，`token_usage` 单独成列便于列表展示。
    """
    sql = """
        INSERT INTO llm_trace_events (
            event_id, event_type, user_id, conversation_id, thread_id, request_id,
            run_id, parent_run_id, model_name, token_usage, payload
        ) VALUES (
            %(event_id)s, %(event_type)s, %(user_id)s, %(conversation_id)s, %(thread_id)s, %(request_id)s,
            %(run_id)s, %(parent_run_id)s, %(model_name)s, %(token_usage)s, %(payload)s
        )
    """
    params = {
        **event,
        # MySQL 表中这两个字段是 JSON 文本；写入前统一序列化，读取时再反序列化。
        "token_usage": _json_dumps(event.get("token_usage")) if event.get("token_usage") is not None else None,
        "payload": _json_dumps(event.get("payload", {})),
    }
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
        conn.commit()


def list_trace_events_by_thread(
    thread_id: str,
    limit: int = 200,
    event_type: str | None = None,
    request_id: str | None = None,
) -> list[dict[str, Any]]:
    """按 thread_id 查询 trace 事件。

    `event_type` 和 `request_id` 是可选过滤器：
    - `event_type` 用于只看 input/output/error 中的一类；
    - `request_id` 用于在同一 thread 的多次 `/recommend` 调用中定位单次请求。

    返回前会把 JSON 字段反序列化，并把 `created_at` 转成 ISO 字符串。
    """
    # 防止前端或误调用一次拉取过多 trace payload；完整 prompt/tools 可能很大。
    safe_limit = max(1, min(int(limit), 1000))
    where = ["thread_id = %s", "event_type IN %s"]
    params: list[Any] = [thread_id, ALLOWED_EVENT_TYPES]
    if event_type:
        where.append("event_type = %s")
        params.append(event_type)
    if request_id:
        where.append("request_id = %s")
        params.append(request_id)
    params.append(safe_limit)

    sql = f"""
        SELECT id, event_id, event_type, user_id, conversation_id, thread_id, request_id,
               run_id, parent_run_id, model_name, token_usage, payload, created_at
        FROM llm_trace_events
        WHERE {' AND '.join(where)}
        ORDER BY created_at ASC, id ASC
        LIMIT %s
    """
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    for row in rows:
        for field in ("token_usage", "payload"):
            if isinstance(row.get(field), str):
                row[field] = json.loads(row[field])
        if row.get("created_at") is not None:
            row["created_at"] = row["created_at"].isoformat()
    return rows
