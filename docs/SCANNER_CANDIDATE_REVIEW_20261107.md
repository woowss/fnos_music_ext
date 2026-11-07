# 扫描器候选能力改动独立复审（2026-11-07）

> **总体 verdict：PASS-with-limits（无阻断项）。**
> 被审候选 `scripts/verify_position_file_evidence.py`（`341dd4f4…`，31,009 B）与 `offline_tests/test_verify_position_file_evidence.py`（`de28e935…`，35,222 B）的**代码层能力均已按要求实现且诚实**：O_NOATIME 读失败 fail-closed（不静默回退）、syncfs/sync barrier 前后记录 generation 且失败→unknown、root 可为 RO 快照内子树（GET_SUBVOL_INFO 校验所在子卷 RO + openat2 `RESOLVE_BENEATH` 等不越界）、`max_entries` 默认不变且超限 unknown、independent-copy 不假装 RO 语义且 complete 恒 false、三个硬门均保持 `False`、旧 JSON 键保留且**默认门关闭路径与旧版逐字节一致**（本复审实测）；测试计数与声称一致（本地 Windows 18 passed/20 skipped；NAS Linux 38 passed/0 skipped，两轮复跑一致），skip 均有明示理由、未发现 fail→skip 或掩盖真实行为。候选**未接入产品**、未写 `/vol1`、未改服务/配置。
> **限制（不阻断，但不得当作原生验收证据）**：所有 Btrfs 专属行为（真实 `GET_SUBVOL_INFO`、真实 RO 快照/子树、真实 generation 变化/ABA）目前只有**合成测试**（monkeypatch），NAS 复跑也在**非 Btrfs 的 /tmp** 上、且以 **root** 身份执行；因此"root 可为 RO 快照内子树"是**代码+单测层面**的结论，不是原生 Btrfs 验证。两个硬门仍为 `False`，complete 无法被伪装，故无 fail-open。

## 0. 抬头、对象与范围

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（JST 约 11:47–12:05） |
| 复审者 | `final-reviewer`（未参与候选实现；只新增本报告） |
| 被审脚本 | `scripts/verify_position_file_evidence.py`，31,009 B，SHA-256 `341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0`（与声称一致） |
| 被审测试 | `offline_tests/test_verify_position_file_evidence.py`，35,222 B，SHA-256 `de28e935d88b50ee1ba32c68b29abf2e6201f054bdb216f3022f193e819ad855`（与声称一致） |
| 对照旧版 | 上一轮只读 probe 中保留的旧脚本 `/tmp/fnmusic-vol1-probe/code/scripts/verify_position_file_evidence.py`，16,669 B，SHA-256 `197d43a9…`（本复审只读复制用于门关闭路径对比，未修改） |
| 复审方式 | 本地只读审计 + 本地重跑 pytest + NAS `/tmp` 只读副本复跑（vendored pytest，未装系统包，跑后删除）+ 旧/新版本输出逐字节对比；未改被审文件、未改产品代码、未停启服务、未写 `/vol1` |
| 不在范围 | 不接入路由/不部署/不翻转硬门；不创建真实 Btrfs RO 快照做原生验证（需 `/vol1` 写权限与授权，超出本任务）；不重做产品验收 |

## 1. 能力点逐项核验

| # | 声称能力 | 实现核验 | 测试/实测 | 判定 |
| --- | --- | --- | --- | --- |
| 1 | O_NOATIME 读，EPERM→unknown，不静默回退 | `_O_NOATIME` 缺失→`NoAtimeUnavailableError`；`_openat2_noatime` 把 EPERM/EACCES→`NoAtimeDeniedError`；scan 捕获后 `read_mode=o_noatime_denied`/`unavailable` 并返回 unknown；无"去掉 O_NOATIME 重开"分支 | `test_read_opens_request_o_noatime`、`test_missing_o_noatime_fails_closed`、`test_o_noatime_permission_denial_fails_closed`、`test_every_read_and_directory_open_requests_o_noatime`（spy 验证 openat2 flags 均含 O_NOATIME） | 符合（措辞见 SC-3） |
| 2 | 扫描前后 syncfs/sync barrier；前后记录 generation；失败→unknown | `_sync_filesystem`（syncfs，libc 无则 sync）；`_barrier` 失败→`sync_barrier_failed`；`generation_before_pre_sync`/`generation_start`/`generation_before_post_sync`/`generation_end` 全记录；scan 后与 baseline 严格比较，变化→`view_mutated` | `test_filesystem_sync_barrier_really_commits_on_linux`（真实内核 syncfs，无 mock）、`test_sync_barrier_runs_before_and_after_the_scan`、`test_pre/post_barrier_failure_is_fail_closed`、`test_generation_change_committed_by_final_barrier_is_unknown` | 符合（原生 generation 语义未验证，SC-1） |
| 3 | root 可为 RO 快照内子树；GET_SUBVOL_INFO 校验所在子卷 RO；openat2 不越界 | `_require_readonly_snapshot(fd)` 对任意 inode 调 GET_SUBVOL_INFO，校验 treeid/uuid/parent_uuid 与 `BTRFS_ROOT_SUBVOL_RDONLY`（bit 0）；`_open_root` 逐段 O_NOFOLLOW，final dir O_NOATIME；所有后代经 `openat2`，resolve = `RESOLVE_BENEATH|NO_SYMLINKS|NO_MAGICLINKS|NO_XDEV` | `test_subtree_root_is_accepted_and_confined`（同字节 sibling 在 root 外不被遍历）、`test_subvolume_root_is_labelled_as_subvolume_root`、`test_symlink_in_tree...`、`test_child_mount_crossing_or_path_escape...`；Btrfs ioctl 部分为 mock（SC-1） | 符合（代码/单测；原生未验证） |
| 4 | `max_entries` 默认不变；超限 unknown | `MAX_ENTRIES=100_000`（与旧版相同）；`--max-entries` 覆盖；`entries_seen > max` 时置 `limit_exceeded=True` 并 unknown（`limits_exceeded`）；默认/覆盖值均回显 | `test_entry_limit_override_is_recorded_and_exceeded_limit_is_unknown`；`test_capability...`/输入校验 | 符合 |
| 5 | independent-copy 不假装 RO；complete 恒 false；受 `_INDEPENDENT_COPY_VIEW_VALIDATED=False` 门 | 独立 profile 不调用 Btrfs ioctl；`ro_semantics=unavailable`、`generation_bracket=not_applicable`；仍做 unprivileged/O_NOATIME/identity/hash/barrier；结尾无条件 `unknown_report`，complete 永不为 true；gate 只影响 `unknown_reason`（见 SC-4） | `test_independent_copy_profile_scans_but_stays_unknown`（`_read_btrfs_subvol_info` 被禁止调用） | 符合 |
| 6 | 硬门不放松 | `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、`_POINT_IN_TIME_SCAN_VALIDATED=False`、`_INDEPENDENT_COPY_VIEW_VALIDATED=False`；默认 profile 必须两门都 True 才进入扫描，否则 legacy unknown；无任何产品接线 | `test_capability_gates_stay_false`、`test_scan_fails_closed_without_native_generation_stability_evidence`、`test_point_in_time_gate_blocks_scan_even_when_legacy_gate_is_patched`；`grep` 全仓仅测试 import 该脚本 | 符合 |
| 7 | 旧 JSON 键保留；默认门关闭路径与旧版行为一致 | gate 关闭/输入非法时走 `_legacy_unknown`（`{scope, complete, positions}`）；完整报告是旧键超集（新增 `evidence_profile/read_mode/root_scope/sync_barrier/limits/checks`） | **本复审实测**：本地 Windows 上旧版与新默认版对同一输入 stdout **逐字节相同**（sha `0ac7e178…`）、退出码同为 2；旧版常量/`MAX_ENTRIES` 与新版一致 | 符合 |
| 8 | 写入范围/清理/退出码 fail-closed | 脚本无写文件/建目录/删除；证据树只用只读 open；stdout 输出；`main` 返回 0 **仅当** complete=True，否则 2；异常安全输出+2；测试仅写 pytest `tmp_path` | 代码审计 + 本地/NAS 运行；NAS 运行后清理 `/tmp/scanner-review` | 符合 |

## 2. 测试真实性与覆盖审计

### 2.1 计数复现

| 环境 | 命令/条件 | 结果 | 与声称 |
| --- | --- | --- | --- |
| 本地 Windows | `python -B -m pytest -p no:cacheprovider -q -rs offline_tests/test_verify_position_file_evidence.py` | **18 passed, 20 skipped**（19× `Linux x86_64 openat2 required` + 1× `Linux x86_64 syncfs required`），0 failed | 与 implementer 声称一致 ✓ |
| NAS Linux（`/tmp` 只读副本） | vendored pytest 9.1.1（纯 Python，复制到 `/tmp`，`PYTHONPATH` 加载，**未装系统包**）；Python 3.11.2；两轮 | **38 passed, 0 skipped, 0 failed**，RC=0（两轮一致：0.21s / 0.16s） | 与声称一致 ✓ |

### 2.2 skip / mock / fail-closed 审计

- **skip 理由**：20 个 skip 全部来自 `_HAS_SECURE_WALK`（Linux+x86_64+openat2 缺失）或 `syncfs` 平台条件，理由明示；未发现"失败后转 skip"（无 `except ... pytest.skip`；只有 symlink 测试在**平台不支持创建 symlink**时 skip，且该测试本身已被 Linux skipif 保护）。
- **mock 是否掩盖真实行为**：`_allow_synthetic_snapshot_traversal` 明确注释"Linux CI tmp 非不可变 Btrfs 快照；只 mock 进程/门/子卷 ioctl/sync 以驱动遍历，**不是原生快照/生成/屏障证据**"。真实路径仍有覆盖：openat2 真实系统调用（spy 只记录 flags）、O_NOATIME flags 真实传递、真实 `syncfs` 内核调用、真实 tmp 目录遍历/哈希/符号链接/越界拒绝。Btrfs ioctl、unprivileged 门、barrier 失败注入为 mock/合成，属**能力单测**而非原生证据。
- **测试未削弱**：旧版失败路径（symlink、跨设备/越界、读失败、可写子卷、嵌套子卷、generation 变化、limit、路径不回显）均在当前测试中保留；新增门关闭、O_NOATIME 两类失败、barrier 前后失败、子树 root、independent-copy、entry limit、CLI 参数等；`test_capability_gates_stay_false` 直接断言三个门为 False。
- **hard gate 仍不可绕过**：默认 profile 进入扫描前必须 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED and _POINT_IN_TIME_SCAN_VALIDATED` 都为 True；当前均 False，扫描只能 legacy unknown；`--evidence-profile independent-copy` 可绕过两门但 complete 恒 false、且受第三门控制；无路由/产品接线（全仓仅测试 `import verify_position_file_evidence`）。

## 3. 写入范围、清理与退出码

- 脚本：无 `open(...,"w")`/`os.mkdir`/`os.remove`/`O_CREAT`；只用 `os.open` 读、`os.scandir`/`os.stat`、`os.read`、`syncfs`/`sync` 屏障、stdout；不做 `rm -rf`/清空。`sync` 回退是系统级 commit（不是对证据树写内容），机制会被记录为 `"sync"`。
- 测试：只写 pytest `tmp_path`，不引用 `/vol1`（脚本/测试对 `/vol1` 检索 0 命中）；NAS 复跑目录 `/tmp/scanner-review` 已 `rm -rf` 清理，无系统包安装。
- 退出码：`main` 只在 `complete=True` 返回 0；gate 关闭/unknown/平台不支持/输入异常→2；异常路径 stdout 只输出安全的 `{scope, complete:false, positions:[]}`，不回显输入。
- 结论：**脚本不会写证据树/业务树，清理与退出码 fail-closed**；NAS 复跑未写 `/vol1`、未停启服务。

## 4. 编号发现

**SC-1（重要，证据边界，不阻断）：原生 Btrfs 能力未验证，只有代码+合成测试。**
所有 Btrfs 专属行为——真实 `GET_SUBVOL_INFO` UAPI（offset/bit0）、真实 RO 快照/子树、真实 generation 变化/ABA、真实 RO→RW→write→RO 的 generation 行为、原生 `syncfs` 与 generation bracket 的联动——均通过 monkeypatch 合成；NAS 复跑在非 Btrfs 的 `/tmp`、以 root 身份执行，没有创建/读取真实 Btrfs RO 快照。因此"root 可为 RO 快照内子树"是**代码与单测层面**成立，不能作为原生验收证据；`_POINT_IN_TIME_SCAN_VALIDATED=False` 与 legacy 门正确地挡住了 complete，故**不是 fail-open**，但原生验证必须另立任务（需真实 Btrfs 只读快照与授权）。**未解除。**

**SC-2（次要）：unprivileged-process 路径未被原生执行。**
NAS 复跑 `uid=0(root)`；所有会进入 `_require_unprivileged_process` 的扫描测试都 monkeypatch 该函数（或 gate 先返回），`test_filesystem_sync_barrier_really_commits_on_linux` 注释声称"unprivileged process must be able to call"但实际以 root 运行；只有 capability parser 单测。建议增加以非 root 运行的测试（或明确记录"该路径仅代码审计+合成"）。**未解除（非阻断）。**

**SC-3（次要）："O_NOATIME 全部 open" 表述不精确。**
证据树**内容读/遍历 open** 都带 O_NOATIME（最终 root dir、所有后代文件/目录）；但 root 路径的**中间祖先段** open 不带 O_NOATIME（不读、不更新 atime，故无实际风险），`/proc/self/status` open 也不带；若 root 为 `/`，最终 root fd 同样不带 O_NOATIME（契约要求 root 是授权测试库子树，`/` 不是合法目标）。建议把声称改为"所有证据树内容读/遍历 open"，或对最终 root `/` 边缘补 O_NOATIME。**不影响当前契约。**

**SC-4（次要）：independent-copy gate 是占位锁，不是 enable 开关。**
`_INDEPENDENT_COPY_VIEW_VALIDATED=True` 只把 `unknown_reason` 从 `ro_semantics_unavailable` 改为 `independent_validation_unimplemented`，仍 `complete=false`，没有任何独立验证实现。这是**正确的 fail-closed 设计**，但"受 gate 控制"易被误读为"翻门就可用"；建议注释/命名明确"gate 只表示禁止未实现路径完成"。

**SC-5（次要，流程）：旧测试文件不可追溯，无法逐条 diff"测试未削弱"。**
新测试文件为未跟踪文件，旧测试内容未保留在 git/本地副本（本复审只能审计当前测试覆盖与 skip 理由）；当前测试保留了旧失败路径且无 fail→skip，但不能做逐条新旧对照。建议保留旧测试版本（或至少哈希+副本）以便后续复审。

## 5. 解除 / 未解除清单

**解除（本复审可确认）**：
1. 新脚本/测试哈希与声称一致；本地 Windows 计数 18/20、NAS Linux 38/0 均复现（NAS 两轮一致）。
2. 代码层能力点实现：O_NOATIME fail-closed（EPERM/不可用→unknown，无静默回退）；syncfs/sync barrier + 前后 generation 记录、barrier 失败→unknown、generation 变化→unknown；root 一律经 GET_SUBVOL_INFO 校验所在子卷 RO；所有后代经 openat2 带 `RESOLVE_BENEATH|NO_SYMLINKS|NO_MAGICLINKS|NO_XDEV`；子树 root 可用；`max_entries` 默认 100,000 不变、超限 unknown；independent-copy 不假装 RO、complete 恒 false。
3. 硬门未放松：三个 gate 均 False；默认 profile 必须 legacy+PIT 门同时 True 才扫描；候选未接入路由/产品。
4. 旧行为兼容：默认门关闭路径 stdout 与旧版**逐字节一致**（sha `0ac7e178…`，rc 2）；旧键 `scope/complete/positions` 保留；常量与旧版一致。
5. 测试未发现削弱：skip 有明示理由、无 fail→skip、mock 有注释且不掩盖 openat2/O_NOATIME/syncfs 的真实路径；测试断言 gate 保持 False。
6. 写入/清理/退出码 fail-closed：脚本不写证据树，退出码 0 仅 complete；测试仅写 tmp；NAS `/tmp` 副本已清理、未写 `/vol1`、未装系统包、未停启服务。

**未解除（不得据此声称）**：
1. 原生 Btrfs 验证：真实 GET_SUBVOL_INFO/RO 快照/子树/generation/ABA/原生 syncfs+generation 尚未在真实 Btrfs RO 快照上执行（SC-1）。
2. 原生 unprivileged 执行未验证（SC-2）。
3. independent-copy 独立验证未实现（gate 仍 False，complete 恒 false）。
4. `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED`、`_POINT_IN_TIME_SCAN_VALIDATED` 仍为 False；翻门需单独、独立复审的代码改动，本复审不授权。
5. 候选未接入路由/部署；P7 A4 及整条验收仍 BLOCKED/NOT RUN；本复审不是上线授权。

## 6. 复审操作与限制

- 本地：只读审计脚本/测试；`python -B -m pytest -p no:cacheprovider -q -rs offline_tests/test_verify_position_file_evidence.py`（18/20）；用 Python subprocess 对比旧/新脚本同输入 stdout/退出码（逐字节相同）；`grep` 全仓确认未接线；未改被审文件（复审前后哈希一致）。
- NAS：把脚本+测试+deps 复制到 `/tmp/scanner-review`（pytest 从本地纯 Python 包 vendored，未装系统包），以现有 root 会话运行 pytest 两轮（38/0 两轮），随后 `rm -rf /tmp/scanner-review`；未写 `/vol1`、未停启服务、未改配置。
- 限制：无法创建真实 Btrfs RO 快照/子树做原生验证（需 `/vol1` 写权限与授权）；NAS 复跑以 root 身份，未覆盖 unprivileged；旧测试文件不可追溯；未做安全审计，只判断候选实现与测试是否诚实、硬门是否放松。

## 7. 附录：本轮关键哈希

| 对象 | SHA-256 | 大小 |
| --- | --- | --- |
| 新脚本 | `341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0` | 31,009 B |
| 新测试 | `de28e935d88b50ee1ba32c68b29abf2e6201f054bdb216f3022f193e819ad855` | 35,222 B |
| 对照旧脚本 | `197d43a99e244b99021936df12e321b9fcc4c48779ce8a1b029c0de9db049b80` | 16,669 B |
| 旧/新门关闭路径 stdout | `0ac7e1784e0a310c6fc653f4c520f92ae9a5ea39bf2c877e078a209def0ab0c8` | 96 B（单行 JSON） |

### 7.1 本报告自校验

- 已写入 `docs/SCANNER_CANDIDATE_REVIEW_20261107.md`；文末 `REPORT-HASH-STAMP` 给出 `sha256(该行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）；最终整文件 SHA-256 见任务消息。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 37216466303e0cf5f80744348141cb6ecbef333d83c9b70fdee4ad49f829e043 -->
