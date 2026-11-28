# Backup and recovery

Back up the agent database, its encryption keys and retained host state
together, as one recovery set. A database dump cannot rebuild a native CLI
session whose Docker writable layer was lost. Restorable checkpoints in S3 cover
only groups that stopped cleanly with archives on, and CloudLab node rotation
only moves a source that is still available; neither replaces an off-node
backup. The deployment owner provides backup storage and scheduling; this guide
installs no backup service and claims no measured recovery times.

## Initial operating policy

Use these targets until the deployment owner records others and proves them
with a restore drill. Alert on failed or late backups and on failed integrity
checks: retention alone meets no objective.

| Recovery material | Frequency and retention | Initial recovery objective |
| --- | --- | --- |
| Agent Postgres database | Hourly encrypted dumps for 24 hours; daily copies for 30 days; monthly copies for 3 months | Database recovery point at most 1 hour old |
| Coordinated database and retained host state | Daily off-node recovery set for 7 days; weekly set for 4 weeks | Native-session recovery point at most 24 hours old |
| Configuration, encryption keys, host credentials, SSH trust, release locks and recipes | Capture with every recovery set and before every change; retain while any dependent backup/release is retained | Recover the exact keys and identities used by the selected set |
| Previous verified artifact release | Keep the current and previous successful release, plus every release referenced by retained recovery sets | Available without rebuilding or relying on mutable image tags |

The initial recovery-time target is 4 hours after choosing a usable set. Large
Docker stores can take longer, so measure copy and restore times and revise the
target. A restore that includes retained chats needs a coherent host and
database set, so hourly database dumps do not give a 1-hour native-session
objective. For a shorter database recovery point, use Postgres continuous WAL
archiving.

Keep encrypted backups off the machines and CloudLab allocation they protect.
Restrict access, keep decryption material apart from the backups, watch free
space, and verify hashes after transfer. Do not delete an encryption key or
registry image that a retained set still needs.

## What belongs in a recovery set

Record a set ID, start and end times (UTC), source host identities, source
commit, database revision, deployment protocol, Docker and storage-driver
versions, data-root paths, container inventory, file hashes and the release
manifest hash: identifiers only, never prompts, tokens or credentials. Preserve,
privately:

- **A full Postgres backup** of the agent database, including settings, the
  identity namespace, hosts, placement, jobs, attempts, connections and compute
  records; never just the conversation tables.
- **The deployment keys, apart from the database.** The `agent-keys` volume
  holds `AGENT_SETTINGS_ENCRYPTION_KEY` (or the original `AGENT_SESSION_SECRET`
  root), the account key and the database connection. Export them only to the
  encrypted backup storage:

  ```bash
  docker exec "${COMPOSE_PROJECT:?}-control-plane-1" python -m cloud_agent.deployment.deployment_keys export > keys.env
  ```

  On a replacement machine, that file used as the deployment's `.env` restores
  them at the first start. Also keep each deployment root's `.env`, where one
  exists, and mounted credential files: source-control and compute encryption
  keys, signing secrets, sandbox manager credentials and GitHub App keys. Keys
  held in saved settings still need the settings root key to decrypt, and
  generating new keys recovers nothing.
- **Each host's state**: the enrollment credential directory and deployment
  identity, `.deployed-sha`, readiness marker, namespace and project, tunnel SSH
  credentials and verified `known_hosts`, and runtime and storage
  configuration. The credential must match the restored host row's credential
  hash.
- **The complete retained Docker store and containerd image store**:
  `<disk>/docker` and `<disk>/containerd` in the supported bind layout, while
  `docker info` still reports `DockerRootDir=/var/lib/docker`. Copy both with
  Docker and containerd stopped, preserving ownership, modes, hard links,
  extended attributes and sparse files, and keep the matching database: a
  container filesystem export alone lacks the thread-to-host-to-container
  identities. A read-only Docker socket does not make raw overlay copying safe
  ([retained Docker storage](operations.md#retained-docker-storage-on-a-host)).
- **Release material** for the current and previous serving releases: the
  verified `release.json`, its pinned SHA-256, `recipe.tar.gz`, the extracted
  recipe, helper dependencies and image digests, with the digest-addressed
  images kept in the registry or a tested private archive.

Never put these backups in the repository or attach credential archives to
issues or CI logs.

## Capture a coordinated set

1. Record the deployment markers, placement, host status, container inventory
   and encryption-key version identifiers. In a maintenance window, stop new
   admissions and admin configuration changes, drain the hosts and wait for
   active attempts to finish. Note which hosts were already draining or
   disabled.
2. Stop the main API and MCP listener, which also stops compute reconciliation
   and background writers. Stop the retained sandbox containers and each host
   stack, then `docker.socket`, Docker and containerd. No source host may write
   while its set is captured.
3. With the listeners stopped, take a full database dump from the control-plane
   deployment root, with the generated Compose file and project selected. It
   writes no credentials to the output:

   ```bash
   umask 077
   export BACKUP_DIRECTORY=/path/on/encrypted-backup-storage/set-id
   mkdir -p "${BACKUP_DIRECTORY:?}"
   docker compose -p "${COMPOSE_PROJECT:?}" --env-file .env \
     -f "${COMPOSE_FILE:?}" exec -T postgres \
     sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
     > "${BACKUP_DIRECTORY}/agent.dump"
   ```

   It dumps `POSTGRES_DB`, the database `AGENT_POSTGRES_DB` names, which is the
   deployment's database only while `AGENT_DATABASE_URL` uses it too: confirm
   that `docker compose logs keys` for this project shows no `AGENT_POSTGRES_DB`
   warning. Capture the configuration and the stopped host storage into the same
   set with your encrypted backup tooling, transfer it off-node, record hashes,
   and check that the archive and dump are readable.
4. Start the daemons and both listeners, then the host stacks, and run the host
   checks; their live probes need the API and MCP listener. Verify identity,
   sandbox manager access and readiness, restore only the drain states you
   changed, and reopen admissions. A failed backup never justifies deleting or
   replacing the source state.

An hourly database-only dump can run while Postgres serves; `pg_dump` takes a
consistent snapshot. Label it database-only, and never pair it with a host
snapshot from another time as a coherent native-session restore.

## Restore and verify before reopening

1. Keep ingress closed and replacement host daemons stopped. Isolate recovered
   compute controllers from provider side effects until allocations are
   reconciled, because an old database can refer to expired allocations.
   Confirm the source hosts are stopped or fenced: two machines must never serve
   the same host credential and namespace.
2. Verify the set's hashes and decryption material, and restore into an isolated
   database first, with a compatible Postgres and the selected release. Restore
   the matching configuration and root keys before starting a listener. A
   settings root key in the environment that differs from the stored one stops
   the start, because the `keys` service never replaces a root. Only when the
   environment's key is the one the settings were saved with, delete the stored
   file first, using the Postgres image the deployment pins so nothing new is
   pulled:

   ```bash
   docker run --rm --entrypoint rm -v "${COMPOSE_PROJECT:?}_agent-keys:/keys" postgres:16.16-alpine@sha256:0adbf25a9e96d38823bb813f3ac0ed41fadb665c88f1df7fe6dbfb060cbdc106 /keys/AGENT_SETTINGS_ENCRYPTION_KEY
   ```

   A deployment that serves the restored database on the bundled `postgres`
   service sets `AGENT_POSTGRES_DB` to its name along with `AGENT_DATABASE_URL`.
   The database must be bound to the account system; a Gateway-era one is
   refused and stays an archive
   ([old databases](operations.md#preserving-an-old-chain-database)). Do not
   improvise an Alembic downgrade.
3. Restore the complete stopped host state with a compatible Docker and storage
   driver and the right mounts, checking the mounts before starting Docker. Keep
   the matching enrollment credential, namespace and container IDs. Start and
   inspect the daemon without admitting tasks; do not run generic prune or
   orphan cleanup to make mismatched state look empty.
4. Compare each retained group's database owner and container identity with the
   restored Docker labels, and reconcile stale attempts, leases and compute
   operations through their supported recovery paths. A missing container or a
   credential mismatch is a blocked recovery, not a sign that another host can
   recreate the session.
5. Run namespace verification and the Kata and candidate checks, then the phased
   deployment sequence, listeners before host live checks. Once those pass, set
   only hosts that were eligible for work back to `online`; the readiness fence
   needs an online host and reverse sandbox manager probes. This can resume work
   already queued in the restored database. With external ingress still closed,
   check with disposable conversations: sign-in, file access, retained
   continuation, separate Fork context, a queued turn, cancellation, a pull
   request the agent opens, and a host restart. Reopen admissions only after
   these checks and external health and queue monitoring pass.

Run an isolated restore drill quarterly and after changing storage topology, key
handling or the deployment protocol. Record the set and release IDs, checks,
elapsed time, lost-data window and failures, without secrets. A static test, a
readable dump, a manual node rotation or a backup hash is not a drill.

Moving retained sessions to another host needs a compatible full host-state
restore. The exception is a stopped group whose
[restorable checkpoint](operations.md#restoring-a-removed-hosts-sandboxes) is
in S3: once its host is removed from the pool, its node is confirmed terminated,
or the host moves out before its lease ends, another host restores it on the
group's next turn. If the source disk, its off-node copy and any checkpoint are
all lost, native context cannot be recovered from Postgres.

## Recover a previous artifact release

The host deployer is handed one release and does not restore an older manifest
by itself. Keep each previous verified release in its own immutable directory
outside `DEPLOY_ROOT`, with the reviewed lock, and never overwrite it with a
failed candidate.

Before rolling back, verify the previous manifest's pinned hash, the recipe
hash, registry digests, platforms and labels, and the deployment protocol. Check
the candidate's `compatibility.verified_rollback_to` against the previous
commit, and the previous release against the database now installed; empty
compatibility records prove nothing. An older recipe must not run migrations
backwards or cross the deployment-protocol fence. Database preparation runs
`upgrade head` from the selected recipe, and Alembic refuses unknown newer
revisions, so verify this against the current schema in isolation. If it is
refused, do not delete or stamp migration history; use an explicitly tested
compatible recipe or a full stopped-writer recovery.

For a compatible rollback, on **each** machine select its previously verified
directory and stable deployment root:

```bash
export AGENT_DEPLOY_MODE=artifacts
export RECOVERY_RELEASE=/opt/cloud-agent/releases/previous-verified-release
export AGENT_RELEASE_MANIFEST="${RECOVERY_RELEASE}/release.json"
export AGENT_DEPLOY_PYTHON=/opt/cloud-agent/deployer/bin/python
export DEPLOY_ROOT=/path/to/this-machines/persistent-deployment
```

Then run the [phased deployment sequence](releases.md#rolling-out-both-machines)
with `"${RECOVERY_RELEASE}/recipe/deploy/control-plane/deploy.sh"` on the
control-plane machine and `"${RECOVERY_RELEASE}/recipe/deploy/host/deploy.sh"`
on its execution host: prepare both, prepare the database, activate the host,
then activate the control plane. Every exact-generation and isolation check
still applies. Afterwards, read both `.deployed-sha` files and host readiness,
verify retained continuation and sandbox manager access before reopening
traffic, and record the incident as a failed candidate rollout even though the
previous release serves again.
