# komodo-deploy setup

## 1. Komodo service user (once, in the Komodo UI)

1. Settings → Users → **New Service User**, e.g. `claude-deployer` (not admin).
2. Settings → User Groups → new group, e.g. `deployers`, add the user and grant:

   | Resource type | Permission |
   |---|---|
   | Server, Stack, Deployment, Build, Repo, Procedure, Action, Builder, Alerter, ResourceSync | **Read** on *All* |
   | Stack, ResourceSync | **Execute** on *All* |

   No Write and no Admin: stack definitions change through your sync repo or the UI, never through
   this key. Non-admin users also never see secret variable values.
3. Users → the service user → **API Keys** → create. The secret is shown once; put it straight
   into an environment variable, never into a chat.

## 2. Environment variables

| Variable | Value |
|---|---|
| `KOMODO_HOST` | `https://komodo.example.com` (a bare hostname also works) |
| `KOMODO_DEPLOY_API_KEY` | `K-...` |
| `KOMODO_DEPLOY_API_SECRET` | `S-...` |
| `KOMODO_CA_BUNDLE` *(optional)* | path to your CA certificate (`.pem`) if Komodo uses a private CA |
| `KOMODO_INSECURE` *(last resort)* | `1` disables TLS verification |
| `KOMODO_SYNC_NAME` *(optional)* | default resource sync; auto-detected when there is only one |
| `KOMODO_SYNC_REPO` *(optional)* | local checkout of your sync repo (used for onboarding) |

### Windows — PowerShell (persistent, current user)

```powershell
[Environment]::SetEnvironmentVariable('KOMODO_HOST', 'https://komodo.example.com', 'User')
[Environment]::SetEnvironmentVariable('KOMODO_DEPLOY_API_KEY', 'K-...', 'User')
[Environment]::SetEnvironmentVariable('KOMODO_DEPLOY_API_SECRET', 'S-...', 'User')
```

Restart your terminal / editor afterwards. Current session only: `$env:KOMODO_HOST = '...'` etc.

### Windows — cmd

```bat
setx KOMODO_HOST "https://komodo.example.com"
setx KOMODO_DEPLOY_API_KEY "K-..."
setx KOMODO_DEPLOY_API_SECRET "S-..."
```

### Share the Windows variables with WSL (optional)

```powershell
[Environment]::SetEnvironmentVariable('WSLENV', 'KOMODO_HOST/u:KOMODO_DEPLOY_API_KEY/u:KOMODO_DEPLOY_API_SECRET/u:KOMODO_CA_BUNDLE/p', 'User')
```

`/u` passes a value from Windows to WSL; `/p` also translates a Windows path to `/mnt/...`.
Run `wsl --shutdown` once so new WSL sessions pick it up. If `WSLENV` already exists, append to it.

### macOS — zsh

```bash
cat >> ~/.zshrc <<'EOF'
export KOMODO_HOST="https://komodo.example.com"
export KOMODO_DEPLOY_API_KEY="K-..."
export KOMODO_DEPLOY_API_SECRET="S-..."
EOF
source ~/.zshrc
```

### Linux / WSL — bash

```bash
cat >> ~/.bashrc <<'EOF'
export KOMODO_HOST="https://komodo.example.com"
export KOMODO_DEPLOY_API_KEY="K-..."
export KOMODO_DEPLOY_API_SECRET="S-..."
EOF
source ~/.bashrc
```

## 3. Private CA (self-signed Komodo certificate)

Python uses its own trust store in some environments (WSL, Linux, some macOS installs), so a CA
your browser trusts may still fail. Export the CA certificate as PEM and point `KOMODO_CA_BUNDLE`
at it:

- **Windows** (CA already in the user's Trusted Root store — replace the subject):

  ```powershell
  $dir = "$HOME\.config\komodo-deploy"; New-Item -ItemType Directory -Force $dir | Out-Null
  $c = Get-ChildItem Cert:\CurrentUser\Root, Cert:\LocalMachine\Root |
       Where-Object Subject -like 'CN=My LAN CA*' | Select-Object -First 1
  "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($c.RawData, 'InsertLineBreaks') +
    "`n-----END CERTIFICATE-----`n" | Set-Content -NoNewline -Encoding ascii "$dir\ca.pem"
  [Environment]::SetEnvironmentVariable('KOMODO_CA_BUNDLE', "$dir\ca.pem", 'User')
  ```

- **macOS** (CA in the System keychain):
  `security find-certificate -c "My LAN CA" -p /Library/Keychains/System.keychain > ~/.config/komodo-deploy/ca.pem`
- **Linux / WSL**: copy the PEM to `~/.config/komodo-deploy/ca.pem`, or trust it system-wide:
  `sudo cp ca.pem /usr/local/share/ca-certificates/my-ca.crt && sudo update-ca-certificates`.

## 4. Multiple Komodo instances (optional)

`~/.config/komodo-deploy/config.toml`:

```toml
default_host = "https://komodo.example.com"

[hosts."komodo-staging.example.com"]
key_env = "KOMODO_STAGING_API_KEY"
secret_env = "KOMODO_STAGING_API_SECRET"
sync = "staging-sync"
sync_repo = "/path/to/staging-sync-repo"
ca_bundle = "/path/to/ca.pem"
```

Then e.g. `komodo.py --host komodo-staging.example.com find --name web`.
Precedence: `--host` > `KOMODO_HOST` > `default_host`.

## 5. Verify

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/komodo.py" doctor   # use `python` on Windows
```

Every failed check has a `fix`. Common issues: the Komodo hostname doesn't resolve from WSL
(check `/etc/resolv.conf`), TLS verify failure (see section 3), Komodo older than v2.
