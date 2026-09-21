"""Read existing NSSM service configuration without changing it."""
import os
import re


def apply_service_context():
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r'SYSTEM\CurrentControlSet\Services\CiscoNetworkManager\Parameters')
    except FileNotFoundError:
        return
    with key:
        def read(name, default=None):
            try: return winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError: return default
        for name in ('AppEnvironment', 'AppEnvironmentExtra'):
            for entry in read(name, []) or []:
                match = re.fullmatch(r'(NETMGR_[A-Z_]+|CISCO_NM_DATA_DIR)=(.*)', entry, re.S)
                if match: os.environ[match[1]] = match[2]
        parameters = read('AppParameters', '') or ''
        port = re.search(r'(?:^|\s)--port\s+(\d+)(?=\s|$)', parameters)
        if port: os.environ['NETMGR_PORT'] = port[1]
        directory = re.search(r'(?:^|\s)--data-dir\s+(?:"([^"]+)"|(\S+))', parameters)
        if directory: os.environ['CISCO_NM_DATA_DIR'] = directory[1] or directory[2]
        working = read('AppDirectory')
        if working: os.chdir(working)
