# 生命周期 runbook Rev-5 独立复审（真机复验）

> **复审者**：`native-reviewer`（未参与 runbook 撰写/修订）。**复审对象**：`E:\fnmusic-ext-212` 的 `docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md`，**Rev-5**，整文件 `0d7b3a4923fa56bcfca9ea4511edf05c2bd19a75f8e57640a40504e6e54d4546`（179,069 B / 1,943 行 LF）；文末 `REV5-HASH-STAMP` 前缀值 `11c1bb1a690032e7740c6664ddb804c35f1f3d1fde930faa7fa664b2010e75b8`。复审时 HEAD `064271a`。
> **范围**：只做 Rev-5 限定复核：真机 Python 3.11.2 实跑 §5.3 编辑器 heredoc、键级校验器、§5.5-4.0 grep、§5.4-3.5 ss 过滤，并全文件再扫 Python 3.11/grep/ss/命名假设、附录 E 绑定与戳。**未执行 runbook 的真实设备窗口步骤**（未停服、未停容器、未改配置、未写 `/vol1`）；NAS 只写 `/tmp` 合成数据，跑后清理。只新增本报告，不改被审文档。

---

## 0. 结论

**判定：PASS-with-limits。**

- **Rev-5 三处真机缺陷修复全部通过**：
  - §5.3 f-string 已改为 f-string 外预计算 `eol_name` + `%` 输出；**NAS Python 3.11.2** 下三个 Python heredoc 均 `py_compile` 通过，编辑器 LF/CRLF、export/行尾注释/其它键保留、mode/owner（640 或 600，uid/gid 1000:1000）、二次幂等全部通过。
  - §5.5-4.0 已改为 `grep -qE '^status=exited( |$)'`；实测 `status=exited exit=0` 与 `status=exited` 命中，`status=running`/`status=exitedx` 拒绝。
  - §5.4-3.5 已把 established 连接按本应用 `fnmusic-ext.sock` 与官方 socket 分开；构造“官方 12 条 + 本应用 0 条”不误拒，本应用 1 条能检出；官方连接只记录、不参与判定。
- **真机片段测试：27 PASS / 0 FAIL**（Python 3.11.2；合成 `.env`/样例文件，仅 `/tmp`）。服务 `MainPID=1870197`、`NRestarts=0`、`ActiveState=active` 前后不变；`/tmp/fnm-rev5-*` 已清理。
- **全文件再扫未发现新的 Python 3.11 不兼容写法**；grep/ss/w3 命名/marker/residual/ACL 限制/§3.2 `<TS>` 注记均与 Rev-5 声明一致。
- **唯一未完全项（F1，次要）**：附录 E 有 **1/19** 条绑定与当前磁盘不一致——`docs/MANUAL_SAVE_PLAYLIST_JOB.md` 表为 118,962 B / `4b5610ce…`，当前磁盘为 **117,678 B / `c3c17774…`**。runbook 已明确“窗口内必须重算”，因此**不阻断**；但作为“附录 E 与当前磁盘一致”的复核项，需更新或标注时点。

---

## 1. 对象与哈希

| 项 | 值 |
| --- | --- |
| runbook | `0d7b3a49…54d4546`，179,069 B / 1,943 行 LF |
| `REV5-HASH-STAMP` 前缀（删除 stamp 行后） | `11c1bb1a…0e75b8`；本机独立复算一致 |
| 整文件 SHA-256（含 stamp 行） | `0d7b3a49…54d4546`；与 Lead 提供一致 |
| 复审时 HEAD | `064271a`（runbook 未被后续提交改动） |
| 被审文档状态 | 草稿/计划，未执行；门禁不变（文首 §1.5、§8、附录 C 均保留） |
| 证据边界 | 本报告只证明“抽取片段在真机 Python 3.11.2 上可运行”，不证明整个部署窗口可执行 |

---

## 2. 真机复验（NAS `kotomi`，Python 3.11.2）

环境：`kotomi`，内核 `6.18.18.c1168-trim`，`/usr/bin/python3` = **Python 3.11.2**；测试目录 `mktemp -d /tmp/fnm-rev5-XXXXXX`；上传的抽取代码哈希：

| 抽取物 | SHA-256 |
| --- | --- |
| `rev5_editor.py`（§5.3 第 1066–1118 行） | `fb773f717312791d1af7900393a71c862dd297f4272e10cf38582e92293090bc` |
| `rev5_validator.py`（§5.3 第 1141–1164 行） | `ebec1f6a8efbc67de129f2111fa780b8e6b47e16b63460343d4cc621fcb23de0` |
| `rev5_sqlite.py`（§4.3 第 920–935 行） | `543759d2da2a553ffba9762ca63810d0fb72566a1768ccaa36a07055fed64e6e` |
| 测试 harness `rev5_test.sh` | `734fc351a9c07784d2a740dacf389a4fa924476594b7f347c59980a9e6d2ca02` |

### 2.1 §5.3 编辑器（f-string / fchmod / fchown / 原子替换）

真机输出（摘要）：

```text
PASS py_compile rev5_editor.py (3.11)
PASS editor LF rc=0
PASS editor LF export+comment preserved
PASS editor LF key2=false
PASS editor LF other key unchanged
PASS editor LF OTHER_KEY unchanged
PASS editor LF mode/owner preserved (640 1000 1000)
PASS editor LF idempotent (hash stable)
PASS editor LF idempotent mode/owner (640 1000 1000)
PASS editor CRLF rc=0
PASS editor CRLF bytes/keys
PASS editor CRLF mode/owner preserved (600 1000 1000)
```

覆盖：`export` 前缀、`#` 行尾注释、非目标键不变、CRLF 保留、mode/owner 保留、二次执行哈希稳定；f-string 中不再有反斜杠表达式。

### 2.2 键级校验器（0 行/两键/空格/异常路径）

```text
PASS validator A rc=0 (KEYS_OK changed=FNMUSIC_MANUAL_SAVE_ENABLED)
PASS validator B rc=0 (KEYS_OK changed=none(idempotent))
PASS validator C rc=0 (KEYS_OK changed=FNMUSIC_MANUAL_SAVE_ENABLED,FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED)
PASS validator D rc=1 (REFUSE: ... non_target_changed=['OTHER'])
PASS validator E rc=1 (REFUSE: missing=['FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED'] ...)
PASS validator F rc=1 (REFUSE: ... not_false=['FNMUSIC_MANUAL_SAVE_ENABLED'] ...)
```

### 2.3 §5.5-4.0 grep

```text
PASS grep exited_exit0 -> match
PASS grep exited_only -> match
PASS grep running -> no
PASS grep exitedx -> no
```

### 2.4 §5.4-3.5 ss 过滤

用 fake `ss` 输出构造（官方 12 条 + 本应用 0 条，再加 1 条本应用）：

```text
PASS ss app filter 0 on official-only
PASS ss official filter 12
PASS ss app filter detects 1
```

结论：官方 `trim_music.socket`/`trim_music_upstream.socket` 连接不会误拒；本应用 `fnmusic-ext.sock` 连接能被检出并触发 REFUSE。

### 2.5 次要项静态核对

- **marker 最后写 + 新鲜 inspect**：§5.4-3.5 先 `rm -f` 旧 marker、重新 `docker inspect` 到 `container.premarker.txt` 并确认 `status=exited`，之后才写 `window/container_finished_at/marker_created_at`；§5.5-4.0 复核 marker 的 `window` 与 `container_finished_at` 与本窗口/本容器实例匹配 → 符合 Rev-5 声明。
- **residual 拆分**：先全量 `ps` 取候选，再拆 `residual-procs.*`（含 `$REPO`）与 `residual-unattributed.*`（不含 repo，典型容器内 uvicorn）；任一非空均 REFUSE → 符合。
- **`.env` ACL 已知限制**：修改前后 `getfacl` 取证；明确 `os.replace` 新 inode 不复制 ACL/xattr（trimacl），不要求实现 ACL 复制 → 符合。
- **§3.2 `<TS>` 占位符注记**：已说明含 `<TS>` 时 `bash -n`/直接执行报语法错属预期，必须先替换 → 符合。

---

## 3. 全文件再扫（Python 3.11 / grep / ss / 命名）

- 3 个 Python heredoc（§4.3 sqlite、§5.3 编辑器、§5.3 校验器）在 **Python 3.11.2** 下全部 `py_compile` 通过；未发现 f-string 表达式内反斜杠、`|` 联合类型运行时注解、`match`/海象等新增不兼容写法。全文中的 f-string 代码只有 §4.3 `f"file:{src_path}?mode=ro"` 与 §5.3 `print(f"REFUSE: ...")`，均无反斜杠表达式；附录 D 修订记录里的 f-string 示例是**说明文字**，不是可执行代码。
- `grep` 假设：§5.5-4.0 使用锚定 `grep -qE '^status=exited( |$)'`；§5.4 两处 `grep -q 'status=exited'` 是**预检子串匹配**，真实 `docker inspect` 输出固定为 `status=exited exit=0`，可接受；marker 的 `grep -qx` 与写入内容逐字一致；`grep -x fnmusic-sources` 容器名精确匹配。
- `ss` 假设：本应用/官方 socket 均按路径正则过滤，已实测；官方连接只写 `.count`/记录，不参与 REFUSE。
- 命名：W3 采样统一为 `w3.<tag>.{env,sources-data,cache-logs}.sha256`，比对读 `w3.t0/t15.$f.sha256`，并先断言文件存在；未再发现“名字对不上”的路径。
- 未发现新的阻断级问题。

---

## 4. 附录 E 绑定核对

逐行解析附录 E 的 **19** 条 `docs/...` 绑定，重算整文件 SHA-256/字节/行数：

- **18/19 与当前磁盘一致**（包括 Rev-4 复审 `7ace09c1…`/17,873、Rev-3 复审 `50a00296…`/17,494、Rev-2 复审 `2ca80471…`/17,985、SCAN `6bef7134…`/114,285、schema `dec0aabb…`/119,555、schema Rev-3 复审 `ee8b0437…`/10,953 等）。
- **1/19 不一致（F1）**：`docs/MANUAL_SAVE_PLAYLIST_JOB.md`
  - 附录 E：**118,962 B / 1,284 行 / `4b5610ce8bc5e458bfbe9917d29a99764b5e96761340ed9fc8aa441abce0b0cf`**
  - 当前磁盘：**117,678 B / 1,284 行 / `c3c17774f9a375bf1dced39f75aa5f0f5e19d6fd2b899604218570d27e27649a`**
  - 该文件最后变更于 `91bd9c5`（与 runbook 同一提交）；runbook 附录 E 明确“窗口内必须重算、旧值只用于追溯”，所以属**绑定时点差异**，不阻断本次复核，但“附录 E 与当前磁盘一致”一项不能判完全通过。

整文件哈希与 stamp 两种语义已分开核对：`REV5-HASH-STAMP` 是“该行之前字节”的哈希 `11c1bb1a…`；整文件（含 stamp 行）是 `0d7b3a49…`，二者不同且均与文件一致。

---

## 5. 发现

### F1（次要，未解除）附录 E 有 1 条绑定过期
`docs/MANUAL_SAVE_PLAYLIST_JOB.md` 表值 `4b5610ce…`/118,962 B 与当前磁盘 `c3c17774…`/117,678 B 不一致；该文件在 `91bd9c5` 已变更，runbook 表应为变更前版本。runbook 已要求窗口重算，故不阻断；建议更新附录 E 该行（或明确标注“Rev-5 时点值，窗口必须重算”）。
### F2（限制，非缺陷）真机复验是片段级，不是完整窗口演练
本次只在 NAS `/tmp` 用合成 `.env`/样例文件运行抽取片段；未执行真实停服/停容器/备份/安装/回滚，不能证明整个 runbook 窗口可执行。符合任务范围；完整窗口仍须按 §5 执行并留证。
### F3（信息）无新的阻断项
未发现其它 Python 3.11 不兼容、grep/ss 误判、命名错位或 marker 复用问题。

---

## 6. 解除 / 未解除清单

**已解除（真机/静态复核）**：
- F-R1：§5.3 f-string 修复（3.11.2 编译/执行/CRLF/幂等/属主保留）。
- F-R2：§5.5-4.0 grep 修复（exited exit=0 命中，running/exitedx 拒绝）。
- F-R3：§5.4-3.5 ss 分离（官方 12 条不误拒，本应用 1 条检出）。
- F-R4–F-R7：marker 最后写+新鲜 inspect、residual 拆分、ACL 已知限制、§3.2 `<TS>` 注记，文本/结构核对通过。
- 自校验戳/整文件哈希一致；runbook 仍为草稿、未执行、门禁不变。

**未解除**：
- F1：附录 E 1 条绑定过期（`MANUAL_SAVE_PLAYLIST_JOB.md`）。
- F2：完整窗口未执行（范围限制，非本次缺陷）。

---

## 7. 环境副作用与清理

- NAS 测试前后：`fnmusic-ext.service` `MainPID=1870197`、`NRestarts=0`、`ActiveState=active`、`SubState=running` 不变。
- 只写 `/tmp/fnm-rev5-XXXXXX` 与 `/tmp/fnm-rev5-upload`；测试结束已 `rm -rf`，`ls -d /tmp/fnm-rev5-*` → none。
- 未写 `/vol1`、未改 `.env`/配置/服务、未停启容器/服务；未 commit、未改 runbook/代码。

---

## 8. 关键哈希

| 文件 | SHA-256 |
| --- | --- |
| 被审 runbook（Rev-5，179,069 B/1,943 行） | `0d7b3a4923fa56bcfca9ea4511edf05c2bd19a75f8e57640a40504e6e54d4546` |
| runbook `REV5-HASH-STAMP` 前缀值 | `11c1bb1a690032e7740c6664ddb804c35f1f3d1fde930faa7fa664b2010e75b8` |
| 抽取编辑器 `rev5_editor.py` | `fb773f717312791d1af7900393a71c862dd297f4272e10cf38582e92293090bc` |
| 抽取校验器 `rev5_validator.py` | `ebec1f6a8efbc67de129f2111fa780b8e6b47e16b63460343d4cc621fcb23de0` |
| 抽取 sqlite `rev5_sqlite.py` | `543759d2da2a553ffba9762ca63810d0fb72566a1768ccaa36a07055fed64e6e` |
| 测试 harness `rev5_test.sh` | `734fc351a9c07784d2a740dacf389a4fa924476594b7f347c59980a9e6d2ca02` |
| 附录 E 不一致文档（当前磁盘） | `c3c17774f9a375bf1dced39f75aa5f0f5e19d6fd2b899604218570d27e27649a` |
| 附录 E 表值（旧） | `4b5610ce8bc5e458bfbe9917d29a99764b5e96761340ed9fc8aa441abce0b0cf` |

---

## 9. 本报告自校验

文末 `REV5-REVIEW-HASH-STAMP` 记录 `sha256(该行之前全部字节, UTF-8)`；删除该行后重算即得。本报告只新增，不 commit、不改被审文档/代码。
<!-- REV5-REVIEW-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 7e265fceb0b22cb20cc95d263d1cdbfe68ed7a33f4a44f71074c001fc46dd519 -->
