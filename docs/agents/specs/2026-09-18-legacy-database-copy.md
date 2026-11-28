# Convert a pre-baseline database copy

Status: implemented as `scripts/convert_legacy_database_copy.py`. Nobody has authorized a production cutover.

## Problem

The September 10 baseline rewrite assumed every old-chain database had been rebuilt. One production database was still at `0025_retained_followup_gate`, with jobs, messages, credentials and retained state. The rebuild procedure keeps only selected account and host state, so it cannot serve this database.

## Conversion contract

- **Only a separate copy.** The target is a restored database whose name starts with `agent_legacy_copy_`. The script needs its exact name and a dedicated DSN variable (`LEGACY_COPY_DATABASE_URL`) and never falls back to `AGENT_DATABASE_URL`. It refuses any other connection, active jobs, an unsupported revision and an already converted database. The default is a read-only audit.
- **Keep the old schema.** The whole source schema stays as `legacy_archive`. The script builds the public schema from the repository's real baseline, copies compatible rows, then applies the real additive migrations. It does not weaken constraints or fake an Alembic stamp.
- **Derive what the old rows lack.** Attempt thread IDs and membership owners come from their parents. Circular current-attempt pointers are restored after the attempts are inserted. Snapshot membership JSON expands into the new association table. Serial sequences are set past the historical maximum so new writes cannot collide.
- **Retired data stays in the archive.** Old `replay` telemetry lives there. The new continuity field is NULL for those attempts, because neither supported native mode describes them. Unknown mode values are refused.
- **Verify.** Every source row must still exist unchanged in the archive. The script compares live fields, explicit transformations, owners, credentials, relationships, content hashes, table counts and sequences. Tests cover valid and negative cases on PostgreSQL, including rollback on a constraint failure.
- **Namespace adoption is separate.** An operator binds the namespace after confirming the original Gateway issuer. The converter never guesses it or binds it automatically. A converted but unbound database cannot serve traffic.

## Rehearsal and cutover

Rehearse on a full production dump restored into a PostgreSQL container with no network and no published port. Attach no application or runner. After converting, run current-version queries against the copy and check that a fresh write allocates IDs beyond the historical ones.

At cutover:

1. Drain and stop the writers, then take a fresh consistent snapshot.
2. Repeat the rehearsed conversion into a new database.
3. Bind the verified issuer explicitly.
4. Point only the Agent service at the new database, and validate the service before admitting new work.

Keep the old live database and the full dump. Once new writes arrive, a rollback cannot discard them or point old code at the new schema, so a recovery has to account for the difference.
