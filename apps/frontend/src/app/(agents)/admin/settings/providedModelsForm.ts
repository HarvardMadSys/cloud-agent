import { sameBaseUrl, validBaseUrl } from '@/components/features/settings/modelConnectionChanges';
import {
  OPERATOR_MAX_NAME_CHARS,
  type OperatorModelConnection,
  type OperatorModelConnectionCreate,
  type OperatorModelConnectionUpdate,
  type OperatorModelPrices,
} from '@/lib/api/adminOperatorModels';
import { invalidFields, refusedModels, takenModel } from '@/lib/api/modelConnections';
import type { Translator } from '@/lib/i18n/translate';
import { formatUsd } from '@/lib/money';
import { APIError, getErrorMessage } from '@/lib/utils/errors';

// What the provided-models form holds, what it sends, and how a refusal
// reads. Kept apart from the components so each rule has one place and a test.
//
// A connection is a name, a Base URL and a key: its models are whatever the
// provider lists for the key, so the form asks for nothing else.

/** What the connection form holds. */
export interface ProvidedFields {
  name: string;
  base_url: string;
  /** Write-only; blank keeps the saved key on an edit. */
  key: string;
}

/** A field the administrator can fix, as the form and the server name it. */
export type ProvidedField = 'name' | 'base_url' | 'api_key';

export const PROVIDED_FIELDS: readonly ProvidedField[] = ['name', 'base_url', 'api_key'];

export function initialFields(connection?: OperatorModelConnection): ProvidedFields {
  return { name: connection?.name ?? '', base_url: connection?.base_url ?? '', key: '' };
}

/** What is wrong with each field before anything is sent; empty when nothing is. */
export function providedFieldProblems(
  fields: ProvidedFields,
  creating: boolean,
): Partial<Record<ProvidedField, true>> {
  const problems: Partial<Record<ProvidedField, true>> = {};
  if (!fields.name || fields.name.length > OPERATOR_MAX_NAME_CHARS) problems.name = true;
  if (!validBaseUrl(fields.base_url)) problems.base_url = true;
  if ((creating && !fields.key) || /[^\x21-\x7e]/.test(fields.key)) problems.api_key = true;
  return problems;
}

/** The body of a create. */
export function newProvidedConnection(fields: ProvidedFields): OperatorModelConnectionCreate {
  return { name: fields.name, base_url: fields.base_url, api_key: fields.key };
}

/**
 * Only what the administrator changed, so a rename reads nothing again and a
 * blank key keeps the stored one.
 */
export function providedChanges(
  connection: OperatorModelConnection,
  fields: ProvidedFields,
): OperatorModelConnectionUpdate {
  const changes: OperatorModelConnectionUpdate = {};
  if (fields.name !== connection.name) changes.name = fields.name;
  if (!sameBaseUrl(fields.base_url, connection.base_url)) changes.base_url = fields.base_url;
  if (fields.key) changes.api_key = fields.key;
  return changes;
}

/**
 * Whether saving calls the provider, which can take minutes: a new
 * connection, or a new key or Base URL, reads the provider's models and
 * verifies each. A rename saves at once.
 */
export function providedNeedsVerification(
  connection: OperatorModelConnection | undefined,
  fields: ProvidedFields,
): boolean {
  if (!connection) return true;
  return Boolean(fields.key) || !sameBaseUrl(fields.base_url, connection.base_url);
}

/**
 * A model's listed prices in dollars per million tokens, input then output,
 * with the cache prices the listing gives; `null` when it gives none.
 */
export function pricesText(prices: OperatorModelPrices | undefined, t: Translator): string | null {
  if (!prices) return null;
  const usd = (amount: number) => formatUsd(amount, { precise: true });
  const parts = [
    t('adminModels.card.priceInOut', {
      input: usd(prices.input_price),
      output: usd(prices.output_price),
    }),
  ];
  if (prices.cache_read_price !== null) {
    parts.push(t('adminModels.card.priceCacheRead', { amount: usd(prices.cache_read_price) }));
  }
  if (prices.cache_write_price !== null) {
    parts.push(t('adminModels.card.priceCacheWrite', { amount: usd(prices.cache_write_price) }));
  }
  return parts.join(' · ');
}

/** The refusal of a create, a verifying edit or an enable while model connections are off. */
const FEATURE_OFF = 'model_connections_disabled';

/**
 * Whether a failure means this administrator's access ended. Not every 403
 * does: with model connections switched off for the deployment the server
 * refuses to add, verify or enable a connection with one, and the
 * administrator is still an administrator.
 */
export function isAccessDenied(cause: unknown): boolean {
  if (!(cause instanceof APIError)) return false;
  return cause.statusCode === 401 || (cause.statusCode === 403 && cause.code !== FEATURE_OFF);
}

const REFUSALS = {
  [FEATURE_OFF]: 'adminModels.error.featureOff',
  operator_model_connection_changed: 'connections.changed',
  model_connection_check_limit: 'connections.limited',
  model_connection_verification_timeout: 'connections.timedOut',
  operator_model_connection_name_taken: 'adminModels.error.nameTaken',
  operator_model_connection_not_found: 'adminModels.error.notFound',
  operator_model_listing_failed: 'adminModels.error.listingFailed',
  operator_model_listing_empty: 'adminModels.error.listingEmpty',
  operator_model_listing_too_long: 'adminModels.error.listingTooLong',
} as const;

/**
 * Why a request failed, in the administrator's words. Refusals this page
 * knows get its own sentence; anything else shows what the server said.
 */
export function providedFailure(cause: unknown, t: Translator): string {
  if (refusedModels(cause)) return t('adminModels.error.noneUsable');
  if (invalidFields(cause)) return t('connections.invalid');
  if (cause instanceof APIError) {
    const taken = takenModel(cause);
    if (cause.code === 'operator_model_model_taken' && taken)
      return t('adminModels.error.modelTaken', { model: taken.model, name: taken.connection });
    if (Object.hasOwn(REFUSALS, cause.code))
      return t(REFUSALS[cause.code as keyof typeof REFUSALS]);
    if (cause.statusCode === 504) return t('connections.timedOut');
    if (cause.statusCode === 429) return t('connections.limited');
  }
  return cause instanceof APIError ? getErrorMessage(cause) : t('adminModels.error.generic');
}

/** The fields a refused request named, as the connection form's own field names. */
export function refusedFields(cause: unknown): Partial<Record<ProvidedField, true>> {
  const problems: Partial<Record<ProvidedField, true>> = {};
  for (const field of invalidFields(cause) ?? []) {
    const name = field.split('.')[0] as ProvidedField;
    if (PROVIDED_FIELDS.includes(name)) problems[name] = true;
  }
  return problems;
}

/** A UTC day as `YYYY-MM-DD`, `daysAgo` days before `now`. */
export function utcDay(daysAgo = 0, now: number = Date.now()): string {
  return new Date(now - daysAgo * 86_400_000).toISOString().slice(0, 10);
}

/** A whole count, grouped for reading. */
export function countText(value: number): string {
  return value.toLocaleString('en-US');
}
