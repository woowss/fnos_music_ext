# 扫描器原生冒烟：脚本外清理/服务状态的独立复观察（2026-11-07）

> **只读复观察记录，不是验收、不是授权、不改变任何门禁。** 目的：补上 `docs/SCANNER_NATIVE_SMOKE_REVIEW_20261107.md` 的 **N5**——冒烟文档 §5 的"脚本外复核"未随附原始命令/输出，且复审者按任务约束不访问设备，只能核验 result.json 内部一致性。
> 执行者：Lead（未参与冒烟脚本编写与运行）。窗口：2026-11-07 11:43:54（NAS 本地 CST，`date -Is` 输出 `2026-11-07T11:43:54+08:00`）。
> 关联：`docs/SCANNER_NATIVE_SMOKE_20261107.md`（`5bec1fbf…`）、`docs/SCANNER_NATIVE_SMOKE_REVIEW_20261107.md`（`ba9838b0…`）、`docs/evidence/20261107/SCANNER_NATIVE_SMOKE_RECHECK.log`。

## 1. 复观察命令（完整）

```bash
#!/bin/bash
set -u
echo "== time =="; date -Is
echo "== probe root absent? =="
ls -d /vol1/1000/fnm-scanner-native-20261107 2>&1
echo "== subvolume count + any probe subvolumes =="
btrfs subvolume list /vol1 2>/dev/null | wc -l
btrfs subvolume list /vol1 2>/dev/null | grep -E "fnm-scanner|scanner-native" | wc -l
echo "== /vol1/1000 top-level entries + names sha =="
ls -A /vol1/1000 2>/dev/null | sort | sha256sum
ls -A /vol1/1000 2>/dev/null | wc -l
echo "== /tmp staging residue =="
ls -d /tmp/fnm-scanner* /tmp/scanner-native* 2>&1 | head
echo "== service state =="
systemctl show fnmusic-ext.service -p ActiveState -p SubState -p MainPID -p NRestarts -p InvocationID --value
echo "== /vol1 mount =="
findmnt -no SOURCE,TARGET,OPTIONS /vol1
```

脚本仅执行 `ls`/`wc`/`sha256sum`/`btrfs subvolume list`/`systemctl show`/`findmnt`，无写操作。

## 2. 原始输出（全文）

```
== time ==
2026-11-07T11:43:54+08:00

== probe root absent? ==
ls: cannot access '/vol1/1000/fnm-scanner-native-20261107': No such file or directory

== subvolume count + any probe subvolumes ==
49
0

== /vol1/1000 top-level entries + names sha ==
c78985babe685f2c5851623258b1946f4e06a31f30bcabac6b360af951435f9c  -
47

== /tmp staging residue ==
ls: cannot access '/tmp/fnm-scanner*': No such file or directory
ls: cannot access '/tmp/scanner-native*': No such file or directory

== service state ==
1870197
0
active
running
53d953f3475047e484e9fdd955b71716

== /vol1 mount ==
/dev/mapper/trim_8f1e972a_e1ee_4afd_b791_e7e4fc2aa457-0 /vol1  rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/

== done ==
```

（`systemctl show` 的 5 行按 `-p` 顺序为：`ActiveState=active`、`SubState=running`、`MainPID=1870197`、`NRestarts=0`、`InvocationID=53d953f3…`。）

## 3. 与冒烟文档/复审的对照

| 项 | 冒烟文档声称 | 本次独立复观察 | 结论 |
| --- | --- | --- | --- |
| 探针根 | `ABSENT` | `No such file or directory` | 一致 |
| 有关子卷数 | 0（49→49 行） | 总数 49、匹配 0 | 一致 |
| `/vol1/1000` 顶层 | 47 项、names_sha 不变 | 47 项、`c78985ba…`（与更早证据记录的同名 sha 一致） | 一致 |
| `/tmp` 残留 | 无 | 无 | 一致 |
| 服务 | active、MainPID 1870197、NRestarts=0 | active/running、1870197、0 | 一致 |
| `/vol1` 挂载 | 未变 | `rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/` | 一致 |

## 4. 结论与边界

- 本次复观察**支持**冒烟文档 §5 的清理/服务声明，N5 的"缺少脚本外原始输出"已由本文件与原始日志补齐。
- 但**不**改变复审对 SC-1/SC-2 的判定：仍是**部分闭合**（单机单次、极小树、无并发/长扫描/M1 `-o ro`/G1 矩阵、非特权仅 `kurumi`/单属主组合）。
- 本次复观察为**某一时刻**快照；冒烟之后的任何设备变动都不在本文件覆盖范围内。
- 不改变 §0.1/§0.2、P7 A4/A5/A6 或任何门禁；不是验收/部署授权。

## 5. 证据文件

| 文件 | SHA-256 | 大小 | 说明 |
| --- | --- | --- | --- |
| `docs/evidence/20261107/SCANNER_NATIVE_SMOKE_RECHECK.log` | `0c54fe9e301780c8f4c54732017885ea5c9f79499db9cca063f37af488c25cc2` | 882 B | NAS `/tmp` 原始输出的持久副本（`tee` 产生，未编辑；与 NAS 原件同哈希） |
