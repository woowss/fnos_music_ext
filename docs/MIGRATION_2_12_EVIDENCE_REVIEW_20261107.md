# 迁移到 2.12.0 基线：验证证据独立复现（2026-11-07）

> **边界声明：本报告不构成部署/升级/生产迁移授权。** 本文仅独立复现 `docs/MIGRATION_TO_2_12_0_20261107.md` §4/§6 的**验证数字**；结构/冲突解决部分由 `native-reviewer`（task-39）负责，本文不重复。设备（NAS `kotomi`）未做任何变更：未升级、未停启、未改配置；所有设备侧操作只在 `/tmp` 副本中完成。
>
> 复审者：`test-reviewer`（独立复审者，未参与迁移）。对象：`E:\fnmusic-ext-212`，分支 `codex/fnmusic-2.12-baseline`，**最终复现用 HEAD `8b0ca6ef1b8c16b06ba0d3a3c6865727176fada1`**（复核期间 Lead 将 HEAD 从 `406c4d5` 推进到 `8b0ca6e`；`proxy/tests` 相关结论在两个 HEAD 上一致，下文计数以 8b0ca6e 为准）。被审文档 `docs/MIGRATION_TO_2_12_0_20261107.md` = `9033f65c0affef49538900ef97cbfc40faedc6b23bf1a2daa801709b683adb80`（12,931 B / 153 行）。
>
> 方法：本地 `git archive`/工作树打包两棵树 → `scp` 上传 NAS → SHA-256 校验 → `/tmp` 解包 → 用 `/tmp/fnm212-deps`（pytest 9.2.0 / fastapi 0.162.0 / httpx 0.28.1 / pydantic 2.14.3，`import` 复核；系统 python3 无 pytest/fastapi，证明依赖确实来自离线目录）独立复跑。所有命令与原始日志见 NAS `/tmp/fnm212-trv2-logs/`（保留作证据）。

## 0. 快照与资产核对

| 项 | 值 |
| --- | --- |
| 合并后树（迁移基线） | HEAD `8b0ca6e`，VERSION `2.12.0-8`，413 个跟踪文件；本次上传 tarball SHA-256 `6342993716a14da51c75e1d565efc55f0a279df81159c4a37b41ed8a8cd4277a` |
| 纯净上游树 | tag `v2.12.0` = `71ade6b`，VERSION `2.12.0-8`，262 个跟踪文件；tarball SHA-256 `8d65e3a83ab1acb18da2b486bb4d2811bd5670861c4912e1f50da048263c3e2` |
| NAS 现有 `/tmp/fnm212` | **已过期**（缺 `docs/MIGRATION_TO_2_12_0_20261107.md`，与 Lead 说明一致）→ 已自行重打包上传，未使用该副本 |
| NAS 现有 `/tmp/fnm212-pristine` | VERSION `2.12.0-8`；`proxy/tests` 72 个 `.py` 文件与 `git archive v2.12.0` 逐文件 SHA-256 一致 → 内容可信（但本轮改用自行打包的纯净树） |
| NAS 现有 `/tmp/fnm212-deps` | pytest 9.2.0 / fastapi 0.162.0 / httpx 0.28.1 / pydantic 2.14.3，与报告 §6 环境声明一致 |
| 环境 | kotomi，内核 `6.18.18.c1168-trim`，Debian 12，Python 3.11.2；本地 Node `v24.18.0`（NAS 无 node） |

> **并发工作区说明**：复核打包快照（17:45）之后、本报告完成前，工作区出现**并行任务**的未提交改动（`proxy/app.py`、`proxy/tests/test_maintenance_startup.py`，mtime 18:00/18:02，内容为维护模式不降权的修复），**非本复审产生、也未纳入本报告任何数字**。本报告所有计数绑定已提交 HEAD `8b0ca6e` 的干净树快照；复审者除新增本报告文件外，未修改任何被审文件或代码。

## 1. 计数总表（独立复跑）

| 套件 | 合并后树（8b0ca6e） | 纯净 v2.12.0 | 报告声明 | 判定 |
| --- | --- | --- | --- | --- |
| `proxy/tests`（同上 3 个 ignore） | **42 failed / 3128 passed / 3 skipped**（192.44s） | **42 failed / 1726 passed / 3 skipped**（176.17s） | 合并 42/3128/3 ✓；纯净 42/**1776** ✗ | 合并计数**已复现**；纯净 passed **不一致**（见 F2） |
| └ FAILED 列表 `comm` 比对 | 42 条，`cmp` 逐字节相同 | 42 条 | 「失败集逐条完全一致（0 差异）」 | **已复现**（F1） |
| └ 收集总数 | 3173 | 1771 | 通过数差 +1352 | 实际差 **+1402**（不一致，F2） |
| `webui-service` | **8 failed / 452 passed / 2 skipped** | **8 failed / 451 passed / 2 skipped**；两者 FAILED 集合逐条相同 | 合并 **7 failed / 330 passed**；纯净 8/451 | 纯净 **已复现**；合并计数**不一致**（F3） |
| `lxmusic-service` | **2 failed / 339 passed** | **2 failed / 339 passed**；同一用例 `test_verify_cancel_cleanup_timeout_is_bounded_and_logged[False]/[True]` | 2/339 = 2/339 | **已复现**（F5） |
| `container`+`packaging/tests`+`tests/runner`+`tests/integration` | **2 collection errors**（`packaging/tests/test_donate_assets.py`、`test_gen_wechat_qr.py`：`ModuleNotFoundError: PIL`） | **2 collection errors**（同因） | 2 collection error = 2 | **已复现**（F5） |
| `offline_tests`（裸跑，无隔离 env） | **474 passed / 23 errors / 274 subtests passed**；23 errors 全部是 `test_manual_save_anonymous.py::AnonymousStoreLinuxTests` 的 `REFUSED: ... dedicated disposable runner` | 不适用 | **245 passed / 19 errors** | 定性一致（runner 缺失）、计数**不一致**（F4） |
| `offline_tests`（专用隔离协议，kurumi 非特权） | **494 passed / 3 skipped / 0 failed**（skips：`test_bind_mount_socket_probe.py:439/:77`、`test_restore_fixture.py:340`，均 root 专属） | — | 494/3/0 | **已复现**（F4/F6） |
| `offline_tests`（专用隔离协议，root） | **496 passed / 1 failed**；唯一失败 = `test_real_destination_permission_denied_is_frozen_without_publication`（语义要求非特权，root 必然失败） | — | 496/1 | **已复现**（F4/F6） |
| JS `test_app_js.js` | **193/193 passed, 0 failed** | — | 193/193 | **已复现**（F5） |
| JS `test_llm_usage_js.js` / `test_public_playlists_frontend.js` | passed / **14/14** | — | passed / 14/14 | **已复现**（F5） |

## 2. §6.1 与 §4 的逐项复现

| # | 报告声明（§6.1/§4） | 独立复现 | 判定 |
| --- | --- | --- | --- |
| F6 | `audit_doc_hash_bindings.py`：indexed 359 / documents 80 / active 151 / valid self-stamps 17 / STALE 0 | 干净树：**359 / 80 / 151 / 17 / 0**（historical tokens 229）——完全一致；另用自写脚本独立重算 17 个 `*HASH-STAMP` 的前缀 SHA-256：**17/17 有效、0 STALE**；全树文本文件 CRLF 计数 **0**；`sh -n container/env_flag.sh`（Debian dash）通过 | **已复现**（LF 规范化确未破坏自校验戳） |
| F7 | G-X3 `offline_tests/test_verify_acceptance_evidence_semantics.py` 51 passed | **51 passed** | **已复现** |
| F7 | `scripts/verify_position_file_evidence.py --help` 可运行 | 退出码 0，usage 正常 | **已复现** |
| F7 | §6.1 两个 Linux-only 缺陷修复后通过 | 两个用例（`test_procfd_directory_path_uses_held_directory_after_rename`、`test_reopened_destination_hash_mismatch_blocks_success`）在 kurumi+隔离 runner 下 **2 passed** | **已复现** |
| F8 | §4 五处回归已修（tee 默认对齐上游 true） | `test_netease.py -k iks5fw` **7 passed**；`test_installer_tee_save_default_is_on_and_preserves_existing_choice`、`test_env_hot_reload_whitelist_updates_conf_and_environ`、`test_config_view_defaults_tee_save_on_when_key_is_missing`、重命名后的 `test_tee_save_is_on_by_default_when_environment_key_is_missing`、tee schema/UI 用例全部通过。默认值实测：`.env.example:93` `true`、`install.sh:1310` `'true'`、`proxy/app.py:184` 缺省 `true`、`webui-service/app.py:188` `"default": "true"` | **已复现**（README 有例外，见 F9） |
| F11 | 编译/语法声明（`node_compile`/`py_compile`/`sh -n`） | 全树 239 个 `.py` 用 `compile()` 检查 **0 语法错误**；3 个关键文件 `py_compile`（临时 cfile）通过；23 个 `.sh` 按 shebang 选择解释器 `sh -n`/`bash -n` **0 失败**（20 个 bash 脚本 `bash -n` 全过）；本地 `node --check static/app.js`、`test_app_js.js`、`test_public_playlists_frontend.js` 全过 | **已复现** |

## 3. §4/§6 其它声明与 Windows 本地复核

- **Windows 本地 `offline_tests`**：`21 failed / 375 passed / 101 skipped / 256 subtests passed`。失败原因 = **20 个** `ModuleNotFoundError: No module named 'fastapi'` + **1 个** `test_r2_isolation_probe.py::...test_document_shell_allows_only_matching_hash_and_cleans_own_directory`（Git-Bash 返回 1，`AssertionError: 1 != 0`）。该 r2 用例在**迁移前旧仓库** `E:\fnmusic-ext-dev` 同样失败 → 属既有 Windows/Git-Bash 环境问题，**不是迁移引入**；但「21 个失败确因缺 fastapi」的表述不精确（19/21 归属不成立，见 F10）。
- **历史声明（未在本次复现范围）**：§5.1「node 测试从 178 增至 193」——193 已复现；「178」是中间合并态的历史数字，旧仓库当前为 `150/150`，无法从现有工作树独立再现（不影响结论，见 F13）。
- **文件数**：报告 §7/§9 称「181 files」；当前 HEAD 实测 `git diff --name-only v2.12.0..HEAD` = **182**；`91bd9c5` 时为 181，`7ee04ee` 起（迁移报告提交）变 182 → 报告数字对应更早提交，已过时（F12，次要）。

## 4. 编号发现

| # | 级别 | 结论 | 说明 |
| --- | --- | --- | --- |
| F1 | 已复现 | 通过 | 合并后 `proxy/tests` 42 failed/3128 passed/3 skipped 与文档一致；与纯净 v2.12.0 的 FAILED 列表 `comm` 为空、`cmp` 相同 → 「失败集逐条完全一致、0 差异」成立 |
| F2 | **不一致** | 报告数字错误 | 纯净树 passed 实测 **1726**（非 1776）；收集总数 1771，`1776+42=1818` 数学上不可能；通过数差实测 **+1402**（非 +1352），且 1402 恰等于两树收集数差 3173−1771，内部自洽。建议改为「42 failed / 1726 passed / 3 skipped，+1402」 |
| F3 | **不一致** | 报告数字无法复现 | `webui-service` 合并树实测 **8 failed / 452 passed / 2 skipped**（非 7/330）；与纯净树 FAILED 集合逐条相同，故「合并后更少 1 个失败（tee 默认测试已更新）」不成立。纯净 8/451 与报告一致 |
| F4 | **不一致（已过时）** | §6 表格 stale | 裸跑 `offline_tests` 实测 **474 passed / 23 errors**（非 245/19）；23 个 error 全部为 Linux 专用类因缺隔离 runner 的 `REFUSED`。同表下的 §6.1 新数字（kurumi 494/3/0、root 496/1）**已复现**，建议以 §6.1 为准并修正表格 |
| F5 | 已复现 | 通过 | lxmusic 2/339 两边一致（同一 flake 用例）；infra 2 collection errors 两边一致（PIL 缺失）；JS 193/193、llm passed、14/14 |
| F6 | 已复现 | 通过 | audit 干净树 359/80/151/17/STALE 0；自写脚本 17/17 stamp 通过；0 CRLF；env_flag.sh dash 语法 OK → 「LF 规范化没破坏自校验戳」成立 |
| F7 | 已复现 | 通过 | G-X3 51 passed；`verify_position_file_evidence.py --help` 正常；两个 §6.1 修复用例通过 |
| F8 | 已复现 | 通过 | §4 五处回归相关用例全部通过；tee 默认 true 在 4 个配置来源实测成立 |
| F9 | 部分不一致（文档） | 建议修正 | README 自相矛盾：`README.md:162` 仍写「「边听边存」默认关闭」，而 `README.md:238` 写「默认开启（与上游 2.12 一致）」；§4「README 全部改 true」不完全成立 |
| F10 | 部分不一致 | 归属需修正 | Windows 本地 21 个 offline 失败：**20** 个确为缺 fastapi；**1** 个是既有的 Windows/Git-Bash 环境失败（迁移前旧仓库同样失败）。两者均非迁移引入，但「21 个确因缺 fastapi」不精确 |
| F11 | 已复现 | 通过 | 编译/语法声明成立（239 py / 23 sh / 3 js 抽查，0 语法错误） |
| F12 | 不一致（次要） | 报告 stale | 「181 files」在当前 HEAD 为 182（181 对应 `91bd9c5`；迁移报告自身提交后 +1） |
| F13 | 无法复现（历史） | 不阻断 | §5.1「node 178→193」的 193 已复现；178 属中间态历史数字，旧仓库现为 150/150，无法独立再现 |
| F14 | 方法注意 | 非缺陷 | `audit_doc_hash_bindings.py` 的计数依赖树内状态：在**跑过测试的树**上会多出 2 个被索引文件（`online_favorites/user-a.json`、`logs/env_snapshot.txt`），indexed 变 361、active 不变；干净树为 359。复现该数字须用干净树 |

## 5. 设备副作用与清理

- `fnmusic-ext.service`：`InvocationID=53d953f3475047e484e9fdd955b71716`、`MainPID=1870197`、`NRestarts=0`、`ActiveState=active`，复跑前后一致 → **未停启/未重启**。
- `/vol1` 关键路径（`/vol1/1000/fnmusic-manual-save-staging` 700 root:root、`/vol1/1000/fnmusic-test-20261031` 750 kurumi:Users）mode/owner/size/mtime 均不变 → **未写 /vol1**。
- 全部复跑只读写 `/tmp`（两棵树的副本与隔离 runner）；未改配置、未装/回滚包。注意：测试在树内会生成 `logs/`、`recommend_cache/`、`online_favorites/` 等产物（均在 `/tmp` 树内）；诊断时曾在 Lead 的旧 `/tmp/fnm212` 中跑过测试，可能在该目录新增同类测试产物（`/tmp` 内，未清理）。
- 复审者本轮产生的 `/tmp` 资产：两棵自打包树、tarball、kurumi/root 隔离 runner、探针脚本（均已删除）；仅保留 `/tmp/fnm212-trv2-logs/`（1.2 MB 原始日志）作证据。Lead 预置的 `/tmp/fnm212-deps`、`/tmp/fnm212-pristine` 未改动。

## 6. 结论

**PASS-with-limits（验证证据部分）。**

- **复现成立的核心**：合并后 `proxy/tests` 42 failed/3128 passed/3 skipped；与纯净 v2.12.0 **失败集逐条一致、零差异**（F1）；lxmusic 2/339、infra 2 collection errors、JS 193/193+14/14+llm（F5）；§6.1 的 audit 359/80/151/17/STALE 0（含独立 stamp 重算，F6）、G-X3 51、两个 Linux-only 修复（F7）；§4 五处回归与 tee 默认 true（F8）；编译/语法声明（F11）。据此，「合并未引入新的测试回归」这一**定性结论**有独立证据支撑。
- **不成立/需修正的数字**：纯净树 passed 1776（实为 1726；+1352 应为 +1402，F2）；webui 合并树 7 failed/330 passed（实为 8/452，F3）；offline 裸跑 245/19（实为 474/23，F4）；文件数 181（HEAD 为 182，F12）；README tee 默认矛盾（F9）；Windows 失败归属（F10）。这些是**报告数字/文档问题，未发现伪造或选择性引用**：所有可复跑数字均能用独立数据解释（多数是旧快照或笔误）。
- **不在本报告范围**：结构/冲突解决/合并语义（native-reviewer task-39）；生产升级、部署、回滚授权（仍 BLOCKED，未解除）。本报告不构成任何生产/升级授权。
