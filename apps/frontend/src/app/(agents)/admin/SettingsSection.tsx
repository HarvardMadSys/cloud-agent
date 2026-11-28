'use client';

import { useCallback, useEffect, useState, type ReactNode } from 'react';

/** A titled group of related settings, always visible. */
export function SettingsSection({
  title,
  description,
  children,
  className = 'mt-4',
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <h3 className="text-base font-semibold text-foreground">{title}</h3>
      {description && <p className="mt-1 text-sm leading-6 text-muted">{description}</p>}
      <div className="mt-3">{children}</div>
    </div>
  );
}

export type ReportPending = (id: string, pending: boolean) => void;

/**
 * Settings most deployments keep at their defaults, placed after the ones they
 * change and collapsed until opened. What it holds stays mounted while it is
 * collapsed, so drafts in it survive, and the page opens it before focusing a
 * field inside for a link or a search result. While it holds an unsaved
 * change or an error it stays open, so neither is ever out of sight.
 */
export function AdvancedSettings({
  count,
  description,
  open,
  onOpenChange,
  pending = false,
  className = 'mt-4',
  children,
}: {
  /** How many settings it holds, for its summary. */
  count: number;
  description: string;
  /** Whether it is open, when its owner decides that; otherwise it keeps its own state. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Whether it holds an unsaved change or an error, which keeps it open. */
  pending?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const [ownOpen, setOwnOpen] = useState(false);
  const expanded = (open ?? ownOpen) || pending;
  const change = (next: boolean) => {
    setOwnOpen(next);
    onOpenChange?.(next);
  };
  return (
    <details
      data-advanced-settings=""
      open={expanded}
      onToggle={(event) => {
        // The browser also opens it, for find in page, a link or a search result.
        if (event.currentTarget.open !== expanded) change(event.currentTarget.open);
      }}
      className={`group/advanced rounded-xl border border-border ${className}`}
    >
      <summary
        aria-disabled={pending || undefined}
        onClick={(event) => {
          // State, not the browser, toggles it, so a render never undoes a click.
          event.preventDefault();
          if (!pending) change(!expanded);
        }}
        className="ui-focus-ring flex min-h-11 cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 rounded-xl px-4 py-2 text-sm hover:bg-surface-hover aria-disabled:cursor-default sm:px-5 [&::-webkit-details-marker]:hidden"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-4 w-4 shrink-0 text-muted transition-transform group-open/advanced:rotate-90"
        >
          <path d="m8 5 5 5-5 5" />
        </svg>
        <span className="font-semibold text-foreground">Advanced</span>
        <span className="text-xs text-muted">
          {count} {count === 1 ? 'setting' : 'settings'}
        </span>
        {pending && (
          <span className="ml-auto rounded-full bg-accent/10 px-2 py-0.5 text-[11px] font-medium text-accent">
            Unsaved changes
          </span>
        )}
      </summary>
      <div className="border-t border-border px-4 pb-4 pt-3 sm:px-5">
        <p className="text-xs leading-5 text-muted">{description}</p>
        <div className="mt-4">{children}</div>
      </div>
    </details>
  );
}

/**
 * Whether any editor in a section holds an unsaved change or an error, and the
 * callback each editor reports that through with `useReportPending`.
 */
export function usePendingEditors(): [boolean, ReportPending] {
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  const report = useCallback<ReportPending>((id, isPending) => {
    setPending((previous) => {
      if (previous.has(id) === isPending) return previous;
      const next = new Set(previous);
      if (isPending) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);
  return [pending.size > 0, report];
}

/** Reports that one editor is pending while it is, and withdraws that afterwards. */
export function useReportPending(id: string, pending: boolean, report?: ReportPending) {
  useEffect(() => {
    if (!pending || !report) return;
    report(id, true);
    return () => report(id, false);
  }, [id, pending, report]);
}
