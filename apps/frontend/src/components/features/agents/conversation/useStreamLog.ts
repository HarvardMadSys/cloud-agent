// The event log a conversation keeps in step with its stream.
//
// Streamed frames are buffered for a short window and committed together, so a
// burst of deltas costs one render rather than one per frame. Rows are only
// appended: a commit never rewrites the rows already stored. A per-turn index
// rides along so projection can read one turn's slice without regrouping the
// whole conversation.

import { startTransition, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import type { AgentJobEventApi, AgentThreadEventApi } from '@/lib/api/agents';
import {
  appendToThreadIndex,
  indexThreadEvents,
  mergeEventRows,
  STREAM_BATCH_MS,
} from './eventLog';

export interface EventLog {
  /** The turn on screen, unlabelled. */
  live: AgentJobEventApi[];
  /** Every turn, labelled with the job and turn it belongs to. */
  thread: AgentThreadEventApi[];
}

export interface StreamLog {
  log: EventLog;
  /** Each turn's slice of `log.thread`, kept current as rows are appended. */
  index: React.MutableRefObject<Map<string, AgentThreadEventApi[]>>;
  /** Replace the log, as a history read does, and regroup its index. */
  publish: (next: EventLog) => void;
  /** Buffer one streamed frame; it is committed with the rest of its window. */
  queue: (event: AgentJobEventApi, threadEvent: AgentThreadEventApi) => void;
  /** Commit buffered frames now, before a read replaces them. */
  flush: () => void;
  /** The currently published log, outside a render. */
  current: () => EventLog;
  /** Buffered frames not yet published, removed from the buffer. */
  drain: () => { live: AgentJobEventApi[]; thread: AgentThreadEventApi[] };
  /** Drop the log and any buffered frames, as a read-scope change does. */
  reset: () => void;
}

/** Buffered rows that belong to `jobId`; the two arrays are parallel by id. */
function scopedBuffered(
  buffered: { live: AgentJobEventApi[]; thread: AgentThreadEventApi[] },
  jobId: string,
): { live: AgentJobEventApi[]; thread: AgentThreadEventApi[] } {
  if (buffered.thread.every((row) => row.job_id === jobId)) return buffered;
  const thread = buffered.thread.filter((row) => row.job_id === jobId);
  const ids = new Set(thread.map((row) => row.id));
  return { live: buffered.live.filter((row) => ids.has(row.id)), thread };
}

export function useStreamLog(jobId: string): StreamLog {
  const [log, setLog] = useState<EventLog>({ live: [], thread: [] });
  const logRef = useRef<EventLog>({ live: [], thread: [] });
  const index = useRef<Map<string, AgentThreadEventApi[]>>(new Map());
  const pending = useRef<{ live: AgentJobEventApi[]; thread: AgentThreadEventApi[] }>({
    live: [],
    thread: [],
  });
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scope = useRef(jobId);

  const stopTimer = useCallback(() => {
    if (timer.current === undefined) return;
    clearTimeout(timer.current);
    timer.current = undefined;
  }, []);

  // A turn switch drops what the old stream buffered. It belongs to the turn
  // being left, and committing it into the new turn's log would show those
  // rows under the wrong job.
  useLayoutEffect(() => {
    if (scope.current === jobId) return;
    scope.current = jobId;
    stopTimer();
    pending.current = { live: [], thread: [] };
  }, [jobId, stopTimer]);

  const publish = useCallback((next: EventLog) => {
    logRef.current = next;
    index.current = indexThreadEvents(next.thread);
    setLog(next);
  }, []);

  const flush = useCallback(() => {
    stopTimer();
    const buffered = pending.current;
    if (!buffered.live.length && !buffered.thread.length) return;
    pending.current = { live: [], thread: [] };
    const scoped = scopedBuffered(buffered, scope.current);
    if (!scoped.live.length && !scoped.thread.length) return;
    appendToThreadIndex(index.current, scoped.thread);
    // The transcript is not urgent: input and the status pill stay responsive
    // while the frames of one window are committed together.
    startTransition(() => {
      const next = {
        live: mergeEventRows(logRef.current.live, scoped.live),
        thread: mergeEventRows(logRef.current.thread, scoped.thread),
      };
      logRef.current = next;
      setLog(next);
    });
  }, [stopTimer]);

  const queue = useCallback(
    (event: AgentJobEventApi, threadEvent: AgentThreadEventApi) => {
      pending.current.live.push(event);
      pending.current.thread.push(threadEvent);
      if (timer.current === undefined) timer.current = setTimeout(flush, STREAM_BATCH_MS);
    },
    [flush],
  );

  const current = useCallback(() => logRef.current, []);

  const drain = useCallback(() => {
    stopTimer();
    const buffered = pending.current;
    pending.current = { live: [], thread: [] };
    return scopedBuffered(buffered, scope.current);
  }, [stopTimer]);

  const reset = useCallback(() => {
    stopTimer();
    pending.current = { live: [], thread: [] };
    publish({ live: [], thread: [] });
  }, [publish, stopTimer]);

  useEffect(() => stopTimer, [stopTimer]);

  return { log, index, publish, queue, flush, current, drain, reset };
}
