# P1 验收证据 Schema 与权限/身份证据独立复审（2026-11-07）

> **总体 verdict：PASS-with-limits（无阻断项）。**
> - **P1 证据 Schema**：`docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md` —— **PASS-with-limits**。内嵌 schema 经独立工具校验为**合法 Draft 2020-12**（JSON 可解析、meta-schema 通过、`$ref` 全部可解析、无重复键），文档自哈希/自校验戳成立；硬规则与 fail-closed 立场写得清楚，且与 P7/账本/扫描器现状大体一致。但存在 4 个重要自洽性缺口（不变量与 schema 互斥、`claim_scope` 缺失、播放侧机器可读性不可表达、fail-closed 不变量覆盖不全）与 4 个次要缺口，**在实现前必须修正**；作为"草案"本身无阻断。
> - **权限模型证据** `docs/VOL1_PERMISSION_MODEL_FINDING_20261107.md` —— **PASS-with-limits**。核心结论（`/vol1` 上 POSIX mode 不作为本机安全边界；NAS 管理员近似 root；`www-data` 可读普通文件；非管理员被拒）由受控实验支撑，且**明确没有把管理员特权/platform ACL 写成平台漏洞**；未测项基本诚实。
> - **受信入口证据** `docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md`（含 §9 更正）—— **PASS-with-limits**。外部无认证被 nginx/安全码层拦截、`trim_http_cgi` token 门、扩展网关信任 `X-Trim-*` claim（R1 应用层）、重复 identity 头 fail-closed、L1 对管理员不可靠/L2 为实际边界等结论与本地代码/证据一致；§9 更正是准确的。残留：§4/§6 仍留有更正前的 L1 表述、§6 未知项 #3 已过时、部分"观测 vs 代码预期"混写、部署版 `webui_gateway.py` 源码无法本地复核。
> 本次复审**不改变** P7 A4/A5/A6/A13/A16 的 NOT RUN/BLOCKED、§0.1/§0.2、`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、`_POINT_IN_TIME_SCAN_VALIDATED=False` 或任何部署门禁。本复审者未参与被审文档撰写；**未访问设备、未运行设备采集命令**，只新增本报告。

## 0. 抬头、对象与范围

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（JST 11:2x–11:4x） |
| 复审者 | `final-reviewer`（未参与被审文档撰写） |
| 被审文档 1 | `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md`，93,052 B，SHA-256 `38df19de43ab909082cfc65cca485ce89500bc12213b7cb399fdd3d492e20a4f`；内嵌 `DOC-HASH-STAMP` 自校验值 `271e79de…65b2`（已验证） |
| 被审文档 2 | `docs/VOL1_PERMISSION_MODEL_FINDING_20261107.md`，5,897 B，SHA-256 `6473c1af0cb3f8c0317ade72eb82ef832e891d199ba1be503fbddde03929b5bb` |
| 被审文档 3 | `docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md`，9,345 B，SHA-256 `286e70c8aceba63c2287af6b344e8adcf307cb0fb80a775361d4522150a74b5e` |
| 复审方式 | 本地只读：重算哈希/自校验戳、抽取并独立校验内嵌 JSON Schema、逐条读文档与日志式证据、对照本仓库代码/既有设备证据文档；未访问设备、未执行采集、未改被审文档/代码 |
| 不在范围 | 不实现/不部署 schema 或采集；不重做权限/入口实验；不对 `trimacl`/`trim_http_cgi` 做逆向或安全审计；不评估"音频听感/权益/合法性"；不改变任何门禁 |

## 1. P1 验收证据 Schema 复审

### 1.1 结构与绑定（独立重算）

| 检查 | 结果 |
| --- | --- |
| 文档自身 SHA-256 | `38df19de…20a4f` = 任务声称 ✓（93,052 B） |
| 文末 `DOC-HASH-STAMP` | `sha256(该行之前全部字节)` = `271e79de7fa76554fa3914bb2c4cbcf015b60558b126aaffc711d2667ec265b2` = 文档声称 ✓ |
| 内嵌 JSON Schema 抽取 | ```json 围栏（第 224–584 行）共 21,714 字符；**JSON 解析通过**，无重复键（`object_pairs_hook` 审计） |
| `$schema` / `$id` | `https://json-schema.org/draft/2020-12/schema` / `urn:fnmusic:p1-acceptance-evidence:0.1-draft-20261107` ✓ |
| **Draft 2020-12 meta-schema** | 使用独立工具 `jsonschema.validators.Draft202012Validator.check_schema` 校验：**PASS**（本地复审工具环境安装；非随仓库依赖） |
| `$ref` 解析 / `required`-属性一致性 | 全部 `$ref` 指向本地 `$defs`、可解析；未发现 `required` 指向不存在的 `properties` ✓ |
| schema 规模 | 顶层 8 键、`$defs` 15 个；position 必填 12 字段；基础实例 `{}` 不通过（证明 required 生效） |

**输入文档绑定（§1.2，写作时声称）**：13 份文档中 **11 份**的 SHA-256+大小与本地重算一致（runbook/NETEASE delivery/metadata/guard/OFFLINE_* /SOURCE_EVIDENCE/JOB/IMPORT 等）；**2 份已过期**（因写作后其他任务继续修订）：

| 文档 | §1.2 声称 | 本地当前 | 判定 |
| --- | --- | --- | --- |
| `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` | 111,567 B `7f4304e8…` | 114,069 B `47331bea…` | **过期**（task-16 正在修订 §7；复审时该文件可能仍在变） |
| `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` | 7,851 B `91f65503…` | 8,520 B `4c013274…` | **过期**（F3/N2 修正后） |

代码绑定：§1.2 所列 5 个代码文件哈希**全部与当前工作树一致**（`manual_save_route.py 07dc7ce2…`、`manual_save_playlist.py a9f1c4af…`、`manual_save_jobs.py a17e3a62…`、`manual_save_playlist_jobs.py a3116d30…`、`webui_gateway.py 1b07f815…`）——但注意这是**仓库候选**，不是安装版本（安装版对应哈希见 §1.3）。

### 1.2 14 条语义不变量的一致性审计

| 不变量 | 与 schema/判据一致性 | 结论 |
| --- | --- | --- |
| INV-1 published⇒delivered 非空、sha/size 独立重算；byte_scan 非 not_run/unknown 或有 limitations | 与 §2.3/§2.4 方向一致；schema 未把 delivered 设必填（由不变量兜底，合理） | 基本自洽（limitations 内容无强制） |
| INV-2 pass⇒默认 gate（bytes/quality/tags/duplicate）pass；cover/lyric/index_play advisory 不得当 pass | **与 §5.1 冲突**：§5.1 要求 position pass 时 `index_play` 必须 `pass`，而不变量把 index_play 排除在默认 gate 外、只禁止 advisory 当 pass；cover/lyric 是否 gate 又推给 Q5 | **不一致（S1）** |
| INV-3 fail⇒至少一个 `dimensions.*=fail` 且 fixed_reasons 非空 | **与 §5.3 P4 冲突**：P4 的"预期失败位置"（state=rejected，quality/bytes=not_applicable，position verdict=fail）没有任何维度 fail，按 INV-3 不成立 | **不一致（S1）** |
| INV-4 reported_status=reported⇒reported_level 非空+正则；反之空 | 与 §2.4.2 表一致；schema 未强制（不变量兜底，合理） | 自洽 |
| INV-5 不得由 tier_requested/format/bit_rate 推导 reported_level | 与 §3.1 硬规则一致；可由测试实现 | 自洽 |
| INV-6 level_in_ladder=false⇒quality 至多 unknown | 与 §3.1 UNKNOWN 判据一致 | 自洽 |
| INV-7 cover embedded/preserved⇒delivered.cover.embedded=true 且 decode_ok=true | schema 中 `decode_ok` 非必填（可为 null），不变量要求 true；属可测语义，合理 | 自洽（schema 弱） |
| INV-8 lyric published⇒kind=sidecar+严格 UTF-8 | schema `encoding` 允许 other/unknown，由不变量兜底；合理 | 自洽 |
| INV-9 源 cover/lyric 缺失⇒不得 pass | 与 §3.3/§3.4 一致 | 自洽 |
| INV-10 duplicate_of_position≠null⇒duplicate 必须给出 `conflict_expected`/`distinct_basenames`/`unresolved` 裁决 | **schema 的 `dimensions.duplicate` 枚举只有 `pass/fail/unknown/advisory/not_applicable`，且无任何字段可承载这三个裁决值/`distinct_basenames`** → 按 INV-10 填写的记录无法通过 schema，二者互斥 | **不一致（S1）** |
| INV-11 index.machine_readable=false⇒至多 unknown；playback advanced⇒position_advanced>0 且 duration_match≠mismatch | **与 §3.7 PASS 不一致**（§3.7 要求 machine-readable 观测、时长在容差内 `match`、无 error；INV-11 允许 duration_match=unknown）；且 playback 在 schema 中**没有 machine_readable/method 字段**（见 S3） | **不一致（S1/S3）** |
| INV-12 ref 的 sha/bytes 与 manifest 一致；custody=reviewed 须来自独立复审 | 与 §2.7 一致 | 自洽 |
| INV-13 record_sha256 对"规范化记录"重算；manifest 覆盖全部 refs | 方向正确，但"规范化"**未定义规范字节形态**（见 S6） | 部分自洽 |
| INV-14 blocked 仅用于外部门禁/前置 | 与 §5.1 一致 | 自洽 |

### 1.3 硬规则落实与 fail-closed 审计

| 硬规则 | 文档落实 | schema 能否强制 | 判定 |
| --- | --- | --- | --- |
| 扩展名/码率/请求档/URL/`type`/`br`/`sr` 不得当档位证据 | §3.1 Q-E1..E6、§5.2 X1–X4（逐条列出禁止来源） | 不能单独强制（需 INV-5 类语义测试；G-X3 未做） | 设计守住；**未机器强制** |
| 截图/UI 不得当档位/索引/播放证据 | §1.5、§2.4.5、§3.7、§4.5.5、§5.2 X11、判定矩阵 | index 有 `machine_readable`；**playback 无该字段**；`limitations` 含固定串未用 `contains` 强制 | **部分守住（S3）** |
| provider-reported 与独立验证区分 | §1.5 claim/observation/binding 三层；`claim_scope` 固定语义；§5.3 P1 样例标注 | schema **无 `claim_scope` 字段**（文档自称"建议新增"），且 `additionalProperties:false` 使该标注无法写入 | **文档区分清楚，但 schema 不可表达（S2）** |
| unknown/失败 fail-closed | §2.1.7、§5.1、INV-1/3/14；`overall_verdict` 枚举 | 方向正确；但缺多项一致性不变量（见 S4） | 基本守住，**不变量覆盖不全（S4）** |
| 不把"schema 校验通过"当证据通过 | §1.3、§2.5 注、§7 | — | 诚实 ✓ |
| 与 P7/账本/扫描器现状一致 | 见下表 | — | 大体一致 ✓ |

**与 P7/账本/扫描器一致性（本地核对）**：
- P7 A4 当前 `BLOCKED / NOT RUN`（runbook A4 第 70–81 行）✓；A5 重复位置、A6 失败可见、A13 脱敏、A16 `commit_unknown` 冻结语义与 schema 的 failure/verdict/redaction/blocked 设计方向一致 ✓。
- 账本 detail 白名单：`proxy/manual_save_jobs.py` `_DETAIL_LEGACY_KEYS` 10 键 + `published_sha256/published_size` = 10+2 ✓（与 §1.2 声称一致）；无 basename/track/disc/md5/路径 ✓；`job_id` 为 `secrets.token_hex(24)` 48 hex（与 schema `job_id` 模式一致）✓；`manifest_sha256` 存在；`source_fingerprint` 在 playlist 合同/代码中（`collection_version = "sha256-"+fingerprint`），schema 的 job 字段需从 manifest/collection_version **派生**（见 S8）。
- `published_sha256/size` 语义：route 代码注释明确"是对送入 staging 的 tagged 字节流的指纹，不是落盘回读"（`manual_save_route.py:768-801`）✓ 与文档一致。
- 扫描器：当前 `scripts/verify_position_file_evidence.py` 仍 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、新增 `_POINT_IN_TIME_SCAN_VALIDATED=False`，硬门未解除、返回 unknown/exit 2 ✓（文档 §4.4/§6.3 一致）。
- 安装版本 2.10.2 **不含** P1/P2/P4/P5/P6 候选代码（DEVICE_INVENTORY §2.3）✓ 与文档 §6.5"候选未部署"一致。
- 但 schema §1.2 的两份文档哈希已过期（S7），说明"写作时绑定"不再等于"当前状态"；定稿前需重绑。

### 1.4 采集假设/过度声明审计

- **诚实处**：§1.3/§7 明确"草案/未实现/未执行/未复审/不改变门禁"；§6.1 逐项列出 G-Q1/Q2/Q3/Q4、G-T1/T2、G-C1、G-L1、G-D1、G-F1、G-I1/I2、G-X1..X3 等实现缺口；§5.2 X1–X16 反例表；"不得把 schema 通过当证据通过"。
- **可能无法实现的假设（需在实现前澄清）**：
  1. `environment.deployed.git_head` 为**必填** 40-hex；安装树/FPK 是否携带构建 commit 未证明（DEVICE_INVENTORY 列出的安装树内容未见 `.git`；FPK 由 `/tmp/build/head` 构建）。若无构建元数据，该字段无法诚实填充（应改为 nullable + `build_ref`/unknown，或从 FPK 构建信息取得）。见 S8。
  2. `job.source_fingerprint` 在账本表无直接列（需从 `collection_version` = `sha256-<fingerprint>` / manifest 派生）；文档 §2.2 写成"账本绑定"，应写明派生路径。见 S8。
  3. `record_sha256`/manifest 的"规范化"未定义 canonical JSON 规则 → 不同实现会得到不同摘要，跨实现不可复核。见 S6。
  4. 索引/播放机器可读观测、逐档 attempts、源标签 track/disc、源封面/歌词字节等均需产品改动/新授权；文档已列为缺口（G-I1/I2、G-Q1/T1/C1/L1），**不是过度声明**，但不能当作现有能力。
- **未发现**把未验证能力写成"已可用"的过度声明；schema 的"完整证据模型"声称与"草案"定位一致。

### 1.5 P1 Schema 编号发现

**S1（重要）14 条不变量与 schema/判据存在 4 处互斥或阈值不一致**：INV-10 的裁决值不在 schema 枚举内且无字段；INV-3 与 §5.3 P4"预期失败位置"冲突；INV-2 与 §5.1（index_play 必须 pass）冲突；INV-11 与 §3.7（时长必须 match、播放须机器可读）不一致。→ 按当前不变量实现的记录可能无法通过 schema，或按 schema 生成的记录违反不变量；实现前必须统一。
**S2（重要）`claim_scope` 未进入 schema**：§3.1 说"建议新增字段"、§5.3 P1 样例要求标注 `provider_reported_not_independently_verified`，但 schema `source_evidence.quality` 是 `additionalProperties:false` 且无此字段 → "provider-reported vs 独立验证"的关键边界在 schema 层不可表达，样例本身不是合法实例。建议定稿前加入（如 `claim_scope` enum + §5.3 样例同步）。
**S3（重要）"截图/人工视觉不得当播放证据"无法结构化落地**：`index_play.playback` 没有 `method`/`machine_readable` 字段，INV-11 只约束 index 的机器可读性；`index_play.limitations` 要求含 `cannot_prove_audible_output` 但 schema 无 `contains` 约束。建议 playback 对称加入 method/machine_readable，并给 limitations 加 `contains: {const: cannot_prove_audible_output}`（或把该串并进 invariant）。
**S4（重要）fail-closed 不变量覆盖不全**：缺少以下可机器测试条款——position 集合唯一；`summary.pass+fail+unknown+blocked == positions_total` 且 `overall_verdict` 与计数一致；`job.positions_total == len(positions)`；`state≠published ⇒ failure` 非空；`quality.fallback=true ⇒ attempts` 完整；`failure.code`/`fixed_reasons` 属于固定码集合；`byte_scan.profile≠none ⇒ scanner/view` 具备；playback 机器可读。文档说"JSON Schema 表达不了，必须另测"，但该"另测清单"本身不完整；G-X3 仍"未做"，当前无机器保障。
**S5（次要）schema 结构缺口 / 文档-模式不一致**：`byte_scan.view` 仅 `type: object`（§2.4.4 的 kind/identity_ref/treeid/generation/uuid/parent_uuid/flags 等字段完全不校验）；`scanner` 与 `view` 均可为 null 使 `profile=independent_view` 缺少一致性约束；`delivered_evidence` 未 required `basename_ref`（§2.4.3 要求提供）；`bits_per_raw_sample` `minimum:1` 可能拒绝真实 ffprobe 的 0（建议最小 0 或 null）；`container_claim.api_type` 枚举含 `other/unknown` 而 §2.4.2 只写 `flac/mp3/""`。
**S6（次要）摘要规范未定义**：`record_sha256` 的"规范化 position 记录"、evidence manifest 的 canonical 字节生成规则（排序/编码/是否含 ref 行本身）未定义 → 跨实现不可复核；建议明确 canonical JSON（如 I-JSON：UTF-8、键排序、无 NaN、固定分隔符）。
**S7（次要）输入绑定 2/13 已过期**：`SCAN_CONSISTENCY_REDESIGN_20261107.md`（声称 111,567/`7f4304e8…`，现 114,069/`47331bea…`；且复审时正被 task-16 修订，可能继续变化）与 `BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md`（声称 7,851/`91f65503…`，现 8,520/`4c013274…`）。定稿前应重新绑定/复核；代码绑定 5/5 当前仍一致。
**S8（次要）采集假设未澄清**：`environment.deployed.git_head` 必填但安装树/FPK 是否含构建 commit 未知（无本地证据；见 1.4）；`job.source_fingerprint` 需从 manifest/collection_version 派生（账本无直接列）——二者都应在 schema 定稿时明确"可空/派生规则"，否则可能诱导伪造值。

### 1.6 P1 Schema 判定

**PASS-with-limits**：schema 本身合法、自绑定、硬规则与 fail-closed 立场设计良好、与 P7/账本/扫描器现状大体一致、缺口披露诚实；但 S1–S4（重要）使"按此 schema + 不变量实现"仍不可直接施工，S5–S8 需收口。无阻断（草案定位准确、未声称已实现/已验收）。

## 2. 权限/身份证据复审

### 2.1 权限模型结论与证据一致性

| 结论（文档） | 证据（文档摘要） | 与本地既有证据/代码的一致性 | 判定 |
| --- | --- | --- | --- |
| `/vol1`（trimacl）上 VFS/POSIX mode 不是可靠本地安全边界 | kurumi（NAS 管理员）读/追加 root:600 文件均 ACCEPTED；0700 目录内建文件 ACCEPTED；www-data 读 root:600 ACCEPTED；nobody/trim-media EACCES；`/tmp` ext4 同权限对照 REJECTED | 与既有 BTRFS/权限证据方向一致；实验设计有对照、有非管理员拒绝 | 符合（本机） |
| 管理员可连 root:600 socket；www-data 被 EACCES | `/vol1/.../test-600.sock` kurumi connect+send/recv 成功；www-data EACCES；`/run/trim_http_cgi.socket` 0770：kurumi EACCES、www-data ACCEPTED（符合 0770 组） | 与 L1/L2 代码语义一致；设备事实（socket 600 root:root、pid 1870256）与 DEVICE_INVENTORY 一致 | 符合（本机） |
| 扩展网关 socket 0600 root:root 挡不住管理员，但 L2 拒绝 | kurumi connect ACCEPTED → 随后 ECONNRESET、无 HTTP 响应（fail-closed）；www-data EACCES；root 连接无 header → 403 | 本地 `gateway_identity.evaluate_channel` 对不在 allow-list 的 uid 抛 `peer_uid_not_allowed`；`_admit_peer` 调 `read_peer_credentials`+`evaluate_channel`；默认 allow `{0}`（G0）→ 与"拒绝 admin peer"一致 | 符合（代码层面；运行观测仅"重置"） |
| 不能把 mode/socket mode 当授权边界；应改为 L2 + 上游 token + 头剥离/覆盖 | §3/§4 | 与 G0 L1–L5 分层一致；§9 更正准确 | 符合 |

**"管理员特权/platform ACL 是否被误写成平台漏洞"**：**未误写**。文档开头即声明"不是安全审计结论，不证明 fnOS 存在跨用户漏洞，也不排除管理员/服务账户属于产品设计内特权"；§3.2 明确"可能是 fnOS 设计……不能区分设计特权与 ACL 缺陷"；§6 要求官方文档/独立审计才能用于安全结论。**结论定性正确**。

### 2.2 受信入口结论与证据一致性

| 结论 | 证据摘要 | 本地核对 | 判定 |
| --- | --- | --- | --- |
| 外部未认证请求在 nginx/安全码层被拦截 | curl `https://nas:11026` / `/app/...` 返回安全码页（200、`x-trim-safe-code-challenge`、Content-Length 8893） | 与 DEPLOY_GATE_READONLY_EVIDENCE 的 nginx 配置/入口描述一致 | 符合（单路径观测） |
| `/app/` 由 nginx 转发到 `trim_http_cgi`；**未显式清除 X-Trim-*** | 配置片段（`location /app/` 仅设置 Upgrade/Connection/Host）；未出现头清理 | 配置引用与 DEPLOY_GATE_READONLY_EVIDENCE 引用的配置一致 | 符合（配置层面） |
| trim_http_cgi 以 token 判会话；无 token 时伪造 `X-Trim-*` 无效 | 无头/伪造头均 `invalid token`（13 B）；页面路径同 | 与 G0 L3/L4 设计及设备文档的 strings 证据一致（`X-Trim-Isadmin/Userid/Username` 字符串） | 符合（无有效会话路径） |
| 扩展网关信任 `X-Trim-*` claim（root 直连 200） | 无头 403；伪造三头+CSRF 头 200 返回 enabled/target 等；重复头 fail-closed（代码 `DUPLICATE_IDENTITY_HEADER`） | 本地 `_manual_save_authorized`：CSRF `x-fnmusic-save:1` + canonical admin claim + user-id；与文档 §8"写路由另有 CSRF/claim 校验"一致；root 直连伪造头 200 与 DEVICE_INVENTORY §9 烟雾一致 | 符合（应用层） |
| R1 在应用层成立：单条伪造 claim 若原样到达 socket 即被视为管理员 | 条件句；§6/§8 明确"未验证有效会话下的头剥离/覆盖"、未测试写路径 | 逻辑成立且边界诚实；但"若原样到达"依赖上游行为（未验证） | 符合（条件性结论） |
| L2 为实际边界（fail-closed） | 观测：非允许管理员 connect 后被重置、无响应；代码：`evaluate_channel` 仅允许内核 peer uid∈allow-list | 本地 `gateway_identity.py` 哈希与部署一致（`860e9309…`）；`webui_gateway.py` 部署哈希 `48b51e93…` 与仓库候选 `1b07f815…` 不同（见 M3） | 基本符合（见 M3） |
| §9 更正：L1 socket 0600 对管理员不可靠；L2 才是边界 | 引用权限实验 | 与权限文档结论一致 | 符合 |

### 2.3 L1/L2 边界表述准确性

- **§9 更正准确**：L1"0600 ⇒ 仅 root 可连"在本机**不成立**（管理员可连），对非管理员仍有效；L2（SO_PEERCRED + allow-list）是实际边界。与权限实验一致。
- **残留（M1）**：§4 仍写"安全性完全依赖：L1 socket 权限（**仅 root 可连**）+ L2 peer allow-list（仅 root）+ 上游…"，§6 第 4 点仍以"socket 权限 0600 root:root"作为安全项，均未在正文处标注 §9 更正；单独读前文会沿用已被 §9 推翻的 L1 说法。建议在 §4/§6 加"（见 §9 更正）"或就地修正。
- **L2 表述应补边界**：allow-list 默认 `{0}` 意味着**信任本机所有 root 进程**（G0 §6 R3），且本次只实测了 1 个非允许 uid（1000）；"真正可依赖的边界"应写作"相对非允许 uid 的边界"而非绝对安全边界（M4）。

### 2.4 未测项与证据缺口

**文档已诚实列出的**：权限文档 §6（trimacl/trimafs2 规则未审计；其它管理员/AD/LDAP 未测；真实曲库未读未写；容器内进程未测；需官方文档/独立审计）；入口文档 §6（有效会话下头剥离/覆盖未测——唯一关键未知；真实认证链路未测；非 root 本地进程拒绝未单测；安全码/2FA 关系未测；未审计二进制）。**基本诚实**。

**仍可补充的未测/推断项**：
- 权限实验：只测了 1 个管理员（kurumi）与 www-data；`nobody`/`trim-media` 只测普通文件读、未测 socket connect；`www-data` 未测对 root 文件的**写/追加**（测了 0700 目录创建被拒）；其它管理员/AD/LDAP/SMB 映射、容器内 uid、ACL 继承规则未测；`.env`"可被管理员与（若 ACL 类似）www-data 读取"是**未读取的推断**（有 hedge，但应标 unknown）。
- 入口文档：`nignx 默认转发所有客户端头` 是**配置/常识推断**，本次未做运行时头捕获（§6 已把"有效会话下转发结果"列为未知，但 §2 的表述宜标注"配置层面"）；写路径/CSRF 未测仅写在 §8，未进 §6 unknown 清单；`trim_http_cgi` 的"注入/覆盖"仅 strings 静态线索（§5 已诚实标注）。

### 2.5 权限/身份编号发现

**M1（次要）** 入口文档 §4/§6 残留更正前 L1 表述（"仅 root 可连"/"socket 0600 root:root"），未就地指向 §9；建议加交叉引用或修正，避免断章取义。
**M2（次要）** 入口文档 §6 未知项 #3"非 root 本地进程的实际拒绝未实跑"已过时：权限实验已观测 `www-data` EACCES、`kurumi` connect+重置；应更新未测清单（剩余：其它 uid/AD/容器、日志行、有效会话头路径）。
**M3（次要）** "已部署网关 `_admit_peer`/`evaluate_channel` 对非允许 uid **返回** `peer_uid_not_allowed`"混写了代码预期（固定码）与本次观测（连接重置、无 HTTP 响应）；未见日志行引用。且部署版 `webui_gateway.py`（`48b51e93…`，与 DEVICE_INVENTORY 安装树一致）与仓库候选（`1b07f815…`）不同，`_admit_peer` 文本无法本地复核；`gateway_identity.py`（`860e9309…`）与部署一致，L2 核心语义可复核。建议：观察与代码/日志分开表述，绑定部署哈希与其来源证据。
**M4（次要）** 边界表述的覆盖不足：L2 信任所有 root peer（G0 R3）；仅 1 个管理员/1 个非管理员实测；`nobody`/`trim-media` socket、`www-data` 写文件、其它管理员/AD/LDAP/容器未测；`权限文档标题`"POSIX mode 不是本地安全边界"未带 `/vol1` 限定（正文有）；`.env` www-data 读取是推断。建议补入 §6 并给 L2 加"相对边界"限定。
**M5（次要）** 入口文档 §2"nginx 默认转发客户端请求头"属配置推断（未运行时捕获）；写路径/CSRF 未测应列入 §6 unknown；R1 结论应始终保留"若上游未剥离/覆盖则…"的条件式（当前 §6 已条件化，§4/§6 建议同步）。

### 2.6 权限/身份判定

**PASS-with-limits**（两份文档分别 PASS-with-limits）：
- 权限文档：核心结论由对照实验支撑，明确区分"设计特权 vs ACL 缺陷"、**未把管理员特权写成平台漏洞**，未测项基本诚实；无阻断。残留为边界覆盖/推断标注（M4）。
- 入口文档：核心结论与本地代码/既有设备证据一致，§9 更正准确；无阻断。残留为更正前表述未就地标注（M1）、未测清单过时（M2）、观测与代码预期混写（M3）、推断/边界表述（M5）。

## 3. 解除/未解除清单

**在本复审范围内解除（声称已核实）**：
1. Schema 文档自哈希与 `DOC-HASH-STAMP` 成立；内嵌 JSON Schema 是**合法 Draft 2020-12**（独立 meta-schema 校验通过、JSON 合法、无重复键、`$ref` 全解析）。
2. Schema 的硬规则（扩展名/码率/请求档/URL/type/br/sr、截图不得当档位/播放证据、provider-reported 与独立观测分离、unknown/fail-closed）在**设计文字与判据**中明确；缺口披露诚实（G-*）。
3. Schema 与 P7/账本/扫描器现状**大体一致**：A4 BLOCKED/NOT RUN；账本 detail 10+2 键、无 basename/track/disc/md5/路径；`job_id` 48 hex；`published_sha256/size` 语义为 tagged 字节流；扫描器双硬门 False、unknown；安装版 2.10.2 无候选。5 个代码哈希与本地一致。
4. 权限结论：本机 `/vol1` 上管理员可绕过 POSIX mode（读/追加/建文件）、`www-data` 可读普通文件、非管理员被拒、`/tmp` 对照正常——有受控对照，结论与既有证据一致；**未声称平台漏洞**。
5. 入口结论：外部无认证拦截、`trim_http_cgi` token 门、扩展网关信任 claim（R1 应用层）、重复 identity 头 fail-closed、L1 对管理员不可靠/L2 为边界——与本地代码/设备证据一致；§9 更正准确。
6. 两份权限/入口文档未测项基本诚实（关键未知=有效会话下头剥离/覆盖未验证）。

**未解除（不得据此暗示）**：
1. **P7 A4/A5/A6/A13/A16、§0.1/§0.2 全部仍 BLOCKED/NOT RUN**；schema 是草案，未实现、未执行、未复审通过、未部署；不得把本复审当作验收/授权。
2. Schema 的 S1–S4 重要不自洽（不变量 vs schema、claim_scope、playback 机器可读、fail-closed 不变量覆盖）**未修复前不具备施工资格**；S5–S8（结构缺口/canonical 摘要/绑定过期/采集假设）需收口。
3. Schema 所需的新采集能力（逐档 attempts、源 track/disc/lyricist/composer、源封面/歌词字节、basename/duplicate、索引/播放机器观测）**均未实现**（G-Q1/Q2/T1/T2/C1/L1/D1/I1/I2）。
4. 权限/入口的**唯一关键未知**（有效会话下 `trim_http_cgi` 是否剥离/覆盖客户端 `X-Trim-*`）未测；写路径、CSRF、真实认证链路未测；`trimacl`/`trimafs2` 规则未审计；其它管理员/AD/LDAP/容器未测；真实曲库/.env 内容未读。
5. 部署版 `webui_gateway.py` 源码无法本地复核（哈希不同）；入口实验的 nginx/二进制/进程事实为设备侧观测，本复审未重采。
6. 本复审**未**做安全审计/漏洞结论，不改变任何门禁。

## 4. 复审者操作与限制

- 只读本地：读取被审文档/代码/既有证据文档，重算哈希与自校验戳；用本地安装的 `jsonschema`（`Draft202012Validator`）校验内嵌 schema；用 Python 做 JSON 重复键/`$ref`/required 审计；未访问设备、未执行任何设备命令、未改被审文档/代码；仅新增本报告。
- 无法核验：设备现场观测本身（nginx/trim_http_cgi/进程/socket/权限实验）无法在本复审复核，只能与本地既有证据文档/代码交叉一致性；部署版 `webui_gateway.py` 文本不可得；`trim_http_cgi` 完整行为未审计。
- 本轮复审窗口内 `SCAN_CONSISTENCY_REDESIGN_20261107.md` 正被另一任务修订，其哈希可能继续变化（S7 已注明）。

## 5. 附录

### 5.1 本轮重算的关键哈希

| 对象 | SHA-256 | 大小 |
| --- | --- | --- |
| `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md` | `38df19de43ab909082cfc65cca485ce89500bc12213b7cb399fdd3d492e20a4f` | 93,052 B |
| `docs/VOL1_PERMISSION_MODEL_FINDING_20261107.md` | `6473c1af0cb3f8c0317ade72eb82ef832e891d199ba1be503fbddde03929b5bb` | 5,897 B |
| `docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md` | `286e70c8aceba63c2287af6b344e8adcf307cb0fb80a775361d4522150a74b5e` | 9,345 B |
| schema 内嵌 JSON 块 | 21,714 字符；JSON 解析 ✓；Draft2020-12 meta-schema ✓ | — |
| 代码绑定（§1.2，5/5 匹配） | `manual_save_route.py 07dc7ce2…`、`manual_save_playlist.py a9f1c4af…`、`manual_save_jobs.py a17e3a62…`、`manual_save_playlist_jobs.py a3116d30…`、`webui_gateway.py 1b07f815…` | — |
| 过期绑定（2/13） | SCAN 文档现 `47331bea…`/114,069 B；BTRFS_RO 文档现 `4c013274…`/8,520 B | — |

### 5.2 本报告自身哈希说明

- 本报告写入完成后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REPORT-HASH-STAMP` 行给出 `sha256(该行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 3410b42713e8a8f45e8eab4a42f4222d731afc118159c8d684eb0ddf8295f8fd -->
