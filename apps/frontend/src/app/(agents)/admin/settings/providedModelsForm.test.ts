import { describe, expect, it } from 'vitest';

import { translate } from '@/lib/i18n/translate';
import { APIError } from '@/lib/utils/errors';

import {
  initialFields,
  isAccessDenied,
  newProvidedConnection,
  pricesText,
  providedChanges,
  providedFailure,
  providedFieldProblems,
  providedNeedsVerification,
  refusedFields,
  utcDay,
  type ProvidedFields,
} from './providedModelsForm';
import { priced, providedConnection, refusal } from './providedModelsTestFixtures';

const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate('en', key, vars);

const filled: ProvidedFields = {
  name: 'House models',
  base_url: 'https://api.example.com/v1',
  key: 'sk-test',
};

describe('the provided connection form', () => {
  it('accepts a complete form, and names each field that is wrong', () => {
    expect(providedFieldProblems(filled, true)).toEqual({});
    expect(
      providedFieldProblems(
        { name: '', base_url: 'http://api.example.com/v1', key: 'has space' },
        true,
      ),
    ).toEqual({ name: true, base_url: true, api_key: true });
  });

  it('needs a key to create, and keeps the saved one on an edit', () => {
    expect(providedFieldProblems({ ...filled, key: '' }, true)).toEqual({ api_key: true });
    expect(providedFieldProblems({ ...filled, key: '' }, false)).toEqual({});
  });

  it('allows a name of 64 characters, and no more', () => {
    expect(providedFieldProblems({ ...filled, name: 'n'.repeat(64) }, true)).toEqual({});
    expect(providedFieldProblems({ ...filled, name: 'n'.repeat(65) }, true)).toEqual({
      name: true,
    });
  });

  it('creates with a name, a Base URL and a key, and nothing else', () => {
    expect(newProvidedConnection(filled)).toEqual({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-test',
    });
  });

  it('starts an edit from the saved values, never the key', () => {
    expect(initialFields(providedConnection())).toEqual({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      key: '',
    });
    expect(initialFields()).toEqual({ name: '', base_url: '', key: '' });
  });

  it('sends only what an edit changed', () => {
    const saved = providedConnection();
    const unchanged = initialFields(saved);
    expect(providedChanges(saved, unchanged)).toEqual({});
    expect(
      providedChanges(saved, {
        name: 'Renamed',
        base_url: 'https://other.example.com/v1',
        key: 'sk-new',
      }),
    ).toEqual({ name: 'Renamed', base_url: 'https://other.example.com/v1', api_key: 'sk-new' });
  });

  it('reads the provider’s models again only for a new connection, key or Base URL', () => {
    const saved = providedConnection();
    const unchanged = initialFields(saved);
    expect(providedNeedsVerification(undefined, filled)).toBe(true);
    expect(providedNeedsVerification(saved, unchanged)).toBe(false);
    expect(providedNeedsVerification(saved, { ...unchanged, name: 'Renamed' })).toBe(false);
    expect(providedNeedsVerification(saved, { ...unchanged, key: 'sk-new' })).toBe(true);
    expect(
      providedNeedsVerification(saved, { ...unchanged, base_url: 'https://other.example.com/v1' }),
    ).toBe(true);
  });
});

describe('a model’s listed prices', () => {
  it('read in dollars per million tokens, with the cache prices the listing gives', () => {
    expect(pricesText(priced(), t)).toBe('Per million tokens: input $2.80, output $11.20');
    expect(
      pricesText(priced({ cache_read_price: 15_000, cache_write_price: 3_500_000 }), t),
    ).toMatch(
      /^Per million tokens: input \$2\.80, output \$11\.20 · cached input \$0\.015.* · cache writes \$3\.50$/,
    );
    // A free model is listed as free, not as having no price.
    expect(pricesText(priced({ input_price: 0, output_price: 0 }), t)).toMatch(
      /^Per million tokens: input \$0(\.00)?, output \$0(\.00)?$/,
    );
    expect(pricesText(undefined, t)).toBeNull();
  });
});

describe('how a refusal reads', () => {
  it('has a sentence for each refusal the page knows', () => {
    const said = (code: string, status: number) => providedFailure(refusal(code, 'raw', status), t);
    expect(said('operator_model_connection_name_taken', 409)).toBe(
      'Another provided connection already has this name.',
    );
    expect(
      providedFailure(
        new APIError('operator_model_model_taken', 'raw', 409, {
          taken: { model: 'glm-5.3-flash', connection: 'Campus' },
        }),
        t,
      ),
    ).toMatch(/^This key lists glm-5\.3-flash, which Campus already provides\./);
    expect(said('operator_model_connection_not_found', 404)).toMatch(/no longer exists/);
    expect(said('operator_model_listing_failed', 422)).toMatch(/could not be read with this key/);
    expect(said('operator_model_listing_empty', 422)).toBe(
      'The provider lists no model for this key.',
    );
    expect(said('operator_model_listing_too_long', 422)).toMatch(/Limit the key at the provider/);
    expect(said('model_connections_disabled', 403)).toMatch(
      /Model connections are switched off on this deployment/,
    );
    expect(said('operator_model_connection_changed', 409)).toMatch(
      /changed while this was being saved/,
    );
    expect(said('model_connection_verification_timeout', 504)).toMatch(/took too long/);
    // A gateway's own timeout page carries no type: the status says enough.
    expect(said('TIMEOUT_ERROR', 504)).toMatch(/took too long/);
  });

  it('says a refused verification or an invalid field in its own words', () => {
    expect(
      providedFailure(
        refusal('model_connection_verification_failed', 'raw', 422, { models: [] }),
        t,
      ),
    ).toBe('Not saved: no model the provider lists for this key could be verified for any agent.');
    const invalid = refusal('invalid_model_connection', 'raw', 422, {
      fields: ['base_url', 'api_key', 'unknown_field'],
    });
    expect(providedFailure(invalid, t)).toBe('Check the highlighted fields, then try again.');
    expect(refusedFields(invalid)).toEqual({ base_url: true, api_key: true });
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
    expect(isAccessDenied(new APIError('operator_model_connection_not_found', 'No.', 404))).toBe(
      false,
    );
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
