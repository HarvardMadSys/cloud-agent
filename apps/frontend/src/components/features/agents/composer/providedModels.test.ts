import { afterEach, describe, expect, it } from 'vitest';
import type { AgentConfigApi } from '@/lib/api/agents';
import {
  providedSource,
  TEST_BUDGET,
  TEST_PROVIDED,
  TEST_RESETS_AT,
} from '@/test/connectionConfig';
import {
  allowanceOf,
  findSource,
  hasRunnableProvidedModel,
  isProvidedId,
  leadingProvidedSource,
  modelSetupRequired,
  nextAllowanceReset,
  providedAllowance,
  providedSources,
  resetTime,
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

describe('an allowance on a provided source', () => {
  /** $5.00 a day, of which `spent` micro-dollars are gone. */
  const spending = (spent: number) =>
    providedSource(['house-fast', 'house-deep'], { spent_today_microusd: spent });

  it('is what is left of today’s budget, never below zero', () => {
    expect(allowanceOf(spending(1_800_000))).toEqual({
      budget: TEST_BUDGET,
      spent: 1_800_000,
      remaining: 3_200_000,
      exhausted: false,
      resetsAt: TEST_RESETS_AT,
    });
    expect(allowanceOf(spending(0))).toMatchObject({ remaining: TEST_BUDGET, exhausted: false });
    expect(allowanceOf(spending(TEST_BUDGET))).toMatchObject({ remaining: 0, exhausted: true });
    // Spending can pass the budget: requests in flight finish, and a budget can be lowered.
    expect(allowanceOf(spending(TEST_BUDGET + 40_000))).toMatchObject({
      remaining: 0,
      exhausted: true,
    });
  });

  it('is not used up while a millionth of a dollar is left', () => {
    expect(allowanceOf(spending(TEST_BUDGET - 1))).toMatchObject({
      remaining: 1,
      exhausted: false,
    });
  });

  it('starts over once the reset time has passed, without a new answer from the server', () => {
    const reset = Date.parse('2026-11-13T00:00:00Z');
    const yesterday = { ...spending(TEST_BUDGET), resets_at: '2026-11-13T00:00:00Z' };
    expect(allowanceOf(yesterday, reset - 1)).toMatchObject({ exhausted: true });
    expect(allowanceOf(yesterday, reset)).toMatchObject({
      spent: 0,
      remaining: TEST_BUDGET,
      exhausted: false,
    });
  });

  it('is one budget for every model the source lists', () => {
    const loaded = config({ provided_sources: [spending(1_800_000)] });
    const fast = providedAllowance(loaded, TEST_PROVIDED, 'house-fast');
    expect(fast?.remaining).toBe(3_200_000);
    expect(providedAllowance(loaded, TEST_PROVIDED, 'house-deep')).toEqual(fast);
  });

  it('is read by source id, and only for a model that source lists', () => {
    const loaded = config({ provided_sources: [spending(1_800_000)] });
    expect(providedAllowance(loaded, TEST_PROVIDED, 'house-fast')?.remaining).toBe(3_200_000);
    expect(providedAllowance(loaded, TEST_PROVIDED, 'elsewhere')).toBeNull();
    expect(providedAllowance(loaded, 'mconn_test', 'house-fast')).toBeNull();
    expect(providedAllowance(null, TEST_PROVIDED, 'house-fast')).toBeNull();
  });

  it('does not limit a source whose answer names no budget', () => {
    // The server still decides when the turn is sent; nothing here says "$NaN left".
    const { daily_budget_microusd: _budget, spent_today_microusd: _spent, ...bare } = spending(0);
    const loaded = config({ provided_sources: [bare as ReturnType<typeof spending>] });
    expect(providedAllowance(loaded, TEST_PROVIDED, 'house-fast')).toBeNull();
  });
});

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

  it('lead with the default source, else the first', () => {
    const other = { ...source, id: 'oconn_ffffffffffffffffffffffffffffffff', is_default: false };
    expect(leadingProvidedSource(config({ provided_sources: [other, source] }))).toBe(source);
    expect(leadingProvidedSource(config({ provided_sources: [other] }))).toBe(other);
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

describe('when the next day starts over', () => {
  const at = (iso: string) => Date.parse(iso);
  const spent = { spent_today_microusd: TEST_BUDGET };
  const sources = [
    providedSource(['a'], { ...spent, id: 'oconn_a', resets_at: '2026-11-13T00:00:00Z' }),
    providedSource(['b'], { ...spent, id: 'oconn_b', resets_at: '2026-11-14T00:00:00Z' }),
    providedSource(['c'], { ...spent, id: 'oconn_c', resets_at: 'soon' }),
  ];

  it('is the earliest reset still ahead', () => {
    const loaded = config({ provided_sources: sources });
    expect(nextAllowanceReset(loaded, at('2026-11-12T23:59:59Z'))).toBe(at('2026-11-13T00:00:00Z'));
    // One that has passed is no longer waited for.
    expect(nextAllowanceReset(loaded, at('2026-11-13T00:00:00Z'))).toBe(at('2026-11-14T00:00:00Z'));
    expect(nextAllowanceReset(loaded, at('2026-11-14T00:00:00Z'))).toBeNull();
  });

  it('is nothing where no provided source is on offer', () => {
    const now = at('2026-11-12T12:00:00Z');
    expect(nextAllowanceReset(null, now)).toBeNull();
    expect(nextAllowanceReset(config(), now)).toBeNull();
    const off = config({ provided_sources: sources, model_connections_enabled: false });
    expect(nextAllowanceReset(off, now)).toBeNull();
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

describe('the reset time', () => {
  const zone = process.env.TZ;
  afterEach(() => {
    if (zone === undefined) delete process.env.TZ;
    else process.env.TZ = zone;
  });

  it('is midnight UTC in the viewer’s own time zone', () => {
    process.env.TZ = 'Asia/Shanghai';
    expect(resetTime('2026-11-13T00:00:00Z')).toMatch(/8:00/);
    process.env.TZ = 'America/New_York';
    expect(resetTime('2026-11-13T00:00:00Z')).toMatch(/7:00/);
  });

  it('is empty for a time that cannot be read', () => {
    expect(resetTime('soon')).toBe('');
  });
});
