# Cisco 网络自动化运维平台

> 自动备份配置 · 邻居发现 · 设备信息采集 · 拓扑可视化

## 功能概览

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
