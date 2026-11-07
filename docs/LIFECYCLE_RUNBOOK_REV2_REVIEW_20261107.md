# runbook Rev-2 限定复核（F1–F12 修复确认，2026-11-07）

> **复审者**：`native-reviewer` —— 独立复审者，未参与 Rev-2 的撰写/修订，也未参与设备证据采集。
> **被审版本**：`docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md`；**151,576 B / 1,684 行**；SHA-256 `768678093bae36c8b27eef05dcd64b9ea8fa76c63a5af7571614b0859034f7eb`；文末 `REV2-HASH-STAMP` 声称 `dd39bb5b…`。**独立重算：整文件哈希 = `76867809…f7eb`、stamp = `dd39bb5b…a717`，均与交付一致。**
> **上轮版本**：1,327 行 / 117,588 B / `09b6d1fc…9fddb`（本复审依据的上一轮报告 `docs/LIFECYCLE_RUNBOOK_REVIEW_20261107.md` = `17547232…e8e`，F1–F12）。
> **方法**：本地只读。重算 Rev-2/附录 E 全部哈希与行数、通读修订差异段、对照仓库代码；另在**本机**（Git Bash + GNU tar 1.35 + Python 3.14）用**合成文件**复现 F7 的 `.env` 改写/强制 diff 逻辑与 F3 的 GNU tar 流式/排除语义（仅为工具行为验证，**不是设备证据**）。**未访问设备、未执行任何设备/备份/停启命令、未修改被审文档或代码**；唯一写入为本报告。
> **并发核对**：`OPEN_DECISIONS_20261107.md` 当前 = `7573e743…`/7,781 B/70 行，与附录 E 绑定一致（Rev-2 后未被再更新）。

---

## 0. 结论速览

**判定：PASS-with-limits（Rev-2 为实质修订，但仍不可按稿执行）。**

- **F2、F3、F4、F5、F6、F8、F9、F10、F11、F12 已实质修复**（逐项见 §1；非文字敷衍）。
- **F1 部分修复（R1，重要）**：`BK_PRE`/`BK_FINAL` 拆分、防覆盖守卫、B12、证据目录均已落地；但 **§5.5-4.5 的「两份 MANIFEST」循环仍会在停服后写入 `BK_PRE`**，直接违反其自己的不变式，并会让 `BK_PRE` 自描述清单自相矛盾。
- **F7 部分修复（R2，重要）**：原子替换、`export`/行尾注释/换行风格保留、`flock`、失败恢复均已实现且经本机合成用例验证；但**强制 diff 校验在常见合法场景会误拒**（已为 false、两键都存在、`=` 两侧有空格），且 `os.replace` 未保留 `.env` 属主（kurumi→root）。
- 其余为 4 项次要残留（R3–R6）。**§0.1/§0.2 仍 BLOCKED，门禁不变，本复审不构成任何授权。**

---

## 1. 逐项修复核验（F1–F12 + 附录 E + ⑩）

| 项 | Rev-2 声称 | 独立核验（位置/代码/本机复现） | 结论 |
| --- | --- | --- | --- |
| **F1** | 拆 `BK_PRE`/`BK_FINAL`、防覆盖守卫、两份独立 MANIFEST、pre-vs-final diff | 拆分（§4.1:529-535、§5.5:1206-1214）、B12 停前 MANIFEST（§4.3:757-760）、§5.5 守卫与 filelist diff（1210-1227）**已落地**；但 §5.5-4.5（1228-1232）对 `BK_PRE` **重新生成** MANIFEST/manifest.hash.txt → 见 **R1** | **部分修复**（R1） |
| **F2** | §5.3 改为「关开关+在途盘点」；静止断言移到停容器后（15 s×2）；失败不得进入最终备份 | 标题/范围修正（§5.3:976-984）、W3 行更新（§1.4:83）、停容器后 3.5 断言块（§5.4:1151-1188）、失败判据「不得进入 §5.5」（1199）**已实质修复**；采样点措辞/项覆盖有残留（R3） | **实质修复**（R3 次要） |
| **F3** | `.env`/`sources-data` 流式校验；rehearsal 显式 exclude 并断言无明文 | B3 流式摘要（§4.3:665-674）、§4.5 先流式校验再 `--exclude` 解包并断言秘密项不存在（§4.5:809-832）、限制声明（839-840）**已落地**；本机 GNU tar 1.35 实测：`tar -xO … sources-data` 递归输出与显式文件流哈希一致；`--exclude` 组合确实排除 `.env`/`.env.*`/`sources-data/**`（R5 仅剩余非秘密项只比条目不比内容） | **实质修复**（R5 次要） |
| **F4** | 端点改为 6 个（3 GET + 3 POST）；§5.8/§5.9 同步 | 代码 `webui_gateway.py:2475-2482` 实测为 `playlists/snapshot/job` 3×GET + `create/jobs/advance/cancel` 3×POST；§5.8 循环（1306-1316）与 §5.9（1353-1357）已按 6 个更新；方法不匹配→501 的说明与 dispatch 一致；POST 无 body 时 gate 在 body 校验前（`handle_…_create`:2314 vs 2317），关闭态应得 403。**作者对 Lead「4 写+2 读」的纠正正确。** | **实质修复** |
| **F5** | §7 编号空间与 `RUNBOOK-R` 映射、区分 `DESIGN-D1` | §7:1538-1547 明确局部 D1–D12 = `OPEN_DECISIONS` `RUNBOOK-R1..R12`，并明确 `DESIGN-D1` 是扫描窗口（不重启服务）、`RUNBOOK-R1` 是部署窗口（停服务+容器），两次独立决策；映射表（1549-1562）与 `OPEN_DECISIONS` §D 逐项一致 | **实质修复** |
| **F6** | 表/命令一一对应；B2/B6/B9/B11/B8/B4/B5 修正；去 `\|\| true` 与非空校验 | 表新增「命令块」列（§4.1:539-551）；B2 覆盖 `<repo>/<app-ui>/<app-config>`（639-650）；B6 采 `ownership.json`/`deployment`（690-709）；B9 记录 install.log（729-740）；B11 显式判定（746-755）；B8 窗口变量 + `[ -s ]` 非空 + 无 `\|\| true`（719-728）；B4/B5 用 `LIB`/`STG` 变量 + 非空/同设备校验（675-688）**已落地**；残留见 R4/R6 | **实质修复**（R4/R6 次要） |
| **F7** | 保留 `export`/风格、临时文件+fsync+`os.replace`、flock、强制 diff、失败恢复 | 原子改写/flock/恢复（§5.3:991-1078）**已落地**；本机合成用例：`export`+行尾注释+CRLF 均被保留（用例 E/F 通过）。**但强制 diff 校验存在误拒缺陷，且属主未保留 → R2** | **部分修复**（R2） |
| **F8** | 候选 A 补齐目录与全树 manifest | `mkdir -p` 已含 `fake-official/stub-upstream/stub-sources/env`（§3.1:367）；全树 manifest（排除 `.venv-proxy`/`__pycache__`）+ 条目数（369-375）**已修复** | **实质修复** |
| **F9** | 启动失败改为「按 §6.5 处置」，明确不触发 extend rollback | §2.12-3（270-273）已明确：`systemctl start` 失败只使 unit failed，不触发 `extend.sh` rollback，按 §5.8→§6.5 | **实质修复** |
| **F10** | 行号校正（40.0 在 `:758`；`extend.sh` 1.3 在 `:408-413`） | §2.2:132 已写「40.0 在 `takeover.py:758`」；§2.9:224 已写 `extend.sh` 1.3 在 `:408-413` 并说明 `:114-137` 是 owner/`.env` 检查 | **实质修复** |
| **F11** | 修正停服顺序理由 | §2.12-2（265-269）已改为「`restore()` 依赖 official upstream listener，与容器无关」，并说明先停 unit 后 W3 仍活、影响 §5.3 断言范围 | **实质修复** |
| **F12** | 烟雾头请求加 §0.2 标注 | §5.3（1074-1076）与 §5.8（1334-1338）均加「仅烟雾检查、非身份/授权证明、不据此推断 §0.2 PASS」 | **实质修复** |
| **附录 E** | 自校验戳 + 14 份被引证据哈希绑定 | **14/14 文档的整文件 SHA-256、字节数、行数全部与当前磁盘一致**（含 `SCAN 6bef7134…/114285/749`、`RO_ROOTCAUSE 4c013274…/8520/90`、`generation 0c99d3ac…/13780/147`、`OPEN_DECISIONS 7573e743…/7781/70`、上轮复审 `17547232…/29461/202`）；Rev-2 stamp `dd39bb5b…` 与整文件哈希 `76867809…` 独立重算一致 | **符合** |
| **⑩** | 仍为草稿/未执行/门禁不变 | 文首状态（1-26）、§1.3、附录 C 均保持「草稿/未执行」；§8 六条映射与 BLOCKED 结论未削弱；§0.1/§0.2/G0-P5/P7 仍 BLOCKED | **符合** |

---

## 2. 独立验证记录（本地只读 + 本机合成用例）

### 2.1 哈希/戳
- Rev-2 整文件 `76867809…f7eb`（= 交付值）；`REV2-HASH-STAMP` 行前字节 SHA-256 = `dd39bb5b…a717`（= 交付值）。
- 附录 E 14 份文档：SHA-256、字节数、行数**全部一致**；`OPEN_DECISIONS` 自 Rev-2 后未再变化（`7573e743…`/7,781 B/70 行），无可报告的绑定漂移。

### 2.2 F7 逻辑本机复现（从 §5.3 原样抽取 Python 改写 + diff 校验，合成 `.env`）

| 用例 | `.env` 起始状态 | 结果 | 判断 |
| --- | --- | --- | --- |
| A（当前设备） | `ENABLED=true`，PLAYLIST 缺失 | **PASS**（diff 3 行） | 正常 |
| B | 两键**已均为 false** | **REFUSE**（diff 0 行 → 「无变化」） | **误拒（R2）** |
| C | 两键均为 true | **REFUSE**（diff 4 行 > 3） | **误拒（R2）** |
| D | `ENABLED = true`（`=` 两侧空格） | **REFUSE**（bad 提取未归一化空格） | **误拒（R2）** |
| E | `export …=true # comment` + 另一键 | REFUSE（4 行）；但改写结果**保留 export 与行尾注释** | 风格保留 ✓ / 行数误拒 |
| F | CRLF 文件 | **PASS**；改写后仍 CRLF | 风格保留 ✓ |
| G | `ENABLED="true"` | PASS；值改写为不带引号的 `false` | 语义可接受，引号风格丢失（次要） |

### 2.3 F3 GNU tar 语义本机复现（GNU tar 1.35，合成归档）
- `tar -xOzf data.tar.gz sources-data` **递归输出**子文件内容（`S1\nS2\n`），其流哈希与显式列出子文件一致；
- 按 §4.5 的 `--exclude='./.env' --exclude='.env' --exclude='.env.*' --exclude='./sources-data' --exclude='sources-data'` 解包后：`.env`/`.env.bak.1`/`sources-data/**` 均未出现，非秘密项正常解出。
→ F3 的「流式摘要 + 显式 exclude + 断言无明文」实现**技术成立**。

### 2.4 代码核对（仓库工作树）
- `webui_gateway.py:2475-2482` = 6 端点（3 GET + 3 POST），与 Rev-2 引用一致；
- 6 个 handler 均先过 `_manual_save_playlist_gate`（总开关→整歌单开关）；POST handler 的 gate（如 create:2314）在 body 校验（2317、`_manual_save_playlist_json_request`:1583-1588）之前 → 关闭态无 body 也返回 403，符合 §5.8 预期；
- `_manual_save_playlist_post_head`（1570-1580）只解析 head，不读 body，进一步确认上述顺序。

---

## 3. 发现

### 重要

**R1（重要）F1 未完全修复：§5.5-4.5 在停服后重新生成 `BK_PRE` 的 MANIFEST/manifest.hash.txt，违反自身不变式并使 `BK_PRE` 自描述失配。**
- 不变式：§4.1:534「停服后不得再向 `BK_PRE` 写入任何字节」；§4.4:785「`BK_PRE` 在停服后不得再有新写入（比较停止前后目录清单）」；§5.5 预期 1239「`BK_PRE` 内文件 mtime 不得晚于停服时刻」。
- 实际：§5.5-4.5（1228-1232）`for d in "$BK_PRE" "$BK_FINAL"; do ( cd "$d" && find . -type f ! -name MANIFEST.sha256 … > MANIFEST.sha256; sha256sum MANIFEST.sha256 > manifest.hash.txt ) done` —— **对 `BK_PRE` 执行了写操作**。且 `BK_PRE` 在 B12（757-760）已生成 `manifest.hash.txt`，此时 `find` 会把旧的 `manifest.hash.txt` 计入新 `MANIFEST.sha256`，随后又覆盖 `manifest.hash.txt` → 新 MANIFEST 中该条目哈希**立即失配**，`BK_PRE` 自描述不可 `sha256sum -c` 复算；mtime 也晚于停服时刻。
- 影响：§0.1-4 的「停机前独立备份」自描述证据被自身污染；若按稿执行，复核者会看到 `BK_PRE` 被写、mtime 异常。
- 修法：§5.5-4.5 只**校验** `BK_PRE`（`(cd "$BK_PRE" && sha256sum -c MANIFEST.sha256)` + 与停服前复制到 `EVID/backup/pre/` 的 `manifest.hash.txt` 比对），仅对 `BK_FINAL` 生成 MANIFEST；停服后先对 `BK_PRE` 做一次 `find … -printf '%p %T@'` 快照（或 `stat`）作为「无写入」证据，再进入 §5.5。

**R2（重要）F7 未完全修复：强制 diff 校验会误拒合法状态；`os.replace` 未保留 `.env` 属主。**
- 误拒（本机已复现，见 §2.2）：① 两键**已为 false** → diff 0 行被当作「无变化」REFUSE（重启/重跑窗口的合法前置被阻断）；② 两键**都存在且需改**（如第二窗口）→ 4 个 diff 行 > 上限 3 → REFUSE；③ `KEY = value`（`=` 两侧空格）→ `bad` 提取未归一化空格 → REFUSE。现脚本的 `-gt 3` 与「无变化即失败」只适配当前设备「一个键在、一个键缺」这一种初态。
- 属主：`tempfile.mkstemp` 创建的文件属主为执行用户（root），`os.replace` 后 `.env` 由 `kurumi:Administrators` 变为 `root:root`；脚本只 `chmod 600`，未 `fchown` 为原 `st_uid/st_gid`，`env.after-stop.stat` 记录了却无断言。`/vol1` 的 trimacl 可能掩盖功能影响，但属主漂移未被显式接受，容器侧 WebUI（kurumi）后续写 `.env` 的语义应重新论证。
- 修法：改为「解析两键的最终值 == false」的结构化判定（允许 0 行 diff）；允许两键替换共 4 行；`bad` 归一化 `KEY[[:space:]]*=`；`os.fchown(tmp, st.st_uid, st.st_gid)` 后再 replace，并在 after-stat 中断言属主/模式与 before 一致（或显式记录并接受变化）；如要求保留引号/行尾空格，再补风格保持。

### 次要

**R3（次要）F2 残留：采样点措辞/覆盖与命令不一致。**
- §5.4:1184 称「T3/T4（停容器后 15 s 与再 15 s）两个采样点」，但命令是「停容器后立即采基线 → `sleep 15` → 复采」（1164-1172），实际是约 0 s 与 +15 s；建议加第二次 `sleep 15` 复采，或把措辞改为「0 s 与 +15 s」。
- 断言称 `.env / sources-data / recommend_cache / logs` 四类哈希「连续两次一致」，但 `recommend_cache`/`logs` 只在 1166-1167 采了一次、之后未复核；建议纳入 +15 s 复核或从断言中删去。
- `ss -x | grep …`（1162）包含 LISTEN 条目，不能单独证明「无 established 连接」；建议 `ss -x state established` 或过滤 LISTEN。
- `docker inspect … container.after-stop.txt`（1153）只落盘未断言；建议 `grep -q 'status=exited'` 后再采样。
- §5.5-4.1 只检查 `BK_PRE/MANIFEST.sha256` 存在，未硬校验「§5.4-3.5 已通过」（仅靠 §5.4 失败判据 1199 的流程约束）；建议 3.5 通过后写 marker，§5.5 校验 marker。

**R4（次要）F6/证据布局残留：**
- §4.1 表 B5 行（545）「命令块」列写 `B4`（应 `B5` 或 `B4–B5`）；B12（757-760）未列入 §4.1 表（可加一行或注明）。
- §4.4:786 称对 `MANIFEST.sha256` 的哈希写入 `EVID/backup/{pre,final}/manifest.hash.txt`，但 B12/§5.5 实际写入的是 **`BK` 目录内**的 `manifest.hash.txt`；EVID 侧未生成副本/未记录。应统一（建议：BK 内保留自描述，EVID 侧复制并绑定）。

**R5（次要）F3 残留：非秘密项只比「条目」不比「内容」。**
§4.5 步骤 3（823-824）只记录解出的非秘密项列表；通过条件 ④ 也只要求「非秘密项条目与清单一致」。`online_favorites*`/`play_history` 属用户数据，建议对非秘密白名单生成清单并 `sha256sum -c` 比对内容，或明确声明本演练只做条目级校验。

**R6（次要）`grep -c … || echo 0` 产生多行值。**
B1（636）与 B11（747）：当 `.env` 存在但键缺失时，`grep -c` 先输出 `0`（退出码 1），`|| echo 0` 再输出 `0` → 变量为 `"0\n0"`；B1 的 keycounts 行会多出一行，B11 的 `[ "$DB_HITS" -gt 0 ]` 会报 `integer expression expected`（随后走 else，不会 fail-open，但证据文件被污染）。本机已复现。建议 `DB_HITS=$(grep -cE … 2>/dev/null || true); DB_HITS=${DB_HITS:-0}` 并只取首行。

---

## 4. 本审查解除 / 未解除清单

**解除（本轮已独立核实）**：
- F2/F3/F4/F5/F6/F8/F9/F10/F11/F12 **为实质修复**（不是文字敷衍）；F4 的「3 GET + 3 POST」与代码 `webui_gateway.py:2475-2482` 一致，作者对 Lead 误述的纠正正确。
- F7 的原子替换/`flock`/`export`+行尾注释+CRLF 保留/失败恢复**已经实现并经本机合成用例验证**。
- F3 的流式摘要/`--exclude`/无明文断言**技术成立**（GNU tar 语义本机验证）。
- 附录 E 的 14 份证据哈希/字节/行数与当前磁盘**完全一致**；Rev-2 自校验戳与整文件哈希**独立重算一致**；`OPEN_DECISIONS` 无绑定漂移。
- 文档仍为草稿/未执行；§0.1/§0.2/P7/G0-P5 门禁未被削弱。

**未解除**：
- **R1（F1 残留）**：`BK_PRE` 停服后被 §5.5-4.5 写入（含自描述失配）——**按稿执行会破坏 §0.1-4 的停机前独立备份证据**。
- **R2（F7 残留）**：强制 diff 在「已为 false / 两键都存在 / `=` 带空格」时误拒；`.env` 属主未被保留/断言。
- R3–R6 次要残留。
- §0.1/§0.2 仍 BLOCKED；无隔离环境（候选 C）、无停机前备份/恢复演练、无回滚目标/降级证据、无 G0-P5；本 runbook 未执行。
- 本复审不构成部署、备份、停启、安装或回滚授权。

---

## 5. 执行前最小修复清单

1. **R1**：§5.5-4.5 只校验 `BK_PRE`（`sha256sum -c` + 与停服前副本比对），仅对 `BK_FINAL` 生成 MANIFEST；停服后先冻结 `BK_PRE` 的 mtime/文件清单快照作为「无写入」证据。
2. **R2**：把 `.env` 强制 diff 改为「结构化键值判定」（允许 0 行；允许两键替换 4 行；归一化 `=` 两侧空格）；`os.fchown` 保留原属主并在 after-stat 中断言（或显式记录接受变化）。
3. **R3**：补齐第二次 15 s 采样/或修正措辞；把 `recommend_cache`/`logs` 纳入复核；`ss` 过滤 LISTEN；断言容器 `exited`；§5.5 增加 3.5 通过 marker 守卫。
4. **R4/R5/R6**：B5 命令块列改 `B5`；统一 manifest.hash.txt 位置；非秘密项增加内容级校验（或显式声明条目级限制）；修 `grep -c || echo 0`。

> 以上均属命令/判据草案层面。修复后仍需**真实隔离环境（候选 C）**取得 §0.1-3 证据并经独立复核，才能谈 §0.1 PASS；本清单不构成任何执行授权。

---

## 6. 本报告哈希说明

- 本报告写入完成后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REPORT-HASH-STAMP` 行给出 `sha256(本行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）。
- 被审 Rev-2：1,684 行 / 151,576 B / `76867809…`；其 `REV2-HASH-STAMP` `dd39bb5b…` 与整文件哈希均已在 §2.1 独立核验。
<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 6890dfc28616ad80e09ca21bab1547170359c5e5b436ca75dec1692a9c829386 -->
