import type {
  AgentConfigApi,
  ModelConnectionOptionApi,
  ProvidedModelSourceApi,
} from '@/lib/api/agents';

// Models the deployment provides, as their owner-facing pages read them.
//
// A provided source (`oconn_…`) sits beside the owner's own connections
// (`mconn_…`) and is chosen the same way: a model and the source's id. It is
// every owner's, with the same models for everyone, and runs on the
// deployment's key; nothing here counts what an owner may spend on it.

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

/** The provided source a newcomer is pointed at: the oldest, where new owners start. */
export function leadingProvidedSource(
  config: AgentConfigApi | null,
): ProvidedModelSourceApi | undefined {
  return providedSources(config)[0];
}

/**
 * The line that says whose a turn's usage is: the owner's provider bills a
 * turn on their own connection; one on a provided model runs on this
 * deployment's key. A control plane that predates provided
 * models names no source, which reads as the owner's own.
 */
export function usageSourceKey(
  usage: { source?: 'personal' | 'provided' | null } | null | undefined,
): 'modelSource.usageProvided' | 'modelSource.usageExternal' {
  return usage?.source === 'provided' ? 'modelSource.usageProvided' : 'modelSource.usageExternal';
}
