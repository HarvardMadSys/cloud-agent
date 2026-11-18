// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getAgentAdminSettings } from '@/lib/api/adminSettings';
import {
  getSandboxArchiveSettings,
  updateSandboxArchiveSettings,
  type SandboxArchiveSettings,
} from '@/lib/api/adminArchiveSettings';
import { APIError } from '@/lib/utils/errors';

import AdminSettingsPage from './page';
import { applicationSnapshot } from './settingsTestFixtures';

// The archive bucket is set once: the control plane refuses a change with a
// typed 409, and the page shows why beside the bucket, not as a page error.

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams('tab=sandbox'),
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
  getSandboxArchiveSettings: vi.fn(),
  updateSandboxArchiveSettings: vi.fn(),
}));
vi.mock('@/lib/api/adminRuntimeUpgrades', async (original) => ({
  ...(await original<typeof import('@/lib/api/adminRuntimeUpgrades')>()),
  getRuntimeUpgrades: vi.fn(async () => ({ runtimes: [] })),
}));

const ARCHIVES: SandboxArchiveSettings = {
  enabled: true,
  s3_uri: 's3://example-archives/cloud-agent/sandbox',
  region: 'us-east-1',
  kms_key_arn: 'arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000',
  expected_bucket_owner: '123456789012',
  aws_access_key_id: '',
  aws_secret_access_key_configured: false,
  max_archive_bytes: 2_147_483_648,
  multipart_threshold_bytes: 8_388_608,
  multipart_part_bytes: 8_388_608,
  revision: 1,
  updated_at: null,
};
const REFUSAL =
  'The archive bucket cannot be changed: 1 checkpoint of a live sandbox is stored in ' +
  's3://example-archives/cloud-agent/sandbox and can be read back only from that bucket. ' +
  'Moving a deployment to another bucket is not supported.';

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAgentAdminSettings).mockResolvedValue(applicationSnapshot());
  vi.mocked(getSandboxArchiveSettings).mockResolvedValue(ARCHIVES);
});
afterEach(cleanup);

async function saveBucket(bucket: string) {
  const field = await screen.findByLabelText('S3 bucket');
  fireEvent.change(field, { target: { value: bucket } });
  fireEvent.click(screen.getByRole('button', { name: 'Save archive settings' }));
  return field;
}

describe('a refused archive bucket change', () => {
  it('is shown beside the bucket, with the draft kept and no page error', async () => {
    vi.mocked(updateSandboxArchiveSettings).mockRejectedValue(
      new APIError('archive_bucket_locked', REFUSAL, 409),
    );
    render(<AdminSettingsPage />);

    const field = await saveBucket('other-archives');

    expect(await screen.findByRole('alert')).toHaveTextContent(REFUSAL);
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveValue('other-archives');
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });

  it('leaves any other failure to the page error', async () => {
    vi.mocked(updateSandboxArchiveSettings).mockRejectedValue(
      new APIError(
        'UNKNOWN_ERROR',
        'Sandbox archive settings changed since this page loaded.',
        409,
      ),
    );
    render(<AdminSettingsPage />);

    const field = await saveBucket('other-archives');

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(field).toHaveAttribute('aria-invalid', 'false');
  });
});
