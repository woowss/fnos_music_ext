# 离线单文件提交核心

> 合同：`FNMEXT-OFFLINE-SAVE-CORE-20261023-01`。仅为内部字节提交协议和内存假后端；不代表已实现可用的保存功能。

## 范围

`proxy/manual_save_core.py` 只负责校验一个内部保存计划、把调用方传入的有限字节块写入注入的私有暂存，并调用存储适配器提供的原子不覆盖发布操作。它不读取目录或网络，不连接账号、下载器、转码器、WebUI、任务队列或 NAS。

接口为 `plan_single_file(target_id, basename, source_format, expected_bytes, max_bytes)` 和 `execute_single_file(plan, chunks, storage, cancelled=None)`。目标 ID 是内部标签，不是授权凭证；basename 必须与声明扩展名相符。计划和参数校验拒绝发生在调用存储动作之前；流处理阶段遇到 NCM、长度或块约束错误时，可能已经创建暂存，随后会尝试清理本任务暂存。结果只描述字节提交状态，始终带 `audio_verified=false` 和 `production_ready=false`。

存储对象只能提供以下动作：

1. `begin_owned_stage(target_id)`：取得本任务独占的不透明暂存句柄。
2. `append(stage, bytes)`：按输入顺序追加块。
3. `seal(stage)`：完成暂存内容。
4. `publish_noreplace(stage, basename)`：原子发布；只返回 `published` 或 `conflict`。
5. `abort_owned_stage(stage)`：失败/取消时清理本任务暂存。
6. `release_owned_stage(stage)`：成功发布后释放暂存资源。

核心没有删除最终文件或覆盖目标的存储方法。假后端只在测试内存中模拟这些语义。

## 限制和结果

- 硬上限 512 MiB；最多 4096 个非空 `bytes` 块，每块最多 1 MiB。长度必须等于调用方声明值；不在内存中拼接整条音频。
- basename 拒绝路径分隔符、冒号、控制字符、`.`/`..`、Windows 保留设备名、尾随空格/点、超长名及扩展名不一致；不会自动修正或改名。
- 有限格式集合：aac、aiff、ape、flac、m4a、mp3、ogg、opus、wav、wma。
- 声明 `.ncm` 或跨块前缀识别到 `CTENFDAM` 会在发布前拒绝；不解密、不转码、不伪装成 FLAC。
- 冲突保留既有内容。发布异常返回 `commit_unknown` 并保留暂存以供受控核查，不重试。发布成功后释放暂存失败返回 `published_cleanup_pending`。取消在 seal 后、发布前再次检查；取消检查异常返回固定 `cancel_check_failed` 并尝试清理暂存。发布后不会撤销目标。
- 对外 reason 是固定代号，不包含异常、路径或正文。清理失败保留主结果并标记 `cleanup_pending`。

## 验证

从仓库根目录运行：

```powershell
<隔离 Python> -B -m unittest discover -s offline_tests -p test_manual_save_core.py -v
```

测试仅使用内存假存储。相同名称的两次顺序调用只验证先成功、后冲突及保留旧内容，不验证真正并发下适配器的原子性。导入副作用检查只覆盖常见的文件、socket、子进程、环境查询和线程入口；不是完整沙盒证明。

## 后续真实适配器的独立门槛

内存测试不能证明磁盘原子性、符号链接和目录替换防护、目录授权、崩溃持久性、空间耗尽、Linux 并发竞争、NCM 转换或音频可解码性。任何文件系统适配器需独立合同和审查，证明目录句柄锚定与授权、临时文件所有权、原子不覆盖、冲突与故障恢复、取消竞态和清理边界。接入真实保存还需通过 G0、应用/操作者目录授权、受支持的音频取得路径、真实格式验证，以及针对明确样曲和目标目录的单次写入授权。

因此此文件不能作为 NAS 上线、管理页开放或“歌曲已保存/已入库”的依据。
