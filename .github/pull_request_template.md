## What and why

<!-- What changes, and what problem it solves. Link the issue, if there is one. -->

## Checklist

- [ ] `make check` is green, and so is every check this change needs
      (frontend, `make test-db-local`, `make docs-verify`).
- [ ] No test was weakened to make it pass.
- [ ] No secrets, tokens, keys, or internal hostnames in the diff. This
      repository is public.
- [ ] No `serving.*` import and no Gateway secret (`API_KEY_SECRET`,
      `JWT_SECRET_KEY`).
- [ ] New environment variables are documented in `ENVIRONMENT.md`.
- [ ] Database changes ship as an Alembic migration, not startup DDL.

## Verification

<!-- What you actually ran, and what it printed. Not what you expect to happen. -->
