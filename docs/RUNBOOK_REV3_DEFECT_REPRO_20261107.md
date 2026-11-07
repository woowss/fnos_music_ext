# runbook Rev-3 两处代码级缺陷的独立复现（2026-11-07）

> **证据文档，不是验收、不是部署授权。** 目的：对 `docs/LIFECYCLE_RUNBOOK_REV3_REVIEW_20261107.md` 指出的 R2/R3 两处缺陷做**独立复现**，确认它们不是复审者的笔误，并验证修法方向可行。全程只在 `/tmp` 临时目录与内存中操作，未读写设备上的生产路径、未执行任何 runbook 步骤。
> 执行者：Lead（未参与 runbook 编写，也未参与该轮复审）。时间：2026-11-07 13:12–13:14（本地）；Linux 侧 12:13 CST 前后。

## 1. 被复现的两个缺陷

| 编号 | 复审原文要点 | 影响 |
| --- | --- | --- |
| **R2** | §5.3:1075-1076 把**路径**传给 `os.fchmod`/`os.fchown`，而这两个 API 要求文件描述符 | 抛 `TypeError`，`.env` 不会被替换；属主/模式保留不可达 → 正常窗口 Step 2 直接 fail-closed 终止 |
| **R3** | §5.4-3.5 采样写 `w3.env.$tag.sha256`（:1260），两轮比对用 `w3.$f.t0`（f=`.env` → `w3..env.t0`，:1273-1275） | `diff` 找不到文件 → 3.5 恒 REFUSE → marker 永不生成 → §5.5 被永久挡住 |

## 2. 复现脚本

`scripts/repro_runbook_rev3_defects.py`，SHA-256 `4ed3984211ac7c791b4b55186ca2d119deddce9835edeb91304f2236a8f52ddf`（6,3xx B），仅标准库、只读仓库、只用 `tempfile.TemporaryDirectory()`。

检查项：

| ID | 检查 | 说明 |
| --- | --- | --- |
| D1a | `os.fchmod(path, 0o600)` 抛 `TypeError` | 复现 R2 的根因（与平台无关） |
| D1b | fd 形式 `os.fchmod(fd, 0o600)` 生效 | 验证修法方向（POSIX） |
| D1c | fd 形式 `os.fchown(fd, uid, gid)` 保留属主 | 验证修法方向（POSIX） |
| D1d | `os.replace(tmp, path)` 后模式仍为 0600 | 验证"替换不丢元数据" |
| D2a | 写入名与查找名不同 | 复现 R3（`w3.env.t0.sha256` vs `w3..env.t0`） |
| D2b | 按查找名找不到已写入的文件 | 复现 R3 的后果 |
| D2c | 统一命名后两侧一致 | 验证修法方向 |
| D2d | 存在性断言能捕获该缺陷 | 验证"加断言"的有效性 |

## 3. 运行结果

### 3.1 Windows（本地）

```
[PASS] D1a os.fchmod(path) raises TypeError: TypeError as expected: 'str' object cannot be interpreted as an integer
[SKIP] D1b fd form applies mode: Windows does not implement POSIX mode bits
[SKIP] D1c fd form applies owner: os.fchown absent on this platform (POSIX-only)
[SKIP] D1d os.replace keeps mode: Windows does not implement POSIX mode bits
[PASS] D2a names differ (defect reproduced): written='w3.env.t0.sha256' looked_up='w3..env.t0'
[PASS] D2b lookup misses the written file: exists('w3..env.t0')=False
[PASS] D2c corrected naming matches: 'w3.env.t0.sha256' == 'w3.env.t0.sha256'
[PASS] D2d existence assertion catches the defect: open() on missing lookup path raises FileNotFoundError

8/8 checks passed   (4 项 SKIP，均为 Windows 无 POSIX mode/属主语义)
```

### 3.2 Linux（NAS `/tmp`，root 与 kurumi 各一轮）

以 `scp` 把脚本复制到 `/tmp/fnm-repro-rev3.py`（与本地同哈希 `4ed39842…`），分别以 `root` 与 `runuser -u kurumi` 运行：

```
[PASS] D1a os.fchmod(path) raises TypeError: TypeError as expected: 'str' object cannot be interpreted as an integer
[PASS] D1b fd form applies mode: mode=0o600
[PASS] D1c fd form applies owner: uid=0 gid=0          （root 轮）
[PASS] D1d os.replace keeps mode: mode=0o600
[PASS] D2a names differ (defect reproduced): written='w3.env.t0.sha256' looked_up='w3..env.t0'
[PASS] D2b lookup misses the written file: exists('w3..env.t0')=False
[PASS] D2c corrected naming matches
[PASS] D2d existence assertion catches the defect
8/8 checks passed   root_exit=0

（kurumi 轮同上，仅 D1c 为 uid=1000 gid=1001；8/8，kurumi_exit=0）
```

运行后脚本已 `rm -f /tmp/fnm-repro-rev3.py`（输出 `cleaned`），未在设备留下文件。

## 4. 结论

1. **R2 的根因被独立证实**：`os.fchmod(path, mode)` 在 Linux 与 Windows 都抛 `TypeError: 'str' object cannot be interpreted as an integer`；因此 runbook Rev-3 §5.3 的 `.env` 编辑脚本必然失败，属主保留逻辑不可达。
2. **R3 的命名错配被独立证实**：写入名 `w3.env.t0.sha256` 与查找名 `w3..env.t0` 不同，按查找名取文件必然 `FileNotFoundError`；若脚本对缺失文件直接 REFUSE，则 3.5 阶段的 marker 永不生成，§5.5 被永久挡住。
3. **修法方向可行**：在 `with open(tmp, ...)` 内用 fd 形式 `os.fchmod(fd, ...)` / `os.fchown(fd, ...)`（放在最终 `fsync` 之前）在 Linux 上把模式设为 `0600`、属主保持为调用者（root 轮 uid=0、kurumi 轮 uid=1000/gid=1001）；统一命名 + 存在性断言可消除 R3。
4. 本复现只验证"缺陷真实存在 + 修法方向可行"，**不**验证 runbook Rev-4 的实际改写是否正确——那需要下一轮独立复审。

## 5. 边界

- 只在 `/tmp` 临时目录与内存中操作；未读写设备生产路径、未停启服务、未执行 runbook 任何步骤。
- Windows 轮 4 项 POSIX 语义检查为 SKIP（不是 fail）；Linux 轮覆盖了这些项。
- 未测试真实 `.env`（避免触碰生产配置）；D1 用的是临时样例文件。
- `os.fchown` 在 Windows 不存在，脚本已做平台分支；runbook 目标平台是 Linux，因此 Linux 结果是权威的。
- 本文件不改变 §0.1/§0.2、P7 或任何门禁；不构成执行授权。

<!-- DOC-HASH-STAMP: sha256(all bytes before this line, UTF-8) = 89e8f3060bb797b0d534241973da2aaa7de25bd4ea6b7c14c8c8d8f92c18805f -->
