import { describe, expect, it } from 'vitest';
import type { AgentConfigApi } from '@/lib/api/agents';
import { providedSource, TEST_PROVIDED } from '@/test/connectionConfig';
import {
  findSource,
  hasRunnableProvidedModel,
  isProvidedId,
  leadingProvidedSource,
  modelSetupRequired,
  providedSources,
  usageSourceKey,
} from './providedModels';

function config(overrides: Partial<AgentConfigApi> = {}): AgentConfigApi {
  return {
    repos: [],
    runtimes: ['codex'],
    github_connected: true,
    github_install_url: null,
    model_connections_enabled: true,
    ...overrides,
  };
}

describe('provided sources in the configuration', () => {
  const source = providedSource(['house-fast']);

  it('are found by id beside the owner’s own connections', () => {
    const loaded = config({
      provided_sources: [source],
      model_connections: [{ id: 'mconn_test', name: 'Mine', models: [], model_runtimes: {} }],
    });
    expect(findSource(loaded, TEST_PROVIDED)?.name).toBe('House models');
    expect(findSource(loaded, 'mconn_test')?.name).toBe('Mine');
    expect(findSource(loaded, 'oconn_ffffffffffffffffffffffffffffffff')).toBeUndefined();
    expect(findSource(loaded, null)).toBeUndefined();
    expect(isProvidedId(TEST_PROVIDED)).toBe(true);
    expect(isProvidedId('mconn_test')).toBe(false);
    expect(isProvidedId(null)).toBe(false);
  });

  it('are not offered where model connections are switched off, or before the feature', () => {
    expect(providedSources(config({ provided_sources: [source] }))).toEqual([source]);
    expect(
      providedSources(config({ provided_sources: [source], model_connections_enabled: false })),
    ).toEqual([]);
    expect(providedSources(config())).toEqual([]);
    expect(providedSources(null)).toEqual([]);
  });

  it('lead with the first, the oldest, where new owners start', () => {
    const other = { ...source, id: 'oconn_ffffffffffffffffffffffffffffffff' };
    expect(leadingProvidedSource(config({ provided_sources: [other, source] }))).toBe(other);
    expect(leadingProvidedSource(config({ provided_sources: [source, other] }))).toBe(source);
    expect(leadingProvidedSource(config())).toBeUndefined();
  });
});

describe('whether the owner must add a connection of their own', () => {
  const runnable = providedSource(['house-fast']);

  it('follows the server', () => {
    expect(modelSetupRequired(config({ model_connection_required: true }))).toBe(true);
    expect(modelSetupRequired(config({ model_connection_required: false }))).toBe(false);
    expect(modelSetupRequired(config())).toBe(false);
    expect(modelSetupRequired(null)).toBe(false);
  });

  it('is never asked of an owner a provided model can run for', () => {
    const loaded = config({ model_connection_required: true, provided_sources: [runnable] });
    expect(hasRunnableProvidedModel(loaded)).toBe(true);
    expect(modelSetupRequired(loaded)).toBe(false);
  });

  it('is still asked where the provided source offers this owner nothing that runs', () => {
    // Every model withheld from this account: the source is listed with none.
    const withheld = providedSource([]);
    // Listed, but no agent can run it yet.
    const unverified = providedSource(['house-fast'], {}, []);
    for (const source of [withheld, unverified]) {
      const loaded = config({ model_connection_required: true, provided_sources: [source] });
      expect(hasRunnableProvidedModel(loaded)).toBe(false);
      expect(modelSetupRequired(loaded)).toBe(true);
    }
  });
});

describe('the line that says whose a turn’s usage is', () => {
  it('names this deployment for a provided turn, the owner’s provider otherwise', () => {
    expect(usageSourceKey({ source: 'provided' })).toBe('modelSource.usageProvided');
    expect(usageSourceKey({ source: 'personal' })).toBe('modelSource.usageExternal');
    // A control plane that predates provided models names no source.
    expect(usageSourceKey({})).toBe('modelSource.usageExternal');
    expect(usageSourceKey({ source: null })).toBe('modelSource.usageExternal');
    expect(usageSourceKey(null)).toBe('modelSource.usageExternal');
  });
});
