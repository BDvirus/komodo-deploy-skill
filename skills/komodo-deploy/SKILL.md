---
name: komodo-deploy
description: Deploy, pull, restart, and check status, logs and history of Komodo (komo.do) stacks, and onboard new apps into a Komodo resource-sync repo. Use when the user says "deploy <stack>", "deploy" inside an app repo, "redeploy", "restart <stack>", "is <stack> up", "stack logs", "add this app to Komodo", or in Hebrew "תעלה את", "תפרוס", "דפלוי", "תריסטרט", "מה המצב של".
license: MIT
compatibility: Komodo Core v2.x; Python 3.11+ (stdlib only); network access to Komodo Core; Windows, macOS, Linux, WSL.
---

# Komodo deploy

All Komodo access goes through one stdlib-only Python script that prints JSON:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/komodo.py" <command> [options]
```

On Windows use `python` (or `py -3`) instead of `python3`. Put `--host <url|hostname>` **before**
the command to target a specific Komodo instance (use it when the user names one).

Exit codes: `0` ok, `1` failed / unhealthy / no match, `2` config or API error.
Progress lines go to stderr; the JSON result goes to stdout.

| Command | Purpose |
|---|---|
| `doctor` | Checks Python, host/credentials, DNS, TLS, Komodo version, API access — each failure has a `fix` |
| `find [--name N]` | Find a stack by name; without a name, by the current directory's git remote (mono-repos: by run directory) |
| `preview --stack N` | Deployed vs latest commit, commits between, image changes, link |
| `sync-info [--sync S]` | Resource syncs: repo, branch, resource_path, files, managed/delete flags |
| `sync-check [--stack N]` | Which sync manages the stack (if any), refresh, pending changes |
| `sync-run [--sync S]` | Execute a resource sync and wait |
| `stack-action --stack N --action deploy\|pull\|restart` | Run, wait, health-check, diagnose on failure |
| `status --stack N [--tail 50]` | Service states, health, log tail |
| `history --stack N [--limit 5]` | Recent updates: when, who, result |
| `vars-check --names A,B` | Which Komodo variables are missing / not secret (never prints values) |

Exit code `2` with a config, TLS or auth error → run `doctor` and show the user the `fix` lines.
First-time setup (service user, permissions, environment variables): [references/setup.md](references/setup.md).

## Deploy workflow

1. **Resolve** — `find --name <stack>`, or `find` with no name inside an app repo.
   No match → show `candidates`; if the app isn't in Komodo yet, go to *Onboard*.
   Several matches → ask which one.
2. **Sync check** — `sync-check --stack <name>`.
   - `managed_by: null` → the stack is managed in the Komodo UI; skip this step.
   - `affects_stack: true` → show the pending change and ask whether to `sync-run` first. If declined, stop.
   - Other pending updates → mention them, don't apply them. Any `pending_error` → show it and stop.
3. **Preview + confirm** — `preview --stack <name>`. Show briefly: host, stack, server, state,
   deployed → latest commit (one-line messages), `commits_between` if present, image changes
   (`unresolved` = resolved at deploy time). If `up_to_date`, say a deploy re-applies the same commit.
   **Ask for explicit confirmation. Never deploy without it.**
4. **Deploy** — `stack-action --stack <name> --action deploy` (run it in the background if your tool
   allows; it can take minutes).
5. **Report** —
   - Success + healthy: one line with duration and the `link`.
   - `health.timed_out` or `health.problems`: list affected services and a short `status.log_tail` excerpt.
   - Failure: `update.failed_stage` (stage, stderr tail), each `diagnosis` hint, and the
     `rollback_hint` commit as a *manual* option. Never auto-retry or auto-rollback.
   - If `duration_s` > 60 and a notification tool is available, notify the user of the result.

`pull` and `restart` follow the same flow (skip the commit preview, still confirm).
Status, logs and history requests are read-only: run the command and summarize, no confirmation.

## Onboard workflow (app not in Komodo yet)

Follow [references/onboarding.md](references/onboarding.md): gather details → secrets become
`[[VARIABLE]]` references that the **user** creates in the Komodo UI → `vars-check` → add the
`[[stack]]` to the sync repo (or give the user UI steps when there is no git-backed sync) →
validate + secret scan → show diff → commit/push only after confirmation → `sync-run` → Deploy.

## Rules

- Never print, echo, commit or paste secret values — only variable names.
- Never deploy, pull, restart or run a sync without explicit confirmation for that action.
- Never stop/destroy stacks or delete resources; the script has no such command — point the user to the UI.
- Change stack definitions only through the sync repo (or the user in the UI), never via API writes.
- Log tails may contain application output; summarize instead of pasting large chunks.
- Always state which Komodo host you acted on.
