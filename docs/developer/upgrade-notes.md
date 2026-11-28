# Upgrade notes

What an operator upgrading an existing deployment needs to know about recent
releases and their migrations. A new installation starts from an empty
database and can skip this page. The deployment order and rollback are in
[rolling deployment order](operations.md#rolling-deployment-order) and
[deployment reliability](deployment-reliability.md).

## Before starting this release

Before starting this release, run `alembic upgrade head` through
`0120_operator_model_budgets`. The supported deployment scripts migrate before
replacing the API. Every migration here is additive and safe under the running
previous release unless it says otherwise; the few that need stopped writers
are in
[applying migrations that are not backwards-compatible](operations.md#applying-migrations-that-are-not-backwards-compatible).
After migrating, start the main control-plane listener with its complete
existing environment, so it imports newly registered settings once
([Admin Settings](operations.md#admin-settings)).

## One destination for every sandbox archive

**Archive to S3 and delete** now uses the **Sandbox archives** policy under
**Admin → Settings → Sandbox**, the one sandbox managers already use: its
bucket and region, the prefix `cloud-agent/sandbox/deletion`, and its saved
access key if it has one ([how](operations.md#sandbox-archive-storage)). Its
four own settings under **Admin → Settings → Application → Sandbox archives**
(`sandbox_archive.bucket`, `.region`, `.endpoint` and `.prefix`) and their
`AGENT_SANDBOX_ARCHIVE_S3_*` imports are gone; the stored values are no longer
read, and a later release removes them. Before upgrading:

- Deletion with an archive works only while the policy is enabled. Otherwise
  an administrator can only discard a sandbox, and a deleted chat's sandbox is
  retired without an archive. A deployment that archived to its own bucket
  enables the policy first, with that bucket if it should stay the
  destination.
- There is no S3-compatible endpoint any more; the policy names an AWS bucket.
- With a saved access key the control plane needs no AWS credentials of its
  own, so a Compose override that only supplied them
  (`AGENT_CONTROL_PLANE_COMPOSE_OVERRIDE_FILE`) can go once this release runs.
  The key needs `s3:PutObject` and `s3:GetObject` on
  `cloud-agent/sandbox/deletion/*`, which the documented lifecycle grant on
  `cloud-agent/sandbox/*` includes.

Completed deletions keep their receipts, each with the full `s3://` location
it was verified at. A request not yet archived goes to the policy's
destination when it next runs.

## Drain a host without `ADMIN_TOKEN`

An orchestrator can drain a host with
`python -m cloud_agent.deployment.host_fence` in the control-plane container
instead of the admin API, without `ADMIN_TOKEN`
([how](deployment-reliability.md#reaching-the-fence-without-a-credential)).
The running control plane answers the drain, so switch only once every
control plane it drains through runs this release. `ADMIN_TOKEN` can then go,
unless you keep it as the recovery door; without it,
`python -m cloud_agent.accounts.bootstrap_admin` in the same container
restores an administrator.

## No `.env` needed

A control plane no longer needs a `.env`, and releases no longer ship
`deploy/control-plane/.env.example` or `deploy/host/.env.example`. The database
connection, the bundled database's password, the account key and the settings
root key live in the `agent-keys` Docker volume, kept by the Compose `keys`
service ([how](../../ENVIRONMENT.md#settings-and-bootstrap-configuration)). A
host's `.env` is written by Admin → Servers → **Add server**.

An existing deployment keeps working. On its first start on this release, the
`keys` service stores the `.env` values of `AGENT_DATABASE_URL`,
`AGENT_POSTGRES_PASSWORD`, `AGENT_LOCAL_SESSION_SECRET`,
`AGENT_SETTINGS_ENCRYPTION_KEY` and `AGENT_SESSION_SECRET` in the volume, and a
value still in `.env` keeps winning. Keep those lines while a rollback is
possible: an earlier release reads them only from `.env`, and its Compose file
refuses to start without them. Before deleting them, save the keys apart from
the database ([Disaster recovery](disaster-recovery.md)), because
`docker compose down -v` deletes the volume. `deploy.sh` no longer requires a
`.env` or writes keys into one. A new deployment's control plane also
generates the sandbox manager credential into its settings; an existing one
keeps its own.

The host build-time `*_EXTRA_VERSIONS` menus are gone: the sandbox image
carries only its reviewed pins, and other versions of Claude Code, Codex,
OpenCode and pi are installed from Admin → Settings → Agent versions. Before
upgrading a host, check its `.env` for `CLAUDE_CODE_EXTRA_VERSIONS`,
`CODEX_EXTRA_VERSIONS`, `PI_EXTRA_VERSIONS`, `OPENCODE_EXTRA_VERSIONS` and
`KILO_EXTRA_VERSIONS`. A version listed there that is selected in Admin or
used by a retained chat is not in the new image. Selecting it before the
upgrade does not help, because the old image still carries it and the update
marks it ready without downloading it. After deploying the new image, check in
Admin → Settings → Agent versions that every online host offers the version,
and run **Update** to it again where one does not. Until it reports ready, new
chats there run the default version, and chats that need the old one cannot be
forked. The lines are then ignored and can be deleted.

First-run setup also asks for the site address, prefilled from the page's own
address, and ends on Admin → Servers.

## A deployment can provide models

An administrator can save a provider's endpoint and key once, under
**Admin → Settings → Models**, and every admitted owner runs on the models the
provider lists for that key, with no setup
([Provided models](operations.md#provided-models)). The relay records each
request's tokens and their cost at the listed prices and limits nothing; the
provider's budget on the key does. Owners' own connections are unchanged, and
nothing changes for a deployment that adds no provided connection.

`0118_operator_model_connections` only adds tables, and
`0120_operator_model_budgets` adds prices, budgets and recorded costs to them.
Builds between them let an administrator choose the models, a default model, a
daily budget per account, prices, caps on requests in flight, an end-user
header and per-account answers; none of that is read any more. A connection
saved on such a build keeps its model list, verification and prices until it
is saved again with a new key or Base URL, or its models are read again,
which replaces the list with the key's. Its budgets, caps and per-account
answers stay in the database, unread, until a later revision drops them.

The feature needs two services, which every stack now runs with no profile:
`model-relay` on the control plane and `model-tunnel` on each host. Remove
`model-relay` from `COMPOSE_PROFILES` if you added it. A server that
**Add server** sets up from this release gets its model tunnel and
`AGENT_SANDBOX_MODEL_RELAY_URL`. A server set up earlier, or not by Add
server, keeps working with an idle `model-tunnel` and gets no turns on
provided models until it is set up again. Roll hosts first, as usual; a host
on the previous release does not advertise `model-relay-v1` and gets no such
turns either.

After a rollback, a host that Add server set up from this release still names
its model relay, but the previous release starts its `model-tunnel` only with
the `model-relay` profile: add the profile to that host's `COMPOSE_PROFILES`,
or its turns on provided models fail. Rolling back needs no downgrade while no
provided connection exists. Once one does, delete it and let its conversations
go first: the previous release cannot read a turn that names one.

## Inference provisioning is withdrawn

The option to invite approved accounts to a connected inference service
(`accounts.inference_provisioning_*`, off by default, added one release
earlier) is removed. A deployment that wants its accounts on its own models
[provides them](#a-deployment-can-provide-models) instead; its key stays in the
control plane.

- The four settings and their `AGENT_LOCAL_INFERENCE_PROVISIONING_*` variables
  are no longer read; remove the variables. `0119_drop_account_provisioning`
  deletes the stored values, including the dispatch token, and drops the two
  tables.
- `python -m cloud_agent.accounts.provision_backfill` is gone.
- Keys already issued at the inference service are not revoked by the
  upgrade, and this service no longer revokes one when an account's access
  ends. Revoke them there if they should stop working.
- The audit log no longer records invitations or revocations; existing rows
  still read as before.

## The bundled database is the one `AGENT_POSTGRES_DB` names

The bundled `postgres` service's healthcheck, pgAdmin's bundled server and the
documented backup, drain and undrain commands open the database
`AGENT_POSTGRES_DB` names (`cloudagent` by default). A deployment that went
through the [cutover](#independent-accounts-cutover) on the bundled service by
pointing `AGENT_DATABASE_URL` at a new database there, and left
`AGENT_POSTGRES_DB` unset, still has all of them on the archived database:
pgAdmin and `psql` show its old state, and the documented backup dumps it. The
`keys` service warns at every start while the two differ. To fix it:

1. Set `AGENT_POSTGRES_DB` in `.env` to the database `AGENT_DATABASE_URL` uses,
   and deploy. Postgres restarts with its healthcheck on that database. Both
   databases stay, since `POSTGRES_DB` creates nothing in a data directory
   that already holds one.
2. In pgAdmin, edit the server it imported at its first setup and set its
   maintenance database to the same name.
3. Take a new backup. Every dump taken before step 1 is of the archive.
4. Optionally, rename the archive so nothing opens it by default, for example
   `ALTER DATABASE cloudagent RENAME TO cloudagent_archive`, run from another
   database with no session on the archive. Record the new name with its dump
   and keys.

## Postgres and the edge run pinned images

The control plane's Compose file now pins the bundled `postgres` image and the
optional `edge` image by version tag and multi-architecture index digest, as
it already pinned pgAdmin: `postgres:16.16-alpine@sha256:0adbf25a…` and
`nginx:1.27.5-alpine@sha256:65645c7b…`. They were named by tag alone
(`postgres:16-alpine`, `nginx:1.27.5-alpine`), and artifact deployments pull
every image on each deploy, so a new upstream build could restart Postgres on
another minor version during an unrelated deploy. A new Postgres or nginx now
comes only with a release that changes the pin, and this page says when.

The first deploy of this release recreates `postgres`, and `edge` where its
profile is enabled, once; Postgres is unavailable for the few seconds of the
restart. Check the running version first with
`docker exec "${COMPOSE_PROJECT:?}-postgres-1" postgres --version`: an earlier
16.x moves up to 16.16, and a later 16.x moves back to it. A minor release
keeps the data format, so neither needs a dump or restore, but read the
PostgreSQL release notes in between for any steps they ask for. nginx stays on
1.27.5, and pgAdmin's pin is unchanged.

## The audit log names who acted on whom

**Admin → Audit log** names the administrator and the account of each action,
files account decisions under **Accounts**, and by default shows only people:
administrators' sessions and break-glass access (`ADMIN_TOKEN`, first-run
setup, the bootstrap command). Automation, the control plane's own workers and
policy, and accounts changing themselves are one switch away. A request whose
work runs later, such as a sandbox deletion or a new server, shows its final
result. Erasing an account no longer deletes the audit rows about it, and an
audit row that cannot be written no longer fails the action it records; the
page reports the miss
([reading the trail](operations.md#reading-a-tasks-recorded-events)).

## Agents open their own pull requests

The platform no longer publishes workspaces: there is no **Create PR** or
**Update PR** button, no `POST /v1/agent/threads/{thread_id}/publish`, no
workspace snapshot and no `agent/<thread>` branch. When the owner asks, the
agent commits, pushes a branch and opens the pull request itself, as the
owner, through [the GitHub relay](operations.md#the-github-relay); without the
relay, agents cannot push. **Watch PR and merge** has the agent use git and
`gh api` REST calls. Follow-up turns start from the conversation's own base.
Pull requests the publisher opened stay on GitHub as they are, and nothing
pushes to their `agent/…` branches again.

The control plane no longer reads `AGENT_GITHUB_TOKEN`,
`AGENT_PUBLISH_BASE_BRANCH`, `AGENT_GIT_AUTHOR_NAME` or
`AGENT_GIT_AUTHOR_EMAIL`, and Admin → Settings has no Publication group;
delete those lines from the control plane's `.env`. Commits are authored as
the conversation's owner, with `AGENT_GITHUB_COAUTHOR` as co-author.

The publisher's tables (`agent_sandbox_snapshots`, `agent_snapshot_jobs`,
`agent_pr_archive_checks`) and `agent_jobs.published_commit_sha` stay, unused,
until a migration in the next release drops them. This release is an ordinary
rolling deploy, and rolling back needs no downgrade and loses nothing.

## MCP servers run in two places

An MCP connection is an online service (`remote`) or a program in the chat's
sandbox (`sandbox`). The two locations nothing ever ran, `managed-worker` and
`user-connector`, are gone: the API refuses them, as it refuses
`connector_id`, and connection views no longer carry `connector_id`.
`0115_mcp_retire_dead_locations` moves any connection stored at either to
`sandbox`, with its command unchanged. It runs when its command passes the
sandbox's rules (`npx` or `uvx` and one exactly pinned package, no mounts or
working directory) and is left out of chats otherwise; its owner can
disconnect and add it again, or edit its command through the API. To count
such rows before upgrading:

```sql
SELECT location, count(*) FROM agent_mcp_connections
WHERE location IN ('managed-worker', 'user-connector') GROUP BY location;
```

The connector tables (`agent_mcp_connectors`,
`agent_mcp_connector_connections`) and `agent_mcp_connections.connector_id`
stay, unused, until a later release drops them. During the rollout, the
previous release refuses to store a connection at a removed location. Rolling
back needs no downgrade: the previous release reads the moved rows as
programs.

## Shorter `.env` files

The example `.env` files now hold only what Admin cannot manage: the database
connection, the root keys, the recovery token, container wiring, and on a host
its tunnels, identity and sandbox boundaries. Every other value is set in
Admin → Settings, or per server on the Servers page, and a missing required
setting is named at the top of the Admin pages.

An existing `.env` keeps working: the first start of a release that registers
a setting imports its value once, and from then on the database wins. After
the control plane or host has started once on this release, these lines can
be deleted:

| Where | Lines that can be deleted |
|---|---|
| Control plane | `AGENT_WEB_BASE_URL`, `AGENT_SMTP_*`, `AGENT_LOCAL_ADMIN_EMAILS`, `AGENT_SANDBOX_ARCHIVE_S3_*`, `AGENT_MAX_STREAMS_PER_USER`, `AGENT_API_DOCS`, `AGENT_RUNTIME_REGISTRY_URL` |
| Each host | `AGENT_HOST_SLOTS`, `AGENT_SHARED_RESIDENT_CAPACITY`, `AGENT_SANDBOX_CPU_MILLICORES`, `AGENT_SANDBOX_MEMORY_MIB`, `AGENT_SANDBOX_DISK_MIB`, `AGENT_CHAT_IDLE_TIMEOUT_S`, `AGENT_SOURCE_IMPORT_MAX_MIB`, `AGENT_LEASE_TTL`, `AGENT_RUNTIME_REGISTRY_URL`, `AGENT_GIT_AUTHOR_NAME`, `AGENT_GIT_AUTHOR_EMAIL` |
| A managed host's environment file in a deployment profile | `AGENT_HOST_SLOTS`, `AGENT_SHARED_RESIDENT_CAPACITY` |

`AGENT_COMPUTE_SECRET_KEY` is imported the same way: once stored, saved
compute credentials decrypt with the stored key, so the line can go too.
`deploy.sh` no longer writes one; a new control plane generates it into its
settings. Back up the settings root key (`AGENT_SETTINGS_ENCRYPTION_KEY`, or
`AGENT_SESSION_SECRET` where that is the root) with the database. Keep
`AGENT_SANDBOX_MANAGER_TOKEN` on each host: it is the host's copy of the
credential.

`ADMIN_TOKEN` is optional: a new deployment's first administrator uses the
one-time setup code, and an existing deployment can keep the token as its
recovery door. `deploy.sh` now generates a new installation's database
password and DSN, account key and settings root key. It never replaces a set
value or adds a settings key beside `AGENT_SESSION_SECRET`, and on a root that
has deployed before it stops and names any missing required value rather than
generate one.

The web app is now served only at the root of its own hostname, with the API
at `/api`; mounting it under a path such as `/agents` was removed, and
releases no longer carry the `web-agents` image. Delete `AGENT_WEB_BASE_PATH`,
`AGENT_WEB_API_URL` and `AGENT_DEPLOYMENT_SHA` from the control plane's `.env`
(`deploy.sh` exports the commit it deploys). An empty `AGENT_WEB_BASE_PATH` is
harmless; one with a value stops `deploy.sh` and the release deployer.
`AGENT_NETWORK_NAME` can go too, unless another stack's reverse proxy joins
the network by a name other than `cloud-agent`. A distribution that resolves
releases must accept one without the `/agents` web image before deploying it.

Four values moved into Admin:

- **Live streams per account** (`AGENT_MAX_STREAMS_PER_USER`) and **Serve API
  documentation** (`AGENT_API_DOCS`), under Admin → Settings → Operations, in
  its **Advanced** section, applied without a restart.
- **Agent package registry** (`AGENT_RUNTIME_REGISTRY_URL`), under Admin →
  Settings → Agent versions. Hosts receive it on every heartbeat; until every
  online host reports it, that tab names the hosts missing it and agent
  updates stay disabled.
- **Turn lease** (`AGENT_LEASE_TTL`), for all servers or one, from the admin
  pages. Each host imports its own value once, from its next heartbeat.

A deployment that skipped the releases where these settings were added still
imports an old value once.

## Independent accounts cutover

This release removes HybridInference Gateway sign-in, admission and
inference. Cloud Agent keeps its own accounts, and every task runs on a model
connection its owner adds with their own provider key. An earlier database is
keyed by Gateway owner IDs that no account holds, so it is not upgraded,
converted or mapped: the release starts on a **new, empty database**, and the
old one stays untouched as the archive and rollback source. People register
again, apply through the waiting list and add their model connections; nothing
migrates old tasks, connections or identities.

1. **Archive the old deployment.** Drain it, stop every writer, and take a
   custom-format `pg_dump` of the old Agent database with its checksum, the
   old configuration and the root keys. Keep the old database; do not delete,
   downgrade or rebind it. To confirm, read-only, that this release refuses
   it:

   ```bash
   AGENT_DATABASE_URL='<old database>' python -m cloud_agent.deployment.identity_namespace --check
   ```

   It exits non-zero, names this runbook, and changes nothing.

2. **Create a new, empty Agent database** and point `AGENT_DATABASE_URL` at
   it. On the bundled `postgres` service, also set `AGENT_POSTGRES_DB` to its
   name, or the healthcheck, pgAdmin and the documented backup stay on the
   archive ([why](#the-bundled-database-is-the-one-agent_postgres_db-names)).
   The same check must now report an empty database that will be bound on
   first start; `deploy/control-plane/deploy.sh` runs it read-only before it
   migrates, and stops if it refuses.

3. **Give the deployment its own secrets**, shared with neither the Gateway
   nor another environment: `AGENT_LOCAL_SESSION_SECRET`,
   `AGENT_SETTINGS_ENCRYPTION_KEY`, `AGENT_CONTROL_TOKEN_SECRET` and
   `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` (each at least 32 random bytes,
   `openssl rand -hex 32`), and a separate `ADMIN_TOKEN`. Hosts enroll again
   with new enrollment tokens.

4. **Remove every Gateway variable.** The control plane and `deploy.sh` refuse
   `AGENT_AUTH_MODE`, `GATEWAY_*` (issuer, JWKS, authorize, base URL, grant
   path, binding and dispatch token), `IDENTITY_*`,
   `AGENT_SESSION_REDIRECT_URI`, `AGENT_GATEWAY_URL`,
   `AGENT_GATEWAY_SSH_HOST`, `AGENT_WEB_GATEWAY_URL`, `AGENT_WEB_FEATURE_RAG`
   and the on-call dispatch variables; a host refuses `AGENT_GATEWAY_URL`,
   `AGENT_GATEWAY_HOSTNAME` and the inference tunnel's `AGENT_TUNNEL_*`. Set
   `AGENT_WEB_BASE_URL`, the base of every emailed link, and
   `AGENT_PUBLIC_API_URL`, this API's public address, which
   `AGENT_SESSION_REDIRECT_URI` used to give.

5. **Mail.** An existing SMTP transport, such as HybridInference's, may be
   reused: set `AGENT_SMTP_HOST`, `AGENT_SMTP_PORT` (587),
   `AGENT_SMTP_SECURITY=starttls`, `AGENT_SMTP_USERNAME`,
   `AGENT_SMTP_PASSWORD` and `AGENT_SMTP_FROM` from the operator's secret
   store, never from the repository. Only the transport is shared: links are
   built from `AGENT_WEB_BASE_URL` and signed with this deployment's own
   `AGENT_LOCAL_SESSION_SECRET`, and the account data is this database's.

6. **Initial administrators.** List the verified addresses of the people who
   administer the deployment in `AGENT_LOCAL_ADMIN_EMAILS`
   (`admin-one@example.org,admin-two@example.org` are placeholders). Like
   every account setting, the list is imported once, at the first start, and
   edited under **Admin → Settings → Accounts** afterwards. A listed account
   becomes an administrator only once a link sent to its address proves it,
   and the first person to sign up gets nothing. For the very first
   administrator you can instead exchange the setup code the control plane
   logs (`agent_local_setup_pending`) at `/setup/administrator`, or use
   `python -m cloud_agent.accounts.bootstrap_admin`
   ([first administrator](local-accounts.md#the-first-administrator)).

7. **Deploy and verify.** Run the usual deployment, then check that sign-up
   sends a verification link at `AGENT_WEB_BASE_URL`, that an administrator
   can approve an application, and that the applicant can add a model
   connection and run a task. Model connections are on by default; to hold new
   turns until every host and control-plane replica runs this release, set
   `AGENT_MODEL_CONNECTIONS_ENABLED=false` before deploying and unset it once
   they all do.

**Rollback.** Before the new deployment takes writes that matter, stop it and
redeploy the previous release against the archived database and
configuration, which never changed. After that, keep the new database: the
old release cannot read its accounts, and the two databases are never merged.

## Conversation history read contract

`GET /v1/agent/jobs/{job_id}/thread` now answers with `history_version: 2`;
deploy the frontend with the API. The message and job arrays keep their
chronological order but are a bounded page: at most 50 turn and message
entries by default, `limit` up to 100, and at most 512 KiB serialized. Small
histories stay complete. `current_job_id` names the newest turn whatever the
page, and `anchor=true` starts the page at the selected job.

Pass `next_cursor` as `cursor` to load older history. To refresh, keep older
pages, pass `sync_cursor` as `after`, then pass each returned `next_cursor` as
`after` until it is null. This rereads the two mutable tail turns, so an
in-flight write cannot hide behind a newly allocated ID. Merge messages by ID
and jobs by ID and revision, keeping turn order with each opening prompt
before its replies and mid-turn messages. Every request checks the cursor
against the owner's undeleted conversation.

A message longer than 4,096 Unicode characters carries `content_truncated` and
`next_offset`. Read the rest through
`GET /v1/agent/jobs/{job_id}/thread/messages/{message_id}?offset=...`, at most
16,384 characters per request, with offsets in Unicode characters rather than
UTF-8 bytes or JavaScript UTF-16 units. `thread_preview` marks a job whose wide
fields need the single-job detail read. A false `pending_messages_complete`
means read the queue from `GET /v1/agent/threads/{thread_id}/messages`; an
omitted queue snapshot is not an empty queue. Only the owner-facing read API
changes, not runtime session recovery or transcript replay.

## Recent migrations

Newest first.

| Migration | What it changes |
|---|---|
| `0120_operator_model_budgets` | Prices provided models by cost instead of counting requests. Adds `user_daily_budget_microusd` to `agent_operator_model_connections`; four prices and `named_only` to `agent_operator_model_limits`; the tables `agent_operator_user_budgets` (an account's own daily budget) and `agent_operator_model_user_access` (an account's own answer on a model); and to `agent_operator_model_usage` the cost, tokens and estimated-request count of what it counts. Only additions: `user_daily_requests` and `agent_operator_model_user_limits` stay, unread. A request limit of `0` becomes a model for named accounts only, and an account's own limit becomes its answer (`0` withheld, otherwise offered); no budget or price is derived from a request count. The previous release keeps enforcing its request limits, and a model first priced by this release gets a limit of `0`, which that release reads as offered to nobody. A downgrade drops the additions ([A deployment can provide models](#a-deployment-can-provide-models)). |
| `0119_drop_account_provisioning` | Drops `agent_account_provisioning` and `agent_account_provisioning_activation`, with their rows, and removes the four `accounts.inference_provisioning_*` stored settings, including the dispatch token, moving the settings revision on by one if it removed any. The previous release runs beside it with provisioning off. A downgrade recreates the tables empty and does not restore the settings ([Inference provisioning is withdrawn](#inference-provisioning-is-withdrawn)). |
| `0118_operator_model_connections` | Adds the tables for provided models: `agent_operator_model_connections` (endpoint, encrypted key, models and their verification), `agent_operator_model_limits` (each model's default daily request allowance and concurrency cap), `agent_operator_model_user_limits` (an account's own allowance), `agent_operator_model_usage` (requests the relay accepted per account, model and UTC day) and `agent_operator_connection_residencies` (the revision a member's process started on). A turn names a provided connection in the existing `model_connection_id` columns. The previous release runs beside it. A downgrade refuses while a provided connection exists or a turn or waiting message names one. |
| `0117_connection_model_limit` | Raises `agent_model_connections_models_check` from eight model ids to sixteen, under the same name and its table lock; no row is rewritten. The previous release runs beside it: it refuses a seventeenth id itself, and the wider check accepts what it stored. A downgrade restores the eight-model check and fails while a connection lists more than eight. |
| `0116_account_provisioning` | Adds `agent_account_provisioning` (one invitation row per account for the connected inference service) and `agent_account_provisioning_activation` (when provisioning was first enabled, so only later approvals are invited). Nothing read them before, so the previous release runs unchanged beside it. A downgrade drops both. |
| `0115_mcp_retire_dead_locations` | Moves every MCP connection at `managed-worker` or `user-connector` to `sandbox`, keeping its launch, credentials and connector link; moves its `config_revision` and `authorization_epoch` on by one, as a location edit does; and turns an `error` status into `draft`. Then narrows `agent_mcp_connections_location_check` to `remote` and `sandbox` under the table lock it already holds. No row is deleted. The previous release runs beside it, except that its writes of a removed location are refused. A downgrade restores the four-location check; moved rows stay `sandbox` ([MCP servers run in two places](#mcp-servers-run-in-two-places)). |
| `0114_admin_audit_attribution` | Adds attribution to `agent_admin_audit_log`: `actor_type`, generated from `actor_id`, and `actor_label`, `target_type`, `target_id`, `target_label`, `subject_label`, `outcome`, `reason` and `correlation_id`, backfilled from each row's action and details and the current account names; and the tables `agent_admin_audit_outcomes` (a requested sandbox deletion's final result) and `agent_admin_audit_write_failures`. It deletes the Servers page's polling reads and connection checks, and collapses the sandbox deletion worker's per-step rows into one result per request, keeping worker rows that name no request. The generated column rewrites the table under a brief lock. The previous release keeps using the original columns, and its rows are classified by the generated column. A downgrade drops the columns and tables; deleted rows are not restored, and the deletion steps remain in `agent_sandbox_events`. |
| `0113_sandbox_incidents` | Makes sandbox incidents idempotent and tracks what each chat's agent was told: a unique index on `agent_sandbox_events (sandbox_id, details->>'incident_id')` for `incident.*` rows, and the tables `agent_thread_incident_cursors` and `agent_attempt_incident_marks`. A trigger on `agent_thread_sandbox_memberships` starts a chat's cursor when it joins or moves sandbox, and every existing membership gets one. The previous release runs unchanged beside it. A downgrade drops the triggers, tables and index; incident rows stay. |
| `0112_thread_pull_requests` | Adds `agent_thread_pull_requests`, the pull requests a conversation's agent opened through the GitHub relay, one row per conversation and pull request. The previous release runs unchanged beside it. A downgrade drops the table. |
| `0111_mcp_sandbox_location` | Lets an MCP connection run in the task sandbox: `agent_mcp_connections_location_check` admits `sandbox` beside `remote`, `managed-worker` and `user-connector`. No row changes. A downgrade restores the three-location check, and refuses while a sandbox connection exists. |
| `0110_host_late_settings` | Adds two columns to `agent_runner_hosts`: the host settings registered after a host's first import that it has already offered (so each is imported once, and an administrator's reset is never undone), and what the host reports it uses for configuration delivered on the heartbeat, such as the agent package registry. A downgrade drops both. |
| `0109_local_account_parity` | Accounts only. Splits `agent_local_password_logins` into sign-in identities (email, login name, phone) and optional password credentials, carrying every account over with its verification time and hash (a verified email is recorded as verified by link); adds the access view every SQL gate reads, the `deleted` state with the account's name, note, language and sign-in and deletion times, phone codes, browser sessions, sign-in events, the signup domain list, first-run setup and mail to accounts. A downgrade restores the email-and-password table and drops the new tables, with their sign-in events and mail history; it refuses while an account signs in another way or is deleted. |
| `0108_local_accounts` | Creates the account tables: accounts, password sign-in, waiting-list admissions, server-side sessions, emailed tokens, abuse counters, access terminations and the identity marker. It changes no existing row and locks no existing table. A downgrade drops them, and every account with them. |
| `0107_attempt_context_profiles` | Adds `agent_attempt_context_profiles`, a rebuildable cache of each attempt's context profile behind the Context view and the Usage page's context insights. It scans and rewrites nothing: profiles are derived when an owner reads them, and a stale row is rebuilt from the event ledger. A downgrade drops the cache. |
| `0106_user_model_connections` | Adds `agent_model_connections`, `agent_model_connection_checks` and `agent_model_connection_scan_keys` for owners' own inference endpoints; the nullable source columns `agent_jobs.model_connection_id` and `agent_pending_messages.model_connection_id`; and `agent_model_connection_residencies`, binding a resident member's process to one connection revision (a separate table, so the previous release's prepared statements on memberships keep working). Nothing is rewritten. Connections are on unless `AGENT_MODEL_CONNECTIONS_ENABLED` is `0`, `false` or `no`. A downgrade refuses while any connection exists, or any turn, waiting message or resident member names one ([owner model connections](operations.md#owner-model-connections)). |
| `0105_job_event_usage_summary` | Adds a nullable `usage_summary` column to `agent_job_events`, holding its lock only for the catalog change. New events store the projection usage reads need; older events are projected when read. A downgrade drops the column. |
| `0104_notification_change_keyset` | Builds a `(user_id, kind, change_xid, entity_id)` index concurrently before dropping the old owner/XID index, so notification delta pages seek directly. No row changes, and foreground writes are not blocked. Existing v1 continuations finish in their old order before moving to v2, and the previous API keeps working, possibly on a less selective index. A downgrade restores the old index concurrently. |
| `0103_terminal_resume_keyset` | Rebuilds the partial terminal-resume index on `(updated_at, id)`, matching the replay keyset. The online build blocks no writes and changes no row. A downgrade restores the one-column index. |
| `0102_admin_sandbox_marker_order` | Replaces 0097's membership trigger so it locks a membership's sandbox group before marking the group changed, the order every write to the group takes. Before, settling, abandoning, ending, releasing or reaping a turn could deadlock with the admin terminal check or a workspace start, and Postgres aborted one. Only the trigger function changes. A downgrade restores the 0097 trigger. |
| `0101_notification_state_deltas` | Adds coalesced per-owner job and thread change markers, an active-job index and a partial recent-terminal marker index. Notification clients bootstrap from bounded keyset pages, then request bounded deltas; nothing is backfilled. Markers expire after 24 hours, and stale cursors rebuild. The previous push stream stays available. A downgrade drops the derived state and restores push-only triggers. |
| `0100_memory_archive_pages` | Builds partial owner/scope indexes for active memories and archive pages concurrently. Memory rows are unchanged, an interrupted build can be retried, and offline SQL builds them plainly. A downgrade drops the indexes. |
| `0099_thread_stats_rollups` | Adds a derived, versioned per-attempt cache of owner thread statistics, filled in bounded 1,000-row pages; unchanged reads reuse it without reading old events. Event storage and the billing ledger are unchanged. A downgrade drops the cache. |
| `0098_measurement_counts` | Backfills exact profiling counts into at most 64 counter shards per run and installs accounting triggers. The backfill holds a write-conflicting lock on measurements until commit, so schedule a write pause sized to the retained history. Raw samples and run summaries are unchanged. A downgrade removes the counters and triggers. |
| `0097_admin_sandbox_deltas` | Adds coalesced metadata-change markers and short-lived, content-free admin inventory snapshot pages, so admin refreshes fetch only changed rows after the first snapshot; expired cursors resynchronize. Triggers cover sandboxes, membership counts, origin titles and host heartbeats without a fleet-wide lock. Deploy the migration before enabling the new UI; older list clients keep working. A downgrade drops the derived state and triggers. |
| `0096_snapshot_title_context` | Adds a bounded JSON title context to sandbox snapshots, so a publisher claim never rebuilds or transfers the whole transcript. Each capture continues from the newest earlier snapshot of its group that has one. Existing snapshots start empty, and one still empty when claimed gets its context built then, from at most 24 earlier snapshots and the newest 24 turns. A downgrade drops the context. |
| `0095_memory_retention` | Builds partial archived-time and non-null superseded-by indexes concurrently, without changing memory rows or the 90-day retention. An interrupted build can be retried, and offline SQL builds them plainly. A downgrade drops the indexes. |
| `0094_thread_message_pages` | Builds a concurrent index on each job's messages in prompt-first order, so history pages read a bounded set of message IDs before the bodies. No row changes. An interrupted online build is dropped and retried; offline conversion builds it plainly. A downgrade drops it concurrently. |
| `0093_recent_requests` | Adds an owner/time-indexed projection of recent requests with maintenance triggers, backfilled once. The trigger installation holds write-conflicting locks during the backfill, so schedule a low-write period on a large history. Source messages are unchanged. A downgrade removes the projection and triggers. |
| `0092_targeted_work_wakes` | Replaces the `agent_work_available` trigger function so its notification names the one host that can take a retained conversation's turn, and only that host's runner wakes. No table or row changes; a control plane on the previous release ignores the name and wakes every host. A downgrade restores the empty notification. |
| `0091_env_queued_runs_index` | Builds a partial index on `agent_environment_runs` concurrently, holding only the runs queued for a host to claim; without it, every claim read the whole test and prebuild history. It does not block inserts. An interrupted build leaves an invalid index that the next run rebuilds, and offline SQL builds it plainly. No row changes. A downgrade drops it concurrently; claimed runs stay claimed. |
| `0090_user_model_preferences` | Adds an empty table for each owner's pinned default model for new tasks. Owners without a pin keep following their last task's model. A downgrade drops the pins. |
| `0089_superseded_removals` | Adds an empty table recording each authorization to remove a container that a moved sandbox group left on a host being released, and when the host confirmed the removal. A downgrade drops the records; removed containers stay removed. |
| `0088_sandbox_retry_budget` | Adds the automatic deletion retry window and attempt count. Pending requests start their 24-hour window at their request time, and retries stop after eight claims with bounded backoff. An administrator resumes an archived terminal request under its request ID and verified receipt; a terminal prearchive request is replaced through the authorized deletion path, which gives the host a new archive identity. |
| `0087_sandbox_deletion_attempts` | Adds the admin-only, secret-free attempt history of sandbox deletion. |
| `0086_conversation_read_receipts` | Adds an empty table for each owner's latest read per conversation, with indexes for incremental polling and foreign-key cleanup, taking brief foreign-key locks on the referenced tables. A browser's own receipts are imported when it next opens the app. A downgrade drops the stored receipts; browser caches stay. |
| `0085_terminal_resume_index` | Builds a partial index on `agent_jobs` concurrently, holding only the turns whose terminals wait to resume. It does not block the API, and an interrupted run can be repeated. No row changes. |
| `0084_host_removal_move_out` | Adds `agent_runner_hosts.removal_moves_out` and `removal_leaves_unmoved`, false for existing hosts, so a pending removal keeps its earlier meaning: revoke once its running turns end. |
| `0083_host_move_out` | Adds the `move_out` purpose to the admission-hold check. No row changes. A downgrade deletes move-out holds; groups already moved keep their checkpoints. |
| `0082_team_environments` | Adds nullable team columns to environments and their bindings, and lets a repository default name any environment. No row changes. A downgrade refuses while a team environment exists, archived ones included. |
| `0081_snapshot_pushed_commit` | Adds `agent_sandbox_snapshots.pushed_commit_sha` and copies into it the commit of every never-published snapshot. `published_commit_sha` is unchanged, so the API it replaces keeps working, and no notice fires. |
| `0080_user_ssh_servers` | Adds the owner SSH server tables ([SSH servers](operations.md#ssh-servers)). No row changes. |
| `0079_teams` | Adds teams: members, single-use invitations, join requests and the team audit log. No row changes. |
| `0078_environment_snapshots` | Adds environment installation snapshots, and the setup fingerprint on environment bindings that decides which snapshot a workspace may start from ([environment installation snapshots](operations.md#environment-installation-snapshots)). |
| `0077_sandbox_usage_days` | Adds the per-day container usage table and an owner index on `agent_sandboxes`, built without `CONCURRENTLY`; with one row per retained container, the write lock is short. |
| `0076_mcp_residency_fence` | Adds a nullable residency generation to MCP approval records; existing approvals keep it empty. |
| `0075_runtime_upgrades` | Adds two empty JSON columns for agent updates from Admin → Settings. |
| `0074_host_removal_pending` | Adds `agent_runner_hosts.removal_requested_at`, empty for existing hosts. A downgrade drops it and leaves a host that was waiting for removal draining. |
| `0073_sandbox_restore` | Adds the `cold` and `restoring` sandbox states and the start fence that binds a checkpoint to the run it ends ([restoring a removed host's sandboxes](operations.md#restoring-a-removed-hosts-sandboxes)). Existing checkpoints carry no fence and never move a group. A downgrade refuses while a group is cold or restoring. |
| `0072_drop_old_memory_tables` | Drops the retired memory tables. A rollback to before `0070` finds them empty, without the earlier portraits and preferences. |
| `0071_temporary_workspaces` | Drops `NOT NULL` from `agent_threads.repo` and `agent_environment_bindings.repo`, so conversations can start without a repository. A downgrade refuses while such a conversation exists. |
| `0070_memory_cutover` | Moves the memory switches and saved preferences onto the durable-memory table. |
| `0069_agent_memories` | Adds the durable-memory table. |
| `0067_failed_snapshot_wake` | Only replaces a trigger function. |
| `0066_admin_job_listing_index` | Builds an index on `agent_jobs` concurrently. On a long job history it takes a while without blocking the API, and an interrupted run can be repeated. |
| `0051_host_drain_fence_epoch` | Backfills a non-null drain-fence epoch for every host, which the database rotates whenever drain-relevant lifecycle, credential, placement or ownership state changes. Once the V2 control plane is active, this revision and the `AGENT_DEPLOY_DRAIN_FENCE_V2` recipe marker are the application and schema rollback floor ([rollback compatibility](operations.md#rollback-compatibility)). |
| `0049_host_drain_fence` | Starts every existing host with no deployment drain owner or release receipt. Its V1 marker remains only so an older orchestrator can do the bridge upgrade, and is not a rollback target after V2. |
| `0048_sandbox_plugins` | Adds `agent_threads.plugins`; existing chats have no plugins. Rebuild the sandbox image so the CLI wrappers load selected plugins. |
| `0046_job_claim_retry` | Adds `agent_jobs.claim_retry_at`, which the claim query needs. Queued jobs stay immediately eligible. |

Older revisions that need a maintenance window are listed in
[applying migrations that are not backwards-compatible](operations.md#applying-migrations-that-are-not-backwards-compatible).
A database from before the independent-accounts release, on either chain, is
not upgraded; see [the cutover](#independent-accounts-cutover).

## When you add a migration

Add a row to the table above in the same pull request, saying what an operator
upgrading a live deployment needs to know: which existing rows it changes, how
long it can lock, and whether its downgrade refuses. Then update the head
revision named under *Before starting this release* and in
[ENVIRONMENT.md](../../ENVIRONMENT.md); a test checks that both name the
current head.
