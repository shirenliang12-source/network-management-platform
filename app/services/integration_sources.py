"""Stable per-instance source names, retaining the legacy vcenter identity."""
import re


def config_key(source):
    if not re.fullmatch(r'zabbix|vcenter(?:-[0-9a-f]{12})?', source):
        raise ValueError('无效的集成来源')
    return 'integration_' + source


def provider(source):
    config_key(source)
    return 'zabbix' if source == 'zabbix' else 'vcenter'
