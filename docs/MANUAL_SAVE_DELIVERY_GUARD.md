# 手动保存交付守卫（FNMEXT-DELIVERY-GUARD）

本文件记录手动单曲保存（`proxy/manual_save_route.py`）的交付守卫判据、失败码、
常量、测试方式与残余风险。守卫分两部分：

- **Part 1 · 音质语义**：音质阶梯只允许在"明确证据"下降档，服务端上报的档位
  永远不当作失败，也不翻译成营销名称。
- **Part 2 · 下载目标**：签名媒体 URL 必须逐跳通过 URL 策略、DNS/地址策略和
  有界探测，最终传输固定使用探测选定的单个地址。

所有判据只使用固定失败码与固定中文消息；任何异常文本、URL、host、IP、token
都不会进入日志、异常或返回结果。

## 1. 音质判据（Part 1）

阶梯：`QUALITY_ORDER = ("jymaster", "hires", "lossless", "exhigh")`，逐档顺序执行。
下列任一条命中即整体失败，**绝不继续更低档**；不做同档重试。

| # | 观测 | 动作 |
|---|---|---|
| 1 | `http_json` 抛 `OSError`/`TimeoutError`（含 5xx/超时/连接失败；`urllib.error.HTTPError` 是 `OSError` 子类） | 中止 `service_unavailable` |
| 2 | `http_json` 抛其它异常（JSON 解析等） | 中止 `quality_unverified` |
| 3 | payload 非 Mapping | 中止 `quality_unverified` |
| 4 | `payload["ok"] is not True` | 中止 `quality_unverified` |
| 5 | `payload["data"]` 非 Mapping | 中止 `quality_unverified` |
| 6 | `data.code` 缺失/非 int（bool 非法）/∉{200,404} | 中止 `quality_unverified` |
| 7 | `data.id` 缺失/非法/≠请求 `song_id`；或 `data.songId` **存在**但非法/≠`id`/≠请求 | 中止 `quality_identity_mismatch`（`songId` 键不存在 = 正常） |
| 8 | `code==404` 且 `url` 缺失/None/"" | 明确不可用 → 继续下一档 |
| 9 | `code==404` 但 `url` 为其它值 | 中止 `quality_unverified` |
| 10 | `code==200` 且 `url` 非 str/空/非 http(s) | 中止 `quality_unverified` |
| 11 | `code==200` 且 `freeTrialInfo` **键缺失** | 中止 `quality_unverified`（缺失 ≠ null） |
| 12 | `code==200` 且 `freeTrialInfo is None` | 完整曲目证据 → 进入 level 归一化 |
| 13 | `code==200` 且 `freeTrialInfo` 为非空 Mapping | 明确试听 → 记录 trial，继续下一档 |
| 14 | `code==200` 且 `freeTrialInfo` 为空 Mapping/其它类型 | 中止 `quality_unverified` |
| 15 | `code==200` 且 `level` 为合法 token（`strip().casefold()` 后匹配 `^[a-z0-9][a-z0-9._-]{0,63}$`） | **成功**（无论是否等于请求档）→ `quality_actual=<token>`、`quality_actual_status="reported"` |
| 16 | `code==200` 且 `level` 缺失/空白/非 str/字符集或长度非法 | **成功，档位未知** → `quality_actual=""`、`quality_actual_status="unknown"` |

阶梯耗尽：全为 #8 → `no_quality_available`；至少一次 #13 且无成功 → `trial_only`。

`level` 永不导致失败：请求 `jymaster` 而服务端上报 `lossless`（2026-11-01 真机
证据）是成功路径，`quality_requested="jymaster"`、`quality_actual="lossless"`。

### 结果字段（成功与 conflict 响应都带）

| 字段 | 语义 |
|---|---|
| `quality_requested` | 产出该 URL 的阶梯请求档（canonical token，非实际档） |
| `quality_actual` | 服务端 `data.level` 的 `strip().casefold()` 原始 token；不可解析时为 `""` |
| `quality_actual_status` | `"reported"` 或 `"unknown"` |
| `quality_fallback` | bool；`selected_index > 0` 或（`reported` 且 `quality_actual != quality_requested`）时为 true |

`fee`/`payed`/`rightSource`/`levelConfuse`/`message`/`br`/`sr`/`type` 不参与音质判定，
也不用来宣称权益、完整性或音质；`level` 不做任何翻译/映射。

## 2. 下载目标守卫判据（Part 2）

### 2.1 URL 规范化（`_normalize_media_url`，每一跳都重做）

- 只允许 `http`/`https`（scheme casefold）；长度 1..4096。
- 含 NUL/CR/LF → `download_failed`（参数框架完整性）。
- 含空格/反斜杠/控制符/非 ASCII → `download_target_untrusted`。
- `urlsplit` 后禁止：userinfo、fragment（含空 fragment `#`）、非默认端口、IP 字面量
  host、空 host、host 含尾点/非 ASCII/超 253 字符/标签语法非法、path 不以 `/` 开头。
- 显式默认端口（`http:80`/`https:443`）允许并规范化掉；非默认端口整体拒绝。
- query 原样保留（不解码、不重编码）；返回重建后的规范化 URL。

### 2.2 DNS / 地址策略（`_resolve_media_addresses`，每一跳）

- `resolve(host, port)` 默认真实 `socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)`；
  测试可注入（`SaveDeps.media_resolve` 或 `_default_download(resolve=...)`）。
- 结果按 IP 去重保序；数量必须 1..32，0 或 >32 → `download_target_untrusted`。
- 每个地址都必须通过：`ipaddress` 解析成功、`is_global` 为真，且不是
  private/loopback/link-local/multicast/reserved/unspecified、不带 zone-id，
  并显式拒绝 IPv4 `100.64.0.0/10`、`198.18.0.0/15`、IPv6 `::ffff:0:0/96`（IPv4-mapped）、
  `64:ff9b::/96`（NAT64）。**任一地址不合法 → 整体拒绝**（不跳过坏地址）。
- DNS 抛异常 → `download_failed`；策略拒绝 → `download_target_untrusted`。

### 2.3 逐跳探测（`_probe_media_hop` / `_follow_media_redirects`）

- 全链最多跟随 **3** 次重定向（第 4 次 3xx → `download_failed`）；重定向回环 →
  `download_failed`；https → http 降级 → `download_target_untrusted`（http → https 允许）。
- 每 hop 按解析顺序逐个地址串行探测，单 hop 最多 **4** 个地址，全失败 →
  `download_failed`。每次探测只 `--resolve host:port:<单个地址>`（IPv6 用 `[addr]`）。
- 探测 curl 配置（stdin，`curl -K -`，argv 恒为 `[CURL_BIN, "-K", "-"]`）：
  `url`（规范化）、`output="/dev/null"`、UA/Referer、`silent`、`show-error`、`head`、
  `max-redirs=0`、`max-time=min(15, 剩余)`、`resolve`、`proto="=http,https"`、
  `write-out="%{http_code} %{redirect_url}"`；**不得**含
  `location`/`fail`/`continue-at`/`max-filesize`。
- 解析 stdout 最后一个非空行 → `(status, redirect_url)`：
  - `200`/`206` → 直接选定该地址为最终目标，不触发 range 回退；`redirect_url` 为空是
    正常路径（真机零重定向）。
  - `301/302/303/307/308`：`redirect_url` 必须非空且为绝对 http(s) URL → 校验下一跳
    URL 策略 + DNS；空/非法 → 视为该地址探测失败（换下一地址）；下一跳策略拒绝 →
    终止性 `download_target_untrusted`（不换地址重试）。
  - HEAD 返回其它状态（如 403/405/501/204）→ 同地址回退一次 range GET 探测
    （去 `head`、加 `range="0-0"`，其余同上）；仍非 2xx/3xx → 该地址探测失败。
  - curl rc≠0/超时/无可解析状态 → 该地址探测失败（换下一地址）。
  - 每次探测 `max-time=min(15, 剩余)`；剩余 <1s → `download_failed`。
- 探测阶段记住选定的 `(host, address, url)` 供最终传输固定使用。

### 2.4 最终传输（`_default_download`）

- 签名 URL、目标路径均只经 curl stdin 配置传递，argv 永不含 URL。
- `--resolve` **固定为探测选定的地址**；传输阶段不换地址（D-36 最终口径）。
- 配置：`url`（选定跳的规范化 URL）、`output=destination`、UA/Referer、`fail`、
  `silent`、`show-error`、`max-redirs=0`（无 `location`）、`max-filesize=MAX_BYTES`、
  `continue-at="-"`、`max-time`（沿用 180/580 预算）、`resolve`、`proto="=http,https"`、
  `connect-timeout=15`、`write-out="%{http_code}"`。
- 尝试循环：最多 4 次、共享 580s 传输预算、每次保留 `continue-at="-"` 续传；
  **探测只做一次，重试不重复探测**；探测阶段有独立预算，不消耗传输预算。
- 结果判定：rc0 且 200/206 → 继续既有 size/md5 完整性校验；rc0 且 3xx → 删除目标
  文件 + `download_target_untrusted`；rc0 且其它/不可解析 → 删除目标文件 +
  `download_failed`；rc≠0 → 保留部分文件重试同一地址；耗尽 → `download_failed`；
  预算耗尽 → `download_failed`（不回退档位、不扩预算）。

## 3. 失败码

| code | 中文固定消息 | 网关 HTTP |
|---|---|---|
| `service_unavailable` | 音乐服务不可用 | 503 |
| `no_quality_available` | 没有可用的音质 | 502 |
| `quality_unverified` | 音质响应无法核验 | 502 |
| `quality_identity_mismatch` | 无法确认音质响应所属曲目 | 502 |
| `trial_only` | 仅取得试听片段，未保存 | 502 |
| `download_target_untrusted` | 下载目标未通过安全校验 | 502 |
| `download_failed` | 下载失败 | 502 |
| `integrity_mismatch` | 文件校验不一致 | 502 |
| 其它既有码 | 见 `_MESSAGES` | 见 `_MANUAL_SAVE_HTTP_STATUS` |

已删除概念：`quality_level_mismatch`（音质档位与服务端上报不一致**不是**失败）。

## 4. 常量

| 常量 | 值 | 说明 |
|---|---|---|
| `QUALITY_ORDER` | `jymaster, hires, lossless, exhigh` | 音质阶梯 |
| `MAX_BYTES` | `1 << 30` | 单文件上限 |
| `DOWNLOAD_MAX_REDIRECTS` | `3` | 全链最多跟随重定向次数 |
| `DOWNLOAD_PROBE_TIMEOUT` | `15.0` | 单次探测上限 |
| `DOWNLOAD_MAX_MEDIA_PROBE_ATTEMPTS` | `4` | 单 hop 最多探测地址数 |
| `DOWNLOAD_MAX_MEDIA_ADDRESSES` | `32` | 单 hop 允许的解析地址数上限 |
| `DOWNLOAD_MAX_MEDIA_URL_CHARS` | `4096` | URL 字符上限 |
| `DOWNLOAD_TIMEOUT` | `180.0` | 单次传输尝试上限 |
| `DOWNLOAD_SUBPROCESS_TIMEOUT` | `580.0` | 传输总预算（探测另有同值独立预算） |
| `DOWNLOAD_MAX_ATTEMPTS` | `4` | 传输尝试次数上限 |

## 5. 实际 curl 调用序列

一次成功下载（零重定向、HEAD 可用）：

1. `subprocess.run([CURL_BIN, "-K", "-"], input=<probe 配置>, ...)`
   —— `output="/dev/null"`、`head`、`write-out="%{http_code} %{redirect_url}"`；
   返回 `200 ` → 选定该地址。
2. `subprocess.run([CURL_BIN, "-K", "-"], input=<transfer 配置>, ...)`
   —— `output=<destination>`、`fail`、`max-redirs=0`、`continue-at="-"`、
   `write-out="%{http_code}"`；返回 `200`/`206` → 校验 size/md5。

有重定向时，第 1 步对每个 hop 重复（最多 3 次跟随，第 4 次 3xx 失败），每次只探测
当前 hop 的地址；探测成功后才进入第 2 步，且传输固定使用最后一跳选定的地址。
HEAD 被拒（403/405/501/204 等）时，同一地址会多一次 range GET 探测调用。

## 6. 测试与注入方式

- 所有测试离线：`route.subprocess.run` 一律替换为
  `proxy/tests/test_manual_save_route.py` 的 `_CurlStub`（按配置里是否含
  `output = "/dev/null"` 区分探测/传输两次调用，并记录 stdin 配置），DNS 一律通过
  `SaveDeps.media_resolve` 或 `_default_download(resolve=...)` 注入假地址。
  因此测试中不会出现真实 curl、真实 DNS 或真实网络。
- 时间预算用 `monkeypatch.setattr(route.time, "monotonic", ...)` 注入假时钟。
- 文件：`proxy/tests/test_manual_save_delivery_guard.py`（Part 1 音质 + Part 2 下载守卫）、
  `proxy/tests/test_manual_save_route.py`（引擎管线与 `_default_download*` 系列）。

```bash
# 手动保存三件套
PYTHONUTF8=1 <py> -m pytest \
  proxy/tests/test_manual_save_route.py \
  proxy/tests/test_manual_save_gateway.py \
  proxy/tests/test_manual_save_delivery_guard.py -q

# 手动保存全部测试文件
PYTHONUTF8=1 <py> -m pytest proxy/tests/test_manual_save_*.py -q

# 离线管线（不得减少）
PYTHONUTF8=1 <py> -m pytest offline_tests -q

# 网关与前端
PYTHONUTF8=1 <py> -m pytest webui-service/test_app_js.py -q
cd webui-service && node test_app_js.js
```

`<py>` 指部署测试环境使用的 Python 解释器；Windows 上必须设置 `PYTHONUTF8=1`。

## 7. 残余风险与限制

- **无 host allowlist（按设计）**：重定向允许到任意公网 host，只要 URL/DNS/探测
  全部通过。网易 CDN 域名会变化，白名单不可维护。
- **DNS rebinding 不能改写传输目标**：传输使用探测选定的数字地址
  （`--resolve` 固定），探测后 DNS 变化不会把传输引到新地址；但共享 CDN 的同一
  公网 IP 仍可能返回不同内容，最终仍受 size/md5 完整性校验约束。
- **TLS**：依赖 curl 默认校验，未使用 `--insecure`，也不做证书固定。
- **HEAD 不可用的 CDN**：回退一次 `range="0-0"` GET；两者都被拒绝则失败（fail closed）。
- **仅 DNS 名称**：URL 中的 IP 字面量（含 IPv6）一律拒绝；只有 IPv6 可达的极端场景
  不支持。
- **地址探测上限**：单 hop 最多探测前 4 个解析地址（上限 32），其余地址不参与探测；
  探测顺序即解析顺序。
- **预算**：探测与传输各自拥有 `DOWNLOAD_SUBPROCESS_TIMEOUT` 预算，最坏情况下
  下载阶段约 2×580s + 子进程余量；仍低于网关手动保存 1200s 总超时，但网络极差时
  会接近该上限。
- **重定向状态**：只把 301/302/303/307/308 视为可跟随；其它 3xx 会先尝试 range GET，
  仍不可用则该地址失败。
- **错误信息**：所有路径只返回固定 code/message，不记录也不回显 URL/host/IP/token；
  排障只能依赖阶段码，不能从错误文本定位具体 CDN。
