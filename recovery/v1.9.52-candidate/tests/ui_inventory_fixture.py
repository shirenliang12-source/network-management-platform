"""Local-only UI fixture: synthetic CDP chain, isolated data, no SSH traffic."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = tempfile.TemporaryDirectory(prefix="netmgr-ui-")
os.environ['CISCO_NM_DATA_DIR'] = DATA.name
os.environ['NETMGR_SECRET_KEY'] = 'ui-fixture-only-not-a-production-key'
os.environ['NETMGR_INITIAL_ADMIN_PASSWORD'] = 'UiFixture-Only-1943!'

from app.database import init_db, SessionLocal
from app.models import Device, Neighbor
from app.services import discovery_service
init_db()
with SessionLocal() as db:
    db.add(Device(name='UI-SW-A', ip_address='192.0.2.1', company='UI Test Company'))
    db.commit()

def fake_discover(device, db):
    suffix = int(device.ip_address.rsplit('.', 1)[1]) + 1
    if not db.query(Neighbor).filter_by(device_id=device.id).first():
        db.add(Neighbor(device_id=device.id, neighbor_name=f'UI-SW-{suffix}',
                        neighbor_ip=f'192.0.2.{suffix}', neighbor_platform='Cisco C9300',
                        protocol='cdp', local_interface='Gi1/0/1', neighbor_interface='Gi1/0/2'))
        db.commit()
    return {'success': True, 'cdp_count': 1, 'lldp_count': 0}

discovery_service.discover_device_neighbors = fake_discover
from app.main import app
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18943)
