# /vol1 Btrfs 探针证据独立复审（2026-11-07）

> **结论：PASS-with-limits（有条件通过）。**
> 被审文档的全部技术性数字与结论，均被 NAS 原始 JSON / `run-meta.txt` 支撑，并由本复审者在同一设备上**独立原子复跑一次**完整复现；无阻断级问题。但存在 **1 个重要表述问题（§9 并行会话/来源独立性不可核实且与同仓另一文档的归属相冲突）** 与 6 个次要文档/可复现性问题（编号 F1–F7），因此不能给无条件 PASS。
> **本复审不改变、也不构成 §0.1/§0.2 部署门禁或任何上线授权的依据。** 被审文档是现场证据，不是部署授权；本次复审也不是。
> 本复审者（final-reviewer）未参与被审文档与任何被审证据的采集或撰写；除受审文档规定的隔离探针目录外，未对设备做写操作（见 §9 披露）。

## 0. 抬头与快照绑定

| 项 | 值 |
| --- | --- |
| 复审日期 | 2026-11-07（复审窗口 JST 02:37–02:5x / NAS 本地 CST 01:37–01:5x） |
| 复审者 | `final-reviewer`（独立复审角色） |
| 被审文档 | `docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`，13,458 B，SHA-256 `b11b851c5b04d25d6d7b760a6c7dbdd5d07f7319c2910f5b5edc6203fbb31507`（复审开始/结束两次重算一致） |
| 扩展仓库快照 | 分支 `codex/fnmusic-prelaunch-20261025`，HEAD `cf843a40d735fb9a20e1bc850f711cd4693cb3aa`（2026-11-02 00:30:28 +0900）；工作树 48 项未提交（17 M + 31 ??），本复审未改动任何一项 |
| 被审驱动/候选的 git 状态 | `scripts/anonymous_staging_btrfs_probe.py`、`scripts/btrfs_subvol_state_probe.py`、`scripts/verify_position_file_evidence.py`、被审文档均为 **untracked (`??`)**；`proxy/manual_save_anonymous.py`/`proxy/manual_save_core.py` 为 **已提交且工作树干净**（HEAD blob `039cfc11…`/`763c6672…`，提交 `512a68c`，2026-10-31 16:05:22 +0900）——见 F2 |
| NAS 代码副本 | `/tmp/fnmusic-vol1-probe/code`（被审文档 §2 路径），6 个文件哈希与本地逐一相同 |
| 被审原始输出 | `/tmp/fnmusic-vol1-probe/logs/10..16-*.json` + `/tmp/fnmusic-vol1-probe/run-meta.txt`；本复审在复跑前已做哈希清单与备份（见 §4.5） |

**复审范围**：①哈希绑定（本地 ↔ 文档 ↔ NAS 副本）；②逐项核对文档 §1–§5、§7 的每个数字与结论对原始 JSON/`run-meta`；③一次独立原子复跑（含清理核验）；④设备只读状态核验（服务、`/vol1/1000`、生产路径、子卷、进程）；⑤断言审计（RO 子卷可写、非特权 linkat、generation 语义、扫描器常量/偏移、§6 证据边界、§9 并行会话）；⑥清理审计。
**不在范围**：不重测匿名暂存候选的失败/权限/并发路径（EPERM、EROFS 目标、ENOSPC、fsync 失败、并发替换等）；不验证接收端/持久化/隔离/恢复；不做停写/备份/回滚/部署/身份与授权；不打开任何数据库；不把结论外推至其它内核、其它 fnOS 版本或上游 Btrfs。

## 1. 哈希绑定（独立重算）

| 文件 | 本地 SHA-256 | NAS 副本 SHA-256 | 文档 §2 声称 | 判定 |
| --- | --- | --- | --- | --- |
| `proxy/manual_save_anonymous.py` | `fa4dc6bafcd8bddacba78584ac97e04a6ac29a765791d2cb75f4b4ee6785bc52`（20,047 B） | 同左 | `fa4dc6ba…bc52` | 符合 |
| `proxy/manual_save_core.py` | `53783b5874e7f87ed3e40741d2164dd799710df95cfc7d78b28beb66c612ba6c`（8,591 B） | 同左 | `53783b58…a6c` | 符合 |
| `scripts/verify_position_file_evidence.py` | `197d43a99e244b99021936df12e321b9fcc4c48779ce8a1b029c0de9db049b80`（16,669 B） | 同左 | `197d43a9…9b80` | 符合 |
| `scripts/anonymous_staging_btrfs_probe.py` | `3a3730e5419eed4800380af6d31ed0b5aff10c1cd601b464701e20f2d7f58c82`（21,231 B） | 同左 | `3a3730e5…8c82` | 符合 |
| `scripts/btrfs_subvol_state_probe.py` | `b3bc66dfd5c7f5754e01f3c7e339d36efa0ef85db6b9ad6cb1cb9b99d40169bf`（19,543 B） | 同左 | `b3bc66df…69bf` | 符合 |
| `proxy/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（0 B） | 同左 | `e3b0c442…b855` | 符合 |
| 被审文档自身 | `b11b851c5b04d25d6d7b760a6c7dbdd5d07f7319c2910f5b5edc6203fbb31507`（13,458 B） | —（未上传） | — | 复审开始/结束一致 |
| 编排脚本 `run_vol1_probe.sh`（文档未绑定，本复审补绑） | —（仓库内无此文件） | `b5a2350ea46ab17ae0f43e2fa8cbc26f7d957d7f0fb761071d0cad979449953e`（2,001 B） | 未声称 | 见 F3 |

→ 文档 §2 的每条哈希、以及"NAS 副本逐文件一致"的声称**全部成立**。补充事实：两个驱动脚本与扫描器为未跟踪文件，候选模块为已提交文件（F2）。

## 2. 逐项核验表（声称 → 独立证据 → 判定）

| # | 文档声称（位置） | 独立证据 | 判定 |
| --- | --- | --- | --- |
| 1 | 执行窗口 2026-11-07 02:34:41–02:34:44 JST = NAS 01:34:41–01:34:44 CST，单次原子运行（§0 抬头） | `run_meta`：`RUN_START 2026-11-07T01:34:41+08:00` / `RUN_END …01:34:44+08:00`；7 个阶段 rc=0；`probe-root-exists-before=no`、`…after=no`（+8h=02:34:41–44 JST） | 符合 |
| 2 | 环境：fnOS `1.2.0903`；内核 `6.18.18.c1168-trim`；`/vol1` btrfs、`rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/`；Python 3.11.2；btrfs-progs v6.2（§1） | 独立命令：`cat /usr/trim/etc/version`→`1.2.0903`；`uname -r`→`6.18.18.c1168-trim`；`findmnt` 同串；`python3 --version`→3.11.2；`btrfs --version`→`btrfs-progs v6.2`；JSON 内 `vol1_mount` 同值 | 符合 |
| 3 | 探针目录 `system.trim_acl` xattr 存在；`/vol1/1000` 为 `d---------` 但 kurumi 可遍历/写入（§1/§2） | `10-setup.json.probe_root_xattrs=["system.trim_acl"]`；`stat /vol1/1000`→`mode=d--------- uid=1000 gid=0`；`user-phase*` 目录由 kurumi 创建成功且 uid=1000 gid=1001（`Users`），uid 1000 发布写入成功 | 符合 |
| 4 | 探针目录结构与属主（§2）：root-phase/staging 0700 root、destination 0755 root；user-phase/staging 0700 kurumi:Users、destination 0755 kurumi:Users；btrfs-phase 含 subvol-src/snapshot-ro | `10-setup.json.dirs`：0700/0:0、0755/0:0、0700/1000:1001、0755/1000:1001；`13-btrfs.json.dirs` 含 btrfs-phase 0755；`getent group 1001`→`Users` | 符合 |
| 5 | 全部 `.err` 为空（§2） | NAS `stat -c %s logs/1[0-6]-*.err` 全为 0；备份清单中 7 个 `.err` 哈希均 `e3b0c442…`（空文件） | 符合 |
| 6 | root 的原始 O_TMPFILE：请求 `0o600`，`fchmod` 前实际 `0o700`，`fchmod` 后 `0o600`；uid 0、nlink 0、regular（§3.1） | `11-root.json.raw_tmpfile` = `requested 0o600 / before 0o700 / after 0o600 / uid 0 / nlink 0 / is_regular true`；caps `CapEff=000001ffffffffff` | 符合 |
| 7 | kurumi 同上：请求 0600 → 实测 0700 → fchmod 0600；uid 1000、nlink 0、regular；CapEff=`0000000000000000`（§3.1） | `12-user.json.raw_tmpfile` 同构 + `caps.CapEff=0000000000000000`、euid 1000 | 符合 |
| 8 | 成功链路：store 构造 ok（staging/destination 同 `st_dev=60`）；begin ok；stage fd `0600 uid0 nlink0 regular inheritable=False`；暂存期 staging/destination 均空；append/seal ok；readback SHA 与 payload 一致 `fc412855…0800c`；publish `published`，目标 `0600 uid0 nlink1 size36` 且 SHA 一致；release/close ok（§3.2） | `11-root.json.success_path` 全部字段逐项核对；`dirs…dev=60`；payload 常量独立计算 SHA-256 = `fc412855fbc86ff26f296b51d58c86a048ba434a0da7d3e974a0d53851a0800c`（36 B），与 JSON/文档一致 | 符合 |
| 9 | 冲突与负向：同 basename 再次发布 → `conflict`，原文件 inode/SHA 未变（`content_unchanged=true`）；staging 0755 → `staging_directory_not_private`；同 fd → `directories_must_be_distinct`；basename 含 `/` → `invalid_basename` 且随后 abort ok（§3.3） | `11-root.json.conflict_path`（publish value=`conflict`、`publish_is_conflict=true`、before/after ino 均 652903、SHA 不变、`content_unchanged=true`）；`negative_paths` 三项 str 与文档逐字一致、abort ok | 符合 |
| 10 | 非 root（kurumi, euid 1000, CapEff=0）成功链路：begin ok，stage fd `0600 uid1000 nlink0`，append/seal/readback 一致，publish `published`，目标 `0600 uid1000:Users nlink1`、SHA 一致（§3.4） | `12-user.json` 对应字段逐项核对（published_info: mode 0o600、uid 1000、gid 1001、nlink 1、size 36、SHA `fc412855…`） | 符合 |
| 11 | 非 root 发布真实成功；候选保留的 `EPERM → at_empty_path_capability_required` 失败关闭路径未被本机触发（§3.4） | `publish.value=published`（无异常）；`12-user.json` 无 EPERM/Unsupported 记录；源码 `manual_save_anonymous.py:164-182` 确有 EPERM→`at_empty_path_capability_required` 分支且无回退 | 符合（结论成立；失败路径未被触发本身是文档已披露的边界） |
| 12 | `_BTRFS_IOC_GET_SUBVOL_INFO = 0x81f8943c`、treeid 682、generation 1691566（读取时）、flags 0x1、uuid `5bad31ea…07fb5`、parent_uuid `da9a8553…81c6a`、`ro=true`（§4.1） | 独立重算 `(2<<30)|(504<<16)|(0x94<<8)|60 = 0x81f8943c`；`13-btrfs.json` 该快照字段逐项同值；`btrfs property get -ts … ro` stdout `ro=true` | 符合 |
| 13 | GET_SUBVOL_INFO 与 `btrfs subvolume show` 的 treeid/uuid/parent_uuid/generation 逐项一致；早期运行 treeid 674/678、generation 1691522/1691539（§4.1） | 规范运行：show 输出 `Subvolume ID 682 / UUID 5bad31ea… / Parent UUID da9a8553… / Generation 1691566` 与 ioctl 值一致；早期：`logs/04-btrfs.json` 有 674/1691522（及源 673），`logs/06-ro-diagnose2.log:54-55,83-84` 有 678/1691539 | 符合 |
| 14 | 门函数：真实 RO 快照 accepted；RW 子卷与顶层普通目录 rejected `[Errno 30] not a read-only Btrfs snapshot`；root rejected `[Errno 1] privileged scanner process`；kurumi accepted（§4.2） | `13-btrfs.json.require_*` + `unprivileged_process_gate`；`14-btrfs-user.json.unprivileged_process_gate.result=accepted`、`require_snapshot_ro.result=accepted` | 符合 |
| 15 | 非特权进程可直接 `GET_SUBVOL_INFO` 读快照：treeid 682 / flags 0x1（§4.2） | `14-btrfs-user.json.info_snapshot_ro`（euid 1000、CapEff=0）：treeid 682、flags_hex `0x1`、parent_uuid 非零 | 符合 |
| 16 | 快照根目录快照前被设为 0777、文件 0666，使写入拒绝只可能来自 RO 子卷状态（§5 引言） | `13-btrfs.json.source_modes`：source_dir `0o777`、payload `0o666`（`btrfs subvolume snapshot -r` 继承该模式） | 符合 |
| 17 | RO 快照内创建文件：root ACCEPTED；kurumi（CapEff=0）ACCEPTED；重锁 RO 后再创建 root ACCEPTED；`ro=true` 前后不变；flags 仍 0x1（§5.1） | `13-btrfs.json`: `write_inside_readonly_snapshot.result=unexpected-accept`、`ro_write_observed_effect.file_present=true`、`…snapshot_readonly_bit_still_set=true`、`info_snapshot_after_ro_write.flags_hex=0x1`、`ro_property_get…ro=true`；`write_after_final_lock.result=unexpected-accept`；`14-btrfs-user.json.unprivileged_write_inside_readonly_snapshot.result=unexpected-accept` + `unprivileged_ro_property_get…ro=true` | 符合（"写入前也执行过 property get"为措辞不精确，见 F5） |
| 18 | generation：root 写+sync 1691566→1691567；kurumi 写+sync 1691569→1691572；RO→RW→写→RO 1691566→1691569；仅 RO→RW→RO 不写 1691569→1691569；treeid/uuid/parent_uuid 不变、RO 位恢复（§5.2） | `13-btrfs.json`：`ro_write_observed_effect` 1691566→1691567；`relock_comparison` 1691566→1691569、`readonly_bit_restored=true`、三个 `*_same=true`；`cycle2_comparison.generation_changed_without_write=false`；`14-btrfs-user.json` 1691569→1691572 | 符合 |
| 19 | 影响：`_require_readonly_snapshot` 只能证明元数据标记；`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须继续 False；不得放宽 unknown 门槛（§5.3） | 源码 `verify_position_file_evidence.py:38` 常量为 `False`；`13-btrfs.json.verifier_gate_constant=False`（运行时导入值）；`scan_tree()` 在该常量为 False 时直接返回 `complete:false/unknown` | 符合 |
| 20 | §6"可证明"清单（ACL 继承/规范化、root+非特权链路、EEXIST 不覆盖、负向 reason、非特权 linkat、扫描器常量与偏移、RO 子卷可写/generation） | 见第 6–19 行；无超出原始数据的条目（仅第 1 条非 root 的 `release` 措辞过于概括，见 F6） | 符合（个别措辞，F6） |
| 21 | §6"不能证明"清单：不证明上游一般行为；不证明失败/权限路径（未触发 EPERM）；不证明生产管线可用（`PIPELINE_INTEGRATION_ALLOWED=False`）；不证明 jobs DB/停写/备份/恢复/回滚/部署/身份；§0.1/§0.2 门禁仍 BLOCKED | 逐条核对：候选源码 `PIPELINE_INTEGRATION_ALLOWED=False`（第 38 行）；失败路径确无真实内核证据（本复跑同样只触发成功路径）；§6 引用"独立复审 F1/F3"可核实——`docs/NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md` 的 F1（"全部失败/权限路径"过度声称）与 F3（故障/竞争条款缺用例）确实存在且与本处上下文一致 | 符合（诚实；除 F1 的 §9 归属问题，见下） |
| 22 | 清理：两个子卷 `btrfs subvolume delete` 成功 + btrfs-phase 目录删除；全树 rmtree；`probe-root-exists-after=false`；无残留子卷；`/vol1/1000` 顶层运行前后完全一致；未停/启服务、未改配置、未安装/回滚（§7） | `15-btrfs-clean.json`：delete_snapshot/delete_source rc=0、`exists_after_delete` 均 false、`rmtree_phase_dir=ok`、`phase_dir_exists_after=false`；`16-clean.json`：`probe_root_exists_before=true`、`rmtree=ok`、`after=false`；`16-clean` 与 `10-setup` 的 `/vol1/1000` 条目集合相等（47/47）；设备侧：无探针子卷（当前 `btrfs subvolume list /vol1` 49 项、grep 无）、服务 `ActiveEnterTimestamp 2026-11-01 17:18:30`/`InvocationID 53d953f3…`/`MainPID 1870197`/`NRestarts=0`（复审前后一致，说明未重启） | 符合 |
| 23 | 复现说明（§8：scp 6 文件 → `bash /tmp/fnmusic-vol1-probe/run_vol1_probe.sh`；先确认探针根不存在） | 复跑验证可行；但 `run_vol1_probe.sh` 只在 NAS `/tmp`、文档未绑定其哈希、仓库中不存在，§8 的 scp 清单不足以重建"单次原子运行"编排 | 部分（见 F3） |
| 24 | §9：另一并行会话的记录 `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` 引用了本探针早期运行的 JSON `（/tmp/fnmusic-btrfs-subvol-btrfs.json，已由该并行会话清理）`；两份记录"结论一致、来源独立" | 该 JSON 路径当前（复审 01:39 时）内容为**规范运行 01:34 的输出**（SHA-256 `7953d632…` 与 `logs/13-btrfs.json` 相同），不是早期运行内容；同仓该文档第 5 行把该早期 JSON 称为"**属并行操作者产物**"、第 66 行称两个探针脚本"由并行操作者创建并在生产 /vol1 上执行，超出 Lead 下发的只读任务范围"——与本文件 §9 的"本探针早期一次运行""来源独立"在归属上相冲突；"已清理"无独立记录可证 | **部分/unknown（F1）** |

## 3. 独立复跑（原始输出摘要）

复跑与此前证据路径的处置顺序：**先备份原始证据并记录哈希 → 复跑 → 比对 → 恢复原始证据并校验**（因为 `run_vol1_probe.sh` 设计上会覆盖 `logs/10..16` 与 `run-meta.txt`，见 §4.5）。

### 3.1 复跑前（NAS 本地 01:39:14 CST）

- `/vol1/1000/fnmusic-btrfs-probe-20261107`：**不存在**（`PROBE_ROOT=ABSENT`）。
- `/vol1/1000`：47 个条目，集合 SHA（`ls -A | sort`）`c78985babe685f2c5851623258b1946f4e06a31f30bcabac6b360af951435f9c`；`stat`：`d--------- uid=1000 gid=0 nlink=1`。
- 服务 `fnmusic-ext.service`：`active/running`，`MainPID=1870197`，`InvocationID=53d953f3475047e484e9fdd955b71716`，`ActiveEnterTimestamp=Sun 2026-11-01 17:18:30 CST`，`NRestarts=0`。
- 探针子卷：无（`btrfs subvolume list /vol1` 共 49 项，grep `fnmusic-btrfs-probe` 无命中）；探针进程：无。
- 生产路径（只读 `stat`，未打开 DB）：`fnmusic-test-20261031` 3 个条目、`fnmusic-manual-save-staging` **0 个条目**、`music`（原生曲库）mtime 2026-10-18、`repo/.env` mtime 2026-11-01 17:01:47、`repo/VERSION` 同上——均记录指纹备比。

### 3.2 复跑输出（NAS 本地 01:41:17–01:41:19 CST，约 2–3 秒）

```
### 10-setup rc=0
### 11-root rc=0
### 12-user rc=0
### 13-btrfs rc=0
### 14-btrfs-user rc=0
### 15-btrfs-clean rc=0
### 16-clean rc=0
== run-meta ==
RUN_START 2026-11-07T01:41:17+08:00
probe-root-exists-before=no
...（7 个阶段 rc=0）...
RUN_END 2026-11-07T01:41:19+08:00
probe-root-exists-after=no
== residual ==
ls: cannot access '/vol1/1000/fnmusic-btrfs-probe-20261107': No such file or directory
no-probe-subvolumes
```

### 3.3 规范运行 vs 独立复跑的结构化比对（复跑前用本地备份做）

- 7 个 JSON 的**键集合完全一致**（10-setup 186、11-root 165、12-user 84、13-btrfs 239、14-btrfs-user 75、15-btrfs-clean 98、16-clean 84 个叶子键）。
- **非易变字段差异 = 0**；差异只出现在预期易变维度：`time`、`ino`、`treeid`（681/682→683/684）、`generation`（基值 1691566→1691587，每次写入相对推进关系一致）、`uuid`/`parent_uuid`、以及含上述值的 btrfs 命令 stdout。
- **关键语义逐项相同**：ACL 继承 `0700→fchmod 0600`（root/kurumi 均是）、stage fd `0600/uid/nlink0/inheritable=False`、payload/readback/published SHA 均 `fc412855…0800c`、publish=`published`、conflict=`conflict`+`content_unchanged=true`、三个负向 reason 逐字相同、ioctl=`0x81f8943c`、gate 常量 `(False, 1)`、RO 快照 accepted / RW 与普通目录 rejected Errno30、root rejected Errno1、kurumi accepted、非特权 GET_SUBVOL_INFO 可读 flags `0x1`、RO 快照写入 `unexpected-accept` 且 RO 位仍置位、generation 相对变化（写推进 / 仅切标志不推进）、清理 rc=0 且探针根不存在。

### 3.4 复跑后（NAS 本地 01:42:00 `stat` + 最终复核）

- 探针根**不存在**；无探针子卷（49 项不变）；无探针进程。
- `/vol1/1000` 47 条目、集合 SHA 仍为 `c78985ba…`；且与规范运行 `10-setup` 的 `vol1_1000_entries_before` **集合完全相等（47/47，`sets_equal True`，两个方向差集均为空）**。
- 服务状态与复跑前逐字段相同（同一 `InvocationID`/`MainPID`/启动时间，`NRestarts=0`）→ 未停/启/重启。
- 生产路径 `stat` 与目录指纹（`music-test` 3 条目 `1bef11ce…`、`staging` 0 条目 `98fdfec9…`、`music` mtime 2026-10-18）与复跑前完全一致。
- 复跑产物已另存 `/tmp/fnmusic-review-20261107/rerun/`（logs 14 个文件 + `run-meta.txt`），并保留本地副本。

## 4. 清理与残留核验（复审者视角，含自己）

| 项 | 结果 | 证据 |
| --- | --- | --- |
| `/vol1` 探针根 | **无** | `ls -d /vol1/1000/fnmusic-btrfs-probe*` → 无；前后两次快照均 `PROBE_PATH_ABSENT` |
| 探针子卷 | **无** | `btrfs subvolume list /vol1 \| grep fnmusic-btrfs-probe` → 无；子卷总数 49（复跑前/后相同） |
| 探针进程 | **无** | `ps` grep 两个探针脚本与编排脚本 → 无命中 |
| `/vol1/1000` 顶层 | 无增删 | 47/47 集合相等；运行前（01:34 证据内）、复审前（01:39）、复审后（01:42/最终）四次观测一致 |
| 生产曲库/暂存/账本/配置 | 未被写入 | `stat`/目录指纹与复审前一致（mtime 均早于复审窗口）；`staging` 0 条目；未打开任何 DB（设备本无 jobs DB 文件，依据 `docs/DEVICE_INVENTORY_20261107.md` §1.1-4，仅引用不重查） |
| 服务/容器 | 未停/启/改 | 服务 `InvocationID`、`MainPID`、`ActiveEnterTimestamp` 不变、`NRestarts=0`；未执行任何 systemctl 写操作 |
| 探针在 `/tmp` 的副产物 | **存在 7 个，属规范运行设计内产物**（`/tmp/fnmusic-vol1-probe-{setup,root,user,cleanup}.json`、`/tmp/fnmusic-btrfs-subvol-{btrfs,user,cleanup}.json`），复跑后内容被同路径刷新；复审结束前已按"备份 → 复跑 → 恢复"流程恢复为规范运行字节 | 见 §4.5；文档 §2/§7 未提及这 7 个副产物（F4） |
| 复审者自身产物 | NAS `/tmp/fnmusic-review-20261107/`（236 KB：原始证据备份、复跑副本、审查脚本与日志）与本机 `E:\fnmusic\review-tmp\`；**均在受保护路径之外**，未写入仓库与 `/vol1` | 披露见 §9 |

### 4.5 对"被审证据路径"的处置（必须披露）

1. 复跑前（01:39 CST）将 `logs/`、`run-meta.txt`、`run_vol1_probe.sh` 复制到 `/tmp/fnmusic-review-20261107/canonical/`，并记录全部哈希（含 7 个 `.err` 空文件）。
2. 01:41:17–19 按任务要求执行 `bash /tmp/fnmusic-vol1-probe/run_vol1_probe.sh`；该脚本**按设计覆盖** `logs/10..16`、`run-meta.txt` 与 7 个 `/tmp` 副产物 JSON。原始规范运行产物已先备份。
3. 比对完成后，把**规范运行的原始字节**恢复到文档引用的路径，并以复跑前记录的哈希清单执行 `sha256sum -c`：**30/30 OK，FAILED=0**（含 14 个 logs 文件、`run-meta.txt`、`run_vol1_probe.sh`、7 个 `/tmp` 副产物、6 个 NAS 代码文件）。恢复后的 `run-meta.txt` 仍为 `RUN_START 01:34:41 … RUN_END 01:34:44`。
4. 复跑产物保留在 `/tmp/fnmusic-review-20261107/rerun/`，供交叉核对；本机 `E:\fnmusic\review-tmp\` 亦有副本。
5. 因此当前 NAS 文档引用路径中存放的仍是 **01:34 规范运行**的证据；复跑证据与规范证据可区分（哈希见 §10）。

## 5. 断言审计

1. **"RO 子卷可写"成立（本机）**：`ro=true` + `flags bit0=1` 时，root 与 uid1000/CapEff=0 的 `open(…,"wb")` 均返回 `unexpected-accept`，文件真实出现在快照树内（`file_present=true`，`entries_after.snapshot` 含写入文件），RO 位未被清除；独立复跑复现。**边界**：仅本机（fnOS 1.2.0903 + `6.18.18.c1168-trim` + `trimacl`），不能外推上游/其它版本——被审文档已如此声明。
2. **"非特权 linkat(AT_EMPTY_PATH) 成功"成立（本机）**：kurumi 进程 `CapPrm/CapEff=0`、euid 1000，经候选模块的 `linkat(fd,"",dirfd,name,AT_EMPTY_PATH)` 真实发布成功，目标 `0600 uid1000:Users nlink1`；候选源码无 `/proc/self/fd`/rename 回退（`manual_save_anonymous.py:8-12,140-182`）。**边界**：成功原因（内核 6.18 行为 vs fnOS 补丁）未定位；失败关闭分支（EPERM）未被真实触发——文档已披露。
3. **"generation 写入后推进、仅切标志不推进"成立（本机，单次运行内）**：1691566→1691567（root 写）、1691566→1691569（RO→RW→写→RO）、1691569→1691569（仅两次切标志、无写）。**边界**：只是"推进/不推进"的现象观测，不是完整一致性证明；文档 §5.3-3 已限制。
4. **"扫描器常量/偏移正确"在本机 UAPI 上成立**：ioctl 号独立重算 `0x81f8943c`；treeid/generation/uuid/parent_uuid 四个字段的读取值与 `btrfs subvolume show` 逐项一致；flags bit0 与 `btrfs property get ro` 一致；504 字节 ioctl 在真实快照上成功。**边界**：证的是"本机 UAPI 下这些常量/偏移可用"，不是"永久正确"。
5. **§6 证据边界总体诚实**：`EPERM` 未触发、失败/权限路径仍为 mock/推导（并正确引用独立复审 F1/F3）、`PIPELINE_INTEGRATION_ALLOWED=False`、jobs DB/停写/备份/恢复/部署/身份均未证明、§0.1/§0.2 仍 BLOCKED——逐条核实无相反事实。发现少数措辞不精确（F5/F6/F7），以及 §9 的归属/独立性问题（F1）。
6. **§9 可核实性：不可完全核实**。详见 F1：早期运行的执行者归属在两份同仓文档间冲突；"来源独立"未被证明（数据血缘有重叠）；"已由该并行会话清理"无独立记录，且该路径在复审时是规范运行输出（已恢复）。**不影响**本文机器可验证证据——规范运行自包含，且已被独立复跑复现。

## 6. 编号发现

**阻断（blocking）：无。**

### F1（重要）§9 并行会话/来源独立性的表述不可核实，且与同仓另一文档归属冲突

- 位置：被审文档 §9（第 176–178 行）。
- 事实：(a) 同仓 `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` 第 5 行把早期 JSON 称为"属**并行操作者**产物"，第 66 行称两个探针脚本"由**并行操作者**创建并在生产 `/vol1` 上执行，**超出 Lead 下发的只读任务范围**"；而被审文档 §9 称其引用了"**本探针早期一次运行**的 JSON"。(b) "已由该并行会话清理"无独立记录；复审时该路径内容为规范运行输出（与 `logs/13-btrfs.json` 同哈希），其早期内容已不存在，无法验证由谁、何时清理。(c) "来源独立"未被证明：早期探测（01:19–01:20）与规范运行由同一组脚本驱动，且另一文档的第②来源是 Lead 在同一快照上的诊断——数据血缘有重叠。
- 影响：不影响规范运行本身的机器可验证结论（自包含 + 本复审独立复跑），但影响**归属与证据链表述的诚实性**，且涉及"是否超出 Lead 任务范围"的治理问题。
- 建议：把 §9 改写为可核实表述——明确早期运行的实际执行者与所用脚本来源、说明另一文档的引用关系、删除或限定"来源独立"（可改为"结论一致；早期数据与本规范运行由同一组脚本/同一探针树驱动，不构成独立数据源"），并对"已清理"注明依据来源或改为 unknown。

### F2（次要）"未提交候选"标签与 git 事实不符

- 位置：被审文档 §2 第 3 行、§6 第 138 行。
- 事实：`proxy/manual_save_anonymous.py` 与 `proxy/manual_save_core.py` **已提交且工作树干净**：HEAD `cf843a4` 的 blob 分别为 `039cfc1145cdbf2f7fe04dc8fecfa0815df44e39` / `763c66729317ef4a4aba9bf503bf3f61b632a289`，提交 `512a68c8d9c71aea26ca54955d7d07e0ad878715`（2026-10-31 16:05:22 +0900）；`git hash-object` 与 `git rev-parse HEAD:<path>` 相同，`git diff HEAD` 为空。未跟踪(`??`)的是两个驱动脚本与 `verify_position_file_evidence.py`（现有扫描器），不是这两个候选。
- 影响：元数据标签错误；哈希绑定与证据可复现性不受影响（文件内容哈希仍正确）。若"未提交"意在"未部署/未集成"，建议明确区分。
- 建议：改为"已提交于 HEAD、未部署/未集成候选（哈希 …）"。

### F3（次要）复现绑定不完整：编排脚本未绑定哈希且不在仓库

- 位置：被审文档 §2/§8。
- 事实：`run_vol1_probe.sh`（NAS `/tmp/fnmusic-vol1-probe/`，2,001 B，SHA-256 `b5a2350ea46ab17ae0f43e2fa8cbc26f7d957d7f0fb761071d0cad979449953e`）是"单次原子运行 + 7 阶段 + 清理 + run-meta"的唯一编排者，但文档 §2 未给其哈希，仓库内无该文件；§8 的 scp 清单（6 个文件）不包含它，第三方无法仅按 §8 重建同一原子运行。`/tmp` 易失，长期不可复现。
- 建议：把 `run_vol1_probe.sh` 纳入仓库并在 §2 绑定哈希；§8 增加该文件的 scp/内容。

### F4（次要）探针的 `/tmp` 副产物未在文档中说明

- 位置：被审文档 §2/§7。
- 事实：两个探针脚本除 stdout→`logs/*.json` 外，还会写 `/tmp/fnmusic-vol1-probe-{setup,root,user,cleanup}.json` 与 `/tmp/fnmusic-btrfs-subvol-{btrfs,user,cleanup}.json`（共 7 个，代码 `main()` 中 `open("/tmp/…-{phase}.json","w")`）。它们不随 §7 清理；§9 引用的正是其中一个路径。复审时其内容 = 规范运行输出（哈希与 `logs/` 对应文件相同），复跑后被同路径刷新（复审已恢复）。
- 影响：读者核对 §9 时会看到该文件存在，易与"已清理"表述混淆；`/tmp` 残留无清理承诺。
- 建议：§2 列明这 7 个副产物（或改为写入 `logs/`）；§9 注明"该路径已被规范运行自身重写"。

### F5（次要）§5.1"`btrfs property get` 写入前后均如此"精度不准确

- 位置：被审文档 §5.1 表第 4 行。
- 事实：规范运行中 `btrfs property get … ro` 只在**首次写入之后**执行（`13-btrfs.json.ro_property_get` 位于 `write_inside_readonly_snapshot` 之后；`14-btrfs-user.json` 同理）。写入前的证据是 `GET_SUBVOL_INFO flags=0x1` 与 `btrfs subvolume show` 的 `Flags: readonly`，不是 `property get`。
- 影响：结论不变（RO 标记在写入前后均存在，由两套观测共同支撑），但表述应精确。
- 建议：改为"写入后 `property get ro=true`；写入前由 GET_SUBVOL_INFO flags 与 subvolume show 证明 readonly"。

### F6（次要）§6 可证明清单把非 root 路径写成包含 `release`

- 位置：被审文档 §6 第 138 行。
- 事实：root 链路调用了 `release_owned_stage`（`11-root.json.success_path.release=ok`）；但 **kurumi 阶段没有调用 release**（`12-user.json` 无 release 键，仅有 `close=ok`）。§3.4 表本身未声称 release（正确），§6 概括句"root 与非 root … → release"对非 root 不成立。
- 影响：轻微过度概括；不影响发布成功结论。
- 建议：§6 改为"root 完成了含 release 的完整链路；非 root 完成了至 publish 的链路并以 close 结束（release 未在该阶段执行）"。

### F7（次要）§2 三处措辞/路径不精确

- 位置：被审文档 §2。
- 事实：(a)"不存在跨阶段长驻目录"字面不准确——探针树在单次运行的 7 个阶段之间持续存在（setup 创建、cleanup 删除），应表述为"运行结束后不残留、不跨运行存在"。(b)"原始输出：NAS `/tmp/fnmusic-vol1-probe/logs/10..16-*.json` 与 `run-meta.txt`"——`run-meta.txt` 实际在 `/tmp/fnmusic-vol1-probe/run-meta.txt`（`logs/` 的兄弟路径），不在 `logs/` 内。(c)`logs/` 中还存有 01–08 早期运行产物（01-setup/02-root/03-user/04-btrfs/05-btrfs-user/05-ro-diagnose/06-ro-diagnose2/07-hygiene/08-cleanup-monitors），文档未说明其归属；它们被 §4.1 的早期运行交叉核验实际使用（674/1691522、678/1691539）。
- 建议：修正措辞与路径，并说明 01–08 为早期运行产物（可在 §4.1 引用出处）。

## 7. Verdict：PASS-with-limits

理由：
- 被审文档 §2 的全部 5+1 个哈希、以及"NAS 副本逐文件一致"成立；被审文档自身哈希固定且全程未变。
- §1–§5、§7 的**每一个数字与结论**都能在原始 JSON/`run-meta.txt` 中找到对应项（本复审逐项核对，含 47 项顶层清单、每个 mode/uid/nlink/SHA、每个 generation 值、每个门函数结果、每条清理结果）。
- 独立原子复跑一次成功（7/7 rc=0，前后探针根均不存在，无残留子卷/进程），**0 个非易变字段差异**，关键语义逐项相同；复跑后设备状态与复审前一致（服务同一 `InvocationID`、生产路径指纹不变、`/vol1/1000` 集合不变）。
- 未发现阻断问题，也未发现被审文档对原始数据的错误引用；§6 证据边界总体诚实。
- 但存在 F1（重要：§9 归属/独立性不可核实且与同仓文档冲突）与 F2–F7（次要：标签、复现绑定、/tmp 副产物、措辞/精度），故为**有条件通过**而非无条件 PASS。

## 8. 明确"解除了什么、没解除什么"

**在本复审范围内解除（即：这些声称已被独立证据支撑/复现）**：
1. 被审文档记录的**真实性与内部一致性**：§2 哈希链、§3/§4/§5 的每个数字、§7 清理结论，均与原始 JSON 一致；规范运行可独立复跑复现。
2. 候选 `manual_save_anonymous.py` 在**本机 `/vol1` Btrfs + trimacl** 上的匿名暂存成功链路（root 与 uid1000/CapEff=0），包括 O_TMPFILE 请求 0600 实测 0700、fchmod 规范化、seal/readback、真实 `linkat(AT_EMPTY_PATH)` 发布、0600 与 nlink=1、EEXIST 冲突不覆盖、三个负向 reason。
3. 扫描器 `verify_position_file_evidence.py` 的 ioctl 号/结构偏移/字段语义在**本机 UAPI** 上成立；RO 快照门与非特权门按设计接受/拒绝。
4. **本机** `ro=true` Btrfs 子卷可被 root 与 kurumi 写入、RO 位不被清除、真实写入推进 generation、仅切只读标志不推进。
5. 本次探针运行**未在 `/vol1` 留下残留**（探针根、子卷、`/vol1/1000` 名称集合、进程），生产路径/服务状态未因该运行改变（浅层只读核验）。

**没有解除（不得据此暗示）**：
1. **§0.1/§0.2 部署门禁与任何上线/迁移授权仍为 BLOCKED**；`PIPELINE_INTEGRATION_ALLOWED=False` 未变。
2. 匿名暂存候选的**失败/权限/并发路径**（EPERM、EROFS 目标、ENOSPC、fsync/写失败、并发替换、部分封条等）仍无真实内核证据（与 `NATIVE_EVIDENCE_INDEPENDENT_REVIEW_20261107.md` 的 F1/F3 一致）。
3. 匿名暂存作为**生产管线**：接收端、持久化、隔离、恢复、身份/授权边界均未验证。
4. 扫描器**不能**作为文件验收证据：`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须保持 `False`，"RO 位=1 ⇒ 扫描期不可变"在本机不成立；generation 只是"可能变化"的候选信号，不是完整一致性证明。
5. **不可外推**到其它 fnOS 版本、其它内核、上游标准 Btrfs 或其它挂载选项。
6. **§9 的来源独立性/并行会话归属**未被本复审核实（F1），且与同仓文档存在归属冲突。
7. 本次复审只做了浅层设备只读核验（`stat`/`findmnt`/`systemctl show`/子卷列表/目录列表），**未**做生产链路级、容器级、账本级或权限级的深度验证；未打开任何 DB。
8. 本次复审**没有**重测"停写→快照→扫描"设计，也没有验证任何替代方案。

## 9. 复审者操作披露与无法核验项

**已执行的操作**（除复跑外均为只读）：
- 只读命令：哈希、`stat`、`ls`、`btrfs subvolume list`、`findmnt`、`systemctl is-active/show`、`ps`、`grep`（原始 logs）、`cat /usr/trim/etc/version`、`uname`、`python3 --version`、`btrfs --version`、`getent group 1001`。
- 写操作仅限：① `/vol1` 上由**受审脚本自身**创建并删除的一次性探针树（任务明确允许的独立复跑）；② NAS `/tmp/fnmusic-review-20261107/` 下本复审的备份/副本/脚本/日志（236 KB，未在受保护路径）；③ 本机 `E:\fnmusic\review-tmp\` 的临时分析文件（不在仓库内）。**未**修改任何代码、被审文档、设备配置，未停/启/重启服务，未安装/回滚包，未打开 DB。
- 证据路径处置：复跑覆盖 → 恢复规范运行原始字节（30/30 哈希校验通过），复跑副本另存，详见 §4.5。

**无法核验/仍为 unknown 的项**：
1. §9 早期运行（01:19–01:20）与并行会话的实际执行者、清理动作与授权边界（无独立记录；两份文档归属冲突）。
2. SSH "key-only"：只证明了 key 登录可用（`BatchMode=yes` 成功），未证明服务端禁用了密码认证。
3. 规范运行当时的 stdout（编排脚本回显）未被保存；只剩 `run-meta` + 7 个 JSON，故无法复核规范运行的即时控制台输出。
4. 规范运行是否在代码路径之外还有其他设备副作用：只能从脚本静态审查（仅写 `PROBE_ROOT`、`/tmp`、及其下 btrfs 子卷命令）与前后状态推断，未做系统调用级审计。
5. `/tmp` 中其它 session 的 `fnmusic-*` 文件（如 `fnmusic-native-20261107/`、`fnmusic-offline-runner-20261107/`）归属与内容不在本复审范围。
6. 本复审窗口之外（01:34–01:39 之间）设备是否被其它会话改动：无法排除；但规范运行 JSON 的 pre/post 清单、本复审 01:39 与 01:42 的两次快照、以及服务重启计数器共同提供了连续覆盖。

## 10. 附录：关键原始输出哈希（复审时录制）

**规范运行（01:34:41–44，复跑前录制；恢复后 30/30 校验通过）**

| 文件 | SHA-256 |
| --- | --- |
| `logs/10-setup.json` | `a1121f73833dc51a19f7798a8e4d2038376a2c5e118e6ad4f37cb181e6da2c43` |
| `logs/11-root.json` | `42cfa46bb84ead1e2e26b44020d09bac9a88ff5fc348a325ec5f18e8ffbc462c` |
| `logs/12-user.json` | `855e76395bfe0d5fe40ec4a009761ac540cfee186cd4a7c9faab754d83c6b19c` |
| `logs/13-btrfs.json` | `7953d632096a8dfae71266c1fb50239487afbd92d587a3da29a892cbb94fbcb6` |
| `logs/14-btrfs-user.json` | `ad4756d3b683aff44c598a9564b2f27f0a1324a88cf553ec949ed3d04014c4e9` |
| `logs/15-btrfs-clean.json` | `9ed048e0a4977e0748ad65284dd08691e98a3eabf3726d267d6a1088b51c5da3` |
| `logs/16-clean.json` | `aa78f38a26afb8f943da57cb16323df427dd3327eae1afd1fc37a0e116f3f503` |
| `logs/1[0-6]-*.err`（7 个） | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（全空） |
| `run-meta.txt` | `4998863d5720bbeeaa123937371f70d1cbe23a5906ac030c5ad2730994195af8` |

**独立复跑（01:41:17–19；副本：`/tmp/fnmusic-review-20261107/rerun/`、本机 `review-tmp\rerun\`）**

| 文件 | SHA-256 |
| --- | --- |
| `logs/10-setup.json` | `04897011052747270dd481298bbcc67bb9fc2e1090e6f5ee8d4d275339928238` |
| `logs/11-root.json` | `de3ebca6968e1c3d3ed96981d28ccd66fb7e731910a839d1c30710b4ad1757c7` |
| `logs/12-user.json` | `2235524468428c7ce7c28e17b58593061025052e5037f703983d1464babcaab4` |
| `logs/13-btrfs.json` | `5f1a1de291c8bfa3daa5d49980243b87340ec2d98023fa5bcb069dc30bd9ce90` |
| `logs/14-btrfs-user.json` | `78ab3028beab8ebde39dd5aef4db01aa891e61233ca78404e3555914252be3c8` |
| `logs/15-btrfs-clean.json` | `89f82f54b5a51f0219c8aff015ab63606e65f7b78e74b12694a1d8bb3915f983` |
| `logs/16-clean.json` | `a6f668ca5bb0402bdc9d5fcc8c51ec023f9dba28e656235f8f9591df5327e54b` |
| `run-meta.txt` | `cf4b03447ad11c19c0984723def57d491996429797b31154af03760ed492f2da` |

## 11. 第 2 轮返工确认（F1–F7 逐条）

> 本轮**只核对返工**：未重跑探针、未访问设备、未修改被审文档或任何代码；唯一写入是本报告本节与文末哈希戳行。

### 11.1 本轮对象与绑定

| 项 | 值 |
| --- | --- |
| 被审文档修订版 | `docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`，16,103 B，mtime 2026-11-07 02:49:31 JST，SHA-256 `ff3c4d0bc51aa9ac35a15064ce953472251e2d0a79acfcc278c5f7793c3206f4`（与 Lead 声称一致；旧版 `b11b851c…1507`） |
| 新增编排脚本 | `scripts/run_vol1_probe.sh`，2,001 B，SHA-256 `b5a2350ea46ab17ae0f43e2fa8cbc26f7d957d7f0fb761071d0cad979449953e`；与第 1 轮记录的 NAS `/tmp/fnmusic-vol1-probe/run_vol1_probe.sh` 哈希**相同**；git 状态为 untracked（`??`，见 N2） |
| 证据基座 | HEAD 仍 `cf843a40…`；5 个受审文件 + `proxy/__init__.py` 的 SHA-256 与第 1 轮**逐一相同**（未被改动），本轮未触碰设备 |

### 11.2 F1–F7 逐条结论

| 编号 | 第 1 轮问题 | 修订证据（修订版位置） | 结论 | 残留 / 理由 |
| --- | --- | --- | --- | --- |
| **F1**（重要） | §9 归属/独立性不可核实，且与并行文档冲突 | §9 整体改写（第 178–184 行）：①明确早期运行与规范运行均由 Lead 使用 §2 同一组驱动脚本、同一探针树执行，早期运行不是独立数据源；②"来源独立"明确改为**不成立**并删除该声称，可信度改绑定自包含复现/哈希/本复跑；③早期探针树消失时间 01:23 前后，**清理执行者与授权依据记为 unknown**，并如实引述并行文档"由其清理"的声称无独立证据；④新增治理/授权说明；⑤如实引述并行文档"并行操作者/超出 Lead 只读任务范围"的原话并显式声明两份文档归属表述不一致、本节按实际执行记录更正 | **resolved** | R1（非阻断）：并行文档 `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` 本轮未见同步（mtime 02:24:57，早于第 1 轮复审读取；其"并行操作者产物/由并行操作者创建…超出 Lead 下发的只读任务范围"表述仍在），跨文档矛盾仍存在，仅被本文件披露而未消除；"早期运行由 Lead 执行"只能依据 Lead 陈述，原始产物无法独立裁决。 |
| **F2**（次要） | 候选标签"未提交"与 git 事实不符 | 抬头第 3 行改为"已提交于 HEAD、未部署/未集成"；§6 第 139 行改为"已提交于 HEAD `512a68c`，未部署/未集成" | **resolved** | N1（轻微）：HEAD 实为 `cf843a40…`，`512a68c` 是其祖先提交；"已提交于 HEAD `512a68c`"易被读成 HEAD=512a68c。建议改为"已在提交 512a68c 提交（HEAD cf843a4 的祖先）"。 |
| **F3**（次要） | 编排脚本未绑哈希/不在仓库 | §2 第 30 行绑定 `run_vol1_probe.sh = b5a2350e…`（并注明与 NAS 同哈希）；§8 第 170 行补 `scp scripts/run_vol1_probe.sh kotomi-root:/tmp/fnmusic-vol1-probe/run_vol1_probe.sh` | **resolved** | N2（轻微）：该文件在工作树中为 untracked（`git status` 显示 `??`）；"已纳入本仓库"若理解为 git 跟踪则不准确。建议写"已加入工作树（未跟踪）"，或在允许提交后跟踪。 |
| **F4**（次要） | 7 个 `/tmp` 副产物未说明 | §2 第 32 行列出 7 个副产物（`/tmp/fnmusic-vol1-probe-{setup,root,user,cleanup}.json`、`/tmp/fnmusic-btrfs-subvol-{btrfs,user,cleanup}.json`），说明与 `logs/` 对应文件同内容、不随 §7 清理、属易失副产物 | **resolved** | 与第 1 轮实测映射一致（7/7 副产物与对应 `logs` 同哈希），无需进一步动作。 |
| **F5**（次要） | §5.1 `property get`"写入前后均如此"不精确 | §5.1 第 115 行改为"首次写入**之后**执行，`ro=true`；写入前的只读状态由 `GET_SUBVOL_INFO flags=0x1` 与 `btrfs subvolume show` 的 `Flags: readonly` 证明" | **resolved** | 与 `13-btrfs.json` 的执行顺序一致（`property get` 在首次写入后；写入前为 `info_snapshot_ro.flags_hex=0x1` 与 `subvolume_show_snapshot` readonly）。 |
| **F6**（次要） | §6 把非 root 路径也写成含 `release` | §6 第 139 行改为"root 完成含 `release_owned_stage` 的完整链路，非 root（CapEff=0）完成至 `publish` 的链路并以 `close` 结束（该阶段未调用 release）" | **resolved** | 与 `11-root.json.success_path.release=ok`、`12-user.json` 无 release 键（仅 `close=ok`）一致。 |
| **F7**（次要） | §2 措辞/路径不精确 | §2 第 22 行改为"探针树在本次运行的各阶段之间短暂存在，运行结束即删除；**不跨运行残留**（§7）"；第 32 行改为"与**同级** `/tmp/fnmusic-vol1-probe/run-meta.txt`"并说明 `logs/` 另存 01–08 早期运行产物（见 §9） | **resolved** | 与第 1 轮实测一致；01–08 产物归属与 §4.1 早期运行交叉核验（674/1691522、678/1691539）自洽。 |

### 11.3 是否引入新问题 / 回归检查

- **技术数字回归检查**：逐行比对修订版与第 1 轮已核验清单——§1 环境、§2 的 5 个旧哈希、§3 的 mode/uid/nlink/SHA、§4 的 `0x81f8943c`/682/1691566/0x1、§5 的 1691566→1691567 等 generation 链、§7 清理结论均**未变**且仍与原始 JSON 一致；未发现因改写引入的数值错误。
- **新引入问题**：仅 N1（"HEAD 512a68c"措辞）与 N2（"已纳入本仓库"与 untracked 状态）两处**轻微精度问题**，均不改变事实结论、不构成阻断。
- **降级风险**：修订版第 6/188 行引用本复审为 PASS-with-limits 且"无阻断发现"，与本报告一致；未发现修订版把未解除项写成已解除。

### 11.4 本轮判定

- **F1 resolved（残留 R1）；F2–F7 resolved（F2/F3 各带轻微残留 N1/N2）；unresolved：无；新增阻断：无。**
- 返工满足第 1 轮全部编号要求，修订版仍为 **PASS-with-limits**；第 1 轮技术结论与"未解除"清单（§8）不变，仍不得暗示 §0.1/§0.2 或任何部署门禁通过。
- 非阻断建议：(a) 在并行文档中加交叉引用或统一 §9 的归属记录，以消除跨文档矛盾；(b) F2 措辞改为"已在提交 512a68c 提交（HEAD cf843a4 的祖先）"；(c) F3 若指 git 跟踪，注明当前为 untracked。

### 11.5 本报告自身哈希说明

- 第 1 轮版本（§0–§10 全部内容，37,947 B）：SHA-256 `73272c24fb2c8fb11893d8b24e03888af8ce2696d6072a2459eefa6760b25183`。
- 第 2 轮仅在本节追加内容，§0–§10 的字节未被改动（复核方法：取本文件前 37,947 字节重算，应等于上列第 1 轮哈希）。
- 追加本节后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REPORT-HASH-STAMP` 行给出 `sha256(本行之前全部字节)` 的自校验值（删除该行后对整文件重算即得）。

<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 9d3318e2c314209c72e5191f26f3dc59db0c386170534319d3c2e6981edc3e76 -->
