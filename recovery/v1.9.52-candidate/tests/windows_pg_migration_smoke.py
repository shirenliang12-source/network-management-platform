"""Isolated real Windows PostgreSQL migration test; never uses installed services."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1]).resolve()
    if not (binary / 'pg_ctl.exe').is_file():
        raise RuntimeError('Provide extracted PostgreSQL bin directory')
    with tempfile.TemporaryDirectory(prefix='netmgr-pg-test-') as scratch:
        root = Path(scratch)
        password = secrets.token_hex(24)
        password_file = root / 'password.txt'
        password_file.write_text(password, encoding='ascii')
        data = root / 'pgdata'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        def execute(arguments):
            subprocess.run(arguments, check=True, capture_output=True, creationflags=flags, timeout=90)
        execute([str(binary/'initdb.exe'), '-D', str(data), '-U', 'netmgr', '--encoding=UTF8', '--locale=C',
                 '--auth=scram-sha-256', '--pwfile=' + str(password_file)])
        password_file.unlink()
        started = False
        try:
            execute([str(binary/'pg_ctl.exe'), '-D', str(data), '-l', str(root/'pg.log'),
                     '-o', f'-h 127.0.0.1 -p {port}', '-w', 'start'])
            started = True
            os.environ['NETMGR_DATABASE_URL'] = f'postgresql+psycopg://netmgr:{password}@127.0.0.1:{port}/postgres'
            os.environ['CISCO_NM_DATA_DIR'] = str(root/'appdata')
            os.environ['NETMGR_SECRET_KEY'] = secrets.token_hex(32)
            from sqlalchemy import create_engine, text
            from sqlalchemy.orm import Session
            from app.database import engine, Base, init_db
            from app.models import SystemSetting, IPAMPrefix, DCSite, DCRack, ServerAsset, ServerIP
            from app.services.sqlite_to_postgres import copy_verified_snapshot
            init_db()
            sqlite = create_engine('sqlite:///' + str(root/'source.db'))
            Base.metadata.create_all(sqlite)
            with engine.connect() as connection:
                revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            with sqlite.begin() as connection:
                connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
                connection.execute(text('INSERT INTO alembic_version VALUES (:revision)'), {'revision': revision})
            with Session(sqlite) as db:
                db.add(SystemSetting(key='preserve', value='配置与密钥指针保持原样'))
                db.add(IPAMPrefix(id=20, prefix='192.0.2.0/25', parent_id=30))
                db.add(IPAMPrefix(id=30, prefix='192.0.2.0/24'))
                db.add(DCSite(id=10, name='test-site')); db.flush()
                db.add(DCRack(id=11, site_id=10, rack_number='A01')); db.flush()
                db.add(ServerAsset(id=12, name='server', rack_id=11, u_start=13, u_size=2)); db.flush()
                db.add(ServerIP(server_id=12, ip_address='192.0.2.10')); db.commit()
            report = copy_verified_snapshot(sqlite, engine)
            assert report['ok'] and report['rows'] >= 7
            with Session(engine) as db:
                assert db.get(IPAMPrefix, 20).parent_id == 30
                assert db.get(SystemSetting, 'preserve').value == '配置与密钥指针保持原样'
                db.add(DCSite(name='next')); db.commit()
                assert db.query(DCSite).filter_by(name='next').one().id > 10
            try:
                copy_verified_snapshot(sqlite, engine)
                raise AssertionError('Nonempty PG should be rejected')
            except ValueError as exc:
                assert '已有业务数据' in str(exc)
            sqlite.dispose(); engine.dispose()
            print('PASS real Windows PG: verified migration, self links, Unicode, sequences and nonempty target guard')
        finally:
            if started:
                execute([str(binary/'pg_ctl.exe'), '-D', str(data), '-m', 'fast', '-w', 'stop'])


if __name__ == '__main__':
    main()
