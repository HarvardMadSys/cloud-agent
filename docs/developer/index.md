# HybridInference Cloud Agent

Cloud Agent runs coding agents against a user's repository inside retained
sandbox containers. Users work from a web interface, while enrolled hosts run
Claude Code, Codex, pi, OpenCode, or Kilo. The control plane coordinates jobs
and sessions, and relays the agents' own pushes and pull requests to GitHub.
Accounts are its own: people sign up, apply through a waiting list and are
approved by an administrator. Every task runs on a model connection: one its
owner adds with their own provider key, or one the deployment provides through
its control-plane model relay.

This site documents the software: running it locally, operating a
deployment, how it works, and contributing. The source is at
[github.com/HarvardMadSys/cloud-agent](https://github.com/HarvardMadSys/cloud-agent),
under the MIT license; bugs and questions go to its
[issue tracker](https://github.com/HarvardMadSys/cloud-agent/issues).

## Where to start

| If you want to | Start here |
|---|---|
| Run the browser, API, hosts, and sandboxes on one machine | [Local Docker stack](local-stack.md) |
| Set up a checkout, find the code that owns a change, and run checks | [Developer guide](development.md) |
| Find the module that owns a responsibility and follow its dependencies | [Code structure](code-structure.md) |
| Change the owner web app, and see the rules and APIs behind each feature | [The owner web app](web-app.md) |
| Send a change | [Contributing](contributing.md) |
| Translate a page, or update a translation after an English edit | [Translating the docs](translations.md) |
| Configure credentials, enroll hosts, qualify Kata, deploy, and migrate | [Operations runbook](operations.md) |
| Upgrade an existing deployment and see what each migration changes | [Upgrade notes](upgrade-notes.md) |
| Look up a configuration name, default, or owning component | [Configuration reference](configuration.md) |
| Build release artifacts and deploy them in the required order | [Releases](releases.md) |
| Understand the sandbox trust model before deploying | [Security](security.md) |
| See the API this service serves, as its committed contract | [Changing an API](development.md#changing-an-api) |

## Scope of this site

These pages document the software, not any one installation of it. How end
users work with a particular deployment, which agents and models it offers,
and how to get an account there are the operator's to publish. Design records
in the repository are linked from these pages where they explain the current
design.

```{toctree}
:caption: Getting started
:maxdepth: 2

local-stack
development
code-structure
contributing
translations
```

```{toctree}
:caption: Concepts
:maxdepth: 2

context-recall
local-accounts
security
web-app
```

```{toctree}
:caption: Operations
:maxdepth: 2

operations
upgrade-notes
configuration
operations-compute
releases
deployment-reliability
disaster-recovery
runtime-settings
host-execution-limits
runner-lease-troubleshooting
smoke
```
