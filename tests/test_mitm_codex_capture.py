from __future__ import annotations

import json
from importlib import util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON_PATH = REPO_ROOT / "scripts" / "mitmproxy_codex_full_capture.py"
RUNNER_PATH = REPO_ROOT / "scripts" / "mitm_codex_capture.py"


def _load_script(name: str, path: Path):
    spec = util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mitm_full_capture_preserves_request_body_values() -> None:
    addon = _load_script("mitmproxy_codex_full_capture", ADDON_PATH)
    payload = {
        "model": "gpt-5.5",
        "prompt_cache_key": "cache-secret",
        "previous_response_id": "resp-secret",
        "stream": True,
        "store": False,
        "tools": [{"type": "web_search"}, {"type": "function", "function": {"name": "run"}}],
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "raw prompt must not leak"}],
            }
        ],
        "client_metadata": {"x-codex-turn-metadata": "metadata-secret"},
    }

    class Message:
        headers = {"content-type": "application/json"}

        @staticmethod
        def get_content(strict: bool = False) -> bytes:
            return json.dumps(payload).encode()

    body = addon._body_record(Message())
    encoded = json.dumps(body, sort_keys=True)

    assert body["json"]["model"] == "gpt-5.5"
    assert body["json"]["tools"][1]["function"]["name"] == "run"
    assert body["json"]["input"][0]["content"][0]["text"] == "raw prompt must not leak"
    assert body["json"]["prompt_cache_key"] == "cache-secret"
    assert body["json"]["previous_response_id"] == "resp-secret"
    assert body["json"]["client_metadata"]["x-codex-turn-metadata"] == "metadata-secret"
    assert "raw prompt must not leak" in encoded
    assert "cache-secret" in encoded
    assert "resp-secret" in encoded
    assert "metadata-secret" in encoded


def test_mitm_full_capture_preserves_header_values() -> None:
    addon = _load_script("mitmproxy_codex_full_capture", ADDON_PATH)

    headers = addon._headers_to_records(
        {
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Codex-Turn-State": "opaque-value-123",
            "Content-Type": "application/json",
        }
    )
    encoded = json.dumps(headers, sort_keys=True)

    assert {"name": "Authorization", "value": "Bearer secret"} in headers
    assert {"name": "Cookie", "value": "session=secret"} in headers
    assert {"name": "X-Codex-Turn-State", "value": "opaque-value-123"} in headers
    assert "Bearer secret" in encoded
    assert "session=secret" in encoded
    assert "opaque-value-123" in encoded


def test_mitm_codex_capture_dry_run_records_proxy_and_ca_contract(tmp_path: Path) -> None:
    runner = _load_script("mitm_codex_capture", RUNNER_PATH)
    args = runner.parse_args(
        [
            "--marker",
            "MITM_TEST",
            "--artifact-root",
            str(tmp_path),
            "--prompt",
            "Do not edit files. Reply MITM_TEST",
        ]
    )

    plan = runner.build_plan(args)
    public_plan = runner._public_plan(plan)

    assert plan["mode"] == "dry-run"
    assert plan["lane"] == "direct"
    assert plan["mitmproxy"]["command"][:5] == [
        "uvx",
        "--from",
        "mitmproxy",
        "mitmdump",
        "-q",
    ]
    assert "--set" in plan["mitmproxy"]["command"]
    assert plan["mitmproxy"]["ca_certificate"].endswith("mitmproxy-ca-cert.pem")
    assert plan["mitmproxy"]["capture_path"].endswith("flows.jsonl")
    assert plan["codex"]["command"][-1] == "-"
    assert plan["codex"]["prompt_source"]["turns"] == 1
    assert "Do not edit files. Reply MITM_TEST" not in json.dumps(public_plan)
    assert "CODEX_MITM_CAPTURE_OK" not in json.dumps(public_plan)
    assert plan["codex"]["prompt_source"]["bytes"] == len(
        b"Do not edit files. Reply MITM_TEST"
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in plan["codex"]["command"]
    assert 'model_provider="openai"' in plan["codex"]["command"]
    assert plan["safety"]["capture"] == "full_fidelity_local_jsonl_raw_headers_and_bodies"
    assert plan["safety"]["disable_websockets_for_capture"] is False


def test_mitm_codex_capture_can_plan_resumed_turns_without_prompt_text(
    tmp_path: Path,
) -> None:
    runner = _load_script("mitm_codex_capture", RUNNER_PATH)
    args = runner.parse_args(
        [
            "--marker",
            "MITM_MULTI",
            "--artifact-root",
            str(tmp_path),
            "--session-turns",
            "3",
            "--prompt",
            "Do not edit files. Reply MULTI",
        ]
    )

    plan = runner.build_plan(args)
    public_plan = runner._public_plan(plan)
    encoded = json.dumps(public_plan)

    assert plan["codex"]["prompt_source"]["turns"] == 3
    assert len(plan["codex"]["commands"]) == 3
    assert "resume" not in plan["codex"]["commands"][0]
    assert "resume" in plan["codex"]["commands"][1]
    assert runner.SESSION_ID_PLACEHOLDER in plan["codex"]["commands"][1]
    assert runner.SESSION_ID_PLACEHOLDER in plan["codex"]["commands"][2]
    assert len(plan["_prompts"]) == 3
    assert "Do not edit files. Reply MULTI" not in encoded
    assert "MITM_MULTI-turn-03" not in encoded


def test_mitm_codex_capture_can_force_http_responses_for_direct_lane(
    tmp_path: Path,
) -> None:
    runner = _load_script("mitm_codex_capture", RUNNER_PATH)
    args = runner.parse_args(
        [
            "--marker",
            "MITM_HTTP",
            "--artifact-root",
            str(tmp_path),
            "--disable-websockets-for-capture",
        ]
    )

    plan = runner.build_plan(args)

    assert plan["safety"]["disable_websockets_for_capture"] is True
    assert 'model_provider="openai-http-capture"' in plan["codex"]["command"]
    assert (
        "model_providers.openai-http-capture.supports_websockets=false"
        in plan["codex"]["command"]
    )
    assert (
        'model_providers.openai-http-capture.base_url="https://chatgpt.com/backend-api/codex"'
        in plan["codex"]["command"]
    )


def test_mitm_codex_capture_threads_marker_env_for_proxy_lane(tmp_path: Path) -> None:
    runner = _load_script("mitm_codex_capture", RUNNER_PATH)
    args = runner.parse_args(
        [
            "--marker",
            "MITM_PROXY",
            "--artifact-root",
            str(tmp_path),
            "--lane",
            "proxy",
            "--model",
            "gpt-5.4-mini",
            "--reasoning-effort",
            "low",
            "--model-verbosity",
            "low",
            "--responses-provider-passthrough",
            "off",
        ]
    )

    plan = runner.build_plan(args)

    assert plan["codex"]["environment"] == {
        "CODEX_LITELLM_ANALYTICS_URL": "http://127.0.0.1:28010",
        "CODEX_LITELLM_BASE_URL": "http://10.20.30.1:24040",
        "CODEX_LITELLM_CLIENT": "codex",
        "CODEX_LITELLM_MODEL": "gpt-5.4-mini",
        "CODEX_LITELLM_MODEL_VERBOSITY": "low",
        "CODEX_LITELLM_REASONING_EFFORT": "low",
        "CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH": "off",
        "LITELLM_PROXY_RUN_MARKER": "MITM_PROXY",
    }
