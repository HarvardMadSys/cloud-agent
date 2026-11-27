// The conversation's event log, kept as ascending-id rows.
//
// SSE frames and history pages both arrive in id order, so merging is a
// two-pointer append rather than a sort. The hook keeps one log per view: the
// turn on screen and every turn of the conversation. Rows are immutable and
// are never rewritten, so a flush only has to add the rows it received.

import type { AgentThreadEventApi } from '@/lib/api/agents';

/** How long streamed frames wait before one state update is committed. */
export const STREAM_BATCH_MS = 100;

/**
 * `current` and `added` merged by ascending id, with `added` winning a
 * duplicate. Both inputs must already be ascending; that is how the server and
 * the stream deliver them.
 */
export function mergeEventRows<T extends { id: number }>(current: T[], added: readonly T[]): T[] {
  if (!added.length) return current;
  const last = current[current.length - 1];
  if (!last || added[0].id > last.id) return current.concat(added);
  const rows: T[] = [];
  let left = 0;
  let right = 0;
  while (left < current.length || right < added.length) {
    const older = current[left];
    const newer = added[right];
    if (newer === undefined || (older !== undefined && older.id < newer.id)) {
      rows.push(older);
      left += 1;
    } else if (older === undefined || newer.id < older.id) {
      rows.push(newer);
      right += 1;
    } else {
      rows.push(newer);
      left += 1;
      right += 1;
    }
  }
  return rows;
}

/** Every turn's rows, grouped by the turn they belong to. */
export function indexThreadEvents(rows: AgentThreadEventApi[]): Map<string, AgentThreadEventApi[]> {
  const byJob = new Map<string, AgentThreadEventApi[]>();
  for (const row of rows) {
    const group = byJob.get(row.job_id);
    if (group) group.push(row);
    else byJob.set(row.job_id, [row]);
  }
  return byJob;
}

export function appendToThreadIndex(
  byJob: Map<string, AgentThreadEventApi[]>,
  rows: AgentThreadEventApi[],
): void {
  for (const row of rows) {
    const group = byJob.get(row.job_id);
    // Rows are ascending and pages can overlap a streamed frame; a row at or
    // before the group's last id is one the projection already has.
    if (group) {
      const last = group[group.length - 1];
      if (last.id < row.id) group.push(row);
      else if (last.id === row.id) group[group.length - 1] = row;
    } else {
      byJob.set(row.job_id, [row]);
    }
  }
}
