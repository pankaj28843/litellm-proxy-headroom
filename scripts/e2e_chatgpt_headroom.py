from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

DEFAULT_PROXY_URL = "http://127.0.0.1:4000"
DEFAULT_ANALYTICS_URL = "http://127.0.0.1:8010"
DEFAULT_MODEL_CANDIDATES = "gpt-5.4-mini"


def _env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _get_status(client: httpx.Client, url: str) -> int:
    response = client.get(url)
    return response.status_code


def _completion_payload(model: str, marker: str) -> dict[str, Any]:
    diagnostic_context = "\n".join(
        f"event={idx} component=litellm-headroom-callback route=/v1/chat/completions "
        "signal=repeatable-runtime-evidence status=observed"
        for idx in range(180)
    )
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Reply with only the requested verification marker.",
            },
            {
                "role": "user",
                "content": (
                    f"Return exactly this marker and nothing else: {marker}\n\n"
                    "Diagnostic context follows. It is only there to exercise "
                    "Headroom compression before the request reaches the model.\n\n"
                    f"{diagnostic_context}"
                ),
            },
        ],
    }


def _response_text(response: httpx.Response) -> str:
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def _error_summary(response: httpx.Response) -> str:
    text = response.text
    markers = []
    if "__cf_chl" in text or "challenge-error-text" in text:
        markers.append("cloudflare_challenge")
    if "<html" in text.lower():
        markers.append("html_response")
    content_type = response.headers.get("content-type", "unknown").split(";")[0]
    marker_text = ",".join(markers) if markers else "none"
    return f"content_type={content_type} markers={marker_text} bytes={len(text)}"


def main() -> int:
    proxy_url = os.environ.get(
        "LITELLM_E2E_PROXY_URL",
        os.environ.get("HEADROOM_E2E_PROXY_URL", DEFAULT_PROXY_URL),
    ).rstrip("/")
    analytics_url = os.environ.get(
        "ANALYTICS_BACKEND_URL", DEFAULT_ANALYTICS_URL
    ).rstrip("/")
    api_key = _env_required("LITELLM_MASTER_KEY")
    model_candidates = [
        model.strip()
        for model in os.environ.get(
            "HEADROOM_E2E_MODELS",
            os.environ.get("HEADROOM_E2E_MODEL", DEFAULT_MODEL_CANDIDATES),
        ).split(",")
        if model.strip()
    ]
    marker = f"litellm-headroom-e2e-ok-{int(time.time())}"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    with httpx.Client(timeout=120.0, headers=headers) as client:
        health_status = _get_status(client, f"{proxy_url}/health")
        print(f"health_status={health_status}")
        if health_status >= 500:
            return 1
        analytics_before_response = client.get(f"{analytics_url}/stats")
        analytics_before_response.raise_for_status()
        analytics_before = analytics_before_response.json()

        last_error = ""
        for model in model_candidates:
            response = client.post(
                f"{proxy_url}/v1/chat/completions",
                json=_completion_payload(model, marker),
            )
            if response.status_code != 200:
                last_error = _error_summary(response)
                print(f"model={model} chat_status={response.status_code} {last_error}")
                continue

            text = _response_text(response).strip()
            print(f"model={model} chat_status=200 response={text[:120]!r}")
            if marker not in text:
                last_error = f"marker {marker!r} not found in response {text[:200]!r}"
                continue

            analytics_after = analytics_before
            for _ in range(20):
                analytics_after_response = client.get(f"{analytics_url}/stats")
                analytics_after_response.raise_for_status()
                analytics_after = analytics_after_response.json()
                if analytics_after.get("requests", 0) > analytics_before.get(
                    "requests", 0
                ):
                    break
                time.sleep(0.25)

            analytics_status = _get_status(client, f"{analytics_url}/stats")
            print(f"analytics_stats_status={analytics_status}")
            print(
                "analytics_requests_before="
                f"{analytics_before.get('requests')} "
                f"analytics_requests_after={analytics_after.get('requests')}"
            )
            if analytics_after.get("requests", 0) <= analytics_before.get(
                "requests", 0
            ):
                last_error = "analytics backend request count did not increase"
                continue
            return 0

    print(f"e2e_failed={last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
