# Security

## Reporting a vulnerability

Report vulnerabilities through GitHub's private vulnerability reporting for
this repository: **Report a vulnerability** under the **Security** tab. Do not
open a public issue for anything you believe is exploitable.

Expect an acknowledgement within a few days. We will tell you what we found,
what we changed, and when the change shipped.

## What is in scope

- The control plane, web app, runner host, and sandbox image in this repository.
- The account system, its sessions and its waiting list.
- The committed API contract, `apps/backend/openapi.json`.

A model provider that owners connect is not part of this service; report a
provider's issues to that provider.

## What a deployment must not assume

The threat model is in [AGENTS.md](AGENTS.md#7-security-boundaries--do-not-relax-without-review)
and the [operations runbook](docs/developer/operations.md). Two requirements
matter most:

- The plain Docker runner profile isolates trusted repositories from each
  other, not hostile code from the host. Do not offer it to untrusted users.
- The sandbox never holds a platform or administration credential. It holds
  the owner's own provider key for the turn it runs, and short-lived scoped
  tokens. On a model the deployment provides it holds no provider key at all,
  only a placeholder that the control plane's model relay accepts while that
  turn runs. Putting an admin token, or the deployment's own provider key, into
  a sandbox removes the security property this design rests on.
