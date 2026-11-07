# 整歌单保存 · 部署检查单（P7 前的部署窗口）

> 状态：**检查单（离线准备，尚未执行）**。本会话已授权在安全门通过后自主进行受控 NAS 操作；本文件不扩大该授权范围。不得为每个例行步骤重复索要同一授权，遇到超范围动作、未知权益或无法判断的影响则停止并报告。
>
> **当前状态：BLOCKED。** 尚无针对本次设备运行状态、经验证的停/启与回滚方案。完成 §0.1 并记录
> 可复核证据前，只允许只读盘点，以及写入独立隔离备份目录的备份/恢复验证；不得停止/启动/重启服务或 worker、编辑配置、安装/升级/降级
> FPK、对生产路径恢复备份或执行下游运行手册中的写操作。本文件后续步骤均受此门禁约束，不能作为绕过门禁的依据。
> 关联：合同 [`docs/MANUAL_SAVE_PLAYLIST_JOB.md`](MANUAL_SAVE_PLAYLIST_JOB.md) §12、P7 运行手册
> [`docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md`](MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md)、
> 交接 `E:\fnmusic\PROJECT_HANDOFF_20261101.md` §6。

## 0. 为什么需要部署

此前部署状态记录为 **2.10.2**（`f5fe383` 时代的代码）；每个部署窗口都必须重新核对设备当前版本。
2.10.2 包内容对照显示其**不包含**本轮 P1/P2/P4/P5/P6 的整歌单保存代码
（账本 reason 扩展、快照 producer 端点、适配层、网关 6 端点、管理页歌单入口）。
P3 只读观察可以用临时目录规避部署，但 **P7 真实验收必须在部署后进行**。

<a id="lifecycle-gate"></a>
## 0.1 BLOCKED 前置门（覆盖本文件全部设备操作）

**门禁现为 BLOCKED。** 只有下面各项都已针对部署窗口内观察到的实际设备状态验证、记录并经独立复核后，
才可将状态改为 PASS。当前资料、代码审查、合成测试、此前 NAS 状态或用户授权本身都不能替代这组证据。
任何一项无法验证，就保持 BLOCKED，不停止服务、不改配置、不安装/升级/回滚，也不启动服务。

- [ ] 记录本次设备实际 fnOS/扩展包版本及包哈希、服务 unit 与 drop-in、容器/worker 状态、配置解析来源、
  jobs DB/曲库/暂存规范化路径和挂载；证据应与**本次窗口**绑定，不能沿用历史记录。
- [ ] 只读盘点所有在途歌单任务、单曲保存、worker 和 DB 写入者。每项在途工作必须先按其状态安全完成，
  或使用已验证的产品流程取消并核实已进入终态、无文件/账本副作用仍在进行；禁止强杀进程来“排空队列”。
  确认任务如何被恢复/重新派发，以及停止服务、应用中心安装和启动服务是否会触发后台任务。
- [ ] 对照当前设备真实 unit、容器监督器和 fnOS 应用中心生命周期，形成具体停机、启动、健康检查和失败恢复步骤；
  在与本机版本、包、配置及数据快照一致的隔离副本上实际验证停/启行为与回滚恢复，并确认不会重复派发在途任务。
  不能用服务当前 `active`、健康端点、静态审查或一次成功安装代替这项验证。
- [ ] **在任何生产停机之前**，对 `.env`、jobs DB（用 SQLite backup API 取得包含已提交 WAL 内容的一致快照）及
  可能受影响的曲库/暂存状态建立独立备份；校验完整性并在隔离副本实际验证恢复。不得以“停服后再备份”作为
  停服前没有备份的理由。明确每项恢复后的版本/路径/权限，以及验证失败时如何回到当前状态。
- [ ] 明确回滚包、系统是否支持该降级路径、包/配置/DB 兼容性和可执行恢复方案；在隔离副本验证到服务健康、数据
  可读、开关保持关闭且无重复任务。若无法验证当前设备适用的回滚路径，**不得用卸载/重装或直接安装旧 FPK 试探**。
- [ ] 记录以上证据的快照标识、执行时间、命令/结果摘要和独立复核结论；确认本次动作处于当前会话授权范围。记录授权范围不替代本门任何运行证据，也不需要逐项重复确认。

**门禁不可由下游步骤绕过**：§1 的配置示例只说明目标值；§1.2 的停机/备份、§4 的安装和验证、§5 的开关/重启、
§6 的停止/恢复/降级，以及关联 P7 运行手册中的任何设备写操作，都必须先满足本节。启用高权限写入还必须独立通过 §0.2。
Build、离线测试或只读盘点即使通过，也不会把 BLOCKED 自动变成 PASS。

<a id="identity-gate"></a>
## 0.2 高权限写接口身份与路径门（独立上线门）

**当前状态：BLOCKED。** §0.1 的生命周期门通过也不会自动通过本节。只有对同一实际 fnOS 版本、扩展包版本/哈希、
运行配置、socket 规范路径和 8774 监听/访问路径取得真实运行证据，并经独立审查后，才可将本节改为 PASS。
未知或不可验证时，保持本门 BLOCKED，不执行 P7 写入验收。关闭门必须同时确认
`FNMUSIC_MANUAL_SAVE_ENABLED`（单曲/全局总开关）和
`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`（整歌单开关）的**实际生效值均为 false**；任一为 true、缺失、
格式无效、来源冲突或无法确定，都不满足关闭门。若发现现有开关已开启，先按已验证的服务生命周期和回滚方案处理；
不能为通过检查单而直接改生产配置、重启或假设 `.env` 值已生效。

- [ ] 从实际浏览器/管理页入口开始，追踪生产请求经身份认证入口、反向代理/网关、Unix socket 到写路由的完整链路；
  以实际配置和运行请求确认 fnOS/1Panel 官方身份头（如 `X-Trim-UserID`、`X-Trim-IsAdmin`）由哪个受信任组件生成。
  证明入口会剥离或覆盖调用方提供的身份头，管理员身份来自已认证会话/可信主体，而不是仅凭客户端可提交的头部。
- [ ] 对实际已安装版本和配置，核验 socket 的真实规范路径、监听进程、父目录权限与实际请求路径。用对应运行环境/UID
  验证非授权本地调用者确实不能绕过认证直达写接口；仅查看 `root:root 0600` 的 `stat` 输出不构成访问控制证明。
  `/run`、临时目录或其他版本上的模拟结果不能代替生产 socket 路径验证。
- [ ] 在真实运行实例上检查 8774 绑定地址，并只做无副作用的未认证/伪造头直连探测；在与当前版本及实际 socket/8774
  路径一致的隔离副本中，再分别验证受信任管理员入口与未认证、非管理员、伪造身份头对写路由的行为。隔离副本中后者须被拒绝
  且账本、曲库、暂存区无变化，前者只写入隔离测试目标。生产开关关闭时的拒绝响应本身不证明管理员授权边界。不要把
  `127.0.0.1` 绑定当作本地调用者的身份边界，也不从本项结果推断未测试路径或远程攻击结论。
- [ ] 保存精确包哈希、fnOS/扩展版本、socket 规范路径、监听/绑定信息、请求入口与拒绝结果、测试前后无副作用证据；
  由未参与实现/部署的独立审查者复核原始证据及本节范围。缺少身份头来源、生产路径测试、直连拒绝或独立审查任一项，保持 BLOCKED。

§4 中手工带 `x-trim-isadmin: true` 的 Unix socket 请求仅是指定头部下的接口烟雾检查，**不是**身份来源或管理员授权证明，
不能用来通过本节或开启保存写开关。

## 1. 部署前需完成的配置与只读检查

- [ ] 仅在 §0.1 PASS、已备份且本次影响可控后，执行者可按本会话授权通过可用的受控方法补齐/设置以下 `.env` 键；不要求用户逐项手动编辑或重复授权。若凭据围栏/工具能力使安全读写无法完成，保持 BLOCKED 并报告，不绕过围栏：
  ```dotenv
  FNMUSIC_MANUAL_SAVE_ENABLED=false          # 单曲/全局总开关；先关闭
  FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=false   # 先保持关闭，验收时再按 A10 流程开
  FNMUSIC_MANUAL_SAVE_JOBS_DB=<私有可写目录下的 sqlite 路径，root 可写>
  FNMUSIC_PLAYLIST_MANIFEST_READONLY=false     # P3 只读观察时临时置 true
  ```
- [ ] 确认目标/暂存目录沿用已验收配置（`/vol1/1000/fnmusic-test-20261031`、`/vol1/1000/fnmusic-manual-save-staging`）。
- [ ] 收集旧版 FPK（2.10.2、2.8.2）及升级前备份供审阅，核对来源和 SHA-256；这些文件本身
  **不证明降级受支持，也不构成已验证回滚方案**。回滚边界见 §6。

### 1.1 每个部署窗口重新做的只读盘点

以下信息会变化，历史检查不能代替窗口内复查。此前一次 `.env` 检查没有看到
`FNMUSIC_MANUAL_SAVE_JOBS_DB` 这个键；这只描述那次配置检查，**不证明账本文件不存在，也不代表当前配置**。

- [ ] 在 NAS 本地只检查相关键的状态，不输出 `.env` 全文、Cookie、其他环境变量或密钥。确认
  `FNMUSIC_MANUAL_SAVE_ENABLED`、`FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`、`FNMUSIC_MANUAL_SAVE_JOBS_DB` 与
  `FNMUSIC_PLAYLIST_MANIFEST_READONLY` 是否存在/是否为预期值；若检查 systemd unit 或 drop-in，
  也只筛查这几个键，不运行会打印全部 `Environment` 的命令。不要把未脱敏输出贴到聊天或工单。
- [ ] 分别按当前单曲路由、整歌单网关和音乐服务的配置读取规则，确认两个保存开关的**实际生效值**；只看
  `.env`、`printenv` 或某一次历史状态响应均不够。记录每个键的生效来源与 `false`/`true`/`unknown` 结果，
  不记录其他环境变量。两个有效值必须都为 `false` 才满足关闭门；任一 `true` 或 `unknown` 即 BLOCKED。
  当前单曲 `status` 路由的 `enabled` 由 `manual_save_route.load_config()` 读取 repo `.env`；整歌单网关
  对 playlist 开关采用 repo `.env` 优先，键缺失时才回退进程环境。要按运行中的实际版本核对这些读取规则，
  并用对应状态/禁用响应交叉确认；若来源冲突、路由状态不可用或解析结果不明确，记为 `unknown`。
  如果当前实例已有任一开关为 `true`，不得直接编辑生产 `.env`、临时关开关或重启来“修正”盘点结果；
  先使用已验证的生命周期与回滚方案确定安全操作窗口，未验证则停止部署。
- [ ] 按当前代码的配置解析规则确定**实际** jobs DB 路径：网关优先读取 repo `.env` 中的两个
  playlist 键，缺键时才回退进程环境；检查服务 unit/drop-in 时只看这些键，不回显值。`.env` 没有该键时
  不得推断没有 DB，也不得猜默认位置。若路径无法确定，停止部署。
- [ ] 对实际 DB、其父目录及 `-wal`、`-shm`、`-journal` 旁文件做只读盘点：规范化路径、是否存在、
  文件类型、属主/组、权限、大小；确认父目录可安全访问。检查 DB 是否位于会被包替换的 repo/target
  下。若在其中，记录为高风险并纳入 §0.1 的**停机前**独立备份及隔离恢复验证；§0.1 PASS 后，停机再按 §1.2
  做最终备份。路径是外部挂载也不自动代表安全或持久，须核对实际挂载、
  目录权限和目标位置。
- [ ] 对曲库和暂存目录做同样的路径、挂载、属主/权限盘点；确认它们不在会被包替换的 repo/target
  下，并记录当前文件数/总大小及暂存中的未完成文件。另核对曲库与暂存是否满足同文件系统原子发布
  要求。若目录指向不明、权限过宽或无法确认是否会被升级脚本触及，停止部署；不要用删除目录来
  “验证”路径。
- [ ] 确认无运行中的整单任务/写入者，且关闭写入开关。单看服务停止前的 `.env` 或 DB mtime
  不足以证明没有其他进程持有 SQLite 写连接。

只读路径盘点示例（先把变量填成上述**已核实**的实际路径；输出仅留在受控终端）：

```bash
repo=/vol1/@appcenter/fnmusic-ext/repo
jobs_db='<从当前配置核实；不得猜测>'
library='<从当前配置核实>'
staging='<从当前配置核实>'
for p in "$repo" "$jobs_db" "$jobs_db-wal" "$jobs_db-shm" "$jobs_db-journal" "$library" "$staging"; do
  if [ -e "$p" ]; then
    printf '\n-- %s --\n' "$p"
    readlink -e -- "$p" || exit 1
    stat -c 'type=%F mode=%a owner=%U:%G size=%s device=%d' -- "$p" || exit 1
    findmnt -T "$p" -no TARGET,SOURCE,FSTYPE || exit 1
  else
    printf '\n-- absent: %s --\n' "$p"
  fi
done
```

## 1.2 部署窗口内的独立备份（仅 §0.1 PASS 后；在升级或回滚动作前）

- [ ] **不得把本条当作试探停机的许可。** 仅在 §0.1 已验证并 PASS、在途任务已安全处理且本窗口停机方案已通过
  复核后，才按已验证步骤停止扩展服务及所有可能写 jobs DB 的 worker；确认无写者后保持保存开关为 `false`。
  停服前 §0.1 的独立备份和恢复验证必须已完成；停服后再做一次本节的最终备份。不要只靠 `upgrade_init` 自动停止服务作为唯一备份步骤。
- [ ] 在已核实的私有持久目录创建本次独立、带时间/版本标识的备份目录（不在 repo/target、
  `/tmp/upgrade-backup`、曲库或暂存目录内），使用仅 root 可访问的权限。`upgrade_init` 会清理并重建
  `${TRIM_PKGVAR:-/tmp}/upgrade-backup`，成功的 `upgrade_callback` 随后也会删除它；这是升级流程的
  临时恢复材料，不能当作长期回滚备份。
- [ ] 用 SQLite backup API 对已确定的 DB 生成自包含快照，随后对**备份副本**执行
  `PRAGMA integrity_check` 并要求结果为 `ok`；记录 DB schema/user_version、备份文件大小与 SHA-256，
  不记录或输出歌曲/账号等敏感行内容。推荐 Python `sqlite3.Connection.backup()`，不要在服务仍写入时
  仅 `cp` 主 `.db` 文件：WAL 中可能有尚未合并的已提交事务。若只能做文件级快照，必须先确认全部写者
  已停，并对 `.db` 与当时存在的 `-wal`、`-shm`、`-journal` 做同一时点的一致快照；保留源端 sidecar，
  不手工删除或截断 WAL。不能取得一致快照或完整性检查失败，就停止部署/回滚。
- [ ] 将 `.env` 以受限权限另存为版本化副本（如 `umask 077`、文件 mode `0600`）；只记录备份文件名、
  时间与 SHA-256，不 `cat`、回显或复制其内容到聊天/工单。新账本备份与配置备份必须放在上述独立目录。
- [ ] 记录曲库、暂存路径和挂载信息的盘点结果；曲库文件、暂存文件和账本都不在升级白名单内，分别
  验证备份/保留策略。不要假定路径在 repo 外就一定安全，也不要把应用自己的临时 tarball 当作这些数据的备份。

## 2. 待打包的代码范围（相对 `f5fe383` 之后的 6 个提交）

| 提交 | 内容 | 涉及文件 |
| --- | --- | --- |
| `15d7a42` | 失败项消息固定化 + 账本崩溃对账测试 | `proxy/manual_save_playlist.py`、测试、文档 |
| `fb1960d` | 合同 | `docs/MANUAL_SAVE_PLAYLIST_JOB.md` |
| `0d43c63` | 账本 reason 扩展、快照 producer、适配层 | `proxy/manual_save_jobs.py`、`musicbox-service/netease_playlist_snapshot.py`、`musicbox-service/app.py`、`musicbox-service/requirements.txt`、`proxy/manual_save_playlist_jobs.py`、测试 |
| `dd45943` | 网关 6 端点 | `proxy/webui_gateway.py`、`proxy/tests/test_manual_save_playlist_gateway.py` |
| `52c1e6d` | 管理页入口 + container 导出键 | `webui-service/static/{index.html,app.js}`、`webui-service/test_app_js.js`、`container/env_flag.sh`、`container/test_container.py` |
| `d4051b6` | P3 只读观察工具与证据模板 | `tools/netease_playlist_observe.py`、`offline_tests/test_netease_playlist_observe.py`、`docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md` |
| `345ef5a` | P7 验收运行手册 | `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` |

**明确不打包**：3 个受保护未跟踪文件（`proxy/manual_save_verified_pipeline.py`、
`offline_tests/test_manual_save_verified_pipeline.py`、`docs/OFFLINE_VERIFIED_PIPELINE.md`）
与全部 `*probe*` 脚本/测试（它们保持未跟踪、不进入任何包）。

## 3. 构建

构建方式沿用上一轮已成功的方式（交接 §6.1：NAS 上离线构建 `fnpack 1.2.4`，使用设备现有
lxserver artifact；ARM64 ensure 步骤做构建期 stub，x86_64 安装不受影响）。

- [ ] 记录 `git rev-parse HEAD` 与新包 SHA-256。
- [ ] 版本号递增（`2.10.2` → 建议 `2.11.0`，因新增 API/UI；**由用户确认版本号**）。
- [ ] 校验包内**不含** `.env`、Cookie、jobs DB、日志、`__pycache__`、probe 文件。
- [ ] **容器镜像风险**：`musicbox-service/requirements.txt` 已从 `NetEase-MusicBox>=0.5.3`
      钉为 `==0.5.3`。若镜像在安装时按 requirements 构建，需确认镜像可成功重建且版本为 0.5.3；
      若现场不便重建镜像，可临时保持旧镜像但**必须**在 P3 观察里实测容器内版本（§4.7 步骤 1）
      并按合同 §4.1 记录 deviation。

## 4. 安装与即时验证（G3；仅 §0.1 PASS 后）

1. **门禁未 PASS 不得执行。** 仅按 §0.1 中已验证且影响可控的步骤，通过应用中心手动安装新 FPK 并升级/覆盖；该常规受控操作属于本会话既有用户授权，无需重复索要同一授权。若步骤超出授权范围、影响无法判断，或实际版本、任务状态、包哈希、路径与复核快照不一致，停止并重新评估门禁。
2. 验证：
   ```bash
   ssh kotomi-root 'systemctl is-active fnmusic-ext.service; cat /vol1/@appcenter/fnmusic-ext/repo/VERSION'
   ssh kotomi-root 'curl -s http://127.0.0.1:8774/healthz'
   ssh kotomi-root 'stat -c "%a %U:%G %n" /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock'   # 期望 600 root:root
   ssh kotomi-root 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8774/api/manual-save/playlist/playlists'  # 期望 501
   ssh kotomi-root 'curl -s --unix-socket /vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock -H "x-trim-userid: 1000" -H "x-trim-isadmin: true" -H "x-trim-username: kurumi" -H "x-fnmusic-save: 1" http://127.0.0.1/app/fnmusic-ext/api/manual-save/status'
   ```
3. **默认关闭**验证：总开关与歌单开关的实际生效值都必须是 `false`；单曲状态端点应报告全局保存关闭，
   整歌单端点应返回 403 `disabled`（总开关关闭）或 `playlist_disabled`（仅整歌单关闭），
   且无账本文件被创建（`FNMUSIC_MANUAL_SAVE_JOBS_DB` 路径不存在或 mtime 不变）。
4. 检查容器内 `printenv FNMUSIC_PLAYLIST_MANIFEST_READONLY`（应为空/未设置 = 关闭）与
   `python3 -c "import importlib.metadata as m; print(m.version('NetEase-MusicBox'))"`。

## 5. 部署后顺序（不要跳步）

以下顺序只有在 §0.1 PASS、已验证流程可控且动作属于本会话既有授权范围后才可执行；不因例行步骤或另选操作时间重复索要授权。任一状态变化或验证失败都回到 BLOCKED，不得继续后续步骤。
启用写入开关、重启使其生效及执行 P7 写入前，§0.2 还必须针对该已安装包和生产路径独立 PASS；不得先打开开关“测试权限”。

```
经 §0.1 PASS 且符合本会话既有授权 → 部署 → G3 即时验证（本节）
     → P3 只读观察（临时置 FNMUSIC_PLAYLIST_MANIFEST_READONLY=true，按证据模板记录，完成后置回 false）
  → P3 通过 + §0.2 独立 PASS + 本会话已有授权覆盖隔离测试写入（不需重复确认）
     → 在已验证生命周期/回滚方案与 §0.2 门禁下，选择影响可控的维护窗口启用
       FNMUSIC_MANUAL_SAVE_ENABLED=true、FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=true + 配置 JOBS_DB → 重启
     → P7 按验收运行手册执行（测试歌单 + 重复位置 + 失败位置）
```

## 6. 回滚

- 回滚/停机/恢复本身也受 §0.1 门禁约束。没有针对当前设备和任务状态验证过的回滚流程时，状态仍为 **BLOCKED**；
  遇到故障不得临时试装旧包、卸载/重装或随意重启。
- **先核实回滚机制**：当前材料没有证明 fnOS 应用中心支持降级，也没有验证任何降级操作。不得假定
  “选择旧 FPK”就能回退；不以卸载/重装代替回滚。先确认设备版本对应的受支持回滚流程、目标包来源/哈希、
  目标版本兼容性、备份可读性和恢复演练方案。无法确认时保持服务/写入开关状态可控并停止回滚动作。
- **能力边界**：包内容对照显示，2.10.2 不含本轮整歌单保存能力；2.8.2 不具备手动保存功能。
  回到这些版本会失去对应功能（2.8.2 连手动单曲保存也没有），不能把“应用恢复运行”描述成“功能回滚完成”。
- **升级脚本备份边界**：`fnmusic_data_items()` 只列入 repo 内 `.env`、`.env.bak*`、`sources-data`、
  `online_favorites`、`online_favorites.json`、`play_history`。jobs DB 未列入；曲库和暂存目录也不在该清单。
  `upgrade_init` 生成的归档不能保护这些路径，且升级完成时临时归档会被删除。包替换可能丢失位于 repo/target
  内但不在白名单的数据；升级后新写入的账本/媒体也不可能存在于升级前归档中。
- **账本回滚**：不要删除、覆盖或用旧快照覆盖当前 jobs DB。先停全部写者，再把当前 DB 用 §1.2 的 SQLite
  backup API 另存为新的时间点副本并验证完整性；保留原 DB 与 sidecar，不确定 schema 兼容性时不要让旧版本
  打开它。若必须恢复，先由用户确认要恢复的时间点和审计数据损失范围，并保留恢复前副本。
- **配置回滚/再升级**：`.env` 恢复必须使用私有备份。旧版本可能忽略新开关，但 `.env` 中的新键可能仍保留；
  再升级前重新只读检查相关键，并明确将写入开关设为 `false`，确认账本路径后仅在全部安全门 PASS 且属于本会话既有授权范围时按已验证流程启用；不重复索要同一常规授权。不要根据
  历史上某次 `.env` 缺少键就推断 DB 不存在或开关当前关闭。
- **媒体与暂存**：已发布媒体**不自动删除**；暂存内容也不自动清空。仅清理本任务明确记录且仍处于当前授权范围内的测试产物，并先按任务 `positions[]`、实际路径和文件清单逐项核对。清理无关文件、权益/来源不明数据或任何超出当前授权的内容，须单独确认；禁止通配/递归删除。路径位于 repo 外不构成安全或持久性证明。

## 7. 不证明

本检查单不证明：构建可复现、镜像可重建、安装成功、G0 真机链路、音频有效性或用户可播放；
这些必须在实际部署窗口内逐项记录。
