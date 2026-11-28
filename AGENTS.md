# AGENTS.md

The onboarding guide for anyone working in this repository, human or AI coding
agent. Read it first.

## 1. What this is

Cloud Agent runs coding agents (Claude Code, Codex, pi, OpenCode, Kilo) in
sandboxed containers against a user's repository. When the owner asks, the
agent pushes a branch and opens a pull request itself, as that owner, through
the platform's GitHub relay.

It began inside the HybridInference LLM gateway
(`HarvardMadSys/hybridInference`) and is now a separate service with its own
accounts and database. [§5](#5-permanent-rules) keeps it that way.

## 2. Repo map

```text
apps/
├── backend/
│   ├── cloud_agent/           control plane (FastAPI): API, accounts,
│   │                          scheduler, MCP, GitHub and model relays, storage
│   ├── cloud_agent_host/      host agent: runner, sandbox, runtimes, sandbox manager
│   ├── cloud_agent_shared/    vocabulary both images ship: capability strings,
│   │   │                      protocol versions, pins, sandbox network, shared errors,
│   │   │                      and the operation-profiling collector both sides run
│   │   └── git/               workspace branch names and the source-archive builders
│   ├── migrations/            Alembic migrations (agent-owned database only)
│   └── openapi.json           the committed snapshot of this service's OpenAPI
└── frontend/                  Next.js user UI + admin dashboard
deploy/{control-plane,host,sandbox,local,release}/
tests/{unit,servers,integration,external}/  # plus fixtures/ and support/
docs/{developer,agents}/       maintained guides and design records
```

[Code structure](docs/developer/code-structure.md) maps every module and the
direction of dependencies. Three boundaries hold throughout: neither
implementation package imports the other (shared facts live in
`cloud_agent_shared`), lifecycle wiring must not import HTTP routers, and
callers outside storage must not import repository implementations.

The schema starts at one baseline revision,
`apps/backend/migrations/versions/0001_schema_baseline.py`, and every later
revision is additive. The baseline replaced the first chain of revisions before
the service took real traffic: that chain carried the gateway's schema and
compatibility code for writers that no longer existed, and enforced almost none
of the store's invariants. The baseline's docstring records the design;
[Preserving an old-chain database](docs/developer/operations.md#preserving-an-old-chain-database)
covers a database on the old chain.

The code behind §6 and §7 (paths under `apps/backend/`):

| Module | What it owns |
|---|---|
| `cloud_agent/accounts/` (`agent_access.py`), `application/owner_termination.py` | The account system: sign-in, sessions, the waiting list, admission (`agent.use`, `agent.admin`), and stopping a revoked account's work ([guide](docs/developer/local-accounts.md)) |
| `cloud_agent/deployment/identity_namespace.py` | Binds an empty database to the account system once, and refuses a Gateway-era one |
| `cloud_agent/mcp/`, `api/mcp_connections.py`, `api/mcp_relay.py` | Per-user MCP connections, hosting, authorization and the MCP relay |
| `cloud_agent/source_control/`, `api/github_relay.py` | The GitHub relay, its policy, and the GitHub and GitLab credentials behind it |
| `cloud_agent/api/model_relay.py`, `api/admin_operator_models.py`, `domain/operator_models.py` | The model relay and the models a deployment provides |
| `cloud_agent/usage/` | What agents reported about their model calls, for the Usage pages; no cost or quota ([usage](docs/developer/operations.md#usage)) |
| `cloud_agent_shared/egress.py`, `cloud_agent_host/network/network_guard.py` | The sandbox network's host rules, and the helper that installs and verifies them |

## 3. Quality gates

```bash
make setup-dev   # uv venv + uv sync
make check       # format + lint + type-check + test — must be green before every PR
```

- `make test` runs everything except the `external` and `dbtest` markers.
- A change to storage, SQL, a migration or a database test also needs
  `make test-db-local` (Docker). It gives the checkout fresh databases on this
  machine's shared test PostgreSQL and fails if any database test was skipped;
  `ARGS="<paths> -x"` narrows it. `make test-db` runs the tier against a
  PostgreSQL you provide ([ENVIRONMENT.md](ENVIRONMENT.md)). Never run
  `make test-db-local-down`: it discards every worktree's databases and fails
  their runs.
- A frontend change needs Node.js 22 and `make ci-frontend` (`npm ci`, then
  lint, formatting, types, tests and build guards). `make setup-dev` installs
  backend dependencies only; if `apps/frontend/node_modules` is missing, run
  `npm ci` there before you report a frontend check as blocked.
- A change to a page under `docs/developer/`, or to a root guide the site
  includes (`ENVIRONMENT.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `deploy/local/README.md`, `deploy/release/README.md`), needs its Chinese
  translation: run `make docs-translate`, translate what it adds or marks
  fuzzy, then run `make docs-verify`
  ([Translating the docs](docs/developer/translations.md)).

Complexity and size limits only ratchet down (#771):

| Code | Limit | Set in |
|---|---|---|
| Backend functions (`apps/backend`, migration revisions excluded) | McCabe 15, 12 branches, 50 statements | Ruff, `pyproject.toml` |
| Backend modules (every `.py` under `apps/backend`) | 1,500 lines, no exceptions | `tests/unit/test_file_size_budget.py` |
| Python test modules (`tests/`) | 2,000 lines | the same test's `TEST_BASELINE` |
| Frontend functions and files | complexity 15, 800 lines | `apps/frontend/eslint-ratchet.json` |
| Frontend tests (`*.test.*`, `*.spec.*`, `e2e/`) | 1,200 lines, no complexity limit | `apps/frontend/eslint-test-ratchet.json` |

- A backend function over a limit carries a `noqa` naming #771. Never add one
  to new code; `make check` deletes it once the function is back under (Ruff's
  `RUF100`).
- `TEST_BASELINE` and the two ESLint ratchet files list the files over a limit
  at their current value. Lower or remove an entry when its file shrinks. A
  listed complexity applies to every function in its file; keep new functions
  within 15 anyway.
- `tests/unit/test_distribution_neutrality.py` keeps mentions of one
  particular deployment of this code from growing, per file, against
  `tests/unit/distribution_neutrality_allowlist.json`. After removing mentions,
  run `uv run python scripts/check_distribution_neutrality.py --write`.
- `tests/unit/test_env_classification.py` keeps the variables and Compose
  profiles a deployment must supply itself (`tests/unit/deployment_config_baseline.json`)
  from growing, and fails on a listed one that something now writes or that is
  gone. Put new configuration in Admin, a host setting or what server setup
  writes, and start every service always, with a setting deciding what it
  does. Adding to the list is a decision: say in the pull request why nothing
  else can supply it.

## 4. Workflow

- Branch off `dev` as `<user>/<scope>/<name>`; pull requests target `dev`.
  Keep each one to a single feature, fix or documentation change.
- Never commit secrets. This repository is public.
- Maintained guides go in `docs/developer/`. New designs and implementation
  plans go in `docs/agents/specs/` as `YYYY-MM-DD-<topic>.md`. Delete a record
  once its work ships or is superseded and no maintained guide cites it; git
  history keeps it. Verification and review evidence goes in the pull request,
  not the repository. Create no other top-level folder under `docs/`: tools
  that default elsewhere, such as the superpowers plugin's
  `docs/superpowers/`, must write to these folders instead.
- Hosts roll out before the control plane, and each side supports the previous
  release only. Managed compute hosts are the exception: the new control plane
  rolls them forward ([Compute resources](docs/developer/operations-compute.md)).
- Mark code kept for an older release with a comment,
  `COMPAT(remove-after=YYYY-MM-DD): <what makes it removable>`.
  `tests/unit/test_compat_markers.py` fails after that date: remove the code,
  or move the date and give the reason it must stay.

## 5. Permanent rules

Tests and design records cite these rules by number; rules 1 and 2 covered the
move out of HybridInference and are retired.

3. **Never weaken a test to make it pass.** A failing test has found a break:
   fix the cause, or report it. Adapting fixtures and imports is fine; changing
   an assertion to get a pass is not.
4. **No HybridInference secrets here.** This service never sees the
   HybridInference Gateway's `API_KEY_SECRET`, `JWT_SECRET_KEY` or database.
   Its own tokens use its own secrets (`AGENT_CONTROL_TOKEN_SECRET`,
   `AGENT_LOCAL_SESSION_SECRET`, `AGENT_SETTINGS_ENCRYPTION_KEY`). A mail
   transport credential an operator chooses to share (SMTP) is configuration,
   not identity.
5. **No Gateway dependency.** Never import `serving.*` in Python, and use no
   Gateway sign-in, admission, grant or catalog. They were removed
   ([decision](docs/agents/specs/2026-10-30-independent-accounts-only.md)), and
   a configuration that names them is refused at startup.

## 6. Key concepts

[Code structure](docs/developer/code-structure.md#vocabulary) has the full
glossary.

**Conversation, job, attempt.** A conversation (`thread_id` in code; a chat or
task in the UI) is a series of turns. Each turn is a job: one user message and
the agent's work on it, numbered by `turn_no`, and every job after the first
names its `parent_job_id`. An attempt is one run of a job on one host, under a
lease with a fencing generation. The store rejects a superseded attempt's
writes, which keeps retries safe and prevents duplicate pull requests.

**Model connection.** Every turn runs on a model connection: a provider's Base
URL, an API key and verified model ids. Usually it is the owner's own, and the
claim and residency of the owner's turn hand its key to the host for the
member's CLI. A deployment may also provide connections: an administrator saves
an endpoint and the deployment's key, every admitted owner may use it, and the
key stays in the control plane behind the model relay
([design](docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md)). A turn
runs at Auto, the provider's default, unless its owner picks a reasoning level
that the provider's model listing declares for that model and API and that the
agent's CLI can send
([design](docs/agents/specs/2026-11-05-upstream-reasoning-effort.md)).

**No budget.** There is no per-task dollar budget, no local quota and no
billing: the provider keeps each key's quota and bill, a provided connection's
included. The model relay records the tokens and listed-price cost of each
provided-model request but limits nothing. The Usage pages show the tokens
agents reported for calls attributable to a turn (`runtime_reported`), which
may undercount the provider's bill. If a task seems to need a budget field,
stop: having none is a settled decision.

**MCP.** The control plane resolves MCP servers per user, and sandboxes reach
them through its MCP-only relay with a separate scoped credential. A member's
CLI holds its residency's MCP credential, which the relay resolves on every
call to the turn that residency is running, so follow-ups keep the live
process.

**Host.** A machine in the pool running the host agent. It registers with a
one-time enrollment token, heartbeats, and claims jobs it has the capacity and
capabilities for. Hosts are added and removed without redeploying the control
plane.

**Sandbox.** A sandbox group is one retained container and its workspace. Each
chat is a member with its own native session and CLI process; Fork adds a
member to the same group and workspace. Follow-ups resume the native session in
that container, and nothing replays a transcript. If the container or its
native state is lost, the turn is refused, unless the group was stopped cleanly
with a current restorable checkpoint that another host can restore
([recall](docs/developer/context-recall.md),
[design](docs/agents/specs/2026-08-31-native-session-fork-shared-workspace-design.md)).
Each group has one network, the same for setup and the agent, on a routable
bridge of its own; [§7](#the-sandbox-network) lists what it reaches
([design](docs/agents/specs/2026-09-23-agent-public-egress-design.zh-CN.md)).

## 7. Security boundaries — do not relax without review

Each item is a boundary, or a risk accepted on purpose.

### The sandbox network

A sandbox group reaches only:

- public IPv4 on TCP 80 and 443;
- the one named resolver, on TCP and UDP 53;
- on its own bridge, its MCP relay (the MCP-only listener's tools) and, where
  its host has them, the model relay and the GitHub relay's tunnel, which `git`
  and `gh` reach as `github.com` and `api.github.com` on port 443;
- the exact public IPv4 address and port of each SSH server its owner
  registered, checked and enabled for one of the group's chats.

Nothing else: none of the host's addresses; no private, link-local/metadata,
CGNAT, multicast or reserved range; nothing in `AGENT_EGRESS_PROTECTED_CIDRS`;
no other group, other port, UDP 443 or IPv6; and no inbound connection.
Per-group bridges and this deployment's host nftables table enforce it on the
actual packets. `cloud_agent_shared/egress.py` renders the table, and
`cloud_agent_host/network/network_guard.py` installs it and reads it back
before any group joins its bridge; a failed check stops the group, with no
fallback. No relay is a forward proxy a caller can point elsewhere, and there
is no proxy at all. No mode, flag or proxy widens this network: adding one is a
design change, not a configuration.

### Credentials in the sandbox

- MCP tool calls are served only on the MCP-only listener. The public main API
  must not expose them, and the MCP tunnel targets that listener.
- Platform-held long-lived account and service credentials never enter the
  sandbox.
- Each member gets its own scoped inference and MCP credentials, which a
  sibling cannot read: distinct UIDs, private provider homes and tmpfs, and no
  credential in a process argument. That stops reading, not writing. Siblings
  share one writable workspace, `.git` included, so code a member plants there
  (a Git hook, `core.fsmonitor`, a build or test script) runs with the
  credentials of whichever member executes it next.
- Accepted: a member sandbox holds its owner's provider key for the turn it
  runs, and code or instructions it meets may send the key out. It is the
  owner's own key and quota, and grants no MCP or platform-management authority
  ([decision](docs/agents/specs/2026-09-23-agent-public-egress-design.zh-CN.md)).
  On a provided model the sandbox holds only a placeholder
  ([Provided models](#provided-models)).

### GitHub

No credential that GitHub accepts enters a sandbox. A member's `GH_TOKEN` is
its residency's `agh.` placeholder, which only the GitHub relay accepts. While
the residency serves a running turn, the relay sends requests about the
conversation's repository with the owner's GitHub user token, and any other
request with no credential. It refuses branch deletion, tags, GraphQL, and the
repository's administration and secrets. Commits are authored as the owner;
the owner's token and refresh token, and the App's private key, stay in the
control plane. Nothing on the platform scans what goes through the relay:
branch protection, GitHub's push protection and the repository's CI still
apply ([design](docs/agents/specs/2026-11-04-agent-native-pull-requests.zh-CN.md)).

### Owner environment credentials

An owner may deliberately inject personal variables and Secrets through a
frozen environment binding. Accepted: repository code, dependencies and the
agent can read them, send them out, or write them into files or model context.
The platform encrypts stored values, keeps Secrets write-only, filters values
by phase (setup or runtime) and masks known values in durable output, but none
of this prevents deliberate exfiltration. Nothing on the platform scans a push;
GitHub's push protection and the repository's CI are the only checks.

A same-owner Fork inherits the binding; across owners, only a team environment
is shared. Reserved platform and host variable names, the network and the
member credential boundaries still apply. An owner's `OPENAI_*` or
`ANTHROPIC_*` variables never replace an agent CLI's own inference
credentials; a member-private `BASH_ENV` script gives them to the commands the
CLI runs
([design](docs/agents/specs/2026-10-12-repository-environment-design.zh-CN.md)).

### Team environments

A team's owner and admins may save an environment that every active member
uses for their own tasks, in their own sandboxes. Members of a team are one
trust domain for its environments, as for a repository they can all push to
([design](docs/agents/specs/2026-10-15-team-shared-workspace-design.zh-CN.md)):

- Its Secrets are readable by every member who runs it. The write-only editor
  hides them on screen, not between members, and the dialog says so.
- Its installation snapshots serve the whole team, and a member's task or test
  can queue a build from a commit that member checked out, so one member can
  shape what the others' sandboxes start from. Personal globals join only at
  run time, so the installation is the same for everyone.
- A binding records the member's access generation. Every read that hands its
  values to a container (each turn's claim, terminals, actions, Fork,
  preparation, tests) refuses once the member has left or the team is deleted.
  A value already read cannot be taken back: rotate it.

### Owner SSH servers

An owner may register SSH servers and enable them per chat. The platform
generates one Ed25519 key per server, stores it encrypted, and writes it only
into the enabled member's private tmpfs, with the host key the owner confirmed
pinned. Accepted: the agent, repository code and anything they run can read,
use or exfiltrate the key, and run anything the server account may run. The
owner revokes it by deleting the server, which cuts live sessions and erases
the stored key, and by removing its line from `authorized_keys`.

A key reaches a member only from that member's own turn's claim, and an update
pushed mid-turn only takes access away, so the runner masks every key in a
sandbox in its durable output. The control plane dials a server only when its
owner runs a check, under the same public-address rules, an hourly per-owner
limit and a 64 KB cap on what it reads back
([design](docs/agents/specs/2026-10-15-user-ssh-servers-design.zh-CN.md)).

### Owner model connections

An owner may add an inference endpoint (a Base URL, an API key and up to
sixteen model ids) and run their own tasks on it, paying the provider. It is
private to them: never shared with a team or reachable through another owner's
Fork, and every claim, residency, follow-up, waiting message, goal round and
Fork re-reads it as theirs. Login and Agent admission stay with the account
system ([design](docs/agents/specs/2026-10-27-user-model-connections.zh-CN.md)).

**The key** is encrypted in its own domain and write-only, and reaches a host
only in the claim and residency of its owner's turn on it. The member's CLI
holds it in its environment and activation tmpfs, never in argv, and the runner
masks it in durable output. Accepted: repository code, dependencies and the
agent can read it and send it out. An encrypted scanning copy is kept with the
sandbox group record once a key is bound to a residency, even after the
connection is deleted; nothing reads it, and no execution path selects it.
Deleting the connection does not revoke the key at the provider.

**Verification.** Nothing is saved unverified, and nobody declares a
connection's APIs by hand. Creating a connection, or changing its key or Base
URL or adding models, verifies each model inside the save, on the owner's
explicit action, and writes nothing (not even a new key) unless every model has
an agent that can use it. An edit commits only if the row is unchanged since
verification read it, so a late result never undoes a rotation, a disable or a
delete. Verification reserves the owner's hourly checks before any provider
call and has a 240-second deadline. It dials only public IPv4 HTTPS, under the
MCP connection policy and the sandbox's protected ranges, pinned, with no
redirects, and echoes no upstream body. Listing a provider's models to fill in
the form writes nothing and spends one hourly check; it uses the key just
entered, or a saved key only at the exact Base URL it was saved with.

**Runtimes.** A model without current facts runs on nothing. A runtime uses its
own API when that API is verified for the model (Chat Completions: OpenCode,
Kilo, pi; Responses: Codex; Anthropic Messages: Claude Code). Codex and Claude
Code may also use a model verified only for Chat Completions, through a
member-private translator: a loopback service forked as the same member UID,
bound to that one upstream, key and model, and dying with the CLI. It answers
only a platform-issued token scoped to the owner, member, connection revision,
client protocol and model; the runner masks the token like the key, and a
sibling's token is refused. The CLI never holds the key on this path, and the
service refuses, rather than drops, what Chat Completions cannot carry.
Nothing else is translated, nothing falls back to another connection, model or
API, and a claim and its residency must agree on both the credential revision
and the plan
([design](docs/agents/specs/2026-10-28-model-protocol-compatibility.zh-CN.md)).

**Request meter.** A direct plan's CLI starts behind the same launcher, its
loopback service in passthrough: bound to the connection's one upstream origin,
it forwards the CLI's own requests and credentials unchanged, holds no key and
issues no token. It only adds a size record per model request on the CLI's
stderr (the character counts of the system prompt and tool schemas, never
their text), which the host reports as a `request_prefix` event. It is not a
network proxy: the member's egress and the network rules stay the same, and a
sibling that reaches its port can do nothing through it that the public egress
does not already allow. A CLI pointed at its vendor's own host (Claude Code on
`api.anthropic.com`) is not metered, and a meter that cannot start leaves its
CLI pointed at the provider directly
([design](docs/agents/specs/2026-11-05-request-prefix-meter.zh-CN.md)).

**Changes.** A verified new key or Base URL replaces every model's facts. That
change, disabling or deleting closes every process still running on the old
state; a rename, removing models or enabling makes no provider call and closes
nothing.

### Provided models

An administrator may save a provider's Base URL and the deployment's own key.
The connection offers the models the provider lists for that key (up to
sixteen) to every admitted owner alike, with no setup on their part
([design](docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md)).

- The key never leaves the control plane. It is encrypted under the settings
  root key in its own domain and write-only, and is in no claim, residency,
  host, sandbox, response, log or audit row. A turn's claim marks the
  connection *relayed* and carries no key. The member's CLI is launched at the
  connection's path on its host's model tunnel and holds its residency's
  `amd.` placeholder, which no provider accepts.
- Only the model relay accepts the placeholder, on a listener of its own that
  serves nothing else, and only while the residency is the chat's hot one,
  serving a running, leased, unstopped turn on an enrolled host, on that
  connection, at the revision its process started on. Stop, a superseded
  attempt, a rotated residency, a removed host, and a connection disabled,
  deleted or moved to another Base URL take effect on the next request, with
  nothing to revoke at the provider. A rotated key closes no process.
- The relay is not a proxy. It dials only the connection's Base URL, under the
  rules of a connection check (public IPv4 HTTPS, pinned, no redirect, no
  proxy). It forwards only the inference and model-listing paths an agent CLI
  calls, matched whole, for a model the connection lists, and refuses any
  credential but its own placeholder, so an owner's key never comes this way.
  Nothing falls back between a provided connection and an owner's own.
- Nothing is limited per owner: the provider's own budget and limits on that
  key bound what the deployment spends.
- Accepted: an owner can relay their own running turn's requests to something
  outside the sandbox, under their own name and up to the key's own limits;
  one owner's heavy use spends the key's budget for every owner; a sibling can
  cause a member's placeholder to be used, as with the other residency
  credentials; and provided-model requests and answers pass through the
  control plane, which stores and logs none of their content.
- Saving, editing and reading the listing again verify the models like an
  owner's connection, on the administrator's explicit action, and write nothing
  unless at least one model has an agent that can use it. Every change is in
  the admin audit trail, without the key.

### Isolation and the Docker socket

- Public multi-tenant use requires Kata isolation, non-root, dropped
  capabilities, seccomp and resource limits. Plain Docker containers are for
  trusted internal repositories only.
- The Docker socket is the host's authority. Only the runner and the Sandbox
  Manager mount it, and `:ro` on that mount does not narrow the Docker API:
  either can start a privileged container, so a flaw in either is a host
  compromise. No other service may mount it; the deployment tests and CI's
  Compose check pin the holders.
