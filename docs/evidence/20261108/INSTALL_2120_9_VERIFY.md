# 2.12.0-9 安装与验收证据（2026-11-08 01:2x CST）

设备：fnOS「kotomi」192.168.0.106:11026（SSH root 只读核验）
产物：`fnmusic-ext-2.12.0-9.fpk`，sha256 `35af6788e3f6915a887e3cdef482c2df8bf19238e52db4fbc05c2e1d179be538`
来源提交：`codex/fnmusic-2.12-baseline` @ `e52674f`（Dockerfile 补 netease_playlist_snapshot / playlist_manifest；VERSION→2.12.0-9）

## 1. 安装结果

| 检查项 | 实测 | 判定 |
| --- | --- | --- |
| 应用目录 | `/vol1/@appcenter/fnmusic-ext`（repo 属主 kurumi:Administrators） | PASS |
| 版本 | `repo/VERSION` = `2.12.0-9` | PASS |
| systemd | `ActiveState=active SubState=running MainPID=579075 NRestarts=0` | PASS |
| 接管 | `ExecStartPost=takeover.py ready --timeout 65` → `status=0/SUCCESS`；安装日志「Unix socket 接管成功且健康探测通过」 | PASS |
| 容器 | `fnmusic-sources` `Up (healthy)`，镜像 `sha256:4490d2f6c600…` | PASS |
| 镜像指纹标签 | `98c4b6fdc1e9…`（= 安装目录内复算值，见 §4） | PASS |
| 容器日志 | 近 300 行无 error / traceback / ModuleNotFoundError | PASS |
| 服务日志 | 近 10 分钟无 error / traceback / exception | PASS |

## 2. 健康端点

| 端点 | 结果 |
| --- | --- |
| `curl --unix-socket /var/run/trim_music.socket http://localhost/_ext/healthz` | `ok:true version:2.12.0-9 musicbox:ok upstream:ok degraded:false failures:[]`（body md5 `5a106c182775f16001c22d676a8a29f3`） |
| `http://127.0.0.1:8770/healthz`（musicbox） | `{"status":"ok","source":"https://github.com/darknessomi/musicbox"}` |
| `http://127.0.0.1:8774/healthz`（WebUI） | `{"ok":true}`；首页 `HTTP 200`，48,890 B |
| 官方链路 | `upstream.http_status=401`（未登录态，属预期） |

## 3. 数据恢复

| 项 | 实测 |
| --- | --- |
| `repo/sources-data` | 596 K（netease cache/config、lxserver、样本目录） |
| `repo/online_favorites` | 8.0 K（1 个文件） |
| `repo/play_history` | 8.0 K（1 个文件） |
| `.env` | 3,705 B，合并完成；`FNMUSIC_VERSION=2.12.0-9`、`FNMUSIC_DEPLOY_MODE=docker` |
| 手动保存 | `FNMUSIC_MANUAL_SAVE_ENABLED=true`、`FNMUSIC_MANUAL_SAVE_TARGET=/vol1/1000/fnmusic-test-20261031`（隔离测试目录，非生产曲库）、`FNMUSIC_MANUAL_SAVE_STAGING` 保留 |
| 镜像源 | `FNMUSIC_PIP_INDEX` / `FNMUSIC_APT_MIRROR` 均保留为华为云（本次为过网络关而改，见交接 §12） |
| 备份 | `repo/.env.bak.20261107-2315`、`.env.bak.20261108001057` 均在；`/vol1/1000/fnmusic-backup-20261107-pre-212/` 未动 |

注：`/vol1/fnmusic-ext-data`（卸载保留目录）在本次安装被 adopt 消费后已不存在；`.env` 同时存在于 repo 与 `fnmusic-ext-backup-20261107184529.tar.gz` 内。

## 4. 指纹与「预构建复用」的偏差（新发现，需记入 runbook）

- 预构建在 `/vol1/1000/fnmusic-prebuild-20261107/repo` 算得 `82d75107…`，安装侧算得 `98c4b6fd…`，**指纹不一致 → 安装时重建镜像**（日志「应用镜像缺失或构建输入变化，重建」）。
- 根因：`proxy/build_fingerprint.py` 的 `path_entries()` 把 **每个 COPY 源的文件/目录 mode** 计入哈希；App Center 解包后目录 `0775`、部分文件 `0775`，而预构建目录为 `0755/0664`，**同一份内容因 mode 不同得出不同指纹**（本次实测 223 处 mode 差异，文件内容 md5 全部一致）。
- 影响与结论：
  1. 「预构建 → 指纹命中 → 零构建安装」在不同目录/不同 mode 下**不可靠**；
  2. 但预构建仍然有效：`RUN` 层缓存键不含构建上下文，apt/pip 层全部命中缓存，只有 4 个 COPY 层重跑 —— 本次安装全程约 5 分钟，未再联网构建；
  3. 因此预构建的正确定位是**预热层缓存**，不是「伪造指纹复用」。若真要做到指纹复用，必须在与安装完全相同的路径与 mode 下构建。

## 5. 待用户参与的验收项（未做）

| 项 | 为什么需要用户 | 建议动作 |
| --- | --- | --- |
| 音乐 App 内搜索/播放 | 接口需 fnOS 用户 token（未登录直连返回 `INVALID TOKEN`，属预期） | 打开飞牛桌面「音乐」→ 搜一首 → 播 10 秒 |
| P7 真实音频 / 手动保存验收 | 需用户指认测试曲目、允许写隔离库 | 按 `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` 在 `/vol1/1000/fnmusic-test-20261031` 执行 |
| §0.2 身份链路观测 | 需用户先登录 | 登录后采只读证据（socket peer 凭据 / header claims） |
| 网易云扫码登录 | 需用户扫码 | 管理页「fnMusic 扩展管理」→ 音乐源 → 扫码 |

## 6. 门禁状态（未因本次安装改变）

`§0.1 生命周期 BLOCKED`、`§0.2 身份 BLOCKED`、`P7 A4/A5/A6 BLOCKED/NOT RUN`、`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、`_POINT_IN_TIME_SCAN_VALIDATED=False`、`_INDEPENDENT_COPY_VIEW_VALIDATED=False`、`PIPELINE_INTEGRATION_ALLOWED=False`。
