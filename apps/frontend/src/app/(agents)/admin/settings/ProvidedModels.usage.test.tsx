// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api/adminOperatorModels';

import ProvidedModelsSettings from './ProvidedModels';
import { utcDay } from './providedModelsForm';
import {
  COMPLETE,
  PROVIDED_ID,
  providedConnection,
  providedList,
  refusal,
} from './providedModelsTestFixtures';

// What each account used of a connection's models per day, and what it cost.

vi.mock('@/lib/api/adminOperatorModels', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/adminOperatorModels')>()),
  listOperatorModelConnections: vi.fn(),
  createOperatorModelConnection: vi.fn(),
  updateOperatorModelConnection: vi.fn(),
  deleteOperatorModelConnection: vi.fn(),
  setOperatorModelTerms: vi.fn(),
  listOperatorModelUsers: vi.fn(),
  setOperatorModelUserBudget: vi.fn(),
  clearOperatorModelUserBudget: vi.fn(),
  setOperatorModelUserAccess: vi.fn(),
  clearOperatorModelUserAccess: vi.fn(),
  getOperatorModelUsage: vi.fn(),
}));

const ESTIMATED =
  'Estimated: the provider’s answer named no usage, or was cut short before it could, so the request was charged an estimate of its input and of the output it had produced.';

/** One account's use of one model on one day: nothing estimated, nothing written to the cache. */
function used(overrides: Partial<api.OperatorModelUsageRow> = {}): api.OperatorModelUsageRow {
  return {
    day: '2026-11-12',
    account_id: 'acct_ada',
    email: 'ada@example.com',
    model: 'house-fast',
    requests: 42,
    cost_microusd: 1_250_042,
    input_tokens: 400_000,
    cache_read_tokens: 300_000,
    cache_write_tokens: 0,
    output_tokens: 9_000,
    estimated_requests: 0,
    ...overrides,
  };
}

async function openUsage() {
  const house = await screen.findByRole('article', { name: 'House models' });
  fireEvent.click(within(house).getByRole('button', { name: 'Show usage by day' }));
  return within(house).findByRole('region', { name: 'Usage' });
}

function rowTexts(table: HTMLElement): string[][] {
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole('cell')
        .map((cell) => cell.textContent ?? ''),
    );
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
    providedList([providedConnection(COMPLETE)]),
  );
});
afterEach(cleanup);

describe('what was used of a connection', () => {
  it('says cost is tokens at the model’s prices, and not the provider’s bill', async () => {
    render(<ProvidedModelsSettings />);
    const usage = await openUsage();
    expect(usage).toHaveTextContent(
      'What each account used per UTC day and model, as the provider’s answers reported it. Cost is the tokens each answer said it used, at the model’s prices at the time: it is what a daily budget counts, not the provider’s bill. Input tokens include the cached ones.',
    );
    // Nothing is read until a range is asked for.
    expect(api.getOperatorModelUsage).not.toHaveBeenCalled();
  });

  it('reads a date range and lists requests, tokens and cost per day, account and model', async () => {
    vi.mocked(api.getOperatorModelUsage).mockResolvedValue({
      rows: [
        used(),
        used({
          day: '2026-11-11',
          account_id: 'acct_bob',
          email: '',
          model: 'house-deep',
          requests: 3,
          cost_microusd: 4_200,
          input_tokens: 1_500,
          cache_read_tokens: 0,
          output_tokens: 20,
        }),
      ],
      truncated: false,
    });
    render(<ProvidedModelsSettings />);
    const usage = await openUsage();
    // The last seven UTC days, to start with.
    expect(within(usage).getByLabelText('From (UTC day)')).toHaveValue(utcDay(6));
    expect(within(usage).getByLabelText('To (UTC day)')).toHaveValue(utcDay());
    fireEvent.change(within(usage).getByLabelText('From (UTC day)'), {
      target: { value: '2026-11-01' },
    });
    fireEvent.change(within(usage).getByLabelText('To (UTC day)'), {
      target: { value: '2026-11-12' },
    });
    fireEvent.click(within(usage).getByRole('button', { name: 'Show usage' }));
    const table = await within(usage).findByRole('table', { name: 'Usage' });
    expect(api.getOperatorModelUsage).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
      from: '2026-11-01',
      to: '2026-11-12',
    });
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual([
      'Day (UTC)',
      'Account',
      'Model',
      'Requests',
      'Input tokens',
      'Cached tokens',
      'Output tokens',
      'Cost',
    ]);
    expect(rowTexts(table)).toEqual([
      // Cost to its last decimal: a fraction of a cent is still what the budget was charged.
      [
        '2026-11-12',
        'ada@example.com',
        'house-fast',
        '42',
        '400,000',
        '300,000',
        '9,000',
        '$1.2500',
      ],
      ['2026-11-11', 'acct_bob', 'house-deep', '3', '1,500', '0', '20', '$0.0042'],
    ]);
    expect(within(usage).queryByText(/More rows matched/)).not.toBeInTheDocument();
    // Nothing was charged by estimate, so nothing is said about estimates.
    expect(usage).not.toHaveTextContent(/estimate/i);
  });

  it('says how many requests were charged by estimate, and what that means', async () => {
    vi.mocked(api.getOperatorModelUsage).mockResolvedValue({
      rows: [used({ estimated_requests: 2 }), used({ day: '2026-11-11', requests: 1_200 })],
      truncated: false,
    });
    render(<ProvidedModelsSettings />);
    const usage = await openUsage();
    fireEvent.click(within(usage).getByRole('button', { name: 'Show usage' }));
    const table = await within(usage).findByRole('table', { name: 'Usage' });
    // In the requests of the row it happened in, and on no other.
    expect(rowTexts(table).map((row) => row[3])).toEqual(['42 2 estimated', '1,200']);
    expect(within(usage).getByText(ESTIMATED)).toBeVisible();
  });

  it('shows tokens written to the cache beside the ones read from it', async () => {
    vi.mocked(api.getOperatorModelUsage).mockResolvedValue({
      rows: [used({ cache_write_tokens: 12_000 }), used({ day: '2026-11-11' })],
      truncated: false,
    });
    render(<ProvidedModelsSettings />);
    const usage = await openUsage();
    fireEvent.click(within(usage).getByRole('button', { name: 'Show usage' }));
    const table = await within(usage).findByRole('table', { name: 'Usage' });
    expect(rowTexts(table).map((row) => row[5])).toEqual([
      '300,000 12,000 written to cache',
      '300,000',
    ]);
  });

  it('says when more rows matched than are shown, and when none did', async () => {
    vi.mocked(api.getOperatorModelUsage)
      .mockResolvedValueOnce({ rows: [used()], truncated: true })
      .mockResolvedValueOnce({ rows: [], truncated: false });
    render(<ProvidedModelsSettings />);
    const usage = await openUsage();
    fireEvent.click(within(usage).getByRole('button', { name: 'Show usage' }));
    expect(
      await within(usage).findByText(
        'More rows matched than are shown. Choose a shorter range to see the rest.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(usage).getByRole('button', { name: 'Show usage' }));
    expect(
      await within(usage).findByText('No usage was recorded in these days.'),
    ).toBeInTheDocument();
    expect(within(usage).queryByRole('table')).not.toBeInTheDocument();
  });

  it('will not ask for a range that ends before it starts', async () => {
    render(<ProvidedModelsSettings />);
    const usage = await openUsage();
    fireEvent.change(within(usage).getByLabelText('From (UTC day)'), {
      target: { value: '2026-11-12' },
    });
    fireEvent.change(within(usage).getByLabelText('To (UTC day)'), {
      target: { value: '2026-11-01' },
    });
    expect(within(usage).getByRole('button', { name: 'Show usage' })).toBeDisabled();
    expect(
      within(usage).getByText('Choose a first day that is not after the last day.'),
    ).toBeInTheDocument();
  });

  it('says what went wrong when the usage cannot be read', async () => {
    vi.mocked(api.getOperatorModelUsage).mockRejectedValue(
      refusal('invalid_usage_range', 'Choose a range of at most 92 days.', 422),
    );
    render(<ProvidedModelsSettings />);
    const usage = await openUsage();
    fireEvent.click(within(usage).getByRole('button', { name: 'Show usage' }));
    expect(await within(usage).findByRole('alert')).toHaveTextContent(
      'Choose a range of at most 92 days.',
    );
  });
});
