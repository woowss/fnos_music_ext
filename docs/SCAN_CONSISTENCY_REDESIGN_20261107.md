# 扫描期一致性替代设计（RO 位不可靠前提下）

> **状态：设计文件，仅设计。** 本文回答"在 `/vol1` 的 Btrfs 只读子卷仍可写的前提下，P7 逐位置文件验收所需的扫描期一致性证据应如何设计"。**本文不实现、不部署、不改变任何门禁、不授权任何设备写操作**；`docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md` §0.1/§0.2 仍 **BLOCKED**，`scripts/verify_position_file_evidence.py` 的 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须继续为 `False`（扫描器继续对所有位置返回 `unknown`），P7 A4 仍 **NOT RUN/BLOCKED**。本文不构成上线、迁移或验收授权。
> **写作时间**：2026-11-07 JST 03:0x（NAS 本地 CST 02:0x）。写作期间对设备仅执行**只读**查询（`ssh` 的 `btrfs subvolume list/show`、`stat`、`find`（有界）、`ls`、`df`、`findmnt`、`ss -ltn`、`systemctl list-units`、`btrfs qgroup show`），未写 `/vol1`、未停启服务、未改配置、未执行写探针。
> **证据绑定**（本机文件 SHA-256，写作时重算）：
>
> | 文件 | 大小 | SHA-256 |
> | --- | --- | --- |
> | `docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`（主证据，已独立复审 PASS-with-limits） | 16,103 B | `ff3c4d0bc51aa9ac35a15064ce953472251e2d0a79acfcc278c5f7793c3206f4` |
> | `docs/BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW_20261107.md`（复审报告） | 44,925 B | `8ad667afc8b0d6e45b0bb1cb78dd6afbfb3332ce69804019b6dcc5072e94cb7d` |
> | `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md`（并行会话记录，仅旁证） | 5,985 B | `61e0391234a63b51813bb11b27c355344d3dd5f43e4b5555c2ed67c47bc93914` |
> | `scripts/verify_position_file_evidence.py`（现有扫描器） | 16,669 B | `197d43a99e244b99021936df12e321b9fcc4c48779ce8a1b029c0de9db049b80` |
> | `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md`（P7 A1–A16） | 16,111 B | `3e0ea759546adf3f64913c09f53d2ec960ac0b7b2690b12555e6e2a71e7fde02` |
> | `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md`（§0.1/§0.2） | 22,996 B | `6e1648037a4dedd9bcefe43ab742b817c42bcde3f9318441eaa5ca19666a2548` |
> | `docs/MANUAL_SAVE_PLAYLIST_JOB.md`（合同：detail 白名单/账本语义） | 118,962 B | `4b5610ce8bc5e458bfbe9917d29a99764b5e96761340ed9fc8aa441abce0b0cf` |
> | `docs/DEVICE_INVENTORY_20261107.md`（设备只读盘点） | 47,515 B | `d46342be8e1b4a7f99998af7afa8f390c8d454293296e3d3fd54a0cea760fed6` |
>
> 仓库 HEAD：`cf843a40d735fb9a20e1bc850f711cd4693cb3aa`（**未提交工作树**；本文不修改仓库其它文件）。
> **引用规则**：本机实测以主证据文档 + 复审报告为准；并行会话文档 `BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` **只作为"同一现象的可核实旁证"引用**，它不是授权、不是独立来源（复审 F1 已认定两份文档血缘重叠、归属表述冲突），其"可核实内容"仅限与主证据一致的部分。所有"本机"结论**不得外推**到其它 fnOS 版本、内核、挂载选项或上游 Linux/Btrfs。

## 0. 结论摘要

### 0.1 核心事实（决定设计走向）

1. **"只读子卷=扫描期不可变"在本机不成立。** `ro=true` 且 `GET_SUBVOL_INFO` flags bit0=1 的真实 Btrfs 只读快照，在本机 `/vol1`（`trimacl`）上仍被 root 与 uid1000/CapEff=0 成功写入（主证据 §5.1；复审 §5 断言 1）；并行会话记录另有"删除只读快照内既有文件成功"的观测，但那只作同现象旁证（F1，未纳入主证据复审）。写入后 RO 位未被清除。因此 `_require_readonly_snapshot` 只能证明"元数据标记为只读快照"，不能证明"扫描期间不可写"。
2. **generation 是候选信号，不是完整证明。** 本机只观测到"真实写入 + `sync` 推进 generation；仅 `btrfs property set ro true/false` 不推进"（主证据 §5.2；复审 §5 断言 3）。**未证明**：不 `sync` 的写入何时推进、仅元数据（chmod/xattr/utimes/rename）是否推进、`trimacl`/`trimafs2` 是否改变语义、所有写入路径是否必然推进。generation 可以用于**变化检测**，但不得作为唯一一致性证明。
3. **现有扫描器必须继续 unknown。** `scripts/verify_position_file_evidence.py:38` 的 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False` 必须保持（主证据 §5.3-1；复审 §8 未解除项 4）；该常量解除需要一个新的、单独审查的代码变更 + 本机原生证据，不能用合成测试或"RO 位=1"绕过。
4. **账本指纹是"意图"而非"落盘观测"。** `published_sha256`/`published_size` 是对**送入 staging store 的 tagged 字节流**计算的指纹（`proxy/manual_save_route.py:768-801`；合同 `MANUAL_SAVE_PLAYLIST_JOB.md:333` 明确"不能证明对发布路径重新读取成功"）。它必须与一个**独立读取实际文件**的观测（扫描器）配对，才能支撑 P7 A4"逐位置核对结果与实际文件"。
5. **本机拓扑约束（写作时只读观测）**：`/vol1/1000` 是子卷（ID 256，top level 5）；验收曲库 `/vol1/1000/fnmusic-test-20261031` 是其内**普通目录**（非子卷）；该用户子卷内**没有嵌套子卷**（`btrfs subvolume list -o /vol1/1000` 0 项）；但该子卷有 **>100,001 个文件**，超过现有扫描器 `MAX_ENTRIES=100,000`——**整用户子卷快照不能直接作为扫描根**。fnOS 自身已存在 `@snapshot/1000/<日期>` 周期只读快照（2 个日期，`ro=true`，路径 mode `0 root:root`），说明平台快照能力存在，但其只读性同样不可依赖（与事实 1 同机制、未在本设计内重测）。

### 0.2 推荐方案（一句话）

> **"带时间点视图（Btrfs 快照或受控副本）+ 视图内 generation/身份 bracket + 对现存文件的逐位置独立重读 + 账本指纹 oracle + fail-closed 证据封存"** 的分层组合；**停写/无写者证明是"更强证据档（Grade B）"的可选强化，不是字节一致性的必要条件**。扫描目标优先选"曲库独立子卷的快照"；如不接受数据布局变更，则用"用户子卷快照 + 只扫曲库子树"的最小变更形态；两者都不可行时退到"库范围受控副本（优先 `FICLONE`，明确失败关闭）"。**任何形态上线前都必须先完成本机原生代际语义验证（§4）**。

### 0.3 需要用户决定的事项（详见 §5.2）

- **D1 停写窗口**：是否允许、范围与时长（仅手动保存写入 / 整个验收曲库子卷 / 整个用户家目录）；是否接受"只做检测、不做停写"的 Grade A 证据。
- **D2 数据布局**：是否允许把验收曲库改为**独立 Btrfs 子卷**（最干净，但需迁移/配置变更，且会改变 fnOS 周期快照对该目录的覆盖）；还是接受"用户子卷快照 + 子树扫描"（不改布局，但快照/扫描器需支持子树模式）；还是接受"受控副本"（需 `FICLONE` 原生验证 + 磁盘/时间开销）。
- **D3 扫描器身份**：是否创建**专用非特权只读用户**（+ 曲库 ACL + 快照路径访问）；这需要一次受控部署变更。
- **D4 失败注入授权**：是否允许在**隔离测试数据**（NAS `/tmp` 的 btrfs 镜像/一次性受权测试树）上做原生代际/写入注入；若不允许，扫描器无法取得原生证据，只能保持 `unknown`。
- **D5 重试策略**：外部竞态导致的变化检测，允许**自动重试一次**后失败关闭，还是任何变化都直接 BLOCKED 人工处理。
- **D6 重复位置裁决**：`ambiguous`（同字节多处）是否允许凭"重复 `source_id` + 独立 basename 证据"放行；谁提供 basename 映射证据（账本 detail 不含 basename）。
- **D7 验收衔接**：扫描器输出何时可以进入 P7 A4 判据（本文建议：先只做"支撑证据"，A4 结论仍需人工/独立复审；升级为判据需改 A4 手册与合同，另立任务）。

## 1. 问题陈述与威胁模型

### 1.1 验收目标与现有扫描器契约

P7 A4 要求"逐位置核对结果与实际文件"（`MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md:70-83`）：对账本中每个 `state=published` 的位置，独立确认其 `published_sha256/published_size` 与实际落盘文件一致，并能与隔离测试库中的实际文件名/位置独立对应；A5 另要求重复位置可解释。账本 detail 的 `published_sha256/published_size` 只是送入 staging store 的字节指纹，不是落盘回读（合同 `:333`）。

现有扫描器 `scripts/verify_position_file_evidence.py` 的契约（写作时源码状态）：

- 输入：`{"positions":[{position,published_sha256,published_size}]}`；上限 10,000 位置、512 MiB/位置。
- 在 Linux x86_64 上要求**非特权进程**（euid≠0 且 `CapEff/CapPrm` 均为 0，`:136-163`）；扫描根必须是**同一 Btrfs 只读子卷**（`_require_readonly_snapshot`：treeid>0、uuid/parent_uuid 非零、root item flags bit0=1，`:189-195`）；每次目录/文件 fd 复核同一身份 tuple（treeid、generation、flags、uuid、parent_uuid，`:198-204`），并复核文件 stat（dev/ino/mode/size/mtime_ns/ctime_ns）在读取前后不变（`:207-241`）。
- 遍历：`openat2` + `RESOLVE_BENEATH|NO_SYMLINKS|NO_MAGICLINKS|NO_XDEV`（`:69-87`），拒绝 symlink/子卷/跨挂载/特殊文件；上限 100,000 entries；只哈希 size 命中输入位置的文件。
- 输出：只输出 `position/status/match_count`，不输出文件名/路径/异常文本；`status ∈ {unknown, no_match, unique_match, ambiguous}`；任何不满足 → 整批 `complete:false` + 每位置 `unknown`，CLI 退出码 2。
- **硬门**：`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False` 时在打开扫描根之前就返回全部 `unknown`（`:269-273`）。

该契约的**结构性缺口**（本设计的出发点）：

1. **不可变前提已死**：RO 位/快照身份不等于不可写（事实 1）。`_require_same_snapshot` 的 generation 对比是**候选检测器**，但其在所有写入路径上是否必然推进 generation **未验证**，因此不能作为完整证明，硬门必须保持 `False`。
2. **"非特权"只排除了 root/caps，不排除"同 UID 可写"**：测试库文件属主是 uid1000（`600/750 kurumi:Users`，写作时只读观测），若扫描器就以 uid1000 运行，则**扫描器所在 uid 本身对树有写权限**；同 uid 的其它进程也可在扫描期改文件。当前门没有"扫描器对树不可写"的检查。
3. **扫描根粒度**：现有门槛要求根**本身**是只读子卷；而验收曲库是用户子卷内的普通目录，且用户子卷 >100k 文件/超出条目上限（事实 5）。不改变数据布局就无法"以曲库为根"直接扫用户子卷快照。
4. **账本指纹不是独立 witness**：账本与扫描器必须一个记录"意图"、一个观测"落盘"，两者不能同源。
5. **扫描结束没有"封存"**：即便扫描期间一切稳定，扫描结束后、证据被引用前文件仍可变化；需要定义证据窗口的终止边界并在边界处重绑现存文件。

### 1.2 本机证据（只引用可核实内容；明确不可外推）

主证据：`docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`（规范运行 2026-11-07 01:34:41–44 CST，已独立复审 **PASS-with-limits**，无阻断发现）。复审报告：`docs/BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW_20261107.md`。

| # | 事实 | 观测值/描述 | 来源与边界 |
| --- | --- | --- | --- |
| E1 | 环境 | fnOS `1.2.0903`；内核 `6.18.18.c1168-trim`；`/vol1` = `/dev/mapper/trim_8f1e972a_...-0`，`btrfs`，`rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/`；btrfs-progs v6.2；Python 3.11.2 | 主证据 §1；复审 §2 第 2 项（独立复跑同值） |
| E2 | 只读快照元数据 | treeid 682、generation 1691566（读取时）、flags `0x1`（RO bit0）、uuid/parent_uuid 非零、`btrfs property get -ts … ro` = `ro=true`；ioctl 号 `0x81f8943c`；与 `btrfs subvolume show` 的 treeid/uuid/parent_uuid/generation 逐项一致 | 主证据 §4.1；复审 §2 第 12/13 项 |
| E3 | **RO 子卷仍可写** | 在 `ro=true`、flags bit0=1 的快照内：root 创建新文件 **ACCEPTED**；kurumi（uid1000、CapEff=0）创建新文件 **ACCEPTED**；重锁 RO 后再创建 root **ACCEPTED**；写入后 `ro=true`、flags 仍 `0x1`；快照根/文件在快照前设为 `0777/0666`，排除权限位解释 | 主证据 §5.1；复审 §2 第 16/17 项、§5 断言 1。并行文档同现象，但仅旁证（F1，非独立来源） |
| E4 | 写入推进 generation，切标志不推进 | root 写 + `sync`：1691566→1691567；kurumi 写 + `sync`：1691569→1691572；RO→RW→写→RO：1691566→1691569；RO→RW→RO 不写：1691569→1691569（不变） | 主证据 §5.2；复审 §2 第 18 项、§5 断言 3。**边界：仅这几种操作；不证明不 `sync` 的写入、元数据写入或所有写入路径** |
| E5 | 扫描器门函数真实行为 | 真实 RO 快照 accepted；RW 子卷/顶层普通目录 rejected `[Errno 30] not a read-only Btrfs snapshot`；root rejected `[Errno 1] privileged scanner process`；kurumi（CapEff=0）accepted；非特权进程可直接 ioctl 读快照 info | 主证据 §4.2；复审 §2 第 14/15 项 |
| E6 | 硬门状态 | `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`；`scan_tree` 在该常量为 `False` 时直接返回 `complete:false` + 全部 `unknown`；CLI 退出码 2 | `scripts/verify_position_file_evidence.py:38,269-273`；复审 §2 第 19 项 |
| E7 | 账本 detail | `detail` 严格白名单含 `published_sha256/published_size`，旧行可缺；`published_sha256/size` 在 no-replace 发布成功后提供，来自送入 staging store 的 tagged 字节流，**不是落盘回读**；不含 basename/路径/MD5（detail 层） | 合同 `MANUAL_SAVE_PLAYLIST_JOB.md:324-333,690-694`；`proxy/manual_save_route.py:768-801` |
| E8 | 匿名暂存发布链路（本机真实） | root 与 kurumi 的 `O_TMPFILE` 请求 `0600` 实测 `0700`、`fchmod` 后 `0600`；seal/readback SHA 与 payload 一致；`linkat(AT_EMPTY_PATH)` 发布成功、目标 `0600`、`nlink=1`、SHA 一致；同名冲突不覆盖 | 主证据 §3；复审 §2 第 6–11 项。**边界：候选未部署/未集成；失败/并发路径仍无真实内核证据（复审 §8 未解除项 2）** |

写作时新增的**只读**设备观测（未独立复审；仅本设计参考，作为"当前拓扑"的输入，不当作永久事实）：

| # | 观测 | 值/结论（2026-11-07 JST 03:0x，NAS CST 02:0x） |
| --- | --- | --- |
| E9 | 子卷拓扑 | `btrfs subvolume list /vol1` 共 49 项；`/vol1/1000` = 子卷 ID 256（top level 5）；`btrfs subvolume show /vol1/1000/fnmusic-test-20261031` → `ERROR: Not a Btrfs subvolume`；`btrfs subvolume list -o /vol1/1000` → **0 项**（用户子卷无嵌套子卷） |
| E10 | fnOS 平台快照 | `@snapshot`（ID 264）下有 `@snapshot/1000/<日期>` 只读快照（`UTC+08-2026.10.26-00.00.00`、`UTC+08-2026.11.02-00.00.00`）；最新者 `Flags: readonly`、`ro=true`、`Gen at creation: 1677320`；`/vol1/@snapshot` 与 `/vol1/@snapshot/1000` 路径 `stat -c %a %U:%G` = `0 root:root` |
| E11 | 条目/容量 | 对 `/vol1/1000` 的有界 `find` 在 100,001 处截断（**>100,001 个文件/条目**）；`/vol1` 容量 3.7T、已用 2.1T、可用 1.6T；`/`（ext4）63G、可用 39G（`/tmp` 位于 `/`，**不是** tmpfs）；`/vol1/1000` qgroup 显示 `WARNING: qgroup data inconsistent, rescan recommended`（只读显示；含义/风险 unknown） |
| E12 | 曲库/暂存现状 | 曲库目录 4 个文件：3 个音频（126,459,367 / 126,459,476 / 126,906,165 B）+ 1 个 944 B 文件；mode 依次 `600/600/750/750`，属主均 `kurumi:Users`；暂存目录 `700 root:root`、**0 条目** |
| E13 | 网络文件服务 | `ss -ltn` 无 445/139/2049 监听；`smbd.service` inactive/dead、`nfs-server.service` inactive/dead；但 `nmbd`/`winbind` 在运行（服务可被管理员随时启用，威胁模型仍须包含 SMB/NFS 写入） |

**证据边界（不得外推）**：E1–E8 只绑定本机该版本/内核/`trimacl` 挂载；不证明上游 Btrfs 一般行为、其它 fnOS 版本、其它挂载选项；不证明生产服务/曲库/账本当前状态未被影响之外的任何生产链路（主证据 §6、复审 §8）；匿名暂存的失败/权限/并发路径仍无真实内核证据；`PIPELINE_INTEGRATION_ALLOWED=False` 未变。E9–E13 是写作时单次只读观测，**未经独立复审、未重复采样**，实现前必须重新核实。

### 1.3 前提废止与"不安全推理"清单

以下推理在本机证据下**不得再使用**（实现/复审时按红线对待）：

- ❌ "RO 位=1 / `ro=true` ⇒ 扫描期不可变"（E3 反例）。
- ❌ "快照是 RO 子卷 ⇒ 扫描器读到的内容不会变"（E3：RO 子卷仍可写；且写快照路径的进程可能同 uid）。
- ❌ "进程非 root ⇒ 不会改树"（E5 门只排除 root/caps；测试库属主 uid1000；同 uid 进程可写）。
- ❌ "POSIX mode/`access()` 能证明不可写"（本机 `/vol1/1000` mode `d---------` 却可被其属主遍历/写入；`trimacl` 使 mode/ACL 不是有效写权限的完整证据，E3 探针快照根 `0777` 是主动排除权限解释；`access()` 结果不能单独作为证据）。
- ❌ "generation 变了=一定有写入 / 没变=一定没写入"（E4 只证明了部分正向路径；未验证不 `sync` 的提交滞后、元数据写入、`trimacl` 补丁语义）。
- ❌ "账本 SHA-256 = 落盘字节已核验"（E7：它是送入 staging store 的 tagged 字节流指纹）。
- ❌ "整用户子卷快照可以直接给扫描器扫"（E11：>100k 文件，超 `MAX_ENTRIES` ⇒ 必然 `unknown`；且 generation 是全子卷粒度，家目录任何无关写入都会让 bracket 误报）。
- ❌ 把并行会话文档当作授权或独立证据来源（F1）。

### 1.4 威胁模型

**资产**：

- A1 已发布音频文件的字节（P7 A4 比对对象）。
- A2 文件名 ↔ 位置/清单的对应关系（A4 要求 basename 可独立对应；A5 重复位置）。
- A3 账本位置记录（`published_sha256/size/state`）。
- A4 验收证据窗口本身（扫描结果/证据文件的完整性、可复现性）。

**写者/威胁主体**（"能不能改库"针对验收曲库；"本文能否检测"指在推荐方案的分层检测下）：

| 编号 | 主体 | 能力/路径 | 是否必须防 | 处理策略 |
| --- | --- | --- | --- | --- |
| W0 | 扩展自身 worker/网关（目标版本） | 以发布流程 `linkat` 新增文件；**不改已发布文件**（`publish_noreplace` 不覆盖）；清理 staging | 新发布不应污染"已发布位置"证据；与扫描并发 | **可检测**（快照时间点 + live 重读；已发布文件名已存在 → 不会再写同名文件）；作业终态/无在途时更强（Grade B） |
| W1 | 本机普通用户（含 uid1000，扫描器可能同 uid） | 拥有曲库文件、可写；可 `rename/truncate/改写/删除`；可写快照路径（若可达） | 是（A4 的核心威胁） | 检测：generation/stat bracket + live 重读；Grade B：无写者证明 |
| W2 | SMB/NFS 客户端（经 fnOS 文件服务） | 远程读写共享；本机写作时无监听（E13），但服务可启用 | 是（配置启用后） | 检测同 W1；Grade B：维护窗口内禁用/确认无会话（用户决定） |
| W3 | fnOS 平台（媒体索引/缩略图/周期任务/升级） | 可能写 xattr/缩略图/媒体库元数据；**本设计未定位其具体写入路径** | 是（至少检测） | 检测：generation/stat bracket（可能造成误报→ unknown/重试）；Grade B：观察并证明窗口内无写入；若发现写库行为，需单独评估 |
| W4 | root/管理员/密钥持有者（`trimacl` RO 绕过即此类能力） | 可任意写、可改快照/删快照/改扫描器/改账本/改系统时间 | **不承诺检测/防御**（信任边界之外） | 明确排除；仅要求"操作员无写操作"的显式声明 + 独立复审 + 证据哈希绑定；不得声称能抵御被攻陷内核/管理员（与扫描器既有边界一致） |
| W5 | 维护脚本/未来的实现者脚本（`repair_unknown_library.py`、升级脚本、探针、本设计的 root helper） | 可写曲库/快照/临时目录；可能在窗口内执行 | 是（尤其自己引入的写入） | 窗口内禁止其它维护写操作；root helper 的写入只允许发生在**一次性快照/副本/验证目录**，并记录精确路径与清理证据 |
| W6 | 内核/存储故障（崩溃、掉电、坏块、缓存丢失） | 可产生字节损坏、generation 不一致、丢失写 | 部分（数据完整性） | 检测可发现部分（哈希/stat/generation），不能预防；reflink 副本与快照共享物理 extent，**不能**作为坏块隔离证据；全量副本/独立主机才提供物理独立 |
| W7 | 扫描器环境自身（同 uid 其它进程、扫描器被注入/误运行） | 若扫描器 uid 对树可写，则"扫描器视图"可被同 uid 进程篡改；扫描器进程本身不写树 | 是 | 专用只读 uid（D3）+ 可选"扫描器不可写"负向检查；证据记录 uid/caps/脚本哈希；根信任边界 |
| W8 | 时间源/时钟 | 改系统时间可影响 mtime 记录与证据时间戳 | 低（不影响内容哈希） | 证据记录 UTC 时间与来源；不把 mtime 当唯一证据 |

**信任边界声明**（必须写进任何验收结论）：本设计最多能证明"在给定证据窗口内、在给定进程/权限模型下、给定文件系统语义观测成立时，实际文件字节与账本意图一致"；**不能**抵御 W4（root/管理员/被攻陷内核）、不能证明音频内容"正确/可播放/标签正确"（A4 仍需独立证据）、不能证明索引层面的可播放性。

### 1.5 检测目标（各方案要映射到的 D 项）

- **D1** 已发布文件字节在窗口内被改写（同 size / 异 size）。
- **D2** 同名替换（新 inode 顶替旧文件）。
- **D3** truncate/append/稀疏改写。
- **D4** "时间点视图"（快照/副本）在扫描期被写入（RO 位不可靠 + 路径可达）。
- **D5** 目录项增删/rename（含制造同字节重复、删除匹配文件）。
- **D6** 现存库内容与账本意图不一致（内容缺失/不同/截断）。
- **D7** 重复位置（合法同字节多处）造成的 `ambiguous` 裁决。
- **D8** 扫描器环境不可信/平台不支持（uid 可写、非 Btrfs、跨挂载、超限、IOCTL 失败等）→ 必须整批 unknown。

## 2. 候选方案逐项分析

**评价口径**：每方案给出"机制 / 能检测 / 不能检测 / 失败模式 / trimacl 可行性 / 需要权限 / 生产影响 / 实施复杂度 / 证据可验收性"。复杂度：S（小）< M（中）< L（大）< XL（很大）。"可验收性"指**单独**能否作为 P7 A4 文件字节证据（配合其它层时可能提升）。

### 2.1 方案 A：停写（应用层 quiesce）+ 一次性快照 + 扫描前/后 generation 对比 + 逐文件独立重读

- **机制**：①应用层/操作员抑制写者（作业终态、无在途 advance、无保存线程、staging 空；可选关闭开关/维护锁；SMB/NFS 用户写入由维护窗口排除）；②root 对目标子卷做一次性 `btrfs subvolume snapshot -r`，记录快照身份 tuple 与 `Gen at creation`；③非特权扫描器扫描快照，扫描开始/结束/每个 fd 复核快照 tuple（现有 `_require_same_snapshot`）；④扫描完成后对**现存曲库**逐个匹配位置"独立重读"（二次 open + 哈希 + stat 身份对比），把快照时间点绑定到当前实际文件；⑤任何 bracket/身份/stat 变化 → 整批 unknown，最多重试一次。
- **能检测**：D1/D2/D3（live 重读 + stat）；D4（快照 tuple/generation 变化；若 generation 语义在该写入路径上可靠）；D5（目录 stat + generation + 匹配数量变化）；D6（账本指纹 vs 快照哈希）；D7（给出 `ambiguous` 及 match_count，由上层裁决）；D8（identities/limits 任一不满足）。
- **不能检测**：未提交写入的 generation 滞后窗口（本机未验证）；不推进 generation 的元数据/写路径（未验证）；W4（root/管理员/内核）；快照**创建之前**已存在的篡改（只能靠 D6 内容比对发现"不一致"，不能发现"与账本一致但内容本身是错的历史"）；账本与文件"同源一致地错"（见方案 D）；basename/位置对应（扫描器不输出名字）；LRC/标签/封面/索引/可播放；证据窗口终止后的变化（§3.7）。
- **失败模式**：快照源选错（整家子卷 vs 曲库子卷）→ 条目超限或无关写入误报；扫描器无法读快照路径；快照路径可被写（同 uid/root）→ 若 generation 语义不可靠则漏检；quiesce 无法证明；快照删除失败/残留子卷；重试策略掩盖真实变化；`trimacl` 与上游语义不同 → 全 unknown（fail-closed，可接受）。
- **trimacl 可行性**：快照机制在本机已真实工作（主证据 §4.1 建过真实 RO 快照并由扫描器门接受）；曲库当前非子卷（E9）→ 需 T-A/T-B 拓扑（§3.2）；RO 位不可依赖（E3）→ 必须叠加 bracket/live 重读；generation 推进仅在"写入+sync"验证（E4）→ 需 §4 原生验证。
- **权限**：root 建/删快照 + 读快照路径（或 ACL 授权扫描用户）；扫描器必须非特权（现有门）、不得有 caps；扫描用户对树**不可写**（新增建议门，D3）。
- **生产影响**：快照创建/删除的元数据 COW 开销（创建轻；删除开销取决于子卷大小，整家子卷可能较大，E11）；扫描只读 I/O；Grade B 的停写窗口为分钟级写停顿（仅手动保存功能，不是整机停服）；不重启服务即可实现（作业终态即可，见 §3.5）。
- **实施复杂度**：**L**（root helper + 扫描器新模式 + 证据 schema + 清理/残留核验 + 权限模型）。
- **证据可验收性**：**高**（完成 §4 原生验证 + §5 判据后），但仅覆盖"字节一致性"，A4 的 basename/可播放等仍须其它证据。**单靠 RO 位不可验收——这正是本方案必须叠加 bracket/重读的原因。**

### 2.2 方案 B：扫描副本（`cp --reflink=always` / 普通复制到隔离目录后只扫副本）

- **机制**：由受信 root helper 把曲库目录树复制到**专用验证子卷/目录**（同一 `/vol1` 文件系统）；优先 `cp --reflink=always`（`FICLONE`），失败时按显式策略 fail-closed 或降级为全量复制并记录；扫描器只扫副本；扫描完成后对现存曲库逐匹配位置独立重读，绑定"副本时间点"↔"当前实际文件"。
- **COW 语义（重点回答"reflink 后修改会不会影响"）**：`FICLONE` 在 clone 时刻共享 extent（引用计数 +1）；**之后对源或副本任一方的写入都触发受影响 extent 的 copy-on-write，因此不会改变另一方的已克隆内容**（克隆保持 clone 时刻的数据视图）。但必须同时看到 5 个限定：
  1. **不是整树原子点**：`cp --reflink` 是逐文件 clone，文件 A 与文件 B 的 clone 时刻不同；跨文件一致性只能靠"对现存文件 live 重读"和每文件 stat 对比兜底，不能声称"整树 T 时刻"。
  2. **dirty page/delalloc 语义未验证**：对本机 `trimacl`/6.18 上"刚写入尚未回写的源文件"做 FICLONE 时，clone 是否强制回写或复制页缓存内容，**必须先做原生验证**；未验证前必须"clone 前/后各自独立哈希并对比源再读"，否则可能拿到旧视图。
  3. **元数据不共享**：mode/uid/xattr/mtime 由 `cp` 另行设置，存在元数据窗口；ACL/`trim ACL` 是否完整复制未验证。
  4. **共享物理 extent ⇒ 不提供物理独立性**：reflink 副本与源共享盘上 extent，坏块/raw 设备写/底层损坏会同时影响两者；因此 reflink 副本**不能**当作"独立存储证据"。全量副本才写新 extent（时间/空间换物理独立）。
  5. **副本自身可写**：副本目录若同 uid 可写，则 D4 依然存在；需要专用只读 uid / 专用验证子卷 bracket。
- **能检测**：D1/D2/D3（live 重读）；D4（验证子卷 generation bracket + 副本统计）；D5（复制清单/统计 + live 重读）；D6（副本哈希 vs 账本）；D7（副本内 match_count）；D8（FICLONE 不支持/超限/副本身份不符 → unknown）。
- **不能检测**：跨文件原子点（非整树）；源在 clone 前已变更但副本与账本恰好一致（内容层面仍算一致，可接受）；W4；basename/播放等其它 A4 维度；共享 extent 的物理损坏。
- **失败模式**：`EOPNOTSUPP/EXDEV`（FICLONE 不可用）；`ENOSPC`（全量副本）；复制中断产生不完整副本（必须整批 unknown 并删除）；symlink/hardlink/sparse/特殊文件；副本目录权限/可见性错误；静默降级为全量复制（必须显式记录模式）；副本删除失败/残留。
- **trimacl 可行性**：`/tmp` 在 `/` 上（ext4）**不能** reflink（E11 实测挂载）；目标必须在 `/vol1`（可用 1.6T）；FICLONE 在 `trimacl` 下未验证（原生测试项 I11）；曲库当前 363MB，全量复制成本可接受（但未来库可能更大）。
- **权限**：root 或专用 uid 执行复制；扫描器非特权只读；副本路径必须不可被普通/同 uid 写（需要专用验证子卷 + 权限模型）。
- **生产影响**：reflink 模式≈元数据/共享 extent（轻），全量模式≈库大小 I/O + 空间；复制窗口内源可能被写（需 bracket/重读）；副本清理。
- **实施复杂度**：**M–L**。
- **证据可验收性**：**中–高**（需 FICLONE 原生验证、明确 fallback、扫描器"副本模式"新门、live 重读；共享 extent 的物理独立性限制必须在证据中声明）。

### 2.3 方案 C：`btrfs send` / `receive` 到新子卷或外部文件后的独立树

- **机制**：①对 RO 快照（`btrfs send` 要求源子卷只读）执行 `btrfs send`，管道给 `btrfs receive` 到新子卷（同机或外部），或 `btrfs send -f` 生成流文件；②接收树为**新分配 extent**（物理独立于源）；③对接收树扫描 + 账本比对；④live 重读现存源文件绑定。
- **能检测**：D1/D2/D3/D4（接收树自身 generation/stat bracket + 快照 tuple bracket）；D5（接收树目录/统计）；D6（账本 oracle）；D7（接收树内 match_count）；D8（send/receive 失败 → unknown）。
- **不能检测**：快照创建前的篡改；快照自身在 send 期间被写（RO 位不可靠；send 可能报错或产生不一致流，需 bracket + 重传）；W4；basename/播放等。
- **失败模式**：send 需要 RO 源（RO 位可写不等于 send 可安全）；快照被并发写 → send 失败/流不一致；`receive` 目标必须不存在/为新子卷；`ENOSPC`；中断重传；btrfs-progs 版本/`send_stream_version` 兼容；`trimacl` 下 send/receive 未验证；全量数据成本（≈库大小）；外部文件形态不能直接作为扫描根（要 receive 回来或离线解析，成本更高）。
- **trimacl 可行性**：btrfs-progs v6.2 存在；`/sys/fs/btrfs/features` 列出 `send_stream_version`（写作时只读观测）；但 send/receive 在 `trimacl` 本机**未验证**；且必须先有子卷（曲库独立子卷 T-A，或对整家子卷 send——1.73TiB/>100k 文件，成本不可接受）。
- **权限**：root（send/receive、建子卷）。
- **生产影响**：全量数据复制（363MB 现在可接受，长期随库增长）；I/O 与空间；对生产服务的间接影响。
- **实施复杂度**：**M–L**。
- **证据可验收性**：**中**（独立树/物理独立强，但成本高、必须叠加快照与 live 重读；对单次 1,000 位置验收属于过度设计，更适合归档/离线复验）。

### 2.4 方案 D：发布账本指纹 vs 现库指纹比对（无需快照）

- **机制**：从管理员查询/授权接口取得每个位置的 `published_sha256/published_size`；扫描现库（或视图）计算匹配；输出 unique/ambiguous/no_match。
- **能检测**：D6 主要内容（缺失/截断/不同字节）；D7（重复同字节 → ambiguous）；配合 stat/identity 层可发现部分 D1/D2/D3。
- **不能检测（盲区，必须写清）**：
  - **"都一致性错误"**：账本指纹是对同一进程的 tagged 字节流计算的，**不是对落盘的独立观测**（E7）。如果"错误"发生在账本与文件共同的上游（例如 tagged 源字节在哈希前已被破坏、或发布路径把同一错误内容既算指纹又写盘），比对会"一致通过"；本方案无法提供独立于该链路的 witness。
  - **位置/名字对应**：只按 `(size,sha256)` 匹配，无法证明某文件是"该位置的文件"（同名替换但字节相同/不同名字的重复文件都会影响结论）。P7 A4 的 basename 对应必须另行取证。
  - **元数据/附属内容**：LRC、封面、标签、xattr、索引状态不在扫描器范围。
  - **时间稳定性**：无快照/无 bracket 时，扫描可能读到瞬时状态（可能"碰巧匹配"）；必须由方案 A/B/E 的稳定性层兜底。
  - 账本被 root/管理员篡改（W4）。
- **失败模式**：旧行无 detail 指纹（`published_sha256=""`/size=0）→ 无法验证，必须 unknown；账本/查询接口不可用；扫描器 gate 关闭 → unknown；重复/歧义无法裁决。
- **trimacl 可行性**：只需读文件 + 账本查询；今天即可只读运行（但扫描器硬门 `False` → unknown；解除需新证据）。
- **权限**：无特权（读取曲库/视图）；账本数据通过已授权管理员查询取得（扫描 stdin 只含 position/hash/size）。
- **生产影响**：可忽略（只读，只哈希 size 命中的文件）。
- **实施复杂度**：**S**（组件已存在）。
- **证据可验收性**：**必需组件，但单独不可验收**（会"一致通过"盲区 + 无位置对应 + 无时间稳定性）。

### 2.5 方案 E：双遍哈希 + 时间戳/inode/size 稳定性（最弱）

- **机制**：对每个文件独立 open 两次（或整树两遍），比较 `(hash, st_dev, st_ino, st_mode, st_size, st_mtime_ns, st_ctime_ns)`；对目录也比较 stat；两次结果必须一致；可选在两次之间加延迟。
- **能检测**：读取期间的变化（per-file stat before/after）；两次观测之间的变化（第二遍对比）；同名替换（ino 变化）；truncate（size）；目录项 churn（目录 stat）。非特权用户无法直接设置 ctime，通常能发现"改后又恢复 mtime"的做法（ctime 会变）。
- **不能检测（盲区）**：
  - **不产生时间点视图**：两次观测是"两个瞬间"，不能证明两次之间没有发生过变化（只要变化发生在两次观测窗口之外，或在两次观测中恰好一致）；长扫描窗口越大盲区越大。
  - **终止后无保护**：最后一次观测之后、证据被使用之前的变化完全不可检测（除非再叠加 live 重读/封存）。
  - **跨文件逻辑一致性**：每个文件在不同时刻采样；无法证明"存在一个整树一致的瞬间"。
  - **inode 复用**：删除后重建可能复用 ino（哈希/ctime 通常仍能区分，但不能作为强证据）。
  - **同 uid 可写**：扫描器自身 uid 可写时，双遍仍是被同一权限域操纵。
  - W4：root/管理员/内核可绕过（改 ctime/时间/文件系统）。
  - 性能：两遍全树哈希 I/O 翻倍（只对候选文件做第二遍可缓解）。
- **失败模式**：把"两次观测一致"误当成"窗口内不变"；长扫描期间被改而恰好未被两次采样覆盖；成本失控。
- **trimacl 可行性**：无特殊要求；但 `trimacl` 下 stat 字段语义/可见性未验证（本机只读过普通 stat，未见异常）。
- **权限**：只读即可。
- **生产影响**：扫描 I/O 加倍（若全树两遍）。
- **实施复杂度**：**S**。
- **证据可验收性**：**低**（不能单独上验收；只能作为方案 A/B 的辅助层，且不能替代快照/时间点视图）。

### 2.6 方案 F1：fnOS 自带快照/受管快照（`@snapshot`）

- **机制**：使用平台已有的 `@snapshot/<uid>/<日期>` 只读快照（fnOS 周期任务生成，E10），或经平台管理接口触发受管快照；扫描该快照内的曲库子树。
- **能检测**：D1/D2/D3/D5/D6（若能把扫描根限定到子树且快照不变）；提供平台层面的时间点视图。
- **不能检测**：RO 位不可靠（E3；平台快照同为 Btrfs RO 子卷）；快照时间点由平台调度决定，**不可控、不能覆盖验收窗口**；粒度=用户子卷（>100k 文件，E11）；平台快照保留/轮转可能在我们读取期间删除；平台管理接口/权限/是否存在对用户可用的"手动触发"**未核实**；W4。
- **失败模式**：快照被平台轮转删除；父路径 mode `0 root:root`（E10）导致非特权扫描器无法访问（需要 ACL/专用路径）；无法把"证据窗口"与"平台快照时刻"绑定；把平台快照误当授权。
- **trimacl 可行性**：平台快照存在（E10）；只读访问需要 root/ACL；不能执行写；不依赖平台 API 时可作为"只读参考视图"，但**不能作为验收窗口控制**。
- **权限**：root/平台管理；扫描器读访问。
- **生产影响**：低（平台本来就在做）。
- **实施复杂度**：**M**（若平台接口可用；未验证则更高）。
- **证据可验收性**：**低–中**（作为辅助视图可以；不能控制时间点/不能解决条目与 RO 问题；需先取得平台机制证据并经用户授权）。

### 2.7 方案 F2：`fsfreeze`/`thaw` + 快照（整文件系统冻结）

- **机制**：`fsfreeze --freeze /vol1` 冻结整个 Btrfs 文件系统（所有子卷/应用写入阻塞），在冻结窗口内创建快照/副本，然后 thaw；可给出强一致时间点。
- **能检测/保证**：时间点视图在冻结-快照瞬间强一致（vFS 层阻塞写）；配合 bracket 可强化。
- **不能检测/代价**：冻结期间**所有**依赖 `/vol1` 的进程/容器/服务 D 状态阻塞，可能触发看门狗/超时/用户可见卡顿；不能用于长扫描；需要 root/CAP_SYS_ADMIN；对 fnOS 平台与容器是重大生产影响；`trimacl`/`trimafs2` 下 fifreeze 行为未验证；W4。
- **失败模式**：冻结后某进程持写锁/卡死 → 系统部分挂起；thaw 失败；影响其它应用；误用于生产窗口。
- **trimacl 可行性**：未验证（需隔离环境先测）；本机 `/vol1` 承载 appcenter/容器/用户数据，风险最高。
- **权限**：root。
- **生产影响**：**很高**。
- **实施复杂度**：**M**。
- **证据可验收性**：**不建议**作为本需求主路径（可作为"快照瞬间"的可选强化，但必须有维护窗口 + 用户明确批准 + 隔离预演）。

### 2.8 方案 F3：fs-verity / 签名清单（长期强方案）

- **机制**：publisher 在发布前对文件启用 `fs-verity`（Merkle 树），把 verity root digest 记入账本；验收时读取 `FS_IOC_MEASURE_VERITY` 与账本比对。内容验证独立于 mtime/inode/ACL，可跨扫描、可耐"改后恢复时间戳"。
- **能检测**：D1/D2/D3 的字节层（文件级强完整性），不受 stat 伪造影响（除非内核被攻陷）；可在任何时刻对现存文件复核。
- **不能检测**：仍需证明"该文件是该位置的文件"（名字映射）；历史/一次性发布前的错误；verity 启用后的元数据/附属文件；W4；**当前未实现**。
- **失败模式**：publisher 必须改变发布流程（XL）；`fs-verity` 启用权限语义（是否允许非特权属主启用、对已发布文件启用需写 fd）**未在目标内核/`trimacl` 上验证**；老文件/旧账本无 verity；回滚/兼容；`/sys/fs/btrfs/features` 列出 `verity`（写作时只读观测）只证明特性声明，不证明可用性。
- **trimacl 可行性**：unknown（必须原生验证）。
- **权限**：publish 侧需要相应能力；验收侧只读。
- **生产影响**：发布链路改动 + CPU/空间（Merkle 树小）；长期收益大。
- **实施复杂度**：**XL**。
- **证据可验收性**：长期**高**（但本轮不可用）。

### 2.9 方案 F4：独立主机只读拉取比对

- **机制**：由**独立主机/独立存储**通过 SSH/只读文件协议拉取曲库文件字节并计算哈希，与账本比对；或把快照/副本同步到独立主机后离线校验。
- **能检测**：D1/D2/D3（独立观测）；提供独立于本机账本/存储链路的 witness（补方案 D 的"同源一致错误"盲区）；D6；坏块物理独立性（若为全量复制）。
- **不能检测**：拉取窗口内源仍可写（需要源侧 bracket/只读导出）；网络错误/截断（需要哈希+长度）；位置/名字映射；W4；LRC/播放。
- **失败模式**：传输时源变化；网络/凭据/权限；成本与时间；第二主机信任与管理。
- **trimacl 可行性**：本机侧只读；需要用户提供第二主机与授权（D4/D 决定）。
- **权限**：源侧只读；目标侧写空间。
- **生产影响**：只读网络/IO；视数据量。
- **实施复杂度**：**M**。
- **证据可验收性**：**中**（作为外部 witness 有价值；本需求主要目标可以本机完成，是否有第二主机取决于用户）。

### 2.10 方案 F5：SMB 只读导出 / 只读 bind mount / `chattr +i`（否决项）

- **机制/为何否决**：
  - **SMB 只读导出**：只限制通过该导出路径的写；**不阻止**本地进程、扩展、root 或原共享其它写路径；不能提供不可变保证。
  - **只读 bind mount/remount ro**：是**mount namespace 内**的视图约束，不阻止通过其它挂载/原始路径写；root 可 remount；且本机已证明 RO 子卷语义可被写（E3）；runbook 也明确"普通只读 bind mount、inode immutable 标记不是此处的稳定性证明"（`MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md:83`）。
  - **`chattr +i`**：设置需要 root/CAP_LINUX_IMMUTABLE；root 可清除；不是对 W4 的防御，也不能证明扫描期无写入者（只是事后状态）。
- **结论**：不作为独立方案；可作为"减少特定写者"的辅助配置（例如维护窗口内禁止 SMB 写入），但**不得**计入不可变证据链。

### 2.11 汇总矩阵

| 方案 | 时间点视图 | 检测"已发布文件被改" (D1/D2) | 检测"视图被改" (D4) | 绑定当前实际文件 | 需要停写 | 物理独立性 | 本机 trimacl 可行性 | 生产影响 | 复杂度 | 单独可验收 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A 停写+快照+gen bracket+重读 | 是（子卷快照） | 强（bracket+live 重读） | 强（若 gen 语义验证通过） | 是（live 重读） | 可选（Grade B 需要；Grade A 不需要） | 否（快照共享 extent） | **中–高**（快照已验证；曲库需独立子卷或子树模式） | 中 | L | 高（前置后） |
| B 扫描副本（reflink/全量） | 逐文件近似（非整树） | 强（副本+live 重读） | 中（验证子卷 bracket；reflink 不自带） | 是 | 否 | reflink 否；全量是 | **中**（FICLONE 未验证；/tmp 不可用） | 低–中 | M–L | 中–高（需新门） |
| C send/receive 独立树 | 是（需 RO 快照） | 强 | 强（接收树独立） | 是 | 否 | **是**（新 extent） | 中（未验证；成本高） | 中–高 | M–L | 中 |
| D 账本指纹 vs 现库 | 无 | 弱（仅内容，无时间/身份） | 无 | 否（无位置对应） | 否 | 否 | 高（只读） | 极低 | S | **否**（必需组件） |
| E 双遍哈希+stat | 无（两个瞬间） | 弱–中（窗口外盲区） | 无 | 部分 | 否 | 否 | 高 | 低–中 | S | **否**（辅助） |
| F1 fnOS 平台快照 | 是（但时点不可控） | 中 | 同 A 的 RO 问题 | 可（子树扫描） | 否 | 否 | 中（需平台证据/ACL） | 低 | M | 低–中 |
| F2 fsfreeze+快照 | 是（强） | 强 | 强 | 可 | 冻结整 FS（极短） | 否 | 未验证 | **很高** | M | 不建议主路径 |
| F3 fs-verity | 文件级完整性 | 强（字节） | 不适用 | 文件级可 | 否 | 否 | 未验证 | 发布链路大改 | XL | 长期 |
| F4 独立主机拉取 | 依赖源侧 | 强（外部 witness） | 源侧 bracket | 可 | 否 | **是**（全量） | 需第二主机 | 低（只读） | M | 中 |
| F5 SMB ro/bind ro/+i | 否 | 否 | 否 | 否 | — | 否 | — | — | S | **否决** |

## 3. 推荐方案与关键设计参数

### 3.1 推荐组合（分层，缺一不可）

> **L1 时间点视图（快照优先，副本备选） + L2 视图身份/代际 bracket + L3 逐位置对现存文件的独立重读 + L4 账本指纹 oracle + L5 fail-closed 证据封存 + L6（可选）无写者证明/Grade B**

| 层 | 作用 | 关键要求 | 最小证据 |
| --- | --- | --- | --- |
| L1 时间点视图 | 给长扫描一个不随时漂移的内容视图 | 快照优先（子卷粒度）；副本备选（`FICLONE` 优先，失败显式处理）；视图身份可绑定 | 视图身份 tuple（treeid/uuid/parent_uuid/generation/`Gen at creation`）+ 创建命令/时间/脚本哈希 |
| L2 bracket | 检测"视图在扫描期被写"（RO 位不可靠的补偿） | 扫描开始/结束、每 fd 的视图 tuple 复核；根 helper 在最终检查前做 **sync barrier**（root，见 §3.4）；任何 tuple/gen 变化 → unknown | 每次 tuple 观测值（固定字段）+ 检查点序列 |
| L3 现存文件重读 | 把"视图时间点"绑定到"当前实际文件"（A4 对象） | 对每个匹配位置：在**现存曲库**按相对路径二次 open + 哈希 + stat 身份（ino/size/mtime/ctime）+ 子卷身份；与视图记录比对 | 每位置 `live_reread=ok/mismatch`（不输出路径）；汇总计数 |
| L4 账本 oracle | 提供"应该是什么"的独立输入 | 账本 `published_sha256/size` 只经授权管理员查询取得；缺指纹→unknown | 输入位置数与指纹完整性；账本查询时间/来源（不落敏感值） |
| L5 证据封存 | 让证据可复核、防"碰巧通过" | 固定 schema、固定 unknown 原因码（不含路径）、重试记录、脚本/文档哈希、时间戳、退出码语义；证据生成只写 stdout（或受控证据文件） | JSON + 操作员报告 + 哈希清单 |
| L6 无写者证明（Grade B，可选） | 把"匹配文件未变"升级为"整库子树未变" | 作业终态/无在途；无保存线程/暂存空/账本 mtime 不变；无其它进程对视图/曲库持有写者；SMB/NFS 无会话；窗口内无维护写操作 | `/proc` 检查快照、`stat` 指纹、操作员声明、证据时间线 |

**为什么不能只做 L4**：L4 会"一致地错"（§2.4 盲区）且无位置/时间保证；**为什么不能只做 L1/L2**：快照只能证明"视图内容"，A4 要的是"实际文件"；**为什么不能只做 L3/L5**：没有时间点视图时长扫描的重读结果无法组合成一个一致状态；**为什么停写不是正确性前提**：已发布文件在 `publish_noreplace` 语义下不会被扩展自身二次写，A4 只需证明"匹配的现存文件在证据窗口内可被独立观测为正确字节"；停写是更强声明（Grade B）与降低误报的手段。

### 3.2 拓扑选项与推荐（本机实测约束下）

**约束**：曲库是 `/vol1/1000`（子卷 256）内普通目录（E9）；用户子卷 >100k 文件（E11）> 扫描器 `MAX_ENTRIES=100,000`；用户子卷 generation 是**全子卷粒度**，家目录任何无关写入都会推进它（使整家快照的 bracket 误报）；曲库本身 363MB/4 文件（E12），适合库级快照/副本。

| 拓扑 | 形态 | 优点 | 缺点/代价 | 适用 |
| --- | --- | --- | --- | --- |
| **T-A 曲库独立子卷** | 把验收曲库根改为独立 Btrfs 子卷（`btrfs subvolume create` + 迁移文件 + 配置/部署变更，需用户批准），快照该子卷作为扫描根 | 扫描根=子卷（现有扫描器门天然接受快照根）；条目/字节预算小；generation 只反映曲库写入（bracket 干净）；快照创建/删除轻 | 需数据迁移/配置变更/服务重启（受 §0.1 约束）；**嵌套在用户子卷内的子卷不会被 fnOS 周期快照 `@snapshot/1000` 包含**（备份/快照覆盖变化，用户需知情）；生产化后仍需重新评估 | P7 验收（测试库）；长期例行化的目标形态 |
| **T-B 用户子卷快照 + 子树扫描** | root 对 `/vol1/1000` 做一次性 `btrfs subvolume snapshot -r`（目标路径放在专用 root-only 验证目录），扫描器新增"子树模式"：根是快照内曲库子目录，验证其包含子卷 tuple = 已记录的快照 tuple；只遍历曲库子树 | **不改数据布局**；快照覆盖完整（用户子卷无嵌套子卷，E9）；创建/删除为元数据操作；扫描子树回避 100k 上限 | 快照整家子卷，删除开销可能较大（未实测）；快照路径的写权限/访问需验证（E10 路径 mode 0 root）；扫描器需新子树模式；若误用整家 gen 校验会因无关写入误报（本设计规定只对子树/快照 tuple 校验，不对家目录 gen 作全窗断言） | **P7 最小变更默认**（若用户不接受数据布局变更） |
| **T-C 受控副本** | 仅复制曲库目录到专用验证子卷/目录（`FICLONE` 优先，普通复制 fallback），扫描副本；live 重读现存曲库 | 不需要子卷/整家快照；只复制 363MB；对生产影响最小（reflink 时） | `FICLONE` 在 `trimacl` 未验证；副本不是整树时间点；共享 extent 无物理独立性；扫描器需"副本模式"新门；需一次性创建验证子卷（用户批准） | 用户不允许任何快照调度/数据布局变更时的备选 |
| **T-D send/receive** | 需 T-A/T-B 的快照，再 send/receive | 物理独立 | 成本高，过度设计 | 不推荐本轮 |

**推荐**：**T-B 作为 P7 的最小变更默认**（不改数据布局、快照覆盖完整、代价可控；需扫描器"子树模式"与 root-only 验证目录），**T-A 作为长期目标形态**（若用户愿意做一次受控数据布局变更并接受 fnOS 快照覆盖影响），**T-C 作为两者都被否时的备选**（必须先完成 FICLONE 原生验证）。**无论哪种，都不得把"整用户子卷快照"直接当作扫描根（条目上限）或将整家 generation 当作全窗 bracket。**

### 3.3 推荐流程（阶段 P0–P8；本设计不执行，任何设备动作需后续授权）

| 阶段 | 动作（root helper / 扫描器 / 操作员分工） | 前置/检查 | 失败处置 |
| --- | --- | --- | --- |
| P0 准备（只读） | 操作员确认作业终态；读取曲库路径/挂载/子卷/容量/暂存状态；读取本次位置的账本指纹；记录脚本/设备/版本哈希 | 曲库/暂存/快照目标路径与身份已核实；无在途写者或已授权窗口 | 任何 unknown → 停止 |
| P1 视图创建（root） | T-B：对用户子卷建 RO 快照到专用 root-only 目录，记录快照 tuple + `Gen at creation` + 创建时间；T-A：对曲库子卷建快照；T-C：复制曲库到验证目录并记录复制模式/每文件结果 | 目标路径不存在（防覆盖）；源子卷身份匹配预期；记录前后源 generation（仅作为"创建窗口"竞态信息，不作整家全窗断言） | 目标已存在/身份不符/权限不符 → 停止；失败副本删除并核验 |
| P2 扫描（非特权扫描器） | 扫描视图（T-B 为快照内曲库子树），按 size 命中做哈希；每 fd 复核视图 tuple；输出内部匹配表（不输出路径） | 扫描器 uid 不可写视图（D3）；输入位置数/大小上限；平台 x86_64/Linux | 任何 tuple/gen/stat/IOCTL/超限异常 → unknown；单次重试策略见 §3.5 |
| P3 live 重读（非特权扫描器） | 对每个匹配/歧义位置：在**现存曲库**按相对路径 openat2 二次打开，独立哈希，比较 stat 身份与内容；比较账本指纹 | 现存曲库根身份 = 预期子卷；路径解析仍用 openat2（BENEATH/NOFOLLOW/NO_XDEV） | 任一 mismatch → 该位置 unknown（或整批 unknown，见 §3.6） |
| P4 封存屏障（root） | `syncfs`/`sync -f /vol1` 或 `btrfs filesystem sync`（需审查权限/语义）后，扫描器做最终 tuple + stat sweep | 屏障前所有已知写入应已提交；屏障后不再有写者（Grade B）或声明窗口边界 | 最终 tuple 变化/stat 变化 → unknown；重试或 BLOCKED |
| P5 证据输出 | 扫描器输出 JSON（固定 schema）；root helper 输出证据清单（视图 tuple、gen 时间线、清理证据、脚本哈希） | no paths/no names/no exceptions；退出码语义固定 | 不完整 → 退出码 2/fail-closed |
| P6 清理（root） | 删除快照/副本（精确路径），核验无残留子卷/目录，记录 `btrfs subvolume list` 差异 | 只删本任务创建的精确路径；先确认路径身份 | 清理失败 → 保留现场、记录 unknown，不"假装清理成功" |
| P7 复核（独立评审） | 独立复审者按 §6.3 检查原始 JSON/哈希/时间线/清理/未过度声称 | 与实现/执行无关 | 不通过 → 证据作废 |
| P8 A4 衔接 | 操作员把扫描结果填入 A4 记录；basename/标签/LRC/可播放另取证据 | 只把扫描结果用于其能力范围（字节一致性） | 任何缺口 → A4 保持 BLOCKED/NOT RUN |

### 3.4 关键设计参数

| # | 参数 | 推荐值/规则 | 理由/备注 |
| --- | --- | --- | --- |
| P-1 | generation 采样点 | ①快照创建后读一次（记录 `Gen at creation` 与当前 generation 基线）；②扫描开始时读视图根 tuple；③每个目录/文件 fd 复核（沿用现有 `_require_same_snapshot`）；④扫描结束读视图根 tuple；⑤**P4 屏障后**再读视图根 + 现存曲库根 tuple；⑥最终 stat sweep 后再读一次 | 实现前必须按 §4.3 验证：每次观测在写入路径上是否必然变化；**未验证路径只能作为辅助信号** |
| P-2 | 采样频度 | 每 fd（已有）+ 阶段边界（新增）；不在每字节/每 1MiB 采样（IOCTL 成本） | 文件级 fd 采样可发现"读期间被改"；阶段边界防止"视图被写/提交滞后" |
| P-3 | sync barrier | 在最终 bracket 前由 root 执行一次文件系统同步（`syncfs(2)`/`sync -f /vol1`/`btrfs filesystem sync` 三选一，须实现时审查：权限、范围、对生产的影响） | 让"屏障前已发出但未提交"的写入在最终 generation 观测中可见；**这不是提交所有未来写入**，只是封住滞后窗口 |
| P-4 | stat 身份字段 | `(st_ino, st_size, st_mode, st_mtime_ns, st_ctime_ns)` + 每侧子卷身份；**跨子卷不要比较 `st_dev`**（快照与源可能是不同匿名设备）；同侧要求 `st_dev == 根 st_dev` | 现有 `_stat_unchanged` 字段可复用；`st_ctime` 能捕捉"改后恢复 mtime"（非 root） |
| P-5 | 重读范围 | 仅对**候选匹配/歧义**位置做 live 重读（≤1,000 位置；单文件 ≤512MiB；总量受位置数约束）；全量重读不做 | 成本可控；全量重读收益低（非匹配文件不影响 A4） |
| P-6 | 双遍/延迟参数 | 双遍只用于候选（第二遍 = live 重读）；可选延迟 `T>=0`，由证据窗口定义决定，不做固定延迟 | 避免"靠 sleep 制造稳定假象" |
| P-7 | 变化处置 | **fail-closed**：任何 D1–D8 未通过 → 整批 `complete:false` + 每位置 `unknown` + 固定原因码；**最多自动重试一次**（仅限外部竞态；重试必须新建视图并重新执行 P1–P4；两次证据都保留）；第二次失败 → **BLOCKED**，人工处理 | 不允许"重试直到通过"；不允许把 unknown 降级为 no_match/通过 |
| P-8 | 超时/上限 | 沿用现有 10,000 位置/100,000 entries/512MiB 上限；新增整体扫描超时（建议 600s，验证时按库大小定）并 fail-closed；超时不得输出 `complete:true` | 防止长扫描被误当稳定；超时=unknown |
| P-9 | 重试/尝试编号 | 证据 schema 记录 `attempt`（1/2）、每次的视图 tuple/时间线/失败原因 | 让复审看到"第一次失败"不是被掩盖 |
| P-10 | 视图目录权限 | 专用 root-only 验证目录（如 `/vol1/@fnmusic-verify/` 或用户批准的位置）；扫描用户只读（ACL），普通用户/同 uid 不可写；**必须在目标 uid 上实测有效写拒绝**（`trimacl` 下 mode 不是证据） | D3 的一部分；路径选择需用户批准（不得污染 fnOS `@snapshot`） |
| P-11 | 证据窗口定义 | 窗口 = [视图创建时刻 T0, P4 最终观测时刻 T4]；证据明示：**不能保证 T4 之后**的变化；A4 记录中必须写明窗口 | 避免"事后变化"被误认为"验收期内变化" |
| P-12 | 现有硬门 | `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` **保持 `False`**；新设计若实现，必须新增独立命名的新门常量（例如 `_POINT_IN_TIME_SCAN_VALIDATED`，默认 `False`），由**单独的、经独立复审的代码变更**在 §5.1 判据全部满足后才可置 `True`；不得复用/绕过旧常量 | 防止"边实现边放宽"；维持现有 unknown 行为直到证据齐备 |

### 3.5 变化检测与处置策略（fail-closed / 重试 / 锁 / quiesce 语义）

1. **检测即失败关闭**：任何 bracket 不匹配、视图 tuple 变化、live 重读不一致、stat 变化、账本指纹缺失、平台/权限/上限不满足 → 整批 `unknown`（exit 2）。**不存在"部分 complete"**（现有契约；保持）。
2. **重试**：只对"外部竞态"（Grade A 下他人写视图/曲库导致的变化）允许**一次**自动重试：丢弃本次视图 → 重新走 P1–P4 → 两次证据都保留。**不允许**对"权限/平台/输入/超限/实现错误"重试掩盖问题。第二次失败 = BLOCKED（人工）。
3. **锁**：本设计**不依赖**跨进程锁。可选实现：受审的"维护锁"（网关在锁持有期间拒绝 `advance`/写路径；需代码变更与部署）可把 Grade B 从"操作员声明"提升为"机制强制"；未实现前不得声称有锁。**不得**用账本 DB 的写事务/文件锁作为扫描期互斥（会干扰生产且语义不清）。
4. **quiesce 的两档语义**：
   - **Grade A（默认，正确性不依赖停写）**：不做全量停写；证据 = "视图时间点 + 视图 bracket + 现存匹配文件在 P3/P4 窗口内被独立观测为账本字节"；残余风险 = 窗口终止后的变化、未验证的 generation 盲区、W4。
   - **Grade B（更强，需用户批准与窗口）**：在整个窗口内对"能写验收曲库子卷/视图的写者"实现无写者证明（作业终态、无在途保存线程、staging 空、账本 DB mtime 不变、`/proc` 无对曲库的写 fd、SMB/NFS 无会话、窗口内无维护写；可选维护锁），并在证据中记录每项检查的时间与结果。**Grade B 是推荐给 P7 A4 的档位**（因为验收需要更强声明），但若用户选择 Grade A，则扫描结果只能作为**支撑证据**，A4 不得据此判通过（D1/D7）。
   - quiesce **不需要重启服务**：A3 合同保证逐首推进、作业终态后无在途工作；操作员只需不触发新 advance、等作业终态；若发现后台线程/暂存残留则停止。
5. **发现变化后的处理**：先保留现场（不删视图/副本，记录哈希）、输出 unknown；分析原因（外部用户/索引器/自身脚本），决定是否走一次重试；**不得**通过修改账本/文件"使之一致"来消除 unknown（那是伪造证据）。

### 3.6 与 P7 "逐位置核对"（A4）和重复位置报告（A5）的衔接

- **唯一匹配 `unique_match`**：视图内恰好一个文件的 `(size,sha256)` 与位置指纹一致，且 live 重读在同一相对路径上观测到同一字节与身份 → 作为该位置的**字节一致性证据**（支持 A4 的字节维度）。扫描器不输出文件名；A4 的"basename 可独立对应"必须另取证据（D6），例如隔离测试库的受控文件清单/管理员受控观测；不得把 `unique_match` 当成名字对应证明。
- **`no_match`**：视图/现存库不存在与指纹一致的字节 → A4 失败（内容缺失/不同）；必须人工复核（可能是账本记录与落盘不一致——这本身就是需要记录的重要发现）。
- **`ambiguous`（重复位置 A5）**：多个文件同字节。**仅当**（a）任务 manifest 存在重复 `source_id` / 预期重复位置，且（b）`match_count` 与预期重复数一致，且（c）操作员提供独立 basename 映射证据（A5 要求解释"basename 不同"）时，才可在 A4 记录中作为"可解释的重复"处理；否则一律 unknown/BLOCKED。扫描器只报告 `ambiguous` 与计数，**不做裁决**。
- **A5 的"第 2 次通常 conflict（未覆盖）"**：`publish_noreplace` 不覆盖语义使同名文件不会被二次写；扫描器不验证"没有第二次写入"，但 live 重读覆盖"现存字节是否正确"，A5 的冲突日志仍由账本/route 证据负责。
- **A4 仍为多证据合成**：字节一致性（本设计）+ basename/位置对应 + `ffprobe` 可解码/格式/实际音质/完整标签/封面/LRC/索引/播放（runbook 既有要求）。本设计**不替代**这些；也不得用扫描器结果填补 A4 缺项。
- **合同/手册变更**：若要把扫描输出写入 A4 判据，需要修改 `MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` 与合同并独立审查；本文不修改它们（D7）。

### 3.7 为什么长扫描期间被改也能被发现（时间窗口与终止边界）

- **视图隔离**：快照/副本创建后，扫描读的是视图；源子卷在扫描期间的变化**不会**进入视图（快照共享 extent，写入触发 COW；副本是独立/半独立数据）。
- **视图被写**：RO 位不可靠 ⇒ 任何能访问视图路径的写者都可能写视图；L2 bracket（每 fd + 阶段边界 + sync barrier 后终检）检测已提交变化；未提交/不推进 generation 的路径是**残余盲区**，由"视图路径仅 root/扫描 uid 可读、无人可写（D3）"+ Grade B（无写者）+ §4 原生验证共同压缩，并必须写入残余风险。
- **保留到 A4 时**：live 重读在 P3/P4 把视图绑定到现存文件；P4 的最终 stat sweep（所有匹配文件）使"重读之后又被改"可在最终观测处被发现（若改动作随 stat 可观测）；**T4 之后的变化超出证据窗口**（必须明示）。若需要更长的"验收期不变"声明，只能靠 Grade B 无写者证明或 fs-verity 等长期机制（不在本轮）。

### 3.8 证据产物与机器可校验 schema（提案，不是现有契约）

> 以下只是**设计提案**；实现必须放在新的、单独复审的变更里，且默认行为保持现状（旧扫描器继续 unknown）。不得把本提案当作已批准 schema。

建议输出（仅 stdout 或 root helper 证据目录，不含路径/名字/异常文本）：

```json
{
  "scope": "verified_point_in_time_tree",
  "evidence_schema_version": 2,
  "complete": false,
  "attempt": 1,
  "checks": {
    "view_identity": "ok|mismatch|unknown",
    "view_generation_stable": "ok|changed|unknown",
    "scanner_uid_readonly": "ok|writable|unknown",
    "sync_barrier": "done|failed|unknown",
    "live_reread": "ok|mismatch|unknown",
    "limits_within_bounds": "ok|exceeded|unknown"
  },
  "unknown_reason": "view_mutated|live_changed|identity_changed|content_changed|generation_lag_unverified|input_invalid|platform_unsupported|limits_exceeded|permission_unverifiable|null",
  "view": {"tree_uuid": "…", "parent_uuid": "…", "generation_start": 0, "generation_end": 0, "gen_at_creation": 0},
  "positions": [{"position": 1, "status": "unique_match|no_match|ambiguous|unknown", "match_count": 0, "live_reread": "ok|mismatch|unknown"}],
  "timing": {"started_at": "…Z", "finished_at": "…Z", "wall_seconds": 0}
}
```

- 固定 `unknown_reason` 白名单；**禁止**回显路径/文件名/异常文本（沿用现有安全约束）。
- 现有 `verify_position_file_evidence.py` 的旧输出必须保持不变（默认 unknown）；新 schema 只能在新模式/新脚本中启用，避免破坏既有消费者。
- root helper 另出证据清单：脚本/文档 SHA-256、命令类别、视图 tuple、generation 时间线（每次观测值+时间）、清理前后 `btrfs subvolume list` 差异、设备版本/内核/挂载；**不记录**曲库文件名/路径到公开证据中（必要时用受控编号）。

### 3.9 回滚/清理与残留核验

- 只删除本任务创建的**精确**视图路径（快照子卷/副本目录），删除前复核路径身份与创建记录；禁止通配/递归清理生产目录。
- 清理后核验：`btrfs subvolume list /vol1` 无本任务子卷；`/vol1/1000` 顶层条目集合与运行前一致（沿用探针 §7 的集合核验方法）；扫描器/root helper 进程退出；无临时文件残留。
- 清理失败 → 保留现场并记录 unknown（不得"假装清理成功"）。

## 4. 失败注入验证计划（仅隔离测试数据，**不触生产**）

> **治理边界**：本节是**计划**，本设计**不执行**任何注入；执行必须另立任务、取得用户明确授权、在隔离测试数据上进行（不得写生产曲库/暂存/账本、不得停启服务/改配置）。仅允许：①离线单测（Windows/Linux，mock 视图/IOCTL）；②在隔离的 Linux 测试环境（NAS `/tmp` 上的 btrfs 镜像/一次性受权测试树；**不是** `/vol1` 生产路径）做真实内核语义测试。无法真实诱导的项必须如实记为 mock 覆盖/未覆盖，禁止用 mock 冒充原生证据（与现有离线测试原则一致）。

### 4.1 注入矩阵

| ID | 注入（隔离环境） | 预期检测 | 判据（通过条件） | 否定对照 |
| --- | --- | --- | --- | --- |
| I1 | 扫描进行中向视图（快照）内写一个文件（root 与 uid1000/CapEff=0 各一次，先复现 E3） | 视图 tuple/generation 变化 → unknown | `complete:false`、`unknown_reason=view_mutated`、退出码 2；注入后视图内确有文件/内容变化 | 同样夹具不注入 → `complete:true`（证明检测不是"永远失败"） |
| I2 | 扫描进行中改写一个**已匹配文件**内容（同 size） | live 重读内容不一致 → unknown | 该位置/整批 unknown、`live_reread=mismatch`；无 `unique_match` 作为最终结论 | 不注入 → `unique_match` + `live_reread=ok` |
| I3 | 发布后 truncate（size 变小） | size/内容不一致 → unknown（或 no_match，按 §3.6 规则） | 位置状态符合 §3.6 规则；`complete:false`；不出现 `unique_match` | 不注入 → 正常 |
| I4 | rename 同名替换（不同 inode，异字节） | ino/stat/内容变化 → unknown | `identity_changed`/`content_changed`；不出现 ok | 不注入 → ok |
| I5 | 扫描期间目录项增删（制造同字节重复 / 删除匹配文件） | 目录 stat/视图 bracket/匹配数变化 → unknown 或 `ambiguous` 计数变化 | 按 §3.6 裁决；不得静默 complete | 不注入 → 计数稳定 |
| I6 | RO 位不可靠复现：视图 `ro=true` 仍写入（E3 复现）并推进 generation；再测"写后不 sync"时 generation 的可见性 | 已提交写入：bracket 检出；**未提交写入：记录 generation 滞后**（这是关键 native 验证） | 已提交路径必须 100% 检出；滞后路径必须记录与设计参数（sync barrier）后的检出结果 | 无写入 → 视图 tuple 不变 |
| I7 | 元数据写入（chmod/xattr/utimes/rename 不改变内容） | 验证 generation/stat 是否变化 → 决定是否需要额外检测 | 若 generation 不变但 stat 变 → 必须依赖 stat/目录检查；若都不变 → 记为**已知盲区**并升级为设计约束（视图路径无写者/Grade B） | 不注入 → 无变化 |
| I8 | ABA：改写后再写回原字节 + `utimes` 恢复 mtime | ctime 变化 → 检出；若仅恢复内容不恢复时间 → mtime 检出 | 若检出 → 通过；若未检出 → 记录为盲区（仅限 root 能力） | 不注入 → 无变化 |
| I9 | 快照创建竞态：创建前/后紧邻写入源子卷 | `Gen at creation`/源 generation/创建窗口记录能识别竞态；live 重读发现现存文件变化 | 竞态必须被识别或明确记录为"创建窗口边界"；不得在竞态下输出 complete | 无竞态 → ok |
| I10 | 扫描器身份：用可写 uid 扫描 | 新"扫描器不可写"门拒绝（fail-closed） | `unknown_reason=permission_unverifiable`/等价固定码；不得开始遍历 | 专用只读 uid → 允许（且 live 重读正常） |
| I11 | 副本模式：`FICLONE` 不支持/失败（若隔离环境可诱导） | 显式失败关闭或明确降级并记录模式 | 不得静默全量复制；降级必须在证据中可见；`EOPNOTSUPP` → unknown 或记录 fallback 模式 | 支持 FICLONE → reflink 模式记录 |
| I12 | 上限/平台：>10,000 位置、>100,000 entries、超大文件、非 Btrfs、symlink/子卷/跨挂载、IOCTL 失败 | 整批 unknown | 每种情况 `complete:false` + 固定原因码；无路径泄露 | 边界内输入 → 正常 |
| I13 | 泄露：让异常/路径含敏感串（token/路径/文件名） | 输出不含路径/文件名/异常文本 | 报告与 stderr 均不含注入串 | 正常输入同样检查 |
| I14 | 重试：第一次注入变化、第二次干净 | 第一次 unknown + 第二次 complete 且证据保留两次；第二次注入变化 → BLOCKED | 不允许"只保留成功那次"；`attempt` 字段/证据清单齐全 | 第一次干净 → 无需重试 |

### 4.2 如何证明"检测到变化"而不是"碰巧通过"

1. **成对对照**：每个注入场景跑"阳性（注入）+ 阴性（同夹具不注入）"两次；阳性必须 fail-closed 且**固定原因码匹配注入类型**（不是笼统 unknown），阴性必须 complete；否则该注入项不算验证通过。
2. **可观测注入**：注入动作本身留独立证据（测试夹具日志/标记文件/计数器），断言注入确实发生在目标窗口内（例如在读取第 N 个文件后、或在 P2→P3 之间），避免"注入没发生所以看起来安全"。
3. **多时点矩阵**：对同一注入在多个注入时点（扫描前、读取中、读取后、P3 重读中、P4 终检前）各跑一遍；要求**所有**时点都不出现 `complete:true`；这覆盖竞态窗口。
4. **确定性 + 随机化**：核心窗口用确定性钩子（`read/hash` 的测试钩子/受控文件系统操作）保证可复现；再以随机化参数（文件顺序、注入字节偏移、延迟）重复 N 次（建议 ≥20），确认无"偶然通过"。
5. **环境真实性分层**：mock 测试只证明"代码对输入的判定"；**原生内核语义（generation 推进/滞后、FICLONE、RO 绕过）必须用真实内核实验证明**，且在报告中分开记录（mock/真实、Linux/Windows skip=NOT RUN），不得混用。
6. **独立复审与复跑**：注入证据、原始日志、测试代码哈希由未参与实现者复审；至少一次独立复跑关键注入项（隔离环境）。

### 4.3 原生代际语义验证（G1，最关键的前置）

在隔离 btrfs 测试树（非生产）上，对**与本设计相关的全部写入路径**逐一测量"快照/子卷 generation 是否推进、何时推进"：

- 操作矩阵：创建文件、写小/大文件、追加、truncate、rename、unlink、chmod、xattr、utimes、sparse write、`sync`/不 `sync`/sleep 跨提交、`btrfs property set ro true/false`、在 `ro=true` 视图内写（复现 E3）、快照创建/删除。
- 每个操作：记录操作前 generation、操作后立即 generation、`sync` 后 generation、再等待后 generation；重复 ≥3 次；同时记录 `st_*` 与内容哈希。
- 判定：**若存在"内容/身份已变但 generation 不变"的路径** → generation bracket 不能单独作为视图完整性证明，必须依赖"视图无写者（D3/Grade B）+ stat/目录检查 + live 重读"，并在残余风险中登记；**若所有相关路径在 sync barrier 后都推进** → 可作为 bracket 的主要信号（仍需独立复审）。
- 同时记录 `trimacl`/`trimafs2` 与上游差异的观测事实（不定位补丁、不外推）。

### 4.4 测试分工

| 层 | 环境 | 覆盖 | 限制 |
| --- | --- | --- | --- |
| 单元/合同测试 | Windows/Linux 离线（mock IOCTL/stat/视图） | schema、fail-closed、输入校验、重试、原因码、不泄露 | 不证明原生语义；Windows skip=NOT RUN |
| 原生失败注入 | 隔离 Linux btrfs（NAS `/tmp` 镜像/受权测试树） | I1–I11 的真实内核路径 | 需用户授权；不得写生产；必须清理并核验 |
| 端到端演练 | 隔离测试库（非生产） | 发布→扫描→重读→证据→清理全流程 | 不代表生产部署/服务链路；不证明 A4 其它维度 |
| 设备窗口（P7） | 生产测试库 | 真实 A4 场景 | 仅在前置全部通过、门禁解除后；本设计不执行 |

### 4.5 结果记录格式

- 每次注入：`id / injection / environment(host,kernel,fs,mount) / time / fixture / expected / observed(report_json_hash) / pass|fail / mock|native / reviewer`。
- 汇总表必须给出：通过数/失败数/**未覆盖数**（含 Windows skip、mock 冒充风险）；未覆盖项不得计为通过。
- 所有测试代码/夹具/日志的 SHA-256 与仓库状态；独立复审结论。

## 5. 验收判据与残余风险

### 5.1 扫描器从 `unknown` 升级为"可用"的判据（全部满足，缺一不可）

- **G1 原生代际语义**（§4.3）：在隔离本机 btrfs 上完成相关写入路径矩阵；明确哪些路径可检测、哪些是盲区，并有原始日志。
- **G2 视图与 bracket 语义**：快照/副本的创建-记录-终检流程在隔离环境端到端验证；`Gen at creation`/tuple 绑定、`sync barrier` 后终检、重试语义均有真实证据。
- **G3 fail-closed 合同**：I1–I14 的阳性/阴性对照全部符合 §4.1 判据；任何未知/异常都 `complete:false` + 固定原因码；无路径/异常泄露；CLI 退出码语义固定。
- **G4 隔离端到端演练**：在隔离测试库（非生产）完成"发布→扫描→live 重读→证据→清理"，覆盖 unique/no_match/ambiguous/重复位置/同名替换/truncate/并发 rename 等场景；清理无残留。
- **G5 扫描器身份/权限**：专用只读 uid 或等价的"不可写"机制在目标设备上实测有效（`trimacl` 下 mode 不是证据）；写者证明/无写者检查可执行。
- **G6 独立复审**：未参与实现/执行者复核原始证据、失败注入方法学、过度声称、清理与仓库改动；结论 PASS-with-limits 或 PASS。
- **G7 手册/合同衔接**：A4 手册与合同对"扫描证据"的引入方式经用户/复审批准（D7）；设备窗口运行步骤与回滚/清理写入手册。
- **代码门**：上述全部满足后，由**新的、单独复审的代码变更**把新的门常量置 `True`（旧常量保持或按新语义显式替换，需独立审查）；**在完成前，扫描器继续返回 `unknown`，现有行为不变**。

### 5.2 必须由用户决定的事项

| # | 决定 | 选项 | 影响/默认建议 |
| --- | --- | --- | --- |
| D1 | 停写窗口 | (a) 无停写（Grade A，仅检测） (b) 短窗口 Grade B（P1–P4 期间无写者，不重启服务） (c) 整家停写 | (b) 推荐给 P7 A4（分钟级、只影响手动保存写入）；(a) 只能作支撑证据；(c) 影响用户使用，需充分理由 |
| D2 | 数据布局 | (a) T-B 用户子卷快照+子树扫描（不改布局） (b) T-A 曲库独立子卷（长期干净） (c) T-C 受控副本 | 推荐 (a) 做 P7 最小变更；(b) 需迁移/配置变更且改变 fnOS 快照覆盖，长期再评估；(c) 需 FICLONE 原生验证 |
| D3 | 扫描器身份 | (a) 专用只读用户 + ACL (b) 现有 uid1000 运行（同 uid 可写） | (a) 推荐；需一次受控部署变更；(b) 只能得 Grade A 且残余风险更高 |
| D4 | 原生失败注入授权 | (a) 允许隔离环境内核注入 (b) 不允许 | (b) → 无法取得 G1，扫描器保持 unknown（诚实结论） |
| D5 | 重试策略 | (a) 外部竞态自动重试一次 (b) 任何变化直接 BLOCKED | (a) 推荐（大幅降低误报）；两次证据都保留；(b) 更保守但验收可能反复失败 |
| D6 | 重复位置裁决 | (a) `ambiguous` 凭"重复 source_id + 独立 basename 证据"放行 (b) 一律 BLOCKED | (a) 需明确 basename 证据来源（账本 detail 不含 basename）；(b) 最保守 |
| D7 | A4 判据衔接 | (a) 先作支撑证据，A4 结论仍人工/复审 (b) 直接写入 A4 判据 | (a) 推荐（先积累证据）；(b) 需改手册/合同并独立审查 |
| D8 | 视图目录位置 | (a) 专用 root-only 验证目录（用户批准的新目录） (b) 现有 fnOS `@snapshot` | (a) 推荐（不污染平台目录）；(b) 不推荐（平台轮转/语义不受控） |

### 5.3 残余风险登记（即使完成 G1–G7 仍存在）

| # | 风险 | 影响 | 缓解 | 可否消除 |
| --- | --- | --- | --- | --- |
| R1 | generation 语义存在未验证写入路径/提交滞后 | 视图被写而未被 bracket 检出 | §4.3 穷举 + sync barrier + 视图路径无写者（D3/Grade B） | 不能完全消除，只能压缩并声明 |
| R2 | W4 root/管理员/被攻陷内核 | 任意伪造，证据可被操纵 | 信任边界声明 + 独立复审 + 证据哈希绑定 + 操作员范围约束；**不承诺防御** | 不能 |
| R3 | 扫描器/账本/脚本完整性 | 证据被篡改而不自知 | 脚本/文档 SHA-256、独立复审、干净执行环境、只读运行 | 不能对 W4 消除 |
| R4 | 账本指纹"意图 vs 观测"同源一致错误 | D 的盲区 | live 重读（独立于同一执行路径的 FS 读取）+ 可选外部 witness（F4） | 可大幅降低，非绝对 |
| R5 | basename/位置对应 | A4 无法仅凭扫描器证明 | 另取独立证据（D6）；ambiguous 严格裁决 | 需补充证据链 |
| R6 | A4 非字节维度（可解码/标签/LRC/封面/索引/播放） | 扫描器不覆盖 | runbook 既有独立证据；不得用扫描结果替代 | 设计边界 |
| R7 | 证据窗口终止（T4 之后）的变化 | 事后变化不在证据内 | 明示窗口 + Grade B/定期复扫；fs-verity 长期方案 | 不能消除 |
| R8 | reflink/快照共享物理 extent | 坏块/底层损坏同时影响视图与源 | 全量副本/独立主机（F4）用于需要物理独立时；声明限制 | 视方案 |
| R9 | trimacl/fnOS 版本漂移 | 本机结论不能外推 | 每次部署窗口重新验证（E1–E13 类观测）；不缓存跨版本结论 | 不能 |
| R10 | qgroup 告警/空间统计不可信 | 快照/副本空间与清理判断 | 以 `btrfs subvolume list`/实际 `df` 为准；不依赖 qgroup | 视平台 |
| R11 | fnOS 平台索引/缩略图未知写入 | 误报/漏报 | Grade B 无写者 + 窗口内观察 generation；发现写库行为则单独评估 | 需平台证据 |
| R12 | 长扫描资源/超时 | 验收失败或误判 | 明确超时 fail-closed、上限、隔离预演 | 可管理 |

### 5.4 明确仍未解除/仍 BLOCKED

- §0.1/§0.2 部署门禁仍 **BLOCKED**；`PIPELINE_INTEGRATION_ALLOWED=False` 未变。
- 扫描器**仍不能**作为文件验收证据；`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 仍为 `False`；P7 A4 仍 **NOT RUN/BLOCKED**。
- 匿名暂存的失败/权限/并发路径仍无真实内核证据（复审 §8 未解除项 2）。
- 本设计**没有**验证"停写→快照→扫描"设计、没有执行任何替代方案、没有做原生代际/失败注入实验。
- 不可外推到其它 fnOS 版本/内核/上游 Btrfs；不主张生产管线可用、不主张服务生命周期/身份/授权/备份/恢复已就绪。

## 6. 设计边界、实施改动点与复审点

### 6.1 本设计不被实现、不改门禁

- 本文只新增一个设计文档（仓内文件），**不修改代码、配置、设备状态、服务、账本、曲库、暂存**；不产生任何"已实现"的暗示。
- **不改门禁**：`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 保持 `False`；`_require_readonly_snapshot`/`_require_same_snapshot`/`_require_unprivileged_process` 语义不变；现有 CLI/输出/schema 不变；既有 unknown 行为不变。
- 任何后续实现都是**新任务**：需用户授权、独立复审、设备窗口与回滚/清理验证；在 §5.1 的 G1–G7 全部满足并由单独的代码变更前，不得放宽任何门。

### 6.2 需要后续 implementer 的最小代码改动点（提案清单，非本轮实施）

1. `scripts/verify_position_file_evidence.py`：
   - 新增"时间点视图模式"：允许扫描根为**已验证快照内的子树**（T-B）或**受控副本**（T-C），以明确的身份/权限/来源检查替代"根必须是 RO 子卷"这一不充分前提；
   - 新增 bracket 检查点、`sync barrier` 后终检、live 重读现存曲库、最终 stat sweep；
   - 新增固定 `unknown_reason` 原因码与 schema v2（**仅新模式启用**，默认行为保持不变）；
   - 新增"扫描器对树不可写"负向检查（D3）与相关 fail-closed 路径；
   - 不改变旧常量、旧 CLI、旧输出语义；不引入任何写树行为。
2. **新 root helper**（独立脚本，`scripts/` 下，未跟踪/待审）：创建/记录/清理快照或副本；记录 generation 时间线、`Gen at creation`、脚本/文档哈希；编排 P1–P6；只写**明确创建的一次性视图路径**；dry-run 与精确清理；不得硬编码生产路径。
3. **新证据 schema/文档**（`docs/`）：扫描证据格式、窗口定义、Grade A/B 定义、A4 衔接说明；由复审者审查后进入手册。
4. **测试**：扩展 `offline_tests/test_verify_position_file_evidence.py`（mock/Windows 安全）；新增 Linux 原生测试（隔离 btrfs）与失败注入夹具（I1–I14）；测试代码不得在生产路径产生写入；所有 skip 记录为 NOT RUN。
5. **手册/合同**（若 D7 选直接衔接）：修改 A4 步骤与合同（另立任务、独立审查）；不得在本设计生效前修改。

### 6.3 需要独立复审的点

- 原生代际语义矩阵（G1）的原始日志与结论是否支持"bracket 可检测"的范围；盲区是否被诚实登记。
- 视图/bracket 端到端：快照创建/删除、tuple 绑定、sync barrier 后的终检、重试与两次证据保留。
- live 重读是否真正独立于账本/同一执行路径，是否用 openat2 锚定、是否比较正确字段（跨子卷 `st_dev` 问题）。
- 扫描器身份/权限模型（D3）是否在目标设备上**实测**有效（`trimacl` 下不能只凭 mode/`access()`）。
- fail-closed：I1–I14 阳性/阴性对照、原因码、无路径泄露、退出码；mock 与原生证据是否分离标注。
- 证据窗口/终止边界是否明示；A4 衔接是否越权（不得把扫描结果用于其能力外维度）。
- 清理与残留：无本任务子卷/目录/进程残留；不得通配清理。
- 不改变旧门禁/旧行为：`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 仍为 `False`；旧 schema 不变；无隐藏的"放宽开关"。
- 是否遵守本机证据边界与 F1 引用规则（不把并行文档当独立来源/授权）；是否重复声称"生产可用"。
- 仓库改动范围与哈希绑定；是否只改允许的文件（本设计只允许新增本文档）。

### 6.4 后续任务拆分建议（供 Lead）

1. 用户决策（D1–D8）→ 记录在案。
2. 原生代际语义验证（G1；隔离环境，需 D4 授权）→ 独立任务 + 复审。
3. 扫描器新模式 + root helper 实现（G2/G3/G5）→ 独立任务 + 独立复审。
4. 隔离端到端演练（G4）→ 独立任务 + 复审。
5. A4 手册/合同衔接（G7）→ 用户/复审批准；之后才可讨论设备窗口验收。
6. 每一步都保持 §0.1/§0.2 BLOCKED，直到对应判据满足。

## 附录 A：证据引用与哈希

| 引用 | 用途 | 说明 |
| --- | --- | --- |
| `docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`（`ff3c4d0b…`） | E1–E8 主证据 | 已独立复审 PASS-with-limits；规范运行 2026-11-07 01:34:41–44 CST；§6 边界与 §9 归属说明必须一起引用 |
| `docs/BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW_20261107.md`（`8ad667af…`） | 复审边界/F1–F7/未解除项 | F1：并行会话归属/独立性不可核实；§8 明确未解除事项 |
| `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md`（`61e039…`） | **仅旁证** | 同一现象；与主证据血缘重叠，**不是独立来源、不是授权**（F1；其第 5/66 行归属表述与主证据 §9 冲突，以主证据为准） |
| `scripts/verify_position_file_evidence.py`（`197d43a9…`） | 扫描器契约/门 | 本文引用源码行号；实现前需重新核对 SHA-256 |
| `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md`（`3e0ea759…`） | A4/A5 要求与扫描器边界 | A4 BLOCKED 状态、basename/ffprobe/索引/播放的其它证据要求 |
| `docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md`（`6e164803…`） | §0.1/§0.2 门禁 | 仍 BLOCKED；本文不改变 |
| `docs/MANUAL_SAVE_PLAYLIST_JOB.md`（`4b5610ce…`） | detail 白名单/账本语义/幂等 | `published_sha256/size` 是 tagged 字节指纹、非落盘回读 |
| `docs/DEVICE_INVENTORY_20261107.md`（`d46342be…`） | 设备/路径/服务现状 | 仅引用，不重查其原始证据 |
| `docs/OFFLINE_VERIFIED_PIPELINE.md` / `docs/OFFLINE_POSIX_STORE.md` / `docs/ISOLATED_LEDGER_MIGRATION.md` | 发布侧既有假设/边界 | 均为候选/离线合同，不等于生产可用；扫描设计不得假设其已验证链路 |
| 写作时只读设备观测 E9–E13 | 拓扑/容量/服务现状输入 | **未独立复审、未重复采样**；实现前必须重新核实 |

## 附录 B：写作时只读观测记录（2026-11-07 JST 03:0x–03:1x；NAS CST 02:0x–02:1x）

| 观测 | 命令类别（只读） | 结果摘要 |
| --- | --- | --- |
| 子卷清单/拓扑 | `btrfs subvolume list /vol1`（49 项）、`btrfs subvolume show /vol1`、`btrfs subvolume show /vol1/1000`、`btrfs subvolume show /vol1/1000/fnmusic-test-20261031`、`btrfs subvolume list -o /vol1/1000`（0 项） | `/vol1` = FS_TREE subvol 5；`/vol1/1000` = subvol 256；曲库=普通目录；用户子卷无嵌套子卷 |
| 平台快照 | `ls -A /vol1/@snapshot/1000`、`btrfs subvolume show <最新>`、`btrfs property get -ts <最新> ro`、`stat /vol1/@snapshot /vol1/@snapshot/1000` | 2 个日期快照（10.26 / 11.02）；`Flags: readonly`、`ro=true`、`Gen at creation: 1677320`；路径 mode `0 root:root` |
| 容量/挂载 | `df -h /vol1 /tmp /`、`findmnt -T /vol1`、`findmnt -T /tmp`、`stat -c` 曲库/暂存 | `/vol1` btrfs `trimacl` 3.7T/1.6T 可用；`/tmp` 在 `/`（ext4）39G 可用；曲库 `750 kurumi:Users`，暂存 `700 root:root`、0 条目 |
| 条目上界 | `find /vol1/1000 -xdev … \| head -n 100001 \| wc -l` | 两种计数均 100001（截断）→ **>100,001**，超扫描器上限 |
| 文件现状 | `find /vol1/1000/fnmusic-test-20261031 -xdev -type f -printf` | 4 文件（3 音频 126,459,367/126,459,476/126,906,165 B + 944 B），属主 `kurumi:Users` |
| 文件服务 | `ss -ltn`（445/139/2049）、`systemctl list-units --all`（smb/nfs/snap）、`ls /usr/trim/bin` grep | 无监听；`smbd`/`nfs-server` inactive；`nmbd`/`winbind` 运行 |
| 空间统计 | `btrfs qgroup show /vol1` | `WARNING: qgroup data inconsistent, rescan recommended`（只读显示；含义 unknown） |
| 仓库状态 | `git rev-parse HEAD`、`git status --short`（只读） | HEAD `cf843a40…`；工作树多项未提交（本文不在其中，写作时新增本文档） |

## 附录 C：术语表

- **时间点视图**：快照/受控副本等"创建后不再随源变化的可读树"；其"点"的精度受创建机制与 bracket 证明范围约束。
- **bracket（括号校验）**：在扫描窗口前后/每个 fd 上复核视图身份 tuple（treeid/uuid/parent_uuid/generation/flags），用于发现视图被写/身份漂移。
- **`Gen at creation`**：`btrfs subvolume show` 输出的快照创建时代际（本机 E10 实测可读）；用于把视图绑定到源子卷的创建时刻；实现时须用 ioctl 字段或 btrfs-progs 输出独立核实，不得照抄本文。
- **live 重读**：扫描完成后在**现存实际曲库**上对匹配位置再次独立 open+哈希+stat，把视图证据绑定到 A4 的实际文件。
- **Grade A / Grade B**：A=不做全量停写、依赖检测与重读；B=窗口内无写者证明（更强声明，推荐用于 P7 A4）。
- **`sync barrier`**：最终 bracket 前的文件系统同步（root），把"已发出未提交"的写入在 generation 观测中显形；必须实现时审查权限与影响。
- **fail-closed**：任何不确定/异常 → 整批 unknown + 非零退出，不输出 `complete:true`，不把 unknown 降级。

## 7. Addendum：RO 根因与缓解（2026-11-07 新证据）

> **本节的定位**：§0–§6 是原设计原文，**未被改动**（本节只追加；N1 同步只改 §7 内文字，§0–§6 的字节保持与旧版一致，见 §7.8 的旧前缀哈希）。本节并入 Lead 于 **2026-11-07 10:02:59–10:04:01 CST** 由**两个探针脚本各自原子运行**（10:02:59–10:03:00 与 10:03:59–10:04:01）取得的 RO 根因/缓解新证据，回答"RO 子卷为什么可写、有哪些已实测可用的缓解"，并据此修订原设计中相关结论（T-A/T-B/T-C 状态、推荐组合、P7 A4 证据链、残余风险、失败注入/验收增量）。
> **边界不变**：本节不实现、不部署、不改任何门禁；`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须继续为 `False`，扫描器继续返回 `unknown`，§0.1/§0.2 与 P7 A4 仍 **BLOCKED**。本节也没有访问设备、没有复跑新实验（只是并入 RO 根因/缓解文档、generation 语义文档与 M3 纠正实验的可核实内容及本机已有证据）。
> **复审状态**：新证据已由独立复审 Round-2 判定 **PASS（无阻断；N1/N2 已修）**（报告 `docs/BTRFS_EVIDENCE_ROUND2_REVIEW_20261107.md`，50,508 B，SHA-256 `830ab5f9…`；F1/F2/F3 已修，N1/N2 文档同步项已修——本段即 N1 收口）。RO 实验的实际运行方式为**两个探针脚本各自原子运行（10:02:59–10:03:00 与 10:03:59–10:04:01）**，不是一次原子运行。即便如此，它仍只作为"证据"引用，**不当作独立来源、不当作授权**；旧结论与外推边界（§1.2、§0.1）继续有效。

### 7.1 本节绑定与新证据来源

| 项 | 值 |
| --- | --- |
| RO 根因/缓解文档 | `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md`，8,520 B，SHA-256 `4c01327466001e4cd89b3245bac25e60c9b92cde1d512672eb2399bf4171f842`（N1 同步时按磁盘实际重算；旧绑定 `91f65503…`/7,851 B 已作废） |
| generation 语义补证文档 | `docs/BTRFS_GENERATION_SEMANTICS_20261107.md`，13,780 B，SHA-256 `0c99d3ac9aae9dd958300fa0fbc2ccf9615951c91e00c58bd7bd61e4b20c07a3`（Round-2 复审范围；bracket 实现规格须含 `syncfs` barrier 与 `O_NOATIME`/`noatime`，见其 §8） |
| M3 纠正实验日志 | `docs/evidence/20261107/REFLINK_COW_CORRECTED.log`，1,529 B，SHA-256 `973b05eb4a1ea4a00aa8a2f02043b35ee4bdc1173a98ab775e30fd4c99ed8c55`（持久证据；替代旧 E4 的第二方向检查） |
| Round-2 复审报告 | `docs/BTRFS_EVIDENCE_ROUND2_REVIEW_20261107.md`，50,508 B，SHA-256 `830ab5f92d1a1205c8e8cf166fd20ab791b4fcfb58b53656893324e86c45add9`（PASS（无阻断）；F1–F3 resolved；N1/N2 已修） |
| 执行者/窗口 | Lead；2026-11-07 10:02:59–10:04:01（NAS 本地 CST），两个探针脚本各自原子运行（10:02:59–10:03:00 与 10:03:59–10:04:01），各自 `trap` 自清理 |
| 设备 | `kotomi`（fnOS 1.2.0903 / 内核 `6.18.18.c1168-trim` / `/vol1` btrfs `trimacl`；与 §1.2 E1 同机、时间更晚） |
| 实验脚本（文档 §1 绑定，本地重算一致） | `scripts/btrfs_ro_rootcause_probe.sh` = `d5074d637d8390857059424e71f072eae33d86ccf63ddbc3a2e71791481383db`；`scripts/btrfs_ro_mitigation_probe.sh` = `c7e513b3d2167558e65d0f36eb4a010c4daed6a5d697669e8a0b736bab6115a2`；`scripts/write_probe_helper.py` = `a6ce5b775652ebcc32b711601f74400f8ff182b13dcb04567c41ebf6ec289462` |
| 原始日志（文档 §1 绑定；`/tmp` 易失） | `/tmp/fnm-ro-rootcause.log` = `8cbbab2364cffe7e9b34d63789b5de7244cb381f6afb4e6ea2a06c8c7ae8cbad`；`/tmp/fnm-ro-mitigation.log` = `1a4b893c4f11dc14492a14419fa5bfe7827a045be2022c561b3527ed60aafb8b` |
| 证据边界（原样承接） | 仅本机；未定位 `trimacl`/`trimafs2` 补丁代码；E3b 以 root 执行（普通用户未单独实跑）；E4/纠正实验仅 8 MiB；E5 接收端在 `/tmp` loop 镜像（易失）；未做坏块注入、未做库级/大文件/失败恢复 |

**编号约定**：本节提到的 `新证据 E1/E2/E3a/E3b/E4/E5` 指新证据文档 §2/§3 的实验编号；原设计 §1.2 的 E1–E13 继续用"原 E1–E13"指代，避免混淆。

### 7.2 根因结论：`trimacl` 挂载选项绕过了只读子卷的写强制（新证据 §2）

| 实验（新证据） | 视图 | 操作 | 结果 |
| --- | --- | --- | --- |
| E1 | `/tmp` 内 512 MiB loop btrfs，挂载**无** `trimacl` | RO 快照内创建文件 | **`WRITE_REJECTED 30 Read-only file system`**（标准行为） |
| E2 | **同一 loop btrfs 镜像**，挂载**加** `trimacl` | 同一 RO 快照内创建文件 | **`WRITE_ACCEPTED`**，文件真实出现；`property get ro` 仍 `true` |
| E3a | `/vol1` 正常 RW 路径（`trimacl`） | 向 RO 快照直接创建文件 | **`WRITE_ACCEPTED`**（与主证据 §5.1 / 原 E3 一致） |
| E3b | 同一 `/vol1` 快照，**私有 mount namespace** 内以 `-o ro,subvolid=<id>` 只读挂载 | 向该只读挂载写入 | **`WRITE_REJECTED 30 Read-only file system`** |

**根因结论（仅本机，不得外推）**：同一内核、同一 loop btrfs 镜像，仅切换 `trimacl` 挂载选项即复现/消除"RO 子卷可写"；触发点是 **`trimacl` 挂载选项（fnOS 补丁/`trimafs2` 模块）**，**不是 Btrfs 文件系统本身**，也不是探针写法。未定位到具体补丁代码行；不得外推到其它 fnOS 版本/内核/挂载组合。

**对原设计的修订**：原 §1.3 的"RO 位不可依赖"**仍然成立且得到机制解释**——不可依赖的是"经 **RW 挂载 + `trimacl`** 的 RO 子卷写强制"；新增两条可操作结论：①**RO 挂载**（`-o ro`，M1）可恢复内核强制只读，但只对**该挂载视图**成立；②**独立副本**（M2/M4）根本不需要依赖源子卷的 RO 语义。原 §2.1 方案 A / §3.1 L2 中"RO 位不可作为不可变证据"的理由应更新为"根因是 `trimacl` 绕过，且 RW 挂载路径仍开放"，结论（必须叠加 bracket/重读）不变。

### 7.3 已实测缓解 M1–M4（新证据 §3）

#### 7.3.1 M1：RO 挂载（`-o ro,subvolid=`，私有 mount namespace 或受控全局挂载）

- **机制**：由 root 帮助进程在**私有 mount namespace**（或受控路径）把目标子卷以 `-o ro,subvolid=<id>` 只读挂载，扫描器只读该挂载视图。
- **实测**：新证据 E3b——同一 `/vol1` 快照经该只读挂载写入 → **`EROFS`**（即使选项串中仍显示 `trimacl`）。→ 内核强制只读在**挂载视图层**可恢复。
- **能支持什么**：让"扫描视图"恢复写强制，降低"视图被写"的概率；可作为原设计 L1 视图的强化层。
- **边界与成本**：**同一子卷经 `/vol1` RW 路径仍可写（E3a）** → M1 只保护视图本身，**不能**让"快照内容不可变"成为结论；必须叠加 generation bracket / 逐文件重读（原 §3.1 L2/L3 不变）。需要 root 帮助进程与 mount namespace/挂载生命周期管理（泄漏/清理/可见性）；E3b 以 root 执行（普通用户未单独实跑，预期同 EROFS）；不能防 W4（root/管理员/内核）；视图路径仍须防同 uid 可达写入之外的风险。
- **复杂度/成本**：中（新增 namespace/mount 管理，但无数据拷贝）。
- **对原设计修订**：M1 作为 §3.1 L2 的**可选强化**（视图层 RO），并在 §3.4 P-10 的视图目录权限之外新增"视图挂载方式"参数；**不得**因为 M1 恢复 `EROFS` 就省掉 bracket/live 重读。

#### 7.3.2 M2：`btrfs send` → `receive` 到独立 btrfs（无 `trimacl`）

- **机制**：对 RO 快照执行 `btrfs send`，把流 `receive` 到**独立 btrfs 文件系统/专用 scratch 卷（不挂 `trimacl`）**，得到新子卷；收到子卷设 `ro`（或经 `-o ro` 重挂载）后作为扫描视图。
- **实测**：新证据 E5——send 8,395,408 B；receive rc=0；收到子卷设 `ro` 后写入 **`EROFS`**；再以 `-o ro` 重挂载仍 **`EROFS`**；收到内容哈希与快照**完全一致**（`916ff831…`）。→ **独立物理存储 + 标准 RO 强制**在本机实证可用。
- **能支持什么**：目前**最强的独立视图**：接收树是**新分配 extent**（物理独立于源），且不依赖 `trimacl` 的 RO 行为；适合作为 P7 A4 的扫描载体（原设计 §2.3 方案 C 的升级）。
- **边界与成本**：
  - `btrfs send` **要求源子卷是只读快照** → M2 仍然需要先做 RO 快照（快照创建本身仍在本机 `trimacl` 下进行，已由主证据验证可行）；M2 不能免除"源快照在 send 期间稳定"的要求（源 RO 位不可依赖 → 仍需 bracket/短窗口 + 重试）。
  - **全量拷贝**（约等于库大小，实验 8.4 MB；未做库级/大文件吞吐测试）；需要 root 帮助进程（send/receive/建卷/清理）。
  - 本实验接收端在 `/tmp` 的 loop 镜像中（**`/tmp` 易失**、位于 ext4），**未验证持久 scratch 卷、未验证失败恢复/坏块**；生产化需要专用 scratch btrfs 卷 + 幂等清理与残留核验。
  - 接收树在 `ro` 设置前是普通可写子卷，设置后仍要防"root/RW 路径可写"（独立 btrfs 无 `trimacl` 时 RO 语义标准，但同一接收子卷若经 RW 挂载路径访问仍可写——沿用"路径/挂载隔离 + bracket"原则）。
- **复杂度/成本**：中–高（全量数据 + scratch 卷 + root helper + 清理）。
- **对原设计修订**：§2.3 方案 C 从"中/成本高/本机未验证"升级为"**本机已验证可行、RO 强制恢复、物理独立**；成为 P7 A4 首选独立视图候选之一"；§2.11 汇总矩阵 C 行的"本机 trimacl 可行性"从"中（未验证）"改为"**已验证（send/receive，无 trimacl）**；仍需库级/持久/坏块测试"。

#### 7.3.3 M3：reflink（FICLONE）副本

- **机制**：`cp --reflink=always` 把曲库目录树克隆到隔离目录（同一 `/vol1` 文件系统），扫描副本；克隆后对源/副本任一方的写入触发 COW，不影响另一方。
- **实测**：`cp --reflink=always` rc=0；`filefrag` 显示源与副本**同一物理 extent + `shared` 标志**（两文件首 extent 物理偏移完全相同：`694057586..694059633`）。双向 COW 以**纠正实验**为准（`docs/evidence/20261107/REFLINK_COW_CORRECTED.log`，SHA-256 `973b05eb…`，8 MiB；**每侧哈希在对方被修改之前取得**）：复制后 `H0=C0=938ce08b…`；改源后 `H1=14776329…`、`C1=C0`；再改副本后 `H2=H1`、`C2=e63df1de…` → **改源→副本不变、改副本→源不变（双向 COW 独立）**。旧 E4 的"改副本→源不变"比较因同一时刻读同一文件被复审 F2 判为同义反复，**已由本纠正实验替代，不再作为依据**。
- **能支持什么**：**廉价、COW 独立的稳定视图**（无数据拷贝，仅元数据/共享 extent）；适合快速抽检/差分（如重复位置、抽样文件）与原设计 §3.2 T-C。
- **边界与成本**：
  - **同文件系统共享物理 extent** → 坏块/底层损坏/raw 设备写会同时影响源与副本（原 R8 保留并细化）；**不提供物理独立性**。
  - **普通文件权限模型**（非 RO 语义）：副本可被 root/管线覆盖，需专用 root-only/受控目录 + 权限模型 + bracket；同 uid 可写问题仍在。
  - 仅 8 MiB 实测；未做**库级/大文件/配额/坏块**；原 §2.2 的 5 个限定（逐文件非整树原子、dirty page/delalloc 语义未验证、元数据窗口、共享 extent、副本可写）**全部继续有效**。
- **复杂度/成本**：低（近乎零拷贝）＋权限/清理设计。
- **对原设计修订**：§2.2 与 §3.2 T-C 中"**FICLONE 未验证**"更新为"**已在 `/vol1` 验证可用（8 MiB，双向 COW + shared extent）**"；但 T-C 仍**不作为 A4 最终证据**（同 fs 坏块 + 权限模型 + 非整树原子），只作抽检/差分。

#### 7.3.4 M4：普通拷贝到 ext4（`/tmp`）

- **机制**：把曲库目录树**普通复制**到独立 ext4（`/tmp` 位于 `/` 根 ext4），扫描副本；不需要任何 RO 语义。
- **实测**：新证据 §3 明确"本次**未单独重测**"；`/tmp` 为 ext4 是设计期已知事实（原 E11：`/` ext4 63G/可用 39G）。
- **能支持什么**：最简单、物理独立、无需 RO 语义的独立视图候选；适合作为 M2 的简化替代（若用户不建 scratch btrfs）。
- **边界与成本**：全量拷贝（时间/空间）；`/tmp` 在根 ext4，**易失且容量受限**（39G 可用，随库增长需评估）；普通文件权限模型（需 root-only/受控目录 + 权限 + bracket）；**本次无实测**（只能作为设计候选，需 P0 实测 + 独立复审）。
- **复杂度/成本**：低–中（无 send/receive 复杂度，但全量拷贝）。
- **对原设计修订**：原 §3.2 的 T-D 只谈 send/receive；现新增 **M4 独立视图路线**（普通拷贝到独立 ext4/独立存储），并在 §5.2 增加用户决定（D9）。持久证据建议落盘到受控**非易失**位置，`/tmp` 只作临时。

### 7.4 对拓扑 T-A/T-B/T-C 与推荐组合的修订（覆盖原 §3.2/§3.1 对应行；原论述不删除）

- **T-A（曲库独立子卷）**：拓扑价值不变（扫描根/条目预算/生成粒度最干净、快照创建/删除轻）。**新增注意**：即便曲库独立子卷，对快照的写入仍可经 `/vol1` RW 路径发生（E3a）→ 仍必须叠加 bracket + live 重读；M1 可给视图层 RO，但不改变"RW 路径开放"。
- **T-B（用户子卷快照 + 子树扫描）**：**结论加强**：新证据表明 RO 写强制失效的根因是 `trimacl`；T-B 必须叠加 generation bracket + live 重读（原设计已如此要求），可选 M1 私有 namespace `-o ro` 挂载给扫描视图**降低**视图被写概率，但**不得**据此省掉 bracket（E3a 显示 RW 路径仍可写）。
- **T-C（受控副本）**：**状态更新**——FICLONE 已在 `/vol1` 验证可用（M3/纠正实验 `REFLINK_COW_CORRECTED.log`）；T-C 从"需先验证 FICLONE"升级为"**可用候选（抽检/差分）**"；但仍受同 fs 共享 extent、权限模型、非整树原子限制，**不作为 A4 最终证据**，除非叠加独立副本（M2/M4）。
- **新增"独立视图路线"（M2 或 M4）**：无需 T-A/T-B 的快照+子树模式；流程 = 源 RO 快照（send 要求）→ M2 send/receive 到专用 scratch btrfs（无 `trimacl`）**或** M4 普通拷贝到独立 ext4/受控存储 → 扫描独立副本 → 账本指纹比对 → live 重读现存曲库 → 证据封存。**这是 P7 A4 的首选证据链**（新证据 §4 建议，且与原设计 L1–L5 兼容：L1 视图 = M2/M4；L2 bracket 仍用于源快照/接收树；L3 live 重读不变；L4 oracle 不变）。
- **推荐组合修订（原 §3.1 L1 优先级）**：由"快照优先、副本备选"改为"**独立视图（M2/M4）优先；RO 快照 + 子树（T-A/T-B，可叠 M1）次之；M3 抽检**"。理由：M2/M4 的 RO 强制/物理独立证据更强，且不依赖 `trimacl` 行为延续；M1 只恢复视图层 RO，RW 路径仍开放。**不变**：任何依赖原 `/vol1` 视图的方案必须叠加 generation bracket + 逐文件重读；generation 语义补证已有首轮证据（`docs/BTRFS_GENERATION_SEMANTICS_20261107.md`，Round-2 PASS；实现规格须为两端 `syncfs`/`sync` barrier + `O_NOATIME`/`noatime` 读取，见该文档 §8；仍不充分，不得依赖时间等待）。

### 7.5 P7 A4 证据链建议（修订版，覆盖原 §3.6/§3.3 的证据载体选择，其它参数不变）

1. **默认（推荐）**：**M2（send/receive 到专用 scratch btrfs）或 M4（普通拷贝到独立 ext4/受控存储）独立视图 + 账本 `published_sha256/size` oracle（L4）+ 逐位置 live 重读（L3）+ 内容哈希对比 + 证据封存（L5）**；M2 提供 RO 强制与物理独立，M4 提供简单性与 ext4 独立。选择取决于用户对 scratch btrfs/容量/持久性/root helper 的授权（见 D2 修订/D9/D10）。
2. **轻量视图**：**M1**（私有 namespace `-o ro` 挂载同一快照）用于快速扫描；**必须**叠加 generation bracket + live 重读，**不得单独作为不可变证据**（E3a）。
3. **抽检/差分**：**M3** reflink 副本用于快速抽检（重复位置/抽样文件）；不作为最终证据（同 fs 坏块 + 权限模型）。
4. **T-B 快照 + 子树扫描**：仍可用作 L1；必须叠加 bracket + live 重读（原设计不变）；不得因 M1 恢复 `EROFS` 就把 RO 位当不可变证明。
5. **A4 仍为多证据合成**：basename/位置对应、`ffprobe` 可解码/格式/实际音质/标签/封面/LRC/索引/播放仍须其它证据；扫描证据只覆盖字节一致性；A4 判据衔接仍需 D7。
6. **参数不变**：原 §3.4 的 generation 采样点/频度、stat 字段、sync barrier、重读范围、超时/上限、证据窗口/Grade A/B、fail-closed、单次重试、unknown 原因码（§3.5–§3.8）**全部继续适用**；M2/M4 的视图创建/内容哈希/RO 设置/清理纳入原 P0–P8 流程与 L1/L5 证据要求。

### 7.6 新增/更新的残余风险（追加到原 §5.3 的 R 表；原 R1–R12 不变）

| # | 风险 | 影响 | 缓解/边界 |
| --- | --- | --- | --- |
| R13 | `trimacl` 根因未定位补丁、未审计模块全行为 | 行为可能随 fnOS 版本/内核/挂载组合变化；本机结论不能外推 | 每次设备窗口重新验证（原 R9 加强）；禁止把本机根因当作平台承诺 |
| R14 | M1 视图 RO 但 **RW 路径仍开放**（E3a） | 依赖 M1 的方案若省掉 bracket/重读会误判不可变 | M1 只作视图层强化；必须叠加 generation bracket + live 重读 |
| R15 | M2/M4 全量拷贝的时间/空间成本；scratch 卷持久性/恢复；实验接收端在 `/tmp`（易失） | 验收窗口拉长/失败恢复困难；证据易失 | 专用 scratch 卷 + 幂等清理 + 残留核验；持久证据落盘到受控非易失位置 |
| R16 | M2 依赖 send 源快照在 send 期间稳定（源 RO 位不可依赖） | 源被写时 send 流可能不一致/失败 | 源 side bracket/短窗口/重试；未做库级/大文件/失败恢复/坏块注入，需 G8 补证 |
| R17 | M3 同文件系统共享物理 extent | 坏块/底层损坏/raw 写同时影响源与副本（原 R8 细化） | 需要物理独立时用 M2/M4；M3 只作抽检 |
| R18 | M3 普通文件权限模型（非 RO 语义），副本可被覆盖；仅 8 MiB 实证 | 视图被改/未测库级 | 专用 root-only 目录 + 权限模型 + bracket；库级/大文件补测 |
| R19 | M4 本次**未实测**，`/tmp` 容量/易失 | 不能作为"已实测"主张；容量随库增长 | P0 实测 + 独立复审；持久化到受控存储 |
| R20 | M1 的私有 mount namespace/受控全局挂载引入新的挂载生命周期/清理风险 | 挂载泄漏、命名空间管理复杂、root helper 复杂 | 实现时纳入清理/残留核验与独立复审；不进生产测试 |
| R21 | 新证据虽经 Round-2 独立复审 PASS（无阻断），但设备实验为单机单次窗口，且 generation 首次运行 stdout 未完整持久化（F1 已披露）；`/tmp` 原件易失 | 证据链强度不足以单独支撑门禁或跨窗口复现 | 持久副本已归档到 `docs/evidence/20261107/` 并绑定哈希；实现前仍需独立复审 + 每次设备窗口复验；不当作授权 |

### 7.7 对原 §4 失败注入计划与 §5 验收判据的增量

**§4（失败注入）新增/修订**：

- **§4.3 G1 原生代际语义验证**补充：①"经 M1 `-o ro` 挂载的视图内写入"与"经 `/vol1` RW 路径写同一子卷"的对照；②M2 接收树"设 `ro` 后写入 `EROFS`"、"`-o ro` 重挂载 `EROFS`"的复验；③M3 库级/大文件双向 COW 与坏块/底层损坏注入；④M4 全量拷贝完整性/容量/失败恢复；⑤仍必须做原 §4.3 的 generation 语义矩阵（不 `sync`/元数据/truncate/rename/ABA/RO 绕过写入后的推进时机）——首轮补证见 `docs/BTRFS_GENERATION_SEMANTICS_20261107.md`（`syncfs`/`sync` barrier、`O_NOATIME`/`noatime`、并发 writer 与 RO 绕过写入已有实测；跨运行 #14 差异已披露；仍需实现侧 fail-closed 与库级/长扫描补测）。
- **注入矩阵新增行**（在原 I1–I14 之后）：I15 经 M1 只读挂载写入 → `EROFS`（扫描视图写保护生效）；I16 同一子卷经 RW 路径写入 → 必须被 bracket/live 重读检出（M1 不能替代）；I17 M2 接收树 RO 强制与内容哈希一致性（含"接收树被 root 改写"→ unknown）；I18 M3 库级双向 COW（改源/改副本各自独立性）与 shared extent 断言；I19 M4 全量拷贝中断/容量不足 → fail-closed；I20 M2 scratch 卷清理失败 → 保留现场、unknown。

**§5（验收判据）新增**：

- **G8（M2/M4 独立视图端到端）**：独立视图创建（含源 RO 快照 + send/receive 或普通拷贝）→ 内容哈希一致性 → 接收树 RO 强制 → 扫描 → live 重读 → 证据 → 清理/残留核验 → 持久化与失败恢复；至少一次独立复跑。
- **G9（M3 抽检用途）**：库级/大文件双向 COW、shared extent、坏块/底层损坏测试；明确"仅抽检、不作最终证据"的边界写入证据 schema。
- **G1 补充**：`trimacl` 根因的版本绑定与"每次设备窗口重验"要求（新证据 §5 的不可外推边界）。
- 原 G1–G7 **不变**；G1–G9 全部满足 + 独立复审 + 新代码门置位前，扫描器仍 `unknown`。

**§5.2 用户决定增量**：

- **D2 修订**：新增"M2（scratch btrfs + send/receive）"与"M4（拷贝到独立 ext4/受控存储）"两条独立视图路线；需用户选择并授权 scratch 卷/容量/持久位置/root helper。
- **新增 D9**：是否授权 M2 所需的**专用 scratch btrfs 卷**与 `btrfs send/receive`（含幂等清理、失败恢复、残留核验）；是否接受全量拷贝成本；持久证据放哪里。
- **新增 D10**：是否值得实现 M1（私有 mount namespace `-o ro` 视图，root helper + 挂载管理），还是直接采用 M2/M4（更简单/更强独立性）。
- **新增 D11**：M4 的 `/tmp` 临时副本是否只作演练（不作验收证据），以及持久 bloom 证据的受控位置。

**§5.4 "仍 BLOCKED"不变**：新证据与本节**不改变**任何门禁；扫描器仍 `unknown`、A4 仍 BLOCKED、`PIPELINE_INTEGRATION_ALLOWED=False` 未变；M1–M4 只是候选**载体**，未接入扫描器、未实现；证据文档已由 Round-2 独立复审 PASS（限定复核），但 M1–M4 作为实现载体仍未实现、未复审。

### 7.8 本节证据边界与自校验

- 本节只并入新证据的可核实内容；**未执行**任何设备操作（无 ssh、无写探针、无复跑）。新证据已由独立复审 Round-2 判定 **PASS（无阻断）**（报告哈希见 §7.1）；其中 RO 实验为**两个探针脚本各自原子运行（10:02:59–10:03:00 与 10:03:59–10:04:01）**，不是一次原子运行。持久证据已归档到 `docs/evidence/20261107/`（含 M3 纠正实验日志），NAS `/tmp` 原件仍属易失副本；本节 N1 同步后的文本一致性需复审者确认。
- 新证据的边界（原样承接）：仅本机 fnOS 1.2.0903 / 内核 6.18.18.c1168-trim / `trimacl`；未定位 `trimacl`/`trimafs2` 补丁；E3b 以 root 执行（普通用户未单独实跑）；E4/纠正实验仅 8 MiB；E5 接收端在 `/tmp` loop 镜像、未验证持久 scratch 卷/大文件/失败恢复/坏块；不得外推到上游/其它版本。
- **§0–§6 未被改动**的证明：本文件旧版正文（§0–§6 + 附录 A–C）在旧自校验戳之前的**前缀哈希**为 `ae7bc37f5fbb37725005937c0efd1138d0f4f4012caf515a27a81fbf153b64ac`（旧版最终文件哈希 `bf9c91c2d01539ec48c05c867ebbc0eda45518f66ed482b3992a90b9fa6b0f68`，89,615 B）；**N1 同步只改 §7 内文字**，新文件开头 **89,481 字节**仍等于该旧前缀（复核：取新文件前 89,481 字节重算 SHA-256 = `ae7bc37f…`；本次同步后已复核通过）。本节自校验戳见文末 `DOC-HASH-STAMP` 行（定义：`sha256(该行之前全部字节, UTF-8)`；N1 同步后已重算）。
- 与原设计的冲突处理：原 §2.2/§2.3/§3.1/§3.2/§3.6 中关于"FICLONE 未验证""send/receive 成本高且未验证""T-C 状态""L1 优先级"的表述，按本节 7.3–7.5 的**修订**理解；其余原结论（RO 位不可依赖、必须 bracket/重读、fail-closed、A4 多证据、门禁不变）继续有效。§6 的"不实现/不改门禁/最小改动点/复审点"结论不变，并新增 G8–G9、I15–I20、D9–D11 作为后续实现/复审的增量。

<!-- DOC-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 96a7e092078f947998c1c19b1a3817d953cba025fbc3f7a5479cad03b55f40d7 -->
