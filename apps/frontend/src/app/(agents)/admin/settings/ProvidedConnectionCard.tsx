'use client';

import { useId, useState, type ReactNode } from 'react';

import { ModelStatusList } from '@/components/features/settings/ModelStatusList';
import { useT } from '@/components/providers/LocaleProvider';
import {
  deleteOperatorModelConnection,
  refreshOperatorModels,
  updateOperatorModelConnection,
  type OperatorModelConnection,
} from '@/lib/api/adminOperatorModels';

import { ProvidedModelUsage } from './ProvidedModelUsage';
import { isAccessDenied, pricesText, providedFailure } from './providedModelsForm';

const buttonClass = 'ui-button ui-button-secondary min-h-11';

/**
 * A part of the card that is read from the server only once it is opened, so
 * a closed one asks nothing; closing it again keeps what it showed.
 */
function Disclosure({ show, hide, children }: { show: string; hide: string; children: ReactNode }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [visited, setVisited] = useState(false);
  return (
    <div className="mt-3 border-t border-border pt-2">
      <button
        type="button"
        className="ui-focus-ring inline-flex min-h-9 items-center rounded text-sm font-medium text-accent hover:underline"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setVisited(true);
          setOpen((value) => !value);
        }}
      >
        {open ? hide : show}
      </button>
      <div id={id} hidden={!open}>
        {visited ? children : null}
      </div>
    </div>
  );
}

/** The card's buttons; each waits while the card is working or a form is open. */
function CardActions({
  connection,
  held,
  blockedId,
  refreshBlocked,
  refreshing,
  onEdit,
  onToggle,
  onRefresh,
  onDelete,
}: {
  connection: OperatorModelConnection;
  /** Working, or a form is open. */
  held: boolean;
  /** The id of the sentence saying why Enable is not offered; `''` when it is. */
  blockedId: string;
  refreshBlocked: boolean;
  refreshing: boolean;
  onEdit: () => void;
  onToggle: () => void;
  onRefresh: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  return (
    <div className="flex flex-wrap gap-2">
      <button type="button" className={buttonClass} onClick={onEdit} disabled={held}>
        {t('connections.edit')}
      </button>
      <button
        type="button"
        className={buttonClass}
        disabled={held || blockedId !== ''}
        aria-describedby={blockedId || undefined}
        onClick={onToggle}
      >
        {t(connection.enabled ? 'connections.disable' : 'connections.enable')}
      </button>
      <button
        type="button"
        className={buttonClass}
        disabled={held || refreshBlocked}
        aria-busy={refreshing || undefined}
        onClick={onRefresh}
      >
        {t('adminModels.card.refresh')}
      </button>
      <button type="button" className={buttonClass} disabled={held} onClick={onDelete}>
        {t('connections.delete')}
      </button>
    </div>
  );
}

/** Asks before a delete or a disable, which stop work for every account. */
function ConfirmPanel({
  kind,
  name,
  busy,
  onConfirm,
  onCancel,
}: {
  kind: 'delete' | 'disable';
  name: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  return (
    <div
      role="group"
      aria-label={t('connections.confirm')}
      className="mt-3 space-y-3 rounded-lg border border-border p-3"
    >
      <p className="text-sm">
        {t(
          kind === 'delete' ? 'adminModels.card.confirmDelete' : 'adminModels.card.confirmDisable',
          {
            name,
          },
        )}
      </p>
      <div className="flex gap-2">
        <button type="button" className={buttonClass} disabled={busy} onClick={onConfirm}>
          {t('connections.confirm')}
        </button>
        <button type="button" className={buttonClass} disabled={busy} onClick={onCancel}>
          {t('connections.cancel')}
        </button>
      </div>
    </div>
  );
}

/** The key's models, each with its verification and its listed prices. */
function ListedModels({ connection }: { connection: OperatorModelConnection }) {
  const t = useT();
  const unpriced = connection.models.filter((model) => !Object.hasOwn(connection.prices, model));
  return (
    <>
      <p className="mt-3 text-xs leading-5 text-muted">{t('adminModels.card.modelsFromKey')}</p>
      <ModelStatusList
        statuses={connection.model_status}
        notUsable={t('adminModels.card.notUsable')}
        detail={(model) =>
          pricesText(connection.prices[model], t) ?? t('adminModels.card.noListedPrice')
        }
      />
      {unpriced.length ? (
        <p className="mt-2 text-xs leading-5 text-muted">
          {t('adminModels.card.unpricedNote', { models: unpriced.join(', ') })}
        </p>
      ) : null}
    </>
  );
}

/**
 * One provided connection: what it is, whether it is on, the models its
 * provider lists for the key with their verification and listed prices, and,
 * on request, what was used.
 *
 * The models are read-only here: they are the key's, and **Read models again**
 * follows a key whose models changed at the provider. Disabling or deleting
 * the connection stops the agent processes running on it, for every account.
 */
export function ProvidedConnectionCard({
  connection,
  featureOff,
  locked,
  onEdit,
  onChange,
  onDelete,
  onAccessDenied,
}: {
  connection: OperatorModelConnection;
  /** Model connections are switched off for the deployment: nothing can be enabled. */
  featureOff: boolean;
  /** A form is open: the card's own actions wait for it. */
  locked: boolean;
  onEdit: () => void;
  onChange: (connection: OperatorModelConnection) => void;
  onDelete: () => void;
  onAccessDenied?: (cause: unknown) => void;
}) {
  const t = useT();
  const id = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState<'delete' | 'disable' | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // Model connections switched off for the deployment: nothing can be enabled.
  const blockedBy = !connection.enabled && featureOff ? t('adminModels.card.enableFeatureOff') : '';
  const refusedSince = connection.key_refused_at
    ? new Date(connection.key_refused_at).toLocaleString()
    : '';
  const keyRefused = refusedSince ? t('adminModels.card.keyRefused', { since: refusedSince }) : '';

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await action();
      setConfirm(null);
    } catch (cause) {
      if (isAccessDenied(cause)) onAccessDenied?.(cause);
      setError(providedFailure(cause, t));
    } finally {
      setBusy(false);
    }
  }

  const setEnabled = (enabled: boolean) =>
    run(async () => onChange(await updateOperatorModelConnection(connection.id, { enabled })));

  const remove = () =>
    run(async () => {
      await deleteOperatorModelConnection(connection.id);
      onDelete();
    });
  const confirmed = () => (confirm === 'delete' ? remove() : setEnabled(false));

  const refresh = () => {
    setRefreshing(true);
    return run(async () => onChange(await refreshOperatorModels(connection.id))).finally(() =>
      setRefreshing(false),
    );
  };

  return (
    <article className="min-w-0 rounded-xl border border-border p-4" aria-label={connection.name}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="break-words text-sm font-semibold">{connection.name}</h3>
          <p className="mt-1 break-all text-xs text-muted">{connection.base_url}</p>
          <p className="mt-1 text-xs text-muted">
            {t(connection.enabled ? 'connections.enabled' : 'connections.disabled')}
          </p>
        </div>
        <CardActions
          connection={connection}
          held={busy || locked}
          blockedId={blockedBy ? `${id}-blocked` : ''}
          refreshBlocked={featureOff}
          refreshing={refreshing}
          onEdit={onEdit}
          onToggle={() => {
            if (connection.enabled) setConfirm('disable');
            else void setEnabled(true);
          }}
          onRefresh={() => void refresh()}
          onDelete={() => setConfirm('delete')}
        />
      </div>
      {blockedBy ? (
        <p id={`${id}-blocked`} className="mt-2 text-xs leading-5 text-status-warning">
          {blockedBy}
        </p>
      ) : null}
      {keyRefused ? (
        <p role="alert" className="mt-2 text-sm leading-6 text-status-warning">
          {keyRefused}
        </p>
      ) : null}
      <div role="status" aria-live="polite">
        {busy ? (
          <p className="mt-2 text-sm text-muted">
            {t(refreshing ? 'adminModels.card.refreshing' : 'connections.busy')}
          </p>
        ) : null}
      </div>
      {confirm ? (
        <ConfirmPanel
          kind={confirm}
          name={connection.name}
          busy={busy}
          onConfirm={() => void confirmed()}
          onCancel={() => setConfirm(null)}
        />
      ) : null}
      {error ? (
        <p role="alert" className="mt-3 text-sm text-status-error">
          {error}
        </p>
      ) : null}
      <ListedModels connection={connection} />
      <Disclosure show={t('adminModels.usage.showSection')} hide={t('adminModels.usage.hide')}>
        <ProvidedModelUsage connection={connection} onAccessDenied={onAccessDenied} />
      </Disclosure>
    </article>
  );
}
