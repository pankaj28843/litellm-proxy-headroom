from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def default_home_path(name: str, *, fallback: Path) -> Path:
    home = os.environ.get("HOME")
    if home:
        return Path(home).expanduser() / name
    return fallback


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def truthy_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw in {"1", "true", "TRUE", "yes", "YES", "on", "ON"}


def normalize_base_url(raw: str, *, env_name: str, suffix: str) -> str:
    parts = urlsplit(raw.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            f"{env_name} must be an http(s) URL without credentials, query, or fragment."
        )
    path = parts.path.rstrip("/")
    if not path:
        path = suffix
    elif not path.endswith(suffix):
        path = f"{path}{suffix}"
    if suffix == "/mcp":
        path = f"{path}/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def project_name_from_cwd() -> str:
    name = Path.cwd().name.strip()
    return quote(name, safe="-_.() ") if name else ""


def toml_key(key: str) -> str:
    if BARE_KEY_RE.match(key):
        return key
    return json.dumps(key)


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"cannot render TOML value of type {type(value).__name__}")


def render_toml(data: dict[str, object], *, header: str) -> str:
    lines: list[str] = [header]

    def write_table(table: dict[str, object], path: list[str]) -> None:
        scalars: list[tuple[str, object]] = []
        nested: list[tuple[str, dict[str, object]]] = []
        for key in sorted(table):
            value = table[key]
            if isinstance(value, dict):
                nested.append((key, value))
            else:
                scalars.append((key, value))

        if path:
            lines.append("")
            lines.append("[" + ".".join(toml_key(part) for part in path) + "]")
        for key, value in scalars:
            lines.append(f"{toml_key(key)} = {toml_value(value)}")
        for key, value in nested:
            write_table(value, [*path, key])

    write_table(data, [])
    return "\n".join(lines).rstrip() + "\n"


def sync_native_state(
    *,
    native_home: Path,
    managed_home: Path,
    excluded_names: set[str],
    backup_tag: str,
) -> None:
    if not native_home.exists():
        return
    for source in native_home.iterdir():
        if source.name in excluded_names:
            continue
        if source.name.endswith(".headroom-backup"):
            continue
        destination = managed_home / source.name
        if destination.is_symlink():
            if destination.resolve(strict=False) == source.resolve(strict=False):
                continue
            destination.unlink()
        elif destination.exists():
            backup = managed_home / (
                f".{source.name}.{backup_tag}.{int(time.time())}.{os.getpid()}"
            )
            destination.rename(backup)
        destination.symlink_to(source, target_is_directory=source.is_dir())


def find_real_executable(*, binary_name: str, wrapper_path: Path) -> str:
    self_path = wrapper_path.resolve()
    candidates: list[str] = []
    for path_dir in os.get_exec_path():
        candidate = Path(path_dir) / binary_name
        if candidate.exists() and os.access(candidate, os.X_OK):
            resolved = candidate.resolve()
            if resolved != self_path and str(resolved) not in candidates:
                candidates.append(str(candidate))
    if not candidates:
        raise FileNotFoundError(binary_name)
    return candidates[0]
