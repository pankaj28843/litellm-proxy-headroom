import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE = REPO_ROOT / "scripts" / "smoke_agent_cli_wrappers.py"


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("smoke_agent_cli_wrappers", SMOKE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_smoke_command_shapes_cover_agent_cli_entrypoints() -> None:
    module = load_smoke_module()
    commands = {
        spec.name: module.command_for(spec, f"{spec.name}-litellm", "marker-123")
        for spec in module.WRAPPERS
    }

    assert commands["codex"][:4] == [
        "codex-litellm",
        "exec",
        "--skip-git-repo-check",
        "--json",
    ]
    assert commands["claude"][:5] == [
        "claude-litellm",
        "--print",
        "--output-format",
        "json",
        "--no-session-persistence",
    ]
    assert commands["opencode"][:3] == ["opencode-litellm", "run", "--format"]
    assert "--yolo" not in commands["copilot"]
    assert commands["copilot"][:2] == ["copilot-litellm", "-s"]
    assert commands["pi"][:6] == [
        "pi-litellm",
        "--mode",
        "json",
        "--no-tools",
        "--thinking",
        "xhigh",
    ]


def test_smoke_env_disables_compression_and_analytics_mcp() -> None:
    module = load_smoke_module()

    env = module.common_env(
        model="gpt-5.5",
        litellm_url="http://10.20.30.1:24040",
        marker="marker-123",
    )

    assert env["CODEX_LITELLM_COMPRESSION_MODE"] == "off"
    assert env["CODEX_LITELLM_REASONING_EFFORT"] == "xhigh"
    assert env["CLAUDE_LITELLM_MODEL"] == "sonnet"
    assert env["CLAUDE_LITELLM_EFFORT"] == "xhigh"
    assert env["CLAUDE_LITELLM_DISABLE_ANALYTICS_MCP"] == "1"
    assert env["OPENCODE_LITELLM_DISABLE_ANALYTICS_MCP"] == "1"
    assert env["OPENCODE_LITELLM_VARIANT"] == "xhigh"
    assert env["COPILOT_LITELLM_MODEL"] == "gpt-5.5"
    assert env["COPILOT_LITELLM_REASONING_EFFORT"] == "xhigh"
    assert env["PI_LITELLM_SMALL_MODEL"] == "gpt-5.5"
    assert env["PI_LITELLM_THINKING"] == "xhigh"


def test_smoke_redacts_litellm_secrets() -> None:
    module = load_smoke_module()

    assert "sk-test-secret" not in module.redact(
        "LITELLM_MASTER_KEY=sk-test-secret token sk-other-secret"
    )
