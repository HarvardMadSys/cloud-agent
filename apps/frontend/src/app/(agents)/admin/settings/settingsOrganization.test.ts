import { describe, expect, it } from 'vitest';

import type { AdminSettingDefinition } from '@/lib/api/adminSettings';

import {
  applicationCategory,
  applicationCategoryName,
  applicationSettingGroup,
  compareCategories,
  isAdvancedSetting,
  organizeCategory,
} from './settingsOrganization';

function definition(key: string, category: string): AdminSettingDefinition {
  return {
    key,
    category,
    label: key,
    description: '',
    kind: 'string',
    default: '',
    secret: false,
  };
}

function titled(groups: { title: string; fields: AdminSettingDefinition[] }[]) {
  return groups.map(({ title, fields }) => [title, fields.map(({ key }) => key)]);
}

describe('application settings organization', () => {
  it.each([
    ['Appearance', 'General'],
    ['Sign-in', 'General'],
    ['Accounts', 'Accounts'],
    ['Security', 'Security'],
    ['Source control', 'Source control'],
    ['MCP', 'MCP'],
    ['Web search', 'Web search'],
    ['Attachments', 'Content'],
    ['Skills', 'Content'],
    ['Task titles', 'Content'],
    ['Operations', 'Operations'],
    ['Goals', 'Operations'],
    ['Database tools', 'Operations'],
    ['Agent versions', 'Agent versions'],
    ['Execution', 'Sandbox'],
    ['Compute', 'CloudLab'],
  ])('places registry category %s on %s', (category, expected) => {
    expect(applicationCategory(definition('future.setting', category))).toBe(expected);
    expect(applicationCategoryName(category)).toBe(expected);
  });

  it('sends a link to the former Identity & access tab to Accounts', () => {
    expect(applicationCategoryName('Identity & access')).toBe('Accounts');
  });

  it('orders tabs as administrators reach for them, with unknown categories after', () => {
    const categories = [
      'Operations',
      'Future',
      'Security',
      'Content',
      'Accounts',
      'Web search',
      'Another',
      'MCP',
      'Source control',
      'General',
    ];
    expect([...categories].sort(compareCategories)).toEqual([
      'General',
      'Accounts',
      'Source control',
      'MCP',
      'Web search',
      'Content',
      'Security',
      'Operations',
      'Future',
      'Another',
    ]);
  });

  it('keeps future categories and settings visible without inferring grouping from their names', () => {
    const field = definition('future.request_timeout_s', 'Future / category ✓');
    expect(applicationCategory(field)).toBe('Future / category ✓');
    expect(applicationSettingGroup(field)).toBe('Future / category ✓');
    expect(isAdvancedSetting(field)).toBe(false);
    expect(applicationCategoryName('constructor')).toBe('constructor');
  });

  it('puts uncategorized settings in General', () => {
    const field = definition('future.setting', '');
    expect(applicationCategory(field)).toBe('General');
    expect(applicationSettingGroup(field)).toBe('General');
  });

  it('groups the GitHub App and the agents’ commit co-author together', () => {
    const app = definition('github_app_id', 'Source control');
    const coauthor = definition('github_coauthor', 'Source control');
    expect(applicationCategory(app)).toBe(applicationCategory(coauthor));
    expect(applicationSettingGroup(app)).toBe('GitHub');
    expect(applicationSettingGroup(coauthor)).toBe('GitHub');
  });

  it.each([
    ['web_base_url', 'Public addresses'],
    ['public_api_url', 'Public addresses'],
    ['attachments.max_file_bytes', 'Attachments'],
    ['task_titles.max_chars', 'Task title length'],
    ['default_execution_profile', 'Isolation'],
    ['sandbox_manager_token', 'Sandbox managers'],
    ['site.org_name', 'Branding'],
    ['log_level', 'Logging'],
    ['mcp_allow_private_targets', 'MCP connections'],
    ['runtime_registry_url', 'Package registry'],
    ['compute.cloudlab_api_url', 'CloudLab API'],
  ])('organizes %s into its related group', (key, group) => {
    const field = definition(key, 'General');
    expect(applicationSettingGroup(field)).toBe(group);
  });

  it('keeps related keys together regardless of whether they are read only', () => {
    const field = definition('source_control_encryption_key', 'Security');
    expect(applicationSettingGroup(field)).toBe('Signing and encryption keys');
    expect(applicationSettingGroup({ ...field, read_only: true })).toBe(
      'Signing and encryption keys',
    );
  });
});

describe('important settings and tuning', () => {
  it.each([
    'site.app_name',
    'web_base_url',
    'public_api_url',
    'accounts.signup_enabled',
    'accounts.approval_policy',
    'accounts.admin_emails',
    'accounts.email_provider',
    'accounts.smtp_host',
    'accounts.turnstile_secret_key',
    'github_app_id',
    'github_app_private_key',
    'repo_allowlist',
    'gitlab_client_secret',
    'mcp_registry_url',
    'mcp_github_client_secret',
    'web_search_provider',
    'attachments.max_user_bytes',
    'control_signing_secret',
    'log_level',
    'alert_webhook_url',
    'pgadmin_url',
    'runtime_registry_url',
    'default_execution_profile',
    'compute.cloudlab_api_url',
  ])('shows %s among the settings administrators change', (key) => {
    expect(isAdvancedSetting(definition(key, ''))).toBe(false);
  });

  it.each([
    'accounts.login_per_15min',
    'accounts.signup_require_resolved_ip',
    'accounts.sms_code_ttl_s',
    'accounts.sms_send_per_ip_day',
    'github_api_url',
    'github_request_timeout_s',
    'github_repository_cache_s',
    'github_reload_interval_s',
    'gitlab_request_timeout_s',
    'mcp_catalog_sync_seconds',
    'mcp_catalog_retry_seconds',
    'mcp_public_base_url',
    'mcp_connect_timeout_s',
    'mcp_allow_private_targets',
    'mcp_allow_plain_http',
    'attachments.staged_retention_hours',
    'task_titles.max_chars',
    'session_cookie_secure',
    'trust_proxy_headers',
    'queue_alert_cooldown_s',
    'max_attempts',
    'residency_ttl_s',
    'goals.wakeup_backoff_seconds',
    'capacity_history_retention_s',
    'api_docs',
    'streams.max_per_user',
    'compute.registration_url',
    'compute.poll_seconds',
  ])('collapses the tuning setting %s under Advanced', (key) => {
    expect(isAdvancedSetting(definition(key, ''))).toBe(true);
  });

  it('lists the settings administrators change first, whatever order the registry uses', () => {
    const fields = [
      'github_reload_interval_s',
      'github_repository_cache_s',
      'github_request_timeout_s',
      'gitlab_request_timeout_s',
      'repo_allowlist',
      'github_app_id',
      'github_app_private_key',
      'github_api_url',
      'gitlab_client_id',
      'github_coauthor',
    ].map((key) => definition(key, 'Source control'));

    const { groups, advanced } = organizeCategory(fields);
    expect(titled(groups)).toEqual([
      ['GitHub', ['github_app_id', 'github_app_private_key', 'github_coauthor']],
      ['Repository access', ['repo_allowlist']],
      ['GitLab', ['gitlab_client_id']],
    ]);
    expect(titled(advanced)).toEqual([
      [
        'GitHub API',
        [
          'github_api_url',
          'github_request_timeout_s',
          'github_repository_cache_s',
          'github_reload_interval_s',
        ],
      ],
      ['GitLab API', ['gitlab_request_timeout_s']],
    ]);
  });

  it('shows an unlisted setting after the listed groups, outside Advanced, in registry order', () => {
    const { groups, advanced } = organizeCategory([
      definition('goals.future_wait_s', 'Goals'),
      definition('goals.arm_after_seconds', 'Goals'),
      definition('log_level', 'Operations'),
      definition('goals.future_limit', 'Goals'),
    ]);
    // An unlisted Goals setting does not join the collapsed Goal scheduling group.
    expect(titled(groups)).toEqual([
      ['Logging', ['log_level']],
      ['Goals', ['goals.future_wait_s', 'goals.future_limit']],
    ]);
    expect(titled(advanced)).toEqual([['Goal scheduling', ['goals.arm_after_seconds']]]);
  });
});
