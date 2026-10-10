"""Conservative LLDP decoders: never invent management addresses or ports."""
import ipaddress
import re


def _empty():
    return dict.fromkeys(('neighbor_name', 'neighbor_ip', 'local_interface',
                          'neighbor_interface', 'neighbor_platform', 'neighbor_capability'), '')


def _ip(value):
    for token in re.findall(r'[0-9a-fA-F:.]+', value):
        try:
            return str(ipaddress.ip_address(token))
        except ValueError:
            pass
    return ''


def parse_details(raw):
    # Fortinet indexed key/value output; index boundaries prevent merging peers.
    rows = {}
    fields = {'port.txt': 'local_interface', 'port.desc.data': 'neighbor_interface',
              'port.id.data': 'neighbor_interface', 'system.name.data': 'neighbor_name',
              'system.desc.data': 'neighbor_platform', 'system.caps.enabled.txt': 'neighbor_capability'}
    for match in re.finditer(r'^\s*lldp(?:rx|\.rx)\.neighbor\.(\d+)\.([\w.]+):\s*([^\r\n]*)', raw, re.M | re.I):
        index, key, value = match.groups()
        row = rows.setdefault(index, _empty())
        if key.lower() in fields:
            row[fields[key.lower()]] = value.strip()
        elif re.fullmatch(r'address\.\d+\.addr', key, re.I):
            row['neighbor_ip'] = row['neighbor_ip'] or _ip(value)
    if rows:
        return [r for r in rows.values() if r['neighbor_name'] or r['neighbor_ip']]

    # Line-oriented Cisco / PAN-OS / Gaia detail formats. Local interface is
    # carried across multiple neighbors on that interface, but no other field is.
    result, row, local = [], _empty(), ''
    def flush():
        nonlocal row
        if row['neighbor_name'] or row['neighbor_ip']:
            result.append(row)
        row = _empty(); row['local_interface'] = local
    for line in raw.splitlines():
        cp = re.match(r"\s*Interface '([^']+)' has \d+ LLDP Neighbors", line, re.I)
        pair = re.match(r'\s*([^:]+):\s*(.*)$', line)
        if cp:
            flush(); local = cp.group(1); row['local_interface'] = local
            continue
        if re.match(r'\s*(?:-{3,}|Local information:|Neighbor \d+:)', line, re.I):
            flush()
            if re.match(r'\s*Local information:', line, re.I):
                local = ''; row['local_interface'] = ''
            continue
        if not pair:
            continue
        key, value = pair.group(1).strip().lower(), pair.group(2).strip()
        if key in ('local intf', 'local interface', 'local port id'):
            if row['neighbor_name'] or row['neighbor_ip']:
                flush()
            local = value; row['local_interface'] = value
        elif key == 'chassis id':
            if row['neighbor_name'] or row['neighbor_ip']:
                flush()
            row['neighbor_name'] = value
        elif key == 'system name':
            row['neighbor_name'] = value
        elif key in ('port id', 'port description'):
            if key == 'port id' or not row['neighbor_interface']:
                row['neighbor_interface'] = re.sub(r'^(?:Interface Name|Locally Assigned)\s*-\s*', '', value, flags=re.I)
        elif key in ('ipv4 address', 'ipv6 address', 'management address', 'management ip address'):
            row['neighbor_ip'] = row['neighbor_ip'] or _ip(value)
        elif key in ('system description', 'system capabilities', 'system capabiltiies'):
            row['neighbor_platform' if key == 'system description' else 'neighbor_capability'] = value
    flush()
    return result


def explicit_empty(raw):
    if re.search(r'\b(?:has|total (?:entries|neighbors)(?: displayed)?\s*:)\s*[1-9]\d*\b', raw, re.I):
        return False
    return bool(re.search(r'\b(?:no (?:lldp |cdp )?neighbors?(?: found)?|'
                          r'total (?:entries|neighbors)(?: displayed)?\s*:\s*0\b|'
                          r'0 (?:lldp |cdp )?neighbors?\b)', raw, re.I))
