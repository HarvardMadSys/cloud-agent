# Developer guide

How to get a checkout running, find the code that owns your change, and choose
its checks. [AGENTS.md](../../AGENTS.md) has the repository rules and
[CONTRIBUTING.md](../../CONTRIBUTING.md) the pull-request workflow.

## Set up a checkout

You need Python 3.12 and [`uv`](https://docs.astral.sh/uv/). From the
repository root:

```bash
make setup-dev
make check
```

`make setup-dev` creates `.venv` and installs the project with its development
dependencies (`uv sync`). CI uses Python 3.12; 3.13 is also supported. Run
Python tools with `uv run`. Package discovery already includes `apps/backend/`,
so you do not need `PYTHONPATH`.

For frontend work, use Node.js 22 and install the locked dependencies:

```bash
cd apps/frontend
npm ci
```

To exercise sign-in, migrations, host enrollment and real Agent CLIs together,
run the [local Docker stack](../../deploy/local/README.md) from the repository
root:

```bash
./deploy/local/dev.sh up
./deploy/local/dev.sh smoke
```

## Find the code that owns a change

The backend is three installed packages under `apps/backend/`:

| Package | Owns |
|---|---|
| [`cloud_agent/`](../../apps/backend/cloud_agent/) | Control plane: HTTP APIs, accounts and admission, scheduling, persistence, and the MCP, GitHub and model relays. |
| [`cloud_agent_host/`](../../apps/backend/cloud_agent_host/) | Host execution: claims, retained sandboxes, the Sandbox Manager, and Agent CLI adapters. |
| [`cloud_agent_shared/`](../../apps/backend/cloud_agent_shared/) | Protocol versions, capability names, shared payloads and errors, profiling collection, and shared Git and archive code. |

[Code structure](code-structure.md) maps the layers inside each package. For
common changes, start with these files (backend paths are relative to
`apps/backend/`):

| Change | Start here |
|---|---|
| Job or conversation HTTP behavior | `cloud_agent/api/jobs.py`, `catalog.py`, `turns.py`, or `threads.py`. `api/routes.py` mounts every agent surface listed in `SURFACE_ROUTERS`. |
| Request or response validation | The owning module in `cloud_agent/schemas/`; public imports use `cloud_agent.schemas`. |
| A new caller of task creation | `cloud_agent/application/job_admission.py`; `api/jobs.py` is an existing caller. |
| Workspace files, terminals, or Git views | `cloud_agent/api/workspace.py` and `sandbox_manager/sandbox_manager_client.py`; host handling is in `cloud_agent_host/manager_api/workspaces.py` and `terminals.py`, which `sandbox_manager.py` assembles into the app. |
| A query or persistent field | The relevant `cloud_agent/ports/` interface and `storage/` implementation, plus an additive revision under `apps/backend/migrations/versions/` for schema changes. |
| Compute-resource lifecycle | `cloud_agent/application/compute_resources.py`, `compute_reconcile.py`, `workers/compute_resources.py`, and `providers/compute/`. |
| Host claims or worker HTTP calls | `cloud_agent_host/claimed_job.py`, `control_plane_client.py`, and the orchestration in `service/host_service.py` and `turn/turn_child.py`. |
| Retained-container values or lifecycle | `cloud_agent_host/container/chat_sandbox_models.py` for value types; `container/chat_sandbox.py` for Docker lifecycle. |
| CLI behavior or version compatibility | `cloud_agent_host/runtimes/` and `drivers.py`; discovery facts are in `cloud_agent_shared/runtime_catalog.py`, shared pins in `shared_protocol.py`, image pins in `deploy/sandbox/Dockerfile.agent-sandbox`. |
| Browser UI or a BFF route | `apps/frontend/src/`; browser API clients are in `src/lib/api/agents/`, job-page components and hooks in `components/features/agents/job-detail/`. |

Keep a change in the module that owns the behavior. Extract a flow when it
needs its own test boundary, and a rule when several entry points share it.

## Keep the dependency boundaries

The deployment images ship different packages, so an import that works in a
checkout can fail in production. The
[import-boundary tests](../../tests/unit/test_import_boundary.py) and the
architecture checks enforce these rules, including for relative imports,
imports inside functions and `TYPE_CHECKING` blocks:

- Import the installed package names (`cloud_agent`, `cloud_agent_host`,
  `cloud_agent_shared`), never the checkout path `apps.backend`. The host
  image flattens the packages, so a checkout-prefixed import can work locally
  and fail after deployment.
- The host never imports the control plane, the control plane never imports
  the host, and the shared package imports neither. Runtime discovery facts
  both sides need live in `cloud_agent_shared/runtime_catalog.py`.
- In the shared package, only `cloud_agent_shared/git/` (Git subprocesses and
  archives) and the operation-profiling collector may import process, network
  or background-thread modules. The tests list each exemption.
- HTTP adapters call application flows and the storage capabilities they need.
  Bootstrap, application flows, workers and domain modules never import route
  modules. Application flows and domain rules never import FastAPI or
  Starlette: they raise the domain errors in `domain/errors.py`, which
  `api/errors.py` maps onto HTTP.
- `ports/` defines capabilities without importing their implementations.
  `Depends(get_storage)` returns the `Storage` assembly; type against the
  narrow protocol you use and call a capability such as `store.audit`. Callers
  outside storage never import `storage/repositories/`.
- Nothing depends on the HybridInference Gateway
  ([AGENTS.md §5](../../AGENTS.md#5-permanent-rules)).

A change to sandbox networking, credential scope or isolation also needs review
against [the security boundaries in AGENTS.md](../../AGENTS.md#7-security-boundaries--do-not-relax-without-review).

## Run the relevant checks

While you work, run the focused tests for the behavior you changed. Before a
pull request, run the whole backend gate:

```bash
uv run pytest -q tests/servers/test_agent_jobs_api.py
make check
```

| Command | Checks |
|---|---|
| `make format` | Ruff formatting and automatic lint fixes; changes files. |
| `make lint` | Ruff format check, and Ruff lint without fixes, docstring rules included. |
| `make typecheck` | Mypy over all three backend packages. |
| `make test` | Pytest excluding `external` and `dbtest`, with four workers and each file kept on one worker. |
| `make check` | `format`, `lint`, `typecheck`, then `test`. Review any automatic fixes before committing. |
| `make test-db` | The PostgreSQL-backed `dbtest` tier, against a PostgreSQL you provide. |
| `make test-db-local` | The `dbtest` tier on this machine's shared test PostgreSQL, in fresh databases of this checkout's own; needs Docker. |
| `make test-db-local-clean` | Drop the local test databases of checkouts that no longer exist. |
| `make test-all` | All tests except `external`, including `dbtest`. |
| `make docs` | Build this documentation site, every language, into `docs/build/html`; any warning fails. |
| `make docs-verify` | `make docs`, then check that every translation still matches its English page. CI runs this. |

Beyond `make check`, run what your change touches:

| Change | Also run |
|---|---|
| Storage, SQL, a migration or a database test | `make test-db-local` ([Database tests](#database-tests)) |
| Frontend | [the frontend checks](#frontend-checks), or `make ci-frontend` |
| Environment variables, Compose files, Docker packaging or workflows | The tests that check their wiring; document new variables in [ENVIRONMENT.md](../../ENVIRONMENT.md) |
| A page of this site, or a root guide it includes | `make docs-verify`, after updating the translation ([Translating the docs](translations.md)) |

`make test` and CI run the default tier with four workers, each test file on
one worker; `make check PYTEST_WORKERS=0` runs it serially for debugging.
Tests under `tests/servers/` exercise HTTP without a deployed server. Tests
marked `external` list their prerequisites (Docker, a real CLI or a model
provider) in their module, and CI runs a selection of them. Every test process
runs with CI's umask of `022` and without an inherited `CODEX_VERSION`
(`tests/conftest.py`). Outside the `external` and `dbtest` tiers, a host name
no resolver answers (under `.test`, `.invalid` or `.example`, or a single label
other than `localhost`) fails at once without a DNS lookup; use one for a host
a test only pretends to reach.

### Database tests

`make test-db-local` needs only Docker. It starts one PostgreSQL 15 container
per machine (`cloud-agent-test-postgres` on `127.0.0.1:54339`, data in
memory), creates empty databases for this checkout, and runs the `dbtest` tier
with up to twelve workers. Every run starts from empty databases, two worktrees
never share tables, and two runs in one checkout take turns. The run fails if
any database test was skipped.

- Narrow it with `ARGS`, for example
  `make test-db-local ARGS="tests/integration/storage/test_team_store.py -x"`.
- To run pytest yourself, `eval "$(scripts/test-db-local.sh env)"` creates the
  databases and exports the `TEST_DB_*` values.
- `make test-db-local-clean` drops the databases of removed worktrees.
  `make test-db-local-down` removes the container and every checkout's
  databases; do not run it while anyone else may be testing.

`make test-db` runs the tier against a PostgreSQL of your own. Fixtures read
`TEST_DB_HOST`, `TEST_DB_PORT`, `TEST_DB_NAME`, `TEST_DB_USER` and
`TEST_DB_PASSWORD`; `TEST_DB_NAME` must contain `_test_`, and parallel workers
need separate pre-created databases
([ENVIRONMENT.md](../../ENVIRONMENT.md#database)). Check the result for skipped
tests: an unreachable database makes fixtures skip rather than fail.

### Frontend checks

```bash
cd apps/frontend
npm ci
npm run lint
npm run format:check
npm run type-check
npm run test
```

CI also runs two build guards. Run the matching one when you change
server-only imports or runtime site configuration:

```bash
node scripts/check-server-only-guard.mjs
node scripts/check-runtime-site-config.mjs
```

Browser recovery tests run the production Next.js build in Chromium:

```bash
npx playwright install chromium
npm run test:browser
```

Run them on a test host with Node 22 and Chromium's system libraries; the
command installs no OS packages, and `PLAYWRIGHT_BROWSERS_PATH` can keep the
browser in a disposable directory. The runner builds the root web variant,
picks its own loopback ports and stops its servers afterwards; do not run
another build against the same `.next` directory at the same time. An HTTP
fixture serves the API and fails the suite on an unknown request, so these
tests cover the browser and its API calls, not the control plane, database or
hosts.

A test whose failed attempt saw `net::ERR_NETWORK_CHANGED` runs once more and
is reported as flaky; shared CI runners change addresses as other jobs start
containers. Any other failure fails at once (`apps/frontend/e2e/network-change.ts`).
Failures keep screenshots and traces in `apps/frontend/test-results/` and an
HTML report in `apps/frontend/playwright-report/`. After a successful build,
`CLOUD_AGENT_E2E_SKIP_BUILD=1 npm run test:browser` reruns the specs without
rebuilding; rebuild after app changes.

### Run CI locally

```bash
make ci
# Or select a tier:
make ci-backend
make ci-database
make ci-frontend
```

The launcher runs each gate with check-only formatting and linting. Backend and
database tiers need `uv` and Python 3.12, the frontend tier Node.js 22 and npm,
and the database tier a running Docker daemon. It syncs with
`uv sync --locked` and `npm ci`, and fails when `uv.lock` no longer matches
`pyproject.toml`; run `uv lock` and commit the result.
`./scripts/ci-local.sh [all|backend|database|frontend]` works from any
directory, and `all` is the default.

- Backend: Ruff format and lint checks, mypy, and pytest with four workers and
  `--dist loadfile`, excluding `external` and `dbtest`.
- Database: a fresh PostgreSQL 15 container on a random loopback port, one
  database per worker, removed on exit. It fails on skipped tests or fewer
  than 100 tests.
- Frontend: lint, format check, type-check, tests, both build guards and the
  browser recovery tests.

Logs go to a fresh gitignored `.local-ci/<tier>.XXXXXX` directory, or to
`CI_LOG_DIR`. External regressions, rolling upgrades, mixed-version protocol
checks, Gitleaks, release-image builds and Kata qualification run only in
hosted CI.

### Continuous integration

[CI](../../.github/workflows/ci.yml) classifies the changed paths and runs the
backend, frontend and image jobs they need. A pull request is compared with its
base, and a push with the newest `agent-*` release, so a released commit has
had every change since the previous release checked. `CI Gate` requires the
classifier to succeed and every other job to succeed or be skipped.

Secret scanning and the default test tier run for every change; the tier's
tests also read the documentation, `ENVIRONMENT.md` and the web app's sources.
Depending on the paths, CI adds Ruff and mypy, the PostgreSQL tier, frontend
checks, Compose rendering, image builds and smoke tests, and selected real CLI,
session and MCP flows. Pull-request image jobs build and smoke-test without
publishing. Real-Kata qualification runs only for trusted `dev` events, after
review. [Releases](releases.md) cover publication.

A pull request splits the default tier, the PostgreSQL tier and the frontend
unit tests over two hosted VMs each (`Tests (1/2)` and so on).
`scripts/ci_shard.py` deals whole test files to the pytest shards; vitest uses
`--shard`. To run one shard locally:

```bash
uv run pytest -m "not external and not dbtest" \
  $(uv run python scripts/ci_shard.py --shard 1/2 -- -m "not external and not dbtest")
```

Of the 15 real-CLI MCP cases, a pull request runs Kilo's three as separate
`MCP conformance (kilo, …)` jobs and the other twelve in the sandbox image job;
trusted events run all 15 there. Pull requests only read the Actions cache.
After each push to `dev`, the optional `pr-caches.yml` workflow refills the uv,
npm and sandbox-image layer caches under the `dev` scope; it runs apart from CI
and never changes CI's result.

#### Runners and forks

Every pull request, from a fork or from a branch here, runs on disposable
GitHub-hosted VMs (`ubuntu-24.04-arm` for ARM64 artifacts, `ubuntu-24.04`
otherwise), with a read-only token that checkout does not persist, anonymous
registry pulls and no secrets. Pushes to `dev` and `main`, authorized manual
dispatches, publication and synchronization use the self-hosted pools. Each
publish job builds in a fresh builder under its own `RUNNER_TEMP`. The only
layers a release reuses are its own registry cache, a
`buildcache-<artifact>-<ISO week>` tag that only the publish workflow writes,
so the first release each week builds cold.

A fork can edit workflow files, so the runner expressions checked in here are
not a security boundary. The boundary is GitHub's runner-group configuration,
which repository tests cannot set or verify; issue
[#464](https://github.com/HarvardMadSys/cloud-agent/issues/464) tracks the
remaining administrator work:

1. Move the persistent pools from the repository into an organization runner
   group.
2. Allow that group only the trusted workflow references on `refs/heads/dev`
   and `refs/heads/main`, including publication and manual synchronization.
   Deny pull-request merge and head refs and every other workflow file.
3. Until then, require approval for fork workflows from every external
   contributor, not only first-time ones.
4. Verify with an untrusted fork that edits a workflow to request a persistent
   runner: it must stay ineligible.

If the organization cannot restrict runner groups by workflow reference,
remove the persistent runners from the repository's eligible groups and run
trusted CI and publication in a separate restricted repository, or replace them
with credential-free single-job disposable runners. Fork approval alone is not
enough: approving a test run must never grant access to publication's hosts or
credentials.

## Changing an API

`apps/backend/openapi.json` is the committed snapshot of the API this service
serves. The web client's types and contract tests are checked against it, so a
route or schema change shows up in review as a change to this file. After an
intentional change, regenerate it from the repository root and run the surface
tests:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

from cloud_agent.app import create_app

Path("apps/backend/openapi.json").write_text(
    json.dumps(create_app().openapi(), sort_keys=True, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY

uv run pytest -q tests/unit/test_api_surface_contract.py
```

Review the diff and explain its compatibility implications in the pull
request. The surface tests also reject duplicate registrations and overlapping
agent surface routers. The snapshot records a change; the behavioral tests and
review decide whether it is compatible.

The shared protocols in `apps/backend/cloud_agent_shared/` are a separate
contract between the control plane and hosts. When you change them, review the
protocol versions, capabilities and runtime pins together.

## Packaging and documentation

When you add or move a package, review package discovery in `pyproject.toml`,
the mypy and linter roots, Docker `COPY` paths, and the path classifier in
`.github/workflows/ci.yml`. Checkout imports cannot show that a built image has
its dependencies; CI builds and starts selected images, and its classifier
tests derive shared package paths from the Dockerfiles.

Schema changes are explicit additive Alembic revisions on the chain that starts
at `apps/backend/migrations/versions/0001_schema_baseline.py`
([why it starts there](../../AGENTS.md#2-repo-map)). Service startup never
creates or alters the schema, and there is no SQLAlchemy model metadata to
autogenerate from. A database from before the independent-accounts release is
archived, not upgraded ([the cutover](upgrade-notes.md#independent-accounts-cutover)).

Update the guide that owns a changed configuration, command or behavior:

| Topic | Document |
|---|---|
| Environment variables and runtime pins | [ENVIRONMENT.md](../../ENVIRONMENT.md) |
| A new migration | [Upgrade notes](upgrade-notes.md) |
| Operation and troubleshooting | [operations.md](operations.md) |
| Local development stack | [Local-stack guide](../../deploy/local/README.md) |
| Release artifacts and validation | [Release guide](../../deploy/release/README.md) |

This site is built with Sphinx and MyST from `docs/developer/`. List a new page
in a table of contents in `docs/developer/index.md`. A root guide such as
`ENVIRONMENT.md` appears through a one-line `{include}` page registered in
`INCLUDED_PAGES` in `docs/developer/conf.py`. Links to other repository files
may stay relative: the build points them at GitHub, and fails on a missing
path, or on a missing heading in a page of this site. Run `make docs-verify`
after changing a page; CI's Docs Build job runs the same target. The site is
also published in Simplified Chinese, so an English edit needs its translation
updated ([Translating the docs](translations.md)).

Design records go in [`docs/agents/specs/`](../agents/specs/) under the rules in
[AGENTS.md](../../AGENTS.md#4-workflow). When the implementation changes,
update the maintained guide.
