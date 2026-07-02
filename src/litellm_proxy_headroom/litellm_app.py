from __future__ import annotations

import asyncio
import json
from typing import Any

from litellm.proxy.proxy_server import app as litellm_app
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SYSTEM_AS_USER_PREFIX = "System instructions:\n\n"
CHATGPT_BACKED_MODEL_PREFIXES = ("codex-", "gpt-")
CHATGPT_BACKED_MODEL_ALIASES = {
    "sonnet",
    "opus",
    "fable",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-fable-5",
}


def _content_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _content_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        content = value.get("content")
        if content is not None:
            return _content_text(content)
    return None


def _is_chatgpt_backed_alias(model: Any) -> bool:
    return isinstance(model, str) and (
        model in CHATGPT_BACKED_MODEL_ALIASES
        or model.startswith(CHATGPT_BACKED_MODEL_PREFIXES)
    )


def _prepend_text_to_user_message(message: dict[str, Any], text: str) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = f"{text}\n\n{content}" if content else text
    elif isinstance(content, list):
        message["content"] = [{"type": "text", "text": text}, *content]
    else:
        message["content"] = text


def _rewrite_anthropic_system_as_user(payload: dict[str, Any]) -> bool:
    if not _is_chatgpt_backed_alias(payload.get("model")):
        return False
    if "system" not in payload:
        return False
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False

    system_text = _content_text(payload.get("system"))
    if not system_text:
        return False
    payload.pop("system", None)

    user_text = SYSTEM_AS_USER_PREFIX + system_text
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            _prepend_text_to_user_message(message, user_text)
            return True

    messages.insert(0, {"role": "user", "content": user_text})
    return True


class AnthropicSystemAsUserMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/v1/messages"
        ):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, receive, send)
                return
            body.extend(message.get("body", b""))
            more_body = bool(message.get("more_body", False))

        request_body = bytes(body)
        rewritten_body = request_body
        try:
            payload = json.loads(request_body)
            if isinstance(payload, dict) and _rewrite_anthropic_system_as_user(payload):
                rewritten_body = json.dumps(
                    payload,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
        except TypeError, ValueError, UnicodeDecodeError:
            rewritten_body = request_body

        sent = False

        async def replay_receive() -> Message:
            nonlocal sent
            if sent:
                await asyncio.Event().wait()
            sent = True
            return {
                "type": "http.request",
                "body": rewritten_body,
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)


app = AnthropicSystemAsUserMiddleware(litellm_app)
