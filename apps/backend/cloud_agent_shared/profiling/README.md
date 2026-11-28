# Operation profiling

Producers import `cloud_agent_shared.operation_profiling`, the stable facade;
its `finish` closes a measurement and queues its frozen record. This package
holds the implementation:

| Module | Responsibility |
| --- | --- |
| `schema.py` | Environment names, wire headers, limits, operation boundaries and process identity |
| `models.py` | Run and control descriptors, measurement state and span payloads |
| `runs.py` | JSON run validation and immutable catalogue manifests |
| `policy.py` | Runtime control snapshots, environment caches and per-run admission budgets |
| `measurement.py` | Root selection, attribution, timing, fragment accounting and record freezing |
| `stages.py` | Local stage observers and duration-only recorders |
| `context.py` | Selection handoffs, continuations and response headers |
| `export.py` | Bounded background queue, structured logs and optional JSONL delivery |
| `clock.py` | Clock access through the facade's replaceable `time` object |

Dependencies point one way: runs build on schema and models, policy on runs,
measurement on policy, and observers and contexts on measurements. The
exporter takes serialized records and depends on neither policy nor
measurement state.

## Behavior to preserve

- Collection is off by default; an unselected root operation allocates
  nothing and reads no clock.
- Control policy is one versioned snapshot, so an older answer cannot restart
  a stopped experiment. Environment descriptors are parsed once per process.
- A measurement keeps the run and target that selected it. Stopping a run
  ends new selection, and its deadline truncates collection in flight.
- Root selection takes one per-process budget slot; discarding unselected
  work returns it, and continuing a parent's selection takes none. Continuous
  Admin runs have no cumulative sample cap.
- `bind` proves every configured target fact; a scoped measurement that was
  never bound produces no record.
- Only durations cross independent monotonic clocks. Fragment call IDs tell
  retries, missing answers and truncated answers apart.
- Finalization freezes the end and completeness once, and response metadata,
  persistence and export reuse that payload.
- Logs and JSONL are written on the background thread. A full queue drops and
  counts records; it never blocks the measured operation.

The collector, runtime-control, shutdown and overhead tests cover these:

```bash
uv run pytest -q tests/unit/test_agent_operation_profiling.py \
  tests/unit/test_operation_profiling_runtime_control.py \
  tests/unit/test_operation_profiling_shutdown.py \
  tests/unit/test_operation_overhead_benchmark.py
```

To replace a helper in a test, patch it in the module that uses it, such as
`cloud_agent_shared.profiling.policy.parse_run`; rebinding the facade's
import does not reach other modules. The facade's `operation_profiling.time`
is the one clock hook every module shares.

Field definitions and operation boundaries are in
`docs/agents/specs/2026-09-14-sandbox-startup-measurement-design.zh-CN.md` and
`docs/agents/specs/2026-09-14-platform-operation-measurement-inventory.zh-CN.md`.
