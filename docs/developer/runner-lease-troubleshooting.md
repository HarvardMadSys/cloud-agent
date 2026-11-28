# Runner lease failures

A retained sandbox can outlive the lease of the worker driving its turn. Start
from the attempt records and runner logs: a container that still exists does not
prove that its native session can safely continue.

## Lease timing and retries

| Setting | Default | Purpose |
| --- | --- | --- |
| Attempt lease | 120 seconds | Fences the worker's authority to write. |
| Heartbeat interval | 30 seconds | Renews that authority on a separate thread. |
| Heartbeat HTTP timeout | 10 seconds | Limits each renewal request. |
| Retry delay after heartbeat failure | 5 seconds | Retries while the current lease remains valid. |
| Agent execution timeout | 3,600 seconds | Limits the turn itself. |
| Ordinary control RPC timeout | 30 seconds | Default for other control requests. |

Renewal does not depend on model output, tool activity or progress events, so a
quiet call can span many lease periods. The first heartbeat goes out as soon as
the turn's process starts, because the lease runs from the claim. With repeated
ten-second read timeouts, retries start at 0, 15, 30 … 105 seconds, which leaves
chances to reconnect before a 120-second lease ends but guarantees nothing; an
outage longer than the remaining lease loses it. A successful heartbeat restores
the normal interval.

The runner stops the agent, so that it cannot write to the shared workspace
after the reaper has superseded the attempt, when:

- a heartbeat answers HTTP 409: the lease is gone for good;
- the control plane refuses the worker credential (HTTP 401, 403 or 404, which
  no retry changes);
- its last acknowledged renewal is a whole lease old.

A lease must therefore outlast one heartbeat interval plus one request timeout
(40 seconds): a shorter `AGENT_LEASE_TTL` is ignored in favour of the default,
and a shorter `--lease-ttl` is refused. A cancellation does not stop renewal;
the lease is kept while the runner releases or drains the turn.

## Diagnose from positive evidence

Tell a renewal timeout apart from an execution timeout, a cancellation or the
agent exiting. `lease_expired` proves only that the database saw no timely
renewal. Missing error events prove nothing: the outage that blocks renewal can
block error reporting too. Use these log records:

| Record | Evidence |
| --- | --- |
| `agent_attempt_claimed` | Job, attempt, worker and host identifiers. |
| `agent_control_failure` | Failed operation/stage, job ID, exception class and HTTP status when available. |
| `agent_heartbeat_retry` | Consecutive failures, time since the last acknowledgement (or heartbeat start), and next retry delay. |
| `agent_heartbeat_recovered` | Renewal succeeded after failures. |

They carry no request URLs, tokens, response bodies or exception messages, and
the exception class and status are logged once per outage. Correlate them with
the attempt's `lease_owner`, `lease_expires_at` and event timestamps. Collect
runner and control-plane logs outside their containers: replacing a container
can delete its Docker logs.

An `attempt_superseded` event does not mean another attempt ran. A retained
session can become lost without a replacement, for example when activation
ownership was uncertain during preparation. The UI shows another attempt only
when its events exist.

## Reporting and acknowledgement failures

Event upload and the terminal `finish` request are independent: the runner
attempts `finish` even if uploads fail, and the reaper is the last backstop.

The turn loop numbers, redacts and queues each event, and one sender thread
posts the queue in order to `POST /v1/agent/worker/jobs/<job>/events:batch`, up
to 100 events or 256 KiB per request. A batch is retried whole for up to 60
seconds on transport failures and HTTP 408, 429, 500, 502, 503 and 504, with
delays doubling from one second to eight; the event numbers make the retry safe.
Past 1,000 queued events or 4 MiB the turn loop waits, so a CLI whose events
cannot be recorded stops being read. A 409 ends the turn as a refused heartbeat
does, and if the sender gives up, the turn fails before it settles. `finish`,
the release and terminal suspend and resume wait for the events queued before
them. A control plane that answers the batch route with 404 or 405 predates it:
the runner logs `agent_event_batches_unsupported` and posts that attempt's
remaining events one at a time to `/events`.

A failure in post-claim preparation, such as image inspection or manifest
validation, still attempts a terminal failure before heartbeat renewal stops,
unless the lease was rejected. A failed upload of the final
`shared_workspace_settled` notice does not fail a turn that the supervisor and
control plane both settled.

The control plane's admission and settlement receipts are retried **for as long
as the lease holds**, with ten-second timeouts and delays doubling from one
second to eight, which rides out a recreated control-plane container. A turn the
control plane admitted but never saw settle is marked LOST, even if the
supervisor settled it. Retries stop when the heartbeat loses the lease, or after
a whole lease of failures from a control plane that keeps renewing it; a failed
receipt never tears down a session the supervisor settled. Supervisor
settlement gets three attempts, five seconds apart. Only transport failures and
HTTP 408, 502, 503 and 504 are retried; 409 and 410 are final. The prompt is
sent exactly once, after its admission acknowledgement.

Repeating a settlement returns the recorded boundary for the same live attempt
and activation, without moving the workspace epoch or the native turn ordinal. A
sandbox manager restart loses its in-memory receipt, so an uncertain completion
across a restart fails closed.

## When a retained conversation can continue

A reported preparation failure **before admission** leaves the conversation
usable: the runner abandons the activation and reports a failed turn, and the
next message resumes the retained native session. That needs a known retained
container; a failure before its identity was recorded gives no such proof.

An admission request with no acknowledgement is different. The database may have
committed it although the prompt was never sent, so the membership records the
job as admitted. `abandon` only releases pre-admission turns
(`admitted_job_id IS NULL`), so the terminal transition marks that context LOST.
The runner never replays an unconfirmed admitted prompt.

Recovering after an expired activation or a killed runner would need a further
protocol: quarantine the member, fence the old runner's preparation commands and
sandbox manager token, verify the exact container and native turn boundary, then
resume the session or report it unrecoverable. A sandbox manager `busy=false` is
not enough, because workspace setup commands run outside its agent-turn
transport. Never requeue a turn just because its admission field is empty, clear
a lost flag, or replay an admitted prompt to repair what the UI shows.

Owners see plain-language messages for interrupted responses and unavailable
conversations; infrastructure details stay in API records and operator logs.
LOST memberships stay disabled.

## The safe release case: a child never accepted its turn

The host runs each turn in a child process. It releases a claim back to the
queue only when it can prove that the child never accepted the turn: the process
could not be created, the payload could not be delivered, or the child closed
its handover descriptor without accepting, for example after a failed read-only
Docker or network preflight. A silent child that is still alive is never
released. After acceptance, crashes, kills and lost acknowledgements belong to
the lease protocol, and the conversation may become lost rather than repeat the
turn.

The release is `POST /v1/agent/worker/jobs/<job>/release` with the turn's worker
token. The control plane requires the same running attempt and lease generation
**and** no membership that admitted the job. The database statement enforces
that, so a wrong host observation cannot requeue admitted work.

Consecutive launch failures stop claims on that host and back off. Heartbeats
report `launch_error`, which appears beside the host on Admin → Servers.
