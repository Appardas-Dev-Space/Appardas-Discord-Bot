# New Sub-Account Credential DM

## Outcome

**Status: SUCCESS.** Successful account creation now sends a credential DM to the newly created account's Discord identity. The administrator is not the recipient and receives only creation/delivery status. Delivery failures leave the committed account and grants intact.

## Objective

After an authorized owner clicks **Create account** and creation succeeds, the Discord bot must privately send the assigned username and temporary password to **the new account's Discord user ID, never the administrator's ID**.

Local username/password authentication and first-login password-change enforcement remain unchanged.

## Scope

- Backend delivery automation using the existing account-creation route and Discord SDK.
- No template, layout, styling, OAuth, schema, or account-permission changes.
- `taste-skill` is not authorized for new UI artifacts and is not used. Existing response/notice handling will report delivery status; no UI design artifact is being created.
- Preserve the uncommitted authentication fixes already in the working tree. The preceding full-suite baseline was **244 tests passed**.

## Implementation

1. `create_account` commits account creation, grants, and the creation activity record before invoking `_send_credentials_dm`.
2. The sender uses `account.discord_user_id` from the returned **new account**, not `request["account"]` (the administrator). It checks the bot cache, then fetches that user if needed.
3. The private message contains the saved, normalized username, the exact submitted temporary password, the console sign-in URL, and a first-password-change reminder. Mentions and link previews are disabled.
4. Ordinary passwords are rendered literally in a code block. For passwords exceeding Discord's message limit, containing a code-block delimiter/control characters, or requiring whitespace preservation, the DM attaches `temporary-password.txt` containing the exact UTF-8 password bytes. Buffers are in memory only and are closed on completion or failure; credentials are never truncated or altered.
5. Delivery is bounded by five seconds or the remaining HTTP request deadline, with a small response margin. Closed DMs, unknown users, offline/HTTP failures, timeouts, and exhausted deadlines produce an unconfirmed-delivery warning, not account rollback.
6. JSON responses include `dm_delivered` and a safe redirect; normal forms use the same safe redirect. The existing detail-page notice shows the delivery outcome. No password enters the response, URL, delivery log, or a plaintext database/retry queue.
7. Duplicate, invalid, unauthorized, or CSRF-failing creation requests and rolled-back activity writes cannot trigger delivery. Updates and credential resets do not send unrequested DMs.

### Changed Files

| File | Change |
| --- | --- |
| `app/web/accounts.py` | Post-commit credential DM, new-account targeting, safe formatting/attachments, bounded delivery, and admin status reporting. |
| `app/web/status.py` | Expose the existing request guard's deadline to the sender. |
| `tests/test_auth_credentials.py` | Mocked Discord delivery coverage and explicit protection against messaging the administrator; deterministic audit-row ordering in a pre-existing test. |
| `tests/test_web.py` | Mock Discord lookup/delivery in HTTP fixtures so regression tests never contact Discord. |
| `README.md` and the authentication report | Updated behavior and cross-reference to this report. |
| This report | Durable scope, decisions, and validation context. |

## Existing Structure

- `app/web/accounts.py`: account creation already uses the atomic `mutation` boundary; plaintext password remains transiently available from the submitted form after commit.
- `app/web/status.py`: the existing HTTP request guard can expose its deadline to keep Discord delivery inside the response budget.
- `app/services/nudge.py` and `app/services/announcements.py`: existing private-message flows demonstrate Discord SDK user lookup/send and disabled mentions. Credential delivery must not reuse nudge storage, which persists message bodies.
- The existing Graphify `nudge` query was consulted as a structural map; current source and the prior authentication report remain authoritative.

## Validation

| Check | Result |
| --- | --- |
| Initial focused HTTP/authentication run | 37 passed; one pre-existing test assumed unordered audit rows. Its query now explicitly orders by account activity ID. |
| `.venv/bin/python -m pytest -q --disable-warnings` | **260 passed**, 349 aiohttp key warnings, in 31.63 seconds. The warning category is unchanged; HTTP coverage and request metadata increased. |
| `.venv/bin/python tools/dump_commands.py --check` | Manifest matches the live command tree. |
| `.venv/bin/python -m compileall -q app tests tools` | Passed. |
| `git diff --check` | Passed. |
| Scoped source review | Completed; recipient selection, post-commit ordering, deadlines, password handling, and unchanged auth/permission boundaries checked. |

Sixteen added tests cover new-account versus admin targeting, cached/fetched users, exactly one application-level attempt per successful creation, completed account/activity commits before sending, closed DMs, HTTP/lookup/offline errors, timeouts and cancellation, expired deadlines, exact password attachments (including Unicode and whitespace), buffer cleanup, CSRF rejection, and no automatic reset DM. Existing validation/authorization/audit-rollback tests also assert no notification is sent on failure.

The delivery logger records only request/account/recipient IDs and exception class names, never Discord error bodies that may echo credentials. Existing `app/logging_setup.py` keeps Discord payload DEBUG logs disabled even when application logging is DEBUG; no global logging changes were needed.

All Discord operations in automated tests are mocked. No real passwords, live Discord messages, or production database changes were used. Live delivery still requires a working bot connection and Discord permission to DM the new user. A timeout means delivery is unconfirmed, not proof that Discord never accepted the message; no automatic replay is performed.
