import { describe, expect, it } from 'vitest';

import type { AgentThreadEventApi } from '@/lib/api/agents';
import {
  appendToThreadIndex,
  indexThreadEvents,
  mergeEventRows,
  STREAM_BATCH_MS,
} from './eventLog';

function row(id: number, jobId = 'job-a'): AgentThreadEventApi {
  return {
    id,
    attempt_id: 1,
    seq: id,
    event_type: 'message',
    payload: { text: `event ${id}` },
    created_at: null,
    job_id: jobId,
    turn_no: jobId === 'job-a' ? 1 : 2,
  };
}

describe('mergeEventRows', () => {
  it('appends a page that follows the log without comparing rows', () => {
    const current = [row(1), row(2)];
    const merged = mergeEventRows(current, [row(3), row(4)]);
    expect(merged.map((item) => item.id)).toEqual([1, 2, 3, 4]);
    expect(current.map((item) => item.id)).toEqual([1, 2]);
  });

  it('returns the held log when the page adds nothing', () => {
    const current = [row(1)];
    expect(mergeEventRows(current, [])).toBe(current);
  });

  it('keeps one row per id and lets the newer copy win', () => {
    const older = row(2);
    const newer = { ...older, payload: { text: 'rewritten' } };
    const merged = mergeEventRows([row(1), older, row(3)], [newer]);
    expect(merged.map((item) => item.id)).toEqual([1, 2, 3]);
    expect(merged[1]).toBe(newer);
  });

  it('interleaves a page that reaches back into the log', () => {
    const merged = mergeEventRows([row(1), row(4)], [row(2), row(3), row(4)]);
    expect(merged.map((item) => item.id)).toEqual([1, 2, 3, 4]);
  });
});

describe('thread event index', () => {
  it('groups rows by turn and appends each turn in order', () => {
    const index = indexThreadEvents([row(1), row(2), row(3, 'job-b')]);
    appendToThreadIndex(index, [row(4), row(5, 'job-b')]);
    expect(index.get('job-a')?.map((item) => item.id)).toEqual([1, 2, 4]);
    expect(index.get('job-b')?.map((item) => item.id)).toEqual([3, 5]);
  });

  it('replaces a row that arrived again and ignores an older one', () => {
    const index = indexThreadEvents([row(1)]);
    const rewritten = { ...row(1), payload: { text: 'newer' } };
    appendToThreadIndex(index, [rewritten, row(0)]);
    expect(index.get('job-a')).toEqual([rewritten]);
  });
});

it('waits long enough for a frame window but no longer than the issue allows', () => {
  expect(STREAM_BATCH_MS).toBeGreaterThanOrEqual(100);
  expect(STREAM_BATCH_MS).toBeLessThanOrEqual(150);
});
