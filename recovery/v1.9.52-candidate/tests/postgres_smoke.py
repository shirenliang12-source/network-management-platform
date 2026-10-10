"""Run only against an explicitly supplied disposable PostgreSQL database."""
import os
import tempfile
assert os.environ.get('NETMGR_DATABASE_URL','').startswith('postgresql+psycopg://'), 'Explicit disposable PG database required'
os.environ['CISCO_NM_DATA_DIR']=tempfile.mkdtemp(prefix='netmgr-pg-')
from sqlalchemy import inspect, text
from app.database import engine, init_db, SessionLocal
assert not inspect(engine).get_table_names(), 'Refusing to test against a nonempty database'
init_db()
from app.models import VMInstance, IPInventory, SystemSetting
from app.services.csv_inventory_import import run_import
from app.services.ipam_import import run_import as ipam_import
with SessionLocal() as db:
    raw='subnet,mask,remarks\n192.0.2.0,255.255.255.0,keep\n192.0.2.0/24,,duplicate'
    assert run_import(db,'ip_inventory',raw)['created']==1
    assert run_import(db,'ip_inventory',raw)['created']==0
    assert db.query(IPInventory).count()==1
    assert run_import(db,'vms','name,management_ip\npg-vm,192.0.2.10')['created']==1
    assert run_import(db,'vms','name,management_ip\npg-vm,192.0.2.10')['created']==0
    ipam_import(db,'prefixes','prefix\n192.0.2.0/24')
    db.add(SystemSetting(key='pg-preserve',value='keep'));db.commit()
init_db()  # Exercises pg_dump and repeated Alembic startup, preserving data.
with SessionLocal() as db:
    assert db.get(SystemSetting,'pg-preserve').value=='keep'
    assert db.query(VMInstance).count()==1
    assert db.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20260910_0006'
from app.services.data_backup import create_data_backup, restore_data_backup
assert not create_data_backup()['ok']
assert not restore_data_backup(b'not-a-sqlite-backup')['ok']
print('PASS PostgreSQL migration, idempotent imports, restart backup, preserved settings and restore guards')
