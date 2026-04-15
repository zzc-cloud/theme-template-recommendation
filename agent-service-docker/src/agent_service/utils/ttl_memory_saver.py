
import time
import logging
from threading import Lock
from typing import Optional

from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)


class TTLMemorySaver(InMemorySaver):

    def __init__(self, ttl_seconds: int = 86400):
        super().__init__()
        self.ttl_seconds = ttl_seconds
        self._timestamps: dict[str, float] = {}
        self._lock = Lock()

    def put(
        self,
        config,
        checkpoint,
        metadata,
        new_versions,
    ):
        thread_id = config["configurable"]["thread_id"]
        with self._lock:
            self._timestamps[thread_id] = time.time()
        return super().put(config, checkpoint, metadata, new_versions)

    async def aput(
        self,
        config,
        checkpoint,
        metadata,
        new_versions,
    ):
        thread_id = config["configurable"]["thread_id"]
        with self._lock:
            self._timestamps[thread_id] = time.time()
        return await super().aput(config, checkpoint, metadata, new_versions)

    def cleanup_expired(self) -> int:
        now = time.time()

        with self._lock:
            expired_ids = [
                tid for tid, ts in self._timestamps.items()
                if now - ts > self.ttl_seconds
            ]

            if not expired_ids:
                return 0

            for tid in expired_ids:
                self.storage.pop(tid, None)
                self.writes.pop(tid, None)
                self._timestamps.pop(tid, None)

        logger.info(f"[TTLMemorySaver] 清理 {len(expired_ids)} 个过期 thread: {expired_ids}")
        return len(expired_ids)

    def stats(self) -> dict:
        with self._lock:
            total = len(self._timestamps)
            now = time.time()
            active = sum(
                1 for ts in self._timestamps.values()
                if now - ts <= self.ttl_seconds
            )
        return {
            "total_threads": total,
            "active_threads": active,
            "expired_threads": total - active,
            "ttl_seconds": self.ttl_seconds,
        }
