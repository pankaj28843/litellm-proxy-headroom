from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import asdict, replace
from typing import Any

import httpx
from headroom.cache.compression_store import CompressionEntry

logger = logging.getLogger(__name__)


def _copy_entry(entry: CompressionEntry) -> CompressionEntry:
    return replace(entry, search_queries=list(entry.search_queries))


def _is_expired(entry: CompressionEntry) -> bool:
    try:
        return entry.is_expired()
    except Exception:
        return False


def _query_hash(query: str | None) -> str | None:
    if not query:
        return None
    return hashlib.sha256(query.encode()).hexdigest()


class AnalyticsCcrBackend:
    """Headroom CompressionStore backend backed by the analytics HTTP API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 0.75,
        tenant_prefix: str = "",
        max_local_entries: int = 1000,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._tenant_prefix = tenant_prefix
        self._max_local_entries = max_local_entries
        self._entries: dict[str, CompressionEntry] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=timeout_seconds)

    @classmethod
    def from_env(
        cls,
        *,
        url: str = "",
        tenant_prefix: str = "",
        **_: Any,
    ) -> AnalyticsCcrBackend:
        base_url = (
            os.getenv("HEADROOM_ANALYTICS_URL", "").strip()
            or os.getenv("HEADROOM_CCR_ANALYTICS_URL", "").strip()
            or url.strip()
        )
        if not base_url:
            raise RuntimeError(
                "HEADROOM_ANALYTICS_URL is required for analytics CCR backend"
            )
        timeout = float(
            os.getenv(
                "HEADROOM_CCR_ANALYTICS_TIMEOUT_SECONDS",
                os.getenv("HEADROOM_ANALYTICS_TIMEOUT_SECONDS", "0.75"),
            )
        )
        max_local_entries = int(os.getenv("HEADROOM_CCR_LOCAL_CACHE_ENTRIES", "1000"))
        return cls(
            base_url=base_url,
            timeout_seconds=timeout,
            tenant_prefix=tenant_prefix
            or os.getenv("HEADROOM_CCR_TENANT_PREFIX", "").strip(),
            max_local_entries=max_local_entries,
        )

    def get(self, hash_key: str) -> CompressionEntry | None:
        with self._lock:
            entry = self._entries.get(hash_key)
            if entry is not None and not _is_expired(entry):
                return _copy_entry(entry)
            if entry is not None:
                self._entries.pop(hash_key, None)

        try:
            response = self._client.get(f"{self._base_url}/headroom/ccr/{hash_key}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            entry = CompressionEntry(**response.json())
        except Exception as exc:
            logger.warning("analytics CCR get failed for %s: %s", hash_key, exc)
            return None

        if _is_expired(entry):
            return None
        with self._lock:
            self._remember(hash_key, entry)
        return _copy_entry(entry)

    def set(self, hash_key: str, entry: CompressionEntry) -> None:
        entry = _copy_entry(entry)
        with self._lock:
            previous = self._entries.get(hash_key)
            self._remember(hash_key, entry)

        if self._content_changed(previous, entry):
            self._put_entry(hash_key, entry)
        if previous is not None and entry.retrieval_count > previous.retrieval_count:
            self._post_retrieval(hash_key, previous, entry)

    def delete(self, hash_key: str) -> bool:
        with self._lock:
            existed = self._entries.pop(hash_key, None) is not None
        return existed

    def exists(self, hash_key: str) -> bool:
        entry = self.get(hash_key)
        return entry is not None and not _is_expired(entry)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._entries)

    def items(self) -> list[tuple[str, CompressionEntry]]:
        with self._lock:
            return [(key, _copy_entry(entry)) for key, entry in self._entries.items()]

    def get_stats(self) -> dict[str, Any]:
        return {
            "backend_type": "analytics-http",
            "entry_count": self.count(),
            "base_url": self._base_url,
            "tenant_prefix": self._tenant_prefix,
            "timeout_seconds": self._timeout_seconds,
        }

    def _remember(self, hash_key: str, entry: CompressionEntry) -> None:
        self._entries[hash_key] = _copy_entry(entry)
        while len(self._entries) > self._max_local_entries:
            oldest_key = next(iter(self._entries))
            self._entries.pop(oldest_key, None)

    @staticmethod
    def _content_changed(
        previous: CompressionEntry | None,
        entry: CompressionEntry,
    ) -> bool:
        if previous is None:
            return True
        return any(
            getattr(previous, field) != getattr(entry, field)
            for field in (
                "original_content",
                "compressed_content",
                "original_tokens",
                "compressed_tokens",
                "original_item_count",
                "compressed_item_count",
                "tool_name",
                "tool_call_id",
                "query_context",
                "tool_signature_hash",
                "compression_strategy",
                "ttl",
            )
        )

    def _put_entry(self, hash_key: str, entry: CompressionEntry) -> None:
        try:
            response = self._client.put(
                f"{self._base_url}/headroom/ccr/{hash_key}",
                json=asdict(entry),
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("analytics CCR set failed for %s: %s", hash_key, exc)

    def _post_retrieval(
        self,
        hash_key: str,
        previous: CompressionEntry,
        entry: CompressionEntry,
    ) -> None:
        latest_query = None
        if len(entry.search_queries) > len(previous.search_queries):
            latest_query = entry.search_queries[-1]
        try:
            response = self._client.post(
                f"{self._base_url}/headroom/ccr/{hash_key}/retrievals",
                json={
                    "retrieval_source": "headroom_ccr_backend",
                    "query_hash": _query_hash(latest_query),
                    "result_count": entry.original_item_count,
                    "success": True,
                },
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning(
                "analytics CCR retrieval record failed for %s: %s", hash_key, exc
            )


def create_analytics_ccr_backend(**kwargs: Any) -> AnalyticsCcrBackend:
    return AnalyticsCcrBackend.from_env(**kwargs)
