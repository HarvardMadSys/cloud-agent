import type { AdminSettingDefinition } from '@/lib/api/adminSettings';

/**
 * Registry categories, and the names of tabs this page has since retired,
 * mapped to the tab that shows them now. Any other category is its own tab.
 */
const CATEGORY_NAMES = new Map([
  ['Appearance', 'General'],
  ['Sign-in', 'General'],
  ['Identity & access', 'Accounts'],
  ['Attachments', 'Content'],
  ['Skills', 'Content'],
  ['Task titles', 'Content'],
  ['Goals', 'Operations'],
  ['Database tools', 'Operations'],
  ['Execution', 'Sandbox'],
  ['Compute', 'CloudLab'],
]);

/**
 * Categories in the order administrators reach for them: what a deployment is
 * called and who may use it first, service internals last. Categories not
 * listed here follow, in the order the registry first names them.
 */
const CATEGORY_ORDER = [
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
  'CloudLab',
];

interface SettingGroup {
  title: string;
  keys: string[];
  /** Tuning most deployments keep at its defaults, shown under a collapsed Advanced section. */
  advanced?: true;
}

// Keys are listed explicitly so a new setting remains visible until its
// presentation has been considered: an unlisted key joins the group named after
// its registry category, outside Advanced and after every key listed here.
// Within a category, groups and keys appear in the order listed here, so the
// settings administrators change most come first. Timeouts, polling and retry
// intervals, cache lifetimes, rate limits, retention and low-level transport
// switches are advanced. An advanced group never takes a registry category's
// name, so an unlisted setting can never join a collapsed group.
const SETTING_GROUPS: SettingGroup[] = [
  // General
  {
    title: 'Branding',
    keys: ['site.app_name', 'site.org_name', 'site.org_url'],
  },
  {
    title: 'Public addresses',
    keys: ['web_base_url', 'public_api_url'],
  },
  {
    title: 'Navigation links',
    keys: ['site.status_url', 'site.docs_url', 'site.contact_url'],
  },
  // Accounts
  {
    title: 'Sign-up and approval',
    keys: [
      'accounts.signup_enabled',
      'accounts.require_email_verification',
      'accounts.approval_policy',
      'accounts.signup_admin_notify',
      'accounts.signup_notify_emails',
      'accounts.terms_url',
      'accounts.terms_text',
      'accounts.admin_emails',
      'accounts.session_ttl_days',
    ],
  },
  {
    title: 'Account email',
    keys: [
      'accounts.email_provider',
      'accounts.smtp_host',
      'accounts.smtp_port',
      'accounts.smtp_username',
      'accounts.smtp_password',
      'accounts.smtp_from',
      'accounts.smtp_security',
      'accounts.tencent_ses_region',
      'accounts.tencent_ses_from_email',
      'accounts.tencent_ses_from_name',
      'accounts.tencent_ses_template_verification',
      'accounts.tencent_ses_template_password_reset',
      'accounts.tencent_ses_template_approval',
      'accounts.tencent_ses_template_rejection',
      'accounts.tencent_ses_template_admin_signup',
      'accounts.tencent_ses_template_notice',
    ],
  },
  {
    title: 'Phone sign-in',
    keys: [
      'accounts.sms_enabled',
      'accounts.sms_provider',
      'accounts.sms_default_country_code',
      'accounts.sms_allowed_prefixes',
      'accounts.tencent_sms_region',
      'accounts.tencent_sms_app_id',
      'accounts.tencent_sms_sign_name',
      'accounts.tencent_sms_template_id',
    ],
  },
  {
    title: 'Tencent Cloud credentials',
    keys: ['accounts.tencent_secret_id', 'accounts.tencent_secret_key', 'accounts.tencent_token'],
  },
  {
    title: 'Captcha',
    keys: ['accounts.turnstile_site_key', 'accounts.turnstile_secret_key'],
  },
  {
    title: 'Sign-in and sign-up limits',
    advanced: true,
    keys: [
      'accounts.login_per_15min',
      'accounts.login_account_per_15min',
      'accounts.login_ip_per_hour',
      'accounts.signup_per_hour',
      'accounts.signup_per_day',
      'accounts.signup_require_resolved_ip',
      'accounts.unresolved_signup_per_hour',
      'accounts.unresolved_signup_per_day',
    ],
  },
  {
    title: 'Phone sign-in limits',
    advanced: true,
    keys: [
      'accounts.sms_code_ttl_s',
      'accounts.sms_send_cooldown_s',
      'accounts.sms_send_per_phone_hour',
      'accounts.sms_send_per_phone_day',
      'accounts.sms_send_per_ip_hour',
      'accounts.sms_send_per_ip_day',
      'accounts.sms_max_attempts',
      'accounts.sms_verify_per_phone_15min',
      'accounts.sms_verify_per_ip_hour',
    ],
  },
  // Source control
  {
    title: 'GitHub',
    keys: [
      'github_app_id',
      'github_app_client_id',
      'github_app_client_secret',
      'github_app_private_key',
      'github_app_install_url',
      'github_coauthor',
    ],
  },
  {
    title: 'Repository access',
    keys: ['repo_allowlist'],
  },
  {
    title: 'GitLab',
    keys: ['gitlab_client_id', 'gitlab_client_secret', 'gitlab_redirect_uri'],
  },
  {
    title: 'GitHub API',
    advanced: true,
    keys: [
      'github_api_url',
      'github_request_timeout_s',
      'github_repository_cache_s',
      'github_reload_interval_s',
    ],
  },
  {
    title: 'GitLab API',
    advanced: true,
    keys: ['gitlab_request_timeout_s'],
  },
  // MCP
  {
    title: 'MCP catalog',
    keys: ['mcp_registry_url'],
  },
  {
    title: 'GitHub sign-in',
    keys: ['mcp_github_client_id', 'mcp_github_client_secret'],
  },
  {
    title: 'MCP catalog refresh',
    advanced: true,
    keys: [
      'mcp_catalog_sync_seconds',
      'mcp_catalog_retry_seconds',
      'mcp_catalog_sync_timeout_s',
      'mcp_catalog_request_timeout_s',
      'mcp_catalog_storage_timeout_s',
    ],
  },
  {
    title: 'MCP connections',
    advanced: true,
    keys: [
      'mcp_public_base_url',
      'mcp_request_timeout_s',
      'mcp_connect_timeout_s',
      'mcp_metadata_timeout_s',
      'mcp_allow_private_targets',
      'mcp_allow_plain_http',
    ],
  },
  // Web search
  {
    title: 'Web search',
    keys: ['web_search_provider', 'serper_api_key', 'brave_search_api_key', 'parallel_api_key'],
  },
  // Content
  {
    title: 'Attachments',
    keys: [
      'attachments.max_file_bytes',
      'attachments.max_total_bytes',
      'attachments.max_files',
      'attachments.max_user_bytes',
    ],
  },
  {
    title: 'Skills',
    keys: [
      'skills.max_skill_bytes',
      'skills.max_selection_bytes',
      'skills.max_selected',
      'skills.max_library',
    ],
  },
  {
    title: 'Abandoned uploads',
    advanced: true,
    keys: ['attachments.staged_retention_hours'],
  },
  {
    title: 'Task title length',
    advanced: true,
    keys: ['task_titles.max_chars'],
  },
  // Security
  {
    title: 'Signing and encryption keys',
    keys: ['control_signing_secret', 'source_control_encryption_key', 'compute_encryption_key'],
  },
  {
    title: 'Session security',
    advanced: true,
    keys: ['session_cookie_secure', 'trust_proxy_headers'],
  },
  // Operations
  {
    title: 'Logging',
    keys: ['log_level', 'log_format'],
  },
  {
    title: 'Alerts',
    keys: ['alert_webhook_url'],
  },
  {
    title: 'Database tools',
    keys: ['pgadmin_url', 'pgadmin_public_url'],
  },
  {
    title: 'Alert timing',
    advanced: true,
    keys: ['queue_alert_after_s', 'queue_monitor_interval_s', 'queue_alert_cooldown_s'],
  },
  {
    title: 'Job recovery',
    advanced: true,
    keys: ['max_attempts', 'residency_ttl_s'],
  },
  {
    title: 'Goal scheduling',
    advanced: true,
    keys: [
      'goals.arm_after_seconds',
      'goals.stalled_after_seconds',
      'goals.max_wakeup_attempts',
      'goals.wakeup_backoff_seconds',
      'goals.wakeup_backoff_max_seconds',
    ],
  },
  {
    title: 'Usage and history',
    advanced: true,
    keys: ['ledger_idle_s', 'capacity_history_retention_s'],
  },
  {
    title: 'Main API',
    advanced: true,
    keys: ['api_docs', 'streams.max_per_user'],
  },
  // Agent versions
  {
    title: 'Package registry',
    keys: ['runtime_registry_url'],
  },
  // Sandbox
  {
    title: 'Isolation',
    keys: ['default_execution_profile'],
  },
  {
    title: 'Sandbox managers',
    keys: ['sandbox_manager_token', 'sandbox_manager_url_allowed_hosts'],
  },
  // CloudLab
  {
    title: 'CloudLab API',
    keys: ['compute.cloudlab_api_url'],
  },
  {
    title: 'Managed servers',
    advanced: true,
    keys: ['compute.registration_url', 'compute.poll_seconds', 'compute.observation_seconds'],
  },
];

interface SettingPlacement {
  group: string;
  advanced: boolean;
  /** Position among the listed settings; every unlisted setting shares the last. */
  rank: number;
}

const PLACEMENTS = new Map<string, SettingPlacement>();
for (const { title, keys, advanced = false } of SETTING_GROUPS) {
  for (const key of keys) PLACEMENTS.set(key, { group: title, advanced, rank: PLACEMENTS.size });
}

function placement(definition: AdminSettingDefinition): SettingPlacement {
  return (
    PLACEMENTS.get(definition.key) ?? {
      group: definition.category || 'General',
      advanced: false,
      rank: PLACEMENTS.size,
    }
  );
}

/**
 * Application settings categories shown on another tab rather than their own,
 * because that tab already describes what they configure. `title` heads their
 * card there, and `section` names the tab in search results.
 */
export const HOSTED_CATEGORIES: ReadonlyMap<
  string,
  { tab: string; section: string; title: string }
> = new Map([
  [
    'Agent versions',
    { tab: 'versions', section: 'Agent versions', title: 'Where agent versions come from' },
  ],
  ['Sandbox', { tab: 'sandbox', section: 'Sandbox', title: 'Sandbox execution' }],
  ['CloudLab', { tab: 'cloudlab', section: 'CloudLab', title: 'Compute services' }],
]);

/** The settings tab that shows an application settings category. */
export function applicationCategoryTab(category: string): string {
  return HOSTED_CATEGORIES.get(category)?.tab ?? `application-${encodeURIComponent(category)}`;
}

export function applicationCategoryName(category: string): string {
  return CATEGORY_NAMES.get(category) ?? (category || 'General');
}

export function applicationCategory(definition: AdminSettingDefinition): string {
  return applicationCategoryName(definition.category);
}

/** Sorts categories into the order their tabs appear; others keep their order, after. */
export function compareCategories(first: string, second: string): number {
  const rank = (category: string) => {
    const index = CATEGORY_ORDER.indexOf(category);
    return index === -1 ? CATEGORY_ORDER.length : index;
  };
  return rank(first) - rank(second);
}

export function applicationSettingGroup(definition: AdminSettingDefinition): string {
  return placement(definition).group;
}

/** Whether a setting belongs under its category's collapsed Advanced section. */
export function isAdvancedSetting(definition: AdminSettingDefinition): boolean {
  return placement(definition).advanced;
}

export interface OrganizedGroup {
  title: string;
  fields: AdminSettingDefinition[];
}

/**
 * One category's settings in groups: the groups an administrator changes most
 * first, in the order listed above, then any unlisted ones in registry order;
 * the advanced groups separately, in the same order.
 */
export function organizeCategory(definitions: AdminSettingDefinition[]): {
  groups: OrganizedGroup[];
  advanced: OrganizedGroup[];
} {
  const ranked = definitions
    .map((definition, index) => ({ definition, index, ...placement(definition) }))
    .sort((first, second) => first.rank - second.rank || first.index - second.index);
  const collect = (advanced: boolean) => {
    const groups = new Map<string, AdminSettingDefinition[]>();
    for (const entry of ranked) {
      if (entry.advanced !== advanced) continue;
      groups.set(entry.group, [...(groups.get(entry.group) ?? []), entry.definition]);
    }
    return [...groups].map(([title, fields]) => ({ title, fields }));
  };
  return { groups: collect(false), advanced: collect(true) };
}
