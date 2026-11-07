# /vol1 Btrfs 上的匿名暂存与只读快照门·现场探针证据（2026-11-07）

> **证据记录，不是上线/部署/迁移授权。** 本文记录在用户授权的隔离测试目录中，对**已提交于 HEAD、未部署/未集成**的候选与现有扫描器常量在真实 fnOS `/vol1`（Btrfs + `trimacl`）上的最小探针结果。
> 执行者：Lead。执行窗口：2026-11-07 02:34:41–02:34:44 JST（NAS 本地 01:34:41–01:34:44 CST），单次原子运行。
> 本次**未**停止/启动/重启任何服务、**未**改配置、**未**安装/回滚包、**未**触碰生产曲库/暂存/账本/gateway/容器。
> 独立复审：已完成 **PASS-with-limits**（`docs/BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW_20261107.md`，被审版本 SHA-256 `b11b851c…1507`）。本修订吸收复审 F1–F7；复审后本文件的新哈希由复审者重算绑定。

## 1. 环境（实测）

| 项 | 观测值 |
| --- | --- |
| 主机 | `kotomi`（fnOS NAS，192.168.0.106），`ssh kotomi-root`（root，key-only，端口 721） |
| fnOS | `1.2.0903`（`/usr/trim/etc/version`，与 `docs/DEVICE_INVENTORY_20261107.md` 同期一致） |
| 内核 | `Linux 6.18.18.c1168-trim #1168 SMP PREEMPT_DYNAMIC x86_64` |
| 文件系统 | `/vol1` = `/dev/mapper/trim_8f1e972a_e1ee_4afd_b791_e7e4fc2aa457-0`，`btrfs`，挂载 `rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/` |
| 探针目录 | `/vol1/1000/fnmusic-btrfs-probe-20261107`（`system.trim_acl` xattr 存在；`d--------- /vol1/1000`，但 `kurumi` 可遍历/写入，见 §2） |
| Python | 3.11.2 |
| btrfs-progs | v6.2 |

## 2. 方法、绑定与隔离

- 采用**单次 SSH 原子运行**：`setup → 匿名暂存(root) → 匿名暂存(kurumi) → Btrfs(root) → Btrfs(kurumi) → Btrfs 清理 → 全树清理`，全程约 3 秒。探针树在本次运行的各阶段之间短暂存在，运行结束即删除；**不跨运行残留**（§7）。
- NAS 代码副本：`/tmp/fnmusic-vol1-probe/code`（本地 `scp` 上传，**逐文件 `sha256sum` 与本地一致**）：
  - `proxy/manual_save_anonymous.py` = `fa4dc6bafcd8bddacba78584ac97e04a6ac29a765791d2cb75f4b4ee6785bc52`
  - `proxy/manual_save_core.py` = `53783b5874e7f87ed3e40741d2164dd799710df95cfc7d78b28beb66c612ba6c`
  - `scripts/verify_position_file_evidence.py` = `197d43a99e244b99021936df12e321b9fcc4c48779ce8a1b029c0de9db049b80`
  - `scripts/anonymous_staging_btrfs_probe.py` = `3a3730e5419eed4800380af6d31ed0b5aff10c1cd601b464701e20f2d7f58c82`（本探针驱动，仅证据工具，产品不导入）
  - `scripts/btrfs_subvol_state_probe.py` = `b3bc66dfd5c7f5754e01f3c7e339d36efa0ef85db6b9ad6cb1cb9b99d40169bf`（同上）
  - `proxy/__init__.py` = `e3b0c442…b855`（空文件，仅用于导入）
  - 编排脚本 `scripts/run_vol1_probe.sh` = `b5a2350ea46ab17ae0f43e2fa8cbc26f7d957d7f0fb761071d0cad979449953e`（已纳入本仓库；NAS `/tmp/fnmusic-vol1-probe/` 同哈希）
- 探针目录结构（root 与 kurumi 分相，**不经跨 UID chown**）：`root-phase/{staging(0700 root), destination(0755 root)}`、`user-phase/{staging(0700 kurumi:Users), destination(0755 kurumi:Users)}`、`btrfs-phase/{subvol-src, snapshot-ro}`。
- 原始输出：NAS `/tmp/fnmusic-vol1-probe/logs/10..16-*.json`（规范运行）与同级 `/tmp/fnmusic-vol1-probe/run-meta.txt`；`logs/` 另存 01–08 早期运行产物（见 §9）。两个驱动 `main()` 还会写 7 个 `/tmp/fnmusic-vol1-probe-{setup,root,user,cleanup}.json` 与 `/tmp/fnmusic-btrfs-subvol-{btrfs,user,cleanup}.json`（与 `logs/` 对应文件同内容，不随 §7 清理，属 `/tmp` 易失副产物）。全部 `.err` 文件为空。
- `run-meta.txt`：`probe-root-exists-before=no`；7 个阶段均 `rc=0`；`probe-root-exists-after=no`。

## 3. 结果 A：匿名 O_TMPFILE + linkat(AT_EMPTY_PATH) 在 `/vol1` Btrfs 上

### 3.1 原始 O_TMPFILE 的 ACL 继承（复核候选设计的核心前提）

| 身份 | 请求 mode | `fchmod` 前实际 mode | `fchmod` 后实际 mode | uid | nlink | regular |
| --- | --- | --- | --- | --- | --- | --- |
| root (uid 0, CapEff `000001ffffffffff`) | `0o600` | **`0o700`** | `0o600` | 0 | 0 | 是 |
| kurumi (uid 1000, CapEff `0000000000000000`) | `0o600` | **`0o700`** | `0o600` | 1000 | 0 | 是 |

→ 在真实 `/vol1`（`trimacl`）上，`O_TMPFILE` 请求 `0600` 实际得到 `0700`，与 `manual_save_anonymous.py` 注释所述一致；`fchmod(0600)` 后严格复验通过。**该平台的 mode 规范化是必需的，不是防御性冗余。**

### 3.2 成功链路（root）

| 步骤 | 观测 |
| --- | --- |
| 构造 store | ok（staging/destination 同设备 `st_dev=60`） |
| `begin_owned_stage` | ok；stage fd `mode=0o600 uid=0 nlink=0 regular=True inheritable=False` |
| 暂存期目录内容 | staging `[]`、destination `[]`（无命名临时文件） |
| append / seal | ok；`read_for_verification` 与写入 payload 的 SHA-256 一致（`fc412855…0800c`） |
| `publish_noreplace("probe-track-1.bin")` | **`published`**；目标文件 `mode=0o600 uid=0 nlink=1 size=36`，SHA-256 与 payload 一致 |
| `release_owned_stage` / `close` | ok |

### 3.3 冲突与负向路径（root）

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 同 basename 再次发布 | 原子不覆盖 → `CONFLICT` | `conflict`；原文件 inode/SHA-256 均未变（`content_unchanged=true`） |
| staging 目录非 0700（0755） | 拒绝 | `ValueError: staging_directory_not_private` |
| 同一 fd 作 staging 与 destination | 拒绝 | `ValueError: directories_must_be_distinct` |
| basename 含 `/` | 在任何发布尝试前拒绝 | `ValueError: invalid_basename`，随后 `abort_owned_stage` ok |

### 3.4 非 root 成功链路（kurumi，**euid 1000、CapEff=0**）

| 步骤 | 观测 |
| --- | --- |
| 构造 / begin | ok；stage fd `mode=0o600 uid=1000 nlink=0` |
| append / seal / readback | ok，SHA-256 一致 |
| `publish_noreplace` | **`published`**；目标文件 `mode=0o600 uid=1000:Users nlink=1`，SHA-256 与 payload 一致 |

**重要发现（相对文档的保守假设）**：候选文档称 `linkat(AT_EMPTY_PATH)` 可能需要 `CAP_DAC_READ_SEARCH`、普通用户不保证可用；在**本机内核**上，无任何 capability 的 uid 1000 进程**成功完成真实发布**。候选保留的 `EPERM → at_empty_path_capability_required` 失败关闭路径因此**未被本机触发**（该路径仍未在真实内核上取得失败证据，见 §6）。

## 4. 结果 B：`GET_SUBVOL_INFO` 常量与只读快照门

探针直接调用 `scripts/verify_position_file_evidence.py` 中的常量与函数（哈希见 §2），并与 `btrfs-progs` 输出交叉核对。

### 4.1 快照元数据（本机真实 `btrfs subvolume snapshot -r`）

| 字段 | 观测值 |
| --- | --- |
| `_BTRFS_IOC_GET_SUBVOL_INFO` | `0x81f8943c`（= (2<<30) \| (504<<16) \| (0x94<<8) \| 60） |
| treeid | 682 |
| generation | 1691566（读取时） |
| flags | `0x1` → `root_subvol_rdonly_bit0 = true` |
| uuid / parent_uuid | 均非零（`5bad31ea…07fb5` / `da9a8553…81c6a`） |
| `btrfs property get -ts <snap> ro` | `ro=true` |

交叉核验（同一快照与早期运行）：`GET_SUBVOL_INFO` 返回的 treeid/uuid/parent_uuid/generation 与 `btrfs subvolume show` 的 `Subvolume ID` / `UUID` / `Parent UUID` / `Generation` 逐项一致（早期运行 treeid 674/678，generation 1691522/1691539）。→ **扫描器的常量与字段偏移在本机 UAPI 上成立。**

### 4.2 扫描器门函数的真实结果

| 调用 | 输入 | 结果 |
| --- | --- | --- |
| `_require_readonly_snapshot` | 真实 RO 快照 | **accepted** |
| `_require_readonly_snapshot` | 普通 RW 子卷 | rejected `[Errno 30] not a read-only Btrfs snapshot` |
| `_require_readonly_snapshot` | 顶层子卷内普通目录（treeid 256 区） | rejected 同上 |
| `_require_unprivileged_process` | root（uid 0） | rejected `[Errno 1] privileged scanner process` |
| `_require_unprivileged_process` | kurumi（uid 1000, CapEff=0） | **accepted** |
| 非特权进程直接 `GET_SUBVOL_INFO` | kurumi 打开快照 fd | **可读**，返回 treeid 682 / flags 0x1（无需 root） |

## 5. 结果 C：本机 `/vol1` 的只读子卷**并不阻止写入**（阻断性发现）

快照根目录在快照前被设为 `0777`（文件 `0666`），因此写入被拒只可能来自只读子卷状态，而不会来自普通权限位。

### 5.1 对 `ro=true` 快照的写入（root 与 kurumi 均测试）

| 操作 | 身份 | 期望（上游 Btrfs） | 本机实测 |
| --- | --- | --- | --- |
| 在 RO 快照内创建新文件 | root | `EROFS` 拒绝 | **ACCEPTED**（文件存在，内容可读回） |
| 在 RO 快照内创建新文件 | kurumi（CapEff=0） | `EROFS` 拒绝 | **ACCEPTED**（`user-phase-ro-write.txt` 存在） |
| 重锁 RO 后再次创建 | root | 拒绝 | **ACCEPTED** |
| `btrfs property get -ts <snap> ro` | — | `ro=true` | 首次写入**之后**执行，`ro=true`；写入前的只读状态由 `GET_SUBVOL_INFO flags=0x1` 与 `btrfs subvolume show` 的 `Flags: readonly` 证明 |
| `GET_SUBVOL_INFO` flags | — | bit0=1 | 写入后仍 `0x1`（RO 位未被清除） |

### 5.2 写入对 generation 的影响

| 场景 | generation 变化 |
| --- | --- |
| root 在 RO 快照内写入 + `sync` | `1691566 → 1691567`（**变化**） |
| kurumi 在 RO 快照内写入 + `sync` | `1691569 → 1691572`（**变化**） |
| RO→RW→写入→RO（`btrfs property set`） | `1691566 → 1691569`（**变化**；treeid/uuid/parent_uuid 不变，RO 位恢复） |
| RO→RW→RO 但**不写入** | `1691569 → 1691569`（**不变**） |

→ 本机上：**真实数据写入会推进 generation，而仅切换只读标志不会**。generation 仍可作为"内容是否被改过"的候选信号，但"RO 位=1 ⇒ 扫描期不可变"这一前提在本机**不成立**。

### 5.3 影响

1. `scripts/verify_position_file_evidence.py` 的 `_require_readonly_snapshot` 只能证明"元数据标记为只读快照"，**不能**证明"扫描期间不可写"。`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED` 必须**继续为 `False`**；不得因 RO 位=1 放宽 unknown 门槛。
2. 任何"停写→快照→扫描"的设计，在本机必须叠加**独立的变化检测**（扫描前后 generation 对比、扫描副本而非快照、或对已发布文件做独立重读校验）；单靠 RO 位不够。
3. generation 语义受 fnOS 定制内核（`trimacl`、`trimafs2` "TRIM aggregate stacking filesystem"）影响，本机只观测到"写入会推进"，**未**证明其可作为完整一致性证明。

## 6. 证据边界

**本文件可证明：**

- 候选 `manual_save_anonymous.py`（**已提交于 HEAD `512a68c`，未部署/未集成**；文件哈希 `fa4dc6ba…`）在真实 `/vol1` Btrfs 上：ACL 继承导致 `O_TMPFILE` `0600`→`0700`、`fchmod` 规范化生效；root 完成含 `release_owned_stage` 的完整链路，非 root（CapEff=0）完成至 `publish` 的链路并以 `close` 结束（该阶段未调用 release）；产物 `0600`、`nlink=1`、内容 SHA-256 一致；真实 `EEXIST` 冲突不覆盖原文件；三个负向校验按固定 reason 拒绝。
- 本机内核上 `linkat(AT_EMPTY_PATH)` 对无 capability 的普通用户可用（与文档的保守假设不同）。
- `verify_position_file_evidence.py` 的 ioctl 号、结构偏移、treeid/generation/flags/uuid/parent_uuid 语义与 `btrfs-progs` 一致；RO 快照门与"非特权进程"门在真实设备上按设计接受/拒绝。
- 本机 `/vol1` 上 `ro=true` 的 Btrfs 子卷可被 root 与 kurumi 写入；RO 位不会被清除；写入推进 generation，仅标志切换不推进。

**本文件不能证明（不得外推）：**

- 生产曲库/暂存/账本/服务/容器当前状态未受影响之外的任何生产链路；本次只写一个一次性合成目录（详见 §7 清理核验）。
- 上游标准 Linux/Btrfs 的一般行为；不能把本机 `trimacl`/`trimafs2` 的行为外推到其它 fnOS 版本或标准内核。
- 匿名暂存候选的**失败/权限路径**：本机未触发 `EPERM`（发布成功），`EROFS` 目标、磁盘满、fsync 失败、写入失败、并发替换等分支仍只有 mock/推导证据（独立复审 F1/F3 仍成立）。
- 匿名暂存候选可作为生产管线：`PIPELINE_INTEGRATION_ALLOWED=False` 未变，接收端/持久化/隔离/恢复均未验证。
- jobs DB 路径、停写、备份、恢复、回滚、部署、身份与管理员授权边界；§0.1/§0.2 门禁仍 **BLOCKED**。

## 7. 清理与残留核验

| 项 | 结果 |
| --- | --- |
| Btrfs 子卷清理 | `snapshot-ro`、`subvol-src` 均 `btrfs subvolume delete` 成功；`btrfs-phase` 目录删除 |
| 全树清理 | `shutil.rmtree(/vol1/1000/fnmusic-btrfs-probe-20261107)`；`probe-root-exists-after = false` |
| 残留子卷 | `btrfs subvolume list /vol1 \| grep fnmusic-btrfs-probe` → 无 |
| `/vol1/1000` 顶层 | 运行前/后条目集合**完全一致**（无增删） |
| 设备其它状态 | 未停/启服务、未改配置、未安装/回滚 |

## 8. 复现

```bash
# 本地（E:\fnmusic-ext-dev）→ NAS
scp proxy/__init__.py proxy/manual_save_anonymous.py proxy/manual_save_core.py \
    kotomi-root:/tmp/fnmusic-vol1-probe/code/proxy/
scp scripts/verify_position_file_evidence.py scripts/anonymous_staging_btrfs_probe.py \
    scripts/btrfs_subvol_state_probe.py kotomi-root:/tmp/fnmusic-vol1-probe/code/scripts/
scp scripts/run_vol1_probe.sh kotomi-root:/tmp/fnmusic-vol1-probe/run_vol1_probe.sh
# NAS 单次原子运行（内部含清理）
bash /tmp/fnmusic-vol1-probe/run_vol1_probe.sh
# 原始 JSON: /tmp/fnmusic-vol1-probe/logs/1[0-6]-*.json；run-meta: /tmp/fnmusic-vol1-probe/run-meta.txt
```

驱动脚本为**证据工具**，不被产品导入；哈希见 §2。若需重跑，请先确认 `/vol1/1000/fnmusic-btrfs-probe-20261107` 不存在（`setup` 会拒绝已有目录）。

## 9. 并行活动与归属（按复审 F1 更正后）

- 同一工作区/同一 NAS 上并存另一个并行会话。其文档 `docs/BTRFS_RO_ENFORCEMENT_FINDING_20261107.md` 引用了本探针**早期一次运行（2026-11-07 01:19–01:20 CST）**的输出 `/tmp/fnmusic-btrfs-subvol-btrfs.json`，并把该输出称为「并行操作者产物」、把两个探针脚本称为「由并行操作者创建并在生产 `/vol1` 上执行，超出 Lead 下发的只读任务范围」；同一文档第 ② 来源描述的「Lead 独立最小诊断」即本文件的执行者。**两份文档的归属表述不一致，本节按实际执行记录更正如下：**
  1. 早期运行与规范运行均由 **Lead** 使用本文件 §2 所列的**同一组驱动脚本**（`anonymous_staging_btrfs_probe.py`、`btrfs_subvol_state_probe.py`）在同一探针树执行；早期运行**不是**独立数据源。
  2. 早期探针树在 2026-11-07 01:23 CST 前后消失；**清理动作的实际执行者与授权依据无独立记录，记为 unknown**（并行会话文档声称由其清理，但无独立证据）。
  3. 并行文档记录的 RO 子卷可写现象与本文结论一致；但「来源独立」**不成立**（同一组脚本、同一文件系统、同一时段），本文不再作此声称。结论可信度的依据是 §7 的自包含复现、哈希绑定，以及独立复审在同一设备上的复跑，而不是「第二来源」。
  4. **治理说明**：早期运行与规范运行的实际授权依据是用户在本会话「自主开发、允许独立测试目录/可控 NAS 测试」的授权；所有设备写入均在一次性合成探针树内并已核验清理（§7），未触碰生产曲库/暂存/账本/服务/配置。本文不主张超出该授权的操作。

---

**复审状态**：独立复审已完成，verdict **PASS-with-limits**（`docs/BTRFS_VOL1_PROBE_INDEPENDENT_REVIEW_20261107.md`，被审版本 `b11b851c…`；无阻断发现）。本修订（F1–F7）的复审确认结果以该报告后续更新为准；未解除项见复审 §8（§0.1/§0.2 门禁、匿名暂存失败/并发路径、生产管线、扫描器验收资格均仍 BLOCKED/unknown）。
