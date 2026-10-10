"""PostgreSQL upgrade snapshots: fail closed and never log database credentials."""
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from sqlalchemy import inspect
from sqlalchemy.engine import make_url


def upgrade_snapshot(engine, database_url, directory):
    tables=inspect(engine).get_table_names()
    if not tables:
        return ''
    if 'alembic_version' not in tables:
        raise RuntimeError('PostgreSQL target is not empty and has no migration revision; refusing to modify it')
    executable=shutil.which('pg_dump')
    if not executable:
        raise RuntimeError('PostgreSQL startup requires pg_dump in PATH for an upgrade snapshot; install a client version not older than the server')
    url=make_url(database_url)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    output=directory / ('postgres-' + uuid.uuid4().hex + '.dump')
    environment=dict(os.environ)
    environment.update(PGHOST=url.host or 'localhost',PGPORT=str(url.port or 5432),PGDATABASE=url.database or '',PGUSER=url.username or '',PGPASSWORD=url.password or '',PGCONNECT_TIMEOUT='15')
    for key in ('sslmode','sslrootcert','sslcert','sslkey'):
        if key in url.query:environment['PG'+key.upper()]=url.query[key]
    try:
        result=subprocess.run([executable,'--format=custom','--no-password','--file',str(output)],env=environment,capture_output=True,timeout=600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not output.is_file() or output.stat().st_size==0:
            raise RuntimeError('pg_dump failed; upgrade blocked. Check client version, permissions, connection and free disk space')
    except (OSError,subprocess.TimeoutExpired):
        raise RuntimeError('PostgreSQL snapshot did not complete; upgrade blocked') from None
    return str(output)
