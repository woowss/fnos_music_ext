# P1 验收证据「保存时采集」设计（2026-11-07）

> **状态：离线设计 + 生产者骨架已实现并测试；未接入保存流水线，不改变任何保存语义。**
> 依据：`docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md`（Rev-3，独立复审 **PASS**，sha256 `dec0aabb…`）与
> `scripts/verify_acceptance_evidence_semantics.py`（G-X3，已修 Rev-2 泄漏缺陷）。
> 门禁未变：`PIPELINE_INTEGRATION_ALLOWED=False`。本文与代码**不构成**任何验收/部署授权。

## 1. 范围

**做**：把「保存过程中可观察的事实」抽取成 schema 合规的 EvidenceSet，供 G-X3 校验与独立复核；采集失败如实降级为 unknown。

**不做**（明确非目标）：
1. 不修改保存/发布行为、不改变返回值、不新增失败路径；
2. 不接入生产流水线（本设计只交付模块 + 离线合同测试）；
3. 不把 provider 的 `level/type` 声明当作独立结论（`claim_scope` 固定值，S2）；
4. 不持久化 URL、token、完整响应、绝对路径；
5. 不声称「可听/主观正确」（`limitations` 必含 `cannot_prove_audible_output`）。

## 2. 已实现的生产者骨架

- 模块：`proxy/acceptance_capture.py`（纯 stdlib，**不 import `proxy.app`**，可离线/独立运行）
  - sha256 `af4e675c3f516a83ae667a33eb3e6b811f90d13b13cd36d908093a820fd6c15a`（366 行）
- 合同测试：`offline_tests/test_acceptance_capture.py`
  - sha256 `4c262c2bac543ce71b496d2c459ced81a7d827d31ff30c2bb68c7014a7ba8a40`（235 行，**18 passed**）

### 2.1 API

| 函数 | 作用 | 关键约束 |
| --- | --- | --- |
| `evidence_ref(kind, payload, captured_at, …)` | 生成 `evidence_ref` | `sha256/bytes` 必须对**实际原始字节**重算；`ref_id=ev_+sha256(kind+payload)[:32]` |
| `source_evidence_uncaptured()` | 未采集时的 claim 层 | 全部显式 unknown/空；`claim_scope=provider_reported_not_independently_verified`；`claim_refs=[]` |
| `byte_scan_uncaptured(endpoint)` | 未做独立字节/位置对应 | `profile=none`、`status=not_run`，**不得伪装 `unique_match`** |
| `index_play_uncaptured()` | 未观测索引/播放 | `index/playback=not_run`，`limitations` 含 `cannot_prove_audible_output` |
| `build_position_uncaptured(...)` | 单个位置记录 | `state=not_executed`、`delivered_evidence=null`、`failure=null`、`verdict=unknown` + `fixed_reasons` 非空（INV-23） |
| `build_environment(...)` | 环境绑定 | 只存逻辑引用；`provenance_status=observed` 必须同时给 `package_sha256(64hex)`+`git_head(40hex)`（INV-22），否则显式拒绝 |
| `build_job(...)` | 账本绑定 | `manifest_sha256` 64hex；`source_fingerprint_origin ∈ {snapshot_response, collection_version_manifest, unknown}` |
| `build_evidence_set(...)` | 组装 EvidenceSet | position 去重/越界校验；`job.positions_total` 必须等于实际条数；`record_sha256` 逐条重算校验；**summary 计数与 overall 由 position 派生**，不接受调用方声称 |
| `recompute_record_sha256(position)` | 规范化重算 | 剔除 `record_sha256` 后 canonical JSON 的 SHA-256 |
| `find_sensitive_leaks(document)` | 脱敏自检 | 命中 `http(s)://`、`sk-`、`token=`、`Authorization`、`/vol1/`、`/repo/`、`/data/`、`/var/apps` 即报路径 |

### 2.2 verdict 语义（与 G-X3 INV-23 对齐）

- `expected_outcome=published` 但 `state=not_executed` ⇒ 必须 `verdict=unknown` 且 `fixed_reasons` 非空（**不能**用 `blocked`，INV-23 拒绝）；
- `blocked` 仅保留给 `commit_unknown`（A16）等真正的未知提交态；
- `fail` 只在有 `fail` 维度、或 expected/actual 不匹配、或 failure 非法时出现，且必须有 fixed reason（INV-3）；
- 采集失败 ⇒ 该位置维度 unknown、整体 non-pass，**绝不产出 pass**。

## 3. 字段映射（schema → 真实来源）

| schema 字段 | 真实来源（文件:行，合并后基线） | 现状 |
| --- | --- | --- |
| `source_evidence.quality.tier_requested` | `proxy/manual_save_route.py:734`（`quality.requested`）、`manual_save_playlist.py:77` | 已在账本 |
| `quality.reported_level / reported_status` | `proxy/app.py:4075 _netease_quality_evidence(payload)`（provider `data.level`） | 可抽取，**未持久化**（G-Q1） |
| `quality.level_in_ladder` | 阶梯集合（七档）判定 | 需采集时计算 |
| `quality.fallback` | `manual_save_route.py:737`（`quality.fallback`） | 已在账本 |
| `quality.attempts[]` | 逐档解析结果 | **未持久化**（G-Q1），缺失时降档理由 unknown |
| `container_claim.api_type/status` | provider `data.type` | 未持久化 |
| `metadata.*` | `/info` 解析（title/artist/album/…）；`track/disc` 未进最终 data（G-T1） | 部分 |
| `cover.status/error` | `manual_save_route` 封面状态机；`app.py:2069 download_cover_bytes`、`:2347 embed_audio_cover` | 状态在账本 |
| `cover.source` | 源封面字节/MIME/宽高 | **未持久化**（G-C1） |
| `lyric.status` | 歌词状态机；`app.py:2844 find_lyric_file`、`:2975 resolve_online_lyric` | 状态在账本 |
| `lyric.source` | 源歌词 UTF-8 字节/行数 | **未持久化**（G-L1） |
| `integrity.*` | 下载后 size/md5 校验；账本 `published_sha256/published_size`（`manual_save_jobs.py:145/477`） | 部分 |
| `delivered_evidence` | 落盘后**独立**读取（非保存进程）：stat/sha256/ffprobe/解码/标签/内嵌封面/侧车歌词 | 需新工具（本设计不含） |
| `byte_scan` | 扫描器/独立视图；当前硬门 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False` | 只能 `not_run/unknown` |
| `index_play` | 飞牛音乐侧只读观测 | 需 §3.7 方案 |

## 4. 采集点（实现时只读挂钩，不改行为）

| 采集点 | 触发位置 | 抽取物 | 失败语义 |
| --- | --- | --- | --- |
| P-q1 质量阶梯 | `app.py:4226 _resolve_netease_download_media` / `:4322 _resolve_netease_url_uncached` / `:7621 _resolve_direct_target` | tier→outcome、request_id/attempt_id、白名单 provider 字段摘要 | 缺任一档 ⇒ 降档理由 unknown |
| P-c1 封面 | `app.py:2099 _fetch_cover_bytes_async`、`:2347 embed_audio_cover` | 源字节 sha256/MIME/宽高/来源 kind | 采不到 ⇒ `cover.source` 缺失，维度 unknown |
| P-l1 歌词 | `app.py:2953 cache_lyrics_from_musicdl`、`:2975 resolve_online_lyric` | 选中字段、UTF-8 字节 sha256、行数 | 同上 |
| P-d1 落盘观测 | 发布成功后由**独立工具**执行 | stat/sha256/ffprobe/解码/标签/内嵌封面/侧车 | 工具不可用 ⇒ `delivered_evidence=null` + 维度 unknown |
| P-f1 失败/拒绝 | 账本 position 行（`GET job`） | 固定 reason + 阶段 + 引用 | 不得写异常文本/URL/路径 |

**硬性要求**：采集代码只读、异常必须被吞掉并标记 unknown；**采集失败不得改变保存结果**；采集开关默认关闭。

### 4.1 已实现：落盘后独立观测器（`delivered_evidence`）

- `scripts/observe_delivered_evidence.py`
  sha256 `c43e30f5af6c88d3bd6b0f708d8f418107c3eb4bfea81577cfdbd8f13e4752b9`
  - 只读：`O_RDONLY|O_NOFOLLOW` + `fstat` + 单遍流式 `sha256/md5`；`ffprobe`/`ffmpeg` 只读调用
  - 产出 schema §2.4.3 的 `delivered_evidence`：`basename/basename_ref`（basename 写入受控 `basename.txt` 并绑哈希）、`stat`、`sha256/size/md5`、`audio`（format/codec/sample_rate/channels/sample_fmt/bits_per_raw_sample/duration/bit_rate/stream_count）、`decode`（`-xerror -f null`）、`tags`（白名单 + raw_keys）、`cover`（内嵌 attached_pic 或侧车 jpg/png/webp + 字节哈希）、`lyric`（侧车 .lrc 的编码/字节/行数/文本哈希）、可选 `mode_owner`（目标目录采样）
  - **缺 ffprobe/ffmpeg 时显式失败**（exit 3，固定码），不产出「看似有效的 unknown 证据」
  - 证据文件全部落在 `--evidence-dir` 受控目录，ref 的 sha256/bytes 对实际文件重算
- `offline_tests/test_observe_delivered_evidence.py`
  sha256 `5b20d3827263ec92732819e51c00f4ea30d7290c0f04dc8bb3f957e9b8d24b0b`
  - 纯函数与失败路径（Windows 可跑）：**10 passed / 3 skipped**（skip 为缺 ffprobe 的端到端用例）
  - NAS 真机（ffprobe/ffmpeg 可用）：**31 passed**（含 18 项 capture 合同测试），端到端覆盖 WAV（pcm_s16le/8000/1ch/decode ok/侧车歌词 2 行）与带标签 MP3（mp3/44100/2ch/bit_rate/title·artist·album·track 解析）
  - 结构契约：把观测结果嵌入 EvidenceSet 后，`_structure_issues` 对 `delivered_evidence` 零违规

## 5. 验收与测试

- 生产者契约：新模块输出必须能通过 G-X3 `verify_document`（当前 18/18 通过，含「语义校验通过 ≠ 验收通过」断言）。
- 诚实性负例：未采集/采集失败/篡改哈希/计数不符/重复位置/越界/敏感信息注入，全部显式失败或降级。
- 端到端（未来）：在隔离测试库跑一次真实保存，产出 EvidenceSet → G-X3 `result=pass`（语义一致）且 `summary.overall_verdict != pass`（未验收）；再由独立复核给出 verdict。

## 6. 未决问题（需用户/设计决定）

1. **采集默认开关**：建议 `FNMUSIC_ACCEPTANCE_CAPTURE=0`（默认关），仅在验收窗口开启；需确认命名与是否随包分发。
2. **证据存放位置**：受控目录（建议 `/vol1/.../fnmusic-test-*/evidence/` 或 `/tmp` + 显式归档），需确认持久化策略与清理责任。
3. **provider 抽取深度**：`/info`、`/lyric`、封面字节是否允许落盘（当前 G-C1/G-L1 未持久化），涉及隐私与体积。
4. **`delivered_evidence` 独立工具**：需实现 ffprobe/ffmpeg 只读观测器（可复用 `_has_embedded_cover` 思路），并纳入 §0.1 门禁验收。
5. **与扫描器关系**：`byte_scan` 在 `_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED=False` 期间只能 unknown，验收前必须先解决 SC-1/SC-2。

## 7. 复现

```bash
cd /e/fnmusic-ext-212
python -m pytest offline_tests/test_acceptance_capture.py -q   # 18 passed
python -c "import proxy.acceptance_capture as c; print(c.SCHEMA_VERSION)"
```
