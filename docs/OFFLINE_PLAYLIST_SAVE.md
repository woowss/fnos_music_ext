# 有序批量保存引擎（FNMEXT-PLAYLIST-IMPORT-20261101）

本文件记录手动保存的**纯离线、可注入**"有序批量保存"引擎原语：
`proxy/manual_save_playlist.py`。本轮只交付引擎原语与离线测试，**不接**管理页/网关、
不新增 HTTP 长请求、不联网、不部署、不 push；不修改任何已跟踪文件，也不新增依赖
（仅标准库 + 调用方注入的执行器）。

批量语义的权威来源是单曲 `manual_save_route.save_track`：本模块只负责按位置串行
调度、预算/取消控制、失败分类与固定信封归一化，所有真实取数/下载/打标/发布仍由
注入的 `save_one` 完成。

## 1. 不变式（本模块的硬约束）

- 模块内**不存在**默认真实 I/O 路径：没有默认 `save_one`、没有默认依赖包。
  导入本模块不会接触网络、文件系统、NAS、ffmpeg/curl 或 WebUI 网关。
- `save_playlist` 的 `save_one` 必填；测试注入 fake，生产未来绑定
  `manual_save_route.save_track`（本轮不绑定、不暴露 HTTP）。
- 返回信封与 route 一致：`{ok, code, message, data}`；信封 `message` 固定、
  不插值、不回显 URL/cookie/路径/原始异常文本。
- `ok=True` **当且仅当** `code == "ok"`（即 `status == "completed"`）。
- 每一输入位置恰好产生 1 个 item，顺序与输入一致；不可执行项（`None`）也在 items 中。
- `counts.total == positions_total == len(items)`，且
  `published + conflict + failed + skipped + not_executed == total`。
- 串行执行；重入/并发批量立即 `busy`（0 次执行）；批量只持有模块自己的
  `_PLAYLIST_LOCK`，**不** acquire/timeout/inspect route 的 `_SAVE_LOCK`。

## 2. 公开 API

```python
def save_playlist(
    song_ids: object,                    # list|tuple；元素为 int(>0) 或 None
    *,
    save_one: Callable[[int], object],   # 必填执行器
    start_position: object = 1,          # 本段第一首的全局 1-based 位置
    budget_seconds: object = PLAYLIST_TOTAL_BUDGET_SECONDS,
    cancelled: Callable[[], bool] | None = None,
    now: Callable[[], float] | None = None,
) -> dict
```

`now=None` 时使用 `time.monotonic`（时钟不是 I/O）；`cancelled=None` 表示不可取消。

公开常量：

| 常量 | 值 | 含义 |
|---|---|---|
| `PLAYLIST_MAX_TRACKS` | `100` | 单批最大位置数 |
| `PLAYLIST_TOTAL_BUDGET_SECONDS` | `900.0` | 默认批量预算 |
| `PLAYLIST_MAX_BUDGET_SECONDS` | `3600.0` | 预算上限（含） |
| `PLAYLIST_MIN_REMAINING_SECONDS` | `60.0` | 预算地板：剩余 `< 60` 即停止 |
| `PLAYLIST_ITEM_FIELDS` | 21 键元组 | 每个 item 的精确键与顺序 |
| `PLAYLIST_CONTINUE_CODES` | 11 码 frozenset | 只失败当前位置，后续继续 |
| `PLAYLIST_ABORT_CODES` | 6 码 frozenset | 环境级中止整批 |

`PLAYLIST_ITEM_FIELDS`（顺序固定，共 21 键）：

```
position, song_id, status, code, message, title, artist, basename, format,
quality_requested, quality_actual, quality_actual_status, quality_fallback,
size, md5, published_size, published_md5, cover_status, cover_error,
lyric_status, duplicate_of_position
```

## 3. 输入校验（先全量校验，后执行）

校验失败时 `data={}`，且**任何位置都不执行**（0 次 I/O）。

| 条件 | 信封 code |
|---|---|
| `song_ids` 非 `list`/`tuple`，或为空 | `invalid_request` |
| 长度 > `PLAYLIST_MAX_TRACKS` | `too_many_positions` |
| 元素既不是 `int>0`（且非 bool）也不是 `None` | `invalid_song_id`（整批拒绝） |
| `start_position` 非 `int>0`（且非 bool） | `invalid_request` |
| `budget_seconds` 非 int/float、bool、非有限、`<=0` 或 `>3600` | `invalid_request` |
| `save_one`/`now`/`cancelled` 非 callable（后两者给了值时） | `invalid_request` |

元素校验对**所有**位置生效：`[1, 2, "3"]` 整体拒绝，不会先保存 1、2。
重复 `song_id` 合法：不预去重、逐位置独立执行；`duplicate_of_position` 记录本次调用
内更早相同 `song_id` 的最早位置（首现为 `null`），**仅报告、不改变执行**。
是否冲突由 store（`publish_noreplace`）唯一裁决，本模块不做伪 conflict 优化：
同一 `song_id` 第二次通常因 basename 相同返回 `conflict`；若元数据变化导致 basename
不同，则允许两条都 `published`。

## 4. 执行、锁与并发

- 严格串行：`for position: result = save_one(song_id)`；每个可执行位置恰好调用一次。
- 整批持有模块级 `_PLAYLIST_LOCK = threading.Lock()`（非阻塞获取，`finally` 释放）。
- 第二个批量/重入拿到锁失败 → 立即 `busy`：`status="incomplete"`、
  `stop_reason="busy"`、`items` 全部 `not_executed`、`elapsed_seconds=0.0`、0 次执行。
- 外部单曲在位置间隙抢占 `route._SAVE_LOCK` 时，下一位置 `save_one` 返回 `busy`
  （`PLAYLIST_ABORT_CODES`）→ 环境级中止整批，已完成位置的真实结果保留。
- 本轮只做引擎原语：不暴露 HTTP 长请求，不做位置内取消/超时注入；在飞
  `save_one` 不可中断。

## 5. 预算、取消与停止顺序

每个位置固定顺序：**取消检查 → 预算检查 → 执行**。

```python
started = now()
for i in 0..len(song_ids)-1:
    if cancelled is not None:
        try: stop if cancelled()           # 检查抛异常按已取消处理
        except Exception: stop("cancelled")
    used = max(0.0, now() - started)
    if budget - used < PLAYLIST_MIN_REMAINING_SECONDS:
        stop("budget_exhausted")
    if song_ids[i] is None: item = skipped; continue
    result = save_one(song_ids[i])
elapsed = max(0.0, now() - started)
```

- **预算**：`0 < budget <= 3600`，有限、非 bool。地板判断严格小于：剩余恰好
  `60.0` 时仍允许该位置执行。停止后已执行位置保留真实结果，其余 `not_executed`；
  `stop_reason`/信封 `code` = `budget_exhausted`；`next_position` = 第一个未执行位置。
- `elapsed_seconds` 在最后一个位置执行完之后才测量，因此**可能大于**
  `budget_seconds`（例如最后一首执行耗尽了剩余时间）；这是契约允许并在此写明的行为。
- **取消**：每位置执行前检查一次；truthy → `stop_reason="cancelled"`；
  检查抛异常 → 按已取消处理；在飞位置不可取消。
- **提前停止**时 `status="incomplete"`，`next_position`=第一个未执行位置
  （若中止发生在末尾、已无未执行位置则为 `null`）。
- 走到末尾且全部 `published` → `status="completed"`, `code="ok"`, `ok=True`，
  `stop_reason="input_exhausted"`。
- 走到末尾但至少 1 个非 `published`（含 `conflict`/`failed`/`skipped`）→
  `status="completed_with_issues"`, `code="partial"`, `ok=False`。

## 6. 失败分类

继续（只失败当前项，后续继续执行）：

```
conflict, service_unavailable, no_quality_available, quality_unverified,
quality_identity_mismatch, trial_only, download_target_untrusted,
download_failed, integrity_mismatch, invalid_audio, tag_failed
```

中止（环境级，停止派发后续位置）：

```
disabled, config_invalid, busy, staging_unavailable, publish_failed, internal
```

归一化规则：

| 执行器返回 | item 状态 | 信封 |
|---|---|---|
| `ok=True, code="ok", data=dict` | `published` | 继续 |
| `ok=False, code="conflict"` | `conflict` | 继续；`message="目标文件已存在"` |
| `ok=False, code` ∈ CONTINUE | `failed` | 继续 |
| `ok=False, code` ∈ ABORT | `failed` | 停止，`code`=该码 |
| 非 Mapping / `ok` 非 bool / `code` 非 str 或未知 / `ok=True` 但 `code≠"ok"` / `data` 非 dict | `failed` + `internal` | 停止，`code="internal"` |
| `save_one` 抛异常 | `failed` + `internal` | 停止，原始异常文本不回显 |

`conflict` 语义：不覆盖、不改名、不换目录；只报告 store 的裁决。NCM 路径不产生
`.ncm`，本模块只上报 route 的 code，不新增格式分支。

## 7. `data` 与 item 字段

`data` 键（顺序固定）：

```
status, stop_reason, start_position, next_position, positions_total,
counts, items, budget_seconds, elapsed_seconds
```

| 键 | 取值 |
|---|---|
| `status` | `completed` / `completed_with_issues` / `incomplete` |
| `stop_reason` | `input_exhausted` / `budget_exhausted` / `cancelled` / ABORT 码之一 |
| `counts` | `total, published, conflict, failed, skipped, not_executed` |
| `next_position` | 提前停止=第一个未执行位置；`input_exhausted` 时 `null` |
| `budget_seconds` / `elapsed_seconds` | float；elapsed 可能 > budget（见 §5） |

item 状态与固定字段：

| status | code | message | 交付字段来源 |
|---|---|---|---|
| `published` | `ok` | `""` | 执行器 `data` 白名单复制 |
| `conflict` | `conflict` | `目标文件已存在` | 白名单复制（`published_*` 强制 0/`""`） |
| `failed` | route 失败码或 `internal` | 按错误码选择的本地固定中文；忽略执行器 message | 空 |
| `skipped` | `invalid_song_id` | 本地固定中文 | 空 |
| `not_executed` | `not_executed` | `未执行` | 空 |

白名单复制与归一化（仅 `published`/`conflict`）：

- `title`/`artist` ≤512 字符，`basename`/`format` ≤255，`quality_*` ≤120；只接受
  `str` 并截断，非 `str` → `""`。
- `quality_actual_status` ∈ `reported|unknown`：仅当执行器声明 `reported` 且
  `quality_actual` 命中 `[a-z0-9][a-z0-9._-]{0,63}` 才保留 `reported`，否则降
  `unknown` 并清空 `quality_actual`；缺省 `unknown`。
- `quality_fallback`：bool，仅字面 `True` 为真。
- `size`：非负 int（bool→0）；`md5`：32 hex 转小写，否则 `""`。
- `published_size`：仅 `published` 且 int>0；`published_md5`：仅 `published` 的
  32 hex 小写；其它状态强制 `0`/`""`（`conflict` 也强制清零）。
- `cover_status` ∈ `preserved|embedded|unavailable|unsupported|error|not_published|not_attempted`，
  白名单复制，其它 `not_attempted`。
- `cover_error`：仅 `cover_status=="error"` 时保留 8 类之一
  （`host_untrusted|url_invalid|address_untrusted|http_error|payload_invalid|decode_failed|embed_failed|unknown`），
  否则 `""`。
- `lyric_status` ∈ `published|unavailable|error|conflict|not_attempted|unknown`，
  白名单复制，其它 `not_attempted`。
- 未白名单键（`signed_url`/`cookie`/`target_dir`/`tagged` 等）全部丢弃；
  结果保证 `json.dumps` 成功。失败项的 `message` 只取本模块错误码文案表，
  不复制执行器文本；成功/冲突项经 `_copy_success_fields` 白名单复制的短文本
  字段（`title`/`artist`/`basename`/`format`/`quality_*`）只按长度上限截断、
  不校验内容，执行器回填非常规文本时会原样保留。

## 8. 测试覆盖映射

新增 `proxy/tests/test_manual_save_playlist.py`（120 用例）：编排层用 fake `save_one`，
集成层用真实 `route.save_track` + 全注入 `SaveDeps` fake（禁真实网络/真实 store）。

| 关键实现 | 覆盖用例 |
|---|---|
| `_validate_inputs`（§3 全部输入分支） | `test_empty_playlist_is_invalid_request`、`test_non_sequence_input_is_invalid_request`、`test_invalid_song_id_entries_reject_whole_call`、`test_over_max_tracks_rejected`、`test_exactly_max_tracks_accepted`、`test_invalid_start_position_rejected`、`test_invalid_budget_rejected`、`test_invalid_now_or_cancelled_rejected` |
| `_finalize`（信封/status/next_position/counts） | `test_all_published_completes_ok`、`test_counts_and_positions_invariants`、`test_item_field_set_is_exact` |
| `_normalize_result` + `_copy_success_fields` | `test_executor_data_is_whitelisted_and_normalized`、`test_executor_malformed_results_abort_as_internal` |
| 失败消息固定化 | `test_executor_failure_messages_are_fixed_for_all_failure_codes`（17 码参数化，断言敏感文本不出现）、`test_executor_failure_missing_or_non_string_message_uses_fixed_text` |
| 失败分类/中止 | `test_continue_codes_do_not_stop_later_positions`（11 码参数化）、`test_abort_codes_stop_dispatch`（6 码参数化）、`test_executor_exception_aborts_as_internal`、`test_conflict_continues_and_is_reported` |
| `_duplicate_link` / `None` 跳过 | `test_duplicate_positions_are_all_executed_and_linked`、`test_none_positions_are_skipped_without_executor_calls` |
| `_run` 取消逻辑 | `test_cancelled_before_first_position_executes_nothing`、`test_cancelled_between_positions_marks_remainder_not_executed`、`test_cancelled_check_error_counts_as_cancelled` |
| `_run` 预算逻辑 | `test_budget_exhaustion_marks_remainder_not_executed`、`test_budget_boundary_allows_position_when_remaining_equals_floor`、`test_budget_bounds_accept_positive_values`、`test_budget_sufficient_completes` |
| `next_position`/`start_position` | `test_next_position_and_start_position_resume_semantics` |
| `_PLAYLIST_LOCK` | `test_locked_playlist_returns_busy_without_execution`、`test_concurrent_second_batch_returns_busy`、`test_playlist_lock_released_after_every_outcome` |
| 序列化/边界 | `test_result_is_json_serializable_and_bounded` |
| 真实 route 串行发布 | `test_pipeline_three_ordered_tracks_publish_in_order` |
| store 冲突权威 | `test_pipeline_duplicate_position_conflicts_without_overwriting`、`test_pipeline_duplicate_same_id_different_metadata_publishes_both` |
| 单曲失败不中止 | `test_pipeline_track_failure_does_not_stop_later_tracks` |
| 预算停止 | `test_pipeline_budget_stops_before_later_tracks` |
| 环境级中止 | `test_pipeline_disabled_config_aborts_without_touching_store_or_http` |
| 无默认 seam 参与 | `test_pipeline_default_seams_are_not_used`（monkeypatch 全部 `route._default_*` 为 fail） |

## 9. 验收记录（真实输出）

环境：Windows，`Python 3.14.6`，解释器绝对路径
`C:\Users\admin\AppData\Local\Temp\fnmusic-testvenv-py314\Scripts\python.exe`；
Windows 下必须设置 `PYTHONUTF8=1`。日期：2026-11-01。

基线（新增文件不减少原通过数）：

```bash
PYTHONUTF8=1 <py> -m pytest \
  proxy/tests/test_manual_save_route.py \
  proxy/tests/test_manual_save_gateway.py \
  proxy/tests/test_manual_save_delivery_guard.py \
  proxy/tests/test_manual_save_metadata.py -q
# 455 passed, 2 skipped

PYTHONUTF8=1 <py> -m pytest offline_tests -q
# 214 passed, 53 skipped, 196 subtests passed

cd webui-service && node test_app_js.js
# 102/102 passed, 0 failed
```

新增测试：

```bash
PYTHONUTF8=1 <py> -m pytest proxy/tests/test_manual_save_playlist.py -q
# 120 passed

# 四件套 + 新引擎（合并计数 = 465 + 120）
PYTHONUTF8=1 <py> -m pytest \
  proxy/tests/test_manual_save_route.py \
  proxy/tests/test_manual_save_gateway.py \
  proxy/tests/test_manual_save_delivery_guard.py \
  proxy/tests/test_manual_save_metadata.py \
  proxy/tests/test_manual_save_playlist.py -q
# 585 passed, 2 skipped
```

工作区状态（本轮交付）：

- 已跟踪文件 0 diff（`git diff --stat` 无输出）；未执行 commit/add/stash。
- `git status --porcelain` 只有 3 个新增文件与 3 个既有受保护未跟踪文件：
  新增 `proxy/manual_save_playlist.py`、`proxy/tests/test_manual_save_playlist.py`、
  `docs/OFFLINE_PLAYLIST_SAVE.md`；受保护
  `proxy/manual_save_verified_pipeline.py`、
  `offline_tests/test_manual_save_verified_pipeline.py`、
  `docs/OFFLINE_VERIFIED_PIPELINE.md` 内容未读写/未修改。
- 未新增依赖；未联网；未运行真实 ffmpeg/curl；未访问 NAS。

## 10. 残余限制

- **只是引擎原语**：本轮不暴露 HTTP 端点、不接管理页/网关、不做任务持久化；
  进程内 `threading.Lock` 不跨进程，多进程需要上层另加互斥。
- **不做位置内取消/超时**：`save_one` 在飞时不可中断；取消/预算只在位置边界生效。
- **elapsed 可超预算**（见 §5），调用方不能把 `elapsed_seconds <= budget_seconds`
  当作不变式。
- **不预测冲突**：不缓存跨位置元数据、不预去重；basename 是否相同由 store 裁决。
  重复 `song_id` 第二次通常 `conflict`，元数据变化时允许两次都发布。
- **依赖注入质量决定语义**：执行器返回非法/未知 code 一律归一化 `internal` 并中止；
  白名单外的字段与值全部丢弃，可能丢失上游新增但未纳入契约的元数据。
- **`quality_actual` 的 fail-closed 裁决**：执行器未声明 `reported`（含缺失/未知）
  时清空 `quality_actual`，避免"未核验却展示档位"；如未来需要保留未核验 token，
  必须先修契约与本模块测试。
- **时钟异常**：注入的 `now` 在批次开始前抛异常 → 全部 `not_executed`、
  `stop_reason="internal"`、`ok=False`；循环中抛异常同样按 `internal` 中止。
  默认 `time.monotonic` 不受影响。
- **失败消息固定化**：失败 item 的 `message` 只从本模块的错误码文案表生成，忽略
  执行器返回的 message，避免 URL、cookie、路径或原始异常文本经该字段进入批次结果。
  成功/冲突项的白名单短文本字段仍由执行器提供（见 §7），本模块只做长度上限。
