# runbook Rev-4 限定复核（R2/R3 修复确认 + 片段真机实跑，2026-11-07）

> **复审者**：`native-reviewer`（独立复审者；未参与 Rev-4 撰写/修订，也未参与设备证据采集）。
> **被审版本**：`docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md`；**171,830 B / 1,887 行（LF）**；SHA-256 `74155b9ef7a49d560340b467d3da3ce79f244b8a97469d5780b2404a92ef13a4`；文末 `REV4-HASH-STAMP = f212e9de…3f982`。**独立重算：整文件哈希 = `74155b9e…13a4`、stamp（该行前全部字节）= `f212e9de…3f982`，均与交付一致。**
> **上一版本**：Rev-3 = 1,834 行 / 165,249 B / `6c4ace21…`（stamp `f0eca772…`）；本复审依据 `docs/LIFECYCLE_RUNBOOK_REV3_REVIEW_20261107.md` = `50a00296…`（F-R1/F-R2 重要 + F-R3–F-R6 次要）。
> **方法（本轮含真机实跑）**：本地只读审计 + 在 **NAS `/tmp` 合成目录**上真机执行从 Rev-4 原样抽出的片段（NAS：Python 3.11.2、bash、GNU tar）。**未写 `/vol1`、未执行任何 runbook 设备步骤、未停启服务/容器、未改 `.env`/配置**；所有合成目录已清理（含 fail-closed 提前退出留下的两个目录）。本报告是唯一写入。

---

## 0. 结论速览

**判定：PASS-with-limits（R1/R4–R6 已确认；R2 修复“逻辑正确但按稿不可执行”；R3 采样/比对修复通过，但相邻前置仍有 2 处执行阻断缺陷）。**

- **R2（未通过“按稿可执行”）**：fd 形式、`with os.fdopen` 内、fchmod→fchown→fsync→replace→dir-fsync 的顺序、`hasattr(os,"fchown")` fail-closed **全部正确**；但 **原样抽取的编辑器 heredoc 在目标 Python 3.11.2 上编译失败**：`print(f"… eol={'CRLF' if eol == b'\r\n' else 'LF'} …")` 的 f-string 表达式含反斜杠 → `SyntaxError`（PEP 701 仅 3.12+ 允许）。`.env` 不会被改写；正常窗口在键级校验处 fail-closed REFUSE。把该行做**单行**修补后真机实跑通过：无 TypeError、mode/uid/gid `600/1000/1000` 保留、`export`/行尾注释保留、两键 false、其它键不变、validator KEYS_OK、二次幂等通过。
- **R3（采样/比对修复通过）**：真机合成实跑：三类 `w3.t0/t15.{env,sources-data,cache-logs}.sha256` 均按同一路径生成并比对，正常两轮 rc=0；内容变化 rc=1；缺 `w3` 文件/缺其它采样文件均命中 `REFUSE: 采样文件缺失: <路径>`。**但**：
  - **§5.5-4.0 的 `grep -qx 'status=exited'` 与实际文件格式 `status=exited exit=0` 不匹配** → 真机复现：真实 docker 格式下恒 REFUSE（“容器当前不是 exited”），最终备份无法开始；
  - **§5.4-3.5 的 `ss -x state established | grep -E 'fnmusic-ext.sock|trim_music'` 把官方 `/var/run/trim_music.socket` 也算进去**：当前设备该官方 socket 有 12 条 established 连接（`fnmusic-ext.sock` 为 0）→ 真机原样 A 场景 rc=1；把 `ss` 隔离后 A2 rc=0。真实窗口（unit 已停、官方应用仍在）很可能因此误拒。
- **R1 未回归**：停服后对 `BK_PRE` 仍全部只读（`sha256sum -c`、MANIFEST 哈希比对、freeze diff）；B12 自描述仍只在停服前生成；全文件扫描无停服后写 `BK_PRE` 的路径。
- **R4/R5/R6 通过**：B5 命令块列、B12 表外说明、manifest 位置统一；非秘密解包 tar rc + N/A 计数校验；`grep -c` 多行值修复；§5.2-7 措辞已含 freeze/selftest。
- **附录 E 18/18 哈希/字节/行数与当前磁盘一致**（含 Rev-3 复审 `50a00296…`/17,494/127、Rev-2 复审 `2ca80471…`/17,985/142、schema `dec0aabb…`/119,555/1,187、schema 复审 `ee8b0437…`/10,953/109），整文件哈希与文内 stamp 两种语义区分正确。
- **仍为草稿/未执行，§0.1/§0.2/P7/G0-P5 门禁不变**；本复审不构成任何授权。

---

## 1. 逐项核验表

| 项 | Rev-4 声称 | 独立核验（静态 + 真机实跑） | 结论 |
| --- | --- | --- | --- |
| **R2a fd 形式** | `os.fchmod(os.fchown)` 第一参改 fd、在 `with` 内、最终 fsync 之前、无 `fchown` fail-closed | §5.3:1085-1093：`hasattr(os,"fchown")` → `with os.fdopen(fd,"wb")` → `os.fchmod(fh.fileno(),…)` → `os.fchown(fh.fileno(),…)` → `os.fsync(fh.fileno())` → `os.replace` → 目录 fsync；静态正确；全文件无其它“路径当 fd” | **通过（静态）** |
| **R2b 原样 heredoc 可执行** | （声称已修复） | **真机 Python 3.11.2 编译失败**：`SyntaxError: f-string expression part cannot include a backslash`（doc :1104，block 第 50 行）；编辑器 rc=1、`.env` 未替换、validator REFUSE（fail-closed）。本地 3.14 不报错，故此前的本地合成用例未暴露 | **不通过（目标平台）** |
| **R2c 单行修补后语义** | 保留 mode/uid/gid、export/注释、键级正确 | 仅把 :1104 的 print 改为预计算 eol 名（不动其它逻辑），NAS 真机实跑：editor rc=0、stderr 空、mode/uid/gid `600/1000/1000` 保留、`export … # keep me` 保留、两键 false、`OTHER_KEY` 不变、validator KEYS_OK、二次幂等通过、无 tmp 残留 | **通过（逻辑正确）** |
| **R3a 采样命名/比对/存在性** | 统一 `w3.<tag>.{env,sources-data,cache-logs}.sha256`；比对读 `w3.t0.$f`/`w3.t15.$f`；缺失即 REFUSE 并打印路径 | 真机 A2（仅把 `ss` 置空以隔离生产官方连接）：rc=0；生成 `w3.t0.env.sha256`/`w3.t0.sources-data.sha256`/`w3.t0.cache-logs.sha256` 等 6 个，diffs 为空；B（T0 后改 sources-data）rc=1；C（删 `w3.t0.env.sha256`）rc=1 且打印该路径；D（删 `fd-holders.t0.txt`）rc=1 且打印该路径 | **通过** |
| **R3b marker 绑定** | marker 含 window/FinishedAt；§5.5-4.0 校验 | marker 精确行场景：window/FIN 匹配 rc=0、FIN 不匹配 rc=1；**但** `grep -qx 'status=exited'` 对真实 `status=exited exit=0` 恒失败（真机复现 rc=1） | **不通过（status 行）** |
| **R3c ss 断言范围** | `ss -x state established` 过滤 LISTEN、无扩展 socket 连接 | 生产当前 `trim_music` 官方 socket 有 12 条 established、`fnmusic-ext.sock` 0 条；原样 A 场景因此 rc=1；断言把官方应用连接也算作“扩展未静止” | **不通过（范围过宽）** |
| **R1** | 停服后零写 `BK_PRE` | §4.3:779-792 B12（排除自描述、freeze、selftest）；§5.5:1391-1400 只读三重复核；4.6（:1417-1422）再 diff；全文件 `$BK_PRE` 写路径扫描无停服后写入 | **通过（未回归）** |
| **R4** | B5 列、B12 说明、manifest 位置 | B5 列已为 `B4–B5（同一命令块；B5 不含独立块）`（:546）；B12 表外说明（:554-556）；§4.4 与命令一致（BK 内 + EVID 副本；manifest.hash 只存哈希值） | **通过** |
| **R5** | tar rc、N/A 计数 | B3 `if ! tar …; then REFUSE`（:685-690）；§4.5 解包 rc（:858-863）；N/A 仅当 `data.nonsecret.count.txt=0`（:878-880） | **通过** |
| **R6** | `grep -c` 多行值 | B1:642-645 / B11:768-771 吞退出码+取首行，`[ -gt ]` 不再收到 `"0\n0"` | **通过** |
| **附录 E** | 18 份绑定 | **18/18 SHA-256 + 字节 + 行数全部与磁盘一致**；stamp/整文件哈希独立复算一致；两种哈希语义区分正确 | **通过** |
| **⑩ 边界** | 草稿/未执行/门禁不变 | 文首状态、附录 C、§8 均保持；§0.1/§0.2/P7/G0-P5 仍 BLOCKED | **通过** |

---

## 2. 真机实跑记录（NAS `/tmp` 合成目录）

**环境**：`kotomi-root@NAS`，`python3 = 3.11.2`，`/tmp` 空间充足；所有测试目录为 `/tmp/rev4-*`，脚本经 `scp` 上传（避免 PowerShell 管道破坏 UTF-8）。**未写 `/vol1`**；未执行 `systemctl/docker` 变更；测试后 `ls -d /tmp/rev4-*` 为空（含两个 fail-closed 提前退出留下的目录，已手工删除）；复核时 `fnmusic-ext.service` active、`fnmusic-sources` running 未变。

### 2.1 R2：原样 heredoc（失败）与单行修补（通过）
- **原样**：抽取 §5.3:1054-1105 的 heredoc 放入 `run_editor()`，前置快照按 :1047-1049，合成 `.env`：`export FNMUSIC_MANUAL_SAVE_ENABLED=true # keep me`、`export FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=true`、`OTHER_KEY=keep`，`chown 1000:1000`（kurumi:Administrators）、`chmod 600`。
  - 结果：`editor_rc=1`；stderr：`File "<stdin>", line 50 … SyntaxError: f-string expression part cannot include a backslash`；owner 断言 rc=0（文件未变），validator `REFUSE missing=[] not_false=[两个键]` → **fail-closed**。
- **单行修补**：仅把 :1104 的 print 改成 `eol_name = "CRLF" if eol == b"\r\n" else "LF"` + `print("matched_key_lines=%d eol=%s export_style=%s" % (…))`，其余逐字不动。
  - 结果：`editor_rc=0`、stderr 空、`matched_key_lines=2 eol=LF export_style=True`；`MODE_OWNER_PRESERVED=PASS (600/1000/1000 → 600/1000/1000)`、`EXPORT_COMMENT_PRESERVED=PASS`、`SECOND_KEY_FALSE=PASS`、`OTHER_KEY_UNCHANGED=PASS`、`NO_TRUE_LEFT=PASS`、`TMP_LEFTOVER=PASS`；validator KEYS_OK；二次运行通过；5 s 并发检测 rc=0。

### 2.2 R3：采样/比对（通过）与 marker/ss（暴露缺陷）
- **A2 正常**（仅把 `ss` 置为空实现以隔离生产官方连接，其它逐字）：rc=0；生成 `w3.t0.env/sources-data/cache-logs.sha256` 与 `w3.t15.*`，三类 diff 为空；`fd-holders`/`residual-procs`/`ss` 两轮一致且为空。
- **B 内容变化**：T0 后 3 s 向 `sources-data/a.txt` 追加 → rc=1（`W3/sources-data … 变化`）。
- **C 缺 w3 文件**：删 `w3.t0.env.sha256` → rc=1（`REFUSE: 采样文件缺失: …/w3.t0.env.sha256`）。
- **D 缺其它采样**：删 `fd-holders.t0.txt` → rc=1（`REFUSE: 采样文件缺失: …/fd-holders.t0.txt`）。
- **E marker**：精确 `status=exited` 行 → window/FIN 匹配 rc=0；FIN 不匹配 rc=1；**真实 docker 格式 `status=exited exit=0` → rc=1（“容器当前不是 exited”）**。
- **生产 `ss` 证据**：`ss -x state established` 中匹配 `fnmusic-ext.sock|trim_music` 的 12 条全部是 `/var/run/trim_music.socket`（官方应用），`fnmusic-ext.sock` 为 0；原样 A 因此 rc=1。

### 2.3 全文件同类缺陷扫描
- **路径当 fd**：`grep 'os.(fchmod|fchown|fsync|fstat|…)('` 仅 §5.3 三处，均为 `fh.fileno()`/`dfd`；无其它误用。
- **Python 3.11 兼容**：抽取全部 3 个 `<<'PY'` 块用 NAS `python3 -m py_compile`：block1（SQLite §4.6）OK、block3（validator）OK、**block2（编辑器）FAIL（上述 f-string）**——全文件仅此一处。
- **名字/变量**：`w3.` 全部一致（生成 `w3.<tag>.<f>`、比对 `w3.t0.$f`）；`fd-holders/tmp-workdirs/residual-procs/ss.unix.established` 生成与断言一致；`manifest.hash.txt`/`manifest.sha256-only.txt`/`bkpre.freeze-*` 命名一致；`TS/EVID/REPO/APP/VOL/LIB/STG` 均有定义；`mkdir -p`（:595）已含 `backup/pre`、`backup/final`、`rehearsal`，B12/§5.5 使用前已存在。
- **bash 语法**：15 个 ```bash 块 `bash -n`，14 通过；仅 §3.2 的 `docker ps --filter name=fnm-iso-sources-<TS>` 因 `<TS>` 占位符报语法错（预期占位符，不是代码缺陷）。
- **R1 回归**：`$BK_PRE` 写路径扫描仍无停服后写入。

---

## 3. 发现

### 重要（按稿不可执行）

**F-R1（重要）R2 原样编辑器在目标 Python 3.11.2 上编译失败（f-string 反斜杠），`.env` 不会被改写。**
- 位置：§5.3:1104 `print(f"matched_key_lines={len(seen)} eol={'CRLF' if eol == b'\r\n' else 'LF'} export_style={style_export}")`。
- 证据：NAS Python 3.11.2 `py_compile` 报 `SyntaxError: f-string expression part cannot include a backslash`；原样实跑 `editor_rc=1`、validator REFUSE（fail-closed）；单行修补后全部断言通过。
- 影响：正常窗口 Step 2 必然 fail-closed 拒绝；R2 的属主/模式保留逻辑虽正确但不可达。此前“本地合成用例验证 fd 形式”很可能在 Python 3.12+（本机 3.14）完成，未覆盖目标 3.11。
- 修法：把 eol 名提前计算（`eol_name = "CRLF" if eol == b"\r\n" else "LF"`），print 改用 `%` 或仅用变量；并在目标 Python 3.11 上复跑一次。

**F-R2（重要）§5.5-4.0 的 `grep -qx 'status=exited'` 与实际文件格式 `status=exited exit=0` 不匹配，最终备份恒被 REFUSE。**
- 位置：§5.5:1377（`grep -qx 'status=exited' "$EVID/stop/container.after-stop.txt"`）；文件由 §5.4:1262 `docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}}'` 生成，内容为单行 `status=exited exit=0`。
- 证据：真机 marker 测试：精确 `status=exited` 行 → rc=0；真实 docker 格式 → rc=1（“容器当前不是 exited”）。
- 修法：改用 `grep -q 'status=exited'`（同 §5.4:1263/1330）或 `grep -qE '^status=exited( |$)'`。

**F-R3（重要）§5.4-3.5 的 `ss` 断言把官方 `trim_music` socket 也算入，真实窗口会误拒。**
- 位置：§5.4:1278 `ss -x state established | grep -E 'fnmusic-ext.sock|trim_music'`，断言“无 established 扩展 socket 连接”。
- 证据：当前设备该命令匹配 12 条，全部为 `/var/run/trim_music.socket`（官方应用），`fnmusic-ext.sock` 为 0；原样 A 场景 rc=1，隔离 `ss` 后 A2 rc=0。真实窗口 unit 已停、官方应用仍在，官方连接很可能保留。
- 修法：只 grep `fnmusic-ext.sock`（扩展 socket），或把 `trim_music` 连接单独记录为“官方应用预期连接”不参与扩展静止判定。

### 次要

**F-R4（次要）marker 写入顺序：先写 marker（:1328-1329）再做最后的容器状态检查（:1330-1331），且该检查读的是采样前（:1262）的旧 `container.after-stop.txt`，不是新鲜 inspect。** 若该检查失败或采样期间容器重启，marker 已落盘；重入时可能被 §5.5 误用。建议：marker 最后写；写前重新 `docker inspect` 并校验 `status=exited` 与本实例 `FinishedAt`；失败则不写 marker。

**F-R5（次要）residual-procs 过滤按 `$REPO` 过滤会漏掉不含 repo 路径的容器 `uvicorn app:app`。** 生产证据：5 个匹配 `takeover.py|webui_gateway.py|uvicorn app:app` 的进程中，仅 3 个含 `/vol1/@appcenter/fnmusic-ext/repo`；两个容器 `uvicorn app:app --host 0.0.0.0 --port 8004/8002`（kurumi）不含该路径。当前容器 exited 断言可覆盖，但若容器 stop 部分失败/进程残留，该过滤会漏检。建议：按 cgroup/容器归属或允许 `uvicorn app:app` 行参与匹配，并在注释中说明。

**F-R6（次要）R2 的 `os.replace` 只保留 mode/uid/gid，不保留 ACL/xattr；`/vol1` 的 trimacl 恰是实际访问机制。** 编辑器用 `mkstemp` 新 inode + replace，旧 `.env` 上的 ACL 条目不会自动复制；runbook 的 owner/mode 断言（:1149-1155）不比较 `getfacl`。建议：编辑前 `getfacl`、编辑后比较（或显式声明 ACL 变化并接受）；否则“属主/属性保留”在 trimacl 场景下不完整。

**F-R7（次要）§3.2 的 `docker ps --filter name=fnm-iso-sources-<TS>` 含 `<TS>` 占位符，`bash -n` 会报语法错。** 这是草案占位符的预期行为，但建议统一写成 `<TS>` 并加注释，避免执行者直接复制粘贴时误解析为重定向。

---

## 4. 本审查解除 / 未解除清单

**解除（本轮已独立核实）**：
- R1 未回归：停服后对 `BK_PRE` 只读三重复核、B12 仅停服前生成、全文件无停服后写路径。
- R4/R5/R6 修复成立（B5/B12/manifest、tar rc/N/A、grep -c 多行、§5.2-7）。
- R3 的采样命名/比对/存在性修复**真机通过**（A2 rc=0、B/C/D rc=1 符合预期）。
- R2 的 fd/owner/export/comment/键级逻辑**在单行修补后真机通过**（静态 fd 形式与顺序亦正确）。
- 附录 E 18/18 绑定与当前磁盘一致；Rev-4 整文件哈希与 stamp 独立重算一致；两种哈希语义正确。
- 文档仍为草稿/未执行；§0.1/§0.2/P7/G0-P5 门禁未被削弱。

**未解除**：
- **F-R1**：原样编辑器在目标 Python 3.11.2 上 SyntaxError（R2 修复确认未通过）。
- **F-R2**：§5.5-4.0 `grep -qx` 与真实格式不匹配 → 最终备份恒 REFUSE。
- **F-R3**：3.5 `ss` 断言含官方 socket → 真实窗口可能误拒。
- F-R4–F-R7 次要项。
- §0.1/§0.2 仍 BLOCKED；无隔离环境（候选 C）、无停机前备份/恢复演练、无回滚目标/降级证据、无 G0-P5；runbook 未执行。
- 本复审不构成任何部署、备份、停启、安装或回滚授权。

---

## 5. 执行前最小修复清单

1. **F-R1**：修 :1104 的 f-string（提前算 `eol_name`），在 **NAS Python 3.11.2** 上复跑原样 heredoc 与完整 §5.3 片段（合成 `.env`）。
2. **F-R2**：§5.5:1377 改 `grep -q 'status=exited'`（或 `grep -qE '^status=exited( |$)'`）。
3. **F-R3**：§5.4:1278 只 grep `fnmusic-ext.sock`；官方 `trim_music` 连接另行记录。
4. F-R4：marker 最后写，写前新鲜 inspect；F-R5：residual 过滤按 cgroup/容器归属；F-R6：ACL 捕获/比较或显式接受；F-R7：占位符标注。

> 以上修复后仍需真实隔离环境（候选 C）取得 §0.1-3 证据并经独立复核，才能谈 §0.1 PASS。本清单不构成任何执行授权。

---

## 6. 本报告哈希说明

- 本报告写入完成后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REPORT-HASH-STAMP` 行给出 `sha256(本行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）。
- 被审 Rev-4：1,887 行 / 171,830 B / `74155b9e…13a4`；其 `REV4-HASH-STAMP` `f212e9de…3f982` 与整文件哈希均已在 §0 独立核验。
- 真机实跑仅使用 `/tmp/rev4-*` 合成目录；测试后已清理（`ls -d /tmp/rev4-*` 为空），未写 `/vol1`、未停启服务/容器。
<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = b894c30ea5c23620e369d1cf0e14c76f6c8bd4c7026b0a70a8260b8f30866355 -->
