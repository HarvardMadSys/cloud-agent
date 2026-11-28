# Reasoning levels on owner model connections

Status: implemented. Checked against local fakes and the pinned Claude Code 2.1.220 and Codex 0.145.0. OpenCode, Kilo and pi rely on their earlier pinned captures. No real provider or Kata host has been exercised.

This design replaces the earlier rule that a connection has no reasoning effort. See [the model connection design](2026-10-27-user-model-connections.zh-CN.md) and [the protocol compatibility design](2026-10-28-model-protocol-compatibility.zh-CN.md). The per-runtime CLI mechanisms and wire shapes in `tests/fixtures/agent_runtime_streams/effort_wire_shapes.md` still apply.

## Problem

A turn on a connection used to run at the provider's default. The composer offered no level, `require_usable` refused any level, and the translator dropped the level the CLI sent. No reliable capability table existed. Owners want the levels the upstream supports, and want a chosen level to reach the upstream.

## Decision

The evidence is the provider's own declaration, per model and per API. The platform reads two documented listing formats with the owner's key, under the same address rules as verification.

| Listing | Field | Describes |
|---|---|---|
| `GET <Base URL>/models` (bearer) | `data[].reasoning.supported_efforts` (OpenRouter's field; the HybridInference gateway publishes it too) | Chat Completions `reasoning_effort` and Responses `reasoning.effort` at that Base URL |
| `GET <Anthropic base>/v1/models?limit=1000` (`x-api-key`, `anthropic-version`) | `data[].capabilities.effort` (Anthropic Models API; the gateway publishes it too) | Anthropic Messages `output_config.effort` |

For each model and API the platform stores one of:

- `supported`, with the levels.
- `unsupported`: the provider declares none (`supported: false`, `supported_efforts: []`, or a `reasoning` object without `supported_efforts`).
- `unknown`, with a reason: `not_read` (verified before this feature), `listing_unavailable`, `not_listed`, or `not_declared` (no field, or a malformed one).

`supported_efforts: null` is OpenRouter's "every value accepted". It maps to the documented set, minus `none` when the model is `mandatory`. The platform never infers levels from a model name or assumes a default. An accepted request is not evidence, because a provider may accept a parameter and ignore it.

### When the platform reads it

- **On every verifying save.** At most two listing requests, no inference, inside the save's reservation and deadline. A failed listing never fails the save.
- **On Refresh reasoning levels** (`POST /v1/agent/model-connections/{id}/refresh-efforts`). This is how connections saved earlier get their levels. It is limited to one check per hour, uses the saved key at the saved Base URL, and writes only the effort facts of models verified at the current state. It is guarded like an edit (409 if the connection changed) and closes no process.

### What the composer offers

For a connection, model and runtime, the composer offers Auto plus the declared levels of the plan's upstream API (Chat Completions on a translated plan). The levels are narrowed to the runtime's verified CLI vocabulary (`runtime_catalog`) and keep the runtime's order. `/config` returns this per model and runtime as `model_efforts`, with a status and reason, so the composer can explain why only Auto is offered. Unknown and unsupported both offer Auto only, with different explanations.

### Validation

`require_usable` refuses a level outside the offer. It covers admission, follow-ups, queued messages, goal rounds, Fork, claims and residencies. The errors are `effort_unknown`, `effort_unsupported` and `effort_level_unsupported` (with `offered_efforts`). A follow-up inherits its parent's level, like its model, and the effective level is checked. `"auto"` clears it. The platform never renames, rounds or drops a level.

### Wire

- **Direct plans** use the per-pin CLI mechanisms: `--effort`, `-c model_reasoning_effort` and the `turn/start` effort, `--thinking`, `options.reasoningEffort`.
- **Translated plans.** The translator receives the turn's level at launch (`AGENT_COMPAT_REASONING_EFFORT`, absent at Auto) and sends it as `reasoning_effort` on every Chat request. It ignores the CLI's own field, because Claude Code sends `output_config.effort: high` at Auto.
- A level change restarts the member process, since the level is part of the resident launch identity. The translator's binding is never stale.
- At Auto, launches are byte-identical to launches without this feature.

## HybridInference gateway side

The gateway's model listings publish, per model, the levels every route delivers. They come from configuration only:

- An `OpenAICompatAdapter` route is the only adapter that forwards `reasoning_effort`. With the parameter in `supported_params`, the route delivers its declared `reasoning_efforts`. Without it, the list is empty.
- Any other adapter, the parameter without a domain, or a malformed domain makes the route unknown.
- One unknown route makes the model unknown. Auto models publish nothing.

A contract test sends every advertised level through `/v1/messages` and checks that the upstream receives it. The golden listings are in `tests/fixtures/gateway_model_effort_listing/`.

## Limits

- A provider that declares nothing (OpenAI's own `/models`, most self-hosted servers) stays unknown, so only Auto is offered.
- An OpenAI-style declaration applies to both OpenAI APIs at that Base URL, because neither format distinguishes them.
- The gateway publishes only what its operators configured. Until an operator declares levels for a model, that model stays unknown.
