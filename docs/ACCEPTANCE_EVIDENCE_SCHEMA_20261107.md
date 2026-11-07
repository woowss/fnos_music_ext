# P1 验收证据机制 · 证据 Schema 设计与采集草案（草稿）

> **状态：草稿 / 提案，未实现、未执行、未部署。** 本文定义 P7 真实音频验收（A1–A16，核心 A4/A5/A6）所需的**逐位置、可机器校验**证据 schema 与采集方法；目的是让实现者/复审者有可施工的字段、判据与缺口清单。**本文件不执行任何命令、不访问设备、不修改代码/配置/服务、不改变 P7 或 §0.1/§0.2 门禁，也不构成任何"已验收"结论。** 当前 P7 真实验收仍为 **BLOCKED**，本文只讨论"如何把证据做到可验收"。独立复审：初稿 `docs/ACCEPTANCE_AND_PERMISSION_REVIEW_20261107.md` 判 **PASS-with-limits**（S1–S8）；Rev-2 `docs/ACCEPTANCE_SCHEMA_REV2_REVIEW_20261107.md` 判 **PASS-with-limits（无阻断）**（S1–S3 与 schema 合法性 PASS，V-1/V-2 需修）；**本 Rev-3 已修 V-1/V-2、同步绑定与附录 D/E，Rev-3 仍需复审确认**。
> **写作/修订时间**：2026-11-07 初稿（JST 11:2x）；同日 Rev-2 按 S1–S8 修订；同日 Rev-3 按 Rev-2 复审 V-1/V-2 修订。修订期间未访问设备、未运行采集命令、未改其它文件。
> **针对的问题**：P7 A4 缺少"实际档位/标签/封面/歌词/重复位置/逐项失败/索引与播放"的可机器校验证据 schema；`MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` A4 的细节证据仍为 **NOT RUN / BLOCKED**，且候选 detail 不含 basename/track/disc/路径/MD5。
> **硬规则（贯穿全文，不得违反）**：**不能用扩展名、文件名后缀、码率/采样率/位深等探测事实、请求的档位参数、URL/响应中的 `type`/`br`/`sr` 字段、UI 标签或截图单独冒充"真实音质"；也不能用"文件存在/格式存在"当作通过。** 任何维度都允许且必须允许 `unknown`，不得用推断填补。

## 1. 范围与边界

### 1.1 目标与非目标

**目标**：给出一个 schema，使 P7 的每个 position 都有一条记录，能回答：

1. 该位置的来源声明是什么（请求档位、provider 报告的档位/格式、源标签、源封面、源歌词、逐档结果）；
2. 实际落盘文件是什么、字节是否与账本指纹一致、是否可解码、标签/封面/歌词是否与来源一致；
3. 重复位置如何保留与裁决（unique/no_match/ambiguous、conflict/双发布）；
4. 失败位置为什么失败（固定码 + 证据引用），刷新后原因是否仍可见；
5. 飞牛音乐索引与播放是否有可机器校验的观测证据；
6. 每个结论的证据边界（能证明什么/不能证明什么/哪些是 unknown）。

**非目标（本轮不做）**：不实现采集代码（见 §6.1 缺口）；不执行真机采集；不修改 runbook/合同/检查单；不重新定义产品语义；不证明"音频听感/主观音质"；不证明权益授权之外的下载合法性；不改变 P7/§0.1/§0.2 门禁与任何"BLOCKED"状态；不把 schema 草案当作已批准接口。

### 1.2 输入文档与证据绑定（写作时本地重算）

| 文档 | 大小 | SHA-256 | 本文引用点 |
| --- | --- | --- | --- |
| `docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md` | 16,111 B | `3e0ea759546adf3f64913c09f53d2ec960ac0b7b2690b12555e6e2a71e7fde02` | A1–A16、A4 细节、§6 索引/播放、§4 证据清单 |
| `docs/NETEASE_DELIVERY_EVIDENCE.md` | 11,186 B | `97098fdc034483630b9cd583a0eb7a8f4343eb15594499967b3c6d70dfb4e0b3` | artifact 版本/摘要；NCM/下载边界 |
| `docs/MANUAL_SAVE_METADATA_DELIVERY.md` | 14,723 B | `d4100c029e8912c3d34e9e8bbe2096a089ce3626a539bf23f961c4d5e7590a26` | 封面 host 白名单/`cover_error`/歌词超时与重试 |
| `docs/MANUAL_SAVE_DELIVERY_GUARD.md` | 13,367 B | `cf6af436885f8927700a94a289e40c8443c65192533cde5e48b98c414d48714f` | 音质阶梯/降档证据/固定失败码 |
| `docs/OFFLINE_BEST_AVAILABLE_QUALITY.md` | 5,404 B | `4731548ee4b7684f836dec7b31a8fab09864ce2ee849fed921c76a7178628edc` | "档位名字不是质量证明"的边界 |
| `docs/OFFLINE_MASTER_QUALITY.md` | 4,290 B | `c792f5a9790a2ea583f7e63d3199ba460dc4c64d57afced113db5a3fc197cf48` | 无已验证"网易云档位→母带"映射 |
| `docs/MANUAL_SAVE_PLAYLIST_SOURCE_EVIDENCE.md` | 5,950 B | `4f218ada8d9ef886fb8f84080dc6b8c747465a7e7b031541f3e8bdb78571732e` | P3 只读观察模板/不证明项 |
| `docs/MANUAL_SAVE_PLAYLIST_JOB.md` | 118,962 B | `4b5610ce8bc5e458bfbe9917d29a99764b5e96761340ed9fc8aa441abce0b0cf` | §3.5 detail、§3.6 未满足要求、§4 快照、§5.3 信息边界、§10 验收 |
| `docs/OFFLINE_VERIFIED_PIPELINE.md` | 4,331 B | `52a1f1308dc8e09eff2afdc254261210bc8434f5ae27911906018ce49ef6f1cd` | 发布侧既有契约/边界（不证明真实保存） |
| `docs/OFFLINE_POSIX_STORE.md` | 4,373 B | `0e549e06c2b86ef0cf9b049e685001a8c31e818b0711080b6d6eb5f14be9f988` | 发布侧假设/不证明项 |
| `docs/MANUAL_IMPORT_PLAN.md` | 22,031 B | `721dc5ee02b684e1902232c8b861f158e9c41e634f4728eae166925a65312c26` | §7 格式规则、§8/§9 验收预期 |
| `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` | 114,285 B | `6bef7134511985ee825f7668b735fc755f61b3aaf487ae34099bc6476f71d1ce` | 字节一致性/扫描器/独立视图（§6.3）；Rev-2 重绑 |
| `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` | 8,520 B | `4c01327466001e4cd89b3245bac25e60c9b92cde1d512672eb2399bf4171f842` | RO 位不可依赖 / M2/M4（§6.4）；Rev-2 重绑 |
| `docs/BTRFS_GENERATION_SEMANTICS_20261107.md` | 13,780 B | `0c99d3ac9aae9dd958300fa0fbc2ccf9615951c91e00c58bd7bd61e4b20c07a3` | generation/barrier 语义（§6.3/§6.4）；Rev-2 重绑 |
| `docs/BTRFS_EVIDENCE_ROUND2_REVIEW_20261107.md` | 50,508 B | `830ab5f92d1a1205c8e8cf166fd20ab791b4fcfb58b53656893324e86c45add9` | BTRFS 证据 Round-2 PASS（背景约束） |
| `docs/ACCEPTANCE_AND_PERMISSION_REVIEW_20261107.md` | 30,092 B | `cd39c372…`（S1–S8 依据；完整哈希见该报告文末） | P1 schema 复审 S1–S8；Rev-2 修订依据 |
| `docs/ACCEPTANCE_SCHEMA_REV2_REVIEW_20261107.md` | 18,407 B | `8494a79358e1155552b7fa382c565a69dd2aee23c40268f77904cfbfb0af5f95` | P1 schema Rev-2 复审（PASS-with-limits，V-1/V-2）；**Rev-3 修订依据** |
| 代码（仅引用字段/语义，写作时哈希） | — | `proxy/manual_save_route.py` `07dc7ce27072407a0cffd24c928802f77b73f9f152cefb193dd18d2282c60185`；`proxy/manual_save_playlist.py` `a9f1c4afd8c24677f57947c9529249074cb9756506ec125ad6f1bb93715eafb3`；`proxy/manual_save_jobs.py` `a17e3a624b168c01ed15ef47e77934d5370cc294e50338232ccc58824421ad42`；`proxy/manual_save_playlist_jobs.py` `a3116d30e9f4ab09942acc14d09348302c03e806a62c1ce57f8320b786951f3b`；`proxy/webui_gateway.py` `1b07f8156084d21d64c618b0eb49b6173eab4e1649b6a28e0f0698d06e06848d` | 字段真值表/依赖（附录 B） |

> 说明：任务消息中的 `METEASE_DELIVERY_EVIDENCE` 在仓库中不存在；实际对应上表 `NETEASE_DELIVERY_EVIDENCE.md`（哈希如上行）。本文不修改任何文档/代码；所有结论以可核实文本与代码现状为准，不把"计划/候选"当作已实现。

### 1.3 本文件明确不做

- 不执行任何采集命令、不访问 NAS/G0/容器/账本/曲库；本文件中的命令均为**待授权执行的草案**。
- 不修改 `MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md`、合同、检查单、代码、配置；不改变 A4 "NOT RUN/BLOCKED" 的现状。
- 不把 schema 当作已被复审/批准；任何实现（采集代码、证据存储、索引观测）都必须另立任务、独立复审、并按门禁取得授权。
- 不写"已验收"；不把"schema 完整"当作"验收通过"。

### 1.4 证据分级（用于判断"现在缺什么"）

| 级别 | 内容 | 当前可得性 | 能否支撑 A4 |
| --- | --- | --- | --- |
| **L0** | 只有 UI 显示、文件存在、扩展名/`ffprobe` 格式 | 现在可得 | **不能**（runbook 明确"文件存在/UI 显示 FLAC 不能代替完整验收"；本文 §5.2 反例） |
| **L1** | L0 + 账本 detail（`quality_actual`/status、`format`、`cover_status`、`lyric_status`、`published_sha256/size`）+ 落盘文件复核（解码/标签/封面流/LRC 存在） | 账本 detail 是候选（未部署/未审查）；落盘复核只读可得 | **不足**：缺源侧声明快照（逐档、源标签 track/disc、源封面/歌词字节）、缺 basename 对应、缺字节绑定、缺索引/播放机器观测 |
| **L2** | L1 + **保存时采集的源侧证据** + **独立字节绑定**（scan/独立视图或直接文件哈希+目录核验）+ **索引/播放机器可读观测** + 证据哈希/脱敏/复审 | 需要新实现 + 授权（§6） | **目标形态**；在 L2 全部实现、复审、门禁通过前，A4 保持 BLOCKED |

### 1.5 术语

- **position**：歌单清单中的一个位置（1..1000）；重复曲目按位置分别记录，不合并。
- **provider claim（来源声明）**：保存发生时 provider/适配层返回的字段（如 `data.level`、`al.picUrl`、歌词响应、`data.size/md5`）以及本批次请求档位。它是"声明"，不是"落盘观测"。
- **delivered artifact（交付物）**：实际发布到测试库的音频文件（及 `.lrc` 侧车、被接受为既有冲突目标的文件）。
- **binding（绑定）**：证明某条 claim 与某次保存、某位置、某文件字节对应（song_id、job_id、position、attempt、时间窗、哈希）。
- **独立观测**：不依赖同一内存对象/同一进程结果、从落盘文件或独立视图重新读取得到的证据（ffprobe/ffmpeg、hash、目录清单、独立副本）。
- **证据引用（evidence_ref）**：指向受控证据存储中某个原始产物的引用（含 SHA-256 与字节数），报告本身不内嵌敏感原文。
- **unknown / blocked**：`unknown`＝证据不足或无法判定（合法结论）；`blocked`＝外部门禁/前置未满足，本轮无法开始（如 §0.1/§0.2 未 PASS）。
- **claim 层 / observation 层 / binding 层**：本文所有维度都按这三层记录；任何缺层 → `unknown`。

## 2. 证据模型与 JSON Schema 草案

### 2.1 设计原则

1. **一个 position 一条记录**（含重复位置各一条）；顶层汇总计数；`job/manifest/poisitions_total` 与账本一致。
2. **claim / observation 分离**：来源声明与落盘观测分开字段、分开证据引用；禁止用 observation 反推 claim（除明确允许的"矛盾检查"）。
3. **每个关键值都有证据引用**：没有 `evidence_ref` 的"值"只能标记 `unknown`。
4. **固定枚举 + 固定失败码**；未知输入不进入输出（沿用现有脱敏原则：不回显异常文本/URL/Cookie/token/绝对路径）。
5. **所有原始证据以 SHA-256 + 字节数绑定**；报告保存 `record_sha256`（对规范化记录重算），证据集有 `manifest.sha256`。
6. **三层判定**：每个维度 `pass|fail|unknown`；position 级 `pass|fail|unknown|blocked`；整个 evidence set 有 `overall_verdict`。
7. **fail-closed**：证据缺失、类型非法、无法绑定、无法解密/解码、无法观测 → `unknown`（不得降级为 pass）。
8. **不做推断**：不得由请求档位补 `quality_actual`、不得由扩展名补 `format`、不得由"文件存在"补"已保存"、不得由截图补索引/播放。
9. **schema 只是提案**：字段命名/新增字段均需复审；实施时向后兼容旧账本 detail（旧行缺字段 → unknown，不 fail open）。
10. **脱敏**：schema 不持久化签名 URL、Cookie/token、绝对路径、完整响应体；只持久化白名单抽取物 + 摘要（§2.7）。

### 2.2 顶层结构

```text
EvidenceSet
├─ schema / schema_version / evidence_set_id / created_at / created_by
├─ environment        环境绑定（设备、版本、挂载、部署版本、脚本哈希）
├─ job                账本绑定（job_id / manifest_sha256 / source_fingerprint / positions_total / duplicates）
├─ scope              合同/runbook/本次覆盖的验收项与排除项
├─ positions[]        PositionRecord（每个位置一条）
├─ summary            计数与总体判定（含 blocked_reasons）
├─ review             独立复审记录（reviewer/report_ref/verdict）
└─ redaction          脱敏策略版本与声明
```

### 2.3 PositionRecord 字段总表（表的详细定义见 §2.4；JSON Schema 见 §2.5）

| 字段 | 类型 | 必填 | 允许值/约束 | 来源/说明 |
| --- | --- | --- | --- | --- |
| `position` | int | 是 | `1..1000`，集合内唯一 | 清单位置 |
| `source_id` | string \| null | 是 | 十进制正整数串；未知/无效为 `null` | 账本 positions |
| `duplicate_of_position` | int \| null | 是 | 更早同 `source_id` 位置 | 批次引擎 `duplicate_of_position`；账本不持久化（需采集，见 G-D1） |
| `state` | enum | 是 | `published/conflict/rejected/failed/commit_unknown/cancelled/queued/running/not_executed` | 账本位置状态 |
| `expected_outcome` | enum | 是 | `published/conflict/failure/cancelled` | 该位置的**预期结果**（由 job/操作员设定）；用于统一 verdict 语义（S1） |
| `reason` | string \| null | 是 | 账本/route 固定 reason（附录 C） | `GET job` position 行 |
| `reason_message` | string \| null | 是 | 固定中文文案（不插值） | 合同 §6.3；刷新一致性用 |
| `source_evidence` | object | 是 | §2.4.2 | claim 层 |
| `delivered_evidence` | object \| null | 条件 | `state=published` 时必填；`conflict`/失败态时为 `null`；不得用失败位置的残留文件冒充 | 落盘观测 |
| `byte_scan` | object | 是 | `status` ∈ `unique_match/no_match/ambiguous/unknown/not_run` | 独立字节绑定/位置对应（当前扫描器受限，§6.3） |
| `index_play` | object | 是 | 索引/播放观测或 `not_run` | 飞牛音乐侧（§3.7） |
| `verdict` | object | 是 | 维度判定 + `duplicate_resolution` + position 判定 + 固定原因码 | §2.4.7 |
| `failure` | object \| null | 条件 | `state ∈ rejected/failed/commit_unknown` 时必填；`conflict` 用 `duplicate_resolution`；`published` 仅在矛盾场景填 | 固定码/阶段/证据引用 |
| `timestamps` | object | 是 | `captured_at`,`verified_at`（ISO-8601 UTC） | 采集/复核时间 |
| `record_sha256` | string | 是 | 64 hex；对规范化 position 记录重算 | 防篡改 |

### 2.4 子对象定义

#### 2.4.1 `evidence_ref`

```text
{ ref_id: "ev_<32hex>", kind: <枚举>, sha256: <64hex>, bytes: int>=0,
  captured_at: ISO-8601, capture_method: string<=128,
  custody: "captured"|"hash_verified"|"reviewed" }
```

`kind` 枚举（封闭）：`provider_response_extract`、`route_result`、`ledger_row`、`admin_query`、`ffprobe_json`、`ffmpeg_log`、`file_bytes`、`lrc_bytes`、`cover_bytes`、`stat_snapshot`、`scanner_json`、`ui_observation`、`playback_observation`、`manual_attestation`。
**规则**：`sha256`/`bytes` 必须是对证据存储中**实际原始文件**重算；不得只填声称值。`manual_attestation` 只能用于"操作员声明"（如无写者声明），不能替代机器观测（§5.2）。

#### 2.4.2 `source_evidence`（claim 层）

| 子字段 | 说明 | 允许值/约束 | 关键点 |
| --- | --- | --- | --- |
| `quality.tier_requested` | 产出该 URL 的阶梯档 | `jymaster/hires/lossless/exhigh` 或 `""` | 来自 route `quality_requested`；**不是**实际档位 |
| `quality.reported_level` | provider `data.level` 的 reported token | 合法 token 或 `""` | 只允许 `reported_status=reported` 时非空 |
| `quality.reported_status` | 服务端是否报告 | `reported/unknown` | 代码语义：无法解析 → `unknown/""` |
| `quality.level_in_ladder` | token 是否属于阶梯集合 | `true/false/"unknown"` | false 时不得推断语义 → 维度 unknown（§3.1） |
| `quality.claim_scope` | provider-reported 边界（**S2：schema 必填字段**） | 固定值 `provider_reported_not_independently_verified` | 即使一切通过也只断言"服务端报告 + 字节一致/可解码"，不断言档位等价/母带 |
| `quality.fallback` | 是否降档 | bool | 来自 route `quality_fallback` |
| `quality.attempts[]` | 逐档尝试（若用户策略要求证明降档） | 每项 `{tier, outcome, ref}`；`outcome ∈ available/unavailable/trial/unverified/identity_mismatch`；`fallback=true` 时 schema 要求至少一项 | **当前未持久化（G-Q1）**；缺失时降档理由 unknown |
| `quality.claim_refs[]` | 证据引用 | 至少一个 `provider_response_extract` | 无 ref 时该维度 unknown |
| `container_claim.api_type` | provider `data.type` | `flac/mp3/""` | 仅作 claim |
| `container_claim.status` | `reported/unknown` | — | — |
| `metadata.*` | `title/artist/album/track/disc/lyricist/composer` | 类型/长度同 route；缺失用 `null`/`""` | 源标签声明；track/disc 当前不进 route 最终 data → G-T1 |
| `cover.status` | 账本/route 封面状态 | `preserved/embedded/unavailable/unsupported/error/not_published/not_attempted` | 与代码枚举一致 |
| `cover.error` | `cover_status=error` 时的固定码 | `host_untrusted/url_invalid/address_untrusted/http_error/payload_invalid/decode_failed/embed_failed/unknown` | 其余为 `""` |
| `cover.source` | 源封面描述 | `{kind: attached_pic/provider_pic/none, mime, width, height, bytes, sha256, ref}` | **当前不持久化（G-C1）** |
| `lyric.status` | 歌词状态 | `published/unavailable/error/conflict/not_attempted/unknown` | 与代码枚举一致 |
| `lyric.source` | 源歌词描述 | `{selected_field: lyric/tlyric, utf8_sha256, bytes, line_count, ref}` | **当前不持久化（G-L1）** |
| `integrity` | 下载完整性声明 | `{api_size, api_md5, downloaded_size, downloaded_md5, status: match/mismatch/not_provided/unknown, ref}` | 来自 route `size/md5` 校验（代码在下载后比较）；仓库不持久化 api 值（G-Q2） |

#### 2.4.3 `delivered_evidence`（observation 层，用独立工具从落盘文件读取）

| 子字段 | 说明 | 约束 |
| --- | --- | --- |
| `basename` | 实测文件名（basename 级；**不进公开报告，存受控证据**） | 1..255；不含 `/`；`basename_ref` **必填**（S5） |
| `stat` | 文件元数据 | `{mode,uid,gid,size,mtime_ns}`；只用于一致性与发布语义 |
| `sha256` / `size` | 独立重算的文件摘要/大小 | 必填；用于与账本 `published_sha256/published_size` 绑定 |
| `md5` | 独立重算（若需要对账 route `published_md5`） | 可选 |
| `audio` | ffprobe 的音频事实 | `{format_name, codec_name, sample_rate, channels, sample_fmt, bits_per_raw_sample|null, duration_seconds, bit_rate_bps|null, stream_count}`；**`bits_per_raw_sample` 允许 `0`**（ffprobe 对未知/非 PCM 可能返回 0；0 视为 unknown/N/A，S5/S6） |
| `decode` | ffmpeg 解码检查 | `{ok, exit_code, ref}`；`-xerror -f null` 语义 |
| `tags` | ffprobe 读取的最终标签 | `{title,artist,album,track,disc,lyricist,composer,raw_keys[], ref}` |
| `cover` | 内嵌封面观测 | `{embedded: bool, stream_index|null, codec|null, mime|null, width|null, height|null, bytes_sha256|null, decode_ok|null, sidecar: null|{basename,mime,width,height,sha256}, ref}` |
| `lyric` | 侧车歌词观测 | `{kind: sidecar/none, basename|null, encoding, bytes, line_count, text_sha256, ref}`；`encoding ∈ utf-8/utf-8-bom/other/unknown` |
| `mode_owner` | 与目标目录既有文件/发布语义的一致性 | 记录 `mode/uid/gid` 并与目标目录样本对比（采样方法与样本 ref 记录） |

#### 2.4.4 `byte_scan`（独立字节绑定/位置对应层）

```text
{ profile: "independent_view"|"snapshot"|"copy"|"directory_listing"|"none",
  status: "unique_match"|"no_match"|"ambiguous"|"unknown"|"not_run",
  match_count: int>=0,
  view: null | { kind: "snapshot|copy|send_receive|ext4_copy",
                 identity_ref: evidence_ref, provenance_ref: evidence_ref,
                 tree_uuid: 32hex|null, parent_uuid: 32hex|null,   // S5/V-1：Btrfs UUID 为 16 B=32hex，不是 SHA-256
                 generation_start: int|null, generation_end: int|null,
                 ro_enforcement: "verified|unverified|not_applicable",
                 view_refs: [evidence_ref], cleanup_ref: evidence_ref|null },
  scanner: null | { name, version, sha256, ingress: "local|root_helper", evidence_ref },
  limitations: [string] }
```

**规则**：
- `profile∈{independent_view,snapshot,copy}` ⇒ `scanner` 与 `view` 均**必须为非 null**（schema `if/then` 强制，S4/S5）。
- `profile=directory_listing` ⇒ `scanner` 必须非 null（描述 `find`+`sha256sum` 本地清单工具），`view` **必须为 null/省略**（目录清单不是独立视图；**S5/V-2**）。给 `directory_listing` 配 `snapshot/copy/send_receive/ext4_copy` 的 view 会**假称存在独立视图**，schema 负例拒绝。
- `profile=none` ⇒ `status∈{not_run,unknown}` 且 `view=null`。
- `view.kind=snapshot/copy/send_receive/ext4_copy` 时 `identity_ref`/`provenance_ref` 必填；`ro_enforcement=verified` 必须有真实 EROFS 证据引用（不得口称，§6.4）。
- `tree_uuid`/`parent_uuid` 是 `GET_SUBVOL_INFO` 的**原始 16 B UUID（32hex）**，与扫描器 `_read_btrfs_subvol_info` 读取的 16 B 一致（**V-1**）；**不是 SHA-256**。若将来需要摘要形态，必须另设字段名（如 `tree_uuid_sha256`）并写明规则，不得把 64hex 塞进 `tree_uuid`。
- 当前候选扫描器硬门 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`，**只能输出 unknown**；因此本字段在扫描器升级/独立复审前只能是 `not_run/unknown`（§6.3）。
- 不允许把 `unique_match` 当作"位置对应"的唯一证明（扫描器不输出名字）；basename 对应必须有独立证据（§3.5）。
- 独立视图/M2/M4 的证据字段必须包含视图来源与 RO 强制状态，不得声称未被验证的不可变性（§6.4）。

#### 2.4.5 `index_play`（飞牛音乐侧观测）

```text
{ index: { status: "visible"|"absent"|"unknown"|"not_run",
           method: "machine_api"|"network_payload"|"dom_payload"|"manual_visual",
           machine_readable: bool, entry_ref, observed_fields:
             {title,artist,album,cover,lyric: "present"|"absent"|"unknown"}, refs[] },
  playback: { status: "started"|"advanced"|"failed"|"unknown"|"not_run",
              method: "machine_api"|"network_payload"|"dom_payload"|"manual_visual",   // S3
              machine_readable: bool,                                                    // S3
              parsed_duration_seconds|null, ui_duration_seconds|null,
              duration_match: "match"|"mismatch"|"unknown",
              position_advanced_seconds|null, error_event|null, refs[] },
  limitations: [string]   // 必须含 "cannot_prove_audible_output"（schema contains 强制，S3）
}
```

规则：
- `manual_visual` 或截图**不能**把 `index`/`playback` 的 `status` 判为 `visible`/`started`/`advanced`（schema `if/then` 强制只能 `unknown/not_run`）；`machine_readable=false` 时 index/play 维度至多 `unknown`（§3.7）。
- `playback.status=advanced` 必须满足：`method ∈ {machine_api,network_payload,dom_payload}`、`machine_readable=true`、`position_advanced_seconds>0`、`duration_match="match"`、`error_event=null`（S1/S3；INV-21）。
- `limitations` 必含 `cannot_prove_audible_output`（schema `contains`），防止"截图/播放器无报错"被当作可听/主观正确证据。

#### 2.4.6 `failure`

```text
{ stage: "manifest|quality|download|integrity|probe|tag|publish|cover|lyric|index|play|evidence",
  code: <附录 C 固定码枚举>,          // S4：schema 用固定 enum，不接受任意字符串
  fatal: bool,                    // 是否导致该位置不能算 audio pass
  reason_message: string,         // 固定文案（来自合同表）
  refs: [evidence_ref] }
```

**规则**：`state ∈ {rejected,failed,commit_unknown}` 时 `failure` 必须非 null（INV-17）；`code` 必须属于附录 C 固定集合（schema enum + INV-19）；不得填异常文本/URL/路径。`commit_unknown` 只能得 `blocked/unknown`，不能 pass（A16）。

#### 2.4.7 `verdict`

```text
{ verdict: "pass"|"fail"|"unknown"|"blocked",
  dimensions: { bytes, quality, tags, cover, lyric, duplicate, index_play:
                "pass"|"fail"|"unknown"|"advisory"|"not_applicable" },
  duplicate_resolution: null | "conflict_expected"|"distinct_basenames"|
                        "ambiguous_expected"|"unresolved"|"not_applicable"|"unknown",  // S1：承载 INV-10
  fixed_reasons: [<附录 C>],      // 失败/未知的固定码（schema enum）
  notes: [string] }                // 不含敏感信息；解释边界
```

**判定语义（S1，统一 INV-2/INV-3 与 §5.1/§5.3）**：
- `expected_outcome=published`：`pass` 需 bytes/quality/tags/index_play 均 `pass`、duplicate 为 `pass|not_applicable`、cover/lyric 为 `pass|advisory`（Q5 决定是否设 gate）；否则按 fail/unknown/blocked 规则。
- `expected_outcome=conflict`：`pass` 需 `state=conflict`、`duplicate_resolution=conflict_expected`、`dimensions.duplicate=pass`、`delivered_evidence=null`。
- `expected_outcome=failure`：预期失败被确认（state ∈ rejected/failed + 固定 reason + `failure` 合法 + 刷新一致）⇒ `verdict=pass`（"预期失败已被确认"，不是"保存成功"）；预期失败却 published/矛盾 ⇒ `fail`。`commit_unknown` ⇒ `blocked/unknown`。
- `expected_outcome=cancelled`：`state=cancelled` 且无该位置成功文件 ⇒ `pass`。

### 2.5 JSON Schema 草稿（Draft 2020-12；字段语义以 §2.3/§2.4 为准）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:fnmusic:p1-acceptance-evidence:0.1-draft-20261107",
  "title": "fnmusic P1 acceptance evidence set (draft)",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema", "schema_version", "evidence_set_id", "created_at", "environment", "job", "positions", "summary"],
  "properties": {
    "schema": { "const": "fnmusic.p1.acceptance-evidence" },
    "schema_version": { "const": "0.1-draft-20261107" },
    "evidence_set_id": { "type": "string", "pattern": "^[a-f0-9]{48}$" },
    "created_at": { "type": "string", "format": "date-time" },
    "created_by": { "type": "object", "additionalProperties": false, "required": ["kind"],
      "properties": { "kind": { "enum": ["human", "tool"] }, "identity_ref": { "type": "string", "maxLength": 128 } } },
    "environment": { "$ref": "#/$defs/environment" },
    "job": { "$ref": "#/$defs/job" },
    "scope": { "$ref": "#/$defs/scope" },
    "positions": { "type": "array", "minItems": 1, "maxItems": 1000, "items": { "$ref": "#/$defs/position" } },
    "summary": { "$ref": "#/$defs/summary" },
    "review": { "$ref": "#/$defs/review" },
    "redaction": { "$ref": "#/$defs/redaction" }
  },
  "$defs": {
    "hash64": { "type": "string", "pattern": "^[a-f0-9]{64}$" },
    "evidence_ref": {
      "type": "object", "additionalProperties": false,
      "required": ["ref_id", "kind", "sha256", "bytes", "captured_at"],
      "properties": {
        "ref_id": { "type": "string", "pattern": "^ev_[a-f0-9]{32}$" },
        "kind": { "enum": ["provider_response_extract", "route_result", "ledger_row", "admin_query",
          "ffprobe_json", "ffmpeg_log", "file_bytes", "lrc_bytes", "cover_bytes",
          "stat_snapshot", "scanner_json", "ui_observation", "playback_observation", "manual_attestation"] },
        "sha256": { "$ref": "#/$defs/hash64" },
        "bytes": { "type": "integer", "minimum": 0 },
        "captured_at": { "type": "string", "format": "date-time" },
        "capture_method": { "type": "string", "maxLength": 128 },
        "custody": { "enum": ["captured", "hash_verified", "reviewed"] }
      }
    },
    "environment": {
      "type": "object", "additionalProperties": false,
      "required": ["device_ref", "fnos_version", "kernel", "filesystem", "library_ref", "staging_ref", "deployed"],
      "properties": {
        "device_ref": { "type": "string", "maxLength": 128 },
        "fnos_version": { "type": "string", "maxLength": 64 },
        "kernel": { "type": "string", "maxLength": 128 },
        "filesystem": { "type": "object", "additionalProperties": false,
          "required": ["fstype", "mount_options_digest"],
          "properties": { "fstype": { "type": "string", "maxLength": 32 },
            "mount_options_digest": { "$ref": "#/$defs/hash64" },
            "mount_options_class": { "enum": ["trimacl", "no_trimacl", "other", "unknown"] } } },
        "library_ref": { "type": "string", "maxLength": 128 },
        "staging_ref": { "type": "string", "maxLength": 128 },
        "deployed": { "type": "object", "additionalProperties": false,
          "required": ["version", "provenance_status"],
          "properties": { "version": { "type": "string", "maxLength": 64 },
            "package_sha256": { "type": ["string", "null"], "pattern": "^[a-f0-9]{64}$" },
            "git_head": { "type": ["string", "null"], "pattern": "^[a-f0-9]{40}$" },
            "build_ref": { "type": ["string", "null"], "maxLength": 128 },
            "provenance_status": { "enum": ["observed", "partial", "unknown"] } },
          "if": { "properties": { "provenance_status": { "const": "observed" } }, "required": ["provenance_status"] },
          "then": { "required": ["package_sha256", "git_head"],
                    "properties": { "package_sha256": { "type": "string" }, "git_head": { "type": "string" } } } },
        "tool_hashes": { "type": "object", "additionalProperties": { "$ref": "#/$defs/hash64" } }
      }
    },
    "job": {
      "type": "object", "additionalProperties": false,
      "required": ["job_id", "manifest_sha256", "source_fingerprint", "source_fingerprint_origin", "positions_total"],
      "properties": {
        "job_id": { "type": "string", "pattern": "^[a-f0-9]{48}$" },
        "manifest_sha256": { "$ref": "#/$defs/hash64" },
        "source_fingerprint": { "type": ["string", "null"], "pattern": "^[a-f0-9]{64}$" },
        "source_fingerprint_origin": { "enum": ["snapshot_response", "collection_version_manifest", "unknown"] },
        "playlist_ref": { "type": "string", "maxLength": 128 },
        "positions_total": { "type": "integer", "minimum": 1, "maximum": 1000 },
        "duplicate_positions": { "type": "array", "items": { "type": "integer", "minimum": 1, "maximum": 1000 } }
      },
      "if": { "properties": { "source_fingerprint_origin": { "const": "unknown" } }, "required": ["source_fingerprint_origin"] },
      "then": { "properties": { "source_fingerprint": { "const": null } } }
    },
    "scope": {
      "type": "object", "additionalProperties": false,
      "required": ["contract", "runbook_sha256", "acceptance_items", "excluded"],
      "properties": {
        "contract": { "type": "string", "maxLength": 128 },
        "runbook_sha256": { "$ref": "#/$defs/hash64" },
        "acceptance_items": { "type": "array", "items": { "enum": ["A1","A2","A3","A4","A5","A6","A7","A8","A9","A10","A11","A12","A13","A14","A15","A16"] } },
        "excluded": { "type": "array", "items": { "type": "string", "maxLength": 256 } }
      }
    },
    "position": {
      "type": "object", "additionalProperties": false,
      "required": ["position", "source_id", "duplicate_of_position", "state", "expected_outcome", "reason", "reason_message",
        "source_evidence", "byte_scan", "index_play", "verdict", "timestamps", "record_sha256"],
      "properties": {
        "position": { "type": "integer", "minimum": 1, "maximum": 1000 },
        "source_id": { "type": ["string", "null"], "pattern": "^[1-9][0-9]{0,127}$" },
        "duplicate_of_position": { "type": ["integer", "null"], "minimum": 1, "maximum": 1000 },
        "state": { "enum": ["published", "conflict", "rejected", "failed", "commit_unknown", "cancelled", "queued", "running", "not_executed"] },
        "expected_outcome": { "enum": ["published", "conflict", "failure", "cancelled"] },
        "reason": { "type": ["string", "null"], "maxLength": 64 },
        "reason_message": { "type": ["string", "null"], "maxLength": 256 },
        "source_evidence": { "$ref": "#/$defs/source_evidence" },
        "delivered_evidence": { "oneOf": [{ "type": "null" }, { "$ref": "#/$defs/delivered_evidence" }] },
        "byte_scan": { "$ref": "#/$defs/byte_scan" },
        "index_play": { "$ref": "#/$defs/index_play" },
        "verdict": { "$ref": "#/$defs/verdict" },
        "failure": { "oneOf": [{ "type": "null" }, { "$ref": "#/$defs/failure" }] },
        "timestamps": { "type": "object", "additionalProperties": false,
          "required": ["captured_at"],
          "properties": { "captured_at": { "type": "string", "format": "date-time" },
            "verified_at": { "type": ["string", "null"], "format": "date-time" } } },
        "record_sha256": { "$ref": "#/$defs/hash64" }
      },
      "allOf": [
        { "if": { "properties": { "state": { "const": "published" } }, "required": ["state"] },
          "then": { "required": ["delivered_evidence"],
                    "properties": { "delivered_evidence": { "$ref": "#/$defs/delivered_evidence" } } },
          "else": { "properties": { "delivered_evidence": { "type": "null" } } } },
        { "if": { "properties": { "state": { "enum": ["rejected", "failed", "commit_unknown"] } }, "required": ["state"] },
          "then": { "required": ["failure"], "properties": { "failure": { "$ref": "#/$defs/failure" } } } },
        { "if": { "properties": { "state": { "const": "conflict" } }, "required": ["state"] },
          "then": { "properties": { "verdict": { "properties": { "duplicate_resolution": { "enum": ["conflict_expected", "unresolved"] } },
                                                 "required": ["duplicate_resolution"] } } } }
      ]
    },
    "source_evidence": {
      "type": "object", "additionalProperties": false,
      "required": ["quality", "metadata", "cover", "lyric", "integrity"],
      "properties": {
        "quality": {
          "type": "object", "additionalProperties": false,
          "required": ["tier_requested", "reported_level", "reported_status", "level_in_ladder", "claim_scope", "fallback", "claim_refs"],
          "properties": {
            "tier_requested": { "enum": ["jymaster", "hires", "lossless", "exhigh", ""] },
            "reported_level": { "type": "string", "maxLength": 64 },
            "reported_status": { "enum": ["reported", "unknown"] },
            "level_in_ladder": { "enum": [true, false, "unknown"] },
            "claim_scope": { "const": "provider_reported_not_independently_verified" },
            "fallback": { "type": "boolean" },
            "attempts": { "type": "array", "items": { "type": "object", "additionalProperties": false,
              "required": ["tier", "outcome"],
              "properties": { "tier": { "enum": ["jymaster", "hires", "lossless", "exhigh"] },
                "outcome": { "enum": ["available", "unavailable", "trial", "unverified", "identity_mismatch"] },
                "ref": { "$ref": "#/$defs/evidence_ref" } } } },
            "claim_refs": { "type": "array", "minItems": 0, "items": { "$ref": "#/$defs/evidence_ref" } }
          },
          "if": { "properties": { "fallback": { "const": true } }, "required": ["fallback"] },
          "then": { "required": ["attempts"], "properties": { "attempts": { "minItems": 1 } } }
        },
        "container_claim": { "type": "object", "additionalProperties": false,
          "required": ["api_type", "status"],
          "properties": { "api_type": { "enum": ["flac", "mp3", ""] },
            "status": { "enum": ["reported", "unknown"] },
            "ref": { "$ref": "#/$defs/evidence_ref" } } },
        "metadata": { "type": "object", "additionalProperties": false,
          "required": ["title", "artist", "album", "track", "disc", "lyricist", "composer", "status"],
          "properties": {
            "title": { "type": ["string", "null"], "maxLength": 512 },
            "artist": { "type": ["string", "null"], "maxLength": 512 },
            "album": { "type": ["string", "null"], "maxLength": 512 },
            "track": { "type": ["integer", "null"], "minimum": 1 },
            "disc": { "type": ["integer", "null"], "minimum": 1 },
            "lyricist": { "type": ["string", "null"], "maxLength": 512 },
            "composer": { "type": ["string", "null"], "maxLength": 512 },
            "status": { "enum": ["reported", "unknown"] },
            "ref": { "$ref": "#/$defs/evidence_ref" }
          } },
        "cover": { "type": "object", "additionalProperties": false,
          "required": ["status", "error"],
          "properties": {
            "status": { "enum": ["preserved", "embedded", "unavailable", "unsupported", "error", "not_published", "not_attempted"] },
            "error": { "enum": ["", "host_untrusted", "url_invalid", "address_untrusted", "http_error", "payload_invalid", "decode_failed", "embed_failed", "unknown"] },
            "source": { "oneOf": [{ "type": "null" }, { "type": "object", "additionalProperties": false,
              "required": ["kind", "mime", "width", "height", "bytes", "sha256"],
              "properties": { "kind": { "enum": ["attached_pic", "provider_pic"] },
                "mime": { "enum": ["image/jpeg", "image/png", "image/webp", "unknown"] },
                "width": { "type": "integer", "minimum": 1 }, "height": { "type": "integer", "minimum": 1 },
                "bytes": { "type": "integer", "minimum": 1 }, "sha256": { "$ref": "#/$defs/hash64" },
                "ref": { "$ref": "#/$defs/evidence_ref" } } }] }
          } },
        "lyric": { "type": "object", "additionalProperties": false,
          "required": ["status"],
          "properties": {
            "status": { "enum": ["published", "unavailable", "error", "conflict", "not_attempted", "unknown"] },
            "source": { "oneOf": [{ "type": "null" }, { "type": "object", "additionalProperties": false,
              "required": ["selected_field", "bytes", "line_count", "sha256"],
              "properties": { "selected_field": { "enum": ["lyric", "tlyric"] },
                "bytes": { "type": "integer", "minimum": 1 }, "line_count": { "type": "integer", "minimum": 0 },
                "sha256": { "$ref": "#/$defs/hash64" }, "ref": { "$ref": "#/$defs/evidence_ref" } } }] }
          } },
        "integrity": { "type": "object", "additionalProperties": false,
          "required": ["status"],
          "properties": {
            "api_size": { "type": ["integer", "null"], "minimum": 1 },
            "api_md5": { "type": ["string", "null"], "pattern": "^[a-f0-9]{32}$" },
            "downloaded_size": { "type": ["integer", "null"], "minimum": 1 },
            "downloaded_md5": { "type": ["string", "null"], "pattern": "^[a-f0-9]{32}$" },
            "status": { "enum": ["match", "mismatch", "not_provided", "unknown"] },
            "ref": { "$ref": "#/$defs/evidence_ref" }
          } }
      }
    },
    "delivered_evidence": {
      "type": "object", "additionalProperties": false,
      "required": ["basename", "basename_ref", "stat", "sha256", "size", "audio", "decode", "tags", "cover", "lyric"],
      "properties": {
        "basename": { "type": "string", "minLength": 1, "maxLength": 255, "pattern": "^[^/]+$" },
        "basename_ref": { "$ref": "#/$defs/evidence_ref" },
        "stat": { "type": "object", "additionalProperties": false,
          "required": ["mode", "uid", "gid", "size", "mtime_ns"],
          "properties": { "mode": { "type": "string", "pattern": "^[0-7]{3,4}$" },
            "uid": { "type": "integer", "minimum": 0 }, "gid": { "type": "integer", "minimum": 0 },
            "size": { "type": "integer", "minimum": 1 }, "mtime_ns": { "type": "integer", "minimum": 0 } } },
        "sha256": { "$ref": "#/$defs/hash64" },
        "size": { "type": "integer", "minimum": 1 },
        "md5": { "type": ["string", "null"], "pattern": "^[a-f0-9]{32}$" },
        "audio": { "type": "object", "additionalProperties": false,
          "required": ["format_name", "codec_name", "sample_rate", "channels", "duration_seconds"],
          "properties": { "format_name": { "type": "string", "maxLength": 64 },
            "codec_name": { "type": "string", "maxLength": 64 },
            "sample_rate": { "type": "integer", "minimum": 1 },
            "channels": { "type": "integer", "minimum": 1 },
            "sample_fmt": { "type": ["string", "null"], "maxLength": 64 },
            "bits_per_raw_sample": { "type": ["integer", "null"], "minimum": 0 },
            "duration_seconds": { "type": "number", "exclusiveMinimum": 0 },
            "bit_rate_bps": { "type": ["integer", "null"], "minimum": 1 },
            "stream_count": { "type": "integer", "minimum": 1 },
            "ref": { "$ref": "#/$defs/evidence_ref" } } },
        "decode": { "type": "object", "additionalProperties": false,
          "required": ["ok"],
          "properties": { "ok": { "type": "boolean" }, "exit_code": { "type": ["integer", "null"] },
            "ref": { "$ref": "#/$defs/evidence_ref" } } },
        "tags": { "type": "object", "additionalProperties": false,
          "required": ["title", "artist", "album", "track", "disc"],
          "properties": {
            "title": { "type": ["string", "null"], "maxLength": 512 },
            "artist": { "type": ["string", "null"], "maxLength": 512 },
            "album": { "type": ["string", "null"], "maxLength": 512 },
            "track": { "type": ["string", "null"], "maxLength": 32 },
            "disc": { "type": ["string", "null"], "maxLength": 32 },
            "lyricist": { "type": ["string", "null"], "maxLength": 512 },
            "composer": { "type": ["string", "null"], "maxLength": 512 },
            "raw_keys": { "type": "array", "items": { "type": "string", "maxLength": 64 } },
            "ref": { "$ref": "#/$defs/evidence_ref" } } },
        "cover": { "type": "object", "additionalProperties": false,
          "required": ["embedded"],
          "properties": {
            "embedded": { "type": "boolean" },
            "stream_index": { "type": ["integer", "null"], "minimum": 0 },
            "codec": { "type": ["string", "null"], "maxLength": 64 },
            "mime": { "type": ["string", "null"], "maxLength": 64 },
            "width": { "type": ["integer", "null"], "minimum": 1 },
            "height": { "type": ["integer", "null"], "minimum": 1 },
            "bytes_sha256": { "type": ["string", "null"], "pattern": "^[a-f0-9]{64}$" },
            "decode_ok": { "type": ["boolean", "null"] },
            "ref": { "$ref": "#/$defs/evidence_ref" }
          } },
        "lyric": { "type": "object", "additionalProperties": false,
          "required": ["kind"],
          "properties": {
            "kind": { "enum": ["sidecar", "none"] },
            "basename": { "type": ["string", "null"], "maxLength": 255 },
            "encoding": { "enum": ["utf-8", "utf-8-bom", "other", "unknown"] },
            "bytes": { "type": ["integer", "null"], "minimum": 1 },
            "line_count": { "type": ["integer", "null"], "minimum": 0 },
            "text_sha256": { "type": ["string", "null"], "pattern": "^[a-f0-9]{64}$" },
            "ref": { "$ref": "#/$defs/evidence_ref" }
          } }
      }
    },
    "byte_scan": {
      "type": "object", "additionalProperties": false,
      "required": ["profile", "status", "match_count", "limitations"],
      "properties": {
        "profile": { "enum": ["independent_view", "snapshot", "copy", "directory_listing", "none"] },
        "status": { "enum": ["unique_match", "no_match", "ambiguous", "unknown", "not_run"] },
        "match_count": { "type": "integer", "minimum": 0 },
        "view": { "oneOf": [{ "type": "null" }, { "$ref": "#/$defs/view" }] },
        "scanner": { "oneOf": [{ "type": "null" }, { "$ref": "#/$defs/scanner" }] },
        "limitations": { "type": "array", "items": { "type": "string", "maxLength": 256 } }
      },
      "allOf": [
        { "if": { "properties": { "profile": { "enum": ["independent_view", "snapshot", "copy"] } }, "required": ["profile"] },
          "then": { "required": ["scanner", "view"],
                    "properties": { "scanner": { "$ref": "#/$defs/scanner" }, "view": { "$ref": "#/$defs/view" } } } },
        { "if": { "properties": { "profile": { "const": "directory_listing" } }, "required": ["profile"] },
          "then": { "required": ["scanner"],
                    "properties": { "scanner": { "$ref": "#/$defs/scanner" }, "view": { "type": "null" } } } },
        { "if": { "properties": { "profile": { "const": "none" } }, "required": ["profile"] },
          "then": { "properties": { "status": { "enum": ["not_run", "unknown"] }, "view": { "type": "null" } } } }
      ]
    },
    "view": {
      "type": "object", "additionalProperties": false,
      "required": ["kind", "identity_ref", "provenance_ref", "ro_enforcement"],
      "properties": {
        "kind": { "enum": ["snapshot", "copy", "send_receive", "ext4_copy"] },
        "identity_ref": { "$ref": "#/$defs/evidence_ref" },
        "provenance_ref": { "$ref": "#/$defs/evidence_ref" },
        "tree_uuid": { "type": ["string", "null"], "pattern": "^[a-f0-9]{32}$" },
        "parent_uuid": { "type": ["string", "null"], "pattern": "^[a-f0-9]{32}$" },
        "generation_start": { "type": ["integer", "null"], "minimum": 0 },
        "generation_end": { "type": ["integer", "null"], "minimum": 0 },
        "ro_enforcement": { "enum": ["verified", "unverified", "not_applicable"] },
        "view_refs": { "type": "array", "items": { "$ref": "#/$defs/evidence_ref" } },
        "cleanup_ref": { "oneOf": [{ "type": "null" }, { "$ref": "#/$defs/evidence_ref" }] }
      },
      "if": { "properties": { "ro_enforcement": { "const": "verified" } }, "required": ["ro_enforcement"] },
      "then": { "required": ["view_refs"], "properties": { "view_refs": { "minItems": 1 } } }
    },
    "scanner": {
      "type": "object", "additionalProperties": false,
      "required": ["name", "sha256", "ingress"],
      "properties": { "name": { "type": "string", "maxLength": 64 }, "version": { "type": "string", "maxLength": 32 },
        "sha256": { "$ref": "#/$defs/hash64" }, "ingress": { "enum": ["local", "root_helper"] },
        "evidence_ref": { "$ref": "#/$defs/evidence_ref" } }
    },
    "index_play": {
      "type": "object", "additionalProperties": false,
      "required": ["index", "playback", "limitations"],
      "properties": {
        "index": { "type": "object", "additionalProperties": false,
          "required": ["status", "method", "machine_readable"],
          "properties": { "status": { "enum": ["visible", "absent", "unknown", "not_run"] },
            "method": { "enum": ["machine_api", "network_payload", "dom_payload", "manual_visual"] },
            "machine_readable": { "type": "boolean" },
            "entry_ref": { "$ref": "#/$defs/evidence_ref" },
            "observed_fields": { "type": "object",
              "additionalProperties": { "enum": ["present", "absent", "unknown"] } },
            "refs": { "type": "array", "items": { "$ref": "#/$defs/evidence_ref" } } },
          "allOf": [
            { "if": { "properties": { "method": { "const": "manual_visual" } }, "required": ["method"] },
              "then": { "properties": { "status": { "enum": ["unknown", "not_run"] }, "machine_readable": { "const": false } } } },
            { "if": { "properties": { "machine_readable": { "const": false } }, "required": ["machine_readable"] },
              "then": { "properties": { "status": { "enum": ["unknown", "not_run"] } } } }
          ] },
        "playback": { "type": "object", "additionalProperties": false,
          "required": ["status", "method", "machine_readable"],
          "properties": { "status": { "enum": ["started", "advanced", "failed", "unknown", "not_run"] },
            "method": { "enum": ["machine_api", "network_payload", "dom_payload", "manual_visual"] },
            "machine_readable": { "type": "boolean" },
            "parsed_duration_seconds": { "type": ["number", "null"] },
            "ui_duration_seconds": { "type": ["number", "null"] },
            "duration_match": { "enum": ["match", "mismatch", "unknown"] },
            "position_advanced_seconds": { "type": ["number", "null"], "minimum": 0 },
            "error_event": { "type": ["string", "null"], "maxLength": 128 },
            "refs": { "type": "array", "items": { "$ref": "#/$defs/evidence_ref" } } },
          "allOf": [
            { "if": { "properties": { "method": { "const": "manual_visual" } }, "required": ["method"] },
              "then": { "properties": { "status": { "enum": ["unknown", "not_run"] }, "machine_readable": { "const": false } } } },
            { "if": { "properties": { "machine_readable": { "const": false } }, "required": ["machine_readable"] },
              "then": { "properties": { "status": { "enum": ["unknown", "not_run"] } } } },
            { "if": { "properties": { "status": { "const": "advanced" } }, "required": ["status"] },
              "then": { "required": ["duration_match", "position_advanced_seconds"],
                        "properties": { "method": { "enum": ["machine_api", "network_payload", "dom_payload"] },
                          "machine_readable": { "const": true },
                          "duration_match": { "const": "match" },
                          "position_advanced_seconds": { "type": "number", "exclusiveMinimum": 0 },
                          "error_event": { "const": null } } } }
          ] },
        "limitations": { "type": "array", "minItems": 1, "items": { "type": "string", "maxLength": 256 },
          "contains": { "const": "cannot_prove_audible_output" } }
      }
    },
    "failure": {
      "type": "object", "additionalProperties": false,
      "required": ["stage", "code", "fatal"],
      "properties": {
        "stage": { "enum": ["manifest", "quality", "download", "integrity", "probe", "tag", "publish", "cover", "lyric", "index", "play", "evidence"] },
        "code": { "$ref": "#/$defs/reason_code" },
        "fatal": { "type": "boolean" },
        "reason_message": { "type": "string", "maxLength": 256 },
        "refs": { "type": "array", "items": { "$ref": "#/$defs/evidence_ref" } }
      }
    },
    "reason_code": { "enum": [
      "service_unavailable", "no_quality_available", "quality_unverified", "quality_identity_mismatch",
      "trial_only", "download_target_untrusted", "download_failed", "integrity_mismatch",
      "invalid_audio", "tag_failed", "conflict", "disabled", "config_invalid", "busy",
      "staging_unavailable", "publish_failed", "internal", "invalid_song_id", "too_many_positions",
      "budget_exhausted", "not_executed", "commit_unknown", "completion_unknown", "cancelled",
      "evidence_missing_quality_claim", "evidence_quality_contradiction", "evidence_tag_mismatch",
      "evidence_cover_mismatch", "evidence_lyric_mismatch", "evidence_basename_unresolved",
      "evidence_byte_binding_unavailable", "evidence_scanner_unknown", "evidence_index_unverified",
      "evidence_playback_unverified", "evidence_capture_failed", "evidence_redaction_violation",
      "evidence_schema_invalid"
    ] },
    "verdict": {
      "type": "object", "additionalProperties": false,
      "required": ["verdict", "dimensions", "duplicate_resolution"],
      "properties": {
        "verdict": { "enum": ["pass", "fail", "unknown", "blocked"] },
        "dimensions": { "type": "object", "additionalProperties": false,
          "required": ["bytes", "quality", "tags", "cover", "lyric", "duplicate", "index_play"],
          "properties": {
            "bytes": { "enum": ["pass", "fail", "unknown", "advisory", "not_applicable"] },
            "quality": { "enum": ["pass", "fail", "unknown", "advisory", "not_applicable"] },
            "tags": { "enum": ["pass", "fail", "unknown", "advisory", "not_applicable"] },
            "cover": { "enum": ["pass", "fail", "unknown", "advisory", "not_applicable"] },
            "lyric": { "enum": ["pass", "fail", "unknown", "advisory", "not_applicable"] },
            "duplicate": { "enum": ["pass", "fail", "unknown", "advisory", "not_applicable"] },
            "index_play": { "enum": ["pass", "fail", "unknown", "advisory", "not_applicable"] }
          } },
        "duplicate_resolution": { "enum": [null, "conflict_expected", "distinct_basenames",
          "ambiguous_expected", "unresolved", "not_applicable", "unknown"] },
        "fixed_reasons": { "type": "array", "items": { "$ref": "#/$defs/reason_code" } },
        "notes": { "type": "array", "items": { "type": "string", "maxLength": 256 } }
      }
    },
    "summary": {
      "type": "object", "additionalProperties": false,
      "required": ["positions_total", "pass", "fail", "unknown", "blocked", "overall_verdict"],
      "properties": {
        "positions_total": { "type": "integer", "minimum": 1, "maximum": 1000 },
        "pass": { "type": "integer", "minimum": 0 },
        "fail": { "type": "integer", "minimum": 0 },
        "unknown": { "type": "integer", "minimum": 0 },
        "blocked": { "type": "integer", "minimum": 0 },
        "overall_verdict": { "enum": ["pass", "fail", "unknown", "blocked"] },
        "blocked_reasons": { "type": "array", "items": { "type": "string", "maxLength": 256 } }
      }
    },
    "review": {
      "type": "object", "additionalProperties": false,
      "required": ["required"],
      "properties": {
        "required": { "type": "boolean" },
        "reviewer_ref": { "type": ["string", "null"], "maxLength": 128 },
        "reviewed_at": { "type": ["string", "null"], "format": "date-time" },
        "verdict": { "enum": ["PASS", "PASS-with-limits", "BLOCKED", "not_reviewed"] },
        "report_ref": { "oneOf": [{ "type": "null" }, { "$ref": "#/$defs/evidence_ref" }] }
      }
    },
    "redaction": {
      "type": "object", "additionalProperties": false,
      "required": ["policy_version", "no_urls", "no_tokens", "no_absolute_paths", "canonicalization_version"],
      "properties": {
        "policy_version": { "type": "string", "maxLength": 32 },
        "no_urls": { "const": true },
        "no_tokens": { "const": true },
        "no_absolute_paths": { "const": true },
        "basename_in_private_store_only": { "type": "boolean" },
        "raw_provider_response_retained": { "const": false },
        "canonicalization_version": { "const": "utf8-sorted-json-v1" }
      }
    }
  }
}
```

> 该 schema 是**结构草案**：字段命名、`required` 集合、是否需要把 `duplicate` 并入 `byte_scan` 等细节需复审后定稿；实现前必须用 JSON Schema 校验器验证，并另写"语义不变量"测试（§2.6）。**不得把 schema 校验通过当作证据通过。**

### 2.6 跨字段语义不变量（JSON Schema 表达不了，必须另测）

- **INV-1**：`state=published` ⇒ `delivered_evidence` 非空，且 `delivered.sha256/size` 已由独立重算；`expected_outcome=published` 而实际为失败/冲突时，`verdict` 不得为 `pass`；`byte_scan.status∈{not_run,unknown}` 时 bytes 维度不得 `pass`（除非 `limitations` 明确记录且复审接受）。
- **INV-2（S1：与 §5.1 统一）**：`verdict=pass` 的维度门槛由 `expected_outcome` 决定：
  - `expected_outcome=published`：`bytes/quality/tags/index_play` 必须均 `pass`；`duplicate` 必须 `pass|not_applicable`；`cover/lyric` 必须 `pass|advisory`（是否设为 gate 由 Q5 决定）。
  - `expected_outcome=conflict`：`duplicate=pass` 且 `duplicate_resolution=conflict_expected`、`delivered_evidence=null`。
  - `expected_outcome=failure`：失败被确认（`state∈{rejected,failed}` + 固定 reason + `failure` 合法 + 刷新一致）⇒ `verdict=pass`（"预期失败已确认"，不是"保存成功"）；`commit_unknown` ⇒ `blocked/unknown`，不得 pass。
  - `expected_outcome=cancelled`：`state=cancelled` 且无该位置成功文件。
- **INV-3（S1：与 §5.3 P4 统一）**：`verdict=fail` ⇒ 至少满足其一：①存在 `dimensions.*=fail`；②实际 `state` 与 `expected_outcome` 不符；③`failure` 非法/缺失或 `code` 不在固定集合；且 `fixed_reasons` 非空。**预期失败位置的"失败已被确认"是 pass，不是 fail**（见 INV-2）。
- **INV-4**：`quality.reported_status=reported` ⇒ `reported_level` 非空且匹配 `^[a-z0-9][a-z0-9._-]{0,63}$`；反之 `reported_level=""`。
- **INV-5**：`quality.reported_level` 与 `tier_requested` 不得互相推导：`reported_level` 只能来自 claim 证据，不得由 `tier_requested`/`format`/`bit_rate` 填充。
- **INV-6**：`quality.level_in_ladder=false` ⇒ quality 维度至多 `unknown`（没有可信映射，禁止猜测档位语义）。
- **INV-7**：`cover.status=embedded/preserved` ⇒ `delivered.cover.embedded=true` 且 `delivered.cover.decode_ok=true`；`cover.status=unavailable/unsupported/error` ⇒ `delivered.cover.embedded=false`（不一致 → cover fail）。
- **INV-8**：`lyric.status=published` ⇒ `delivered.lyric.kind=sidecar` 且文件可读取、可严格 UTF-8 解码；`unavailable/error` ⇒ `kind=none`（不一致 → lyric fail）。
- **INV-9**：`source_evidence.cover.source`/`lyric.source` 缺失时，对应维度不得判 `pass`（只能 unknown/advisory）。
- **INV-10（S1：裁决值进入 schema 枚举）**：`duplicate_of_position≠null` 或 `job.duplicate_positions` 含该 position ⇒ `verdict.duplicate_resolution` 必须非 null 且 ∈ `{conflict_expected,distinct_basenames,ambiguous_expected,unresolved,unknown}`；`dimensions.duplicate=pass` 要求 resolution ∈ `{conflict_expected,distinct_basenames}`；`state=conflict` 要求 resolution ∈ `{conflict_expected,unresolved}` 且 `delivered_evidence=null`（schema 已用 if/then 强制）。`conflict` 位置不得填本位置文件冒充。
- **INV-11（S1：与 §3.7 统一）**：`index.status∈{visible,absent}` 必须 `machine_readable=true` 且 `method ∈ {machine_api,network_payload,dom_payload}`；`playback.status=advanced` 必须 `method` 为机器可读、`machine_readable=true`、`position_advanced_seconds>0`、`duration_match="match"`、`error_event=null`；`method=manual_visual` 时 index/play status 只能是 `unknown/not_run` 且 `machine_readable=false`；`index_play.limitations` 必须含 `cannot_prove_audible_output`。
- **INV-12**：任何 `evidence_ref` 的 `sha256/bytes` 必须与证据存储 manifest 一致；`custody=reviewed` 必须来自独立复审者的记录。
- **INV-13（S6/S7：规范化定义）**：`record_sha256` 必须对"去掉该字段后的 position 记录"按 §2.7 的 `utf8-sorted-json-v1` 规范化后重算一致；`evidence set` 的 `manifest.sha256` 覆盖全部 refs，行格式见 §2.7。
- **INV-14**：`blocked` 只能用于"外部门禁/前置未满足"（如 §0.1/§0.2 未 PASS、扫描器硬门未解除、部署未包含候选），不能用于掩盖数据缺失（数据缺失应 `unknown`）。
- **INV-15（S4：位置/总数）**：`positions[].position` 集合内唯一（1..1000），且 `job.positions_total == positions.length`；若清单顺序可得，positions 顺序与 manifest 位置顺序一致。
- **INV-16（S4：汇总一致）**：`summary.pass + summary.fail + summary.unknown + summary.blocked == summary.positions_total`；`overall_verdict` 推导：任一 fail ⇒ `fail`；否则任一 blocked ⇒ `blocked`；否则任一 unknown ⇒ `unknown`；全部 pass ⇒ `pass`。不得把部分完成表述为整单成功（A7）。
- **INV-17（S4：state↔failure/delivered）**：`state∈{rejected,failed,commit_unknown}` ⇒ `failure` 非空；`state=published` ⇒ `delivered_evidence` 非空且 `failure=null`（除非记录矛盾）；`state=conflict` ⇒ `delivered_evidence=null` 且 `duplicate_resolution∈{conflict_expected,unresolved}`；`state∈{cancelled,queued,running,not_executed}` ⇒ `delivered_evidence=null`。
- **INV-18（S4：fallback→attempts）**：`quality.fallback=true` ⇒ `attempts` 非空，且按 tier 顺序覆盖所有高于 `tier_requested` 的档位（每项 `outcome` 合法）；`reported_status=reported` 且 `reported_level≠tier_requested` 时 `fallback` 必须为 true（对齐 route 语义）。
- **INV-19（S4：固定码）**：`failure.code` 与 `verdict.fixed_reasons[]` 必须属于附录 C 固定码集合（schema enum）；不得写入异常文本、自由字符串、URL/路径；`reason_message` 只允许合同固定文案或 null。
- **INV-20（S4/S5：视图/扫描器）**：`profile∈{independent_view,snapshot,copy}` ⇒ `scanner` 与 `view` 均非 null（schema if/then）；`profile=directory_listing` ⇒ `scanner` 非 null 且 `view=null/省略`（不得伪造独立视图，V-2）；`profile=none` ⇒ `status∈{not_run,unknown}` 且 `view=null`；`view.tree_uuid/parent_uuid` 若提供必须为 **32hex 原始 Btrfs UUID（16 B，V-1）**；`view.ro_enforcement=verified` ⇒ `view_refs` 含真实 EROFS/RO 强制证据且 `identity_ref/provenance_ref` 非空。
- **INV-21（S3：播放机器可读）**：`index/playback` 的 `method/machine_readable/status` 关系同 INV-11；`limitations` 必含 `cannot_prove_audible_output`（schema `contains`）；截图/`manual_visual` 不能进入 pass 证据。
- **INV-22（S8：绑定可得性）**：`environment.deployed.provenance_status=observed` ⇒ `package_sha256/git_head` 非 null；否则可为 null 但必须显式 `partial/unknown`；`job.source_fingerprint_origin=unknown` ⇒ `source_fingerprint=null`；`snapshot_response` ⇒ `source_fingerprint` 为 64hex。
- **INV-23（S1：预期≠实际）**：`expected_outcome` 与实际 `state` 不符时可以存在（用于记录异常），但必须由 `verdict=fail/unknown` 表达并在 `fixed_reasons` 给出原因码；不得静默 pass。

> 标记为"schema 已强制"的条目由 §2.5 的 `if/then`/`enum`/`contains` 表达；其余条目必须由实现侧测试覆盖（INV 测试；见 §6.1 G-X3）。**不允许**用"schema 校验通过"代替 INV 测试。Rev-2 复审的 V-3 明确：**S4 中位置唯一（INV-15）、汇总计数/overall（INV-16）、expected_outcome↔verdict（INV-2/3/23）、published failure=null（INV-17）等不可由 JSON Schema 表达的条款，归入实现侧 G-X3 语义测试，不是 schema 本身缺陷**；在 INV 测试实现并通过前，不声称"S4 已机器闭环"。

### 2.7 证据存储、哈希与脱敏规则

- **存储位置**：受控证据目录（建议 root 0700，位于非 repo 的私有持久目录；不得在 `/tmp` 作长期证据）；每个 evidence set 一个目录，含 `manifest.sha256`（按文件名排序的 `sha256sum` 清单）、`record.json`、`artifacts/`。
- **写入方式**：采集脚本一次写入、只追加；目录/文件权限不得让普通用户/管线写入；复审后只读。
- **绑定**：`evidence_set_id` 与 `manifest` 一起记录；报告引用 `ev_...`，**不内嵌**原始响应/路径/URL。
- **规范化（S6/S7）**：`record_sha256` 与 `manifest.sha256` 必须按 `utf8-sorted-json-v1` 计算：UTF-8 无 BOM；对象键按 Unicode 码点升序；无多余空白/换行；字符串仅做 JSON 必要转义、非 ASCII 直接 UTF-8；数字用 JSON 数值规范形式（整数不带小数点/指数；浮点用最短往返表示）；数组保持原序。`record_sha256 = SHA-256(规范化(position 记录去掉 record_sha256))`；`manifest.sha256` 覆盖 `artifacts/` 下全部文件，按相对文件名排序，每行 `<sha256>  <relative_name>\n`，对该 UTF-8 文本重算；manifest 自身哈希另外记录在 evidence set 外层（不得把 manifest 自己列入自身）。实现必须记录 `redaction.canonicalization_version`。
- **绝不持久化**：签名 URL、Cookie/token/authorization、完整 provider 响应体（含 URL）、绝对路径、job DB 行原文、账号 UID（用 `account_ref` 哈希替代）、歌曲签名 URL 的任何可重放形态。
- **允许持久化但敏感**：basename（A4/A5 需要）、title/artist/album、`published_sha256/size`、ffprobe 输出（不含路径时）、歌词文本摘要（全文如必须，放受控证据并限制访问）。报告中默认只放摘要/计数；全文证据放受控目录。
- **保留期/销毁**：由用户决定（§6.2 Q6）；在决定前默认"保留到独立复审完成 + 用户确认"，不做自动清理。
- **不可伪造声明**：任何"手动声明"（如无写者）只能作为辅助字段，不得替代机器观测（§5.2 反例）。

## 3. 各维度采集与判据

> 通用分层：claim（源声明）→ observation（独立观测）→ binding（把两者与 position/file 绑定）→ verdict。每节先给"证据来源"，再给"判据"，最后给"缺口"。

### 3.1 实际音质档位（Q，最关键）

**证据来源（按可信度排序，且全部只作为"证据"，不翻译成营销名称）**：

| 编号 | 来源 | 能证明 | 不能证明 | 当前可得性 |
| --- | --- | --- | --- | --- |
| Q-E1 | provider 响应 `data.level`（经 route `_reported_quality_level` 归一化，`reported/unknown`） | "服务端在该次 URL 响应中报告了该 level token"，可绑定 song_id/request/attempt | 不能证明实际交付字节的真实档位；不能证明账号权益；不能证明"超清母带" | route 有值但**未持久化**（仅进最终 data），逐次响应未留证（G-Q2） |
| Q-E2 | 逐档阶梯结果（`jymaster→hires→lossless→exhigh` 每档 available/unavailable/trial 等） | 降档是否按"明确证据"发生（delivery guard） | 不能证明更高档的真实可用性（provider 单方声明） | 内存中计算，**未持久化**（G-Q1） |
| Q-E3 | provider 响应 `data.size`/`data.md5` vs 下载字节 size/md5（route 强校验） | 下载字节完整性；响应与落盘字节同源 | 不能证明档位；size/md5 是 provider 声明 | 校验有，值**未持久化**（G-Q2） |
| Q-E4 | 落盘文件独立观测：`ffprobe` 的 `format_name/codec_name/sample_rate/bits_per_raw_sample/duration` + `ffmpeg` 解码 | 文件可解码、容器/编码、时长等**事实** | **不能**证明档位（硬规则：码率/采样率/位深不是档位证明；仓库也无已验证档位映射） | 可只读获得（采集步骤 §4.3） |
| Q-E5 | 文件 SHA-256/size 与账本 `published_sha256/size` 的绑定 | 落盘文件就是 route 送交 staging 的 tagged 字节（若账本可信） | 不能证明 provider 声明真实；不能证明文件名/位置对应（需 §3.5） | 账本候选未部署/未审查；需独立重算 + 扫描器/目录匹配（当前 unknown） |
| Q-E6 | 独立视图（M2 send/receive 或 M4 拷贝）上的字节扫描/哈希 | 证据载体独立于源（物理独立/RO 强制），可跨窗口复核 | 仍不能证明档位语义 | 设计见 SCAN 文档 §7；当前未实现/未复审 |

**硬规则在 schema 中的落实**：`quality.reported_level` 只允许来自 Q-E1（带 `provider_response_extract` 引用）；**禁止**把以下任何字段写进 `reported_level`/`quality_actual` 或作为其推导：文件名后缀/扩展名、`format_name`/`codec_name`、`bit_rate`、`sample_rate`、`bits_per_raw_sample`、`quality_requested`、URL、`data.type`、`data.br`、`data.sr`、`levelConfuse`、`fee/payed/rightSource/message`、UI 标签、截图、相邻样本。
`quality.claim_scope`（**S2：schema 必填字段**）：固定值 `provider_reported_not_independently_verified`——即使一切通过也只断言"服务端报告了该 level 且交付字节与响应一致/可解码"，**不断言绝对档位等价或母带**（`OFFLINE_MASTER_QUALITY.md` 明确仓库无已验证映射）。

**判据（quality 维度）**：

- **PASS** 需同时（AND）：
  1. `reported_status=reported` 且 token 合法（INV-4）；`level_in_ladder=true`（否则 unknown，INV-6）；
  2. 有 `provider_response_extract` 证据引用，绑定 `song_id`、`job_id/position`、请求档位、响应 code/id/level 抽取物；无则 unknown；
  3. 若 `fallback=true`：`attempts[]` 完整且每一更高档的 outcome ∈ `{unavailable, trial}`（按 delivery guard 的"明确证据"语义；缺少逐档证据则 unknown，不得默认"更高档不可用"）；
  4. 交付字节独立观测（Q-E4）：可解码、`format_name ∈ {flac,mp3}`、`duration>0`；且无"损失性矛盾"：若 reported level 属于损失性家族（`jymaster/hires/lossless`）而 `codec_name=mp3`/`format_name=mp3` → **fail（矛盾）**；
  5. 字节绑定（Q-E5）：`delivered.sha256/size == ledger.published_sha256/size`（独立重算）；若账本指纹缺失/候选未部署 → unknown；
  6. 用户策略满足：`quality_requested` 为阶梯首档且仅在更高档明确不可用时降档（由 attempts 证明）；用户确认过"账号无更高档权益"的证据若存在则记录（当前无独立权益证据 → 由 Q-E2 的 provider 明确 unavailable 兜底，见 Q2）。
- **FAIL**：证据矛盾且可归因：损失性容器 vs 损失性 level；duration 与源声明/元数据严重不符（源时长不可得则跳过，不 fail）；字节绑定不一致（文件被改/发布错误）；`reported_status=reported` 但 token 非法（route 应已归一化为 unknown，若出现则为采集 bug → fail/unknown）。
- **UNKNOWN**：`reported_status=unknown`（服务端未报告 level）；`level_in_ladder=false`（token 未识别，无映射）；逐档证据缺失（无法证明降档正当）；字节绑定不可用（扫描器 unknown/未实现）；账号权益证据缺失且用户要求更强证明。
- **绝不**：用 `quality_requested` 回填 `reported_level`；用扩展名/codec/码率判档位；把 `reported` 当作"已独立验证档位"；把降档默认视为合理。

**当前实现缺口**：G-Q1（逐档 attempts 未持久化）、G-Q2（响应抽取物/attempt 绑定未持久化）、G-Q3（档位语义映射未验证 → 需要用户决定 Q1/Q2）、G-Q4（Q-E5 依赖账本候选部署 + 扫描器/独立视图，当前 unknown）。见 §6。

### 3.2 标签（T：title/artist/album/track/disc/lyricist/composer）

**证据来源**：

- **T-E1 源标签声明**：保存时 `/api/v1/song/{id}/info` 响应抽取（`name`、`ar[].name` 用 `/` 连接、`al.name`、`no`、`cd`、`lyricist`、`composer`）；route 只在成功 data 中放 `title/artist/album`（+ `lyricist/composer`），**`track/disc` 只进 tag metadata、不进最终 data**（G-T1）；source 响应未持久化（G-T2）。
- **T-E2 落盘标签观测**：`ffprobe` 读取最终文件的 `format.tags` + 音频流 `tags`；按别名归一（`track/tracknumber/track number`、`disc/discnumber/disc number/disk` 等，与 `_validate_tagged_metadata` 的别名语义一致）。
- **T-E3 发布期自校验**：route 在 `validate_tagged` 中用同一份 `metadata` 严格比对（`_validate_tagged_metadata`），只能证明"tagging 后与期望值一致"，**不是独立验收**（同一进程、同一来源值）。

**判据**：

- **PASS**：T-E1 与 T-E2 对每个应有字段一致：
  - `title/artist/album`：NFC 规范化后字符串精确相等（区分大小写与分隔符 `"/"`；不做模糊匹配/相似度）；
  - `track/disc`：源正整数 vs 文件标签解析出的前导整数相等（允许 `"3/12"` 形式）；缺失按"源有/文件无 → fail；源无/文件有 → advisory/unknown"；
  - `lyricist/composer`：源非空时文件须相等；源空时文件额外值记 advisory（不 fail）；
  - 文件额外标签（encoder 等）不算失败，记录 `raw_keys`；
  - 缺失 T-E1（源响应未采集）→ **unknown（不是 pass）**，因为无法对照来源。
- **FAIL**：应有字段值不一致/缺失；文件标签被 `tagged_metadata_mismatch` 类错误拒绝但账本显示已发布（不一致）；track/disc 语义冲突。
- **UNKNOWN**：源响应未采集（G-T2）；track/disc 不可得（G-T1）；编码/规范化差异无法判定时（记录差异类别，不静默 pass）。
- **禁止**：用文件名推断标签；用 route `tagged=True` 或 `validate_tagged` 通过代替独立对照；用 UI 显示的标题反推源声明。

### 3.3 封面（C）

**证据来源**：

- **C-E1 源封面声明**：`cover_status=preserved` 时源音频自带 attached_pic（源文件 probe 的 attached_pic 流）；`embedded` 时来自 provider `al.picUrl` 抓取（host 仅 `p1.music.126.net`/`p2.music.126.net`，MIME/magic 一致、≤10MB、尺寸校验、ffmpeg 解码；见元数据文档）；`unavailable/unsupported/error` 有固定原因。源封面字节/尺寸/MIME 的 hash **未持久化**（G-C1）。
- **C-E2 落盘封面观测**：最终文件 `ffprobe` 的 video 流 `disposition.attached_pic==1`；提取该流字节（`ffmpeg -c copy`）后 sniff MIME、解码、宽高、hash；检查无同名外置封面副作用（当前实现不外置）。
- **C-E3 状态一致性**：`cover_status` 与 `cover_embedded`/`cover_error` 的既有语义（`embedded/preserved` ⇔ 有 attached_pic；`error` 仅此时 `cover_error` 非空；其余 `""`）。

**判据**：

- **PASS**（一致性口径）：账本/route 声明状态与落盘观测一致，且：
  - `preserved`/`embedded`：存在 attached_pic、可解码、MIME ∈ {jpeg,png,webp}、尺寸通过校验、`C-E2` hash 与源封面 hash 相等（源字节可得时）；源字节不可得 → 至多 unknown（G-C1）；
  - `unavailable`：无 attached_pic 且文件确实无封面（与声明一致）；
  - `unsupported`：无 attached_pic、`cover_error=""`（webp-on-mp3 固定路径，见代码）；记录但不判 fail；
  - `error`：无 attached_pic 且 `cover_error` ∈ 固定枚举，与账本一致；**A4 的"一致性"可 pass，但"封面成功"不算 pass**（是否要求封面成功由 Q5 决定）。
- **FAIL**：声明有封面但落盘无 attached_pic；落盘有封面但声明 error/unavailable；MIME/尺寸/解码失败与声明矛盾；hash 与源字节不一致（源字节可得时）。
- **UNKNOWN**：源封面字节未采集（无法证明"这张封面来自该曲目"）；提取/解码工具失败；外置封面语义未定义。
- **禁止**：用文件名/UI 截图显示封面当作 pass；用"有 attached_pic"单独证明封面正确（还需要来源一致性/可解码/尺寸/（可得时）hash）。

### 3.4 歌词（L）

**证据来源**：

- **L-E1 源歌词声明**：保存时 `/api/v1/song/{id}/lyric` 响应 `data.lyric`（空则 `tlyric`），`strip()` + `"\n"` 后 UTF-8（strict）编码，≤1 MiB；route 只在内存保留并发布 `.lrc`；源 bytes/hash/行数**未持久化**（G-L1）。
- **L-E2 落盘观测**：同名 `.lrc`（`basename` 去扩展名 + `.lrc`）是否存在、可读、严格 UTF-8 解码（或不含 BOM；若发现 BOM/其它编码按 unknown/fail 规则）、字节数、行数、规范化文本 hash；与 `lyric_status` 一致性（published ⇔ 存在；unavailable/error ⇔ 不存在；conflict ⇔ 记录既有同名文件，不覆盖）。
- **L-E3 行级一致**：源文本与落盘文本逐行比较（规范化行尾 `\r\n`→`\n`；如编码一致则字节哈希应完全相等；若只有规范化后相等 → `match_normalized`，判定 unknown/advisory，不直接 pass）。

**判据**：

- **PASS**：`lyric_status` 与落盘存在性一致，且（源证据可得时）源 UTF-8 字节 sha256 == 落盘字节 sha256（最强）；行数/行内容一致。
- **FAIL**：声明 published 但 `.lrc` 缺失/不可解码；声明 unavailable/error 但存在有效 `.lrc`（不一致）；行内容不同（源可得时）。
- **UNKNOWN**：源歌词未采集（G-L1）；只有规范化行相等但字节不同；编码/行尾差异无法解释；`.lrc` 属主/mode 无法与发布语义核对。
- **禁止**：用 UI 显示歌词当作 pass；用"存在 .lrc"单独证明歌词正确；用行数相同代替内容比较。

### 3.5 重复位置（D）

**语义**（来自 JOB §4.5/§5.3、A5）：重复 `source_id` 的位置**必须逐位置保留**；第 2 个通常 `conflict`（`publish_noreplace` 不覆盖），若两条都 `published` 必须能用 basename 不同解释。账本 detail 不含 basename（候选），批次引擎内存项有 `basename`/`duplicate_of_position` 但**未进入账本 detail**（G-D1）。

**证据来源**：
- D-E1 清单：`source_fingerprint`、manifest entries（position/source_id，重复保留）；
- D-E2 结果：每个 position 的 state/reason（published/conflict/...）、`duplicate_of_position`（需保存时采集或由 manifest 分组推出）；
- D-E3 文件对应：独立目录清单 + 每个文件的 sha256/size（与账本绑定）；basename 清单（受控证据）；扫描器/独立视图的 `unique_match/no_match/ambiguous`（§3.5 的字节层，当前 unknown）；
- D-E4 冲突目标：若 `conflict`，需证明冲突文件确实存在且属于既有发布（可读 stat/hash + 账本前序记录），不得把本位置的文件冒充冲突文件。

**裁决表**：

| 场景 | 期望 | 判定 |
| --- | --- | --- |
| 第 1 次 published，第 2 次 conflict | 保留双位置；第 2 次不覆盖 | 两位置 `duplicate` 维度 pass（记录 conflict 证据与既有文件 hash；若既有文件归属不可知 → unknown） |
| 两条都 published 且 basename 不同 | 允许 | pass：记录两个 basename + 两个文件 hash；若字节相同导致扫描 `ambiguous`，但 basename 不同且数量吻合 → 记录 `distinct_basenames` 证据，仍可 pass（需 Q 决定是否接受） |
| 两条都 published 且 basename 相同 | 不可能（no-replace 会 conflict） | fail/unknown（实现/账本不一致） |
| 扫描 `ambiguous` 且重复数不吻合或 basename 无法解释 | 无法裁决 | unknown（不得 pass） |
| 缺失/跳过重复位置 | 违反合同 | fail |

**禁止**：把重复位置合并/去重；用"总数相同"代替逐位置；用 ambiguous 计数单独证明位置对应；把 conflict 当作失败（产物语义上允许）。

### 3.6 逐项失败（F）

**证据来源**：
- F-E1 账本/接口：`GET job` 的 position `state/reason/reason_message/cleanup_pending/detail`（detail 仅白名单）；固定文案来自合同 §6.3；失败码来自 route/账本（附录 C）。
- F-E2 一致性：刷新前后两次读取的 `reason/reason_message` 相同（A6 要求"刷新后仍显示具体固定原因"），两次读取分别存证（hash + 时间）。
- F-E3 归因：若需要，route 单曲结果（受控证据）或日志摘要（只允许固定码/阶段，不得含 URL/token/路径）。

**判据**：
- PASS（失败位置）：`state ∈ {rejected, failed, commit_unknown, cancelled}` 且 `reason` ∈ 固定集合、`reason_message` 与合同映射一致、刷新前后一致；`failure.stage/code` 与 reason 对应；无"部分成功"文案矛盾；整单状态符合 A7（有失败 → partial/completed_with_issues）。
- FAIL：reason 不在固定集合/文案不匹配/刷新后变化/整单标成功/失败位置却存在可疑落盘（需核对是否本位置文件）。
- UNKNOWN：账本不可读/候选未部署/固定映射未验证（Q：需要合同/实现核对）。
- **`commit_unknown` 专项**：必须保持 `reconciliation_required`、不自动重试、不按文件存在性判成功（A16）；schema 记录 `cleanup_pending=true` 与人工对账标记；不得 pass，只能 `blocked/unknown`（等待人工对账）。

**禁止**：把 UI 文案/截图当作唯一证据；用异常文本；用"文件存在"判失败位置成功；把 mock/离线构造当作真机失败证据。

### 3.7 索引与播放（I：飞牛音乐侧）

**要求**（runbook §6 + A4）：A4 通过后，在飞牛音乐界面定位测试库文件，确认封面/歌词/歌手/专辑显示并可播放；**截图/格式存在不能当通过**。因此需要**机器可读观测**。

**可接受证据方法（优先级）**：
1. **machine_api / network_payload**：飞牛音乐/文件索引的只读 API 或页面网络响应（JSON），记录条目 ID（opaque）、字段（title/artist/album/cover/lyric 的存在性）、库路径归属（用 ref 不写全路径）；保存原始响应到受控证据并 hash（不得含 Cookie/token）。
2. **dom_payload**：浏览器自动化读取 DOM 结构（结构化字段/属性），导出规范化 JSON；记录页面 URL（脱敏）/构建版本；需要同时记录"字段存在性"与"字段值"。
3. **manual_visual**：只能作为辅助（`machine_readable=false`），**不能**判 visible/advanced；只能记录 unknown 并报告需要更强观测。

**判据**：
- **PASS（index）**：`machine_readable=true` 且 `status=visible`，条目可绑定到测试库中的具体文件（hash/大小/条目 ID 与文件证据对应），显示字段与落盘标签一致（title/artist/album/cover/lyric 的存在性；值不一致 → fail）。
- **PASS（playback）**：`status=advanced` 且 `method` 为机器可读（`machine_api/network_payload/dom_payload`）、`machine_readable=true`、`position_advanced_seconds>0`（播放确实推进）、`ui_duration` 与 `ffprobe duration` 在容差内（建议 ±2s，理由：容器/展示舍入；容差需复审确认）、`duration_match="match"`、无 error_event；记录采样时间线；`limitations` 必须含 `cannot_prove_audible_output`。
- **FAIL**：条目不可见/字段错误/播放失败/时长显著不一致/播放器报错。
- **UNKNOWN/NOT RUN**：接口不可用、浏览器工具不可用、无 machine-readable 观测、fnOS 索引尚未完成；记录 NOT RUN 并报告，**不得推断成功**。
- **残余 unknown（必须在 `limitations` 声明）**：无法机器证明"扬声器实际发声/听感主观正确"；无法证明音频内容与网易云源一致（只能证明文件字节/标签/解码/时长）；无法证明索引的实时性/完整性。

**缺口**：G-I1（fnOS 音乐索引机制/API 未定位，需只读调查+授权）、G-I2（播放观测的机器接口/浏览器自动化能力与责任未定）、G-I3（索引/播放证据的保存与复核格式）。详见 §6。

### 3.8 维度判定矩阵（汇总）

| 维度 | PASS 的最低充分条件 | 常见 FAIL | 常见 UNKNOWN | 禁止的"伪证据" |
| --- | --- | --- | --- | --- |
| bytes（字节绑定/位置对应） | 独立重算文件 sha256/size = 账本 published 指纹；或独立视图扫描 `unique_match` 且位置/basename 证据充分 | 哈希/大小不一致；同名替换；ambiguous 无法解释 | 扫描器硬门未解除（当前）、视图不可信、账本指纹缺失 | 仅文件存在；仅 `unique_match` 无 basename；仅目录里有同名 |
| quality（实际档位） | Q-E1 reported + 绑定 + Q-E2 降档证据 + Q-E3/E4 无矛盾 + Q-E5 字节绑定 | 损失性 level vs 损失性容器；字节绑定矛盾 | 未 reported/未识别 token/逐档缺失/映射未验证 | 扩展名/码率/请求档/URL/type/br/sr/UI |
| tags | 源响应抽取 vs 落盘 ffprobe 精确一致（含 track/disc 可得的范围） | 字段不一致/缺失 | 源响应未采集；track/disc 不可得 | 文件名；tagged=True；UI 标题 |
| cover | 状态与落盘一致；可解码/尺寸/MIME 合法；（源可得时）源字节 hash 一致 | 声明有但无；有但声明无；hash 不一致 | 源字节未采集；提取失败 | 有 attached_pic 单独判 pass；截图 |
| lyric | 状态一致；严格 UTF-8；源字节 hash 一致 | 声明有但缺；内容不同 | 源未采集；仅规范化相等 | 存在 .lrc；行数相同；UI |
| duplicate | 每位置保留、冲突/双发布可解释、basename/字节证据 | 合并/缺位置/不可解释 ambiguous | basename 证据缺、扫描器 unknown | 总数相同；去重后核对 |
| index_play | machine-readable visible + 字段一致 + 播放推进 + 时长匹配 | 不可见/字段错/播放失败 | 无机器观测/NOT RUN | 截图；文件存在；格式存在 |
| failure | 固定码+文案+刷新一致+状态语义 | 非固定码/文案不符/整单误报成功 | 账本不可读/映射未验证 | 异常文本；UI 截图；猜测 |

## 4. 采集步骤草案（命令级）

> **治理前提**：以下全部是**草案**，本文件不执行。执行时必须：§0.1/§0.2 PASS + 当前会话授权 + 隔离测试库 + 不触碰生产曲库/账本/服务；只读命令与"需要隔离测试库写入"的动作分开标注；所有命令输出脱敏后进受控证据目录并 hash。

### 4.1 前置绑定（只读）

```bash
# 环境/版本（只读；不得输出 .env 全文/密钥）
uname -r; cat /usr/trim/etc/version
stat -c 'type=%F mode=%a owner=%U:%G size=%s dev=%d' -- "<testlib>" "<staging>"
findmnt -T "<testlib>" -no TARGET,SOURCE,FSTYPE,OPTIONS
sha256sum "<repo>/VERSION" 2>/dev/null; git -C "<repo>" rev-parse HEAD
# 部署版本/包哈希（若已受控部署）
sha256sum "<installed>/VERSION" "<installed>/repo/<关键文件>"
```

### 4.2 保存时采集（需要实现，草案接口；当前不存在）

建议新增"验收证据采集"边界（只增字段/只写受控证据，不改变保存语义）：

1. **provider 响应抽取**：在 quality ladder 每档、`/info`、`/lyric`、封面抓取处，抽取白名单字段 + 生成 `provider_response_extract`（含 `request_id/attempt_id`、song_id、tier、code/id/level/type/size/md5/freeTrialInfo 存在性、抽取物 hash、时间）；**不持久化 URL/完整响应**。
2. **逐档 attempts**：`_select_quality` 返回 attempts（tier→outcome），供 evidence set 使用；当前只有最终结果（G-Q1）。
3. **源封面/歌词快照**：源封面字节（或至少 sha256/MIME/宽高/来源 kind）与源歌词 UTF-8 字节 sha256/行数，绑定到 position/attempt。
4. **元数据快照**：`/info` 的 title/artist/album/track/disc/lyricist/composer 与 expected tagged metadata 一致快照（track/disc 当前不进 data，G-T1）。
5. **落盘后独立证据**：由采集脚本（非保存进程）在发布成功后读取文件，产出 `delivered_evidence` 与字节绑定；证据写入受控目录。
6. **失败/拒绝位置**：同样产出 `failure` 证据（账本行 + 固定码；不得含异常文本）。
7. **采集失败语义**：采集失败不得改变保存结果，但该 position 的 evidence 维度 = unknown；若采集本身不可用 → 整个 evidence set `blocked`/`unknown`，不得 pass。

### 4.3 落盘后只读复核命令（隔离测试库；需授权/门禁）

```bash
# 文件身份/哈希
stat -c 'mode=%a uid=%u gid=%g size=%s mtime_ns=%Y' -- "<file>"
sha256sum -- "<file>"
# 音频事实/解码（ffprobe/ffmpeg 只读源）
ffprobe -v error -print_format json -show_format -show_streams "<file>" > ffprobe.json
ffmpeg -v error -xerror -i "<file>" -map 0:a -f null - >/dev/null 2> decode.log; echo $?
# 内嵌封面提取与观测（派生文件只写隔离临时目录，不写生产）
ffmpeg -v error -i "<file>" -map 0:v:0 -c copy -f image2pipe - > cover.bin 2>/dev/null
file --brief --mime-type cover.bin; sha256sum cover.bin
# 标签（FLAC 专用可用 metaflac；通用用 ffprobe tags）
metaflac --list --export-tags-to=- "<file>" 2>/dev/null || true
# 歌词侧车（只读；编码/行数/hash）
file --brief --mime-encoding "<stem>.lrc"; sha256sum -- "<stem>.lrc"; wc -l < "<stem>.lrc"
# 目录清单（basename 级；受控证据内保留，公开报告脱敏）
find "<testlib>" -maxdepth 1 -type f -printf '%f\t%s\n' | sort
ls -A "<staging>"   # 期望空；若非空记录并停止
```

> 注意：`metaflac/flac` 工具是否存在于目标设备需先只读探测（`command -v`）；不得为安装工具而改设备。工具缺失 → 用 ffprobe 标签替代并记录工具边界。

### 4.4 字节绑定/位置对应（依赖 SCAN 设计；当前受限）

- 若已有**独立视图**（M2 send/receive 到无 `trimacl` 的 btrfs，或 M4 普通拷贝到独立 ext4；见 SCAN §7）：按 SCAN 设计的独立视图流程做整树/子集扫描，产出 `scanner_json`，填入 `byte_scan`；视图的 RO 强制/来源/清理证据必须一并保存。
- 若无独立视图：至少做**目录清单 + 直接文件哈希**（`find` + `sha256sum`）并记录稳定性检查（扫描前后 stat/清单 hash 一致）；该 profile 记为 `directory_listing`，此时 `view` 必须为 null/省略（**V-2：目录清单不是独立视图；不得填 snapshot/copy 等冒充独立视图**），`scanner` 记录本地清单工具，其局限（无快照/无 RO 强制/并发窗口）必须写入 `limitations`，且 **不能单独** 支撑 duplicate/ambiguous 裁决（§3.5）。
- `scripts/verify_position_file_evidence.py` 当前硬门 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`：**只能 unknown/exit 2**，`byte_scan.status` 填 `not_run/unknown`；不得把其输出冒充 `unique_match`。

### 4.5 索引/播放观测（飞牛音乐；只读）

1. 通过 DSH 浏览器（用户已登录飞牛桌面）打开飞牛音乐 → 测试库目录/媒体库。
2. 优先从页面网络响应/只读 API 捕获条目 JSON（不得包含 Cookie/token；只保留字段存在性与值摘要），生成 `ui_observation`（machine_readable=true）。
3. 若只能 DOM：导出结构化 DOM 字段（条目卡片的文本/属性）为 JSON；记录页面版本/URL（脱敏）。
4. 播放：点击/开始播放，采样 `currentTime` 至少两次（间隔 ≥5s），记录 UI 显示的时长；与 ffprobe 时长比对（容差 ±2s 建议，复审确认）；记录 error 事件。
5. 截图只能作为**辅助**（不做判定）；`manual_visual` 一律 `machine_readable=false` → unknown。
6. 无法观测 → `not_run`，A4 保持 BLOCKED，按 runbook 记录 NOT RUN。

### 4.6 证据归档与复核

- 生成 `evidence_set` 目录 + `manifest.sha256` + `record.json`（schema 校验通过）；关键原始产物（provider 抽取、ffprobe、decode log、cover bytes、lrc bytes、scanner json、ui/playback json）逐一 hash 并列入 manifest。
- 独立复审者按 §6.6 检查后把 `review` 填为 PASS/PASS-with-limits/BLOCKED；复审未完成 → `overall_verdict` 不得 pass。
- 证据集与报告分开：报告只引用 `ev_*`/摘要；敏感全文只进受控目录。

### 4.7 禁止操作

- 不得写生产曲库/暂存/账本；不得重启/停启服务；不得改配置/安装包；不得清空/递归删除；不得输出签名 URL/Cookie/token/绝对路径；不得用 mock/合成数据替代真机证据（合成测试只能用于 schema 校验，必须标 MOCK）；不得把"格式存在/截图/文件存在"当 pass。

## 5. 通过/失败/unknown 判据与反例

### 5.1 总判定规则（S1：与 INV-2/INV-3 统一）

- **position verdict 由 `expected_outcome` 决定**（不再用一套维度门槛套所有位置；见 INV-2）：
  - `expected_outcome=published`：`pass` 需 `bytes/quality/tags/index_play` 均 `pass`、`duplicate` 为 `pass|not_applicable`、`cover/lyric` 为 `pass|advisory`（Q5 决定是否设 gate）；任一 gate 维度 fail/unknown → 相应 fail/unknown。
  - `expected_outcome=conflict`：`state=conflict` + `duplicate_resolution=conflict_expected` + `dimensions.duplicate=pass` + `delivered_evidence=null` ⇒ `pass`（不覆盖是预期行为）。
  - `expected_outcome=failure`：`state∈{rejected,failed}` + 固定 reason/文案 + 刷新一致 + `failure` 合法 ⇒ `verdict=pass`（"预期失败已确认"）；`state=published` 或 reason 不符 ⇒ `fail`；`commit_unknown` ⇒ `blocked/unknown`（A16 人工对账）。
  - `expected_outcome=cancelled`：`state=cancelled` 且无该位置成功文件 ⇒ `pass`。
- **fail**：预期结果与实际不符、或适用 gate 维度 fail、或 `failure` 非法/缺失；`fixed_reasons` 必填（附录 C 固定码）。
- **unknown**：适用 gate 维度证据不足/无法判定；不得用"其他维度通过"抵消。
- **blocked**：外部门禁未满足（§0.1/§0.2/A4 前置、部署未包含候选、扫描器硬门、授权缺失）；blocked 不是 pass，也不是数据层 unknown 的替代。
- **overall**：按 INV-16 推导（任一 fail → fail；否则任一 blocked → blocked；否则任一 unknown → unknown；全 pass → pass）；不得把"部分完成"表述为整单成功（A7）。

### 5.2 反例表（什么**不能**算通过）

| # | 伪证据 | 为什么不行 | schema 对应约束 |
| --- | --- | --- | --- |
| X1 | 文件扩展名 `.flac`/`.mp3` | 扩展名可伪造/与实际容器不符；代码明确"仅后缀不构成格式证明" | `quality` 禁止用扩展名；`format` 只能来自 ffprobe |
| X2 | 码率/采样率/位深（ffprobe 或 UI） | 高参数不等于高档位；仓库无已验证档位映射 | quality 只认 Q-E1；码率仅作矛盾检查 |
| X3 | 请求档位 `quality_requested=jymaster` | 请求≠实际；服务端可能回落/未知 | schema 禁止用它回填 `reported_level` |
| X4 | URL/`type`/`br`/`sr`/`levelConfuse` | 响应字段是声明/可伪造/与档位无可靠映射 | 禁止进 quality 证据 |
| X5 | 文件存在/大小正确 | 不能证明内容、标签、封面、歌词、索引/播放 | `bytes` 需哈希绑定 + 其它维度 |
| X6 | `tagged=True` 或 route 自校验通过 | 同一进程/同一来源值的自证，不是独立验收 | tags 需源抽取 vs 落盘独立读数 |
| X7 | `cover_embedded=true` 或"有 attached_pic" | 不能证明封面来自该曲目/可解码/尺寸合规 | cover 需源字节 hash（可得时）+ 解码/尺寸 |
| X8 | `.lrc` 存在/行数相同 | 不能证明内容一致/编码正确 | lyric 需源字节 hash + 严格 UTF-8 |
| X9 | 账本 detail 自洽（quality_actual 有值） | detail 是候选、未部署未审查；且可能是同一写者自证 | L1 不足；需源抽取 + 独立观测 |
| X10 | 扫描器 `unique_match` | 扫描器当前硬门关、不输出名字；不能证明位置/basename | `byte_scan` 规则 + basename 独立证据 |
| X11 | UI/截图显示"已保存/封面/歌词" | 截图不是机器可读证据，且可能只反映缓存/UI 状态 | `manual_visual` 只能 unknown |
| X12 | 飞牛音乐里"能看到条目"但未播放 | 可见≠可播放 | playback 需推进+时长匹配 |
| X13 | 合成/mock 测试通过 | 不证明真机/真实音频/真实索引 | 合成数据只能 schema 校验并标 MOCK |
| X14 | "文件存在且大小>0"用于失败位置 | 失败位置可能残留/冲突文件，不代表本位置成功 | failure 需账本终态 + 归属证据 |
| X15 | `commit_unknown` 后按"文件存在"判成功 | 合同禁止（A16 冻结，人工对账） | verdict 只能 blocked/unknown |
| X16 | 库目录/文件属主/mode 与生产一致就够了 | 发布语义还需 no-replace/staging 空等 | 记录但不单独 pass |

### 5.3 判定样例（示意，非真实执行）

- **P1（正常发布，expected_outcome=published）**：state=published；`quality.claim_scope=provider_reported_not_independently_verified`、reported=jymaster + 逐档 attempts（jymaster available；无降档）→ Q-E3 size/md5 match + 文件 ffprobe flac/24bit/可解码 + 文件 sha256==账本 → tags 与源 /info 一致（含 track/disc）→ cover embedded 且源封面 hash 一致 → lyric published 且源歌词 hash 一致 → byte_scan（独立视图）unique_match 且 basename 独立对应 → index_play（machine_readable）visible + 播放推进 + 时长匹配 ⇒ `pass`（quality 标注 `provider_reported_not_independently_verified`）。
- **P2（降档）**：reported=lossless、requested=jymaster、fallback=true，attempts 显示 jymaster unavailable（明确无 URL/trial 标记）→ 其余同 P1 ⇒ `pass`（quality 记录降档理由；若 attempts 缺失 ⇒ schema 拒绝/`unknown`）。
- **P3（第 2 个重复位置，expected_outcome=conflict）**：state=conflict、reason=destination_conflict；`duplicate_resolution=conflict_expected`；文件系统中存在第 1 个位置发布的同名文件（hash 与第 1 位置账本一致）；本位置 `delivered_evidence=null`、`dimensions.duplicate=pass` ⇒ verdict `pass`（记录冲突归属）；若两条都 published 且 basename 不同且文件 hash 可绑定 ⇒ `duplicate_resolution=distinct_basenames` + duplicate pass（记录两个 basename）。
- **P4（预期失败位置，expected_outcome=failure）**：state=rejected、reason=no_quality_available、reason_message 固定文案、刷新前后一致、`failure` 合法 ⇒ `verdict=pass`（"预期失败已确认"，不是"保存成功"；`quality/bytes` 可为 `not_applicable`）；整体 job 为 partial，不标整单成功。若该位置意外 published ⇒ `fail`；`commit_unknown` ⇒ `blocked/unknown`。
- **P5（索引 NOT RUN）**：其它维度全 pass，但飞牛音乐机器观测不可用 ⇒ index_play=unknown/not_run ⇒ position 不能 pass；A4 保持 BLOCKED（runbook §6：记录 NOT RUN，不推断）。

### 5.4 不可外推边界（必须写进每个结论）

- 仅绑定本机（fnOS 1.2.0903 / 内核 `6.18.18.c1168-trim` / `/vol1` Btrfs + `trimacl`）与本次测试歌单/账号/权益；不证明其它版本/其它内核/其它挂载/上游一般行为。
- 不证明生产服务生命周期/身份/授权（§0.1/§0.2 仍 BLOCKED）；不证明接收端/持久化/隔离/恢复；不证明 NCM 解密/转换（仓库候选拒 NCM；上游 CLI 可下载但不等于产品链路）。
- 不证明音频"听感/主观音质/母带等价"；不证明飞牛音乐索引的实时性/完整性；不证明账号权益与 provider 声明真实性（只能记录 provider-reported）。
- 不证明扫描器/独立视图的不可变性（RO 位在本机不可靠；见 SCAN 文档与 BTRFS 证据）。

## 6. 缺口与衔接

### 6.1 实现/授权缺口（每维度）

| 编号 | 缺口 | 影响维度 | 需要实现/授权 | 当前状态 |
| --- | --- | --- | --- | --- |
| G-Q1 | 逐档 attempts 未持久化（`_select_quality` 只在内存，最终 data 只有选定结果） | quality（降档合法性） | 保存时采集中保留 attempts（tier/outcome/时间），只写受控证据 | 未实现 |
| G-Q2 | provider 响应抽取物未持久化（level/type/size/md5/freeTrialInfo/request/attempt 绑定） | quality/integrity | 保存时捕获白名单抽取 + hash（不存 URL/完整响应） | 未实现 |
| G-Q3 | 档位语义映射未验证（仓库无已验证"网易云 level→真实档位/母带"映射） | quality（绝对档位） | 用户决定是否接受 provider-reported 作为"实际档位"证据（Q1）；否则永远 unknown | 未决 |
| G-Q4 | 字节绑定依赖账本候选部署 + 扫描器/独立视图 | bytes/quality | 部署候选（门禁）、扫描器升级（SCAN 文档 G1–G9）、独立视图流程 | BLOCKED |
| G-T1 | `track/disc`（及部分 `lyricist/composer` 路径）未进入 route 最终 data/账本 detail | tags | 保存时采集快照（不要求进账本 detail） | 未实现 |
| G-T2 | `/info` 源元数据响应未持久化 | tags | 保存时抽取+hash | 未实现 |
| G-C1 | 源封面字节/尺寸/MIME（及 preserved 的源 attached_pic）未持久化 | cover | 保存时快照源封面信息；`-c copy` 字节一致性需 P0 实测 | 未实现 |
| G-C2 | 封面"状态一致"之外是否要求"成功"未定（产品允许元数据失败） | cover 判定 | 用户决定 Q5 | 未决 |
| G-L1 | 源歌词字节/hash/行数未持久化 | lyric | 保存时快照 UTF-8 字节 hash/行数 | 未实现 |
| G-L2 | `.lrc` 属主/mode/发布路径语义（sidecar 经 store 发布）未纳入 detail/证据 | lyric | 采集文件 stat + 与目标目录样本对比 | 未实现 |
| G-D1 | 重复位置 basename/`duplicate_of_position` 不进账本 detail；冲突归属证据缺 | duplicate | 保存时采集（route result 的内存字段）或独立目录/哈希匹配；扫描器可用后补 ambiguous 裁决 | 未实现 |
| G-F1 | 失败位置逐项固定码/文案/刷新一致性未形成证据记录 | failure | 采集 `GET job` 两次读数 + route 单曲结果（受控） | 未实现 |
| G-I1 | 飞牛音乐索引机制/只读 API 未定位 | index | 只读调查（需授权）：应用/服务/DB/网络 | unknown |
| G-I2 | 播放观测机器接口/浏览器自动化能力/责任未定 | playback | 授权 + 设计（DOM/网络/媒体元素采样） | unknown |
| G-I3 | 索引/播放证据格式与复核流程未定 | index/play | 本文 §2.4.5 草案 + 复审 | 草案 |
| G-X1 | 证据存储位置/保留期/访问控制/销毁策略未定 | 全部 | 用户决定 Q6 + 设计评审 | 未决 |
| G-X2 | 保存时采集的产品代码改动未实现 | 全部 | 独立实现任务（默认关闭、不改变保存语义、A13 脱敏约束） | 未实现 |
| G-X3 | schema Rev-3 已修 V-1/V-2 与绑定；仍需 **Rev-3 复审** + INV 语义测试（INV-1..23；V-3：位置唯一/汇总计数/expected_outcome↔verdict/published failure=null 等只能靠实现侧测试） | 全部 | 复审 Rev-3（V-1/V-2 闭环）+ Draft 2020-12 校验器 + INV 测试 | Rev-3 已修订、待复审 |
| G-X4 | §0.1/§0.2/P7 前置仍 BLOCKED；部署版本不含候选 | 全部 | 部署/身份/生命周期门禁（另见检查单） | BLOCKED |

### 6.2 需要用户决定的事项

| # | 决定 | 选项 | 影响 |
| --- | --- | --- | --- |
| Q1 | "实际档位"证据强度 | (a) 接受 provider-reported `data.level` + 绑定 + 无矛盾 作为 A4 的档位证据（标注 `not_independently_verified`）；(b) 要求独立 witness（外部主机/厂商文档/信号分析）才接受 | (a) 可在现有能力内做到；但"超清母带"仍只是 provider 声明；(b) 当前无法满足 → quality 永远 unknown |
| Q2 | 降档依据 | (a) 接受 delivery guard 的"明确 unavailable/trial 标记"作为"账号无更高档可用"的证据；(b) 要求独立的账号权益证据 | (b) 现有接口不提供，需新的只读权益查询（未知/未授权） |
| Q3 | 保存时采集授权 | 是否允许新增"证据采集"代码（读 provider 响应抽取、源封面/歌词快照、写受控证据目录）并接受其存储/保留 | 不授权 → A4 无法 machine-check，保持 BLOCKED |
| Q4 | 索引/播放调查 | 是否授权对飞牛音乐索引/播放做**只读**接口/DB/浏览器观测调查 | 不授权 → index_play 永远 NOT RUN → A4 BLOCKED |
| Q5 | 封面/歌词缺失算不算 A4 失败 | (a) 只要求"与账本/明细状态一致"（产品允许元数据失败）；(b) 要求成功（必须 embedded/published） | 影响 position pass 门槛与产品语义解释 |
| Q6 | 证据保留/脱敏/复核 | 证据目录位置、保留期、销毁、谁可读、basename/title 等敏感度 | 不决定无法归档与复审 |
| Q7 | NCM/转换边界 | 是否会在验收中出现 NCM 输入？（当前候选拒 NCM；`MANUAL_IMPORT_PLAN` §7 要求真正 FLAC） | 若出现 → 必须 fail/blocked，禁止伪装 FLAC |

### 6.3 与 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` 的衔接

- 本文的 `bytes` 维度是 SCAN 文档 §7（推荐组合 L1–L5、M1–M4、G1–G9）的**使用方**：`byte_scan.profile` 对应 SCAN 的视图类型（independent_view/snapshot/copy/directory_listing）；`view.ro_enforcement` 必须如实标注 M1/M2/M3/M4 的验证状态。
- **当前 `scripts/verify_position_file_evidence.py` 硬门 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False` 未解除** → 扫描器只能 `unknown/exit 2`；因此 `bytes` gate 在扫描器升级或独立视图可用前不能 pass；本文不改变该硬门。
- RO 位/快照不可作为不可变证据（见 BTRFS 证据与 SCAN §7.2/§7.4）；M2（send/receive 到无 `trimacl` btrfs）或 M4（普通拷贝到独立 ext4）是更稳的独立视图候选；M3 reflink 只作抽检（同 fs 坏块/权限模型）。
- 长扫描期的一致性证据（generation bracket/独立视图/重读）必须按 SCAN 设计采集，不得在本 schema 中以"未验证的不可变"字段冒充。

### 6.4 与 BTRFS 证据文档的衔接

- `docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`：RO 位=1 仍可写（本机）；`docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md`：根因是 `trimacl` 挂载选项；M1 RO 挂载恢复视图强制但 RW 路径仍可写；M2/M4 为独立视图。
- 本文据此：`byte_scan.view.ro_enforcement` 只允许 `verified`（有真实 EROFS 证据）/`unverified`/`not_applicable`；不得写"不可变"；结论不外推到其它 fnOS/内核/挂载。
- 证据窗口与"当前实际文件"的关系：按 SCAN §7.7 的窗口终止边界执行；本文的 `timestamps`/`verified_at` 与 `byte_scan.limitations` 必须记录窗口。

### 6.5 与 P7 runbook / 合同 / 检查单的衔接

- 对应 A4（逐位置核对）、A5（重复位置）、A6（失败可见）、A13（脱敏）、A16（commit_unknown）；A1–A3/A7–A12/A14–A15 的既有证据仍按 runbook，本 schema 不替代。
- **不改变** §0.1/§0.2 门禁；部署版本 2.10.2 不含候选代码（见 `docs/DEVICE_INVENTORY_20261107.md`），P7 无法开始；本文不构成部署授权。
- 若要把本文 schema 写入 A4 判据，需要单独修改 runbook/合同并独立审查（本文只给草案与缺口）。

### 6.6 独立复审要求（schema 与后续实现）

- schema 本身：字段完整性/枚举/必填、语义不变量（INV-1..23，含 S1–S4 的 fail-closed 条款）、schema 的 Draft 2020-12 meta-schema 校验（重跑验证）、是否把"不可证明源"误设为 pass、脱敏与哈希绑定、fail-closed 语义、`expected_outcome`/`duplicate_resolution`/`claim_scope` 的一致性。
- 采集实现：是否只新增受控证据、不改变保存语义/账本/UI、不引入网络/路径泄露；字段抽取是否与代码语义一致（quality/format/tag/cover/lyric 的现有语义）；`-c copy` 字节一致性、ffprobe 工具/版本、工具缺失时的降级是否诚实；规范化（`utf8-sorted-json-v1`）实现与 `record_sha256/manifest.sha256` 是否可复算。
- 真实证据：每类证据是否与原始产物逐项一致；是否用 mock/合成冒充真机；索引/播放是否为机器可读证据；是否过度声称"档位/听感/索引成功"。
- 与 SCAN/BTRFS 证据的引用是否诚实（RO 位不可依赖、扫描器 unknown、不可外推）。

## 7. 状态声明

- 本文是**草案/提案**：未实现、未执行、未部署；独立复审历史：初稿 `docs/ACCEPTANCE_AND_PERMISSION_REVIEW_20261107.md` 判 **PASS-with-limits**（S1–S8）；Rev-2 `docs/ACCEPTANCE_SCHEMA_REV2_REVIEW_20261107.md` 判 **PASS-with-limits（无阻断）**（S1–S3 与 schema 合法性 PASS，V-1/V-2 需修）；本 Rev-3 已修 V-1（UUID 32hex）与 V-2（directory_listing 豁免 view）、同步绑定与附录 D/E；**Rev-3 仍需复审确认**。本文不表示任何验收通过，也不改变 P7 A4/A5/A6 的 BLOCKED 状态、§0.1/§0.2 门禁或任何上线授权。
- 本文只修改本文档（Rev-3）；未访问设备、未修改代码/配置/服务、未写入 `/vol1`、未执行任何采集命令；文中命令均为待授权的草案。
- 下一步（若用户批准）：Rev-3 复审（V-1/V-2 闭环；S4 不可 schema 化部分转 INV 测试）→ schema 定稿 → 补齐 G-Q1/G-Q2/G-T1/G-T2/G-C1/G-L1/G-D1/G-F1 的保存时采集设计（新任务）→ 定位 G-I1/G-I2 索引/播放机制（只读调查）→ 与 SCAN 文档的独立视图/扫描器升级对齐 → 才能讨论 P7 A4 的 machine-checkable 证据采集与验收。

## 附录 A：本文引用的关键代码位置（现状事实；实现前须复核哈希）

| 主题 | 位置（写作时） | 说明 |
| --- | --- | --- |
| 音质阶梯/逐档判定 | `proxy/manual_save_route.py:1458-1492`（`_select_quality`）、`:1411-1455`（`_evaluate_quality_response`）、`:1401-1408`（`_reported_quality_level`） | `data.level` reported/unknown；trial/404/身份校验；降档仅"明确证据" |
| 质量字段 | `manual_save_route.py:734-737`、`_success_data :1555-1593`；`webui_gateway.py:854-936`（sanitize） | `quality_requested/actual/actual_status/fallback`；sanitize 白名单 |
| 元数据抓取 | `manual_save_route.py:876-924`（`_fetch_song_info`） | title/artist/album/track(number)/disc(number)/lyricist/composer/cover_url |
| 歌词 | `manual_save_route.py:945-976`（`_fetch_lyric_bytes`） | lyric/tlyric → UTF-8 + `\n`，≤1 MiB |
| 封面 | `manual_save_route.py:641-668,704-724,1216-1239,2290-2307`；`MANUAL_SAVE_METADATA_DELIVERY.md` | preserved/embedded/unavailable/unsupported/error；p1/p2 白名单；尺寸/解码校验 |
| 标签写入/校验 | `manual_save_route.py:2310-2353`（tag）、`:2214-2287`（`validate_tagged`/`_validate_tagged_metadata`） | ffmpeg `-c copy` + metadata；track/disc 别名比较；attached_pic |
| 文件格式 | `manual_save_route.py:1513-1535`（`_probed_audio_format`） | 仅 flac/mp3；来自 ffprobe `format_name` |
| 下载字节指纹 | `manual_save_route.py:768-801` | `published_size/sha256` 是对送入 staging 的 tagged 字节流，不是落盘回读 |
| 账本 detail 白名单 | `proxy/manual_save_jobs.py:140-145`；`proxy/manual_save_playlist.py:280-313` | detail 仅 10+2 键；basename/track/disc/md5/路径不在内 |
| 批次 item 字段 | `proxy/manual_save_playlist.py:66-90,316-343` | basename/duplicate_of_position 在内存 item，非账本 detail |
| 失败码/文案 | `proxy/manual_save_playlist.py:95-150`；`manual_save_route.py:162-183`；合同 §5.2/§6.3 | 固定码/固定文案 |

## 附录 B：当前可用字段真值表（"哪里有什么、哪里缺什么"）

| 字段 | route 单曲结果 | 批次内存 item | 账本 detail | admin `GET job` | 可否用于 A4 |
| --- | --- | --- | --- | --- | --- |
| title/artist/album | 有 | 有（title/artist） | 有（3 个） | detail | 需源响应对照（G-T2） |
| track/disc | 无（只进 tag metadata） | 无 | 无 | 无 | 缺（G-T1） |
| lyricist/composer | 有（含 status） | 无 | 无 | 无 | 缺（G-T1） |
| quality_requested | 有 | 有 | 无 | 无 | 可作"请求"记录，非实际 |
| quality_actual/status/fallback | 有 | 有（actual/status/fallback） | 有（actual/status） | 无 fallback | 需响应抽取（G-Q2） |
| 逐档 attempts | 无 | 无 | 无 | 无 | 缺（G-Q1） |
| format | 有（ffprobe） | 有 | 有 | detail | 可，但非档位证明 |
| size/md5 | 有（下载字节） | 有 | 无 | 无 | 需采集（G-Q2） |
| published_size/sha256 | 有（发布成功） | 有 | 有 | detail | 可作字节绑定（需独立重算） |
| basename | 有 | 有 | 无 | 无 | 缺（G-D1）；可用目录/哈希匹配补 |
| cover_status/error/embedded | 有 | 有 | 有（status/embedded） | detail | 需源字节（G-C1） |
| cover 源字节/宽高/MIME | 无 | 无 | 无 | 无 | 缺（G-C1） |
| lyric_status | 有 | 有 | 有 | detail | 一致性可；内容对照缺源（G-L1） |
| lyric 源字节/hash/行数 | 无 | 无 | 无 | 无 | 缺（G-L1） |
| duplicate_of_position | 无 | 有 | 无 | 无 | 缺（G-D1） |
| failure reason/message | 有（单曲） | 有（code/message） | 有（reason） | detail+reason_message | 可用（需刷新一致性采集 G-F1） |

## 附录 C：固定枚举/失败码（供 schema 引用；以代码/合同为准）

- **position state**：`published/conflict/rejected/failed/commit_unknown/cancelled/queued/running/not_executed`（账本 `_POSITION_STATES`；以代码为准）。
- **route/批次失败码**：`service_unavailable/no_quality_available/quality_unverified/quality_identity_mismatch/trial_only/download_target_untrusted/download_failed/integrity_mismatch/invalid_audio/tag_failed/conflict/disabled/config_invalid/busy/staging_unavailable/publish_failed/internal/invalid_song_id/too_many_positions/budget_exhausted/not_executed`。
- **账本 reason 扩展**（P1 已批准，见合同 §5.5）：`service_unavailable/no_quality_available/quality_unverified/quality_identity_mismatch/trial_only/download_target_untrusted/download_failed/integrity_mismatch/invalid_audio/tag_failed/disabled/config_invalid/busy` 等固定串。
- **cover_status**：`preserved/embedded/unavailable/unsupported/error/not_published/not_attempted`；**cover_error**：`host_untrusted/url_invalid/address_untrusted/http_error/payload_invalid/decode_failed/embed_failed/unknown`。
- **lyric_status**：`published/unavailable/error/conflict/not_attempted/unknown`。
- **quality_actual_status**：`reported/unknown`；**quality.claim_scope**：固定 `provider_reported_not_independently_verified`（S2）。
- **expected_outcome**：`published/conflict/failure/cancelled`（position 预期结果，S1）。
- **verdict.duplicate_resolution**：`null/conflict_expected/distinct_basenames/ambiguous_expected/unresolved/not_applicable/unknown`（S1；`conflict` 态只允许 `conflict_expected/unresolved`）。
- **reason_code 固定集合（schema `$defs.reason_code`）** = 上方 route/批次失败码 + 账本 reason 扩展 + 下方验收层固定码；`failure.code` 与 `verdict.fixed_reasons[]` 只能引用该集合（S4）。实现时必须从代码常量核对，不得凭本文抄写。
- **验收层固定码（草案，需复审后实现）**：`evidence_missing_quality_claim/evidence_quality_contradiction/evidence_tag_mismatch/evidence_cover_mismatch/evidence_lyric_mismatch/evidence_basename_unresolved/evidence_byte_binding_unavailable/evidence_scanner_unknown/evidence_index_unverified/evidence_playback_unverified/evidence_capture_failed/evidence_redaction_violation/evidence_schema_invalid`。
- **注意**：以上枚举随代码演进；实现时必须从代码常量生成/核对，不得凭本文抄写。本文不修改代码/枚举。

## 附录 D：Rev-2/Rev-3 修订记录（复审 S1–S8 与 V-1/V-2/V-3；本表供复审者逐项核对）

| 发现 | 严重度 | 修订位置（本文） | 修法摘要 |
| --- | --- | --- | --- |
| **S1**（不变量与 schema/判据互斥） | 重要 | §2.3、§2.4.7、§2.5（`position.expected_outcome`/`verdict.duplicate_resolution`/`conflict` if/then）、§2.6（INV-2/3/10/11）、§5.1、§5.3（P1/P3/P4） | 引入 `expected_outcome` 统一 verdict 语义；`duplicate_resolution` 进入 schema 枚举并由 if/then 约束 `conflict`；INV-2/3 与 §5.1/§5.3 对齐（预期失败=pass 确认，非 fail）；INV-11 与 §3.7 统一为"机器可读 + advanced 必要条件" |
| **S2**（`claim_scope` 无 schema 字段） | 重要 | §2.3 表、§2.4.2 表、§2.5 `source_evidence.quality.claim_scope`、§3.1、INV（约束） | 增加必填 `claim_scope="provider_reported_not_independently_verified"`，使 provider-reported 边界可机器表达 |
| **S3**（播放缺 method/machine_readable，截图未强制） | 重要 | §2.4.5、§2.5 `index_play.playback`、§3.7、INV-11/21 | playback 增加 `method`/`machine_readable`；`manual_visual`/`machine_readable=false` 由 if/then 强制只能 `unknown/not_run`；`limitations` 用 `contains: cannot_prove_audible_output` 强制 |
| **S4**（fail-closed 不变量缺） | 重要 | §2.6 INV-15..INV-23；§2.5 的 if/then（published⇒delivered、失败态⇒failure、fallback⇒attempts、profile⇒scanner+view、failure/fixed_reasons⇒固定码 enum、summary 必填 blocked） | 新增位置唯一/总数一致、汇总计数与 overall 推导、state↔failure/delivered、fallback→attempts、固定码集合、视图/扫描器绑定、播放机器可读、绑定可得性、预期≠实际等 fail-closed 条款 |
| **S5**（view 结构未定义） | 次要 | §2.4.4、§2.5 `$defs.view`/`$defs.scanner`、INV-20 | 定义 view `kind/identity_ref/provenance_ref/tree_uuid/parent_uuid/generation/ro_enforcement/view_refs/cleanup_ref`；`ro_enforcement=verified` 要求 view_refs |
| **S6**（`bits_per_raw_sample` min 1 冲突） | 次要 | §2.4.3、§2.5 delivered.audio | 允许 `0`（ffprobe 未知/N/A），避免把真实 0 判为非法 |
| **S7**（record/manifest 规范化未定义） | 次要 | §2.7 新增 `utf8-sorted-json-v1`；INV-13；`redaction.canonicalization_version` | 定义对象键排序/UTF-8/数值形式/数组保序；`record_sha256` 与 `manifest.sha256` 可复算规则 |
| **S8**（git_head 不可得/source_fingerprint 派生；2 处输入绑定过期） | 次要 | §2.3/§2.5 `environment.deployed`、`job.source_fingerprint_origin`、INV-22；§1.2 绑定表 | `deployed` 增加 `provenance_status` 并允许 `git_head/package_sha256` 为 null；`source_fingerprint_origin` 显式化并约束 unknown⇒null；SCAN/RO/generation/Round-2 绑定更新为当前磁盘哈希 |
| **V-1**（view UUID 用 64hex，与真实 Btrfs UUID 16 B=32hex 不符） | 中等（Rev-2 复审） | §2.4.4 view 定义与规则、§2.5 `$defs.view.tree_uuid/parent_uuid`、INV-20、§4.4 边界 | 改为 **32hex 原始 UUID**（与扫描器 `_read_btrfs_subvol_info` 读取 16 B 一致）；明确"不是 SHA-256；若需摘要另设 `tree_uuid_sha256` 并写规则"；负例实测 32hex 通过/64hex 拒绝 |
| **V-2**（`directory_listing` 被强制要求 view，但 view.kind 无该类型且 §4.4 本应 view=null） | 中等（Rev-2 复审） | §2.4.4 规则、§2.5 `byte_scan` if/then、INV-20、§4.4 | `profile=directory_listing` 改为：`scanner` 必填、`view` 必须 null/省略（目录清单不是独立视图）；保留 `profile∈{independent_view,snapshot,copy}` 的 scanner+view 要求；负例：directory_listing+snapshot view 被拒（假称独立视图） |
| **V-3**（S4 不可 schema 化条款仍无 INV 测试） | 次要（Rev-2 复审） | §2.6 注、§6.1 G-X3、§7 | 明确归入**实现侧 G-X3 语义测试**（位置唯一/汇总计数/expected_outcome↔verdict/published failure=null/snapshot_response⇒64hex 等），不是 schema 缺陷；测试实现前不称"S4 已机器闭环" |
| **V-0**（Rev-2 中间态绑定） | 信息 | §1.2 表（以 Rev-2 最终 `7a0c8cdd…`/115,449 B 与 Rev-3 最终哈希为准） | 记录中间态 `f4ea94a1…` 已弃用；下游引用 Rev-3 最终哈希/戳 |

## 附录 E：Rev-3 后的复核命令（本文件本身，不执行设备操作）

```text
# 1) 抽取 ```json 围栏 → JSON 解析（无重复键）
# 2) Draft 2020-12 meta-schema：jsonschema.validators.Draft202012Validator.check_schema(schema)
# 3) $ref 遍历：全部指向 #/$defs，可解析
# 4) 负例（Rev-3 必测）：
#    - view.tree_uuid/parent_uuid：32hex 实例通过；64hex 实例被拒（V-1）
#    - byte_scan profile=directory_listing：view=null + scanner 非空 → 通过；
#      配 snapshot/copy 等 view → 被拒（V-2，假称独立视图）
#    - profile∈{independent_view,snapshot,copy} 缺 view/scanner → 被拒
#    - {} / 缺 required / 非法 enum / manual_visual+advanced / fallback 缺 attempts → 被拒
# 5) 本文件 DOC-HASH-STAMP：sha256(该行之前全部字节, UTF-8)
```

<!-- DOC-HASH-STAMP: sha256(all bytes before this line, UTF-8) = ef8648237744c5af7194f4ce92afeee24ad74b14c8743998d21fc2d3a322722d -->
