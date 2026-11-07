# P1 验收证据 Schema Rev-2 独立复审（2026-11-07）

> **总体 verdict：PASS-with-limits（无阻断项）。**
> Rev-2 对上一轮 S1–S4 的**规范层互斥已实质修复**：`expected_outcome` 统一了 verdict 语义（INV-2/3/10/11 与 §5.1/§5.3 P4 对齐），`claim_scope` 进入必填 schema 字段（S2 闭环），`playback` 增加 `method/machine_readable` 并由 `if/then`+`contains` 强制（S3 闭环），S4 的 `published⇒delivered`、失败态⇒`failure`、`fallback⇒attempts`、固定 `reason_code`、`profile⇒scanner+view` 已由 schema `if/then`/`enum` 强制（本复审以负例实测）；S6–S8 处理可接受；内嵌 schema 仍是合法 Draft 2020-12（meta-schema 通过、无重复键、全部 `$ref` 可解析）；§1.2 输入绑定与**当前磁盘**一致。
> **剩余（不阻断但需 Rev-3/后续任务）**：①S5 的 `view` 结构有两个实质缺陷：`tree_uuid/parent_uuid` 用 64hex 但真实 Btrfs UUID 是 16 B（32hex）；`directory_listing` profile 被 schema 强制要求 `view`，而 `view.kind` 无 `directory_listing`，与 §4.4 的"无独立视图时用 directory_listing"矛盾（V-1/V-2）；②S4 中"位置唯一/汇总计数/expected_outcome↔verdict/发布态 failure=null"等无法用 JSON Schema 表达，只有 INV-15/16/17/23 文本，**INV 测试（G-X3）仍未实现**（V-3）；③初始任务绑定 `f4ea94a1…`/115,392 B 为中间态，Lead 已确认以最终磁盘 `7a0c8cdd…`/115,449 B 为准（V-0，仅记录）。

## 0. 抬头、对象与方法

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（JST 约 12:00–12:18） |
| 复审者 | `final-reviewer`（未参与 Rev-2 撰写；只新增本报告） |
| **被审最终对象** | `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md`，115,449 B，1,171 行，SHA-256 `7a0c8cdd273e253b4ec840656c0f2f67640709d82e80a95bb98f99082481dc9f`；文末 `DOC-HASH-STAMP` = `0bc7722a43a20e405c40a24339200c05302743ce82fc7c9d43035f8faa034ee6`（本复审复算匹配） |
| 初始任务绑定（已弃用） | `f4ea94a1a317c9b878c2977f970c72a9e0ed07ae8f1ec4d34f59b8ec2dc4f28f`（115,392 B，Rev-2 中间态）；Lead 确认以最终磁盘版本为准，中间态未在磁盘保留、未复审（V-0） |
| 上一版审计依据 | `docs/ACCEPTANCE_AND_PERMISSION_REVIEW_20261107.md`，30,092 B，`cd39c37216cbbe887d562775399ed829fffe8f7318a46770f3d19f2734f00173`（S1–S8） |
| 复审方式 | 本地只读：重算哈希/自校验戳/行数；抽取内嵌 JSON 并做 `Draft202012Validator.check_schema`、JSON 重复键、`$ref` 解析、required/enum/if-then 负例实测；逐条读 §1.2 绑定并重算当前磁盘；未访问设备、未改被审文档/代码 |
| 不在范围 | 不实现采集/扫描/索引播放；不改变 P7/§0.1/§0.2 门禁；不把 schema 校验当作证据通过 |

## 1. 结论总表

| 核验项 | 判定 | 说明 |
| --- | --- | --- |
| ① S1：14 不变量 ↔ schema/判据互斥 | **PASS** | `expected_outcome` 统一语义；`duplicate_resolution` 进入 schema 枚举并由 conflict if/then 约束；INV-2/3/10/11 与 §5.1/§5.3 P4 已对齐；未再发现互斥。 |
| ② S2：`claim_scope` 可表达 provider-reported | **PASS** | `source_evidence.quality.claim_scope` 为必填 `const: provider_reported_not_independently_verified`；§3.1/§5.3 P1 已同步；schema 拒绝其他值（实测）。 |
| ③ S3：playback 机器可读 + 截图不得当播放证据 | **PASS** | `index`/`playback` 均必填 `method/machine_readable`；`manual_visual`/`machine_readable=false` 由 if/then 强制只能 `unknown/not_run`；`limitations` 由 `contains` 强制含 `cannot_prove_audible_output`；§3.7/INV-11/21 对齐（实测负例全部通过）。 |
| ④ S4：fail-closed 覆盖 | **PASS-with-limits** | schema 已强制：published⇒delivered、rejected/failed/commit_unknown⇒failure、fallback⇒attempts（minItems 1）、`failure.code`/`fixed_reasons` 固定 `reason_code` enum、profile≠none⇒scanner+view、summary 必填 blocked；**但位置唯一、汇总计数/overall、expected_outcome↔verdict、published failure=null、duplicate_resolution 条件等仍只有 INV 文本，INV 测试（G-X3）未实现**（V-3）。 |
| ⑤ S5：view 结构 | **PASS-with-limits** | `$defs.view/$defs.scanner` 已定义、`ro_enforcement=verified⇒view_refs`、`basename_ref` 必填；但 V-1（UUID 长度错误）与 V-2（directory_listing 与 view.kind 矛盾）需修。 |
| ⑥ S6：`bits_per_raw_sample` | **PASS** | 现允许 `0`（schema `minimum: 0`）；实测 0 合法、-1 非法。 |
| ⑦ S7：canonicalization | **PASS** | §2.7 定义 `utf8-sorted-json-v1` 与 `record_sha256`/`manifest.sha256` 规则；`redaction.canonicalization_version` 必填 `const`。 |
| ⑧ S8：`deployed.git_head` / `source_fingerprint` | **PASS-with-limits** | `provenance_status` + if/then 允许 `git_head/package_sha256=null`（observed 时必填）；`source_fingerprint_origin` 显式化；绑定已更新。剩余：`snapshot_response` + `source_fingerprint=null` 仍 schema-valid（INV-22 语义，归入 V-3）。 |
| ⑨ 内嵌 schema Draft 2020-12 合法 | **PASS** | JSON 可解析、无重复键；`$schema=.../2020-12/schema`；meta-schema `check_schema` PASS；18 个 `$defs`；全部 `$ref` 解析；`{}` 被 required 拒绝；负例（错误 enum/if-then/contains）被拒。 |
| ⑩ 输入绑定与当前磁盘一致 | **PASS** | §1.2 全表 16 份文档 + 5 个代码哈希均与当前磁盘逐项一致（含 Lead 指定 SCAN `6bef7134…`/114,285、RO `4c013274…`/8,520、generation `0c99d3ac…`/13,780、Round-2 `830ab5f9…`/50,508、本修订依据 `cd39c372…`/30,092）。 |
| ⑪ 草案/未验收/门禁边界 | **PASS** | §1.3/§7 仍明确草案、未实现、未部署、不改变 P7 A4/A5/A6 的 BLOCKED 与 §0.1/§0.2；G-X3 仍写"Rev-2 待复审"；未发现"Rev-2 已批准/可上线"类过度声明。 |

## 2. S1–S8 逐项核验

### 2.1 S1（不变量互斥）——已实质修复

- `expected_outcome` 进入 position 必填字段与 schema enum（`published/conflict/failure/cancelled`）；INV-2 按 expected_outcome 给出 pass 门槛，INV-3 明确"预期失败位置的失败已被确认是 pass，不是 fail"，§5.1 与 §5.3 P4 同步改写；P4 的 quality/bytes `not_applicable` 不再与 INV-3 冲突。
- `verdict.duplicate_resolution` 为 schema 必填字段，enum 含 `conflict_expected/distinct_basenames/ambiguous_expected/unresolved/not_applicable/unknown/null`；`state=conflict` 由 if/then 限定为 `conflict_expected/unresolved` 且 delivered 为 null；INV-10 与 §3.5/P3 对齐。
- INV-11 与 §3.7 统一：index `visible/absent` 必须机器可读；playback `advanced` 必须方法机器可读、`machine_readable=true`、`position_advanced_seconds>0`、`duration_match="match"`、`error_event=null`；`manual_visual` 只能 `unknown/not_run`。
- 未再发现上一轮 S1 的四处互斥；§2.6 与 §5.1 的 pass/fail/unknown/blocked 规则一致。

### 2.2 S2（claim_scope）——已闭环

- schema `source_evidence.quality` 将 `claim_scope` 列入 required，值为 `const: provider_reported_not_independently_verified`；§2.3/§2.4.2/§3.1/附录 C 同步；§5.3 P1 样例已带该标注。
- 实测：缺 `claim_scope` 或填其他值均被 schema 拒绝；provider-reported 边界现在可机器表达。

### 2.3 S3（playback/截图）——已闭环

- `index_play.index` 与 `playback` 均 required `status/method/machine_readable`；`manual_visual` 或 `machine_readable=false` 时 status 被 if/then 强制只能 `unknown/not_run`；`playback.advanced` 的四个必要条件（方法机器可读、machine_readable=true、advance>0、duration_match=match、error_event=null）由 if/then 强制；`index_play.limitations` 由 `minItems:1 + contains const` 强制含 `cannot_prove_audible_output`。
- 实测负例：manual_visual+visible、manual_visual+advanced、machine_readable=false+advanced、duration_match=unknown、position_advanced_seconds=0、error_event 非空、缺 limitation 全部被拒；合法机器可读样例通过。截图/manual_visual 无法进入 pass 证据。

### 2.4 S4（fail-closed）——规范层覆盖，机器闭环未完成

| 要求 | Rev-2 实现 | 核验结果 |
| --- | --- | --- |
| 位置唯一 | INV-15（文本，要求实现侧测试） | schema 无法表达字段唯一性；`positions` 数组无字段唯一约束；INV 测试未实现 → 剩余 |
| 汇总计数/overall 一致 | INV-16（文本） | schema 仅 required 计数；实测 `positions_total=2, pass=1` 等不一致实例仍 schema-valid；INV 测试未实现 → 剩余 |
| state≠published ⇒ failure | rejected/failed/commit_unknown ⇒ failure 由 if/then 强制；published⇒delivered 由 if/then 强制 | schema 已强制失败态 failure；published 的 failure=null、cancelled/queued 等的 failure=null 仅 INV-17 文本；剩余（语义测试） |
| fallback ⇒ attempts | quality if/then：fallback=true ⇒ required attempts + minItems 1 | **已强制**（实测无/空 attempts 被拒） |
| failure.code 固定集 | `failure.code`→`$defs.reason_code` enum；`verdict.fixed_reasons[]`→同一 enum | **已强制**（实测 bogus code 被拒） |
| profile ⇒ scanner+view | byte_scan if/then：profile∈{independent_view,snapshot,copy,directory_listing} ⇒ required scanner+view；profile=none ⇒ status 仅 not_run/unknown | **已强制**（但 directory_listing 自身有 V-2 矛盾） |

结论：S4 的**可 schema 化部分已实质修复**（失败态/fallback/固定码/视图绑定），**不可 schema 化的位置唯一、汇总算术、expected_outcome↔verdict、published failure=null 仍是 INV 文本**；§2.6 注明确要求"另测"、G-X3 仍列"INV 测试未实现"，故当前不能称"fail-closed 机器闭环"；作为草案的规范定义是自洽的。

### 2.5 S5–S8

- **S5（view 结构）：基本收口但有 2 个实质缺陷**。已定义 `$defs.view`（kind/identity_ref/provenance_ref/tree_uuid/parent_uuid/generation/ro_enforcement/view_refs/cleanup_ref）与 `$defs.scanner`；`ro_enforcement=verified` 要求 `view_refs` 非空；`delivered_evidence.basename_ref` 必填。缺陷见 V-1/V-2。
- **S6（bits_per_raw_sample）：可接受**。schema `minimum: 0`；实测 0 合法、-1 非法；§2.4.3 已说明 0 视为 unknown/N/A。
- **S7（canonicalization）：可接受**。§2.7 定义 `utf8-sorted-json-v1`（UTF-8 无 BOM、键按 Unicode 码点排序、数值规范形式、数组保序）、`record_sha256` 去掉自身重算、manifest 行格式；`redaction.canonicalization_version` required const。
- **S8（git_head/source_fingerprint）：可接受但仍有语义裁剪**。`environment.deployed` 增加 `provenance_status`（observed/partial/unknown），observed 时 package_sha256/git_head 必填；`job.source_fingerprint_origin`（snapshot_response/collection_version_manifest/unknown）约束 unknown⇒null；实测 observed+null git 被拒、partial+null 通过。剩余：`snapshot_response`+`source_fingerprint=null` 仍 schema-valid（INV-22 要求 64hex，语义测试缺口，归入 V-3）。

## 3. 编号发现

**V-0（信息/流程，非文档缺陷）**：任务初始绑定 `f4ea94a1…`（115,392 B）是 Rev-2 中间态；复审时磁盘已是作者收尾版 `7a0c8cdd…`（115,449 B，1,171 行，DOC-STAMP `0bc7722a…`），Lead 已确认以最终磁盘为准。两者均记录于此；下游引用应使用最终哈希/戳。**不构成被审文档问题。**

**V-1（中等；S5 需修）：`view.tree_uuid`/`parent_uuid` 的 64hex 约束与真实 Btrfs UUID 不一致。**
证据：§2.4.4 写 `tree_uuid: 64hex|null, parent_uuid: 64hex|null`；schema pattern `^[a-f0-9]{64}$`。但 Btrfs `GET_SUBVOL_INFO` 的 uuid/parent_uuid 是 16 字节（32 hex），仓库扫描器 `_read_btrfs_subvol_info` 明确读取 16 B（`scripts/verify_position_file_evidence.py:314-315`）。实测：32hex（真实 Btrfs UUID）被拒、64hex 通过。若意图存原始 UUID，应改为 32hex；若意图存 UUID 的 SHA-256，必须改名/在 §2.4.4 明确哈希规则。否则实现者为通过 schema 只能省略该字段或写非真实值——证据诚实性风险。当前 view/扫描器未实现、门未开，故无现行 fail-open。

**V-2（中等；S5 需修）：`directory_listing` profile 与 `view.kind`/"无独立视图"语义矛盾。**
证据：`byte_scan.profile` enum 含 `directory_listing`；§2.4.4 与 schema if/then 规定 profile≠none ⇒ `scanner` 与 `view` 均非 null；但 `view.kind` enum 仅 `snapshot|copy|send_receive|ext4_copy`，没有 `directory_listing`。而 §4.4 明确"**若无独立视图**：至少做目录清单 + 直接文件哈希……该 profile 记为 `directory_listing`"——此时本应 view=null。实测：`profile=directory_listing, view=null` 被拒；给一个 `snapshot` view 虽通过 schema，但会错误声称存在独立视图。建议：把 `directory_listing` 加入 `view.kind` 并定义其字段，或让 `directory_listing` 不要求 `view`（只要求 `scanner`/`limitations`）。当前同样无现行 fail-open，但该 fallback 证据形态不可诚实表达。

**V-3（次要；S4 剩余，实施层）：不可 schema 化的 fail-closed 条款仍无 INV 测试。**
INV-15（位置唯一）、INV-16（汇总计数/overall）、INV-2/3/23（expected_outcome↔verdict）、INV-17（published failure=null 等）、INV-10（duplicate_resolution 条件）、INV-22（snapshot_response⇒64hex）以及"claim_refs 至少一个 provider_response_extract"目前只有文本；schema 允许违反实例（实测 `summary` 计数不一致、`job.source_fingerprint_origin=snapshot_response`+null 均 schema-valid）。文档 §2.6 已明确"JSON Schema 表达不了，必须另测"，G-X3 也列"INV 测试未实现"——**这是诚实披露的剩余工作，不是 Rev-2 的新矛盾**；但在测试实现并通过前，不能称 S4 已机器闭环。

**V-4（信息）：§5.1/§2.4.7 的"state≠published⇒failure"需按实际语义理解。**
Rev-2 schema 强制的是"失败态（rejected/failed/commit_unknown）⇒failure"与"published⇒delivered"；conflict/cancelled/queued/running/not_executed 的 failure 应为 null 目前只在 INV-17 文本，不是 schema 约束。Lead 的检查项若按字面"所有非 published 都要求 failure"理解，会与实际设计不符；建议在复核口径中采用"失败态⇒failure；其它非 published 态⇒delivered=null，failure 由 INV-17 语义约束"。此项与 V-3 同属语义测试缺口。

## 4. 解除 / 未解除清单

**在本复审范围内解除（可确认）**：
1. S1 的四处不变量↔schema/判据互斥已消除：`expected_outcome` + `duplicate_resolution` + INV-2/3/10/11 + §5.1/§5.3 P4 一致；未发现新的互斥。
2. S2 闭环：`claim_scope` 必填 const，provider-reported 边界可机器表达；样例已同步。
3. S3 闭环：playback/index 的 method/machine_readable、manual_visual/machine_readable=false 的 if/then、`limitations` contains 均由 schema 强制，且本复审以负例实测。
4. S4 的可 schema 化部分已强制：published⇒delivered、失败态⇒failure、fallback⇒attempts、fixed code enum、profile⇒scanner+view、summary blocked；负例实测通过。
5. S6/S7/S8：bits 0 允许、canonicalization 定义与字段、deployed provenance/git_head 可空、source_fingerprint_origin 与绑定更新均接受（S8 仅余语义裁剪见 V-3）。
6. 内嵌 schema 仍是合法 Draft 2020-12（meta-schema/JSON/无重复键/18 defs/全部 `$ref` 可解析）；`{}` 与关键负例被拒。
7. §1.2 全表绑定（16 文档+5 代码）与当前磁盘一致；Lead 指定的 5 个哈希均匹配。
8. 边界守住：草案/未实现/未部署、P7 A4/A5/A6 与 §0.1/§0.2 仍 BLOCKED、硬门未变、无"已验收/已批准"过度声明。

**未解除（不得据此声称）**：
1. **V-1/V-2 修复前，S5 不能称完全收口**：view 的真实 Btrfs UUID 与 directory_listing profile 证据形态不可如实表达；建议 Rev-3 修正（或明确 UUID 哈希规则/豁免 directory_listing 的 view 要求）。
2. **S4 的 INV-15/16/17/2/3/10/22 等语义测试（G-X3）尚未实现**；位置唯一、汇总算术、expected_outcome↔verdict、published failure=null、duplicate_resolution 条件、snapshot_response⇒64hex 仍可被 schema-valid 实例违反；不能称 fail-closed 已机器闭环。
3. 采集实现/账本/扫描器/索引播放均未实现；A4 仍 BLOCKED；`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、§0.1/§0.2 未变；本复审不是验收、部署或上线授权。
4. Rev-2 中"本轮已跑 Draft 2020-12 meta-schema 校验"由本复审独立复现（PASS），但 INV 语义测试与后续实现仍需独立任务。

## 5. 复审操作与限制

- 本地只读：重算 SHA-256/大小/行数/自校验戳；用本地 `jsonschema`（`Draft202012Validator`，复审工具环境，不随仓库）做 meta-schema、重复键、`$ref`、required/enum/if-then/contains 负例；逐条重算 §1.2 文档/代码哈希（全部匹配）；未访问设备、未运行采集、未改被审文档/代码。
- 限制：不实现/不部署 schema；不创建真机证据；INV 测试未运行（不存在）；不对 `f4ea94a1` 中间态做内容审计（已弃用）。

## 6. 附录：关键哈希与自校验

| 对象 | SHA-256 | 大小/行数 |
| --- | --- | --- |
| Rev-2 最终（被审） | `7a0c8cdd273e253b4ec840656c0f2f67640709d82e80a95bb98f99082481dc9f` | 115,449 B / 1,171 行 |
| Rev-2 中间态（任务初始绑定，已弃用） | `f4ea94a1a317c9b878c2977f970c72a9e0ed07ae8f1ec4d34f59b8ec2dc4f28f` | 115,392 B |
| Rev-2 自校验戳 | `0bc7722a43a20e405c40a24339200c05302743ce82fc7c9d43035f8faa034ee6` | 复算匹配 |
| 上一轮审计依据 | `cd39c37216cbbe887d562775399ed829fffe8f7318a46770f3d19f2734f00173` | 30,092 B |
| 内嵌 schema | 28,735 字符；Draft2020-12 meta PASS；18 `$defs` | — |

- 本报告文末 `REPORT-HASH-STAMP` 给出 `sha256(该行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）；最终整文件 SHA-256 见任务消息。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = a413baad72c33c86ea1520523ce6296e35cc85afe79b9fd3bf605f47fb5a8e7e -->
