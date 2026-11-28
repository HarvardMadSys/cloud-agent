# Operations runbook

This runbook is organized by task, so you can read only the section you need.
It covers provisioning hosts, deploying Cloud Agent, protecting retained
workspaces, and diagnosing job and network failures.

The control plane owns the job API, the database, accounts and admission, the
MCP, GitHub and model relays, and the admin settings. Every turn runs on a
model connection: either one its owner added with their own provider key, or
one the deployment provides through the model relay.

Start with the task you need:

| Task | Go to |
| --- | --- |
| Prepare a host and its credentials | [Prerequisites](#before-a-job-can-run-for-real), [credentials](#2-credentials) |
| Start or inspect the host service | [Running self-hosted](#running-self-hosted) |
| Enable isolation for untrusted repositories | [Provisioning Kata](#provisioning-kata-on-a-runner-host) |
| Change application behavior, integrations, or appearance | [Admin Settings](#admin-settings) |
| Open PostgreSQL administration | [pgAdmin](#pgadmin) |
| Browse and filter database rows | [Database browser](#database-browser) |
| Change concurrency, sandbox settings, or placement | [How many runners](#how-many-runners), [which machine runs the jobs](#which-machine-runs-the-jobs) |
| Add a host on another machine | [Tunnels and sandbox manager routing](#running-runners-on-another-machine) |
| Upgrade an existing deployment | [Upgrade notes](upgrade-notes.md) |
| Deploy or roll back a release | [Rolling deployment order](#rolling-deployment-order), [deployment reliability](deployment-reliability.md) |
| Archive an old database or apply a maintenance migration | [Archiving a Gateway-era database](#preserving-an-old-chain-database), [maintenance migrations](#applying-migrations-that-are-not-backwards-compatible) |
| Protect workspaces and monitor disk use | [Sandbox archives](#sandbox-archives), [retained Docker storage](#retained-docker-storage-on-a-host), [storage measurement](#how-full-is-the-sandbox-storage) |
| Diagnose connectivity | [The sandbox network](#the-sandbox-network), [refusal messages](#when-something-is-refused) |
| Configure or troubleshoot tools and repository access | [MCP servers](#mcp-servers), [repository entitlements](#who-may-run-a-job-against-which-repository) |
| Answer a waiting agent question | [Native agent questions](#native-agent-questions) |
| Investigate a task | [Recorded events and audit](#reading-a-tasks-recorded-events) |
| Choose the tests for a runtime or MCP change | [Runtime verification](#runtime-verification) |
| Let owners reach their own servers | [SSH servers](#ssh-servers) |
| Let owners run tasks on their own inference endpoint | [Owner model connections](#owner-model-connections) |
| Provide shared models through the control-plane model relay | [Provided models](#provided-models) |
| Operate environments and team environments | [Reusable cloud environments](#reusable-cloud-environments) |

[ENVIRONMENT.md](../../ENVIRONMENT.md) documents configuration, and the
[compute-resource runbook](operations-compute.md) covers administrator-managed
SSH and CloudLab hosts. For pre-drain checks, migration regression, failed peers,
and queue alerts, use [deployment reliability](deployment-reliability.md).
Replace every `<...>` placeholder before running a command.

## Admin Settings

**Admin → Settings** holds the application and integration configuration, web
appearance, operational intervals, upload and skill limits, task titles, sandbox
and workspace limits, and sandbox archives, stored in the Agent database. It is
operator access: grant `agent.admin` only to people you trust to operate the
deployment, because it edits the host signing key, the account settings
(sign-up, approval, mail transport, administrator addresses) and the GitHub API
URL that GitHub credentials are sent to. Saved credentials stay write-only.

### Editing settings

A group is read-only until you choose **Edit**. Change the fields, select
**Review changes**, check the current and proposed values, then choose
**Apply changes**; **Cancel** discards the group's drafts, and unsaved edits
survive switching categories. A credential field shows only whether a value is
set: **Replace secret** (or **Set secret**) enters a new one, and an empty
replacement keeps the saved value. Each field's **More actions** menu offers
**Restore default…** or **Clear secret…**. The **Administrator setup required**
prompt links to required or partly configured fields.

The API is `GET /admin/agent/settings` and `PUT /admin/agent/settings` with a
partial body such as `{"settings": {"max_attempts": 3}}`. Add
`"expected_revision"` with the `revision` you read to have the write refused with
409 if someone saved in the meantime; the editor always sends it. Writes appear
in the Audit log.

Listeners pick up saved changes every five seconds, and each setting's
description says when a change takes effect. The web server reads the public
appearance on every request, within 400 ms; when that fails, it uses the last
appearance it read, or neutral defaults before its first successful read.

### Settings during an upgrade

Apply migrations first ([upgrade notes](upgrade-notes.md)), then start the main
control-plane listener with its full environment. It imports the registered
environment settings once, and settings a new release adds only when they are
absent, never overwriting a saved value; the MCP-only listener waits for that
import. After that, `.env` values no longer matter, and resetting a field never
brings one back.

The import refuses an `AGENT_CONTROL_TOKEN_SECRET` or
`AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` shorter than 32 bytes, an
`AGENT_GITHUB_APP_INSTALL_URL` that is not an `https://github.com` URL, and an
`AGENT_GITLAB_OAUTH_REDIRECT_URI` that is not HTTPS (HTTP on localhost is
allowed). The listener stops with an error naming the variable before anything is
written, so rolling back is safe. Replacing the control key means re-enrolling
hosts, and a new encryption key orphans saved user connections.

When a release narrows what a setting accepts, a saved value it refuses does not
stop the listeners. Each logs `saved setting not applied as stored: …` once per
settings revision, naming the key but never the value, and uses the built-in
default. A signing key, encryption key or service token below a new minimum
length stays in use until you replace it. Save a valid value or confirm
**Restore default** to clear the warning. Stored credentials that cannot be
decrypted, or are not encrypted, still stop the listener.

### A new installation

A new installation needs no `.env`. Start the web application before you enroll
hosts or run the deployment readiness gate. From the deployment checkout:

```bash
AGENT_DEPLOYMENT_SHA=$(git rev-parse HEAD) docker compose -p cloudagent \
  -f deploy/control-plane/docker-compose.yml --profile edge \
  up -d --build control-plane mcp-relay web edge
```

The browser reaches the web app and the API on one origin, with the API at
`/api`. The `edge` profile is the nginx that splits that origin, on
`127.0.0.1:3003` (`AGENT_EDGE_BIND`, `AGENT_EDGE_PORT`); point the TLS proxy or
tunnel in front of the hostname at it. Leave the profile out only when that proxy
already routes `/api/*` to the control plane with the prefix stripped, and
everything else to the web app.

On the bundled database, the `keys` service generates the database password,
`AGENT_DATABASE_URL`, `AGENT_LOCAL_SESSION_SECRET` and
`AGENT_SETTINGS_ENCRYPTION_KEY` into the `agent-keys` volume before anything else
starts ([how the keys are kept](../../ENVIRONMENT.md#settings-and-bootstrap-configuration));
an artifact deployment's first `prepare` does the same
([release artifacts](../../deploy/release/README.md)). For another database, set
those three yourself in the environment or a `.env` beside it, because the
`keys` service cannot prove such a database is new. It stores what it is given,
so you can delete the lines after one start. Back the keys up apart from the
database ([Disaster recovery](disaster-recovery.md)): the settings root key
decrypts every saved secret.

On its first start, the control plane generates its host signing key, credential
encryption keys and sandbox manager credential, and logs a one-time setup code
(`agent_local_setup_pending`). Then:

1. Open `/setup/administrator`, enter the code and create the first
   administrator.
2. Confirm the site address the page offers; account emails link to it.
3. On **Admin → Servers**, add the first server with **Add server**, which
   writes the host's configuration ([Compute resources](operations-compute.md)).
4. Run the usual deployment command.

### Recovery access and the settings root key

`/setup` is the recovery door for when no administrator can sign in. It works
only when the deployment sets `ADMIN_TOKEN`, goes through the ordinary
administrator API and audit trail, and keeps the token only in page memory.

An older installation may still use `AGENT_SESSION_SECRET` as the settings root.
Before removing it, set `AGENT_SETTINGS_ENCRYPTION_KEY` to the **same value** on
both listeners, and back it up apart from the database. Changing the root key
needs the stored settings re-encrypted and a coordinated listener rollout;
replacing the environment value alone makes existing ciphertext unreadable. The
API refuses to replace or reset an existing credential encryption key without a
separate re-encryption procedure.

Database access, the settings encryption root, recovery authentication, host
provisioning, networking and listeners, host isolation, build routing, mounted
files and trusted compute deployment profiles stay deployment configuration.
Admin Settings lists them with their values hidden
([environment inventory](../../ENVIRONMENT.md#settings-and-bootstrap-configuration)).

## Sandbox archive storage

**Archive to S3 and delete** writes to the bucket and region of the
**Sandbox archives** policy ([Sandbox archives](#sandbox-archives)), so it is
available only while that policy is enabled. While it is disabled, an
administrator can only discard a sandbox, and a deleted chat's sandbox is retired
without an archive. Each request writes one object,
`s3://<bucket>/cloud-agent/sandbox/deletion/<sandbox>/<request>/archive.tar`.

The control plane signs and verifies the upload with the policy's saved access
key or, without one, its standard AWS SDK credential chain, preferably an IAM
workload role. That identity needs `s3:PutObject` and `s3:GetObject` on
`arn:aws:s3:::<bucket>/cloud-agent/sandbox/deletion/*` (the lifecycle grant on
`cloud-agent/sandbox/*` already includes both), plus `kms:GenerateDataKey` and
`kms:Decrypt` on the key if the bucket applies SSE-KMS by default. It does not
need `s3:DeleteObject`. A policy's expected bucket owner is asserted on the
`HeadObject`.

For environment or shared-profile credentials, create a private Compose override
for the main service; the base Compose forwards no AWS variables. For example:

```yaml
services:
  control-plane:
    environment:
      AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:?Set the archive access key}
      AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:?Set the archive secret key}
      AWS_SESSION_TOKEN: ${AWS_SESSION_TOKEN:-}
```

Keep the override outside the checkout and set its absolute path as
`AGENT_CONTROL_PLANE_COMPOSE_OVERRIDE_FILE` in the deployment root's `.env`; the
deployer applies it after the release's own overlays, in source and artifact
modes. For shared profiles, mount the files read-only and set
`AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE` and `AWS_PROFILE`. Neither path
gives AWS credentials to sandboxes, the web app or the MCP relay.

The host gets a 15-minute presigned PUT with a fixed byte length, a full-object
SHA-256 and `If-None-Match: *`. An archive over **5 GiB** is refused and the
container kept, and a retry after a successful upload can get HTTP 412. Before
deleting, the control plane reads `HeadObject` with `ChecksumMode=ENABLED` and
requires the exact SHA-256 and length that S3 verified; ETags and supplied
metadata do not count.

Use a private bucket with public access blocked, encryption, retention and
recovery access. Other principals must keep the objects immutable too: a bucket
policy can require conditional writes, and versioning or Object Lock protect
further. Do not expire an archive that a deletion or recovery may still need.
The receipt records the original S3 URI, SHA-256, length and version, so changing
the bucket does not redirect verification of an existing request.

## Database browser

**Admin → Database** browses the Agent database in the dashboard: select a table,
add filters (combined with AND; Contains matches a literal, case-insensitive
substring), choose a sort column and page size, then select **Run query**. It
shows only reviewed metadata columns, never stored credentials, raw user content
or opaque configuration documents. Queries are read-only, parameterized, bounded
and time-limited, each checks current `agent.admin`, and outcomes appear under
**Audit log → Database** without filter values or returned data. The API is
`GET /admin/agent/database/tables` and `POST /admin/agent/database/query`. For
writes and arbitrary SQL, use pgAdmin.

## pgAdmin

**Admin → Database → Open pgAdmin** opens pgAdmin through the same-origin API
proxy, so the public edge needs no new route. Every request needs current
`agent.admin`. pgAdmin keeps its own login and CSRF checks, and Agent cookies and
bearer credentials are never sent to it.

To enable it, add this to the control-plane deployment's `.env`, keeping any
existing profiles (for example `COMPOSE_PROFILES=edge,pgadmin`), and run the
normal deployment:

```dotenv
COMPOSE_PROFILES=pgadmin
AGENT_PGADMIN_BOOTSTRAP_URL=http://pgadmin:5050
AGENT_PGADMIN_BOOTSTRAP_DIR=/var/lib/cloud-agent/pgadmin-bootstrap
```

On an existing stack, this starts the service and applies the new control-plane
environment (for an artifact deployment, use the release-generated Compose file):

```bash
docker compose --env-file .env -f <selected-compose-file> up -d pgadmin control-plane
```

Then open **Admin → Database**, choose an administrator email and a password of
at least 12 characters, and wait for **Open pgAdmin**. Only a current Agent
administrator can complete setup, and no account or default password is shipped.
Setup saves the connection under **Admin → Settings → Operations** (Database
tools) if none is saved. The public URL defaults to the web base URL plus
`/api/admin/agent/pgadmin/`; for another prefix, set
`AGENT_PGADMIN_BOOTSTRAP_PUBLIC_URL` before setup. Keep the
`agent-pgadmin-bootstrap` and `agent-pgadmin` volumes across deployments.

Sign in with the email and password you chose. The server definition points to
`postgres:5432`, with the database and user named by `AGENT_POSTGRES_DB` and
`AGENT_POSTGRES_USER` (both `cloudagent` by default); enter the database password
when you connect. pgAdmin imports that definition only once, so after changing
either variable, edit the connection in pgAdmin.

**Private pgAdmin URL** can only select a pgAdmin the deployment names: the
bundled `http://pgadmin:5050`, or the scheme, host and port of
`AGENT_PGADMIN_URL` or `AGENT_PGADMIN_BOOTSTRAP_URL` in the control plane's
environment. Any other saved URL gets 503 without being contacted, because the
proxy forwards every method, path and body and could otherwise reach a cloud
metadata endpoint or another private service. To run pgAdmin elsewhere, set
`AGENT_PGADMIN_URL`, restart the control plane, select the same address, and keep
the variable set.

**Security.** pgAdmin runs on the Agent's origin, where the administrator's
session authorizes every admin route. The proxy therefore adds a second
`Content-Security-Policy` that keeps pgAdmin's fetches, forms, frames and
workers under its own path, plus `frame-ancestors 'self'`, `nosniff`,
`X-Frame-Options: SAMEORIGIN` and `Cross-Origin-Opener-Policy: same-origin`.
This narrows what an injected script could do, but it is not an origin boundary:
pgAdmin shares the origin's browser storage, and only a separate origin would
remove the shared session, which this release does not provide. Keep the pinned
image and its default security headers, enable the profile only where it is
used, and grant `agent.admin` only to operators. pgAdmin connects as the database
owner, and the Agent audit log records no SQL (only
`agent.database.pgadmin.setup` and `agent.database.pgadmin.open`), so treat
pgAdmin access as full database access.

**Troubleshooting.** For the HTTP-only local stack, set
`AGENT_PGADMIN_COOKIE_SECURE=False`. If the dashboard reports pgAdmin
unavailable, check `docker compose logs pgadmin`. Before setup, the container is
healthy and logs `Waiting for first-time setup in Admin -> Database.`; after
setup, unreadable bootstrap storage or an unavailable PostgreSQL stops it. A
setup that cannot write the bootstrap volume is recorded as failed: fix the
volume and set up again. To turn access off, reset **Private pgAdmin URL**;
removing the profile keeps the volume.

## What a job does

```text
POST /v1/agent/jobs             owner queues work
  → enrolled host claims        exact shared-live capability attestation
    → runner creates/starts     one retained Docker group and its writable layer
      → native Agent CLI        one live process/session per member chat
      → normalized events       streamed to the owner over SSE
      → model calls             the owner's provider, on their own key
      → push and pull request   git/gh as the owner, through the GitHub relay
  → turn settles                the workspace stays in the retained sandbox
```

The platform never pushes or opens a pull request itself. When the owner asks,
the agent does, as the owner, through the [GitHub relay](#the-github-relay), and
the pull requests it opens are recorded for the conversation
([Resume, Fork, and pull requests](#resume-fork-and-pull-requests)).
**Watch PR and merge** asks the agent to merge once required checks and reviews
pass; the repository's GitHub rules decide what may merge without a person.

Credential boundaries:

- **No repository credential enters the sandbox.** The owner's GitHub token stays
  in the control plane for the GitHub relay. The runner's clone credential is
  read-only, scoped to one repository and never written to `.git/config` (it
  travels in git's environment), and the runner refuses to start the agent if it
  finds the token anywhere under `.git/`.
- **git and `gh` act as the owner without the owner's token.** A member's
  `GH_TOKEN` is its residency's placeholder, which only the GitHub relay accepts.
- **Model and MCP credentials are narrow.** The CLI runs on the owner's provider
  key, handed over only in that owner's claim and residency, or, on a provided
  model, holds only a placeholder that the model relay accepts. A separate
  attempt-fenced `mcp` token reaches only the MCP relay and the servers the
  conversation's bindings authorize. Neither can write the event log or end the
  job; only the runner's token can.
- **There is no per-job spend cap and no local quota.** The provider keeps the
  key's quota and bill.

### Temporary workspaces

A job created with `"repo": null` starts a conversation without a repository.
Any user with `agent.use` may create one, and its claim carries no clone or
`GH_TOKEN` credential. The host makes `/workspace` a new Git repository on `main`
with one empty commit, the same on every host
(`77bbec2039abcd5c7663078db4f90f3c860a751a`), which becomes the job's base. A
request that also names `base_ref`, `base_sha` or `environment_id` is refused.
The GitHub relay sends the agent's requests without credentials and records no
pull request. Everything else (archive, delete, Fork, Side Chat, goals, MCP,
retention, checkpoints, S3 archiving) works as for a repository chat, but no
project memory is built. `temporary=true` on `GET /v1/agent/jobs`,
`/conversations` and `/sessions` lists only these conversations and cannot be
combined with `repo`. While a configured GitHub App cannot be loaded, creating
one answers `503`, like any job.

`GET /v1/agent/jobs/{job_id}/workspace/archive` downloads any retained chat's
`/workspace` as a gzipped tar. The host compresses it to disk first, so a
workspace over `workspace_download_max_mib` (default 1024 MiB, before
compression) is refused with `413` instead of arriving truncated.

### Conversation titles

A new conversation is titled with its prompt's first meaningful line, cut to
**Maximum task title length** (36 characters by default; under Advanced on
**Admin → Settings → Content**). The owner can rename
it; follow-ups keep the name, and forks inherit it. No model is asked for a
title.

### Conversation status and run history

The UI shows **Queued**, **Running**, **Idle** or **Failed**. A running turn wins
over queued ones; otherwise the latest turn decides, and completed and
user-stopped turns leave the conversation idle. The owner API reports this as
`conversation_state`, separate from the selected job's `state`. A turn's
execution history appears only when it had several attempts, and viewing history
never restarts or moves a task.

### Native agent questions

Claude Code `AskUserQuestion`, Codex user-input requests, and OpenCode/Kilo
questions open a dialog while the turn waits; submitting continues the same
native tool call. **Answer later** closes it, **Review question** reopens it,
and **Skip** declines. Claude Code `ExitPlanMode` uses the same dialog:
**Approve plan** lets Claude leave plan mode, and **Skip** asks it to keep
planning. Pending questions are read from the resident agent, so a reload finds
them again. An answer names the job, attempt and request, both the API and the
host fence it, and answers are never queued as follow-ups.

Deploy the runner, Sandbox Manager and rebuilt sandbox image together. The
`http-agent-live-bridge` helper pin is `5`
([native conversation recall](context-recall.md)); helper `2` and earlier cannot
execute question replies, and anything older than the pin fails the turn-time
attestation. Before starting a replacement OpenCode/Kilo process, the host
installs its trusted bridge into the retained container, keeping the workspace
and native sessions.

## Who may run a job against which repository

A repository needs at least one entitlement, the user's GitHub connection or the
deployment allowlist; job creation fails closed without one.

**User connection.** The user authorizes the GitHub App through the frontend
callback. The control plane exchanges the code for a user token, records the
account in `agent_github_accounts`, and stores the installations GitHub reports
in `agent_repo_grants`; reconnecting removes grants the user can no longer reach.
The composer, job creation, claims and workspace reads check repositories
**as the user** through `/user/installations/{id}/repositories`, and only
repositories the user can **push** to qualify. The answer is cached per user for
**GitHub repository cache duration** (60 seconds by default; under Advanced on
**Admin → Settings → Source control**). Do not replace this
check with an installation-token listing: GitHub can report an organization's
installation to a collaborator who can see only one of its repositories, and the
installation token would expose the rest.

The user token stays in the control plane, encrypted with
`AGENT_SOURCE_CONTROL_ENCRYPTION_KEY`. GitHub App user tokens last eight hours,
with a six-month, single-use refresh token; refresh starts five minutes before
expiry under a row lease. A connection without a usable token grants nothing, and
the composer asks the user to **Connect GitHub** again. Connecting and installing
the App are separate steps: if the App is not installed on the user's own
account, the connect flow offers GitHub's installation page (the user can choose
**Not now**), and **Configure repositories** and
**Configure repository access** open the same page.

**Deployment allowlist.** `AGENT_REPO_ALLOWLIST` (comma-separated `owner/name` or
`owner/*`) is for single-tenant use. It is not per user: every admitted user may
have the platform clone every listed repository with the deployment's App, so
list only repositories all of them may work on. It grants no push; pushes and
pull requests always use the owner's own connection. When the App's OAuth
credentials and `AGENT_GITHUB_APP_INSTALL_URL` are configured, the composer still
requires a connection before any repository can be chosen; until then a task
runs in a [temporary workspace](#temporary-workspaces).

**Disconnecting.** **Disconnect GitHub** under Integrations deletes the account,
token and grants and asks GitHub to withdraw the authorization. Uninstalling the
App revokes the platform's installation access.

## Sandbox inventory, resource usage, and controls

The admin **Sandboxes** tab lists every group recorded as running, including
containers retained between turns; the state filter adds stopped, starting,
stopping or failed groups, and Fork members share one row. It refreshes every 30
seconds from `GET /admin/agent/sandboxes` (default `state=running`; `state=all`
for every state). Search covers titles, repositories, owners, hosts, agent
versions and container identifiers, and combines with the host and activity
filters; unchecking **Auto-refresh** pauses polling. A sandbox's details show its
member sessions, host, runtime, container identity and lifecycle, but no native
session identifiers, credentials, prompts or raw container diagnostics.

### Stopping an idle sandbox

**Stop idle sandbox** stops the shared container for every member of the group
and keeps its workspace and native sessions for a later turn. The API checks the
reviewed container, generation and last activity, and refuses a busy group, a
group with open terminals, and one whose terminal observations are stale; the
host repeats the idle check before stopping. When the terminal report is zero,
missing or older than 90 seconds, the details (and a stop request) ask the owning
sandbox manager for the group's terminal count directly. An unavailable sandbox
manager or unknown activity keeps stopping disabled, and the page shows the
recovery step. The request is audited and shown as pending until a host
observation confirms the stop, so an offline host cannot complete it, and new
turns wait meanwhile. Stopping neither deletes the container nor frees its disk.

### Deleting a sandbox

**Archive to S3 and delete** removes a stopped or dead, idle container after its
whole retained filesystem (`manifest.json` and `container-rootfs.tar`, with the
workspace and native session files) has been uploaded to the
**Sandbox archives** bucket and verified
([Sandbox archive storage](#sandbox-archive-storage)). New turns, Fork and
workspace operations wait while the archive is prepared. An archive over 5 GiB
is refused, and a failed upload keeps the container. The host removes the
container only after the object's SHA-256 and length match and the receipt is
committed; if the host reports the container missing or changed first, the
request fails for operator review. Once an archive is verified, an uncertain
deletion acknowledgement keeps admission blocked and retries the same request,
and the sandbox manager keeps its deletion receipts on its persistent volume
across restarts. For a Sandbox Manager rolled back past #763, see
[rollback compatibility](#rollback-compatibility).

**Delete without archive** removes the same container without uploading
anything, and needs no bucket. The host still refuses a running, busy or changed
container, and native resume is marked lost only after the host confirms
removal. While the host is offline or removed from the pool, the details say so,
and what ends the wait: the host reconnecting, or being enrolled again. The
exception is a host whose server its provider confirmed `terminated`: after 10
minutes of silence, the container is taken as gone with the machine and the
deletion finishes without it (`host_reclaimed` in the trajectory and audit),
even after automatic retries have stopped. Let a pending or verified archive
deletion finish or fail before you choose this action.

A `cold` sandbox lives only in its
[checkpoint](#restoring-a-removed-hosts-sandboxes), so
**Archive to S3 and delete** refuses it. **Delete without archive** deletes it at
once, with no host call, once the host it names is gone (removed, forgotten, or
silent for 30 minutes). One transaction marks it `lost` and deleted, marks its
chats' native context lost, fails their queued turns and supersedes its
checkpoint receipts (`checkpoint_only`). The checkpoint objects stay in S3 under
the bucket's lifecycle rules; the control plane deletes no objects. Until the
host is gone, the next message to one of its chats restores it, and
**Remove stale entry** can hide it.

**Sandboxes of deleted chats are removed automatically.** When an owner
permanently deletes the last chat in a sandbox on an online host, a sweep that
runs every minute (actor `user-deletion-sweeper`, source `system`) stops it and
queues its deletion, archived first when a bucket is configured. A sandbox that
another chat still uses is never touched.

**Verify stopped and force delete** recovers a sandbox still recorded as
running, but only when the owning sandbox manager confirms that the container is
stopped or dead, and idle. On failure, the error names the cause (missing host
registration, an absent or refused advertised URL, a missing or rejected sandbox
manager credential, a connection failure, a timeout, or unavailable status), and
the control plane logs `agent_sandbox_delete_verification_failed`. For a
connection failure, check the
[reverse sandbox manager route](#the-way-back-the-control-plane-reaching-this-hosts-sandbox-manager).

Deletion keeps chat history, memberships, execution records and audit receipts,
but deleted native sessions cannot resume. Each row shows a deletion as pending,
failed or deleted, and a finished one leaves the default inventory
(**Include removed sandboxes** lists it).

**How requests run.** One worker handles deletion requests one at a time, in
order. Only transient failures retry, with backoff from one minute to one hour,
for up to eight claims or 24 hours. A stopped request stays fenced. After you fix
the cause, **Resume deletion** starts a new retry window for the same request,
keeping its archive or discard choice and any verified receipt. The details show
each request's attempts (host, last confirmed stage, outcome, failure code); this
history is best-effort and never holds up a deletion.

### Removing a stale entry

**Remove stale entry** is available when the owning host has not reported for
more than 90 seconds. It hides the entry from the default inventory without
contacting the sandbox manager, so a retired host does not leave an entry that
can never be cleared. The server refuses it while turns, workspace operations,
snapshots or a deletion are active. It keeps every record and deletes nothing:
no container or file is removed, no archive is made and no disk is freed, so
restore the sandbox manager connection to finish archival and deletion. Undo it
with **Include removed** and **Restore to inventory**; a matching host
observation or new work also brings the entry back. **Remove from inventory** is
the same reversible visibility control for stopped sandboxes.

### Batch operations

Select rows, or **Select all visible sandboxes** (only what the current filters
show), choose an action, then **Review selected sandboxes**. The review reads each
sandbox's current details and lists blockers. One confirmation then applies the
action to the eligible entries, with the same checks as the individual controls,
up to four at a time. A stop or deletion result means the request was accepted
(a cold sandbox's deletion is done at once). Skipped and failed entries say why
and stay selected, and expired reviews and changed containers are refused.

### Resource measurements

CPU is occupied cores between Docker samples, memory excludes inactive file
cache, and network and block I/O show cumulative counters and rates, all for the
whole shared container. The details also show process and terminal counts,
session capacity, active operations, snapshot state and sandbox age (counted from
group creation, not container uptime). The sandbox manager samples Docker in the
background and sends the observations with the heartbeat, each keeping its
original age. Readings older than 90 seconds are marked stale and left out of the
fleet totals, which show their coverage; a current heartbeat does not mean
current measurements. A host with an older sandbox manager shows resource usage
as unavailable. Only the latest observation is stored, so there is no resource
history.

### The owner's Usage page

**Settings → Usage** (`/settings/usage`) shows each owner their own containers,
what the running ones use now, and what their work used over the last 7 or 30
UTC days. It reads three owner-scoped routes, none of which names a host,
container ID, image or generation:

| Route | What it reads |
|---|---|
| `GET /v1/agent/usage/containers` | Containers behind chats the owner has not deleted, active first, with limits and the latest sample of a running one. Fresh samples (90 seconds) are summed into current CPU and memory. |
| `GET /v1/agent/usage/resources?days=` | Per UTC day: running time from the lifecycle events in `agent_sandbox_events`, and measured CPU and memory from `agent_sandbox_usage_days`. |
| `GET /v1/agent/usage/tokens?days=` | The admin Usage aggregation, filtered to the owner's runs: reported tokens and turns by day and by model (see [Usage](#usage)). |

Running time counts the `starting`, `running` and `stopping` states, with history
back to migration `0046_admin_sandbox_trajectory`; CPU and memory history starts
with `0077_sandbox_usage_days`. Each accepted sample adds the time since the
previous one (at most 120 seconds), and its CPU and memory over that time, to the
container's rows for the UTC days it covers, split at midnight. The page shows
how much of the running time the measurements cover, and that its days are UTC.
It shows no disk usage and no cost.

## Usage

The owner's Usage page, the admin Usage page (`/admin/agent/usage`), a run's
usage footer (`/v1/agent/jobs/{id}/usage`) and the admin run detail all show the
tokens each turn's agent reported about its own model calls
(`source: "runtime_reported"`; the per-job read's `runtime.source` is
`"runtime"`). They are not a provider bill, and these pages hold no pricing,
quota or billing. Several accounts using keys of one provider account share that
account's allowance. The model relay keeps its own record of provided-model use
([Provided models](#provided-models)).

Each attempt's events are reduced once into an `agent_attempt_thread_stats`
rollup (reducer version 2). A repeated report counts once, a retry's attempt
counts as its own report, and a Fork's copied history is not counted again. A
turn that reported nothing shows as pending, then unavailable, never as zero.

| Agent | What it reports | Input total |
|---|---|---|
| Claude Code | Anthropic counters per response: uncached input, cache reads, cache writes, output | The three input counters added |
| pi | The same counters per assistant message | The three input counters added |
| Codex 0.145.0 | `thread/tokenUsage/updated` per model call: `inputTokens` (cached included), `cachedInputTokens`, `cacheWriteInputTokens`, `outputTokens`, `reasoningOutputTokens` | `inputTokens` |
| OpenCode, Kilo | No verified counter shape; any counter they emit is shown as reported | Unknown |

Only Codex reports reasoning tokens, which are part of the output and shown
beside it. No agent reports a cost or a request count, so those are always null.

**What is not counted.** Codex reports each model call with its thread's running
total. A call counts toward a turn only when the notification names that running
turn, or comes from a subagent spawned during it. Calls from a subagent after its
turn ended, or spawned in an earlier turn or process, notifications replayed on
resume, and malformed reports count nowhere, so a provider can bill more than the
totals show. The pages say so where the numbers appear.

## Reading a task's recorded events

The admin console shows tool-call measurements and reported token usage, but has
no route to a task's raw runtime events; owners read their own through the
session routes. **Do not read `agent_job_events.payload` directly.** A `psql`
query returns prompt text, tool arguments and tool output with no audit record,
no size bound and no column allowlist. If an investigation needs raw events,
build an audited route.

**The audit trail.** _Admin → Audit log_ (`GET /admin/agent/audit-log`) shows who
did what, to whom, with what result, newest first, paged by `before` (the oldest
`id` of the previous page). Each action is declared once in
`cloud_agent/domain/audit.py`, with a `category` (`accounts`, `settings`,
`servers`, `sandboxes`, `measurements`, `sensitive_access`, `access_control`),
its target (`target_type`, `target_id`) and the user it affected
(`target_user_id`).

- **Who acted** (`actor_type`, derived from `actor_id`): `admin`, `break_glass`
  (`ADMIN_TOKEN`, first-run setup, the bootstrap command), `service`, `system`,
  `user` or `anonymous`. The page shows `admin` and `break_glass` unless asked
  for everything (`actors=all`); break-glass access is always shown, in red.
- **Names** are kept as they were when the row was written, with a short id
  beside them. Erasing an account deletes no audit row.
- **Outcome** is `applied`, `refused`, `failed`, `replayed`, or `requested` for
  work that runs later. A requested sandbox deletion or compute operation
  carries its final result; the worker's steps are in the sandbox's trajectory.
- **Filters.** `user=<account id>` shows everything about an account, and
  `target_type=` with `target_id=` everything about one host, sandbox or server
  operation.
- **Recording never blocks an action.** A row that cannot be written is logged
  as `agent_admin_audit_write_failed`, counted in
  `agent_admin_audit_write_failures`, and reported at the top of the page for
  seven days.

Reads and connection checks are not recorded.

## Teams

Owners and admins manage their teams from **Settings → Teams**. Platform
administrators see every team, read-only, under **Admin → Users → Teams**:

- The list (`GET /admin/agent/teams`) shows teams newest first, with the owner,
  active members, admins and pending join requests. `state` is `active`
  (default), `deleted` or `all`; `q` matches part of a team name, a team id, or
  the user id of anyone who has been a member. It pages with `before` (the
  response's `next_before`).
- A team's page (`GET /admin/agent/teams/{team_id}`) lists the active members,
  the 50 most recent departures (`departed_member_count` counts them all), the
  50 oldest open join requests (`pending_request_count`) and the invitation link
  counts. `GET /admin/agent/teams/{team_id}/audit-events` pages the team's audit
  history with `before`.

**Show in People** opens a team's members in **Admin → Users → People**. Names
and emails are read on each request and never stored. These pages change
nothing, so they leave no audit record. You cannot yet move ownership of a team
whose owner lost access.

## MCP servers

MCP calls pass through the control plane. The sandbox holds only a scoped relay
credential; upstream credentials stay outside it:

```text
sandbox ──[residency mcp credential]──▶ MCP-only relay ──[owner credential]──▶ MCP server
```

A member's CLI serves several turns, so it holds its residency's credential
(`amr.…`). On every call, the relay resolves it to that chat's current attempt
and checks that the attempt is leased, its job running and not stopped, and its
host enrolled. Between turns, or once the residency rotates, expires or is
released, it authorizes nothing. A follow-up whose MCP servers did not change
keeps the live process; adding or removing a connection restarts it, because a
CLI reads its servers once, at start. A request keeps the attempt and residency
it was authenticated with, and the relay checks both again after waiting for
OAuth, an approval or an upstream session. Memory writes, Web Search admission
and approval claims also check the residency inside their transactions.

### Listeners and connections

The MCP listener is a separate FastAPI app on port 8001 that serves only the MCP
tool surface. Sandboxes reach `/v1/agent/mcp` on it over the MCP tunnel on their
group bridge.

**Keep the tool relay off the public main API.** The public edge forwards
`/api/*` to port 8000, where no tool route is mounted, so a leaked `mcp` token
cannot be replayed there. Connection management, OAuth callbacks and approvals
also use the `/v1/agent/mcp` prefix and must stay on 8000, so do not block the
whole prefix at the edge. Point the sandbox MCP tunnel at 8001.

Users add connections through `/v1/agent/mcp`: a remote URL, an imported
`mcpServers` snippet, or a stdio launch specification. `agent_mcp_connections`
stores the configuration and the credential, encrypted with
`AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` under an MCP-specific domain, and
discovery stores a capability snapshot. Templates only prefill the form. MCP
OAuth uses `AGENT_MCP_PUBLIC_BASE_URL` for the API callback and
`AGENT_WEB_BASE_URL` for the browser's return to `/integrations`, for example
`https://agents.example.org/api` and `https://agents.example.org`.

Of the optional protocol capabilities, the platform answers only `roots`. When a
server offers elicitation, sampling, resource subscriptions, tasks or MCP Apps,
the connection's diagnostics mark them unsupported.

### Authorization and routing

- **Nothing is reachable by name.** A tool name's prefix is a _binding alias_
  (the name one conversation gave one connection), and a token names a job, so
  the relay resolves the alias against that job's own bindings. Nobody can name
  a connection that someone else added.
- **Every capability has a policy:** `deny`, `confirm` or `allow`. An omitted
  capability is denied, including one the server adds later, and a policy whose
  tool description or schema changed goes back to the owner.
- **`confirm` holds the original call** until the owner decides in the
  conversation's Tools panel or the [Approvals inbox](web-app.md#tool-approvals).
  Approval dispatches it once, under the current attempt and permission fence,
  and the CLI gets the result without the model retrying. Denial, expiry or
  changed authorization drops it.
- **Credentials fail closed.** A missing encryption key, unreadable ciphertext,
  an expired connection with no refresh, or a disconnected connection removes
  that server from the owner's reachable set, and the Integrations page says
  why.
- **Bindings are read on every call**, so narrowing a session takes effect on
  its next call.
- **One endpoint.** `/v1/agent/mcp` merges the job's connections into one
  catalogue, which all five adapters receive as one entry named `session`. It
  answers requests only, and talks to legacy SSE servers itself.

Tools and prompts appear as `<alias>__<name>`, looked up in the catalogue, never
split on `__`; ambiguous or long names get a stable, bounded suffix. Resources
appear as `mcp-resource://<connection-id>/<original-uri>`. Resources and prompts
have their own permission scopes, decided like tools. An unresolved connection is
logged and skipped, so the others stay usable.

The merged endpoint also serves the platform's own tools: `web_search` when a
search provider is configured, and `memory_save`, `memory_update` and
`memory_delete` for every task (named in the claim by the markers
`:platform:web-search` and `:platform:memory`). A memory tool writes only for
the owner and task its fenced token names, and only to that task's repository or
the owner's user scope.

### End-to-end coverage

`tests/external/test_agent_mcp_end_to_end.py` runs a real listener, a migrated
Postgres, the image's pi 0.73.1 CLI (or the release named by
`AGENT_PI_TEST_VERSION`) and the shipped bridge, against stub model and MCP
servers. It checks that the model sees `session__github__get_issue`, that the
upstream receives `server/discover`, `initialize` and `tools/call` with the
connection's credential instead of the sandbox token and with the tool name
`get_issue`, and that a handshake follows when the era probe returns `-32601`.
It needs Postgres, npm and network access, skips without them, and belongs to
the external tier. Run it after changing the merged endpoint, host or bridge.

### Where a connection runs

An address runs as a `remote` service behind this relay, and a command as a
`sandbox` program ([sandbox MCP servers](#sandbox-mcp-servers)); the API refuses
any other location. Connections stored at the old `managed-worker` or
`user-connector` locations were moved to `sandbox`
([upgrade notes](upgrade-notes.md#mcp-servers-run-in-two-places)), and the
connector tables and `agent_mcp_connections.connector_id` are unused.

### Deployment and rollback

Apply migrations through head before replacing the API, then deploy the runner
and sandbox artifacts from the same release; MCP is on for every runtime. The
migration that introduced per-user connections put the legacy server names it
could not map unambiguously into `agent_mcp_legacy_bindings`
(`no_connection` or `ambiguous_connection`). Review those rows as the sessions
that need manual tool selection. The empty `agent_threads.mcp_servers` and
always-NULL `agent_mcp_connections.server` columns cannot authorize anything.

Before launching members, the runner upgrades retained containers' adapter files
and the patched Kilo 7.4.17 (built from the revision and patch recorded beside
`build-kilo.sh`; another version needs a reviewed build), and installs Codex's
system allowlist, with no host bind mount or extra capability. A rollback must
keep a schema-compatible service and the migrated data; fix forward when a
downgrade would discard them. MCP credentials share
`AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` with source control, separated by domain.

### Runtime boundaries

Claude Code retries transient inference failures (HTTP 429 and 5xx) within the
native request. The resident launch sets `CLAUDE_CODE_MAX_RETRIES=15` and
`CLAUDE_CODE_RETRY_WATCHDOG=0`, and the pinned CLI backs off from 500 ms to 32
seconds plus up to 25% jitter, honoring an integer `Retry-After` of up to 60
seconds. Retries keep completed tool calls and the native session; when they run
out, the turn fails with its context kept, and the runner never resends the
prompt. HTTP 400 and 403 are not retried, and 401 keeps the CLI's own credential
refresh. The runner's execution timeout still bounds the turn, owner
cancellation still interrupts a backoff, and the chat shows each retry.

An adapter declares MCP support only after both behaviors are checked against the
pinned binary: it points the CLI at the platform relay, and it stops every other
MCP configuration source from adding servers. A job that names connections on a
runtime without MCP support is refused at creation.

| Runtime | Pin | How the relay reaches it | Why the repository cannot add servers |
| --- | --- | --- | --- |
| `claude-code` | 2.1.220 | `--mcp-config` with the platform's servers | `--strict-mcp-config`, verified against the CLI: without it a committed `.mcp.json` is loaded alongside ours with no approval step |
| `opencode` | 1.18.9 | the reviewed `opencode-freeinference` wrapper merges the relay endpoints into the `OPENCODE_CONFIG` it writes | the wrapper exports `OPENCODE_DISABLE_PROJECT_CONFIG`, which covers both `opencode.json` and `.opencode/` |
| `kilo` | 7.4.17 | the reviewed wrapper configures the authenticated merged relay for run and serve; the live bridge verifies connection before reporting ready | the pinned build patches the legacy MCP loader to honor `KILO_DISABLE_PROJECT_CONFIG`, covering `.kilo/mcp.json` and `.kilocode/mcp.json` alongside the regular project configuration |
| `codex` | 0.145.0 | exec and app-server share the merged relay URL and token environment reference; app-server verifies MCP startup | trusted guest-root bootstrap atomically installs the deployment's exact relay URL as the sole server identity in `/etc/codex/requirements.toml` before launch; repository and user configuration cannot expand that system allowlist. After `AGENT_SANDBOX_MCP_URL` changes, a retained container's next Codex launch replaces that one-relay policy with the new URL, unless another Codex member of the group is still running; any other policy shape is refused, never replaced |
| `pi` | 0.73.1 | the reviewed `pi-mcp-bridge.ts` shipped at `/usr/local/libexec/`, loaded with an explicit `-e` | `--no-extensions` disables every other extension source (the project's `.pi/extensions/`, the global directory, and anything a settings file names), so the explicit path is the only way in. Verified against the pinned binary and against 1.2.0 from `@earendil-works/pi-coding-agent`, the package pi moved to: a repository's own extension does not load, the relay's tools are registered as `<alias>__<tool>`, and a call reaches the upstream. On 1.2.0 the flag also keeps pi's built-in MCP client off, so neither `.pi/mcp.json` nor the config directory's `mcp.json` starts a server, with the project trusted or not |

**Use the runtime's credential-reference syntax.** Claude Code expands
`${AGENT_MCP_TOKEN}`, and the OpenCode family substitutes
`{env:AGENT_MCP_TOKEN}`; the wrong syntax sends a literal placeholder as the
bearer token and gets 401. The adapters in
`apps/backend/cloud_agent_host/runtimes/` choose the address, and the CLI
adapters in `deploy/sandbox/` write the reference. The token travels in the
environment, never in argv or a configuration file.

### What `--strict-mcp-config` is holding shut

Always pass `--strict-mcp-config` with `--mcp-config`, even for an empty platform
configuration: it makes that configuration Claude Code's only source of MCP
servers. Without it, a committed `.mcp.json` loads with no prompt under the job's
configuration (headless `-p`, `bypassPermissions`, a checkout as the working
directory). Measured for hybridInference#1129, a directory containing

```json
{
  "mcpServers": {
    "evil_repo_server": { "command": "/bin/echo", "args": ["pwned"] }
  }
}
```

produces these two `system/init` lines, differing only in the flag:

| invocation | MCP servers reported by the CLI |
| --- | --- |
| `--mcp-config <ours>` | `evil_repo_server`, `github` |
| `--mcp-config <ours> --strict-mcp-config` | `github` |

## Sandbox browser

The sandbox image ships headless Chromium and `cloud-agent-browser`, a root-owned
launcher that runs one browser per sandbox. `cloud-agent-browser start` returns a
CDP WebSocket URL for Playwright (`connectOverCDP`, Playwright MCP
`--cdp-endpoint`), Puppeteer (`browserWSEndpoint`) or Chrome DevTools MCP
(`--wsEndpoint`). See the
[design](../agents/specs/2026-11-02-computer-use-base-capability.md).

- **Shared by the sandbox.** A chat and its Forks share one browser, tabs and
  sign-ins included. It runs as `agent-browser` (uid 10002), so a member's
  turn-end reap never stops it. The host runs `cloud-agent-browser service start`
  before each member launch, and a failure never blocks the chat.
- **Isolation.** Chromium speaks CDP only over a pipe to the launcher, which
  serves it on loopback behind a random token in `/run/agent-browser` (written
  only by `agent-browser`, readable only by the sandbox group). Clients cannot
  reach each other's CDP sessions.
- **Chromium's own sandbox is off.** Docker's default seccomp profile refuses the
  user namespaces it needs, under runc and Kata alike, so Chromium runs with
  `--no-sandbox`, and the container and the Kata VM are the boundary.
  `cloud-agent-browser status` reports `chromium_sandbox`.
- **Downloads** are staged by the service and copied into the member's private
  download directory. One that cannot be handed over shows as canceled, and the
  service log says why.
- **Lifecycle.** Chromium starts with the first client and stops after 15 minutes
  without one (`CLOUD_AGENT_BROWSER_IDLE_S`). The profile
  (`/home/agent-browser/profile`) stays in the retained container, and
  `cloud-agent-browser stop` closes Chromium for every member.
- **Resources.** About 174 MiB and 140 threads idle, and 321 MiB and 213 threads
  with four pages open, counted against the group's memory limit and its
  512-process limit, which counts threads. At most four renderer processes run.
- **Screenshots** go to `/workspace/.cloud-agent-artifacts`
  (`CLOUD_AGENT_ARTIFACTS_DIR`), which is listed in `.git/info/exclude`. A turn
  shows the images written there
  (`GET /v1/agent/jobs/{job_id}/artifacts/images`, up to 200), and
  `GET /v1/agent/jobs/{job_id}/files/image` opens PNG, JPEG, GIF and WebP files
  of up to 10 MiB.

To check a host, run `cloud-agent-browser status` and `cloud-agent-browser self-test`
in a sandbox; the image build runs the self-test too. `/run/agent-browser/daemon.log`
has the service's log. A container created from an image without the browser has
none until it is recreated.

## Sandbox MCP servers

An MCP connection can run in each chat's sandbox (location `sandbox`), where it
reaches the task's `localhost`, files and the sandbox browser; the Playwright and
Chrome DevTools presets drive that browser. See the
[design](../agents/specs/2026-11-02-computer-use-base-capability.md#in-sandbox-mcp-servers-phase-2).

- **What may run.** One exactly pinned package started by `npx` or `uvx`, with no
  mount or working directory. An import pins a bare name, or a tag such as
  `latest`, to the release the public registry names at that moment
  (`cloud_agent/mcp/package_pins.py`); a range or an unknown package is refused.
- **Credentials.** Keys pasted with it are sealed at rest and named
  `secret://NAME`, and `${NAME}` is filled from the chat's environment. The host
  hands both only to the slot's runner, in
  `/run/agent-mcp-slots/<slot>/secrets.json`, which only that runner's user can
  read. A sealed key shorter than 8 characters is refused, because the runner
  masks only longer keys. An edit keeps the stored keys only while the command
  and arguments stay the same apart from the package release; any other change
  must send the keys again.
- **How it runs.** Before launching a member's CLI, the host writes the slot's
  admission as root, starts the image's MCP gateway, `cloud-agent-mcp`, as
  `agent-mcp` (uid 10003) on `http://127.0.0.1:47801/mcp`, then starts the
  slot's runner as `agent-mcp-<slot>` (uid 12001 + slot). The CLI reaches the
  servers as the MCP server `sandbox`, with its relay token. A server affects
  only its own slot, and a failure leaves the chat without those tools but still
  runs the turn.
- **No approval step.** The relay's per-tool policies do not apply: a sandbox
  server can do what the agent's shell can. Changes take effect at the next
  turn.
- **Where to look.** `/run/agent-mcp/gateway.log` records the methods each slot
  called (readable as `agent-mcp`); `/run/agent-mcp-slots/<slot>/runner.log`
  and `/run/agent-mcp-slots/<slot>/<alias>.log` are readable as
  `agent-mcp-<slot>`; admissions are in `/run/agent-mcp-admissions`. When a CLI
  lists a slot's tools, the gateway reports them to the relay
  (`cloud_agent/sandbox_tools`), and the connection's card shows them.

## Sandbox incidents

The host records what goes wrong in each retained sandbox as **incidents** in the
sandbox's event list, and the sandbox list shows the newest one within a day under
the sandbox's name. See the [design](../agents/specs/2026-11-04-sandbox-incidents.md).

- **Kinds.** `out_of_memory` (the OOM killer killed a process),
  `container_exited` (a `die` nobody asked for), `memory_high` and `pids_high`
  (a reading at 90% of the limit), `browser_crashed`, and `mcp_server_failed` (a
  sandbox MCP program could not start or exited on its own; it names the chat and
  the server). A stop or deletion the platform made is not an incident.
- **Repeats.** Repeats of a kind within a minute become one `.repeated` row with
  a count. A sandbox records at most 60 rows an hour, and one `dropped` row
  counts the rest.
- **What people see.** A running chat shows a line for `out_of_memory`,
  `container_exited` and its own `mcp_server_failed`, and the agent's next prompt
  lists what happened since it was last told.
- **Contents.** Kinds, times, exit codes, limits and MCP aliases only: no stderr,
  URL, command line or environment.
- **Where to look.** The browser service journals to
  `/run/agent-browser/events.jsonl`, and each slot's runner to
  `/run/agent-mcp-slots/<slot>/events.jsonl`. The sandbox manager reads them
  every minute and holds the rows until a heartbeat answer says
  `incidents: "accepted"`.

## Before a job can run for real

Provision each host, check its network, enroll it and set up the credentials
before enabling job claims.

### 0. The sandbox network needs its host rules, a resolver and its relay

Every sandbox group runs on its own routable bridge, the same for setup, the
agent and each Fork sibling. This deployment's host nftables tables decide what
it reaches: public IPv4 on TCP 80/443, the resolver in `AGENT_EGRESS_DNS_SERVER`,
the group's relays (MCP, plus the model and GitHub relays where configured) and
the SSH servers its owner enabled, and nothing else. There is no mode to choose
and no setting that opens it further. [The sandbox network](#the-sandbox-network)
has the full rule set and what each refusal means.

Before it can run a job, a host needs:

- `AGENT_EGRESS_CHAT_NETWORK_POOL` and `AGENT_EGRESS_DNS_SERVER`. Neither has a
  default: the pool must be this deployment's own, and the resolver is the only
  DNS a sandbox can use.
- The MCP relay on the relay network, `AGENT_RELAY_NETWORK` (the host compose
  derives `<COMPOSE_PROJECT>-agent-relays`), declared `internal: true`. No
  sandbox group joins it: lifecycle code finds the MCP tunnel there by alias and
  attaches it to each group's bridge, and refuses the group network before the
  Agent CLI starts if the alias has no running container, or more than one. Let
  Compose create this network; one created by hand is refused because its labels
  do not match.
- A kernel with nftables, bridge family included.
- Docker 28 or newer, client and daemon. The relay joins each group bridge with
  gateway priority -1, so that a group bridge never becomes its default route
  (`freeinference-chat-net-*` sorts before `<COMPOSE_PROJECT>_internal`). At the
  default priority, the relay's SSH session would meet the sandbox rules and die
  for every group sharing it.

Checks run at four points:

1. `sandbox_host.sh prepare` validates the network settings with the candidate
   runner image's parser before draining any runner.
2. `sandbox_host.sh check` requires HTTP 200 from MCP `/healthz`, sent from
   containers on the relay network, before publishing the ready marker. Name
   resolution alone does not prove that the tunnel's upstream is up.
3. Each runner's preflight installs and reads back the host tables before it
   claims anything.
4. Each group cold start checks inside the guest, as the sandbox user, for a
   default route, the configured resolver and the relay's TCP port. A failure
   stops the sandbox, keeps its workspace and fails the turn.

For a runner on another machine, the MCP relay is an SSH tunnel to the MCP-only
listener; see [Running runners on another machine](#running-runners-on-another-machine).

Lifecycle code creates the `freeinference-chat-net-*` group networks through the
Docker API: one `/29` from the pool each, named from the group and generation,
with host interfaces named from this deployment's prefix. A group's bridge is
deleted on an idle or lost stop, while the container's writable layer stays, and
is recreated on wake. Sandbox Manager reconciliation removes labelled bridges
left by an interrupted create after a five-minute grace period. A bridge that an
environment test or prebuild still uses is removed by that run; one that
reconciliation cannot remove, such as one with an unknown container attached, is
logged as `chat_network_prune_failed` and retried on the next pass.

### 1. Only enrolled sandbox hosts run jobs

Jobs run only on enrolled hosts. Deploy at least one host through
`deploy/host/deploy.sh` and enroll it; its runner then polls
`/v1/agent/worker/claim`, and claims nothing until its replica attests the
retained protocol, image and runtime pins, sandbox manager configuration,
execution profile and host capacity.

**Admin → Servers → Add server** writes the host's `.env`
([Compute resources](operations-compute.md)), and you write none of it by hand.
Besides the enrollment credential, it holds `COMPOSE_PROJECT`,
`AGENT_SANDBOX_IMAGE`, `AGENT_EGRESS_CHAT_NETWORK_POOL` and
`AGENT_EGRESS_DNS_SERVER` (each interpolated with `:?` in
`docker-compose.sandbox.yml`, so an unset one stops the render),
`AGENT_SANDBOX_MANAGER_TOKEN`, the two tunnel destinations with their own keys
and known-hosts files, and the three sandbox manager reverse-forward settings.

### 2. Credentials

**Host credential.** Mint a short-lived enrollment token for a named host:

```bash
# Admin API; the response shows the single-use token once.
POST /admin/agent/runner-hosts/enroll-token
```

Put it in `AGENT_ENROLL_TOKEN` for the first start. Registration replaces it with
a long-lived credential bound to that host, stored under `AGENT_HOST_STATE_ROOT`,
which every heartbeat and claim then uses. A host name in a request is never
identity by itself.

**Sandbox manager credential.** One `AGENT_SANDBOX_MANAGER_TOKEN` per
environment, with the same value on the control plane and every host's Sandbox
Manager. A new installation generates it, and server setup writes it into the
hosts it sets up. It authorizes file, terminal, Git and session-lifecycle calls,
is not a claim credential, and never enters a chat container.

**GitHub credential.** Install a GitHub App and give the _control plane_ its
private key. The platform signs a nine-minute App JWT (`github_app.py`; GitHub
refuses more than ten) and exchanges it for an hour-long installation token for
the runner's clone credential, branch lookups and repository reads, so nothing
is minted or rotated by hand. The key reaches the container through a read-only
bind mount at a fixed path, and two different variables are involved:

```bash
# The *host* file that holds the PEM. This is the mount source.
AGENT_GITHUB_APP_PRIVATE_KEY_FILE=/etc/cloud-agent/github-app.pem
```

```yaml
# compose: $AGENT_GITHUB_APP_PRIVATE_KEY_FILE ──▶ /etc/cloud-agent/github-app.pem:ro
```

`AGENT_GITHUB_APP_PRIVATE_KEY_PATH`, the path **inside** the container, defaults
to that mount, so the Compose deployment sets only
`AGENT_GITHUB_APP_PRIVATE_KEY_FILE`. Put the file on the host, owned by the
service user, with `chmod 600`. Alternatively, save an inline PEM in Admin
Settings: it is encrypted and takes precedence over the mounted file. A legacy
`AGENT_GITHUB_APP_PRIVATE_KEY` value is imported once.

**The saved GitHub App ID decides whether GitHub is configured.** It is imported
once from `AGENT_GITHUB_APP_ID`. The key path always has a default, and
`/dev/null` is mounted there when there is no App. With the App ID set, a key
that cannot be loaded (missing, empty, unreadable or invalid) does not stop the
control plane (issue #47):

- Integrations and the composer say GitHub is temporarily unavailable. Creating
  a job (temporary workspaces included), a runner's claim or a branch lookup
  answers `503 source_control_unavailable` with `Retry-After: 30`, and queued jobs
  and goal wakeups wait without spending retries.
- The log has one `ERROR` event, `source_control_load_failed`, with a `reason`
  (`key_file_unreadable`, `key_missing`, `key_invalid`, `unexpected`) and, for a
  file, its path and the OS error. It repeats only when the reason changes.
  Queued jobs that cannot start also fire the `queue_not_starting` alert.
- The key is read again every 30 seconds, so fixing the file in place recovers
  within a minute (`source_control_load_recovered`). Changing the host path of
  the bind mount needs the container recreated.

**App permissions.** Checkout, pushes and pull requests need `contents: write` and
`pull_requests: write`; an owner's user token holds only what both the App and
the owner may do. Without `workflows`, GitHub refuses a push that changes a file
under `.github/workflows/`. For **Create repository** in the pickers, also grant
**Repository creation: write**; GitHub also accepts the broader
**Administration: Read and write** (see GitHub's
[repository creation permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps#repository-permissions-for-repository-creation)).
Owners of existing installations may have to approve the new permission, and
without it repository creation reports that the App cannot create repositories.
Creation calls `POST /user/repos` with the user's own token, in their personal
account, private, with a README. It adds no local grant, so on an installation
limited to selected repositories, the owner grants access through
**Configure repository access**. To revoke everything, uninstall the App.

**Connect flow.** For users to authorize their own repositories, the App also
needs its OAuth half:

```bash
AGENT_GITHUB_APP_CLIENT_ID=Iv1.xxxxxxxx
AGENT_GITHUB_APP_CLIENT_SECRET=...
AGENT_GITHUB_APP_INSTALL_URL=https://github.com/apps/<app-slug>/installations/new
# App setting "Callback URL": <agent web origin>/connected?provider=github
#   for example https://agents.example.org/connected?provider=github
```

Set the App's Callback URL to the **agent web's own origin**, where the callback
page and session cookie live. The authorize link sends no `redirect_uri`, so
GitHub uses the App's first Callback URL. Move it whenever the web origin moves:
a stale callback breaks the connect flow without any error from GitHub. The
Connect link is built from `AGENT_GITHUB_APP_CLIENT_ID` and GitHub's OAuth
endpoint, and `AGENT_GITHUB_APP_INSTALL_URL` (`/apps/<slug>/installations/new`)
is offered after connecting and behind **Configure repositories**. Keep the two
separate: GitHub sends an installed App's install link to its settings page
without calling the callback, and an install URL pointing at the OAuth endpoint
loops a user with no installation through authorization.

The runner's clone token is a separate installation token, requested as
`contents: read` on the one repository being worked on, so checkout does not
inherit the App's broader permissions.

#### The GitHub relay

No GitHub token reaches a sandbox. Each member's residency carries a placeholder
(`agh.`) that git and `gh` present to GitHub, and commits name the owner as
author (`GIT_AUTHOR_*`, from the owner's noreply address) with
`AGENT_GITHUB_COAUTHOR` as co-author. Every group bridge resolves `github.com`
and `api.github.com` to the host's `github-tunnel`, so every process in the
sandbox reaches the relay. Before the first process starts, a root exec writes
the deployment's relay CA, and a bundle of the image's roots plus the CA, to
`/etc/freeinference/`, root-owned and read-only. The tunnel forwards TLS unopened
to the control plane's GitHub relay, a listener of its own that holds the
certificate.

The relay accepts the placeholder only while its residency serves a running,
leased, unstopped turn on an enrolled host. It then sends the request with the
owner's GitHub App user token when the request concerns the conversation's
repository and is on the relay's allowlist, and with no credential otherwise. It
refuses repository administration, secrets, GraphQL, branch deletion and tags. A
pull request it sees created is recorded for the conversation. The control plane
renews the owner's token, so a long turn does not outlive it.

To turn it on:

1. Run `deploy/control-plane/github-relay-certs.sh <dir>`. Keep `ca.key`
   offline; give `relay.pem` and `relay.key` to the control plane
   (`AGENT_GITHUB_RELAY_TLS_DIR`) and `ca.pem` to every host
   (`AGENT_SANDBOX_GITHUB_RELAY_CA_FILE`).
2. Give `relay.key` to the relay's user. The `github-relay` container runs as
   uid 10001 and reads the directory through a read-only bind mount, which keeps
   host ownership, so a key the operator owns at `0600` fails with
   `PermissionError`. Run the script as root, which sets the key to
   `10001:10001` and `0400`, or run `sudo chown 10001:10001 <dir>/relay.key` as
   the script prints. Move `ca.key` out of `<dir>` before you mount it.
3. Enable the `github-relay` Compose profile on the control plane and on each
   host, and give each host's `github-tunnel` its own SSH key, limited by
   `permitopen` to the relay's port (`AGENT_GITHUB_TUNNEL_*`).
4. Set `AGENT_GITHUB_COAUTHOR` to the App bot's noreply address.

Nothing on the platform scans what goes through the relay; branch protection,
GitHub's push protection and the repository's CI still apply
([design](../agents/specs/2026-11-04-agent-native-pull-requests.zh-CN.md)).

Without an App, the runner clones anonymously, which works for public
repositories only. Without the App and the relay, agents cannot push or open pull
requests: jobs still run, and their changes stay in the retained workspace,
readable in the Git pane or as a download. There is no static-token fallback
(`AGENT_GITHUB_TOKEN` is not read), and a host without the relay gives the
sandbox no placeholder. The workspace is a shallow checkout of the pinned commit
and its parent, with the base branch as a local branch at that commit, so a
review can diff without fetching.

## Running self-hosted

Administrators can also attach an SSH server or provision a CloudLab experiment
from **Admin → Servers**, and select the ready host as the location for new
sandboxes ([compute-resource runbook](operations-compute.md)). Retained sandboxes
stay on their original host, and neither Remove nor Forget destroys the machine.

Run the host agent as a **standing service**, so queued jobs are claimed after
crashes and reboots. **Add server** writes the host's `.env` and runs the
commands below for you; installing a host from a hand-written `.env` is not
supported, and this section is for operating a host that exists. Its `.env` holds
the enrollment token and the shared sandbox manager token. The example below
includes the shared-kernel setting for trusted internal repositories; untrusted
repositories need the default `kata` backend and
[Kata provisioning](#provisioning-kata-on-a-runner-host).

```bash
AGENT_ENROLL_TOKEN="<single-use token for this host>"
AGENT_SANDBOX_MANAGER_TOKEN="<same environment value as the control plane>"
AGENT_SANDBOX_BACKEND=container  # explicit trusted-repository downgrade
```

`AGENT_SANDBOX_BACKEND=container` is an **explicit downgrade**: sandbox groups
share the host kernel. Compose uses `restart: unless-stopped`, so enable the
host's Docker daemon at boot. The host lifecycle commands are:

```bash
deploy/host/sandbox_host.sh up      # build images, tunnel + runners
deploy/host/sandbox_host.sh preflight # validate this host against an existing image
deploy/host/sandbox_host.sh check   # probe all relay/control paths
deploy/host/sandbox_host.sh status  # containers + recent log tail
deploy/host/sandbox_host.sh down    # stop them

# `up` in its two halves, for a deployer that has to put something between
# them. Nothing in `prepare` replaces a container that is serving.
deploy/host/sandbox_host.sh prepare  # pull or build, Kata smoke test, candidate check
deploy/host/sandbox_host.sh activate # drain, replace, prove
```

Use the standalone host stack even on the control plane's machine, with tunnel
destinations pointing to its local listeners. `preflight` checks Docker, the
retained-session configuration, host isolation and the configured image, and
`up` repeats those checks for the image it deploys.

### Host configuration reference

`sandbox_host.sh` validates the required environment before starting and prints
the runner's preflight verdict: control plane reachable, retained image and
runtime usable, Docker data root correct, relay network present and route-less,
host network rules installed and read back. The equivalent by hand:

```bash
# The image carries exactly the source-reviewed pins, which change together
# with SHARED_SANDBOX_RUNTIME_PINS. Other agent versions are installed from
# Admin -> Settings -> Agent versions, without a rebuild.
docker build -f deploy/sandbox/Dockerfile.agent-sandbox \
             -t freeinference-cloud-agent-job:latest .

AGENT_SANDBOX_IMAGE=freeinference-cloud-agent-job:latest \
AGENT_HOST_SLOTS=4 \
  docker compose -f deploy/host/docker-compose.sandbox.yml \
               up -d
```

`AGENT_SANDBOX_IMAGE` has no default: the runner and the Sandbox Manager both
interpolate it with `:?`. Tag it per environment (`…-job:staging`, `…-job:prod`)
so a rebuild on one side cannot replace the other's image. All hosts share one
queue (`claim_job` uses `FOR UPDATE SKIP LOCKED`), with no leader and no
sharding.

| Variable | Default | Notes |
| --- | --- | --- |
| `AGENT_SANDBOX_BACKEND` | `kata` | Deploy refuses `kata` until the host has the shim and a real container proves it gets its own kernel. `container` is an explicit shared-kernel downgrade. `process` (no isolation) refuses to start unless `AGENT_SANDBOX_ALLOW_UNISOLATED=1` |
| `AGENT_SANDBOX_IMAGE` | required | Image built from `Dockerfile.agent-sandbox`, or an equivalent deployment-owned image |
| `AGENT_RUNTIME_DOWNLOAD_NETWORK` | `bridge` | Docker network for the download step of an agent update from Admin Settings. Only npm runs there, with package scripts off; the scripts and the check run with no network ([design](../agents/specs/2026-10-15-agent-cli-updates-design.zh-CN.md)) |
| `AGENT_RUNTIME_REGISTRY_URL` | public npm | Registry for agent updates until the host's first heartbeat answer; after that, **Agent package registry** in Admin → Settings → Agent versions. pi updates come from `@earendil-works/pi-coding-agent`, so a private mirror must carry it as well as the image's `@mariozechner` package |
| `AGENT_EGRESS_CHAT_NETWORK_POOL` | required | This deployment's RFC1918 IPv4 pool, one `/29` per active group bridge. Keep it disjoint from every other deployment's pool on the machine (of two that overlap, the host rules refuse the one installed later) and from the host's Docker, VPN and private-service routes |
| `AGENT_EGRESS_DNS_SERVER` | required | The one IPv4 resolver sandboxes may use, on TCP and UDP 53, and the only exception to the protected ranges. Not loopback, and not inside the pool; see [Choosing the resolver](#choosing-the-resolver) |
| `AGENT_EGRESS_PROTECTED_CIDRS` | none | Extra IPv4 blocks to refuse on top of the built-in ranges, such as an institution's services on public addresses. It can only add protection. Set the same value on the control plane, whose SSH server and model connection checks refuse these blocks too |
| `AGENT_RELAY_NETWORK` | derived | Where lifecycle finds the MCP relay: `<COMPOSE_PROJECT>-agent-relays`. It must exist and be `internal`, and no sandbox group joins it. Set it only to name an existing network |
| `AGENT_RUNNER_HOST` | host name | The enrolled execution environment that the admin placement switch picks between; `sandbox_host.sh` fills it from the host's name. After enrollment the persisted credential decides identity, so editing it renames nothing. Unset keeps the host out of the pool |
| `AGENT_PHYSICAL_HOST` | `hostname -s` | Display-only name of the physical server, used to group the admin inventory. Set it explicitly when invoking Compose directly |
| `AGENT_HOST_SLOTS` | `3` | Turns at once until an admin sets a number on the Servers page; 2 to 128 |
| `AGENT_RETAINED_DOCKER_DATA_ROOT` | `/var/lib/docker` | Expected daemon `DockerRootDir`. Keep the default; see [Retained Docker storage](#retained-docker-storage-on-a-host) |
| `AGENT_HOST_STORAGE_PROBE` | `overlay_root` | Probe behind the Servers page's Storage column; `off` turns it off. See [How full is the sandbox storage?](#how-full-is-the-sandbox-storage) |
| `AGENT_STORAGE_MIN_AVAILABLE_MIB` | `1024` | Bootstrap value for **Storage reserve for new sandboxes**, replaced by the admin setting after the first settings heartbeat. Below it, a new sandbox allocation is declined without spending the turn; existing sandboxes may resume. It is an admission guard, not a disk quota |
| `AGENT_SANDBOX_UID` / `_GID` | `10001` | Only for a custom sandbox image; must match its user |
| `AGENT_REPO_ALLOWLIST` | none | `owner/name` or `owner/*`, comma-separated; see [the deployment allowlist](#who-may-run-a-job-against-which-repository) |
| `AGENT_GITHUB_APP_CLIENT_ID` / `_CLIENT_SECRET` | none | The App's OAuth half, needed only for the connect flow |
| `AGENT_GITHUB_APP_INSTALL_URL` | none | `https://github.com/apps/<slug>/installations/new`; see [2. Credentials](#2-credentials) |
| `AGENT_GITLAB_OAUTH_CLIENT_ID` / `_CLIENT_SECRET` | none | GitLab.com OAuth application, with only the `read_user` and `read_api` scopes |
| `AGENT_GITLAB_OAUTH_REDIRECT_URI` | none | Exact registered callback, normally `<frontend>/agents/connected?provider=gitlab`; HTTPS except for localhost |
| `AGENT_SANDBOX_MCP_URL` | derived | Host only: the MCP-only relay URL as this host's sandboxes see it, derived from the MCP tunnel alias. Never point it at the full control API |
| `AGENT_MCP_TUNNEL_*` | none | MCP-only listener destination and its own restricted SSH key and known-hosts; see `ENVIRONMENT.md` |

Nothing reads `AGENT_EGRESS_SETUP_TIER`, `AGENT_EGRESS_AGENT_TIER`,
`AGENT_EGRESS_ALLOWLIST`, `AGENT_EGRESS_NETWORK_*`, `AGENT_EGRESS_PROXY_*`,
`AGENT_EGRESS_STOP_GRACE`, `AGENT_SANDBOX_NETWORK`,
`AGENT_SANDBOX_ALLOW_OPEN_NETWORK` or `AGENT_RUNNER_REPLICAS`; delete them from
`.env`.

Start either OAuth flow only from the signed-in `/agents/integrations` page. The
OAuth state is bound to that user and provider, expires after ten minutes and
works once; GitLab also uses PKCE. GitLab tokens are encrypted at rest, rotated
on refresh, never returned to the browser, and revoked best-effort on
disconnect. GitLab support covers identity and discovery only: the control plane
verifies the GitLab user and lists their projects, but GitLab projects do not
appear in the task composer, and it neither reads their source nor opens merge
requests.

### Provisioning Kata on a runner host

Kata gives each sandbox group a lightweight VM with its own kernel. Use it for
untrusted repositories, whose build and test scripts run arbitrary code; plain
containers share the host kernel and are for trusted internal repositories only.
Kata needs an x86_64 Linux host with KVM, bare metal or with nested
virtualization. Install the runtime on the **host** (the runner image has only a
Docker client, and the host daemon must resolve `io.containerd.kata.v2`), once,
as root:

```bash
sudo deploy/host/setup_kata_runtime.sh
```

The script is idempotent. It checks for `/dev/kvm` and CPU virtualization before
downloading, verifies the release against a pinned SHA-256, and links the shim
into `/usr/local/bin`. After the pinned version changes in the script, `--check`
fails on every host still carrying the old one.

#### Guest tracing and member isolation

The setup selects the release's `vmlinuz-debug.container` kernel, which carries
the eBPF/BTF support the process observer needs, and sets
`runtime.disable_guest_seccomp=false`, without which Kata ignores the OCI seccomp
filter; `--check` verifies both. Runner preflight then starts a disposable
sandbox shaped like a real guest and runs the observer and tool-marker
self-tests, so a missing BPF syscall, BTF image, tracepoint or exec permission
fails the deployment before the runner claims work. See Kata's
[debug-kernel build contract](https://github.com/kata-containers/kata-containers/blob/main/tools/packaging/kernel/README.md#debug-kernel).

Only the fixed observer and lifecycle helpers run as guest root. Agent CLIs and
repository code run under per-member UIDs with all capabilities dropped and
`no-new-privileges`, and cannot touch the root-only journal. The bounding set
keeps `BPF`, `PERFMON`, `SETUID`, `SETGID`, `CHOWN`, `DAC_OVERRIDE`, `KILL` and
`SYS_PTRACE` for those helpers, and ordinary exec enters through
`setpriv --no-new-privs`, because Kata 3.32 does not inherit PID 1's setting.
Preflight requires non-root execution, zero effective and permitted
capabilities, `NoNewPrivs=1` and `Seccomp=2`. Privileged helpers verify the
inspected container's Kata runtime first, so an old runc container can never
become a guest-root target.

Per-tool attribution is diagnostic only, never a security or billing authority.
Member turns report aggregate CPU, memory, I/O and network as unavailable
(`coverage=0`, `sampler_version=shared-member-v1`), because Docker statistics
cover the whole shared container; per-call numbers come from the eBPF process
census, which charges a process to the one tool call whose window contains its
start, and marks ambiguous calls partial.

#### Faster VM boot settings (opt-in)

Two installer settings may shorten the VM boot that every new or resumed group
waits for. Both stay off until they are qualified
([#1054](https://github.com/HarvardMadSys/cloud-agent/issues/1054)). To try them
on one host:

```bash
sudo KATA_STATIC_SANDBOX_RESOURCES=true KATA_DEFAULT_MEMORY_MIB=1024 \
  deploy/host/setup_kata_runtime.sh
```

- `KATA_STATIC_SANDBOX_RESOURCES=true` sets
  `runtime.static_sandbox_resource_mgmt`: each VM boots with its container's
  `--cpus` and `--memory` already added, instead of hot-plugging them. The VM
  cannot grow later, which the platform never needs, and a VM larger than the
  host's memory plus 1 GiB fails to start.
- `KATA_DEFAULT_MEMORY_MIB` sets `hypervisor.qemu.default_memory` (Kata's default
  is 2048 MiB), added to every VM on top of its container's limit for the guest
  kernel, its page tables and the Kata agent. The installer refuses less than
  256 MiB.

They go into one drop-in, `config.d/cloud-agent-vm-boot.toml`, which `--check`
reads back; it refuses any other file there and any change to this one. An
install without the settings deletes the drop-in. The control plane's automated
deploy of a managed host installs prerequisites by default, so it reverts the
settings unless they are passed again. VM templating stays off, because it shares
kernel memory and RNG/KASLR state across tenants, and so does the uncompressed
debug kernel, which would boot every guest at the same kernel addresses.

#### Guest networking

Docker's embedded DNS resolver (`127.0.0.11`) is outside the guest, so after each
Kata activation a guest-root helper writes the relays' bridge addresses into
`/etc/hosts` and `AGENT_EGRESS_DNS_SERVER` into `/etc/resolv.conf`; under runc
the container gets `--dns` instead. Kata 3.32 does not apply live network
changes to a running guest, so a running sandbox that is not on exactly its group
bridge is refused until stopped.

#### Existing runc groups and activation checks

A backend change does not change an existing container's runtime, and the
privileged helpers refuse old runc containers. When switching a host from runc,
keep its retained runc groups on a compatible sandbox manager or migrate them
separately; do not delete their workspaces. Installing the shim does not prove
that a sandbox reaches its relays, so check from the host checkout, with its
`.env`, before switching:

```bash
AGENT_SANDBOX_BACKEND=kata deploy/host/sandbox_host.sh preflight
AGENT_SANDBOX_BACKEND=kata deploy/host/sandbox_host.sh check
```

`preflight` verifies the image and guest kernel. `check` starts relay probes under
Kata on the relay network, requires HTTP 200 from MCP `/healthz`, and checks the
live runner's sandbox preflight (which installs and reads back the host tables),
the sandbox manager and the control-plane paths. A bare `alpine getent` probe or
the compose `..._internal` network proves nothing here. Then drop
`AGENT_SANDBOX_BACKEND=container` from the `.env` and redeploy: `up` requires the
shim **and** a real container from the sandbox image reporting a kernel other
than the host's. Nothing falls back to a shared kernel; that takes
`AGENT_SANDBOX_BACKEND=container`, and every deploy warns about it.

#### Scheduling and runtime attestation

`AGENT_DEFAULT_EXECUTION_PROFILE` is `untrusted-kata-v1` in
`deploy/control-plane/docker-compose.yml`, so **a deployment whose hosts have not
been through Kata provisioning claims no secure work at all**. Set it explicitly
in the `.env` once a Kata host passes preflight, so the value is visible. A
secure job is claimable only when, in one claim transaction, the caller presents
the host's current enrollment credential, the runner replica reports
`io.containerd.kata.v2`, and preflight saw the configured image boot a guest
kernel different from the host's. The claim, not the host's heartbeat row,
supplies the attempt's runtime, image digest, CLI version and kernel stamp.
`trusted-v1` selects the shared-kernel boundary for trusted internal
repositories. Follow-ups and forks inherit the thread's execution profile, and a
retained chat's image digest is fixed in its container labels.

### How many runners

Each chat runs in a group that shares one retained container and workspace; Fork
adds a member with its own native session and CLI process, and members run turns
concurrently.

#### Execution slots

Set a host's concurrency on the admin Servers page (**Execution slots**), or with
`PUT /admin/agent/runner-hosts/<host>/slots` and `{"slots": N}`; `null` returns
it to the host's `AGENT_HOST_SLOTS` default (3). The range is 2 (a fork's sibling
runs beside its parent) to 128. The limit is how many turns the host runs at
once; the rest wait in the queue. A sandbox gets 2 vCPU and 4 GiB by default, so
budget roughly that per slot. The host reads a new limit from its next heartbeat
answer, so nothing restarts.

Saving is not applying, and the page shows the difference. Claims admit against
the lower of the saved number and the host's reported `slots_total`, so lowering
takes effect at once (running turns finish as _finishing_), while raising waits
for the host to confirm (_applying N_). An offline host shows _N not applied_,
and a runner too old for remote limits shows _redeploy to change_. Only
**Host default** returns the host to its environment value.

#### Member process cleanup

Each member has its own UID. Before handing over a turn, the host records that
UID's processes with the root-owned `member-process-reaper`; cleanup passes the
baseline to a fresh helper over stdin, rechecks each process's identity before
signalling, escalates TERM to KILL, and fails closed if anything survives. The
baseline never lives in a member-writable file, so repository code cannot edit it
to keep a process alive.

#### Fork compatibility and turn ordering

The claiming process must advertise `shared-sandbox-v1`. Forking from an earlier
point also needs `native-fork-at-anchor-v1`, and either the host's current image
is the group's own, or the group's recorded `claude-session-fork` helper
(`agent_sandboxes.fork_helper_version`) is the reviewed one and the host's image
still ships the CLI version the container runs. A group with an older or unknown
helper, such as one copied from a legacy database, needs the exact image.
Sending a message into a running turn follows the same CLI-version rule.

The Docker socket (or a reviewed mTLS Engine endpoint) is the host's only
interface to chat state; nothing is mapped from the host. Every turn that has
not started is `queued`, and a runner claims one only after every earlier turn in
the chat is terminal.

#### Removing a server

Open the server's details on the admin Servers page and choose **Remove server**.
Confirming deletes its allocation, nodes, hosts, host settings, capacity history,
registration tokens, admission holds and compute activity; shared connections,
deployment profiles, conversations, runs and deleted-sandbox audit records stay.
First:

1. Release its cloud resources (an attached server without release support can
   be removed once its hosts are disabled).
2. Delete its retained sandboxes and wait for cleanup to finish.
3. Clear the default placement pin, running attempts and deployment drains.

Removal also refuses with _The external operation is unresolved_ while the
server's **Activity** tab shows:

- an operation still running, or waiting to confirm a provider call: wait for
  it, or stop it;
- an operation whose outcome is `unknown`, such as an acquisition whose create
  request timed out: retry it or, for an acquisition, adopt the experiment it
  created;
- on a server that never recorded its provider resource, an operation suggesting
  the provider has one: release the server, after checking the provider for a
  resource the request created.

Once the provider confirms the machine is gone, stopped operations no longer
hold the server. The API is `DELETE /admin/agent/servers/<allocation_id>` with
`{"expected_revision": N}` (refresh after a revision conflict), or
`DELETE /admin/agent/runner-hosts/<host>` for a host without an allocation. Stop
an unmanaged host agent first; removal does not touch its machine or files.

#### Sandbox settings

The **Sandbox** tab of **Admin → Settings** holds the sandbox and workspace limits
for every server: turn time limit, repository size limit, container CPU, memory
and disk, idle timeout, sessions per sandbox, workspace command and file limits,
and terminal limits (the less common ones under Advanced). The **Sandbox** table
in a server's details on the Servers page overrides them for that server. The API
is `PUT /admin/agent/host-settings` and
`PUT /admin/agent/runner-hosts/<host>/settings` with
`{"settings": {"<key>": N}}`; `null` clears a key. A server's value wins over the
global one, and otherwise the built-in default applies. `ENVIRONMENT.md`
§_Sandbox settings from the admin pages_ lists the precedence and initial
variables; once imported, `.env` edits have no effect.

Settings travel in the heartbeat answer, so the page shows _applying_ until the
host's report matches. Nothing running is cut short: a turn keeps its time limit,
an existing sandbox keeps its size and capacity, and a shortened terminal
lifetime closes terminals already past it at the next sweep. A runner without
`host-settings-v1` keeps its `.env` values until redeployed.

Container size applies to each new group (default 2 vCPU, 4 GiB, disk 0, meaning
no quota) and stays with it through follow-ups, restarts, snapshots and Fork. A
positive disk limit bounds the writable layer with `--storage-opt size=`, which
needs `overlay2` on XFS mounted with project quotas (`pquota` or `prjquota`);
elsewhere Docker refuses to create the container.

**Set a disk limit on servers that run untrusted work.** A Kata sandbox's
writable layer, workspace and installed files included, shares one disk with
every other sandbox on the server, so without a limit one tenant can fill it for
all. Set **Container disk** above 0 once the Docker data root is on XFS with
project quotas. It is not enforced, but the control plane and runner log
`agent_untrusted_sandbox_without_disk_quota` when an unlimited server gets an
untrusted group, and the Servers page shows **No container disk quota**.

#### Sandbox archives

The **Sandbox archives** card under **Admin → Settings → Sandbox** stores the
deployment-wide archive policy. When enabled, it captures an initial and a final
lifecycle archive of each sandbox under `s3://<bucket>/cloud-agent/sandbox`, and
**Archive to S3 and delete** uses the same bucket
([Sandbox archive storage](#sandbox-archive-storage)). The prefix is fixed; the
bucket defaults to `freeinference`.

The card saves, as one revision, the bucket, enabled state, AWS region, the
credentials, and (under Advanced) the maximum compressed archive size, multipart
threshold and part size. Uploads send eight parts at a time, so one upload holds
up to 64 MiB in memory at the default 8 MiB part size. Authenticate with an AWS
access key or with the workload-identity pair: a customer-managed KMS key ARN and
the expected 12-digit bucket owner. With an access key the pair is optional, and
uploads use SSE-S3 unless a KMS key is set. The secret is write-only, encrypted
at rest, sent to the sandbox manager on the authenticated heartbeat channel and
kept only in its memory; it never enters a container or the audit row. Updates
carry the revision last read, so two administrators cannot overwrite each other.
The API is admin-only `GET` and `PUT` `/admin/agent/sandbox-archive-settings`.
Do not put the policy or an access key in `.env`. To inspect the policy and its
rollout in Postgres:

```sql
SELECT sandbox_archive_settings,
       sandbox_archive_revision,
       sandbox_archive_updated_at
FROM agent_runner_policy
WHERE id IS TRUE;

SELECT host,
       sandbox_archive_settings_applied_revision
FROM agent_runner_hosts
ORDER BY host;
```

The bucket must have versioning enabled. Grant the hosts' AWS workload identity,
or the access key:

- `s3:GetBucketVersioning` on `arn:aws:s3:::<bucket>`;
- `s3:ListBucket` on the bucket, limited to the `cloud-agent/sandbox/*` prefix,
  so an exact-key `HeadObject` can tell a missing object from a refusal;
- `s3:GetObject`, `s3:GetObjectVersion`, `s3:PutObject` and
  `s3:AbortMultipartUpload` on `arn:aws:s3:::<bucket>/cloud-agent/sandbox/*`;
- for SSE-KMS, `kms:GenerateDataKey` and `kms:Decrypt` on the configured key;
- optionally, for snapshot cleanup, `s3:DeleteObject` and
  `s3:DeleteObjectVersion` on `arn:aws:s3:::<bucket>/cloud-agent/sandbox/env/*`
  (see [Environment installation snapshots](#environment-installation-snapshots)).

Configure lifecycle retention and access logging on the bucket, with an
`AbortIncompleteMultipartUpload` rule for hosts that vanish mid-upload. Archives
need hosts that advertise `sandbox-archive-settings-v1`. The card shows the saved
revision beside the revision each sandbox manager has confirmed, and while
archives are enabled, a host or runner replica that has not applied the current
revision claims nothing.

**What is archived.** The initial object captures `/workspace` once, after source
and setup and before the first prompt, and is immutable. Each clean stop managed
by the sandbox manager flushes native Agent state and stores a final object with
`/workspace`, `/agent-state` and the optional
`/home/agent/.freeinference-platform` state, replacing the previous final key
(bucket versioning keeps older versions). Turns while the sandbox stays live
create nothing. Final capture runs inside the stop hook, within the size limit
and deadline, and an exhausted upload is a failed clean stop. Nothing lists,
downloads or replays these archives: they are for export and forensics, while
moving a sandbox uses [restorable checkpoints](#restorable-checkpoints).

Policy changes apply forward only: a sandbox that ran while archives were off has
no initial object, and disabling them can leave an initial object without a
final one. For paired archives, enable the policy and wait until the card shows
the revision applied on every server before admitting new work.

The first prompt waits for the initial capture, not for S3. The bundle is written
under `AGENT_SANDBOX_ARCHIVE_STATE_DIR/initial-uploads/<sha256(namespace)>/` and
uploaded in the background (`sandbox_initial_archive_uploaded`,
`deferred: true`), with retries after 5 seconds, 30 seconds and 2 minutes, then
every 10 minutes (`sandbox_initial_archive_deferred_retry`). Until it is
uploaded, that directory holds the only copy of the pre-prompt workspace, so keep
it on persistent disk and let it drain before removing a host. When it is full
(8 GiB) or unwritable, the capture uploads before the prompt instead
(`sandbox_initial_archive_handoff_unavailable`).

A stop the Sandbox Manager did not perform cleanly (a host crash or reboot, a
Sandbox Manager that exited, a `docker stop` behind its back) has no final
archive. It records a `skipped` final marker (`sandbox_final_archive_skipped`)
and the chat continues through the ordinary native-resume checks.

**Changing the bucket.** New archives go to the new bucket and restores read from
it, but checkpoints and snapshots are read back by object version, so those in
the previous bucket cannot be restored. Change it only when no retained group
still needs a checkpoint from the previous bucket:

1. Redeploy every host first. Only a host that advertises
   `sandbox-archive-destination-v1` can use a bucket other than `freeinference`;
   with archives enabled, the others claim no work, and the card lists them:

   ```sql
   SELECT host, status
   FROM agent_runner_hosts
   WHERE status <> 'disabled'
     AND NOT capabilities ? 'sandbox-archive-destination-v1'
   ORDER BY host;
   ```

2. Keep the previous bucket writable by every host's identity and the saved
   access key (old and new), with its KMS key usable, until every host has
   reported the new revision and no final archive is pending. A stop fenced
   before its host applied the new revision still finishes in the previous
   bucket, and its group refuses every start and deletion until it does.
3. Wait for pending finals, which the database does not show. The sandbox
   manager logs `sandbox_final_archive_failed` (with sandbox id and generation)
   each time one fails; one in progress finishes within 12 minutes of its
   container stopping, plus up to 12 for its checkpoint. Wait until every host
   reports the new revision, then until no host has logged that event for an
   hour.
4. Treat restore points captured before then as being in the previous bucket
   (`agent_sandbox_checkpoints` does not record the bucket). Until its next clean
   stop, such a group cannot move to another host. To list them, with the end of
   the wait as the cutoff:

   ```sql
   SELECT g.id, g.host, g.state, c.captured_at
   FROM agent_sandboxes g
   JOIN agent_sandbox_checkpoints c ON c.id = g.restore_checkpoint_id
   WHERE c.captured_at < '<cutoff>'::timestamptz
     AND g.deletion_state NOT IN ('pending', 'archived', 'deleted')
   ORDER BY g.host, c.captured_at;
   ```

A snapshot in the previous bucket stays on offer, but only a host that already
holds its image can use it; elsewhere the load fails as `unavailable` and the
group installs in full. Before rolling the control plane back to a release
without bucket selection, reset the bucket to `freeinference`: an older control
plane refuses any other bucket, and no turn is claimed.

#### Restorable checkpoints

While archives are enabled, every clean archived stop also stores a checkpoint:
the container's changes over its sandbox image, as an OCI image archive at
`cloud-agent/sandbox/v2/<sha256(sandbox id)>/g<generation>/<checkpoint id>.oci.tar`.
The key names the sandbox, not the host, so another server can restore it (see
[Restoring a removed host's sandboxes](#restoring-a-removed-hosts-sandboxes)).
Checkpoints are on by default; clear **Save restorable checkpoints** on the card
to turn them off. They use the archive bucket, credentials, size limit and
permissions, and each object is written once.

- A checkpoint never fails a stop: it runs after the final archive, within the
  same deadline, and a failure logs `sandbox_checkpoint_failed`.
- The Sandbox Manager reads the new layers from containerd's content store,
  mounted read-only at `AGENT_CONTAINERD_CONTENT_DIR` from
  `AGENT_HOST_CONTAINERD_CONTENT_DIR` (default
  `/var/lib/containerd/io.containerd.content.v1.content`), checking every digest.
  When that mount is missing or belongs to another daemon, it logs
  `sandbox_checkpoint_content_unavailable` and falls back to
  `docker image save`, which is far slower.
- The latest receipt per sandbox is kept under
  `AGENT_SANDBOX_ARCHIVE_STATE_DIR/checkpoints` and repeated on every heartbeat;
  the control plane records it in `agent_sandbox_checkpoints` as `reported`, then
  `verified` once a restore has started it. The admin sandbox page shows the
  latest.
- A receipt covers its group only while the group's start fence (`start_seq`) is
  still the one its stop ended. Every path that can start the container advances
  the fence first, and a stale request is refused with 409 and retried.
- The checkpoint holds the writable layer (workspace, native session state and
  anything installed outside `/workspace`), not the credential tmpfs mounts.

#### Restoring a removed host's sandboxes

When a host leaves the pool (**Remove** on the Servers page, or the autoscaler
releasing its node), its credential is revoked, and each stopped group whose
latest checkpoint still covers it becomes `cold` in the same transaction. A
checkpoint covers a group when it matches the group's container, generation and
start fence, records every member's last settled turn, and has a base image
another host can pull by digest. Other groups stay bound to the removed host and
continue only if it is enrolled again; the admin sandbox page shows which is
which. A provider confirming that a node terminated, or a host
[moving out before its lease ends](operations-compute.md#moving-sandboxes-out-before-a-lease-ends),
also turns covered groups `cold`.

A workspace request (Files, Terminal, Git or an environment retry) to a group
still bound to a removed host answers `workspace_unavailable` (409), and Fork is
refused; a covered group answers `workspace_restoring` (409) until a turn
restores it. A `cold` group does not block **Forget** on its old host, and once
that host is gone, **Delete without archive** deletes it at once.

The next turn of any chat in a `cold` group can be claimed by any host that
advertises `sandbox-restore-v1` with archives enabled, except the one still
holding the group's container (container names carry no generation). The claim
moves the group and advances its generation (`restoring`), and the new host's
Sandbox Manager:

1. pulls the base image by digest if needed;
2. downloads the checkpoint version the receipt names and checks its size and
   SHA-256;
3. checks the archive (one image, every blob verified, every layer the base
   image's or in the archive) and rewrites `index.json` and `manifest.json`
   itself;
4. loads it, creates the container with the group's limits and network, and
   resumes each native session.

Restores need only the archive permissions hosts already have. Downloads are
unnamed files in `AGENT_SANDBOX_ARCHIVE_STATE_DIR/checkpoint-spool`; a leftover
`restore-*` file there comes from an older Sandbox Manager and can be deleted
while no restore runs. The loaded image,
`freeinference-checkpoint/<sandbox hash>:<checkpoint id>`, is removed with its
container.

A restore that fails before the group starts removes what it created (logging
`sandbox_create_cleanup_failed` or `sandbox_checkpoint_image_remove_failed` if it
cannot; `docker rm --volumes` or `docker image rm` clears the rest). The group
returns to `cold` and the turn to the queue without spending an attempt, and is
not claimed again for 30 seconds, 5 minutes and 30 minutes after its first three
failures; the fourth marks it lost and fails its queued turns.

A turn that was running when its host disappeared is not covered and ends as
LOST. A group created from a locally built image cannot move, so autoscaled hosts
should pull the published image (`AGENT_SANDBOX_IMAGE_PULL=true`, see
`ENVIRONMENT.md`). A host enrolled again after its groups moved cannot use them;
remove its old containers with the admin **Discard** action.

#### Environment installation snapshots

A new task whose environment has an install script can start from a saved
installation: a container after installation, without the repository's files,
stored like a checkpoint
([design](../agents/specs/2026-10-15-environment-install-snapshots-design.zh-CN.md)).

- **Requirements.** Archives and **Save restorable checkpoints** must be on, and
  prebuilds go only to hosts that advertise `environment-snapshot-v1`.
- **Storage.** `cloud-agent/sandbox/env/v1/<sha256(owner)>/<snapshot id>.oci.tar`,
  under the checkpoint rules. The building host keeps the image as
  `freeinference-environment/<owner hash>:<snapshot id>`, and other hosts load it
  on first use after the same checks as a restore.
- **Building.** Only `prebuild` runs build snapshots, in disposable containers,
  never from a chat or a test. One is queued when a new task had none, its
  lockfiles changed, the owner asked, or an environment test passed. Prebuilds
  use environment-run slots. After a failed build, tasks queue none for that
  installation for 6 hours, and a prebuild no host started within a day is
  failed.
- **Use.** A new group uses a snapshot only on a host whose sandbox image is the
  snapshot's base, when the setup fingerprint (an HMAC of the install script and
  setup-time variables, keyed by `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY`) and the
  checkout's lockfiles match. Otherwise it installs in full; a snapshot never
  fails a turn.
- **Retention.** A newer snapshot, or a change to the install script or a
  setup-time variable or Secret, supersedes older ones, which are deleted after
  24 hours. A snapshot is not offered after 25 days, and is deleted after 7 days
  unused, when its environment is archived or deleted, or beyond 20 GiB of ready
  snapshots per owner.
- **Deletion.** Hosts poll `/v1/agent/worker/environment-snapshots/deletions`
  every 10 minutes and ask `/v1/agent/worker/environment-snapshots/retired`
  which local `freeinference-environment/*` images to drop. Without the optional
  delete permissions, the host logs `environment_snapshot_delete_failed` and
  retries; as a backstop, give the `cloud-agent/sandbox/env/` prefix a lifecycle
  rule that expires current versions after 30 days and noncurrent ones after 1
  day.
- **Preparation marker.** Installation writes
  `/var/lib/freeinference-broker/environment-setup-v1.json`; a container from a
  snapshot or checkpoint with a matching fingerprint skips the install script and
  runs the optional update script.
- **Team environments.** A team environment's snapshot serves every active
  member and counts toward the quota of the member who queued it; only the
  team's owner and admins can delete or rebuild it.

#### Launch failures and queue order

If a turn process cannot start, or exits before taking delivery, the host
returns the turn with `POST /v1/agent/worker/jobs/<job>/release`, spending no
attempt; once the CLI has admitted the turn, release is refused, so a prompt
never runs twice. Consecutive launch failures pause claims with backoff and show
on the Servers page. Run one runner container per host: scaling it would enroll
the host twice. The queue is global FIFO with no per-user fairness or quota.

### The idle clock and safety watchdogs

`AGENT_CHAT_IDLE_TIMEOUT_S` (default **1800 seconds**) is a group's only
inactivity timeout. Once every member's activity has settled and no operation is
active, the container is stopped and its bridge removed (never `docker rm`); the
next acquire recreates the bridge and starts the same container. At startup,
sandbox manager reconciliation stops running orphans before readmitting work; a
group it cannot reconcile is logged as `shared_sandbox_reconcile_group_failed`
without affecting the others. Closing a terminal by hand counts as activity, and
a running container found with no attempt or deadline gets one fresh 30-minute
window.

#### Lost attempt leases

Before a prompt reaches the CLI, the worker records its `job_id` as the
sandbox's admission receipt. If that attempt's lease then expires, the system
stops the activation, marks the context LOST and fails the job without retrying,
so no prompt or tool runs twice. Messages queued behind it fail too, and a new
follow-up gets `sandbox_context_lost`. The reaper waits at most 5 seconds per
member for the host's sandbox manager, so an unresponsive host does not hold up
other hosts.

#### Safety watchdogs

These guards add no idle states and do not stop a healthy group:

| Setting | Default | What it bounds |
| --- | --- | --- |
| `AGENT_TIMEOUT_S` | 3600 s | One hung agent turn, which is cancelled and fenced |
| `AGENT_TERMINAL_IDLE_TTL_SECONDS` | 30 min | A terminal whose client and process have gone quiet; in a retained chat, the earlier of this and the chat clock applies |
| `AGENT_TERMINAL_HARD_TTL_SECONDS` | 8 h | Any one terminal, however busy |
| `AGENT_RESIDENCY_TTL_S` | 1800 s | A member's ownership of its live process; losing it restarts the member and ends an unfinished activation as LOST |

No timeout removes chat data: a retained sandbox goes only by an administrator's
deletion or when its owner deletes every chat using it, so monitor Docker storage
separately. See [sandbox settings](configuration.md#sandbox-settings-from-the-admin-pages).

#### Resume, Fork, and pull requests

All five agents are claimable only after image, runtime and helper attestation.
A follow-up sends only the new prompt to the resident process, and recovery
resumes the pinned native session in a new process. Fork is immediate inside the
same container, from any settled turn with a persisted native ordinal, and Edit &
rewind forks from the nearest earlier such boundary with the replacement text in
the composer. The UI shows context as `NEW`, `LIVE`, `RESTORED` or `LOST`.

A settled turn leaves its changes in the retained workspace; every follow-up
starts from the conversation's own base, so the Git view shows all its changes.
Pull requests the agent opens through [the GitHub relay](#the-github-relay) are
recorded in `agent_thread_pull_requests`: a job's `published_pr_url` is the first
its turn opened, and `workspace_pr_url` the conversation's newest. A Fork does
not inherit them, and a pull request opened on GitHub directly is not recorded
([design](../agents/specs/2026-11-04-agent-native-pull-requests.zh-CN.md)).
Admission checks free space only through the
[storage reserve](#how-full-is-the-sandbox-storage).

### Rolling deployment order

Downstream deployment automation runs the host and control-plane scripts; this
repository publishes neutral artifacts (see
[the release contract](../../deploy/release/README.md)). The two tracks can run
in parallel behind an exact-SHA readiness fence.

#### Host and control-plane readiness

On a host, activation:

1. prepares the new sandbox and runner artifacts and proves Kata, before
   invalidating the ready marker;
2. stops the old runner gracefully, with no time limit: it stops claiming and
   waits for every admitted turn to finish, while the sandbox manager and
   tunnels stay up;
3. replaces the sandbox manager and runner, runs the isolation, tunnel, runtime
   and Docker-root checks, records `.deployed-sha`, and only then publishes the
   `shared-sandbox-deployment:<sha>` capability.

Meanwhile the control-plane deployment prepares its artifacts and runs
migrations. Before `compose up` replaces a running API, it waits for a heartbeat
newer than the start of its readiness check and reverse-probes every sandbox
manager eligible for new-chat placement, and every online host that still owns a
retained group. `SHARED_READY_TIMEOUT` (default 900 seconds) bounds that wait.
The claim route enforces the same capability: a pending runner registers and
heartbeats but does not poll, so a runner with a pending or failed marker takes
no work, even during a cold-start bootstrap.

A host of a compute allocation the control plane manages is not waited for,
because only the new control plane rolls it forward
([Compute resources](operations-compute.md)). If it owns a retained group, it
must send a fresh compatible heartbeat and answer its sandbox manager, and it
starts no new chats until it reports the target commit.

The deploy user writes `.shared-live-ready` in the deployment checkout; runners
mount only that file, read-only, at `/run/cloud-agent/shared-live-ready`. The
host script keeps its inode when changing `pending:<sha>` to `<sha>`, so mounts
see the change, and a partial write cannot advertise a target SHA.

#### Rollback compatibility

The deployment orchestrator runs separately from the target checkout (in GitHub
Actions, from the workflow commit). Before changing Docker state, it refuses a
target without the current `deploy/DEPLOY_PROTOCOL` or with an incompatible
value. The current protocol, `shared-live-exact-sha-v4`, marks the one sandbox
network: a v3 deployer cannot install it, and no deployment rolls back across it
(a host that ran the previous network needs the
[one-time cleanup](#one-time-cleanup-when-upgrading-to-the-one-network)).
Commits from before the schema baseline (`0001_schema_baseline`, 2026-09-10) are
not rollback targets either, because the database no longer records their
Alembic chain. For downstream Actions triggered by `workflow_run`, the workflow
comes from that repository's default branch: update the orchestrator there
before relying on a new fence, and do not dispatch an older workflow during the
rollout.

Database changes are expand-first. Never downgrade columns under a running new
API: roll code back to a schema-compatible commit first, and treat any later
contract migration as a separate reviewed operation.

The permanent floor is the drain-fence V2 release
(`0051_host_drain_fence_epoch`). The orchestrator must reject a control-plane
recipe without `AGENT_DEPLOY_DRAIN_FENCE_V2`, and once V2 has run, do not
downgrade the application or schema below it; the recipe keeps
`AGENT_DEPLOY_DRAIN_FENCE_V1` only so an older orchestrator can do the bridge
upgrade. Every host has a `drain_fence_epoch` that rotates on each change to its
status, credential, fence or compute node. Acquiring or transferring a fence
must present it with the expected owner token, and a database trigger enforces
the rotation and refuses to delete a host with an active fence. A host that went
`offline` and comes back keeps its inactive receipt, and delayed requests stay
fenced.

Inspect fence state before a rollback or recovery:

```sql
SELECT host, status, slots_used, drain_fence_token,
       drain_fence_status_before, drain_fence_active, drain_fence_epoch
FROM agent_runner_hosts
WHERE drain_fence_token IS NOT NULL;
```

If an active fence is abandoned, redeploy the V2 release and use its
token-conditional release (or the audited force override). If that is
impossible, stop the control plane and every deployment workflow, and clear one
host's fence in a transaction that checks its token, epoch and `slots_used = 0`:

```sql
BEGIN;
SELECT host, status, slots_used, drain_fence_token, drain_fence_active,
       drain_fence_epoch
FROM agent_runner_hosts
WHERE host = '<exact-host>'
FOR UPDATE;

UPDATE agent_runner_hosts
SET status = 'online',
    drain_fence_token = NULL,
    drain_fence_status_before = NULL,
    drain_fence_active = FALSE
WHERE host = '<exact-host>'
  AND status = 'draining'
  AND slots_used = 0
  AND drain_fence_token = '<expected-token>'::uuid
  AND drain_fence_epoch = '<expected-epoch>'::uuid
  AND drain_fence_active
RETURNING host, drain_fence_epoch;
COMMIT;
```

Require exactly one returned row. The returned epoch is the newly rotated value;
do not assign it yourself. To clear only an inactive receipt, use the same host,
token and epoch predicates with `NOT drain_fence_active`, and change the status
only when the recovery requires it. Never bulk-clear fence fields. If the owner,
token or epoch is uncertain, leave the host draining and restore the V2 control
plane.

**Rolling a Sandbox Manager back past #763.** An older Sandbox Manager cannot
upload an **Archive to S3 and delete** request that a newer one prepared but had
not uploaded. It answers HTTP 500 and logs a `FileNotFoundError` for
`archive.tar`, and the control plane marks the deletion failed
(`sandbox_manager_unavailable`, Sandbox Manager HTTP 500) without retrying.
Nothing is lost: the container stays and its chats continue. The newer layout's
`container-rootfs.tar` and `archive.head` (up to 5 GiB) stay in the request's
directory, and nothing removes them. As root on each rolled-back host, remove
them from every request directory that holds `abort.json` or `deleted.json`, and
leave everything else. If the host's `.env` sets `AGENT_HOST_STATE_ROOT`, export
that value first.

```bash
archives="${AGENT_HOST_STATE_ROOT:-/var/lib/hybridinference/agent-host}/sandbox-archives"
for request in "$archives"/*/; do
  if [ -e "${request}abort.json" ] || [ -e "${request}deleted.json" ]; then
    rm -fv "${request}container-rootfs.tar" "${request}archive.head"
  fi
done
```

To retry, refresh the sandbox's details and, once it is stopped and idle, choose
**Archive to S3 and delete** again (**Retry archive and delete** on the admin
sandbox page). Rolling forward again needs nothing more.

#### Cold starts and failed deployments

On failure, the existing control plane keeps running and the deploy stops for
repair. On a true cold start, with neither the API nor the MCP relay running,
the host cannot complete its readiness probes, so after migration the control
script starts only the missing services. A failed gate stops only the services
that invocation started, and web does not start until the exact-SHA fence
succeeds. Stale `AGENT_RESTARTABLE_*` or `AGENT_SHARED_LIVE_*` values change
nothing. A later API or web probe failure leaves the new stack running for
diagnosis but does not write `.deployed-sha`.

### Preserving an old-chain database

A database from before the independent-accounts release, on either the
pre-2026-09-10 Alembic chain or the current one, is keyed by HybridInference
Gateway owner IDs. This release does not serve, convert or map such a database:
the identity-namespace check refuses it before anything is migrated. Keep it
untouched as the archive and rollback source, with its dump, checksum,
configuration and root keys, and start the release on a new, empty database
([cutover](upgrade-notes.md#independent-accounts-cutover)).

To read old history with the current schema,
`scripts/convert_legacy_database_copy.py` converts a separately restored copy of
a `0025_retained_followup_gate` database, keeping every original row in
`legacy_archive`. The converted copy is still Gateway-owned and refused like the
original, so never point a deployment at it. See
[the legacy database copy plan](../agents/specs/2026-09-18-legacy-database-copy.md).

### Rebuilding the agent database

A Gateway-era database has nothing to carry forward: its owners, connections and
MCP grants belong to Gateway user IDs that no account holds. Archive it as
described above and start fresh. Re-enroll hosts with new enrollment tokens;
owners register, apply and add their model connections again.

### Applying migrations that are not backwards-compatible

`deploy.sh` migrates before replacing the API, so each migration must stay
compatible with the running version until the replacement, and most do. The
revisions below need stopped writers when you upgrade across them; check the
actual source and target versions before choosing a rolling deploy.

| Revision | Why the old API cannot run beside it |
| --- | --- |
| `0002_request_keys_and_actors` | Drops the default on `agent_admin_audit_log.actor_id`; the old API's audit inserts would fail silently and lose rows |
| `0003_thread_level_config` | Drops `agent_jobs` columns the old API reads on every job |
| `0004_residency_on_membership` | Drops `agent_thread_residencies`, which the old API writes on every residency heartbeat. It refuses to run over an unfinished fork |
| `0005_queued_follow_ups` | Replaces the `waiting` job state with ordered `queued` turns, which the old scheduler would claim out of order. Its downgrade also needs stopped writers, and refuses while a queued follow-up depends on an unfinished turn |
| `0008_generic_mcp_connections` to `0011_mcp_rolling_compat` | Rebuild MCP connections and bindings (see [Deployment and rollback](#deployment-and-rollback)). Apply through head before starting the new control plane and MCP relay |
| `0058_mcp_connector_invariant` | Repairs MCP connector routes and adds a unique constraint. Older binaries could recreate or keep stale connector authority, so never run a mixed-version deploy across it |

The runner holds no database connection (its heartbeats are HTTP calls to the
control plane), so stopping the control plane, the MCP relay and the web tier
leaves the database with no writer. The `psql` commands below run in the bundled
Postgres container against its `POSTGRES_DB`, the database `AGENT_POSTGRES_DB`
names, which must be the one `AGENT_DATABASE_URL` uses
([database settings](../../ENVIRONMENT.md#database)).

1. Drain the hosts, keep the generated `cloudagent-undrain-*.sql`, and wait for
   both counts to reach zero; neither can settle once the writers stop:

   ```bash
   docker exec -i cloudagent-postgres-1 sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' <<'SQL'
   SELECT count(*) FROM agent_attempts WHERE status = 'running';
   SELECT count(*) FROM agent_session_forks WHERE state IN ('preparing', 'materialized');
   SQL
   ```

   A fork still `preparing` or `materialized` is one its coordinator has not
   finished, and the reaper that settles it runs inside the API. A stale
   `preparing` operation fails after the stall window (15 minutes by default).

2. Stop the writers:

   ```bash
   docker stop cloudagent-control-plane-1 cloudagent-mcp-relay-1 cloudagent-web-1
   ```

3. From the deploy root, with the target commit checked out, build and run the
   target's `migrate` image while nothing writes:

   ```bash
   export AGENT_DEPLOYMENT_SHA="$(git rev-parse HEAD)"
   # `keys` builds the control-plane image that `migrate` runs.
   docker compose -p cloudagent --env-file .env -f deploy/control-plane/docker-compose.yml build keys
   docker compose -p cloudagent --env-file .env -f deploy/control-plane/docker-compose.yml \
     run --rm --no-deps migrate
   ```

4. Undrain the hosts recorded in step 1, then deploy the target commit as usual.
   Its migration is now a no-op, and readiness can see a host that may claim:

   ```bash
   docker exec -i cloudagent-postgres-1 sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' \
     < cloudagent-undrain-$(date +%F).sql
   deploy/control-plane/deploy.sh "${AGENT_DEPLOYMENT_SHA}"
   ```

### Retained Docker storage on a host

Retained chat state lives in each container's Docker writable layer, and with
the containerd image store the images live beside it under containerd. For
durable same-host operation, put both stores on a mounted data disk and leave the
daemon paths at their defaults: `docker info` must report
`DockerRootDir=/var/lib/docker`, and `AGENT_RETAINED_DOCKER_DATA_ROOT` stays
`/var/lib/docker`. A custom `data-root`, `graph` or containerd `root` is refused
before the host starts.

Automatic SSH bootstrap sets this up from the server's
[sandbox storage path](operations-compute.md#sandbox-storage-path). On a manual
host, with `<disk>` an existing writable mount (for example
`/scratch/hybridInference-cloud-agent/sandbox`):

1. Bind the two stores:

   ```text
   <disk>/docker      bind -> /var/lib/docker
   <disk>/containerd  bind -> /var/lib/containerd
   ```

2. Persist the binds in `/etc/fstab`:

   ```text
   <disk>/docker /var/lib/docker none bind,nofail,x-systemd.requires-mounts-for=<disk> 0 0
   <disk>/containerd /var/lib/containerd none bind,nofail,x-systemd.requires-mounts-for=<disk> 0 0
   ```

3. Give each daemon a drop-in,
   `/etc/systemd/system/docker.service.d/cloud-agent-storage.conf` and
   `/etc/systemd/system/containerd.service.d/cloud-agent-storage.conf` (naming
   `/var/lib/containerd` instead), and run `systemctl daemon-reload`, so that a
   missing disk keeps the daemon stopped instead of opening an empty store:

   ```ini
   [Unit]
   RequiresMountsFor=<disk> /var/lib/docker
   ```

To move a store that already has data, copy it while the daemons run, stop Docker
and containerd, and copy again with `--delete` until the copy exits 0. Move the
old directories aside, create empty mount points, bind-mount and start the
daemons; do not point `daemon.json` at the new path. Containers with
`restart=no` must be started by hand. Delete the old copies only after a reboot
comes back on the binds.

The sandbox manager refuses an `AGENT_RETAINED_DOCKER_DATA_ROOT` that differs
from the daemon's `DockerRootDir`, but cannot check the mount itself without a
host-root mount, so the `RequiresMountsFor=` drop-ins are the mount check.

- Do not bind `<disk>`, `/var/lib/docker`, `/var/lib/containerd`, `/workspace` or
  `/agent-state` into the sandbox manager or a sandbox.
- Do not run `docker system prune`, container TTL cleanup, `docker rm` or image
  garbage collection against `org.freeinference.chat-sandbox=true` objects;
  operate on them through Docker (`docker inspect`, `docker exec`, `docker cp`)
  and the sandbox manager, not by walking overlay directories.
- Back up or move the stores only with both daemons stopped, preserving
  ownership, hard links, extended attributes and sparse files.

With the binds in place, `docker stop` and `docker start` survive restarts of
every service and of the host. Another host can continue a group only from its
[restorable checkpoint](#restoring-a-removed-hosts-sandboxes). A missed
heartbeat or an unreachable sandbox manager does not prove the state is gone:
keep the chat pinned, recover or re-enroll the same named host, and reconcile its
container. Only an inspection on the owning daemon may mark the context `LOST`;
never create a blank replacement elsewhere.

### How full is the sandbox storage?

The Servers table's **Storage** column shows how much of the filesystem holding
the sandbox writable layers is in use, in `df` terms. It covers the filesystem,
not one chat; a stopped container still holds its repository, dependencies,
changes and native session there. The same reading feeds
**Storage reserve for new sandboxes** (default 1024 MiB): below it, the runner
declines a turn that would create a sandbox and returns it to the queue. Free
Docker storage or lower the reserve to allow new allocations
([sandbox settings](configuration.md#sandbox-settings-from-the-admin-pages)).

#### What the reading measures

A runner shares the sandboxes' Docker daemon and storage driver, so
`os.statvfs("/")` inside it describes the writable layers' filesystem. `used` is
`total - free`, `available` is what an unprivileged writer may still use, and the
percentage is over `used + available`, so free blocks reserved for root are in
neither.

#### Attesting the storage topology

A project quota (`overlay2.size`, or `--storage-opt size=` on XFS) would make
`statfs` report the quota instead of the disk. `sandbox_host.sh` therefore
compares the runner's numbers with `df` of the **mount root** of the upper
directory's filesystem, and writes its verdict to `.storage-probe/verdict.json`
beside `.shared-live-ready`. The mount root's size must equal the runner's
`total_bytes` exactly; a size mismatch with `df <upperdir>`, or `overlay2.size`
in `daemon.json` or on the `dockerd` command line, gives `unsupported`. When a
non-root deployment user cannot traverse Docker's snapshot directories, a
locked-down temporary helper (no network, capabilities or Docker socket) reads
the two directories through read-only binds; it needs **Docker 25 or newer on the
host**, and older Docker reports `unverified`.

Each runner rereads the verdict on every sample and publishes a number only for
its own upper directory with a matching size; otherwise the column shows
`unverified` or `unsupported`. A rebuilt container gets a new upper directory, so
an old verdict stops applying by itself.

#### Refreshing or disabling the metric

Compose binds `.storage-probe/` read-only at `/run/cloud-agent/storage-probe`;
keep the directory. Deleting `verdict.json` reports `unverified` until
`sandbox_host.sh check` recreates it. A host still on the old
`.storage-probe-verdict` file bind needs a normal `up`.
**Storage verification never blocks a deployment**: failures are warnings.

```bash
# Re-run the checks without redeploying — this also refreshes the verdict.
deploy/host/sandbox_host.sh check

# Turn the metric off entirely (the rollback). The `.env` is the deploy
# checkout root's, beside `.deployed-sha`, which is where every reader looks.
echo 'AGENT_HOST_STORAGE_PROBE=off' >> .env
deploy/host/sandbox_host.sh up
```

The `up` is required, because a container's environment is fixed when it is
created. The variable takes only `overlay_root` or `off`.

#### Measurement limits

The reading is block space: `available` does not promise that a container can
write (inodes, quotas and read-only remounts still fail), it includes every other
writer on the filesystem, and it does not predict how many more sandboxes fit.
Rootless, remote-Docker, btrfs, ZFS and devicemapper hosts report `unsupported`,
and Docker Desktop shows `unverified`.

### Which machine runs the jobs

**Admin → Servers** lists every enrolled execution environment with
authenticated heartbeats, grouped by the physical server it reported. Its
**New chats** selector picks where a chat with no sandbox is created; the
default, **Automatic**, lets whichever host polls first take it. Once a group
exists, only its owning host can claim its turns. Runners pull work, so
placement is enforced when a runner claims a job:

- **The switch is neither preemptive nor a drain.** The new host starts claiming
  chats that have no sandbox yet. Jobs running on the old host finish, holding
  their leases, and existing chats stay pinned to the host that holds their
  writable layer. A group moves only from a covering checkpoint, after its host
  is removed, terminates or moves out
  ([restoring a removed host's sandboxes](#restoring-a-removed-hosts-sandboxes)).
- **An unenrolled runner cannot claim.** The control plane derives the host from
  its persisted credential, so a name in a request cannot impersonate a pinned
  machine. `AGENT_ENROLL_TOKEN` is bound to the chosen host name and redeemed
  once; replicas then share the persisted credential.
- **There is no storage failover.** If the selected host stops, new chats wait
  rather than starting elsewhere, and existing chats wait for their own host.
  The admin page warns when the selected host has not polled recently, but the
  signals that count are its heartbeat and its sandbox manager probe.

A second machine needs more than this switch: it needs the two tunnels described
next and everything in
[§0 above](#0-the-sandbox-network-needs-its-host-rules-a-resolver-and-its-relay).
Preflight refuses on that machine at startup, rather than failing jobs at their
first model or MCP call.

### Running runners on another machine

A sandbox's group bridge reaches the public internet only on TCP 80/443, and the
host rules refuse private addresses except the group's fixed MCP relay: an SSH
tunnel that lifecycle code finds on the relay network and attaches to each
group's bridge. A separate control-plane tunnel is visible only to the runner:

```text
runner host                                      service host(s)
┌──────────────────────────────────────┐
│ [sandbox, its own group bridge]      │
│   ├→ mcp-tunnel ─────────────────────┼──ssh──→ MCP-only :8001
│   └→ host rules and NAT ─────────────┼────────→ public IPv4, TCP 80/443 only
│                                      │          (the owner's model provider)
│                                      │
│ [runner, internal network]           │
│   └→ control-plane-tunnel ───────────┼──ssh──→ control API :8000
└──────────────────────────────────────┘
```

Each tunnel is a relay, not a proxy (no CONNECT, no caller-chosen destination),
so nothing a sandbox sends can widen either route. The control-plane tunnel never
joins a group bridge, and its address on the runner's network is a private one
that the host rules refuse from every group. The tunnels use SSH so that each
service keeps listening on loopback; binding them to a shared network would put
scoped MCP tokens and host credentials there in cleartext.

```bash
deploy/host/sandbox_host.sh up       # relays, sandbox manager and the runner
deploy/host/sandbox_host.sh check    # probe relays and runner/Sandbox Manager paths
deploy/host/sandbox_host.sh status
deploy/host/sandbox_host.sh down
```

`deploy/host/docker-compose.sandbox.yml` holds the runner, the sandbox manager,
the two tunnels and two networks: the stack's own network and the route-less
relay network. A host environment that still sets the removed inference tunnel
(`AGENT_GATEWAY_URL`, `AGENT_GATEWAY_HOSTNAME`, `AGENT_TUNNEL_*`) is refused.

| Variable | Notes |
| --- | --- |
| `AGENT_MCP_TUNNEL_*` | The MCP-only relay's destination and SSH material; remote default `127.0.0.1:8001`. Do not point it at the full control API on 8000 |
| `AGENT_CONTROL_PLANE_TUNNEL_*` | The runner-only control API relay; remote default `127.0.0.1:8000`. It never joins a sandbox network |
| `AGENT_*_TUNNEL_SSH_KEY` | Private key, mounted read-only. Each relay has its own key and its own account on the far machine, restricted as [described below](#authorizing-the-tunnels-on-the-far-machines) |
| `AGENT_*_TUNNEL_SSH_KNOWN_HOSTS` | Required host-key file for each relay. Host checking cannot be turned off: an unverified hop would hand scoped tokens to whatever answers there |
| `AGENT_MCP_HOSTNAME` | The name a sandbox resolves on its group bridge, `agent-mcp` by default. A relay named by IP address is refused at startup |
| `AGENT_CONTROL_PLANE_HOSTNAME` | The runner-only control relay's name, `agent-control-plane` by default |

Generate the known-hosts entry, and compare its key fingerprint with a trusted
record for the service host:

```bash
ssh-keyscan -p 22 "<control-plane-host>" > /etc/agent-tunnel/known_hosts
```

#### Authorizing the tunnels on the far machines

Prefer **Admin → Servers → Server setup → Automatic setup**
([compute runbook](operations-compute.md#automatic-setup)). It creates the
accounts below, writes and reads back their sshd policy, and refuses an existing
policy that would widen it. Authorize a tunnel by hand only when that is not
available, and then reproduce exactly what it generates.

Give each relay its own account with no shell on the control-plane machine. Each
account's `authorized_keys` holds one line:

```text
# control-plane machine, MCP account
command="",restrict,port-forwarding,permitopen="127.0.0.1:8001" ssh-ed25519 AAAA... mcp
# control-plane machine, control account
command="",restrict,port-forwarding,permitopen="127.0.0.1:8000",permitlisten="172.24.0.1:18092" ssh-ed25519 AAAA... control
```

`port-forwarding` re-enables remote (`-R`) as well as local forwarding, and
`permitopen` limits only the local destination, so on its own any of these keys
could open listeners on the far machine. The control key's `permitlisten`
therefore names its single reverse listener: the control-plane machine's Docker
bridge gateway and this host's `AGENT_SANDBOX_MANAGER_REVERSE_PORT`. Each account
also gets its `Match User` block from
[`deploy/host/sshd-tunnels.conf.example`](../../deploy/host/sshd-tunnels.conf.example).
The MCP block sets `AllowTcpForwarding local` and `PermitListen none`; the
control block allows its one `PermitListen` with `GatewayPorts clientspecified`.
Install the blocks before any other `Match` block that could apply to these
accounts, run `sshd -t`, and confirm each account with
`sshd -T -C user=<account>,host=localhost,addr=127.0.0.1` before reloading sshd.

Then run `sandbox_host.sh check` from the host checkout. Sandbox-runtime
containers on the relay network must get HTTP 200 from MCP `/healthz`, and the
live runner must pass full sandbox preflight (host table read-back included) and
reach the sandbox manager and the control plane. Requests from the host shell do
not exercise these paths. Once it is up, the machine appears in
**Admin → Servers** and can be selected there.

#### The way back: the control plane reaching this host's sandbox manager

File listings, terminal traffic and `git status` go from the control plane to
the sandbox manager beside the Docker daemon that holds the workspace, at the
address the host reported at enrollment. The integrated local stack shares a
network (`http://sandbox-manager:8092`); the standalone host stack does not, even
on the same machine as the control plane. An unreachable sandbox manager answers
**503**, including on `terminals/suspend` during normal execution, so verify the
route before admitting jobs.

The control plane's machine usually cannot reach this host, so the route rides
the connection this host already opens: a reverse forward on the control-plane
tunnel.

```text
  runner host                                control plane host
┌──────────────────────────┐              ┌──────────────────────────┐
│ sandbox-manager         │◀─── -R ──────│ 172.24.0.1:18092         │
│        :8092             │              │  (docker bridge gateway) │
│                          │              │        ▲                 │
│ control-plane-tunnel ────┼──── ssh ────▶│        │ control-plane   │
└──────────────────────────┘              └────────┴─────────────────┘
```

Set all three of these; an advertise URL with no tunnel behind it gives the same
503:

| Variable | Value |
| --- | --- |
| `AGENT_SANDBOX_MANAGER_REVERSE_BIND` | The **control plane host's Docker bridge gateway**. Not `127.0.0.1`, which its containers cannot see, and not `0.0.0.0`, which publishes the sandbox manager to that machine's whole network behind one shared token. Find it with `docker network inspect <net> --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'` |
| `AGENT_SANDBOX_MANAGER_REVERSE_PORT` | A free port on that host |
| `AGENT_SANDBOX_MANAGER_ADVERTISE_URL` | `http://<bind>:<port>`: what the runner reports, and what the control plane dials |

The control-plane machine's sshd must apply `GatewayPorts clientspecified` to the
control account, as its block in
[`deploy/host/sshd-tunnels.conf.example`](../../deploy/host/sshd-tunnels.conf.example)
does, with that account's `PermitListen` and the key's `permitlisten` naming this
exact address and port. Never set it to `yes`: sshd then binds every remote
forward to the wildcard address, whatever the client asked for, and still
reports success, so each host's sandbox manager is published on all of that
machine's interfaces behind one shared token. With the default, `no`, sshd binds
the forward to loopback, where the control plane's containers cannot reach it. A
listener that sshd refuses outright (a port in use, or one `PermitListen` does
not allow) makes ssh exit under `ExitOnForwardFailure` and the container restart;
the two wrong settings do not. On that machine, confirm with `ss -ltn` that the
port listens on the bridge address and nowhere else.

The bind address alone does not keep the LAN out, because Linux accepts a packet
for any local address on any interface. Also limit the reverse ports in that
machine's firewall to its Docker bridge interface. Find the interface with
`ip -o -4 addr show to <bind>`, then add a rule for each reverse port (or the
range you assign), for example:

```bash
iptables -I INPUT -p tcp --dport 18092 ! -i br-0123456789ab -j DROP
```

Persist the rule the way that machine persists its firewall. Then check from
inside the control-plane container:

```bash
docker exec "<control-plane-container>" \
  python -c "import urllib.request; print(urllib.request.urlopen('http://<bind>:<port>/health', timeout=5).status)"
```

**HTTP 401 proves the route works**: the sandbox manager refuses the
unauthenticated request, and `urllib` reports that as an HTTPError. A DNS error
or a timeout means the route failed.

### Notes on this topology

- **The runner is not the control-plane image.** It builds from
  `Dockerfile.agent-runner`, which adds a Docker client and `git`. A runner
  built from the control-plane image fails preflight on every host.
- **There is no host worktree mapping.** For the first turn, the runner fetches
  the requested commit into a temporary directory, removes all repository
  credentials, normalizes ownership in a tar stream, imports it into
  `/workspace` in the retained container, and deletes the temporary clone.
  Everything after that stays in the container's writable layer.
- **The Docker socket is the host's authority.** The runner and the sandbox
  manager can create, start, stop, inspect and exec in chat containers, so keep
  those services away from users; their Unix UID matters less. The security
  boundary is the retained sandbox (Kata for untrusted multi-tenant work, plain
  Docker only for trusted repositories), not a host bind mount or a per-turn
  container.

### The sandbox network

Each sandbox group (setup, the agent and every Fork sibling) gets one routable
Docker bridge, a `/29` from `AGENT_EGRESS_CHAT_NETWORK_POOL`, and there is no
other network to select
([design](../agents/specs/2026-09-23-agent-public-egress-design.zh-CN.md)). The
bridge's host interface is named with this deployment's prefix: `ca` and five
hex digits derived from `AGENT_SANDBOX_NAMESPACE`, then eight derived from the
group. Docker routes and masquerades the bridge, and this deployment's host
nftables tables decide what it reaches.

A chat's optional `setup_script` runs inside the retained container before the
first Agent CLI turn, on the same network the agent gets afterwards. Its files
persist in the container's writable layer; there is no host-side setup cache.
Package managers reach public registries directly over HTTPS, with no proxy to
configure. Anything that needs another port, such as Git over SSH, is refused,
except an owner's own checked [SSH servers](#ssh-servers) in the chats that
enabled them. The sandbox runs unprivileged, so `apt install` does not work; use
`pip --user`, a virtualenv or `npm ci`.

New chats can select up to eight runtime plugins (**+ → Plugins**, or the create
request's `plugins` field); follow-ups keep them and Fork copies them. Claude
Code and Codex take entries such as
`{"plugin": "review@example", "marketplace": "example/plugins"}`, from a GitHub
repository or a credential-free public HTTPS source (a Git repository for
Codex). pi, OpenCode and Kilo take npm entries such as
`{"plugin": "@example/plugin@1.2.3"}`. Ranges, tags, local paths, install
commands and credentials in source URLs are refused. Plugins install during
member initialization, before the CLI accepts a prompt, under the member's own
identity and network. An error or timeout fails startup with a short
diagnostic, and a successful install is kept across restarts.

#### What it allows and refuses

Allowed:

- public IPv4 on TCP 80 and 443: curl, Python HTTP clients, pip, npm, Git over
  HTTPS;
- the resolver in `AGENT_EGRESS_DNS_SERVER`, on TCP and UDP 53. It is checked
  before the protected ranges, so a resolver on a private address or on this
  host works, on port 53 only;
- the group's own MCP relay, by exact bridge, address and port;
- the model relay, the same way, on a host configured with one
  ([Provided models](#provided-models));
- the GitHub relay's tunnel, the same way, on a host configured with it:
  `github.com` and `api.github.com` resolve to it on every bridge, on port 443
  ([The GitHub relay](#the-github-relay));
- the exact public IPv4 address and port of each SSH server the group's owner
  registered, checked and enabled for one of its chats (the `ssh_targets` set).
  It is checked after the protected ranges, so a protected address stays
  refused ([SSH servers](#ssh-servers)).

Refused:

- every address of this host, the bridge's own gateway included, except the
  resolver when it runs here;
- every IPv4 address that is not ordinary public unicast: `0.0.0.0/8`,
  `10.0.0.0/8`, `100.64.0.0/10`, `127.0.0.0/8`, `169.254.0.0/16` (cloud metadata
  among them), `172.16.0.0/12`, `192.0.0.0/24`, `192.0.2.0/24`,
  `192.88.99.0/24`, `192.168.0.0/16`, `198.18.0.0/15`, `198.51.100.0/24`,
  `203.0.113.0/24`, `224.0.0.0/4` and `240.0.0.0/4`, plus Azure's WireServer
  `168.63.129.16` and whatever `AGENT_EGRESS_PROTECTED_CIDRS` adds, on any port;
- other groups, of this deployment or another;
- every other port and protocol: UDP 443 (QUIC), ping, all IPv6;
- connections started from outside. A reply is accepted only for a connection
  the group opened, and one from a protected address is dropped even then.

The rules judge the address a packet is sent to, so a name that resolves (or is
rebound) to a private address is refused like the address itself. Most refusals
are answered at once with a TCP reset or an ICMP "administratively prohibited",
so a blocked request fails immediately instead of timing out; IPv6 is dropped
silently.

**What it does not promise.** TCP 80/443 is a port rule: nothing inspects the
traffic, so DNS over HTTPS or a tunnel on those ports is not prevented. Nothing
stops a sandbox from sending the repository, its own short-lived tokens or its
owner's provider key to a public host, and the platform's own public addresses
may be reachable.

#### MCP prerequisite and accepted owner-key exposure

The main API does not mount the tool-call router, so the MCP tunnel must point
at port 8001 (see [MCP servers](#mcp-servers)).

The member's CLI holds its owner's provider key for the turn it runs (see
[owner model connections](#owner-model-connections)), and code or instructions
a task meets may cause that key to leave the sandbox. This is accepted: calls
made with it spend the owner's own quota and grant no other user's identity, MCP
tools or platform-management rights. The owner revokes the key at the provider;
deleting the connection here does not revoke it upstream. Accepting this waives
none of the other gates: Kata and network acceptance, member isolation and the
restrictions on long-lived credentials still apply.

#### The host tables

The rules are two nftables tables of this deployment's: an `inet` table for
routed traffic (to the internet, the host, another bridge) and a `bridge` table
for frames between a sandbox and its relays on the same bridge. Both are named
`cloud_agent_ca` plus the first five hex digits of the SHA-256 of
`AGENT_SANDBOX_NAMESPACE`, which on a host stack is the Compose project:

```bash
digits="$(printf '%s' "$COMPOSE_PROJECT" | sha256sum | cut -c1-5)"
table="cloud_agent_ca$digits"
```

The runner and the sandbox manager hold no `NET_ADMIN` and are not in the host's
network namespace. They change host rules through a helper container: their own
image, run in the host's network namespace with only `NET_ADMIN` and a read-only
root filesystem, as the `host_nft` function below does. Each keeps one helper
running and hands it one step at a time. A helper that fails a step is
discarded (`network_guard_helper_discarded`), and for five minutes every step
runs in a fresh helper. Helpers always run under runc, because a Kata VM would
change its own kernel's rules, not the host's.

- **Installed before anything joins a bridge.** The runner's and the sandbox
  manager's preflight install the tables and read them back (every chain, its
  hook and rule count, the protected set, the registered pool). Any difference
  stops the runner claiming and the sandbox manager starting. Group network
  work repeats the check and trusts a pass for 60 seconds, and both services
  renew their pass in the background about every 45 seconds, so starting a group
  rarely waits for it. A turn process that reads a pass already 60 seconds old
  logs `network_guard_verification_aged_out`, checks again itself, and runs only
  if that passes.
- **Scoped to this deployment.** The tables recognize traffic by the bridge
  interface prefix and never touch another deployment's rules, so several
  deployments can share a host.
- **Relays by exact endpoint.** A group's relay bridge, address and port are
  written to a `relays` set in both tables when the relay attaches, and removed
  with the bridge. Every activation and workspace command re-syncs its bridge's
  exceptions.
- **Watched while groups run.** While any group runs, the sandbox manager reads
  the tables back every five seconds, logs `network_guard_rules_changed` when
  they differ, reinstalls what is missing, and restores each running group's
  relay and SSH exceptions (`chat_group_exceptions_not_restored` when it
  cannot). Two failed checks in a row log `shared_chat_network_unguarded` and
  stop every running group, keeping workspaces and native sessions. Waking a
  group reinstalls and verifies the rules first.
- **Kept by the kernel.** A runner exiting leaves the tables in place; a reboot
  clears them, and no group starts until they are installed again.

To read them, use the same kind of helper:

```bash
image="$(docker inspect --format '{{.Image}}' "${COMPOSE_PROJECT}-runner-1")"
host_nft() {
  docker run --rm --network host --runtime runc --cap-drop ALL \
    --cap-add NET_ADMIN --read-only --entrypoint nft "$image" "$@"
}
host_nft list table inet "$table"      # chains; each refusal has a counter
host_nft list table bridge "$table"
host_nft list set inet "$table" relays # relay endpoints, per live bridge
host_nft list tables                   # every deployment's tables here
```

Keep `--runtime runc`: on a host whose default runtime is Kata, the listing would
otherwise come from a VM. The counters are totals across all of this
deployment's groups; no per-connection log is kept.

#### Overlapping pools

After installing, each deployment compares its pool with those of the
`cloud_agent_*` tables created before its own. Of two whose pools overlap, the
later one refuses with `this deployment's pool … overlaps …, registered earlier by …`,
so a neighbour started with a copied pool cannot take a working deployment down.
Give each deployment its own `AGENT_EGRESS_CHAT_NETWORK_POOL`.

The pool must also stay clear of every network the host routes. Docker checks
only other Docker networks, so a group bridge inside a LAN, VPN or
private-service range would draw the host's traffic for those addresses into a
sandbox. The guard reads every IPv4 route (`ip -4 route show table all`) and
refuses with `this deployment's pool … overlaps the host route … on …`. The
default route, catch-alls wider than a `/8` (such as a full-tunnel VPN's two
`/1` halves) and this deployment's own bridges do not count. A network reached
only through the default gateway has no route to find, so choose the pool with
the site's addressing in mind.

A retired deployment's tables stay, with its pool registered, until the host
reboots. Once it is gone for good, delete its two tables, and no one else's,
with the helper above: `host_nft delete table inet cloud_agent_ca<its digits>`,
then the same for `bridge`. Never delete or flush a table that a running
deployment uses: Docker routes and masquerades every group bridge, and the table
is what limits it. Anything that flushes the whole ruleset removes these tables
too. They come back at the next check, but do not run such a reload on a host
with active groups.

#### Choosing the resolver

`AGENT_EGRESS_DNS_SERVER` is one IPv4 address and the only DNS a sandbox can use.
A public resolver works anywhere. A site resolver on a private address or on
this host works too, and becomes the one private address a sandbox can reach, on
port 53 only. It cannot be loopback (a sandbox cannot reach the host's
`127.0.0.53`), unspecified, or inside the pool. Under runc, a container gets it
as `--dns` when it is created, and Docker's resolver at `127.0.0.11` forwards to
it; in a Kata guest the helper writes it into `/etc/resolv.conf`
([Guest networking](#guest-networking)). The in-guest check at every cold start
confirms that the guest uses this resolver and that it answers a query for the
root's name servers within three seconds.

#### Protecting more addresses

`AGENT_EGRESS_PROTECTED_CIDRS` adds comma-separated IPv4 blocks to refuse, such
as an institution's services on public addresses; it can only add protection.
The resolver stays reachable on port 53 even inside an added block. A change
takes effect when the runner and the sandbox manager are redeployed with it.

#### When something is refused

**Settings.** `sandbox_host.sh prepare` reads them with the candidate image's own
parser before any runner is drained. A refusal prints
`the sandbox network settings were refused (see the error above)` under one of:

- `AGENT_EGRESS_CHAT_NETWORK_POOL must name this deployment's group address pool`
  or `… must be RFC1918 space holding at least one /29`: unset, outside
  `10.0.0.0/8`, `172.16.0.0/12` and `192.168.0.0/16`, or smaller than a `/29`;
- `AGENT_EGRESS_DNS_SERVER='…' must be one IPv4 resolver address` or
  `… must be reachable from a group bridge`: unset, not IPv4, loopback,
  unspecified, or inside the pool;
- `AGENT_SANDBOX_MCP_URL='…' names a relay by IP; use its network alias`: each
  group bridge gives the relay a fresh address, so only its name carries over.

**Preflight.** Until it passes, the runner claims nothing and the sandbox manager
does not start:

- `the relay network '…' does not exist`: the stack is not up, or
  `AGENT_RELAY_NETWORK` names something else;
- `` the relay network '…' is not `internal` ``: let Compose recreate it as
  declared;
- `nft failed: …` or `network guard helper could not run: …`: the host kernel
  lacks nftables support, or Docker refused the helper container;
- `the network guard runs its helper from this container's own image`: the
  runner finds its image by inspecting its own container, named by `HOSTNAME`,
  so a custom `hostname:` breaks it;
- `… is missing chain …`, `… holds N rules, expected M`, `… is not hooked on …`,
  `… protects …, not the configured set` or `… registers pool …, not …`, each
  naming the table: the tables changed after they were installed, or a runner
  and a sandbox manager with different settings are overwriting each other.
  Redeploy both from one `.env`;
- `this deployment's pool … overlaps …` or `… overlaps the host route … on …`:
  see [Overlapping pools](#overlapping-pools);
- `the sandbox network needs Docker 28 or newer, client and daemon`: see §0.

**Group lifecycle.** These fail the turn or operation that needed the group's
network, and keep the workspace:

- `relay alias '…' has no running container on relay network '…'` (or
  `multiple running containers`): a tunnel is down or restarting, or two stacks
  share one relay network;
- `AGENT_EGRESS_CHAT_NETWORK_POOL … has no free /29 subnet`: every `/29` in the
  pool is taken, by this deployment's groups or by other Docker networks in the
  same range;
- `group network check failed inside the sandbox: …`: the in-guest check after a
  cold start found one of the problems below, and the sandbox is stopped, not
  deleted. Reaching the public internet is not part of the check.
  - `no default route`, or `resolver is […], expected […]`;
  - `resolver … did not answer: …` or `… did not answer the query usefully`:
    the resolver is down, refuses recursion, or (under runc) the container was
    created with a resolver the rules no longer allow;
  - `relay host:port unreachable: …`;
  - `the host at …:9/udp was refused by ECONNREFUSED, not by the network
    rules`, `the metadata address 169.254.169.254:80 answered; the network
    rules are not in this guest's path` (or the same for `the platform
    address 168.63.129.16:80`), or `… was not refused within 2s`: the guest's
    traffic is not passing this deployment's tables;
- `fixed relay joined the chat network without its gateway priority`: the
  Docker daemon ignored `--gw-priority` and needs Docker 28 or newer;
- `Kata guest network sync failed: …`: the guest-root helper could not write
  `/etc/hosts` or `/etc/resolv.conf`;
- `retained chat is attached to network '…', which is not its group bridge`,
  ending `needs the one-time cleanup`: a chat from the previous network, which
  keeps the sandbox manager from starting; see the next section;
- `a running retained chat is not on exactly its group bridge`: a running guest
  is never rewired; stop it first.

In the sandbox manager's log, `chat_legacy_network_found` names a bridge of this
deployment from the previous per-phase topology; it is reported, never adopted or
repaired. `chat_group_relay_exceptions_left` means a removed bridge's relay
entries could not be withdrawn from the tables. They match nothing once the
bridge is gone, and if the helper still cannot run, the next activation fails.

### One-time cleanup when upgrading to the one network

Only a host that ran the previous network (per-phase bridges and the Squid proxy)
needs this. The new version adopts nothing the old one left. It refuses a
retained chat still attached to an old network, and because the sandbox manager
stops every retained chat of its deployment when it starts, one such container
keeps it from starting at all. These steps discard the old retained chats; the
switch was meant for development and test hosts with no users to carry over.
**Export any workspace you need before step 4.**

Every listing below filters by this deployment's own labels: the Compose project
for the stack's own containers, networks and volumes, and
`org.freeinference.sandbox-namespace` (the same Compose project, on a host
stack) for chat containers and group bridges. Read each listing before removing
anything, and leave anything that belongs to another project or namespace, or
that you cannot attribute.

1. Drain the host (**Admin → Servers**), wait until it runs nothing, and stop
   this stack. If the checkout already holds the new release, first add the two
   settings from step 7 to `.env`: the new compose file does not render without
   them.

   ```bash
   export COMPOSE_PROJECT="<this stack's Compose project>"
   deploy/host/sandbox_host.sh down
   ```

2. List this deployment's chat containers. They all predate the upgrade, and
   they all go: the new version refuses any still on an old network, and none
   was created with `AGENT_EGRESS_DNS_SERVER`, which under runc is fixed when a
   container is created.

   ```bash
   docker ps -a \
     --filter label=org.freeinference.chat-sandbox=true \
     --filter "label=org.freeinference.sandbox-namespace=$COMPOSE_PROJECT" \
     --format '{{.ID}}  {{.Names}}  {{.Status}}  {{.Networks}}'
   ```

3. Export what you want to keep. A workspace exists only in its container's
   writable layer, and `docker cp` reads a stopped container:

   ```bash
   docker cp "<container>:/workspace" "./<container>-workspace"
   ```

4. Remove the containers from step 2, by ID. This is the one sanctioned
   `docker rm` of chat containers (see
   [Retained Docker storage on a host](#retained-docker-storage-on-a-host)).
   Their chats' context reads `LOST`, and new tasks get new containers.

   ```bash
   docker rm -f "<id>" …
   ```

5. List this deployment's chat networks, and remove the old ones. Each line shows
   the name, whether the network is internal, and its bridge interface name. An
   old `freeinference-chat-net-*` network is internal (`true`) with no interface
   name; a new group bridge is not internal and has a `ca…` name. Before the new
   version has run, every one listed is old.

   ```bash
   docker network ls -q \
     --filter label=org.freeinference.chat-sandbox-network=true \
     --filter "label=org.freeinference.sandbox-namespace=$COMPOSE_PROJECT" |
     xargs -r docker network inspect --format \
       '{{.Name}} {{.Internal}} {{index .Options "com.docker.network.bridge.name"}}'
   docker network rm "<name>" …
   ```

6. Remove the proxy and the old Compose networks and volume. List what Compose
   created for this project:

   ```bash
   docker ps -a --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
     --format '{{.ID}}  {{.Names}}  {{.Label "com.docker.compose.service"}}'
   docker network ls --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
     --format '{{.Name}}  {{.Label "com.docker.compose.network"}}'
   docker volume ls --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
     --format '{{.Name}}  {{.Label "com.docker.compose.volume"}}'
   ```

   With `docker rm -f`, `docker network rm` and `docker volume rm`, remove the
   containers of the services `agent-egress-proxy` and
   `agent-egress-proxy-config`, the networks whose Compose key is
   `agent-egress`, `agent-egress-trusted` or `agent-egress-uplink`, and the
   `agent-egress-proxy-config` volume. Nothing else in these listings is part of
   the cleanup, and a `down` from the previous release may already have removed
   some of them.

7. In the host's `.env`, set `AGENT_EGRESS_CHAT_NETWORK_POOL`, which has no
   default, and `AGENT_EGRESS_DNS_SERVER`. The old default pool was
   `10.240.0.0/16`; if two deployments on this machine kept it, give one of them
   another pool, or the later one refuses to start. Delete the retired settings
   listed under [Host configuration reference](#host-configuration-reference).

8. Undrain the host (**Admin → Servers**, or
   `POST /admin/agent/runner-hosts/<host>/undrain`); with its runners stopped it
   claims nothing yet, and a heartbeat never clears a drain. Then deploy the new
   release as usual, with a deployer at `shared-live-exact-sha-v4`. Confirm that
   `host_nft list tables` (see [The host tables](#the-host-tables)) shows this
   deployment's `inet` and `bridge` tables, and run a new task whose setup
   installs a package.

## Runtime verification

After changing runtime integration code, run the adapter contract tests. They
replay recorded and synthetic events against the normalized mapping, with no
live model or deployment credential:

```bash
uv run pytest tests/unit/test_agent_runtimes.py
```

After changing the MCP path (catalogue, per-user registry, relay, filters, API or
runtime adapter), run at least:

```bash
uv run pytest tests/unit/test_mcp_connection_config.py \
  tests/unit/test_mcp_protocol_and_authorization.py \
  tests/servers/test_mcp_relay.py tests/servers/test_agent_jobs_api.py \
  tests/unit/test_agent_runtimes.py
```

These tests do not run the installed agent binaries. An installed-CLI smoke test
and a live-model matrix belong to the deployment that operates those binaries,
models and credentials, and their configuration belongs in that deployment's
overlay, not in this repository.

## SSH servers

An owner registers their own Linux servers under **Settings → SSH connections**
(`/settings/ssh-connections`), checks each one, and turns servers on per chat from
**+ → SSH servers**. The agent then runs `ssh`, `scp` and `rsync` against them
from the sandbox; the platform never runs a command on a server. The
[SSH server design](../agents/specs/2026-10-15-user-ssh-servers-design.zh-CN.md)
and the owner SSH servers entry in [AGENTS.md](../../AGENTS.md) §7 describe the
accepted risks.

- **Key.** Each server's Ed25519 private key is encrypted with
  `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` under its own
  `hybridinference/ssh-server-key/v1` domain. Without that key the settings
  section says so, and nothing can be added.
- **Hosts.** Runners and sandbox managers advertise `ssh-access-v1`, and the
  sandbox image ships `openssh-client`. A runner without the capability hands
  back, without spending an attempt, a claim for a group whose chats ever
  selected a server, so roll hosts out before owners enable servers; a group
  already pinned to an old host waits.
- **What a turn does.** Before setup and the agent start, the runner sends the
  sandbox manager the group's SSH state from the claim. The manager records the
  newest state in the root-owned `/run/agent-members/.ssh-access-state`
  (revisions and digests, no keys) and sets the bridge's `ssh_targets` from it.
  It removes the SSH directories of members the state allows nothing, and writes
  the starting member's servers into that member's private tmpfs (key,
  `known_hosts` and a `Host` block per server), with one `Include` line at the
  top of its `~/.ssh/config`. A group none of whose chats ever selected a server
  gets no state at all.
- **Taking access away is immediate; granting it waits for the next turn.**
  Turning a server on or off, editing, checking, rekeying or deleting it pushes
  the new state to every running group it reaches. A push only removes:
  deleting an element cuts a live SSH session on its next packet, and a
  member's directory for a server it may no longer use, or whose key changed, is
  deleted. A newly enabled server or a new key arrives with the chat's next
  turn, whose runner masks the key. If the newest state fails to apply, the
  manager clears the bridge's SSH elements and the call fails, so the group
  stays closed until a state applies whole. A host the control plane cannot
  reach gets the state with its next turn's claim.
- **Checks dial out from the control plane.** A check resolves the address and
  refuses it if any answer is private, reserved or protected, reads the host
  keys with `ssh-keyscan`, and, after the owner confirms a fingerprint, logs in
  once with the platform key and runs `true`. The control plane needs outbound
  TCP to owners' SSH ports; set `AGENT_EGRESS_PROTECTED_CIDRS` on it to the
  hosts' value so that checks refuse the same blocks. Each owner may run 20
  checks an hour, and a check reads at most 64 KB of each output stream.
- **Owner firewalls.** A server that limits who may connect must allow the
  control plane (for checks) and every host its chats may run on.

## Owner model connections

An owner adds their own inference endpoint under **Settings → Models**
(`/settings/models`): a name, a Base URL, an API key and up to sixteen model ids.
**Verify and save** verifies every model before anything is stored. The models
then appear in the composer for the agents that can use them, beside any
[provided models](#provided-models), and take precedence over a same-named
provided model for that owner. Turns run on the owner's key and bill. The
[model connection design](../agents/specs/2026-10-27-user-model-connections.zh-CN.md)
and the owner model connections entry in [AGENTS.md](../../AGENTS.md) §7 describe
the protocol matrix and the accepted risks.

- **Turning it off.** `AGENT_MODEL_CONNECTIONS_ENABLED` is on unless set to `0`,
  `false` or `no`. Off, it stops new connections and new turns or messages on
  owners' connections and provided models alike; admitted turns still run. Set
  it to `false` during a rollout to hold new turns until every host and replica
  runs the new release. It does not stop running work.
- **Rolling back.** Do not run a release from before independent accounts
  against this database. Downgrading `0106` refuses while any connection, any
  turn or waiting message naming one, or any member resident on one exists.
- **Key.** Keys are encrypted with `AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` under
  their own `hybridinference/model-connection-key/v1` domain; without that key
  nothing can be saved. No owner route returns a key, and only the host's
  authorized claim and residency responses carry it.
- **Verification.** Owners do not declare their provider's APIs. Each model
  gets a streaming tool call and its result on OpenAI Chat Completions, OpenAI
  Responses and Anthropic Messages, side by side, and each API's outcome is
  stored: `supported`, `unsupported` or `failed` (undetermined). OpenCode, Kilo
  and pi need Chat Completions, Codex needs Responses, and Claude Code needs
  Anthropic Messages. Codex and Claude Code can also run on a model whose Chat
  Completions is supported, through a translator (below). Nothing falls back to
  another connection, model or API: an unservable pairing is refused at
  submission and at every claim and residency.
- **Saving.** Creating a connection, or an edit that changes its key or Base URL
  or adds models, verifies before writing anything, with the checks reserved
  from the owner's hourly budget first, all or none. The save commits only if
  every model has an agent that can use it; otherwise it answers
  `422 model_connection_verification_failed` with each model's result and writes
  nothing. An edit that lost a race answers `409 model_connection_changed`.
  Verification has a 240-second deadline
  (`504 model_connection_verification_timeout`), which every proxy in front of
  the API must allow. Renaming, removing models, enabling and disabling make no
  provider call.
- **Network rules.** Verification runs only on the owner's explicit save, with
  at most six provider requests per model and one of the owner's 20 hourly
  checks. It dials only public IPv4 HTTPS on port 443, through the MCP
  connection policy and the sandbox's protected ranges (including
  `AGENT_EGRESS_PROTECTED_CIDRS`), pinned to the checked addresses, with no
  redirects or proxy, and never echoes or logs an upstream body or key.
- **Reasoning levels** come only from the provider's model listings, read with
  each verification and on **Refresh reasoning levels**: `GET <Base URL>/models`
  (`reasoning.supported_efforts`, or DeepSeek's `effort.supported_levels`) and
  the Anthropic `GET /v1/models?limit=1000` (`capabilities.effort`). An agent is
  offered the declared levels its CLI can send, plus Auto, which sends nothing
  extra. A level the model does not offer is refused with
  `effort_level_unsupported`.
- **Where requests go.** The OpenAI CLIs append `/chat/completions` or
  `/responses` to the Base URL as written. Claude Code appends `/v1/messages` to
  `ANTHROPIC_BASE_URL`, so one trailing `/v1` is dropped first: `https://host`,
  `https://host/v1` and `https://host/anthropic/v1` all reach `…/v1/messages`,
  with `x-api-key` and `anthropic-version: 2023-06-01`.
- **Model ID suggestions.** The forms can list the provider's models through
  `POST /v1/agent/model-connections/discover-models`, with the entered key or a
  saved one (only at the Base URL it was saved with; otherwise
  `422 model_connection_key_required`). It spends one hourly check and writes
  nothing.
- **Hosts.** Runners advertise `model-connections-v1`, and
  `model-protocol-translation-v1` when they can run a translated member; a
  runner without what a turn needs hands it back without spending an attempt.
- **What a turn does.** The claim carries the Base URL, protocol, credential
  revision and key (masked by the runner), and the residency binds the member to
  that revision. The CLI gets the key in its environment and activation tmpfs,
  never in argv. A turn after the key, Base URL or verified path changed starts
  a new process.
- **Translated turns.** For Codex or Claude Code on a Chat-Completions-only
  model, the member runs `python3 -I model-protocol-translator.pyz <CLI argv>`
  (installed digest-checked at `/usr/local/libexec/`), which forks a loopback
  translator as the same member UID and execs the CLI. The translator forwards
  only to the connection's `/chat/completions`, answers only the member's own
  token, refuses what Chat Completions cannot carry (hosted tools such as web
  search, files, `previous_response_id`) and dies with the CLI. Codex starts
  without web search and Claude Code without WebSearch on this path.
- **Changes take effect at once.** A verified new key or Base URL, disabling or
  deleting closes every process on the old state, stopping its turn; renaming or
  removing models closes nothing.
- **Scan copies.** Binding a key to a residency still records an encrypted copy
  against the sandbox group, kept after the connection is deleted, though
  nothing reads it. Deleting a connection does not revoke the key at the
  provider.

## Provided models

A deployment can offer models of its own. An administrator saves a provider's
Base URL and the deployment's key under **Admin → Settings → Models**; the
connection offers every model the provider lists for that key, and every
admitted owner finds them in the composer with nothing to set up. The
[design](../agents/specs/2026-11-12-platform-model-relay.zh-CN.md) and the
provided models entry in [AGENTS.md](../../AGENTS.md) §7 describe the
boundaries.

**Where the key is.** Only in the control plane, encrypted under the settings
root key. A member's CLI on a provided model points at the host's `model-tunnel`
and holds a residency placeholder (`amd.`) as its API key. The tunnel ends at the
control plane's model relay (`create_model_relay_app`, port 8002), which
resolves the placeholder to the running turn, checks that the turn runs on that
connection, forwards only the inference and model-listing paths an agent CLI
calls, for listed models, to the connection's Base URL with the deployment's
key, and records what the answer says it used. Stop, a finished turn, a rotated
residency or a removed host takes effect on the next request.

**Models and prices.** The save reads the provider's listing (`GET …/models`) and
offers what it lists, so limit the key at the provider to the models you want to
provide. An unreadable or empty listing, or one with more than 16 models, is
refused. Every model is verified like an owner's, and one that no agent can use
is offered to nobody. **Read models again** picks up a changed listing. Prices
come from the listing's `pricing` object (`prompt`, `completion`, and optionally
`input_cache_reads` and `input_cache_writes`, in US dollars per million tokens);
a model without one is recorded by tokens alone. A model ID can come from at most
one provided connection, and an owner's own enabled connection offering the same
model takes precedence for that owner.

**What a request records.** When an answer ends, the relay adds its reported
tokens, and their cost at the listed prices (rounded half up to a millionth of a
dollar per request, as the inference gateway computes it), to the owner's use for
the day. An answer with no usage, or one cut short, is recorded by estimate (one
token per four bytes of text; 85, 200 or 1,000 tokens per image, audio clip or
video), and the usage table counts those separately. A request the provider did
not serve records nothing.

**Nothing limits use here.** There is no budget, quota or cap, per owner or per
model. The provider's own budget and limits on the key bound spending, so set
them there, knowing that one owner's heavy use spends them for everyone. The
recorded cost is arithmetic on listed prices, not a bill.

**Setting it up.** The control plane always runs the `model-relay` service on
`127.0.0.1:8002` (`AGENT_MODEL_RELAY_BIND` / `AGENT_MODEL_RELAY_PORT`; keep it off
the public edge), refusing every request until a provided connection is enabled.
Every host has a `model-tunnel` service, idle until **Add server** gives it a
destination: its own SSH key and account on the control-plane machine, allowed
only that forward, plus `AGENT_SANDBOX_MODEL_RELAY_URL` and
`AGENT_MODEL_TUNNEL_*` in the host's configuration. Such a host advertises
`model-relay-v1`; only those hosts get turns on provided models, and the admin
page warns while there are none.

1. Make sure at least one host advertises the model relay. Give a server set up
   before the model tunnel existed a new setup under **Admin → Servers**. A host
   that **Add server** did not set up needs a `model-tunnel` key of its own
   (`AGENT_MODEL_TUNNEL_*`) on its own account, allowed that one forward, and
   `AGENT_SANDBOX_MODEL_RELAY_URL=http://agent-models:8000`:

   ```text
   command="",restrict,port-forwarding,permitopen="127.0.0.1:8002" ssh-ed25519 AAAA... models
   ```

2. In **Admin → Settings → Models**, add the connection (a name, the Base URL and
   the key), let the save verify it, then enable it.

**Operating it:**

- **A new key closes nothing**, because no process holds it. A new Base URL,
  disabling and deleting close every member process on the connection.
- **The relay is on the path.** Requests and answers pass through the control
  plane, which stores and logs none of their content: one log line per request
  (job, account id, model, status, duration) and `agent_model_relay_charged` for
  its recorded use. A `model-relay` restart, as in a control-plane release, cuts
  answers in flight; CLIs retry, and cut answers are recorded by estimate.
- **Use that could not be written** is retried after 1, 2, 5, 10, 30, 60 and 120
  seconds, then every 5 minutes (`agent_model_relay_charge_retry`,
  `agent_model_relay_charged_late`). A stopping relay logs
  `agent_model_relay_charge_lost` for records it could not write, and
  `agent_model_relay_charge_unknown` for one that may have landed.
- **A refused key.** If the provider answers `401`, the relay answers `502`, logs
  `agent_model_relay_key_refused`, and the admin page shows since when, until a
  request succeeds or the key is replaced.
- **Nothing falls back** between a provided connection and an owner's own.
  `AGENT_MODEL_CONNECTIONS_ENABLED` switches both off.
- **Rolling back.** The previous release offers a connection saved by this one to
  nobody until an administrator sets a daily budget there. Downgrading `0120`
  drops prices and recorded costs, and downgrading `0118` refuses while a
  provided connection or a turn naming one exists.
- **Claude Code** decides some features from the host name it is pointed at, so
  a provided connection to its vendor's own API behaves like any third-party
  endpoint.

## Reusable cloud environments

Environments are always available: Settings, task selection and the Environment
tab always show them, and `/config` always returns environment limits. Saved
values for the old `repository_environments_enabled` setting
(`AGENT_REPOSITORY_ENVIRONMENTS_ENABLED`) are ignored. A task without an
environment keeps the base environment and runs no preparation.

Environment variables and Secrets are encrypted with
`AGENT_SOURCE_CONTROL_ENCRYPTION_KEY` under a separate
`hybridinference/environment-variable/v1` domain. Back up this key with the
credential store; changing it without re-encryption makes old bindings
unreadable. Owner read and history APIs never return Secrets.

**Rollout and rollback.** Upgrade every control-plane replica before any new
environment-bound workspace is created: old and new admission, Fork and terminal
handlers running side by side may silently lose binding information. After the
first binding, do not roll back to binaries that cannot read it. To roll back,
close those entry points, drain environment tests and preparation, and keep
bound workspaces on compatible hosts or fenced off. Roll out the runner and the
sandbox manager together: a claiming replica must attest `environment-v1`, and
its sandbox manager must advertise it before receiving configuration. An older
host hands back jobs that need configuration without spending an attempt, and
never runs them without their variables. A task whose binding is empty still
pins its original selection, but needs neither `environment-v1` nor preparation.

**Environment tests** obey the deployment pin, compute admission, replica
attestation and host slot limits. Each reserves one local slot on a background
worker, so other slots keep claiming coding tasks, and gets read-only source
credentials with no inference, MCP or repository write grants. Cancelling fences
the result at once, but the slot stays leased until cleanup is confirmed or the
lease expires; the reaper expires leases and 30-minute executions. A host that
returns with expired tests removes their labelled containers before it
acknowledges cleanup, and claims nothing new until then. Do not delete unrelated
groups, or bypass lease and container identity checks, to free capacity.

**Host state.** Keep these directories, in the host credential directory, across
sandbox manager restarts:

- `environment-preparation` holds preparation receipts: binding, container and
  boot identities and masked steps, never values. Installation succeeds once per
  fixed workspace, and startup runs once per boot. A failed step stays visible
  in the Environment tab and can be retried against the original binding.
- `environment-actions` holds manual-action receipts. Each reserves its run
  before launching the command and keeps the masked result after the terminal is
  gone. Deleting the directory removes the host's replay fence for requests
  whose acknowledgement was lost. An interrupted launch without a recoverable
  result is reported as `unknown` and never replayed automatically; check its
  external effects before starting a new run with a different idempotency key.

The output of tests, preparation and actions is stored masked and bounded, with
each NUL and unpaired surrogate replaced by U+FFFD, because Postgres stores
neither. Archived, unreferenced environment versions are collected after 30
days; existing workspace and test bindings keep their encrypted values for
redaction.

Setup, startup and actions share executable package data in
`/workspace/.cloud-agent-tools`, excluded through Git's local exclude file, and
member credential homes stay private. A runtime refresh applies from the next
turn, and running services keep their old values. Startup recovery allows one
further human turn; it is never a permanent success.

Owner-provided credentials can be read and sent outside by user code over the
normal network. Masking known values does not cover encoded secrets, raw owner
terminals or files, or Git pushes, and nothing on the platform scans a push.
Platform-held long-lived credentials, reserved variable names and the fixed
network stay protected.

### Team environments

A team's owner and admins can create environments for the team: in
**Settings → Environments**, **New environment** or **Copy** offers the teams they
own or administer. Every active member sees them there and in the new-task
picker, marked with the team's name, and can view, test, copy and choose them.
Only the owner and admins can edit, restore, remove, rebuild or delete snapshots
(anyone else gets 403), and creating, editing and removing a team environment is
recorded in the team's audit log. See
[Teams](../agents/specs/2026-10-15-team-shared-workspace-design.zh-CN.md) §6.

- **Each member's own container.** A team environment is shared configuration,
  not a shared workspace. A member's task runs in that member's own container,
  with the team's variables and scripts, the member's own model and MCP
  connections, and the member's personal global variables at run time only
  (install-only ones are left out, and the team's definition of a name wins).
  The installation is therefore the same for every member, which lets the team
  share snapshots.
- **Secrets are shared.** Every member who can run a task with a team
  environment can read its Secrets from inside that task, as the dialog says.
  Treat them as known to the whole team, and rotate one after a member who
  should not keep it leaves.
- **Leaving.** A binding records the team and the member's access generation.
  When a member leaves or is removed, or the team is deleted, each of their
  conversations on a team environment fails its next turn at claim with
  `team_environment_revoked` (403, logged as
  `agent_job_team_environment_revoked`); a running turn finishes. Terminals,
  actions, Fork, preparation and runtime refresh on such a conversation return
  409. Rejoining does not revive old conversations, and a repository default
  naming a team environment the member can no longer use is cleared at their
  next task.
- **Removal.** Removing a team environment clears every member's default that
  names it; conversations already bound keep their fixed configuration.
- **Rollback.** Downgrading below `0082_team_environments` is refused while any
  team environment exists, archived ones included.
