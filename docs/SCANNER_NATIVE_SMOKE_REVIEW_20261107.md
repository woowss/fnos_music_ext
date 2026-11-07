# 扫描器原生冒烟证据独立复审（2026-11-07）

> **总体 verdict：PASS-with-limits（无阻断项）。**
> 本次冒烟证据在**其所声明的单机单次冒烟范围内**成立且诚实：文档/探针脚本/候选 verifier 哈希一致；文档内嵌的 `result.json`（提取后加末尾换行）为 **44,319 B / `7fec8e60…`**，与声称完全一致；`result.json` 解析为 PASS（24/24 断言、6 个场景、`failure_reasons=[]`、每场 stderr 空）；6 场景与文档 §2/§4 的退出码、unknown_reason、read_mode、root_scope、barrier、positions、stdout SHA-256 **逐项一致**；harness 源码可由探针脚本重建并命中 `46fa2404…`，且仅在子进程内存 patch 两个能力门（GATE-1/2），staged verifier 前后哈希 `341dd4f4…` 未变，仓库三门仍全为 False，未接路由；真实内核路径（真实 `btrfs subvolume snapshot -r`、`GET_SUBVOL_INFO`、`syncfs`、`runuser kurumi` euid=1000/caps=0、`O_NOATIME`）有命令/视图证据，非 mock；C/D/E/F 阴性均 fail-closed（exit 2/unknown，不 pass）。
> **限制**：①本复审按任务约束**不访问设备**，因此 result.json 内自报的清理/服务/`/vol1` 顶层状态只能核验其内部一致性，无法独立复观察；文档 §5 的"脚本外复核"没有原始命令输出随证（N5）。②**SC-1/SC-2 只是部分闭合，不是完全闭合**：单机、单次、极小树（3 条目/34 B）、无并发/长扫描/100k 上限/多子卷嵌套/M1 `-o ro`/trimacl 绕过 RO 写；generation 仅单次稳定观测，不能替代 G1 全写入矩阵；非特权仅一个用户/一种属主组合（N6）。③P7 A4、§0.1/§0.2 与产品门禁均未改变。

## 0. 抬头、对象与方法

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（JST 约 12:20–12:35） |
| 复审者 | `final-reviewer`（未参与 task-23 采集；只新增本报告） |
| 被审文档 | `docs/SCANNER_NATIVE_SMOKE_20261107.md`，72,531 B，SHA-256 `5bec1fbf855c72e9c5731159f75a706b88d362bc1bc4fa26ff83774ccd2d8a3c`（与声称一致） |
| 探针脚本 | `scripts/scanner_native_smoke_probe.py`，40,528 B，SHA-256 `fd59c730f84ed99b7c932a2f90ad2bb8447241d55f804751783c6b51cb1d7c73`（与声称一致） |
| 候选 verifier | `scripts/verify_position_file_evidence.py`，31,009 B，SHA-256 `341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0`（与声称一致，复审前后未变） |
| 原始 result.json | 文档附录 A 内嵌原文，提取后为 44,318 字符；**加末尾换行 = 44,319 B，SHA-256 `7fec8e605eb3cd5115d53b1862ab9aad551fc9b8cb69f3b56a3a5ed98124cd98`**（与 task/doc 声称一致） |
| 复审方式 | 本地只读：重算哈希；抽取内嵌 JSON 并解析；文档 §2/§4 与 JSON 逐项比对；从脚本重建 harness 源码并哈希；读脚本核验 gate patch/真实命令/清理/退出码；`grep` 确认产品门与路由未变；**未访问设备**、未改被审文件（复审前后哈希一致） |
| 不在范围 | 不重跑设备实验、不创建/删除探针树、不验证其它用户/并发/长扫描/M1 `-o ro`；不把本冒烟当验收；不改变任何门禁 |

## 1. 哈希与文档-原始 JSON 绑定

| 检查 | 结果 |
| --- | --- |
| 文档哈希/大小 | `5bec1fbf…` / 72,531 B = 声称 ✓ |
| 探针脚本哈希/大小 | `fd59c730…` / 40,528 B = 声称 ✓ |
| 候选 verifier 哈希/大小 | `341dd4f4…` / 31,009 B = 声称；与 task-18 被审版本一致、复审前后未变 ✓ |
| 内嵌 `result.json` | 44,318 字符 + 末尾换行 = 44,319 B；SHA-256 `7fec8e60…` = 声称/document §1 表 ✓ |
| harness 源码 | 从脚本 `HARNESS_SOURCE` 重建 5,517 字符，SHA-256 `46fa240487f7842e22ac6f1a9b157e01c2997a7c1ee2b2e530ce202bbdda4788` = document/result 声称 ✓ |
| `result.json` 解析 | JSON 合法；`result="PASS"`；`counts={checks_passed:24, checks_total:24, runs:6}`；`failure_reasons=[]`；6 个 run 的 `stderr` 均为空 ✓ |
| 文档 §4 断言表 vs JSON | 24 行 ID/PASS/detail **逐字一致**（0 差异）✓ |
| 文档 §2 场景表 vs JSON | 6 场景 exit/complete/reason/read_mode/root_scope/barrier/positions/stdout SHA-256 全部一致 ✓ |
| 文档 §1 标注 | `stderr 0 字节`：JSON 内各 run stderr 为空且 result.json 可直接解析；**外层 `result.stderr` 文件未随附**（N5） |

## 2. 6 场景与原始 JSON 一致性

| 场景 | root/profile（摘要） | JSON 关键值 | 文档 §2 行 | 判定 |
| --- | --- | --- | --- | --- |
| A_subvolume_root | snap / btrfs-ro-snapshot | rc0、complete=True、reason=None、read_mode=o_noatime、root_scope=subvolume_root、barrier done/done syncfs、positions ambiguous count2、stdout `9b5c7df2…` | 一致 | ✓ |
| B_subtree | snap/lib / btrfs-ro-snapshot | rc0、complete=True、reason=None、o_noatime、subtree、done/done syncfs、unique_match count1、stdout `5aa70a14…` | 一致 | ✓ |
| C_writable_subvolume | src / btrfs-ro-snapshot | rc2、complete=False、reason=ro_semantics_unavailable、view flags=0/readonly_accepted=False、positions unknown、stdout `68423c84…` | 一致 | ✓ |
| D_root_owned_file | snap2/lib2 / btrfs-ro-snapshot | rc2、complete=False、reason=noatime_permission_denied、read_mode=o_noatime_denied、root_scope=subtree、pre=done/post=not_attempted、stdout `cb57d1c1…` | 一致 | ✓ |
| E_independent_copy | snap/lib / independent-copy | rc2、complete=False、reason=ro_semantics_unavailable、ro=unavailable、bracket=not_applicable、hash=performed、barrier done/done、stdout `7850f99c…` | 一致 | ✓ |
| F_cli_default | snap / 默认 CLI | rc2、legacy JSON `{"scope":"read_only_test_tree","complete":false,"positions":[{"position":1,"status":"unknown"}]}`、stdout `2838eddc…` | 一致 | ✓ |

补充：A 的 JSON `view` 为 treeid=712、generation=1692821、flags=1（RO bit0）、uuid/parent 非零；B 的 view 解析为同一 treeid 712；C 的 view flags=0、`readonly_snapshot_error_errno=30`；D 的 view flags=1/treeid=714 但文件 O_NOATIME 被拒——与文档 §3 叙述一致；A7/B7 的 observed_file before/after atime/mtime/ctime/ino/size 相等（文档称扫描未改文件），JSON 支撑。

## 3. Harness / 门禁 / 未接路由

- `HARNESS_SOURCE` 仅在 `importlib` 加载模块后设置 `module._BTRFS_GENERATION_RELOCK_GUARD_VALIDATED = True`、`module._POINT_IN_TIME_SCAN_VALIDATED = True`；**不写模块文件**；`_INDEPENDENT_COPY_VIEW_VALIDATED` 保持 False（脚本 §94–251）。
- JSON 的 GATE-1 证明加载时三门均 False；GATE-2 证明仅两个能力门被置 True、独立门仍 False；HASH-1 与 §1 的 `staged_verifier_sha256 == verifier_source_sha256` 证明 staged 模块文件未被改写；每 run 的 `module_sha256=341dd4f4…`。
- 本地复核：仓库 verifier 三门仍全部 `False`（`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` line 63、`_POINT_IN_TIME_SCAN_VALIDATED` line 68、`_INDEPENDENT_COPY_VIEW_VALIDATED` line 69）；`grep` 全仓仅测试 import verifier，**无路由/产品接线**。
- 结论：harness 的 patch 只存在于一次性 `/tmp` 子进程内存，产品门禁/行为未被改变；此点与文档 §1/§6/§7 一致。

## 4. 真实内核路径 / 非 mock / fail-closed

- **真实内核路径**（脚本 + JSON 互相支撑）：
  - 脚本真实执行 `btrfs subvolume create`、`btrfs subvolume snapshot -r`、`btrfs subvolume show/delete`（result `commands` 可见真实 btrfs stdout/stderr/returncode）；
  - harness 直接调用模块 `_read_btrfs_subvol_info`（真实 `BTRFS_IOC_GET_SUBVOL_INFO`），JSON 的 `view` 给出 treeid/generation/flags/RO bit/UUID 非零；
  - 每个扫描子进程经 `/usr/sbin/runuser -u kurumi` 运行，JSON process 显示 euid=1000、CapEff/CapPrm=0；真实 `syncfs` barrier 的 `pre/post` 与 generation 前后值来自模块记录；
  - `O_NOATIME` 的属主可用（A/B/E）与非属主拒绝（D，`o_noatime_denied`）均有实测；脚本/result 无 mock/fake 字段或替换路径。
- **fail-closed**：C（可写子卷）→ `ro_semantics_unavailable`/unknown；D（root 文件 O_NOATIME 拒绝）→ `noatime_permission_denied`/unknown；E（independent-copy）恒 complete=False；F（产品门未 patch 的默认 CLI）→ legacy unknown + exit 2。四者均**没有**被判 pass。
- 限制：C/D/E/F 的结论仅覆盖这两类阴性；未覆盖其它 ioctl/权限/并发失败注入（§6 已如实列出）。

## 5. 清理、服务与只读核验

- JSON `cleanup` 内部自洽：`probe_root_removed=true`、4 个子卷删除均 true、`related_subvolumes_after=[]`、`subvolume_count` baseline=49/after=49/unchanged、`/vol1/1000` top_level count=47/names_sha=`bbc6f1dc…`/unchanged、`staging_removed=true`、service MainPID=1870197/NRestarts=0/active、mount `rw,relatime,trimacl…` unchanged；`cleanup.ok=true`。
- 文档 §5 的"脚本外复核"列（`test -e`、`grep -c`、`ls -d /tmp/...`、49 行构成、`os.listdir`=47 等）**不在内嵌 result.json 中**，也没有随附命令/输出；按任务约束本复审**不访问设备**，因此只能确认：脚本确实在 `finally` 中执行 `_cleanup` 并把这些值写入 JSON，且文档 §5 的"脚本内记录"与 JSON 完全一致；**无法独立复观察 NAS 当前状态**（N5）。
- 服务 MainPID/NRestarts 与既有设备盘点文档的 1870197/0 一致，属交叉一致性旁证（非本次设备复测）。

## 6. SC-1/SC-2 判定：部分闭合，不是完全闭合

- **SC-1（此前"原生 Btrfs 能力未验证，仅代码+合成测试"）**：本冒烟首次在真机内核上跑通真实 RO 快照子卷根、真实子树、真实 GET_SUBVOL_INFO/RO bit、真实 syncfs barrier、真实 generation 稳定与真实 O_NOATIME，因此相比之前的"仅合成"是**实质推进**；但只有单机/单次/极小树，未覆盖并发 writer、长扫描、100k 上限、多子卷嵌套、M1 `-o ro` namespace、trimacl 绕过 RO 写、全写入路径 generation 矩阵（G1 未替代）。→ **SC-1 部分闭合，不是 fully closed**。
- **SC-2（此前"unprivileged path 未被原生执行"）**：本次以 kurumi（uid 1000、caps 0）真实执行 A/B/D/E/F 扫描，root 文件 O_NOATIME 阴性 fail-closed，故"非特权路径从未实跑"已消解；但只有一个非特权用户、一种文件属主组合（root-owned 文件）与 caps=0 情形，脚本本体仍 root，未覆盖其它 UID/组/能力组合、O_NOATIME 其它 errno 路径。→ **SC-2 部分闭合，不是 fully closed**。
- 文档 §6 自己明确写了"不能替代系统性失败注入（I1–I20）、独立复审、G8 端到端与任何验收"；本复审同意该边界，**不需要也不应该**把本次冒烟写成 SC-1/SC-2 完全闭合。

## 7. 编号发现

**N1（信息/确认）**：三份文件哈希、内嵌 result.json 哈希（raw+末尾换行）、HARNESS_SOURCE 重建哈希全部与声称一致；文档 §4 的 24 条断言与 JSON checks **逐字一致**；此绑定成立。

**N2（信息/确认）**：6 个场景的退出码、complete、unknown_reason、read_mode、root_scope、barrier、positions、stdout SHA-256 与 JSON/文档一致；A/B 阳性 complete=true，C/D/E/F 阴性 exit 2 且不 pass；`counts=24/24 checks、6 runs`、`failure_reasons=[]`。

**N3（信息/确认）**：harness 仅进程内存 patch 两个能力门；GATE-1/GATE-2/HASH-1 + staged verifier 前后哈希 `341dd4f4…` + 本地仓库三门 False + 无路由接线，支持"产品门禁未变、未接入"的声称。

**N4（信息/确认）**：真实 btrfs/`runuser`/`GET_SUBVOL_INFO`/`syncfs`/`O_NOATIME` 路径有命令和结构化证据，非 mock；C/D/E/F 失败均 fail-closed。

**N5（次要，证据完整性）**：清理/服务/`/vol1` 顶层状态的"脚本外复核"没有随附命令与输出；内嵌 result.json 只含脚本内记录。因此本复审无法独立验证设备当前状态（也按任务不访问设备）。若该冒烟要作为后续 SC-1/SC-2 闭合依据重用，建议把脚本外复核命令与原始输出（含时间、stdout/stderr/hash）一并归档。**不阻断本次冒烟结论**（内部一致性成立）。

**N6（次要/边界）**：SC-1/SC-2 仅部分闭合（见 §6）；文档 §6 已如实列出"单机单次小树/无并发/长扫描/M1 -o ro/G1 未替代/两类型阴性"等边界，未过度声明。**不得把本冒烟当 fully closed 或验收依据。**

**N7（信息）**：文档 §5 记录首跑因探针自身 baseline 截断（默认 1200 字符，26 vs 49 行）而重跑，最终证据为 11:14:03–11:14:04；属诚实披露的探针 bug（与设备/扫描器无关），最终脚本哈希已绑定。无需动作。

**N8（信息/边界）**：探针文件在 `trimacl` 子卷内 `mode=0` 的平台表示法被记录但未审计/未定位；文档明确不外推，O_NOATIME 属主判定使用 uid；与本冒烟结论不冲突，保留为 residual unknown。

## 8. 解除 / 未解除清单

**解除（本复审可确认）**：
1. 文档/脚本/verifier 哈希与内嵌 result.json（44,319 B / `7fec8e60…`）一致；HARNESS_SOURCE 可重建并命中 `46fa2404…`。
2. 6 场景、24/24 断言与原始 JSON 及文档 §2/§4 逐项一致；A/B 阳性、C/D/E/F 阴性 fail-closed（exit 2/unknown，不 pass）。
3. harness 仅内存 patch 两个能力门；产品三常量仍 False、staged verifier 前后 `341dd4f4…` 未变、无路由接线。
4. 真实内核路径（btrfs snapshot/`subvolume show`、GET_SUBVOL_INFO、syncfs、runuser euid=1000 caps=0、O_NOATIME 属主/非属主）有结构化证据，非 mock。
5. 清理/服务/`/vol1` 状态的脚本内记录与文档 §5 "脚本内记录"列一致、内部 ok=true。
6. 边界诚实：文档明确单机单次小树、无并发/长扫描/M1 `-o ro`、generation 不替代 G1、SC-1/SC-2 不能替代系统性验证/验收；未发现把冒烟当验收或声称完全闭合。

**未解除（不得据此声称）**：
1. 本复审**未独立复观察设备当前状态**（任务禁止访问设备）：探针根/子卷/staging/服务/挂载/`/vol1` 顶层的实际清理与不变性仍主要依赖 result.json 自报 + 文档 §5 脚本外核查（N5）；脚本外命令输出未随证。
2. **SC-1/SC-2 仍未完全闭合**：缺并发 writer、长扫描/100k 上限、大库、多子卷嵌套、M1 `-o ro` namespace、trimacl RO 写绕过、全写入路径 generation 矩阵（G1）、系统性失败注入（I1–I20）、G8 端到端与其它 UID/属主组合。
3. 产品三门仍 False、候选未接入路由；P7 A4/A5/A6 与 §0.1/§0.2 仍 BLOCKED；本冒烟不是验收/部署授权；后续仍须独立复审与真实门禁流程。

## 9. 复审操作与限制

- 本地只读：重算文档/脚本/verifier 哈希；从文档附录 A 抽取内嵌 JSON（44,318 字符 + 换行）并重算 SHA-256/解析；从脚本 `HARNESS_SOURCE` 重建 harness 并重算 SHA-256；解析 JSON 的 24 checks/6 runs/cleanup 并与文档 §2/§4/§5 对照；读脚本核对 gate patch、真实命令、cleanup/finally、退出码；`grep` 核对产品门与路由。未访问设备、未运行采集、未改被审文件（复审前后哈希一致）。
- 限制：不能复跑设备实验/不能独立观察 NAS 清理与服务状态；不覆盖并发/长扫描/M1/G1/其它用户；不把 `result.json` 的 PASS 当作验收通过。

## 10. 附录：关键哈希

| 对象 | SHA-256 | 大小 |
| --- | --- | --- |
| `docs/SCANNER_NATIVE_SMOKE_20261107.md` | `5bec1fbf855c72e9c5731159f75a706b88d362bc1bc4fa26ff83774ccd2d8a3c` | 72,531 B |
| `scripts/scanner_native_smoke_probe.py` | `fd59c730f84ed99b7c932a2f90ad2bb8447241d55f804751783c6b51cb1d7c73` | 40,528 B |
| `scripts/verify_position_file_evidence.py`（候选，复审前后未变） | `341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0` | 31,009 B |
| 内嵌 result.json（含末尾换行） | `7fec8e605eb3cd5115d53b1862ab9aad551fc9b8cb69f3b56a3a5ed98124cd98` | 44,319 B |
| HARNESS_SOURCE（从脚本重建） | `46fa240487f7842e22ac6f1a9b157e01c2997a7c1ee2b2e530ce202bbdda4788` | 5,517 字符 |

- 本报告文末 `REPORT-HASH-STAMP` 给出 `sha256(该行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）；最终整文件 SHA-256 见任务消息。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 5f9438fc423bd0c69f7ce021225191a71bf58abe684db45c9bd1f5d34e4b8246 -->
