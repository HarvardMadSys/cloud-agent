// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  getAgentJob,
  getAgentJobGitMetadata,
  getAgentJobThread,
  listAllAgentThreadEvents,
  streamAgentJob,
  type AgentJobApi,
  type AgentThreadApi,
} from '@/lib/api/agents';

import { event, flushStream, stayOpen } from '@/test/agentStreamTestHelpers';
import { useAgentJob } from './useAgentJobs';

vi.mock('@/lib/api/agents', () => ({
  getAgentJob: vi.fn(),
  getAgentJobGitMetadata: vi.fn(),
  getAgentJobThread: vi.fn(),
  listAllAgentThreadEvents: vi.fn(),
  streamAgentJob: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('streamed frame batching', () => {
  it('commits the frames of one window together', async () => {
    const job = {
      id: 'live-job',
      thread_id: 'live-thread',
      state: 'running',
      conversation_state: 'running',
      current_attempt_id: 10,
      turn_no: 1,
      task_prompt: 'Task',
      title: 'Task',
      title_source: 'manual',
      runtime: 'codex',
      model: 'test',
    } as AgentJobApi;
    vi.mocked(getAgentJob).mockResolvedValue(job);
    vi.mocked(getAgentJobGitMetadata).mockResolvedValue({
      available: false,
    } as Awaited<ReturnType<typeof getAgentJobGitMetadata>>);
    vi.mocked(getAgentJobThread).mockResolvedValue({
      thread_id: job.thread_id,
      jobs: [job],
      messages: [],
    } as AgentThreadApi);
    vi.mocked(listAllAgentThreadEvents).mockResolvedValue([]);
    vi.mocked(streamAgentJob).mockImplementation(stayOpen);

    const { result } = renderHook(() => useAgentJob(job.id));
    await waitFor(() => expect(result.current.loading).toBe(false));
    const stream = vi.mocked(streamAgentJob).mock.calls[0][1];

    act(() => {
      stream.onEvent(event(1, 'message', { text: 'one' }));
      stream.onEvent(event(2, 'message', { text: 'two' }));
    });
    expect(result.current.events).toEqual([]);

    await flushStream();
    expect(result.current.events.map((row) => row.id)).toEqual([1, 2]);
    expect(result.current.job?.events.map((row) => row.sourceEventId)).toEqual([1, 2]);
  });
});
