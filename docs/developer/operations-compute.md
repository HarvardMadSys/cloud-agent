# Compute resources and the global sandbox location

Use Admin → Servers to deploy existing SSH machines or CloudLab allocations,
manage their leases, and choose where new sandboxes run. Operations and renewal
run in the control plane, so closing the browser does not stop them. The
[compute lifecycle design](../agents/specs/2026-10-04-compute-resource-lifecycle-design.zh-CN.md)
records the protocol; [ENVIRONMENT.md](../../ENVIRONMENT.md#optional-compute-resource-management)
lists the configuration.

The global sandbox location applies to **all new, still-unassigned sandbox
groups**. Retained groups, stopped ones and their Fork members included, keep
their Host. `Automatic` clears the restriction. A selected Host that is
unavailable keeps work waiting; nothing falls back to another Host.

## What stays on stable infrastructure

Keep the control plane, Postgres, background worker, deployment profiles,
provider credentials and the settings root key on stable infrastructure. A
managed resource runs only the Host, sandbox manager, tunnels and sandboxes.
Provider expiry is a hard limit; renewal does not guarantee uptime.

Stored API tokens and management SSH keys are encrypted with the compute
credential key (`AGENT_COMPUTE_SECRET_KEY`), a setting stored in the database
under the settings root key. Keep the settings root key with every database
backup: without it, compute connections and every other saved secret are lost.
Do not reuse account, settings, enrollment or sandbox manager keys. Compute
management credentials never enter sandboxes or the deployed Host.

The Host needs its own tunnel credentials and `AGENT_SANDBOX_MANAGER_TOKEN`,
which is pool-wide: every deployment profile uses the control plane's value.

## Supported initial deployment

Automatic SSH bootstrap supports:

- Ubuntu 22.04 or 24.04 on x86-64;
- Python 3, passwordless `sudo` and working KVM;
- reachable package repositories and the pinned Kata release, when it installs
  prerequisites.

With `install_prerequisites=true`, bootstrap installs `docker.io`,
`docker-compose-v2`, `curl`, `ca-certificates`, `xz-utils` and `nftables`,
starts Docker and runs the repository's checksum-pinned Kata installer. With it
false, Docker, Compose and the approved Kata runtime must already exist. Kata is
required either way.

The node needs no Cloud Agent preinstalled. Bootstrap uploads a reviewed source
bundle and configuration, then runs `deploy/host/sandbox_host.sh up` and
`check`, which verify Kata, the guest observer, sandbox manager, relays and
nftables. The Host takes no work until those checks, an enrolled heartbeat and a
control-plane-to-sandbox-manager health check pass. Automatic deployment handles
one node per allocation and refuses multi-node CloudLab experiments, which can
still be observed. A failed installation leaves the resource visible and intact.

### Preferred CloudLab node types

New CloudLab allocations try these types in order. They are general-purpose
families from 2021 or later with at least 16 physical cores, 128 GiB RAM and
more than 20 nodes, checked against the
[CloudLab hardware catalog](https://docs.cloudlab.us/hardware.html) and
[public inventory](https://www.cloudlab.us/portal-hardware.php) on October 12,
2026.

| Preference | Type | Introduced | Total nodes | Physical cores | RAM (GiB) |
|---|---|---|---|---|---|
| 1 | `r6615` | 2025 | 80 | 32 | 192 |
| 2 | `c6620` | 2025 | 132 | 28 | 128 |
| 3 | `r650` | 2022 | 32 | 72 | 256 |
| 4 | `r6525` | 2022 | 32 | 64 | 256 |
| 5 | `c6525-100g` | 2021 | 36 | 24 | 128 |
| 6 | `c6525-25g` | 2021 | 144 | 16 | 128 |

Change the list in **Admin → Settings → CloudLab**, or override it for one
server or autoscaling policy; each allocation saves the list it used. Free
counts are shown but do not reorder it. A confirmed capacity refusal moves to
the next type, an uncertain answer pauses acquisition for review, and when every
type lacks capacity the operation waits and retries from the top. A nonempty
`phystype` profile parameter selects only that type; clear it to use the list
again. Profiles with a different hardware parameter can use
`node_type_binding`; an empty list uses the profile's own choice.

### Sandbox storage path

When you add or create a server, enter **Sandbox storage path**: an existing,
writable data disk mounted on the server, for example `/mydata`. New CloudLab
servers start with `/mydata`, where the `small-lan` and Cloud Agent profiles
mount their data disk; change it if your template mounts the disk elsewhere.
Observation-only imports need no path.

Bootstrap keeps sandbox workspaces and native sessions in `<path>/docker` and
`<path>/containerd`, bound onto the daemons' standard `/var/lib/docker` and
`/var/lib/containerd`; a host installed by hand uses the same binds,
`/etc/fstab` lines and systemd drop-ins
([Retained Docker storage on a host](operations.md#retained-docker-storage-on-a-host)).
The path is saved with the allocation and reused on retries and replacement
nodes, which need the same mount. Preflight checks the disk without changing the
host, and deployment checks it again. Setup stops before changing anything when:

- the mount is missing, read-only or a symlink;
- a nonempty Docker or containerd store exists outside the selected mount, or a
  new host's `docker` and `containerd` directories on the disk are not empty (a
  retry may reuse them only when the deployment identity matches and both are
  already bound there);
- `/etc/fstab` has conflicting or duplicate entries for either daemon directory,
  or native systemd mount units, masks or overrides exist for them (ordinary
  `fstab`-generated units are fine);
- Docker or containerd is configured with a custom data directory.

Retained data with missing binds needs operator repair before a retry; bootstrap
never deletes or moves sandbox data. After a reboot, the daemons start only once
the disk and bind mounts are present. Older API clients that omit
`sandbox_storage_path` use the profile's `data_mount`.

## Prepare a trusted deployment profile

### Automatic setup

Open **Admin → Servers → Server setup** and use **Automatic setup**. The
control-plane image carries the Host source bundle for its own release. The
application supplies the revision, environment and sandbox manager token,
generates two tunnel keys per setup slot, and discovers the control-plane
listener ports and Docker bridge. Supply these once:

1. Add and validate a management SSH connection to the stable control-plane
   machine, with its independently verified host key.
2. Select it and confirm the machine's SSH address and user. The supported
   control-plane deployer supplies the node's FQDN; override it with
   `AGENT_CONTROL_PLANE_SSH_HOST` when SSH uses another address (other deployers
   must supply it). Saved and pending setups take precedence. The machine needs
   Python 3, passwordless `sudo` and the supported Docker Compose deployment.
3. Enter an approved private IPv4 pool for chat networks, a reachable DNS
   resolver outside it, and the protected infrastructure CIDRs. These depend on
   your network and need an administrator's decision.
4. Choose the number of setup slots and prepare the setups. Two slots give a
   current node and its replacement separate sandbox manager routes.

The application creates restricted SSH accounts on the control-plane machine:
the MCP credential reaches only the MCP-only listener, and the control
credential only the main API and its assigned reverse sandbox manager route.
Setup refuses SSH policies that would not permit these accounts rather than
broadening them.

Keys and configuration are encrypted in Postgres before remote authorization
starts. A setup becomes selectable once all its slots finish. If a request fails
or the browser closes, reopen Server setup and retry the pending setup; it reuses
its credentials and routes. **Start new setup** keeps the infrastructure inputs
and creates a new request, leaving an unfinished setup unavailable. A setup's
tunnel credentials, install directory and enrollment stay fixed.

A control-plane release rolls managed hosts forward once it runs. Stored
profiles that share the previous commit and bundle advance together with the
allocations and autoscaling reservations using them, so a later replacement or
pool member sees one revision. The deployment's readiness check does not wait
for these hosts; they take new chats again when they report the new commit. A
rollout that took the running generation down and then failed restores the
previous stored bundle. File-backed profiles are never rewritten.

A rollout waits while a Host carries a deployment drain fence. If the fence
stays 30 minutes on a Host off the control plane's commit,
`compute_host_release_blocked` alerts once per release. Release the fence from
the deployment that drained the Host, or override it with
`POST /admin/agent/runner-hosts/<host>/undrain?force=true`.

When the registry in `AGENT_COMPUTE_SANDBOX_IMAGE_REPOSITORY` (default
`ghcr.io/harvardmadsys/cloud-agent-sandbox`) holds `sandbox-<commit>` for this
release, the generated Host configuration pulls
`<repository>:sandbox-{deployment_sha}` instead of building, and a profile that
advances with a release pulls that release's image. A pulled image is recorded
by its digest, so another Host can restore the node's chats from their
checkpoints ([Restoring a removed host's sandboxes](operations.md#restoring-a-removed-hosts-sandboxes));
a built image keeps chats on their Host. The profile's
`automatic_setup.sandbox_image` says which (`published` or `built`). Stored
setups keep the environment they were created with: use **Start new setup** to
move existing slots to the published image. A setup that pulls fails on a later
release whose image was never published.

Automatic setup needs a control-plane image built with its full release
revision, plus the compute encryption key and the sandbox manager token, which a
new deployment generates. The form shows missing prerequisites; update older
images or use manual setup.

### Manual and file-backed setup

**Advanced manual setup** takes a reviewed source archive, its 40-character
revision and installation directory, the Host environment, the two tunnel keys
and verified known-hosts files. The control plane validates the archive and
encrypts every environment and credential value in Postgres; profile read APIs
expose only IDs and labels. Create a new profile to change tunnel credentials,
the install directory or the Host environment.

File-backed configuration remains for existing deployments and recovery. Start
from these templates:

- [`compute.example.json`](../../deploy/control-plane/compute.example.json)
- [`compute-host.env.example`](../../deploy/control-plane/compute-host.env.example)
- [Host field descriptions](operations.md#host-configuration-reference)

Copy them to a private directory outside Git and replace every example address,
token, network and all-zero digest or revision. Secret files must be readable by
the control-plane container's UID 10001 and not world-readable; mount the
directory read-only. The Servers form uses the first available profile in
configuration order, and API clients may name a profile ID. Requests cannot
supply scripts, source paths, Docker flags or environment values. Encrypted
compute connections are referenced by ID; mounted deployment secrets use the
separate `deployment:<name>` namespace, and deployment files cannot reference
compute connections.

Build the bundle from the reviewed commit the control plane lets Hosts
advertise; every component uses the same revision:

```sh
COMPUTE_DEPLOY_SHA=$(git rev-parse HEAD)
git archive --format=tar.gz --output=/private/compute/bundles/host.tar.gz "$COMPUTE_DEPLOY_SHA"
shasum -a 256 /private/compute/bundles/host.tar.gz
```

Set the profile fields as follows:

| Field | Value |
| --- | --- |
| `deployment_sha` | Full 40-character revision. |
| `source_sha256` | Printed SHA-256 digest. |
| `source_bundle` | Absolute bundle path visible inside the control-plane container. |
| `deploy_root` | Dedicated installation directory on the remote node. |

The digest is checked before upload. The archive must hold the reviewed
repository root, with no containing directory and no working-tree secrets.
Bootstrap rejects symlinks, path traversal and private deployment state, and
adds `.dockerignore` entries that keep tunnel files out of image builds. The
first installation writes a marker binding `deploy_root` to its allocation, node
and Host. Retries reuse the marker and the enrollment credential; a different
marker or an unmarked existing installation is refused. Bootstrap does not adopt
the files of manually enrolled Hosts.

Each live allocation reserves its profile, because the reverse sandbox manager
endpoint is fixed. Overlapping old and new Hosts need separate profiles with
different reverse ports, installation directories and tunnel authorizations;
renaming a profile does not separate it. Only a confirmed `terminated` or
`absent` allocation frees a profile. Refresh availability before retrying a
refused addition.

Choose `AGENT_EGRESS_CHAT_NETWORK_POOL`, the resolver and the protected CIDRs
for your network; the sample range is only an example. Keep the release's
per-group nftables policy. Environment values are single-line dotenv data,
never sourced as shell. The supported substitutions are `{deploy_root}`,
`{host}`, `{node_id}` and `{allocation_id}`.

### Two tunnels and the sandbox manager route

Authorize the tunnels on the stable control-plane machine before adding a
server; bootstrap cannot grant that access. Use two distinct keys with no shell
and narrowly permitted forwarding. There is no inference tunnel: model calls go
to each owner's provider over public HTTPS.

| Tunnel | Remote destination | Audience |
| --- | --- | --- |
| MCP | Control plane's **MCP-only** listener, default `127.0.0.1:8001` | Sandbox tool relay |
| Host control | Main API, default `127.0.0.1:8000` | Trusted runner only |

The Host control connection also reverse-forwards from the control-plane
machine to `sandbox-manager:8092`. Bind that listener to the control-plane
Docker bridge gateway address, never loopback or `0.0.0.0`. Configure
`GatewayPorts clientspecified` and restrict the key's reverse listen address,
port and forward destination; never use `yes`, which binds every remote forward
to the wildcard address. Firewall the reverse ports so they answer only on the
Docker bridge
([sandbox manager route](operations.md#the-way-back-the-control-plane-reaching-this-hosts-sandbox-manager)).
Set `AGENT_SANDBOX_MANAGER_ADVERTISE_URL` to the resulting URL as the
control-plane container reaches it, add its host to
`AGENT_SANDBOX_MANAGER_URL_ALLOWED_HOSTS` when that allowlist is on, and give
every simultaneously deployed Host its own reverse port.

Verify server and tunnel host keys through a trusted source before adding them
to `known_hosts`; `ssh-keyscan` finds a candidate key but does not authenticate
it. These transports use strict host-key checking, isolated keys, no ambient SSH
configuration or agent, and no trust on first use.

CloudLab VMs use the manifest's SSH port, and by default the control plane
connects without verifying the VM's host key. That allows automatic setup but
leaves the connection open to impersonation of the node. Select **SSH host-key
checking → Registered by the node at first boot** when the Cloud Agent profile
is available and the host identity must be pinned. The previous release cannot
use this setting: after a rollback, observation, lease and provider operations
still work, but bootstrap, inventory or retirement that needs SSH waits until
this release is back. Redeploy forward rather than replacing the marker with
unverified keys.

### CloudLab nodes that register their own host keys

Automatic replacement needs **SSH host-key checking → Registered by the node at
first boot**, in the CloudLab server's advanced options. It works only for
experiments this control plane creates from the Cloud Agent profile:

1. Publish [`deploy/cloudlab/cloud-agent-node.py`](../../deploy/cloudlab/cloud-agent-node.py)
   as a **project-private** CloudLab profile and set its project and name as
   the server template. Its node, image and data-disk parameters replace
   `small-lan`'s `phystype`, `osImage`, `dataMount` and `dataSize`.
2. Provide a public HTTPS control-plane address through `AGENT_PUBLIC_API_URL`
   or `AGENT_COMPUTE_REGISTRATION_URL`.
3. Each acquisition passes a one-time token in the experiment parameters; the
   control plane stores only its hash, and it expires after two hours. At first
   boot the node generates fresh SSH host keys and posts their public halves to
   `/v1/compute/node-registrations` over HTTPS. The control plane pins the
   strongest key and checks strictly against it alone.

The token registers keys once; it cannot claim work, enroll or rename a Host.
Repeating the same keys succeeds, different keys are refused, and a resubmitted
acquisition's newer token replaces the old one. CloudLab project members can
read experiment parameters, the token included, and already administer every
project node, so keep the profile private and the project membership small.

If an operation stays at `waiting_for_registration` and ends with
`registration_expired`, inspect `/var/lib/cloud-agent-node/registration`
through the CloudLab console and check that the node can reach the registration
URL. After fixing the cause, retry the operation, which gives the token another
two hours, and restart the node from CloudLab so its boot script posts again; it
stops trying after an hour or a refusal. Alternatively, release the server:
nothing ran on a node that never registered, so release needs no SSH inventory.
`registration_unavailable` means no public HTTPS control-plane address is
configured.

## Enable legacy file-backed profiles

Admin-managed profiles need no compute directory or JSON configuration.
CloudLab deployments still set the Portal API URL; existing-SSH deployments do
not. To keep mounting profiles and credential files, set these in the private
deployment environment:

```text
AGENT_COMPUTE_DIRECTORY=/private/compute
AGENT_CLOUDLAB_API_URL=<operator-verified HTTPS Portal API base URL>
```

Put `compute.json`, `bundles/host.tar.gz` and `secrets/` under that directory.
`deploy/control-plane/deploy.sh` then adds `docker-compose.compute.yml`, which
mounts the directory read-only at `/run/compute` and sets
`AGENT_COMPUTE_CONFIG=/run/compute/compute.json` on the main API; the MCP-only
listener gets neither. Manual Compose commands must add
`-f deploy/control-plane/docker-compose.compute.yml`, and other deployment
systems can set `AGENT_COMPUTE_CONFIG` with an equivalent read-only mount. The
image includes `openssh-client`; the control plane gets no Docker socket or
root. Apply database migrations before starting the worker.

CloudLab has no default endpoint. Set the **CloudLab API URL** setting (or
`AGENT_CLOUDLAB_API_URL`) to the production HTTPS Portal API,
`https://boss.emulab.net:43794`, as CloudLab's
[portal-api README](https://gitlab.flux.utah.edu/emulab/portal-api) gives it,
then run **Connections → Validate**. The portal's web address,
`www.cloudlab.us`, does not serve the API: its port 43794 is filtered, so a URL
naming it waits out the 60-second connect timeout and reports that CloudLab
could not be reached. Validation makes a read-only experiment-list request,
checks the response shape and refuses redirects. See the
[Portal API manual](https://docs.cloudlab.us/advanced-topics.html) and its
[OpenAPI source](https://gitlab.flux.utah.edu/emulab/portal-api/-/blob/master/openapi.json).

## Day-to-day workflow

In **Admin → Settings → CloudLab**, set the project, initial duration, template
project and name, SSH user, and the SSH public key installed for that user.
These are non-secret defaults an administrator can change per allocation; leave
the key blank to use the keys already on the CloudLab account.

1. In **Admin → Servers**, choose **Add server**, then existing SSH or
   CloudLab.
2. Create or select credentials. Existing SSH needs a private key and verified
   host keys. CloudLab needs an API token and a management SSH private key; its
   advanced options can replace the unchecked host key with first-boot
   registration.
3. Enter the details: for existing SSH, an address, port and user; for
   CloudLab, an experiment UUID to attach, or a profile and project to
   provision, plus its SSH user. A public key set for that user is added to a
   new experiment.
4. Submit and follow the operation until it is ready. HTTP 202 only means the
   operation was recorded; allocation, installation, enrollment and readiness
   follow.
5. Optionally select the ready Host as the **global sandbox location**.
   Readiness does not change the saved location.
6. Before enabling renewal, review the allocation's full scope. Attached or
   imported resources need explicit lifecycle adoption, a real reason and
   ordered lead times. Saving a policy does not extend a lease; confirm
   extensions from the observed expiry.

Observation-only imports record facts without installing anything. Once adopted
they can support lease management, but observation does not prove their
retained state is disposable: release still needs active Host bindings and a
verified inventory.

## Recovery and release boundaries

An operation persists its request ID, parameters, step and resource ID, so
retry the same operation after a browser disconnect or worker restart; a new
request ID creates a new command. **Activity**, and each server's **Activity**
tab, list the 200 most recent operations plus every one still pending, running,
waiting or `unknown`, and any failed or blocked acquisition not confirmed gone.

| Result | Recovery |
| --- | --- |
| CloudLab could not be reached | The message names the endpoint and the reason. An unresolved name, a refused connection or a 60-second connect timeout points at the **CloudLab API URL** (`https://boss.emulab.net:43794`) or a firewall in between; a reset connection usually means a proxy; a failed TLS handshake means the URL names a port that is not HTTPS. An expired certificate is CloudLab's to replace; report it. The operation retries every 30 seconds; **Connections → Validate** checks at once. |
| SSH key or identity refused | Fix the machine or key, update the connection and retry the same operation. Host-key checks stay on. |
| Cloud create outcome unknown, no UUID saved | Find the experiment in the account and project and resolve it explicitly; the worker never repeats the POST or adopts an experiment by name. If the portal has none, **Nothing was created** cancels the operation for good and the server can be removed. That is your statement, not checked with the provider, so check the portal first. It is refused once anything names an experiment: a registered node, a saved UUID, node or Host, or a provider diagnostic. Autoscaled acquisitions cancel on their own. |
| UUID saved, later observation failed | Repair access and resume. Releasing the server deletes that experiment by its UUID. |
| Installation or readiness failed | Check the deployment checks, fix the prerequisites or profile and retry; the resource is not destroyed. |
| Extension pending or unknown | Check the real expiry. Retries keep the fixed target; a shorter extension is not success, and the provider may refuse or limit it. An expiry unchanged ten minutes after the request blocks as `extension_unconfirmed`, so other operations and a replacement can proceed; retry sends the same target. |
| Retained state remains | Keep the resource available or manage its chat lifecycle; stopped containers and archived chats count. |
| Release committed, response unknown | Keep admission closed and confirm the exact resource ID; cancellation cannot undo a release. |

A CloudLab DELETE answering `204` confirms deletion of that experiment UUID, and
so does a terminal experiment object with the same UUID and project. A `404`
alone does not, because token visibility can change: the adapter reports
`resource_visibility_unknown` and keeps the reservation, and a lost DELETE
acknowledgement followed by `404` stays pending until the provider confirms.
Restore access and retry; never edit the database to infer deletion. A missing
experiment counts as `expired_not_found` only ten minutes past a recorded lease
deadline. Renewal continues while retirement waits on retained state, and does
not clear a manual drain or resource-owned admission holds. The claim
transaction enforces the last known lease cutoff even if the resource worker
stops.

| Action | Effect |
| --- | --- |
| `Remove from pool` | Revokes the Host credential and leaves the machine intact. If another Host can take its sandboxes, the Host first moves them out as [before a lease ends](#moving-sandboxes-out-before-a-lease-ends): it shows `removing`, takes no new work, stops each idle sandbox into a checkpoint for handover, and is revoked once none is left and its turns have finished. Stopped sandboxes covered by a checkpoint move when it is revoked. Before leaving any sandbox behind, because no Host can take it or it can never move, the page asks; removing anyway leaves it on the Host until the Host returns. |
| `Cancel removal` | Returns a `removing` Host to work; moved sandboxes stay where they are. |
| `Remove now` | Revokes a `removing` Host at once. Its running turns are requeued once, or marked lost if they may already have run; unmoved sandboxes stay on it. |
| `Forget` | Removes an eligible history record. A `cold` sandbox, waiting in its checkpoint for any Host, does not block it. |
| `Retire/release` | Releases the whole allocation (for CloudLab, the experiment) and cancels any acquisition or queued upgrade still waiting on it. An autoscaled server's profile returns to the pool once the release is confirmed. Existing SSH has no cloud-destruction capability. |
| `Remove server` | Deletes the server's records once its resource is released, was never created, or (attached without release) its Hosts are disabled; it calls no provider. An operation still running, confirming a provider call or `unknown` keeps them ([Removing a server](operations.md#removing-a-server)). |

A Host held by a deployment drain shows `updating`; one drained through the
`/drain` API shows `paused` and offers `Resume new work`. There is no separate
pause action.

Release requires complete scope, no global placement reference, no running,
in-flight or retained dependencies, and a fresh inventory of the sandbox manager
and the machine; there is no force. Stopped or unknown containers, orphan
networks or volumes, an offline sandbox manager, and any file under the Host's
state directory other than its enrollment credential, checkpoint receipts and
confirmed removal receipts block it. Only a CloudLab node whose host-key
registration never completed needs no inventory.

Before the inventory, release removes the stopped containers left by groups that
moved away (handed over through a checkpoint, restored elsewhere or deleted),
retrying each until the Host confirms, and counts them in `superseded_removed`.
It then stops each node's runner with `docker stop`, removes the runner's
leftover self-test sandboxes, and keeps the findings under `inventory`; the
sandbox manager keeps running. It retries only when Docker could not be read and
nothing else was found; any finding stops it for attention.

## Moving sandboxes out before a lease ends

A Host whose allocation does not use automatic replacement moves its sandbox
groups to other Hosts before its lease ends, one at a time, through their
[restorable checkpoints](operations.md#restoring-a-removed-hosts-sandboxes).
The lead is **Move sandboxes to other servers before expiry** in the
allocation's renewal settings: 120 minutes by default, 0 to turn it off. With
renewal on, extension is tried 15 minutes before the window opens, so only a
lease that could not be extended starts a move; the window ends 25 minutes
before the extension horizon.

Inside the window the Host gets a `move_out` admission hold only when its groups
can move:

- lifecycle archives and checkpoints are on, and every Host of the allocation
  advertises `sandbox-checkpoints-v1` and has applied the current archive
  revision;
- another online Host that restores and archives on that revision would pass
  admission for new groups now. A Host under a `new_groups` hold, in its own
  lease window or with a stale observation does not count, and of two Hosts
  entering their windows together only one moves out. An `untrusted-kata-v1`
  group also needs a target that runs Kata.

Without a target nothing changes, and the Host serves until its lease ends. A
started move continues if its target goes away, and stops if checkpoints do.

During the hold the Host claims nothing. Every 15 seconds the control plane
stops each idle group as an administrator would; a group with a running turn,
operation or open terminal waits for a later pass. The stop stores a final
checkpoint, after which the group is `cold` and its next turn is restored on
another Host; the user sees that message wait a little longer. A sandbox whose
deletion failed before its archive moves like any other; a pending or verified
deletion is left to finish.

In an autoscaled CloudLab pool, a moving-out member stops counting toward
`min_hosts`, so its replacement is acquired while it still serves. It still
counts toward `max_hosts`: keep `max_hosts` at least one above `min_hosts`, with
a spare deployment profile, or the replacement waits. The Servers page shows the
member as _moving out_, and it is released at its lease end.

| Alert | When |
| --- | --- |
| `compute_move_out_blocked` | Once, when the window opens with groups that would stay. The reason is `archives_disabled`, `checkpoints_disabled`, `host_offline`, `host_without_checkpoints` (the Host's release predates checkpoints), `archive_settings_not_applied` or `no_restore_target`, or the number of groups that can never move (no checkpoint, or an image no other Host can pull). |
| `compute_move_out_unfinished` | Once, if groups are still unmoved five minutes before the lease ends, or the claim-stop lead if longer. |
| `compute_groups_lost_with_server` | When the provider confirms the machine `terminated`. Covered stopped groups become `cold`; every other group lost its only copy. Once the Host has been silent 10 minutes with no turn, claim or Fork in flight, those groups and their chats become LOST and their queued turns fail. A group being deleted without an archive is deleted instead. The Host row stays for **Remove from pool**. |

## Automatic CloudLab node replacement

Replacement moves one whole Host, stopped sandboxes and native sessions
included, keeping the Host name and sandbox namespace and enrolling a fresh
credential. It does not rebuild context from transcripts. Before enabling it:

1. Use a **provisioned, managed** single-Host CloudLab allocation with
   `host_key_source=registration` and the private Cloud Agent profile.
2. Prepare two alternating deployment profiles with distinct tunnel keys and
   reverse sandbox manager ports, pinning the same `deployment_sha` and
   `source_sha256`. Replacement never picks a profile from another release, and
   a host rollout advances both. Keep Docker and containerd versions compatible,
   and set `data_mount` to the local disk, for example `/mydata`.
3. Enable renewal and replacement in Servers → renewal settings, with the
   renewal reason and hardware types in order. The defaults are a **180-minute
   replacement lead time** and a **7-day maximum node age**. Saving the policy
   does not start a move.
4. Set `AGENT_ALERT_WEBHOOK_URL` for external alerts; without it, events stay in
   the logs and operation status.

In the renewal window, extension is tried first. A refused or unconfirmed
extension starts replacement, and a node at maximum age moves instead of
extending while the move is on time. A move unfinished at its drain deadline, or
one that blocked, failed or was cancelled, keeps the old node extended. Failed
candidates are released before the next type is tried. Configuration or
credential errors stop at once, and the same failure on three types stops with
an alert. A capacity refusal before any resource is named releases the
candidate at once instead of after the two-hour candidate limit; rate limits
and unknown outcomes never count as capacity refusals. Retry resumes the saved
operation.

The candidate installs the host recipe and takes a live pre-copy without
enrolling or claiming; the source then waits for an idle moment. At the drain
deadline, 100 minutes before expiry with the default one-hour turn limit, new
claims pause even if the candidate is still preparing. Running turns finish and
new messages queue; no move kills an active turn. The worker then stops the
runner, then the sandbox manager for up to 10 minutes (it stops each sandbox
cleanly and, with lifecycle archives on, stores its final archive), then the
remaining containers and daemons. It copies the final changes, transfers the
database binding, and starts and verifies the replacement. Claims resume only
after the takeover is recorded.

Before the takeover, a failure restores the source. If the replacement cannot
be confirmed stopped, claims stay paused and the operation blocks. A source
restart that loses SSH or times out is retried for ten minutes; one that still
fails, or any unexpected error after the source began stopping, blocks with the
urgent `compute_rotation_failed` alert and keeps claims paused. An unexpected
error before the stop blocks and resumes claims. Cancellation works until the
stop begins. Cleanup failures retry without interrupting the replacement, and
releasing the source still passes the retained-state and allocation guards.

### Acceptance before enabling production traffic

Use disposable staging conversations and dedicated profiles. Record operation
IDs, node identities, the release revision and the observed pause, without
credentials.

- Create a node through the deployed registration endpoint and check that SSH
  uses its pinned key.
- Run Claude Code, a Fork and Codex with files and distinct context markers,
  use **Replace node now**, and check native continuation, Fork separation,
  files and container IDs. Repeat across CloudLab sites.
- Send a message while claims are paused; it must queue and run once after
  takeover, and the source must no longer authenticate.
- Exercise refused renewal, an unavailable first hardware type, a failed final
  copy, a failed target start and a control-plane restart. Check rollback or
  continuation of saved steps, release of failed candidates, and that no
  allocation or serving copy is duplicated.
- Run the policy for **two weeks with at least two automatic moves**, checking
  alerts, cleanup and retained chats.

The [2026-10-10 manual staging drill](../agents/specs/2026-10-10-cloudlab-node-rotation-design.zh-CN.md#验证二结果2026-10-10staging)
moved real sessions from Clemson to Utah with a 161-second pause. Losing the only
source disk, or having no replacement capacity before expiry, can still lose
sessions; keep off-node backups where required. Before live traffic, verify the
CloudLab profile end to end: deployment, a real task, retained continuation and
Fork on one Host. Renewal success and unit tests, which mock HTTP and SSH, do
not prove the profile's virtualization or network suitability.
