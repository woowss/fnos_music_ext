# F1/F3 失败路径测试独立复审（2026-11-07）

> **边界声明：本复审不构成生产迁移/部署授权。** 本文只评估 implementer 对 `offline_tests/` 的失败/合同测试补充是否真实、可失败、无设备副作用；生产账本迁移、停写、备份、恢复、回滚、接收端、部署与身份授权仍为 BLOCKED（沿用 `docs/NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md` §4.2 的结论）。
>
> 复审者：`test-reviewer`（独立复审者，未参与被审测试的撰写或实现）。被审对象：implementer 任务 `task-5`（2026-11-07 02:49–02:52 窗口）。
> 日期：2026-11-07（JST）。环境：NAS `kotomi`，内核 `6.18.18.c1168-trim`，Debian 12.10，Python 3.11.2，SQLite 3.40.1，无 pytest（unittest）。

---

## 0. 被审快照与哈希

工作区 `E:\fnmusic-ext-dev`，分支 `codex/fnmusic-prelaunch-20261025`，HEAD `cf843a40d735fb9a20e1bc850f711cd4693cb3aa`。工作树含大量未提交改动/未跟踪候选（`git status --short` 53 条），复审全程**未执行** `git reset/clean/stash/checkout/commit/push`，唯一写入文件为本报告。

### 0.1 被审改动（4 个测试文件）

| 文件 | 期望 SHA-256 | 本地实测 | NAS 独立副本 |
| --- | --- | --- | --- |
| `offline_tests/test_manual_save_anonymous.py` | `cd7522c67d00afa26b959f6efab5243a2c3b805d79679fb33512d84417295b00` | 一致 | 一致 |
| `offline_tests/test_migrate_ledger_snapshot.py` | `1554a017c820fdac43431a9d3e0b19e147125dfd317618e259d475fded362f76` | 一致 | 一致 |
| `offline_tests/_anonymous_ns_scenario.py`（新增） | `ecdd9ba47858a54f289780967487c80728fa166574527aed02251885d1522565` | 一致 | 一致 |
| `offline_tests/_audit_support.py`（新增） | `da1bfcb599953281fdd0bde718bdbae456061de64d59d9a035917121a806509b` | 一致 | 一致 |

### 0.2 产品候选哈希（应未变）

| 文件 | 期望 SHA-256 | 实测 |
| --- | --- | --- |
| `proxy/manual_save_anonymous.py` | `fa4dc6bafcd8bddacba78584ac97e04a6ac29a765791d2cb75f4b4ee6785bc52` | 一致 |
| `proxy/manual_save_core.py` | `53783b5874e7f87ed3e40741d2164dd799710df95cfc7d78b28beb66c612ba6c` | 一致 |
| `scripts/migrate_ledger_snapshot.py` | `9b05d196efc9ddcc2eb950c97ef19e68b5e3d995622f89ea2c4d0ea1493d59f9` | 一致 |
| `proxy/manual_save_jobs.py` | `a17e3a624b168c01ed15ef47e77934d5370cc294e50338232ccc58824421ad42` | 一致 |

### 0.3 范围与修改窗口

- **仅 `offline_tests/` 被 implementer 改动**：全仓 mtime 扫描（`>= 2026-11-07 02:45`）只命中 2 个新 helper、2 个测试文件（02:49:45、02:50:18、02:51:26、02:52:41），以及**并行任务**的 3 个文档（`BTRFS_VOL1_TMPFILE_PROBE_20261107.md` 02:49:31、`LINUX_NATIVE_EVIDENCE_20261107.md` 02:51:40、`BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW_20261107.md` 02:52:37，分属 Lead/final-reviewer，不是测试改动）。窗口内 **无** `proxy/`、`scripts/`、`webui-service/` 文件被修改。
- `git diff --stat -- offline_tests/`：`test_manual_save_anonymous.py` **+234/−0**（纯增量）；`test_manual_save_jobs.py` **2+/2−**（mtime 2026-11-05，是前序工作：为产品 `_complete(..., detail)` 签名补参数，与 F1/F3 无关）。
- `test_migrate_ledger_snapshot.py` 在 git 中为未跟踪候选（前序版本 `07bacf32…` 见前序复审附录 A；NAS `/tmp/fnmusic-native-20261107` 副本可复现该旧哈希），本次改为 `1554a0…`。
- 未发现隐藏产品改动或测试专用后门：产品 4 候选哈希与期望完全一致；新增 helper 只在测试进程内 import 产品模块并调用真实 API，未向产品注入全局 monkeypatch/环境开关；唯一的 test double `_test_only_publish_fd_via_procfs` 是**前序已存在**、docstring 明确标注 test-only 的 `os.link(/proc/self/fd/N)` 模拟，未被本次改为冒充真实 `linkat(AT_EMPTY_PATH)`。

---

## 1. 核验表（声称 → 独立证据 → 结论）

| # | 声称 | 独立证据 | 结论 |
| --- | --- | --- | --- |
| C1 | 4 个被审文件哈希如上报 | 本地 + NAS 独立副本逐文件 SHA-256 重算 | **符合** |
| C2 | 产品代码与 4 个候选哈希未变 | 本地重算 4/4 一致；修改窗口 mtime 扫描无产品文件 | **符合** |
| C3 | 只改 `offline_tests/`，无隐藏产品改动/后门 | `git status --short`、`git diff --stat`、全仓 mtime 窗口扫描、helper 源码审阅 | **符合** |
| C4 | 匿名暂存套件 29 例 | NAS kurumi+专用 runner 实跑 `Ran 29 / OK`（两轮一致，0 skip） | **符合** |
| C5 | 迁移套件 26 例，root 与 kurumi 各可跑 | NAS root `Ran 26 / OK`、kurumi `Ran 26 / OK`（两轮一致，0 skip） | **符合** |
| C6 | 新增 4 个真实内核失败场景（EROFS×2、ENOSPC、EACCES）确为真实路径 | 见 §2；另用独立 errno 探针复现 `EROFS`/`EACCES`/`ENOSPC` | **符合** |
| C7 | 无法真实诱导项（EMLINK/EXDEV、读取期 size/seal 变化）显式 MOCK 且不计真实覆盖 | docstring 含 `MOCK ONLY` / `MOCK-ONLY`；实现不可诱导性逐条审阅 | **符合** |
| C8 | 能力缺失时 fail/error 而非 skip | 缺 `unshare` → `FAILED(failures=1)`；root 用 kurumi runner → `FAILED(errors=1) REFUSED`；**但** EACCES 用例在 euid==0 时 `skipTest` | **部分符合**（见 R1） |
| C9 | 未削弱/修改旧断言 | 匿名：diff 纯增量；迁移：35 行删除全部是旧 helper 内部搬移到模块级，旧 15 个用例断言逐字节未变 | **符合** |
| C10 | 固定 reason 文案与候选实现一致 | 逐条对照产品 reason 表（§4.3） | **符合** |
| C11 | 断言真的能失败（非空转） | 3 组变异实验均 `FAILED(failures=1)`（§4.1） | **符合** |
| C12 | 复跑稳定 | 2 轮 × 3 套件，计数与结果完全一致 | **符合** |
| C13 | 无设备副作用（/tmp 外无写、/vol1 未变、服务未停启、runner marker 不变） | 前后快照比对（§5） | **符合** |
| C14 | 测试只写 `/tmp`、不触 `/vol1`/服务/生产账本 | 代码审阅（`/vol1` 仅作保护路径字符串）+ helper 私有 mount namespace + 无 host mount 泄漏 + 审计钩子零违规 | **符合** |
| C15 | 不写盘断言为真实断言 | 子进程 cwd/TMPDIR/HOME 目录残留检查 + Python audit hook；范围有限（§R3） | **部分符合** |

---

## 2. 真实性审计（核心）

### 2.1 真实内核路径如何被构造

`offline_tests/_anonymous_ns_scenario.py` 由一个**私有 unprivileged user+mount namespace** 驱动：
`unshare --user --map-root-user --mount --propagation private`（外层进程仍是非 root 的 kurumi），namespace 内 `mount -t tmpfs`、`mount --bind` + `remount,bind,ro`，随后调用**真实** `proxy.manual_save_anonymous.AnonymousLinuxStore` / `proxy.manual_save_core`。namespace 退出后 mount 全部消失；helper 自我报告 `euid_inside_namespace=0`（映射 root）、`devices_equal=true`、`read_only_view_is_read_only=true`，并在 helper 内部用断言把失败转成非 0 退出，外层测试对非 0 退出执行 `self.fail`。

| 新场景 | 测试方法 | 真实内核对象 |
| --- | --- | --- |
| EROFS 目标发布 | `test_real_read_only_destination_is_a_frozen_erofs_publication` | 同一 tmpfs 上 destination 的 ro bind mount；真实 `linkat(AT_EMPTY_PATH)` → EROFS |
| EROFS 暂存 O_TMPFILE | `test_real_read_only_staging_rejects_o_tmpfile_without_fallback` | staging 的 ro bind mount；真实 `open(O_TMPFILE)` → EROFS |
| ENOSPC | `test_real_no_space_write_failure_is_fail_closed_and_abortable` | `size=64k` tmpfs + 256 KiB 真实写满 |
| EACCES | `test_real_destination_permission_denied_is_frozen_without_publication` | 目标目录 mode 0555，外层非 root（kurumi）真实 `linkat` → EACCES |

### 2.2 独立 errno 探针（复审者自写，非 implementer 代码）

为排除“helper 只是断言了自己的映射表”的可能，我在 NAS 上以 kurumi 身份另写了 3 组最小探针（共 4 项检查，只在 `/tmp` + 私有 namespace 内运行，用后即删）：

| 探针 | 结果 |
| --- | --- |
| ro bind mount 上 `open(O_TMPFILE)` | `errno = EROFS` |
| ro bind mount 上 `linkat(fd, "", rofd, "probe.mp3", AT_EMPTY_PATH)` | `rc=-1, errno = EROFS` |
| 0555 目标目录（外层 euid=1000 kurumi）上 `linkat(AT_EMPTY_PATH)` | `rc=-1, errno = EACCES`，目标目录无残留 |
| `size=64k` tmpfs 写 256 KiB | 写满 **65536 字节**后 `errno = ENOSPC` |

结论：4 个新场景对应的 errno 均为**真实内核返回**，helper 报告的 reason 是适配器对真实 errno 的映射结果，不是 mock。
另：直接运行 3 个 helper 场景均 `rc=0`，JSON 显示 `euid_inside_namespace=0`、`devices_equal=true`、`read_only_view_is_read_only=true`、`*_audit_write_violations=[]`、目录 entries 为空、宿主 mount namespace 无泄漏（`/proc/self/mountinfo` 无 `fnmusic-testreview-scen`）。

### 2.3 MOCK 标注审计

| 不可真实诱导项 | 位置 | 标注 | 是否计入真实覆盖 |
| --- | --- | --- | --- |
| EMLINK / EXDEV（+ EINVAL/EOPNOTSUPP/ENOSYS 映射） | `AnonymousStorePlatformTests.test_unreachable_errno_mappings_are_mock_only_and_fixed` | docstring 首行 `MOCK ONLY`，并解释“`st_nlink==0` 与同设备约束使二者不可达……不是真实内核失败路径覆盖” | **否**，不计入 |
| `snapshot_changed` / `snapshot_seals_changed`（读取期 size/seal 变化） | `DefensiveInputRecheckTests`（2 例） | 类 docstring `MOCK-ONLY`：“A conforming kernel cannot resize a fully sealed memfd… These tests inject changed observations (mock `os.fstat`/`fcntl`)… NOT real kernel failure-path coverage” | **否**，不计入 |
| 其余旧 mock 注入（写失败、fsync 失败、目标被替换、模式规范化失败、能力拒绝、通用 linkat EIO、O_TMPFILE mock 拒绝等） | 匿名套件旧用例（本次未改动） | 前序复审已指出为 mock；本次未改，也未新增“真实”声明 | 否（但**仍计入套件总数**，见 R2） |

EMLINK/EXDEV 的不可达论证本身经实现审阅成立：适配器在唯一一次 `linkat` 前断言 `st_nlink==0`，且构造器拒绝跨设备目录，因此真实内核无法为“0 链接 inode 的首次 link”返回 EMLINK，EXDEV 在构造期已被拒。

### 2.4 fail vs skip 审计

| 能力缺失情形 | 行为 | 证据 |
| --- | --- | --- |
| `unshare` 不存在/被禁 | **FAIL**（`self.fail`），非 skip | 变异实验 M4：`PATH` 指向空目录 → `Ran 1 / FAILED(failures=1)`，消息 “real failure-path test requires the 'unshare' utility”；`unshare` 返回非 0 同样走 `self.fail` |
| helper 内 mount 失败/断言失败 | 非 0 退出 → 外层 **FAIL** | `_run_namespace_scenario` 对 rc≠0 / 无 JSON 均 `self.fail` |
| runner 缺失或 env 错误 | **ERROR/REFUSED**（`setUpClass` RuntimeError），非 skip | M6：root + kurumi runner → `Ran 6 / FAILED(errors=1)` + `REFUSED` |
| 非 Linux 平台 | 类级 `skipUnless(sys.platform=="linux")`（预期平台门，不是能力缺失） | 本地 Windows 复跑 |
| **euid==0（root）时的 EACCES 用例** | **skipTest** | M5：刻意创建 root 属主 runner 后以 root 跑 → `Ran 29 / OK (skipped=1)`；见 R1 |

除 R1 一项外，“能力缺失 → fail/error”成立；指定环境（kurumi + `0700` runner）下 EACCES 真实执行。

**有/无权限两路径的如实性**：本机 unprivileged user namespace 可用（有权限路径），4 个场景真实执行并通过；无权限路径按设计不 skip——`unshare` 缺失（M4）与 helper/mount 失败均 `FAIL`，root 用文档指定 runner 则 `REFUSED error`（M6）。仅“内核显式禁用 userns 时的 rc 形态”未在真机复现，但其错误路径与 M4 相同（非 0 退出 → `self.fail`）。

### 2.5 无写盘/无泄漏审计

- helper 的 mount 全部发生在私有 mount namespace，退出即消失；宿主 `/proc/self/mountinfo` 无残留，`/tmp/fnmusic-testreview-scen` 下只见空目录（tmpfs 内容随 namespace 消失）。
- `WriteAudit`（Python audit hook）在发布/写失败期间对真实路径记录 `open/os.mkdir/os.link/...`，4 个场景均为 `[]`；`os.chmod(int_fd, ...)` 被正确视为匿名 inode 操作。
- 迁移 worker 的“不写盘”用例用独立 cwd/TMPDIR/HOME 三目录残留检查 + 进程内 audit hook，均为空。范围限制见 R3。
- 测试代码中 `/vol1` 仅作为 `_verified_isolated_runner_root()` 的保护路径字符串出现，无 `open/stat` 访问；无网络/HTTP 调用。

---

## 3. 独立复跑（原始计数）

### 3.1 本地 Windows（Python 3.14.6）

| 套件 | 原始计数 | skip 理由 |
| --- | --- | --- |
| `offline_tests.test_migrate_ledger_snapshot` | `Ran 26 tests` / `OK (skipped=15)`，exit 0 | 15 个 Linux-only 类用例：`SealedDescriptorProtocolTests`(11) + `DefensiveInputRecheckTests`(2) + `NoDiskWriteTests`(2)，理由 “memfd/SCM_RIGHTS integration requires Linux” 等（平台门，符合预期） |
| `offline_tests.test_manual_save_anonymous` | `Ran 29 tests` / `OK (skipped=23)`，exit 0 | `AnonymousStoreLinuxTests` 类级 skip “Linux O_TMPFILE/linkat integration only”；6 个平台用例实际执行（其中 1 个为 MOCK-only errno 用例） |

### 3.2 NAS 独立副本

- 复审者**自行打包**（本地 `tar`：`offline_tests/*.py` + `proxy/*.py` + `scripts/*.py`，排除 `__pycache__`，共 66 文件，tarball SHA-256 `891702e5724e3a057765a6251f6f1d75533ba08fe9aee0fba777914b2ec0e759`），`scp` 至 `/tmp/fnmusic-testreview-20261107-src.tar.gz`，NAS 复算哈希一致后解包到 `/tmp/fnmusic-testreview-20261107`；解包后 4 个被审文件 + 4 个产品候选哈希与本地逐一一致。未依赖 implementer 的 `/tmp/fnmusic-fix-20261107`（仅用其做旁证比对）。
- 匿名套件环境：`runuser -u kurumi`，`FNMUSIC_OFFLINE_TEST_ISOLATION=dedicated-disposable-runner-v1`，`FNMUSIC_OFFLINE_TEST_ROOT=/tmp/fnmusic-offline-runner-20261107`（目录 0700 kurumi，marker 0400 kurumi 35B）。
- 迁移套件：root 与 kurumi 各跑一次；匿名套件 kurumi+runner。**重复一轮**核对稳定性。

| 轮次 | 套件/身份 | 原始结果 |
| --- | --- | --- |
| 第 1 轮 | migrate / root | `Ran 26 tests in 2.545s` / `OK`，rc 0，**skipped=0** |
| 第 1 轮 | migrate / kurumi | `Ran 26 tests in 2.574s` / `OK`，rc 0，**skipped=0** |
| 第 1 轮 | anonymous / kurumi+runner | `Ran 29 tests in 0.306s` / `OK`，rc 0，**skipped=0** |
| 第 2 轮 | migrate / root | `Ran 26 tests in 2.526s` / `OK`，rc 0 |
| 第 2 轮 | migrate / kurumi | `Ran 26 tests in 2.519s` / `OK`，rc 0 |
| 第 2 轮 | anonymous / kurumi+runner | `Ran 29 tests in 0.327s` / `OK`，rc 0 |

- verbose 原始输出的测试行计数：migrate root 26、migrate kurumi 26、anonymous 29；两个套件两轮均无 `skipped=` 标记。
- **4 个新真实场景确实执行（不是 skip）**：verbose 日志逐一出现 `test_real_destination_permission_denied_is_frozen_without_publication`、`test_real_read_only_destination_is_a_frozen_erofs_publication`、`test_real_read_only_staging_rejects_o_tmpfile_without_fallback`、`test_real_no_space_write_failure_is_fail_closed_and_abortable`，且整轮 `OK` 无 skip。
- 复跑后重算副本 4 个被审文件哈希不变（测试未回写自身）；副本无 `__pycache__`/`.pyc`（全程 `-B`）。

---

## 4. 断言强度与旧断言

### 4.1 变异实验（证明断言可失败）

在 `/tmp` 下另复印一份**副本**（不动仓库、不动 NAS 主副本），改错期望值后单测运行：

| 实验 | 变异 | 结果 |
| --- | --- | --- |
| M1 | EROFS destination 期望 reason 改为 `destination_permission_denied` | `Ran 1 / FAILED (failures=1)`：`'destination_filesystem_read_only' != 'destination_permission_denied'` |
| M2 | EROFS staging 期望 `o_tmpfile_unavailable` 改为 `o_tmpfile_unsupported` | `Ran 1 / FAILED (failures=1)`：`'o_tmpfile_unavailable' != 'o_tmpfile_unsupported'` |
| M3 | 迁移双描述符期望 `request_packet_invalid` 改为 `snapshot_not_fully_sealed` | `Ran 1 / FAILED (failures=1)`：`{'reason': 'request_packet_invalid', ...} != {...}` |

### 4.2 旧断言是否被削弱

- 匿名套件：`git diff` 为 **+234 / −0**，旧用例与旧断言逐字节未变；新增内容只有 imports、`_tree_entries()`、1 个 MOCK-only 平台用例、`_run_namespace_scenario()`、4 个真实 Linux 用例。
- 迁移套件（旧 `07bacf32…` 426 行 vs 新 `1554a0…` 813 行）：`+422 / −35`。**35 行删除全部是旧类内 helper（`_make_memfd`/`_run_worker`）被搬移到模块级函数**（`_sealed_memfd`/`_raw_memfd`/`_spawn_worker`/`_run_worker`），行为等价；旧 4 个 `SealedDescriptorProtocolTests` 用例与全部 V1/Memory 语义用例的断言未变（diff hunk 只覆盖 helper 区段与文件尾部新增段）。唯一的“放宽”是子进程等待超时 10s→30s，不影响断言强度。
- 旧 `test_manual_save_jobs.py` 的 2+/2− 为 2026-11-05 前序签名适配，与本被审范围无关。

### 4.3 固定 reason 文案一致性（测试 ← 产品实现）

| 测试期望 | 产品位置 |
| --- | --- |
| `destination_filesystem_read_only` | `proxy/manual_save_anonymous.py:174-175`（EROFS→reason） |
| `destination_permission_denied` | `proxy/manual_save_anonymous.py:172-173`（EACCES→reason，经 `publish_noreplace` 包装为 `PublicationUncertain`） |
| `o_tmpfile_unavailable` | `proxy/manual_save_anonymous.py:286-290`（EOPNOTSUPP/EINVAL/ENOSYS→unsupported；EACCES/EPERM→permission；其余含 EROFS→unavailable 兜底） |
| `stage_write_failed` | `proxy/manual_save_anonymous.py:330` |
| `ambiguous_stage_retained_until_process_exit` | `proxy/manual_save_anonymous.py:455` |
| `store_blocked_after_unknown_publication` | `proxy/manual_save_anonymous.py:258` |
| core `commit_unknown` / `staging_failed` | `proxy/manual_save_core.py:186,200,204-205` |
| `request_packet_invalid` | `scripts/migrate_ledger_snapshot.py:375-381` |
| `snapshot_not_fully_sealed` / `snapshot_size_or_type_rejected` | `scripts/migrate_ledger_snapshot.py:251-255` |
| `socket_type_rejected` / `snapshot_changed` / `snapshot_seals_changed` | `scripts/migrate_ledger_snapshot.py:266-268,358` |

---

## 5. 副作用审计（复跑前后）

| 对象 | 复跑前 | 复跑后 | 结论 |
| --- | --- | --- | --- |
| `fnmusic-ext.service` | `InvocationID=53d953f3475047e484e9fdd955b71716`，`MainPID=1870197`，`NRestarts=0`，active/running | 完全相同 | **未停/未启/未重启** |
| `fnmusic-sources` 容器 | `Up 5 days (healthy)` | 相同 | 未动 |
| `/vol1`、`/vol1/1000`、`/vol1/@appcenter/fnmusic-ext`、staging(700)、target(750) | 逐项 mode/owner/size/**mtime** | 完全相同 | **`/vol1` 未变** |
| runner 根 marker `.fnmusic-offline-isolated-runner` | `mode=400 owner=1000:1001 size=35 mtime=2026-11-07 01:07:55` | 完全相同 | 权限/属主/内容不变 |
| runner 根目录 | 0700 kurumi，84 个 fixture 目录 | 0700 kurumi，133 个（本轮 +49 = 2 轮×23 + 3 次单测×1） | 仅测试设计的 fixture 累积（见 R6） |
| `/tmp` 顶层 | 71 项 | 仅多出复审者自有 `/tmp/fnmusic-testreview-*`，runner 目录尺寸变化（fixture 累积） | 无其它新增/改动 |
| `/tmp` 之外 | — | 无任何可观测写入；helper mount 无宿主泄漏 | 符合 |

**复审期间产生的 `/tmp` 目录与清理**：
- 保留（证据）：`/tmp/fnmusic-testreview-20261107`（独立副本 + `logs/`，约 2.5 MB）、`/tmp/fnmusic-testreview-20261107-src.tar.gz`（528,245 B）。
- 已删除：`/tmp/fnmusic-testreview-20261107-mut`（变异副本）、`-scen`（直接场景探针根）、`-emptybin`、`-probe2/3/4`、`-root-runner`（刻意创建的 root 属主 runner，用后即删）。
- runner 内 49 个 `fnmusic-anonymous-test-*` fixture 目录按测试设计**保留未删**（套件刻意保留现场；runner 为一次性隔离目录，删除需单独确认）。

---

## 6. F1/F3 覆盖结论

### 6.1 F1（匿名暂存“全部失败/权限路径”过度声称）

| F1 子项 | 当前状态 | 证据 |
| --- | --- | --- |
| 真实成功发布（O_TMPFILE + AT_EMPTY_PATH）与真实 EEXIST 冲突 | **真实**（旧用例，未削弱） | 前序复审 + 本次复跑 |
| **EROFS 目标发布** | **真实（新）** | `test_real_read_only_destination...` + 独立 errno EROFS 探针 |
| **EROFS 暂存 O_TMPFILE** | **真实（新）** | `test_real_read_only_staging...` + 独立 errno EROFS 探针 |
| **ENOSPC 写失败** | **真实（新）** | `test_real_no_space...` + 独立 ENOSPC 探针（65536B 后失败） |
| **EACCES 权限拒绝** | **真实（新，非 root）** | `test_real_destination_permission_denied...`（kurumi）+ 独立 EACCES 探针；root 下 skip（R1） |
| EMLINK / EXDEV | **MOCK-only（显式）** | 新 `test_unreachable_errno_mappings_are_mock_only_and_fixed`，不计真实覆盖 |
| 写失败/fsync 失败/目标被替换/模式规范化失败/能力拒绝/通用 linkat 错误/O_TMPFILE mock 拒绝 | **仍为 mock**（旧用例未改） | 旧套件 mock 注入；本次未新增真实路径，也未声明为真实 |

结论：**F1 的“过度声称”在测试层面被实质补强**——新增 4 类真实内核失败路径；但 F1 并未把“全部失败/权限路径”变为真实，剩余 mock 分支仍须按 mock 陈述。另：证据文档 `docs/LINUX_NATIVE_EVIDENCE_20261107.md:95` 仍写“失败/权限分支仍为 mock”，已相对本次补测**过时**（见 R5）。

### 6.2 F3（合同 6 类故障/竞争条款）

| # | F3 子项 | 状态 | 用例 |
| --- | --- | --- | --- |
| 1 | 0B / 非普通 fd / >64 MiB 拒绝；1B 与恰好 64 MiB 通过 size gate | **真实（新）** | `test_zero_byte_and_non_regular_inputs_are_size_or_type_rejected`、`test_one_byte_and_exact_64_mib_inputs_pass_the_size_gate`、`test_oversize_snapshot_is_rejected_before_content_validation`（真实 memfd/pipe/64 MiB 稀疏封条 memfd） |
| 2 | 部分 seal 组合（缺一即拒） | **真实（新）** | `test_each_missing_seal_is_rejected`（4 个 subTest，真实 `F_ADD_SEALS` 组合） |
| 3 | >1 输入描述符拒绝 | **真实（新）** | `test_two_input_descriptors_are_rejected`（真实 2 fd `SCM_RIGHTS`） |
| 4 | peer 关闭 / 无效 socket 不发送成功 artifact | **真实（新）** | `test_peer_close_without_receiver_sends_no_success_artifact`、`test_invalid_socket_descriptor_sends_no_success_artifact`（真实进程/真实 fd） |
| 5 | 读取期 size/seal 变化 | **MOCK-ONLY（显式）** | `DefensiveInputRecheckTests` 2 例；full sealed memfd 无法被真实内核改变，docstring 明示不计真实覆盖 |
| 6 | “不写盘”直接断言 | **真实（新，范围受限）** | `NoDiskWriteTests` 2 例：真实 worker 在隔离 cwd/TMPDIR/HOME 无残留 + 进程内 audit hook 零写意图；范围见 R3 |

结论：**F3 的可诱导项已全部有真实用例**；唯一按设计只能 mock 的 size/seal 竞态已诚实标注、未冒充真实覆盖。

### 6.3 计数口径（重要）

| 套件 | 总数 | 旧用例 | 新增真实 | 新增 MOCK-only |
| --- | --- | --- | --- | --- |
| anonymous | 29 | 24 | 4（EROFS×2/ENOSPC/EACCES） | 1（EMLINK/EXDEV 映射） |
| migrate | 26 | 15 | 9（F3 第1/2/3/4/6 项） | 2（size/seal 变化） |

“29/26”是**套件总数**，不是“真实内核失败路径总数”；引用时必须带上本表口径（见 R2）。

---

## 7. 发现

### 阻断
无。未发现伪造计数、skip 当 pass、mock 冒充真实、产品代码改动或设备副作用。

### 重要

**R1（重要，政策偏差）EACCES 用例在 `euid==0` 时 `skipTest` 而非 fail/error。**
`test_real_destination_permission_denied_is_frozen_without_publication` 开头 `if os.geteuid() == 0: self.skipTest(...)`。实测：刻意创建 root 属主 0700 runner + 0400 marker 后以 root 运行 → `Ran 29 / OK (skipped=1)`，该能力缺失路径以“OK+skip”呈现，与「能力缺失必须 fail/error、不得 skip 当 pass」的验收要求不符。
缓解事实：指定环境（kurumi + `0700` runner）下该用例真实执行；用文档指定的 kurumi runner 以 root 运行会在 `setUpClass` **REFUSED error**（实测 `Ran 6 / FAILED(errors=1)`），因此正常误用不会静默通过。建议改为 `self.fail`（或在 `setUpClass` 断言非 root），使任何 root 运行都不能以“OK (skipped=1)”结束。

**R2（重要，计数口径）套件总数包含 MOCK-only 用例，不能读作真实覆盖面。**
anon 29 中 1 个、migrate 26 中 2 个是显式 MOCK-only；另有大量**旧** mock 注入用例仍在套件内。implementer 的 MOCK 标注本身诚实，但“29/26”若被当作“真实故障路径数”引用会形成新的过度声称。建议后续汇报/文档一律采用 §6.3 的口径。

### 次要

**R3（次要）“不写盘”断言范围有限。**
迁移 worker 的写审计只覆盖 cwd/TMPDIR/HOME 三个目录的目录项残留 + Python 级 audit hook；不覆盖其它路径的写入，也看不到扩展模块的 C 级 `open`（`_audit_support.py` docstring 已诚实说明，但 `NoDiskWriteTests` 类 docstring 未写明 scope）。若后续把“不写盘”当作强合同证据，需 strace/seccomp 级验证或明确限定为“三个受控目录 + Python 层”。

**R4（次要）EROFS 暂存用例锁定的是 generic reason。**
EROFS 在 `x_tmpfile` 分支按兜底映射为 `o_tmpfile_unavailable`（产品 `manual_save_anonymous.py:286-290`），测试锁定该文案。独立探针已证实真实 errno=EROFS，真实性成立；仅提示将来产品若为 EROFS 增加专属 reason，此测试需同步更新，否则会变成“实现耦合”而非行为断言。

**R5（次要）证据文档未同步。**
`docs/LINUX_NATIVE_EVIDENCE_20261107.md`（02:51:40 由 Lead 更新）第 95 行仍称匿名暂存“失败/权限分支仍为 mock”、第 103 行仍称 F3“尚无显式用例”；相对本次补测已过时（现有 4 类真实失败路径、F3 除 size/seal 竞态外均有真实用例）。属文档同步项，不阻塞测试结论，但建议在下次证据文档修订时更新并指向本报告。

**R6（次要）runner fixture 无清理机制。**
每轮匿名套件全量运行在 runner 根下保留 23 个 fixture 目录（本轮 84→133，+49）；前序运行已累积 84 个。设计上“保留现场”合理，但长期运行会持续膨胀 `/tmp`，建议在 runner 生命周期里约定清理方式（清理需单独确认，本复审未删）。

---

## 8. Verdict

**PASS-with-limits（限定范围通过）。**

- **通过的核心**：4 个被审文件哈希与产品候选哈希全部一致；implementer 只改 `offline_tests/`；4 个新失败场景（EROFS×2、ENOSPC、EACCES）经代码审阅 + 独立 NAS 实跑 + 复审者自写 errno 探针确认为**真实内核路径**；F3 可诱导项全部真实覆盖；不可诱导项显式 MOCK 且不计真实覆盖；本地/NAS 两轮合计 8 次套件运行（本地 2 次 + NAS 6 次）全部 `OK`、0 skip（匿名 29、迁移 26 root & kurumi）；断言可通过变异实验失败；旧断言未被削弱；固定 reason 与产品实现一致；无 `/tmp` 外写入、`/vol1` 未变、服务未停启、runner marker 不变。
- **限定**：R1（root 下 EACCES skip，政策偏差）、R2（套件计数含 MOCK-only）、R3（不写盘范围）、R5（证据文档未同步）、R6（fixture 累积）。
- **不成立 BLOCKED**：没有发现伪造、mock 冒充、skip 当 pass 或副作用；上述为可修正项而非推翻性缺陷。

---

## 9. 未解除项（不得外推）

1. **生产迁移/部署仍为 BLOCKED**：停写、备份、恢复、回滚、接收端、部署、安装、身份与管理员授权均未证明；本复审不构成任何生产授权。
2. `/vol1` Btrfs 上的 `O_TMPFILE`/`AT_EMPTY_PATH`/ACL 真实行为仍未验证（本轮只覆盖 `/tmp` ext4 与私有 tmpfs；前序 `/vol1` 探针为另一任务范围）。
3. 读取期 size/seal 变化、EMLINK/EXDEV 仍只能 mock（按内核语义不可真实诱导），非真实覆盖。
4. “不写盘”未做全路径强证明（见 R3）。
5. 无 userns 权限主机的真实禁用行为未在真机验证；只验证了 `unshare` 缺失时 `FAIL`（按设计不会 skip）。
6. EACCES 真实路径依赖非 root 运行身份；root 运行时为 skip（R1，建议改为 fail）。
7. 证据文档 `docs/LINUX_NATIVE_EVIDENCE_20261107.md` 需按本报告 §6 更新（F1 部分真实、F3 已覆盖），否则文档与测试现状不一致（R5）。

---

## 10. 第 2 轮返工确认（2026-11-07，Lead 返工后）

**范围与方法**：仅核对 R1/R5 返工，不重跑全部套件；采用「代码/文档静态核对 + NAS 最小复现」。不修改被审文件、不写 `/vol1`、不停启服务。
**本轮被审更新**：`offline_tests/test_manual_save_anonymous.py` = `c7bb6a4511074ab309eebb07fbdf311a567b9d48913c040c8b46c54bed894594`；`docs/LINUX_NATIVE_EVIDENCE_20261107.md` = `30db6e49d4d39d59c9393c76f5fc4b286faef89e9f87cb56b1239dc10905a6c3`。4 个产品候选哈希与其余 3 个被审测试文件哈希与第 1 轮相同（未变）。

### 10.1 R1（EACCES 用例 root 下 skip → fail）：**resolved**

- **代码核对**：与第 1 轮独立副本逐行 diff 仅 1 行变化（`+1/−1`）：`self.skipTest(` → `self.fail(`（参数与文案不变）；全文已无 `skipTest`，类级 `skipUnless(sys.platform=="linux")` 平台门保留（预期）。
- **NAS 最小复现**（独立副本更新后实跑）：
  - 刻意创建 root 属主 `0700` runner + `0400` marker，以 root 运行 → `Ran 29 tests` / **`FAILED (failures=1)`**，rc=1；唯一失败为 `test_real_destination_permission_denied_is_frozen_without_publication`，失败信息为 `AssertionError: real EACCES requires a non-root effective UID; root bypasses directory write permission...`（不再出现 `OK (skipped=1)`）。
  - 指定环境 kurumi + 专用 runner（`FNMUSIC_OFFLINE_TEST_ISOLATION=dedicated-disposable-runner-v1`，runner `0700` kurumi）→ `Ran 29 tests` / **`OK`**，0 skip；4 个真实场景用例仍逐一执行。
  - migrate root 回归 → `Ran 26 tests` / `OK`（未受本次改动影响）。
  - root 属主 runner 已精确删除（`/tmp` 内已确认不存在）。
- **结论**：R1 已修复且不引入回归；“能力缺失 → fail/error”政策在匿名套件中已无例外（非 Linux 的类级平台门除外）。

### 10.2 R5（证据文档未同步）：**resolved**

- **文档核对**（`docs/LINUX_NATIVE_EVIDENCE_20261107.md`，SHA-256 `30db6e49…`）：第 95 行已改为“F1 补测后新增 EROFS（目标/暂存）、ENOSPC、EACCES 四类真实内核失败路径用例；其余失败分支（fsync/EIO、写入失败、目标被替换、通用 linkat 错误等）仍为 mock，不计入真实内核证据”；第 103 行已改为“F3 补测后，64 MiB/大小类型边界、四种部分封条、多描述符、peer 关闭/无接收方、不写盘断言均已有真实用例；读取期 size/seal 变化与 EMLINK/EXDEV 仍只能 mock”，两处均指向 `docs/OFFLINE_FAILURE_TESTS_REVIEW_20261107.md`（PASS-with-limits）。
- **一致性**：与 §6.1/§6.2/§6.3 的真实/mock 划分一致，未发现新的过度声称。注意 R3 的范围限制仍适用：第 103 行“不写盘断言已有真实用例”应理解为“非 mock 用例”，其证明范围仍限定为“cwd/TMPDIR/HOME 三目录残留 + Python 层审计”（见 §7 R3）。

### 10.3 新引入问题：**未发现**

- 除上述 1 行测试改动与证据文档更新外，修改窗口（`>= 2026-11-07 03:00`）内仓库无其它被审相关改动（另有一个并行任务文档 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md`，非本范围）。
- 产品 4 候选哈希未变；指定环境 29/OK、root 环境 29/FAILED(failures=1) 均符合返工预期；`self.fail` 位于 `setUp` 之后的测试体内、无资源副作用（失败时未创建 stage）。

### 10.4 其它发现项状态与设备副作用

- R2（计数口径）已按 Lead 汇报采用“套件总数 29/26，含 MOCK-only”，无需代码改动；R3（不写盘范围）、R4（EROFS generic reason）、R6（fixture 累积）按第 1 轮记录保持，无需代码改动。
- 本轮副作用核对：`fnmusic-ext.service` 的 `InvocationID=53d953f3475047e484e9fdd955b71716`、`MainPID=1870197`、`NRestarts=0`、active 与第 1 轮一致；`/vol1`、staging(700)、target(750) 的 mode/owner/size/**mtime** 均不变；runner marker `0400 1000:1001 35B` mtime 不变；`/tmp` 仅新增 Lead 的 `fnmusic-lead-verify-r1*.sh` 与本复审自有路径（root runner、探针目录均已清理）。
- **报告自身哈希说明**：第 1 轮交付版 SHA-256 = `0038e695b7a4304d6c0e65c5acbe925e6eb503d5c66bcba7e7c95e3554f21049`；本节追加后文件哈希随之变化，最终哈希以第 2 轮交付消息中给出为准（自引用无法在文件内静态记录最终值）。
- **verdict 不变**：仍为 **PASS-with-limits**（本复审不构成生产迁移/部署授权）；未解除项维持 §9。
