import type { AdminSettingDefinition, AdminSettingValue } from '@/lib/api/adminSettings';

type ParsedValue = { value: AdminSettingValue } | { error: string };

/** Whether S3 accepts `bucket` as the name of a general purpose bucket. */
export function isS3BucketName(bucket: string): boolean {
  return (
    /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket) &&
    !/^\d{1,3}(?:\.\d{1,3}){3}$/.test(bucket) &&
    !/\.\.|\.-|-\./.test(bucket) &&
    // Prefixes and suffixes S3 reserves for names it gives out itself.
    !/^(?:xn--|sthree-|amzn-s3-demo-)/.test(bucket) &&
    !/(?:-s3alias|--ol-s3|\.mrap|--x-s3|--table-s3)$/.test(bucket)
  );
}

export function displayValue(value: AdminSettingValue): string {
  return typeof value === 'boolean' ? (value ? 'Enabled' : 'Disabled') : String(value);
}

interface SettingOption {
  value: string;
  label: string;
}

// Named choices for free-text settings whose usual values are internal names.
// The server still accepts any value (a registered email provider, say), so a
// saved value outside this list stays selectable instead of being replaced.
const NAMED_OPTIONS = new Map<string, SettingOption[]>([
  [
    'accounts.email_provider',
    [
      { value: 'smtp', label: 'SMTP' },
      { value: 'tencent_ses', label: 'Tencent SES' },
    ],
  ],
]);

/** The dropdown for a setting with named choices, keeping its saved value; else `null`. */
export function namedOptions(key: string, saved: string): SettingOption[] | null {
  const options = NAMED_OPTIONS.get(key);
  if (!options) return null;
  if (options.some((option) => option.value === saved)) return options;
  return [...options, { value: saved, label: `${saved || '(empty)'} (current value)` }];
}

/** How a saved value reads: its named choice where it has one. */
export function valueLabel(key: string, value: AdminSettingValue): string {
  const named = NAMED_OPTIONS.get(key)?.find((option) => option.value === value);
  return named?.label ?? displayValue(value);
}

export function parseValue(definition: AdminSettingDefinition, draft: string): ParsedValue {
  if (definition.kind === 'boolean') return { value: draft === 'true' };
  if (definition.kind === 'string') {
    if (definition.choices?.length && !definition.choices.includes(draft)) {
      return { error: 'Choose one of the available values.' };
    }
    if (
      draft &&
      definition.minimum_bytes &&
      new TextEncoder().encode(draft).length < definition.minimum_bytes
    ) {
      return { error: `Enter a value of at least ${definition.minimum_bytes} bytes.` };
    }
    return { value: draft };
  }
  const value = Number(draft);
  if (draft.trim() === '' || !Number.isFinite(value)) return { error: 'Enter a number.' };
  if (definition.kind === 'integer' && !Number.isInteger(value)) {
    return { error: 'Enter a whole number.' };
  }
  if (definition.minimum !== undefined && value < definition.minimum) {
    return { error: `Enter a value of at least ${definition.minimum}.` };
  }
  if (definition.maximum !== undefined && value > definition.maximum) {
    return { error: `Enter a value no greater than ${definition.maximum}.` };
  }
  return { value };
}
