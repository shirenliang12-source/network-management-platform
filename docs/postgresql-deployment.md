# PostgreSQL deployment (1.9.51 candidate)

This deployment does not move existing SQLite data. Do not point an existing installation at a blank PG database and expect its inventory/accounts to appear. Keep the original database, data directory and `.secret_key` backed up. An explicit data-transfer and verification procedure is required before switching an existing installation.

## New Docker deployment

Use `linux/docker-compose.pg.yml`. Set `POSTGRES_PASSWORD` and `NETMGR_DATABASE_URL` in a private environment file outside Git. The URL must be `postgresql+psycopg://netmgr:<URL-encoded-password>@postgres:5432/netmgr`. The password in the URL must match POSTGRES_PASSWORD; special characters must be percent-encoded. No production password is supplied by the repository.

Run from the project root: `docker compose -f linux/docker-compose.pg.yml --env-file /secure/netmgr.env up -d --build`.

The PG service is not published on a host TCP port. App and PG storage use separate persistent volumes. Never run `docker compose down -v` against an installation whose data must be preserved. Use one platform process/replica: scheduler and local config storage are not designed for an HA deployment.

The initial pair is PostgreSQL 15 plus Debian Bookworm's PG client. pg_dump must not be older than the database server. Do not change PostgreSQL major-version image tags in place on an existing data volume; use a separately planned PG upgrade.

## Windows

SQLite remains the default for existing installations. To use an external PG server, install PostgreSQL client tools (pg_dump) and expose them to the Windows service PATH. Configure `NETMGR_DATABASE_URL=postgresql+psycopg://...` in the service's environment, then restart only after backups and data-transfer verification. Browser login settings do not configure the service environment. The app installer does not install or overwrite a PostgreSQL server.

## Backups and restore

For an existing versioned PG database, startup creates a custom-format pg_dump snapshot under the app backup directory before migrations. Missing tools or backup failure stop the upgrade. Snapshots are not automatically pruned; monitor disk space and manage retention. Back up the data directory and encryption key as well: a PG dump alone does not include command settings, backup files or the key needed to decrypt credentials.

The UI's SQLite ZIP backup/restore is explicitly disabled in PG mode. Use pg_dump/pg_restore under administrator supervision and stop the application before restoring. Do not restore SQLite ZIPs into PG. PG health checks validate connection availability, not a full physical integrity audit.

## Verification

`.github/workflows/postgres-docker.yml` tests a disposable PG database: fresh Alembic migration, repeated import deduplication, restart snapshot, settings retention and restore guards. Only a successful PG job allows publishing a candidate GHCR image. Candidate tags include the exact Git commit; they are not production approval. Real device/DHCP connections are not exercised in CI.
