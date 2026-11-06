"""Stack lookup, change preview, health evaluation and failure diagnosis."""
from __future__ import annotations

import re
import subprocess
import time
from typing import Callable
from urllib.parse import urlparse

from .client import Client

HEALTH_TIMEOUT_S = 120
HEALTH_POLL_S = 5.0
LOG_TAIL = 50
HISTORY_LIMIT = 5
GIT_TIMEOUT_S = 10

_SCP_REMOTE_RE = re.compile(r"^[\w.-]+@[\w.-]+:(?P<path>.+)$")

# (kind, pattern, hint) — every matching rule is reported.
DIAGNOSIS_RULES = [
    ("missing_variable", r"\[\[([A-Z0-9_]+)\]\]|variable .* not found",
     "A [[VARIABLE]] was not interpolated — create it in Komodo (Settings → Variables, Secret)."),
    ("image_pull", r"pull access denied|manifest unknown|unauthorized|not found: manifest",
     "Image pull failed — check the image tag and registry credentials (registry_account)."),
    ("port_conflict", r"port is already allocated|address already in use",
     "Host port already in use — stop the conflicting container or change the port."),
    ("compose_error", r"yaml:|services\.[\w-]+ |invalid compose|no such file or directory",
     "Compose file problem — check run_directory / file_paths and YAML syntax."),
    ("network_missing", r"network [\w-]+ (?:declared as external, but could not be found|not found)",
     "External docker network missing — create it or deploy the stack that owns it first."),
    ("env_missing", r"variable is not set|required variable",
     "A compose ${VAR} is unset — add it to the stack environment."),
    ("healthcheck", r"unhealthy|dependency failed to start",
     "A container failed its healthcheck — inspect that service's logs."),
]

PROBLEM_STATES = {"dead", "empty", "missing", "removing", "paused"}
PENDING_STATES = {"created"}


# ---------- git helpers ----------

def parse_git_remote(url: str) -> str | None:
    """Return the repo path ('owner/repo', or 'group/sub/repo') from any git remote URL."""
    url = url.strip()
    if not url:
        return None
    scp = _SCP_REMOTE_RE.match(url)
    path = scp.group("path") if scp else urlparse(url).path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path.lower() if "/" in path else None


def _git(args: list[str], cwd: str | None) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_S, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def current_repo_slug(cwd: str | None = None) -> str | None:
    out = _git(["remote", "get-url", "origin"], cwd)
    return parse_git_remote(out) if out else None


def current_repo_prefix(cwd: str | None = None) -> str:
    """cwd relative to the repo root, e.g. 'apps/web/' ('' at the root)."""
    return _git(["rev-parse", "--show-prefix"], cwd) or ""


# ---------- lookup ----------

def match_stacks(stacks: list[dict], name: str | None, repo_slug: str | None) -> list[dict]:
    """Exact name > case-insensitive name > substring; else match by git repo path."""
    if name:
        exact = [s for s in stacks if s["name"] == name]
        if exact:
            return exact
        lowered = [s for s in stacks if s["name"].lower() == name.lower()]
        return lowered or [s for s in stacks if name.lower() in s["name"].lower()]
    if repo_slug:
        return [s for s in stacks if (s["info"].get("repo") or "").lower() == repo_slug]
    return []


def pick_by_run_directory(candidates: list[tuple[dict, str]], prefix: str) -> list[dict]:
    """Mono-repo: keep stacks whose run_directory contains (or is contained in) the cwd prefix.
    candidates: (stack, run_directory). The deepest matching run_directory wins."""
    norm_prefix = prefix.strip("/").lower()
    if not norm_prefix:
        return [s for s, run_dir in candidates if run_dir.strip("./").strip("/") == ""]
    scored = []
    for stack, run_dir in candidates:
        rd = run_dir.strip("./").strip("/").lower()
        if rd and (norm_prefix == rd or norm_prefix.startswith(rd + "/")):
            scored.append((len(rd), stack))
    if not scored:
        return []
    best = max(score for score, _ in scored)
    return [s for score, s in scored if score == best]


def stack_brief(item: dict) -> dict:
    info = item["info"]
    return {
        "name": item["name"],
        "id": item["id"],
        "server": info.get("server_name") or info.get("swarm_name"),
        "state": info.get("state"),
        "status": info.get("status"),
        "repo": info.get("repo"),
        "branch": info.get("branch"),
        "linked_repo": info.get("linked_repo_name") or None,
        "deployed_hash": info.get("deployed_hash"),
        "latest_hash": info.get("latest_hash"),
    }


def find(client: Client, name: str | None, cwd: str | None = None) -> dict:
    stacks = client.list_all("ListStacks")
    slug = None if name else current_repo_slug(cwd)
    matches = match_stacks(stacks, name, slug)
    if slug and len(matches) > 1:
        runs = [(s, client.read("GetStack", {"stack": s["id"]})["config"].get("run_directory", ""))
                for s in matches]
        narrowed = pick_by_run_directory(runs, current_repo_prefix(cwd))
        matches = narrowed or matches
    return {
        "query": name or (f"git remote {slug}" if slug else None),
        "matches": [stack_brief(s) for s in matches],
        "candidates": [] if matches else sorted(s["name"] for s in stacks),
    }


# ---------- preview ----------

def _images(services: list[dict] | None) -> dict:
    return {s.get("service_name"): s.get("image") for s in services or []}


def image_changes(deployed: list[dict] | None, latest: list[dict] | None) -> list[dict]:
    """Changed images per service. Latest images containing ${...} are compose templates
    resolved only at deploy time, so they are reported as 'unresolved' not as a change."""
    before, after = _images(deployed), _images(latest)
    changes = []
    for svc in sorted(set(before) | set(after)):
        old, new = before.get(svc), after.get(svc)
        if old == new:
            continue
        if new and "${" in new:
            changes.append({"service": svc, "from": old, "to": None, "unresolved": new})
        else:
            changes.append({"service": svc, "from": old, "to": new})
    return changes


def first_line(text: str | None) -> str | None:
    return text.strip().splitlines()[0] if text and text.strip() else text


def local_commits(deployed: str, latest: str, cwd: str | None) -> list[str] | None:
    out = _git(["log", "--oneline", f"{deployed}..{latest}"], cwd)
    return None if out is None else [line for line in out.splitlines() if line]


def preview(client: Client, stack: str, cwd: str | None = None) -> dict:
    full = client.read("GetStack", {"stack": stack})
    info = full.get("info", {})
    deployed, latest = info.get("deployed_hash"), info.get("latest_hash")
    stack_repo = (full.get("config", {}).get("repo") or "").lower()
    same_repo = bool(cwd) and bool(stack_repo) and current_repo_slug(cwd) == stack_repo
    commits = local_commits(deployed, latest, cwd) if deployed and latest and same_repo else None
    return {
        "stack": stack,
        "deployed": {"hash": deployed, "message": first_line(info.get("deployed_message"))},
        "latest": {"hash": latest, "message": first_line(info.get("latest_message"))},
        "up_to_date": bool(deployed) and deployed == latest,
        "commits_between": commits,
        "image_changes": image_changes(info.get("deployed_services"), info.get("latest_services")),
        "remote_errors": info.get("remote_errors") or [],
        "note": "latest_hash is Komodo's last poll; a very recent push may not be seen yet.",
    }


# ---------- health ----------

def _service_state(svc: dict) -> tuple[str, str]:
    container = svc.get("container") or {}
    state = (svc.get("state") or container.get("state") or "missing").lower()
    status = (container.get("status") or "").lower()
    return state, status


def evaluate_health(services: list[dict], ignore: list[str] | None = None) -> dict:
    """Ignored services and one-shot containers that exited 0 are not problems.
    Uses the v2 top-level service state (works for compose and swarm stacks)."""
    problems, pending = [], []
    for svc in services:
        name = svc.get("service")
        if name in (ignore or []):
            continue
        state, status = _service_state(svc)
        if state == "exited" and status.startswith("exited (0)"):
            continue
        if state == "restarting" or "restarting" in status:
            problems.append({"service": name, "issue": "restart loop", "status": status})
        elif state == "exited" or state in PROBLEM_STATES:
            problems.append({"service": name, "issue": state, "status": status})
        elif "unhealthy" in status:
            problems.append({"service": name, "issue": "unhealthy", "status": status})
        elif "health: starting" in status or state in PENDING_STATES:
            pending.append(name)
    return {"healthy": not problems and not pending, "pending": pending, "problems": problems}


def ignored_services(client: Client, stack: str) -> list[str]:
    return client.read("GetStack", {"stack": stack}).get("config", {}).get("ignore_services") or []


def wait_healthy(client: Client, stack: str, timeout_s: float = HEALTH_TIMEOUT_S,
                 interval_s: float = HEALTH_POLL_S, sleep: Callable = time.sleep) -> dict:
    deadline = time.monotonic() + timeout_s
    ignore = ignored_services(client, stack)
    while True:
        result = evaluate_health(client.read("ListStackServices", {"stack": stack}), ignore)
        if result["healthy"] or time.monotonic() >= deadline:
            return {**result, "timed_out": not result["healthy"] and not result["problems"]}
        sleep(interval_s)


# ---------- failure / status / history ----------

def diagnose(text: str) -> list[dict]:
    findings = []
    for kind, pattern, hint in DIAGNOSIS_RULES:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            findings.append({"kind": kind, "evidence": match.group(0)[:200], "hint": hint})
    return findings


def summarize_update(update: dict) -> dict:
    logs = update.get("logs") or []
    failed = next((log for log in logs if not log.get("success")), None)
    return {
        "operation": update.get("operation"),
        "success": bool(update.get("success")),
        "stages": [{"stage": log.get("stage"), "success": log.get("success")} for log in logs],
        "failed_stage": None if not failed else {
            "stage": failed.get("stage"),
            "command": failed.get("command"),
            "stderr_tail": (failed.get("stderr") or "")[-2000:],
            "stdout_tail": (failed.get("stdout") or "")[-1000:],
        },
    }


def status(client: Client, stack: str, tail: int = LOG_TAIL) -> dict:
    services = client.read("ListStackServices", {"stack": stack})
    log = client.read("GetStackLog", {"stack": stack, "services": [], "tail": tail})
    return {
        "stack": stack,
        "services": [{
            "service": s.get("service"),
            "image": s.get("image"),
            "state": _service_state(s)[0],
            "status": (s.get("container") or {}).get("status"),
        } for s in services],
        "health": evaluate_health(services, ignored_services(client, stack)),
        "log_tail": (log.get("stdout") or "") + (log.get("stderr") or ""),
    }


def display_user(username: str | None) -> str | None:
    """Komodo's built-in actors (sync, auto-update…) report all-zero-prefixed ids."""
    if username and re.fullmatch(r"0{20,}\d+", username):
        return f"system ({username.lstrip('0') or '0'})"
    return username


def history(client: Client, stack_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    resp = client.read("ListUpdates", {
        "query": {"target.type": "Stack", "target.id": stack_id}, "page": 0})
    return [{
        "id": u.get("id"),
        "operation": u.get("operation"),
        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(u["start_ts"] / 1000))
        if u.get("start_ts") else None,
        "user": display_user(u.get("username")),
        "status": u.get("status"),
        "success": u.get("success"),
    } for u in (resp.get("updates") or [])[:limit]]


def stack_link(host: str, stack_id: str) -> str:
    return f"{host}/stacks/{stack_id}"
