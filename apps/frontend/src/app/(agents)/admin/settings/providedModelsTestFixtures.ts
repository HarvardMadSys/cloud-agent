import type {
  OperatorModelConnection,
  OperatorModelConnectionList,
  OperatorModelPrices,
} from '@/lib/api/adminOperatorModels';
import type { ModelStatus } from '@/lib/api/modelConnections';
import { APIError } from '@/lib/utils/errors';

export const PROVIDED_ID = 'oconn_0123456789abcdef0123456789abcdef';

/** A model verified for every agent. */
export function readyModel(model: string): ModelStatus {
  return {
    model,
    status: 'ready',
    category: null,
    message: '',
    runtimes: ['claude-code', 'codex'],
    paths: [],
    protocols: {},
    checked_at: '2026-11-12T00:00:00Z',
  };
}

/** A model the provider refused: no agent can use it. */
export function refusedModel(model: string, category = 'auth'): ModelStatus {
  return { ...readyModel(model), status: 'failed', category, runtimes: [] };
}

/** A model's listed prices: $2.80 in and $11.20 out per million tokens, no cache prices. */
export function priced(overrides: Partial<OperatorModelPrices> = {}): OperatorModelPrices {
  return {
    input_price: 2_800_000,
    output_price: 11_200_000,
    cache_read_price: null,
    cache_write_price: null,
    ...overrides,
  };
}

/** A saved, disabled connection whose key lists two models, the provider pricing neither. */
export function providedConnection(
  overrides: Partial<OperatorModelConnection> = {},
): OperatorModelConnection {
  const models = overrides.models ?? ['house-fast', 'house-deep'];
  return {
    id: PROVIDED_ID,
    name: 'House models',
    base_url: 'https://api.example.com/v1',
    models,
    enabled: false,
    prices: {},
    model_status: models.map(readyModel),
    key_refused_at: null,
    created_at: '2026-11-12T00:00:00Z',
    updated_at: '2026-11-12T00:00:00Z',
    ...overrides,
  };
}

/** Both models of the two-model connection, priced by the provider's listing. */
export const PRICED: Pick<OperatorModelConnection, 'prices'> = {
  prices: { 'house-fast': priced(), 'house-deep': priced() },
};

export function providedList(
  connections: OperatorModelConnection[] = [providedConnection()],
  overrides: Partial<OperatorModelConnectionList> = {},
): OperatorModelConnectionList {
  return { connections, relay_hosts: 1, feature_enabled: true, ...overrides };
}

/** A refusal as the API client raises it. */
export function refusal(
  code: string,
  message: string,
  status: number,
  details?: Record<string, unknown>,
): APIError {
  return new APIError(code, message, status, details);
}
