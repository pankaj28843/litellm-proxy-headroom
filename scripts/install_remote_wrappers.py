#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
from pathlib import Path
from shlex import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTALL_ROOT = "~/.local/share/litellm-proxy-wrapper"
DEFAULT_BIN_DIR = "~/.local/bin"
DEFAULT_LITELLM_URL = "http://10.20.30.1:24040"
WRAPPERS = {
    "codex": "CODEX",
    "claude": "CLAUDE",
    "opencode": "OPENCODE",
    "copilot": "COPILOT",
    "pi": "PI",
}
ANALYTICS_MCP_PREFIXES = {"CODEX", "CLAUDE", "OPENCODE"}
IGNORED_ROOT_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".env",
    ".env.local",
    "__pycache__",
    "data",
    "tmp",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install LiteLLM agent CLI wrapper launchers into a user-local bin dir."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=REPO_ROOT,
        help="repo checkout to copy from; defaults to this script's checkout",
    )
    parser.add_argument(
        "--install-root",
        type=Path,
        default=Path(DEFAULT_INSTALL_ROOT),
        help=f"wrapper code install root; default {DEFAULT_INSTALL_ROOT}",
    )
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=Path(DEFAULT_BIN_DIR),
        help=f"launcher directory; default {DEFAULT_BIN_DIR}",
    )
    parser.add_argument(
        "--litellm-url",
        default=os.environ.get("LITELLM_REMOTE_BASE_URL", DEFAULT_LITELLM_URL),
        help=f"LiteLLM base URL without /v1; default {DEFAULT_LITELLM_URL}",
    )
    parser.add_argument(
        "--litellm-master-key",
        default=os.environ.get("LITELLM_MASTER_KEY"),
        help="LiteLLM key to write into launchers; defaults to LITELLM_MASTER_KEY",
    )
    parser.add_argument(
        "--compression-mode",
        choices=("off", "on"),
        default="off",
        help="default wrapper compression header mode; default off",
    )
    parser.add_argument(
        "--enable-analytics-mcp",
        action="store_true",
        help="keep analytics MCP config enabled for wrappers that support it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be installed without writing files",
    )
    parser.add_argument(
        "--remote",
        action="append",
        default=[],
        metavar="USER@HOST",
        help=(
            "install to a remote SSH target instead of this host; may be passed "
            "more than once"
        ),
    )
    parser.add_argument(
        "--remote-hosts",
        default=os.environ.get("WRAPPER_REMOTE_HOSTS", ""),
        help="space-separated SSH targets to install remotely",
    )
    return parser.parse_args()


def expand(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def remote_targets(args: argparse.Namespace) -> list[str]:
    targets = [target.strip() for target in args.remote if target.strip()]
    targets.extend(args.remote_hosts.split())
    return list(dict.fromkeys(targets))


def ignore_names(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_ROOT_NAMES}


def copy_source(source: Path, install_root: Path, *, dry_run: bool) -> None:
    if source.resolve(strict=False) == install_root.resolve(strict=False):
        return
    if dry_run:
        return
    install_root.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name in IGNORED_ROOT_NAMES:
            continue
        destination = install_root / child.name
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        elif destination.exists() or destination.is_symlink():
            destination.unlink()
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, destination, ignore=ignore_names)
        else:
            shutil.copy2(child, destination, follow_symlinks=False)


def launcher_text(
    *,
    wrapper_name: str,
    prefix: str,
    install_root: Path,
    litellm_url: str,
    litellm_master_key: str,
    compression_mode: str,
    enable_analytics_mcp: bool,
) -> str:
    exports = [
        'export PATH="$HOME/.local/bin:$HOME/.local/share/node/bin:$PATH"',
        f"export LITELLM_MASTER_KEY={quote(litellm_master_key)}",
        f"export {prefix}_LITELLM_BASE_URL={quote(litellm_url)}",
        f"export {prefix}_LITELLM_COMPRESSION_MODE={quote(compression_mode)}",
    ]
    if prefix in ANALYTICS_MCP_PREFIXES and not enable_analytics_mcp:
        exports.append(f"export {prefix}_LITELLM_DISABLE_ANALYTICS_MCP=1")
    target = install_root / "bin" / f"{wrapper_name}-litellm"
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            *exports,
            f'exec {quote(str(target))} "$@"',
            "",
        ]
    )


def write_launchers(
    args: argparse.Namespace, install_root: Path, bin_dir: Path
) -> None:
    if args.dry_run:
        return
    bin_dir.mkdir(parents=True, exist_ok=True)
    for wrapper_name, prefix in WRAPPERS.items():
        path = bin_dir / f"{wrapper_name}-litellm"
        path.write_text(
            launcher_text(
                wrapper_name=wrapper_name,
                prefix=prefix,
                install_root=install_root,
                litellm_url=args.litellm_url,
                litellm_master_key=args.litellm_master_key,
                compression_mode=args.compression_mode,
                enable_analytics_mcp=args.enable_analytics_mcp,
            ),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_command(
    command: list[str],
    *,
    input_text: str | None = None,
) -> None:
    subprocess.run(
        command,
        check=True,
        text=True,
        input=input_text,
    )


def remote_expand_script(path: str, variable_name: str) -> str:
    return "\n".join(
        [
            f"{variable_name}_arg={quote(path)}",
            f'case "${{{variable_name}_arg}}" in',
            f"  '~') {variable_name}=\"$HOME\" ;;",
            f"  '~/'*) {variable_name}=\"$HOME/${{{variable_name}_arg#'~/'}}\" ;;",
            f'  *) {variable_name}="${{{variable_name}_arg}}" ;;',
            "esac",
        ]
    )


def remote_bootstrap_script(install_root: str) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            remote_expand_script(install_root, "install_root"),
            'mkdir -p "$install_root"',
            "",
        ]
    )


def remote_install_script(args: argparse.Namespace) -> str:
    install_args = [
        "--source",
        str(args.install_root),
        "--install-root",
        str(args.install_root),
        "--bin-dir",
        str(args.bin_dir),
        "--litellm-url",
        args.litellm_url,
        "--compression-mode",
        args.compression_mode,
    ]
    if args.enable_analytics_mcp:
        install_args.append("--enable-analytics-mcp")
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "export LITELLM_MASTER_KEY=\"$(cat <<'__LITELLM_MASTER_KEY__'",
            args.litellm_master_key,
            "__LITELLM_MASTER_KEY__",
            ')"',
            remote_expand_script(str(args.install_root), "install_root"),
            'python3 "$install_root/scripts/install_remote_wrappers.py" '
            + " ".join(quote(value) for value in install_args),
            "",
        ]
    )


def rsync_excludes() -> list[str]:
    excludes: list[str] = []
    for name in sorted(IGNORED_ROOT_NAMES):
        excludes.extend(["--exclude", name])
    return excludes


def install_remote(args: argparse.Namespace, target: str) -> None:
    if args.dry_run:
        print(
            f"would_install_remote target={target} install_root={args.install_root} "
            f"bin_dir={args.bin_dir} litellm_url={args.litellm_url} "
            f"analytics_mcp={'enabled' if args.enable_analytics_mcp else 'disabled'} "
            f"compression_mode={args.compression_mode}"
        )
        return

    source = expand(args.source)
    install_root = str(args.install_root)
    run_command(
        ["ssh", target, "bash", "-s"], input_text=remote_bootstrap_script(install_root)
    )
    run_command(
        [
            "rsync",
            "-a",
            "--delete",
            *rsync_excludes(),
            f"{source}/",
            f"{target}:{install_root.rstrip('/')}/",
        ]
    )
    run_command(["ssh", target, "bash", "-s"], input_text=remote_install_script(args))


def main() -> int:
    args = parse_args()
    if not args.litellm_master_key:
        raise SystemExit(
            "install_remote_wrappers.py: set LITELLM_MASTER_KEY or pass "
            "--litellm-master-key."
        )
    targets = remote_targets(args)
    if targets:
        for target in targets:
            install_remote(args, target)
            if not args.dry_run:
                print(f"installed_remote target={target}")
    else:
        source = expand(args.source)
        install_root = expand(args.install_root)
        bin_dir = expand(args.bin_dir)
        copy_source(source, install_root, dry_run=args.dry_run)
        write_launchers(args, install_root, bin_dir)
        action = "would_install" if args.dry_run else "installed"
        print(
            f"{action}_wrappers={','.join(f'{name}-litellm' for name in WRAPPERS)} "
            f"install_root={install_root} bin_dir={bin_dir} "
            f"litellm_url={args.litellm_url} "
            f"analytics_mcp={'enabled' if args.enable_analytics_mcp else 'disabled'} "
            f"compression_mode={args.compression_mode}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
