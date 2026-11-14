import { config } from '@/config/env';
import { APIError } from '@/lib/utils/errors';

import { fetchWithAuth, jsonOrThrow } from './client';
import { connectionJson, MAX_MODELS, type ModelStatus } from './modelConnections';

// Models the deployment provides, as an administrator sets them up.
//
// A provided connection (`oconn_…`) is the deployment's own inference
// endpoint, offered to every admitted account with no setup of theirs. Its
// key is write-only. Each request is charged the tokens it used at its
// model's prices, and every account has one daily budget on a connection's
// models together. What an owner is offered is `ProvidedModelSourceApi` in
// `/config`.
//
// Money is in whole millionths of a US dollar (`…_microusd`: `5000000` is
// $5.00), and a price in those per million tokens (`2800000` is $2.80 per
// million tokens); `@/lib/money` converts both.
//
// Refusals mirror the owner's connection API, so `refusedModels` and
// `invalidFields` from `./modelConnections` read them too. A refused enable
// also says what the connection still lacks, which `refusedEnable` reads.

const ROOT = '/admin/agent/operator-model-connections';

/** How many models one provided connection may list: the same as an owner's. */
export const OPERATOR_MAX_MODELS = MAX_MODELS;
/** The longest name the server keeps for a connection. */
export const OPERATOR_MAX_NAME_CHARS = 64;
/** The most a daily budget is ever set to, in micro-dollars: a million dollars a day. */
export const MAX_DAILY_BUDGET_MICROUSD = 1_000_000_000_000;
/** The most a price is ever set to, in micro-dollars per million tokens. */
export const MAX_PRICE_MICROUSD = 1_000_000_000_000;
/** The most a deployment-wide concurrency cap is ever set to. */
export const MAX_GLOBAL_CONCURRENCY = 1024;
/** The most requests one account may have in flight at once. */
export const MAX_USER_CONCURRENCY = 64;
export const DEFAULT_USER_CONCURRENCY = 4;

/**
 * What one model is provided on: its prices, who it is open to, and a cap on
 * requests in flight. Prices are in micro-dollars per million tokens.
 */
export interface OperatorModelTerms {
  input_price: number;
  /** Output tokens, reasoning included. */
  output_price: number;
  /** Input tokens read from the provider's prompt cache; `null` charges them at `input_price`. */
  cache_read_price: number | null;
  /** Input tokens written to the provider's prompt cache; `null` charges them at `input_price`. */
  cache_write_price: number | null;
  /** Offered only to the accounts it is offered to by name, not to every account with a budget. */
  named_only: boolean;
  /** Requests in flight across all accounts; `null` is unlimited. */
  global_concurrency: number | null;
}

/** What a provided connection still lacks before it can be enabled, and so offers anything. */
export interface OperatorTermsMissing {
  /** Its default daily budget is still to be chosen. */
  budget: boolean;
  /** The listed models still without prices. */
  models: string[];
}

/** An enable refused because the connection has no default budget, or a model has no prices. */
export const TERMS_MISSING = 'operator_model_terms_missing';

/**
 * What a refused enable said the connection still lacks; `null` for any other
 * failure, and for a refusal that names it only in its message.
 */
export function refusedEnable(cause: unknown): OperatorTermsMissing | null {
  if (!(cause instanceof APIError) || cause.code !== TERMS_MISSING) return null;
  const { budget, models } = cause.details ?? {};
  if (typeof budget !== 'boolean' || !Array.isArray(models)) return null;
  return { budget, models: models.map(String) };
}

/** One provided connection, as an administrator reads it. Never its key. */
export interface OperatorModelConnection {
  id: string;
  name: string;
  base_url: string;
  models: string[];
  default_model: string | null;
  /** Whether a new account with no history starts here. */
  is_default: boolean;
  enabled: boolean;
  /** The header each relayed request names its account in, by an opaque tag. */
  end_user_header: string | null;
  /** Requests one account may have in flight at once. */
  user_concurrency: number;
  /**
   * What each account may spend per UTC day on this connection's models
   * together, in micro-dollars. `null` is not chosen yet: the connection
   * cannot be enabled and offers nothing. `0` offers it only to accounts given
   * a budget of their own.
   */
  user_daily_budget_microusd: number | null;
  /**
   * Each model's terms. A model absent here has no prices yet: it is offered
   * to nobody, and the connection cannot be enabled until every model has them.
   */
  terms: Record<string, OperatorModelTerms>;
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
  models: string[];
  default_model?: string | null;
  end_user_header?: string | null;
  user_concurrency?: number;
  /** May be left out and chosen later, before enabling. */
  user_daily_budget_microusd?: number;
}

/**
 * An edit: a field left out is kept. `''` clears `default_model` and
 * `end_user_header`; a daily budget, once chosen, can be changed but not cleared.
 */
export interface OperatorModelConnectionUpdate {
  name?: string;
  base_url?: string;
  api_key?: string;
  models?: string[];
  default_model?: string;
  is_default?: boolean;
  enabled?: boolean;
  end_user_header?: string;
  user_concurrency?: number;
  user_daily_budget_microusd?: number;
}

/** One account with a budget or a model answer of its own, or with use today. */
export interface OperatorModelUser {
  account_id: string;
  email: string;
  /** The account's own daily budget, in micro-dollars; `null` follows the connection's default. */
  daily_budget_microusd: number | null;
  /**
   * The account's own answers, per model: offered or withheld. A model absent
   * here follows the model's default.
   */
  access: Record<string, boolean>;
  /** What the account's requests on the connection have cost today, in micro-dollars. */
  spent_today_microusd: number;
  /** Requests the provider served the account today, across models. */
  requests_today: number;
}

export interface OperatorModelUserList {
  users: OperatorModelUser[];
  next_cursor: string | null;
}

/** What one account used of one model on one UTC day, and what it cost. */
export interface OperatorModelUsageRow {
  day: string;
  account_id: string;
  email: string;
  model: string;
  /** Requests the provider served. */
  requests: number;
  /** What they cost at the model's prices at the time, in micro-dollars. */
  cost_microusd: number;
  /** Every input token, the cached ones included. */
  input_tokens: number;
  /** Of `input_tokens`: read from the provider's prompt cache. */
  cache_read_tokens: number;
  /** Of `input_tokens`: written to the provider's prompt cache. */
  cache_write_tokens: number;
  output_tokens: number;
  /**
   * Of `requests`: charged by estimate, because the provider's answer named no
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
 * Verify every model with the provider, then save the connection, disabled.
 * This can take minutes; a refusal stores nothing (`refusedModels`).
 */
export async function createOperatorModelConnection(
  input: OperatorModelConnectionCreate,
): Promise<OperatorModelConnection> {
  return connectionJson(await fetchWithAuth(config.apiBase, ROOT, json('POST', input)));
}

/**
 * Save changes. A new key or Base URL, or an added model, is verified first;
 * a refusal leaves the connection as it was.
 */
export async function updateOperatorModelConnection(
  id: string,
  changes: OperatorModelConnectionUpdate,
): Promise<OperatorModelConnection> {
  return connectionJson(await fetchWithAuth(config.apiBase, path(id), json('PATCH', changes)));
}

export async function deleteOperatorModelConnection(id: string): Promise<void> {
  await done(await fetchWithAuth(config.apiBase, path(id), { method: 'DELETE' }));
}

/** Set one model's prices, who it is open to, and its cap on requests in flight. */
export async function setOperatorModelTerms(
  id: string,
  terms: { model: string } & OperatorModelTerms,
): Promise<OperatorModelConnection> {
  return connectionJson(
    await fetchWithAuth(config.apiBase, path(id, '/terms'), json('PUT', terms)),
  );
}

/** Accounts with a budget or a model answer of their own, or with use today, a page at a time. */
export async function listOperatorModelUsers(
  id: string,
  options: { cursor?: string | null; limit?: number; signal?: AbortSignal } = {},
): Promise<OperatorModelUserList> {
  const query = new URLSearchParams();
  if (options.cursor) query.set('cursor', options.cursor);
  if (options.limit) query.set('limit', String(options.limit));
  const suffix = query.size ? `/users?${query}` : '/users';
  return jsonOrThrow(
    await fetchWithAuth(config.apiBase, path(id, suffix), {
      signal: options.signal,
      cache: 'no-store',
    }),
  );
}

/**
 * Give one account, named by id or email, a daily budget of its own on this
 * connection, in place of the default. `0` withholds the connection from it.
 *
 * Answers the account as `listOperatorModelUsers` has it now: all of it, not
 * only the budget written.
 */
export async function setOperatorModelUserBudget(
  id: string,
  budget: { account: string; daily_budget_microusd: number },
): Promise<OperatorModelUser> {
  return connectionJson(
    await fetchWithAuth(config.apiBase, path(id, '/user-budgets'), json('PUT', budget)),
  );
}

/** Put one account back on the connection's default budget. */
export async function clearOperatorModelUserBudget(id: string, accountId: string): Promise<void> {
  const query = new URLSearchParams({ account_id: accountId });
  await done(
    await fetchWithAuth(config.apiBase, path(id, `/user-budgets?${query}`), { method: 'DELETE' }),
  );
}

/**
 * Offer one account, named by id or email, a model, or withhold it, in place
 * of the model's default.
 *
 * Answers the account as `listOperatorModelUsers` has it now, as
 * `setOperatorModelUserBudget` does.
 */
export async function setOperatorModelUserAccess(
  id: string,
  access: { account: string; model: string; offered: boolean },
): Promise<OperatorModelUser> {
  return connectionJson(
    await fetchWithAuth(config.apiBase, path(id, '/user-access'), json('PUT', access)),
  );
}

/** Put one account back on a model's default: open to every account, or to named ones. */
export async function clearOperatorModelUserAccess(
  id: string,
  accountId: string,
  model: string,
): Promise<void> {
  const query = new URLSearchParams({ account_id: accountId, model });
  await done(
    await fetchWithAuth(config.apiBase, path(id, `/user-access?${query}`), { method: 'DELETE' }),
  );
}

/**
 * Requests, tokens and cost per UTC day, account and model, between two
 * `YYYY-MM-DD` days: what the provider's own answers said was used, at the
 * model's prices at the time. Not the provider's bill.
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
