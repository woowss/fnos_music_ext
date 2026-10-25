# G0 身份与生命周期证据计划（BLOCKED）

> 合同：`FNMEXT-G0-EVIDENCE-PLAN-20261025-01`（2026-10-25）
>
> 当前结论：**BLOCKED**。本文件是证据审查计划，不是可执行 runbook、NAS 操作授权、部署证明或上线结论。即使本文件和本地验证通过，也不代表 G0 已通过、允许部署或可以上线。任何服务停止、配置/文件写入、observer 部署或音乐文件写入都必须先经过针对该操作的审查；实施前须核对已有明确授权是否覆盖精确目标、动作、时间窗、副作用和 rollback 范围，未覆盖时再请求补充授权。

## 目标与边界

G0 的目标是以证据证明：扩展 WebUI gateway 看到的传输 peer 与身份来源可信、管理员权限判断无法由请求方伪造，并且观察/维护的服务生命周期有受支持且可回滚的方案。transport 观察、身份信任和生命周期证据是三个独立门槛，必须全部通过。`gateway_observation` 只提供有限结构摘要，单独不能证明 G0。

本文件定义 G0 设备证据计划。设备调查仅限受支持的只读身份/生命周期读取；不发送下载或媒体请求，不调用 Media API，不访问 native download prepare/delete 路由，不改 NAS 设置，不安装/升级，不停止/重启服务，不触发扫描，不部署 observer，也不写音乐文件。本文后文描述的 `/healthz` 路径和 observer 是本地 dirty 候选及其合成测试，不表示授权设备执行，也不改变上述设备操作边界。

## 基准与证据分类

既有计划记录的初始本地文档基准为 `E:\fnmusic-ext-dev`，`main@839fd314015e6e9fd1c1da5641a5c0d20d61b722`；当时检查到该 worktree 有 32 条 dirty 状态记录，并计划修改本文、[观测运行手册](G0_OBSERVATION_RUNBOOK.md) 的归档标识及 [维护启动本地合同](MAINTENANCE_STARTUP.md) 的交叉引用。这段是原计划的历史范围记录，不描述本轮实现变更。当前 `/healthz` 候选及其哈希见下文；dirty 工作区中的代码只作为本地候选审查，不能表述成已部署内容。

下表区分设备历史快照、本地候选事实和未知项。带日期的设备结果是当时观察，不代表当前实时状态；每个设备阶段都必须重新只读取证。

| 范围 | 已有证据 | 含义与限制 |
| --- | --- | --- |
| 设备 service 快照，2026-10-24 | `fnmusic-ext.service` 当时为 `active/running`；`Restart=no`、`CanReload=no`、`Delegate=no`、`KillMode=mixed`；`DropInPaths` 为空；`ExecStopPost` 调用 `takeover.py restore` | 历史快照而非当前状态。未证明安装文件完整性、所有有效 unit 属性、自然 drain、热替换或回滚安全。 |
| 设备安装副本，历史只读观察 | 当时安装副本中没有 `gateway_identity.py` 和 `gateway_observation.py` | 不可外推到当前安装状态；也不能以本地维护开关推定设备行为。 |
| 设备安装副本，2026-10-25 | 当前 `/vol1/@appcenter/fnmusic-ext/repo/VERSION` 为 `2.8.2`；安装路径缺少 `proxy/gateway_identity.py`、`proxy/gateway_observation.py`；已安装 `proxy/webui_gateway.py`、`proxy/takeover.py` 的 SHA-256 与当前本地候选不同 | 这是设备安装路径与当前候选不一致的证据；本次记录未保留设备侧摘要值，本地候选参考值见下文；不能据此推断安装版本的具体行为或完整文件清单。 |
| 设备 systemd unit 快照，2026-10-25 | 有效 unit 为 `active/running`，`User=root`、`Restart=no`、`CanReload=no`、`KillMode=mixed`；无 drop-ins；`ExecStopPost` 为 `takeover.py restore` | 只描述本次读取到的有效属性；不证明自然 drain、writer 已退出、接管/恢复安全或生命周期方案受支持。不得据此盲目重启或安装。 |
| 设备 systemd cgroup 进程快照，2026-10-25 | 观察到 `takeover.py`（PID `35198`）、一个 `argv[1]=-m` 的 Python 进程，以及 `webui_gateway.py`（PID `35257`） | 仅为读取时点的进程清单；不能证明所有 writer/子进程已排空或没有 successor。当前本地候选 `webui_gateway.py` 会双 fork daemonize，但已安装文件哈希不同，不能将该候选行为归属于设备版本，也不能据此声称 cgroup 已排空。 |
| 设备 gateway 路由与身份头线索，2026-10-25 | nginx `/websocket` 路由到 `/var/run/trim_cgi.socket`，后端为 `trim_cgi`；对所检查的配置目录检索未发现显式 `X-Trim` identity-header directive；`trim_http_cgi` 二进制 strings 含 `X-Trim-IsAdmin`、`X-Trim-Userid`、`X-Trim-Username` | socket 路由和静态字符串不是身份认证/授权证据；未发现显式配置不代表不存在其他注入路径，二进制字符串也不证明运行时实际发出、由可信边界注入或不可伪造。 |
| 设备 gateway Unix-socket 身份声明检查，2026-10-25 | 已认证的 1Panel root 终端以 OS 用户 `www-data` 直接连接安装 socket `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`，对 GET `/api/about` 做两次限时只读请求：无身份头为 HTTP 403；请求自带 `x-trim-isadmin: true` 为 HTTP 200；响应体均丢弃。读取时 service 为 `active/running`，socket 权限为 `0666`。 | 证明该本地 OS 调用者能连接 socket 并让安装版该 handler 接受伪造的管理员声明；不证明外部/互联网匿名可达、官方统一网关会转发请求方伪造头、响应内容或任何写入接口可被利用。此直接 socket 测试也不替代远程代理链路验证。 |
| 本地 observer 候选 | 记录 header count/present/canonical 与**同一已接受 socket** 的 `SO_PEERCRED` PID/UID/GID；不记录身份头值或 admin 真值 | 结构摘要不证明头的来源可信、peer 对应哪个官方账号或管理员身份。observer ready 还需与本次 marker 匹配。 |
| 本地身份候选 | `gateway_identity` 将头解析为 untrusted claims；严格解析可拒绝畸形/重复值，但一个格式正确的伪造管理员头仍可能通过本地 request gate | 需要证明可信注入点和绕过面；当前不能据此放行管理员能力。 |
| 本地 observer 启动链 | gateway 的 observer 启动异常可能被捕获并忽略，gateway 继续启动/监听 | `active`、进程存在或 listener 存在都不代表 observer ready。必须有与目标 PID、启动时间和本次 marker 关联的 ready 证据。 |
| 本地 marked diagnostic 请求候选 | 旧 `/api/status` handler/downstream 调用链已作静态审阅并得出否定结论（见下文）。当前 dirty 候选的 observer 改为精确匹配桌面前缀 `/healthz` 请求；本地 handler 只返回固定 JSON，相关 ASGI sentinel 测试结果见下文。 | 当前候选的 WebUI handler 不运行 `supervisorctl` 或下游探活；但 gateway relay、observer peer 读取/私有记录、通常的请求日志和既有 service lifespan 活动仍在诊断范围。仅有本地源码/合成测试，installed 副本是否匹配和现场副作用仍未验证；P2 不因此通过。 |
| 本地维护启动合同 | [MAINTENANCE_STARTUP.md](MAINTENANCE_STARTUP.md) 描述九类被抑制的新进程启动副作用；请求处理仍可写，且不排空旧进程/线程 | 仅本地实现合同，未在 NAS/systemd 部署验证。九类不是全系统写入清单或全局只读保证。 |
| 未知/待重新验证 | 已知安装版本为 `2.8.2`，但已安装文件与本地候选存在差异；仍未知的是安装代码完整清单/行为、可信头来源与运行时发出路径、被接受 peer、观测链路现场副作用、旧 writer 自然 drain、successor 排除及回滚行为 | 均为 G0 阻断项；不得使用旧快照或本地源码填补设备证据。旧 `/api/status` 调用链已有结论；新的本地 `/healthz` 候选不等同于设备安装版本，仍需独立审查及受支持的版本比对。 |

### G0-P2 旧 `/api/status` marked 候选的否定审阅（2026-10-25）

**范围与源码快照**：只审阅 `E:\fnmusic-ext-dev` 中 `main@839fd314015e6e9fd1c1da5641a5c0d20d61b722` 加当时 dirty worktree 的旧 observer 候选；下列哈希绑定那次本地字节，不是提交树或 NAS 安装 manifest。之后 observer target 已在当前 dirty worktree 改为 `/healthz`，新快照见下一节：

| 文件 | 本地 SHA-256 |
| --- | --- |
| `webui-service/app.py` | `DF287EE021E0E8E9BCA87D13D8E6A412F7AC5CFAC5E130DD9B7DC2D9107DBC13` |
| `proxy/env_merge.py` | `2882A63E9DB0B2C1D6E17E1289F27B60D6889143BD73E65E1E772D83D967A83F` |
| `proxy/webui_gateway.py` | `69CF16C8CE94DE9353987DE63CB3D5D937AAEA6E7425BED0E029BD1F0AD024EB` |
| `proxy/gateway_observation.py` | `AC6D30020C5806DDD737BCB29DEF6DFFF888C6FD0E2C2BF9EAABEED5EEDB6655` |
| `lxmusic-service/app.py` | `FD600B0671A4D3AD8B67E56770E8860B157D7F3036308127C125A7E6217B6BDF` |
| `lxmusic-service/lxserver_client.py` | `1381FD6F908428FA490B34537A847BF8C1B0BDF24807908125DE8FAA45D2C3AE` |

**已审阅调用链**：`webui-service/app.py:1111-1144` 的 GET `/api/status` 先读取配置并调用 `supervisor_status()`；后者在 `app.py:204-229` 通过 `subprocess.run` 执行 `supervisorctl status`。随后该 handler 对当前启用音源，或启用常驻标志的 MusicBox，调用 `/healthz`；当前音源为 lxmusic 时，还会 GET `/api/v1/source`。本地 `lxmusic-service/app.py:1036-1065,1559-1562` 与 `lxserver_client.py:391-404,668-679` 显示健康/音源查询继续请求 lxserver 的 `/api/status` 和 `/api/custom-source/list`。在该旧 observer 源码快照中，`proxy/gateway_observation.py:28` 匹配 `/app/fnmusic-ext/api/status?g0_observe=<marker>`；`proxy/webui_gateway.py:462-491` 观察后仍将原请求转发给 WebUI，marker 不会短路该调用链。

**结论与限制**：该旧 marked GET 明确会启动本地子进程并产生下游 HTTP 请求，因此不满足“不启动子进程、不请求下游服务”的惰性观测边界。审阅的 handler 路径中未发现直接写配置或音乐文件的代码；这不证明 supervisor、服务端、连接层或日志没有内部副作用，也不表示其是严格无副作用。以上只适用于该本地旧 dirty 源码快照；未请求设备、未确认安装副本匹配，也未证明下游服务内部行为。

**P2 处置**：旧 `/api/status` marked request 不适合严格惰性 handler 观测；该否定结论只绑定本节来源快照。当前候选已在本地改用已有 `/healthz` handler，详细证据和限制见下一节。安装副本比对、gateway/observer 的可接受副作用审查及现场证据仍未完成，P2 整体仍 **BLOCKED**。

### G0-P2 固定 `/healthz` marked 请求本地候选（2026-10-25）

**范围与候选字节**：本地候选仍位于 `E:\fnmusic-ext-dev` 的 dirty `main@839fd314015e6e9fd1c1da5641a5c0d20d61b722`，未提交、未部署。`proxy/gateway_observation.py` 改为精确匹配 `GET /app/fnmusic-ext/healthz?g0_observe=<32位小写十六进制>`，接受 HTTP/1.0 或 HTTP/1.1；拒绝旧 `/api/status` path、桌面前缀缺失、额外 query、编码变体、路径大小写/尾斜杠变化、其他 method/version。观测记录 schema 升为本地候选 v2，以便与旧 status 目标记录区分。实际本地文件 SHA-256：observer `22B19521327719C6748814EFB6974E117C6B7F5B2066246CFC84E7EADCAAEFB5`；观察器测试 `501723F928388A0E52E808B82E615B5EE92E96BF2505E27807197FC2C45C96F7`；healthz ASGI/sentinel 测试 `9A504E9832441CCF60613A5507ACD951EA0C856CF68A05C48F9D14DF3764E064`。这些是本地 dirty 文件哈希，不是部署 manifest。

**handler 与中间件审阅**：候选目标复用 `webui-service/app.py:1077-1079` 的现有 `/healthz` 固定 `{"ok":true}` 响应，不新增管理操作。`DesktopPrefixMiddleware`（1969-1983）剥除 `/app/fnmusic-ext` 前缀；`AuthMiddleware`（1992-1999）对非 `/api/` 路径绕过管理员 claim 检查。因此该响应不读取或验证身份头，不能作为账号/管理员身份或权限证据，也不新增 privileged 能力。`webui-service/app.py` 本地哈希仍为 `DF287EE021E0E8E9BCA87D13D8E6A412F7AC5CFAC5E130DD9B7DC2D9107DBC13`，其 handler 未修改。

**本地测试证据**：在仓库外 Python 3.14.6 虚拟环境中，未启动 WebUI server 或 lifespan；通过真实 ASGI app 和两个中间件覆盖直接 `/healthz` 与桌面前缀路径。sentinel 对配置/文件读取、HTTP client/探活、socket、subprocess、thread/task 创建与身份解析 helper 记录尝试并立即失败；请求期间记录为零，响应仍固定且不回显 marker/header canary。实际命令（仓库根目录，测试进程环境目录重定向到仓库外临时目录）为：

```text
C:\Users\admin\AppData\Local\Temp\fnmusic-g0-healthz-pytest-env-20261025-dae6eb60899a44ba90b21da4286541f5\venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q --tb=short proxy/tests/test_gateway_observation.py offline_tests/test_g0_healthz_target.py offline_tests/test_management_claims.py
```

进程的 `USERPROFILE`、XDG 类目录、`FNMUSIC_HOME`、`FNMUSIC_LOG_DIR`、`TEMP`、`TMP`、`TMPDIR` 均指向同一外部临时目录；退出码 **0**，结果 **124 passed**。`git diff --check` 通过。该结果只证明被审阅 ASGI 候选和测试覆盖，不证明 gateway 到 WebUI 的真实 socket/relay、systemd、fnOS 或 NAS 运行行为。

**副作用边界与结论**：现场 marked GET 仍会经过 gateway transport/relay；diagnostic 模式会读取被接受 socket 的 peer metadata、解析允许的 header 结构，并把有限记录写入 observer 私有输出。通常的 proxy/server access logging 也可能发生；WebUI lifespan 原有后台任务已由其自身启动，测试不启动它们。不得声称整条链路或整台 NAS “零副作用”。该候选避开旧 `/api/status` 的 `supervisorctl` 和音源/lxserver 请求，但实际安装文件是否相同、可信身份头来源、accepted peer 和服务/日志行为仍待只读取证；G0 与上线继续 **BLOCKED**。

**静态审计：gateway transport / observer 生命周期**：以下仅审阅本地 dirty 候选字节，不代表设备安装副本：`proxy/webui_gateway.py` SHA-256 `69CF16C8CE94DE9353987DE63CB3D5D937AAEA6E7425BED0E029BD1F0AD024EB`；`proxy/gateway_observation.py` SHA-256 `22B19521327719C6748814EFB6974E117C6B7F5B2066246CFC84E7EADCAAEFB5`。

- **文件 / socket**：`webui_gateway.py:303-311,333-335` 创建 socket 父目录，尝试删除遗留 socket pathname，再 bind、chmod `0666` 并 listen；退出路径只 close listener，没有显示 unlink 新 pathname。observer 启动前，`gateway_observation.py:83-171,178-185` 创建或打开 root-owned `0700` 输出目录，并以 `O_CREAT|O_EXCL|O_NOFOLLOW` 创建 root-owned `0600` marker JSONL。若文件创建后校验/后续启动失败，清理路径关闭 fd 但不删除已创建目录或文件，因此可能留下空的或部分 JSONL 文件。`webui_gateway.py:41,641-667` 还写 gateway PID 文件；observer 模式的 finally 不走普通模式的 PID 文件 unlink 分支。
- **线程 / connection / 转发**：`webui_gateway.py:318-326,454-492` 为 accepted Unix-socket 请求启动 handler thread。`webui_gateway._handle()` 在该 accepted-request handler thread 上同步调用 `observer.observe()`；该调用中的 header parsing、peer metadata 读取、记录构造与 enqueue 都在此线程执行。独立的 observer writer thread（`gateway_observation.py:298-314,316-385,459-496`）消费队列并写 JSONL。精确 marked GET 随后落入普通转发路径：连接 `127.0.0.1:8774`，启动两个 relay threads，并将已读请求头 `head_raw` 原样作为 relay 前缀转发。故即使 handler 响应固定 `/healthz`，marked GET 仍会产生 gateway handler、loopback connection 和 relay 线程副作用。
- **JSONL / stop / timeout**：`gateway_observation.py:26,414-434,459-496` 将 `ready` 及至多一条 observation 或 terminal 记录写入 JSONL，总量上限 8 KiB。`webui_gateway.py:327-332` 调用 `observer.stop()`；`gateway_observation.py:390-412` 只请求 writer 终止/排入 terminal，不 join writer thread。观测 deadline 只使 writer 写 terminal 并退出，`webui_gateway.py:318-319` 的 gateway accept loop 不因该 timeout 停止。
- **日志 / readiness**：`webui_gateway.py:511-524,610-667` 的 double-fork 父进程会在最终 child 启动 listener 前退出；这不能证明 child 已完成 listener 或 observer readiness。observer `ready` JSONL（`gateway_observation.py:465-471`）没有 gateway PID 或 start ticks，不能单独关联到具体 gateway 实例。该文件把标准输入/输出/错误重定向到 `/dev/null`；两份候选没有显式 logger 写入，但 nginx、supervisor、systemd/journald 或设备侧其他日志效果未经验证，不能据此认定“无日志副作用”。

**测试覆盖边界**：记录的 124 passed 包含合成 WebUI ASGI/sentinel handler 覆盖；`proxy/tests/test_gateway_observation.py` 中 socket、fd、clock 与 writer-thread factories 在相关测试里使用 stub。不过 `test_concurrent_matching_requests_enqueue_exactly_one_record` 使用真实 `threading.Barrier` 和 12 个真实 Python threads，只证明并发 enqueue 行为，不证明真实 gateway transport/relay/process/filesystem lifecycle。整体测试不验证真实 gateway transport、loopback relay 或 observer 的真实进程/文件生命周期。上述仅为本地静态审计；不替代独立审查，也不提供现场 P1/P2 证据。G0-P2、G0 与部署继续 **BLOCKED**，live P1/P2 只读取证须单独完成。

### 有界写入者模型

“无写入”只按本扩展的已知写入主体建立有界证明，不要求停止正常 journald，也不宣称整台 NAS 零写入。必须分别识别并控制：旧 unit cgroup 中的进程、已知 supervised children、扩展外部任务和正在处理的请求。`asyncio.to_thread()` 的 awaiter 被取消不代表线程已结束。正常 journald 写入不等于旧扩展 writer，也不得要求停止 journald；只有当计划需要检查相关 journal 副作用时，才将其作为预期外部副作用列入审查与授权。takeover/socket 写入是单独的预期副作用，应明确列入范围。未知 writer、未排除的 successor 或无法证明自然结束的线程一律 **BLOCKED**。

## 分阶段证据计划

按顺序记录来源、采集时间、命令/观察范围、脱敏结果及独立复核人。静态代码证据、合成测试、历史设备摘录和实时设备观察分开记录。授权是副作用操作的前置门，不能放在所有技术证据之后：只读预检与方案审查先完成，随后核对授权，再进行经授权的现场动作，最后复核结果。

| 阶段 | 工作与证据 | PASS 条件 | BLOCKED / 停止条件 |
| --- | --- | --- | --- |
| G0-P0 本地计划审查 | 审查本文、旧运行手册归档标识及本地维护启动合同；核实文档只描述候选，不提供执行授权 | 独立 reviewer 确认当前架构事实、证据类别、阶段顺序和停止条件完整 | 仍把历史步骤当作可执行路径、将本地事实外推到 NAS，或把文档审查写成 G0/上线 PASS |
| G0-P1 新鲜只读安装与 unit 盘点 | 只读确认设备身份、安装目录/版本、相关文件存在性与哈希/owner/mode、完整有效 unit 属性、所有 drop-ins、`ExecStart`/有序 `ExecStartPost`/`ExecStopPost`、MainPID/ControlGroup 及 observer/gateway 进程和 listener 关联 | 记录带时间戳且内部一致；安装副本与待审候选的差异明确；不执行写操作、不停止服务 | 版本、权限、进程归属或命令链不明；出现与历史快照不同的配置；读取需写入或停止服务 |
| G0-P2 诊断调用链与副作用审查 | 旧 `/api/status` 候选的本地 handler/downstream 调用链已作静态审阅，结论见旧路径审阅小节；当前 dirty 候选将 marked request 改为固定 `/healthz` handler，并有 ASGI sentinel 测试。仍须审阅 gateway/observer 侧的可接受副作用，并在目标设备安装版本可受支持地只读取得时比对。 | 拟用请求的精确范围与已知副作用可说明，所选请求符合经审查的观测边界；若明确接受有限副作用，则须另行写明其边界、依据及审核结论。 | 当前旧 `/api/status` 候选会启动子进程并请求下游服务，不满足严格惰性 handler 边界；`/healthz` 仍只是未匹配设备的本地候选，gateway/observer 活动或下游副作用不清、安装版本不匹配时均 BLOCKED。不得把本地测试写成设备事实。 |
| G0-P3 生命周期、自然 drain 与 rollback 方案审查 | 仅做方案审查：根据 P1/P2 设计旧 service cgroup、已知 supervised children、外部任务、请求处理者的自然 drain 判据；审查完整备份基线、runtime-only drop-in 对完整 `ExecStart`/有序 `ExecStartPost` 的重建、失败回滚与副作用 | 独立 reviewer 认可设备版本相符的生命周期候选及可现场验证的 drain/回滚判据；不声称实际 drain/rollback 已演练 | 维护启动/readiness 未在设备安装版证实；未知 writer/successor、方案没有可现场验证的自然 drain 判据、备份或 rollback 方案不完整；要求修改官方 nginx 或手动迁移 cgroup |
| G0-P4 既有授权范围核对 | 在任何现场副作用之前检查本项目已有的明确授权；逐项比对 NAS/目标、拟执行动作、维护时间窗、预期副作用及 rollback 范围 | 既有明确授权逐项覆盖本阶段精确操作，且与 P3 审查方案一致；记录授权来源和边界 | 授权缺失、模糊、目标/时间窗不符或计划发生实质变化时，先暂停并只请求缺失部分的补充授权。笼统“继续项目”不能授权 NAS 写入或服务变更；但不因此否定已经明确且仍覆盖本目标的旧授权 |
| G0-P5 实际 writer-drain / rollback 演练门 | 若 P3 方案需要 service stop/start、drain 或 rollback 演练，须在 P4 范围核对通过后单独执行经授权的现场演练；确认旧 unit cgroup、已知 child、外部任务及请求 handler 已自然退出，并按批准范围恢复服务/配置。若完全不需要这些动作，须由 P3 独立 reviewer 根据 P1/P3 设备支持证据明确判为 N/A | 需要演练时，现场证据证明有界 writer 已结束且 rollback 恢复预期状态；不需要时，有 P1/P3 证据支持的 P3 reviewer 明确 N/A。P3 方案审查本身不能证明已演练 | 条件是否适用不明确、该类操作不在授权范围、不得用任务取消/MainPID 消失/`inactive` 代替线程或子进程 drain 证据，或存在未知 writer/successor/恢复异常时均 BLOCKED |
| G0-P6 observer 文件部署及服务动作门 | 仅当 P3 方案需要部署 observer/修改运行时配置时，分别核对目标文件、runtime-only drop-in 和每项 service stop/start/reload 动作是否落在既有授权中；若不需要部署或服务变更，须用 P1/P3 证据说明其不适用。执行前再次检查备份基线和授权范围 | 具体文件、目标、字节/权限、drop-in 完整命令链及每个服务动作均已审查并明确授权；或经独立审查证据确认没有此类动作；只执行授权覆盖的步骤 | 目标/内容未冻结、实际副作用超出授权、不能证明不适用，或需要官方 nginx/CGI 修改或手动 cgroup 迁移时停止；不得把“观察授权”扩大解释为部署授权 |
| G0-P7 经授权的 transport 与身份观察 | P0–P3 完成后先通过 P4 授权范围核对；按需通过 P5/P6 门，才可对精确单次 marked GET 做 transport/accepted-peer 观察。该 GET 只观察同一 accepted socket 的 `SO_PEERCRED` 与脱敏 header 结构，并关联 observer ready marker、gateway PID/start ticks/cgroup/listener。权威身份来源及伪造、覆盖、绕过矩阵须另以权威来源证据审查，并通过单独授权、限只读的验证检查 | transport 证据与本次 marker、accepted peer、gateway 实例相对应。身份 PASS 另须证明头来源来自受信任边界、管理员判断不能由请求方伪造，并有独立验证证据 | 单次 GET 或 observer 输出不得推出 identity PASS。未部署/ready、marker或PID不匹配、peer归属不唯一、权威身份来源未知、未完成独立伪造/覆盖/绕过验证，或请求下游副作用超范围时保持 BLOCKED |
| G0-P8 现场结果复核 | 由独立 reviewer 对照 P1–P7 原始脱敏证据、授权边界、计划偏差、回滚记录和最终状态复核 | 所需现场证据完整、授权无越界、所有偏差已处置；复核绑定明确快照 | 缺证据、证据冲突、发生越权副作用或 rollback 未验证即 BLOCKED，不宣称 G0/上线通过 |
| G0 最终技术评估 | 在现场结果复核后汇总安装身份、调用链、transport、可信身份、生命周期及 rollback 证据 | **只有 P1–P3 方案证据充分、P5 PASS 或由 P3 reviewer 根据 P1/P3 证据支持的 N/A、P7 的 transport 与独立可信 identity 证据通过、P8 独立复核通过，且 P6 已通过或有审查证据证明不适用时，才可评估 G0 PASS** | 授权本身不代替技术证据，G0 技术评估也不倒置为先要求未授权的实操结果。需要的生命周期演练不得由方案审查代替；未能证明 P5 不适用时必须 BLOCKED。任何必要证据缺失、过期或冲突时保持 BLOCKED；G0 PASS 不自动等于上线批准。 |

### 授权范围清单

先核对既有明确授权是否覆盖目标与动作；只对缺少或发生实质变化的范围请求补充授权。任何拟执行的操作计划都必须说明下列适用项；“不适用”也应说明理由，而不是默认为无副作用：

- **私有备份**：备份目录/权限、写入位置、文件范围、保留/删除方式和校验操作。
- **目标候选源码**：逐个文件的安装目标、版本/哈希、owner/mode、创建/替换/移除行为；不复制整个 dirty worktree。
- **runtime drop-in**：确切路径、完整内容、有效 `ExecStart` 与全部 `ExecStartPost` 的重建、daemon reload 及撤销方式。
- **PID / ownership 状态**：可能读写或恢复的 PID、start ticks、takeover ownership 记录及其备份/恢复边界。
- **socket 生命周期**：哪些官方/代理/observer socket 可能创建、替换、恢复或移除；禁止未批准删除。
- **observer JSONL**：输出路径、文件权限、记录范围、marker、留存/清理方式；不记录身份值、凭据或响应体。
- **日志与 journal**：列明本次可能产生的扩展日志及预期 journald 记录。journald 的正常写入不作为旧扩展 writer，也不得要求停止 journald；只有计划要检查 journal 副作用时才把该检查列入授权。
- **rollback 文件与状态**：回滚要恢复/删除的配置、源码、备份、PID/ownership 与 socket 状态，以及单次失败退出条件。

某项未经计划或授权的文件、socket、PID/ownership、日志保留或服务状态副作用一旦出现，立即停止并保留证据，不自行清理或继续回滚越界操作。

纯只读 P1/P2 预检、潜在 observer 文件/运行时配置写入与 service 动作、实际 writer-drain/rollback 演练是不同门。已有授权若只覆盖其中一类，不扩展到其他类；任何新的 observer 部署、service 操作或 drain/rollback 演练，都必须按其精确范围通过相应门后才进行。

P0 文档审查通过只代表文档符合计划质量要求；它既不是设备证据，也不自动使 P1–P8 通过。P1/P2 和 P3 为只读盘点/方案审查，不应等待尚未进行的部署或运行期观察。合成测试只证明其覆盖的本地候选行为，不证明 Linux、fnOS、NAS、真实身份来源或运行期安全。

## 条件式生命周期候选（不是操作步骤）

当前唯一可继续审查的设备生命周期方向是：先完成真实安装/有效 unit 只读盘点和完整备份基线；若设备负责人另行批准维护窗口，再正常停止扩展 service，证明旧 unit cgroup、已知 supervised children、外部任务和请求处理者自然 drain；最后评估单一 runtime-only systemd drop-in 是否能重建完整有效 `ExecStart` 与全部有序 `ExecStartPost`，成对选择已审查的维护 `run/ready`，并预先证明失败回滚。任何步骤都尚未在 NAS 上验证。

此候选不得修改 fnOS 官方 nginx/CGI/飞牛音乐，不手动迁移 cgroup，不用终端进程代替 systemd lifecycle，也不以取消 awaiter、MainPID 消失或 `inactive` 单独声称 writer 已排空。正常 service stop/start、drop-in、文件替换、observer 部署和 rollback 每一类均须独立审查与具体操作授权。若现场无法精确恢复原有效命令链或自然 drain，停止为 **BLOCKED**，不临场改用旧热替换方法。

## 本地源码参考哈希（非设备 manifest）

以下 SHA-256 是 `main@839fd314015e6e9fd1c1da5641a5c0d20d61b722` 加 dirty worktree 的**本地源码参考**。它们用于说明本次审阅所见候选字节；不是提交树哈希，不表示 NAS 安装文件相同，不能作为 NAS 安装 manifest、部署清单或通过 G0 的证据。实际设备文件必须通过新鲜只读盘点逐一验证存在性、内容及元数据。

| 本地候选源码 | 本地 SHA-256 |
| --- | --- |
| `proxy/app.py` | `3BFB0EEA25A2D04C760427F9896FE4438C3BD16E968A9451872A69115EEF25C3` |
| `proxy/takeover.py` | `82CA849B68E165580491E6E8D70824EBA5BFB06D4ED26AD7AE873253F8B81AB5` |
| `proxy/webui_gateway.py` | `69CF16C8CE94DE9353987DE63CB3D5D937AAEA6E7425BED0E029BD1F0AD024EB` |
| `proxy/gateway_identity.py` | `028022897609444A4D6870CB2BE6CA75AF8787BE9E622CC73FB7FF1C0C392FDC` |
| `proxy/gateway_observation.py` | `22B19521327719C6748814EFB6974E117C6B7F5B2066246CFC84E7EADCAAEFB5` |

历史 proposal/runbook 中的旧 commit 与旧 hash 只保留为历史材料，不能代替本地当前参考或设备 manifest。由于 dirty worktree 可能继续变化，实施任何新计划前必须重新读取 Git 状态和源码哈希，并重新进行独立审查。

## 当前判定与未完成项

**BLOCKED：不得按本文件对 NAS 执行维护或上线。** 2026-10-25 的新鲜只读快照确认安装仓库版本为 `2.8.2`，并记录了有效 unit 属性、当时 cgroup 进程、`/websocket` 到 `trim_cgi` 的路由，以及身份头配置/二进制字符串线索；但已安装的 `webui_gateway.py` 与 `takeover.py` 哈希不同于本地候选，安装路径还缺少本地 identity/observation 模块。因此不能把 dirty 本地源码行为当作设备行为。新测试进一步证明，能以 `www-data` 连接安装 socket 的本地调用者可用请求方自带的 `x-trim-isadmin: true` 使 `/api/about` 从 403 变成 200；它没有经过统一网关，不证明远程匿名可达或官方代理会转发伪造头，但确认了本地 socket/声明信任边界不能作为管理员身份来源。可信身份头由谁注入、运行时注入路径及远程绕过面仍未证明；也未证明 observer 就绪、旧 writer 已自然排空、没有 successor 或 rollback 安全。旧 `/api/status` 调用链已静态审阅并否定；当前 dirty 本地候选改用固定 `/healthz` handler，局部 ASGI/sentinel 测试通过，但未确认设备安装副本匹配，也未验证真实 gateway/observer/service/log 副作用。当前本地 observer 不记录身份值或管理员真值；本地 identity 解析也不能阻止单个格式正确的伪造管理员头。维护启动只抑制九类新进程启动动作，不是全局只读锁，且不排空旧请求、线程或外部 writer。不得据此盲目重启、安装或扩大操作授权。

后续顺序为：完成 P0–P3 的本地计划审查、新鲜只读设备盘点及生命周期/自然 drain/rollback 方案审查；已知旧 `/api/status` 不符合惰性 handler 边界，当前 `/healthz` 候选需先经独立源码审查及受支持的设备安装版本比对。随后由 P4 核对既有明确授权覆盖。若授权不足，在进入相应副作用操作前请求补充授权。之后仅对授权覆盖且适用的项目通过 P5/P6/P7 各自门槛执行，最后进行 P8 独立现场结果复核。本轮本地实现不构成部署/身份/授权结论。
