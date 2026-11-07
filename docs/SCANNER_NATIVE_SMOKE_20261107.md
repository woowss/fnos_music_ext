# 扫描器候选原生冒烟证据（SC-1/SC-2，2026-11-07）

> **状态：一次设备原生冒烟证据，不是验收、不是部署授权，也不改变任何门禁。**
> 本证据只证明：在 `kotomi` 本机真实 Btrfs 上，候选扫描器以**非特权用户**、针对**真实只读快照/子树**执行了一次完整扫描路径，并用真实 `GET_SUBVOL_INFO`/`syncfs` 观测到预期结果与阴性拒绝。
> 为实际走到扫描路径，本次运行在 **`/tmp` 的一次性 harness 进程内存中** patch 了两个能力门；仓库文件里的门常量仍为 `False`（见 §4 GATE-1/GATE-2/HASH-1 与 §1 哈希）。产品仍未接入、未验收。

## 1. 环境、命令与证据绑定

| 项 | 值 |
| --- | --- |
| 设备 | `kotomi`，`6.18.18.c1168-trim`，`x86_64` |
| 发行版 | Debian GNU/Linux 12 (bookworm)；`/etc/fnos-release` 不存在（`None`），fnOS 版本未由本机文件提供 |
| Python | `3.11.2 (main, Oct  7 2026, 12:35:07) [GCC 12.2.0]` |
| btrfs-progs | `btrfs-progs v6.2` |
| `/vol1` 挂载 | `rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/`（`btrfs`） |
| 扫描用户 | `kurumi` uid=1000 gid=Users（非 root） |
| 运行窗口 | 2026-11-07T11:14:03+0800 → 2026-11-07T11:14:04+0800 |
| 结果 | **PASS**（断言 24/24，场景 6 个） |

**证据哈希绑定：**

| 对象 | SHA-256 | 说明 |
| --- | --- | --- |
| `scripts/scanner_native_smoke_probe.py`（本次运行副本） | `fd59c730f84ed99b7c932a2f90ad2bb8447241d55f804751783c6b51cb1d7c73` | 与仓库文件同哈希（见 §7） |
| `scripts/verify_position_file_evidence.py`（被扫候选） | `341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0` | 与工作区候选同哈希（§7） |
| harness 源码 | `46fa240487f7842e22ac6f1a9b157e01c2997a7c1ee2b2e530ce202bbdda4788` | 由探针脚本生成，仅存在于 /tmp staging |
| 原始输出 `result.json` | `7fec8e605eb3cd5115d53b1862ab9aad551fc9b8cb69f3b56a3a5ed98124cd98` | 44319 字节；stderr 0 字节 |

**运行命令（root，仅写一次性探针树与 /tmp）：**

```bash
# 本地 → NAS /tmp（只读副本；仓库文件不改）
scp scripts/scanner_native_smoke_probe.py scripts/verify_position_file_evidence.py kotomi-root:/tmp/fnm-scanner-impl2/

# NAS root
cd /tmp/fnm-scanner-impl2
python3 -B scanner_native_smoke_probe.py \
  --verifier /tmp/fnm-scanner-impl2/verify_position_file_evidence.py \
  --probe-root /vol1/1000/fnm-scanner-native-20261107 \
  > result.json 2> result.stderr
sha256sum result.json
```

**探针树（运行中被创建，结束后已删除）：**

```text
/vol1/1000/fnm-scanner-native-20261107/               一次性探针根（0700/0755 root）
  src/                         子卷（chown kurumi:Users）
    lib/track.flac             kurumi 创建（O_NOATIME 属主）
    sibling.flac               与 track 同字节，位于子树之外
  snap/                        src 的真实只读快照（root 创建，`btrfs subvolume snapshot -r`）
  src2/                        子卷（root 拥有）
    lib2/                      chown kurumi:Users
      rootfile.bin             root 拥有的文件（O_NOATIME 拒绝阴性）
  snap2/                       src2 的真实只读快照
```

**方法（harness）：**`/tmp` staging 中复制候选模块，`importlib` 加载后仅在内存执行
`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED = True`、`_POINT_IN_TIME_SCAN_VALIDATED = True`；
`_INDEPENDENT_COPY_VIEW_VALIDATED` 保持 `False`；随后直接调用 `scan_tree()` 并输出 GET_SUBVOL_INFO
identity、扫描 JSON、真实 barrier generation、文件 stat 前后值、进程身份/capabilities 与退出码。
harness 不写任何文件；模块文件哈希运行前后一致（HASH-1）。

## 2. 结果总表

| 场景 | root | profile | 结果 | unknown_reason | read_mode | root_scope | barrier(pre/post/机制) | positions | exit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_subvolume_root | `/vol1/1000/fnm-scanner-native-20261107/snap` | btrfs-ro-snapshot | complete | None | o_noatime | subvolume_root | done/done syncfs | [{'match_count': 2, 'position': 1, 'status': 'ambiguous'}] | 0 |
| B_subtree | `/vol1/1000/fnm-scanner-native-20261107/snap/lib` | btrfs-ro-snapshot | complete | None | o_noatime | subtree | done/done syncfs | [{'match_count': 1, 'position': 1, 'status': 'unique_match'}] | 0 |
| C_writable_subvolume | `/vol1/1000/fnm-scanner-native-20261107/src` | btrfs-ro-snapshot | unknown | ro_semantics_unavailable | o_noatime | unknown | not_attempted/not_attempted None | [{'position': 1, 'status': 'unknown'}] | 2 |
| D_root_owned_file | `/vol1/1000/fnm-scanner-native-20261107/snap2/lib2` | btrfs-ro-snapshot | unknown | noatime_permission_denied | o_noatime_denied | subtree | done/not_attempted syncfs | [{'position': 1, 'status': 'unknown'}] | 2 |
| E_independent_copy | `/vol1/1000/fnm-scanner-native-20261107/snap/lib` | independent-copy | unknown | ro_semantics_unavailable | o_noatime | unknown | done/done syncfs | [{'position': 1, 'status': 'unknown'}] | 2 |
| F_cli_default | `/vol1/1000/fnm-scanner-native-20261107/snap` | 默认 | unknown（legacy） | null | — | — | — | `[{"position":1,"status":"unknown"}]` | 2 |

逐场景 stdout SHA-256：

| 场景 | exit | stdout SHA-256 |
| --- | --- | --- |
| A_subvolume_root | 0 | `9b5c7df2cc7257a1e952ed8da23b5d60fea3548cbba135ee61810a42aa486e79` |
| B_subtree | 0 | `5aa70a14e7e6667b3f6cfdc4d863e63e672d00353a25e4c107ad7b4082e304f1` |
| C_writable_subvolume | 2 | `68423c84dfbc2e6ed1273929fe1c5ab78aa465d3c0cab66656275d002f2e3513` |
| D_root_owned_file | 2 | `cb57d1c175913ea5b88b71aba5dbb23a406fa669d541358061f69128223a2ffc` |
| E_independent_copy | 2 | `7850f99c2ad116e7b5d59c9378717d6e02bec1e45a5704b8271c97cbeaae1c42` |
| F_cli_default | 2 | `2838eddc6c582bd9a9f91044632801fe400148bb920bf20948d789ca07e0dd2f` |

## 3. 逐场景证据要点

### 3.1 A：真实只读快照子卷根（阳性，complete=true）

真实 `GET_SUBVOL_INFO` 在非特权 kurumi 下识别到快照：treeid=712、generation=1692821、flags=1（`BTRFS_ROOT_SUBVOL_RDONLY` bit0）、UUID/parent UUID 非零，`_require_readonly_snapshot` 接受；`root_scope=subvolume_root`（快照根 inode 256）。扫描 `read_mode=o_noatime`，真实 `syncfs` barrier 前后两次读取的 generation 全部等于 1692821，position 1 匹配到 `lib/track.flac` 与 `sibling.flac` 两个同字节文件（ambiguous/2），`entries_seen=3`；被观测文件的 atime/mtime/ctime/ino/size 扫描前后不变。

> **平台观测（未审计、未定位）：** 被观测文件（及 `sibling.flac`）由 kurumi 在 fnOS `trimacl` 子卷内创建，`os.stat` 读到 `mode=0`（`uid=1000`）；同一探针内由 root 显式 `chmod 0644` 的 `rootfile.bin` 读到 `mode=420`。只读旁证：`/vol1/1000` 自身（root 与 kurumi 两侧 `stat` 一致）也是 `mode=0`，其既有子目录为 `mode=700`。因此 `mode=0` 是 **fnOS `trimacl`/ACL 路径上的原始表示法观测**，不是本条 harness 的记录错误；本次**未审计**该表示法，也未据此推断访问控制语义。扫描器 O_NOATIME 的属主判定使用 `uid`（kurumi=1000，与文件 `uid` 相同），不依赖 `mode` 位，故不影响 A/B 结论；该平台差异只记录、不外推。

```json
{
 "gates_after_patch": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": true
 },
 "gates_original": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": false
 },
 "observed_file": {
  "after": {
   "atime_ns": 1794021244023574152,
   "ctime_ns": 1794021244023574152,
   "gid": 1001,
   "ino": 258,
   "mode": 0,
   "mtime_ns": 1794021244023574152,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac",
   "size": 34,
   "uid": 1000
  },
  "before": {
   "atime_ns": 1794021244023574152,
   "ctime_ns": 1794021244023574152,
   "gid": 1001,
   "ino": 258,
   "mode": 0,
   "mtime_ns": 1794021244023574152,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac",
   "size": 34,
   "uid": 1000
  },
  "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"
 },
 "process": {
  "caps": {
   "CapEff": "0000000000000000",
   "CapPrm": "0000000000000000",
   "Gid": "1001\t1001\t1001\t1001",
   "Uid": "1000\t1000\t1000\t1000"
  },
  "euid": 1000
 },
 "profile": "btrfs-ro-snapshot",
 "report": {
  "checks": {
   "content_hash": "performed",
   "generation_bracket": "stable",
   "identity": "ok",
   "ro_semantics": "metadata_ro_bit_only",
   "scanner_unprivileged": "ok"
  },
  "complete": true,
  "evidence_profile": "btrfs-ro-snapshot",
  "limits": {
   "entries_seen": 3,
   "limit_exceeded": false,
   "max_entries": 100000
  },
  "positions": [
   {
    "match_count": 2,
    "position": 1,
    "status": "ambiguous"
   }
  ],
  "read_mode": "o_noatime",
  "root_scope": "subvolume_root",
  "scope": "read_only_test_tree",
  "sync_barrier": {
   "generation_before_post_sync": 1692821,
   "generation_before_pre_sync": 1692821,
   "generation_end": 1692821,
   "generation_start": 1692821,
   "mechanism": "syncfs",
   "post": "done",
   "pre": "done"
  },
  "unknown_reason": null
 },
 "returncode": 0,
 "root": "/vol1/1000/fnm-scanner-native-20261107/snap",
 "stdout_sha256": "9b5c7df2cc7257a1e952ed8da23b5d60fea3548cbba135ee61810a42aa486e79",
 "view": {
  "flags": 1,
  "generation": 1692821,
  "parent_uuid_nonzero": true,
  "readonly_snapshot_accepted": true,
  "ro_bit0": true,
  "treeid": 712,
  "uuid_nonzero": true
 }
}
```

### 3.2 B：快照内子树根（阳性，complete=true）

root 改为快照内 `snap/lib`（非子卷根）：仍解析到同一 treeid=712 且被接受，`root_scope=subtree`；`entries_seen=1`（只遍历 lib 内 1 个文件），position 1 变为 `unique_match/1` —— 证明 root 外的同字节 `snap/sibling.flac` 未被计入；barrier 与 generation 同样稳定（1692821），文件 stat 前后不变。

```json
{
 "gates_after_patch": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": true
 },
 "gates_original": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": false
 },
 "observed_file": {
  "after": {
   "atime_ns": 1794021244023574152,
   "ctime_ns": 1794021244023574152,
   "gid": 1001,
   "ino": 258,
   "mode": 0,
   "mtime_ns": 1794021244023574152,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac",
   "size": 34,
   "uid": 1000
  },
  "before": {
   "atime_ns": 1794021244023574152,
   "ctime_ns": 1794021244023574152,
   "gid": 1001,
   "ino": 258,
   "mode": 0,
   "mtime_ns": 1794021244023574152,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac",
   "size": 34,
   "uid": 1000
  },
  "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"
 },
 "process": {
  "caps": {
   "CapEff": "0000000000000000",
   "CapPrm": "0000000000000000",
   "Gid": "1001\t1001\t1001\t1001",
   "Uid": "1000\t1000\t1000\t1000"
  },
  "euid": 1000
 },
 "profile": "btrfs-ro-snapshot",
 "report": {
  "checks": {
   "content_hash": "performed",
   "generation_bracket": "stable",
   "identity": "ok",
   "ro_semantics": "metadata_ro_bit_only",
   "scanner_unprivileged": "ok"
  },
  "complete": true,
  "evidence_profile": "btrfs-ro-snapshot",
  "limits": {
   "entries_seen": 1,
   "limit_exceeded": false,
   "max_entries": 100000
  },
  "positions": [
   {
    "match_count": 1,
    "position": 1,
    "status": "unique_match"
   }
  ],
  "read_mode": "o_noatime",
  "root_scope": "subtree",
  "scope": "read_only_test_tree",
  "sync_barrier": {
   "generation_before_post_sync": 1692821,
   "generation_before_pre_sync": 1692821,
   "generation_end": 1692821,
   "generation_start": 1692821,
   "mechanism": "syncfs",
   "post": "done",
   "pre": "done"
  },
  "unknown_reason": null
 },
 "returncode": 0,
 "root": "/vol1/1000/fnm-scanner-native-20261107/snap/lib",
 "stdout_sha256": "5aa70a14e7e6667b3f6cfdc4d863e63e672d00353a25e4c107ad7b4082e304f1",
 "view": {
  "flags": 1,
  "generation": 1692821,
  "parent_uuid_nonzero": true,
  "readonly_snapshot_accepted": true,
  "ro_bit0": true,
  "treeid": 712,
  "uuid_nonzero": true
 }
}
```

### 3.3 C：可写子卷根（阴性，必须拒绝）

以可写子卷 `src`（treeid=711、flags=0、无 RO 位、parent UUID 为空）为 root：`_require_readonly_snapshot` 拒绝，扫描返回 `complete=false`、`unknown_reason=ro_semantics_unavailable`，barrier 未尝试（`pre/post=not_attempted`），exit 2。

```json
{
 "gates_after_patch": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": true
 },
 "gates_original": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": false
 },
 "observed_file": {
  "after": null,
  "before": null,
  "path": null
 },
 "process": {
  "caps": {
   "CapEff": "0000000000000000",
   "CapPrm": "0000000000000000",
   "Gid": "1001\t1001\t1001\t1001",
   "Uid": "1000\t1000\t1000\t1000"
  },
  "euid": 1000
 },
 "profile": "btrfs-ro-snapshot",
 "report": {
  "checks": {
   "content_hash": "not_attempted",
   "generation_bracket": "unknown",
   "identity": "unknown",
   "ro_semantics": "unknown",
   "scanner_unprivileged": "ok"
  },
  "complete": false,
  "evidence_profile": "btrfs-ro-snapshot",
  "limits": {
   "entries_seen": 0,
   "limit_exceeded": false,
   "max_entries": 100000
  },
  "positions": [
   {
    "position": 1,
    "status": "unknown"
   }
  ],
  "read_mode": "o_noatime",
  "root_scope": "unknown",
  "scope": "read_only_test_tree",
  "sync_barrier": {
   "generation_before_post_sync": null,
   "generation_before_pre_sync": null,
   "generation_end": null,
   "generation_start": null,
   "mechanism": null,
   "post": "not_attempted",
   "pre": "not_attempted"
  },
  "unknown_reason": "ro_semantics_unavailable"
 },
 "returncode": 2,
 "root": "/vol1/1000/fnm-scanner-native-20261107/src",
 "stdout_sha256": "68423c84dfbc2e6ed1273929fe1c5ab78aa465d3c0cab66656275d002f2e3513",
 "view": {
  "flags": 0,
  "generation": 1692821,
  "parent_uuid_nonzero": false,
  "readonly_snapshot_accepted": false,
  "ro_bit0": false,
  "treeid": 711,
  "uuid_nonzero": true
 }
}
```

### 3.4 D：属主为 root 的文件（阴性，O_NOATIME 必须 fail-closed）

root 为 `snap2/lib2`（所在子卷为真实 RO 快照，treeid=714、flags=1），其中一个文件 `rootfile.bin` 属主为 root；以 kurumi 扫描时 open 带 `O_NOATIME` 被内核拒绝（EPERM），扫描返回 `complete=false`、`unknown_reason=noatime_permission_denied`、`read_mode=o_noatime_denied`，exit 2，**不会静默回退**。

```json
{
 "gates_after_patch": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": true
 },
 "gates_original": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": false
 },
 "observed_file": {
  "after": {
   "atime_ns": 1794021244403568532,
   "ctime_ns": 1794021244406310250,
   "gid": 0,
   "ino": 258,
   "mode": 420,
   "mtime_ns": 1794021244406310250,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin",
   "size": 34,
   "uid": 0
  },
  "before": {
   "atime_ns": 1794021244403568532,
   "ctime_ns": 1794021244406310250,
   "gid": 0,
   "ino": 258,
   "mode": 420,
   "mtime_ns": 1794021244406310250,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin",
   "size": 34,
   "uid": 0
  },
  "path": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin"
 },
 "process": {
  "caps": {
   "CapEff": "0000000000000000",
   "CapPrm": "0000000000000000",
   "Gid": "1001\t1001\t1001\t1001",
   "Uid": "1000\t1000\t1000\t1000"
  },
  "euid": 1000
 },
 "profile": "btrfs-ro-snapshot",
 "report": {
  "checks": {
   "content_hash": "attempted",
   "generation_bracket": "unknown",
   "identity": "unknown",
   "ro_semantics": "metadata_ro_bit_only",
   "scanner_unprivileged": "ok"
  },
  "complete": false,
  "evidence_profile": "btrfs-ro-snapshot",
  "limits": {
   "entries_seen": 1,
   "limit_exceeded": false,
   "max_entries": 100000
  },
  "positions": [
   {
    "position": 1,
    "status": "unknown"
   }
  ],
  "read_mode": "o_noatime_denied",
  "root_scope": "subtree",
  "scope": "read_only_test_tree",
  "sync_barrier": {
   "generation_before_post_sync": null,
   "generation_before_pre_sync": 1692822,
   "generation_end": null,
   "generation_start": 1692822,
   "mechanism": "syncfs",
   "post": "not_attempted",
   "pre": "done"
  },
  "unknown_reason": "noatime_permission_denied"
 },
 "returncode": 2,
 "root": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2",
 "stdout_sha256": "cb57d1c175913ea5b88b71aba5dbb23a406fa669d541358061f69128223a2ffc",
 "view": {
  "flags": 1,
  "generation": 1692822,
  "parent_uuid_nonzero": true,
  "readonly_snapshot_accepted": true,
  "ro_bit0": true,
  "treeid": 714,
  "uuid_nonzero": true
 }
}
```

### 3.5 E：independent-copy 视图（阳性检查 + 永不 complete）

同一子树以 `--evidence-profile independent-copy` 方式（在 harness 中作为参数）扫描：不调用 Btrfs RO 语义、`checks.ro_semantics=unavailable`、`generation_bracket=not_applicable`；仍执行真实 `syncfs` barrier（pre/post done）与真实 O_NOATIME 内容哈希（`content_hash=performed`），但整体 `complete=false`、`unknown_reason=ro_semantics_unavailable`，exit 2。

```json
{
 "gates_after_patch": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": true
 },
 "gates_original": {
  "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false,
  "_INDEPENDENT_COPY_VIEW_VALIDATED": false,
  "_POINT_IN_TIME_SCAN_VALIDATED": false
 },
 "observed_file": {
  "after": {
   "atime_ns": 1794021244023574152,
   "ctime_ns": 1794021244023574152,
   "gid": 1001,
   "ino": 258,
   "mode": 0,
   "mtime_ns": 1794021244023574152,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac",
   "size": 34,
   "uid": 1000
  },
  "before": {
   "atime_ns": 1794021244023574152,
   "ctime_ns": 1794021244023574152,
   "gid": 1001,
   "ino": 258,
   "mode": 0,
   "mtime_ns": 1794021244023574152,
   "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac",
   "size": 34,
   "uid": 1000
  },
  "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"
 },
 "process": {
  "caps": {
   "CapEff": "0000000000000000",
   "CapPrm": "0000000000000000",
   "Gid": "1001\t1001\t1001\t1001",
   "Uid": "1000\t1000\t1000\t1000"
  },
  "euid": 1000
 },
 "profile": "independent-copy",
 "report": {
  "checks": {
   "content_hash": "performed",
   "generation_bracket": "not_applicable",
   "identity": "ok",
   "ro_semantics": "unavailable",
   "scanner_unprivileged": "ok"
  },
  "complete": false,
  "evidence_profile": "independent-copy",
  "limits": {
   "entries_seen": 1,
   "limit_exceeded": false,
   "max_entries": 100000
  },
  "positions": [
   {
    "position": 1,
    "status": "unknown"
   }
  ],
  "read_mode": "o_noatime",
  "root_scope": "unknown",
  "scope": "read_only_test_tree",
  "sync_barrier": {
   "generation_before_post_sync": null,
   "generation_before_pre_sync": null,
   "generation_end": null,
   "generation_start": null,
   "mechanism": "syncfs",
   "post": "done",
   "pre": "done"
  },
  "unknown_reason": "ro_semantics_unavailable"
 },
 "returncode": 2,
 "root": "/vol1/1000/fnm-scanner-native-20261107/snap/lib",
 "stdout_sha256": "7850f99c2ad116e7b5d59c9378717d6e02bec1e45a5704b8271c97cbeaae1c42",
 "view": {
  "flags": 1,
  "generation": 1692821,
  "parent_uuid_nonzero": true,
  "readonly_snapshot_accepted": true,
  "ro_bit0": true,
  "treeid": 712,
  "uuid_nonzero": true
 }
}
```

### 3.6 F：默认 CLI（产品门禁未变）

以 kurumi 运行 `/tmp` 中的候选 CLI（默认 profile，未 patch 任何门）：输出仍为 legacy unknown，
exit=2，证明默认产品行为在本机原生路径上未改变：

```json
{"scope":"read_only_test_tree","complete":false,"positions":[{"position":1,"status":"unknown"}]}
```

## 4. 断言清单（24/24 PASS）

| ID | 结果 | 说明 |
| --- | --- | --- |
| GATE-1 | PASS | harness loaded module with all three gates False: {'_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED': False, '_INDEPENDENT_COPY_VIEW_VALIDATED': False, '_POINT_IN_TIME_SCAN_VALIDATED': False} |
| GATE-2 | PASS | only the two capability gates were patched in memory: {'_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED': True, '_INDEPENDENT_COPY_VIEW_VALIDATED': False, '_POINT_IN_TIME_SCAN_VALIDATED': True} |
| PRIV-1 | PASS | harness ran as euid=1000 CapEff=0000000000000000 CapPrm=0000000000000000 (expected euid=1000, all caps 0) |
| VIEW-A1 | PASS | snap GET_SUBVOL_INFO treeid=712 gen=1692821 flags=1 ro_bit0=True uuid=True parent=True |
| VIEW-B1 | PASS | subtree root resolves to the same snapshot treeid 712 (accepted=True) |
| A1 | PASS | snapshot subvolume scan complete=True unknown_reason=None |
| A2 | PASS | read_mode=o_noatime |
| A3 | PASS | root_scope=subvolume_root |
| A4 | PASS | real barrier pre=done post=done mechanism=syncfs |
| A5 | PASS | generations before_pre=1692821 start=1692821 before_post=1692821 end=1692821 |
| A6 | PASS | whole snapshot scan sees lib + sibling: status=ambiguous match_count=2 entries_seen=3 |
| A7 | PASS | observed file stats unchanged across the scan (atime/mtime/ctime/ino/size) |
| B1 | PASS | subtree scan complete=True unknown_reason=None |
| B2 | PASS | read_mode=o_noatime |
| B3 | PASS | root_scope=subtree |
| B4 | PASS | subtree scan excludes the same-bytes sibling: status=unique_match match_count=1 |
| B5 | PASS | subtree entries_seen=1 (lib contains only track.flac) |
| B6 | PASS | subtree barrier pre=done post=done start=1692821 end=1692821 |
| B7 | PASS | observed file stats unchanged across the subtree scan |
| C1 | PASS | writable subvolume rejected: complete=False reason=ro_semantics_unavailable readonly_accepted=False |
| D1 | PASS | root-owned file refused O_NOATIME: complete=False reason=noatime_permission_denied read_mode=o_noatime_denied |
| E1 | PASS | independent-copy scans with real barrier/hash but never claims complete: complete=False reason=ro_semantics_unavailable ro=unavailable bracket=not_applicable hash=performed barrier=done/done |
| F1 | PASS | default CLI (product gates False) returned legacy unknown JSON and exit 2: 2 |
| HASH-1 | PASS | verifier module file was not modified by the in-memory harness: 341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0 |

## 5. 清理与设备只读核验

探针脚本在 `finally` 中执行清理并自校验；以下为脚本内记录与**脚本之外**另行执行的只读复核。

| 检查 | 脚本内记录 | 脚本外复核 |
| --- | --- | --- |
| 探针根 `/vol1/1000/fnm-scanner-native-20261107` | `probe_root_removed=True` | `ABSENT`（`test -e`） |
| 4 个子卷删除 | `{'/vol1/1000/fnm-scanner-native-20261107/snap': True, '/vol1/1000/fnm-scanner-native-20261107/snap2': True, '/vol1/1000/fnm-scanner-native-20261107/src': True, '/vol1/1000/fnm-scanner-native-20261107/src2': True}` | — |
| `btrfs subvolume list /vol1` 相关行 | `related_subvolumes_after=[]`（`subvolume_list_clean=True`） | `grep -c fnm-scanner-native` = `0`（两次独立执行） |
| 子卷总数 | baseline=49 / after=49（unchanged=True） | 复核 `btrfs subvolume list /vol1` 共 49 行（38 个 `@snapshot` + 11 个用户子卷，无本探针） |
| `/vol1/1000` 顶层条目 | count=47、names_sha256=`bbc6f1dc8fd5738b6b223629d3241e92e1a76f5253c6bd3e0172a59ec778ade5`（unchanged=True） | `len(os.listdir('/vol1/1000'))` = 47 |
| `/tmp` staging | `staging_removed=True` | `ls -d /tmp/fnm-scanner-native-smoke-*` = NONE |
| 服务 `fnmusic-ext` | active、MainPID=1870197、NRestarts=0（unchanged=True） | `active`、MainPID=1870197、NRestarts=0 |
| `/vol1` 挂载 | `rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/`（unchanged=True） | 相同 `rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/` |

**运行历史（诚实记录）：** 2026-11-07 11:12:47 的首跑同样 PASS 且清理成功，但暴露了探针脚本自身的缺陷：baseline 子卷列表在采集时被默认 1200 字符截断（记录 26 行 vs 实际 49 行），导致 `subvolume_count.unchanged=false`。该问题与设备无关；已修正为完整采集并重跑，本文证据为 **11:14:03–11:14:04** 的最终运行（脚本哈希 `fd59c730f84ed99b7c932a2f90ad2bb8447241d55f804751783c6b51cb1d7c73`）。

## 6. 边界与仍未验证项（不得外推）

- **harness patch 仅本次、仅内存**：两个能力门只在本次 `/tmp` harness 进程内被置 True；仓库 `scripts/verify_position_file_evidence.py` 中 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED`、`_POINT_IN_TIME_SCAN_VALIDATED` 仍为 `False`（本地复核见 §7），`_INDEPENDENT_COPY_VIEW_VALIDATED` 亦为 `False`。本证据**不改变任何产品门禁、不接入路由/写路径、不构成验收**。
- **仅本机单次冒烟**：单机（`kotomi`，内核 `6.18.18.c1168-trim`，`trimacl` 挂载）、单次运行、小文件（34 B）与 3 个条目的极小树；未测长扫描/大库/100k 上限行为/并发 writer/多子卷嵌套/快照删除开销。
- **未覆盖的实现边界**：未验证 M1 私有 namespace `-o ro` 挂载下的 `syncfs` 行为；未验证 `trimacl` 绕过 RO 写的检测（本树 COW 后未向快照写入）；`O_NOATIME` 仅验证“属主可用 / 非属主被拒”两种情形；未覆盖 scanner UID 与文件属主不同的其它组合。
- **平台表示法观测**：探针文件在 `trimacl` 子卷内 `mode` 读作 `0`（`uid=1000`；`/vol1/1000` 自身同样为 `0`），本证据只记录该原始观测，**未审计/未定位** fnOS ACL 表示法与访问控制语义，也不据此改变 O_NOATIME 或权限结论（O_NOATIME 属主判定用 `uid`）。该现象不能外推到其它平台/挂载组合。
- **generation 语义仍是单次观测**：本证据只记录“本次扫描期间快照 generation 稳定（1692821）”与 barrier 后无变化，不能替代设计 §4.3/G1 的全写入路径 generation 矩阵；产品门禁仍需单独的原生验证与独立复审。
- **SC-1/SC-2 范围**：本证据支持“真实内核上非特权进程可对真实 RO 快照/子树执行完整扫描路径（含 O_NOATIME、syncfs barrier、identity bracket）并 fail-closed 处理两类阴性”的冒烟结论；**不能**替代系统性失败注入（I1–I20）、独立复审、G8 端到端与任何验收。
- 不把本机结论外推到其它 fnOS 版本/内核/上游 Btrfs/挂载组合。

## 7. 复现与仓库状态

- 仓库候选：`scripts/verify_position_file_evidence.py` = `341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0`；门常量复核（本地）：`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、`_POINT_IN_TIME_SCAN_VALIDATED=False`、`_INDEPENDENT_COPY_VIEW_VALIDATED=False`。
- 本探针脚本：`scripts/scanner_native_smoke_probe.py` = `fd59c730f84ed99b7c932a2f90ad2bb8447241d55f804751783c6b51cb1d7c73`。
- 复现：按 §1 命令把两个文件复制到 NAS `/tmp`，以 root 运行；脚本拒绝已存在的探针根/非预期 fstype/非 root 执行，并始终尝试自清理与清理核验；产物只有 stdout JSON。
- 运行结束后 NAS `/tmp/fnm-scanner-impl2/` 与探针 staging 已清理（见 §5）；仓库只新增本文件与探针脚本（均为未跟踪新文件）。

## 附录 A：原始输出 JSON（`result.json`，按原样嵌入）

> 下列代码块内容即原始输出行（JSON；末尾换行后与 `7fec8e605eb3cd5115d53b1862ab9aad551fc9b8cb69f3b56a3a5ed98124cd98` 的 SHA-256 对应）。

```json
{"baseline": {"mount": {"fstype": "btrfs", "options": "rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/", "target": "/vol1"}, "service": {"is_active": "active", "main_pid": "1870197", "n_restarts": "0"}, "subvolume_list": {"line_count": 49, "lines_sha256": "0025e31380a885d35c4da3f82a5e4fb6b9e2bdb4ef96236439cb96e4bc5e1317", "returncode": 0, "sample": ["ID 256 gen 1692819 top level 5 path 1000", "ID 257 gen 1691512 top level 5 path 1001", "ID 258 gen 1691512 top level 5 path 1002", "...", "ID 670 gen 1677328 top level 285 path @snapshot/1008/UTC+08-2026.11.02-00.00.00", "ID 671 gen 1677329 top level 407 path @snapshot/1009/UTC+08-2026.11.02-00.00.00", "ID 672 gen 1677330 top level 420 path @snapshot/1010/UTC+08-2026.11.02-00.00.00"], "stderr": ""}, "top_level": {"count": 47, "names_sha256": "bbc6f1dc8fd5738b6b223629d3241e92e1a76f5253c6bd3e0172a59ec778ade5"}}, "checks": [{"detail": "harness loaded module with all three gates False: {'_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED': False, '_INDEPENDENT_COPY_VIEW_VALIDATED': False, '_POINT_IN_TIME_SCAN_VALIDATED': False}", "id": "GATE-1", "pass": true}, {"detail": "only the two capability gates were patched in memory: {'_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED': True, '_INDEPENDENT_COPY_VIEW_VALIDATED': False, '_POINT_IN_TIME_SCAN_VALIDATED': True}", "id": "GATE-2", "pass": true}, {"detail": "harness ran as euid=1000 CapEff=0000000000000000 CapPrm=0000000000000000 (expected euid=1000, all caps 0)", "id": "PRIV-1", "pass": true}, {"detail": "snap GET_SUBVOL_INFO treeid=712 gen=1692821 flags=1 ro_bit0=True uuid=True parent=True", "id": "VIEW-A1", "pass": true}, {"detail": "subtree root resolves to the same snapshot treeid 712 (accepted=True)", "id": "VIEW-B1", "pass": true}, {"detail": "snapshot subvolume scan complete=True unknown_reason=None", "id": "A1", "pass": true}, {"detail": "read_mode=o_noatime", "id": "A2", "pass": true}, {"detail": "root_scope=subvolume_root", "id": "A3", "pass": true}, {"detail": "real barrier pre=done post=done mechanism=syncfs", "id": "A4", "pass": true}, {"detail": "generations before_pre=1692821 start=1692821 before_post=1692821 end=1692821", "id": "A5", "pass": true}, {"detail": "whole snapshot scan sees lib + sibling: status=ambiguous match_count=2 entries_seen=3", "id": "A6", "pass": true}, {"detail": "observed file stats unchanged across the scan (atime/mtime/ctime/ino/size)", "id": "A7", "pass": true}, {"detail": "subtree scan complete=True unknown_reason=None", "id": "B1", "pass": true}, {"detail": "read_mode=o_noatime", "id": "B2", "pass": true}, {"detail": "root_scope=subtree", "id": "B3", "pass": true}, {"detail": "subtree scan excludes the same-bytes sibling: status=unique_match match_count=1", "id": "B4", "pass": true}, {"detail": "subtree entries_seen=1 (lib contains only track.flac)", "id": "B5", "pass": true}, {"detail": "subtree barrier pre=done post=done start=1692821 end=1692821", "id": "B6", "pass": true}, {"detail": "observed file stats unchanged across the subtree scan", "id": "B7", "pass": true}, {"detail": "writable subvolume rejected: complete=False reason=ro_semantics_unavailable readonly_accepted=False", "id": "C1", "pass": true}, {"detail": "root-owned file refused O_NOATIME: complete=False reason=noatime_permission_denied read_mode=o_noatime_denied", "id": "D1", "pass": true}, {"detail": "independent-copy scans with real barrier/hash but never claims complete: complete=False reason=ro_semantics_unavailable ro=unavailable bracket=not_applicable hash=performed barrier=done/done", "id": "E1", "pass": true}, {"detail": "default CLI (product gates False) returned legacy unknown JSON and exit 2: 2", "id": "F1", "pass": true}, {"detail": "verifier module file was not modified by the in-memory harness: 341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "id": "HASH-1", "pass": true}], "cleanup": {"mount": {"fstype": "btrfs", "options": "rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/", "target": "/vol1"}, "mount_unchanged": true, "ok": true, "probe_root_removed": true, "related_subvolumes_after": [], "service": {"is_active": "active", "main_pid": "1870197", "n_restarts": "0"}, "service_unchanged": true, "staging_removed": true, "subvolume_count": {"after": 49, "baseline": 49, "unchanged": true}, "subvolume_list_clean": true, "subvolumes": {"/vol1/1000/fnm-scanner-native-20261107/snap": true, "/vol1/1000/fnm-scanner-native-20261107/snap2": true, "/vol1/1000/fnm-scanner-native-20261107/src": true, "/vol1/1000/fnm-scanner-native-20261107/src2": true}, "top_level": {"count": 47, "names_sha256": "bbc6f1dc8fd5738b6b223629d3241e92e1a76f5253c6bd3e0172a59ec778ade5", "unchanged": true}}, "commands": [{"argv": ["/usr/bin/btrfs", "subvolume", "show", "/vol1/1000/fnm-scanner-native-20261107/src"], "returncode": 0, "seconds": 0.005, "stderr": "", "stdout": "1000/fnm-scanner-native-20261107/src\n\tName: \t\t\tsrc\n\tUUID: \t\t\t03de6ac2-8514-9a43-a280-5226eccf6664\n\tParent UUID: \t\t-\n\tReceived UUID: \t\t-\n\tCreation time: \t\t2026-11-07 11:14:04 +0800\n\tSubvolume ID: \t\t711\n\tGeneration: \t\t1692821\n\tGen at creation: \t1692820\n\tParent ID: \t\t256\n\tTop level ID: \t\t256\n\tFlags: \t\t\t-\n\tSend transid: \t\t0\n\tSend time: \t\t2026-11-07 11:14:04 +0800\n\tReceive transid: \t0\n\tReceive time: \t\t-\n\tSnapshot(s):\n\t\t\t\t1000/fnm-scanner-native-20261107/snap\n\tQuota group:\t\t0/711\n\t  Limit referenced:\t-\n\t  Limit exclusive:\t-\n\t  Usage referenced:\t0.00B\n\t  Usage exclusive:\t16.00KiB"}, {"argv": ["/usr/bin/btrfs", "subvolume", "show", "/vol1/1000/fnm-scanner-native-20261107/snap"], "returncode": 0, "seconds": 0.005, "stderr": "", "stdout": "1000/fnm-scanner-native-20261107/snap\n\tName: \t\t\tsnap\n\tUUID: \t\t\t65da3ca3-73fc-d84e-b8b5-eec908685711\n\tParent UUID: \t\t03de6ac2-8514-9a43-a280-5226eccf6664\n\tReceived UUID: \t\t-\n\tCreation time: \t\t2026-11-07 11:14:04 +0800\n\tSubvolume ID: \t\t712\n\tGeneration: \t\t1692821\n\tGen at creation: \t1692821\n\tParent ID: \t\t256\n\tTop level ID: \t\t256\n\tFlags: \t\t\treadonly\n\tSend transid: \t\t0\n\tSend time: \t\t2026-11-07 11:14:04 +0800\n\tReceive transid: \t0\n\tReceive time: \t\t-\n\tSnapshot(s):\n\tQuota group:\t\t0/712\n\t  Limit referenced:\t-\n\t  Limit exclusive:\t-\n\t  Usage referenced:\t0.00B\n\t  Usage exclusive:\t16.00KiB"}, {"argv": ["/usr/bin/btrfs", "subvolume", "show", "/vol1/1000/fnm-scanner-native-20261107/src2"], "returncode": 0, "seconds": 0.005, "stderr": "", "stdout": "1000/fnm-scanner-native-20261107/src2\n\tName: \t\t\tsrc2\n\tUUID: \t\t\te366ba74-8845-6743-af81-879695e8727f\n\tParent UUID: \t\t-\n\tReceived UUID: \t\t-\n\tCreation time: \t\t2026-11-07 11:14:04 +0800\n\tSubvolume ID: \t\t713\n\tGeneration: \t\t1692822\n\tGen at creation: \t1692822\n\tParent ID: \t\t256\n\tTop level ID: \t\t256\n\tFlags: \t\t\t-\n\tSend transid: \t\t0\n\tSend time: \t\t2026-11-07 11:14:04 +0800\n\tReceive transid: \t0\n\tReceive time: \t\t-\n\tSnapshot(s):\n\t\t\t\t1000/fnm-scanner-native-20261107/snap2\n\tQuota group:\t\t0/713\n\t  Limit referenced:\t-\n\t  Limit exclusive:\t-\n\t  Usage referenced:\t0.00B\n\t  Usage exclusive:\t16.00KiB"}, {"argv": ["/usr/bin/btrfs", "subvolume", "show", "/vol1/1000/fnm-scanner-native-20261107/snap2"], "returncode": 0, "seconds": 0.004, "stderr": "", "stdout": "1000/fnm-scanner-native-20261107/snap2\n\tName: \t\t\tsnap2\n\tUUID: \t\t\t935af74e-7b89-0a43-abcf-48724a5f8bee\n\tParent UUID: \t\te366ba74-8845-6743-af81-879695e8727f\n\tReceived UUID: \t\t-\n\tCreation time: \t\t2026-11-07 11:14:04 +0800\n\tSubvolume ID: \t\t714\n\tGeneration: \t\t1692822\n\tGen at creation: \t1692822\n\tParent ID: \t\t256\n\tTop level ID: \t\t256\n\tFlags: \t\t\treadonly\n\tSend transid: \t\t0\n\tSend time: \t\t2026-11-07 11:14:04 +0800\n\tReceive transid: \t0\n\tReceive time: \t\t-\n\tSnapshot(s):\n\tQuota group:\t\t0/714\n\t  Limit referenced:\t-\n\t  Limit exclusive:\t-\n\t  Usage referenced:\t0.00B\n\t  Usage exclusive:\t16.00KiB"}, {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"], "returncode": 0, "seconds": 0.065, "stderr": "", "stdout": "[harness JSON omitted; see runs.A_subvolume_root]"}, {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"], "returncode": 0, "seconds": 0.049, "stderr": "", "stdout": "[harness JSON omitted; see runs.B_subtree]"}, {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/src", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot"], "returncode": 2, "seconds": 0.053, "stderr": "", "stdout": "[harness JSON omitted; see runs.C_writable_subvolume]"}, {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2", "--positions", "[{\"position\":1,\"published_sha256\":\"6e68e569b673e88d75fe5abf5b5cbd2834e74786856bb816811b7986c0a4ad3f\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin"], "returncode": 2, "seconds": 0.05, "stderr": "", "stdout": "[harness JSON omitted; see runs.D_root_owned_file]"}, {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "independent-copy", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"], "returncode": 2, "seconds": 0.047, "stderr": "", "stdout": "[harness JSON omitted; see runs.E_independent_copy]"}, {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap"], "returncode": 2, "seconds": 0.044, "stderr": "", "stdout": "{\"scope\":\"read_only_test_tree\",\"complete\":false,\"positions\":[{\"position\":1,\"status\":\"unknown\"}]}"}, {"argv": ["/usr/bin/btrfs", "subvolume", "delete", "/vol1/1000/fnm-scanner-native-20261107/snap2"], "returncode": 0, "seconds": 0.001, "stderr": "", "stdout": "Delete subvolume (no-commit): '/vol1/1000/fnm-scanner-native-20261107/snap2'"}, {"argv": ["/usr/bin/btrfs", "subvolume", "delete", "/vol1/1000/fnm-scanner-native-20261107/src2"], "returncode": 0, "seconds": 0.003, "stderr": "", "stdout": "Delete subvolume (no-commit): '/vol1/1000/fnm-scanner-native-20261107/src2'"}, {"argv": ["/usr/bin/btrfs", "subvolume", "delete", "/vol1/1000/fnm-scanner-native-20261107/snap"], "returncode": 0, "seconds": 0.002, "stderr": "", "stdout": "Delete subvolume (no-commit): '/vol1/1000/fnm-scanner-native-20261107/snap'"}, {"argv": ["/usr/bin/btrfs", "subvolume", "delete", "/vol1/1000/fnm-scanner-native-20261107/src"], "returncode": 0, "seconds": 0.001, "stderr": "", "stdout": "Delete subvolume (no-commit): '/vol1/1000/fnm-scanner-native-20261107/src'"}], "counts": {"checks_passed": 24, "checks_total": 24, "runs": 6}, "environment": {"btrfs_version": "btrfs-progs v6.2", "fnos_release": null, "hostname": "kotomi", "kernel": "6.18.18.c1168-trim", "machine": "x86_64", "mount_vol1": {"fstype": "btrfs", "options": "rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/", "target": "/vol1"}, "os_release": "PRETTY_NAME=\"Debian GNU/Linux 12 (bookworm)\"\nNAME=\"Debian GNU/Linux\"\nVERSION_ID=\"12\"\nVERSION=\"12 (bookworm)\"\nVERSION_CODENAME=bookworm\nID=debian\nHOME_URL=\"https://www.debian.org/\"\nSUPPORT_URL=\"https://www.debian.org/support\"\nBUG_REPORT_URL=\"https://bugs.debian.org/\"", "python": "3.11.2 (main, Oct  7 2026, 12:35:07) [GCC 12.2.0]", "script_euid": 0, "timezone": "+0800", "user": {"group": "Users", "name": "kurumi", "uid": 1000}}, "failure_reasons": [], "finished_at": "2026-11-07T11:14:04+0800", "hashes": {"harness_source_sha256": "46fa240487f7842e22ac6f1a9b157e01c2997a7c1ee2b2e530ce202bbdda4788", "probe_script_sha256": "fd59c730f84ed99b7c932a2f90ad2bb8447241d55f804751783c6b51cb1d7c73", "staged_verifier_after_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "staged_verifier_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "verifier_source_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "verifier_unchanged": true}, "probe": "scanner-native-smoke", "probe_root": "/vol1/1000/fnm-scanner-native-20261107", "probe_tree": {"root": "/vol1/1000/fnm-scanner-native-20261107", "root_owned_file": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin", "sibling_outside_subtree": "/vol1/1000/fnm-scanner-native-20261107/snap/sibling.flac", "snapshot": "/vol1/1000/fnm-scanner-native-20261107/snap", "snapshot2": "/vol1/1000/fnm-scanner-native-20261107/snap2", "snapshot_subtree_root": "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "src2_subvolume": "/vol1/1000/fnm-scanner-native-20261107/src2", "src_subvolume": "/vol1/1000/fnm-scanner-native-20261107/src"}, "published_positions": {"root_owned": {"sha256": "6e68e569b673e88d75fe5abf5b5cbd2834e74786856bb816811b7986c0a4ad3f", "size": 34}, "track": {"sha256": "72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47", "size": 34}}, "result": "PASS", "runs": {"A_subvolume_root": {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"], "key": "A_subvolume_root", "parse_error": null, "parsed": {"complete": true, "gates_after_patch": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": true}, "gates_original": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": false}, "module_path": "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "module_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "observed_file": {"after": {"atime_ns": 1794021244023574152, "ctime_ns": 1794021244023574152, "gid": 1001, "ino": 258, "mode": 0, "mtime_ns": 1794021244023574152, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac", "size": 34, "uid": 1000}, "before": {"atime_ns": 1794021244023574152, "ctime_ns": 1794021244023574152, "gid": 1001, "ino": 258, "mode": 0, "mtime_ns": 1794021244023574152, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac", "size": 34, "uid": 1000}, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"}, "positions_sha256": "1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276", "process": {"caps": {"CapEff": "0000000000000000", "CapPrm": "0000000000000000", "Gid": "1001\t1001\t1001\t1001", "Uid": "1000\t1000\t1000\t1000"}, "egid": 1001, "euid": 1000}, "profile": "btrfs-ro-snapshot", "python": "3.11.2", "report": {"checks": {"content_hash": "performed", "generation_bracket": "stable", "identity": "ok", "ro_semantics": "metadata_ro_bit_only", "scanner_unprivileged": "ok"}, "complete": true, "evidence_profile": "btrfs-ro-snapshot", "limits": {"entries_seen": 3, "limit_exceeded": false, "max_entries": 100000}, "positions": [{"match_count": 2, "position": 1, "status": "ambiguous"}], "read_mode": "o_noatime", "root_scope": "subvolume_root", "scope": "read_only_test_tree", "sync_barrier": {"generation_before_post_sync": 1692821, "generation_before_pre_sync": 1692821, "generation_end": 1692821, "generation_start": 1692821, "mechanism": "syncfs", "post": "done", "pre": "done"}, "unknown_reason": null}, "root": "/vol1/1000/fnm-scanner-native-20261107/snap", "scan_error": null, "view": {"flags": 1, "generation": 1692821, "method": "BTRFS_IOC_GET_SUBVOL_INFO via module functions", "parent_uuid_nonzero": true, "readonly_snapshot_accepted": true, "ro_bit0": true, "root": "/vol1/1000/fnm-scanner-native-20261107/snap", "treeid": 712, "uuid_nonzero": true}}, "returncode": 0, "seconds": 0.065, "stderr": "", "stdout": "{\"complete\": true, \"gates_after_patch\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": true, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": true}, \"gates_original\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": false, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": false}, \"module_path\": \"/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py\", \"module_sha256\": \"341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0\", \"observed_file\": {\"after\": {\"atime_ns\": 1794021244023574152, \"ctime_ns\": 1794021244023574152, \"gid\": 1001, \"ino\": 258, \"mode\": 0, \"mtime_ns\": 1794021244023574152, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\", \"size\": 34, \"uid\": 1000}, \"before\": {\"atime_ns\": 1794021244023574152, \"ctime_ns\": 1794021244023574152, \"gid\": 1001, \"ino\": 258, \"mode\": 0, \"mtime_ns\": 1794021244023574152, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\", \"size\": 34, \"uid\": 1000}, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\"}, \"positions_sha256\": \"1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276\", \"process\": {\"caps\": {\"CapEff\": \"0000000000000000\", \"CapPrm\": \"0000000000000000\", \"Gid\": \"1001\\t1001\\t1001\\t1001\", \"Uid\": \"1000\\t1000\\t1000\\t1000\"}, \"egid\": 1001, \"euid\": 1000}, \"profile\": \"btrfs-ro-snapshot\", \"python\": \"3.11.2\", \"report\": {\"checks\": {\"content_hash\": \"performed\", \"generation_bracket\": \"stable\", \"identity\": \"ok\", \"ro_semantics\": \"metadata_ro_bit_only\", \"scanner_unprivileged\": \"ok\"}, \"complete\": true, \"evidence_profile\": \"btrfs-ro-snapshot\", \"limits\": {\"entries_seen\": 3, \"limit_exceeded\": false, \"max_entries\": 100000}, \"positions\": [{\"match_count\": 2, \"position\": 1, \"status\": \"ambiguous\"}], \"read_mode\": \"o_noatime\", \"root_scope\": \"subvolume_root\", \"scope\": \"read_only_test_tree\", \"sync_barrier\": {\"generation_before_post_sync\": 1692821, \"generation_before_pre_sync\": 1692821, \"generation_end\": 1692821, \"generation_start\": 1692821, \"mechanism\": \"syncfs\", \"post\": \"done\", \"pre\": \"done\"}, \"unknown_reason\": null}, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap\", \"scan_error\": null, \"view\": {\"flags\": 1, \"generation\": 1692821, \"method\": \"BTRFS_IOC_GET_SUBVOL_INFO via module functions\", \"parent_uuid_nonzero\": true, \"readonly_snapshot_accepted\": true, \"ro_bit0\": true, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap\", \"treeid\": 712, \"uuid_nonzero\": true}}\n", "stdout_sha256": "9b5c7df2cc7257a1e952ed8da23b5d60fea3548cbba135ee61810a42aa486e79", "timeout_seconds": 300}, "B_subtree": {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"], "key": "B_subtree", "parse_error": null, "parsed": {"complete": true, "gates_after_patch": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": true}, "gates_original": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": false}, "module_path": "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "module_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "observed_file": {"after": {"atime_ns": 1794021244023574152, "ctime_ns": 1794021244023574152, "gid": 1001, "ino": 258, "mode": 0, "mtime_ns": 1794021244023574152, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac", "size": 34, "uid": 1000}, "before": {"atime_ns": 1794021244023574152, "ctime_ns": 1794021244023574152, "gid": 1001, "ino": 258, "mode": 0, "mtime_ns": 1794021244023574152, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac", "size": 34, "uid": 1000}, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"}, "positions_sha256": "1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276", "process": {"caps": {"CapEff": "0000000000000000", "CapPrm": "0000000000000000", "Gid": "1001\t1001\t1001\t1001", "Uid": "1000\t1000\t1000\t1000"}, "egid": 1001, "euid": 1000}, "profile": "btrfs-ro-snapshot", "python": "3.11.2", "report": {"checks": {"content_hash": "performed", "generation_bracket": "stable", "identity": "ok", "ro_semantics": "metadata_ro_bit_only", "scanner_unprivileged": "ok"}, "complete": true, "evidence_profile": "btrfs-ro-snapshot", "limits": {"entries_seen": 1, "limit_exceeded": false, "max_entries": 100000}, "positions": [{"match_count": 1, "position": 1, "status": "unique_match"}], "read_mode": "o_noatime", "root_scope": "subtree", "scope": "read_only_test_tree", "sync_barrier": {"generation_before_post_sync": 1692821, "generation_before_pre_sync": 1692821, "generation_end": 1692821, "generation_start": 1692821, "mechanism": "syncfs", "post": "done", "pre": "done"}, "unknown_reason": null}, "root": "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "scan_error": null, "view": {"flags": 1, "generation": 1692821, "method": "BTRFS_IOC_GET_SUBVOL_INFO via module functions", "parent_uuid_nonzero": true, "readonly_snapshot_accepted": true, "ro_bit0": true, "root": "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "treeid": 712, "uuid_nonzero": true}}, "returncode": 0, "seconds": 0.049, "stderr": "", "stdout": "{\"complete\": true, \"gates_after_patch\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": true, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": true}, \"gates_original\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": false, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": false}, \"module_path\": \"/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py\", \"module_sha256\": \"341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0\", \"observed_file\": {\"after\": {\"atime_ns\": 1794021244023574152, \"ctime_ns\": 1794021244023574152, \"gid\": 1001, \"ino\": 258, \"mode\": 0, \"mtime_ns\": 1794021244023574152, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\", \"size\": 34, \"uid\": 1000}, \"before\": {\"atime_ns\": 1794021244023574152, \"ctime_ns\": 1794021244023574152, \"gid\": 1001, \"ino\": 258, \"mode\": 0, \"mtime_ns\": 1794021244023574152, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\", \"size\": 34, \"uid\": 1000}, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\"}, \"positions_sha256\": \"1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276\", \"process\": {\"caps\": {\"CapEff\": \"0000000000000000\", \"CapPrm\": \"0000000000000000\", \"Gid\": \"1001\\t1001\\t1001\\t1001\", \"Uid\": \"1000\\t1000\\t1000\\t1000\"}, \"egid\": 1001, \"euid\": 1000}, \"profile\": \"btrfs-ro-snapshot\", \"python\": \"3.11.2\", \"report\": {\"checks\": {\"content_hash\": \"performed\", \"generation_bracket\": \"stable\", \"identity\": \"ok\", \"ro_semantics\": \"metadata_ro_bit_only\", \"scanner_unprivileged\": \"ok\"}, \"complete\": true, \"evidence_profile\": \"btrfs-ro-snapshot\", \"limits\": {\"entries_seen\": 1, \"limit_exceeded\": false, \"max_entries\": 100000}, \"positions\": [{\"match_count\": 1, \"position\": 1, \"status\": \"unique_match\"}], \"read_mode\": \"o_noatime\", \"root_scope\": \"subtree\", \"scope\": \"read_only_test_tree\", \"sync_barrier\": {\"generation_before_post_sync\": 1692821, \"generation_before_pre_sync\": 1692821, \"generation_end\": 1692821, \"generation_start\": 1692821, \"mechanism\": \"syncfs\", \"post\": \"done\", \"pre\": \"done\"}, \"unknown_reason\": null}, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib\", \"scan_error\": null, \"view\": {\"flags\": 1, \"generation\": 1692821, \"method\": \"BTRFS_IOC_GET_SUBVOL_INFO via module functions\", \"parent_uuid_nonzero\": true, \"readonly_snapshot_accepted\": true, \"ro_bit0\": true, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib\", \"treeid\": 712, \"uuid_nonzero\": true}}\n", "stdout_sha256": "5aa70a14e7e6667b3f6cfdc4d863e63e672d00353a25e4c107ad7b4082e304f1", "timeout_seconds": 300}, "C_writable_subvolume": {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/src", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot"], "key": "C_writable_subvolume", "parse_error": null, "parsed": {"complete": false, "gates_after_patch": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": true}, "gates_original": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": false}, "module_path": "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "module_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "observed_file": {"after": null, "before": null, "path": null}, "positions_sha256": "1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276", "process": {"caps": {"CapEff": "0000000000000000", "CapPrm": "0000000000000000", "Gid": "1001\t1001\t1001\t1001", "Uid": "1000\t1000\t1000\t1000"}, "egid": 1001, "euid": 1000}, "profile": "btrfs-ro-snapshot", "python": "3.11.2", "report": {"checks": {"content_hash": "not_attempted", "generation_bracket": "unknown", "identity": "unknown", "ro_semantics": "unknown", "scanner_unprivileged": "ok"}, "complete": false, "evidence_profile": "btrfs-ro-snapshot", "limits": {"entries_seen": 0, "limit_exceeded": false, "max_entries": 100000}, "positions": [{"position": 1, "status": "unknown"}], "read_mode": "o_noatime", "root_scope": "unknown", "scope": "read_only_test_tree", "sync_barrier": {"generation_before_post_sync": null, "generation_before_pre_sync": null, "generation_end": null, "generation_start": null, "mechanism": null, "post": "not_attempted", "pre": "not_attempted"}, "unknown_reason": "ro_semantics_unavailable"}, "root": "/vol1/1000/fnm-scanner-native-20261107/src", "scan_error": null, "view": {"flags": 0, "generation": 1692821, "method": "BTRFS_IOC_GET_SUBVOL_INFO via module functions", "parent_uuid_nonzero": false, "readonly_snapshot_accepted": false, "readonly_snapshot_error_errno": 30, "ro_bit0": false, "root": "/vol1/1000/fnm-scanner-native-20261107/src", "treeid": 711, "uuid_nonzero": true}}, "returncode": 2, "seconds": 0.053, "stderr": "", "stdout": "{\"complete\": false, \"gates_after_patch\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": true, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": true}, \"gates_original\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": false, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": false}, \"module_path\": \"/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py\", \"module_sha256\": \"341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0\", \"observed_file\": {\"after\": null, \"before\": null, \"path\": null}, \"positions_sha256\": \"1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276\", \"process\": {\"caps\": {\"CapEff\": \"0000000000000000\", \"CapPrm\": \"0000000000000000\", \"Gid\": \"1001\\t1001\\t1001\\t1001\", \"Uid\": \"1000\\t1000\\t1000\\t1000\"}, \"egid\": 1001, \"euid\": 1000}, \"profile\": \"btrfs-ro-snapshot\", \"python\": \"3.11.2\", \"report\": {\"checks\": {\"content_hash\": \"not_attempted\", \"generation_bracket\": \"unknown\", \"identity\": \"unknown\", \"ro_semantics\": \"unknown\", \"scanner_unprivileged\": \"ok\"}, \"complete\": false, \"evidence_profile\": \"btrfs-ro-snapshot\", \"limits\": {\"entries_seen\": 0, \"limit_exceeded\": false, \"max_entries\": 100000}, \"positions\": [{\"position\": 1, \"status\": \"unknown\"}], \"read_mode\": \"o_noatime\", \"root_scope\": \"unknown\", \"scope\": \"read_only_test_tree\", \"sync_barrier\": {\"generation_before_post_sync\": null, \"generation_before_pre_sync\": null, \"generation_end\": null, \"generation_start\": null, \"mechanism\": null, \"post\": \"not_attempted\", \"pre\": \"not_attempted\"}, \"unknown_reason\": \"ro_semantics_unavailable\"}, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/src\", \"scan_error\": null, \"view\": {\"flags\": 0, \"generation\": 1692821, \"method\": \"BTRFS_IOC_GET_SUBVOL_INFO via module functions\", \"parent_uuid_nonzero\": false, \"readonly_snapshot_accepted\": false, \"readonly_snapshot_error_errno\": 30, \"ro_bit0\": false, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/src\", \"treeid\": 711, \"uuid_nonzero\": true}}\n", "stdout_sha256": "68423c84dfbc2e6ed1273929fe1c5ab78aa465d3c0cab66656275d002f2e3513", "timeout_seconds": 300}, "D_root_owned_file": {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2", "--positions", "[{\"position\":1,\"published_sha256\":\"6e68e569b673e88d75fe5abf5b5cbd2834e74786856bb816811b7986c0a4ad3f\",\"published_size\":34}]", "--profile", "btrfs-ro-snapshot", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin"], "key": "D_root_owned_file", "parse_error": null, "parsed": {"complete": false, "gates_after_patch": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": true}, "gates_original": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": false}, "module_path": "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "module_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "observed_file": {"after": {"atime_ns": 1794021244403568532, "ctime_ns": 1794021244406310250, "gid": 0, "ino": 258, "mode": 420, "mtime_ns": 1794021244406310250, "path": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin", "size": 34, "uid": 0}, "before": {"atime_ns": 1794021244403568532, "ctime_ns": 1794021244406310250, "gid": 0, "ino": 258, "mode": 420, "mtime_ns": 1794021244406310250, "path": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin", "size": 34, "uid": 0}, "path": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin"}, "positions_sha256": "35ac9636696ba8ca6b6520947c6aaa9344bb0bb4a51a8017fa0a1c3bec2309a1", "process": {"caps": {"CapEff": "0000000000000000", "CapPrm": "0000000000000000", "Gid": "1001\t1001\t1001\t1001", "Uid": "1000\t1000\t1000\t1000"}, "egid": 1001, "euid": 1000}, "profile": "btrfs-ro-snapshot", "python": "3.11.2", "report": {"checks": {"content_hash": "attempted", "generation_bracket": "unknown", "identity": "unknown", "ro_semantics": "metadata_ro_bit_only", "scanner_unprivileged": "ok"}, "complete": false, "evidence_profile": "btrfs-ro-snapshot", "limits": {"entries_seen": 1, "limit_exceeded": false, "max_entries": 100000}, "positions": [{"position": 1, "status": "unknown"}], "read_mode": "o_noatime_denied", "root_scope": "subtree", "scope": "read_only_test_tree", "sync_barrier": {"generation_before_post_sync": null, "generation_before_pre_sync": 1692822, "generation_end": null, "generation_start": 1692822, "mechanism": "syncfs", "post": "not_attempted", "pre": "done"}, "unknown_reason": "noatime_permission_denied"}, "root": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2", "scan_error": null, "view": {"flags": 1, "generation": 1692822, "method": "BTRFS_IOC_GET_SUBVOL_INFO via module functions", "parent_uuid_nonzero": true, "readonly_snapshot_accepted": true, "ro_bit0": true, "root": "/vol1/1000/fnm-scanner-native-20261107/snap2/lib2", "treeid": 714, "uuid_nonzero": true}}, "returncode": 2, "seconds": 0.05, "stderr": "", "stdout": "{\"complete\": false, \"gates_after_patch\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": true, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": true}, \"gates_original\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": false, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": false}, \"module_path\": \"/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py\", \"module_sha256\": \"341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0\", \"observed_file\": {\"after\": {\"atime_ns\": 1794021244403568532, \"ctime_ns\": 1794021244406310250, \"gid\": 0, \"ino\": 258, \"mode\": 420, \"mtime_ns\": 1794021244406310250, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin\", \"size\": 34, \"uid\": 0}, \"before\": {\"atime_ns\": 1794021244403568532, \"ctime_ns\": 1794021244406310250, \"gid\": 0, \"ino\": 258, \"mode\": 420, \"mtime_ns\": 1794021244406310250, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin\", \"size\": 34, \"uid\": 0}, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap2/lib2/rootfile.bin\"}, \"positions_sha256\": \"35ac9636696ba8ca6b6520947c6aaa9344bb0bb4a51a8017fa0a1c3bec2309a1\", \"process\": {\"caps\": {\"CapEff\": \"0000000000000000\", \"CapPrm\": \"0000000000000000\", \"Gid\": \"1001\\t1001\\t1001\\t1001\", \"Uid\": \"1000\\t1000\\t1000\\t1000\"}, \"egid\": 1001, \"euid\": 1000}, \"profile\": \"btrfs-ro-snapshot\", \"python\": \"3.11.2\", \"report\": {\"checks\": {\"content_hash\": \"attempted\", \"generation_bracket\": \"unknown\", \"identity\": \"unknown\", \"ro_semantics\": \"metadata_ro_bit_only\", \"scanner_unprivileged\": \"ok\"}, \"complete\": false, \"evidence_profile\": \"btrfs-ro-snapshot\", \"limits\": {\"entries_seen\": 1, \"limit_exceeded\": false, \"max_entries\": 100000}, \"positions\": [{\"position\": 1, \"status\": \"unknown\"}], \"read_mode\": \"o_noatime_denied\", \"root_scope\": \"subtree\", \"scope\": \"read_only_test_tree\", \"sync_barrier\": {\"generation_before_post_sync\": null, \"generation_before_pre_sync\": 1692822, \"generation_end\": null, \"generation_start\": 1692822, \"mechanism\": \"syncfs\", \"post\": \"not_attempted\", \"pre\": \"done\"}, \"unknown_reason\": \"noatime_permission_denied\"}, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap2/lib2\", \"scan_error\": null, \"view\": {\"flags\": 1, \"generation\": 1692822, \"method\": \"BTRFS_IOC_GET_SUBVOL_INFO via module functions\", \"parent_uuid_nonzero\": true, \"readonly_snapshot_accepted\": true, \"ro_bit0\": true, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap2/lib2\", \"treeid\": 714, \"uuid_nonzero\": true}}\n", "stdout_sha256": "cb57d1c175913ea5b88b71aba5dbb23a406fa669d541358061f69128223a2ffc", "timeout_seconds": 300}, "E_independent_copy": {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/scanner_native_harness.py", "--module", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "--positions", "[{\"position\":1,\"published_sha256\":\"72e9d316d9b89bf42c7ecd684a24d79721562487cba24f94a82918952d54ce47\",\"published_size\":34}]", "--profile", "independent-copy", "--observe-file", "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"], "key": "E_independent_copy", "parse_error": null, "parsed": {"complete": false, "gates_after_patch": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": true, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": true}, "gates_original": {"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": false, "_INDEPENDENT_COPY_VIEW_VALIDATED": false, "_POINT_IN_TIME_SCAN_VALIDATED": false}, "module_path": "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "module_sha256": "341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0", "observed_file": {"after": {"atime_ns": 1794021244023574152, "ctime_ns": 1794021244023574152, "gid": 1001, "ino": 258, "mode": 0, "mtime_ns": 1794021244023574152, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac", "size": 34, "uid": 1000}, "before": {"atime_ns": 1794021244023574152, "ctime_ns": 1794021244023574152, "gid": 1001, "ino": 258, "mode": 0, "mtime_ns": 1794021244023574152, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac", "size": 34, "uid": 1000}, "path": "/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac"}, "positions_sha256": "1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276", "process": {"caps": {"CapEff": "0000000000000000", "CapPrm": "0000000000000000", "Gid": "1001\t1001\t1001\t1001", "Uid": "1000\t1000\t1000\t1000"}, "egid": 1001, "euid": 1000}, "profile": "independent-copy", "python": "3.11.2", "report": {"checks": {"content_hash": "performed", "generation_bracket": "not_applicable", "identity": "ok", "ro_semantics": "unavailable", "scanner_unprivileged": "ok"}, "complete": false, "evidence_profile": "independent-copy", "limits": {"entries_seen": 1, "limit_exceeded": false, "max_entries": 100000}, "positions": [{"position": 1, "status": "unknown"}], "read_mode": "o_noatime", "root_scope": "unknown", "scope": "read_only_test_tree", "sync_barrier": {"generation_before_post_sync": null, "generation_before_pre_sync": null, "generation_end": null, "generation_start": null, "mechanism": "syncfs", "post": "done", "pre": "done"}, "unknown_reason": "ro_semantics_unavailable"}, "root": "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "scan_error": null, "view": {"flags": 1, "generation": 1692821, "method": "BTRFS_IOC_GET_SUBVOL_INFO via module functions", "parent_uuid_nonzero": true, "readonly_snapshot_accepted": true, "ro_bit0": true, "root": "/vol1/1000/fnm-scanner-native-20261107/snap/lib", "treeid": 712, "uuid_nonzero": true}}, "returncode": 2, "seconds": 0.047, "stderr": "", "stdout": "{\"complete\": false, \"gates_after_patch\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": true, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": true}, \"gates_original\": {\"_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED\": false, \"_INDEPENDENT_COPY_VIEW_VALIDATED\": false, \"_POINT_IN_TIME_SCAN_VALIDATED\": false}, \"module_path\": \"/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py\", \"module_sha256\": \"341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0\", \"observed_file\": {\"after\": {\"atime_ns\": 1794021244023574152, \"ctime_ns\": 1794021244023574152, \"gid\": 1001, \"ino\": 258, \"mode\": 0, \"mtime_ns\": 1794021244023574152, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\", \"size\": 34, \"uid\": 1000}, \"before\": {\"atime_ns\": 1794021244023574152, \"ctime_ns\": 1794021244023574152, \"gid\": 1001, \"ino\": 258, \"mode\": 0, \"mtime_ns\": 1794021244023574152, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\", \"size\": 34, \"uid\": 1000}, \"path\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib/track.flac\"}, \"positions_sha256\": \"1f984da0856dfc602d376de923c064b0ccfb90a11413df38809e30058ea0b276\", \"process\": {\"caps\": {\"CapEff\": \"0000000000000000\", \"CapPrm\": \"0000000000000000\", \"Gid\": \"1001\\t1001\\t1001\\t1001\", \"Uid\": \"1000\\t1000\\t1000\\t1000\"}, \"egid\": 1001, \"euid\": 1000}, \"profile\": \"independent-copy\", \"python\": \"3.11.2\", \"report\": {\"checks\": {\"content_hash\": \"performed\", \"generation_bracket\": \"not_applicable\", \"identity\": \"ok\", \"ro_semantics\": \"unavailable\", \"scanner_unprivileged\": \"ok\"}, \"complete\": false, \"evidence_profile\": \"independent-copy\", \"limits\": {\"entries_seen\": 1, \"limit_exceeded\": false, \"max_entries\": 100000}, \"positions\": [{\"position\": 1, \"status\": \"unknown\"}], \"read_mode\": \"o_noatime\", \"root_scope\": \"unknown\", \"scope\": \"read_only_test_tree\", \"sync_barrier\": {\"generation_before_post_sync\": null, \"generation_before_pre_sync\": null, \"generation_end\": null, \"generation_start\": null, \"mechanism\": \"syncfs\", \"post\": \"done\", \"pre\": \"done\"}, \"unknown_reason\": \"ro_semantics_unavailable\"}, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib\", \"scan_error\": null, \"view\": {\"flags\": 1, \"generation\": 1692821, \"method\": \"BTRFS_IOC_GET_SUBVOL_INFO via module functions\", \"parent_uuid_nonzero\": true, \"readonly_snapshot_accepted\": true, \"ro_bit0\": true, \"root\": \"/vol1/1000/fnm-scanner-native-20261107/snap/lib\", \"treeid\": 712, \"uuid_nonzero\": true}}\n", "stdout_sha256": "7850f99c2ad116e7b5d59c9378717d6e02bec1e45a5704b8271c97cbeaae1c42", "timeout_seconds": 300}, "F_cli_default": {"argv": ["/usr/sbin/runuser", "-u", "kurumi", "--", "/usr/bin/python3", "-B", "/tmp/fnm-scanner-native-smoke-fl_g_g9c/verify_position_file_evidence.py", "--root", "/vol1/1000/fnm-scanner-native-20261107/snap"], "parsed": {"complete": false, "positions": [{"position": 1, "status": "unknown"}], "scope": "read_only_test_tree"}, "returncode": 2, "stderr": "", "stdout": "{\"scope\":\"read_only_test_tree\",\"complete\":false,\"positions\":[{\"position\":1,\"status\":\"unknown\"}]}\n", "stdout_sha256": "2838eddc6c582bd9a9f91044632801fe400148bb920bf20948d789ca07e0dd2f"}}, "schema": 1, "staging_dir": "/tmp/fnm-scanner-native-smoke-fl_g_g9c", "started_at": "2026-11-07T11:14:03+0800", "user": "kurumi"}
```

