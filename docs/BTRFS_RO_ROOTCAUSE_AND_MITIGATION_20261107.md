# `/vol1` Btrfs 只读子卷写入绕过：根因与可行缓解（2026-11-07）

> **设备现场证据，不是上线/部署/迁移授权。** 本文记录在 `/tmp` 与一次性 `/vol1` 合成探针树中的对照实验；未停/启服务、未改配置、未安装/回滚、未触碰生产曲库/暂存/账本。
> 执行者：Lead。窗口：2026-11-07 10:02:59–10:04:01（NAS 本地，CST），由**两个探针脚本各自原子运行**（10:02:59–10:03:00 与 10:03:59–10:04:01），各自 `trap` 自清理。
> 关联：`docs/BTRFS_VOL1_TMPFILE_PROBE_20261107.md`（现象与首次证据）、`docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md`（并行记录）、`docs/SCAN_CONSISTENCY_REDESIGN_20261107.md`（原设计）。

## 1. 环境与证据绑定

| 项 | 值 |
| --- | --- |
| 设备 | `kotomi`（fnOS NAS，192.168.0.106），`ssh kotomi-root`（root，key-only，721） |
| fnOS / 内核 | `1.2.0903` / `6.18.18.c1168-trim` |
| `/vol1` | `/dev/mapper/trim_8f1e972a_…-0`，btrfs，`rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/` |
| Python / btrfs-progs | 3.11.2 / v6.2（另用 `losetup`/`mount`/`mkfs.btrfs`/`unshare`） |
| 根因实验脚本（仓库内） | `scripts/btrfs_ro_rootcause_probe.sh` = `d5074d637d8390857059424e71f072eae33d86ccf63ddbc3a2e71791481383db` |
| 缓解实验脚本（仓库内） | `scripts/btrfs_ro_mitigation_probe.sh` = `c7e513b3d2167558e65d0f36eb4a010c4daed6a5d697669e8a0b736bab6115a2` |
| 写入探针 helper | `scripts/write_probe_helper.py` = `a6ce5b775652ebcc32b711601f74400f8ff182b13dcb04567c41ebf6ec289462` |
| NAS `/tmp` 副本 | 三个文件与本地逐字节同哈希 |
| 原始日志 | `/tmp/fnm-ro-rootcause.log`=`8cbbab2364cffe7e9b34d63789b5de7244cb381f6afb4e6ea2a06c8c7ae8cbad`；`/tmp/fnm-ro-mitigation.log`=`1a4b893c4f11dc14492a14419fa5bfe7827a045be2022c561b3527ed60aafb8b` |

## 2. 根因：`trimacl` 挂载选项绕过了只读子卷的写强制

| 实验 | 视图 | 操作 | 结果 |
| --- | --- | --- | --- |
| E1 | `/tmp` 内 512 MiB loop btrfs，挂载 **无** `trimacl` | `btrfs subvolume snapshot -r` 后在其中创建文件 | **`WRITE_REJECTED 30 Read-only file system`**（标准行为） |
| E2 | **同一 loop btrfs 镜像**，挂载 **加** `trimacl` | 同一 RO 快照内创建文件 | **`WRITE_ACCEPTED`**，文件真实出现；`property get ro` 仍 `true` |
| E3a | `/vol1` 正常 RW 路径（`trimacl`） | 向 RO 快照直接创建文件 | **`WRITE_ACCEPTED`**（与既有证据一致） |
| E3b | 同一 `/vol1` 快照，在**私有 mount namespace** 内以 `-o ro,subvolid=<id>` 只读挂载 | 向该只读挂载写入 | **`WRITE_REJECTED 30 Read-only file system`** |

**根因结论（仅本机）**：在**同一内核、同一 loop btrfs 镜像**上，仅切换 `trimacl` 挂载选项就复现了"RO 子卷可写"；去掉该选项则恢复标准 `EROFS`。因此触发点是 **`trimacl` 挂载选项（fnOS 补丁/`trimafs2` 模块）**，而不是 Btrfs 文件系统本身，也不是我们探针的写法。未定位到具体补丁代码行；不得外推到其他 fnOS 版本/内核。

## 3. 已验证的可行缓解

| 方案 | 实测 | 能力 | 边界/成本 |
| --- | --- | --- | --- |
| **M1 RO 挂载** | E3b：`-o ro` 挂载后写入 `EROFS`（即使选项串中仍显示 `trimacl`） | 让"扫描视图"恢复内核强制只读 | **同一子卷经 `/vol1` RW 路径仍可写（E3a）**→ 只能保护视图本身，必须叠加 generation bracket / 逐文件重读；需要 root 帮助进程挂载（私有 namespace 或受控全局挂载） |
| **M2 `btrfs send` → `receive` 到独立 btrfs（无 `trimacl`）** | E5：send 8,395,408 B；receive rc=0；收到子卷设 `ro` 后写入 **`EROFS`**；再以 `-o ro` 重挂载仍 **`EROFS`**；收到内容哈希与快照**完全一致**（`916ff831…`） | **最强独立视图**：独立物理存储 + RO 强制可用 | 全量拷贝（约库大小）+ root 帮助进程；本实验镜像在 `/tmp`（易失），持久验证需专用 scratch 卷；未做坏块注入 |
| **M3 reflink（FICLONE）副本** | E4：`cp --reflink=always` rc=0；`filefrag` 显示源与副本**同一物理 extent + `shared` 标志**；**纠正实验**（`docs/evidence/20261107/REFLINK_COW_CORRECTED.log`，8 MiB，每侧哈希在对方修改前取得）：改源→副本不变（C1=C0）、改副本→源不变（H2=H1），**双向 COW 独立均已验证** | **廉价、COW 独立的稳定视图**（8 MiB 数据） | 同文件系统（与源共享坏块风险）；普通文件权限模型（非 RO 语义），需防 root/管线覆盖；大文件实测 8 MiB，未测库级 |
| **M4 普通拷贝到 ext4（/tmp）** | 设计期已知 `/tmp` 为 ext4；本次未单独重测 | 简单、物理独立、无需 RO 语义 | 全量拷贝；/tmp 易失 |

E4 的 `filefrag` 关键输出（两文件第一条 extent 的物理偏移完全相同）：

```
src/big.bin  0..2047: 694057586..694059633 : 2048 : last,shared,eof
copy.bin     0..2047: 694057586..694059633 : 2048 : last,shared,eof
```

## 4. 对扫描器/验收设计的影响

1. 原设计"RO 位不可依赖"**仍然成立**；新增两条可操作结论：
   - RO **挂载**（`-o ro`）能恢复内核强制只读（M1），可用于构造扫描视图，但不能阻止经 RW 挂载路径的修改；
   - 独立副本（M2/M4）**根本不需要 RO 语义**，是更稳的证据载体；M3 是廉价 COW 视图。
2. 设计文档中的 T-C（reflink 副本）应从"FICLONE 未验证"升级为"已在 `/vol1` 验证可用"；新增 M2 作为 P7 A4 的首选独立视图候选。
3. 凡是仍依赖原 `/vol1` 视图的方案（M1/T-B 快照 + 子树扫描），**必须**叠加 generation bracket 与逐文件独立重读；generation 语义仍需 P0-3 补证。
4. 建议：P7 A4 默认用 **M2**（send/receive 到专用 scratch btrfs）或 **M4**（拷贝到独立 ext4）+ 账本 `published_sha256/size` 比对；M3 用于快速抽检/差分；M1 作为轻量只读视图，不得单独作为"不可变"证据。

## 5. 证据边界（不得外推）

- 仅证明本机（fnOS 1.2.0903 + 内核 6.18.18.c1168-trim + `trimacl`）；不能证明上游 Linux/Btrfs 或其它 fnOS 版本/挂载组合。
- 未定位 `trimacl`/`trimafs2` 的具体补丁位置；未审计该模块全部行为。
- E3b 的 RO 挂载写测试以 root 执行（`EROFS` 对 root 已生效，预期对普通用户同样，但未单独实跑）；未测试通过 RW 挂载路径并发写入时的竞态。
- E4/纠正实验只测 8 MiB 文件；原 E4 的"改副本→源不变"比较曾被复审指出为同义反复（同一时刻读同一文件），已用"对方修改前取得哈希"的单独实验（`REFLINK_COW_CORRECTED.log`）补证双向 COW；未做库级 reflink、坏块注入、reflink 后快照/配额影响。
- E5 的接收端在 `/tmp` 的 loop 镜像中（`/tmp` 易失、位于 ext4），未验证持久 scratch 卷、未验证大文件/全库的吞吐与失败恢复。
- 本文件不改变 §0.1/§0.2 或任何部署门禁。

## 6. 复现与清理

```bash
# 本地 → NAS（三个文件与本文 §1 哈希一致）
scp scripts/btrfs_ro_rootcause_probe.sh scripts/btrfs_ro_mitigation_probe.sh scripts/write_probe_helper.py \
    kotomi-root:/tmp/
ssh kotomi-root 'mkdir -p /tmp && cp /tmp/write_probe_helper.py /tmp/fnm-writecheck.py && \
                 bash /tmp/btrfs_ro_rootcause_probe.sh; bash /tmp/btrfs_ro_mitigation_probe.sh'
```

两脚本均以 `trap cleanup EXIT` 清理：loop 镜像、挂载点、`/vol1` 一次性探针树及其子卷、send 流。运行后核验：`/vol1/1000` 无 `fnmusic-btrfs-ro-probe*`、`btrfs subvolume list /vol1` 无相关子卷、无 `/tmp` 遗留镜像/流、无遗留挂载。本次运行后三项均确认为空。

M3 纠正实验（复审 F2 后）：

```bash
scp scripts/btrfs_reflink_cow_probe.sh kotomi-root:/tmp/fnm-reflink-fix.sh
ssh kotomi-root 'bash /tmp/fnm-reflink-fix.sh'   # 输出见 docs/evidence/20261107/REFLINK_COW_CORRECTED.log
```

## 7. 下一步

1. 把本证据并入 `docs/SCAN_CONSISTENCY_REDESIGN_20261107.md`（追加 addendum：M1/M2/M3/M4、T-C 状态更新、推荐组合）。
2. P0-3：generation 语义补证（不 sync、元数据、truncate、rename、ABA、RO 绕过写入后的推进时机），决定 generation bracket 是否可靠。
3. P0-2：扫描器 100k 上限与子树模式；结合 M2 独立视图评估实现路径。
4. §0.2/P7：以独立副本 + 账本指纹为默认证据链，提交设计评审。
