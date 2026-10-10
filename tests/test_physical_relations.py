import unittest
import test_security
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Device, ServerAsset, DCSite, DCRack, IPAMPrefix, IPAMIPAddress, ServerIP
from app.services.asset_relations import relations


class PhysicalRelationsTests(unittest.TestCase):
    def test_imported_cidr_extra_ip_and_rack_reverse_lookup(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            site = DCSite(name='DC'); db.add(site); db.flush()
            rack = DCRack(site_id=site.id, rack_number='R13'); db.add(rack); db.flush()
            server = ServerAsset(name='host', rack_id=rack.id, u_start=13, u_size=2)
            device = Device(name='switch', ip_address='192.0.2.10')
            prefix = IPAMPrefix(prefix='192.0.2.0/24')
            db.add_all([server, device, prefix]); db.flush()
            address = IPAMIPAddress(prefix_id=prefix.id, address='192.0.2.10/24')
            db.add_all([address, ServerIP(server_id=server.id, ip_address='192.0.2.10')]); db.commit()
            def targets(kind, ident, allowed=None):
                return {(n['kind'], n['id']) for g in relations(db, kind, ident, allowed)['groups'] for n in g['items']}
            self.assertTrue({('device', device.id), ('server', server.id), ('rack', rack.id)} <= targets('ip', address.id))
            for kind, ident in [('device', device.id), ('server', server.id), ('rack', rack.id)]:
                self.assertIn(('ip', address.id), targets(kind, ident))
            self.assertNotIn(('device', device.id), targets('ip', address.id, {'ipam', 'servers', 'dc'}))
            self.assertIsNone(address.assigned_device_id)
        engine.dispose()
