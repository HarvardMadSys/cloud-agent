import { afterEach, describe, expect, it, vi } from 'vitest';

import { APIError } from '@/lib/utils/errors';

import { expectRequestsMatchContract } from './__tests__/contract';
import {
  createOperatorModelConnection,
  deleteOperatorModelConnection,
  getOperatorModelUsage,
  listOperatorModelConnections,
  refreshOperatorModels,
  updateOperatorModelConnection,
} from './adminOperatorModels';
import * as client from './client';
import { invalidFields, refusedModels, takenModel } from './modelConnections';

afterEach(() => vi.restoreAllMocks());

const ID = 'oconn_0123456789abcdef0123456789abcdef';
const ROOT = '/admin/agent/operator-model-connections';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

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
    });
    await updateOperatorModelConnection(ID, {
      name: 'Renamed',
      base_url: 'https://api.example.com/v2',
      api_key: 'sk-new',
      enabled: true,
    });
    await refreshOperatorModels(ID);
    await getOperatorModelUsage(ID, { from: '2026-11-01', to: '2026-11-12' });
    answer(null, 204);
    await deleteOperatorModelConnection(ID);
    expect(vi.mocked(client.fetchWithAuth)).toHaveBeenCalledTimes(6);
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

  it('creates with only a name, a Base URL and a key, and edits only what it is given', async () => {
    const fetch = answer({ id: ID }, 201);
    const input = {
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
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
  });

  it('reads the provider’s models again with the stored key', async () => {
    const fetch = answer({ id: ID });
    await refreshOperatorModels(ID);
    expect(fetch).toHaveBeenLastCalledWith(expect.any(String), `${ROOT}/${ID}/refresh-models`, {
      method: 'POST',
    });

    answer(
      {
        detail: {
          error: { type: 'operator_model_listing_too_long', message: 'Limit the key.' },
        },
      },
      422,
    );
    await expect(refreshOperatorModels(ID)).rejects.toMatchObject({
      code: 'operator_model_listing_too_long',
      statusCode: 422,
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
    }).catch((error: unknown) => error);
    expect(cause).toBeInstanceOf(APIError);
    expect(refusedModels(cause)).toEqual(models);
    expect(invalidFields(cause)).toBeNull();
  });

  it('keeps which model a refused save found already provided, and where', async () => {
    answer(
      {
        detail: {
          error: {
            type: 'operator_model_model_taken',
            message: 'glm is already provided by Campus.',
            taken: { model: 'glm', connection: 'Campus' },
          },
        },
      },
      409,
    );
    const cause = await createOperatorModelConnection({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
    }).catch((error: unknown) => error);
    expect(takenModel(cause)).toEqual({ model: 'glm', connection: 'Campus' });
    expect(takenModel(new APIError('other', 'raw', 409))).toBeNull();
  });

  it('keeps which fields an invalid request named, never their values', async () => {
    answer(
      {
        error: {
          type: 'invalid_model_connection',
          message: 'Check the fields.',
          fields: [
            { field: 'base_url', message: 'Use https.' },
            { field: 'api_key', message: 'No spaces.' },
          ],
        },
      },
      422,
    );
    const cause = await updateOperatorModelConnection(ID, { base_url: 'http://x' }).catch(
      (error: unknown) => error,
    );
    expect(invalidFields(cause)).toEqual(['base_url', 'api_key']);
    expect(refusedModels(cause)).toBeNull();
  });
});
