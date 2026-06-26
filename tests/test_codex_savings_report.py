import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_codex_savings_report_marks_provider_negative_as_unproven(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    direct_mitm = tmp_path / "direct-mitm"
    proxy_mitm = tmp_path / "proxy-mitm"
    outbound_mitm = tmp_path / "outbound-mitm"
    proxy_db_rows = tmp_path / "proxy-db-rows.json"
    out_dir = tmp_path / "report"
    summary_path.write_text(
        json.dumps(
            {
                "marker": "REPORT_TEST",
                "lane_order": ["proxy", "direct"],
                "minimum_input_token_floor": {
                    "ok": True,
                    "combined_input_tokens": 1_200_000,
                },
                "overall_usefulness": {"status": "pass", "scope": "account_capacity"},
                "account_comparison": {
                    "status": "observed",
                    "usefulness": "pass",
                    "reason": "proxy_not_worse",
                    "lanes": {
                        "direct": {
                            "delta": {
                                "primary_used_percent": 1,
                                "weekly_used_percent": 0,
                                "credits_balance": 0,
                                "reset_credits_available": 0,
                                "latest_daily_bucket_tokens": 0,
                            }
                        },
                        "proxy": {
                            "delta": {
                                "primary_used_percent": 0,
                                "weekly_used_percent": 0,
                                "credits_balance": 0,
                                "reset_credits_available": 0,
                                "latest_daily_bucket_tokens": 0,
                            }
                        },
                    },
                },
                "token_comparison": {
                    "completion_contract": {
                        "status": "fail",
                        "fail_reasons": ["proxy_total_tokens_worse"],
                        "missing_reasons": ["cost_missing"],
                    },
                    "mvp_usefulness": {
                        "checks": {"total_tokens_not_worse": {"ok": False}}
                    },
                },
                "results": [
                    {
                        "lane": "proxy",
                        "returncode": 0,
                        "turn_count": 12,
                        "session_id": "proxy-thread",
                        "token_summary": {
                            "usage_source": "latest",
                            "input_tokens": 200,
                            "cached_input_tokens": 150,
                            "output_tokens": 2,
                            "reasoning_tokens": 0,
                            "total_tokens": 202,
                            "cost_usd": None,
                        },
                    },
                    {
                        "lane": "direct",
                        "returncode": 0,
                        "turn_count": 12,
                        "session_id": "direct-thread",
                        "token_summary": {
                            "usage_source": "latest",
                            "input_tokens": 100,
                            "cached_input_tokens": 80,
                            "output_tokens": 2,
                            "reasoning_tokens": 0,
                            "total_tokens": 102,
                            "cost_usd": None,
                        },
                    },
                ],
            }
        )
    )
    _write_mitm(
        direct_mitm,
        lane="direct",
        host="chatgpt.com",
        request_path="/backend",
    )
    _write_mitm(
        proxy_mitm,
        lane="proxy",
        host="127.0.0.1",
        request_path="/v1/responses",
    )
    _write_outbound_mitm(outbound_mitm)
    _write_proxy_db_rows(proxy_db_rows)

    subprocess.run(
        [
            sys.executable,
            "scripts/report_codex_savings_proof.py",
            "--summary",
            str(summary_path),
            "--direct-mitm-dir",
            str(direct_mitm),
            "--proxy-mitm-dir",
            str(proxy_mitm),
            "--litellm-outbound-mitm-dir",
            str(outbound_mitm),
            "--proxy-db-rows",
            str(proxy_db_rows),
            "--out-dir",
            str(out_dir),
        ],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    report = json.loads((out_dir / "report.json").read_text())
    markdown = (out_dir / "report.md").read_text()

    assert report["verdict"]["shareability_status"] == "pass"
    assert report["verdict"]["real_savings_status"] == "unproven_provider_negative"
    assert report["mitm"]["proxy_full_fidelity"]["model_requests"][0]["host"] == (
        "127.0.0.1"
    )
    assert report["mitm"]["litellm_outbound_provider"]["flow_count"] == 1
    assert report["mitm"]["litellm_outbound_provider"]["model_requests"][0][
        "host"
    ] == "chatgpt.com"
    assert "| proxy | 0 | 0 | 0 | 0 | 0 |" in markdown
    assert "proxy_total_tokens_worse" in markdown
    assert report["proxy_provider_row_diagnostics"]["summary"]["row_count"] == 2
    assert report["proxy_provider_row_diagnostics"]["summary"]["distinct_counts"][
        "stable_top_level_field_hashes"
    ]["client_metadata"] == 2
    assert "Only client_metadata varied" in markdown


def test_codex_savings_report_keeps_coarse_account_deltas_unavailable(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "summary.json"
    out_dir = tmp_path / "report"
    summary_path.write_text(
        json.dumps(
            {
                "marker": "REPORT_PROVIDER_POSITIVE_ACCOUNT_COARSE",
                "lane_order": ["proxy", "direct"],
                "minimum_input_token_floor": {
                    "ok": True,
                    "combined_input_tokens": 1_300_000,
                },
                "overall_usefulness": {
                    "status": "pass",
                    "scope": "provider_usage_cache",
                },
                "account_comparison": {
                    "status": "observed",
                    "usefulness": "unavailable",
                    "reason": "snapshot_deltas_too_coarse",
                    "lanes": {
                        "direct": {
                            "delta": {
                                "primary_used_percent": 0,
                                "weekly_used_percent": 0,
                                "credits_balance": 0,
                                "reset_credits_available": 0,
                                "latest_daily_bucket_tokens": 0,
                            }
                        },
                        "proxy": {
                            "delta": {
                                "primary_used_percent": 0,
                                "weekly_used_percent": 0,
                                "credits_balance": 0,
                                "reset_credits_available": 0,
                                "latest_daily_bucket_tokens": 0,
                            }
                        },
                    },
                },
                "token_comparison": {
                    "completion_contract": {
                        "status": "pass",
                        "scope": "provider_usage_cache",
                        "fail_reasons": [],
                        "missing_reasons": ["cost_missing"],
                    },
                    "mvp_usefulness": {
                        "checks": {
                            "total_tokens_not_worse": {"ok": True},
                            "billing_equivalent_input_not_worse": {"ok": True},
                        }
                    },
                },
                "results": [
                    {
                        "lane": "proxy",
                        "returncode": 0,
                        "turn_count": 12,
                        "session_id": "proxy-thread",
                        "token_summary": {
                            "usage_source": "latest",
                            "input_tokens": 626_174,
                            "cached_input_tokens": 590_848,
                            "output_tokens": 1_836,
                            "reasoning_tokens": 0,
                            "total_tokens": 628_010,
                            "cost_usd": None,
                        },
                    },
                    {
                        "lane": "direct",
                        "returncode": 0,
                        "turn_count": 12,
                        "session_id": "direct-thread",
                        "token_summary": {
                            "usage_source": "latest",
                            "input_tokens": 720_465,
                            "cached_input_tokens": 639_488,
                            "output_tokens": 2_028,
                            "reasoning_tokens": 0,
                            "total_tokens": 722_493,
                            "cost_usd": None,
                        },
                    },
                ],
            }
        )
    )

    subprocess.run(
        [
            sys.executable,
            "scripts/report_codex_savings_proof.py",
            "--summary",
            str(summary_path),
            "--out-dir",
            str(out_dir),
        ],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    report = json.loads((out_dir / "report.json").read_text())
    markdown = (out_dir / "report.md").read_text()

    assert report["verdict"]["shareability_status"] == "unavailable"
    assert report["verdict"]["real_savings_status"] == "proven"
    assert report["verdict"]["account_status"] == "unavailable"
    assert report["verdict"]["provider_diagnostic_status"] == "pass"
    assert "account-capacity shareability is unavailable" in markdown
    assert "snapshot_deltas_too_coarse" in markdown


def _write_mitm(
    dir_path: Path,
    *,
    lane: str,
    host: str,
    request_path: str,
) -> None:
    dir_path.mkdir(parents=True)
    (dir_path / "plan.json").write_text(
        json.dumps(
            {
                "lane": lane,
                "safety": {
                    "disable_websockets_for_capture": lane == "direct",
                    "bypass_localhost": lane == "direct",
                },
            }
        )
    )
    (dir_path / "result.json").write_text(
        json.dumps({"returncode": 0, "flow_count": 1})
    )
    (dir_path / "flows.jsonl").write_text(
        json.dumps(
            {
                "event": "request",
                "request": {
                    "host": host,
                    "path": request_path,
                    "body": {
                        "bytes": 123,
                        "json": {
                            "model": "gpt-5.5",
                            "input": [1, 2, 3],
                            "tools": [1],
                            "prompt_cache_key": "cache",
                            "client_metadata": {},
                        },
                    },
                },
            }
        )
        + "\n"
    )


def _write_outbound_mitm(dir_path: Path) -> None:
    dir_path.mkdir(parents=True)
    (dir_path / "flows.jsonl").write_text(
        json.dumps(
            {
                "event": "request",
                "request": {
                    "host": "chatgpt.com",
                    "path": "/backend-api/codex/responses",
                    "body": {
                        "bytes": 123,
                        "json": {
                            "model": "gpt-5.5",
                            "input": [1, 2, 3],
                            "tools": [1],
                            "prompt_cache_key": "cache",
                            "client_metadata": {},
                        },
                    },
                },
            }
        )
        + "\n"
    )


def _write_proxy_db_rows(path: Path) -> None:
    rows = []
    for index in range(2):
        rows.append(
            {
                "created_at": f"2026-06-26 12:00:0{index}+00",
                "execution_status": "skipped",
                "input_tokens": str(100 + index),
                "cached_input_tokens": "80",
                "newly_processed_input_tokens": str(20 + index),
                "output_tokens": "2",
                "total_tokens": str(102 + index),
                "transforms": json.dumps(
                    {
                        "applied": [
                            "openai:responses:chatgpt_session_affinity",
                            "openai:responses:prompt_cache_key_passthrough",
                        ],
                        "skip_reason": (
                            "responses_mutable_output_compression_disabled_"
                            "no_positive_provider_proof"
                        ),
                        "cache_hot_zone": {
                            "stable_input_prefix_hash": "prefix",
                            "stable_top_level_hash": f"top-{index}",
                            "stable_prefix_without_prompt_cache_key_hash": (
                                f"without-cache-{index}"
                            ),
                            "stable_input_item_count": 5,
                            "stable_prefix_bytes": 1234,
                            "input_item_count": 7 + index,
                            "mutable_boundary": {
                                "input_index": 5,
                                "item_type": "function_call_output",
                            },
                            "stable_top_level_field_hashes": {
                                "client_metadata": f"metadata-{index}",
                                "model": "model",
                                "prompt_cache_key": "cache-key",
                                "tools": "tools",
                            },
                        },
                        "deployment_payload": {
                            "cache_hot_zone": {
                                "stable_input_prefix_hash": "prefix",
                                "stable_top_level_hash": f"top-{index}",
                                "stable_prefix_without_prompt_cache_key_hash": (
                                    f"without-cache-{index}"
                                ),
                                "stable_input_item_count": 5,
                                "stable_prefix_bytes": 1234,
                                "input_item_count": 7 + index,
                                "mutable_boundary": {
                                    "input_index": 5,
                                    "item_type": "function_call_output",
                                },
                                "stable_top_level_field_hashes": {
                                    "client_metadata": f"metadata-{index}",
                                    "model": "model",
                                    "prompt_cache_key": "cache-key",
                                    "tools": "tools",
                                },
                            },
                            "mutable_output": {
                                "output_item_count": index,
                                "output_tokens_estimate": index * 100,
                            },
                        },
                    }
                ),
            }
        )
    path.write_text(json.dumps(rows))
