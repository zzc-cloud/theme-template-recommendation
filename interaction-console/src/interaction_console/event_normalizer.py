"""把上游 DeepAgents SSE chunk 转换为前端稳定可展示的 ConsoleEvent。

上游返回的过程事件并不是单一稳定协议：可能是 JSON dict、字符串化的
LangChain 消息、heartbeat、`[DONE]`，也可能出现暂未识别的新结构。本模块的
核心设计是“尽量解析已知结构，未知结构保留为 raw”，避免单个异常 chunk 中断
整条浏览器事件流。
"""

import ast
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .schemas import ConsoleEvent

# 这些正则用于兼容上游可能返回的字符串化 LangChain 对象，例如
# "content='...' tool_calls=[...]"。理想情况下上游应返回结构化 JSON，但控制台
# 需要对当前真实输出保持兼容。
CONTENT_RE = re.compile(r"content=(?P<quote>['\"])(?P<value>.*?)(?P=quote)(?:\s|$)", re.DOTALL)
NAME_RE = re.compile(r"(?:^|\s)name=(?P<quote>['\"])(?P<value>.*?)(?P=quote)(?:\s|$)", re.DOTALL)
TOOL_CALL_ID_RE = re.compile(r"tool_call_id=(?P<quote>['\"])(?P<value>.*?)(?P=quote)(?:\s|$)", re.DOTALL)
TOOL_CALLS_RE = re.compile(r"tool_calls=(?P<value>\[.*?\])(?:\s+[a-zA-Z_]+=|$)", re.DOTALL)


def parse_sse_data(line: str) -> Any | None:
    """解析单行 SSE 文本，过滤 heartbeat，并尽量还原 data 中的 JSON。"""
    if not line or line.startswith(":") or line.startswith("event:"):
        return None
    if not line.startswith("data:"):
        return line
    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return {"status": "done"}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        # 非 JSON data 不丢弃，后续会以 raw 事件展示，便于排查上游格式变化。
        return data


class EventNormalizer:
    """维护一次前端请求内的事件序号，并输出统一 ConsoleEvent。"""

    def __init__(self, thread_id: str, agent_id: str, initial_seq: int = 0):
        self.thread_id = thread_id
        self.agent_id = agent_id
        self.seq = initial_seq

    def user_message(self, content: str) -> ConsoleEvent:
        """把本地请求中的用户输入包装为标准时间线事件。"""
        return self._event("user_message", {"content": content}, None)

    def normalize_line(self, line: str) -> list[ConsoleEvent]:
        """标准化一行上游 SSE；空 heartbeat 会返回空列表。"""
        parsed = parse_sse_data(line)
        if parsed is None:
            return []
        return self.normalize(parsed)

    def normalize(self, raw: Any) -> list[ConsoleEvent]:
        """公开标准化入口；任何未知结构都降级为 raw，而不是向外抛错。"""
        try:
            events = self._normalize(raw)
        except Exception as exc:  # noqa: BLE001 - 归一化不能让未知上游结构中断流
            events = [("raw", {"reason": f"normalize_failed: {exc}", "value": _safe_text(raw)}, raw)]
        return [self._event(event_type, payload, event_raw) for event_type, payload, event_raw in events]

    def _normalize(self, raw: Any) -> list[tuple[str, dict[str, Any], Any]]:
        if isinstance(raw, str):
            return [("raw", {"value": raw}, raw)]
        if not isinstance(raw, dict):
            return [("raw", {"value": raw}, raw)]

        # 终态和错误状态直接映射为前端事件，不再继续解析内部结构。
        status = raw.get("status")
        if status == "done":
            return [("done", {}, raw)]
        if status == "error":
            return [("error", raw, raw)]

        # DeepAgents 的 HITL interrupt 以 action_requests/review_configs 表达。
        if raw.get("action_requests"):
            return [("interrupt", self._interrupt_payload(raw), raw)]

        # 纯 None middleware chunk 噪声较高，前端无需展示。
        if raw and all("Middleware." in key and value is None for key, value in raw.items()):
            return []

        output: list[tuple[str, dict[str, Any], Any]] = []
        for key, value in raw.items():
            if key == "SkillsMiddleware.before_agent" and isinstance(value, dict):
                output.append(("skill_loaded", {"skills": value.get("skills_metadata", [])}, raw))
                continue
            if "Middleware." in key:
                if value is None:
                    continue
                output.append(("middleware", {"name": key, "value": value}, raw))
                continue
            if key == "model" and isinstance(value, dict):
                output.extend(self._model_events(value, raw))
                continue
            if key == "tools" and isinstance(value, dict):
                output.extend(self._tool_events(value, raw))
                continue

        if output:
            return output
        # 最后的兜底 raw 是有意设计：新上游结构应先可见，再按需要补解析规则。
        return [("raw", {"value": raw}, raw)]

    def _model_events(self, model: dict[str, Any], raw: Any) -> list[tuple[str, dict[str, Any], Any]]:
        """解析模型消息；同一条 model chunk 可能同时产生文本和 tool_use。"""
        output: list[tuple[str, dict[str, Any], Any]] = []
        for message in _as_list(model.get("messages")):
            content = _message_content(message)
            if content:
                event_type = "user_message" if _message_role(message) in {"user", "human"} else "assistant_message"
                output.append((event_type, {"content": content}, raw))
            for tool_call in _message_tool_calls(message):
                output.append(("tool_use", _tool_call_payload(tool_call), raw))
            if not content and not _message_tool_calls(message):
                output.append(("raw", {"value": message}, raw))
        return output

    def _tool_events(self, tools: dict[str, Any], raw: Any) -> list[tuple[str, dict[str, Any], Any]]:
        """解析工具返回消息；content 会尽量 JSON 化，方便前端格式化展示。"""
        output: list[tuple[str, dict[str, Any], Any]] = []
        for message in _as_list(tools.get("messages")):
            parsed = _parse_tool_message(message)
            if parsed:
                output.append(("tool_result", parsed, raw))
            else:
                output.append(("raw", {"value": message}, raw))
        return output

    def _interrupt_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        """提取前端表单所需的 interrupt 信息。

        第一版只取第一个 action_request 和第一个 review_config；这是当前上游输出的
        实际形态。若上游未来一次返回多个确认请求，再扩展这里和前端表单模型。
        """
        action_request = raw.get("action_requests", [{}])[0] or {}
        args = action_request.get("args") or {}
        review_config = None
        review_configs = raw.get("review_configs") or []
        if review_configs:
            review_config = review_configs[0]
        return {
            "interrupt_type": args.get("interrupt_type"),
            "thread_id": args.get("thread_id") or self.thread_id,
            "sections": args.get("sections") or [],
            "action_request": action_request,
            "review_config": review_config,
            "allowed_decisions": (review_config or {}).get("allowed_decisions", []),
        }

    def _event(self, event_type: str, payload: dict[str, Any], raw: Any) -> ConsoleEvent:
        self.seq += 1
        return ConsoleEvent(
            type=event_type,
            thread_id=self.thread_id,
            agent_id=self.agent_id,
            seq=self.seq,
            timestamp=datetime.now(UTC).isoformat(),
            payload=payload,
            raw=raw,
        )


def _as_list(value: Any) -> list[Any]:
    """把 None / 单对象 / 列表统一成列表，简化上游字段形态差异。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _message_content(message: Any) -> str | None:
    """兼容 dict、字符串化对象和真实对象三种消息形态中的 content。"""
    if isinstance(message, dict):
        content = message.get("content")
        return content if isinstance(content, str) and content else None
    if isinstance(message, str):
        return _regex_group(CONTENT_RE, message)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) and content else None


def _message_role(message: Any) -> str | None:
    """提取消息角色；无法判断时返回 None，保持旧 assistant 默认行为。"""
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
        return role.lower() if isinstance(role, str) and role else None
    if isinstance(message, str):
        return None
    role = getattr(message, "role", None) or getattr(message, "type", None)
    return role.lower() if isinstance(role, str) and role else None


def _message_tool_calls(message: Any) -> list[Any]:
    """提取模型消息中的 tool_calls，兼容结构化字段和字符串化列表。"""
    if isinstance(message, dict):
        return _as_list(message.get("tool_calls"))
    if not isinstance(message, str):
        return _as_list(getattr(message, "tool_calls", None))

    match = TOOL_CALLS_RE.search(message)
    if not match:
        return []
    raw_calls = match.group("value")
    try:
        value = ast.literal_eval(raw_calls)
    except (SyntaxError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _tool_call_payload(tool_call: Any) -> dict[str, Any]:
    """把上游 tool_call 形态压平为前端展示所需的最小字段。"""
    if isinstance(tool_call, dict):
        return {
            "tool_name": tool_call.get("name") or tool_call.get("tool_name"),
            "args": tool_call.get("args") or {},
            "id": tool_call.get("id") or tool_call.get("tool_call_id"),
        }
    return {"tool_name": None, "args": {}, "value": _safe_text(tool_call)}


def _parse_tool_message(message: Any) -> dict[str, Any] | None:
    """解析工具返回消息；无法识别时返回 None，让调用方输出 raw。"""
    if isinstance(message, dict):
        content = message.get("content")
        parsed_content, is_json = _parse_content_json(content)
        return {
            "tool_name": message.get("name") or message.get("tool_name"),
            "tool_call_id": message.get("tool_call_id") or message.get("id"),
            "content": parsed_content,
            "is_json": is_json,
        }
    if not isinstance(message, str):
        return None

    content = _regex_group(CONTENT_RE, message)
    name = _regex_group(NAME_RE, message)
    tool_call_id = _regex_group(TOOL_CALL_ID_RE, message)
    if content is None and name is None and tool_call_id is None:
        return None
    parsed_content, is_json = _parse_content_json(content)
    return {
        "tool_name": name,
        "tool_call_id": tool_call_id,
        "content": parsed_content,
        "is_json": is_json,
    }


def _parse_content_json(content: Any) -> tuple[Any, bool]:
    """工具返回 content 常是 JSON 字符串；能解析就返回结构化对象。"""
    if not isinstance(content, str):
        return content, False
    try:
        return json.loads(content), True
    except json.JSONDecodeError:
        return content, False


def _regex_group(pattern: re.Pattern[str], value: str) -> str | None:
    match = pattern.search(value)
    if not match:
        return None
    return match.group("value")


def _safe_text(value: Any) -> str:
    """兜底序列化未知对象，避免调试 raw 本身再次触发异常。"""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)
