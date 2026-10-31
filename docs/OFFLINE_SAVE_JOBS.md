# 离线保存任务账本

> 合同：`FNMEXT-OFFLINE-SAVE-JOBS-20261024-01`。这是纯离线 SQLite 任务记录与显式同步派发组件；它不接入现有保存、下载、导入或管理页流程，也不代表媒体已保存、已转换、可播放或已入库。

## 范围与信任边界

`proxy/manual_save_jobs.py` 只记录调用方提供的清单、每个位置的顺序状态、取消标记和注入执行器返回的单文件字节提交结果。它不读取音频、访问网络或账号、不调用 NAS/媒体 API、不检查路径、不发布文件、不转码，也不创建线程、调度器、路由或 UI。

`ManualSaveJobsLedger` 必须由调用方显式构造，并传入绝对 SQLite 文件路径。模块导入不会打开文件或连接数据库。调用方负责选择私有、稳定的数据目录；该模块不证明路径授权，也不提供符号链接防护。数据库使用 SQLite 外键和 `synchronous=FULL`，并设置有界 busy timeout；这些设置不构成断电、存储硬件或文件系统持久性证明。

所有公开的读取、取消、执行和恢复方法都要求 `actor_scope`。它只是可信内部调用方提供的隔离标签，不是用户身份、登录态或授权凭据。模块不解析 UID、请求头或身份字段。随机 `job_id` 本身不能访问任务；其他 scope 与不存在的任务对外都返回固定 `not_found`。

## 清单与幂等

清单只接受以下字段，不接受额外元数据：

```json
{
  "kind": "single|playlist",
  "target_id": "internal-label",
  "provider_id": "provider-label",
  "collection_id": "source-collection-label",
  "collection_version": "source-version-label",
  "enumeration_complete": true,
  "entries": [
    {"position": 1, "source_id": "source-item-id", "source_status": "ok"}
  ]
}
```

标识符使用 ASCII 字母或数字开头，其余字符限于 ASCII 字母、数字、点、下划线、冒号和连字符，最长 128 个字符。`source_id` 可为 `null`；`source_status` 只允许 `ok`、`invalid_id`、`unavailable`、`missing`。位置必须是严格整数，从 1 开始连续递增；重复 `source_id` 会按原位置保留。`single` 恰有一个位置；`playlist` 有 1 到 1000 个位置。`enumeration_complete` 必须是布尔值；不完整清单可以保存，但不会派发。

清单先严格校验，再按 UTF-8、ASCII 转义、键排序和紧凑分隔符规范序列化，最多 1 MiB，并保存完整规范字节及 SHA-256。幂等键按 `(actor_scope, idempotency_key)` 唯一。同 scope/key 的规范字节完全相同时返回原任务；字节不同则固定返回 `idempotency_conflict`；不同 scope 可以复用同一 key。创建、容量检查和位置插入在一个 `BEGIN IMMEDIATE` 事务中完成，幂等重放不消耗配额。

## 状态、派发与取消

账本最多保留 1000 个任务、10000 个位置。位置状态为 `queued`、`running`，或现有单文件核心的七种结果：`published`、`published_cleanup_pending`、`conflict`、`cancelled`、`rejected`、`failed`、`commit_unknown`。每个任务最多一个 `running` 位置。

`run_next(actor_scope, job_id, executor)` 在短 `BEGIN IMMEDIATE` 事务中选取最早的 queued 位置，写入随机 claim token 并提交；之后才在事务外同步调用 `executor(position_snapshot, cancellation_check)`。没有后台 worker。不同连接竞争时不会重复 claim，也不会跳过最早位置。执行器必须返回严格有效的 `manual_save_core.SaveResult`：结果类型、Outcome、reason 白名单、布尔 `cleanup_pending` 和两个 false readiness 字段都必须有效。异常或无效返回会作为 `commit_unknown` 记录，因为执行器可能已产生外部副作用；异常文本不会保存。

完成写入用 `running + claim_token` 做 compare-and-set。旧 claim 的迟到完成不会覆盖恢复结果。执行器运行后若结果写入失败，调用返回固定 `completion_unknown`，不会自动再次执行；位置仍可能处于 `running`，需由操作者显式处理。SQLite busy timeout 有界，不进行重试循环；claim 事务失败时绝不调用执行器。

取消只将任务标记为已请求，并把仍 queued 的位置设为 `cancelled`；running 位置保留，执行器可通过传入的 `cancellation_check` 协作检查。检查发生错误时按已取消处理。取消不删除媒体、不撤回已发布结果；取消与发布竞态下，已报告的发布结果会保留。

任务出现任何 `commit_unknown` 后即冻结后续派发。打开数据库不会自动恢复或执行任务。`recover_stale_running(..., worker_stopped=True)` 需要调用方显式声明旧 worker 已停止，把仍 running 的位置改为 `commit_unknown` 并清除 claim token；API 无法验证该声明。恢复不会重排、重试、按文件存在性自动对账，也不会删除历史。之后的外部核对必须由单独授权的流程完成。

## 汇总与结果含义

`get_job` 返回的 manifest 是从数据库重新解码出的副本；修改输入字典或返回快照不会改变已存记录。汇总计数从位置行派生，计数总和等于位置总数。`partial`、`completed_with_issues` 与 `reconciliation_required` 不表示整项任务成功；`completed_bytes_reported` 只表示所有执行器都报告了字节发布，不表示音频有效或被索引。

所有公开快照均固定带有 `audio_verified=false`、`production_ready=false`、`import_ready=false`。即使状态为 `published`，也只表示执行器报告了字节发布。NCM 必须转换为真实 FLAC、其他格式保持不变的产品规则不由此账本实现或验证；这里没有转换器或媒体检查器。

## 数据库拒绝与恢复边界

首次打开空 SQLite 文件时创建本模块唯一标识和版本 1 schema。已有数据库必须有匹配的应用 ID、schema 版本、表、索引、外键、数据完整性和可校验的规范 manifest。未知版本、外部 schema、损坏或非法内容会固定报错；模块不迁移、清空、覆盖或自动重建已有数据。容量上限也在重新打开时验证。

SQLite 事务保证账本内的原子更新，但不让执行器副作用与数据库提交形成分布式事务。因此，`commit_unknown` 和 `completion_unknown` 都需要人工按独立证据处理；账本不基于目标文件是否存在来猜测执行结果。

## 离线验证

测试只在 `TemporaryDirectory` 中创建真实 SQLite 文件，不接触真实音频、NAS、网络或生产数据库。从仓库根目录运行：

```powershell
py -B -m unittest discover -s offline_tests -p test_manual_save_jobs.py -v
py -B -m unittest discover -s offline_tests -p test_manual_save_core.py -v
```

这些测试验证本地 SQLite 状态转换和账本并发约束；不证明文件系统媒体发布、音频解码/转换、外部 worker 生命周期、断电持久性、生产接入或导入完成。
