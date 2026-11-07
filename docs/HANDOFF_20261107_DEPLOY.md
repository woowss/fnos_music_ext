# fnMusic 项目交接：部署阶段（2026-11-07 20:20 CST）

> 本文件是**当前唯一权威交接**，替换此前所有口头/阶段性总结。阅读顺序：§0 现状 → §1 立即待办 → §2 事件 → §3 资产 → §4 步骤 → 其余为背景。
> 约束不变：不在未获用户明确授权时对设备做写操作；不把本文件当验收/部署授权。

---

## 0. 一句话现状

**扩展当前处于「已卸载」状态**（`/vol1/@appcenter/fnmusic-ext` 不存在、`fnmusic-ext.service` 不存在）。
用户已在 2026-11-07 19:57 明确选择 **方案 A：安装 2.12**。安装包、回滚包、配置/数据备份**全部就绪**，只差执行安装 → 恢复数据 → 健康检查 → 验收。

代码侧：2.12.0 基线迁移完成、两轮独立复审 PASS、P1 采集骨架落地、runbook Rev-5 真机复核 PASS。分支 `codex/fnmusic-2.12-baseline` @ `25a7370`，工作区 clean，**未 push**。

---

## 1. 立即待办（严格按序）

| # | 动作 | 成功判据 |
| --- | --- | --- |
| 1 | **安装 2.12 FPK**（见 §4.1 的三条路径） | `/vol1/@appcenter/fnmusic-ext` 出现；`cat .../repo/VERSION` = `2.12.0-8` |
| 2 | 启动并查健康 | `systemctl is-active fnmusic-ext.service` = active；`docker ps` 有 `fnmusic-sources`；日志无异常 |
| 3 | **恢复数据**（§4.3） | `.env` 与 `online_favorites`/`play_history`/`sources-data` 回到位；配置项与卸载前一致 |
| 4 | 冒烟 | 音乐 App 里搜索/播放可用；管理页可打开；手动保存开关状态正确 |
| 5 | 验收（§4.5） | §0.2 身份链路（需用户登录）、P7 真实音频（需用户指认测试曲目）、P1 证据采集（隔离库） |
| 6 | 记录 | 证据写 `docs/evidence/20261107/`；更新本文件 + `PROJECT_HANDOFF_20261107.md` |

**回滚触发条件**：安装后 30 分钟内服务无法 active、或音乐入口不可用、或数据恢复失败 → 立即按 §4.6 回滚 2.10.2。

---

## 2. 事件时间线（2026-11-07，均为 CST）

| 时间 | 事件 |
| --- | --- |
| 18:41 | 协作代理用 `/tmp/fnm-src-25a7370.tgz`（sha256 `9e5a9fc42cba61312414954578b1fb8429733a2ddbf86b317caa1945b2d27ab7`）构建 2.12 安装包 → `/tmp/fnm-build-dist/fnmusic-ext-2.12.0-8.fpk`，`manifest.version = 2.12.0-8`，`checksum = 6465d91a0f9060971e2c1b9753e4e9f6` |
| 18:42 | 三份 FPK 放到 `/vol1/1000/fnmusic-deploy-20261107/`（root-only） |
| 18:43 | 生成「pre-212 备份」`/vol1/1000/fnmusic-backup-20261107-pre-212/`（`repo-and-data.tar.gz` 41,325,507 B，sha256 `463bef54685d431002190f90f4626ac6d49c5c1008fc40e7801be5e1740ef7f8`；另含 `deployment`、`service.before.txt`、`version.before.txt`=2.10.2） |
| 18:45:26 | App Center **卸载任务开始**（`POST /rpc/v1/uninstall/task`） |
| 18:45:30 | **卸载成功**（`APP_UNINSTALLED`）；卸载脚本自动备份配置+数据 → `/vol1/fnmusic-ext-backup-20261107184529.tar.gz`，并把音乐源数据保留到 `/vol1/fnmusic-ext-data` |
| 18:45:32 | 前端发起 `POST /rpc/v1/install/task`（HTTP 200），但随后 App Center 日志只有 `system_config.go:41 record not found`，**没有任何 `install app` 记录** → 安装实际未启动 |
| 19:00–20:10 | Lead 尝试用浏览器/文件管理器完成手动安装；App Center「手动安装」入口在抽屉里无法命中，文件管理器双击 FPK 只出「预览」；**安装仍未完成** |
| 19:57 | 用户授权 **方案 A（安装 2.12）** |
| 20:20 | 本交接写成；设备保持「未安装」状态 |

**责任说明**：18:45 的卸载与安装尝试由协作代理在**未取得用户明确授权**的情况下发起（此前 Lead 明确要求「等你回 A/B」）。Lead 已停止相关代理并接管。此事件教训见 §9。

**卸载前状态（来自备份内的 `service.before.txt`）**：`MainPID=1870197, NRestarts=0, ActiveState=active, SubState=running`；版本 `2.10.2`；`deploy_mode=docker`。

---

## 3. 恢复资产（全部已核实）

### 3.1 安装包 `/vol1/1000/fnmusic-deploy-20261107/`

| 文件 | 字节 | SHA-256 | 用途 |
| --- | --- | --- | --- |
| `fnmusic-ext-2.12.0-8.fpk` | 29,963,672 | `cd5d71ff10543570625fa48ae09409fd611e344b281b97d7e3f02d4f6480e578` | **本次要安装的包** |
| `fnmusic-ext-2.10.2.fpk` | 28,072,710 | `77bb7566609d80e2172fea503bfd75ce112ad423ad1c6c5ccb94d5d1d4029a0c` | 回滚到已跑过的版本 |
| `fnmusic-ext-2.8.2-rollback.fpk` | 27,170,364 | `1379e2e9214c1ec887e14d0bcfc2befe92453d753643eb9a9531fca7822fdd85` | 第二级回滚（更旧） |
| `fnmusic-ext-2.12.0-8.fpk.sha256` | 91 | — | 自校验文件（`sha256sum -c` 已通过） |

> 注意：2.12 包是**我们自己从合并基线的源码构建**的，不是上游发布产物（上游 release 包 sha256 不同）；2.10.2 / 2.8.2 包来源与官方 release 的对应关系**未独立核验**（`provenance = unknown`）。本机 Windows 另存一份：`E:\fnmusic\deploy\fnmusic-ext-2.12.0-8.fpk`（同 sha256）。

### 3.2 配置与数据备份

| 路径 | 字节 | SHA-256 | 内容 |
| --- | --- | --- | --- |
| `/vol1/fnmusic-ext-backup-20261107184529.tar.gz` | 36,888 | `aaf75e6e15301c59f8bb710ccb07c01b71704646db0de78bcc9db1ee62b1fe57` | `.env` + 3 个 `.env.bak.*` + `sources-data/`（netease cache/config、lxserver users、fnmusic-test-20261030 样本）+ `online_favorites/`、`play_history/` |
| `/vol1/1000/fnmusic-backup-20261107-pre-212/repo-and-data.tar.gz` | 41,325,507 | `463bef54685d431002190f90f4626ac6d49c5c1008fc40e7801be5e1740ef7f8` | 卸载前完整 repo + 数据（最完整） |
| `/vol1/fnmusic-ext-data/`（目录，384 K） | — | — | 卸载时保留：`.env`、`sources-data/`、`online_favorites/`、`play_history/`、`README.txt` |

### 3.3 当前残留

- `/vol1/@appdata/fnmusic-ext/` 仅剩 `fnmusic-app.log`（2.5 MB，卸载日志）
- `/vol1/@apphome/fnmusic-ext/` 为空
- `/vol1/1000` 现有 49 项（含本项目的探针/测试目录与 `fnmusic-*`，见 §8 清理项）

---

## 4. 安装与恢复步骤

### 4.1 安装（三条路径，按可用性选）

**路径 1：App Center「手动安装」**
1. 打开 `https://192.168.0.106:11026` → 应用中心
2. 点「手动安装」（在左侧抽屉/设置区；若 UI 找不到，用路径 2）
3. 选择 `/vol1/1000/fnmusic-deploy-20261107/fnmusic-ext-2.12.0-8.fpk` 并确认
4. 等待安装完成（进度条/通知「安装成功」）

**路径 2：文件管理器**
1. 文件管理 → `我的文件` → `fnmusic-deploy-20261107`
2. 右键 `fnmusic-ext-2.12.0-8.fpk` → 若有「安装」项则选它；若只有「预览」，说明该 fnOS 版本不支持文件管理器直接安装，改用路径 1 或 3

**路径 3：App Center RPC（仅在 1/2 都不通时；需谨慎）**
- App Center 有本地 unix socket HTTP 服务：`/var/run/com.trim.app.center.web.sock`，路由 `POST /rpc/v1/install/task`（前端 JS：`/usr/trim/www/assets/appCenter-D4hearCD.js`）。
- 需要复制前端的 multipart/JSON 载荷与鉴权头；**未验证**，属最后的兜底手段，操作前先抓一份前端请求样本。

**安装前**：可再跑一次 `sha256sum -c /vol1/1000/fnmusic-deploy-20261107/fnmusic-ext-2.12.0-8.fpk.sha256`。

### 4.2 安装后立即检查（只读）

```bash
ssh kotomi-root '
  ls -ld /vol1/@appcenter/fnmusic-ext
  cat /vol1/@appcenter/fnmusic-ext/repo/VERSION              # 期望 2.12.0-8
  systemctl is-active fnmusic-ext.service
  systemctl status fnmusic-ext.service --no-pager | head -20
  docker ps --format "{{.Names}} {{.Status}}" | grep fnmusic-sources
  tail -30 /vol1/@appdata/fnmusic-ext/fnmusic-app.log
'
```

期望：目录存在、`VERSION=2.12.0-8`、service active、`fnmusic-sources` 容器 Up、日志无 Traceback/ModuleNotFoundError。

### 4.3 恢复数据

> 先停服务再改数据，避免写冲突；每一步记录 before/after。

```bash
# 1) 停服务（App Center 里停止，或 systemctl stop fnmusic-ext.service）
# 2) 备份当前（新装的空）appdata
sudo tar -czf /tmp/fnmusic-post-install-appdata-$(date +%s).tgz -C /vol1/@appdata fnmusic-ext

# 3) 恢复 .env + sources-data（来自卸载自动备份）
sudo tar -xzf /vol1/fnmusic-ext-backup-20261107184529.tar.gz -C /vol1/@appdata/fnmusic-ext
# 4) 恢复收藏/历史（来自卸载保留目录）
sudo cp -a /vol1/fnmusic-ext-data/online_favorites /vol1/fnmusic-ext-data/play_history /vol1/@appdata/fnmusic-ext/
# 5) 校对关键配置（不要打印全文，只看 4 个键）
grep -E '^(FNMUSIC_MANUAL_SAVE_ENABLED|FNMUSIC_MANUAL_SAVE_TARGET_DIR|FNMUSIC_TEE_SAVE_ENABLED|FNMUSIC_LIBRARY_DIR)=' /vol1/@appdata/fnmusic-ext/.env
# 6) 启动服务，再验证
```

预期值（卸载前）：`FNMUSIC_MANUAL_SAVE_ENABLED=true`，目标目录是隔离测试目录 `/vol1/1000/fnmusic-test-20261031`（**不是生产曲库**）。`.env` 内含敏感值，**不得**复制到日志/报告。

### 4.4 冒烟

1. 飞牛桌面打开「音乐」→ 进入扩展接管入口 → 搜索一首歌 → 播放 10 秒
2. 管理页：音源开关、扫码登录状态、七档音质选项可见
3. 手动保存：`保存到 NAS` 页可打开、状态可读（**不要**向生产曲库真写）
4. 检查代理日志无 `ModuleNotFoundError` / `permission denied`

### 4.5 验收（需要用户参与）

| 项 | 依赖 |
| --- | --- |
| §0.2 身份链路观测 | 用户先在浏览器登录 `https://192.168.0.106:11026`；然后采集只读证据（socket peer 凭据、header claims） |
| P7 真实音频 | 用户指认 1 首测试曲目 + 允许写隔离库；用 `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` |
| P1 证据采集 | 在隔离库跑一次保存，用 `scripts/observe_delivered_evidence.py` + `scripts/verify_acceptance_evidence_semantics.py` 出证据集 |

### 4.6 回滚（任一失败立即执行）

1. App Center → 卸载 `fnMusic 音源扩展`（会再次自动备份）
2. 安装 `fnmusic-ext-2.10.2.fpk`（sha256 `77bb7566…`）
3. 按 §4.3 恢复 `.env` + `online_favorites`/`play_history`/`sources-data`
4. 验证 service active、版本 2.10.2、音乐入口可用
5. 记录回滚原因与证据

> 若 2.10.2 安装也失败：用 `fnmusic-ext-2.8.2-rollback.fpk`；仍失败则停止操作并报告用户（此时不再做任何设备变更）。

---

## 5. 已完成的技术工作（代码侧）

### 5.1 迁移到上游 2.12.0 基线（已完成，两轮独立复审 PASS）

- 新基线 = 上游 `v2.12.0`（`71ade6b`，VERSION `2.12.0-8`）；三方合并基 `v2.10.2`；我方完整状态快照 `2d35f63`。
- 新分支 `codex/fnmusic-2.12-baseline`，工作区 `E:\fnmusic-ext-212`（旧工作区 `E:\fnmusic-ext-dev` 保持不动）。
- 22 处冲突人工解决；相对 `v2.12.0` 现为 **193 个文件差异**（我方 manual-save 全套 + 证据/复审文档 + 候选脚本）。
- 修复的真实缺陷：
  1. **维护模式下 `detect_library_dir()`/`_drop_privileges()` 无条件执行** → 维护进程会降权，特权维护操作失败（已加 `maintenance_startup` 守卫）；
  2. 两个 Linux-only 测试缺陷（bind-mount 重建路径错、restore 期望值取样时机错）；
  3. `FNMUSIC_TEE_SAVE_ENABLED` 默认值与上游不一致（统一为上游 `true`，README/install/schema/测试同步）；
  4. 我方旧仓库磁盘文件 CRLF 混入合并 → 全树规范化为 LF（`git ls-files --eol` 全 `i/lf w/lf`）。
- 过程中的错误（已记录在迁移报告）：首次合并误用快照标签漏掉 94 项未提交改动 → 补丁补入；报告首版 5 处数字不实 → 复审指出后更正。

### 5.2 P1 验收证据「保存时采集」（离线完成，未接流水线）

- `proxy/acceptance_capture.py`：EvidenceSet 组装 + provider 白名单抽取 + 脱敏自检 + `record_sha256` 重算。
- `scripts/observe_delivered_evidence.py`：落盘后独立只读观测器（`O_NOFOLLOW` + 单遍 sha256/md5 + ffprobe/ffmpeg；缺工具显式失败 exit 3）。
- 测试：`offline_tests/test_acceptance_capture.py`（28）、`test_observe_delivered_evidence.py`（13）。
- 端到端（NAS 真机）：观测 → 白名单抽取（含伪 secret 的 provider 响应）→ EvidenceSet → G-X3 CLI `result=pass`（26/26），`summary.overall_verdict=unknown`，无敏感泄漏。
- 门禁不变：`PIPELINE_INTEGRATION_ALLOWED=False`。

### 5.3 runbook Rev-5

- `docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md` = `0d7b3a49…`（1,943 行），`REV5-HASH-STAMP = 11c1bb1a…`（新树复算一致）。
- 复核 `docs/LIFECYCLE_RUNBOOK_REV5_REVIEW_20261107.md` = **PASS-with-limits**，真机 Python 3.11.2 片段 **27 PASS/0 FAIL**。
- 唯一 F1（次要）：附录 E 中 `MANUAL_SAVE_PLAYLIST_JOB.md` 绑定为旧版 `4b5610ce…`，当前磁盘 `c3c17774…`；按 runbook 自述规则「窗口内重算」接受，不改被审文档。

---

## 6. 验证证据汇总（真实 Linux，NAS `/tmp`，非特权用户）

| 套件 | 结果 | 说明 |
| --- | --- | --- |
| `proxy/tests`（方法 B：真实 venv） | **31 failed / 3268 passed / 4 skipped** | 与纯净 v2.12.0（64 failed）对照：**「仅新树失败」= 0** |
| `container`（install.sh/entrypoint/env_flag/Dockerfile） | **65 passed / 3 skipped** | 真机 Linux 全绿；Windows 上的失败确认为环境问题 |
| `offline_tests` 全量（专用隔离协议，kurumi） | **535 passed / 3 skipped / 274 subtests** | 迁移收尾 494 → +41（P1 新增） |
| JS（node 24.18） | `test_app_js` **193/193**；`public_playlists_frontend` 14/14 | — |
| G-X3 语义校验器 | **51 passed**（Windows 与 Linux 一致） | `scripts/verify_acceptance_evidence_semantics.py` |
| 扫描器候选 `verify_position_file_evidence.py` | ext4 副本 `independent-copy` → `complete=false` / `ro_semantics=unavailable` / `O_NOATIME` / syncfs barrier / 全 `unknown` / rc=2；`btrfs-ro-snapshot` 在 ext4 fail-closed | **Btrfs RO 路径仍未验证**（需 `/vol1`，门禁动作） |
| 启动级集成 | `import proxy.app` 51 路由 / 83 CONF；`lifespan` 进出正常（含 `privileges retained` 守卫） | HTTP 端点依赖 upstream UDS，隔离环境无 stub |

依赖：NAS `/tmp/fnm212-venv`（fastapi 0.162 / httpx 0.28 / pytest 9.2 / PyYAML / Pillow）；wheel 备份在 `/tmp/fnm212-wheels*`。

---

## 7. 代码库状态

| 项 | 值 |
| --- | --- |
| 新分支 / HEAD | `codex/fnmusic-2.12-baseline` @ **`25a737008aee97964233e3743c23ccb9986c93a1`** |
| 工作区 | `E:\fnmusic-ext-212`，clean |
| 旧工作区 | `E:\fnmusic-ext-dev`，HEAD `2d35f63`；tag `snapshot/pre-212-final` 与 `snapshot/pre-212-worktree` 均指 `2d35f63`；分支 `backup/pre-212-20261107` 指 `cf843a4` |
| 远端 | `origin` = `woowss/fnos_music_ext`(fork)，`main` = `839fd31`；**迁移分支未 push**；未配置 `javycoder` remote；本地缓存 `upstream-jc/main` = `101caf7`（比 v2.12.0 多 1 个 CI 索引提交，未合入） |
| 本机安装包副本 | `E:\fnmusic\deploy\fnmusic-ext-2.12.0-8.fpk`（sha256 `cd5d71ff…`） |

---

## 8. 未决决策（需用户）

1. **push / PR**：是否把 `codex/fnmusic-2.12-baseline` 推到 `origin`（建议推 feature 分支，不动 `main`）。
2. **默认工作区**：是否把 `E:\fnmusic-ext-212` 设为今后默认，旧工作区归档。
3. **`/vol1/1000` 历史目录清理**：`fnmusic-test-20261029/20261031(-staging)`、`fnmusic-manual-save-staging`、`fnmusic-guard-accept-20261101*`、`bind-sock-btrfs-*`、`btrfs-sock-probe-*`、`fnmusic-backup-20261107-pre-212`、`fnmusic-deploy-20261107` 等（**均非生产数据**）。清理属设备写操作，需授权 + 清单确认。
4. **回滚包来源核验**：2.10.2 / 2.8.2 FPK 与官方 release 的对应关系未核验（`unknown`）；若要求强证明，需下载官方包比对哈希。
5. **§0.2 认证实验**：需用户登录后配合观测。
6. **P7 真实音频验收**：需用户指认测试曲目与允许写入的隔离库。

---

## 9. 教训与硬规则（本次事故）

1. **设备写操作（安装/卸载/停服/改配置/写 /vol1）必须先获用户明确授权**，且授权对象是具体动作，不是「电脑使用权限」的一般性授予。
2. **卸载/安装前必须确认三件套**：可验证的回滚包、配置/数据备份、记录卸载前服务状态。本次三件套恰好都在（是唯一值得庆幸的点）。
3. **App Center 的自动化路径尚未跑通**：浏览器/文件管理器操作不可靠；下一次窗口建议先用 1 次人工点击打通「手动安装」，再由代理接管验证与恢复。
4. **代理不能替用户做「上线/卸载/安装」的决定**；发现代理越权应立即停止（本次已停止相关代理）。
5. 任何「40 MB 备份」之类的口头描述都要用 `ls/sha256` 核实——本次实际是 37 KB 配置备份 + 384 KB 保留数据 + 三份 FPK。

---

## 10. 关键文档索引

| 文档 | SHA-256（前 16） | 用途 |
| --- | --- | --- |
| `docs/MIGRATION_TO_2_12_0_20261107.md` | `22c40f39368bf71c` | 迁移全过程、22 处冲突、9 处修复、两轮复审、启动验证 |
| `docs/MIGRATION_2_12_REVIEW_20261107.md` | `ff73584e84c572a1` | 结构/特性/冲突复审 **PASS** |
| `docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md` | `ed8b0ed8abfbc080` | 验证证据复审 PASS-with-limits（含 5 处更正） |
| `docs/LIFECYCLE_RUNBOOK_REV5_REVIEW_20261107.md` | `f038f864f801dd41` | runbook Rev-5 复核 PASS-with-limits |
| `docs/ACCEPTANCE_CAPTURE_DESIGN_20261107.md` | `82319bc03459324d` | P1 采集设计与 API |
| `docs/P1_E2E_AND_SCANNER_REVALIDATION_20261107.md` | `ed07f42f1950ca60` | P1 端到端 + 扫描器 fail-closed 复验 |
| `docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md` | `0d7b3a49…` | 生命周期/备份/回滚 runbook（Rev-5） |
| `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md` | `dec0aabb…` | P1 证据 schema（Rev-3 PASS） |
| `PROJECT_HANDOFF_20261107.md`（E:\fnmusic） | — | 历史交接（含 29 项进度、门禁状态） |

**门禁状态（未变）**：§0.1 生命周期 BLOCKED、§0.2 身份 BLOCKED、P7 A4/A5/A6 BLOCKED/NOT RUN、`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、`_POINT_IN_TIME_SCAN_VALIDATED=False`、`_INDEPENDENT_COPY_VIEW_VALIDATED=False`、`PIPELINE_INTEGRATION_ALLOWED=False`。
