# Signing in at a model provider for a key

- **Date**: 2026-11-28.
- **Status**: implemented, in the same pull request.
- **Builds on**: option 5, "Connect *Provider*", in [operator-provided model connections](2026-11-09-operator-provided-model-connections.md). That document listed it as a later improvement to bring-your-own-key.

## Decision

An owner with no model connection should be able to get one without copying a key. The motivating case is FreeInference: an owner creates a provider account, or links the one they have, and is back with a working connection.

The deployment names one provider, and the owner signs up or signs in **at the provider**. The provider hands this service a one-time code, which it exchanges for an API key on the owner's own provider account. The key is saved as an ordinary owner connection and verified like any other. The contract is OpenRouter's PKCE key flow: a provider that implements it needs nothing specific to Cloud Agent.

The owner's own key and the owner's own bill are kept exactly as before. Nothing about the account system, admission, turns or the sandbox changes.

**Not chosen: creating the provider account server to server.** Cloud Agent would assert the owner's verified email to the provider with a provisioning credential, and the provider would create or match an account. That is an identity mapping between the two account systems, which the [account import](2026-11-05-freeinference-account-import.zh-CN.md) §7 declined. It is also a credential this service would hold. And matching an existing account by email lets whoever controls a Cloud Agent email take a key on the provider account with that email. With PKCE, the provider's own sign-in proves who the owner is there, and the provider's consent page asks them.

## The contract

1. **Authorize.** The browser is sent to the configured authorization page with `callback_url`, `code_challenge` and `code_challenge_method=S256`:
   - `code_challenge` is base64url(SHA-256(verifier)), without padding.
   - The callback is `<web app URL>/settings/models/connected?state=<state>`. The state rides in the callback's query, so the provider only appends `code`.
   - The provider signs the owner up or in. It names the app by the callback's host, and asks them to approve a key.
   - On approval it redirects to the callback with `code=<one-time code>` added. On refusal it adds `error=access_denied`.
2. **Exchange.** The control plane makes a JSON POST of `{"code", "code_verifier", "code_challenge_method": "S256"}` to the configured key exchange URL. The answer is `200 {"key": "<API key>", ...}`; other fields are ignored.
   - A 4xx other than 408/429 is the provider refusing this code.
   - Anything else without a key is an outage.
3. **Provider obligations**, which this service relies on but cannot check:
   - codes are single use and short-lived;
   - the challenge is verified;
   - callbacks are restricted to an allowlist the operator registers;
   - the key belongs to the account that approved it.

## What Cloud Agent does

- **Settings.** Five Admin settings in the `Model sign-in` category, on the Models tab:
  - the provider's name;
  - the authorization page URL;
  - the key exchange URL;
  - the inference Base URL;
  - up to sixteen model ids.

  All are required, and any one empty turns the feature off. Each is held to the rule the flow applies to it: HTTPS without query or fragment for the two URLs, the connection Base URL rule, and the model id rule. None has an environment variable.
- **Offer.** `GET /v1/agent/model-connections` (`sign_in`) and `GET /v1/agent/config` (`model_sign_in`) carry `{name, base_url}`. They do so while the feature is configured, model connections are on, the web app URL is set, and the owner has no connection with that Base URL.
- **Start.** `POST /v1/agent/model-sign-in` stores a single-use state in `agent_oauth_states` (provider `model-sign-in`, ten minutes). The state is bound to the owner, and holds the verifier encrypted under its own domain of the user-credential key. The route answers the authorization URL.
- **Finish.** `POST /v1/agent/model-sign-in/finish` runs these steps in order:
  1. Consume the state for the same owner.
  2. Re-check the settings and the owner's connections, which refuses if one with the Base URL appeared meanwhile.
  3. Name the connection after the provider, numbered if the name is taken.
  4. Skip models already on another of the owner's connections. If none is left, refuse.
  5. Reserve one hourly check per model.
  6. Only then exchange the code. The exchange dials the address the egress policy checked, with no redirects and no proxy, a 30-second bound and a 64 KiB answer limit.
  7. Verify the models with the new key, and save those an agent can use.

  The answer is the connection and the models left off.
- **Callback page.** `/settings/models/connected` submits once, even under Strict Mode. A spent state is not resent after Back or a refresh. The `error` parameter is never displayed as sent. After success, the page returns to where the owner started, or to Models.

What was saved cannot be told apart from a connection typed by hand: the key is write-only, and claim, residency, invalidation, deletion and usage are unchanged.

## AGENTS.md § 5

Section 5 forbids Gateway sign-in, admission, grants and catalogs. This flow is none of them, provided that:

1. It only obtains the owner's key. It signs no one in to Cloud Agent and sends the provider no Cloud Agent identity (no email or account id). The provider learns only that some app on the callback host asked for a key.
2. Admission never depends on it. A provider that is down or refuses blocks neither sign-in to Cloud Agent nor adding a connection by hand.
3. No turn's start or continuation calls it, and the provider never calls Cloud Agent.
4. It is configured by URL through this neutral contract, with no Gateway client and none of the retired `GATEWAY_*` or `IDENTITY_*` names.

A change that breaks one of these is a § 5 change and needs its own review.

## Risks accepted

- **A key the provider issued can be orphaned.** This happens when the provider issues a key and then no model verifies, or verification times out. Nothing is saved here, and the message says the key can be deleted at the provider. Everything that can be refused without a key is checked first, the hourly check budget included.
- **The provider sees the callback host and the challenge.** It does not see the state's meaning, the owner's identity here, or the verifier.

## Deployment

The provider registers `<web app URL>/settings/models/connected` as an allowed callback. The HybridInference Gateway implements this contract upstream, as a consent page and `POST /v1/auth/keys`. A deployment then fills in the five settings with its console's consent page, its key exchange URL, its public inference Base URL and the models to offer.
