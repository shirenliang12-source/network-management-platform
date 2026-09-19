"""Describe configured capabilities; does not claim firmware was live-tested."""
from app.services.command_config import get_command, resolve_device_driver


def describe_device_type(key):
    try:
        driver = resolve_device_driver(key)
    except ValueError:
        return {'driver': '', 'note': '未配置连接驱动，请在设置中维护。'}
    if driver == 'inventory_only':
        return {'driver': driver, 'note': '仅登记：不会尝试 SSH 登录或备份。CUCM 使用 DRS；电话与轻量 AP 通过对应管理系统维护。'}
    enabled = [label for command, label in [('running_config', '配置采集'), ('show_version', '版本采集'),
                ('cdp_neighbors', 'CDP'), ('lldp_neighbors', 'LLDP')] if get_command(key, command).strip()]
    limits = {
        'cisco_asa': '需要独立 Enable 密码；未填写仅尝试空密码。不支持 FXOS。',
        'cisco_ftd': '需要诊断 CLI 权限；仅备份 LINA，不是完整 FMC/FDM 恢复备份。',
        'fortinet': '采集范围取决于 VDOM/账号权限；旧版自定义模板需人工确认 LLDP 命令。',
        'paloalto_panos': '读取 running configuration；命令与权限需按实际 PAN-OS 验证。',
        'checkpoint_gaia': '仅 Gaia 配置，不含完整管理策略；启用 LLDP 需将命令设为 lldpneighbors，并在 Enable 密码栏填写独立 Expert 密码。',
    }
    return {'driver': driver, 'note': f"驱动：{driver}；已配置：{'、'.join(enabled) or '无采集命令'}。" + limits.get(driver, '请核对实际系统及命令权限。')}
