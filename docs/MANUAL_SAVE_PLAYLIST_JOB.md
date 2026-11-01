# 管理页整歌单保存合同（任务账本 + 按请求推进）

> 合同编号：`FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01`
> 状态：**P1/P2/P4 已实现，独立审查 FAIL 项已修正（2026-11-01）；P5/P6 待实现**。本文定义规格，实现状态见 §14。
> 仓库：`E:\fnmusic-ext-dev`　分支：`codex/fnmusic-prelaunch-20261025`　基线 HEAD：`15d7a42`　仓库 VERSION：2.10.2
> **行号基线声明**：本文所有 `文件:行号` 以 `15d7a42` 为准；P1 的 reason 枚举扩展落地后 `proxy/manual_save_jobs.py` 在 `_OUTCOME_REASONS` 之后整体后移约 12 行、`offline_tests/test_manual_save_jobs.py` 后移约 36 行，复核时按符号定位。
> 执行形态：**B（后端任务账本 + 按请求推进）**；快照来源：**A（固定 musicbox artifact + 先做一次只读集成测试观察副作用，再实施接线）**
> 读者：implementer（唯一写者）、final-reviewer（独立审查）、Lead（验收/决策）

本文所有事实性断言都带 `文件:行号` 或交接文档章节引用；无法确证的写"待核实"，不编造行号。引用路径均相对仓库根目录，唯一例外是桌面仓库交接文档 `E:\fnmusic\PROJECT_HANDOFF_20261101.md`。

---

## 目录

1. [范围与非目标](#1-范围与非目标)
2. [数据流与信任边界](#2-数据流与信任边界)
3. [端点规格](#3-端点规格)
4. [快照与固定](#4-快照与固定)
5. [执行器映射](#5-执行器映射)
6. [状态机与 UI](#6-状态机与-ui)
7. [并发、预算与超时](#7-并发预算与超时)
8. [安全与脱敏](#8-安全与脱敏)
9. [测试计划](#9-测试计划)
10. [验收标准](#10-验收标准)
11. [风险与未决问题](#11-风险与未决问题)
12. [交付物清单](#12-交付物清单)
13. [证据索引](#13-证据索引)

---

## 1. 范围与非目标

### 1.1 本轮做什么

用户在产品上要的最终结果是：**在管理页选定自己的一张网易云歌单，把整张歌单逐首保存到已配置的 NAS 目标目录，并逐首核对结果**。

本合同的实现范围（G5 阶段，见交接文档 §7 `E:\fnmusic\PROJECT_HANDOFF_20261101.md:185-202`）：

1. 管理页新增"整歌单保存"入口，可列出**已登录账号**的歌单、读取某个歌单的**有序成员快照**、确认成员数量后创建保存任务。
2. 后端引入**任务账本**（复用既有离线组件 `proxy/manual_save_jobs.py`），持久化任务意图与每个位置的顺序结果。
3. **按请求推进**：前端每发一次推进请求，后端恰好执行**一首**（`manual_save_route.save_track`），不引入常驻 worker 线程。
4. 逐首进度、逐首结果、失败码、重复位置在 UI 可见；刷新后进度不丢（见 §6.5）。
5. 失败一律固定码 fail-closed；`commit_unknown` 显式冻结并要求对账（§5.4、§5.5）。

### 1.2 非目标（本轮明确不做）

| 非目标 | 理由/依据 |
| --- | --- |
| 自动去重（按 song_id 或 basename 预去重） | 批次语义权威明确规定"不预去重、逐位置独立执行，重复位置保留"（`docs/OFFLINE_PLAYLIST_SAVE.md:78`）；冲突由 store 唯一裁决（同文件 `:290`） |
| 播放镜像导入（如 `/api/v1/public-playlists/{id}/tracks`） | 交接文档硬约束：清单必须来自**可验证的有序成员快照**，不能拿 `/preview` 或播放镜像冒充导入清单（`E:\fnmusic\PROJECT_HANDOFF_20261101.md:190-196`）；`docs/MANUAL_IMPORT_PLAN.md:211` 要求"不复用会丢项的播放镜像" |
| NCM 转换 | 当前保存路径从 musicbox 拿到的始终是 flac/mp3 直链，仓库对 NCM 只有拒绝逻辑；交接 §8 结论"当前路径不涉及 NCM，不需要转换器"（`E:\fnmusic\PROJECT_HANDOFF_20261101.md:203-207`） |
| 对账 API / 自动重试 / 自动恢复 | 本轮不开放，见 §3.8；账本契约要求外部核对由单独授权流程完成（`docs/OFFLINE_SAVE_JOBS.md:45,57`） |
| 任务列表 / 历史浏览 / 删除任务 | 账本无 `list_jobs`/`delete_job` API（`proxy/manual_save_jobs.py` 公开方法清单见 `:541,601,619,655,809`），本轮不扩展 |
| 常驻 worker、后台线程、调度器 | 执行形态 B 已拍板；账本明确"没有后台 worker"（`docs/OFFLINE_SAVE_JOBS.md:35-45`） |
| 修改 `.env`、部署、重启服务、NAS 写入 | 交接 §9.2 纪律：功能默认关闭，未经用户明确同意不改 `.env`、不部署、不重启（`E:\fnmusic\PROJECT_HANDOFF_20261101.md:219-228`） |
| 修改 `webui-service/app.py` 的 501 收口 | WebUI 不是信任边界，`/api/manual-save*` 必须继续 501（`webui-service/app.py:1749-1758`，`docs/G0_TRUSTED_INGRESS.md:87-92`） |

### 1.3 与既有交付物的关系

| 既有组件 | 本轮关系 |
| --- | --- |
| `proxy/manual_save_jobs.py`（账本，合同 `FNMEXT-OFFLINE-SAVE-JOBS-20261024-01`） | **首次生产接线**；并需做一次**受审修订**：扩展 `FAILED`/`REJECTED` 的固定 reason 枚举（§5.5），不动 DB schema |
| `proxy/manual_save_playlist.py`（批次引擎） | **不用于执行**（执行形态 B 不用整批调用）；仅复用其 **continue/abort 码分类**作为映射语义来源（`proxy/manual_save_playlist.py:42-64`） |
| `proxy/manual_save_route.py`（单曲执行器） | 逐首执行入口 `save_track`（`:550`）；**不修改** |
| `proxy/webui_gateway.py`（宿主网关） | 新增 6 个端点 + 共享执行锁 + 固定码/固定文案表；既有单曲端点语义不变 |
| `musicbox-service/playlist_manifest.py`（快照候选） | **不修改**；新增调用方（§4.2） |
| `webui-service/static/{app.js,index.html}` | 新增整歌单卡片；复用既有 `manualSaveApi`/`manualSaveMessage`/禁用态风格 |

---

## 2. 数据流与信任边界

### 2.1 链路

```
[浏览器 管理页]
   │  同源 fetch，路径前缀 APP_BASE="/app/fnmusic-ext"（webui-service/static/app.js:6）
   │  每个请求自动带 x-fnmusic-save: 1（app.js:1951 manualSaveApi）
   ▼
[官方统一网关 trim_http_cgi, uid=0]  ── 转发 ──▶ [fnmusic-ext.sock]
   │                                            │
   │                          ┌─────────────────▼──────────────────────────────┐
   │                          │ proxy/webui_gateway.py（宿主 root 进程）        │
   │                          │ L2 accept: SO_PEERCRED uid 允许名单             │
   │                          │ L3 本地处理器自有 strict head 解析               │
   │                          │ L4 x-fnmusic-save + 单条 canonical admin claim  │
   │                          └───────┬───────────────────┬─────────────────┬──┘
   │                                  │                   │                 │
   │                    创建前只读快照 │        推进一次    │     账本读写     │
   │                                  ▼                   ▼                 ▼
   │                    ┌──────────────────────┐  ┌──────────────┐  ┌──────────────────────┐
   │                    │ musicbox-service      │  │ manual_save_  │  │ 新建 proxy/manual_   │
   │                    │ 127.0.0.1:8770        │  │ route         │  │ save_playlist_jobs.py│
   │                    │ GET .../member-       │  │ .save_track   │  │ + ManualSaveJobsLedger│
   │                    │ snapshot（只读）      │  │ (:550)        │  │ （SQLite，宿主私有）  │
   │                    └──────────────────────┘  └──────────────┘  └──────────────────────┘
   │                           │                        │                     │
   │                           ▼                        ▼                     ▼
   │                    NetEase-MusicBox 0.5.3     NAS 目标/暂存目录   <DB 目录>/manual-save-jobs.sqlite3
   ▼
[webui-service:8774]  /api/manual-save* 一律 501（webui-service/app.py:1749-1758）
```

关键点：

- 浏览器**只与宿主网关**对话；`/api/manual-save/*` 在 WebUI 侧是 501 占位（`webui-service/app.py:1749-1758`），不存在"直连 8774 执行"的降级路径（`docs/G0_TRUSTED_INGRESS.md:87-92`）。
- 宿主网关是唯一持有保存引擎与账本的进程；musicbox 端口只绑定 `127.0.0.1:8770`（`docker-compose.yml:31`）。
- 网关对 NetEase 的访问**只经由 musicbox-service**（既有 `save_track` 也是这么做的，`proxy/manual_save_route.py:320-336` 读 `.env` 的 `FNMUSIC_MUSICBOX_URL`）；网关自身不持有 NetEase Cookie、不解析签名 URL。

### 2.2 为什么账本在 gateway 侧

1. **特权与引擎同址**：单曲保存的引擎与特权 `manual_save_route` 只在宿主网关进程内（`webui-service/app.py:1749-1758` 明确 WebUI 不持有引擎）。
2. **单写者**：SQLite 账本 `synchronous=FULL` + 有界 busy timeout（`docs/OFFLINE_SAVE_JOBS.md:5-11`），由唯一网关进程写入可避免跨进程锁；跨进程互斥不在本轮范围。
3. **跨容器重启存活**：WebUI/音源容器重启后任务仍在宿主 DB 里；账本"打开数据库不会自动恢复或执行任务"（`docs/OFFLINE_SAVE_JOBS.md:45`），符合 fail-closed。
4. **不新增权限面**：不新增 socket、端口、systemd 单元或容器能力；只新增网关内的 HTTP 出站到 `127.0.0.1:8770`（与既有保存路径同类）。

### 2.3 为什么前端只轮询/推进，不做长请求

- 网关 worker 超时 `MANUAL_SAVE_TIMEOUT = 1200` 秒（`proxy/webui_gateway.py:106`），注释明确"引擎 worst case 可能超过 600 s，网关不得先于引擎预算返回 504"（`:101-106`）；单曲 worst case 已接近上限（交接 `E:\fnmusic\PROJECT_HANDOFF_20261101.md:190-196`）。
- 因此整单**绝不能塞进一个同步请求**；每次推进请求只跑一首，签入账本，浏览器关掉也只是停止推进，不丢状态。
- 不引入常驻 worker：账本设计就是"没有后台 worker，`run_next` 事务提交 claim 后在事务外同步调用执行器"（`docs/OFFLINE_SAVE_JOBS.md:35-45`，`proxy/manual_save_jobs.py:655-700`）。

### 2.4 `actor_scope` 由谁提供

- `actor_scope` 是账本要求每个公开方法都传入的**可信内部隔离标签，不是身份凭据**（`docs/OFFLINE_SAVE_JOBS.md:5-11`，`proxy/manual_save_jobs.py:541,601,619,655,809`）。
- 来源：网关 L4 已校验的**单条 canonical `X-Trim-Userid` claim**（`proxy/webui_gateway.py:581-600`，`MANUAL_SAVE_REQUIRE_USER_ID=True` 在 `:102`）。
- **推导规则（强制）**：`actor_scope = "u" + sha256(user_id_claim_bytes).hexdigest()[:40]`（41 字符，满足 `_IDENTIFIER_RE`，`proxy/manual_save_jobs.py:33`）。理由：
  1. 不把原始 claim 写入 DB/日志/响应（G0 残余风险 R1 指出单 claim 仍可能是伪造的，`docs/G0_TRUSTED_INGRESS.md:150-166`）；
  2. 不同 claim 天然分区，`get_job`/`cancel_job`/`run_next` 的 scope 不匹配一律固定 `not_found`（`docs/OFFLINE_SAVE_JOBS.md:5-11`）；
  3. 客户端**永远不能**在请求体/查询串里提供 `actor_scope`（出现即 `invalid_request`）。
- `job_id` 本身不是访问凭据（同文档），因此前端可把它放进 `localStorage` 用于刷新恢复（§6.5），但请求仍必须通过 L4。

### 2.5 与既有单曲端点的关系

新增的"推进一次"与既有 `/api/manual-save/track` **共享同一把网关级执行锁**（§7.1），保证"同一时间只有一个保存动作在跑"这一产品承诺（`webui-service/static/index.html:291` 已写明"整个保存过程中同一时间只执行一个任务"）。既有单曲端点的 URL/头/信封/错误码/超时语义**不得改变**（`docs/G0_TRUSTED_INGRESS.md:80-86`）。

---

## 3. 端点规格

### 3.0 通用约定

| 项 | 规格 |
| --- | --- |
| 路径前缀 | 双写：`/app/fnmusic-ext/api/...` 与 `/api/...`，与既有常量一致（`proxy/webui_gateway.py:81-92`） |
| 方法 | 严格：GET 只接受 GET，POST 只接受 POST；方法不符的请求**不进入新处理器**，落回转发路径 → WebUI 501（同既有行为） |
| 头 | 必须 `x-fnmusic-save: 1`（`proxy/webui_gateway.py:97-98`）+ 单条 canonical `X-Trim-Isadmin: true` + 语法合规 `X-Trim-Userid`（`:102,581-600`）。**不得放宽、不得新增可选旁路** |
| head 解析 | 复用 `_parse_manual_save_head`（`:542-580`）：单请求行、ASCII、无重复头、禁 `Transfer-Encoding`、`Content-Length` 只允许十进制且 ≤ 上限 |
| 请求体上限 | `MANUAL_SAVE_MAX_BODY = 4096`（`:94`）；GET 查询串上限 `MANUAL_SAVE_MAX_QUERY = 2048`（`:95`） |
| 头上限 | `MANUAL_SAVE_MAX_HEADER = 16 * 1024`（`:93`） |
| 信封 | 固定 `{"ok": bool, "code": str, "message": str, "data": object}`，与既有端一致（`proxy/webui_gateway.py:505-513,725-741`） |
| 文案 | 新码一律进固定文案表；`message` 不插值、不回显输入、不回显异常文本（同 `proxy/manual_save_playlist.py:94-121` 的纪律） |
| 引擎开关 | 需同时满足 `FNMUSIC_MANUAL_SAVE_ENABLED=true`（既有）与 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=true`（新增，默认 false）；否则 403 且无副作用——**总开关关闭 → `disabled`，仅整歌单开关关闭 → `playlist_disabled`**（两码都在 §3.9.1） |
| 账本可用 | DB 路径配置非法/不可写 → 500 `config_invalid`（与 §3.9.1 一致；不新增 403 版本），不得创建空 DB 后继续 |
| 幂等 | 创建按 `(actor_scope, idempotency_key)` + 规范 manifest 字节幂等（`docs/OFFLINE_SAVE_JOBS.md:13-22`）；查询/取消天然幂等；推进**不是**幂等操作但**每个位置至多执行一次**（§7.4） |

**开关取值来源（P5 实现）**：`fnmusic-ext.service` 没有 `EnvironmentFile=`，所以网关优先从 `<repo>/.env` 读取两个已复核的键（`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`、`FNMUSIC_MANUAL_SAVE_JOBS_DB`），进程环境只补缺键；**只保留这两个键**，不把 `.env` 其余内容留在内存。文件缺失/不可读 → 视为全部未设置（fail-closed）。这是已审 route 模块 `read_env_file()` 的既有先例，属部署便利而非新增信任边界。

### 3.1 路径与常量命名建议

在 `proxy/webui_gateway.py` 现有常量区（`:81-108`）追加：

```python
MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/playlists",
    "/api/manual-save/playlist/playlists",
)
MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/snapshot",
    "/api/manual-save/playlist/snapshot",
)
MANUAL_SAVE_PLAYLIST_JOBS_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/jobs",
    "/api/manual-save/playlist/jobs",
)
MANUAL_SAVE_PLAYLIST_JOB_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/job",
    "/api/manual-save/playlist/job",
)
MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/advance",
    "/api/manual-save/playlist/advance",
)
MANUAL_SAVE_PLAYLIST_CANCEL_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/cancel",
    "/api/manual-save/playlist/cancel",
)
MANUAL_SAVE_PLAYLIST_READ_TIMEOUT = 120.0     # 两次快照 + 列表读取的上限
MANUAL_SAVE_PLAYLIST_TIMEOUT = MANUAL_SAVE_TIMEOUT  # 1200，推进一首复用同一预算
MANUAL_SAVE_PLAYLIST_MAX_POSITIONS = 1000     # 与账本 MAX_ENTRIES_PER_JOB 对齐
MANUAL_SAVE_PLAYLIST_JOB_ID_RE = re.compile(r"^[0-9a-f]{48}$")
MANUAL_SAVE_PLAYLIST_ENABLED_KEY = "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED"
MANUAL_SAVE_JOBS_DB_KEY = "FNMUSIC_MANUAL_SAVE_JOBS_DB"
MANUAL_SAVE_PLAYLIST_LOCK = threading.Lock()  # 模块级，单曲端点与推进端点共享
```

**为什么用"固定路径 + 查询/请求体参数"而不是 `/jobs/{job_id}` 路径模板**：`_parse_manual_save_head` 只做**精确 target 匹配**（`:542-580`），路径模板需要放宽解析器；本轮优先复用已审解析器、把 `job_id` 当参数严格校验（48 位小写 hex）。若实现者坚持 RESTful 路径，必须为解析器新增独立、可测的 `prefix` 模式，且不得放宽其余规则。

### 3.2 `GET .../playlist/playlists` — 列出账号歌单

- **作用**：UI 选歌单。宿主网关转发到 musicbox `GET /api/v1/user/playlists?limit=100`（`musicbox-service/app.py:278-286`）。
- **请求**：无请求体、无查询串（`rest` 必须为空且 `content-length` 缺省或 `0`）。
- **响应 `data`**（白名单，逐字段重新构造，禁止透传）：

```json
{
  "account_uid": 123456789,
  "playlists": [
    {"playlist_id": 987654321, "name": "…", "track_count": 12,
     "updated_at": 1730000000, "owned": true}
  ]
}
```

- `name`：`str`，≤256，超长截断；非 `str` → `""`（**不做** HTML 处理，前端继续用既有 `escapeHtml`，`webui-service/static/app.js:1300`）。
- `playlist_id`/`track_count`：非 bool 的 `int` 且 `>0`/`>=0` 才保留，否则丢弃该行。
- `owned`：`user_id == account_uid`（musicbox 摘要含 `user_id`，`musicbox-service/netease_ext.py:443-456`）。
- **丢弃**：`cover_url`（含 CDN 域名）、`special_type` 等未列字段。上限 100 条（musicbox 服务 `limit<=100`，`musicbox-service/app.py:279`）；无分页，超出部分不可见（限制写进 UI 提示"最多显示 100 张"）。
- **错误**：`not_logged_in`(403)、`playlist_unavailable`(502)、`timeout`(504)、`internal`(500)、`disabled`(403)、`forbidden`(403)、`invalid_request`(400)。

### 3.3 `GET .../playlist/snapshot?playlist_id=<int>` — 只读预检

- **作用**：让用户看到"共 N 首/含 M 个重复位置"并拿到**快照指纹**，用于创建时绑定确认。
- **请求**：唯一查询键 `playlist_id`（十进制、1..4294967295、无符号、无前导 `+`）；查询串解析规则复刻 `_parse_manual_save_search_keyword` 的严格风格（`proxy/webui_gateway.py:877-905`）：必须恰好 1 个键、`name` 解码后必须等于 `playlist_id`、禁止空段/未知键。
- **行为**：worker 线程内（`_run_manual_save` 同款模式，`:749-765`）调用 musicbox 新端点（§4.2）两次**独立**观测由 musicbox 侧完成；网关只做一次调用 + 结构校验。
- **响应 `data`**（成功，`enumeration_complete=true` 才可能被创建接受）：

```json
{
  "playlist_id": 987654321,
  "track_count": 12,
  "position_count": 12,
  "duplicate_position_count": 1,
  "enumeration_complete": true,
  "source_fingerprint": "<64 位小写 hex>",
  "source_artifact": {
    "provider": "netease",
    "package": "NetEase-MusicBox",
    "version": "0.5.3",
    "wheel_sha256": "7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12"
  },
  "manifest_schema_version": 1
}
```

- **失败 `data`**（`ok=false`）：

```json
{"playlist_id": 987654321, "track_count": 13, "position_count": 12,
 "enumeration_complete": false, "source_error_code": "TRACK_COUNT_MISMATCH"}
```

  `source_error_code` 只允许固定白名单（§3.9.2），未知值一律丢弃改 `""`。
- **错误码映射**：
  - musicbox `status=not_logged_in` → 403 `not_logged_in`
  - `status=upstream_error` / 连不上 8770 → 502 `playlist_unavailable`
  - `status=unsupported` 且 `error_code∈{POSITION_LIMIT_EXCEEDED,SOURCE_LIMIT_REACHED}` → 409 `playlist_too_large`
  - `status=unsupported` 其它 → 409 `playlist_unsupported`
  - `status=incomplete` → 409 `manifest_incomplete`
  - `status=source_changed` → 409 `source_changed`
  - `track_count > 1000` → 409 `playlist_too_large`（即使 musicbox 报 complete 也必须拒绝，§4.4）
  - 版本/摘要不符 → 500 `source_artifact_mismatch`（§4.1）
  - 超时 → 504 `timeout`

### 3.4 `POST .../playlist/jobs` — 创建任务

- **请求体**（**不接受 `entries`/`actor_scope`/任何未列字段**；出现未列字段 → 400 `invalid_request`）：

```json
{"playlist_id": 987654321,
 "source_fingerprint": "<64 位小写 hex，来自 §3.3>",
 "idempotency_key": "可选；缺省由服务端派生"}
```

| 字段 | 规则 | 违反时 `code` |
| --- | --- | --- |
| `playlist_id` | 非 bool 的 `int`，`1..4294967295` | `invalid_playlist_id` (400) |
| `source_fingerprint` | `^[0-9a-f]{64}$` | `invalid_fingerprint` (400) |
| `idempotency_key` | 可选；若给，必须匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`（同账本 `_IDENTIFIER_RE`，`proxy/manual_save_jobs.py:33`） | `invalid_idempotency_key` (400) |

- **服务端行为（顺序固定，任一步失败即返回且不写账本）**：
  1. L4 授权 + 两个开关 + 账本路径校验（§3.0）。
  2. 重新读取一次只读快照（§4.2，musicbox 侧两次独立观测）。
  3. 校验 `snapshot.source_fingerprint == body.source_fingerprint`，否则 409 `source_changed`（**这就是"用户确认的那一份清单"的绑定**，防止用户确认后歌单被改动）。
  4. 校验 `enumeration_complete=true` 且 `track_count == position_count` 且 `1 <= track_count <= 1000`；否则按 §3.3 的码 fail-closed（`manifest_incomplete`/`playlist_too_large`）。
  5. 校验 artifact（§4.1）：**版本断言由 producer 完成**（网关进程未安装 `NetEase-MusicBox`，无法做进程内版本检查）；网关只校验 producer 返回的 `source_artifact.version=="0.5.3"`、`dist_info_present==true` 且 `artifact_error_code` 为空；不符 → 500 `source_artifact_mismatch`。**不得**声称网关校验了 wheel 摘要（§4.1 第 3 条）。
  6. 由**网关**（不是浏览器）构造账本 manifest（§4.3），字段取值见 §4.3.2。
  7. 派生幂等键（当 body 未给）：`"p" + str(playlist_id) + "-" + source_fingerprint`（长度 76，合法标识符）。**派生键使"刷新后重新创建"幂等返回同一个 job**（§6.5）。
  8. `ledger.create_job(actor_scope, key, manifest)`（`proxy/manual_save_jobs.py:541`）。
  9. 若 `IdempotencyConflict` → 409 `idempotency_conflict`；`CapacityExceeded` → 409 `capacity_exceeded`；`ValidationError` → 500 `internal`（网关构造的 manifest 不合法属于编程错误，不得把细节回显）；`LedgerUnavailable` → 503 `ledger_unavailable`；`SchemaError`/`CorruptLedgerError` → 500 `internal`（细节只进日志）。
- **为什么请求体不携带 `entries`**：清单必须是**服务端可验证的有序成员快照**（交接 §7），浏览器无法自行产生可信清单；让客户端提交清单等于把"导入内容"交给不可信输入，直接违反 §2 信任边界与 `docs/MANUAL_IMPORT_PLAN.md:211`。响应里会返回完整 manifest 摘要供审查。
- **响应 `data`**：

```json
{"job": { …JobSummary… }, "manifest_sha256": "<64 hex>",
 "manifest": {"kind": "playlist", "target_id": "manual-save",
              "provider_id": "netease-0.5.3-7f695b35",
              "collection_id": "netease-playlist-987654321",
              "collection_version": "sha256-<64 hex>",
              "enumeration_complete": true, "positions_total": 12}}
```

  `manifest.entries` 不回显（可由 §3.5 的 positions 得到），避免重复大载荷。
- **幂等语义**：同 scope + 同 key + 规范 manifest 字节完全相同 → 返回**原 job**（不新建、不消耗容量，`docs/OFFLINE_SAVE_JOBS.md:13-22`）；字节不同 → `idempotency_conflict`（409）。服务端派生键下，"同一歌单 + 同一指纹"重复确认永远命中同一 job。
- **HTTP 状态**：成功 200。

### 3.5 `GET .../playlist/job?job_id=<48hex>` — 查询任务

- **请求**：唯一查询键 `job_id`，值 `^[0-9a-f]{48}$`（账本 `_JOB_ID_RE`，`proxy/manual_save_jobs.py:34`）；格式不合法 → 404 `not_found`（**与账本一致：非法/不存在/跨 scope 同样固定 `not_found`**，`proxy/manual_save_jobs.py:915-919`、`docs/OFFLINE_SAVE_JOBS.md:5-11`）。
- **响应 `data`**：

```json
{"job": { …JobSummary… },
 "positions": [
   {"position": 1, "source_id": "1901371647", "state": "published",
    "reason": "bytes_published", "cleanup_pending": false},
   {"position": 2, "source_id": "1901371647", "state": "conflict",
    "reason": "destination_conflict", "cleanup_pending": false}
 ]}
```

- `positions` 顺序 = manifest 位置顺序，长度 ≤ 1000；`source_id` 为十进制字符串或 `null`（账本允许 `null`，`docs/OFFLINE_SAVE_JOBS.md:31`）。
- **附加字段 `reason_message`**（P5 实现，本轮加入）：每个位置行另带一个**固定中文**文案，由 §6.3 的 reason 文案表生成、不做插值；未知 reason 回落固定兜底文案。它服务 UI 直接渲染，不改变上面的字段语义。
- **不得包含**：任何本地路径、签名 URL、Cookie、token、执行器原始返回（§8）。
- `JobSummary` 见 §3.7.1。

### 3.6 `POST .../playlist/advance` — 推进一次（跑一首）

- **请求体**：`{"job_id": "<48hex>"}`，仅此一个键。
- **行为（时序固定）**：
  1. L4 + 开关 + 账本校验；`job_id` 格式校验（不合法 → 404 `not_found`）。
  2. **不预读 job 之外的状态、不在锁外调用执行器**。启动 worker 线程（复用 `_run_manual_save` 模式，`proxy/webui_gateway.py:749-765`）。
  3. worker 内：非阻塞获取 `MANUAL_SAVE_PLAYLIST_LOCK`；失败 → `busy`（409），**此时尚未 claim 任何位置**（关键：不能让 busy 吃掉位置，§7.1）。
  4. worker 内：`report = ledger.run_next(actor_scope, job_id, executor)`（`proxy/manual_save_jobs.py:655`）。`run_next` 自己完成"选最早 queued → 写 claim token → 提交 → 事务外同步执行 → CAS 写回"（`docs/OFFLINE_SAVE_JOBS.md:35-45`）。
  5. worker 内 finally 释放锁。
  6. 主线程 `join(MANUAL_SAVE_PLAYLIST_TIMEOUT)`；仍存活 → 504 `timeout`（**worker 继续跑完并释放锁**，超时不是取消，见 §7.3）。
- **`executor` 契约**：见 §5.1；只返回 `manual_save_core.SaveResult`，绝不抛异常（抛了也由账本转 `commit_unknown`，`proxy/manual_save_jobs.py:675-679`）。
- **响应 `data`**：

```json
{"run": {"status": "completed|commit_unknown|no_work|completion_unknown|stale_completion",
         "disposition": "continue|abort|freeze",
         "position": 7,
         "outcome": "published",
         "reason": "bytes_published",
         "detail": { …单曲已脱敏 data，仅 published/conflict 时存在… }},
 "job": { …JobSummary… }}
```

| `run.status` | HTTP | `disposition` | UI 行为 |
| --- | --- | --- | --- |
| `completed` | 200 | `continue` 或 `abort`（按 `reason` 查 §5.2） | 继续下一首；`abort` 时停止循环并提示 |
| `commit_unknown` | 200 | `freeze` | 停止循环，转"需要对账"（job 会被账本自动冻结，`proxy/manual_save_jobs.py:711-716`） |
| `no_work` | 200 | `continue` | 重新 GET job：终态则停；`running` 说明另一请求/标签页在跑 → 退避重试（§7.2） |
| `completion_unknown` | 500 | `freeze` | 停止并提示"结果写入失败，不得自动重试"（`docs/OFFLINE_SAVE_JOBS.md:35-45`） |
| `stale_completion` | 409 | `freeze` | 停止并提示"迟到的结果未覆盖记录" |
| （锁被占用） | 409 `busy` | — | 退避重试 |
| （超时） | 504 `timeout` | — | **先 GET job 再决定**是否重试（§7.3） |
| 其它 | 4xx/5xx | — | 按 §3.9 固定文案展示，停止循环 |

- **`detail`**：仅当 `run.status=="completed"` 且 `outcome∈{published}` 或 `reason=="destination_conflict"` 时出现；内容必须走**既有** `_sanitize_manual_save_data`（`proxy/webui_gateway.py:658-724`）白名单（`signed_url`/`cookie` 等未知键在被丢弃方面已有测试，`proxy/tests/test_manual_save_gateway.py` 的 `_published_result` 用例）。`detail` **不落账本、不落日志**。

### 3.7 `POST .../playlist/cancel` — 请求取消

- **请求体**：`{"job_id": "<48hex>"}`。
- **行为**：`ledger.cancel_job(actor_scope, job_id)`（`proxy/manual_save_jobs.py:619`）→ 置 `cancel_requested=1`，把仍 `queued` 的位置改为 `cancelled`（`reason="cancelled_before_publish"`），**running 位置保留**（`docs/OFFLINE_SAVE_JOBS.md:35-45`）。
- **语义**：取消不删除媒体、不撤回已发布结果、不是"回滚"；取消反复调用幂等（第二次仍返回同一快照，快照含 `cancel_requested=true`）。
- **响应 `data`**：`{"job": { …JobSummary… }}`；HTTP 200。
- **与推进并发**：取消请求**不**获取 `MANUAL_SAVE_PLAYLIST_LOCK`（快速状态转换，不执行引擎）；账本 `BEGIN IMMEDIATE` 保证与 claim/completion 的串行化（`proxy/manual_save_jobs.py:619-654,702-766`）。若取消发生在 claim 之后，running 位置仍会跑完并按真实结果记录（`docs/OFFLINE_SAVE_JOBS.md:45`）。

#### 3.7.1 `JobSummary` 字段（创建/查询/推进/取消响应共用）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `job_id` | 48 位小写 hex | 账本主键 |
| `state` | 字符串 | §6.1 的 8 个任务状态之一 |
| `cancel_requested` | bool | 是否已请求取消 |
| `counts` | object | 按位置状态计数的映射（键 ⊆ §6.2 状态集） |
| `positions_total` | int | 位置总数 |
| `manifest_sha256` | 64 位 hex | 账本对规范 manifest 字节的摘要 |
| `audio_verified` / `production_ready` / `import_ready` | bool，恒 `false` | 账本固定标志，UI/API 不得改写或隐藏 |

依据：账本 `JobSnapshot`（`proxy/manual_save_jobs.py` 约 `:147-160`）、`docs/OFFLINE_SAVE_JOBS.md:47-51`。

### 3.8 对账端点：**本轮不开放**

- 本轮**不提供** `reconcile`/`recover` 端点，也**不提供** `recover_stale_running` 的 HTTP 入口。
- 理由：账本要求"`recover_stale_running(..., worker_stopped=True)` 需要调用方显式声明旧 worker 已停止，API 无法验证该声明"（`docs/OFFLINE_SAVE_JOBS.md:45`，`proxy/manual_save_jobs.py:809-820`）；且"`commit_unknown` 和 `completion_unknown` 都需要人工按独立证据处理；账本不基于目标文件是否存在来猜测执行结果"（`docs/OFFLINE_SAVE_JOBS.md:57`）。
- 呈现方式：`get_job` 的 `state=="reconciliation_required"`（`proxy/manual_save_jobs.py:887-888`）直接透出到 UI；UI 固定展示"存在结果未知的位置，已停止推进，需要人工对账"横幅，禁用推进按钮，保留取消。
- 未定义的 `/api/manual-save/playlist/reconcile*` 等路径**不加入** target 列表：请求会落回转发路径，由 `webui-service/app.py:1749-1758` 返回 501，不触碰引擎/账本/文件系统。**这是预期行为，不是缺陷**。
- 由此产生的"进程崩溃导致 running 卡死"问题见 §11 Q2。

### 3.9 错误码总表

#### 3.9.1 端点错误码

| `code` | HTTP | 固定中文 `message` | 来源 |
| --- | --- | --- | --- |
| `ok` | 200 | `""` | 成功 |
| `invalid_request` | 400 | 请求无效 | head/体/查询解析失败 |
| `invalid_playlist_id` | 400 | 歌单 ID 必须是正整数 | 新 |
| `invalid_fingerprint` | 400 | 快照指纹无效 | 新 |
| `invalid_idempotency_key` | 400 | 幂等键无效 | 新 |
| ~~`invalid_job_id`~~ | — | — | **不设独立码**：格式非法/不存在/跨 scope 一律固定 `not_found`(404)（§3.5） |
| `not_found` | 404 | 任务不存在 | 账本 `NotFoundError`（`proxy/manual_save_jobs.py:111-113`） |
| `forbidden` | 403 | 仅管理员可执行手动保存 | 既有（`proxy/webui_gateway.py:113`） |
| `disabled` | 403 | 手动保存未启用 | 既有（`:117`） |
| `playlist_disabled` | 403 | 整歌单保存未启用 | 仅整歌单开关关闭；与总开关 `disabled` 区分（两者都为 403，均无副作用） |
| `not_logged_in` | 403 | 网易云账号未登录 | musicbox `not_logged_in` |
| `config_invalid` | 500 | 手动保存配置无效（与 route 码表逐字一致，§6.3 防漂移） | 新（DB 路径/音乐服务 URL） |
| `playlist_unavailable` | 502 | 音乐服务不可用 | musicbox 读取失败/连不上 |
| `playlist_unsupported` | 409 | 该歌单不支持可验证快照 | musicbox `unsupported` |
| `playlist_too_large` | 409 | 歌单成员超过 1000 个 | §4.4 |
| `manifest_incomplete` | 409 | 歌单成员清单不完整 | §4.3 |
| `source_changed` | 409 | 歌单在确认后发生变化，请重新读取 | §3.4 步骤 3 |
| `source_artifact_mismatch` | 500 | 音乐服务版本与固定证据不一致 | §4.1 |
| `idempotency_conflict` | 409 | 幂等键已用于不同清单 | 账本 `IdempotencyConflict`（`:115-117`） |
| `capacity_exceeded` | 409 | 任务账本容量已满 | 账本 `CapacityExceeded`（`:119-121`） |
| `ledger_unavailable` | 503 | 任务账本不可用 | 账本 `LedgerUnavailable`（`:131-133`） |
| `busy` | 409 | 已有保存任务进行中 | 网关执行锁；复用既有文案（`:115`） |
| `completion_unknown` | 500 | 任务结果写入失败，需人工核对 | 账本 `RunReport("completion_unknown")`（`:655-700`） |
| `stale_completion` | 409 | 迟到的执行结果未覆盖记录 | 账本 `RunReport("stale_completion")` |
| `timeout` | 504 | 保存超时 | 既有（`proxy/webui_gateway.py:118`） |
| `internal` | 500 | 内部错误 | 既有（`:124`）；不暴露细节 |

实现位置：新增 `_MANUAL_SAVE_PLAYLIST_MESSAGES` 与 `_MANUAL_SAVE_PLAYLIST_HTTP_STATUS` 两张表（照抄 `_MANUAL_SAVE_GATEWAY_MESSAGES`/`_MANUAL_SAVE_HTTP_STATUS` 的形状，`proxy/webui_gateway.py:112-148`），并加测试断言"未登记 code → 500 `internal`，不得透传"。

#### 3.9.2 `source_error_code` 白名单（可回显）

仅允许（来自快照候选的固定枚举）：

```
SOURCE_READ_FAILED, PLAYLIST_ID_MISMATCH, POSITION_LIMIT_EXCEEDED,
SOURCE_LIMIT_REACHED, TRACK_COUNT_MISMATCH, INVALID_TRACK_ID,
SOURCE_SCHEMA_CHANGED, ACCOUNT_CHANGED, PLAYLIST_CHANGED,
SOURCE_VERSION_CHANGED, TRACK_SEQUENCE_CHANGED, UNSUPPORTED_SOURCE,
SOURCE_CHANGED_OR_RECHECK_FAILED
```

依据：`musicbox-service/playlist_manifest.py:25-28,122,222-224,353-367,379,387`；`SOURCE_CHANGED_OR_RECHECK_FAILED` 来自既有 preview 的等价判断（`musicbox-service/netease_ext.py:757-784`，仅作命名对齐，不表示复用其路径）。

---

## 4. 快照与固定

### 4.1 musicbox artifact 选型字段（固定）

| 字段 | 固定值 | 证据 |
| --- | --- | --- |
| 包名 | `NetEase-MusicBox` | `docs/NETEASE_DELIVERY_EVIDENCE.md:7-25` |
| 版本 | `0.5.3` | 同上（CI job 121190860533/121190860514 日志） |
| wheel 文件 | `netease_musicbox-0.5.3-py3-none-any.whl` | 同上 |
| wheel SHA-256 | `7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12` | 同上（PyPI JSON `digests.sha256` 与内存计算一致） |
| 来源 | PyPI 官方发布物（`pypi.org/pypi/NetEase-MusicBox/0.5.3/json`） | 同上 |
| 代码侧适配层 | `musicbox-service/netease_ext.py` + `musicbox-service/playlist_manifest.py`，以基线 `15d7a42` 为准 | 交接 `E:\fnmusic\PROJECT_HANDOFF_20261101.md:32-48` |

**实现前置条件（硬性）**：

1. `musicbox-service/requirements.txt:2` 当前为 `NetEase-MusicBox>=0.5.3`，**必须改为 `==0.5.3`**（Lead 指出未精确钉版本）；改动列入交付物（§12），并在 `docs/NETEASE_DELIVERY_EVIDENCE.md` 追加一行"已钉版本"。
2. 运行时断言：**producer 模块**在每次快照时检查 `importlib.metadata.version("NetEase-MusicBox") == "0.5.3"` 且 `dist-info/METADATA` 存在；不等 → 返回 `status=unsupported` + `error_code=UNSUPPORTED_SOURCE` + `artifact_error_code="source_artifact_mismatch"`，**在读上游前 fail-closed**。网关只按 `artifact_error_code` 映射 500 `source_artifact_mismatch`，不做进程内版本检查（网关进程未安装该包）。
3. wheel 摘要无法在运行期低成本重算（已安装为解包后的 dist-info），因此摘要的**验证时机**是构建/CI 与只读观察测试：CI 记录 `pip download NetEase-MusicBox==0.5.3` 的 sha256 并与固定值比对；运行期只断言版本号 + `dist-info/METADATA` 存在。**这一分工必须在实现说明中写明，不得声称运行期校验了摘要。**
4. 若只读观察测试实测版本 ≠ 0.5.3：**停止接线**，回到"artifact 决策"（不在本合同范围），不得用其它版本继续。

### 4.2 快照 producer（musicbox-service 新增只读端点）

- **路径**：`GET /api/v1/user/playlists/{playlist_id}/member-snapshot`（新增；**不修改**既有 `/preview`、`/tracks` 的语义）。
- **开关**：`FNMUSIC_PLAYLIST_MANIFEST_READONLY`（默认 `false`）；关闭时返回 `{"ok": false, "error": "manifest_disabled"}` + 503，**不读取账号**。
  - **与保存开关解耦**：该 flag 只控制 producer；**不得**依赖 `FNMUSIC_MANUAL_SAVE_ENABLED` / `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`。原因：P3 只读观察的前提恰恰是保存侧全关（§4.7），若 producer 依赖保存开关则 P3 永远 503（本轮审查发现的缺陷，已在实现中修正）。
  - **取值来源**：容器内 `[program:musicbox]` 未声明 `environment=`，`.env` 是挂载文件而非进程环境。**P5/部署阶段必须**在启动时同源读取 `/repo/.env`（或经 `container/env_flag.sh:export_source_env()` 导出 / supervisord 注入）；**当前 P2 实现只读进程环境**，未配置时按关闭处理（fail-closed 503），因此 P3 之前必须先完成该部署项，不得声称 P3 就绪。
- **实现要点（不改候选模块）**：
  1. 新增 `musicbox-service/netease_playlist_snapshot.py`（调用方），**只 import，不修改** `playlist_manifest.py`。
  2. 每次调用做**两次独立观测**：
     ```
     before = snapshot_from_responses(get_account_info(), eapi("/api/v6/playlist/detail", {"id":pid,"n":1001,"s":0}), pid)
     after  = snapshot_from_responses(get_account_info(), eapi("/api/v6/playlist/detail", {"id":pid,"n":1001,"s":0}), pid)
     manifest = compare_snapshots(before, after)
     ```
     - **两次观测必须各自重新读取 account 与 playlist**，不得复用同一个响应对象（否则 `ACCOUNT_CHANGED` 永不触发，`compare_snapshots` 的账户检查形同虚设，见 `musicbox-service/playlist_manifest.py:353-367`）。
     - `n = 1001`：比账本单任务上限 1000 多 1，用来**排除截断**（若上游按 n 截断，`trackCount=1001` 时会得到 1001 条并触发 §4.4 的 `playlist_too_large`；而 `trackCount=1000` 且返回 1000 条时，因为请求了 1001 条，可以断言未截断）。**不得**沿用 preview 的 `n=1000`（`musicbox-service/netease_ext.py:511,542`）——在 1000 边界上无法区分"恰好 1000 首"与"被截断在 1000"。
     - `s = 0`，不做分页（与候选文档描述的上游观测一致：`docs/OFFLINE_PLAYLIST_MANIFEST.md` "The observed upstream client request used n=100000 without offset pagination"）。
     - **边界诚实性**（本轮审查修正）：候选文档观测的是 `n=100000`；**`n=1001` 是尚未真机验证的新形状**。截断排除不依赖"上游如何解释 n"，而依赖 `trackCount == len(positions)`（§4.6）与 §4.7 的 n 对照观察；若 §4.7 实测 1000 边界行为与预期不符，回到 n 决策，不得声称已验证。
  3. 输出 payload（所有字段由 manifest + 调用方持有的 `after` 快照组合得出）：

```json
{"ok": true,
 "status": "complete|incomplete|source_changed|unsupported|upstream_error|not_logged_in",
 "error_code": null,
 "schema_version": 1, "provider": "netease",
 "account_uid": 123456789, "playlist_id": 987654321,
 "track_count": 12, "position_count": 12,
 "enumeration_complete": true,
 "source_fingerprint": "<64 hex>",
 "source_versions": [["updateTime", 1730000000], ["trackUpdateTime", 1730000001]],
 "positions": [{"position": 1, "track_id": 1901371647, "error_code": null}],
 "metadata_status": "not_requested",
 "metadata_complete": false, "import_ready": false,
 "production_ready": false, "provider_verified": false}
```

  - `schema_version`/`provider`/`source_versions` 来自 `after` 快照（`musicbox-service/playlist_manifest.py:53-62`），**不是**改候选模块的输出。
  - **`ok` 语义（本轮审查修正）**：所有观测/失败 payload 的 `ok` **恒为 `true`**，失败只靠 `status` + `error_code` 表达；只有端点关闭（503）响应是 `ok:false` + `error`。理由：`ok:false` 表示"协议层失败"，若用它表达业务失败，下游无法区分"响应畸形"与"已知失败态"（本轮已修复 producer/consumer 不兼容）。
  - **`source_artifact`**（producer 输出，网关只透出/记录）：`{"provider","package","version","wheel_sha256","dist_info_present"}`；`wheel_sha256` 是固定常量，**运行期不重算**（§4.1 第 3 条）。
  - **`artifact_error_code`**：仅当版本/元数据断言失败时为 `"source_artifact_mismatch"`，否则 `null`。
  - `positions[].error_code` 只允许 `null` 或 `INVALID_TRACK_ID`（候选固定值，`:255`）。
  - 三个 readiness 标志**恒为 false**，与候选文档要求一致（`docs/OFFLINE_PLAYLIST_MANIFEST.md` "Every result keeps `metadata_complete`, `import_ready`, `production_ready`, and `provider_verified` false"）。
  4. **不请求**歌曲详情、音频 URL、歌词、封面；不做元数据补全（`metadata_status="not_requested"` 已在候选 dataclass 中，`:70-77`）。这样上游调用固定为 4 次只读请求（2×account + 2×playlist）。
  5. 失败传播：候选的 `SourceFailure`（`unsupported/upstream_error/not_logged_in/incomplete/source_changed`，`:25-28`）原样映射到 `status`+`error_code`，不吞异常文本。
- **不得**在 gateway 侧直连网易云或解析 Cookie：producer 是唯一 NetEase 适配层。

### 4.3 两次快照的判定与 manifest 构造

#### 4.3.1 判定（候选模块给判定，producer 忠实输出，网关**必须自行复核**结构）

- **分工边界**：producer 只做候选判定与 payload 组装，**不做**下列结构复核，因此下游**不得盲信** `status`/`enumeration_complete`。这组复核由网关在 P5 逐条执行（下列 5 条结构 + provider/schema 固定值 + "计数一致不静默修复"共 **7 项**）；P4 适配层的 `build_manifest` 已实现同等复核，待 P5 接线。

- `status=="complete"` 的充要条件：两次观测的 schema/provider、账户、歌单、版本键值、`trackCount`、**完整有序位置序列**全部相同，且 `position_count == track_count` 且无无效 ID（`musicbox-service/playlist_manifest.py:333-399`；候选文档 "Completeness requires identical account, playlist, version-key set and values, declared count, and full ordered ID positions, plus a position count equal to trackCount and no invalid IDs"）。
- 网关**必须自行复核**（不盲信 musicbox 返回的布尔值）：
  1. `positions` 是 list，长度 == `position_count` == `track_count`；
  2. 每个 `position` 严格 `1..N` 连续递增；
  3. 每个 `track_id` 是**非 bool 的正整数**；任一 `error_code != null` → 不完整；
  4. `1 <= track_count <= 1000`；
  5. `source_fingerprint` 匹配 `^[0-9a-f]{64}$`。
  任一条不满足 → 409 `manifest_incomplete`（或 `playlist_too_large`），**不创建任务**。
- `enumeration_complete=false` → **禁止派发**，两层保障：
  - 创建层直接拒绝（本条）；
  - 账本层兜底：`_claim_next` 在 `not job["enumeration_complete"]` 时返回 `None`，绝不调用执行器（`proxy/manual_save_jobs.py:724-726`；`docs/OFFLINE_SAVE_JOBS.md:31` "不完整清单可以保存，但不会派发"）。

#### 4.3.2 账本 manifest 字段取值（网关构造）

```json
{
  "kind": "playlist",
  "target_id": "manual-save",
  "provider_id": "netease-0.5.3-7f695b35",
  "collection_id": "netease-playlist-987654321",
  "collection_version": "sha256-<source_fingerprint>",
  "enumeration_complete": true,
  "entries": [{"position": 1, "source_id": "1901371647", "source_status": "ok"}]
}
```

| 字段 | 取值 | 依据 |
| --- | --- | --- |
| `kind` | `"playlist"` | `docs/OFFLINE_SAVE_JOBS.md:13-31`（1..1000 位置） |
| `target_id` | `"manual-save"`，与执行器 `TARGET_ID` 一致 | `proxy/manual_save_route.py:86` |
| `provider_id` | `"netease-0.5.3-7f695b35"`（版本 + wheel 摘要前 8 位） | 把 artifact provenance 写进账本；标识符规则允许点（`proxy/manual_save_jobs.py:33`） |
| `collection_id` | `"netease-playlist-" + str(playlist_id)` | 可追溯来源歌单 |
| `collection_version` | `"sha256-" + source_fingerprint`（71 字符） | 指纹是"确定性一致性标签，不是 provider 签名"（`docs/OFFLINE_PLAYLIST_MANIFEST.md` Fingerprint 节） |
| `enumeration_complete` | `true`（创建时强制） | §4.3.1 |
| `entries[].source_id` | 十进制字符串（`str(track_id)`），前导数字、≤128 → 合法 | `proxy/manual_save_jobs.py:33,173-236` |
| `entries[].source_status` | `"ok"`（全部位置） | 只有完整快照才允许创建 |

- **限制说明（必须写进实现注释与文档）**：账本 manifest 字段是**封闭集合**（`_validate_manifest` 要求键集合严格等于 7 个，`proxy/manual_save_jobs.py:173-181`），**无法**持久化额外 artifact 摘要/标题/路径。因此 artifact 的**版本断言只在 producer 内做一次**（快照产生时），网关按 `artifact_error_code` 映射失败；`provider_id`/`collection_version` 只承载版本与指纹标签；完整证据放 `docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md`（§4.7）。
- `manifest_sha256` 由账本对规范字节计算（`docs/OFFLINE_SAVE_JOBS.md:13-22`），创建响应回显，UI 可用于展示"本次确认的清单指纹"。

### 4.4 位置上限的三层边界

| 层 | 上限 | 行为 | 依据 |
| --- | --- | --- | --- |
| 账本单任务 | 1000 位置 | 超过 → 409 `playlist_too_large`（账本自身也会 `ValidationError("invalid_playlist_size")`，`proxy/manual_save_jobs.py:204-207`） | `MAX_ENTRIES_PER_JOB = 1000`（`:30`）；`docs/OFFLINE_SAVE_JOBS.md:31` |
| 账本全局 | 10000 位置 / 1000 任务 | 超过 → 409 `capacity_exceeded`（`CapacityExceeded`，`:565-567`） | `MAX_POSITIONS=10_000`、`MAX_JOBS=1000`（`:27-28`） |
| 快照候选 | 100000 源位置 | `>100000` → `POSITION_LIMIT_EXCEEDED`；`==100000` → `SOURCE_LIMIT_REACHED`（"恰好满容量无法排除截断"） | `MAX_SOURCE_POSITIONS = 100_000`（`musicbox-service/playlist_manifest.py:20`），判定 `:222-224`；`docs/OFFLINE_PLAYLIST_MANIFEST.md` |

- 有效上限 = `min(1000, 剩余全局位置)`；`trackCount > 1000` 时**在创建阶段**就拒绝（不得创建"必然卡死"的任务）。
- 边界用例：`track_count == 1000` 且 `position_count == 1000`、且 `n=1001` 请求返回恰好 1000 条 → 允许创建；`track_count == 1001` → `playlist_too_large`。`track_count == 100000` → `playlist_too_large`（先于候选层的 `SOURCE_LIMIT_REACHED` 命中，因为网关以 1000 为有效上限）。
- 全局容量耗尽是真实运维风险（无删除 API），见 §11 Q3。

### 4.5 无效位置与重复位置

- **重复位置必须保留**：同一 `source_id` 出现在多个位置时，`entries` 逐位置保留（不合并、不标记 skipped）；账本"重复 `source_id` 会按原位置保留"（`docs/OFFLINE_SAVE_JOBS.md:31`），manifest 校验不为去重（`proxy/manual_save_jobs.py:173-236`）。
  - 结果预期：第 2 个相同曲目通常因 basename 相同返回 `conflict`（不覆盖），若元数据变化导致 basename 不同则允许两条都 `published`（`docs/OFFLINE_PLAYLIST_SAVE.md:78,290`）。**这两种都是正确行为，不是缺陷**。
  - UI 必须在位置列表里显式展示重复（同 `source_id` 出现多次），不得折叠。
- **无效位置不进入账本**：`error_code != null` 的位置使 `enumeration_complete=false`（候选 `:379-395`），创建被 409 `manifest_incomplete` 拒绝。UI 展示失败原因与 `source_error_code`，不提供"部分保存"按钮（fail-closed）。
- `missing`/`unavailable` 语义（账本允许的 `source_status`）本轮**不会产生**：因为快照只做 ID 枚举，不做元数据补全；记录这一点以免 review 误判"漏用字段"（`docs/OFFLINE_SAVE_JOBS.md:31` 列出的四个值中，本轮只用 `ok`）。
- 防御性处理：若账本中出现 `source_status != "ok"` 的位置（仅可能由非本合同的写者造成），执行器映射表（§5.2）规定 `rejected/invalid_target`，**绝不调用 `save_track`**。

### 4.6 `trackCount` 与实得位置数不一致

- 候选层：`track_count != len(positions)` → `status="incomplete"`, `error_code="TRACK_COUNT_MISMATCH"`（`musicbox-service/playlist_manifest.py:375-384`）。
- 网关层：再复核一次（§4.3.1 第 1 条），任何不一致 → 409 `manifest_incomplete`，`data.source_error_code = "TRACK_COUNT_MISMATCH"`。
- **不得**用 `positions` 的条数"修正"`trackCount`，也不得按 `trackCount` 截断/补齐；不做任何静默降级。

### 4.7 只读集成测试（接线前的硬门槛）

依据：`docs/OFFLINE_PLAYLIST_MANIFEST.md` "Do not wire a live adapter until an exact release artifact and digest is selected and reviewed, and a separately authorized read-only integration test can safely observe its side effects"；Lead 已拍板"先做一次只读集成测试观察副作用，再实施接线"。

**执行前提**：用户明确同意；维护窗口；`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false`（保存侧全关）；只用 GET；不写 NAS 目标/暂存；不改 `.env`（临时开关由部署窗口按 §12 的清单操作，或由 Lead 授权的最小改动方式完成，**不在本合同授权范围内**）。

**步骤**（记录到 `docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md`）：

1. **冻结 artifact**：记录 `git rev-parse HEAD`、`musicbox-service/requirements.txt` 内容、`sha256sum musicbox-service/netease_ext.py musicbox-service/playlist_manifest.py musicbox-service/netease_playlist_snapshot.py`、容器内 `importlib.metadata.version("NetEase-MusicBox")`（期望 `0.5.3`）、wheel 文件名与固定 sha256。
2. **基线只读观察**：`stat` 记录 Cookie 文件的 `size/mtime/mode`（**不读内容**）；记录 `sources-data` 相关目录与目标/暂存目录的 `find -newer` 基线；记录 musicbox 日志偏移。
3. **单次读取**：对一张**测试歌单**调用 `GET /api/v1/user/playlists/{id}/member-snapshot`（只读端点，flag 已开）；记录返回的 `status/track_count/position_count/source_fingerprint/source_versions`（**不记录歌单名、曲目名、Cookie、URL**）。
4. **第二次读取**：重复步骤 3，断言 `source_fingerprint` 与前一次一致（两次独立观测一致 = `enumeration_complete` 的可信度来源）。
5. **副作用观察**：
   - Cookie 文件 `stat` 是否变化（预期可能变化：上游 EAPI 请求会 `_persist_new_nmtid()` 条件写回，`docs/NETEASE_DELIVERY_EVIDENCE.md` 静态路径表 "Cookie 加载与登录写入" 行；**只记录"是否变化/变化类型"，不记录内容/摘要**）。
   - musicbox 访问日志：只应出现 account 与 `/api/v6/playlist/detail` 相关条目；**不得**出现 `/api/song/enhance/player/url`、`/weapi/v3/song/detail`、`/lyric`（说明没有偷偷做元数据/音频解析）。
   - 目标/暂存目录：`find -newer` 为空（没有写文件）。
   - 网关日志：无新增错误/拒绝码。
   - 服务/容器：无重启、无崩溃、无 5xx。
6. **绑定摘要供实现使用**：把 `{HEAD, wheel sha256, 实测版本, 文件 sha256, 观察日期, 测试 playlist_id（可用脱敏占位）, fingerprint 样本, 每次读取耗时}` 写入证据文档；后续实现必须引用该文档作为"artifact 已选型"的证据。

**通过判据**：artifact 版本/摘要符合 §4.1；两次 fingerprint 一致；`track_count == position_count`；无音频 URL/歌词/封面请求；无文件写入；无错误日志。任一不满足 → **停止接线**，回退到"选择/复核 artifact"。

**不证明**：provider 成员列表的完整性/实时性/授权、Cookie 未被服务端轮换、NAS 写入能力、音频有效性。证据文档必须原样写上这句。

---

## 5. 执行器映射

### 5.1 适配器形状

新增 `proxy/manual_save_playlist_jobs.py`（纯逻辑 + 薄服务层，无网络默认路径）：

```python
# 伪签名（实现者按此形状落地，内部可拆分）
def build_manifest(playlist_id: int, payload: dict) -> dict:
    """把 producer payload 复核并转成账本 manifest；不合法 raise ManifestError(fixed_code)。"""

def map_save_result(result: object) -> tuple[SaveResult, str]:
    """route.save_track 返回 → (账本 SaveResult, disposition∈{continue,abort,freeze})。
    永不抛异常：无法识别的输入返回 (COMMIT_UNKNOWN, 'freeze')。"""

def make_executor(engine=manual_save_route) -> Callable[[PositionSnapshot, Callable], SaveResult]:
    """返回 run_next 的 executor：只把 source_id 交给 engine.save_track 并映射结果。"""
```

`run_next` 的执行器签名固定为 `executor(position_snapshot, cancellation_check)`（`proxy/manual_save_jobs.py:655-671`）。适配器要点：

1. `position.source_id` 必须是纯数字字符串（创建时保证）；`int()` 失败或 `<=0` → 直接返回 `SaveResult(REJECTED, "invalid_target")`，**不调用引擎**。
2. `position.source_status != "ok"` → 同上，**不调用引擎**。
3. 调用 `engine.save_track(song_id)`（`proxy/manual_save_route.py:550`），**不传 `deps`**（生产默认 seam）。
4. 忽略 `cancellation_check`：单曲在飞不可取消（`docs/OFFLINE_PLAYLIST_SAVE.md:119,287`），取消在位置边界生效（下次 `run_next` 的 claim 前检查）。
5. 所有结果显式构造 `manual_save_core.SaveResult(outcome, reason, cleanup_pending=...)`，`audio_verified=False`、`production_ready=False`（账本要求这两个字段必须**严格为 False**，`proxy/manual_save_jobs.py:253-267`）。
6. 适配器内部**禁止**捕获后"重试"任何 `continue`/`abort` 码；只允许对 `busy` 做一次预检层的锁（§7.1），引擎返回 `busy` 时按 §5.2 处理。

### 5.2 逐码映射表（权威）

route 失败码全集来自 `proxy/manual_save_route.py:162-183` 的 `_MESSAGES`；continue/abort 分类以批次引擎为权威（`proxy/manual_save_playlist.py:42-64`，文档 `docs/OFFLINE_PLAYLIST_SAVE.md:52-53,127-145`）。

| # | `save_track` 返回（`ok`, `code`, `data.status`） | 账本 `Outcome` | 账本 `reason` | `disposition` | 说明 |
| --- | --- | --- | --- | --- | --- |
| 1 | `ok=True, code="ok", data.status="published"` | `published` | `bytes_published` | `continue` | 仅表示字节已发布；`audio_verified` 等仍 false（`docs/OFFLINE_SAVE_JOBS.md:51`） |
| 2 | `ok=False, code="conflict"` | `conflict` | `destination_conflict` | `continue` | 不覆盖、不改名、不换目录（`docs/OFFLINE_PLAYLIST_SAVE.md:145-151`） |
| 3 | `service_unavailable` | `rejected` | `service_unavailable` | `continue` | CONTINUE 码 |
| 4 | `no_quality_available` | `rejected` | `no_quality_available` | `continue` | 同上 |
| 5 | `quality_unverified` | `rejected` | `quality_unverified` | `continue` | 同上 |
| 6 | `quality_identity_mismatch` | `rejected` | `quality_identity_mismatch` | `continue` | 同上 |
| 7 | `trial_only` | `rejected` | `trial_only` | `continue` | 同上 |
| 8 | `download_target_untrusted` | `rejected` | `download_target_untrusted` | `continue` | 同上 |
| 9 | `download_failed` | `rejected` | `download_failed` | `continue` | 同上 |
| 10 | `integrity_mismatch` | `rejected` | `integrity_mismatch` | `continue` | 同上 |
| 11 | `invalid_audio` | `rejected` | `invalid_audio` | `continue` | 同上 |
| 12 | `tag_failed` | `rejected` | `tag_failed` | `continue` | 同上 |
| 13 | `disabled` | `failed` | `disabled` | `abort` | ABORT 码；环境前置（开关被关） |
| 14 | `config_invalid` | `failed` | `config_invalid` | `abort` | ABORT 码 |
| 15 | `busy` | `failed` | `busy` | `abort` | ABORT 码；正常不可达（§7.1），防御性映射 |
| 16 | `staging_unavailable`（route 码） | `failed` | `stage_unavailable`（账本既有 reason；原表字面 `staging_unavailable` 为笔误，P1 未新增该值） | `abort` | ABORT 码；既有 reason（`proxy/manual_save_jobs.py:58` 附近） |
| 17 | `publish_failed` | `commit_unknown` | `commit_unknown` | `freeze` | **发布异常=结果未知**：posix store 把发布期异常统一表述为"可能已可见、绝不重试/回滚"（`proxy/manual_save_posix.py:254-288`，`docs/OFFLINE_POSIX_STORE.md:34-36`），route 收敛为 `publish_failed`（`proxy/manual_save_route.py:792,806`） |
| 18 | `internal` | `commit_unknown` | `commit_unknown` | `freeze` | 引擎捕获到未预期异常，**无法排除**已产生副作用；fail-closed |
| 19 | `invalid_song_id` | `rejected` | `invalid_target` | `abort` | 契约被破坏（manifest 已保证正整数）；不调用引擎/或引擎拒收 |
| 20 | 非 Mapping / `ok` 非 bool / `code` 非 str / 未知 code / `ok=True` 但 `code!="ok"` / `data` 非 dict | `commit_unknown` | `commit_unknown` | `freeze` | "非法返回 → commit_unknown"，对齐账本对执行器无效返回的处理（`proxy/manual_save_jobs.py:675-679`） |
| 21 | 执行器抛异常（适配器自身 bug） | `commit_unknown` | `commit_unknown` | `freeze` | 账本自动转换；异常文本不保存（同上） |

补充规则：

- `outcome=commit_unknown` → `cleanup_pending=True`；其余全部 `cleanup_pending=False`（账本校验规则，`proxy/manual_save_jobs.py:268-284`）。
- `published_cleanup_pending` / `cancelled` 两个 Outcome **执行器永不返回**：`cancelled` 只由 `cancel_job` 产生（`proxy/manual_save_jobs.py:629-638`），`published_cleanup_pending` 由单文件核心的清理语义产生，route 不产生。
- **continue/abort 分类必须与批次引擎一致**，但 `publish_failed`/`internal` 在本合同上升级为 `freeze`（比 abort 更严格：停止推进且需人工对账）。测试断言：`ROUTE_CONTINUE_CODES == PLAYLIST_CONTINUE_CODES`；`ROUTE_ABORT_CODES ∪ FREEZE_CODES == PLAYLIST_ABORT_CODES`，且 `publish_failed`/`internal` 属 `freeze`（防止两份表漂移；不得要求 freeze 码也出现在 ABORT 集合里）。
- `disposition=="abort"` 时**不自动取消任务**：剩余位置保持 `queued`，由前端停止循环；环境修复后可继续（这是执行形态 B 相比整批引擎的关键增益，§7.5）。

### 5.3 逐位置结果的可持久化信息边界

- 账本只存 `Outcome + reason + cleanup_pending`（`proxy/manual_save_jobs.py:60-92` 的表结构），**不存**标题/歌手/basename/quality/cover/lyric 等执行器 detail。
- 因此：**刷新后**每个位置可见的信息 = 状态 + 固定原因（含失败码），不含单曲元数据。验收必须回到 NAS 文件系统核对（§10）。
- 若要持久化逐位置元数据，需要账本 schema v2（新增列或附表），**不在本轮范围**（账本会拒绝未知 schema，`docs/OFFLINE_SAVE_JOBS.md:53-58`）。

### 5.4 `commit_unknown` / `completion_unknown` 的显式对账

- 任何位置进入 `commit_unknown` 后，账本**自动冻结后续派发**（`proxy/manual_save_jobs.py:711-716`；`docs/OFFLINE_SAVE_JOBS.md:45`），job 汇总状态变为 `reconciliation_required`（`:887-888`）。
- UI 必须展示固定横幅："存在结果未知的位置，已停止推进。请核对 NAS 上是否已出现该文件后再人工处理。" 并提供位置列表（状态 `commit_unknown`）。
- **不做**：按文件是否存在自动判定、自动重试、自动标记成功/失败。账本明确禁止（`docs/OFFLINE_SAVE_JOBS.md:57`）。
- `completion_unknown`（结果写回 CAS 失败）同样冻结并要求人工处理（`docs/OFFLINE_SAVE_JOBS.md:35-45`）。

### 5.5 受审修订：账本 reason 枚举扩展

**问题**：账本当前为每个 `Outcome` 固定了 reason 白名单（`proxy/manual_save_jobs.py:39-58`），`FAILED` 只有 `stage_unavailable/staging_failed/cancel_check_failed`，`REJECTED` 只有核心层的 `invalid_*`。若不扩展，则 route 的 10 个 CONTINUE 失败码与 `disabled/config_invalid/busy` 只能塌缩成 1..3 个通用码，**"失败逐首可见（刷新后仍可见）"这一验收无法达成**（且会把 `no_quality_available` 误显示成"暂存不可用"这类错误信息）。

**修订内容（架构已选定；批准状态见 §11 Q1）**：

| Outcome | 现有 reason | 新增 reason |
| --- | --- | --- |
| `rejected` | 17 个 `invalid_*`/`unsupported_*`/`too_many_chunks` 等（`:42-49`） | `service_unavailable`, `no_quality_available`, `quality_unverified`, `quality_identity_mismatch`, `trial_only`, `download_target_untrusted`, `download_failed`, `integrity_mismatch`, `invalid_audio`, `tag_failed` |
| `failed` | `stage_unavailable`, `staging_failed`, `cancel_check_failed`（`:50`） | `disabled`, `config_invalid`, `busy` |
| 其它 | 不变 | 不变（`conflict/commit_unknown/published/cancelled` 的 reason 不动） |

**影响面（必须同步修改）**：

- `proxy/manual_save_jobs.py` 的 `_OUTCOME_REASONS`（`:39-58`）——**只加字符串，不改 DB schema/表结构**；`reason` 列没有 CHECK 枚举（`:76-92`），旧行继续合法，重开校验通过。
- `docs/OFFLINE_SAVE_JOBS.md:47-51` 汇总/结果含义小节：补充"reason 枚举由单文件核心扩展到 route 失败码"，并把合同号升为 `...-02`。
- `offline_tests/test_manual_save_jobs.py`：`test_all_core_outcomes_are_persisted_with_fixed_reasons`（`:441`）、`test_terminal_outcome_reason_and_cleanup_are_validated_on_read_and_reopen`（`:319`）需要按新枚举更新/参数化；新增"旧 DB（仅旧 reason）重开仍通过"的兼容用例。
- 新增测试：`FAILED`/`REJECTED` 的每个新 reason 都能写入并读回；未知 reason 仍被拒绝（fail-closed 不回退）。

**不批准时的退化路径**（合同仍可实施，但验收打折）：所有失败码映射到 `failed/stage_unavailable`（CONTINUE/ABORT 语义仍按 §5.2 的 `disposition` 执行），UI 刷新后只能显示通用"失败"，具体原因仅在当次响应可见。**这种退化不满足 §10 的"失败逐首可见（刷新后）"**，因此强烈建议批准。

---

## 6. 状态机与 UI

### 6.1 任务状态（账本汇总，`proxy/manual_save_jobs.py:887-901`）

| `state` | 产生条件 | 允许推进 | 允许取消 | UI 文案（固定） |
| --- | --- | --- | --- | --- |
| `queued` | 有 queued、无 running/cancel | 是 | 是 | 等待开始（共 N 首） |
| `running` | 有 running、无 commit_unknown、未请求取消 | **是**（另一个标签页可能正在推进；本页也可推进） | 是（只作用于未开始位置） | 正在保存… |
| `cancelling` | 有 running 且 `cancel_requested` | 否（`no_work`） | 否（已请求） | 正在取消…（当前这首会跑完） |
| `cancelled` | 全部位置 `cancelled` | 否 | 否 | 已取消 |
| `partial` | 有 `published`，但未全部 published、无 queued/running/commit_unknown | 否（无 queued） | 否 | 部分完成（有失败项） |
| `completed_with_issues` | 无 published、无 queued/running/commit_unknown、未全 cancelled | 否 | 否 | 已完成但有失败项 |
| `completed_bytes_reported` | 全部位置 `published` | 否 | 否 | 已完成（仅表示字节已发布，不表示可播放/已入库） |
| `reconciliation_required` | 任一位置 `commit_unknown` | 否（冻结） | 是 | **需要对账**：存在结果未知的位置 |

- `partial`/`completed_with_issues`/`reconciliation_required` **都不表示整单成功**（`docs/OFFLINE_SAVE_JOBS.md:47-51`）；`ok` 语义在本合同里只用于 HTTP 信封（请求是否被处理），**不得**把任务的成功与否塞进 `ok`。
- 所有任务快照固定带 `audio_verified=false`、`production_ready=false`、`import_ready=false`（`proxy/manual_save_jobs.py:902-911`），UI 与 API 都不得改写/隐藏这三个标志。

### 6.2 位置状态（`_POSITION_STATES`，`proxy/manual_save_jobs.py:36`）

| `state` | 固定中文 | 备注 |
| --- | --- | --- |
| `queued` | 等待 | 尚未执行 |
| `running` | 保存中 | 每任务至多一个（DB 唯一索引，`:87`） |
| `published` | 已发布 | 仅表示执行器报告字节发布（`docs/OFFLINE_SAVE_JOBS.md:51`） |
| `published_cleanup_pending` | 已发布（暂存待清理） | 执行器本轮不产生 |
| `conflict` | 已存在（未覆盖） | reason `destination_conflict` |
| `cancelled` | 已取消 | reason `cancelled_before_publish` |
| `rejected` | 未保存 | 按 reason 显示具体文案（§6.3） |
| `failed` | 失败 | 按 reason 显示具体文案 |
| `commit_unknown` | 结果未知 | 必须对账 |

位置 `reason` → 固定中文（新增表 `_MANUAL_SAVE_PLAYLIST_REASON_MESSAGES`）：

| reason | 文案 |
| --- | --- |
| `bytes_published` | 已发布 |
| `destination_conflict` | 目标已存在同名文件，未覆盖 |
| `cancelled_before_publish` | 已取消（未执行） |
| `commit_unknown` | 执行结果未知，需要人工对账 |
| `stage_unavailable` / `staging_failed` | 暂存不可用 |
| `cancel_check_failed` | 取消检查失败（按已取消处理） |
| `disabled` | 手动保存未启用 |
| `config_invalid` | 保存配置无效 |
| `busy` | 已有保存任务进行中 |
| `service_unavailable` | 音乐服务不可用 |
| `no_quality_available` | 没有可用的音质 |
| `quality_unverified` | 音质响应无法核验 |
| `quality_identity_mismatch` | 无法确认音质响应所属曲目 |
| `trial_only` | 仅取得试听片段，未保存 |
| `download_target_untrusted` | 下载目标未通过安全校验 |
| `download_failed` | 下载失败 |
| `integrity_mismatch` | 文件校验不一致 |
| `invalid_audio` | 音频文件无效 |
| `tag_failed` | 写入标签失败 |
| `invalid_target` | 输入无效，未执行 |

文案必须与 route 既有 `_MESSAGES`（`proxy/manual_save_route.py:162-183`）逐字一致；新增测试断言两张表对重叠码一致（防漂移）。未知 reason 一律显示固定兜底"未保存（原因未登记）"，**不得**回显原始文本。

### 6.3 UI 结构

位置：`webui-service/static/index.html` 的 `#page-save`（现有"手动保存单曲"卡片之后，`:287-300`）。新增 `<div class="card">` "手动保存歌单"，复用既有 class（`card/card-title/hint/btn/btn-row/stack/lx-item`）与 `role="status" aria-live="polite"` 风格。

元素（id 建议）：

| id | 用途 |
| --- | --- |
| `playlist-status` | 状态行（复用 `loadSaveStatus` 的文案风格，`app.js:1987-2016`） |
| `playlist-select` | `<select>` 账号歌单列表；disabled 当 `not_logged_in`/开关关 |
| `playlist-refresh-btn` | 重新拉取歌单列表 |
| `playlist-snapshot-btn` | 读取所选歌单快照（§3.3） |
| `playlist-snapshot-status` | "共 N 首，含 M 个重复位置" / 不完整原因 |
| `playlist-start-btn` | 创建任务并开始（disabled 除非 `enumeration_complete=true` 且开关开） |
| `playlist-job-panel` | 任务面板（hidden 直到有 job） |
| `playlist-job-state` | 任务状态徽标（§6.1 文案） |
| `playlist-progress` | "已处理 x/N：已发布 a · 已存在 b · 失败 c · 未执行 d" |
| `playlist-stop-btn` | 停止推进（仅停前端循环，不取消任务） |
| `playlist-cancel-btn` | 取消任务（§3.7，二次确认） |
| `playlist-positions` | 位置列表（位置号、source_id、状态、原因、是否重复） |
| `playlist-reconcile-banner` | 对账横幅（`reconciliation_required` 时出现） |

行为：

- 所有 API 调用继续走 `manualSaveApi`（`app.js:1951-1957`，自动带 `x-fnmusic-save: 1`）。
- 错误文案走新的 `playlistJobMessage(body, fallback)`：固定码 → 固定中文（§3.9.1），未知码 → `internal` 文案，**绝不**拼接服务端 `message` 之外的原始文本（沿用 `manualSaveMessage` 的保守风格，`app.js:1959-1964`）。
- 禁用态（必须同时覆盖）：
  1. `FNMUSIC_MANUAL_SAVE_ENABLED=false` 或 `playlist_disabled`：整卡片禁用，文案沿用"手动保存未启用…"（`app.js:2000`）。
  2. `not_logged_in`：只有"整歌单保存"禁用，单曲搜索保存保持既有行为。
  3. 快照 `enumeration_complete=false`：开始按钮禁用 + 固定原因。
  4. `state∈{reconciliation_required,cancelling}`：推进/开始禁用。
  5. 终态：显示汇总 + 位置列表，开始按钮恢复（可对同一歌单再建新任务，§6.5）。
  6. `saveState.enabled=false` 时不得发起任何 playlist 请求（与 `setSaveControlsEnabled` 一致的守卫，`app.js:1980-1985`）。
- `escapeHtml`（`app.js:1300`）用于所有来自服务端的字符串（歌单名、source_id 只需数字但仍统一转义）。

### 6.4 进度循环（前端）

```
start(playlistId):
  snapshot = GET snapshot?playlist_id=…      # 失败→固定文案，结束
  if !snapshot.enumeration_complete: 禁用开始, 结束
  job = POST jobs {playlist_id, source_fingerprint}   # 幂等：刷新后再点=同一个 job
  renderJob(job)
  await advanceLoop(job.job_id)

advanceLoop(jobId):
  while (running && !stopped):
    r = POST advance {job_id}
    if HTTP 409 busy/no_work:               # 另一标签页/上一请求仍在跑
        await sleep(backoff); refreshJob(); continue
    if HTTP 504 timeout:                    # 不重试！先查真相
        refreshJob(); 若该位置已不是 running/queued 状态变化则继续，否则停止
    if HTTP 200:
        renderRun(r.run); renderJob(r.job)
        if r.job.state 终态: break
        if r.run.disposition == "abort": 显示固定原因; break
        if r.run.disposition == "freeze": 显示对账横幅; break
        # continue：立刻进入下一首（无固定间隔）
  updateButtons()
```

- **不在循环内做 `setInterval` 固定轮询**：推进是自驱动的"请求-响应"节奏；只有 `busy/no_work/超时/网络错误` 才退避（建议 1s→2s→4s→8s→15s 上限）。
- "停止推进"只置前端标志，不发取消；下次进入页面/点"继续"可接着推进（账本无进度损失）。
- 页面关闭/刷新：循环停止；账本状态保留；重新打开页面时若 `localStorage` 有 `{playlistId, jobId}` 则 `GET job` 恢复面板；没有也能靠"重新读取快照 → 创建（派生幂等键）"拿回同一个 job（§6.5）。

### 6.5 刷新后进度不丢（两种恢复路径）

1. **localStorage 恢复**：保存 `{"playlist_id": int, "job_id": "<48hex>", "manifest_sha256": "..."}`，键 `fnmusic.save.playlist.job.v1`。`job_id` 不是访问凭据（`docs/OFFLINE_SAVE_JOBS.md:5-11`），但仍必须通过 L4；缓存只用于恢复 UI。
2. **幂等重建**（不依赖 localStorage 的兜底）：重新选歌单 → `GET snapshot` 拿指纹 → `POST jobs`（不给 `idempotency_key`，服务端派生 `p<playlist_id>-<fingerprint>`）→ 账本"同 scope/key 且规范字节相同返回原任务"（`docs/OFFLINE_SAVE_JOBS.md:13-22`）→ 得到同一个 `job_id` 与原有进度。**这是本设计能"刷新不丢"的根本原因**，实现与测试都必须覆盖。
3. 若歌单在刷新期间被改动：指纹变化 → 派生键变化 → 创建新任务；旧任务仍可查询（若 UI 保留了 job_id）。这是正确行为（用户确认的是旧清单），UI 提示"歌单已变化，上次任务仍在/已按新清单创建"。
4. `localStorage` 在 fnOS 桌面 iframe 中是否可用**待核实**；不可用时走路径 2，功能不受影响（只多一次点击）。实现必须捕获 `localStorage` 异常（隐私模式/配额）并降级到路径 2。

### 6.6 多标签页/多客户端

- 账本保证每任务至多一个 `running`（唯一局部索引，`proxy/manual_save_jobs.py:87`）：后到的推进请求得到 `no_work`，不会重复执行（`_claim_next` 在存在 running 时直接返回 None，`:717-720`）。
- 网关执行锁保证跨任务也只有一首在飞：后到的推进请求在**不 claim** 的前提下得到 409 `busy`（§7.1）。
- UI 对 `busy/no_work` 显示固定提示"另一窗口正在保存，本页稍后自动继续"并退避；不得并行发起第二个 advance。

---

## 7. 并发、预算与超时

### 7.1 每请求只推进一次 + 网关级执行锁

- 理由：单曲 worst case 接近 1200s 上限（§2.3）；N 首串行必然超时。
- 锁：`MANUAL_SAVE_PLAYLIST_LOCK`（模块级 `threading.Lock`），语义：
  - **获取位置在 worker 内、claim 之前**：拿不到锁 → 直接 `busy`，**不写账本、不消耗位置**（这是防止 `busy` 造成位置永久失败的关键）。
  - **释放位置在 worker 的 finally**：即使 handler 已 504 返回，worker 仍持锁跑完。
  - 既有 `/api/manual-save/track` 的 worker 也必须获取同一把锁（非阻塞，失败 → 409 `busy`，不调引擎）。这样 route 内部的 `_SAVE_LOCK`（`proxy/manual_save_route.py:158,562-568`）在单进程内不再可能被并发争用，`busy` 在映射表里成为**防御性不可达分支**。
- 跨进程：单进程假设来自部署纪律"不启动第二个 gateway"（交接 `E:\fnmusic\PROJECT_HANDOFF_20261101.md:219-228`）；如果被违反，route `_SAVE_LOCK` 仍是最后防线（返回 `busy` → 按 §5.2 记为 `failed/busy` 并 `abort`，不 freeze）。

### 7.2 轮询/退避建议

| 场景 | 节奏 |
| --- | --- |
| 刚完成一首（`run.status=completed`, `disposition=continue`） | 立即发下一次 advance（不留空转延迟） |
| `no_work` 且 job 非终态 | 2s 后重试（最多连续 5 次；仍 `no_work` 则停并提示"另一窗口正在推进或状态异常"） |
| `busy` | 1s→2s→4s→8s→15s 上限 |
| 网络错误/非 2xx | 同上退避；**不重放非幂等语义之外的推断**（advance 可安全重试，因为每位置至多执行一次） |
| 用户点"停止推进" | 立即停止循环，不再发请求 |
| 任务终态 | 停止 |

### 7.3 单次请求超时与 1200s 的关系

- 推进请求超时 = `MANUAL_SAVE_TIMEOUT = 1200`（`proxy/webui_gateway.py:106`），**不得**为整单设置更长的请求超时，**不得**为推进新增超过 1200 的预算。
- 超时的正确语义（本合同的明确取舍）：**504 不是取消**。worker 继续执行并最终 CAS 写回；前端收到 504 后**必须** `GET job` 再判定，不能盲目重试造成误判。原因：账本用 `running + claim_token` 做完成 CAS，迟到的完成会正常写入；错过 504 响应的客户端通过查询收敛（`docs/OFFLINE_SAVE_JOBS.md:35-45`）。
- 引擎内部预算不变：info ≤31s、lyric ≤62s、下载子进程 ≤580s、probe 30s、decode 300s、tag 120s（`proxy/manual_save_route.py:51-83`）。**本事务不新增/不放宽任何引擎预算**。

### 7.4 推进的幂等/重试语义（强性质）

- `run_next` 的 claim 在 `BEGIN IMMEDIATE` 事务内完成（`proxy/manual_save_jobs.py:702-766`）：并发连接不会重复 claim、不会跳过最早位置（`docs/OFFLINE_SAVE_JOBS.md:35-45`）。
- 完成写入用 `running + claim_token` CAS（`:767-806`）；旧 claim 的迟到完成不覆盖（`stale_completion`）。
- 结论：**任何重试都不会使同一位置执行两次**；推进请求可安全重试，最坏后果是"多推进了一首"（响应丢失时），UI 以 job 快照为准。这条必须写进实现注释与测试（并发/重试用例）。

### 7.5 预算/取消的传递方式

- 账本**没有**批量预算概念；本轮预算 = 每请求一首 + 1200s 请求上限 + 前端循环策略（§7.2）。
- 取消只在位置边界生效（`docs/OFFLINE_PLAYLIST_SAVE.md:119,287`）：`cancel_job` 只改 queued（`proxy/manual_save_jobs.py:629-638`），running 跑完按真实结果记录；`run_next` 的 `cancellation_check` 在当前一首执行期间不会被引擎消费（`save_track` 无取消钩子）。
- `disposition=abort` 与取消的区别：abort **不**改任务状态、不 cancel 剩余位置（可恢复）；取消是用户意图、不可逆地取消 queued。

---

## 8. 安全与脱敏

### 8.1 不得记录/回显

| 类别 | 规则 |
| --- | --- |
| 签名 URL / 音频直链 | 一律不落库、不落日志、不进响应（route 的 `_sanitize_manual_save_data` 已丢弃 `signed_url` 等未知键，`proxy/webui_gateway.py:658-724`；新增路径沿用同一白名单） |
| Cookie / token / 密码 / 认证头 | 网关不读、不传、不记；`X-Trim-*` 只做 claim 判定，**头值不得进日志**（G0 既有纪律，`docs/G0_TRUSTED_INGRESS.md:52-63`） |
| 本地路径（target/staging/DB 路径） | 不进账本、不进 playlist 响应、不进日志；DB 路径只在 `config_invalid` 固定码的**诊断日志**里出现"配置已加载/未加载"布尔，不打印路径 |
| 原始 claim | 不入库/不入日志/不入响应；DB 只存 `actor_scope`（哈希派生的隔离标签，§2.4） |
| 异常文本 | 一律不出现；固定码 + 固定中文（`proxy/manual_save_route.py:162-183`、`webui-service/static/app.js:1959-1964`） |
| 歌单名/曲目名 | 只在 `playlists` 列表响应中返回给已授权管理员，不写日志、不写账本 |

### 8.2 日志字段白名单

新增日志行只允许（仿 G0 的固定码风格，`docs/G0_TRUSTED_INGRESS.md:56-63`）：

```
fnmusic-manual-save-playlist code=<固定码> job=<job_id> position=<int> state=<位置状态> elapsed_ms=<int>
```

- 允许：固定码、`job_id`（48hex，不是访问凭据）、位置号、位置状态、耗时。
- 禁止：头值、`source_id`、歌单名、路径、URL、Cookie、异常文本、`actor_scope` 原文。
- 同一个码在短时间内重复时按 G0 的节流风格限流（`CHANNEL_REJECT_LOG_INTERVAL` 模式，`proxy/webui_gateway.py:66-70`）。

### 8.3 授权/G0 不得放宽

- 所有新端点必须先过 L2（accept 时 `SO_PEERCRED` 允许名单，`proxy/webui_gateway.py:1091-1112`，`docs/G0_TRUSTED_INGRESS.md:8-22`）、再走既有 strict head 解析与 L4（CSRF + canonical admin + userid）。
- **禁止**：新增任何"无 `x-fnmusic-save` 也能读"的分支；禁止新增 query/body 里的身份参数；禁止用 WebUI 侧新增路由绕过 501（`webui-service/app.py:1749-1758` 保持原样）。
- `webui-service/app.py` 的 501 覆盖 `/api/manual-save/{target:path}`，新增路径天然被覆盖（同文件 `:1750`），无需改动即安全。
- 新增出站请求（网关→`127.0.0.1:8770`）必须：
  1. 只允许 http loopback：`urlsplit(url)` 的 scheme 必须 `http`、host ∈ `{127.0.0.1, localhost, ::1}`、无 path/query（`.env` 配置 `FNMUSIC_MUSICBOX_URL`，`proxy/manual_save_route.py:88,92,328-334`）；
  2. 有界超时（`MANUAL_SAVE_PLAYLIST_READ_TIMEOUT`）、有界响应体（≤1 MiB，与 route 的 `MAX_JSON_BYTES` 同量级，`:36`）；
  3. 失败一律固定码，不重试风暴（最多 1 次重试需明确写入实现，默认不重试）。
- `ACTOR_SCOPE` 派生必须使用 L4 已校验的 claim；账本 `agent` 层不得接受客户端传入的 scope（§2.4）。

### 8.4 与本轮无关但必须在 reviewed diff 中确认的边界

- 不新增 socket、端口、进程、systemd 单元、容器能力；不改 `fnmusic-ext.service`（`fnmusic-ext.service:1-20`）。
- 不写 NAS 目标/暂存目录（快照路径不碰文件系统）。
- DB 文件安全：目录 mode 0700、属主为服务 euid；`fnmusic-ext.service` 的 `UMask=0077`（`:18`）保证新建 DB 文件 0600，不额外 chmod 依赖。

---

## 9. 测试计划

测试基线（交接 `E:\fnmusic\PROJECT_HANDOFF_20261101.md:118-146`，文档 `docs/OFFLINE_PLAYLIST_SAVE.md:233-282`）：手动保存五套 **562 passed / 2 skipped**；`offline_tests` **214 passed / 53 skipped / 196 subtests**；WebUI node **102/102**。本轮新增测试后**只增不减**；不得把 Windows 既有平台失败当作回归判据（交接 §5）。

### 9.1 单元测试（纯离线，无网络/无 NAS）

新增 `proxy/tests/test_manual_save_playlist_jobs.py`（或 `offline_tests/`，按依赖选择；适配层不 import route 时放 `offline_tests` 更纯）：

| 覆盖 | 用例要点 |
| --- | --- |
| manifest 构造 | 正常 12 位置；重复 source_id 保留；`track_count!=position_count` 拒绝；位置不连续拒绝；非正整数 ID 拒绝；>1000 拒绝；`enumeration_complete=false` 拒绝 |
| 字段取值 | `provider_id/collection_id/collection_version` 精确等于 §4.3.2；manifest 键集合严格 7 个（对齐 `_validate_manifest`） |
| 映射表 | §5.2 全部 21 行逐行断言 `(Outcome, reason, disposition)` |
| continue/abort 一致性 | `disposition` 分类 == `manual_save_playlist.PLAYLIST_CONTINUE_CODES`/`ABORT_CODES`；route 码集合覆盖完整 |
| 文案一致性 | 新 reason 中文表与 `manual_save_route._MESSAGES` 重叠码逐字一致 |
| 非法返回 | 非 Mapping、`ok` 非 bool、未知 code、`ok=True&code!="ok"`、`data` 非 dict → `commit_unknown` + `freeze` |
| 异常 | executor 抛异常 → 账本记 `commit_unknown`（用真实 `ManualSaveJobsLedger` + 临时目录，参照 `offline_tests/test_manual_save_jobs.py:467`） |
| 账本 reason 扩展 | 新 reason 可写可读；旧 DB（旧 reason）重开通过；未知 reason 被拒 |
| 幂等 | 同 key 同字节 → 同 job_id；同 key 不同字节 → `IdempotencyConflict`；派生键公式稳定 |
| 状态机 | 用真实账本 + fake executor 跑完 5 位置，断言各阶段 job `state` 与 counts；`commit_unknown` 后 `run_next` 返回 `no_work` |
| 容量 | 1000 位置可创建、1001 拒绝；`CapacityExceeded` 映射 409 |

### 9.2 契约测试（网关层，沿用既有打桩风格）

新增 `proxy/tests/test_manual_save_playlist_gateway.py`，复用 `proxy/tests/test_manual_save_gateway.py` 的 `FakeEngine`/`FakeClient`/请求构造函数（该文件已覆盖 head 严格解析、L4、超时、脱敏；新测试照此扩展）：

| 覆盖 | 用例要点 |
| --- | --- |
| 路径匹配 | 6 个路径 × 双前缀 × 方法严格性；方法不符不进入新处理器 |
| 授权 | 缺 CSRF / 非 admin / 缺 userid → 403 `forbidden`，**引擎 0 次被调用** |
| head 严格性 | 重复头、非法 Content-Length、`Transfer-Encoding`、超长头/体、未知 query 键 → 400 `invalid_request` |
| 查询解析 | `job_id`/`playlist_id` 非法格式 → 固定码；多余键拒绝 |
| 幂等 | 同 body 两次 POST → 同一 job（fake ledger 或真账本临时目录） |
| 执行锁 | 先占锁再调 advance → 409 `busy` 且 `run_next` 未被调用 |
| 超时 | fake executor 阻塞 → 504 `timeout`，网关仍可响应下一个请求（对齐 `test_track_timeout_returns_504_and_gateway_stays_responsive`，`:497`） |
| 脱敏 | executor 返回含 `signed_url/cookie` 的 detail → 响应中不出现；job 响应不含路径/md5/URL |
| 状态映射 | `commit_unknown`/`completion_unknown`/`stale_completion`/`no_work` → 规定 HTTP + disposition |
| 禁用 | 两个开关任一 false → 403，账本 0 次被构造/调用 |
| 账本异常 | `LedgerUnavailable`→503、`CapacityExceeded`→409、`CorruptLedgerError`→500 `internal` 且无细节 |
| app.py 收口 | 直接断言 `webui-service/app.py:1749-1758` 的占位仍是 501（现有测试已覆盖 `/api/manual-save` 前缀，新增子路径参数化即可） |

### 9.3 集成测试（fake provider + 临时账本 DB + fake 执行器）

| 覆盖 | 做法 | 禁止 |
| --- | --- | --- |
| 全链路 | fake musicbox HTTP（本地 socket/注入函数）+ 真 `ManualSaveJobsLedger`（`TemporaryDirectory`）+ fake `save_track`（返回各码序列） | 真实网络、真实 NAS、真实 ffmpeg/curl |
| 创建→推进→查询 | 5 位置，第 3 个返回 `download_failed`，断言 counts/状态/位置 reason | — |
| commit_unknown 冻结 | 第 2 个返回 `publish_failed`，断言 job `reconciliation_required`、后续 advance `no_work` 且执行器不再被调用 | — |
| 取消 | 推进 2 首后取消，断言 queued→cancelled、published 保留、running 保留语义 | — |
| producer 集成 | 用 `musicbox-service` 的纯函数 + 注入的响应对象（不联网），断言两次一致→complete、不一致→`SOURCE_CHANGED`、count 不符→`TRACK_COUNT_MISMATCH` | 真实账号/Cookie |
| 并发 | 两个线程同时 advance（同 job / 不同 job），断言至多一个执行器调用、另一个 409/`no_work` | — |

### 9.4 前端 node 测试（`webui-service/test_app_js.js`，零 npm 依赖）

- 扩展既有 DOM/fetch 桩（该文件用 `vm` 加载真实 `app.js`，`:1-30`）：
  - `advanceLoop` 遇到 `no_work`→退避→终态停止；遇到 `disposition=abort/freeze` 停止；
  - 504 后**先 GET job** 再决定，不直接重试；
  - `busy` 退避序列（可用假时钟）；
  - 固定文案表：新错误码 → 固定中文，未知码 → `internal` 文案；
  - `localStorage` 抛异常时降级到"幂等重建"路径；
  - 禁用态矩阵（§6.3）逐个断言按钮 disabled。
- 运行：`cd webui-service && node test_app_js.js`，基线 102/102 不得下降。

### 9.5 本测试计划**不证明**的部分（必须写进实现报告与证据文档）

1. 真实 NAS 上的落盘、文件系统语义（btrfs/link/fsync）、并发/断电持久性；
2. 音频有效性、可解码、标签/封面/LRC 实际正确（需真机验收 §10 与既有单曲验收）；
3. 官方曲库索引可见/可播放；
4. provider 成员列表的完整性/实时性/授权（只证明"两次本地观测一致"）；
5. WebUI 容器重启/网关重启后的行为（无真机演练）；
6. Cookie 轮换对其它播放/登录会话的影响；
7. 跨进程/多实例网关的安全性（单实例是部署假设）。

---

## 10. 验收标准

前置：G0 已就绪（`docs/G0_TRUSTED_INGRESS.md:8-22`；真机证据同文档 §4）；单曲链路已通过验收（`docs/MANUAL_IMPORT_PLAN.md:191-205`）；用户明确同意该次真实写入；`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=true`、`FNMUSIC_MANUAL_SAVE_ENABLED=true`；目标/暂存目录沿用已验收配置（交接 `E:\fnmusic\PROJECT_HANDOFF_20261101.md:151-184`）。**验收本身不授权改 `.env`/部署/重启**。

用例：**一张包含重复曲目、成员数 ≥5 的测试歌单**（用户选定）。

| # | 验收项 | 判定 |
| --- | --- | --- |
| A1 | 只读预检 | `GET snapshot` 返回 `track_count == position_count`、`enumeration_complete=true`、指纹稳定（连续两次一致） |
| A2 | 创建幂等 | 同歌单连续两次创建 → 同一个 `job_id`；不消耗额外容量 |
| A3 | 逐首推进 | 每次 advance 至多执行一首；进度单调；前端展示"已处理 x/N" |
| A4 | 逐首核对 | 对每个 `published` 位置：NAS 上存在目标文件、basename 与响应一致、`ffprobe` 可解码、标签（title/artist/album/track）与封面/LRC 状态符合 route 报告、`mode` 与目标目录既有文件一致、staging 目录**清空**（无残留 `fnmusic-manual-save-*`） |
| A5 | 重复位置 | 重复 `source_id` 的每个位置都在列表里；第 2 个通常 `conflict`（不覆盖）；若两条都 `published` 需解释为 basename 不同（允许） |
| A6 | 失败逐首可见 | 制造至少一个可复现失败位置（如 VIP/无版权曲目 → `no_quality_available`/`trial_only`）；刷新页面后该位置仍显示具体固定原因；整单不标成功 |
| A7 | 部分完成语义 | 若有失败，job `state∈{partial,completed_with_issues}`；`ok=True` 的 HTTP 响应**不得**被 UI 表述为"整单成功" |
| A8 | 刷新不丢 | 中途刷新/关闭页面再打开：进度与位置结果不变（§6.5 两条恢复路径至少一条可用） |
| A9 | 取消 | 推进中途取消：queued → `cancelled`，已发布保留，running 跑完后如实记录；无文件回滚 |
| A10 | 开关关闭 | `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false` 时：列表/快照/创建/推进全部 403（仅整歌单开关关 → `playlist_disabled`；总开关关 → `disabled`）；**无账本写入、无引擎调用、无 NAS 写**（检查日志与 DB mtime） |
| A11 | 直连 8774 | `curl 127.0.0.1:8774/api/manual-save/playlist/...` → 501（`webui-service/app.py:1749-1758` 收口）；不得出现执行路径 |
| A12 | 越权与伪造 | 无 CSRF 头/非 admin/缺 userid/普通用户直连 socket → 403 或被 L2 关闭；跨 `actor_scope` 用他人 `job_id` → 404 |
| A13 | 脱敏（**限定新增 playlist 链路**）| 新增端点的响应/新日志/DB 中无签名 URL、Cookie、token、**目标目录或暂存路径**；DB 只含 §4.3.2 的 manifest 与状态列。既有 `status` 端点回显 `target_dir/staging_dir` 属已验收行为，不在本条范围 |
| A14 | 容量/上限 | 1001 首/1001 个成员位置以上 → 409 `playlist_too_large`（1000 首合法，不要求跑完）；账本容量（1000 任务/10000 位置）打满时创建 → 409 `capacity_exceeded` |
| A15 | 测试基线 | 五套手动保存 + `offline_tests` + node 全部只增不减；新增测试全绿；无真实网络/NAS 参与离线测试 |
| A16 | `commit_unknown` 冻结 | 构造一个 `commit_unknown` 位置后：job 变 `reconciliation_required`、后续 advance 返回 `no_work`（HTTP 200）、UI 显示"需要对账"固定横幅、取消仍可用；**不得**自动重试、不得按文件是否存在判定成功（§5.4） |

验收记录必须包含：命令、exit code、计数、NAS 上逐位置文件清单（可用 `find` 输出）、失败位置证据、DB 中 job 快照（脱敏后）。**代码审查、本地测试、真机验收分别记录，不互相替代**（`docs/MANUAL_IMPORT_PLAN.md:219-243`）。

---

## 11. 风险与未决问题

> **状态（2026-11-01，Lead 已决）**：Q1 **批准**（账本 reason 枚举扩展；已完成、已测、已审查）；Q2 **本轮不开放 recover 端点**（`worker_stopped` 无法由 API 验证，stuck running 需人工介入）；Q3 **本轮不新增删除 API**（容量清理另立项）。以下保留当时决策依据，供后续复核。

### Q1 是否批准对已交付账本做"reason 枚举扩展"（§5.5）？

- **需要决定**：允许 `proxy/manual_save_jobs.py` 的 `_OUTCOME_REASONS` 增加 13 个固定 reason（10 个 `rejected` + 3 个 `failed`），并同步修订 `docs/OFFLINE_SAVE_JOBS.md` 合同号与 `offline_tests` 用例？
- **推荐**：批准。否则 §5.2 的失败码只能塌缩到通用 `stage_unavailable`，刷新后"失败逐首可见 + 原因正确"无法满足（会把 `no_quality_available` 显示成"暂存不可用"）。
- **影响**：不改 DB schema、旧数据继续可读；风险面是"受审合同的语义范围从单文件核心结果扩展到 route 结果"，需要 reviewer 重点看 reason 白名单与测试。
- **退化路径**：不批准时全部失败映射为 `failed/stage_unavailable`，UI 只显示当次会话的具体原因，§10 A6 降级为"会话内可见"。

### Q2 网关进程在推进中途崩溃导致 `running` 卡死，本轮是否开放恢复通道？

- **需要决定**：本轮是否提供 `recover` 端点（内部调用 `recover_stale_running(actor_scope, job_id, worker_stopped=True)`，`proxy/manual_save_jobs.py:809`）？
- **现状**：按 §3.8 本轮不开放 → 崩溃后该位置永久 `running`，`_claim_next` 因存在 running 位置返回 `None`（`:717-720`），job 无法推进也无法靠取消清除（`cancel_job` 保留 running，`:629-638`）。UI 只能显示"任务卡在运行中，需要人工处理"。
- **推荐**：本轮**不开放**（`worker_stopped` 无法由 API 验证，账本契约明确要求调用方显式声明；贸然开放会把"未知副作用"错误地转成 `commit_unknown` 后再被误当已对账）。下一轮若开放，必须补：网关进程代际标记（启动 epoch 落盘）+ 该端点仅在"本进程从未处理过该 job"或"epoch 已变化"时放行 + 独立测试。
- **影响**：极低概率（单进程、单请求、1200s 上限内）但一旦发生该 job 报废，需要人工介入 DB（本轮不提供）。

### Q3 账本容量（10000 位置 / 1000 任务，无删除 API）是否本轮处理？

- **需要决定**：是否本轮新增"删除任务"或提高容量？（`MAX_POSITIONS = 10_000`、`MAX_JOBS = 1000`，`proxy/manual_save_jobs.py:27-28`；无删除方法）
- **现状**：位置行不回收，**累计 10 个满额 1000 首歌单后**创建会永久 409 `capacity_exceeded`；正常使用（每单几十首）约可容纳数百单，但一旦打满只能改代码/删 DB。
- **推荐**：本轮**不新增删除 API**（会扩大账本契约与数据安全面：删除 = 销毁审计证据），改为：创建失败时返回固定码 + UI 明确提示"任务账本已满，请联系维护者"，并把"容量清理"列为下一个独立需求（需要定义保留期/审计要求）。
- **影响**：真实验收不受阻（用例只建 1–2 个任务）；长期运维需要一次人工清理方案。

---

## 12. 交付物清单

> 只列路径与职责，供 implementer 按图施工；**本轮（architect）不改任何代码**。目录相对仓库根 `E:\fnmusic-ext-dev`。

### 12.1 新增文件

| 路径 | 职责 |
| --- | --- |
| `proxy/manual_save_playlist_jobs.py` | 适配层：producer HTTP 客户端（loopback 校验/超时/有界响应）、manifest 构造与复核、`save_track`→`SaveResult` 映射、job 服务封装（DB 路径/开关/scope 派生）、固定码与固定文案表 |
| `proxy/tests/test_manual_save_playlist_jobs.py` | §9.1 单元/集成测试（临时目录真账本 + fake 执行器） |
| `proxy/tests/test_manual_save_playlist_gateway.py` | §9.2 网关契约测试 |
| `musicbox-service/netease_playlist_snapshot.py` | 新调用方：两次独立观测 + `snapshot_from_responses`/`compare_snapshots` + payload 组装；**不修改** `playlist_manifest.py` |
| `proxy/tests/test_musicbox_playlist_snapshot.py` | 新调用方纯函数测试（注入响应对象，不联网；参照 `proxy/tests/test_musicbox_playlist_manifest.py:383-390` 的按路径加载方式） |
| `tools/netease_playlist_observe.py` | §4.7 只读观察脚本（**未来由用户授权后单独运行**；本 architect 阶段不运行、不创建运行时副作用） |
| `docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md` | §4.7 观察记录（artifact 冻结值、两次指纹、副作用观察、不证明项） |

### 12.2 修改文件

| 路径 | 修改点 |
| --- | --- |
| `proxy/webui_gateway.py` | 6 个 `MANUAL_SAVE_PLAYLIST_*_PATHS` 常量 + 对应 `_is_*_request` + handler（status/list/snapshot/create/get/advance/cancel）；`_MANUAL_SAVE_PLAYLIST_MESSAGES`/`_MANUAL_SAVE_PLAYLIST_HTTP_STATUS`；`MANUAL_SAVE_PLAYLIST_LOCK` 与既有 track handler 共享；`_claim_manual_save_authorized_claims`（返回 claims，供 scope 派生）；`_handle` 分发新增 6 个分支（在 `_validate_head` 之前，`:1310-1330` 风格）；脱敏器复用/新增 |
| `proxy/manual_save_jobs.py` | §5.5 的 `_OUTCOME_REASONS` 扩展（只加固定字符串） |
| `offline_tests/test_manual_save_jobs.py` | 更新 reason 枚举相关用例 + 新增兼容/拒绝用例 |
| `docs/OFFLINE_SAVE_JOBS.md` | 合同号升版 + 说明 reason 枚举由核心扩展到 route 失败码 |
| `musicbox-service/app.py` | 新增 `GET /api/v1/user/playlists/{playlist_id}/member-snapshot`（flag 默认关；只读） |
| `musicbox-service/requirements.txt` | `NetEase-MusicBox>=0.5.3` → `==0.5.3`（§4.1 前置条件） |
| `docs/NETEASE_DELIVERY_EVIDENCE.md` | 追加"已钉版本（`==0.5.3`）"与 CI 摘要校验说明 |
| `webui-service/static/index.html` | `#page-save` 新增"手动保存歌单"卡片（§6.3） |
| `webui-service/static/app.js` | 歌单选择/快照/创建/推进循环/取消/恢复/固定文案/禁用态 |
| `webui-service/test_app_js.js` | §9.4 node 测试扩展 |
| `.env.example` | 新增 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false`、`FNMUSIC_MANUAL_SAVE_JOBS_DB=`（含安全约束注释），紧随既有手动保存段（`.env.example:123-127`） |
| `docs/MANUAL_SAVE_PLAYLIST_JOB.md` | 本文件；实现完成后追加"实现状态/测试计数/验收记录"小节（不得改动合同语义） |

### 12.3 不得修改

| 路径 | 理由 |
| --- | --- |
| `proxy/manual_save_route.py` | 逐首执行器语义已审；本合同只调用 `save_track`（`:550`） |
| `proxy/manual_save_playlist.py` | 批次引擎原语；本轮不执行、只复用其码分类 |
| `proxy/manual_save_core.py` | 单文件协议 |
| `musicbox-service/playlist_manifest.py` | 候选模块保持原样，避免推翻其离线测试证据 |
| `webui-service/app.py` | 501 收口不得放宽（`docs/G0_TRUSTED_INGRESS.md:87-92`） |
| `fnmusic-ext.service`、`docker-compose.yml` | 不改部署形态/端口/权限 |
| 3 个受保护未跟踪文件 | 交接 §2.3（`E:\fnmusic\PROJECT_HANDOFF_20261101.md:54-60`） |

### 12.4 实现顺序与门槛

| 阶段 | 内容 | 门槛/前置 |
| --- | --- | --- |
| P0 | 本合同的独立审查（reviewer 绑定哈希 PASS） | 本文；**P0 PASS 是所有后续阶段的严格前置**。本轮实际执行偏差：P1/P2/P4 在 P0 结论出来前已并发落地，后以"绑定哈希 diff 审查 + P0 发现项回改"补救（见 §14），此偏差不重复 |
| P1 | 账本 reason 扩展 + `offline_tests` 更新（纯离线） | Q1 批准（§11） |
| P2 | `netease_playlist_snapshot.py` + 纯函数测试 + `member-snapshot` 端点（flag 默认关）+ `requirements.txt` 钉版本 | 无 NAS 写入 |
| P3 | **§4.7 只读观察测试**（部署 producer、开只读 flag、跑 `tools/netease_playlist_observe.py`、写证据文档） | **用户授权 + 维护窗口**；artifact 证据齐备；`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false` |
| P4 | `proxy/manual_save_playlist_jobs.py` 适配层 + 单元/集成测试（fake producer payload） | 可与 P2/P3 并行编写；接线需 P3 PASS |
| P5 | 网关 6 端点 + 契约测试 | P3 PASS + P1 完成 + **P4 适配层完成**（P5 import 它；P4 自身只需单测，不依赖 P3） |
| P6 | 前端卡片 + node 测试 | P5 完成 |
| P7 | 真实 NAS 整歌单验收（§10） | 用户明确同意写库 + 选定测试歌单 + P5/P6 完成 |
| P8 | 交付评审/提交（由 Lead 执行；implementer 不 commit） | P7 记录 |

---

## 13. 证据索引

### 13.1 账本

| 事实 | 引用 |
| --- | --- |
| 常量/上限/标识符规则 | `proxy/manual_save_jobs.py:24-38` |
| `_OUTCOME_REASONS` 封闭白名单 | `proxy/manual_save_jobs.py:39-58` |
| 表结构（jobs/positions，SQL CHECK） | `proxy/manual_save_jobs.py:60-92` |
| 异常类型与固定码 | `proxy/manual_save_jobs.py:107-133` |
| manifest 校验（7 键封闭、位置连续、1..1000） | `proxy/manual_save_jobs.py:173-236` |
| `create_job` / 幂等 / 容量 | `proxy/manual_save_jobs.py:541-600` |
| `get_job` / `cancel_job` | `proxy/manual_save_jobs.py:601-654` |
| `run_next`（claim→事务外执行→CAS） | `proxy/manual_save_jobs.py:655-766` |
| `_claim_next` 冻结/单 running 守卫 | `proxy/manual_save_jobs.py:702-766`（`commit_unknown` :711-716、running :717-720、cancel :721-723、incomplete :724-726） |
| `_complete` CAS 与 `completion_unknown` | `proxy/manual_save_jobs.py:767-806` |
| `recover_stale_running(worker_stopped=True)` | `proxy/manual_save_jobs.py:809-848` |
| 汇总状态与三个 false 标志 | `proxy/manual_save_jobs.py:849-911`（状态 :887-901、标志 :902-911） |
| 账本契约文档 | `docs/OFFLINE_SAVE_JOBS.md:5-11,13-22,31,35-45,47-51,53-58,59-70` |

### 13.2 批次引擎 / 单曲执行器

| 事实 | 引用 |
| --- | --- |
| `save_playlist` 签名与串行/锁/预算 | `proxy/manual_save_playlist.py:569-613`；文档 `docs/OFFLINE_PLAYLIST_SAVE.md:12-27,84-126` |
| CONTINUE/ABORT 码 | `proxy/manual_save_playlist.py:42-64`；`docs/OFFLINE_PLAYLIST_SAVE.md:52-53,127-151` |
| 重复位置合法/不预去重 | `docs/OFFLINE_PLAYLIST_SAVE.md:78,290` |
| 在飞不可取消 | `docs/OFFLINE_PLAYLIST_SAVE.md:92,119,287` |
| `save_track` 入口 | `proxy/manual_save_route.py:550-572` |
| `_MESSAGES` 固定文案 | `proxy/manual_save_route.py:162-183` |
| 引擎预算常量与 1200s 说明 | `proxy/manual_save_route.py:45-83` |
| `_SAVE_LOCK` busy | `proxy/manual_save_route.py:158,560-568` |
| 配置/路径校验（0700、同文件系统） | `proxy/manual_save_route.py:381-419` |
| 发布/冲突/异常收敛 | `proxy/manual_save_route.py:761-806`；posix store `proxy/manual_save_posix.py:254-288`；`docs/OFFLINE_POSIX_STORE.md:34-36` |
| `TARGET_ID="manual-save"` | `proxy/manual_save_route.py:86` |
| musicbox URL 配置键 | `proxy/manual_save_route.py:88,92,328-334` |

### 13.3 网关 / G0 / WebUI 收口

| 事实 | 引用 |
| --- | --- |
| `MANUAL_SAVE_*_PATHS`、上限、CSRF、userid 开关、超时 | `proxy/webui_gateway.py:81-108` |
| 固定文案表 / HTTP 状态表 | `proxy/webui_gateway.py:112-148` |
| strict head 解析 | `proxy/webui_gateway.py:542-580` |
| L4 授权 | `proxy/webui_gateway.py:581-600` |
| body 读取上限/超时 | `proxy/webui_gateway.py:602-623` |
| 结果脱敏白名单 | `proxy/webui_gateway.py:658-741` |
| worker 线程 + 超时 | `proxy/webui_gateway.py:749-765` |
| 三个既有 handler | `proxy/webui_gateway.py:773-1024` |
| 分发顺序（新处理器必须在 L3 之前风格） | `proxy/webui_gateway.py:1310-1330` |
| L2 accept 准入 | `proxy/webui_gateway.py:1091-1112,1113-1130` |
| G0 L1–L6 分层与拒绝码 | `docs/G0_TRUSTED_INGRESS.md:8-22,24-50,51-92` |
| G0 残余风险 R1（单 claim 可伪造） | `docs/G0_TRUSTED_INGRESS.md:150-166` |
| WebUI 501 收口 | `webui-service/app.py:1749-1758` |
| 前端 API 封装/状态/按钮禁用/单曲流程 | `webui-service/static/app.js:6,34,1943-2210` |
| 保存页结构 | `webui-service/static/index.html:287-300` |

### 13.4 快照候选 / artifact / 交接

| 事实 | 引用 |
| --- | --- |
| 候选常量（`MAX_SOURCE_POSITIONS=100_000`、schema/provider） | `musicbox-service/playlist_manifest.py:17-28` |
| `snapshot_from_responses` / 限制 / 无效位置保留 | `musicbox-service/playlist_manifest.py:179-272`（限制 :222-224、无效 :240-258） |
| `snapshot_fingerprint` | `musicbox-service/playlist_manifest.py:274-298` |
| `compare_snapshots` 判定与失败码 | `musicbox-service/playlist_manifest.py:333-399`（:353-367、:375-395） |
| 候选文档（"未选型不得接活适配器"、指纹非签名、上限与截断边界） | `docs/OFFLINE_PLAYLIST_MANIFEST.md`（Scope / Fingerprint / Provider and side-effect gate 三节） |
| 账号歌单/曲目路由 | `musicbox-service/app.py:278-299` |
| 自建/收藏判定字段 | `musicbox-service/netease_ext.py:443-456` |
| preview 的 1000 上限与两次比对（仅作对照，不复用） | `musicbox-service/netease_ext.py:511,540-543,671-801`（:757-771） |
| 播放镜像（禁止用作导入清单） | `musicbox-service/app.py:456-484`（`/public-playlists/{id}` 与 `/tracks`） |
| artifact 固定证据 | `docs/NETEASE_DELIVERY_EVIDENCE.md:7-25` 及其静态路径表（Cookie/NMTID、`playlist_songlist`、音频 URL） |
| 部署形态 | `docker-compose.yml:30-33`（8770 仅本机）、`fnmusic-ext.service:8-18`（root/WorkingDirectory/UMask） |
| 交接：G5 路线与硬约束 | `E:\fnmusic\PROJECT_HANDOFF_20261101.md:185-202` |
| 交接：测试基线与命令 | `E:\fnmusic\PROJECT_HANDOFF_20261101.md:118-146` |
| 交接：代码地图与关键语义 | `E:\fnmusic\PROJECT_HANDOFF_20261101.md:70-92` |
| 交接：纪律（默认关闭/不部署/不记密钥） | `E:\fnmusic\PROJECT_HANDOFF_20261101.md:219-228` |
| 整歌单验收要求 | `docs/MANUAL_IMPORT_PLAN.md:206-218` |
| 单曲验收要求（复用前提） | `docs/MANUAL_IMPORT_PLAN.md:191-205` |
| 既有手动保存环境变量块 | `.env.example:123-127` |

### 13.5 待核实清单（实现/观察阶段必须回填）

| 待核实项 | 谁核实/何时 |
| --- | --- |
| 容器内实测 `NetEase-MusicBox` 版本是否 `0.5.3` | §4.7 只读观察测试（P3） |
| 登录 Cookie 文件的实际容器内路径与是否会被 EAPI 写回 | §4.7（只记录 stat 变化，不读内容） |
| 上游 `playlist/detail` 对 `n=1001` 的实际行为（是否截断/报错） | §4.7（两次读取 + count 校验） |
| `get_account_info()` 返回是否含 `code=200` 与 `account.id`（候选模块要求） | §4.7（失败即 fail-closed，不得合成响应） |
| fnOS 桌面 iframe 内 `localStorage` 是否可用 | P6 真机冒烟（不可用则走 §6.5 路径 2） |
| 浏览器对超长 fetch（接近 1200s）的中断行为 | P7 验收（504/网络错误均按 §7.3 处理） |

---

## 14. 实现状态与审查记录（2026-11-01）

| 阶段 | 状态 | 交付 | 实测证据 |
| --- | --- | --- | --- |
| P1 账本 reason 扩展 | ✅ 完成 | `proxy/manual_save_jobs.py`（+13 reason，未改 DB schema）、`offline_tests/test_manual_save_jobs.py`、`docs/OFFLINE_SAVE_JOBS.md`（-02） | 该测试文件 21 passed / 29 subtests |
| P2 快照 producer | ✅ 完成（默认关闭） | `musicbox-service/netease_playlist_snapshot.py`、`proxy/tests/test_musicbox_playlist_snapshot.py`、`musicbox-service/app.py` 只读端点、`requirements.txt` 钉 `==0.5.3`、`docs/NETEASE_DELIVERY_EVIDENCE.md` | 新增 41 例；开关与保存开关解耦 |
| P4 适配层 | ✅ 完成（零生产接线） | `proxy/manual_save_playlist_jobs.py`、`proxy/tests/test_manual_save_playlist_jobs.py` | 207 例 |
| P3 只读 artifact 观察 | ⏳ 待用户授权 + 维护窗口 | — | 需真实 NAS 只读观察（§4.7） |
| P5 网关 6 端点 | ⏳ 待实现 | — | 依赖 P3 PASS + P4 |
| P6 管理页入口 | ⏳ 待实现 | — | 依赖 P5 |
| P7 真实整歌单验收 | ⏳ 待用户同意写库 | — | 依赖 P5/P6 |

**审查记录**

- **合同审查（P0）：FAIL → 已修订本版**。3 个 P0：① 流程前置缺失（实现先于 P0 结论落地）→ §12.4 已写明 P0 为严格前置并记录本轮补救方式；② artifact 校验接口未定义（网关不可能做进程内版本检查）→ §4.1 第 2 条 / §3.4 步骤 5 改为"版本断言在 producer，网关只按 `artifact_error_code` 映射"；③ P3 只读开关不可达（producer 依赖保存开关，与"保存侧全关"互斥）→ §4.2 明确解耦 + 取值来源。
- **实现审查（P1/P2/P4）：FAIL → 已回改**。P0：producer 失败信封 `ok` 语义与 consumer 不兼容 → §4.2 明确"观测 payload `ok` 恒 true，仅 503 关闭为 false"；P1：P3 开关互斥（同上）、HTTP seam 跟随重定向（改为禁用/校验最终 URL）、非 complete 状态映射（按 §3.3 分流）；P2：`::1`/http-only、响应体 ≤1 MiB、§5.2 row16 勘误、reason 文案别名、`n=1001` 请求参数断言。
- **P5 必补清单**（继承自两份审查，缺一不可）：
  1. §4.3.1 的 **7 项**网关复核（5 条结构 + provider/schema 固定值 + 计数一致且不静默修复）；
  2. §3.4 步骤 5 的网关 artifact 复核（`source_artifact.version=="0.5.3"`、`dist_info_present==true`、`artifact_error_code` 为空；P4 目前只在 unsupported 分支用 `artifact_error_code`，complete 分支未查 `source_artifact`）；
  3. §3.9.1 固定码与 HTTP 表（含 `playlist_unsupported`/`source_changed`/`playlist_too_large`/`not_logged_in`/`playlist_unavailable`/`source_artifact_mismatch`）与 §3.9.2 `source_error_code` 白名单（未知一律丢弃为 `""`）；
  4. §3.3 只读预检 + §3.4 步骤 3 指纹绑定（body fp vs 新快照 fp → 409 `source_changed`）与派生幂等键；
  5. §3.6 `detail` 缺口决策（P4 的 executor 只返 `SaveResult`，不含已脱敏 detail；不得回传原始 data）；
  6. `_MANUAL_SAVE_PLAYLIST_REASON_MESSAGES`（含 `stage_unavailable`/`staging_failed`）；
  7. 网关级执行锁与既有单曲端点串行化（避免锁序死锁，`busy` 不得吃掉位置）；
  8. advance 的**五类** run status（`completed`/`commit_unknown`/`no_work`/`stale_completion`/`completion_unknown`）HTTP 映射——P4 的 `_advance_report` 对 `no_work`/`stale_completion` 的 disposition 仍为 `None`，须按 §3.6 表补 `continue`/`freeze`；
  9. gateway 契约测试文件 `proxy/tests/test_manual_save_playlist_gateway.py`；
  10. 部署侧导出键（见下）。
- **环境变量落盘限制（本轮实测）**：`.env.example` 在 DSH 工具层属于凭据围栏路径（`.env*`），任何文件工具读写均被拒绝，因此本轮的 `.env.example` 增补未被执行。**必须由操作者在外部编辑器补入**，或在 P5 用不含凭据的示例文档替代。
  需补的三个键（按 `.env.example` 既有注释风格）：
  ```dotenv
  # 整歌单保存（管理页）开关，默认关闭
  FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false
  # 任务账本 SQLite 路径（私有目录、root 可写）
  FNMUSIC_MANUAL_SAVE_JOBS_DB=
  # 只读歌单成员快照端点开关，默认关闭；与上面两个保存开关解耦
  FNMUSIC_PLAYLIST_MANIFEST_READONLY=false
  ```
  部署生效路径（P5/部署清单）：`container/env_flag.sh` 的 `export_source_env()` 导出键列表追加 `FNMUSIC_PLAYLIST_MANIFEST_READONLY`（`musicbox-service` 的 `[program:musicbox]` 无 `environment=`，仅写 `.env` 不会进入进程环境；`entrypoint.sh` 在启动 supervisord 前调用该函数，故追加即可被继承）。
- 本表与审查记录**不证明**真实 NAS 行为、真实网易云响应、音频有效性或生产接入。

---

*本文档为唯一交付物；实现阶段另行产生代码/测试改动（见 §12）。*

