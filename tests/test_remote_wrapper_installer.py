import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install_remote_wrappers.py"


def test_remote_wrapper_installer_writes_secret_local_launchers(tmp_path: Path) -> None:
    install_root = tmp_path / "install"
    bin_dir = tmp_path / "bin"

    result = subprocess.run(
        [
            "python3",
            str(INSTALLER),
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


def test_remote_wrapper_installer_accepts_deduplicated_ssh_targets() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("install_remote_wrappers", INSTALLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = SimpleNamespace(
        remote=["pankaj@10.20.30.102", "pankaj@10.20.30.102"],
        remote_hosts="neeraj@10.20.30.131 pankaj@10.20.30.102",
    )

    assert module.remote_targets(args) == [
        "pankaj@10.20.30.102",
        "neeraj@10.20.30.131",
    ]


@pytest.mark.parametrize(
    ("path", "variable"),
    [
        ("~/.local/share/litellm-proxy-wrapper", "install_root"),
        ("/opt/litellm-proxy-wrapper", "install_root"),
    ],
)
def test_remote_expand_script_is_shell_side_home_aware(
    path: str, variable: str
) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("install_remote_wrappers", INSTALLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    script = module.remote_expand_script(path, variable)

    assert f"{variable}_arg=" in script
    assert f'{variable}="$HOME' in script
