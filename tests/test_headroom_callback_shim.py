import asyncio
import copy
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path


def load_shim_module():
    spec = importlib.util.spec_from_file_location(
        "headroom_litellm_callback",
        Path("config/headroom_litellm_callback.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_shim_callback() -> type:
    return load_shim_module().HeadroomCallback


def test_headroom_callback_shim_exposes_litellm_post_call_hooks() -> None:
    callback = load_shim_callback()(api_key=None)

    assert hasattr(callback, "async_pre_call_hook")
    assert hasattr(callback, "async_success_handler")
    assert hasattr(callback, "async_failure_handler")
    assert hasattr(callback, "async_post_call_success_hook")
    assert hasattr(callback, "async_post_call_failure_hook")


def test_headroom_callback_shim_defines_pre_call_for_litellm_proxy_detection() -> None:
    callback_class = load_shim_callback()

    assert "async_pre_call_hook" in vars(callback_class)


def test_headroom_callback_shim_post_call_hooks_are_noops() -> None:
    callback = load_shim_callback()(api_key=None)

    success_result = asyncio.run(
        callback.async_post_call_success_hook({}, None, {"choices": []})
    )
    failure_result = asyncio.run(
        callback.async_post_call_failure_hook({}, RuntimeError("boom"), None)
    )

    assert success_result is None
    assert failure_result is None


def test_headroom_callback_shim_exports_litellm_config_instance() -> None:
    module = load_shim_module()

    assert isinstance(module.headroom_callback, module.HeadroomCallback)
    assert hasattr(module.headroom_callback, "async_pre_call_hook")
    assert "headroom_callback" in module.__all__


def test_local_compression_uses_agent_90_profile(monkeypatch) -> None:
    from types import SimpleNamespace

    module = load_shim_module()

    captured = {}

    def fake_compress(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            messages=kwargs["messages"],
            tokens_before=1000,
            tokens_after=100,
            tokens_saved=900,
            compression_ratio=0.1,
            transforms_applied=["fake"],
        )

    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    monkeypatch.setattr(callback, "compress", fake_compress)

    result = module.HeadroomCallback()._local_compress(
        [{"role": "user", "content": "large prompt"}],
        "chatgpt",
    )

    assert captured["config"].savings_profile == "agent-90"
    assert result["savings_profile"] == "agent-90"
    assert result["tokens_saved"] == 900


def test_local_compression_uses_headroom_savings_profile_env(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("HEADROOM_SAVINGS_PROFILE", "balanced")
    module = load_shim_module()

    captured = {}

    def fake_compress(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            messages=kwargs["messages"],
            tokens_before=1000,
            tokens_after=300,
            tokens_saved=700,
            compression_ratio=0.7,
            transforms_applied=["fake"],
        )

    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    monkeypatch.setattr(callback, "compress", fake_compress)

    result = module.HeadroomCallback()._local_compress(
        [{"role": "user", "content": "large prompt"}],
        "chatgpt",
    )

    assert captured["config"].savings_profile == "balanced"
    assert result["savings_profile"] == "balanced"
    assert result["tokens_saved"] == 700


def test_responses_shape_summary_redacts_content() -> None:
    from litellm_proxy_headroom.analytics.adapters.litellm.callback import (
        redacted_litellm_payload_shape,
    )

    shape = redacted_litellm_payload_shape(
        {
            "model": "gpt-5.4-mini",
            "instructions": "raw system prompt must not appear",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "raw user prompt"}],
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "raw tool output",
                },
            ],
            "metadata": {
                "request_id": "shape-request",
                "api_key": "secret-key",
            },
        },
        "aresponses",
        response={
            "id": "resp_1",
            "usage": {
                "input_tokens": 20,
                "output_tokens": 3,
                "input_tokens_details": {"cached_tokens": 10},
            },
        },
    )

    serialized = json.dumps(shape, sort_keys=True)
    assert "raw system prompt" not in serialized
    assert "raw user prompt" not in serialized
    assert "raw tool output" not in serialized
    assert "secret-key" not in serialized
    assert shape["hook"] == "async_pre_call_hook"
    assert shape["call_type"] == "aresponses"
    assert shape["input"]["items"][0]["type"] == "message"
    assert shape["input"]["items"][1]["type"] == "function_call_output"
    assert shape["input"]["items"][1]["output_type"] == "str"
    assert shape["response"]["usage"]["keys"] == [
        "input_tokens",
        "input_tokens_details",
        "output_tokens",
    ]
    assert shape["cache_hot_zone"]["mutable_boundary"] == {
        "input_index": 1,
        "item_type": "function_call_output",
    }


def test_responses_cache_hot_zone_fingerprint_is_stable_for_live_output_changes() -> (
    None
):
    from litellm_proxy_headroom.analytics.adapters.litellm.callback import (
        responses_cache_hot_zone_fingerprint,
    )

    fixture = json.loads(
        Path("tests/fixtures/responses_cache_hot_zone_repeated.json").read_text()
    )
    first = responses_cache_hot_zone_fingerprint(fixture["first"])
    second = responses_cache_hot_zone_fingerprint(fixture["second"])
    serialized = json.dumps([first, second], sort_keys=True)

    assert first["stable_prefix_hash"] == second["stable_prefix_hash"]
    assert (
        first["stable_prefix_without_prompt_cache_key_hash"]
        == second["stable_prefix_without_prompt_cache_key_hash"]
    )
    assert first["stable_top_level_hash"] == second["stable_top_level_hash"]
    assert first["stable_input_prefix_hash"] == second["stable_input_prefix_hash"]
    assert first["stable_input_item_hashes"] == second["stable_input_item_hashes"]
    assert first["stable_prefix_bytes"] == second["stable_prefix_bytes"]
    assert first["stable_top_level_keys"] == [
        "instructions",
        "model",
        "prompt_cache_key",
        "tools",
    ]
    assert first["stable_input_item_count"] == 2
    assert first["mutable_boundary"] == {
        "input_index": 2,
        "item_type": "function_call_output",
    }
    assert second["mutable_boundary"] == first["mutable_boundary"]
    assert first["volatile_top_level_keys"] == ["metadata"]
    assert second["volatile_top_level_keys"] == ["metadata"]
    assert "first-marker" not in serialized
    assert "second-marker" not in serialized
    assert "redacted-cache-key" not in serialized
    assert "redacted stable developer instructions" not in serialized
    assert "redacted repeated user task" not in serialized
    assert "redacted live tool output" not in serialized


def test_responses_cache_hot_zone_identifies_prompt_cache_key_only_changes() -> None:
    from litellm_proxy_headroom.analytics.adapters.litellm.callback import (
        responses_cache_hot_zone_fingerprint,
    )

    fixture = json.loads(
        Path("tests/fixtures/responses_cache_hot_zone_repeated.json").read_text()
    )
    first = copy.deepcopy(fixture["first"])
    second = copy.deepcopy(fixture["first"])
    first["prompt_cache_key"] = "cache-key-a"
    second["prompt_cache_key"] = "cache-key-b"

    first_fingerprint = responses_cache_hot_zone_fingerprint(first)
    second_fingerprint = responses_cache_hot_zone_fingerprint(second)
    serialized = json.dumps([first_fingerprint, second_fingerprint], sort_keys=True)

    assert (
        first_fingerprint["stable_prefix_hash"]
        != second_fingerprint["stable_prefix_hash"]
    )
    assert (
        first_fingerprint["stable_prefix_without_prompt_cache_key_hash"]
        == second_fingerprint["stable_prefix_without_prompt_cache_key_hash"]
    )
    assert (
        first_fingerprint["stable_top_level_field_hashes"]["prompt_cache_key"]
        != second_fingerprint["stable_top_level_field_hashes"]["prompt_cache_key"]
    )
    assert (
        first_fingerprint["stable_input_item_hashes"]
        == second_fingerprint["stable_input_item_hashes"]
    )
    assert "cache-key-a" not in serialized
    assert "cache-key-b" not in serialized
    assert "redacted stable developer instructions" not in serialized
    assert "redacted repeated user task" not in serialized


def test_responses_cache_hot_zone_detects_tool_schema_stable_prefix_changes(
    monkeypatch,
) -> None:
    from litellm_proxy_headroom.analytics.adapters.litellm import callback
    from litellm_proxy_headroom.analytics.adapters.litellm.callback import (
        _compact_responses_tools,
        responses_cache_hot_zone_fingerprint,
    )

    fixture = json.loads(
        Path("tests/fixtures/responses_cache_hot_zone_repeated.json").read_text()
    )
    data = copy.deepcopy(fixture["first"])
    data["tools"][0]["parameters"]["$schema"] = "https://json-schema.org/schema"
    data["tools"][0]["parameters"]["properties"]["cmd"]["examples"] = ["pwd"]

    monkeypatch.setattr(callback, "_count_text_tokens", lambda text, model: len(text))

    before = responses_cache_hot_zone_fingerprint(data)
    compaction = _compact_responses_tools(data, "gpt-5.5")
    after = responses_cache_hot_zone_fingerprint(data)

    assert compaction is not None
    assert before["stable_prefix_hash"] != after["stable_prefix_hash"]
    assert (
        before["stable_top_level_field_hashes"]["tools"]
        != after["stable_top_level_field_hashes"]["tools"]
    )


def test_responses_tool_output_compression_preserves_cache_hot_fields(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    module = load_shim_module()
    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    long_output = "verbose tool output " * 80
    original_user_item = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "do not rewrite me"}],
    }
    original_call_item = {
        "type": "function_call",
        "name": "shell",
        "call_id": "call_1",
        "arguments": "{}",
    }
    data = {
        "model": "gpt-5.4-mini",
        "instructions": "preserve instructions",
        "input": [
            dict(original_user_item),
            dict(original_call_item),
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": long_output,
            },
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "preserve reasoning"}],
            },
        ],
        "metadata": {"request_id": "responses-compress"},
    }
    captured = {}

    def fake_compress(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            messages=[{"role": "tool", "content": "compressed tool output"}],
            tokens_before=1000,
            tokens_after=100,
            tokens_saved=900,
            compression_ratio=0.9,
            transforms_applied=["fake-transform"],
        )

    monkeypatch.setattr(callback, "compress", fake_compress)

    shim = module.HeadroomCallback()
    result = asyncio.run(shim.async_pre_call_hook(data=data, call_type="aresponses"))

    assert result is data
    assert captured["messages"] == [{"role": "tool", "content": long_output}]
    assert captured["config"].savings_profile == "agent-90"
    assert data["instructions"] == "preserve instructions"
    assert data["input"][0] == original_user_item
    assert data["input"][1] == original_call_item
    assert data["input"][2]["output"] == "compressed tool output"
    assert data["input"][3]["type"] == "reasoning"

    capture = shim._pending["responses-compress"]
    assert capture.tokens_before == 1000
    assert capture.tokens_after == 100
    assert capture.tokens_saved == 900
    assert capture.skip_reason is None
    assert capture.compression_status is None
    assert "openai:responses:tool_output_units" in capture.transforms_applied


def test_responses_cache_guard_preserves_tools_by_default_while_compressing_output(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    module = load_shim_module()
    from litellm_proxy_headroom.analytics.adapters.litellm import callback
    from litellm_proxy_headroom.analytics.adapters.litellm.callback import (
        responses_cache_hot_zone_fingerprint,
    )

    monkeypatch.delenv("HEADROOM_RESPONSES_TOOL_SCHEMA_COMPACTION", raising=False)
    verbose_description = " ".join(["Verbose schema description."] * 40)
    long_output = "verbose tool output " * 80
    data = {
        "model": "gpt-5.5",
        "prompt_cache_key": "cache-key",
        "instructions": "preserve instructions",
        "tools": [
            {
                "type": "function",
                "name": "shell",
                "title": "Shell",
                "description": verbose_description,
                "parameters": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": "ShellParameters",
                    "type": "object",
                    "properties": {
                        "cmd": {
                            "type": "string",
                            "description": verbose_description,
                            "examples": ["pwd"],
                        }
                    },
                    "required": ["cmd"],
                    "additionalProperties": False,
                },
            }
        ],
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "do not rewrite me"}],
            },
            {
                "type": "function_call",
                "name": "shell",
                "call_id": "call_1",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": long_output,
            },
        ],
        "metadata": {"request_id": "responses-cache-guard"},
    }
    original_tools = copy.deepcopy(data["tools"])
    before = responses_cache_hot_zone_fingerprint(data)

    def fake_compress(**kwargs):
        return SimpleNamespace(
            messages=[{"role": "tool", "content": "compressed tool output"}],
            tokens_before=1000,
            tokens_after=100,
            tokens_saved=900,
            compression_ratio=0.9,
            transforms_applied=["fake-transform"],
        )

    monkeypatch.setattr(callback, "compress", fake_compress)

    shim = module.HeadroomCallback()
    result = asyncio.run(shim.async_pre_call_hook(data=data, call_type="aresponses"))
    after = responses_cache_hot_zone_fingerprint(data)

    assert result is data
    assert data["tools"] == original_tools
    assert data["input"][2]["output"] == "compressed tool output"
    assert before["stable_prefix_hash"] == after["stable_prefix_hash"]
    assert (
        before["stable_top_level_field_hashes"]["tools"]
        == after["stable_top_level_field_hashes"]["tools"]
    )

    capture = shim._pending["responses-cache-guard"]
    assert "openai:responses:tool_output_units" in capture.transforms_applied
    assert "openai:responses:tool_schema_compaction" not in capture.transforms_applied
    assert capture.tokens_saved == 900


def test_responses_deployment_payload_diagnostic_is_content_free(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    module = load_shim_module()
    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    long_output = "verbose deployment tool output " * 80
    compressed_output = "compressed deployment output"
    data = {
        "model": "gpt-5.5",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": long_output,
            },
        ],
        "metadata": {"request_id": "responses-deployment-shape"},
    }

    def fake_compress(**kwargs):
        return SimpleNamespace(
            messages=[{"role": "tool", "content": compressed_output}],
            tokens_before=1000,
            tokens_after=100,
            tokens_saved=900,
            compression_ratio=0.9,
            transforms_applied=["fake-transform"],
        )

    monkeypatch.setattr(callback, "compress", fake_compress)

    shim = module.HeadroomCallback()
    asyncio.run(shim.async_pre_call_hook(data=data, call_type="aresponses"))
    asyncio.run(shim.async_pre_call_deployment_hook(data, "aresponses"))

    diagnostic = shim._deployment_payload_shapes["responses-deployment-shape"]
    mutable_output = diagnostic["mutable_output"]

    assert diagnostic["hook"] == "async_pre_call_deployment_hook"
    assert mutable_output["output_item_count"] == 1
    assert mutable_output["text_output_item_count"] == 1
    assert mutable_output["output_bytes"] == len(compressed_output)
    assert mutable_output["output_tokens_estimate"] > 0
    serialized = json.dumps(diagnostic)
    assert long_output not in serialized
    assert compressed_output not in serialized


def test_codex_proxy_prompt_cache_key_is_preserved_by_default() -> None:
    callback = load_shim_callback()()
    data = {
        "model": "gpt-5.5",
        "prompt_cache_key": "codex-generated-key",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-cache-key-default"},
        "proxy_server_request": {
            "headers": {
                "X-LiteLLM-Proxy-Client": "codex",
            }
        },
    }

    result = asyncio.run(
        callback.async_pre_call_hook(data=data, call_type="aresponses")
    )

    assert result is data
    assert data["prompt_cache_key"] == "codex-generated-key"
    assert "extra_body" not in data
    capture = callback._pending["responses-cache-key-default"]
    assert capture.skip_reason == "responses_string_input_protected"
    assert "openai:responses:prompt_cache_key_removed" not in capture.transforms_applied
    assert (
        "openai:responses:prompt_cache_key_passthrough"
        not in capture.transforms_applied
    )
    assert "prompt_cache_key" in capture.cache_hot_zone["stable_top_level_keys"]


def test_codex_prompt_cache_key_sets_stable_chatgpt_session_affinity() -> None:
    first_callback = load_shim_callback()()
    second_callback = load_shim_callback()()
    first = {
        "model": "gpt-5.5",
        "prompt_cache_key": "codex-generated-key",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-affinity-first"},
        "proxy_server_request": {
            "headers": {
                "X-LiteLLM-Proxy-Client": "codex",
            }
        },
    }
    second = copy.deepcopy(first)
    second["metadata"]["request_id"] = "responses-affinity-second"

    asyncio.run(first_callback.async_pre_call_hook(data=first, call_type="aresponses"))
    asyncio.run(
        second_callback.async_pre_call_hook(data=second, call_type="aresponses")
    )

    assert first["prompt_cache_key"] == "codex-generated-key"
    assert first["litellm_session_id"].startswith("codex-cache-")
    assert first["metadata"]["session_id"] == first["litellm_session_id"]
    assert first["litellm_session_id"] == second["litellm_session_id"]

    capture = first_callback._pending["responses-affinity-first"]
    assert "openai:responses:chatgpt_session_affinity" in capture.transforms_applied
    assert capture.request_metadata["provider_session_affinity_source"] == (
        "prompt_cache_key"
    )
    assert capture.request_metadata["provider_session_affinity_hash"] == first[
        "litellm_session_id"
    ].removeprefix("codex-cache-")
    assert "codex-generated-key" not in json.dumps(capture.request_metadata)
    assert "litellm_proxy_provider_session_affinity_hash" not in first["metadata"]


def test_codex_chatgpt_session_affinity_analytics_hash_is_model_scoped() -> None:
    callback = load_shim_callback()()
    first = {
        "model": "gpt-5.5",
        "prompt_cache_key": "codex-generated-key",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-affinity-gpt55"},
        "proxy_server_request": {
            "headers": {
                "X-LiteLLM-Proxy-Client": "codex",
            }
        },
    }
    second = copy.deepcopy(first)
    second["model"] = "gpt-5.4-mini"
    second["metadata"]["request_id"] = "responses-affinity-gpt54-mini"

    asyncio.run(callback.async_pre_call_hook(data=first, call_type="aresponses"))
    asyncio.run(callback.async_pre_call_hook(data=second, call_type="aresponses"))

    assert first["litellm_session_id"] != second["litellm_session_id"]
    assert first["litellm_session_id"].startswith("codex-cache-")
    assert second["litellm_session_id"].startswith("codex-cache-")


def test_codex_chatgpt_session_affinity_preserves_existing_session_id() -> None:
    callback = load_shim_callback()()
    data = {
        "model": "gpt-5.5",
        "prompt_cache_key": "codex-generated-key",
        "litellm_session_id": "caller-owned-session",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-existing-session"},
        "proxy_server_request": {
            "headers": {
                "X-LiteLLM-Proxy-Client": "codex",
            }
        },
    }

    asyncio.run(callback.async_pre_call_hook(data=data, call_type="aresponses"))

    assert data["litellm_session_id"] == "caller-owned-session"
    assert "session_id" not in data["metadata"]
    capture = callback._pending["responses-existing-session"]
    assert "openai:responses:chatgpt_session_affinity" not in (
        capture.transforms_applied
    )
    assert "provider_session_affinity_hash" not in capture.request_metadata


def test_codex_proxy_prompt_cache_key_can_be_removed_for_experiments(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HEADROOM_RESPONSES_DROP_CODEX_PROMPT_CACHE_KEY", "1")
    callback = load_shim_callback()()
    data = {
        "model": "gpt-5.5",
        "prompt_cache_key": "volatile-codex-generated-key",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-cache-key-removed"},
        "proxy_server_request": {
            "headers": {
                "X-LiteLLM-Proxy-Client": "codex",
            }
        },
    }

    result = asyncio.run(
        callback.async_pre_call_hook(data=data, call_type="aresponses")
    )

    assert result is data
    assert "prompt_cache_key" not in data
    assert "extra_body" not in data
    capture = callback._pending["responses-cache-key-removed"]
    assert capture.skip_reason == "responses_string_input_protected"
    assert "openai:responses:prompt_cache_key_removed" in capture.transforms_applied
    assert (
        "openai:responses:prompt_cache_key_passthrough"
        not in capture.transforms_applied
    )
    assert "prompt_cache_key" not in capture.cache_hot_zone["stable_top_level_keys"]


def test_non_codex_prompt_cache_key_is_preserved() -> None:
    callback = load_shim_callback()()
    data = {
        "model": "gpt-5.5",
        "prompt_cache_key": "caller-owned-stable-key",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-cache-key-preserved"},
    }

    result = asyncio.run(
        callback.async_pre_call_hook(data=data, call_type="aresponses")
    )

    assert result is data
    assert data["prompt_cache_key"] == "caller-owned-stable-key"
    assert "extra_body" not in data
    capture = callback._pending["responses-cache-key-preserved"]
    assert capture.skip_reason == "responses_string_input_protected"
    assert "openai:responses:prompt_cache_key_removed" not in capture.transforms_applied
    assert (
        "openai:responses:prompt_cache_key_passthrough"
        not in capture.transforms_applied
    )
    assert "prompt_cache_key" in capture.cache_hot_zone["stable_top_level_keys"]


def test_codex_responses_provider_passthrough_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("HEADROOM_RESPONSES_CHATGPT_PROVIDER_PASSTHROUGH", "1")
    callback = load_shim_callback()()
    data = {
        "model": "gpt-5.5",
        "prompt_cache_key": "codex-key",
        "client_metadata": {"x-codex-turn-metadata": "redacted"},
        "parallel_tool_calls": False,
        "service_tier": "default",
        "store": True,
        "stream": True,
        "text": {"verbosity": "medium"},
        "metadata": {"request_id": "responses-provider-passthrough"},
        "input": "live user text should stay intact",
    }

    result = asyncio.run(
        callback.async_pre_call_hook(data=data, call_type="aresponses")
    )

    assert result is data
    assert data["extra_body"] == {
        "client_metadata": {"x-codex-turn-metadata": "redacted"},
        "parallel_tool_calls": False,
        "prompt_cache_key": "codex-key",
        "service_tier": "default",
        "store": True,
        "stream": True,
        "text": {"verbosity": "medium"},
    }
    assert "metadata" not in data["extra_body"]
    capture = callback._pending["responses-provider-passthrough"]
    assert "openai:responses:chatgpt_provider_passthrough" in (
        capture.transforms_applied
    )
    assert "openai:responses:prompt_cache_key_passthrough" in capture.transforms_applied
    assert "client_metadata" in capture.cache_hot_zone["stable_top_level_keys"]
    assert "store" in capture.cache_hot_zone["stable_top_level_keys"]


def test_chatgpt_transform_can_skip_builtin_default_instruction_prefix(
    monkeypatch,
) -> None:
    from litellm.llms.chatgpt.responses.transformation import (
        ChatGPTResponsesAPIConfig,
    )

    monkeypatch.setenv("CHATGPT_DEFAULT_INSTRUCTIONS", " ")
    existing_instructions = "existing Codex request instructions"

    request = ChatGPTResponsesAPIConfig().transform_responses_api_request(
        model="gpt-5.5",
        input="live user text",
        response_api_optional_request_params={
            "instructions": existing_instructions,
            "tools": [],
        },
        litellm_params={},
        headers={},
    )

    assert request["instructions"] == existing_instructions


def test_responses_tool_schema_compaction_preserves_invocation_shape(
    monkeypatch,
) -> None:
    module = load_shim_module()
    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    verbose = " ".join(["Use this tool to read a file from the workspace."] * 20)
    data = {
        "model": "gpt-5.4-mini",
        "input": "live user text should stay intact",
        "tools": [
            {
                "type": "function",
                "name": "read_file",
                "title": "Read File",
                "description": f"  {verbose}\n\n",
                "parameters": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": "ReadFileParameters",
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": verbose,
                            "examples": ["src/main.py"],
                        },
                        "title": {
                            "type": "string",
                            "description": "A property named title must survive.",
                        },
                    },
                    "required": ["path", "title"],
                    "additionalProperties": False,
                },
            }
        ],
        "metadata": {"request_id": "responses-tool-schema"},
    }

    monkeypatch.setenv("HEADROOM_RESPONSES_TOOL_SCHEMA_COMPACTION", "1")
    monkeypatch.setattr(callback, "_count_text_tokens", lambda text, model: len(text))

    shim = module.HeadroomCallback()
    result = asyncio.run(shim.async_pre_call_hook(data=data, call_type="aresponses"))

    assert result is data
    assert data["input"] == "live user text should stay intact"
    tool = data["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "read_file"
    assert "title" not in tool
    assert tool["description"] == verbose

    params = tool["parameters"]
    assert "$schema" not in params
    assert "title" not in params
    assert params["type"] == "object"
    assert params["required"] == ["path", "title"]
    assert params["additionalProperties"] is False
    assert "examples" not in params["properties"]["path"]
    assert "title" in params["properties"]

    capture = shim._pending["responses-tool-schema"]
    assert capture.skip_reason is None
    assert capture.compression_status is None
    assert capture.tokens_saved is not None
    assert capture.tokens_saved > 0
    assert capture.tokens_after == capture.tokens_before - capture.tokens_saved
    assert "openai:responses:tool_schema_compaction" in capture.transforms_applied


def test_responses_partial_compression_counts_unmodified_units_as_original(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    module = load_shim_module()
    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    compressible_output = "compressible tool output " * 80
    unchanged_output = "already compact tool output " * 80
    data = {
        "model": "gpt-5.4-mini",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": compressible_output,
            },
            {
                "type": "function_call_output",
                "call_id": "call_2",
                "output": unchanged_output,
            },
        ],
        "metadata": {"request_id": "responses-partial-compress"},
    }

    def fake_compress(**kwargs):
        content = kwargs["messages"][0]["content"]
        if content == compressible_output:
            return SimpleNamespace(
                messages=[{"role": "tool", "content": "short tool output"}],
                tokens_before=1000,
                tokens_after=100,
                tokens_saved=900,
                compression_ratio=0.9,
                transforms_applied=["fake-transform"],
            )
        return SimpleNamespace(
            messages=[{"role": "tool", "content": unchanged_output}],
            tokens_before=200,
            tokens_after=50,
            tokens_saved=150,
            compression_ratio=0.75,
            transforms_applied=["fake-transform"],
        )

    monkeypatch.setattr(callback, "compress", fake_compress)

    shim = module.HeadroomCallback()
    result = asyncio.run(shim.async_pre_call_hook(data=data, call_type="aresponses"))

    assert result is data
    assert data["input"][0]["output"] == "short tool output"
    assert data["input"][1]["output"] == unchanged_output

    capture = shim._pending["responses-partial-compress"]
    assert capture.tokens_before == 1200
    assert capture.tokens_saved == 900
    assert capture.tokens_after == 300
    assert capture.tokens_after == capture.tokens_before - capture.tokens_saved


def test_responses_retrieval_tool_output_is_protected(monkeypatch) -> None:
    module = load_shim_module()
    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    retrieved_output = "retrieved original context " * 80
    data = {
        "model": "gpt-5.4-mini",
        "input": [
            {
                "type": "function_call",
                "name": "mcp__analytics__litellm_proxy_analytics_retrieve_chunk",
                "call_id": "call_retrieve",
                "arguments": '{"ccr_hash":"abc123"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_retrieve",
                "output": retrieved_output,
            },
        ],
        "metadata": {"request_id": "responses-retrieval-protected"},
    }

    def fail_compress(**kwargs):
        raise AssertionError(f"retrieval output should not be compressed: {kwargs}")

    monkeypatch.setattr(callback, "compress", fail_compress)

    shim = module.HeadroomCallback()
    result = asyncio.run(shim.async_pre_call_hook(data=data, call_type="aresponses"))

    assert result is data
    assert data["input"][1]["output"] == retrieved_output

    capture = shim._pending["responses-retrieval-protected"]
    assert capture.skip_reason == "responses_retrieval_output_protected"
    assert capture.compression_status == "skipped"
    assert capture.tokens_before is None
    assert capture.attempted_input_tokens == 0


def test_responses_string_input_records_protected_skip_reason() -> None:
    callback = load_shim_callback()()
    data = {
        "model": "gpt-5.4-mini",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-string"},
    }

    result = asyncio.run(
        callback.async_pre_call_hook(data=data, call_type="aresponses")
    )

    assert result is data
    assert data["input"] == "live user text should stay intact"
    capture = callback._pending["responses-string"]
    assert capture.skip_reason == "responses_string_input_protected"
    assert capture.compression_status == "skipped"
    assert capture.tokens_before is None
    assert capture.attempted_input_tokens == 0


def test_pre_call_request_key_is_synced_to_litellm_logging_metadata() -> None:
    from types import SimpleNamespace

    callback = load_shim_callback()()
    logging_obj = SimpleNamespace(
        litellm_params={"metadata": {}},
        model_call_details={"litellm_params": {"metadata": {}}},
    )
    data = {
        "model": "gpt-5.4-mini",
        "input": "live user text should stay intact",
        "metadata": {"request_id": "responses-logging-sync"},
        "litellm_logging_obj": logging_obj,
    }

    asyncio.run(callback.async_pre_call_hook(data=data, call_type="aresponses"))

    assert (
        logging_obj.litellm_params["metadata"]["litellm_proxy_analytics_request_key"]
        == "responses-logging-sync"
    )
    assert (
        logging_obj.model_call_details["litellm_params"]["metadata"][
            "litellm_proxy_analytics_request_key"
        ]
        == "responses-logging-sync"
    )


def test_success_handler_captures_responses_input_without_pre_call(monkeypatch) -> None:
    callback = load_shim_callback()(api_key=None)
    posted = {}

    async def fake_post_capture(capture, **kwargs):
        posted["capture"] = capture
        posted["kwargs"] = kwargs

    monkeypatch.setattr(callback, "_post_capture", fake_post_capture)

    asyncio.run(
        callback.async_success_handler(
            kwargs={
                "model": "gpt-5.4-mini",
                "input": "responses payload",
                "metadata": {"request_id": "responses-fallback"},
            },
            response={
                "id": "response-id",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            },
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )
    )

    capture = posted["capture"]
    assert capture.request_key == "responses-fallback"
    assert capture.model == "gpt-5.4-mini"
    assert capture.incoming_route == "/v1/responses"
    assert capture.compression_status == "skipped"
    assert capture.skip_reason == "compression_not_attempted_post_call_fallback"
    assert posted["kwargs"]["status"] == "succeeded"


def test_post_capture_bounds_long_responses_ids() -> None:
    callback = load_shim_callback()(api_key=None)
    submitted = {}

    class FakeBuffer:
        def submit_nowait(self, command):
            submitted["command"] = command
            return True

    callback._analytics_buffer = FakeBuffer()
    capture = callback._post_call_capture(
        {
            "model": "gpt-5.4-mini",
            "input": "responses payload",
            "metadata": {"request_id": "responses-long-id"},
        }
    )

    assert capture is not None

    asyncio.run(
        callback._post_capture(
            capture,
            response={
                "id": "resp_" + ("x" * 320),
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            },
            status="succeeded",
            duration_ms=10,
        )
    )

    command = submitted["command"]
    assert len(command.event.event_key) <= 255
    assert len(command.provider_calls[0].provider_call_key) <= 255
    assert len(command.provider_calls[0].provider_response_id) <= 255
    assert command.request.incoming_route == "/v1/responses"


def test_post_capture_persists_litellm_call_id_on_provider_call() -> None:
    callback = load_shim_callback()(api_key=None)
    submitted = {}

    class FakeBuffer:
        def submit_nowait(self, command):
            submitted["command"] = command
            return True

    callback._analytics_buffer = FakeBuffer()
    capture = callback._post_call_capture(
        {
            "model": "gpt-5.4-mini",
            "input": "responses payload",
            "metadata": {"request_id": "responses-call-id"},
            "litellm_call_id": "litellm-call-123",
        }
    )

    assert capture is not None

    asyncio.run(
        callback._post_capture(
            capture,
            response={
                "id": "resp_call_id",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            },
            status="succeeded",
            duration_ms=10,
        )
    )

    command = submitted["command"]
    assert command.provider_calls[0].litellm_call_id == "litellm-call-123"
    assert command.provider_calls[0].provider_response_id == "resp_call_id"


def test_post_capture_keeps_provider_success_on_skipped_compression() -> None:
    callback = load_shim_callback()(api_key=None)
    submitted = {}

    class FakeBuffer:
        def submit_nowait(self, command):
            submitted["command"] = command
            return True

    callback._analytics_buffer = FakeBuffer()
    capture = callback._post_call_capture(
        {
            "model": "gpt-5.4-mini",
            "input": "responses payload",
            "metadata": {"request_id": "responses-skipped-status"},
        }
    )

    asyncio.run(
        callback._post_capture(
            capture,
            response={
                "id": "resp_skipped",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            },
            status="succeeded",
            duration_ms=10,
        )
    )

    command = submitted["command"]
    assert command.execution.status == "skipped"
    assert command.execution.transforms["skip_reason"] == (
        "compression_not_attempted_post_call_fallback"
    )
    assert command.provider_calls[0].status == "succeeded"
    assert command.provider_calls[0].token_usage[0].input_tokens == 12


def test_responses_usage_mapping_reads_input_token_details_cache() -> None:
    from litellm_proxy_headroom.analytics.adapters.litellm.usage_mapping import (
        token_usage_from_response,
    )

    usage = token_usage_from_response(
        {
            "id": "resp_usage_details",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 12,
                "total_tokens": 112,
                "input_tokens_details": {"cached_tokens": 64},
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        }
    )

    assert usage is not None
    assert usage.measurement_source == "provider_reported"
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 64
    assert usage.newly_processed_input_tokens == 36
    assert usage.output_tokens == 12
    assert usage.reasoning_tokens == 5
    assert usage.total_tokens == 112
    assert usage.raw_usage["input_tokens_details"] == {"cached_tokens": 64}
    assert usage.raw_usage["output_tokens_details"] == {"reasoning_tokens": 5}


def test_post_capture_persists_proxy_run_marker_request_metadata() -> None:
    callback = load_shim_callback()(api_key=None)
    submitted = {}

    class FakeBuffer:
        def submit_nowait(self, command):
            submitted["command"] = command
            return True

    callback._analytics_buffer = FakeBuffer()
    capture = callback._post_call_capture(
        {
            "model": "gpt-5.4-mini",
            "input": "responses payload",
            "metadata": {"request_id": "responses-marker"},
            "proxy_server_request": {
                "headers": {
                    "X-LiteLLM-Proxy-Run": "AGENT90-MARKER",
                    "X-LiteLLM-Proxy-Project": "project%20name\n",
                    "X-LiteLLM-Proxy-Client": "codex",
                    "X-LiteLLM-Proxy-Ignored": "must-not-be-copied",
                    "Authorization": "Bearer secret-must-not-be-copied",
                }
            },
        }
    )

    asyncio.run(
        callback._post_capture(
            capture,
            response={
                "id": "resp_marker",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            },
            status="succeeded",
            duration_ms=10,
        )
    )

    command = submitted["command"]
    assert command.request.metadata == {
        "integration": "litellm-responses",
        "litellm_proxy_client": "codex",
        "litellm_proxy_project": "project name",
        "litellm_proxy_run_marker": "AGENT90-MARKER",
        "savings_profile": "agent-90",
    }
    assert "Authorization" not in command.request.metadata
    assert "X-LiteLLM-Proxy-Ignored" not in command.request.metadata
    assert "must-not-be-copied" not in json.dumps(command.request.metadata)
    assert "secret-must-not-be-copied" not in json.dumps(command.request.metadata)
