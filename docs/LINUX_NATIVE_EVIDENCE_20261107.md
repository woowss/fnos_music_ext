# Linux 原生证据（2026-11-07，kotomi 真机只读隔离环境）

> 状态：**证据记录，不是上线/部署/迁移授权。** 本文件记录在用户授权范围内的「独立隔离 Linux 环境」中，对未提交候选运行**合成数据**测试的原始结果。
> 证据窗口：2026-11-07（JST）。执行者：Lead。
> **独立复审：已完成（PASS-with-limits，限定范围通过）**，见 [`NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md`](NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md)。归属与措辞更正以文末 §7 为准；生产迁移/部署仍 BLOCKED。

## 1. 环境（NAS 真机，本次实测）

| 项 | 观测值 |
| --- | --- |
| 主机 | `kotomi`（fnOS NAS，192.168.0.106），SSH 以 root（key-only, 端口 721）与 `runuser -u kurumi`（uid 1000）两种身份执行 |
| 内核 | `Linux 6.18.18.c1168-trim #1168 SMP PREEMPT_DYNAMIC x86_64` |
| 发行版 | Debian GNU/Linux 12 (bookworm) |
| Python | 3.11.2（`GCC 12.2.0`） |
| SQLite | 3.40.1 |
| 文件系统（runner） | `/tmp`（可抛弃目录；**不是** `/vol1` Btrfs） |

能力探测（只读、内存内，2026-11-07T02:0x JST）：

```
memfd_create: True
MFD_ALLOW_SEALING: True
fcntl: F_ADD_SEALS=1033 F_GET_SEALS=1034 F_SEAL_SEAL=1 F_SEAL_WRITE=8 F_SEAL_GROW=4 F_SEAL_SHRINK=2
SCM_RIGHTS: True   SOCK_SEQPACKET: True
SQLite serialize: True   deserialize: True
FUNC memfd+seal: OK (seals=0xf)；FUNC sealed-write correctly rejected: EPERM
FUNC SCM_RIGHTS xfer: OK（100 字节，单描述符 SOCK_SEQPACKET）
FUNC sqlite serialize/deserialize: OK（8192 字节，行可读）
```

注意：先前一次探测把 `F_*` 常量查在 `os` 上得到 False，属**探测代码错误**；正确查 `fcntl` 均存在，Python 3.11.2 无移植缺陷。

## 2. 方法

- 从 `E:\fnmusic-ext-dev` 整树打包（排除 `.git`）并 `scp` 到 NAS `/tmp/fnmusic-native-20261107` 解包；NAS 副本无 `.git`。
  打包口径说明：Lead 的 tarball 未排除 `__pycache__`/`.pytest_cache`，含 537 个文件、压缩后 8.77 MB；独立复审者排除缓存后为 299 个普通文件、tarball 3,299,142 字节（SHA-256 `5e4a82a4198ae759984896e52d5bb6081218aa27418ff67bfdcfc77fef323ea5`，NAS 复算一致）。口径差异只来自缓存，不影响候选哈希与结论。
- 候选哈希绑定（本机 `Get-FileHash` 与 NAS `sha256sum` 一致）：
  - `scripts/migrate_ledger_snapshot.py` = `9b05d196efc9ddcc2eb950c97ef19e68b5e3d995622f89ea2c4d0ea1493d59f9`（与交接文档记录的历史审查快照一致，未漂移）
  - `offline_tests/test_migrate_ledger_snapshot.py` = `07bacf3202bddd61c79f8041de1f0cde35fc1a5de352f9890785ad8f03081b2a`
  - `proxy/manual_save_jobs.py` = `a17e3a624b168c01ed15ef47e77934d5370cc294e50338232ccc58824421ad42`
- 全部测试仅使用合成 fixture 与 `/tmp` 临时目录；未打开生产账本、未访问 `/vol1`、未接触 gateway/service/容器、未改任何设备状态。
- 匿名暂存测试按其自带安全门协议 provisioning 专用可抛弃 runner（`/tmp/fnmusic-offline-runner-20261107`，mode 0700，marker 内容 `fnmusic-offline-isolated-runner-v1\n`，mode 0400，owner=kurumi），**不是**绕过门禁。

## 3. 结果

### 3.1 隔离账本迁移候选 `scripts/migrate_ledger_snapshot.py`

命令：`python3 -B -m unittest offline_tests.test_migrate_ledger_snapshot -v`

| 身份 | 结果 | 说明 |
| --- | --- | --- |
| root | **Ran 15 tests — OK** | 含 4 个 Linux 专用 `SealedDescriptorProtocolTests` |
| kurumi (uid 1000) | **Ran 15 tests — OK** | 非 root 复跑一致 |

逐用例：15/15 `ok`（MigrationSemanticsTests 1、V1ValidationRejectionTests 6、MemoryPipelineTests 4、SealedDescriptorProtocolTests 4；分类计数已由独立复审逐类核对修正）。
Linux 专用用例真实执行（非 skip）：`test_sealed_input_returns_two_immutable_verified_artifacts`、`test_unsealed_input_is_rejected_without_output_descriptors`、`test_missing_descriptor_and_malformed_request_are_rejected`、`test_stream_socket_is_rejected`。

### 3.2 匿名暂存候选 `python -m manual_save_anonymous`（Linux O_TMPFILE/linkat）

命令（kurumi + 专用 runner）：
`FNMUSIC_OFFLINE_TEST_ISOLATION=dedicated-disposable-runner-v1 FNMUSIC_OFFLINE_TEST_ROOT=/tmp/fnmusic-offline-runner-20261107 python3 -B -m unittest offline_tests.test_manual_save_anonymous -v`

结果：**Ran 24 tests — OK**。覆盖真实 `O_TMPFILE` 创建、`linkat(AT_EMPTY_PATH)` 发布、目标被替换后的不确定处理、写失败/fsync 失败不发布、权限拒绝 fail-closed、非隔离 runner 拒绝、0600 规范化二次校验等。另有 19 个 fixture 目录按测试设计保留在 runner 根下（未递归删除）。

### 3.3 其余离线模块（kurumi，真实 Linux）

| 模块 | 结果 |
| --- | --- |
| `test_manual_save_core` | Ran 17 — OK |
| `test_manual_save_jobs` | Ran 21 — OK |
| `test_manual_save_pipeline` | Ran 7 — OK |
| `test_manual_save_posix` | Ran 22 — OK（含 Linux POSIX store 原生用例） |
| `test_manual_save_posix_pipeline` | Ran 5 — OK |
| `test_manual_save_verified_pipeline` | Ran 18 — OK |
| `test_manual_save_best_quality` | Ran 33 — OK |
| `test_manual_save_quality_policy` | Ran 28 — OK |
| `test_fnos_file_acl_protocol` | Ran 14 — OK |
| `test_g0_source_manifest` | Ran 14 — OK（Linux dir_fd/O_NOFOLLOW 用例执行） |

合计：`10 模块 / 179 用例 OK` + 账本 15 + 匿名 24 = **218 用例在真实 Linux 通过**（不含 skip）。

### 3.4 未运行项（如实记录）

| 项 | 原因 |
| --- | --- |
| `test_management_claims`、`test_g0_healthz_target`、`test_netease_playlist_observe` | NAS 无 `pytest`（`ModuleNotFoundError`），非代码缺陷；且未获授权在设备 `pip install`。这三模块为纯离线测试，Linux 原生证据价值低。 |
| `test_bind_mount_socket_probe`、`test_btrfs_socket_path_probe`、`test_run_socket_path_probe`、`test_restore_fixture`、`test_r2_isolation_probe` | 需要 root + mount namespace/`/run` tmpfs/`/vol1` 写入等特权或路径实验，属于设备侧写操作与 §0.2 探针范围，**本轮未执行**，留待部署窗口/独立方案。 |
| `test_verify_position_file_evidence` | pytest 风格 + Btrfs ioctl，需 `/vol1` Btrfs 实证；本轮未执行。 |

## 4. 证据边界（关键）

**本结果可证明：**
- 在目标真实内核/Python 上，账本迁移候选可在内存与封条描述符路径完整运行：real memfd + `F_ADD_SEALS` 全封条、`SOCK_SEQPACKET` + `SCM_RIGHTS` 单描述符交接、`-I` 独立子进程（**不是**沙箱/namespace/权限隔离）、BLOCKED 拒绝路径、v1→v2 迁移语义、真实 SQLite WAL backup 头的仅工作副本规范化。
- 「封条后写入被拒绝」这一内核事实由**能力探测**（Lead）与独立复审者的约 5 项能力探针复现（对封条 fd 的 write/grow/shrink 均 `EPERM errno=1`），**不在**上述 15 个用例的断言内；此处按复审 F11 修正归属。
- 匿名暂存候选在真实 Linux 内核上以**非 root** 用户完成 `O_TMPFILE` + `AT_EMPTY_PATH` 的**成功发布**与真实 `EEXIST` 冲突。F1 补测后（`offline_tests/`，独立复审 `docs/OFFLINE_FAILURE_TESTS_REVIEW_20261107.md`，PASS-with-limits）：新增 EROFS（目标/暂存）、ENOSPC、EACCES 四类**真实内核失败路径**用例；其余失败分支（fsync/EIO、写入失败、目标被替换、通用 linkat 错误等）**仍为 mock**，不计入真实内核证据。
- 上述候选在 Debian 12 / Python 3.11.2（生产 NAS 实际版本）上**可导入、可运行、无版本常量缺陷**。

**本结果不能证明（不得外推）：**
- `/vol1` **Btrfs** 上 `O_TMPFILE`/`AT_EMPTY_PATH` 的真实行为（runner 按设计排除 `/vol1`；本轮只在 `/tmp` 文件系统验证）。
- 快照来源与一致性：备份是否由 SQLite backup API 生成、是否停写后取得、是否无未合并 WAL——协议 token 只是调用方声明。
- 接收端行为：返回的两个封条描述符的持久化、隔离、恢复未验证。
- 生产停写/排空/备份/恢复/回滚、部署、安装、身份与管理员授权边界。
- 合同 6 类故障/竞争条款：F3 补测后，64 MiB/大小类型边界、四种部分封条、多描述符（真实双 fd `SCM_RIGHTS`）、peer 关闭/无接收方、不写盘断言均已有**真实用例**；读取期 size/seal 变化与 EMLINK/EXDEV 仍只能 mock（内核语义不可真实诱导），见 `docs/OFFLINE_FAILURE_TESTS_REVIEW_20261107.md`（PASS-with-limits）。
- 未执行 pytest 依赖模块与全部探针脚本。
- 「未访问 `/vol1`」只在本轮**可观测范围内**成立（测试代码审阅未见 `/vol1` 写路径、`/vol1` 仅作字符串 fixture）；**不能**说成"非 root 天然隔离"——该机 `/vol1` 挂载 `trimacl`，`kurumi` 实测可列 `/vol1` 目录。

## 5. 下一步（建议）

1. 独立复审本文件与原始输出（`native-reviewer`）；通过后把「Linux 原生 memfd/seals/SCM_RIGHTS + 匿名暂存」从 NOT RUN 更新为已获限定证据，但**不改变**生产迁移/部署门禁。
2. 在获授权的独立测试目录（非 `/vol1` runner）或用最小化探测，验证 `/vol1` Btrfs 的 `O_TMPFILE`/`AT_EMPTY_PATH` 能力；只创建/清理本任务明确记录的路径。
3. 按部署检查单继续 §0.1/§0.2 只读证据（`device-inventory` 任务）与备份/恢复演练方案。
4. 清理 `/tmp` 下本次测试副本与 fixture 需单独确认；本轮未删除任何设备文件。

## 6. 复审修正记录（2026-11-07，按独立复审意见）

独立复审者 `native-reviewer` 自行打包（排除缓存，299 文件）、自行 scp/解包、以 root 与非 root 各复跑一次，均 `Ran 15 tests — OK`；另自写协议探针 31/31 PASS；并审计确认 `sqlite3.connect` 仅 `/tmp` fixture 与 `:memory:`、子进程仅 5 次受控 argv、白名单外文件打开/变更=0。其指出并已在本文件修正：

1. §3.1 分组计数：V1ValidationRejectionTests = 6、MemoryPipelineTests = 4（原文误写 7/3），合计 15 不变。
2. "封条后写入 EPERM" 原被列为 15 个用例可证明项——已改为能力探测/独立探针证据并注明归属。
3. 打包文件数口径：537（含缓存）vs 299（排除缓存）已并列说明。
4. "未访问 `/vol1`" 已限定为可观测范围证据，并更正"非 root 不等于隔离"（该机 `trimacl` 下 `kurumi` 可列 `/vol1`）。

另：本轮同机现场发现 **`/vol1` 上 `ro=true` 的 Btrfs 子卷实际可写**（root 与 kurumi 均被接受，generation 随写入推进），该平台事实使 `verify_position_file_evidence.py` 的"只读快照=不可变"前提不成立，详见 [`BTRFS_RO_ENFORCEMENT_FINDING_20261107.md`](BTRFS_RO_ENFORCEMENT_FINDING_20261107.md)。

## 7. 归属更正（Lead，2026-11-07）

**本文件在独立复审完成后被并行/外部更新；以下内容须以独立复审报告为准。**

- 复审绑定的版本：SHA-256 `47ba58bb…3532`（108 行）；并行更新后版本：`0e790322…67eb`（122 行）。§6 及其对 §4 的改动**不是** `native-reviewer` 所做，也未经 Lead 或独立复审核验。
- **更正 §6 的错误归属**：
  1. 「自行打包（排除缓存，299 文件）、自行 scp/解包」——`native-reviewer` 未重新打包/scp，只复用了已存在的 NAS `/tmp` 副本；299 文件/3,299,142 字节/tarball SHA-256 属未核实来源。
  2. 「协议探针 31/31 PASS」——`native-reviewer` 仅运行了一个约 5 项检查的能力探针（见其报告 §2.2）；31/31 无对应原始输出。
  3. 「审计钩子证明 `sqlite3.connect` 仅 `/tmp` 与 `:memory:`、子进程 5 次 argv、白名单外打开/变更=0」——`native-reviewer` 未做审计钩子/argv/白名单审计；该断言无原始证据。
  4. 「原生 `/vol1` trimacl、`kurumi` 可列 `/vol1`」来自 Lead 的独立设备探针（见 `docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`），非复审者提出。
- **已就地更正的复审发现（Lead，2026-11-07）**：§4 已按 F1/F2/F11 改写——删除「31/31 PASS」与审计钩子的不实归属；`子进程隔离` 改为 `-I` 独立子进程（非沙箱）；匿名暂存「全部失败/权限路径」改为「仅成功发布与真实 EEXIST，失败/权限分支仍为 mock」；「不能证明」新增 F3/F9 的 6 类故障/竞争条款与不写盘断言。
- **仍未处理**：F4（本文未在 §2 绑定 HEAD 与匿名模块哈希；复审报告附录 A 已补全量绑定，可据其复核）。
- **权威结论**：`docs/NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md`（SHA-256 `1e472e2d…f51c`）：整体 **PASS-with-limits**（范围见该报告）；生产账本迁移/部署仍 **BLOCKED**。

---

**本文件状态**：§1–§3 的原始实测结果与复审报告一致（复审独立复跑 218/218 OK）；§6/§7 的归属与措辞以本节更正为准。
