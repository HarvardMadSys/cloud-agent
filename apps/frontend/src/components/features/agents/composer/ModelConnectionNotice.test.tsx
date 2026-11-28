// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AgentConfigApi } from '@/lib/api/agents';
import { providedSource } from '@/test/connectionConfig';

import { leaveFor } from '../githubAccess';
import { takeReturnPath } from '../githubReturn';
import { ModelConnectionNotice } from './ModelConnectionNotice';

const signIn = vi.hoisted(() => ({ start: vi.fn() }));
const startModelSignIn = signIn.start;

vi.mock('@/lib/api/modelConnections', () => ({ startModelSignIn: signIn.start }));
vi.mock('../githubAccess', () => ({ leaveFor: vi.fn() }));

beforeEach(() => {
  sessionStorage.clear();
  startModelSignIn.mockReset();
  (leaveFor as unknown as ReturnType<typeof vi.fn>).mockReset();
});

function config(overrides: Partial<AgentConfigApi> = {}): AgentConfigApi {
  return {
    repos: [],
    runtimes: ['codex'],
    github_connected: true,
    github_install_url: null,
    model_connections_enabled: true,
    model_connections: [],
    ...overrides,
  };
}

afterEach(cleanup);

describe('ModelConnectionNotice', () => {
  it('asks an owner with nothing to run on to set up a connection', () => {
    render(<ModelConnectionNotice config={config({ model_connection_required: true })} />);
    expect(screen.getByRole('status')).toHaveTextContent('Connect your model to start a task');
    expect(screen.getByRole('link', { name: 'Set up a model connection' })).toHaveAttribute(
      'href',
      '/settings/models#settings-connections',
    );
  });

  it('says nothing to an owner the deployment provides a model to', () => {
    const provided = [providedSource(['house-fast'])];
    const { container, rerender } = render(
      <ModelConnectionNotice
        config={config({ model_connection_required: false, provided_sources: provided })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    // A provided model that can run is enough, whatever else an answer says.
    rerender(
      <ModelConnectionNotice
        config={config({ model_connection_required: true, provided_sources: provided })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('names the provided connection, never a provider, when it offers this owner nothing', () => {
    render(
      <ModelConnectionNotice
        config={config({
          model_connection_required: true,
          provided_sources: [providedSource([], { name: 'House models' })],
        })}
      />,
    );
    const notice = screen.getByRole('status');
    expect(notice).toHaveTextContent(
      'House models, which this deployment provides, has no model this account can use right now.',
    );
    expect(notice).toHaveTextContent('add a connection of your own');
    expect(screen.getByRole('link', { name: 'Set up a model connection' })).toBeInTheDocument();
  });

  it('offers no setup where the deployment has switched model connections off', () => {
    render(
      <ModelConnectionNotice
        config={config({ model_connection_required: true, model_connections_enabled: false })}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Model connections are unavailable');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('says nothing before the configuration has loaded', () => {
    const { container } = render(<ModelConnectionNotice config={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('offers signing in at the provider first, and setting one up by hand beside it', async () => {
    window.history.replaceState(null, '', '/?repo=acme%2Fapp');
    startModelSignIn.mockResolvedValue('https://provider.example/authorize/key?code_challenge=x');
    render(
      <ModelConnectionNotice
        config={config({
          model_connection_required: true,
          model_sign_in: { name: 'Example Inference', base_url: 'https://api.provider.example/v1' },
        })}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Create a Example Inference account, or link the one you have',
    );
    expect(screen.getByRole('link', { name: 'Add a connection by hand' })).toHaveAttribute(
      'href',
      '/settings/models#settings-connections',
    );

    fireEvent.click(
      screen.getByRole('button', { name: 'Create or link a Example Inference account' }),
    );

    await waitFor(() =>
      expect(leaveFor).toHaveBeenCalledWith(
        'https://provider.example/authorize/key?code_challenge=x',
      ),
    );
    // The callback page comes back here.
    expect(takeReturnPath()).toBe('/?repo=acme%2Fapp');
  });

  it('says why signing in could not start, and stays', async () => {
    startModelSignIn.mockRejectedValue(new Error('This deployment does not offer it.'));
    render(
      <ModelConnectionNotice
        config={config({
          model_connection_required: true,
          model_sign_in: { name: 'Example Inference', base_url: 'https://api.provider.example/v1' },
        })}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: 'Create or link a Example Inference account' }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This deployment does not offer it.',
    );
    expect(leaveFor).not.toHaveBeenCalled();
  });

  it('offers no sign-in where model connections are switched off', () => {
    render(
      <ModelConnectionNotice
        config={config({
          model_connection_required: true,
          model_connections_enabled: false,
          model_sign_in: { name: 'Example Inference', base_url: 'https://api.provider.example/v1' },
        })}
      />,
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
