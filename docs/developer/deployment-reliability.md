# Deployment failure checks and queue monitoring

Every activation must pass the exact-SHA readiness fence. The control-plane
wait allows 900 seconds (`SHARED_READY_TIMEOUT`) for cold host builds; host-local
checks keep a 120-second budget after the build. When an Actions deployment sets
`DEPLOY_PEER_JOB`, [`deploy/watch_peer.py`](../../deploy/watch_peer.py) watches
the peer host job in the current run attempt during the read-only readiness
wait. A failed or cancelled peer ends the wait and removes its one-off
container; if the Actions API is unavailable, the bounded wait continues. The
watcher never interrupts migrations or service replacement.

## Drain through both activations

A single-host rollout that must not interrupt work drains one named host through
a database-backed admission fence. The orchestrator runs on the control-plane
machine, outside the pool:

1. It reads the host's current owner token and `drain_fence_epoch` and asks the
   API to replace that pair with a fresh opaque owner token. The database
   accepts only if both are still current, and rotates the epoch on commit.
2. It waits until the host reports `draining` and `slots_used=0`. A claim
   committed just before the drain finishes; no later claim can commit while
   the host is draining.
3. It activates the host with the fence in place, then the control plane with
   `AGENT_DEPLOY_DRAINING_HOST` set to the host's enrollment name. The readiness
   check then accepts only that draining host, with zero occupied slots, the
   target deployment capability, a fresh heartbeat and a successful reverse
   sandbox manager probe, and rereads them after the network wait. If new-chat
   placement is pinned to another host, that host must be online at the target
   commit. Ordinary readiness accepts only online hosts.
4. It releases the fence with the same owner token, which puts the host back
   online.

Acquire and release retries are idempotent, even after a lost response. A
successor takes over only from the exact token and epoch it observed; an
operator action, a lifecycle, credential or placement change, or a competing
rollout rotates the epoch, and the stale request fails. The database refuses to
delete a host while its fence is active, so drain safety does not depend on
deployments being serialized or on operators leaving the row alone.

On cancellation or an uncertain result, fail closed: leave the host draining and
retry with the same owner. Queued work waits; a running turn is never killed or
replayed. Migration `0051_host_drain_fence_epoch` and the
`AGENT_DEPLOY_DRAIN_FENCE_V2` recipe marker are the rollback floor; `deploy.sh`
still carries the V1 marker for older orchestrators.

### Reaching the fence without a credential

The orchestrator can read the pool and hold the fence through the admin API
(`GET /admin/agent/runner-hosts`, `POST …/<host>/drain` and `…/undrain`), which
needs `ADMIN_TOKEN`. On the control-plane machine it can instead run the same
operations in the control-plane container, which already holds the database
credential, so the deployment needs no static administrator credential:

```bash
docker exec <project>-control-plane-1 python -m cloud_agent.deployment.host_fence snapshot
docker exec -i <project>-control-plane-1 python -m cloud_agent.deployment.host_fence drain <host> < acquire.json
docker exec -i <project>-control-plane-1 python -m cloud_agent.deployment.host_fence undrain <host> < release.json
```

`drain` and `undrain` read the API's request body on standard input, never as
arguments, which any process on the machine can read. Each command prints the
`GET /admin/agent/runner-hosts` document. A refusal prints
`{"status", "code", "detail"}` on standard error, with the status the API would
answer, and exits 2. The audit trail records `agent.runner_host.drain` and
`agent.runner_host.undrain` with the actor `service:deployment-drain`.

## Before stopping a serving runner

Source and artifact deployments resolve the candidate runner image through
Compose and run `cloud_agent_host.candidate_check` before stopping the old
runners. The candidate has no network, enrollment state, sandbox manager token
or live stack mounts besides the Docker socket. It creates and removes a
temporary guest on network `none`, checks kernel isolation and the guest
observer, and compares the **built image's** runtime and helper labels with its
pins. If it fails, the old readiness marker and runners stay in place. The new
runners, relays and sandbox manager must still pass their live checks before
`mark-ready`; a later failure uses the recorded-generation recovery path.

The stop signal drains the host service: it stops claiming and waits for each
turn child to finish its turn, without forwarding the signal to the children.

### Host runtime qualification

The host service runs the full Kata probe at startup, whenever the sandbox image
or the runtime epoch changes, and at least every 30 minutes; a Docker restart
also restarts the runner, which probes again. A failed qualification stops
claims, logs once and retries after five seconds, backing off up to five
minutes. Updating or malformed epoch evidence blocks acceptance (see
[Kata runtime qualification generation](../../ENVIRONMENT.md#kata-runtime-qualification-generation)).
Probe containers carry `org.freeinference.chat-probe-created`, so the sandbox
manager never adopts them as chats, and each probe removes abandoned probe
containers in its namespace that are over an hour old.

The host passes its verdict to each turn child. The child rereads the epoch,
declines during an update, probes for itself if the epoch changed, and checks
the host network before accepting the turn. Without an epoch, every turn runs
the full probe, as do deployment canaries.

### Network verification and startup work

The host service keeps the network guard verification from its startup check
fresh, renewing it between claims once it is 35 seconds old (or at a handover
once it is 45 seconds old), and passes it to each turn child. The child declines
the turn unless that verification is present and successful. A child that
starts after the guard's one-minute window logs
`network_guard_verification_aged_out` and installs and reads back the rules
itself. The sandbox manager verifies a group's bridge when it starts a turn
activation and returns `network_verified`, which the runner trusts for one
minute; terminals and workspace operations always check. The sandbox manager
also installs and reads back the rules before any group joins its bridge.

A new chat's repository is cloned and archived on the host while its container
starts; the `checkout` measurement includes the remaining clone wait
(`source_wait`) and import (`source_import`). Startup measurements cover claim
request through prompt dispatch, with qualification under `preflight`, and
exclude queue wait and model time. A host at capacity claims again as soon as a
turn's child exits or an environment test ends; failing that, it checks after a
second, or five seconds while an environment test holds one of its slots.

### Empty-queue waits and CI evidence

After an empty claim, the host calls `/v1/agent/worker/wait`, which returns when
Postgres reports queued, requeued or ended work, or after five seconds; the host
then claims again. Changes the triggers cannot see, such as a moved placement
pin or a finished Fork, are still found within five seconds. If the endpoint
answers 404, the host polls and retries the endpoint after five minutes, so
hosts can deploy before the control plane.

CI runs the candidate check on the real Kata host for reviewed `dev` commits
before `CI Gate` permits a release, using uniquely tagged images and never the
deployed stack; pull-request code does not run on that machine. A local Docker
test is not evidence that the Kata-host check passed: use the named CI job's
result.

## Old process, candidate schema

The Postgres CI job finds the latest `agent-*` release tag reachable from the
change's base and runs
[`scripts/check_rolling_upgrade.py`](../../scripts/check_rolling_upgrade.py). It
migrates a random `rolling_test_*` database with the previous release's Alembic
graph and warms that release's storage pool with conversation reads and writes
and with the writes a runner's turn makes: a host heartbeat, the claim, a lease
heartbeat, an event, the sandbox reservation, residency, admission and
settlement, and the terminal transition, for a new chat and a follow-up. With
the old process still running, it applies the candidate migration and repeats
those operations, tests an idempotent migration retry, and removes the database.

Run it against disposable local Postgres, with an existing earlier `agent-*`
tag:

```bash
ROLLING_BASE_TAG='agent-<previous-sha>'
TEST_DB_HOST=127.0.0.1 TEST_DB_PORT=5432 \
  uv run python scripts/check_rolling_upgrade.py --base "$ROLLING_BASE_TAG"
```

It covers the common storage paths against the previous release only;
authenticated browser flows and older releases need separate evidence.

## Host and control plane one release apart

Hosts deploy before the control plane, and managed compute hosts follow a new
control plane later, so each side talks to the other's previous release for a
while. When a change touches a backend package or the locked dependencies, the
same Postgres job runs
[`scripts/check_mixed_protocol.py`](../../scripts/check_mixed_protocol.py)
against the previous release. It runs four pairings, each on a random
`mixed_protocol_test_*` database migrated by its control plane: this host with
the previous control plane, the previous host with this control plane, and each
release with itself as the baseline.

In each pairing the real control plane serves on loopback, and the host's real
enrollment, claim and attempt client run a new chat's turn and two follow-ups, a
cancellation, settle, abandon and finish, and a stop and residency eviction the
control plane orders, against a scripted sandbox manager. A pairing fails on a
refused call, a message the receiver rejects or would silently drop a field of,
or a database that differs from the baseline's. Four more pairings check the
control plane's calls to a host's sandbox manager (inventory, member status,
group registration, residency loss, SSH access push, terminal suspend and
resume) against each release's fake supervisor. No image, sandbox or model runs,
so this does not replace release acceptance or the Kata check.

Run it with the same tag as above:

```bash
TEST_DB_HOST=127.0.0.1 TEST_DB_PORT=5432 \
  uv run python scripts/check_mixed_protocol.py --base "$ROLLING_BASE_TAG"
```

## Detection after deployment

The main API runs one queue monitor; the MCP listener runs none. After two
consecutive observations a minute apart, it reports queue heads at least ten
minutes old:

| Event | Meaning |
| --- | --- |
| `queued_without_host` | Waiting work has no enrolled, fresh, compatible host, accounting for retained ownership and the new-chat placement pin. A turn of a `cold` sandbox counts any Host that restores, but not the Host it left while that one still holds its container. |
| `queue_not_starting` | Old queue heads remain and no job is running, even if host heartbeats look healthy. |
| `queue_health_unavailable` | Repeated database observations failed. |
| `queue_recovered` | A reported problem cleared. |

Follow-ups waiting behind an earlier turn are excluded, and occupied slots with
running work are normal backpressure. The monitor only reports: it never claims
jobs, restarts hosts, changes scheduling or relaxes isolation. A dead API cannot
monitor itself, so keep external process and HTTP monitoring.

Events are always logged. For notifications, set **Alert webhook URL** in
Admin → Settings → Operations (initially `AGENT_ALERT_WEBHOOK_URL`) to a receiver
you own; the alert timings are under Advanced there. Queue alerts send JSON with
`text`, `event`, `deployment_sha` and aggregate `queue` counts, never prompts,
repository names, job IDs, credentials or logs; chat providers may need an
adapter. Redirects are refused, and a failed delivery is logged without the URL
and does not affect requests. Notifications follow state changes, repeat at most
every 30 minutes while unchanged, and retry a failed delivery the next minute.
Deployment-workflow alerts belong to the downstream distribution.

## Recovery boundaries

The [backup and recovery runbook](disaster-recovery.md) covers retention and
recovery targets, coordinated database and host recovery sets, encryption keys,
restore drills and recovering a previous artifact release.

After a failed deployment, inspect both machines' deployment markers and the
failure reason, then retry the same verified target. A failed host rollout is a
failed rollout even if it restored the recorded generation. A control-plane
failure leaves compatible migrations in place; there is no automatic database
downgrade. A hard-cancelled host activation may leave the readiness marker
pending while Docker finishes the graceful runner stop; running turns still
finish, and the host stays unavailable until activation is rerun for the same
target. Do not publish readiness by hand. If the host succeeded and the control
plane failed, the exact-SHA gate can keep new work queued until the retry
succeeds; the queue monitor reports it.

### Push and pull-request failures

The platform does not publish workspaces, so there is no publication to retry.
A push or pull request the agent could not make is part of its turn: the
transcript shows what git or `gh` printed, and the owner asks again once the
cause is fixed. The GitHub relay logs each request it refuses as
`agent_github_relay_refused` (`agent_github_relay_push_refused` for a push) with
the conversation and job, and each pull request it records as
`agent_github_relay_pull_request_recorded`. A refusal from GitHub itself, such
as branch protection, push protection or a missing App permission, appears only
in the transcript. See [the GitHub relay](operations.md#the-github-relay).
