import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import util
from pathlib import Path
from threading import Thread
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPO_ROOT / "scripts" / "e2e_agent90_usefulness.py"


def _load_harness():
    spec = util.spec_from_file_location("e2e_agent90_usefulness", HARNESS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _litellm_preflight_server(
    model_ids: list[str],
    *,
    callbacks: dict[str, list[str]] | None = None,
    status: int = 200,
) -> Iterator[tuple[str, dict[str, Any]]]:
    callback_payload = callbacks or {
        "success": [],
        "failure": [],
        "success_and_failure": ["HeadroomCallback", "arize_phoenix"],
    }
    seen: dict[str, Any] = {"paths": [], "authorization_by_path": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen["paths"].append(self.path)
            seen["authorization_by_path"][self.path] = self.headers.get("Authorization")
            if self.path == "/v1/models":
                body = json.dumps(
                    {"object": "list", "data": [{"id": model} for model in model_ids]}
                ).encode()
            elif self.path == "/callbacks/list":
                body = json.dumps(callback_payload).encode()
            else:
                self.send_response(404)
                self.end_headers()
                return

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_agent90_usefulness_harness_dry_run_contract(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--artifact-root",
            str(tmp_path),
            "--task-lines",
            "3",
        ],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    plan = json.loads(result.stdout)

    assert plan["mode"] == "dry-run"
    assert plan["marker"] == "AGENT90_TEST"
    assert plan["artifact_dir"] == str(tmp_path.resolve() / "AGENT90_TEST")
    assert not (tmp_path / "AGENT90_TEST").exists()

    direct = plan["lanes"]["direct"]
    proxy = plan["lanes"]["proxy"]
    assert proxy["command"][:11] == [
        str(REPO_ROOT / "bin" / "codex-litellm"),
        "-m",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="medium"',
        "-c",
        'model_verbosity="medium"',
        "-a",
        "never",
        "-s",
        "read-only",
    ]
    assert direct["command"][:13] == [
        "codex",
        "-m",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="medium"',
        "-c",
        'model_verbosity="medium"',
        "-a",
        "never",
        "-s",
        "read-only",
        "-C",
        str(REPO_ROOT),
    ]
    assert proxy["command"][11] == "-C"
    assert proxy["command"][12] == str(REPO_ROOT)
    assert direct["command"][13] == proxy["command"][13] == "exec"
    assert direct["command"][14] == proxy["command"][14] == "--json"
    assert direct["command"][15] == proxy["command"][15]
    assert "AGENT90_TEST" in direct["command"][15]
    assert "for i in range(3)" in direct["command"][15]
    assert plan["task"]["model"] == "gpt-5.5"
    assert plan["task"]["reasoning_effort"] == "medium"
    assert plan["task"]["model_verbosity"] == "medium"
    assert plan["task"]["expected_savings_profile"] == "agent-90"
    assert plan["preflight"]["enabled"] is True
    assert plan["preflight"]["litellm_url"] == "http://127.0.0.1:4000"
    assert plan["preflight"]["model_list_url"] == "http://127.0.0.1:4000/v1/models"
    assert plan["preflight"]["require_model_available"] is True
    assert plan["preflight"]["model"] == "gpt-5.5"
    assert plan["preflight"]["callback_list_url"] == (
        "http://127.0.0.1:4000/callbacks/list"
    )
    assert plan["preflight"]["require_callback_loaded"] is True
    assert plan["preflight"]["expected_callback"] == "HeadroomCallback"
    assert plan["preflight"]["analytics_url"] == "http://127.0.0.1:8010"
    assert plan["preflight"]["require_analytics_ready"] is False
    assert plan["preflight"]["artifacts"]["result"].endswith("preflight-result.json")

    assert plan["lanes"]["direct"]["artifacts"]["stdout"].endswith("direct/stdout.txt")
    assert plan["lanes"]["proxy"]["artifacts"]["result"].endswith("proxy/result.json")
    assert plan["lanes"]["proxy"]["artifacts"]["environment"].endswith(
        "proxy/environment.json"
    )
    assert plan["lanes"]["proxy"]["environment"] == {
        "CODEX_LITELLM_ANALYTICS_URL": "http://127.0.0.1:8010",
        "CODEX_LITELLM_BASE_URL": "http://127.0.0.1:4000/v1",
        "CODEX_LITELLM_CLIENT": "codex",
        "CODEX_LITELLM_MODEL": "gpt-5.5",
        "CODEX_LITELLM_MODEL_VERBOSITY": "medium",
        "CODEX_LITELLM_REASONING_EFFORT": "medium",
        "LITELLM_PROXY_RUN_MARKER": "AGENT90_TEST",
    }
    assert plan["lanes"]["proxy"]["artifacts"]["summary_lines"].endswith(
        "proxy/summary-lines.txt"
    )
    assert plan["lanes"]["direct"]["artifacts"]["token_summary"].endswith(
        "direct/token-summary.json"
    )
    assert "<marker>" in plan["proxy_db"]["query_template"]
    assert "'agent-90' as expected_strategy_name" in plan["proxy_db"]["query_template"]
    assert "litellm_proxy_run_marker" in plan["proxy_db"]["query_template"]
    assert "litellm_proxy_project" in plan["proxy_db"]["query_template"]
    assert "litellm_proxy_client" in plan["proxy_db"]["query_template"]
    assert "correlation_source" in plan["proxy_db"]["query_template"]
    assert "<proxy_started_at_utc>" in plan["proxy_db"]["query_template"]
    assert "<proxy_ended_at_utc>" in plan["proxy_db"]["query_template"]
    assert "interval '300 seconds'" in plan["proxy_db"]["query_template"]
    assert "cr.created_at <=" in plan["proxy_db"]["query_template"]
    assert "compression_config_snapshots" in plan["proxy_db"]["query_template"]
    assert "strategy_name" in plan["proxy_db"]["query_template"]
    assert "provider_call_key" in plan["proxy_db"]["query_template"]
    assert "litellm_call_id" in plan["proxy_db"]["query_template"]
    assert "provider_request_id" in plan["proxy_db"]["query_template"]
    assert "provider_response_id" in plan["proxy_db"]["query_template"]
    assert "measurement_source" in plan["proxy_db"]["query_template"]
    assert "newly_processed_input_tokens" in plan["proxy_db"]["query_template"]
    assert "cost_total" in plan["proxy_db"]["query_template"]
    assert "'aggregate' as proof_row_type" in plan["proxy_db"]["query_template"]
    assert "provider_reported_call_count" in plan["proxy_db"]["query_template"]
    assert "aggregate_cached_input_ratio" in plan["proxy_db"]["query_template"]
    assert "distinct_prompt_cache_key_hashes" in plan["proxy_db"]["query_template"]
    db_command = plan["proxy_db"]["manual_command"]
    assert db_command[db_command.index("-d") + 1] == "analytics"
    assert db_command[db_command.index("-f") + 1] == "-"
    assert plan["proxy_db"]["manual_command_stdin_file"].endswith("proxy/db-proof.sql")
    assert any("auth token" in rule for rule in plan["stop_rules"])
    assert any("read-only" in rule for rule in plan["stop_rules"])
    assert any("Preflight" in rule for rule in plan["stop_rules"])
    assert any("configured model" in rule for rule in plan["stop_rules"])
    assert any("reasoning effort" in rule for rule in plan["stop_rules"])
    assert any("model verbosity" in rule for rule in plan["stop_rules"])
    assert any("local Headroom callback" in rule for rule in plan["stop_rules"])
    assert any("LiteLLM base URL" in rule for rule in plan["stop_rules"])
    assert any("analytics MCP URL" in rule for rule in plan["stop_rules"])
    assert any(
        "expected Headroom strategy profile" in rule for rule in plan["stop_rules"]
    )


def test_agent90_usefulness_harness_threads_custom_litellm_url_to_proxy() -> None:
    harness = _load_harness()
    args = harness.parse_args(
        [
            "--marker",
            "AGENT90_TEST",
            "--task-lines",
            "3",
            "--litellm-url",
            "http://127.0.0.1:4100",
        ]
    )

    plan = harness.build_plan(args)

    assert plan["preflight"]["litellm_url"] == "http://127.0.0.1:4100"
    assert plan["preflight"]["model_list_url"] == "http://127.0.0.1:4100/v1/models"
    assert plan["preflight"]["callback_list_url"] == (
        "http://127.0.0.1:4100/callbacks/list"
    )
    assert plan["lanes"]["proxy"]["environment"]["CODEX_LITELLM_BASE_URL"] == (
        "http://127.0.0.1:4100/v1"
    )


def test_agent90_usefulness_harness_pins_custom_reasoning_effort() -> None:
    harness = _load_harness()
    args = harness.parse_args(
        [
            "--marker",
            "AGENT90_TEST",
            "--task-lines",
            "3",
            "--reasoning-effort",
            "high",
        ]
    )

    plan = harness.build_plan(args)

    assert plan["task"]["reasoning_effort"] == "high"
    assert 'model_reasoning_effort="high"' in plan["lanes"]["direct"]["command"]
    assert 'model_reasoning_effort="high"' in plan["lanes"]["proxy"]["command"]
    assert (
        plan["lanes"]["proxy"]["environment"]["CODEX_LITELLM_REASONING_EFFORT"]
        == "high"
    )


def test_agent90_usefulness_harness_pins_custom_model_verbosity() -> None:
    harness = _load_harness()
    args = harness.parse_args(
        [
            "--marker",
            "AGENT90_TEST",
            "--task-lines",
            "3",
            "--model-verbosity",
            "low",
        ]
    )

    plan = harness.build_plan(args)

    assert plan["task"]["model_verbosity"] == "low"
    assert 'model_verbosity="low"' in plan["lanes"]["direct"]["command"]
    assert 'model_verbosity="low"' in plan["lanes"]["proxy"]["command"]
    assert (
        plan["lanes"]["proxy"]["environment"]["CODEX_LITELLM_MODEL_VERBOSITY"] == "low"
    )


def test_agent90_usefulness_harness_preserves_litellm_v1_url_for_proxy() -> None:
    harness = _load_harness()
    args = harness.parse_args(
        [
            "--marker",
            "AGENT90_TEST",
            "--task-lines",
            "3",
            "--litellm-url",
            "http://127.0.0.1:4100/v1",
        ]
    )

    plan = harness.build_plan(args)

    assert plan["preflight"]["model_list_url"] == "http://127.0.0.1:4100/v1/models"
    assert plan["lanes"]["proxy"]["environment"]["CODEX_LITELLM_BASE_URL"] == (
        "http://127.0.0.1:4100/v1"
    )


def test_agent90_usefulness_harness_threads_custom_analytics_url_to_proxy() -> None:
    harness = _load_harness()
    args = harness.parse_args(
        [
            "--marker",
            "AGENT90_TEST",
            "--task-lines",
            "3",
            "--analytics-url",
            "http://127.0.0.1:8110",
            "--query-db",
            "--execute",
        ]
    )

    plan = harness.build_plan(args)

    assert plan["preflight"]["analytics_url"] == "http://127.0.0.1:8110"
    assert plan["preflight"]["require_analytics_ready"] is True
    assert plan["lanes"]["proxy"]["environment"]["CODEX_LITELLM_ANALYTICS_URL"] == (
        "http://127.0.0.1:8110"
    )


def test_agent90_usefulness_harness_rejects_litellm_url_credentials() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--litellm-url",
            "http://user:secret@127.0.0.1:4000",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "without credentials" in result.stderr
    assert "secret" not in result.stdout
    assert "secret" not in result.stderr


def test_agent90_usefulness_harness_rejects_analytics_url_query() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--analytics-url",
            "http://127.0.0.1:8010?token=secret",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "without credentials" in result.stderr
    assert "secret" not in result.stdout
    assert "secret" not in result.stderr


def test_agent90_usefulness_harness_rejects_shell_unsafe_marker() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "bad marker",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "marker may contain" in result.stderr


def test_agent90_usefulness_harness_rejects_shell_unsafe_model() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--model",
            'bad"model',
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "model may contain" in result.stderr


def test_agent90_usefulness_harness_rejects_unsupported_reasoning_effort() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--reasoning-effort",
            "extreme",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "reasoning effort must be one of" in result.stderr


def test_agent90_usefulness_harness_rejects_unsupported_model_verbosity() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--model-verbosity",
            "verbose",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "model verbosity must be one of" in result.stderr


def test_agent90_usefulness_harness_rejects_shell_unsafe_savings_profile() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--savings-profile",
            "bad profile",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "savings profile may contain" in result.stderr


def test_agent90_usefulness_harness_rejects_unknown_savings_profile() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_TEST",
            "--savings-profile",
            "unknown-profile",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "unknown savings profile" in result.stderr


def test_agent90_usefulness_query_db_plan_requires_analytics_ready() -> None:
    harness = _load_harness()

    args = harness.parse_args(
        ["--marker", "AGENT90_QUERY_DB", "--execute", "--query-db"]
    )
    plan = harness.build_plan(args)

    assert plan["preflight"]["enabled"] is True
    assert plan["preflight"]["require_analytics_ready"] is True
    assert plan["preflight"]["analytics_url"] == "http://127.0.0.1:8010"


def test_agent90_preflight_model_check_uses_master_key_without_persisting_value(
    monkeypatch,
) -> None:
    harness = _load_harness()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test-secret")

    with _litellm_preflight_server(["gpt-5.5"]) as (url, seen):
        args = harness.parse_args(
            [
                "--marker",
                "AGENT90_MODEL_AUTH",
                "--litellm-url",
                url,
                "--execute",
            ]
        )
        plan = harness.build_plan(args)
        preflight = harness._run_preflight(plan)

    assert preflight["ok"] is True
    assert seen["paths"] == ["/v1/models", "/callbacks/list"]
    assert seen["authorization_by_path"]["/v1/models"] == "Bearer sk-test-secret"
    assert seen["authorization_by_path"]["/callbacks/list"] == "Bearer sk-test-secret"
    model_check = next(
        check
        for check in preflight["checks"]
        if check["name"] == "litellm_model_available"
    )
    callback_check = next(
        check
        for check in preflight["checks"]
        if check["name"] == "litellm_callback_loaded"
    )
    assert model_check["ok"] is True
    assert model_check["matched_model"] == "gpt-5.5"
    assert model_check["model_count"] == 1
    assert model_check["auth_header_used"] is True
    assert model_check["auth_source"] == "LITELLM_MASTER_KEY"
    assert callback_check["ok"] is True
    assert callback_check["matched_callback"] == "HeadroomCallback"
    assert callback_check["callback_count"] == 2
    assert callback_check["auth_header_used"] is True
    assert callback_check["auth_source"] == "LITELLM_MASTER_KEY"
    assert "sk-test-secret" not in json.dumps(model_check)
    assert "sk-test-secret" not in json.dumps(callback_check)


def test_token_summary_parser_supports_codex_summary_variants() -> None:
    harness = _load_harness()

    summary = harness.parse_token_summary(
        {
            "stdout": (
                "Codex reported total=81.279 input=79.030 "
                "(+ 10.752 cached) output=2.249 (reasoning 1.277) cost=$0.042\n"
            ),
            "stderr": "",
        }
    )

    assert summary["complete"] is True
    assert summary["input_tokens"] == 79030
    assert summary["cached_input_tokens"] == 10752
    assert summary["output_tokens"] == 2249
    assert summary["reasoning_tokens"] == 1277
    assert summary["total_tokens"] == 81279
    assert summary["cost_usd"] == "0.042"
    assert summary["cost_complete"] is True
    assert summary["cost_source"] == {"stream": "stdout", "line_number": 1}
    assert summary["missing_fields"] == []
    assert summary["source_lines"][0]["fields"] == [
        "cached_input_tokens",
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    ]

    alternate = harness.parse_token_summary(
        {
            "stdout": "input tokens: 123\ncached_input_tokens=45\n",
            "stderr": "output tokens: 67\nreasoning tokens: 8\ntotal tokens: 198\n",
        }
    )

    assert alternate["complete"] is True
    assert alternate["input_tokens"] == 123
    assert alternate["cached_input_tokens"] == 45
    assert alternate["output_tokens"] == 67
    assert alternate["reasoning_tokens"] == 8
    assert alternate["total_tokens"] == 198
    assert alternate["cost_usd"] is None
    assert alternate["cost_complete"] is False


def test_token_summary_parser_supports_codex_json_usage_event() -> None:
    harness = _load_harness()

    summary = harness.parse_token_summary(
        {
            "stdout": (
                '{"type":"turn.completed","usage":{"input_tokens":15986,'
                '"cached_input_tokens":3456,"output_tokens":36,'
                '"reasoning_output_tokens":23}}\n'
            ),
            "stderr": "",
        }
    )

    assert summary["complete"] is True
    assert summary["input_tokens"] == 15986
    assert summary["cached_input_tokens"] == 3456
    assert summary["output_tokens"] == 36
    assert summary["reasoning_tokens"] == 23
    assert summary["total_tokens"] == 16022
    assert summary["usage_source"] == "codex_json_turn_completed_cumulative_latest"
    assert summary["json_turn_completed_count"] == 1


def test_token_summary_parser_uses_latest_cumulative_codex_json_usage_event() -> None:
    harness = _load_harness()

    summary = harness.parse_token_summary(
        {
            "stdout": (
                '{"type":"turn.completed","usage":{"input_tokens":1000,'
                '"cached_input_tokens":800,"output_tokens":50,'
                '"reasoning_output_tokens":10,"total_tokens":1050}}\n'
                '{"type":"turn.completed","usage":{"input_tokens":2000,'
                '"cached_input_tokens":400,"output_tokens":70,'
                '"reasoning_output_tokens":20,"total_tokens":2070}}\n'
            ),
            "stderr": "",
        }
    )

    assert summary["complete"] is True
    assert summary["usage_source"] == "codex_json_turn_completed_cumulative_latest"
    assert summary["json_turn_completed_count"] == 2
    assert summary["input_tokens"] == 2000
    assert summary["cached_input_tokens"] == 400
    assert summary["output_tokens"] == 70
    assert summary["reasoning_tokens"] == 20
    assert summary["total_tokens"] == 2070
    assert summary["field_sources"]["cached_input_tokens"]["event_count"] == 2
    assert summary["field_sources"]["cached_input_tokens"]["stream"] == (
        "latest_cumulative"
    )
    assert summary["field_sources"]["cached_input_tokens"]["latest_line_source"] == {
        "stream": "stdout",
        "line_number": 2,
    }
    assert summary["field_sources"]["cached_input_tokens"]["line_sources"] == [
        {"stream": "stdout", "line_number": 1},
        {"stream": "stdout", "line_number": 2},
    ]


def test_token_summary_comparison_marks_cost_missing_when_unreported() -> None:
    harness = _load_harness()

    comparison = harness._compare_token_summaries(
        [
            {
                "lane": "direct",
                "token_summary": harness.parse_token_summary(
                    {
                        "stdout": "input=10 cached=1 output=2 reasoning=0 total=12\n",
                        "stderr": "",
                    }
                ),
            },
            {
                "lane": "proxy",
                "token_summary": harness.parse_token_summary(
                    {
                        "stdout": (
                            "input=8 cached=2 output=2 reasoning=0 total=10 USD 0.001\n"
                        ),
                        "stderr": "",
                    }
                ),
            },
        ]
    )

    assert comparison["status"] == "complete"
    assert comparison["cost"] == {
        "status": "missing",
        "direct_usd": None,
        "proxy_usd": "0.001",
        "delta_proxy_minus_direct_usd": None,
        "missing_by_lane": {
            "direct": True,
            "proxy": False,
        },
    }
    assert comparison["mvp_usefulness"]["status"] == "incomplete"
    assert comparison["mvp_usefulness"]["fail_reasons"] == []
    assert comparison["mvp_usefulness"]["missing_reasons"] == ["cost_missing"]
    assert comparison["completion_contract"] == {
        "status": "pass",
        "scope": "provider_usage_cache",
        "cost_status": "unavailable",
        "fail_reasons": [],
        "missing_reasons": ["cost_missing"],
    }


def test_agent90_usefulness_execute_preflight_failure_skips_lanes(
    tmp_path: Path,
) -> None:
    direct_bin = tmp_path / "direct-codex"
    proxy_bin = tmp_path / "proxy-codex"
    direct_bin.write_text("#!/usr/bin/env python3\nraise SystemExit('direct ran')\n")
    proxy_bin.write_text("#!/usr/bin/env python3\nraise SystemExit('proxy ran')\n")
    direct_bin.chmod(0o755)
    proxy_bin.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_PREFLIGHT_FAIL",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--task-lines",
            "3",
            "--codex-bin",
            str(direct_bin),
            "--proxy-bin",
            str(proxy_bin),
            "--litellm-url",
            "http://127.0.0.1:9",
            "--preflight-timeout",
            "0.1",
            "--execute",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "preflight=failed" in result.stderr

    artifact_dir = tmp_path / "artifacts" / "AGENT90_PREFLIGHT_FAIL"
    preflight = json.loads((artifact_dir / "preflight-result.json").read_text())
    summary = json.loads((artifact_dir / "summary.json").read_text())

    assert preflight["ok"] is False
    assert preflight["checks"][0]["name"] == "litellm_tcp"
    assert summary["preflight_result"]["ok"] is False
    assert summary["results"] == []
    assert not (artifact_dir / "direct" / "stdout.txt").exists()
    assert not (artifact_dir / "proxy" / "stdout.txt").exists()


def test_agent90_usefulness_execute_model_preflight_failure_skips_lanes(
    tmp_path: Path,
) -> None:
    direct_bin = tmp_path / "direct-codex"
    proxy_bin = tmp_path / "proxy-codex"
    direct_bin.write_text("#!/usr/bin/env python3\nraise SystemExit('direct ran')\n")
    proxy_bin.write_text("#!/usr/bin/env python3\nraise SystemExit('proxy ran')\n")
    direct_bin.chmod(0o755)
    proxy_bin.chmod(0o755)

    with _litellm_preflight_server(["different-model"]) as (url, _seen):
        result = subprocess.run(
            [
                sys.executable,
                "scripts/e2e_agent90_usefulness.py",
                "--marker",
                "AGENT90_MODEL_PREFLIGHT_FAIL",
                "--artifact-root",
                str(tmp_path / "artifacts"),
                "--task-lines",
                "3",
                "--codex-bin",
                str(direct_bin),
                "--proxy-bin",
                str(proxy_bin),
                "--litellm-url",
                url,
                "--preflight-timeout",
                "1",
                "--execute",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )

    assert result.returncode == 1
    assert "preflight=failed" in result.stderr

    artifact_dir = tmp_path / "artifacts" / "AGENT90_MODEL_PREFLIGHT_FAIL"
    preflight = json.loads((artifact_dir / "preflight-result.json").read_text())
    summary = json.loads((artifact_dir / "summary.json").read_text())
    model_check = next(
        check
        for check in preflight["checks"]
        if check["name"] == "litellm_model_available"
    )

    assert preflight["ok"] is False
    assert model_check["ok"] is False
    assert model_check["model"] == "gpt-5.5"
    assert model_check["matched_model"] is None
    assert "not advertised" in model_check["error"]
    assert summary["results"] == []
    assert not (artifact_dir / "direct" / "stdout.txt").exists()
    assert not (artifact_dir / "proxy" / "stdout.txt").exists()


def test_agent90_usefulness_execute_callback_preflight_failure_skips_lanes(
    tmp_path: Path,
) -> None:
    direct_bin = tmp_path / "direct-codex"
    proxy_bin = tmp_path / "proxy-codex"
    direct_bin.write_text("#!/usr/bin/env python3\nraise SystemExit('direct ran')\n")
    proxy_bin.write_text("#!/usr/bin/env python3\nraise SystemExit('proxy ran')\n")
    direct_bin.chmod(0o755)
    proxy_bin.chmod(0o755)

    with _litellm_preflight_server(
        ["gpt-5.5"],
        callbacks={
            "success": [],
            "failure": [],
            "success_and_failure": ["arize_phoenix"],
        },
    ) as (url, _seen):
        result = subprocess.run(
            [
                sys.executable,
                "scripts/e2e_agent90_usefulness.py",
                "--marker",
                "AGENT90_CALLBACK_PREFLIGHT_FAIL",
                "--artifact-root",
                str(tmp_path / "artifacts"),
                "--task-lines",
                "3",
                "--codex-bin",
                str(direct_bin),
                "--proxy-bin",
                str(proxy_bin),
                "--litellm-url",
                url,
                "--preflight-timeout",
                "1",
                "--execute",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )

    assert result.returncode == 1
    assert "preflight=failed" in result.stderr

    artifact_dir = tmp_path / "artifacts" / "AGENT90_CALLBACK_PREFLIGHT_FAIL"
    preflight = json.loads((artifact_dir / "preflight-result.json").read_text())
    summary = json.loads((artifact_dir / "summary.json").read_text())
    callback_check = next(
        check
        for check in preflight["checks"]
        if check["name"] == "litellm_callback_loaded"
    )

    assert preflight["ok"] is False
    assert callback_check["ok"] is False
    assert callback_check["expected_callback"] == "HeadroomCallback"
    assert callback_check["matched_callback"] is None
    assert "not advertised" in callback_check["error"]
    assert summary["results"] == []
    assert not (artifact_dir / "direct" / "stdout.txt").exists()
    assert not (artifact_dir / "proxy" / "stdout.txt").exists()


def test_agent90_usefulness_execute_writes_token_summary_artifacts(
    tmp_path: Path,
) -> None:
    direct_bin = tmp_path / "direct-codex"
    proxy_bin = tmp_path / "proxy-codex"
    docker_bin = tmp_path / "docker"
    direct_bin.write_text(
        "#!/usr/bin/env python3\n"
        "print('Codex reported total=1,100 input=1,000 (+ 250 cached) "
        "output=100 (reasoning 40) cost=$0.002')\n"
    )
    proxy_bin.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('input_tokens=900 cached_input_tokens=300 output_tokens=90 "
        "reasoning_tokens=30 total_tokens=990 cost=USD 0.001', file=sys.stderr)\n"
    )
    docker_bin.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--csv' in sys.argv:\n"
        "    print('proof_row_type,request_key,strategy_name,measurement_source,"
        "input_tokens,cached_input_tokens,cost_total')\n"
        "    print('call,proxy-request,agent-90,provider_reported,900,300,0.001')\n"
        "else:\n"
        "    print('request_key strategy_name measurement_source input_tokens "
        "cached_input_tokens cost_total')\n"
        "    print('proxy-request agent-90 provider_reported 900 300 0.001')\n"
    )
    direct_bin.chmod(0o755)
    proxy_bin.chmod(0o755)
    docker_bin.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_FAKE",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--task-lines",
            "3",
            "--codex-bin",
            str(direct_bin),
            "--proxy-bin",
            str(proxy_bin),
            "--docker-bin",
            str(docker_bin),
            "--db-window-grace-seconds",
            "17",
            "--skip-preflight",
            "--execute",
            "--query-db",
        ],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert "agent90_usefulness=ok" in result.stdout

    artifact_dir = tmp_path / "artifacts" / "AGENT90_FAKE"
    direct_summary = json.loads(
        (artifact_dir / "direct" / "token-summary.json").read_text()
    )
    proxy_summary = json.loads(
        (artifact_dir / "proxy" / "token-summary.json").read_text()
    )
    summary = json.loads((artifact_dir / "summary.json").read_text())
    db_result = json.loads(
        (artifact_dir / "proxy" / "db-proof-result.json").read_text()
    )

    assert (
        (artifact_dir / "direct" / "summary-lines.txt")
        .read_text()
        .startswith("Codex reported")
    )
    assert direct_summary["complete"] is True
    assert direct_summary["input_tokens"] == 1000
    assert direct_summary["cached_input_tokens"] == 250
    assert direct_summary["cost_usd"] == "0.002"
    assert proxy_summary["complete"] is True
    assert proxy_summary["total_tokens"] == 990
    assert proxy_summary["cost_usd"] == "0.001"
    assert summary["token_comparison"]["status"] == "complete"
    assert summary["token_comparison"]["delta_proxy_minus_direct"] == {
        "cached_input_tokens": 50,
        "input_tokens": -100,
        "output_tokens": -10,
        "reasoning_tokens": -10,
        "total_tokens": -110,
    }
    assert summary["token_comparison"]["cost"] == {
        "status": "complete",
        "direct_usd": "0.002",
        "proxy_usd": "0.001",
        "delta_proxy_minus_direct_usd": "-0.001",
        "missing_by_lane": {
            "direct": False,
            "proxy": False,
        },
    }
    assert summary["token_comparison"]["derived"]["direct"] == {
        "newly_processed_input_tokens": 750,
        "cached_input_ratio": 0.25,
        "billing_equivalent_input_tokens": 775.0,
    }
    assert summary["token_comparison"]["derived"]["proxy"] == {
        "newly_processed_input_tokens": 600,
        "cached_input_ratio": 0.333333,
        "billing_equivalent_input_tokens": 630.0,
    }
    assert summary["token_comparison"]["mvp_usefulness"]["status"] == "pass"
    assert summary["token_comparison"]["mvp_usefulness"]["fail_reasons"] == []
    assert summary["token_comparison"]["mvp_usefulness"]["missing_reasons"] == []
    assert summary["token_comparison"]["completion_contract"] == {
        "status": "pass",
        "scope": "provider_usage_cache_cost",
        "cost_status": "observed",
        "fail_reasons": [],
        "missing_reasons": [],
    }
    assert json.loads((artifact_dir / "proxy" / "environment.json").read_text()) == {
        "CODEX_LITELLM_ANALYTICS_URL": "http://127.0.0.1:8010",
        "CODEX_LITELLM_BASE_URL": "http://127.0.0.1:4000/v1",
        "CODEX_LITELLM_CLIENT": "codex",
        "CODEX_LITELLM_MODEL": "gpt-5.5",
        "CODEX_LITELLM_MODEL_VERBOSITY": "medium",
        "CODEX_LITELLM_REASONING_EFFORT": "medium",
        "LITELLM_PROXY_RUN_MARKER": "AGENT90_FAKE",
    }
    assert (
        "compression_config_snapshots"
        in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "litellm_proxy_run_marker' = 'AGENT90_FAKE'"
        in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "'agent-90' as expected_strategy_name"
        in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "'aggregate' as proof_row_type"
        in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "aggregate_cached_input_ratio"
        in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "distinct_stable_input_prefix_hashes"
        in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "interval '17 seconds'" in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert "<marker>" not in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    assert (
        "<proxy_started_at_utc>"
        not in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "<proxy_ended_at_utc>"
        not in (artifact_dir / "proxy" / "db-proof.sql").read_text()
    )
    assert (
        "proxy-request agent-90 provider_reported 900 300 0.001"
        in (artifact_dir / "proxy" / "db-proof.stdout.txt").read_text()
    )
    assert db_result["returncode"] == 0
    assert db_result["stdin"].endswith("proxy/db-proof.sql")
    assert db_result["aggregate_result"]["returncode"] == 0
    assert db_result["aggregate_result"]["row_count"] == 1
    assert db_result["rows_result"]["returncode"] == 0
    assert db_result["rows_result"]["row_count"] == 1
    assert db_result["structured_artifacts"]["aggregate_csv"].endswith(
        "proxy/db-proof-aggregate.csv"
    )
    assert db_result["structured_artifacts"]["aggregate_json"].endswith(
        "proxy/db-proof-aggregate.json"
    )
    assert db_result["structured_artifacts"]["rows_csv"].endswith(
        "proxy/db-proof-rows.csv"
    )
    assert db_result["structured_artifacts"]["rows_json"].endswith(
        "proxy/db-proof-rows.json"
    )
    assert (
        json.loads((artifact_dir / "proxy" / "db-proof-aggregate.json").read_text())[
            "request_key"
        ]
        == "proxy-request"
    )
    assert (
        json.loads((artifact_dir / "proxy" / "db-proof-rows.json").read_text())[0][
            "measurement_source"
        ]
        == "provider_reported"
    )
    assert summary["proxy_db_result"]["returncode"] == 0
    assert summary["proxy_db_aggregate_query_file"].endswith(
        "proxy/db-proof-aggregate.sql"
    )
    assert summary["proxy_db_rows_query_file"].endswith("proxy/db-proof-rows.sql")


def test_agent90_usefulness_execute_passes_usage_cache_when_cost_unavailable(
    tmp_path: Path,
) -> None:
    direct_bin = tmp_path / "direct-codex"
    proxy_bin = tmp_path / "proxy-codex"
    direct_bin.write_text(
        "#!/usr/bin/env python3\n"
        "print('input=1000 cached=800 output=100 reasoning=40 total=1100')\n"
    )
    proxy_bin.write_text(
        "#!/usr/bin/env python3\n"
        "print('input=900 cached=720 output=90 reasoning=30 total=990')\n"
    )
    direct_bin.chmod(0o755)
    proxy_bin.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_COST_MISSING_OK",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--task-lines",
            "3",
            "--codex-bin",
            str(direct_bin),
            "--proxy-bin",
            str(proxy_bin),
            "--skip-preflight",
            "--execute",
        ],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    artifact_dir = tmp_path / "artifacts" / "AGENT90_COST_MISSING_OK"
    summary = json.loads((artifact_dir / "summary.json").read_text())

    assert "agent90_usefulness=ok" in result.stdout
    assert "scope=provider_usage_cache" in result.stdout
    assert "cost=unavailable" in result.stdout
    assert summary["token_comparison"]["cost"]["status"] == "missing"
    assert summary["token_comparison"]["mvp_usefulness"]["status"] == "incomplete"
    assert summary["token_comparison"]["completion_contract"] == {
        "status": "pass",
        "scope": "provider_usage_cache",
        "cost_status": "unavailable",
        "fail_reasons": [],
        "missing_reasons": ["cost_missing"],
    }


def test_agent90_usefulness_execute_fails_when_cache_accounting_regresses(
    tmp_path: Path,
) -> None:
    direct_bin = tmp_path / "direct-codex"
    proxy_bin = tmp_path / "proxy-codex"
    direct_bin.write_text(
        "#!/usr/bin/env python3\n"
        "print('input=100 cached=80 output=10 reasoning=0 total=110 cost=USD 0.001')\n"
    )
    proxy_bin.write_text(
        "#!/usr/bin/env python3\n"
        "print('input=100 cached=0 output=10 reasoning=0 total=110 cost=USD 0.001')\n"
    )
    direct_bin.chmod(0o755)
    proxy_bin.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_agent90_usefulness.py",
            "--marker",
            "AGENT90_CACHE_FAIL",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--task-lines",
            "3",
            "--codex-bin",
            str(direct_bin),
            "--proxy-bin",
            str(proxy_bin),
            "--skip-preflight",
            "--execute",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    artifact_dir = tmp_path / "artifacts" / "AGENT90_CACHE_FAIL"
    summary = json.loads((artifact_dir / "summary.json").read_text())

    assert result.returncode == 2
    assert "mvp_usefulness=fail" in result.stderr
    assert summary["token_comparison"]["mvp_usefulness"]["status"] == "fail"
    assert summary["token_comparison"]["mvp_usefulness"]["fail_reasons"] == [
        "proxy_billing_equivalent_input_worse",
        "proxy_cache_ratio_drop_too_large",
    ]
