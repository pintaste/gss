# Changelog

## 0.1.0 — 2026-07-15

Initial public release.

### Features

- Capture and switch multiple Grok CLI OAuth accounts (`add`, `ls`, `sw`, `to`)
- Resolve accounts by number, email, or profile name
- Transactional switch with rollback and exclusive lock
- Refuse switch when the live login is unmanaged
- Directory mapping (`dir` / `auto`)
- Isolated runs via `GROK_HOME` (`exec` / `config-dir`)
- Diagnostics: `check`, `status`, `stats`, `whoami`
- Dry-run (`-n`) and quiet mode (`GAS_SILENT=1`)
- Automatic migration from the early named-profile layout

### Security

- Store directory mode `700`, token files mode `600`
- Atomic JSON writes
