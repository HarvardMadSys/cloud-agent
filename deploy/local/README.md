# Complete local stack

Run the browser, control plane, runner and real Agent CLIs together on one
machine, without production accounts or credentials. The stack has the web
app behind a same-origin proxy, the account system, Postgres, host enrollment,
the sandbox manager, the sandbox image with pinned Claude Code, Codex, Pi,
OpenCode and Kilo CLIs, and a read-only `local/demo` Git repository on the
private control-plane network.

Accounts work as in production, and turns run on a model connection the
account adds with its own provider key; the stack ships no model. Sandboxes
reach public addresses only, so the provider must be public. Isolation is
plain Docker, for trusted development; public multi-tenant deployments need
Kata.

## Run

From the repository root, with Docker 28 or newer (for the route priority used
on sandbox networks) and Compose, `openssl`, `curl` and Python 3:

```bash
./deploy/local/dev.sh up
./deploy/local/dev.sh smoke
```

Give Docker Desktop at least 4 GiB of memory, 8 GiB when running turns; about
2 GiB can run out while Kilo starts. The first `up` builds every image, which
is slow, then migrates, starts the services and waits for readiness.

Open <http://127.0.0.1:13002/setup/administrator> and create the first
administrator with the one-time code from `./deploy/local/dev.sh logs
control-plane`. It signs in with a login name and needs no mail server. Email
sign-up stays closed until you set an email transport, or turn off email
verification, under Admin → Settings → Accounts.

`dev.sh` generates local credentials in the git-ignored
`deploy/local/.env.local` (mode `0600`). Keep it with the Postgres volume: if
the file is missing while the volume exists, the script refuses to generate
new credentials, and you must restore the original, especially
`AGENT_POSTGRES_PASSWORD` and `AGENT_DATABASE_URL`.

| Command | Use |
|---|---|
| `./deploy/local/dev.sh status` | List service state |
| `./deploy/local/dev.sh logs runner` | Follow a service's logs; omit the name for all |
| `./deploy/local/dev.sh config` | Validate the Compose configuration without printing secrets |
| `./deploy/local/dev.sh stop` | Stop the containers and keep their state, retained chat containers included |
| `./deploy/local/dev.sh start` | Check credentials, migrate and start existing services |
| `./deploy/local/dev.sh up` | Rebuild and reconcile after code or configuration changes |

## Ports and parallel stacks

`LOCAL_EDGE_PORT`, in `.env.local` or exported, changes the browser port. For a
second checkout on the same Docker engine, give it different values for all
of these, and use them on every `dev.sh` call:

- `LOCAL_PROJECT_NAME`, `LOCAL_SANDBOX_IMAGE`, `AGENT_NETWORK_NAME` and
  `LOCAL_RELAY_NETWORK`;
- `LOCAL_CHAT_NETWORK_POOL`, not overlapping the first stack's pool;
- `LOCAL_EDGE_PORT`, `AGENT_CONTROL_PLANE_PORT`, `AGENT_MCP_RELAY_PORT` and
  `AGENT_WEB_PORT`.

An existing `.env.local` names its sandbox image tag explicitly, so change it
when you change the project name. `up`, `start` and `smoke` refuse networks
another Compose project owns, and the host rules refuse overlapping pools; do
not accept Compose's network warning, or traffic can reach the wrong stack.

## Sandbox networking

Each retained sandbox group gets its own routable `freeinference-chat-net-*`
bridge, and the runner or sandbox manager installs and verifies this stack's
nftables rules before a sandbox joins it. The rules allow public IPv4 on TCP
80/443, DNS to `LOCAL_EGRESS_DNS_SERVER` on TCP/UDP 53, and the group's MCP
relay port. They block the engine's own addresses, private and special-purpose
ranges, other groups, other ports and IPv6, as in production. On first setup,
`dev.sh` records a non-loopback IPv4 resolver from the Docker engine (Colima's
`192.168.5.1`, for example), or `1.1.1.1`; change `LOCAL_EGRESS_DNS_SERVER` if
it does not work for you.

## Building through a proxy

On Docker Desktop, builds can reach a proxy on the host through
`host.docker.internal`. Prebuild with Docker's proxy arguments, then start
without building, using the same overrides for both commands:

```bash
./deploy/local/dev.sh build \
  --build-arg HTTP_PROXY=http://host.docker.internal:7890 \
  --build-arg HTTPS_PROXY=http://host.docker.internal:7890 \
  --build-arg NO_PROXY=localhost,127.0.0.1
./deploy/local/dev.sh up --no-build
```

Re-run `build` after code changes. The proxy applies to build downloads only,
not to sandboxes, and certificate and signature checks stay on. Python downloads allow 120 seconds of
inactivity; raise `PIP_DEFAULT_TIMEOUT` and the runner's `UV_HTTP_TIMEOUT`
build arguments on slow links. If a Debian mirror fails, pass
`--build-arg DEBIAN_MIRROR=http://mirrors.aliyun.com` (default
`http://deb.debian.org`); it changes only the mirror URLs, not the signed
keyring.

## What the smoke test covers

`smoke` runs first-run setup with the code from the control plane's log,
checks the session cookie and an administrator reading settings, checks that
the configuration reports `model_connection_required` and that a turn without
a connection gets 409, and signs out. On a stack that already has an
administrator it accepts `setup_complete` instead, and it fails while setup is
pending but the log shows no code.

It runs no turn: the stack has no model. To exercise the CLIs, add a model
connection for a public provider in the browser and start a task. The runner
takes two concurrent turns by default (`LOCAL_HOST_SLOTS`, at least 2 for
concurrent forks; the Servers page can change it live). The local idle
timeout is 15 seconds instead of production's 1800, and it never stops an
active turn.
