# Releases

A Cloud Agent release is two files. `release.json`, the manifest, names one
source commit, every owned image by registry digest with its platforms, the
sandbox runtime catalog, the database revision, the deployment protocol and
the recorded compatibility checks. `recipe.tar.gz` holds the scripts and
Compose templates that install those images without a source checkout. A
downstream distribution pins the SHA-256 of the manifest's exact bytes in its
own reviewed lock; the manifest pins the recipe and images.

| File | Purpose |
|---|---|
| [artifact_catalog.json](artifact_catalog.json) | The single source for build cells, native platforms, web variants, runner labels and smoke keys |
| [manifest.schema.json](manifest.schema.json) | The published manifest schema that consumers validate against |
| [release_manifest.py](release_manifest.py) | Generates the manifest from build records |
| [generate_artifact_contracts.py](generate_artifact_contracts.py) | Projects the catalog into the schema and the publication matrix |
| [build_recipe.py](build_recipe.py) | Packages the explicit recipe file list into a deterministic archive |
| [release_env.py](release_env.py), [artifact_compose.py](artifact_compose.py) | Validate release configuration and render digest-bound deployment inputs |

After editing the catalog, regenerate and check its projections:

```bash
python3 deploy/release/generate_artifact_contracts.py --write
python3 deploy/release/generate_artifact_contracts.py --check
```

A change visible in the manifest (role, variant, base path, platform or cell
count) changes `manifest_contract.artifact_shape_sha256`. Review its
compatibility and schema impact, then accept it with
`--write --accept-contract-change`; plain `--write` refuses. A new catalog
cell still needs a Dockerfile, a real case in `smoke.py`, deployment
integration, and a contract fixture when compatibility changes.

## What the generator checks

The generator requires these five artifacts, with distinct digests and exactly
these platforms:

| Role | Variant | Platform |
|---|---|---|
| `control` | — | `linux/arm64` |
| `web` | `root`, base path `""` | `linux/arm64` |
| `runner` | — | `linux/amd64` |
| `sandbox` | — | `linux/amd64` |
| `tunnel` | — | `linux/amd64` |

The `agents` web variant (base path `/agents`) is no longer built, but the
schema still accepts it in v1 manifests (`manifest_contract.retired_web_variants`)
so that a rollback validates. New manifests name the source repository
`HarvardMadSys/cloud-agent`, and the generator accepts a `publisher_run_url`
only under that name. Releases published before 2026-11-10 carry the former
name, `HarvardMadSys/hybridInference-cloud-agent`, in `source_repository`,
`publisher_run_url` and the recipe URL; the schema accepts exactly these two
names.

Image references must be digests under this organization's registry. The
schema enforces role and web-variant cardinality, and the generator validates
its output against the schema before writing it. It also checks that each
runtime's `default` appears in its `versions`, which JSON Schema cannot
express, so consumers must check that themselves. `deploy_protocol` comes from
`deploy/DEPLOY_PROTOCOL`, `database_revision` from Alembic's script directory,
and `runtime_catalog` from the sandbox image's labels.

The generator does not query the registry. A downstream resolver must verify
that each digest exists, has the declared platforms, and carries the expected
`org.opencontainers.image.revision` and `io.hybridinference.artifact.role`
labels on each platform image.

## Producing one

[The publication workflow](../../.github/workflows/publish.yml) runs on pushes
to `dev`, on successful CI, and by manual dispatch for a `dev` commit with
successful CI. A push builds alongside CI, but publishes only after the newest
push CI run on that commit succeeds. The workflow:

1. Checks that the commit is on `dev`, and leaves an existing release
   unchanged.
2. Builds and pushes each artifact by digest with `SOURCE_COMMIT` set to the
   full commit SHA, pulls the digest back, smoke-tests it, and records its
   digest, platform and labels.
3. Assembles the five cell records, builds the recipe and writes the manifest.
4. Runs control-plane integration acceptance on those exact files.
5. Creates a draft release tagged `agent-<full-sha>`, uploads both files and
   makes it visible; a failed upload deletes the draft.

Failed or cancelled CI leaves built images in the registry without a
manifest. The workflow has no pull-request trigger, and it publishes
artifacts without deploying any environment.

To generate by hand from the repository root, put the five cell JSON files in
`cells/`, and set `SOURCE_COMMIT` to their full 40-character SHA and
`PUBLISHER_RUN_URL` to the publisher's Actions run URL:

```bash
mkdir -p release
uv run python -m deploy.release.publish_inputs assemble \
  --cells cells \
  --source-commit "${SOURCE_COMMIT:?Set the build commit}" \
  --output release/inputs.json
uv run python deploy/release/build_recipe.py \
  --output release/recipe.tar.gz --report
RECIPE_SHA256=$(sha256sum release/recipe.tar.gz | cut -d' ' -f1)
uv run python -m deploy.release.release_manifest \
  --inputs release/inputs.json \
  --recipe-url "https://github.com/HarvardMadSys/cloud-agent/releases/download/agent-${SOURCE_COMMIT}/recipe.tar.gz" \
  --recipe-sha256 "${RECIPE_SHA256}" \
  --publisher-run-url "${PUBLISHER_RUN_URL:?Set the publisher Actions run URL}" \
  --output release/release.json
```

`inputs.json` holds `source_commit`, `artifacts` and `runtime_labels`, the
sandbox's label map from `docker image inspect` (what the host reads when
stamping attempts). The manifest is written atomically, after every check
passes.

## Smoke tests

[smoke.py](smoke.py) starts each artifact and checks its identity labels. CI
and publication share it; publication runs it on the images pulled back from
the registry.

| Artifact | What starting it proves |
|---|---|
| Control | Migrates real Postgres with `alembic upgrade head`, then answers `/healthz` |
| Web | Serves `/login` and renders the runtime site identity |
| Runner | Imports its modules and has the Docker CLI and `nft` |
| Sandbox | Each Agent CLI reports the version its image labels declare |
| Tunnel | Runs its entrypoint and fails clearly for an unreachable peer |

The tests also check `org.opencontainers.image.revision`,
`io.hybridinference.artifact.role` and the web image's
`io.hybridinference.artifact.base_path`. Dockerfiles default the revision to
`unknown`, so publication must supply the commit. None of this proves that a
job runs or that Kata works on a host.

## Control-plane integration acceptance

[acceptance.py](acceptance.py) installs the candidate from an extracted
recipe, in a directory with no application source or Dockerfiles. It runs the
real control-plane deployer in artifact mode, pulls the candidate digests, and
compares the running containers with the manifest through
[accept_artifacts.py](accept_artifacts.py), by registry `RepoDigests` and role
labels.

| Acceptance establishes | Left to environment acceptance |
|---|---|
| Installation with no checkout and no image build | An execution host, Kata and real jobs |
| Registry pulls and the digest of each running container | Any other architecture |
| The read-only account-namespace check, then real Postgres migrations | Browser sign-in |
| Healthy API and MCP listeners and the right web labels | User-visible console behavior |
| Refusal of conflicting configured images before pulling | Registry validation by the downstream resolver |

There is no execution host, so the exact-commit host readiness fence is
expected to time out; the test asserts that the deploy reached it after every
earlier stage passed. Because the deployer starts `web` only after the fence,
acceptance then starts it with the generated Compose file and
`--no-build --pull never`. The workflow publishes the same bytes that passed.

## Registry layout

Deployments select images by the manifest's digests and read each image's role
from its label. Repository names and tags are storage conventions.

| Package | Contents |
|---|---|
| `ghcr.io/harvardmadsys/cloud-agent` | Control, web, runner and tunnel |
| `ghcr.io/harvardmadsys/cloud-agent-sandbox` | Sandbox, with its own visibility setting because it bundles third-party CLIs |

After its smoke test passes, each digest is also tagged, for example
`control-<sha>`, `web-root-<sha>` or `sandbox-<sha>`, to help people inspect
images. Tags such as `buildcache-sandbox-2026-w42` are the publication
workflow's layer cache, a new one each ISO week; unchanged layers keep their
digests, so a host on the previous release pulls only what changed. When
moving images between registries, check that the copy keeps the original
index digest: a recreated or converted index has a new digest and needs a
reviewed lock update. Keep old addresses until the rollback window closes.

## Deployment protocol

The protocol is `shared-live-exact-sha-v4` (`deploy/DEPLOY_PROTOCOL`). v4
introduced the
[single sandbox network](../../docs/agents/specs/2026-09-23-agent-public-egress-design.zh-CN.md),
removed the Squid `egress` artifact, and requires
`AGENT_EGRESS_CHAT_NETWORK_POOL` and `AGENT_EGRESS_DNS_SERVER` on every
runner host. A v3 deployer cannot install a v4 release, and rollback does not
cross the boundary. Runtime labels keep the `org.freeinference.runtime.*`
namespace; renaming them needs a separate dual-read migration.

## Deploying a recipe

Before replacing services, verify the manifest against the downstream lock,
the recipe's SHA-256 against the manifest, and the image digests. Extracting
`recipe.tar.gz` creates a `recipe/` directory of deployment helpers, examples
and Compose templates without `build:` entries.

Both machines need Bash, Docker with Compose v2, and Python 3.12 or newer. The
execution host also needs its Kata provisioning; deployment installs no
sandbox runtime. Install the helper dependencies on each machine:

```bash
python3.12 -m venv /opt/cloud-agent/deployer
/opt/cloud-agent/deployer/bin/python -m pip install \
  -r /opt/cloud-agent/release/recipe/deploy/release/requirements.txt
```

Keep `DEPLOY_ROOT` the same across releases: its optional `.env`,
`.deployed-sha` and `.shared-live-ready` describe the deployment, while each
recipe can be extracted anywhere. A new control plane needs no `.env`; its
first `prepare` phase starts the `keys` service, which generates its keys
([new installation](../../docs/developer/operations.md#admin-settings)). A
host's `.env` is the one server setup writes; a downstream deployer that
manages hosts itself writes the same variables
([host configuration](../../docs/developer/operations.md#host-configuration-reference)).
An existing installation keeps its configuration, Compose project name, host
enrollment root and Docker data root. Generated Compose files stay under
`DEPLOY_ROOT`, so relative mounts keep their data paths.
`AGENT_WEB_VARIANT` may be omitted or `root`; an `AGENT_WEB_BASE_PATH` with a
value is refused.

Set the artifact environment on the control-plane machine, and the same on the
execution host with its own root (`DEPLOY_ROOT=/srv/cloud-agent-host`). Use
absolute paths: `deploy.sh` changes into `DEPLOY_ROOT` before it reads
`AGENT_RELEASE_MANIFEST`.

```bash
export AGENT_DEPLOY_MODE=artifacts
export AGENT_RELEASE_MANIFEST=/opt/cloud-agent/release/release.json
export AGENT_DEPLOY_PYTHON=/opt/cloud-agent/deployer/bin/python
export DEPLOY_ROOT=/srv/cloud-agent-control
```

The manifest supplies the commit and image references, so remove old image
and commit overrides; a conflicting effective Compose value is refused before
anything is pulled. The default `source` mode still deploys from a checkout.

### Rolling out both machines

Run the phases in this order, with the environment above set on each machine:

1. Prepare both sides. They may run in parallel and replace no serving
   service.

   ```bash
   # Control-plane machine
   bash /opt/cloud-agent/release/recipe/deploy/control-plane/deploy.sh --phase prepare
   # Execution host
   bash /opt/cloud-agent/release/recipe/deploy/host/deploy.sh --phase prepare
   ```

2. Prepare the database on the control-plane machine. This migrates and checks
   the identity namespace; on a cold machine it also starts the API and MCP
   listener that the host readiness probes need.

   ```bash
   bash /opt/cloud-agent/release/recipe/deploy/control-plane/deploy.sh --phase prepare-database
   ```

3. Activate the execution host. It drains and replaces runners, checks the
   replacement, records the deployment and publishes its generation.

   ```bash
   bash /opt/cloud-agent/release/recipe/deploy/host/deploy.sh --phase activate
   ```

4. Activate the control plane. The readiness fence must pass before public
   services switch.

   ```bash
   bash /opt/cloud-agent/release/recipe/deploy/control-plane/deploy.sh --phase activate
   ```

Without `--phase`, a script runs all its phases, which cannot express this
cross-machine order. Phases can be retried: database preparation keeps track
of the listeners it started, and activation restarts any a failed attempt
stopped so they can accept the heartbeat the fence needs. If the other machine
fails and you abandon the rollout, remove the listeners started only for this
cold deployment; serving listeners are left alone:

```bash
bash /opt/cloud-agent/release/recipe/deploy/control-plane/deploy.sh --phase abandon
```

Image pulls and host Kata checks run before runners drain. A failed readiness
check keeps the replacement control-plane stack from activating. The script
tests run extracted recipes with Docker calls recorded and failures injected;
they do not prove a particular host, live Kata runtime or registry release, so
verify those when cutting over an environment.

## What a downstream deployment still owns

This repository publishes artifacts and deploys nothing. A consuming
distribution must:

- pin the exact manifest bytes in a reviewed lock and verify the schema,
  recipe hash, registry digests, image labels and platforms;
- check protocol compatibility and the manifest's `verified_upgrade_from` and
  `verified_rollback_to` records, where an empty list means the path was not
  verified;
- run preparation, Kata checks and environment acceptance on its own hosts;
- keep environment configuration, deployment records and recovery material
  outside this repository.

Keep previous verified manifests, recipes, hashes and digest-addressed images
apart from the deployment roots. An artifact-mode host rollout receives one
manifest and cannot select an older release by itself, and a previous release
is not automatically compatible with newer migrations. To roll back, follow
[recover a previous artifact release](../../docs/developer/disaster-recovery.md#recover-a-previous-artifact-release).
