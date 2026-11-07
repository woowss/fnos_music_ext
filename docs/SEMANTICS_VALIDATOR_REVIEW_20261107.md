# G-X3 语义校验器独立复审（2026-11-07）

> **总体 verdict：PASS-with-limits（无阻断项）。**
> 校验器主体成立：`scripts/verify_acceptance_evidence_semantics.py`（`ec287c9e…`/49,722 B）与测试（`08983a82…`/32,282 B）哈希一致；本地 **44 passed / 0 skipped**；输出 **26 个 check**；baseline 合法文档 `result=pass / exit 0`；我独立构造的 20+ 个负例、非法 JSON、缺文件、非对象、空 positions 全部 `exit 2`；非升序 manifest-order 代理为 `unknown` 也 `exit 2`；失败码集合与 schema `$defs.reason_code` **37/37 完全一致**；`not_checked` 诚实列出 manifest 顺序、账本刷新、reason_message、record_sha256/manifest 重算、evidence_ref 绑定等；只读、无设备/网络/路由/写盘（仅读打开输入 + stdout）。
> **限制**：发现 1 个需修的**敏感输出泄漏**（`schema_version` 未白名单，可在 `result=pass/exit 0` 时把任意 ≤64 字符字符串原样回显到 stdout；测试未覆盖）；1 个 INV-18 **边界放宽**（`tier_requested=""` 时跳过 reported≠requested⇒fallback 检查，与 INV-18 字面不一致）；1 个 INV-22 **比文档更严**（`collection_version_manifest` 要求 64hex，而文档 INV-22/§2.5 未要求、schema 允许 null）；1 个 INV-15 manifest-order **代理 pass**（升序即 pass，实际 manifest 顺序未验证，not_checked 已披露但 check 名/状态可能被误读）。此外 Lead 摘要中的 INV-17 口径与文档不符（实现正确，文档无需实质修改）。

## 0. 抬头、对象与方法

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（JST 约 13:05–13:25） |
| 复审者 | `final-reviewer`（未参与实现；只新增本报告） |
| 被审校验器 | `scripts/verify_acceptance_evidence_semantics.py`，49,722 B，SHA-256 `ec287c9ebb72d49a80b39492b488625a6853f5cee33315e7223d811a7e1c455c`（与声称一致） |
| 被审测试 | `offline_tests/test_verify_acceptance_evidence_semantics.py`，32,282 B，SHA-256 `08983a8278f68e4368bf0b7c88a2b3678ffb90c2afdcfeafc4c8e9e92427adc5`（与声称一致） |
| 依据 | `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md` Rev-3，119,555 B，`dec0aabb8eefa2fa8354dc44b9a71f070727d1e4df8529f2eb13057ea86b9fd1`（未变） |
| 复审方式 | 本地只读：重算哈希；`python -B -m pytest -p no:cacheprovider -q -rs` 重跑（44 passed/0 skipped）；用测试 fixture + 我构造的负例直接调用 `verify_document`/CLI；把失败码集合与 schema `$defs.reason_code` 对比；读实现逐条对照 INV；检查写盘/设备/路由；未访问设备、未改被审文件（复审前后哈希一致） |
| 不在范围 | 不重跑真机/账本/证据存储；不实现 INV-13/INV-12 等需 artifact 的条款；不改变 schema/产品/门禁 |

## 1. 基本结构与覆盖

- 26 个 check（`INV-15` 3、`INV-16` 2、`INV-17` 7、`INV-19` 1、`INV-2` 5、`INV-3` 2、`INV-23` 1、`INV-22` 2、`INV-18` 3）。
- baseline（测试 fixture `_document()`，position=1、published、expected=published、全维度 pass、summary 一致、job/summary total 一致、deployed partial、fingerprint unknown=null）→ **26/26 pass、result=pass、exit 0**。
- 独立负例（用同一 fixture 逐项变异；均实测）：
  - duplicate position / out-of-range / job-summary-positions total mismatch → `INV-15.*` fail、exit 2；
  - summary 计数/overall 矛盾 → `INV-16.*` fail、exit 2；
  - published 带 failure / published 无 delivered / rejected 无 failure / failure code 非固定 → `INV-17.*` fail、exit 2；
  - conflict 带 delivered / conflict resolution 非允许值 / cancelled 带 delivered → `INV-17.*` fail、exit 2；
  - published 的 gate 维度不 pass / commit_unknown verdict=pass / expected=failure 却实际 published 且 pass / fail verdict 无 fixed_reasons 或不可解释 / fixed_reasons 非固定码 → 对应 `INV-2/3/23/19` fail、exit 2；
  - observed provenance 缺 package/git、snapshot_response fingerprint null、unknown origin fingerprint 非 null → `INV-22.*` fail、exit 2；
  - fallback=true 无 attempts / 缺更高档覆盖 / reported≠requested 且 fallback=false → `INV-18.*` fail、exit 2（详见 §3 边界）；
  - positions 非升序 → `INV-15.manifest_order=unknown`、result=unknown、exit 2；
  - 非法 JSON / 数组 / 空对象 / 空 positions / 不存在路径 → `input.readable` 或 `structure.preconditions` fail、exit 2。
- 失败码集合：validator `_EVIDENCE_REASON_CODES` 与 schema `$defs.reason_code` **37 个完全一致**（无缺失、无多余）；所有实际输出 reason 均来自固定集合。
- 测试：**44 passed / 0 skipped / 0 failed**；无 `pytest.skip`；`monkeypatch` 仅用于替换 stdin（不是 mock 校验器内部）；fixture 为合成 EvidenceSet，无设备/网络/真机依赖。测试覆盖了正例与多条负例，但存在 §4 的边界缺口。

## 2. 逐条 INV 对照（26 checks）

| INV / check | 实现要点 | 我的实测 | 判定 |
| --- | --- | --- | --- |
| INV-15 positions_unique | 1..1000、无重复 | dup/range 负例 fail | 符合 |
| INV-15 positions_total | job==summary==len(positions) | mismatch fail | 符合 |
| INV-15 manifest_order | 升序→pass；非升序→unknown | 升序 pass、降序 unknown/exit2 | **部分符合（G-5）** |
| INV-16 summary_counts | 按每 position `verdict.verdict` 计数；和=positions_total | mismatch fail | 符合 |
| INV-16 overall_verdict | fail>blocked>unknown>pass 推导 | mismatch fail | 符合 |
| INV-17.*（7 项） | published 有 delivered且 failure null；rejected/failed/commit_unknown 有 failure；fixed code；conflict delivered null + resolution；其它非 published delivered null | 全类负例 exit2 | 符合（口径见 §3） |
| INV-19 fixed_reasons | 仅允许固定 reason_code | 非固定码 fail、不回显原串 | 符合 |
| INV-2.*（5 项） | expected published gate、conflict 条件、failure 确认、cancelled 条件、commit_unknown 不得 pass | 负例 fail | 符合 |
| INV-3.*（2 项） | fail verdict 需 fixed_reasons 且存在维度 fail/expected 不符/failure 非法之一 | 负例 fail | 符合 |
| INV-23 expected≠actual | silent pass、invalid verdict、缺 fixed_reasons、unexpected published | 负例 fail | 符合 |
| INV-22 deployed_provenance | observed 要求 package 64hex/git 40hex | 负例 fail | 符合 |
| INV-22 fingerprint_origin | unknown⇒null；snapshot_response⇒64hex；**collection_version_manifest⇒64hex** | 负例 fail | **部分符合（G-4：文档未要求 collection_version_manifest）** |
| INV-18 fallback_attempts_present | fallback=true⇒attempts 非空 | 负例 fail | 符合 |
| INV-18 fallback_tier_coverage | attempts 覆盖所有高于 tier_requested 的档位 | requested=lossless 仅给 lossless→fail；给 jymaster/hires→pass | 符合（与文档字面一致） |
| INV-18 reported_diff_requires_fallback | reported_status=reported、reported_level≠tier_requested、fallback false→fail | 正常 requested 负例 fail；**requested="" 时 pass** | **部分符合（G-3）** |

## 3. INV-17 口径裁定（Lead 摘要 vs 文档）

- **文档明确**：§2.4.6 第 231 行："`state ∈ {rejected,failed,commit_unknown}` 时 `failure` 必须非 null（INV-17）"；§2.6 INV-17 第 730 行："`state∈{rejected,failed,commit_unknown}` ⇒ `failure` 非空；`state=published` ⇒ delivered 非空且 failure null；`state=conflict` ⇒ delivered null 且 resolution∈{conflict_expected,unresolved}；`state∈{cancelled,queued,running,not_executed}` ⇒ delivered null"。**没有**"所有非 published ⇒ failure 非空"的条款。
- **实现正确**：`_FAILURE_STATES={rejected,failed,commit_unknown}` 才要求 failure；conflict 用 duplicate_resolution；cancelled/queued/running/not_executed 只要求 delivered=null。我的独立负例（rejected 缺 failure → fail；conflict 带 delivered → fail；cancelled 带 delivered → fail）与文档一致。
- **结论**：**实现正确；Lead 任务摘要的"非 published⇒failure 非空"是被过度简化的错误口径**（会把 conflict/cancelled 等合法位置误判为失败）。文档本身已足够明确，**不需要实质修改**；但可在 §2.6/§2.4.6 增加一行"failure 仅 failure-states 必填；conflict 用 duplicate_resolution，其余非 published 不填 failure"以减少误读（建议性，非阻断，本次不改文档）。

## 4. 编号发现

**G-1（信息/裁定）INV-17 口径**：实现按 §2.6/§2.4.6，正确；Lead 摘要"非 published⇒failure 非空"与文档不符，不应按该摘要改实现。文档已明确，无需实质更正（可选澄清见 §3）。

**G-2（中等/重要，需修）敏感输出泄漏：`schema_version` 未白名单，可原样回显到 stdout。**
`_apply_document_meta()` 对 `schema_version` 只做 `isinstance(str) and len<=64` 就复制到 `result.input.schema_version`；实测把 `schema_version="https://secret.example/token=SECRET"` 放入 fixture（即使 schema `const` 要求固定值、该输入 schema-invalid），`verify_document` 返回 **result=pass / exit 0**，stdout 含 `secret`；CLI 同样以 exit 0 输出含 secret 的 JSON。这违反任务"stdout 不回显敏感串"，也说明测试 `test_output_does_not_echo_untrusted_strings` 未覆盖该字段。**修复建议**：只回显固定值 `0.1-draft-20261107`，否则 `<invalid>`/`<unexpected>`；同时补负例测试。由于该输入本应被 schema 拒绝（schema `schema_version` 为 const），也可在语义校验前做固定值检查/拒绝。**这是本次唯一需要代码/测试修的实质问题。**

**G-3（中等，判据放宽）INV-18 在 `tier_requested=""` 时跳过 reported≠requested⇒fallback 检查。**
`_check_inv18` 仅在 `requested in _TIER_RANK` 时检查 `reported_level != requested && fallback is not True`；文档 INV-18（§2.6 第 731 行）字面要求 `reported_status=reported && reported_level≠tier_requested` ⇒ `fallback=true`，未排除 `tier_requested=""`。实测 fixture 设 `tier_requested=""`、`reported_level="lossless"`、`reported_status=reported`、`fallback=false` → **result=pass/exit 0**。这属于边界放宽（不产生危险 pass 数据，但违反字面判据）。建议：要么去掉 `requested in _TIER_RANK` 条件（对 `""` 也要求 fallback），要么在文档/代码注释明确"requested 未知时不比较"，并补测试。

**G-4（次要，超出文档的额外严格）INV-22 `collection_version_manifest` 要求 64hex。**
文档 INV-22 只约束 `unknown⇒null` 与 `snapshot_response⇒64hex`；schema `job.source_fingerprint` 为 `string|null` 且没有 collection_version_manifest 的 if/then；实现却要求 `collection_version_manifest` 也必须 64hex（实测 null→fail `fingerprint_declared_not_64hex`）。这是比文档/schema 更严的判据（不是放宽；可能误拒 schema-valid 的 null 情况）。建议把该要求补进 INV-22/§2.5，或实现按文档放宽并只把 null 标 unknown；补对应测试。

**G-5（次要，代理 pass）INV-15 `manifest_order` 升序即 pass，实际 manifest 顺序未验证。**
Check 名为 `INV-15.manifest_order`，实现仅检查 `positions[]` 严格升序：升序→`pass`，非升序→`unknown`，而 EvidenceSet 只有 manifest_sha256、不含 manifest 顺序。`not_checked` 已诚实写"manifest_position_order... ascending proxy（non-ascending→unknown）"，但 check 状态 `pass` 可能被下游误读为"manifest 顺序已验证"；严格 fail-closed 做法是：没有 manifest 顺序时该 check 应为 unknown/pass 需改名（如 `positions_ascending_proxy`）并在结论中注明。建议改名或把 pass 限定为"ascending-proxy consistent"。

**G-6（次要，测试覆盖）44 passed/0 skip/无 mock 掩盖，但有边界缺口。**
- 未覆盖 G-2（敏感 schema_version）、G-3（tier_requested=""）、G-4（collection_version_manifest）三个边界；
- INV-19 只有 1 个显式负例（`test_inv19_fixed_reasons_must_be_fixed_codes`），task-29 要求"至少 2 负例/条"未完全满足；其余条目覆盖较好；
- 测试显式声明 fixture "schema-plausible but not claimed schema-valid"，且不跑 JSON Schema；这与工具定位一致，但需在使用时先跑 schema 校验（或工具自身拒绝 schema-invalid 的 const 字段，顺带修 G-2）。
建议补上述测试与 schema_version 白名单负例。

**G-7（信息）`not_checked` 诚实**：列出 `manifest_position_order`（ascending proxy）、`ledger_refresh_consistency`、`reason_message_fixed_text`、`record_sha256_manifest_recompute`、`evidence_ref_artifact_binding`、`dimension_invariants_requiring_artifacts`、`no_successful_file_filesystem_check`，与任务点一致；未把未检查项写成 pass。**唯一措辞问题是** manifest_order 的 check 仍会给 pass（见 G-5），但 not_checked 本身如实披露。

**G-8（信息）边界/只读合规**：校验器只读输入（`open(path,"rb")`），stdout 输出；无设备/网络/subprocess/路由；无写盘；全仓仅测试 import；不改变 schema/产品/门禁。Emitted reason 全部固定；invalid/unknown 均 exit 2（baseline 仅全 pass exit0）。**除 G-2 的 schema_version 回显外**，`test_output_does_not_echo_untrusted_strings` 和我的独立测试（title/reported_level/failure.code/fixed_reasons/notes/reason_message/evidence_set_id）均未回显敏感值。

## 5. 解除 / 未解除清单

**解除（可确认）**：
1. 哈希一致：validator `ec287c9e…`/49,722、test `08983a82…`/32,282、schema 仍 `dec0aabb…`；复审前后被审文件未变。
2. 本地 **44 passed / 0 skipped**；26 checks 结构清晰；baseline exit0；独立负例/非法输入/非升序 unknown 均 exit2；fail-closed 主体成立。
3. 失败码固定集合与 schema `$defs.reason_code` **37/37 一致**；输出 reason 均为固定码。
4. INV-15/16/17/19/2/3/23/22/18 的**主判据**与文档一致；INV-17 实现口径正确（Lead 摘要错误）。
5. `not_checked` 诚实列出 manifest 顺序、账本刷新、reason_message、record_sha256/manifest 重算、evidence_ref 绑定、需 artifact 的维度、无成功文件的文件系统检查。
6. 只读、不接设备/路由/不写盘（除 stdout）；无 mock 掩盖；测试 fixture 合成、无真机依赖。
7. 敏感回显除 `schema_version` 外未发现；G-2 是明确、可修的一个字段。

**未解除（不得据此声称）**：
1. **G-2 必须修**：`schema_version` 白名单/拒绝 schema-invalid；否则不能声称"stdout 不回显敏感串"，且该输入会被错误地 `pass/exit 0`。
2. G-3/G-4 需与文档对齐（放宽或加强均须写入文档/注释）；G-5 的 manifest-order pass 语义需改名/降级或补充说明；G-6 测试边界需补。
3. 该校验器只覆盖 schema 外语义；INV-12/13、账本刷新、artifact 绑定、固定 reason_message 等仍由 `not_checked` 排除，需后续独立机制；它不替代 JSON Schema 校验，也不代表 P1 验收通过。
4. 产品/路由/设备/门禁未变；P7 A4 与 §0.1/§0.2 仍 BLOCKED；本复审不是验收或上线授权。

## 6. 复审操作与限制

- 本地只读：哈希；`python -B -m pytest -p no:cacheprovider -q -rs`（44 passed/0 skipped）；导入测试 fixture 后反复调用 `verify_document` 做独立负例（不改被审测试/实现）；用临时文件跑 CLI 文件输入/非法 JSON/缺文件；从 schema 文档提取 reason_code 与实现集合比对；读实现逐条核对 INV；grep 确认无设备/网络/写盘/路由；未访问设备、未改被审文件。
- 限制：未重跑真机/账本/证据存储；不覆盖需 artifact 的 INV；不验证测试 fixture 的 schema 合法性（测试自述 schema-plausible，非 schema-valid）；不把本报告当作验收。

## 7. 附录：关键信息

| 对象 | SHA-256 | 大小 |
| --- | --- | --- |
| `scripts/verify_acceptance_evidence_semantics.py` | `ec287c9ebb72d49a80b39492b488625a6853f5cee33315e7223d811a7e1c455c` | 49,722 B |
| `offline_tests/test_verify_acceptance_evidence_semantics.py` | `08983a8278f68e4368bf0b7c88a2b3678ffb90c2afdcfeafc4c8e9e92427adc5` | 32,282 B |
| `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md`（Rev-3，未变） | `dec0aabb8eefa2fa8354dc44b9a71f070727d1e4df8529f2eb13057ea86b9fd1` | 119,555 B |
| 测试实跑 | 44 passed / 0 skipped | — |
| 检查数 | 26 checks；baseline 26 pass；negative/unknown→exit 2 | — |
| 固定码集合 | 37/37 与 schema `$defs.reason_code` 一致 | — |

- 本报告文末 `REPORT-HASH-STAMP` 给出 `sha256(该行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）；最终整文件 SHA-256 见任务消息。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 240c3d09afa1ddc89384e331b77c2809a4a31e92363c6717407848a6d755db90 -->
