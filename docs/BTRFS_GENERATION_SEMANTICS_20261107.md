# `/vol1` Btrfs 子卷 generation 语义补证（2026-11-07）

> **设备现场证据，不是上线/授权证明。** 目的：判定"扫描前后 generation 对比（bracket）"能否作为 `/vol1` 上内容是否被修改的检测信号。记录在一次性 `/vol1` 合成探针树中进行，运行后自清理。
> 执行者：Lead。窗口：2026-11-07 10:06:01–10:07:2x（NAS 本地 CST）。
> 关联：`docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md`（`trimacl` 绕过 RO 写强制）、`docs/SCAN_CONSISTENCY_REDESIGN_20261107.md`（设计）。

## 1. 证据绑定

| 项 | 值 |
| --- | --- |
| 设备/内核 | `kotomi`，fnOS `1.2.0903`，`6.18.18.c1168-trim` |
| 文件系统 | `/vol1` btrfs，`rw,relatime,trimacl,space_cache=v2,subvolid=5,subvol=/` |
| 探针脚本（仓库内） | `scripts/btrfs_generation_semantics_probe.py` = `7f27a07a2ceeaec75c607425cc66bcef9f38b3af2492eca5e0d52245b8cd6a7d`；barrier 补测 `scripts/btrfs_generation_barrier_probe.py` = `4795bba548fd9e54076075a5acbd17201721726013d669aea510dd1ba6e456e7` |
| 原始输出（持久副本） | `docs/evidence/20261107/BTRFS_GENERATION_SEMANTICS_OUTPUT.json` = `6add2ee5ac7169bde761de43e915a24818da5c7b8f27d9706eab0add8b7f60fb`（**第二次运行**）；`docs/evidence/20261107/BTRFS_GENERATION_BARRIER_OUTPUT.json` = `1038a03912e10d1414004b181b7a1eafff1ed480b0c3ca5e82c0d79bf66e9e84`（第二次运行）；`docs/evidence/20261107/REFLINK_COW_CORRECTED.log` = `973b05eb4a1ea4a00aa8a2f02043b35ee4bdc1173a98ab775e30fd4c99ed8c55`（复审 F2 后的纠正实验） |
| 探针树 | `/vol1/1000/fnmusic-btrfs-gen-probe-20261107`（`src` 子卷 + `snapshot-ro` 子卷），运行后 `probe_root_removed=true`，两个子卷 delete rc=0 |
| 原始输出 | 本次运行 stdout JSON（在 Lead 会话记录中）；脚本可复现 |

## 2. 实测序列（子卷 `src`，generation 为 `GET_SUBVOL_INFO` root_item.generation）

> **绑定说明（复审 F1）**：下表数值来自**首次运行**（`treeid` 689/690）；持久 JSON 保存的是**第二次运行**（`treeid` 693/694，§2 数值整体 +34、§8 barrier 数值整体 +31）。两次运行**除 #14 外的布尔结论一致**，唯 #14 两次结果不同（见该行注）。§9 记录本轮复审后的更正经复算。
> **列语义（复审 F6）**：脚本对 `sync=False` 的步骤记录后仍会执行一次内部 settle（`os.sync()`），因此下一行的 `before` 可能已 +1；#1/#3 的"下一步起点/稳定后"数值来自该 settle，**不代表动作本身立即推进 generation**。

| # | 操作 | sync 前（立即） | 强制 `os.sync()` 后 | 结论 |
| --- | --- | --- | --- | --- |
| 1 | 创建文件，未 sync | 不变（1692585） | 下一步起点 1692586 | 需提交障碍 |
| 2 | 创建文件 + sync | 不变（1692586） | **1692587（+1）** | 数据写推进 |
| 3 | 写文件 + **仅 `fsync(file)`**，无 `os.sync()` | 不变（1692587） | 稳定后 1692588 | **`fsync(file)` 单独不足以让 root generation 推进** |
| 4 | `chmod`（仅元数据） | 不变 | **+1** | 元数据变化也推进 |
| 5 | `truncate` | 不变 | **+1** | 推进 |
| 6 | `rename` | 不变 | **+1** | 推进 |
| 7 | `append` | 不变 | **+1** | 推进 |
| 8 | 重写相同字节（内容不变） | 不变 | **+1** | 推进（写操作本身） |
| 9 | 创建+删除（净无变化） | 不变 | **+1** | 推进（历史不被抵消） |
| 10 | **只读读取**（open/read 不写） | 不变 | **+1** | **读也可推进（atime 更新）** |
| 11 | ABA：写 A → sync | — | **+1** | 推进 |
| 12 | ABA：恢复原字节 → sync | — | **+1** | **恢复原内容仍推进**→历史上发生过修改可被检出 |
| 13 | `utimensat`（touch 时间） | 不变 | **+1** | 推进 |
| 14 | 写文件后**不 sync 等 35 秒** | 不变 | **首次运行：35 s 后仍不变（1692599）；归档/复跑：35 s 后 +1** | **提交时机不稳定；时间不可作为提交保证** |
| 15 | 向 `ro=true` 快照写入（trimacl 绕过，允许）+ sync | 不变 | **+1**（1692602→1692603） | RO 绕过写入也推进（sync 后） |
| 16 | 向 RO 快照再写 + 不 sync 等 35 秒 | 不变 | **+1**（1692603→1692604） | 观察到的提交时机不稳定 |

补充：`base` treeid=689；`snapshot-ro` treeid=690、flags=1（RO 位）。

## 3. 结论（对本机）

1. **generation 能反映真实修改**：数据、元数据（chmod/truncate/rename/utimensat）、写回原内容（ABA）都会在**提交之后**推进 generation；历史不会被"恢复原状"抹掉。
2. **必须显式提交障碍**：仅在写文件后 `fsync(file)` **没有**观察到 root generation 推进；需要文件系统级 `sync`/`syncfs` 之后才能读到推进。**等待 35 秒在两次运行中结果相反（一次不变、一次 +1）→ 时间不可作为提交保证**。
3. **读取本身可能推进 generation（atime）**：先 rename（ctime 变化）再只读读取时，读取触发的 atime 更新使 generation +1。→ **扫描器读取文件本身可能造成假阳性**。
4. **RO 绕过写入同样推进 generation**（#15/#16）：若 bracket 建立在快照/子卷上，可检测到 trimacl 绕过导致的修改——前提是两端有提交障碍。
5. 因此 generation bracket 可用但**不充分**，必须同时满足：
   - 扫描前后都做 `syncfs`（不是仅 `fsync`）；且要记录 barrier 后的 generation；
   - 读取路径使用 `noatime` 挂载或 `O_NOATIME`（避免自读引起的 atime 推进）；若无法避免 atime，需要区分/容忍 atime 类推进；
   - generation 变化只能说明"发生过修改"，不能定位是哪个文件；需配合逐文件重读哈希与 stat 身份；
   - 不能把"等一段时间再读"作为提交保证。

## 4. 对设计的影响

- 设计文档 L2 的"视图 tuple/generation bracket"必须写成 **syncfs barrier + noatime 读取**，否则 bracket 会因 atime 产生假阳性、或因未提交产生假阴性。
- 若采用独立副本（`btrfs send`→receive 或拷贝，见 `docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` 的 M2/M4），generation bracket 的用途从"检测副本被改"退化为"检测源视图在取景期间被改"，同样需要 barrier。
- 建议在 §4 失败注入计划中增加：atime 假阳性注入（扫描前后 rename/touch 场景）、无 sync 假阴性注入、ABA 注入、RO 绕过写入注入。

## 5. 证据边界

- 仅本机（fnOS 1.2.0903 / 6.18.18.c1168-trim / `trimacl`）；不能外推上游或其它版本。
- 数据量小（单文件级）、单进程顺序操作；未测并发写入、大文件、目录规模、长事务。
- **35 秒等待在两次运行中结果相反**（一次不变、一次 +1）；不能推断 commit interval 具体值，也不能据此断言任何时间保证。
- `fsync(file)` 未推进 root generation 的解释未定位到内核路径，只记录现象。
- `noatime` 挂载或 `O_NOATIME` 已补测可消除 atime 假阳性（§8 B/C）；仍未测 **scanner 非文件属主**时 `O_NOATIME` 是否可用（需属主/CAP_FOWNER）、只读挂载视图上的 `syncfs` 行为。
- 本文件不改变 §0.1/§0.2 或部署门禁。

## 6. 复现

```bash
scp scripts/btrfs_generation_semantics_probe.py kotomi-root:/tmp/fnm-generation-probe.py
ssh kotomi-root 'python3 -B /tmp/fnm-generation-probe.py \
  --probe-root /vol1/1000/fnmusic-btrfs-gen-probe-20261107 --long-wait-seconds 35'
```

脚本拒绝已存在的探针树，结束后删除 `snapshot-ro`、`src` 子卷并 rmdir 探针树。

## 7. 下一步最小补测

1. `noatime` 挂载或 `O_NOATIME` 下重复 #10（只读读取）与 #2（写），确认 atime 假阳性可消除。**（已完成：见 §8 的 B/C）**
2. 加入 `syncfs()`（不是 `sync()`）作为 barrier，确认与 `sync()` 行为一致。**（已完成：见 §8 的 D）**
3. 并发写入场景（另一进程在扫描期间写）下的 bracket 检出率。**（已完成：见 §8 的 E）**

## 8. 追加补测结果（barrier，2026-11-07 10:08 CST）

脚本 `scripts/btrfs_generation_barrier_probe.py`（哈希见 §1），一次性探针树，运行后 `probe_root_removed=true`。

| 测试 | 场景 | 结果 |
| --- | --- | --- |
| A | rename 后普通 read（RW `relatime` 挂载） | **generation +1（1692611→1692612）**，复现 atime 假阳性 |
| B | 同一文件 `utime` 后以 **`O_NOATIME`** 读取 | **不变**（1692613）→ **O_NOATIME 可消除假阳性** |
| C | 私有 mount namespace 内以 **`rw,noatime`** 挂载同一子卷并读取全部文件（读取前 atime=1） | **不变**（1692616）→ **noatime 挂载可消除假阳性**；mount/unmount 本身不推进 generation |
| D | 写文件 + `fsync(file)` 后读 generation，再调用 **`syncfs()`** | fsync 后不变；`syncfs` 后 **+1** → `syncfs` 与 `sync` 同为有效提交障碍 |
| E | 扫描窗口内由**另一进程**追加写入（bracket：sync → 等待 → writer → sync） | **generation +1（1692614→1692615）** → 并发写入可被 bracket 检出 |

**修订结论**：

- generation bracket 在**显式提交障碍（`sync` 或 `syncfs`）+ 无 atime 噪声的读取路径**下，既能检出并发写入（E），也不会因扫描自身读取产生假阳性（B/C）。
- atime 假阳性是真实且可复现的（A），**必须**在实现中通过 `O_NOATIME`（需文件属主/CAP_FOWNER）或 `noatime`/只读挂载的读取视图规避；否则 bracket 不可直接用。
- `fsync(file)` 不是足够的 barrier；实现必须用文件系统级 `sync`/`syncfs`。
- 仍未验证：`O_NOATIME` 在 **scanner 非文件属主**时是否可用（需 CAP_FOWNER）；长扫描期间多个提交窗口的检出边界；并发 rename/truncate 等具体类型；`syncfs` 在只读挂载视图上的行为。

**对设计的影响（补充 §4）**：L2 bracket 的实现规格应固定为：两端 `syncfs`（或受控挂载点的 `sync`）→ 读 generation → 若变化则 fail-closed；读取必须走 `O_NOATIME` 或 `noatime` 视图；bracket 只证明"发生过修改"，仍需逐文件重读与账本指纹。失败注入新增：atime 假阳性注入（A）、`syncfs` 缺失假阴性注入、并发 writer 注入（E）。

复现（barrier，复审 F7 已补命令）：

```bash
scp scripts/btrfs_generation_barrier_probe.py kotomi-root:/tmp/fnm-barrier-probe.py
ssh kotomi-root 'python3 -B /tmp/fnm-barrier-probe.py \
  --probe-root /vol1/1000/fnmusic-btrfs-barrier-probe-20261107'
```

脚本拒绝已存在的探针树，并在 finally 删除 `snapshot-ro`/`src` 子卷、rmdir 探针树。

## 9. 独立复审后的更正（F1/F2，2026-11-07）

独立复审（`docs/BTRFS_EVIDENCE_ROUND2_REVIEW_20261107.md`，PASS-with-limits，F1/F2）指出两处证据绑定问题；本节的更正与补充实验已可由持久文件复核。

### 9.1 F1：两次运行数值/实例绑定

- 本文 §2 的值来自**首次运行**；持久 `BTRFS_GENERATION_SEMANTICS_OUTPUT.json` 是**第二次运行**（`treeid` 693/694，§2 数值整体 +34、§8 barrier 数值整体 +31）。两次运行**除 #14 外**的布尔结论一致。
- **#14 在两次运行间结果相反**：首次运行 35 秒后 generation 不变；归档与复审复跑均为 +1。故正确表述是"**提交时机不稳定，时间不可作为提交保证**"，而不是"35 秒一定不提交"；§2/#14 与 §3-2 已按此更正。
- 更正**不改变** §8 的结论方向（必须显式 barrier），但要求任何实现/验收不得依赖时间等待。

### 9.2 F2：reflink 双向 COW 的纠正实验

复审指出原 E4 的"改副本→源哈希不变"比较是同义反复（两个哈希在同一时刻读同一文件）。纠正实验（`docs/evidence/20261107/REFLINK_COW_CORRECTED.log`，8 MiB 随机文件，`cp --reflink=always`，每侧哈希在对方被修改**之前**取得）：

| 步骤 | 值 |
| --- | --- |
| 复制后源 `H0` = 副本 `C0` | `938ce08bbaf227f42f75c317d2d41063b028d0cf23e271834aca3ace61d900aa` |
| 改源后：源 `H1`（≠H0）、副本 `C1`（=C0） | `147763299b5bda116aecd457dd81ffd7f7910ee5a3eaa766963bc5218fab88ba` / `938ce08b…` |
| 再改副本后：源 `H2`（=H1）、副本 `C2`（≠C1） | `147763299b…` / `e63df1de8db0f3a339e581634bb81749e921db3aa568b176023d0e67762c122e` |
| `filefrag -v` | 源与副本第一条 extent 的 physical_offset 完全相同，且均带 `shared` 标志 |

结论：**reflink 副本与源在双向修改下都保持内容独立（COW）**；方向一（改源）与方向二（改副本）均成立。边界不变：同文件系统共享 extent/坏块风险、普通文件权限模型、仅 8 MiB 样本。

### 9.3 其它复审次要项（按复审报告编号 F3–F7）

- **F3**（"单次原子运行"与两段日志 59 秒间隔不符）：RO 实验文档实际是**两个脚本各自原子运行**（10:02:59–10:03:00 与 10:03:59–10:04:01）；`docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md` 抬头已按此更正。
- **F4**（generation §5/§7 未随 §8 更新）：§7 已标注"已完成"；§5 的"35 秒/未测 noatime"两条陈旧表述已按 §8 更新。
- **F5**（脚本退出码非 fail-closed）：脚本仅作证据夹具，不应以退出码单独判定成功；本文件与 RO 文档均要求核对原始日志/输出。**该限制保留**，实现侧工具必须 fail-closed（扫描器任务另测）。
- **F6**（§2 表列语义易误读）：已在 §2 绑定说明中解释 `sync=False` 步骤的内部 settle，并说明 #14 的两次观测差异。
- **F7**（M2/E5 为 root、barrier 无复现命令）：M2/E5 的 root 执行已在本文与 RO 文档明示；barrier 复现命令已补到 §8；非 root 的 M2/M1 行为仍未测，保留为 unknown。
