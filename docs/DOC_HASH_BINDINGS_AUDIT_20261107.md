# 文档哈希绑定与自校验戳一致性审计（2026-11-07）

> **只读审计报告。** 目的：在多轮修订（Rev-1 → Rev-3、F1–F12 → N1–N7 → R1–R6 → V-1/V-2 等）之后，一次性核对仓库内**所有**文档声称的哈希是否仍与磁盘一致，并逐个重算每份文档的自校验戳。它不是验收、不改变任何门禁、不访问设备。
> 执行者：Lead。执行时间：2026-11-07 13:05–13:08（本地）。

## 1. 为什么需要它

本项目每条证据都靠 SHA-256 绑定（证据文档 ↔ 原始日志 ↔ 脚本 ↔ 复审报告）。但同一份文档会被复审要求改多轮，于是出现两类风险：

1. **陈旧绑定**：文档里写着"依据 X（`abc…`）"，而 X 已经改过，`abc…` 不再对应磁盘。
2. **自校验戳失效**：文档末尾声明 `sha256(该行之前全部字节) = <hex>`，若改完正文忘了重算，戳就是假的——而复审者通常直接引用该戳。

本次审计用脚本机械核对这两类，而不是靠人读。

## 2. 方法与脚本

- 脚本：`scripts/audit_doc_hash_bindings.py`，SHA-256 `956f4430a734038f5f829a479c89a57d8ae6226df4ea761a428801e14e811d52`（7,281 B），仅标准库、只读。
- 被索引文件：仓库内 `.md/.py/.sh/.json/.txt/.service/.example/.in` 与 `VERSION`（跳过 `.git`/`__pycache__`/`.pytest_cache`/`node_modules` 等）。
- 对 74 份 `.md` 中出现的每个 64-hex token 分四类：
  - `active-binding`：token 等于某个被索引文件的当前整文件 SHA-256；
  - `valid-stamp`：token 是文档声明的自校验戳，且**重算前缀哈希一致**；
  - `STALE-STAMP`：声明了戳但重算不一致（应当为 0，否则退出码 1）；
  - `historical`：既不等于当前任何文件哈希、也不是有效戳——预期存在（历史版本哈希、"已作废"注记、NAS 侧未入库产物哈希、被引用的其他文档的戳值）。
- 复现：`python scripts/audit_doc_hash_bindings.py --root . --json-out report.json`。

## 3. 结果（本次运行）

| 指标 | 值 |
| --- | --- |
| 索引文件数 | 295 |
| 扫描文档数（`.md`） | 74 |
| **有效绑定**（token = 当前文件哈希） | **164** |
| **有效自校验戳**（重算一致） | **13** |
| **失效自校验戳** | **0** |
| 历史/外部 token | 202 |
| 重复内容文件组 | 0 |

**结论：仓库内 13 个自校验戳全部重算通过，没有失效戳；164 条跨文档哈希绑定指向当前磁盘的真实文件。**

## 4. 13 个自校验戳（全部有效）

| 文档 | 行 | 名称 | 声明值（前 16 位） |
| --- | --- | --- | --- |
| `docs/ACCEPTANCE_AND_PERMISSION_REVIEW_20261107.md` | 201 | REPORT-HASH-STAMP | `3410b42713e8a8f4` |
| `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md` | 1187 | DOC-HASH-STAMP | `ef8648237744c5af` |
| `docs/ACCEPTANCE_SCHEMA_REV2_REVIEW_20261107.md` | 125 | REPORT-HASH-STAMP | `a413baad72c33c86` |
| `docs/ACCEPTANCE_SCHEMA_REV3_REVIEW_20261107.md` | 109 | REPORT-HASH-STAMP | `737fe0547ed08854` |
| `docs/BTRFS_EVIDENCE_ROUND2_REVIEW_20261107.md` | 303 | REPORT-HASH-STAMP | `43772733cf8779c7` |
| `docs/BTRFS_EVIDENCE_ROUND2_REVIEW_20261107.md` | 390 | REPORT-HASH-STAMP-R2 | `e9cbac06337b6a8b` |
| `docs/BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW_20261107.md` | 309 | REPORT-HASH-STAMP | `9d3318e2c314209c` |
| `docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md` | 1834 | REV3-HASH-STAMP | `f0eca77253871ad1` |
| `docs/LIFECYCLE_RUNBOOK_REV2_REVIEW_20261107.md` | 142 | REPORT-HASH-STAMP | `6890dfc28616ad80` |
| `docs/LIFECYCLE_RUNBOOK_REVIEW_20261107.md` | 202 | REPORT-HASH-STAMP | `74f9171fed0463c5` |
| `docs/SCANNER_CANDIDATE_REVIEW_20261107.md` | 108 | REPORT-HASH-STAMP | `37216466303e0cf5` |
| `docs/SCANNER_NATIVE_SMOKE_REVIEW_20261107.md` | 126 | REPORT-HASH-STAMP | `5f9438fc423bd0c6` |
| `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` | 749 | DOC-HASH-STAMP | `96a7e092078f9479` |

> 注意两种语义：整文件哈希（含戳行）与戳值（**不含**戳行）必然不同。引用时须写清是哪一个，例如 `LIFECYCLE_RUNBOOK_REVIEW` 整文件 `17547232…`、其内部戳 `74f9171f…`。

## 5. 202 个历史/外部 token 的分类

| 类别 | 数量（约） | 说明 | 是否需要处理 |
| --- | --- | --- | --- |
| 历史版本哈希 | ~60 | 文档明确标注"已作废/中间态/Rev-1/Rev-2"，如 `38df19de…`（schema 初稿）、`7a0c8cdd…`（Rev-2）、`f4ea94a1…`（Rev-2 中间态）、`76867809…`（runbook Rev-2）、`91f65503…`（RO 文档旧版） | 否，属审计留痕，复审已接受 |
| 被引用的"戳值" | ~46 行含 STAMP/HASH 字样 | 复审报告引用被审文档的戳值（该值本就不等于整文件哈希） | 否，语义正确 |
| NAS/包侧产物哈希 | ~90 | `SCANNER_NATIVE_SMOKE`（47）、`BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW`（22）、`DEVICE_INVENTORY`（17）、`G0_OBSERVATION_RUNBOOK`（13）等：FPK/容器/探针树/各次 stdout 的哈希，**从未复制进仓库** | 否，但建议后续把关键产物归档到 `docs/evidence/`（已有 20261107 目录先例） |
| 前缀哈希 | 少数 | 如 `ae7bc37f…`（设计文档前 89,481 字节），用于证明"§0–§6 未改动" | 否，方法本身正确 |

**没有发现"写错/抄错"的可疑 token。** 未匹配不等于错误：本工具刻意不把历史留痕判为失败。

## 6. 局限

1. 只识别**行内** 64-hex（大小写敏感为小写）；大写十六进制、短哈希前缀（如 `341dd4f4…` 省略形式）不参与匹配。
2. 自校验戳只识别形如 `<!-- <NAME>HASH-STAMP ... = <hex> -->` 的注释行；写在正文句子里的戳值会被归入 `historical`（语义仍正确，但不会被重算验证）。
3. 不验证"绑定是否指向**语义正确**的文件"——只验证"这个哈希是否存在且等于某文件"。指错文件的绑定（哈希正确但语义错）需人工/复审判断。
4. 历史 token 的分类（历史版本/戳值/外部产物）由人工归纳，脚本只做存在性判定。
5. 审计反映**运行时刻**的磁盘状态；任何后续编辑都可能使其过期，需重跑。

## 7. 建议（供后续收口）

1. 把本审计脚本纳入收口检查（每次大改后运行，`STALE-STAMP` 必须为 0）。
2. 关键 NAS 侧产物（各次 `result.json`、`stdout` 哈希）逐步归档到 `docs/evidence/20261107/` 并在 `MANIFEST.md` 登记，减少"外部 token"比例。
3. 引用哈希时统一写"整文件 vs 戳值"两种语义，避免复审混淆（已有多份文档踩过）。
