"""Resource syncs: describe, find the sync managing a stack, check pending, run."""
from __future__ import annotations

import json
import re

from .client import Client, KomodoError, update_id, wait_update

SYNC_FIELDS = ("repo", "branch", "git_provider", "resource_path", "files_on_host",
               "linked_repo", "managed", "delete", "include_resources")


def describe(sync: dict) -> dict:
    config = sync.get("config", {})
    info = sync.get("info", {})
    return {
        "name": sync.get("name"),
        **{field: config.get(field) for field in SYNC_FIELDS},
        "last_sync_hash": info.get("last_sync_hash"),
        "files": sorted({f.get("path") for f in info.get("remote_contents") or [] if f.get("path")}),
    }


def defines_stack(sync: dict, stack_name: str) -> bool:
    pattern = re.compile(
        r'\[\[stack\]\]\s*\n\s*name\s*=\s*"' + re.escape(stack_name) + r'"', re.MULTILINE)
    return any(pattern.search(f.get("contents") or "")
               for f in sync.get("info", {}).get("remote_contents") or [])


def load_syncs(client: Client, only: str | None = None) -> list[dict]:
    names = [only] if only else [s["name"] for s in client.list_all("ListResourceSyncs")]
    return [client.read("GetResourceSync", {"sync": n}) for n in names]


def info(client: Client, only: str | None = None) -> list[dict]:
    return [describe(s) for s in load_syncs(client, only)]


def managing_syncs(client: Client, stack_name: str, only: str | None = None) -> list[str]:
    return [s["name"] for s in load_syncs(client, only) if defines_stack(s, stack_name)]


def resolve_sync_name(client: Client, configured: str | None) -> str:
    if configured:
        return configured
    syncs = client.list_all("ListResourceSyncs")
    if len(syncs) == 1:
        return syncs[0]["name"]
    names = sorted(s["name"] for s in syncs)
    raise KomodoError(f"Found {len(names)} resource syncs {names}; pass --sync or set "
                      "KOMODO_SYNC_NAME")


def _touches(entry: dict, stack_name: str, stack_id: str | None) -> bool:
    blob = json.dumps(entry)
    return f'"{stack_name}"' in blob or (bool(stack_id) and stack_id in blob)


def pending_for(sync_info: dict, stack_name: str | None, stack_id: str | None) -> dict:
    resource = sync_info.get("resource_updates") or []
    related = [e for e in resource if stack_name and _touches(e, stack_name, stack_id)]
    return {
        "pending_hash": sync_info.get("pending_hash"),
        "last_sync_hash": sync_info.get("last_sync_hash"),
        "pending_error": sync_info.get("pending_error"),
        "total_resource_updates": len(resource),
        "variable_updates": len(sync_info.get("variable_updates") or []),
        "affects_stack": bool(related),
        "stack_updates": related,
        "other_updates": [e.get("target") for e in resource if e not in related],
    }


def check(client: Client, sync: str, stack_name: str | None, stack_id: str | None) -> dict:
    refreshed = True
    try:
        client.write("RefreshResourceSyncPending", {"sync": sync})
    except KomodoError as exc:
        if exc.status not in (401, 403):
            raise
        refreshed = False  # no permission: fall back to Core's cached pending state
    sync_info = client.read("GetResourceSync", {"sync": sync}).get("info", {})
    return {"sync": sync, "refreshed": refreshed, **pending_for(sync_info, stack_name, stack_id)}


def run(client: Client, sync: str, timeout_s: float) -> dict:
    update = client.execute("RunSync", {"sync": sync})
    done = wait_update(client, update_id(update), timeout_s)
    return {
        "sync": sync,
        "success": bool(done.get("success")),
        "stages": [{"stage": log.get("stage"), "success": log.get("success"),
                    "stdout": (log.get("stdout") or "")[-1500:],
                    "stderr": (log.get("stderr") or "")[-1500:]}
                   for log in done.get("logs") or []],
    }
