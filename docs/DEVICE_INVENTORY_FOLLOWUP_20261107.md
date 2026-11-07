# 设备只读盘点 · 未知项消解（§1.1 续）· 2026-11-07

> **状态：只读补充证据。本文件不改变 §0.1 / §0.2 的 BLOCKED 状态，也不构成任何部署、停机、安装、回滚或写操作的授权。**

## 0. 抬头与声明

| 项目 | 内容 |
| --- | --- |
| 采集时间（JST） | 2026-11-07 11:02–11:09 JST（= 2026-11-07 10:02–10:09 CST，设备时区 Asia/Shanghai）。逐项采集时刻见各节。 |
| 采集者 | `device-inventory`（DSH Agent Team 独立执行者；未参与实现/部署） |
| 对象设备 | fnOS NAS，主机名 `kotomi`（Linux 6.18.18.c1168-trim x86_64，fnOS/TRIM `1.2.0903`），与首份盘点同一设备 |
| 窗口绑定 | 本文件全部证据在本窗口内从设备实时采集；首份盘点（`docs/DEVICE_INVENTORY_20261107.md`，2026-11-07 02:15–02:28 JST）的基线在本窗口**重新复核**且未变化（见 §1）。 |
| 只读声明 | 全程只执行读取类命令（`stat/find/readlink/getfacl/ss/ps/journalctl/docker ps\|inspect\|diff\|history\|top/sha256sum/md5sum/tar -t/-xO/curl GET/grep`）。**未** stop/start/restart 服务，**未** docker restart/exec 生产容器，**未**改配置，**未**安装/升级/回滚 FPK，**未**写 /vol1，**未**对任何生产 DB 打开连接，**未**调用写接口。 |
| 临时副本（已记录并清理） | 为做包内文件逐字节比对，在 `/tmp/fnmusic-inv-followup-20261107` 解出 2.10.2/2.8.2 的 `app.tgz`（113 MB），比对后 `rm -rf` 并验证 `cleanup_ok=YES`；为做 SQLite 只读检查，复制 `nemcache.sqlite` 到 `/tmp/fnmusic-inv-followup-sqlite-20261107`（24 KB，副本 SHA-256 与源一致），以 `file:...?mode=ro` 只读打开，检查后 `rm -rf` 并验证清理。除这两个已清理的 /tmp 副本外未在设备落任何文件。 |
| 原始输出留存 | 执行机本地：`C:\Users\admin\AppData\Local\Temp\fnmusic-inv-followup\out-a.txt` … `out-l.txt`（未提交仓库；可复核）。本文件为脱敏摘录。 |
| 关联 | 首份盘点 `docs/DEVICE_INVENTORY_20261107.md`；检查单 `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` §0.1/§0.2/§1.1。 |

### 0.1 结论摘要（对首份盘点 unknown 的消解）

| 首份 unknown | 本次结论 | 判定 |
| --- | --- | --- |
| ① jobs DB 路径/是否存在 | 受检范围内**不存在** jobs DB 文件；唯一 SQLite 是 musicbox HTTP 缓存（`nemcache.sqlite`，表 `redirects`/`responses`，0 行）；配置两来源均无 `FNMUSIC_MANUAL_SAVE_JOBS_DB`，当前安装版本也无 jobs 账本消费者 → **文件存在性消解为“不存在（受检范围）”，但配置路径仍无法确定** | 部分消解（配置路径仍 unknown） |
| ② FPK 与安装树对应关系 | **消解**：2.10.2 FPK（sha256 `77bb7566…`）与安装树 114 个包内文件逐字节比对：113 个一致、**1 个不一致**（`webui-service/static/app.js`，安装后有本地补丁）；FPK manifest 的 `checksum` = `md5(app.tgz)`，自洽 | **observed**（附 1 处安装后修改） |
| ③ 容器内依赖版本 | **消解**：镜像层内 `site-packages/netease_musicbox-0.5.3.dist-info/METADATA` → `NetEase-MusicBox 0.5.3`；`PYTHON_VERSION=3.13.16`。未 exec 容器 | **observed** |
| ④ socket 访问控制 | **部分消解**：规范路径、`600 root:root`、监听 pid/fd/inode、父目录 ACL、peer 策略默认值（仅 root uid 0、模式 0600）与 accept 时内核 peer 凭据校验代码均已观察；**非 root 实际拒绝行为未测** | 配置层 observed；行为未验证 |
| ⑤ 内部 `listen.sock` 不存在 | **消解（非异常）**：`takeover.py` 将私有 staging socket 在 `publish()` 中 `chmod 0666` 后 **rename** 到 `/var/run/trim_music.socket`；私有目录因此为空，`/proc/net/unix` 保留 bind 时路径名 | **observed（设计如此）** |
| ⑥ 升级/卸载路径白名单 | **消解**：从 2.10.2 FPK 的 `cmd/*` 脚本只读提取：白名单 = `.env`、`.env.bak*`、`sources-data`、`online_favorites`、`online_favorites.json`、`play_history`；升级会停服务、替换 target/repo 后恢复白名单；卸载会归档到 `/vol1/fnmusic-ext-backup-*.tar.gz` 并保留副本到 `/vol1/fnmusic-ext-data`。**jobs DB/staging 不在白名单内** | **observed** |
| ⑦ 其余：任务状态不可观察、journal 轮转、smoke 可达性 | journal 为**易失**（`/run/log/journal`）且只有当前 boot（2026-11-05 起），服务启动于更早 boot → `journalctl -u fnmusic-ext` 0 条；应用日志停在 2026-11-01；2.10.2 无持久任务队列；smoke 只读可达性本窗口复测通过 | **observed（可观察性边界已解释）** |

---

## 1. 窗口基线复核（与首份盘点对比）

**采集时刻：2026-11-07T11:02:57+0900（JST）/ 10:02:57 CST。**

| 事实 | 命令（摘要） | 脱敏输出（截取） | 判定 |
| --- | --- | --- | --- |
| 主机/运行时长 | `hostname`；`uname -r`；`uptime` | `kotomi`；`6.18.18.c1168-trim`；`up 6 days, 11:28, load average: 0.20, 0.29, 0.33` | observed |
| 服务状态 | `systemctl is-active/is-enabled`；`systemctl show -p …` | `active`；`enabled`；`MainPID=1870197`；`ExecMainStartTimestamp=Sun 2026-11-01 17:18:28 CST`；`ActiveState=active`；`SubState=running`；`DropInPaths=` | observed（与首份一致，未重启） |
| 扩展进程 | `pgrep -af 'fnmusic-ext|webui_gateway|takeover.py|uvicorn app:app'` | `1870197 takeover.py run --base …/repo`；`1870225 .venv-proxy/bin/python -m uvicorn app:app … --uds /run/fnmusic-ext/proxy-ntd48y9d/listen.sock`；`1870256 webui_gateway.py …/repo`；容器内 `1809118`（8004）、`1836506`（8002） | observed（PID 未变） |
| `.env` 身份 | `stat`；`sha256sum` | `mode=600 owner=kurumi:Administrators size=2663 mtime=2026-11-01 17:01:47.050890768 +0800`；sha256 `ccc4c872c8f887a6346b1c48362566c1b1b20b3801cf3193a52329cba21de4da` | observed（与首份一致，未变化） |
| 4 键当前值（只筛这 4 键） | 与 `read_env_file()` 同规则解析，仅打印 4 键 + 2 个路径键 | `FNMUSIC_MANUAL_SAVE_ENABLED=true`；`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=ABSENT`；`FNMUSIC_MANUAL_SAVE_JOBS_DB=ABSENT`；`FNMUSIC_PLAYLIST_MANIFEST_READONLY=ABSENT`；`FNMUSIC_MANUAL_SAVE_TARGET=/vol1/1000/fnmusic-test-20261031`；`FNMUSIC_MANUAL_SAVE_STAGING=/vol1/1000/fnmusic-manual-save-staging` | observed（与首份一致） |
| 容器状态 | `docker ps`；`docker inspect -f` | `fnmusic-sources … Up 5 days (healthy)`；`started=2026-11-01T08:43:17.394729123Z` | observed（未重启） |
| 库/暂存复核 | `stat`；`find … \| wc -l`；`find -printf '%s' \| awk` | 曲库 `mode=750 owner=kurumi:Users device=60`，`files=4 bytes=379825952`；暂存 `mode=700 owner=root:root device=60`，`files=0 bytes=0` | observed（与首份一致） |

> 结论：本窗口与首份盘点是同一运行实例、同一配置状态；首份的“开关/状态”结论未发生变化，因此 §0.2/§1.1 的阻断项（总开关 true、PLAYLIST/READONLY/JOBS_DB 键缺失）仍然成立。

---

## 2. ① jobs DB 定位（路径与文件存在性）

**采集时刻：2026-11-07T11:02:57–11:08:43（JST）。** 全程只读；未对任何生产 DB 打开连接。

### 2.1 配置解析规则（与首份一致，复核）

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| 4 键来源 | 同 §1（repo `.env` 解析 + 进程环境筛选 + unit/drop-in 无命中） | `.env` 无 `FNMUSIC_MANUAL_SAVE_JOBS_DB`；gateway 进程 1870256 的 `/proc/<pid>/environ` 无该键；uvicorn 进程 1870225 无该键 | observed（路径无法由配置确定） |
| 当前安装版本的 jobs 账本消费者 | `grep -rn 'ManualSaveJobsLedger'` 安装树 | 仅 `proxy/manual_save_jobs.py` 的类定义与 `__all__`；无实例化 | observed（2.10.2 运行代码不创建 jobs DB） |

### 2.2 文件系统搜索（覆盖 lead 指定路径 + 容器层/绑定目录）

| 搜索范围 | 命令（摘要） | 结果 |
| --- | --- | --- |
| `/vol1/@appcenter/fnmusic-ext`（maxdepth 8） | `find … \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.db-journal' -o -name '*jobs*' -o -name '*ledger*' \)` | 仅 `repo/proxy/manual_save_jobs.py`（源码 38380B）与 `repo/sources-data/cache/netease-musicbox/nemcache.sqlite`（24576B） |
| `/vol1/@appdata/fnmusic-ext`、`/vol1/@apphome/fnmusic-ext`（maxdepth 6） | 同上 | 无命中 |
| `/run/fnmusic-ext`（maxdepth 3） | 同上 | 无命中 |
| `/tmp`（fnmusic 相关，maxdepth 4） | 同上 + `-path '*fnmusic*'` | 无命中 |
| `/vol1/1000` 全部 fnmusic 目录（`fnmusic-guard-accept-20261101`、`-staging`、`fnmusic-test-20261029`、`fnmusic-test-20261031`、`-staging`、`fnmusic-manual-save-staging`，maxdepth 4） | `find … -type f -printf …` | 只有 flac/lrc/FPK/备份 tgz；**无 jobs DB** |
| 容器可写层（upperdir） | `docker inspect -f '{{.GraphDriver.Data.UpperDir}}'` + `find` 同模式 | `/vol1/docker/overlay2/74274def…/diff` 下无 DB/账本候选 |
| 容器变更清单 | `docker diff fnmusic-sources` | 20 条变更（`/srv/webui-service/static/app.js`、`/tmp/musicdl_outputs`、`/home/appuser/.local`、`/tmp/supervisor.sock` 等），**无 DB/账本文件** |
| 绑定目录 | 容器 Mounts：`repo → /repo`、`repo/sources-data → /data`（均 rw） | 已在上述 appcenter 搜索范围内覆盖 |
| 打开中的 DB 文件 | 扫描 `/proc/*/fd` 的 readlink（`*.db`/`*.sqlite*`/`*-wal`/`*-shm`） | 仅系统/其它应用 DB 与容器内 `nemcache.sqlite`（pid 1836506，`/data/cache/netease-musicbox/nemcache.sqlite`）；**无 jobs DB** |

### 2.3 唯一 SQLite 候选的只读检查（`nemcache.sqlite`）

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| 源文件 | `stat`、`sha256sum` | `mode=600 owner=kurumi:Administrators size=24576 mtime=2026-10-11 16:17:42`；sha256 `57f5fa663a5af9d4c3b1a2983749608ab25808bc8580bd35ced6a5b5cf80bfab` | observed |
| 副本只读查询 | `cp -p` 到 `/tmp/fnmusic-inv-followup-sqlite-20261107/`；python3 stdlib `sqlite3`，URI `file:…?mode=ro` | 副本 sha256 与源一致；`user_version=0`；`journal_mode=delete`；表仅 `redirects`、`responses`（各 0 行）；`jobs_like_tables=[]` | **observed（不是 jobs DB）** |
| 清理 | `rm -rf` 分析目录 | `cleanup_ok=YES` | observed |

> 说明：宿主无 `sqlite3` CLI（首份已记录），但系统 Python 3.11 自带 `sqlite3` 模块；本次用它以只读 URI 打开**副本**，未安装任何东西、未对源 DB 打开连接。

### 2.4 结论

- **文件存在性：** 在受检范围内（扩展安装树/数据目录/运行目录/临时目录/曲库与暂存/容器可写层与绑定目录/进程 fd）**没有 jobs DB 文件**；唯一 SQLite 是 musicbox HTTP 缓存，已用 schema 证明不是任务账本。
- **路径确定性：** `FNMUSIC_MANUAL_SAVE_JOBS_DB` 在 repo `.env` 与 gateway 进程环境中均缺失；当前安装版本无 playlist/jobs 消费者。按 §1.1-3「不得猜默认位置」→ **实际路径仍为 unknown，停止部署的结论不变**；但“设备上是否已存在账本文件”已消解为**否（受检范围）**。
- **残余风险：** 未来部署 P5 时若把 DB 配到 repo/target 内，会被升级替换且不在升级备份白名单（见 §7）；必须选私有持久路径并重新盘点。

---

## 3. ② 旧 FPK 与回滚包

**采集时刻：2026-11-07T11:03:54–11:05:47（JST）。** 只读：`stat/sha256sum/md5sum/tar -tzf/-xOzf`；未执行任何包、未安装。

### 3.1 找到的 FPK 与构建产物

| 文件 | 大小 | SHA-256 | 判定 |
| --- | --- | --- | --- |
| `/vol1/1000/fnmusic-guard-accept-20261101/packages/fnmusic-ext-2.10.2.fpk` | 28,072,710 | `77bb7566609d80e2172fea503bfd75ce112ad423ad1c6c5ccb94d5d1d4029a0c` | observed |
| `/vol1/1000/fnmusic-guard-accept-20261101/packages/fnmusic-ext-2.8.2-rollback.fpk` | 27,170,364 | `1379e2e9214c1ec887e14d0bcfc2befe92453d753643eb9a9531fca7822fdd85` | observed |
| `/vol1/1000/下载备份/ndm/fnmusic-ext-2.8.2.fpk` | 27,173,490 | `a6aa066217e4f5929960acf438e3b49a4af30649c44c4bf4471848754f9df670` | observed |
| `/tmp/build/dist-head-fnmusic-ext-2.10.2.fpk` | 28,072,710 | `77bb7566…`（与上表 2.10.2 **同哈希**） | observed（构建产物） |
| `/tmp/build/fnpack-out-282/fnmusic-ext.fpk` | 27,170,364 | `1379e2e9…`（与 rollback **同哈希**） | observed（构建产物） |
| `/vol1/1000/fnmusic-guard-accept-20261101/pre-upgrade-2.8.2-backup.tgz` | 57,733,049 | `bb8b5bf5d1271b6869dfa7d7a2b1a91550cb559a4e9b94853f4b9bfa2c067a0b` | observed（升级前整树备份） |
| `/tmp/build/head/VERSION`、`/tmp/build/282/VERSION` | — | `2.10.2`、`2.8.2` | observed（构建树） |

搜索范围（只读 `find`，maxdepth 4）：`/vol1/@appcenter`、`/usr/trim`、`/vol1/1000`、`/tmp`、`/vol1/@appdata`、`/var/lib` → 仅上表文件；**未在应用中心缓存/`/usr/trim` 发现其它 FPK**。

### 3.2 FPK 自洽性与 2.10.2 ↔ 安装树逐文件比对

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| 2.10.2 manifest | `tar -xOzf … manifest` | `appname=fnmusic-ext`；`version=2.10.2`；`checksum=f9cc5ff94bc26db814b4b033e62aaf42` | observed |
| manifest checksum 语义 | `tar -xOzf … app.tgz \| md5sum` | `md5(app.tgz)=f9cc5ff94bc26db814b4b033e62aaf42` → **manifest checksum = md5(app.tgz)** | observed（包自洽） |
| app.tgz 内容 | 解到 `/tmp` 分析副本 | 114 个普通文件（`repo/…`、`ui/…`、`config/…`）；**不含 `.env`/`.db`/sqlite/probe 文件** | observed |
| 与安装树逐字节比对 | 114 个文件逐一 `sha256sum`（包 vs `/vol1/@appcenter/fnmusic-ext/{repo,ui,config}`） | `compare_total=114 same=113 mismatch=1 absent=0`；唯一不一致：`repo/webui-service/static/app.js`：包 `e12e6a5e…`（99360B）vs 安装树 `22cde4ea…`（99379B） | **observed：2.10.2 FPK 就是当前安装包（除 1 处安装后修改）** |
| 唯一差异内容 | `diff` 包内 vs 安装树 | 安装树多一处：`fetch(APP_BASE + path, Object.assign({ cache: "no-store" }, options, { headers }))`（包内无 `cache: "no-store"`） | observed（本地补丁，非包内内容） |
| 差异来源线索 | `stat` | 安装树 `app.js` mtime `2026-11-01 17:05:33.831788047 +0800`；同目录 `app.js.bak-manual-save-170533`（99360B）sha256 = **包内版本**；容器 upperdir `/srv/webui-service/static/app.js` 同为 99379B / `22cde4ea…` / mtime `2026-11-01 17:05:33` | observed（安装后本地补丁，同时写入 repo 与容器 /srv；**不是** FPK 内容） |
| 清理 | `rm -rf /tmp/fnmusic-inv-followup-20261107` | `cleanup_ok=YES` | observed |

### 3.3 2.8.2-rollback 与历史 2.8.2 包的关系

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| rollback manifest | `tar -xOzf` | `version=2.8.2`；`checksum=d0c9a61065959c2e6668fffda5a36dc5`；`md5(app.tgz)` 同为 `d0c9a610…` | observed（包自洽） |
| 历史下载包 manifest | 同上 | `version=2.8.2`；`checksum=33d3bf021642aaa6d47685681e65b1a0`；`md5(app.tgz)` 同为 `33d3bf02…` | observed（包自洽） |
| 两包差异 | 解出两份 `app.tgz` 后 `diff -rq` | **63 条差异**（60 个同名文件内容不同、3 个 only-in）：rollback 独有 `container/ensure_lxserver_native.sh`；下载包独有 `scripts/update_lxserver.sh`、`webui-service/static/alipay.png`；`VERSION`：rollback `2.8.2\r\n` vs 下载包 `2.8.2\n` | **observed：rollback 包是重建版，≠ 历史 2.8.2 包** |
| 关键文件哈希 | `sha256sum` | `webui_gateway.py`：rollback `8eb87ea0…` vs 下载包 `b87e39fd…`；`musicbox-service/app.py`：`670d5432…` vs `2aa7b6b7…` | observed（内容不同） |
| 升级前整树备份 | `tar -tzf` / `-xOzf` | 3205 条目；含 `fnmusic-ext/repo/.env`（sha256 `e9995492…`，与当前 `.env` `ccc4c872…` 不同）、`.env.bak.20261011160606`、`.env.webui.bak`、`sources-data/cache/netease-musicbox/nemcache.sqlite`、`cache/covers/*`；`VERSION=2.8.2`；`webui_gateway.py` 哈希 `b87e39fd…`（= 历史下载包版本） | observed（2.8.2 时期的整树备份；**无 jobs DB**） |
| 升级临时备份目录 | `ls /tmp/upgrade-backup` | **ABSENT**（`upgrade_callback` 成功后会 `rm -rf`，见 §7） | observed |

### 3.4 结论

- **2.10.2 FPK 与安装树对应关系已消解：** 包内 114 文件 113 个逐字节一致；唯一差异是安装后写入的 `app.js` 本地补丁（`cache: "no-store"`，同时出现在 repo 与容器 `/srv`，mtime 17:05:33），**该补丁不在 FPK 中**。
- **风险：** 任何重装/升级都会用 FPK 内 `app.js` 覆盖该补丁（升级流程替换 target/repo 后仅恢复白名单，见 §7）；回滚到 2.8.2 同样不含该补丁。补丁的作者/目的/是否应保留为 **unknown**（不是本次采集者所为，首份盘点时已存在）。
- **回滚包风险：** `fnmusic-ext-2.8.2-rollback.fpk` 是 **2026-11-01 重建的 2.8.2**（与 `/tmp/build/fnpack-out-282` 同哈希），与历史下载的 2.8.2 包有 60 个文件内容差异；两者都自洽（manifest checksum=md5(app.tgz)），但**“回滚包”不等于用户历史安装过的 2.8.2**。哪一个是受支持的回滚目标仍 **unknown**（不执行、不判断）。
- **升级前备份：** 存在一份 2026-11-01 15:14 的 2.8.2 整树备份（含 `.env`、sources-data、cache），大小/哈希已记录；它**不是** `upgrade_init` 生成的白名单 `data.tar.gz`（后者已被 `upgrade_callback` 删除），可作为历史参考，但**不构成 §0.1 要求的本次窗口停机前备份**。

---

## 4. ③ 容器内依赖版本（不 exec）

**采集时刻：2026-11-07T11:06:03（JST）。** 只用 `docker image inspect`、`docker history`、镜像 layer 文件（host 路径只读）推断，未 exec 生产容器。

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| 镜像身份 | `docker image inspect fnmusic-sources:latest -f …` | `created=2026-11-01T15:51:21.819179256+08:00`；`size=1319744113`；image id `sha256:16d5e8ef…`；label `io.fnmusic-ext.build-fingerprint=c50b9cc23e6b639cb8bf145d4eee25e1dbe824382098f78ad24b36172fa93a7f` | observed |
| Python 版本 | 镜像 `Config.Env` + layer 文件 | `PYTHON_VERSION=3.13.16`；layer 内 `/usr/local/bin/python3.13`、`/usr/local/lib/python3.13` | **observed** |
| NetEase-MusicBox 版本 | 遍历镜像 28 个 lowerdir，读 `…/site-packages/*dist-info/METADATA` | `/vol1/docker/overlay2/o8n2nqv8jf4ytpzk1cosjmtov/diff/usr/local/lib/python3.13/site-packages/netease_musicbox-0.5.3.dist-info/METADATA` → `Name: NetEase-MusicBox`，`Version: 0.5.3` | **observed = 0.5.3** |
| 安装方式 | `docker history --no-trunc` | `pip install --no-cache-dir -r /srv/req.musicdl -r /srv/req.musicbox -r /srv/req.lxmusic -r /srv/req.webui`（requirements 由构建上下文 COPY 进镜像） | observed（版本由构建时 pip 解析；本次观察到 0.5.3，与检查单 §3 目标 `==0.5.3` 一致） |
| 容器 `/srv` 前端文件 | upperdir vs 镜像 layer 的 `srv/webui-service/static/app.js` | upperdir `22cde4ea…`（99379B，= 安装树补丁版）；镜像 layer `e12e6a5e…`（99360B，= FPK 包内版） | observed（容器运行层被本地补丁覆盖） |
| 容器 4 键环境 | `/proc/<container-init-pid>/environ` 过滤 | 4 键均无 | observed |

> 边界：以上为**静态镜像内容**，不是运行时 `importlib.metadata` 的结果；容器内模块能否正常导入/运行未执行（属 §4.7 部署后验证）。本项不声称“容器可重建”。

---

## 5. ④ gateway socket 访问控制证据

**采集时刻：2026-11-07T11:06:16–11:07:20（JST）。** 只读 `stat/getfacl/ss/readlink//proc`；**未发起任何非 root 连接尝试**。

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| 规范路径 | `readlink -e /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock` | 同路径（无符号链接跳转） | observed |
| socket 属性 | `stat`；`getfacl` | `type=socket mode=600 owner=root:root size=0 device=55 inode=3233706 mtime=2026-11-01 17:18:30.203644356 +0800`；ACL `user::rw- group::--- other::---` | observed |
| 父目录 | `stat`；`getfacl` | `mode=775 owner=root:root inode=3196919`；ACL `user::rwx`、`user:fnmusic-ext:rwx`、`group::r-x`、`mask::rwx`、`other::r-x` | observed（目录 ACL 不改变 socket 自身 0600） |
| 监听进程 | `ss -xlpn`；`/proc/net/unix`；`/proc/<pid>/fd` | `u_str LISTEN … /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock … users:(("python3",pid=1870256,fd=4))`；`/proc/net/unix` inode `6226435`；`/proc/1870256/fd/4 → socket:[6226435]` | observed（路径↔进程↔inode 三方一致） |
| peer 策略配置 | gateway 进程环境（只筛 2 键）；代码 `proxy/gateway_identity.py` | `FNMUSIC_GATEWAY_PEER_ALLOW` 未设置（0 次）、`FNMUSIC_GATEWAY_SOCKET_MODE` 未设置（0 次）→ 默认 `DEFAULT_PEER_UIDS={0}`、`DEFAULT_SOCKET_MODE_ROOT_ONLY=0o600`；实际 socket mode 0600 与推导一致 | **observed（配置层：仅 root）** |
| 内核 peer 校验代码 | `grep`/阅读 `webui_gateway.py`、`gateway_identity.py` | `_admit_peer()` 在 `accept()` 后、读字节前调用 `gateway_identity.read_peer_credentials(client)` + `evaluate_channel(peer, allow_uids)`；启动时 `os.chmod(sock_path, socket_mode)` | observed（代码路径） |
| 实际拒绝行为 | 未测（不做非 root 连接/伪造 peer 测试） | — | **unknown（行为未验证；§0.2 仍需独立验证）** |

补充观察（不属 gateway socket，但同一接管体系）：
- `/var/run/trim_music.socket`：`type=socket mode=666 owner=root:root inode=206495`；`/run/fnmusic-ext/ownership.json` 记录 `proxy` = 该 inode、进程 `pid 1870225`（uvicorn）；`official` = `/var/run/trim_music_upstream.socket` inode `2386`、进程 `pid 5687`（官方 `trim-music`）。`ss` 中 `/var/run/trim_music.socket` 的 LISTEN 行仍显示官方进程的 bind 时名称（内核保留 bind 名，见 §6）。
- 该 666 是 fnOS 官方音乐 socket 路径的既有模式；本盘点只记录，**不判断**其访问控制是否满足 §0.2。

---

## 6. ⑤ 内部 `listen.sock` 文件系统不存在（异常调查）

**采集时刻：2026-11-07T11:06:16–11:07:20（JST）。**

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| 私有目录 | `stat`、`ls -la /run/fnmusic-ext/proxy-ntd48y9d` | `type=directory mode=700 owner=root:root size=40`；`total 0`（空目录） | observed |
| 私有 socket 文件 | `stat`、`readlink -e …/listen.sock` | `No such file or directory` | observed（文件不存在） |
| 但内核仍监听该路径 | `ss -xlpn`；`/proc/net/unix` | `u_str LISTEN … /run/fnmusic-ext/proxy-ntd48y9d/listen.sock … users:(("python",pid=1870225,fd=8))`；`/proc/net/unix` inode `6225493` | observed |
| 目标 socket 文件 | `stat /var/run/trim_music.socket`；`ownership.json` | inode `206495`；`ownership.json` 的 `proxy.inode=[26,206495]`、`proxy.process.pid=1870225` | observed（私有 socket 已被 rename 到目标路径） |
| 代码解释 | 阅读安装树 `proxy/takeover.py`（只读，不执行） | `supervise()`：`tempfile.TemporaryDirectory(prefix='proxy-', dir=state.directory)` → `staged = …/listen.sock` → uvicorn `--uds staged`；`publish()`：`os.chmod(staged, 0o666)` → `move_no_replace(staged, self.target)`；临时目录随后为空 | **observed（rename 发布，非 unlink；私有路径只是 bind 时名称）** |

> 结论：`listen.sock` 在文件系统不存在**不是异常/残留**，而是接管设计的预期结果：私有 staging socket 被原子 rename 为 `/var/run/trim_music.socket`（inode 206495），`/proc/net/unix` 显示的是 bind 时的路径字符串，不代表当前 dentry。私有目录为空、目标 inode 与 `ownership.json` 的 `proxy` 记录一致。

---

## 7. ⑥ 升级/卸载路径白名单（只读提取包内脚本，不执行）

**采集时刻：2026-11-07T11:04:47（JST）。** 从 2.10.2 FPK 的 `cmd/*` 以 `tar -xOzf` 只读打印脚本内容；**未执行任何脚本**。

### 7.1 数据白名单（`cmd/_common` 的 `fnmusic_data_items()`）

```text
.env  .env.bak*  sources-data  online_favorites  online_favorites.json  play_history
```

（相对 `repo` 目录，仅收录实际存在的项；`jobs DB`、`cache/`、`playlist_tracks/`、`recommend_cache/`、`llm_usage.json`、`logs/` **均不在**白名单内。）

### 7.2 生命周期脚本行为（只读阅读）

| 脚本 | 行为（摘要） |
| --- | --- |
| `cmd/upgrade_init` | `systemctl stop fnmusic-ext.service`；若 unit 的 `WorkingDirectory` 与本次安装目录不一致则 `rm -f /etc/systemd/system/fnmusic-ext.service` + `daemon-reload`；若 `fnmusic-sources` 容器归属旧目录则 `docker rm -f`；`BACKUP_DIR=${TRIM_PKGVAR:-/tmp}/upgrade-backup` → `rm -rf` + `mkdir -p` → `tar --ignore-failed-read -czf ${BACKUP_DIR}/data.tar.gz -C repo ${ITEMS}` |
| `cmd/upgrade_callback` | `tar -xzf ${BACKUP_DIR}/data.tar.gz -C repo`；按保存的源执行 `bash ./install.sh --non-interactive --sources … --webui --extend --adopt`；失败时 `docker rm -f fnmusic-sources` 并报错；成功后 `rm -rf ${BACKUP_DIR}` |
| `cmd/uninstall_init` | 先 `bash repo/restore.sh`（失败再 `--adopt`，仍失败降级强制清理：stop/disable、删 unit、`docker rm -f`、删 `/var/lib/fnmusic-ext/deployment`）；删 `repo/logs`；把白名单归档到 `${VOL}fnmusic-ext-backup-<ts>.tar.gz`（`VOL` 由 `target` 符号链接或 repo 路径推导，默认 `/vol1/`）；再把白名单 `cp -a` 到 `${VOL}fnmusic-ext-data`（保留目录 + README） |
| `cmd/uninstall_callback` | `systemctl stop/disable`；`rm -f /etc/systemd/system/fnmusic-ext.service`；`daemon-reload`；`docker rm -f fnmusic-sources`；`rm -f /var/lib/fnmusic-ext/deployment`；`rm -rf repo/logs` |
| `cmd/install_callback` | 若 `/var/lib/fnmusic-ext/deployment` 记录旧部署目录，则从旧目录继承 `.env`/`sources-data`/`online_favorites`/`online_favorites.json`/`play_history`；执行 `install.sh --non-interactive --sources … --webui --adopt` |

### 7.3 与本项目数据的交叉结论

| 对象 | 是否在升级/卸载白名单 | 说明 |
| --- | --- | --- |
| `.env` | **是** | 升级前归档、升级后恢复；卸载归档 + 保留副本 |
| `sources-data`（含 nemcache、网易登录态） | **是** | 整目录 |
| `online_favorites*`、`play_history` | **是** | 单文件/目录 |
| **jobs DB** | **否** | 路径本身 unknown；若未来放在 repo/target 内：升级时 target/repo 被替换、白名单不含它 → **不会备份、会被替换/丢失**；放在 repo 外则不在升级脚本触及范围（但也不被备份） |
| **暂存目录** `/vol1/1000/fnmusic-manual-save-staging` | **否（也不在 repo 内）** | 升级/卸载脚本只操作 repo 内白名单路径，暂存目录在 `/vol1/1000/`，**不被脚本触及**；但同样不被备份 |
| 曲库 `/vol1/1000/fnmusic-test-20261031` | 否 | 同上，不被脚本触及/备份 |
| `cache/`、`playlist_tracks/`、`recommend_cache/`、`llm_usage.json`、`logs/` | 否 | 位于 repo 内但不在白名单 → 升级替换 target 后会丢失（现有 `cache/covers` 等为可重建缓存；`logs/` 卸载即删） |
| 容器 repo bind RW | — | 容器以 `kurumi` 运行、`repo` 以 rw 绑定到 `/repo`；容器可写 repo（含 `.env`、`app.js`）。本次观察到 `/srv` 的 app.js 补丁来自运行期写入（见 §3.2/§4） |
| `TRIM_PKGVAR/upgrade-backup` | 临时 | 升级前生成、升级回调成功后删除；当前 `/tmp/upgrade-backup` **ABSENT**（符合脚本行为） |
| `/vol1/fnmusic-ext-backup-*.tar.gz`、`/vol1/fnmusic-ext-data` | 卸载产物 | 本次未观察到（未卸载） |

> 结论：检查单 §6 的“升级脚本备份边界”陈述与本窗口从实际 2.10.2 FPK 读出的脚本**一致**；jobs DB 与 staging 都不在白名单内。**若 jobs DB 最终配置在 repo/target 内，包替换会丢失它**（与 §1.1-4 的高风险要求一致）。

---

## 8. ⑦ 其余 unknown：任务状态、journal、smoke 可达性

**采集时刻：2026-11-07T11:06:30–11:08:16（JST）。**

### 8.1 journal 轮转原因（已解释）

| 事实 | 命令 | 脱敏输出 | 判定 |
| --- | --- | --- | --- |
| journal 存储位置 | `ls /var/log/journal`；`ls /run/log/journal`；`journalctl --header` | `/var/log/journal` **不存在**；`/run/log/journal/<machine-id>/system.journal` 存在；header：`File path: /run/log/journal/…`、`Boot ID: 65a36a42…`、head realtime `2026-11-07 08:50:02 CST` | observed（**易失 journal**，重启即失） |
| boot 历史 | `journalctl --list-boots` | 只有 1 个 boot（`0 … Thu 2026-11-05 20:38:10 CST – Sat 2026-11-07 10:07 CST`） | observed |
| unit 日志 | `journalctl -u fnmusic-ext` | `fnmusic_ext_journal_entries=0`（`-- No entries --`） | observed |
| journald 配置 | `grep` `/etc/systemd/journald.conf` | `Storage`/`SystemMaxUse` 等均被注释（默认值）；未启用持久化目录 | observed |
| 应用日志 | `stat`、`grep -c`（只计数） | `/vol1/@appdata/fnmusic-ext/fnmusic-app.log` 2,536,824B mtime `2026-11-01 16:44:00`，`manual.?save` 计数 0、`playlist` 计数 3；`/var/log/apps/fnmusic-ext-install.log` 0B；`/var/log/apps/fnmusic-ext.log` 0B mtime `2026-11-02`；`repo/logs` 不存在 | observed |

> 解释：服务启动于 2026-11-01（更早 boot），journal 为 `/run` 下的易失日志且只保留当前 boot（2026-11-05 起）→ 单元日志为 0 条；应用侧只有安装/升级日志（停在 2026-11-01 16:44），无运行时请求日志。**因此“历史在途任务/写入”不可证**（首份 unknown 保留）。
>
> 附带观察：`/var/log/apps/*.log` 的 mtime 每 ~30 秒被 fnOS 日志采集器刷新（1Panel.log、alist3.log、docker-anirss.log、fnmusic-ext-install.log 等同一批 mtime ≈10:07:5x）；这是**系统级日志采集行为**，不是安装器并发运行，也**不是本盘点写入**。

### 8.2 任务状态为何不可观察（已解释）

- 当前安装的 2.10.2 **没有**持久任务队列/账本（§2.1）；单曲保存由 gateway 进程内同步执行、`_SAVE_LOCK` 串行、工作线程名 `fnmusic-manual-save`（`proxy/webui_gateway.py`）。
- 本窗口采样：gateway `/proc/1870256/task` 线程数 = 1（仅主线程）；`/tmp/fnmusic-manual-save-*` 无；无进程持有库/暂存 fd；无 jobs DB 写连接。
- 由于无持久任务状态、无运行时请求日志、journal 易失，**“采样时刻无在途”可观察，但“窗口前无在途/无写入”不可证**。

### 8.3 只读接口 smoke 可达性（本窗口复测）

| 探测 | 命令 | 结果 |
| --- | --- | --- |
| 8774 健康 | `curl -sS -m 5 http://127.0.0.1:8774/healthz` | `healthz_http=200`（`{"ok":true}`） |
| gateway 状态（只读 GET，§4.3 同款烟雾头） | `curl --unix-socket …/fnmusic-ext.sock -H 'x-trim-*' … /app/fnmusic-ext/api/manual-save/status` | `gateway_status_http=200`（`enabled:true`，target/staging 路径同 §1） |
| gateway 状态（未认证） | 同上无头 | `403` |
| 整歌单端点（只读 GET） | `… /manual-save/playlist/playlists` | `501`（安装版本无 P5 端点） |

> 均为 GET、无副作用；未调用任何写接口。带 `x-trim-isadmin` 的 GET 只用于状态交叉确认，不构成身份/授权证明。

### 8.4 曲库/暂存复核

`/vol1/1000/fnmusic-test-20261031`：`mode=750 owner=kurumi:Users device=60`，`files=4 bytes=379825952`；`/vol1/1000/fnmusic-manual-save-staging`：`mode=700 owner=root:root device=60`，`files=0 bytes=0`——与首份盘点一致，本窗口无变化。

---

## 9. 消解后的剩余 unknown 与原因

| # | 剩余 unknown | 原因 | 影响 |
| --- | --- | --- | --- |
| 1 | **jobs DB 的“实际配置路径”** | 两个配置来源均无该键；当前版本无消费者；不猜默认位置 | §1.1-3 仍要求停止部署；但“设备上已存在账本文件”已消解为**否（受检范围）** |
| 2 | 窗口前的历史在途任务/写入 | journal 易失且只有当前 boot；应用日志停在 2026-11-01；无持久任务队列 | §0.1-2 仍不满足；只能证明采样时刻 |
| 3 | 总开关 `true` 是否为有意生产状态 | 只观察值与来源，无变更历史/意图记录 | 仍按阻断处理，不自行修正 |
| 4 | gateway socket 的**实际**访问控制行为（非 root/命名 ACL 用户） | 未做非 root 连接测试（只读边界） | §0.2-2 仍需独立验证；本次只证明配置与代码路径 |
| 5 | `/var/run/trim_music.socket`（mode 666）的访问控制语义 | 未做连接/身份测试 | §0.2 需独立判断；本次仅记录 |
| 6 | 容器内 `NetEase-MusicBox 0.5.3` 的**运行时**可用性 | 只读镜像层文件，未 exec/import | §4.7 部署后仍需实测 |
| 7 | 安装后 `app.js` 补丁的作者/目的/是否应保留 | FPK 不含该补丁；无变更记录 | 升级/回滚会覆盖它，需用户/Lead 决定；不属于本盘点结论 |
| 8 | 哪个 2.8.2 包是“受支持回滚目标” | rollback 包是重建版（≠ 历史下载包），两者都自洽 | §6 回滚门仍未满足；需独立确认 |
| 9 | 同文件系统 rename 的实际原子性 | 只核对 `st_dev` 相同，未做写测试 | 仅静态前提 |
| 10 | fnOS 版本第二来源 | `/etc/fnos-release` 不存在，仅 `/usr/trim/etc/version` | 版本证据单一来源 |
| 11 | 容器环境对非 4 键配置的同步时序 | 超出 §1.1 指定键范围 | 不影响 4 键结论 |

---

## 10. 本次不证明（边界）

1. 不证明 §0.1/§0.2 任何一项完成或 PASS；**不改变 BLOCKED 状态**。
2. 不证明 jobs DB 不存在于**未受检位置**（受检范围见 §2.2）；不证明未来部署不会创建 jobs DB。
3. 不证明 socket 访问控制实际有效（未做非 root 连接测试）；不证明身份头来源或 8774 直连拒绝边界。
4. 不证明容器可重建、运行时依赖可导入、音频有效；仅静态镜像层文件观察。
5. 不证明升级/卸载/回滚可安全执行；脚本仅被**阅读**，未执行；回滚包与历史包不一致，受支持回滚路径仍未知。
6. 不证明历史无在途任务/写入（journal 易失、日志陈旧、无持久队列）。
7. 不证明 `app.js` 本地补丁应保留或应删除；它是既有事实，非本盘点写入。
8. 不证明同文件系统 rename 原子性（未做写测试）。
9. 本文件不授权任何后续写操作。

---

## 11. 对首份盘点 §10 的逐项修订

| 首份 §10 项 | 修订后结论 |
| --- | --- |
| 1 jobs DB 路径 | 配置路径仍 unknown（两来源缺失）；**文件存在性 observed：受检范围内不存在**；当前版本无账本消费者 |
| 2 FPK 与安装树对应关系 | **observed**：2.10.2 FPK = 安装包，除 1 处安装后 `app.js` 本地补丁；回滚包是重建 2.8.2（≠历史包） |
| 3 容器内 NetEase-MusicBox 版本 | **observed**：0.5.3；Python 3.13.16（镜像层，未 exec） |
| 4 采样前历史在途/写入 | 仍 unknown；原因已解释（易失 journal 仅当前 boot、应用日志陈旧、无持久队列） |
| 5 升级脚本数据白名单 | **observed**：`.env`、`.env.bak*`、`sources-data`、`online_favorites*`、`play_history`；jobs DB/staging 不在其中 |
| 6 socket 实际访问控制 | 配置层 observed（600 root:root、peer 默认仅 uid 0、accept 时内核凭据校验）；**行为未验证** |
| 7 READONLY 运行期值 | 仍不可运行期验证（P2/P6 未安装）；`.env`/容器环境均 absent → 缺省 false |
| 8 整歌单开关运行期值 | 仍不适用（P5 未安装）；缺省 false、键缺失 |
| 9 总开关 true 的意图 | 仍 unknown |
| 10 rename 原子性 | 仍 unknown（未做写测试） |
| 11 fnOS 版本第二来源 | 仍 unknown（单一来源） |
| 12 容器环境与 `.env` 同步 | 4 键无冲突（均 absent 或由 gateway 实时读取）；非 4 键超出范围 |

---

## 附录 A：命令清单（摘要）

```text
# 基线复核
TZ=Asia/Tokyo date; systemctl is-active/is-enabled; systemctl show -p ActiveState,SubState,MainPID,ExecMainStartTimestamp
stat/sha256sum repo/.env；4 键只筛解析；pgrep -af；docker ps；docker inspect -f（state/health/started）

# ① jobs DB
find <appcenter/appdata/apphome/run/tmp/vol1-1000-fnmusic-* > -maxdepth N \( -name '*.db' -o -name '*.sqlite' … \)
docker inspect -f '{{.GraphDriver.Data.UpperDir}}' fnmusic-sources；find upperdir 同模式
docker diff fnmusic-sources；/proc/*/fd readlink 扫描
cp -p nemcache.sqlite /tmp/…；python3 sqlite3 URI file:…?mode=ro（PRAGMA user_version/journal_mode/sqlite_master/count）；rm -rf

# ② FPK
stat/sha256sum <3 FPK>；tar -xOzf FPK manifest；tar -xOzf FPK app.tgz | md5sum
解 app.tgz 到 /tmp 分析副本 → 114 文件逐一 sha256sum vs 安装树；diff app.js
解 2.8.2 rollback/download → diff -rq；tar -tzf/-xOzf pre-upgrade-2.8.2-backup.tgz；rm -rf

# ③ 容器依赖
docker image inspect -f（Created/Size/Config.Env）；docker history --no-trunc
docker inspect -f '{{json .GraphDriver.Data.LowerDir}}' → 28 层；find -iname '*netease*musicbox*dist-info'；grep METADATA Name/Version
docker inspect -f '{{.GraphDriver.Data.UpperDir}}' → /srv app.js sha256/mtime

# ④ socket
readlink -e/stat/getfacl fnmusic-ext.sock 与父目录；ss -xlpn；grep /proc/net/unix；/proc/<pid>/fd readlink
gateway 进程环境只筛 FNMUSIC_GATEWAY_PEER_ALLOW/FNMUSIC_GATEWAY_SOCKET_MODE；阅读 gateway_identity.py/webui_gateway.py 默认值与 accept 校验

# ⑤ listen.sock
stat/ls -la /run/fnmusic-ext/proxy-ntd48y9d；stat/readlink listen.sock（ENOENT）；ss//proc/net/unix；ownership.json；阅读 takeover.py supervise()/publish()

# ⑥ 升级/卸载
tar -xOzf FPK cmd/{_common,upgrade_init,upgrade_callback,uninstall_init,uninstall_callback,install_callback}

# ⑦ 可观察性
journalctl --disk-usage/--list-boots/--header/-u fnmusic-ext；ls /var/log/journal /run/log/journal；grep journald.conf
stat/grep -c 应用日志；curl GET healthz/status/playlist（只读）；stat/find 库与暂存
```

**执行方式：** 本机 `ssh -o BatchMode=yes kotomi-root 'bash -s'`，脚本经 base64 stdin 传入；设备侧未落脚本。除 §0 声明的两个已清理 `/tmp` 分析副本外无写入；未启停服务、未改配置、未安装/回滚、未 docker exec 生产容器、未对生产 DB 打开连接。
