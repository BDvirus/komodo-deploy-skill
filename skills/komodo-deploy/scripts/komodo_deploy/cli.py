"""CLI entry: every subcommand prints one JSON document to stdout.

Exit codes: 0 ok, 1 operation failed / unhealthy / no match, 2 usage, config or API error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import config, doctor, stacks, syncs
from .client import DEFAULT_WAIT_TIMEOUT_S, Client, KomodoError, update_id, wait_update

EXIT_OK, EXIT_FAILED, EXIT_ERROR = 0, 1, 2
ACTIONS = {"deploy": "DeployStack", "pull": "PullStack", "restart": "RestartStack"}


def emit(payload: dict | list, code: int = EXIT_OK) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return code


def _client(args) -> Client:
    return Client(config.resolve_settings(args.host))


def _resolve_one(client: Client, name: str) -> dict:
    found = stacks.find(client, name)
    exact = [m for m in found["matches"] if m["name"] == name] or found["matches"]
    if len(exact) != 1:
        names = [m["name"] for m in found["matches"]] or found["candidates"]
        raise KomodoError(f"Stack '{name}' is ambiguous or unknown; candidates: {names}")
    return exact[0]


def cmd_doctor(args) -> int:
    result = doctor.run(args.host)
    return emit(result, EXIT_OK if result["ok"] else EXIT_FAILED)


def cmd_whoami(args) -> int:
    client = _client(args)
    version = client.read("GetVersion", {}).get("version")
    return emit({"host": client.settings.host, "version": version,
                 "visible_stacks": len(client.list_all("ListStacks"))})


def cmd_find(args) -> int:
    client = _client(args)
    result = stacks.find(client, args.name, os.getcwd())
    return emit({"host": client.settings.host, **result},
                EXIT_OK if result["matches"] else EXIT_FAILED)


def cmd_preview(args) -> int:
    client = _client(args)
    brief = _resolve_one(client, args.stack)
    result = stacks.preview(client, brief["name"], os.getcwd())
    return emit({"host": client.settings.host, "server": brief["server"],
                 "state": brief["state"],
                 "link": stacks.stack_link(client.settings.host, brief["id"]), **result})


def cmd_sync_info(args) -> int:
    client = _client(args)
    return emit({"host": client.settings.host, "local_sync_repo": client.settings.sync_repo,
                 "syncs": syncs.info(client, args.sync or client.settings.sync_name)})


def cmd_sync_check(args) -> int:
    client = _client(args)
    configured = args.sync or client.settings.sync_name
    if not args.stack:
        name = syncs.resolve_sync_name(client, configured)
        return emit({"host": client.settings.host, **syncs.check(client, name, None, None)})
    brief = _resolve_one(client, args.stack)
    managing = syncs.managing_syncs(client, brief["name"], configured)
    if not managing:
        return emit({"host": client.settings.host, "stack": brief["name"], "managed_by": None,
                     "note": "Stack is not defined in any resource sync (UI-managed); "
                             "skip the sync step."})
    results = [syncs.check(client, name, brief["name"], brief["id"]) for name in managing]
    return emit({"host": client.settings.host, "stack": brief["name"],
                 "managed_by": managing, "syncs": results,
                 "affects_stack": any(r["affects_stack"] for r in results)})


def cmd_sync_run(args) -> int:
    client = _client(args)
    name = syncs.resolve_sync_name(client, args.sync or client.settings.sync_name)
    result = syncs.run(client, name, args.timeout)
    return emit({"host": client.settings.host, **result},
                EXIT_OK if result["success"] else EXIT_FAILED)


def _failure_details(client: Client, brief: dict, summary: dict) -> dict:
    status = stacks.status(client, brief["name"])
    failed = summary["failed_stage"] or {}
    evidence = " ".join([failed.get("stderr_tail", ""), failed.get("stdout_tail", ""),
                         status["log_tail"]])
    return {"diagnosis": stacks.diagnose(evidence), "status": status,
            "rollback_hint": brief.get("deployed_hash")}


def cmd_stack_action(args) -> int:
    client = _client(args)
    brief = _resolve_one(client, args.stack)
    started = time.monotonic()
    update = client.execute(ACTIONS[args.action], {"stack": brief["name"], "services": []})
    summary = stacks.summarize_update(wait_update(client, update_id(update), args.timeout))
    health = (stacks.wait_healthy(client, brief["name"], args.health_timeout)
              if summary["success"] else None)
    result = {
        "host": client.settings.host,
        "stack": brief["name"],
        "action": args.action,
        "duration_s": round(time.monotonic() - started),
        "update": summary,
        "health": health,
        "link": stacks.stack_link(client.settings.host, brief["id"]),
    }
    ok = bool(summary["success"] and health and not health["problems"]
              and not health["timed_out"])
    if not ok:
        result.update(_failure_details(client, brief, summary))
    return emit(result, EXIT_OK if ok else EXIT_FAILED)


def cmd_wait(args) -> int:
    client = _client(args)
    summary = stacks.summarize_update(wait_update(client, args.update, args.timeout))
    return emit(summary, EXIT_OK if summary["success"] else EXIT_FAILED)


def cmd_status(args) -> int:
    client = _client(args)
    brief = _resolve_one(client, args.stack)
    result = stacks.status(client, brief["name"], args.tail)
    return emit({"host": client.settings.host, **result},
                EXIT_OK if result["health"]["healthy"] else EXIT_FAILED)


def cmd_history(args) -> int:
    client = _client(args)
    brief = _resolve_one(client, args.stack)
    return emit({"host": client.settings.host, "stack": brief["name"],
                 "updates": stacks.history(client, brief["id"], args.limit)})


def cmd_vars_check(args) -> int:
    client = _client(args)
    wanted = [n.strip() for n in args.names.split(",") if n.strip()]
    existing = {v["name"]: bool(v.get("is_secret")) for v in client.list_all("ListVariables")}
    result = {
        "missing": [n for n in wanted if n not in existing],
        "not_secret": [n for n in wanted if n in existing and not existing[n]],
        "ok": [n for n in wanted if existing.get(n)],
    }
    code = EXIT_OK if not result["missing"] and not result["not_secret"] else EXIT_FAILED
    return emit(result, code)


def _add(sub, name: str, func, *arguments: tuple[tuple, dict]) -> None:
    parser = sub.add_parser(name)
    for flags, kwargs in arguments:
        parser.add_argument(*flags, **kwargs)
    parser.set_defaults(func=func)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="komodo.py", description=__doc__)
    parser.add_argument("--host", help="Komodo URL or hostname (overrides KOMODO_HOST)")
    sub = parser.add_subparsers(dest="command", required=True)
    stack = (("--stack",), {"required": True})
    timeout = (("--timeout",), {"type": float, "default": DEFAULT_WAIT_TIMEOUT_S})
    sync = (("--sync",), {"help": "Resource sync name"})

    _add(sub, "doctor", cmd_doctor)
    _add(sub, "whoami", cmd_whoami)
    _add(sub, "find", cmd_find, (("--name",), {}))
    _add(sub, "preview", cmd_preview, stack)
    _add(sub, "history", cmd_history, stack,
         (("--limit",), {"type": int, "default": stacks.HISTORY_LIMIT}))
    _add(sub, "sync-info", cmd_sync_info, sync)
    _add(sub, "sync-check", cmd_sync_check, (("--stack",), {}), sync)
    _add(sub, "sync-run", cmd_sync_run, sync, timeout)
    _add(sub, "stack-action", cmd_stack_action, stack,
         (("--action",), {"choices": sorted(ACTIONS), "default": "deploy"}), timeout,
         (("--health-timeout",), {"type": float, "default": stacks.HEALTH_TIMEOUT_S}))
    _add(sub, "wait", cmd_wait, (("--update",), {"required": True}), timeout)
    _add(sub, "status", cmd_status, stack,
         (("--tail",), {"type": int, "default": stacks.LOG_TAIL}))
    _add(sub, "vars-check", cmd_vars_check,
         (("--names",), {"required": True, "help": "Comma-separated variable names"}))
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except config.ConfigError as exc:
        return emit({"error": str(exc), "setup": doctor.setup_commands()}, EXIT_ERROR)
    except KomodoError as exc:
        return emit({"error": str(exc), "status": exc.status}, EXIT_ERROR)
