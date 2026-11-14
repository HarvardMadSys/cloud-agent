'use client';

import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { useT } from '@/components/providers/LocaleProvider';
import {
  clearOperatorModelUserAccess,
  clearOperatorModelUserBudget,
  listOperatorModelUsers,
  type OperatorModelConnection,
  type OperatorModelUser,
} from '@/lib/api/adminOperatorModels';
import { formatUsd, formatUsdCost } from '@/lib/money';
import { APIError } from '@/lib/utils/errors';

import { SetAccessForm, SetBudgetForm } from './ProvidedModelAccountForms';
import { countText, isAccessDenied, providedFailure } from './providedModelsForm';

const cell = 'px-3 py-2 align-top';
/** The refusal of a clear for an account that has no budget of its own (any more). */
const NO_OWN_BUDGET = 'operator_model_user_budget_not_found';
/** The refusal of a clear for an account that has no answer of its own on the model (any more). */
const NO_OWN_ACCESS = 'operator_model_user_access_not_found';

type Translate = ReturnType<typeof useT>;

/** `users` with one account's line as the server holds it now: in its place, or added. */
function withUser(users: OperatorModelUser[], row: OperatorModelUser): OperatorModelUser[] {
  return users.some((user) => user.account_id === row.account_id)
    ? users.map((user) => (user.account_id === row.account_id ? row : user))
    : [...users, row];
}

/**
 * `users` with a page added. An account already shown, because a write here
 * answered its line before its page was read, keeps its place and takes the
 * page's line.
 */
function withPage(users: OperatorModelUser[], page: OperatorModelUser[]): OperatorModelUser[] {
  return page.reduce(withUser, users);
}

/** Whether an account has a line: something of its own, or use today. */
function listed(user: OperatorModelUser): boolean {
  if (user.daily_budget_microusd !== null || Object.keys(user.access).length > 0) return true;
  return user.requests_today > 0 || user.spent_today_microusd > 0;
}

/**
 * `users` with one account's line as `cleared` leaves it, or without the line
 * once it has nothing to say. A clear answers nothing, so the line is adjusted
 * here.
 */
function withCleared(
  users: OperatorModelUser[],
  accountId: string,
  cleared: (user: OperatorModelUser) => OperatorModelUser,
): OperatorModelUser[] {
  return users.flatMap((user) => {
    if (user.account_id !== accountId) return [user];
    const left = cleared(user);
    return listed(left) ? [left] : [];
  });
}

/** `users` with one account's own budget removed: it follows the default again. */
function withoutBudget(users: OperatorModelUser[], accountId: string): OperatorModelUser[] {
  return withCleared(users, accountId, (user) => ({ ...user, daily_budget_microusd: null }));
}

/** `users` with one account's own answer on a model removed: it follows the model's default. */
function withoutAccess(
  users: OperatorModelUser[],
  accountId: string,
  model: string,
): OperatorModelUser[] {
  return withCleared(users, accountId, (user) => ({
    ...user,
    access: Object.fromEntries(Object.entries(user.access).filter(([key]) => key !== model)),
  }));
}

/** An account's own daily budget, as the table says it. */
function budgetText(budget: number | null, t: Translate): string {
  if (budget === null) return t('adminModels.accounts.followsDefault');
  const amount = formatUsd(budget, { precise: true });
  return budget === 0 ? t('adminModels.accounts.withheldBudget', { amount }) : amount;
}

/** What names the clear of an account's own budget while it is in flight. */
function budgetKey(user: OperatorModelUser): string {
  return `budget:${user.account_id}`;
}

/** What names the clear of an account's own answer on a model while it is in flight. */
function accessKey(user: OperatorModelUser, model: string): string {
  return `access:${user.account_id}:${model}`;
}

/** An account as the table names it: its email, with the id kept beside it. */
function AccountName({ user }: { user: OperatorModelUser }) {
  return (
    <>
      <span className="block break-all">{user.email || user.account_id}</span>
      {user.email ? (
        <span className="block break-all font-mono text-[11px] text-muted">{user.account_id}</span>
      ) : null}
    </>
  );
}

/** Puts an account back on a default: the connection's budget, or one model's. */
function ClearButton({
  label,
  busy,
  disabled,
  onClick,
}: {
  label: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const t = useT();
  return (
    <button
      type="button"
      className="ui-button ui-button-secondary min-h-9"
      disabled={disabled}
      aria-busy={busy || undefined}
      aria-label={label}
      onClick={onClick}
    >
      {t('adminModels.accounts.clear')}
    </button>
  );
}

/**
 * One account: its own daily budget, what it spent and how many requests it
 * was served today, and its own answer on each model it has one for. Each
 * thing of its own can be cleared, which puts the account back on the default.
 */
function AccountRow({
  user,
  clearing,
  onClearBudget,
  onClearAccess,
}: {
  user: OperatorModelUser;
  /** What is being cleared anywhere in the table: nothing else is cleared meanwhile. */
  clearing: string;
  onClearBudget: () => void;
  onClearAccess: (model: string) => void;
}) {
  const t = useT();
  const account = user.email || user.account_id;
  const models = Object.keys(user.access).sort();
  return (
    <tr>
      <td className={cell}>
        <AccountName user={user} />
      </td>
      <td className={cell}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>{budgetText(user.daily_budget_microusd, t)}</span>
          {user.daily_budget_microusd !== null ? (
            <ClearButton
              label={t('adminModels.accounts.clearBudgetFor', { account })}
              busy={clearing === budgetKey(user)}
              disabled={clearing !== ''}
              onClick={onClearBudget}
            />
          ) : null}
        </div>
      </td>
      <td className={`${cell} whitespace-nowrap text-right tabular-nums`}>
        {formatUsdCost(user.spent_today_microusd)}
      </td>
      <td className={`${cell} whitespace-nowrap text-right tabular-nums`}>
        {countText(user.requests_today)}
      </td>
      <td className={cell}>
        {models.length === 0 ? (
          <span>{t('adminModels.accounts.followsModels')}</span>
        ) : (
          <ul className="space-y-1">
            {models.map((model) => (
              <li key={model} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <span className="break-all font-mono text-[13px]">{model}</span>{' '}
                  {t(
                    user.access[model]
                      ? 'adminModels.accounts.offered'
                      : 'adminModels.accounts.withheld',
                  )}
                </span>
                <ClearButton
                  label={t('adminModels.accounts.clearAccessFor', { account, model })}
                  busy={clearing === accessKey(user, model)}
                  disabled={clearing !== ''}
                  onClick={() => onClearAccess(model)}
                />
              </li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}

/**
 * The accounts that differ from the defaults on this connection: those given
 * a daily budget of their own, those offered a model or refused one by name,
 * and those with use today. A budget or an answer is set for an account by
 * email or id, and cleared to put it back on the default.
 *
 * A write answers the account's whole line as the list has it, so the line
 * shown is the server's: for an account that was not in the table too.
 */
export function ProvidedModelAccounts({
  connection,
  onAccessDenied,
}: {
  connection: OperatorModelConnection;
  onAccessDenied?: (cause: unknown) => void;
}) {
  const t = useT();
  const headingId = useId();
  const [users, setUsers] = useState<OperatorModelUser[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState('');
  const [error, setError] = useState('');
  const request = useRef(0);
  const connectionId = connection.id;

  const fail = useCallback(
    (cause: unknown) => {
      if (isAccessDenied(cause)) onAccessDenied?.(cause);
      setError(providedFailure(cause, t));
    },
    [onAccessDenied, t],
  );

  /** The first page, or the page after `after`, added to the ones already shown. */
  const load = useCallback(
    async (after: string | null) => {
      const current = ++request.current;
      setLoading(true);
      setError('');
      try {
        const page = await listOperatorModelUsers(connectionId, { cursor: after });
        if (current !== request.current) return;
        setUsers((shown) => withPage(shown ?? [], page.users));
        setCursor(page.next_cursor);
      } catch (cause) {
        if (current === request.current) fail(cause);
      } finally {
        if (current === request.current) setLoading(false);
      }
    },
    [connectionId, fail],
  );

  useEffect(() => {
    void load(null);
    return () => {
      request.current += 1;
    };
  }, [load]);

  /** Clear one thing of an account's own, then show its line without it. */
  async function clear(
    key: string,
    send: () => Promise<void>,
    gone: string,
    without: (shown: OperatorModelUser[]) => OperatorModelUser[],
  ) {
    if (clearing) return;
    setClearing(key);
    setError('');
    let cleared = true;
    try {
      await send();
    } catch (cause) {
      // Already cleared elsewhere: the line only has to stop saying otherwise.
      cleared = cause instanceof APIError && cause.code === gone;
      if (!cleared) fail(cause);
    } finally {
      setClearing('');
    }
    if (cleared) setUsers((shown) => without(shown ?? []));
  }

  const clearBudget = (user: OperatorModelUser) =>
    clear(
      budgetKey(user),
      () => clearOperatorModelUserBudget(connectionId, user.account_id),
      NO_OWN_BUDGET,
      (shown) => withoutBudget(shown, user.account_id),
    );

  const clearAccess = (user: OperatorModelUser, model: string) =>
    clear(
      accessKey(user, model),
      () => clearOperatorModelUserAccess(connectionId, user.account_id, model),
      NO_OWN_ACCESS,
      (shown) => withoutAccess(shown, user.account_id, model),
    );

  const saved = (user: OperatorModelUser) => setUsers((shown) => withUser(shown ?? [], user));

  const rows = users ?? [];
  return (
    <section aria-labelledby={headingId} className="mt-4">
      <h4 id={headingId} className="text-sm font-semibold">
        {t('adminModels.accounts.title')}
      </h4>
      <p className="mt-1 text-xs leading-5 text-muted">{t('adminModels.accounts.description')}</p>
      {error ? (
        <p role="alert" className="mt-2 text-sm text-status-error">
          {error}
        </p>
      ) : null}
      {users && rows.length === 0 ? (
        <p className="mt-2 text-sm text-muted">{t('adminModels.accounts.empty')}</p>
      ) : null}
      {rows.length > 0 ? (
        <div className="mt-2 overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <caption className="sr-only">{t('adminModels.accounts.title')}</caption>
            <thead className="border-b border-border bg-surface-muted text-xs text-muted">
              <tr>
                <th scope="col" className={`${cell} font-medium`}>
                  {t('adminModels.accounts.account')}
                </th>
                <th scope="col" className={`${cell} font-medium`}>
                  {t('adminModels.accounts.ownBudget')}
                </th>
                <th scope="col" className={`${cell} text-right font-medium`}>
                  {t('adminModels.accounts.spentToday')}
                </th>
                <th scope="col" className={`${cell} text-right font-medium`}>
                  {t('adminModels.accounts.requestsToday')}
                </th>
                <th scope="col" className={`${cell} font-medium`}>
                  {t('adminModels.accounts.ownAccess')}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((user) => (
                <AccountRow
                  key={user.account_id}
                  user={user}
                  clearing={clearing}
                  onClearBudget={() => void clearBudget(user)}
                  onClearAccess={(model) => void clearAccess(user, model)}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div role="status" aria-live="polite">
        {loading ? (
          <p className="mt-2 text-sm text-muted">{t('adminModels.accounts.loading')}</p>
        ) : null}
      </div>
      {cursor && !loading ? (
        <button
          type="button"
          className="ui-button ui-button-secondary mt-2 min-h-11"
          onClick={() => void load(cursor)}
        >
          {t('adminModels.accounts.more')}
        </button>
      ) : null}
      <SetBudgetForm connection={connection} onSaved={saved} onAccessDenied={onAccessDenied} />
      <SetAccessForm connection={connection} onSaved={saved} onAccessDenied={onAccessDenied} />
    </section>
  );
}
