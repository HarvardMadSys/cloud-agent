import { describe, expect, it } from 'vitest';
import type { AgentConfigApi } from '@/lib/api/agents';
import { translate } from '@/lib/i18n/translate';
import {
  choiceKey,
  choiceLabel,
  isModelUnavailable,
  listedChoices,
  modelOverride,
  modelPicker,
  NO_MODEL,
  offeredChoices,
  resolvedChoice,
  storedChoice,
  turnChoice,
  turnModelPicker,
} from './modelSource';

const A = 'mconn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const B = 'mconn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate('en', key, vars);

const config = {
  repos: [],
  runtimes: ['claude-code', 'codex', 'opencode'],
  model_connections: [
    { id: A, name: 'Alpha', models: ['gpt-5'], model_runtimes: { 'gpt-5': ['codex', 'opencode'] } },
    { id: B, name: 'Beta', models: ['gpt-5'], model_runtimes: { 'gpt-5': ['opencode'] } },
  ],
  github_connected: true,
  github_install_url: null,
} as AgentConfigApi;

describe('model choices', () => {
  it('gives every source of one model name its own identity', () => {
    const keys = offeredChoices(config, 'opencode').map(choiceKey);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toEqual([`["${A}","gpt-5"]`, `["${B}","gpt-5"]`]);
    // A name that looks like a key cannot pass for one.
    expect(choiceKey({ model: '[odd]', connectionId: null })).toBe('[null,"[odd]"]');
  });

  it('keys and labels a model by its name alone where only one source offers it', () => {
    const picker = modelPicker(
      config,
      offeredChoices(config, 'codex'),
      {
        model: 'gpt-5',
        connectionId: A,
      },
      () => {},
      t,
    );
    expect(picker.options).toEqual(['gpt-5']);
    expect(picker.value).toBe('gpt-5');
    // Two connections list it, so the label still says which one this is.
    expect(picker.optionLabels['gpt-5']).toBe('gpt-5 · Alpha');
  });

  it('filters connections by the runtime’s protocol', () => {
    expect(offeredChoices(config, 'claude-code')).toEqual([]);
    expect(offeredChoices(config, 'codex').map((choice) => choice.connectionId)).toEqual([A]);
  });

  it('reads a legacy draft string as a model with no source, never as an encoded key', () => {
    expect(storedChoice(`["${A}","gpt-5"]`, undefined)).toEqual({
      model: `["${A}","gpt-5"]`,
      connectionId: null,
    });
    expect(storedChoice('gpt-5', '')).toEqual({ model: 'gpt-5', connectionId: null });
    expect(storedChoice('', A)).toEqual({ model: '', connectionId: null });
  });

  it('labels and keeps an unavailable personal choice instead of swapping it', () => {
    const gone = { model: 'gpt-5', connectionId: 'mconn_cccccccccccccccccccccccccccccccc' };
    const picker = modelPicker(config, offeredChoices(config, 'codex'), gone, () => {}, t);
    expect(picker.value).toBe(choiceKey(gone));
    expect(picker.options[0]).toBe(picker.value);
    expect(picker.optionLabels[picker.value]).toBe('gpt-5 · personal connection (unavailable)');
    expect(picker.optionLabels[choiceKey({ model: 'gpt-5', connectionId: A })]).toBe(
      'gpt-5 · Alpha',
    );
    expect(picker.options).toHaveLength(2);
  });

  it('offers nothing to pick when nothing can run, so the inherited model is named', () => {
    const empty = { ...config, model_connections: [] };
    const inherited = { model: 'gpt-5', connectionId: A };
    expect(turnModelPicker(empty, 'codex', inherited, () => {}, t, inherited)).toBeNull();
  });
});

describe('modelOverride', () => {
  const parent = { model: 'gpt-5', modelConnectionId: A };

  it('sends nothing for the inherited pair', () => {
    expect(modelOverride(parent, { model: 'gpt-5', connectionId: A })).toEqual({});
  });

  it('sends the model again when only the source changes', () => {
    expect(modelOverride(parent, { model: 'gpt-5', connectionId: B })).toEqual({
      model: 'gpt-5',
      model_connection_id: B,
    });
    expect(modelOverride(parent, { model: 'gpt-5', connectionId: null })).toEqual({
      model: 'gpt-5',
    });
    expect(
      modelOverride(
        { model: 'gpt-5', modelConnectionId: null },
        { model: 'gpt-5', connectionId: A },
      ),
    ).toEqual({ model: 'gpt-5', model_connection_id: A });
  });
});

describe('per-model availability', () => {
  const mixed = {
    ...config,
    model_connections: [
      {
        id: A,
        name: 'Mixed',
        models: ['verified', 'fresh'],
        // `fresh` was added after verification: it is listed, but not usable yet.
        model_runtimes: { verified: ['claude-code', 'codex', 'kilo', 'opencode', 'pi'] },
      },
    ],
  } as AgentConfigApi;

  it('offers only the models verified for the agent, never the whole connection', () => {
    for (const runtime of ['codex', 'claude-code', 'pi']) {
      expect(offeredChoices(mixed, runtime)).toEqual([{ model: 'verified', connectionId: A }]);
    }
    expect(offeredChoices(mixed, 'goose')).toEqual([]);
  });

  it('never names the API an agent reaches a model on', () => {
    const choice = { model: 'verified', connectionId: A };
    expect(choiceLabel(mixed, choice, t, { runtime: 'codex' })).toBe('verified');
    expect(
      choiceLabel(mixed, { model: 'fresh', connectionId: A }, t, {
        runtime: 'codex',
        unavailable: true,
      }),
    ).toBe('fresh (unavailable)');
  });
});

describe('models the deployment provides', () => {
  const P = 'oconn_dddddddddddddddddddddddddddddddd';
  const S = 'oconn_ffffffffffffffffffffffffffffffff';
  /** The oldest provided source, with three models. */
  const house = {
    id: P,
    name: 'House models',
    models: ['gpt-5', 'house-fast', 'house-open'],
    model_runtimes: {
      'gpt-5': ['codex', 'opencode'],
      'house-fast': ['codex'],
      'house-open': ['codex'],
    },
  };
  /** A second provided source, with two models. */
  const spentHouse = {
    id: S,
    name: 'Spent house',
    models: ['house-spent', 'house-idle'],
    model_runtimes: { 'house-spent': ['codex'], 'house-idle': ['codex'] },
  };
  const provided = {
    ...config,
    model_connections_enabled: true,
    provided_sources: [house, spentHouse],
  } as AgentConfigApi;
  const pick = (model: string, connectionId = P) => ({ model, connectionId });
  const codex = (loaded: AgentConfigApi = provided) =>
    modelPicker(loaded, listedChoices(loaded, 'codex'), NO_MODEL, () => {}, t);

  it('lists provided models first, each with its source id, filtered by the agent', () => {
    expect(listedChoices(provided, 'codex')).toEqual([
      pick('gpt-5'),
      pick('house-fast'),
      pick('house-open'),
      pick('house-spent', S),
      pick('house-idle', S),
      pick('gpt-5', A),
    ]);
    // Only what each agent can run, exactly as for the owner's own connections.
    expect(listedChoices(provided, 'opencode')).toEqual([
      pick('gpt-5'),
      pick('gpt-5', A),
      pick('gpt-5', B),
    ]);
    expect(listedChoices(provided, 'claude-code')).toEqual([]);
  });

  it('groups the picker into provided models and the owner’s connections when both exist', () => {
    const picker = codex();
    const mine = choiceKey(pick('gpt-5', A));
    expect(picker.optionGroups).toEqual({
      [choiceKey(pick('gpt-5'))]: 'Provided by this deployment',
      'house-fast': 'Provided by this deployment',
      'house-open': 'Provided by this deployment',
      'house-spent': 'Provided by this deployment',
      'house-idle': 'Provided by this deployment',
      [mine]: 'Your connections',
    });
    // One kind of source alone needs no headings.
    const only = { ...provided, model_connections: [] } as AgentConfigApi;
    expect(codex(only).optionGroups).toBeUndefined();
    expect(
      modelPicker(config, listedChoices(config, 'opencode'), NO_MODEL, () => {}, t).optionGroups,
    ).toBeUndefined();
  });

  it('names the source on a model more than one source lists, and only there', () => {
    const picker = codex();
    expect(picker.optionLabels[choiceKey(pick('gpt-5'))]).toBe('gpt-5 · House models');
    expect(picker.optionLabels[choiceKey(pick('gpt-5', A))]).toBe('gpt-5 · Alpha');
    expect(picker.optionLabels['house-fast']).toBe('house-fast');
  });

  it('offers every model of every provided source, with nothing to count down', () => {
    const chosen: unknown[] = [];
    const picker = modelPicker(
      provided,
      listedChoices(provided, 'codex'),
      NO_MODEL,
      (choice) => chosen.push(choice),
      t,
    );
    expect(picker).not.toHaveProperty('optionNotes');
    expect(picker).not.toHaveProperty('disabledOptions');
    expect(picker.optionLabels['house-spent']).toBe('house-spent');
    picker.onChange('house-spent');
    expect(chosen).toEqual([pick('house-spent', S)]);
    expect(offeredChoices(provided, 'codex')).toContainEqual(pick('house-spent', S));
    expect(isModelUnavailable(provided, 'codex', pick('house-spent', S))).toBe(false);
  });

  it('finds the source name of a turn that ran on a provided connection', () => {
    // A turn's `modelConnectionId` is the source id: `oconn_…` resolves like `mconn_…`.
    expect(choiceLabel(provided, turnChoice({ model: 'gpt-5', modelConnectionId: P }), t)).toBe(
      'gpt-5 · House models',
    );
    expect(
      choiceLabel(provided, turnChoice({ model: 'house-fast', modelConnectionId: P }), t),
    ).toBe('house-fast');
    // A provided source that is gone is never called a personal connection.
    const gone = pick('gpt-5', 'oconn_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee');
    expect(choiceLabel(provided, gone, t, { unavailable: true })).toBe(
      'gpt-5 · provided by this deployment (unavailable)',
    );
  });

  it('resolves a default that names a provided source', () => {
    expect(
      resolvedChoice({
        ...provided,
        default_model: 'house-fast',
        default_model_connection_id: P,
      }),
    ).toEqual(pick('house-fast'));
  });

  it('offers nothing where model connections are switched off', () => {
    const off = { ...provided, model_connections_enabled: false } as AgentConfigApi;
    expect(listedChoices(off, 'codex')).toEqual([]);
    expect(offeredChoices(off, 'codex')).toEqual([]);
  });

  it('submits a provided model like any other: the model and the source id', () => {
    expect(modelOverride({ model: 'gpt-5', modelConnectionId: A }, pick('gpt-5'))).toEqual({
      model: 'gpt-5',
      model_connection_id: P,
    });
  });
});
