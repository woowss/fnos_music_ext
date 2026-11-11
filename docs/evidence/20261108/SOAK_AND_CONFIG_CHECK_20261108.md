# 2.12.0-9 装机后复查（重启自恢复 + 配置/资产核对）

- **采集时间**：2026-11-08 14:10–14:30 JST（NAS 本地 CST 13:10–13:30）
- **设备**：fnOS「kotomi」192.168.0.106:11026（fnOS 1.2.0800）
- **方法**：`ssh kotomi-root` 只读命令 + `fnos-cli 0.6.1 --profile kotomi`（`user info` = `result: succ`）+ 本地仓库只读代码核对。**未做任何设备写操作**（未改 .env、未建目录、未启停服务、未清理镜像）。
- **基线**：`codex/fnmusic-2.12-baseline` @ `ae7680d`；安装版本 `2.12.0-9`（2026-11-08 00:10 安装，见 `INSTALL_2120_9_VERIFY.md`）。

## 1. 结论速览

| 项 | 实测 | 判定 |
| --- | --- | --- |
| 版本 / App Center 状态 | `repo/VERSION=2.12.0-9`；fnos-cli `app list` 中 `status=running, version=2.12.0-9` | PASS |
| 重启自恢复 | NAS 01:24:30 重启 → 容器 01:25:21 起、服务 01:26:23 起（`NRestarts=0`）；当前连续运行约 11h50m | PASS（有一次记录级噪声，见 F3） |
| 服务 / 容器 | `active/running`；`fnmusic-sources` Up (healthy)，`RestartCount=0` | PASS |
| 健康端点 | `/_ext/healthz` → `ok:true version:2.12.0-9 musicbox:ok upstream:ok degraded:false failures:[]`；musicbox `/healthz` ok；WebUI `:8774/` 200/48,890 B | PASS |
| 未登录快速路径 | `/music/api/v1/search/track` → `401 {"code":99999,"msg":"INVALID TOKEN"}` 13 ms；`favorite-track/list` 6 ms；`search/suggest` 7.7 ms（<3 s 验收口径） | PASS |
| 日志 | `journalctl -u fnmusic-ext` 自 00:00 起 0 条 critical/traceback/exception，`-p warning` 无条目；容器近 300 行 0 error | PASS |
| 资源 | 服务 Memory 117.9 MB / CPU 累计 122 s / 7 tasks；宿主 load 0.33；内存 3.8/15.6 GB；`/vol1` 57% | PASS |
| 数据 | `sources-data` 592 K、`online_favorites` 8 K、`play_history` 8 K；`.env` 3,705 B / 68 个 `FNMUSIC_*` 键 | PASS |
| 官方音乐 App 版本 | **1.1.19**（manifest mtime 2026-11-07 20:27）；扩展 `FNMUSIC_COMPAT.json` 仍声明 `1.0.10` | 路径级 PASS / 基线待更新（F5） |
| 网易云登录态 | musicbox `auth/status` → `logged_in: true` | PASS |
| 推荐缓存 | `recommend_cache/.../daily-20261108.json` 41.7 K、`hot-20261108.json` 42.1 K 均已生成（00:18） | PASS（LLM 层故障见 F1） |
| 生产曲库 | `/vol1/1000/music` mtime 2026-10-18，无新写入 | PASS |

## 2. 重启自恢复证据

| 事实 | 证据 |
| --- | --- |
| 重启时刻 | `uptime -s` = `2026-11-08 01:24:30`；`last reboot` 显示 `still running` |
| 容器先起 | `docker inspect fnmusic-sources`：`StartedAt=2026-11-07T17:25:21Z`（=01:25:21 CST），`RestartCount=0`，health=healthy |
| 服务自启 | `systemctl show`：`ExecMainStartTimestamp=2026-11-08 01:26:23`，`NRestarts=0`，`ExecStartPost=takeover.py ready --timeout 65` 通过 |
| 接管 socket | `/var/run/trim_music.socket` `srw-rw-rw- kurumi:Users`，mtime 01:26；`/_ext/healthz` 正常 |
| App Center 事件 | `TRIMEVENT ... "eventId":"APP_AUTO_STARTED"` @ 01:27:01；`/app/fnmusic-ext` 入口每 30 分钟注册成功（`trim_http_cgi ... service register success`） |

## 3. 官方音乐 App 1.1.19 兼容性核对（路径级）

- 官方 App 已从适配基线 `1.0.10` 升级到 **`1.1.19`**（`/var/apps/trim.music/manifest`，磁盘 mtime 2026-11-07 20:27，早于本次安装）。
- 扩展拦截的 35 条 `/music/api/v1/*` 路径，逐条在 `trim-music` 二进制字符串中核对：**33 条逐字存在**；仅 2 条参数化 HLS 路径（`/{guid}/preset.m3u8`、`/{guid}/{filename}`）在字符串表中不逐字出现，但其基路径 `/api/v1/track/hls/` 存在 → **路径级兼容 PASS**。
- 1.1.19 新增端点（`/api/v1/search/tracks/best`、`/search/lyrics/best`、`/track/roam-*`、`/folder-view/*`、`/shared-library/*`、`/user/auth-login`、`/user/temp-token` 等）不在拦截清单内，按设计直通官方上游，不参与在线源合并。
- `FNMUSIC_COMPAT.json` 的 `music_versions` 仍是 `["1.0.10"]`，且代码中无任何运行时校验/告警（`grep FNMUSIC_COMPAT` 在 proxy/webui/extend 无命中）→ 纯文档基线，需在 UI 验收后更新。

## 4. 发现项

### F1（功能，高优先）LLM 推荐调用失败：协议/URL 错配 + 凭据被拒

- 现状 `.env`：`FNMUSIC_LLM_BASE_URL='https://opencode.ai/inference/openai/v1/chat/completions'`、`FNMUSIC_LLM_PROTOCOL='responses'`、`FNMUSIC_LLM_MODEL='deepseek-v4.1-flash'`。
- 代码 `proxy/recommend.py:_llm_endpoint()`：protocol=responses 且 base 不以 `/responses` 结尾时**追加** `/responses` → 实际请求 `.../v1/chat/completions/responses`。
- 实测（NAS 本机、无凭据路由探测）：`.../chat/completions` → 400（路由存在）；**`.../chat/completions/responses` → 404（路由不存在）**；`.../v1/responses` → 400（路由存在）；`.../v1` → 404。
- `llm_usage.json`：20261108 共 3 次请求，`prompt/completion/total=0`，`unknownUsageRequests=3`，`lastFailure.reason=http-404` → 与上述路径错配一致。
- 进一步用 `.env` 中的 key 实测：正确路径下两个协议均返回 `401 {"error":{"type":"authentication_error","message":"Invalid credential"}}`（key 长度 51、前缀 `oc_sk`）→ **即使修好路径，当前凭据也不可用**。
- 影响：推荐链路 LLM 层降级（healthz `recommend.mode="per-user: llm -> local-random"`），网易云热歌/每日缓存仍可用，不阻塞播放。
- 修复建议（需授权）：
  1. 二选一修正配置：`FNMUSIC_LLM_PROTOCOL=chat`（base 保持含 `/chat/completions`）；或 `FNMUSIC_LLM_BASE_URL='https://opencode.ai/inference/openai/v1'` + 保留 `responses`。
  2. 提供对该服务有效的 API key（管理页「LLM 配置」或 `.env`）。
  3. 建议管理页对「base URL 已含 /chat/completions 但协议选 responses」做一致性提示（WebUI 当前无校验）。

### F2（数据/显示，低）`llm_usage.json` 的 `lastFailure.at` 恒为 1000000000

- `proxy/llm_usage.py:26` `_MAX_INT = 1_000_000_000`，`:79` 对 `at`（epoch 秒，当前约 1.79e9）套用 `_safe_int` 上限截断 → 恒等于 1e9（2001-09-09）。
- 影响面：healthz / `llm_usage.json` 中该时间戳错误；WebUI 用量面板只显示 reason/detail，不显示 at（`webui-service/static/app.js:112`），故用户侧影响很小。
- 修复：时间戳字段单独用「合理上限」（如 `int(time.time()) + 86400`）或不做上限，补一条回归测试。**本次仅记录，未改代码。**

### F3（启动顺序，低）开机时 App Center 启动脚本在官方音乐 socket 就绪前执行，记录 ERROR 后退出

- `fnmusic-app.log` 末尾（219–220 行，mtime 01:25）：
  `==> 步骤 1/5: 环境预检...` → `[ERROR]【前置条件未满足】未检测到飞牛音乐运行套接字。`
- 时序：01:25:19 `sudo ... takeover.py prepare-install-lock`（App Center 自动启动路径调用 `cmd/main start` → `extend.sh`）→ 失败；01:25:21 容器起；01:26:23 systemd `fnmusic-ext.service` 自启成功；01:27:01 `APP_AUTO_STARTED`。
- 同窗口其它 App 也出现 `APP_AUTO_START_FAILED_DOCKER_NOT_AVAILABLE`（01:25:01），属 fnOS 开机竞态；本扩展最终由已 enable 的 systemd 单元兜底，无用户影响。
- 改进建议（代码侧，需重打包才生效）：`/var/apps/fnmusic-ext/cmd/main` 的 `start` 分支在调用 `extend.sh` 前等待 `/var/run/trim_music.socket`（有界等待 + 重试，例如 60 s），避免开机日志出现误导性 ERROR。

### F4（P7 前置，需授权）手动保存的 target / staging 目录均不存在

- `.env`：`FNMUSIC_MANUAL_SAVE_ENABLED='true'`、`TARGET='/vol1/1000/fnmusic-test-20261031'`、`STAGING='/vol1/1000/fnmusic-manual-save-staging'`。
- 实测两目录 **均不存在**（`ls -ld` No such file or directory；`/vol1/1000` 下已无任何 `fnmusic*` 目录）。
- 代码 `proxy/manual_save_route.py:_default_open_store()` 以 `O_RDONLY|O_DIRECTORY` 打开两者，任一不存在即 `_SaveFailure("staging_unavailable")` → **按当前环境执行保存会失败**。
- 历史属性（`DEVICE_INVENTORY_20261107.md` §7）：target `750 kurumi:Users`，staging `700 root:root`，同 btrfs 挂载（st_dev 相同，满足原子 rename 前提）。
- 建议（设备写操作，需授权）：
  ```bash
  install -d -m 750 -o kurumi -g Users /vol1/1000/fnmusic-test-20261031
  install -d -m 700 -o root   -g root  /vol1/1000/fnmusic-manual-save-staging
  ```
  之后按 `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` 做 P7。

### F5（资产，需确认）回滚/备份资产已从 NAS 消失

| 原路径（2026-11-08 01:2x 仍在） | 现状 |
| --- | --- |
| `/vol1/1000/fnmusic-deploy-20261107/`（2.10.2 / 2.8.2 / 2.12.0-8 / 2.12.0-9 FPK） | **不存在** |
| `/vol1/1000/fnmusic-backup-20261107-pre-212/`（41,325,507 B 完整备份） | **不存在** |
| `/vol1/1000/fnmusic-prebuild-20261107/`（约 130 MB 预构建） | **不存在** |
| `/vol1/fnmusic-ext-data/` | 不存在（安装 adopt 后已清理，属预期） |
| `/vol1/fnmusic-ext-backup-20261107184529.tar.gz`（36,888 B） | 仍在（权限 `000`） |
| `/vol1/fnmusic-ext-backup-20261101152452.tar.gz`（34,421 B） | 仍在（权限 `000`） |

- `find /vol1 /vol2 /vol3 -maxdepth 3 -name 'fnmusic*.fpk'` → 0 命中。Windows 侧 `E:\fnmusic\deploy\` 仅有 `2.12.0-8` / `2.12.0-9` 两份，**没有 2.10.2 / 2.8.2**。
- 结论：**回滚到 2.10.2 缺少 FPK**（`fnmusic-sources:rollback-20261101` 镜像仍在，可复用镜像层；但重装 2.10.2 需重新取得官方 release 包或按 tag 构建）。需确认该清理是否用户主动执行；若是，请接受「回滚需重新取包」这一事实并记录。

### F6（卫生，可选）Docker 镜像/构建缓存可回收约 7.35 GB

`docker system df`：Images 10.94 GB / 可回收 7.347 GB（含 4 个 fnmusic 悬空镜像各约 1.32 GB：`b36effe0`(82d75107 指纹)、`1fbf38aa`、`ba948fa4`、`39153d82`；Build Cache 768 MB）。清理属设备写操作，需授权后再动。

## 5. 待用户参与的验收项（未变化）

| 项 | 依赖 | 当前状态 |
| --- | --- | --- |
| 音乐 App 内搜索/播放 | 用户登录态下操作 | 接口层 401 快速路径已验证；1.1.19 路径级兼容通过；**UI 实测未做** |
| P7 真实音频 / 手动保存 | 指认测试曲目 + 允许写隔离库 | 目录缺失（F4），未开始 |
| §0.2 身份链路观测 | 用户登录后采只读证据 | 门禁 BLOCKED，未做 |
| 网易云扫码登录 | 用户扫码 | **已登录**（`logged_in: true`） |

## 6. 门禁状态（未变）

`§0.1 生命周期 BLOCKED`、`§0.2 身份 BLOCKED`、`P7 A4/A5/A6 BLOCKED/NOT RUN`、`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、`_POINT_IN_TIME_SCAN_VALIDATED=False`、`_INDEPENDENT_COPY_VIEW_VALIDATED=False`、`PIPELINE_INTEGRATION_ALLOWED=False`。

## 7. 复现命令（只读）

```bash
# 服务/容器/健康
systemctl show fnmusic-ext.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStartTimestamp
docker inspect fnmusic-sources --format '{{.State.Health.Status}} {{.RestartCount}} {{.State.StartedAt}}'
curl -s --unix-socket /var/run/trim_music.socket http://localhost/_ext/healthz
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' --unix-socket /var/run/trim_music.socket \
  'http://localhost/music/api/v1/search/track?keyword=x'   # 期望 401 + 48 B
# 日志
journalctl -u fnmusic-ext.service --since '2026-11-08 00:00' -p warning
docker logs --tail 300 fnmusic-sources 2>&1 | grep -ciE 'traceback|error'
# 官方音乐版本 / 扩展基线
cat /var/apps/trim.music/manifest | grep -E 'appname|version'
cat /vol1/@appcenter/fnmusic-ext/repo/FNMUSIC_COMPAT.json
```

## 8. 处理结果（2026-11-08 14:32–14:50 CST / 15:32–15:50 JST，用户已授权写操作）

### 8.1 F1 LLM 推荐 —— 已修复并实测通过

- **根因确认（两条）**：
  1. 路径错配：`protocol=responses` + base 已含 `/chat/completions` → 实际请求 `…/chat/completions/responses`（404）。
  2. 凭据不可用：原 `oc_sk` key 属于 DSH 的 `opencode-go` provider（`https://opencode.ai/inference/go/v1`），该上游要求 `x-opencode-session` 头（实测无头返回 `400 MissingSessionID`），扩展无法注入该头；原 key 对旧路径 `…/openai/v1` 亦返回 `401 Invalid credential`。
- **采用方案**：改用 DSH 中已配置的 `new-api` provider（OpenAI Responses/Chat 兼容）：
  - `FNMUSIC_LLM_BASE_URL='https://llm.tantantan.tech/v1'`
  - `FNMUSIC_LLM_PROTOCOL='chat'`（扩展拼接 `/chat/completions`）
  - `FNMUSIC_LLM_MODEL='deepseek-v4.1-flash'`
  - `FNMUSIC_LLM_API_KEY` = new-api key（写入设备 `.env`，未在文档/日志中出现）
  - `FNMUSIC_LLM_TIMEOUT_S='60'`（原默认 20s 偏短：实测一次 2,204 token 的真实请求耗时 25.26s）
  - `FNMUSIC_RECOMMEND_BUDGET_S='90'`（原默认 40s；`recommend.py::run_tier` 以剩余预算作 `asyncio.wait_for` 超时，90s 才容得下 25–60s 的 LLM 层；该键为热重载键，proxy.log 记录 `14:53:32 .env 热重载生效: FNMUSIC_RECOMMEND_BUDGET_S`）
- **变更前备份**：`repo/.env.bak.20261108-143203`（root:root 640）。`.env` 保持 `kurumi:Administrators 640`。
- **运行态验证**（服务已重启，MainPID 变更）：
  - 子进程环境（`/proc/<uvicorn pid>/environ`）：`FNMUSIC_LLM_BASE_URL=https://llm.tantantan.tech/v1`、`PROTOCOL=chat`、`MODEL=deepseek-v4.1-flash`（`API_KEY` 已设置）。
  - 扩展代码路径：`recommend._llm_endpoint()` = `('chat', 'https://llm.tantantan.tech/v1/chat/completions')`；`recommend._llm_complete()` 返回 `ok`，账本记 42+2 token（用 `/tmp` 隔离账本验证，未污染正式账本）。
  - 真实规模请求：HTTP 200 / 8,877 B / **25.26s** / prompt 95 + completion 2,109 token，返回合法 JSON 歌曲数组。
- 说明：`llm_usage.json` 中 2026-11-08 的 4 次历史失败（http-404×3、timeout×1）保留为审计记录，未篡改；`lastFailure` 会在下一次失败时覆盖（成功不清空，属既有行为）。

### 8.2 F4 手动保存目录 —— 已建立

```text
install -d -m 750 -o kurumi -g Users /vol1/1000/fnmusic-test-20261031
install -d -m 700 -o root   -g root  /vol1/1000/fnmusic-manual-save-staging
```

- 实测：`drwxr-x--- kurumi:Users`、`drwx------ root:root`，两者 `st_dev=60`（同 btrfs 挂载，满足原子 rename 前提）。
- 以扩展代码验证：`manual_save_route.load_config()` → `enabled=true`、target/staging 路径一致；`O_RDONLY|O_DIRECTORY` 打开均 `OPEN_OK`（原 `staging_unavailable` 风险解除）。
- P7 真实保存验收仍需用户指认测试曲目（本步未写任何音频文件）。

### 8.3 F5 回滚资产 —— 已恢复

- 从 Windows 副本恢复四个 FPK 与校验文件到 `/vol1/1000/fnmusic-deploy-20261107/`（目录 700，文件 600，root）：
  - `fnmusic-ext-2.10.2.fpk` 28,072,710 B `77bb7566…` ✔
  - `fnmusic-ext-2.8.2-rollback.fpk` 27,170,364 B `1379e2e9…` ✔
  - `fnmusic-ext-2.12.0-8.fpk` 29,963,672 B `cd5d71ff…` ✔
  - `fnmusic-ext-2.12.0-9.fpk` 29,919,023 B `35af6788…` ✔
  - `SHA256SUMS`（368 B）由设备侧重算生成，四个哈希与交接文档完全一致。
- 新建当前状态备份：`/vol1/1000/fnmusic-backup-20261108-post-2120-9.tar.gz`，30,036,776 B，`sha256 6cdd40dad827baf0063a775a642be55b127fc0126d7fdb28de61937537a2cc3b`（排除 `.venv-proxy`/`__pycache__`/`recommend_cache`/`cache`；已验证含 `.env`、`online_favorites`、`play_history`、`sources-data`）。
- 原 `fnmusic-backup-20261107-pre-212`（2.10.2 时代 41,325,507 B）**不可恢复**；回滚到 2.10.2 现在仍有 FPK 可用。

### 8.4 F6 Docker 悬空镜像 —— 已清理（保留构建缓存）

- 删除 4 个 `fnmusic-sources` 悬空镜像：`b36effe0`（指纹 82d75107）、`1fbf38aa`（dc712048）、`ba948fa4`（2c4fbdcb）、`39153d82`（2026-10-11 旧构建），共约 5.28 GB。
- `docker system df`：Images 10.94 GB→9.136 GB；可回收 7.347 GB→4.34 GB。
- **刻意保留** Build Cache（2.575 GB）与 `fnmusic-sources:latest`、`fnmusic-sources:rollback-20261101` 镜像——前者用于下次 FPK 升级时命中 apt/pip 层（NAS 外网弱），后者是 2.10.2 回滚镜像。
- 其余可回收层属其它应用（ani-rss/ubuntu 旧层），未动。

### 8.5 F3 开机竞态 —— 代码已修 + 设备已打补丁（未触发镜像重建）

- 仓库 `packaging/fpk/cmd/main`：`start` 分支在调用 `extend.sh` 前对有界等待 `/var/run/trim_music.socket` / `trim_music_upstream.socket`（默认 90s，可由 `FNMUSIC_START_SOCKET_WAIT_S` 覆盖），并写明确的 `[INFO]`/`[WARN]` 日志；超时仍交由 `extend.sh` 输出原诊断。
- 设备热补丁：`/var/apps/fnmusic-ext/cmd/main`（备份 `main.bak-20261108-pre-socketwait`，原文件与仓库 `ae7680d` 版本逐字节一致，`bash -n` 通过）。该路径在 App Center 应用包目录、**不在** Dockerfile COPY 源内，不影响 `build_fingerprint`，不会触发镜像重建。
- 未改动设备上的 `repo/proxy/*.py`、`repo/webui-service/*.py`：这些是 Dockerfile COPY 源，直接改会让构建指纹漂移、下次 `extend.sh` 触发镜像重建（弱网下风险高）；对应修复随下个 FPK 生效。

### 8.6 F2 `lastFailure.at` 截断 + F1 预防性校验 —— 代码已修 + 测试

- `proxy/llm_usage.py`：新增 `_MAX_TS=4_102_444_800` 与 `_safe_epoch()`，`_safe_failure()` 的时间戳不再套用 `_MAX_INT(1e9)`。
- `webui-service/app.py::validate_updates()`：当本次保存涉及 `FNMUSIC_LLM_BASE_URL`/`FNMUSIC_LLM_PROTOCOL` 时校验组合，拒绝 `responses + …/chat/completions` 与 `chat + …/responses` 两种错配，并给出可执行的修改建议。
- `FNMUSIC_COMPAT.json`：`music_versions` 增加 `1.1.19`，`checked_at=2026-11-08`，notes 记录路径级核对结论与新增端点直通说明。
- 测试（本地 venv，Python 3.14）：
  - `proxy/tests/test_llm_ledger.py` **19 passed**（含新增 `test_failure_epoch_timestamp_not_clamped`）
  - `proxy/tests/test_llm_protocols.py` **18 passed**
  - `webui-service/test_webui.py` **337 passed / 1 failed**，唯一失败是 `test_logs_export_excludes_symlinks_and_listing_matches`，原因 Windows 无符号链接权限（`WinError 1314`），与本次改动无关
  - `bash -n packaging/fpk/cmd/main` 通过
- 提交：`65ae09a`（代码+测试+compat）、`0ad120a`（本证据文档）、另见本次处理追加提交。

### 8.7 处理后的整体状态（只读复核）

| 项 | 结果 |
| --- | --- |
| 服务 | active/running，`NRestarts=0`，14:46:50 启动 |
| 容器 | running / healthy，`RestartCount=0` |
| healthz | `ok:true version:2.12.0-9 upstream:ok musicbox:ok llm:enabled` |
| WebUI | `:8774/` 200 / 48,890 B |
| 日志 | 服务 0 critical/traceback；容器近 50 行 0 error |
| 目录/资产 | P7 两目录存在；FPK×4 + SHA256SUMS + 新备份均在；`/vol1` 仍 57% |

## 9. 真实用户视角审查（2026-11-08 16:0x–16:4x JST，Computer Use 实际操作浏览器）

> 方式：用桌面/浏览器实际操作（Edge 已登录 fnOS 管理员 kurumi），从 fnOS 桌面打开「fnMusic 扩展管理」与「音乐」App，按普通用户路径操作。未输入任何密码（「音乐」App 用页面自带「使用 NAS 登录」按钮完成登录）。截图存 `docs/evidence/20261108/screenshots/`。

### 9.1 证明「可用」的部分（实操作验证）

| 用户路径 | 结果 |
| --- | --- |
| 桌面打开「fnMusic 扩展管理」 | 概览正常：当前音源「网易云音乐盒子」、musicbox RUNNING、WebUI RUNNING、服务健康（musicbox 正常） |
| 「大模型」页 | 显示本次修复后的配置：Base URL `https://llm.tantantan.tech/v1`、模型 `deepseek-v4.1-flash`、协议「Chat Completions（/chat/completions）」、Key 掩码显示；用量面板明确标注「最近失败: timeout」与更新时间（截图 `mgmt-llm.png`） |
| 「保存到 NAS」→ 搜索「晴天」→ 保存第 1 首 | **成功**。提示「音频已保存；实际档位 jymaster·flac·113.3 MB·…\晴天（原唱 周杰伦）.flac…封面已嵌入·歌词已保存为同名 LRC·歌手标签已写入·词：未提供/曲：未提供」；设备核实：FLAC 119,785,613 B、24bit/192kHz、270.7 s + LRC 2,551 B，staging 目录为空（截图 `manual-save-success.png`） |
| 音乐 App（官方 1.1.19） | 「使用 NAS 登录」可用；侧边栏出现扩展注入的「每日推荐 11-08 / 猜你喜欢 / 热门推荐 / [网易]各榜单」；每日推荐可打开并列出 20 首可播放曲目；**搜索「晴天」返回 89 首（含 `[music box]` 在线源）并可实际播放**，播放器进度/封面正常，歌词页有同步歌词（截图 `music-search-results.png`） |
| App Center 状态 | 恢复为 `isOpen:true / running / 2.12.0-9`，桌面图标重新出现 |

结论：**本次修复（LLM 配置、P7 目录、回滚资产、开机等待、兼容基线）在真实用户路径上成立**；单曲手动保存链路已端到端打通。

### 9.2 真实用户视角发现的问题（新，按影响排序）

| # | 问题 | 证据 | 建议 |
| --- | --- | --- | --- |
| U1（高） | **首次搜索新关键词很慢且没有加载态**：冷缓存 `/music/api/v1/search/track` 实测 **16.0 s**，期间搜索页显示「空空如也」，用户会以为搜索坏了；同一关键词第二次（缓存命中）立即出结果 | proxy.log `ms=16034`；两次搜索截图 | 前端加骨架/加载提示；后端调小 `FNMUSIC_SEARCH_DEEP_MAX_PAGES` / `FNMUSIC_SEARCH_TIMEOUT=15`，或先返回首屏再补全 |
| U2（中） | **用 `systemctl restart` 启停会让 App Center 记 `APP_CRASH` 并摘掉桌面入口**：15:12:19 记录 APP_CRASH 后 `isOpen:false`、桌面「fnMusic 扩展管理」消失；用 App Center 正常 start 后恢复（已恢复并核实） | TRIMEVENT APP_CRASH；fnos-cli status 前后对比 | 运维约定：只用 App Center / `cmd/main start` 启停；重启后如发现入口消失，执行 `fnos-cli app start fnmusic-ext --yes` 修复 |
| U3（中，偶发） | 管理页曾显示「无法读取手动保存状态（HTTP 501），请确认正通过飞牛桌面打开管理台」，重启服务后自动恢复，未能稳定复现 | 16:0x 截图（501 首屏） | 管理页对 501 显示「服务正在启动，请刷新重试」并自动重试；`webui_gateway.py` 目前 stdout→/dev/null 无日志，建议加持久日志便于定位 |
| U4（低） | 「关于」页有红色 NEW 徽标，但 App Center `upgrade=false`（无可用升级入口）；管理页版本 `v2.12.0` 与 App Center `2.12.0-9` 不一致 | 关于页截图 | 版本比较逻辑区分「自编译包」；无可用更新时不显示 NEW |
| U5（低） | 手动保存写出的文件为 `root:root 0600`：普通用户（kurumi）在文件管理器中能看到目录但**打不开/下载不了**；如目标改为正式曲库，用户后续管理受限 | `ls -l` 目标目录 | 按目标目录属主 chown（或说明仅音乐 App 可读） |
| U6（低） | 保存结果提示「词：未提供 / 曲：未提供」，未写入作词/作曲标签 | 保存成功提示 | 元数据补全（可选） |
| U7（低） | 整歌单保存默认关闭，页面提示「请在 .env 设置 FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=true 后重启代理」——开发者口吻 | 保存页截图 | 在管理页提供开关，或文案改为面向用户 |
| U8（低） | 搜索「晴天」首条是翻唱（RyaVocal）而非周杰伦原唱；结果标题统一带 `[music box]` 前缀（来源标识对普通用户是噪声） | 搜索截图 | 在线结果排序（原唱/热度优先）；前缀可做成小徽标 |
| U9（观察） | 播放时用户自己的下载管理器扩展弹出「MP3 File 113.33 MB」下载气泡（拦截媒体流），非项目缺陷，但可能干扰播放体验 | 音乐页顶部气泡 | 文档提示：勿让下载类扩展接管 `/music/api/v1/track/stream` |

### 9.3 操作副作用与回滚说明

- 「音乐」App 登录态：仅用「使用 NAS 登录」，未输入密码；如不需要可退出登录。
- 手动保存测试：在隔离目录 `/vol1/1000/fnmusic-test-20261031` 新增 1 个 FLAC + 1 个 LRC（`RyaVocal - 晴天 (原唱 周杰伦)`），未触碰正式曲库；如需清理是普通文件删除。
- 播放器已在测试后暂停。
- App Center 应用状态已用正常 `start` 恢复；`.env` 与备份对比：**只多出** `FNMUSIC_LLM_TIMEOUT_S`、`FNMUSIC_RECOMMEND_BUDGET_S` 两个键，无键丢失。



## 10. 2026-11-08 17:0x–18:1x JST 「尽数修复」记录（对应 §9.2 的 U1–U9）

### 10.1 修复与验证矩阵

| # | 修复内容 | 代码位置 | 设备侧验证 |
| --- | --- | --- | --- |
| U1 | 搜索首屏独立预算 `search_first_screen_s`（默认 8s）+ 深页补齐转后台；`search_deep_wait_s`（默认 3s）；`search_deep_max_pages` 默认 10→3 | `proxy/app.py`（`_search_first_screen_budget_s` / `_schedule_deep_fill` / `_fill_deep_pages`） | 新配置已加载（proxy.log `search_first_screen_s = 8.0`、`search_deep_wait_s = 3.0`）；`.env` 的 `FNMUSIC_SEARCH_DEEP_MAX_PAGES` 已改 3 并热重载（`16:53:02 [cfg] search_deep_max_pages=3`）。mock 回归：首查 16.06s → 5.05s，同词二次 0.003s；真实首查耗时待用户下次搜索观察 |
| U2 | 运维文档：只用 App Center / `cmd/main` 启停；入口丢失的恢复命令 | `docs/INSTALL.md` §9.1 | 本次热更新改走 App Center `stop`/`start`（无 APP_CRASH）；`isOpen:true/running` |
| U3 | 网关持久日志 `/var/log/apps/fnmusic-ext-gateway.log`（启动/分发/转发异常/异常/权限拒绝，>5MB 轮转）；管理页 501/503 自动重试 2 次 + 「扩展服务正在启动」文案 | `proxy/webui_gateway.py`、`webui-service/static/app.js` | 日志已产生：`gateway-start ...`、`dispatch manual-save op=track ...`（两次保存请求） |
| U4 | 完整版本 `v2.12.0-9`；只有「官方渠道 + 上游发行包 + 上游版本更高」才提示更新；本地构建显示「本地构建」徽标、隐藏 NEW | `webui-service/app.py`、`static/app.js` | `/api/about.version=v2.12.0-9`；`/api/update-check`：`build_kind=local、has_update=false、latest=2.12.3`；静态 app.js 已含「本地构建」 |
| U5 | 发布后 best-effort `fchown`(目标目录属主/属组)+`fchmod(0640)`，FLAC/LRC/整歌单同引擎全覆盖；`ownership`/`lyric_ownership` 返回 | `proxy/manual_save_anonymous.py`、`manual_save_posix.py`、`manual_save_route.py` | 实测保存 `Lie - 稻香.mp3/.lrc` → `-rw-r----- kurumi Users`，响应 `ownership=tagged、lyric_ownership=tagged` |
| U6 | 管理页词曲文案改为「词/曲：上游未提供」；后端从歌词首部解析词曲（/info 显式值优先）并写标签，新增 `credits_source` | `proxy/manual_save_route.py`、`static/app.js` | 实测该曲 `credits_source=upstream_unavailable`（该曲 LRC 开头无词曲行，属上游确实未提供）；写标签路径已就绪 |
| U7 | `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED` 进 SCHEMA（bool/group storage），页面注入开关并即时生效；文案去 .env 口吻 | `webui-service/app.py`、`static/app.js` | `/api/config` schema 已返回该键（value=false）；node 测试 199/199 |
| U8 | 在线结果按标题相关度稳定排序（完全相等→前缀→包含→其它；同级保持上游原序）；保留 `[music box]` 来源前缀 | `proxy/app.py`（`sort_online_items_for_keyword`） | 单元测试覆盖；真机排序效果待用户下次搜索观察（带「(原唱 …)」后缀的翻唱会排在标题完全相等者之后） |
| U9 | 运维文档提示下载管理器拦截媒体流 | `docs/INSTALL.md` §9.3 | 文档 |

### 10.2 部署与回滚

- 主机侧：`proxy/{app.py,webui_gateway.py,manual_save_anonymous.py,manual_save_posix.py,manual_save_route.py}` 已覆盖到设备 repo（备份 `/vol1/1000/fnmusic-hotfix-20261108-2300/host/`），`py_compile` 通过。
- 容器侧：`/srv/webui-service/app.py` 与 `static/app.js` 经 `docker cp` 更新并 `supervisorctl restart webui`（备份同目录 `container/`）。设备 repo 的 `webui-service/*` **未改动**（避免触发镜像 fingerprint 重建）；容器重建/升级前该补丁有效，下个 FPK 会正式包含这些修复。
- 重启方式：App Center `stop` → `start`（无 APP_CRASH，入口正常）；容器与补丁经重启后仍在（`md5sum` 一致）。
- 回滚：恢复备份文件 + App Center `stop/start`；`.env` 备份 `.env.bak.20261108-1755`。

### 10.3 测试结果（本次修复后）

- `proxy` 定向套件：809 passed, 7 skipped, 2 failed（均为 Windows 缺 `os.O_DIRECTORY` 的环境失败，非本次改动）。
- `webui-service`：350 passed, 1 failed（Windows 符号链接权限环境问题）；`node test_app_js.js` 199/199。
- 网关新增测试：日志落盘/自动建目录/不可写路径不抛异常/查询串不入日志。
- 设备实测：手动保存属主与 0640 正确、网关日志落盘、`/api/about` 与 `/api/update-check` 行为符合预期、SCHEMA 新键生效、搜索新配置热重载生效。

## 11. 2026-11-09 23:1x–24:0x JST 真实用户复验（完成「以一个真实用户的视角完成审查」）

> 方式：从飞牛桌面打开「fnMusic 扩展管理」，按普通用户路径逐页点击；遇到异常时用 Edge DevTools 控制台读取真实 HTTP 状态（普通用户也可看到控制台报错）。截图见 `docs/evidence/20261108/screenshots/`（`about-local-build.png`、`save-nas-toggle.png`、`save-success-credits.png`、`transient-save-failure.png`、`devtools-501-evidence.png`）。

### 11.1 ✅ 已修复项的真实界面复验（通过）

| 项 | 界面证据 | 结论 |
| --- | --- | --- |
| U4 版本/NEW | 侧边栏与关于页均显示 **v2.12.0-9**；关于页徽标为「v2.12.0-9 / Docker 部署 / **本地构建**」，无 NEW、导航无红点 | 通过 |
| U7 整歌单开关 | 「保存到 NAS → 手动保存歌单」出现「整歌单保存」开关（未勾选），文案：「整歌单保存未开启，可在本页上方的开关开启（保存后立即生效，无需重启代理）」 | 通过 |
| U6 词曲文案 | 保存成功提示：`…封面已嵌入·歌词已保存为同名 LRC·歌手标签已写入·词/曲：上游未提供`（不再出现两个「未提供」） | 通过 |
| U5 属主/权限 | 界面保存 3 首均成功；设备核实全部为 `-rw-r----- kurumi Users`（0640） | 通过（3 次） |
| 冲突语义 | 对已存在文件再次点保存 → 提示「目标目录已存在同名文件，未覆盖。」，不覆盖原文件 | 通过 |
| U2 App Center | 桌面入口存在；App Center stop/start 后 `isOpen:true / running`，无 APP_CRASH，桌面图标正常 | 通过 |
| U3 网关日志 | `/var/log/apps/fnmusic-ext-gateway.log` 记录 `gateway-start`、`dispatch manual-save op=track/search/status/playlist`，并新增 `manual-save track result code=conflict` 结果码 | 通过（可定位） |

### 11.2 ⚠️ 新发现（真实用户可见）

**N1（高）重启扩展后，管理页的「首次特权请求」可能失败且提示误导**
- 现象：App Center 重启扩展后进入管理页，「保存」或「搜索」第一次点按可能失败：保存显示「保存失败：内部错误」，搜索显示「搜索失败：服务不可用」；重试一次或刷新页面即恢复。
- DevTools 证据（`devtools-501-evidence.png`）：这些请求真实返回 **HTTP 501**（`/app/fnmusic-ext/api/manual-save/track`）、501（`/api/search?…`）、403（`/api/playlist/playlists`）、503（`/api/platforms`）——501 是 WebUI 直连占位响应，说明请求**没有走桌面网关**，而是落到了 WebUI 服务。
- 网关日志同步证据：这些失败请求**没有** `dispatch manual-save …` 行（未到网关）；而同一 URL 稍后从页面控制台手发 fetch 得到 501→改回网关后的 409/200 正常结果。
- 根因方向：fnOS 应用中心重启期间 `trim_http_cgi` 先 `upstream remove`（22:39:09）再 `upstream register`（22:39:46），浏览器到该前缀的持久连接/上游状态在切换窗口内可能落到 WebUI 直连路由；窗口过后恢复。
- 建议：① 前端把现有 501/503 自动重试从「状态接口」扩展到保存/搜索/歌单接口；② 失败文案改为「扩展服务正在重启/路由未就绪，请重试或刷新页面」；③ 文档提示「重启扩展后刷新一次管理页」。
- 现状：已用旧版前端复现，属**用户可感知但可自愈**的问题，建议下个补丁修（本次未改，避免再动容器侧）。

**N2（低）失败行状态残留**：保存失败的行会保留「失败」标记，且没有「重试」字样，用户需再点一次「保存」才能重试（可接受，但可改进）。

**N3（运维）`fnos-cli app start` 客户端超时**：连续两次出现 `http2: timeout awaiting response headers`，但应用实际已启动（`isOpen:true`）。自动化应把该超时当「结果未知 → 轮询状态」，不要直接判失败。

### 11.3 本轮代码改动（诊断能力，已热更设备）

- `proxy/webui_gateway.py`：新增 `manual-save worker-exception class=…`、`manual-save track result code=…`、`engine-status-unavailable` 固定 code 日志（不含异常文本/请求数据），用于定位偶发失败。
- `proxy/manual_save_route.py`：复核后确认 `sys` 原本已导入（先前怀疑的 NameError 不成立）；保留「属主告警失败绝不影响保存」的健壮性测试 `test_ownership_failure_log_never_raises_and_is_fixed_code`，并去掉一处重复 import。
- 设备热更：两份文件已覆盖并 App Center stop/start 重启（`isOpen:true`，网关新 pid 682042）；日志已验证输出结果码。

## 12. 2026-11-11 21:0x–22:2x JST 浏览器真实用户判断（音乐 App 实测）

> 方式：在已登录的飞牛桌面里打开「音乐」App，按普通用户操作搜索/播放/看每日推荐；异常时用 DevTools/服务端日志取证。截图：`daily-llm-playlist.png`、`search-daoxiang-3s.png`、`music-home-library.png`、`play-daoxiang.png`。

### 12.1 实测结论（通过）

| 用户路径 | 实测结果 |
| --- | --- |
| 搜索新关键词「稻香」（冷缓存） | **3.2 秒**返回 39 首（proxy.log `ms=3246`），修复前同场景 16.0s；官方 App 搜索页正常展示，无「空空如也」 |
| 在线播放 | 点第 1 首 `[music box] 稻香 - Lie` 成功播放（播放器进度/暂停正常），顶部下载管理器提示 MP3 8.34MB（已知 N-U9） |
| 保存到 NAS 的曲目入库 | 音乐 App「最近播放/最近添加」出现 `稻香 - Lie`、`晴天 - RyaVocal`（手工保存的文件被官方曲库扫描入库） |
| 每日推荐（LLM） | **tiers=llm**：20 首全部为在线曲目、封面完整（イナンナの見た夢、光るなら、secret base、告白气球、光辉岁月、Blinding Lights、STAY、群青…），不再是本地随机兜底 |

### 12.2 本轮发现并修复的真实问题（LLM 每日推荐一直降级）

- 现象：11-08/11-11 两次构建日志均为 `daily recommend tier llm llm failure timeout` → `tiers=local-random`，用户看到的「每日推荐」其实是本地随机。
- 根因链：
  1. `proxy/recommend.py` 把 `FNMUSIC_LLM_TIMEOUT_S` 硬夹在 **60s**（`_env_float(..., 5.0, 60.0)`），.env 调到 120 无效；
  2. 配置的 `deepseek-v4.1-flash` 是**推理模型**：真实推荐 prompt 实测 53s（1615 tokens→4966 completion，其中 reasoning 3244），稍大即超 60s；
  3. 改大 `max_tokens` 后请求变慢再次超时；最终换非推理模型 `gpt-4.1-mini`（同 prompt 实测 **20s**、JSON 合法 20 首）。
- 修复：代码上限 60→180（`recommend.py` + 新增 `proxy/tests/test_recommend_timeout.py`）；设备 `.env`：`FNMUSIC_LLM_MODEL='gpt-4.1-mini'`、`FNMUSIC_LLM_TIMEOUT_S='120'`、`FNMUSIC_RECOMMEND_BUDGET_S='180'`、`FNMUSIC_LLM_MAX_TOKENS='16384'`（均热重载生效，备份 `.env.bak.20261111-*`）。
- 验证：21:18:05 日志 `tracks=20 status=ready tiers=llm`；界面 20 首在线推荐（见截图）。

### 12.3 仍存在的真实用户问题

- **N1（高，已再次复现）**：App Center 重启扩展后，浏览器里已打开/新打开的页面在 30–60s 内请求可能不达扩展（页面白屏/空空如也/提示「内部错误」），刷新一次或等 re-register 完成即恢复。建议：前端对 501/502/503 自动重试 + 「重启后请刷新页面」提示；文档补充。
- N2（低）：失败行残留「失败」标记，无「重试」文案。
- N3（运维）：`fnos-cli app start` 客户端 http2 超时但实际启动成功（本轮 3 次），自动化应轮询状态而非判失败。
- LLM 推荐质量依赖所选模型：默认 `deepseek-v4.1-flash` 推理模型不适配本任务；建议把「推荐模型」默认值改为非推理模型或增加「推理模型需要更长超时」的说明/校验。

### 12.4 用户裁定：禁止使用 gpt-4.1-mini（2026-11-11 22:3x）

- 用户明确「不允许使用」`gpt-4.1-mini`。已立即回滚：`FNMUSIC_LLM_MODEL` 恢复为 `deepseek-v4.1-flash`（热重载日志 21:27:58 确认），该模型生成的推荐缓存已被配置变更清除，**后续不得再调用该模型**。
- 代价（用户已知情）：`deepseek-v4.1-flash` 为推理模型，真实推荐 prompt 单次 53s 起、更大 prompt 仍会超时；21:30 猜你喜欢构建仍记录 `lastFailure: timeout`，每日推荐/猜你喜欢将回退本地/榜单兜底。
- 保留内容：`recommend.py` 超时上限 60→180s 的代码修复（与模型无关，属通用健壮性）；`.env` 的 `LLM_TIMEOUT_S=120`、`RECOMMEND_BUDGET_S=180`、`MAX_TOKENS=16384` 保留。
- 如需 LLM 推荐真正可用，须由用户指定「允许使用的非推理模型」（同一中转下可选 gemini/claude 等）或明确关闭 LLM 推荐；在获得授权前不再更换模型。
