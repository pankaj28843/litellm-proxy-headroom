from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised by mitmdump, not ordinary unit tests.
    from mitmproxy import http
except ModuleNotFoundError:  # pragma: no cover - keeps pure helpers importable.
    http = None  # type: ignore[assignment]

CAPTURE_PATH_ENV = "CODEX_MITM_CAPTURE_PATH"
CAPTURE_LANE_ENV = "CODEX_MITM_CAPTURE_LANE"
CAPTURE_MARKER_ENV = "CODEX_MITM_CAPTURE_MARKER"


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _decode_http_text(value: bytes) -> str:
    return value.decode("latin-1", errors="replace")


def _headers_to_records(headers: Any) -> list[dict[str, str]]:
    fields = getattr(headers, "fields", None)
    if fields is not None:
        records = []
        for name, value in fields:
            name_bytes = bytes(name)
            value_bytes = bytes(value)
            records.append(
                {
                    "name": _decode_http_text(name_bytes),
                    "value": _decode_http_text(value_bytes),
                    "name_base64": base64.b64encode(name_bytes).decode("ascii"),
                    "value_base64": base64.b64encode(value_bytes).decode("ascii"),
                }
            )
        return records

    try:
        items = list(headers.items(multi=True))
    except TypeError:
        items = list(headers.items())
    except AttributeError:
        if isinstance(headers, Mapping):
            items = list(headers.items())
        else:
            return []
    return [{"name": str(name), "value": str(value)} for name, value in items]


def _header_value(headers: Any, name: str) -> str | None:
    try:
        value = headers.get(name)
    except AttributeError:
        value = headers.get(name) if isinstance(headers, Mapping) else None
    return str(value) if value is not None else None


def _message_content(message: Any) -> bytes:
    try:
        return bytes(message.get_content(strict=False) or b"")
    except Exception:  # noqa: BLE001 - mitmproxy message decoding should not break capture.
        return b""


def _body_record(message: Any) -> dict[str, Any]:
    content = _message_content(message)
    content_type = _header_value(message.headers, "content-type")
    record: dict[str, Any] = {
        "bytes": len(content),
        "content_type": content_type,
        "base64": base64.b64encode(content).decode("ascii"),
    }
    if not content:
        return record

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        record["utf8_decode_error"] = str(exc)
        record["text_replace"] = content.decode("utf-8", errors="replace")
        return record

    record["text"] = text
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type == "application/json" or content.lstrip().startswith((b"{", b"[")):
        try:
            record["json"] = json.loads(text)
        except json.JSONDecodeError as exc:
            record["json_parse_error"] = str(exc)
    return record


def _request_record(flow: Any) -> dict[str, Any]:
    request = flow.request
    return {
        "event": "request",
        "flow_id": flow.id,
        "captured_at": _utc_timestamp(),
        "lane": os.environ.get(CAPTURE_LANE_ENV),
        "marker": os.environ.get(CAPTURE_MARKER_ENV),
        "request": {
            "method": request.method,
            "scheme": request.scheme,
            "host": request.host,
            "port": request.port,
            "path": request.path,
            "url": getattr(request, "pretty_url", None),
            "http_version": getattr(request, "http_version", None),
            "headers": _headers_to_records(request.headers),
            "body": _body_record(request),
        },
    }


def _response_record(flow: Any) -> dict[str, Any]:
    response = flow.response
    return {
        "event": "response",
        "flow_id": flow.id,
        "captured_at": _utc_timestamp(),
        "lane": os.environ.get(CAPTURE_LANE_ENV),
        "marker": os.environ.get(CAPTURE_MARKER_ENV),
        "response": {
            "status_code": response.status_code,
            "reason": response.reason,
            "headers": _headers_to_records(response.headers),
            "body": _body_record(response),
        },
    }


def _append_record(record: dict[str, Any]) -> None:
    path_value = os.environ.get(CAPTURE_PATH_ENV)
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def request(flow: Any) -> None:
    _append_record(_request_record(flow))


def response(flow: Any) -> None:
    if flow.response is not None:
        _append_record(_response_record(flow))


def error(flow: Any) -> None:
    _append_record(
        {
            "event": "error",
            "flow_id": flow.id,
            "captured_at": _utc_timestamp(),
            "lane": os.environ.get(CAPTURE_LANE_ENV),
            "marker": os.environ.get(CAPTURE_MARKER_ENV),
            "error": str(flow.error) if flow.error else None,
        }
    )
