// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api/adminOperatorModels';

import ProvidedModelsSettings from './ProvidedModels';
import {
  DAILY_BUDGET,
  PROVIDED_ID,
  priced,
  providedConnection,
  providedList,
  refusal,
} from './providedModelsTestFixtures';

// Each model's terms: its prices per million tokens, who it is for, and its cap.

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

const UNIT =
  'Prices are in US dollars per million tokens. A cache price left blank charges those tokens at the input price.';
const CACHE_READ = 'Cached input price (optional)';
const CACHE_WRITE = 'Cache write price (optional)';
const CAP = 'Requests in flight across accounts (optional)';

async function card() {
  return screen.findByRole('article', { name: 'House models' });
}

/** One model's row in the model prices list. */
function row(house: HTMLElement, model: string) {
  const list = within(house).getByRole('region', { name: 'Model prices' });
  return within(list).getByRole('listitem', { name: model });
}

function type(within_: HTMLElement, label: string, value: string) {
  fireEvent.change(within(within_).getByLabelText(label), { target: { value } });
}

function save(within_: HTMLElement, model: string) {
  return within(within_).getByRole('button', { name: `Save the prices for ${model}` });
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList());
});
afterEach(cleanup);

describe('model prices', () => {
  it('shows what is saved for each model, and which still have no prices', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([
        providedConnection({
          models: ['house-fast', 'house-cached', 'house-named', 'house-new'],
          terms: {
            'house-fast': priced({ global_concurrency: 8 }),
            'house-cached': priced({
              input_price: 150_000,
              output_price: 600_000,
              cache_read_price: 75_000,
              cache_write_price: 187_500,
            }),
            'house-named': priced({ input_price: 0, output_price: 0, named_only: true }),
          },
        }),
      ]),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    expect(row(house, 'house-fast')).toHaveTextContent(
      'Per million tokens: input $2.80, output $11.20. At most 8 in flight across accounts.',
    );
    // A price keeps every decimal it has: a tenth of a cent is not rounded away.
    expect(row(house, 'house-cached')).toHaveTextContent(
      'Per million tokens: input $0.15, output $0.60. Cached input $0.075. Cache writes $0.1875.',
    );
    expect(row(house, 'house-named')).toHaveTextContent(
      'Per million tokens: input $0.00, output $0.00. Offered only to named accounts.',
    );
    expect(row(house, 'house-new')).toHaveTextContent('No prices yet: offered to nobody.');

    // A row starts from what is saved, in dollars.
    const cached = row(house, 'house-cached');
    expect(within(cached).getByLabelText('Input price')).toHaveValue('0.15');
    expect(within(cached).getByLabelText('Output price')).toHaveValue('0.60');
    expect(within(cached).getByLabelText(CACHE_READ)).toHaveValue('0.075');
    expect(within(cached).getByLabelText(CACHE_WRITE)).toHaveValue('0.1875');
    expect(within(row(house, 'house-fast')).getByLabelText(CAP)).toHaveValue('8');
    expect(within(row(house, 'house-named')).getByRole('checkbox')).toBeChecked();
    // Nothing changed yet: nothing to save.
    expect(save(cached, 'house-cached')).toBeDisabled();

    // A model with no prices has nothing to save, and nothing wrong with it, until one is typed.
    const unset = row(house, 'house-new');
    expect(within(unset).getByLabelText('Input price')).toHaveValue('');
    expect(within(unset).getByLabelText(CACHE_READ)).toHaveAttribute(
      'placeholder',
      'Same as input',
    );
    expect(within(unset).getByLabelText(CAP)).toHaveAttribute('placeholder', 'No cap');
    expect(within(unset).getByRole('checkbox', { name: 'Only named accounts' })).not.toBeChecked();
    expect(save(unset, 'house-new')).toBeDisabled();
    expect(within(unset).getByLabelText('Input price')).toBeValid();
    expect(unset).not.toHaveTextContent(/Enter an input price/);
  });

  it('says on every price that it is in dollars per million tokens', async () => {
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    for (const label of ['Input price', 'Output price', CACHE_READ, CACHE_WRITE]) {
      expect(within(fast).getByLabelText(label)).toHaveAccessibleDescription(UNIT);
    }
    // The cap is a count, not a price.
    expect(within(fast).getByLabelText(CAP)).not.toHaveAccessibleDescription();
  });

  it('saves an input and an output price in micro-dollars, leaving out what is optional', async () => {
    const saved = providedConnection({ terms: { 'house-fast': priced() } });
    vi.mocked(api.setOperatorModelTerms).mockResolvedValue(saved);
    render(<ProvidedModelsSettings />);
    const house = await card();
    const fast = row(house, 'house-fast');
    type(fast, 'Input price', '2.8');
    expect(save(fast, 'house-fast')).toBeDisabled();
    type(fast, 'Output price', '11.20');
    fireEvent.click(save(fast, 'house-fast'));
    await waitFor(() =>
      expect(api.setOperatorModelTerms).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        model: 'house-fast',
        input_price: 2_800_000,
        output_price: 11_200_000,
        cache_read_price: null,
        cache_write_price: null,
        named_only: false,
        global_concurrency: null,
      }),
    );
    expect(
      await within(house).findByText('Per million tokens: input $2.80, output $11.20.'),
    ).toBeVisible();
    // Saved and unchanged: nothing to save again, and the row reads what the server holds.
    expect(save(row(house, 'house-fast'), 'house-fast')).toBeDisabled();
    expect(within(row(house, 'house-fast')).getByLabelText('Input price')).toHaveValue('2.80');
  });

  it('saves cache prices, named accounts only and a deployment-wide cap with them', async () => {
    const terms = priced({
      cache_read_price: 280_000,
      cache_write_price: 3_500_000,
      named_only: true,
      global_concurrency: 8,
    });
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection({ terms: { 'house-fast': priced() } })]),
    );
    vi.mocked(api.setOperatorModelTerms).mockResolvedValue(
      providedConnection({ terms: { 'house-fast': terms } }),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    const fast = row(house, 'house-fast');
    type(fast, CACHE_READ, '0.28');
    type(fast, CACHE_WRITE, '3.5');
    type(fast, CAP, '8');
    fireEvent.click(within(fast).getByRole('checkbox', { name: 'Only named accounts' }));
    fireEvent.click(save(fast, 'house-fast'));
    await waitFor(() =>
      expect(api.setOperatorModelTerms).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        model: 'house-fast',
        ...terms,
      }),
    );
    expect(
      await within(house).findByText(
        'Per million tokens: input $2.80, output $11.20. Cached input $0.28. Cache writes $3.50. Offered only to named accounts. At most 8 in flight across accounts.',
      ),
    ).toBeVisible();
  });

  it('takes a cache price away again when its field is emptied', async () => {
    const before = priced({ cache_read_price: 280_000, global_concurrency: 8 });
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection({ terms: { 'house-fast': before } })]),
    );
    vi.mocked(api.setOperatorModelTerms).mockResolvedValue(
      providedConnection({ terms: { 'house-fast': priced() } }),
    );
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    type(fast, CACHE_READ, '');
    type(fast, CAP, ' ');
    fireEvent.click(save(fast, 'house-fast'));
    await waitFor(() =>
      expect(api.setOperatorModelTerms).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        model: 'house-fast',
        ...priced(),
      }),
    );
  });

  it('needs both prices, and says so once anything is typed', async () => {
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    type(fast, 'Input price', '2.80');
    expect(save(fast, 'house-fast')).toBeDisabled();
    expect(fast).toHaveTextContent(
      'Enter an input price and an output price, each from 0 to $1,000,000.00 with at most six decimal places.',
    );
    // The field that is wrong is the one marked, and says why.
    expect(within(fast).getByLabelText('Input price')).toBeValid();
    expect(within(fast).getByLabelText('Output price')).toBeInvalid();
    expect(within(fast).getByLabelText('Output price')).toHaveAccessibleDescription(
      /^Enter an input price and an output price/,
    );
    // A model may be free: zero is a price.
    type(fast, 'Output price', '0');
    expect(within(fast).getByLabelText('Output price')).toBeValid();
    expect(fast).not.toHaveTextContent(/Enter an input price/);
    expect(save(fast, 'house-fast')).toBeEnabled();
    expect(api.setOperatorModelTerms).not.toHaveBeenCalled();
  });

  it('will not send a price that is not one', async () => {
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    type(fast, 'Output price', '11.20');
    for (const value of ['-1', 'cheap', '2,8', '$2.80', '1.1234567', '1000000.000001']) {
      type(fast, 'Input price', value);
      expect(save(fast, 'house-fast'), value).toBeDisabled();
      expect(within(fast).getByLabelText('Input price'), value).toBeInvalid();
    }
    type(fast, 'Input price', '1000000');
    expect(save(fast, 'house-fast')).toBeEnabled();
    expect(api.setOperatorModelTerms).not.toHaveBeenCalled();
  });

  it('refuses a cache price of zero itself, which the server would', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection({ terms: { 'house-fast': priced() } })]),
    );
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    for (const [label, other] of [
      [CACHE_READ, CACHE_WRITE],
      [CACHE_WRITE, CACHE_READ],
    ]) {
      type(fast, label, '0');
      expect(save(fast, 'house-fast')).toBeDisabled();
      expect(within(fast).getByLabelText(label)).toBeInvalid();
      expect(within(fast).getByLabelText(label)).toHaveAccessibleDescription(
        /^A cache price is above 0 and at most \$1,000,000\.00, with at most six decimal places\. Leave it blank to charge those tokens at the input price\./,
      );
      expect(within(fast).getByLabelText(other)).toBeValid();
      type(fast, label, '0.000001');
      expect(within(fast).getByLabelText(label)).toBeValid();
      expect(save(fast, 'house-fast')).toBeEnabled();
      type(fast, label, '');
    }
    expect(api.setOperatorModelTerms).not.toHaveBeenCalled();
  });

  it('will not send a cap that is not a whole number of requests', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection({ terms: { 'house-fast': priced() } })]),
    );
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    for (const value of ['0', '1.5', 'many', '2000']) {
      type(fast, CAP, value);
      expect(save(fast, 'house-fast'), value).toBeDisabled();
      expect(within(fast).getByLabelText(CAP), value).toBeInvalid();
    }
    expect(within(fast).getByLabelText(CAP)).toHaveAccessibleDescription(
      'Requests in flight is a whole number from 1 to 1,024, or blank for no cap.',
    );
    type(fast, CAP, '1024');
    expect(save(fast, 'house-fast')).toBeEnabled();
    expect(api.setOperatorModelTerms).not.toHaveBeenCalled();
  });

  it('lets the connection be enabled once it has a budget and every model has prices', async () => {
    const budgeted = { user_daily_budget_microusd: DAILY_BUDGET };
    const free = priced({ input_price: 0, output_price: 0 });
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection(budgeted)]),
    );
    vi.mocked(api.setOperatorModelTerms)
      .mockResolvedValueOnce(providedConnection({ ...budgeted, terms: { 'house-fast': priced() } }))
      .mockResolvedValueOnce(
        providedConnection({ ...budgeted, terms: { 'house-fast': priced(), 'house-deep': free } }),
      );
    render(<ProvidedModelsSettings />);
    const house = await card();
    const enable = () => within(house).getByRole('button', { name: 'Enable' });
    expect(enable()).toHaveAccessibleDescription(
      'It cannot be enabled until every model has prices. Still without prices: house-fast, house-deep.',
    );

    type(row(house, 'house-fast'), 'Input price', '2.80');
    type(row(house, 'house-fast'), 'Output price', '11.20');
    fireEvent.click(save(house, 'house-fast'));
    await within(house).findByText('Per million tokens: input $2.80, output $11.20.');
    // The other model still blocks enabling.
    expect(enable()).toBeDisabled();
    expect(enable()).toHaveAccessibleDescription(/Still without prices: house-deep\./);

    type(row(house, 'house-deep'), 'Input price', '0');
    type(row(house, 'house-deep'), 'Output price', '0');
    fireEvent.click(save(house, 'house-deep'));
    await within(house).findByText('Per million tokens: input $0.00, output $0.00.');
    expect(vi.mocked(api.setOperatorModelTerms).mock.calls.map((call) => call[1])).toEqual([
      { model: 'house-fast', ...priced() },
      { model: 'house-deep', ...free },
    ]);
    // Every model now has prices: the gate is gone.
    expect(enable()).toBeEnabled();
    expect(within(house).queryByText(/cannot be enabled/)).not.toBeInTheDocument();
  });

  it('shows a pending state while saving, and what the server said when it refuses', async () => {
    let refuse!: (cause: unknown) => void;
    vi.mocked(api.setOperatorModelTerms).mockReturnValue(
      new Promise((_resolve, reject) => {
        refuse = reject;
      }),
    );
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    type(fast, 'Input price', '2.80');
    type(fast, 'Output price', '11.20');
    fireEvent.click(save(fast, 'house-fast'));
    expect(save(fast, 'house-fast')).toHaveTextContent('Working…');
    expect(save(fast, 'house-fast')).toHaveAttribute('aria-busy', 'true');
    expect(within(fast).getByLabelText('Input price')).toBeDisabled();
    refuse(refusal('operator_model_connection_not_found', 'Gone.', 404));
    expect(await within(fast).findByRole('alert')).toHaveTextContent(
      'This connection no longer exists.',
    );
    expect(save(fast, 'house-fast')).toHaveTextContent('Save');
    // What was typed is kept for another try.
    expect(within(fast).getByLabelText('Input price')).toHaveValue('2.80');
  });

  it('says when the model is no longer listed on the connection', async () => {
    // Taken off the list since this page read it: the server's own sentence is not shown.
    vi.mocked(api.setOperatorModelTerms).mockRejectedValue(
      refusal('operator_model_not_listed', 'That model is not listed on this connection.', 404),
    );
    render(<ProvidedModelsSettings />);
    const fast = row(await card(), 'house-fast');
    type(fast, 'Input price', '2.80');
    type(fast, 'Output price', '11.20');
    fireEvent.click(save(fast, 'house-fast'));
    expect(await within(fast).findByRole('alert')).toHaveTextContent(
      'That model is no longer listed on this connection. Reload the page to see the current list.',
    );
    expect(fast).not.toHaveTextContent('That model is not listed on this connection.');
    // Nothing was saved: the model still has no prices here.
    expect(fast).toHaveTextContent('No prices yet: offered to nobody.');
  });

  it('marks the fields a refused save named, until the row is edited', async () => {
    vi.mocked(api.setOperatorModelTerms).mockRejectedValue(
      refusal('invalid_model_connection', 'Input should be greater than 0', 422, {
        fields: ['cache_read_price'],
      }),
    );
    const onAccessDenied = vi.fn();
    render(<ProvidedModelsSettings onAccessDenied={onAccessDenied} />);
    const fast = row(await card(), 'house-fast');
    type(fast, 'Input price', '2.80');
    type(fast, 'Output price', '11.20');
    type(fast, CACHE_READ, '0.28');
    fireEvent.click(save(fast, 'house-fast'));
    expect(await within(fast).findByRole('alert')).toHaveTextContent(
      'Check the highlighted fields, then try again.',
    );
    expect(within(fast).getByLabelText(CACHE_READ)).toBeInvalid();
    expect(within(fast).getByLabelText('Input price')).toBeValid();
    expect(onAccessDenied).not.toHaveBeenCalled();
    type(fast, CACHE_READ, '0.3');
    expect(within(fast).getByLabelText(CACHE_READ)).toBeValid();
    expect(within(fast).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('hands an ended admin session to the page', async () => {
    const denied = refusal('forbidden', 'Admin access required.', 403);
    vi.mocked(api.setOperatorModelTerms).mockRejectedValue(denied);
    const onAccessDenied = vi.fn();
    render(<ProvidedModelsSettings onAccessDenied={onAccessDenied} />);
    const fast = row(await card(), 'house-fast');
    type(fast, 'Input price', '1');
    type(fast, 'Output price', '2');
    fireEvent.click(save(fast, 'house-fast'));
    await waitFor(() => expect(onAccessDenied).toHaveBeenCalledWith(denied));
  });
});
