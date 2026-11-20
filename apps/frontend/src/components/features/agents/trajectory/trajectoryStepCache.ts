// Trajectory steps are built per turn and reused while that turn is settled.
//
// A streamed event only changes the live turn, so rebuilding every settled
// turn's steps for each frame repeats work that cannot change. Turns are
// separated by `job_id`; the log is contiguous per turn, which is what makes
// concatenating the per-turn steps the same as building them together.

import type { AgentThreadEventApi } from '@/lib/api/agents';
import {
  buildTrajectorySteps,
  type StoredEvent,
  type TrajectoryStep,
  type TurnContext,
} from './trajectorySteps';

type CachedTurn = {
  rows: StoredEvent[];
  context: TurnContext;
  steps: TrajectoryStep[];
};

function jobIdOf(event: StoredEvent): string {
  return (event as AgentThreadEventApi).job_id ?? '';
}

function sameRows(left: StoredEvent[], right: StoredEvent[]): boolean {
  return left.length === right.length && left.every((row, index) => row === right[index]);
}

/** The live turn's context only: the prompt of the turn these events belong to. */
function contextForTurn(context: TurnContext, jobId: string): TurnContext {
  if (!context.inputs) return context;
  return { ...context, inputs: context.inputs.filter((input) => input.jobId === jobId) };
}

/**
 * One cache per mounted conversation. A turn whose rows keep their identity
 * keeps its steps; only the turn that streamed new rows is built again.
 */
export function createTrajectoryStepCache(): (
  events: StoredEvent[],
  context: TurnContext,
) => TrajectoryStep[] {
  let cached = new Map<string, CachedTurn>();
  return (events, context) => {
    if (!events.length) return buildTrajectorySteps(events, context);
    const groups: Array<{ jobId: string; rows: StoredEvent[] }> = [];
    const seen = new Set<string>();
    let current: { jobId: string; rows: StoredEvent[] } | undefined;
    for (const event of events) {
      const jobId = jobIdOf(event);
      // A job-scoped read has no turn labels; there is nothing to split on.
      if (!jobId) return buildTrajectorySteps(events, context);
      if (current?.jobId !== jobId) {
        // Turns are stored in order. A repeated or missing turn means the log is
        // not laid out per turn, so build it whole rather than reorder it.
        if (seen.has(jobId)) return buildTrajectorySteps(events, context);
        seen.add(jobId);
        current = { jobId, rows: [] };
        groups.push(current);
      }
      current.rows.push(event);
    }
    // A turn with no stored events still has its prompt in the context. It
    // cannot be represented by a per-turn group, so keep the whole build.
    if (context.inputs?.some((input) => !seen.has(input.jobId))) {
      return buildTrajectorySteps(events, context);
    }
    const misses = groups.filter((group) => {
      const previous = cached.get(group.jobId);
      return !previous || previous.context !== context || !sameRows(previous.rows, group.rows);
    });
    if (misses.length === groups.length) {
      // A first build reads the log whole: one pass over every row, then the
      // steps are filed under the turn they belong to for later appends.
      const steps = buildTrajectorySteps(events, context);
      const byJob = new Map(groups.map((group) => [group.jobId, [] as TrajectoryStep[]]));
      for (const step of steps) {
        const group = byJob.get(step.jobId) ? step.jobId : groups[0].jobId;
        byJob.get(group)?.push(step);
      }
      cached = new Map(
        groups.map((group) => [
          group.jobId,
          { rows: group.rows, context, steps: byJob.get(group.jobId) ?? [] },
        ]),
      );
      return steps;
    }
    const next = new Map<string, CachedTurn>();
    const steps: TrajectoryStep[] = [];
    for (const group of groups) {
      const previous = cached.get(group.jobId);
      let entry: CachedTurn;
      if (previous && previous.context === context && sameRows(previous.rows, group.rows)) {
        entry = previous;
      } else {
        entry = {
          rows: group.rows,
          context,
          steps: buildTrajectorySteps(group.rows, contextForTurn(context, group.jobId)),
        };
      }
      next.set(group.jobId, entry);
      steps.push(...entry.steps);
    }
    cached = next;
    return steps;
  };
}
