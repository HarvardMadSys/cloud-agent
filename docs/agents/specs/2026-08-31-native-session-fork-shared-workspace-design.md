# Retained native sessions and shared-workspace Fork

Status: implemented. This is the only chat-sandbox topology; there is no
feature flag and no older executor.

This record covers how a chat runs: a retained Docker container, live Agent CLI
sessions, immediate native Fork, concurrent sibling chats, lifecycle and
publication. Archives, checkpoints and cross-host restore are described in
[operations](../../developer/operations.md#restoring-a-removed-hosts-sandboxes).
Rows from the removed topology stay in the database for audit but are not
runnable: follow-up, Fork, terminal, file and claim operations on such a chat
return a context/workspace-unavailable error.

## 1. Topology

Every chat belongs to a sandbox group. A normal chat is a group with one member:

```text
chat A -> native session A -> live Agent CLI process PA
                              -> sandbox group G -> Docker container C -> /workspace
```

Fork adds a member to the same group:

```text
chat A -> native session A -> process PA --+
                                           +-> container C -> /workspace
chat B -> native session B -> process PB --+
```

Invariants:

1. A group owns one retained container and one `/workspace`.
2. A chat belongs to one group and owns one native Agent CLI session in it.
3. An active member has its own supervisor-owned CLI process. A settled turn
   leaves the process alive for the next message.
4. Fork creates a new chat, native session, resident slot and process in the
   same group. It does not copy the container or the repository.
5. Parent and child can run turns at the same time on the same workspace.
   There is no group-wide turn lock. Each member admits at most one unresolved
   turn.
6. Native session state is the source of truth. The platform never rebuilds a
   chat from a transcript or replays messages to a model. After `docker stop`,
   a crash or a process-fixed setting change, a replacement process resumes the
   same native session.

This behaves like two local CLI terminals in one repository: the workspace is
shared, the conversations are separate.

## 2. Storage

Chat data lives only in the container's writable layer:

- `/workspace`: Git metadata, dirty files, dependencies, build output.
- `/agent-state`: runtime-native sessions.
- the platform's native-fork receipts, kept outside provider state roots that
  some CLIs enumerate.
- member-private provider homes, for CLIs that cannot share one home between
  concurrent processes.

No chat workspace, state directory, snapshot, named volume or per-chat host path
is mounted into the sandbox. Host services mount the Docker socket to address
the container.

`docker stop` keeps the writable layer and `docker start` wakes the same
container, so the host's Docker data root must live on storage as durable as the
chats. If Docker loses the container or its data root, every member becomes
`LOST`, unless the group has a restorable checkpoint. The platform never fakes
recovery from stored event text.

Private homes:

- Claude's launch settings and process registry live in the member's
  `/home/agent-member-N/.claude-members/<thread-hash>`. Only `projects/` links
  to the group's `/agent-state/claude/projects`, which keeps Fork and resume
  working. Claude loads only private user settings, so project and local
  settings in the shared repository cannot configure a sibling's
  credential-bearing process. The thread hash stops a reassigned slot from
  inheriting an earlier member's settings.
- Codex, OpenCode and Kilo homes under `/agent-state/<runtime>-homes` are mode
  0700 to their member UID, because their config and plugins run as that member
  on every start. The collection directory is owned by the sandbox user, mode
  3770 (setgid, sticky), inside a sticky `/agent-state`, so a sibling can
  neither swap a member's home nor replace the collection. The member checks
  both before use.
- Fork copies a flushed source home into the child's through a host-side pipe
  between a source-UID `tar` and a child-UID unpack, so neither UID opens the
  other's home.
- The shared-workspace permission pass never widens private homes.

The optional archive policy (Admin, Settings, Sandbox) stores `/workspace` and
`/agent-state` archives on a clean stop, for export and forensics. The retained
container stays the source of truth.

## 3. Live sessions

The sandbox manager runs one supervisor per group, and the supervisor owns one
resident transport per active member. For a follow-up with a running process,
the runner sends only the new message. If the process is gone but the native
session is intact, the supervisor runs the runtime's native resume, then sends
the message.

| Runtime | Mechanism |
|---|---|
| Claude Code | stream-json live process, native session id, reviewed `claude-session-fork` helper |
| Codex | app-server live thread, resume by rollout id, native app-server fork |
| Pi | RPC process, `--session` resume, RPC `fork`/`clone` |
| OpenCode | authenticated serve bridge, native session fork |
| Kilo | authenticated serve bridge, native session fork |

A runtime is claimable only when the sandbox image labels, runtime and helper
versions, driver capability, sandbox manager configuration and slot capacity
match the protocol attestation. A mismatch or missing native state fails
closed.

Some CLIs fix model, reasoning effort, MCP configuration or credentials at
process start. When a change cannot be applied live, only that member restarts
over its native session. Siblings are untouched.

## 4. Fork

Fork is immediate: it runs the CLI's native fork before the API returns the
child chat, not on the child's first message.

The anchor is any settled native turn of the source chat, including an old one.
It needs a positive `native_turn_no`, an owner host that advertises
`native-fork-at-anchor-v1`, and a group image that can take the anchor (§9).
Failed, cancelled and unsettled turns are never anchors. Siblings keep running
during a Fork. Only the source session is quiesced while its prefix is forked.

The platform assigns `native_turn_no` when a CLI turn settles, before the job's
final state is written, and hands that stable ordinal to the host. The host
resolves it to the provider's own identifier at fork time, because Claude Code
and OpenCode/Kilo can rewrite message ids in a fork:

- Codex: inclusive `lastTurnId`.
- Claude: inclusive top-level assistant UUID.
- Pi: fork before the next user entry, or clone the leaf.
- OpenCode and Kilo: the next user `messageID` as an exclusive boundary.

The adapters count platform prompts, not every provider record with role `user`.
Claude tool results are user-role entries, so the adapter reads the raw chain,
skips `tool_result`, meta, sidechain and compact-summary records, and follows a
compact boundary's `logicalParentUuid` to keep earlier ordinals. OpenCode and
Kilo skip user messages with a `compaction` part and all-synthetic
auto-continue parts. An unrecognized history shape fails closed.

Sequence:

1. Create or recover an idempotent fork operation and mark the source
   membership `fork_preparing`.
2. Make sure the container is running and reserve a child slot.
3. Flush the source session `S` and run the prompt-free fork at the anchor.
4. Validate the new native session id `T`.
5. Create member-private provider state if the runtime needs it.
6. Start and probe a child transport over `T`. Resume the source over `S` if
   the fork needed a process restart.
7. In one fenced transaction, create the child chat and membership, store
   `S -> T`, copy the settled history prefix for display, and mark the child
   ready.
8. Return the child only after both native ids are stored.

A journal in the Docker layer makes a retry idempotent when the fork succeeded
but the response was lost. An ambiguous result fails closed.

Fork never runs `docker commit`, clones a sandbox, creates a second workspace
or branch, sends a synthetic prompt, or copies a transcript into model context.

The UI offers Fork once per turn, on the final settled assistant or tool event.
"Edit & rewind" is the same operation from the user-message side. It forks at
the nearest earlier forkable boundary, and the child opens with only that
prefix and the selected prompt as an unsent draft. Submitting it is a new native
turn. The original prompt is never replayed.

## 5. Shared-workspace semantics

Histories diverge after Fork. Files do not. Both agents see every workspace
change at once, so:

- simultaneous writes follow real filesystem ordering, and a member can see a
  sibling's partial edit;
- Git operations can contend on `.git/index.lock` or change the shared
  HEAD and index;
- cancelling stops only the selected member's turn and does not roll files
  back. After prompt admission, that member's native context becomes `LOST`,
  because the CLIs offer no portable, provable cancelled-turn boundary.
  Siblings stay usable;
- a publication snapshot can contain changes from several members.

The platform does not hide these races with a lock. Independent branches or
isolated filesystems would be a different operation, not Fork.

## 6. Concurrency and credential isolation

Correctness fences are narrow:

- one unresolved turn per member;
- one process generation and residency generation per member;
- one activation, event and cancellation stream per attempt;
- group operation references around Docker start and stop;
- one terminal-suspension token per protected turn, so terminals resume only
  after every concurrent sibling has left preparation and finalization;
- a short source-member fork transition;
- a bounded quiescent publication capture.

Members run in execution slots with separate UIDs and private tmpfs credential
paths. Siblings do not share residency or attempt grants, MCP tokens,
activation directories, process generations, event streams or cancellation
authority. A member must not read another process's credential from argv, disk,
`/proc` or reusable environment files.

This protects reading, not writing. The workspace, `.git` included, is one
writable tree. Code one member plants there (a Git hook, `core.fsmonitor`, a
build or test script) runs with the UID, environment and credentials of the
member that executes it next. Siblings belong to one owner, and the design does
not defend them from each other's writes.

Resource accounting follows the same split. Whole-container CPU, memory, I/O and
network counters belong to the group and are never copied onto a member
attempt. Each turn records a zero-coverage `shared-member-v1` aggregate plus
tool timing. Kata additionally records fork, exec, exit and settled counters
through a guest-root observer filtered by the member's UID. Non-Kata trusted
mode gives timing only.

Turn cleanup uses the root-owned, mode-0555 `member-process-reaper` in the
sandbox image. It captures a member UID's `(pid, /proc starttime)` baseline in
its own memory and takes the cleanup baseline over stdin, not from a
member-writable pidfile. Before each TERM or KILL it rechecks UID and starttime
to fence PID reuse. After escalation it rescans and fails closed if a
non-baseline process survives. A new runner installs the helper into an older
retained container that lacks it.

Public multi-tenant deployments still need the Kata, non-root, capability,
seccomp and resource boundaries from the
[security model](2026-09-01-agent-sandbox-security-model.zh.md). The group has
one network for setup, the agent and every Fork member. What it reaches is in
the [egress design](2026-09-23-agent-public-egress-design.zh-CN.md) and
AGENTS.md §7.

## 7. Lifecycle

The idle deadline comes from the group's latest real activity: the maximum
across members and sandbox-manager operations. Admitted turns and owner file,
terminal, Git, Fork and publication operations count. An idle resident process
does not.

At the deadline (1800 seconds by default) the host:

1. checks, under the group admission fence, that no turn, terminal or
   operation is active;
2. flushes native state, closes resident processes, suspends protected
   terminals, and runs `docker stop` on the fenced container;
3. stores the archive and checkpoint if the policy is enabled. An exhausted
   upload fails the clean stop instead of claiming success;
4. disconnects the container from its bridge and removes the bridge, keeping
   the writable layer;
5. reports the group as stopped on the next heartbeat.

After a sandbox manager restart the in-memory deadline is gone. The control
plane then applies the same activity and admission fences, plus a bounded
recovery window, before sending the stop command.

Any interaction with any member recreates the bridge (host rules first), starts
the same container and restores only the transports that interaction needs. A
stopped sibling is not restored eagerly. On startup the sandbox manager stops
containers whose resident ownership it cannot rebuild, before allowing a fenced
wake.

Container lifecycle and fatal container loss are group-scoped. A process
failure is member-scoped.

## 8. Context labels

| Label | Meaning |
|---|---|
| `NEW` | No settled native turn yet. |
| `LIVE` | The turn used the compatible resident process. |
| `RESTORED` | A replacement process resumed the same native session from the retained layer. |
| `LOST` | The native state or container cannot be proved intact. |

`LIVE` and `RESTORED` both have full native context. They differ only in
whether the process was reused.

HOT, WARM, COLD and REPLAY remain as diagnostic labels on older metrics and UI
rows. They do not pick an executor. HOT is a live process, WARM a native resume,
COLD a stopped container. REPLAY is never a valid recovery path.

## 9. Control plane, host and deployment

The control plane owns job, attempt, lease, group, membership, residency and
fork fencing, and routes a workspace request only to the sandbox manager of the
group's owning host. Follow-ups are pinned to that host. The admin active-host
choice only decides where a chat without a sandbox is created.

The host runner claims a turn, prepares the first checkout or imports an
approved published base, obtains attempt and residency grants, and streams the
turn through the group supervisor. The sandbox manager is the authenticated
Docker authority for files, terminals, bounded workspace commands, Git status
and diff, group and member start, stop and resume, native Fork, resident
transports, and quiescent workspace capture. Missing sandbox manager
credentials or address fail readiness and claim. Nothing falls back to a host
worktree or a GitHub snapshot.

Publication capture waits for a bounded quiet point, takes a credential-free
snapshot through the owning container, and releases the barrier promptly. It is
not a restore artifact. It may start the same fenced container if the group is
stopped.

Tables:

- `agent_sandboxes`: container identity, owning host, generation, runtime, image
  and profile pins, state, activity, idle deadline.
- `agent_thread_sandbox_memberships`: chat-to-group link, native session id,
  slot, admission, process and residency generations, context state.
- `agent_session_forks`, `agent_native_session_edges`: fork idempotency and
  session lineage.
- jobs, attempts, leases, events and publication records.

Every mutation is fenced by the relevant attempt, sandbox, activation, process
or residency generation, so a late result from a superseded owner cannot settle,
stop, publish or revoke the current one.

A usable host needs:

- an enrolled credential and fresh heartbeat;
- a sandbox manager URL the control plane can reach;
- the expected Docker data root and sandbox image digest;
- exact runtime and helper versions;
- `native-fork-at-anchor-v1` from the current host image. For each retained
  group, either that same image, or the exact CLI version the group's container
  runs plus its recorded compatible fork helper;
- at least two host slots and enough runner replicas to run siblings at once;
- the isolation configuration, and the sandbox network's host rules installed
  and read back.

Fork is offered only when the host advertises the capability and either runs the
group's image or has the recorded helper at the reviewed pin and the container's
CLI version. An unrecorded or unreviewed helper stays on the same-image rule.
Comparing digests alone would disable Fork on every deploy, because each release
labels its image with its commit. Mid-turn input needs only the host's
per-runtime capability and the same CLI version.

Names such as `shared-sandbox-v1` are capability identifiers used for claim
attestation. There are no feature switches. `AGENT_DEPLOYMENT_SHA` and
`AGENT_SHARED_LIVE_READY_FILE` belong to the rollout fence below.

### Rolling deploy

Host and control-plane jobs start in parallel. The control job builds images,
checks its database and runs a backwards-compatible migration while the old API
keeps serving. Once a host's runners, sandbox manager, tunnel, isolation
configuration and data root pass local checks, it publishes a capability that
carries the target commit. Before replacing the API container, the control
plane requires a fresh heartbeat with that commit from every host eligible for a
new chat. It also probes every online host that still owns a non-lost group.
Such an owner may stay on an older compatible generation for follow-ups. The
scheduler sends it only work for its existing groups, and creating a group still
needs the pinned fork helper. A failed gate stops the rollout.

While the marker is pending, replacement runners enroll and heartbeat but do not
poll for jobs. The previous API does not know the exact-commit claim field, so
the target API's gate alone would leave a window where the old API could assign
work to an unverified runner.

On a cold start neither the API nor the MCP relay exists. The control script
starts Postgres, migrates, then bootstraps only the missing services so a host
can enroll, send its heartbeat and prove both fixed relay paths. Web stays down
until the gate passes. On failure, cleanup stops only the services that
invocation started.

### Schema notes

`native_turn_no` is in the baseline schema with a unique partial index per
thread and a CHECK that a `succeeded` job has one. The settlement transaction
assigns it before the terminal transition, including for a turn whose native
context settled and then failed. Follow-ups are stored `queued`, and a claim
needs all earlier turns in the thread to be terminal. A successful parent also
needs a usable retained membership. If the parent's membership is gone, the
child fails instead of becoming a fresh native session.

## 10. Acceptance

The design is met when:

1. A normal chat creates one group, container, workspace, membership, native
   session and resident process.
2. Sequential turns reuse the live process. Stop and start resumes the same
   native session in the same container.
3. Each runtime passes real-binary tests at its pin: new, live follow-up,
   stopped restore, prompt-free native Fork.
4. Fork returns only after a distinct child native id and live child transport
   exist, and creates no Docker clone, mount, host directory or model request.
   Forking an old settled turn copies exactly that prefix.
5. Parent and child run at once on one workspace with no group busy error and
   no cross-routed events or cancellation.
6. Repeated Forks work within slot and resource capacity.
7. Group activity prevents idle stop. Either member restarts the stopped
   container.
8. Missing container or native state, mismatched pins, ambiguous fork recovery,
   stale generations and non-shared historical rows fail closed.
9. Docker inspection shows no chat-state bind or volume mount, and the
   configured data root is the one the sandbox manager controls. Unrelated
   groups share no Docker network, and a stopped group's bridge is removed.
10. `NEW`, `LIVE`, `RESTORED` and `LOST` match native context truth.
11. A member attempt never receives its container's aggregate counters.
    Unavailable, partial and complete process observations stay distinguishable
    on success, cancellation, timeout and boundary failure.

Real-provider traffic, Kata isolation, adversarial multi-process tests, OOM
stress and disk-pressure operations remain deployment checks.
