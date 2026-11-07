# 生命周期 / 备份 / 回滚 runbook 草稿（部署检查单 §0.1）

> **状态：草稿 / 计划。本文件尚未执行，也不表示任何操作已被批准或验证。**
>
> 本文件是 `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` §0.1「BLOCKED 前置门」的**候选执行框架**，
> 用于把该门要求的「停机 / 启动 / 健康检查 / 失败恢复 / 停写前备份 / 隔离恢复 / 回滚」写成可复核、
> 可判定通过或失败的步骤。**它不改变 §0.1 / §0.2 的 BLOCKED 状态，不构成部署、安装、停启、备份、
> 恢复或回滚的授权。**
>
> 真正执行其中任一步骤，必须同时满足：① §0.1 按本窗口设备事实通过并有独立复核；② 在**部署窗口**内；
> ③ 覆盖该精确动作（目标、命令、副作用、时间窗、回滚范围）的**用户授权**；④ 若涉及 G0 生命周期的
> writer-drain / rollback 演练，还需满足 `docs/G0_MAINTENANCE_PROPOSAL.md` 的 G0-P5 门。
>
> 本文件中的一切命令均为**命令草案**（含 `<占位符>`），未经执行；任何输出、哈希、时间都必须在实际
> 窗口内现场采集，**不得沿用历史记录**。文中不得出现 `.env` 内容、Cookie、token、签名 URL、
> jobs DB 行内容或完整环境变量转储；敏感对象只记录文件名、时间、计数与 SHA-256。

| 项目 | 内容 |
| --- | --- |
| 文件性质 | 草稿 / 计划（不是执行记录、不是证据、不是上线结论） |
| 当前修订 | **Rev-5**（2026-11-07）：按 Rev-4 限定复核 `docs/LIFECYCLE_RUNBOOK_REV4_REVIEW_20261107.md`（`7ace09c1…`，含 NAS 真机实跑）修复 3 处真机缺陷（Py3.11 f-string、`status=exited exit=0` grep、ss 只统计本应用 socket）及 4 项次要项；修订记录见附录 D；自校验戳与证据哈希绑定见附录 E |
| 上一修订 | Rev-4：按 Rev-3 复核 `50a00296…` 修复 R2/R3 两处代码级缺陷；Rev-4 版本 = 1,887 行 / 171,830 B / `74155b9e…`，stamp `f212e9de…`。Rev-3 版本 = 1,834 行 / 165,249 B / `6c4ace21…` |
| 编写依据快照 | 工作区 `E:\fnmusic-ext-dev`，分支 `codex/fnmusic-prelaunch-20261025`，HEAD `cf843a4`，`VERSION` = `2.10.2`（2026-11-07 核对） |
| 设备证据窗口 | 2026-11-07 02:15–02:28 JST 与 11:02–11:19 JST（`docs/DEVICE_INVENTORY_20261107.md`、`docs/DEVICE_INVENTORY_FOLLOWUP_20261107.md`、`docs/DEPLOYED_CODE_ANOMALIES_20261107.md`） |
| 本文覆盖门禁 | 检查单 §0.1（生命周期门）；间接引用 §0.2、§1.2、§4、§5、§6 与 P7 运行手册 A1–A16 |
| 明确不覆盖 | §0.2 身份与路径门（独立门，本文件不提供其证据）；真实音频/整歌单 P7 验收；fnOS 官方应用、nginx/CGI、系统更新的回滚 |
| 执行前置 | §0.1 PASS + 用户授权 + 独立复核 + 部署窗口（缺一即不得执行本文任何设备写操作） |

> 关联文档（按本文引用顺序）：
> `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md`（§0.1/§0.2/§1.1/§1.2/§4/§5/§6）、
> `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md`（A1–A16、§7）、
> `docs/DEVICE_INVENTORY_20261107.md`、`docs/DEVICE_INVENTORY_FOLLOWUP_20261107.md`、
> `docs/DEPLOYED_CODE_ANOMALIES_20261107.md`、`docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md`、
> `docs/VOL1_PERMISSION_MODEL_FINDING_20261107.md`、
> `docs/NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md`、`docs/OFFLINE_FAILURE_TESTS_REVIEW_20261107.md`、
> `docs/G0_MAINTENANCE_PROPOSAL.md`、`docs/MAINTENANCE_STARTUP.md`、`docs/RESTORE_FIXTURE.md`、
> `docs/R2_ISOLATION_PROBE.md`、`docs/ISOLATED_LEDGER_MIGRATION.md`、`docs/SCAN_CONSISTENCY_REDESIGN_20261107.md`、
> `E:\fnmusic\PROJECT_HANDOFF_20261107.md`。

---

## 1. 范围与边界

### 1.1 本文是什么

1. **§0.1 的执行框架草案**：把检查单 §0.1 六条要求展开为「当前事实 → 隔离副本方案 → 停机前备份 →
   部署窗口逐步 runbook → 回滚边界 → 用户决策项 → 仍缺口」的可判定结构。
2. **命令与判据草案**：每一步给出命令草案、预期输出、失败/回滚判据、证据留存格式（§5.12）。
3. **未知项登记**：逐项标注 `observed` / `derived` / `unknown`，并给出补齐条件（§2.11、§8）。
4. **风险提示**：把设备盘点与代码阅读中发现的、会直接影响停机/备份/回滚的陷阱写清楚
   （例如 `webui_gateway.py main()` 会杀掉同机生产网关、`KillMode=mixed` 下网关可能被 SIGKILL、
   升级白名单不含 jobs DB / 曲库 / 暂存、2.8.2-rollback 是重建版等）。

### 1.2 本文不是什么

- **不是执行记录**：全文没有任何一条命令在设备上执行过；文中所有「预期输出」都是待验证的设计值。
- **不是证据**：本文不能作为 §0.1 任何一条的 PASS 证据；引用的设备事实均来自上列文档，不是本文采集。
- **不是授权**：不构成对停服、停容器、编辑 `.env`、安装/升级/回滚 FPK、写 `/vol1`、打开
  jobs DB、调用写接口的授权。检查单 §0.1/§0.2 未 PASS 前，仍然只允许只读盘点与
  「写入独立隔离备份目录的备份/恢复验证」。
- **不是可上线结论**：P7 真实验收、飞牛音乐索引/播放、音源权益、音频有效性均不在本文范围。
- **不解除任何门禁**：§0.1、§0.2、P7 与 G0 的 BLOCKED 状态全部保持；本文若被执行到一半也不会
  改变它们，除非检查单要求的那组证据真的取得并经独立复核。

### 1.3 本轮实际动作（只读声明）

| 动作 | 状态 |
| --- | --- |
| 阅读仓库代码与文档（检查单、运行手册、盘点、异常分析、复审报告、G0 文档、`install.sh`/`extend.sh`/`restore.sh`/`takeover.py`/`webui_gateway.py`/`packaging/`/`container/`） | 已做（只读） |
| 访问 / 修改设备（SSH、docker、systemctl、卷） | **未做**（本轮不需要，也不允许） |
| 修改代码 / 配置 / `.env` / unit / 容器 | **未做** |
| 执行安装 / 停启 / 备份 / 恢复 / 回滚命令 | **未做** |
| 本轮唯一写操作 | 新增本文件（`docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md`） |

### 1.4 写者分类（后文「停写」的判定对象）

为避免「无写入」被泛化或误判，本文按检查单与 G0 文献的「有界写入者模型」
（`docs/G0_MAINTENANCE_PROPOSAL.md`「有界写入者模型」）把写入分成六类：

| 类 | 写入对象 | 已知主体（本仓库代码） | 停写窗口内是否必须静止 |
| --- | --- | --- | --- |
| **W1 曲库** | `/vol1/1000/fnmusic-test-20261031`（target） | 单曲保存发布（`proxy/manual_save_route.py` `publish_noreplace`→`linkat`）；**边听边存**（`proxy/app.py` `tee_save_enabled`，默认 `false`，目录回退到自动探测的曲库）；整歌单 P7 发布（未部署） | 是（备份前后哈希不变） |
| **W2 暂存** | `/vol1/1000/fnmusic-manual-save-staging` | 匿名暂存（`O_TMPFILE`，无目录项）；`manual_save_anonymous.py` | 是（必须为空） |
| **W3 仓库配置与数据** | `repo/.env*`、`repo/sources-data`、`online_favorites*`、`play_history`、`recommend_cache`、`cache`、`logs`、`llm_usage.json` | 宿主 proxy/gateway 进程；**容器内 WebUI**（8774，以 `kurumi` 运行，`repo` 为 rw bind） | 是（**最终备份前**必须静止，见 §5.4-3.5）；**停服前独立备份（BK_PRE）时 W3 仍活跃**，属已知限制，必须在报告声明（§4.7） |
| **W4 jobs 账本** | `FNMUSIC_MANUAL_SAVE_JOBS_DB` 路径（**当前 unknown / 不存在**） | 目标版本：`webui_gateway.py` 进程内按请求建立的短连接（每个操作 `sqlite3.connect`，`finally` 关闭，`isolation_level=None`，未设 `journal_mode`） | 是（未来配置后：最终备份前无打开连接、无 `-wal/-shm/-journal` 残留；BK_PRE 需按 §4.6 定义时间点） |
| **W5 运行态** | `/run/fnmusic-ext/*`（tmpfs：`ownership.json`、锁、私有 socket 目录）、`/var/run/trim_music*.socket`、unit、容器、deployment 注册表 | `takeover.py`、systemd、docker | 仅在停启/部署动作期间变化，属于**预期副作用**，需逐项记录 |
| **W6 外部/系统** | journald、docker 日志、fnOS 官方音乐应用、App Center、`/vol1/@appdata` 日志采集 | 非本扩展可控 | 不要求停止；但必须**显式声明为预期外部写入**，不得计入「扩展无写入」证据 |

### 1.5 与门禁的关系（不得绕过）

- 检查单 §0.1 原文：门禁未 PASS 时「不得停止/启动/重启服务或 worker、编辑配置、安装/升级/降级 FPK、
  对生产路径恢复备份或执行下游运行手册中的写操作」。**本文 §3 的隔离候选若涉及设备上的服务启停，
  同样受此约束**；只有「写入独立隔离备份目录的备份/恢复验证」是当前已允许的例外。
- §0.1 第 3 条明确要求「在与本机版本、包、配置及数据快照一致的隔离副本上**实际验证**停/启行为与
  回滚恢复」——本文 §3 只到「方案 + 可行性 + 缺口」，**没有取得该证据**，因此 §0.1 仍 BLOCKED。
- 检查单 §6：没有验证过的回滚流程时，「不得用卸载/重装或直接安装旧 FPK 试探」。本文 §6 把这条写成红线。
- P7 运行手册 A10（开关关闭 + 重启验证）与 A14（容量）、A16（`commit_unknown`）都依赖本文的
  生命周期步骤先可行；本文不替代 P7。
- §0.2 身份与路径门**独立于 §0.1**：即使本文所有步骤将来执行成功，§0.2 仍必须按真实运行证据单独 PASS。

---

## 2. 当前设备生命周期事实与未知

> 说明：本节所有条目都来自**已归档的只读证据**，不是本文新采集。每条都给出判定与来源；
> 「derived」表示由代码/脚本静态阅读推出、设备上未直接观测的行为；「unknown」表示没有任何来源可判定。
> 设备与配置可能已变化，**窗口内必须重新采集**（检查单 §1.1）。

### 2.1 平台与版本

| 事实 | 判定 | 来源 |
| --- | --- | --- |
| fnOS/TRIM `1.2.0903`（`/usr/trim/etc/version` 单一来源；`/etc/fnos-release` 不存在） | observed（单一来源） | `DEVICE_INVENTORY_20261107.md` §1、§10-11 |
| Debian 12 (bookworm)，内核 `6.18.18.c1168-trim x86_64`，主机 `kotomi` | observed | 同上 §1 |
| Python 3.11.2（系统 `/usr/bin/python3`；stdlib 含 sqlite3 3.40.1）；无 `sqlite3` CLI；无 `php`；宿主 PATH 无 `node` | observed | 同上 §1；`NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md` §1.2-B4 |
| Docker 28.5.2 | observed | `DEVICE_INVENTORY_20261107.md` §1 |
| 扩展安装根 `/vol1/@appcenter/fnmusic-ext`（`config/`、`fnmusic-ext.sock`、`repo/`、`ui/`） | observed | 同上 §2.1 |
| 已安装 `repo/VERSION` = `2.10.2`，sha256 `06674b96…2244ef`，775 root:root，8 B，mtime 2026-11-01 15:24:54 +0800 | observed | 同上 §2.2 |
| 当前安装版本**不含** P1/P2/P4/P5/P6 整歌单代码（快照 producer、适配层、网关 6 端点、管理页入口均缺） | observed | 同上 §2.3 |

### 2.2 服务 unit 与生命周期语义

| 事实 | 判定 | 来源 |
| --- | --- | --- |
| 唯一相关 unit：`fnmusic-ext.service`，`enabled` + `active (running)`，MainPID 1870197，启动于 2026-11-01 17:18:28 CST，`NRestarts=0` | observed | `DEVICE_INVENTORY_20261107.md` §3；`DEVICE_INVENTORY_FOLLOWUP_20261107.md` §1 |
| unit 文件 `/etc/systemd/system/fnmusic-ext.service`：600 root:root，788 B，sha256 `f6b2a6d0…10a9`；**无 drop-in**（`DropInPaths=` 空，两处 `.d` 目录 absent） | observed | 同上 §3 |
| `Type=simple`、`User=root`、`WorkingDirectory=<repo>`、`UMask=0077`、`Restart=no`、`KillMode=mixed`、`TimeoutStartSec=75`、`TimeoutStopSec=20` | observed | 同上 §3；关键字段与仓库模板 `fnmusic-ext.service` 兼容（其余行未逐行比对；`Environment=PYTHONUNBUFFERED=1` 等未被盘点输出覆盖） |
| `ExecStart=/bin/bash <repo>/proxy/run_proxy.sh` → `exec /usr/bin/python3 <repo>/proxy/takeover.py run --base <repo>` | observed（unit 字段）+ derived（脚本内容） | 同上 §3；仓库 `proxy/run_proxy.sh` |
| `ExecStartPost=/usr/bin/python3 <repo>/proxy/takeover.py ready --timeout 65`：**启动成功以「接管就绪」为门槛**，超时/依赖不 ok 则 unit start 失败 | derived（unit + `takeover.py wait_ready`） | 同上 §3；仓库 `proxy/takeover.py:526-547` |
| `ExecStartPost=/usr/bin/python3 <repo>/proxy/webui_gateway.py <repo>`：网关由 unit 启动，但 `main()` **double-fork 后台化**（父进程退出即视为 ExecStartPost 成功） | derived（代码） | 仓库 `proxy/webui_gateway.py:2869-2881,2968-3028` |
| `ExecStopPost=/usr/bin/python3 <repo>/proxy/takeover.py restore`：**停止 unit 会把官方 socket 复位**；`takeover supervise` 退出时也会先尝试 `state.restore()`，两者幂等 | derived（unit + 代码） | 仓库 `proxy/takeover.py:717-794` |
| **停止语义风险**：`KillMode=mixed` 下 SIGTERM 只发主进程，主进程退出后对同 cgroup 其余进程发 SIGKILL；`webui_gateway.py` 未见 SIGTERM 处理器 → 网关进程很可能被**强制终止**而非优雅退出 | **derived / 未实测**（按 systemd 语义 + 代码缺失推断；必须在隔离副本复现） | `fnmusic-ext.service`；`proxy/webui_gateway.py` |
| `Restart=no`：unit 停止后**不会自动拉起**；重启整机/官方应用后是否自动恢复接管，取决于 unit enabled 与官方 socket 出现时序 | observed（Restart=no）+ derived（`wait_official_target` 40 s 等待预算；调用处实参 `40.0` 在 `takeover.py:758`） | 同上；`proxy/takeover.py:699-714`（定义）与 `:758`（实参） |
| 停止/启动是否受「飞牛音乐官方应用必须运行」约束 | **derived**：`takeover restore-plan` 要求 upstream（或 target）存在 positively identified official listener；`restore()` 在无 official listener 时抛 `no official listener present; restart the official music app` | 仓库 `proxy/takeover.py:336-412` |
| 官方 listener 的识别方式 | **derived**：`snapshot()` 把 peer 进程 `/proc/<pid>/exe` 的 basename 为 `trim-music` 者判为 `official`；其余靠 `/_ext/livez` 判 proxy | 仓库 `proxy/takeover.py:118-144` |

### 2.3 进程与职责

| 进程 | 角色 | 判定 / 来源 |
| --- | --- | --- |
| 1870197 `takeover.py run --base <repo>` | unit 主进程；派生 uvicorn、发布/恢复 socket、写 `/run/fnmusic-ext/ownership.json` | observed `DEVICE_INVENTORY_20261107.md` §3 |
| 1870225 `.venv-proxy/bin/python -m uvicorn app:app --uds /run/fnmusic-ext/proxy-ntd48y9d/listen.sock` | 代理业务进程（`proxy/app.py`）；其 socket 在 `publish()` 时被 `chmod 0666` 后 rename 到 `/var/run/trim_music.socket` | observed（进程）+ derived（rename 解释见 `DEVICE_INVENTORY_FOLLOWUP_20261107.md` §6） |
| 1870256 `webui_gateway.py <repo>` | 桌面网关 → 8774；持有 `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`；**目标版本 jobs 账本的写入者** | observed §3/§9.2；derived（`manual_save_playlist_jobs.PlaylistJobService` 在 gateway 侧按请求构建） |
| `fnmusic-sources` 容器内：`entrypoint.sh`、`supervisord`、`uvicorn app:app --port 8004`（WebUI）、`uvicorn app:app --port 8002`（musicbox），全部以 `kurumi` 运行 | 音源/管理页容器 | observed `DEVICE_INVENTORY_20261107.md` §4 |
| 无独立 worker / 无持久任务队列（当前版本） | — | observed 同上 §4/§8；`DEVICE_INVENTORY_FOLLOWUP_20261107.md` §8.2 |

### 2.4 socket 拓扑与接管机制

| 事实 | 判定 | 来源 |
| --- | --- | --- |
| 官方路径 `/var/run/trim_music.socket`：当前由代理占用（mode 666 root:root，inode 206495，`ownership.json` 记 `proxy`） | observed | `DEVICE_INVENTORY_FOLLOWUP_20261107.md` §5 |
| 官方应用真实 listener `/var/run/trim_music_upstream.socket`（inode 2386，pid 5687 `trim-music`），由接管 rename 生成 | observed | 同上 §5 |
| 桌面网关 socket `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`：600 root:root，listener 1870256；父目录 775 root:root + 命名 ACL `user:fnmusic-ext:rwx` | observed | `DEVICE_INVENTORY_20261107.md` §9.2；followup §5 |
| 网关节点的 L2 边界：`SO_PEERCRED` + `FNMUSIC_GATEWAY_PEER_ALLOW`（未设置 → 默认仅 uid 0）+ `FNMUSIC_GATEWAY_SOCKET_MODE`（未设置 → 0600）；非允许 uid 连接被 reset、无 HTTP 响应（fail-closed） | observed（配置层）+ observed（`kurumi` 被 reset，`www-data` 被 EACCES） | followup §5；`VOL1_PERMISSION_MODEL_FINDING_20261107.md` §2.2/§3-3 |
| **`/vol1` 上 mode 不是本地安全边界**：NAS 管理员 `kurumi` 可读 root 0600 文件、可连 root 0600 socket（应用层再拒） | observed | `VOL1_PERMISSION_MODEL_FINDING_20261107.md` §2–§3 |
| 接管状态目录 `/run/fnmusic-ext/`（tmpfs）：`ownership.json` 只在当前 boot 有效，重启即丢（代码可容忍，重启后靠官方 socket 出现时序重建） | observed（目录/文件）+ derived（tmpfs 易失） | followup §5；代码 `State`/`wait_official_target` |
| `restore-plan` 的预检语义：target 为 official → `official-direct`/`official-reclaim`；upstream 为 official → `proxy-recovery`/`vacant-target-repair`；否则拒绝停止 | derived（代码） | 仓库 `proxy/takeover.py:336-371` |
| 停止时若官方应用已重启导致 upstream listener 变更，`restore`/`remember` 采用**实时内核身份**而不是缓存记录 | derived（代码） | 仓库 `proxy/takeover.py:257-270,397-405` |

### 2.5 容器监督者与启动脚本

| 事实 | 判定 | 来源 |
| --- | --- | --- |
| `fnmusic-sources`：镜像 `fnmusic-sources:latest`（image `sha256:16d5e8ef…`，created 2026-11-01 15:51:21 +08:00，构建指纹 label `c50b9cc2…`），`Up 5 days (healthy)`，`restart=unless-stopped` | observed | `DEVICE_INVENTORY_20261107.md` §4；`docker-compose.yml` |
| 端口映射（宿主仅 127.0.0.1）：8768→8001（musicdl）、8770→8002（musicbox）、8772→8003（lxmusic）、8774→8004（WebUI） | observed | 同上 §4；compose 文件 |
| 绑定：`<repo> → /repo`（rw）、`<repo>/sources-data → /data`（rw） | observed | 同上 §4 |
| 容器内进程集由 `/repo/.env` 决定（`entrypoint.sh` 读 `env_flag`；只启动所选音源 + WebUI），supervisor `autostart=false` 按需加载 | observed（进程）+ derived（脚本） | `container/entrypoint.sh`、`container/env_flag.sh`、`supervisord.conf` |
| 容器内 `NetEase-MusicBox 0.5.3`、Python 3.13.16（镜像层静态观察；非运行时 import 证明） | observed（静态） | followup §4 |
| healthcheck：30 s 间隔、10 s 超时、3 次重试、start_period 20 s | derived | `docker-compose.yml` |
| **停止 unit 不会停止容器**：容器由 compose/restart policy 独立管理；`docker stop` 后 `unless-stopped` 保持停止（重新 `compose up`/`start` 才会拉起） | derived（compose + docker 语义） | `docker-compose.yml` |
| 容器以 `kurumi` 运行且 repo 为 rw bind → 容器内 WebUI 可改写 `.env`、切源、重启 supervisor 程序 | observed（挂载/用户）+ derived（代码） | `DEVICE_INVENTORY_20261107.md` §4/§11-R2；compose |

### 2.6 配置来源与开关生效规则

| 事实 | 判定 | 来源 |
| --- | --- | --- |
| `.env` 位于 `repo/.env`（会被包替换的路径内），600 `kurumi:Administrators`，2663 B，mtime 2026-11-01 17:01:47，sha256 `ccc4c872…` | observed | `DEVICE_INVENTORY_20261107.md` §2.2/§5.1 |
| 同目录 5 个 `.env` 历史备份，其中一个 `.env.bak.20261101152455` 权限 **000**（不可读，历史遗留） | observed | 同上 §2.2/§11-R1 |
| unit / drop-in **不承载**这 4 个键，且 unit 无 `EnvironmentFile=` | observed | 同上 §3 |
| 单曲总开关 `FNMUSIC_MANUAL_SAVE_ENABLED`：**当前 = true**；`manual_save_route.load_config()` 每次调用读 repo `.env`（per-request），运行期 GET status 已交叉确认 `enabled:true` | observed | 同上 §5.2/§9.2；`DEPLOYED_CODE_ANOMALIES_20261107.md` §2.2 |
| 整歌单开关 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`：**键缺失**；目标代码按「repo `.env` 优先、缺键回退进程环境、缺省 false」解析；当前安装版本无消费者，运行期不可交叉验证 | observed（缺失）+ derived（解析规则） | `DEVICE_INVENTORY_20261107.md` §5.2；`proxy/webui_gateway.py:1437-1461` |
| `FNMUSIC_MANUAL_SAVE_JOBS_DB`：**两来源均缺失 → 路径 unknown**；目标代码同样 `.env` 优先、进程环境回退；`_valid_database_path` 仅要求绝对路径、无 NUL/换行、长度上限 | observed（缺失）+ derived（校验规则） | 同上 §5.2；`proxy/manual_save_playlist_jobs.py:917-952` |
| `FNMUSIC_PLAYLIST_MANIFEST_READONLY`：`.env` 缺失；容器启动时由 `env_flag.sh export_source_env` 从 `/repo/.env` 导出（当前无该键）；缺省 false | observed（缺失）+ derived | 同上 §5.2；`container/env_flag.sh:32-51` |
| 目标代码的整歌单 deps（开关、DB 路径、producer 客户端、`PlaylistJobService`）在**每个请求**重建；403/500 判定发生在任何任务创建/文件写入之前 | derived（代码）→ 必须在部署后实测 | `proxy/webui_gateway.py:1431-1503,1528-1545`；`DEPLOYED_CODE_ANOMALIES_20261107.md` §2.5 |
| 宿主机 proxy 的 `.env` 热加载/后台任务（推荐、喜欢定时、日志保留、孤儿歌词扫描、LX 预热）在普通启动时启用 | derived（代码 + `docs/MAINTENANCE_STARTUP.md`） | `docs/MAINTENANCE_STARTUP.md`「开关」 |
| 维护启动模式（`takeover run --maintenance-startup`）可抑制九类启动副作用，但**本机未部署/未在 fnOS 验证**，`production_ready=false`，且不是只读锁 | observed（文档状态） | `docs/MAINTENANCE_STARTUP.md`「状态/部署边界」 |

### 2.7 无 jobs DB 的影响（本轮核心前提）

| 事实 / 结论 | 判定 | 来源 |
| --- | --- | --- |
| 受检范围内**不存在** jobs DB 文件（安装树 / appdata / apphome / `/run` / `/tmp` / `/vol1/1000` fnmusic 目录 / 容器 upperdir / 进程 fd），唯一 SQLite 是 musicbox HTTP 缓存 `nemcache.sqlite`（24,576 B，`user_version=0`，`journal_mode=delete`，表 `redirects`/`responses` 共 0 行） | observed | `DEVICE_INVENTORY_FOLLOWUP_20261107.md` §2.2/§2.3 |
| `FNMUSIC_MANUAL_SAVE_JOBS_DB` 配置路径仍 **unknown**（两来源缺失；不猜默认值） | unknown（配置路径） | 同上 §2.4/§9-1 |
| 当前 2.10.2 启动/运行**不依赖** jobs DB：网关启动不读 DB；单曲保存链路完全无 DB；`ManualSaveJobsLedger` 在安装树中只有定义、无实例化 | observed（代码 + 进程未重启） | `DEPLOYED_CODE_ANOMALIES_20261107.md` §2.1 |
| **无 DB 仍会写盘**：单曲保存在总开关 true 时可直接发布到曲库（`O_TMPFILE` 暂存 + `linkat` no-replace），历史上已产生 4 个文件；失败主要留 `/tmp` 并自动清理；staging 不留命名文件；「发布结果不确定」时目标文件可能存在但无账本可对账 | observed（现状）+ derived（代码路径） | `DEPLOYED_CODE_ANOMALIES_20261107.md` §2.3/§2.4 |
| 目标版本（P5 整歌单）若 `JOBS_DB` 仍缺失：在创建任何任务/写文件**之前**即 500 `config_invalid`；v1 账本不自动迁移到 v2，旧库迁移/部署仍 BLOCKED | derived（目标代码）+ observed（合同） | 同上 §2.5；`docs/OFFLINE_SAVE_JOBS.md:59,63`；`docs/MANUAL_SAVE_PLAYLIST_JOB.md:378` |
| 对备份的含义 | — | 本轮备份范围**不含** jobs DB（不存在），但必须包含**曲库清单/内容**与 `.env`/数据白名单；未来一旦配置 DB，备份必须改为「停写后 SQLite backup API 一致快照 + sidecar 校验」（§4.6） |

### 2.8 版本 / 包 / 回滚包状态

| 事实 | 判定 | 来源 |
| --- | --- | --- |
| FPK `fnmusic-ext-2.10.2.fpk`：28,072,710 B，sha256 `77bb7566…029a0c`，`manifest` version 2.10.2、`checksum=f9cc5ff9…` = `md5(app.tgz)`（包自洽）；`app.tgz` 内 114 个普通文件，不含 `.env`/DB/probe | observed | `DEVICE_INVENTORY_20261107.md` §2.2；followup §3.1/§3.2 |
| 2.10.2 FPK ↔ 当前安装树：**113/114 逐字节一致**，唯一差异 `repo/webui-service/static/app.js`（FPK `e12e6a5e…` 99,360 B vs 安装树 `22cde4ea…` 99,379 B）；差异为安装后本地补丁 `fetch(..., {cache:"no-store"})`，同时写入容器 `/srv`；FPK 内**不含**该补丁 | observed | followup §3.2；`DEPLOYED_CODE_ANOMALIES_20261107.md` §1 |
| `run_proxy.sh` 内容 = 包内版本，但 mtime 被重写（2026-11-01 16:44:44）——仅触碰无内容变化 | observed | `DEPLOYED_CODE_ANOMALIES_20261107.md` §1.4 |
| 回滚包 `fnmusic-ext-2.8.2-rollback.fpk`：27,170,364 B，sha256 `1379e2e9…2fdd85`，manifest 2.8.2、自洽；由 `/tmp/build/282`（来源未识别的混合树）打包 | observed | followup §3.3；anomalies §3.3 |
| 历史下载包 `fnmusic-ext-2.8.2.fpk`：27,173,490 B，sha256 `a6aa0662…4f9df670`；与 rollback 包有 **60 个文件内容差异 + 3 个 only-in**；`VERSION` 编码差异（CRLF vs LF）；两者生命周期 shell 脚本一致 | observed | followup §3.3；anomalies §3.2 |
| 升级前整树备份 `/vol1/1000/fnmusic-guard-accept-20261101/pre-upgrade-2.8.2-backup.tgz`：57,733,049 B，sha256 `bb8b5bf5…67a0b`，3205 条目，含当时 `.env`（sha `e9995492…` ≠ 当前 `ccc4c872…`）与 `VERSION=2.8.2`；不含 jobs DB | observed | followup §3.3 |
| `/tmp/build/dist-head-fnmusic-ext-2.10.2.fpk` 与 2.10.2 同哈希；`/tmp/build/fnpack-out-282/fnmusic-ext.fpk` 与 rollback 同哈希 | observed | followup §3.1 |
| `upgrade_init` 临时备份目录 `/tmp/upgrade-backup` 当前 ABSENT（`upgrade_callback` 成功后删除，符合脚本行为） | observed | followup §3.3 |
| **受支持的回滚目标 / fnOS 是否支持降级：unknown**（未执行任何安装/降级测试） | unknown | followup §9-8；anomalies §3.5 |

### 2.9 升级 / 卸载脚本行为（从仓库与 FPK 内 `cmd/*` 只读阅读）

| 脚本 | 关键行为 | 判定 / 来源 |
| --- | --- | --- |
| `cmd/_common` `fnmusic_data_items()` | 白名单（相对 repo、仅收录存在项）：`.env`、`.env.bak*`、`sources-data`、`online_favorites`、`online_favorites.json`、`play_history`。**jobs DB、`cache/`、`playlist_tracks/`、`recommend_cache/`、`llm_usage.json`、`logs/` 均不在** | observed（FPK 内脚本）；仓库 `packaging/fpk/cmd/_common:123-138` |
| `cmd/upgrade_init` | `systemctl stop fnmusic-ext.service`；unit `WorkingDirectory` 与安装目录不一致则删 unit + daemon-reload；`fnmusic-sources` 容器归属旧目录则 `docker rm -f`；`BACKUP_DIR="${TRIM_PKGVAR:-/tmp}/upgrade-backup"` → `rm -rf` + `mkdir -p` → `tar --ignore-failed-read -czf data.tar.gz -C <repo> <ITEMS>`；失败即中止（未改数据） | observed（脚本）；`DEVICE_INVENTORY_FOLLOWUP_20261107.md` §7.2 |
| `cmd/upgrade_callback` | 解回 `data.tar.gz` → 按保存的音源执行 `bash ./install.sh --non-interactive --sources … --webui --extend --adopt`；失败时 `docker rm -f fnmusic-sources` 并报错；成功 `rm -rf ${BACKUP_DIR}` | 同上 |
| `cmd/install_callback` | 若 `/var/lib/fnmusic-ext/deployment` 记录旧部署目录，按白名单继承 `.env`/`sources-data`/`online_favorites*`/`play_history`；执行 `install.sh --non-interactive --sources … --webui --adopt`（向导未选 extend 则不加） | 同上 |
| `cmd/uninstall_init` | 先 `bash repo/restore.sh`（失败 `--adopt`，再失败降级强制清理）；删 `repo/logs`；白名单归档到 `${VOL}fnmusic-ext-backup-<ts>.tar.gz`；再 `cp -a` 到 `${VOL}fnmusic-ext-data` 并写 README | 同上 |
| `cmd/uninstall_callback` | `systemctl stop/disable`、删 unit、daemon-reload、`docker rm -f fnmusic-sources`、删 `/var/lib/fnmusic-ext/deployment`、删 `repo/logs` | 同上 |
| `extend.sh` | `trap rollback` 在任何 service 变更前 armed；`takeover restore-plan` 预检；`takeover render-unit` → `/etc/systemd/system/fnmusic-ext.service`；`enable --now`/`restart`；`takeover ready --timeout 30`；`verify_acceptance`（未登录 401/`INVALID TOKEN` 且 <3 s + 音源搜索连通性）；失败 `rollback`（`takeover restore`）；成功后写 deployment registry | derived（仓库脚本）。**边界**：仓库脚本与安装树/FPK 内版本的逐文件一致性未单独核对（FPK 比对覆盖 114 个文件、113 一致，但未按脚本逐条列名）；如需以脚本行为作证据，窗口内应对 `install.sh`/`extend.sh`/`restore.sh` 补一次哈希核对 |
| `restore.sh` | 默认语义：`takeover restore-plan` **先于任何停止**（不可恢复即拒绝）、`takeover remember`、`systemctl disable --now`、`takeover restore`、删 unit、清理 v1.x 三容器与 `fnmusic-sources`、清理 `cache/online_*`、**保留 `.env` 与全部用户数据**；`--full` 才删配置与数据；成功 `takeover deployment-clear` | derived（仓库脚本） |
| `install.sh`（关键点） | `takeover preflight` → 容器形态 `install_sources_container`（`reclaim_container`、`ensure_image_current` 可能**重建镜像**、按 `.env` 等待各 healthz）→ `--extend` 时 `exec extend.sh --force --adopt`；`extend.sh` 的「1.3 检查官方音乐 socket」在 `:408-413`（`:114-137` 是 unit/deployment owner 检查与 `.env` 存在检查，不是 socket 检查）；安装需要合格 Python 3.11+ 与 `sudo -n`/root | derived（仓库脚本 `install.sh:1406-1524,1783-1864`；`extend.sh:114-137,408-413`） |

### 2.10 日志与可观察性（决定「在途任务」能证明到什么程度）

| 事实 | 判定 | 来源 |
| --- | --- | --- |
| journal 为**易失**：`/var/log/journal` 不存在；实际 `/run/log/journal/<machine-id>/system.journal`；仅保留**当前 boot**（2026-11-05 20:38:10 起）；`journalctl -u fnmusic-ext` = 0 条（服务启动于更早 boot） | observed | followup §8.1 |
| 应用日志 `/vol1/@appdata/fnmusic-ext/fnmusic-app.log`：2,536,824 B，mtime 2026-11-01 16:44:00；`manual.?save` 计数 0、`playlist` 计数 3（仅计数）；`/var/log/apps/fnmusic-ext-install.log` 0 B；`repo/logs` 不存在 | observed | followup §8.1 |
| `_common` 的持久安装日志默认 `/var/log/apps/fnmusic-ext-install.log`（`TRIM_PKGVAR` 回滚后仍保留），升级/安装过程输出同时写入 | derived（脚本） | `packaging/fpk/cmd/_common:20-30` |
| `TRIM_PKGVAR` 实际值**未直接观测**；由应用日志路径推断很可能是 `/vol1/@appdata/fnmusic-ext`（derived，窗口内必须用 `printf '%s\n' "$TRIM_PKGVAR"` 核实，升级临时备份目录也随之确定） | derived | 推断链：`_common` 日志路径 + 设备 `fnmusic-app.log` 位置 |
| 结论：**采样时刻无在途可观察**，但「窗口前/窗口内历史无写入」**不可证**；SMOKE 只能证明采样点 | observed + 边界 | `DEVICE_INVENTORY_20261107.md` §8/§12；followup §8.2 |

### 2.11 事实 / 未知汇总（§0.1 直接相关）

| # | 项 | 判定 | 说明 / 来源 |
| --- | --- | --- | --- |
| F1 | fnOS 版本、内核、Python、Docker | observed | §2.1 |
| F2 | 扩展版本 / VERSION 哈希 | observed | §2.1 |
| F3 | 「包哈希」与安装树对应 | observed（附 1 处安装后漂移 `app.js`） | §2.8；漂移必须在窗口内单独记录，不能只记录 FPK 哈希 |
| F4 | unit / drop-in / 关键字段 | observed | §2.2 |
| F5 | 容器、端口、挂载、健康、镜像指纹 | observed | §2.5 |
| F6 | 配置解析来源（`.env` / 进程环境 / unit 无 EnvironmentFile） | observed | §2.6 |
| F7 | 两个保存开关的实际生效值 | 总开关 observed=true（**阻断**）；整歌单开关键缺失（**阻断**） | §2.6 |
| F8 | jobs DB 路径 | **unknown** | §2.7 |
| F9 | jobs DB 文件是否存在 | observed：受检范围**不存在**（不能外推未受检位置） | §2.7 |
| F10 | 曲库 / 暂存路径、大小、文件数、st_dev | observed | §2.12（库 4 文件 379,825,952 B；暂存 0；同 device 60） |
| F11 | 曲库/暂存是否在 repo/target 下 | observed：不在 | `DEVICE_INVENTORY_20261107.md` §7.1/§7.2 |
| F12 | 升级/卸载白名单 | observed（FPK 内脚本） | §2.9 |
| F13 | 静/动态启停与健康检查语义 | derived（代码/unit），**未实测** | §2.2/§2.4 |
| F14 | 停止 unit 时网关进程是否被强制终止 | **derived / 未实测** | §2.2 |
| F15 | 官方音乐应用在停/启/重启接管时的角色 | derived；行为未实测 | §2.2 |
| F16 | 窗口前历史在途任务 / 写入 | **unknown（不可证）** | §2.10 |
| F17 | 受支持的回滚目标 / fnOS 降级支持 | **unknown** | §2.8/§6 |
| F18 | 安装后 `app.js` 补丁的作者/目的/是否保留 | **unknown** | §2.8；anomalies §1.6 |
| F19 | 目标版本（含 P1–P6）的 job 生命周期 / 是否自动重派 | **unknown（未部署）**；本地代码 A8 声称不自动恢复、不重复 POST | `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` A8 |
| F20 | 升级触发镜像重建时的网络/依赖可用性 | **unknown**（未验证；镜像 1,319,744,113 B，构建输入变化时需 `ensure_base_image`） | followup §4；`proxy/install_common.sh:291-342` |

### 2.12 对生命周期 runbook 的直接含义

1. **停机 = 两个动作**：`systemctl stop fnmusic-ext.service`（复位官方 socket）+ `docker stop fnmusic-sources`
   （停掉容器内 `.env`/数据写者与 supervisor 子进程）。只停 unit 不足以让 W3 静止。
2. **停服顺序必须先 unit 后容器**：① unit 停止会通过 `ExecStopPost`/`supervise()` 的 `state.restore()` 把官方
   socket 复位（`restore()` 依赖的是 official upstream listener，**与容器无关**）；② 若先停容器，代理会在
   残留窗口内向不可用音源转发请求（用户侧报错）；③ unit 的启动 readiness 依赖音源健康，先停容器会让
   后续排障与健康判定更混乱。**注意**：先停 unit 不会停止容器，因此「容器写的 W3 仍活着」——这直接影响
   §5.3 的断言范围（见该节）。
3. **启动前置条件是「官方音乐在运行」**：`systemctl start` 通过 `ExecStartPost ready --timeout 65`
   等待「接管 + 依赖健康」；容器未就绪/官方 socket 不在时启动会失败。**窗口内 `systemctl start` 失败
   只会让 unit failed，不会触发 `extend.sh` 的 `trap rollback`（该 trap 只存在于安装/升级的 extend 流程，
   `extend.sh:633-634`）**；失败处置按 §5.8→§6.5 走。
4. **不能靠 unit/容器状态或 MainPID 证明「无在途」**：必须用 `/proc` fd、线程名、socket 连接、
   曲库/暂存/`.env` 的哈希不变等**多来源采样**（§5.3），并明确「只证明采样窗口」。
5. **备份必须在停止之后做「最终」一份，停止之前做「独立」一份**：检查单 §1.2 明确要求停服前
   独立备份与恢复验证已完成；`upgrade_init` 自己的 `data.tar.gz` 不能替代（会被删、且白名单不全）。
6. **升级会覆盖本地漂移**：`app.js` 的 `cache:"no-store"` 补丁不在任何 FPK 中，升级/回滚/重装都会覆盖它
   → 备份必须单独保存该文件哈希与内容，并在部署后核对。
7. **升级白名单不含 jobs DB / 曲库 / 暂存**：未来配置 DB 后，DB 必须放在 repo 之外的私有持久路径，
   并且每次窗口单独备份；曲库/暂存不受升级脚本触及，但也不被升级流程保护。
8. **`/vol1` 的 trimacl 使「root 0700 备份目录」不是保密边界**：NAS 管理员可读 root 0600 文件
   （`VOL1_PERMISSION_MODEL_FINDING_20261107.md` §2.1）。备份目录的权限只能防非管理员；
   若 `.env`/登录态/曲库内容对管理员保密是硬要求，需要用户决定额外手段（§7）。

---

## 3. 「隔离副本」验证方案

### 3.0 目标与判定标准

检查单 §0.1 第 3 条要求：**在与本机版本、包、配置及数据快照一致的隔离副本上实际验证停/启行为与
回滚恢复，并确认不会重复派发在途任务**；且「不能用服务当前 `active`、健康端点、静态审查或一次成功
安装代替」。因此本节不是「随便找个地方试试」，而是定义：

> **一个候选只有同时满足下列 5 项，才算对 §0.1-3 有贡献：**
> ① 不改动生产 unit / 容器 / socket / 生产数据（可事后逐项核对）；
> ② 版本与包、配置、数据快照与当前设备一致（或差异被显式列出并说明为何不影响结论）；
> ③ 能真实执行「停 → 启 → 健康检查 → 失败恢复」并留下可复核输出；
> ④ 能观察「是否有任务被重复派发 / 是否产生新的写入」；
> ⑤ 能覆盖回滚路径（至少恢复到本窗口开始时的形态）。

以下三个候选按「能做到多少」排序。**任何候选都不允许在生产上做「试探停机」**；生产上的停/启只能
按 §5 的窗口 runbook、在授权下执行一次，不能作为演练。

### 3.1 候选 A：复制 repo 树到 `/tmp`，手动运行 proxy / gateway（进程级隔离）

**思路**：把当前安装树（或 dev 工作区快照）复制到 `/tmp/iso-<ts>/repo`，用**显式路径覆盖**运行
`takeover.py`、uvicorn 代理、以及 `webui_gateway.serve()`（不跑 `main()`），上游用一个本地 stub
socket 代替官方 `trim-music`；所有状态目录、socket 路径、数据目录都在 `/tmp` 下。

**关键可行性依据（代码已提供入口）**：

| 机制 | 依据 |
| --- | --- |
| `takeover.py` 支持 `--base/--target/--upstream/--state-dir` 覆盖 | 仓库 `proxy/takeover.py:924-940`（`render-unit`/`status`/`restore-plan`/`restore`/`ready`/`run` 均接受） |
| 代理 app 的依赖路径可全部 env 覆盖 | `proxy/app.py:120-152`（`FNMUSIC_MUSICDL_URL`/`MUSICBOX_URL`/`LX_URL`/`UPSTREAM_SOCK`/`MUSIC_DB`/`CACHE_DIR`/`TEE_SAVE_DIR` 等）、`takeover.environment()` 的 `FNMUSIC_HOME` |
| 网关可只跑 `serve()`，绕过 `main()` 的 daemonize/PID/杀前代逻辑 | 仓库 `proxy/webui_gateway.py:2608-2666`；现有 Linux 进程级测试就是这样做的（`proxy/tests/test_gateway_process_linux.py:28-66`，`serve(sock_path, (host,port), observer)`） |
| 网关 socket 路径可由 base 形状或 env 旁的路径决定；隔离时应显式使用 `serve(sock_path)` | `socket_path_for()` 只在 `main()` 路径使用（`webui_gateway.py:388-395,3002`） |
| 官方 listener 的判定只看 peer 进程 `/proc/<pid>/exe` 的 basename 是否为 `trim-music` | `takeover.py:118-144` —— 隔离副本可用「把一份可执行文件命名为 `trim-music` 的 stub」模拟 official |
| 「启动即养护」的 sidecar：`/<run>/maintenance.sock` 固定路径 | `webui_gateway.py:81`（`MAINTENANCE_SOCKET="/run/fnmusic-ext/maintenance.sock"`）→ 隔离副本**不得调用** `/api/maintenance`，否则会连到生产 |
| 网关 `main()` 会读取并 SIGTERM `/run/fnmusic-ext/webui-gateway.pid` 的进程 | `webui_gateway.py:2854-2866,62` → **隔离副本绝对不能执行 `main()`**，否则会杀掉生产网关（这是本方案的第一红线） |

**建议的隔离布局（只在 `/tmp` 与独立 `--state-dir`）**：

```text
/tmp/fnm-iso-<TS>/
  repo/                    # cp -a 当前安装树或 dev 快照（排除 .venv-proxy 可改为 venv 软链/新建）
  home/                    # FNMUSIC_HOME、logs、recommend、play_history、cache
  data/                    # sources-data 副本（脱敏或不含登录态）
  run/                     # ownership.json、supervisor.lock、stub sockets、state
  fake-official/trim-music # stub：python3 复制件改名，绑定 /tmp/.../run/official.sock
  stub-upstream/           # 最小 HTTP 上游（返回 200 与固定 JSON），供 proxy 的 upstream 健康探测
  stub-sources/            # 8768/8770/8772/8774 的替代最小 healthz 服务（换端口）
  env/iso.env              # 隔离配置副本（开关 false，target/staging/DB 全部指向 /tmp）
  logs/                    # 每次实验的命令/输出/哈希（证据）
```

**必须做的路径改写（否则会打到生产）**：

| 对象 | 生产值 | 隔离值（草案） |
| --- | --- | --- |
| 官方 socket | `/var/run/trim_music.socket` | `/tmp/fnm-iso-<TS>/run/trim_music.socket` |
| upstream socket | `/var/run/trim_music_upstream.socket` | `/tmp/fnm-iso-<TS>/run/trim_music_upstream.socket` |
| takeover state | `/run/fnmusic-ext` | `--state-dir /tmp/fnm-iso-<TS>/run/state` |
| 网关 socket | `<app>/fnmusic-ext.sock` | `/tmp/fnm-iso-<TS>/run/gateway.sock`（`serve()` 直传） |
| 网关 upstream（8774） | `127.0.0.1:8774` | `127.0.0.1:<隔离端口>`（stub/隔离容器） |
| 曲库 target | `/vol1/1000/fnmusic-test-20261031` | `/tmp/fnm-iso-<TS>/library`（复制或空） |
| 暂存 staging | `/vol1/1000/fnmusic-manual-save-staging` | `/tmp/fnm-iso-<TS>/staging` |
| jobs DB | unknown | `/tmp/fnm-iso-<TS>/jobs.db`（仅在需要测账本时） |
| 安装树 / repo | `/vol1/@appcenter/fnmusic-ext/repo` | `/tmp/fnm-iso-<TS>/repo` |

> 注意：`takeover.py` 的 `wait_ready` 要求 `/_ext/healthz` 返回 `ok:true` 且 `peer == 记录的 proxy 身份`，
> 而 `/_ext/healthz` 的 `ok` 依赖 upstream + 至少一个音源 ok；因此隔离必须让 stub upstream 与
> stub 音源在**约定端口**可访问，或者只测 `livez`/`publish`/`restore` 子集并显式记录「未走完整 readiness」。

**命令草案（示例，未执行）**：

```bash
# 0) 前置：确认本机 /tmp 与目标端口空闲、复制来源快照哈希、记录起始时间
TZ=Asia/Tokyo date -Iseconds; date -Iseconds
df -h /tmp; df -i /tmp
# 1) 复制树（只读源；写入 /tmp）
umask 077
ISO=/tmp/fnm-iso-$(date +%Y%m%d-%H%M%S)
# 全部后续步骤要用到的目录一次建齐（含 fake-official/stub-upstream/stub-sources/env）
mkdir -p "$ISO"/{repo,home,data,run,logs,fake-official,stub-upstream,stub-sources,env}
cp -a /vol1/@appcenter/fnmusic-ext/repo/. "$ISO/repo/"     # 或使用 dev 工作区同提交快照
# 2) 关键哈希绑定（证明副本来源）：全树（排除可重建的 .venv-proxy 与 __pycache__），并记录条目数
find /vol1/@appcenter/fnmusic-ext/repo -path '*/.venv-proxy' -prune -o \
     -name '__pycache__' -prune -o -type f -print0 \
  | sort -z | xargs -0 -r sha256sum > "$ISO/logs/source-manifest.sha256"
wc -l < "$ISO/logs/source-manifest.sha256" > "$ISO/logs/source-manifest.count.txt"
#    另外记录 .venv-proxy 的存在性与解释器身份（不逐文件哈希，因为它可重建；若缺失，takeover run 会失败）
ls -ld "$ISO/repo/.venv-proxy" 2>/dev/null; readlink -f "$ISO/repo/.venv-proxy/bin/python" 2>/dev/null
# 3) 制造 fake official（仅 /tmp）：把 python3 复制为名为 trim-music 的可执行文件
#    前提：fake-official/ 已由上面的 mkdir -p 创建；该文件还必须实现 socket server（stub 脚本另行 review）
install -m 0755 /usr/bin/python3 "$ISO/fake-official/trim-music"
sha256sum /usr/bin/python3 "$ISO/fake-official/trim-music" > "$ISO/logs/fake-official.sha256"
# 4) 启动 stub（示例：极简 socket HTTP server，接受连接并返回 200 JSON）
#    —— 具体 stub 脚本必须在隔离目录内创建、review 后运行，不进生产路径
# 5) 以覆盖路径运行 takeover（不使用 run_proxy.sh 的默认参数）
/usr/bin/python3 "$ISO/repo/proxy/takeover.py" status \
  --target "$ISO/run/trim_music.socket" --upstream "$ISO/run/trim_music_upstream.socket" \
  --state-dir "$ISO/run/state"
/usr/bin/python3 "$ISO/repo/proxy/takeover.py" restore-plan \
  --target "$ISO/run/trim_music.socket" --upstream "$ISO/run/trim_music_upstream.socket" \
  --state-dir "$ISO/run/state"
# 6) 单独验证网关 serve()（一个临时 wrapper，只 import serve，不调用 main()）
# 7) 停/启/健康/失败恢复/重复派发观察，逐项记录（见下方「可验证内容」）
# 8) 收尾：kill 仅本实验 pid、核对 /vol1 生产路径哈希/列表未变、删除 /tmp 实验目录（保留证据目录）
```

**可行性**：高（改动只发生在 `/tmp` 与显式 `--state-dir`/`--target`/`--upstream`；root 权限可取得）。
**所需权限/资源**：root（创建 socket、`cp -a`、访问安装树）；`/tmp` 空间 ≥ 副本大小（`.venv-proxy` 可排除或重建）；
空闲端口若干；不得占用 8768/8770/8772/8774 与 `/var/run/trim_music*.socket`。
**风险**：
1. **网关 `main()` 不可用**（会杀生产网关、写生产 PID 文件）——只能 `serve()`；这是一条硬约束。
2. `takeover` 默认路径不可用——必须每次都显式 `--target/--upstream/--state-dir`，任何一条漏写都会操作生产 socket。
3. `mimic official` 依赖「exe basename = `trim-music`」这一实现细节；若将来版本改变识别方式，方案失效。
4. `/tmp` 为 ext4、`/vol1` 为 btrfs+trimacl：**权限/ACL/rename/renameat2 行为不同**，隔离结论不能外推
   到生产 `/vol1` 的 ACL 语义（`VOL1_PERMISSION_MODEL_FINDING_20261107.md` §2–3）。
5. 复制树里 `.env` 可能指向生产 target/staging → 必须改写隔离 `.env`（并确认改写后哈希绑定）；
    **任何写接口都不得以生产 target 为目标**。
6. 若误用 8774 或 gateway `main()`，可能与生产容器/网关冲突（PID 文件、端口、socket 路径）。

**可验证内容**：代理进程的启动/停止/健康探测（`/_ext/livez`、`/_ext/healthz` 在 stub 依赖下）、
网关 `serve()` 的 accept/拒绝路径、`takeover publish/restore` 状态机在**显式路径**上的行为、
「启动失败→回滚到 fake official」、`restore-plan` 拒绝不可恢复拓扑、重复启动时的幂等/拒绝、
被停掉后再启是否会重复派发（隔离版无真实任务，只能验证「无持久队列 → 无自动派发」的代码事实）。
**不可验证内容**：真实 systemd unit 生命周期（`ExecStartPost/StopPost`、`KillMode`、`TimeoutStopSec`）、
真实 `trim-music` 官方应用交互、真实容器/镜像/compose 监督、FPK 安装/升级/回滚、生产 `/vol1` ACL 与
Btrfs 行为、升级白名单与 `.env` 恢复、job 真实派发。**因此候选 A 单独不满足 §0.1-3。**

### 3.2 候选 B：复用容器镜像，在隔离网络/端口启动副本（容器级隔离）

**思路**：不重建镜像，用现成 `fnmusic-sources:latest` 以 `docker run`（或独立 compose project + 不同
容器名/端口/卷）启动一个副本，挂载**复制后的 repo** 与**复制后的 sources-data**，验证入口脚本、
supervisor 进程集、`.env` 开关解析、健康检查、`docker stop/start`、以及容器侧的失败恢复。

**可行性与命令草案（示例）**：

```bash
# ⚠ 本块是**草案占位符块**：所有 <TS> 必须先用窗口变量替换（如 TS=$(date +%Y%m%d-%H%M%S)）。
#   含 `<TS>` 时直接 `bash -n`/复制执行会报语法错（`<` 被当作重定向）——这是**预期**（F-R7），
#   不代表步骤错误；不得把本块原样粘贴到生产 shell。
ISO=/tmp/fnm-iso-$(date +%Y%m%d-%H%M%S); mkdir -p "$ISO"/{repo,data}
# 复制而非挂载生产路径（避免容器写 .env / 登录态）
cp -a /vol1/@appcenter/fnmusic-ext/repo/. "$ISO/repo/"
cp -a /vol1/@appcenter/fnmusic-ext/repo/sources-data/. "$ISO/data/"
# 独立名字 + 独立端口 + 独立卷；不加入默认 network（<TS> 替换后再执行）
docker run -d --name fnm-iso-sources-<TS> \
  -p 127.0.0.1:18768:8001 -p 127.0.0.1:18770:8002 -p 127.0.0.1:18772:8003 -p 127.0.0.1:18774:8004 \
  -v "$ISO/repo":/repo:rw -v "$ISO/data":/data:rw \
  --label io.fnmusic-ext.iso-run=<TS> \
  fnmusic-sources:latest
docker ps --filter name=fnm-iso-sources-<TS>
docker logs --tail 100 fnm-iso-sources-<TS>
curl -sS -m 5 http://127.0.0.1:18774/healthz
docker stop fnm-iso-sources-<TS>; docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' fnm-iso-sources-<TS>
docker start fnm-iso-sources-<TS>; # 再次健康检查
# 收尾：docker rm -f 本实验容器；核对 /vol1 生产路径未变
```

**可行性**：中–高（镜像已在本地，`docker run` 不需要网络；受限点是镜像内 `/srv` 代码是**构建时快照**）。
**所需权限/资源**：root/docker 组；空闲宿主端口（不得占用 8768/8770/8772/8774）；磁盘（镜像已存在，
新容器仅写层与日志）；内存（镜像 1.3 GB，运行 ≈ 容器现状）。
**风险**：
1. **镜像内代码不是当前安装树**：镜像是 2026-11-01 构建（`/srv` 来自 FPK 2.10.2 原版，`app.js` 补丁在
   可写层而非镜像），与「本机版本、包、配置及数据快照一致」有偏差；不能用来验证**目标版本**行为。
2. 容器名/端口/卷若与生产重名（`fnmusic-sources`、8768–8774）会与生产冲突；必须显式改名/改端口。
3. `docker-compose.yml` 的 `image`/`container_name` 是硬编码；用 `docker run` 绕过 compose 时，
   容器内 `supervisorctl`、entrypoint 的行为与 compose 启动可能不同（compose 会注入构建 label/环境）。
4. 容器内 WebUI 会读写 `/repo/.env`；隔离副本必须挂复制目录，不能挂生产 repo。
5. 容器的 `restart: unless-stopped` 在 `docker run` 下不会自动继承（需显式 `--restart`），
   与生产的重启语义不一致，需记录偏差。
6. 无 `docker.sock`（compose 文件注释明确不挂），容器内不能控制生产容器，这是好事；
   但也不能在容器内验证「谁启动/停止容器」。

**可验证内容**：入口脚本按 `.env` 选进程集、各 healthz、`docker stop/start` 的退出码与日志、
container 层失败恢复、`.env` 开关解析（`env_flag`）、supervisor 子进程生命周期、镜像内依赖版本可 import（需测）。
**不可验证内容**：真实 systemd/unit/FPK 安装与升级、`takeover` socket 语义、官方音乐应用交互、
真实生产 `sources-data`/曲库、目标版本（P1–P6）行为。**单独也不满足 §0.1-3**，但可与候选 A 组合
补足「容器侧停/启/健康」与「代理侧停/启/恢复」两类证据。

### 3.3 候选 C：真正满足 §0.1-3 的形态 —— 第二台 fnOS / 虚拟机 / 可回滚环境

**结论先行：当前不可用（unknown）。** 检查单 §0.1-3 要求「与本机版本、包、配置及数据快照一致的隔离
副本」，而本机唯一一致的环境就是生产本身；生产不允许试探停机。因此严格满足该条的候选只有：

| 形态 | 需要满足 | 当前状态 |
| --- | --- | --- |
| 第二台 fnOS 主机（或同一 NAS 上的 fnOS 虚拟机） | 相同 fnOS `1.2.0903` + 官方飞牛音乐 + 允许 App Center 手动安装/升级 FPK + root | **unknown/不可用**（未提供，未验证 NAS 是否支持 VM） |
| NAS 支持的文件系统/虚拟机快照回滚 | 能对系统盘/`/vol1` 做可回滚快照，且快照回滚后 `fnmusic-ext.service`/容器/官方音乐可自愈 | **unknown**（`SCAN_CONSISTENCY_REDESIGN_20261107.md` 提到 fnOS 有 `@snapshot/1000/<日期>`，但那是**用户子卷只读快照**，不是系统/应用状态回滚；且本机 RO 位不被强制） |
| 隔离的 Linux 容器/namespace 完整模拟 systemd + FPK | 能运行 systemd 与 fnOS App Center 生命周期脚本 | **不可行**（App Center 生命周期是 fnOS 平台组件，容器内无法等价） |

**需要的资源（若用户愿意提供）**：一台可装 fnOS 的机器/VM、同一 fnOS 版本、官方飞牛音乐已安装并运行、
能执行 `fnpack`/App Center 手动安装、具备与生产相同的数据布局（`.env` 脱敏副本、曲库样本、暂存空目录）、
以及明确的授权与时间窗。
**可验证内容**：全部 §0.1-3 内容（真实 unit 停/启/健康/失败恢复、FPK 安装/升级/回滚演练、官方音乐
socket 交接、在途任务不重复派发）。
**不可验证内容**：仍不能外推到本机硬件/ACL/Btrfs 的 trimacl 行为（除非同型号同版本），但可作为
「受支持回滚路径」的主要证据来源。

### 3.4 三方案对照

| 维度 | A 复制树 + 手跑进程 | B 复用镜像容器 | C 第二 fnOS/VM |
| --- | --- | --- | --- |
| 可行性 | 高 | 中–高 | 低（当前不可用） |
| 所需权限 | root、`/tmp`、空闲端口 | root/docker、空闲端口 | 第二环境 + root + 授权 |
| 对生产风险 | 中（误用默认 socket / 网关 `main()` 会伤生产） | 中（重名/端口冲突；挂错卷会写生产 repo） | 低（物理隔离），但需平台支持 |
| 能验证停/启/健康 | 部分（进程级，无 systemd） | 部分（容器级） | **是（完整）** |
| 能验证失败恢复/回滚 | 部分（socket 状态机、fake official） | 部分（容器重启） | **是（含 FPK 回滚）** |
| 能验证 FPK 安装/升级 | 否 | 否 | **是** |
| 能验证「不重复派发」 | 否（无真实任务/队列） | 否 | 是 |
| 与「本机版本/包/配置/数据一致」 | 中（代码可一致，宿主语义不一致） | 中（镜像=构建时点，非目标版本） | 高（需同版本） |
| 是否单独满足 §0.1-3 | **否** | **否** | **是**（若执行） |

### 3.5 隔离测试执行纪律（任何候选都适用）

1. **一次只做一个候选**；每个候选有独立实验目录、独立日志、独立结论；不得并行以免互相干扰。
2. **禁止清单**：不使用默认 `--target/--upstream/--state-dir`；不运行 `webui_gateway.py main()`；
   不 `systemctl` 安装/写生产 unit；不 `docker compose up` 生产 project；不挂载生产 repo/sources-data 可写；
   不向 8768/8770/8772/8774 生产端口发起写请求；不写 `/vol1` 生产路径（除非在已授权的独立备份目录内）。
3. **每次实验前后**都要采集「生产未受影响」证据：生产 unit `InvocationID`/`NRestarts`/MainPID、
   容器 `StartedAt`/健康、`/vol1` 关键路径的哈希/列表、`/var/run/trim_music*.socket` inode。
4. **证据留存**：实验目录内 `commands.log`（逐条命令）、`outputs/`、`MANIFEST.sha256`、`conclusion.md`；
   删除实验目录前先计算并保存哈希清单。
5. **结论口径**：只能写「在隔离副本 X 上观察到 Y」；不得写成「生产停/启已验证」，也不得把候选 A/B
   的通过当作 §0.1-3 PASS。

### 3.6 建议与缺口

- **本轮最低成本的有价值组合**：A（代理/网关进程级 + socket 状态机）+ B（容器入口/supervisor/健康）。
  它们可以回答「停哪个进程会破坏什么」「gateway 是否会被强杀」「容器停后 unit start 是否会失败」等
  具体问题，但**不足以**回答 §0.1-3 的 systemd/FPK/回滚部分。
- **真正 PASS §0.1-3 的必要条件**：候选 C 或等价的第二环境。**在用户提供前，§0.1-3 保持 BLOCKED。**
- 仍需在窗口内补充的隔离证据清单：见 §8 第 3 条「补齐条件」。

---

## 4. 停机前备份方案（无 jobs DB 前提下）

> 依据：检查单 §0.1 第 4 条、§1.2；当前设备事实：**没有 jobs DB**，曲库/暂存路径已 observed，
> `.env` 在 repo 内，升级白名单不含 jobs DB / 曲库 / 暂存。
>
> **本方案不执行**。执行前提：§0.1 PASS + 用户授权 + 部署窗口；停服前的「独立备份」与「隔离恢复验证」
> 必须在停服之前完成（检查单 §1.2 明确「不得以停服后再备份作为停服前没有备份的理由」）。

### 4.1 备份范围（无 DB）

> **两份备份，物理隔离，禁止互相写入**（Rev-2 / F1）：
> - `BK_PRE` = 停服前**独立备份**（`$VOL/fnmusic-ext-backup-<TS>-v2.10.2-predeploy`）——§4.3 全套 B1–B11，
>   必须在 §5.4 停服**之前**完成，且是检查单 §0.1-4 要求的「停机前」证据；
> - `BK_FINAL` = 停服后**最终备份**（`$VOL/fnmusic-ext-backup-<TS>-v2.10.2-final`）——§5.5 中把 `BK`
>   重绑定到它后重跑同一套命令，用于记录停服后的稳定状态；
> - 两者各自生成 `MANIFEST.sha256`/`manifest.hash.txt`，时间戳不同；**停服后不得再向 `BK_PRE` 写入任何字节**
>   （§5.5 有显式防覆盖守卫）。
>
> 下表「命令块」列与 §4.3 的代码块编号一一对应，不得只列不采。

| # | 对象 | 精确路径/来源 | 内容形式 | 敏感性 | 方法与校验 | 命令块 |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | `.env` 及历史备份 | `<repo>/.env`、`.env.bak*`、`.env.pre-manual-save.*`、`.env.webui.bak` | 文件（含秘密） | **高**（登录态/密钥） | `cp -p` 到备份目录并 0600；只记录 sha256、size、mtime、mode/owner、键计数；**不 cat/不回显** | B1 |
| B2 | 安装树清单与漂移文件 | `<repo>` **以及** `<app>/ui`、`<app>/config`（后两者在 repo 外，FPK 也含） | 全量哈希清单（repo 排除 `.venv-proxy`（仅记录存在性/解释器）、`__pycache__`、`cache`、`logs`）+ **实际复制** 2 个漂移文件（`webui-service/static/app.js`、`proxy/run_proxy.sh`） | 中（含页面代码） | `find … -print0 \| sort -z \| xargs -0 sha256sum` 生成 `install-tree.sha256` + `install-tree.extra.sha256`，各自记录条目数；复制漂移文件并单独哈希 | B2、B2b |
| B3 | 用户数据白名单 | `<repo>/sources-data`（含网易登录态/缓存）、`online_favorites`、`online_favorites.json`、`play_history` | 目录/文件（含登录态） | **高** | `tar -C <repo> -czf data.tar.gz <items>`，0600；记录条目数与哈希；对**秘密目录**额外记录 `tar -xOzf … sources-data \| sha256sum` 的流式摘要（供 §4.5 演练比对，不落盘） | B3 |
| B4 | 曲库 target | 由窗口内 `.env` 路径键核实的 `LIB`（当前值 `/vol1/1000/fnmusic-test-20261031`，4 文件，379,825,952 B，750 kurumi:Users，device 60） | 文件清单 + 逐文件 sha256；可选全量 `tar`（空间决定） | 高（用户音频） | `find "$LIB" … -print0 \| sort -z \| xargs -0 sha256sum`；若全量复制则复制后 `sha256sum -c`；**`LIB` 必须非空且为目录，否则失败** | B4 |
| B5 | 暂存 staging | 由窗口内 `.env` 路径键核实的 `STG`（当前值 `/vol1/1000/fnmusic-manual-save-staging`，700 root:root） | 空目录核查 | 低 | `find "$STG" -mindepth 1 -maxdepth 1 -print` 必须为空；记录 inode/mode；**`STG` 必须非空且为目录** | B4–B5（同一命令块；B5 不含独立块） |
| B6 | 服务 unit 与运行态 | `/etc/systemd/system/fnmusic-ext.service`、`systemctl show` 安全字段、`/run/fnmusic-ext/ownership.json`（易失，仅取证）、`/var/lib/fnmusic-ext/deployment` | 文件副本 + 字段快照 | 中 | `cp -p` unit；`sha256sum`；`systemctl show` 只取安全字段；**额外**采集 `ownership.json`（若存在）与 `deployment` 文件哈希/内容（仅路径与模式，不含秘密） | B6 |
| B7 | 容器与镜像身份 | `docker inspect` 选定字段（image id、构建指纹 label、mounts、ports、health、started）、`docker ps` | JSON/文本快照（**不含 Env**） | 低 | `docker inspect -f` 白名单字段；`sha256sum` 结果文件 | B7 |
| B8 | 包与回滚材料 | **窗口内核实的** `PKG_2102`、`PKG_ROLLBACK`、`PKG_282_HIST`、`PREUP_TGZ`（当前候选见 §2.8；路径不硬编码） | 只登记哈希/大小/路径，不复制大文件（已存在） | 低 | `sha256sum` 重新计算并**要求四项均非空、文件存在且非空**；缺失 → 按失败判据停止（**不再 `\|\| true` 静默**） | B8 |
| B9 | 日志取证 | `journalctl`（易失，先抓取）、`/vol1/@appdata/fnmusic-ext/fnmusic-app.log`、`/var/log/apps/fnmusic-ext-install.log`、`/var/log/apps/fnmusic-ext.log`、容器 `docker logs --tail` | 文本（脱敏） | 中（可能含路径） | 先 `grep -v` 脱敏（不打印 Cookie/token/签名 URL）；记录大小/mtime/行数与哈希；**install.log 即使 0 B 也要记录 stat** | B9 |
| B10 | 平台事实 | `uname -a`、`/usr/trim/etc/version`、`docker --version`、`python3 -V`、`df -h`/`df -i`（备份卷）、`TRIM_PKGVAR`、相关 `du -sh` | 文本 | 低 | 直接写入证据目录；`df` 必须记录**备份卷与 `/tmp`** | B10 |
| B11 | **jobs DB（当前无）** | `FNMUSIC_MANUAL_SAVE_JOBS_DB` 路径（当前 unknown / 受检范围无文件） | 判定记录；若未来存在则按 §4.6 | — | 用 `.env` 键计数 + 受检路径 `find` 生成「DB 未配置/未创建」的**显式判定行**（不得静默跳过）；若已配置且存在 → 立即转 §4.6，失败即停 | B11（新增） |

> 表外包装步骤 **B12**（§4.3 末尾）：为 `BK_PRE` 生成 `MANIFEST.sha256` + `manifest.hash.txt`（只存哈希值），
> 并把自描述复制到 `EVID/backup/pre/`、生成 `bkpre.freeze-pre-stop.txt` 冻结快照；**只在停服前执行一次**，
> 停服后仅只读校验（§5.5 / §4.4）。`BK_FINAL` 的自描述在 §5.5 生成，同样排除 `manifest.hash.txt` 自身。

**明确不在本轮备份内**：jobs DB（不存在）；`.venv-proxy`（可重建；只记录 `.fnmusic-deps` 状态与解释器身份）；
`cache/`（可重建缓存）；`recommend_cache`（可重建，除非用户要求保留）；容器镜像本体（已存在，可重建）。

### 4.2 备份目录与权限

| 要求 | 草案 |
| --- | --- |
| 两份、物理隔离 | `BK_PRE`（停服前独立备份）与 `BK_FINAL`（停服后最终备份）必须是**两个不同目录**、各自 MANIFEST、各自时间戳；§5.5 写入前有「禁止等于 `BK_PRE`」的守卫 |
| 位置 | 独立的私有持久目录，**不得**在 repo/target、`/tmp/upgrade-backup`、曲库、暂存目录内；候选 `<VOL>/fnmusic-ext-backup-<TS>-v2.10.2-{predeploy,final}`（`<VOL>` 由实际 `findmnt -T <repo>` 推导，不猜 `/vol1/`） |
| 命名 | 含时间戳 + 来源版本 + 用途：`fnmusic-ext-backup-<YYYYmmdd-HHMMSS>-v2.10.2-predeploy` / `…-final` |
| 权限 | 目录 `0700 root:root`，文件 `0600`；`umask 077` |
| 空间 | 先 `df -h`/`du -sh` 实测；预留 ≥ 全量 tar + rehearsal 解包（至少 2× 数据量）；不足则改「清单+哈希+选择性复制」并记录（§7 D3/R3） |
| 保密边界 | `/vol1` 的 trimacl 使 0700 目录对 NAS 管理员仍可读（`VOL1_PERMISSION_MODEL_FINDING_20261107.md` §2.1）→ 若要求对管理员保密，需要用户决定额外手段（§7 D6/R6） |
| 保留期 | 由用户决定（§7 D6/R6）；至少保留到下一次成功部署并被新证据覆盖 |

### 4.3 执行步骤（停服前独立备份 / 停服后最终备份共用）

> 所有命令在**已授权的窗口内**执行；先只读、后写备份目录；每一步记录 exit code、JST/CST 时间、
> 输入的源哈希与输出哈希。设备时区 Asia/Shanghai，执行侧同时记录 JST。
> **同一窗口必须复用 §5.1 建立的 `TS`/`EVID`（不要在 §4.3 重新取时间戳，否则证据目录会分裂）**；
> 若单独执行本块，先显式 `TS=…; EVID=…` 并把它写进 `00-window-time.txt`。
> 以下为顺序命令草案；若整体保存为脚本运行，注意 `exit 1` 会结束整个脚本（这是有意的 fail-fast）。

```bash
# ---- 变量（窗口内实测填写；不得沿用历史值；同一窗口复用 §5.1 的 TS/EVID） ----
# 本块**只用于停服前独立备份（BK_PRE）**；final 阶段由 §5.5 重绑定 BK，**禁止再次执行本变量块**
# （再次执行会把 BK 绑回 BK_PRE，重建 F1 的覆盖风险）。§5.5 会先把 BK 切到 BK_FINAL，并带防覆盖守卫。
TS=${TS:?先按 §5.1 设置 TS；同一窗口不得重新取时间戳}
EVID=${EVID:?先按 §5.1 设置 EVID}
APP=$(readlink -f /vol1/@appcenter/fnmusic-ext)
REPO=$APP/repo
VOL=$(findmnt -T "$APP" -no TARGET | sed -n 's#^\(/vol[0-9]\+\)/.*#\1#p'); VOL=${VOL:-/vol1}
EVID=$VOL/fnmusic-ext-ops-$TS                       # 证据目录（命令/输出/哈希）
BK_PRE=$VOL/fnmusic-ext-backup-$TS-v2.10.2-predeploy  # 停服前独立备份（只写一次）
BK_FINAL=$VOL/fnmusic-ext-backup-$TS-v2.10.2-final    # 停服后最终备份（§5.5 才创建/写入）
BK=$BK_PRE                                            # 本块的角色变量
umask 077
mkdir -p "$EVID"/{pre,stop,backup/pre,backup/final,rehearsal,install,post,rollback} "$BK"
TZ=Asia/Tokyo date -Iseconds > "$EVID/00-window-time.txt"; date -Iseconds >> "$EVID/00-window-time.txt"
printf 'BK_PRE=%s\nBK_FINAL=%s\nVOL=%s\n' "$BK_PRE" "$BK_FINAL" "$VOL" > "$EVID/backup/dirs.txt"

# 路径键读取器：只读 .env 的两个**非秘密路径键**（与 DEVICE_INVENTORY §5.1 同口径），不打印其它键
read_path_key() {
  sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$1=//p" "$REPO/.env" 2>/dev/null | tail -n1 \
    | sed -e "s/^['\"]//" -e "s/['\"]$//"
}
LIB=$(read_path_key FNMUSIC_MANUAL_SAVE_TARGET);  LIB=${LIB:-<窗口内核实的曲库路径>}
STG=$(read_path_key FNMUSIC_MANUAL_SAVE_STAGING); STG=${STG:-<窗口内核实的暂存路径>}
case "$LIB" in *'<'*|'') echo "REFUSE: LIB 路径未核实" >&2; exit 1;; esac
case "$STG" in *'<'*|'') echo "REFUSE: STG 路径未核实" >&2; exit 1;; esac
[ -d "$LIB" ] && [ -d "$STG" ] || { echo "REFUSE: LIB/STG 不是目录" >&2; exit 1; }

# 包材料路径：窗口内核实的**变量**（不硬编码 /vol1/1000；当前候选见 §2.8）
PKG_2102="<窗口内核实的 fnmusic-ext-2.10.2.fpk 绝对路径>"
PKG_ROLLBACK="<窗口内核实的 fnmusic-ext-2.8.2-rollback.fpk 绝对路径>"
PKG_282_HIST="<窗口内核实的 fnmusic-ext-2.8.2.fpk 绝对路径>"
PREUP_TGZ="<窗口内核实的 pre-upgrade-2.8.2-backup.tgz 绝对路径>"
for v in PKG_2102 PKG_ROLLBACK PKG_282_HIST PREUP_TGZ; do
  eval "p=\$$v"; case "$p" in *'<'*|'') echo "REFUSE: $v 未核实" >&2; exit 1;; esac
  [ -s "$p" ] || { echo "REFUSE: $v 不存在或为空: $p" >&2; exit 1; }
done

# ---- B0 空间与基线（只读） ----
df -h "$VOL" > "$EVID/pre/df.txt"; df -i "$VOL" >> "$EVID/pre/df.txt"
df -h /tmp >> "$EVID/pre/df.txt"
du -sh "$REPO" "$REPO/sources-data" "$REPO/.venv-proxy" 2>/dev/null > "$EVID/pre/du.txt"
cat "$REPO/VERSION" > "$EVID/pre/version.txt"
sha256sum "$REPO/VERSION" /etc/systemd/system/fnmusic-ext.service > "$EVID/pre/hashes-unit-version.txt"
printf 'TRIM_PKGVAR=%s\nLIB=%s\nSTG=%s\n' "${TRIM_PKGVAR:-<unset>}" "$LIB" "$STG" > "$EVID/pre/paths.txt"

# ---- B1 配置（含秘密：不 cat、不回显；先建目录再复制） ----
mkdir -p "$BK/config"
shopt -s nullglob
for f in .env .env.bak.* .env.pre-manual-save.* .env.webui.bak; do
  [ -e "$REPO/$f" ] || continue
  install -m 0600 -p "$REPO/$f" "$BK/config/$(basename "$f")"
done
shopt -u nullglob
sha256sum "$BK"/config/* 2>/dev/null > "$BK/config.sha256"
stat -c '%a %U:%G %s %n' "$REPO"/.env* >> "$BK/config.stats.txt"
# 只记录 4+1 个键的存在性/计数（不打印值）
# R6：`grep -c` 在 0 匹配时仍输出 `0` 但退出码 1，若写 `|| echo 0` 会得到两行 "0\n0" → 先吞掉退出码再取首行
for k in FNMUSIC_MANUAL_SAVE_ENABLED FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED \
         FNMUSIC_MANUAL_SAVE_JOBS_DB FNMUSIC_PLAYLIST_MANIFEST_READONLY FNMUSIC_TEE_SAVE_ENABLED; do
  n=$(grep -cE "^[[:space:]]*(export[[:space:]]+)?${k}=" "$REPO/.env" 2>/dev/null || true)
  n=${n:-0}; n=${n%%$'\n'*}
  printf '%s=%s\n' "$k" "$n"
done > "$BK/config.keycounts.txt"

# ---- B2 安装树清单（repo + app/ui + app/config；哈希，不复制全树） ----
find "$REPO" -path "$REPO/.venv-proxy" -prune -o -path "$REPO/cache" -prune -o \
     -path "$REPO/logs" -prune -o -name '__pycache__' -prune -o -type f -print0 \
  | sort -z | xargs -0 -r sha256sum > "$BK/install-tree.sha256"
wc -l < "$BK/install-tree.sha256" > "$BK/install-tree.count.txt"
# repo 之外、但属于 FPK 安装树的两棵树（ui/ 与 config/）
{ find "$APP/ui" "$APP/config" -type f -print0 2>/dev/null | sort -z | xargs -0 -r sha256sum; } \
  > "$BK/install-tree.extra.sha256"
wc -l < "$BK/install-tree.extra.sha256" > "$BK/install-tree.extra.count.txt"
# .venv-proxy 仅记录存在性与解释器身份（可重建；不逐文件哈希）
readlink -f "$REPO/.venv-proxy/bin/python" > "$BK/venv-python.txt" 2>/dev/null || echo absent > "$BK/venv-python.txt"
cat "$REPO/.venv-proxy/.fnmusic-deps" > "$BK/venv-deps-state.txt" 2>/dev/null || true

# ---- B2b 漂移文件实际复制（升级会覆盖） ----
for f in "webui-service/static/app.js" "proxy/run_proxy.sh"; do
  mkdir -p "$BK/drift/$(dirname "$f")"; cp -p "$REPO/$f" "$BK/drift/$f"
done
sha256sum "$BK"/drift/webui-service/static/app.js "$BK"/drift/proxy/run_proxy.sh > "$BK/drift.sha256"

# ---- B3 数据白名单（tar；含登录态，0600） ----
# 与 FPK cmd/_common 的 whitelist 对齐。必须在 repo 内展开通配（cwd 若有 .env.bak* 会被错误展开，
# 见 packaging/fpk/cmd/_common:123-138 的同类注释），故用子 shell + nullglob + 数组。
( cd "$REPO" && shopt -s nullglob && items=(.env .env.bak* sources-data online_favorites online_favorites.json play_history) \
  && tar --ignore-failed-read -czf "$BK/data.tar.gz" "${items[@]}" )
tar -tzf "$BK/data.tar.gz" > "$BK/data.list.txt"; wc -l < "$BK/data.list.txt" > "$BK/data.count.txt"
sha256sum "$BK/data.tar.gz" > "$BK/data.sha256"
# 秘密项的**流式摘要**（不落盘）：供 §4.5 演练比对；sources-data 是目录，用 `tar -xO` 输出成员内容流
{ tar -xOzf "$BK/data.tar.gz" sources-data 2>/dev/null | sha256sum; } > "$BK/data.secret-stream.sources-data.sha256"
# .env 类文件逐个流式摘要（文件名 + 内容摘要；成员名从归档列表派生，不硬编码备份文件名）
: > "$BK/data.secret-stream.env.sha256"
while IFS= read -r f; do
  printf '%s  ' "$f" >> "$BK/data.secret-stream.env.sha256"
  tar -xOzf "$BK/data.tar.gz" "$f" | sha256sum | awk '{print $1}' >> "$BK/data.secret-stream.env.sha256"
done < <(tar -tzf "$BK/data.tar.gz" | grep -E '^\.env(\.|$)')
# 注意：.env.bak.20261101152455 权限为 000（历史遗留），是否可读须在窗口内记录；不可读不等于不存在
# 非秘密白名单项的**内容级清单**（R5：供 §4.5 演练做 sha256sum -c 比对；不落秘密）
NSTMP=$BK/.nonsecret-check; rm -rf "$NSTMP"; mkdir -p "$NSTMP"
# R5-1/Rev-4：解包 rc 必须显式检查；失败不得留下空/部分清单当作「无内容」
if ! tar -xzf "$BK/data.tar.gz" -C "$NSTMP" \
    --exclude='./.env*' --exclude='.env*' \
    --exclude='./sources-data' --exclude='sources-data' \
    --exclude='./sources-data/*' --exclude='sources-data/*'; then
  rm -rf "$NSTMP"; echo "REFUSE: 非秘密白名单解包失败（tar rc != 0）" >&2; exit 1
fi
( cd "$NSTMP" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum ) > "$BK/data.nonsecret.sha256"
wc -l < "$BK/data.nonsecret.sha256" > "$BK/data.nonsecret.count.txt"
rm -rf "$NSTMP"

# ---- B4 曲库 / B5 暂存（只读清单 + 可选全量复制；LIB/STG 来自上面核实的路径键） ----
# 清单与哈希都用绝对路径（避免 cwd 依赖）；文件名若含换行会在清单里显式体现，不得静默丢弃
find "$LIB" -maxdepth 1 -type f -print0 | sort -z | xargs -0 -r sha256sum > "$BK/library.sha256"
find "$LIB" -maxdepth 1 -type f -printf '%p\t%s\t%T@\n' | sort > "$BK/library.list.txt"
wc -l < "$BK/library.sha256" > "$BK/library.count.txt"
stat -c 'mode=%a owner=%U:%G device=%d inode=%i nlink=%h' "$LIB" > "$BK/library.stat.txt"
find "$STG" -mindepth 1 -maxdepth 1 -print > "$BK/staging.entries.txt"   # 必须为空
stat -c 'mode=%a owner=%U:%G device=%d inode=%i' "$STG" > "$BK/staging.stat.txt"
# 非空/同设备/非 repo 前缀的显式检查（失败即停，不静默）
[ -s "$BK/library.sha256" ] || { echo "REFUSE: library 清单为空" >&2; exit 1; }
if [ -s "$BK/staging.entries.txt" ]; then echo "REFUSE: staging 非空，先查明原因" >&2; exit 1; fi
[ "$(stat -c %d "$LIB")" = "$(stat -c %d "$STG")" ] || { echo "REFUSE: LIB/STG 不同文件系统" >&2; exit 1; }
case "$LIB$STG" in *"$REPO"*) echo "REFUSE: LIB/STG 位于 repo 内" >&2; exit 1;; esac
# 可选（用户 D3/R3 决定）：tar -C "$(dirname "$LIB")" -czf "$BK/library.tar.gz" "$(basename "$LIB")"

# ---- B6 运行态（unit / ownership / deployment） ----
cp -p /etc/systemd/system/fnmusic-ext.service "$BK/fnmusic-ext.service"
systemctl show fnmusic-ext.service \
  -p ActiveState,SubState,UnitFileState,MainPID,ExecMainStartTimestamp,InvocationID,NRestarts,Restart,KillMode,TimeoutStopUSec \
  > "$BK/unit.show.txt"
# /run/fnmusic-ext/ownership.json 是 tmpfs 易失记录，仅取证（不用于恢复）；不存在也要显式记录
if [ -e /run/fnmusic-ext/ownership.json ]; then
  cp -p /run/fnmusic-ext/ownership.json "$BK/ownership.json"
  sha256sum /run/fnmusic-ext/ownership.json > "$BK/ownership.sha256"
else
  echo absent > "$BK/ownership.absent.txt"
fi
# 部署注册表（跨 checkout 归属；非秘密，含 base 路径与 deploy mode）
if [ -e /var/lib/fnmusic-ext/deployment ]; then
  cp -p /var/lib/fnmusic-ext/deployment "$BK/deployment"
  sha256sum /var/lib/fnmusic-ext/deployment > "$BK/deployment.sha256"
else
  echo absent > "$BK/deployment.absent.txt"
fi

# ---- B7 容器与镜像身份（不含 Env） ----
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "$BK/docker.ps.txt"
docker inspect -f '{{.State.Status}} {{.State.Health.Status}} {{.Image}} {{.Created}} {{range .Mounts}}{{.Source}}->{{.Destination}} rw={{.RW}} {{end}}' fnmusic-sources \
  > "$BK/docker.inspect.txt"
docker inspect -f '{{index .Config.Labels "io.fnmusic-ext.build-fingerprint"}}' fnmusic-sources \
  > "$BK/docker.fingerprint.txt"
# 容器不存在是本窗口的异常状态（脚本会先记录）：显式失败而不是静默产生空文件
[ -s "$BK/docker.inspect.txt" ] || { echo "REFUSE: docker inspect 无输出（容器缺失？）" >&2; exit 1; }

# ---- B8 包材料哈希（不复制；路径来自窗口变量，缺失即失败，不静默） ----
: > "$BK/packages.sha256"
for v in PKG_2102 PKG_ROLLBACK PKG_282_HIST PREUP_TGZ; do
  eval "p=\$$v"
  [ -s "$p" ] || { echo "REFUSE: $v 缺失或为空: $p" >&2; exit 1; }
  sha256sum "$p" | tee -a "$BK/packages.sha256"
  stat -c '%s %a %U:%G %y %n' "$p" >> "$BK/packages.stat.txt"
done
[ -s "$BK/packages.sha256" ] || { echo "REFUSE: packages.sha256 为空" >&2; exit 1; }

# ---- B9 日志取证（易失 journal 必须先抓；install.log 即使 0 B 也记录） ----
journalctl --list-boots > "$EVID/pre/journal.boots.txt" 2>&1 || true
journalctl -u fnmusic-ext --no-pager > "$EVID/pre/journal.unit.txt" 2>&1 || true
docker logs --tail 500 fnmusic-sources > "$EVID/pre/docker.log.txt" 2>&1 || true
for lg in "$VOL/@appdata/fnmusic-ext/fnmusic-app.log" /var/log/apps/fnmusic-ext-install.log /var/log/apps/fnmusic-ext.log; do
  if [ -e "$lg" ]; then
    stat -c '%s %a %U:%G %y %n' "$lg" >> "$EVID/pre/logs.stat.txt"
    sha256sum "$lg" >> "$EVID/pre/logs.sha256"      # 0 B 也记录（sha256 为 e3b0c442…）
  else
    echo "absent: $lg" >> "$EVID/pre/logs.stat.txt"
  fi
done

# ---- B10 平台 ----
{ uname -a; cat /usr/trim/etc/version; docker --version; python3 -V; printf 'TRIM_PKGVAR=%s\n' "${TRIM_PKGVAR:-<unset>}"; } \
  > "$EVID/pre/platform.txt"

# ---- B11 jobs DB 判定（当前无 DB；显式记录，不静默跳过） ----
# R6：`grep -c` 0 匹配时输出 0 但退出码 1；先吞退出码并只取首行，避免 "0\n0" 让 `[ -gt ]` 报错
DB_HITS=$(grep -cE '^[[:space:]]*(export[[:space:]]+)?FNMUSIC_MANUAL_SAVE_JOBS_DB=' "$REPO/.env" 2>/dev/null || true)
DB_HITS=${DB_HITS:-0}; DB_HITS=${DB_HITS%%$'\n'*}
printf 'jobs_db_key_count=%s\n' "$DB_HITS" > "$BK/jobsdb.status.txt"
if [ "$DB_HITS" -gt 0 ] 2>/dev/null; then
  # 已配置：必须由配置解析规则确定绝对路径，并转 §4.6 取得一致快照
  echo "jobs_db_configured=YES → 停止本轮无 DB 流程，先执行 §4.6 并纳入备份" >> "$BK/jobsdb.status.txt"
  echo "REFUSE: JOBS_DB 已配置，本流程需按 §4.6 重新规划" >&2; exit 1
else
  echo "jobs_db_configured=NO（键缺失；不得据此断言「设备上绝无 DB」，受检范围见 §2.7）" >> "$BK/jobsdb.status.txt"
fi

# ---- B12 停服前独立备份的自描述清单（**只在停服前生成一次**；停服后只读校验，绝不再写 BK_PRE） ----
# 自描述文件自身不参与清单（排除 MANIFEST.sha256 与 manifest.hash.txt），保证可重复 `sha256sum -c`
( cd "$BK" && find . -type f ! -name MANIFEST.sha256 ! -name manifest.hash.txt -print0 \
    | sort -z | xargs -0 sha256sum > MANIFEST.sha256 )
sha256sum "$BK/MANIFEST.sha256" | awk '{print $1}' > "$BK/manifest.hash.txt"
[ -s "$BK/MANIFEST.sha256" ] || { echo "REFUSE: BK_PRE MANIFEST 为空" >&2; exit 1; }
# 复制自描述到 EVID/backup/pre/（EVID 侧副本用于停服后比对；BK 内保留原件）
cp -p "$BK/MANIFEST.sha256" "$BK/manifest.hash.txt" "$EVID/backup/pre/"
sha256sum "$BK/MANIFEST.sha256" | awk '{print $1}' > "$EVID/backup/pre/manifest.sha256-only.txt"
# 停服前冻结快照：文件清单+size+mtime；停服后必须与它逐行一致（即「BK_PRE 未被写入」的硬证据）
find "$BK" -type f -printf '%p\t%s\t%T@\n' | sort > "$EVID/backup/pre/bkpre.freeze-pre-stop.txt"
# 自检：停服前 BK_PRE 自身必须能通过 MANIFEST 校验
( cd "$BK" && sha256sum -c MANIFEST.sha256 ) > "$EVID/backup/pre/selftest.txt" 2>&1 \
  || { echo "REFUSE: BK_PRE 自描述校验失败" >&2; exit 1; }
```

**每步失败判据（任一即停，不得继续部署）**：

| 步骤 | 失败判据 |
| --- | --- |
| 目录/空间 | 目录不在预期卷、不可写、剩余空间 < 预估需要、`umask` 未生效；`BK_PRE == BK_FINAL` |
| 变量/路径 | `LIB`/`STG`/四个包路径仍含 `<占位符>`、为空或不是预期类型（脚本内 `REFUSE` 即失败） |
| 清单 | `find`/`sha256sum` 非 0、清单为空或条目数明显少于预期、`install-tree.extra.sha256` 为空 |
| `.env` 备份 | 复制后 sha256 与源不一致、权限不是 0600、出现 `cat`/终端回显（流程违规） |
| 白名单 tar | `tar` 非 0、`tar -t` 失败、条目数为 0、压缩包哈希无法稳定复算、秘密项流式摘要生成失败 |
| 曲库/暂存 | 曲库哈希清单为空或读取失败；暂存非空（先查明，不删除）；LIB/STG 不同设备或在 repo 内 |
| unit/容器 | 字段缺失、容器 inspect 失败、`fnmusic-sources` 不存在或非 healthy（视窗口阶段而定） |
| 包哈希 | 与 `DEVICE_INVENTORY(s)` 登记的哈希不一致 → **停止部署并重新评估**（可能包被替换）；任一包缺失/为空 |
| jobs DB 判定 | `JOBS_DB` 键存在（说明已配置）→ 本无 DB 流程不适用，转 §4.6 并重新规划 |
| 日志 | journal 抓取失败（易失，稍后即丢）→ 记录为缺失证据，不得口头补充；install.log 缺失/不可读需记录 |

### 4.4 完整性校验与失败判据总表

> 对 `BK_PRE` 与 `BK_FINAL` **分别**执行（两者都有独立 `MANIFEST.sha256`/`manifest.hash.txt`）；
> 下表 `$BK` 指「当前角色的备份目录」。
> **R1/R4 布局统一**：自描述文件在 **`BK` 目录内**（`MANIFEST.sha256` 排除自身与 `manifest.hash.txt`；
> `manifest.hash.txt` 只存 `MANIFEST.sha256` 的哈希值）；同时在 **`EVID/backup/{pre,final}/`** 保留一份
> 可复核副本（`MANIFEST.sha256`、`manifest.hash.txt`、`manifest.sha256-only.txt`）。
> **`BK_PRE` 的自描述清单只在停服前生成（§4.3 B12）；停服后只读校验，绝不重写**（§5.5）。

| 校验 | 方法 | 通过 | 失败处理 |
| --- | --- | --- | --- |
| 两份独立 | `BK_PRE != BK_FINAL`；两份各有 MANIFEST 且时间戳不同；`BK_PRE` 停服后**零写入**（见下行「BK_PRE 冻结」） | 符合 | 停止；视为证据链污染，重建 |
| 备份自描述 | `BK` 内 `MANIFEST.sha256`（排除自身与 `manifest.hash.txt`）+ `manifest.hash.txt`（仅哈希值）；EVID 侧保留副本；`( cd "$BK" && sha256sum -c MANIFEST.sha256 )` 返回 0 | 所有条目可复算 | 停止；重建备份 |
| **BK_PRE 冻结** | 停服前生成 `EVID/backup/pre/bkpre.freeze-pre-stop.txt`（`find -printf '%p\t%s\t%T@'`）；停服后重跑同命令并 `diff` 必须为空；且 `sha256sum "$BK_PRE/MANIFEST.sha256"` 的首列 == `EVID/backup/pre/manifest.sha256-only.txt` | 完全一致 | 停止；说明 BK_PRE 被写入，证据链失效 |
| 归档可读 | `tar -tzf data.tar.gz >/dev/null`；`gzip -t` | 返回 0 | 停止；不得使用该备份 |
| 抽读（非秘密） | 从 `data.tar.gz` 用 `tar -xOzf` 流式取 1 个非秘密文件算 sha256 与源比较（**不落盘**） | 一致 | 停止；重做 |
| 抽读（秘密） | `.env` 用 `tar -xOzf data.tar.gz .env \| sha256sum` 与 B1 哈希比对；`sources-data` 用 B3 记录的流式摘要比对（**不落盘**） | 一致 | 停止；流程违规需记录 |
| `.env` | 备份副本 sha256 = 源；键计数一致；不被打印；mode 0600 | 一致 | 停止；流程违规需记录 |
| 曲库 | 清单条目数与 `library.count.txt` 一致；每个 sha256 与清单一致；可选全量复制后 `sha256sum -c` | 一致 | 停止；查明是否发生保存/边听边存 |
| 非秘密白名单内容 | `data.nonsecret.sha256`（B3 生成）逐文件 `sha256sum -c`（§4.5 演练） | 一致 | 停止；重做备份 |
| 生产静止 | 备份前后 `library.sha256`、`config.sha256`、`install-tree.sha256` 对比（只允许时间戳类差异被显式记录） | 无内容差异 | 停止；视为「有写入者」，重新排队 |
| 目录权限 | `stat -c '%a %U:%G' $BK` 为 `700 root:root`；文件 600 | 符合 | 修正后重检 |

### 4.5 隔离恢复验证（rehearsal）

> 目的：证明「备份可用」而不是「备份看起来存在」。**不得**对生产路径解包或覆盖。
> 现行代码候选 `scripts/` 下的 restore fixture 在 `docs/RESTORE_FIXTURE.md` 中**明确 BLOCKED**
> （在打开归档前就返回 `archive_scan_isolation_unproven`），因此**不得**把它当作恢复验证证据；
> 本演练是人工、显式、限定范围的步骤。

**草案步骤（Rev-2 / F3：秘密项不落盘，先流式校验）**：

```bash
# 演练对象：BK_PRE（停服前独立备份）；BK_FINAL 的可恢复性在 §5.6 用同一方法复核
RS=$EVID/rehearsal/restore-$(date +%Y%m%d-%H%M%S)
mkdir -p "$RS"
umask 077
# 0) 秘密项先做**流式**校验，不落盘（.env 与 sources-data 都含秘密）
{ tar -xOzf "$BK_PRE/data.tar.gz" sources-data | sha256sum; } \
  | diff -u "$BK_PRE/data.secret-stream.sources-data.sha256" - > "$EVID/rehearsal/secret-stream-sources.diff" \
  || { echo "REFUSE: sources-data 流式摘要不一致" >&2; exit 1; }
while IFS= read -r f; do
  printf '%s  ' "$f"; tar -xOzf "$BK_PRE/data.tar.gz" "$f" | sha256sum | awk '{print $1}'
done < <(tar -tzf "$BK_PRE/data.tar.gz" | grep -E '^\.env(\.|$)') \
  | diff -u "$BK_PRE/data.secret-stream.env.sha256" - > "$EVID/rehearsal/secret-stream-env.diff" \
  || { echo "REFUSE: .env 类流式摘要不一致" >&2; exit 1; }
# 1) 只把**非秘密**白名单项解到 rehearsal；显式排除 .env 与 sources-data
#    R5-1/Rev-4：解包 rc 必须显式检查（失败不得继续做「空清单 = N/A」的判断）
if ! tar -xzf "$BK_PRE/data.tar.gz" -C "$RS" \
    --exclude='./.env' --exclude='.env' --exclude='.env.*' --exclude='./.env.*' \
    --exclude='./sources-data' --exclude='sources-data' \
    --exclude='./sources-data/*' --exclude='sources-data/*'; then
  echo "REFUSE: rehearsal 非秘密解包失败（tar rc != 0）" >&2; exit 1
fi
# 2) 校验「秘密项没有明文落盘」：这两项必须不存在（含任意层级的 .env* / sources-data*）
[ ! -e "$RS/.env" ] || { echo "REFUSE: rehearsal 出现明文 .env" >&2; exit 1; }
[ ! -e "$RS/sources-data" ] || { echo "REFUSE: rehearsal 出现明文 sources-data" >&2; exit 1; }
if find "$RS" \( -name '.env' -o -name '.env.*' -o -name 'sources-data' -o -name 'sources-data.*' \) -print -quit | grep -q .; then
  echo "REFUSE: rehearsal 出现秘密项明文（含子目录）" >&2; exit 1
fi
# 3) 非秘密项**内容级**比对（R5）：对解出的树重新生成 sha256 清单，与 B3 的 data.nonsecret.sha256 逐行比对
( cd "$RS" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum ) > "$EVID/rehearsal/extracted.nonsecret.sha256"
if [ -s "$BK_PRE/data.nonsecret.sha256" ]; then
  diff -u "$BK_PRE/data.nonsecret.sha256" "$EVID/rehearsal/extracted.nonsecret.sha256" \
    > "$EVID/rehearsal/nonsecret.diff" \
    || { echo "REFUSE: 非秘密项内容级清单不一致（见 nonsecret.diff）" >&2; exit 1; }
else
  # R5-1/Rev-4：只有 B3 明确记录「0 个非秘密项」时才允许 N/A；计数缺失/非 0 → REFUSE
  cnt=$(cat "$BK_PRE/data.nonsecret.count.txt" 2>/dev/null || echo missing)
  [ "$cnt" = "0" ] || { echo "REFUSE: 非秘密清单为空但 B3 计数=${cnt}（应为 0），疑似解包/生成异常" >&2; exit 1; }
  echo "非秘密白名单项为空（data.nonsecret.count.txt=0，已核实），内容级比对记为 N/A" > "$EVID/rehearsal/nonsecret.diff"
fi
# 4) 安装树：只验证「按 FPK + 漂移文件可重建」的可判定子集（不在此处全树恢复）
#    验证：FPK 存在且哈希一致；drift.sha256 与备份一致；install-tree*.sha256 中漂移文件条目一致
# 5) 曲库（若做了全量复制）：sha256sum -c library.sha256（在副本目录内，路径重写或 -C）
# 6) 权限：记录 rehearsal 内文件的 mode/owner 与备份前记录对比（不要求完全一致，需显式列出差异）
# 7) 清理：删除 rehearsal 目录本身并记录；秘密项从未落盘，因此不存在额外秘密副本需要清理
```

**通过条件**：① 归档可解；② 秘密项流式摘要与 B1/B3 记录一致；③ rehearsal 内**不存在** `.env`/`sources-data` 明文；
④ 非秘密项**内容级**清单与 `data.nonsecret.sha256` 一致（逐文件 sha256，非仅比条目；空集为 N/A 并记录）；
⑤ 曲库（若复制）逐文件一致；⑥ 未对生产路径做任何读写。
**失败条件**：解包失败、流式摘要不一致、rehearsal 出现秘密明文、非秘密项内容不一致、出现生产路径写入、
差异无法解释 → **不得进入安装/升级步骤**。
**边界**：该演练**不**证明 `.env`/`sources-data` 可被实际解包恢复（为避免明文落盘，秘密项只做流式校验）；
这是本演练的已知限制，必须在报告中写明，并由 §7 D6/R6 的保密决策决定是否允许一次「受限明文恢复演练」。

### 4.6 未来配置 jobs DB 后的一致快照方案（本轮不执行）

> 触发条件：目标版本部署后按 §5 配置 `FNMUSIC_MANUAL_SAVE_JOBS_DB`，并实际创建账本文件。
> 依据：检查单 §0.1-4 / §1.2；`docs/OFFLINE_SAVE_JOBS.md` schema v2 的 fail-closed 行为。

**前置（缺一不可）**：

1. 已停止/排空所有写者：停 unit（网关进程为唯一写入者）、确认容器不写该文件、`/proc/*/fd` 中
   无任何进程持有该 DB（含 `-wal`/`-shm`/`-journal`）；
2. 已确定并记录实际 DB 路径、父目录、挂载、属主/权限；确认不在 repo/target 内（否则升级会替换）；
3. 已读出 `PRAGMA journal_mode`（**不得假设**：账本代码未显式设置 `journal_mode`，默认应为 `delete`；
   若实际为 `wal`，快照必须覆盖 WAL 内容并记录 checkpoint 状态）。

**草案（在 NAS 上以 root 用 Python stdlib；宿主无 sqlite3 CLI）**：

```bash
DB='<窗口内由配置确定的绝对路径>'
BKDB="$BK/jobs.db.snapshot"
# 注意：路径含 ? # % 等 URI 特殊字符时需转义；不确定时先复制到不含特殊字符的临时只读路径再打开。
/usr/bin/python3 - "$DB" "$BKDB" <<'PY'
import json, os, sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
# 只读打开源，避免任何写；backup() 读取的是已提交内容（含 WAL 已提交页）
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=5)
# journal_mode 必须从源库读取（备份副本是新库，其 journal_mode 不反映源库）
src_journal_mode = src.execute("PRAGMA journal_mode").fetchone()[0]
src_user_version = src.execute("PRAGMA user_version").fetchone()[0]
dst = sqlite3.connect(dst_path)
try:
    src.backup(dst)
    dst.commit()
    integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    print(json.dumps({"integrity_check": integrity, "src_user_version": src_user_version,
                      "src_journal_mode": src_journal_mode, "size": os.path.getsize(dst_path)}))
finally:
    dst.close(); src.close()
PY
sha256sum "$BKDB" > "$BK/jobsdb.sha256"
# 校验：备份副本无 sidecar；源端 sidecar 保留原样（不删除/不截断）
ls -la "$DB" "$DB-wal" "$DB-shm" "$DB-journal" 2>/dev/null || true
```

**通过判据**：`integrity_check == "ok"`；副本无 `-wal/-shm/-journal`；`src_user_version` 与当前 schema 一致
（v2 为期望；v1 需按合同保持原样、不得迁移）；`src_journal_mode` 已记录（`delete` 或 `wal` 都要有对应处置）；
哈希/大小记录；恢复演练在副本上进行（不打开生产库）。
**失败判据**：integrity 非 ok、副本残留 sidecar、无法确认停写、路径在 repo/target 内、schema 未知 →
停止部署/回滚，并按检查单 §6「账本回滚」处理（不删除/不覆盖当前 DB）。

### 4.7 备份不能证明什么

- 不能证明「备份时没有并发写入」——只能证明备份前后清单一致、采样窗口内无观察到的变化；
- 不能证明「停服前独立备份」与「停服后最终备份」的差异都能被解释——两者的差异必须逐项说明，且
  `BK_PRE` 绝不可被 `BK_FINAL` 覆盖（F1）；也不能用 `BK_FINAL` 冒充 §0.1-4 要求的「停机前」证据；
- 不能证明备份副本在所有恢复路径上语义等价（`.venv-proxy`、ACL/xattr、Btrfs 快照/COW 残留）；
- 不能证明 `/vol1` 上 0700 的机密性（trimacl：管理员可读）；
- 不能证明恢复后服务可用（必须走 §4.5 + §5.8）；
- 不能证明 jobs DB 的 schema 兼容性（v1/v2、未来版本）；
- 不能替代 `upgrade_init` 的白名单备份与 `upgrade_callback` 的恢复（那是升级流程材料，会被删除）。

---

## 5. 部署窗口逐步 runbook（草案）

> 全部步骤**未执行**。执行前提见文首。每步的「证据」列给出必须留存的文件/哈希/时间。
> 统一约定：设备时区 CST，执行侧同时记 JST；敏感值只记录哈希/计数；任何一步失败按该步「失败/回滚判据」停止，
> 不得跳步或「先装再修」。

### 5.0 窗口前置（G0 授权与独立复核）

| 前置 | 要求 | 判定 |
| --- | --- | --- |
| §0.1 状态 | 已按本窗口事实 PASS 并经独立复核 | 否则不进入本 runbook |
| §0.2 状态 | 若窗口含写开关启用/P7 写入，必须已独立 PASS | 本 runbook 默认**不**开启写开关 |
| 授权 | 覆盖本窗口全部动作（停服/停容器/编辑 `.env`/安装/回滚/写 `/vol1` 备份目录） | 记录授权来源与边界 |
| G0-P5 | 若含 writer-drain/rollback 演练，需 G0-P5 通过或明确 N/A | 见 `docs/G0_MAINTENANCE_PROPOSAL.md` |
| 目标版本 | 新 FPK 路径 + SHA-256 + VERSION 由用户确认（检查单 §3 建议 2.11.0，**未决定**） | 记录 |
| 回滚目标 | 用户确认（§6；当前 unknown） | 未确认则只允许 R0/R1（§6.5） |
| 人员 | 执行者 + 独立复核者（未参与实现/部署） | 记录 |

### 5.1 Step 0：窗口开启与只读基线

**命令草案**：

```bash
APP=$(readlink -f /vol1/@appcenter/fnmusic-ext); REPO=$APP/repo
VOL=$(findmnt -T "$APP" -no TARGET | sed -n 's#^\(/vol[0-9]\+\)/.*#\1#p'); VOL=${VOL:-/vol1}
TS=$(date +%Y%m%d-%H%M%S); EVID=$VOL/fnmusic-ext-ops-$TS
umask 077; mkdir -p "$EVID"/{pre,stop,backup,rehearsal,install,post,rollback}
TZ=Asia/Tokyo date -Iseconds > "$EVID/00-window-time.txt"; date -Iseconds >> "$EVID/00-window-time.txt"
{
  uname -a; cat /usr/trim/etc/version; docker --version; python3 -V
  printf 'TRIM_PKGVAR=%s\n' "${TRIM_PKGVAR:-<unset>}"
  systemctl is-active fnmusic-ext.service; systemctl is-enabled fnmusic-ext.service
  systemctl show fnmusic-ext.service -p ActiveState,SubState,MainPID,InvocationID,NRestarts,ExecMainStartTimestamp
  systemctl show fnmusic-ext.service -p DropInPaths
  systemctl show fnmusic-ext.service -p WorkingDirectory,Restart,KillMode,TimeoutStartUSec,TimeoutStopUSec
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  curl -sS -m 5 -w '\nhttp=%{http_code}\n' http://127.0.0.1:8774/healthz
  /usr/bin/python3 "$REPO/proxy/takeover.py" status
} > "$EVID/pre/baseline.txt" 2>&1
```

**预期**：`active`、`enabled`、`NRestarts=0`、容器 healthy、healthz 200、`takeover status` 显示
target=`proxy`、upstream=`official` 且 inode/进程一致。
**失败判据**：任一状态与 §2 基线明显不符（服务未运行、容器不健康、socket 身份异常、有 drop-in、
有未知 systemd 变更）→ **停止窗口并重新只读盘点**（不得"顺手修"）。
**证据**：`pre/baseline.txt`、`systemctl show` 全字段快照、容器 inspect、socket `stat`、ss/ps 快照。

### 5.2 Step 1：停机前检查（含只读盘点与备份前置）

**清单**：

1. 重新采集 §2.1–§2.10 中所有 `observed` 项（版本/包哈希/unit/容器/配置来源/库/暂存/日志）；
2. 核对 FPK 与安装树：用 `install-tree.sha256` 与已有清单比对，确认只有已知漂移（`app.js`、
   `run_proxy.sh` mtime）；
3. 核对包哈希与 `DEVICE_INVENTORY(s)` 登记一致；
4. 确认开关现状（**只读**：status 响应 + `.env` 键计数），总开关当前为 `true`（阻断项，需要窗口内按
   §5.3 关闭）；
5. 确认曲库/暂存当前清单与 §2 一致；暂存为空；
6. 确认无在途（§5.3 的采样，先做一遍作为基线）；
7. **完成停服前独立备份 `BK_PRE`（含 B12 MANIFEST + `manifest.hash` + freeze 快照 + selftest）+ 隔离恢复演练**（§4.3–§4.5）——**在 §5.4 停服之前**；
8. 记录 `restore-plan` 预检结果：`takeover restore-plan` 必须返回可恢复计划（不是错误）。

```bash
/usr/bin/python3 "$REPO/proxy/takeover.py" restore-plan > "$EVID/pre/restore-plan.json" 2>&1; echo "rc=$?"
/usr/bin/python3 "$REPO/proxy/takeover.py" status > "$EVID/pre/takeover-status.json" 2>&1
# socket 清单（仅 inventory：含官方 socket，属预期；不参与 §5.4-3.5 的扩展静止判定）
ss -xlpn | grep -E 'fnmusic-ext\.sock' > "$EVID/pre/ss.unix.ext.txt" 2>&1 || true
ss -xlpn | grep -E '/var/run/trim_music(\.socket|_upstream\.socket)' > "$EVID/pre/ss.unix.official.txt" 2>&1 || true
ss -ltnp | grep -E ':(8768|8770|8772|8774)\b' > "$EVID/pre/ss.tcp.txt" 2>&1 || true
ps -eo pid,ppid,user,etime,comm,args --sort=pid > "$EVID/pre/ps.txt" 2>&1
```

**失败判据**：`restore-plan` 非 0 / 返回拒绝（说明停止后无法验证恢复 → **绝对不停止**）；
备份/演练未完成或失败；清单与历史不一致且无法解释；包哈希变化；开关实际生效值无法确定。

### 5.3 Step 2：关闭写入开关 + 在途盘点（W1/W2/请求级可观察范围）

> **Rev-2 / F2 的重要范围修正**：本步发生在 §5.4 停容器**之前**，此时容器内 WebUI（W3）与 unit 内 proxy
> 的后台任务（推荐/喜欢定时、日志保留、歌词 sweep、`.env` 热加载等）**仍在运行**。所以本步只做三件事：
> ① 关闭写开关（阻止新的手动保存/整歌单写入）；② 盘点**请求级在途**与 **W1（曲库）/W2（暂存）** 是否静止；
> ③ 列出仍活跃的写者并声明其将在何时停止。**本步不得断言「全部写者静止」**；该断言只能在 §5.4 停容器
> 之后做（§5.4 3.5），并作为 §5.5 最终备份的前置。
>
> 本步仍需**当前无 jobs DB** 的前提；若 §4.3 B11 判定 `JOBS_DB` 已配置，本流程不适用（转 §4.6 重新规划）。

**2.1 关闭开关（需已授权；先备份、加锁、原子改写、强制 diff）**

```bash
# 0) 前置：窗口内暂停管理页操作；记录容器启动时间（用于判断编辑期间容器是否重启）
docker inspect -f '{{.State.StartedAt}}' fnmusic-sources > "$EVID/stop/container.started-before.txt"
# 1) 写前快照（含 mode/owner；后续失败恢复的权威来源；记录数值 uid/gid 供属主断言）
cp -p "$REPO/.env" "$EVID/stop/env.before-stop"
sha256sum "$REPO/.env" > "$EVID/stop/env.before-stop.sha256"
stat -c '%a %u %g %U:%G %n' "$REPO/.env" > "$EVID/stop/env.before-stop.stat"
# ACL/xattr 取证（F-R6：os.replace 用新 inode，**不会**复制 trimacl/xattr；这里只记录，作为已知限制证据）
if command -v getfacl >/dev/null 2>&1; then
  getfacl -p "$REPO/.env" > "$EVID/stop/env.before-stop.acl" 2>&1 || true
else
  echo "getfacl absent" > "$EVID/stop/env.before-stop.acl"
fi
# 2) 与容器/其它窗口的编辑互斥：使用独立锁文件（/run tmpfs，不与产品锁冲突）
exec 9>>/run/fnmusic-ext-deploy-env.lock
flock -n 9 || { echo "REFUSE: 另一个窗口正在编辑 .env" >&2; exit 1; }
# 3) 原子改写：保留 export 前缀/行尾注释/换行风格/原 mode 与**属主**（R2：fchown 后再 replace）
/usr/bin/python3 - "$REPO/.env" <<'PY'
import os, re, sys, tempfile
path = sys.argv[1]
keys = ("FNMUSIC_MANUAL_SAVE_ENABLED", "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED")
st = os.stat(path)                      # 保留原 mode/uid/gid，避免 replace 后属主漂移为 root
data = open(path, "rb").read()
eol = b"\r\n" if b"\r\n" in data else b"\n"
lines = data.splitlines(keepends=True)
key_re = rb"(?P<key>" + b"|".join(k.encode() for k in keys) + rb")"
line_re = re.compile(rb"^(?P<prefix>[ \t]*(?:export[ \t]+)?)" + key_re +
                     rb"(?P<sep>[ \t]*=[ \t]*)(?P<value>.*?)(?P<comment>[ \t]+#.*)?(?P<eol>\r?\n)?$")
seen, style_export, out = set(), False, []
for raw in lines:
    m = line_re.match(raw)
    if not m:
        out.append(raw); continue
    seen.add(m.group("key"))
    if b"export" in m.group("prefix"):
        style_export = True
    out.append(m.group("prefix") + m.group("key") + m.group("sep") + b"false"
               + (m.group("comment") or b"") + (m.group("eol") or b""))
for key in keys:
    if key.encode() not in seen:
        out.append((b"export " if style_export else b"") + key.encode() + b"=false" + eol)
    if out and not out[-1].endswith((b"\n", b"\r")):
        out.append(eol)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".env.rev4.")
try:
    # R2/R4 修订：os.fchmod/os.fchown 的第一个参数是 **fd**（不是路径）；在最终 fsync 之前
    # 修改 mode/owner，使内容与元数据一起落盘（崩溃一致性）。fnOS/Linux 有 os.fchown；
    # 非 Linux 平台不具备该能力 → fail-closed（不要静默跳过属主保留）。
    if not hasattr(os, "fchown"):
        raise OSError("os.fchown unavailable on this platform; refuse (runbook targets Linux)")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"".join(out))
        fh.flush()
        os.fchmod(fh.fileno(), st.st_mode & 0o7777)   # 保留原文件模式（fd 形式）
        os.fchown(fh.fileno(), st.st_uid, st.st_gid)  # 保留原属主（kurumi:Administrators），不变 root（fd 形式）
        os.fsync(fh.fileno())                          # 最后 fsync：内容+元数据一起提交（在 fchmod/fchown 之后）
    os.replace(tmp, path)
except BaseException:
    try: os.unlink(tmp)
    except OSError: pass
    raise
try:
    dfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try: os.fsync(dfd)
    finally: os.close(dfd)
except OSError:
    pass
# R5/Rev-5：f-string 表达式内不得含反斜杠（PEP 701 仅 Python 3.12+；目标设备是 3.11.2），
# 因此 eol 名在 f-string 外预计算，输出改用 % 格式化。
eol_name = "CRLF" if eol == b"\r\n" else "LF"
print("matched_key_lines=%d eol=%s export_style=%s" % (len(seen), eol_name, style_export))
PY
sha256sum "$REPO/.env" > "$EVID/stop/env.after-stop.sha256"
stat -c '%a %u %g %U:%G %n' "$REPO/.env" > "$EVID/stop/env.after-stop.stat"
# ACL 取证（F-R6：仅记录；差异属已知限制，见下方说明）
if command -v getfacl >/dev/null 2>&1; then
  getfacl -p "$REPO/.env" > "$EVID/stop/env.after-stop.acl" 2>&1 || true
else
  echo "getfacl absent" > "$EVID/stop/env.after-stop.acl"
fi
# 失败恢复函数（恢复备份、还原 mode/属主、再校验哈希）
restore_env() {
  cp -p "$EVID/stop/env.before-stop" "$REPO/.env"
  chmod 600 "$REPO/.env"
  chown "$(stat -c %u "$EVID/stop/env.before-stop"):$(stat -c %g "$EVID/stop/env.before-stop")" "$REPO/.env" 2>/dev/null || true
  sha256sum -c "$EVID/stop/env.before-stop.sha256" >/dev/null || { echo "FATAL: .env 恢复校验失败" >&2; exit 1; }
}
# 4) 强制校验（R2）：结构化键值判定，而不是行数启发式
#    - 允许 **0 行变化**（两键本已是 false → 幂等窗口，合法）
#    - 允许两键同时替换（最多 4 行 diff，例如两个键都从 true→false）
#    - `KEY = value`（= 两侧空格）按 key=value 归一化后比较
#    - 其它键的值不得变化；目标键最终值必须为 false
if ! /usr/bin/python3 - "$EVID/stop/env.before-stop" "$REPO/.env" <<'PY'
import re, sys
before, after = sys.argv[1], sys.argv[2]
TARGETS = {"FNMUSIC_MANUAL_SAVE_ENABLED", "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED"}
line_re = re.compile(rb"^[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(?P<value>.*?)[ \t]*(?:#.*)?$")
def parse(path):
    values = {}
    for raw in open(path, "rb").read().splitlines():
        m = line_re.match(raw)
        if not m:
            continue
        value = m.group("value").strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in (b'"', b"'"):
            value = value[1:-1]
        values[m.group("key").decode()] = value.decode("utf-8", "surrogateescape").lower()
    return values
a, b = parse(before), parse(after)
missing = sorted(k for k in TARGETS if k not in b)
not_false = sorted(k for k in TARGETS if b.get(k) != "false")
changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
non_target = sorted(set(changed) - TARGETS)
if missing or not_false or non_target:
    print(f"REFUSE: missing={missing} not_false={not_false} non_target_changed={non_target}")
    raise SystemExit(1)
print("KEYS_OK changed=" + (",".join(changed) if changed else "none(idempotent)"))
PY
then
  echo "REFUSE: .env 键级校验失败" >&2; restore_env; exit 1
fi
# 4b) 属主/模式断言（R2）：replace 后必须与写前一致，且 mode=600
read -r bm bu bg _ < "$EVID/stop/env.before-stop.stat"
read -r am au ag _ < <(stat -c '%a %u %g %U:%G %n' "$REPO/.env")
if [ "$am" != "600" ] || [ "$au" != "$bu" ] || [ "$ag" != "$bg" ]; then
  echo "REFUSE: .env mode/owner 变化（before=${bm}:${bu}:${bg} after=${am}:${au}:${ag}）" >&2
  restore_env; exit 1
fi
# 4c) 证据：保留 diff 供人工复核（不参与判据；0 行 = 幂等窗口）
diff -u "$EVID/stop/env.before-stop" "$REPO/.env" > "$EVID/stop/env.diff" || true
# 5) 复核两开关的**实际解析值**（只读 GET；见下方 §0.2 标注）
curl -s --unix-socket "$APP/fnmusic-ext.sock" \
  -H 'x-trim-userid: 1000' -H 'x-trim-isadmin: true' -H 'x-trim-username: kurumi' -H 'x-fnmusic-save: 1' \
  http://127.0.0.1/app/fnmusic-ext/api/manual-save/status \
  | tee "$EVID/stop/status.after-switch.json"
# 6) 并发检测：5 s 后再取一次哈希；若与 after-stop 不同，说明有其它写者（容器 WebUI/外部）在改 .env
sleep 5
sha256sum "$REPO/.env" > "$EVID/stop/env.after-5s.sha256"
if ! sha256sum -c "$EVID/stop/env.after-stop.sha256" >/dev/null 2>&1; then
  echo "REFUSE: .env 在编辑后 5s 内被其它进程改写（容器 WebUI/外部）" >&2
  restore_env
  echo "已从 env.before-stop 恢复；请先停止容器（经授权）后再重试本步，或查明写者" >&2
  exit 1
fi
```

> **R2/R4 说明（Rev-4）**：本步不再用「diff 行数」作判据——两键本已为 false 时 diff 为 0 行是**合法幂等**；
> 两键都需改写时 diff 最多 4 行（每键 `-true`/`+false`）也是合法。判据是「键级集合」：其它键值不变、
> 两键最终解析值均为 false、mode/owner 与写前一致、5 s 后哈希稳定。`=` 两侧空格按 key=value 归一化比较。
> 属性保留用 **fd 形式**：`os.fchmod(fd, …)`/`os.fchown(fd, …)` 在 `with os.fdopen(fd, "wb")` 内调用，
> 并**先改元数据、后 `fsync(fd)`**（元数据与内容一起提交），最后 `os.replace` + 目录 fsync；若平台无
> `os.fchown` 则 fail-closed（不静默放弃属主保留）。
>
> **已知限制（F-R6）**：`os.replace` 使用**新 inode**，因此只保留 mode/uid/gid，**不复制 POSIX ACL/xattr**
> （`/vol1` 的 trimacl 正是实际访问机制）。本步已用 `getfacl` 记录 before/after 供证据对照，但**不要求**
> 实现 ACL 复制；若窗口内发现 ACL 差异，必须在报告中显式声明并由 §7 D6/R6 决定是否接受（备份/恢复语义
> 不因此改变——`.env` 的访问权限以 mode/uid/gid 为准的那部分已保留，trimacl 部分属已知未覆盖项）。
> 另：`eol_name` 预计算是因为目标设备 Python **3.11.2** 不允许 f-string 表达式内出现反斜杠（PEP 701 仅 3.12+）。

> **§0.2 标注（Rev-2 / F12）**：上面带 `x-trim-*` 的请求只是检查单 §0.2 所述的「指定头部下的接口烟雾检查」，
> **不是身份来源或管理员授权证明**；不得用其成功/失败推断 §0.2 是否 PASS，也**不得**用它调用任何写接口
> （本 runbook 全程只做 GET status）。

**预期**：键级校验输出 `KEYS_OK changed=…`（`none(idempotent)` = 两键本已 false，合法）；
`env.diff` 只含两个目标键的值行变化（0 行或 ≤4 行，均合法）；`.env` mode/owner 与写前一致（600 + 原属主）；
`status.after-switch.json` 返回 `enabled:false`（`manual_save_route.load_config()` per-request 读 `.env`，无需重启）；
目标版本部署后，整歌单端点应返回 403 `disabled`（总开关关）；5 s 复核哈希稳定。
**失败判据**：目标键最终值非 false；其它键值变化；mode/owner 变化；status 仍 `enabled:true`；5 s 内哈希变化 →
任一步失败都 `restore_env` 后停止（不得带病继续）。
**注意**：编辑 `.env` 是生产配置变更，必须在 §0.1 PASS + 授权后；**不得**以此作为"修复盘点结果"。

**2.2 在途盘点（W1/W2/请求级；**不含**容器写者）**

采样（间隔 ≥ 30 s 连续 2–3 次，每次记录 exit code 与时间）：

```bash
# a) unit cgroup 进程与线程（找同步单曲保存线程 fnmusic-manual-save）
MAIN=$(systemctl show fnmusic-ext.service -p MainPID --value); echo "main_pid=$MAIN"
systemctl status fnmusic-ext.service --no-pager -l > "$EVID/stop/unit.status.txt" 2>&1
for pid in $(systemctl show fnmusic-ext.service -p MainPID --value) \
           $(pgrep -f 'webui_gateway.py|uvicorn app:app|takeover.py' || true); do
  echo "== pid=$pid =="; cat "/proc/$pid/comm" 2>/dev/null
  for t in /proc/$pid/task/*/comm; do cat "$t" 2>/dev/null; done | sort | uniq -c
done > "$EVID/stop/threads.txt" 2>&1
# b) 单曲保存临时目录（存在即可能有在途）
ls -lad /tmp/fnmusic-manual-save-* 2>/dev/null > "$EVID/stop/tmp-workdirs.txt" || echo none > "$EVID/stop/tmp-workdirs.txt"
# c) 持有曲库/暂存/仓库 fd 的进程（全机扫描，只读；路径用 $LIB/$STG，不硬编码）
for pid in /proc/[0-9]*; do
  p=${pid#/proc/}
  ls -l /proc/$p/fd 2>/dev/null | grep -qF -e "$LIB" -e "$STG" -e "$REPO" \
    && echo "pid=$p $(cat /proc/$p/comm 2>/dev/null)"
done > "$EVID/stop/fd-holders.txt" 2>&1
# d) 两个 socket 上的 established 连接（只读记录）：本应用 socket 与官方 socket **分开记录**；
#    官方连接（真实设备当前 12 条）属于官方应用的预期连接，仅作上下文，不参与后续扩展静止判定（R5/Rev-5）
ss -x state established 2>/dev/null | grep -E 'fnmusic-ext\.sock' \
  > "$EVID/stop/ss.unix.established.txt" 2>&1 || true
ss -x state established 2>/dev/null | grep -E '/var/run/trim_music(\.socket|_upstream\.socket)' \
  > "$EVID/stop/ss.official.established.txt" 2>&1 || true
# e) W1/W2 稳定采样（连续两次相同）；W3（.env/sources-data/recommend/logs）本步**不**要求静止
find "$LIB" -maxdepth 1 -type f -printf '%P %s %T@\n' | sort > "$EVID/stop/lib.sample1.txt"
find "$STG" -mindepth 1 -maxdepth 1 -print | sort > "$EVID/stop/stg.sample1.txt"
sleep 30
# 重复 a–e，命名为 sample2；两次样本一致才算 W1/W2 稳定
```

**断言与边界（必须写进报告）**：

- **可以**说：「在 T1/T2 两个采样点，未观察到进程持有曲库/暂存 fd、无 `fnmusic-manual-save-*` 工作目录、
  无 established 保存请求连接、曲库/暂存清单未变、网关线程名中无 `fnmusic-manual-save`，且两个写开关
  的实际生效值为 false。」
- **不可以**说：「全部写者静止」。本步容器（W3：`repo/.env*`、`sources-data`、`recommend_cache`、`logs`）与
  unit 内 proxy 后台任务仍在运行；它们必须在 §5.4 停容器/停 unit 后才静止（§5.4 3.5 才做该断言）。
- **不可以**说：「历史无写入」「队列已排空」「无其他写者」。理由：journal 易失且只有当前 boot、
  应用日志停在 2026-11-01、无持久任务队列（`DEVICE_INVENTORY_FOLLOWUP_20261107.md` §8.1/§8.2）。
- **目标版本/未来 DB 的额外要求**：所有 job 必须处于终态（GET job 的状态）、`/proc/*/fd` 无 jobs DB
  的打开句柄、无 `-wal/-shm/-journal` 活动；不得用「MainPID 消失」或「unit inactive」代替；
  **不得强杀进程排空**（检查单 §0.1-2 明令禁止）。
- **仍活跃的写者清单（必须列出并在后续步骤核对）**：容器内 WebUI 与 supervisor 子进程（W3，§5.4 stop 后消失）、
  unit 内 proxy 后台任务（W3，unit stop 后消失）、journald/docker 日志（W6，预期外部写入，不要求停止）。

**失败判据**：任一采样出现 W1/W2 fd/工作目录/连接/清单变化 → 不停止、查明原因；`.env` 被外部改写 →
先停容器（经授权）再重试；无法证明 W1/W2 稳定 → 停止窗口。

### 5.4 Step 3：停服（unit + 容器）与官方 socket 恢复验证

```bash
# 3.1 先停 unit：ExecStopPost 会 takeover restore，把官方 socket 复位
systemctl stop fnmusic-ext.service; RC=$?; echo "stop_rc=$RC" | tee "$EVID/stop/systemctl-stop.txt"
systemctl show fnmusic-ext.service -p ActiveState,SubState,ExecMainStatus,NRestarts >> "$EVID/stop/systemctl-stop.txt"
# 3.2 socket 状态核对（readlink/stat 允许失败，但必须记录 rc 与现象，不得静默忽略）
/usr/bin/python3 "$REPO/proxy/takeover.py" status > "$EVID/stop/takeover-status.after-stop.json" 2>&1
readlink -e /var/run/trim_music.socket; echo "readlink_rc=$?"
stat -c 'type=%F mode=%a owner=%U:%G inode=%i' /var/run/trim_music.socket; echo "stat_rc=$?"
# 期望：target 为 official（不在代理态）；/_ext/livez 不再响应代理
curl -sS -m 5 -o /dev/null -w 'ext_livez_http=%{http_code}\n' --unix-socket /var/run/trim_music.socket http://localhost/_ext/livez || true
# 3.3 官方音乐可用性（只读；未登录预期 401/INVALID TOKEN）
curl -sS -m 8 -o /dev/null -w 'official_probe_http=%{http_code}\n' \
  --unix-socket /var/run/trim_music.socket 'http://localhost/music/api/v1/search/track?keyword=test' || true
# 3.4 停容器（停掉 W3 写者：容器内 WebUI/音源 supervisor）
docker stop fnmusic-sources; echo "docker_stop_rc=$?" | tee "$EVID/stop/docker-stop.txt"
docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} finished={{.State.FinishedAt}}' fnmusic-sources >> "$EVID/stop/docker-stop.txt"
docker logs --tail 100 fnmusic-sources > "$EVID/stop/docker.log.txt" 2>&1 || true
# 3.5 停容器后的「全部写者静止」采样与断言（Rev-2 / F2；R3：0 s 与 +15 s 两轮、含 W3 全部对象、ss 过滤 LISTEN）
#     顺序：先确认容器 exited，再采样；任何一步不满足都不得进入最终备份，也不得写 marker
docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}}' fnmusic-sources > "$EVID/stop/container.after-stop.txt"
grep -q 'status=exited' "$EVID/stop/container.after-stop.txt" \
  || { echo "REFUSE: 容器未 exited，不能开始静止采样" >&2; exit 1; }
# 定义一轮采样函数（0 s 与 +15 s 各调用一次；两轮结果必须一致）
# R3/Rev-4 修订：W3 三类对象的文件名统一为 `w3.<tag>.<f>.sha256`（f=env|sources-data|cache-logs），
# 生成与比对严格同一路径；并在比对前做存在性断言，避免路径打错静默变成「diff 找不到文件」。
sample_writers() {
  local tag="$1"
  for pid in /proc/[0-9]*; do
    p=${pid#/proc/}
    ls -l /proc/$p/fd 2>/dev/null | grep -qF -e "$LIB" -e "$STG" -e "$REPO" \
      && echo "pid=$p $(cat /proc/$p/comm 2>/dev/null)"
  done > "$EVID/stop/fd-holders.$tag.txt" 2>&1
  ls -lad /tmp/fnmusic-manual-save-* 2>/dev/null > "$EVID/stop/tmp-workdirs.$tag.txt" \
    || echo none > "$EVID/stop/tmp-workdirs.$tag.txt"
  # established 连接（R5/Rev-5）：只统计**本应用** `fnmusic-ext.sock`；官方 `trim_music.socket` 上的
  # 连接是官方应用自身的预期连接（真实设备当前 12 条），不能算作「扩展未静止」而误拒。
  ss -x state established 2>/dev/null | grep -E 'fnmusic-ext\.sock' \
    > "$EVID/stop/ss.unix.established.$tag.txt" 2>&1 || true
  # 官方 socket 连接仅作上下文记录，不参与「扩展静止」判定
  ss -x state established 2>/dev/null | grep -E '/var/run/trim_music(\.socket|_upstream\.socket)' \
    > "$EVID/stop/ss.official.established.$tag.txt" 2>&1 || true
  # W3 全部对象（统一命名：w3.<tag>.env|sources-data|cache-logs.sha256）
  sha256sum "$REPO/.env" > "$EVID/stop/w3.$tag.env.sha256"
  find "$REPO/sources-data" -type f -print0 | sort -z | xargs -0 -r sha256sum \
    > "$EVID/stop/w3.$tag.sources-data.sha256"
  find "$REPO/recommend_cache" "$REPO/logs" -type f -print0 2>/dev/null | sort -z \
    | xargs -0 -r sha256sum > "$EVID/stop/w3.$tag.cache-logs.sha256"
  # 残留进程（R5/Rev-5）：先按扩展进程名全量取，再按本部署 $REPO 归属拆分。
  # - residual-procs.*：含本部署 repo 路径 → 属扩展残留，出现即 REFUSE；
  # - residual-unattributed.*：不含 repo 的候选（典型为**容器内** uvicorn app:app；容器 exited 后
  #   宿主 ps 中不应再出现）→ 同样出现即 REFUSE，避免「按 $REPO 过滤漏检」。
  ps -eww -o pid,ppid,user,etime,args --sort=pid > "$EVID/stop/ps.full.$tag.txt" 2>&1
  grep -E 'takeover\.py|webui_gateway\.py|uvicorn app:app' "$EVID/stop/ps.full.$tag.txt" \
    | grep -v grep > "$EVID/stop/residual-all.$tag.txt" 2>&1 || true
  grep -F -- "$REPO" "$EVID/stop/residual-all.$tag.txt" \
    > "$EVID/stop/residual-procs.$tag.txt" 2>&1 || true
  grep -vF -- "$REPO" "$EVID/stop/residual-all.$tag.txt" \
    > "$EVID/stop/residual-unattributed.$tag.txt" 2>&1 || true
}
sample_writers t0            # ≈ 停容器后 0 s
sleep 15
sample_writers t15           # +15 s
# 两轮逐项比对（先断言六个 W3 文件都存在；任一不一致 → REFUSE）
for f in env sources-data cache-logs; do
  a="$EVID/stop/w3.t0.$f.sha256"; b="$EVID/stop/w3.t15.$f.sha256"
  [ -f "$a" ] || { echo "REFUSE: 采样文件缺失: $a" >&2; exit 1; }
  [ -f "$b" ] || { echo "REFUSE: 采样文件缺失: $b" >&2; exit 1; }
  diff -u "$a" "$b" > "$EVID/stop/w3.$f.t0-vs-t15.diff" \
    || { echo "REFUSE: W3/$f 在 0s 与 +15s 之间变化（见 w3.$f.t0-vs-t15.diff）" >&2; exit 1; }
done
# 其余采样文件的存在性断言（缺失即停止，打印具体路径）
for p in "$EVID/stop/fd-holders.t0.txt" "$EVID/stop/fd-holders.t15.txt" \
         "$EVID/stop/tmp-workdirs.t0.txt" "$EVID/stop/tmp-workdirs.t15.txt" \
         "$EVID/stop/residual-all.t0.txt" "$EVID/stop/residual-all.t15.txt" \
         "$EVID/stop/residual-procs.t0.txt" "$EVID/stop/residual-procs.t15.txt" \
         "$EVID/stop/residual-unattributed.t0.txt" "$EVID/stop/residual-unattributed.t15.txt" \
         "$EVID/stop/ss.unix.established.t0.txt" "$EVID/stop/ss.unix.established.t15.txt" \
         "$EVID/stop/ss.official.established.t0.txt" "$EVID/stop/ss.official.established.t15.txt"; do
  [ -f "$p" ] || { echo "REFUSE: 采样文件缺失: $p" >&2; exit 1; }
done
diff -u "$EVID/stop/fd-holders.t0.txt" "$EVID/stop/fd-holders.t15.txt" > "$EVID/stop/fd-holders.t0-vs-t15.diff" \
  || { echo "REFUSE: fd 持有者在 0s 与 +15s 之间变化" >&2; exit 1; }
diff -u "$EVID/stop/residual-procs.t0.txt" "$EVID/stop/residual-procs.t15.txt" \
  > "$EVID/stop/residual-procs.t0-vs-t15.diff" \
  || { echo "REFUSE: 残余进程在 0s 与 +15s 之间变化" >&2; exit 1; }
for f in "$EVID/stop/fd-holders.t0.txt" "$EVID/stop/residual-procs.t0.txt"; do
  if [ -s "$f" ]; then echo "REFUSE: 仍有 fd 持有者/残余扩展进程（$f）" >&2; exit 1; fi
done
# R5/Rev-5：未归属到本部署 repo 的候选（容器内 uvicorn 等）在容器 exited 后宿主不应再有
for f in "$EVID/stop/residual-unattributed.t0.txt" "$EVID/stop/residual-unattributed.t15.txt"; do
  if [ -s "$f" ]; then echo "REFUSE: 存在未归属的扩展候选进程（容器内 uvicorn 残留？见 $f）" >&2; exit 1; fi
done
# R5/Rev-5：官方 trim_music 连接只记录、**不参与**扩展静止判定（避免真实窗口误拒）
for f in "$EVID/stop/ss.official.established.t0.txt" "$EVID/stop/ss.official.established.t15.txt"; do
  wc -l < "$f" > "$f.count" 2>/dev/null || echo 0 > "$f.count"
done
if [ -s "$EVID/stop/ss.unix.established.t0.txt" ] || [ -s "$EVID/stop/ss.unix.established.t15.txt" ]; then
  echo "REFUSE: 本应用 fnmusic-ext.sock 上仍存在 established 连接" >&2; exit 1
fi
systemctl show fnmusic-ext.service -p ActiveState,SubState,ExecMainStatus >> "$EVID/stop/unit.after-stop.txt"
# 3.6 记录官方应用是否仍在运行（不重启、不停止它；官方 socket established 连接数仅作上下文）
ss -xlpn | grep -E 'trim_music' > "$EVID/stop/ss.sockets.after-stop.txt" 2>&1 || true
# 3.7 最后一步才写「3.5 PASS」marker（R5/Rev-5：先清旧文件、再新鲜 inspect、全部通过后才落盘）
#     marker 绑定本窗口 + 本次容器实例（window id + container FinishedAt），防重入误用旧 marker。
rm -f "$EVID/stop/3.5.all-writers-static.PASS" "$EVID/stop/3.5.marker.sha256"
docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} finished={{.State.FinishedAt}}' \
  fnmusic-sources > "$EVID/stop/container.premarker.txt"
grep -q 'status=exited' "$EVID/stop/container.premarker.txt" \
  || { echo "REFUSE: marker 写入前容器不是 exited" >&2; exit 1; }
FIN=$(docker inspect -f '{{.State.FinishedAt}}' fnmusic-sources)
printf 'window=%s\ncontainer_finished_at=%s\nmarker_created_at=%s\n' \
  "$TS" "$FIN" "$(date -Iseconds)" > "$EVID/stop/3.5.all-writers-static.PASS"
sha256sum "$EVID/stop/3.5.all-writers-static.PASS" | tee "$EVID/stop/3.5.marker.sha256"
```

**「全部写者静止」断言（只在 3.5 全部通过并**最后**写出 marker 后才能写）**：在 T0（停容器后 ≈0 s）与 T15（+15 s）
两个采样点，`LIB`/`STG`/`REPO` 无 fd 持有者、无 `/tmp/fnmusic-manual-save-*`；`ss -x state established` 下
**本应用** `fnmusic-ext.sock` 无连接（官方 `trim_music.socket` 上的连接是官方应用的预期连接，**只记录、不参与**
判定，避免真实窗口误拒）；宿主无本部署 `$REPO` 归属的残留扩展进程，也无**未归属**的扩展候选（典型为容器内
`uvicorn app:app`，容器 `exited` 后不应在宿主出现）；`.env`、`sources-data`、`recommend_cache`、`logs` 的逐文件
哈希在两轮之间完全一致；容器 `exited`。**外部系统写入（journald/docker 日志/fnOS 日志采集）仍可能发生，
必须声明为预期副作用，不计入「扩展静止」**；也不得把本断言扩展为「设备无写入」或「历史无写入」。
marker 在**所有检查之后**才写入，写前 `rm -f` 旧文件并重新 `docker inspect` 确认仍 `exited`，因此不会复用
窗口中断遗留的旧 marker。`$EVID/stop/3.5.all-writers-static.PASS` 是 §5.5 开始最终备份的硬前置：marker
必须存在、`window` 与 `container_finished_at` 与本窗口/本容器实例匹配（§5.5-4.0 校验），否则 REFUSE。

**预期**：`stop_rc=0`；unit `inactive (dead)`；target 变回 official（`takeover status` 的 kind/进程为官方 `trim-music`）；
`/_ext/livez` 不再由代理响应（连接失败/非 200）；官方搜索探针返回官方响应（401/`INVALID TOKEN` 类）；
容器 `exited`，退出码记录；容器日志无异常终止；3.5 的「全部写者静止」断言通过，且 marker 绑定
`window=$TS` 与 `container_finished_at`（§5.5-4.0 会复核）。

**失败/回滚判据**：
- `stop_rc != 0` 或 unit `failed`：说明 `ExecStopPost` 恢复失败/超时 → **不要继续备份以外的动作**，
  先按 §6.5 R1 恢复官方直连并报告；**不得重复 stop/start 试探**。
- 官方 socket 不是 official 或官方探针失败：视为「用户音乐已受影响」→ 立即按 §6.5 R1 处置并向用户报告。
- 容器 stop 非 0 / 仍 running：先查 `docker ps`/`docker inspect`，不得 `docker rm -f` 生产容器。
- 3.5 任一采样失败（仍有 fd/进程/连接，或 `.env`/`sources-data` 仍变化）：**不得进入 §5.5**；
  查明来源（是否有未停的容器子进程/外部管理页/并发窗口），停在 R0 报告。

**证据**：`stop/*`（systemctl 输出、takeover status、socket stat、官方探针、docker stop/log、3.5 全部采样与 diff）。

### 5.5 Step 4：最终备份（停止后；独立目录 `BK_FINAL`，禁止覆盖 `BK_PRE`）

> **Rev-2 / F1 + Rev-3 / R1 + Rev-4 / R3**：`BK_PRE` 是 §4.3 在停服**之前**完成的独立备份，是 §0.1-4 的时间点证据；
> 它的 `MANIFEST.sha256`/`manifest.hash.txt`/冻结快照已在 §4.3 B12 生成。**本步只写 `BK_FINAL`；
> 停服后对 `BK_PRE` 只做只读校验（`sha256sum -c`、MANIFEST 哈希比对、freeze 快照 diff），
> 绝不重写它的任何文件**（R1：重写会让自描述失配且 mtime 晚于停服）。
> 本步沿用 §5.1/§5.4 的 `TS`/`EVID`（同一窗口、同一 shell 会话）；若分终端执行必须先恢复这两个变量，
> 否则 marker 的 `window=$TS` 绑定与证据目录都会错位。

```bash
# 4.0 前置：3.5 的「全部写者静止」marker 必须存在，且必须绑定本窗口与本次容器实例（R3/Rev-4）
[ -s "$EVID/stop/3.5.all-writers-static.PASS" ] \
  || { echo "REFUSE: 3.5 未通过（缺 marker），不得开始最终备份" >&2; exit 1; }
FIN_NOW=$(docker inspect -f '{{.State.FinishedAt}}' fnmusic-sources)
grep -qx "window=$TS" "$EVID/stop/3.5.all-writers-static.PASS" \
  || { echo "REFUSE: 3.5 marker 不属于本窗口（window 不匹配），疑似遗留旧 marker" >&2; exit 1; }
grep -qx "container_finished_at=$FIN_NOW" "$EVID/stop/3.5.all-writers-static.PASS" \
  || { echo "REFUSE: 3.5 marker 与本容器实例不匹配（FinishedAt=${FIN_NOW}）" >&2; exit 1; }
grep -qE '^status=exited( |$)' "$EVID/stop/container.after-stop.txt" \
  || { echo "REFUSE: 容器当前不是 exited，3.5 静止前提不成立" >&2; exit 1; }
# 4.1 防覆盖守卫：BK 必须从 BK_PRE 切到 BK_FINAL；任一条件不满足即停止
[ "$BK_PRE" != "$BK_FINAL" ] || { echo "REFUSE: BK_PRE==BK_FINAL 配置错误" >&2; exit 1; }
[ -f "$BK_PRE/MANIFEST.sha256" ] || { echo "REFUSE: BK_PRE 未完成（缺 MANIFEST），不得开始最终备份" >&2; exit 1; }
[ -s "$EVID/backup/pre/bkpre.freeze-pre-stop.txt" ] \
  || { echo "REFUSE: 缺 BK_PRE 停服前冻结快照，无法证明其未被写入" >&2; exit 1; }
BK=$BK_FINAL
[ "$BK" != "$BK_PRE" ] || { echo "REFUSE: 最终备份不得写入 BK_PRE" >&2; exit 1; }
mkdir -p "$BK"
printf 'role=final\nBK=%s\nBK_PRE=%s\n' "$BK" "$BK_PRE" > "$EVID/backup/final/dirs.txt"
# 4.2 记录停服后的状态（作为 final 备份的上下文，与 pre 形成对照）
systemctl show fnmusic-ext.service -p ActiveState,SubState,ExecMainStatus >> "$EVID/backup/final/unit.state.txt"
docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}}' fnmusic-sources >> "$EVID/backup/final/container.state.txt" 2>&1 || true
# 4.2b **BK_PRE 只读校验（R1；不写 BK_PRE 任何字节）**
( cd "$BK_PRE" && sha256sum -c MANIFEST.sha256 ) > "$EVID/backup/pre/verify-after-stop.txt" 2>&1 \
  || { echo "REFUSE: BK_PRE MANIFEST 停服后校验失败" >&2; exit 1; }
cur_manifest_hash=$(sha256sum "$BK_PRE/MANIFEST.sha256" | awk '{print $1}')
[ "$cur_manifest_hash" = "$(cat "$EVID/backup/pre/manifest.sha256-only.txt")" ] \
  || { echo "REFUSE: BK_PRE MANIFEST 哈希与停服前记录不一致" >&2; exit 1; }
find "$BK_PRE" -type f -printf '%p\t%s\t%T@\n' | sort > "$EVID/backup/pre/bkpre.freeze-after-stop.txt"
diff -u "$EVID/backup/pre/bkpre.freeze-pre-stop.txt" "$EVID/backup/pre/bkpre.freeze-after-stop.txt" \
  > "$EVID/backup/pre/bkpre.freeze.diff" \
  || { echo "REFUSE: BK_PRE 在停服后被写入（freeze 快照不一致，见 bkpre.freeze.diff）" >&2; exit 1; }
# 4.3 重跑 §4.3 的 **B1–B11 命令块**（$BK 此时已是 $BK_FINAL）
#     —— 严禁重跑 §4.3 开头的「变量块」（它会把 BK 重新绑回 BK_PRE），
#        也严禁重跑 B12（B12 只用于生成 BK_PRE 的 MANIFEST）。
#     B4/B5 的 LIB/STG 校验、B8 的包路径变量、B11 的 DB 判定同样适用。
# 4.4 两份备份的差异对照（必须逐项解释；不得用 final 覆盖 pre）
diff -u <(cd "$BK_PRE" && find . -type f ! -name MANIFEST.sha256 ! -name manifest.hash.txt -print | sort) \
        <(cd "$BK_FINAL" && find . -type f ! -name MANIFEST.sha256 ! -name manifest.hash.txt -print | sort) \
  > "$EVID/backup/pre-vs-final.filelist.diff" || true
# 4.5 **只为 BK_FINAL 生成** MANIFEST/manifest.hash.txt（R1：BK_PRE 这里绝不重写）
( cd "$BK_FINAL" && find . -type f ! -name MANIFEST.sha256 ! -name manifest.hash.txt -print0 \
    | sort -z | xargs -0 sha256sum > MANIFEST.sha256 )
sha256sum "$BK_FINAL/MANIFEST.sha256" | awk '{print $1}' > "$BK_FINAL/manifest.hash.txt"
cp -p "$BK_FINAL/MANIFEST.sha256" "$BK_FINAL/manifest.hash.txt" "$EVID/backup/final/"
sha256sum "$BK_FINAL/MANIFEST.sha256" | awk '{print $1}' > "$EVID/backup/final/manifest.sha256-only.txt"
( cd "$BK_FINAL" && sha256sum -c MANIFEST.sha256 ) > "$EVID/backup/final/selftest.txt" 2>&1 \
  || { echo "REFUSE: BK_FINAL 自描述校验失败" >&2; exit 1; }
# 4.6 再次确认 BK_PRE 未被写（本步最多只读；用最终 freeze 与停服前快照再 diff 一次）
find "$BK_PRE" -type f -printf '%p\t%s\t%T@\n' | sort | \
  diff -u "$EVID/backup/pre/bkpre.freeze-pre-stop.txt" - \
  > "$EVID/backup/pre/bkpre.freeze-final-check.diff" \
  || { echo "REFUSE: BK_PRE 在最终备份阶段被写入" >&2; exit 1; }
stat -c '%a %U:%G %n' "$BK_PRE" "$BK_FINAL" > "$EVID/backup/both.dirs.stat.txt"
```

**预期**：`3.5` marker 存在；`BK_PRE` 通过只读 `sha256sum -c`、MANIFEST 哈希与停服前记录一致、
freeze 快照 diff 为空（**证明停服后零写入**）；`BK_FINAL` 是唯一新生成的 MANIFEST/`manifest.hash.txt`；
两份的 filelist 差异只来自停服后的状态文件（如 `unit.show.txt` 从 active→inactive）与新增日志，逐项可解释；
`.env`、`data.tar.gz`、`install-tree.sha256` 等核心材料在 final 中与 pre 的差异必须能对应「停写/停服导致的
合法变化」。
**失败判据**：缺 3.5 marker；`BK_PRE` 校验失败/哈希不一致/freeze 有差异；`BK_FINAL` MANIFEST 生成或自检失败；
差异无法解释 → **不得安装**。恢复路径：若安装尚未开始，按「原状态启回 unit+容器」或 §6.5 R0 处置并请用户
决定，**不得**用重试备份掩盖失败。

### 5.6 Step 5：隔离恢复演练结论复核

- 使用 §4.5 的通过判据复核 `BK_PRE` 的关键项：归档可读、秘密项**流式**摘要一致、rehearsal 无秘密明文、
  **非秘密项内容级清单一致（R5）**、曲库副本（若有）一致、无生产写入；
- 复核 `BK_PRE` 的停服后只读校验证据：`sha256sum -c`、MANIFEST 哈希比对、`bkpre.freeze.diff` 为空（R1）；
- 对 `BK_FINAL` 用同一方法复核（新建 rehearsal 目录；同样排除 `.env`/`sources-data` 明文）；
- 写死「本次恢复演练覆盖/未覆盖」清单（与 §3 的候选范围一致），**不得**把未覆盖项写成通过；
  特别注明「秘密项仅流式校验、未做明文解包恢复」这一限制（§4.5 边界）；
- **失败 → 不安装**（回到 §6.5 R0/R1）。

### 5.7 Step 6：安装 / 升级 FPK

> 依据：检查单 §4.1（应用中心手动安装新 FPK 并升级/覆盖）、`cmd/upgrade_init`/`upgrade_callback` 行为。
> 具体 UI 操作由用户在 App Center 完成；执行者只负责准备、观察与验证。**降级不在本步范围（§6）。**

**准备**：新 FPK 的路径/SHA-256/版本；确认包内不含 `.env`/DB/Cookie/日志/probe（检查单 §3）；
确认官方音乐在运行（升级 `extend` 需要）；确认容器处于 stopped（让安装器负责重建/启动）。

**关键预期行为（来自脚本，非实测）**：

1. `upgrade_init`：`systemctl stop fnmusic-ext.service`（此时应已停）→（必要时）清理失效 unit/旧目录容器 →
   在 `${TRIM_PKGVAR:-/tmp}/upgrade-backup` 创建 `data.tar.gz`（白名单）；
2. App Center 替换 `target/repo`（应用目录 inode 可能变化；容器若仍在运行会挂到旧 inode——本步骤已停容器）；
3. `upgrade_callback`：解回白名单 → `install.sh --non-interactive --sources … --webui --extend --adopt`
   → 重建/复用 venv、**按构建指纹决定是否重建镜像**、`compose up` 启动容器、`extend.sh --force --adopt`
   渲染 unit → `enable --now`/`restart` → `takeover ready` → 验收（未登录 401 快速路径 + 音源搜索）→
   写 deployment registry；成功删除 `${TRIM_PKGVAR}/upgrade-backup`；
4. 失败：`fnmusic_cleanup_sources_container`（`docker rm -f fnmusic-sources`）+ 报错；App Center 回滚 target。

**观察命令草案**：

```bash
# 安装进行中/后（只读）
ls -la "${TRIM_PKGVAR:-/tmp}"/upgrade-backup 2>&1 | tee "$EVID/install/upgrade-backup.txt"
tail -n 200 /var/log/apps/fnmusic-ext-install.log | grep -viE 'cookie|token|secret|password' > "$EVID/install/install.log.tail.txt" 2>&1 || true
docker ps -a --format 'table {{.Names}}\t{{.Status}}' | grep fnmusic-sources >> "$EVID/install/docker.txt" 2>&1 || true
systemctl is-active fnmusic-ext.service; systemctl show fnmusic-ext.service -p ActiveState,SubState,MainPID,NRestarts
sha256sum /etc/systemd/system/fnmusic-ext.service "$REPO/VERSION" > "$EVID/install/post-install.hashes.txt"
```

**预期**：安装成功；`VERSION` = 新版本；unit 重新渲染（哈希变化）；`.env` 被白名单恢复（值可能被
`env_merge` 补齐新键——必须 diff 并只允许预期新增，秘密不打印）；`app.js` 被 FPK 原版覆盖（漂移丢失，
必须记录）；容器 running/healthy；`${TRIM_PKGVAR}/upgrade-backup` 在成功后**不存在**。
**失败判据**（任一即进入 §6.5）：
- 安装失败 / App Center 弹错 / 日志出现 unrecoverable 错误；
- `VERSION` 未变或与目标不符；
- 安装后服务起不来、容器不健康、镜像构建失败、网络/依赖失败；
- `.env` 丢失/内容异常（键消失、值变为非预期）或出现额外未解释变化；
- 曲库/暂存出现非预期文件变化（基线已知的除外）；
- 安装日志出现对白名单之外路径的删除/覆盖迹象。

### 5.8 Step 7：启动与健康检查（检查单 §4 + 观察项）

```bash
systemctl start fnmusic-ext.service; echo "start_rc=$?" | tee "$EVID/post/start.txt"
systemctl is-active fnmusic-ext.service; systemctl show fnmusic-ext.service -p ActiveState,SubState,MainPID,NRestarts
/usr/bin/python3 "$REPO/proxy/takeover.py" ready --timeout 65; echo "ready_rc=$?" | tee -a "$EVID/post/start.txt"
cat "$REPO/VERSION"
curl -sS -m 5 -w '\nhttp=%{http_code}\n' http://127.0.0.1:8774/healthz | tee "$EVID/post/healthz.txt"
curl -sS -m 5 -o /dev/null -w 'direct_playlist_http=%{http_code}\n' \
  http://127.0.0.1:8774/api/manual-save/playlist/playlists | tee -a "$EVID/post/endpoints.txt"   # 期望 501
curl -sS -m 5 -o /dev/null -w 'direct_status_http=%{http_code}\n' http://127.0.0.1:8774/api/manual-save/status >> "$EVID/post/endpoints.txt"  # 期望 403（未认证）
stat -c 'socket mode=%a owner=%U:%G' "$APP/fnmusic-ext.sock" | tee "$EVID/post/socket.stat.txt"     # 期望 600 root:root
# 六个整歌单端点（3 只读 GET + 3 写 POST；见 §5.9 与 webui_gateway.py:2475-2482）
# 关闭态（总开关 false）经网关 socket 请求应全部 403 disabled（请求方法不匹配会落到 501，属预期）
smoke_hdr=(-H 'x-trim-userid: 1000' -H 'x-trim-isadmin: true' -H 'x-trim-username: kurumi' -H 'x-fnmusic-save: 1')
for probe in "GET playlist/playlists" "GET playlist/snapshot" "GET playlist/job" \
             "POST playlist/jobs" "POST playlist/advance" "POST playlist/cancel"; do
  m=${probe%% *}; p=${probe#* }
  code=$(curl -s -o /dev/null -w '%{http_code}' -X "$m" --unix-socket "$APP/fnmusic-ext.sock" \
    "${smoke_hdr[@]}" "http://127.0.0.1/app/fnmusic-ext/api/manual-save/$p")
  printf '%s %s -> %s\n' "$m" "$p" "$code" >> "$EVID/post/endpoints.six.txt"
done
cat "$EVID/post/endpoints.six.txt"
# 单曲总开关状态端点（只读 GET；带 smoke 头，见下方 §0.2 标注）
curl -s --unix-socket "$APP/fnmusic-ext.sock" "${smoke_hdr[@]}" \
  http://127.0.0.1/app/fnmusic-ext/api/manual-save/status | tee "$EVID/post/status.json.txt"
docker inspect -f 'status={{.State.Status}} health={{.State.Health.Status}} image={{.Image}}' fnmusic-sources | tee "$EVID/post/container.txt"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep fnmusic-sources >> "$EVID/post/container.txt"
# 官方音乐侧（只读）
curl -sS -m 8 -o /dev/null -w 'official_probe_http=%{http_code}\n' --unix-socket /var/run/trim_music.socket \
  'http://localhost/music/api/v1/search/track?keyword=test' | tee -a "$EVID/post/official.txt"
# 漂移核对：app.js 是否被覆盖
sha256sum "$REPO/webui-service/static/app.js" | tee -a "$EVID/post/drift.txt"   # 期望 = FPK 原版 e12e6a5e…
# 白名单数据/unit/容器/账本路径
ls -la "$REPO/sources-data" "$REPO/online_favorites" "$REPO/play_history" 2>/dev/null > "$EVID/post/data.txt"
# 若本项目配置了 JOBS_DB：确认文件不存在/mtime 不变（关闭态不应创建账本）
#   JOBS_DB='<配置路径>'; test -e "$JOBS_DB" && echo "JOBS_DB_EXISTS（异常）" || echo "jobs_db_absent"
# 若未配置 JOBS_DB：以「整歌单端点 403 disabled」+「无新增 .db 文件」交叉确认，不得凭键缺失就断言关闭
```

> **§0.2 标注（Rev-2 / F12）**：以上经网关 socket 的请求手工带 `x-trim-*` / `x-fnmusic-save` 头，
> 按检查单 §0.2 原文，它们「仅是指定头部下的接口烟雾检查，**不是**身份来源或管理员授权证明」。
> 本 runbook **只**用其做状态/拒绝码的交叉确认，不据此推断 §0.2 PASS，也不调用任何写操作；
> 上述 `POST` 探测只用于**关闭态下的拒绝码验证**（预期 403/501 且无副作用），执行前必须确认
> 两个写开关的生效值均为 false，且不得在开关打开时运行该循环。

**通过条件**：unit `active`；`ready_rc=0`；`/healthz` 200 且 `ok:true`；socket 600 root:root 且监听进程为
gateway；直接 8774 的 playlist 端点 501；未认证 status 403；六个整歌单端点经网关均为 403
（`disabled`；若仅整歌单关则为 `playlist_disabled`）且无写副作用；smoke 头 status 返回 `enabled:false`；
容器 running/healthy；官方音乐探针正常；`.env` 两开关为 false 且生效值 false；jobs DB 路径不存在或未被创建；
曲库/暂存与停写前一致。
**失败/回滚判据**：`start_rc != 0`、`ready_rc != 0`、healthz 非 200/`ok:false`、socket/端点码不符、
容器不健康、官方音乐受影响、开关仍为 true、出现 jobs DB 或曲库变化 → 进入 §6.5。

### 5.9 Step 8：默认关闭与烟雾验证

- 复核 `FNMUSIC_MANUAL_SAVE_ENABLED=false` 与 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false` 的
  **实际生效值**（status 响应 + 目标版本端点拒绝码），记录来源（`.env` 优先/回退进程环境）与 `unknown` 情形；
- 复核 `FNMUSIC_PLAYLIST_MANIFEST_READONLY` 不存在/为 false（若存在则记录，不得留 true）；
- 目标版本：确认关闭态下**六个**整歌单端点全部 403（`webui_gateway.py:2475-2482` 的
  `_MANUAL_SAVE_PLAYLIST_ROUTES`：**3 个只读 GET** —— `playlist/playlists`、`playlist/snapshot`、
  `playlist/job`；**3 个写 POST** —— `playlist/jobs`、`playlist/advance`、`playlist/cancel`）。
  总开关关 → 6 个均 `disabled`；仅整歌单关 → 6 个均 `playlist_disabled`；请求方法不匹配不在 6 个 handler
  内，会落到转发路径返回 501（同样属于预期，不应误判为端点缺失）。
  且**没有账本文件被创建**（`JOBS_DB` 路径不存在/mtime 不变）；不触发任何保存；
- 曲库/暂存/数据白名单与 §5.5 最终备份对比（应无变化）；
- 记录 `journalctl`/安装日志/容器日志（脱敏）作为「部署后观察」。

**失败判据**：任一开关生效值非 false、端点可写/非预期码、账本出现、曲库变化 → §6.5。

### 5.10 Step 9：回滚（触发、目标、命令、失败处置）

**触发判据（任一成立即触发，先 R0 冻结再选梯子）**：

| # | 触发条件 | 观测方式 |
| --- | --- | --- |
| T1 | 安装/升级失败或 App Center 弹错 | 安装日志、App Center 提示 |
| T2 | 服务无法启动：`systemctl start` 非 0（unit `TimeoutStartSec=75`、`ExecStartPost ready --timeout 65` 均失败） | `systemctl`、`takeover ready` |
| T3 | `/_ext/healthz` 持续非 ok（≥3 次、≥60 s） | healthz 采样 |
| T4 | 官方 socket 未复位/官方音乐不可用 | `takeover status`、官方探针 |
| T5 | 容器持续不健康（≥3 个 healthcheck 周期） | `docker inspect .State.Health` |
| T6 | 开关生效值非 false、账本被意外创建、曲库/暂存被意外修改 | status/端点/备份对比 |
| T7 | 版本/包/哈希与预期不符 | `VERSION`、`install-tree.sha256`、unit 哈希 |
| T8 | 出现无法解释的进程/连接/写入者 | `/proc` 采样、ss |

**回滚阶梯（从最保守到最激进）**：

- **R0 冻结（默认第一步，不是回滚）**：保持停写（两开关 false）、不改包、不重启；保留现场；
  立即向用户/复核者报告并等待决策。适用：状态不明或影响无法判断时。
- **R1 恢复官方直连（恢复用户可用性，保留应用与数据）**：`bash "$REPO/restore.sh"`。
  其默认语义 = `restore-plan` 预检 → `remember` → `disable --now` → `takeover restore` → 删 unit →
  清理容器（v1.x 三容器 + `fnmusic-sources`）→ 清理在线试听滚动缓存（保留 `.env` 与全部用户数据）。
  **代价**：扩展不再运行（应用仍安装）；之后要恢复扩展需重新安装/配置。
- **R2 重装 2.10.2 FPK（回到上一版本）**：**前提是已证明 fnOS 支持该降级/覆盖安装**；
  当前材料**不能**证明（§6）。不得作为默认动作。
- **R3 安装 2.8.2-rollback**：**禁止**（重建版、非历史 2.8.2、功能更少、来源未识别）。
- **R4 卸载后重装**：**禁止作为试探**（§6.4 红线）；仅在用户明确授权且 R1/R2 都不适用时，按
  `uninstall_init` 的归档/保留语义单独设计流程。

**R1 执行草案（含失败处置）**：

```bash
# 前置：takeover restore-plan 必须成功；否则不停止任何东西，直接报告
/usr/bin/python3 "$REPO/proxy/takeover.py" restore-plan > "$EVID/rollback/restore-plan.json" 2>&1; echo "rc=$?"
bash "$REPO/restore.sh" 2>&1 | tee "$EVID/rollback/restore.log"; echo "restore_rc=${PIPESTATUS[0]}"
# 验证：官方直连可用；unit 已不存在；容器已清理
systemctl list-unit-files | grep fnmusic-ext || echo "unit_absent"
curl -sS -m 8 -o /dev/null -w 'official_probe_http=%{http_code}\n' --unix-socket /var/run/trim_music.socket \
  'http://localhost/music/api/v1/search/track?keyword=test'
docker ps -a --format '{{.Names}}' | grep -x fnmusic-sources || echo "container_absent"
stat -c '%F %a %U:%G' /var/run/trim_music.socket
```

**R1 失败处置**：`restore.sh` 非 0 → 若它已 `disable --now` 但 `takeover restore` 失败，
会「保留未知 socket，不宣称成功」并退出；此时**不要**重跑 stop/start、不要手动 mv socket；
记录现场，请求用户按官方音乐应用恢复（重启官方音乐）后重试一次；仍失败 → 报告 BLOCKED。
若卸载/删除 unit 已发生，则扩展已不可用，按用户决策走 R2/R4。

**R2 风险（若用户决定）**：2.10.2 FPK 与安装树只有 1 个漂移文件；重装会覆盖 `app.js` 补丁；
`.env` 与数据白名单由 `install_callback`/`upgrade` 逻辑恢复（取决于安装类型），**jobs DB（未来）
不在白名单**，必须先从 §4.6 快照恢复；2.10.2 不含整歌单代码（功能回退是事实，不能描述成"功能回滚完成"）。

### 5.11 Step 10：窗口收尾

| 动作 | 要求 |
| --- | --- |
| 恢复开关默认关闭 | 两开关 false、`READONLY` false/不设置；实际生效值复核；不因验收而打开 |
| 状态归档 | `EVID` 全树 `MANIFEST.sha256`；两份备份各有自己的 `MANIFEST.sha256`/`manifest.hash.txt`（不得合并）；把三者的 sha256 都写入 `99-report.md` |
| 门禁状态 | 明确写回：§0.1/§0.2 是否变化（本 runbook 不改）；未取得的证据逐项列出 |
| 用户报告 | 结构同本文件 §8：已做/未做、通过/失败、风险、待决策项 |
| 现场清理 | 只清理本窗口创建、明确记录的临时文件；**禁止通配/递归删除**；生产数据不清理 |
| 复核 | 独立复核者检查证据与偏差；不通过则不得宣称本窗口成功 |

### 5.12 证据留存格式

**目录结构**：

```text
<VOL>/fnmusic-ext-ops-<TS>/
  00-window.md               # 授权来源、人员、时间(JST/CST)、目标版本、回滚目标、决策 ID
  00-window-time.txt
  pre/                       # Step 0–1：baseline、unit/容器/socket/ps/ss、restore-plan、journal 抓取、平台
  stop/                      # Step 2–3：停写前后 .env 哈希/键级校验/diff、线程/fd/tmp/连接采样、systemctl stop、socket 状态、docker stop、3.5 两轮静止采样、3.5.all-writers-static.PASS marker
  backup/pre/                # Step 2 之前：BK_PRE 的 MANIFEST.sha256、manifest.hash.txt、manifest.sha256-only.txt、bkpre.freeze-pre-stop.txt、selftest.txt、停服后只读校验（verify-after-stop / freeze.diff）、pre-vs-final 对照
  backup/final/              # Step 4：BK_FINAL 的 MANIFEST.sha256、manifest.hash.txt、manifest.sha256-only.txt、selftest.txt（与 pre 分开）
  rehearsal/                 # Step 5：恢复演练输出（秘密项仅流式摘要，无明文落盘）
  install/                   # Step 6：升级临时目录列表、安装日志尾部、docker/systemctl 状态、post-install hashes
  post/                      # Step 7–8：start/ready、healthz、六个端点码、socket stat、status、container、official、drift
  rollback/                  # Step 9（若触发）：restore-plan、restore.log、验证输出
  99-report.md               # 结论、偏差、未取得证据、待决策
  MANIFEST.sha256            # 除自身外所有文件的哈希（EVID 级；与两份 BK 的 MANIFEST 分开）
```

**每条记录字段**：

| 字段 | 要求 |
| --- | --- |
| step | 对应本文件步骤号（如 5.4） |
| time_jst / time_cst | 精确到秒，两时区都记 |
| operator / reviewer | 执行者、复核者（不写账号密码） |
| command | 实际命令（脱敏后的文本） |
| exit_code | 必须记录 |
| expected / observed | 预期与实测分离写 |
| artifacts | 输出文件相对路径 + sha256 |
| decision | go / no-go / rollback |
| notes | 偏差、解释、未验证项 |

**生成方式（草案）**：

```bash
cd "$EVID" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256
sha256sum MANIFEST.sha256 | tee backup/manifest.hash.txt
```

### 5.13 中断处理与时间预算

| 情况 | 处理 |
| --- | --- |
| 窗口内断电/断网/SSH 断 | 先恢复连接→只读盘点状态（§5.1）；不得盲目重跑安装/回滚；若安装中断，按 App Center 日志判断处于哪一阶段（§5.7 行为顺序） |
| App Center 卡住/无响应 | 记录时间与日志；不关闭页面、不重复点击安装；长时间无进展则按 T1 处理（先 R0） |
| 需要回滚但回滚目标 unknown | 只允许 R0/R1；报告并请求用户决策（禁止 R3/R4） |
| 时间预算 | 停写到恢复写入应尽量短（建议 ≤ 30–60 min，**由用户决定** §7 D1）；备份/演练时间单独预算，不占用停写窗口 |
| 证据不足 | 宁可停在 R0，也不"补写"记录 |

---

## 6. 回滚边界

### 6.1 已核实的包事实

| 包/对象 | 事实 | 来源 |
| --- | --- | --- |
| `fnmusic-ext-2.10.2.fpk` | 28,072,710 B，sha256 `77bb7566…029a0c`；manifest version 2.10.2、checksum = md5(app.tgz)；114 文件；**与当前安装树 113 一致、1 处安装后漂移（app.js）** | `DEVICE_INVENTORY_FOLLOWUP_20261107.md` §3.2；`DEPLOYED_CODE_ANOMALIES_20261107.md` §1.4 |
| 2.10.2 的能力 | **不含** P1/P2/P4/P5/P6 整歌单代码（快照 producer、适配层、网关 6 端点、管理页入口） | `DEVICE_INVENTORY_20261107.md` §2.3 |
| `fnmusic-ext-2.8.2-rollback.fpk` | 27,170,364 B，sha256 `1379e2e9…2fdd85`；**由来源未识别的 `/tmp/build/282` 混合树重建**（23 个文件与 HEAD 一致、34 个文件两代皆非、生命周期脚本沿用历史）；≠ 用户历史安装过的 2.8.2 | followup §3.3；anomalies §3.3 |
| 历史 `fnmusic-ext-2.8.2.fpk` | 27,173,490 B，sha256 `a6aa0662…4f9df670`；与升级前整树备份中的 `webui_gateway.py`（`b87e39fd…`）吻合 → 历史实际版本是下载包 | followup §3.3；anomalies §3.3 |
| 2.8.2 的能力 | 不具备手动保存功能（连单曲手动保存也没有） | 检查单 §6「能力边界」 |
| 回滚目标 / 降级支持 | **unknown**：未证明 fnOS 应用中心支持降级；未验证任一 2.8.2 包在 2.10.2 数据/配置上安全 | followup §9-8；anomalies §3.5 |

### 6.2 回滚的能力边界（不得夸大）

- 回到 2.10.2 = 回到「没有整歌单保存」的状态；回到 2.8.2 = 连单曲手动保存也没有。
  **「应用恢复运行」不等于「功能回滚完成」**（检查单 §6 原文）。
- 回滚 `.env` 后旧版本可能忽略新键，但新键可能仍留在 `.env` 中；再升级前必须重新只读检查并把写开关
  设为 false（检查单 §6「配置回滚/再升级」）。
- 已发布媒体**不自动删除**；暂存内容**不自动清空**；回滚不清理测试产物（P7 手册 §7）。
- jobs DB（未来）：**不删除、不覆盖、不用旧快照覆盖当前 DB**；先停全部写者，再用 §4.6 另存新副本并验证；
  不确定 schema 兼容性时不要让旧版本打开它（检查单 §6「账本回滚」）。

### 6.3 受支持降级仍 unknown

| 问题 | 现状 |
| --- | --- |
| fnOS 应用中心是否支持对同一 app 降级/覆盖旧版本？ | **unknown**（无证据；未执行） |
| 若支持，目标应是哪个 2.8.2 包？ | **unknown**（rollback 重建版 ≠ 历史包；两者都自洽） |
| 2.8.2 能否读/容忍新版本 `.env` 与数据？ | **unknown**（新键会被忽略；但数据目录/格式、`sources-data` 兼容性未验证） |
| 回滚后能否安全再升级？ | **unknown**（需重新走 §5 全流程） |

### 6.4 红线（不得以任何理由试探）

1. **不得**为了"看看能不能回退"而卸载/重装或安装旧 FPK（检查单 §6 明令禁止）。
2. **不得**删除、截断、覆盖、`cp` 覆盖当前 jobs DB（未来）或其 `-wal/-shm/-journal`。
3. **不得**通配/递归删除曲库、暂存、repo 数据或测试产物；只允许按任务 `positions[]`/文件清单逐项处理，
   且需单独授权。
4. **不得**用"服务当前 active/健康端点/一次成功安装/静态审查"代替停/启/回滚验证（检查单 §0.1-3 原文）。
5. **不得**在生产上做"试探停机"：任何停/启只能作为 §5 窗口内的一次授权动作，失败即进入 R0/R1。
6. **不得**通过修改生产配置、临时关开关或重启来"修正"盘点结果（检查单 §0.2/§1.1 原文）。
7. **不得**把 §3 的隔离副本结论写成生产结论；也不得把 `scripts/` 下 BLOCKED 的 restore fixture
   （`docs/RESTORE_FIXTURE.md`）当作恢复验证证据。

### 6.5 允许的动作阶梯与前置条件

| 阶梯 | 动作 | 前置 | 能恢复什么 | 风险 | 是否允许默认使用 |
| --- | --- | --- | --- | --- | --- |
| R0 | 冻结（停写、不改包、不重启、保留现场） | 无 | 不恢复，但避免扩大影响 | 用户音乐仍不可用（若已坏） | 是（第一动作） |
| R1 | `restore.sh` 恢复官方直连 | `restore-plan` 成功 + 授权 | 官方音乐可用；扩展停止 | 删 unit/容器；扩展功能消失；`.env`/数据保留 | 是（用户可用性优先时） |
| R2 | 重装 2.10.2 FPK | **已证明** fnOS 支持 + 用户确认目标 + 新 FPK 哈希核对 + 备份 | 回到 2.10.2 代码（无整歌单） | 覆盖 app.js 漂移；数据/配置需按白名单恢复；DB（未来）需手动 | **仅当证明支持** |
| R3 | 安装 2.8.2-rollback | — | — | 来源未识别、能力更少 | **禁止** |
| R4 | 卸载后重装 | 用户明确授权 + 单独流程 | 可能"干净" | 白名单外数据丢失、unit/容器/日志删除、无降级证据 | **禁止作为试探** |

---

## 7. 待用户决定 / 授权项

> **编号空间（Rev-2 / F5）**：本表是**本 runbook 的局部编号 D1–D12**；`docs/OPEN_DECISIONS_20261107.md`
> §D 已把它们登记为 **`RUNBOOK-R1..R12`**（同一页的 `DESIGN-D1..D11` 属于**扫描期一致性**，`SCHEMA-Q*` 属于
> 验收证据 schema，`U1..U8` 是本轮新增用户决策）。引用时务必带文档前缀，**不得**把 `R1` 误读为 `DESIGN-D1`。
>
> **两次独立决策/窗口**：`DESIGN-D1` 是「扫描期是否停写」——推荐 (b) 短窗口 Grade B，**不重启服务**；
> `RUNBOOK-R1` 是「部署窗口的停写」「停服务+容器」意义上的停写。两者虽然都叫「停写窗口」，但**对象、影响面、
> 授权与证据要求不同**，`OPEN_DECISIONS` 只把它们记为 `DESIGN-D1 ≈ RUNBOOK-R1 + R11`（近似，不是同一决定）。
> 执行任何一方前必须分别取得该窗口的授权与证据，不能用一个窗口的决定替代另一个。
> 另：`RUNBOOK-R6` 与 `U4`（secrets 权限模型）相关、`R7` 与 `U1` 相关、`R8 = U2`、`R9 = U3`、
> `R12 ≈ U1`、`R4 + R10 ≈ U8`；权威编号与映射以 `OPEN_DECISIONS_20261107.md` §D 为准。

| 本文局部 | OPEN_DECISIONS | 决策/授权 | 选项 | 建议 | 未决定时的状态 |
| --- | --- | --- | --- | --- | --- |
| D1 | RUNBOOK-R1 | 停写窗口范围与时长 | (a) 只停手动保存写开关不重启；(b) 短窗口停服务+容器；(c) 整库/整家目录停写 | (b)：本 runbook 的默认（备份/演练需停容器） | §0.1 保持 BLOCKED |
| D2 | RUNBOOK-R2（= DESIGN-D2） | 数据布局（与扫描一致性设计相关） | T-A 曲库独立子卷 / T-B 用户子卷快照 + 子树 / T-C reflink 副本 | 本轮**不改布局**；若要改，先按 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` D1–D8 决定 | 扫描器维持 unknown |
| D3 | RUNBOOK-R3（≈ DESIGN-D9） | 曲库是否全量拷贝（scratch 卷/空间） | 只清单+哈希 / 全量 tar / 复制到另一卷 | 若有空间：全量（可恢复）；否则清单+哈希并接受"不可恢复内容" | 备份不具备曲库内容级恢复能力 |
| D4 | RUNBOOK-R4（≈ U8 一半） | 是否允许隔离副本「起服务」 | 允许（候选 A/B，root/端口/`/tmp`）/ 仅静态分析 | 允许 A+B，但须遵守 §3.5 纪律与独立复核 | §0.1-3 无任何验证 |
| D5 | RUNBOOK-R5（新） | 是否提供第二 fnOS 环境（候选 C） | 提供（主机/VM）/ 不提供 | 提供：这是 §0.1-3 真正 PASS 的现实路径 | §0.1-3 保持 BLOCKED |
| D6 | RUNBOOK-R6（与 U4 相关） | 备份目录位置、保留期，以及「对 NAS 管理员保密」是否是硬要求 | 接受 `/vol1` 0700 + trimacl 现实 / 需要额外手段 | 记录并接受 trimacl 现状（管理员可读），或改用加密归档（需另行设计） | 备份保密性 unknown |
| D7 | RUNBOOK-R7（与 U1 相关） | `FNMUSIC_MANUAL_SAVE_JOBS_DB` 是否现在配置、放在哪 | repo 外私有持久路径（推荐）/ 暂不配置 / 其他 | 暂不配置（保持默认关闭与无 DB）；若配置，必须在 repo 外并纳入 §4.6 | 目标版本整歌单无法启用（预期） |
| D8 | RUNBOOK-R8（= U2） | 是否接受 `app.js` 本地补丁在升级/回滚后丢失 | 接受并记录 / 要求纳入新 FPK / 先取出补丁 | 要求纳入下一版 FPK（或书面记录为接受偏差） | 升级后手动保存响应缓存行为可能变化 |
| D9 | RUNBOOK-R9（= U3） | 回滚目标确认 | 2.10.2（需证明降级支持）/ 只做 R1 恢复官方直连 / 其他 | 只确认 R0+R1；R2 待证据；R3 禁止 | 回滚门保持 BLOCKED |
| D10 | RUNBOOK-R10（≈ U8 一半） | 是否授权 G0-P5 的 writer-drain / rollback 现场演练 | 授权 / 不授权 | 若要做 §0.1-3，必须授权并单独评审 | 演练门保持 BLOCKED |
| D11 | RUNBOOK-R11 | 部署窗口时间与总时长 | 用户指定 | 停写 ≤30–60 min（备/演练在窗口前完成） | 不进入窗口 |
| D12 | RUNBOOK-R12（≈ U1） | 是否允许在窗口内修改生产 `.env`（仅两个开关 + 未来 DB 路径） | 允许（本会话已有授权范围）/ 需单独确认 | 按检查单 §1 的既有授权范围执行，不逐项重问 | 无法停写关闭 → 不能部署 |

---

## 8. 仍为 BLOCKED 的项与补齐条件（映射检查单 §0.1 每一条）

| §0.1 条目 | 现状 | 本文补充了什么 | 仍缺的证据 | 可判定的补齐条件 |
| --- | --- | --- | --- | --- |
| **第 1 条**：记录本次设备实际 fnOS/扩展包版本及包哈希、unit 与 drop-in、容器/worker、配置解析来源、jobs DB/曲库/暂存路径和挂载；证据与本次窗口绑定 | 部分 observed（`DEVICE_INVENTORY` + `_FOLLOWUP`：平台/版本/unit/容器/库/暂存/配置来源）；**包哈希只到候选**（FPK ↔ 安装树有 1 处漂移）；**jobs DB 路径 unknown** | 给出窗口内重采集清单、漂移文件单列要求、`TRIM_PKGVAR` 推断与核实方法 | ① 本窗口内重新采集全部 `observed` 字段；② 明确记录「FPK 哈希 + 安装树清单 + 漂移文件」三元组；③ jobs DB 路径由配置确定或记录为「未配置」；④ 独立复核 | 以上 ①–④ 全部有原始输出+哈希+时间，且复核无异议 |
| **第 2 条**：只读盘点在途任务/worker/DB 写者；先安全完成或按已验证流程取消并核实终态；禁止强杀；确认恢复/重派与停/装/启是否触发后台任务 | 采样时刻无在途（`DEVICE_INVENTORY` §8），但总开关 true、无持久队列、journal 易失 → **不可证**；目标版本 job 生命周期未部署 | §5.3 给出「无 DB 时的无可观察写者」采样方法与断言边界；列明启动会触发后台任务（推荐/喜欢/日志/歌词/LX 预热） | ① 窗口内两轮稳定采样（fd/线程/连接/哈希）；② 目标版本部署后 observation：无自动重派（A8/A16）、无 DB 连接残留；③ 明确外部写者（journald/docker）为预期副作用 | ①+② 有原始证据；③ 在报告中显式声明；任何未解释写入者 → 不 PASS |
| **第 3 条**：对照真实 unit/容器监督器/fnOS 生命周期形成停/启/健康/失败恢复步骤；在与本机版本/包/配置/数据一致**隔离副本**上实际验证；不能用 active/健康端点/静态审查/一次成功安装代替 | **未满足**：无停/启验证、无隔离副本、无回滚恢复演练 | §2 给出生命周期语义（含 `KillMode=mixed` 强杀风险）、§3 给出 3 个隔离候选与硬约束、§5 给出步骤草案 | ① 候选 A+B 的隔离实验（进程级+容器级）；② 候选 C（或第二 fnOS）上的真实 systemd/FPK 停/启/回滚演练；③ 「不重复派发」观察 | ② 是必要条件；仅 A+B 不足以 PASS。②完成并有独立复核 → 该条可评估 PASS |
| **第 4 条**：**在任何生产停机之前**，对 `.env`、jobs DB（SQLite backup API 一致快照）及可能受影响的曲库/暂存建立独立备份；校验完整性并在隔离副本验证恢复；不得以「停服后再备份」替代 | **未满足**：`DEPLOY_GATE_READONLY_EVIDENCE` 明确记为「未做」（门禁禁止）；当前无 jobs DB | §4 给出无 DB 的完整备份范围/命令/校验/演练方案与失败判据；§4.6 给出未来 DB 的一致快照方案 | ① 停服前独立备份执行证据（哈希/清单/时间）；② 隔离恢复演练通过；③ 曲库内容级备份（若 D3 选全量）；④ 未来 DB 配置后追加 §4.6 证据 | ①+②+（③ 或用户书面接受清单级）+ ④（仅配置 DB 时）全部具备，且复核通过 |
| **第 5 条**：明确回滚包、系统是否支持降级、包/配置/DB 兼容性和可执行恢复方案；在隔离副本验证到服务健康/数据可读/开关关闭/无重复任务；无法验证则**不得**用卸载/重装/旧 FPK 试探 | **未满足**：回滚目标 unknown；降级支持 unknown；无隔离验证；`2.8.2-rollback` 为重建版 | §6 给出包事实、能力边界、R0–R4 阶梯与红线；§5.10 给出触发判据与 R1 草案 | ① 用户确认回滚目标（D9）；② fnOS 降级支持的真实证据（App Center 文档或隔离环境实测）；③ 隔离副本上的回滚演练（服务健康/数据可读/开关关闭/无重复任务）；④ 任何使用 2.10.2 FPK 回滚的前提证据 | ①+②+③ 全部具备；或缺 ②/③ 时明确只允许 R0/R1，回滚门保持 BLOCKED |
| **第 6 条**：记录证据快照标识、执行时间、命令/结果摘要与独立复核结论；确认动作在会话授权范围内（授权不替代运行证据） | **未满足**：无窗口执行证据；本文仅模板 | §5.12 证据目录与字段模板；§5.0 授权/复核前置 | ① 授权记录（来源/边界）；② 证据目录 + MANIFEST；③ 独立复核报告；④ 偏差处置记录 | ①–④ 齐备；缺少任一 → 不得称门禁通过 |

**附加说明（不改变上表结论）**：

- §0.1 与 §0.2 **互相独立**：本文完成 §0.1 相关准备，不代表 §0.2（身份头来源、socket 实际拒绝矩阵、
  8774 直连拒绝、独立审查）有任何进展；`docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md` 仍记录
  「`trim_http_cgi` 带会话时是否剥离/覆盖客户端 `X-Trim-*`」为唯一剩余关键未知。
- P7 真实验收仍 BLOCKED（部署版本无整歌单代码；jobs DB 路径 unknown；未获授权真实音频全链路）。
- G0 的 writer-drain/rollback 演练门（G0-P5）仍 BLOCKED；本文不替代其证据要求。
- 本文所述「隔离副本」若被执行，同样受检查单 §0.1 的「生产停机」禁令与授权约束；**不允许在生产上
  试探停机**。

---

## 9. 附录

### 附录 A：引用文档与章节索引

| 文档 | 本文引用内容 |
| --- | --- |
| `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` | §0.1（六条门禁）、§0.2、§1.1、§1.2、§2、§3、§4、§5、§6、§7 |
| `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` | A1–A16（尤其 A8/A10/A11/A12/A13/A16）、§7 |
| `docs/DEVICE_INVENTORY_20261107.md` | §1–§7、§8、§9、§10、§11、§12、附录 A/B |
| `docs/DEVICE_INVENTORY_FOLLOWUP_20261107.md` | §0.1、§1–§9、§10、§11、附录 A |
| `docs/DEPLOYED_CODE_ANOMALIES_20261107.md` | §1（app.js 漂移全文与影响）、§2（无 DB 行为与写序）、§3（回滚包分析）、§4/§5 |
| `docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md` | §2（入口链路）、§4（网关信任头）、§6/§7/§9（剩余未知与更正） |
| `docs/VOL1_PERMISSION_MODEL_FINDING_20261107.md` | §2（trimacl 实测）、§3（解释边界）、§4（对门禁影响） |
| `docs/NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md` | 判定 PASS-with-limits、F1–F4、§4.2 边界；生产迁移/部署仍 BLOCKED |
| `docs/OFFLINE_FAILURE_TESTS_REVIEW_20261107.md` | 判定 PASS-with-limits、R1/R5 已解决、生产停写/备份/恢复/回滚仍 BLOCKED |
| `docs/G0_MAINTENANCE_PROPOSAL.md` | 有界写入者模型、G0-P5、BLOCKED 结论 |
| `docs/MAINTENANCE_STARTUP.md` | 维护启动合同、九类副作用抑制、`production_ready=false` |
| `docs/RESTORE_FIXTURE.md` | restore fixture 在打开归档前 BLOCKED（不得当恢复证据） |
| `docs/R2_ISOLATION_PROBE.md` | socket DAC 实验的范围与限制 |
| `docs/ISOLATED_LEDGER_MIGRATION.md` | 一致快照必须由外部可信进程在停写后取得；不得复制运行中 DB |
| `docs/OFFLINE_SAVE_JOBS.md` | §59/§63：schema v2、v1 不自动迁移、旧库迁移 BLOCKED |
| `docs/MANUAL_SAVE_PLAYLIST_JOB.md` | §378：v1 ledger 部署 BLOCKED |
| `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` | DESIGN-D1 停写窗口、DESIGN-D2 数据布局、T-A/T-B/T-C、Grade A/B 概念（仅作决策参考，不改门禁） |
| `docs/OPEN_DECISIONS_20261107.md` | §D 编号空间与交叉映射：本文 D1–D12 = `RUNBOOK-R1..R12`；与 `DESIGN-D1..D11`、`U1..U8` 的关系（Rev-2 / F5） |
| `docs/LIFECYCLE_RUNBOOK_REVIEW_20261107.md` | Rev-2 的修订依据：F1–F12 发现与「执行前最小修复清单」（PASS-with-limits） |
| `docs/LIFECYCLE_RUNBOOK_REV2_REVIEW_20261107.md` | **Rev-3 的修订依据**：Rev-2 限定复核（PASS-with-limits），R1–R6 残留与修复要求 |
| `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md` | 验收证据 schema（当前 Rev-3）；本文仅在附录 E 绑定其哈希，不采用其字段（避免跨文档版本漂移） |
| `docs/ACCEPTANCE_SCHEMA_REV3_REVIEW_20261107.md` | schema Rev-3 限定复审（附录 E 哈希绑定；本文不依赖其结论） |
| `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` | trimacl/RO 绕过根因与 M1–M4 缓解（隔离副本与 RO 语义边界参考） |
| `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` | RO 位不强制写（`/vol1`），用于说明隔离结论不能外推 |
| `docs/BTRFS_GENERATION_SEMANTICS_20261107.md` | generation/fsync 语义（扫描与时间点证据的边界） |
| `E:\fnmusic\PROJECT_HANDOFF_20261107.md` | 门禁状态、下一接手动作、并行会话与文件保护 |
| 仓库代码 | `install.sh`、`extend.sh`、`restore.sh`、`fnmusic-ext.service`、`proxy/{run_proxy.sh,takeover.py,webui_gateway.py,app.py,install_common.sh,manual_save_jobs.py,manual_save_playlist_jobs.py,manual_save_route.py}`、`packaging/fpk/cmd/*`、`container/{entrypoint.sh,env_flag.sh}`、`docker-compose.yml` |

### 附录 B：本文引用的关键设备事实（窗口内必须重新采集）

```text
fnOS/TRIM 1.2.0903；kernel 6.18.18.c1168-trim；Debian 12；Python 3.11.2；Docker 28.5.2
安装根 /vol1/@appcenter/fnmusic-ext；VERSION 2.10.2 sha256 06674b96...
unit /etc/systemd/system/fnmusic-ext.service 600 root:root 788B sha256 f6b2a6d0...；enabled+active；无 drop-in
容器 fnmusic-sources：image sha256:16d5e8ef...；build-fingerprint c50b9cc2...；healthy；127.0.0.1:{8768,8770,8772,8774}->{8001..8004}
socket /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock 600 root:root；/var/run/trim_music.socket 666 root:root
.env repo/.env 600 kurumi:Administrators 2663B sha256 ccc4c872...；ENABLED=true；PLAYLIST/JOBS_DB/READONLY absent
曲库 /vol1/1000/fnmusic-test-20261031 750 kurumi:Users 4 文件 379,825,952 B device=60
暂存 /vol1/1000/fnmusic-manual-save-staging 700 root:root 0 文件 device=60
jobs DB：受检范围不存在；配置路径 unknown；唯一 SQLite = sources-data/cache/netease-musicbox/nemcache.sqlite（0 行）
FPK：2.10.2 sha256 77bb7566...；2.8.2-rollback sha256 1379e2e9...；历史 2.8.2 sha256 a6aa0662...；pre-upgrade tgz sha256 bb8b5bf5...
日志：/run/log/journal 易失、仅当前 boot；journalctl -u fnmusic-ext = 0 条；app log mtime 2026-11-01 16:44
```

### 附录 C：本文「未执行」清单

1. 未连接 / 未访问设备（无 SSH、无 docker、无 systemctl、无 curl 到设备）。
2. 未停/启/重启任何服务、容器、worker。
3. 未修改 `.env`、unit、配置、代码或数据。
4. 未安装/升级/卸载/回滚任何 FPK。
5. 未执行任何备份/恢复/演练命令（§4 全为草案）。
6. 未打开任何 jobs DB 或 SQLite 文件。
7. 未调用任何写接口（包括手动保存、整歌单、管理页写操作）。
8. 未创建任何设备侧文件；本轮唯一写入是本文件。

### 附录 D：变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-07（初稿） | 依据检查单 §0.1/§0.2/§1.1/§1.2/§4/§5/§6、两份设备盘点、部署异常分析、独立复审、G0 文档与仓库代码编写；全部为计划，未执行。 |
| 2026-11-07（Rev-2） | 按 `docs/LIFECYCLE_RUNBOOK_REVIEW_20261107.md` 修复 F1–F12：拆分 `BK_PRE`/`BK_FINAL` 与防覆盖守卫（F1）；§5.3 缩小为「关开关+在途盘点」并把静止断言移到 §5.4-3.5（F2）；rehearsal 秘密项流式校验不落盘（F3）；整歌单端点改为 6 个（F4）；§7 增加 `RUNBOOK-R1..R12` 映射并区分 `DESIGN-D1`（F5）；备份表/命令一一对应、路径变量化、去 `\|\| true`（F6）；`.env` 原子替换+flock+diff+恢复（F7）；候选 A 目录/manifest（F8）；启动失败措辞（F9）；行号校正（F10）；停服顺序理由（F11）；烟雾头 §0.2 标注（F12）。新增附录 E。Rev-2 版本 = 1,684 行 / 151,576 B / `76867809…`，stamp `dd39bb5b…`。 |
| 2026-11-07（Rev-3） | 按 `docs/LIFECYCLE_RUNBOOK_REV2_REVIEW_20261107.md`（整文件 `2ca80471…`，内部 stamp `6890dfc2…`）修复 R1–R6：<br>**R1** §5.5 不再重写 `BK_PRE` 的 MANIFEST/manifest.hash.txt：B12 在停服前生成并冻结（`bkpre.freeze-pre-stop.txt`、`manifest.sha256-only.txt`、selftest）；停服后只做只读 `sha256sum -c`、MANIFEST 哈希比对、freeze diff，只有 `BK_FINAL` 生成自描述；B12 生成时排除自身两个自描述文件，保证可重复校验。<br>**R2** `.env` 编辑：Python 侧 `os.fchmod/fchown` 保留原 mode 与**属主**（不再 kurumi→root）+ fsync 目录；校验改为键级结构化判定（允许 0 行幂等、允许两键 4 行变化、`KEY = value` 空格归一化、目标键必须 false、其它键不得变化），并断言 mode/owner 与写前一致；`restore_env` 统一失败恢复。<br>**R3** §5.4-3.5 改为 T0(≈0 s)/T15(+15 s) 两轮采样，覆盖 `.env`/`sources-data`/`recommend_cache`/`logs`，`ss -x state established` 过滤 LISTEN，断言容器 `exited`，并写出 `3.5.all-writers-static.PASS` marker；§5.5 以 marker 为硬前置。<br>**R4** B5 命令块列改为 `B4–B5`；表外补 B12 包装步骤说明；manifest.hash.txt 位置统一（BK 内仅存哈希值 + EVID 副本），§4.4 与命令一致。<br>**R5** B3 新增非秘密项内容级清单（`data.nonsecret.sha256`），§4.5 用逐文件 sha256 比对（空集为 N/A）。<br>**R6** B1/B11 的 `grep -c \|\| echo 0` 改为「吞退出码 + 只取首行」，修掉 `0\n0` 风险。<br>附录 E 补绑 Rev-2 复审、schema Rev-3、schema Rev-3 复审三份哈希；自校验戳升级为 `REV3-HASH-STAMP`。 |
| 2026-11-07（Rev-4） | 按 `docs/LIFECYCLE_RUNBOOK_REV3_REVIEW_20261107.md`（整文件 `50a00296…`，内部 stamp `8f4fe2ac…`）修复两处代码级缺陷与次要项：<br>**R2/F-R1** §5.3 编辑器把 `os.fchmod/os.fchown` 的第一参从**路径**改为 **fd**（`fh.fileno()`），放进 `with os.fdopen(fd, "wb")` 内、且在最终 `os.fsync(fd)` **之前**（内容+元数据一起提交）；平台无 `os.fchown` 时 fail-closed。本地合成用例验证：路径形式抛 `TypeError`、fd 形式被调用且参数为 int、调用顺序 fchmod→fchown→fsync→replace→dir-fsync。<br>**R3/F-R2** §5.4-3.5 统一 W3 采样/比对命名为 `w3.<tag>.{env,sources-data,cache-logs}.sha256`，并对全部采样文件加**存在性断言**（缺失即 REFUSE 并打印具体路径）；本地合成用例验证 `.env` 与 `sources-data` 两种 f 下均能生成并被同一路径比对到。<br>**F-R3** residual-procs 改为按本部署 `$REPO` 过滤 + 保留全机 ps 快照；marker 写入 `window`/`container_finished_at`/`marker_created_at`，§5.5-4.0 复核 marker 与本窗口/本容器实例匹配（防旧 marker 复用）。<br>**F-R4** 元数据 fsync 顺序已按「先 fchmod/fchown、后 fsync」修正。<br>**F-R5** B3 与 §4.5 的非秘密解包显式检查 `tar` rc；R5 的 N/A 仅在 B3 计数确为 0 时允许。<br>**F-R6** §5.2 第 7 条改为「B12 MANIFEST + manifest.hash + freeze + selftest」，与 §4.3/§4.4/§5.5 一致。<br>附录 E 补绑 Rev-3 复审哈希；自校验戳升级为 `REV4-HASH-STAMP`。 |
| 2026-11-07（Rev-5） | 按 `docs/LIFECYCLE_RUNBOOK_REV4_REVIEW_20261107.md`（整文件 `7ace09c1…`，内部 stamp `b894c30e…`；含 NAS 真机实跑）修复 3 处真机缺陷 + 4 项次要项：<br>**F-R1（阻断）** §5.3 编辑器的 `print(f"… eol={'CRLF' if eol == b'\r\n' else 'LF'} …")` 在目标 **Python 3.11.2** 上 SyntaxError（PEP 701 仅 3.12+）→ 改为 f-string 外预计算 `eol_name = "CRLF" if eol == b"\r\n" else "LF"`，再用 `%` 格式化输出；NAS 真机验证原样 heredoc 编译/执行通过。<br>**F-R2（阻断）** §5.5-4.0 `grep -qx 'status=exited'` 与真实 `status=exited exit=0` 不匹配 → 改为 `grep -qE '^status=exited( \|$)'`（真机样例命中，running 样例仍拒绝）。<br>**F-R3** §5.4-3.5 的 `ss -x state established` 只统计本应用 `fnmusic-ext.sock`；官方 `trim_music(\.socket\|_upstream\.socket)` 连接另存 `ss.official.established.*` **只记录不判定**，并注明理由（官方连接是预期，非扩展未静止）。<br>**F-R4** marker 改为最后一步写入：先 `rm -f` 旧文件 → 新鲜 `docker inspect` 到 `container.premarker.txt` 并确认 `exited` → 才写 `window/container_finished_at/marker_created_at`（防旧 marker 复用）。<br>**F-R5** residual-procs 先取全量候选再拆分：`residual-procs.*`（含 `$REPO`）与 `residual-unattributed.*`（不含 repo，典型为容器内 uvicorn）二者任一非空即 REFUSE，覆盖「按 `$REPO` 过滤漏检」。<br>**F-R6** `.env` 修改前/后 `getfacl` 取证；`os.replace` 使用新 inode、**不复制 ACL/xattr**（`/vol1` trimacl）明确记为已知限制，不要求实现 ACL 复制。<br>**F-R7** §3.2 的 `<TS>` 占位符块加注：含 `<TS>` 时 `bash -n`/直接复制执行会报语法错属**预期**，必须先用窗口变量替换；真机 `bash -n` 原始 rc=2、替换 `<TS>` 后 rc=0。<br>附录 E 补绑 Rev-4 复审哈希；自校验戳升级为 `REV5-HASH-STAMP`。 |

### 附录 E：自校验戳与被引证据哈希绑定（Rev-5）

**自校验约定**：文末 `REV5-HASH-STAMP` 行记录 `sha256(该行之前的全部字节, UTF-8)`；删除该行后重算即得。
文件**整文件** SHA-256（含 stamp 行）在 Rev-5 交付消息中报告；本项目不便把「整文件哈希」写进文件自身
（写入会改变哈希）。历史版本：Rev-4 = 1,887 行 / 171,830 B / `74155b9e…13a4`（stamp `f212e9de…3f982`，
见 Rev-4 交付与 Rev-4 复审）；Rev-3 = 1,834 行 / 165,249 B / `6c4ace21…`；Rev-2 = 1,684 行 / 151,576 B / `76867809…`。

**被引证据文档哈希（Rev-5 时点，当前磁盘实测；窗口内必须重算）**：

| 文档 | 字节 | 行数 | SHA-256（整文件） | 备注 |
| --- | --- | --- | --- | --- |
| `docs/LIFECYCLE_RUNBOOK_REVIEW_20261107.md` | 29,461 | 202 | `17547232be6c4234ac17d50e2c65fe1c8af22417ffc1c07d4351d59078ac2e8e` | Rev-2 修订依据（F1–F12）；其文内 `REPORT-HASH-STAMP` 记录 `sha256(该 stamp 行之前的字节)` = `74f9171fed0463c5a4f7da2e6d373ae5d580415b0e47eb3fd4496dafd8c31f62`（消息中简称 `74f9171f…`）。**两个值语义不同**：整文件含 stamp 行，故整文件哈希 ≠ stamp 值 |
| `docs/LIFECYCLE_RUNBOOK_REV2_REVIEW_20261107.md` | 17,985 | 142 | `2ca804713aa17772397adad59718aa744f7b4ae912043a4d830020b0feb03d26` | **Rev-3 修订依据**（R1–R6，PASS-with-limits）；消息中简称 `2ca80471…`。其文内 `REPORT-HASH-STAMP` = `6890dfc28616ad80e09ca21bab1547170359c5e5b436ca75dec1692a9c829386`（该 stamp 行之前字节的哈希；与整文件哈希语义不同） |
| `docs/LIFECYCLE_RUNBOOK_REV3_REVIEW_20261107.md` | 17,494 | 127 | `50a0029614c42161b7fbe25dfc766f688e6ad4aaab5b69443934c06efd723bf3` | Rev-4 修订依据（F-R1/F-R2 两处代码级缺陷 + F-R3–F-R6 次要项，PASS-with-limits）；消息中简称 `50a00296…`。其文内 `REPORT-HASH-STAMP` = `8f4fe2aca1ac49eb7fa149bb1a7ca8a1dd144065bace1cacfc8b11dcaa329e6a`（行前字节哈希，语义不同于整文件哈希） |
| `docs/LIFECYCLE_RUNBOOK_REV4_REVIEW_20261107.md` | 17,873 | 139 | `7ace09c110d407d553b69f4dfb26940c88017bae30b3eb44e35871b0d24c3661` | **Rev-5 修订依据**（NAS 真机实跑：Py3.11 f-string、grep 格式、ss 过滤 + F-R4–F-R7）；消息中简称 `7ace09c1…`。其文内 `REPORT-HASH-STAMP` = `b894c30ea5c23620e369d1cf0e14c76f6c8bd4c7026b0a70a8260b8f30866355`（行前字节哈希） |
| `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` | 114,285 | 749 | `6bef7134511985ee825f7668b735fc755f61b3aaf487ae34099bc6476f71d1ce` | 设计提案（DESIGN-D1..D11/T-A..T-C）；不改门禁 |
| `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` | 8,520 | 90 | `4c01327466001e4cd89b3245bac25e60c9b92cde1d512672eb2399bf4171f842` | 消息中简称「RO `4c013274…`」；trimacl/RO 绕过根因与 M1–M4 |
| `docs/BTRFS_GENERATION_SEMANTICS_20261107.md` | 13,780 | 147 | `0c99d3ac9aae9dd958300fa0fbc2ccf9615951c91e00c58bd7bd61e4b20c07a3` | generation/fsync 语义 |
| `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` | 5,985 | 66 | `61e0391234a63b51813bb11b27c355344d3dd5f43e4b5555c2ed67c47bc93914` | RO 位不强制写（**注意**：与「RO_ROOTCAUSE」是两个文档，勿混） |
| `docs/OPEN_DECISIONS_20261107.md` | 7,781 | 70 | `7573e743f2de5a1bc94809c6047ba7439edd491023b1c17e09cb0c74bfd7b82b` | §D 含 `RUNBOOK-R1..R12` 映射（Rev-2 / F5 依据；Rev-3 复核未再变） |
| `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md` | 119,555 | 1,187 | `dec0aabb8eefa2fa8354dc44b9a71f070727d1e4df8529f2eb13057ea86b9fd1` | 验收证据 schema **Rev-3**（消息中简称 `dec0aabb…`）；本文只绑定哈希，不采用其字段 |
| `docs/ACCEPTANCE_SCHEMA_REV3_REVIEW_20261107.md` | 10,953 | 109 | `ee8b043734a5a233a45e148507a3bebb38f6db69e942f99020b79f17e8255fb4` | schema Rev-3 限定复审（消息中简称 `ee8b0437…`）；本文不依赖其结论 |
| `docs/DEVICE_INVENTORY_20261107.md` | 47,515 | 417 | `d46342be8e1b4a7f99998af7afa8f390c8d454293296e3d3fd54a0cea760fed6` | 首份设备盘点（复审文首表同值） |
| `docs/DEVICE_INVENTORY_FOLLOWUP_20261107.md` | 38,690 | 367 | `abcae1f7f7ff059c950281d8d402ff9cec33e5f64aa9d867112a0b1c09f23859` | 续盘（复审文首表同值） |
| `docs/DEPLOYED_CODE_ANOMALIES_20261107.md` | 29,328 | 297 | `95d391a4c8accf35ba6791894e1f75e5e8fffe7231f6e818c3db68dc438e5734` | 部署异常分析（复审文首表同值） |
| `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` | 22,996 | 240 | `6e1648037a4dedd9bcefe43ab742b817c42bcde3f9318441eaa5ca19666a2548` | 检查单（§0.1/§0.2 来源） |
| `docs/MANUAL_SAVE_PLAYLIST_JOB.md` | 118,962 | 1,284 | `4b5610ce8bc5e458bfbe9917d29a99764b5e96761340ed9fc8aa441abce0b0cf` | 合同 |
| `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` | 16,111 | 177 | `3e0ea759546adf3f64913c09f53d2ec960ac0b7b2690b12555e6e2a71e7fde02` | P7 运行手册 |
| `docs/VOL1_PERMISSION_MODEL_FINDING_20261107.md` | 6,229 | 66 | `8fe03f78215d17703d58b85f410a4d475fd0df0dd037a554ffc3b75d0850d356` | trimacl 权限模型（管理员可读 0600） |
| `docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md` | 10,057 | 125 | `2c4fc8bbd5c259d6cc97a56be5d4fb0f89d5d12f5ca3cf77a7908c864eba335f` | §0.2 只读证据（本文件不改变 §0.2） |

> 说明（不改变任何门禁）：① 上表哈希在 **Rev-5 编写时点**对当前磁盘重算；若并行会话更新了其中任一文档，
> 执行窗口必须重新计算并在证据目录记录新值，旧值只用于「本修订基于哪一版」的追溯；② 上表不把任何
> 文档变成「已验收/已执行」；③ 本文件自身不含 secrets；④ 本文件不是证据，不能替代 §0.1-6 要求的
> 窗口证据与独立复核；⑤ 同一文档的「整文件哈希」与「文内 REPORT-HASH-STAMP（行前字节哈希）」是两个
> 不同值，引用时必须写明是哪一种。

<!-- REV5-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 11c1bb1a690032e7740c6664ddb804c35f1f3d1fde930faa7fa664b2010e75b8 -->
