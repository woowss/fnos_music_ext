# 手动保存元数据交付（FNMEXT-METADATA-DELIVERY-20261101）

本文件记录手动单曲保存的元数据交付补齐：封面域名白名单（新增 `p2.music.126.net`）
与可观测的 `cover_error` 归类，以及歌词/歌曲信息的有界超时+重试。所有改动只影响
元数据附加项，不改任何失败码、HTTP 状态表、`cover_status`/`lyric_status` 枚举，
也不改变音频发布语义（元数据失败绝不丢弃已发布的音频）。

## 1. 封面 host 白名单

`NETEASE_COVER_HOSTS = frozenset({"p1.music.126.net", "p2.music.126.net"})`

- 精确匹配、禁止通配 `*.music.126.net`；未观测 host（`p3/p4/pass/m801`、子域、
  尾点根域等）仍整体拒绝，不会发起任何抓取。
- p2 与 p1 复用同一套约束，没有任何放宽：
  - `http`/`https` 一律升级为 `https`，host `casefold` 后精确匹配；
  - 只允许 443（显式 80/443 归一化掉，其它端口拒绝）；
  - 禁止 userinfo、fragment、非默认端口、控制符、path 不以 `/` 开头、解析失败；
  - DNS 全部地址必须 `is_global`（最多 8 个、去重、非 public/空即拒绝）；
  - 只连接已校验地址（`_PinnedCoverConnection` 固定地址 + SNI host），不跟随重定向；
  - MIME 声明与 magic 必须一致、≤10 MB、尺寸头校验、有界 ffmpeg 解码。

### 准入流程（新增 host 时）

1. 用真机只读抓取统计该 host 出现次数（本次：12 首真实歌曲 `al.picUrl`，p1=7、p2=5，
   无 p3/p4；p2 DNS 全 global，IPv6 `240e:…` + IPv4 `182.242.223.99`，pinned TLS 正常）。
2. 确认该 host 可被 `socket.getaddrinfo` 解析为公网地址，且 HTTPS 证书与 host 匹配。
3. 只把**精确 hostname** 加入 `NETEASE_COVER_HOSTS`；禁止前缀/通配/根域/尾点写法。
4. 补测试：`test_cover_host_allowlist_is_exactly_p1_and_p2`、
   `test_cover_url_accepts_p2_and_upgrades_to_https`、未观测 host 拒绝用例。
5. 记录证据与日期到本文件；不接受"先加白名单再观察"。

### 可观测字段 `cover_error`

`cover_error` 仅当 `cover_status == "error"` 时非空，其余状态恒为 `""`（引擎与网关
双重保证；conflict 响应也恒为 `""`）。固定枚举：

| 取值 | 触发点（离线可复现） |
|---|---|
| `host_untrusted` | `_normalize_cover_url`：host 不在精确白名单（如 p3/pass/子域/尾点） |
| `url_invalid` | `_normalize_cover_url`：scheme/userinfo/端口/fragment/path/控制符/解析失败等非 host 原因 |
| `address_untrusted` | `_public_cover_addresses`：DNS 抛错、地址非法、非 public、空、>8 个 |
| `http_error` | `_default_cover_fetch` 所有地址失败且原因为网络/证书/非 200（`cover_fetch_http_error`）；或 fetch 阶段裸 `OSError`/TLS/HTTPException |
| `payload_invalid` | 响应结构/MIME/编码/大小/尺寸不合法（`cover_response_invalid`/`cover_mime_invalid`/`cover_too_large`/`cover_encoding_invalid`/`cover_dimensions_invalid`/`cover_dimensions_unsafe`/`cover_fetch_payload_invalid`） |
| `decode_failed` | 本地校验或 ffmpeg 解码失败（`cover_decode_failed`，或 `verify_cover` 阶段 OSError/ValueError） |
| `embed_failed` | 打标签后 `validate_tagged` 期望有 attached_pic 但实际没有 |
| `unknown` | 其它无法归类的异常（含 `cover_fetch_unknown`） |

`_default_cover_fetch` 在多地址全部失败时只抛固定原因串
`cover_fetch_http_error` / `cover_fetch_payload_invalid` / `cover_fetch_unknown`
（仍是 `ValueError`），不再抛 `type(last_error).__name__`，原因串中不含
host/URL/IP/token。

网关 `_sanitize_manual_save_data`：`cover_error` 进入白名单；`cover_status != "error"`
→ 强制 `""`；`error` 且缺失/非法 → `"unknown"`。失败码/HTTP 状态/messages 均未改动。

## 2. 歌词与 info 的有界超时 + 重试

真机证据（2026-11-01，kotomi）：同一 musicbox 服务 `/lyric` 出现 27 s 级延迟
（search 33–42 s）；旧版 15 s `API_TIMEOUT` 导致 `lyric_status=error`。

| 常量 | 值 | 说明 |
|---|---|---|
| `API_TIMEOUT` / `INFO_TIMEOUT` | `15.0` | info 单次超时（沿用原值） |
| `INFO_MAX_ATTEMPTS` | `2` | info 最多 2 次 |
| `INFO_RETRY_DELAY` | `0.5` | info 重试间隔 |
| `INFO_TOTAL_BUDGET` | `31.0` | info 软预算（见下；上界由 timeout×次数保证，最坏 30.5s） |
| `LYRIC_TIMEOUT` | `30.0` | 歌词单次超时（默认 callable 绑定 30s） |
| `LYRIC_MAX_ATTEMPTS` | `2` | 歌词最多 2 次 |
| `LYRIC_RETRY_DELAY` | `1.0` | 歌词重试间隔 |
| `LYRIC_TOTAL_BUDGET` | `62.0` | 歌词软预算（见下；上界由 timeout×次数保证，最坏 61s） |

- `_default_http_json(url, *, timeout=API_TIMEOUT)` 新增可选 timeout，默认仍 15s；
  `_default_lyric_http_json` 绑定 `LYRIC_TIMEOUT`；`_default_info_http_json` 绑定
  `INFO_TIMEOUT`（`_save_locked` 默认 callable 显式使用它；值仍 15s，行为不变）。
- 内部 `_retry_transient(operation, *, max_attempts, retry_delay, total_budget,
  sleep=None, monotonic=None)`：`sleep`/`monotonic` 默认在调用时解析模块 `time`
  属性（测试 monkeypatch `route.time.sleep` 即不真实等待）。
- **`total_budget` 是软预算**：真正的时长上界由每次请求自身的 timeout（15s/30s）
  与最多 2 次尝试保证；`*_TOTAL_BUDGET` 只用于在 deadline 已过后阻止"再次尝试/
  睡眠"，不是硬性总时长截断，也不替代单次请求 timeout。
- 可重试：`TimeoutError`、`ConnectionError`、非 `HTTPError` 的 `OSError`、
  `HTTPError` code ∈ {408,429} 或 ≥500。
- 不可重试：其它 HTTPError 4xx、`ValueError`/`UnicodeError`/JSON 解析、
  `_SaveFailure`、其它异常。
- 失败语义：重试耗尽或不可重试 → 歌词 `lyric_status="error"`、info
  `service_unavailable`；**绝不**把超时/失败映射成 `lyric_status="unavailable"`
  （`unavailable` 只表示服务 200/ok 但无 lyric/tlyric/空白）；绝不伪报成功；
  不新增含 URL/host/token 的日志或异常文本。
- **有界**：**上界由单次请求自身 timeout（15s/30s）+ 最多 2 次尝试保证**——
  info 最坏 15+0.5+15=30.5s、lyric 最坏 30+1+30=61s；`INFO_TOTAL_BUDGET`/
  `LYRIC_TOTAL_BUDGET` 只是软预算兜底（deadline 已过则不再尝试/睡眠），不改变网关
  1200s 保存超时；`search` 与质量阶梯不在此重试范围内。
- **防御性分支（F6）**：`_save_locked` 中 `elif cover_status in {"embedded",
  "preserved"}`（原约 :709-711）是防御性/默认不可达代码：`expected_cover` 为 False
  时 `cover_path` 为空且无源封面，`cover_status` 不可能已是 embedded/preserved；
  保留它只为防止未来改动引入矛盾状态，正常路径不会命中。
- **尺寸归类（F1）**：`_validate_cover_dimensions` 抛出的 `cover_dimensions_unsafe`
  （超宽/超高/总像素超限）已映射为 `payload_invalid`，与 `cover_dimensions_invalid`
  同属"尺寸非法"类，可由压缩超尺寸 PNG 用例复现。

## 3. UI 展示

`webui-service/static/app.js` 的保存结果展示按 `data.cover_error` 细化封面失败文案
（仅 `cover_status === "error"` 时）：

| `cover_error` | 文案 |
|---|---|
| `host_untrusted` | 封面域名不在已验证列表（p1/p2），已跳过抓取 |
| `address_untrusted` | 封面地址未通过公网校验，已跳过抓取 |
| `url_invalid` | 封面地址格式无效，已跳过抓取 |
| `http_error` | 封面获取失败 |
| `payload_invalid` | 封面内容无效 |
| `decode_failed` | 封面解码失败 |
| `embed_failed` | 封面嵌入失败 |
| 缺失/`unknown`/其它 | 封面获取或嵌入失败（保持原通用文案） |

UI 绝不回显 host/URL；歌词文案不变；其它状态沿用原 `coverLabels`。

## 4. 验收命令

```bash
# 三件套 + 元数据
PYTHONUTF8=1 <py> -m pytest \
  proxy/tests/test_manual_save_route.py \
  proxy/tests/test_manual_save_gateway.py \
  proxy/tests/test_manual_save_delivery_guard.py \
  proxy/tests/test_manual_save_metadata.py -q

# 手动保存全部测试文件
PYTHONUTF8=1 <py> -m pytest proxy/tests/test_manual_save_*.py -q

# 离线管线（不得减少）
PYTHONUTF8=1 <py> -m pytest offline_tests -q

# 网关与前端
PYTHONUTF8=1 <py> -m pytest webui-service/test_app_js.py -q
cd webui-service && node test_app_js.js
```

`<py>` 指部署测试环境使用的 Python 解释器；Windows 上必须设置 `PYTHONUTF8=1`。

## 5. 残余风险与限制

- **白名单是精确枚举**：新 CDN host 出现时必须按第 1 节流程重新取证并加白，否则
  该曲封面安全失败（音频仍发布，`cover_error=host_untrusted`）。这是有意的
  fail-closed 取舍，不做通配或"先抓后验"。
- **p2 复用 p1 约束**：本次只验证了 DNS 全 global 与 pinned TLS 正常，未做长期
  可用性监控；p2 若变更证书/地址策略，封面会退化为 `http_error`/`address_untrusted`。
- **歌词延迟**：30s×2 只能覆盖已观测的 27s 级延迟；若服务端持续 >61s，仍会
  `lyric_status=error`（不重试耗尽预算、不误报 downloaded 成功）。
- **重试不覆盖 payload**：200/ok 但 lyric 为空 → `unavailable`（语义不变）；
  非 JSON/结构错误不重试，直接 `error`。
- **重试与下载/标签共享总时长**：歌词+info 最坏约 91.5s，与下载预算 580s、
  解码/标签预算叠加后仍低于网关 1200s，但极慢服务下会接近上限。
- **`cover_error` 只是归类**：它不泄露 host/URL/IP，排障只能定位到类别；具体
  CDN 需要看审计日志（本模块不记录 URL）。

## 6. Addendum A1：`image/jpg` 声明别名规范化

### 真机证据（2026-11-01 03:0x，隔离 /tmp，只读探针）

- 同一首歌 `al.picUrl` host 在 1 小时内由 **p2 轮换回 p1**，证明"精确白名单"策略
  正确（不是白名单写错，也不是需要通配）。
- 音频 PASS（published、jymaster/reported、126,459,367、md5 `8ab58d1b…`、
  FLAC 192k/24bit、标签、mode 0600、staging 空）。
- 歌词 PASS（`lyric_status="published"`，同名 LRC 944 字节；30s+2 次重试生效）。
- 封面 FAIL：`cover_status="error"`、`cover_error="payload_invalid"`。p1 URL 的
  pinned GET 实际返回：`status 200`、`Content-Type: image/jpg`、
  无 `Content-Encoding`、**446480 字节**、magic `ffd8ffe0…`（JPEG）。
  旧校验要求 `declared == sniffed`（`image/jpg` vs `image/jpeg`）→
  `cover_mime_invalid` → `payload_invalid`。这是服务端**非标准别名**，内容本身合法。

### 别名表与参数文法（封闭，不扩表）

```python
_COVER_DECLARED_MIME_ALIASES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",   # 真机观测到的非标准别名
    "image/png": "image/png",
    "image/webp": "image/webp",
}
_COVER_MIME_PARAM_TOKEN_RE = "^[A-Za-z0-9._-]{1,32}$"
```

`_normalize_cover_declared_mime(value)`（纯函数）：

1. 非 `str` → `""`；
2. 按 `;` 切段：首段 `strip().casefold()` 必须**精确命中**别名表（逗号多值
   `image/jpg, image/png` 自然不命中）→ 否则 `""`；
3. 参数最多 1 个；无参数合法；唯一允许参数名 `charset`（casefold）；其值
   `strip()` 后必须匹配 `^[A-Za-z0-9._-]{1,32}$`（值本身被忽略）；拒绝其它参数名、
   引号值、空值、多个 `;`、参数含 `=`/逗号/空格/斜杠/星号、末尾空参数（`image/jpg;`）；
4. 返回 canonical MIME（`image/jpg`→`image/jpeg`），否则 `""`。

`_default_cover_fetch` 只把 `Content-Type` 的**声明值**经该函数规范化，随后仍强制：
声明非空、magic sniff ∈ {jpeg,png,webp}、`declared == sniffed`，否则
`cover_mime_invalid`（→ `payload_invalid`）。**绝不**只信 `Content-Type`，绝不放宽到
`image/*`；`_COVER_MIME_EXTENSIONS` 不变。

`_fetch_cover_asset` 注入 seam 只接受 **canonical** MIME：
`mime.strip().casefold()` ∈ {image/jpeg,image/png,image/webp} 且等于 sniff；
注入 `image/jpg` → `cover_mime_invalid`（不自动别名化；默认 fetch 已规范化）。

### 判据正/反例（对应测试 `proxy/tests/test_manual_save_metadata.py`）

| 输入 | 结果 | 测试 |
|---|---|---|
| `image/jpg`+JPEG / `IMAGE/JPG` / ` image/jpg ` / tab OWS | 接受→`image/jpeg` | `test_cover_declared_mime_normalizes_case_and_ows`、`test_default_cover_fetch_normalizes_image_jpg_to_jpeg` |
| `image/jpg; charset=binary`、`;charset=binary`、` ; charset = binary`、`CHARSET=UTF-8` | 接受 | `test_cover_declared_mime_accepts_single_charset_token` |
| `image/jpeg`/`png`/`webp` 对应 magic | 接受 | `test_default_cover_fetch_normalizes_image_jpg_to_jpeg`、`_cover_declared_mime_normalizes_case_and_ows` |
| `image/jpg`+PNG magic | 拒绝 `cover_mime_invalid`→payload_invalid | `test_default_cover_fetch_rejects_image_jpg_with_png_magic` |
| `text/html`、`""`、缺失 header、`image/*`、`image/pjpeg`、`image/x-png` | 拒绝 | `test_default_cover_fetch_rejects_non_image_or_missing_content_type`、`test_default_cover_fetch_mime_invalid_is_payload_invalid`、`test_cover_declared_mime_rejects_other_or_malformed_parameters` |
| `image/jpg, image/png`、`image/jpg; foo=bar`、`charset="binary"`、`image/jpg;`、`charset=`、双参数 | 拒绝 | `test_cover_declared_mime_rejects_other_or_malformed_parameters` |
| 注入 `image/jpg` / canonical `image/jpeg` | 拒绝 / 接受 | `test_fetch_cover_asset_injected_mime_must_be_canonical` |
| p1 `image/jpg` 全链路 | embedded + `.jpg` 封面文件 + `cover_error==""` | `test_image_jpg_alias_pipeline_embeds_cover_with_jpg_extension` |
| 别名表封闭性 | 仅 4 个精确键 | `test_cover_declared_mime_alias_table_is_closed` |

### 真机验收判据 C1（A1 更新后）

C1 通过需同时满足（p1 或 p2 均可）：

- `cover_status == "embedded"`；
- `cover_embedded is True`；
- `cover_error == ""`；
- ffprobe 能看到 attached_pic 封面流（`disposition.attached_pic == 1`）。

### 残余风险

- `image/pjpeg`、`image/x-png` 等其它历史别名仍 **fail closed**（
  `cover_mime_invalid`→payload_invalid）；如真机再观测到，需按本流程单独取证加白，
  不做通配或前缀匹配。
- 重复 `Content-Type` 头取 `http.client` 的**首个**值；若服务端把真实类型放在第二个
  头，仍会 fail closed（安全优先）。
- 参数只允许单个 `charset=<token>`；`boundary=` 等其它参数一律拒绝（图片响应不需要）。
- 该规范化只放宽"声明值与 magic 的别名差异"，不跳过 MIME→magic 一致性校验、不改变
  大小/编码/尺寸/解码检查。
