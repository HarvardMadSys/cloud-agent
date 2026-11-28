# Documentation

Start with the [project README](../README.md) for an overview and the local
quick start.

| Folder | What it holds |
|---|---|
| [developer/](developer/) | The maintained guides, published as the developer site. Start with the [developer guide](developer/development.md), the [code structure](developer/code-structure.md) and the [operations runbook](developer/operations.md). They describe current behavior and change with the code. |
| [agents/specs/](agents/specs/) | Design records: accepted designs that a maintained guide cites, and plans still being carried out. A record describes its design as of its date; the guides and the code describe what runs. |

A record is deleted once its work ships or is superseded and nothing maintained
cites it; git history keeps it. Verification and review evidence belongs in
pull requests. [AGENTS.md](../AGENTS.md#4-workflow) has the rules.
Repository-wide references stay at the root: [AGENTS.md](../AGENTS.md),
[CONTRIBUTING.md](../CONTRIBUTING.md), [ENVIRONMENT.md](../ENVIRONMENT.md) and
[SECURITY.md](../SECURITY.md).

Design records to read first:

- [Native sessions, Fork, and shared workspaces](agents/specs/2026-08-31-native-session-fork-shared-workspace-design.md)
- [Sandbox security model](agents/specs/2026-09-01-agent-sandbox-security-model.zh.md)
- [Public egress and the accepted owner-key exposure](agents/specs/2026-09-23-agent-public-egress-design.zh-CN.md)
- [Reusable cloud environments](agents/specs/2026-10-12-repository-environment-design.zh-CN.md)
- [Teams and team environments](agents/specs/2026-10-15-team-shared-workspace-design.zh-CN.md)
- [Compute resource lifecycle](agents/specs/2026-10-04-compute-resource-lifecycle-design.zh-CN.md)
- [CloudLab node rotation](agents/specs/2026-10-10-cloudlab-node-rotation-design.zh-CN.md)
