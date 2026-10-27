# Tally loader — security notes

Surfaced while making the incremental sync multi-tenant-safe. Item 1 is now
partially remediated in-repo (see below); the remaining steps (credential
rotation, history purge) and item 2 must be tracked/remediated separately.

## 1. Committed database credentials  — PARTIALLY REMEDIATED
`tally-loader/config.json` previously contained a **live Aiven PostgreSQL host,
username and password in plaintext**, committed to git. Anyone with repo access
(or a shipped desktop build) could read/write the production analytics database
directly.

The loader does **not** need the committed values: `src/services/tally_loader_service.js`
overwrites `config.json` at runtime from `PG_SERVER` / `PG_USERNAME` / `PG_PASSWORD`
environment variables before each run.

Done:
- `config.json` is now **untracked** (`git rm --cached`) and **git-ignored**
  (`electron_app/.gitignore`), so the runtime-regenerated file (with real creds)
  can never be re-committed.
- A sanitized `config.example.json` (placeholders, incremental defaults) is
  committed as the template; copy it to `config.json` for standalone runs.

Still required (cannot be done from the repo):
- **Rotate the exposed Aiven password** — it must be assumed compromised, since
  it lived in git history (commits `352ae95`, `e3b5e86`, `d354435`).
- **Purge it from git history** (BFG / `git filter-repo`) — untracking only stops
  *future* commits; the secret remains in past commits until history is rewritten.
- Keep real credentials only in the desktop app's environment / secret store.

## 2. Every desktop writes directly to the shared production DB
Each desktop runs `node dist/index.mjs`, which connects straight to the shared
Postgres and writes `mst_*` / `trn_*` rows. The cross-tenant correctness of those
writes now depends entirely on the loader scoping every query by
`(user_id, company_name)` (fixed in `dist/tally.mjs`). This is defensible, but a
compromised or modified desktop client could still write outside its tenant because
it holds full DB credentials. A backend-mediated write path (desktop → backend →
DB, with the backend enforcing the tenant) would remove that trust assumption. Out
of scope for this change; noted for a future hardening pass.
