import type { AgentConfigApi, ProvidedModelSourceApi } from '@/lib/api/agents';

/** The connection a test's owner runs on. */
export const TEST_CONNECTION = 'mconn_test';

/** The configuration fields of an owner with one connection listing `models`. */
export function offering(
  models: string[],
  runtimes: string[] = ['claude-code', 'codex', 'pi', 'opencode', 'kilo'],
): Pick<AgentConfigApi, 'model_connections' | 'model_connections_enabled'> {
  return {
    model_connections_enabled: true,
    model_connections: [
      {
        id: TEST_CONNECTION,
        name: 'Mine',
        models,
        model_runtimes: Object.fromEntries(models.map((model) => [model, runtimes])),
      },
    ],
  };
}

/** A connection the deployment provides, as a test's owner is offered it. */
export const TEST_PROVIDED = 'oconn_0123456789abcdef0123456789abcdef';

/** A time safely after any test's clock: today's spending has not started over. */
export const TEST_RESETS_AT = '2999-01-01T00:00:00Z';

/** What a test's owner may spend a day on a provided source: $5.00, in micro-dollars. */
export const TEST_BUDGET = 5_000_000;

/**
 * One provided source listing `models`, on which the owner may spend
 * `TEST_BUDGET` a day and has spent nothing. `overrides` sets another budget
 * (`daily_budget_microusd`) or what was spent today (`spent_today_microusd`):
 * all of the budget spent is a source used up for today.
 */
export function providedSource(
  models: string[],
  overrides: Partial<ProvidedModelSourceApi> = {},
  runtimes: string[] = ['claude-code', 'codex', 'pi', 'opencode', 'kilo'],
): ProvidedModelSourceApi {
  return {
    id: TEST_PROVIDED,
    name: 'House models',
    is_default: true,
    default_model: models[0] ?? null,
    models,
    model_runtimes: Object.fromEntries(models.map((model) => [model, runtimes])),
    daily_budget_microusd: TEST_BUDGET,
    spent_today_microusd: 0,
    resets_at: TEST_RESETS_AT,
    ...overrides,
  };
}
