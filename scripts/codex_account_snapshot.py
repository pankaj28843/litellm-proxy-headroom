from __future__ import annotations

import argparse
import json
import selectors
import subprocess
import time
from datetime import UTC, datetime
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a first-party Codex account quota/usage snapshot through "
            "codex app-server JSON-RPC."
        )
    )
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--include-daily-buckets",
        action="store_true",
        help="Include every account usage daily bucket instead of only the latest.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def send_json(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("codex app-server stdin is closed")
    proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def collect_app_server_responses(
    codex_bin: str,
    timeout_seconds: float,
) -> tuple[dict[int, dict[str, Any]], list[str]]:
    proc = subprocess.Popen(
        [codex_bin, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    selector = selectors.DefaultSelector()
    if proc.stdout is None or proc.stderr is None:
        raise RuntimeError("failed to open codex app-server stdio pipes")
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")

    responses: dict[int, dict[str, Any]] = {}
    stderr_lines: list[str] = []
    sent_queries = False
    deadline = time.monotonic() + timeout_seconds

    try:
        send_json(
            proc,
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "litellm_proxy_headroom_quota_probe",
                        "title": "LiteLLM Proxy Headroom Quota Probe",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            },
        )

        while time.monotonic() < deadline and {0, 1, 2} - set(responses):
            for key, _ in selector.select(timeout=0.25):
                line = key.fileobj.readline()
                if not line:
                    continue
                if key.data == "stderr":
                    if len(stderr_lines) < 20:
                        stderr_lines.append(line.rstrip())
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message.get("id"), int):
                    responses[message["id"]] = message
                if message.get("id") == 0 and not sent_queries:
                    send_json(proc, {"method": "initialized"})
                    send_json(proc, {"method": "account/rateLimits/read", "id": 1})
                    send_json(proc, {"method": "account/usage/read", "id": 2})
                    sent_queries = True
            if proc.poll() is not None:
                break
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        selector.close()

    return responses, stderr_lines


def codex_version(codex_bin: str) -> str | None:
    try:
        completed = subprocess.run(
            [codex_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def response_result(
    responses: dict[int, dict[str, Any]], response_id: int
) -> dict[str, Any] | None:
    response = responses.get(response_id)
    if not response or "error" in response:
        return None
    result = response.get("result")
    return result if isinstance(result, dict) else None


def build_snapshot(
    responses: dict[int, dict[str, Any]],
    stderr_lines: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    rate_limits = response_result(responses, 1)
    usage = response_result(responses, 2)
    daily_buckets = []
    if usage and isinstance(usage.get("dailyUsageBuckets"), list):
        daily_buckets = usage["dailyUsageBuckets"]

    compact_usage: dict[str, Any] | None = None
    if usage:
        compact_usage = {
            "summary": usage.get("summary"),
            "daily_bucket_count": len(daily_buckets),
            "latest_daily_bucket": daily_buckets[-1] if daily_buckets else None,
        }
        if args.include_daily_buckets:
            compact_usage["dailyUsageBuckets"] = daily_buckets

    errors = {
        str(response_id): response.get("error")
        for response_id, response in responses.items()
        if isinstance(response, dict) and response.get("error")
    }
    missing = [response_id for response_id in (1, 2) if response_id not in responses]

    status = "observed" if rate_limits and usage else "unavailable"
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "codex_version": codex_version(args.codex_bin),
        "account_snapshot_status": status,
        "rate_limits": rate_limits,
        "usage": compact_usage,
        "errors": errors,
        "missing_response_ids": missing,
        "stderr_line_count": len(stderr_lines),
    }


def main() -> int:
    args = parse_args()
    responses, stderr_lines = collect_app_server_responses(
        args.codex_bin, args.timeout_seconds
    )
    snapshot = build_snapshot(responses, stderr_lines, args)
    indent = 2 if args.pretty else None
    print(json.dumps(snapshot, indent=indent, sort_keys=True))
    return 0 if snapshot["account_snapshot_status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
