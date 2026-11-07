# Linux 原生证据独立复审（2026-11-07）

> **复审日期**：2026-11-07（JST）
> **复审者**：`native-reviewer` —— 独立复审者，**未参与**原证据的采集、执行或文档撰写；与被审执行者 Lead 分离。
> **被审文档**：`docs/LINUX_NATIVE_EVIDENCE_20261107.md`。本报告结论依据版本 SHA-256 `47ba58bb21da58a66786752874e379389428124e799687d17a336c538f235532`（108 行，复审时）；复审完成后该文件被并行更新为 `0e7903229da63955291c5ade5f0a8690d7b1a0e57028fe1e294548f6b5dd67eb`（122 行），新增「复审修正记录」并将部分本审查**未执行**的事项归入本审查者名下——见 §5「复审后文档变更与归属更正」。
> **被审快照**：`E:\fnmusic-ext-dev`，分支 `codex/fnmusic-prelaunch-20261025`，HEAD `cf843a40d735fb9a20e1bc850f711cd4693cb3aa`；审查开始时工作区 41 项未提交变更（17 modified + 24 untracked），审查结束时 46 项（并行任务与本报告新增 5 个未跟踪文件；被审的 15 个复跑文件哈希全程未变，见 §2.5）。本审查全程只读，**未执行**任何 `git reset/clean/stash/checkout/commit/push`。
> **先前判定来源**：`E:\fnmusic\PROJECT_HANDOFF_20261107.md:42`（迁移候选 **BLOCKED**）、`docs/OFFLINE_SAVE_JOBS.md:59,63`、`docs/MANUAL_SAVE_PLAYLIST_JOB.md:378`（生产旧账本迁移/部署 BLOCKED）。
> **关联契约**：`docs/ISOLATED_LEDGER_MIGRATION.md`、`docs/OFFLINE_ANONYMOUS_STAGING.md`、`docs/MANUAL_SAVE_PLAYLIST_DEPLOY_CHECKLIST.md`、`docs/MANUAL_SAVE_PLAYLIST_ACCEPTANCE_RUNBOOK.md`。
> **本审查不改变**部署检查单、验收手册或 G0 的任何 BLOCKED 门；也不构成部署、迁移、生产写入授权。

**复审范围**：核对文档 §2 的哈希绑定；核对 §3.1/§3.2/§3.3 的计数与测试代码/实跑是否自洽；检查 Linux 专用用例是否真实执行（skip 条件、skip-as-pass 风险）；在 NAS `/tmp` 副本上独立复跑迁移套件（root 与 kurumi）、匿名暂存 24 例（kurumi + 专用 runner）及 §3.3 全部 10 模块；抽查断言质量；逐条评估 §4 证据边界。

**不在范围**：生产账本/服务/容器/gateway 的任何读写；`/vol1` 写入探针；`/vol1` Btrfs 的 `O_TMPFILE`/`AT_EMPTY_PATH` 原生行为；停写、备份、恢复、回滚、部署、身份与管理员授权；接收端实现；pytest 依赖模块与特权探针的实跑。

---

## 1. 核验表

结论口径：**符合** / **不符** / **unknown**。凡声称强于证据者记「不符」，并说明差在哪。

### 1.1 哈希与快照绑定

| # | 声称（出处） | 我的独立证据 | 结论 |
| --- | --- | --- | --- |
| A1 | `scripts/migrate_ledger_snapshot.py` = `9b05d196…d59f9`（§2） | 本地 `Get-FileHash` = `9B05D196…D59F9`；NAS `sha256sum` = `9b05d196…d59f9`；与交接文档 `PROJECT_HANDOFF_20261107.md:42` 记录一致 | 符合 |
| A2 | `offline_tests/test_migrate_ledger_snapshot.py` = `07bacf32…1b2a`（§2） | 本地 = `07BACF32…1B2A`；NAS = `07bacf32…1b2a` | 符合 |
| A3 | `proxy/manual_save_jobs.py` = `a17e3a62…1ad42`（§2） | 本地 = `A17E3A62…1AD42`；NAS = `a17e3a62…1ad42` | 符合 |
| A4 | 「与交接文档记录的历史审查快照一致，未漂移」（§2） | 交接文档记录同一脚本哈希（大写）；本次复算一致 → 该候选自先前 BLOCKED 审查以来**未改动** | 符合 |
| A5 | 大小写（任务提示） | 文档用**小写**；`Get-FileHash` 输出**大写**；NAS `sha256sum` 小写。按 hex 值比较一致，无内容漂移 | 符合（大小写差异仅表示法） |
| A6 | NAS 副本无 `.git`（§2） | `ls -d /tmp/fnmusic-native-20261107/.git` → `No such file or directory` | 符合 |
| A7 | 被审文档本身 | `Get-FileHash docs\LINUX_NATIVE_EVIDENCE_20261107.md` = `47ba58bb…3532`（本审查绑定此版本，108 行）；复审完成后被并行更新为 `0e7903…67eb`（122 行），归属问题见 §5 | 符合（记录用） |

> 补充绑定（我额外做的，超出文档 §2）：本地与 NAS 对 **31 个 `proxy/*.py` + `scripts/migrate_ledger_snapshot.py`（共 32 个文件）全部哈希一致（0 mismatch / 0 missing）**；本次复跑涉及的 15 个文件哈希见附录 A。因此我的 NAS 复跑可绑定到当前工作区快照，而不是某个不明副本。

### 1.2 环境与隔离

| # | 声称（出处） | 我的独立证据 | 结论 |
| --- | --- | --- | --- |
| B1 | 主机 `kotomi`（fnOS NAS，192.168.0.106），SSH root、key-only、端口 721（§1） | `hostname`=kotomi；`ssh -G kotomi-root` → hostname 192.168.0.106、port 721、user root、identityfile；`BatchMode=yes` 连接成功 | 符合 |
| B2 | 内核 `6.18.18.c1168-trim`（§1） | `uname -r` = `6.18.18.c1168-trim` | 符合 |
| B3 | Debian 12（bookworm）（§1） | `/etc/os-release` PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"；debian_version 12.10 | 符合 |
| B4 | Python 3.11.2、SQLite 3.40.1（§1） | `python3 -V`=3.11.2；`sqlite3.sqlite_version`=3.40.1 | 符合 |
| B5 | runner 文件系统为 `/tmp`，**不是** `/vol1` Btrfs（§1） | `/tmp` 位于 `/dev/sdb2` **ext4**；`/vol1` fstype=**btrfs** | 符合 |
| B6 | 能力探测：memfd/ALLOW_SEALING、F_* 常量 1033/1034/1/8/4/2、SCM_RIGHTS、SOCK_SEQPACKET、SQLite serialize/deserialize、sealed-write EPERM、SCM_RIGHTS 单描述符、sqlite 8192B（§1） | 我以 **kurumi(uid 1000)** 独立探针复现：seals=`0xf`；sealed write → errno 1 **EPERM**；`SOCK_SEQPACKET` 收到 1 个描述符、其 seals=`0xf`；sqlite roundtrip 值可读、8192 字节；F_* 常量逐一相同；`os.O_TMPFILE` 存在 | 符合 |
| B7 | 匿名暂存 runner：`/tmp/fnmusic-offline-runner-20261107`，mode 0700，marker mode 0400、owner kurumi（§2） | `stat`：目录 `drwx------ kurumi:Users`；marker `-r-------- kurumi:Users` 35 字节（=`fnmusic-offline-isolated-runner-v1\n`） | 符合 |
| B8 | 「全部测试仅使用合成 fixture 与 `/tmp` 临时目录；未打开生产账本、未访问 `/vol1`、未接触 gateway/service/容器」（§2） | 审阅复跑涉及的全部测试代码：仅 `tempfile.TemporaryDirectory()` 与 runner 根；`/vol1` 仅作为字符串 fixture（`test_fnos_file_acl_protocol.py`）或受保护路径比较项（`test_manual_save_anonymous.py:65`），无 `open/stat` 访问；无网络/服务调用；我的复跑 cwd 在 `/tmp` 副本内 | 符合 |
| B9 | 「整树打包（排除 `.git`，537 文件，8.77 MB）」（§2） | 当前副本 **540** 文件；多出的 3 个是 `proxy/__pycache__/*.cpython-311.pyc`（mtime 2026-11-07 01:06，早于我的复跑），由测试导入生成；8.77 MB 为打包时压缩体积，现已不可复现 | unknown（可解释漂移） |

### 1.3 计数与实跑自洽

| # | 声称（出处） | 我的独立证据 | 结论 |
| --- | --- | --- | --- |
| C1 | §3.1 root：Ran 15 — OK，含 4 个 Linux 专用 | 我以 root 复跑 `python3 -B -m unittest offline_tests.test_migrate_ledger_snapshot -v` → **Ran 15 tests in 1.281s / OK**，verbose 逐条 15 个 `ok`，无 skipped | 符合 |
| C2 | §3.1 kurumi：Ran 15 — OK | `runuser -u kurumi -- bash -s` 复跑 → **Ran 15 tests in 1.293s / OK** | 符合 |
| C3 | §3.1 逐用例 15/15，分类「MigrationSemantics 1、V1ValidationRejection 7、MemoryPipeline 3、SealedDescriptorProtocol 4」（§3.1） | 静态 `def test_` 计数 + verbose 实测：**1 / 6 / 4 / 4 = 15**。总数正确，**分类计数错误** | 不符（见 F5） |
| C4 | 4 个 Linux 专用用例真实执行（非 skip）（§3.1） | verbose 输出逐个 `ok`：`test_sealed_input_returns_two_immutable_verified_artifacts`、`test_unsealed_input_is_rejected_without_output_descriptors`、`test_missing_descriptor_and_malformed_request_are_rejected`、`test_stream_socket_is_rejected`；类级 `skipUnless(sys.platform=="linux")` 在 Linux 为真，无内层 skip | 符合 |
| C5 | §3.2：Ran 24 — OK（kurumi + 专用 runner） | 以文档 §3.2 的 env/身份复跑 → **Ran 24 tests in 0.140s / OK**，无 skipped；24 = 5 平台用例 + 19 Linux 用例（`AnonymousStoreLinuxTests`） | 符合 |
| C6 | §3.2：「另有 19 个 fixture 目录按测试设计保留在 runner 根下」（§3.2） | 复跑前 runner 内 19 个 `fnmusic-anonymous-test-*`；复跑后 38 个（每次运行 +19），marker 仍在 | 符合 |
| C7 | §3.3：10 模块 179 用例 OK（逐模块 17/21/7/22/5/18/33/28/14/14） | 以 kurumi 逐模块复跑，输出逐项一致：17、21、7、22、5、18、33、28、14、14，全部 `OK`、无 skipped；静态方法计数亦一致 | 符合 |
| C8 | 合计 218 = 179 + 15 + 24（不含 skip）（§3.3） | 179 + 15 + 24 = **218**；三次批次均 `OK` 且无 `(skipped=N)` | 符合 |
| C9 | NAS 无 pytest，三模块因 `ModuleNotFoundError` 未运行（§3.4） | `python3 -c 'import pytest'` → **ModuleNotFoundError**；`test_management_claims.py`、`test_g0_healthz_target.py`、`test_netease_playlist_observe.py` 均 `import pytest` | 符合 |
| C10 | 5 个特权探针 + `test_verify_position_file_evidence` 未运行（§3.4） | 这些文件含 root/mount namespace/`/vol1`/Btrfs ioctl 标记（特权探针 privilege markers 9–239 处；verify 文件 import pytest + Btrfs ioctl）；本轮同样**未运行** | 符合（未运行事实） |

### 1.4 断言质量（是否真的断言了声称的行为）

| # | 声称（§4） | 我的独立证据（读码 + 实跑） | 结论 |
| --- | --- | --- | --- |
| D1 | real memfd + `F_ADD_SEALS` 全封条 | 测试 `_make_memfd` 真调 `os.memfd_create(..., MFD_ALLOW_SEALING)` 并 `F_ADD_SEALS(REQUIRED_SEALS)`；成功路径对返回的两个 fd 用 `F_GET_SEALS` 断言四位全置（`seals & REQUIRED == REQUIRED`）；我的探针独立得 `0xf` | 符合 |
| D2 | 封条后写入被 EPERM 拒绝 | **不在 unittest 中**：测试套件未对封条 fd 尝试写入。该事实来自文档 §1 的临时能力探针；我以独立探针复现 EPERM（errno 1） | 符合（事实成立），但出处需更正（见 F11） |
| D3 | `SOCK_SEQPACKET` + `SCM_RIGHTS` **单描述符**交接 | 成功路径实测 1 个输入 fd、2 个输出 fd；worker 代码 `_recv_one_sealed_fd` 强制 `len(descriptors) != 1 → BLOCKED`。但**没有测试注入 2 个描述符**来验证该拒绝 | 符合（成功路径）/ 部分（拒绝路径未测，见 F3） |
| D4 | BLOCKED 拒绝路径 | 4 个用例：未封条（断言精确 JSON `{"reason":"snapshot_not_fully_sealed","status":"BLOCKED"}`、exit 2、无输出 fd）、缺失描述符/畸形请求（status=BLOCKED、exit 2、无 fd）、`SOCK_STREAM`（精确 JSON `socket_type_rejected`）；均真实子进程执行 | 符合 |
| D5 | v1→v2 迁移语义 | 断言 counts、jobs/positions 全行相等、`user_version=2`、`position_details` 为空、`integrity_check=ok`；事务失败用例断言回滚后仍 v1 且无 `position_details` | 符合 |
| D6 | 真实 SQLite WAL backup 头「仅工作副本」规范化 | 用例真造 WAL backup：断言 raw 头 `(2,2)`、工作镜像 `(1,1)`、原始 bytes 哈希不变、候选可读；我复跑通过 | 符合 |
| D7 | 匿名暂存：真实 `O_TMPFILE` 创建、`linkat(AT_EMPTY_PATH)` 发布、失败/权限路径 | 成功发布用例 `test_real_at_empty_path_publication_when_host_privilege_allows_it` 真调 `linkat`，遇 EPERM/unsupported **主动 fail**，并验证真实 `EEXIST` CONFLICT；我以 kurumi 复跑通过。但写失败/fsync 失败/权限拒绝/目标被替换均为 **mock 注入**（`unittest.mock.patch`），只验证适配器状态机 | 符合（真实成功+冲突）/ 不符（「全部失败/权限路径」过度声称，见 F1） |
| D8 | 0600 规范化二次校验 | `fchmod(0o600)` + 严格复验（regular/uid/0600/nlink==0）；ACL 继承用真实 `os.open`+`fchmod` 包装模拟；失败/不生效分支用 mock 注入并断言固定 reason、fd 已关、无命名回退 | 符合 |
| D9 | 非隔离 runner 拒绝 | `test_linux_file_runner_is_refused_without_explicit_isolation_marker` 只断言 helper 返回 `None`；未端到端断言 Linux 测试类在缺失/错误 marker 时以 `REFUSED` RuntimeError 失败（代码 `setUpClass` 确实如此，但无测试锁定） | 部分（见 F8） |
| D10 | 「不写盘」（合同：不创建输出目录/临时 DB/命名文件） | 测试仅断言 stdout/stderr 为空、失败无输出 fd；无文件系统写操作审计。代码审阅未见写路径（`sys.dont_write_bytecode=True`，无 open/mkdir 输出） | 未覆盖（见 F9） |

### 1.5 边界审计（§4「能证明 / 不能证明」）

| # | 声称（§4） | 我的判断 | 结论 |
| --- | --- | --- | --- |
| E1 | 可证明：真实内核/Python 上内存与封条描述符路径完整运行 | 核心成立；「完整」措辞偏宽（见 F3） | 符合（有保留） |
| E2 | 可证明：`SOCK_SEQPACKET`+`SCM_RIGHTS` 单描述符交接 | 成功路径成立；单描述符**拒绝**路径未测 | 符合（有保留） |
| E3 | 可证明：**子进程隔离** | 实为 `subprocess.Popen(..., -I, -B, pass_fds=...)`；`-I` 是 Python isolated mode，不是沙箱/namespace/权限隔离；工具自身 `unverified_gates` 亦列 `execution_isolation` | **不符**（措辞过强，见 F2） |
| E4 | 可证明：匿名暂存「全部失败/权限路径」 | 失败/权限分支多为 mock 注入，非真实内核错误；真实内核仅成功发布与 EEXIST | **不符**（见 F1） |
| E5 | 不能证明：`/vol1` Btrfs 行为 | 正确；runner 在 `/tmp` ext4，`/vol1` 为 btrfs，未写入 | 符合 |
| E6 | 不能证明：快照来源/一致性（backup API、停写、WAL sidecar） | 正确；协议 token 只是调用方声明 | 符合 |
| E7 | 不能证明：接收端持久化/隔离/恢复 | 正确；仓库内无接收端实现 | 符合 |
| E8 | 不能证明：生产停写/排空/备份/恢复/回滚、部署、安装、身份与管理员授权 | 正确，且与本审查结论一致 | 符合 |
| E9 | 不能证明：pytest 依赖模块与全部探针 | 正确（我亦未运行） | 符合 |
| E10 | 不能证明清单是否穷尽 | **不穷尽**：缺 64 MiB/大小/类型边界、多描述符、部分封条、peer 关闭、读取期 size/seal 变化、不写盘断言（见 F3/F9） | **不符**（清单不完整） |
| E11 | 对先前 BLOCKED 的影响（§5 建议） | 见 §4 结论：可降为**限定范围 PASS**，但生产门禁不变 | 符合（结论方向正确） |

---

## 2. 独立实跑结果（原始输出摘要）

环境：NAS `kotomi`，`6.18.18.c1168-trim`，Debian 12.10，Python 3.11.2，SQLite 3.40.1；副本 `/tmp/fnmusic-native-20261107`（无 `.git`）。

### 2.1 迁移候选（root 与 kurumi 各一次）

```text
# root
$ cd /tmp/fnmusic-native-20261107 && python3 -B -m unittest offline_tests.test_migrate_ledger_snapshot -v
...
Ran 15 tests in 1.281s
OK

# kurumi (uid 1000)
$ runuser -u kurumi -- bash -c 'cd /tmp/fnmusic-native-20261107 && python3 -B -m unittest offline_tests.test_migrate_ledger_snapshot -v'
...
Ran 15 tests in 1.293s
OK
```

两次均 **15/15 ok、skipped=0**；verbose 显示 4 个 `SealedDescriptorProtocolTests` 均真实执行。

### 2.2 独立能力探针（kurumi，内存内，无设备写入）

```text
platform linux python 3.11.2 euid 1000
memfd_seals 0xf
sealed_write_errno 1 EPERM
seqpacket_recv b'hello' fd_count 1 recv_seals 0xf
sqlite_roundtrip_value 1 bytes 8192
F_ADD_SEALS 1033 F_GET_SEALS 1034
F_SEAL_SEAL 1 F_SEAL_WRITE 8 F_SEAL_GROW 4 F_SEAL_SHRINK 2
has O_TMPFILE True
```

与文档 §1 的每项数值一致。

### 2.3 匿名暂存（kurumi + 文档 §3.2 的 runner 环境）

```text
$ export FNMUSIC_OFFLINE_TEST_ISOLATION=dedicated-disposable-runner-v1
$ export FNMUSIC_OFFLINE_TEST_ROOT=/tmp/fnmusic-offline-runner-20261107
$ python3 -B -m unittest offline_tests.test_manual_save_anonymous -v
Ran 24 tests in 0.140s
OK
```

**24/24、skipped=0**；复跑后 runner 根下 fixture 目录 19 → 38（每次运行保留 19 个），marker 保持 `0400 kurumi`。

### 2.4 §3.3 十个模块（kurumi）

```text
### test_manual_save_core            Ran 17 tests in 0.009s   OK
### test_manual_save_jobs            Ran 21 tests in 1.943s   OK
### test_manual_save_pipeline        Ran 7 tests in 0.408s    OK
### test_manual_save_posix           Ran 22 tests in 0.206s   OK
### test_manual_save_posix_pipeline  Ran 5 tests in 0.404s    OK
### test_manual_save_verified_pipeline Ran 18 tests in 0.005s OK
### test_manual_save_best_quality    Ran 33 tests in 1.839s   OK
### test_manual_save_quality_policy  Ran 28 tests in 0.475s   OK
### test_fnos_file_acl_protocol      Ran 14 tests in 0.013s   OK
### test_g0_source_manifest          Ran 14 tests in 0.038s   OK
```

合计 **179/179、skipped=0**；与文档 §3.3 表逐项一致。三个 Linux 专用类（`LinuxPosixStoreTests`、`LinuxPosixPipelineIntegrationTests`、`LinuxManifestTests`）的 `skipUnless(sys.platform=="linux")` 在 Linux 为真，实际执行。

### 2.5 复跑后完整性复核

- 三个被审哈希在**全部复跑后重算不变**（`9b05…` / `07bac…` / `a17e…`）→ 测试未改写被审文件。
- 31 个 `proxy/*.py` + migrate 脚本（共 32 个文件）：本地与 NAS 哈希 **0 mismatch / 0 missing**。
- 全部复跑仅发生在 `/tmp`（副本 + runner）；未写 `/vol1`、未重启/停止服务、未改配置。
- 共享工作区在审查期间有并行任务写入（untracked 24 → 29，新增 device inventory / Btrfs 探针等文件，另含本报告）；这些新增文件不在本次被审范围内，且被审 15 个复跑文件的本地哈希在审查结束时复核仍与附录 A 一致。

---

## 3. 发现

### 阻断
无。被审文档的核心可复现声称全部成立；未发现伪造计数或未执行却声称通过的情况。

### 重要

**F1（重要）§4「匿名暂存…全部失败/权限路径」过度声称。**
文档 §4 第 92 行称「在真实 Linux 内核上以**非 root** 用户完成 `O_TMPFILE` + `AT_EMPTY_PATH` 发布与全部失败/权限路径」。实际真实内核路径只覆盖：成功发布 + 真实 `EEXIST` 冲突（`test_real_at_empty_path_publication_when_host_privilege_allows_it` 的第二次发布）。写失败、`fsync` 失败、EPERM/EACCES/EOPNOTSUPP 权限拒绝、目标被替换均为 `unittest.mock` 注入（`os.write`/`os.fsync`/`_linkat_empty_path`），只验证适配器 fail-closed 状态机；契约文档自身也声明 `_test_only_publish_fd_via_procfs` 是 test double。**建议改为**：「真实内核完成成功发布与 EEXIST 冲突；其余失败/权限分支经 mock 注入验证状态机，不是真实内核错误」。

**F2（重要）§4「子进程隔离」措辞过强。**
测试只是 `subprocess.Popen(sys.executable, "-I", "-B", ..., pass_fds=...)`。`-I` 是 Python isolated mode（忽略 user site/PYTHONPATH 等），**不是**沙箱、namespace、权限或网络隔离；工具报告自身的 `unverified_gates` 就包含 `execution_isolation`。**建议改为**「独立子进程（`-I`）执行」。

**F3（重要）合同若干故障/竞争条款无显式用例，且 §4「不能证明」清单未列出。**
`docs/ISOLATED_LEDGER_MIGRATION.md` 要求：输入为 1 B–64 MiB 的**普通文件**、四个 seal 全置、worker `pread` 后**复核 size/seals**、恰好**一个** `SCM_RIGHTS` 描述符、peer 关闭或 socket 无效时不得发送成功 artifact。当前测试缺：
1. >64 MiB / 0 字节 / 非普通 fd 的拒绝（`snapshot_size_or_type_rejected` 无测试）；
2. 部分 seal 组合（仅缺一个 seal 的拒绝）；
3. >1 输入描述符的拒绝（代码有 `len != 1` 分支，无测试）；
4. peer 提前关闭/无接收方时的行为；
5. 读取期间 size/seal 变化（`snapshot_changed` / `snapshot_seals_changed` 分支无测试）；
6. 「不写盘」的直接断言（见 F9）。
这些不推翻已通过路径，但属于合同条款未覆盖，应写入 §4「不能证明/未覆盖」。

**F4（重要）证据文档未绑定快照 HEAD，也未绑定 §3.2/§3.3 的主体文件哈希。**
§2 只列 3 个 SHA-256；未记录 HEAD `cf843a40…`，未列 `proxy/manual_save_anonymous.py`（`fa4dc6ba…`）、`offline_tests/test_manual_save_anonymous.py`（`e0249168…`）及 10 个 §3.3 模块的哈希。我补做了本地↔NAS 全量绑定（附录 A），但**文档本身**无法让第三方仅凭文档复现快照。建议补 HEAD 或完整文件清单。

### 次要

**F5（次要）§3.1 分类计数错误。**
文档写「MigrationSemantics 1、V1ValidationRejection **7**、MemoryPipeline **3**、SealedDescriptorProtocol 4」；实际静态计数与 verbose 实测均为 **1 / 6 / 4 / 4**。总数 15 正确，仅分类错误，易误导后续读者。

**F6（次要）文件数 537 → 540 漂移未解释。**
文档 §2 记「537 文件，8.77 MB」；当前 NAS 副本 540 文件，多出 3 个 `proxy/__pycache__/*.pyc`（mtime 2026-11-07 01:06，早于我的复跑），由测试导入生成（部分命令未用 `-B`）。不影响被审文件哈希，但「537」已不可复现。建议注明「打包时 537；测试运行后 540（+3 pyc）」。

**F7（次要）§3.2 标题 `python -m manual_save_anonymous` 不是可执行入口。**
`proxy/manual_save_anonymous.py` 无 `__main__`/`main`，实际运行方式是 unittest 模块（正文命令正确）。标题易误导。

**F8（次要）「非隔离 runner 拒绝」只测到 helper 级。**
`test_linux_file_runner_is_refused_without_explicit_isolation_marker` 仅断言 `_verified_isolated_runner_root()` 在错误 env 下返回 `None`；未端到端断言 `AnonymousStoreLinuxTests` 在缺失/错误 marker 时以 `REFUSED` RuntimeError 失败（代码 `setUpClass` 确实如此，但无测试锁定）。

**F9（次要）无「不写盘」直接断言。**
合同称 worker 不创建输出目录、临时数据库或命名迁移文件。测试只断言 stdout/stderr 为空、失败无输出 fd；未做文件系统写审计。代码审阅未见写路径（`sys.dont_write_bytecode=True`、无 open/mkdir），但证据层面未锁定。

**F10（次要）`assertNotIn("123", json.dumps(report))` 断言脆弱。**
`test_sealed_input_returns_two_immutable_verified_artifacts` 用 `"123"`（source_id fixture）证明报告不含行数据；`"123"` 可能偶然出现在 SHA-256 hex 串中。本次通过，但断言强度不足。建议改为结构化键检查或更长随机 fixture。

**F11（次要）§4 的 EPERM 证据出处应写明。**
「封条后写入被 EPERM 拒绝」不在 unittest 套件内，仅来自 §1 的临时能力探针（我独立复现成立）。§4 将其与测试套件结果并列，建议注明来源为能力探针。

---

## 4. 结论与残余未知

### 4.1 整体 verdict

**PASS-with-limits（限定范围通过）** —— 针对**被审证据文档所声称的范围**。

理由：文档的核心可复现声称（§2 哈希、§1 环境与能力、§3.1/§3.2/§3.3 计数、真实 Linux 原生路径）经我独立复跑**全部成立**，且测试断言总体是实质性的（非只测辅助函数）。但存在 1 个重要过度声称（F1）、1 个措辞过强（F2）、合同若干故障/竞争条款未覆盖（F3）与文档绑定不完整（F4），因此**不能给无条件 PASS**；这些是文档修正与补测项，不推翻已复现的核心结论。

### 4.2 对先前 `migrate_ledger_snapshot.py` BLOCKED 判定的明确回答

先前 BLOCKED（`PROJECT_HANDOFF_20261107.md:42`）的两项理由：

1. **「原生 memfd、封条、SCM_RIGHTS 缺证据」→ 已解除。**
   我在生产同版本内核/Python（6.18.18.c1168-trim / 3.11.2 / SQLite 3.40.1）上以 root 与 kurumi 各复跑一次，均 **Ran 15 / OK / 0 skipped**；4 个 Linux 专用用例真实执行；独立探针复现 seals=`0xf`、sealed-write EPERM、`SOCK_SEQPACKET`+`SCM_RIGHTS` 单描述符、SQLite 8192B roundtrip。

2. **「合同规定的故障/竞争场景缺证据」→ 部分解除。**
   4 个 Linux 拒绝路径（未封条、缺失描述符、畸形请求、stream socket）已真实执行并通过；但合同另有 6 类故障/竞争条款仍无显式用例（F3）。

**结论**：可将该候选从 **BLOCKED 改为「限定范围 PASS」**，但范围必须严格限定为：

> 「离线迁移候选在真实 Linux 内核/Python 上、使用合成数据、内存 SQLite + sealed memfd + `SOCK_SEQPACKET`/`SCM_RIGHTS` 的路径，及其已演示的拒绝路径。」

**不得**改为无条件 PASS。**生产账本迁移/部署仍为 BLOCKED**（停写、备份、恢复、回滚、接收端、身份与授权门均未证明）。若团队的 BLOCKED 解除条件是「合同故障/竞争场景全部有显式用例」，则**尚不满足**——还差 F3 列出的 6 类用例；补齐并复审后才可再谈扩大范围。

### 4.3 本审查解除了什么 / 没解除什么

**解除（有证据）**：
- 三个候选文件及工作区快照的哈希绑定（含 31 个 `proxy/*.py` 与 migrate 脚本、HEAD、被审文档）。
- 迁移候选在真实 Linux 上的 15/15（root 与 kurumi），含 4 个 Linux 专用用例真实执行、0 skip。
- 匿名暂存 24/24（kurumi + 专用 runner），含真实 `O_TMPFILE` + `linkat(AT_EMPTY_PATH)` 成功发布与真实 EEXIST 冲突。
- §3.3 十模块 179/179。
- 能力探测各项数值与文档 §1 一致。
- 文档未把 skip 当 pass：所有 Linux 专用类在 Linux 上真实执行，失败路径（如无 runner、无 `O_TMPFILE`、`linkat` EPERM）会 **fail/error 而非 skip**。

**没解除（仍未知/仍 BLOCKED）**：
- 生产账本迁移、停写、排空、一致备份、恢复、回滚、部署、安装、身份与管理员授权（部署检查单/验收手册/G0 门不变）。
- `/vol1` Btrfs 上 `O_TMPFILE`/`AT_EMPTY_PATH`/ACL 的真实行为（runner 为 `/tmp` ext4；本轮只在 `/tmp` 验证）。
- 快照来源与一致性：是否 SQLite backup API 生成、是否停写后取得、是否无未合并 WAL——协议 token 只是调用方声明。
- 接收端：返回的两个封条描述符的校验、持久化、隔离、恢复；仓库内无接收端实现。
- F3 的 6 类合同故障/竞争条款。
- 匿名暂存仍 `PIPELINE_INTEGRATION_ALLOWED=False`，未接入任何 pipeline/路由；`production_ready` 仍 false。
- pytest 依赖模块与 5 个特权探针、`test_verify_position_file_evidence` 未运行。

### 4.4 残余未知（本审查未解决）

1. `/vol1` Btrfs 真实 `O_TMPFILE`/`linkat`/`0600` 规范化行为（需独立 `/vol1` 最小探针，另行授权）。
2. 真实 v1 账本备份（非合成 fixture）在迁移工具上的行为；生产 schema 是否与 pinned V1 完全一致。
3. 接收端实现与「校验 packet/描述符数/seals/长度/哈希」的合同条款。
4. 迁移工具在 peer 异常关闭、超大/畸形输入、读取期状态变化下的实际行为（代码有分支，无测试）。
5. 测试环境与生产环境的差异（`/tmp` ext4 vs `/vol1` btrfs；root/kurumi 之外的权限组合）。
6. 文档 §1 所述「先前探测把 F_* 查在 os 上得到 False」的历史陈述（不可复现，不影响本次结论）。

### 4.5 本审查未做的部分（如实记录）

- 未运行 §3.4 的 3 个 pytest 模块、5 个特权探针、`test_verify_position_file_evidence`（与文档一致：缺 pytest / 需特权 / 属另一任务边界）。
- 未做任何 `/vol1` 写入或 Btrfs 探针；未接触生产账本、服务、容器、gateway、配置。
- 未验证 tar 包「537 文件、8.77 MB」（时点快照不可复现；现副本 540 文件）。
- 未逐条审阅 §3.3 十模块的全部断言内容（已全量复跑并核对计数/0 skip；断言质量重点抽查迁移与匿名两套）。
- 未做变异测试（mutation testing）来独立验证测试失败强度；仅确认现有测试会真实失败（如 `linkat` EPERM 路径主动 fail）。
- 未验证 Windows 对照运行（文档已有 2026-10-31 记录，本轮不重复）。
- 未验证设备服务/容器状态与「未接触」之外的副作用（未访问即无副作用；未做设备级快照比对）。
- 未修改被审证据文档；其抬头「独立复审：进行中」与 §4「独立复审尚未完成」需由 Lead 在本报告落地后更新。

---

## 5. 复审后文档变更与归属更正

**事实经过**：本审查读取并绑定的是 `47ba58bb…3532`（108 行）。报告写入后复核发现该文件已被并行更新为 `0e790322…67eb`（122 行）。

**新版中与本审查一致的部分**（视为采纳）：

1. 状态行改为「独立复审：已完成（PASS，严格限定）」——方向与本报告 `PASS-with-limits` 一致，但请以本报告为准。
2. §3.1 分类计数已修正为 **1/6/4/4**（采纳 F5）。
3. §4 的 EPERM 归属已改为能力探测/独立探针（采纳 F11）。
4. §2 新增 537（含缓存）vs 299（排除缓存）的打包口径说明（部分回应 F6）。

**以下归属与本审查实际工作不符，需由 Lead 更正**（本审查者未执行、未声称）：

- 「独立复审者**自行打包**（排除缓存，299 文件）、**自行 scp/解包**」——**未做**。我使用的是 Lead 已放在 NAS 的 `/tmp/fnmusic-native-20261107` 副本；未重新打包、未 scp、未解包。
- 「另自写协议探针 **31/31 PASS**」——**未做**。我写了 1 个能力探针（约 5 项检查：memfd/seals、sealed-write EPERM、SCM_RIGHTS 单描述符、SQLite roundtrip、F_* 常量），输出见 §2.2；没有 31/31。
- 「审计确认 `sqlite3.connect` 仅 `/tmp` fixture 与 `:memory:`、**子进程仅 5 次受控 argv**、**白名单外文件打开/变更=0**」——**未做**。我未使用审计钩子、argv 计数或文件白名单审计；只做了测试代码审阅与 `/vol1` 引用静态扫描。
- 「`/vol1` 挂载 `trimacl`、`kurumi` 实测可列 `/vol1` 目录」及新版「修正项 2–4」——**非本审查者提出**。我只提出 F5（分类计数）与 F11（EPERM 归属）；修正项 2–4（打包口径、`/vol1` 可观测范围、trimacl）来自并行工作，不应写成「按 native-reviewer 意见」。
- 「独立复审者…299 个普通文件、tarball 3,299,142 字节、SHA-256 `5e4a82…`」——**非本审查产物**。我的 B9 记录的是 Lead 副本当前 540 文件（含 3 个 pyc），并标 unknown。

**新版仍未处理**：F1（§3.2 与 §4 仍称匿名暂存「全部失败/权限路径」）、F2（§4 仍称「子进程隔离」）、F3（合同 6 类故障/竞争条款仍未列入「不能证明」）、F4（仍未绑定 HEAD/匿名模块哈希）。因此新版「已按复审意见修正」只对 F5/F6/F11 成立。

**影响**：新版未对证据实质内容（环境/哈希/计数/边界）做与本报告冲突的改动；上述归属问题属于**记录准确性**问题，不改变本报告 `PASS-with-limits` 与「生产迁移/部署仍 BLOCKED」的结论。建议 Lead 在证据文档中明确区分「native-reviewer 实际所做」与「Lead/并行任务所做」，避免把未执行的审计/打包/探针写成本审查者的证据。

---

## 附录 A：本次复跑涉及文件的哈希（本地 = NAS，逐字节一致）

| 文件 | SHA-256（本地 `Get-FileHash`，小写化；NAS `sha256sum` 相同） |
| --- | --- |
| `scripts/migrate_ledger_snapshot.py` | `9b05d196efc9ddcc2eb950c97ef19e68b5e3d995622f89ea2c4d0ea1493d59f9` |
| `offline_tests/test_migrate_ledger_snapshot.py` | `07bacf3202bddd61c79f8041de1f0cde35fc1a5de352f9890785ad8f03081b2a` |
| `proxy/manual_save_jobs.py` | `a17e3a624b168c01ed15ef47e77934d5370cc294e50338232ccc58824421ad42` |
| `proxy/manual_save_anonymous.py` | `fa4dc6bafcd8bddacba78584ac97e04a6ac29a765791d2cb75f4b4ee6785bc52` |
| `offline_tests/test_manual_save_anonymous.py` | `e02491686632f20bf09fde6831ced6526a28016aee47c6d18f8293b3e2f17be3` |
| `offline_tests/test_manual_save_core.py` | `cb4d17f48975ae80991002c68f8812e9d71596e1d0aa1702eefe35ac9234beb2` |
| `offline_tests/test_manual_save_jobs.py` | `dba59300b95b7f9c0e2324be633300194afccb3b90c94900ab4aedf24ab14f28` |
| `offline_tests/test_manual_save_pipeline.py` | `dd8b88556b135f6e50b03579a8330b882fbaed50ea0b1f808d00bf80e6fb048c` |
| `offline_tests/test_manual_save_posix.py` | `503e56243934fc13738eaff357f3496afa88453116a44fe532a7d9b74f8fefc6` |
| `offline_tests/test_manual_save_posix_pipeline.py` | `a60ee4dad74948b5efe6a0f5de7628ec85a2c95f9e8084425c6d3378c7612c13` |
| `offline_tests/test_manual_save_verified_pipeline.py` | `332b3558bc386380f49dd2541c007dc2f141e396dc56784876e865dfecfa0f1e` |
| `offline_tests/test_manual_save_best_quality.py` | `fb721808775ce0c7cb8da1272adc38348b9072b155aefcd3fef9cc1d6c8ae6eb` |
| `offline_tests/test_manual_save_quality_policy.py` | `bffb017a04cfab9dac7148c0d7a64f124b06a2e90cf64af44ff93a8d10476fed` |
| `offline_tests/test_fnos_file_acl_protocol.py` | `e96a5a5180cf33d36046153d85353b184aa20f0def4325ef8d3906d539c70518` |
| `offline_tests/test_g0_source_manifest.py` | `3db3b8c81653e0369c7110a06ba97ca3faa8539cc2d4914b1af445ba0819ed26` |

另：31 个 `proxy/*.py` 与 `scripts/migrate_ledger_snapshot.py`（共 32 个文件）的本地↔NAS 哈希比对为 **0 mismatch / 0 missing**。

## 附录 B：关键命令

```sh
# 环境/能力
uname -r; python3 -V; python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
stat -f -c '%T' /tmp; stat -f -c '%T' /vol1

# 迁移候选（root / kurumi）
cd /tmp/fnmusic-native-20261107 && python3 -B -m unittest offline_tests.test_migrate_ledger_snapshot -v
runuser -u kurumi -- bash -c 'cd /tmp/fnmusic-native-20261107 && python3 -B -m unittest offline_tests.test_migrate_ledger_snapshot -v'

# 匿名暂存（kurumi + 专用 runner）
export FNMUSIC_OFFLINE_TEST_ISOLATION=dedicated-disposable-runner-v1
export FNMUSIC_OFFLINE_TEST_ROOT=/tmp/fnmusic-offline-runner-20261107
python3 -B -m unittest offline_tests.test_manual_save_anonymous -v

# §3.3
python3 -B -m unittest offline_tests.test_manual_save_core -q   # 其余模块同理
```
