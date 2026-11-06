# Changelog

## 0.1.0

First public release.

- Deploy / pull / restart stacks with confirmation, progress, post-deploy health check and failure diagnosis.
- `preview`, `status`, `history`, `find` (by name, git remote, or mono-repo run directory).
- Resource-sync aware: detects the sync managing a stack, refreshes and shows pending changes, runs syncs.
- Onboarding guide for new apps with secrets as Komodo `[[VARIABLE]]` references.
- `doctor` with OS-specific setup instructions (Windows, macOS, Linux, WSL).
- `--host` and per-host profiles for multiple Komodo instances.
- Komodo Core v2.x API (path-style requests, pagination disabled for lists).
