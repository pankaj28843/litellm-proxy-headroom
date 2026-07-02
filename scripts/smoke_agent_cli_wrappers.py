#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from shlex import join as shell_join
from shlex import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LITELLM_URL = "http://10.20.30.1:24040"
DEFAULT_REMOTE_HOSTS = "pankaj@10.20.30.102 neeraj@10.20.30.131"
DEFAULT_MODEL = "gpt-5.4-mini"
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]+"),
    re.compile(r"(LITELLM_MASTER_KEY=)[^\s]+"),
)


@dataclass(frozen=True)
class WrapperSpec:
    name: str
    prefix: str


WRAPPERS = (
    WrapperSpec("codex", "CODEX"),
    WrapperSpec("claude", "CLAUDE"),
    WrapperSpec("opencode", "OPENCODE"),
    WrapperSpec("copilot", "COPILOT"),
    WrapperSpec("pi", "PI"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real non-interactive smoke prompts through each LiteLLM wrapper."
    )
    parser.add_argument(
        "--target",
        action="append",
        choices=("local",),
        default=[],
        help="target to smoke; defaults to local when no remote target is provided",
    )
    parser.add_argument(
        "--remote",
        action="append",
        default=[],
        metavar="USER@HOST",
        help="remote SSH target to smoke; may be passed more than once",
    )
    parser.add_argument(
        "--remote-hosts",
        default="",
        help=(
            "space-separated SSH targets to smoke; use "
            f"WRAPPER_REMOTE_HOSTS or '{DEFAULT_REMOTE_HOSTS}' from the Makefile"
        ),
    )
    parser.add_argument(
        "--wrapper",
        action="append",
        choices=tuple(spec.name for spec in WRAPPERS),
        help="limit smoke to one wrapper; may be passed more than once",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("WRAPPER_SMOKE_MODEL", DEFAULT_MODEL),
        help=f"model for smoke calls; default {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--litellm-url",
        default=os.environ.get("WRAPPER_LITELLM_URL", DEFAULT_LITELLM_URL),
        help=f"LiteLLM base URL without /v1 for local wrappers; default {DEFAULT_LITELLM_URL}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("WRAPPER_SMOKE_TIMEOUT", "300")),
        help="per-wrapper timeout in seconds; default 300",
    )
    parser.add_argument(
        "--marker-prefix",
        default=f"litellm-wrapper-smoke-{int(time.time())}",
        help="marker prefix expected in wrapper responses",
    )
    return parser.parse_args()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def selected_wrappers(names: list[str] | None) -> tuple[WrapperSpec, ...]:
    if not names:
        return WRAPPERS
    requested = set(names)
    return tuple(spec for spec in WRAPPERS if spec.name in requested)


def command_for(spec: WrapperSpec, executable: str, marker: str) -> list[str]:
    prompt = f"Reply exactly: {marker}"
    if spec.name == "codex":
        return [executable, "exec", "--skip-git-repo-check", "--json", prompt]
    if spec.name == "claude":
        return [
            executable,
            "--print",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--max-budget-usd",
            "0.25",
            prompt,
        ]
    if spec.name == "opencode":
        return [executable, "run", "--format", "json", prompt]
    if spec.name == "copilot":
        return [
            executable,
            "-s",
            "--no-custom-instructions",
            "--stream",
            "off",
            "-p",
            prompt,
        ]
    if spec.name == "pi":
        return [
            executable,
            "--mode",
            "json",
            "--no-tools",
            "--thinking",
            "low",
            "-p",
            prompt,
        ]
    raise ValueError(spec.name)


def common_env(*, model: str, litellm_url: str, marker: str) -> dict[str, str]:
    values = {
        "LITELLM_PROXY_RUN_MARKER": marker,
        "CODEX_LITELLM_MODEL": model,
        "CODEX_LITELLM_REASONING_EFFORT": "low",
        "CLAUDE_LITELLM_MODEL": model,
        "OPENCODE_LITELLM_MODEL": model,
        "OPENCODE_LITELLM_SMALL_MODEL": model,
        "COPILOT_LITELLM_MODEL": model,
        "COPILOT_LITELLM_PROVIDER_MODEL_ID": model,
        "COPILOT_LITELLM_WIRE_MODEL": model,
        "PI_LITELLM_MODEL": model,
        "PI_LITELLM_SMALL_MODEL": model,
    }
    for spec in WRAPPERS:
        values[f"{spec.prefix}_LITELLM_COMPRESSION_MODE"] = "off"
        values[f"{spec.prefix}_LITELLM_BASE_URL"] = litellm_url
    for prefix in ("CODEX", "CLAUDE", "OPENCODE"):
        values[f"{prefix}_LITELLM_DISABLE_ANALYTICS_MCP"] = "1"
    return values


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda match: (
                match.group(1) + "[redacted]" if match.groups() else "[redacted]"
            ),
            redacted,
        )
    return redacted


def short_output(text: str, limit: int = 4000) -> str:
    redacted = redact(text)
    if len(redacted) <= limit:
        return redacted
    return redacted[:limit] + "\n... [truncated]"


def marker_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "target"


def run_local(spec: WrapperSpec, args: argparse.Namespace) -> bool:
    marker = f"{args.marker_prefix}-{spec.name}"
    executable = str(REPO_ROOT / "bin" / f"{spec.name}-litellm")
    env = os.environ.copy()
    env.update(
        common_env(model=args.model, litellm_url=args.litellm_url, marker=marker)
    )
    result = subprocess.run(
        command_for(spec, executable, marker),
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=args.timeout,
        check=False,
    )
    output = result.stdout + result.stderr
    if result.returncode == 0 and marker in output:
        print(f"local:{spec.name}=ok")
        return True
    print(f"local:{spec.name}=failed exit={result.returncode}")
    print(short_output(output), end="" if output.endswith("\n") else "\n")
    return False


def remote_script(
    *,
    command: list[str],
    env: dict[str, str],
) -> str:
    exports = [
        'export PATH="$HOME/.local/bin:$HOME/.local/share/node/bin:$PATH"',
        *[f"export {key}={quote(value)}" for key, value in sorted(env.items())],
    ]
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            *exports,
            f"exec {shell_join(command)}",
            "",
        ]
    )


def run_remote(spec: WrapperSpec, args: argparse.Namespace, target: str) -> bool:
    target_slug = marker_slug(target)
    marker = f"{args.marker_prefix}-{target_slug}-{spec.name}"
    env = common_env(model=args.model, litellm_url=args.litellm_url, marker=marker)
    command = command_for(spec, f"{spec.name}-litellm", marker)
    result = subprocess.run(
        ["ssh", target, "bash", "-s"],
        input=remote_script(command=command, env=env),
        text=True,
        capture_output=True,
        timeout=args.timeout,
        check=False,
    )
    output = result.stdout + result.stderr
    if result.returncode == 0 and marker in output:
        print(f"{target}:{spec.name}=ok")
        return True
    print(f"{target}:{spec.name}=failed exit={result.returncode}")
    print(short_output(output), end="" if output.endswith("\n") else "\n")
    return False


def main() -> int:
    args = parse_args()
    load_dotenv(REPO_ROOT / ".env")
    wrappers = selected_wrappers(args.wrapper)
    remote_targets = [target.strip() for target in args.remote if target.strip()]
    remote_targets.extend(args.remote_hosts.split())
    remote_targets = list(dict.fromkeys(remote_targets))
    local_targets = args.target or ([] if remote_targets else ["local"])

    ok = True
    if "local" in local_targets:
        for spec in wrappers:
            ok = run_local(spec, args) and ok
    for target in remote_targets:
        for spec in wrappers:
            ok = run_remote(spec, args, target) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
