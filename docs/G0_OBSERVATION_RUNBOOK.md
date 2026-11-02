# G0 一次性观测补丁运行手册

> **ARCHIVED / DO NOT EXECUTE**：本文件保留旧版同一 service cgroup 内启动 root shell、使用 pidfd 热替换 gateway 及其恢复步骤作为历史记录。该路径已废弃，不能作为当前候选或替代方案；其中的命令、暂存、替换、停止和回滚步骤均不得执行。当前证据计划见 [G0 身份与生命周期证据计划](G0_MAINTENANCE_PROPOSAL.md)。本档案的旧 commit/hash 只具历史意义，不能作为当前本地候选或 NAS 安装 manifest。

> 以下操作内容仅保留为历史材料；即使将来取得其他操作授权，也不得照此运行。当前计划见上方链接。
> 本地代码与合成测试不构成部署授权，也不表示 Linux、NAS、真实 peer 或 G0 已验收。
> G0 保持 BLOCKED；本补丁不开放认证、
> 授权、保存、任务或导入能力。

> **历史快照提示（2026-10-24）**：下方旧审查哈希表绑定此前的诊断快照。
> `FNMEXT-MANAGEMENT-CLAIMS-20261024-01` 已修改当前工作树的 `proxy/webui_gateway.py`，
> 因此旧表不能用于部署当前网关；未来候选必须重新冻结文件并独立审查。
> 本地严格解析只拒绝重复或畸形身份 claim，不证明 claim 来源可信，也不阻止单个伪造管理员头；
> fnOS 实际头格式未验证，G0 仍为 BLOCKED。

## 目的与输出

诊断默认关闭。显式启动时，它只关联一条精确的
`GET /app/fnmusic-ext/api/status?g0_observe=<32位小写十六进制>` HTTP/1.0 或 HTTP/1.1
请求，保存同一已接受 Unix socket 的 Linux peer 元数据与身份头结构摘要。marker 是非秘密关联标签，
不是凭据。记录只包含固定字段、计数、存在性、规范性及固定错误码；不会保存身份值、Cookie、
Authorization、请求体或响应体。记录注明 `diagnostic_only: true`，不作身份或权限结论。

固定文件为 `/run/fnmusic-ext/g0-observe/<marker>.jsonl`。一次运行最多写两行（ready 加
observation 或终态），总量最多 8 KiB；到期时间为 15 至 120 秒，默认 60 秒。过期只停止观察，
网关仍继续转发。输出文件会保留，需按授权决定如何保留或清理。

本批本地审查的文件清单和 SHA-256：

| 部署文件 | 本地审查版本 SHA-256 |
| --- | --- |
| `proxy/webui_gateway.py` | `b0024b44a2d5b4252cfa81630b7dbf033d8297e373d42867e6abbd6523418b24` |
| `proxy/gateway_observation.py` | `ba232d250431852dfbf4304ae80795b98b776cadab22089820e7f323a2474225` |
| `proxy/gateway_identity.py` | `f3ed78d9f021d81b27e94366c5b7e577854ee3d551e769af22f003ad5ddbdfb9` |

部署时只传送上表三份审查文件。不要复制整个 dirty 工作区，也不要转换行尾。历史 NAS 原网关
LF 字节哈希为 `b87e39fd8e03fed104a59e9926891c353c4c09d695142182486fe074d78fdb22`；本地旧网关
CRLF 字节哈希为 `8eb87ea00b9fc3ef8e1229af66f7cb7f08bcfc218ed784f143405efde47db754`。
这两个基线哈希不同。部署前必须重新读取设备上的文件、权限、所有者和模块存在性；复制补丁后，
目标文件的 SHA-256 必须与上表本地审查版本一致。不得静默做 CRLF/LF 归一化。

## 本地离线验证记录（2026-10-23）

基准为 `main@c4a257a007c355b483c9916df834bf615d5d7351`，本次修复只调整 observer 首个头部分隔符的
搜索起点，并增加合成回归；`webui_gateway.py` 和 `gateway_identity.py` 的文件字节未变。以下哈希绑定
本轮修复前草稿与修复后本地快照，不代表部署或 Linux/NAS 验收：

| 文件 | 修复前 SHA-256 | 修复后 SHA-256 |
| --- | --- | --- |
| `proxy/webui_gateway.py` | `b0024b44a2d5b4252cfa81630b7dbf033d8297e373d42867e6abbd6523418b24` | `b0024b44a2d5b4252cfa81630b7dbf033d8297e373d42867e6abbd6523418b24` |
| `proxy/gateway_identity.py` | `f3ed78d9f021d81b27e94366c5b7e577854ee3d551e769af22f003ad5ddbdfb9` | `f3ed78d9f021d81b27e94366c5b7e577854ee3d551e769af22f003ad5ddbdfb9` |
| `proxy/gateway_observation.py` | `36a59aacab8b5285ae37fc244dbb95cd821248fc918f41934b7a0c6240d00863` | `ba232d250431852dfbf4304ae80795b98b776cadab22089820e7f323a2474225` |
| `proxy/tests/test_gateway_observation.py` | `568e0cc8f2bcec6e05eae6a39d4ec90c80f43da37dab3cf5e9063cd2b2e85aa3` | `d7f38dbee52bd15a7f5626234b54e4fabb613232bf06726b5ba83c80f210d5e0` |

本地 pytest runner 使用临时 venv 的 Python：
`C:\Users\admin\AppData\Local\Temp\codex-fnmusic-preview-832a68c841fa4fe7b7118959588de624\Scripts\python.exe`。
实际 pytest argv 如下；三次调用均以 `-B` 禁止 pyc，并以 `-p no:cacheprovider` 禁止 pytest 缓存：

```text
首次复现：-B -m pytest -p no:cacheprovider -q proxy/tests/test_gateway_observation.py::test_zero_header_request_is_observed_without_parsing_or_leaking_body
修复后 observer：-B -m pytest -p no:cacheprovider -q proxy/tests/test_gateway_observation.py
修复后组合：-B -m pytest -p no:cacheprovider -q proxy/tests/test_gateway_observation.py proxy/tests/test_gateway_identity.py proxy/tests/test_musicbox_service.py proxy/tests/test_musicbox_playlist_preview.py
```

runner 由 `py -3 -` 启动 Python 子进程并捕获原始 stdout/stderr。子进程只继承 `PATH`、`SYSTEMROOT`、
`WINDIR`、`TEMP`、`TMP`，并把 `USERPROFILE`、`XDG_CONFIG_HOME`、`XDG_DATA_HOME`、
`XDG_CACHE_HOME`、`XDG_STATE_HOME`、`FNMUSIC_HOME`、`FNMUSIC_LOG_DIR` 指向仓库外的临时目录；
没有修改系统环境、`HOME` 或 `CODEX_HOME`，也没有输出完整环境变量。

| 阶段 | 结果 | 原始输出 |
| --- | --- | --- |
| 修复前定向复现 | exit 1；四个 HTTP/1.0、1.1 零头/带 body 用例均因 `INVALID_HEADER_INPUT` 失败，符合缺陷复现预期 | `C:\Users\admin\AppData\Local\Temp\fnmusic-ext-g0-observe-evidence-20261022\repair-20261023\zero-header-before-fix.raw.log` |
| 修复后 observer 文件 | exit 0；53 passed | `C:\Users\admin\AppData\Local\Temp\fnmusic-ext-g0-observe-evidence-20261022\repair-20261023\gateway-observation-final.raw.log` |
| 修复后组合文件集 | exit 0；230 passed，1 条 Starlette `httpx` deprecation warning | `C:\Users\admin\AppData\Local\Temp\fnmusic-ext-g0-observe-evidence-20261022\repair-20261023\g0-observation-combined-final.raw.log` |

组合文件集精确为上述四个 pytest 文件。observer 的文件 I/O、queue、请求及启动顺序均由 stub 验证；
没有创建真实 socket、执行真实 fork、连接网络、运行服务或访问 NAS。保护的歌单预览与身份文件在本批
保持原有哈希。早期未绑定本次快照的 `216 passed` 输出不作为本轮验收证据。

## 部署前门

以下每项都须在新的明确授权后由操作者重新确认。先使用只读命令，不运行恢复脚本，不修改服务。

1. 核实安装目录、网关路径和三个 Python 文件的当前 SHA-256、所有者、权限；记录
   `gateway_identity.py` 与 `gateway_observation.py` 原先是否存在。
2. 核实 PID 文件、gateway PID、启动时间、完整启动参数、监听 FD、socket 路径，以及
   `fnmusic-ext.service` 的 MainPID、ControlGroup、ActiveState。只使用核实出的 PID，不能套用历史值。
3. 比较 gateway 所属 cgroup 与 `fnmusic-ext.service` 的 ControlGroup，并确认 WebUI 主进程保持运行。
4. 证明新 gateway 能从该 service cgroup 启动并让 daemon 后代继承同一归属；部署前后均要核对。
   本手册没有 cgroup 迁移命令。若无法证明该关系，部署状态为 **BLOCKED**，停止本计划；不得以
   重启 `fnmusic-ext.service` 或整套扩展作为替代。
5. 确认 root 私有备份目录可用，并只读检查 `/run/fnmusic-ext/g0-observe`。代码要求该目录为
   root:root、精确 `0700`；如果目录存在但不合规，拒绝启动且不自动 chmod。
6. 确认一次性官方认证 GET 的操作方式、观察 marker、维护窗口和恢复责任人。marker 可公开，不能用
   token、Cookie 或其他凭据代替。

任一文件哈希、路径、权限、进程身份或 service 归属不匹配即停止。历史只读观察曾看到
`/run/fnmusic-ext` 为 root:root `0700`，但每次部署仍须现场复核，且不得据此推断子目录状态。
诊断启动读取 PID 后使用 `kill(pid, 0)` 仅作进程存在/权限探测，不发送终止信号；旧 gateway 的
停止需使用后文的已核验 pidfd 步骤。

## 只读预检命令

以下命令在设备端以 root shell 执行。先把 `APP_DIR` 设为核实后的实际目录；不要从本地仓库猜路径。

```sh
set -eu
APP_DIR='/核实后的扩展安装目录'
GW="$APP_DIR/proxy/webui_gateway.py"
IDENTITY="$APP_DIR/proxy/gateway_identity.py"
OBSERVER="$APP_DIR/proxy/gateway_observation.py"
SERVICE='fnmusic-ext.service'

systemctl show "$SERVICE" -p ActiveState -p MainPID -p ControlGroup
sha256sum "$GW"
stat -c '%n owner=%U:%G mode=%a size=%s' "$GW"
for f in "$IDENTITY" "$OBSERVER"; do
  if test -e "$f"; then sha256sum "$f"; stat -c '%n owner=%U:%G mode=%a size=%s' "$f";
  else printf 'absent: %s\n' "$f"; fi
done
cat /run/fnmusic-ext/webui-gateway.pid
GATEWAY_PID="$(cat /run/fnmusic-ext/webui-gateway.pid)"
ps -o pid,ppid,lstart,args -p "$GATEWAY_PID"
cat "/proc/$GATEWAY_PID/cgroup"
ls -l "/proc/$GATEWAY_PID/fd"
GATEWAY_START_TICKS="$(python3 - "$GATEWAY_PID" <<'PY'
import sys
raw = open(f'/proc/{int(sys.argv[1])}/stat', 'rt', encoding='ascii').read()
print(raw[raw.rfind(')') + 2:].split()[19])
PY
)"
stat -c '%n owner=%U:%G mode=%a' /run/fnmusic-ext /run/fnmusic-ext/g0-observe 2>/dev/null || true
```

从 `systemctl show` 与 `/proc/<pid>/cgroup` 人工核对进程关系。`stat` 对 g0-observe 返回
“不存在”时只说明预检时该目录不存在；不能跳过目录权限规则或预先创建它。此预检不读凭据，
不发 HTTP 请求，不改文件或服务。

## 暂存、替换和观测（授权后）

只有全部部署前门通过且获得本节操作类别的明确授权后，才执行以下步骤。

1. 在 root 私有位置创建本次备份，保存原字节、owner、mode、时间戳和清单。确认备份目录为
   root:root `0700`，记录每份备份的 SHA-256。示例：

   ```sh
   umask 077
   BACKUP="/root/fnmusic-g0-observe-$(date -u +%Y%m%dT%H%M%SZ)"
   test ! -e "$BACKUP"
   mkdir -m 0700 "$BACKUP"
   mkdir -m 0700 "$BACKUP/proxy"
   cp -a -- "$GW" "$BACKUP/proxy/webui_gateway.py"
   if test -e "$IDENTITY"; then cp -a -- "$IDENTITY" "$BACKUP/proxy/gateway_identity.py"; fi
   if test -e "$OBSERVER"; then cp -a -- "$OBSERVER" "$BACKUP/proxy/gateway_observation.py"; fi
   sha256sum "$BACKUP"/proxy/*
   stat -c '%n owner=%U:%G mode=%a' "$BACKUP" "$BACKUP"/proxy/*
   ```

2. 只从已审查、已核验 SHA-256 的三份文件暂存并替换目标文件。传送后先 `sha256sum` 对比上表，
   再按原网关的已记录 owner/mode 安装；两个模块使用与现有 proxy 源码一致的 owner/mode。
   若传送工具改变了字节，停止并重新暂存，不要用整个仓库或文本编辑器补救。
3. 整个部署、观察和恢复期间必须串行执行，禁止并发启动另一 gateway 或并发执行回滚。重新核对
   gateway PID、start time、命令行、监听 FD 与 service MainPID。保存刚读出的
   `GATEWAY_START_TICKS`，只对同一 PID 使用 pidfd 定向停止；PID 文件仍指向别的实例时不得继续。
   Python 与内核必须支持 pidfd；否则停止部署，不使用普通 `kill` 兜底。示例脚本会在 pidfd 打开
   后再次比对 `/proc/<pid>/stat` start ticks，然后才定向发送 SIGTERM：

   ```sh
   python3 - "$GATEWAY_PID" "$GATEWAY_START_TICKS" <<'PY'
   import os, signal, sys
   pid, expected = int(sys.argv[1]), sys.argv[2]
   def start_ticks():
       raw = open(f'/proc/{pid}/stat', 'rt', encoding='ascii').read()
       return raw[raw.rfind(')') + 2:].split()[19]
   if start_ticks() != expected:
       raise SystemExit('BLOCKED: gateway process identity changed')
   fd = os.pidfd_open(pid, 0)
   try:
       if start_ticks() != expected:
           raise SystemExit('BLOCKED: gateway process identity changed')
       signal.pidfd_send_signal(fd, signal.SIGTERM)
   finally:
       os.close(fd)
   PY
   ```

   停止后确认旧 listener 消失、WebUI MainPID 未变化。不要调用 `_stop_previous()` 的普通替换路径
   来处理诊断启动。
4. 再次验证已批准的 service cgroup 继承启动机制。只有在新 gateway 会留在
   `fnmusic-ext.service` ControlGroup 的方法已被单独证明并授权后，才可启动。若该方法不可用，
   此处停止为 **BLOCKED**，不重启主服务，不移动 cgroup，也不创建临时 unit。
5. 仅当一个已单独证明并获准的 root 启动 shell 已处于该 service cgroup 时，才使用以下检查和
   命令。它只确认当前 shell 的 cgroup，不移动进程；检查不相等就停止为 **BLOCKED**。保留原 base
   参数并附加诊断参数：

   ```text
   --g0-observe-marker <32位小写十六进制非秘密标签>
   --g0-observe-seconds 60
   ```

   ```sh
   SHELL_CGROUP="$(awk -F: '$1 == "0" {print $3}' /proc/$$/cgroup)"
   SERVICE_CGROUP="$(systemctl show fnmusic-ext.service -p ControlGroup --value)"
   test "$SHELL_CGROUP" = "$SERVICE_CGROUP" || { echo 'BLOCKED: cgroup not proven'; exit 1; }
   python3 -B "$GW" "$BASE" --g0-observe-marker "$MARKER" --g0-observe-seconds 60
   ```

   只允许精确三个已审查文件；`-B` 避免产生 pycache。验证唯一 listener、PID 文件、新 gateway
   start time、service cgroup、WebUI MainPID 和私有输出文件 owner/mode。诊断初始化失败应明确停止，
   不要降级成普通替换启动。
6. 在已认证的官方 UI 会话中只发出一次精确 GET。记录 HTTP 状态码，不保存 response body、Cookie
   或 Authorization。不能由 marker 推断发起者身份；其他本地进程也可能使用相同 marker。
7. 读取生成的 JSONL，确认只有固定 schema、脱敏摘要与固定错误码；按需仅核对记录中的 PID/UID/GID
   对应的进程元数据。不要打印或留存请求头、用户值、凭据或响应体。
8. 到期后网关仍继续转发，但观测已终止。按下方人工回滚恢复原网关。不要把合成测试或本次
   脱敏记录称作认证、G0 或运行期安全验收。

## 人工回滚（授权后）

先执行本节第 2 项的 stale PID 串行门，再恢复任何源码或启动原 gateway。诊断模式任何退出路径都不
自动 unlink PID 文件，因此会留下可核实的 stale PID 记录。

1. 核对诊断 gateway PID、start time 和 cgroup；仅用部署步骤的已验证 pidfd 脚本定向停止它。
   确认该 gateway listener 已消失且 WebUI MainPID 未变化。
2. 禁止并发启动/恢复 gateway。确认没有 successor listener，确认 PID 文件内容仍是本次诊断 PID，
   并与记录的 start ticks 对照，确认该 PID 当前不存在或已由其他 start ticks 进程复用。先把 stale
   PID 文件复制到备份目录并记录 SHA-256，再由操作者删除该 stale 记录：

   ```sh
   test "$(cat /run/fnmusic-ext/webui-gateway.pid)" = "$DIAGNOSTIC_PID"
   if test -r "/proc/$DIAGNOSTIC_PID/stat"; then
     CURRENT_TICKS="$(python3 - "$DIAGNOSTIC_PID" <<'PY'
   import sys
   raw = open(f'/proc/{int(sys.argv[1])}/stat', 'rt', encoding='ascii').read()
   print(raw[raw.rfind(')') + 2:].split()[19])
   PY
   )"
     test "$CURRENT_TICKS" != "$DIAGNOSTIC_START_TICKS"
   fi
   if ss -xlpn | grep -F "$SOCK_PATH"; then
     echo 'BLOCKED: gateway listener still exists'
     exit 1
   fi
   cp -a -- /run/fnmusic-ext/webui-gateway.pid "$BACKUP/diagnostic-stale-pid"
   sha256sum "$BACKUP/diagnostic-stale-pid"
   test "$(cat /run/fnmusic-ext/webui-gateway.pid)" = "$DIAGNOSTIC_PID"
   rm -- /run/fnmusic-ext/webui-gateway.pid
   ```

   记录文件哈希和人工删除结果。若进程仍使用原 start ticks、PID 内容变化、其他 listener 存在或串行门
   失效即停止；不允许同时启动/恢复。此人工门不是对恶意同权限并发写者的原子保证，且未在
   Linux/NAS 实测。
3. 从本次 root 私有备份恢复原 `webui_gateway.py` 的完整字节、owner、mode 和时间戳。恢复后
   `sha256sum` 必须等于备份清单中的哈希。
4. 对 `gateway_identity.py` 和 `gateway_observation.py`：若预检记录其原本存在，恢复备份原件；
   若记录为原本不存在，只能在当前文件哈希等于本次部署清单时删除新增文件。其他情况停止。
5. 只有已单独证明的 root 启动 shell 本身处于原 service cgroup 时，才按部署步骤中的 cgroup 相等
   检查，以原 base 参数和 `python3 -B` 启动原 gateway。检查失败即 **BLOCKED**，不移动 cgroup。
6. 验证 PID 文件、新 PID、唯一 listener、gateway cgroup、原网关哈希及 WebUI MainPID。
   通过已认证官方入口确认一次 GET 的 HTTP 状态码，不保存响应体或凭据；确认原运行模式未启用诊断。
7. 诊断 JSONL、备份和 `/run/fnmusic-ext/g0-observe` 是否保留或清理，按单独授权执行。清理前核对
   marker 文件仍是本次文件且未被替换；不触碰媒体或其他服务文件。

不运行 `restore.sh`；不重启 `fnmusic-ext.service`、nginx、CGI、容器或官方音乐服务；不改 service
模板、cgroup、nginx、系统权限或媒体文件。部署、回滚和真实设备证据都需另行记录和审查。
