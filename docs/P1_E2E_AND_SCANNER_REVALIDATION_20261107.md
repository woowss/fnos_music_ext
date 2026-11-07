# 新基线候选能力复验：扫描器 fail-closed 与 P1 端到端（2026-11-07）

> **这不是验收/部署证据。** 本文只记录在本轮迁移后的 2.12.0 基线上，对两个候选能力做的真实 Linux 复验：
> ① 扫描器位置证据校验器的 **fail-closed 语义**；② P1 验收证据「生产者 → G-X3 校验器」的端到端链路。
> 所有操作都在 NAS `/tmp`（ext4）以非特权用户 `kurumi` 执行；**未触碰 `/vol1`、未停启服务、未改配置、未安装/回滚**。
> 硬门未变：`_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False`、`_POINT_IN_TIME_SCAN_VALIDATED=False`、
> `_INDEPENDENT_COPY_VIEW_VALIDATED=False`、`PIPELINE_INTEGRATION_ALLOWED=False`。

## 1. 环境

| 项 | 值 |
| --- | --- |
| 主机 | fnOS `kotomi`（内核 `6.18.18.c1168-trim`，Debian 12） |
| Python | 系统 3.11.2；依赖 venv `/tmp/fnm212-venv`（fastapi 0.162.0 / httpx 0.28.1 / pytest 9.2.0） |
| 工作树 | `/tmp/fnm212`（= 分支 `codex/fnmusic-2.12-baseline` 的 HEAD 副本） |
| 用户 | `kurumi`（非特权；未使用 root 执行被测 CLI） |
| 目录 | 全部在 `/tmp/fnm-scan-e2e`、`/tmp/fnm-p1-e2e`（运行后清理） |

被测候选（SHA-256）：

| 文件 | SHA-256 |
| --- | --- |
| `scripts/verify_position_file_evidence.py` | `341dd4f4b7de912d7e8a46a269c99f06d0a3fabe2f84718fd6cc73d651b329d0` |
| `scripts/verify_acceptance_evidence_semantics.py`（G-X3） | `f448fd0ed404f1d307a0c5586bc6f3b3b76678e5fa70717b2ce3293fe779933e` |
| `proxy/acceptance_capture.py` | `a3894661c934ead8c6ec85727b86272f716fc5b53d1323e5ebc1602c7bdd04ae` |
| `scripts/observe_delivered_evidence.py` | `c43e30f5af6c88d3bd6b0f708d8f418107c3eb4bfea81577cfdbd8f13e4752b9` |
| `scripts/e2e_scan_failclosed.sh`（复现脚本） | `0526ee5e3b9e1a5a87fd4098fbb15a7e2ee5db80004477e6cc9fec90f2cb4735` |
| `scripts/e2e_p1_pipeline.sh`（复现脚本） | `f538ec97d3afebb25e80719014f4f41f136814c5ceaeee00d4813d4d910c4aab` |

## 2. 扫描器 fail-closed 复验（`--evidence-profile` 两条路径）

构造：`/tmp/fnm-scan-e2e/lib` 内 3 个合成文件（100/200/300 B），positions 由实际字节 sha256/size 生成（`position` 1..3）。

### 2.1 `independent-copy`（ext4 普通副本，模拟 M4）

**实测输出**（rc=2）：

```json
{"scope":"read_only_test_tree","evidence_profile":"independent-copy","complete":false,
 "unknown_reason":"ro_semantics_unavailable","read_mode":"o_noatime","root_scope":"unknown",
 "sync_barrier":{"pre":"done","post":"done","mechanism":"syncfs",
                 "generation_before_pre_sync":null,"generation_start":null,
                 "generation_before_post_sync":null,"generation_end":null},
 "limits":{"max_entries":100000,"entries_seen":7,"limit_exceeded":false},
 "checks":{"ro_semantics":"unavailable","generation_bracket":"not_applicable",
           "scanner_unprivileged":"ok","identity":"ok","content_hash":"performed"},
 "positions":[{"position":1,"status":"unknown"},{"position":2,"status":"unknown"},{"position":3,"status":"unknown"}]}
```

结论：**不冒充有 RO 语义**（`ro_semantics=unavailable`）、`complete=false`、全部 position `unknown`、`O_NOATIME` 生效、`syncfs` 前后 barrier 完成、`identity=ok`、`scanner_unprivileged=ok`；退出码 2（fail-closed）。与旧基线报告的行为一致。

### 2.2 `btrfs-ro-snapshot` 在 ext4 上（负例）

**实测输出**（rc=2）：

```json
{"scope":"read_only_test_tree","complete":false,
 "positions":[{"position":1,"status":"unknown"},{"position":2,"status":"unknown"},{"position":3,"status":"unknown"}]}
```

结论：非 Btrfs 根一律拒绝（fail-closed），不会因「文件内容对得上」而给出 `unique_match`。

### 2.3 只读性核对

运行前后对 3 个合成文件重算 sha256，值不变（`6c316f06…`/`9c8c5f6a…`/`c77605ba…`）；`stderr` 为空。

## 3. P1 端到端：生产者 → G-X3 校验器

构造（`/tmp/fnm-p1-e2e`）：

1. 合成 `track.wav`（pcm_s16le / 8000 Hz / 1ch / 0.1 s）+ 侧车 `track.lrc`（2 行）。
2. `scripts/observe_delivered_evidence.py` 在**保存进程之外**只读观测该文件 → `delivered_evidence`（含 `basename_ref`/`stat`/`audio`/`decode`/`tags`/`cover`/`lyric`，证据文件落 `/tmp/fnm-p1-e2e/evidence/`）。
3. `proxy/acceptance_capture.py::build_source_evidence` 抽取一个**含伪 secret 的 provider 响应**（`https://cdn.example/secret?token=SECRET`）→ 白名单 `source_evidence`。
4. 组装 EvidenceSet（1 个 position，`state=published`）→ 写 JSON。

**实测**：

| 检查 | 结果 |
| --- | --- |
| `find_sensitive_leaks(document)` | `[]` |
| 对证据目录 grep `token=SECRET` / `cdn.example` | **无命中**（URL 只以 `url_present=true` 存在性记录） |
| EvidenceSet `summary.overall_verdict` | `unknown`（**不是 pass**，符合「未验收」语义） |
| G-X3 CLI | rc=0，`result=pass`，`fail_closed=false`，`exit_code=0`，`counts={checks:26, pass:26, fail:0, unknown:0}`，`violations=[]` |

**结论**：迁移后基线上，P1 的「观测 → 抽取 → 组装 → 独立校验」链路可端到端跑通，且产出**能通过语义校验器**（结构/不变量一致）同时**保持未知结论**；敏感信息未进入证据或证据集。

## 4. 证明边界（必须保留）

1. **扫描器的 Btrfs RO 路径仍未验证**：本次只在 ext4 副本上验证 `independent-copy` 与「非 Btrfs 拒绝」；Btrfs 只读子卷/生成号 bracket 需要 `/vol1` 上的隔离探针，属门禁动作，未执行。
2. **`independent-copy` 固定 `complete:false`**：这是当前硬门使然，不是本次结果；它只能证明 fail-closed 语义，不能作为位置对应证据。
3. **P1 端到端用的是合成文件与合成 provider 响应**：不证明真实保存路径、真实 provider 响应、索引/播放（`index_play` 仍 `not_run`）或声音可听；`delivered_evidence` 的 `tags` 在合成 WAV 上为空属预期。
4. **未接入产品**：`PIPELINE_INTEGRATION_ALLOWED=False`，采集代码未进保存路径；本文不改变任何门禁，也不构成验收结果。

## 5. 复现

```bash
# NAS 上以非特权用户
scp scripts/e2e_scan_failclosed.sh kotomi-root:/tmp/scan-e2e.sh
ssh kotomi-root 'chmod 755 /tmp/scan-e2e.sh && runuser -u kurumi -- bash /tmp/scan-e2e.sh'

scp scripts/e2e_p1_pipeline.sh kotomi-root:/tmp/p1-e2e.sh
ssh kotomi-root 'chmod 755 /tmp/p1-e2e.sh && runuser -u kurumi -- bash /tmp/p1-e2e.sh'
```
