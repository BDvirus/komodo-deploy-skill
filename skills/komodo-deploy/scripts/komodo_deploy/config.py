"""Settings resolution: --host flag > KOMODO_HOST > config file default_host."""
from __future__ import annotations

import os
import platform
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

HOST_ENV = "KOMODO_HOST"
KEY_ENV = "KOMODO_DEPLOY_API_KEY"
SECRET_ENV = "KOMODO_DEPLOY_API_SECRET"
CA_ENV = "KOMODO_CA_BUNDLE"
INSECURE_ENV = "KOMODO_INSECURE"
SYNC_NAME_ENV = "KOMODO_SYNC_NAME"
SYNC_REPO_ENV = "KOMODO_SYNC_REPO"

CONFIG_PATH = Path.home() / ".config" / "komodo-deploy" / "config.toml"
TRUTHY = ("1", "true", "yes")


class ConfigError(Exception):
    """Raised when settings are missing or invalid; message includes the fix."""


@dataclass(frozen=True)
class Settings:
    host: str
    key: str
    secret: str
    ca_bundle: str | None
    insecure: bool
    sync_name: str | None
    sync_repo: str | None


def normalize_host(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ConfigError("Host is empty")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"Invalid host '{raw}'; expected e.g. https://komodo.example.com")
    return value.rstrip("/")


def platform_name() -> str:
    if os.name == "nt":
        return "windows"
    if platform.system() == "Darwin":
        return "macos"
    try:
        if "microsoft" in Path("/proc/version").read_text().lower():
            return "wsl"
    except OSError:
        pass
    return "linux"


def load_file_config(path: Path = CONFIG_PATH) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc


def _host_profile(file_cfg: dict, host: str) -> dict:
    hosts = file_cfg.get("hosts", {})
    netloc = urlparse(host).netloc
    return hosts.get(host) or hosts.get(netloc) or {}


def resolve_host(host_arg: str | None, env: dict, file_cfg: dict) -> str:
    raw = host_arg or env.get(HOST_ENV) or file_cfg.get("default_host")
    if not raw:
        raise ConfigError(
            f"No Komodo host. Pass --host, set {HOST_ENV}, or set default_host in {CONFIG_PATH}. "
            "Run `doctor` for setup commands."
        )
    return normalize_host(raw)


def resolve_settings(host_arg: str | None, env: dict | None = None,
                     file_cfg: dict | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    file_cfg = load_file_config() if file_cfg is None else file_cfg
    host = resolve_host(host_arg, env, file_cfg)
    profile = _host_profile(file_cfg, host)
    key_env = profile.get("key_env", KEY_ENV)
    secret_env = profile.get("secret_env", SECRET_ENV)
    key, secret = env.get(key_env), env.get(secret_env)
    if not key or not secret:
        raise ConfigError(
            f"Missing API credentials for {host}: set {key_env} and {secret_env}. "
            "Run `doctor` for setup commands."
        )
    insecure = env.get(INSECURE_ENV) or str(profile.get("insecure", ""))
    return Settings(
        host=host,
        key=key,
        secret=secret,
        ca_bundle=env.get(CA_ENV) or profile.get("ca_bundle"),
        insecure=insecure.lower() in TRUTHY,
        sync_name=env.get(SYNC_NAME_ENV) or profile.get("sync"),
        sync_repo=env.get(SYNC_REPO_ENV) or profile.get("sync_repo"),
    )
