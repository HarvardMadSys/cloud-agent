// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSyncExternalStore } from 'react';

import type { AgentAdminSettings } from '@/lib/api/adminSettings';
import { getAgentAdminSettings, setAgentAdminSettings } from '@/lib/api/adminSettings';
import AdminSettingsPage from './page';

const search = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  return {
    value: '',
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    read: () => search.value,
    set(next: string) {
      search.value = next;
      for (const listener of listeners) listener();
    },
  };
});
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(useSyncExternalStore(search.subscribe, search.read)),
}));
vi.mock('@/components/providers', () => ({
  useAuth: () => ({
    state: {
      user: {
        id: 'admin-a',
        role: 'free',
        agent_access: { allowed: true, permissions: ['agent.use', 'agent.admin'] },
      },
    },
  }),
}));
vi.mock('@/lib/api/adminSettings', () => ({
  getAgentAdminSettings: vi.fn(),
  setAgentAdminSettings: vi.fn(),
}));
vi.mock('@/lib/api/admin', async (original) => ({
  ...(await original<typeof import('@/lib/api/admin')>()),
  getAgentRunnerHosts: vi.fn(async () => ({
    hosts: [],
    host_setting_definitions: [],
    runtime_versions: {},
    host_settings: {},
  })),
}));
vi.mock('@/lib/api/adminArchiveSettings', async (original) => ({
  ...(await original<typeof import('@/lib/api/adminArchiveSettings')>()),
  getSandboxArchiveSettings: vi.fn(async () => null),
}));
vi.mock('@/lib/api/adminRuntimeUpgrades', async (original) => ({
  ...(await original<typeof import('@/lib/api/adminRuntimeUpgrades')>()),
  getRuntimeUpgrades: vi.fn(async () => ({ runtimes: [] })),
}));
vi.mock('@/lib/api/compute', () => ({
  getCloudLabDefaults: vi.fn(() => new Promise(() => {})),
  saveCloudLabDefaults: vi.fn(),
}));

type Field = [key: string, category: string, label: string, kind?: 'boolean' | 'integer'];

// Real registry keys and categories, in registry order, which names tuning
// such as request timeouts before the settings administrators change.
const FIELDS: Field[] = [
  ['log_level', 'Operations', 'Control-plane log level'],
  ['github_request_timeout_s', 'Source control', 'GitHub request timeout (seconds)', 'integer'],
  ['mcp_registry_url', 'MCP', 'MCP registry URL'],
  ['mcp_allow_plain_http', 'MCP', 'Allow HTTP MCP targets', 'boolean'],
  ['web_search_provider', 'Web search', 'Web search provider'],
  ['site.app_name', 'Appearance', 'Application name'],
  ['compute.cloudlab_api_url', 'Compute', 'CloudLab API URL'],
  ['compute.poll_seconds', 'Compute', 'Compute scheduling interval (seconds)', 'integer'],
  ['web_base_url', 'Sign-in', 'Web app URL'],
  ['github_app_id', 'Source control', 'GitHub App ID'],
  ['queue_alert_after_s', 'Operations', 'Queue alert age (seconds)', 'integer'],
  ['attachments.max_file_bytes', 'Attachments', 'Maximum upload file size (bytes)', 'integer'],
  ['goals.arm_after_seconds', 'Goals', 'Goal activation delay (seconds)', 'integer'],
  ['task_titles.max_chars', 'Task titles', 'Maximum task title length', 'integer'],
  ['control_signing_secret', 'Security', 'Host and worker signing key'],
  ['default_execution_profile', 'Execution', 'New session isolation policy'],
  ['trust_proxy_headers', 'Security', 'Trust forwarded client addresses', 'boolean'],
  ['runtime_registry_url', 'Agent versions', 'Agent package registry'],
  ['accounts.signup_enabled', 'Accounts', 'Public sign-up', 'boolean'],
  ['accounts.login_per_15min', 'Accounts', 'Sign-in attempts per address (15 min)', 'integer'],
];

function snapshot(over: Partial<AgentAdminSettings> = {}): AgentAdminSettings {
  const initial = (kind: Field[3]) => (kind === 'boolean' ? false : kind === 'integer' ? 60 : '');
  return {
    revision: 1,
    definitions: FIELDS.map(([key, category, label, kind]) => ({
      key,
      category,
      label,
      description: '',
      kind: kind ?? 'string',
      default: initial(kind),
      ...(kind === 'integer' ? { minimum: 60, maximum: 86400 } : {}),
      secret: false,
    })),
    values: Object.fromEntries(FIELDS.map(([key, , , kind]) => [key, initial(kind)])),
    secret_configured: {},
    bootstrap: [],
    ...over,
  };
}

function open(path = '/admin/settings', settings = snapshot()) {
  vi.mocked(getAgentAdminSettings).mockResolvedValue(settings);
  window.history.replaceState(null, '', path);
  render(<AdminSettingsPage />);
}

/** A tab's panel, shown or not: a hidden panel has no accessible name to find it by. */
function panel(name: string): HTMLElement {
  const tab = screen.getByRole('tab', { name });
  return document.getElementById(tab.getAttribute('aria-controls')!)!;
}

/** The collapsible section a field belongs to, or null when it is always in view. */
function section(label: string): HTMLDetailsElement | null {
  return screen.getByLabelText(label).closest('details');
}

function summary(label: string): HTMLElement {
  return section(label)!.querySelector('summary')!;
}

beforeEach(() => {
  vi.clearAllMocks();
  search.value = '';
  const replace = window.history.replaceState.bind(window.history);
  vi.spyOn(window.history, 'replaceState').mockImplementation((data, unused, url) => {
    replace(data, unused, url);
    search.set(window.location.search);
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('settings categories', () => {
  it('lists the tabs administrators reach for first, with service internals last', async () => {
    open();
    const tablist = screen.getByRole('tablist', { name: 'Settings sections' });
    await screen.findByRole('tab', { name: 'General' });
    expect(
      within(tablist)
        .getAllByRole('tab')
        .map((tab) => tab.textContent),
    ).toEqual([
      'General',
      'Accounts',
      'Source control',
      'MCP',
      'Web search',
      'Content',
      'Security',
      'Operations',
      'Agent versions',
      'Sandbox',
      'Models',
      'CloudLab',
    ]);
    expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true');
    // Branding and the public addresses share the first tab.
    expect(within(panel('General')).getByLabelText('Application name')).toBeVisible();
    expect(within(panel('General')).getByLabelText('Web app URL')).toBeVisible();
  });

  it.each([
    ['application-Identity%20%26%20access', 'Accounts'],
    ['application-Appearance', 'General'],
    ['application-Sign-in', 'General'],
    ['application-Security', 'Security'],
    ['application-Goals', 'Operations'],
    ['application-Execution', 'Sandbox'],
    ['application-Compute', 'CloudLab'],
  ])('opens a link to the former tab %s on %s', async (tab, name) => {
    open(`/admin/settings?tab=${tab}`);
    await waitFor(() =>
      expect(screen.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true'),
    );
    expect(panel(name)).toBeVisible();
  });

  it('shows execution and compute services on the Sandbox and CloudLab tabs', async () => {
    open('/admin/settings?tab=sandbox');
    const execution = await screen.findByRole('region', { name: 'Sandbox execution' });
    expect(within(execution).getByLabelText('New session isolation policy')).toBeVisible();
    expect(screen.queryByRole('tab', { name: 'Compute' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'CloudLab' }));
    const compute = screen.getByRole('region', { name: 'Compute services' });
    expect(within(compute).getByLabelText('CloudLab API URL')).toBeVisible();
    expect(
      within(compute).getByLabelText('Compute scheduling interval (seconds)'),
    ).not.toBeVisible();
  });
});

describe('important settings first, tuning under Advanced', () => {
  it.each([
    ['Accounts', 'Public sign-up', 'Sign-in attempts per address (15 min)'],
    ['Source control', 'GitHub App ID', 'GitHub request timeout (seconds)'],
    ['MCP', 'MCP registry URL', 'Allow HTTP MCP targets'],
    ['Content', 'Maximum upload file size (bytes)', 'Maximum task title length'],
    ['Security', 'Host and worker signing key', 'Trust forwarded client addresses'],
    ['Operations', 'Control-plane log level', 'Queue alert age (seconds)'],
  ])('on %s, shows %s first and keeps %s collapsed', async (tab, important, advanced) => {
    open();
    fireEvent.click(await screen.findByRole('tab', { name: tab }));
    const shown = within(panel(tab)).getByLabelText(important);
    const tuning = within(panel(tab)).getByLabelText(advanced);
    expect(shown).toBeVisible();
    expect(section(important)).toBeNull();
    expect(tuning).not.toBeVisible();
    expect(section(advanced)).not.toHaveAttribute('open');
    expect(summary(advanced)).toHaveTextContent(/^Advanced\d+ settings?$/);
    // The registry lists the tuning first; the page does not.
    expect(shown.compareDocumentPosition(tuning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(summary(advanced));
    expect(tuning).toBeVisible();
  });

  it('has no Advanced section where nothing is tuning', async () => {
    open();
    await screen.findByRole('tab', { name: 'General' });
    for (const tab of ['General', 'Web search']) {
      expect(within(panel(tab)).queryByText('Advanced')).not.toBeInTheDocument();
    }
  });
});

describe('reaching a collapsed setting', () => {
  it('opens Advanced for a search result, then focuses and scrolls to the setting', async () => {
    open();
    await screen.findByRole('tab', { name: 'General' });
    const input = screen.getByLabelText('Trust forwarded client addresses');
    const scroll = vi.fn();
    input.scrollIntoView = scroll;

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search settings' }), {
      target: { value: 'forwarded' },
    });
    fireEvent.click(
      within(screen.getByRole('region', { name: 'Settings search results' })).getByRole('button', {
        name: 'Open Trust forwarded client addresses in Security · Advanced',
      }),
    );

    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toBeVisible();
    expect(section('Trust forwarded client addresses')).toHaveAttribute('open');
    expect(screen.getByRole('tab', { name: 'Security' })).toHaveAttribute('aria-selected', 'true');
    expect(scroll).toHaveBeenCalledWith({ block: 'center' });
  });

  it('opens Advanced for a link to the setting, on the tab that hosts it', async () => {
    open('/admin/settings#application-setting-compute.poll_seconds');
    const input = await screen.findByLabelText('Compute scheduling interval (seconds)');
    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toBeVisible();
    expect(screen.getByRole('tab', { name: 'CloudLab' })).toHaveAttribute('aria-selected', 'true');
    expect(new URLSearchParams(window.location.search).get('tab')).toBe('cloudlab');
  });

  it('opens Advanced for a missing setting the setup notice links to', async () => {
    open(
      '/admin/settings',
      snapshot({
        setup: {
          required: true,
          groups: [
            {
              key: 'security',
              label: 'Session security',
              required: true,
              configured: false,
              needs_setup: true,
              missing: [{ key: 'trust_proxy_headers', label: 'Trust forwarded client addresses' }],
            },
          ],
          bootstrap_missing: [],
        },
      }),
    );
    const [link] = await screen.findAllByRole('link', { name: 'Trust forwarded client addresses' });
    const input = screen.getByLabelText('Trust forwarded client addresses');
    expect(input).not.toBeVisible();
    fireEvent.click(link);
    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toBeVisible();
    expect(panel('Security')).toBeVisible();
    expect(new URLSearchParams(window.location.search).get('tab')).toBe('application-Security');
  });
});

describe('edits under Advanced', () => {
  it('keeps an unsaved change and its error in view until it is saved or cancelled', async () => {
    open('/admin/settings?tab=application-Operations');
    const label = 'Queue alert age (seconds)';
    await screen.findByLabelText(label);
    fireEvent.click(summary(label));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Alert timing settings' }));
    const input = screen.getByRole('spinbutton', { name: label });
    fireEvent.change(input, { target: { value: '5' } });

    // Collapsing would hide the draft, so the section stays open and says why.
    fireEvent.click(summary(label));
    expect(input).toBeVisible();
    expect(summary(label)).toHaveTextContent('Unsaved changes');
    expect(summary(label)).toHaveAttribute('aria-disabled', 'true');

    // Its error is in view too, and so is the draft after visiting another tab.
    fireEvent.click(screen.getByRole('button', { name: 'Review Alert timing changes' }));
    expect(within(section(label)!).getByRole('alert')).toHaveTextContent(
      'Enter a value of at least 60.',
    );
    fireEvent.click(screen.getByRole('tab', { name: 'General' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Operations' }));
    expect(input).toBeVisible();
    expect(input).toHaveValue(5);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel editing Alert timing' }));
    expect(summary(label)).not.toHaveTextContent('Unsaved changes');
    fireEvent.click(summary(label));
    expect(section(label)).not.toHaveAttribute('open');
    expect(setAgentAdminSettings).not.toHaveBeenCalled();
  });

  it('keeps an advanced draft while another group of its category is saved', async () => {
    open('/admin/settings?tab=application-Operations');
    const label = 'Queue alert age (seconds)';
    await screen.findByLabelText(label);
    fireEvent.click(summary(label));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Alert timing settings' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: label }), {
      target: { value: '120' },
    });

    const current = snapshot();
    vi.mocked(setAgentAdminSettings).mockResolvedValue({
      ...current,
      revision: 2,
      values: { ...current.values, log_level: 'DEBUG' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Edit Logging settings' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Control-plane log level' }), {
      target: { value: 'DEBUG' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Review Logging changes' }));
    await act(async () => {
      fireEvent.click(
        within(screen.getByRole('dialog')).getByRole('button', { name: 'Apply changes' }),
      );
    });

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('Logging settings saved.'),
    );
    expect(screen.getByRole('spinbutton', { name: label })).toHaveValue(120);
    expect(screen.getByRole('spinbutton', { name: label })).toBeVisible();
    expect(setAgentAdminSettings).toHaveBeenCalledWith(
      { log_level: 'DEBUG' },
      1,
      expect.any(AbortSignal),
    );
  });
});
