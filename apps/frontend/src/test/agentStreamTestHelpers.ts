// Shared fixtures for the stream-to-log tests.
import { act } from '@testing-library/react';

import type { AgentThreadEventApi } from '@/lib/api/agents';
import { STREAM_BATCH_MS } from '@/components/features/agents/conversation/eventLog';

/** Let one streamed frame window commit. */
export async function flushStream(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, STREAM_BATCH_MS));
  });
}

/** A turn's stream that stays open; one that returns without the turn ending could not connect. */
export function stayOpen(): Promise<void> {
  return new Promise(() => undefined);
}

export function event(
  id: number,
  event_type: string,
  payload: Record<string, unknown>,
  attempt_id = 10,
): AgentThreadEventApi {
  return {
    id,
    seq: id,
    event_type,
    payload,
    attempt_id,
    job_id: 'live-job',
    turn_no: 1,
    created_at: `2026-09-11T10:00:0${id}.000Z`,
  } as AgentThreadEventApi;
}
