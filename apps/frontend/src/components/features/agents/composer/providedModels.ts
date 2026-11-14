import type {
  AgentConfigApi,
  ModelConnectionOptionApi,
  ProvidedModelSourceApi,
} from '@/lib/api/agents';

// Models the deployment provides, as their owner-facing pages read them.
//
// A provided source (`oconn_…`) sits beside the owner's own connections
// (`mconn_…`) and is chosen the same way: a model and the source's id. What
// differs is the allowance. Each owner has a budget per UTC day on a source's
// models together, and each request is charged the tokens it used at its
// model's prices; `/config` answers the budget, what was spent of it today
// and when the day starts over. Amounts are micro-dollars (`@/lib/money`).

const PROVIDED_PREFIX = 'oconn_';

/** Whether an id names a connection the deployment provides. */
export function isProvidedId(id: string | null | undefined): boolean {
  return typeof id === 'string' && id.startsWith(PROVIDED_PREFIX);
}

/** The provided sources on offer; none where model connections are switched off. */
export function providedSources(config: AgentConfigApi | null): ProvidedModelSourceApi[] {
  return config?.model_connections_enabled === false ? [] : (config?.provided_sources ?? []);
}

/** The owner's own enabled connections on offer. */
export function ownConnections(config: AgentConfigApi | null): ModelConnectionOptionApi[] {
  return config?.model_connections_enabled === false ? [] : (config?.model_connections ?? []);
}

/** The provided source with this id, whether or not it is on offer right now. */
export function providedSource(
  config: AgentConfigApi | null,
  id: string | null | undefined,
): ProvidedModelSourceApi | undefined {
  return id ? config?.provided_sources?.find((source) => source.id === id) : undefined;
}

/** A source by id: a provided one, or one of the owner's own connections. */
export function findSource(
  config: AgentConfigApi | null,
  id: string | null | undefined,
): ModelConnectionOptionApi | undefined {
  if (!id) return undefined;
  return providedSource(config, id) ?? config?.model_connections?.find((entry) => entry.id === id);
}

/** What an owner has left to spend on one provided source today, in micro-dollars. */
export interface Allowance {
  /** What the owner may spend today on the source's models together. */
  budget: number;
  /** What their requests on the source have cost today. */
  spent: number;
  /** `budget - spent`, never below zero. */
  remaining: number;
  /** Nothing is left until `resetsAt`. */
  exhausted: boolean;
  resetsAt: string;
}

/**
 * The owner's allowance on a source, as of `now`: one budget for the day,
 * shared by every model the source lists.
 *
 * Once `resets_at` has passed the day's spending has started over, whatever a
 * `/config` read before midnight said: a page left open overnight must not
 * keep a model closed.
 */
export function allowanceOf(source: ProvidedModelSourceApi, now: number = Date.now()): Allowance {
  const budget = source.daily_budget_microusd;
  const reset = Date.parse(source.resets_at);
  const spent = Number.isFinite(reset) && now >= reset ? 0 : source.spent_today_microusd;
  const remaining = Math.max(0, budget - spent);
  return { budget, spent, remaining, exhausted: remaining === 0, resetsAt: source.resets_at };
}

/**
 * When the next of the provided sources' days starts over, in epoch
 * milliseconds; `null` when none lies ahead of `now`.
 */
export function nextAllowanceReset(
  config: AgentConfigApi | null,
  now: number = Date.now(),
): number | null {
  const ahead = providedSources(config)
    .map((source) => Date.parse(source.resets_at))
    .filter((reset) => Number.isFinite(reset) && reset > now);
  return ahead.length > 0 ? Math.min(...ahead) : null;
}

/**
 * A provided model's allowance, which is its source's; `null` for any other
 * choice. A source whose answer names no budget is not limited here: the
 * server still decides when the turn is sent.
 */
export function providedAllowance(
  config: AgentConfigApi | null,
  connectionId: string | null | undefined,
  model: string,
  now?: number,
): Allowance | null {
  const source = providedSource(config, connectionId);
  if (!source?.models.includes(model)) return null;
  return Number.isFinite(source.daily_budget_microusd) ? allowanceOf(source, now) : null;
}

/** Whether any provided source has a model some agent can run for this owner. */
export function hasRunnableProvidedModel(config: AgentConfigApi | null): boolean {
  return providedSources(config).some((source) =>
    source.models.some((model) => (source.model_runtimes?.[model]?.length ?? 0) > 0),
  );
}

/**
 * Whether nothing can run until the owner adds a connection of their own.
 *
 * The server says so (`model_connection_required`), and it already counts a
 * provided model as enough. A provided model that can run is checked here too,
 * so an answer that lists one never sends its owner to set up a key.
 */
export function modelSetupRequired(config: AgentConfigApi | null): boolean {
  return config?.model_connection_required === true && !hasRunnableProvidedModel(config);
}

/** The provided source a newcomer is pointed at: the default one, else the first. */
export function leadingProvidedSource(
  config: AgentConfigApi | null,
): ProvidedModelSourceApi | undefined {
  const sources = providedSources(config);
  return sources.find((source) => source.is_default) ?? sources[0];
}

/** When the day's budget starts over, in the viewer's own time zone and format. */
export function resetTime(iso: string): string {
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return '';
  return time.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

/**
 * The line that says whose a turn's usage is: the owner's provider bills a
 * turn on their own connection; one on a provided model spends the owner's
 * daily budget on this deployment. A control plane that predates provided
 * models names no source, which reads as the owner's own.
 */
export function usageSourceKey(
  usage: { source?: 'personal' | 'provided' | null } | null | undefined,
): 'modelSource.usageProvided' | 'modelSource.usageExternal' {
  return usage?.source === 'provided' ? 'modelSource.usageProvided' : 'modelSource.usageExternal';
}
