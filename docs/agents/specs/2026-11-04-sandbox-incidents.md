# Sandbox incidents

Status: implemented.

## Problem

Things that go wrong inside a sandbox used to leave no usable trace. A process
killed by the out-of-memory killer cannot log its own death, and the resource
sampler takes one reading every 15 seconds, so spikes between readings are
invisible. The browser service and the MCP gateway log inside the container,
where the logs vanish with it and cannot be queried across sandboxes.

The platform now records a small set of incidents per sandbox in
`agent_sandbox_events`, the table the admin sandbox page already shows. It adds
no new service (no Prometheus, Grafana or log store).

| Reader | What they get |
| --- | --- |
| Operators | Every incident per sandbox on the admin page, and resource peaks |
| The user | One plain line in the turn it happened in, for incidents that break a turn, only in the chat it concerns |
| The agent | At its first turn, that the sandbox has a browser and how to reach it. At later turns, what happened since it was last told |

## What is recorded

An incident has a scope. Sandbox scope covers the whole container; the browser
is one per sandbox, shared by a chat and its Forks. Member scope covers one chat:
MCP programs run per member slot, and one member's failure must not be reported
to another.

A slot is reused after its member leaves, so ownership is fixed when the failure
happens. When the slot's runner starts an MCP server, it records the digest of
the credential of the member it starts it for, and journals the server's start
failure or exit under that digest. The host admitted that credential, so it
knows whose it is and attaches that `thread_id`. Runners key servers by member:
when a slot passes to another member or is revoked, the previous member's
servers are withdrawn and stopped. A failure whose credential the host never
admitted, or forgot in a restart, belongs to nobody and is not recorded.

| Kind | Scope | Detected by | Told to user | Told to agent |
| --- | --- | --- | --- | --- |
| `out_of_memory` | sandbox | Docker `oom` event | yes | yes |
| `container_exited` | sandbox | Docker `die` event nobody asked for, with exit code | yes | yes |
| `memory_high`, `pids_high` | sandbox | A 15-second reading above 90% of the limit; re-armed below 80% | no | no |
| `browser_crashed` | sandbox | Chromium exited with no stop requested | no | yes ("open pages are gone") |
| `mcp_server_failed` | member | MCP runner, phase `start` (could not start, or exited or timed out before answering `initialize`, including a package that does not install) or `crashed` (exited after starting, no stop requested) | yes | yes |

A stop the platform asked for is not an incident. The MCP runner records a
reason with every deliberate stop (idle timeout, admission withdrawn, launch or
keys changed, shutdown), and only an exit with no requested stop is `crashed`.
The browser service does the same for idle stops and `stop`. For containers,
every stop, idle stop or deletion goes through Docker's `kill`, which Docker
reports before `die`. A `die` within two minutes of a `kill` for the same
container is not an incident.

Incidents carry no content: no stderr (it may hold keys), URLs, command lines or
environment. A row has its kind, scope, `thread_id` when member-scoped, the
observation time, and a few typed fields (exit code, signal, limit, peak, MCP
alias, phase).

### Repeats and caps

`agent_sandbox_events` is append-only (a trigger refuses `UPDATE` and `DELETE`),
so every row is final when the host creates it.

- The first occurrence of a kind for a subject (the sandbox, or thread plus
  alias) goes out at once.
- Repeats within the next 60 seconds are counted on the host and sent when the
  window closes as one `incident.<kind>.repeated` row with `count` and the
  window's start and end. A window with no repeats sends nothing.
- A sandbox records at most 60 incident rows an hour, summaries included. Past
  that the host only counts, and sends one `incident.dropped` row with the total
  at the end of the hour.

Row ids are deterministic: `<source>:<subject>:<seq or window start>[:repeated|:dropped]`.
A resent row is the same row, and the unique index makes the replay a no-op.
Docker replays recent events after a reconnect (`since`) and a journal line can
be read twice, so the host keeps a bounded set of source ids it has counted and
counts each source event once.

## How an incident travels

```text
Docker events ─────────────┐
                           ├─▶ host outbox (per sandbox) ─▶ heartbeat ─▶ agent_sandbox_events
in-sandbox journals ───────┘                                (incidents)   (event_type = incident.<kind>)
```

- **Docker events.** The host holds one long-lived `/events` subscription
  filtered to `type=container` and `event=oom|die|kill`. It reconnects with
  backoff and asks Docker to replay from the last event seen, so a gap still
  finds an exit.
- **In-sandbox journals.** The browser service writes
  `/run/agent-browser/events.jsonl` and each slot's MCP runner writes
  `/run/agent-mcp-slots/<slot>/events.jsonl`. Lines are `{seq, ts, kind, ...}`,
  and each journal is capped at 256 KiB, dropping the oldest lines. Journals are
  mode 0640, group `agent`. The host reads them as root in one exec per sandbox,
  from the last `(journal, seq)` it holds. Nothing in the sandbox opens a port.
  It drains every 60 seconds while the container runs, so a browser that crashes
  between turns is recorded before the next turn, and whenever a Docker incident
  arrives for that container. An MCP program usually fails when a turn first
  lists its tools, so its row is normally stored while that turn still runs.
- **Outbox.** The sandbox manager keeps at most 64 pending rows per sandbox,
  dropping the oldest and counting the drops into the next `incident.dropped`.
  The runner carries them in the heartbeat's `incidents` field. The control
  plane answers `incidents: "accepted"` only when every row was stored, and only
  then does the runner tell the sandbox manager to forget them.
- **Heartbeats are never refused.** An incident the control plane cannot parse
  is dropped, and an unknown kind is ignored.

Resource peaks come from the same path. The collector hands each 15-second
reading to the incident observer with the container's memory and pids limits,
and a reading at 90% becomes `memory_high` or `pids_high`. Shorter spikes are
still missed. The `out_of_memory` event covers the spike that matters. A true
peak (cgroup v2 `memory.peak`, `pids.peak`) is not collected.

Hosts roll out before the control plane, and each side supports the previous
release. A new host talking to an old control plane keeps its rows in the
bounded outbox because the old side never answers `accepted`. A new control
plane talking to an old host receives no incidents.

## Telling the user

For `out_of_memory`, `container_exited` and `mcp_server_failed`, the control
plane appends a `lifecycle` event `{phase: "sandbox_incident", ...}` to the
running turns the incident concerns, in the transaction that stores the row. If
the notice cannot be written the row rolls back, so the heartbeat is not
confirmed and the retry stores and notifies again. A stored row was always
notified, so a replay notifies nobody twice. Appends are fenced by each
attempt's live lease. A sandbox-scoped incident goes to every turn running in
that sandbox, and a member-scoped one only to that member's own running turn.

| Kind | Line shown |
| --- | --- |
| `out_of_memory` | "The environment ran out of memory, and some processes were stopped." |
| `container_exited` | "The environment stopped unexpectedly. It starts again with your next message." |
| `mcp_server_failed`, `start` | "*alias* could not start, so its tools were not available." |
| `mcp_server_failed`, `crashed` | "*alias* stopped unexpectedly. It starts again when it is next used." |

Limits, exit codes and advice are for operators on the admin page. A turn's
failure message does not change.

## Telling the agent

Two prompt producers in `claim_prompt`
(`apps/backend/cloud_agent/application/prompt_injections.py`):

- `sandbox_browser`: at a chat's first turn (`turn_no` 1), and only when the
  claiming host reports the `sandbox-browser` capability, which it does when its
  sandbox image carries the browser helper label. It states facts and names no
  tool: the sandbox has a Chromium browser shared with the chat's forks,
  `cloud-agent-browser start` prints its CDP WebSocket URL, and screenshots and
  downloads belong under `/workspace/.cloud-agent-artifacts`.
- `sandbox_incidents`: at a turn whose thread has untold, agent-relevant
  incidents. At most five lines, newest first, times in UTC. For example: "Since
  you were last told: the sandbox ran out of memory at 15:02 and some processes
  were stopped. `playwright` could not start."

A claim never waits on the host. It reads what is recorded. An incident that
happens within about a minute before a claim (one drain plus one heartbeat) can
miss that claim and is told at the next.

### The cursor

What a thread has been told is a per-thread cursor, not a time.
`agent_thread_incident_cursors.told_through` holds the highest
`agent_sandbox_events.id` told to that thread, and the claim records what it
would tell through against its attempt in `agent_attempt_incident_marks`.

- The claim selects the sandbox's agent-relevant incidents with an id above the
  cursor: sandbox-scoped ones, and member-scoped ones carrying this thread's
  `thread_id`. It renders at most five and records the highest id it selected as
  `through_id`. If more than five exist, `through_id` still moves past all of
  them and the note says "and *n* more".
- The cursor advances to `through_id` only when that attempt's `turn_started`
  event is stored, which the runner writes after the sandbox manager dispatched
  the prompt. It rides that event's lease fence and never moves backwards. A
  claim whose prompt never went out advances nothing, so the retry tells the
  same incidents again.
- A cursor starts when its chat joins a sandbox, through a trigger on the
  membership (at insert and when it moves sandbox), at that sandbox's newest
  incident or zero. So a chat's first incident is told even if its first turn
  had none, and a Fork is not told its parent's past.

Ids order insertion, not observation, which makes late incidents safe: they get
a higher id than anything already told, so the next claim tells them. The note
sorts what it tells by observation time.

## Admin page

The sandbox trajectory lists `agent_sandbox_events` with a label and details line
for each `incident.*` kind, including `repeated` and `dropped`, and the thread
for member-scoped ones. The sandbox list shows "last incident", the newest
within 24 hours.

## Not in scope

- Alert routing. The incidents table is what an alert rule would read.
- Fleet metrics (Prometheus, Grafana, node_exporter). cAdvisor is not planned:
  on Kata it reads the host cgroups, which are empty.

## Open questions

1. Whether Kata delivers `oom` events to Docker on our guest kernel and cgroup
   version. If not, the guest observer would read the guest's `memory.events`
   `oom_kill` counter instead.
2. The claim cannot yet tell that a native session will start fresh after the
   first turn, so the browser note is not repeated in that case.
