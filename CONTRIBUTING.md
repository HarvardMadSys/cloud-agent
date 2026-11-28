# Contributing

Read [AGENTS.md](AGENTS.md) first: it holds the repository's rules and security
boundaries. The [developer guide](docs/developer/development.md) covers local
setup, the checks each kind of change needs, and what CI runs.

1. Branch from `dev` as `<user>/<scope>/<name>`, and keep the change to one
   feature, fix or documentation update.
2. Cover the behavior you change with tests. Fix regressions instead of
   weakening assertions.
3. Update affected contracts, migrations and documentation in the same change.
   A schema change is a new additive Alembic migration; service startup never
   creates or alters the schema. Document new environment variables in
   [ENVIRONMENT.md](ENVIRONMENT.md).
4. Run `make check` and the other checks your change needs
   ([Run the relevant checks](docs/developer/development.md#run-the-relevant-checks)).
   Review the formatting and lint fixes it applies.
5. Open a pull request against `dev`. Describe the resulting behavior, and list
   the checks you ran and their results. The
   [pull request template](.github/pull_request_template.md) has the checklist.

Commit subjects and pull request titles use the conventional format,
`type(scope): summary`. Pull requests are squash merged, so the title becomes
the commit subject on `dev`; `main` advances through the manual
[fast-forward workflow](.github/workflows/sync-main.yml).

Treat every commit as public: never commit secrets, tokens or keys.
