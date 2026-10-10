# 当前设备适配范围

本次修改尚未打包部署，离线回归测试不能代替真实设备验证。

| 类型 | 连接与采集 | 备份范围 |
| --- | --- | --- |
| ASA | cisco_asa；SSH 后独立 Enable，留空只尝试空密码 | running-config |
| FTD | cisco_ftd；诊断 CLI，不涉及 FXOS | 仅 LINA，不是完整 FMC/FDM 恢复备份 |
| IOS 路由器、自主 IOS AP | IOS SSH | running-config |
| AireOS WLC / Catalyst 9800 | 分别选择 cisco_wlc_ssh / IOS-XE | 文本配置，完整恢复需按型号验证 |
| Fortinet | 原有 Fortinet 驱动及可维护模板 | full-configuration；VDOM 范围取决于账号权限 |
| PA | PAN-OS SSH | running configuration |
| Check Point | Gaia Clish SSH，不自动进入 Expert | Gaia 配置，不含完整管理策略备份 |
| CUCM | 可登记，默认 inventory_only，不发起 SSH | 完整备份使用 DRS，尚未集成 |
| IPT / IP Phone、轻量 AP | 可登记，默认 inventory_only，不套用 IOS | 不宣称支持通用 SSH 备份 |

Fortinet 和 PA 新默认模板分别增加 LLDP 读取命令；历史自定义模板保持不变，需在设置中人工确认更新。
已补充 Fortinet 索引详情、Cisco 及 PAN-OS/Gaia 键值详情格式解析和离线样例测试，仍需实机输出验证。Fortinet 默认旧命令被拒绝时可尝试新版本只读命令。
空白、拒绝执行或无法识别的结果不会清除历史邻居；仅明确零邻居结果才会更新为空。未配置协议保持历史记录。
邻居仅按唯一 IP 或无 IP 时的唯一精确名称绑定，不再使用子串匹配。
Check Point 的邻居读取需 Expert 模式执行 lldpneighbors，当前 Clish 模板不配置虚假的 show lldp neighbors 命令。
如需启用：在设置中将 Check Point 的 LLDP 命令明确设为 `lldpneighbors`，并在设备 Enable 密码栏维护独立 Expert 密码。平台只读查询后退出 Expert；缺少密码或退出失败均报错且保留历史邻居。默认空命令不会尝试进入 Expert。
Fortinet 原有 Netmiko 会话会临时调整控制台分页，读取权限/VDOM 限制的账号仍需进一步适配。

参考：
- [Check Point LLDP](https://sc1.checkpoint.com/documents/R81/WebAdminGuides/EN/CP_R81_Gaia_AdminGuide/Topics-GAG/LLDP.htm)
- [Fortinet LLDP](https://community.fortinet.com/fortigate-3/technical-tip-use-lldp-on-fortigate-to-verify-connected-switches-210084)
- [PAN-OS 命令](https://docs.paloaltonetworks.com/ngfw/pan-os-cli-quick-start/cli-command-hierarchy/pan-os-11-2-cli-ops-command-hierarchy)
- [CUCM DRS](https://www.cisco.com/c/en/us/support/docs/unified-communications/unified-communications-manager-callmanager/214287-configure-and-troubleshoot-cisco-unified.html)
