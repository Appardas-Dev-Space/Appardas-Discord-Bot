# 0.5.0 revision — implementation checkpoints

This file tracks verified facts and remaining work; it is not a release claim.
Source of requirements: revision 2 in `revision.md`. The initially truncated local
copy has now been restored through §15 and all three appendices.

## To-do

- [x] Investigate boot order, Graphify context, and baseline tests.
- [x] Migrate schema v3, bootstrap env owners, and add safe transactions.
- [x] Add account/grant services, redacted activity logging, and retention.
- [x] Replace link tokens with persistent cookie sessions and identify-only OAuth.
- [x] Mount public landing/docs/status with live manifest and hashed shared assets.
- [x] Implement grant-gated dashboard settings/cogs and atomic dual logging.
- [x] Implement owner Accounts/Activity, lookup, filters, and CSV.
- [x] Apply public/app directions; verify progressive enhancement and browser gates.
- [x] Archive/remove obsolete surfaces; update config, workflows, README, version.
- [x] Expand/run tests, drift checks, security/regression review, and Graphify update.
- [x] Record unverified deployment/OAuth/manual/48-hour release gates explicitly.

## Markers

### M1 — investigation / baseline

- Baseline: `.venv/bin/python -m pytest -q`: **157 passed**, 8 existing aiohttp AppKey warnings.
- Existing Graphify: 584 nodes / 872 edges; consulted summary and service traversal.
  Graph source positions are historical, so actual files are authoritative.
- Boot: `prepare()` opens DB and loads cogs; `start_server()` precedes `bot.start()`.
- Settings service uses `update_setting` (not the proposed `set_setting` name).
  Preserve Discord callers and share validation; add a compatible web entry point.
- Current settings writes/audit writes commit separately. Transaction-scoped calls
  must use the supplied connection, and cache/event side effects must wait for commit.
- No git repository in this workspace. Preserve source before deletions; never
  modify `.env`, production runtime DB, or other runtime storage for validation.
- Assumptions within the proposal: curated public `/status`, public renewal clock,
  read-only watches, no invented license/source URL. Production host/OAuth setup and
  external deployment are not authorized or verified by local implementation.

### M2 — foundations implemented

- Schema v3, closed accounts/grants, hashed sessions/state, activity redaction,
  explicit transaction connections and post-commit events are implemented.
- Both read-only deployment reviews received; startup wiring has replaced
  the old config consumers in `main.py`.
- Keep the public payload curated and the two dashboard command entry points for
  compatibility; they point to the same secret-free URL. Release version and live
  manifest timestamp are separate, with a deterministic strict debug fixture.
- No `.env` or runtime database has been modified. No deployment is claimed.
- Migration review received: commit cancellation, standalone failure rollback,
  owner startup wiring and camelCase redaction are addressed.

### M3 — full surface unification completed

- Obsolete surfaces archived in `archive/` (`help-site.tar.gz`, `web-preview/`, `tokens.py`, `docs.yml`).
- Fixed schema session identifier query inconsistency in `tests/test_web.py` (`id_hash` primary key).
- Added `pythonpath = .` to `pytest.ini` for direct test execution.
- Bumped `app/version.py` to `0.5.0` and regenerated `app/commands.json` via `tools/dump_commands.py`.
- `tools/dump_commands.py --check` passes cleanly with zero drift.
- Full pytest test suite: **195 passed, 0 failed**.
- Rewrote `README.md` to document the unified 0.5.0 single-process console, Discord OAuth2, and accounts model.

## Validation record

- Latest full test run (2026-10-23): **195 passed in 8.40s**.
- Zero regressions across core services: accounts, activity, announcements, cogs, config, db, github, nudge, prefixes, sessions, settings, status, and web.
- `app/commands.json` confirmed in sync with live Discord command tree.

## External & Runtime Release Gates (Unverified Locally)

The following verification gates require active external hosting credentials, live Discord OAuth application credentials, or production soak testing and cannot be verified within the isolated local environment:

1. **Live Discord OAuth Redirect & Token Exchange**:
   - `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET` configured in production panel.
   - Callback URI `https://<domain>/auth/callback` registered in Discord Developer Portal.
2. **Production Reverse Proxy / TLS Termination**:
   - Verification of `COOKIE_SECURE=true` behind external HTTPS proxy (Cloudflare / Caddy).
   - Validating `TRUST_PROXY=true` preserves real client IP in activity and rate-limit buckets.
3. **4-Day Host Renewal Soak Test**:
   - Verification of the 288-tick heartbeat strip and uptime watchdog on bot-hosting.net free-tier container lifecycle.
