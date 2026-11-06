# komodo-deploy

A [Claude Code](https://claude.com/claude-code) skill for deploying and operating
[Komodo](https://komo.do) stacks from your terminal or editor:

> "deploy web" · "is monitoring healthy?" · "show me the last deploys of api" · "add this app to Komodo"

It talks to the Komodo Core HTTP API with a dedicated, least-privilege API key, and always asks before
changing anything.

## Features

- **Deploy / pull / restart** a stack — with a pre-deploy preview (deployed vs latest commit, commits
  in between, image changes), explicit confirmation, live progress, post-deploy health check
  (restart loops, unhealthy containers) and failure diagnosis (missing variables, image pull, port
  conflicts, compose errors, missing networks).
- **Find the right stack automatically** — by name, from the current git repo, or from the current
  directory inside a mono-repo.
- **Resource-sync aware** — detects which sync manages a stack, shows pending changes before a
  deploy, can run the sync. UI-managed stacks work too.
- **Status, logs, history** of any stack.
- **Onboard new apps** into your sync repo, with secrets kept as Komodo secret variables (`[[VAR]]`).
- **`doctor`** — checks Python, credentials, DNS, TLS, Komodo version and permissions, with copy-paste
  fixes for your OS.
- Works on **Windows, macOS, Linux and WSL**; multiple Komodo instances via `--host` / profiles.
- No dependencies: Python 3.11+ standard library only.

## Requirements

- Komodo Core **v2.x**
- Python **3.11+** (`python3`; `python` on Windows)
- Claude Code

## Install

As a plugin (recommended):

```text
/plugin marketplace add BDvirus/komodo-deploy-skill
/plugin install komodo-deploy@komodo-deploy
```

Or manually — copy (or symlink) `skills/komodo-deploy` into `~/.claude/skills/`:

```bash
git clone https://github.com/BDvirus/komodo-deploy-skill
cp -r komodo-deploy-skill/skills/komodo-deploy ~/.claude/skills/
```

## Setup

1. In Komodo create a **service user** (non-admin) and a user group with **Read** on all resources
   and **Execute** on Stacks and ResourceSyncs, then create an API key for it.
2. Set `KOMODO_HOST`, `KOMODO_DEPLOY_API_KEY` and `KOMODO_DEPLOY_API_SECRET`.
3. Run `doctor` (or just ask Claude "komodo doctor").

Full instructions per OS, private-CA setup and multi-instance config:
[skills/komodo-deploy/references/setup.md](skills/komodo-deploy/references/setup.md).

## Usage

Talk to Claude naturally, or invoke the skill directly (`/komodo-deploy` for a manual install,
`/komodo-deploy:komodo-deploy` for the plugin). The underlying CLI can also be used on its own:

```bash
python3 skills/komodo-deploy/scripts/komodo.py doctor
python3 skills/komodo-deploy/scripts/komodo.py find --name web
python3 skills/komodo-deploy/scripts/komodo.py preview --stack web
python3 skills/komodo-deploy/scripts/komodo.py stack-action --stack web --action deploy
python3 skills/komodo-deploy/scripts/komodo.py --host komodo-staging.example.com status --stack web
```

Every command prints JSON; exit code `0` ok, `1` failed/unhealthy, `2` config/API error.

## Security model

- Use a dedicated non-admin service user: it can read and execute, but cannot modify stack
  definitions, create variables, or see secret variable values.
- The skill never prints secret variable values and never asks for them; secrets you onboard become
  `[[VARIABLE]]` references that you create in the Komodo UI.
- Every deploy, pull, restart and sync requires explicit confirmation. There is no stop/destroy/delete.
- Stack log tails are application output and may contain sensitive data — review before sharing.
- TLS is verified by default; use `KOMODO_CA_BUNDLE` for a private CA rather than `KOMODO_INSECURE`.

## Development

```bash
python -m unittest discover -s tests -v
```

Tested against Komodo Core 2.3.x. Issues and pull requests are welcome.

## License

[MIT](LICENSE)
