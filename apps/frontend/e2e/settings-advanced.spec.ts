import { type Page } from '@playwright/test';
import { expect, test } from './network-change';

type Field = [key: string, category: string, label: string, kind: string, value: unknown];

// Real registry keys: two tabs, each with settings administrators change and tuning.
const FIELDS: Field[] = [
  ['log_level', 'Operations', 'Control-plane log level', 'string', 'INFO'],
  ['queue_alert_after_s', 'Operations', 'Queue alert age (seconds)', 'integer', 600],
  ['goals.arm_after_seconds', 'Goals', 'Goal activation delay (seconds)', 'integer', 60],
  ['pgadmin_url', 'Database tools', 'Private pgAdmin URL', 'string', ''],
  ['trust_proxy_headers', 'Security', 'Trust forwarded client addresses', 'boolean', false],
  ['session_cookie_secure', 'Security', 'Require HTTPS session cookies', 'boolean', true],
];

test.beforeEach(async ({ page, request }) => {
  expect((await request.post('/__fixture/reset')).ok()).toBe(true);
  await page.route('**/api/v1/session/me', (route) =>
    route.fulfill({
      json: {
        id: 'browser-owner',
        agent_access: { allowed: true, permissions: ['agent.use', 'agent.admin'] },
      },
    }),
  );
  await page.route('**/api/admin/agent/settings', (route) =>
    route.fulfill({
      json: {
        revision: 1,
        definitions: FIELDS.map(([key, category, label, kind, value]) => ({
          key,
          category,
          label,
          kind,
          default: value,
          ...(kind === 'integer' ? { minimum: 60, maximum: 86400 } : {}),
          secret: false,
          description: `${label}.`,
        })),
        values: Object.fromEntries(FIELDS.map(([key, , , , value]) => [key, value])),
        secret_configured: {},
        bootstrap: [],
        setup: { required: false, groups: [], bootstrap_missing: [] },
      },
    }),
  );
  await page.route('**/api/admin/agent/runner-hosts', (route) =>
    route.fulfill({ json: { hosts: [], host_setting_definitions: [], runtime_versions: {} } }),
  );
  await page.route('**/api/admin/agent/sandbox-archive-settings', (route) =>
    route.fulfill({ json: null }),
  );
});

test.afterEach(async ({ request }) => {
  const state = await (await request.get('/__fixture/state')).json();
  expect(state.unknown).toEqual([]);
});

const setting = (page: Page, key: string) => page.locator(`[id="application-setting-${key}"]`);

for (const width of [1440, 390]) {
  test(`tuning starts collapsed and a search result opens it at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/admin/settings?tab=application-Operations');
    const panel = page.getByRole('tabpanel', { name: 'Operations', exact: true });
    await expect(setting(page, 'log_level')).toBeVisible();
    await expect(setting(page, 'pgadmin_url')).toBeVisible();
    const advanced = panel.locator('summary', { hasText: 'Advanced' });
    await expect(advanced).toHaveText(/Advanced\s*2 settings/);
    await expect(setting(page, 'queue_alert_after_s')).toBeHidden();
    expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
      true,
    );
    await page.screenshot({ path: testInfo.outputPath('collapsed.png'), fullPage: true });

    await page.getByRole('searchbox', { name: 'Search settings' }).fill('queue alert');
    await page
      .getByRole('region', { name: 'Settings search results' })
      .getByRole('button', { name: 'Open Queue alert age (seconds) in Operations · Advanced' })
      .click();
    await expect(setting(page, 'queue_alert_after_s')).toBeFocused();
    await expect(setting(page, 'queue_alert_after_s')).toBeInViewport();
    await expect(setting(page, 'goals.arm_after_seconds')).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('opened.png'), fullPage: true });
  });
}

test('a link to a tuning setting opens its tab and its Advanced section', async ({ page }) => {
  await page.goto('/admin/settings#application-setting-trust_proxy_headers');
  await expect(page.getByRole('tab', { name: 'Security', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(setting(page, 'trust_proxy_headers')).toBeFocused();
  await expect(setting(page, 'trust_proxy_headers')).toBeInViewport();
});

test('an unsaved change keeps Advanced open until it is cancelled', async ({ page }) => {
  await page.goto('/admin/settings?tab=application-Operations');
  const panel = page.getByRole('tabpanel', { name: 'Operations', exact: true });
  const advanced = panel.locator('summary', { hasText: 'Advanced' });
  await advanced.click();
  await page.getByRole('button', { name: 'Edit Alert timing settings', exact: true }).click();
  const field = page.getByRole('spinbutton', { name: 'Queue alert age (seconds)', exact: true });
  await field.fill('900');

  await advanced.click();
  await expect(field).toBeVisible();
  await expect(advanced).toContainText('Unsaved changes');
  await page.getByRole('tab', { name: 'Security', exact: true }).click();
  await page.getByRole('tab', { name: 'Operations', exact: true }).click();
  await expect(field).toHaveValue('900');

  await page.getByRole('button', { name: 'Cancel editing Alert timing', exact: true }).click();
  await advanced.click();
  await expect(setting(page, 'queue_alert_after_s')).toBeHidden();
});
