import { afterEach, describe, expect, it, vi } from 'vitest';

import { APIError } from '@/lib/utils/errors';

import { expectRequestsMatchContract } from './__tests__/contract';
import {
  clearOperatorModelUserAccess,
  clearOperatorModelUserBudget,
  createOperatorModelConnection,
  deleteOperatorModelConnection,
  getOperatorModelUsage,
  listOperatorModelConnections,
  listOperatorModelUsers,
  MAX_DAILY_BUDGET_MICROUSD,
  MAX_PRICE_MICROUSD,
  refusedEnable,
  setOperatorModelTerms,
  setOperatorModelUserAccess,
  setOperatorModelUserBudget,
  updateOperatorModelConnection,
  type OperatorModelTerms,
  type OperatorModelUser,
} from './adminOperatorModels';
import * as client from './client';
import { invalidFields, refusedModels } from './modelConnections';

afterEach(() => vi.restoreAllMocks());

const ID = 'oconn_0123456789abcdef0123456789abcdef';
const ROOT = '/admin/agent/operator-model-connections';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** $2.80 in and $11.20 out per million tokens, open to every account, with no cap. */
const TERMS: OperatorModelTerms = {
  input_price: 2_800_000,
  output_price: 11_200_000,
  cache_read_price: null,
  cache_write_price: null,
  named_only: false,
  global_concurrency: null,
};

/** An account's line in the list: a model withheld from it by name, and use today. */
const ACCOUNT: OperatorModelUser = {
  account_id: 'acct_1',
  email: 'a@example.com',
  daily_budget_microusd: null,
  access: { 'house-deep': false },
  spent_today_microusd: 1_250_042,
  requests_today: 42,
};

/** Every request is answered with `body`; a response's body can be read only once. */
function answer(body: unknown, status = 200) {
  return vi
    .spyOn(client, 'fetchWithAuth')
    .mockImplementation(async () =>
      status === 204
        ? new Response(null, { status })
        : new Response(JSON.stringify(body), { status, headers: JSON_HEADERS }),
    );
}

describe('admin provided-model connections client', () => {
  it('sends only requests the server accepts', async () => {
    answer({ id: ID });
    await listOperatorModelConnections();
    await createOperatorModelConnection({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
      models: ['house-fast'],
      default_model: 'house-fast',
      end_user_header: 'X-End-User',
      user_concurrency: 4,
      user_daily_budget_microusd: 5_000_000,
    });
    await createOperatorModelConnection({
      name: 'Later',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
      models: ['house-fast'],
      default_model: null,
      end_user_header: null,
    });
    await updateOperatorModelConnection(ID, {
      name: 'Renamed',
      default_model: '',
      is_default: true,
      enabled: true,
      end_user_header: '',
      user_concurrency: 8,
      user_daily_budget_microusd: 0,
    });
    await setOperatorModelTerms(ID, { model: 'house-fast', ...TERMS });
    await setOperatorModelTerms(ID, {
      model: 'vendor/model:v2',
      input_price: 0,
      output_price: MAX_PRICE_MICROUSD,
      cache_read_price: 280_000,
      cache_write_price: 3_500_000,
      named_only: true,
      global_concurrency: 8,
    });
    await listOperatorModelUsers(ID);
    await listOperatorModelUsers(ID, { cursor: 'acct 9', limit: 50 });
    await setOperatorModelUserBudget(ID, {
      account: 'a@example.com',
      daily_budget_microusd: MAX_DAILY_BUDGET_MICROUSD,
    });
    await setOperatorModelUserAccess(ID, {
      account: 'acct_1',
      model: 'vendor/model:v2',
      offered: false,
    });
    await getOperatorModelUsage(ID, { from: '2026-11-01', to: '2026-11-12' });
    answer(null, 204);
    await clearOperatorModelUserBudget(ID, 'acct 1');
    await clearOperatorModelUserAccess(ID, 'acct 1', 'vendor/model:v2');
    await deleteOperatorModelConnection(ID);
    expect(vi.mocked(client.fetchWithAuth)).toHaveBeenCalledTimes(14);
    expectRequestsMatchContract();
  });

  it('lists connections without caching, and supports abort', async () => {
    const list = { connections: [], relay_hosts: 0, feature_enabled: true };
    const fetch = answer(list);
    const controller = new AbortController();
    await expect(listOperatorModelConnections(controller.signal)).resolves.toEqual(list);
    expect(fetch).toHaveBeenCalledWith(expect.any(String), ROOT, {
      signal: controller.signal,
      cache: 'no-store',
    });
  });

  it('creates with the key and the daily budget in the body, and edits only what it is given', async () => {
    const fetch = answer({ id: ID }, 201);
    const input = {
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
      models: ['house-fast'],
      default_model: 'house-fast',
      end_user_header: null,
      user_concurrency: 4,
      user_daily_budget_microusd: 5_000_000,
    };
    await createOperatorModelConnection(input);
    expect(fetch).toHaveBeenLastCalledWith(expect.any(String), ROOT, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(input),
    });

    await updateOperatorModelConnection(ID, { enabled: true });
    expect(fetch).toHaveBeenLastCalledWith(expect.any(String), `${ROOT}/${ID}`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify({ enabled: true }),
    });

    await updateOperatorModelConnection(ID, { user_daily_budget_microusd: 0 });
    expect(JSON.parse(fetch.mock.lastCall?.[2]?.body as string)).toEqual({
      user_daily_budget_microusd: 0,
    });
  });

  it('deletes a connection, and reports a refusal', async () => {
    const fetch = answer(null, 204);
    await expect(deleteOperatorModelConnection(ID)).resolves.toBeUndefined();
    expect(fetch).toHaveBeenLastCalledWith(expect.any(String), `${ROOT}/${ID}`, {
      method: 'DELETE',
    });

    answer(
      { detail: { error: { type: 'operator_model_connection_not_found', message: 'Gone.' } } },
      404,
    );
    await expect(deleteOperatorModelConnection(ID)).rejects.toMatchObject({
      code: 'operator_model_connection_not_found',
      message: 'Gone.',
      statusCode: 404,
    });
  });

  it('sets a model’s terms, keeping null for a cache price and a cap it does not have', async () => {
    const fetch = answer({ id: ID, terms: { 'house-fast': TERMS } });
    const terms = { model: 'house-fast', ...TERMS, cache_read_price: 280_000 };
    await expect(setOperatorModelTerms(ID, terms)).resolves.toMatchObject({ id: ID });
    expect(fetch).toHaveBeenLastCalledWith(expect.any(String), `${ROOT}/${ID}/terms`, {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify(terms),
    });
    const sent = JSON.parse(fetch.mock.calls[0][2]?.body as string);
    expect(sent).toMatchObject({ cache_read_price: 280_000, cache_write_price: null });
    expect(sent.global_concurrency).toBeNull();
  });

  it('keeps what a refused enable says is still missing: the budget, and the models without prices', async () => {
    const message = 'Before enabling, choose a daily budget per account.';
    /** The enable, refused with this beside the sentence. */
    const refused = async (
      missing: Record<string, unknown>,
      type = 'operator_model_terms_missing',
    ) => {
      answer({ detail: { error: { type, message, ...missing } } }, 409);
      return updateOperatorModelConnection(ID, { enabled: true }).catch((error: unknown) => error);
    };

    const both = await refused({ budget: true, models: ['house-fast', 'house-deep'] });
    expect(both).toMatchObject({ code: 'operator_model_terms_missing', message, statusCode: 409 });
    expect(refusedEnable(both)).toEqual({ budget: true, models: ['house-fast', 'house-deep'] });
    // The models named are not verification results, and no field was invalid.
    expect(refusedModels(both)).toBeNull();
    expect(invalidFields(both)).toBeNull();

    expect(refusedEnable(await refused({ budget: false, models: ['house-new'] }))).toEqual({
      budget: false,
      models: ['house-new'],
    });
    expect(refusedEnable(await refused({ budget: true, models: [] }))).toEqual({
      budget: true,
      models: [],
    });
    // A refusal that names it only in its sentence says nothing here.
    expect(refusedEnable(await refused({}))).toBeNull();
    expect(refusedEnable(await refused({ models: ['house-new'] }))).toBeNull();
    // Another refusal is not a refused enable, whatever it carries.
    expect(
      refusedEnable(
        await refused({ budget: true, models: [] }, 'operator_model_connection_changed'),
      ),
    ).toBeNull();
    expect(refusedEnable(new Error('offline'))).toBeNull();
  });

  it('pages through accounts by cursor', async () => {
    const fetch = answer({ users: [], next_cursor: null });
    await listOperatorModelUsers(ID);
    expect(fetch.mock.calls[0][1]).toBe(`${ROOT}/${ID}/users`);
    await listOperatorModelUsers(ID, { cursor: 'acct 9', limit: 50 });
    expect(fetch.mock.calls[1][1]).toBe(`${ROOT}/${ID}/users?cursor=acct+9&limit=50`);
    expect(fetch.mock.calls[1][2]).toMatchObject({ cache: 'no-store' });
  });

  it('sets an account’s own budget by email or id, and clears it by account id', async () => {
    // The answer is the account's whole line: what it already had, with the budget written.
    const row = { ...ACCOUNT, daily_budget_microusd: 20_000_000 };
    const fetch = answer(row);
    const budget = { account: 'a@example.com', daily_budget_microusd: 20_000_000 };
    await expect(setOperatorModelUserBudget(ID, budget)).resolves.toEqual(row);
    expect(fetch).toHaveBeenLastCalledWith(expect.any(String), `${ROOT}/${ID}/user-budgets`, {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify(budget),
    });

    // Zero is a budget too: it withholds the connection from the account.
    await setOperatorModelUserBudget(ID, { account: 'acct_1', daily_budget_microusd: 0 });
    expect(JSON.parse(fetch.mock.lastCall?.[2]?.body as string).daily_budget_microusd).toBe(0);

    const cleared = answer(null, 204);
    await expect(clearOperatorModelUserBudget(ID, 'acct 1')).resolves.toBeUndefined();
    expect(cleared).toHaveBeenLastCalledWith(
      expect.any(String),
      `${ROOT}/${ID}/user-budgets?account_id=acct+1`,
      { method: 'DELETE' },
    );
  });

  it('offers an account a model or withholds it, and clears the answer by account id and model', async () => {
    // Its whole line again: the answer written beside the one it had, and its use today.
    const row = { ...ACCOUNT, access: { ...ACCOUNT.access, 'house-fast': false } };
    const fetch = answer(row);
    const access = { account: 'acct_1', model: 'house-fast', offered: false };
    await expect(setOperatorModelUserAccess(ID, access)).resolves.toEqual(row);
    expect(fetch).toHaveBeenLastCalledWith(expect.any(String), `${ROOT}/${ID}/user-access`, {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify(access),
    });

    const cleared = answer(null, 204);
    await clearOperatorModelUserAccess(ID, 'acct 1', 'vendor/model:v2');
    expect(cleared).toHaveBeenLastCalledWith(
      expect.any(String),
      `${ROOT}/${ID}/user-access?account_id=acct+1&model=vendor%2Fmodel%3Av2`,
      { method: 'DELETE' },
    );
  });

  it('reports a clear for an account that has nothing of its own, each by its own type', async () => {
    answer(
      { detail: { error: { type: 'operator_model_user_budget_not_found', message: 'None.' } } },
      404,
    );
    await expect(clearOperatorModelUserBudget(ID, 'acct_1')).rejects.toMatchObject({
      code: 'operator_model_user_budget_not_found',
      statusCode: 404,
    });
    answer(
      { detail: { error: { type: 'operator_model_user_access_not_found', message: 'None.' } } },
      404,
    );
    await expect(clearOperatorModelUserAccess(ID, 'acct_1', 'house-fast')).rejects.toMatchObject({
      code: 'operator_model_user_access_not_found',
      statusCode: 404,
    });
  });

  it('reads usage between two UTC days', async () => {
    const usage = {
      rows: [
        {
          day: '2026-11-11',
          account_id: 'acct_1',
          email: 'a@example.com',
          model: 'house-fast',
          requests: 42,
          cost_microusd: 1_250_000,
          input_tokens: 400_000,
          cache_read_tokens: 300_000,
          cache_write_tokens: 0,
          output_tokens: 9_000,
          estimated_requests: 2,
        },
      ],
      truncated: false,
    };
    const fetch = answer(usage);
    await expect(
      getOperatorModelUsage(ID, { from: '2026-11-01', to: '2026-11-12' }),
    ).resolves.toEqual(usage);
    expect(fetch.mock.calls[0][1]).toBe(`${ROOT}/${ID}/usage?from=2026-11-01&to=2026-11-12`);
  });

  it('keeps each model’s result from a refused verification, as the owner’s client does', async () => {
    const models = [{ model: 'house-fast', status: 'failed', category: 'auth', runtimes: [] }];
    answer(
      {
        error: {
          type: 'model_connection_verification_failed',
          message: 'A model could not be verified.',
          models,
        },
      },
      422,
    );
    const cause = await createOperatorModelConnection({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
      models: ['house-fast'],
    }).catch((error: unknown) => error);
    expect(cause).toBeInstanceOf(APIError);
    expect(refusedModels(cause)).toEqual(models);
    expect(invalidFields(cause)).toBeNull();
  });

  it('keeps which fields an invalid request named, never their values', async () => {
    answer(
      {
        error: {
          type: 'invalid_model_connection',
          message: 'Check the fields.',
          fields: [
            { field: 'base_url', message: 'Use https.' },
            { field: 'end_user_header', message: 'Not a header name.' },
          ],
        },
      },
      422,
    );
    const cause = await updateOperatorModelConnection(ID, { base_url: 'http://x' }).catch(
      (error: unknown) => error,
    );
    expect(invalidFields(cause)).toEqual(['base_url', 'end_user_header']);
    expect(refusedModels(cause)).toBeNull();
  });
});
