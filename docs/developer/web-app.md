# The owner web app

The rules each owner-facing feature follows, the endpoints behind it, and what
only a browser can check. User documentation belongs to each distribution; the
app's Help link opens it (**Admin → Settings → General**, `site.docs_url`, seeded from
`AGENT_WEB_DOCS_URL`).

## Sidebar and conversation list

The sidebar keeps its layout per signed-in user in the current browser:
grouping (by project or one list), ordering (last updated or manual), expanded
projects, manual positions and added workspaces. Pinned conversations share
one manual order across both groupings and appear once, outside the project
folders. Conversations without a repository go in a **Start from scratch**
folder.

Search (Command+K on a Mac, Ctrl+K elsewhere) matches titles and full
repository names across the whole history; a result opens the conversation's
latest turn. A completed turn shows an unread dot until it is opened. Read
receipts are saved to the account, and open tabs sync them on focus, on
reconnection and every 30 seconds while visible.

### Creating a repository

**Create repository**, in the repository pickers, creates a repository with a
README (so it has a default branch) in the user's personal GitHub account,
never an organization's. It needs the GitHub App's **Repository creation:
write** or **Administration: Read and write** permission
([Credentials](operations.md#2-credentials)). The App does not gain access to
the new repository, and repository-access checks still apply.

### Getting started

An account with no conversations, archived ones included, sees a **Getting
started** row: **Connect a model**, **Connect GitHub**, **Grant a repository**
and **Run your first task**, with state from `/config`. The model step appears
while `model_connections_enabled` is true and is done once
`model_connection_required` is false. The GitHub steps are left out while the
deployment has no usable GitHub App, and the row is hidden when nothing is
left to offer. The sidebar, the new-task page and the conversation
explanations share one first-run check,
`GET /v1/agent/conversations?include_archived=true&limit=2`. **Skip** hides the
row for that account in that browser; the settings page brings it back.

### Conversations API

`GET /v1/agent/conversations` returns `conversations` and `next_cursor`. Each
row carries the latest `job`, the canonical `title`, `archived` and
`last_activity_at`.

| Parameter | Meaning |
| --- | --- |
| `q` | Case-insensitive literal substring of the title or full repository name, at most 200 characters; `%` and `_` match themselves |
| `repo` | Exact repository |
| `temporary` | `true` lists only conversations without a repository; not with `repo` |
| `include_archived` | Include archived conversations; defaults to false |
| `pinned` | `true` or `false` for only pinned or only unpinned rows; omitted, both |
| `limit` | Page size, 1–200; defaults to 50 |
| `cursor` | Opaque cursor from the previous response, scoped to the owner and the filters |

Rows are ordered by pin time, then activity, then thread id. Conversation and
message updates and job transitions count as activity; lease heartbeats do
not. Because a conversation can move ahead of an older cursor, clients reload
from the first page when activity changes. `/jobs` keeps its own semantics.

Sources: [`AgentsSidebar.tsx`](../../apps/frontend/src/components/features/agents/sidebar/AgentsSidebar.tsx),
[`sidebarPreferences.ts`](../../apps/frontend/src/components/features/agents/sidebar/sidebarPreferences.ts),
[`catalog.py`](../../apps/backend/cloud_agent/api/catalog.py) and
[`conversation_queries.py`](../../apps/backend/cloud_agent/storage/repositories/conversation_queries.py).

## Conversation view

### The turn rail

The rail sits in the Chat column's right gutter when the conversation has at
least two visible turns, the column is at least 720px wide, the reading area
at least 200px high, and the pointer is fine; Trajectory has none. The current
turn has the longest, darkest mark. Mark length shows interaction state only,
never message length, duration or tokens. The rail scrolls on its own in long
conversations.

A mark's preview shows the question as sent and, once the turn has ended, the
attempt's last visible reply, capped at 50 and 120 characters, without
reasoning or tool output. Clicking a mark puts the turn's first question about
24px below the top of the reading area, without changing the URL, the
selected attempt or the workspace. Hovering and jumping send no request. The
rail is a named navigation region of buttons with one mark in the tab order;
arrow keys, Home and End reach the others, and the current mark has
`aria-current`.

### Reading position

The transcript either follows new output or holds the reader's text in place.
It opens following, and follows again after **Back to bottom**, a scroll to
the live end, or a follow-up the reader sent. Scrolling up, jumping through
the rail or selecting an older attempt holds the position; new content then
marks **Back to bottom** without pulling the reader back. Switching to
Trajectory and back keeps the position, and reloading opens at the end.

### Tasks and subagents

The **Tasks and subagents** window shows the selected turn and attempt's
latest task list and delegated agents, when either has recorded data. A
subagent's reply, under **Returned to the main agent**, comes from:

- Claude Code's Agent or Task, and OpenCode's and Kilo's task: the call's own
  result (Claude also records the agent's status);
- Codex: the message of the agent state its `wait` call reports;
- pi: nothing, since it has no subagents.

A tool from an MCP server is never shown as a task list or subagent, whatever
its name. Trajectory links a child step to its delegating step through runtime
ownership records: Claude Code records the delegating call, and Codex the
subagent's thread.

### Turn screenshots and sandbox incidents

After a turn settles, the transcript shows up to six of the latest raster
images written under the workspace artifacts directory between `startedAt`
minus 2 seconds and `finishedAt` plus 5 seconds. The listing is cached per
conversation for 20 seconds, and a cold or deleted sandbox shows nothing. A
`lifecycle` event with phase `sandbox_incident` (`out_of_memory`,
`container_exited`, `mcp_server_failed`) shows as a one-line notice without
limits or exit codes.

Sources: [`TurnScreenshots.tsx`](../../apps/frontend/src/components/features/agents/conversation/TurnScreenshots.tsx)
and [`sandboxIncidentNotice.ts`](../../apps/frontend/src/components/features/agents/adapt/sandboxIncidentNotice.ts).

### Workspace pane keyboard

In the workspace, Git, file and terminal tab strips, Left/Right, Home and End
select a view. A narrow Git pane picks the changed file from a selector so the
diff keeps the full width. Opening a file in a narrow pane focuses its
preview, and **Back** or Escape returns to that file in the list.

### Checking it in a browser

Unit and DOM tests (`useConversationScroll.dom.test.tsx`) cover the
navigation data, focus and an injected box model. Reflow, the sticky composer,
resize timing and real pixel positions need a browser and a conversation with
several turns:

1. Click a middle mark: its question lands about 24px below the top and the
   mark becomes current.
2. Scroll well into a long turn: its mark stays current.
3. Scroll up while output streams: the text holds its position within a few
   pixels and **Back to bottom** appears.
4. Use **Back to bottom** or scroll to the end: following resumes.
5. Try columns on both sides of 720px, reading areas on both sides of 200px,
   and touch input.
6. Expand a tool row, resize the composer, and toggle the workspace while
   reading history: the text stays in place.
7. With about 100 turns, check mark spacing, rail scrolling and keyboard
   access.
8. Fork a conversation: the copied turns have marks and previews.
9. Switch to Trajectory and back: no rail there, and Chat keeps its position.
10. Repeat the keyboard checks in both languages and themes, and with reduced
    motion.

Design: [turn navigation](../agents/specs/2026-09-11-conversation-turn-navigation-design.zh.md).
Sources: [`JobDetail.tsx`](../../apps/frontend/src/components/features/agents/JobDetail.tsx),
[`TurnNavigator.tsx`](../../apps/frontend/src/components/features/agents/conversation/TurnNavigator.tsx),
[`conversationTurnNavigation.ts`](../../apps/frontend/src/components/features/agents/conversation/conversationTurnNavigation.ts)
and [`useConversationScroll.ts`](../../apps/frontend/src/components/features/agents/conversation/useConversationScroll.ts).

## Models and the composer picker

**Settings → Models** (`/settings/models`) manages the models an owner's tasks
run on. Deployment-provided models come first, by name; the owner's personal
connections fold beneath them and open for a link with
`#settings-connections`. In the composer, provided models and then the
owner's enabled connections form one flat list. An owner's own connection
overrides a provided model of the same name. A follow-up keeps its parent's
model and connection together, and marks the selection unavailable rather
than switch to another source with the same model name.

Sources: [`ModelsSettingsView.tsx`](../../apps/frontend/src/components/features/settings/ModelsSettingsView.tsx),
[`ProvidedModelsCard.tsx`](../../apps/frontend/src/components/features/settings/ProvidedModelsCard.tsx),
[`providedModels.ts`](../../apps/frontend/src/components/features/agents/composer/providedModels.ts)
and [`modelSource.ts`](../../apps/frontend/src/components/features/agents/composer/modelSource.ts).

## Tool approvals

The **Approvals** inbox lists pending MCP calls across the owner's
conversations. **Approve** releases that one held request under its current
authorization and attempt fence, and **Deny** refuses it; neither changes a
conversation's permission rules. An expired call leaves the queue, and a
decision that loses a race with expiry or another tab cannot revive it.

**Always allow**, in a conversation's tools panel, records the approval and
the permission in one server-side transaction. The rule is saved once no call
on that connection waits for approval or dispatch, and covers only that
capability in that conversation. Before saving, the server checks the owner,
connection and policy revisions and the capability fingerprint again, and
discards the request if any changed, so a revoked permission is never
restored.

A held call waits up to ten minutes. Claude Code's per-server request and idle
timeouts match its 26-minute tool timeout, so its network timers cannot
abandon a call Approvals still shows as pending. A client that accepts SSE
gets keepalives during the wait, then the single JSON-RPC result. Disconnecting
cancels the wait and expires the approval, which is then never dispatched. An
error after dispatch can leave the outcome unknown.

`GET /v1/agent/mcp/invocations?pending_only=true` reads the owner's pending
inbox; `job_id` or `thread_id` narrows it, and a history read requires one of
them. `limit` is 1 to 100 (default 50). Page with `next_before_id` as
`before_id` while `has_more` is true. Reading never changes an invocation or
grants a permission. The relay side is in
[MCP servers](operations.md#authorization-and-routing).

## Memory, avatars and home suggestions

Memory holds the lasting rules agents follow for an owner
([design](../agents/specs/2026-10-13-memory-redesign.zh-CN.md)). Avatars and
home suggestions are worked out from recent requests on each read; they are
not stored, not given to agents, and not affected by memory settings.

### Memory

A memory is one English sentence in one of two scopes: user memory applies to
every project, and project memory to one repository, where it takes
precedence. Agents call `memory_save`, `memory_update` and `memory_delete` on
the platform's MCP endpoint, saving to the task's repository unless told the
rule applies everywhere. Updating or deleting archives the old memory. Each
write checks that the task's attempt is still the running one.

When a task starts, the control plane adds the repository's and the user's
active memories ahead of the request, up to 12,000 characters, repository
memories and the most recently edited first. Each scope has its own switch.
A scope holds up to 30 active memories of at most 240 characters. When it is
full, the least recently used agent-saved memory is archived; owner-written
or edited ones never are, so a scope full of them refuses a new one. Archived
memories can be restored for 90 days.

| Request | What it does |
| --- | --- |
| `GET /v1/agent/memories` | The user memories, as `{repo, enabled, memories, archived}`; `?repo=owner/repository` for one repository's |
| `POST /v1/agent/memories` with `{content, repo}` | Adds an owner-written memory; `repo` is `null` for every project |
| `PATCH /v1/agent/memories/{id}` with `{content, repo, expected_version}` | Rewrites or moves a memory |
| `DELETE /v1/agent/memories/{id}` with `{expected_version}` | Deletes a memory |
| `POST /v1/agent/memories/{id}/restore` with `{expected_version}` | Restores an archived memory |
| `DELETE /v1/agent/memories` | Clears one scope, archive included |
| `PUT /v1/agent/memories/settings` with `{repo, enabled}` | Turns a scope on or off |
| `GET /v1/agent/memories/projects` | The repositories the owner worked in or keeps memories for |

Refusals: `memory_changed` (409, stale version), `memory_scope_full` (409),
`memory_invalid` (422: credentials, over 240 characters, or limited to the
current task) and `memory_not_found` (404). No response echoes memory text.

### Avatars and home suggestions

Without a profile photo, an account is shown as one of seven generated Pokémon
companions, fixed for the account (`companion_data_url` from
`GET /v1/agent/profile`). On the homepage, its habitat and badges follow the
work areas that English keywords reveal in the owner's latest 50 requests,
and up to three suggestions point out change requests without success checks,
failure reports without reproduction steps, and reviews without a stated
scope. Only the owner's own text counts. `GET /v1/agent/insights` returns
`{focus_areas, suggestions, avatar}`. Everything is drawn locally as SVG; no
identifier or request text is embedded, and no image service or model is
used.

## Workspace files

The **Files** pane shows the retained sandbox's repository, read-only. Paths
stay inside the repository: absolute paths, parent traversal and `.git` are
refused, and symlinks are not followed. A long folder listing says what it
left out. Copy and download offer only complete files within the preview
limit; raster images get a preview, and HTML and SVG show as escaped text. In
a conversation without a repository, **Download workspace** saves the
workspace as one `.tar.gz` without `.git` and metadata files, and refuses a
workspace over the server's limit
([Temporary workspaces](operations.md#temporary-workspaces)).

## Smaller features

- **Notifications** (**My Settings → Notifications**): browser notifications
  for agent questions and finished turns, per account, while the app is open.
- **Plugins** (**+ → Plugins**, before a chat's first task, up to eight):
  Claude Code and Codex take `name@marketplace` from a GitHub repository or
  public HTTPS marketplace; pi, OpenCode and Kilo take npm package names,
  optionally pinned (`@example/plugin@1.2.3`). A failed installation stops
  startup, and Fork inherits the selection.
- **Review**: an independent code-review subagent in a workspace tab, covering
  every change since the conversation's original base, told not to edit
  files. It needs a native Fork, so it cannot start from an archived chat or
  one whose context was lost.
- **Watch PR and merge**: a follow-up in which the agent watches checks and
  reviews, pushes fixes to the pull request's branch, and merges once the head
  meets the required checks and reviews. It ends with the turn's timeout.

Watch PR and merge works as the owner through the GitHub relay, with git and
`gh api` REST calls, so the owner needs a GitHub account that may push to the
repository. The relay refuses GraphQL, so the agent asks the owner to mark a
draft ready, and never merges a draft.
