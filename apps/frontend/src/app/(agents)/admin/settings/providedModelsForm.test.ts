import { describe, expect, it } from 'vitest';

import { translate } from '@/lib/i18n/translate';
import {
  MAX_DAILY_BUDGET_MICROUSD,
  MAX_PRICE_MICROUSD,
  OPERATOR_MAX_MODELS,
} from '@/lib/api/adminOperatorModels';
import { APIError } from '@/lib/utils/errors';

import {
  cachePriceValue,
  concurrencyValue,
  initialFields,
  initialTerms,
  isAccessDenied,
  missingTerms,
  newProvidedConnection,
  notEnabledYet,
  providedChanges,
  providedFailure,
  providedFieldProblems,
  providedNeedsVerification,
  refusedFields,
  refusedTermsFields,
  sameTerms,
  termsBlank,
  termsProblems,
  termsValue,
  usdAmount,
  utcDay,
  wholeNumber,
  type ProvidedFields,
  type TermsFields,
} from './providedModelsForm';
import {
  COMPLETE,
  DAILY_BUDGET,
  priced,
  providedConnection,
  refusal,
} from './providedModelsTestFixtures';

const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate('en', key, vars);

const filled: ProvidedFields = {
  name: 'House models',
  base_url: 'https://api.example.com/v1',
  key: 'sk-test',
  models: ['house-fast'],
  default_model: 'house-fast',
  is_default: false,
  end_user_header: 'X-End-User',
  user_concurrency: '4',
  daily_budget: '',
};

describe('the provided connection form', () => {
  it('accepts a complete form, and names each field that is wrong', () => {
    expect(providedFieldProblems(filled, true)).toEqual({});
    expect(
      providedFieldProblems(
        {
          ...filled,
          name: '',
          base_url: 'http://api.example.com/v1',
          key: 'has space',
          models: ['bad id'],
          default_model: 'elsewhere',
          end_user_header: 'X End: User',
          user_concurrency: '65',
          daily_budget: '-1',
        },
        true,
      ),
    ).toEqual({
      name: true,
      base_url: true,
      api_key: true,
      models: true,
      default_model: true,
      end_user_header: true,
      user_concurrency: true,
      user_daily_budget_microusd: true,
    });
  });

  it('needs a key to create, and keeps the saved one on an edit', () => {
    expect(providedFieldProblems({ ...filled, key: '' }, true)).toEqual({ api_key: true });
    expect(providedFieldProblems({ ...filled, key: '' }, false)).toEqual({});
  });

  it('allows a name of 64 characters and as many models as the server does, and no more', () => {
    const ids = (count: number) => Array.from({ length: count }, (_, index) => `model-${index}`);
    expect(providedFieldProblems({ ...filled, name: 'n'.repeat(64) }, true)).toEqual({});
    expect(providedFieldProblems({ ...filled, name: 'n'.repeat(65) }, true)).toEqual({
      name: true,
    });
    const many = { ...filled, default_model: '' };
    expect(providedFieldProblems({ ...many, models: ids(OPERATOR_MAX_MODELS) }, true)).toEqual({});
    expect(providedFieldProblems({ ...many, models: ids(OPERATOR_MAX_MODELS + 1) }, true)).toEqual({
      models: true,
    });
  });

  it('takes a daily budget in dollars, from nothing to a million of them, or none yet', () => {
    const problems = (daily_budget: string) =>
      providedFieldProblems({ ...filled, daily_budget }, true);
    // Left blank, the budget is chosen later, before the connection is enabled.
    for (const amount of ['', '  ', '0', '5', '2.50', '0.000001', ' 12.5 ', '1000000']) {
      expect(problems(amount), amount).toEqual({});
    }
    for (const amount of ['-5', 'plenty', '$5', '1,000', '1.1234567', '1000000.000001', '1e3']) {
      expect(problems(amount), amount).toEqual({ user_daily_budget_microusd: true });
    }
  });

  it('will not take away a daily budget that was chosen', () => {
    const blank = { ...filled, key: '' };
    expect(providedFieldProblems(blank, false, true)).toEqual({
      user_daily_budget_microusd: true,
    });
    // It can be changed, to zero too.
    expect(providedFieldProblems({ ...blank, daily_budget: '0' }, false, true)).toEqual({});
    expect(providedFieldProblems({ ...blank, daily_budget: '7.5' }, false, true)).toEqual({});
  });

  it('creates with what was typed, sending null for what was left out', () => {
    expect(newProvidedConnection(filled)).toEqual({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
      models: ['house-fast'],
      default_model: 'house-fast',
      end_user_header: 'X-End-User',
      user_concurrency: 4,
    });
    expect(
      newProvidedConnection({ ...filled, default_model: '', end_user_header: '' }),
    ).toMatchObject({ default_model: null, end_user_header: null });
    // Where new users start is an edit, never part of the create.
    expect(newProvidedConnection({ ...filled, is_default: true })).not.toHaveProperty('is_default');
  });

  it('creates with the daily budget in micro-dollars, and without one when it was left blank', () => {
    expect(newProvidedConnection(filled)).not.toHaveProperty('user_daily_budget_microusd');
    expect(newProvidedConnection({ ...filled, daily_budget: '5' })).toMatchObject({
      user_daily_budget_microusd: 5_000_000,
    });
    expect(newProvidedConnection({ ...filled, daily_budget: '2.8' })).toMatchObject({
      user_daily_budget_microusd: 2_800_000,
    });
    // Zero is a choice: only accounts given a budget of their own are offered the connection.
    expect(newProvidedConnection({ ...filled, daily_budget: '0' })).toMatchObject({
      user_daily_budget_microusd: 0,
    });
  });

  it('sends only what an edit changed', () => {
    const saved = providedConnection({
      default_model: 'house-fast',
      end_user_header: 'X-End-User',
    });
    const unchanged = initialFields(saved);
    expect(providedChanges(saved, unchanged)).toEqual({});
    expect(providedChanges(saved, { ...unchanged, name: 'Renamed' })).toEqual({ name: 'Renamed' });
    // The same address typed with a trailing slash is not a change.
    expect(
      providedChanges(saved, { ...unchanged, base_url: 'https://api.example.com/v1/' }),
    ).toEqual({});
    expect(
      providedChanges(saved, {
        ...unchanged,
        key: 'sk-new',
        models: ['house-fast'],
        is_default: true,
        user_concurrency: '8',
      }),
    ).toEqual({
      api_key: 'sk-new',
      models: ['house-fast'],
      is_default: true,
      user_concurrency: 8,
    });
  });

  it('starts an edit from the saved daily budget, and sends it only when the amount changed', () => {
    const none = providedConnection();
    expect(initialFields(none).daily_budget).toBe('');
    expect(providedChanges(none, initialFields(none))).toEqual({});
    expect(providedChanges(none, { ...initialFields(none), daily_budget: '0' })).toEqual({
      user_daily_budget_microusd: 0,
    });

    const saved = providedConnection({ user_daily_budget_microusd: DAILY_BUDGET });
    const fields = initialFields(saved);
    expect(fields.daily_budget).toBe('5.00');
    expect(providedChanges(saved, fields)).toEqual({});
    // The same amount typed another way is not a change.
    expect(providedChanges(saved, { ...fields, daily_budget: '5' })).toEqual({});
    expect(providedChanges(saved, { ...fields, daily_budget: '7.5' })).toEqual({
      user_daily_budget_microusd: 7_500_000,
    });
    // A budget of a fraction of a cent is shown whole, so saving does not round it.
    const small = providedConnection({ user_daily_budget_microusd: 4_200 });
    expect(initialFields(small).daily_budget).toBe('0.0042');
    expect(providedChanges(small, initialFields(small))).toEqual({});
  });

  it('clears the default model and the end-user header with an empty string', () => {
    const saved = providedConnection({
      default_model: 'house-fast',
      end_user_header: 'X-End-User',
    });
    expect(
      providedChanges(saved, { ...initialFields(saved), default_model: '', end_user_header: '' }),
    ).toEqual({ default_model: '', end_user_header: '' });
  });

  it('verifies with the provider only for a new key, a new Base URL or an added model', () => {
    const saved = providedConnection();
    const fields = initialFields(saved);
    expect(providedNeedsVerification(undefined, filled)).toBe(true);
    expect(providedNeedsVerification(saved, fields)).toBe(false);
    expect(providedNeedsVerification(saved, { ...fields, name: 'Renamed' })).toBe(false);
    expect(providedNeedsVerification(saved, { ...fields, models: ['house-fast'] })).toBe(false);
    // A budget is the deployment's own business: the provider is not asked about it.
    expect(providedNeedsVerification(saved, { ...fields, daily_budget: '5' })).toBe(false);
    expect(providedNeedsVerification(saved, { ...fields, key: 'sk-new' })).toBe(true);
    expect(
      providedNeedsVerification(saved, { ...fields, base_url: 'https://other.example.com/v1' }),
    ).toBe(true);
    expect(
      providedNeedsVerification(saved, { ...fields, models: [...saved.models, 'house-new'] }),
    ).toBe(true);
  });
});

describe('what a connection still lacks before it offers anything', () => {
  it('is a daily budget and prices for every listed model', () => {
    expect(missingTerms(providedConnection())).toEqual({
      budget: true,
      models: ['house-fast', 'house-deep'],
    });
    expect(missingTerms(providedConnection(COMPLETE))).toEqual({ budget: false, models: [] });
  });

  it('counts a budget of zero and a price of zero as choices somebody made', () => {
    const free = priced({ input_price: 0, output_price: 0 });
    expect(
      missingTerms(
        providedConnection({ user_daily_budget_microusd: 0, terms: { 'house-fast': free } }),
      ),
    ).toEqual({ budget: false, models: ['house-deep'] });
  });

  it('reads a connection answered without a budget or terms as lacking both', () => {
    // What a control plane from before budgets answers: neither field.
    const older = { ...providedConnection() } as Partial<ReturnType<typeof providedConnection>>;
    delete older.user_daily_budget_microusd;
    delete older.terms;
    expect(missingTerms(older as ReturnType<typeof providedConnection>)).toEqual({
      budget: true,
      models: ['house-fast', 'house-deep'],
    });
  });
});

describe('a model’s terms in a form', () => {
  const typed: TermsFields = {
    input_price: '2.8',
    output_price: '11.20',
    cache_read_price: '',
    cache_write_price: '',
    named_only: false,
    global_concurrency: '',
  };

  it('starts blank for a model with no prices, and from what is saved otherwise', () => {
    expect(initialTerms(undefined)).toEqual({
      input_price: '',
      output_price: '',
      cache_read_price: '',
      cache_write_price: '',
      named_only: false,
      global_concurrency: '',
    });
    expect(initialTerms(priced())).toEqual({ ...typed, input_price: '2.80' });
    expect(
      initialTerms(
        priced({
          input_price: 0,
          cache_read_price: 280_000,
          cache_write_price: 3_500_000,
          named_only: true,
          global_concurrency: 8,
        }),
      ),
    ).toEqual({
      input_price: '0.00',
      output_price: '11.20',
      cache_read_price: '0.28',
      cache_write_price: '3.50',
      named_only: true,
      global_concurrency: '8',
    });
    expect(termsBlank(initialTerms(undefined))).toBe(true);
    expect(termsBlank({ ...initialTerms(undefined), named_only: true })).toBe(false);
    expect(termsBlank({ ...initialTerms(undefined), cache_read_price: '0.1' })).toBe(false);
  });

  it('turns dollars per million tokens into the prices sent', () => {
    expect(termsValue(typed)).toEqual(priced());
    expect(
      termsValue({
        input_price: ' 0.15 ',
        output_price: '.6',
        cache_read_price: '0.075',
        cache_write_price: '0.000001',
        named_only: true,
        global_concurrency: '8',
      }),
    ).toEqual({
      input_price: 150_000,
      output_price: 600_000,
      cache_read_price: 75_000,
      cache_write_price: 1,
      named_only: true,
      global_concurrency: 8,
    });
  });

  it('needs an input and an output price, either of which may be zero', () => {
    expect(termsValue({ ...typed, input_price: '0', output_price: '0' })).toMatchObject({
      input_price: 0,
      output_price: 0,
    });
    expect(termsValue({ ...typed, input_price: '' })).toBeNull();
    expect(termsProblems({ ...typed, input_price: '' })).toEqual({ input_price: true });
    expect(termsProblems({ ...typed, output_price: ' ' })).toEqual({ output_price: true });
    expect(termsProblems(initialTerms(undefined))).toEqual({
      input_price: true,
      output_price: true,
    });
    for (const price of ['-1', 'free', '1.1234567', '1000000.000001', '2,8']) {
      expect(termsProblems({ ...typed, output_price: price }), price).toEqual({
        output_price: true,
      });
    }
    expect(termsValue({ ...typed, output_price: '1000000' })?.output_price).toBe(
      MAX_PRICE_MICROUSD,
    );
  });

  it('takes a cache price or none, and never zero', () => {
    expect(cachePriceValue('')).toBeNull();
    expect(cachePriceValue('  ')).toBeNull();
    expect(cachePriceValue('0.28')).toBe(280_000);
    // Zero would read as free; the server refuses it, so the row does first.
    for (const price of ['0', '0.00', '.0', '-0.1', 'none', '1000000.01']) {
      expect(cachePriceValue(price), price).toBeUndefined();
    }
    expect(termsProblems({ ...typed, cache_read_price: '0', cache_write_price: '0.5' })).toEqual({
      cache_read_price: true,
    });
    expect(termsValue({ ...typed, cache_write_price: '0' })).toBeNull();
    expect(termsValue({ ...typed, cache_read_price: '0.28' })).toMatchObject({
      cache_read_price: 280_000,
      cache_write_price: null,
    });
  });

  it('reads an optional concurrency cap: blank is none', () => {
    expect(concurrencyValue('')).toBeNull();
    expect(concurrencyValue('8')).toBe(8);
    expect(concurrencyValue('0')).toBeUndefined();
    expect(concurrencyValue('1025')).toBeUndefined();
    expect(termsProblems({ ...typed, global_concurrency: '1.5' })).toEqual({
      global_concurrency: true,
    });
    expect(wholeNumber('64', 1, 64)).toBe(64);
    expect(wholeNumber('65', 1, 64)).toBeNull();
  });

  it('knows a row that holds what is saved from one that was changed', () => {
    expect(sameTerms(priced(), priced())).toBe(true);
    for (const change of [
      { input_price: 2_800_001 },
      { output_price: 0 },
      { cache_read_price: 280_000 },
      { cache_write_price: 1 },
      { named_only: true },
      { global_concurrency: 8 },
    ]) {
      expect(sameTerms(priced(), priced(change)), JSON.stringify(change)).toBe(false);
    }
    // The saved prices typed another way are the same prices.
    const again = termsValue({ ...initialTerms(priced()), input_price: '2.8000' });
    expect(again && sameTerms(again, priced())).toBe(true);
  });
});

describe('dollars typed into a field', () => {
  it('are micro-dollars up to the most the field takes', () => {
    expect(usdAmount('5', MAX_DAILY_BUDGET_MICROUSD)).toBe(5_000_000);
    expect(usdAmount('1000000', MAX_DAILY_BUDGET_MICROUSD)).toBe(MAX_DAILY_BUDGET_MICROUSD);
    expect(usdAmount('1000000.000001', MAX_DAILY_BUDGET_MICROUSD)).toBeNull();
    expect(usdAmount('', MAX_DAILY_BUDGET_MICROUSD)).toBeNull();
    expect(usdAmount('five', MAX_DAILY_BUDGET_MICROUSD)).toBeNull();
  });
});

describe('how a refusal reads', () => {
  it('has a sentence for each refusal the page knows', () => {
    const said = (code: string, status: number) => providedFailure(refusal(code, 'raw', status), t);
    expect(said('operator_model_connection_name_taken', 409)).toBe(
      'Another provided connection already has this name.',
    );
    expect(said('operator_model_terms_missing', 409)).toBe(
      'Not enabled: the connection needs a daily budget per account and prices for every model first.',
    );
    expect(said('operator_model_connection_not_found', 404)).toMatch(/no longer exists/);
    // A model taken off the connection under an open row or form.
    expect(said('operator_model_not_listed', 404)).toBe(
      'That model is no longer listed on this connection. Reload the page to see the current list.',
    );
    expect(said('account_not_found', 404)).toBe('No account has that email address or id.');
    expect(said('model_connections_disabled', 403)).toMatch(
      /Model connections are switched off on this deployment/,
    );
    expect(said('operator_model_connection_changed', 409)).toMatch(
      /changed while this was being saved/,
    );
    expect(said('model_connection_check_limit', 429)).toMatch(/Too many verifications/);
    expect(said('model_connection_verification_timeout', 504)).toMatch(/took too long/);
    // A gateway's own timeout page carries no type: the status says enough.
    expect(said('TIMEOUT_ERROR', 504)).toMatch(/took too long/);
  });

  it('names what a refused enable says the connection lacks, as the card does', () => {
    const refused = (details?: Record<string, unknown>) =>
      providedFailure(
        refusal(
          'operator_model_terms_missing',
          'Before enabling, choose a daily budget per account and set prices for house-deep.',
          409,
          details,
        ),
        t,
      );
    expect(refused({ budget: true, models: [] })).toBe(
      'It cannot be enabled until a daily budget per account is chosen. Edit the connection to choose one.',
    );
    expect(refused({ budget: false, models: ['house-deep', 'house-new'] })).toBe(
      'It cannot be enabled until every model has prices. Still without prices: house-deep, house-new.',
    );
    expect(refused({ budget: true, models: ['house-deep'] })).toBe(
      'It cannot be enabled until a daily budget per account is chosen and every model has prices. Edit the connection to choose the budget. Still without prices: house-deep.',
    );
    // A refusal that names it only in its sentence gets the page's general one,
    // and so does one that says nothing is missing. The server's sentence is never shown.
    const general =
      'Not enabled: the connection needs a daily budget per account and prices for every model first.';
    for (const details of [
      undefined,
      {},
      { models: ['house-deep'] },
      { budget: true },
      { budget: 'yes', models: ['house-deep'] },
      { budget: false, models: [] },
    ]) {
      expect(refused(details), JSON.stringify(details)).toBe(general);
    }
  });

  it('gives a connection that lacks something one sentence, shared by the card and a refusal', () => {
    expect(notEnabledYet(missingTerms(providedConnection()), t)).toBe(
      'It cannot be enabled until a daily budget per account is chosen and every model has prices. Edit the connection to choose the budget. Still without prices: house-fast, house-deep.',
    );
    expect(notEnabledYet(missingTerms(providedConnection(COMPLETE)), t)).toBe('');
    expect(notEnabledYet(null, t)).toBe('');
    // Another refusal is not read as a refused enable, whatever it carries.
    expect(
      providedFailure(
        refusal('operator_model_connection_not_found', 'Gone.', 404, {
          budget: true,
          models: ['house-deep'],
        }),
        t,
      ),
    ).toMatch(/no longer exists/);
  });

  it('says a refused verification or an invalid field in its own words', () => {
    expect(
      providedFailure(
        refusal('model_connection_verification_failed', 'raw', 422, { models: [] }),
        t,
      ),
    ).toMatch(/a model could not be verified/);
    const invalid = refusal('invalid_model_connection', 'raw', 422, {
      fields: ['base_url', 'models.0', 'user_daily_budget_microusd', 'unknown_field'],
    });
    expect(providedFailure(invalid, t)).toBe('Check the highlighted fields, then try again.');
    expect(refusedFields(invalid)).toEqual({
      base_url: true,
      models: true,
      user_daily_budget_microusd: true,
    });
  });

  it('names the fields of a model’s row the server refused', () => {
    const invalid = refusal('invalid_model_connection', 'raw', 422, {
      fields: ['cache_read_price', 'global_concurrency', 'model', 'base_url'],
    });
    expect(refusedTermsFields(invalid)).toEqual({
      cache_read_price: true,
      global_concurrency: true,
    });
    expect(refusedTermsFields(refusal('operator_model_not_listed', 'raw', 404))).toEqual({});
  });

  it('shows what the server said for a refusal it has no sentence for', () => {
    expect(providedFailure(refusal('provided_unavailable', 'The relay is down.', 503), t)).toBe(
      'The relay is down.',
    );
    expect(providedFailure(new Error('boom'), t)).toBe('The request failed. Try again.');
  });

  it('knows an ended session from any other failure', () => {
    expect(isAccessDenied(new APIError('forbidden', 'No.', 403))).toBe(true);
    expect(isAccessDenied(new APIError('unauthorized', 'No.', 401))).toBe(true);
    expect(isAccessDenied(new APIError('account_not_found', 'No.', 404))).toBe(false);
    // Model connections switched off is refused with a 403 too: the administrator still is one.
    expect(isAccessDenied(new APIError('model_connections_disabled', 'Off.', 403))).toBe(false);
    expect(isAccessDenied(new Error('offline'))).toBe(false);
  });
});

describe('UTC days', () => {
  it('counts back from today in UTC', () => {
    const now = Date.parse('2026-11-12T23:30:00-05:00');
    expect(utcDay(0, now)).toBe('2026-11-13');
    expect(utcDay(6, now)).toBe('2026-11-07');
  });
});
