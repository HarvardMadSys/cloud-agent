// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api/adminOperatorModels';

import ProvidedModelsSettings from './ProvidedModels';
import {
  PRICED,
  PROVIDED_ID,
  priced,
  providedConnection,
  providedList,
  readyModel,
  refusal,
  refusedModel,
} from './providedModelsTestFixtures';

// The calls are mocked; the limits, the error helpers and the vocabulary are the real ones.
vi.mock('@/lib/api/adminOperatorModels', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/adminOperatorModels')>()),
  listOperatorModelConnections: vi.fn(),
  createOperatorModelConnection: vi.fn(),
  updateOperatorModelConnection: vi.fn(),
  refreshOperatorModels: vi.fn(),
  deleteOperatorModelConnection: vi.fn(),
  getOperatorModelUsage: vi.fn(),
}));

function section() {
  return screen.getByRole('region', { name: 'Models this deployment provides' });
}

async function card(name = 'House models') {
  return screen.findByRole('article', { name });
}

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

/** Fill the new-connection form: a name, a Base URL and a key are all it asks for. */
function fillNew() {
  type('Name', 'House models');
  type('Base URL', 'https://api.example.com/v1');
  type('API key', 'sk-deployment-key');
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
  it('shows each connection with its key’s models, their listed prices, and whether it is on', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([
        providedConnection({
          enabled: true,
          prices: {
            'house-fast': priced({ cache_read_price: 280_000 }),
          },
        }),
        providedConnection({ id: 'oconn_ffffffffffffffffffffffffffffffff', name: 'Spare' }),
      ]),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    expect(house).toHaveTextContent('https://api.example.com/v1');
    expect(within(house).getByText('Enabled')).toBeInTheDocument();
    // Each model's verification reads as on an owner's connection.
    expect(within(house).getAllByText('Works with Claude Code, Codex')).toHaveLength(2);
    // Prices are the provider's listing, in dollars per million tokens.
    expect(
      within(house).getByText(
        'Per million tokens: input $2.80, output $11.20 · cached input $0.28',
      ),
    ).toBeInTheDocument();
    expect(within(house).getByText('No listed price')).toBeInTheDocument();
    expect(house).toHaveTextContent(
      'The provider lists no price for house-deep: their use is recorded in tokens, with no cost.',
    );
    // The models are the key's: nothing here edits them, nor sets a budget or a default.
    expect(within(house).queryByRole('textbox')).toBeNull();
    expect(house).not.toHaveTextContent(/budget|Default for new users|in flight/i);
    const spare = await card('Spare');
    expect(within(spare).getByText('Disabled')).toBeInTheDocument();
    // The key is write-only: nothing on the page shows or hints at one.
    expect(section()).not.toHaveTextContent(/sk-/);
  });

  it('says a model no agent can use is offered to nobody', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([
        providedConnection({
          models: ['house-fast', 'house-embed'],
          model_status: [readyModel('house-fast'), refusedModel('house-embed', 'endpoint_missing')],
        }),
      ]),
    );
    render(<ProvidedModelsSettings />);
    expect(
      within(await card()).getByText('No agent can use this model, so it is offered to nobody.'),
    ).toBeInTheDocument();
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
      providedList([providedConnection(PRICED)], { feature_enabled: false }),
    );
    render(<ProvidedModelsSettings />);
    expect(
      await screen.findByText(
        /Model connections are switched off on this deployment\. Nothing here is offered to anyone/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a provided connection' })).toBeDisabled();
    const enable = within(await card()).getByRole('button', { name: 'Enable' });
    expect(enable).toBeDisabled();
    expect(enable).toHaveAccessibleDescription(
      'It cannot be enabled while model connections are switched off on this deployment.',
    );
  });

  it('reads the server’s refusal for the feature being off as that, not as lost admin access', async () => {
    vi.mocked(api.listOperatorModelConnections).mockResolvedValue(
      providedList([providedConnection(PRICED)]),
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

  it('asks only for a name, a Base URL and a key, and saves it disabled', async () => {
    const save = pending<api.OperatorModelConnection>();
    vi.mocked(api.createOperatorModelConnection).mockReturnValue(save.promise);
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    const form = screen.getByRole('form', { name: 'New provided connection' });
    expect(within(form).getByLabelText('API key')).toHaveAttribute('type', 'password');
    expect(within(form).getByRole('button', { name: 'Verify and save' })).toBeDisabled();
    // Nothing else: no model IDs, no default, no budget, no header, no concurrency.
    expect(within(form).queryByLabelText('Model IDs')).toBeNull();
    expect(within(form).queryByRole('checkbox')).toBeNull();
    expect(within(form).getAllByRole('textbox')).toHaveLength(2);
    // Said before anything is sent: the key's models are read, and it is saved disabled.
    expect(form).toHaveTextContent(/reads the models the provider lists for this key/);
    expect(form).toHaveTextContent(/The connection is saved disabled/);
    expect(form).toHaveTextContent(/may bill/);

    fillNew();
    fireEvent.click(within(form).getByRole('button', { name: 'Verify and save' }));

    // The save can take minutes: the form says so and cannot be sent twice or cancelled.
    expect(
      await within(form).findByText(/Reading the provider’s models for this key/),
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
    });

    save.resolve(providedConnection());
    const saved = await card();
    expect(within(saved).getByText('Disabled')).toBeInTheDocument();
    expect(within(saved).getByText('house-fast')).toBeInTheDocument();
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
    expect(api.updateOperatorModelConnection).not.toHaveBeenCalled();
    expect(section()).not.toHaveTextContent('sk-deployment-key');
  });

  it('refuses a form it can see is wrong, without calling the server', async () => {
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    type('Base URL', 'http://api.example.com/v1');
    type('API key', 'sk with spaces');
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Check the highlighted fields, then try again.',
    );
    expect(screen.getByLabelText('Base URL')).toHaveAccessibleDescription(
      /Enter an HTTPS Base URL/,
    );
    expect(screen.getByLabelText('API key')).toBeInvalid();
    expect(api.createOperatorModelConnection).not.toHaveBeenCalled();
  });

  it('shows each model’s result when no model the key lists could be verified', async () => {
    vi.mocked(api.createOperatorModelConnection).mockRejectedValue(
      refusal('model_connection_verification_failed', 'No model could be verified.', 422, {
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
      'Not saved: no model the provider lists for this key could be verified for any agent.',
    );
    const form = screen.getByRole('form', { name: 'New provided connection' });
    expect(within(form).getByText('house-fast')).toBeInTheDocument();
    expect(within(form).getAllByText('No agent could use this model.')).toHaveLength(2);
    // Every input is kept for another try.
    expect(screen.getByLabelText('Name')).toHaveValue('House models');
    expect(screen.getByLabelText('API key')).toHaveValue('sk-deployment-key');
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
    // Editing anything clears results that described the form as it was.
    type('Name', 'House models 2');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(within(form).queryByText('house-fast')).toBeNull();
  });

  it('says plainly when the key’s model list cannot be read, is empty or is too long', async () => {
    vi.mocked(api.createOperatorModelConnection)
      .mockRejectedValueOnce(refusal('operator_model_listing_failed', 'Could not read.', 422))
      .mockRejectedValueOnce(refusal('operator_model_listing_empty', 'None.', 422))
      .mockRejectedValueOnce(refusal('operator_model_listing_too_long', 'Too many.', 422));
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    submit();
    expect(
      await screen.findByText(/The provider’s model list could not be read with this key/),
    ).toBeInTheDocument();
    submit();
    expect(
      await screen.findByText('The provider lists no model for this key.'),
    ).toBeInTheDocument();
    submit();
    expect(
      await screen.findByText(/Limit the key at the provider to the models this deployment/),
    ).toBeInTheDocument();
    expect(api.createOperatorModelConnection).toHaveBeenCalledTimes(3);
  });

  it('marks the fields the server named, and says a taken name or a timeout plainly', async () => {
    vi.mocked(api.createOperatorModelConnection)
      .mockRejectedValueOnce(
        refusal('invalid_model_connection', 'Check the fields.', 422, {
          fields: ['base_url', 'api_key'],
        }),
      )
      .mockRejectedValueOnce(refusal('operator_model_connection_name_taken', 'Taken.', 409))
      .mockRejectedValueOnce(refusal('TIMEOUT_ERROR', 'HTTP 504', 504));
    render(<ProvidedModelsSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a provided connection' }));
    fillNew();
    const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }));

    submit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Check the highlighted fields');
    expect(screen.getByLabelText('Base URL')).toBeInvalid();
    expect(screen.getByLabelText('API key')).toBeInvalid();
    submit();
    expect(
      await screen.findByText('Another provided connection already has this name.'),
    ).toBeInTheDocument();
    submit();
    expect(
      await screen.findByText('Verification took too long. Nothing was saved; try again later.'),
    ).toBeInTheDocument();
    expect(api.createOperatorModelConnection).toHaveBeenCalledTimes(3);
  });
});

describe('editing a provided connection', () => {
  const saved = providedConnection(PRICED);
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

  it('reads the models again for a new key, and shows what the provider lists now', async () => {
    vi.mocked(api.updateOperatorModelConnection).mockResolvedValue(
      providedConnection({ models: ['house-new'] }),
    );
    const form = await openEditor();
    type('API key', 'sk-rotated');
    fireEvent.click(within(form).getByRole('button', { name: 'Verify and save' }));
    await waitFor(() =>
      expect(api.updateOperatorModelConnection).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID, {
        api_key: 'sk-rotated',
      }),
    );
    expect(await within(await card()).findByText('house-new')).toBeInTheDocument();
  });

  it('sends nothing for an editor closed unchanged, and holds the cards while it is open', async () => {
    const form = await openEditor();
    const house = await card();
    for (const name of ['Edit', 'Enable', 'Read models again', 'Delete']) {
      expect(within(house).getByRole('button', { name })).toBeDisabled();
    }
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('form')).not.toBeInTheDocument());
    expect(api.updateOperatorModelConnection).not.toHaveBeenCalled();
    expect(within(house).getByRole('button', { name: 'Edit' })).toBeEnabled();
  });
});

describe('reading the models again', () => {
  it('follows the key’s listing with the stored key, saying it works meanwhile', async () => {
    const change = pending<api.OperatorModelConnection>();
    vi.mocked(api.refreshOperatorModels).mockReturnValue(change.promise);
    render(<ProvidedModelsSettings />);
    const house = await card();
    fireEvent.click(within(house).getByRole('button', { name: 'Read models again' }));
    expect(
      await within(house).findByText('Reading the provider’s models and verifying new ones…'),
    ).toBeInTheDocument();
    expect(within(house).getByRole('button', { name: 'Read models again' })).toBeDisabled();
    expect(api.refreshOperatorModels).toHaveBeenCalledExactlyOnceWith(PROVIDED_ID);
    change.resolve(
      providedConnection({
        models: ['house-fast', 'house-new'],
        prices: { 'house-new': priced({ input_price: 150_000, output_price: 600_000 }) },
      }),
    );
    expect(await within(house).findByText('house-new')).toBeInTheDocument();
    expect(
      within(house).getByText('Per million tokens: input $0.15, output $0.60'),
    ).toBeInTheDocument();
    expect(within(house).queryByText('house-deep')).toBeNull();
  });

  it('keeps the connection as it was, and says why, when the listing cannot be read', async () => {
    vi.mocked(api.refreshOperatorModels).mockRejectedValue(
      refusal('operator_model_listing_empty', 'None.', 422),
    );
    render(<ProvidedModelsSettings />);
    const house = await card();
    fireEvent.click(within(house).getByRole('button', { name: 'Read models again' }));
    expect(await within(house).findByRole('alert')).toHaveTextContent(
      'The provider lists no model for this key.',
    );
    expect(within(house).getByText('house-deep')).toBeInTheDocument();
  });
});

describe('enabling, disabling and deleting', () => {
  it('is enabled at once: nothing has to be chosen first', async () => {
    const ready = providedConnection();
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

  it('asks before disabling, because it stops work for every account', async () => {
    const on = providedConnection({ enabled: true, ...PRICED });
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
