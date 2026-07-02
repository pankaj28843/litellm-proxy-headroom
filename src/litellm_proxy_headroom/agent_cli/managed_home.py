from __future__ import annotations

import json
import os
import re
import time
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
COMPRESSION_MODE_OFF_VALUES = {"0", "false", "no", "off", "disabled"}
COMPRESSION_MODE_ON_VALUES = {"1", "true", "yes", "on", "enabled"}
PREFERENCES_FILE_NAME = "litellm-preferences.json"
LEGACY_LITELLM_WRAPPER_MARKERS = (
    b"LITELLM_PROXY_ENV_FILE",
    b".config/litellm-proxy/env",
    b"10.20.30.1:11435",
)


def default_home_path(name: str, *, fallback: Path) -> Path:
    home = os.environ.get("HOME")
    if home:
        return Path(home).expanduser() / name
    return fallback


def load_dotenv(path: Path) -> set[str]:
    loaded: set[str] = set()
    if not path.exists():
        return loaded
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
        loaded.add(key)
    return loaded


def preference_value(
    *,
    env_name: str,
    preference_key: str,
    preferences: dict[str, str],
    dotenv_keys: set[str],
    cli_value: str | None = None,
    default: str = "",
) -> str:
    shell_value = None if env_name in dotenv_keys else os.environ.get(env_name)
    dotenv_value = os.environ.get(env_name) if env_name in dotenv_keys else None
    return (
        cli_value
        or shell_value
        or preferences.get(preference_key)
        or dotenv_value
        or default
    )


def truthy_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw in {"1", "true", "TRUE", "yes", "YES", "on", "ON"}


def load_preferences(home: Path) -> dict[str, str]:
    path = home / PREFERENCES_FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and value not in {None, ""}
    }


def write_preferences(home: Path, preferences: dict[str, str | None]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    cleaned = {
        key: value
        for key, value in sorted(preferences.items())
        if value is not None and value != ""
    }
    path = home / PREFERENCES_FILE_NAME
    tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    tmp_path.write_text(
        json.dumps(cleaned, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def option_value(argv: list[str], *names: str) -> str | None:
    expect_value = False
    for arg in argv:
        if expect_value:
            return arg
        if arg in names:
            expect_value = True
            continue
        for name in names:
            if arg.startswith(f"{name}="):
                return arg.split("=", 1)[1]
    return None


def compression_mode_header_value(raw: str | None, *, env_name: str) -> str | None:
    if raw is None or not raw.strip():
        return None
    normalized = raw.strip().lower()
    if normalized in COMPRESSION_MODE_OFF_VALUES:
        return "off"
    if normalized in COMPRESSION_MODE_ON_VALUES:
        return "on"
    raise ValueError(f"{env_name} must be on or off.")


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
    excluded_globs: tuple[str, ...] = (),
) -> None:
    if not native_home.exists():
        return
    for source in native_home.iterdir():
        if source.name in excluded_names:
            continue
        if any(fnmatch(source.name, pattern) for pattern in excluded_globs):
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


def sync_native_file(
    *,
    native_file: Path,
    managed_file: Path,
    backup_tag: str,
) -> None:
    if not native_file.exists():
        return
    if managed_file.is_symlink():
        if managed_file.resolve(strict=False) == native_file.resolve(strict=False):
            return
        managed_file.unlink()
    elif managed_file.exists():
        backup = managed_file.with_name(
            f".{managed_file.name}.{backup_tag}.{int(time.time())}.{os.getpid()}"
        )
        managed_file.rename(backup)
    managed_file.symlink_to(native_file, target_is_directory=native_file.is_dir())


def is_legacy_litellm_wrapper(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return False
    return data.startswith(b"#!") and any(
        marker in data for marker in LEGACY_LITELLM_WRAPPER_MARKERS
    )


def find_real_executable(*, binary_name: str, wrapper_path: Path) -> str:
    self_path = wrapper_path.resolve()
    candidates: list[str] = []
    for path_dir in os.get_exec_path():
        candidate = Path(path_dir) / binary_name
        if candidate.exists() and os.access(candidate, os.X_OK):
            resolved = candidate.resolve()
            if resolved == self_path or is_legacy_litellm_wrapper(candidate):
                continue
            if str(resolved) not in candidates:
                candidates.append(str(candidate))
    if not candidates:
        raise FileNotFoundError(binary_name)
    return candidates[0]
