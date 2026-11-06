# Onboarding a new app into Komodo

Goal: get a new `[[stack]]` into Komodo and deploy it. The deploy key has no Write permission, so
stacks are created through a git-backed **resource sync** (preferred) or by the user in the UI.

## 1. Pick the target

Run `sync-info`.

- **A git-backed sync exists** (`repo` set, `files_on_host` false, `include_resources` true): use it.
  Several → ask which. Note its `resource_path` and `files` (the existing layout).
  Local checkout: `local_sync_repo` (from `KOMODO_SYNC_REPO` / the host profile). If unset, ask the
  user for the path, or offer to `git clone` the sync's `repo` and suggest setting `KOMODO_SYNC_REPO`.
- **No git-backed sync** (none, or only `files_on_host` / UI-defined): skip to step 5 (UI mode).

## 2. Gather

Ask for (propose defaults, confirm once):

| Field | How to choose |
|---|---|
| `name` | repo name, lowercase-hyphenated |
| `server` | list existing values from `find` output; ask if more than one |
| Source | **Own repo**: `git_account = "<komodo git account>"` + `repo = "<owner>/<repo>"` (+ `branch`). **Mono-repo already in Komodo as a Repo resource**: `linked_repo = "<repo resource>"` + `run_directory = "<dir>"` |
| `file_paths` | only if the compose file isn't `compose.yaml` / `docker-compose.yml` |
| `run_build` | `true` if the compose file has `build:` sections |
| `ignore_services` | one-shot init / migration services |
| Environment | from the app's `.env.example` |

Mirror the conventions of existing stacks in the sync files (server name, git account, TZ, etc.).

## 3. Secrets

Secret = passwords, tokens, API keys, client secrets, signing keys, salts, URLs embedding passwords.

- Variable name convention: `<STACK>_<KEY>` in UPPER_SNAKE (`-` → `_`), e.g. `WEB_JWT_SECRET`.
  Follow the existing naming in the sync repo if it differs.
- In the environment: `KEY=[[WEB_JWT_SECRET]]`; embedded: `DATABASE_URL=postgres://app:[[WEB_DB_PASS]]@db/app`.
- Give the user the list of names to create in Komodo → Settings → Variables (**Secret** checked).
  They enter the values themselves — never ask for or handle secret values.
- Verify with `vars-check --names A,B,C`: nothing may be `missing` or `not_secret`.

## 4. Write the stack (git-backed sync)

Follow the existing layout inside `resource_path`: append to the file that already holds
`[[stack]]` entries (e.g. `stacks.toml`, separated like the existing entries), or, if stacks are one
per file, create `<name>.toml`.

```toml
[[stack]]
name = "my-app"
[stack.config]
server = "<server>"
git_account = "<git account>"
repo = "<owner>/my-app"
environment = """
API_KEY=[[MY_APP_API_KEY]]
"""
```

Validate and scan from the sync repo root:

```bash
python3 -c "import tomllib,glob,sys;[tomllib.load(open(f,'rb')) for f in glob.glob(sys.argv[1]+'/**/*.toml',recursive=True)];print('ok')" <resource_path>
git diff -U0 | grep -iE '(pass|secret|token|key)[A-Z_]*\s*[=:]\s*[^[[:space:]]' | grep -v '\[\['
```

The grep must print nothing (every secret-looking value is a `[[VAR]]`). Then:

1. Show the diff and get confirmation.
2. `git pull --ff-only`, commit `feat: add <name> stack`, push to the sync's branch — only after
   the user confirms the push.
3. `sync-check` → pending must list only the new stack (Create). Anything else → stop and show it.
4. After confirmation `sync-run`, then continue with the Deploy workflow.

## 5. UI mode (no git-backed sync)

Give the user the TOML from step 4 and these steps: Komodo → Stacks → **New Stack** → set server,
source and environment as listed (or paste the TOML into a sync's UI file editor if they use one).
When they confirm it exists, continue with the Deploy workflow.
