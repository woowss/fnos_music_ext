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
