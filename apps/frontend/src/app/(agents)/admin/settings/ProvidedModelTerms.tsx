'use client';

import { useId, useState } from 'react';

import { useT } from '@/components/providers/LocaleProvider';
import {
  MAX_GLOBAL_CONCURRENCY,
  MAX_PRICE_MICROUSD,
  setOperatorModelTerms,
  type OperatorModelConnection,
  type OperatorModelTerms,
} from '@/lib/api/adminOperatorModels';
import { formatUsd } from '@/lib/money';

import {
  countText,
  initialTerms,
  isAccessDenied,
  providedFailure,
  refusedTermsFields,
  sameTerms,
  termsBlank,
  termsProblems,
  termsValue,
  type TermsField,
  type TermsFields,
} from './providedModelsForm';

const inputClass = 'ui-input mt-1 block min-h-11 w-full rounded-lg';
const labelClass = 'block text-xs font-medium text-muted';

type Translate = ReturnType<typeof useT>;

/** A price per million tokens, to the last decimal it has. */
function priceText(price: number): string {
  return formatUsd(price, { precise: true });
}

/** One model's saved terms, in a sentence for each thing that was set. */
function savedSummary(terms: OperatorModelTerms | undefined, t: Translate): string {
  if (!terms) return t('adminModels.terms.unset');
  let line = t('adminModels.terms.saved', {
    input: priceText(terms.input_price),
    output: priceText(terms.output_price),
  });
  if (typeof terms.cache_read_price === 'number') {
    const price = priceText(terms.cache_read_price);
    line = t('adminModels.terms.savedCacheRead', { line, price });
  }
  if (typeof terms.cache_write_price === 'number') {
    const price = priceText(terms.cache_write_price);
    line = t('adminModels.terms.savedCacheWrite', { line, price });
  }
  if (terms.named_only) line = t('adminModels.terms.savedNamedOnly', { line });
  if (typeof terms.global_concurrency === 'number') {
    const count = countText(terms.global_concurrency);
    line = t('adminModels.terms.savedCap', { line, count });
  }
  return line;
}

/** The ids that describe a control, as `aria-describedby` takes them. */
function describedBy(...ids: (string | false)[]): string | undefined {
  return ids.filter(Boolean).join(' ') || undefined;
}

/** A labelled number typed as text: a price in dollars, or a count. */
function NumberField({
  id,
  label,
  value,
  decimal = false,
  placeholder,
  invalid,
  describedBy: described,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  /** A price, which has decimals; otherwise a whole count. */
  decimal?: boolean;
  placeholder?: string;
  invalid: boolean;
  describedBy?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      <input
        id={id}
        className={inputClass}
        inputMode={decimal ? 'decimal' : 'numeric'}
        value={value}
        disabled={disabled}
        maxLength={decimal ? 20 : 4}
        autoComplete="off"
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        aria-describedby={described}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

/** What is wrong with a row, one line for each kind of field that is. */
function TermsProblems({
  id,
  prices,
  cache,
  cap,
  max,
}: {
  id: string;
  prices: boolean;
  cache: boolean;
  cap: boolean;
  /** The most a price may be, as it is read. */
  max: string;
}) {
  const t = useT();
  const lines = [
    { id: `${id}-prices`, shown: prices, text: t('adminModels.terms.invalidPrices', { max }) },
    { id: `${id}-cache`, shown: cache, text: t('adminModels.terms.invalidCache', { max }) },
    {
      id: `${id}-capProblem`,
      shown: cap,
      text: t('adminModels.terms.invalidCap', { concurrency: countText(MAX_GLOBAL_CONCURRENCY) }),
    },
  ];
  return (
    <>
      {lines.map((line) =>
        line.shown ? (
          <p key={line.id} id={line.id} className="mt-1 text-xs leading-5 text-status-error">
            {line.text}
          </p>
        ) : null,
      )}
    </>
  );
}

/**
 * One model's prices per million tokens, whether it is for named accounts
 * only, and its optional deployment-wide concurrency cap, saved together.
 */
function ModelTermsRow({
  connection,
  model,
  unitId,
  onChange,
  onAccessDenied,
}: {
  connection: OperatorModelConnection;
  model: string;
  /** The line that says what unit a price is in. */
  unitId: string;
  onChange: (connection: OperatorModelConnection) => void;
  onAccessDenied?: (cause: unknown) => void;
}) {
  const t = useT();
  const id = useId();
  const saved = connection.terms?.[model];
  const [fields, setFields] = useState<TermsFields>(() => initialTerms(saved));
  // The fields a refused save named, until the row is edited again.
  const [refused, setRefused] = useState<Partial<Record<TermsField, true>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const terms = termsValue(fields);
  const changed = !saved || (terms !== null && !sameTerms(terms, saved));
  // A model with no prices and nothing typed has nothing wrong with it yet.
  const untouched = !saved && termsBlank(fields);
  const problems = untouched ? refused : { ...termsProblems(fields), ...refused };
  const wrong = (...names: TermsField[]) => names.some((name) => problems[name] === true);
  const pricesWrong = wrong('input_price', 'output_price');
  const cacheWrong = wrong('cache_read_price', 'cache_write_price');
  const capWrong = wrong('global_concurrency');

  /** Any edit after a refusal: what it said described the row as it was. */
  function edit<Name extends keyof TermsFields>(name: Name, value: TermsFields[Name]) {
    setFields((current) => ({ ...current, [name]: value }));
    setRefused({});
    setError('');
  }

  async function save() {
    if (busy || terms === null) return;
    setBusy(true);
    setError('');
    try {
      onChange(await setOperatorModelTerms(connection.id, { model, ...terms }));
    } catch (cause) {
      if (isAccessDenied(cause)) onAccessDenied?.(cause);
      setRefused(refusedTermsFields(cause));
      setError(providedFailure(cause, t));
    } finally {
      setBusy(false);
    }
  }

  /** A price field: it is described by the unit, and by what is wrong with it. */
  const price = (name: TermsField, label: string, problemId: string, optional = false) => (
    <NumberField
      id={`${id}-${name}`}
      label={label}
      value={fields[name]}
      decimal
      placeholder={optional ? t('adminModels.terms.atInputPrice') : undefined}
      invalid={wrong(name)}
      describedBy={describedBy(wrong(name) && problemId, unitId)}
      disabled={busy}
      onChange={(value) => edit(name, value)}
    />
  );

  return (
    <li className="py-3" aria-label={model}>
      <p className="break-all font-mono text-[13px]">{model}</p>
      <p className={`mt-0.5 text-xs ${saved ? 'text-muted' : 'text-status-warning'}`}>
        {savedSummary(saved, t)}
      </p>
      {/* Bottoms aligned: a label that wraps must not push its field out of the row. */}
      <div className="mt-2 grid gap-2 sm:grid-cols-2 sm:items-end lg:grid-cols-4">
        {price('input_price', t('adminModels.terms.inputPrice'), `${id}-prices`)}
        {price('output_price', t('adminModels.terms.outputPrice'), `${id}-prices`)}
        {price('cache_read_price', t('adminModels.terms.cacheReadPrice'), `${id}-cache`, true)}
        {price('cache_write_price', t('adminModels.terms.cacheWritePrice'), `${id}-cache`, true)}
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <NumberField
          id={`${id}-cap`}
          label={t('adminModels.terms.globalConcurrency')}
          value={fields.global_concurrency}
          placeholder={t('adminModels.terms.noCap')}
          invalid={capWrong}
          describedBy={describedBy(capWrong && `${id}-capProblem`)}
          disabled={busy}
          onChange={(value) => edit('global_concurrency', value)}
        />
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4 shrink-0"
            checked={fields.named_only}
            disabled={busy}
            onChange={(event) => edit('named_only', event.target.checked)}
          />
          <span>{t('adminModels.terms.namedOnly')}</span>
        </label>
        <button
          type="button"
          className="ui-button ui-button-secondary min-h-11"
          disabled={busy || terms === null || !changed}
          aria-busy={busy || undefined}
          aria-label={t('adminModels.terms.saveFor', { model })}
          onClick={() => void save()}
        >
          {t(busy ? 'connections.busy' : 'connections.save')}
        </button>
      </div>
      <TermsProblems
        id={id}
        prices={pricesWrong}
        cache={cacheWrong}
        cap={capWrong}
        max={formatUsd(MAX_PRICE_MICROUSD)}
      />
      {error ? (
        <p role="alert" className="mt-1 text-sm text-status-error">
          {error}
        </p>
      ) : null}
    </li>
  );
}

/**
 * Each listed model's terms: what its tokens cost per million (input and
 * output, and cached input and cache writes where the provider prices them
 * apart), whether it is offered only to named accounts, and optionally how
 * many requests may be in flight across the whole deployment. Every model
 * needs prices before the connection can be enabled.
 */
export function ProvidedModelTerms({
  connection,
  onChange,
  onAccessDenied,
}: {
  connection: OperatorModelConnection;
  onChange: (connection: OperatorModelConnection) => void;
  onAccessDenied?: (cause: unknown) => void;
}) {
  const t = useT();
  const id = useId();
  return (
    <section aria-labelledby={`${id}-title`} className="mt-4">
      <h4 id={`${id}-title`} className="text-sm font-semibold">
        {t('adminModels.terms.title')}
      </h4>
      <p className="mt-1 text-xs leading-5 text-muted">{t('adminModels.terms.description')}</p>
      <p id={`${id}-unit`} className="mt-1 text-xs leading-5 text-muted">
        {t('adminModels.terms.unit')}
      </p>
      <ul className="mt-1 divide-y divide-border">
        {connection.models.map((model) => (
          <ModelTermsRow
            // Saved terms restart the row from what the server now holds.
            key={`${model}:${JSON.stringify(connection.terms?.[model] ?? null)}`}
            connection={connection}
            model={model}
            unitId={`${id}-unit`}
            onChange={onChange}
            onAccessDenied={onAccessDenied}
          />
        ))}
      </ul>
    </section>
  );
}
