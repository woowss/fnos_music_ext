# 手动保存与整歌单导入计划

> 更新：2026-10-25。本文是开发与验收计划，不表示保存功能已经实现。
> 基准：`E:\\fnmusic-ext-dev`，`main@839fd314015e6e9fd1c1da5641a5c0d20d61b722`，`VERSION=2.10.2`。
> 本次文档复核合同：`FNMEXT-OFFICIAL-FILE-AUTH-PREFLIGHT-20261025-01-A1`；不是实施或部署授权。
> 历史方案引用（实施前待重新绑定）：`FNMEXT-SINGLE-SAVE-20261018-01 v1.1`、`G0-A1`。

## 1. 产品范围

- 播放保留在飞牛音乐原生界面。
- 从扩展管理页显式发起单曲保存、用户选定整张歌单的导入。
- 结果是 NAS 音乐库中的普通音频文件。
- 实际 `.ncm` 输入转换为真正可解码的 `.flac`；其他格式保留。
- 播放、收藏、加入歌单、听完整首均不等于用户请求保存。

首版不包括自动同步、整理已有媒体或直接写官方音乐数据库。
音频取得和 NCM 转换的受支持路径仍需验证；账号登录、预览可用不等于文件可下载。

## 2. 版本与已有工作

实际开发工作区为 `E:\fnmusic-ext-dev`，Git common directory 为其 `.git`。
独立的 `E:\fnmusic` 桌面客户端工作区不属于本批实现范围。

本次文档修订前的只读快照（2026-10-25）：工作区位于分支
`codex/fnmusic-prelaunch-20261025`，HEAD 为
`d1dd9e604f065ba56fcc781cfd087a30a369ab19`，当时工作树干净。只读命令
`git ls-remote origin refs/heads/codex/fnmusic-prelaunch-20261025` 返回该提交，
`git ls-remote origin refs/heads/main` 返回基准提交
`839fd314015e6e9fd1c1da5641a5c0d20d61b722`。这核验的是远端 `origin` 上的分支引用，
不代表本地存在对应的 `refs/remotes/origin/*`。当前工作树因本次文档修订而有未提交改动。
候选分支的提交级同步不表示 PR 已合并或应用已部署。
本文早先记录的 38 条未提交状态路径属于更早的候选快照，现已过期；该记录不表示
当时每个未提交文件都进入了当前分支。

| 项目 | 已有证据 | 尚不能证明 |
| --- | --- | --- |
| 账号歌单预览 | 本地实现；此前相关 89 项合成测试通过，独立复核通过 | 真实平台、NAS、安装包、保存能力 |
| 预览完整性 | 保留顺序、重复项及异常位置；分开报告成员枚举与元数据完整性 | 持久快照、音频可用性、导入授权 |
| 离线保存任务账本 | `proxy/manual_save_jobs.py`、`offline_tests/test_manual_save_jobs.py`、`docs/OFFLINE_SAVE_JOBS.md`；19 项任务账本测试与 17 项 core 回归通过；R2 Sol 独立复审 PASS | 这是纯离线 SQLite 任务记录；没有管理页/API、账号授权或可信网关、生产文件系统接入、音频获取、NCM→FLAC、NAS 入库或上线能力证明 |
| OfflineLinuxStore 候选 | [POSIX 候选契约](OFFLINE_POSIX_STORE.md)；此前隔离 Linux 运行 22 项 POSIX 测试通过 | 候选没有接入服务、HTTP 路由、任务 worker 或 NAS 曲库；测试不证明生产授权、NAS/Btrfs 耐久性或真实曲库行为 |
| Linux 账本/core/POSIX 集成候选 | [流水线验证记录](OFFLINE_SAVE_PIPELINE.md)：隔离 Linux 运行 5 项测试通过、0 跳过 | 仅为 `/tmp` 中合成 SQLite、测试字节及临时目录；没有真实音频、NCM 转换、生产接线、目标曲库或 NAS 验收 |
| 手动保存与导入 | 本文方案与验收条件；用户保存/导入功能尚未实现或部署 | 实际用户保存、整歌单导入及 NAS 落库 |
| 已安装网关脚本 | 2026-10-22 的历史观察中，已安装 `proxy/webui_gateway.py` 哈希 `b87e39fd8e03fed104a59e9926891c353c4c09d695142182486fe074d78fdb22` 与旧 `main@c4a257a007c355b483c9916df834bf615d5d7351` 源码一致 | 该文件不同于当前基准 `main@839fd314015e6e9fd1c1da5641a5c0d20d61b722` 的哈希 `8113788dfe63a42584148989e6802bb5e6bbf49dd5c1c0c96025ff61af9f511f`；当前候选分支尚未部署；该观察不能证明整套已安装源码与当前工作区一致 |
| 目标曲库 | 原生 UI 显示 `music` 库位于 `/vol1/1000/music` | 最终目标选择、写权限、覆盖授权 |
| NCM、实际落盘与入库 | 尚未执行真实验收 | 已转换、已保存、已索引 |

内部预览接口为 `GET /api/v1/user/playlists/{playlist_id}/preview`，
当前最多处理 1000 个位置，所有响应均为 `import_ready=false`。
它是请求期间生成的预览，不能直接当作完整导入清单或下载保证。

同步后测试门禁变更：`pytest.ini` 与 `scripts/run_tests.py` 将 `offline_tests`
作为独立、串行 `offline` 套件纳入标准测试入口；runner 回归覆盖其唯一调度与选择。
为避免同一实现被顶层模块名和 `proxy.*` 包名分别载入，离线测试统一使用包导入；
导入副作用测试通过独立 `proxy._..._import_probe` 模块检查，避免 reload 共享模块并替换类身份。

### 历史 Windows 验证（2026-10-24，Python 3.14.6）

以下是历史 Windows 运行结果，不代表当前工作树在本次复核中重跑：未安装可选覆盖依赖的全局环境中，runner
回归为 `12 passed, 3 skipped`；其中 2 项是 POSIX-only process-group cleanup/cancellation，
另 1 项因缺少可选 `pytest-cov` 覆盖依赖而跳过。最终仓库外临时 venv 安装 CI 声明的
Python 测试依赖后，runner 回归为 `13 passed, 2 skipped`，两个 skip 均为上述 POSIX-only
检查。core→jobs 与 jobs→core 两种 pytest 顺序均为 `36 passed, 50 subtests passed`；
unittest discover 为 36 项通过。标准 `offline` runner（包含管理 claim 测试）为
`99 passed, 50 subtests passed`。在该历史 Windows 运行时，Linux/CI、生产保存/导入接入、
G0、NCM→FLAC 和 NAS 入库均未验证；后续隔离 Linux 运行见下节，但这些生产门槛仍未通过。

### 历史 Linux 隔离验证（2026-10-25）

- `offline_tests/test_manual_save_posix.py` 的 Linux 运行此前执行了 22 项 POSIX 适配器测试，22 项通过；[POSIX 候选契约](OFFLINE_POSIX_STORE.md)说明其覆盖范围与复现命令。测试验证的是隔离临时目录内的 Linux 文件语义，不是生产服务或曲库。
- `offline_tests/test_manual_save_posix_pipeline.py` 的另一组账本/core/POSIX 集成运行记录在[流水线验证文档](OFFLINE_SAVE_PIPELINE.md)：5 项通过、0 跳过；合成账本、目标目录、stage 和相关测试临时文件均位于隔离 `/tmp` 路径。
- 两组运行使用测试字节和临时目录；小型 MP3/FLAC 负载不是可播放音乐。它们没有取得真实音频，没有实现或验证 `.ncm`→`.flac`，没有接入生产服务/管理页/API，没有写入 `/vol1/1000/music`，也没有触发 NAS 曲库索引或完成 NAS 验收。`OfflineLinuxStore` 仍是离线候选，不能称为生产适配器。

## 3. NAS 只读证据与限制

以下是 2026-10-21 至 2026-10-22 的有限观察，后续部署前须复核：

- 管理界面显示扩展 `2.8.2`；此前原生音乐界面显示 `1.0.10 (0.8.43)`。
- `/app/` 经官方 `trim_http_cgi` 中间层转发，不能假定 nginx 直接连接扩展。
- `trim_http_cgi` 进程为 root，程序为 `/usr/trim/bin/trim_http_cgi`，
  属于 `trim_http_cgi.service`；nginx worker UID 为 712。
- 官方 HTTP socket 为 `root:www-data`、`0770`；官方 API scope socket
  为 `root:root`、`0760`；扩展管理 socket 为 `root:root`、`0666`。
- 扩展代理与管理网关在 `fnmusic-ext.service` 下以 root 运行。
- 所测 UID1000 用户可以直连扩展 socket。只读 GET `/api/about` 在无管理员头时
  返回 403，自行声明管理员头后返回 200；回环端口 8774 也出现同样结果。
- 未执行特权 POST；不能将此结果扩展为远程未登录攻击或所有本地用户均可访问。
- root 读取当前代理和管理网关的进程环境，只用静默匹配输出状态码：
  两个进程都未发现非空 `TRIM_API_TOKEN`。没有输出令牌值。
- 标准位置未取得已安装的 API scope 文件；已安装 scope 仍未核实。
- 75 秒的被动 socket 观察在正常打开“关于”页面期间未捕获匹配的已建立连接。
  这不证明没有转发，也没有取得实际 accepted peer 的证明。

以上“没有改变 NAS 配置、权限、服务或音乐文件，也没有触发扫描”仅适用于 2026-10-21 至 2026-10-22 这一只读观察窗口。之后的 Linux 合成测试只写入隔离 `/tmp` 临时目录；之后的 G0 检查是另行记录的观察，不应与该窗口混为一谈。没有证据表明这些后续活动改动了生产曲库文件。

## 4. G0：可信身份与执行边界

**G0 当前未通过，不开放新的保存、导入或任务执行入口。**

官方网关文档定义了会话校验后的可信身份上下文：
`X-Trim-Userid`、`X-Trim-Isadmin`、`X-Trim-Username`。
优先复用这一身份链，但接收端必须证明请求经过受保护的官方通道。

待补齐或实现的门槛：

1. 确认实际连接扩展 socket 的进程、内核身份及服务归属。
2. 设计可隔离的 socket 入口，身份不匹配、配置缺失或验证失败时拒绝。
3. 验证官方链路如何处理伪造、重复和大小写变体身份头。
4. 扩展严格解析权威身份，拒绝重复或冲突字段；用户名仅用于展示。
5. 封住直连 8774、带前缀与不带前缀路由、内部执行端的授权旁路。
6. 将可信操作者 UID 绑定到任务与文件权限判断，忽略或拒绝客户端伪造 UID。

共享 peer UID、客户端路径和管理员头本身均不足以完成这条证明链。
若转发至 WebUI，须使用受保护内部通道或经验证入口签发的短期证明；
不能根据未经验证的管理员头自行签名后声称身份可信。

鉴权验收须覆盖未登录、普通用户、过期与撤销会话、超时、畸形响应、
伪造与重复头、签名篡改、重放、跨主体任务操作和跨站提交。
拒绝必须发生在目录探测、创建任务、取得音频或写文件之前。

## 5. 应用授权与目标目录

应用 API 身份、当前操作者身份、目录授权和明确保存意图是不同条件。

| 官方能力 | 用途 | 精确 scope |
| --- | --- | --- |
| `trim.file.getSharedAccessibleFolders` | 查询管理员授权给应用的目录 | `trim.file.sharedAccess` |
| `trim.file.checkUserACL` | 按可信 UID 检查路径的读、写、删除权限 | `trim.file.userAcl` |

官方文档要求后端通过官方 socket，使用 lifecycle 环境中的 `TRIM_API_TOKEN`。
不得把令牌持久化到 `.env`、数据库、前端、日志或全局 systemd 环境。
当前源码只声明 `trim.file.userAccess`，token 跨 lifecycle、systemd、容器的
生命周期和传递方案尚未建立，当前进程检查也不能代表所有安装方式。

#### 当前模板启动链复核（源码静态检查，2026-10-25）

- `packaging/fpk/manifest.in` 声明 `micro_app = true`；`packaging/fpk/config/resource`
  当前只声明 `trim.file.userAccess`。
- `packaging/fpk/cmd/main` 调用仓库根目录的 `extend.sh`；扩展脚本再交由
  systemd 管理 `fnmusic-ext.service`。服务模板以 root 启动代理和管理网关，
  显式设置的环境变量只有 `PYTHONUNBUFFERED=1`。
- 对当前应用源码（排除 docs、vendor、logs）的定向检索，没有找到读取
  `TRIM_API_TOKEN` 或调用官方 API scope socket 的代码。因此源码中尚未建立
  从 fnOS lifecycle 到 systemd 代理进程的受支持令牌交接链。
- 以上均为当前模板和源码的静态事实，不等于设备运行态证明。不能假定
  `cmd/main` 收到的 lifecycle 环境会自动穿过 systemd 或容器边界；已有进程
  未发现 token 的只读观察也不能替代对受支持 lifecycle 的确认。取得官方
  API token 的安全、受支持交接方式前，不接入该 API、不将 token 写入配置，
  也不以 root 文件访问绕过官方授权。
- 此项未改变 G0 状态：可信网关身份与安全服务生命周期仍须单独通过审查；
  本文不授权修改 NAS、scope、manifest、systemd、Docker 或安装包。

上述目录与 ACL 能力表列出系统最低版本 `1.2.0401`、App 最低版本 `1.34.0`；
当前包模板最低系统版本是 `1.2.0`。须核实实际能力和兼容分支，不能仅比较版本号。
变更 scope、安装、权限或服务需要另行授权和部署方案。

保存目标必须同时满足应用授权、操作者权限和本次明确选择。
缺失、不支持、超时、失败或无法识别的权限结果均拒绝保存。

现有目录 UI 不能作为新保存链的授权依据：`authorizeDir` 忽略授权失败，
`fsCheckDir` 异常会进入 skip；现有 fs-check 还会创建并删除探测文件。
新签名密钥或会话凭据也不能加入现有普通配置接口的读写往返。

## 6. 保存执行约束

以下是安全边界与候选行为，尚未实现；最终冲突策略仍待明确。

- 服务端绑定受限目标目录标识，不接受任意 UID 或越界路径。
- 防止路径穿越、符号链接替换、校验后路径变化和并发冲突。
- 检查目标类型、资源上限与文件名；最终提交时重新检查必要条件。
- 不覆盖现有文件；冲突必须显式报告，不能默默跳过、改名或换目录。
- 使用本任务的临时空间；音频与必需元数据就绪后再提交最终文件。
- 提交不能覆盖已有文件，不能把半成品发布为成功文件。
- 失败和取消只清理本任务拥有的临时内容，不删除已有媒体。
- 单次重复提交与并发重复提交必须具有明确的幂等结果。
- 任务查询、取消和重试按操作者授权；任务 ID 本身不是访问凭据。

文件命名、重复项处理、封面和歌词要求、任务持久化及恢复策略，
需在实现合同中明确；不能靠现有播放缓存路径隐式决定。

## 7. 格式规则

- 对有权且以受支持方式取得的实际 NCM 输入，生成真正的 FLAC 音频。
- 检查真实容器、编码和可解码性；仅后缀、MIME 或 UI 标签不构成转换证明。
- NCM 内部音频如需重新编码，记录结果；转成 FLAC 不会恢复有损源的音质。
- 其他格式保留，不把所有音频统一转为 FLAC。
- 不支持、取得失败或转换失败时明确失败，不发布伪装的 FLAC。
- 不批量转换已有 NAS 文件，也不以其他版本曲目静默替代原曲。

## 8. 单曲验收

须先通过 G0，建立具体样曲与目标目录，并获准执行该次真实文件写入。

1. 管理页展示明确的曲目、目标、格式预期和保存操作，由用户触发。
2. 未授权、过期身份、伪造头和直连入口不能创建任务。
3. 状态区分等待、执行、成功、失败和取消，并报告稳定的错误原因。
4. 重复提交不重复落盘；冲突、权限、空间、网络与转换失败均有明确结果。
5. 最终路径与确认目标一致，真实格式正确，可独立解码。
6. NCM 用例得到真实 FLAC；非 NCM 用例保持其音频格式。
7. 原有文件不被覆盖，取消和失败不留下被当作成功文件的半成品。
8. 经获准的官方索引流程后，分别验证音乐库可见性和播放。

任务成功响应、文件存在和 UI 显示 FLAC 均不能代替完整验收。

## 9. 整歌单验收

单曲执行链通过后才能复用于批次。

- 用户明确选择歌单、查看成员数量并确认目标。
- 使用可验证的有序成员快照；保留重复与异常位置，不复用会丢项的播放镜像。
- 当前临时预览不能直接冒充持久清单；超限、不完整或来源变化时不宣称整单就绪。
- 每个源位置有结果，总数、成功、失败、冲突、取消与未执行数量可核对。
- 部分完成不能标为整张成功；不静默跳过、替换曲目或改变目标。
- 限制并发、队列与临时空间；取消停止派发新任务并如实报告进行中项目。
- 重试只处理明确选定的失败项，重新检查权限、来源和冲突。
- 整单成功须逐项满足单曲标准，并验证官方曲库中的结果。

## 10. 实施次序与回滚

以下各项均为未来、逐项受门槛约束的工作计划，不是当前实现状态，也不授予 NAS、scope、权限、安装、服务、扫描、曲库写入或回滚操作权限。真实写入前仍须通过 G0，并具备具体样曲、目标曲库目录和该次写入的明确授权。

1. 补齐 G0 证据，确定可信入口、受保护执行通道与具体任务合同。
2. 核实 scope、版本、token 生命周期与目录授权能力。
3. 实现鉴权、目标预检、任务隔离与不覆盖提交；保存能力默认关闭。
4. 验证获支持的音频取得和 NCM 转换，完成单曲本地合成验证。
5. 独立审查实际 Diff，保留已有歌单预览及其回归证据。
6. 在 G0、样曲、目标目录和单次授权均满足后，对具体样曲和目标完成获准的 NAS 单曲验收。
7. 单曲链路经过独立验收后，另行设计并实施整歌单清单与批次执行，再按当时批准的目标做相应验收。

不得以调用 native download prepare/delete 路由来探测能力：prepare 可写音频，
delete 不等于停止生产者。不得用安装或恢复脚本替代本地验证。

遵守仓库约束：不修改官方 nginx；不修改官方二进制或音乐数据库。
当前设备调查保持只读；变更权限、配置、服务、安装、扫描及音乐写入不由本文授权。

将来若获准执行变更，回滚先关闭保存入口和执行，再撤销本批授权并恢复本批文件；
这一顺序仅是候选原则，不授权现在执行回滚或触碰 NAS。
保留现有预览修改和已有媒体，不整树重置或批量删除文件。
代码审查、本地测试、部署和真实媒体验收分别记录，不互相替代。

> 文档快照审计（2026-10-25）：更早的 `D08AEC4F851C01C32C357702AD6C4CC8F9AC2CDCF6CEE5895F69A4CB0C294DC5` 预编辑字节未被保留，现已无法恢复；因此那一轮原始差异及“只改了 §5”的说法均无法证明。本轮修改前已将可验证的 `EFA2098E9F342DB008501553DF289EABAD16A51E4D33DE14EEEFB877C37294EB` 快照备份并核验哈希；本次内容如需回滚，只能回到该快照，不能声称恢复到更早的 D08 快照。

## 参考

- [官方统一网关](https://developer.fnnas.com/docs/core-concepts/gateway-registration/)
- [官方 API 调用](https://developer.fnnas.com/api/calling/)
- [共享目录授权](https://developer.fnnas.com/api/authorization/shared-access/)
- [用户文件权限检查](https://developer.fnnas.com/api/authorization/file-acl/)
- 仓库源码：`proxy/webui_gateway.py`、`webui-service/app.py`、
  `webui-service/static/app.js`、`packaging/fpk/config/resource`、`fnmusic-ext.service`。

> 2026-10-22 状态补充（`FNMEXT-GATEWAY-METADATA-20261022-01`）：新增离线组件只解析未经信任的身份头 claim 并读取 Linux `SO_PEERCRED` 元数据，不构成认证或授权；用户名 claim 按去 OWS 后非空校验且不假定编码，真实网关兼容性未验证；未接入运行时或保存功能，G0 仍为 BLOCKED。

> 2026-10-23 状态补充（`FNMEXT-G0-OBSERVE-20261022-01`）：本地准备默认关闭的一次性网关观测补丁、合成测试和部署/回滚 runbook；当前仅有本地离线测试证据，独立审查待完成，未部署、未运行 Linux/NAS 或真实 socket 验证。诊断退出会保留 PID 记录，由 runbook 串行核验和人工处理 stale 记录；service cgroup 关系未证明时部署 BLOCKED。记录仅供诊断，不认证、不授权、不开放保存功能，G0 仍为 BLOCKED。

> 2026-10-23 状态补充（`FNMEXT-G0-OBSERVE-20261022-01` 首轮复审返工）：首轮 Sol 独立复审对草稿返回 FAIL，定位为合法 HTTP/1.0/1.1 零头请求未被识别；本地最小修复现已纳入待复审快照。修复前新增四种零头场景均按预期失败，修复后 observer 文件 53 passed，组合 observation/identity/musicbox_service/playlist_preview 为 230 passed、1 条 deprecation warning，exit 0。复审后的 Sol 独立复审尚待完成；runbook 已绑定代码哈希、runner 文件集、隔离环境说明、exit code 与外部 raw logs。没有部署、真实 socket/fork、网络或 NAS 验证；G0 与 NAS 部署仍 BLOCKED。

> 2026-10-24 状态补充（`FNMEXT-MANAGEMENT-CLAIMS-20261024-01`）：当前本地候选在管理 API 与 gateway 的两个宿主特殊端点统一拒绝重复或畸形 identity claims，并在受保护动作前失败关闭。此变化只消除解析顺序歧义；单头伪造、可信网关来源、直连入口保护、操作者授权及 fnOS 实际头格式仍未验证，G0 仍 BLOCKED。旧诊断/维护网关哈希仅为历史快照，不能用于部署当前候选。本地合成回归命令 `-B -m pytest -p no:cacheprovider -q offline_tests/test_management_claims.py proxy/tests/test_gateway_identity.py proxy/tests/test_gateway_observation.py` 通过 204 项；新离线测试内含无依赖镜像文件白名单/COPY 检查。因测试环境缺少 PyYAML，未能收集现有 `container/test_container.py`，没有安装依赖或构建镜像；本地测试不证明真实 fnOS 头格式或可信身份链。
