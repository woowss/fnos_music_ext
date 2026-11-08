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

