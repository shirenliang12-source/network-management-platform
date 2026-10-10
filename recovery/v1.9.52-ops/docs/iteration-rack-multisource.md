# 机柜及多来源集成迭代记录

## 已修改源码，尚未发布安装包

- 数据中心、服务器存储统一使用 rack_view.js。U 编号和占用格放在同一个网格行中，消除原先设备格额外 margin 带来的累计偏移。起始 U13、大小 2 对应 U13–14，U1 在底部。不改数据库中的 U 位或配置。
- Windows DHCP 拒绝访问错误显示失败阶段与账号模式。不输出密码、原始 stderr，不回退账号，不修改远程权限。
- 测试覆盖 U13–14、超界、重叠、HTML 转义、两个页面引用同一组件，以及 DHCP 分阶段错误与账号模式。

## Windows DHCP 现场确认

连通性正常不等于远程管理授权正常。根据新的失败阶段，由服务器管理员检查：

1. 建立会话失败：指定域账号是否可用、DCOM 远程访问/启动/激活权限，以及远程 WMI 访问限制。
2. 读取作用域/统计失败：DHCP 只读权限，以及 DHCP WMI 提供程序和命名空间的访问权限。创建 CIM 会话成功不能证明后续查询已经获得授权。
3. 使用服务账号时，平台网页登录账号与 Windows 服务账号不是同一个身份。不要通过关闭防火墙、禁用 UAC 或扩大为 Everyone 权限解决问题。

参考：[微软远程 WMI 安全说明](https://learn.microsoft.com/en-us/windows/win32/wmisdk/securing-a-remote-wmi-connection)。当前未取得现场事件日志，也未远程测试，不能判定具体缺少哪项权限。

## 待完成：多个 VMware 来源

当前仍是单一 integration_vcenter 配置，尚未增加多连接界面。实施前必须覆盖：

- 每个连接使用稳定来源 ID，区分 vCenter 与独立 ESXi，分别保存加密凭据。
- VM 与存储的查找、唯一性、预览、失联标记、同步记录和定时任务按来源 ID 隔离。当前仅按 source_type + external_id 查找，不能直接添加第二个连接，否则相同外部 ID 存在覆盖风险。
- 原有连接、已导入 VM、IP 占用与存储关联需兼容迁移，不应通过删库重导实现。
- 独立 ESXi 与其所属 vCenter 重复纳管需明确提示，不能按同名宿主机自动合并来源。

## 待完成：PA / Fortinet DHCP

尚未新增采集器或来源类型，不能在 Windows DHCP 表单中填入防火墙地址代替。

- 已查到官方只读命令：PA `show dhcp server lease interface all`；Fortinet `execute dhcp lease-list`。仍需按实际 PAN-OS/FortiOS 版本与 vsys/VDOM 范围适配、验证解析。
- 来源身份必须包含设备及虚拟系统/VDOM、接口/作用域，避免不同来源的同网段数据混用。
- 租约列表不等于地址池总容量，不能直接把租约数量当作完整利用率；需验证池范围、排除地址及租约状态。
- 仅允许读取配置/统计。解析失败保留上次成功快照并显示错误，不能回写 0 利用率；不得修改设备 DHCP 配置。

参考：[PA DHCP 信息](https://docs.paloaltonetworks.com/ngfw/networking/dhcp/view-dhcp-server-information)、[FortiOS 7.4.7 DHCP CLI](https://docs.fortinet.com/document/fortigate/7.4.7/cli-reference/329164585/execute-dhcp)。
