# G0 可信入口（G0-FNMEXT-TRUSTED-INGRESS）

状态：**仓库代码实现 + 本地自检完成；未部署、未改 NAS、未 push、未改默认开关。**
日期：2026-11-01（Asia/Tokyo）　分支：`codex/fnmusic-prelaunch-20261025`
基线 HEAD：`ac63dcd`（已推送，工作区干净）　仓库 VERSION：2.10.2（安装版为 2.8.2）
本轮只改仓库代码；下文真机证据来自 2026-11-01 已定案观测，不重新质疑。

## 1. 设计分层

任何一层都不得替代另一层；拒绝一律 fail-closed（不退化权限，不继续处理）。

| 层 | 机制 | 位置 | 失败行为 |
| --- | --- | --- | --- |
| L1 | 内核 DAC：socket 权限 | `proxy/webui_gateway.py` `serve()`（bind 后 `os.chmod`） | 默认 `0600`（root-only）/`0660`（共享名单）；每次启动重断言，绝不继承陈旧 0666 |
| L2 | 内核 peer：`SO_PEERCRED` uid 允许名单 | `proxy/gateway_identity.read_peer_credentials` + `evaluate_channel`；`serve()` accept 后立即执行 | 关闭连接；不读字节、不起线程、不连上游；记录固定原因码 |
| L3 | 边界严格解析：framing + 身份头 | `webui_gateway._validate_head`（转发路径）；本地特权处理器保持既有严格解析 | 400 + 固定码（转发路径）；本地处理器保持文档化固定响应 |
| L4 | 授权：单条 canonical admin claim + userid | `webui_gateway._manual_save_authorized` | 403 `forbidden`，不调引擎 |
| L5 | 业务前置：CSRF / enabled / body 上限 | 既有 `_parse_manual_save_head` / `_engine_status` / `_read_manual_save_body` | 保持既有错误码与信封 |
| L6 | 副作用：引擎调用、文件读写、上游转发 | 既有 handler / `_relay` | — |

`parse_untrusted_identity` / `read_peer_credentials` 的既有语义未改：仍只产出
untrusted claim / 只读元数据，身份声明不构成认证结论。

## 2. 常量与配置

`proxy/gateway_identity.py`（新增，全部 fail-closed）：

| 名称 | 值 | 含义 |
| --- | --- | --- |
| `DEFAULT_PEER_UIDS` | `frozenset({0})` | 未配置时的 peer uid 允许名单（官方网关为 root） |
| `MAX_PEER_ALLOW_ENTRIES` | `8` | 去重后允许名单条目上限 |
| `DEFAULT_SOCKET_MODE_ROOT_ONLY` | `0o600` | 名单 == `{0}` 时的默认 socket 权限 |
| `DEFAULT_SOCKET_MODE_SHARED` | `0o660` | 其它名单的默认 socket 权限 |
| `TrustedChannelError.code` | 固定串 | `peer_allow_invalid` / `socket_mode_invalid` / `peer_uid_not_allowed` / `user_id_claim_missing`（peer 凭据读取失败不是这些码：`PeerCredentialsError` 由调用方记为下表的两个 L2 日志类） |

`proxy/webui_gateway.py` 启动环境变量（每次启动读取一次，非法即拒绝启动）：

| 变量 | 缺省 | 合法值 |
| --- | --- | --- |
| `FNMUSIC_GATEWAY_PEER_ALLOW` | `0`（`{0}`） | 逗号分隔 ASCII 十进制 uid，每项 `^[0-9]{1,10}$` 且 ≤ 4294967295；去重后 1..8 项；空串/空项/符号/空格/非 ASCII 一律非法 |
| `FNMUSIC_GATEWAY_SOCKET_MODE` | 名单 == `{0}` → `0600`，否则 `0660` | 只接受 `0600`/`600`/`0660`/`660`；`0666`/`0777` 等一律非法 |

非法配置：stderr 固定行
`fnmusic-webui-gateway channel-rejected startup-refused code=<code>`，随后
`SystemExit(2)`（在 daemonize / PID / socket 任何副作用之前）。不得静默退化为 0666。

其它常量：`MANUAL_SAVE_REQUIRE_USER_ID = True`（L4 单点开关）；
`CHANNEL_REJECT_LOG_INTERVAL = 60.0`（同原因码 60 秒最多 1 条）。
部署单元模板 `fnmusic-ext.service` 为 `User=root` + `UMask=0077`，与默认 `0600` 一致。

## 3. 协议与拒绝码表

### 3.1 L2（accept 时，日志固定码，不返回 HTTP）

| 日志 `code=` | 触发 | 连接行为 |
| --- | --- | --- |
| `peer_uid_not_allowed` | 内核 `SO_PEERCRED.uid` 不在允许名单 | 立即 close；无字节读取、无线程、无上游连接 |
| `peer_credentials_unsupported` | 平台/socket 类型/常量不支持（含非 Linux） | 同上 |
| `peer_credentials_invalid` | peer 元数据畸形/系统调用失败 | 同上 |

日志行形如 `fnmusic-webui-gateway channel-rejected code=peer_uid_not_allowed pid=123 uid=1000`
（只允许整数 pid/uid；绝不输出头值/URL/cookie/异常文本；按码 60 秒节流）。

### 3.2 L3（转发路径，HTTP 400 固定码）

| `code` | 触发 |
| --- | --- |
| `invalid_framing` | 重复/非法 `Content-Length`、`Content-Length`+`Transfer-Encoding` 共存、重复 `Transfer-Encoding`、非法头名/头值、头数量/字节超限、head 结构不完整 |
| `invalid_identity_claim` | 重复 identity 头（`X-Trim-Userid`/`X-Trim-Isadmin`/`X-Trim-Username`）、畸形 admin/userid/username 值 |

响应体固定为 `{"ok": false, "code": "<固定码>", "message": "<固定中文>"}`；
不读取 body、不调引擎、不连上游、不输出原头值。

既有本地路由的优先级（保持既有契约，不因 L3 改变其文档化响应）：
`/api/maintenance`、`/api/manual-save/*` 先由各自严格解析器处理（400 固定码）；
`/api/host-file`、`/api/fs-check` 由既有 admin 声明解析处理（畸形/重复 → 403）。
这些处理器都在读 body / 引擎 / 文件系统 / 上游之前拒绝同一批畸形 head，
因此不存在把畸形 head 交给业务层的路径。

### 3.3 L4（手动保存授权）

`x-fnmusic-save: 1` + 单条规范 `X-Trim-Isadmin: true` +（`MANUAL_SAVE_REQUIRE_USER_ID`
为真时）存在且语法合规的 `X-Trim-Userid`，全部满足才进入引擎；否则 403
`forbidden`。`X-Trim-Userid` 缺失对应内部固定码 `user_id_claim_missing`（不落日志）。
端点映射、响应信封、错误码、超时语义保持不变。

### 3.4 直连 8774（WebUI 侧）

`webui-service/app.py` 对 `/api/manual-save` 及任意子路径（GET+POST）新增显式
501 占位：WebUI 不是信任边界，不持有手动保存引擎；能直连到 WebUI 的请求一律
不得降级为可执行路径。占位不遮蔽任何既有路由（WebUI 此前没有 manual-save 路由）。

## 4. 真机证据（2026-11-01 定案）

- 官方网关转发进程 = `trim_http_cgi`（`/usr/trim/bin/trim_http_cgi`，cgroup
  `/system.slice/trim_http_cgi.service`），**uid=0 root**；90 秒内 8 次连接全部如此
  （透明代理实测 `SO_PEERCRED`）。→ L2 默认允许名单 `{0}` 的依据。
- 扩展 socket `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock` 当前 **0666 root**；
  监听进程是本扩展 `python3 proxy/webui_gateway.py`（uid 0）。→ L1 需要 0600 的原因。
- 以普通用户 `kurumi` 直连：无头 **403**；自填 `x-trim-isadmin: true` → **200**
  （当前可伪造）。→ 需要 L2 内核 peer + L3/L4 严格 claim 的原因。
- `ui/config`：`gatewaySocket=fnmusic-ext.sock`、`allUsers:false`、
  `url:/app/fnmusic-ext/`、`type:iframe`（路径固定，不能挪）。
- 安装版 2.8.2；仓库 VERSION 2.10.2 —— 本轮未部署，安装版行为不变。

## 5. 测试与验证记录（2026-11-01，本机 Windows）

解释器：`C:\Users\admin\AppData\Local\Temp\fnmusic-testvenv-py314\Scripts\python.exe`
（Python 3.14.6）；Windows 下必须 `PYTHONUTF8=1`；`PYTHONPATH=E:/fnmusic-ext-dev/proxy`
（与 `scripts/run_tests.py` 的 proxy 套件隔离环境一致）。

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q proxy/tests/test_gateway_identity.py proxy/tests/test_manual_save_gateway.py proxy/tests/test_gateway_observation.py` | **292 passed**（identity 136 / manual 98 / observer 58） |
| `python -m pytest -q proxy/tests/test_gateway_channel_linux.py` | **5 skipped**（本机非 Linux，预期；Linux 上执行） |
| `python -m pytest -q proxy/tests/test_gateway_process_linux.py` | **1 skipped**（仅 Linux） |
| `python -m pytest -q proxy/tests/test_webui_gateway.py` | **42 failed, 32 passed** —— 42 failed 为改动前同文件既有失败（`socket.AF_UNIX` 在该 Windows Python 构建中不存在，改动前基线同为 42 failed / 7 passed），非本轮引入；新增 25 个用例全部通过 |
| 手动保存四件套 + 歌单引擎（delivery_guard/gateway/metadata/route/playlist） | **562 passed, 2 skipped**（基线 552 passed, 2 skipped，未减少；新增 10 个 L4 用例） |
| `python -m pytest -q offline_tests` | **214 passed, 53 skipped, 196 subtests passed**（与基线一致） |
| `cd webui-service && node test_app_js.js` | **102/102 passed, 0 failed** |
| 可运行 `proxy/tests` 子集（忽略本机无法收集的 5 个既有平台文件） | **393 failed, 1724 passed, 33 skipped** —— failed 与改动前基线同为 393（无新增失败）；skipped +5 为新增 Linux-only 用例；passed +83 为新用例 |
| `python -m py_compile`（gateway_identity、webui_gateway、app.py、5 个测试文件、观察/进程测试） | **OK（9 files）** |
| `git diff --check` | clean（未改任何 shell，`bash -n` 不适用） |

Linux 上应执行（本机无法运行，仅记录命令）：

```text
python -B -m pytest -q proxy/tests/test_gateway_channel_linux.py proxy/tests/test_gateway_process_linux.py
```

`test_gateway_channel_linux.py` 覆盖：真实 `socketpair` 的 `SO_PEERCRED` pid/uid/gid、
`evaluate_channel` 允许/拒绝、`serve()` 同 uid 连接进入转发路径且上游 stub 收到
原始 head 字节、非允许 uid 连接立即 EOF 且上游 0 连接、policy 模式下陈旧 0666
文件被替换并重断言为 0600。

Windows 上另用**未提交**的临时 harness（TCP 后端伪装 AF_UNIX + 伪造
`read_peer_credentials`）验证 `serve()` 循环机制，输出：`allow-forward: ok`、
`deny-no-upstream: ok`、`policy-refusal: ok`、`log-throttle: ok`；该 harness 不属于
仓库交付物。

### 5.1 变异自检（临时改代码 → 跑对应测试 → 还原，sha256 校验一致）

| 变异 | 对应测试 | 结果 |
| --- | --- | --- |
| M1 删除 `_admit_peer` 中的 `evaluate_channel` 调用（peer 校验消失） | `test_webui_gateway.py -k admit_peer` | **1 failed**（`test_admit_peer_rejects_an_unlisted_uid`），还原后 OK |
| M2 `os.chmod(sock_path, socket_mode)` 改回 `0o666` | `test_webui_gateway.py -k reasserts` | **1 failed**（`test_serve_reasserts_the_policy_socket_mode`），还原后 OK |
| M3 `_validate_head` 直接 `return None`（去掉重复头/框架拒绝） | `test_webui_gateway.py -k ambiguous_head` | **8 failed**，还原后 OK |
| M4 `MANUAL_SAVE_REQUIRE_USER_ID = False`（去掉 userid 要求） | `test_manual_save_gateway.py -k user_id` | **5 failed**，还原后 OK |

## 6. 残余风险

- **R1 官方网关是否覆盖/清洗客户端自带头**：未定案。当前缓解：L2 只允许 root
  网关连入 socket；L3 对重复/畸形 identity 头一律 400。若网关既不注入自身
  `X-Trim-Isadmin` 又原样透传客户端唯一一条 `X-Trim-Isadmin: true`，单 claim 仍
  可能被伪造；需要在下一个 G0 观测窗口确认注入侵入路径（建议抓取网关转发的
  head 计数），再决定是否增加"必须出现网关专有头"之类的正向断言。
- **R2 8774 其它 API**：本轮只把 `/api/manual-save*` 直连设为 501；WebUI 其它
  管理 API 仍由 `AuthMiddleware` 的单条 admin claim 保护，直连 8774 的 claim
  来源不可信。8774 若被端口映射/容器网络暴露，需另行 G0 评估（不在本轮范围）。
- **R3 uid 粒度**：允许 uid 0 等价信任本机所有 root 进程；若将来纳入非 root
  用户，必须显式写 allow-list，并重新评估 0660 的 group 是否等于该名单。
- **R4 升级/重装会重置 socket**：`serve()` 每次启动 unlink 旧文件并重断言 mode；
  但升级过程若旧进程/旧 socket 并存，需按第 7 节顺序重启并检查 `ls -l`。
- **R5 观测模式同样受边界约束**：`--g0-observe-*` 的观测进程也走 L2/L3；Linux
  进程级观测测试已通过子进程环境变量显式给出同 uid allow-list。

## 7. 部署 / 回滚顺序（本轮不执行，供下一步）

- **G1 代码固化**：评审本分支 diff；G2..G4 由具备 NAS 权限的窗口执行。
- **G2 预检（只读）**：记录现有 socket `ls -l`/`stat -c '%a %U'`；确认
  `trim_http_cgi` uid=0 与 cgroup；确认 `fnmusic-ext.service` 为 `User=root`；
  记录安装版 VERSION 与现有文件 sha256。
- **G3 部署**：按安装流程替换 repo → `systemctl restart fnmusic-ext.service` →
  断言 socket 为 `0600 root`、启动日志无 `startup-refused`；桌面内冒烟
  `/api/manual-save/status` 等仍正常；以普通用户直连 socket 应被内核拒绝
  （EACCES，或连接被立即关闭），且 `journalctl` 出现固定 `channel-rejected` 行。
- **G4 回滚**：恢复上一版 repo 并重启；**不要**试图用环境变量恢复 0666（不允许
  该模式）；若确需兼容非 root peer，只能回滚到旧代码并记录风险。回滚后确认
  socket 权限与升级前记录一致。
