# Accounts and the waiting list

Cloud Agent has one account system, its own. People sign up with an email
address and password, or a phone number and code, verify it, sign in, and
apply for Agent use through a waiting list that administrators review. There
is no other sign-in and no mode to choose: HybridInference Gateway sign-in,
admission and inference were removed
([cutover](upgrade-notes.md#independent-accounts-cutover)).

Designs:
[independent accounts and waiting list](../agents/specs/2026-10-27-independent-accounts-and-waitlist-design.zh-CN.md),
[independent accounts only](../agents/specs/2026-10-30-independent-accounts-only.md)
and the [feature parity table](../agents/specs/2026-10-29-account-feature-parity.zh-CN.md).

Administrators work in **Admin → Users**. **People** lists every account with
its application state, account state, teams and last sign-in, and **Review**
opens an account's actions, note and sign-in history. **Teams** lists every
team ([Teams](operations.md#teams)). The old `/admin/accounts` and
`/admin/teams` addresses open these pages. The account pages follow the
administrator's language (English or Simplified Chinese); most other admin
pages are English only.

## How a request is admitted

Every protected owner or administration request, and `/v1/session/me`, reads
the account's current state from this database through the account directory
(`cloud_agent/accounts/directory.py`). Nothing is cached, no other service is
asked, and no account ID leaves the service.

| Permission | Granted when | Allows |
| --- | --- | --- |
| `agent.use` | The waiting-list application is approved | Owner APIs, subject to per-resource ownership checks |
| `agent.admin` | The account is approved and marked administrator | Administration, including runner-host APIs; requires `agent.use` |

`ADMIN_TOKEN` also authorizes administration and the recovery settings page,
even before any administrator account exists. Host and worker credentials,
attempt lease fences and ownership checks are separate controls.

A decision grants access only when it is well formed: its permissions are
unique and recognized, `agent.admin` comes with `agent.use`, and it is for the
account asked about. Account roles and profile fields are not authorization
inputs. The session response carries `agent_access: {allowed, permissions}`,
and the web app denies access when that field is missing.

| Condition | Result |
| --- | --- |
| No session, or a revoked or expired one | 401; the web app returns to sign-in |
| Signed in without `agent.use` | 403; the web app shows the waiting list |
| Account unknown, unverified, suspended or deleted | `subject_unavailable`: sign-in is refused and existing sessions get a denial |
| The account store cannot be read | 503; the web app hides protected content and offers a retry |
| `AGENT_LOCAL_SESSION_SECRET` or `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` missing, or a retired Gateway variable set | The control plane does not start, and names what to set or remove |

Owner streams (job events, the change feed, terminal output) re-read the
session and the admission every 30 seconds between frames, and end on a
refusal. Every stream also ends after 15 minutes; the client reconnects and is
admitted again. An MCP OAuth callback that returns after the account lost
`agent.use` is refused without consuming the authorization.

Code: [`accounts/agent_access.py`](../../apps/backend/cloud_agent/accounts/agent_access.py),
[`accounts/directory.py`](../../apps/backend/cloud_agent/accounts/directory.py),
[`api/deps.py`](../../apps/backend/cloud_agent/api/deps.py) and
[`api/session.py`](../../apps/backend/cloud_agent/api/session.py).

## Enabling it

A deployment needs its own, empty database. At startup the control plane binds
an empty database to the account system (`agent_local_identity_namespace`). It
refuses a database bound to a Gateway issuer, or one holding owner data
without a binding (every Gateway-era database), and names the cutover
runbook. Nothing skips this check or migrates Gateway owners.
`python -m cloud_agent.deployment.identity_namespace --check` runs it
read-only.

Startup, and `deploy/control-plane/deploy.sh` before it pulls anything, also
refuse every variable left from the Gateway integration: `AGENT_AUTH_MODE`,
`GATEWAY_*`, `IDENTITY_*`, `AGENT_SESSION_REDIRECT_URI`, `AGENT_GATEWAY_URL`,
`AGENT_WEB_GATEWAY_URL`, `AGENT_WEB_FEATURE_RAG` and the rest of
`RETIRED_GATEWAY_VARIABLES` in `cloud_agent/accounts/config.py`. A host that
still sets one is refused too.

| Value | Purpose |
| --- | --- |
| `AGENT_LOCAL_SESSION_SECRET` | At least 32 random bytes, different from `AGENT_SESSION_SECRET`. Keys the stored hashes of sessions, emailed tokens and abuse counters. The `keys` service generates it on a new installation with the bundled database. |
| `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` | Required at startup. Encrypts personal model keys and other stored credentials, each in its own domain. A new deployment generates it into its settings. |
| `AGENT_MODEL_CONNECTIONS_ENABLED` | On unless `0`, `false` or `no`. When off, no model can be set up and no new turn starts, on any connection. |
| `AGENT_WEB_BASE_URL` | Seeds the **Web app URL** setting (Admin → Settings → General), which first-run setup also asks for. It is the only base for emailed links; a request's `Host` is never used. |
| `AGENT_PUBLIC_API_URL` | Seeds the **Public API URL** setting, beside it: this API's public address, which OAuth callbacks name. |

Everything else is under **Admin → Settings → Accounts**, imported once from
its variable ([ENVIRONMENT.md](../../ENVIRONMENT.md)): public sign-up and
email verification, the approval policy (`manual`, or `domains` with the
signup domain list), administrator notices and email addresses, the Terms of
Service, the session lifetime, the sign-in and sign-up limits, the email
transport (SMTP, Tencent SES or a registered one), phone sign-in with Tencent
SMS, and Turnstile. Secrets are encrypted and never shown again. While
verification is required and no mail transport is set, email sign-up is
closed and account recovery answers 503; with verification off, sign-up works
without mail.

The web server must reach the same control plane (`AGENT_CONTROL_PLANE_URL`);
it reads `GET /v1/auth/config` and `GET /v1/auth/options` to learn what its
sign-in pages offer. Hosts need nothing for accounts beyond their
control-plane, MCP and model-relay tunnels. In **Admin → Servers → Server
setup**, administrators select only the control-plane machine.

### The first administrator

Signing up first grants nothing. While no administrator exists, every
control-plane process logs a one-time setup code at startup
(`agent_local_setup_pending`, at WARNING). Exchange it at
`/setup/administrator` for an administrator that signs in with a login name
and needs no email. Wrong codes are limited to 10 per address in 15 minutes.
Creating an administrator any other way also closes setup.

With the deployment's database credential and local secret, you can do the
same from the command line. The password is read from standard input or a
prompt, never from the arguments:

```bash
python -m cloud_agent.accounts.bootstrap_admin --create-login-name ops < password.txt
python -m cloud_agent.accounts.bootstrap_admin --create-email a@lab.edu --member
python -m cloud_agent.accounts.bootstrap_admin --find-email admin@example.org
python -m cloud_agent.accounts.bootstrap_admin --account-id <id> --approve
```

`--create-*` makes a verified, approved account, which is an administrator
unless you pass `--member`. `--account-id` makes an existing verified account
an administrator; add `--approve` to approve its application first, since
only an approved account can administer. Each step is the audited action the
administration API uses, with the actor `deployment-bootstrap`.

An account whose proven email is on the **administrator email addresses**
setting (seeded by `AGENT_LOCAL_ADMIN_EMAILS`) is approved and made an
administrator when it signs in. Proven means that a link sent to the address
was followed (verification, an address change or a password reset), or that
the operator created the account. An address accepted while verification is
off is not proven, so listing it does not hand the console to whoever signs
up with it first; its holder proves it with a password reset. The list stays
in charge: withdrawing a listed account's administration or access, or
declining it, is refused with 409 `listed_admin_address` until the address is
off the list or the account is suspended, because the next sign-in would grant
it back.

### Importing accounts from FreeInference

A deployment can be seeded once with FreeInference's active users. Each
becomes an approved account that signs in with the same email, login name or
phone and the same password: both services use the same Argon2id parameters,
so the hash verifies unchanged. Nothing links the two accounts afterwards.

Export from the FreeInference database, read-only. Users pending approval,
rejected, suspended or deleted there are left out:

```bash
(umask 077; psql "$FREEINFERENCE_DATABASE_URL" -At -c "
  SELECT json_build_object(
    'email', email, 'email_verified', coalesce(email_verified, false),
    'login_name', login_name, 'phone', phone, 'phone_verified', phone_verified,
    'user_name', user_name, 'password_hash', password_hash)
  FROM users WHERE status = 'active' AND NOT hard_delete_pending
  ORDER BY created_at, id" > users.jsonl)
```

The file holds password hashes: keep it readable by its owner only, copy it
over SSH, and delete it after the import. Then, on the control plane:

```bash
python -m cloud_agent.accounts.import_accounts users.jsonl          # dry run
python -m cloud_agent.accounts.import_accounts users.jsonl --apply
```

Without `--apply` nothing is written. Both report how many accounts are (or
would be) imported, which lines were skipped and why, and what was dropped,
by line number only.

- An account any of whose identifiers already exists here is skipped whole,
  so a second run imports nothing twice.
- An email FreeInference verified counts as proven; an unverified one is
  imported unverified. A phone is imported only if verified, and a login name
  only with a password. A hash in another format is dropped, and its holder
  resets the password by email.
- Each account is created and approved in one transaction, audited as
  `agent.local_account.import` by `operator:account-import`. No mail is sent,
  no model connection is created, and the Terms are not recorded as accepted.

## Identity and admission

An identity is verified when the account has no unverified email and at least
one verified sign-in identity: a confirmed email, a login name (made by the
operator) or a phone confirmed by code. Each identity records what verified it
in `verified_by`: `link`, `code`, `operator`, or `policy` for an email
accepted unproven while verification is off. Only `link` and `operator` prove
a mailbox, which the administrator address list and the signup domain list
require.

A password reset that proves a `policy` email takes the account back from
whoever claimed the address first. Their display name, language, Terms
acceptance and application text are cleared, a pending application returns to
not applied, and the reset page and an `account_reclaimed` mail tell the
holder. Administrators' decisions, the sign-in history and Agent data stay; a
holder who finds work that is not theirs can delete the account or ask an
administrator to erase it.

The view `agent_local_account_access` answers whether an account is verified,
may sign in and may use Agent. Every SQL gate reads it, and an account missing
a row has no access.

| Account | Signs in | Owner routes, tasks, terminals, MCP | Administration |
|---|---|---|---|
| Identity unverified | Verification and recovery only | No | No |
| Verified, not applied or pending | Yes | No | No |
| Verified, approved | Yes | Yes, with ownership checks | No |
| Verified, rejected or revoked | Yes, and sees the reason | No | No |
| Approved and marked administrator | Yes | Yes | Yes |
| Suspended | No; every session is revoked | No | No |
| Deleted | No; signed out everywhere, links ended | No | No |

With the `domains` policy, an account whose proven email domain is on the
list (exactly, or under a `*.` entry) is approved, audited as
`signup-policy`, as soon as it is verified. With an empty list every verified
account is approved, phone accounts included; otherwise a phone account, which
has no domain, waits for review.

The administrator flag grants nothing on its own, and granting it never
approves an account. Suspension revokes the account's sessions, clears the
flag and turns an approval into a revocation. Restoring lets the account sign
in and apply again, and grants nothing back. Administrators cannot suspend
themselves or remove their own access; `ADMIN_TOKEN` can.

Every account or admission change increments the account's `version`
(`WaitlistEntry.version`). An administrator action names the version it read
and is refused with 409 `version_conflict` if the account changed since. The
change and its `agent_admin_audit_log` row commit together; if the row cannot
be written, the change still commits and the Audit log page counts the miss.
The row keeps the states, version, reason and both names as they were, and
never a password, hash or token. The acting administrator's own permission is
checked again in that transaction (`actor_not_admin`).

## Sessions and credentials

- The session cookie, `__Host-cloud_agent_local_session` (plain
  `cloud_agent_local_session` where cookies are not `Secure`), names a
  browser. A browser holds up to five signed-in accounts, each with its own
  session row; past five, sign-in answers `browser_full`. Every sign-in
  rotates the cookie. Switching needs no password while the chosen account's
  session lasts.
- Each web app request names the account its tab shows (`X-Agent-Account`;
  streams use `agent_account`). Once the browser's active account is another,
  the request is refused with 409 `account_switched` and the tab reloads, so a
  stale tab never acts as the next account.
- A browser that signed in to an account before carries a signed device
  cookie (`__Host-cloud_agent_device`) and has its own sign-in window, so
  others guessing that account's password from the same address cannot lock it
  out there.
- Passwords use HybridInference's Argon2id parameters and rules. Hashing runs
  on four threads with at most 64 waiting; beyond that a request gets 503.
- A session value is `cals1.` plus 256 random bits, and the database keeps
  only its HMAC, keyed from `AGENT_LOCAL_SESSION_SECRET`. A session lasts 30
  days by default (`accounts.session_ttl_days`), is never extended, and ends
  at sign-out, a password change or reset, suspension or deletion. The retired
  Gateway session cookie (`__Host-cloud_agent_session`) is never read.
- Opening a session locks the account row before checking the password, so a
  concurrent reset or suspension always wins.
- No recipient address or link is ever logged.

Abuse limits are fixed-window counters in Postgres, shared by every worker and
replica and keyed by hashes of the normalized identifier:

| Limit | Default |
|---|---|
| Sign-in (settings) | 5 per identifier and address, and 50 per identifier, in 15 minutes; 20 per address in an hour |
| Sign-up (settings) | 5 per address in an hour and 10 in a day; one shared budget for sign-ups whose address cannot be told |
| Mail | 3 per email in an hour and 10 in a day; 10 per address in an hour |
| Token redemption | 30 per address in 15 minutes |
| Phone codes (settings) | One per 60 seconds per number; 5 an hour and 10 a day per number; 20 an hour and 100 a day per address; 5 checks per code |

## The HTTP surface

Errors use the usual envelope, `{"detail": {"error": {"type", "message"}}}`.
Every response is `no-store`, and a 422 names fields and rules without
repeating a password or token.

| Route | Notes |
|---|---|
| `GET /v1/auth/config`, `GET /v1/auth/options` | Public. Whether sign-up is open (`{signup_enabled}`), and what the account pages offer: sign-up methods, mail, phone policy, Turnstile key, Terms, pending setup and the browser limit |
| `POST /v1/auth/signup` | `{email, password, user_name?, use_case?, accepted_tos, turnstile_token?}`. Always `{ok: true}`; a registered address gets a password-reset link instead of a second account. Disposable and reserved domains are refused, and a `use_case` also submits the Agent application |
| `POST /v1/auth/login` | `{email, password}`; `email` may be a login name. `invalid_credentials` for an unknown identifier and a wrong password alike; `account_deleted`, `account_suspended` and `email_not_verified` only after the right password |
| `POST /v1/auth/phone/code`, `/phone/signup`, `/phone/login` | A six-digit code bound to its purpose, number and account, with the same answer for every number. Each code request carries its own Turnstile token when Turnstile is configured |
| `POST /v1/auth/verify-email`, `/resend-verification`, `/forgot-password`, `/reset-password` | `{ok: true}`. Tokens are single-use and expire (24 hours to verify or bind, 1 hour to reset); a newer token replaces older ones. A reset verifies the email and ends every session |
| `GET /v1/auth/accounts`, `POST /v1/auth/switch`, `DELETE /v1/auth/accounts/{id}`, `POST /v1/auth/logout-all` | The browser's signed-in accounts: list, switch, sign one out, sign all out |
| `GET /v1/auth/account`, `POST /v1/auth/account/password`, `/email`, `/phone`, `/delete`, `PUT /v1/auth/account/locale` | [The owner's own changes](#the-owners-own-changes) |
| `POST /v1/auth/setup/admin` | First-run setup |
| `GET /v1/session/me`, `POST /v1/session/logout` | The account (`id`, `agent_access`, `email`, `waitlist`, `identity`, `is_local_admin`); sign the browser's active account out |
| `GET`/`POST /v1/waitlist` | A verified account's application. Submitting again while pending changes nothing |
| `GET /v1/admin/accounts` | Search (`q`) and filter (`status`, `account_status`, `team`, `sort`), with counts per state |
| `PATCH /v1/admin/accounts/{id}` | `approve`, `reject`, `revoke`, `suspend`, `restore`, `grant_admin`, `revoke_admin`, `delete` or `resume`, at `expected_version`. Approval and rejection are mailed |
| `PATCH /v1/admin/accounts/{id}/notes`, `GET /{id}/login-events`, `POST /{id}/erase`, `GET /{id}/teams`, `POST /{id}/teams/{team}/transfer` | The note and suspension message, sign-in history, erasure, and the teams a deleted account owns |
| `GET`/`POST`/`DELETE /v1/admin/accounts/signup-domains`, `DELETE /v1/admin/accounts/login-events` | The signup domain list; purging sign-in events |
| `/v1/admin/account-mail` | [Mail to accounts](#mail-to-accounts): `POST /preview`, `POST /test`, create, list, detail and cancel |

Mail links carry their token in the URL fragment (`/verify-email#token=…`,
`/reset-password#token=…`), which a browser never sends to a server, so it
stays out of access logs. The page sends the token in a POST; following a link
changes nothing by itself. Every unsafe `/v1/auth/*` request passes the
cross-site write guard, with or without a cookie, and needs a JSON body;
otherwise a cross-site sign-in could put an attacker's account in the victim's
browser.

## The owner's own changes

Each change is proven again the way the account signs in: with its current
password, counted against the sign-in limits, or for an account without one, a
code sent to its verified phone.

| Change | Rules |
|---|---|
| Password | Change it, or set a phone account's first one. Other sessions end, open reset links stop working, and a verified email is told |
| Email | A link goes to the new address, which replaces the old one only when followed. The old address is told and its reset links stop working. Whether an address is taken is answered only after the proof |
| Phone | Bind a code-proven number to an account that has none. A bound number is not removed or replaced |
| Delete | Type the account's identifier and give the proof. An administrator cannot delete their own account. The account is soft-deleted (below) |
| Language | Kept with the account; the latest choice wins, including one made in this browser while signed out. One account's choice never moves to another account in the same browser |

## Deleting and erasing

Deleting an account, by its owner or an administrator, marks it `deleted`. The
same transaction revokes every session, takes the account off every browser,
ends open links and phone codes, turns approval into revocation, writes an
audit row, and opens a termination record that stops its work
([below](#stopping-an-accounts-work)). Its data stays. Once that cleanup is
acknowledged, an administrator may resume the account: it can sign in again,
without Agent access.

An administrator may then erase it, typing its id to confirm. Erasure is
refused with `erase_blocked` until the account is deleted, its termination
acknowledged, none of its turns queued, running or resident, and every sandbox
it owns deleted. It is also refused with `host_cleanup_pending`, naming the
runs and hosts, while a host still has to clean up one of the account's
environment runs; an offline host does so when it reconnects.

Teams are settled first. A team the account owns alone is deleted. A team
other members still use refuses erasure (`team_transfer_required`) until an
administrator hands it to a member from the account's review panel.
Invitations the account issued are revoked, and a team environment it made
stays with the team, recorded as the team owner's.

Erasure removes every row of the account's Agent data and never another
owner's row; a row of another owner that refers to the account's data blocks
it. Identifiers, password, sessions, tokens, codes, sign-in events and mail
recipients go too. The admin audit trail keeps every row about the account.
The account row stays as an erased tombstone that every gate denies and that
can never be resumed. The erasure's own audit row lists what was removed and
the sandbox deletion archives left in the bucket, which follow the bucket's
retention policy. Ids in columns that name a person without owning a row
(team owners and creators, team audit actors and targets, invitation creators,
join-request deciders) stay, and resolve to nobody.

## Mail to accounts

Administrators mail active or suspended accounts, chosen by account state and
application state, with a ready-made message (maintenance, announcement) or a
subject and a Markdown body (raw HTML is not rendered). They preview the
recipient count and the message, send a test to themselves, then send now or
at a chosen time, confirming the count. Any change asks for a new preview.
The recipients are fixed when the message is created, and only verified
addresses receive it. A message the transport would refuse is refused at
preview, with the reason: Tencent SES sends only approved templates, and a
message without its own goes through the notice template, whose variables are
limited to 800 bytes. SMTP takes any message.

One process at a time sends a message, under a claim another process may take
over once it is 15 minutes old. Each recipient's outcome is recorded as soon as
their message is handed over, so a stopped sender resends nothing; at most the
one message in flight when the claim changed hands can arrive twice. Accounts
deleted since are skipped.

## Stopping an account's work

Revoking, suspending or deleting an account opens a termination record
(`agent_local_access_terminations`) for the new version in the same
transaction. From that commit, every new owner request, claim, goal round,
waiting message, residency establishment and residency heartbeat is refused,
so an attempt admitted earlier cannot start or extend a residency.

`terminate_owner_access` (`application/owner_termination.py`) then stops the
running work through the paths that already end work safely. It cancels
queued turns, asks running turns to stop, pauses waiting messages and active
goals, cancels environment tests and prebuilds, stops environment actions,
releases every residency and closes the released member processes. In each
sandbox group whose members all belong to the account, it freezes the group's
terminals, kills them and lifts the freeze. Groups shared with another owner's
chat are never touched.

A background pass retries open records every 30 seconds, up to 50 accounts per
pass, under a per-account advisory lock. It closes a record once terminal
cleanup is acknowledged and none of the account's turns runs. Approving the
account or granting it administration needs that lock and no open record
(otherwise 409 `access_cleanup_pending`), so a late pass never stops work that
a newer approval allows. Restoring access replays nothing: cancelled turns
stay cancelled, and paused goals and queues wait for their owner. No step has
a wall-clock deadline: a failing host or a backlog delays completion, and the
open record keeps blocking re-approval.

| Work | When it is refused or checked (default settings) |
|---|---|
| New requests, claims, goal rounds, waiting messages, residency establishment and heartbeats | Refused from the commit |
| Open owner event, change and terminal streams | Re-checked every 30 s (`LOCAL_STREAM_RECHECK_S`), between frames |
| A running turn | Asked to stop by the first pass; it stops at its host's next heartbeat (every 30 s), or when its 120 s attempt lease lapses if the host is silent |
| Resident member processes | Closed by a pass, or at the host's next residency heartbeat, which no longer extends them |
| Group terminals and environment actions | Killed by a pass; retried at each check until the host acknowledges |

## Models

After approval, an owner adds a model connection under **Settings → Models**
(`/settings/models`): a provider's Base URL, API key and model IDs, then
**Verify and save**. A failed check saves nothing, and only the owner can use
the connection. A deployment may also provide models under **Admin → Settings
→ Models**. Their key stays in the control plane, and every admitted owner can
use the models it lists ([Provided models](operations.md#provided-models)).

There is no per-account quota or allowance. Costs and limits belong to the
provider key, the owner's or the deployment's, and separate Cloud Agent
accounts do not get separate provider quotas: keys from one provider account
share its allowance. Cloud Agent's sandbox and concurrency limits are
separate.

`GET /v1/agent/config` lists the provided sources and the owner's usable
connections, and sets `model_connection_required: true` when neither offers a
model. A task records its model ID and connection ID, and follow-ups, queued
turns, goals, Fork and residency keep that source; an unavailable connection
never falls back to a same-named model elsewhere. A turn runs at Auto, the
provider's default reasoning effort, unless the owner picks a level that the
provider's model listing declares and the agent's CLI can send. Anything else
is refused (`model_connection_unusable` with reason `effort_unknown`,
`effort_unsupported` or `effort_level_unsupported`). See
[owner model connections](operations.md#owner-model-connections) for
verification, key handling and reasoning levels, and
[usage](operations.md#usage) for what the Usage pages count.

## Data model and tests

The account tables are `agent_local_*`: `0108_local_accounts` creates them,
and `0109_local_account_parity` splits sign-in identities from passwords and
adds the rest.

The rules, credentials, mail and parity features have unit tests
(`tests/unit/test_local_account_*.py`,
`tests/unit/test_independent_accounts_only.py`), and the HTTP surface has
`tests/servers/test_local_auth_surface.py`. The races, barriers, flows,
erasure, import and model onboarding run against real Postgres in
`tests/integration/storage/` and `tests/integration/servers/`
(`make test-db-local`); their model tests use a fake provider and forbid other
outbound calls, so they are not live-provider or Kata acceptance. The web
app's `Account*.test.tsx` files cover the pages with mocked API answers.

## Reused code

The password hashing and policy, the sign-up, sign-in, verification and
recovery behavior, the mail templates and the abuse limits are adapted from
HybridInference (`HarvardMadSys/hybridInference` at
`321e9a6c457dcc80d35977aaac472d54041204a8`, and later features at
`8b49872d1dcbed5c4bc8132b026b6740c3437aa7`; MIT License, Copyright (c) 2026
Harvard SEAS, the same license and holder as this repository). Each adapted
module names its source file. Nothing imports `serving.*`, and nothing shares
HybridInference's database, sessions, secrets or tokens.

Where Cloud Agent differs: a pending account can sign in; signing up with a
registered address changes nothing; a used link is refused; abuse counters
live in Postgres rather than in each process; tokens travel in URL fragments;
setup waits for an administrator instead of closing sign-up; an erased account
keeps a tombstone; administrator mail goes only to verified addresses and
renders Markdown without raw HTML; an address accepted while verification is
off is not trusted by the administrator or domain lists until it is proven;
the administrator list refuses withdrawals it would undo; erasure hands shared
teams over instead of deleting them; mail Tencent SES cannot send is refused
before it is queued; and `argon2-cffi` is called directly rather than through
`passlib`.
