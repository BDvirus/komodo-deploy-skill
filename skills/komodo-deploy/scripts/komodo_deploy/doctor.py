"""Environment diagnostics with copy-paste fixes per OS."""
from __future__ import annotations

import socket
import ssl
import sys
from urllib.parse import urlparse

from . import config
from .client import Client, KomodoError

MIN_PY = (3, 11)
MIN_KOMODO_MAJOR = 2
EXAMPLE_HOST = "https://komodo.example.com"

SETUP = {
    "windows": f"""# PowerShell (persistent, current user) - then restart your terminal / editor
[Environment]::SetEnvironmentVariable('KOMODO_HOST', '{EXAMPLE_HOST}', 'User')
[Environment]::SetEnvironmentVariable('KOMODO_DEPLOY_API_KEY', 'K-...', 'User')
[Environment]::SetEnvironmentVariable('KOMODO_DEPLOY_API_SECRET', 'S-...', 'User')
# optional: share with WSL
[Environment]::SetEnvironmentVariable('WSLENV', 'KOMODO_HOST/u:KOMODO_DEPLOY_API_KEY/u:KOMODO_DEPLOY_API_SECRET/u:KOMODO_CA_BUNDLE/p', 'User')""",
    "macos": f"""cat >> ~/.zshrc <<'EOF'
export KOMODO_HOST="{EXAMPLE_HOST}"
export KOMODO_DEPLOY_API_KEY="K-..."
export KOMODO_DEPLOY_API_SECRET="S-..."
EOF
source ~/.zshrc""",
    "linux": f"""cat >> ~/.bashrc <<'EOF'
export KOMODO_HOST="{EXAMPLE_HOST}"
export KOMODO_DEPLOY_API_KEY="K-..."
export KOMODO_DEPLOY_API_SECRET="S-..."
EOF
source ~/.bashrc""",
}
SETUP["wsl"] = SETUP["linux"] + "\n# or share the Windows variables via WSLENV (see references/setup.md)"

PYTHON_INSTALL = {
    "windows": "winget install Python.Python.3.13",
    "macos": "brew install python@3.13",
    "linux": "install python3 >= 3.11 with your package manager",
    "wsl": "sudo apt install python3",
}
CA_FIX = ("TLS verification failed: set KOMODO_CA_BUNDLE to your CA certificate (.pem), "
          "see references/setup.md; KOMODO_INSECURE=1 only as a last resort")


def setup_commands() -> str:
    return SETUP[config.platform_name()]


def _check(name: str, ok: bool, detail: str, fix: str | None = None) -> dict:
    return {"check": name, "ok": ok, "detail": detail, "fix": None if ok else fix}


def check_python() -> dict:
    ver = sys.version_info[:3]
    fix = f"Python >= 3.11 required: {PYTHON_INSTALL[config.platform_name()]}"
    return _check("python", ver >= MIN_PY, ".".join(map(str, ver)), fix)


def check_dns(host: str) -> dict:
    hostname = urlparse(host).hostname or ""
    try:
        addr = socket.getaddrinfo(hostname, None)[0][4][0]
        return _check("dns", True, f"{hostname} -> {addr}")
    except OSError as exc:
        fix = f"Make {hostname} resolvable from this machine (DNS server or hosts file)"
        if config.platform_name() == "wsl":
            fix += "; in WSL check /etc/resolv.conf points at a DNS server that knows the name"
        return _check("dns", False, str(exc), fix)


def version_ok(version: str | None) -> bool:
    try:
        return int(str(version).split(".")[0]) >= MIN_KOMODO_MAJOR
    except ValueError:
        return False


def check_api(settings: config.Settings) -> list[dict]:
    try:
        client = Client(settings)
        version = client.read("GetVersion", {}).get("version")
        if not version_ok(version):
            return [_check("version", False, f"Komodo {version}",
                           "komodo-deploy supports Komodo Core v2.x only — upgrade Komodo")]
        stacks = client.list_all("ListStacks")
    except ssl.SSLError as exc:
        return [_check("tls", False, str(exc), CA_FIX)]
    except KomodoError as exc:
        reason = str(exc)
        if "CERTIFICATE_VERIFY_FAILED" in reason:
            return [_check("tls", False, reason, CA_FIX)]
        fix = ("API key/secret rejected — create a new key for the deploy service user"
               if exc.status in (401, 403) else "Check KOMODO_HOST and that Komodo Core is up")
        return [_check("api", False, reason, fix)]
    return [
        _check("api", True, f"Komodo {version} at {settings.host}"),
        _check("read_permission", bool(stacks), f"{len(stacks)} stacks visible",
               "Grant the deploy user's group Read on all Stacks"),
    ]


def run(host_arg: str | None = None) -> dict:
    checks = [check_python()]
    try:
        settings = config.resolve_settings(host_arg)
    except config.ConfigError as exc:
        checks.append(_check("config", False, str(exc), setup_commands()))
        return {"ok": False, "platform": config.platform_name(), "checks": checks}
    checks.append(_check("config", True, f"host={settings.host}"))
    dns = check_dns(settings.host)
    checks.append(dns)
    if dns["ok"]:
        checks.extend(check_api(settings))
    return {"ok": all(c["ok"] for c in checks), "platform": config.platform_name(),
            "checks": checks}
