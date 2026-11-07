# 迁移到上游 2.12.0 基线：结构 / 特性 / 冲突独立复审（2026-11-07）

> **复审者**：`native-reviewer`（独立复审；未参与迁移、未参与 test-reviewer 的验证证据复现）。**复审对象**：`E:\fnmusic-ext-212`，分支 `codex/fnmusic-2.12-baseline`，**HEAD `1c2686f14bab3a7f696fbb41703e360c3e5b99d3`**（复审期间 HEAD 从 `406c4d5` 经 `8b0ca6e`、`ad880b4`、`617c783`、`61da130`、`944546e` 到 `1c2686f`；`8b0ca6e` 之后增量见 §2.7，F1–F3 复验与最终 PASS 见 §7）。
> **基准**：旧仓库 `snapshot/pre-212-final` = `2d35f63`（迁移前完整状态，旧仓库工作树 clean）；上游 `v2.12.0` = `71ade6b`。
> **方法**：本地只读 git/文件检查、静态代码核对、本机工具校验（Python 3.14 / Git-Bash / node 24.18）；**未访问设备、未跑 NAS 测试套件、未停启服务、未改被审文件/代码**；旧工作区 `E:\fnmusic-ext-dev` 未改动（复核时 `git status` clean、HEAD `2d35f63`）。唯一写入为本报告。

---

## 0. 结论摘要

**判定：PASS（F1 已按记录口径解除，见 §7）。**

- **完整性：通过（唯一旧有新无的文件可解释）**。旧快照 350 个跟踪文件 vs 新树 415 个；`comm -23` 仅 `preview_image/preview.png`（635,513 B，`9db8f700…`）。上游 v2.12.0 提供两个新预览图（`2.12.0_preview.png`、`fnmusic_preview.png`），新 README 已改用它们，`install.sh` 对 `preview_image` 只遍历目录名；旧文件无代码/构建引用 → **不是功能缺失，非 BLOCKING**。
- **上游特性：通过**。`PLAY_QUALITY`/`DOWNLOAD_QUALITY`、`public_playlists_by_user`、`_try_migrate_public_playlist_users`、`detect_library_dir`/`_drop_privileges`、`ORGANIZE_PATH`、IKS5FW、diag（`/api/logs/export`）、`llm_usage`（`/api/llm/usage`）均在且在可达路径；app.js/index.html 同时保留 lx 音乐源子页与我方 “保存到 NAS” 页。
- **冲突解决：通过（4 处重点抽查全部正确；全树无冲突 marker）**。`webui_gateway.py` 同时有 `handle_manual_save_status`（挂载到 dispatch）与 accept 循环 `except OSError: break` / `except Exception: time.sleep(0.1)`；`proxy/app.py` 单一 `lifespan`，`maintenance_startup` 约束快照/迁移/任务/lx 预热，正常模式 `_drop_privileges()` 在 `create_task(_lyric_orphan_sweeper)` 之前；`app.js`/`test_app_js.js` 顶层函数无重名（188/32 个、0 重复），两侧功能/测试都在；Dockerfile COPY 源全部存在。
- **TEE 默认 true：五个位置数值一致（617c783 已修 README 正文矛盾）**；测试已合理更新（缺省 true、显式 false 仍关、升级保留既有选择）。F3 已在 944546e 解除（`proxy/app.py` 注释改为“默认开”，与 :184 代码一致；见 §7）。
- **可解析性：通过（F2 已在 944546e 解除；617c783 时点曾有 1 个 tracked CRLF）**。`bash -n`/`sh -n container/env_flag.sh` rc=0；`py_compile` 关键 Python（含本次维护模式修复）通过；`node --check` 两个 JS 通过；YAML workflow 合法；当前 **tracked CRLF = 0**（evidence review 文档已转 LF，sha256 `ed8b0ed8…`；617c783 时点该文件为 CRLF）。
- **诚实性：通过（F1 已按记录口径解除，见 §7）**。报告 §6.3 已按 test-reviewer 证据复审更正 5 处测试数字并修 README 矛盾；§1/§2.5/§5 的旧数字已按实测更正为 94（76A+18M）、203 跟踪 CRLF、106 文件、184 文件与 361/82/active165/valid18（记录时点 944546e）；计数会随复审报告入库 +1，属记录属性，不是缺陷。
- **8b0ca6e→617c783 增量：认可**。两个 Linux-only 测试修复语义正确、无新问题（本机两文件 48 passed/5 skipped）；`ad880b4` 的维护模式不降权修复正确（正常模式行为不变、维护模式保持 root，测试已补）；`617c783` 的 README TEE 修正与报告 §6.2/§6.3 更正有效，但引入了上述 1 个 CRLF 文档（F2）。

---

## 1. 基准与树一致性

| 项 | 结果 |
| --- | --- |
| 旧仓库状态 | `E:\fnmusic-ext-dev` clean，HEAD `2d35f63`；`snapshot/pre-212-final` 与 `snapshot/pre-212-worktree` 均指向 `2d35f63`；`backup/pre-212-20261107` 是**分支**（指向 `cf843a4`，报告 §1 的“分支”表述正确） |
| 新树状态 | clean，HEAD `1c2686f`（`1c2686f14bab3a7f696fbb41703e360c3e5b99d3`） |
| 文件数 | 旧快照 350；新 HEAD 415；old-only = 1（`preview_image/preview.png`）；new-only = 66（上游新增文件 + 我方新增，属预期） |
| `v2.12.0..HEAD` | **184 文件 = 153 A + 31 M；D / R / C = 0**（无上游文件被删/改名） |
| A（153） | 我方新增（docs 74 含 test-reviewer 的 `MIGRATION_2_12_EVIDENCE_REVIEW…` 与本复审报告、scripts 21、offline_tests 24、proxy 30、tools 2、musicbox 2）；均为迁移工作产物 |
| M（31） | 双方合并文件（含 `webui_gateway.py`、`proxy/app.py`、`webui-service/app.py`、前端 3 文件、Dockerfile、workflows、README、pytest/runner、`takeover.py` 等）；逐文件 `git diff --numstat` 未见大规模上游代码删除；diff 中仅 Dockerfile 一行 COPY 被替换为含 `gateway_identity` 的超集 |
| old-only 解释 | `preview_image/preview.png`（635,513 B，sha256 `9db8f700c08f458de458df567a6389eec4499f00`）：仅旧 README 引用；上游 2.12 自带 `2.12.0_preview.png`/`fnmusic_preview.png`，新 README 已改用；`install.sh:1891` 只对目录名做清理白名单；无构建/代码引用 → **可解释，不阻塞** |

---

## 2. 逐项核验

### 2.1 上游特性未丢（HEAD 617c783）
| 特性 | 证据（文件:行） | 结论 |
| --- | --- | --- |
| `PLAY_QUALITY`/`DOWNLOAD_QUALITY` | `.env.example:83/10`；`proxy/app.py:264-265,611-612,943,963`；`proxy/quality_policy.py:48/64`；`webui-service/app.py:181-182`（schema enum）；README:233-234 | 在 |
| `public_playlists_by_user` | `proxy/app.py:276,636,1201,5197,5201`；`proxy/publicplaylists.py:186-187`；`webui-service/app.py:187,240-246,615-618`；app.js 公共歌单 UI | 在 |
| `_try_migrate_public_playlist_users` | `proxy/app.py:5114`（定义）、`5328`（lifespan 内 `background_jobs_enabled` 时调用） | 在 |
| `detect_library_dir` / `_drop_privileges` | `proxy/app.py:2414`/`2515`；lifespan `5389-5391`（`if not maintenance_startup`，正常模式探测+降权，维护模式保持 root） | 在（增量已修） |
| `ORGANIZE_PATH` | `proxy/maintenance_socket.py:19,47-49`；`proxy/webui_gateway.py:86`；维护 socket 路由 | 在 |
| IKS5FW | `proxy/app.py:3606,3614`；`proxy/tests/test_netease.py:672+`（`fLaC` fixture/落盘断言）；CHANGELOG 多处 | 在 |
| diag / 日志导出 | `webui-service/app.py:2452`(`_export_log_files`)、`2616-2617`（`GET /api/logs/export`）、`2644-2646` | 在 |
| `llm_usage` | `proxy/llm_usage.py`；`webui-service/app.py:55`、`1998-1999`（`GET /api/llm/usage`）；`proxy/app.py:5578-5579`（healthz） | 在 |
| lx 子页 + 我方 page-save | `index.html:35/73/133/270/285/396-398`；`app.js:3204+`（manualSave/PLAYLIST_API）与 lx 函数 | 并存 |

### 2.2 冲突解决 4 处重点抽查
| 位置 | 核验结果 | 结论 |
| --- | --- | --- |
| `webui_gateway.py` | `handle_manual_save_status` 定义 :1003，dispatch :2842 调用；`serve()` :2634-2639 同时有 `except OSError: break` 与 `except Exception: time.sleep(0.1)`；线程派发异常也 close+retry | 正确 |
| `proxy/app.py` | `def lifespan` 全文件仅 1 个；`maintenance_startup` 约束宿主快照(:5318)、迁移(:5326-5328)、后台任务(:5394)、lx 预热(:5404)；正常模式 `_drop_privileges()` 在 `create_task(_lyric_orphan_sweeper)` 之前；维护模式不降权（`ad880b4`） | 正确 |
| `app.js` / `test_app_js.js` | 顶层 `function` 声明 188 / 32 个，重名 0；两侧功能都在（lx 字符串、manualSave 10 处；上游测试标记 252 处）；`node --check` 均 rc=0 | 正确 |
| `container/Dockerfile` | 逐条 COPY 源存在性检查 `missing=[]`；唯一改动行是上游 COPY 行替换为含 `proxy/gateway_identity.py` 的超集，未丢 `direct_play/media_url/quality_policy/publicplaylists/llm_usage`；`gateway_observation.py` 仅宿主 gateway 导入，不在容器代码导入 | 正确 |

**全树冲突 marker**：`git grep '^<<<<<<< |=======$|>>>>>>> '` → 0；相关 16 个文件全部存在且可解析。报告 §3 声称 “15 个文件 22 处”无法从 git 元数据独立复核（git 不保留冲突计数），但 4 处重点与 marker 检查均无异常；此计数不构成缺陷，见 §3 F4。

**其它冲突文件抽查**：`test_netease.py` 同时保留 tee 注入与 IKS5FW `fLaC` fixture；`container/env_flag.sh` 同时保留我方只读开关与上游 `FNMUSIC_INTERNAL_TOKEN/LX_*`（`bash -n` rc=0）；`pytest.ini`/`run_tests.py`/`test_run_tests.py` 同时包含 `offline_tests` 与 `tests/integration`；release workflow 保留 diff 规则与上游 `basename` 逻辑，3 个 workflow YAML 均可解析。

### 2.3 TEE_SAVE 默认 true 五处
| 位置 | 值/证据 |
| --- | --- |
| `install.sh` | :1310 `echo "FNMUSIC_TEE_SAVE_ENABLED='true'"` |
| `.env.example` | :93 `FNMUSIC_TEE_SAVE_ENABLED=true` |
| `proxy/app.py` | :184 `os.environ.get(..., "true").lower() in (...)` |
| `webui-service/app.py` | :188 schema `"default": "true"` |
| `README.md` | :162 正文“默认开启（与上游 2.12 一致，可 false 关闭）”；:238 配置表“默认开启”（617c783 已修，此前正文写“默认关闭”） |

测试：`test_tee_save_is_on_by_default_when_environment_key_is_missing`（参数化缺省/true/1/yes/false）、`test_installer_tee_save_default_is_on_and_preserves_existing_choice`（fresh=true、升级保留既有选择）、`test_config_view_defaults_tee_save_on_when_key_is_missing`（webui 缺省 true）、`test_env_hot_reload_whitelist_updates_conf_and_environ`（`tee_save_enabled is True`）均已更新且断言合理。**残留**：`proxy/app.py:182` 注释仍写“默认关”（与代码/README 矛盾，注释级，F3）。

### 2.4 可解析性
- 当前 **tracked CRLF = 1**：`docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md`（87 行/14,178 B，sha256 `dc03f7ba…2185`，`i/crlf w/crlf`），由 `ad880b4` 引入；其余 tracked 文本 LF。该文件是 test-reviewer 的证据复审产物，非代码，但违反“全树无 CRLF 残留”/LF 规范化目标（F2）。
- `bash -n` / `sh -n container/env_flag.sh`：rc=0；`py_compile`：`proxy/app.py`、`proxy/webui_gateway.py`、`webui-service/app.py`、`proxy/tests/test_maintenance_startup.py`、两个修复后的 offline 测试 → 全部 OK；`node --check`：`app.js`、`test_app_js.js` → rc=0；3 个 workflow YAML 解析 OK。
- 文档审计：当前 **360 files / 81 docs / active 151 / valid self-stamps 17 / STALE 0**；G-X3 `51 passed`；scanner CLI `--help` rc=0（与本机复现一致）。注意报告 §6.1 写 359/80（绑定 8b0ca6e 时点），当前 HEAD 已因新增证据复审文档变化（F1）。

### 2.5 验证证据边界（不与 task-40 重复）
test-reviewer 报告 `docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md`（sha256 `dc03f7ba…`，与迁移报告 §6.3 引用一致）独立复现了代理失败集零差异、lxmusic/infra、JS、audit、G-X3、offline 隔离计数等；本复审**不重复** NAS 测试套件，只复核结构/特性/冲突/解析与报告引用一致性。迁移报告 §6.3 对 5 处测试数字的更正与证据复审文件一致。

### 2.6 诚实性（迁移报告 `docs/MIGRATION_TO_2_12_0_20261107.md`，2aa5e1e1…）
已确认的纠正/如实记录：
- §5.1 如实记录标签误用、`git diff cf843a4..2d35f63` 补 94 文件、`git apply --3way` 零冲突、后续重指 `snapshot/pre-212-final`/`snapshot/pre-212-worktree`；§6.2 记录维护模式降权缺陷及修复；§6.3 记录 5 处数字更正与 README 矛盾修复；`backup/pre-212-20261107` 明确写为分支（正确）。
- **残留不准确/过时（F1）**：
  1. §1/§2/§5：`92 项未提交改动`、`74 个新增文件 + 18 个修改`；实测 `cf843a4..2d35f63` = **76 A + 18 M = 94**，报告同段又写“94 文件”，内部不一致（74+18=92）。
  2. §2.5/§5.2：`318 个文本文件 CRLF→LF` 且“我方旧仓库以 CRLF 提交”；实测旧提交 blob 为 LF（`git ls-files --eol` 旧仓库为 `i/lf w/crlf`，工作树 206 个 CRLF），规范化提交 `eae2882` 改 106 个文件；318 无法复现，且“以 CRLF 提交”表述错误。
  3. §7/命令注释：`# 181 files`；当前 HEAD `v2.12.0..HEAD` 为 **183**（152A+31M；`git diff --stat` 同）。§6.3 已承认文件数会随补测增长，但未改该行。
  4. §1/§7/§5 仍以 `91bd9c5` 作为“新分支提交”；当前 HEAD `617c783`（§6.3 证据复审绑定 8b0ca6e，属其快照口径，但正文未更新）。
  5. §6.1 审计计数 359/80 与当前 360/81 不一致（新增证据复审文档/§6.3 后）；属快照口径未标注。
- `§6.3` 的 5 处更正（proxy 1726/+4 skipped、webui 8/452、offline 474/23、文件差异增长、Windows 20+1）与 test-reviewer 证据复审一致；README TEE 矛盾已在 617c783 修复。

### 2.7 增量评估（按 Lead 要求单独评估）
- **`8b0ca6e`**（两个 Linux-only 测试修复）：`test_bind_mount_socket_probe.py` 在 `held_path.rename(replacement_path)` 后改为 `held_path.mkdir()`——语义正确（原路径重建新目录，fd 仍指向被持有的旧目录；原写法对已存在目标 mkdir 必 `FileExistsError`）；`test_restore_fixture.py` 把 `expected_flags` 移到 patch 生效期间取样——正确（Linux 上 `os.O_NOFOLLOW|O_CLOEXEC=655360` 不再导致断言恒假）。本机两文件 `48 passed / 5 skipped`（Windows 下 Linux-only 用例 skip），无语法/新问题；Linux 真机计数由 task-40/test-reviewer 负责。
- **`ad880b4`**（维护模式不得降权）：`proxy/app.py` 的 `detect_library_dir(); _drop_privileges()` 改为 `if not maintenance_startup:` 下执行——正确（正常模式仍先探测再降权、后台任务在降权后创建；维护模式保持 root，避免后续特权维护请求 EACCES）；`test_maintenance_startup.py` 增加 `_drop_privileges` stub 并断言“非维护必探测+降权、维护两者都禁止”，合理；无新问题。
- **`617c783`**（文档/README 更正）：README「边听边存」正文改为默认开启，消除与配置表/上游矛盾；报告 §6.2/§6.3 补充维护缺陷与 5 处数字更正；但新增 `docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md` 带 CRLF（F2），且 §1/§2/§5 的 74/92/318/181/91bd9c5 未改（F1）。

---

## 3. 发现

### F1（重要）迁移报告残留数字/表述不准确，诚实性项未完全解除
`docs/MIGRATION_TO_2_12_0_20261107.md`：`92 项未提交/74 个新增+18 修改` 与实测 `76A+18M=94` 及同段的“94 文件”自相矛盾；`318 个文本文件 CRLF→LF/以 CRLF 提交` 与实测（旧提交 LF、工作树 206 CRLF、规范化 106）不符；`#181 files` 与当前 183 不符；`91bd9c5` 作为新分支提交已过时；§6.1 审计 359/80 与当前 360/81 不一致。§6.3 已更正 5 处测试数字并修 README，但上述项仍在。**不影响结构/特性结论**，但报告作为迁移记录的可核验性受损；建议按实测更新或标注快照口径。

### F2（次要）新增证据复审文档带 CRLF，违反 LF 规范化
`docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md`（`ad880b4` 引入，87 行/14,178 B，sha256 `dc03f7ba…`）是唯一 tracked CRLF 文件（`i/crlf w/crlf`），其余 tracked 文本 LF。文档级问题、无功能风险，但“全树无 CRLF 残留”未完全满足；建议转为 LF（保持其内容/哈希记录或同步更新引用）。

### F3（次要）`proxy/app.py` 注释与代码/README 矛盾
:182 注释“边听边存：默认关”，而代码 :184 默认 `"true"`、README/`.env.example`/webui schema 均为默认开启；注释级残留，建议更新以免误导。

### F4（限制，非缺陷）不可独立复核项
- 报告称“15 个文件 22 处冲突”：git 不保留冲突计数，无法从仓库元数据独立复核；实际相关文件集合为 16 个、全部无 marker，4 处重点抽查正确，未见冲突残留。
- 报告 §6.1 的 NAS `offline_tests` 计数（kurumi 494/3/0、root 496/1）由 test-reviewer 的证据复审负责；本复审未重复跑 NAS 套件，未发现矛盾；§6.3 引用的证据复审文件哈希 `dc03f7ba…` 与磁盘一致。
- 迁移报告的 test 数字（proxy/webui/lxmusic/JS 等）同一归属，本复审不做二次复现。

---

## 4. 解除 / 未解除清单

**已解除（本轮独立核实）**：
- 完整性：唯一 old-only `preview.png` 可解释（上游预览图替代 + README 更新 + 无代码引用），无无法解释的缺失；`v2.12.0..HEAD` 无 D/R/C，上游文件未丢。
- 上游特性：任务列出的 9 类符号/功能均在且可达；前端 lx 子页与 page-save 并存。
- 冲突解决：4 处重点正确；全树无 marker；`webui_gateway`/`app.py`/前端/Dockerfile/`test_netease`/`env_flag`/`pytest.ini`/runner 均通过抽查。
- TEE 默认 true：五个位置数值一致；测试更新合理；README 矛盾已在 617c783 修复。
- 可解析性：`sh -n`/`bash -n`、`py_compile` 关键文件、`node --check` 两个 JS、YAML 解析均通过。
- 增量修复：`8b0ca6e` 两个测试修复、`ad880b4` 维护模式修复、`617c783` README/报告更正均认可；证据复审文件哈希与报告引用一致。

**未解除**：
- **F1**：迁移报告残留 74/92、318、181/183、91bd9c5、§6.1 计数等不准确/过时数字（诚实性未完全闭环）。
- **F2**：新增 `MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md` 为 tracked CRLF（全树唯一），LF 规范化目标未完全达成。
- **F3**：`proxy/app.py` “默认关”注释残留（注释级）。
- F4 为范围限制，不构成缺陷。

---

## 5. 关键文件 SHA-256（HEAD 1c2686f；F1–F3 复验后的当前值）

| 文件 | SHA-256 |
| --- | --- |
| `HEAD` | `1c2686f14bab3a7f696fbb41703e360c3e5b99d3` |
| `docs/MIGRATION_TO_2_12_0_20261107.md`（19,996 B / 182 行） | `0b40daa09b9670e88ba364465c9d00ad1d89ddebaa25c2aa1d920754f100d7aa` |
| `docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md`（14,091 B / 87 行，LF） | `ed8b0ed8abfbc080fddedf2fd82c1b7650b0c2db287f588c0fac568caded0daf` |
| `proxy/webui_gateway.py` | `7fef9086ec83e7f2babb2e820b235950f739e9c8434f9739a83bbf72b81ead61` |
| `proxy/app.py` | `44304f6c825f5f8116452144327b40530814af0657b3ba1409c8443ccc4f194b` |
| `webui-service/static/app.js` | `5af2b676956c7602e9a19ae73662e5c541167b3ffb46df0c0d75a9e43ff0b197` |
| `webui-service/static/index.html` | `b73badba3013ac1ba3728c180fdb21455c70272f76e4c09d443b6d73dd039890` |
| `webui-service/test_app_js.js` | `ef2b96fa2a0ccfaef83493f19ca0644abef9dcdddf5e9ca71a73e3c096a1d67f` |
| `container/Dockerfile` | `61e7c95ee4aab571bb828831f8a27eb8a9a46d40f2dd192162c43bd936f49f78` |
| `container/env_flag.sh` | `0748b16e4f79a6d2360fb14bfd509e9ca24cc893059cb05320ff83195c932c13` |
| `offline_tests/test_bind_mount_socket_probe.py` | `d6bfadc586acec277a3680b6560e6321bb1a1d5d9da5d3fcde952309ba6ba54a` |
| `offline_tests/test_restore_fixture.py` | `deb09f3bec7125f32c2065d8b1d78551ea901c0cbf56bf7b87baf9fb6c65f1eb` |
| `proxy/tests/test_maintenance_startup.py` | `32a8dcc9e9c86e8eabbd70f69696d907330b9e8c2a3030b5be4d783820bbb01c` |
| `install.sh` | `396bdce08594c8b184d1fd4b8c4206962c05014277ca53751da1d50853786b16` |
| `.env.example` | `c07ffc7d32ad3c22d09b309f8f3d5949c0d14732644eb81c959c2a541f86d375` |
| `README.md` | `d3981ed444b25a1e4fdb275e2f854021d9195b6e1117a34379ab06e0f7a8bad3` |
| `preview_image/preview.png`（旧快照 2d35f63，635,513 B） | `9db8f700c08f458de458df567a6389eec4499f00` |

---

## 6. 本报告哈希说明

- 本报告写入完成后的**最终整文件 SHA-256** 见本轮复审消息；文末 `REVIEW-HASH-STAMP` 行给出 `sha256(本行之前全部字节, UTF-8)` 的自校验值（删除该行后重算即得）。
- 本复审只读本地仓库；未 commit、未改被审文件/代码、未访问设备/未跑 NAS 测试套件；旧工作区保持不变。

---

## 7. F1–F3 复验与最终结论（HEAD 1c2686f，追加）

> 本节最终绑定 `1c2686f`（`1c2686f14bab3a7f696fbb41703e360c3e5b99d3`）；§0–§6 中 F1–F3 的描述是 `617c783` 时点的原始发现（保留作审计链），状态以本节为准。仅复核 F1–F3，未重跑全量；未改代码/迁移报告，未访问设备。

| 项 | 复验结果 | 判定 |
| --- | --- | --- |
| **F1** | **解除（记录口径）**。迁移报告 §1/§6.1/§6.5 已写「截至 `944546e` 实测 **184 个文件**；审计 **361 文件 / 82 文档 / active 165 / valid stamps 18**（`617c783` 时点 360/81/151/17）」，并注明含两轮复审报告入库。本机复核：`git diff --name-only v2.12.0..HEAD` = **184**；clean HEAD 审计 = **361/82/active165/valid18/STALE0**，均与记录一致；旧数字（74/92/318/181 等）仅存在于 §6.5 修复记录的历史引用中。**记录属性（重要）**：文件/文档/绑定计数会随「复审报告自身入库」而 +1，任何写死在文档里的数字必然随后滞后，这不是缺陷；判定口径应为「与记录时点 HEAD 一致 + 给出可复核命令」。 | **解除**（记录口径） |
| **F2** | tracked CRLF = **0**（`git grep -I -l $'\r' HEAD -- .` 为空）；`docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md` 为 `i/lf w/lf`，sha256 **`ed8b0ed8abfbc080fddedf2fd82c1b7650b0c2db287f588c0fac568caded0daf`**（14,091 B / 87 行），与 Lead 给定值一致。 | **解除** |
| **F3** | `proxy/app.py:182` 注释已为“边听边存：默认开”；`:184` 代码默认 `"true"`；README:162/238 均为默认开启。 | **解除** |

**新哈希（HEAD 1c2686f）**（§5 表已同步）：

| 文件 | SHA-256 |
| --- | --- |
| `HEAD` | `1c2686f14bab3a7f696fbb41703e360c3e5b99d3` |
| `docs/MIGRATION_TO_2_12_0_20261107.md`（19,996 B / 182 行） | `0b40daa09b9670e88ba364465c9d00ad1d89ddebaa25c2aa1d920754f100d7aa` |
| `docs/MIGRATION_2_12_EVIDENCE_REVIEW_20261107.md`（14,091 B / 87 行，LF） | `ed8b0ed8abfbc080fddedf2fd82c1b7650b0c2db287f588c0fac568caded0daf` |
| `proxy/app.py` | `44304f6c825f5f8116452144327b40530814af0657b3ba1409c8443ccc4f194b` |
| `README.md`（未变） | `d3981ed444b25a1e4fdb275e2f854021d9195b6e1117a34379ab06e0f7a8bad3` |

- 本复审报告在 `944546e` 被收入树内（164 行版本），`1c2686f` 为最终文档修复；本节追加不 commit，只改本报告。
- F1 已解除（记录口径）：184 / 361/82 / active165 / valid18 与记录时点 HEAD 一致；计数随复审报告入库 +1 属**记录属性**，不是缺陷。F2/F3 已解除。
- 最终 verdict：**PASS（F1 已按记录口径解除；F2/F3 解除；结构/特性/冲突结论 PASS-with-limits）。**
<!-- REVIEW-HASH-STAMP: sha256(all bytes before this line, UTF-8) = ea167ce07aef007b177ba904bb74ba31cdff689d5daa3254bc03a127cc382ebd -->
