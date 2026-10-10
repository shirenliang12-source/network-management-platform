"""Conservative version parsing for FortiOS, PAN-OS and ASA."""
import re


def parse_firewall_version(raw, driver):
    patterns = {
        'fortinet': {'hostname': r'^Hostname:\s*(.+)$', 'model': r'^Version:\s*(\S+)',
                     'os_version': r'^Version:.*?\s(v[\d.]+[^,\r\n]*)', 'serial_number': r'^Serial-Number:\s*(\S+)'},
        'paloalto_panos': {'hostname': r'^hostname:\s*(.+)$', 'model': r'^model:\s*(.+)$',
                           'os_version': r'^sw-version:\s*(.+)$', 'serial_number': r'^serial:\s*(.+)$', 'uptime': r'^uptime:\s*(.+)$'},
        'cisco_asa': {'os_version': r'Adaptive Security Appliance Software Version\s+(\S+)',
                      'serial_number': r'^Serial Number:\s*(\S+)', 'model': r'^Hardware:\s*([^,]+)',
                      'hostname': r'^(\S+) up\s+\d', 'uptime': r'^\S+ up\s+(.+)$'},
    }
    result = {}
    for field, pattern in patterns.get(driver, {}).items():
        match = re.search(pattern, raw, re.I | re.M)
        if match:
            result[field] = match.group(1).strip()
    if result:
        result['os_type'] = {'fortinet':'FortiOS', 'paloalto_panos':'PAN-OS', 'cisco_asa':'ASA'}[driver]
    return result
