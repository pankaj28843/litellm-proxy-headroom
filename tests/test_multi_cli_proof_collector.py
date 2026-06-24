from __future__ import annotations

import json
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    "collect_multi_cli_proof",
    REPO_ROOT / "scripts/collect_multi_cli_proof.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
aggregate_provider_rows = MODULE.aggregate_provider_rows
build_proof_record = MODULE.build_proof_record


def test_aggregate_provider_rows_marks_absent_cache_and_unavailable_cost() -> None:
    rows = [
        {
            "request_key": "r1",
            "provider_call_id": "pc1",
            "client": "opencode",
            "incoming_route": "/v1/responses",
            "model": "gpt-5.5",
            "measurement_source": "provider_reported",
            "input_tokens": 16000,
            "cached_input_tokens": None,
            "output_tokens": 40,
            "reasoning_tokens": 10,
            "total_tokens": 16040,
            "cost_total": None,
        },
        {
            "request_key": "r2",
            "provider_call_id": "pc2",
            "client": "opencode",
            "incoming_route": "/v1/responses",
            "model": "gpt-5.4-mini",
            "measurement_source": "provider_reported",
            "input_tokens": "500",
            "cached_input_tokens": "",
            "output_tokens": "20",
            "reasoning_tokens": "5",
            "total_tokens": "520",
            "cost_total": "",
        },
    ]

    assert aggregate_provider_rows(rows) == {
        "request_count": 2,
        "provider_reported_call_count": 2,
        "models": ["gpt-5.4-mini", "gpt-5.5"],
        "clients": ["opencode"],
        "incoming_routes": ["/v1/responses"],
        "input_tokens": 16500,
        "cached_input_tokens": "absent",
        "cache_ratio": "unavailable",
        "output_tokens": 60,
        "reasoning_tokens": 15,
        "total_tokens": 16560,
        "cost_status": "unavailable",
        "cost_total": None,
    }


def test_aggregate_provider_rows_calculates_cache_ratio_and_cost() -> None:
    aggregate = aggregate_provider_rows(
        [
            {
                "request_key": "r1",
                "measurement_source": "provider_reported",
                "input_tokens": 1000,
                "cached_input_tokens": 250,
                "output_tokens": 100,
                "reasoning_tokens": 25,
                "total_tokens": 1100,
                "cost_total": "0.0123",
            }
        ]
    )

    assert aggregate["cached_input_tokens"] == 250
    assert aggregate["cache_ratio"] == "0.250000"
    assert aggregate["cost_status"] == "observed"
    assert aggregate["cost_total"] == "0.0123"


def test_build_proof_record_rejects_unknown_status() -> None:
    try:
        build_proof_record(
            cli="example",
            wrapper="bin/example",
            managed_home="~/.example-headroom",
            support_status="maybe",
            marker="marker",
            model_scope=[],
            artifact_dir="tmp/example",
            db_correlation="marker",
            rows=[],
            notes=[],
        )
    except ValueError as exc:
        assert "support_status" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_collector_cli_writes_normalized_proof_json(tmp_path: Path) -> None:
    rows_path = tmp_path / "rows.json"
    out_path = tmp_path / "proof.json"
    rows_path.write_text(
        json.dumps(
            [
                {
                    "request_key": "r1",
                    "client": "opencode",
                    "incoming_route": "/v1/responses",
                    "model": "gpt-5.5",
                    "measurement_source": "provider_reported",
                    "input_tokens": 16000,
                    "cached_input_tokens": None,
                    "output_tokens": 40,
                    "total_tokens": 16040,
                }
            ]
        )
    )

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/collect_multi_cli_proof.py"),
            "--cli",
            "opencode",
            "--wrapper",
            "bin/opencode-litellm",
            "--managed-home",
            "~/.opencode-headroom",
            "--support-status",
            "route_supported_cache_unproven",
            "--marker",
            "opencode-practical",
            "--model-scope",
            "practical:gpt-5.5",
            "--artifact-dir",
            "tmp/opencode",
            "--db-correlation",
            "marker",
            "--db-rows-json",
            str(rows_path),
            "--note",
            "cost unavailable",
            "--out",
            str(out_path),
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    proof = json.loads(out_path.read_text())
    assert proof["cli"] == "opencode"
    assert proof["support_status"] == "route_supported_cache_unproven"
    assert proof["client_attribution"] == ["opencode"]
    assert proof["aggregate"]["input_tokens"] == 16000
    assert proof["aggregate"]["cached_input_tokens"] == "absent"
    assert proof["aggregate"]["cost_status"] == "unavailable"
