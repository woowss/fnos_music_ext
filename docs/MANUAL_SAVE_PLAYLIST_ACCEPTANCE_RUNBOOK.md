# 整歌单保存 · P7 真实端到端验收运行手册

> 状态：**运行手册（离线准备完成，尚未执行）**。执行需要用户明确授权真实写库，并指定一张
> 含重复曲目、成员数 ≥5 的测试歌单。
> 合同：`FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01` §10；正本
> [`docs/MANUAL_SAVE_PLAYLIST_JOB.md`](MANUAL_SAVE_PLAYLIST_JOB.md)。
> 契约前置证据：[`docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md`](MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md)（P3）。

## 0. 红线（执行期间必须持续成立）

- **只在测试库写入**：目标 `/vol1/1000/fnmusic-test-20261031`、暂存 `/vol1/1000/fnmusic-manual-save-staging`（沿用已验收配置，不新增目录）。
- **不删除任何已保存文件**；若需清理，只能按本次 job 的 `positions[]` 显式列出的 basename 逐个处理，并由用户确认；**禁止按名字通配/递归清理**。
- **不改 `.env`、不部署、不重启服务**，除非该验收项本身要求（A10 需要临时关闭整歌单开关）且用户已就本次窗口明确同意；改前后都记录原值与备份路径。
- 不运行 `scripts/*probe*`；不触碰 3 个受保护未跟踪文件；不把签名 URL/Cookie/token/路径写进证据文档或日志。
- 用户账号（uid 1000）与 root 通道沿用交接 §9.1；root 仅用于只读检查与显式授权的开关切换。

## 1. 前置检查（只读，全部记录 exit code）

```bash
# 1.1 服务与版本
ssh kotomi-root 'systemctl is-active fnmusic-ext.service; cat /vol1/@appcenter/fnmusic-ext/repo/VERSION'
# 1.2 socket 模式与属主（G0）
ssh kotomi-root 'stat -c "%a %U:%G %n" /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock'
# 1.3 手动保存状态（既有单曲链路仍可用）
ssh kotomi-root 'curl -s --unix-socket /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock \
  -H "x-trim-userid: 1000" -H "x-trim-isadmin: true" -H "x-trim-username: kurumi" \
  -H "x-fnmusic-save: 1" \
  http://127.0.0.1/app/fnmusic-ext/api/manual-save/status'
# 1.4 部署状态与仓库差异（记录 HEAD 与部署版本）
ssh kotomi-root 'ls -l /vol1/@appcenter/fnmusic-ext/repo/manual_save_playlist* 2>/dev/null; sha256sum /vol1/@appcenter/fnmusic-ext/repo/proxy/manual_save_playlist_jobs.py 2>/dev/null'
```

**门槛**：1.1 `active` 且版本符合预期；1.2 `600 root:root`；1.3 `enabled:true` 且目标/暂存目录正确；
1.4 必须确认**部署侧是否已包含 P1/P2/P4/P5/P6 代码**——若未包含，先走部署流程（需用户同意），否则 P7 无法执行。

## 2. 用例与准备

| 项 | 要求 |
| --- | --- |
| 测试歌单 | **含至少 1 个重复曲目位置**、成员数 ≥5；由用户选定并确认 ID |
| 预期失败位置 | 至少 1 个可复现失败（VIP/无版权/无可用音质），用于 A6 |
| 记录 | 歌单 ID、成员数、重复位置数、快照指纹（脱敏后入库） |

浏览器用 DSH 浏览器（用户已登录的飞牛桌面）：打开 `https://192.168.0.106:11026` → 应用中心 → fnmusic-ext 管理台 → "保存到 NAS"页。

## 3. 逐项执行（A1–A16）

### A1 只读预检（两次指纹一致）
1. UI 点"刷新歌单"→ 选测试歌单 → 点"读取清单"。
2. 记录界面显示：`共 N 首（含 M 个重复位置）`。
3. 连续再读一次，断言指纹稳定（后端两次独立观测一致；界面不显示指纹时用 3.1 的 curl 直接拿）。
```bash
ssh kotomi-root 'curl -s --unix-socket /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock \
  -H "x-trim-userid: 1000" -H "x-trim-isadmin: true" -H "x-trim-username: kurumi" \
  -H "x-fnmusic-save: 1" \
  "http://127.0.0.1/app/fnmusic-ext/api/manual-save/playlist/snapshot?playlist_id=<ID>"'
```
**通过**：`ok:true`、`track_count==position_count`、`enumeration_complete:true`、两次 `source_fingerprint` 相同。

### A2 创建幂等
1. UI 点"开始保存"两次（或一次后刷新再点）。
2. **通过**：两次同一 `job_id`；账本容量计数不增加（`GET job` 的 `positions_total` 相同）。
3. 记录 `job_id`、`manifest_sha256`。

### A3 逐首推进
1. 观察 UI 的"已处理 x/N"单调递增；每次推进只处理一首。
2. 记录每次 advance 的 HTTP 状态与耗时（脱敏）。
**通过**：无并行推进；`x` 单调不减；`x` 达到 `N` 后停止轮询。

### A4 逐首核对（**核心**）
对每个 `published` 位置，在 NAS 上核对（只读；命令按 basename 显式列出，不遍历曲库）：
```bash
ssh kotomi-root 'ls -l /vol1/1000/fnmusic-test-20261031/<basename>'
ssh kotomi-root 'ffprobe -hide_banner -v error -show_format -show_streams -of json /vol1/1000/fnmusic-test-20261031/<basename>'   # 记录 codec/sample_rate/bits、时长
ssh kotomi-root 'stat -c "%a %U:%G %n" /vol1/1000/fnmusic-test-20261031/<basename>'
ssh kotomi-root 'ls -l /vol1/1000/fnmusic-test-20261031/<basename 同名 .lrc 或内嵌封面标记>'
ssh kotomi-root 'ls -A /vol1/1000/fnmusic-manual-save-staging'   # 必须为空（无 fnmusic-manual-save-* 残留）
```
**通过**：文件存在；basename 与响应一致；`ffprobe` 可解码且格式与 route 报告一致；标签（title/artist/album/track）齐全；封面/LRC 状态与 route 报告一致；mode 与既有文件一致；staging 空。

### A5 重复位置
**通过**：重复 `source_id` 的每个位置都在列表里；第 2 次通常 `conflict`（未覆盖）；若都 `published`，必须能用"basename 不同"解释并记录两个 basename。

### A6 失败逐首可见
1. 找到预期失败位置（VIP/无版权/无音质）。
2. **通过**：该位置 `state=rejected` 且 `reason_message` 为具体固定文案（如"无可用音质"）；刷新页面后仍显示同一原因；整单状态不是"已完成"。

### A7 部分完成语义
**通过**：有失败时 job `state∈{partial, completed_with_issues}`；UI 文案为"部分完成（有失败项）"；不得出现"整单成功"。

### A8 刷新不丢
1. 推进到一半时刷新页面/重开管理台。
2. **通过**：进度与位置结果恢复（localStorage 恢复路径）；再点继续可接着推进。

### A9 取消
1. 另建一个 job，推进 2–3 首后点取消。
2. **通过**：queued → `cancelled`；已发布保留；running 那首跑完后如实记录；无文件回滚。

### A10 开关关闭（**需用户同意改 `.env` + 重启的窗口**）
1. 记录 `.env` 原值（备份路径），临时置 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false`，重启服务。
2. 依次请求列表/快照/创建/推进 → **全部 403**（仅整歌单开关关 → `playlist_disabled`；总开关关 → `disabled`）。
3. 检查：无账本写入（`FNMUSIC_MANUAL_SAVE_JOBS_DB` mtime 不变）、无引擎调用（日志）、无 NAS 写（目标目录元数据不变）。
4. **恢复原值并重启**，记录恢复后的状态。

### A11 直连 8774
```bash
ssh kotomi-root 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8774/api/manual-save/playlist/playlists'
```
**通过**：`501`；日志无执行路径。

### A12 越权与伪造
| 请求 | 期望 |
| --- | --- |
| 缺 `x-fnmusic-save: 1` | 403 |
| 非 admin claim | 403 |
| 缺 `x-trim-userid` | 403 |
| 普通用户（uid 1000）直连 socket | 连接被 L2 关闭/拒绝 |
| 用他人 `job_id` 查询（跨 actor_scope） | 404 `not_found` |

### A13 脱敏
**通过**：新增端点的响应/新日志/DB 中无签名 URL、Cookie、token、目标目录或暂存路径；DB 只含 manifest 与状态列。既有 `status` 端点的 `target_dir/staging_dir` 回显属已验收行为，不算违规。

### A14 容量/上限
1. 1001 首歌单（若可得）→ 409 `playlist_too_large`；1000 首合法（不要求跑完）。
2. 账本容量打满 → 409 `capacity_exceeded`（可用注入小容量账本离线验证，不在真机造满）。

### A15 测试基线
```bash
cd /e/fnmusic-ext-dev
export PYTHONUTF8=1 PYTHONPATH="E:/fnmusic-ext-dev/proxy"
PY="C:/Users/admin/AppData/Local/Temp/fnmusic-testvenv-py314/Scripts/python.exe"
"$PY" -B -m pytest -p no:cacheprovider -q proxy/tests/test_manual_save_playlist_gateway.py proxy/tests/test_manual_save_gateway.py proxy/tests/test_manual_save_route.py proxy/tests/test_manual_save_playlist_jobs.py proxy/tests/test_manual_save_playlist.py proxy/tests/test_manual_save_delivery_guard.py proxy/tests/test_manual_save_metadata.py
"$PY" -B -m pytest -p no:cacheprovider -q offline_tests
cd webui-service && node test_app_js.js
```
**通过**：数字不低于本轮基线（7 套 1029/2；offline 295/61/237；node 135/135）。

### A16 `commit_unknown` 冻结
**离线验证为主**（不真机制造未知副作用）：
- 用 P4/P5 的集成测试构造 `commit_unknown`，断言 job → `reconciliation_required`、后续 advance → `no_work`(200)、UI 横幅、取消仍可用、不自动重试、不按文件存在性判成功。
- 若真机出现（意外），记录并停止推进，人工核对文件后再处理。

## 4. 证据清单（提交时随附，全部脱敏）

- [ ] 前置检查输出（A1/A10/A11 的 curl 状态码）
- [ ] 测试歌单 ID（脱敏）、成员数、重复位置数、两次指纹
- [ ] `job_id` 与 `manifest_sha256`
- [ ] 逐位置结果表（position/state/reason/reason_message）
- [ ] 每个 `published` 位置的 `ls -l` + `ffprobe` 摘要（codec/sample_rate/bits/duration）+ mode + 封面/LRC 状态
- [ ] staging 目录 `ls -A` 输出（应为空）
- [ ] A6 失败位置截图或 JSON 片段（脱敏）
- [ ] A8 刷新前后进度对比
- [ ] A9 取消前后状态
- [ ] A10 开关关闭期间四个端点的 403 码 + DB mtime/目录元数据未变
- [ ] A12 五种越权结果
- [ ] A15 三组测试数字
- [ ] 执行人、UTC+9 时间、维护窗口、HEAD 与部署版本

## 5. 不证明（原文保留）

provider 成员列表的完整性/实时性/授权、Cookie 未被服务端轮换、NAS 写入能力、音频有效性、飞牛音乐索引/可播放性（这一项需另在飞牛音乐界面确认，见下）。

## 6. 飞牛音乐侧确认（用户手动或浏览器操控）

1. 打开飞牛音乐，定位测试库目录下的新文件。
2. 确认条目显示：封面、歌词、歌手、专辑；能播放。
3. 记录界面截图（脱敏）与用户确认结果。

## 7. 失败与回滚

- 任一 A 项不通过 → **停止后续写入**，保留现场证据，按合同 §11 记录为阻塞项。
- 文件级回滚：**不自动删除**；按 job `positions[]` 显式列出的 basename 逐个与用户确认后处理。
- 开关回滚：把 `.env` 恢复为执行前备份值并重启服务（记录时间）。
