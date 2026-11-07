# 整歌单保存 · P3 只读观察证据（模板）

> 状态：**模板，尚未执行**。本文件在用户授权并完成一次只读观察后才成为证据。
> 合同：`FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01` §4.7；合同正本见
> [`docs/MANUAL_SAVE_PLAYLIST_JOB.md`](MANUAL_SAVE_PLAYLIST_JOB.md)。
> 观察工具：[`tools/netease_playlist_observe.py`](../tools/netease_playlist_observe.py)。

## 0. 执行前提（缺一不可）

- [ ] 用户**明确授权**本次只读观察，并给出维护窗口。
- [ ] `FNMUSIC_MANUAL_SAVE_ENABLED` 与 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED` **均为关闭**。脚本只检查**进程环境**中这两个键（容器内保存开关通常只写在 `/repo/.env`，不一定导出到进程），因此**操作者必须另行确认 `/repo/.env` 两键为 false**；脚本不读取 `.env` 内容。
- [ ] 本轮**不部署新 FPK、不改 `.env`、不重启 `fnmusic-ext.service`**；只读开关通过进程内环境变量打开。
- [ ] 只对**一张测试歌单**执行 GET 读取；不触发任何保存/下载/转码。
- [ ] 观察结束清理容器内临时目录（`/tmp/p3-*`），不触碰 NAS 目标/暂存目录与曲库。

## 1. 观察方法（不部署）

仓库新代码**不在 NAS 已安装的 2.10.2 里**，因此观察采用"临时目录 + 只读导入"：

1. 宿主侧把三个受审模块放到临时目录（仓库文件本身不上传覆盖任何部署路径）：
   ```
   musicbox-service/netease_playlist_snapshot.py
   musicbox-service/playlist_manifest.py
   musicbox-service/netease_ext.py        # 仅用于与本机部署版本比对哈希
   tools/netease_playlist_observe.py
   ```
2. `docker cp` 到容器 `/tmp/p3-observe/`（容器内临时目录，可丢弃）。
3. 用容器内 Python 运行（进程内设 `FNMUSIC_PLAYLIST_MANIFEST_READONLY=1`；**不写 .env**）：
   ```
   docker exec -e HOME=/root fnmusic-sources \
     python3 /tmp/p3-observe/tools/netease_playlist_observe.py \
       --module-dir /tmp/p3-observe/musicbox-service \
       --playlist-id <测试歌单ID> \
       --cookie-path <容器内 Cookie 文件路径，只 stat> \
       --target-dir <NAS 目标目录，只比较目录元数据> \
       --staging-dir <NAS 暂存目录，只比较目录元数据> \
       --musicbox-log <musicbox 访问日志路径，只统计禁止路径次数> \
       --confirm-observe \
       --report /tmp/p3-observe/observation.json
   ```
4. 取回报告：`docker cp fnmusic-sources:/tmp/p3-observe/observation.json ./p3-observation.json`。
5. 清理：`docker exec fnmusic-sources rm -rf /tmp/p3-observe`；宿主删除临时目录。

**部署版本与仓库版本差异必须记录**：若 `/repo/musicbox-service/` 下的
`netease_ext.py` / `playlist_manifest.py` 哈希与仓库 HEAD 不同，证据里要写明
"观察使用仓库 HEAD 模块 + 部署容器运行时"，不得声称与部署代码完全一致。

## 2. 执行记录（执行后填写）

- 执行人 / 日期（UTC+9）：
- 维护窗口：
- 机器 / 容器 / Python：
- `git rev-parse HEAD`：
- 观察工具 sha256：

## 3. 步骤 1：冻结 artifact（脚本 `artifact` 段）

| 项 | 值 |
| --- | --- |
| `git_head` | |
| `netease_playlist_snapshot.py` sha256 | |
| `playlist_manifest.py` sha256 | |
| `netease_ext.py` sha256 | |
| `requirements.txt` 中 NetEase-MusicBox 行 | |
| `requirements.txt` sha256 | |
| 容器内 `importlib.metadata.version("NetEase-MusicBox")` | |
| `dist_info_present` | |
| `pinned_version` / `wheel_sha256` | |
| 部署路径下同名文件哈希（若不同） | |

## 4. 步骤 2/4：两次独立读取（脚本 `reads` 段）

| 项 | 第 1 次 | 第 2 次 |
| --- | --- | --- |
| `status` | | |
| `error_code` / `artifact_error_code` | | |
| `track_count` / `position_count` | | |
| `enumeration_complete` | | |
| `source_fingerprint` | | |
| `source_versions` | | |
| `elapsed_seconds` | | |

**判据**：两次 `source_fingerprint` 一致；`track_count == position_count`；两次均 `status=="complete"`。

## 5. 步骤 5：副作用观察（脚本 `side_effects` 段）

| 观察项 | 期望 | 实测 |
| --- | --- | --- |
| Cookie 文件 `size/mtime_ns/mode` 是否变化（**不读内容**） | 可能变化（EAPI 写回 NMTID），只记录类型 | |
| musicbox 日志中 `/api/song/enhance/player/url` **增量**次数 | 0 | |
| musicbox 日志中 `/weapi/v3/song/detail` **增量**次数 | 0 | |
| musicbox 日志中 `/lyric` **增量**次数 | 0 | |
| 目标目录元数据是否变化 | 否 | |
| 暂存目录元数据是否变化 | 否 | |
| 目标/暂存目录摘要是否被截断（`truncated`） | 否；若为是则判 `dir_digest_truncated`，**不得判"未写入"**，需 `find -newer` 人工复核 | |
| 保存开关 guard 作用域 | 进程环境检查 + 操作者确认 `.env` 两键为 false | |
| 日志统计窗口 | 观察前后各计一次、取**增量**（不是整段历史） | |
| 服务/容器是否重启、崩溃或 5xx | 否 | |

## 6. 步骤 6：绑定摘要（供实现引用）

```
HEAD:                 <填写>
wheel sha256:         7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12
实测 SDK 版本:        <填写>
模块 sha256:          <填写 3 个>
观察日期（UTC+9）:     <填写>
测试 playlist_id:     <填写或"脱敏占位">
fingerprint 样本:     <填写前 16 位即可>
每次读取耗时:         <填写>
```

## 7. 结论

- [ ] **通过**：artifact 版本/摘要符合 §4.1；两次 fingerprint 一致；`track_count == position_count`；
      无音频 URL / 歌曲详情 / 歌词请求；无文件写入；无错误日志。
- [ ] **不通过 / 停止接线**：逐条列出 `stop_reasons`，回退到"选择/复核 artifact"。

签名（Lead）：

## 8. 不证明（必须原样保留）

provider 成员列表的完整性/实时性/授权、Cookie 未被服务端轮换、NAS 写入能力、音频有效性。
