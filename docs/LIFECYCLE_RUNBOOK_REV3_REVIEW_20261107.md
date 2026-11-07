# runbook Rev-3 限定复核（R1/R2 确认，2026-11-07）

> **复审者**：`native-reviewer`（独立复审者；未参与 Rev-3 撰写/修订，也未参与设备证据采集）。
> **被审版本**：`docs/LIFECYCLE_BACKUP_ROLLBACK_RUNBOOK_DRAFT_20261107.md`；**165,249 B / 1,834 行（LF）**；SHA-256 `6c4ace211f19d47a8d794506e2919eabe9646125afb75e4b19b5705a2a815ab9`；文末 `REV3-HASH-STAMP = f0eca772…98d7f`。**独立重算：整文件哈希 = `6c4ace21…5ab9`、stamp（该行前全部字节）= `f0eca772…98d7f`，均与交付一致。**
> **上一版本**：Rev-2 = 1,684 行 / 151,576 B / `76867809…`（stamp `dd39bb5b…`）；本复审依据 `docs/LIFECYCLE_RUNBOOK_REV2_REVIEW_20261107.md` = `2ca80471…`（R1–R6）。
> **方法**：本地只读 + 本机合成用例。重算 Rev-3/附录 E 全部哈希、字节与行数；逐行读 R1–R6 修订段并扫描 `$BK_PRE` 全部写路径；在**本机**用合成 `.env` 复现 R2 的键级判定（Python 3.14）与 `os.fchmod/os.fchown` 调用行为。**未访问设备、未执行任何设备/备份/停启命令、未修改被审文档或代码**；唯一写入为本报告。（本机合成用例仅为工具语义验证，不是设备证据。）
> **并发核对**：`OPEN_DECISIONS_20261107.md` 当前 = `7573e743…`/7,781 B/70 行，与附录 E 一致（Rev-2 后未再变）。

---

## 0. 结论速览

**判定：PASS-with-limits（R1 已闭环；R4–R6 已修复；R2 与 R3 的修复项“未通过”，按稿不可执行）。**

- **R1 闭环（重要修复已成立）**：B12 只在停服前生成自描述（MANIFEST 排除自身两个自描述文件，保证可重复 `sha256sum -c`）+ `manifest.hash` + EVID 副本 + freeze 快照 + selftest；§5.5 停服后对 `BK_PRE` **只读三重复核**（`sha256sum -c`、MANIFEST 哈希比对、freeze diff），任一不一致 REFUSE；只有 `BK_FINAL` 生成自描述；pre-vs-final filelist diff 排除自描述文件。**逐命令扫描未发现任何停服后写 `BK_PRE` 的路径**。
- **R2 修复“半通过”**：键级结构化判定本身**正确**（本机复现：0 行幂等通过、两键同时改写通过、`KEY = value` 空格归一化通过、非目标键变化/目标键非 false/目标缺失均 REFUSE）；但 **编辑器把 `os.fchmod`/`os.fchown` 的第一参数写成路径 `tmp`（二者要求 fd）→ 运行必抛 TypeError**，`.env` 替换根本不会发生；属主保留（kurumi→root 的修复目标）**不可执行**。正常窗口（ENABLED=true）会在 Step 2 结构化判定处 fail-closed REFUSE，窗口无法继续。
- **R3 修复“未通过”**：T0/T15 两轮采样、容器 `exited` 断言、`ss -x state established`、W3 全对象、marker 与 §5.5 硬前置的**设计**均已落地；但两轮比对循环对 `.env` 使用 `w3.$f.t0.sha256`（f=`.env` → `w3..env.t0.sha256`），而采样实际写的是 `w3.env.t0.sha256` → `diff` 找不到文件 → **3.5 恒 REFUSE**，marker 永不生成，§5.5 被硬前置永久挡住。
- **R4/R5/R6 通过**（B5 命令块列、B12 表外说明、manifest.hash 位置统一；非秘密项内容级清单与比对；`grep -c` 多行值修复）。
- **附录 E 绑定 17/17 与当前磁盘一致**（含新增 Rev-2 复审 `2ca80471…`、schema Rev-3 `dec0aabb…`、schema Rev-3 复审 `ee8b0437…`），整文件哈希与文内 stamp 两种语义已正确区分。
- **文档仍为草稿/未执行，§0.1/§0.2/P7/G0-P5 门禁不变**；本复审不构成任何授权。

---

## 1. 逐项核验表

| 项 | Rev-3 声称 | 独立核验 | 结论 |
| --- | --- | --- | --- |
| **R1** | B12 停服前一次生成 MANIFEST/manifest.hash/freeze/selftest；停服后对 `BK_PRE` 只读（`sha256sum -c` + MANIFEST 哈希比对 + freeze diff）；只有 `BK_FINAL` 生成自描述；pre-vs-final diff 排除自描述 | B12（§4.3:776-789）：MANIFEST 排除 `MANIFEST.sha256` 与 `manifest.hash.txt`（可重复校验）、`manifest.hash.txt` 只存哈希值、复制到 `EVID/backup/pre/`、freeze `-printf '%p\t%s\t%T@'`、selftest；§5.5-4.2b（1341-1350）三重复核齐全；§5.5-4.5（1359-1366）只对 `BK_FINAL` 生成；4.4 filelist diff（1356-1358）排除两个自描述；**全文件 `$BK_PRE` 写路径扫描：仅 B12 停服前写入，§5.5 全部为读/比对** | **通过（闭环）** |
| **R2a** | `.env` 编辑器 `fchmod`+`fchown` 保留原 mode/uid/gid → fsync → `os.replace` → fsync 目录 | 代码 `os.fchmod(tmp, st.st_mode & 0o7777)`（:1075）与 `os.fchown(tmp, st.st_uid, st.st_gid)`（:1076）**把路径传给要求 fd 的 API**。本机 Python 复现：`os.fchmod("path", …)` → `TypeError: 'str' object cannot be interpreted as an integer`；`os.fchown` 同样要求 fd（Windows 上更无该属性）。异常被 `except BaseException` 捕获并 re-raise → 脚本非 0 退出、`.env` 不被替换；后续键级校验在正常窗口必然 REFUSE | **不通过（不可执行）** |
| **R2b** | 键级结构化校验：0 行幂等、两键≤4 行、`KEY = value` 归一化、目标键 false、其它键不变；断言 mode/owner；失败走 `restore_env` | 独立抽取校验器（:1104-1128）本地复现 6 例：A 两键已 false → `KEYS_OK changed=none(idempotent)`；B 两键 true→false → 通过；C `export KEY = true # c` → 通过（空格/注释/export 均归一化）；D 非目标键变化 → REFUSE；E 目标键仍 true → REFUSE；F 目标键缺失 → REFUSE。owner/mode 断言（:1133-1139）与 `restore_env`（:1093-1098）逻辑正确 | **通过（校验器本身）** |
| **R2c** | 属主保留目标：不再 kurumi→root | 设计正确；但因 R2a 的 TypeError **从未执行到 replace**，该目标在当前文本下不可达（仅幂等窗口下文件未变、检查“碰巧通过”，属主保留代码未被实际验证） | **不通过（依赖 R2a 修复）** |
| **R3** | T0/T15 两轮采样覆盖 fd/`/tmp`/`ss -x state established`（过滤 LISTEN）/.env/sources-data/recommend_cache/logs；容器 exited 断言；marker `3.5.all-writers-static.PASS` 为 §5.5 硬前置 | 设计全部落地：容器 exited 断言（1243-1245）、采样函数按对象取两轮（1247-1271）、`ss -x state established`（1257）、两轮 diff（1272-1288）、marker（1292-1294）、§5.5-4.0 校验 marker（1326-1328）。**但**：采样写 `.env` 为 `w3.env.$tag.sha256`（1260），两轮比对用 `w3.$f.t0.sha256` 且 `for f in .env …`（1273-1275）→ 实际路径 `w3..env.t0.sha256`，`diff` 因文件不存在返回非 0 → **恒 REFUSE**，marker 不可达 | **不通过（命名缺陷）** |
| **R4** | B5 命令块列修正；B12 表外说明；manifest.hash 位置统一 | §4.1 B5 行命令块列已为 `B4–B5（同一命令块；B5 不含独立块）`（546）；B12 作为表外包装步骤说明（554-556）；§4.4（811-814）明确自描述在 BK 内、EVID 侧保留副本，命令（778-784、1359-1364）一致 | **通过** |
| **R5** | B3 非秘密项内容级清单；§4.5 内容级比对 | B3 新增 `data.nonsecret.sha256`（682-690，排除秘密项）；§4.5 对解出树重新生成并 `diff`（864-872，空集记 N/A） | **通过**（留 R5-1 次要：非秘密解包未显式查 tar rc） |
| **R6** | B1/B11 `grep -c \|\| echo 0` 多行值修复 | B1（642-644）与 B11（765-768）均改为「吞退出码 + 默认 0 + 取首行」，`[ -gt ]` 不再收到 `"0\n0"` | **通过** |
| **附录 E** | 自校验戳 + 证据哈希绑定 | 整文件 `6c4ace21…` 与 stamp `f0eca772…` 独立重算一致；**17/17 文档的 SHA-256 与字节数全部与当前盘一致**，新增三份（Rev-2 复审 17,985 B、schema Rev-3 119,555 B、schema Rev-3 复审 10,953 B）及行数 142/1,187/109 均核对一致；整文件哈希 vs 文内 stamp 语义区分正确 | **通过** |
| **⑩ 边界** | 仍为草稿/未执行/门禁不变 | 文首状态与 §1.3、附录 C 保持“未执行”；§8 六条映射未削弱；§0.1/§0.2/P7/G0-P5 仍 BLOCKED | **通过** |

---

## 2. 独立验证记录（本地只读 + 本机合成用例）

### 2.1 哈希/戳
- Rev-3 整文件 `6c4ace21…5ab9`（= 交付值）；`REV3-HASH-STAMP` 行前字节 SHA-256 = `f0eca772…98d7f`（= 交付值）。
- 附录 E 的 17 份文档：SHA-256 + 字节数全部一致；三份新增文档行数（142 / 1,187 / 109）一致；`OPEN_DECISIONS` 未再变化。

### 2.2 R1：`BK_PRE` 写路径全扫描（重点）
- 提取 Rev-3 全部 `$BK_PRE` 出现位置并分类：预停段仅 `BK=$BK_PRE` 赋值（:593）与 EVID 记录（:597）；B12（:778-789）在**停服前**写 `BK_PRE` 的 MANIFEST/`manifest.hash.txt` 并复制到 EVID；§4.5 演练（:845-854）只读；§5.5 全部为比较/读取（:1330-1372）。
- §5.5 中无任何 `> "$BK_PRE…"`、`>>`、`cp … "$BK_PRE"`、`mv`、`rm`、`touch`、`install`、`tee`、`sed -i`、`tar -x`、`chmod/chown` 指向 `BK_PRE` 的语句；`find "$BK_PRE"` 只用于 `-printf`/diff；`( cd "$BK_PRE" && sha256sum -c … )` 只读。
- 三重复核：①`sha256sum -c MANIFEST.sha256`；②`MANIFEST.sha256` 哈希 == 停服前 EVID 记录；③freeze（mtime+size）前后 diff 为空。B12 生成时排除两个自描述文件，selftest 可重复；§5.5 末尾 4.6 再 diff 一次。
- **结论：R1 在命令面闭环**（B12 自描述可重复校验 + 停服后零写入 + 三重复核）。残留限制（非缺陷）：freeze/MANIFEST/EVID 副本同处一个可写卷，只能证明“未被本窗口/意外写入”，不构成对外部恶意篡改的密码学锚定（超出本草稿范围）。

### 2.3 R2：编辑器与校验器分别验证
- **编辑器**：本机 Python 3.14 对 `os.fchmod("path", 0o600)` 复现 `TypeError: 'str' object cannot be interpreted as an integer`；`os.fchown` 的 CPython 签名同样要求 fd（目标 Linux 3.11 行为一致；Windows 无该属性）。故 :1075 先抛异常，:1076 不可达，`os.replace` 不会发生 → `.env` 不被改写。
- **校验器**：抽取 :1104-1128 原样逻辑，6 组合成用例结果见 §1 R2b（A–F 全部符合声称语义）。
- **失败路径**：正常窗口下编辑器失败后，结构化校验发现目标键仍为 true → `REFUSE` → `restore_env` 恢复 → `exit 1`；**fail-closed，无静默改坏**，但 Step 2 不可完成。幂等窗口（两键已 false）下编辑器失败被“文件未变”掩盖，检查通过——这不能证明属主保留有效。

### 2.4 R3：采样/比对路径追踪
- 采样写入：`w3.env.$tag.sha256`（:1260）、`w3.sources-data.$tag.sha256`（:1262）、`w3.cache-logs.$tag.sha256`（:1264）。
- 两轮比对：`for f in .env sources-data cache-logs; do diff -u "$EVID/stop/w3.$f.t0.sha256" …`（:1273-1275）。`f=.env` 时路径为 `w3..env.t0.sha256`，与 `w3.env.t0.sha256` 不一致 → `diff` 以“文件不存在”返回非 0 → `|| { REFUSE; exit 1; }` 触发，3.5 恒失败，marker 永不生成，§5.5 的硬前置恒不满足。
- 其余 R3 设计点（容器 exited 断言、`ss -x state established`、W3 四类对象两轮、fd/tmp/residual 两轮、marker 内容/路径与 §5.5 一致）经核对均正确。

### 2.5 R4/R5/R6 细节
- R4：B5 行已改 `B4–B5（同一命令块；B5 不含独立块）`；B12 已作为表外步骤说明；§4.4 与 B12/§5.5 的 manifest 位置一致（BK 内 + EVID 副本；`manifest.hash.txt` 只存哈希值）。
- R5：B3 生成 `data.nonsecret.sha256`（排除 `.env*`/`sources-data*`）；§4.5 重新解出并逐行 `diff`（空集 N/A）。
- R6：B1/B11 已用 `|| true` + `${var:-0}` + `${var%%$'\n'*}` 取首行，`[ -gt ]` 不再收到多行值。

---

## 3. 发现

### 重要（按稿不可执行）

**F-R1（重要）R2 的 `.env` 属主/模式保留不可执行：`os.fchmod`/`os.fchown` 第一参数传了路径而非 fd。**
- 位置：§5.3 :1075-1076。
- 证据：本机 Python 复现 `TypeError: 'str' object cannot be interpreted as an integer`（`os.fchmod`）；`os.fchown` 同为 fd 签名（Windows 无此属性）。脚本在 :1075 抛异常 → `except` 清理 tmp → re-raise → `.env` 不替换。
- 影响：正常窗口（总开关 true）Step 2 键级校验必然 REFUSE（fail-closed，不会写坏 `.env`），但窗口无法推进；R2 声称的“kurumi→root 属主保留”从未被执行。幂等窗口下因文件未变而被掩盖。
- 修法：在 `with os.fdopen(fd, "wb") as fh:` 内先写入/刷新，再 `os.fchmod(fd, st.st_mode & 0o7777)`、`os.fchown(fd, st.st_uid, st.st_gid)`，**之后** `os.fsync(fh.fileno())` 再关闭；或改用 `os.chmod(tmp, …)`/`os.chown(tmp, …)` 并在 replace 前对文件再 fsync 一次（元数据在 fsync 之后修改，当前顺序有崩溃一致性缺口）。建议在隔离环境对合成 `.env` 做一次脚本冒烟（非设备）再定稿。

**F-R2（重要）R3 停容器后两轮比对的 `.env` 文件名不匹配，导致 3.5 恒失败、marker 永不生成。**
- 位置：采样 :1260（`w3.env.$tag.sha256`）vs 比对 :1273-1275（`w3.$f.t0.sha256`，`f=.env`）。
- 影响：`diff` 对不存在的 `w3..env.t0.sha256` 返回非 0 → `REFUSE: W3/.env 在 0s 与 +15s 之间变化` → 3.5 恒失败；`3.5.all-writers-static.PASS` 永不生成；§5.5-4.0 硬前置恒不满足 → 整个窗口不可执行。
- 修法：把循环改为 `for f in env sources-data cache-logs`（与生成名一致），或把生成名改为 `w3.dotenv.$tag.sha256` 并同步比对；建议用 `set -u` + 存在性断言（`[ -f … ] || REFUSE`）避免同类路径打错静默变成 diff 错误。

### 次要

**F-R3（次要）R3 `residual-procs` 全机 grep 可能误伤无关进程。** :1266-1267 对全机 `ps` grep `takeover.py|webui_gateway.py|uvicorn app:app`，其它 checkout/临时实验的同名进程会造成 false REFUSE（保守但可能卡窗口）；建议按 `$REPO` 路径过滤 argv。另：marker 未绑定本次窗口的容器 stop 时间/实例；若窗口中断后遗留旧 marker，重入时需先核验其时效（可在 marker 中写入 `container FinishedAt` 或 stop 时间并校验）。

**F-R4（次要）R2 元数据 fsync 顺序（修复 F-R1 后仍需注意）。** 当前先 `fsync(fh)` 再 `fchmod/fchown`，元数据修改发生在 fsync 之后；崩溃时 `mode/owner` 可能未落盘。修 F-R1 时应把 fchmod/fchown 放在最终 `fsync(fd)` 之前，或 replace 前再 fsync 文件。

**F-R5（次要）R5 非秘密解包未显式检查 tar 退出码。** B3 中 `tar -xzf … -C "$NSTMP"`（:684）与 §4.5 解包未加 `|| exit`；若解包失败可能得到空/部分清单，而 §4.5 把空清单当 N/A（:870-871）。建议对解包 rc 显式 REFUSE，或仅当 `data.nonsecret.count.txt=0` 且归档确有 0 个非秘密项时才允许 N/A。

**F-R6（次要）§5.2 第 7 条仍只写「含 B12 MANIFEST」，未列 freeze/selftest。** :1008 可补「B12 MANIFEST + manifest.hash + freeze + selftest」，与 §4.3/§4.4/§5.5 的一致表述对齐（纯文档一致性）。

---

## 4. 本审查解除 / 未解除清单

**解除（本轮已独立核实）**：
- **R1 闭环**：停服前一次生成自描述与冻结、停服后只读三重复核、`BK_FINAL` 独立生成、pre-vs-final diff 排除自描述；逐命令扫描未发现停服后写 `BK_PRE` 的路径；B12 排除自描述文件使 `sha256sum -c` 可重复。
- R4/R5/R6 修复成立；附录 E 17/17 绑定与当前磁盘一致（含两种哈希语义区分）；Rev-3 整文件哈希与 stamp 独立重算一致。
- 文档仍为草稿/未执行；§0.1/§0.2/P7/G0-P5 门禁未被削弱。

**未解除**：
- **R2 属主/模式保留不可执行**（F-R1）——按稿 Step 2 在正常窗口 fail-closed 拒绝，不能认为 R2 修复完成。
- **R3 采样恒失败**（F-R2）——marker 不可达，§5.5 被硬前置永久挡住。
- R3 误伤/时效（F-R3）、R2 元数据 fsync 顺序（F-R4）、R5 解包 rc（F-R5）、文档措辞（F-R6）。
- §0.1/§0.2 仍 BLOCKED；无隔离环境（候选 C）、无停机前备份/恢复演练、无回滚目标/降级证据、无 G0-P5；runbook 未执行。
- 本复审不构成任何部署、备份、停启、安装或回滚授权。

---

## 5. 执行前最小修复清单

1. **F-R1**：`os.fchmod/os.fchown` 改为 fd 形式（在 `with` 内、最终 fsync 之前），或 `os.chmod/os.chown(tmp)` + 额外 fsync；对合成 `.env` 冒烟验证 mode/uid/gid 保留（非设备）。
2. **F-R2**：修 R3 两轮比对的 `.env` 文件名（`env` vs `.env`），并加 `[ -f … ] || REFUSE` 防同类打错；回归验证 3.5 marker 可达。
3. F-R3–F-R6：residual-procs 按 repo 过滤、marker 绑定时效；fsync 顺序；非秘密解包 rc；§5.2 措辞。

> 以上修复后仍需真实隔离环境（候选 C）取得 §0.1-3 证据并经独立复核，才能谈 §0.1 PASS。本清单不构成任何执行授权。

---

## 6. 本报告哈希说明

- 本报告写入完成后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REPORT-HASH-STAMP` 行给出 `sha256(本行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）。
- 被审 Rev-3：1,834 行 / 165,249 B / `6c4ace21…`；其 `REV3-HASH-STAMP` `f0eca772…` 与整文件哈希均已在 §2.1 独立核验。
<!-- REPORT-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 8f4fe2aca1ac49eb7fa149bb1a7ca8a1dd144065bace1cacfc8b11dcaa329e6a -->
