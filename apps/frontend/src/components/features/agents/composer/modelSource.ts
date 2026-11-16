import type { AgentConfigApi } from '@/lib/api/agents';
import type { Translator } from '@/lib/i18n/translate';
import { findSource, isProvidedId, ownConnections, providedSources } from './providedModels';

// Where a model runs is part of the choice, not a detail of it.
//
// Each of the owner's connections is a separate source, and so is each
// connection the deployment provides. A model id is listed once: a save that
// would put one on two of the owner's connections, or on two provided ones,
// is refused, and a provided model the owner also has on their own connection
// is not offered to them. Connections saved before that rule may still list
// one id twice (`gpt-5` on two keys bills two accounts), so a choice is still
// the pair, and two choices are equal only when both halves are; the label
// names the source only then. Drafts,
// requests and API state keep the two fields as they are; the encoded key
// below exists only so a picker, which deals in strings, can tell two
// same-named options apart.

/** A model and the connection it runs on; null names no connection, so it cannot run. */
export interface ModelChoice {
  model: string;
  connectionId: string | null;
}

export const NO_MODEL: ModelChoice = { model: '', connectionId: null };

export function sameChoice(a: ModelChoice, b: ModelChoice): boolean {
  return a.model === b.model && (a.connectionId ?? null) === (b.connectionId ?? null);
}

/** The model a turn ran on, with its source. */
export function turnChoice(turn: {
  model: string;
  modelConnectionId?: string | null;
}): ModelChoice {
  return { model: turn.model, connectionId: turn.modelConnectionId ?? null };
}

/**
 * A picker's identity for a choice; `''` for none. Never stored or sent.
 *
 * A choice on a connection is the JSON pair, which starts `["mconn_`. One with
 * no connection keeps its name, or `[null, name]` if that starts with `[`, so
 * the two can never collide.
 */
export function choiceKey(choice: ModelChoice): string {
  if (!choice.model) return '';
  if (!choice.connectionId && !choice.model.startsWith('[')) return choice.model;
  return JSON.stringify([choice.connectionId ?? null, choice.model]);
}

/** A choice read from a stored draft, whose fields may be missing or malformed. */
export function storedChoice(model: unknown, connectionId: unknown): ModelChoice {
  if (typeof model !== 'string' || !model) return NO_MODEL;
  return {
    model,
    connectionId: typeof connectionId === 'string' && connectionId ? connectionId : null,
  };
}

/**
 * Every model a runtime's picker lists: the provided sources' models first,
 * then each of the owner's enabled connections', where this runtime can run
 * them. A model is listed only once its own verification found a way for this
 * agent to use it.
 */
export function listedChoices(config: AgentConfigApi | null, runtime: string): ModelChoice[] {
  return [...providedSources(config), ...ownConnections(config)].flatMap((source) =>
    source.models
      .filter((model) => source.model_runtimes?.[model]?.includes(runtime))
      .map((model) => ({ model, connectionId: source.id })),
  );
}

/**
 * What a runtime can run now: the listed models. Exactly what the server
 * accepts, so nothing is offered and then refused on Run.
 */
export function offeredChoices(config: AgentConfigApi | null, runtime: string): ModelChoice[] {
  return listedChoices(config, runtime);
}

/**
 * Whether a choice may not run here: a loaded catalog that is being read again,
 * or one that does not offer its exact source.
 *
 * Before any catalog has loaded nothing is known, and a turn inheriting its
 * source is left to the server, which checks that connection again on every
 * message and refuses one that cannot serve it. A choice the owner made was
 * picked from a loaded catalog, so it is always checked against one.
 */
export function isModelUnavailable(
  config: AgentConfigApi | null,
  runtime: string,
  choice: ModelChoice,
  refreshing = false,
): boolean {
  if (config === null) return false;
  if (refreshing) return true;
  return !offeredChoices(config, runtime).some((option) => sameChoice(option, choice));
}

/** What the server resolved for a new task, as a choice. */
export function resolvedChoice(config: AgentConfigApi): ModelChoice {
  return storedChoice(config.default_model, config.default_model_connection_id);
}

export interface LabelOptions {
  /** Not on offer here: marked, never swapped for another source. */
  unavailable?: boolean;
  /** The agent it would run with. Kept for callers; which API it uses is never shown. */
  runtime?: string;
}

/** The sources that list a model: the owner's enabled connections and the provided ones. */
function sourcesOf(config: AgentConfigApi | null, model: string): number {
  return [...(config?.provided_sources ?? []), ...(config?.model_connections ?? [])].filter(
    (entry) => entry.models.includes(model),
  ).length;
}

/** A choice's model, with its source's name where another source lists the same model. */
function sourceLabel(config: AgentConfigApi | null, choice: ModelChoice, t: Translator): string {
  const source = findSource(config, choice.connectionId);
  if (!source) {
    const provided = isProvidedId(choice.connectionId);
    return t(provided ? 'modelSource.providedUnknown' : 'modelSource.personalUnknown', {
      model: choice.model,
    });
  }
  return sourcesOf(config, choice.model) > 1
    ? t('modelSource.personal', { model: choice.model, name: source.name })
    : choice.model;
}

/**
 * A choice's model, and its source's name where another source lists the same
 * model, so two sources of one name never read alike; one that cannot run
 * here says so. Which
 * API the agent reaches it on is the platform's business and is not part of
 * the label.
 */
export function choiceLabel(
  config: AgentConfigApi | null,
  choice: ModelChoice,
  t: Translator,
  { unavailable = false }: LabelOptions = {},
): string {
  if (!choice.connectionId) return choice.model;
  const label = sourceLabel(config, choice, t);
  return unavailable ? t('modelSource.unavailable', { label }) : label;
}

export interface ModelPickerProps {
  value: string;
  options: string[];
  optionLabels: Record<string, string>;
  onChange: (key: string) => void;
}

/**
 * The props a `Picker` needs for these choices (`listedChoices`). `kept` is
 * shown first when it is not listed — the turn a follow-up inherits, or a
 * personal pick that can no longer run — so the control never shows one thing
 * while another runs.
 */
export function modelPicker(
  config: AgentConfigApi | null,
  listed: ModelChoice[],
  current: ModelChoice,
  onChange: (choice: ModelChoice) => void,
  t: Translator,
  { kept = current, runtime }: { kept?: ModelChoice; runtime?: string } = {},
): ModelPickerProps {
  const missing = kept.model && !listed.some((choice) => sameChoice(choice, kept));
  const choices = missing ? [kept, ...listed] : listed;
  // A model offered once keys by its own name; one offered by several sources
  // keys by the pair, so the picker can tell them apart.
  const named = new Map<string, number>();
  for (const choice of choices) named.set(choice.model, (named.get(choice.model) ?? 0) + 1);
  const keyOf = (choice: ModelChoice) =>
    named.get(choice.model) === 1 && !choice.model.startsWith('[')
      ? choice.model
      : choiceKey(choice);
  const optionLabels: Record<string, string> = {};
  const byKey = new Map<string, ModelChoice>();
  for (const choice of choices) {
    const key = keyOf(choice);
    byKey.set(key, choice);
    optionLabels[key] = choiceLabel(config, choice, t, {
      unavailable: missing === true && choice === kept,
      runtime,
    });
  }
  const shown = choices.find((choice) => sameChoice(choice, current));
  return {
    value: shown ? keyOf(shown) : choiceKey(current),
    options: [...byKey.keys()],
    optionLabels,
    onChange: (key) => {
      const next = byKey.get(key);
      if (next) onChange(next);
    },
  };
}

/**
 * The picker for one turn of a conversation on `runtime`, or null when there
 * is nothing to offer — the caller then names the model it inherits.
 */
export function turnModelPicker(
  config: AgentConfigApi | null,
  runtime: string,
  current: ModelChoice,
  onChange: (choice: ModelChoice) => void,
  t: Translator,
  inherited: ModelChoice,
): ModelPickerProps | null {
  const listed = listedChoices(config, runtime);
  if (!listed.length) return null;
  return modelPicker(config, listed, current, onChange, t, {
    kept: current.model ? current : inherited,
    runtime,
  });
}

/**
 * What a follow-up must send to run on `choice` instead of `parent`'s source.
 *
 * Nothing when both match: an omitted model inherits the model and its source
 * together. Otherwise the model always goes, with its connection when it has
 * one — a switch between sources of the same model name is still a switch.
 */
export function modelOverride(
  parent: { model: string; modelConnectionId?: string | null },
  choice: ModelChoice,
): { model?: string; model_connection_id?: string } {
  if (!choice.model || sameChoice(turnChoice(parent), choice)) return {};
  return {
    model: choice.model,
    ...(choice.connectionId ? { model_connection_id: choice.connectionId } : {}),
  };
}
