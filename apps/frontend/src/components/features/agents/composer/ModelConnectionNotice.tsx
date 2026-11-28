'use client';

import { useT } from '@/components/providers';
import type { AgentConfigApi } from '@/lib/api/agents';
import { ModelConnectionsLink } from '@/components/features/settings/ModelConnectionsLink';
import { ModelSignInButton } from '@/components/features/settings/ModelSignInButton';
import { leadingProvidedSource, modelSetupRequired } from './providedModels';

/**
 * Shown while nothing can run until the owner adds a connection of their own;
 * setup is actionable only where the deployment lets owners add one. A model
 * the deployment provides, and this owner can run, is enough: no notice then.
 * Where a provided source exists but offers this owner nothing, the notice
 * names it instead of suggesting a provider. Where the deployment offers a
 * provider to sign in to for a key, that comes first, and adding a connection
 * by hand stays beside it.
 */
export function ModelConnectionNotice({ config }: { config: AgentConfigApi | null }) {
  const t = useT();
  if (!config || !modelSetupRequired(config)) return null;
  const enabled = config.model_connections_enabled === true;
  const provided = leadingProvidedSource(config);
  const signIn = enabled ? config.model_sign_in : null;
  const description = !enabled
    ? t('modelSource.disabledDescription')
    : provided
      ? t('modelSource.setupDescriptionProvided', { name: provided.name })
      : signIn
        ? t('modelSignIn.noticeDescription', { name: signIn.name })
        : t('modelSource.setupDescription');
  return (
    <div role="status" className="my-3 min-w-0 space-y-2 rounded-xl border border-border p-4">
      <p className="text-sm font-medium">
        {t(enabled ? 'modelSource.setupTitle' : 'modelSource.disabledTitle')}
      </p>
      <p className="text-sm leading-6 text-muted">{description}</p>
      {signIn ? <ModelSignInButton offer={signIn} /> : null}
      {enabled ? (
        <ModelConnectionsLink className="ui-button ui-button-secondary inline-flex min-h-11 max-w-full items-center whitespace-normal">
          {t(signIn ? 'modelSignIn.manual' : 'modelSource.setupAction')}
        </ModelConnectionsLink>
      ) : null}
    </div>
  );
}
