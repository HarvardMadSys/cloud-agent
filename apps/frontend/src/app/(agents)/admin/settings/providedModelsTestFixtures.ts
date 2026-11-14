import type {
  OperatorModelConnection,
  OperatorModelConnectionList,
  OperatorModelTerms,
  OperatorModelUser,
} from '@/lib/api/adminOperatorModels';
import type { ModelStatus } from '@/lib/api/modelConnections';
import { APIError } from '@/lib/utils/errors';

export const PROVIDED_ID = 'oconn_0123456789abcdef0123456789abcdef';

/** What each account may spend a day on a fixture connection that has a budget: $5.00. */
export const DAILY_BUDGET = 5_000_000;

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

/**
 * A model's terms: $2.80 in and $11.20 out per million tokens, no cache
 * prices, open to every account and with no deployment-wide cap.
 */
export function priced(overrides: Partial<OperatorModelTerms> = {}): OperatorModelTerms {
  return {
    input_price: 2_800_000,
    output_price: 11_200_000,
    cache_read_price: null,
    cache_write_price: null,
    named_only: false,
    global_concurrency: null,
    ...overrides,
  };
}

/** A saved, disabled connection listing two models, with no daily budget and no prices yet. */
export function providedConnection(
  overrides: Partial<OperatorModelConnection> = {},
): OperatorModelConnection {
  const models = overrides.models ?? ['house-fast', 'house-deep'];
  return {
    id: PROVIDED_ID,
    name: 'House models',
    base_url: 'https://api.example.com/v1',
    models,
    default_model: null,
    is_default: false,
    enabled: false,
    end_user_header: null,
    user_concurrency: 4,
    user_daily_budget_microusd: null,
    terms: {},
    model_status: models.map(readyModel),
    key_refused_at: null,
    created_at: '2026-11-12T00:00:00Z',
    updated_at: '2026-11-12T00:00:00Z',
    ...overrides,
  };
}

/** What the two-model connection needs before it can be enabled: a budget, and both priced. */
export const COMPLETE: Pick<OperatorModelConnection, 'user_daily_budget_microusd' | 'terms'> = {
  user_daily_budget_microusd: DAILY_BUDGET,
  terms: { 'house-fast': priced(), 'house-deep': priced() },
};

export function providedList(
  connections: OperatorModelConnection[] = [providedConnection()],
  overrides: Partial<OperatorModelConnectionList> = {},
): OperatorModelConnectionList {
  return { connections, relay_hosts: 1, feature_enabled: true, ...overrides };
}

/** An account in the list, with nothing of its own and no use today unless said. */
export function account(
  account_id: string,
  overrides: Partial<OperatorModelUser> = {},
): OperatorModelUser {
  return {
    account_id,
    email: '',
    daily_budget_microusd: null,
    access: {},
    spent_today_microusd: 0,
    requests_today: 0,
    ...overrides,
  };
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
