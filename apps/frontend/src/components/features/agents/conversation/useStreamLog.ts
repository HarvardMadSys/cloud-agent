// The event log a conversation keeps in step with its stream.
//
// Streamed frames are buffered for a short window and committed together, so a
// burst of deltas costs one render rather than one per frame. Rows are only
// appended: a commit never rewrites the rows already stored. A per-turn index
// rides along so projection can read one turn's slice without regrouping the
// whole conversation.

import { startTransition, useCallback, useEffect, useRef, useState } from 'react';

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

export function useStreamLog(): StreamLog {
  const [log, setLog] = useState<EventLog>({ live: [], thread: [] });
  const logRef = useRef<EventLog>({ live: [], thread: [] });
  const index = useRef<Map<string, AgentThreadEventApi[]>>(new Map());
  const pending = useRef<{ live: AgentJobEventApi[]; thread: AgentThreadEventApi[] }>({
    live: [],
    thread: [],
  });
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const stopTimer = useCallback(() => {
    if (timer.current === undefined) return;
    clearTimeout(timer.current);
    timer.current = undefined;
  }, []);

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
    appendToThreadIndex(index.current, buffered.thread);
    // The transcript is not urgent: input and the status pill stay responsive
    // while the frames of one window are committed together.
    startTransition(() => {
      const next = {
        live: mergeEventRows(logRef.current.live, buffered.live),
        thread: mergeEventRows(logRef.current.thread, buffered.thread),
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
    return buffered;
  }, [stopTimer]);

  const reset = useCallback(() => {
    stopTimer();
    pending.current = { live: [], thread: [] };
    publish({ live: [], thread: [] });
  }, [publish, stopTimer]);

  useEffect(() => stopTimer, [stopTimer]);

  return { log, index, publish, queue, flush, current, drain, reset };
}
