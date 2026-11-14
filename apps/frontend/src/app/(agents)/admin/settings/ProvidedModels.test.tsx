// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { chooseOption, optionNames } from '@/components/ui/__tests__/select';
import * as api from '@/lib/api/adminOperatorModels';

import ProvidedModelsSettings from './ProvidedModels';
import {
  COMPLETE,
  PROVIDED_ID,
  priced,
  providedConnection,
  providedList,
  refusal,
  refusedModel,
} from './providedModelsTestFixtures';

// The calls are mocked; the limits, the error helpers and the vocabulary are the real ones.
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

const BUDGET = 'Daily budget per account (USD)';

function section() {
  return screen.getByRole('region', { name: 'Models this deployment provides' });
}

async function card(name = 'House models') {
  return screen.findByRole('article', { name });
}

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

/** Fill the new-connection form; the last model ID stays typed and still counts. */
function fillNew(models: string[] = ['house-fast', 'house-deep']) {
  type('Name', 'House models');
  type('Base URL', 'https://api.example.com/v1');
  type('API key', 'sk-deployment-key');
  const input = screen.getByLabelText('Model IDs');
  models.forEach((model, index) => {
    fireEvent.change(input, { target: { value: model } });
    if (index < models.length - 1) fireEvent.keyDown(input, { key: 'Enter' });
  });
}

/** A request the test settles by hand, to look at the page while it is pending. */
function pending<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList());
});
afterEach(cleanup);

describe('the list of provided connections', () => {
  it('shows each connection with what it is and whether it is on', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([
        providedConnection({
          enabled: true,
          is_default: true,
          default_model: 'house-fast',
          end_user_header: 'X-End-User',
          user_concurrency: 6,
          ...COMPLETE,
        }),
        providedConnection({ id: 'oconn_ffffffffffffffffffffffffffffffff', name: 'Spare' }),
      ]),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    expect(house).toHaveTextContent('https://api.example.com/v1');
    expect(
      within(house)
        .getAllByRole('listitem')
        .slice(0, 6)
        .map((item) => item.textContent),
    ).toEqual([
      'Enabled',
      'Default for new users',
      'Default model: house-fast',
      'Daily budget per account: $5.00',
      'Up to 6 requests in flight per account',
      'End-user header: X-End-User',
    ]);
    // Each model's verification reads as on an owner's connection.
    expect(within(house).getAllByText('Works with Claude Code, Codex')).toHaveLength(2);
    const spare = await card('Spare');
    expect(within(spare).getByText('Disabled')).toBeInTheDocument();
    // Nobody has chosen what an account may spend on it yet.
    expect(within(spare).getByText('No daily budget chosen')).toBeInTheDocument();
    // The key is write-only: nothing on the page shows or hints at one.
    expect(section()).not.toHaveTextContent(/sk-/);
  });

  it('says so when there are none yet', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([]));
    render(<ProvidedModelsSettings />);
    expect(await screen.findByText('No provided connections yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a provided connection' })).toBeEnabled();
  });

  it('warns that no server can run provided models yet', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList(undefined, { relay_hosts: 0 }),
    );
    render(<ProvidedModelsSettings />);
    await card();
    expect(within(section()).getByText(/No server can run provided models yet/)).toHaveAttribute(
      'role',
      'alert',
    );
  });

  it('has no such warning while a server reaches the relay', async () => {
    render(<ProvidedModelsSettings />);
    await card();
    expect(screen.queryByText(/No server can run provided models yet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/switched off on this deployment/)).not.toBeInTheDocument();
  });

  it('says when model connections are switched off, and offers nothing the server would refuse', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection(COMPLETE)], { feature_enabled: false }),
    );
    render(<ProvidedModelsSettings />);
    expect(
      await screen.findByText(
        /Model connections are switched off on this deployment\. Nothing here is offered to anyone/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a provided connection' })).toBeDisabled();
    // It has its budget and every model its prices, and it still cannot be enabled.
    const enable = within(await card()).getByRole('button', { name: 'Enable' });
    expect(enable).toBeDisabled();
    expect(enable).toHaveAccessibleDescription(
      'It cannot be enabled while model connections are switched off on this deployment.',
    );
  });

  it('reads the server’s refusal for the feature being off as that, not as lost admin access', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection(COMPLETE)]),
    );
    // Switched off after this page was read: the server answers 403 for an enable.
    vi.mocked(api.updateOperatorModelConnection).mockRejectedValue(
      refusal('model_connections_disabled', 'Model connections are switched off.', 403),
    );
    const onAccessDenied = vi.fn();
    render(<ProvidedModelsSettings onAccessDenied={onAccessDenied} />);
    const house = await card();
    fireEvent.click(within(house).getByRole('button', { name: 'Enable' }));
    expect(await within(house).findByRole('alert')).toHaveTextContent(
      'Model connections are switched off on this deployment. Switch them on before adding, verifying or enabling a provided connection.',
    );
    expect(onAccessDenied).not.toHaveBeenCalled();
  });

  it('warns since when the provider has refused the stored key', async () => {
    const since = '2026-11-11T08:30:00Z';
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection({ key_refused_at: since })]),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    expect(
      within(house).getByText(
        `The provider has refused the stored API key since ${new Date(since).toLocaleString()}. Requests on this connection fail until a working key is saved.`,
      ),
    ).toHaveAttribute('role', 'alert');
  });

  it('offers a retry when the list cannot be read', async () => {
    vi.mocked(api.listOperatorModelConnections)
      .mockRejectedValueOnce(refusal('SERVER_ERROR', 'HTTP 500', 500))
      .mockResolvedValue(providedList());
    render(<ProvidedModelsSettings />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Server error. Please try again in a moment');
    expect(screen.queryByRole('button', { name: 'Add a provided connection' })).toBeNull();
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await card()).toBeInTheDocument();
    expect(screen.queryByText(/Server error/)).not.toBeInTheDocument();
  });

  it('hands an ended admin session to the page', async () => {
    const denied = refusal('forbidden', 'Admin access required.', 403);
    vi.mocked(api.listOperatorModelConnections).mockRejectedValue(denied);
    const onAccessDenied = vi.fn();
    render(<ProvidedModelsSettings onAccessDenied={onAccessDenied} />);
    await waitFor(() => expect(onAccessDenied).toHaveBeenCalledWith(denied));
  });
});

describe('adding a provided connection', () => {
  beforeEach(() => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([]));
  });

  it('verifies and saves it disabled, with a visible pending state meanwhile', async () => {
    const save = pending<api.OperatorModelConnection>();
    vi.mocked(api.createOperatorModelConnection).mockReturnValue(save.promise);
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    const form = screen.getByRole('form', { name: 'New provided connection' });
    expect(within(form).getByLabelText('API key')).toHaveAttribute('type', 'password');
    expect(within(form).getByRole('button', { name: 'Verify and save' })).toBeDisabled();
    // Said before anything is sent: it is saved disabled, and the provider may bill the check.
    expect(form).toHaveTextContent(/The connection is saved disabled/);
    expect(form).toHaveTextContent(/may bill/);
    // There is no provider list to load for a provided connection.
    expect(within(form).queryByRole('button', { name: /Load models/ })).toBeNull();
    // The daily budget may wait: the hint says what it is, and that enabling needs one.
    expect(within(form).getByLabelText(BUDGET)).toHaveValue('');
    expect(within(form).getByLabelText(BUDGET)).toHaveAccessibleDescription(
      'What each account may spend per UTC day on this connection’s models together. 0 offers the connection only to accounts given a budget of their own. The connection cannot be enabled until a budget is chosen.',
    );

    fillNew();
    expect(optionNames(within(form).getByLabelText('Default model'))).toEqual([
      'None',
      'house-fast',
      'house-deep',
    ]);
    chooseOption(within(form).getByLabelText('Default model'), 'house-deep');
    type(/^End-user header/, 'X-End-User');
    type('Requests in flight per account', '6');
    fireEvent.click(within(form).getByRole('button', { name: 'Verify and save' }));

    // The save can take minutes: the form says so and cannot be sent twice or cancelled.
    expect(
      await within(form).findByText(/Verifying every model with the provider…/),
    ).toBeInTheDocument();
    expect(form).toHaveTextContent('this can take up to four minutes');
    const busy = within(form).getByRole('button', { name: 'Verifying…' });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute('aria-busy', 'true');
    expect(within(form).getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(within(form).getByLabelText('Name')).toBeDisabled();
    expect(api.createOperatorModelConnection).toHaveBeenCalledExactlyOnceWith({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-deployment-key',
      models: ['house-fast', 'house-deep'],
      default_model: 'house-deep',
      end_user_header: 'X-End-User',
      user_concurrency: 6,
    });

    // Left blank, the daily budget is not sent at all.
    expect(vi.mocked(api.createOperatorModelConnection).mock.calls[0][0]).not.toHaveProperty(
      'user_daily_budget_microusd',
    );

    save.resolve(providedConnection({ default_model: 'house-deep', user_concurrency: 6 }));
    const saved = await card();
    expect(within(saved).getByText('Disabled')).toBeInTheDocument();
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
    expect(api.updateOperatorModelConnection).not.toHaveBeenCalled();
    expect(section()).not.toHaveTextContent('sk-deployment-key');
  });

  it('sends a daily budget typed in dollars as micro-dollars', async () => {
    vi.mocked(api.createOperatorModelConnection).mockResolvedValue(
      providedConnection({ user_daily_budget_microusd: 2_800_000 }),
    );
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    type(BUDGET, ' 2.8 ');
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    expect(await within(await card()).findByText('Daily budget per account: $2.80')).toBeVisible();
    expect(api.createOperatorModelConnection).toHaveBeenCalledExactlyOnceWith({
      name: 'House models',
      base_url: 'https://api.example.com/v1',
      api_key: 'sk-deployment-key',
      models: ['house-fast', 'house-deep'],
      default_model: null,
      end_user_header: null,
      user_concurrency: 4,
      user_daily_budget_microusd: 2_800_000,
    });
  });

  it('will not send a daily budget that is not an amount of dollars', async () => {
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    for (const amount of ['-5', 'plenty', '1.1234567', '1000000.01']) {
      type(BUDGET, amount);
      fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));
      expect(screen.getByLabelText(BUDGET)).toBeInvalid();
      expect(screen.getByLabelText(BUDGET)).toHaveAccessibleDescription(
        /^Enter an amount in US dollars from 0 to \$1,000,000\.00, with at most six decimal places\./,
      );
    }
    expect(api.createOperatorModelConnection).not.toHaveBeenCalled();
    // Zero is an amount: it offers the connection only to accounts given their own budget.
    vi.mocked(api.createOperatorModelConnection).mockResolvedValue(
      providedConnection({ user_daily_budget_microusd: 0 }),
    );
    type(BUDGET, '0');
    expect(screen.getByLabelText(BUDGET)).toBeValid();
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));
    expect(
      await within(await card()).findByText(
        'Daily budget per account: $0.00 (only accounts given their own)',
      ),
    ).toBeVisible();
    expect(vi.mocked(api.createOperatorModelConnection).mock.calls[0][0]).toMatchObject({
      user_daily_budget_microusd: 0,
    });
  });

  it('makes it the default for new users with an edit, once it exists', async () => {
    vi.mocked(api.createOperatorModelConnection).mockResolvedValue(providedConnection());
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue(
      providedConnection({ is_default: true }),
    );
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    fireEvent.click(screen.getByRole('checkbox', { name: /Default for new users/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    expect(await within(await card()).findByText('Default for new users')).toBeInTheDocument();
    expect(vi.mocked(api.createOperatorModelConnection).mock.calls[0][0]).not.toHaveProperty(
      'is_default',
    );
    expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
      is_default: true,
    });
  });

  it('keeps a saved connection, and says so, when making it the default fails', async () => {
    vi.mocked(api.createOperatorModelConnection).mockResolvedValue(providedConnection());
    vi.mocked(api.updateOperatorModelConnection).mockRejectedValue(
      refusal('operator_model_connection_not_found', 'Gone.', 404),
    );
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    fireEvent.click(screen.getByRole('checkbox', { name: /Default for new users/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    expect(
      await screen.findByText(
        /The connection was saved, but it could not be made the default for new users: This connection no longer exists/,
      ),
    ).toHaveAttribute('role', 'alert');
    expect(await card()).toBeInTheDocument();
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });

  it('refuses a form it can see is wrong, without calling the server', async () => {
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew(['house-fast']);
    type('Base URL', 'http://api.example.com/v1');
    type(/^End-user header/, 'X End: User');
    type('Requests in flight per account', '0');
    type(BUDGET, 'five');
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Check the highlighted fields, then try again.',
    );
    expect(screen.getByLabelText('Base URL')).toHaveAccessibleDescription(
      /Enter an HTTPS Base URL/,
    );
    expect(screen.getByLabelText(/^End-user header/)).toBeInvalid();
    expect(screen.getByLabelText('Requests in flight per account')).toHaveAccessibleDescription(
      /Enter a whole number from 1 to 64/,
    );
    expect(screen.getByLabelText(BUDGET)).toHaveAccessibleDescription(
      /Enter an amount in US dollars/,
    );
    expect(api.createOperatorModelConnection).not.toHaveBeenCalled();
  });

  it('shows each model’s result when the provider refuses one, and saves nothing', async () => {
    vi.mocked(api.createOperatorModelConnection).mockRejectedValue(
      refusal('model_connection_verification_failed', 'A model could not be verified.', 422, {
        models: [
          refusedModel('house-fast', 'auth'),
          refusedModel('house-deep', 'endpoint_missing'),
        ],
      }),
    );
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Not saved: a model could not be verified for any agent.',
    );
    const chips = screen.getByRole('list', { name: 'Models to save' });
    expect(chips).toHaveTextContent('Not usable: The provider refused the API key (401/403).');
    expect(chips).toHaveTextContent(/Not usable: The provider has no such endpoint or model/);
    // Every input is kept for another try, the typed model ID included.
    expect(screen.getByLabelText('Name')).toHaveValue('House models');
    expect(screen.getByLabelText('API key')).toHaveValue('sk-deployment-key');
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
    // Editing anything clears results that described the form as it was.
    type('Name', 'House models 2');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(chips).not.toHaveTextContent('Not usable');
  });

  it('marks the fields the server named, and says a taken name or a timeout plainly', async () => {
    vi.mocked(api.createOperatorModelConnection)
      .mockRejectedValueOnce(
        refusal('invalid_model_connection', 'Check the fields.', 422, {
          fields: ['base_url', 'user_daily_budget_microusd'],
        }),
      )
      .mockRejectedValueOnce(refusal('operator_model_connection_name_taken', 'Taken.', 409))
      .mockRejectedValueOnce(refusal('TIMEOUT_ERROR', 'HTTP 504', 504))
      .mockRejectedValueOnce(refusal('model_connection_check_limit', 'Slow down.', 429));
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    submit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Check the highlighted fields');
    expect(screen.getByLabelText('Base URL')).toBeInvalid();
    expect(screen.getByLabelText(BUDGET)).toBeInvalid();
    submit();
    expect(
      await screen.findByText('Another provided connection already has this name.'),
    ).toBeInTheDocument();
    submit();
    expect(
      await screen.findByText('Verification took too long. Nothing was saved; try again later.'),
    ).toBeInTheDocument();
    submit();
    expect(await screen.findByText(/Too many verifications in the last hour/)).toBeInTheDocument();
    expect(api.createOperatorModelConnection).toHaveBeenCalledTimes(4);
  });
});

describe('editing a provided connection', () => {
  const saved = providedConnection({
    default_model: 'house-fast',
    end_user_header: 'X-End-User',
    ...COMPLETE,
  });
  beforeEach(() => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([saved]));
  });

  async function openEditor() {
    render(<ProvidedModelsSettings />);
    fireEvent.click(within(await card()).getByRole('button', { name: 'Edit' }));
    return screen.getByRole('form', { name: 'Edit provided connection' });
  }

  it('starts from the saved values, never the key, and sends only what changed', async () => {
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue({ ...saved, name: 'Renamed' });
    const form = await openEditor();
    expect(within(form).getByLabelText('Name')).toHaveValue('House models');
    expect(within(form).getByLabelText('API key')).toHaveValue('');
    expect(within(form).getByLabelText('API key')).toHaveAttribute(
      'placeholder',
      'Leave blank to keep the saved key',
    );
    expect(within(form).getByLabelText(BUDGET)).toHaveValue('5.00');
    // A rename calls no provider: it saves at once.
    type('Name', 'Renamed');
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        name: 'Renamed',
      }),
    );
    expect(await card('Renamed')).toBeInTheDocument();
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });

  it('verifies again for a new key, and clears the default model and header with empty strings', async () => {
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue({
      ...saved,
      default_model: null,
      end_user_header: null,
      is_default: true,
    });
    const form = await openEditor();
    type('API key', 'sk-rotated');
    chooseOption(within(form).getByLabelText('Default model'), 'None');
    type(/^End-user header/, '');
    fireEvent.click(within(form).getByRole('checkbox', { name: /Default for new users/ }));
    fireEvent.click(within(form).getByRole('button', { name: 'Verify and save' }));
    await waitFor(() =>
      expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        api_key: 'sk-rotated',
        default_model: '',
        is_default: true,
        end_user_header: '',
      }),
    );
    expect(await within(await card()).findByText('Default for new users')).toBeInTheDocument();
  });

  it('changes the daily budget without calling the provider, and sends it in micro-dollars', async () => {
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue({
      ...saved,
      user_daily_budget_microusd: 12_500_000,
    });
    const form = await openEditor();
    type(BUDGET, '12.50');
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        user_daily_budget_microusd: 12_500_000,
      }),
    );
    expect(await within(await card()).findByText('Daily budget per account: $12.50')).toBeVisible();
  });

  it('will not take away a daily budget that was chosen', async () => {
    const form = await openEditor();
    type(BUDGET, '');
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'Check the highlighted fields, then try again.',
    );
    expect(within(form).getByLabelText(BUDGET)).toBeInvalid();
    expect(api.updateOperatorModelConnection).not.toHaveBeenCalled();
  });

  it('chooses the daily budget of a connection that has none yet, or leaves it for later', async () => {
    const none = providedConnection();
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([none]));
    vi.mocked(api.updateOperatorModelConnection)
      .mockResolvedValueOnce({ ...none, name: 'Renamed' })
      .mockResolvedValueOnce({ ...none, name: 'Renamed', user_daily_budget_microusd: 0 });
    const form = await openEditor();
    expect(within(form).getByLabelText(BUDGET)).toHaveValue('');
    // Still none: a rename goes through, and the budget is not sent.
    type('Name', 'Renamed');
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    const renamed = await card('Renamed');
    expect(api.updateOperatorModelConnection).toHaveBeenLastCalledWith(PROVIDED_ID, {
      name: 'Renamed',
    });

    fireEvent.click(within(renamed).getByRole('button', { name: 'Edit' }));
    type(BUDGET, '0');
    const again = screen.getByRole('form', { name: 'Edit provided connection' });
    fireEvent.click(within(again).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.updateOperatorModelConnection).toHaveBeenLastCalledWith(PROVIDED_ID, {
        user_daily_budget_microusd: 0,
      }),
    );
  });

  it('drops a default model that was removed from the list', async () => {
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue({
      ...saved,
      models: ['house-deep'],
      default_model: null,
    });
    const form = await openEditor();
    fireEvent.click(within(form).getByRole('button', { name: 'Remove house-fast' }));
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        models: ['house-deep'],
        default_model: '',
      }),
    );
  });

  it('sends nothing for an editor closed unchanged, and holds the cards while it is open', async () => {
    const form = await openEditor();
    const house = await card();
    for (const name of ['Edit', 'Enable', 'Delete']) {
      expect(within(house).getByRole('button', { name })).toBeDisabled();
    }
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('form')).not.toBeInTheDocument());
    expect(api.updateOperatorModelConnection).not.toHaveBeenCalled();
    expect(within(house).getByRole('button', { name: 'Edit' })).toBeEnabled();
  });

  it('moves the default mark when another connection becomes the default', async () => {
    const other = providedConnection({
      id: 'oconn_ffffffffffffffffffffffffffffffff',
      name: 'Spare',
      is_default: true,
    });
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([saved, other]));
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue({ ...saved, is_default: true });
    const form = await openEditor();
    fireEvent.click(within(form).getByRole('checkbox', { name: /Default for new users/ }));
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    expect(await within(await card()).findByText('Default for new users')).toBeInTheDocument();
    expect(within(await card('Spare')).queryByText('Default for new users')).toBeNull();
  });
});

describe('enabling, disabling and deleting', () => {
  /** The Enable button of a card that lacks something, which must not be offered. */
  async function blockedEnable(connection: api.OperatorModelConnection) {
    cleanup();
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([connection]));
    render(<ProvidedModelsSettings />);
    const enable = within(await card()).getByRole('button', { name: 'Enable' });
    expect(enable).toBeDisabled();
    fireEvent.click(enable);
    expect(api.updateOperatorModelConnection).not.toHaveBeenCalled();
    return enable;
  }

  it('cannot be enabled until it has a daily budget and every model has prices, and says what is missing', async () => {
    const onePriced = { 'house-fast': priced() };
    // A model without prices: named.
    expect(
      await blockedEnable(providedConnection({ ...COMPLETE, terms: onePriced })),
    ).toHaveAccessibleDescription(
      'It cannot be enabled until every model has prices. Still without prices: house-deep.',
    );
    // No daily budget: said, with where to choose one.
    expect(
      await blockedEnable(providedConnection({ ...COMPLETE, user_daily_budget_microusd: null })),
    ).toHaveAccessibleDescription(
      'It cannot be enabled until a daily budget per account is chosen. Edit the connection to choose one.',
    );
    // Neither, as a connection is when it has just been added.
    expect(await blockedEnable(providedConnection())).toHaveAccessibleDescription(
      'It cannot be enabled until a daily budget per account is chosen and every model has prices. Edit the connection to choose the budget. Still without prices: house-fast, house-deep.',
    );
    // A budget of zero and a price of zero are choices: nothing is missing.
    const free = priced({ input_price: 0, output_price: 0 });
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([
        providedConnection({
          user_daily_budget_microusd: 0,
          terms: { 'house-fast': free, 'house-deep': free },
        }),
      ]),
    );
    cleanup();
    render(<ProvidedModelsSettings />);
    const house = await card();
    expect(within(house).getByRole('button', { name: 'Enable' })).toBeEnabled();
    expect(within(house).queryByText(/cannot be enabled/)).not.toBeInTheDocument();
  });

  it('is enabled at once when it has a budget and every model has prices', async () => {
    const ready = providedConnection(COMPLETE);
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([ready]));
    const change = pending<api.OperatorModelConnection>();
    vi.mocked(api.updateOperatorModelConnection).mockReturnValue(change.promise);
    render(<ProvidedModelsSettings />);
    const house = await card();
    expect(within(house).queryByText(/cannot be enabled/)).not.toBeInTheDocument();
    fireEvent.click(within(house).getByRole('button', { name: 'Enable' }));
    expect(await within(house).findByText('Working…')).toBeInTheDocument();
    expect(within(house).getByRole('button', { name: 'Enable' })).toBeDisabled();
    expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
      enabled: true,
    });
    change.resolve({ ...ready, enabled: true });
    expect(await within(house).findByText('Enabled')).toBeInTheDocument();
    expect(within(house).getByRole('button', { name: 'Disable' })).toBeEnabled();
    expect(within(house).queryByText('Working…')).not.toBeInTheDocument();
  });

  it('names what the server said is missing when it still refuses to enable', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection(COMPLETE)]),
    );
    // A model was added since this page was read: the server names what is missing, and
    // the card says it in its own words, never the server's.
    const refused = (details?: Record<string, unknown>) =>
      refusal(
        'operator_model_terms_missing',
        'Before enabling, set prices for house-new.',
        409,
        details,
      );
    vi.mocked(api.updateOperatorModelConnection)
      .mockRejectedValueOnce(refused({ budget: false, models: ['house-new'] }))
      .mockRejectedValueOnce(refused({ budget: true, models: [] }))
      .mockRejectedValueOnce(refused({ budget: true, models: ['house-new', 'house-newer'] }))
      // A refusal that names it only in its sentence: the page's general one is said.
      .mockRejectedValueOnce(refused());
    render(<ProvidedModelsSettings />);
    const house = await card();
    const refusedWith = async (sentence: string) => {
      fireEvent.click(within(house).getByRole('button', { name: 'Enable' }));
      expect(await within(house).findByText(sentence)).toHaveAttribute('role', 'alert');
      expect(house).not.toHaveTextContent('Before enabling');
    };
    await refusedWith(
      'It cannot be enabled until every model has prices. Still without prices: house-new.',
    );
    await refusedWith(
      'It cannot be enabled until a daily budget per account is chosen. Edit the connection to choose one.',
    );
    await refusedWith(
      'It cannot be enabled until a daily budget per account is chosen and every model has prices. Edit the connection to choose the budget. Still without prices: house-new, house-newer.',
    );
    await refusedWith(
      'Not enabled: the connection needs a daily budget per account and prices for every model first.',
    );
    expect(api.updateOperatorModelConnection).toHaveBeenCalledTimes(4);
    expect(within(house).getByText('Disabled')).toBeInTheDocument();
  });

  it('warns that an enabled connection with no daily budget offers nothing', async () => {
    // What a connection enabled before budgets existed is after the upgrade.
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection({ enabled: true })]),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    const warning = within(house).getByRole('alert');
    expect(warning).toHaveTextContent(
      'This connection is enabled but offers nothing to anyone: no daily budget per account has been chosen. Edit the connection to choose one.',
    );
    expect(warning).toHaveTextContent(
      'This connection is enabled, but a model without prices is offered to nobody. Still without prices: house-fast, house-deep.',
    );
    // It is on, so there is nothing to enable: it can be edited, or disabled.
    expect(within(house).getByRole('button', { name: 'Disable' })).toBeEnabled();
    expect(within(house).queryByText(/cannot be enabled/)).not.toBeInTheDocument();
  });

  it('warns which models an enabled connection offers to nobody for want of prices', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([
        providedConnection({ enabled: true, ...COMPLETE, terms: { 'house-fast': priced() } }),
      ]),
    );
    vi.mocked(api.setOperatorModelTerms).mockResolvedValue(
      providedConnection({ enabled: true, ...COMPLETE }),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    expect(within(house).getByRole('alert')).toHaveTextContent(
      'This connection is enabled, but a model without prices is offered to nobody. Still without prices: house-deep.',
    );
    expect(within(house).getByRole('alert')).not.toHaveTextContent(/offers nothing to anyone/);

    // Once the model has prices the warning goes.
    const row = within(house).getByRole('listitem', { name: 'house-deep' });
    fireEvent.change(within(row).getByLabelText('Input price'), { target: { value: '2.80' } });
    fireEvent.change(within(row).getByLabelText('Output price'), { target: { value: '11.20' } });
    fireEvent.click(within(row).getByRole('button', { name: 'Save the prices for house-deep' }));
    await waitFor(() => expect(within(house).queryByRole('alert')).not.toBeInTheDocument());
  });

  it('has no such warning on an enabled connection that is complete, or on a disabled one', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([
        providedConnection({ enabled: true, ...COMPLETE }),
        providedConnection({ id: 'oconn_ffffffffffffffffffffffffffffffff', name: 'Spare' }),
      ]),
    );
    render(<ProvidedModelsSettings />);
    expect(within(await card()).queryByRole('alert')).not.toBeInTheDocument();
    // Disabled and incomplete is the ordinary state of a new connection: it says so by Enable.
    expect(within(await card('Spare')).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('asks before disabling, because it stops work for every account', async () => {
    const on = providedConnection({ enabled: true, ...COMPLETE });
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(providedList([on]));
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue({ ...on, enabled: false });
    render(<ProvidedModelsSettings />);
    const house = await card();
    fireEvent.click(within(house).getByRole('button', { name: 'Disable' }));
    const confirm = within(house).getByRole('group', { name: 'Confirm' });
    expect(confirm).toHaveTextContent(
      'Disable House models? Agent processes running on it stop for every account',
    );
    expect(api.updateOperatorModelConnection).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));
    expect(within(house).queryByRole('group', { name: 'Confirm' })).not.toBeInTheDocument();

    fireEvent.click(within(house).getByRole('button', { name: 'Disable' }));
    fireEvent.click(
      within(within(house).getByRole('group', { name: 'Confirm' })).getByRole('button', {
        name: 'Confirm',
      }),
    );
    expect(await within(house).findByText('Disabled')).toBeInTheDocument();
    expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
      enabled: false,
    });
    expect(within(house).queryByRole('group', { name: 'Confirm' })).not.toBeInTheDocument();
  });

  it('deletes only after confirmation', async () => {
    vi.mocked(api.deleteOperatorModelConnection).mockResolvedValue();
    render(<ProvidedModelsSettings />);
    const house = await card();
    fireEvent.click(within(house).getByRole('button', { name: 'Delete' }));
    const confirm = within(house).getByRole('group', { name: 'Confirm' });
    expect(confirm).toHaveTextContent('Delete House models?');
    expect(confirm).toHaveTextContent('This does not revoke the key at the provider.');
    expect(api.deleteOperatorModelConnection).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(screen.queryByRole('article')).not.toBeInTheDocument());
    expect(api.deleteOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID);
    expect(screen.getByText('No provided connections yet.')).toBeInTheDocument();
  });

  it('keeps the connection, and says why, when the delete is refused', async () => {
    vi.mocked(api.deleteOperatorModelConnection).mockRejectedValue(
      refusal('operator_model_connection_busy', 'Turns are still running on it.', 409),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    fireEvent.click(within(house).getByRole('button', { name: 'Delete' }));
    fireEvent.click(
      within(within(house).getByRole('group', { name: 'Confirm' })).getByRole('button', {
        name: 'Confirm',
      }),
    );
    expect(await within(house).findByRole('alert')).toHaveTextContent(
      'Turns are still running on it.',
    );
    expect(screen.getByRole('article', { name: 'House models' })).toBeInTheDocument();
  });
});
