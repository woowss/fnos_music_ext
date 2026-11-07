# 部署门只读设备证据 · 2026-11-07 窗口

> 本文件是 [`MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md`](MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md) §0.1 生命周期门与 §0.2 身份/路径门的**只读**窗口证据。
> **本文件不改变门禁结论：§0.1 仍为 BLOCKED，§0.2 仍为 BLOCKED。**
> 本次采集没有执行任何停机/启动/重启、配置编辑、安装/升级/回滚、`/vol1` 写入、删除、DB 打开或写请求；未采集到的项目在正文标注 **NOT OBSERVED / 未获得**，不以历史记录或推测填充。

## 0. 元信息与只读声明

| 项 | 值 |
| --- | --- |
| 采集者 | `device-inventory`（Agent Teams 队友，只读盘点） |
| 采集窗口（UTC） | 2026-11-06T17:08:15Z – 2026-11-06T17:16:28Z |
| 采集窗口（JST，UTC+9） | 2026-11-07T02:08:15+09:00 – 2026-11-07T02:16:28+09:00 |
| NAS 本地时钟/时区 | CST（UTC+8），观察时显示 2026-11-07 01:08–01:16 |
| 设备 | hostname `kotomi`（192.168.0.106，SSH 别名 `kotomi-root`，端口 721，root key-only） |
| fnOS 版本 | `1.2.0903`（`/usr/trim/etc/version`）；系统基座 Debian 12 (bookworm)；内核 `6.18.18.c1168-trim #1168` |
| 仓库/分支/HEAD | `E:\fnmusic-ext-dev`，`codex/fnmusic-prelaunch-20261025`，`cf843a4`（仅用于对照，未提交/未切换/未清理） |
| 采集方式 | 本地写 `.sh`（LF）→ `scp` 到 NAS `/tmp/deploy-inv-20261107/`（仅此目录）→ `ssh kotomi-root 'bash /tmp/deploy-inv-20261107/<script>.sh'`；长输出按关键行截断 |
| 设备侧写入 | 仅 `/tmp/deploy-inv-20261107/`（脚本与临时响应文件）；**未写 `/vol1`、未改任何配置文件/unit/容器** |
| 授权边界 | task-1：只读盘点；`systemctl show/status`、`systemctl cat/is-active`、`docker ps/inspect/top`、`ss`、`ps`、`stat/readlink/findmnt/du/df/find`、`sha256sum`、只读 grep、`curl` **GET only**。以下动作一律**未执行**：`systemctl start/stop/restart/reload`、`docker restart/stop/exec`、安装/升级/降级、删除、`/vol1` 写入、SQLite 打开（含只读 URI）、POST 写路由、登录/认证 |
| 秘密处理 | 未输出 `.env` 全文、Cookie、token、密钥；`.env`/进程环境/unit 环境只解析 6 个受审键（4 个门禁键 + `TARGET`/`STAGING`），其余键只计数量 |

### 结论摘要（先行）

1. **§0.1 仍 BLOCKED；§0.2 仍 BLOCKED。** 本次只读盘点补齐了设备/版本/哈希/unit/容器/路径/监听/探测的部分证据，但不满足任何一条"已验证停/启/回滚 + 备份 + 隔离副本验证 + 独立复核"的通过条件。
2. **关键异常：仓库 `.env` 中 `FNMUSIC_MANUAL_SAVE_ENABLED` 存在且布尔分类为 `TRUE`；运行时 GET 状态端点交叉确认为 `enabled: true`。** §0.2 关闭门要求两个保存开关实际生效值**均为 false**，该条件当前不满足。按只读授权，本次**未修改** `.env`、**未**通过重启或临时关开关"修正"盘点结果。
3. 部署版本仍为 **2.10.2**；已安装包**不含**本轮 P1/P2/P4/P5/P6 整歌单保存代码（`manual_save_playlist_jobs.py` 缺失、网关无 playlist 端点、运行时 playlist 路径返回 501 直连占位）。
4. `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`、`FNMUSIC_MANUAL_SAVE_JOBS_DB`、`FNMUSIC_PLAYLIST_MANIFEST_READONLY` 在 `.env`/unit/进程/容器环境中**均未配置**；jobs DB 实际路径**不可确定**（部署加载器无默认路径），因此无法对该 DB、父目录及 sidecar 做 stat，也**不能**据此宣称"DB 不存在"。
5. 未观察到在途手动保存/整单任务进程、未完成暂存文件或 DB 写入者；但**在途任务状态本身未观测**（无可确定的 DB，且禁止打开 DB）。
6. §0.2 身份头来源**未被证明**：`/app/` 由 nginx → `/run/trim_http_cgi.socket` → fnOS `trim_http_cgi`（root，二进制含 `X-Trim-Userid`/`X-Trim-Isadmin`/`X-Trim-Username` 字符串）承载，但未在真实已认证桌面会话中验证注入/剥离行为；本次经 root 直连 socket 的伪造声明只做 GET 烟雾检查，不作为授权边界证明。

---

## 1. §0.1-1 设备与服务 unit（只读）

观察时间：UTC 2026-11-06T17:08:15Z / JST 2026-11-07T02:08:15+09:00；17:09:39Z / 02:09:39+09:00；17:12:15Z / 02:12:15+09:00。

```bash
# 设备
hostname; uname -a; cat /etc/os-release; cat /usr/trim/etc/version
# unit（只读）
systemctl show fnmusic-ext.service -p FragmentPath -p DropInPaths -p ExecStart \
  -p ExecStartPost -p ExecStop -p ActiveState -p SubState -p MainPID \
  -p KillMode -p CanReload -p Restart -p TimeoutStopSec -p User -p EnvironmentFiles
systemctl cat fnmusic-ext.service            # unit 文件按受审键脱敏后阅读
systemctl is-enabled fnmusic-ext.service; systemctl is-active fnmusic-ext.service
```

原始输出关键行：

```
Linux kotomi 6.18.18.c1168-trim #1168 SMP PREEMPT_DYNAMIC Tue Oct 13 11:20:25 UTC 2026 x86_64 GNU/Linux
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
/usr/trim/etc/version = 1.2.0903

FragmentPath=/etc/systemd/system/fnmusic-ext.service
DropInPaths=
ExecStart=/bin/bash /vol1/@appcenter/fnmusic-ext/repo/proxy/run_proxy.sh
ExecStartPost=/usr/bin/python3 .../proxy/takeover.py ready --timeout 65
ExecStartPost=/usr/bin/python3 .../proxy/webui_gateway.py /vol1/@appcenter/fnmusic-ext/repo
ExecStopPost=/usr/bin/python3 .../proxy/takeover.py restore
ActiveState=active  SubState=running  MainPID=1870197
KillMode=mixed  CanReload=no  Restart=no  TimeoutStopSec=20  User=root
（EnvironmentFiles 属性无输出；unit 文件内无 EnvironmentFile= 行）
unit 文件内 Environment= 仅 PYTHONUNBUFFERED（受审 4 键：无）
is-enabled=enabled  is-active=active
ActiveEnterTimestamp=Sun 2026-11-01 17:18:30 CST（已运行 5 天）  NRestarts=0
```

补充（供生命周期门复核，不构成停/启验证）：

- 单元模板 `repo/fnmusic-ext.service`：`Type=simple`、`TimeoutStartSec=75`、`TimeoutStopSec=20`、`KillMode=mixed`、`Restart=no`、`UMask=0077`、`WantedBy=multi-user.target`；`ExecStart` 经 `proxy/run_proxy.sh` 执行 `takeover.py run --base <repo>`（脚本 `set -euo pipefail`）。
- 无 drop-in 文件（`DropInPaths` 为空）。
- **未执行**任何 `systemctl start/stop/restart/reload`、未触碰 socket takeover 的 ready/restore 路径。

---

## 2. §0.1-2 已安装包版本、关键文件 SHA-256 与功能面

观察时间：UTC 2026-11-06T17:09:39Z / JST 02:09:39+09:00。

```bash
cat /vol1/@appcenter/fnmusic-ext/repo/VERSION
sha256sum <repo>/proxy/{run_proxy.sh,webui_gateway.py,takeover.py,app.py,manual_save_*.py}
grep -c 'MANUAL_SAVE_PLAYLIST_ENABLED' <repo>/proxy/webui_gateway.py
grep -c '/api/manual-save/playlist'     <repo>/proxy/webui_gateway.py
```

| 项 | 观察值 |
| --- | --- |
| 已安装包版本 | **2.10.2**（`repo/VERSION`，8 字节，mtime 2026-11-01 15:24:54 CST） |
| 应用目录 | `/vol1/@appcenter/fnmusic-ext`（mode 775 root:root） |
| `repo/proxy/run_proxy.sh` | `938fa39588742c91cd102b1a65a98f61bbf7e7cb5654d465573835269eb52cae`（343 B，775 root:root，mtime 2026-11-01 16:44:44 CST） |
| `repo/proxy/webui_gateway.py` | `48b51e931817f79b2ffbdca55ba56f7abfb6b3472429fcbece1ddd65ffebcbc9`（60858 B） |
| `repo/proxy/takeover.py` | `82ca849b68e165580491e6e8d70824eba5bfb06d4ed26ad7ae873253f8b81ab5`（47994 B） |
| `repo/proxy/app.py` | `a8671f88ebbfc93d0f87988421350ea01d7cdaa1315bc05d029596d9a58ef97d`（491123 B） |
| `repo/proxy/manual_save_jobs.py` | `7a9a3d3343630e357bbe574bf4df6badbde1240fe39c3605ce6bb2dce30a01f5`（38380 B） |
| `repo/proxy/manual_save_route.py` | `f60460b49114c0c49412a7830c924bd30dd4226f96148b1876f25cfa37502c6e`（89720 B） |
| `repo/proxy/manual_save_playlist_jobs.py` | **ABSENT（不存在）** |
| `repo/proxy/manual_save_playlist.py` | `688d5a8fb787959f22ea2b35e753f582c8cbfea90d1dc47fd31ecc5f6a589702`（21389 B；文件头自述为"pure orchestration primitive"，**不是**本轮 P1/P2/P4/P5/P6 的 playlist jobs 模块） |
| `repo/proxy/manual_save_core.py` | `53783b5874e7f87ed3e40741d2164dd799710df95cfc7d78b28beb66c612ba6c`（8591 B） |

功能面核对（部署版本 vs 本轮代码）：

```
grep -c MANUAL_SAVE_PLAYLIST_ENABLED  repo/proxy/webui_gateway.py  -> 0
grep -c /api/manual-save/playlist     repo/proxy/webui_gateway.py  -> 0
grep -c manual_save_playlist          repo/proxy/manual_save_route.py -> 0
grep -c manual_save_playlist          repo/proxy/takeover.py          -> 0
运行时 GET /app/fnmusic-ext/api/manual-save/playlist/playlists
  -> HTTP 501 {"detail":"直连模式不支持手动保存，请在飞牛桌面内打开管理台"}（转发到 webui-service 的直连占位端点）
```

**结论：部署版本 2.10.2 不含整歌单保存（P1/P2/P4/P5/P6）代码；含旧版单曲手动保存模块。** 与 checklist §0 的记录一致；任何整歌单部署都必须先安装新 FPK（当前门禁下**未执行**）。

---

## 3. §0.1-3 容器 / worker 状态（只读）

观察时间：UTC 2026-11-06T17:09:39Z / JST 02:09:39+09:00。

```bash
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'
docker inspect fnmusic-sources --format '{{json .}}' | python3 -c '<仅提取 Name/Image/State/Health/Mounts/PortBindings>'
docker top fnmusic-sources
```

关键结果：

| 项 | 观察值 |
| --- | --- |
| 容器 | `fnmusic-sources`，镜像 `fnmusic-sources:latest`，`Up 5 days (healthy)` |
| 镜像 ID | `sha256:16d5e8ef536363130ede5ff3df7e4b45890f3a30eba9598dd15b61c6c02580a3` |
| 创建 / 启动 | 2026-11-01T07:51:44Z / 2026-11-01T08:43:17Z；RestartCount=0；健康 `healthy`，FailingStreak=0 |
| 重启策略 | `unless-stopped`（max_retry=0）；privileged=false；network=`repo_default` |
| 端口（宿主） | `127.0.0.1:8768→8001`、`127.0.0.1:8770→8002`、`127.0.0.1:8772→8003`、`127.0.0.1:8774→8004`（均只发布到回环） |
| 挂载 | bind `/vol1/@appcenter/fnmusic-ext/repo/sources-data` → `/data` **RW**；bind `/vol1/@appcenter/fnmusic-ext/repo` → `/repo` **RW** |
| 健康检查 | `CMD /usr/local/bin/healthcheck.sh`，interval 30s / timeout 10s / start_period 20s / retries 3 |
| 容器进程 | `entrypoint.sh` → `supervisord` → `uvicorn app:app --host 0.0.0.0 --port 8004`（webui，pid 1809118）、`uvicorn ... --port 8002`（musicbox，pid 1836506）；当前只启用这两个 uvicorn |

容器进程环境（只读 `/proc/<pid>/environ` 解析）：4 个受审键在容器进程/`Config.Env` 中**均未配置**（`reviewed_env=none`；`Config.Env` 14 个其他键，名称/值未输出）。

> 记录但不判定：`repo` 以 **RW** 挂进容器。若未来 jobs DB 位于 `repo` 内，升级替换/容器写入会使其暴露于包替换与并发写风险（checklist §1.1/§6 已提示），当前该键未配置。

---

## 4. §0.1-4 / §1.1 配置解析：4 个受审键的存在与生效值

观察时间：UTC 2026-11-06T17:09:39Z、17:13:37Z、17:14:58Z（JST 02:09:39、02:13:37、02:14:58）。

```bash
# 只解析受审键：存在性 + 布尔分类 / POSIX 绝对路径；不输出其他键值
python3 - <<'PY'  # 读 <repo>/.env，按 manual_save_route.read_env_file 规则（最后赋值生效、去引号）
...
PY
# unit/drop-in 环境只筛受审键
systemctl show fnmusic-ext.service -p Environment --value   # 解析后仅打印受审键名
# 运行进程/容器环境同样只筛受审键（/proc/<pid>/environ）
```

### 4.1 `repo/.env`（唯一观察到的受审键来源）

- 文件：`/vol1/@appcenter/fnmusic-ext/repo/.env`，mode **600**，owner `kurumi:Administrators`，size 2663，mtime **2026-11-01 17:01:47 CST**（早于本次窗口；本次未改）。
- 精确赋值计数：`FNMUSIC_MANUAL_SAVE_ENABLED=1`、`TARGET=1`、`STAGING=1`、其余受审键 0。
- 其他键数量 49（**名称与值均未输出**）。

| 键 | 存在性 | 值分类/值 | 生效来源与交叉确认 |
| --- | --- | --- | --- |
| `FNMUSIC_MANUAL_SAVE_ENABLED` | **存在（1 处赋值）** | **`TRUE`**（布尔归一化 true 类） | 单曲路由读取 repo `.env`；**运行时 GET status 交叉确认 `enabled: true`**（见 §8.4-A）；进程 `proxy uvicorn` 环境亦为 `TRUE`（一致，无冲突） |
| `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED` | **缺失** | —（按受审加载器规则缺键=默认关；但部署版本无该开关代码） | `.env`/unit/进程/容器环境均无；部署版本无 playlist 端点（501） |
| `FNMUSIC_MANUAL_SAVE_JOBS_DB` | **缺失** | — | `.env`/unit/进程/容器环境均无；部署版本无 playlist jobs 加载器；单曲 ledger 模块要求调用方传入绝对路径、**无默认值** |
| `FNMUSIC_PLAYLIST_MANIFEST_READONLY` | **缺失** | —（未设置=关，但部署版本代码不引用该键） | `.env` 无；容器进程环境无；`grep -r` 部署 repo 无引用（P3 producer 未部署） |
| `FNMUSIC_MANUAL_SAVE_TARGET` | 存在 | `/vol1/1000/fnmusic-test-20261031` | 状态端点回显一致 |
| `FNMUSIC_MANUAL_SAVE_STAGING` | 存在 | `/vol1/1000/fnmusic-manual-save-staging` | 状态端点回显一致 |

### 4.2 unit / drop-in / 进程环境（来源冲突核对）

- unit `Environment=` 仅 `PYTHONUNBUFFERED`（受审 4 键：**无**）；unit 无 `EnvironmentFile=`；无 drop-in。
- `systemctl show -p Environment` 解析结果：受审键 0 个，其他键 1 个（未输出名称/值）。
- `proxy uvicorn`（pid 1870225）进程环境：`FNMUSIC_MANUAL_SAVE_ENABLED=TRUE`、`TARGET`、`STAGING`（与 `.env` 一致）；受审 playlist/DB/manifest 键无。
- `webui_gateway.py`（pid 1870256）进程环境：受审键无（`FNMUSIC_ENV_FILE` 等也未设置；仅 HOME/PATH 与 12 个其他键）。
- 容器 4 个进程：受审键无。
- **结论：受审键不存在来源冲突；但主开关实际生效值 = true，不满足 §0.2 关闭门。** 本次未修改配置、未重启、未通过临时关开关"修正"。

---

## 5. §0.1-5 账本 DB 路径推断与只读盘点

观察时间：UTC 2026-11-06T17:09:39Z / JST 02:09:39+09:00。

规则核对（部署版本代码，只读 grep/sed）：

- 部署版本 `webui_gateway.py` 内**没有** playlist env 合并/`FNMUSIC_MANUAL_SAVE_JOBS_DB` 读取逻辑（整歌单网关未部署）。
- 部署版本 `manual_save_jobs.py::ManualSaveJobsLedger.__init__` 要求 `database_path` 为**调用方传入的绝对路径**（否则 `ValidationError("absolute_database_path_required")`），模块内**无默认路径**；部署代码中也**没有** `ManualSaveJobsLedger(` 实例化点。
- 因此 `FNMUSIC_MANUAL_SAVE_JOBS_DB` 缺失 ⇒ **实际 jobs DB 路径不可确定（NOT DETERMINABLE）**。按 checklist §1.1 要求，**不得猜测默认位置**。

盘点结果：

| 目标 | 结果 |
| --- | --- |
| DB 路径 | **不可确定**（无配置键、无默认值） |
| `-wal` / `-shm` / `-journal` | **未获得路径，未 stat**（NOT OBSERVED） |
| 父目录 | 同上（NOT OBSERVED） |
| 包目录内 DB/类 DB 文件扫描 | `find /vol1/@appcenter/fnmusic-ext -xdev -maxdepth 5 \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*-wal' -o -name '*-shm' \)` 仅命中 `repo/sources-data/cache/netease-musicbox/nemcache.sqlite`（24576 B，2026-10-11T16:17），**与 manual_save 账本无关** |
| 是否存在其他位置账本 | **NOT OBSERVED**：无路径即无法证明"不存在"，不得据此宣称无 DB |

> 门禁含义：§0.1 要求"按当前代码规则确定实际 DB 路径并对 DB/父目录/sidecar 做 stat"——当前**无法完成**，该项保持未满足。

---

## 6. §0.1-6 曲库 / 暂存路径盘点

观察时间：UTC 2026-11-06T17:09:39Z / JST 02:09:39+09:00。

路径来源：repo `.env` 的 `FNMUSIC_MANUAL_SAVE_TARGET` / `FNMUSIC_MANUAL_SAVE_STAGING`（非猜测默认值），与 checklist §1 指定路径一致。

```bash
for p in /vol1/1000/fnmusic-test-20261031 /vol1/1000/fnmusic-manual-save-staging; do
  readlink -e -- "$p"; stat -c 'type=%F mode=%a owner=%U:%G size=%s device=%d' -- "$p"
  findmnt -T "$p" -no TARGET,SOURCE,FSTYPE; df -h "$p"
  find "$p" -xdev -type f | wc -l; du -sh --apparent-size "$p"; ls -la "$p"
done
```

| 项 | 曲库 target | 暂存 staging |
| --- | --- | --- |
| 路径（规范化） | `/vol1/1000/fnmusic-test-20261031` | `/vol1/1000/fnmusic-manual-save-staging` |
| 存在性/类型 | 存在，目录 | 存在，目录 |
| 权限/属主 | `750 kurumi:Users` | `700 root:root` |
| size / mtime | 128 / 2026-11-01 12:45:54 CST | 0 / 2026-11-01 16:30:16 CST |
| 挂载 | `/vol1`，`/dev/mapper/trim_8f1e972a_...-0`，**btrfs**，device 60 | 同左（**同一文件系统**） |
| 容量 | `/vol1/1000` 3.7T 总量，2.1T 已用，1.6T 可用 | 同左 |
| 文件数 / 目录数 | 4 / 3 | **0 / 1** |
| 总大小（apparent） | **363M** | 0 |
| 是否在包目录/repo 内 | **NO** | **NO** |
| 顶层内容 | `phase1/`、`metadata-20261101/`、3 个 `山田タマル - Always in my heart.flac`（126459367 / 126459476 / 126906165 B）+ 1 个同名 `.lrc`（944 B） | 空 |
| 未完成/残留暂存文件 | —（曲库内为既有测试产物，本次**未删除**） | **无** |

结论/观察：

- 曲库与暂存位于同一 `/vol1` btrfs 文件系统（同 device 60），满足"同文件系统原子 rename"的**前提条件**；但本次未验证发布路径行为，不作为通过证据。
- 两者均不在会被包替换的 repo/包目录内；升级脚本白名单不含这两条路径（见 checklist §6）。
- 属主差异（target `kurumi:Users` vs staging `root:root 0700`）记录待审，不在此判定安全性。

---

## 7. §0.1-7 在途任务 / 写入者只读盘点

观察时间：UTC 2026-11-06T17:09:39Z–17:15:58Z（JST 02:09:39–02:15:58）。

```bash
ps -eo pid,ppid,user,etime,cmd | grep -E 'fnmusic|manual_save|ffmpeg|ffprobe|uvicorn|takeover|webui_gateway|musicbox'
ss -ltnp | grep -E ':(8768|8770|8772|8774|8001|8002|8003|8004)\b'
ss -lxnp | grep -i -E 'fnmusic|listen.sock'
command -v lsof
ls -la /tmp | grep -i -E 'fnmusic|manual|save'
ls -A /vol1/1000/fnmusic-manual-save-staging | wc -l
```

观察结果：

- 相关进程仅服务本体与容器 worker：
  `takeover.py run`（pid 1870197，root）、`repo/.venv-proxy/bin/python -m uvicorn app:app --uds /run/fnmusic-ext/proxy-ntd48y9d/listen.sock`（pid 1870225，root）、`webui_gateway.py <repo>`（pid 1870256，root）、容器内 `uvicorn` 8004/8002、`supervisord`、`entrypoint.sh`。
- **无** `ffmpeg` / `ffprobe` / 单曲下载 curl / manual-save worker 进程在观察时刻运行。
- `/tmp` 无 `fnmusic-manual-save-*` 临时目录；staging 为空；曲库文件数在采集前后一致（4）。
- `lsof` 存在（`/usr/bin/lsof`），但 **DB 路径不可确定 ⇒ 未执行 lsof / `/proc/*/fd` 定向扫描**（脚本按分支跳过），**DB 写入者状态 NOT OBSERVED**。
- `ss -x` 观察时刻无 fnmusic 相关已建立连接。
- 部署版本的单曲保存为请求内同步执行；部署代码中未实例化 ledger，故不存在可观察的后台整单队列（静态推断，不等同于运行时证明）。
- `/tmp` 下另有其他队友/历史会话目录（`fnmusic-native-20261107`、`fnmusic-offline-runner-20261107`、`fnmusic-review-native-reviewer-20261107`、旧 ffmpeg bundle 等）；**本次未触碰**，与本任务脚本目录 `/tmp/deploy-inv-20261107` 相互独立。

> 门禁含义：checklist §0.1 要求"只读盘点所有在途歌单任务、单曲保存、worker 和 DB 写入者，并确认其如何恢复/取消"。当前**无法观测任务状态本身**（无 DB 路径且禁止打开 DB；整单功能亦未部署），该项未满足。

---

## 8. §0.2 高权限写接口：socket / 8774 / 身份头 / 无副作用探测

观察时间：UTC 2026-11-06T17:09:39Z–17:16:28Z（JST 02:09:39–02:16:28）。

### 8.1 socket 规范路径、监听进程、父目录

```bash
SOCK=/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock
readlink -e -- "$SOCK"; stat -c 'type=%F mode=%a owner=%U:%G size=%s mtime=%y' -- "$SOCK"
stat -c 'type=%F mode=%a owner=%U:%G size=%s' -- /vol1/@appcenter/fnmusic-ext
findmnt -T "$SOCK" -no TARGET,SOURCE,FSTYPE
ss -lxnp | grep -i 'fnmusic'
find /run/fnmusic-ext -maxdepth 3 -printf '%M %u:%g %p -> %l\n'
```

| 项 | 观察值 |
| --- | --- |
| 规范路径 | `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`（`readlink -e` 同值，非符号链接） |
| 类型/权限/属主 | **socket，mode 600，root:root**，mtime 2026-11-01 17:18:30 CST |
| 父目录 | `/vol1/@appcenter/fnmusic-ext`，目录，mode **775 root:root**；挂载 `/vol1`（btrfs） |
| 监听进程 | `ss -lxnp`：`users:(("python3",pid=1870256,fd=4))` = `repo/proxy/webui_gateway.py <repo>`（root，PPID 1） |
| 网关内部状态目录 | `/run/fnmusic-ext` mode **700 root:root**（tmpfs）；`maintenance.sock` mode 600 root:root；`ownership.json` mode 600；`webui-gateway.pid` |
| 内部 upstream socket | `ss` 显示 `/run/fnmusic-ext/proxy-ntd48y9d/listen.sock` 由 pid 1870225 fd 8 监听，但该路径在文件系统中**不存在**（目录为空）——见 §10 异常 |
| 非 root 访问冒烟 | `runuser -u nobody -- curl --unix-socket <sock> ...` → 无法连接（curl exit 7 / HTTP 000）；root 对照 → HTTP 403（未认证）。0600 在文件系统层阻断非 root 本地用户 |

### 8.2 8774 绑定与直连可达性

```bash
ss -ltnp | grep -E ':(8774|8770|8768|8772)\b'
docker ps --format '{{.Names}}|{{.Ports}}'
docker top fnmusic-sources
```

- 宿主监听：`127.0.0.1:8774`（及其余 8768/8770/8772），进程为 `docker-proxy`（pid 1808990），转发到容器 `fnmusic-sources` 的 **8004**（webui）。
- 容器内 `uvicorn app:app --host 0.0.0.0 --port 8004`（容器网络命名空间内 0.0.0.0，但宿主只发布到回环）。
- 8774 的 webui-service 是**非特权展示/代理层**：`repo/webui-service/app.py` 明确把 `/api/manual-save/*`、`/api/fs-check`、`/api/maintenance/cleanup` 在直连场景固定返回 **501 直连模式占位**；其 `AuthMiddleware` 只"解析单个规范管理员 claim"，代码注释明确"本中间件不认证 claim 来源"。

### 8.3 反向代理与身份头来源（只读配置/静态证据）

```bash
cat /usr/trim/nginx/conf/nginx.conf
cat /usr/trim/nginx/conf/conf.d/trim_http_cgi.conf
cat /vol1/@appcenter/fnmusic-ext/ui/config
ps -eo pid,cmd | grep nginx
ss -lxnp | grep -i 'trim_http_cgi\|trim_cgi'
systemctl cat trim_http_cgi.service
strings -a /usr/trim/bin/trim_http_cgi | grep -i 'x-trim'
sha256sum /usr/trim/bin/trim_http_cgi
```

关键观察：

- `trim_nginx`（master pid 2785；`/usr/trim/nginx/sbin/nginx`）：桌面端口 `0.0.0.0:11026 ssl`（另有 21927 → 302 跳转）。
- nginx `conf.d/trim_http_cgi.conf`：
  ```nginx
  location /app/ {
      proxy_pass http://unix:/var/run/trim_http_cgi.socket:;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
      proxy_set_header Host $host;
      proxy_read_timeout 300s;
  }
  ```
- `/var/run/trim_http_cgi.socket`：socket 模式 **770 root:www-data**，监听者 `/usr/trim/bin/trim_http_cgi`（pid 1781，**root**，systemd unit active since 2026-10-31 22:34:46 CST）。
- `trim_http_cgi` 二进制 SHA-256：`7773bee829fe535a36f941c0445a88d5de186e8c35960a882ea7063c2b7393c0`；二进制字符串中观察到 **`X-Trim-Userid`、`X-Trim-Isadmin`、`X-Trim-Username`**，以及 `gatewaySocket`/`GatewayPrefix`/`IsAdminOnly`。
- 应用注册清单 `/vol1/@appcenter/fnmusic-ext/ui/config`：`"url": "/app/fnmusic-ext/"`、`"gatewaySocket": "fnmusic-ext.sock"`、`"gatewayPrefix": "/app/fnmusic-ext"`、`"allUsers": false`；同目录 `config/privilege` 为 `{"defaults":{"run-as":"root"}}`。
- nginx 配置中**未发现** `X-Trim-*` 注入语句（`grep -rIn -i 'x-trim' /usr/trim/nginx/...` 无命中）⇒ 身份头**不是** nginx 静态配置注入，候选注入者是 fnOS 平台组件 `trim_http_cgi`。
- **未获证明（NOT OBSERVED）**：`trim_http_cgi` 是否剥离/覆盖客户端自带 `X-Trim-*` 头；其真实注入值是否来自已认证会话。需要真实已认证桌面请求或隔离副本才能验证——本次没有登录/认证，也未接触管理员会话。

### 8.4 无副作用 GET 探测（GET only；未调用任何写路由）

```bash
curl -sS --max-time 8 -o /dev/null -w 'HTTP=%{http_code}\n' http://127.0.0.1:8774/healthz
curl -sS --max-time 8 http://127.0.0.1:8774/api/manual-save/status
curl -sS --max-time 8 --unix-socket <sock> http://127.0.0.1/app/fnmusic-ext/api/manual-save/status
# 烟雾检查用伪造声明（GET only；不是身份证明）
curl -sS --max-time 8 --unix-socket <sock> \
  -H 'x-trim-userid: 1000' -H 'x-trim-isadmin: true' -H 'x-trim-username: kurumi' \
  -H 'x-fnmusic-save: 1' http://127.0.0.1/app/fnmusic-ext/api/manual-save/status
```

| # | 探测 | 结果（原始） |
| --- | --- | --- |
| A | 8774 `/healthz` | **200** `{"ok":true}` |
| B | 8774 `/api/manual-save/status`（无认证头） | **403** `{"ok":false,"error":"需要管理员"}` |
| C | 8774 status + 伪造完整头 + CSRF | **501** `{"detail":"直连模式不支持手动保存，请在飞牛桌面内打开管理台"}` |
| D | socket status（无头） | **403** `{"ok": false, "code": "forbidden", "message": "仅管理员可执行手动保存", "data": {}}` |
| E | socket status + 伪造完整头 + CSRF（GET） | **200** `{"ok": true, "code": "ok", "data": {"enabled": true, "target_dir": "/vol1/1000/fnmusic-test-20261031", "staging_dir": "/vol1/1000/fnmusic-manual-save-staging", "quality_order": ["jymaster","hires","lossless","exhigh"], "max_bytes": 1073741824}}` |
| F | socket status，`x-trim-isadmin: false` + userid + CSRF | **403** forbidden |
| G | socket status，admin=true 但无 userid + CSRF | **403** forbidden |
| H | socket status，admin=true + userid 但无 CSRF | **403** forbidden |
| I | socket playlist 路径 + 伪造完整头 + CSRF | **501** 直连模式占位（部署版本无 playlist 端点，转发到 webui-service） |
| J | 8774 playlist 路径（无认证） | **403** 需要管理员 |

解读（严格区分"观察"与"证明"）：

- 观察：宿主网关的 L4 授权仅要求"规范 `x-trim-isadmin: true` 声明 + `x-trim-userid` 声明 + `x-fnmusic-save: 1`"；这些是**声明（claim）**，网关代码注释与模块文档均明确"不认证 claim 来源"（`gateway_identity.py` / `webui_gateway.py` L4）。宿主的真实信任边界是 **L2 内核 peer uid 允许名单（默认仅 root）+ L1 socket 0600**。因此 root 本地直连可注入声明——这是设计内的通道信任模型。
- 观察：E 的 200 响应**交叉确认**了 `.env` 主开关的实际生效值 = **true**（`enabled: true`），并回显 target/staging 与 §6 盘点一致。
- 观察：D/F/G/H 的 403 只说明"声明/CSRF 不完整即拒绝"，**不能**证明"非授权本地调用者无法绕过认证直达写接口"；本次未调用 POST 写路由，也没有隔离副本中的授权矩阵测试。
- 观察（重要）：E 是 GET status，无副作用；探测前后 `.env` mtime、staging 空目录、曲库 4 文件/363M 均无变化（§8.5）。
- **§0.2 关闭门条件未满足**：`FNMUSIC_MANUAL_SAVE_ENABLED` 运行时 = **true**（要求 false）；`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED` 缺失；身份头来源/剥离未证明；无独立审查。

### 8.5 探测后无副作用复核

```
.env: mode=600 owner=kurumi:Administrators size=2663 mtime=2026-11-01 17:01:47.050890768 +0800  （与探测前一致）
staging entries=0
target file_count=4
target total_apparent=363M
/tmp/fnmusic-manual-save-* dirs=0
jobs DB 键赋值数=0
服务 active；容器 running/healthy；socket 600 root:root
```

结论：本次 GET 探测未观察到账本/曲库/暂存/配置变化。

---

## 9. 门禁判定（本次窗口）

### 9.1 §0.1 生命周期门：**BLOCKED**

| 要求 | 本次证据状态 |
| --- | --- |
| 记录本次设备 fnOS/扩展版本、包哈希、unit/drop-in、容器/worker、配置来源、DB/曲库/暂存路径与挂载 | **部分完成**：版本 2.10.2、关键文件 SHA-256、unit、容器、配置来源、曲库/暂存已完成；**jobs DB 路径不可确定** |
| 只读盘点在途歌单任务/单曲保存/worker/DB 写入者，并确认恢复/取消与停启是否触发后台任务 | **未满足**：无 DB 路径、禁止打开 DB ⇒ 任务状态 NOT OBSERVED；仅确认观察时刻无相关进程/暂存残留 |
| 形成并经隔离副本实测停/启/健康/失败恢复步骤，确认不会重复派发 | **未做**（门禁禁止；本次只读） |
| 生产停机前对 `.env`、jobs DB（SQLite backup API 一致快照）、曲库/暂存建立独立备份并在隔离副本验证恢复 | **未做**（门禁禁止；无 DB 路径亦不可做） |
| 明确回滚包/降级支持/兼容性并隔离验证 | **未做**（本次未找到/未验证包来源与哈希，仅有历史 FPK 记录；未安装/未回滚） |
| 快照标识、执行时间、命令/结果摘要、独立复核结论 | 本文件提供窗口/命令/结果摘要；**独立复核未进行** |

### 9.2 §0.2 身份/路径门：**BLOCKED**

未满足项：

1. **开关关闭条件不满足**：主开关运行时 `enabled: true`（要求 false）；整歌单开关缺失（按 §0.2 文字，缺失/无法确定即不满足关闭门）。
2. **身份头来源未证明**：未在真实已认证桌面链路（浏览器 → trim_nginx → trim_http_cgi → socket）观察到 `X-Trim-*` 的生成/剥离；仅取得静态候选证据（`trim_http_cgi` 二进制字符串与 `/app/` 路由配置）。
3. **生产路径直连拒绝/授权边界未验证**：只做了 GET 冒烟与 root 直连声明测试；未在隔离副本中分别验证受信任管理员、未认证、非管理员、伪造头对**写路由**的行为，也未验证拒绝时账本/曲库/暂存无变化。
4. **独立审查缺失**：本文件由采集者单方形成；§0.2 明确要求未参与实现/部署的独立审查者复核。
5. 8774 直连为非特权占位层（501 直连模式），**不构成**管理员授权边界证明；也不得把 127.0.0.1 绑定当作身份边界。

**本次只读盘点不改变门禁；在补齐上述证据并经独立复核前，不得执行任何部署/停服/开写操作。**

---

## 10. 异常与未知（需 lead/后续窗口关注）

1. **【高】主保存开关已为 true**：`repo/.env` 的 `FNMUSIC_MANUAL_SAVE_ENABLED` 存在且为 true，运行时状态端点确认 `enabled: true`；部署版本 2.10.2 含单曲手动保存能力。按只读授权**未修改**，也未通过重启/临时关开关"修正"。部署/回滚前需按已验证生命周期方案处理，不能直接编辑生产 `.env`。
2. **jobs DB 路径未知**：4 个键中 `FNMUSIC_MANUAL_SAVE_JOBS_DB` 缺失，且代码无默认路径；无法对 DB/父目录/sidecar 做 stat，也无法观察 DB 写入者或在途任务。**"没有 DB"未被证明。**
3. **部署版本功能缺口**：不含整歌单保存代码；`manual_save_playlist_jobs.py` 缺失、网关无 playlist 端点、运行时 501。整歌单 P7 必须在部署新包后进行。
4. **身份头来源未闭环**：`trim_http_cgi` 是静态候选注入者（binary strings + `/app/` 路由 + `gatewaySocket` 注册），但未验证其剥离客户端伪造头；且宿主网关自身对 root 直连的声明是"信任但不认证"，安全边界依赖 L1/L2（0600 + peer uid 只允许 root）。
5. **内部 listen socket 路径异常**：`ss` 显示 `/run/fnmusic-ext/proxy-ntd48y9d/listen.sock` 由 pid 1870225 监听，但该路径在文件系统中不存在（目录为空）。可能为启动后 unlink 的 socket；对 manual-save 路径的直接影响未观察到（宿主网关 status 端点自处理并返回 200），记录为未知。
6. **容器 repo bind 为 RW**：`/vol1/@appcenter/fnmusic-ext/repo` 以读写挂入 `fnmusic-sources`；若未来 DB 放进 repo，将暴露于包替换/容器写风险（当前未配置 DB）。
7. **曲库既有测试产物**：3 份重复 `山田タマル - Always in my heart.flac` + 1 个 lrc（363M，156±）；暂存为空。本次**未删除/未改名**；清理需独立授权与逐项核对。
8. **权限差异**：staging `root:root 0700` 与 target `kurumi:Users 0750` 属主/权限不同；服务以 root 运行单曲保存，容器进程以 kurumi 运行 —— 供后续发布路径/写权限复核。
9. `.env` 存在多个历史备份（`.env.bak.20261011160606`、`.env.bak.20261101152455`、`.env.bak.20261101155312`、`.env.pre-manual-save.20261101162531`），其中两个历史备份 mode 为 `----------`；本次仅记录文件名/stat，**未读取内容**。
10. **未获得（NOT OBSERVED / 未执行）**：
    - 任意停机/启动/重启/回滚验证、任何备份或恢复演练；
    - 容器内 `NetEase-MusicBox` 版本、容器内 `printenv FNMUSIC_PLAYLIST_MANIFEST_READONLY`（未 `docker exec`，避免在生产容器内起进程）；
    - fnOS 应用中心安装记录/安装时间元数据（未定位到应用元数据文件；未读应用中心 DB）；
    - 真实已认证桌面会话的请求链路（未登录/未认证）；
    - 账本 DB 内容/任务状态（禁止打开 DB）；
    - `systemctl` journal 全量日志、nginx access log 内容（隐私/范围原因未读）。

---

## 11. 采集命令与可复核性

- 所有设备侧命令均以只读 `.sh` 脚本执行：`/tmp/deploy-inv-20261107/recon.sh`、`inventory.sh`、`followup.sh`、`followup3.sh`–`followup8.sh`（本地副本在 `E:\tmp\*.sh`）。脚本经 `scp` 上传、`sed -i 's/\r$//'` 去 CRLF、`bash` 执行。
- 采集会话全程 `ssh -o BatchMode=yes kotomi-root ...`（已实测直连）；未使用密码、未输出任何密钥/Cookie/token。
- 本次窗口内观察时间以 `date -u`（UTC）与 `TZ=Asia/Tokyo date`（JST）分别记录；NAS 本地显示为 CST（UTC+8）。
- 只读命令清单（节选）：`hostname`、`uname -a`、`cat /etc/os-release`、`cat /usr/trim/etc/version`、`systemctl show/status/is-active/is-enabled/cat`、`docker ps/inspect/top`、`ps`、`ss -ltnp/-lxnp`、`stat`、`readlink -e`、`findmnt`、`df`、`du`、`find`（均 `-xdev`/有 timeout 限制）、`sha256sum`、`grep`、`sed`、`cat`（仅上述配置/unit/代码片段）、`curl GET`。
- 复核建议：按 §0.1/§0.2 原文逐项对照本文件 §9；任何需要写操作或停启的项必须在获得相应验证方案与授权后另开窗口记录，不得引用本文件替代。

---

## 12. 与 checklist 的绑定关系

- 依据：`docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` §0.1（生命周期门）、§0.2（身份/路径门）、§1.1（只读盘点规则）。
- 本文件**只**覆盖 2026-11-07 只读窗口；不覆盖：构建可复现、镜像可重建、安装成功、G0 真机链路、音频有效性/可播放性（checklist §7）。
- 下一步若要把 §0.1/§0.2 改为 PASS，必须针对同一设备/版本/包哈希/配置补齐 §9 所列证据（含真实身份链路与隔离副本授权矩阵、停/启/回滚与备份恢复演练），并由独立审查者复核原始证据。
