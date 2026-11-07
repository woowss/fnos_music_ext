# 整歌单保存 · 部署检查单（P7 前的部署窗口）

> 状态：**检查单（离线准备，尚未执行）**。部署会改变用户 NAS 上的应用，**必须先获得用户
> 明确同意并约定窗口**。本文件不授权任何部署动作。
> 关联：合同 [`docs/MANUAL_SAVE_PLAYLIST_JOB.md`](MANUAL_SAVE_PLAYLIST_JOB.md) §12、P7 运行手册
> [`docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md`](MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md)、
> 交接 `E:\fnmusic\PROJECT_HANDOFF_20261101.md` §6。

## 0. 为什么需要部署

NAS 现装 **2.10.2**（`f5fe383` 时代的代码），**不包含**本轮 P1/P2/P4/P5/P6 的整歌单保存代码
（账本 reason 扩展、快照 producer 端点、适配层、网关 6 端点、管理页歌单入口）。
P3 只读观察可以用临时目录规避部署，但 **P7 真实验收必须在部署后进行**。

## 1. 部署前必须由用户确认的事项

- [ ] 同意本次部署与窗口时间（服务重启会让音乐 API 短暂中断）。
- [ ] 同意临时把 `.env` 补三个键（**操作者手动编辑**；`.env*` 在 DSH 工具层属凭据围栏，脚本不可读写）：
  ```dotenv
  FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false   # 先保持关闭，验收时再按 A10 流程开
  FNMUSIC_MANUAL_SAVE_JOBS_DB=<私有可写目录下的 sqlite 路径，root 可写>
  FNMUSIC_PLAYLIST_MANIFEST_READONLY=false     # P3 只读观察时临时置 true
  ```
- [ ] 确认目标/暂存目录沿用已验收配置（`/vol1/1000/fnmusic-test-20261031`、`/vol1/1000/fnmusic-manual-save-staging`）。
- [ ] 确认已有可回滚物：上一版 FPK（2.10.2 升级包/2.8.2 回滚包）与升级前备份 tarball（交接 §6.1）。

## 2. 待打包的代码范围（相对 `f5fe383` 之后的 6 个提交）

| 提交 | 内容 | 涉及文件 |
| --- | --- | --- |
| `15d7a42` | 失败项消息固定化 + 账本崩溃对账测试 | `proxy/manual_save_playlist.py`、测试、文档 |
| `fb1960d` | 合同 | `docs/MANUAL_SAVE_PLAYLIST_JOB.md` |
| `0d43c63` | 账本 reason 扩展、快照 producer、适配层 | `proxy/manual_save_jobs.py`、`musicbox-service/netease_playlist_snapshot.py`、`musicbox-service/app.py`、`musicbox-service/requirements.txt`、`proxy/manual_save_playlist_jobs.py`、测试 |
| `dd45943` | 网关 6 端点 | `proxy/webui_gateway.py`、`proxy/tests/test_manual_save_playlist_gateway.py` |
| `52c1e6d` | 管理页入口 + container 导出键 | `webui-service/static/{index.html,app.js}`、`webui-service/test_app_js.js`、`container/env_flag.sh`、`container/test_container.py` |
| `d4051b6` | P3 只读观察工具与证据模板 | `tools/netease_playlist_observe.py`、`offline_tests/test_netease_playlist_observe.py`、`docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md` |
| `345ef5a` | P7 验收运行手册 | `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` |

**明确不打包**：3 个受保护未跟踪文件（`proxy/manual_save_verified_pipeline.py`、
`offline_tests/test_manual_save_verified_pipeline.py`、`docs/OFFLINE_VERIFIED_PIPELINE.md`）
与全部 `*probe*` 脚本/测试（它们保持未跟踪、不进入任何包）。

## 3. 构建

构建方式沿用上一轮已成功的方式（交接 §6.1：NAS 上离线构建 `fnpack 1.2.4`，使用设备现有
lxserver artifact；ARM64 ensure 步骤做构建期 stub，x86_64 安装不受影响）。

- [ ] 记录 `git rev-parse HEAD` 与新包 SHA-256。
- [ ] 版本号递增（`2.10.2` → 建议 `2.11.0`，因新增 API/UI；**由用户确认版本号**）。
- [ ] 校验包内**不含** `.env`、Cookie、jobs DB、日志、`__pycache__`、probe 文件。
- [ ] **容器镜像风险**：`musicbox-service/requirements.txt` 已从 `NetEase-MusicBox>=0.5.3`
      钉为 `==0.5.3`。若镜像在安装时按 requirements 构建，需确认镜像可成功重建且版本为 0.5.3；
      若现场不便重建镜像，可临时保持旧镜像但**必须**在 P3 观察里实测容器内版本（§4.7 步骤 1）
      并按合同 §4.1 记录 deviation。

## 4. 安装与即时验证（G3）

1. 应用中心 → 手动安装 → 选新 FPK → 升级/覆盖 → 等待完成。
2. 验证：
   ```bash
   ssh kotomi-root 'systemctl is-active fnmusic-ext.service; cat /vol1/@appcenter/fnmusic-ext/repo/VERSION'
   ssh kotomi-root 'curl -s http://127.0.0.1:8774/healthz'
   ssh kotomi-root 'stat -c "%a %U:%G %n" /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock'   # 期望 600 root:root
   ssh kotomi-root 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8774/api/manual-save/playlist/playlists'  # 期望 501
   ssh kotomi-root 'curl -s --unix-socket /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock -H "x-trim-userid: 1000" -H "x-trim-isadmin: true" -H "x-trim-username: kurumi" -H "x-fnmusic-save: 1" http://127.0.0.1/app/fnmusic-ext/api/manual-save/status'
   ```
3. **默认关闭**验证：整歌单端点应返回 403 `playlist_disabled`（或总开关关时 `disabled`），
   且无账本文件被创建（`FNMUSIC_MANUAL_SAVE_JOBS_DB` 路径不存在或 mtime 不变）。
4. 检查容器内 `printenv FNMUSIC_PLAYLIST_MANIFEST_READONLY`（应为空/未设置 = 关闭）与
   `python3 -c "import importlib.metadata as m; print(m.version('NetEase-MusicBox'))"`。

## 5. 部署后顺序（不要跳步）

```
部署 → G3 即时验证（本节）
     → P3 只读观察（临时置 FNMUSIC_PLAYLIST_MANIFEST_READONLY=true，按证据模板记录，完成后置回 false）
     → P3 通过后，用户同意写库
     → 开 FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=true + 配置 JOBS_DB → 重启
     → P7 按验收运行手册执行（测试歌单 + 重复位置 + 失败位置）
```

## 6. 回滚

- 应用回滚：安装上一版 FPK（2.10.2 升级包）。
- 配置回滚：恢复 `.env` 备份（记录备份文件名与时间）。
- 账本回滚：**不删除** jobs DB；如必须重建，先复制备份并由用户确认（账本是审计证据）。
- 已保存媒体：**不自动删除**；按 job `positions[]` 显式 basename 与用户确认后处理。

## 7. 不证明

本检查单不证明：构建可复现、镜像可重建、安装成功、G0 真机链路、音频有效性或用户可播放；
这些必须在实际部署窗口内逐项记录。
