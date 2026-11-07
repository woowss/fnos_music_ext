# 维护启动模式（本地实现合同）

状态：本地代码与合成测试；尚未部署或在 fnOS 上验证。

当前 G0 证据阶段与设备生命周期门槛见 [G0 身份与生命周期证据计划](G0_MAINTENANCE_PROPOSAL.md)。本文只定义本地实现合同；本文或合成测试通过不代表 G0 通过，也不表示已在 NAS/systemd 安装、运行或验证。

```text
production_ready=false
```

## 开关

维护启动只能通过显式命令 `takeover run --maintenance-startup` 启用；相应 readiness 必须显式使用 `takeover ready --maintenance-startup`。普通 `takeover run` 将 `FNMUSIC_MAINTENANCE_STARTUP=0`、`FNMUSIC_BACKGROUND_JOBS=1`；维护模式将它们分别强制设为 `1` 和 `0`。最终值在读取父环境与 `.env` 后覆盖两者，避免冲突配置误开维护模式或后台任务。Supervisor 为导入预检和最终 Uvicorn 子进程复用同一个环境，因此这两个新进程都收到维护模式的 `1/0`，普通模式收到 `0/1`。`app` 只有在环境值精确等于字面量 `1` 时启用维护模式；`true`、`yes`、`false`、空值都不是维护模式。CLI 不从 `.env`、环境或响应推断 readiness 模式；遗漏 `ready --maintenance-startup` 会调用健康检查并收到本地 503，不会降级重试。该变量仅影响新进程启动期间的动作，不改变已运行进程。

维护模式保留 console 日志、HTTP 客户端创建、请求转发、正常任务取消和客户端关闭。Supervisor 创建的客户端在退出时仍各关闭一次；预先由调用方提供的客户端仍由调用方所有，lifespan 不关闭它们。它抑制以下九类启动副作用：

1. `app` 导入时不建立文件日志 handler，也不调用 `setup_logging()` 隐含的 `purge_stale()`；stdout/stderr 日志保留。
2. 不采集或写入 host 环境快照。
3. 不同步推荐版本标记，不失效或重命名推荐缓存，也不写 `.last-version`。
4. 不启动孤儿歌词扫描。
5. 不启动日志保留任务。
6. 不启动 `.env` 热加载任务。
7. 不启动洛雪脚本映射预热请求。
8. 不启动喜欢歌曲定时任务。
9. 不启动维护 Unix Socket 服务；普通模式仅在 `FNMUSIC_BACKGROUND_JOBS` 启用时启动并在退出时关闭它。维护启动即使继承后台任务开关，也不会发布该 Socket。

普通启动仍执行上述原有行为。请求处理本身没有被设为只读；这个开关只约束新进程启动副作用。

## 部署边界

维护模式的显式 readiness 只请求本地 Unix Socket 上的 `/_ext/livez`，并校验 HTTP 200、JSON 对象、`ok is True`、精确 service 名、维护标记、整数 PID 与内核 peer PID 相等；随后复验 ownership 记录、socket inode、peer PID、进程 start time 和 executable。该标记只用于选择本地维护 readiness 契约，不是身份凭据、授权、G0 网关证据或部署证明。普通 readiness 仍使用 `/_ext/healthz` 并保留原有音源检查语义。若维护模式进程误收到普通 `/_ext/healthz`，handler 会立即返回固定 HTTP 503，不创建依赖客户端、不发送请求。

本批仅是本地维护启动和 readiness 路径实现，不包含或替代 G0 网关身份/授权设计，未在 NAS 或 Linux systemd 上部署或验证，也不授权设备部署。设备侧是否能使用该实现、其有效 unit 如何启动/停止、writer 如何自然 drain、以及完整回滚是否安全，均由当前 [G0 身份与生命周期证据计划](G0_MAINTENANCE_PROPOSAL.md) 的独立阶段门决定；该计划本身仍为 BLOCKED。不得将本地合成测试解释为 NAS 上线许可；在设备证据和授权完成前 `production_ready=false`。

本地隔离测试在 pytest collection 前将 HOME、XDG、日志、缓存、曲库、音乐数据库、上游 Unix Socket 和全部音源 URL 指向临时位置；新进程使用拒绝所有 HTTP 的假客户端，Supervisor 的 subprocess 入口由哨兵替代，项目测试 fixture 将 ffmpeg/ffprobe 设为 `None`。这些哨兵仅用于本地回归，不会访问真实网络、ffmpeg、NAS 或曲库。

维护模式不是文件系统只读锁或事务边界。旧进程已经提交给 `asyncio.to_thread()` 的孤儿歌词 sweep 无法被 task cancellation 撤销；即使 awaiter 被取消，线程函数仍可能继续完成已经开始的文件操作。其他请求处理、被监督服务、console/既有日志写入、takeover state、Unix Socket 发布/恢复和 journal 仍可能产生写入。进入维护模式前必须独立停止/排空旧进程并核实这些写入主体；本开关本身不完成这些步骤，也不取消已提交的线程工作。

本地隔离测试覆盖 import/lifespan 各环境值矩阵、日志与推荐 marker/cache 保留、目录探测/歌词 sweep/日志 purge/LX warmup 哨兵、后台任务创建与取消，以及 owned/borrowed HTTP client 的创建和关闭语义。通过这些测试不代表 Linux fork、Unix Socket、systemd、fnOS、NAS 曲库或实际部署已验收。
