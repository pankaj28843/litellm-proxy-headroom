import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_remote_wrapper_installer_writes_secret_local_launchers(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    bin_dir = tmp_path / "bin"

    result = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "scripts" / "install_remote_wrappers.py"),
            "--source",
            str(REPO_ROOT),
            "--install-root",
            str(install_root),
            "--bin-dir",
            str(bin_dir),
            "--litellm-master-key",
            "sk-test-installer-key",
            "--litellm-url",
            "http://10.20.30.1:24040",
        ],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert "sk-test-installer-key" not in result.stdout
    assert not (install_root / ".env").exists()
    assert (install_root / "bin" / "codex-litellm").exists()

    for name in ("codex", "claude", "opencode", "copilot", "pi"):
        launcher = bin_dir / f"{name}-litellm"
        assert launcher.exists()
        assert os.access(launcher, os.X_OK)
        text = launcher.read_text()
        assert "sk-test-installer-key" in text
        assert "http://10.20.30.1:24040" in text
        assert f"{install_root}/bin/{name}-litellm" in text

    assert (
        "CODEX_LITELLM_DISABLE_ANALYTICS_MCP=1"
        in (bin_dir / "codex-litellm").read_text()
    )
    assert (
        "CLAUDE_LITELLM_DISABLE_ANALYTICS_MCP=1"
        in (bin_dir / "claude-litellm").read_text()
    )
    assert (
        "OPENCODE_LITELLM_DISABLE_ANALYTICS_MCP=1"
        in (bin_dir / "opencode-litellm").read_text()
    )
    assert (
        "COPILOT_LITELLM_COMPRESSION_MODE=off"
        in (bin_dir / "copilot-litellm").read_text()
    )
