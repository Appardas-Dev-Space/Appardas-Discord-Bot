# appardas — revision.md

> **Status:** Proposed · revision 2
> **Date:** 2026-10-23
> **Supersedes:** README §1–§5 (v3 planning), `.env.example` dashboard block, `help-site/` deployment
> **Target version:** `0.5.0` (from `0.4.0`)
> **Owner:** bot + web surface
> **Design authorization:** `taste-skill` granted for this revision (see §8)

---

## 0. The six decisions

| # | Decision | In one line |
|---|---|---|
| 1 | **One deployment, one source** | Delete Astro. Public docs, landing, status and dashboard are rendered by the bot's own `app/web/templates/` on the one HTTP port. |
| 2 | **Always-on dashboard** | The dashboard lives as long as the process does. No single-use magic links, no TTL page, no "expired" dead end. Public face on `/`, private face under `/dashboard`. |
| 3 | **The landing page is the status page** | `/` shows the 24-hour heartbeat, all five cog states and the current health verdict, from live data. |
| 4 | **Login, not registration** | Discord OAuth2 proves identity. Access is an account the owner created — there is no sign-up form, no invite link and no auto-provisioning. |
| 5 | **Accounts are grants, not Discord roles** | The owner assigns a role (`owner`, `admin`, `viewer`) and the guilds each account may touch. Manage Server in Discord grants nothing here. |
| 6 | **Every change is attributable** | An append-only `activity_log` records which account did what, before and after, from where, and whether it succeeded. The owner reads it in an Activity screen. |

Everything below is written to be implemented without re-deriving these decisions.

---

## 1. What exists today (0.4.0)

```
                       ┌──────────────────────────────┐
   Discord  ──────────▶│  app/main.py                 │
                       │  · discord.py (5 cogs)       │
                       │  · aiohttp on :8080          │
                       │    /health  /status  /assets │
                       │    /dashboard?t=<token>      │
                       └──────────────────────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
   help-site/ (Astro, static)                  GitHub Pages / Cloudflare
   /bot-docs, /cogs, /getting-started          separate deploy, separate build
   built from app/commands.json                can be green while bot is dead
```

| Surface | Served by | Host | Auth | Lifetime | Source of truth |
|---|---|---|---|---|---|
| Landing / docs | Astro static build | GitHub Pages (`/bot-docs`) | none | permanent | `help-site/src/data/commands.json` (a copy) |
| Status JSON | aiohttp `/status` | bot host | optional `STATUS_TOKEN` | process | `StatusService` |
| Health | aiohttp `/health` | bot host | none | process | `StatusService` |
| Dashboard | aiohttp `/dashboard?t=…` | bot host | single-use signed link, TTL 600 s | **expires while the bot is online** | `DashboardTokens` |
| Settings | Discord only | — | Manage Server | — | `SettingsService` |

### 1.1 The six problems this revision fixes

| ID | Problem | Evidence in the code |
|---|---|---|
| **P1** | Docs can be stale and are a second deployment | `help-site/scripts/sync-commands.mjs` copies `app/commands.json`; the build only runs when someone remembers `/appardas dump-commands` and pushes `main`. |
| **P2** | The dashboard expires | `DASHBOARD_LINK_TTL_S=600`, `consume()` deletes the token, the "expired or already used" page returns 410 (`app/web/templates/pages.py`, `expired_page`). |
| **P3** | Secrets travel in URLs | `?t=<token>` in every nav link, form action and `window.__APPARDAS__`; the URL survives in browser history, copied links and screenshots (`Referrer-Policy: no-referrer` limits the rest). |
| **P4** | No settings UI | `CONFIG_KEYS = ("announce_channel", "github_channel", "mod_log", "timezone", "prefix")` has no web counterpart; only cog toggles are writable. |
| **P5** | Two truths about availability | The docs site stays up when the bot dies, so the public face can claim health the bot does not have. |
| **P6** | Access is inferred, not granted; changes are not attributable to an account | Authorization reads Manage Server from the OAuth guild list; `audit_log` records a Discord `actor_id` but no account, IP, before/after or sign-in trail. |

### 1.2 What stays

- One Python process, two direct dependencies (`discord.py`, `python-dotenv`), SQLite, no Node, no build step.
- The five cogs, the registry, the command gate, the audit log and the guardrails.
- `StatusService` snapshot caching (60 s), the heartbeat table (288 buckets × 5 min) and the renewal clock.
- Amber-on-stone identity, lowercase `appardas`, Public Sans self-hosted.
- `data/` runtime storage and the 512 MB / 1 GB host budget.

---

## 2. Decision 1 — Ditch Astro, one deployment from web templates

### 2.1 Principle

> The website is not a second product. It is the bot's HTTP port rendered through
> `app/web/templates/`. If the bot is deployed, the site is deployed.

No build step, no Node, no generated copy of the command manifest, no second host.

### 2.2 Removed

| Remove | Reason |
|---|---|
| `help-site/` (entire directory) | Astro, `node_modules`, `dist`, `.astro`, `public/site.css`, `src/data/commands.json`, the three `.mjs` scripts |
| `.github/workflows/docs.yml` | No Pages build, no `pages: write` permission |
| `HELP_SITE_URL`, `STATUS_PAGE_URL`, `DASHBOARD_BASE_URL` | Replaced by one `PUBLIC_BASE_URL` |
| `HELP_BASE` / Astro `base: "/bot-docs"` | Routes now live at the root of the bot host |
| `web-preview/` | Design reference only; superseded by the implemented templates (archive before deleting) |
| `app/web/tokens.py` as a link/session mechanism | Replaced by `app/web/sessions.py` + `app/web/auth.py` (§3) |

### 2.3 Added

| Add | Purpose |
|---|---|
| `app/web/public.py` | Landing, commands index, cog pages, getting started, status page, robots/sitemap |
| `app/web/templates/public/` | `layout.py`, `landing.py`, `commands.py`, `getting_started.py`, `errors.py` |
| `app/web/assets/tokens.css` | Shared colour/type/space tokens for both faces |
| `app/web/assets/public.css` + `public.js` | Public face only |
| `app/commands_manifest.py` | Builds the manifest **in memory** from the live tree at startup |
| `app/services/accounts.py` | Account lookup, owner bootstrap, creation, status changes, grants |
| `app/services/activity.py` | `record_activity()`, filtering, CSV rows, pruning |
| `app/web/accounts.py` | Owner-only Accounts screens and POST handlers |
| `app/web/activity.py` | Owner-only Activity screen and CSV export |
| `app/web/templates/app/admin_accounts.py` | List, detail and new-account forms |
| `app/web/templates/app/admin_activity.py` | Filter bar, table, detail rows |
| `tests/test_accounts.py` | Gate, roles, grants, lifecycle |
| `tests/test_activity.py` | Event coverage, atomicity, redaction, retention |
| `tests/test_public.py` | Route, payload and render tests |

### 2.4 Route map (final)

| Method | Path | Face | Auth | Notes |
|---|---|---|---|---|
| GET | `/` | public | none | Landing + live status |
| GET | `/commands` | public | none | Full command index, searchable |
| GET | `/commands/{cog}` | public | none | Per-cog page, 404 for unknown |
| GET | `/getting-started` | public | none | Three steps to first post |
| GET | `/status` | public | none | Curated JSON (§9.1) |
| GET | `/health` | public | none | Liveness, never touches SQLite |
| GET | `/robots.txt`, `/sitemap.xml`, `/favicon.svg` | public | none | Static, in-memory |
| GET | `/assets/{hashed}.{css,js,woff2}` | public | none | Immutable, hashed at import |
| GET | `/login` | public | none | "Sign in with Discord" |
| GET | `/auth/callback` | public | state cookie | OAuth code exchange; unknown IDs denied |
| POST | `/logout` | private | session | Deletes the session row |
| GET | `/dashboard` | private | account | Redirects to last granted guild or `/login` |
| GET | `/dashboard/g/{guild_id}` | private | account + grant | Overview |
| GET | `/dashboard/g/{guild_id}/cogs` | private | account + grant | Toggles |
| GET | `/dashboard/g/{guild_id}/settings` | private | account + grant | Config forms |
| GET | `/dashboard/g/{guild_id}/watches` | private | account + grant | Read-only watches |
| GET | `/dashboard/g/{guild_id}/audit` | private | account + grant | Paginated guild audit |
| POST | `/dashboard/g/{guild_id}/cogs/{cog}` | private | account + grant + CSRF | Toggle (`admin`/`owner`) |
| POST | `/dashboard/g/{guild_id}/settings/{key}` | private | account + grant + CSRF | Set one key (`admin`/`owner`) |
| GET | `/dashboard/admin/accounts` | private | `owner` | Account list |
| GET | `/dashboard/admin/accounts/new` | private | `owner` | Create form |
| POST | `/dashboard/admin/accounts` | private | `owner` + CSRF | Create account + grants |
| GET | `/dashboard/admin/accounts/{id}` | private | `owner` | Detail / edit |
| POST | `/dashboard/admin/accounts/{id}` | private | `owner` + CSRF | Name, note, role |
| POST | `/dashboard/admin/accounts/{id}/status` | private | `owner` + CSRF | Suspend / reactivate / revoke |
| POST | `/dashboard/admin/accounts/{id}/guilds` | private | `owner` + CSRF | Grant / revoke guilds |
| GET | `/dashboard/admin/activity` | private | `owner` | Filterable log |
| GET | `/dashboard/admin/activity.csv` | private | `owner` | Export (same filters) |
| GET | `/dashboard/status.json` | private | account | Full snapshot |
| GET | `/dashboard/events` | private | account | SSE, **max 8** concurrent (§9.5) |

Rules:

- Every private HTML route returns **302 to `/login?next=…`** when there is no valid session. Never a 401 HTML page, never an "expired" page.
- Every private JSON/SSE route returns **401 JSON** when unauthenticated.
- A valid session whose account is `suspended` or `revoked` is deleted and redirected to `/login`; it is never honoured.
- `/dashboard` redirects to `sessions.last_guild_id` when it is still granted, otherwise to the first granted guild; with zero grants it renders "No servers assigned yet".
- `/dashboard/admin/*` is owner-only. A non-owner gets **404** (not 403), consistent with guild probing.
- `/register`, `/signup`, `/invite/accept` and every similar path are **404**. There is no registration surface to disable, because none is mounted.
- Old `/dashboard/guild/{id}` and `/dashboard/github` aliases are removed (no external consumer).

### 2.5 Docs data flow

```
cogs load (bot.prepare)
   └─▶ AppBot.manifest = build_manifest(self.tree)      # in memory, one time
          ├─▶ /commands, /commands/{cog}                # server-rendered
          ├─▶ /status                                   # version only
          └─▶ /appardas dump-commands (debug)           # optional file write
```

- `build_manifest(tree)` already exists in `app/commands_export.py`; refactor it to **return** the dict and keep `write_manifest()` only for the debug command and tests.
- Prose still comes from `app/commands_docs.py`; missing prose is a startup **warning** (and a failing test), not a broken deploy.
- The manifest refreshes when `/appardas sync` or `/appardas reload` changes the tree: rebuild `bot.manifest` after either command, and bump `manifest.version` (timestamp) for ETags.
- `app/commands.json` stops being a build input. It may remain as a test fixture; `tools/dump_commands.py --check` stays in CI as a drift test, not as a release gate.

### 2.6 Rendering and caching

| Response | Cache-Control | ETag |
|---|---|---|
| `/` | `public, max-age=0, must-revalidate` | snapshot `checked_at` |
| `/commands*`, `/getting-started` | `public, max-age=0, must-revalidate` | manifest version |
| `/status` | `public, max-age=15, stale-while-revalidate=15` | snapshot `checked_at` |
| `/assets/*` | `public, max-age=31536000, immutable` | hashed name |
| `/dashboard*`, `/login`, `/auth/*` | `no-store` | — |

- Add a ~30-line gzip middleware (`zlib`, text types > 1 KB, `Vary: Accept-Encoding`, level 6). No new dependency.
- Public HTML budget: **≤ 40 KB uncompressed, ≤ 12 KB gzipped** including the 288-tick SVG strip.
- Assets stay in memory, hashed at import (`CSS_NAME`, `JS_NAME` pattern already exists) — extend it to `public.css`, `public.js`, `tokens.css`, `favicon.svg`.

### 2.7 Deployment procedure

1. Point the host's public URL at the bot's existing port (bot-hosting.net exposes one port).
2. Set `PUBLIC_BASE_URL=https://<host>` and restart. Nothing else changes in the panel.
3. Delete the GitHub Pages site; keep the repository's Pages setting disabled to prevent accidental redeploy.
4. If the old `/bot-docs` URL has inbound links, add a **301** at the proxy/host level: `/bot-docs/* → /*`. No Astro required.
5. Keep `.github/workflows/uptime.yml` and `backup.yml`; change uptime to `GET $PUBLIC_BASE_URL/health`.

### 2.8 Acceptance

- [ ] `help-site/` is gone; `grep -r "astro"` and `grep -r "node"` in the repo return nothing deploy-related.
- [ ] One `git push` deploys bot + site + dashboard together.
- [ ] `/commands` contains every leaf in `bot.tree`, verified by test, with zero manual steps.
- [ ] `/` returns 200 with no cookie and no JavaScript.
- [ ] `/status` gzips under 3 KB.
- [ ] No process on the host other than Python.

### 2.9 Rollback

Revert the deploy commit; the Astro directory is deleted, so rollback restores the previous tag. Acceptable: the old docs site is static and can be re-published from the tag if the port is unavailable.

---

## 3. Decision 2 — Always-on dashboard, public face + private face

### 3.1 Why the current model fails the requirement

| Current behaviour | Consequence |
|---|---|
| `mint(kind="link")` on `/dashboard` | Only reachable by asking the bot for a link |
| `consume()` deletes the token on first GET | Refresh after a failed page load burns the link |
| Session token also expires (`ttl = min(600, …)`) | The page dies mid-session |
| Token in every `href`, `form action`, SSE URL and `window.__APPARDAS__` | Survives in browser history, copied links and screenshots; any policy change on referrers or access logs widens the exposure |
| 410 `expired_page` | A dead end with no way back except Discord |
| `DASHBOARD_ENABLED=false` default | The dashboard does not exist on a normal deploy |

The dashboard is not a "dynamic expiring website". It is a **console for a running process**, and its lifetime is the process's lifetime.

### 3.2 New lifecycle

```
process start
  ├─ DB open, cogs load, manifest built
  ├─ HTTP starts            ← site is reachable, Discord still "connecting"
  ├─ Discord login
  └─ on_ready
        │
        ├─ public face: always available while the process lives
        └─ private face: available whenever a valid session cookie is present
                        (sessions are DB rows; they survive restarts and redeploys)
```

| Event | Public `/` | Private `/dashboard` |
|---|---|---|
| Process booting, Discord connecting | 200, verdict `offline` / "connecting" | 302 → `/login` (or 200 with cached session) |
| Discord connected | 200, verdict `ok` or `degraded` | 200 |
| Discord reconnecting | 200, verdict `offline` | 200 (cog actions still work; they write to SQLite) |
| Session idle > 30 days | — | 302 → `/login`, cookie cleared |
| Session absolute age > 90 days | — | 302 → `/login` |
| Process down | host 502/503 | host 502/503 |

There is **no TTL on the dashboard itself**. The only expiry is a real session expiry, and it is handled by a redirect with `next=` preserved.

### 3.3 Discord OAuth2 flow (identity only)

Scopes: `identify`. No `guilds`, no bot scope, no `email`. The server never asks Discord what the user is allowed to do — the account row answers that.

```
GET /login?next=/dashboard/g/123/cogs
  ├─ if session valid → 302 next
  ├─ state = 32 random bytes (urlsafe)
  ├─ store sha256(state) in oauth_states(next_path, expires_at = now + 10 min)
  ├─ set cookie appardas_oauth = state   (HttpOnly, SameSite=Lax, Path=/auth, 10 min)
  └─ 302 https://discord.com/oauth2/authorize
         ?client_id=…&response_type=code
         &redirect_uri=<PUBLIC_BASE_URL>/auth/callback
         &scope=identify&state=<state>

GET /auth/callback?code=…&state=…
  ├─ constant-time compare state with the oauth cookie, then consume the DB row
  ├─ POST https://discord.com/api/oauth2/token   (8 s timeout, aiohttp)
  ├─ GET  /api/users/@me
  ├─ SELECT * FROM accounts WHERE discord_user_id = <id>
  │    ├─ no row            → activity "auth.login.denied_unknown" → 403 "not invited"
  │    ├─ status suspended  → activity "auth.login.denied_suspended" → 403
  │    ├─ status revoked    → activity "auth.login.denied_revoked" → 403
  │    └─ active            → continue
  ├─ insert session row (account_id, discord_user_id)
  ├─ update accounts.last_login_at, last_login_ip
  ├─ activity: "auth.login.success"
  └─ 302 safe_next or /dashboard
```

There is **no `/register` route and no auto-provisioning**. An unknown Discord ID is denied and recorded; it is never turned into an account.

Failure responses (all render the login page with a reason, never a stack trace):

| Case | Message |
|---|---|
| state mismatch / expired | "That sign-in took too long. Try again." |
| token exchange fails | "Discord did not complete the sign-in. Try again." |
| Discord account has no account here | "This Discord account has not been invited. Ask the owner for access." |
| account suspended / revoked | "This account is not active. Ask the owner." |
| signed in but zero guild grants | Sign-in succeeds; the dashboard shows "No servers assigned yet." |
| rate limited | "Too many attempts. Wait a minute." |

`safe_next` must start with `/`, must not start with `//`, must not contain `\`, and is capped at 200 chars.

### 3.4 Sessions

- Cookie value: 32 random bytes, base64url. The DB stores **only `sha256(id)`**.
- The row carries `account_id` and `discord_user_id`; the account row is the authority for role and status.
- Name: `__Host-appardas_session` in production (requires `Secure`, `Path=/`, no `Domain`); `appardas_session` when `COOKIE_SECURE=false` for localhost.
- Flags: `HttpOnly`, `Secure` (prod), `SameSite=Lax`, `Path=/`, `Max-Age=<idle seconds>`.
- Idle timeout **30 days**, absolute **90 days**. `last_seen_at` updates at most once per 60 s per session to keep writes cheap.
- `last_guild_id` updates whenever a guild screen is opened, so `/dashboard` returns to the server you were last working in.
- Rotation: a new session ID is issued on every login; the old row is not reused. No rotation per request (the dashboard is not a bank).
- Every private request joins `sessions → accounts` and rejects a missing, suspended or revoked account **even if the cookie is still valid**.
- Revocation: "Sign out" deletes the row; suspending or revoking an account deletes **all** of its rows immediately.
- Survives restarts and redeploys because it is a SQLite row, not a signed blob.

### 3.5 Authorization

| Actor | Can view | Can mutate |
|---|---|---|
| Anonymous | `/`, `/commands*`, `/getting-started`, `/status`, `/health`, `/login` | nothing |
| Account `viewer` | granted guilds, read-only | nothing |
| Account `admin` | granted guilds | cogs + settings for granted guilds |
| Account `owner` | every guild, Accounts, Activity | everything, including account management |
| Discord user with no account | login denied | nothing |
| Discord user with Manage Server | **nothing extra** | nothing extra |

- `guild_id` from the URL is checked against `account_guilds` on **every** request (owners bypass the grant check). A mismatch is a **404**, not a 403, so guild IDs cannot be probed.
- Role and status come from the account row on every request, so a demotion or suspension applies on the next click — no re-login, no token to expire.
- Mutations call the same `SettingsService` methods as the Discord commands, so locking, validation, caching, events and audit cannot drift.
- **Dashboard access is a grant, not a Discord permission.** Manage Server is never queried and never trusted.

### 3.6 What "active while the bot is online" means concretely

1. `DASHBOARD_ENABLED` is removed. Routes are always mounted.
2. Missing OAuth config is a **startup configuration error** in production, not a silently disabled feature.
3. No `?t=` in any link, form, redirect or SSE URL.
4. No page says "expired". A dead session redirects.
5. The web server starts **before** `bot.start()`, so `/` is live during the handshake and reports `connecting`.
6. `/appardas dashboard` and `/dashboard` reply with `<PUBLIC_BASE_URL>/dashboard` — a plain URL, no secret.
7. There is no registration route. The only way an account exists is an owner action (§6).

### 3.7 Forms, CSRF, audit

- Every session row carries a random `csrf` value, rendered into forms and required on POST.
- Accepted as `csrf` form field or `X-CSRF-Token` header (for `fetch`).
- `SameSite=Lax` is the second layer, not the only one.
- Every mutation writes **two rows in one transaction**:
  - `audit_log` — the guild-facing change (`action`, `target`, `detail.source="web"`, `detail.request_id`);
  - `activity_log` — the accountable record (`account_id`, `role`, `action`, `before`, `after`, `ip`, `user_agent`, `outcome`, `request_id`).
- If the activity row cannot be written, the change rolls back. No silent changes.
- POST success → `303 See Other` back to the screen with `?saved=<key>`; the screen renders a one-line confirmation. No flash store.
- POST failure → 4xx JSON for `fetch`, or the same screen with an inline error for a plain form.

### 3.8 Removed configuration

`DASHBOARD_ENABLED`, `DASHBOARD_SECRET`, `DASHBOARD_LINK_TTL_S`, `DASHBOARD_BASE_URL`, `HELP_SITE_URL`, `STATUS_PAGE_URL`, `STATUS_TOKEN`.

There is no application signing secret any more: session IDs are random and looked up in the DB; OAuth state is random and stored; CSRF is random per session.

> The OAuth scope also shrinks from `identify guilds` to `identify`. The dashboard no longer needs the user's guild list, because access is stored here.

---

## 4. Decision 3 — The public face

### 4.1 Pages

| Page | Job | Primary data |
|---|---|---|
| `/` | Say what appardas is, prove it is alive, route to docs or invite | `StatusService.public_snapshot()` + `heartbeat_series(24)` |
| `/commands` | Every command, searchable, copy-pasteable | `bot.manifest` |
| `/commands/{cog}` | One cog's commands, permissions and failure notes | `bot.manifest` |
| `/getting-started` | Invite → configure → first post | invite URL + real examples |
| `/login` | Sign in, explain that accounts are created by the owner | account lookup presence |

Opening the domain root never redirects to a login wall. `/` is the public home; `/login` is a separate, explicit page reached only from the nav, a dashboard redirect, or `/dashboard`. There is **no registration page** — see §6.

### 4.2 Landing blocks (top to bottom)

| # | Block | Content | Live? |
|---|---|---|---|
| 1 | Hero | One sentence with the 24 h heartbeat strip inline; supporting sentence; one primary action | yes |
| 2 | **Right now** | Verdict, 5 cog rows, 4 readings (gateway, loop lag, RSS, uptime), renewal clock | yes, 30 s poll |
| 3 | **What it does** | One paragraph, five inline interface fragments (one per cog) | no |
| 4 | **Find a command** | A real input filtering the manifest; result rows show usage + example | yes, client-side |
| 5 | **From invite to first post** | Three stages with real commands | no |
| 6 | **Questions** | Six honest answers (open, not an accordion) | no |
| 7 | Close + footer | Invite, commands, status, sign in, version | no |

Requirement mapping:

- **Heartbeat** → hero strip (288 × 5-minute ticks) and a summary line.
- **Cogs active status** → five named rows with a text state, plus `5 / 5 loaded`.
- **Current health** → verdict pill + four readings, with the rule stated in one sentence.

### 4.3 Health contract

Verdict precedence: `offline` > `degraded` > `ok`.

| Condition | Verdict |
|---|---|
| `discord.state != "connected"` | `offline` |
| `db.healthy == false` | `degraded` |
| `runtime.loop_lag_ms > 50` | `degraded` |
| `runtime.mem_rss_mb > 350` | `degraded` |
| a scheduler error in the last 15 min | `degraded` |
| otherwise | `ok` |

Cog state is **runtime**, not per-guild:

| State | Meaning | Public word |
|---|---|---|
| `active` | cog loaded, no fault | `active` |
| `faulted` | import/registration raised | `unavailable` |
| `loading` | not yet attached (boot) | `starting` |

Per-guild on/off is **never** public: it belongs to a server, not to the bot.

Staleness: if the snapshot is older than **90 s**, the page says "last reading HH:MM" and dims the readings; it never presents stale numbers as current.

### 4.4 Command documentation spec

Each command entry renders:

```
/github watch <org-or-repo> [channel] [events]        manage server
Watches a GitHub org or repo and posts grouped updates.
Example   /github watch appardas #dev-releases
          /github watch appardas/bot #dev events:pulls,releases
Note      One request per org per poll; five watches maximum.
```

- Deep link: `/commands/github#github-watch` (slug = command path, non-alphanumerics → `-`).
- Search: client-side filter over path + description + usage; `<form method="get" action="/commands">` with `q=` works without JS, and the full list is always in the DOM (JS only hides rows).
- Permissions are words, not coloured pills: `everyone`, `manage server`, `bot owner`.
- Unknown cog → 404 with the five cog links; never a blank page.

### 4.5 States to design and test

| State | Public behaviour |
|---|---|
| Bot starting | Verdict `offline`, subtitle "connecting to Discord", cog rows `starting` |
| Discord reconnecting | Verdict `offline`, "reconnecting" — do not say "down" |
| DB unhealthy | Verdict `degraded`, DB row flagged, the rest still renders |
| No heartbeats yet (fresh DB) | Strip shows a flat baseline; summary "24 hours, no readings yet"; not an error |
| Just restarted | Pre-start buckets are a flat baseline, never a red gap; summary "readings since 05:40" |
| Snapshot stale | "last reading HH:MM" + a retry link |
| JS disabled | Server-rendered values stay; no polling; docs search falls back to the form |
| 404 | One line, links to `/`, `/commands` |
| 500 | "Something went wrong. The bot log has the rest." + link to `/health` |

### 4.6 Metadata

- Public pages: indexable, canonical to `PUBLIC_BASE_URL`, `<title>` per page, description from the first sentence.
- Private pages: `noindex, nofollow`, `Cache-Control: no-store`.
- `robots.txt`: allow `/`, disallow `/dashboard`, `/login`, `/auth`, `/status`.
- `sitemap.xml`: `/`, `/commands`, `/commands/{cog}` ×5, `/getting-started`.
- Two essential cookies, both disclosed on `/login`: the session cookie, and a 10-minute OAuth `state` cookie that exists only during sign-in. No analytics, no consent banner.

---

## 5. Decision 4 — The private face

### 5.1 Screens

| Screen | Question it answers | Writes |
|---|---|---|
| Overview | Is it healthy, what changed, what is due? | nothing (shortcut toggles) |
| Cogs | Which cogs run, and why is one off? | `cog_state` |
| Settings | What are this server's prefix, channels and timezone? | 5 config keys |
| Watches | What is being watched, and when did it last poll? | nothing (Discord only) |
| Audit | What changed in this server, and who did it? | nothing |
| Accounts *(owner)* | Who can sign in, with which role and which guilds? | `accounts`, `account_guilds` |
| Activity *(owner)* | Which account did what, from where, and did it succeed? | nothing (append-only) |

Every screen renders read-only for a `viewer`: controls are disabled with the line "Your account can view this server but not change it", and the server refuses any forged POST with 403 + `authz.denied`.

### 5.2 Overview

- Title row: guild name, verdict, `since`, version; one primary action ("Open in Discord").
- `Right now` strip: same four readings as the public page, plus DB size and last write.
- Heartbeat: 288 ticks, full width, 40 px tall, with the gap summary.
- Cog strip: five rows, state word, last change (actor + time), toggle for the two unlockable cogs.
- Renewal clock: 4 segments, countdown, "last renewed".
- Recent audit: 6 rows, link to the full log.

### 5.3 Cogs

- One row per cog: name, description, state, control, reason, last change.
- Locked cogs (`status`, `settings`, `utility`) render a disabled control and the sentence "This is how you find out something is wrong."
- Toggle flow: confirm reason optional → POST → row updates → the guild audit row and the Activity row appear.
- Only `admin` and `owner` see an enabled toggle; `viewer` sees the state word and a disabled control.
- Empty/never-toggled state: "Never changed. Default is on."

### 5.4 Settings

Five rows, one form each. Validation is server-side **and** mirrored in the UI.

| Key | Control | Validation | Example |
|---|---|---|---|
| `prefix` | text, empty = mention-only | `validate_prefix`: 1–16 visible characters, no spaces or control characters, cannot start with `/`, no `@`/`#` mention patterns | `!` → preview `!help` |
| `announce_channel` | channel select (text channels) + "clear" | must be a channel in this guild | `#announcements` |
| `github_channel` | channel select | same | `#dev-releases` |
| `mod_log` | channel select | same | `#mod-log` |
| `timezone` | select + custom IANA input | `ZoneInfo` parse | `Asia/Manila` |

- Prefix row shows a live preview: `!help` vs `@appardas help`.
- Channel options come from `guild.text_channels` in the cache; if the cache is cold, the select is disabled with "Reconnect to Discord to list channels" and the value can still be cleared.
- Saving one key never rewrites the others (`json_set` on the existing row, as `SettingsService.set_setting` already does).
- Success line: "Prefix saved. New prefix: `!`." with `role="status"`.

### 5.5 Watches (read-only in 0.5.0)

Columns: target, type, channel, events, health, last poll, last delivery, error. Empty: "No watches. Run `/github watch appardas #releases`." Footer: "Watches are added and removed in Discord so the channel and events stay explicit."

### 5.6 Audit

- This is the **guild-facing** log: what changed in this server, read from `audit_log`.
- Columns: time, actor, action, target, source, detail.
- Filters: `?action=` (select) and `?limit=` (50/100/200).
- Actor renders as `@name` when the user is cached, otherwise `user 123…`.
- Empty: "Nothing recorded yet."
- The accountable, cross-guild view (which account, from which IP, with before/after) lives in Activity (§7).

### 5.7 Auth screens

| Screen | Content |
|---|---|
| `/login` | Wordmark, "Sign in with Discord", one line: "Accounts are created by the bot owner. Signing in only proves who you are." |
| Denied — not invited | "This Discord account has not been invited. Ask the owner for access." + invite link (for the server, not for the dashboard) |
| Denied — suspended/revoked | "This account is not active. Ask the owner." |
| No servers | Signed in, zero grants: "No servers assigned yet. Ask the owner to grant you a server." |
| Signed out | "Signed out." + sign-in link |
| Config error (dev) | "OAuth is not configured. Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET." |

There is no "Request access" form. The only path is the owner's Accounts screen.

### 5.8 Permission matrix

| Action | `viewer` | `admin` | `owner` |
|---|---|---|---|
| View guild screens (granted guilds) | yes | yes | yes (all guilds) |
| Toggle `github`, `dev` | no | yes | yes |
| Toggle locked cogs | no | no | no (by design) |
| Set prefix/channels/timezone | no | yes | yes |
| View guild audit | yes | yes | yes |
| View another guild | no | no | yes |
| Create / edit / suspend accounts | no | no | yes |
| View Activity and export it | no | no | yes |
| Renewal / reload / shutdown | no | no | Discord only |

### 5.9 Progressive enhancement

- Every form works with a normal POST + 303 (no JS).
- With JS: `fetch` + `Accept: application/json`, inline result, no reload for toggles; the SSE stream refreshes rows changed by another session.
- No optimistic state: the control shows a pending state until the server answers, then the server's value.

---

## 6. Decision 5 — Accounts are granted, never registered

### 6.1 Principle

There is **no sign-up**. A Discord user cannot create an account, request one, or turn a successful OAuth login into access. The owner creates an account ahead of time, keyed to a Discord user ID, and assigns:

- a **role** — `owner`, `admin` or `viewer`;
- **guild grants** — the servers that account may open.

Discord OAuth2 is only proof of identity: "this browser belongs to Discord user 123…". It never answers "what may this person do". That answer comes from the `accounts` and `account_guilds` rows, which only the owner writes.

Three consequences, stated plainly because they drive the code:

1. A valid Discord login with no account row is a **403**, not a new account.
2. Manage Server, Administrator and server ownership in Discord grant **nothing** here.
3. Removing access is instant: suspend the account, remove a guild grant, or revoke the account. No token to wait out.

### 6.2 Data model

```sql
CREATE TABLE IF NOT EXISTS accounts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  discord_user_id  INTEGER NOT NULL UNIQUE,        -- snowflake, identity key
  name             TEXT    NOT NULL,               -- label the owner sees; not unique
  role             TEXT    NOT NULL DEFAULT 'viewer'
                     CHECK (role IN ('owner', 'admin', 'viewer')),
  status           TEXT    NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'suspended', 'revoked')),
  is_system        INTEGER NOT NULL DEFAULT 0,     -- 1 = seeded from BOT_OWNER_IDS
  note             TEXT,                           -- owner-only memo ("who is this?")
  created_by       INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  created_at       TEXT    NOT NULL,
  updated_at       TEXT    NOT NULL,
  last_login_at    TEXT,
  last_login_ip    TEXT
);

CREATE INDEX IF NOT EXISTS idx_accounts_status  ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_accounts_discord ON accounts(discord_user_id);

CREATE TABLE IF NOT EXISTS account_guilds (
  account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  guild_id    INTEGER NOT NULL REFERENCES guilds(id)   ON DELETE CASCADE,
  granted_by  INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  granted_at  TEXT    NOT NULL,
  PRIMARY KEY (account_id, guild_id)
);

CREATE INDEX IF NOT EXISTS idx_account_guilds_guild ON account_guilds(guild_id);
```

`sessions` changes shape at the same time (details in §9.3):

| Before | After |
|---|---|
| `guilds_json` (the OAuth guild list) | **removed** — access comes from `account_guilds` |
| `guilds_at` (5-minute refresh stamp) | **removed** — nothing to refresh |
| `user_id` | `discord_user_id` + `account_id` (FK, `ON DELETE CASCADE`) |

Why `account_guilds` references `guilds(id)`: `SettingsService.forget_guild()` keeps the guild row and sets `left_at`, so a grant survives a temporary removal and is flagged in the UI; a hard-deleted guild row removes its grants cleanly. Foreign keys are already enforced (`PRAGMA foreign_keys = ON`).

`accounts.discord_user_id` is `INTEGER`, not `TEXT`. Discord snowflakes fit in SQLite's signed 64-bit integer; storing them as text invites `"0123"`-style mismatches that silently deny a real user.

### 6.3 Owner bootstrap

`BOT_OWNER_IDS` is already required config and is the source of truth for ownership.

At startup, after migrations:

1. For each ID in `BOT_OWNER_IDS`: upsert an `accounts` row with `role='owner'`, `status='active'`, `is_system=1`, `created_by=NULL`. Keep the existing `name`, `note` and `last_login_*`.
2. Any `accounts` row with `role='owner'` whose ID is **not** in `BOT_OWNER_IDS` is demoted to `viewer`, `is_system` is cleared, and its sessions are deleted. Removing an ID from the env therefore removes owner access on the next boot instead of leaving a forgotten owner behind; the owner can promote the account deliberately if it should keep working.
3. A `BOT_OWNER_IDS` entry that maps to a suspended row is reactivated — the env is authoritative.

This resolves the chicken-and-egg problem without a setup wizard and without a second secret. The owner can never be locked out by a UI mistake, because the owner row is not editable from the UI:

- no suspend, revoke, role change or delete for `is_system=1`;
- the Accounts list renders it as `owner · managed by BOT_OWNER_IDS`;
- the note and display name are still editable (cosmetic only).

If `BOT_OWNER_IDS` is empty, startup already fails in `config.py`; that check stays. With a single ID in it — the expected setup — the owner is the only person who can create, edit or revoke accounts, which is exactly the requirement.

### 6.4 Creating an account (owner only)

Route: `GET /dashboard/admin/accounts/new` renders the form; `POST /dashboard/admin/accounts` creates the account and redirects to its detail page.

| Field | Rule |
|---|---|
| Discord user ID | Required. 17–20 digits, no spaces, no `@`, not already in `accounts`. This is the identity key. |
| Display name | Required, 1–64 visible chars. Prefilled from Discord when the lookup succeeds. Editable — it is the owner's label, not a Discord sync. |
| Role | `admin` or `viewer`. `owner` is **not** offered in the UI and is rejected server-side if posted. |
| Guild grants | Multi-select of guilds the bot is currently in (`left_at IS NULL`), grouped alphabetically. Zero is allowed (pre-provision now, grant later) but shows a warning. |
| Note | Optional, ≤ 280 chars. "Who is this, why do they have access?" |

Flow:

1. Owner pastes the Discord ID and presses **Look up** (or submits directly).
2. `bot.get_user(id)` first; on a miss, `bot.fetch_user(id)` with an 8 s timeout and a per-owner rate limit of **10 lookups/minute**.
3. On success: prefill name and avatar, show `@handle · created 2019` so the owner can confirm they have the right person.
4. On `404`: "Discord does not know this ID." The form is not saved.
5. On network failure: allow **Save without verification** (explicit button), flagged in the activity detail as `verified: false`. Identity is still enforced at login by the ID itself.
6. POST validates everything again server-side. The lookup is a convenience, never the authority.
7. Insert `accounts`; insert one `account_guilds` row per selected guild; write `account.create` to `activity_log`.
8. Show the new account's detail page with: "Share `<PUBLIC_BASE_URL>/login` with them. They sign in with Discord; no invite code is needed."

Validation failures render the form with values preserved and a single inline error per field. No partial writes: account + grants are inserted in one transaction.

Duplicate handling: `UNIQUE(discord_user_id)` is the real guard. The pre-check gives a friendly message; the constraint wins a race.

### 6.5 Roles and grants

| Role | Scope | Can view | Can mutate | Can manage accounts |
|---|---|---|---|---|
| `owner` | every guild the bot is in | everything | everything | yes |
| `admin` | granted guilds only | overview, cogs, settings, watches, audit | cog toggles + settings | no |
| `viewer` | granted guilds only | overview, cogs, settings, watches, audit | nothing | no |

Rules:

- **One role per account**, global. Per-guild roles are deliberately deferred; a second role column would double every permission test for a use case nobody has asked for yet.
- **Owner bypasses guild grants.** The owner sees every guild in the bot cache, including guilds joined after the account was created.
- **Admin and viewer require a grant** for the guild in the URL. No grant → **404**, so guild IDs cannot be enumerated.
- **A grant is checked against the live bot cache too.** A grant for a guild the bot has left renders as `appardas left this server` and grants no access.
- **Locked cogs stay locked for everyone.** `owner` means "may administer the dashboard", not "may break the bot". Locked cogs remain Discord-owner-only as today.
- A viewer sees the fields disabled and no submit button, and the server rejects a forged POST with `403` + `authz.denied`. Hiding the control is UX; the check is the control.

### 6.6 Lifecycle

| Status | Login | Sessions | Guild access | Reversible | Typical reason |
|---|---|---|---|---|---|
| `active` | allowed | valid | per grants | — | normal |
| `suspended` | denied | **all deleted** | none | yes (`reactivate`) | temporary pause, suspected compromise |
| `revoked` | denied | **all deleted** | none | yes, but a new account is preferred | person left, access withdrawn |

Transitions, all owner-only, all audited:

| Action | Route | Effect |
|---|---|---|
| Suspend | `POST /dashboard/admin/accounts/{id}/status` with `status=suspended` | set status, delete sessions, `account.suspend` |
| Reactivate | same, `status=active` | set status, `account.reactivate` |
| Revoke | same, `status=revoked` | set status, delete sessions, keep grants for history, `account.revoke` |
| Change role | `POST /dashboard/admin/accounts/{id}` | `account.update` with before/after; sessions stay (role is read per request) |
| Grant guild | `POST /dashboard/admin/accounts/{id}/guilds` | insert row, `account.guild.grant` |
| Revoke guild | same, with `remove=<guild_id>` | delete row, `account.guild.revoke`; the target's next request to that guild 404s |

Revoke does **not** delete the row. The account is historical evidence: its activity entries must keep a resolvable `account_id`, and "revoked" is more useful than a hole. Hard delete exists only in SQL, for a genuine data-removal request; grants cascade, `activity_log` keeps its denormalised label.

There is no "last owner" guard to write, because `is_system` owner rows cannot be suspended, revoked or demoted. A non-system `owner` cannot be created in the first place.

### 6.7 Edge cases

| Case | Behaviour |
|---|---|
| Owner pastes a user ID that is not a snowflake | Rejected before any API call: "That does not look like a Discord user ID." |
| Duplicate Discord ID | 409-style inline error on the form, linking to the existing account |
| Account exists but is suspended | Creating again is rejected; the UI offers **Reactivate** instead |
| Bot leaves a granted guild | Grant stays, shown greyed with `left this server`; access denied until the bot returns |
| Bot rejoins | Grant becomes live again automatically (same `guild_id`) |
| Guild is hard-deleted from `guilds` | `ON DELETE CASCADE` removes the grant |
| Account has zero grants | Login works; `/dashboard` shows "No servers assigned yet" |
| Owner's own row | Not editable beyond name/note; no status buttons |
| Two owners suspend each other | Impossible: owner rows are system-managed |
| Discord ID recycled / account deleted | Access requires a valid OAuth login for that ID; a deleted Discord account cannot log in. The row remains for the log. |
| Owner edits role while target is signed in | Applies on the next request (role is joined per request). No stale session privilege. |
| Owner revokes a guild while target is on that screen | Next request 404s and redirects to the first remaining grant |

### 6.8 Accounts screen

`GET /dashboard/admin/accounts` — owner only, `noindex`.

- Header: "Accounts" + count, primary action **New account**.
- Table: name + `@handle` (or `user 123…`), role chip, status chip, guild count, last sign-in (relative + absolute tooltip), created by, row action **Open**.
- Filters: `?q=` (name or ID), `?role=`, `?status=`. Server-rendered, no JS required.
- Empty: "No accounts yet. The owner account from `BOT_OWNER_IDS` is the only way in."
- Sort: status (`active` first), then role (`owner`, `admin`, `viewer`), then name.

`GET /dashboard/admin/accounts/{id}`:

- Identity card: avatar (from bot cache if available, else initials), name, Discord ID with a copy button, role, status, note, created by/at, last sign-in + IP.
- **Access** card: guild checkboxes with live search, `Save grants` (one POST diffing added/removed).
- **Role** card: radio `admin`/`viewer`, `Save role`; hidden for `is_system`.
- **Danger** card: Suspend / Reactivate / Revoke with a confirmation that states exactly what happens ("Deletes 2 active sessions. They will be signed out immediately."). Hidden for `is_system`.
- **Recent activity** card: last 20 `activity_log` rows for this account, linking to the filtered Activity screen.

All mutations are normal POSTs with CSRF and 303 back to the same page with `?saved=<key>`.

### 6.9 Deliberately absent

| Not built | Why |
|---|---|
| `/register`, `/signup`, "request access" | The requirement is closed registration. A request form is still a registration flow. |
| Passwords, password reset, MFA | Discord is the identity provider; a second credential adds a second way to be phished. |
| Email invites / invite tokens | The account is the invite. The owner shares the login URL out of band. |
| Self-service guild selection | That is Discord's permission model, which this decision explicitly stops trusting. |
| Per-guild roles | Deferred until a real need appears. |
| Account deletion UI | Revoke preserves the audit trail. |

---

## 7. Decision 6 — The activity log

### 7.1 Principle

The owner asked for one thing: **"see whose accounts perform the changes."** That means every authenticated write, every authentication outcome, and every account-management action must leave a row that answers:

- **who** — `account_id`, the label at the time, Discord ID, role;
- **what** — action, target, before, after;
- **where** — guild ID, IP, user agent;
- **result** — `ok`, `denied` or `error`;
- **when** — UTC timestamp, plus a `request_id` that ties the whole request together.

Two logs, two audiences, one transaction:

| Log | Audience | Answers | Retention |
|---|---|---|---|
| `audit_log` (exists) | server members via `/settings audit` and the guild Audit screen | "what changed in **this server**" | `AUDIT_RETENTION_DAYS` (90) |
| `activity_log` (new) | the owner, via the Activity screen | "which **account** did what, from where, and did it work" | `ACTIVITY_RETENTION_DAYS` (180) |

They are not merged. `audit_log` is guild-scoped and already consumed by Discord commands; bolting IP addresses and login events onto it would leak operator data into a server-visible screen. `activity_log` is not guild-scoped and must survive a guild leaving. Mutations write both, correlated by `request_id`.

### 7.2 What is logged

| Event | Outcome | Captured |
|---|---|---|
| `auth.login.success` | `ok` | account, IP, UA, Discord ID |
| `auth.login.denied_unknown` | `denied` | Discord ID from OAuth, IP, UA — **no account row** |
| `auth.login.denied_suspended` | `denied` | account, IP, UA |
| `auth.login.denied_revoked` | `denied` | account, IP, UA |
| `auth.login.state_mismatch` | `denied` | IP, UA, reason |
| `auth.logout` | `ok` | account, IP, UA |
| `auth.session.expired` | `denied` | account if resolvable, path |
| `authz.denied` | `denied` | account, role, attempted guild/action, path |
| `account.create` | `ok` | target Discord ID, role, grants, `verified` |
| `account.update` | `ok` | before/after `{name, role, note}` |
| `account.suspend` | `ok` | before/after status, sessions deleted count |
| `account.reactivate` | `ok` | before/after status |
| `account.revoke` | `ok` | before/after status, sessions deleted count |
| `account.guild.grant` | `ok` | guild ID |
| `account.guild.revoke` | `ok` | guild ID |
| `cog.toggle` | `ok` / `error` | guild, cog, before/after enabled |
| `config.set` | `ok` / `error` | guild, key, before/after value |
| `settings.import` (if it ever ships) | `ok` / `error` | summary only |

**Not logged:** page views, static assets, `/status`, `/health`, SSE reconnects, favicon requests, session-id values, CSRF tokens, OAuth codes, cookies, message content, full user agents (truncated to 200 chars), and any value of a secret config key (there are none in `CONFIG_KEYS`; the rule still holds).

Reads are not logged. A log that records every page view is a privacy problem and grows faster than it is useful. The questions the owner actually has — "who changed the prefix?", "who turned off github?", "who signed in from that IP?" — are all writes or logins.

### 7.3 Table

```sql
CREATE TABLE IF NOT EXISTS activity_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id      TEXT    NOT NULL,               -- 16 hex chars, one per HTTP request
  account_id      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  discord_user_id INTEGER,                        -- snapshot; survives account deletion
  account_label   TEXT,                           -- accounts.name at write time
  role            TEXT,                           -- snapshot
  action          TEXT    NOT NULL,               -- auth.login.success, cog.toggle, …
  outcome         TEXT    NOT NULL
                    CHECK (outcome IN ('ok', 'denied', 'error')),
  guild_id        INTEGER,                        -- NULL for auth/account events
  target          TEXT,                           -- cog name, config key, guild id, account id
  before_json     TEXT,                           -- JSON or NULL
  after_json      TEXT,                           -- JSON or NULL
  ip              TEXT,                           -- remote addr, proxy-aware
  user_agent      TEXT,                           -- truncated to 200 chars
  detail          TEXT,                           -- JSON: reason, path, verified, counts…
  created_at      TEXT    NOT NULL                -- UTC ISO-8601, second precision
);

CREATE INDEX IF NOT EXISTS idx_activity_created  ON activity_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_account  ON activity_log(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_guild    ON activity_log(guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_action   ON activity_log(action, created_at DESC);
```

Design notes:

- **Append-only.** No `UPDATE`, no `DELETE` from application code. The only delete is the retention prune, by age.
- **`account_label` is denormalised on purpose.** Renaming or revoking an account must not rewrite history. The row says who they were when they acted.
- **`account_id` is nullable** because denied logins have no account, and because a hard-deleted account sets it to `NULL`. The `discord_user_id` and label still identify the actor.
- **`before_json` / `after_json`** store only the changed fields, not whole rows: `{"prefix": "!"}` → `{"prefix": "?"}`. This keeps rows small and diffs readable.
- **`detail`** is for context that is not a diff: `{"reason": "viewer_write", "path": "/dashboard/g/…/cogs/github"}`, `{"verified": false}`, `{"sessions_deleted": 2}`.
- `created_at` is UTC ISO-8601, the same `iso()` helper the rest of the schema uses.

### 7.4 Write path

One helper, used by every route that logs:

```python
async def record_activity(
    *,
    request: web.Request,
    action: str,
    outcome: str = "ok",
    account: Account | None = None,
    guild_id: int | None = None,
    target: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    detail: dict | None = None,
    conn: sqlite3.Connection | None = None,   # required inside a transaction
) -> None: ...
```

It pulls `request_id`, IP (`request.remote`, or `X-Forwarded-For` only when `TRUST_PROXY=true`), user agent, and the account snapshot from the request context. It never accepts a raw cookie or token.

**Atomicity.** `Database` currently commits per `execute()`. Add:

```python
@asynccontextmanager
async def transaction(self):
    async with self._lock:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
```

Then a mutation does:

```python
async with db.transaction():
    await settings.set_cog_state(guild_id, cog, enabled, actor_id=..., source="web", conn=conn)
    await record_activity(request=request, account=account, action="cog.toggle",
                          guild_id=guild_id, target=cog,
                          before={"enabled": was}, after={"enabled": enabled}, conn=conn)
```

A middleware assigns `request["request_id"]` once per request; `record_activity()` reads it rather than accepting one, so two call sites cannot disagree.

`SettingsService` must accept an optional connection (or a `db.transaction()`-scoped mode) so its `audit_log` insert joins the same transaction. If the activity insert fails, the toggle rolls back — the dashboard never shows a change the log cannot explain. This is the one place where "log it or don't do it" is worth the extra plumbing.

The transaction owns the `Database` lock, so **no code inside it may call `db.execute()`** — that would deadlock on the same lock. The service methods take the connection explicitly, and `record_activity()` takes the same connection; neither reaches for the global one. If that constraint is not honoured, the transaction helper is worse than no helper.

**Correlation.** `audit_log.detail` gains `"request_id": "<same value>"` and `"source": "web"`. The guild Audit screen can therefore point at the exact Activity row, and the Activity screen shows the guild audit sibling when one exists.

**Auth events** follow the same rule at a smaller scale: the session insert and the `auth.login.success` row share a transaction. If the row cannot be written, no session is issued and the login page says "Could not complete sign-in. Try again." A denied login still writes its row; if that write fails, the denial stands (fail closed) and the error is logged to stderr.

**Failure policy.** If the transaction cannot commit, the route returns 503 with "The change was not saved." A 500 with a half-applied change is worse than a retry.

**Logging the logger's failure.** If `activity_log` itself is unwritable (disk full, corruption), the mutation fails. That is deliberate: an unattributable change is not allowed. Startup already fails on an unwritable database.

### 7.5 Activity screen

`GET /dashboard/admin/activity` — owner only, `noindex`.

| Control | Values |
|---|---|
| Account | select of all accounts (including revoked), plus `anonymous` |
| Action | select, grouped: Auth / Accounts / Guild |
| Outcome | `any`, `ok`, `denied`, `error` |
| Guild | select of known guilds + `global` |
| From / To | `<input type="date">`, interpreted as UTC day bounds |
| Limit | 50 / 100 / 200, default 50 |

Table columns: **time** (relative, absolute on hover), **account** (label + role chip), **action**, **guild**, **target**, **outcome** chip, **IP** (full, owner only), **details**.

- `details` is a `<details>` element containing pretty-printed `before_json` → `after_json` and `detail`, escaped.
- Denied rows are visually distinct (a left rule in the danger colour, not a red row background).
- Pagination: `?before=<id>` cursor, "Load older" link; no page numbers, because the log only grows at the head.
- Empty: "No activity matches these filters." With no filters: "Nothing logged yet."
- **Export CSV**: `GET /dashboard/admin/activity.csv` with the same query string, owner only, `Content-Disposition: attachment`, streams rows in batches of 500, hard-capped at 50,000 rows, `Cache-Control: no-store`. Columns are the same as the table plus the raw JSON fields.
- **Account detail** embeds the last 20 rows for that account with a link to the filtered view.

The screen is server-rendered. No SSE, no auto-refresh: an audit log that changes under the cursor is hostile. A **Refresh** link sits in the header.

### 7.6 Retention, redaction, integrity

- **Retention:** `ACTIVITY_RETENTION_DAYS=180`. The existing daily prune loop adds `DELETE FROM activity_log WHERE created_at < <cutoff>`. One statement, indexed by `created_at`.
- **Redaction:** a denylist of keys is stripped before serialisation — `password`, `token`, `secret`, `cookie`, `authorization`, `code`, `csrf`, `session`. Values are truncated to 500 chars; the whole `detail` JSON is capped at 4 KB. If a value exceeds the cap, it is replaced with `{"truncated": true, "bytes": n}`.
- **Integrity:** append-only plus a `request_id` is enough for this scale. A hash chain (`prev_hash`/`row_hash`) is noted as a future option, not built: anyone who can write the SQLite file can rewrite the chain too, so it would add ceremony without adding a trust boundary. Real tamper-evidence would require shipping rows off-box, which is out of scope.
- **Backups:** the log lives in the same SQLite file as everything else, so the existing backup story covers it. Document that deleting `data/appardas.db` deletes the audit trail.
- **Privacy:** IP and user agent are operator data, visible only to the owner, never rendered on a public page, never sent to Discord. The login page discloses that sign-ins are recorded.

### 7.7 Sample rows

```
2026-10-23T09:14:02Z  request 4f2a9c1e  account 1 (owner, sebastian)  auth.login.success       ok
2026-10-23T09:15:11Z  request 91b0d7aa  account 1 (owner, sebastian)  config.set  guild 123  target prefix
                                         before {"prefix":"!"}  after {"prefix":"?"}  ip 203.0.113.7
2026-10-23T09:16:40Z  request c3e81b02  account 4 (viewer, ana)      authz.denied  guild 123
                                         detail {"reason":"viewer_write","path":"/dashboard/g/123/cogs/github"}
2026-10-23T09:17:05Z  request 7d1f0c33  account — (anonymous)         auth.login.denied_unknown
                                         detail {"discord_user_id":"456…","reason":"no_account"}  ip 198.51.100.9
```

Read the third row as: Ana is a real, signed-in viewer who tried to POST a cog toggle. The dashboard refused it, and the owner can see the attempt. Read the fourth as: someone signed in with Discord successfully but has no account — the exact case that must never auto-provision.


## 8. UI and UX direction (`taste-skill`)

Permission was granted for this revision. The direction was dealt with (`<skill>` = `~/.agents/skills/design-taste-frontend`):

```bash
node <skill>/scripts/draw.mjs \
  --sector dev-tool \
  --brief "appardas: a small dependable Discord bot, five cogs, one process, live public status" \
  --sections features,showcase,how-it-works,stats,faq,cta,footer \
  --keep palette,type --direction sentence-as-layout
# seed 168555587

node <skill>/scripts/draw.mjs --app --kind devtools --theme dark \
  --screens overview,list,settings \
  --brief "appardas dashboard: cogs, settings, GitHub watches, audit for a Discord bot" \
  --keep palette,type --out .taste-skill/app
# True-black flat · seed 2001075905
```

Both sheets are saved in `.taste-skill/direction.json` and `.taste-skill/app/direction.json`; reprint with `--show`.

### 8.1 The idea

> **On this page, the bot's own 24-hour heartbeat becomes a word in the headline,
> and every claim below is answered by a live reading.**

The competitor test: put a generic Discord bot in the sentence and it still reads; only appardas can fill that slot with its own 24-hour record. The page is not a brochure about a bot — it is the bot's operating record, written in the same process that answers commands.

**The one live thing:** the heartbeat strip inside the sentence, plus the command index that filters as you type. Both use the product's real content, both work on a phone, both have a server-rendered fallback.

### 8.2 Deviations from the dealt sheets (and why)

| Slot | Dealt | Taken | Reason |
|---|---|---|---|
| Hero media | A photograph strip | The live heartbeat strip (288 ticks, inline SVG) | The subject is software and has no photographs; the skill's rule is to show the product's own interface. It is also the strongest proof of the claim. |
| Imagery | Sky/space photographs | Four interface fragments (`data-ui`) + zero stock photos | Any stock photo of a laptop would be a lie about this product. The page's pictures are its own screens. |
| Palette | — | Kept brand: amber `#d97706`, stone near-black, ink `#f5f5f4` | Existing identity (README §6). |
| Radius | 14 px inline frame, 8/6 app cards | **4 px everywhere** | Brand constraint; one radius on both faces. |
| App primary | Inversion (white pill) | **Amber fill, near-black label** | Brand constraint and cross-face consistency: one amber action per screen, never on nav, tabs or links. |
| Dashboard rail | 56 px icon rail (current) | 224 px labelled sidebar | The app study's clearest finding: an icon rail where people need labels. |

### 8.3 Tokens (one source: `app/web/assets/tokens.css`)

```css
:root{
  /* colour — roles, not names */
  --ground:#0c0a09;        /* stone-950, both faces */
  --panel:#171412;         /* one step up */
  --panel-2:#1f1b18;       /* nested panel / table header */
  --ink:#f5f5f4;
  --muted:#a8a29e;
  --faint:#948d87;         /* 13px meta only, ≥ 4.5:1 on --ground */
  --line:#292524;          /* structural rules only (tables, inputs) */
  --accent:#d97706;        /* live data + the one primary action */
  --accent-ink:#1c1005;    /* text on amber */
  --bad:#e5566f;           /* faulted / destructive */
  --focus:#d97706;

  /* type — Public Sans, five steps */
  --t-display:clamp(40px,6.6vw,92px);
  --t-h2:clamp(26px,3vw,40px);
  --t-lead:clamp(18px,1.6vw,22px);
  --t-body:16px;
  --t-small:13px;

  /* app face */
  --t-app-h1:22px;
  --t-app-value:20px;
  --t-app:13px;

  /* space and shape */
  --gutter:clamp(16px,4vw,32px);
  --section:clamp(96px,12vw,180px);
  --r:4px;
  --control-h:36px;
  --hit:44px;
}
```

Rules:

- **Amber means live, or it is the one action.** Never on nav, tabs, table headers or ordinary links. Ordinary links are ink with an underline.
- One ground, one ink, one accent. Surfaces separate by a tone step (`--panel`), not by shadows.
- No gradients, no glows, no glass, no purple, no Discord blurple.
- Monospace only inside `<pre><code>`; everything else Public Sans.
- Two faces at most — here, one.

### 8.4 Public page blueprint

**Hero (desktop ≥ 1024 px)**

```
┌──────────────────────────────────────────────────────────────┐
│ appardas                            Commands  Status  Sign in│  15px, one row, 32px inset
│                                                              │
│                                                              │
│   A Discord bot, ▐████░██▌███░███▌ 24h heartbeat ▌, no gaps. │  one line, clamp(40,6.6vw,92)
│                                                              │  strip: 1 cap-height tall, 45–50vw
│   Five cogs in one process: GitHub watches, announcements,   │  18–22px, max 52ch, 40px below
│   nudges, settings and status. It runs on 512 MB.            │
│                                                              │
│   [ Read the commands ]      Add to Discord · Sign in        │  one amber button
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

- Sentence: **"A Discord bot, [strip], no gaps."** — 4 words + the strip where "24 hours" would be.
- The strip is `role="img"` with the summary as `aria-label`; ticks are not focusable.
- The strip is server-rendered SVG; JS only replaces it when the tick string changes.
- No eyebrow, no pill badge, no logo row, no scroll cue.
- Reveal: the strip wipes in once (`clip-path` 400 ms) and the newest tick is amber. Reduced motion: no wipe.

**Hero (phone ≤ 480 px)**

```
appardas                 Sign in
A Discord bot,
[ ████████ strip ████████ ]
no gaps.
Five cogs in one process…
[ Read the commands ]
Add to Discord
```

- The strip drops to its own line at full width (2:1), 16 px margins.
- Headline at 11.5vw, max two lines either side of the strip.
- One action, full width; the invite stays a text link.

**Section geometry**

| Section | Composition |
|---|---|
| Right now | `h2` left; verdict + 4 readings in one row of type (no KPI cards); five cog rows below, 48 px pitch; renewal clock at the right on desktop, below on phone |
| What it does | One statement paragraph at `--t-h2`, 35–50 words, with five inline `data-ui` fragments 1 em tall (24–40 px wide) — never a card grid |
| Find a command | A real input at 56–72 px, flush left, no border; results below at true interface size; three example chips as plain text buttons; rest of the field empty |
| From invite → first post | Three stages in a horizontal rule-of-three with real command lines; on phone, vertical with 44 px step marks |
| Questions | Six rows: question in the left six columns at 26–32 px, answer in the right five at 16 px, open, 72 px between rows; heading is the first row in muted ink |
| Close | Full-width field, "Put it in your server." at 6–8vw, one amber button, three text links, cropped wordmark at the foot |

No section repeats a neighbour's layout. No thin decorative rules; only table rules and input borders.

**Motion (required, restrained)**

| Move | Detail |
|---|---|
| Signature | Heartbeat strip wipe on first paint (400 ms), newest tick amber |
| Section entrance | Heading → text → figure, 60–90 ms stagger, 16 px rise, once |
| Command search | Result rows crossfade 120 ms; count updates in a `role="status"` |
| Controls | 1 px press offset, background step on hover, 2 px amber focus ring |
| Moving data | Poll every 30 s; values change without layout shift (tabular figures) |
| Reduced motion | All transforms removed; opacity only; polling still runs |

### 8.5 Dashboard app sheet (True-black flat, adapted)

- **Shell:** 224 px labelled sidebar on `--ground`, separated by one `--line` rule; 56 px top bar carrying the guild switcher, verdict, "Open in Discord"; content gutter 24 px, max 1200 px.
- **Nav:** Overview · Cogs · Settings · Watches · Audit, 34 px pitch, 14 px labels, active = ink label on a `--panel` fill. No icons in tinted squares, no coloured pills, no amber on nav.
- **Owner nav:** a separated `Admin` group below a hairline — Accounts · Activity. Visible only to `owner`; the group label is 11 px uppercase muted, not a link. It is a group, not a second rail.
- **Admin tables:** same table rules as Audit. Status is a word plus a 6 px dot; `suspended`/`revoked` use `--bad` for the word only, never a filled row. `denied` activity rows get a 2 px `--bad` left rule on the first cell, not a red background.
- **Elevation:** `--panel` on `--ground`, one step; `--panel-2` for nested tables. No shadows at rest.
- **KPIs are type, not tiles:** label 13 px muted, value 20 px 600 tabular, one line of context 13 px.
- **Tables:** 12–13 px sentence-case headers, no fill, rows 48 px, numbers right-aligned, one status word per row, no zebra.
- **Forms:** label above control, 36 px control, 4 px radius, `--line` border, focus ring amber; errors 13 px `--bad` under the field.
- **Toggle:** 36 × 20 px track, 16 px thumb, 44 px hit area; on = `--ink` track with a `--ground` thumb, off = `--panel-2` track with a `--faint` thumb. State is always also written as a word. Amber stays out of the toggle so five rows do not turn the screen amber.
- **Empty states keep their frame** and name the next command.
- **Phone:** top bar 52 px + bottom bar of five destinations; tables become two-line rows; the sidebar becomes a full-screen sheet. Admin destinations are reached from the sheet (and the top-bar overflow), not by widening the bottom bar to seven.
- **Accounts detail:** three stacked cards (Identity, Access, Danger) in one column; Access is a scrollable checkbox list capped at 320 px with a live filter. The Danger card is visually last and uses `--line`, not a red panel.

### 8.6 Anti-slop rules for this build

- No Discord blurple, no purple, no gradient text, no glowing bot avatar.
- No "supercharge", "seamless", "all-in-one", "empower", "unleash".
- No invented servers, testimonials, logos, ratings or "trusted by".
- No icon-topped feature cards, no six identical cards, no 01/02/03 index labels.
- No fake dashboard of grey bars: every fragment uses real labels, real commands, real states.
- No emoji as UI icons.
- No cookie banner: one essential cookie, disclosed on `/login`.
- No number that is not computed from the bot's own data.

### 8.7 Accessibility

- Body text ≥ 4.5:1; amber on `--ground` is used at ≥ 16 px or as a 2 px mark.
- Focus visible on every control: 2 px `--accent`, 2 px offset.
- State is text first, colour second (cog state, verdict, errors).
- Heartbeat strip has a summary in `aria-label` (gaps, hours, percentage).
- Forms: real `<label>`, `aria-describedby` for errors, `role="status"` for save confirmations.
- Skip link on public pages; landmarks (`header`, `nav`, `main`, `footer`).
- Touch targets ≥ 44 px; the toggle keeps a 36 px visual with a 44 px hit area.
- Reduced motion honoured; no auto-playing media.

---

## 9. Data contracts

### 9.1 Public JSON — `GET /status`

```json
{
  "status": "ok",
  "checked_at": "2026-10-23T06:40:12Z",
  "version": "0.5.0",
  "uptime_s": 184223,
  "discord": { "state": "connected", "gateway_latency_ms": 41.2, "guilds": 14 },
  "runtime": { "loop_lag_ms": 1.4, "mem_rss_mb": 148.6 },
  "db": { "healthy": true, "latency_ms": 0.3 },
  "cogs": [
    { "key": "status", "state": "active" },
    { "key": "settings", "state": "active" },
    { "key": "github", "state": "active" },
    { "key": "dev", "state": "active" },
    { "key": "utility", "state": "active" }
  ],
  "heartbeat": {
    "hours": 24,
    "bucket_s": 300,
    "legend": { "1": "on", "l": "low", "0": "gap", "-": "before start", "n": "now" },
    "ticks": "111111111111111111111111111111111111111111011111...",
    "summary": "24 hours, no gaps"
  },
  "renewal": { "last_renewed_at": "2026-10-21T14:02:00Z", "due_at": "2026-10-25T14:02:00Z" }
}
```

- `status` is the verdict from §4.3. HTTP **200** for `ok` and `degraded`, **503** for `offline`.
- Removed from the public payload: `pid`, `members_cached`, `github_token`, `environment`, raw `scheduler.last_error`, module counters. These stay in `/dashboard/status.json`.
- ETag = `checked_at`; `If-None-Match` → 304 with no body.
- No guild IDs, no channel IDs, no usernames.

### 9.2 Private JSON and SSE

| Endpoint | Payload |
|---|---|
| `GET /dashboard/status.json` | Today's full snapshot (`StatusService.snapshot()`) |
| `GET /dashboard/events` | SSE frames: `hello`, `cog.toggle`, `config.set`, `heartbeat`, keepalive `: ping` every 20 s |
| `POST /dashboard/g/{id}/cogs/{cog}` | `{cog, enabled, state, locked, audit_id, activity_id, held_announcements}` |
| `POST /dashboard/g/{id}/settings/{key}` | `{key, value, display, audit_id, activity_id}` |

- Unauthenticated, or a session whose account is suspended/revoked → **401 JSON** (`{"error": "auth"}`). The row is deleted first.
- Authenticated but under-privileged → **403 JSON** (`{"error": "forbidden", "needed": "admin"}`) and an `authz.denied` row.
- `activity_id` lets the client link a change to its accountable record without a second request.

SSE is authenticated by the **cookie**; `EventSource` sends it automatically on same-origin. No token in the URL.

### 9.3 Schema additions

```sql
-- v3: accounts, grants, accountable activity; sessions rebuilt.
CREATE TABLE IF NOT EXISTS accounts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  discord_user_id INTEGER NOT NULL UNIQUE,
  name            TEXT    NOT NULL,
  role            TEXT    NOT NULL DEFAULT 'viewer'
                    CHECK (role IN ('owner', 'admin', 'viewer')),
  status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended', 'revoked')),
  is_system       INTEGER NOT NULL DEFAULT 0,
  note            TEXT,
  created_by      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  created_at      TEXT    NOT NULL,
  updated_at      TEXT    NOT NULL,
  last_login_at   TEXT,
  last_login_ip   TEXT
);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts (status);

CREATE TABLE IF NOT EXISTS account_guilds (
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  guild_id   INTEGER NOT NULL REFERENCES guilds(id)   ON DELETE CASCADE,
  granted_by INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  granted_at TEXT    NOT NULL,
  PRIMARY KEY (account_id, guild_id)
);
CREATE INDEX IF NOT EXISTS idx_account_guilds_guild ON account_guilds (guild_id);

CREATE TABLE IF NOT EXISTS sessions (
  id_hash         TEXT PRIMARY KEY,         -- sha256 of the cookie value
  account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  discord_user_id INTEGER NOT NULL,
  username        TEXT,
  avatar          TEXT,
  last_guild_id   INTEGER,
  csrf            TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions (account_id);

CREATE TABLE IF NOT EXISTS oauth_states (
  state_hash  TEXT PRIMARY KEY,
  next_path   TEXT,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id      TEXT    NOT NULL,
  account_id      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  discord_user_id INTEGER,
  account_label   TEXT,
  role            TEXT,
  action          TEXT    NOT NULL,
  outcome         TEXT    NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
  guild_id        INTEGER,
  target          TEXT,
  before_json     TEXT,
  after_json      TEXT,
  ip              TEXT,
  user_agent      TEXT,
  detail          TEXT,
  created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_account ON activity_log (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_guild   ON activity_log (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_action  ON activity_log (action, created_at DESC);

INSERT INTO meta(key, value) VALUES ('schema_version', '3')
  ON CONFLICT(key) DO UPDATE SET value = excluded.value;
```

Migration notes:

- The old `sessions` table is **dropped and recreated** at v3. Its rows are magic-link sessions with no `account_id`; they cannot be mapped to accounts and should not be. Everyone signs in once.
- The old `sessions.user_id` index disappears with the table; the new indexes replace it.
- `meta.dash.jti.*` rows from the link-token model are deleted once on boot (`DELETE FROM meta WHERE key LIKE 'dash.jti.%'`).
- `accounts` is seeded from `BOT_OWNER_IDS` *after* the schema step, inside the same startup transaction, so a failed seed does not leave a half-migrated database.
- `guilds` already exists before this block; `account_guilds.guild_id` therefore has a valid FK target.
- `activity_log` has no FK on `guild_id`: a guild hard-deleted later must not erase the record that a change was made in it.

### 9.4 Heartbeat encoding

- `heartbeat_series(24)` already returns 288 `ticks` (`on|low|off|now`) and a `summary`.
- JSON encodes the list as a 288-char string; HTML renders 288 `<rect>` elements in one `<svg>` with `viewBox="0 0 575 40"`, each `x = 2 * index`, `width = 1`, `preserveAspectRatio="none"` and `shape-rendering="crispEdges"` — a 1-unit gap between ticks at any width.
- `now` is the last bucket only when it has a reading; otherwise the last bucket is `gap`.
- **Buckets before `started_at` render as `none` (a 2 px baseline), never as downtime.** The summary already excludes pre-start buckets; the public strip must apply the same rule, or a freshly restarted bot looks like it was down for 23 hours. Only gaps after start render as `off`.
- The summary keeps the existing honest wording ("one gap, 03:10–03:15, process not heartbeating") but the public page omits anything that names a guild.

### 9.5 Limits

| Limit | Value | Note |
|---|---|---|
| Global per-IP | 120/min, burst 30 | assets count; a page load is ~6 requests |
| `/login`, `/auth/callback` | 20/min, burst 5 | separate bucket |
| Mutations | 30/min per session | separate bucket |
| Public SSE | **none** | public uses polling |
| Private SSE | **8 concurrent**, separate semaphore | must not consume `connection_slots` |
| Request timeout | 10 s | SSE exempt |
| `MAX_CONNECTIONS` | 32 | unchanged |

**Required fix:** the current `request_guard_middleware` holds a `connection_slots` permit for the entire SSE stream. With public traffic this can starve the site after 32 dashboard tabs. Add a dedicated `sse_slots` semaphore and bypass `connection_slots` for `/dashboard/events`.

---

## 10. Configuration changes

| Var | Action | Default | Notes |
|---|---|---|---|
| `PUBLIC_BASE_URL` | **add** | — | Required in production. Canonical URL, OAuth redirect base, `/help` links, sitemap. `https://` in production; `http://localhost:8080` allowed in development. |
| `DISCORD_CLIENT_ID` | **add** | — | Required in production. |
| `DISCORD_CLIENT_SECRET` | **add** | — | Required in production. Never rendered, never logged. |
| `SESSION_IDLE_DAYS` | add | `30` | Rolling cookie age. |
| `SESSION_MAX_DAYS` | add | `90` | Absolute age. |
| `COOKIE_SECURE` | add | `true` | `false` only for `http://localhost`. |
| `ACTIVITY_RETENTION_DAYS` | add | `180` | Pruned daily; must be ≥ 30. |
| `TRUST_PROXY` | add | `false` | When `true`, read `X-Forwarded-For` for the activity IP. Only behind a proxy that overwrites the header. |
| `BOT_OWNER_IDS` | change | — | Still required; now also **seeds `owner` accounts** at boot and is the source of truth for ownership (§6.3). |
| `DASHBOARD_ENABLED` | **remove** | — | Always mounted. |
| `DASHBOARD_SECRET` | **remove** | — | No signed blobs. |
| `DASHBOARD_LINK_TTL_S` | **remove** | — | No links. |
| `DASHBOARD_BASE_URL` | **remove** | — | Folded into `PUBLIC_BASE_URL`. |
| `HELP_SITE_URL` | **remove** | — | `/help` uses `PUBLIC_BASE_URL/commands`. |
| `STATUS_PAGE_URL` | **remove** | — | `/` is the status page. |
| `STATUS_TOKEN` | **remove** | — | Public payload is curated; private data needs a session. |
| `RATE_LIMIT_PER_MIN` | change | `120` | See §9.5. |

Validation:

- Production requires `PUBLIC_BASE_URL`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`; all problems are collected into one `ConfigError` (existing pattern).
- `PUBLIC_BASE_URL` must not end with `/`.
- `SESSION_IDLE_DAYS` must be ≤ `SESSION_MAX_DAYS`.
- `ACTIVITY_RETENTION_DAYS` must be ≥ 30 and ≤ 3650; a typo must not silently delete the log at the next prune.
- If `COOKIE_SECURE=false`, log a warning at startup.
- If `TRUST_PROXY=true` and `COOKIE_SECURE=false`, log a warning: a proxy-terminated deployment should be HTTPS.
- The OAuth scope is hard-coded to `identify`; there is no env knob to widen it.

---

## 11. File-level change map

### Delete

```
help-site/                                # entire directory
.github/workflows/docs.yml
app/web/tokens.py
tests/test_tokens.py                      # replaced by tests/test_sessions.py
web-preview/                              # archive first, then delete
```

### Add

```
app/web/public.py                         # public routes
app/web/auth.py                           # /login, /auth/callback, /logout
app/web/sessions.py                       # session + oauth_state store, cookie helpers
app/web/templates/public/__init__.py
app/web/templates/public/layout.py        # public shell (nav, footer, meta)
app/web/templates/public/landing.py
app/web/templates/public/commands.py
app/web/templates/public/getting_started.py
app/web/templates/public/login.py
app/web/templates/public/errors.py
app/web/assets/tokens.css
app/web/assets/public.css
app/web/assets/public.js
app/commands_manifest.py                  # build_manifest() returning a dict
app/services/accounts.py                  # lookup, bootstrap, create, status, grants
app/services/activity.py                  # record_activity(), query, CSV, prune
app/web/accounts.py                       # /dashboard/admin/accounts*
app/web/activity.py                       # /dashboard/admin/activity(.csv)
app/web/templates/app/admin_accounts.py   # list / new / detail
app/web/templates/app/admin_activity.py   # filters, table, detail
tests/test_auth.py
tests/test_sessions.py
tests/test_accounts.py
tests/test_activity.py
tests/test_public.py
revision.md                               # this file
```

### Modify

| File | Change |
|---|---|
| `app/main.py` | Build manifest after cogs load; log the boot stages; remove dashboard-secret guard |
| `app/config.py` | §10 env changes; `ACTIVITY_RETENTION_DAYS`, `TRUST_PROXY`; require OAuth config in production |
| `app/schema.sql` | §9.3 tables; `schema_version=3` |
| `app/db.py` | `transaction()` async context manager; one-time delete of `dash.jti.*`; drop/rebuild v2 `sessions`; seed owner accounts after migration |
| `app/services/settings.py` | Accept an optional connection so `set_cog_state`/`set_setting` join the activity transaction; add `request_id` to `audit_log.detail` |
| `app/web/status.py` | Route table, gzip, ETag, `sse_slots`, `PUBLIC_BASE_URL` root, security headers, `/status` curated |
| `app/web/dashboard.py` | Cookie sessions, redirects, guild paths, settings POST, CSRF, activity writes; role/grant checks on every request |
| `app/web/templates/base.py` | New app shell (labelled rail, top bar, no token in URLs, no inline config); owner-only Admin group |
| `app/web/templates/pages.py` | Drop `token` params; add settings page; keep overview/cogs/watches/audit structure; render viewer as read-only |
| `app/web/assets/app.css` | Adopt tokens, 224 px sidebar, table/form styles, remove card borders where a tone step works |
| `app/web/assets/app.js` | Cookie-based SSE, async forms with CSRF header, no `alert()`, status polling on the public page |
| `app/services/status.py` | `public_snapshot()`, verdict rule, public cogs, `checked_at` |
| `app/cogs/status.py` | `/appardas dashboard` returns a plain URL; explain that accounts are owner-created |
| `app/cogs/utility.py` | `/help` → `PUBLIC_BASE_URL/commands/{cog}#slug` |
| `app/commands_docs.py` | Remove "help-site build fails" wording; one `dashboard` entry; permission words |
| `app/commands_export.py` | `build_manifest()` pure; file write only for debug |
| `.env.example` | §10 |
| `.github/workflows/uptime.yml` | Ping `$PUBLIC_BASE_URL/health` |
| `README.md` | Rewrite §2, §3, §5; add a "web surfaces" section; remove Astro rows |
| `app/version.py` | `0.5.0` |
| `README.md` | Account model section: "no self-registration", roles, grants, activity retention |

### README statements that become false

| README | Now |
|---|---|
| §1 "Astro static build on Cloudflare Pages ✅" | Removed; one process serves everything |
| §2 "Status page: Astro static → Cloudflare/GitHub Pages" | Removed |
| §3 repo layout includes `help-site/`, `web-preview/` | Removed |
| §5 "No OAuth and no session cookies — signed magic link and read-only" | Overturned; OAuth2 + DB sessions, and the dashboard is a console |
| §5 "No web-based editing of settings until HTTPS exists" | Overturned; HTTPS is a prerequisite, and settings are editable |
| Any "Manage Server required for the dashboard" claim | Overturned; access is an owner-created account + guild grants |
| Audit described as the only log | Now two: guild-facing `audit_log` and owner-facing `activity_log` |
| Dashboard footer "Read-only except cog toggles" | Now a real console; update the copy and the lock icon |

---

## 12. Implementation phases

### Phase 0 — Prerequisites (0.5 day)

- Confirm the host exposes the bot's port on a public HTTPS URL.
- Create the Discord application OAuth2 client; register `https://<host>/auth/callback`.
- Record the old Pages URL and decide on a 301.

**Gate:** `/health` is reachable over HTTPS from a phone on cellular data without a VPN.

### Phase 1 — Public face (1.5–2 days)

- Add `app/web/public.py`, public templates, tokens/assets.
- Add `public_snapshot()`, public cogs, verdict rule.
- Move docs to `/commands` from the in-memory manifest.
- Delete `help-site/`, `docs.yml`; update `/help`.

**Gate:** `/`, `/commands`, `/commands/{cog}`, `/getting-started` render with JS disabled; every tree leaf appears; `inspect.mjs` 0 FAIL at 1440/768/390.

### Phase 2 — Accounts and auth (2–2.5 days)

- `accounts.py`, `account_guilds`, schema v3, owner bootstrap from `BOT_OWNER_IDS`.
- `sessions.py`, `auth.py`: `identify` only, account lookup, denial paths.
- Replace link tokens; redirects; CSRF; `activity_log` + transaction helper.
- Delete `DASHBOARD_SECRET`, `DASHBOARD_LINK_TTL_S`.

**Gate:** owner signs in from `BOT_OWNER_IDS`; an unknown Discord ID is denied and appears in Activity; suspending an account kills its session on the next request; no registration route exists.

### Phase 3 — Private face and account administration (1.5–2 days)

- Guild paths, settings screen, channel selects, prefix preview, audit filters, empty states.
- Accounts list/new/detail, role and grant forms, status actions.
- Activity screen with filters, cursor pagination and CSV export.
- Cookie SSE + separate `sse_slots`.

**Gate:** all five settings round-trip; viewer POSTs are refused; every write appears in both `audit_log` and `activity_log` with the same `request_id`; the Activity CSV matches the on-screen filters.

### Phase 4 — Art direction and polish (0.5–1 day)

- Apply the hero, section blueprints, motion, app shell.
- Run `lint.mjs`, `inspect.mjs` (page + app rules), fix every FAIL, look at every screenshot.

**Gate:** 0 FAIL at three widths; the critique is written; 8+ moving elements; reduced-motion pass.

### Phase 5 — Docs, deploy, soak (0.5 day)

- Rewrite README, `.env.example`, this file's status.
- Deploy; watch RSS for 48 h; confirm the uptime workflow and the backup job.
- Optional: `/dashboard/g/{id}/errors` screen from `cog_errors`.

**Gate:** 48 h with no memory growth above 200 MB, no 5xx in the access log, uptime green.

---

## 13. Test plan

### Unit / integration (`pytest`)

| Area | Cases |
|---|---|
| Auth state | valid, reused, expired, mismatched cookie, missing cookie |
| OAuth exchange | success, non-200, timeout, malformed JSON |
| Account gate | unknown ID denied + logged; suspended denied; revoked denied; no row created on denial; `GET /register` → 404 |
| Owner bootstrap | seeds missing owners; demotes removed owners; reactivates a suspended owner; empty env fails startup |
| Sessions | create, lookup, idle expiry, absolute expiry, logout, prune, hash-at-rest; suspension deletes rows |
| Authorization | ungranted guild → 404; viewer write → 403 + `authz.denied`; owner bypasses grants; admin cannot open another guild; non-owner `/dashboard/admin/*` → 404 |
| Accounts | duplicate ID, bad snowflake, `owner` role rejected by POST, zero grants allowed, grant diff, revoke keeps row and grants |
| Activity | every event in §7.2 emits exactly one row; mutation + log roll back together; before/after captured; secrets redacted; retention prune; CSV filters |
| CSRF | missing, wrong, header form, form field |
| Public | `/` without cookie; `/status` schema; no guild ID in payload; `/commands` contains every leaf; unknown cog 404 |
| Verdict | matrix of discord/db/lag/rss/error |
| Cache | two `/status` calls in 60 s → one refresh; ETag 304 |
| Docs drift | every leaf has prose; no orphan prose (existing test extended) |

Replace `tests/test_tokens.py` with `tests/test_sessions.py`; rewrite the magic-link part of `tests/test_web.py`.

### Browser

- `inspect.mjs` on `/`, `/commands`, `/commands/github`, `/login`, `/dashboard`, `/dashboard/admin/accounts`, `/dashboard/admin/accounts/{id}`, `/dashboard/admin/activity` with seeded owner and viewer session cookies.
- `lint.mjs` on `app/web/templates/**` and `app/web/assets/*.css|js`.
- Manual: keyboard-only pass, 200% zoom, iOS Safari, reduced motion.
- Manual: the Accounts and Activity tables at 390 px — horizontal scroll must be contained, not page-wide.

### Manual release checklist

- [ ] Invite a test guild, run `/status`, `/github watch`, `/settings prefix !`.
- [ ] Sign in as the owner, toggle `dev` off, see the guild audit row **and** the Activity row, toggle it on.
- [ ] Change the prefix to `!`, verify `!help` works and the preview matched.
- [ ] Create a viewer account for a second Discord user; grant one guild; sign in as them in a private window.
- [ ] As the viewer: settings render read-only; a forged POST returns 403 and shows up as `authz.denied`.
- [ ] Suspend the viewer; their next request redirects to `/login`; the Activity row records the session count deleted.
- [ ] Try to sign in with a Discord ID that has no account: 403 "not invited", with an `auth.login.denied_unknown` row.
- [ ] Kill the process; the host 502 is the only "offline" page.
- [ ] Restart; the old session still works and `/` shows `connecting` → `ok`.
- [ ] Open 10 dashboard tabs; the site still responds (SSE cap).

---

## 14. Risks and open questions

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | The host does not expose the HTTP port publicly | Blocking: no OAuth, no public site | Verify in Phase 0; if unavailable, one reverse proxy/tunnel in front of the same port (still one deployment, no Astro) |
| R2 | Site availability now equals bot availability | A restart is a site outage | Accepted and documented on `/`; the bot restarts in seconds; uptime workflow alarms |
| R3 | OAuth redirect mismatch | Login fails with `invalid_request` | Register the exact URI; show it in the startup log |
| R4 | Cookies on an IP-only host | `__Host-` cookie rejected | Require a hostname; `COOKIE_SECURE=false` only for localhost |
| R5 | Public traffic on 512 MB | RSS growth, slow pages | Gzip, 60 s snapshot cache, no public SSE, 32 connections, measure in Phase 5 |
| R6 | Owner forgets an account and it keeps access | Silent over-permission | Accounts list shows last sign-in + status; weekly owner review; suspend is one click and kills sessions |
| R7 | `connection_slots` starvation by SSE | Site hangs | Separate `sse_slots` (8) — required |
| R8 | Session table growth | Disk | Daily prune; ~1 row per login |
| R9 | Public status leaks operations | Low | Curated payload; no IDs, no error strings, no environment |
| R10 | Docs and tree drift again | Wrong docs | Manifest built from the tree at boot; test enforces prose coverage |
| R11 | Activity log grows faster than expected | Disk / slow queries | Indexed reads, 180-day prune, cursor pagination, 50k CSV cap; rows are small (no full snapshots) |
| R12 | Owner account lockout | No way in | Owner rows are env-seeded and cannot be suspended in the UI; `BOT_OWNER_IDS` is validated at startup |
| R13 | OAuth identity is the only factor | A stolen Discord session is dashboard access | Log every login with IP; suspension is immediate and kills sessions; Discord 2FA protects the identity. A shorter idle window for owner accounts is a possible follow-up, not a v1 requirement. |

### Decisions needed before Phase 1

1. **Public host name** (subdomain vs custom domain) — fixes cookies, OAuth redirect and canonical URLs.
2. **Is `/status` public?** Proposal: yes, curated. If not, the landing page still needs the same numbers server-side, and the JSON can require a session.
3. **Is the renewal clock public?** Proposal: yes; it is the product's most distinctive constraint. Remove if the exact renewal time is considered sensitive.
4. **Watches writable in 0.5.0?** Proposal: no. Adding a watch needs a channel and event choices; the Discord command is better and already validated.
5. **License / source link in the footer** — currently unknown; do not invent one.

---

## 15. Definition of done

- [ ] `help-site/` and `docs.yml` are gone; nothing in the repo builds with Node.
- [ ] One deploy serves `/`, `/commands*`, `/getting-started`, `/status`, `/health`, `/login`, `/dashboard*`.
- [ ] `/` shows the 24 h heartbeat, five cog states and the health verdict without JavaScript.
- [ ] No `?t=` token exists anywhere in templates, redirects, forms or SSE URLs.
- [ ] A session survives a process restart and a redeploy.
- [ ] No route renders an "expired link" page; expired sessions redirect to `/login`.
- [ ] All five settings and both unlockable cogs are editable in the dashboard, audited with `source: "web"`.
- [ ] Locked cogs refuse with an explanation, not a generic error.
- [ ] `guild_id` in a URL is validated against `account_guilds` on every request; owners bypass grants; mismatches are 404.
- [ ] There is no registration route, no auto-provisioning, and an unknown Discord ID is denied **and logged**.
- [ ] The owner creates, edits, suspends, reactivates and revokes accounts; owner rows themselves are env-managed.
- [ ] A suspended account's sessions are deleted and its next request is refused.
- [ ] Every mutation writes `audit_log` **and** `activity_log` in one transaction with the same `request_id`; a failed log write rolls the change back.
- [ ] The Activity screen filters by account, action, outcome, guild and date, and exports CSV with the same filters.
- [ ] `activity_log` contains no cookies, tokens, OAuth codes or CSRF values.
- [ ] CSRF is enforced on every mutation.
- [ ] Public payload contains no guild, channel or user IDs.
- [ ] SSE cannot consume the general connection pool.
- [ ] Gzip is on; `/` is under 12 KB gzipped.
- [ ] `inspect.mjs` 0 FAIL at 1440, 768 and 390 on public pages and the dashboard.
- [ ] README, `.env.example` and `revision.md` agree with the shipped routes.
- [ ] Version is `0.5.0`; `/about` and the footer agree.

---

## Appendix A — Copy deck

### Navigation

`appardas` · Commands · Getting started · Status · Sign in

### Hero

- **H1:** `A Discord bot, [heartbeat strip], no gaps.`
- **Support:** `Five cogs in one process: GitHub watches, announcements, nudges, settings and status. It runs on 512 MB and answers for itself.`
- **Primary:** `Read the commands`
- **Secondary:** `Add to Discord` · `Sign in`

### Right now

- **H2:** `Right now`
- **Lead:** `Served by the same process that answers in Discord. Nothing here is a screenshot.`
- **Labels:** `gateway` · `event-loop lag` · `memory` · `uptime` · `database` · `renewal`
- **States:** `active` · `unavailable` · `starting`
- **Verdicts:** `ok` · `degraded` · `offline`
- **Stale line:** `Last reading 06:41 UTC. Retrying.`

### What it does

> `Five cogs, each with one job. Status says whether the bot is healthy. Settings turns the other cogs on and off and keeps the audit. GitHub watches an organisation and posts grouped updates. Dev posts announcements and forwards a guarded nudge. Utility answers /help, /about and /invite.`

Inline fragments after `status`, `GitHub`, `announcements`, `nudge`, `help`.

### Find a command

- **H2:** `Find a command`
- **Input label (visually hidden):** `Search commands`
- **Placeholder:** `/github watch appardas #releases`
- **Examples:** `/settings prefix !` · `/dev schedule when:"in 2h" …` · `/github watch appardas #dev-releases`
- **Zero results:** `No command matches “<q>”. Try the cog name: status, settings, github, dev, utility.`

### From invite to first post

1. **`Invite it`** — `You need Manage Server. The bot asks for no message content unless you turn prefixes on.`
2. **`Set the channel`** — `/settings config announce_channel #announcements` — `One channel for announcements. One default channel for GitHub.`
3. **`Read the board`** — `/status` — `Ping, latency, memory, CPU and a five-row cog table. If something is wrong, /errors names the exception.`

### Questions (open)

1. **Is it free?** — `It runs on a free 512 MB host. There is no paid tier and no upsell. If the host is down, so is this page.`
2. **What does it store?** — `Guild settings, cog state, announcements, nudges, GitHub cursors, heartbeats and an audit log in one SQLite file. Message text is never stored.`
3. **Why sign in with Discord?** — `To prove who you are. The login asks only for your Discord identity — no server list, no email. What you can change comes from an account the owner created.`
4. **Can I turn a cog off?** — `GitHub and dev, yes, per server. Status, settings and utility stay on: they are how you find out what is wrong and get back.`
5. **Does it do music, moderation or XP?** — `No. Five cogs that work, not fifteen that half-work.`
6. **Why does this page go down when the bot restarts?** — `Because the page is served by the bot's own process. One deployment, one source of truth. A separate static site could stay green while the bot was dead.`

### Close

- **H2:** `Put it in your server.`
- **Action:** `Add appardas`
- **Links:** `Commands` · `Status` · `Sign in`
- **Footer:** `appardas 0.5.0 · one process, five cogs · built for a 512 MB host`

### Private face

- **Login:** `Sign in with Discord` / `Accounts are created by the bot owner. Signing in only proves who you are.`
- **Denied — not invited:** `This Discord account has not been invited. Ask the owner for access.`
- **Denied — suspended/revoked:** `This account is not active. Ask the owner.`
- **No servers:** `No servers assigned yet. Ask the owner to grant you a server.`
- **Viewer banner:** `You can see this server's settings but not change them.`
- **Saved:** `Prefix saved. New prefix: !` / `Announcement channel saved.` / `Timezone saved: Asia/Manila.`
- **Locked:** `status cannot be turned off — it is how you find out something is wrong.`
- **Empty watches:** `No watches yet. Run /github watch appardas #releases in Discord.`
- **Empty audit:** `Nothing recorded yet. Toggles and settings appear here with the actor and the source.`
- **Signed out:** `Signed out.`

### Accounts (owner)

- **List lead:** `Only the people here can sign in. Discord proves identity; these rows decide access.`
- **New lead:** `Paste the Discord user ID, choose a role and the servers they may open. They sign in at /login — no invite code.`
- **Created:** `Account created. Share /login with them; they sign in with Discord.`
- **Duplicate:** `That Discord ID already has an account. Open it to change the role or reactivate it.`
- **Bad ID:** `That does not look like a Discord user ID. It is a 17–20 digit number.`
- **Zero grants warning:** `No servers assigned. They can sign in, but they will not see anything yet.`
- **System row:** `Managed by BOT_OWNER_IDS. It cannot be suspended or demoted here.`
- **Suspend confirm:** `Suspend Ana? All 2 of her sessions are deleted and she is signed out immediately.`
- **Revoke confirm:** `Revoke Ana? She is signed out now and cannot sign in again. The activity log keeps her history.`

### Activity (owner)

- **Lead:** `Every sign-in, denial and change, with the account that made it. Append-only; nothing here can be edited.`
- **Empty:** `Nothing logged yet.`
- **No match:** `No activity matches these filters.`
- **Denied helper:** `Denied means the request was refused. The attempted action is in Details.`
- **Export:** `Export CSV`

---

## Appendix B — Route → template map

| Route | Template function | File |
|---|---|---|
| `/` | `landing_page(snapshot, heartbeat, cogs, renewal, manifest)` | `templates/public/landing.py` |
| `/commands` | `commands_index(manifest, query)` | `templates/public/commands.py` |
| `/commands/{cog}` | `cog_page(meta, commands)` | `templates/public/commands.py` |
| `/getting-started` | `getting_started(manifest, invite_url)` | `templates/public/getting_started.py` |
| `/login` | `login_page(error, next_path)` | `templates/public/login.py` |
| `/dashboard/g/{id}` | `overview_page(...)` | `templates/pages.py` |
| `/dashboard/g/{id}/cogs` | `cogs_page(...)` | `templates/pages.py` |
| `/dashboard/g/{id}/settings` | `settings_page(...)` | `templates/pages.py` |
| `/dashboard/g/{id}/watches` | `watches_page(...)` | `templates/pages.py` |
| `/dashboard/g/{id}/audit` | `audit_page(...)` | `templates/pages.py` |
| `/dashboard/admin/accounts` | `accounts_page(accounts, filters)` | `templates/app/admin_accounts.py` |
| `/dashboard/admin/accounts/new` | `account_new_page(guilds, error)` | `templates/app/admin_accounts.py` |
| `/dashboard/admin/accounts/{id}` | `account_detail_page(account, guilds, activity)` | `templates/app/admin_accounts.py` |
| `/dashboard/admin/activity` | `activity_page(rows, filters, next_cursor)` | `templates/app/admin_activity.py` |
| `/dashboard/admin/activity.csv` | `activity_csv(rows)` (plain text) | `app/web/activity.py` |
| 404 / 500 / 403 | `error_page(kind, detail)` | `templates/public/errors.py` |

Every template function takes data and returns a string. No I/O, no globals, no session lookups inside templates.

---

## Appendix C — Implementation notes for the coding agent

1. **Read `app/main.py` boot order first.** `prepare()` runs before `start_server()`, so the manifest, DB and cogs exist before the first request. Keep that order; add a `boot_stage` field (`db`, `cogs`, `discord`) to the snapshot so `/` can say "connecting".
2. **Do not change `SettingsService` semantics.** The web must call `set_cog_state` and `set_setting` exactly as the Discord commands do; that is what keeps locking, cache invalidation, events and audit consistent.
3. **Escape everything.** Use the existing `e()` helper for every interpolated value, including command prose.
4. **No new dependency.** OAuth uses the `aiohttp` that ships with `discord.py`. Gzip uses `zlib`. Hashing uses `hashlib`.
5. **Keep the existing asset hashing pattern** (`sha256(bytes)[:8]`) and extend it; never serve an unhashed CSS/JS file.
6. **Do not put a token in a URL, ever.** If a feature seems to need one, use the session cookie.
7. **Prefer redirects to error pages** for private HTML routes.
8. **Test with cookies disabled** for the public face and **with JS disabled** for docs and forms.
9. **Delete code, do not orphan it.** `tokens.py`, the Astro folder and the `dash.jti.*` rows must go in the same change that replaces them.
10. **The account row is the only authority.** Never read a role from the cookie, the OAuth response or a hidden form field. Join `sessions → accounts` on every request, then check `account_guilds`.
11. **Log or don't do it.** Mutations and their `activity_log` row share one transaction. If you cannot write the log, do not commit the change.
12. **Redact before serialising.** Run the key denylist in §7.6 over `before`/`after`/`detail`, then cap the JSON. Never pass a raw request dict into the log.
13. **Run the gate before claiming done:** `lint.mjs`, `inspect.mjs` at 1440/768/390, and the pytest suite.
