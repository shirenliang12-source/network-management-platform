import unittest
from unittest.mock import MagicMock, patch
import test_security
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Device, Neighbor
from app.services.discovery_service import discover_device_neighbors
from app.services.ssh_service import SSHService, parse_lldp_neighbors
from app.services.lldp_parser import explicit_empty


class LLDPParserTests(unittest.TestCase):
    def test_fortinet_indexed_peers_ipv4_ipv6(self):
        text = '''lldprx.neighbor.1.port.txt: port1
lldprx.neighbor.1.system.name.data: switch-a
lldprx.neighbor.1.port.id.data: Gi1/1
lldprx.neighbor.1.address.1.addr: 192.0.2.1
lldprx.neighbor.2.port.txt: port2
lldprx.neighbor.2.system.name.data: switch-b
lldprx.neighbor.2.address.1.addr: 2001:db8::2'''
        rows = parse_lldp_neighbors(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['neighbor_ip'], '192.0.2.1')
        self.assertEqual(rows[1]['neighbor_ip'], '2001:db8::2')
        self.assertEqual(rows[1]['local_interface'], 'port2')

    def test_cisco_name_preferred_over_chassis(self):
        rows = parse_lldp_neighbors('Local Intf: Gi1/0/1\nChassis id: aabb.ccdd.eeff\nPort id: Gi0/1\nSystem Name: phone-a\nIPv4 address: 192.0.2.2')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['neighbor_name'], 'phone-a')

    def test_local_interface_boundaries(self):
        rows = parse_lldp_neighbors('Local information:\nLocal interface: ethernet1/1\nChassis id: abc\nSystem name: peer-a\nPort id: p1\nLocal information:\nLocal interface: ethernet1/2\nChassis id: def\nSystem name: peer-b\nPort id: p2')
        self.assertEqual([r['local_interface'] for r in rows], ['ethernet1/1', 'ethernet1/2'])
        self.assertEqual([r['neighbor_name'] for r in rows], ['peer-a', 'peer-b'])

    def test_unknown_text_not_neighbors_or_empty(self):
        self.assertEqual(parse_lldp_neighbors('Permission denied'), [])
        self.assertFalse(explicit_empty(''))
        self.assertTrue(explicit_empty('Total entries displayed: 0'))
        self.assertFalse(explicit_empty("Interface 'eth0' has 0 LLDP Neighbors:\nInterface 'eth1' has 2 LLDP Neighbors:"))

    def test_command_rejection_not_returned_as_configuration(self):
        device = MagicMock(device_type='cisco_ios', ip_address='192.0.2.1')
        ssh = SSHService(device); ssh.connection = MagicMock()
        ssh.connection.send_command.return_value = '% Invalid input detected at marker.'
        self.assertEqual(ssh.send_command('show running-config'), '')
        self.assertIn('拒绝', ssh.last_error)

    def test_gaia_neighbor_fields(self):
        rows = parse_lldp_neighbors("Interface 'eth1' has 1 LLDP Neighbors:\nNeighbor 1:\nPort ID: Interface Name - Gi1/1\nSystem Name: lab-switch\nManagement Address: IPv4 - 192.0.2.8 (ifIndex - 1)")
        self.assertEqual(rows[0]['neighbor_ip'], '192.0.2.8')
        self.assertEqual(rows[0]['local_interface'], 'eth1')

    def test_checkpoint_expert_requires_explicit_secret_and_exits(self):
        device = MagicMock(device_type='checkpoint_gaia')
        device.get_enable_password.return_value = ''
        ssh = SSHService(device); ssh.connection = MagicMock()
        ssh.connection.check_enable_mode.return_value = False
        with patch('app.services.ssh_service.get_command', return_value='lldpneighbors'):
            self.assertEqual(ssh.get_lldp_neighbors(), '')
            ssh.connection.enable.assert_not_called()
            device.get_enable_password.return_value = 'expert-secret'
            ssh.connection.send_command.return_value = 'no neighbors found'
            self.assertEqual(ssh.get_lldp_neighbors(), 'no neighbors found')
            ssh.connection.enable.assert_called_once()
            ssh.connection.exit_enable_mode.assert_called_once()

    def test_fortinet_fallback_only_for_known_default(self):
        ssh = SSHService(MagicMock(device_type='fortinet'))
        ssh.connection = MagicMock()
        ssh.connection.send_command.side_effect = ['command parse error before lldprx', 'no neighbors found']
        with patch('app.services.ssh_service.get_command', return_value='diagnose lldprx neighbor details'):
            self.assertEqual(ssh.get_lldp_neighbors(), 'no neighbors found')
        self.assertEqual(ssh.connection.send_command.call_args.args[0], 'diagnose lldp rx neighbor details')
        self.assertEqual(ssh.last_error, '')


class DiscoverySafetyTests(unittest.TestCase):
    def test_inventory_types_never_connect_or_read_passwords(self):
        from app.services.device_capabilities import describe_device_type
        from app.services.discovery_service import infer_device_type_from_platform
        self.assertEqual(infer_device_type_from_platform('Cisco IP Phone 8841'), 'cisco_ipt')
        for kind in ('cisco_ipt', 'cisco_cucm', 'cisco_ap_lightweight'):
            device = MagicMock(device_type=kind)
            with patch('app.services.ssh_service.ConnectHandler') as connect:
                ssh = SSHService(device)
                self.assertFalse(ssh.connect())
                self.assertEqual(ssh.error_stage, 'capability')
                connect.assert_not_called()
                device.get_password.assert_not_called()
                self.assertEqual(describe_device_type(kind)['driver'], 'inventory_only')

    def test_similar_name_does_not_create_false_link(self):
        self.db.add(Device(name='peer-other', ip_address='192.0.2.9')); self.db.commit()
        ssh = MagicMock(); ssh.connect.return_value = True; ssh.last_error = ''
        ssh.get_cdp_neighbors.return_value = 'Total entries displayed: 0'
        ssh.get_lldp_neighbors.return_value = 'Local Intf: Gi1\nChassis id: aabb\nSystem Name: peer\nPort id: Gi2'
        with patch('app.services.discovery_service.SSHService', return_value=ssh):
            self.assertTrue(discover_device_neighbors(self.device, self.db)['success'])
        self.assertIsNone(self.db.query(Neighbor).one().neighbor_device_id)

    def setUp(self):
        self.engine = create_engine('sqlite://'); Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.device = Device(name='test', ip_address='192.0.2.1', device_type='cisco_ios', status='online')
        self.db.add(self.device); self.db.flush()
        self.db.add(Neighbor(device_id=self.device.id, protocol='lldp', neighbor_name='existing'))
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def test_authentication_and_unknown_output_preserve_neighbors(self):
        for connected, error in [(False, 'authentication failed'), (True, '')]:
            ssh = MagicMock(); ssh.connect.return_value = connected; ssh.last_error = error
            ssh.get_cdp_neighbors.return_value = 'unrecognized output'
            with patch('app.services.discovery_service.SSHService', return_value=ssh):
                result = discover_device_neighbors(self.device, self.db)
            self.assertFalse(result['success'])
            self.assertEqual(self.db.query(Neighbor).one().neighbor_name, 'existing')
            self.assertEqual(self.device.status, 'online')

    def test_explicit_empty_replaces_successful_protocol_only(self):
        ssh = MagicMock(); ssh.connect.return_value = True; ssh.last_error = ''
        ssh.get_cdp_neighbors.return_value = 'Total entries displayed: 0'
        with patch('app.services.discovery_service.SSHService', return_value=ssh), patch('app.services.command_config.get_command', side_effect=lambda _, key: 'show cdp neighbors detail' if key == 'cdp_neighbors' else ''):
            self.assertTrue(discover_device_neighbors(self.device, self.db)['success'])
        self.assertEqual(self.db.query(Neighbor).one().neighbor_name, 'existing')
