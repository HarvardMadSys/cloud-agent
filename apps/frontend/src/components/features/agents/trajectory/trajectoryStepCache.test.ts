import { describe, expect, it } from 'vitest';

import type { AgentThreadEventApi } from '@/lib/api/agents';
import { buildTrajectorySteps, turnContextFromJobs } from './trajectorySteps';
import { createTrajectoryStepCache } from './trajectoryStepCache';

function message(id: number, jobId: string, turnNo: number, text: string): AgentThreadEventApi {
  return {
    id,
    attempt_id: turnNo,
    seq: id,
    event_type: 'message',
    payload: { text },
    created_at: null,
    job_id: jobId,
    turn_no: turnNo,
  };
}

const JOBS = [
  { id: 'turn-1', state: 'succeeded', task_prompt: 'first', turn_no: 1, created_at: null },
  {
    id: 'turn-2',
    state: 'running',
    current_attempt_id: 2,
    task_prompt: 'second',
    turn_no: 2,
    created_at: null,
  },
];

describe('trajectory step cache', () => {
  it("keeps a settled turn's steps and rebuilds only the turn that grew", () => {
    const firstTurn = [message(1, 'turn-1', 1, 'done'), message(2, 'turn-1', 1, 'reply')];
    const secondTurn = [message(3, 'turn-2', 2, 'working')];
    const context = turnContextFromJobs(JOBS);
    const cache = createTrajectoryStepCache();

    const before = cache([...firstTurn, ...secondTurn], context);
    const settledSteps = before.filter((step) => step.jobId === 'turn-1');

    const after = cache([...firstTurn, ...secondTurn, message(4, 'turn-2', 2, 'more')], context);
    const reused = after.filter((step) => step.jobId === 'turn-1');

    expect(reused).toHaveLength(settledSteps.length);
    expect(reused.every((step, index) => step === settledSteps[index])).toBe(true);
    expect(after.filter((step) => step.jobId === 'turn-2').length).toBeGreaterThan(
      before.filter((step) => step.jobId === 'turn-2').length,
    );
  });

  it('falls back to the whole build when a turn has a prompt but no stored events', () => {
    const events = [message(1, 'turn-1', 1, 'done')];
    const context = turnContextFromJobs(JOBS);
    const cache = createTrajectoryStepCache();
    const steps = cache(events, context);
    expect(steps.some((step) => step.kind === 'user' && step.jobId === 'turn-2')).toBe(true);
  });

  it('falls back when turns are interleaved rather than stored in order', () => {
    const events = [
      message(1, 'turn-1', 1, 'one'),
      message(2, 'turn-2', 2, 'two'),
      message(3, 'turn-1', 1, 'one again'),
    ];
    const context = turnContextFromJobs(JOBS);
    const cache = createTrajectoryStepCache();
    expect(cache(events, context).map((step) => step.key)).toEqual(
      buildTrajectorySteps(events, context).map((step) => step.key),
    );
  });
});
