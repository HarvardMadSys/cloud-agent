// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AgentConfigApi } from '@/lib/api/agents';
import { providedSource, TEST_PROVIDED } from '@/test/connectionConfig';
import { useComposerModel } from './useComposerModel';

vi.mock('@/components/providers', () => ({ useT: () => (key: string) => key }));

const PROVIDED = { model: 'model-1', connectionId: TEST_PROVIDED };

/** An owner whose default the server resolved to a provided model. */
function loaded(): AgentConfigApi {
  return {
    repos: [],
    runtimes: ['codex'],
    github_connected: true,
    github_install_url: null,
    default_model: 'model-1',
    default_model_connection_id: TEST_PROVIDED,
    model_connections_enabled: true,
    model_connections: [],
    provided_sources: [providedSource(['model-1', 'model-2'])],
  };
}

function composer(config: AgentConfigApi) {
  const view = renderHook(() => useComposerModel(null, 'codex'));
  act(() => view.result.current.adoptModels(config));
  return view;
}

afterEach(cleanup);

describe('a provided model a new owner starts on', () => {
  it('runs with nothing chosen, and nothing counted down beside it', () => {
    const { result } = composer(loaded());
    expect(result.current.missingModel).toBe(false);
    expect(result.current.choice).toEqual(PROVIDED);
    expect(result.current.requestFields).toEqual({
      model: 'model-1',
      model_connection_id: TEST_PROVIDED,
    });
    expect(result.current.unavailableHint).toBe('');
    expect(result.current.picker).not.toHaveProperty('optionNotes');
    expect(result.current.picker).not.toHaveProperty('disabledOptions');
  });

  it('sets no timer: there is no day to wait out', () => {
    vi.useFakeTimers();
    try {
      composer(loaded());
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
