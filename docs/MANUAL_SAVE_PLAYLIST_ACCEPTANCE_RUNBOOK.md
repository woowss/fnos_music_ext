# 整歌单保存 · P7 真实端到端验收运行手册

> 状态：**运行手册（离线准备完成，尚未执行；不代表可上线）**。候选实现已增加经白名单核验、按位置持久化的 detail；该候选尚未完成独立审查、受控部署与真机验收，因此真实歌单验收仍为 **BLOCKED**。detail 不包含 basename、路径或 track/disc，A4 仍需隔离曲库的文件级证据核对实际音质、格式、完整标签、封面/LRC、索引与播放。音源权益、运行影响或范围不明确时同样保持 BLOCKED 并报告。
> 合同：`FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01` §10；正本
> [`docs/MANUAL_SAVE_PLAYLIST_JOB.md`](MANUAL_SAVE_PLAYLIST_JOB.md)。
> 契约前置证据：[`docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md`](MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md)（P3）。

## 0. 红线（执行期间必须持续成立）

- **只在测试库写入**：目标 `/vol1/1000/fnmusic-test-20261031`、暂存 `/vol1/1000/fnmusic-manual-save-staging`（沿用已验收配置，不新增目录）。
- **不删除任何已保存文件**；**禁止按名字通配/递归清理**。任何无关或来源/权益不明数据清理均须单独授权。
- `.env`、部署和重启只能在[部署检查单 §0.1 生命周期门](MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md#lifecycle-gate)与[§0.2 身份门](MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md#identity-gate) PASS、备份和回滚路径已验证后，按本会话授权范围执行；不逐项重复索要同一例行授权。改前后记录原值与备份路径；影响无法判断时停止。
- 不运行 `scripts/*probe*`；不触碰 3 个受保护未跟踪文件；不把签名 URL/Cookie/token/路径写进证据文档或日志。
- 用户账号（uid 1000）与 root 通道沿用交接 §9.1；root 仅用于只读检查及安全门通过后、处于本会话既有授权范围内的受控开关切换，无需重复索要同一授权。

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
1.4 必须确认**部署侧是否已包含 P1/P2/P4/P5/P6 代码**——若未包含，先按部署检查单完成已授权的受控部署流程；安全门未 PASS 则停止，不能靠重复确认替代验证。

## 2. 用例与准备

| 项 | 要求 |
| --- | --- |
| 测试歌单 | **含至少 1 个重复曲目位置**、成员数 ≥5；执行者在当前授权范围内选择权益明确的隔离测试歌单并记录 ID；权益不明则停止 |
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
**异常规则**：网络错误、504、非精确 `busy`、非 2xx、未知码或畸形响应后最多 GET 一次观察，然后停止；不得重发 advance。GET 只读取账本，不能证明旧 worker 已退出或请求没有生效。

### A4 逐首核对（**核心**）
**当前状态：BLOCKED，不能开始真实歌单写入验收。** 候选 detail 仅在成功发布且 `song_id` 与该位置匹配时提供 title/artist/album、实际音质及报告状态、探测格式、封面和歌词状态；它不包含 basename、track/disc、路径或 MD5。候选尚未独立审查或部署，且尚无真机歌单结果。后续 A4 必须用隔离测试曲库的独立文件证据核对 basename、可解码性、完整标签、mode、staging、索引和播放。音质仍必须遵守“优先超清母带；仅当前账号明确无权且较低档明确可用时才降级到实际可获得的最高档，并报告实际档位”的用户要求。不得把请求的档位、URL、扩展名/codec、相邻单曲样本或手动猜测当作实际结果。

以下只读核验命令须等候选完成独立审查、满足部署安全门并完成受控部署后才可执行；它们本身不构成 A4 通过证据：
```bash
ssh kotomi-root 'ls -l /vol1/1000/fnmusic-test-20261031/<basename>'
ssh kotomi-root 'ffprobe -hide_banner -v error -show_format -show_streams -of json /vol1/1000/fnmusic-test-20261031/<basename>'   # 记录 codec/sample_rate/bits、时长
ssh kotomi-root 'stat -c "%a %U:%G %n" /vol1/1000/fnmusic-test-20261031/<basename>'
ssh kotomi-root 'ls -l /vol1/1000/fnmusic-test-20261031/<basename 同名 .lrc 或内嵌封面标记>'
ssh kotomi-root 'ls -A /vol1/1000/fnmusic-manual-save-staging'   # 必须为空（无 fnmusic-manual-save-* 残留）
```
**通过条件**：文件存在且 basename 可与隔离测试位置独立对应；`ffprobe` 可解码；格式、实际音质档位、标签（title/artist/album/track）、封面/LRC 状态与 route/ledger 明细及落盘一致；mode 与目标目录既有文件一致；staging 空。当前候选未审查/部署且 A4 为 **NOT RUN / BLOCKED**，不得判通过或宣称可上线。

位置 detail 中的新 `published_sha256`/`published_size` 是传给 staging store 的 tagged bytes 指纹，仅在 no-replace 发布成功后提供；它不证明发布路径的落盘字节一致。仅在受控、授权、隔离测试目录中，可将管理员查询取得的 position/hash/size 输入本地只读 `scripts/verify_position_file_evidence.py --root <测试快照根目录>`，与 stdin JSON 中每个位置的 `position,published_sha256,published_size` 比对。扫描器只接受 Linux x86_64 上的 Btrfs 快照：通过 `BTRFS_IOC_GET_SUBVOL_INFO` 验证根及每个目录/普通文件属于同一 treeid/UUID、parent UUID 非空且 root item 的 `BTRFS_ROOT_SUBVOL_RDONLY` 位（bit 0）为只读；这不是 `BTRFS_SUBVOL_RDONLY`（bit 1，供 GETFLAGS/SETFLAGS 与创建类 ioctl 使用）。并在整次扫描中复核 generation、flags 和身份 tuple。generation 是该 ioctl 返回的 root item generation；Linux 上游源码显示 root item generation 与当前 root tree block generation 同步更新，但当前没有目标 Linux/Btrfs 内核上的原生 RO→RW→写入→RO 实测证明每种写入路径都必然改变被该 ioctl 观察到的 generation。离线用例仅注入 generation 改变，不能证明内核行为。因此候选代码设有硬门 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`：在原生 Linux/Btrfs 验证和独立审查完成、并通过另一个明确代码变更撤销该硬门前，扫描器对所有有效位置只返回 `unknown`，且 CLI 以退出码 2 标记不完整，不会报告 `complete:true`；抗中途解只读/写入/重置保证仍为 **BLOCKED**。不得把合成测试或绕过测试内硬门的调用当作原生证据。普通只读 bind mount、inode immutable 标记也不是此处的稳定性证明。非 Btrfs、可写子卷、嵌套子卷、symlink、跨挂载、generation/身份变化、IOCTL 不支持或读取错误均整批 `unknown`。结果只含 position/status/match_count，不输出文件名、路径或异常文本；0 匹配为 `no_match`，唯一匹配为 `unique_match`，多处相同字节为 `ambiguous`。扫描器只读，不创建快照、不改子卷属性、不写曲库、不连接服务；主机内核/存储管理员可改变整个系统或伪造进程状态，属于其信任边界，不声称能抵御被攻陷的内核/管理员。它也不取代独立 `ffprobe` 标签/封面、LRC、索引和播放验收。当前 Windows 开发环境不能验证其 Linux `openat2`/Btrfs ioctl 路径，NAS 原生 generation 实测为 **NOT RUN**。

### A5 重复位置
**通过**：重复 `source_id` 的每个位置都在列表里；第 2 次通常 `conflict`（未覆盖）；若都 `published`，必须能用"basename 不同"解释并记录两个 basename。

### A6 失败逐首可见
1. 找到预期失败位置（VIP/无版权/无音质）。
2. **通过**：该位置 `state=rejected` 且 `reason_message` 为具体固定文案（如"无可用音质"）；刷新页面后仍显示同一原因；整单状态不是"已完成"。

### A7 部分完成语义
**通过**：有失败时 job `state∈{partial, completed_with_issues}`；UI 文案为"部分完成（有失败项）"；不得出现"整单成功"。

### A8 刷新不丢
1. 推进到一半时刷新页面/重开管理台。
2. **通过**：已有 job ID 时 GET 可展示账本状态；持久 `paused` 在刷新后仍为 paused 且不自动推进。GET 只观察，不能证明旧 worker 已退出；本版本不承诺自动恢复，也不得用重复 POST 试探。

### A9 取消
1. 另建一个 job，推进 2–3 首后点取消。
2. **通过**：queued → `cancelled`；已发布保留；running 那首跑完后如实记录；无文件回滚。

### A10 开关关闭（配置与重启门禁）
1. 仅在[部署检查单 §0.1 生命周期门](MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md#lifecycle-gate) PASS、备份/回滚验证完成后，记录 `.env` 原值（备份路径），临时置 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false`，按已验证步骤重启服务；此动作在本会话既有受控操作授权范围内，不需重复逐项确认。
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
**通过**：新增端点的响应/新日志/DB 中无签名 URL、Cookie、token、目标目录或暂存路径；DB 只含 manifest、位置状态与固定 detail 白名单。既有 `status` 端点的 `target_dir/staging_dir` 回显属已验收行为，不算违规。

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
- [ ] **当前 BLOCKED**：候选实现经独立审查并受控部署后，附每个 `published` 位置的 route/ledger detail 与隔离文件证据：basename、实际档位/格式、`ls -l`、`ffprobe` 摘要、完整标签及封面/LRC 状态、mode；不得用请求值或文件特征填补缺项
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

## 6. 飞牛音乐侧确认（A4 可执行并通过后；优先自动只读浏览器验证）

1. 通过已登录的浏览器/Computer Use 打开飞牛音乐，定位测试库目录下的新文件；无需用户手动重复操作。若控制工具不可用或浏览器证据不可读，记录为 NOT RUN 并报告实际阻塞，不推断索引或播放成功。
2. 确认条目显示：封面、歌词、歌手、专辑；能播放。
3. 保存脱敏界面证据与实际观察结果；播放未实际验证则标记 NOT RUN。

## 7. 失败与回滚

- 任一 A 项不通过 → **停止后续写入**，保留现场证据，按合同 §11 记录为阻塞项。
- 文件级回滚：**不自动删除**；本手册不授权清理已发布文件。任何测试产物清理都必须严格限定于本次 job 的 `positions[]` 明确列出的文件；无关文件或权益/来源不明数据须单独授权，不得递归/通配删除。
- 开关回滚：把 `.env` 恢复为执行前备份值并重启服务（记录时间）。
