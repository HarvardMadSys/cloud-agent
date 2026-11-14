'use client';

import { useId, useState, type FormEvent, type ReactNode } from 'react';

import { useT } from '@/components/providers/LocaleProvider';
import { Select } from '@/components/ui/Select';
import {
  MAX_DAILY_BUDGET_MICROUSD,
  setOperatorModelUserAccess,
  setOperatorModelUserBudget,
  type OperatorModelConnection,
  type OperatorModelUser,
} from '@/lib/api/adminOperatorModels';
import { formatUsd } from '@/lib/money';

import { isAccessDenied, providedFailure, usdAmount } from './providedModelsForm';

const inputClass = 'ui-input mt-1 block min-h-11 w-full rounded-lg';
const labelClass = 'block text-xs font-medium text-muted';

interface FormProps {
  connection: OperatorModelConnection;
  /** The account as the server holds it after the write: its whole line in the list. */
  onSaved: (account: OperatorModelUser) => void;
  onAccessDenied?: (cause: unknown) => void;
}

/** One request at a time, with what went wrong said in the administrator's words. */
function useSubmit(onAccessDenied?: (cause: unknown) => void) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(request: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await request();
    } catch (cause) {
      if (isAccessDenied(cause)) onAccessDenied?.(cause);
      setError(providedFailure(cause, t));
    } finally {
      setBusy(false);
    }
  }

  return { busy, error, submit };
}

/** A form's frame: its title, its fields, one action and what went wrong. */
function AccountForm({
  title,
  action,
  busy,
  ready,
  error,
  onSubmit,
  children,
}: {
  title: string;
  action: string;
  busy: boolean;
  /** Whether what was entered can be sent. */
  ready: boolean;
  error: string;
  onSubmit: () => void;
  children: ReactNode;
}) {
  const t = useT();
  function send(event: FormEvent) {
    event.preventDefault();
    if (ready) onSubmit();
  }
  return (
    <form onSubmit={send} aria-label={title} className="mt-3 rounded-lg border border-border p-3">
      <p className="text-sm font-medium">{title}</p>
      {children}
      <button
        type="submit"
        className="ui-button ui-button-secondary mt-3 min-h-11"
        disabled={busy || !ready}
        aria-busy={busy || undefined}
      >
        {busy ? t('connections.busy') : action}
      </button>
      {error ? (
        <p role="alert" className="mt-2 text-sm text-status-error">
          {error}
        </p>
      ) : null}
    </form>
  );
}

/** The account a form is for, named by its email address or its id. */
function AccountField({
  id,
  value,
  disabled,
  onChange,
}: {
  id: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const t = useT();
  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {t('adminModels.accounts.account')}
      </label>
      <input
        id={id}
        className={inputClass}
        value={value}
        disabled={disabled}
        maxLength={320}
        autoComplete="off"
        spellCheck={false}
        placeholder={t('adminModels.accounts.accountPlaceholder')}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

/**
 * Give one account, named by email or id, a daily budget of its own on this
 * connection, in place of the default. Zero withholds the connection from it.
 */
export function SetBudgetForm({ connection, onSaved, onAccessDenied }: FormProps) {
  const t = useT();
  const id = useId();
  const [account, setAccount] = useState('');
  const [amount, setAmount] = useState('');
  const { busy, error, submit } = useSubmit(onAccessDenied);
  const budget = usdAmount(amount, MAX_DAILY_BUDGET_MICROUSD);
  // Nothing typed yet is not a mistake; anything else that is not an amount is.
  const invalid = budget === null && amount.trim() !== '';

  const save = () =>
    submit(async () => {
      if (budget === null) return;
      onSaved(
        await setOperatorModelUserBudget(connection.id, {
          account: account.trim(),
          daily_budget_microusd: budget,
        }),
      );
      setAccount('');
    });

  return (
    <AccountForm
      title={t('adminModels.accounts.budgetTitle')}
      action={t('adminModels.accounts.setBudget')}
      busy={busy}
      ready={budget !== null && account.trim() !== ''}
      error={error}
      onSubmit={() => void save()}
    >
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <AccountField id={`${id}-account`} value={account} disabled={busy} onChange={setAccount} />
        <div>
          <label htmlFor={`${id}-budget`} className={labelClass}>
            {t('adminModels.accounts.budget')}
          </label>
          <input
            id={`${id}-budget`}
            className={inputClass}
            inputMode="decimal"
            value={amount}
            disabled={busy}
            maxLength={20}
            autoComplete="off"
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? `${id}-problem ${id}-hint` : `${id}-hint`}
            onChange={(event) => setAmount(event.target.value)}
          />
        </div>
      </div>
      {invalid ? (
        <p id={`${id}-problem`} className="mt-1 text-xs leading-5 text-status-error">
          {t('adminModels.invalid.budget', { max: formatUsd(MAX_DAILY_BUDGET_MICROUSD) })}
        </p>
      ) : null}
      <p id={`${id}-hint`} className="mt-1 text-xs leading-5 text-muted">
        {t('adminModels.accounts.budgetHint')}
      </p>
    </AccountForm>
  );
}

type Access = 'offer' | 'withhold';

/**
 * Offer one account, named by email or id, a model, or withhold it, in place
 * of the model's default: open to every account, or only to named ones.
 */
export function SetAccessForm({ connection, onSaved, onAccessDenied }: FormProps) {
  const t = useT();
  const id = useId();
  const [account, setAccount] = useState('');
  const [model, setModel] = useState(connection.models[0] ?? '');
  const [access, setAccess] = useState<Access>('offer');
  const { busy, error, submit } = useSubmit(onAccessDenied);
  // The list may have changed under the form: a model no longer listed is not sent.
  const chosen = connection.models.includes(model) ? model : (connection.models[0] ?? '');

  const save = () =>
    submit(async () => {
      onSaved(
        await setOperatorModelUserAccess(connection.id, {
          account: account.trim(),
          model: chosen,
          offered: access === 'offer',
        }),
      );
      setAccount('');
    });

  return (
    <AccountForm
      title={t('adminModels.accounts.accessTitle')}
      action={t('adminModels.accounts.setAccess')}
      busy={busy}
      ready={chosen !== '' && account.trim() !== ''}
      error={error}
      onSubmit={() => void save()}
    >
      <div className="mt-2 grid gap-2 lg:grid-cols-3">
        <AccountField id={`${id}-account`} value={account} disabled={busy} onChange={setAccount} />
        <div>
          <label htmlFor={`${id}-model`} className={labelClass}>
            {t('adminModels.accounts.model')}
          </label>
          <Select
            id={`${id}-model`}
            className={inputClass}
            value={chosen}
            disabled={busy}
            onValueChange={setModel}
            options={connection.models.map((item) => ({ value: item, label: item }))}
          />
        </div>
        <div>
          <label htmlFor={`${id}-access`} className={labelClass}>
            {t('adminModels.accounts.access')}
          </label>
          <Select<Access>
            id={`${id}-access`}
            className={inputClass}
            value={access}
            disabled={busy}
            onValueChange={setAccess}
            aria-describedby={`${id}-hint`}
            options={[
              { value: 'offer', label: t('adminModels.accounts.offer') },
              { value: 'withhold', label: t('adminModels.accounts.withhold') },
            ]}
          />
        </div>
      </div>
      <p id={`${id}-hint`} className="mt-1 text-xs leading-5 text-muted">
        {t('adminModels.accounts.accessHint')}
      </p>
    </AccountForm>
  );
}
