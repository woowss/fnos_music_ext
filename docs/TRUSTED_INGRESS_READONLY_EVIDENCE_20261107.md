# 受信入口与身份链路：只读证据（2026-11-07）

> **只读现场证据，不是授权或门禁通过证明。** 目的：推进部署检查单 §0.2「高权限写接口身份与路径门」的只读部分。全程仅 GET/HEAD 与只读文件检查；未停/启服务、未改配置、未安装/回滚、未写 `/vol1`、未发写请求。
> 执行者：Lead。窗口：2026-11-07 10:11–10:13（NAS 本地 CST）。
> 关联：`docs/G0_TRUSTED_INGRESS.md`（候选侧 L1–L5 模型与 **R1 未定案**）、`docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` §0.2。

## 1. 证据绑定

| 项 | 值 |
| --- | --- |
| 设备/内核 | `kotomi`，fnOS `1.2.0903`，`6.18.18.c1168-trim` |
| 扩展版本 | `2.10.2`（`/vol1/@appcenter/fnmusic-ext/repo/VERSION`） |
| nginx 配置 | `/usr/trim/nginx/conf/conf.d/trim_http_cgi.conf`、`/usr/trim/nginx/conf/nginx.conf`（`/app/` 路由） |
| `trim_http_cgi` | `/usr/trim/bin/trim_http_cgi`，14,404,544 B，SHA-256 `7773bee829fe535a36f941c0445a88d5de186e8c35960a882ea7063c2b7393c0`，pid 1781，root |
| 已部署网关代码 | `webui_gateway.py`=`48b51e931817f79b2ffbdca55ba56f7abfb6b3472429fcbece1ddd65ffebcbc9`；`gateway_identity.py`=`860e93095093182a51cc75085f331c88a308a58830331eee06b08c8a8a225b5a` |
| 服务身份 | `fnmusic-ext.service`：`User=root`，`ExecStart=/bin/bash /vol1/@appcenter/fnmusic-ext/repo/proxy/run_proxy.sh`，MainPID 1870197 |
| 网关 socket | `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`，`srw------- root:root`，监听 pid 1870256 fd=4 |
| 原始命令输出 | 本文件 §3–§5 直接引用；命令均为 ssh 只读（GET/stat/grep/sed/strings） |

## 2. 观测到的入口链路

1. 外部浏览器 → `https://<nas>:11026` → nginx（pid 2791 等，`0.0.0.0:11026` TLS）。
2. nginx 对未通过安全码校验的外部请求返回 **安全码验证页**（HTTP 200、`Content-Length: 8893`、`x-trim-safe-code-challenge: 1`）；对 `/app/fnmusic-ext/` 与 `/app/fnmusic-ext/api/manual-save/status` 均如此（本次 curl 也命中该页并记入 access.log）。
3. `location /app/` 配置（`trim_http_cgi.conf`）：
   ```nginx
   location /app/ {
       proxy_pass http://unix:/var/run/trim_http_cgi.socket:;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header Host $host;
       proxy_read_timeout 300s;
   }
   ```
   注意：**未显式清除 `X-Trim-*` 客户端头**；nginx 默认会把客户端请求头转发给上游。
4. `/var/run/trim_http_cgi.socket`（`srwxrwx--- root:www-data`）→ `trim_http_cgi`（root，Go 二进制）。
5. 扩展网关 socket（`0600 root:root`）→ 已部署 `webui_gateway.py` 的 L2（`SO_PEERCRED` allow-list，默认仅 root）+ L3（identity 头解析）+ L4（admin/userid claim）→ 写路由。（L1 对 NAS 管理员不一定成立，见 §9 更正）

## 3. trim_http_cgi 的 token 校验（无会话时）

直接向 `/var/run/trim_http_cgi.socket` 发 GET（模拟 nginx 转发），不带 Cookie：

| 请求 | 结果 |
| --- | --- |
| 无任何头 | HTTP 200 `text/plain`，body=`invalid token`（13 B） |
| 伪造 `X-Trim-Userid/X-Trim-Isadmin/X-Trim-Username` | 同样 `invalid token`；**伪造头未被当作身份** |
| `GET /app/fnmusic-ext/`（页面） | 同样 `invalid token` |

→ 该层以 **token/cookie**（`TokenQuery`/`trim-cgi-sign` 等）判定会话；没有有效会话时，伪造 identity 头不会放行。

## 4. 扩展网关对 identity 头的信任（root 直连，仅 GET）

直接连接 `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`（root 通过 L1/L2）：

| 请求头 | 结果 |
| --- | --- |
| 无身份头 | **403** `{"ok":false,"code":"forbidden","message":"仅管理员可执行手动保存"}` |
| 伪造 `X-Trim-Userid: 1000` + `X-Trim-Isadmin: true` + `X-Trim-Username: kurumi` + `x-fnmusic-save: 1` | **200** `{"ok":true,...}`，返回 `enabled:true`、`target_dir=/vol1/1000/fnmusic-test-20261031`、`staging_dir=/vol1/1000/fnmusic-manual-save-staging`、`quality_order=[jymaster,hires,lossless,exhigh]` 等 |
| 同上但头名小写 | **200**（HTTP 头名大小写不敏感；应用解析器会把名字归一化） |

→ **应用层信任 `X-Trim-*` 声明**，只校验"是否恰有一条规范 claim + admin 值"；它**不校验会话**。安全性完全依赖：L1 socket 权限（挡非管理员；本机实测 NAS 管理员可连，见 §9）+ L2 peer allow-list（默认仅 root）+ 上游 `trim_http_cgi` 是否对客户端自带的同名头**剥离或覆盖**（仍未验证）。

已部署 `gateway_identity.py` 的解析规则（节选）：

```python
_IDENTITY_HEADERS = {
    b"x-trim-userid": "user_id_claim",
    b"x-trim-isadmin": "admin_claim",
    b"x-trim-username": "username_claim",
}
...
if claim_field in seen: raise HeaderClaimsError("DUPLICATE_IDENTITY_HEADER")
```

→ 重复 identity 头会被拒绝（`DUPLICATE_IDENTITY_HEADER`）；因此**若** `trim_http_cgi` 注入自己的 identity 头而客户端也带一条，应用会因重复而 400，而不是被伪造头放行。

## 5. trim_http_cgi 的注入线索（静态，未证明行为）

- 二进制中存在精确字符串 **`X-Trim-Isadmin`、`X-Trim-Userid`、`X-Trim-Username`**，以及与 token 校验相关的 `invalid token`、`entry-token`、`trim-cgi-sign`、`empty signature`、`permission deny`、`TokenQuery`。
- 这**支持**"该层校验 token 并注入/覆盖 identity 头"的假设，但**字符串存在不等于运行时行为**；本文件不把静态线索当作证明。

## 6. 结论与剩余未知

**已由本次只读证据支持（本机、本版本）：**

1. 外部未认证请求在 nginx/安全码层即被拦截（返回安全码页）；`/app/` 转发到 root 的 `trim_http_cgi`。
2. `trim_http_cgi` 以 token 判定会话；无有效 token 时返回 `invalid token`，伪造 `X-Trim-*` 头不能通过该层。
3. 扩展网关信任 `X-Trim-*` claim（root 直连可 200）；因此**单条伪造 claim 若原样到达 socket 即被视为管理员**——即 `G0_TRUSTED_INGRESS.md` 的 **R1 在应用层成立**（本次实证）。
4. 网关对重复 identity 头 fail-closed（`DUPLICATE_IDENTITY_HEADER`）；socket 权限 0600 root:root + peer allow-list 默认仅 root（L1 对 NAS 管理员不一定成立，见 §9）。
5. `trim_http_cgi` 是 root 的 Go 二进制，内部含 identity 头名与 token 校验字符串。

**仍未证明（§0.2 不能转 PASS）：**

1. **`trim_http_cgi` 在带有效会话时是否剥离/覆盖客户端自带的 `X-Trim-*` 头**——这是唯一的剩余关键问题。需要真实已认证会话（浏览器登录）后，对比"仅正常请求"与"正常请求 + 伪造 X-Trim 头"的转发结果（预期：重复头 → 400；若返回 200 且 admin 被伪造，则为漏洞）。
2. 真实浏览器已认证链路的端到端观测（浏览器 → 安全码 → nginx → trim_http_cgi）未做。
3. 非 root 本地进程对扩展 socket 的拒绝行为：本次已观测 `www-data` connect 被内核 EACCES 拒绝；NAS 管理员可 connect，随后被 L2 重置。peer allow-list 的代码路径未逐行复核（部署版与仓库版不同）。
4. 安全码层与 fnOS 会话/2FA 的关系、管理页入口的真实请求头注入路径未验证。
5. 未审计 `trim_http_cgi` 的完整代码（仅 strings）；未做动态追踪。

## 7. 下一步（需一次用户登录）

1. 请用户在 **DSH 浏览器**里登录 `https://192.168.0.106:11026` 一次（账号/密码/2FA 由用户自己输入，Lead 不接触凭据）；随后 Lead 用浏览器只读打开 `/app/fnmusic-ext/`，对比：
   - 正常 GET 的响应；
   - 带伪造 `X-Trim-Isadmin: true`（通过页面脚本/CDP 请求头）的 GET 是否被 400/403 或放行。
   该实验只做 GET，不触发写接口。
2. 若可得，抓取 `trim_http_cgi` 转发的请求头（例如通过受控的本地 echo 端点/日志，需另行设计且不触生产写路径）。
3. 完成以上后，才能评估 §0.2 能否从 BLOCKED → PASS；在此之前保持 BLOCKED。

## 8. 未改变的边界

- §0.1/§0.2 仍 **BLOCKED**；本文件不构成部署/写接口授权。
- 本次未做任何写请求；伪造头 200 仅发生在 **root 直连 socket** 的只读 status 查询上，不代表可绕过认证执行写操作（写路由另有 CSRF/claim 校验，且本次未测试写路径）。
- 设备与配置未被修改；`fnmusic-ext.service` 仍 active（同一 MainPID）。
- **边界补充（复审 M4）**：L2 对 root peer 是信任的；只实测了 1 个管理员账户（`kurumi`）与 1 个服务账户（`www-data`），未测其它 uid/AD/LDAP/容器内进程/真实曲库路径；nginx 把客户端头转发给 `trim_http_cgi` 是**配置层推断**（未抓到实际转发报文）；写路由未测试。

## 9. 追加更正（同日晚）：L1 socket mode 不是本机可靠边界

`docs/VOL1_PERMISSION_MODEL_FINDING_20261107.md` 的受控实验显示：本机 `/vol1`（`trimacl`）上，**NAS 管理员 `kurumi` 可连接受 mode 0600 root:root 保护的网关 socket**（connect 成功，随后被网关 `SO_PEERCRED` allow-list 拒绝：连接重置、无 HTTP 响应）；`www-data` 则被内核 EACCES 拒绝；对照 `/run`（tmpfs）与 `/tmp`（ext4）均按 POSIX 拒绝。

因此：

- **L1"socket 0600 ⇒ 仅 root 可连"在本机不成立**；但对非管理员仍有效。
- **L2（内核 SO_PEERCRED + peer uid allow-list）是实际边界**：`peer_uid_not_allowed` 是**代码层预期拒绝码**（部署版 `webui_gateway.py`=`48b51e93…` 与仓库版 `1b07f815…` 不同，本地不能逐行复核）；**实际观测**为连接被重置、无任何 HTTP 响应（fail-closed）。
- §0.2 的安全论证必须改为"L1 限非管理员 + L2 allow-list fail-closed + trim_http_cgi token 校验 + 头剥离/覆盖（未验证）"，不能引用 L1 作为"仅 root"证据。
- 本更正不改变 §0.2 BLOCKED，也不构成授权。
