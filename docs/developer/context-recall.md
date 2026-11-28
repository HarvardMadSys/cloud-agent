# Native conversation recall

Each chat's native CLI session lives in its retained sandbox. The CLI owns the
conversation history; Cloud Agent passes each user message through unchanged
and never replays transcripts, rewrites JSONL or adds a continuation reminder.

When an answer seems to forget earlier turns, ask two separate questions: did
the same native session survive, and did the model answer correctly from the
history it was sent? An investigation in September 2026 found both kinds of
failure: bugs in native session persistence, and models claiming history was
missing when the request carried all of it. The session architecture is in the
[native Fork and shared workspace design](../agents/specs/2026-08-31-native-session-fork-shared-workspace-design.md).

## Context status

| Context status | Meaning |
|---|---|
| `NEW` | No native turn has previously settled for this member |
| `LIVE` | The turn uses the compatible resident CLI process |
| `RESTORED` | A replacement process resumes the retained native session |
| `LOST` | Required native state or container integrity cannot be established; execution is refused |

Stopping a container keeps its writable layer. Losing the container or the
Docker data root loses the native state, unless the group was stopped cleanly
with a current
[restorable checkpoint](operations.md#restoring-a-removed-hosts-sandboxes),
which another host can restore once this one is removed, terminates or moves
out before its lease ends.

## Session identity

- After each turn settles, including one that settled normally after an API
  failure, the runner checks the native session ID before saving it. A
  continuation that reports a different ID fails the turn before it can
  replace the saved locator.
- When a launch omits the locator, the sandbox manager uses the member's saved
  one, rejects a conflict between the requested and observed locators, and
  checks again at settlement. The runner rejects a retained chat with no
  resume locator.
- The Claude driver checks root stream events before normalizing them, so a
  child agent's events (`parent_tool_use_id`) cannot replace the root session
  ID. The other adapters validate their own resume handshakes.

## Failed turns

All five CLIs keep the native session after a failed turn, so a follow-up can
continue it, but only when the native turn verifiably ended, activation
cleanup completed, and the transport and native locator are still usable. A
command rejection, dead transport, timeout or cancellation is not settlement.

| CLI | Native settlement evidence |
| --- | --- |
| Claude Code | `result`, including its `is_error` value |
| Codex | `turn/completed` for the active native turn ID |
| Pi | `agent_end`; the last assistant's `stopReason` determines failure |
| OpenCode / Kilo | Bridge `settled` for the active turn at native idle |

Pi reports terminal API errors as failures, not as empty successful turns.
OpenCode and Kilo collect only assistant-owned text, by message role, so user
text never appears in the assistant's output.

The pinned Claude Code CLI (2.1.220) has a reproduced defect: on a later
resume, a delayed `system/api_error` can bypass an assistant reply that
succeeded on retry. So when the driver sees `system/api_retry`, it settles the
turn, saves the locator and gracefully retires the process; the next turn
resumes the same session in the same container once the history is flushed.
If the exit cannot be confirmed, the turn still settles and the next launch
reaps the member's slot first.

Host attestation requires the sandbox's `http-agent-live-bridge=5` helper.
The helper gates each turn on its live SSE subscription and aborts the native
turn if that stream is lost. It answers permission requests automatically for
OpenCode and Kilo, including their Task child sessions, and for the Context
view it reads what each turn's first request carries, dropping the read if it
is slow, large or late. Deploy the runner, Sandbox Manager and sandbox image
together; an older helper image must not advertise readiness for the new host.

## Known limits

Nothing repairs a transcript that older behavior already damaged or migrates
old disposable chats; start a new chat when legacy history has no native
shared-live membership. A successful HTTP response does not prove recall, and
neither does a persisted session. Two findings lie outside this repository:

- Flash (`deepseek-v4-flash`) can claim that history is missing even when the
  outbound messages are complete. Neither reasoning effort nor newer CLIs
  reliably fixed it, and client-side evidence cannot tell whether the engine,
  chat template or model is at fault.
- The tested gateway snapshot drops the effort configuration and earlier
  thinking blocks when it translates a request. Patches outside this
  repository were not verified on a deployment and do not show that Flash
  recall is fixed.

## Verification before accepting a deployment

1. Require a green **CI Gate**. It runs the pinned native CLI request-history
   regressions (exact user and assistant text, roles, order and repeats across
   live follow-ups, close and restore, failed-call retries and internal
   retries) and the Sandbox Manager and runner checks that reject a changed
   locator. The loopback fixture needs no inference credentials.
2. Check the exact host and API SHA, the reverse Sandbox Manager probe and a
   fresh heartbeat ([deployment reliability](deployment-reliability.md)). A
   working model call does not prove the intended host revision serves jobs.
3. Run the opt-in behavior preflight from the target checkout against the
   provider the deployment's owners use, with a test key you hold:

   ```bash
   CLOUD_AGENT_TEST_API_KEY_FILE=/path/to/test-key \
     uv run python -m deploy.preflight.check_model_context \
     https://provider.example.com --repetitions 3
   ```

   `CLOUD_AGENT_TEST_PROXY` can name an HTTP proxy. The key stays in the test
   process. Each repetition sends five synthetic conversations to
   `deepseek-v4-flash`, so the default makes 15 ordinary, billed calls. The
   check fails on a non-200 response, a stream error, truncation, missing
   facts or a recognized missing-history claim, and also requires thinking
   output for the `adaptive/high` settings it requests. It reports
   `recall_passed` and `thinking_observed` separately. Read each failure: the
   detector may reject a reply that quotes a wrong claim to correct it.
4. In the deployed app, check a new chat, a live follow-up and an idle restore
   keep the same retained sandbox and native locator. If an answer reports
   lost context, compare the outbound request history and settlement evidence
   before blaming persistence.

No deployment workflow runs the preflight, and CI has no inference key.
Repeat it when model routes, the model deployment or CLI pins change.

Code and checks:
[`shared_live_attempt.py`](../../apps/backend/cloud_agent_host/turn/shared_live_attempt.py),
[`runtimes/claude/session.py`](../../apps/backend/cloud_agent_host/runtimes/claude/session.py),
[`shared_session_supervisor.py`](../../apps/backend/cloud_agent_host/supervisor/shared_session_supervisor.py),
[`shared_protocol.py`](../../apps/backend/cloud_agent_shared/shared_protocol.py),
[`check_model_context.py`](../../deploy/preflight/check_model_context.py) and
[`test_claude_driver_context.py`](../../tests/external/test_claude_driver_context.py).
