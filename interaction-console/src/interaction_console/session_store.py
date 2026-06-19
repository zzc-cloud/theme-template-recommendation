"""进程内事件缓存。

该缓存只服务第一版调试和刷新恢复辅助：API 可以按 thread_id 查询当前进程已看到的
标准化事件。它不是数据库，也不跨进程共享；服务重启后内容会丢失。
"""

from dataclasses import dataclass, field


@dataclass
class SessionStore:
    """按 thread_id 保存标准化后的事件字典列表。"""

    events: dict[str, list[dict]] = field(default_factory=dict)

    def append(self, thread_id: str, event: dict) -> None:
        """追加单个事件；调用方负责保证 event 已符合 ConsoleEvent envelope。"""
        self.events.setdefault(thread_id, []).append(event)

    def list_events(self, thread_id: str) -> list[dict]:
        """返回指定线程的事件列表；未知线程返回空列表。"""
        return self.events.get(thread_id, [])

    def max_seq(self, thread_id: str) -> int:
        """返回指定线程当前已缓存事件的最大 seq；未知线程返回 0。"""
        max_value = 0
        for event in self.events.get(thread_id, []):
            try:
                max_value = max(max_value, int(event.get("seq", 0) or 0))
            except (TypeError, ValueError):
                continue
        return max_value


session_store = SessionStore()
