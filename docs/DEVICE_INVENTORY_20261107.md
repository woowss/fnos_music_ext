# 设备只读盘点证据 · 2026-11-07 部署窗口（§1.1）

> **状态：只读盘点证据。本文件不改变 §0.1 / §0.2 的 BLOCKED 状态，也不构成任何部署、停机、安装、回滚或写操作的授权。**

## 0. 抬头与声明

| 项目 | 内容 |
| --- | --- |
| 采集时间（JST） | 2026-11-07 02:15–02:28 JST（= 2026-11-07 01:15–01:28 CST，设备时区 Asia/Shanghai）。逐节采集时刻见各节首行。 |
| 采集者 | `device-inventory`（DSH Agent Team 独立执行者；未参与实现/部署，独立于 Lead） |
| 对象设备 | fnOS NAS，主机名 `kotomi`（`uname -n`），Linux 6.18.18.c1168-trim x86_64，fnOS/TRIM 版本 `1.2.0903`（`/usr/trim/etc/version`；`/etc/fnos-release` 不存在） |
| 采集方式 | 本机 PowerShell → `ssh -o BatchMode=yes kotomi-root 'bash -s'`（root、key-only、已配置端口；脚本以 base64 经 stdin 传输，避免引号/CRLF 破坏）；设备侧未落任何脚本/临时文件 |
| 窗口绑定声明 | 本文件所有事实均在本窗口内从设备实时采集，命令与输出见各节；**未沿用任何历史记录**。采集前后未发生服务启停（服务启动时间 2026-11-01，见 §3）。 |
| 只读声明 | 全程只执行读取类命令：`cat/grep/stat/find/ls/readlink/findmnt/getfacl/du/sha256sum/ss/ps/docker ps\|inspect\|top/systemctl status\|show/journalctl/curl GET`。**未** stop/start/restart 服务，**未** docker restart，**未**改配置，**未**安装/升级/回滚 FPK，**未**写 /vol1，**未**在设备上创建文件，**未**对任何 jobs DB 打开连接（路径未知），**未**调用任何写接口。唯一一次带 `x-trim-isadmin` 头的请求是 §9 的**只读 GET status** 烟雾交叉核对（与检查单 §4.3 同款），不构成身份或授权证明。 |
| 原始输出留存 | 逐次命令的完整原始输出保存在执行机本地：`C:\Users\admin\AppData\Local\Temp\fnmusic-inv\out-01.txt` … `out-13.txt`（未提交仓库；可复核）。本文件为脱敏摘录。 |

### 0.1 结论摘要

| # | 检查项 | 结论 | 是否阻断 |
| --- | --- | --- | --- |
| 1 | 平台版本/内核/运行时 | **observed**（见 §1） | 否 |
| 2 | 扩展包安装位置/版本/指纹 | **observed**：`/vol1/@appcenter/fnmusic-ext/repo`，VERSION `2.10.2`（见 §2） | 否 |
| 3 | 当前安装版本是否含 P1/P2/P4/P5/P6 整歌单代码 | **observed：不包含**（见 §2.3） | 是（部署前必须新包） |
| 4 | service unit / drop-in | **observed**：`fnmusic-ext.service`，enabled+active，无 drop-in（见 §3） | 否 |
| 5 | 容器 / worker | **observed**：仅 `fnmusic-sources` 容器（healthy），无独立网易云容器/worker（见 §4） | 否 |
| 6 | 4 个配置键存在性 | **observed**：`ENABLED` 存在且为 true；`PLAYLIST_ENABLED`/`JOBS_DB`/`MANIFEST_READONLY` 均 **absent**（见 §5） | 是 |
| 7 | 总开关 `FNMUSIC_MANUAL_SAVE_ENABLED` 生效值 | **observed = true**（repo `.env` 来源；运行期 GET status 交叉确认 enabled=true） | **是** |
| 8 | 整歌单开关 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED` 生效值 | 按目标代码缺省解析 = false；但键在 `.env`/进程环境/unit 中均缺失，当前安装版本无该消费者 → 运行期不可交叉验证。按 §0.2「缺失」→ **关闭门不满足** | **是** |
| 9 | jobs DB 实际路径 | **unknown**（两个来源均无 `FNMUSIC_MANUAL_SAVE_JOBS_DB`；按 §1.1 停止该项，不猜默认位置） | **是** |
| 10 | jobs DB 及 -wal/-shm/-journal 只读属性 | **unknown / 不适用**（路径未知；安装树内无 jobs DB 文件，见 §6） | 是（同 #9） |
| 11 | 曲库目录 | **observed**（见 §7） | 否 |
| 12 | 暂存目录 | **observed**：空（0 文件），见 §7 | 否 |
| 13 | 曲库/暂存同文件系统原子发布前提 | **observed**：同一 btrfs 挂载、`st_dev` 相同（见 §7.3；仅静态前提，未做 rename 实测） | 否 |
| 14 | 在途整单任务 / DB 写者 / 写开关关闭 | 采样时**未见**在途任务或写者；但**总写开关为 true**、无持久任务队列证据、journal 已轮转 → 不能证明无写入 | **是** |
| 15 | 8774 监听与健康端点 | **observed**（见 §9.1） | 否 |
| 16 | Unix socket 规范路径/监听进程/父目录权限 | **observed**（见 §9.2） | 否（访问控制本身未验证） |
| 17 | 无法确定项 | 见 §10（12 项） | — |

---

## 1. 平台

**采集时刻：2026-11-07T02:15:21+0900（JST）/ 01:15 CST。**

| 事实 | 命令（摘要） | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| fnOS/TRIM 版本 | `cat /usr/trim/etc/version`；`cat /etc/fnos-release`；`ls /etc \| grep -iE 'fnos\|trim\|version\|release'` | `/usr/trim/etc/version` → `1.2.0903`；`/etc/fnos-release` 不存在；`/etc` 下仅有 `trim/` 目录 | **observed**（单一来源） | §0.1-1 |
| 发行版 | `head -6 /etc/os-release`；`hostnamectl` | `PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"`；`Operating System: Debian GNU/Linux 12 (bookworm)` | observed | §0.1-1 |
| 内核/架构 | `uname -a` | `Linux kotomi 6.18.18.c1168-trim #1168 SMP PREEMPT_DYNAMIC Tue Oct 13 11:20:25 UTC 2026 x86_64 GNU/Linux` | observed | §0.1-1 |
| 硬件 | `hostnamectl` | `Hardware Vendor: BIOSTAR Group`，`Hardware Model: H110MHV3`，`Firmware Version: 5.12` | observed（辅助） | §0.1-1 |
| Python | `python3 --version`；`readlink -f .venv-proxy/bin/python` | `Python 3.11.2`；venv 解释器 → `/usr/bin/python3.11` | observed | §0.1-1 |
| proxy venv 依赖 | `.venv-proxy/bin/python -m pip list`（仅筛运行相关包） | `fastapi 0.162.0`、`uvicorn 0.54.0`、`httpx 0.28.1`、`mutagen 1.48.1`、`pydantic 2.14.2`、`starlette 1.10.0` | observed | §0.1-1 |
| PHP | `php -v` | `bash: php: command not found` | observed（未安装） | §0.1-1 |
| Node（宿主 PATH） | `node --version` | `bash: node: command not found`（`/vol1/@appcenter/nodejs_v24` 目录存在，但不在 root PATH） | observed（宿主 PATH 无） | §0.1-1 |
| Docker | `docker --version` | `Docker version 28.5.2, build ecc6942` | observed | §0.1-1 |
| sqlite3 CLI | `sqlite3 --version` | `bash: sqlite3: command not found`（宿主无 CLI；本盘点未需要查库） | observed | §6 |
| 容器 Python | `docker top fnmusic-sources` | 容器内 `uvicorn` 以 `/usr/local/bin/python3.13` 运行 | observed | §0.1-1 |

> 说明：fnOS 版本只有一个可读来源（`/usr/trim/etc/version` = `1.2.0903`）；`/etc/fnos-release` 等其它候选文件不存在。未据此推断更多版本信息。

---

## 2. 扩展包

**采集时刻：2026-11-07T02:15:21+0900 / 02:28:00+0900（JST）。**

### 2.1 安装位置与顶层结构

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 安装根目录 | `stat`、`ls -la /vol1/@appcenter/fnmusic-ext` | `path=/vol1/@appcenter/fnmusic-ext type=directory mode=775 owner=root:root size=56 device=55 mtime=2026-11-01 17:18:30 +0800`；顶层：`config/`、`fnmusic-ext.sock`、`repo/`、`ui/` | **observed** | §0.1-1 |
| 代码目录（repo） | `ls -la /vol1/@appcenter/fnmusic-ext/repo` | 存在 `proxy/`、`musicbox-service/`、`webui-service/`、`container/`、`tools/`、`VERSION`、`.env`、`.env.bak*`、`.venv-proxy/`、`sources-data/` 等 | observed | §0.1-1 |
| 应用中心权限/资源声明 | `cat config/privilege`、`cat config/resource` | `{"defaults":{"run-as":"root"}}`；`{"api-scope": ["trim.file.userAccess"]}` | observed | §3 |
| UI 网关声明 | `cat ui/config` | `"gatewaySocket": "fnmusic-ext.sock"`，`"gatewayPrefix": "/app/fnmusic-ext"`，`"url": "/app/fnmusic-ext/"`，`"allUsers": false` | observed | §9.2 |

### 2.2 版本与关键文件指纹

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 已安装版本 | `cat repo/VERSION`；`sha256sum repo/VERSION` | `2.10.2`；sha256 `06674b960b46258f58e6d6ddddb319ae69dff8da28a6b9a3f605e3f74e2244ef`，mode 775 root:root，8 bytes，mtime `2026-11-01 15:24:54 +0800` | **observed** | §0.1-1 |
| 关键文件哈希（安装树） | `sha256sum` + `stat`（逐文件） | `proxy/manual_save_route.py` `f60460b49114c0c49412a7830c924bd30dd4226f96148b1876f25cfa37502c6e`（89720B）；`proxy/webui_gateway.py` `48b51e931817f79b2ffbdca55ba56f7abfb6b3472429fcbece1ddd65ffebcbc9`（60858B）；`proxy/manual_save_jobs.py` `7a9a3d3343630e357bbe574bf4df6badbde1240fe39c3605ce6bb2dce30a01f5`（38380B）；`proxy/manual_save_playlist.py` `688d5a8fb787959f22ea2b35e753f582c8cbfea90d1dc47fd31ecc5f6a589702`（21389B）；`musicbox-service/app.py` `3695794d1b381260f0af6403b8cca55859277ab55602cb9c3f2864562b84c679`；`musicbox-service/playlist_manifest.py` `7e4de19310c416ca5d2d351426b2fbe3bda5e32b44c489c5a7eb7fd65d8a8886`；`webui-service/app.py` `baff197db776f4bed4596bf428f2c5884c6f75b576422d6d774a7beeaa53519a`；`webui-service/static/app.js` `22cde4ea84ff5477cb2549cdb33ce527d57d2fe7e18af5554f74eb22b4a861bf`；`webui-service/static/index.html` `c2d3a2475147b0fe1c32bd46771f7c3be3b671c77c7ceaac39194e7ab353f0b8`；`container/entrypoint.sh` `ba919b961624ee5220de10139213cc5c29c91883fc2b29a1d00f108e6903b950`；`container/env_flag.sh` `2f0fd5cd2e85af14029f686fd6ad1379408f26ab5dec63174773542c5363e394` | observed | §0.1-1 |
| 已安装 unit 渲染文件 | `sha256sum /etc/systemd/system/fnmusic-ext.service` | 见 §3（`f6b2a6d0…`，788B） | observed | §0.1-1 |
| `.env` 身份（不输出内容） | `stat`、`sha256sum repo/.env` | `type=regular file mode=600 owner=kurumi:Administrators size=2663 mtime=2026-11-01 17:01:47.050890768 +0800`；sha256 `ccc4c872c8f887a6346b1c48362566c1b1b20b3801cf3193a52329cba21de4da` | observed（仅指纹/属性） | §1.1-1/§1.2 |
| `.env` 备份文件 | `ls -la repo/.env*` | `.env.bak.20261011160606`（600 root:root，1102B，10-11 16:05）；`.env.bak.20261101152455`（**000** kurumi:Administrators，1579B，10-21 23:31）；`.env.bak.20261101155312`（600 kurumi:Administrators，1579B）；`.env.pre-manual-save.20261101162531`（600 kurumi，2076B，11-01 15:59）；`.env.webui.bak`（600 kurumi，2331B，11-01 17:01）；`.env.example`（775 root:root，19771B） | observed（仅属性；未读取内容） | §1.2 风险 |
| FPK 候选（非安装目录内） | `stat`、`sha256sum` | `/vol1/1000/fnmusic-guard-accept-20261101/packages/fnmusic-ext-2.10.2.fpk` 28,072,710B sha256 `77bb7566609d80e2172fea503bfd75ce112ad423ad1c6c5ccb94d5d1d4029a0c`；`…/fnmusic-ext-2.8.2-rollback.fpk` 27,170,364B sha256 `1379e2e9214c1ec887e14d0bcfc2befe92453d753643eb9a9531fca7822fdd85`；`/vol1/1000/下载备份/ndm/fnmusic-ext-2.8.2.fpk` 27,173,490B sha256 `a6aa066217e4f5929960acf438e3b49a4af30649c44c4bf4471848754f9df670` | observed（**与安装树的对应关系 unknown**，见 §10-2） | §1.1-3 |

### 2.3 包内容对照：当前安装版本是否包含 P1/P2/P4/P5/P6 整歌单代码

**采集时刻：2026-11-07T02:15:21+0900（JST）。** 仅 `grep`/`ls` 只读核对，未执行安装树内代码。

| 阶段 | 检查方式 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| P1 账本 reason 扩展 | `grep` 安装树 `proxy/manual_save_jobs.py` 的 `_OUTCOME_REASONS` 路由失败码 | 路由码 `service_unavailable`/`no_quality_available`/`quality_unverified`/`download_failed`/`integrity_mismatch`/`invalid_audio`/`tag_failed`/`disabled`/`config_invalid`/`busy` 命中数均为 **0**；仅既有 `stage_unavailable`=1 | **不包含** |
| P2 快照 producer | `ls`/`grep` `musicbox-service/` | `netease_playlist_snapshot.py` **ABSENT**；`requirements.txt` 仍为 `NetEase-MusicBox>=0.5.3`（未钉 `==0.5.3`）；`musicbox-service/app.py` 无 `member-snapshot` 端点 | **不包含** |
| P4 适配层 | `ls` `proxy/manual_save_playlist_jobs.py` | **ABSENT** | **不包含** |
| P5 网关 6 端点 | `grep -c 'manual-save/playlist' proxy/webui_gateway.py` | 命中 **0** | **不包含** |
| P6 管理页入口 + 容器导出键 | `grep -c 'manual-save/playlist' webui-service/static/app.js`；`grep FNMUSIC_PLAYLIST_MANIFEST_READONLY container/env_flag.sh` | app.js 命中 **0**；`env_flag.sh` 无该键导出（`export_source_env` 键列表中无 READONLY）；存在 `app.js.bak-manual-save-170533`（单曲时代备份） | **不包含** |
| 汇总 token 交叉 | `grep -rF` 安装树 proxy/musicbox-service/webui-service/container | `netease_playlist_snapshot`=0、`PlaylistJobService`=0、`manual_save_playlist_jobs`=0、`manual-save/playlist`=0、`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`=0、`FNMUSIC_MANUAL_SAVE_JOBS_DB`=0 | 与上表一致 |

**结论（observed）：** 当前安装的 **2.10.2** 不包含本轮 P1/P2/P4/P5/P6 整歌单保存代码；与检查单 §0「2.10.2 包内容对照」的历史结论一致，但本次为**本窗口设备实测**。

---

## 3. 服务 unit 与 drop-in

**采集时刻：2026-11-07T02:17:55+0900（JST）/ 01:17 CST。**

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 实际 unit 名 | `systemctl list-unit-files \| grep -iE 'fnmusic\|netease\|musicbox'`；`systemctl list-units --all` | 仅 `fnmusic-ext.service`（`enabled enabled`；`loaded active running "fnmusic-ext Proxy Service (Socket Takeover)"`） | **observed** | §0.1-1 |
| 运行状态 | `systemctl status fnmusic-ext.service --no-pager -l` | `Active: active (running) since Sun 2026-11-01 17:18:30 CST; 5 days ago`；`Main PID: 1870197 (python3)`；`Tasks: 6`；`Memory: 93.4M` | **observed** | §0.1-1 |
| 最近启动时间 | `systemctl show … -p ExecMainStartTimestamp` | `ExecMainStartTimestamp=Sun 2026-11-01 17:18:28 CST` | **observed** | §0.1-1 |
| unit 文件路径/身份 | `stat`、`sha256sum /etc/systemd/system/fnmusic-ext.service` | `type=regular file mode=600 owner=root:root size=788 mtime=2026-11-01 16:43:29 +0800`；sha256 `f6b2a6d086ab920db7ecef62c18819734d4d764309a5ed7860ada744e76310a9`；`FragmentPath=/etc/systemd/system/fnmusic-ext.service` | observed | §0.1-1 |
| drop-in | `systemctl show -p DropInPaths`；`ls /etc/systemd/system/fnmusic-ext.service.d`；`ls /run/systemd/system/fnmusic-ext.service.d` | `DropInPaths=`（空）；两个目录均 **absent** | **observed（无 drop-in）** | §0.1-1 |
| 关键只读字段 | `grep` 安全字段 + `systemctl show -p` | `Type=simple`；`User=root`；`Group=`（空）；`WorkingDirectory=/vol1/@appcenter/fnmusic-ext/repo`；`ExecStart=/bin/bash "/vol1/@appcenter/fnmusic-ext/repo/proxy/run_proxy.sh"`；`ExecStartPost=/usr/bin/python3 ".../proxy/takeover.py" ready --timeout 65`；`ExecStartPost=/usr/bin/python3 ".../proxy/webui_gateway.py" ".../repo"`；`ExecStopPost=... takeover.py restore`；`TimeoutStartSec=75`；`TimeoutStopSec=20`；`KillMode=mixed`；`Restart=no`；`UMask=0077`；`WantedBy=multi-user.target`；`UnitFileState=enabled`；`NeedDaemonReload=no` | **observed** | §0.1-1/§0.1-3 |
| 4 个配置键是否在 unit/drop-in | `grep -nE 'FNMUSIC_MANUAL_SAVE_ENABLED\|…PLAYLIST_ENABLED\|…JOBS_DB\|…MANIFEST_READONLY' /etc/systemd/system/fnmusic-ext.service`（+ 无 drop-in） | **无命中**；unit 无 `EnvironmentFile=` | **observed（unit 未承载这 4 键）** | §1.1-1 |
| 进程树 | `systemctl status` CGroup；`ps -eo …` | `1870197 /usr/bin/python3 .../proxy/takeover.py run --base .../repo`；`1870225 .venv-proxy/bin/python -m uvicorn app:app --app-dir .../proxy --uds /run/fnmusic-ext/proxy-ntd48y9d/listen.sock --no-access-log`；`1870256 /usr/bin/python3 .../proxy/webui_gateway.py .../repo` | observed | §0.1-3 |

> 说明：按 §1.1 要求，未打印 unit 中其余 `Environment` 行，仅筛上述 4 个键（无命中）。

---

## 4. 容器 / worker

**采集时刻：2026-11-07T02:17:55+0900 / 02:25:28+0900（JST）。**

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 容器清单 | `docker ps`；`docker ps -a \| grep -iE 'fnmusic\|netease\|musicbox'` | 仅 `fnmusic-sources`（image `fnmusic-sources:latest`，`Up 5 days (healthy)`，`127.0.0.1:8768->8001`、`8770->8002`、`8772->8003`、`8774->8004`）；**无独立“网易云/netease”容器** | **observed** | §0.1-1/§0.1-3 |
| 容器身份 | `docker inspect -f` | `name=/fnmusic-sources state=running health=healthy started=2026-11-01T08:43:17.394729123Z`（=16:43:17 CST）；image `sha256:16d5e8ef536363130ede5ff3df7e4b45890f3a30eba9598dd15b61c6c02580a3`；image created `2026-11-01T15:51:21+08:00`；label `io.fnmusic-ext.build-fingerprint=c50b9cc23e6b639cb8bf145d4eee25e1dbe824382098f78ad24b36172fa93a7f`；compose project `repo`/service `fnmusic-sources`/version 2.40.3 | observed | §0.1-1 |
| 容器挂载 | `docker inspect -f '{{range .Mounts}}…'` | `bind /vol1/@appcenter/fnmusic-ext/repo → /repo rw=true`；`bind /vol1/@appcenter/fnmusic-ext/repo/sources-data → /data rw=true` | observed | §0.1-1/风险 R3 |
| 容器健康 | `docker inspect -f '{{json .State.Health}}'` | `"Status":"healthy"`，`FailingStreak:0`，最近 5 次检查（01:23–01:25 CST 11-07）ExitCode 0 | **observed** | §1.1-6 |
| 容器内进程 | `docker top fnmusic-sources` | 用户均为 `kurumi`：`/bin/sh /usr/local/bin/entrypoint.sh`；`/usr/bin/python3 /usr/bin/supervisord`；`/usr/local/bin/python3.13 … uvicorn app:app --host 0.0.0.0 --port 8004`（WebUI）；`/usr/local/bin/python3.13 … uvicorn app:app --host 0.0.0.0 --port 8002`（musicbox） | **observed** | §0.1-1 |
| 宿主 worker 进程 | `ps -eo pid,ppid,user,etime,stat,args \| grep -iE 'fnmusic\|netease\|musicbox\|8774\|8770'` | 仅 §3 的 3 个扩展进程 + 4 个 docker-proxy（8768/8770/8772/8774）；无独立 worker | **observed** | §1.1-6 |
| 可能写 jobs DB 的进程 | 结合 §2.3（P4/P5 未安装）、§6（无 DB 路径） | 当前安装版本**没有** jobs DB 消费者/写者；若未来部署 P5，写入者将是 `webui_gateway.py`（pid 1870256）——本窗口未安装该代码 | observed（当前版本）/ 目标版本见 §6 | §1.1-6 |

---

## 5. 配置键与生效值

**采集时刻：2026-11-07T02:20:24+0900（JST）/ 01:20 CST。**

### 5.1 4 个键的存在性（仅筛选，不输出 `.env` 全文）

| 键 | 存在性（repo `.env`） | 值（脱敏） | 判定 |
| --- | --- | --- | --- |
| `FNMUSIC_MANUAL_SAVE_ENABLED` | 存在，1 次 | **true**（按代码 true-ish 集合解析） | observed |
| `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED` | **ABSENT（0 次）** | — | observed（缺失） |
| `FNMUSIC_MANUAL_SAVE_JOBS_DB` | **ABSENT（0 次）** | — | observed（缺失） |
| `FNMUSIC_PLAYLIST_MANIFEST_READONLY` | **ABSENT（0 次）** | — | observed（缺失） |

命令（摘要）：`grep -cE '^[[:space:]]*(export[[:space:]]+)?<KEY>=' repo/.env`（4 个键分别计数）；另用与 `read_env_file()` 同规则的解析脚本只打印上述 4 键 + 2 个路径键（其余键不输出）。`.env` 属性：600 `kurumi:Administrators`，2663B，mtime `2026-11-01 17:01:47 +0800`，sha256 `ccc4c872…`。

> 说明：为确定 §7 的库/暂存目录，额外只读取了两个**路径键**的值（非密钥）：`FNMUSIC_MANUAL_SAVE_TARGET=/vol1/1000/fnmusic-test-20261031`、`FNMUSIC_MANUAL_SAVE_STAGING=/vol1/1000/fnmusic-manual-save-staging`（各 1 次）。未输出其它键。

### 5.2 两个保存开关的**实际生效值**与来源

按当前安装版本与目标代码的读取规则分别核对（检查单 §1.1-2）：

| 键 | 读取规则（代码证据） | 生效来源 | 生效值 | 运行期交叉验证 |
| --- | --- | --- | --- | --- |
| `FNMUSIC_MANUAL_SAVE_ENABLED`（单曲/总开关） | 单曲 `status` 路由由 `manual_save_route.load_config()` 读 repo `.env`（`proxy/manual_save_route.py:287-291` 的 `_env_path()` = `<repo>/.env`；`:320` `load_config`；true-ish = `true/1/yes/on`，缺省 false） | **repo `.env`**（`/vol1/@appcenter/fnmusic-ext/repo/.env`，mtime 17:01:47；gateway 进程 1870256 启动于 17:18:28，晚于该 mtime） | **true** | ✅ 通过网关 Unix socket `GET /app/fnmusic-ext/api/manual-save/status`（只读，§4.3 同款烟雾头）→ `200 {"ok":true,"code":"ok","data":{"enabled":true,"target_dir":"/vol1/1000/fnmusic-test-20261031","staging_dir":"/vol1/1000/fnmusic-manual-save-staging",…}}` |
| `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`（整歌单开关） | 目标代码（HEAD）网关：repo `.env` 优先，键缺失才回退**进程环境**（`proxy/webui_gateway.py:1437-1461`；`proxy/manual_save_playlist_jobs.py:879/…load_service_config`）；缺省 false | **键缺失**（repo `.env` 0 次；gateway 进程 1870256 的 `/proc/<pid>/environ` 无该键；uvicorn 1870225 无；unit/drop-in 无） | 按缺省解析 = **false**；但**键缺失 → §0.2 关闭门不满足**；当前安装版本无该功能消费者，运行期无真实“生效值”可交叉验证 | ⚠️ 不可交叉验证（P5 未安装；`GET …/manual-save/playlist/playlists` 经网关转发返回 `501` 固定文案） |
| `FNMUSIC_MANUAL_SAVE_JOBS_DB` | 同上（repo `.env` 优先，缺键回退进程环境；路径须为 POSIX 绝对路径） | **两来源均缺失** | **unknown（路径无法确定）** | — |
| `FNMUSIC_PLAYLIST_MANIFEST_READONLY` | musicbox producer 读**进程环境**（`musicbox-service/netease_playlist_snapshot.py:106/139` `producer_enabled`，true-ish 才启用）；容器 `entrypoint.sh`/`env_flag.sh export_source_env` 从 `/repo/.env` 导出该键（`container/env_flag.sh`） | repo `.env` **缺失**；容器 init（pid 1808892）`/proc/<pid>/environ` **无该键** | 按缺省 = **false**；当前安装版本无 producer（P2 未安装）、容器也无该键 → 无运行期消费者 | ⚠️ 不可交叉验证（P2/P6 未安装） |

补充观测（来源一致性）：
- gateway 进程 1870256：4 键在 `/proc/1870256/environ` 中**均不存在**（它按请求读 repo `.env`）。
- uvicorn 进程 1870225：`FNMUSIC_MANUAL_SAVE_ENABLED=true`（由启动链导入 `.env` 所得），与 repo `.env` 一致，**无冲突**。
- unit 文件与 drop-in：4 键均无命中；无 `EnvironmentFile=`。
- 未修改、未临时关闭、未重启任何服务/开关（遵守 §0.2 与 §1.1 对 true/unknown 的处置要求）。

**判定：总开关 `true` → §0.2 关闭门不满足、§1.1-6“关闭写入开关”不满足（BLOCKED）。** 整歌单开关按缺省为 `false` 但键缺失，§0.2 同样不满足（BLOCKED）。jobs DB 路径 `unknown` → §1.1-3 要求“若路径无法确定，停止部署”。

---

## 6. 账本（jobs DB）

**采集时刻：2026-11-07T02:24:21+0900 / 02:25:28+0900（JST）。**

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 实际 jobs DB 路径 | 由 §5.2 规则推出：repo `.env` 无键 → 回退 gateway 进程环境 → 亦无键；unit/drop-in 无键 | 无任何来源给出路径 | **unknown**（按 §1.1 停止该项，不猜默认值） | §1.1-3 |
| 安装树内是否存在 jobs DB | `find /vol1/@appcenter/fnmusic-ext -maxdepth 3 \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.db-journal' -o -name 'jobs*.sqlite*' \)` | 仅 `/vol1/@appcenter/fnmusic-ext/repo/sources-data/cache/netease-musicbox/nemcache.sqlite`（24576B，mtime 2026-10-11 16:17:42；musicbox 缓存，**不是** jobs DB） | observed（无 jobs DB 文件） | §1.1-4 |
| 其它候选目录 | 同上 `find` 于 `/vol1/@appdata/fnmusic-ext`、`/vol1/@apphome/fnmusic-ext`、`/run/fnmusic-ext`、`/tmp` | 均无 `*.db`/`*.db-wal`/`*.db-shm`/`*.db-journal`/`jobs*.sqlite*` 命中 | observed | §1.1-4 |
| DB 是否在会被包替换的 repo/target 下 | 无法判定（路径 unknown） | — | **unknown** | §1.1-4 |
| DB 父目录 / 本身 / `-wal`/`-shm`/`-journal` 的 `readlink -e`/`stat`/`findmnt` | 未执行（无路径；不对猜测路径做打开/连接） | — | **unknown / 不适用** | §1.1-4 |
| 当前安装版本是否使用 jobs 账本 | `grep -rn 'ManualSaveJobsLedger'` 安装树 | 仅 `proxy/manual_save_jobs.py` 中的类定义与 `__all__` 导出（**无实例化**）；`grep -rn 'manual_save_jobs' proxy` 仅其自身内部引用 | observed（当前运行代码不创建 jobs DB） | §1.1-3/4 |
| 若未来部署 P5 的写者 | 目标代码 `proxy/manual_save_playlist_jobs.py:1019`（`ManualSaveJobsLedger(self._config.db_path)`，HEAD 未安装） | 目标版本写入者为 `webui_gateway.py` 进程 | 目标版本事实，非本窗口 observed | §1.1-6 |

> 未对任何 DB 打开写连接；未创建 /tmp DB 副本（无路径可查）。因此本文件**不能证明 jobs DB 不存在**（只证明上述受检位置没有 jobs DB 文件、且当前版本不使用账本）。

---

## 7. 曲库与暂存目录

**采集时刻：2026-11-07T02:23:09+0900（JST）/ 01:23 CST。** 路径来源：§5.1 的两个路径键实际值（非默认猜测）。

### 7.1 曲库 `/vol1/1000/fnmusic-test-20261031`

| 事实 | 命令 | 脱敏输出（截取） | 判定 |
| --- | --- | --- | --- |
| 规范路径 | `readlink -e` | `/vol1/1000/fnmusic-test-20261031`（无符号链接跳转） | observed |
| 属性 | `stat -c 'type=%F mode=%a owner=%U:%G size=%s device=%d inode=%i mtime=%y'` | `type=directory mode=750 owner=kurumi:Users size=128 device=60 inode=651357 mtime=2026-11-01 12:45:54.654363477 +0800` | observed |
| 挂载 | `findmnt -T <path> -no TARGET,SOURCE,FSTYPE,OPTIONS` | `/vol1 /dev/mapper/trim_8f1e972a_e1ee_4afd_b791_e7e4fc2aa457-0 btrfs rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/` | observed |
| ACL | `getfacl -p` | `user::rwx`、`group::r-x`、`other::---` | observed |
| 文件数/目录数/总大小 | `find -xdev -type f \| wc -l`；`find -printf '%s\n' \| awk`；`du -sh` | 文件 4、目录 3、总字节 379,825,952（`363M`） | observed |
| 内容（文件名） | `ls -la`；`find -printf` | 3 个 `.flac` + 1 个 `.lrc`；最新 mtime `2026-11-01 02:14:25` | observed |
| 未完成/临时文件 | `find … \( -name '*.part' -o -name '*.tmp' -o -name '*.download' -o -name '*.partial' \)` | 无命中 | observed |
| 是否在 repo/target 下 | 路径前缀比对 | `/vol1/1000/…` vs repo `/vol1/@appcenter/fnmusic-ext` → **不在** | observed |

### 7.2 暂存 `/vol1/1000/fnmusic-manual-save-staging`

| 事实 | 命令 | 脱敏输出（截取） | 判定 |
| --- | --- | --- | --- |
| 规范路径 | `readlink -e` | `/vol1/1000/fnmusic-manual-save-staging` | observed |
| 属性 | `stat` | `type=directory mode=700 owner=root:root size=0 device=60 inode=651985 mtime=2026-11-01 16:30:16.007010762 +0800` | observed |
| 挂载 | `findmnt -T` | 同一挂载：`/vol1 … btrfs rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/` | observed |
| ACL | `getfacl -p` | `user::rwx`、`group::---`、`other::---` | observed |
| 文件数/总大小 | `find …`；`du -sh` | **文件 0**、目录 1、总字节 0（`0`） | observed |
| 未完成暂存文件 | `find` 同上 | 无（目录为空） | observed |
| 是否在 repo/target 下 | 前缀比对 | **不在** | observed |

### 7.3 同文件系统原子发布前提

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| 曲库/暂存 `st_dev` | `stat -c 'device=%d path=%n'` | `device=60 path=/vol1/1000/fnmusic-test-20261031`；`device=60 path=/vol1/1000/fnmusic-manual-save-staging` | **observed：同设备/同 btrfs 挂载**（满足 rename 原子发布的前提；**未做实际 rename 测试**） |
| 目标代码的校验规则 | 代码阅读（`proxy/manual_save_route.py:_validate_config`） | 要求 target/staging 均为目录且 `st_dev` 相等 | observed（规则），实际配置值也相等 |

### 7.4 升级脚本是否触及这些路径

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| `fnmusic_data_items()` 白名单 | `grep -rn 'fnmusic_data_items' /vol1/@appcenter/fnmusic-ext` | 仅 `CHANGELOG.md:413` 提及；安装树内**没有**该函数（安装后不保留打包脚本） | **unknown**（无法从设备核实升级脚本实际白名单；检查单 §6 的陈述未在本窗口设备上验证） |

---

## 8. 在途任务与写者

**采集时刻：2026-11-07T02:24:21+0900（JST）/ 01:24 CST。**

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 网关保存线程 | `ls /proc/1870256/task`；读 `comm` | `thread_count=1`（仅主线程 `python3`）；目标代码的单曲保存工作线程名为 `fnmusic-manual-save` → **未见** | observed（采样时刻） | §1.1-6 |
| uvicorn 线程 | `/proc/1870225/task` | `thread_count=3`（`python`×3） | observed | §1.1-6 |
| 手动保存临时目录 | `ls -lad /tmp/fnmusic-manual-save-*` | `none` | observed | §1.1-6 |
| 持有库/暂存 fd 的进程 | 扫描 `/proc/*/fd` 的 readlink，匹配两个目录前缀 | 无命中 | observed（采样时刻） | §1.1-6 |
| 打开的 SQLite 文件 | 扫描 `/proc/*/fd`，匹配 `*.sqlite*`/`*.db*` | 无 jobs DB；仅见系统/其它应用 DB 与容器内 `nemcache.sqlite`（pid 1836506，容器命名空间 `/data/cache/netease-musicbox/nemcache.sqlite`） | observed | §1.1-6 |
| 网关 socket 上的已建立连接 | `ss -x \| grep -E 'fnmusic-ext.sock\|proxy-ntd48y9d\|maintenance.sock'` | 无 established 行（仅监听） | observed（采样时刻） | §1.1-6 |
| 系统 journal | `journalctl -u fnmusic-ext --since '2026-11-01 00:00:00' \| grep -iE 'manual.?save\|playlist'` | 无命中；`systemctl status` 提示 `journal has been rotated since unit was started, output may be incomplete` | observed（受轮转限制） | §0.1-2 |
| 应用日志 | `stat` + `grep -c`（只计数，不打印内容） | `/vol1/@appdata/fnmusic-ext/fnmusic-app.log` 2,536,824B mode 644 root:root mtime `2026-11-01 16:44:00 +0800`；`manual.?save` 计数 0、`playlist` 计数 3 | observed（仅计数） | §0.1-2 |
| 持久任务队列 | §6（无 jobs DB、无队列代码） | 当前安装版本没有任务账本/队列 | observed | §0.1-2 |
| 写开关状态 | §5.2 | **总开关 = true**（可随时发起单曲保存；单曲保存同步执行、`_SAVE_LOCK` 串行） | **observed（未关闭）** | §1.1-6 |

**结论：** 采样时刻未见整单任务/写者/DB 写连接；但 **总写开关为 true**，单曲保存可在采样后任意时刻开始；journal 轮转与日志陈旧使“历史无写入”不可证。→ §1.1-6 不满足，§0.1 第 2 项不满足。

---

## 9. 只读探针

**采集时刻：2026-11-07T02:17:55+0900 / 02:20:24+0900（JST）。** 仅 GET、无副作用；未调用任何写接口。

### 9.1 8774 监听与健康端点

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 8774 监听地址 | `ss -ltnp \| grep ':8774'` | `LISTEN 0 4096 127.0.0.1:8774 0.0.0.0:* users:(("docker-proxy",pid=1808990,fd=7))`（**仅 127.0.0.1**） | observed | §0.2-3/§1.1 探针 |
| 8774 转发目标 | `docker inspect -f`（端口映射） | `8004/tcp -> 127.0.0.1:8774`；容器 `fnmusic-sources`（WebUI，容器内 `uvicorn … --port 8004`，用户 `kurumi`） | observed | §0.2-3 |
| 健康端点 | `curl -sS -m 5 -w '\nhttp_code=%{http_code}\n' http://127.0.0.1:8774/healthz` | `{"ok":true}`；`http_code=200` | **observed** | §1.1 探针 |
| 状态端点（未认证） | `curl … http://127.0.0.1:8774/api/manual-save/status` | `{"ok":false,"error":"需要管理员"}`；`http_code=403` | observed（未认证被拒；仅只读） | §1.1-2 交叉 |
| 整歌单端点（未认证） | `curl … http://127.0.0.1:8774/api/manual-save/playlist/playlists` | `{"ok":false,"error":"需要管理员"}`；`http_code=403` | observed（未认证被拒；不构成端点存在性证明） | §1.1-2 |

### 9.2 Unix socket 规范路径 / 监听进程 / 父目录权限

| 事实 | 命令 | 脱敏输出（截取） | 判定 | 对应 |
| --- | --- | --- | --- | --- |
| 规范路径 | `readlink -e /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock` | `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`（无跳转） | **observed** | §0.2-2 |
| socket 属性 | `stat -c 'type=%F mode=%a owner=%U:%G size=%s device=%d inode=%i mtime=%y'` | `type=socket mode=600 owner=root:root size=0 device=55 inode=3233706 mtime=2026-11-01 17:18:30.203644356 +0800` | **observed** | §0.2-2 |
| 监听进程 | `ss -xlpn \| grep fnmusic-ext.sock` | `u_str LISTEN … /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock … users:(("python3",pid=1870256,fd=4))`（= `webui_gateway.py`，§3） | **observed** | §0.2-2 |
| 父目录权限 | `stat`、`getfacl -p /vol1/@appcenter/fnmusic-ext` | `mode=775 owner=root:root`；ACL：`user::rwx`、`user:fnmusic-ext:rwx`、`group::r-x`、`mask::rwx`、`other::r-x` | **observed** | §0.2-2 |
| 网关状态（只读 GET，§4.3 同款烟雾头） | `curl --unix-socket <sock> -H 'x-trim-userid: 1000' -H 'x-trim-isadmin: true' -H 'x-trim-username: kurumi' -H 'x-fnmusic-save: 1' … /app/fnmusic-ext/api/manual-save/status` | `200 {"ok": true, "code": "ok", "data": {"enabled": true, "target_dir": "/vol1/1000/fnmusic-test-20261031", "staging_dir": "/vol1/1000/fnmusic-manual-save-staging", "quality_order": ["jymaster","hires","lossless","exhigh"], "max_bytes": 1073741824}}` | observed（**只读**；不构成身份/授权证明） | §1.1-2 |
| 网关状态（未认证） | 同上，无头 | `403 {"ok": false, "code": "forbidden", "message": "仅管理员可执行手动保存", "data": {}}` | observed | §0.2-3 |
| 整歌单端点（经网关，只读 GET） | `curl --unix-socket … /app/fnmusic-ext/api/manual-save/playlist/playlists`（带烟雾头） | `501 {"detail":"直连模式不支持手动保存，请在飞牛桌面内打开管理台"}` | observed（与安装版本缺少 P5 端点一致；**不是** P5 存在性的独立证明，也未调用写接口） | §1.1-2 |

> 上述带 `x-trim-isadmin` 的请求仅用于 §1.1-2 要求的“状态/禁用响应交叉确认”，全部为 GET；未对 `/track`、`/playlist/*` 等写接口发起任何请求。

---

## 10. 明确无法确定的项

| # | 无法确定项 | 原因 | 影响 |
| --- | --- | --- | --- |
| 1 | **jobs DB 实际路径** | `FNMUSIC_MANUAL_SAVE_JOBS_DB` 在 repo `.env`、gateway/uvicorn 进程环境、unit/drop-in 中均缺失；当前安装版本无 playlist 消费者；不猜默认值 | §1.1-3 直接停止项；部署 BLOCKED |
| 2 | FPK 2.10.2 候选与当前安装树的对应关系 | 设备上无安装包清单/签名；只核对到 VERSION 与文件哈希；FPK 仅作为候选指纹记录 | 不能宣称“当前安装包哈希 = 77bb75…”；§0.1-1 的“包哈希”只能给候选 |
| 3 | 容器内 `NetEase-MusicBox` 实际版本 | 未执行容器内命令（本盘点未做 docker exec）；当前安装版本无 producer 代码 | §4.7 部署后项，非本盘点结论 |
| 4 | 采样窗口之前的在途任务/写入历史 | journal 已轮转；应用日志 mtime 2026-11-01 16:44；无持久任务队列 | 只能证明采样时刻无在途；§0.1-2 不满足 |
| 5 | 升级脚本实际数据白名单（`fnmusic_data_items`） | 安装树不保留打包/升级脚本；仅 CHANGELOG 提及 | 无法从设备核实“库/暂存/账本不在升级白名单”的声明 |
| 6 | socket 访问控制的实际行为（非 root / 命名 ACL 用户） | 只做 `stat`/`getfacl`，未做访问尝试（§0.2 要求独立验证） | §0.2-2 不能据此 PASS |
| 7 | `FNMUSIC_PLAYLIST_MANIFEST_READONLY` 的运行期值 | 当前安装版本无 producer、容器进程环境无该键；目标代码缺省 false | P3 只读窗口前提不成立（需部署 P2/P6 后再验证） |
| 8 | 整歌单开关的“当前运行期生效值” | 当前安装版本无整歌单消费者；只能按目标代码缺省解析为 false | §0.2 关闭门因“缺失”已不满足，无需依赖此值 |
| 9 | 总开关 `true` 是否为有意生产状态 | 仅观测到值与来源，无变更历史 | 不能自行判断/修正；按 BLOCKED 处理 |
| 10 | 曲库/暂存同文件系统上的**实际** rename 原子性 | 只核对 `st_dev` 相同；未做 rename 实测（写操作，禁止） | 仅“静态前提 observed”，不构成原子发布验证 |
| 11 | fnOS 版本的第二来源 | `/etc/fnos-release` 不存在，仅 `/usr/trim/etc/version`=`1.2.0903` | 版本证据为单一来源 |
| 12 | 容器进程环境是否与当前 `.env` 同步 | 容器启动 2026-11-01T08:43:17Z（16:43 CST），`.env` mtime 17:01 CST（晚于容器启动）；但 READONLY 键在 `.env` 与容器环境均缺失，未观察到冲突；其它键不在本盘点范围 | 仅记录时序，不推断其它键 |

---

## 11. 阻塞 / 风险

### 11.1 阻断项（直接导致 §0.1/§0.2 不能 PASS）

1. **B1 总写开关为 true**：`FNMUSIC_MANUAL_SAVE_ENABLED=true`（repo `.env` 来源，运行期 GET status `enabled:true` 交叉确认）。§0.2 要求两个开关实际生效值均为 `false`；§1.1-6 要求关闭写入开关。**不得**为通过检查单而直接改生产 `.env`、临时关开关或重启（检查单 §0.2/§1.1 明确禁止）；按 BLOCKED 处理，等待已验证生命周期与回滚方案。
2. **B2 整歌单开关键缺失**：`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED` 在 `.env`/进程环境/unit 中均缺失；§0.2 明确“缺失”不满足关闭门（即使代码缺省为 false）。
3. **B3 jobs DB 路径 unknown**：`FNMUSIC_MANUAL_SAVE_JOBS_DB` 两来源缺失；§1.1-3 要求“若路径无法确定，停止部署”。无法盘点 DB、`-wal`/`-shm`/`-journal`、父目录、挂载与“是否在包替换路径下”。
4. **B4 当前安装版本不含整歌单代码**：P1/P2/P4/P5/P6 均不包含（§2.3）→ 任何 P3/P7 验收前必须先构建/安装新包（属 §2–§5 后续步骤，本窗口不执行）。

### 11.2 风险项（不直接阻断本盘点，但影响后续部署窗口）

- **R1 `.env` 位于 repo（会被包替换的路径）内**：`/vol1/@appcenter/fnmusic-ext/repo/.env`（600 `kurumi:Administrators`）。§1.2 要求在停机前做独立备份（仅记录文件名/时间/哈希，不复制内容到聊天）。同目录还有多个 `.env.bak*`，其中 `.env.bak.20261101152455` 权限为 **000**（内容不可读，归属 `kurumi:Administrators`）——备份/恢复时需注意。
- **R2 容器以 `kurumi` 用户运行且 repo 为 rw 绑定挂载**：`/vol1/@appcenter/fnmusic-ext/repo → /repo rw`。容器内 WebUI（8774）可读写 repo（含 `.env`）。这影响身份/路径门与“谁能改配置”的判断；本盘点未验证访问控制。
- **R3 socket 父目录权限较宽**：`/vol1/@appcenter/fnmusic-ext` 为 `775 root:root`，并带命名 ACL `user:fnmusic-ext:rwx`、`mask::rwx`、`other::r-x`；socket 本身 `600 root:root`。§0.2-2 要求实际访问控制验证，`stat` 结果不构成证明。
- **R4 曲库目录是测试目标**：`/vol1/1000/fnmusic-test-20261031`（750 `kurumi:Users`，363M，3 flac + 1 lrc）。总开关为 true 时单曲保存会写入该目录；该目录名表明是测试目录，但本盘点不判断其业务归属，**不得删除**。
- **R5 在途证据受日志轮转限制**：journal 已轮转、应用日志 mtime 停在 2026-11-01 16:44；采样时刻无在途，但无法证明窗口前/窗口内历史无写入；单曲保存同步、锁串行，可在采样后立即开始。
- **R6 升级脚本白名单不可从设备核实**：`fnmusic_data_items()` 不在安装树；若未来把 jobs DB 放在 repo/target 内，包替换风险需按 §0.1/§1.2 独立备份策略处理。
- **R7 8774 仅本机监听，但容器端口在容器内为 0.0.0.0**：宿主发布为 `127.0.0.1:8774`（observed）；容器内 uvicorn `--host 0.0.0.0 --port 8004`。本盘点不推断容器网络之外的暴露面。
- **R8 无 jobs DB 文件 ≠ jobs DB 不存在**：路径 unknown，无法排除设备其它位置存在账本；部署前必须由配置确定路径后重新盘点。
- **R9 `.env` 修改时间晚于容器启动**：`.env` mtime 2026-11-01 17:01:47 +0800，容器启动 2026-11-01 16:43:17 +0800。容器侧仅启动时从 `/repo/.env` 导出部分键（READONLY 不在其中）；本盘点只记录时序，不推断其它键是否已热更新。

---

## 12. 本次不证明

本盘点**不**证明、也不替代：

1. §0.1 的任何一项门禁完成或 PASS；**不改变 §0.1/§0.2 的 BLOCKED 状态**。
2. 停/启/重启/失败恢复方案已形成或已验证；在途任务已安全完成/取消；不会重复派发任务。
3. 任何备份/恢复验证（`.env`、jobs DB、曲库、暂存），包括 §1.2 的停机前独立备份与 SQLite backup API 快照。
4. 回滚包、降级路径、包/配置/DB 兼容性；FPK 候选与安装树的对应关系。
5. 安装/升级成功、G3 即时验证、P3 只读观察、P7 真实整歌单验收。
6. §0.2 的身份头来源、socket 实际访问控制、8774 直连拒绝边界；本文件中的带烟雾头 GET 只是只读状态交叉确认，不是身份/授权证明。
7. 构建可复现、镜像可重建、容器内依赖版本（含 `NetEase-MusicBox` 版本）、音频有效性或用户可播放。
8. jobs DB 不存在；曲库/暂存目录是否被升级脚本触及；同文件系统 rename 的实际原子性。
9. 本窗口之外的任何历史状态；也不授权本窗口之后的任何写操作。

---

## 附录 A：§0.1 / §1.1 对应关系

| 条款 | 本文件对应 | 结论 |
| --- | --- | --- |
| §0.1 第 1 项：记录设备 fnOS/扩展包版本及包哈希、服务 unit 与 drop-in、容器/worker、配置解析来源、jobs DB/曲库/暂存路径与挂载 | §1、§2、§3、§4、§5、§6、§7 | 平台/包/unit/容器/库/暂存 **observed**；包哈希仅候选（§10-2）；jobs DB 路径 **unknown** → 第 1 项**不完整** |
| §0.1 第 2 项：只读盘点在途任务/worker/DB 写者 | §8 | 采样时刻无在途；写开关 true；历史不可证 → **不满足** |
| §1.1-1：只筛 4 键、不输出 `.env` 全文/Cookie/其它环境变量/密钥 | §5.1 | **满足**（只输出 4 键 + 2 个路径键；未输出其它键/密钥） |
| §1.1-2：按读取规则确认两个保存开关实际生效值与来源 | §5.2、§9.2 | 总开关 **true**（交叉确认）；整歌单开关缺省 false 但键缺失、运行期不可交叉验证 → **关闭门不满足** |
| §1.1-3：按规则确定实际 jobs DB 路径；无法确定则停止部署 | §6 | **unknown → 停止该项/停止部署** |
| §1.1-4：DB/父目录/`-wal`/`-shm`/`-journal` 只读盘点；是否在包替换路径下 | §6 | **unknown / 不适用**（路径未知；受检位置无 DB 文件） |
| §1.1-5：曲库/暂存路径、挂载、属主/权限、文件数/大小、未完成暂存文件、同文件系统、不在 repo/target 下 | §7 | **observed**（同 `st_dev`；暂存为空；均不在 repo/target 下；升级脚本白名单见 §10-5） |
| §1.1-6：无运行中整单任务/写者，且关闭写入开关 | §8、§5.2 | 采样时无在途/写者；**写开关 true → 不满足** |

## 附录 B：本次执行的只读命令清单（摘要）

```text
# 平台/版本
TZ=Asia/Tokyo date '+%Y-%m-%dT%H:%M:%S%z (%Z)'; uname -a; hostnamectl; cat /etc/os-release
cat /usr/trim/etc/version; python3 --version; php -v; node --version; docker --version; sqlite3 --version

# 扩展包
stat/ls -la /vol1/@appcenter/fnmusic-ext; cat repo/VERSION; sha256sum repo/VERSION repo/.env 关键文件
sha256sum /vol1/1000/fnmusic-guard-accept-20261101/packages/*.fpk /vol1/1000/下载备份/ndm/fnmusic-ext-2.8.2.fpk
grep -nE 'reason' repo/proxy/manual_save_playlist.py；grep -c 'manual-save/playlist' repo/proxy/webui_gateway.py repo/webui-service/static/app.js
ls repo/musicbox-service/netease_playlist_snapshot.py repo/proxy/manual_save_playlist_jobs.py（均为 ABSENT）

# 服务
systemctl list-unit-files | grep -iE 'fnmusic|netease|musicbox'
systemctl list-units --all | grep -iE 'fnmusic|netease|musicbox'
systemctl status fnmusic-ext.service --no-pager -l
systemctl show fnmusic-ext.service -p Id,FragmentPath,DropInPaths,ActiveState,SubState,UnitFileState,MainPID,ExecMainStartTimestamp,ExecMainStatus,User,Group,Restart,WorkingDirectory,TimeoutStartUSec,TimeoutStopUSec,UMask,EnvironmentFiles,NeedDaemonReload
stat/sha256sum /etc/systemd/system/fnmusic-ext.service
grep -nE '^\[|^(Description|After|Before|Requires|Wants|Type|User|Group|WorkingDirectory|ExecStart|ExecStartPost|ExecStopPost|TimeoutStartSec|TimeoutStopSec|KillMode|Restart|UMask|EnvironmentFile|WantedBy)=' /etc/systemd/system/fnmusic-ext.service
grep -nE '<4 keys>' /etc/systemd/system/fnmusic-ext.service   # 无命中
ls -la /etc/systemd/system/fnmusic-ext.service.d /run/systemd/system/fnmusic-ext.service.d   # 均 absent

# 容器/进程
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker inspect -f '…' fnmusic-sources（State/Image/Mounts/Health/Ports）
docker top fnmusic-sources -o pid,ppid,user,args
ps -eo pid,ppid,user,etime,stat,args --sort=pid | grep -iE 'fnmusic|netease|musicbox|8774|8770'
ss -ltnp | grep -E ':(8774|8770|8768|8772)\b'; ss -xlpn | grep -iE 'fnmusic|trim_music|musicbox'

# 配置（只筛键）
stat/sha256sum repo/.env；grep -cE '^…<KEY>=' repo/.env（4 键）
与 read_env_file() 同规则的只读解析（仅打印 4 键 + TARGET/STAGING 两个路径键）
tr '\0' '\n' < /proc/<pid>/environ | grep -E '<4 keys>='（gateway/uvicorn/容器 init；仅筛选后脱敏显示）
docker inspect -f '{{range .Config.Env}}…' | grep -E '<4 keys>='（仅筛选后脱敏显示）

# 账本/目录
find <受检目录> -maxdepth 3 \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.db-journal' -o -name 'jobs*.sqlite*' \)
readlink -e/stat/findmnt -T/getfacl -p/du -sh/find -printf（曲库、暂存）
find … -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}'

# 在途
ls /proc/1870256/task; cat /proc/<tid>/comm
ls -lad /tmp/fnmusic-manual-save-*；扫描 /proc/*/fd 的 readlink（匹配库/暂存前缀、SQLite 后缀）
ss -x | grep -E 'fnmusic-ext.sock|proxy-ntd48y9d|maintenance.sock'
journalctl -u fnmusic-ext --since '2026-11-01 00:00:00' --no-pager | grep -iE 'manual.?save|playlist'
stat /vol1/@appdata/fnmusic-ext/fnmusic-app.log；grep -c -iE 'manual.?save|playlist'（仅计数）

# 只读探针
curl -sS -m 5 -w '\nhttp_code=%{http_code}\n' http://127.0.0.1:8774/healthz
curl -sS -m 5 -w '\nhttp_code=%{http_code}\n' http://127.0.0.1:8774/api/manual-save/status
curl -sS -m 5 -w '\nhttp_code=%{http_code}\n' http://127.0.0.1:8774/api/manual-save/playlist/playlists
curl -sS -m 5 --unix-socket /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock -H 'x-trim-userid: 1000' -H 'x-trim-isadmin: true' -H 'x-trim-username: kurumi' -H 'x-fnmusic-save: 1' -w '\nhttp_code=%{http_code}\n' http://127.0.0.1/app/fnmusic-ext/api/manual-save/status   # 只读 GET
curl -sS -m 5 --unix-socket /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock -w '\nhttp_code=%{http_code}\n' http://127.0.0.1/app/fnmusic-ext/api/manual-save/status   # 未认证只读 GET
```

**执行方式说明：** 所有命令经本机 `ssh -o BatchMode=yes kotomi-root 'bash -s'` 以只读脚本执行；脚本通过 base64 经 stdin 传入，设备侧未创建临时文件。未对设备做任何写操作、未启停服务、未改配置、未安装/回滚包、未连接任何写接口或 DB。
