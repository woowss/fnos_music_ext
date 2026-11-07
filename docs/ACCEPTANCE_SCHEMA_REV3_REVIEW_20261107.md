# P1 验收证据 Schema Rev-3 限定独立复审（2026-11-07）

> **总体 verdict：PASS（本限定复审范围内，无阻断项）。**
> Rev-3 对 Rev-2 复审的 V-1/V-2 做了**实质修复且经本复审实测**：`view.tree_uuid/parent_uuid` 已改为 **32hex 原始 Btrfs UUID**（明确不是 SHA-256；32hex 通过、64hex 被拒）；`byte_scan` 已按 profile 分类：`independent_view/snapshot/copy` 必须 `scanner+view`，`directory_listing` 必须 `scanner` 且 `view=null/省略`，`none` 必须 `view=null` 且 status∈`not_run/unknown`（directory_listing+null/省略通过、+snapshot view 被拒、independent_view 缺 view/scanner 被拒）。内嵌 schema 仍是合法 Draft 2020-12（meta-schema PASS、无重复键、18 个 `$defs`、全部 `$ref` 可解析、`{}` 被 required 拒绝）；§1.2 全表绑定（16 文档+5 代码，含新增 Rev-2 复审报告 `8494a793…`/18,407 B）与当前磁盘一致；V-3 已明确归入实现侧 G-X3 语义测试，未声称 S4 已机器闭环；草案/未验收/门禁边界守住，P7 A4/A5/A6 与 §0.1/§0.2 仍 BLOCKED。

## 0. 抬头、对象与方法

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（JST 约 12:13–12:25） |
| 复审者 | `final-reviewer`（未参与 Rev-3 撰写；只新增本报告） |
| 被审对象 | `docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md`，119,555 B，1,187 行，SHA-256 `dec0aabb8eefa2fa8354dc44b9a71f070727d1e4df8529f2eb13057ea86b9fd1`；文末 `DOC-HASH-STAMP` = `ef8648237744c5af7194f4ce92afeee24ad74b14c8743998d21fc2d3a322722d`（本复审复算匹配） |
| 上一轮依据 | `docs/ACCEPTANCE_SCHEMA_REV2_REVIEW_20261107.md`，18,407 B，`8494a79358e1155552b7fa382c565a69dd2aee23c40268f77904cfbfb0af5f95`（Rev-2，PASS-with-limits，V-1/V-2） |
| 复审方式 | 本地只读：重算哈希/行数/自校验戳；抽取内嵌 JSON 并做 `Draft202012Validator.check_schema`、重复键、`$ref` 解析、V-1/V-2 及回归负例实测；逐条重算 §1.2 绑定；未访问设备、未改被审文档/代码 |
| 不在范围 | 不实现采集/扫描/索引播放；不改变 P7/§0.1/§0.2 门禁；不把 schema 校验当作证据通过；不重审 S1–S4 全文（仅回归确认关键约束仍在） |

## 1. 逐项核验

### 1.1 V-1：Btrfs UUID 32hex（实质修复，实测通过）

- 文档：§2.4.4 写 `tree_uuid: 32hex|null, parent_uuid: 32hex|null`，并注明"Btrfs UUID 为 16 B=32hex，不是 SHA-256"（第 181 行）；§2.4.4 规则（第 194 行）明确"原始 16 B UUID（32hex），与扫描器 `_read_btrfs_subvol_info` 读取的 16 B 一致；不是 SHA-256；若需摘要另设 `tree_uuid_sha256` 并写规则"；INV-20（第 733 行）同步。
- schema：`$defs.view.tree_uuid/parent_uuid` = `{"type":["string","null"],"pattern":"^[a-f0-9]{32}$"}`。
- 实测（本复审）：`tree_uuid/parent_uuid` 均 32hex → **通过**；tree_uuid 或 parent_uuid 为 64hex → **被拒**；两者 null → 通过。
- 结论：**V-1 已实质修复**，文本与 schema 一致，未发现其他 64hex UUID 残留（全文检索 `tree_uuid/parent_uuid` 均指向 32hex）。

### 1.2 V-2：directory_listing 豁免 view（实质修复，实测通过）

- 文档：§2.4.4 规则（第 190–192 行）：`profile∈{independent_view,snapshot,copy}` ⇒ scanner+view 必须非 null；`profile=directory_listing` ⇒ scanner 必须非 null、view 必须 null/省略（"目录清单不是独立视图；配 snapshot/copy 等会假称存在独立视图"）；`profile=none` ⇒ status∈`not_run/unknown` 且 view=null。INV-20（第 733 行）与 §4.4（第 962 行）同步。
- schema：`byte_scan.allOf` 三支：
  1. profile∈{independent_view,snapshot,copy} → required scanner+view；
  2. profile=directory_listing → required scanner，view `type: null`（省略也允许）；
  3. profile=none → status 仅 `not_run/unknown`，view `type: null`。
- 实测（本复审）：
  - independent_view + view/scanner → 通过；缺 view → 拒；缺 scanner → 拒；
  - snapshot/copy + view/scanner → 通过；
  - directory_listing + scanner + view=null → 通过；view 省略 → 通过；
  - directory_listing + snapshot view → **拒**；directory_listing 缺 scanner → 拒；
  - none + not_run + view=null → 通过；none + unique_match → 拒；none + view 非 null → 拒。
- 结论：**V-2 已实质修复**，与 §4.4 "无独立视图时用 directory_listing"语义一致。

### 1.3 schema 合法性与回归

- JSON 围栏 29,007 字符，`json.loads` 通过，`object_pairs_hook` 未发现重复键；`$schema=https://json-schema.org/draft/2020-12/schema`；`Draft202012Validator.check_schema` **PASS**；18 个 `$defs`；全部 `$ref` 指向 `#/$defs` 且可解析；`{}` 被 required 拒绝。
- 关键旧约束回归（确认 Rev-3 改动未误伤）：`claim_scope` 非固定值被拒；`fallback=true` 缺 attempts 被拒；`failure.code` 非固定 enum 被拒；`manual_visual+visible`/缺 `cannot_prove_audible_output` 被拒；`view.ro_enforcement=verified` 且 view_refs 空被拒。
- 结论：schema 仍合法，S1–S4 已确认的强制约束未见回归。

### 1.4 输入绑定（含新增 Rev-2 复审报告行）

- §1.2 全表逐项重算：16 份文档哈希/大小全部与当前磁盘一致（runbook/NETEASE/metadata/guard/OFFLINE_*/JOB/IMPORT/SCAN `6bef7134…`/114,285、RO `4c013274…`/8,520、generation `0c99d3ac…`/13,780、Round-2 `830ab5f9…`/50,508、本修订依据 `cd39c372…`/30,092）。
- 新增行：`docs/ACCEPTANCE_SCHEMA_REV2_REVIEW_20261107.md` = 18,407 B / `8494a79358e1155552b7fa382c565a69dd2aee23c40268f77904cfbfb0af5f95` → 与磁盘一致。
- 代码 5 个哈希（route/playlist/jobs/playlist_jobs/webui_gateway）与当前工作树一致。
- 结论：绑定全部一致（0 mismatch）。

### 1.5 V-3 表述

- §2.6 注（第 738 行）明确：schema 不能表达的 S4 条款（INV-15 位置唯一、INV-16 汇总计数/overall、INV-2/3/23 expected_outcome↔verdict、INV-17 published failure=null 等）归入**实现侧 G-X3 语义测试**，"不是 schema 本身缺陷；在 INV 测试实现并通过前，不声称 S4 已机器闭环"。
- §6.1 G-X3 行、§7 状态声明、附录 D 的 V-3 行均重复该边界；未发现"已机器闭环/已实现"类过度声明。
- 结论：V-3 表述正确，与 Rev-2 复审意见一致。

### 1.6 草案/未验收/门禁边界

- §1.3/§7 仍明确：草案/未实现/未执行/未部署；不改变 P7 A4/A5/A6 的 BLOCKED、§0.1/§0.2 门禁或上线授权；"不写已验收"。
- 硬门 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False` 表述未变（§2.4.4/§4.4/§6.3），扫描器仍只能 unknown/exit 2；未发现放松。
- 未发现"Rev-3 已批准/可上线"或把 schema 校验当验收的表述；状态行仍写"Rev-3 仍需复审确认"（本报告即为该复审确认，文件本身没有过度声明）。

## 2. 编号发现

**R3-1（信息/确认）**：V-1 修复成立——32hex pattern + §2.4.4/INV-20 文本一致；实测 32hex 通过、64hex 拒绝、null 通过。**Closes V-1。**

**R3-2（信息/确认）**：V-2 修复成立——profile 分类 if/then 与 §2.4.4/§4.4/INV-20 一致；实测 directory_listing+null/省略通过、+snapshot view 拒绝、independent_view 缺 view/scanner 拒绝、none 规则通过。**Closes V-2。**

**R3-3（信息/剩余，非缺陷）**：V-3 已正确归入实现侧 G-X3；S4 的不可 schema 化条款（位置唯一/汇总计数/expected_outcome↔verdict/published failure=null 等）仍无 INV 测试，**这不是 Rev-3 的 schema 缺陷**，但 schema 定稿后的实现任务必须完成实测，且在此之前不得声称"S4 已机器闭环"。

**R3-4（信息）**：Rev-3 输入绑定与当前磁盘一致，含新增 Rev-2 复审报告行 `8494a793…`/18,407 B；schema 自身 `dec0aabb…`/119,555 B/1,187 行与戳 `ef864823…` 均复算匹配。

**未发现新缺陷**：本轮未发现 Rev-3 引入的新 schema 矛盾、绑定错误或边界放松。

## 3. 解除 / 未解除清单

**解除（本限定复审可确认）**：
1. V-1（Btrfs UUID 64hex）已修复：schema 32hex、文本明确"原始 16 B UUID，不是 SHA-256"、32hex 通过/64hex 拒绝（实测）。
2. V-2（directory_listing 与 view 矛盾）已修复：profile 分类 if/then，directory_listing 要求 scanner 且 view null/省略；+snapshot view、independent_view 缺 view/scanner 等负例全部被拒（实测）。
3. 内嵌 schema 仍合法 Draft 2020-12：meta-schema PASS、无重复键、18 `$defs`、全部 `$ref` 可解析、`{}` 被拒；旧关键约束无回归。
4. §1.2 全表绑定（16 文档+5 代码，含新增 Rev-2 复审报告行）与当前磁盘一致。
5. V-3 表述正确：不可 schema 化条款明确归入实现侧 G-X3，未声称 S4 机器闭环。
6. 草案/未验收/门禁边界守住：P7 A4/A5/A6 与 §0.1/§0.2 仍 BLOCKED、硬门未放松、无"已验收/已批准"过度声明。

**未解除（本报告不改变）**：
1. G-X3 的 INV 语义测试（位置唯一/汇总计数/expected_outcome↔verdict/published failure=null/snapshot_response⇒64hex 等）仍未实现；这是**实现侧**工作，不是 schema 缺陷。
2. 采集实现、账本候选部署、扫描器升级、索引/播放观测、§0.1/§0.2 与 P7 A4 仍全部 BLOCKED/NOT RUN；本复审不授权实现/部署/验收。
3. 本文仅为 schema Rev-3 的复审确认，不构成 product/runbook 变更授权；schema 定稿与实现须另立任务。

## 4. 复审操作与限制

- 本地只读：重算 SHA-256/大小/行数/自校验戳；抽取 JSON 并用本地评审工具 `jsonschema`（`Draft202012Validator`）做 meta-schema、重复键、`$ref` 与负例实测；逐条重算 §1.2 文档/代码哈希（全部匹配）；未访问设备、未运行采集、未改被审文档/代码（复审前后哈希一致）。
- 限制：不实现/不部署 schema；不创建真机证据；不执行 INV 测试（尚不存在）；不对 Rev-1/Rev-2 历史全文重审（只做 V-1/V-2 与边界回归）。

## 5. 附录：关键哈希与自校验

| 对象 | SHA-256 | 大小/行数 |
| --- | --- | --- |
| Rev-3 被审文档 | `dec0aabb8eefa2fa8354dc44b9a71f070727d1e4df8529f2eb13057ea86b9fd1` | 119,555 B / 1,187 行 |
| Rev-3 DOC-HASH-STAMP | `ef8648237744c5af7194f4ce92afeee24ad74b14c8743998d21fc2d3a322722d` | 复算匹配 |
| Rev-2 复审依据 | `8494a79358e1155552b7fa382c565a69dd2aee23c40268f77904cfbfb0af5f95` | 18,407 B |
| 内嵌 schema | 29,007 字符；Draft2020-12 meta PASS；18 `$defs` | — |

- 本报告文末 `REPORT-HASH-STAMP` 给出 `sha256(该行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）；最终整文件 SHA-256 见任务消息。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 737fe0547ed08854438adcbaba2ceefb82e33ecc25cfd5972c3cfb730233d8ad -->
