# 2026-11-07 证据原始输出清单（持久副本）

本目录保存 2026-11-07 设备探针的**原始输出**，用于把易失的 NAS `/tmp` 证据复制到受控持久位置（对应 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` §7.6 的 R21 风险项）。

| 项 | 值 |
| --- | --- |
| 采集设备 | `kotomi`（fnOS NAS，192.168.0.106） |
| 环境 | fnOS `1.2.0903`，内核 `6.18.18.c1168-trim`，`/vol1` btrfs `rw,relatime,trimacl,space_cache=v2`，Python 3.11.2，btrfs-progs v6.2 |
| 采集者 | Lead（Agent Teams） |
| 窗口 | 2026-11-07 10:02–10:24（NAS 本地 CST；含 F2 纠正实验 10:24:57） |
| 证据性质 | **现场证据，不是上线/部署/迁移授权**；不得外推到其它内核/fnOS 版本 |

## 文件

| 文件 | SHA-256 | 大小 | 来源 |
| --- | --- | --- | --- |
| `fnm-ro-rootcause.log` | `8cbbab2364cffe7e9b34d63789b5de7244cb381f6afb4e6ea2a06c8c7ae8cbad` | 2,414 B | `scripts/btrfs_ro_rootcause_probe.sh` 的运行日志（NAS `/tmp` 原件同哈希） |
| `fnm-ro-mitigation.log` | `1a4b893c4f11dc14492a14419fa5bfe7827a045be2022c561b3527ed60aafb8b` | 2,466 B | `scripts/btrfs_ro_mitigation_probe.sh` 的运行日志（NAS `/tmp` 原件同哈希） |
| `BTRFS_GENERATION_SEMANTICS_OUTPUT.json` | `6add2ee5ac7169bde761de43e915a24818da5c7b8f27d9706eab0add8b7f60fb` | 5,929 B | `scripts/btrfs_generation_semantics_probe.py` **第二次运行** stdout（文档 §2 表为首次运行数值；两次布尔结论一致，唯 #14 提交时机相反，见文档 §9.1 更正） |
| `BTRFS_GENERATION_BARRIER_OUTPUT.json` | `1038a03912e10d1414004b181b7a1eafff1ed480b0c3ca5e82c0d79bf66e9e84` | 1,732 B | `scripts/btrfs_generation_barrier_probe.py` 第二次运行 stdout |
| `REFLINK_COW_CORRECTED.log` | `973b05eb4a1ea4a00aa8a2f02043b35ee4bdc1173a98ab775e30fd4c99ed8c55` | 1,529 B | 复审 F2 后的纠正实验（reflink 双向 COW，哈希在对方修改前取得）；探针树 `/vol1/1000/fnm-reflink-cow-test-20261107` 运行后已清理（复核时无 `/vol1/1000/fnm-*` 目录与相关子卷） |
| `SCANNER_NATIVE_SMOKE_RECHECK.log` | `0c54fe9e301780c8f4c54732017885ea5c9f79499db9cca063f37af488c25cc2` | 882 B | Lead 对扫描器原生冒烟清理/服务状态的**只读独立复观察**（2026-11-07 11:43:54 CST），对应 `docs/SCANNER_NATIVE_SMOKE_RECHECK_20261107.md` 与复审 N5 |

## 生成脚本（复议 N5：日志可重放）

| 脚本 | SHA-256 |
| --- | --- |
| `scripts/btrfs_ro_rootcause_probe.sh` | `d5074d637d8390857059424e71f072eae33d86ccf63ddbc3a2e71791481383db` |
| `scripts/btrfs_ro_mitigation_probe.sh` | `c7e513b3d2167558e65d0f36eb4a010c4daed6a5d697669e8a0b736bab6115a2` |
| `scripts/write_probe_helper.py` | `a6ce5b775652ebcc32b711601f74400f8ff182b13dcb04567c41ebf6ec289462` |
| `scripts/btrfs_generation_semantics_probe.py` | `7f27a07a2ceeaec75c607425cc66bcef9f38b3af2492eca5e0d52245b8cd6a7d` |
| `scripts/btrfs_generation_barrier_probe.py` | `4795bba548fd9e54076075a5acbd17201721726013d669aea510dd1ba6e456e7` |
| `scripts/btrfs_reflink_cow_probe.sh`（对应 `REFLINK_COW_CORRECTED.log`；NAS `/tmp/fnm-reflink-fix.sh` 同哈希） | `08ee88bcc5331d72fae9fa21fc15a2859304a6fcb27370d62fef6a32316107a0` |

## 对应分析文档

- `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md`（根因 trimacl / M1–M4 缓解）
- `docs/BTRFS_GENERATION_SEMANTICS_20261107.md`（generation 语义与 barrier 补测）
- `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md` §7（设计 addendum，引用上述证据）

## 边界

- 原始日志/JSON 由脚本直接产生，本目录未编辑其内容；哈希可用 `Get-FileHash`/`sha256sum` 复核。
- NAS `/tmp` 同名原件可能被后续清理；本目录为持久副本。
- 运行时脚本会创建并清理 `/vol1` 一次性探针树；两次运行后均核验无残留（`probe_root_removed=true`、子卷 delete rc=0、无遗留挂载）。
- 本目录不构成任何门禁通过证据，也不是独立复审报告；独立复审见后续 `docs/*_REVIEW_*` 文件。
