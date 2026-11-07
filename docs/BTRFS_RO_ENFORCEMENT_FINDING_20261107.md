# Btrfs 只读快照在 fnOS `/vol1` 上并不阻止写入（2026-11-07 现场证据）

> 状态：**设备现场证据 + Lead 独立复验**。结论仅绑定本机（fnOS 1.2.0903、内核 `6.18.18.c1168-trim`、`/vol1` 挂载 `rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/`）。
> 该结论**不是**对上游 Linux/Btrfs 的一般性断言，也不构成部署/迁移授权。
> 数据来源：①2026-11-07 01:19–01:20 CST 在 `/vol1/1000/fnmusic-btrfs-probe-20261107` 运行的一次性探测（报告见 `/tmp/fnmusic-btrfs-subvol-btrfs.json`，属并行操作者产物）；②Lead 于 01:22–01:23 CST 在该一次性快照内做的独立最小诊断。探测树已于 01:23 CST 精确清理（子卷 delete + rmdir，`/vol1/1000` 无残留、无探测子卷）。

## 1. 事实（原始观测）

一次性的真实 Btrfs 快照创建（`btrfs subvolume snapshot -r`）后，内核元数据明确标记为只读：

```
btrfs property get -ts <snapshot> ro          -> ro=true
btrfs subvolume show <snapshot>               -> Flags: readonly ; Subvolume ID: 674 ; Generation: 1691522
GET_SUBVOL_INFO (probe, 4.fnOS)                -> flags_hex 0x1, root_subvol_rdonly_bit0 = true
require_readonly_snapshot(fd)                  -> accepted
```

但在该只读快照目录内，以下写入**全部被接受**（Lead 独立复验，01:22 CST）：

| 操作 | 身份 | 结果 |
| --- | --- | --- |
| `echo x > snapshot-ro/diag-root-write.txt` | root (uid 0) | **ACCEPTED**（期望 EROFS 拒绝） |
| `runuser -u kurumi -- echo x > snapshot-ro/diag-kurumi-write.txt` | kurumi (uid 1000, 无 caps) | **ACCEPTED**（期望拒绝） |
| `rm -f snapshot-ro/must-not-exist.txt` | root | **ACCEPTED**（删除只读快照内既有文件成功） |
| 对照组：写 RW 源子卷 `subvol-src` | root | ACCEPTED（预期） |

写入真实落在快照树内（`ls -la` 可见文件、内容长度正确），并**推进了该子卷的 generation**：`1691522 → 1691524`（首轮 RW→写入→RO）→ 诊断写入后 `1691528 → 1691533`。`btrfs property get` 与 `subvolume show` 依旧报告 `readonly`。

并行操作者的探测 JSON 独立复现了同一现象：`write_inside_readonly_snapshot.result = "unexpected-accept"`、`write_after_final_lock.result = "unexpected-accept"`，快照目录随后确实含 `must-not-exist.txt`、`relock-marker.txt`、`post-relock-write.txt`；另有 `manual-root-test.txt` / `manual-kurumi-test.txt` 两个手工验证空文件。同一 JSON 还记录：`cycle2`（RO→RW→RO 但**不写入**）generation 不变（1691524→1691524），而含写入的重锁 generation 变化（`generation_changed_after_write_relock = true`）。

本机 `/vol1` 的 `trimacl` 是 fnOS 定制挂载选项（文件系统类型仍为 `btrfs`，设备 `/dev/mapper/trim_8f1e972a_…-0`）。**未**验证上游标准 Btrfs 是否同样行为；也**未**定位到 fnOS 内核补丁的具体位置。观测事实：本机这台设备上，VFS 层的 `O_RDONLY/EROFS` 约束对"只读"子卷未生效。

## 2. 对项目的影响（重要）

1. **`scripts/verify_position_file_evidence.py` 的"已验证只读快照"前提在本机不成立**。它用 `GET_SUBVOL_INFO` 的 `BTRFS_ROOT_SUBVOL_RDONLY`(bit 0) 判定扫描期不可变；本机证据显示该位为 1 时仍可被写入/删除。因此：
   - `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须**继续为 `False`**，返回 `unknown` 的门槛不能因"RO 位=1"而放宽；
   - 扫描器目前**不能**用作文件验收证据（与交接 §1 结论一致，且现在有了更强的负面理由）。
2. **RO→RW→写入→RO 的竞争场景比原设想更严重**：不是"窗口内能否重新锁上"，而是"锁上后依然可写"。任何依赖 RO 位冻结快照的设计都需要重新设计（例如：停写后一次性生成快照 + 扫描前后 generation 对比 + 独立校验；或扫描副本而非快照）。
3. generation 在真实写入后会推进（本机观测），使其仍可能作为"是否有变化"的证据来源；但 `trimacl` 补丁对 generation 语义的影响未验证，不能据此单独放行。

## 3. 复现步骤（一次性、可丢弃快照）

```bash
# 1) 在一次性测试目录建真实快照（示例路径已完成清理）
btrfs subvolume create /vol1/1000/<disposable>/subvol-src
printf 'payload\n' > /vol1/1000/<disposable>/subvol-src/payload.txt
btrfs subvolume snapshot -r /vol1/1000/<disposable>/subvol-src /vol1/1000/<disposable>/snapshot-ro
# 2) 确认元数据只读
btrfs property get -ts /vol1/1000/<disposable>/snapshot-ro ro      # ro=true
btrfs subvolume show /vol1/1000/<disposable>/snapshot-ro | grep Flags  # Flags: readonly
# 3) 写入应被拒绝，但本机实际被接受
echo x > /vol1/1000/<disposable>/snapshot-ro/should-be-EROFS.txt
runuser -u kurumi -- sh -c 'echo x > /vol1/1000/<disposable>/snapshot-ro/should-be-EROFS-kurumi.txt'
rm -f /vol1/1000/<disposable>/snapshot-ro/<existing-file>
# 4) 清理（精确路径，顺序：解 RO → 删快照 → 删源 → rmdir）
btrfs property set -ts /vol1/1000/<disposable>/snapshot-ro ro false
btrfs subvolume delete /vol1/1000/<disposable>/snapshot-ro
btrfs subvolume delete /vol1/1000/<disposable>/subvol-src
rmdir /vol1/1000/<disposable>
```

## 4. 证据边界

- 仅证明本机 `/vol1`（`trimacl`）上该行为；不证明其它 fnOS 版本/其它挂载选项/其它文件系统。
- 不证明生产曲库/暂存当前状态受影响；本次探测只写了一个一次性合成测试目录，未触碰 `/vol1/1000/music`、暂存、账本、服务或容器（服务在清理后仍 `active`）。
- 探测脚本（`scripts/anonymous_staging_btrfs_probe.py`、`scripts/btrfs_subvol_state_probe.py`）由**并行操作者**创建并在生产 `/vol1` 上执行，超出 Lead 下发的只读任务范围；详见交接进展记录中的事件说明。本文件只采信其可复验的原始观测，并已由 Lead 独立复验关键项。
