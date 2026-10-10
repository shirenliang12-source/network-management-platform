"""Windows managed PG provisioning. Never initializes an existing cluster."""
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import uuid
from app.services.managed_runtime import protect_directory, read_runtime, write_runtime

SERVICE = 'CiscoNetworkManagerPG'
PG_EXECUTABLES = ('initdb.exe', 'postgres.exe', 'pg_ctl.exe', 'pg_dump.exe')


class PGSetupError(RuntimeError):
    """Safe diagnostic text: never includes subprocess output or credentials."""
    exit_code = 1


class PGExecutionBlocked(PGSetupError):
    pass


class PGPreflightBlocked(PGSetupError):
    # Only preflight can offer a no-migration SQLite continuation.
    exit_code = 20


def _command(args, env=None, timeout=120):
    try:
        result = subprocess.run([str(v) for v in args], env=env, capture_output=True,
                                timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise PGSetupError('Database setup command failed: ' + Path(str(args[0])).name + f' (exit {result.returncode})')
    except subprocess.TimeoutExpired:
        raise PGSetupError('Database setup command timed out: ' + Path(str(args[0])).name) from None
    except OSError as exc:
        code = getattr(exc, 'winerror', None)
        if code in (4551, 577, 1260):
            raise PGExecutionBlocked(f'Windows application-control/signature policy blocked {Path(str(args[0])).name} (Windows error {code}). Security policy was not changed.') from None
        raise PGSetupError(f'Database setup could not execute {Path(str(args[0])).name}; Windows error {code}') from None


def preflight_binaries(pg_bin):
    """Version-only probes: no cluster, passwords, service or database writes.

    This checks basic executable loading, not every DLL/runtime policy decision.
    Do not retry a rejected process, copy binaries elsewhere or alter policy.
    """
    for name in PG_EXECUTABLES:
        if not (pg_bin / name).is_file():
            raise PGSetupError('Incomplete PostgreSQL distribution: ' + name)
    try:
        for name in PG_EXECUTABLES:
            _command([pg_bin / name, '--version'], timeout=20)
    except PGExecutionBlocked as exc:
        raise PGPreflightBlocked(str(exc) + ' PostgreSQL migration has not started; SQLite and configuration are unchanged. Use an administrator-approved PostgreSQL distribution.') from None


def _archive_uninitialized_recovery(pg_root):
    """Caller has ruled out an existing PG service; preserve legacy credentials.

    Never touch even an empty/partial cluster: it requires manual inspection.
    """
    if (pg_root / '15' / 'data').exists():
        raise PGSetupError('Existing PG cluster/partial initialization retained; manual recovery required')
    recovery = pg_root / 'recovery.json'
    if recovery.is_symlink():
        raise PGSetupError('Recovery file is a link; manual inspection required')
    if recovery.exists():
        if not recovery.is_file():
            raise PGSetupError('Invalid recovery file; manual inspection required')
        # Same protected directory, unique destination, no credential contents logged.
        recovery.rename(pg_root / ('recovery-uninitialized-' + uuid.uuid4().hex + '.json'))


def _prepare_database(url, scratch, secret):
    env = dict(os.environ)
    env.update(NETMGR_DATABASE_URL=url, NETMGR_SECRET_KEY=secret, CISCO_NM_DATA_DIR=str(scratch))
    args = [sys.executable]
    if not getattr(sys, 'frozen', False):
        args.append(str(Path(__file__).resolve().parents[2] / 'run.py'))
    _command([*args, '--data-dir', scratch, '--prepare-database'], env=env, timeout=600)


def install_builtin(data_dir, pg_bin, pg_root, secret, port=54329):
    """App must be stopped and its data-directory lock held by the caller."""
    if os.name != 'nt':
        raise RuntimeError('Built-in service provisioning is Windows-only')
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL, make_url
    from app.config import settings
    import psycopg
    from psycopg import sql
    from app.services.sqlite_to_postgres import copy_verified_snapshot
    data_dir, pg_bin, pg_root = map(lambda p: Path(p).resolve(), (data_dir, pg_bin, pg_root))
    # ProgramData is local and survives application uninstall; never accept UNC.
    approved_root = (Path(os.environ['PROGRAMDATA']) / 'CiscoNetworkManager' / 'postgresql').resolve()
    if pg_root != approved_root or str(pg_root).startswith('\\\\'):
        raise ValueError('PostgreSQL data must use the dedicated local ProgramData directory')
    for executable in PG_EXECUTABLES:
        if not (pg_bin / executable).is_file():
            raise ValueError('Incomplete bundled PostgreSQL distribution')
    values = read_runtime(data_dir)
    if values.get('DATABASE_URL'):
        if values.get('PG_BIN_DIR') != str(pg_bin):
            raise ValueError('An external or different PG installation is configured; automatic replacement refused')
        with create_engine(values['DATABASE_URL']).connect() as connection:
            connection.exec_driver_sql('SELECT 1')
        return values
    active_url = make_url(settings.DATABASE_URL)
    if os.environ.get('NETMGR_DATABASE_URL'):
        raise ValueError('Explicit service DATABASE_URL requires administrator reconciliation before built-in migration')
    if active_url.get_backend_name() != 'sqlite' or not active_url.database or active_url.database == ':memory:':
        raise ValueError('Existing external database must be retained; automatic replacement refused')
    source_db = Path(active_url.database).resolve()
    # Never replace or reuse an unrelated service or partially initialized DB.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SYSTEM\\CurrentControlSet\\Services\\' + SERVICE):
            raise ValueError('PG service already exists without active managed settings; manual recovery required')
    except FileNotFoundError:
        pass
    cluster = pg_root / '15' / 'data'
    if cluster.exists():
        raise ValueError('PG data directory already exists; refusing initialization or overwrite')
    # Must run before credentials, cluster directories or ACL modifications.
    preflight_binaries(pg_bin)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
    protect_directory(pg_root)
    protect_directory(data_dir / 'managed')
    _archive_uninitialized_recovery(pg_root)
    cluster.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(48)
    admin_password = secrets.token_urlsafe(48)
    recovery = pg_root/'recovery.json'
    descriptor = os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump({'admin_password':admin_password, 'app_password':password, 'port':port}, handle)
    password_file = pg_root / ('init-' + uuid.uuid4().hex)
    descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii') as handle:
        handle.write(admin_password)
    try:
        _command([pg_bin/'initdb.exe', '-D', cluster, '-U', 'netmgr_admin', '--encoding=UTF8',
                  '--locale=C', '--auth=scram-sha-256', '--pwfile=' + str(password_file)])
    except Exception:
        if not cluster.exists():
            try:
                _archive_uninitialized_recovery(pg_root)
            except Exception:
                pass  # Preserve original error and untouched recovery file.
        raise
    finally:
        password_file.unlink(missing_ok=True)
    # New cluster only: no editing of previously installed PG settings.
    with (cluster/'postgresql.conf').open('a', encoding='utf-8') as handle:
        handle.write(f"\nlisten_addresses = '127.0.0.1'\nport = {int(port)}\npassword_encryption = 'scram-sha-256'\n")
    started = False
    try:
        started = True
        _command([pg_bin/'pg_ctl.exe', '-D', cluster, '-l', pg_root/'setup.log', '-w', 'start'])
        with psycopg.connect(host='127.0.0.1', port=port, user='netmgr_admin', password=admin_password,
                             dbname='postgres', autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE ROLE netmgr LOGIN PASSWORD {}').format(sql.Literal(password)))
            connection.execute('CREATE DATABASE netmgr OWNER netmgr')
        url = URL.create('postgresql+psycopg', username='netmgr', password=password,
                         host='127.0.0.1', port=port, database='netmgr').render_as_string(hide_password=False)
        scratch = data_dir/'managed'/('migration-' + uuid.uuid4().hex)
        scratch.mkdir(mode=0o700)
        _prepare_database(url, scratch/'target', secret)
        if source_db.exists():
            snapshot = scratch/'source.db'
            with sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True) as source:
                with sqlite3.connect(snapshot) as destination:
                    source.backup(destination)
            source_url = 'sqlite:///' + str(snapshot)
            _prepare_database(source_url, scratch/'source', secret)
            source_engine, target_engine = create_engine(source_url), create_engine(url)
            try:
                report = copy_verified_snapshot(source_engine, target_engine)
                (scratch/'verified.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            finally:
                source_engine.dispose(); target_engine.dispose()
    finally:
        if started:
            _command([pg_bin/'pg_ctl.exe', '-D', cluster, '-m', 'fast', '-w', 'stop'])
    # NetworkService may access the cluster, but not the recovery credentials.
    _command(['icacls', cluster, '/grant', '*S-1-5-20:(OI)(CI)M', '/T', '/Q'])
    _command([pg_bin/'pg_ctl.exe', 'register', '-N', SERVICE, '-D', cluster,
              '-U', 'NT AUTHORITY\\NetworkService', '-S', 'auto'])
    _command(['sc.exe', 'start', SERVICE])
    # Wait for the registered service, not merely the temporary setup process.
    import time
    connected = False
    for _ in range(30):
        try:
            with psycopg.connect(host='127.0.0.1', port=port, user='netmgr', password=password,
                                 dbname='netmgr', connect_timeout=2):
                connected = True
                break
        except psycopg.OperationalError:
            time.sleep(1)
    if not connected:
        raise RuntimeError('Registered PG service failed health check; SQLite remains active')
    values.update(DATABASE_URL=url, PG_BIN_DIR=str(pg_bin))
    write_runtime(data_dir, values)
    return values
