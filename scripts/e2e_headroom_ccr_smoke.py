from __future__ import annotations

import hashlib
import json
import os
import time
from importlib.metadata import entry_points
from typing import Any

import httpx
from headroom.cache.compression_store import CompressionStore

ENTRY_POINT_NAME = "analytics-postgres"
DEFAULT_ANALYTICS_URL = "http://127.0.0.1:8010"


def _entry_point_factory() -> Any:
    matches = [
        entry
        for entry in entry_points(group="headroom.ccr_backend")
        if entry.name == ENTRY_POINT_NAME
    ]
    if not matches:
        raise SystemExit(f"missing headroom.ccr_backend entry point {ENTRY_POINT_NAME}")
    return matches[0].load()


def main() -> int:
    analytics_url = os.environ.get("HEADROOM_ANALYTICS_URL", DEFAULT_ANALYTICS_URL)
    marker = f"headroom-ccr-smoke-{int(time.time())}"
    ccr_hash = hashlib.sha256(marker.encode()).hexdigest()[:24]
    original = json.dumps(
        [{"idx": idx, "marker": marker, "payload": f"value-{idx}"} for idx in range(20)]
    )
    compressed = json.dumps([{"idx": 0, "marker": marker}])

    factory = _entry_point_factory()
    backend = factory(url=analytics_url, tenant_prefix="smoke")
    store = CompressionStore(backend=backend, default_ttl=1800)
    stored_hash = store.store(
        original=original,
        compressed=compressed,
        original_tokens=1000,
        compressed_tokens=100,
        original_item_count=20,
        compressed_item_count=1,
        tool_name="analytics_ccr_smoke",
        tool_call_id=marker,
        query_context=marker,
        tool_signature_hash=hashlib.sha256(b"analytics_ccr_smoke").hexdigest()[:16],
        compression_strategy="analytics-ccr-smoke",
        explicit_hash=ccr_hash,
    )
    if stored_hash != ccr_hash:
        raise SystemExit(f"unexpected hash {stored_hash}")

    fresh_backend = factory(url=analytics_url, tenant_prefix="smoke")
    fresh_store = CompressionStore(backend=fresh_backend, default_ttl=1800)
    retrieved = fresh_store.retrieve(ccr_hash)
    if retrieved is None or marker not in retrieved.original_content:
        raise SystemExit("stored CCR entry was not retrievable from fresh backend")

    search_results = fresh_store.search(ccr_hash, marker)
    if not search_results:
        raise SystemExit("stored CCR entry was not searchable")

    with httpx.Client(timeout=5.0) as client:
        entry_response = client.get(
            f"{analytics_url.rstrip('/')}/headroom/ccr/{ccr_hash}"
        )
        entry_response.raise_for_status()
        entry_payload = entry_response.json()
        stats_response = client.get(f"{analytics_url.rstrip('/')}/stats")
        stats_response.raise_for_status()
        stats = stats_response.json()

    if marker not in entry_payload["original_content"]:
        raise SystemExit("analytics CCR API returned the wrong entry")

    print(
        "headroom_ccr_smoke=ok "
        f"hash={ccr_hash} "
        f"marker={marker} "
        f"retrieval_count={entry_payload['retrieval_count']} "
        f"requests={stats['requests']} "
        f"retrievals={stats['retrievals']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
