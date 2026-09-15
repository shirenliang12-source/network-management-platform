# Cisco 网络自动化运维平台

> 自动备份配置 · 邻居发现 · 设备信息采集 · 拓扑可视化

## 功能概览

### 1.9.51 PostgreSQL / Docker 迭代候选

新增 PostgreSQL 驱动、连接兼容、升级前 pg_dump 备份、导入与占用并发保护，以及独立 Docker/PG 部署文件。现有 SQLite 默认设置不变，不会自动切换或转移数据。PG 不使用原 SQLite ZIP 恢复功能；必须同时保存数据目录和密钥。详见 [PostgreSQL 部署说明](docs/postgresql-deployment.md)。GitHub 工作流通过真实临时 PG 验证后才发布候选镜像；尚未对现有生产数据执行迁移。

### 1.9.50 DHCP 多来源首版

- 平台集成增加 Windows / Palo Alto / Fortinet 类型；旧 Windows 配置继续使用，无需重建。PA 按单虚拟系统、指定接口读取；Fortinet 每个 VDOM/接口分别配置，独立统计、去重与 IPAM 关联。
- 防火墙采用 HTTPS 只读 API 密钥，非 SSH/网页登录密码。密钥加密保存，不回传、不写入 URL，不接受重定向，默认验证证书。需要设备管理员自行配置只读 API 权限及管理访问范围。
- PA 读取运行 DHCP 配置与租约统计，并交叉核对池容量；需手工确认网段掩码用于 IPAM 绑定。Fortinet 读取指定 VDOM 的地址池配置及租约列表；首版仅支持普通 IPv4 范围池，含排除范围、保留地址或同接口多个服务器配置时明确拒绝计算，不能视为全版本/全场景兼容。
- 不固定 PAN-OS/FortiOS 版本号，记录设备返回版本并按响应结构校验；未知/不完整格式、权限错误、容量不一致时保留上次成功快照并报错。多段池首尾仅表示外边界，不代表中间地址全部可分配。
- 共用原有定期同步、阈值预警、IPAM 显式绑定；不自动创建或覆盖静态 IP 占用。真实防火墙现场联调尚未完成，多 VMware 独立来源仍待实施。

接入参考：[Fortinet API 认证](https://docs.fortinet.com/document/fortigate/latest/administration-guide/940602/using-apis)、[PA DHCP 统计](https://docs.paloaltonetworks.com/ngfw/networking/dhcp/view-dhcp-server-information)。

### 1.9.49 导入去重与机柜 U 位修复

- 设备、虚拟机、IP 地址清单 CSV：数据库和同文件双重查重，默认跳过，不自动覆盖。重复结果显示行号与已有记录 ID；字段错误整批回滚。IP 清单按公司、防火墙、接口、VLAN、规范化子网区分重叠地址空间；VM/设备同名或主/附加 IP 冲突需要人工核对，可能是合法同名对象，不自动合并。
- 平台集成导入默认不更新既有对象，勾选更新需确认；新外部 VM 与手工对象同名/同 IP 时跳过并提示。单来源配置更换服务器时禁止直接覆盖旧来源身份；多 VMware 独立来源仍待实施。
- 原有重复数据不自动删除，已有 DHCP 配置、IP 占用和关联不重建。IPAM 沿用规范化 CIDR/地址查重及关系冲突校验。CSV 覆盖按钮尚未提供，需要按提示 ID 在原记录中编辑。
- 机柜两个页面统一网格：U13、占 2U 对应 U13–14；新增越界与占用冲突提示。
- Windows DHCP 拒绝访问提示失败阶段与账号模式；未修改服务器权限，PA/Fortinet DHCP 接入仍待完成。

### 1.9.48 IPAM 导入关系修复与 DHCP 指定账号

- 修复 IPAM CSV 继承的 4000 字符限制（单次最多 1000 万字符 / 50000 行）；支持 UTF-8、GB18030 文件，以及逗号、分号、制表符分隔。严格检查必填列、重复表头和列数。
- 导入 CIDR/IP 规范化，防止同一网络因主机位或 IPv6 写法产生重复。聚合/父子网段必须包含关系正确；按网段大小处理父子顺序；重名聚合、重名设备和同 CIDR 多网段不猜测绑定，可用聚合 CIDR、设备管理 IP 消除歧义。
- 导入改为整批校验：任一行异常全部回滚，显示行号；已有记录跳过且不覆盖占用。CSV 是新增库存交换格式，不是完整应用恢复备份，不自动重排已有错误关系或重建 VM/DHCP 绑定。迁移全部配置请使用应用数据备份。
- 网段导入/导出保留父网段与分配池；IP 导出增加聚合列并用设备管理 IP 标识关联。修复编辑时无法清空父网段/聚合的问题；阻止不包含的父子关系、循环关系、越界 IP 移动及目标重复 IP。
- 增加 IPAM「检查关联关系（只读）」，列出问题记录 ID；未归属聚合的网段显示在独立组中，旧循环关系不再导致树形页面无限递归。**不会自动修正安装环境中原有的关系数据**，请备份后按报告确认。
- 平台集成 / IP 清单的 DHCP 配置支持「服务运行账号」或「指定账号」。指定账号可用 `DOMAIN\user` 或 `user@domain`；密码加密保存，留空保留，更换账号或服务器需重填。同步只使用所选身份，失败不回退；上次成功快照记录采集使用账号。
- 指定账号通过 PowerShell `New-CimSession -Credential` 的 DCOM 会话读取 DHCP。密码仅通过子进程标准输入传递，不进入进程命令行、不写明文文件、不返回 API。仍需安装电脑的 RSAT DHCP 管理模块，以及目标 DHCP 读取权限、远程 CIM/DCOM 访问权限；不会自动放宽防火墙或更改 Windows 服务账号。Ping 可达不能证明 RPC/DCOM 可用。
- 错误提示区分本机模块加载、可识别的认证失败/拒绝访问/RPC 不可用及作用域/统计读取阶段。未能识别的错误保留阶段提示，不推断为密码错误。实际服务器仍需现场联调。

认证方案依据：[Microsoft New-CimSession](https://learn.microsoft.com/en-us/powershell/module/cimcmdlets/new-cimsession?view=powershell-5.1) 与 [Get-DhcpServerv4Scope 的 CimSession 参数](https://learn.microsoft.com/en-us/powershell/module/dhcpserver/get-dhcpserverv4scope?view=windowsserver2025-ps)。升级前备份应用数据，在原目录覆盖安装，保留 data 和原密钥。

### 1.9.47 DHCP 平台集成与 IP 规划联动

- **平台集成 → Windows DHCP**：统一维护服务器、IPv4 作用域、名称、预警阈值和同步间隔；支持手动同步/测试、停用以及定期同步。每项连接对应一个作用域，多作用域可添加多项。不是新增 DHCP 服务器，不向 Windows DHCP 写配置。
- **IP 地址规划 → 网段 → DHCP 地址池**：先在平台集成同步成功，再选择 CIDR 完全相同的地址池并保存关联。网段树、网段列表和查看 IP 页面均展示共享的池统计；可从网段页面触发同步或解除关联。
- DHCP 池已用/空闲/利用率和平台注册占用分开展示，不相加、不创建租约记录、不覆盖静态 IP 或 VM 手动占用。失败保留上次成功快照并标注错误；作用域掩码改变导致不匹配时不展示错误归属的统计。
- v1.9.46 IP 清单中的 DHCP 配置直接显示为兼容来源，读取不修改原配置；两处维护同一份数据，不复制配置、不丢失原快照或定时策略。被 IP 规划引用的地址池/清单记录不能直接删除或更换来源。
- 新配置和网段关联保存在原 `system_settings` 表，无新增数据库迁移。定期同步仍由应用内调度器执行。配置连接仅超级管理员；维护网段关联及触发同步需同时拥有 IP 规划和平台集成权限；仅 IP 规划权限可查看关联统计。
- 仍需 Windows DHCP 管理组件及平台服务运行账号的 DHCP 读取权限，本次测试使用模拟 DHCP 返回值，尚未连接用户的实际服务器。

升级前请先备份应用数据，再安装到原目录；安装包不会替你修改 Windows 服务运行账号或 DHCP 服务器权限。

### 1.9.46 手动占用、DHCP 地址池与批量维护

- 关联反查弹窗增加「手动占用 / 释放占用」：将虚拟机主 IP / 附加 IP 登记到规划网段，保存 VM 外键及使用中状态。检查网络设备、重复虚拟机、已有分配、DHCP 等冲突，不覆盖既有占用。VM 的 IP 改变后，旧占用仍需手动释放。正在占用的 VM/IP/网段不能直接删除。
- IPAM 利用率按「使用中」计算，不再将仅生成的「规划」记录当成已用；已登记总数单独保留。
- IP 地址清单每行「DHCP 地址池」中选择 DHCP，填写 Windows DHCP 服务器、作用域网络地址、预警阈值及同步间隔。默认阈值 80%，间隔 60 分钟，0 关闭定期同步。自动同步在程序运行期间执行；失败显示错误并保留上次成功快照。
- DHCP 为只读 IPv4 作用域统计：池范围、已用、空闲、保留和服务器返回的利用率，不写 DHCP 配置、不导入租约到 IPAM，也不把 DHCP 池外的静态 IP 计入 DHCP 利用率。DHCP 故障转移节点的数据不自动相加。配置服务器仅限超级管理员。
- 运行条件：Windows 安装电脑具备 `DhcpServer` PowerShell 模块（RSAT DHCP 管理工具）；**平台服务运行账号**具备目标 DHCP 服务器读取权限及网络连通性。当前使用 Windows 集成身份，不在网页保存域密码、不自动更改服务账号。本版本尚未在用户实际 DHCP 服务器上联调。
- 设备、虚拟机、服务器存储、IT 资产、IP 清单/规划、数据中心、账号、SSH 凭据、定时任务页面增加「多选 / 全选批量维护」。独立窗口筛选，全选当前筛选结果；编辑仅修改明确选择的字段。逐条执行、逐条报错，成功项不回滚。审计日志、备份内容及仪表盘等非普通库存记录不提供批量改写。关联的站点/机柜/硬件禁止直接删除。
- 配置备份页面「每台设备保留数量」：留空继承全局天数；0 全部保留；正整数为最近 N 次，基线额外保留。保存策略不立即删除，下次该设备成功备份后清理；失败备份不会触发该设备清理。
- 增加 Fortinet FortiGate、Cisco ASA、Palo Alto PAN-OS 内置 SSH 驱动与采集/备份命令模板及基础版本信息解析。用户原命令配置优先，不覆盖自定义命令；不对防火墙发送 IOS 备用采集命令。Cisco FTD 不等同于 ASA；接口/性能输出差异仍需实际型号联调，不承诺所有版本字段完整解析。
- 升级新增迁移 `20260910_0006`，仅增加 VM 占用关系。DHCP、保留策略存于原配置数据库，随应用数据备份保存。升级前请在旧版做「一键数据备份」，在原目录覆盖安装，勿卸载或删除 data 与密钥。

实现依据：[Microsoft DHCP 作用域统计](https://learn.microsoft.com/en-us/powershell/module/dhcpserver/get-dhcpserverv4scopestatistics)、[Fortinet 性能命令](https://docs.fortinet.com/document/fortigate/7.6.0/administration-guide/152469/checking-cpu-and-memory-resources)、[PAN-OS 管理命令](https://docs.paloaltonetworks.com/ngfw/pan-os-cli-quick-start/cli-cheat-sheet-device-management)、[Cisco ASA 查询命令](https://www.cisco.com/c/en/us/td/docs/security/asa/asa-cli-reference/S/asa-command-ref-S/show-f-to-show-ipu-commands.html)。

### 1.9.45 跨模块关联反查

- 虚拟机列表「IP / 硬件反查」：主 IP 和附加 IP 匹配已登记规划 IP 及所属网段（支持 IPv4/IPv6），并查看已绑定宿主硬件、机柜和站点。
- IP 地址规划：网段和 IP 行增加「虚拟机反查」，同 IP 多台虚拟机全部列出并提示冲突，不覆盖原设备关联、不自动登记地址或改变利用率。
- 服务器存储「虚拟机 / 机柜反查」和数据中心「关联资产」：按现有 host_id、rack_id、site_id 双向查看，可连续反查或进入对应模块。
- 虚拟机宿主硬件在编辑表单选择；硬件机柜及 U 位在服务器存储编辑表单维护。同名但尚未绑定的宿主仅作为候选展示；没有用名称猜测并改写关联。修复显式取消宿主关联无效的问题。
- 仅展示账号有权限访问的目标模块。全部反查是只读功能，不修改数据库结构、现有记录或同步配置。这里的硬件关系是宿主机关系，不把 vCenter 的逻辑 datastore 名称自动当作物理存储设备。

### 1.9.44 IP 地址清单修复

- 修复 IPv4 地址范围反查遇到 IPv6 设备时抛异常、导致新增或列表 HTTP 500 的问题；混合版本或倒序范围保留原文但不用于自动关联。
- 取消关联设备正确保存 NULL，不再将整数字段写成空字符串；兼容旧版已保存的空字符串记录，无须删数据。
- IP 清单文本字段从 4000 字符放宽至 65536 字符（不是字节），CSV 请求单独放宽至 1000 万字符，避免整份导入文件被普通文本字段限制拦截。

### 1.9.42 清单管理更新

1.9.43 修复：公司维护、邻居选择及驱动维护弹窗恢复可见；设备详情页增加逐条添加邻居和“进入设备继续发现”链接。操作流程：打开设备 A → 发现邻居 → 添加需要的 B → 进入 B → 发现并添加 C，可按需重复。发现本身不自动添加设备，也不递归扫描。已管理的邻居可直接进入，附加 IP 也能匹配已有设备。公司弹窗每行维护一个名称，保存后更新下拉框。升级后若浏览器仍使用旧脚本，请按 Ctrl+F5 刷新。

- 设备列表的「CDP / LLDP 邻居」支持单台刷新、勾选和全选可添加邻居，不递归自动添加。按平台、能力及名称识别 IPT 电话、VG 网关、交换机、AP、控制器和路由器；无法识别时归入「待识别设备」。只对新增邻居分组，不覆盖已有手工分组。电话和未知设备默认仅建档，可在编辑中选择是否参与自动 SSH 任务。
- 「维护公司」按行维护公司下拉框，现有公司自动保留，正在使用的公司不能直接移除。
- 设置中的设备类型支持重命名、维护 SSH 驱动和删除未使用的自定义类型。类型名称与连接驱动分离；新类型继承原类型的驱动。旧自定义类型优先匹配内置命令模板，否则兼容原 IOS 默认模板，可手动校正驱动。内置类型保留，避免破坏默认配置。
- 设备和虚拟机清单支持多选删除，表头全选仅作用于当前筛选结果；删除需确认，设备删除会移除关联备份记录、采集记录和邻居记录。不会删除或关机真实设备/虚拟机。外部虚拟机记录删除后，下次同步可能重新导入。
- 设备筛选按公司、分组、连接类型、在线状态组合，关键词支持名称、主/附加 IP、型号、用途。搜索请求按最新结果展示，避免旧请求覆盖新筛选。
- 平台同步跳过明确标记为模板或已关机的虚拟机，保留已有本地记录且不误标失联。vCenter 提供这些状态；Zabbix 的启用监控状态不能用来判断真实开关机，缺少明确电源数据时不会猜测排除。
- 平台集成页增加定期同步入口，进入「定时任务」选择 Zabbix / vCenter 资产同步、配置时间并启用。不会擅自修改已有任务周期。

本版沿用现有数据库结构，公司目录保存于 `system_settings`，类型驱动保存于数据目录的 `commands.json`。升级前请通过平台数据备份保存数据库和命令配置，并单独安全保管原密钥（`.secret_key` 或配置的环境变量）；平台备份包不会导出密钥。保留原数据目录，不要使用卸载并清除数据的方式升级。

| 功能 | 说明 |
|------|------|
| **自动备份配置** | 通过 SSH 定时备份 Cisco 设备 running-config，支持版本对比和变更检测 |
| **配置变更中心** | 汇总配置漂移，支持基线、差异查看和预期/异常/忽略审核闭环 |
| **平台集成** | 连接 Zabbix 与 VMware vCenter，支持差异预演、虚拟机/存储关系、定时同步、重试和失联治理 |
| **安全升级** | 升级前数据库检查与快照、迁移状态记录、安装后版本健康验证，失败自动恢复上一程序版本 |
| **邻居发现** | 解析 CDP / LLDP 邻居信息，自动建立设备间关系 |
| **设备信息采集** | 自动采集版本号、序列号、型号、接口信息，并通过序列号编码规则估算生产日期 |
| **拓扑可视化** | 基于 vis.js 生成交互式网络拓扑图，支持缩放、拖拽、节点详情 |
| **定时任务** | APScheduler 支持 Cron 表达式调度，可配置备份/发现/采集的自动执行时间 |
| **Web 管理界面** | 完整的 Web UI，支持设备 CRUD、CSV 批量导入导出、配置查看与下载 |

## 支持的设备类型

| 设备类型 | Netmiko device_type | 说明 |
|----------|-------------------|------|
| Cisco IOS 交换机 | `cisco_ios` | Catalyst 系列 (2960, 3560, 3750, etc.) |
| Cisco IOS-XE 交换机 | `cisco_ios_xe` | Catalyst 9000 系列 |
| Cisco NX-OS | `cisco_nxos` | Nexus 系列 |
| Cisco WLC | `cisco_wlc` | 无线控制器 (AireOS) |
| Cisco AP | `cisco_ap` | 独立 AP (IOS-based) |

## 快速开始

### 1. 安装依赖

```bash
# Windows
install.bat

# 或手动安装
pip install -r requirements.txt
```

### 2. 启动服务

```bash
# Windows
start.bat

# 或手动启动
python run.py

# 指定端口
python run.py --host 0.0.0.0 --port 9000
```

### 3. 访问界面

- **管理界面**: http://localhost:9632
- **API 文档**: http://localhost:9632/docs（登录后仅超级管理员可访问）
- **健康检查**: http://localhost:9632/health

多网卡主机接入 Zabbix 时，在「平台集成 → Zabbix → 出口网卡（源 IP）」选择能
访问 Zabbix API 的本机网卡，保存后再执行「测试连接」。选择结果会持久化，手动
获取和定时同步都会使用该源 IP。系统信息优先读取 Zabbix Host Inventory；若资产
清单未启用，会自动回退到 `system.sw.os[...]` 和 `system.uname` 监控项。
也可在「设置 → 网卡列表」把一块在线网卡设为平台默认出口；新建设备、凭据配置
和未单独指定出口的 Zabbix 配置会自动继承，单个对象仍可选择其他网卡覆盖。

首次启动使用账号 `admin`。系统会生成随机强密码并写入数据目录下的
`initial_admin_password.txt`；登录后请立即修改密码，修改成功后该文件会自动删除。

### 4. 添加设备

- **单个添加**: 在「设备管理」页面点击「添加设备」
- **批量导入**: 准备 CSV 文件（参考 `sample_devices.csv`），点击「导入CSV」

CSV 格式:
```
name,ip_address,device_type,username,password,enable_password,port,group_name
SW-Core-01,10.0.0.1,cisco_ios,admin,cisco123,cisco123,22,核心层
```

### 5. 执行操作

- 在「仪表盘」点击「立即备份全部」或「立即发现邻居」
- 在「设备管理」选择设备后执行批量操作
- 在「拓扑图」查看自动生成的网络拓扑
- 在「设置」配置定时任务的 Cron 表达式

## 架构设计

```
┌─────────────────────────────────────────────────┐
│                   Web UI (浏览器)                 │
│  仪表盘 │ 设备管理 │ 拓扑图 │ 配置备份 │ 设置    │
└────────────────────┬────────────────────────────┘
                     │ HTTP API
┌────────────────────┴────────────────────────────┐
│              FastAPI 后端 (Python)               │
│  ┌─────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ Routers │ │ Services │ │ Scheduler (APSc) │  │
│  └────┬────┘ └────┬─────┘ └────────┬─────────┘  │
│       │           │                │             │
│  ┌────┴───────────┴────────────────┴──────────┐ │
│  │           SSH Service (Netmiko)             │ │
│  │     SSH ──── Cisco Switches / WLC / AP     │ │
│  └─────────────────────────────────────────────┘ │
│         ┌─────────────────────────┐              │
│         │   SQLite Database       │              │
│         │  (设备/备份/邻居/任务)    │              │
│         └─────────────────────────┘              │
└──────────────────────────────────────────────────┘
```

## 技术栈

- **后端**: Python 3.13 + FastAPI + Uvicorn
- **网络自动化**: Netmiko (SSH) + TextFSM 解析
- **平台集成**: Zabbix JSON-RPC API + VMware pyVmomi/vSphere API
- **数据库**: SQLAlchemy + SQLite
- **调度器**: APScheduler (Cron 表达式)
- **拓扑图**: NetworkX (图算法) + vis.js (可视化)
- **前端**: Jinja2 + 原生 CSS/JS

## 序列号生产日期估算说明

Cisco 序列号的生产日期编码 **未公开文档化**，本系统采用基于社区研究的启发式方法进行估算：

1. **产地代码识别**: 序列号前3位为制造地代码（如 FOC=墨西哥富士康，FXS=深圳富士康）
2. **序列号范围估算**: 根据序列号数字部分的大小区间推断大致生产年份
3. **周编码检测**: 部分序列号包含生产周次信息

> ⚠️ 估算结果仅供参考，精确生产日期请以采购记录或 Cisco 保修查询为准。

## 配置文件

系统配置在 `app/config.py` 中，支持环境变量覆盖（前缀 `NETMGR_`）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `9632` | 监听端口 |
| `DEBUG` | `False` | 调试模式 |
| `SECRET_KEY` | 每次安装随机生成并持久化 | 会话签名与凭据加密密钥；也可用 `NETMGR_SECRET_KEY` 指定（至少 32 字符） |
| `COOKIE_SECURE` | `False` | HTTPS 部署时应设为 `True` |
| `MAX_CONCURRENT_SESSIONS` | `20` | 最大并发 SSH 会话数 |
| `SSH_TIMEOUT` | `30` | SSH 超时（秒） |
| `DEFAULT_BACKUP_SCHEDULE` | `0 2 * * *` | 默认备份计划（每日2点） |
| `DEFAULT_DISCOVERY_SCHEDULE` | `0 3 * * *` | 默认发现计划（每日3点） |
| `DEFAULT_INFO_SCHEDULE` | `0 4 * * 1` | 默认采集计划（每周一4点） |

## API 接口

所有业务 API 均需要登录并通过模块权限校验。Swagger 文档： http://localhost:9632/docs

主要接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/devices` | 设备列表 |
| POST | `/api/devices` | 添加设备 |
| POST | `/api/devices/import-csv` | CSV 批量导入 |
| GET | `/api/devices/export/csv` | 导出 CSV |
| POST | `/api/backups/run` | 执行备份 |
| POST | `/api/backups/run-all` | 备份全部设备 |
| GET | `/api/backups/{id}` | 查看备份内容 |
| GET | `/api/backups/{id}/download` | 下载配置文件 |
| POST | `/api/topology/discover` | 执行邻居发现 |
| GET | `/api/topology` | 获取拓扑数据 |
| GET | `/api/topology/export/graphml` | 导出 GraphML |
| GET | `/api/dashboard` | 仪表盘统计 |
| GET/POST | `/api/schedule` | 定时任务管理 |
| GET | `/api/serial/decode/{sn}` | 序列号解码 |
| GET | `/api/integrations/config` | 获取脱敏后的 Zabbix/vCenter 配置 |
| POST | `/api/integrations/{source}/test` | 测试平台连接 |
| POST | `/api/integrations/{source}/discover` | 预览虚拟机与存储清单 |
| POST | `/api/integrations/{source}/import` | 将所选记录同步到虚拟机系统 |
| POST | `/api/integrations/{source}/preview` | 计算新增、变更、无变化及失联差异 |
| POST | `/api/integrations/{source}/sync` | 执行完整或选择性同步 |
| GET | `/api/integrations/sync-runs` | 查询同步历史与重试结果 |
| GET | `/api/integrations/storage` | 查询持久化的外部存储资产 |

## 数据存储

- **数据库**: `data/netmgr.db` (SQLite)
- **安装密钥**: `data/.secret_key`（解密数据库内凭据所必需）
- **自定义采集命令**: `data/commands.json`
- **Zabbix/vCenter 连接配置**: `data/netmgr.db`（密码使用安装密钥加密）
- **配置备份文件**: `data/backups/*.cfg`
- **导出文件**: `data/exports/`

`data/` 是版本迭代边界，程序文件可以替换，但升级程序不得覆盖或删除这个目录。
Windows 安装器会记住上次选择的数据目录；Linux systemd 固定使用
`/opt/cisco-netmgr/data`；Docker 使用 `cisco-netmgr-data` 命名卷。

Windows 原地升级时直接运行更高版本安装包，不要先卸载。v1.9.39 起，安装器会先
保留上一版程序，启动新版后同时核对 `/health` 可访问且版本号正确；若 60 秒内未
通过检查，会自动恢复并重新启动上一版。升级过程、数据库快照及迁移结果可在
「设置 → 系统信息 → 升级与数据库健康」中查看。

升级前建议停止服务并额外复制整个数据目录。不要只复制 `netmgr.db`：如果遗漏
`.secret_key`，数据库中的设备、保险库及 AD 凭据将无法解密。内置 ZIP 数据备份
不会导出安装密钥；迁移到新主机时，应通过受控、安全的方式单独迁移整个数据目录。

## 数据库迁移

数据库结构由 Alembic 版本化管理。应用启动时会先创建 SQLite 安全快照，
再自动执行 `alembic upgrade head`。从旧版本首次升级时，现有手写迁移只用于
建立 `20260902_0001` 基线，之后的结构变更必须新增迁移文件：

```bash
# 开发环境生成并检查迁移
alembic revision --autogenerate -m "describe_change"
alembic upgrade head
```

迁移文件位于 `migrations/versions/`，不要直接修改已经部署过的迁移。
每次发布前应使用旧版本数据目录做一次启动升级测试，并确认 `alembic check`
没有检测到未生成的结构变更。

## 审计日志

平台会记录登录、增删改、备份下载、恢复和密码查看等管理操作。超级管理员可在
「系统日志 → 管理操作审计」查看最近记录，也可通过
`GET /api/logs/audit` 查询。审计记录不保存请求正文、密码、令牌或 Cookie。

## 注意事项

1. 确保 SSH 凭据正确，且设备已开启 SSH/TELNET 访问
2. CDP/LLDP 需在设备上启用（`cdp run` / `lldp run`）
3. 100+ 设备场景建议调整 `MAX_CONCURRENT_SESSIONS` 以平衡性能和设备负载
4. 设备与保险库密码采用安装专属密钥的 AES-GCM 加密；请同时备份数据目录中的 `.secret_key`，丢失后无法解密已有凭据
5. 建议定期检查 `data/` 目录大小，清理旧备份

## Linux 部署

应用核心代码已跨平台（仅 Windows 安装器/服务注册相关脚本是 Windows 专属）。Linux 下有三种部署方式：

### 方式一：systemd 服务（裸机 / 虚拟机，推荐生产）

1. 在 Linux 主机上构建单文件二进制：

   ```bash
   sudo apt-get install -y python3-venv build-essential libssl-dev libffi-dev
   ./linux/build_linux.sh          # 产物: dist/CiscoNetworkManager
   ```

2. 安装为系统服务：

   ```bash
   sudo linux/install_linux.sh dist/CiscoNetworkManager
   ```

   脚本会自动：复制到 `/opt/cisco-netmgr/`、创建无登录权限系统用户 `netmgr`、初始化数据目录、写入 `cisco-netmgr.service`、放行防火墙 9632 端口并启动服务。
   日志查看：`journalctl -u cisco-netmgr -f`

### 方式二：Docker 容器（最快，单机）

```bash
docker compose -f linux/docker-compose.yml up -d
# 访问 http://<host>:9632/
```

数据持久化在命名卷 `cisco-netmgr-data`。若只要导出一个裸二进制：

```bash
docker build -t cisco-netmgr:1.9.41 -f linux/Dockerfile .
docker create --name extract cisco-netmgr:1.9.41
docker cp extract:/usr/local/bin/CiscoNetworkManager ./CiscoNetworkManager
docker rm extract
```

### 方式三：源码直接运行

```bash
pip install -r requirements.txt
python run.py --host 0.0.0.0 --port 9632
```

### Linux 构建说明

- 构建规格：`build_onefile_linux.spec`（基于 Windows 版 `build_onefile.spec`，去除了 Windows 专属参数，
  并将 `psutil` 的隐藏导入按平台切换为 `_pslinux`，产物名为 `CiscoNetworkManager` 不带 `.exe`）。
- 默认端口 9632，可用 `CiscoNetworkManager --port 9000` 或环境变量 `NETMGR_PORT` 覆盖。
- 数据目录：默认在二进制同级的 `data/`，可用 `--data-dir <路径>` 或环境变量 `CISCO_NM_DATA_DIR` 指定。
