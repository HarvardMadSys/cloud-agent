// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { chooseOption, optionNames } from '@/components/ui/__tests__/select';
import * as api from '@/lib/api/adminOperatorModels';

import ProvidedModelsSettings from './ProvidedModels';
import {
  account,
  COMPLETE,
  PROVIDED_ID,
  providedConnection,
  providedList,
  refusal,
} from './providedModelsTestFixtures';

// The accounts that differ from a connection's defaults: their own daily
// budget, their own answer on a model, and what they used today.
//
// A write answers the account's whole line as the list has it, so the fake
// API answers whole lines too: the table shows what was answered, never what
// it worked out from the request.

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

/** $20.00 a day of her own, one model refused and one offered by name, and use today. */
const ADA = account('acct_ada', {
  email: 'ada@example.com',
  daily_budget_microusd: 20_000_000,
  access: { 'house-fast': false, 'house-deep': true },
  spent_today_microusd: 1_250_042,
  requests_today: 42,
});
/** Nothing of his own: listed for what he used today, on the defaults. */
const BOB = account('acct_bob', { spent_today_microusd: 4_200, requests_today: 3 });

const BUDGET_FORM = 'Set a daily budget for an account';
const ACCESS_FORM = 'Offer or withhold a model for an account';

async function card() {
  return screen.findByRole('article', { name: 'House models' });
}

async function openAccounts() {
  const house = await card();
  fireEvent.click(within(house).getByRole('button', { name: 'Show accounts and their budgets' }));
  return within(house).findByRole('region', { name: 'Accounts' });
}

/** The table's rows, each as the text of its cells. */
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

/** The row of the account named `name` in the table. */
function accountRow(table: HTMLElement, name: string): HTMLElement {
  const row = within(table)
    .getAllByRole('row')
    .find((candidate) => candidate.textContent?.includes(name));
  if (!row) throw new Error(`No row for ${name}.`);
  return row;
}

function listed(...users: api.OperatorModelUser[]) {
  vi.mocked(api.listOperatorModelUsers).mockResolvedValue({ users, next_cursor: null });
}

/** Ada is on the first page; more accounts follow on pages not read yet. */
function firstPageOnly() {
  vi.mocked(api.listOperatorModelUsers).mockResolvedValueOnce({
    users: [ADA],
    next_cursor: 'acct_ada',
  });
}

/** Ada's line as the table shows it while nothing of hers has changed. */
const ADA_ROW = [
  'ada@example.comacct_ada',
  '$20.00Clear',
  '$1.2500',
  '42',
  'house-deep OfferedClearhouse-fast WithheldClear',
];

/** Fill and send the form that gives an account a budget of its own. */
function setBudget(accounts: HTMLElement, name: string, amount: string) {
  const form = within(accounts).getByRole('form', { name: BUDGET_FORM });
  fireEvent.change(within(form).getByLabelText('Account'), { target: { value: name } });
  fireEvent.change(within(form).getByLabelText('Daily budget (USD)'), {
    target: { value: amount },
  });
  fireEvent.click(within(form).getByRole('button', { name: 'Set budget' }));
  return form;
}

/** Fill and send the form that offers an account a model, or withholds it. */
function setAccess(accounts: HTMLElement, name: string, model: string, access: string) {
  const form = within(accounts).getByRole('form', { name: ACCESS_FORM });
  fireEvent.change(within(form).getByLabelText('Account'), { target: { value: name } });
  chooseOption(within(form).getByLabelText('Model'), model);
  chooseOption(within(form).getByLabelText('Access'), access);
  fireEvent.click(within(form).getByRole('button', { name: 'Save access' }));
  return form;
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
    providedList([providedConnection(COMPLETE)]),
  );
  listed();
});
afterEach(cleanup);

describe('the accounts that differ from the defaults', () => {
  it('asks for nothing until it is opened', async () => {
    render(<ProvidedModelsSettings />);
    await card();
    expect(api.listOperatorModelUsers).not.toHaveBeenCalled();
    expect(api.getOperatorModelUsage).not.toHaveBeenCalled();
  });

  it('lists each account with its own budget, what it spent and asked today, and its own models', async () => {
    listed(ADA, BOB, account('acct_cy', { email: 'cy@example.com', daily_budget_microusd: 0 }));
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    expect(api.listOperatorModelUsers).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
      cursor: null,
    });
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual(['Account', 'Own daily budget', 'Spent today', 'Requests today', 'Own model access']);
    expect(rowTexts(table)).toEqual([
      [
        'ada@example.comacct_ada',
        '$20.00Clear',
        // To the last decimal: this is what her budget is counted against.
        '$1.2500',
        '42',
        'house-deep OfferedClearhouse-fast WithheldClear',
      ],
      // Use today on the defaults: nothing of his own to clear.
      ['acct_bob', 'Follows the default', '$0.0042', '3', 'Follows each model’s default'],
      [
        'cy@example.comacct_cy',
        '$0.00 (connection withheld)Clear',
        '$0.0000',
        '0',
        'Follows each model’s default',
      ],
    ]);
    // Each thing of an account's own says what clearing it does, and for whom.
    const ada = accountRow(table, 'ada@example.com');
    expect(
      within(ada)
        .getAllByRole('button')
        .map((button) => button.getAttribute('aria-label')),
    ).toEqual([
      'Put ada@example.com back on the default budget',
      'Put ada@example.com back on the default for house-deep',
      'Put ada@example.com back on the default for house-fast',
    ]);
    expect(within(accountRow(table, 'acct_bob')).queryByRole('button')).toBeNull();
  });

  it('says so when no account differs from the defaults', async () => {
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    expect(
      await within(accounts).findByText(
        'No account has a budget of its own or a model offered or withheld by name, and none has used a model today.',
      ),
    ).toBeInTheDocument();
    expect(within(accounts).queryByRole('table')).not.toBeInTheDocument();
  });

  it('says what went wrong when the list cannot be read, and not that it is empty', async () => {
    vi.mocked(api.listOperatorModelUsers).mockRejectedValue(
      refusal('SERVER_ERROR', 'HTTP 500', 500),
    );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    expect(await within(accounts).findByText(/Server error/)).toHaveAttribute('role', 'alert');
    expect(
      within(accounts).queryByText(/No account has a budget of its own/),
    ).not.toBeInTheDocument();
  });

  it('loads further pages by cursor, keeping the ones already shown', async () => {
    vi.mocked(api.listOperatorModelUsers)
      .mockResolvedValueOnce({ users: [ADA], next_cursor: 'acct_ada' })
      .mockResolvedValueOnce({ users: [BOB], next_cursor: null });
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    fireEvent.click(await within(accounts).findByRole('button', { name: 'Load more accounts' }));
    await within(accounts).findByText('acct_bob');
    expect(api.listOperatorModelUsers).toHaveBeenLastCalledWith(PROVIDED_ID, {
      cursor: 'acct_ada',
    });
    expect(within(accounts).getByText('ada@example.com')).toBeInTheDocument();
    expect(rowTexts(within(accounts).getByRole('table'))).toHaveLength(2);
    expect(within(accounts).queryByRole('button', { name: 'Load more accounts' })).toBeNull();
  });
});

describe('an account’s own daily budget', () => {
  it('is set for an account named by email, in dollars', async () => {
    vi.mocked(api.setOperatorModelUserBudget).mockResolvedValue(
      account('acct_cy', { email: 'cy@example.com', daily_budget_microusd: 12_500_000 }),
    );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const form = within(accounts).getByRole('form', { name: BUDGET_FORM });
    expect(within(form).getByRole('button', { name: 'Set budget' })).toBeDisabled();
    expect(within(form).getByLabelText('Daily budget (USD)')).toHaveAccessibleDescription(
      'Replaces the connection’s default for this account. 0 withholds the connection from it.',
    );
    setBudget(accounts, ' cy@example.com ', '12.5');
    await waitFor(() =>
      expect(api.setOperatorModelUserBudget).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        account: 'cy@example.com',
        daily_budget_microusd: 12_500_000,
      }),
    );
    // The line answered is all the server holds for cy: this budget, and no use today.
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    expect(rowTexts(table)).toEqual([
      ['cy@example.comacct_cy', '$12.50Clear', '$0.0000', '0', 'Follows each model’s default'],
    ]);
    // The form is ready for the next account at the same budget.
    expect(within(form).getByLabelText('Account')).toHaveValue('');
    expect(within(form).getByLabelText('Daily budget (USD)')).toHaveValue('12.5');
  });

  it('shows the account’s line as the server answered it, in the place it had', async () => {
    listed(ADA, BOB);
    // Her whole line as the list has it now. Since this page read it she was served more
    // requests, and her answer on house-deep was cleared elsewhere.
    vi.mocked(api.setOperatorModelUserBudget).mockResolvedValue({
      ...ADA,
      daily_budget_microusd: 0,
      access: { 'house-fast': false },
      spent_today_microusd: 1_900_000,
      requests_today: 57,
    });
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    expect(rowTexts(table)[0]).toEqual(ADA_ROW);
    setBudget(accounts, 'acct_ada', '0');
    await waitFor(() =>
      expect(api.setOperatorModelUserBudget).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        account: 'acct_ada',
        daily_budget_microusd: 0,
      }),
    );
    // Her one row is replaced where it stood, never listed twice, and all of it is the answer's.
    await waitFor(() =>
      expect(rowTexts(table)).toEqual([
        [
          'ada@example.comacct_ada',
          '$0.00 (connection withheld)Clear',
          '$1.9000',
          '57',
          'house-fast WithheldClear',
        ],
        ['acct_bob', 'Follows the default', '$0.0042', '3', 'Follows each model’s default'],
      ]),
    );
  });

  it('will not send a budget that is not an amount of dollars', async () => {
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const form = within(accounts).getByRole('form', { name: BUDGET_FORM });
    const amount = within(form).getByLabelText('Daily budget (USD)');
    fireEvent.change(within(form).getByLabelText('Account'), { target: { value: 'acct_ada' } });
    // Nothing typed is not a mistake yet; it is not a budget either.
    expect(amount).toBeValid();
    expect(within(form).getByRole('button', { name: 'Set budget' })).toBeDisabled();
    for (const value of ['-1', 'lots', '1.1234567', '1000000.01', '$5']) {
      fireEvent.change(amount, { target: { value } });
      expect(within(form).getByRole('button', { name: 'Set budget' }), value).toBeDisabled();
      expect(amount, value).toBeInvalid();
    }
    expect(amount).toHaveAccessibleDescription(
      /^Enter an amount in US dollars from 0 to \$1,000,000\.00, with at most six decimal places\./,
    );
    fireEvent.submit(form);
    expect(api.setOperatorModelUserBudget).not.toHaveBeenCalled();
    fireEvent.change(amount, { target: { value: '1000000' } });
    expect(amount).toBeValid();
    expect(within(form).getByRole('button', { name: 'Set budget' })).toBeEnabled();
  });

  it('says when no account has that email or id, and keeps what was typed', async () => {
    vi.mocked(api.setOperatorModelUserBudget).mockRejectedValue(
      refusal('account_not_found', 'No such account.', 404),
    );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const form = setBudget(accounts, 'nobody@x.io', '5');
    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'No account has that email address or id.',
    );
    expect(within(form).getByLabelText('Account')).toHaveValue('nobody@x.io');
    expect(within(accounts).queryByRole('table')).not.toBeInTheDocument();
  });

  it('is cleared to put the account back on the default', async () => {
    listed(ADA, account('acct_bob', { daily_budget_microusd: 1_000_000 }));
    vi.mocked(api.clearOperatorModelUserBudget).mockResolvedValue();
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });

    fireEvent.click(
      within(table).getByRole('button', { name: 'Put ada@example.com back on the default budget' }),
    );
    await waitFor(() =>
      expect(api.clearOperatorModelUserBudget).toHaveBeenCalledExactlyOnceWith(
        PROVIDED_ID,
        'acct_ada',
      ),
    );
    // Ada has models of her own and use today, so her line stays, now on the default budget.
    await waitFor(() =>
      expect(rowTexts(table)[0]).toEqual([
        'ada@example.comacct_ada',
        'Follows the default',
        '$1.2500',
        '42',
        'house-deep OfferedClearhouse-fast WithheldClear',
      ]),
    );

    // Bob had only that budget and no use today: nothing is left to list.
    fireEvent.click(
      within(table).getByRole('button', { name: 'Put acct_bob back on the default budget' }),
    );
    await waitFor(() => expect(within(table).queryByText('acct_bob')).not.toBeInTheDocument());
    expect(api.clearOperatorModelUserBudget).toHaveBeenLastCalledWith(PROVIDED_ID, 'acct_bob');
  });

  it('is dropped from a line when it was already cleared elsewhere, without an error', async () => {
    listed(account('acct_bob', { daily_budget_microusd: 1_000_000 }));
    vi.mocked(api.clearOperatorModelUserBudget).mockRejectedValue(
      refusal('operator_model_user_budget_not_found', 'No budget of its own.', 404),
    );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    fireEvent.click(
      await within(accounts).findByRole('button', {
        name: 'Put acct_bob back on the default budget',
      }),
    );
    await waitFor(() => expect(within(accounts).queryByRole('table')).not.toBeInTheDocument());
    expect(within(accounts).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('stays, with why, when clearing it fails', async () => {
    listed(ADA);
    vi.mocked(api.clearOperatorModelUserBudget).mockRejectedValue(
      refusal('SERVER_ERROR', 'HTTP 500', 500),
    );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    fireEvent.click(
      within(table).getByRole('button', { name: 'Put ada@example.com back on the default budget' }),
    );
    expect(await within(accounts).findByText(/Server error/)).toHaveAttribute('role', 'alert');
    expect(rowTexts(table)[0][1]).toBe('$20.00Clear');
  });
});

describe('an account’s own answer on a model', () => {
  it('offers a model to an account by name, or withholds it', async () => {
    listed(BOB);
    vi.mocked(api.setOperatorModelUserAccess)
      .mockResolvedValueOnce({ ...BOB, access: { 'house-deep': true } })
      .mockResolvedValueOnce({ ...BOB, access: { 'house-deep': true, 'house-fast': false } });
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    const form = within(accounts).getByRole('form', { name: ACCESS_FORM });
    expect(within(form).getByRole('button', { name: 'Save access' })).toBeDisabled();
    expect(optionNames(within(form).getByLabelText('Model'))).toEqual(['house-fast', 'house-deep']);
    expect(optionNames(within(form).getByLabelText('Access'))).toEqual([
      'Offer the model',
      'Withhold the model',
    ]);
    expect(within(form).getByLabelText('Access')).toHaveAccessibleDescription(
      'Replaces the model’s default for this account: open to every account with a budget, or only to named accounts.',
    );

    setAccess(accounts, ' acct_bob ', 'house-deep', 'Offer the model');
    await waitFor(() => expect(within(form).getByLabelText('Account')).toHaveValue(''));
    setAccess(accounts, 'acct_bob', 'house-fast', 'Withhold the model');
    await waitFor(() => expect(api.setOperatorModelUserAccess).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.setOperatorModelUserAccess).mock.calls.map((call) => call[1])).toEqual([
      { account: 'acct_bob', model: 'house-deep', offered: true },
      { account: 'acct_bob', model: 'house-fast', offered: false },
    ]);
    // His one line is the server's each time: both answers, and what he used today.
    await waitFor(() =>
      expect(rowTexts(table)).toEqual([
        [
          'acct_bob',
          'Follows the default',
          '$0.0042',
          '3',
          'house-deep OfferedClearhouse-fast WithheldClear',
        ],
      ]),
    );
  });

  it('replaces the answer an account already had on that model', async () => {
    listed(ADA);
    vi.mocked(api.setOperatorModelUserAccess).mockResolvedValue({
      ...ADA,
      access: { ...ADA.access, 'house-fast': true },
    });
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    setAccess(accounts, 'ada@example.com', 'house-fast', 'Offer the model');
    await waitFor(() =>
      expect(rowTexts(table)[0]).toEqual([
        'ada@example.comacct_ada',
        '$20.00Clear',
        '$1.2500',
        '42',
        'house-deep OfferedClearhouse-fast OfferedClear',
      ]),
    );
  });

  it('says when the account is unknown or the model is no longer listed', async () => {
    vi.mocked(api.setOperatorModelUserAccess)
      .mockRejectedValueOnce(refusal('account_not_found', 'No such account.', 404))
      .mockRejectedValueOnce(
        refusal('operator_model_not_listed', 'That model is not listed on this connection.', 404),
      );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const form = setAccess(accounts, 'nobody@x.io', 'house-fast', 'Offer the model');
    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'No account has that email address or id.',
    );
    expect(within(form).getByLabelText('Account')).toHaveValue('nobody@x.io');
    // The model was taken off the connection since this page read it: said in this page's words.
    fireEvent.click(within(form).getByRole('button', { name: 'Save access' }));
    expect(
      await within(form).findByText(
        'That model is no longer listed on this connection. Reload the page to see the current list.',
      ),
    ).toHaveAttribute('role', 'alert');
    expect(form).not.toHaveTextContent('That model is not listed on this connection.');
    expect(within(accounts).queryByRole('table')).not.toBeInTheDocument();
  });

  it('is cleared to put the account back on the model’s default', async () => {
    listed(ADA, account('acct_bob', { access: { 'house-fast': true } }));
    vi.mocked(api.clearOperatorModelUserAccess).mockResolvedValue();
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });

    fireEvent.click(
      within(table).getByRole('button', {
        name: 'Put ada@example.com back on the default for house-fast',
      }),
    );
    await waitFor(() =>
      expect(api.clearOperatorModelUserAccess).toHaveBeenCalledExactlyOnceWith(
        PROVIDED_ID,
        'acct_ada',
        'house-fast',
      ),
    );
    // Her other answer and her budget are untouched.
    await waitFor(() =>
      expect(rowTexts(table)[0]).toEqual([
        'ada@example.comacct_ada',
        '$20.00Clear',
        '$1.2500',
        '42',
        'house-deep OfferedClear',
      ]),
    );

    // Bob had only that answer and no use today: nothing is left to list.
    fireEvent.click(
      within(table).getByRole('button', {
        name: 'Put acct_bob back on the default for house-fast',
      }),
    );
    await waitFor(() => expect(within(table).queryByText('acct_bob')).not.toBeInTheDocument());
    expect(api.clearOperatorModelUserAccess).toHaveBeenLastCalledWith(
      PROVIDED_ID,
      'acct_bob',
      'house-fast',
    );
  });

  it('is dropped from a line when it was already cleared elsewhere, without an error', async () => {
    listed(account('acct_bob', { access: { 'house-fast': false } }));
    vi.mocked(api.clearOperatorModelUserAccess).mockRejectedValue(
      refusal('operator_model_user_access_not_found', 'No answer of its own.', 404),
    );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    fireEvent.click(
      await within(accounts).findByRole('button', {
        name: 'Put acct_bob back on the default for house-fast',
      }),
    );
    await waitFor(() => expect(within(accounts).queryByRole('table')).not.toBeInTheDocument());
    expect(within(accounts).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('stays, with why, when clearing it fails, and nothing else is cleared meanwhile', async () => {
    listed(ADA);
    let refuse!: (cause: unknown) => void;
    vi.mocked(api.clearOperatorModelUserAccess).mockReturnValue(
      new Promise((_resolve, reject) => {
        refuse = reject;
      }),
    );
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    const clear = within(table).getByRole('button', {
      name: 'Put ada@example.com back on the default for house-deep',
    });
    fireEvent.click(clear);
    // One clear at a time: the one in flight says so, the others wait.
    await waitFor(() => expect(clear).toHaveAttribute('aria-busy', 'true'));
    for (const button of within(table).getAllByRole('button')) expect(button).toBeDisabled();
    refuse(refusal('SERVER_ERROR', 'HTTP 500', 500));
    expect(await within(accounts).findByText(/Server error/)).toHaveAttribute('role', 'alert');
    expect(rowTexts(table)[0][4]).toBe('house-deep OfferedClearhouse-fast WithheldClear');
    expect(clear).toBeEnabled();
    expect(api.clearOperatorModelUserBudget).not.toHaveBeenCalled();
  });
});

describe('an account that was not in the table', () => {
  /** Zed as the server holds him: a model withheld by name, and use today. */
  const ZED = account('acct_zed', {
    email: 'zed@example.com',
    access: { 'house-deep': false },
    spent_today_microusd: 3_100_000,
    requests_today: 9,
  });

  it('is added with what the server holds for it, not only the budget written', async () => {
    firstPageOnly();
    vi.mocked(api.setOperatorModelUserBudget).mockResolvedValue({
      ...ZED,
      daily_budget_microusd: 7_000_000,
    });
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    setBudget(accounts, 'zed@example.com', '7');
    // What he spent and asked today, and his other answer, come with the answer.
    await waitFor(() =>
      expect(rowTexts(table)).toEqual([
        ADA_ROW,
        ['zed@example.comacct_zed', '$7.00Clear', '$3.1000', '9', 'house-deep WithheldClear'],
      ]),
    );

    // When his page is read he is not listed a second time: the page's line takes his place.
    vi.mocked(api.listOperatorModelUsers).mockResolvedValueOnce({
      users: [BOB, { ...ZED, daily_budget_microusd: 7_000_000, spent_today_microusd: 3_400_000 }],
      next_cursor: null,
    });
    fireEvent.click(within(accounts).getByRole('button', { name: 'Load more accounts' }));
    await waitFor(() =>
      expect(rowTexts(table)).toEqual([
        ADA_ROW,
        ['zed@example.comacct_zed', '$7.00Clear', '$3.4000', '9', 'house-deep WithheldClear'],
        ['acct_bob', 'Follows the default', '$0.0042', '3', 'Follows each model’s default'],
      ]),
    );
  });

  it('is added with its own budget and its use today when a model is offered to it', async () => {
    firstPageOnly();
    vi.mocked(api.setOperatorModelUserAccess).mockResolvedValue({
      ...ZED,
      daily_budget_microusd: 0,
      access: { 'house-deep': false, 'house-fast': true },
    });
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    setAccess(accounts, 'acct_zed', 'house-fast', 'Offer the model');
    await waitFor(() =>
      expect(rowTexts(table)).toEqual([
        ADA_ROW,
        [
          'zed@example.comacct_zed',
          '$0.00 (connection withheld)Clear',
          '$3.1000',
          '9',
          'house-deep WithheldClearhouse-fast OfferedClear',
        ],
      ]),
    );
  });

  it('is added before the list has been read, and keeps its line when the list arrives', async () => {
    let answer!: (page: api.OperatorModelUserList) => void;
    vi.mocked(api.listOperatorModelUsers).mockReturnValueOnce(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    vi.mocked(api.setOperatorModelUserBudget).mockResolvedValue({
      ...ZED,
      daily_budget_microusd: 7_000_000,
    });
    render(<ProvidedModelsSettings />);
    const accounts = await openAccounts();
    setBudget(accounts, 'acct_zed', '7');
    const table = await within(accounts).findByRole('table', { name: 'Accounts' });
    expect(rowTexts(table)).toEqual([
      ['zed@example.comacct_zed', '$7.00Clear', '$3.1000', '9', 'house-deep WithheldClear'],
    ]);
    // The first page does not list him: his line stays, beside the ones it does list.
    answer({ users: [ADA], next_cursor: 'acct_ada' });
    await waitFor(() => expect(rowTexts(table)).toHaveLength(2));
    expect(rowTexts(table)).toEqual([
      ['zed@example.comacct_zed', '$7.00Clear', '$3.1000', '9', 'house-deep WithheldClear'],
      ADA_ROW,
    ]);
  });
});
