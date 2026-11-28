# Environment inventory

This page lists the configuration values Cloud Agent reads: which component
reads each one, its default, and where to set it. Where the application default
differs from the supported Compose deployment, both are given.

A deployment needs no hand-written configuration. A new control plane starts
without a `.env` ([Settings and bootstrap configuration](#settings-and-bootstrap-configuration)),
and Admin → Servers → Add server writes each host's configuration
([Compute resources](docs/developer/operations-compute.md)). The variables below
are optional overrides. For local setup, start with the
[developer guide](docs/developer/development.md); the
[release contract](deploy/release/README.md) describes the packaged artifacts.

> **Keep deployment values outside this public repository.**
>
> Keep secrets in the operator's private environment or unit files, or in
> read-only key mounts, and back them up with the data they protect. Keep real
> identifiers, hostnames, port allocations, tunnel topology and app
> registrations in the private deployment overlay. Public examples use
> placeholders such as `agents.example.org`.

## Settings and bootstrap configuration

Set a value where it belongs:

- **Admin → Settings**: application behavior, integration URLs and
  credentials, web appearance, operational intervals, upload and skill limits,
  task titles, and global sandbox and workspace limits.
- A server's page under **Servers**: sandbox and workspace values for that
  server only.
- Deployment files: bootstrap configuration, which gives access to the database
  or configures processes outside the control plane.

Bootstrap configuration covers database access, the settings encryption root
(`AGENT_SETTINGS_ENCRYPTION_KEY`, or the legacy `AGENT_SESSION_SECRET`),
recovery access (`ADMIN_TOKEN`), host enrollment and sandbox manager
provisioning, networking, tunnels and listeners, host isolation, image and
build routing, mounted paths, and trusted compute deployment profiles. The
Settings page lists these names with their values hidden.

### Deployment keys

A new installation has no `.env` and no keys to make up. The values the stack
needs before it can read a setting (`AGENT_DATABASE_URL`,
`AGENT_POSTGRES_PASSWORD`, `AGENT_LOCAL_SESSION_SECRET` and
`AGENT_SETTINGS_ENCRYPTION_KEY`, or an older deployment's `AGENT_SESSION_SECRET`
root) live in the `agent-keys` Docker volume, one file each, readable only by
the user that needs it. The Compose `keys` service
(`cloud_agent.deployment.deployment_keys`) runs before anything that needs one:

| The value is | The `keys` service |
|---|---|
| Set in the environment or `.env` | Stores it. After one start you can delete the line. |
| A settings root that differs from the stored one | Stops the start: the stored root may be the only copy of the key every saved secret needs. |
| Already stored | Keeps it. |
| Missing, and the bundled database volume holds no database | Generates it. |
| Missing, with an existing database or an `AGENT_DATABASE_URL` naming another database | Stops the start and names what to set. A missing settings root is only a warning, because a legacy `AGENT_SESSION_SECRET` may be supplied another way. |

Each control-plane process fills these variables from their files when the
environment leaves them unset, so `docker compose exec` and `docker compose run`
see them too; Postgres reads its password through `POSTGRES_PASSWORD_FILE`.
Back the keys up apart from the database
([Disaster recovery](docs/developer/disaster-recovery.md)). This command prints
them as single-quoted `.env` lines that restore them byte for byte on another
machine (unquoted, Compose would read a `$` as interpolation):

```bash
docker exec <project>-control-plane-1 python -m cloud_agent.deployment.deployment_keys export
```

The control plane then creates its first settings row with random
`AGENT_CONTROL_TOKEN_SECRET`, `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY`,
`AGENT_COMPUTE_SECRET_KEY` and `AGENT_SANDBOX_MANAGER_TOKEN` values where the
environment supplies none. Server setup writes that sandbox manager credential
into each host it deploys. An existing settings row never gets generated values.

### Saved settings

Saved settings live in the Agent database and stay in force across restarts.
A row marked *Initial import* is such a setting: its environment value is
imported once, and later `.env` changes are ignored.

Run `alembic upgrade head` (through `0120_operator_model_budgets`) before
starting this release; the [upgrade notes](docs/developer/upgrade-notes.md)
say what each recent migration changes. The main listener then imports the
settings from its environment. On an upgrade it imports only fields a new
migration added and never overwrites a saved value, even an empty one. The
other listeners wait for that import and use the same saved values.

After that, edit values in **Admin → Settings**. **Reset** restores the built-in
default (empty for a credential), never an old environment value. Listeners pick
up changes within five seconds, and the web server reads saved appearance on
every request, with no rebuild or restart. Existing user and compute encryption
keys cannot be replaced or reset in the editor; that needs a separate
re-encryption procedure.

The Settings page shows missing required fields and incomplete integrations.
A new deployment's first administrator signs in with the one-time setup code
the control plane logs and completes them there. When no administrator can sign
in, an operator with `ADMIN_TOKEN` can open `/setup`, which uses the same
audited settings API and keeps the token in page memory only.

Host sandbox and workspace settings have their own precedence
([Sandbox settings from the admin pages](#sandbox-settings-from-the-admin-pages)),
and sandbox archives are configured in Admin only ([Sandbox archives](#sandbox-archives)).

### Sandbox Manager naming compatibility

The service is **Sandbox Manager** (`sandbox-manager` in Compose,
`cloud_agent_host.sandbox_manager` in Python); use the `AGENT_SANDBOX_MANAGER_*`
names. The previous names still work until 2026-12-31. The deploy scripts warn
when one supplies a value and a Python service logs `sandbox_manager_legacy_env`,
but the fallbacks inside the Compose files, the control plane's included, are
silent. Check each deployment's `.env` for:

| Current name | Previous name |
| --- | --- |
| `AGENT_SANDBOX_MANAGER_TOKEN` | `AGENT_WORKSPACE_BROKER_TOKEN` |
| `AGENT_SANDBOX_MANAGER_URL` | `AGENT_WORKSPACE_BROKER_URL` |
| `AGENT_SANDBOX_MANAGER_ADVERTISE_URL` | `AGENT_WORKSPACE_BROKER_ADVERTISE_URL` |
| `AGENT_SANDBOX_MANAGER_URL_ALLOWED_HOSTS` | `AGENT_BROKER_URL_ALLOWED_HOSTS` |
| `AGENT_SANDBOX_MANAGER_REVERSE_BIND` | `AGENT_BROKER_REVERSE_BIND` |
| `AGENT_SANDBOX_MANAGER_REVERSE_PORT` | `AGENT_BROKER_REVERSE_PORT` |
| `AGENT_SANDBOX_MANAGER_REVERSE_TARGET_HOST` | `AGENT_BROKER_REVERSE_TARGET_HOST` |
| `AGENT_SANDBOX_MANAGER_REVERSE_TARGET_PORT` | `AGENT_BROKER_REVERSE_TARGET_PORT` |
| `AGENT_SANDBOX_MANAGER_STOP_GRACE` | `AGENT_BROKER_STOP_GRACE` |

A direct `docker compose` run must set `AGENT_SANDBOX_MANAGER_TOKEN`; only the
host and local deploy scripts map the old token name. The `sandbox-manager`
service keeps `workspace-broker` as a network alias, and the old
`cloud_agent_host.workspace_broker` entry point no longer exists. The rename
needs no secret rotation or database migration. A few old names stay on purpose:
the `broker_url` column and the field in which previous-release hosts report
their address (new API fields use `sandbox_manager_url`), the telemetry producer
ID, and the paths `/var/lib/freeinference-broker` (in retained sandboxes) and
`/var/lib/hybridinference/broker-archives` (in the manager), which hold lifecycle
fences and pending deletion receipts.

## The one rule

> The HybridInference Gateway's `API_KEY_SECRET` and `JWT_SECRET_KEY`, and its
> database credentials, must never appear in this repository, in its
> configuration, or on any host that runs it.

Cloud Agent keeps its own accounts and signs its own tokens with its own
secrets; it does not depend on the Gateway. An SMTP credential an operator
chooses to share is configuration: keep it in the operator's secret store,
never in this repository.

## Local development tools

Local tooling reads these; they are not saved settings.

| Variable | Value / who provisions |
|---|---|
| `CI_LOG_DIR` | Optional log directory for `scripts/ci-local.sh`: gate logs and the database JUnit report. Defaults to a fresh gitignored `.local-ci/<tier>.XXXXXX` directory in the checkout. |

### Local and CI permission regression

`AGENT_PERMISSION_TEST_IMAGE` turns on the opt-in Linux UID regression in
`tests/external/test_shared_member_permissions.py`. It names an already-pulled
Docker image with Python 3 and GNU `find`/`stat`, such as `python:3.12-slim`:

```bash
AGENT_PERMISSION_TEST_IMAGE=python:3.12-slim \
  uv run pytest -q -m external tests/external/test_shared_member_permissions.py
```

The test runs the production normalizer as separate member UIDs without
capabilities, in disposable containers with no network or mounts. CI runs it
before deployment; it needs no model API key or deployed service.

### Local and CI restore regression

`AGENT_RESTORE_TEST_IMAGE` names the base image for
`tests/external/test_checkpoint_restore_roundtrip.py`, `python:3.12-slim` by
default; pull it first. In real Docker, the test stops a chat container as the
Sandbox Manager does, checkpoints it, loads the checkpoint as a later generation
and starts it through the archive start fence; only S3 is a local file:

```bash
uv run pytest -q -m external tests/external/test_checkpoint_restore_roundtrip.py
```

CI runs it before deployment; it needs no S3 bucket, model API key or deployed
service.

## Control plane (`cloud_agent`)

### Database

| Variable | Value / who provisions |
|---|---|
| `AGENT_DATABASE_URL` | Postgres DSN of a database only Cloud Agent uses. A new deployment starts on an empty one. A database from before the independent-accounts release is refused and kept as an archive ([cutover](docs/developer/upgrade-notes.md#independent-accounts-cutover)). |
| `AGENT_POSTGRES_USER` / `AGENT_POSTGRES_DB` / `AGENT_POSTGRES_PASSWORD` | The bundled `postgres` service's user, the database the deployment uses on it, and its password. User and database default to `cloudagent`; PostgreSQL applies these only to an empty data directory. On a new installation the `keys` service generates the password and a matching DSN. `AGENT_POSTGRES_DB` must name the database `AGENT_DATABASE_URL` uses on the bundled service, because the healthcheck, pgAdmin's bundled server and the documented backup, drain and undrain commands open it; while the two differ, the `keys` service logs a warning at every start. |
| `TEST_DB_HOST` / `TEST_DB_PORT` / `TEST_DB_NAME` / `TEST_DB_USER` / `TEST_DB_PASSWORD` | Read by `make test-db` and the CI `dbtest` tier as five separate values, not a DSN. Defaults: `localhost` / `5432` / `hybridinference_test_db` / `postgres` / `postgres`. The database name must contain `_test_`; fixtures refuse other names. With xdist, each worker appends its ID and needs a pre-created database. CI creates `<TEST_DB_NAME>_gw0`…`_gw3` for `-n 4`. |

### pgAdmin database administration

The optional `pgadmin` Compose profile adds **Admin → Database → Open pgAdmin**.
The service has no published port. Every request needs a current `agent.admin`
decision, and pgAdmin asks for its own login too. Its accounts, sessions and
saved connections persist in `agent-pgadmin`.

| Variable | Value / who provisions |
|---|---|
| `AGENT_PGADMIN_URL` | *Initial import*, as **Private pgAdmin URL**: the private origin, `http://pgadmin:5050` for the bundled service. Empty disables the launch link; credentials, paths, queries and fragments are refused. Start the service with `COMPOSE_PROFILES=pgadmin`. The saved setting may only name the bundled service or the scheme, host and port of this variable or `AGENT_PGADMIN_BOOTSTRAP_URL`, so keep a custom address set here. |
| `AGENT_PGADMIN_PUBLIC_URL` | *Initial import.* Optional public proxy URL ending in `/admin/agent/pgadmin`. Empty derives it from the saved web base URL plus `/api/admin/agent/pgadmin`; set it for a separately mounted API. Never derived from browser headers. |
| `AGENT_PGADMIN_BOOTSTRAP_DIR` | *Required for dashboard setup.* `/var/lib/cloud-agent/pgadmin-bootstrap` for the bundled service. The first administrator chooses pgAdmin's email and password in **Admin → Database**. They are written once to the private `agent-pgadmin-bootstrap` volume, readable only by the control plane and pgAdmin's supplementary group, and never appear in responses, audit records, Compose or source control. pgAdmin manages accounts and passwords after that. |
| `AGENT_PGADMIN_BOOTSTRAP_URL` | *Required for dashboard setup.* Private origin used only to initialize the bundled service, normally `http://pgadmin:5050`. Setup saves it as the pgAdmin URL when none is saved; later requests follow the saved setting, including a reset that disables access. |
| `AGENT_PGADMIN_BOOTSTRAP_PUBLIC_URL` | *Optional.* Public proxy URL for first-time setup, ending in `/admin/agent/pgadmin`, used while no public pgAdmin URL is saved. Without either, setup derives it from the saved web base URL plus `/api/admin/agent/pgadmin`. |
| `AGENT_PGADMIN_ENTRYPOINT_FILE` | *Optional.* Startup wrapper that waits for dashboard setup, then runs pgAdmin's entrypoint. Defaults to the bundled `pgadmin-entrypoint.py`; artifact deployments take it from the release recipe. |
| `AGENT_PGADMIN_SERVERS_FILE` | *Optional.* Read-only server definitions, imported on first initialization. The bundled `pgadmin-servers.json` registers `postgres:5432` with the database and user that `AGENT_POSTGRES_DB` and `AGENT_POSTGRES_USER` name and no password; the entrypoint fills in `${AGENT_POSTGRES_DB}` and `${AGENT_POSTGRES_USER}` in a custom file too. Artifact deployments take the default from the release recipe. |
| `AGENT_PGADMIN_COOKIE_SECURE` | *Optional.* pgAdmin Python boolean, default `True`; `False` only for an HTTP local stack. The proxy scopes pgAdmin's cookies to its route itself, with HttpOnly, SameSite=Lax and, on HTTPS, Secure. |

### Public addresses

| Variable | Value / who provisions |
|---|---|
| `AGENT_WEB_BASE_URL` | *Initial import.* Public web-app URL, for example `https://agents.example.org`. Every emailed link is built from it, never from a request's `Host`. The main API refuses with 403 a POST, PUT, PATCH or DELETE that carries the session cookie and that the browser reports as coming from another origin, unless that origin is this one, the pgAdmin public URL's or, for a browser that sends no `Sec-Fetch-Site`, the request's own host. |
| `AGENT_PUBLIC_API_URL` | NEW. *Initial import.* This API's public base URL with its prefix, for example `https://agents.example.org/api`. OAuth callbacks (MCP sign-in, compute registration) are built from it. It replaces the retired `AGENT_SESSION_REDIRECT_URI`. |

### Retired Gateway variables

Cloud Agent no longer supports HybridInference Gateway sign-in, admission or
inference. Each variable below is refused: the control plane stops at startup
and names it, and `deploy/control-plane/deploy.sh` stops before pulling
anything ([cutover](docs/developer/upgrade-notes.md#independent-accounts-cutover)).

| Variable | Status |
|---|---|
| ~~`GATEWAY_BASE_URL`~~ | **removed**, refused |
| ~~`GATEWAY_JWKS_URL`~~ | **removed**, refused |
| ~~`GATEWAY_GRANT_DISPATCH_TOKEN`~~ | **removed**, refused |
| ~~`GATEWAY_GRANT_BASE_PATH`~~ | **removed**, refused |
| ~~`GATEWAY_GRANT_BINDING_ID`~~ | **removed**, refused |
| ~~`GATEWAY_ISSUER`~~ | **removed**, refused |
| ~~`GATEWAY_AUTHORIZE_URL`~~ | **removed**, refused |
| ~~`AGENT_SESSION_REDIRECT_URI`~~ | **removed**, refused |
| ~~`AGENT_GATEWAY_URL`~~ | **removed**, refused |
| ~~`AGENT_AUTH_MODE`~~ | **removed**, refused |
| ~~`AGENT_GATEWAY_SSH_HOST`~~ | **removed**, refused |
| ~~`AGENT_WEB_GATEWAY_URL`~~ | **removed**, refused |
| ~~`AGENT_WEB_FEATURE_RAG`~~ | **removed**, refused |
| ~~`AGENT_ONCALL_DISPATCH_TOKEN`~~ | **removed**, refused |
| ~~`AGENT_ONCALL_USER_ID`~~ | **removed**, refused |
| ~~`IDENTITY_ISSUER`~~ | **removed**, refused |
| ~~`IDENTITY_JWT_PRIVATE_KEY`~~ | **removed**, refused |
| ~~`IDENTITY_JWT_RETIRING_PUBLIC_KEYS`~~ | **removed**, refused |
| ~~`IDENTITY_ALLOWED_REDIRECTS`~~ | **removed**, refused |

### Own secrets

| Variable | Value / who provisions |
|---|---|
| `AGENT_SETTINGS_ENCRYPTION_KEY` | The settings root key, at least 32 bytes, which encrypts the secrets saved in Admin. Without it, `AGENT_SESSION_SECRET` is the root. Every listener must use the same effective root; keep it with every database backup. Changing it, or rotating `AGENT_SESSION_SECRET` while that is the root, requires re-encrypting the saved settings first. The `keys` service generates it on a new installation, never beside an `AGENT_SESSION_SECRET`. |
| `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` | *Initial import*, generated into the first settings row when unset. Encrypts the credentials users store, each kind in its own domain: source-control credentials, MCP connections, environment variables and Secrets, SSH server keys and model connection keys. The control plane refuses to start without it. Use 32 random bytes, unique per environment; a shorter value saved earlier still loads, and startup logs `agent_weak_credential` until you replace it. Replacing it needs a separate re-encryption procedure. |
| `AGENT_CONTROL_TOKEN_SECRET` | *Initial import*, generated into the first settings row when unset. Signs host and worker control tokens. Use 32 random bytes, unique per environment; a shorter value saved earlier still loads, and startup logs `agent_weak_credential` until you replace it. Replacing it invalidates host and worker credentials, so hosts must re-enroll. |
| `AGENT_SESSION_SECRET` | Legacy settings root, for an installation that never set `AGENT_SETTINGS_ENCRYPTION_KEY`; no session uses it. If set, it must differ from `AGENT_LOCAL_SESSION_SECRET`. |
| `AGENT_DEFAULT_EXECUTION_PROFILE` | *Initial import.* Isolation policy for new conversations. Application default `trusted-v1`; the production Compose file sets `untrusted-kata-v1` and the local one `trusted-v1`. The untrusted profile needs the host's current enrolled credential and the claiming replica's attestation of `io.containerd.kata.v2` with a guest kernel different from the host's. The trusted profile allows runc, for trusted internal or development repositories. Follow-ups and Fork keep the stored policy; callers cannot choose it. |
| `AGENT_ALERT_WEBHOOK_URL` | *Initial import.* An operator-owned JSON webhook for queue incidents and recoveries ([queue monitoring](docs/developer/deployment-reliability.md#detection-after-deployment)) and compute replacement events. Unset, events go only to the structured logs. Payloads carry the event, deployment SHA and aggregate counts; compute events add the Host identity and reason codes. Delivery times out after five seconds. The URL never reaches runners or sandboxes. |
| `AGENT_RESIDENCY_TTL_S` | *Initial import.* How long a member's live-process residency survives between host heartbeats, default 1800 seconds. It governs credential and process ownership only: `AGENT_CHAT_IDLE_TIMEOUT_S` alone decides when a settled sandbox group stops. |
| `ADMIN_TOKEN` | Optional machine-to-machine admin credential, and the way into `/setup` when no administrator can sign in. Unset, it grants nothing. Use at least 32 random bytes; startup logs `agent_weak_credential` for a shorter value. |

### Accounts

Every control-plane listener reads `AGENT_LOCAL_SESSION_SECRET` from the process
environment only. Every other variable here is the initial value of a setting in
Admin → Settings → Accounts, imported once and edited there afterwards. Saved
secrets are encrypted and never shown again. See
[accounts](docs/developer/local-accounts.md).

| Variable | Value / who provisions |
|---|---|
| `AGENT_LOCAL_SESSION_SECRET` | Required: at least 32 random bytes, different from `AGENT_SESSION_SECRET`; the `keys` service generates it on a new installation. Keys the hashes of session cookies, emailed tokens and abuse counters. Rotating it ends every session and outstanding link. |
| `AGENT_LOCAL_SIGNUP_ENABLED` | Defaults to on. `false` closes public sign-up, by email and by phone; existing accounts still sign in. Signing up never grants Agent use. |
| `AGENT_LOCAL_SIGNUP_REQUIRE_EMAIL_VERIFICATION` | NEW. Defaults to `true`. `false` treats a new email account as verified at sign-up and sends it no link, so sign-up works without SMTP. Such an address is accepted, not proven: it matches neither the signup domain list nor `AGENT_LOCAL_ADMIN_EMAILS` until a link sent to it is followed. Accounts created while it was on still verify. |
| `AGENT_LOCAL_SIGNUP_APPROVAL` | NEW. `manual` (default): every account waits for an administrator. `domains`: a verified account whose proven email domain is on the signup domain list (Admin → Users → Sign-up policy) is approved at once; with an empty list, every verified account is. |
| `AGENT_LOCAL_SIGNUP_ADMIN_NOTIFY` / `AGENT_LOCAL_SIGNUP_NOTIFY_EMAILS` | NEW. Mail administrators when a sign-up waits for approval (default on). Recipients are the comma-separated list, or every local administrator with a verified email when it is empty. |
| `AGENT_LOCAL_TERMS_URL` | NEW. Optional link to the Terms of Service the sign-up forms ask every new account to accept. Acceptance is required either way. |
| `AGENT_LOCAL_SMS_ENABLED` / `AGENT_LOCAL_SMS_PROVIDER` | NEW. Phone sign-up, sign-in and binding with a code sent by text message. Off (`false` / `none`) by default; on needs a configured provider (`tencent`). |
| `AGENT_LOCAL_SMS_DEFAULT_COUNTRY_CODE` / `AGENT_LOCAL_SMS_ALLOWED_PREFIXES` | NEW. Digits prepended to a number typed without `+`, and the comma-separated `+` prefixes codes may be sent to (empty: any). |
| `AGENT_TENCENT_CLOUD_SECRET_ID` / `AGENT_TENCENT_CLOUD_SECRET_KEY` / `AGENT_TENCENT_CLOUD_TOKEN` | NEW. Tencent Cloud CAM credentials of the `tencent` text message provider (the token only for temporary credentials). This deployment's own; never sent to a host or sandbox. |
| `AGENT_TENCENT_SMS_REGION` / `AGENT_TENCENT_SMS_SDK_APP_ID` / `AGENT_TENCENT_SMS_SIGN_NAME` / `AGENT_TENCENT_SMS_TEMPLATE_ID` | NEW. The Tencent SMS application, its approved signature and its approved template, which takes the code and its lifetime in minutes. Region defaults to `ap-guangzhou`. |
| `AGENT_TURNSTILE_SITE_KEY` / `AGENT_TURNSTILE_SECRET_KEY` | NEW. Cloudflare Turnstile for sign-up and phone codes. With no secret key nothing is checked; with one, a missing or rejected token, or no answer from Cloudflare, refuses. |
| `AGENT_LOCAL_ADMIN_EMAILS` | NEW. Comma-separated, as HybridInference's `ADMIN_EMAILS`: an account whose **proven** email is listed is approved and made an administrator when it signs in. Proven means a link sent to the address was followed (verification, address change or password reset) or the operator created the account, never an address accepted while verification is off. The list stays in charge: withdrawing a listed account's administration or access is refused until the address is removed from it. |
| `AGENT_LOCAL_TERMS_TEXT` | NEW. Plain-text Terms of Service shown at `/terms` and in the sign-up consent when `AGENT_LOCAL_TERMS_URL` is empty. Deployment Terms [site content files](#site-content-files) replace both settings, which Admin then shows read-only. |
| `AGENT_LOCAL_SESSION_TTL_DAYS` | NEW. Lifetime of a new sign-in, default 30 days, never extended by use. |
| `AGENT_LOCAL_LOGIN_RATE_LIMIT_PER_15MIN` / `AGENT_LOCAL_LOGIN_ACCOUNT_RATE_LIMIT_PER_15MIN` / `AGENT_LOCAL_LOGIN_RATE_LIMIT_PER_HOUR_PER_IP` | NEW. Sign-in windows: per account per address (5, also a recognized browser's own window), per account from every address (50, counting only attempts the per-address windows let through) and per address (20 an hour). |
| `AGENT_LOCAL_SIGNUP_RATE_LIMIT_PER_HOUR` / `AGENT_LOCAL_SIGNUP_RATE_LIMIT_PER_DAY` | NEW. Sign-ups per client address, 5 an hour and 10 a day. |
| `AGENT_LOCAL_SIGNUP_REQUIRE_RESOLVED_CLIENT_IP` / `AGENT_LOCAL_UNRESOLVED_SIGNUP_RATE_LIMIT_PER_HOUR` / `AGENT_LOCAL_UNRESOLVED_SIGNUP_RATE_LIMIT_PER_DAY` | NEW. Sign-ups whose client address is unknown share one budget, 5 an hour and 10 a day. With the first set to `true` they are refused (503) instead. |
| `AGENT_LOCAL_EMAIL_PROVIDER` | NEW. `smtp` (default), `tencent_ses`, or a registered transport. No fallback between them. |
| `AGENT_TENCENT_SES_REGION` / `AGENT_TENCENT_SES_FROM_EMAIL` / `AGENT_TENCENT_SES_FROM_NAME` | NEW. Tencent SES region (`ap-guangzhou` or `ap-hongkong`), approved sender address and display name, with the `AGENT_TENCENT_CLOUD_*` credentials. |
| `AGENT_TENCENT_SES_TEMPLATE_VERIFICATION` / `AGENT_TENCENT_SES_TEMPLATE_PASSWORD_RESET` / `AGENT_TENCENT_SES_TEMPLATE_APPROVAL` / `AGENT_TENCENT_SES_TEMPLATE_REJECTION` / `AGENT_TENCENT_SES_TEMPLATE_ADMIN_SIGNUP` / `AGENT_TENCENT_SES_TEMPLATE_NOTICE` | NEW. Approved SES template ids (0: none). Verification and password reset are required; any other mail without its own template goes through the notice template. |
| `AGENT_SMTP_HOST` / `AGENT_SMTP_PORT` | Mail relay for verification, password-reset and address-change links and the account notices. Port defaults to 587, or 465 with `AGENT_SMTP_SECURITY=tls`. Without a host and sender, email sign-up is closed while verification is required, and recovery answers 503. |
| `AGENT_SMTP_SECURITY` | `starttls` (default), `tls` for implicit TLS, or `none` for a local relay. |
| `AGENT_SMTP_USERNAME` / `AGENT_SMTP_PASSWORD` | Optional relay credentials. Never logged. |
| `AGENT_SMTP_FROM` | Sender address of account mail, e.g. `Cloud Agent <no-reply@example.org>`. Links in it are built from `AGENT_WEB_BASE_URL` only. |

### Sandbox archives

Automatic lifecycle archives and **Archive to S3 and delete** share one policy,
set only in **Admin → Settings → Sandbox**; no environment variable sets it or
its destination. Archives go to `s3://<bucket>/cloud-agent/sandbox`, and the
bucket defaults to `freeinference`. The policy holds the bucket, region, upload
size limits, and either an AWS access key, whose secret is encrypted with the
settings key and never returned by the admin API, or workload identity fields
(a customer-managed KMS key ARN and the expected bucket owner). Without an
access key, sandbox managers use the standard AWS SDK credential chain. Each
Sandbox Manager keeps the local state of both, restorable checkpoints included,
in `AGENT_SANDBOX_ARCHIVE_STATE_DIR`. See
[Sandbox archives](docs/developer/operations.md#sandbox-archives) for IAM,
bucket versioning and rollout.

| Variable | Value / who provisions |
|---|---|
| `AGENT_CONTROL_PLANE_COMPOSE_OVERRIDE_FILE` | *Optional.* Absolute path to a private Compose override, kept outside the checkout and limited to the main `control-plane` service. `deploy.sh` requires a readable regular file and applies it after the release's own overlays. |
| Standard AWS SDK credentials | Needed only when the policy has no saved access key: the main control plane then signs and verifies deletion archives through the SDK chain, preferably with an IAM workload role. The base Compose file forwards no AWS variables, so put environment or shared-profile credentials in `AGENT_CONTROL_PLANE_COMPOSE_OVERRIDE_FILE`. No AWS credential reaches a sandbox, the web app or the MCP listener. |

[Sandbox archive storage](docs/developer/operations.md#sandbox-archive-storage)
lists the permissions, retention and verification requirements.

### Behaviour switches read by the control plane

| Variable | Value / who provisions |
|---|---|
| `COMPOSE_PROFILES` (`edge`, `pgadmin`, `github-relay`) | Read by `docker compose`, not by the service. `edge` starts the nginx edge for a deployment with its own hostname: `/api/*` goes to the control plane with the prefix stripped, everything else to the web app. `pgadmin` starts the private database UI. `github-relay` starts the GitHub relay (see `AGENT_SANDBOX_GITHUB_RELAY_CA_FILE`). Combine them with commas, for example `edge,pgadmin`. |
| `AGENT_EDGE_BIND` / `AGENT_EDGE_PORT` | Where the edge listens; defaults `127.0.0.1` / `3003`. Only meaningful with the `edge` profile. |
| `AGENT_CONTROL_PLANE_BIND` / `AGENT_CONTROL_PLANE_PORT` | Compose main-API binding, defaults `127.0.0.1` / `8000`. Put a TLS reverse proxy in front of public access. |
| `AGENT_MCP_RELAY_BIND` / `AGENT_MCP_RELAY_PORT` | Compose MCP-only listener binding, defaults `127.0.0.1` / `8001`. Keep sandbox tools on this listener. |
| `AGENT_MODEL_RELAY_BIND` / `AGENT_MODEL_RELAY_PORT` | NEW. Compose model relay binding, defaults `127.0.0.1` / `8002`. The `model-relay` service always runs and refuses every request while no provided connection exists. It adds the deployment's provider key to requests that carry a running turn's placeholder, so only the hosts' `model-tunnel` should reach it; keep it off the public edge. See [Provided models](docs/developer/operations.md#provided-models). |
| `AGENT_WEB_BIND` / `AGENT_WEB_PORT` | Compose web-server binding, defaults `127.0.0.1` / `3002`. |
| `AGENT_EDGE_CONFIG_FILE` | Host-side nginx configuration mounted read-only by the edge service. Default `./edge.nginx.conf`, relative to the Compose file. |
| `AGENT_SITE_CONTENT_DIR` | Host-side directory of [site content files](#site-content-files) mounted read-only by the control plane at `/etc/cloud-agent/site`. Default `./site-content`, relative to the Compose file: the recipe's empty directory, which configures none of them. |
| `AGENT_NETWORK_NAME` | Control-plane Compose network name, default `cloud-agent`. Use distinct names for independent stacks. |
| `AGENT_SESSION_COOKIE_SECURE` | *Initial import*, default on. Set `false` **only** for a whole stack on plain `http://localhost`: a cookie without `Secure` can be taken by anyone on the network path. On, the session and device cookies are `__Host-cloud_agent_local_session` and `__Host-cloud_agent_device`, names another host on the same site cannot set. Off, they use the plain names `cloud_agent_local_session` and `cloud_agent_device`, which are never read while it is on. |
| `AGENT_API_DOCS` | *Initial import.* Saved as **Serve API documentation** in Admin → Settings → Operations, under Advanced; default off, checked on every request. On, the main API serves `/docs`, `/redoc` and `/openapi.json`; turn it on only on a development stack, because the schema is unauthenticated and the two pages load scripts from a CDN at a floating version. The MCP-only listener never serves them. |
| `AGENT_MAX_STREAMS_PER_USER` | *Initial import.* Saved as **Live streams per account** in Admin → Settings → Operations, under Advanced (default `24`), checked when a stream opens: how many live-update streams (the change stream and job event streams) one account may hold open on one control-plane process. One more answers 429 with `Retry-After`, which the web app backs off on. A browser tab holds one change stream plus one per conversation it watches. |
| `AGENT_TRUST_PROXY_HEADERS` | *Initial import*, default off. Set `true` only when a proxy you control sits directly in front of the control plane and writes `X-Forwarded-For`; both shipped nginx configurations overwrite it with the peer address. The audit trail records only the rightmost entry, the one that proxy wrote. Behind a further hop, such as a tunnel or load balancer in front of the edge, the recorded address is that hop's unless the edge uses nginx's realip module as its configuration describes. |
| `AGENT_RUNTIME_REGISTRY_URL` | *Initial import.* Saved as **Agent package registry** in Admin → Settings → Agent versions; empty is `https://registry.npmjs.org`. Version lists come from it, and each heartbeat answer tells hosts to download from it. Until every online host reports the saved registry, the Agent versions tab names the hosts still missing it and updates are refused. On a host, this variable is only the registry before its first heartbeat answer. An unreachable registry blocks updating and nothing else. |
| `AGENT_DEPLOYMENT_SHA` | The exact commit this API serves. The production Compose stack requires it and `deploy/control-plane/deploy.sh` exports it; do not edit it by hand during a rollout. A host replica claims new retained groups only while it advertises the completed marker for this commit, so a pending or failed host deployment cannot claim through a freshly started API. The local stack generates its own marker. |

### Source control

Every variable here except `AGENT_GITHUB_APP_PRIVATE_KEY_PATH` and
`AGENT_GITHUB_APP_PRIVATE_KEY_FILE` is an *initial import* into Admin → Settings
→ Source control (`AGENT_GITHUB_API` under Advanced).

| Variable | Value / who provisions |
|---|---|
| `AGENT_GITHUB_APP_ID` | The GitHub App used for repository access. Setting it turns on key loading; an unreadable key makes GitHub unavailable and is retried every 30 seconds. Configure the App's callback and setup URLs for this deployment ([credentials](docs/developer/operations.md#2-credentials)). |
| `AGENT_GITHUB_APP_CLIENT_ID` | GitHub App OAuth client ID for users connecting their own GitHub account. |
| `AGENT_GITHUB_APP_CLIENT_SECRET` | GitHub App OAuth client secret. Store outside Git; rotate when changing the App's audience. |
| `AGENT_GITHUB_APP_PRIVATE_KEY_PATH` | *Bootstrap.* Path inside the container to a read-only PEM key; the supported Compose file mounts it at `/etc/cloud-agent/github-app.pem`. A key saved in Admin Settings takes precedence. Keep private keys out of environment variables, `docker inspect` output and logs. |
| `AGENT_GITHUB_APP_PRIVATE_KEY_FILE` | Host-side key file mounted read-only by control-plane Compose at `/etc/cloud-agent/github-app.pem`. Default `/dev/null` supplies no key. This differs from the in-container `_PATH` setting. |
| `AGENT_GITHUB_APP_PRIVATE_KEY` | Inline PEM, imported once where a deployment already supplies it. Compose does not forward it. Set or replace an inline key in Admin Settings, where it is encrypted and never returned to the browser. |
| `AGENT_GITHUB_APP_INSTALL_URL` | `https://github.com` authorization/installation URL used by the Connect GitHub flow; any other host, scheme or port is refused when saved or imported. The App, OAuth credentials and this URL must all be configured for user connections. |
| `AGENT_GITHUB_COAUTHOR` | NEW. The co-author agents name in their commits, as `Name <email>`. Use the App bot's noreply address, `<bot-user-id>+<app-slug>[bot]@users.noreply.github.com`, so GitHub shows its avatar beside the owner's. Empty: commits name the owner only. Agents always author commits as the conversation's owner. |
| `AGENT_GITHUB_API` | Default `https://api.github.com`: where the control plane sends GitHub App, token and repository requests. GitHub Enterprise Server is not supported: Connect GitHub uses GitHub.com's OAuth and installation URLs, and the GitHub relay serves only `github.com` and `api.github.com`. |
| `AGENT_GITLAB_OAUTH_CLIENT_ID` | GitLab.com OAuth application client ID for user connections. |
| `AGENT_GITLAB_OAUTH_CLIENT_SECRET` | GitLab.com OAuth client secret; store outside Git. |
| `AGENT_GITLAB_OAUTH_REDIRECT_URI` | Public OAuth callback URI registered with GitLab. Must use HTTPS, except for localhost development, and anything else is refused when saved or imported; update the registration when the deployment URL changes. |
| `AGENT_REPO_ALLOWLIST` | Comma-separated `owner/name` entries or `owner/*` rules, matched case-insensitively. Not per user: every user admitted to Cloud Agent may have the platform clone the listed repositories with this deployment's GitHub App, without connecting GitHub, so list only repositories all of them may work on. It grants no push: agents push and open pull requests only with their owner's own GitHub connection, through the GitHub relay. Empty grants no allowlist access. |

### MCP

Cloud Agent owns MCP discovery, per-user credentials, authorization and attempt
fencing. Users add remote URLs, imported `mcpServers` snippets or stdio launch
specs; templates only prefill the form, and environment variables neither add
servers nor grant access to them. Credentials live per user in
`agent_mcp_connections`, encrypted with
[`AGENT_SOURCE_CONTROL_ENCRYPTION_KEY`](#own-secrets). The public base URL,
GitHub sign-in and both `ALLOW` switches below are *initial imports* into
Admin → Settings → MCP, all but GitHub sign-in under Advanced; the switches apply to new MCP transports after the MCP
listener restarts. The MCP tunnel is a host setting
([Tunnels and relays](#tunnels-and-relays)).

| Variable | Value / who provisions |
|---|---|
| ~~`AGENT_MCP_CONFIG`~~ | *Removed*; nothing reads it. It named one file of shared MCP servers and credentials that every user used under one identity. Connections are per user; delete it from `.env`. |
| `AGENT_MCP_PUBLIC_BASE_URL` | *Optional override* of the public API base for MCP OAuth callbacks, including the API prefix, for example `https://example.com/agents/api`. The callback is `<base>/v1/agent/mcp/oauth/callback/<connection-id>`, after which the browser returns to `<AGENT_WEB_BASE_URL>/integrations`. Unset, `AGENT_PUBLIC_API_URL` is used; never derived from request headers. With neither, browser authorization is unavailable and static credentials still work. |
| `AGENT_MCP_GITHUB_CLIENT_ID` / `AGENT_MCP_GITHUB_CLIENT_SECRET` | *Optional legacy import* for GitHub MCP browser sign-in. Register a service-owned GitHub OAuth App or GitHub App with callback `<public API base>/v1/agent/mcp/oauth/callback/github`. With both values and a public callback base, the preset defaults to browser sign-in and each user authorizes their own account. The client secret stays in encrypted settings; user tokens stay in their MCP connections. Prefer a GitHub App limited to the needed repositories and permissions (Contents and Pull requests read and write when agents publish changes); an OAuth App requests `repo` and `read:org`, and `repo` permits writes. A personal access token remains an option for narrower access. |
| `AGENT_MCP_ALLOW_PRIVATE_TARGETS` | Whether a user-added MCP connection may dial private and loopback addresses. Off by default: a connection that reaches loopback reaches **this** service. Turn it on only for MCP servers on a private network you trust. The ranges the platform never dials, such as the cloud metadata service, stay refused. |
| `AGENT_MCP_ALLOW_PLAIN_HTTP` | Whether a user-added MCP connection may use `http://`. Off by default, because the credential would travel in the clear; it is separate from the private-target switch because it is a separate risk. |
| `AGENT_MCP_SERVERS` | **Not operator-set.** The runner publishes the relay endpoints under this name, as one JSON object of `{alias: url}`, for the image's CLI wrappers to merge into their CLI's MCP configuration. A hand-set value would point a sandbox at servers nobody connected. |
| `AGENT_MCP_TOKEN` | **Not operator-set.** The generated sandbox runtime config references `${AGENT_MCP_TOKEN}` for the relay credential the runner injects at launch, the chat process's residency credential. A hand-set value would be a static tool credential shared by every job. |

### Model connections

| Variable | Value / who provisions |
|---|---|
| `AGENT_MODEL_CONNECTIONS_ENABLED` | Whether owners may add model connections (OpenAI Chat Completions or Responses, Anthropic Messages) and start turns on them, and whether turns start on connections the deployment provides. On unless set to `0`, `false` or `no`. Connections are the only inference, so off stops every new turn; turns already admitted still run or fail. Set it to `false` to hold new turns during maintenance or while some host or control-plane replica still runs an earlier release, then unset it. |
| ~~`AGENT_DEFAULT_MODEL`~~ | **removed**. A new task starts on the model and connection of its owner's newest turn, or the owner picks one; nothing reads this. |

### Web search

| Variable | Value / who provisions |
|---|---|
| `WEB_SEARCH_PROVIDER` | *Optional initial import.* Provider behind the built-in `web_search` tool: `auto` (default), `serper`, `brave` or `parallel`. Saved in Admin → Settings → Web search; an existing deployment imports it once on upgrade if it was never saved. The supported control-plane Compose file does not pass it: set it in Admin Settings, or pass it through an override for the import. |
| `SERPER_API_KEY` / `BRAVE_SEARCH_API_KEY` / `PARALLEL_API_KEY` | *Optional initial import.* Provider keys for the built-in `web_search` tool, stored encrypted in Admin → Settings → Web search and never sent to a sandbox. Imported once, as above. |

### Sandbox settings from the admin pages

These host settings are set from the admin pages, not in each host's `.env`:
`AGENT_TIMEOUT_S`, `AGENT_LEASE_TTL`, `AGENT_CHAT_IDLE_TIMEOUT_S`,
`AGENT_SOURCE_IMPORT_MAX_MIB`, `AGENT_SHARED_RESIDENT_CAPACITY`,
`AGENT_STORAGE_MIN_AVAILABLE_MIB`, `AGENT_SANDBOX_CPU_MILLICORES`,
`AGENT_SANDBOX_MEMORY_MIB`, `AGENT_SANDBOX_DISK_MIB`,
`AGENT_TERMINAL_IDLE_TTL_SECONDS`, `AGENT_TERMINAL_HARD_TTL_SECONDS`,
`AGENT_TERMINAL_MAX_SESSIONS` and the limits in the table below. **Admin →
Settings → Sandbox** sets a value for every server (`PUT /admin/agent/host-settings`),
with the terminal, workspace and storage limits and **Turn lease** under
Advanced, and a server's details on **Servers** set one for that server alone
(`PUT /admin/agent/runner-hosts/<host>/settings`). A host uses, highest first:

1. the value set for that server;
2. the value set for every server;
3. the built-in default.

Each heartbeat answer carries the complete effective values, so a host applies a
change within about 30 seconds, without a redeploy; clearing a value falls back
to the next level. A host's environment values apply only until its first
settings answer. Its first complete heartbeat also imports its non-default values
once as that server's values, unless an administrator has already set or reset
them. The registry, units and bounds are in
[`cloud_agent_shared/host_settings.py`](apps/backend/cloud_agent_shared/host_settings.py);
the bounds are typo guards, not capacity statements.

Nothing running is interrupted. A turn keeps the time limit it started with, and
a sandbox keeps the session capacity and CPU, memory and disk limits it was
created with; Fork members share them. The sandbox manager applies terminal
limits from its next sweep. The page shows a value as *applying* until the host
confirms it. A runner that does not advertise `host-settings-v1` keeps its
`.env` values until it is redeployed, and the page says so.

CPU is entered in vCPU and stored in millicores (1 vCPU = 1000); memory and disk
take MiB or GiB. The defaults are `AGENT_SANDBOX_CPU_MILLICORES=2000`,
`AGENT_SANDBOX_MEMORY_MIB=4096` and `AGENT_SANDBOX_DISK_MIB=0` (no disk quota).

A positive disk limit sets Docker's `--storage-opt size=<MiB>M` on the writable
layer: the workspace, caches and installed files, not the image or memory-backed
temporary files. It needs Docker `overlay2` on XFS mounted with project quotas
(`pquota` or `prjquota`); otherwise Docker refuses to create the container. A
positive limit is recommended on every server that runs `untrusted-kata-v1`,
whose sandboxes otherwise share one unbounded disk. A server at 0 still takes
untrusted work; it logs a warning, and the Servers page shows **No container
disk quota**. Apart from this limit, disk is guarded only at admission, by
`AGENT_STORAGE_MIN_AVAILABLE_MIB`.

| Variable | Initial default / purpose |
|---|---|
| `AGENT_TERMINAL_MAX_SESSIONS_PER_WORKSPACE` | 4 open terminals per shared workspace. |
| `AGENT_WORKSPACE_COMMAND_TIMEOUT_S` | 60 seconds for workspace commands and Git inspection. |
| `AGENT_WORKSPACE_FILE_WRITE_MAX_MIB` | 2 MiB for one workspace file replacement. |
| `AGENT_WORKSPACE_FILE_PREVIEW_MAX_KIB` | 512 KiB for one file preview. |
| `AGENT_WORKSPACE_COMMAND_OUTPUT_MAX_MIB` | 2 MiB of workspace command output. |
| `AGENT_WORKSPACE_DIRECTORY_MAX_ENTRIES` | 5,000 entries per directory listing. |
| `AGENT_WORKSPACE_DOWNLOAD_MAX_MIB` | 1,024 MiB for one whole-workspace download, measured before compression. |
| `AGENT_TERMINAL_BUFFER_MAX_MIB` / `AGENT_TERMINAL_BUFFER_MAX_EVENTS` | 2 MiB / 4,096 events retained for terminal reconnects. |
| `AGENT_TERMINAL_OUTPUT_MAX_MIB` | 32 MiB of output over one terminal's lifetime. |
| `AGENT_TERMINAL_INPUT_MAX_KIB` | 64 KiB of input per terminal request. |
| `AGENT_TERMINAL_MAX_STREAMS_PER_SESSION` | 4 concurrent viewers per terminal. |

The Sandbox Manager runs at most 16 workspace commands and Git inspections at
once per host and 4 per workspace; one more is refused with 429. A command that
times out or overflows its output limit is stopped with its own process group,
so other sessions and terminals in the shared container keep running. A native
CLI record over 8 MiB stops its member process. Events are capped at 256 KiB,
and text Postgres cannot store (a NUL or an unpaired surrogate) becomes U+FFFD.

### Optional compute-resource management

These values belong only to the stable main control plane, not its MCP-only
listener, any Host or any sandbox. See
[the compute runbook](docs/developer/operations-compute.md) for deployment
profiles, verified SSH access and CloudLab setup. Without them, manual Host
management still works.

| Variable | Meaning |
| --- | --- |
| `AGENT_COMPUTE_SANDBOX_IMAGE_REPOSITORY` | Where automatic Server setup looks for this release's sandbox image, `<repository>:sandbox-<commit>`. Default `ghcr.io/harvardmadsys/cloud-agent-sandbox`; empty turns the lookup off. When the image exists (checked anonymously), the generated Host configuration pulls `<repository>:sandbox-{deployment_sha}` with `AGENT_SANDBOX_IMAGE_PULL=true`, so its chats record a registry digest and can be restored on another Host. Otherwise the Host builds its own image and its chats stay on it. |
| `AGENT_COMPUTE_SECRET_KEY` | *Initial import*, generated when unset: the key, at least 32 bytes, for stored compute connections and Admin-managed deployment profiles, itself stored encrypted under the settings root key. Later `.env` values are ignored, so you can delete the line; back up the settings root key with the database. |
| `AGENT_COMPUTE_CONFIG` | Absolute path inside the control-plane process to the operator-owned JSON deployment configuration. |
| `AGENT_COMPUTE_DIRECTORY` | Absolute host directory mounted read-only at `/run/compute` by the optional Compose overlay; `deploy.sh` includes the overlay when this is set. |
| `AGENT_CONTROL_PLANE_SSH_HOST` | First-time Server setup address of the service node. The supported deployer defaults it to the node's FQDN, else its hostname; set it for another deployment system or SSH alias. Saved setups take precedence. |
| `AGENT_CLOUDLAB_API_URL` | *Initial import.* CloudLab's HTTPS Portal API, `https://boss.emulab.net:43794`, not the portal's web address `www.cloudlab.us`. Unset disables CloudLab. |
| `AGENT_COMPUTE_REGISTRATION_URL` | *Initial import.* Optional HTTPS address new CloudLab nodes post their SSH host keys to, ending in `/v1/compute/node-registrations`. Unset, it is `AGENT_PUBLIC_API_URL` plus that path, when that URL is HTTPS. A node checks the control plane's certificate before sending its one-time token. Without either, nodes that register their keys cannot be created. |

### Repository environments

Reusable repository environments are always on; nothing configures them, and a
leftover `repository_environments_enabled=false` setting or
`AGENT_REPOSITORY_ENVIRONMENTS_ENABLED=false` is ignored. A task with no
variables or scripts uses the base environment without preparation; a configured
workspace needs an environment-capable runner and sandbox manager.

## Host agent (`cloud_agent_host`)

### The stack's own identity

`deploy/host/deploy.sh` and `deploy/host/sandbox_host.sh` read these, not
Compose: they name the stack rather than configure it.

| Variable | Value / who provisions |
|---|---|
| `COMPOSE_PROJECT` | Required host-stack project name, with no host default. It prefixes containers, images and networks, and reusing a name lets a second deployment recreate the first stack, so give sibling environments distinct names such as `cloud-agent-sandbox-staging` and `cloud-agent-sandbox-prod`. The control-plane deploy script defaults its own project to `cloudagent`. |
| `AGENT_HOST_STATE_ROOT` | Host path for enrollment state and readiness markers; host Compose default `/var/lib/hybridinference/agent-host`. Use a unique path per execution environment (required when `SIBLING_COMPOSE_PROJECTS` is set) and back it up with retained host state. |
| `AGENT_RUNNER_STOP_GRACE` / `AGENT_SANDBOX_MANAGER_STOP_GRACE` | Compose shutdown windows, defaults `5m` / `30s`. The runner drains active children; sandbox manager cleanup follows the runner drain. |
| `SIBLING_COMPOSE_PROJECTS` | Comma-separated projects on this machine that belong to the other environment. The deploy guard reports containers shaped like this stack under any project it does not know, so name each legitimate sibling here. Setting it makes `AGENT_HOST_STATE_ROOT` required: sharing that path would share an enrollment credential. |
| `SHARED_READY_TIMEOUT` | Seconds the deploy scripts wait for the two parallel rollout halves, default 900: the host for cold-start MCP and control endpoints, the control deploy for a fresh exact-SHA heartbeat and reverse sandbox manager probes. A rollout watchdog, not a chat timeout. |

### Scheduling

| Variable | Notes |
|---|---|
| `AGENT_HOST_CREDENTIAL` | What `/v1/agent/host/register` returns. It authenticates and names this machine's heartbeats and claims; there is no shared claim credential. Removing the host revokes it. The runner normally reads the persisted file (`AGENT_HOST_CREDENTIAL_FILE`) instead. |
| `AGENT_LEASE_TTL` | Attempt lease length, default 120 seconds. *Host bootstrap*: the admin **Turn lease** setting (41 to 900 seconds, for all servers or one) replaces it from the next heartbeat, and a host imports this value once as its own server setting. A turn renews with the lease it was claimed with. See [Runner lease failures](docs/developer/runner-lease-troubleshooting.md). |
| `AGENT_TIMEOUT_S` | Hard limit on one running Agent CLI turn, default 3600 seconds. Reaching it fails and fences the turn; it has nothing to do with chat idleness or the sandbox lifecycle. The admin pages override it. |
| `AGENT_RUNNER_HOST` | Execution environment name redeemed by `AGENT_ENROLL_TOKEN` at first registration. After enrollment the credential decides the identity, and changing this value renames nothing; use `AGENT_PHYSICAL_HOST` to group environments for display. |

### Sandbox Manager, runtime and reporting

| Variable | Notes |
|---|---|
| `AGENT_SANDBOX_MANAGER_URL` | How a runner reaches its sandbox manager, normally the Compose service URL. The control plane always finds a sandbox manager through the attempt's enrolled host row, never through this. |
| `AGENT_SANDBOX_MANAGER_ADVERTISE_URL` | The sandbox manager URL the runner reports at enrollment, when it differs from the one it dials. `deploy/host/docker-compose.sandbox.yml` requires it, even on the control plane's machine, because separate Compose networks cannot resolve the service name; only the integrated local stack can omit it. Set it to the far end of the reverse forward (`AGENT_SANDBOX_MANAGER_REVERSE_BIND`/`_PORT`) and never without that tunnel, or every terminal, file listing and Git status answers 503. |
| `AGENT_SANDBOX_MANAGER_TOKEN` | The pool-wide sandbox manager credential: the control plane and every host hold the same value. The control plane generates it into its first settings row (or imports it), and server setup writes it into each host. Host records hold URLs, not separate credentials, so a compromised host's token authenticates to every other sandbox manager. When provisioning by hand, compare SHA-256 fingerprints rather than display the secret; a mismatch shows as 401 answers. |
| `AGENT_SANDBOX_MANAGER_REVERSE_BIND` / `AGENT_SANDBOX_MANAGER_REVERSE_PORT` | Required reverse listener for a standalone host. Bind the control plane's stable Docker bridge gateway address, never loopback or `0.0.0.0`, and give each simultaneously deployed Host its own port. The advertised URL must match. |
| `AGENT_SANDBOX_MANAGER_REVERSE_TARGET_HOST` / `AGENT_SANDBOX_MANAGER_REVERSE_TARGET_PORT` | Host-side target of that reverse forward, defaults `sandbox-manager` / `8092`. |
| ~~`AGENT_TUNNEL_*`~~ (~~`AGENT_TUNNEL_SSH_DESTINATION`~~) (~~`AGENT_TUNNEL_SSH_PORT`~~) (~~`AGENT_TUNNEL_SSH_KEY`~~) (~~`AGENT_TUNNEL_SSH_KNOWN_HOSTS`~~) (~~`AGENT_TUNNEL_REMOTE_HOST`~~) (~~`AGENT_TUNNEL_REMOTE_PORT`~~) (~~`AGENT_TUNNEL_LISTEN_PORT`~~) | **removed**, refused by the host: the inference tunnel is gone. |
| ~~`AGENT_GATEWAY_HOSTNAME`~~ | **removed**, refused by the host. |
| `AGENT_SANDBOX_MANAGER_URL_ALLOWED_HOSTS` | *Initial import.* Comma-separated allowlist of the addresses hosts may register as their sandbox manager, which then receive the pool credential. A bare hostname admits that host on any scheme and port; a `scheme://host:port` origin admits only that origin, which separates sandbox managers behind one reverse-tunnel address. Unset accepts any hostname; a multi-host pool should name its machines. URLs must be bare origins, and link-local addresses, where cloud metadata answers, are always refused. Automatic CloudLab setup expects the bridge address as a bare hostname entry. |
| `AGENT_CONTROL_PLANE_URL` | Runner URL for registration, claims and heartbeats. Standalone Compose derives it from the main-API tunnel alias `AGENT_CONTROL_PLANE_HOSTNAME`, not the MCP alias. Pointing it at the MCP-only listener returns 404 for control requests. |
| `AGENT_WORKER_ID` | Worker-name prefix, default `runner`. The runner appends process identity so concurrent turn children have distinct worker names. |
| `AGENT_REPO_CLONE_BASE_URL` | Base the trusted runner builds source clone URLs from, default `https://github.com`; deployment configuration, never job input. The complete local stack points it at its private `git://local-repo` fixture. `http`, `https` and `git` work for trusted mirrors, but a clone token is sent only over HTTPS. |
| `AGENT_SOURCE_IMPORT_MAX_MIB` | Largest repository a new chat's first turn may import, in MiB; default 1024, accepted 1 to 65536. It counts the working tree **and** the fetched commit's Git objects, which store most content a second time, so 600 MB of files can already exceed 1024. Past it the turn fails before the agent starts with `source checkout … exceeds this host's … retained import size limit`, and a follow-up fails the same way: raise the limit or shrink the repository. The clone stops at the limit, so it never fills the disk. Each concurrent first turn spools the archive to the runner container's disk, not memory, and the `docker cp` timeout grows by two minutes per GiB. The admin pages override it. |
| `AGENT_TERMINAL_IDLE_TTL_SECONDS` | Closes an interactive terminal after this long with no input or output, default 1800 seconds. It never stops the chat container. The admin pages override it. |
| `AGENT_TERMINAL_HARD_TTL_SECONDS` | Longest lifetime of one terminal, even while in use, default 8 hours; it bounds how long a terminal's credentials stay exposed. The admin pages override it. |
| `AGENT_TERMINAL_MAX_SESSIONS` | Interactive terminals the sandbox manager holds open at once across all chats, default 64, accepted 1 to 4096. The admin pages override it. |
| `AGENT_RETAINED_DOCKER_DATA_ROOT` | Expected daemon `DockerRootDir`; host and local Compose default `/var/lib/docker`. A mismatch at sandbox manager startup fails retained mode closed. To keep sandboxes on a data disk, bind `<disk>/docker` onto `/var/lib/docker` and `<disk>/containerd` onto `/var/lib/containerd` and leave this at `/var/lib/docker`; a non-default daemon `data-root` or containerd `root` is rejected ([Retained Docker storage](docs/developer/operations.md#retained-docker-storage-on-a-host)). The path is never mounted into sandbox managers or sandboxes. It is not a checkpoint or an off-node backup. |
| `AGENT_CONTAINERD_CONTENT_DIR` | Where the Sandbox Manager sees containerd's content store, read-only. Code and host Compose default `/var/lib/hybridinference/containerd-content`, bound from `AGENT_HOST_CONTAINERD_CONTENT_DIR` (default `/var/lib/containerd/io.containerd.content.v1.content`, the system containerd Docker uses for its image store). A restorable checkpoint then copies only the commit's new layers; without the store it streams the whole image through `docker image save`, slower but equivalent. Docker's own managed containerd keeps the store under `/var/lib/docker/containerd/daemon/io.containerd.content.v1.content`. |
| `AGENT_UPLOAD_RECEIPT_DIR` | Attachment-import receipts, shared read-write by the runner and sandbox manager. Host Compose sets `/var/lib/hybridinference/upload-receipts` and binds only `${AGENT_HOST_STATE_ROOT}/upload-receipts` into the sandbox manager, not the directory holding the host credential. Keep it durable so imports stay incremental across service replacement. Unset, direct execution uses `upload-receipts` beside `AGENT_HOST_CREDENTIAL_FILE`. |
| `AGENT_SANDBOX_ARCHIVE_STATE_DIR` | Sandbox Manager-only directory for stopped-container exports, deletion receipts and checkpoint state (`checkpoints/`, `checkpoint-spool/`). Code default `/var/lib/hybridinference/broker-archives`; host Compose binds `${AGENT_HOST_STATE_ROOT}/sandbox-archives` there. Keep it durable so a replacement sandbox manager can finish a verified deletion, and never mount it into a sandbox. Exports are mode 0600 and hold repository files and native session state, without credential tmpfs. Archives over 5 GiB are refused and the original kept; at most four requests hold pending archives per sandbox manager. |
| `AGENT_SHARED_RESIDENT_CAPACITY` | Member slots (separate UIDs and private tmpfs) built into each new shared container, default 8, accepted 2 to 128. Fork reserves a free slot first. It bounds the chats in one group; `AGENT_HOST_SLOTS` bounds the turns a machine runs at once. The admin pages override it. |
| `AGENT_CHAT_IDLE_TIMEOUT_S` | The one inactivity clock for a retained chat group, default 1800 seconds. Turns, file and Git operations and terminal activity all advance it. When it expires, the host runs `docker stop`, releases the group's bridge and keeps the container and its writable layer; it never removes them. Waking rebuilds the bridge and host rules before `docker start`. The admin pages override it. |
| `AGENT_ENROLL_TOKEN` | One-time enrollment token, minted at `POST /admin/agent/runner-hosts/enroll-token` for a named `host` and redeemed once at `POST /v1/agent/host/register` for the host credential. It lasts 15 minutes, is hashed at rest and is shown once: if you lose it, issue another. Registration under any other host name is refused without spending it, so a typo cannot revoke a live machine's credential. |
| `AGENT_HOST_CREDENTIAL_FILE` | Where the host credential persists, written mode 0600. The code default is `/var/lib/hybridinference/agent-host-credential`, but host Compose sets `${AGENT_HOST_STATE_ROOT:-/var/lib/hybridinference/agent-host}/credential`, so **on a deployed host it lives under `AGENT_HOST_STATE_ROOT`**; name that path when rebuilding or re-enrolling. `AGENT_HOST_CREDENTIAL` in the environment wins over the file. |
| `AGENT_DEPLOYMENT_SHA` / `AGENT_SHARED_LIVE_READY_FILE` | The rolling-deployment fence, managed by the deploy scripts; never set them to choose behavior. `deploy/host/deploy.sh` supplies the SHA and `sandbox_host.sh` keeps the marker under `AGENT_HOST_STATE_ROOT`. A runner does not poll for work until the `shared-sandbox-deployment:<sha>` marker for its commit is complete, which also protects the previous API during a parallel rollout. |
| `AGENT_HOST_SLOTS` | How many turns this host runs at once until an admin sets a number on the Servers page (`PUT /admin/agent/runner-hosts/<host>/slots`); default 3, accepted 2 to 128 (a fork runs beside its parent). The runner claims only while under the limit and starts one process per turn. A new number arrives on the heartbeat answer and applies without restarting anything; running turns are never interrupted. Lowering applies at once, raising once the host confirms. |
| `AGENT_RUNNER_REPLICAS` | *Removed*, not read. Concurrency is `AGENT_HOST_SLOTS` and the admin page; `sandbox_host.sh` warns while it is set. |
| `AGENT_RUNNER_VERSION` | Free-text version for the pool listing; never a compatibility gate. |
| `AGENT_HOST_CAPACITY_ENABLED` | Whether the runner samples its Docker host's CPU, memory and retained-container count for the Servers page; default on, `0`/`false`/`no`/`off` turns it off (shown as not measured). Host Compose mounts only `/proc/stat`, `/proc/meminfo` and `/proc/sys/kernel/random/boot_id` read-only under `/run/host-metrics`, never all of `/proc` or `pid: host`, because a shared pid namespace would show sandbox neighbours every process on the machine. Sampling is best effort: a missing file is a missing figure. |
| `AGENT_HOST_STORAGE_PROBE` | How the Servers page's Storage column measures the filesystem under the sandbox writable layers. `overlay_root` (default) reads the runner's own root filesystem, which under OverlayFS describes that disk; `off` stops measuring and reaches running replicas only after `up`. The host script refuses any other value. A figure is published only after `sandbox_host.sh` has attested the topology in `.storage-probe/verdict.json`, comparing the container's numbers with `df` of the mount root holding the upper directory. A failed check never blocks a deployment: the runner reports `unverified`. |
| `AGENT_STORAGE_MIN_AVAILABLE_MIB` | Free space a host keeps for new sandboxes, in MiB; default 1024, accepted 1 to 1048576. Before a turn creates a sandbox, the runner reads available space through the attested storage probe and declines the turn when less remains; its launch error suggests freeing storage or changing this setting. Existing sandboxes still resume. Unknown or unverified capacity never counts as pressure. An admission guard, not a quota ([storage measurement](docs/developer/operations.md#how-full-is-the-sandbox-storage)). The admin pages override it. |
| `AGENT_LOG_LEVEL` | Log level, default `INFO`. The control plane imports it once into Admin → Settings and applies changes without restarting; host processes read it at startup. |
| `AGENT_LOG_FORMAT` | `text` (default) or `json`, one object per record with the structured `extra` fields, uvicorn's included. The control plane imports it once into Admin → Settings and applies changes without restarting; host processes read it at startup. An unknown value warns and uses text. Pipe `docker logs …` to `jq` to read JSON. |
| `AGENT_OPERATION_PROFILING_ENABLED` | Legacy static switch. With no `MODE`, `true` selects environment mode, which needs a valid `RUN`; unset or `false` leaves control to Admin, with collection off until an administrator starts a run. Ignored under `MODE=admin`. In environment mode every producer (control plane, runner, sandbox manager) needs the same descriptor. Read once per process. |
| `AGENT_OPERATION_PROFILING_RUN` | JSON descriptor of a static run: `run_id`, a non-empty `operations` list, an absolute UTC `expires_at`, a positive `max_operations` and an optional `target` naming `host`, `job_id`, `thread_id` or `sandbox_id`. The operations are `attempt.startup`, which the runner roots, and `files.list`, `files.open` and `terminal.open`, which the control plane roots. An invalid descriptor leaves profiling off with one warning. A run cannot outlast `expires_at`, even across restarts; at expiry its record is emitted as partial (`profiling_stopped: "expired"`). `max_operations` is a per-process budget, not a fleet-wide one. |
| `AGENT_OPERATION_PROFILING_SINK` | Optional JSONL path for finished measurements, which are logged as well; an external benchmark reads it. Delivery to Postgres and Admin does not need it. |
| `AGENT_OPERATION_PROFILING_MODE` | Optional override of who controls collection: `admin`, `environment` or `disabled`. Unset or blank means Admin, except that legacy `ENABLED=true` keeps environment mode. `disabled`, or an invalid value, forbids collection and makes the Admin switch unavailable. Read once per process; ordinary Enable and Disable need no restart. The control plane and runner sync in the background (about 30 seconds idle, 5 seconds active), and Disable stops new selection while in-flight work keeps its deadline. See the [default-control design](docs/agents/specs/2026-09-22-measurement-admin-default.zh-CN.md). |
| `AGENT_OPERATION_PROFILING_DEPLOYMENT` | Which deployment's control row this control plane commands, default `default`. Set it when deployments share a database (staging and production), so neither can see or stop the other's experiment. It comes from configuration, never from a request; a malformed value falls back to `default` with a warning. |

The Compose files pass these through to each producer. Collection starts
disabled on a fresh database. The Admin Enable/Disable button controls the
persisted run without a restart, and a restart resumes an unexpired run. While
collection is disabled no operation reads the database, though the background
policy sync still runs. The Admin API reports `max_operations` with
`budget_scope=producer_instance`.

Two benchmarks answer different questions.
`scripts/bench_startup_profiling_overhead.py` measures the collector's own cost
on a synthetic attempt, with no container or network.
`scripts/bench_operation_overhead.py` drives the real control-plane routes and
database and, where a container runtime is available, the real supervisor; it
reports per-operation p50/p95 against an uninstrumented cohort and the
`max(10 ms, baseline p95 × 1%)` budget verdict. Read each script's
`what`/`not_what` block before quoting either.

### Tunnels and relays

A host reaches the control plane only through SSH tunnels, each with its own
narrowly restricted key.

| Variable | Notes |
|---|---|
| `AGENT_MCP_TUNNEL_*` | MCP-only SSH tunnel. Required suffixes: `SSH_DESTINATION`, `SSH_KEY`, `SSH_KNOWN_HOSTS`. Defaults for `SSH_PORT` / `REMOTE_HOST` / `REMOTE_PORT` / `LISTEN_PORT`: `22` / `127.0.0.1` / `8001` / `8000`. Its remote destination must be the MCP-only listener; main API port 8000 serves no tools. |
| `AGENT_MCP_HOSTNAME` | The relay's alias on both container networks. Defaults to `agent-mcp`. |
| `AGENT_SANDBOX_MCP_URL` | *Host only.* The MCP relay as this host's sandboxes reach it. Host Compose derives it from the MCP tunnel (`http://<AGENT_MCP_HOSTNAME>:<LISTEN_PORT>`) for the runner and sandbox manager, which hand it to every member CLI with tools; the host rules admit it. The control plane does not read it. Without it, a turn whose owner connected a tool fails before any container work, telling the owner to ask an admin to finish setting up the site. |
| `AGENT_CONTROL_PLANE_TUNNEL_*` | Runner-only SSH tunnel to the main API. Required suffixes: `SSH_DESTINATION`, `SSH_KEY`, `SSH_KNOWN_HOSTS`. Defaults for `SSH_PORT` / `REMOTE_HOST` / `REMOTE_PORT` / `LISTEN_PORT`: `22` / `127.0.0.1` / `8000` / `8000`. It joins only the runner network and is unreachable from sandboxes. |
| `AGENT_CONTROL_PLANE_HOSTNAME` | That tunnel's alias, on the runner network only. Defaults to `agent-control-plane`. |
| `AGENT_SANDBOX_GITHUB_RELAY_CA_FILE` | NEW. *Host only.* The GitHub relay CA's certificate on this host, made by `deploy/control-plane/github-relay-certs.sh`. With it and the `github-relay` Compose profile on this host and the control plane, group bridges resolve `github.com` and `api.github.com` to the host's `github-tunnel` and members trust the CA, so git and `gh` reach GitHub as the conversation's owner through the control plane's GitHub relay. Unset, sandboxes reach GitHub directly and anonymously. See the [design](docs/agents/specs/2026-11-04-agent-native-pull-requests.zh-CN.md). |
| `AGENT_GITHUB_TUNNEL_*` | NEW. The GitHub relay's SSH tunnel, started by the `github-relay` profile. Required with it: `SSH_DESTINATION`, `SSH_KEY`, `SSH_KNOWN_HOSTS`. Defaults for `SSH_PORT` / `REMOTE_HOST` / `REMOTE_PORT`: `22` / `127.0.0.1` / `8443`; it listens on 443 under the names `github.com` and `api.github.com`. Its remote destination must be the GitHub relay's listener (`AGENT_GITHUB_RELAY_BIND` / `AGENT_GITHUB_RELAY_PORT` on the control plane, with its certificate and key in `AGENT_GITHUB_RELAY_TLS_DIR`). |
| `AGENT_SANDBOX_MODEL_RELAY_URL` | NEW. *Host only; written by Admin → Servers → Add server.* Where this host's sandboxes reach the model relay: `http://` plus the `model-tunnel` alias and port (`http://agent-models:8000` by default), a name, never an address. Set, `sandbox_host.sh` requires the model tunnel's destination, key and known_hosts, every group bridge carries the relay, and the host advertises `model-relay-v1` and receives turns on connections the deployment provides. Unset, it receives none; turns on owners' own connections are unaffected. See [Provided models](docs/developer/operations.md#provided-models). |
| `AGENT_MODEL_TUNNEL_*` | NEW. *Written by Add server.* The model relay's SSH tunnel. Its `model-tunnel` service runs on every host and waits idle without `SSH_DESTINATION`. Required with `AGENT_SANDBOX_MODEL_RELAY_URL`: `SSH_DESTINATION`, `SSH_KEY`, `SSH_KNOWN_HOSTS`. Defaults for `SSH_PORT` / `REMOTE_HOST` / `REMOTE_PORT` / `LISTEN_PORT`: `22` / `127.0.0.1` / `8002` / `8000`; `AGENT_MODEL_RELAY_HOSTNAME` (default `agent-models`) is its name on a group bridge. Its remote destination must be the model relay's listener. Add server gives it its own key, with a `permitopen` for that port only, on its own account on the control-plane machine; set these by hand only on a host Add server did not set up. |

### Physical servers and execution environments

`AGENT_RUNNER_HOST` identifies one enrolled execution environment. Two stacks on
one physical server that talk to one control plane need distinct names,
credentials, state roots and sandbox manager URLs. Do not rename or re-enroll an
existing environment to merge its retained chats with another.

`AGENT_PHYSICAL_HOST` names the physical server in the admin inventory: give
every environment on it the same name, unique within the control plane.
`deploy/host/sandbox_host.sh` defaults it to the short hostname; direct Compose
deployments must set it. Runners send it at registration and on each heartbeat
(`labels.physical_host`). Set it before host activation and follow the
[rolling deployment order](docs/developer/operations.md#rolling-deployment-order).

The Servers table shows, for each physical server, the executor that matches the
new-chat execution policy. Keep one placement executor per physical server, and
retire extra old deployments once their retained conversations are no longer
needed. Slots are an environment's concurrency limit, not GPU counts. The global
sandbox location is described in
[Compute resources](docs/developer/operations-compute.md).

## Sandbox isolation (host agent reads, sandbox inherits)

| Variable | Notes |
|---|---|
| `AGENT_SANDBOX_BACKEND` | Defaults to `kata`. `container` is the explicit trusted-repository downgrade. `process` is a non-claimable test seam and cannot serve retained chats. |
| `AGENT_SANDBOX_RUNTIME` | Unset selects `io.containerd.kata.v2` with backend `kata`, or Docker's default runtime with backend `container`. Set `runc` only for the trusted execution profile. The untrusted profile requires exactly `io.containerd.kata.v2`; gVisor needs a separate execution profile. |
| `AGENT_SANDBOX_IMAGE` | Required sandbox image, with no application default. Use distinct tags for sibling environments, such as `cloud-agent-job:staging` / `:prod`, so one rebuild cannot replace the other's image. This is the job image, distinct from the runner image. |
| `AGENT_SANDBOX_IMAGE_PULL` | `true` makes a source deployment (`sandbox_host.sh`) pull `AGENT_SANDBOX_IMAGE` instead of building it; default `false`. Use the image published for the deployed commit, for example `ghcr.io/harvardmadsys/cloud-agent-sandbox:sandbox-<sha>` (an autoscaling template can write `{deployment_sha}`). A pulled image is recorded by its registry digest, so another host can restore this host's chats from their checkpoints; a locally built image cannot be fetched elsewhere, so its groups stay on this host. Artifact deployments always pull. |
| `AGENT_SANDBOX_NAMESPACE` | *Required.* Stable deployment namespace stamped onto retained containers and group bridges, so sibling stacks on one Docker daemon cannot discover, stop or prune each other's chats. It also names this deployment's nftables table and bridge-interface prefix; a runner or sandbox manager without one refuses to start. The host stack uses its Compose project; the complete local stack derives it from `LOCAL_PROJECT_NAME`. |
| `AGENT_SANDBOX_DOCKER` | Docker executable used by the host backend, default `docker`. With the default, the runner and sandbox manager read container and network state from the daemon's Engine API, on the socket `DOCKER_HOST` or the default context names, instead of starting the executable for every read. Any other executable keeps every Docker call on it. |
| `AGENT_SANDBOX_HOME` | Sandbox user home, default `/home/agent`. |
| `AGENT_SANDBOX_UID` / `AGENT_SANDBOX_GID` | Sandbox non-root identity, defaults `10001` / `10001`. |
| `AGENT_SANDBOX_EXTRA_ARGS` | Additional Docker arguments parsed with `shlex.split`. Retained chats reject unsafe options; this is trusted deployment configuration, never job input. |
| `AGENT_MEMBER_PROCESS_REAPER_SOURCE` | *Internal.* Runner-side path to the root-owned `member-process-reaper` helper, copied into a retained container created by an older compatible image. The runner image supplies `/usr/local/libexec/member-process-reaper`; operators normally never set it. |
| `AGENT_SANDBOX_ALLOW_UNISOLATED` | **Isolation escape hatch.** It only opens the `process` backend, a non-claimable test seam that can never serve a retained chat. It must be unset in any environment untrusted repositories can reach. |
| `AGENT_EGRESS_CHAT_NETWORK_POOL` | *Required.* This deployment's RFC1918 IPv4 pool; Docker allocates one `/29` per active group bridge (a `/16` holds 8192). No default. It must not overlap another deployment on the machine (the host rules refuse the later of two overlapping pools) or any network the host already routes (LAN, VPN, private services): preflight refuses such a pool, because a bridge there would draw the host's own traffic. Idle and lost stops remove a group's bridge at once; reconciliation removes stale bridges from interrupted creates after five minutes. |
| `AGENT_EGRESS_DNS_SERVER` | *Required.* The one IPv4 resolver sandboxes may use, on TCP and UDP 53. It is the only exception to the protected ranges, so a site resolver on a private address, or on this host, works. Under runc Docker's embedded resolver forwards to it; a Kata guest is given it in `/etc/resolv.conf` by the trusted guest helper. Not loopback: a sandbox cannot reach the host's `127.0.0.53`. |
| `AGENT_EGRESS_PROTECTED_CIDRS` | Comma-separated IPv4 blocks sandboxes must not reach, on top of the built-in host, private, link-local/metadata, CGNAT, multicast and reserved ranges, for example an institution's services on public addresses. It can only add protection. The control plane reads the same variable when it checks an owner's SSH server, so set it there to the hosts' value. |
| `AGENT_RELAY_NETWORK` | The route-less network the MCP relay is discovered on; no sandbox joins it. Group lifecycle attaches the relay found here, under its alias, to each group's bridge. The host stack derives `<COMPOSE_PROJECT>-agent-relays`, so sibling stacks never find each other's relays; set it only to name an existing network. |
| ~~`AGENT_EGRESS_SETUP_TIER`, `AGENT_EGRESS_AGENT_TIER`, `AGENT_EGRESS_ALLOWLIST`, `AGENT_EGRESS_NETWORK_*`, `AGENT_EGRESS_PROXY_*`, `AGENT_EGRESS_STOP_GRACE`, `AGENT_SANDBOX_NETWORK`, `AGENT_SANDBOX_ALLOW_OPEN_NETWORK`~~ | *Removed* with the network tiers and the Squid proxy: sandboxes have one network ([design](docs/agents/specs/2026-09-23-agent-public-egress-design.zh-CN.md)). Nothing reads them; delete them from `.env`. |
| `AGENT_COMPAT_CLIENT` / `AGENT_COMPAT_UPSTREAM_URL` / `AGENT_COMPAT_UPSTREAM_KEY` / `AGENT_COMPAT_MODEL` / `AGENT_COMPAT_TOKEN` / `AGENT_COMPAT_REASONING_EFFORT` / `AGENT_COMPAT_UPSTREAM_RELAY` | **Not operator-set.** What a host hands the protocol compatibility launcher when Codex or Claude Code runs on a Chat Completions connection: the CLI's protocol, the upstream URL and key, the model, the member's loopback token from the claim, and the turn's reasoning level (sent upstream as `reasoning_effort`, absent at Auto). `AGENT_COMPAT_UPSTREAM_RELAY=1` marks the host's own model relay as the upstream, for a provided model; only then is a plain `http` upstream accepted, and the key is the member's placeholder. The launcher removes all of them before it becomes the CLI, so the CLI never holds the provider key. |
| `AGENT_LIVE_SESSION_ID` / `AGENT_LIVE_WORKDIR` | *Internal.* Generated by the runtime adapter for the private OpenCode/Kilo stdio bridge. Operators must not set these. They select the already-authorized native session and fixed `/workspace` path inside one member process; neither is a credential or host path. |
| `AGENT_PLUGIN_PATHS` | *Internal.* Generated by the host plugin installer for pi, OpenCode, and Kilo. A JSON array of absolute paths to the selected plugins installed in the member's private home; defaults to `[]`. CLI wrappers load these paths explicitly while retaining disabled project plugin discovery. Operators must not set this; each chat's saved selections determine it. |
| `AGENT_ACTION_UID` | *Internal.* Set by the sandbox manager on a repository environment action it starts in a member's terminal: the scoped UID (31000–31063) the action runs as. Operators must not set it. |
| `AGENT_ENVIRONMENT_SCRIPT` / `AGENT_SCRIPT_STATUS` | *Internal.* Passed by the sandbox manager to the wrapper that runs one repository environment script (`environment_process.py`): the script, and where to record its exit status. The wrapper removes both from its environment before the script starts. Operators must not set them. |

### Kata runtime qualification generation

The supported host deployment mounts `/var/lib/freeinference/kata-runtime`
read-only at `/run/agent-kata-runtime` in the runner. After a verified install,
its root-owned `epoch.json` holds
`{ "version": 1, "state": "ready", "generation": "<UUID>" }`.
`setup_kata_runtime.sh` takes a host-wide lock, publishes `updating` before it
changes the runtime, links or configuration, and publishes a new ready
generation only after installation and verification succeed. A failed update
stays `updating`; rerun the installer to repair it. `--check` only verifies.
Every deployment on the host sees the same generation.

On an existing host, deployment verifies Kata and creates only a missing marker;
it never replaces a generation or clears an incomplete update. A missing epoch
means full probes, and a malformed or `updating` one fails closed. Change the
runtime or its configuration only through the supported installer, so every
runner invalidates qualification before the first changed file.

### Complete local stack database recovery

`deploy/local/dev.sh` keeps the local Postgres password and matching
`AGENT_DATABASE_URL` in the gitignored `deploy/local/.env.local`; back it up with
any retained local Docker volume. If the file is missing while the Compose
project still owns `agent-postgres`, the script refuses to generate a new
password: PostgreSQL applies `POSTGRES_PASSWORD` only when it initializes an
empty data directory, so a new value cannot unlock the old volume. Restore the
original `AGENT_POSTGRES_PASSWORD` and `AGENT_DATABASE_URL`; the script never
deletes the volume or resets its password.

`up` builds the images first. `up` and `start` then start Postgres alone, wait
for it, check the DSN from the control-plane image and run Alembic before
starting the rest.

### Claude inference retry policy

For each resident Claude Code process the runtime adapter sets
`CLAUDE_CODE_MAX_RETRIES=15` and `CLAUDE_CODE_RETRY_WATCHDOG=0`. These are fixed
launch settings, not host `.env` options. The CLI retries transient inference
errors with exponential backoff and jitter within the native turn;
`AGENT_TIMEOUT_S` still bounds the whole turn, and exhaustion fails the turn
but keeps its native context. See
[Runtime boundaries](docs/developer/operations.md#runtime-boundaries).

### Agent CLI version pins (image build)

The five default versions are pinned in both
`deploy/sandbox/Dockerfile.agent-sandbox` and
`apps/backend/cloud_agent_shared/shared_protocol.py`. The image installs and
labels them, and runners and the control plane use the matching constants to
decide whether `shared-sandbox-v1` may be advertised or claimed, so they are
source pins, not `.env` settings. Change both together and rerun the agent-loop
conformance suite. The Dockerfile checks each exact version against the
installed binary; a dist-tag (`latest`) or range (`^2.1`) fails the build.

**Updating an agent without a rebuild.** Claude Code, Codex, OpenCode and pi can
be updated from the admin Settings page (_Agent versions_ → _Update to …_).
Every host downloads that exact npm release into a local image beside the
sandbox image, checks it with one real turn against a model on loopback, and
reports progress on its heartbeat. Once every online host holds it, new chats
use it and existing chats keep their version; _Roll back_ returns new chats to
the previous one. Kilo runs only its pin, a source build carrying a reviewed
isolation patch. See the
[design](docs/agents/specs/2026-10-15-agent-cli-updates-design.zh-CN.md).

| Variable | Notes |
|---|---|
| `AGENT_RUNTIME_REGISTRY_URL` | Host side of the control-plane row above: the registry a host uses before its first heartbeat answer. Default `https://registry.npmjs.org`. |
| `AGENT_RUNTIME_DOWNLOAD_NETWORK` | The Docker network the download step runs on. Default `bridge`. Only npm runs there, with install scripts disabled; the packages' own scripts and the check run later with no network. Name another network only when the host's default bridge cannot reach the registry. |

Changing a pin claims that the new version's event stream still fits its adapter
in `cloud_agent_host/runtimes/`. `tests/external/test_runtime_stream_conformance.py`
tests that claim for `claude-code` only; the other four runtimes have contract
fixtures and adapter unit tests, so their pin changes need careful review. The
suite reads the candidate from the Dockerfile's `ARG` line and runs that exact
version with `npx` against a fake model provider, so **put the version on the
`ARG` line** first:

```bash
# deploy/sandbox/Dockerfile.agent-sandbox
ARG CLAUDE_CODE_VERSION=2.2.0

# then, from the checkout — no image build and no deployment needed
uv run pytest -m external tests/external/test_runtime_stream_conformance.py
```

A pass shows that the normalizer understands the version's events. It does not
exercise the deployed image, the sandbox or the dropdown.

## Web (`apps/frontend/`)

Frontend configuration is public and may not contain secrets. Build-time routing
settings are baked into the bundle and need a rebuild. Appearance is saved in
Admin → Settings and read by the web server per request. Deployment time and
release identity are runtime metadata.

### Build-time: the image variant

A `NEXT_PUBLIC_*` variable is substituted into the bundle at `next build`, so it
must be an `ARG` in `deploy/control-plane/Dockerfile.web` *and* a key under the
`web` service's `build.args`; `test_web_build_arguments_are_wired` fails
otherwise. Changing one means a rebuild.

The web app is served at the root of its own hostname and calls the control
plane at `/api` on the same origin, which the edge routes. Path-prefix mounting
is not supported: `deploy.sh` and the release deployer refuse a leftover
`AGENT_WEB_BASE_PATH`. The build rejects a relative API value other than `/api`;
an absolute URL is accepted for cross-origin routing.

| Variable | Compose build argument | Notes |
|---|---|---|
| `NEXT_PUBLIC_AGENT_API_URL` | Set to `/api` | Browser API base, fixed at `/api` on the same origin. A build that needs a cross-origin control plane passes an absolute URL, which needs control-plane CORS with credentials because authentication uses a session cookie. |
| `NEXT_PUBLIC_BUILD_SHA` | `BUILD_SHA` | Optional build identity, baked in by the deploy script. |
| `NEXT_PUBLIC_BUILD_TIMESTAMP` | `BUILD_TIMESTAMP` | Optional build identity. |

### Runtime: the deployment's identity

The main control plane imports the first six variables below into Admin →
Settings when it first initializes its settings: set them before the first
start, then change them in Admin. The web server reads the saved values on every
request through `GET /v1/agent/site-config`, so one image serves any deployment.
A web server without `AGENT_CONTROL_PLANE_URL` reads them from its own
environment, for standalone development.

| Variable | Notes |
|---|---|
| `AGENT_WEB_APP_NAME` | Product name in the header and the document title. Empty shows `Cloud Agent`. |
| `AGENT_WEB_ORG_NAME` | Optional organisation name beside the product name. Empty hides it. |
| `AGENT_WEB_ORG_URL` | Link target for `AGENT_WEB_ORG_NAME`. |
| `AGENT_WEB_STATUS_URL` | Status page, opened in a new tab from the header. Empty hides the link. |
| `AGENT_WEB_DOCS_URL` | The deployment's user documentation, opened in a new tab from Help in the account menu and from the My Settings page. Empty hides the link. |
| `AGENT_WEB_CONTACT_URL` | Where users reach the deployment's operators: an http(s) support page, or `mailto:` and a single address (no `?subject=` or other fields). Shown as Contact us under Help in the account menu. Empty hides the link. |
| `AGENT_WEB_DEPLOYED_AT` | ISO timestamp with a timezone for the deployment footer. The control-plane deploy script sets the activation time in UTC and records it in `.deployed-at` after successful health checks; redeploying the same release keeps it. When starting Compose by hand, supply the real deployment time. Empty shows `Not available`. |

The web service also receives `AGENT_DEPLOYMENT_SHA` for the footer, which shows
the first seven characters with the full commit in a tooltip. Saved URLs and
booleans are validated before an admin update is committed; the standalone
fallback validates them when rendering.

### Site content files

A deployment's Terms of Service, Team page and source link are files in the
deployment's own repository, not environment variables or Admin settings, so a
change is reviewed there and goes live with the next deployment. Copy the
directory to the control-plane host and point `AGENT_SITE_CONTENT_DIR` at it.
The control plane picks up a changed file on the next request, without a
restart. Every file is optional:

| File | Contents |
|---|---|
| `terms.en.txt` | Terms of Service in English, plain UTF-8 text; blank lines separate paragraphs. |
| `terms.zh-CN.txt` | The same in Simplified Chinese. `/terms` shows the reader's language, else English. |
| `site.yaml` | `source_url`, the GitHub link in the footer, and `members`, the Team page. |

Any Terms file replaces the `AGENT_LOCAL_TERMS_URL` and `AGENT_LOCAL_TERMS_TEXT`
settings, which Admin then shows read-only. Without `site.yaml` the footer
hides the Team and GitHub links.

```yaml
source_url: https://github.com/example/cloud-agent
members:
  - name: Ada Lovelace
    website: https://ada.example.org/   # optional
    badge: {en: Lead, zh-CN: 负责人}      # optional
    roles:                               # optional, at most 10
      - {en: Professor, zh-CN: 教授}
      - Visiting scholar                 # one text for every language
```

A label is either text or a mapping of `en` and `zh-CN` to text. URLs must be
absolute http(s). An unknown key, an invalid value or a file over 256 KiB is
logged as `site_content_ignored` and the file is treated as absent, so check
the files before deploying them.

## Infrastructure (not environment variables)

Decide these per deployment; record the answers in the private overlay.

- **Domains**: one host for the web app and the control-plane API, the API
  under `/api`, for example `agents.example.org` and a staging sibling. The
  names go in `AGENT_WEB_BASE_URL` and `AGENT_PUBLIC_API_URL`.
- **What a sandbox reaches**: its MCP relay, the resolver in
  `AGENT_EGRESS_DNS_SERVER` and public IPv4 on TCP 80/443, the same during setup
  and the agent run. Model calls go to each owner's provider over that public
  HTTPS; tool calls stay on the MCP-only listener. A sandbox holds its owner's
  provider key for the turn it runs; that exposure is
  [accepted](docs/agents/specs/2026-09-23-agent-public-egress-design.zh-CN.md)
  and limited to the owner's own key and quota.
- **Sandbox image registry**: where hosts pull `AGENT_SANDBOX_IMAGE` from.

## Adding a variable

1. Add a row here, in the right section, marked NEW.
2. Say who provisions it and whether it differs per environment.
3. For a secret, say where it is stored, never the value.
4. Classify it in `tests/support/env_inventory.py`.
   `tests/unit/test_environment_inventory.py` and
   `tests/unit/test_env_classification.py` fail on a variable the code reads
   without a row here or an entry there.
