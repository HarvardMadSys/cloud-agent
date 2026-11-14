import {
  isModelId,
  sameBaseUrl,
  sameList,
  validBaseUrl,
} from '@/components/features/settings/modelConnectionChanges';
import {
  DEFAULT_USER_CONCURRENCY,
  MAX_DAILY_BUDGET_MICROUSD,
  MAX_GLOBAL_CONCURRENCY,
  MAX_PRICE_MICROUSD,
  MAX_USER_CONCURRENCY,
  OPERATOR_MAX_MODELS,
  OPERATOR_MAX_NAME_CHARS,
  refusedEnable,
  type OperatorModelConnection,
  type OperatorModelConnectionCreate,
  type OperatorModelConnectionUpdate,
  type OperatorModelTerms,
  type OperatorTermsMissing,
} from '@/lib/api/adminOperatorModels';
import { invalidFields, refusedModels } from '@/lib/api/modelConnections';
import type { Translator } from '@/lib/i18n/translate';
import { parseUsd, usdFieldValue } from '@/lib/money';
import { APIError, getErrorMessage } from '@/lib/utils/errors';

// What the provided-models forms hold, what they send, and how a refusal
// reads. Kept apart from the components so each rule has one place and a test.
//
// A budget and a price are typed in dollars and sent in micro-dollars, a price
// being per million tokens (`@/lib/money`).

/** What the connection form holds. Numbers stay as typed until they are sent. */
export interface ProvidedFields {
  name: string;
  base_url: string;
  /** Write-only; blank keeps the saved key on an edit. */
  key: string;
  models: string[];
  /** One of `models`, or `''` for none. */
  default_model: string;
  is_default: boolean;
  /** `''` sends no header. */
  end_user_header: string;
  user_concurrency: string;
  /** Dollars each account may spend a day; `''` leaves it to be chosen later. */
  daily_budget: string;
}

/** A field the administrator can fix, as the form and the server name it. */
export type ProvidedField =
  | 'name'
  | 'base_url'
  | 'api_key'
  | 'models'
  | 'default_model'
  | 'end_user_header'
  | 'user_concurrency'
  | 'user_daily_budget_microusd';

export const PROVIDED_FIELDS: readonly ProvidedField[] = [
  'name',
  'base_url',
  'api_key',
  'models',
  'default_model',
  'end_user_header',
  'user_concurrency',
  'user_daily_budget_microusd',
];

/** An HTTP header name: a token of up to 64 characters. */
const HEADER_NAME = /^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$/;

/** A whole number within `[minimum, maximum]` typed into a field, or `null`. */
export function wholeNumber(text: string, minimum: number, maximum: number): number | null {
  const clean = text.trim();
  if (!/^\d+$/.test(clean)) return null;
  const value = Number(clean);
  return value >= minimum && value <= maximum ? value : null;
}

/** Dollars typed into a field, as micro-dollars within `[0, maximum]`, or `null`. */
export function usdAmount(text: string, maximum: number): number | null {
  const amount = parseUsd(text);
  return amount !== null && amount <= maximum ? amount : null;
}

/** The connection's default budget; `null` while none was chosen. */
function defaultBudget(connection: OperatorModelConnection | undefined): number | null {
  return connection?.user_daily_budget_microusd ?? null;
}

export function initialFields(connection?: OperatorModelConnection): ProvidedFields {
  const budget = defaultBudget(connection);
  return {
    name: connection?.name ?? '',
    base_url: connection?.base_url ?? '',
    key: '',
    models: connection?.models ?? [],
    default_model: connection?.default_model ?? '',
    is_default: connection?.is_default ?? false,
    end_user_header: connection?.end_user_header ?? '',
    user_concurrency: String(connection?.user_concurrency ?? DEFAULT_USER_CONCURRENCY),
    daily_budget: budget === null ? '' : usdFieldValue(budget),
  };
}

/** One to as many model IDs as a connection may list, each well formed. */
function modelsAcceptable(models: string[]): boolean {
  return models.length > 0 && models.length <= OPERATOR_MAX_MODELS && models.every(isModelId);
}

/**
 * Whether a daily budget as typed can be saved: an amount, or blank to choose
 * it later. Once one is saved (`chosen`) it can be changed, never taken away.
 */
function budgetAcceptable(text: string, chosen: boolean): boolean {
  if (text.trim() === '') return !chosen;
  return usdAmount(text, MAX_DAILY_BUDGET_MICROUSD) !== null;
}

/**
 * What is wrong with each field before anything is sent; empty when nothing
 * is. `budgetChosen`: the connection being edited already has a daily budget.
 */
export function providedFieldProblems(
  fields: ProvidedFields,
  creating: boolean,
  budgetChosen = false,
): Partial<Record<ProvidedField, true>> {
  const problems: Partial<Record<ProvidedField, true>> = {};
  if (!fields.name || fields.name.length > OPERATOR_MAX_NAME_CHARS) problems.name = true;
  if (!validBaseUrl(fields.base_url)) problems.base_url = true;
  if ((creating && !fields.key) || /[^\x21-\x7e]/.test(fields.key)) problems.api_key = true;
  const models = fields.models;
  if (!modelsAcceptable(models)) problems.models = true;
  if (fields.default_model && !models.includes(fields.default_model)) {
    problems.default_model = true;
  }
  if (fields.end_user_header && !HEADER_NAME.test(fields.end_user_header)) {
    problems.end_user_header = true;
  }
  if (wholeNumber(fields.user_concurrency, 1, MAX_USER_CONCURRENCY) === null) {
    problems.user_concurrency = true;
  }
  if (!budgetAcceptable(fields.daily_budget, budgetChosen)) {
    problems.user_daily_budget_microusd = true;
  }
  return problems;
}

/** The body of a create. `is_default` is not part of it: it is set by an edit. */
export function newProvidedConnection(fields: ProvidedFields): OperatorModelConnectionCreate {
  const budget = usdAmount(fields.daily_budget, MAX_DAILY_BUDGET_MICROUSD);
  return {
    name: fields.name,
    base_url: fields.base_url,
    api_key: fields.key,
    models: fields.models,
    default_model: fields.default_model || null,
    end_user_header: fields.end_user_header || null,
    user_concurrency:
      wholeNumber(fields.user_concurrency, 1, MAX_USER_CONCURRENCY) ?? DEFAULT_USER_CONCURRENCY,
    // Left blank, the budget is not sent: it is chosen before enabling.
    ...(budget === null ? {} : { user_daily_budget_microusd: budget }),
  };
}

/**
 * Only what the administrator changed, so a rename never re-verifies a model
 * and a blank key keeps the stored one. `''` clears the default model and the
 * end-user header.
 */
export function providedChanges(
  connection: OperatorModelConnection,
  fields: ProvidedFields,
): OperatorModelConnectionUpdate {
  const changes: OperatorModelConnectionUpdate = {};
  if (fields.name !== connection.name) changes.name = fields.name;
  if (!sameBaseUrl(fields.base_url, connection.base_url)) changes.base_url = fields.base_url;
  if (fields.key) changes.api_key = fields.key;
  if (!sameList(fields.models, connection.models)) changes.models = fields.models;
  if (fields.default_model !== (connection.default_model ?? '')) {
    changes.default_model = fields.default_model;
  }
  if (fields.is_default !== connection.is_default) changes.is_default = fields.is_default;
  if (fields.end_user_header !== (connection.end_user_header ?? '')) {
    changes.end_user_header = fields.end_user_header;
  }
  const concurrency = wholeNumber(fields.user_concurrency, 1, MAX_USER_CONCURRENCY);
  if (concurrency !== null && concurrency !== connection.user_concurrency) {
    changes.user_concurrency = concurrency;
  }
  const budget = usdAmount(fields.daily_budget, MAX_DAILY_BUDGET_MICROUSD);
  if (budget !== null && budget !== defaultBudget(connection)) {
    changes.user_daily_budget_microusd = budget;
  }
  return changes;
}

/**
 * Whether saving calls the provider, which can take minutes: a new connection,
 * a new key or Base URL, or an added model. Anything else saves at once.
 */
export function providedNeedsVerification(
  connection: OperatorModelConnection | undefined,
  fields: ProvidedFields,
): boolean {
  if (!connection) return true;
  if (fields.key || !sameBaseUrl(fields.base_url, connection.base_url)) return true;
  return fields.models.some((model) => !connection.models.includes(model));
}

/**
 * What a connection still lacks before it offers anything: a default budget,
 * and prices for the listed models that have none. Until both are there it
 * cannot be enabled. One that is enabled without them, because it was set up
 * before budgets existed or was given a model since, offers nothing, or not
 * those models.
 */
export function missingTerms(connection: OperatorModelConnection): OperatorTermsMissing {
  const terms = connection.terms ?? {};
  return {
    budget: defaultBudget(connection) === null,
    models: connection.models.filter((model) => !Object.hasOwn(terms, model)),
  };
}

/**
 * Why a connection that lacks `missing` cannot be enabled, naming what it
 * lacks: a daily budget, the models without prices, or both; `''` when it
 * lacks nothing. The card says it beside Enable from its own copy of the
 * connection, and a refused enable says it from what the server answered.
 */
export function notEnabledYet(missing: OperatorTermsMissing | null, t: Translator): string {
  const models = missing?.models.join(', ') ?? '';
  if (missing?.budget && models) return t('adminModels.card.enableNeedsBoth', { models });
  if (missing?.budget) return t('adminModels.card.enableNeedsBudget');
  return models ? t('adminModels.card.enableNeedsPrices', { models }) : '';
}

/** What one model's row holds. Prices stay as typed, in dollars per million tokens. */
export interface TermsFields {
  input_price: string;
  output_price: string;
  /** `''` charges cached input at the input price. */
  cache_read_price: string;
  /** `''` charges cache writes at the input price. */
  cache_write_price: string;
  named_only: boolean;
  /** `''` is no cap. */
  global_concurrency: string;
}

/** A field of a model's row that can be wrong, as the row and the server name it. */
export type TermsField = Exclude<keyof TermsFields, 'named_only'>;

const TERMS_FIELDS: readonly TermsField[] = [
  'input_price',
  'output_price',
  'cache_read_price',
  'cache_write_price',
  'global_concurrency',
];

/** A row as it starts: what is saved for the model, or nothing. */
export function initialTerms(saved: OperatorModelTerms | undefined): TermsFields {
  const text = (price: number | null | undefined) =>
    price === null || price === undefined ? '' : usdFieldValue(price);
  return {
    input_price: text(saved?.input_price),
    output_price: text(saved?.output_price),
    cache_read_price: text(saved?.cache_read_price),
    cache_write_price: text(saved?.cache_write_price),
    named_only: saved?.named_only ?? false,
    global_concurrency: saved?.global_concurrency ? String(saved.global_concurrency) : '',
  };
}

/** Whether nothing has been entered in a row. */
export function termsBlank(fields: TermsFields): boolean {
  return !fields.named_only && TERMS_FIELDS.every((field) => fields[field].trim() === '');
}

/** A price that must be there: from 0, which is free; `undefined` when it is not one. */
function priceValue(text: string): number | undefined {
  return usdAmount(text, MAX_PRICE_MICROUSD) ?? undefined;
}

/**
 * An optional cache price: `null` when blank, which charges those tokens at
 * the input price; `undefined` when it is not a price above zero. Zero is not
 * one: it would read as free, and the server refuses it.
 */
export function cachePriceValue(text: string): number | null | undefined {
  if (!text.trim()) return null;
  return usdAmount(text, MAX_PRICE_MICROUSD) || undefined;
}

/** An optional concurrency cap: `null` when blank, `undefined` when invalid. */
export function concurrencyValue(text: string): number | null | undefined {
  if (!text.trim()) return null;
  return wholeNumber(text, 1, MAX_GLOBAL_CONCURRENCY) ?? undefined;
}

/** Each field of a row as it would be sent; `undefined` where it cannot be. */
function termsValues(fields: TermsFields) {
  return {
    input_price: priceValue(fields.input_price),
    output_price: priceValue(fields.output_price),
    cache_read_price: cachePriceValue(fields.cache_read_price),
    cache_write_price: cachePriceValue(fields.cache_write_price),
    global_concurrency: concurrencyValue(fields.global_concurrency),
  };
}

/** What is wrong with a row before it is sent; empty when nothing is. */
export function termsProblems(fields: TermsFields): Partial<Record<TermsField, true>> {
  const values = termsValues(fields);
  const problems: Partial<Record<TermsField, true>> = {};
  for (const field of TERMS_FIELDS) if (values[field] === undefined) problems[field] = true;
  return problems;
}

/** The terms a row would save; `null` while a field is wrong. */
export function termsValue(fields: TermsFields): OperatorModelTerms | null {
  const { input_price, output_price, cache_read_price, cache_write_price, global_concurrency } =
    termsValues(fields);
  if (
    input_price === undefined ||
    output_price === undefined ||
    cache_read_price === undefined ||
    cache_write_price === undefined ||
    global_concurrency === undefined
  ) {
    return null;
  }
  return {
    input_price,
    output_price,
    cache_read_price,
    cache_write_price,
    named_only: fields.named_only,
    global_concurrency,
  };
}

/** Whether two models' terms are the same in every field. */
export function sameTerms(a: OperatorModelTerms, b: OperatorModelTerms): boolean {
  return a.named_only === b.named_only && TERMS_FIELDS.every((field) => a[field] === b[field]);
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
  // Said only when a refused enable does not name what is missing: see `providedFailure`.
  operator_model_terms_missing: 'adminModels.error.termsMissing',
  operator_model_not_listed: 'adminModels.error.modelNotListed',
  account_not_found: 'adminModels.error.accountNotFound',
} as const;

/**
 * Why a request failed, in the administrator's words. Refusals this page
 * knows get its own sentence; anything else shows what the server said.
 */
export function providedFailure(cause: unknown, t: Translator): string {
  if (refusedModels(cause)) return t('connections.refused');
  if (invalidFields(cause)) return t('connections.invalid');
  // A refused enable names what the connection lacks: said as the card says it.
  const lacking = notEnabledYet(refusedEnable(cause), t);
  if (lacking) return lacking;
  if (cause instanceof APIError) {
    if (Object.hasOwn(REFUSALS, cause.code))
      return t(REFUSALS[cause.code as keyof typeof REFUSALS]);
    if (cause.statusCode === 504) return t('connections.timedOut');
    if (cause.statusCode === 429) return t('connections.limited');
  }
  return cause instanceof APIError ? getErrorMessage(cause) : t('adminModels.error.generic');
}

/** The fields among `known` a refused request named, by the part before any index. */
function namedFields<Field extends string>(
  cause: unknown,
  known: readonly Field[],
): Partial<Record<Field, true>> {
  const problems: Partial<Record<Field, true>> = {};
  for (const field of invalidFields(cause) ?? []) {
    const name = field.split('.')[0] as Field;
    if (known.includes(name)) problems[name] = true;
  }
  return problems;
}

/** The fields a refused request named, as the connection form's own field names. */
export function refusedFields(cause: unknown): Partial<Record<ProvidedField, true>> {
  return namedFields(cause, PROVIDED_FIELDS);
}

/** The fields of a model's row a refused request named. */
export function refusedTermsFields(cause: unknown): Partial<Record<TermsField, true>> {
  return namedFields(cause, TERMS_FIELDS);
}

/** A UTC day as `YYYY-MM-DD`, `daysAgo` days before `now`. */
export function utcDay(daysAgo = 0, now: number = Date.now()): string {
  return new Date(now - daysAgo * 86_400_000).toISOString().slice(0, 10);
}

/** A whole count, grouped for reading. */
export function countText(value: number): string {
  return value.toLocaleString('en-US');
}
