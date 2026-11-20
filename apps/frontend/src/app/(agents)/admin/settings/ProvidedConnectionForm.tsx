'use client';

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';

import { PasswordInput } from '@/components/features/auth/PasswordInput';
import { ModelStatusList } from '@/components/features/settings/ModelStatusList';
import { useT } from '@/components/providers/LocaleProvider';
import {
  createOperatorModelConnection,
  OPERATOR_MAX_NAME_CHARS,
  updateOperatorModelConnection,
  type OperatorModelConnection,
} from '@/lib/api/adminOperatorModels';
import { refusedModels, type ModelStatus } from '@/lib/api/modelConnections';
import type { TranslationKey } from '@/lib/i18n/translate';

import {
  initialFields,
  isAccessDenied,
  newProvidedConnection,
  providedChanges,
  providedFailure,
  providedFieldProblems,
  providedNeedsVerification,
  refusedFields,
  type ProvidedField,
  type ProvidedFields,
} from './providedModelsForm';

const inputClass = 'ui-input mt-1 block min-h-11 w-full rounded-lg';

type Translate = ReturnType<typeof useT>;
type Problems = Partial<Record<ProvidedField, true>>;

const PROBLEM_KEYS: Record<ProvidedField, TranslationKey> = {
  name: 'adminModels.invalid.name',
  base_url: 'connections.invalid.url',
  api_key: 'connections.invalid.key',
};

/** What a save answered: the connection as the server holds it now. */
export interface SavedConnection {
  connection: OperatorModelConnection;
}

function actionLabel(busy: boolean, verify: boolean, t: Translate): string {
  if (busy) return t(verify ? 'connections.verifyingShort' : 'connections.busy');
  return t(verify ? 'connections.verifySave' : 'connections.save');
}

/** The save itself; `null` when an edit changed nothing. */
async function submit(
  connection: OperatorModelConnection | undefined,
  fields: ProvidedFields,
): Promise<SavedConnection | null> {
  if (connection) {
    const changes = providedChanges(connection, fields);
    // Closing an unchanged editor sends nothing.
    if (!Object.keys(changes).length) return null;
    return { connection: await updateOperatorModelConnection(connection.id, changes) };
  }
  return { connection: await createOperatorModelConnection(newProvidedConnection(fields)) };
}

/** A labelled control with its hint and its field-level problem, if any. */
function Field({
  label,
  hint,
  problem,
  children,
}: {
  label: string;
  hint?: string;
  problem: string;
  children: (
    inputId: string,
    describedBy: string | undefined,
    invalid: true | undefined,
  ) => ReactNode;
}) {
  const id = useId();
  const described = [problem ? `${id}-problem` : '', hint ? `${id}-hint` : '']
    .filter(Boolean)
    .join(' ');
  return (
    <div>
      <label htmlFor={`${id}-input`} className="block text-sm font-medium">
        {label}
      </label>
      {children(`${id}-input`, described || undefined, problem ? true : undefined)}
      {problem ? (
        <p id={`${id}-problem`} className="mt-1 text-xs leading-5 text-status-error">
          {problem}
        </p>
      ) : null}
      {hint ? (
        <p id={`${id}-hint`} className="mt-1 text-xs leading-5 text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Add or edit a provided connection: a name, a Base URL and a write-only key.
 * Nothing else is asked: the connection offers whatever models the provider
 * lists for the key, so which models the deployment provides is set on the
 * key, at the provider.
 *
 * **Verify and save** reads the provider's model list with the key and checks
 * every model before anything is stored, which can take minutes and cannot be
 * cancelled once sent. A refusal keeps every input and, when no model could
 * be verified, shows each model's result. A new connection is saved disabled
 * and enabled from its card.
 */
export function ProvidedConnectionForm({
  connection,
  onSaved,
  onCancel,
  onAccessDenied,
}: {
  connection?: OperatorModelConnection;
  /** `null`: nothing was changed. */
  onSaved: (saved: SavedConnection | null) => void;
  onCancel: () => void;
  onAccessDenied?: (cause: unknown) => void;
}) {
  const t = useT();
  const [fields, setFields] = useState<ProvidedFields>(() => initialFields(connection));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [problems, setProblems] = useState<Problems>({});
  const [results, setResults] = useState<ModelStatus[]>([]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const current: ProvidedFields = {
    name: fields.name.trim(),
    base_url: fields.base_url.trim(),
    key: fields.key.trim(),
  };
  const verify = providedNeedsVerification(connection, current);
  const problem = (field: ProvidedField) => (problems[field] ? t(PROBLEM_KEYS[field]) : '');

  /** Any edit after a refusal: its results described the form as it was. */
  function edit(change: Partial<ProvidedFields>) {
    setFields((value) => ({ ...value, ...change }));
    setResults([]);
    setProblems({});
    setError('');
  }

  function failed(cause: unknown) {
    if (isAccessDenied(cause)) onAccessDenied?.(cause);
    setResults(refusedModels(cause) ?? []);
    setProblems(refusedFields(cause));
    setError(providedFailure(cause, t));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const found = providedFieldProblems(current, !connection);
    const invalid = Object.keys(found).length > 0;
    setProblems(found);
    setResults([]);
    setError(invalid ? t('connections.invalid') : '');
    if (invalid) return;
    setBusy(true);
    try {
      const saved = await submit(connection, current);
      if (mounted.current) onSaved(saved);
    } catch (cause) {
      if (mounted.current) failed(cause);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  const incomplete = !current.name || !current.base_url || (!connection && !current.key);

  return (
    <form
      onSubmit={(event) => void save(event)}
      noValidate
      aria-label={t(connection ? 'adminModels.form.editTitle' : 'adminModels.form.addTitle')}
      className="mt-4 space-y-4 rounded-xl border border-border p-4"
    >
      <fieldset disabled={busy} className="space-y-4">
        <legend className="text-sm font-semibold">
          {t(connection ? 'adminModels.form.editTitle' : 'adminModels.form.addTitle')}
        </legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label={t('adminModels.form.name')}
            hint={t('adminModels.form.nameHint')}
            problem={problem('name')}
          >
            {(inputId, describedBy, invalid) => (
              <input
                id={inputId}
                className={inputClass}
                value={fields.name}
                onChange={(event) => edit({ name: event.target.value })}
                maxLength={OPERATOR_MAX_NAME_CHARS}
                autoComplete="off"
                aria-invalid={invalid}
                aria-describedby={describedBy}
              />
            )}
          </Field>
          <Field label={t('connections.url')} problem={problem('base_url')}>
            {(inputId, describedBy, invalid) => (
              <input
                id={inputId}
                className={inputClass}
                type="url"
                value={fields.base_url}
                onChange={(event) => edit({ base_url: event.target.value })}
                maxLength={2048}
                placeholder="https://api.example.com/v1"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={invalid}
                aria-describedby={describedBy}
              />
            )}
          </Field>
        </div>
        <Field
          label={t('connections.key')}
          hint={t('adminModels.form.keyHint')}
          problem={problem('api_key')}
        >
          {(inputId, describedBy, invalid) => (
            <PasswordInput
              id={inputId}
              className={inputClass}
              toggleLabels={{ show: t('connections.showKey'), hide: t('connections.hideKey') }}
              value={fields.key}
              onChange={(event) => edit({ key: event.target.value })}
              maxLength={4096}
              autoComplete="new-password"
              spellCheck={false}
              placeholder={connection ? t('connections.keyKept') : undefined}
              aria-invalid={invalid}
              aria-describedby={describedBy}
            />
          )}
        </Field>
      </fieldset>
      <div role="status" aria-live="polite">
        {busy && verify ? (
          <p className="text-sm text-muted">{t('adminModels.form.verifying')}</p>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-sm text-status-error">
          {error}
        </p>
      ) : null}
      {results.length ? (
        <ModelStatusList statuses={results} notUsable={t('adminModels.form.notVerified')} />
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="submit"
          disabled={busy || incomplete}
          aria-busy={busy || undefined}
          className="ui-button ui-button-primary min-h-11"
        >
          {actionLabel(busy, verify, t)}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="ui-button ui-button-secondary min-h-11"
        >
          {t('connections.cancel')}
        </button>
      </div>
      <p className="text-xs leading-5 text-muted">
        {t(connection ? 'adminModels.form.verifyNote' : 'adminModels.form.createNote')}
      </p>
    </form>
  );
}
