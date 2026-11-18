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

function runningJob(id: string, threadId: string): AgentJobApi {
  return {
    id,
    thread_id: threadId,
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
}

describe('streamed frame batching', () => {
  it('drops a buffered window when the reader switches turns', async () => {
    const first = runningJob('first-job', 'first-thread');
    const second = runningJob('second-job', 'second-thread');
    const secondEvent = { ...event(5, 'message', { text: 'second turn' }), job_id: second.id };
    vi.mocked(getAgentJob).mockImplementation(async (id) => (id === first.id ? first : second));
    vi.mocked(getAgentJobGitMetadata).mockResolvedValue({
      available: false,
    } as Awaited<ReturnType<typeof getAgentJobGitMetadata>>);
    vi.mocked(getAgentJobThread).mockImplementation(
      async (id) =>
        ({
          thread_id: id === first.id ? first.thread_id : second.thread_id,
          jobs: [id === first.id ? first : second],
          messages: [],
        }) as AgentThreadApi,
    );
    vi.mocked(listAllAgentThreadEvents).mockImplementation(async (id) =>
      id === first.id ? [] : [secondEvent],
    );
    vi.mocked(streamAgentJob).mockImplementation(stayOpen);

    const { result, rerender } = renderHook(({ id }) => useAgentJob(id), {
      initialProps: { id: first.id },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const oldStream = vi.mocked(streamAgentJob).mock.calls[0][1];
    act(() => oldStream.onEvent(event(100, 'message', { text: 'first turn' })));

    rerender({ id: second.id });
    await waitFor(() => expect(result.current.job?.id).toBe(second.id));
    // The old stream can still deliver while its passive cleanup runs. This
    // frame reaches the new turn's buffer and must be filtered by `job_id`.
    act(() => oldStream.onEvent(event(101, 'message', { text: 'old after switch' })));
    await flushStream();

    expect(result.current.events.map((row) => row.id)).toEqual([5]);
    expect(result.current.job?.events.some((row) => row.sourceEventId === 100)).toBe(false);
  });

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
