# Discord Sub-Account Authentication Fix

## Outcome

**Status: SUCCESS.** Implemented and verified owner-provisioned Discord ID + username + temporary password accounts, local-only sign-in, and safe credential assignment/reset for existing accounts. Deployment remains a separate operation.

**Follow-up:** new account creation now automatically DMs credentials to the new account's Discord ID. See [the credential DM implementation report](discord-sub-account-credential-dm.md) for this extension and its latest validation results. The test counts below record the original authentication fix.

## Scope and Authorization

Implement the supplied `discord-sub-account-auth-fix.md` requirements: an owner creates a sub-account with a Discord ID, local username, and temporary password; sign-in uses local credentials only.

**UI permission:** the user chose **without `taste-skill`** for this fix. Preserve existing styles and form conventions. The skill is disabled for these artifacts; this is not authorization for future UI work.

## Pre-Change Investigation

- The initial Git working tree was clean at revision `96672d1`.
- Baseline validation: `.venv/bin/python -m pytest -q` — **214 passed**, with 127 existing aiohttp key warnings.
- `app/services/accounts.py`: account creation saves Discord identity, role, and grants but no local credentials. Local authentication, password hashing, and `must_change_password` already exist.
- `app/web/accounts.py` and `app/web/templates/app/admin_accounts.py`: creation omits credential inputs, and account details incorrectly instruct users to sign in with Discord.
- `app/web/auth.py`: local login works, but a legacy OAuth start action and callback remain reachable.
- `app/web/common.py`: existing owner-only authorization, CSRF checks, and atomic mutation handling must be retained. The existing first-password-change gate will be reused.
- `app/config.py` and `.env.example`: production still requires Discord OAuth credentials, contrary to the requested local-only login.
- `app/db.py` and `app/schema.sql`: account Discord IDs use SQLite integers, and username uniqueness is case-sensitive despite case-insensitive sign-in.
- The existing `.graphify/graph.json` was queried using its available `auth` and `login` vocabulary. It predates local credential authentication; current source and tests are authoritative for this fix.

## Implementation

1. Account creation now requires a validated username and temporary password, saves only the existing PBKDF2 password hash format, and sets `must_change_password`.
2. `AccountsService.set_credentials` and `POST /dashboard/admin/accounts/{account_id}/credentials` assign/reset credentials on the existing account. They preserve identity, roles, account status, history, and grants and invalidate prior sessions atomically.
3. Creation and detail forms use existing UI styles. Temporary passwords are neither echoed into HTML/JSON nor included in activity logs. Account lists expose/search the local username, and sign-in instructions now describe local credentials.
4. Schema v4 stores account Discord IDs as SQLite `TEXT` and adds a unique index on `lower(username)`. Account rebuilds, missing credential columns, legacy-session handling, and owner bootstrap share one transaction. Foreign-key checks and account sequence high-water marks are preserved.
5. Discord OAuth start/callback behavior and the outbound OAuth HTTP client are removed. Production no longer requires OAuth environment variables. Legacy settings/state helpers remain for compatibility, but cannot grant sign-in access.
6. Regression tests cover provisioning-to-login, first-password-change gating, reset session invalidation, uniqueness, permissions, CSRF, password secrecy, audit rollback, and v2/v3 migrations. Existing tests were updated to provision local credentials.

### Changed Components

| Files | Change |
| --- | --- |
| `app/services/accounts.py` | Validation, credential provisioning/reset, exact Discord identity conversion, username search. |
| `app/db.py`, `app/schema.sql` | Atomic v4 migration, text Discord IDs, case-insensitive uniqueness. |
| `app/web/accounts.py` | Credential form handling, owner-only reset endpoint, safe audit metadata and messages. |
| `app/web/templates/app/admin_accounts.py`, `app/web/templates/public/login.py` | Functional inputs and corrected local sign-in instructions; existing styling retained. |
| `app/web/auth.py`, `app/web/status.py`, `app/config.py`, `.env.example` | Local-only login and removal of the OAuth production prerequisite. |
| `app/services/activity.py` | Credential-reset/password-change activity filter options. |
| `tests/test_accounts.py`, `tests/test_auth_credentials.py`, `tests/test_account_migrations.py` | New regression coverage. |
| `tests/test_web.py`, `tests/test_config.py`, `tests/test_db.py`, `tests/test_settings.py` | Existing expectations aligned with local provisioning and schema v4; prefix persistence remains covered. |
| `README.md`, this report | Operational guidance and durable implementation context. |

## Implementation Defaults

- Account management remains **owner-only**, preserving the existing authorization model rather than broadening access to every admin.
- New usernames are trimmed and lowercased; supported characters are ASCII letters, digits, `.`, `_`, and `-`, with a length of 1–64 characters. Login retains its existing trimmed, case-insensitive lookup; legacy usernames, including those outside the new creation syntax, remain usable.
- Temporary passwords require at least six characters and cannot be blank, matching the existing minimum length for replacement passwords. Passwords are not trimmed before hashing or verification.
- A temporary-password login requires a password change before console access, reusing the existing policy and page.
- Discord IDs are stored as exact decimal strings in the account table. Python integer conversion remains available for existing Discord SDK and session integrations; no floating-point conversion is used.
- Do not silently merge or rename pre-existing usernames that collide case-insensitively. A conflicting migration must roll back so the records can be reconciled explicitly.
- Existing system usernames cannot be renamed through credential reset, preventing preliminary-admin reseeding. Discord-associated system owners without local credentials can receive them without changing their owner role.

## Validation Status

| Check | Result |
| --- | --- |
| Baseline full suite | 214 passed. |
| Focused accounts/auth/web/config/database suite | 58 passed. |
| Provisioning/authentication and legacy-migration suite | 17 passed. |
| Initial post-change full suite | 243 passed; one outdated schema-v3 assertion in the prefix-reopen test was corrected to v4. Prefix persistence itself passed. |
| `.venv/bin/python tools/dump_commands.py --check` | Command manifest matches the live command tree. |
| `.venv/bin/python -m compileall -q app tests tools` | Passed. |
| `git diff --check` | Passed. |
| `.venv/bin/python -m pytest -q --disable-warnings` | **244 passed**, 204 existing-style aiohttp key warnings, in 26.63 seconds. |
| Final scoped diff and migration review | Completed by the main agent; no additional concrete correctness issues found. |

Tests use temporary databases and local HTTP test servers, not a live Discord login or production data. Existing aiohttp application/request-key warnings are outside this fix's scope. No browser visual review or production deployment has been performed.

## Review Notes

- Owner-only authorization, fresh permission checks, CSRF validation, and atomic activity logging remain in place. Server roles/grants and inactive-account restrictions were not broadened.
- v2/v3 migration tests verify exact Discord association, preserved account IDs/hashes/history/grants, compatible v3 sessions, foreign-key enforcement, deleted-ID sequence preservation, repeat startup, and complete rollback on bootstrap failure or username collision.
- HTTP regression tests verify creation-to-login-to-password-change, password secrecy in responses/logs, owner-only resets, CSRF rejection, and rollback of credential changes/session deletion when activity logging fails.
- The repository-discovery explore subagent completed. Additional migration-review subagent attempts failed because of a provider resource mismatch; the main agent performed the migration review directly. No independent-agent validation is claimed.
- No standalone linter/type-checker command was configured or introduced. Python compilation, the full pytest suite, command-manifest synchronization, and diff whitespace checks passed.

## Deployment Notes

1. Take a consistent SQLite backup before starting the updated application. Do not copy a live WAL database as an ordinary file.
2. Check for case-insensitive username collisions on a copy of a v3 database before deployment:

   ```sql
   SELECT lower(username) AS username_key, COUNT(*) AS account_count
   FROM accounts
   WHERE username IS NOT NULL
   GROUP BY lower(username)
   HAVING COUNT(*) > 1;
   ```

3. If collisions exist, reconcile the intended usernames explicitly; migration refuses ambiguous credentials and rolls back rather than silently altering identities.
4. Startup applies schema v4. Older code does not support v4; rollback requires a compatible code/database backup pair.
5. Assign local credentials to existing Discord-ID-only accounts through their detail pages. Roles, account status, and grants remain unchanged; suspended/revoked accounts stay unable to sign in.

No production database has been accessed or migrated during this task. The legacy Graphify graph was not rewritten; this report provides current authentication context until the graph is refreshed.
