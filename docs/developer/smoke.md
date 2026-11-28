# Staging end-to-end smoke

Run this checklist on a disposable staging deployment to check the whole path
from sign-up to a real pull request: a control plane, one runner host, a model
provider you hold a key for, and a disposable GitHub repository. A passing run
proves sign-up and verification, waiting-list approval, administration, a
verified model connection, host enrollment, sandbox execution and a pull
request the agent opened as the owner, for the recorded revision. It does not
cover concurrency, host loss or every isolation boundary; see
[deployment reliability](deployment-reliability.md) and
[native context recall](context-recall.md).

For a local check without provider keys, use the
[local Docker stack](../../deploy/local/README.md) (`./deploy/local/dev.sh up`,
then `./deploy/local/dev.sh smoke`). It tests setup, sign-in, administration
and that nothing runs before a model is connected, but no live provider or
pull request.

## 0. Before you start

- [ ] A disposable staging deployment with Docker and Compose, a checkout,
      enough memory for the CLI you test (8 GiB for running turns), and a
      Kata-capable host for public multi-tenant staging.
- [ ] The exact release SHA, recorded. The API, host, sandbox manager and
      sandbox must pass the shared deployment and version checks.
- [ ] A new, empty Agent database:
      `python -m cloud_agent.deployment.identity_namespace --check` must report
      it empty and change nothing
      ([cutover](upgrade-notes.md#independent-accounts-cutover)).
- [ ] A mail transport for test addresses you control (or email verification
      turned off for the run), and a provider API key you may spend.
- [ ] A disposable GitHub repository with the deployment's GitHub App
      installed, that the test account's GitHub account may push to, and the
      GitHub relay configured. Record its base branch.

## 1. Configure

Neither machine needs a hand-written environment file
([ENVIRONMENT.md](../../ENVIRONMENT.md) lists the optional overrides). On the
first start with the bundled database, the `keys` service generates the
database password and DSN, `AGENT_LOCAL_SESSION_SECRET` and
`AGENT_SETTINGS_ENCRYPTION_KEY` into the `agent-keys` volume, and the control
plane generates its other keys into its settings
([new installation](operations.md#admin-settings)). Back up that volume apart
from the database, or saved connections and settings cannot be decrypted.

- [ ] Confirm the site address in first-run setup (`/setup/administrator`), or
      set **Web app URL** and **Public API URL** under Admin → Settings →
      General. The
      app is served at the root of its hostname, with the API at `/api`. Set
      no `GATEWAY_*`, `IDENTITY_*` or `AGENT_AUTH_MODE`; they are refused.
- [ ] Add the host with Admin → Servers → **Add server**, which writes its
      configuration, including the non-overlapping egress pool and resolver
      you give it.
- [ ] Keep rendered configuration out of logs: it contains secrets. Direct
      control-plane Compose commands need `AGENT_DEPLOYMENT_SHA` exported; the
      deployment scripts set it.

## 2. Start the control plane

Deploy as [deployment reliability](deployment-reliability.md) describes. On a
cold source deployment, the `prepare` and `prepare-database` phases build
images, start Postgres, check the identity namespace, migrate, and start the
API and MCP listener that host readiness needs; activation waits for the host
in section 4.

- [ ] Migrations reach the checkout's head (the API creates no tables).
- [ ] The main API answers `/healthz` on port 8000 and the MCP-only listener
      on port 8001, the one its tunnel targets.
- [ ] The log shows `agent_local_setup_pending` with a one-time setup code.
      Keep the code out of the run log.

## 3. Accounts

- [ ] Exchange the setup code at `/setup/administrator` and sign in. A second
      attempt answers 409 `setup_complete`.
- [ ] `GET /v1/session/me` returns the administrator's `id` and
      `agent_access: {allowed, permissions}` with `agent.admin`. The cookie is
      `__Host-cloud_agent_local_session`; keep cookies and tokens out of the
      log.
- [ ] In a second browser, sign up a test account, verify it and apply. It
      sees the waiting list and cannot create a task.
- [ ] Approve it under **Admin → Users**. It now holds `agent.use`.

## 4. Enroll the host

- [ ] With infrastructure admin access, call
      `POST /admin/agent/runner-hosts/enroll-token` with the `host` name, and
      put the single-use token it shows into the host's private configuration.
- [ ] Deploy with `deploy/host/deploy.sh` for the same release. It attests the
      sandbox image, starts the tunnels and sandbox manager, probes the MCP
      relay and publishes the completed-generation marker; starting the
      runner's containers by hand does not pass this gate.
- [ ] `GET /admin/agent/runner-hosts` shows a fresh heartbeat, the expected
      deployment capability and a reachable sandbox manager URL; check the
      reverse route from the control plane.
- [ ] Finish control-plane activation; its readiness check passes for this
      host and API SHA.

## 5. One job, all the way

- [ ] As the test account, add a model connection under **Settings → Models**
      and choose **Verify and save**.
- [ ] Grant the scratch repository under **Settings → Integrations**.
- [ ] Create a task with a small verifiable change, such as adding a
      `CONTRIBUTING.md` with a given sentence. Record the job, attempt and host
      identifiers as it goes from `queued` to claimed. The turn names its
      model and connection, and the reasoning control offers Auto plus any
      levels the provider declares.
- [ ] Events stream to the browser, a model or tool call succeeds, and the
      usage footer shows reported tokens billed by the provider.
- [ ] The turn succeeds with the expected change. Ask the agent to open a pull
      request, then check that you author the pull request and its commits,
      the target branch and the diff. Record its URL.
- [ ] List files and open a terminal on the retained workspace.

## 6. Failure and recovery

Inject failures only on disposable staging, restoring settings after each.

- [ ] Cancel a running turn: the worker acknowledges and the turn ends
      cancelled.
- [ ] Withdraw the test account's access during a turn: the work stops, open
      streams end at their next 30-second check, and new tasks are refused.
      Restoring access does not restart the old turn.
- [ ] Stop the database briefly: neither a fresh sign-in nor an owner request
      gets access, and `/me` shows a temporary failure. Service recovers once
      the database is back.
- [ ] Stop the API during a task, start it again, and check lease and reaper
      handling.
- [ ] Check a live follow-up and an idle restore as in
      [context recall](context-recall.md#verification-before-accepting-a-deployment).

## 7. Tear down

- [ ] Stop the stacks by their recorded project names, keeping data until the
      evidence is captured; use `down -v` only on a database you mean to
      lose.
- [ ] Delete the scratch branches or repository and the model connection, and
      revoke the test key and repository credentials when staging is retired.
- [ ] Restore failure-injection settings and record the result under
      [Log](#log).

## Log

Record, as you go, the UTC time, result and notes of each numbered section,
and the source SHA, environment, host, job and pull request identifiers. Keep
the record with the deployment's acceptance evidence.
