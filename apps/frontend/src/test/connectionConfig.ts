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

/** One provided source listing `models`, every agent able to run each. */
export function providedSource(
  models: string[],
  overrides: Partial<ProvidedModelSourceApi> = {},
  runtimes: string[] = ['claude-code', 'codex', 'pi', 'opencode', 'kilo'],
): ProvidedModelSourceApi {
  return {
    id: TEST_PROVIDED,
    name: 'House models',
    models,
    model_runtimes: Object.fromEntries(models.map((model) => [model, runtimes])),
    ...overrides,
  };
}
