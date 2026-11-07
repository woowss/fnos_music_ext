# §0.1 生命周期 / 备份 / 回滚 runbook 草稿 独立复审（2026-11-07）

> **复审者**：`native-reviewer` —— 独立复审者，**未参与**本草稿的撰写，也未参与设备证据采集（`DEVICE_INVENTORY*` / `DEPLOYED_CODE_ANOMALIES` 等）。
> **被审文档**：`docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md`；**1327 行，117,588 B**；SHA-256 `09b6d1fc47282a813e80d6beb42ec07bf2c22d52b4ea1efc7244a7ab14e9fddb`；**纯草稿 / 未执行**（文首、§1.2、附录 C 均明确声明）。
> **复审时间 / 方式**：2026-11-07（JST）本地只读：重算哈希、通读草稿全文、对照设备盘点/检查单/决策页/仓库代码；**未访问设备、未执行任何设备/备份/停启/安装/回滚命令、未修改被审文档或代码**。唯一写入为本报告。
> **被审快照**：`E:\fnmusic-ext-dev`，分支 `codex/fnmusic-prelaunch-20261025`，HEAD `cf843a4`。
> **仲裁文档（本审查重算 SHA-256）**：
>
> | 文档 | SHA-256 |
> | --- | --- |
> | `docs/DEVICE_INVENTORY_20261107.md` | `d46342be8e1b4a7f99998af7afa8f390c8d454293296e3d3fd54a0cea760fed6` |
> | `docs/DEVICE_INVENTORY_FOLLOWUP_20261107.md` | `abcae1f7f7ff059c950281d8d402ff9cec33e5f64aa9d867112a0b1c09f23859` |
> | `docs/DEPLOYED_CODE_ANOMALIES_20261107.md` | `95d391a4c8accf35ba6791894e1f75e5e8fffe7231f6e818c3db68dc438e5734` |
> | `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` | `6e1648037a4dedd9bcefe43ab742b817c42bcde3f9318441eaa5ca19666a2548` |
> | `docs/OPEN_DECISIONS_20261107.md`（审查中由 40 行更新为 70 行） | `7573e743f2de5a1bc94809c6047ba7439edd491023b1c17e09cb0c74bfd7b82b` |
>
> **并发提示**：审查期间 `OPEN_DECISIONS_20261107.md` 被并行更新（11:27 → 11:54，新增 §D「编号空间与交叉映射」，RUNBOOK-R1..R12）。本报告的对齐评估以**当前 70 行版本**为准；被审草稿本身哈希在审查期间未变（两次重算均 `09b6d1fc…`）。

---

## 0. 结论速览

**判定：PASS-with-limits（草稿质量限定通过）。**

- 草稿把检查单 §0.1 六条展开为「事实 → 隔离候选 → 停机前备份 → 窗口逐步 runbook → 回滚边界 → 决策项 → 仍缺口」的可判定结构，**事实引用与四份权威证据文档高度一致**（逐项见 §1），三候选评估诚实，回滚边界正确，未把计划写成已执行。
- **但它不是可执行状态**：存在 5 项重要发现（F1–F5）与若干次要项（F6–F12），其中 F1（最终备份覆盖独立备份）、F2（停写步骤与容器写者顺序矛盾）、F3（恢复演练把 `.env` 明文解开）若照稿执行会**破坏 §0.1-4 要求的独立备份或秘密处理声明**。执行前必须修正。
- **§0.1 / §0.2 仍 BLOCKED**；本审查不构成部署、备份、停启、安装或回滚授权。草稿「A+B 不足以过 §0.1-3、必须候选 C/第二环境」的结论**成立**。

**发现统计**：重要 6（F1–F6），次要 6（F7–F12），无「文档造假/把计划写成已执行」类阻断。

---

## 1. 核验表（按复审任务 ①–⑦）

### 1.1 ① 事实引用是否与设备证据 / 检查单一致

| # | 草稿声称（位置） | 独立核对（权威来源） | 结论 |
| --- | --- | --- | --- |
| A1 | unit `fnmusic-ext.service`：600 root:root、788 B、sha `f6b2a6d0…`、无 drop-in；`Type=simple`、`User=root`、`UMask=0077`、`Restart=no`、`KillMode=mixed`、`TimeoutStartSec=75`、`TimeoutStopSec=20`；ExecStart/Post/StopPost（§2.2） | INV §3（`stat`/`systemctl show`，行 110-117）；FOLLOWUP §1（行 39）；ANOMALIES §2.1（NRestarts=0） | 符合 |
| A2 | MainPID 1870197、启动 2026-11-01 17:18:28 CST、enabled+active、NRestarts=0（§2.2） | INV §3 行 111-112（`ExecMainStartTimestamp=17:18:28 CST`）；FOLLOWUP §1；ANOMALIES §2.1 | 符合 |
| A3 | `KillMode=mixed` 下 SIGTERM 只发主进程、其余 cgroup 进程可能被 SIGKILL；`webui_gateway.py` 未见 SIGTERM handler（§2.2） | INV §3 行 115；仓库代码 grep：`webui_gateway.py` 无 `signal.signal`，仅 `os.kill(pid,SIGTERM)`（:2864） | 符合（derived 判定合理） |
| A4 | 容器 `fnmusic-sources`：image `16d5e8ef…`、build label `c50b9cc2…`、created 2026-11-01 15:51:21 +08:00、`Up 5 days (healthy)`、`restart=unless-stopped`；端口 8768/8770/8772/8774→8001..8004；repo→/repo rw、sources-data→/data rw；healthcheck 30s/10s/3/20s（§2.5） | INV §4 行 129-133；FOLLOWUP §4 行 149；`docker-compose.yml` 行 30-43；`container/*` | 符合 |
| A5 | 无 jobs DB：两配置来源缺失、受检范围无文件；唯一 SQLite 为 `nemcache.sqlite` 24576 B、`user_version=0`、`journal_mode=delete`、`redirects`/`responses` 各 0 行（§2.7） | FOLLOWUP §2.2/§2.3 行 65-80；INV §6 行 184-191 | 符合 |
| A6 | 2.10.2 与 FPK：`77bb7566…`、114 文件、113/114 一致、唯一 `app.js` 漂移（FPK `e12e6a5e…` 99,360 B vs 安装树 `22cde4ea…` 99,379 B，`cache:"no-store"` 补丁、同时进入容器 `/srv`）；`run_proxy.sh` 仅 mtime（§2.8） | FOLLOWUP §3.2 行 113-121 与 §4 行 153；ANOMALIES §1.1-§1.4 行 23-76 | 符合 |
| A7 | 2.8.2-rollback 为 `/tmp/build/282` 重建的混合树，与历史 2.8.2 有 60 个内容差异 + 3 个 only-in；`VERSION` CRLF；历史包 `a6aa0662…` 与升级前备份 `webui_gateway.py b87e39fd…` 吻合；受支持回滚目标 unknown（§2.8/§6.1） | FOLLOWUP §3.3 行 127-139；ANOMALIES §3.2-§3.5 行 213-245 | 符合 |
| A8 | 升级/卸载白名单：`.env`、`.env.bak*`、`sources-data`、`online_favorites`、`online_favorites.json`、`play_history`；jobs DB/cache/playlist_tracks/recommend_cache/llm_usage/logs 不在（§2.9） | FOLLOWUP §7.1 行 203-206；`packaging/fpk/cmd/_common:123-138`（`fnmusic_data_items`，行 123-138） | 符合 |
| A9 | journal 易失：`/var/log/journal` 不存在、`/run/log/journal/...`、仅当前 boot（2026-11-05 20:38:10 起）、`journalctl -u fnmusic-ext`=0、应用日志 2,536,824 B mtime 2026-11-01 16:44、install log 0 B、`repo/logs` 不存在（§2.10） | FOLLOWUP §8.1 行 245-249；INV §8 行 251-252 | 符合 |
| A10 | 曲库 4 文件 379,825,952 B mode 750 kurumi:Users device 60；暂存 700 root:root 0 文件 device 60；socket/ACL/L2 描述（§2.4/§2.11） | INV §7.1-§7.3 行 203-228、§9.2 行 278-284；FOLLOWUP §5 行 166-176；`VOL1_PERMISSION_MODEL_FINDING_20261107.md` §2-§3 | 符合 |

### 1.2 ② 三个隔离候选与硬约束

| # | 草稿声称（位置） | 独立核对 | 结论 |
| --- | --- | --- | --- |
| B1 | 候选 A 可行：`takeover.py` 支持 `--base/--target/--upstream/--state-dir`；代理依赖可 env 覆盖；网关只跑 `serve()`；fake official 靠 `/proc/<pid>/exe` basename=`trim-music`（§3.1） | 代码：`takeover.py:927-930`；`app.py:120-152`；`webui_gateway.py:2608`（serve）、`:2854-2866`（PID 文件 + SIGTERM）、`:2968`（main）、`:388-395/3002`（socket_path_for）；`takeover.py:133`（exe basename）、`:138`（/_ext/livez） | 符合 |
| B2 | **硬约束**：隔离副本绝不能执行 `webui_gateway.py main()`（会 SIGTERM 生产网关）；不能漏写 `--target/--upstream/--state-dir`；不能用固定 `MAINTENANCE_SOCKET` 的 `/api/maintenance`（§3.1/§3.5） | 代码：`PID_FILE=/run/fnmusic-ext/webui-gateway.pid`（:62）、`MAINTENANCE_SOCKET="/run/fnmusic-ext/maintenance.sock"`（:81）、`os.kill(pid,SIGTERM)`（:2864）——红线判断正确 | 符合 |
| B3 | 候选 A 单独不满足 §0.1-3（无真实 systemd/FPK/trim-music/容器/回滚）；候选 B 单独也不满足（镜像为构建时快照、非目标版本、docker run 绕过 compose） | §3.1/§3.2「不可验证内容」与 FOLLOWUP §4（镜像 `/srv` 为 FPK 原版、补丁在可写层）一致；结论成立 | 符合 |
| B4 | 候选 C（第二台 fnOS / 同 NAS VM / 系统或 `/vol1` 回滚快照）当前不可用/unknown；`@snapshot` 是用户子卷只读快照、不是系统/应用状态回滚（§3.3） | `SCAN_CONSISTENCY_REDESIGN_20261107.md` E10（行 87）与 §2.6；`BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` | 符合 |
| B5 | **结论**：A+B 可回答部分问题但不足以过 §0.1-3；真正 PASS 需候选 C 或等价第二环境；在此之前 §0.1-3 保持 BLOCKED（§3.6/§8 第 3 条） | 检查单 §0.1-3 原文（行 31-33）要求真实 unit/容器/App Center 生命周期 + 隔离副本实际验证 + 不重复派发；A/B 明确不覆盖，结论正确 | 符合 |
| B6 | 隔离纪律：一次一个候选、禁止默认路径/main()/生产 compose/生产端口、前后采集生产未受影响证据、结论只写「在隔离副本 X 观察到 Y」（§3.5） | 合理且可执行；代码红线与之一致 | 符合 |

### 1.3 ③ 备份范围 / 校验 / 恢复演练

| # | 草稿声称（位置） | 独立核对 | 结论 |
| --- | --- | --- | --- |
| C1 | 备份范围 B1–B11 覆盖：`.env` 及历史备份、安装树清单+漂移文件、数据白名单（含登录态）、曲库清单/可选全量、暂存空目录、unit/运行态、容器身份、包材料、日志取证、平台事实、当前无 jobs DB（§4.1） | 与检查单 §1.2（行 136-154）要求对齐；secrets/曲库/暂存/unit/包均被覆盖；`trimacl` 对管理员可读已注明（§4.2/§4.7） | 部分符合（表与命令不一致见 F6） |
| C2 | 校验：`MANIFEST.sha256` 自描述、归档可读、抽读、`.env` 哈希/键计数、曲库清单、生产静止对比、目录权限（§4.4） | 设计合理；但「抽读 `.env` 不落盘」与 §4.5 步骤 1 实际解包矛盾（F3）；B8/B4/B5 路径/失败处理有 fail-open 缺口（F6） | 部分符合 |
| C3 | 未来配置 jobs DB 后：停全部写者 → 只读 URI 打开源 → SQLite `backup()` → 副本 `integrity_check` → 无 sidecar → 记录 journal_mode/user_version（§4.6） | 与 `ISOLATED_LEDGER_MIGRATION.md`、检查单 §1.2「SQLite backup API 包含已提交 WAL」一致；草案正确 | 符合 |
| C4 | 恢复演练：只解到 rehearsal、逐文件比对、`.env` 流式复核、安装树只验证可重建子集、权限 diff、清理（§4.5） | 「不得对生产解包」正确；但步骤 1 把 `.env` 明文解出（F3），且「`.env` 不明文落盘」声明与命令矛盾 | 不符（F3） |
| C5 | 备份不能证明什么：并发写入、语义等价、trimacl 保密、恢复后可用、DB schema 兼容、不能替代 upgrade_init（§4.7） | 边界诚实，与清单/设备证据一致 | 符合 |

### 1.4 ④ 窗口逐步 runbook

| # | 草稿声称（位置） | 独立核对 | 结论 |
| --- | --- | --- | --- |
| D1 | Step 0–10 每步给出命令草案/预期/失败或回滚判据/证据目录；统一时区、敏感值只记哈希、失败不跳步（§5.1–§5.13） | 结构与检查单 §4/§5/§6 对齐；证据目录 `EVID` 与 `MANIFEST` 设计可执行 | 符合（但 F2/F4/F7 影响自洽） |
| D2 | Step 2 停写：关闭两开关（per-request 生效，无需重启）→ 两轮采样证明无可观察写者；不可说「历史无写入」；不得强杀排空；容器 WebUI/journald 列为外部写者（§5.3） | 关闭开关的单曲生效路径与 ANOMALIES §2.2 一致；但容器 W3（写 repo/data）在 Step 3 才停，Step 2 无法证明 W3 静止（F2） | 部分符合（F2） |
| D3 | Step 3 停服：先 unit（ExecStopPost restore 复位官方 socket）再容器；记录 rc/现象不静默；停后再次采样（§5.4） | unit 字段与 `takeover.py:787` 支持 restore；顺序结论与 §2.12 一致；理由中「ExecStopPost 依赖官方 listener」与容器无关（次要 F11） | 符合（措辞次要） |
| D4 | Step 6 安装/升级：upgrade_init → App Center 替换 repo → upgrade_callback → install.sh/extend；观察命令与失败判据（§5.7） | FOLLOWUP §7.2 行 212-216 行为描述一致；检查单 §3 包内容/§4 验证项一致；`install.sh:1458/1463/1465/1467`、`:673/711`、`:1408` 核对一致 | 符合 |
| D5 | Step 7/8 启动与默认关闭验证：`ready --timeout 65`、healthz 200、socket 600、直连 8774 playlist 501、未认证 status 403、容器 healthy、开关 false、jobs DB 不创建、app.js 漂移核对（§5.8/§5.9） | 检查单 §4（行 188-200）与 INV §9 的观测一致；`extend.sh:653`（ready --timeout 30）与 unit 65 不冲突（安装内/窗口内不同阶段）；但 §5.9「四个整歌单端点」与 6 端点不符（F4） | 部分符合（F4） |
| D6 | Step 9 回滚触发 T1–T8 与 R0–R4 阶梯；R0 冻结为默认第一步；R1 `restore.sh`；R3 禁止；R4 禁止试探（§5.10） | `restore.sh:126-201` 证实默认语义（restore-plan 先行→remember→disable --now→restore→删 unit→清容器→deployment-clear，保留 .env/数据）；阶梯与检查单 §6 一致 | 符合 |
| D7 | Step 10 收尾/证据留存/中断处理：恢复默认关闭、MANIFEST、门禁状态写回、只清本窗口创建文件、独立复核（§5.11–§5.13） | 与检查单 §0.1-6 一致 | 符合 |

### 1.5 ⑤ 回滚边界

| # | 草稿声称（位置） | 独立核对 | 结论 |
| --- | --- | --- | --- |
| E1 | 2.10.2 无整歌单能力；2.8.2 连单曲手动保存也没有；「应用恢复运行」≠「功能回滚完成」（§6.1/§6.2） | 检查单 §6 行 223-224 原文一致 | 符合 |
| E2 | 回滚目标/降级支持 unknown；rollback 重建版 ≠ 历史 2.8.2，两者都自洽；不得用卸载/重装/旧 FPK 试探（§6.3/§6.4） | FOLLOWUP §3.3/§9-8；ANOMALIES §3.5；检查单 §6 行 220-222 | 符合 |
| E3 | R0/R1 可默认；R2 需证明 fnOS 支持；R3 禁止；R4 仅用户明确授权且非试探；jobs DB 不删/不覆盖/不用旧快照覆盖（§6.5/§5.10） | 检查单 §6「账本回滚」行 229-231 一致 | 符合 |

### 1.6 ⑥ 待决策项与 `OPEN_DECISIONS_20261107.md` 对齐

| # | 核对项 | 结果 | 结论 |
| --- | --- | --- | --- |
| F1 | 草稿 §7 有 D1–D12；`OPEN_DECISIONS` 有 DESIGN-D1..D11、U1..U8、SCHEMA-Q1..Q7；两套 D 编号含义不同 | 审查期间 `OPEN_DECISIONS` 新增 §D：明确 `RUNBOOK-R1..R12`（「原文写作 D1–D12」）并给出逐项交叉映射——内容**已对齐/已收录**，草稿 12 项均有对应 R 编号（含 R5 第二 fnOS、R7 jobs DB、R10 G0-P5、R11 窗口时间、R12 改 `.env`） | 部分符合 |
| F2 | 草稿自身是否引用 `OPEN_DECISIONS`/R 编号 | 0 处引用；仍用局部 D1–D12 | 不符（见 F5） |
| F3 | 语义冲突项：`RUNBOOK-R1`(b)「短窗口停服务+容器」vs `DESIGN-D1`(b)「短窗口 Grade B，不重启服务」 | `OPEN_DECISIONS` 映射为 `DESIGN-D1 ≈ RUNBOOK-R1 + R11`，但两者选项互斥；若被当作同一窗口会冲突 | 需澄清（见 F5） |
| F4 | 遗漏项 | 草稿 12 项均出现在 `OPEN_DECISIONS` §D；U5（登录）/U7（真实账号）不在草稿范围（草稿明确 §0.2/P7 独立）——不构成遗漏 | 符合 |

### 1.7 ⑦ §0.1 六条映射与 BLOCKED 缺口

| # | 核对项 | 结果 | 结论 |
| --- | --- | --- | --- |
| G1 | §8 表逐条映射检查单 §0.1 六条（版本/包/unit/容器/配置/路径；在途与写者；隔离副本实测；停机前备份与恢复演练；回滚包/降级/兼容；证据与授权） | 逐条与检查单行 26-39 原文对照：措辞准确，未漏项；每条均给「现状/本文补充/仍缺证据/补齐条件」 | 符合 |
| G2 | §0.1 与 §0.2 独立、P7 与 G0-P5 仍 BLOCKED、不改变门禁 | 草稿文首/§1.5/§8 附加说明均明确；与检查单行 41-43、48-54 一致 | 符合 |
| G3 | 「仅 A+B 不足以过 §0.1-3」与§3.6/§8 第 3 条一致 | 一致，且与检查单行 31-33 的「不能用 active/健康端点/静态审查/一次成功安装代替」一致 | 符合 |

---

## 2. 独立核对记录（本地只读，未访问设备）

- **哈希**：重算被审草稿 `09b6d1fc…`（1327 行 / 117,588 B，与任务给定一致）与 5 份仲裁文档（见文首表）；审查期间草稿哈希未变。
- **全文通读**：被审草稿 1327 行全文。
- **证据对照**：`DEVICE_INVENTORY_20261107.md` §1–§9（平台/包/unit/容器/配置/jobs DB/曲库/socket/在途）；`DEVICE_INVENTORY_FOLLOWUP_20261107.md` §1–§9（基线/jobs DB/FPK/socket/白名单/journal/剩余 unknown）；`DEPLOYED_CODE_ANOMALIES_20261107.md` §1–§3（app.js 漂移/无 DB 行为/回滚包）；`MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` §0.1/§0.2/§1.1/§1.2/§3/§4/§5/§6；`OPEN_DECISIONS_20261107.md`（当前 70 行版）。
- **代码核对**（仓库工作树）：`proxy/webui_gateway.py`（PID_FILE:62、MAINTENANCE_SOCKET:81、serve:2608、_daemonize:2869、SIGTERM:2864、main:2968、socket_path_for:388/3002、6 个 playlist handler 均调用 `_manual_save_playlist_gate` :2235/2272/2314/2359/2402/2446）；`proxy/takeover.py`（--base/--target/--upstream/--state-dir:927-930、official 识别:133、/_ext/livez:138、restore_plan:336-371、wait_official_target:699-714 + 40.0:758、wait_ready:526、supervise:717、state.restore:787）；`proxy/app.py:120-152`；`proxy/manual_save_playlist_jobs.py:917/940/1019`；`container/env_flag.sh:32-51`；`packaging/fpk/cmd/_common:123-138`；`install.sh:673/711/1406-1524/1458-1467/1783-1864`；`extend.sh:408-413/653/634/240-299`；`restore.sh:126-201`；`docker-compose.yml:30-43`。
- **边界**：本审查**未**访问任何设备、未执行任何设备/备份/停启/安装/回滚命令、未读取生产 `.env`/DB、未修改被审文档或代码。

---

## 3. 发现

### 重要（执行前必须处理）

**F1（重要）§5.5「最终备份」与 §4.3「停服前独立备份」共用 `$BK`，按稿执行会覆盖停服前独立备份。**
§4.3 全部 B1–B10 写入 `$BK`（`$VOL/fnmusic-ext-backup-$TS-v2.10.2-predeploy`，行 555）。§5.5 说「重复 §4.3 的 B1–B10，输出到 `EVID/backup/final-*`」（行 957），但**未定义 `BK_FINAL`、未要求重设 `$BK`**，命令草案仍写 `$BK`。执行者若直接重跑 §4.3 命令块，`config/`、`data.tar.gz`、`install-tree.sha256`、`config.sha256` 等会被停服后状态覆盖——而检查单 §1.2（行 138-140）明确要求「停服前 §0.1 的独立备份和恢复验证必须已完成；停服后再做一次本节最终备份」，§0.1-4 也禁止以停服后备份替代停服前备份。**后果**：唯一满足「停机前」时间点的备份被覆盖，§0.1-4 证据链失效。**修法**：定义独立 `BK_FINAL` 与独立 `EVID/backup/final-*`，两份各自生成 MANIFEST 并在报告中绑定 different timestamps；显式禁止写入 predeploy 目录。

**F2（重要）§5.3「停写」发生在 §5.4 停容器之前，而此时已知写者 W3（容器内 WebUI，repo rw）仍在运行；与 §1.4「W3 必须静止」及 §2.12「只停 unit 不足以让 W3 静止」矛盾。**
§1.4 行 82 把 W3（`repo/.env*`、`sources-data`、`online_favorites*`、`play_history`、`recommend_cache`、`logs`）标为「停写窗口内是否必须静止：**是**（备份时哈希稳定）」；§2.12 第 1 条（行 262-263）明确「停机=两个动作……只停 unit 不足以让 W3 静止」；但 §5.3 Step 2 在 §5.4 停容器**之前**做「停写 + 在途排空证明」，且 §5.3 自己说「容器内 WebUI（W3）……在 §5.4 停容器后消失」（行 913-914）。因此 Step 2 的两轮采样**无法证明 W3 静止**；§4.3 B3 的停服前 `sources-data` tar 也可能与容器写入并发（§4.7 承认非原子）。同时 §5.3 对 `.env` 的编辑与容器 WebUI 写 `.env` 存在竞争。**修法**：把 Step 2 明确命名为「关闭写开关 + 在途盘点（W1/W2/W3-host 可观察范围）」，将「全部写者静止」断言移到 §5.4 停容器之后；或把停容器提前（并调整 §2.12 顺序与回滚考虑），同时给 `.env` 编辑加锁/校验。

**F3（重要）§4.5 恢复演练步骤 1 把 `.env` 明文解包到 rehearsal，与步骤 2「`.env` 不明文落盘：改用流式复核」自相矛盾。**
行 680 `tar -xzf "$BK/data.tar.gz" -C "$RS"` 会解出 B3 白名单内的全部文件（含 `.env`、`sources-data` 登录态）；行 681-683 却声称 `.env` 采用流式复核、不明文落盘。rehearsal 位于 `/vol1`（trimacl 下管理员可读），多出一份明文秘密副本，且通过条件 ①「白名单文件数/大小与备份清单一致」在排除 `.env` 后需相应调整。**修法**：改为 `tar --exclude='./.env' --exclude='.env' -xzf ...`（先逐项 `tar -xOf | sha256sum` 流式校验秘密文件），或明确承认 rehearsal 会落盘 `.env` 并调整清理/权限/声明。

**F4（次要但需修正）§5.9「四个整歌单端点全部 403」与本文别处「网关 6 端点」及实际代码不一致。**
草稿行 117/1179 称目标版本含「网关 6 端点」；目标代码 `webui_gateway.py` 确有 6 个 playlist 端点（`playlists`/`snapshot`/`jobs`/`job`/`advance`/`cancel`，行 175-198），且 6 个 handler 都先过 `_manual_save_playlist_gate`（总开关→整歌单开关，行 2235/2272/2314/2359/2402/2446）——总开关关闭时 6 个均返回 403 `disabled`，仅整歌单关时 6 个均 `playlist_disabled`。按 §5.9 只验「四个」会漏验两个（尤其两个 GET 只读端点）。**修法**：改为「六个」，或明确只验四个写端点并列出另外两个只读端点的预期码与理由。

**F5（重要）决策编号空间未在草稿内声明：`OPEN_DECISIONS` 已新增 `RUNBOOK-R1..R12` 映射，但草稿仍用局部 D1–D12 且 0 处引用；`RUNBOOK-R1` 与 `DESIGN-D1` 的停写选项语义不同。**
`OPEN_DECISIONS` §D（行 42-68）已把草稿 D1–D12 登记为 `RUNBOOK-R1..R12` 并做交叉映射，同时注明「待各文档复审/收口时统一……RUNBOOK/SCHEMA 内逐项改引；在此之前按上表理解」。草稿自身未引用该页，执行者可能把 `D1` 误读为 `DESIGN-D1`（扫描期停写，推荐「不重启服务」）而非 `RUNBOOK-R1`（部署窗口，推荐停服务+容器）。**修法**：在 §7 顶部加一段「本文 D1–D12 = `OPEN_DECISIONS` 的 `RUNBOOK-R1..R12`，为局部编号；扫描窗口用 `DESIGN-D1`，与本窗口是两次独立决策/窗口」；按收口建议逐步改用 R 编号。

**F6（重要）§4.1 备份范围表与 §4.3 命令草案不完全对应，且部分命令 fail-open，可能漏证据。**
- B2 表称 `<repo>` + `<app>/ui` + `<app>/config`（行 518），命令只 `find "$REPO"`（行 582）；
- B6 表称含 `/run/fnmusic-ext/ownership.json`（仅取证）、`/var/lib/fnmusic-ext/deployment`（行 522），命令只复制 unit + `systemctl show` + docker（行 613-621），两者均未采集；
- B9 表称 `/var/log/apps/fnmusic-ext-install.log`（行 525），命令只 stat `fnmusic-app.log`（行 634）；
- B8 包哈希命令硬编码 `/vol1/1000/...` 且整体 `2>/dev/null || true`（行 624-628），若包缺失/路径变化会**静默产生空清单**，与 §4.3 失败判据「包哈希与登记不一致 → 停止部署」不匹配；B4/B5 也硬编码 `/vol1/1000`，与 §4.2「`VOL` 由 findmnt 推导、不猜 `/vol1`」不一致。
**修法**：表与命令一一对应；对 B8/关键项去掉 `|| true` 或增加「输出必须非空/条目数=预期」的显式检查；路径改为窗口内核实变量。

**F7（重要）§5.3 的 `.env` 编辑脚本非原子、会改写整文件并可能改变语义；「diff 只含这两行」的预期不保证。**
脚本（行 838-858）按行读取后 `open(path,"w")` 整文件重写：① 对目标键输出 `key=false` 会**丢掉原行的 `export ` 前缀**（若原文件用 `export FNMUSIC_...=true`，语义可能变化）；② 强制 LF 与末尾换行，若原文件为 CRLF/无末尾换行，`diff` 会多出非目标键变化；③ 无临时文件+rename，写入中途失败会截断 `.env`（虽有 `env.before-stop` 备份，但未写恢复命令）。**修法**：保留 `export ` 前缀与原行风格、写临时文件后原子替换（或 `sed -i` 前先备份）、执行后强制 `diff` 仅两键 + 校验 mode/owner/hash，失败即从备份恢复并停止。

### 次要

**F8（次要）§3.1 候选 A 命令草案第 3 步目标目录未创建。** `mkdir -p "$ISO"/{repo,home,data,run,logs}`（行 359）不含 `fake-official/`、`stub-upstream/`、`stub-sources/`、`env/`，但第 3 步 `install -m 0755 /usr/bin/python3 "$ISO/fake-official/trim-music"`（行 365）会因目录不存在而失败；source-manifest `find … -maxdepth 2`（行 362-363）绑定不完整。应补 `mkdir -p` 与完整清单。

**F9（次要）§2.12 第 3 条「启动会失败并触发回滚逻辑」表述不准确。** `systemctl start` 失败只会使 unit failed，不会触发 `extend.sh` 的 `trap rollback`（该 trap 只在安装/升级的 extend 流程内，`extend.sh:634`）；窗口内失败应按 §5.8→§6.5 处置。建议改为「启动失败 → 按 §6.5 处置」。

**F10（次要）若干引用行号偏差（不影响结论，建议核对）：** §2.2「`wait_official_target` 40 s」引 `takeover.py:699-714`，40.0 实参在 `:758`；§2.9「`extend.sh:114-137`」与「1.3 官方音乐 socket 存在」不对应（1.3 在 `:408-413`；:114-137 是 unit/deployment owner 与 `.env` 存在检查）。

**F11（次要）§5.4 停服顺序理由部分不准确。** 「先停容器 → 代理向不可用音源转发」成立；但「unit 的 `ExecStopPost` 仍依赖官方 listener」与容器无关（`restore()` 依赖 official upstream，而非容器）。顺序结论（先 unit 后容器）本身正确（§2.12 第 2 条、unit `ExecStopPost` 语义）。

**F12（次要）§5.3/§5.8 使用 `x-trim-isadmin: true` 等伪造头做只读 status，缺 §0.2 限制标注。** 检查单 §0.2 行 69-70 明确「手工带 `x-trim-isadmin: true` 的 Unix socket 请求仅是指定头部下的接口烟雾检查，不是身份来源或管理员授权证明」。草稿 §5.8 行 1022-1023 直接使用但未在该命令旁标注；建议引用 §0.2 原文防止误用。

---

## 4. 本审查解除 / 未解除清单

**解除（有独立核对证据）**：
- 草稿的事实引用与 `DEVICE_INVENTORY`/`_FOLLOWUP`/`DEPLOYED_CODE_ANOMALIES`/检查单**逐项一致**（§1.1 十项、§1.2–§1.5）。
- 三个隔离候选的可行性/边界评估**诚实**：`webui_gateway.py main()` 杀生产网关、`takeover` 默认路径、fake official 识别、容器镜像非目标版本、候选 C 不可用等硬约束**正确**。
- **「A+B 不足以过 §0.1-3、必须候选 C/第二环境」结论成立**。
- 备份范围覆盖 secrets/曲库/暂存/unit/包（除 F6 的命令缺口）；未来 DB 一致快照方案正确。
- 回滚边界（2.8.2-rollback 重建版≠历史包、R0/R1 默认、R2 需证据、R3/R4 禁止）**正确**。
- §0.1 六条映射完整，逐条给出仍缺证据与可判定补齐条件；未把计划写成已执行（文首/§1.2/附录 C 明确）。

**未解除（仍 BLOCKED / 仍未知）**：
- §0.1 生命周期门、§0.2 身份与路径门（草稿明确不覆盖 §0.2）。
- 真实隔离环境（候选 C/第二 fnOS）不存在，§0.1-3 无任何系统级停/启/健康/回滚实测。
- 停机前独立备份、恢复演练、最终备份**均未执行**（草稿为草案）。
- 受支持回滚目标、fnOS 降级支持、2.8.2 包兼容性 unknown。
- G0-P5 writer-drain/rollback 演练门 BLOCKED。
- 本 runbook 未执行；未取得任何窗口内证据；本复审不改变任何门禁，也不构成授权。

---

## 5. 执行前最小修复清单（按优先级）

1. **F1**：拆分 `BK`（停服前独立备份）与 `BK_FINAL`（停服后最终备份），禁止覆盖；两份各自 MANIFEST。
2. **F2**：明确「Step 2 = 关闭写开关 + 在途盘点（W1/W2/W3-host）」，把「全部写者静止」断言移到停容器之后；或提前停容器并调整顺序。
3. **F3**：rehearsal 不解包 `.env`（先流式校验），或明确落盘并调整清理/声明/通过条件。
4. **F7**：`.env` 编辑改为保留 `export`/原子写入 + 强制 diff + 失败恢复命令。
5. **F6**：备份表与命令一一对应；B8/关键项去掉 `|| true`/增加非空校验；路径改窗口变量。
6. **F4**：把「四个整歌单端点」改为六个（或说明四个写端点+两个只读端点的预期）。
7. **F5**：§7 增加 OPEN_DECISIONS `RUNBOOK-R1..R12` 交叉引用，明确 D1 局部编号与 `DESIGN-D1` 的区别（两次独立窗口）。
8. F8–F12：命令目录/引用行号/措辞/伪造头标注等。

> 以上修复均属文档/命令草案层面；修复后仍需在**真实隔离环境（候选 C）**取得 §0.1-3 证据并经独立复核，才能谈 §0.1 PASS。本列表不构成任何执行授权。

---

## 6. 本报告哈希说明

- 本报告写入完成后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REPORT-HASH-STAMP` 行给出 `sha256(本行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）。
- 被审草稿哈希（`09b6d1fc…`，1327 行 / 117,588 B）在审查期间两次重算均未变化。
<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 74f9171fed0463c5a4f7da2e6d373ae5d580415b0e47eb3fd4496dafd8c31f62 -->
