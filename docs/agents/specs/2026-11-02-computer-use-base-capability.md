# Computer use base capability

Status: implemented for the browser runtime (phase 1) and in-sandbox MCP servers (phase 2). Phase 3 (live view, takeover, recordings) is not built.

## Summary

Cloud Agent ships computer use as a platform base capability, not as a choice of tool. Each task sandbox has a managed browser, a stable environment contract, and lifecycle and isolation rules. Users pick the MCP server or CLI that drives the browser. The platform hard-codes none.

1. **Browser runtime in the sandbox image.** Any browser CLI an agent installs in its home directory can drive the managed browser.
2. **In-sandbox MCP servers.** A user-selected MCP server runs next to the browser, admitted per session by the trusted host, for all five runtimes.
3. **Live view and takeover. Not built.** Claude Code on the web and Codex cloud give the user no live view of or control over the agent's browser, and we follow them. A turn shows the screenshots its tools saved instead.

## Background

No runtime could see or operate a UI before this. The sandbox had no browser, and the agent runs as uid 10001 (or a member uid) without sudo, so it cannot install one. Native computer use does not apply: Claude Code's needs macOS and a claude.ai login, and Codex's ships in the desktop app. Every MCP server ran outside the sandbox, so the agent saw only the platform relay:

- Claude Code starts with `--strict-mcp-config --mcp-config <relay>` and `--setting-sources user`.
- Codex reads a root-owned `/etc/codex/requirements.toml` with an empty `[mcp_servers]` allowlist, and the trusted host admits only the relay, as `session`.
- pi reaches MCP through `pi-mcp-bridge.ts`. OpenCode and Kilo are configured relay-only as well.

Relay-only wiring is on purpose: a repository cannot auto-start its own MCP servers. It also means a relayed Playwright MCP cannot reach the task's `localhost`, its files or a sandbox browser.

## Goals and non-goals

Goals:

- An agent in any of the five runtimes can open, read and operate a page served by the task (`localhost`) or by an allowed external host.
- The user chooses the driver tool.
- Browser state belongs to one sandbox (a chat and its Forks, all one owner's) and is reclaimed with it.
- Existing guarantees hold: repositories cannot auto-start MCP servers, egress stays guarded, no credentials leak into the sandbox.

Non-goals: our own UI-understanding model or browser agent, a graphical desktop, operating the user's own machine or logged-in browser, and picking a "best" tool.

## Requirements

| # | Requirement | What the platform owns |
| --- | --- | --- |
| R1 | Runtime environment | Chromium and its libraries in the image |
| R2 | Reach the task | The browser runs in the task's network namespace and sees its `localhost` and files |
| R3 | Perception and input | Nothing beyond R1. Accessibility tree, DOM, screenshots and input come from CDP, used by the tool |
| R4 | Coding-loop signals | Console, network and page errors reachable from the same tool session |
| R5 | Tool attachment | A user-chosen CLI or MCP server runs next to the browser through a well-known contract |
| R6 | Session continuity | Cookies, tabs and storage persist across turns, for a chat and its Forks |
| R7 | Lifecycle | Start on demand, idle reclaim, crash recovery, kill with the sandbox |
| R8 | Isolation | No browser shared between sandboxes. The browser runs outside every member's UID. No control endpoint is reachable from outside the sandbox |
| R9 | Evidence | Screenshots and traces land in the workspace and show in the UI. A turn shows the images its tools saved |
| R10 | Live view and takeover | Not built |

## Where the tool runs

| Option | Where the tool runs | Cost | Verdict |
| --- | --- | --- | --- |
| A. Expose CDP to external MCP | Control plane or user connector | CDP is full browser control, so it needs per-session auth, a tunnel and revocation | Not built. It needs the same channel as live view |
| B. In-sandbox MCP server | Inside the sandbox | Five runtime wirings, keeping repository configs out, pinned install | Phase 2 |
| C. In-sandbox CLI | Inside the sandbox | No structured tool schema | Phase 1 |

C needs only R1, since Playwright CLI, agent-browser and Browser Use CLI install with `npm` or `uv` into the user's home and every runtime has a shell tool. B keeps the browser's control port inside the sandbox, so there is no new remote attack surface. A is the only option that needs a channel from outside into the sandbox.

## Phase 0 findings

Chromium (Debian bookworm's `chromium` 156.0.8078.79) runs under the sandbox's limits (`--user 10001`, `--cap-drop ALL`, `no-new-privileges`, `--pids-limit 512`, `--memory 4g`), but only without its own sandbox. The Kata guest uses the same OCI seccomp profile, which refuses unprivileged user namespaces.

| Question | Result |
| --- | --- |
| Unprivileged user namespaces | Refused |
| Chromium with its own sandbox | Fails at start: "No usable sandbox!" |
| Chromium with `--no-sandbox` | Starts and renders headless |
| Image growth | About 274 MB uncompressed, including libraries and two font packages |
| Idle browser | 11 processes, about 140 threads, 174 MiB |
| Four real pages | 15 processes, about 213 threads, 321 MiB |

**Decision: run the managed browser with `--no-sandbox` when the guest refuses user namespaces.** `cloud-agent-browser status` then reports `chromium_sandbox: false`.

- Member UIDs already run unvetted repository code, so the image treats that side as untrusted. A compromised renderer gains only the browser's own user (`agent-browser`), which holds no member credentials and can write only the shared workspace and its own home.
- The boundary that matters, the container and the Kata VM, is unchanged.
- The platform cannot enforce Chromium's sandbox anyway. A member can start the same binary, or a browser a tool downloads, with `--no-sandbox`.
- A seccomp profile that allows `CLONE_NEWUSER`, or a VM per browser-using member, widens the kernel surface or costs a VM each, and gains nothing against that threat.

The launcher prefers Chromium's sandbox whenever user namespaces work, so a later seccomp change takes effect without a new image. `ALLOW_UNSANDBOXED` in the launcher reverses the decision. "Work" is probed by mapping a user into a new namespace (`unshare --user --map-root-user`), because under Ubuntu's AppArmor restriction `unshare --user true` succeeds but the namespace has no capabilities. If a sandboxed Chromium still exits before it answers, the service restarts it once with `--no-sandbox` in the time left, logs why, and reports `chromium_sandbox: false`. A sandboxed Chromium that is only slow is not restarted.

One browser with a few pages stays well inside the 512-thread limit. A group shares one managed browser, so a Fork adds tabs, not a second browser. The launcher caps renderers at four (`--renderer-process-limit=4`).

## Design

The platform ships Chromium, a launcher and a small environment contract. Tools attach on top of the contract and are never baked in. The launcher is the boundary: everything above it is the user's choice, everything below it is the platform's. Only the existing relay leaves the sandbox.

### Browser runtime (phase 1)

- **Binary.** Debian's `chromium` package (full Chromium, so a headed mode stays possible), plus `fonts-liberation` and `fonts-noto-color-emoji`, in its own layer of `Dockerfile.agent-sandbox`. Debian's build exists for amd64 and arm64 (Chrome for Testing has no arm64 Linux build) and gets Debian's security updates with every rebuild. CDP clients connect to any current Chromium.
- **One browser per sandbox.** Every member of a group (the chat and its Forks) shares one managed browser, its tabs and its sign-ins, as they share `/workspace`. A group belongs to one owner.
- **Its own user.** The browser runs as `agent-browser` (uid 10002, group `agent`), not as a member. The turn-end reap (`member-process-reaper`) kills every process a member's UID started, so a member's browser would never outlive a turn, and a shared browser cannot belong to one member. A member cannot switch UID inside the sandbox, so the trusted host starts the service.
- **Launcher.** `/usr/local/bin/cloud-agent-browser` is root-owned, mode 0555, Python standard library only. Before each member launch the host runs `cloud-agent-browser service start` as `agent-browser` (`cloud_agent_host/sandbox_browser.py`). The command is idempotent, so a restarted container gets its service back with its next member. The service is a small loopback server. It starts headless Chromium (`--headless=new`, `--disable-dev-shm-usage`, background networking, component updates, crash upload and metrics off, at most four renderers, one profile) when the first client connects, and stops it after the idle timeout.
- **One pipe, many clients.** Chromium speaks CDP only over `--remote-debugging-pipe` to the launcher's daemon. The daemon serves it as a WebSocket on `127.0.0.1`, at a random port, behind a random path token (`ws://127.0.0.1:<port>/<token>/devtools/browser`).
  - Each client gets its own browser-level session (`Target.attachToBrowserTarget`). Command ids are rewritten into one space, and each reply and event goes only to the client that owns the session.
  - `Browser.close` from a client ends only that client's connection.
  - Each client's output is written by its own task, so a client that stops reading cannot delay another. A client more than 256 MiB behind is disconnected.
- **Downloads across users.** A client names its own download directory, which Chromium (as `agent-browser`) often cannot write. When it cannot, the service points Chromium at a staging directory and, when the download finishes, asks a courier running as the client's UID to copy the file to where the client reads it. Only then does it pass on the "completed" event. The client's UID comes from the kernel's socket table. The courier is one process per UID, started by `cloud-agent-browser start`, registered over a unix socket and checked with `SO_PEERCRED`. It copies only a regular file the service user owns, from the staging directory into a directory its own UID owns, under a new name. So a compromised browser can at most add a new file to one member directory. A download with no courier, or one that cannot be copied, is reported to the client as canceled and logged.
- **No HTTP discovery.** There is no `/json/version`, because clients resolve it against the server root, which drops the token from the path. Every client we target accepts a WebSocket URL.
- **Downloads of other browsers stay possible.** Egress allows them, and a repository's tests may depend on the exact browser its tooling downloads, so the image sets neither `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` nor `PUPPETEER_SKIP_DOWNLOAD`. It sets `CHROME_PATH`. The preinstalled libraries are what let a downloaded browser start as a non-root member.
- **Tool install.** Users install a CLI into `~/.local` or `~/.npm-global` from their environment setup. Pinning is the user's choice.

### Environment and connection contract (v1)

This is the stable surface that templates and user tools rely on. It is versioned, and additions are backward-compatible.

| Name | Kind | Meaning |
| --- | --- | --- |
| `cloud-agent-browser start` | command | Ensures Chromium runs and prints the CDP WebSocket URL. `--json` prints the status instead |
| `cloud-agent-browser status` | command | JSON: `service`, `running`, `endpoint`, `browser`, `chromium_sandbox`, `last_error`, `profile`, `artifacts_dir` |
| `cloud-agent-browser stop` | command | Closes Chromium for every member. The service and the profile stay, and the next `start` relaunches it |
| `cloud-agent-browser self-test` | command | Starts the browser and checks it end to end (two clients, a page, a screenshot, session isolation) |
| `cloud-agent-browser service start` / `stop` | command, platform | Runs or ends the service as `agent-browser`. The host runs `service start`. A member cannot stop the service |
| `CLOUD_AGENT_BROWSER_CONTRACT` | env | Contract version, `1` |
| `CLOUD_AGENT_BROWSER_BIN` | env | `/usr/bin/chromium` |
| `CLOUD_AGENT_ARTIFACTS_DIR` | env | `/workspace/.cloud-agent-artifacts`: screenshots, traces and downloads. Shown in the workspace files and kept out of Git |
| `CLOUD_AGENT_BROWSER_STATE_DIR` | env | `/run/agent-browser` (0750, owned by `agent-browser`, group `agent`): the endpoint file, status record (0640) and service log |
| `CLOUD_AGENT_BROWSER_PROFILE` | env | `/home/agent-browser/profile` (0700) |
| `CLOUD_AGENT_BROWSER_IDLE_S` | env, optional | Idle timeout in seconds. Default 900 |

The image sets every value, and they are the same in every sandbox.

Only a browser started by the launcher is managed: it gets idle reclaim, session continuity, token-guarded CDP and a lifetime independent of any member. A tool may still launch `CLOUD_AGENT_BROWSER_BIN` itself. That browser is an ordinary user process, killed with the sandbox and counted against its limits, with no promise about its debug port, profile or lifetime. Templates always use the managed browser.

### In-sandbox MCP servers (phase 2)

A user can run an MCP server in the sandbox, next to the agent, instead of behind the relay. It then sees the task's `localhost`, its files and the sandbox browser.

- **Location.** `sandbox` beside `remote`, `managed-worker` and `user-connector` (migration `0111_mcp_sandbox_location`). Such a server is one exactly pinned package started by `npx` or `uvx` (`npx -y pkg@1.2.3`, `uvx pkg==1.2.3`; not `latest`, not a range), with no mount and no working directory. `cloud_agent.mcp.launch` enforces this when the connection is saved and again at every claim. The server may use credentials, as any command the agent runs may use the owner's environment. Its own keys are sealed at rest and named `secret://NAME`, and `${NAME}` is filled from the chat's environment. Only the slot's runner receives either value. A sign-in (static header credential or OAuth) is refused, because a command takes its keys as environment values.
- **A gateway, not a stdio entry per CLI.** The image ships `cloud-agent-mcp`. Its gateway presents a member's servers to the member's CLI as one Streamable HTTP server named `sandbox`, at the fixed address `http://127.0.0.1:47801/mcp`, the way the relay presents remote ones as `session`. Tools are named `<alias>__<tool>`, as the relay names them.
  - A server spawned by the CLI as the member would be killed by the member's turn-end reap. The gateway's user is not a member.
  - Every runtime already speaks HTTP MCP to the relay, so each needs one more URL, not a stdio client. pi's bridge has none.
  - With a fixed address, Codex's root-owned allowlist names it once (`[mcp_servers.sandbox]`).
- **Three identities.** A server is code the user chose but nobody reviewed, so it must affect nothing beyond its own member.
  - The admission says which servers a slot may use. The host writes it as root into the root-owned `/run/agent-mcp-admissions`.
  - The gateway runs as `agent-mcp` (uid 10003). It serves the address and runs no server.
  - Each slot's runner runs as `agent-mcp-<slot>` (uid 12001 + slot). It runs that slot's servers as stdio children and answers only the gateway, checked with `SO_PEERCRED`.

  A server can reach its own runner and its member's other servers. It cannot write an admission, signal the gateway, take its address (the host starts the gateway before the runner) or reach another slot. An end-to-end test in the built image runs the attack where one slot's server rewrites another's admission, and every step is refused.
- **Admission stays with the trusted host.** A claim carries the job's sandbox servers (`sandbox_mcp_servers`: alias, command, args, plain env). The relay identity never lists them, so the relay never dials one. Before each member launch the host (`cloud_agent_host/sandbox_mcp.py`) writes the slot's admission as root, starts the gateway as `agent-mcp`, and starts the slot's runner as its runner user.
  - The admission carries the SHA-256 of the member's MCP credential, the token the CLI already sends to the relay, so one header reaches both servers. Only the digest is written. A request is served only with the servers of the member whose admission matches its token.
  - The runner starts admitted servers at once, so the CLI's first tool list does not wait for an install. A server with no call running for the idle timeout is stopped, and a call in progress keeps its server. A tool call is routed to the server that listed the tool.
- **Per-runtime wiring.** One more entry in what `AgentRuntime.relay_urls` returns, with the relay's header:
  - Claude Code: another `http` server in `--mcp-config`, still under `--strict-mcp-config`.
  - Codex: another `-c mcp_servers` entry with `required = false` and a 180 s start-up, so a failing server does not stop the turn.
  - pi, OpenCode and Kilo: one more URL in `AGENT_MCP_SERVERS`.
- **Templates.** Two presets ship beside the remote ones.
  - Playwright browser: `@playwright/mcp@0.0.83 --cdp-endpoint ${CLOUD_AGENT_BROWSER_CDP} --output-dir /workspace/.cloud-agent-artifacts/playwright-mcp`.
  - Chrome DevTools: `chrome-devtools-mcp@1.11.0 --wsEndpoint ${CLOUD_AGENT_BROWSER_CDP}`.

  The gateway replaces `${CLOUD_AGENT_BROWSER_CDP}` with the browser's URL from `cloud-agent-browser start` when the server starts. In the import step, a pinned `npx` or `uvx` server runs in each chat's sandbox by default. The user's own computer is an explicit choice.
- **Change and revocation.** The servers a launch was admitted with are part of what the member's CLI process is fixed to (`ResidentTransport.incompatible_fields`, field `mcp`). A server turned off, added or re-pinned makes the next turn start a new CLI that resumes the native session, and that launch replaces the slot's admission. A launch with no sandbox servers revokes the slot's admission. The host skips that exec only for a slot it has itself seen clean since it started, so a restarted host revokes once per slot before trusting it. This matters because the residency's MCP credential is deterministic, so a leftover admission would still open the gateway. An admission written before the container last started is ignored. A change never applies in the middle of a turn.
- **Authorization.** A sandbox server is not behind the relay's per-tool policies (allow, confirm, deny). Its power equals the agent's shell, and audit comes from the runtime's tool-call events. The UI says so, and the connection's test button explains that it is checked in the sandbox.
- **Results.** Image results go through `protocol_compat/tool_images.py`. The Playwright preset writes snapshots and screenshots under `CLOUD_AGENT_ARTIFACTS_DIR`.
- **Known limit.** Playwright MCP 0.0.83 attached with `--cdp-endpoint` never sends `Browser.setDownloadBehavior`, so a link it clicks does not download. This is the server's behavior, and a later preset version or flag can lift it.

### Live view and takeover (phase 3, not built)

The plan was a platform-owned browser supervisor that streams frames over CDP `Page.startScreencast` to the UI through an authenticated, session-scoped channel, and forwards user input during takeover while the agent's turn is paused. Recordings (screencast frames or a Playwright trace) would save to the artifacts directory.

## Security and isolation

The browser adds no new trust boundary: it runs as its own unprivileged user inside the same Kata VM and guarded network, and can do nothing the agent's shell cannot already do.

- **CDP is token-guarded on loopback.** Chromium never opens a TCP debug port itself. Only the launcher's WebSocket is on `127.0.0.1`. Its token is in `/run/agent-browser`, which only `agent-browser` writes and only the `agent` group reads (the members, all the group owner's). A member reads the endpoint only from a directory and file no other user could have written, so no member can plant an endpoint for the others. The path is compared in constant time, and the `Host` header must name loopback. No port is published from the sandbox.
- **Members share the browser, not each other's credentials.** No member owns the browser's processes or profile, and none can signal them. A member's own credentials stay private.
- **Clients are isolated from each other.** A client can send commands only on sessions it created, and events reach only the owning client. Pages are shared, so every client sees every tab.
- **Chromium's own sandbox** is off, with the reasons and the switch in Phase 0 findings.
- **Egress.** Browser traffic uses the sandbox's single guarded network and resolver. A page reaches only what the agent could reach with `curl`. Browsing adds no allowlist entries.
- **Untrusted page content.** Pages can carry prompt injection, the same risk as a fetched web page. Templates default to the task's `localhost` and allowed hosts.
- **Repository configs stay inert.** In-sandbox MCP servers are admitted only by the trusted host from the user's selection. `.mcp.json`, `.codex/config.toml` and similar repository files start nothing.
- **Credentials.** The profile holds only cookies created in the sandbox's tasks. User browser profiles are never imported. A profile lives as long as its sandbox's retained container.

## Session semantics

A browser belongs to one sandbox group: the chat that created the sandbox and every Fork that joined it. They share tabs, cookies and storage, as they share `/workspace`. Nothing is shared between sandboxes.

| Event | Browser process | Profile | Open tabs |
| --- | --- | --- | --- |
| New turn in the same chat | Reused if alive, else started on the first connection | Kept | Kept if the process survived |
| A turn ends | Untouched (the turn-end reap scans the member's UID only) | Kept | Kept |
| Idle past the reclaim timeout | Chromium stopped, the service stays | Kept | Lost |
| Chromium crashes | Clients disconnected, the next connection starts a new one | Kept | Lost |
| Sandbox stop or eviction | Killed with the sandbox. The service restarts with the next member launch | Kept (the browser user's home is in the retained container's writable layer) | Lost |
| Fork | Shared with the source chat | Shared | Shared |
| A member runs `stop` | Stopped for every member | Kept | Lost |
| Two tools or subagents | Shared | Shared | Shared. Each client has its own CDP sessions and should open its own tab |

- A Fork shares the browser because it continues the source chat's work in the same container, on the same files, for the same owner. A Fork that should start clean belongs in a new sandbox.
- Chromium's profile lock stops two browser processes from using one directory. It does not stop several CDP clients from interfering with each other's tabs, cookies or downloads. The launcher accepts any number of clients. Tabs are the tool's job, and templates open a new page per agent by default.

## Resources

A browser that is never started costs only image size.

| Limit | Why the browser stresses it | Mitigation |
| --- | --- | --- |
| 4 GiB memory (`memory_limit`, `sandbox_memory_mib`) | Chromium, a dev server and the agent CLI share one cgroup, and an OOM kill takes the whole sandbox | 174 MiB idle, 321 MiB with four pages. Launch on demand, idle reclaim, at most four renderers |
| `--pids-limit 512` (`chat_sandbox.py`) | The limit counts threads | About 140 threads idle, 213 with four pages. Raise the group limit if browser starts begin to fail on it |
| `/dev/shm` 64 MiB | Chromium renders through shared memory | `--disable-dev-shm-usage` |
| Image size | Chromium adds a layer | About 274 MB uncompressed, in its own cacheable layer |
| CPU limit | Rendering and screenshots compete with builds | Headless only |

Startup is paid once per sandbox per idle period, not per tool call, because the service reuses Chromium for every member.

## Evidence and observability

- **Screenshots and traces.** Tools write into `CLOUD_AGENT_ARTIFACTS_DIR`. The workspace file listing skips only `.git`, and `WorkspaceFiles.tsx` previews PNG, JPEG, GIF and WebP. `cloud-agent-browser start` creates the directory and adds it to `.git/info/exclude`, so it never enters a diff or a published branch. A turn also shows the images written there while it ran, and an image past the 512 KiB preview bound opens through its own image read (up to 10 MiB). The agent is told nothing: the platform finds them.
- **Inline images.** When a tool returns an image in a tool result, `protocol_compat/tool_images.py` moves it into a user message for runtimes that need that.
- **Operator signals.** The service logs Chromium's start, stop, crash and reclaim in the state directory, and the host logs a service that could not start.

## Acceptance

The baseline gate checks the platform, not which tool is best:

- At least two independent tools (for example Playwright CLI and agent-browser) connect to the managed browser through the contract.
- Each opens the task's own `localhost` app, completes a multi-step interaction and writes a screenshot to `CLOUD_AGENT_ARTIFACTS_DIR`.
- Isolation holds: a uid outside the sandbox group cannot read the token, a member cannot stop the service or plant an endpoint, and the browser survives a member's turn-end reap.
- Lifecycle holds: idle reclaim, crash relaunch, kill with the sandbox, no orphaned processes.

The image build runs the self-test as `agent-member-0` against a service run as `agent-browser`, including a download. Not yet run: the same on Kata through a real chat turn.

## Open questions

- Should a user be able to clear the sandbox's browser profile (sign out everywhere) from the UI?
- Should a new chat be able to opt into a saved login, and who approves and scopes it?
- How do agents learn the browser exists? Today they rely on `chromium` and `cloud-agent-browser` being on `PATH` and on the environment. A short platform instruction to every runtime is a possible follow-up.
- Which tool should the catalog mark as the default? The catalog marks none. The plan is to run 20 to 30 real Cloud Agent tasks (verify a UI change with a screenshot, reproduce a frontend bug, complete a form on a fixture app, check layout at two viewport sizes, read paginated data) across Playwright CLI, Playwright MCP, agent-browser, Chrome DevTools MCP and Browser Use CLI on Claude Code and Codex, and pick by pass rate, with cost and stability as tie-breakers.
- When does a full desktop (display server, non-browser apps) become worth its cost?
