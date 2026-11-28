// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { rememberReturn } from '@/components/features/agents/githubReturn';
import { APIError } from '@/lib/utils/errors';
import ModelSignInCallbackPage from './page';

const navigation = vi.hoisted(() => ({ search: '', replace: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

vi.mock('@/lib/api/modelConnections', () => ({ finishModelSignIn: vi.fn() }));
vi.mock('@/components/features/agents/composer/modelConnectionEvents', () => ({
  announceModelConnectionsChanged: vi.fn(),
}));

import { announceModelConnectionsChanged } from '@/components/features/agents/composer/modelConnectionEvents';
import { finishModelSignIn } from '@/lib/api/modelConnections';

const finish = finishModelSignIn as unknown as ReturnType<typeof vi.fn>;
const announce = announceModelConnectionsChanged as unknown as ReturnType<typeof vi.fn>;

function saved(skipped: string[] = []) {
  return { connection: { id: 'mconn_1', name: 'Example Inference' }, skipped_models: skipped };
}

describe('ModelSignInCallbackPage', () => {
  beforeEach(() => {
    sessionStorage.clear();
    navigation.search = '';
    navigation.replace.mockReset();
    finish.mockReset();
    announce.mockReset();
  });

  afterEach(cleanup);

  it('hands the code and state over once, then goes back to where the trip started', async () => {
    rememberReturn('/?repo=acme%2Fapp');
    navigation.search = 'state=signed-state&code=one-time-code';
    finish.mockResolvedValue(saved());

    render(
      <StrictMode>
        <ModelSignInCallbackPage />
      </StrictMode>,
    );

    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/?repo=acme%2Fapp'));
    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith('one-time-code', 'signed-state');
    expect(announce).toHaveBeenCalled();
  });

  it('ends on the Models page when the trip did not note a start', async () => {
    navigation.search = 'state=signed-state&code=one-time-code';
    finish.mockResolvedValue(saved());

    render(<ModelSignInCallbackPage />);

    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/settings/models'));
  });

  it('names the models it left off before going back', async () => {
    navigation.search = 'state=signed-state&code=one-time-code';
    finish.mockResolvedValue(saved(['model-b', 'model-c']));

    render(<ModelSignInCallbackPage />);

    expect(await screen.findByText('Example Inference is connected')).toBeInTheDocument();
    expect(screen.getByText(/Left off: model-b, model-c\./)).toBeInTheDocument();
    expect(navigation.replace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(navigation.replace).toHaveBeenCalledWith('/settings/models');
  });

  it('does not send a code again after it worked', async () => {
    navigation.search = 'state=signed-state&code=one-time-code';
    finish.mockResolvedValue(saved());
    const { unmount } = render(<ModelSignInCallbackPage />);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalled());
    unmount();
    navigation.replace.mockReset();

    render(<ModelSignInCallbackPage />);

    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/settings/models'));
    expect(finish).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['state=s&code=c&error=access_denied', 'No key was approved at the provider'],
    ['state=s', 'The provider did not return a code.'],
    ['code=c', 'The provider did not return the sign-in state.'],
  ])('sends nothing for %s', async (search, message) => {
    navigation.search = search;

    render(<ModelSignInCallbackPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(finish).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'Back to Models' })).toHaveAttribute(
      'href',
      '/settings/models',
    );
  });

  it('never shows what the provider wrote into the address', async () => {
    navigation.search = 'state=s&code=c&error=%3Cb%3Eforged%3C%2Fb%3E';

    render(<ModelSignInCallbackPage />);

    expect(await screen.findByRole('alert')).not.toHaveTextContent('forged');
  });

  it("shows the server's refusal and the way back", async () => {
    navigation.search = 'state=signed-state&code=one-time-code';
    finish.mockRejectedValue(
      new APIError('model_sign_in_expired', 'This sign-in expired or was already used.', 400),
    );

    render(<ModelSignInCallbackPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This sign-in expired or was already used.',
    );
    expect(navigation.replace).not.toHaveBeenCalled();
  });
});
