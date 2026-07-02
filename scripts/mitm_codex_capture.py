from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "tmp" / "codex-mitm"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MODEL_VERBOSITY = "low"
DEFAULT_DIRECT_MODEL_PROVIDER = "openai"
DEFAULT_HTTP_CAPTURE_MODEL_PROVIDER = "openai-http-capture"
CHATGPT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
ADDON_PATH = REPO_ROOT / "scripts" / "mitmproxy_codex_full_capture.py"
SESSION_ID_PLACEHOLDER = "<session-id-from-turn-1>"
CAPTURE_PATH_ENV = "CODEX_MITM_CAPTURE_PATH"
CAPTURE_LANE_ENV = "CODEX_MITM_CAPTURE_LANE"
CAPTURE_MARKER_ENV = "CODEX_MITM_CAPTURE_MARKER"
PROXY_RUN_MARKER_ENV = "LITELLM_PROXY_RUN_MARKER"
CODEX_LITELLM_MODEL_ENV = "CODEX_LITELLM_MODEL"
CODEX_LITELLM_REASONING_EFFORT_ENV = "CODEX_LITELLM_REASONING_EFFORT"
CODEX_LITELLM_MODEL_VERBOSITY_ENV = "CODEX_LITELLM_MODEL_VERBOSITY"
CODEX_LITELLM_CLIENT_ENV = "CODEX_LITELLM_CLIENT"
CODEX_LITELLM_BASE_URL_ENV = "CODEX_LITELLM_BASE_URL"
CODEX_LITELLM_ANALYTICS_URL_ENV = "CODEX_LITELLM_ANALYTICS_URL"
CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH_ENV = (
    "CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH"
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _default_marker() -> str:
    return f"codex-mitm-{int(time.time())}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_file(path: Path, *, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            raise RuntimeError(f"mitmdump exited before creating {path}")
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {path}")


def _mitmdump_command(port: int, confdir: Path) -> list[str]:
    return [
        "uvx",
        "--from",
        "mitmproxy",
        "mitmdump",
        "-q",
        "--flow-detail",
        "0",
        "--listen-host",
        "127.0.0.1",
        "--listen-port",
        str(port),
        "--set",
        f"confdir={confdir}",
        "-s",
        str(ADDON_PATH),
    ]


def _prompts_from_args(args: argparse.Namespace) -> list[str]:
    if args.prompt_file:
        prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        delimiter = "\n---TURN---\n"
        if args.session_turns > 1 and delimiter in prompt:
            prompts = [part.strip() for part in prompt.split(delimiter)]
            prompts = [part + "\n" for part in prompts if part]
            if len(prompts) != args.session_turns:
                raise ValueError(
                    "--prompt-file turn delimiter count must match --session-turns"
                )
            return prompts
        return [prompt for _ in range(args.session_turns)]
    return [
        _turn_prompt(args, turn_index)
        for turn_index in range(1, args.session_turns + 1)
    ]


def _turn_prompt(args: argparse.Namespace, turn_index: int) -> str:
    if args.session_turns == 1:
        return args.prompt
    return (
        f"{args.prompt}\n\n"
        f"This is MITM capture turn {turn_index} of {args.session_turns}. "
        f"Reply with exactly: {args.marker}-turn-{turn_index:02d}"
    )


def _lane_command(
    args: argparse.Namespace,
    *,
    resume_session_id: str | None = None,
) -> list[str]:
    executable = args.codex_bin if args.lane == "direct" else args.proxy_bin
    command = [
        executable,
        "-m",
        args.model,
    ]
    if args.lane == "direct" and args.direct_model_provider:
        if args.disable_websockets_for_capture:
            provider_id = args.http_capture_model_provider
            command.extend(
                [
                    "-c",
                    f'model_provider="{provider_id}"',
                    "-c",
                    f'model_providers.{provider_id}.name="OpenAI HTTP capture"',
                    "-c",
                    f'model_providers.{provider_id}.base_url="{CHATGPT_CODEX_BASE_URL}"',
                    "-c",
                    f"model_providers.{provider_id}.requires_openai_auth=true",
                    "-c",
                    f"model_providers.{provider_id}.supports_websockets=false",
                ]
            )
        else:
            command.extend(["-c", f'model_provider="{args.direct_model_provider}"'])
    command.extend(
        [
            "-c",
            f'model_reasoning_effort="{args.reasoning_effort}"',
            "-c",
            f'model_verbosity="{args.model_verbosity}"',
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(Path(args.workdir).resolve()),
            "exec",
        ]
    )
    if resume_session_id is not None:
        command.extend(["resume", "--json", resume_session_id, "-"])
    else:
        command.extend(["--json", "-"])
    return command


def _child_proxy_environment(
    *,
    base_env: dict[str, str],
    port: int,
    ca_path: Path,
    bypass_localhost: bool,
) -> dict[str, str]:
    env = base_env.copy()
    proxy_url = f"http://127.0.0.1:{port}"
    env.update(
        {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "CODEX_CA_CERTIFICATE": str(ca_path),
        }
    )
    if bypass_localhost:
        existing = env.get("NO_PROXY") or env.get("no_proxy") or ""
        values = [value.strip() for value in existing.split(",") if value.strip()]
        for value in ("127.0.0.1", "localhost", "::1"):
            if value not in values:
                values.append(value)
        no_proxy = ",".join(values)
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy
    return env


def _lane_environment(args: argparse.Namespace) -> dict[str, str]:
    if args.lane != "proxy":
        return {}
    env = {
        CODEX_LITELLM_BASE_URL_ENV: args.litellm_url,
        CODEX_LITELLM_ANALYTICS_URL_ENV: args.analytics_url,
        PROXY_RUN_MARKER_ENV: args.marker,
        CODEX_LITELLM_CLIENT_ENV: "codex",
        CODEX_LITELLM_MODEL_ENV: args.model,
        CODEX_LITELLM_REASONING_EFFORT_ENV: args.reasoning_effort,
        CODEX_LITELLM_MODEL_VERBOSITY_ENV: args.model_verbosity,
    }
    if args.responses_provider_passthrough:
        env[CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH_ENV] = (
            args.responses_provider_passthrough
        )
    return env


def _capture_environment(env: dict[str, str]) -> dict[str, str]:
    keys = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
        "CODEX_CA_CERTIFICATE",
        PROXY_RUN_MARKER_ENV,
        CODEX_LITELLM_MODEL_ENV,
        CODEX_LITELLM_REASONING_EFFORT_ENV,
        CODEX_LITELLM_MODEL_VERBOSITY_ENV,
        CODEX_LITELLM_CLIENT_ENV,
        CODEX_LITELLM_BASE_URL_ENV,
        CODEX_LITELLM_ANALYTICS_URL_ENV,
        CODEX_LITELLM_RESPONSES_PROVIDER_PASSTHROUGH_ENV,
    )
    return {key: env[key] for key in keys if key in env}


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir = Path(args.artifact_root).resolve() / args.marker / args.lane
    prompts = _prompts_from_args(args)
    port = args.listen_port or _free_port()
    confdir = artifact_dir / "conf"
    ca_path = confdir / "mitmproxy-ca-cert.pem"
    capture_path = artifact_dir / "flows.jsonl"
    command = _lane_command(args)
    commands = [
        _lane_command(
            args,
            resume_session_id=SESSION_ID_PLACEHOLDER if index > 1 else None,
        )
        for index in range(1, len(prompts) + 1)
    ]
    return {
        "_prompts": prompts,
        "marker": args.marker,
        "lane": args.lane,
        "mode": "execute" if args.execute else "dry-run",
        "created_at": _utc_now(),
        "artifact_dir": str(artifact_dir),
        "workdir": str(Path(args.workdir).resolve()),
        "mitmproxy": {
            "command": _mitmdump_command(port, confdir),
            "listen_host": "127.0.0.1",
            "listen_port": port,
            "confdir": str(confdir),
            "ca_certificate": str(ca_path),
            "capture_path": str(capture_path),
            "stdout": str(artifact_dir / "mitmdump.stdout.txt"),
            "stderr": str(artifact_dir / "mitmdump.stderr.txt"),
        },
        "codex": {
            "command": command,
            "commands": commands,
            "environment": _lane_environment(args),
            "prompt_source": {
                "type": "file" if args.prompt_file else "literal",
                "path": str(Path(args.prompt_file).expanduser())
                if args.prompt_file
                else None,
                "turns": len(prompts),
                "bytes": sum(len(prompt.encode("utf-8")) for prompt in prompts),
                "sha256": _sha256_text("\n---TURN---\n".join(prompts)),
            },
            "turns": [
                {
                    "turn_index": index,
                    "command": commands[index - 1],
                    "prompt_source": {
                        "bytes": len(prompt.encode("utf-8")),
                        "sha256": _sha256_text(prompt),
                    },
                    "stdout": str(
                        artifact_dir / "turns" / f"{index:02d}" / "codex.stdout.jsonl"
                    ),
                    "stderr": str(
                        artifact_dir / "turns" / f"{index:02d}" / "codex.stderr.txt"
                    ),
                }
                for index, prompt in enumerate(prompts, start=1)
            ],
            "stdout": str(artifact_dir / "codex.stdout.jsonl"),
            "stderr": str(artifact_dir / "codex.stderr.txt"),
        },
        "safety": {
            "capture": "full_fidelity_local_jsonl_raw_headers_and_bodies",
            "bypass_localhost": args.bypass_localhost,
            "disable_websockets_for_capture": args.disable_websockets_for_capture,
            "raw_mitm_flow_dump": False,
            "auth_token_files_read": False,
        },
    }


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def _command_with_session_id(command: list[str], session_id: str | None) -> list[str]:
    if SESSION_ID_PLACEHOLDER not in command:
        return command
    if not session_id:
        raise RuntimeError("cannot resume Codex turn before thread.started is observed")
    return [session_id if arg == SESSION_ID_PLACEHOLDER else arg for arg in command]


def _latest_thread_id(stdout: str) -> str | None:
    latest: str | None = None
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        value = payload.get("thread_id")
        if payload.get("type") == "thread.started" and isinstance(value, str) and value:
            latest = value
    return latest


def execute_plan(plan: dict[str, Any], *, force: bool, timeout: int) -> int:
    artifact_dir = Path(plan["artifact_dir"])
    if artifact_dir.exists() and not force:
        print(f"codex_mitm_capture=failed artifact_dir_exists path={artifact_dir}", file=sys.stderr)
        return 1
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifact_dir / "plan.json", _public_plan(plan))

    mitm_config = plan["mitmproxy"]
    mitm_stdout_path = Path(mitm_config["stdout"])
    mitm_stderr_path = Path(mitm_config["stderr"])
    capture_path = Path(mitm_config["capture_path"])
    ca_path = Path(mitm_config["ca_certificate"])

    mitm_env = os.environ.copy()
    mitm_env.update(
        {
            CAPTURE_PATH_ENV: str(capture_path),
            CAPTURE_LANE_ENV: str(plan["lane"]),
            CAPTURE_MARKER_ENV: str(plan["marker"]),
        }
    )
    mitm_stdout = mitm_stdout_path.open("w", encoding="utf-8")
    mitm_stderr = mitm_stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        mitm_config["command"],
        cwd=Path(plan["workdir"]),
        env=mitm_env,
        text=True,
        stdout=mitm_stdout,
        stderr=mitm_stderr,
    )
    try:
        _wait_for_file(ca_path, process=process, timeout=20)
        base_env = os.environ.copy()
        base_env.update(plan["codex"].get("environment") or {})
        child_env = _child_proxy_environment(
            base_env=base_env,
            port=int(mitm_config["listen_port"]),
            ca_path=ca_path,
            bypass_localhost=bool(plan["safety"]["bypass_localhost"]),
        )
        _write_json(
            artifact_dir / "codex-capture-environment.json",
            _capture_environment(child_env),
        )
        started_at = _utc_now()
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        turn_results: list[dict[str, Any]] = []
        session_id: str | None = None
        returncode = 0
        prompts = plan.get("_prompts") or [""]
        turns = plan["codex"].get("turns") or []
        commands = plan["codex"].get("commands") or [plan["codex"]["command"]]
        for index, prompt in enumerate(prompts, start=1):
            turn_plan = turns[index - 1] if index - 1 < len(turns) else {}
            template = commands[index - 1] if index - 1 < len(commands) else commands[0]
            command = _command_with_session_id(list(template), session_id)
            turn_started_at = _utc_now()
            completed = subprocess.run(
                command,
                cwd=Path(plan["workdir"]),
                env=child_env,
                input=str(prompt),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            turn_ended_at = _utc_now()
            stdout_parts.append(completed.stdout)
            stderr_parts.append(completed.stderr)
            if session_id is None:
                session_id = _latest_thread_id(completed.stdout)
            stdout_path = Path(turn_plan.get("stdout") or plan["codex"]["stdout"])
            stderr_path = Path(turn_plan.get("stderr") or plan["codex"]["stderr"])
            _write_text(stdout_path, completed.stdout)
            _write_text(stderr_path, completed.stderr)
            turn_results.append(
                {
                    "turn_index": index,
                    "started_at": turn_started_at,
                    "ended_at": turn_ended_at,
                    "returncode": completed.returncode,
                    "command": command,
                    "stdout": str(stdout_path),
                    "stderr": str(stderr_path),
                    "session_id_after_turn": session_id,
                }
            )
            returncode = int(completed.returncode)
            if completed.returncode != 0:
                break
        ended_at = _utc_now()
        _write_text(Path(plan["codex"]["stdout"]), "".join(stdout_parts))
        _write_text(Path(plan["codex"]["stderr"]), "".join(stderr_parts))
        flow_count = 0
        if capture_path.exists():
            flow_count = sum(1 for line in capture_path.read_text().splitlines() if line.strip())
        result = {
            "marker": plan["marker"],
            "lane": plan["lane"],
            "started_at": started_at,
            "ended_at": ended_at,
            "returncode": returncode,
            "turn_count": len(turn_results),
            "turn_results": turn_results,
            "session_id": session_id,
            "codex_stdout": plan["codex"]["stdout"],
            "codex_stderr": plan["codex"]["stderr"],
            "flow_count": flow_count,
            "flows": str(capture_path),
            "mitmproxy_ca_certificate": str(ca_path),
        }
        _write_json(artifact_dir / "result.json", result)
        print(
            "codex_mitm_capture="
            f"{'ok' if returncode == 0 else 'failed'} "
            f"lane={plan['lane']} returncode={returncode} turns={len(turn_results)} "
            f"flows={flow_count} artifact_dir={artifact_dir}"
        )
        return returncode
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        mitm_stdout.close()
        mitm_stderr.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Codex through mitmproxy and save full-fidelity local request/response JSONL."
    )
    parser.add_argument("--marker", default=_default_marker())
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--lane", choices=("direct", "proxy"), default="direct")
    parser.add_argument("--workdir", default=str(REPO_ROOT))
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--proxy-bin", default=str(REPO_ROOT / "bin" / "codex-litellm"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--litellm-url", default="http://10.20.30.1:24040")
    parser.add_argument("--analytics-url", default="http://127.0.0.1:28010")
    parser.add_argument("--direct-model-provider", default=DEFAULT_DIRECT_MODEL_PROVIDER)
    parser.add_argument(
        "--http-capture-model-provider",
        default=DEFAULT_HTTP_CAPTURE_MODEL_PROVIDER,
        help="Temporary custom provider id used with --disable-websockets-for-capture.",
    )
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--model-verbosity", default=DEFAULT_MODEL_VERBOSITY)
    parser.add_argument(
        "--responses-provider-passthrough",
        choices=("on", "off"),
        help=(
            "Proxy-lane request-scoped experiment switch for preserving "
            "Responses provider passthrough fields through LiteLLM."
        ),
    )
    parser.add_argument(
        "--prompt",
        default="Do not edit files. Reply with exactly: CODEX_MITM_CAPTURE_OK",
    )
    parser.add_argument("--prompt-file")
    parser.add_argument(
        "--session-turns",
        type=int,
        default=1,
        help="Number of resumed Codex exec turns to run while mitmdump stays active.",
    )
    parser.add_argument("--listen-port", type=int)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--bypass-localhost",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep localhost traffic out of mitmproxy while capturing outbound HTTPS.",
    )
    parser.add_argument(
        "--disable-websockets-for-capture",
        action="store_true",
        help=(
            "For direct Codex, override the selected provider to use HTTP Responses "
            "so mitmproxy can capture the model request shape. This is diagnostic "
            "and not the default yolo transport."
        ),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.prompt_file:
        prompt_path = Path(args.prompt_file).expanduser()
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            parser.error(f"--prompt-file cannot be read: {exc}")
        if not prompt_text.strip():
            parser.error("--prompt-file must contain nonblank text")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.session_turns < 1:
        parser.error("--session-turns must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    plan = build_plan(args)
    if not args.execute:
        print(json.dumps(_public_plan(plan), indent=2, sort_keys=True))
        return 0
    return execute_plan(plan, force=args.force, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
