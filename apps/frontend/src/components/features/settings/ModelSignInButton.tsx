'use client';

import { useState } from 'react';
import { useT } from '@/components/providers';
import {
  startModelSignIn,
  type ModelConnectionList,
  type ModelSignInOffer,
} from '@/lib/api/modelConnections';
import { leaveFor } from '../agents/githubAccess';
import { rememberReturn } from '../agents/githubReturn';

/**
 * Off to the deployment's model provider to sign up or sign in there and
 * approve a key; the provider sends the owner back to the callback page, which
 * saves the connection and returns here. Where the trip started is noted
 * first, so it ends where it began: the composer keeps its own draft.
 */
export function ModelSignInButton({
  offer,
  className = 'ui-button ui-button-primary min-h-11',
}: {
  offer: ModelSignInOffer;
  className?: string;
}) {
  const t = useT();
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setLeaving(true);
    setError(null);
    rememberReturn(`${window.location.pathname}${window.location.search}`);
    try {
      leaveFor(await startModelSignIn());
    } catch (cause) {
      setLeaving(false);
      setError(
        cause instanceof Error && cause.message ? cause.message : t('modelSignIn.startFailed'),
      );
    }
  }

  return (
    <div className="min-w-0 space-y-2">
      <button
        type="button"
        className={`${className} inline-flex max-w-full items-center whitespace-normal`}
        disabled={leaving}
        onClick={() => void go()}
      >
        {t(leaving ? 'modelSignIn.leaving' : 'modelSignIn.action', { name: offer.name })}
      </button>
      {error ? (
        <p role="alert" className="text-sm text-status-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The Models page's offer: what signing in does, and the button. Shown while
 * the owner can add connections, is not in the middle of editing one, and the
 * server offers a provider (it stops once a connection uses that provider).
 */
export function ModelSignInCard({
  list,
  editing,
}: {
  list: ModelConnectionList | null;
  editing: boolean;
}) {
  const t = useT();
  const offer = list?.configured && list.enabled && !editing ? list.sign_in : null;
  if (!offer) return null;
  return (
    <div className="mt-4 space-y-2 rounded-xl border border-border p-4">
      <p className="max-w-2xl text-sm leading-6 text-muted">
        {t('modelSignIn.description', { name: offer.name })}
      </p>
      <ModelSignInButton offer={offer} />
    </div>
  );
}
