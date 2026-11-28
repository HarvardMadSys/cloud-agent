'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useRef, useState } from 'react';
import { useT } from '@/components/providers';
import { announceModelConnectionsChanged } from '@/components/features/agents/composer/modelConnectionEvents';
import {
  callbackAlreadyUsed,
  markCallbackUsed,
  takeReturnPath,
} from '@/components/features/agents/githubReturn';
import { SETTINGS_PATHS } from '@/components/features/settings/navigation';
import { finishModelSignIn, type ModelSignInResult } from '@/lib/api/modelConnections';

type CallbackProblem =
  | 'modelSignIn.denied'
  | 'modelSignIn.missingState'
  | 'modelSignIn.missingCode';

/** What the address says, before anything is sent: the problem with it, if any. */
function callbackProblem(
  params: { get(name: string): string | null } | null,
): CallbackProblem | null {
  // Read as an outcome, never displayed as sent: anyone can write this link.
  if (params?.get('error')) return 'modelSignIn.denied';
  if (!params?.get('state')) return 'modelSignIn.missingState';
  if (!params?.get('code')) return 'modelSignIn.missingCode';
  return null;
}

/**
 * Where the model provider sends the owner back: its one-time code beside the
 * state this service issued. Both go to the server once, which exchanges them
 * for the owner's key and saves the connection; then back to where the trip
 * started, or to Models. A refresh or Back after it worked carries on instead
 * of sending a spent code again.
 */
function ConnectedInner() {
  const t = useT();
  const router = useRouter();
  const params = useSearchParams();
  const started = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<ModelSignInResult | null>(null);
  const code = params?.get('code') ?? '';
  const state = params?.get('state') ?? '';
  const problem = callbackProblem(params);

  useEffect(() => {
    // Strict Mode replays effects in development, and a state works once.
    if (started.current) return;
    started.current = true;
    if (problem) {
      setError(t(problem));
      return;
    }
    const back = () => router.replace(takeReturnPath() ?? SETTINGS_PATHS.models);
    if (callbackAlreadyUsed(state)) {
      back();
      return;
    }
    finishModelSignIn(code, state)
      .then((result) => {
        markCallbackUsed(state);
        announceModelConnectionsChanged();
        if (result.skipped_models.length) setSaved(result);
        else back();
      })
      .catch((cause: unknown) =>
        setError(
          cause instanceof Error && cause.message ? cause.message : t('modelSignIn.finishFailed'),
        ),
      );
  }, [code, problem, router, state, t]);

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-4">
        <h1 className="text-xl font-semibold">{t('modelSignIn.failedTitle')}</h1>
        <p role="alert" className="max-w-md text-center text-sm text-muted">
          {error}
        </p>
        <Link href={SETTINGS_PATHS.models} className="ui-button ui-button-secondary min-h-11">
          {t('modelSignIn.back')}
        </Link>
      </div>
    );
  }

  if (saved) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-4">
        <h1 className="text-xl font-semibold">
          {t('modelSignIn.savedTitle', { name: saved.connection.name })}
        </h1>
        <p className="max-w-md text-center text-sm text-muted">
          {t('modelSignIn.skipped', { models: saved.skipped_models.join(', ') })}
        </p>
        <button
          type="button"
          className="ui-button ui-button-primary min-h-11"
          onClick={() => router.replace(takeReturnPath() ?? SETTINGS_PATHS.models)}
        >
          {t('modelSignIn.continue')}
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full items-center justify-center p-4">
      <p role="status" className="text-sm text-muted">
        {t('modelSignIn.finishing')}
      </p>
    </div>
  );
}

export default function ModelSignInCallbackPage() {
  return (
    <Suspense fallback={<div className="flex h-full items-center justify-center" />}>
      <ConnectedInner />
    </Suspense>
  );
}
