import { config } from '@/config/env';

import { fetchWithAuth, jsonOrThrow } from './client';
import { connectionJson, type ModelStatus } from './modelConnections';

// Models the deployment provides, as an administrator sets them up.
//
// A provided connection (`oconn_…`) is the deployment's own inference
// endpoint, offered to every admitted account with no setup of theirs. An
// administrator gives it a name, a Base URL and a key; its models are the ones
// the provider lists for that key, with the prices that listing gives. The key
// is write-only. Each request's tokens are recorded, with what they cost at
// those prices; nothing here limits them: the provider's own budget on the key
// does. What an owner is offered is `ProvidedModelSourceApi` in `/config`.
//
// Money is in whole millionths of a US dollar (`…_microusd`: `5000000` is
// $5.00), and a price in those per million tokens (`2800000` is $2.80 per
// million tokens); `@/lib/money` converts both.
//
// Refusals mirror the owner's connection API, so `refusedModels` and
// `invalidFields` from `./modelConnections` read them too.

const ROOT = '/admin/agent/operator-model-connections';

/** The longest name the server keeps for a connection. */
export const OPERATOR_MAX_NAME_CHARS = 64;

/** What one model costs as the provider's listing gives it, in micro-dollars per million tokens. */
export interface OperatorModelPrices {
  input_price: number;
  /** Output tokens, reasoning included. */
  output_price: number;
  /** Input tokens read from the provider's prompt cache; `null` records them at `input_price`. */
  cache_read_price: number | null;
  /** Input tokens written to the provider's prompt cache; `null` records them at `input_price`. */
  cache_write_price: number | null;
}

/** One provided connection, as an administrator reads it. Never its key. */
export interface OperatorModelConnection {
  id: string;
  name: string;
  base_url: string;
  /** What the provider lists for the key, in its order. */
  models: string[];
  enabled: boolean;
  /**
   * Each model's prices, from the provider's listing. A model absent here was
   * listed with none: its use is recorded by tokens alone.
   */
  prices: Record<string, OperatorModelPrices>;
  /** Each model's verification. A model no agent can use is offered to nobody. */
  model_status: ModelStatus[];
  /** Since when the provider has refused the stored key; `null` is healthy. */
  key_refused_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OperatorModelConnectionList {
  connections: OperatorModelConnection[];
  /** Enrolled servers that reach the model relay; zero means no turn can run on one. */
  relay_hosts: number;
  /** Whether model connections are switched on at all on this deployment. */
  feature_enabled: boolean;
}

export interface OperatorModelConnectionCreate {
  name: string;
  base_url: string;
  api_key: string;
}

/** An edit: a field left out is kept. A new key or Base URL reads the provider's models again. */
export interface OperatorModelConnectionUpdate {
  name?: string;
  base_url?: string;
  api_key?: string;
  enabled?: boolean;
}

/** What one account used of one model on one UTC day, and what it cost. */
export interface OperatorModelUsageRow {
  day: string;
  account_id: string;
  email: string;
  model: string;
  /** Requests the provider served. */
  requests: number;
  /** What they cost at the model's listed prices then, in micro-dollars; `0` without prices. */
  cost_microusd: number;
  /** Every input token, the cached ones included. */
  input_tokens: number;
  /** Of `input_tokens`: read from the provider's prompt cache. */
  cache_read_tokens: number;
  /** Of `input_tokens`: written to the provider's prompt cache. */
  cache_write_tokens: number;
  output_tokens: number;
  /**
   * Of `requests`: recorded by estimate, because the provider's answer named no
   * usage or was cut short before it could.
   */
  estimated_requests: number;
}

export interface OperatorModelUsage {
  rows: OperatorModelUsageRow[];
  /** More rows matched than are answered. */
  truncated: boolean;
}

function path(id: string, suffix = '') {
  return `${ROOT}/${encodeURIComponent(id)}${suffix}`;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

async function done(response: Response): Promise<void> {
  if (!response.ok) await jsonOrThrow(response);
}

export async function listOperatorModelConnections(
  signal?: AbortSignal,
): Promise<OperatorModelConnectionList> {
  return jsonOrThrow(await fetchWithAuth(config.apiBase, ROOT, { signal, cache: 'no-store' }));
}

/**
 * Read the models the provider lists for the key, verify them, then save the
 * connection, disabled. This can take minutes; a refusal stores nothing
 * (`refusedModels`).
 */
export async function createOperatorModelConnection(
  input: OperatorModelConnectionCreate,
): Promise<OperatorModelConnection> {
  return connectionJson(await fetchWithAuth(config.apiBase, ROOT, json('POST', input)));
}

/**
 * Save changes. A new key or Base URL reads the provider's models again and
 * verifies them first; a refusal leaves the connection as it was.
 */
export async function updateOperatorModelConnection(
  id: string,
  changes: OperatorModelConnectionUpdate,
): Promise<OperatorModelConnection> {
  return connectionJson(await fetchWithAuth(config.apiBase, path(id), json('PATCH', changes)));
}

/**
 * Read the provider's models again with the stored key: new ones are verified
 * and added, ones it no longer lists are removed, and prices are read again.
 */
export async function refreshOperatorModels(id: string): Promise<OperatorModelConnection> {
  return connectionJson(
    await fetchWithAuth(config.apiBase, path(id, '/refresh-models'), { method: 'POST' }),
  );
}

export async function deleteOperatorModelConnection(id: string): Promise<void> {
  await done(await fetchWithAuth(config.apiBase, path(id), { method: 'DELETE' }));
}

/**
 * Requests, tokens and cost per UTC day, account and model, between two
 * `YYYY-MM-DD` days: what the provider's own answers said was used, at the
 * model's listed prices at the time. Not the provider's bill.
 */
export async function getOperatorModelUsage(
  id: string,
  range: { from: string; to: string },
  signal?: AbortSignal,
): Promise<OperatorModelUsage> {
  const query = new URLSearchParams({ from: range.from, to: range.to });
  return jsonOrThrow(
    await fetchWithAuth(config.apiBase, path(id, `/usage?${query}`), { signal, cache: 'no-store' }),
  );
}
