# /vol1 Btrfs 根因 / 缓解 / generation 证据独立复审（2026-11-07，第 3 批）

> **结论：PASS-with-limits（有条件通过）。**
> 被审的两份新证据文档、5 个探针脚本、`docs/evidence/20261107/` 的 4 个原始输出与 `MANIFEST.md`、以及 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` 的 §7 addendum，其**哈希绑定与全部可复现的技术结论**均被本复审者独立复跑验证；根因（`trimacl`）、M1/M2/M3 缓解、generation/barrier 的 A–E 结论在本机复现成立。**无阻断问题**，但存在 **2 个重要发现**（F1：generation 文档的数值未绑定到持久原始输出且跨运行结果不一致未披露；F2：E4 第二方向检查是同义反复）与 5 个次要发现，因此不能给无条件 PASS。
> **本复审不改变 §0.1/§0.2、P7 A4 或任何部署门禁**；`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须仍为 `False`，扫描器继续返回 `unknown`，`PIPELINE_INTEGRATION_ALLOWED=False` 未变。被审文档是现场证据，不是上线/授权依据，本复审也不是。
> 本复审者（`final-reviewer`）未参与被审证据的采集或撰写。复审期间唯一的新增 `/vol1` 写入是**受审脚本本身**在复跑中创建并删除的一次性探针树（任务明确允许的复跑），运行后已核验无残留；设备上无其它写操作。

## 0. 抬头与复审对象

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（复审窗口 JST 11:11–11:2x / NAS 本地 CST 10:11–10:2x） |
| 复审者 | `final-reviewer`（独立角色，未参与本批证据采集/撰写） |
| 被审证据文档 | `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md`（7,851 B，SHA-256 `91f65503…81f2`）；`docs/BTRFS_GENERATION_SEMANTICS_20261107.md`（9,063 B，SHA-256 `ee374a49…ad0f`） |
| 被审设计追加 | `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md`（111,567 B，SHA-256 `7f4304e8…8ed2`）的 §7 Addendum（第 608–746 行） |
| 被审原始输出 | `docs/evidence/20261107/`：`fnm-ro-rootcause.log`（2,414 B，`8cbbab23…8cbad`）、`fnm-ro-mitigation.log`（2,466 B，`1a4b893c…afb8b`）、`BTRFS_GENERATION_SEMANTICS_OUTPUT.json`（5,929 B，`6add2ee5…f60fb`）、`BTRFS_GENERATION_BARRIER_OUTPUT.json`（1,732 B，`1038a039…9e84`）、`MANIFEST.md`（2,438 B，`e439e7c9…0163`） |
| 被审脚本 | `btrfs_ro_rootcause_probe.sh` `d5074d63…83db`（4,865 B）；`btrfs_ro_mitigation_probe.sh` `c7e513b3…15a2`（5,178 B）；`write_probe_helper.py` `a6ce5b77…9462`（226 B）；`btrfs_generation_semantics_probe.py` `7f27a07a…a7d`（7,564 B）；`btrfs_generation_barrier_probe.py` `4795bba5…56e7`（7,736 B） |
| 扩展仓库快照 | 分支 `codex/fnmusic-prelaunch-20261025`，HEAD `cf843a40d735fb9a20e1bc850f711cd4693cb3aa`；被审文件均为 untracked（`??`），本复审未修改其中任何一项 |
| 复审方式 | 只读核验 + 一次独立复跑（4 个脚本/探针）：2026-11-07 10:14:39–10:16:04 CST；运行后恢复 NAS 原始日志字节 |

**复审范围**：①哈希绑定（文档 ↔ 脚本 ↔ 持久证据 ↔ NAS `/tmp` 原件）；②逐项核对文档数字与原始日志/JSON；③独立复跑四个探针并比对；④实验设计是否能支撑其结论、边界是否诚实；⑤脚本清理/写入范围审计；⑥`docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` §7 addendum 的绑定与内容一致性。
**不在范围**：不实现/不部署设计；不验证生产扫描链路、账本、停写/备份/回滚；不打开任何数据库；不尝试定位 `trimacl`/`trimafs2` 补丁代码；不把本机结论外推到其它内核/fnOS/挂载组合。

## 1. 哈希绑定（独立重算）

### 1.1 本地文档/脚本/证据

| 对象 | 本地 SHA-256 | 文档声称 | 判定 |
| --- | --- | --- | --- |
| 根因文档 | `91f65503030ed0f6122944bf79f5494ecc58dd9a349d6342637de3fcd56e81f2` | —（§7 addendum 与本文重算一致） | 一致 |
| generation 文档 | `ee374a49426626c38c031a9e7217668041a91c07714af3fe9ab9716f921bad0f` | — | 一致 |
| rootcause 脚本 | `d5074d637d8390857059424e71f072eae33d86ccf63ddbc3a2e71791481383db` | §1 同值 | 一致 |
| mitigation 脚本 | `c7e513b3d2167558e65d0f36eb4a010c4daed6a5d697669e8a0b736bab6115a2` | §1 同值 | 一致 |
| write_probe_helper | `a6ce5b775652ebcc32b711601f74400f8ff182b13dcb04567c41ebf6ec289462` | §1 同值 | 一致 |
| semantics 探针 | `7f27a07a2ceeaec75c607425cc66bcef9f38b3af2492eca5e0d52245b8cd6a7d` | §1 同值 | 一致 |
| barrier 探针 | `4795bba548fd9e54076075a5acbd17201721726013d669aea510dd1ba6e456e7` | §1 同值 | 一致 |
| 根因日志 | `8cbbab2364cffe7e9b34d63789b5de7244cb381f6afb4e6ea2a06c8c7ae8cbad` | §1/`MANIFEST` 同值 | 一致 |
| 缓解日志 | `1a4b893c4f11dc14492a14419fa5bfe7827a045be2022c561b3527ed60aafb8b` | §1/`MANIFEST` 同值 | 一致 |
| semantics JSON | `6add2ee5ac7169bde761de43e915a24818da5c7b8f27d9706eab0add8b7f60fb` | §1/`MANIFEST` 同值 | 一致 |
| barrier JSON | `1038a03912e10d1414004b181b7a1eafff1ed480b0c3ca5e82c0d79bf66e9e84` | §1/`MANIFEST` 同值 | 一致 |
| `MANIFEST.md` | `e439e7c90eee2cfc9f3c9e28dba6a0e669c530fcf3be9fd8ad52a59032410163` | — | 一致 |

### 1.2 NAS `/tmp` 原件（复跑前逐项重算；恢复后再次核验）

根因文档 §1 声称"NAS `/tmp` 副本三个文件与本地逐字节同哈希"：**成立**——`/tmp/fnm-ro-rootcause.sh`=`d5074d63…`、`/tmp/fnm-ro-mitigation.sh`=`c7e513b3…`、`/tmp/fnm-writecheck.py`=`a6ce5b77…`（均与仓库文件同哈希）。原始日志 `/tmp/fnm-ro-rootcause.log`=`8cbbab23…`、`/tmp/fnm-ro-mitigation.log`=`1a4b893c…`；generation 脚本 `/tmp/fnm-generation-probe.py`=`7f27a07a…`、`/tmp/fnm-barrier-probe.py`=`4795bba5…`；`/tmp` 两个输出 JSON 与 `docs/evidence/20261107/` 对应文件**同哈希**（`6add2ee5…`/`1038a039…`）。`MANIFEST` 声称的 4 个文件哈希全部复算一致。

### 1.3 设计文档 §7 addendum 的自校验

独立重算（Python 字节级）：新文件 111,567 B；**前 89,481 字节** SHA-256 = `ae7bc37f5fbb37725005937c0efd1138d0f4f4012caf515a27a81fbf153b64ac`（= §7.8 声称的旧前缀哈希，**一致**）；文末 `DOC-HASH-STAMP` 值 `cc41080127c42913b88bee6c7f8189dbeab80f7c829eb2209f069e7971e1878a` = `sha256(stamp 行之前全部字节)`（**自校验通过**）；旧文件 89,615 B − 新前缀 89,481 B = 134 B（= 旧自校验戳行长度，量级自洽）。§7.1 引用的新证据文档（7,851 B/`91f65503…`）与 3 个脚本哈希均与本地重算一致。→ **"§0–§6 未被改动、§7 只追加"成立**。

## 2. 逐项核验（文档声称 → 原始输出）

### 2.1 根因对照实验（根因文档 §2；原始日志 = Lead 实际运行原件，哈希已绑定）

| # | 声称 | 原始日志证据 | 判定 |
| --- | --- | --- | --- |
| E1 | `/tmp` 512 MiB loop btrfs，挂载**无** `trimacl`，RO 快照内创建文件 → `WRITE_REJECTED 30 Read-only file system`（标准行为） | 日志：`truncate ok` → `mkfs ok` → `mount ok options=rw,relatime,ssd,discard=async,space_cache=v2,subvolid=5,subvol=/`（**无 trimacl**）→ `prop=ro=true` → `WRITE_REJECTED 30 …`；entries 仅 `payload.txt` | 符合 |
| E2 | **同一 loop 镜像**，挂载**加** `trimacl`，同一 RO 快照内创建 → `WRITE_ACCEPTED`，文件真实出现，`property get ro` 仍 `true` | 日志：第二次 mount `options=…,trimacl,…`；`prop=ro=true`；`WRITE_ACCEPTED …/e2-write.txt`；entries 含 `e2-write.txt` | 符合 |
| E3a | `/vol1` 正常 RW 路径（`trimacl`）向 RO 快照直接创建 → `WRITE_ACCEPTED` | 日志：`snapid=686 … prop=ro=true`；`WRITE_ACCEPTED …/snapshot-ro/e3a-direct-write.txt` | 符合 |
| E3b | 同一快照在私有 mount namespace 内以 `-o ro,subvolid=<id>` 只读挂载后写入 → `WRITE_REJECTED 30` | 日志：`RO_MOUNT_OPTIONS=ro,relatime,trimacl,space_cache=v2,subvolid=686,subvol=/…`（**含 ro 且仍显示 trimacl**）；`WRITE_REJECTED 30 …` | 符合 |

脚本审计：E1/E2 复用同一 `/tmp/fnm-ro-loop.img`（E1 `mkfs`，E2 仅换挂载选项），**唯一变量是 `trimacl`**；E3b 用 `unshare -m` 私有 namespace。→ 根因结论"触发点是 `trimacl` 挂载选项（本机）"有对照支撑；**未定位补丁代码**被文档明确声明。

### 2.2 缓解 M1–M4（根因文档 §3；`fnm-ro-mitigation.log` 为原始运行原件）

| 方案 | 声称 | 原始日志证据 | 判定 |
| --- | --- | --- | --- |
| M1 RO 挂载 | E3b 后写入 EROFS（即使选项串仍显示 trimacl）；同一子卷经 RW 路径仍可写（E3a） | 同 §2.1 E3b/E3a | 符合（视图层 RO 成立；RW 路径开放） |
| M2 send/receive | send 8,395,408 B；receive rc=0；收到子卷设 `ro` 后写入 `EROFS`；再 `-o ro` 重挂载仍 `EROFS`；收到内容哈希与快照完全一致（`916ff831…`） | 日志：`send rc=0 bytes=8395408 sha=6d103c13…`；`mkfs rc=0`；`mount rc=0 opts=rw,…,space_cache=v2,…`（**无 trimacl**）；`receive rc=0`；`set received ro rc=0 prop=ro=true`；两次 `WRITE_REJECTED 30`；`received=916ff831… snapshot=916ff831… equal=yes` | 符合 |
| M3 reflink | `cp --reflink=always` rc=0；`filefrag` 源/副本**同一物理 extent + shared**；改源→副本哈希不变；改副本→源哈希不变；8 MiB | 日志：`reflink cp rc=0`；两文件 filefrag 均 `0.. 2047: 694057586.. 694059633: 2048: last,shared,eof`；`copy unchanged after original modified: yes`；`original stable after copy modified: yes` | **部分符合（F2）**：filefrag 与"改源→副本不变"成立；"改副本→源不变"的检查是**同义反复**（见 F2），不能作证 |
| M4 普通拷贝 | 本次未单独重测；`/tmp` 为 ext4（设计期已知） | 文档自述未测；设备侧 `/` 与 `/tmp` 均 `/dev/sdb2 ext4`（本复审实测），`/tmp` 可用 39 G | 符合（"未实测"边界诚实） |

### 2.3 generation 语义（generation 文档 §2；**注意：文档表格数值来自"第一次运行"，持久 JSON 是"第二次运行"**）

`MANIFEST` 明确：`BTRFS_GENERATION_SEMANTICS_OUTPUT.json` 是**第二次运行** stdout（"第一次运行结论相同、时间戳/generation 值不同"）。本复审反推的数值映射（文档 §2 = 归档 JSON − 34）：

| # | 操作（文档 §2） | 文档值 | 归档 JSON（第二次运行，`time 10:08:54`） | 结论一致性 |
| --- | --- | --- | --- | --- |
| 1 | 创建文件未 sync | 1692585 不变 | before 1692619 = after_immediate；下一步起点 1692620（+1） | 一致（数值差 34） |
| 2 | 创建文件 + sync | 1692586→1692587 | before 1692620 → after_sync 1692621 | 一致 |
| 3 | 写 + 仅 fsync(file) | 立即 1692587 不变；"稳定后"1692588 | before/imm 1692621；after_sync=null；下一步起点 1692622（+1） | 一致（+1 来自脚本内部 settle `os.sync()`，见 F6） |
| 4–9 | chmod / truncate / rename / append / 重写同字节 / 创建+删除 | 各 +1 | 各 `changed_after_sync=true`，依次 1692622→1692628 | 一致 |
| 10 | 只读读取 | +1（atime） | `read_only_noop` 1692628→1692629 | 一致 |
| 11–13 | ABA 写 A、恢复原字节、utimensat | 各 +1 | 1692630→1692633 | 一致 |
| 14 | 写后不 sync 等 35 秒 | **35 秒后仍不变（1692599）** | **after_wait 1692634（changed=true；1692633→1692634）** | **不一致（F1）** |
| 15 | 向 RO 快照写入（trimacl 绕过）+ sync | 1692602→1692603（+1） | 1692636→1692637 | 一致 |
| 16 | 向 RO 快照再写、不 sync 等 35 秒 | 1692603→1692604（+1；"提交时机不稳定"） | 1692637→1692638（changed=true） | 一致 |

补充值：文档 §2 "base treeid=689 / snapshot-ro treeid=690"；归档 JSON 为 **693/694**（差 4，推断为两次运行之间其它子卷创建所致）；两处 base/flags 均 `flags=1`。→ 文档 §2 的**结构**（16 步、每步结论）与归档运行一致，但**具体数值不是归档 JSON 的值**；且 #14 的运行结果相反（F1）。

### 2.4 barrier 补测 A–E（generation 文档 §8；数值来自"第一次 barrier 运行"，持久 JSON 为第二次运行）

归档 `BTRFS_GENERATION_BARRIER_OUTPUT.json`（`time 10:10:08`）与文档 §8 数值映射（文档值 + 31 = 归档值）：

| 测试 | 文档 §8 | 归档 JSON（第二次运行） | 判定 |
| --- | --- | --- | --- |
| A rename 后普通 read（RW relatime） | +1（1692611→1692612） | `A_plain_read_after_rename`: 1692642→1692643, changed=true | 符合（映射 +31） |
| B `O_NOATIME` 读 | 不变（1692613） | 1692644→1692644, changed=false | 符合 |
| C 私有 namespace `rw,noatime` 挂载读全部文件（atime 预置=1） | 不变（1692616）；mount/unmount 不推进 | `C_noatime_rw_mount_read`: 1692647→1692647, changed=false；`mount_cmd_rc=0`；`target_atime_before=1`；`stdout=read-all` | 符合 |
| D 写+fsync 后 `syncfs()` | fsync 后不变；`syncfs` 后 +1 | `D_syncfs_barrier`: before_write=1692644、after_write_before_syncfs=1692644（false）、after_syncfs=1692645（true） | 符合 |
| E 扫描窗口内并发 writer | +1（1692614→1692615） | `E_concurrent_writer_bracket`: 1692645→1692646, changed=true；writer `writer-done` | 符合（映射 +31；E 值小于 C 是因为实际执行顺序 A,B,D,E,C，文档按主题排序） |

→ A–E 的**数值与结论均一致**（跨运行差 31），但同样存在"文档数值≠归档 JSON"的绑定问题（F1，程度较轻：A–E 五个结论在两个运行中相同）。

### 2.5 边界与清理声明（两份文档 §5/§6/§7 对照）

| 声称 | 独立核验 | 判定 |
| --- | --- | --- |
| 仅本机（fnOS 1.2.0903 / 6.18.18.c1168-trim / trimacl）；不外推 | 设备实测同值；文档两处 §5 均明确 | 符合（边界诚实） |
| 未定位 `trimacl`/`trimafs2` 补丁 | 文档明确；本复审未尝试（超出范围） | 符合 |
| E3b 以 root 执行；普通用户未单独实跑 | 文档声明；脚本以 root 运行 | 符合（但 M2/E5 也只测 root，见 F7） |
| E4 仅 8 MiB；未做库级/坏块 | 日志 `bytes=8388608`；文档声明 | 符合 |
| E5 接收端在 `/tmp` loop（易失、位于 ext4）；未验证持久 scratch 卷/大文件/失败恢复 | 设备 `/tmp` 在 `/dev/sdb2 ext4`，39 G 可用；文档声明 | 符合 |
| M4 本次未单独重测 | 文档自述；无 M4 日志 | 符合（边界诚实） |
| generation：未测并发写入/大文件/长事务；"35 秒未提交"是单次观测；fsync 解释未定位内核路径；未测 noatime/O_NOATIME（§5） | §5 文字本身与 §2 一致；但**被 §8 追加后已过时**（§8 已测 A/B/C/E） | 部分（F4：§5/§7 未随 §8 更新） |
| 非属主 `O_NOATIME`（需 CAP_FOWNER）未测 | §8 修订结论明确声明 | 符合（边界诚实） |
| 脚本 `trap`/`finally` 自清理；运行后 `/vol1` 无探针子卷/无遗留镜像/流/挂载 | 静态审计成立；**本复审复跑后实测**：4 个探针树均不存在、子卷总数 49（复跑前后相同）、`losetup`/`findmnt` 无 fnm 残留、`/tmp` 无镜像/流残留 | 符合（详见 §3.5） |

## 3. 独立复跑（4 个脚本/探针，2026-11-07 10:14:39–10:16:04 CST）

运行方式：从仓库原始文件（哈希见 §1）`scp` 到 NAS `/tmp/fnm-r3-review/runtime/`（与 NAS 原副本同哈希）后执行；**先确认 4 个探针树均不存在**，运行后核验无残留；随后把被复跑覆盖的 NAS 原始日志恢复为原始字节。

### 3.1 复跑前状态（NAS 本地 10:13:44）

- 4 个探针树（`…-ro-probe`、`…-ro-probe2`、`…-gen-probe`、`…-barrier-probe`）**均不存在**；`btrfs subvolume list /vol1` 共 49 项，无探针子卷；无 fnm loop 设备、无 fnm 挂载、无探针进程。
- 服务 `fnmusic-ext.service` `active/running`，`MainPID=1870197`、`InvocationID=53d953f3…`、`ActiveEnterTimestamp=2026-11-01 17:18:30`、`NRestarts=0`；`/vol1/1000` 47 项、集合 SHA `c78985ba…`；生产路径指纹与第 1 轮复审一致。
- NAS 原件 9 个文件哈希与文档/仓库**全部 MATCH**（脚本 3 + 日志 2 + 脚本副本 2 + 输出 JSON 2），并已备份到 `/tmp/fnm-r3-review/original/`。

### 3.2 运行 1：根因 + 缓解（10:14:39–10:14:41，两脚本顺序执行，均 rc=0）

| 检查点 | 复跑结果 | 与 Lead 运行比较 |
| --- | --- | --- |
| E1 无 trimacl | mount options 无 trimacl；`WRITE_REJECTED 30 Read-only file system` | 一致 |
| E2 加 trimacl（同一镜像） | options 含 trimacl；`WRITE_ACCEPTED`；`prop=ro=true`；文件真实出现 | 一致 |
| E3a /vol1 RW 路径 | `WRITE_ACCEPTED` | 一致（snapid 686→698，预期） |
| E3b 私有 namespace RO 挂载 | `RO_MOUNT_OPTIONS=ro,…,trimacl,…`；`WRITE_REJECTED 30` | 一致 |
| E4 reflink | `reflink cp rc=0`；filefrag 两文件同一物理 extent `694057586..694059633` + `shared`；`copy unchanged after original modified: yes`；`original stable after copy modified: yes`（**该行对两方向含义不同，见 F2**） | 一致（含同一物理 extent；8 MiB 随机内容哈希不同属预期） |
| E5 send/receive | `send rc=0 bytes=8395408`；`receive rc=0`；设 ro 后两次 `WRITE_REJECTED 30`；`received=… snapshot=… equal=yes`（内容哈希因随机内容不同，结论一致） | 一致 |
| 清理 | 两次运行结束均：探针树 absent、无 ro 子卷、无 fnm loop/挂载、无镜像/流残留 | 一致 |

归一化日志比对（去除路径/snapid/哈希/时间后）：**rootcause 50 行仅差 `subvolid=686→698` 一处；mitigation 49 行完全一致**。

### 3.3 运行 2：generation 语义 + barrier（10:14:48–10:16:04）

- semantics 探针 rc=0，耗时约 73 s（含两次 35 s 等待）；barrier 探针 rc=0，约 3 s；两者结束后探针树 absent、无 gen/barrier 子卷、无 loop/挂载、无 `/tmp/fnm-noatime-mnt` 残留。
- **semantics 复跑（第三次运行）与归档 JSON（第二次运行）逐 step 布尔结果 0 差异**：16 步名称/顺序相同；`changed_immediately` 全 false；有 `after_sync` 的步骤全 true；**#14 `after_wait` 仍为 true（1692681→1692682）**；#15/#16 RO 绕过写入均推进；`snapshot_base.flags=1`；cleanup rc=[0,0]；`probe_root_removed=true`。
- **barrier 复跑与归档 JSON 逐测试结果 0 差异**：A changed=true（1692690→1692691）；B false；D `changed_by_write=false` / `changed_after_syncfs=true`；E true（writer `writer-done`）；C false（`mount_cmd_rc=0`、`target_atime_before=1`）；cleanup rc=[0,0]；`probe_root_removed=true`。
- 复跑输出副本（`/tmp/fnm-r3-review/rerun/`，本机 `E:\fnmusic\review-tmp\r3-rerun\`）：`rootcause.log` `122fd7d5…`、`mitigation.log` `3e03750c…`、`semantics.json` `7e8dec44…`、`barrier.json` `c677904f…`（生成值更高、时间戳不同，哈希与原文不同属预期）。

### 3.4 "文档数值 vs 归档 vs 复跑"汇总

| 对象 | 文档声称值域 | 归档原始输出（第二次运行） | 本复审复跑（第三次运行） | 结论 |
| --- | --- | --- | --- | --- |
| rootcause/mitigation 日志 | 日志哈希绑定（同一次运行） | 与 NAS 原件同哈希 | 归一化后仅 snapid 不同 | 完全一致 |
| generation §2 | 1692585–1692604（第一次运行） | 1692619–1692639（+34） | 1692667–1692686 | 结构/结论一致；**#14 不一致（F1）** |
| barrier §8 | 1692611–1692616（第一次运行） | 1692642–1692647（+31） | 1692690–1692695 | A–E 结论一致 |
| treeid | 689/690 | 693/694 | 701/702 | 单调递增（其它会话/运行创建子卷），不构成矛盾 |

### 3.5 复跑后状态与清理核验（NAS 本地 10:16:54）

- 4 个探针树**均不存在**；`btrfs subvolume list /vol1` 共 **49** 项（复跑前=复跑后），无探针子卷；无 fnm loop 设备、无 fnm 挂载、无 `/tmp/fnm-noatime-mnt`、无探针进程、无镜像/流残留。
- `/vol1/1000` 仍 **47 项**、集合 SHA `c78985ba…`（与复跑前一致）；生产路径 `stat`/指纹（`music-test` `1bef11ce…`、`staging` `98fdfec9…`、0 条目、`music` mtime 2026-10-18、`.env` mtime 2026-11-01 17:01:47）与复跑前**完全一致**。
- 服务状态逐字段不变（同一 `InvocationID`/`MainPID`/启动时间，`NRestarts=0`）→ 未停/启/重启。
- **原始证据恢复**：复跑覆盖的 `/tmp/fnm-ro-rootcause.log`、`/tmp/fnm-ro-mitigation.log` 已从备份恢复为原始字节并复核哈希（`8cbbab23…`/`1a4b893c…`）；Lead 的输出 JSON 未被本复审覆盖（哈希不变）；`docs/evidence/20261107/` 未被改动。
- 复审者自身产物：NAS `/tmp/fnm-r3-review/`（156 KB：原件备份、复跑副本、脚本、日志）与本机 `E:\fnmusic\review-tmp\`；均在受保护路径之外，未写入仓库与生产路径（见 §8 披露）。
- 环境注记：复跑期间设备上另有并行会话的 `/tmp` 文件（`fnm-identity-readonly*.sh` 10:11–10:12、`fnm-ingress-evidence.sh` 10:15），非本复跑产物，也未观察到对本探针树/结果的干扰（探针树为唯一命名路径，运行前后均核验）。

## 4. 实验设计充分性审计（逐结论）

| 结论 | 设计 | 支撑强度 | 边界/缺口 |
| --- | --- | --- | --- |
| `trimacl` 是"RO 子卷仍可写"触发点（本机） | E1 vs E2：**同一镜像、同一内核、同一次运行**，仅挂载选项差 `trimacl` | **强**（对照清晰；本复审独立复跑复现） | 单次观测各一；未定位补丁代码；只证明"挂载选项层"触发，不能外推 |
| RO **挂载**恢复写强制（M1） | E3b 私有 namespace `-o ro,subvolid=` 写 → EROFS；同一快照 E3a RW 路径仍可写 | **强**（机制/现象自洽；复跑复现） | 仅 root 实测；只保护视图，不能阻止 RW 路径；需 root 帮助进程 |
| M2 send/receive 独立视图 + RO 硬强制 + 内容一致 | E5：send 到独立 loop btrfs（无 trimacl）→ receive → 设 ro / `-o ro` 重挂载两次 EROFS；内容 parity | **强**（本机） | 需先做 RO 快照（源 RO 位不可依赖）；scratch 在 `/tmp`、全量拷贝、失败恢复/大文件/持久卷未测；root helper |
| M3 reflink COW 独立 | E4：`shared` extent + 改源→副本不变；改副本→源不变 | **中**：filefrag 与第一方向成立（复跑复现）；**第二方向检查无效（F2）** | 同 fs 共享坏块、权限模型、8 MiB、非整树原子（文档已列）；不足以支撑"双向独立"的完整表述 |
| generation 能反映修改（数据/元数据/ABA） | §2 16 步矩阵；`GET_SUBVOL_INFO` offset 280 | **强**（归档第二次运行与复跑第三次运行均复现；ABA 历史不可抹除） | 需提交障碍；不能定位哪个文件 |
| `fsync(file)` 不足以推进 root generation；需 `sync`/`syncfs` | §2 #2/#3 + §8 D（syncfs） | **强**（三步一致：fsync 后不变、sync/syncfs 后 +1） | 未定位内核路径；单次观测为主 |
| 读取可推进 generation（atime 假阳性） | §2 #10 + §8 A（普通读 +1） | **强** | 属 relatime 行为；扫描读取路径必须处理 |
| `O_NOATIME` / `noatime` 挂载消除假阳性 | §8 B/C 对照（A 复现假阳性、B/C 不变；C 预置 atime=1） | **强**（三向对照） | 非属主 `O_NOATIME`（CAP_FOWNER）未测；长窗口/多次读取未测 |
| 并发 writer 可被 bracket 检出 | §8 E：writer 子进程 + sync bracket → +1 | **中–强**（单场景 append 可检出；复跑复现） | 仅一个 writer/时序；并发 rename/truncate、多提交窗口、检出率/竞态窗口未测（文档已列为未验证） |
| RO 绕过写入也推进 generation | §2 #15/#16（trimacl 绕过写快照） | **强**（复跑复现） | 提交时机不稳定（#16 已如实记录，但见 F1） |
| 设计 addendum 的修订（M1–M4/T-C/P7 A4/风险/门禁） | 引证上述证据 | **合理**：哈希/前缀/戳绑定一致；修订与证据一致；明确"未接入/未实现/门禁不变" | 继承 F3（"单次原子实验"措辞）；R21（新证据未独立复审）已被本复审+复跑部分消解，需后续更新引用 |

## 5. 编号发现

**阻断（blocking）：无。**

### F1（重要）generation 文档的数值未绑定到持久原始输出；#14 跨运行不一致未披露；`MANIFEST` 的"结论相同"不准确

- 位置：`docs/BTRFS_GENERATION_SEMANTICS_20261107.md` §1/§2/§8、`docs/evidence/20261107/MANIFEST.md` 第 19 行。
- 事实：文档 §2/§8 的数值（1692585–1692604、1692611–1692616、treeid 689/690）来自**第一次运行**，而 `docs/evidence/20261107/*_OUTPUT.json` 是**第二次运行**（`time 10:08:54`/`10:10:08`，数值分别为 +34/+31，treeid 693/694）——文档 §1 把后者写成"原始输出（持久副本）"，读者会以为表格数字可直接对 JSON 复核；实际不能逐值对上。更关键：§2 #14 文档称"35 秒后仍不变（1692599）"，第二次运行归档 JSON 为 `after_wait=1692634, changed=true`，本复审第三次复跑也为 `changed=true`——`MANIFEST` 称"第一次运行结论相同"对 #14 **不成立**。该差异不推翻 §3.2 的"等待 35 秒**不能保证**提交"（恰恰支持"提交时机不稳定"），但必须披露并重新绑定。
- 影响：证据链可复现性/诚实性；不影响 generation bracket 的最终结论（另有归档与复跑支撑）。
- 建议：①把 §2/§8 表格更新为归档第二次运行（或第三次复跑）的实际数值，并在表中标注运行编号；②或将**文档所引用那一次运行**的 stdout 也归档到 `docs/evidence/`；③明确写出 #14 的跨运行差异（不变 / +1 / +1），把结论改为"等待 35 秒不保证提交（多次运行不一致）"；④更正 `MANIFEST` 的"结论相同"为"多数结论相同；#14 相反（提交时机不稳定）"。

### F2（重要）E4 的"改副本→源哈希不变"检查是同义反复，不能作证

- 位置：`scripts/btrfs_ro_mitigation_probe.sh` 第 68–75 行；根因文档 §3 M3、`docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` §7.3.3。
- 事实：`H_SRC_AFTER=$(sha256sum src/big.bin)`；`H_SRC_EXPECT=$(python3 -c "…open('src/big.bin','rb').read()…")`——两者读取**同一文件同一时刻**，恒等（除非两次读取间文件被改）。因此日志 `original stable after copy modified: yes` 不证明"改副本不影响源"；真正有效的第一方向（改源→副本不变）是 `H_COPY_BEFORE` 与 `H_COPY_AFTER` 的前后比较。M3 的"双向 COW 独立"目前只有 filefrag 共享 extent + 第一方向有效证据。
- 影响：M3 结论的"双向"表述过强；不推翻"reflink 可用/共享 extent"与"改源→副本不变"；M3 本身被文档定位为"抽检/不作 A4 最终证据"，故不构成阻断。
- 建议：在改副本前记录源哈希（或记录 filefrag/`cmp`/前后哈希数值）再做改副本后对比；或把 M3 结论限定为"filefrag 证明共享 extent、改源→副本不变；改副本→源不变待补测"。并在复跑日志中保存哈希数值而非只打印 yes/no。

### F3（次要）根因文档"单次原子运行"与两段日志的 59 秒间隔不符

- 位置：根因文档抬头（窗口 10:02:59–10:04:01，单次原子运行 + `trap` 自清理）、§6 复现命令；`docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` §7.1 继承"单次原子运行"表述。
- 事实：`fnm-ro-rootcause.log` BEGIN 10:02:59 / END 10:03:00；`fnm-ro-mitigation.log` BEGIN **10:03:59** / END 10:04:01——两段日志相隔约 59 s，是两次独立脚本调用（各自 trap 自清理）；不是一次连续原子运行。§6 的 `bash A; bash B` 命令本身可顺序复现（本复审按同样方式在 10:14:39–10:14:41 连续完成），但"单次原子运行"的表述与原始日志不符。
- 影响：表述精度/证据叙述；不影响实验内容。
- 建议：改为"两次相邻脚本调用（各自 trap 自清理）"，并相应更正设计 addendum §7.1 的引述。

### F4（次要）generation 文档 §5/§7 在追加 §8 后未更新（已过时）

- 位置：generation 文档 §5 第 2–5 条（"未测并发写入""未测 noatime/O_NOATIME"）、§7 下一步 1–3。
- 事实：§8 补测 A/B/C/E 已经测试：atime 假阳性（A）、`O_NOATIME`（B）、`noatime` 挂载（C）、并发 writer（E）、`syncfs`（D）。§5/§7 仍写"未验证/下一个最小补测项"，与 §8 冲突（§8 末尾也列出了真正仍未验证的项：非属主 `O_NOATIME`、多提交窗口、并发 rename/truncate、只读挂载上的 `syncfs`）。
- 建议：更新 §5/§7 为"§8 已覆盖 A–E；仍未验证 = …（§8 列表）"。

### F5（次要）脚本退出码不反映失败/清理结果，"自清理"非 fail-closed

- 位置：5 个脚本；两份文档 §6/§7 的"自清理/运行后核验"。
- 事实：两个 shell 探针无 `set -e`，各步骤失败仅 `say "... FAILED"`，脚本最终仍可 `exit 0`；两个 Python 探针 `main()` 始终 `return 0`（即使 `btrfs subvolume delete` 失败或 `probe_root_removed=false`，只写入报告字段）。清理失败不会让调用方通过退出码发现，需人工读日志/事后核验。本次复跑与文档运行的清理均实际成功（本复审独立核验），但脚本层面不是 fail-closed。
- 建议：脚本末尾显式核验"探针树不存在 + 无相关子卷 + 无 loop/挂载"，失败返回非零；Python `main` 依据 `probe_root_removed`/cleanup rc 返回非零；shell 增加 `set -u` + 关键步骤失败标志聚合。

### F6（次要）generation §2 表列语义易误读（`sync=False` 步骤的内部 settle 值）

- 位置：generation 文档 §2 表第 3 列"强制 `os.sync()` 后"；#1/#3/#14 等行。
- 事实：脚本对 `sync=False` 的步骤仍会在末尾执行一次 `os.sync()`（"settle the transaction"），但不记录 `after_sync`；文档把由这次 settle 造成的 +1 写成"下一步起点 1692586"/"稳定后 1692588"。读者可能把 #3 的 +1 误读为 `fsync(file)` 的效果（实际来自内部 `sync()`）。#3 的结论文字"fsync(file) 单独不足以推进"本身正确，但与表格呈现不一致。
- 建议：在表格/脚注明确"sync=False 步骤的'下一步起点/稳定后'= 脚本内部 settle `sync()` 之后的值；不是 fsync 的效果"。

### F7（次要）记录细节：M2/E5 的写测试也是 root；barrier 无复现命令；NAS 文件名与 §6 命令不同

- 事实：①根因文档对 E3b 明说"以 root 执行"，但 M2/E5 的 `set ro`/写入测试同样是由 root 运行的脚本执行（E5 日志为 root 流程），文档未统一说明；②generation 文档 §6 只给出 semantics 探针的复现命令，barrier 探针（§8）无命令；③§6 的 `scp … kotomi-root:/tmp/` 文件名（`btrfs_ro_*.sh`）与 NAS 实际副本名（`/tmp/fnm-ro-*.sh`）不同（命令本身仍可复现，本复审已用原文件名成功复跑）。
- 建议：统一边界说明与复现命令；记录 NAS 实际文件名。

## 6. Verdict：PASS-with-limits

理由：
- 哈希绑定：两份文档的脚本/日志/JSON 声明、`MANIFEST` 的 4 个哈希、§7 addendum 的旧前缀（89,481 B = `ae7bc37f…`）与自校验戳（`cc410801…`）**全部独立重算一致**；NAS 原件与持久副本一致。
- 全部**关键实验结论**在本复审的独立复跑中复现：E1/E2 对照、E3a/E3b、M2 send/receive RO 强制 + 内容 parity、M3 filefrag/shared extent、generation 16 步矩阵（含 ABA、atime、RO 绕过写）、barrier A–E；复跑后清理与设备状态核验通过。
- 文档边界总体诚实（仅本机、未定位补丁、/tmp 易失、M4 未实测、非属主 O_NOATIME 未测、门禁不变）。
- 但存在 **F1（重要：generation 数值未绑定持久输出且 #14 跨运行差异/MANIFEST 不准确）**、**F2（重要：E4 第二方向检查同义反复）** 与 5 个次要问题，故为有条件通过，不能给无条件 PASS。

## 7. 明确"解除了什么、没解除什么"

**本复审范围内解除（声称已被独立证据/复跑支撑）**：
1. 根因对照实验（E1 无 trimacl → EROFS；E2 同一镜像加 trimacl → 写入被接受；E3a RW 路径可写；E3b 私有 namespace `-o ro` 挂载 → EROFS）在本机可**独立复现**。
2. M1（RO 挂载恢复视图层强制，但 RW 路径仍开放）与 M2（send/receive 到独立无 trimacl btrfs 后 RO 强制 + 内容 parity）在本机复现成立。
3. M3 的 reflink 存在性（`shared` extent、`cp --reflink=always` 成功）与"改源→副本哈希不变"复现；"改副本→源不变"证据无效（F2）。
4. generation：提交后数据/元数据/ABA/读取 atime/RO 绕过写入均推进；`fsync(file)` 单独不足；`sync`/`syncfs` 是有效提交障碍；atime 假阳性真实（A）且 `O_NOATIME`/`noatime` 可消除（B/C）；并发 writer 可被 bracket 检出（E）；"等待 35 秒"不保证提交（跨运行不一致，F1）。
5. 探针脚本的写入范围（`/tmp` + 一次性 `/vol1` 探针树、无生产路径/服务/配置）与自清理机制：本复审独立复跑后确认无残留（探针树/子卷/loop/挂载/进程），`/vol1/1000`、生产路径、服务状态未变；NAS 原始日志已恢复原始字节。
6. 设计 addendum §7 的哈希绑定、追加完整性（§0–§6 前缀未变）与对证据的引用一致；其"未接入/未实现/门禁不变"的定位成立。

**没有解除（不得据此暗示）**：
1. **§0.1/§0.2、P7 A4、扫描器验收门禁全部不变且仍 BLOCKED**；`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须保持 `False`，扫描器继续返回 `unknown`，`PIPELINE_INTEGRATION_ALLOWED=False` 未变。
2. `trimacl`/`trimafs2` 具体补丁位置与全部行为未定位；**不可外推**到其它 fnOS 版本、内核、挂载组合或上游 Btrfs。
3. M4 未实测；M2 未做库级/大文件/持久 scratch 卷/失败恢复/坏块测试；M3 未做库级/坏块/权限模型测试，且"双向 COW"证据不完整（F2）。
4. RO 强制对**普通非 root 用户**未单独实测（root 已生效）；非属主 `O_NOATIME`（CAP_FOWNER）未测；长扫描窗口/多提交窗口检出边界、并发 rename/truncate、只读挂载上的 `syncfs` 行为未测。
5. M2 仍依赖"源 RO 快照在 send 期间稳定"（源 RO 位不可依赖 → 仍需 bracket/短窗口/重试）；generation bracket 只证明"发生过修改"，不能定位文件，仍需逐文件重读与账本指纹。
6. generation 文档数值的持久绑定与 #14 变异披露（F1）、E4 第二方向（F2）仍是待修正项；`MANIFEST` 的"结论相同"需更正。
7. 本次复审**未**做生产链路/账本/停写/备份/回滚/身份授权验证，未打开任何数据库，未对设计做实现层评审。

## 8. 复审者操作披露与无法核验项

**已执行**：
- 只读：哈希重算、文档/脚本/日志/JSON 阅读、SCAN 文档字节级前缀/戳校验、设备只读快照（`stat`/`ls`/`btrfs subvolume list`/`losetup -a`/`findmnt`/`systemctl show`/`ps`/`df`）、NAS 原件哈希核对。
- 写操作：仅 ①`/vol1` 上**受审脚本自身**创建并删除的 4 个一次性探针树（任务明确允许的独立复跑）；②NAS `/tmp/fnm-r3-review/`（156 KB：原件备份、复跑副本、脚本、日志）；③本机 `E:\fnmusic\review-tmp\` 临时分析文件（不在仓库内）。**未**修改任何代码/被审文档/设计文档，未停/启/重启服务，未改配置，未写生产曲线/暂存/账本，未打开 DB。
- 复跑覆盖的 NAS `/tmp/fnm-ro-rootcause.log`、`/tmp/fnm-ro-mitigation.log` 已恢复为原始字节（哈希与文档一致）；`docs/evidence/` 未动；复跑产物另存于 `/tmp/fnm-r3-review/rerun/`。

**无法核验/仍为 unknown**：
1. Lead **第一次运行**（文档 §2/§8 数值所对应）的 stdout 未持久化（文档 §1 自述"在 Lead 会话记录中"）；只能通过归档第二次运行与本次第三次复跑间接验证结论，无法逐值复核文档表格（F1）。
2. 文档窗口（10:02:59–10:04:01、10:06:01–10:07:2x、10:08）对应的"第一次运行"无法直接核验；归档 JSON 时间戳证明其为第二次运行。
3. Lead 运行后的清理"三项均确认为空"无法回溯核验；当前设备状态与本次复跑后的独立核验均为空。
4. `trimacl`/`trimafs2` 补丁代码位置、模块全行为。
5. 复跑期间存在其它并行会话的 `/tmp` 活动（identity/ingress 探针）；未观察到干扰，但无法排除其与设备的只读交互对时序的细微影响（探针树为唯一路径，前后均核验无残留）。
6. M4、普通用户 RO 强制、非属主 `O_NOATIME`、库级/坏块/失败恢复等（见 §7）。
7. 本复审未对 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` §0–§6 的设计内容本身做技术评审（仅核验 §7 追加的绑定/内容一致性）。

## 9. 附录

### 9.1 运行时间线（NAS 本地 CST）

| 时间 | 事件 | 来源 |
| --- | --- | --- |
| 10:02:59–10:03:00 | 根因脚本运行 1（E1–E3d） | `/tmp/fnm-ro-rootcause.log`（已恢复） |
| 10:03:59–10:04:01 | 缓解脚本运行 1（E4–E5） | `/tmp/fnm-ro-mitigation.log`（已恢复） |
| 10:06:01–10:07:2x | generation 语义**第一次运行**（文档 §2 数值） | 无持久 stdout（文档窗口） |
| ~10:08 | barrier **第一次运行**（文档 §8 数值） | 无持久 stdout（文档窗口） |
| 10:08:54–10:10:0x | generation 语义**第二次运行**（归档 JSON） | `docs/evidence/20261107/BTRFS_GENERATION_SEMANTICS_OUTPUT.json` |
| 10:10:08–10:10:12 | barrier **第二次运行**（归档 JSON） | `docs/evidence/20261107/BTRFS_GENERATION_BARRIER_OUTPUT.json` |
| 10:14:39–10:14:41 | 本复审复跑：根因 + 缓解 | `/tmp/fnm-r3-review/rerun/` |
| 10:14:48–10:16:04 | 本复审复跑：generation 语义（73 s）+ barrier | `/tmp/fnm-r3-review/rerun/` |

### 9.2 复跑输出哈希（本复审，与归档不同属预期：generation 值/随机内容不同）

| 文件 | SHA-256 |
| --- | --- |
| `rerun/rootcause.log` | `122fd7d5d7e9d748d641d474ad4e0d0a714c86e2dc88fba1d9486befffe061e7` |
| `rerun/mitigation.log` | `3e03750ccbb1c42d710846456233d29afa71d97573d71e69aa50497c778b9f60` |
| `rerun/semantics.json` | `7e8dec44a1e3b4cd8c2d133a2b97105841103c8662d346f4e00c555c563a111c` |
| `rerun/barrier.json` | `c677904f29b22b53bef6801af2212c8f1640e324aeafb6cd0327bd2df17c2186` |

### 9.3 本报告自身哈希说明

- 本报告写入完成后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REPORT-HASH-STAMP` 行给出 `sha256(该行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 43772733cf8779c7d2a1cebca296380d60b6c5aa7d241c67fd1e39f7a0035c9e -->

## Round-2 追加复核：F1/F2 修正的限定复核（2026-11-07）

> **本轮范围（限定）**：仅本地只读复核——重算修订版文档/新证据日志哈希、读日志与修订文本、对照上轮结论；**未访问设备、未重跑实验、未改被审文档/脚本**。本轮唯一写入是本报告追加的本章与文末新自校验戳。
> **本轮结论：PASS（限定复核，无阻断）。** 请求修正的 F1（generation 两次运行绑定）、F2（reflink 双向 COW 纠正实验）、F3（RO 文档措辞）在指定证据文档中已**实质、如实、可复核**地完成；纠正实验的方法与数值正确。剩余 N1–N7 为文档同步/措辞/可复现性收口项（N1/N2 重要），不影响本轮证据结论，也不改变任何门禁。

### R2.1 本轮对象与哈希

| 对象 | 本轮 SHA-256 | 大小 | 与上轮 / 声称对照 |
| --- | --- | --- | --- |
| `docs/BTRFS_GENERATION_SEMANTICS_20261107.md` | `1872b9fce7b80bf127d45d810d6a203edecc0fa4e3c084a6f2cab664a4eb2cb8` | 12,780 B | 上轮 `ee374a49…ad0f`（9,063 B）；新增 §2 绑定说明、#14 更正、§9 |
| `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` | `16aee49aa8b5e0afb0619ae300d639efa6a05124b7501ee7e7972691cbfa1efa` | 7,932 B | 上轮 `91f65503…81f2`（7,851 B）；仅抬头措辞变更（+81 B），§1 脚本/日志哈希与 §2–§5 结论逐字未变 |
| `docs/evidence/20261107/MANIFEST.md` | `2be4c293130fe1da900c1d0398199351da986be8243168231d40f76da113888a` | 2,697 B | 上轮 `e439e7c9…0163`（2,438 B） |
| `docs/evidence/20261107/REFLINK_COW_CORRECTED.log` | `973b05eb4a1ea4a00aa8a2f02043b35ee4bdc1173a98ab775e30fd4c99ed8c55` | 1,529 B | **= Lead 声称哈希 ✓**；上轮不存在 |
| `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` | `7f4304e874425424648c9e9b0c634f4a9d1f21bf29eb60e93240327d98e18ed2` | 111,567 B | **未变**（见 N1：设计 addendum 未同步） |
| 4 个原始证据文件 | 与上轮一致（`6add2ee5…`/`1038a039…`/`8cbbab23…`/`1a4b893c…`） | — | 未被改动 ✓ |
| 5 个探针脚本 | 与上轮一致（`d5074d63…`/`c7e513b3…`/`a6ce5b77…`/`7f27a07a…`/`4795bba5…`） | — | 未被改动 ✓ |
| 本报告前缀 | 前 38,960 B = `41b608416d84f6e17dafa28e536e6dad2d6982b17731584650bbe2471cdf8070` | 38,960 B | = 上轮最终整文件哈希，证明原 §1–§9 字节未改 |

### R2.2 F1 核验（resolved）

| 修订项（Lead 声称） | 复核证据 | 结论 |
| --- | --- | --- |
| §2 增加"绑定说明" | 文档第 20 行：表为首次运行（treeid 689/690），持久 JSON 为第二次运行（treeid 693/694，数值整体 +34/+31），"两次运行的布尔结论一致，唯 #14 两次结果不同" | 与上轮反推的映射**一致**（语义树 id 693/694、数值偏移 +34；barrier 偏移 +31）→ resolved（措辞小瑕见 N6） |
| #14 行更正 | 第 37 行："首次运行：35 s 后仍不变（1692599）；归档/复跑：35 s 后 +1"；结论"提交时机不稳定；时间不可作为提交保证" | 与归档 JSON（1692633→1692634）及本复审第三次复跑（1692681→1692682）一致 ✓ |
| §3-2 更正 | 第 46 行："等待 35 秒在两次运行中结果相反（一次不变、一次 +1）→ 时间不可作为提交保证" | 如实、正确 ✓ |
| 新增 §9.1 披露 | 第 111–115 行：两次运行数值/实例不同、#14 结果相反、更正不改变 §8 方向、实现不得依赖时间等待 | 与证据一致，且明确未外推 ✓ |
| `MANIFEST` 不再写"结论相同" | 第 19 行改为"第二次运行 stdout（文档 §2 表为首次运行数值；两次布尔结论一致，唯 #14 提交时机相反，见文档 §9.1 更正）" | 不再有上轮的"结论相同"过度声称 ✓ |

→ **F1 resolved**。残余 N6（非阻断）：§2/§9.1 的"+34/+31"把 §2（+34）与 §8 barrier（+31）混写；"布尔结论一致，唯 #14 不同"自相矛盾，建议改为"除 #14 外一致"。

### R2.3 F2 核验（resolved）

新增纠正实验 `REFLINK_COW_CORRECTED.log`（哈希已对上 `973b05eb…`，1,529 B；`date 2026-11-07T10:24:57+08:00`）：

| 日志步骤 | 观测值 | 语义 |
| --- | --- | --- |
| 复制后 | `H0 source = C0 copy = 938ce08bbaf227f42f75c317d2d41063b028d0cf23e271834aca3ace61d900aa` | 复制内容一致；随后 filefrag 两文件均 `0..2047: 694057586..694059633: 2048: last,shared,eof`（同一物理 extent + shared） |
| 改源后 | `H1 = 147763299b5bda116aecd457dd81ffd7f7910ee5a3eaa766963bc5218fab88ba`（≠H0）；`C1 = C0`（dir1 两条断言均 yes） | 方向一有效：副本未随源变（C1 与**修改前**的 C0 比较） |
| 再改副本后 | `H2 = H1`（dir2 yes）；`C2 = e63df1de8db0f3a339e581634bb81749e921db3aa568b176023d0e67762c122e`（≠C1） | 方向二有效：源未随副本变（H2 与**副本修改前**取的 H1 比较）——原 E4 的同义反复已消除 |

文档 §9.2 表内数值与日志**逐项一致**（H0/C0、H1、C1=C0、H2=H1、C2），结论"reflink 副本与源在双向修改下都保持内容独立（COW）"有本日志支撑；边界（同 fs 共享 extent/坏块、普通文件权限、仅 8 MiB）保持。→ **F2 resolved**。

残余（非阻断）：N5——该日志只含观测，**无落地命令/脚本哈希**（可读可复核，但重放需重建步骤）；其探针树 `/vol1/1000/fnm-reflink-cow-test-20261107` 的清理未在日志/MANIFEST 中记录（本轮不访问设备，记 unknown）；MANIFEST 新行大小写成"2,0xx B"（实际 1,529 B）、窗口 10:02–10:09 未覆盖 10:24:57。
残余（重要，非阻断）：N2——`docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` §3 M3（第 38 行）仍以原 E4 作"改副本→源哈希不变"的依据、未引用纠正实验；§5 第 62 行"一次 COW 双向验证"亦过时。纠正事实已归档，但该文档本身读起来仍引用无效子检查。

### R2.4 F3 核验（RO 文档 resolved；设计 addendum 残留 N1）

- RO 文档抬头已改为："由**两个探针脚本各自原子运行**（10:02:59–10:03:00 与 10:03:59–10:04:01），各自 `trap` 自清理"——与两份原始日志的 BEGIN/END **逐字一致** ✓。
- 但 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` **哈希未变**，其 §7 addendum 仍有 4 处"单次原子"（第 610、612、619、741 行）；且 §7.1（第 618 行）仍绑定修订前 RO 文档哈希 `91f65503…`/7,851 B；§7.3.3（第 667 行）仍以旧 E4 支撑"改副本→源不变"。→ **N1（重要，非阻断）**：设计 addendum 需随证据文档同步（更新旧哈希/措辞/M3 引用，并重算其 §7.8 前缀与自校验戳），否则外部核查设计文档会看到过期绑定。

### R2.5 F5/F6/F7 处理核验（N4 编号错位）

上轮本报告 F 编号为：F4=§5/§7 陈旧、F5=脚本退出码、F6=§2 表列语义、F7=记录细节；修订文档 §9.3 使用 F4=§2 表列语义、F5=§5/§7 陈旧、F6=脚本退出码、F7=记录细节——**F4/F5/F6 三者互换**（N4，次要），逐条闭合核对时会对不上号。

| 上轮编号 | 修正文档 §9.3 回应 | 本轮判定 |
| --- | --- | --- |
| F5（脚本退出码非 fail-closed） | 文档 F6：保留为边界；证据夹具不以退出码单独判定，实现侧 fail-closed 另行处理 | **可接受（resolved-as-documented）**：限制被如实保留、未掩盖，符合"证据夹具 vs 实现侧"分工 |
| F6（§2 表列语义：sync=False 步骤内部 settle 的 +1） | 文档 F4 仅说明 #14 两次观测与列名 | **部分（N3，次要）**：仍未解释"下一步起点/稳定后"来自脚本内部 `os.sync()`（#3 的 +1 非 fsync 效果），建议加脚注 |
| F7（M2/E5 为 root、barrier 无复现命令、NAS 文件名差异） | 文档 F7：root 已如实记录；复现命令见 §6 与 RO 文档 §6 | **部分（N7，次要）**：E3b root 已明示，但 M2/E5 仍未逐项注明；barrier 探针仍无复现命令（§6 只有 semantics；RO 文档 §6 覆盖 rootcause/mitigation）；NAS 文件名差异未提 |
| 上轮 F4（§5/§7 未随 §8 更新） | 文档 F5："§7 已标注完成；§5 的边界仍成立" | **部分（N3，次要）**：§7 已加"已完成：见 §8" ✓；但 §5 第 64 行"未测并发写入"、第 67 行"未测试 noatime/O_NOATIME…未验证"仍与 §8 A/B/C/E 矛盾；"§5 的边界仍成立"对该两项不准确。建议更新 §5 两条并保留 §8 末尾的真正未知项（非属主 O_NOATIME、多提交窗口、并发 rename/truncate、只读挂载上的 syncfs） |

### R2.6 剩余项汇总（均非阻断）

| # | 级别 | 位置 | 事实 / 建议 |
| --- | --- | --- | --- |
| N1 | **重要** | `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` §7.1/§7.3.3/§7.8 | 仍绑定旧 RO 文档哈希（`91f65503…`/7,851 B）、4 处"单次原子"、M3 仍引旧 E4；建议同步并重算 §7.8 前缀/戳 |
| N2 | **重要** | `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` §3 M3/§5 | 仍以原 E4 支撑"改副本→源不变"，未引用 `REFLINK_COW_CORRECTED.log`/generation 文档 §9.2；建议加指针并把"一次 COW 双向验证"更新为"原 E4 + F2 纠正实验" |
| N3 | 次要 | generation 文档 §5 第 64/67 行、§9.3 F5/F4 | §5 两处陈旧与 §8 矛盾；§2 表列（sync=False 内部 settle）仍未说明；建议更正 |
| N4 | 次要 | generation 文档 §9.3 | F 编号与上轮复审报告 F4/F5/F6 错位；建议按复审报告编号或改称"复审项 A–G" |
| N5 | 次要 | `MANIFEST.md` 第 21 行/窗口/边界；`REFLINK_COW_CORRECTED.log` | REFLINK 大小"2,0xx B"应为 1,529 B；窗口未覆盖 10:24:57；纠正实验探针树清理未记录；日志无落地命令/脚本哈希（可复核性受限） |
| N6 | 次要 | generation 文档 §2 绑定说明/§9.1 | "+34/+31"混用 §2（+34）与 §8（+31）；"一致，唯 #14 不同"措辞矛盾，建议"除 #14 外一致" |
| N7 | 次要 | generation 文档 §6/§9.3 F7；RO 文档 §5 | barrier 探针复现命令缺失；M2/E5 root 未逐项标注；NAS 实际文件名（`fnm-ro-*.sh`）与 §6 命令文件名不同 |

### R2.7 本轮判定

- **F1 resolved；F2 resolved；F3 resolved（RO 文档；设计 addendum 残留 N1）；F5 resolved-as-documented；F6/F7 部分（N3/N7）；上轮 F4 部分（N3）。**
- 请求修正的三项（F1/F2/F3）在**指定证据文档**中已如实、正确、可复核；纠正实验方法与数值正确，解决了上轮"同义反复"问题。
- 剩余 N1/N2 为**重要但非阻断**的文档同步项（旧哈希/旧证据引用），N3–N7 为次要收口项；均不改变本轮证据结论（根因 `trimacl`、M1/M2/M3、generation/barrier 结论、noatime+syncfs barrier）与任何门禁。
- **本轮判定：PASS（限定复核，无阻断项）**；建议在最终定稿前处理 N1/N2（尤其是设计文档的旧哈希绑定），否则外部核查设计文档时会产生不一致。

### R2.8 本报告哈希说明

- 上轮最终整文件（38,960 B）SHA-256 = `41b608416d84f6e17dafa28e536e6dad2d6982b17731584650bbe2471cdf8070`；本轮在其后追加本章，原 §1–§9 字节未改（复核：取本文件前 38,960 字节重算，应等于该值）。
- 本轮最终整文件 SHA-256 见本轮复审消息；文末 `REPORT-HASH-STAMP-R2` 行给出 `sha256(该行之前全部字节, UTF-8)`（删除该行后重算即得）。

<!-- REPORT-HASH-STAMP-R2: sha256(all bytes before this line, UTF-8) = e9cbac06337b6a8b782df4a4f4248658495cc42ca335ee3814c88490bcee1d16 -->
