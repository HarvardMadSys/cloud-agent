# Independent accounts only: removing Gateway mode

Status: implemented. This decision replaces the requirement, in the [account parity table](2026-10-29-account-feature-parity.zh-CN.md) and the [accounts design](2026-10-27-independent-accounts-and-waitlist-design.zh-CN.md), to keep Gateway mode selectable.

## Decision

Cloud Agent has one account system, built as "local accounts": registration, sign-in, sessions, the waiting list and administration, all in Cloud Agent's own database. It is not a mode and nothing selects it.

Gateway sign-in, admission and inference are removed. Inference runs only on each owner's own model connections, so keys, quotas and billing stay with the provider. Any OpenAI- or Anthropic-compatible service is an ordinary provider that an owner adds with their own key.

Rule: what local mode did becomes the only behavior. The Gateway branches, the Gateway clients and the stand-ins for the Gateway in local mode (`LocalModeGateway`, `LocalModeGrantClient`) are deleted, not renamed into a compatibility mode.

## What changed

### Configuration and startup

- `AGENT_AUTH_MODE`, `local_mode()` and `auth_mode()` are gone, in the control plane and the host.
- Stale configuration is refused at startup, not reinterpreted. The process exits with an error that names the variable and points to the cutover runbook ([upgrade notes](../../developer/upgrade-notes.md#independent-accounts-cutover)) if any of these is set:
  - `AGENT_AUTH_MODE`.
  - Gateway variables: `GATEWAY_BASE_URL`, `GATEWAY_JWKS_URL`, `GATEWAY_ISSUER`, `GATEWAY_AUTHORIZE_URL`, `GATEWAY_GRANT_*`, `AGENT_GATEWAY_URL`.
  - Gateway identity variables: `IDENTITY_JWT_PRIVATE_KEY`, `IDENTITY_JWT_RETIRING_PUBLIC_KEYS`, `IDENTITY_ISSUER`, `IDENTITY_ALLOWED_REDIRECTS`.
- `AGENT_LOCAL_SESSION_SECRET` is the account system's key. `AGENT_SESSION_SECRET` remains only as the fallback root for encrypted settings, and it must differ from the account key.
- A new deployment boots with no Gateway variable, secret, URL or service, and goes straight to first-administrator setup.

### Database identity

`identity_namespace` has one source: Cloud Agent accounts. The control plane binds an empty database to it at startup and starts normally on a bound one. It refuses, with cutover instructions, a database bound to a Gateway issuer and a database that holds owner data but no binding. Nothing adopts existing owner rows, maps Gateway users to accounts or reuses Gateway signing secrets.

### Control plane

- Removed:
  - Gateway token verification, the Gateway session cookie, PKCE and login-flow keys (`identity.py`), the Gateway HTTP client (`gateway.py`), and the `/v1/session/login` and `/v1/session/callback` routes.
  - The Gateway grant client, grant minting on claims, and grant renewal and revocation.
  - Gateway TTFT and admin request detail, the Gateway model catalog and preferences, and the Gateway-grant prompt rewrite.
  - The on-call service door (`api/service_jobs.py`, `AGENT_ONCALL_*`). It ran as a Gateway service account on Gateway grants. Bringing it back needs a Cloud Agent service account with its own model connection.
- Every owner and administrator check reads the account directory (`app.state.owners`) directly. `/v1/session/me` and `/v1/session/logout` stay. Access revocation, the termination fence and the `X-Agent-Account` pin are unchanged.
- Usage reads only what each agent reported about its own model calls. `model_preference.py` remembers the model and connection of the owner's newest turn.
- PR titles use the deterministic title with no model call.
- Job admission refuses a turn that has no model connection.

### Host and sandbox

- Removed: `AGENT_GATEWAY_URL`, the `gateway-tunnel` service, the inference relay on each group bridge (and its port in the host table), and grant-based runtime environments.
- Every resident runs on its owner's model connection, through the existing public egress.
- The MCP-only relay, the control-plane tunnel, the network guard's remaining rules, Kata and the member boundaries are unchanged.

### Web, deploy and documentation

- The web app has no Gateway sign-in page, auth-mode switch, Gateway links, Gateway usage, TTFT or LLM-request admin views, Gateway model catalog, or prompt rewrite. Model connections are the model source in the composer.
- Compose files, environment examples and deploy scripts have no Gateway services or variables.
- Gateway contract mirrors that nothing consumes are removed from `gateway_contracts/`. This service's own API contract stays.

### Schema

There is no destructive migration. Tables that only Gateway paths wrote (attempt grants, grant usage snapshots) stay in the schema, unused. A later, separately reviewed migration can drop them.

## Cutover

A database bound to a Gateway issuer is refused by design. The non-destructive path is operator-run:

1. Keep the existing database untouched as an archive.
2. Point the new release at a fresh database and run `alembic upgrade head`.
3. Start the release. The setup code appears in its log.
4. Create the first administrator. The second administrator registers, and an administrator grants them administration.
5. Users re-register.

Keeping old data active would need a separate identity migration that proves each mapping. That migration does not exist and nobody chose it.

Mail uses the existing SMTP service through `AGENT_SMTP_*` with STARTTLS on 587. `AGENT_WEB_BASE_URL` points at Agent, so reset and verification links open Agent.

## Invariants

- No production code reads `AGENT_AUTH_MODE` or branches on a Gateway.
- The control plane starts on an empty database with no Gateway variable, and refuses the stale configuration and databases listed above.
- Tasks, follow-ups, the waiting queue, goals and Fork run only on model connections.
- Hosts need no Gateway URL or tunnel.
- Account ownership, admission, session and security checks keep their full strength.
