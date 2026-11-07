# 已部署代码异常与无 DB 行为分析 · 2026-11-07

> **状态：只读分析报告。本文件不改变 §0.1 / §0.2 的 BLOCKED 状态，也不构成任何部署、停机、安装、回滚或写操作的授权。**

## 0. 抬头与声明

| 项目 | 内容 |
| --- | --- |
| 采集/分析时间（JST） | 2026-11-07 11:12–11:19 JST（= 10:12–10:19 CST，设备时区 Asia/Shanghai） |
| 分析者 | `device-inventory`（DSH Agent Team 独立执行者；未参与实现/部署） |
| 对象设备 | fnOS NAS `kotomi`（Linux 6.18.18.c1168-trim x86_64，fnOS/TRIM `1.2.0903`），与首份盘点同一运行实例（服务 2026-11-01 17:18 启动、`NRestarts=0`） |
| 窗口绑定 | 全部设备证据在本窗口实时采集；静态分析对象为**当前安装树** `/vol1/@appcenter/fnmusic-ext/repo`（VERSION `2.10.2`）与 FPK `/vol1/1000/fnmusic-guard-accept-20261101/packages/fnmusic-ext-2.10.2.fpk`。 |
| 只读声明 | 未停启服务、未改配置、未安装/回滚、未写 `/vol1`、未 `docker exec`、未打开任何生产 DB、未执行保存请求。仅使用：文件读取、`stat/find/sha256sum/md5sum/tar -t/-xO/ss/ps/systemctl show/journalctl`、`docker inspect/history/diff/top`、只读 GET、**纯函数只读模拟**（`python3 -B`，见 §2.2）。 |
| 临时副本（已清理） | `/tmp/fnmusic-inv-anomalies-20261107`（FPK `app.tgz` 解包，用于全量 diff）、`/tmp/fnmusic-inv-rollback-20261107`、`/tmp/fnmusic-inv-rollback2-20261107`、`/tmp/fnmusic-inv-rollback3-20261107`（2.8.2 双包解包对比），均在使用后 `rm -rf` 并验证 `cleanup_ok=YES`。设备侧未留任何本报告产生的文件。 |
| 原始输出 | `C:\Users\admin\AppData\Local\Temp\fnmusic-inv-anomalies\out-m.txt`、`out-m2.txt`、`out-n.txt`、`out-n2.txt`、`out-n3.txt`、`out-o.txt`、`out-o2.txt`、`out-o3.txt`、`out-p.txt`（未提交仓库，可复核）。 |

---

## 1. ① `app.js` 与 FPK 不一致

### 1.1 身份与差异全文

| 对象 | SHA-256 | 大小 | mtime |
| --- | --- | --- | --- |
| FPK 2.10.2 内 `repo/webui-service/static/app.js` | `e12e6a5e24d9389d9b40a7f76b761bdbe0264cf32fcfcb331c490567e24aad7b` | 99,360 | 包内（安装时 2026-11-01 15:24:54） |
| 安装树 `repo/webui-service/static/app.js` | `22cde4ea84ff5477cb2549cdb33ce527d57d2fe7e18af5554f74eb22b4a861bf` | 99,379 | 2026-11-01 17:05:33.831788047 +0800 |
| 安装树备份 `repo/webui-service/static/app.js.bak-manual-save-170533` | `e12e6a5e…`（= **FPK 原版**） | 99,360 | 15:24:54（`cp -p` 保留了包内 mtime） |
| 容器可写层 `/vol1/docker/overlay2/74274def…/diff/srv/webui-service/static/app.js` | `22cde4ea…`（= 安装树补丁版） | 99,379 | 17:05:33（与安装树同秒） |
| 容器镜像层 `/vol1/docker/overlay2/m9sm5cw3…/diff/srv/webui-service/static/app.js` | `e12e6a5e…`（= **FPK 原版**） | 99,360 | 镜像构建期 |

**完整差异（全文件唯一一处，`diff -u` 全文）：**

```diff
--- <FPK app.js>
+++ <安装树 app.js>
@@ -1950,7 +1950,7 @@
 async function manualSaveApi(path, options = {}) {
   const headers = Object.assign({}, options.headers || {}, { "x-fnmusic-save": "1" });
-  const resp = await fetch(APP_BASE + path, Object.assign({}, options, { headers }));
+  const resp = await fetch(APP_BASE + path, Object.assign({ cache: "no-store" }, options, { headers }));
   let body = {};
```

即：只在 `manualSaveApi()`（手动保存/搜索/状态专用请求封装）的 `fetch` 选项里增加了 `cache: "no-store"`；其余代码逐字节相同。

### 1.2 影响面

| 事实 | 判断 |
| --- | --- |
| 被改函数 `manualSaveApi(path, options)` 仅用于手动保存相关 API（`x-fnmusic-save: 1` 头），不用于普通 `api()` 请求（app.js 中另有 19+ 处 `api()` 调用未变） | 影响面仅限手动保存接口的**浏览器缓存语义** |
| `fetch(..., {cache:"no-store"})` 的含义：该请求/响应不读不写浏览器 HTTP 缓存 | 手动保存的状态/搜索/保存响应不再被浏览器缓存；**不改变鉴权头、不改变 CSRF 头、不改变请求方法/路径** |
| 服务端已有 `NoCacheStaticMiddleware`：对 `/static/` 路径与 HTML 响应追加 `Cache-Control: no-cache`（`webui-service/app.py:2030-2056`） | 静态资源本身已有 no-cache 协商；该补丁针对的是**手动保存 API 响应**，二者互补，不是安全边界 |
| 实测响应头（8774）：`cache-control: no-cache`、`content-length: 99379`、`last-modified: Sun, 01 Nov 2026 09:05:33 GMT` | 服务端对静态资源返回 no-cache（来自中间件） |
| 结论 | **不是安全相关改动**：无认证/授权/密钥/权限代码变化；无新增网络目标；仅缓存行为（使手动保存 API 响应总是回源） |

### 1.3 容器进程实际加载哪一份 / 两副本是否一致

| 事实 | 证据 | 判定 |
| --- | --- | --- |
| 容器 WebUI 进程 | `docker top`：`/usr/local/bin/python3.13 uvicorn app:app --host 0.0.0.0 --port 8004`，宿主可见 pid `1809118`；`/proc/1809118/cwd → /srv/webui-service` | observed |
| 静态目录代码 | `webui-service/app.py:73` `STATIC_DIR = Path(__file__).resolve().parent / "static"`；`:1975` `app.mount("/static", StaticFiles(directory=str(STATIC_DIR)))` | observed（即 `/srv/webui-service/static`） |
| 容器进程视角文件的字节 | `/proc/1809118/root/srv/webui-service/static/app.js` sha256 = `22cde4ea…`（99379B，overlay 合并视图 = 上层补丁版） | **observed：容器加载的是补丁版** |
| 容器内 `/repo` 视角 | `/proc/1809118/root/repo/webui-service/static/app.js` sha256 亦 = `22cde4ea…`（宿主 repo 的 bind mount，同字节） | observed |
| 两副本一致性 | 安装树 repo 文件 = `22cde4ea…`；容器 upperdir `/srv` 文件 = `22cde4ea…`；**逐字节一致** | **observed** |
| 端到端实际下发字节 | `GET http://127.0.0.1:8774/app/fnmusic-ext/static/app.js` → sha256 `22cde4ea…`，`size=99379`；经 gateway socket 同路径 GET → 同 sha256 | **observed：浏览器实际拿到补丁版** |
| 服务链路 | `ui/config` 声明 `gatewaySocket: fnmusic-ext.sock`，browser → 飞牛桌面网关 → `webui_gateway.py`（`UPSTREAM=("127.0.0.1",8774)`，`proxy/webui_gateway.py:54`）→ 容器 8004 | observed |

### 1.4 全量 diff（已安装树 vs FPK 2.10.2）

| 类别 | 数量 | 结论 |
| --- | --- | --- |
| FPK `app.tgz` 普通文件 | 114 | 包内 = `repo/`、`ui/`、`config/`；**不含 `.env`/`.db`/sqlite/probe 文件** |
| 逐字节一致 | **113 / 114** | — |
| 内容不一致 | **1** | `repo/webui-service/static/app.js`（见 §1.1） |
| 安装树缺失 | 0 | — |
| 包内文件 mtime 被改（内容仍一致） | 1 | `repo/proxy/run_proxy.sh`：内容 sha256 = 包内版本，但 mtime `2026-11-01 16:44:44`（安装时间之后）——仅重写/触碰，**无内容变化** |
| 安装树独有文件 | 2530 | `.venv-proxy/` 2499（安装脚本生成 venv）、`sources-data/` 11（数据卷：登录态/缓存）、`proxy/__pycache__/` 8（运行时字节码）、`recommend_cache/` 2、`play_history/` 1、`online_favorites/` 1、`llm_usage.json`、`webui-service/static/app.js.bak-manual-save-170533`、`.env` + 5 个 `.env.*` 备份 |

**安装树中“不在 FPK 内、且非运行期数据”的文件（穷举）：**

| 文件 | 属性 | 性质 |
| --- | --- | --- |
| `webui-service/static/app.js.bak-manual-save-170533` | 644 root:root，99360B，mtime 15:24:54（`cp -p` 保留的包内时间），sha256 = FPK 原版 | 打补丁前的备份 |
| `.env` | 600 `kurumi:Administrators`，2663B，mtime 17:01:47 | 运行配置（FPK 按设计不含 `.env`） |
| `.env.bak.20261011160606` | 600 root:root，1102B | 历史配置备份 |
| `.env.bak.20261101152455` | **000** `kurumi:Administrators`，1579B | 历史配置备份（不可读，历史遗留） |
| `.env.bak.20261101155312` | 600 `kurumi:Administrators`，1579B | 历史配置备份 |
| `.env.pre-manual-save.20261101162531` | 600 `kurumi:Administrators`，2076B | 历史配置备份 |
| `.env.webui.bak` | 600 `kurumi:Administrators`，2331B | WebUI 写 `.env` 前的备份 |
| `proxy/__pycache__/*.pyc` ×8 | 600/644，root/kurumi | 运行时字节码（非源码） |

### 1.5 安全扫描（只读）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| setuid/setgid 文件 | `find /vol1/@appcenter/fnmusic-ext -xdev -type f \( -perm -4000 -o -perm -2000 \)` | **无** |
| 全局可写文件 | `find <repo> -xdev -type f -perm -o+w`（排除运行期目录） | **无**（全 repo 计数 0） |
| 敏感配置权限 | `stat` | `.env`/`.env.*` 均为 600（一个历史备份为 000）；无 world-readable `.env` |
| 其它源码/脚本改动 | 见 §1.4 | **除 app.js 外无内容改动**；`run_proxy.sh` 仅 mtime |
| 容器可写层代码改动 | `docker diff fnmusic-sources` | 代码类只有 `C /srv/webui-service/static/app.js`；其余为运行期目录（`/tmp/musicdl_outputs`、`/home/appuser/.local`、`/tmp/supervisor.sock` 等） |

### 1.6 可能来源（证据链）

| 证据 | 含义 |
| --- | --- |
| FPK 不含该行；`app.js.bak-manual-save-170533` 的 mtime 被 `cp -p` 保留为包内 15:24:54 | 有人先备份原文件，再编辑 |
| 安装树与容器 `/srv` 两副本 mtime 同为 `17:05:33`，字节一致 | 同一操作同时（或先后）写入 repo 与运行中容器（`docker cp` 会保留源 mtime）；不是两次独立编辑 |
| `install.sh` 对 `app.js` 只有 `chmod 0644`（安装树 `install.sh:1171-1172`），**没有任何内容补丁**；`extend.sh/install.sh` 中不存在该 `no-store` 片段 | 不是安装/升级脚本产生的 |
| 全设备（repo、`.dsh-root`、`/tmp/build`、`/tmp/*.sh`）检索 `no-store`/`app.js.bak-manual-save` | 未发现打补丁脚本或引用；`no-store` 仅出现在 app.js 自身与无关的 proxy cover/media 代码 |
| 时间线 | `.env.webui.bak` 17:01:47 → app.js 补丁 17:05:33 → 服务 17:18:28 启动。三者同处 2026-11-01 16:39–17:18 的上一轮部署/验收窗口；`.dsh-root/` 留有该窗口的 `run_proxy.sh.new`/`orig` 实验产物 |
| 结论 | **最可能是上一轮部署窗口内的手工补丁**（先 `cp -p` 备份、编辑 repo、再复制进运行中容器的 `/srv`），**不是** FPK/安装脚本/并行升级器产生；具体执行者与意图在设备上**未留存可核实记录（unknown）** |

### 1.7 ① 结论与建议

- **结论：** 已部署 `app.js` = FPK 2.10.2 原版 + 1 行缓存补丁（`cache:"no-store"`）；两副本（repo `/srv`）逐字节一致；容器进程与端到端 HTTP 实际下发的都是补丁版；补丁只影响手动保存 API 的浏览器缓存语义，**安全中立**。
- **影响/风险：** 这是**未受 FPK 管理的本地漂移**：升级/回滚/重装会以 FPK 原版覆盖它（升级流程替换 target/repo 后仅恢复白名单，`.env`/`data` 等，不含 app.js），届时 `manualSaveApi` 的手动保存响应可能重新被浏览器缓存（是否造成实际问题取决于此前修复的动机，动机 unknown）。
- **建议：** ① 把该 1 行改动正式纳入 dev 仓库并进入下一版 FPK（或书面记录为“接受的本地偏差”并附哈希）；② 升级/回滚前记录安装树 app.js 哈希，升级后核对是否被还原；③ 不要依赖未打包的本地文件作为生产行为的一部分。

---

## 2. ② 无 jobs DB + `ENABLED=true` 的实际行为

**分析时刻：2026-11-07 11:14–11:19 JST。** 对象：已安装 **2.10.2** 代码（未安装 P1/P2/P4/P5/P6 整歌单代码）。

### 2.1 启动行为：不依赖 jobs DB，不会因缺 DB 报错/降级

| 事实 | 证据 | 判定 |
| --- | --- | --- |
| 网关启动路径 | `webui_gateway.py:1465+ main()`：只校验 `FNMUSIC_GATEWAY_PEER_ALLOW`/`FNMUSIC_GATEWAY_SOCKET_MODE`（`_policy_or_exit()`），然后 daemonize + 绑定 socket + serve；**无任何 DB/jobs 配置读取或文件打开** | observed（代码） |
| 模块级副作用 | `manual_save_route.py` 导入 = 仅 stdlib + `manual_save_core`（`manual_save_route.py:24-30`）；模块级只有常量与函数定义 | observed（代码） |
| DB 引用计数 | `grep -cE 'sqlite\|jobs\|ledger\|JOBS_DB'`：`manual_save_route.py` = **0**；`webui_gateway.py` = **0**；`app.py` 的 25 处是 trim 音乐库只读查询（与本功能无关） | **observed：当前单曲保存链路完全不涉及 DB** |
| 未实例化的账本类 | `manual_save_jobs.py:361` 定义 `ManualSaveJobsLedger`，`__all__` 导出；全安装树**无实例化**（grep 仅类定义与导出） | observed |
| 运行状态 | `systemctl show … NRestarts=0`；`ActiveState=active`，`ExecMainStartTimestamp=Sun 2026-11-01 17:18:28 CST`，进程已运行 5 天 16 小时 | observed（无启动失败/重启） |
| 说明 | 设备 journal 为 `/run` 易失日志且只保留当前 boot（服务启动于更早 boot），因此无法回读启动 stderr；上述结论来自**代码路径 + 进程未重启事实**，不依赖日志 | 边界见 §5 |

### 2.2 `GET /api/manual-save/status` 为何返回 `enabled:true`（只读纯函数模拟）

**方法：** 在设备上以 `PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B` 从安装树 `proxy/` 导入 `manual_save_route`（只调用纯函数 `load_config()` / `_validate_config()` / `status()`，不发起保存、不写文件；`-B` 禁止生成 `.pyc`，事后 `__pycache__` 目录 mtime 仍为 2026-11-01 17:36:59，未新建文件）。

| 调用 | 结果 |
| --- | --- |
| `load_config()` | `enabled=True`；`target_dir=/vol1/1000/fnmusic-test-20261031`；`staging_dir=/vol1/1000/fnmusic-manual-save-staging`；`musicbox_url=http://127.0.0.1:8770` |
| `_validate_config(config, None)` | `ok`（两目录存在、同 `st_dev`、不同 inode、staging 属主 = euid(root)、mode 0700） |
| `status()` | `ok=True`、`code="ok"`、`data.enabled=True`（target/staging 同上） |
| status 是否含 DB 字段 | **否**（`status_has_db_field=False`） |

**代码链（已安装树行号）：**

1. `manual_save_route.py:89` `ENABLED_KEY = "FNMUSIC_MANUAL_SAVE_ENABLED"`；
2. `:287-288` `_env_path() = Path(__file__).resolve().parent.parent / ".env"` → **repo `.env`**；
3. `:291-312` `read_env_file()`（只读解析，不创建/不重写）；
4. `:314-317` `_parse_enabled()`：仅 `true/1/yes/on` 为真；
5. `:320-336` `load_config()`：`enabled` **只来自** `FNMUSIC_MANUAL_SAVE_ENABLED`；
6. `:420-441` `status()`：`data["enabled"]=bool(config.enabled)`；**不做目录校验、不读 DB、不检查 playlist/readonly 键**；开关为真即 `ok=True`。

→ **`enabled:true` 的含义只是“单曲保存总开关被 `.env` 打开”**；它与 `FNMUSIC_MANUAL_SAVE_JOBS_DB` 缺失无关（该键属于未安装的整歌单 P5 代码），也不是“DB 可用”的信号。

### 2.3 单曲保存请求的代码路径、失败点与写序

**请求入口（宿主 gateway，socket 600）：** `webui_gateway.py:814` `handle_manual_save_track()` → 严格解析 + L4 授权（`x-trim-isadmin` + `x-fnmusic-save: 1` + user-id）→ `_engine_status()`（`:625` 调用 `manual_save_route.status()`，即上面的开关检查）→ `_run_manual_save()`（`:749`，线程名 `fnmusic-manual-save`，超时 `MANUAL_SAVE_TIMEOUT=1200s`）→ `engine.save_track(song_id)`。

**失败点按顺序（已安装 `manual_save_route.py`）：**

| 阶段 | 代码 | 失败码 | 是否已产生文件副作用 |
| --- | --- | --- | --- |
| 1. 授权 | `webui_gateway.py:814-899` | 400/403（如 `forbidden`/`disabled`） | 无 |
| 2. 开关 | `save_track` `:550-566` → `load_config(); if not enabled: disabled` | `disabled`（403） | 无；**当前开关=true → 通过** |
| 3. 目录校验 | `:568` `_validate_config()`（`:381-419`：POSIX 绝对路径、目录、同 `st_dev`、不同 inode、staging 属主=euid、mode 0700） | `config_invalid`（500） | 无；**模拟结果 ok → 通过** |
| 4. 锁 | `:570` `_SAVE_LOCK.acquire(blocking=False)` | `busy`（409） | 无 |
| 5. 元数据/音质 | `_save_locked` `:574-597`：`_fetch_song_info()` → `_select_quality()`（HTTP 到 `127.0.0.1:8770`） | `service_unavailable`（503）/`download_failed` 等 | **无**（工作目录尚未创建） |
| 6. 工作目录 | `:598-604` `_default_make_tempdir()`（`:1640-1651`，`/tmp/fnmusic-manual-save-*`） | `download_failed` | 仅 `/tmp`，`finally` 中 `shutil.rmtree(workdir, ignore_errors=True)` 清理 |
| 7. 下载/校验/解码/封面/歌词/tag | `:604-760` 全程只在工作目录 + `/tmp` | `integrity_mismatch`/`invalid_audio`/`tag_failed`/`publish_failed`(前段) | 仅 `/tmp`（清理） |
| 8. 打开存储 | `:761-766` `open_store(target, staging)` → `_default_open_store`（`:2349-2370`，以只读 `O_DIRECTORY` 打开两目录 fd） | `staging_unavailable`（503） | 无文件 |
| 9. 匿名暂存 | `:767-780` `begin_owned_stage()` → **O_TMPFILE**（`manual_save_anonymous.py:275-312`）；`append`/`seal`（`:314-346`） | `staging_unavailable` | **无目录项**（O_TMPFILE 匿名 inode，`st_nlink=0`；失败时 `close()` 关 fd 即消失，不残留 staging 文件） |
| 10. 发布 | `:781-792` `publish_noreplace()` → `linkat(fd,"",dest_fd,name,AT_EMPTY_PATH)`（`manual_save_anonymous.py:375-407`，no-replace） | `conflict`(409)/`publish_failed`(500)/冻结 | **成功即向 target 曲库落一个文件（真实副作用）**；`EEXIST` → conflict，不覆盖 |
| 11. 歌词 sidecar | `:794-799` `_publish_sidecar()`（`manual_save_route.py:1341-1367`） | 仅影响 `lyric_status` | 媒体已发布；歌词发布失败不回滚媒体（部分发布） |
| 12. finally | `:806-815` `store.close()` + 删除工作目录 | — | 若发布结果不确定 → `PublicationUncertain`：store 冻结、fd 保留到进程退出，**不重试不删除**（可能已存在 target 文件名，也可能没有；无 DB 可供对账） |

**关键结论：2.10.2 的单曲保存链路没有“打开 jobs DB”这一步**；不存在“因 `FNMUSIC_MANUAL_SAVE_JOBS_DB` 缺失而在读配置/打开 DB 阶段失败”的路径——该键属于未安装的整歌单（P5）代码。

### 2.4 无 DB 时是否可能写出文件：**会**

| 证据 | 说明 |
| --- | --- |
| 代码（§2.3 8–11） | 单曲保存不需要 DB；`open_store` 直接打开 target/staging 目录 fd，O_TMPFILE 暂存后 `linkat` 发布到 **target 曲库** |
| 设备历史痕迹 | `/vol1/1000/fnmusic-test-20261031` 现存 4 个由单曲保存产生的文件（3 flac + 1 lrc，mtime 2026-10-31 15:30 / 17:10 / 2026-11-01 02:14），而设备从未有 jobs DB（首份+续盘均证实）→ **无 DB 的保存已成功写出过文件** |
| 当前设备“已武装” | 总开关 `true`（§2.2）、target/staging 校验 `ok`、`/usr/bin/ffmpeg` 与 `/usr/bin/ffprobe` 存在、musicbox `127.0.0.1:8770` 端口在监听（HTTP 404 说明进程在）→ 任何具备 L4 授权的保存请求都会真实执行到写文件阶段 |
| 部分副作用边界 | 失败在 §2.3 阶段 1–7：只产生 `/tmp` 临时文件并自动清理；阶段 9 的 O_TMPFILE 不产生目录项；**唯一真正的持久副作用是阶段 10 的 target 文件**（成功或“发布结果不确定”时可能已存在），以及阶段 11 的 `.lrc`（媒体已发布后可能单独失败）；**staging 目录不会留下文件**（匿名 inode，非命名文件） |

> 注：本分析**未发送任何保存请求**（写操作），以上均为代码路径 + 只读模拟 + 现存文件痕迹推断。

### 2.5 与“目标版本”（未安装的 P5 整歌单）对比（非本设备证据）

本地 dev 仓库 HEAD 的 P5 代码在 `_manual_save_playlist_resolve_deps()` 中先查 playlist 开关（403 `playlist_disabled`），再在 `config.db_path is None` 时返回 500 `config_invalid`——即**若将来部署 P5 而 `FNMUSIC_MANUAL_SAVE_JOBS_DB` 仍缺失，整歌单端点在创建任何任务/写文件之前就会失败**。这属于目标分支代码（未安装、未执行），仅用于说明“缺 DB 的后果因版本而异”：2.10.2 单曲保存不受影响，P5 整歌单则会 fail-closed。

### 2.6 ② 结论与建议

- **结论：** 无 jobs DB 不会导致 2.10.2 启动失败或降级；`status.enabled=true` 只是单曲总开关为真；单曲保存链路完全不使用 DB，**在当前设备状态下可以成功写出文件到 target 曲库**（且历史上已发生）；失败不会在 staging 留下命名文件，持久副作用仅在 target 发布阶段（含极少见的“发布结果不确定”）。
- **风险：** 总开关 `true` 意味着生产写路径处于“可执行”状态；这是 §0.2/§1.1-6 的既有阻断项。另：单曲保存无账本/幂等记录（2.10.2 设计如此），发布结果不确定时无对账依据。
- **建议：** ① 维持 BLOCKED，不调用保存接口；② 若后续启用 P7，需在已验证生命周期下关闭总开关并备份曲库；③ 若部署 P5，必须先把 `JOBS_DB` 指到 repo/target 之外的私有持久路径，并重新盘点。

---

## 3. ③ 回滚目标评估：2.8.2-rollback 重建版 vs 历史 2.8.2

### 3.1 包身份与自洽性（本窗口复核）

| 包 | 大小 | SHA-256 | manifest version | manifest checksum = md5(app.tgz) |
| --- | --- | --- | --- | --- |
| `fnmusic-ext-2.8.2-rollback.fpk`（重建） | 27,170,364 | `1379e2e9214c1ec887e14d0bcfc2befe92453d753643eb9a9531fca7822fdd85` | `2.8.2` | `d0c9a61065959c2e6668fffda5a36dc5`（一致） |
| `fnmusic-ext-2.8.2.fpk`（历史下载，2026-10-11） | 27,173,490 | `a6aa066217e4f5929960acf438e3b49a4af30649c44c4bf4471848754f9df670` | `2.8.2` | `33d3bf021642aaa6d47685681e65b1a0`（一致） |

### 3.2 两包差异分类（60 个内容差异 + 3 个 only-in）

| 类别 | 代表文件 | 说明 |
| --- | --- | --- |
| 版本文件 | `repo/VERSION` | rollback `32 2e 38 2e 32 0d 0a`（`2.8.2\r\n`）vs 历史 `2.8.2\n`；`/tmp/build/282/VERSION` 同为 CRLF |
| Python 源码（30 个） | `proxy/app.py`、`proxy/takeover.py`、`proxy/webui_gateway.py`、`musicbox-service/app.py`、`webui-service/app.py`、`lxmusic-service/app.py`、`musicdl-service/*`、`scripts/*.py`、`tools/*.py` 等 | 内容均不同（非换行差异） |
| 生命周期 shell 脚本 | `install.sh`、`extend.sh`、`restore.sh` | **两包完全一致**（同 sha256）；差异不在脚本层 |
| 服务/编排/配置 | `fnmusic-ext.service`、`docker-compose.yml`、`.env.example`、`.dockerignore`×4、`config/privilege`、`config/resource`、`ui/config` | 不同；其中 rollback 的 `fnmusic-ext.service` **与当前 HEAD 模板一致**（`1e71db37…`），历史包为 `92d7fcb0…` |
| 依赖清单（5 个 requirements.txt） | proxy/lxmusic/webui/musicdl/musicbox | 文本不同（内容近似，如 head 的 musicbox 多 `httpx`） |
| 文档（7 个 md） | `CHANGELOG.md`、`README.md`、`SECURITY.md`、`PLATFORMS.md` 等 | 不同；rollback CHANGELOG 首节仍为 `[2.8.2] - 2026-10-11`，但正文与历史/HEAD 均不同 |
| 前端静态 | `webui-service/static/{app.js,index.html,style.css,vendor/README.md}`、`test_app_js.js` | 不同 |
| 二进制/制品 | 无 | `container/lxserver-artifact/*` 等二进制/压缩制品两包一致 |
| only-in（rollback 有、历史无） | `repo/container/ensure_lxserver_native.sh` | 新增脚本（构建/运行辅助） |
| only-in（历史有、rollback 无） | `repo/scripts/update_lxserver.sh`、`repo/webui-service/static/alipay.png` | 重建时被移除 |

### 3.3 来源判定：rollback 是“混合重建树”，不是历史 2.8.2 的等价重建

| 对比 | 结果 |
| --- | --- |
| rollback payload vs `/tmp/build/282`（同一设备上的构建树） | **88 个文件逐字节一致、0 不同、6 个未打包**（打包白名单排除）→ **rollback 由 `/tmp/build/282` 构建** |
| `/tmp/build/282` vs `/tmp/build/head`（2026-11-01 的 HEAD 构建树） | 175 条差异 → 282 不是 HEAD 树 |
| rollback vs 历史 2.8.2（60 个差异文件）分类 | **23 个文件与当前 HEAD 版本逐字节一致**（如 `fnmusic-ext.service`、`proxy/version.py`、`env_merge.py`、`fnlog.py`、`nmplaylists.py`、`SECURITY.md`、多个 `.dockerignore`/requirements、`test_app_js.py` 等）；**34 个文件与历史 2.8.2 和 HEAD 都不同**；3 个为包根 app-center 元数据（`config/privilege`、`config/resource`、`ui/config`，不在 repo HEAD 树中） |
| 结论 | `/tmp/build/282` 是**来源未识别**的中间/混合树：生命周期 shell 脚本沿用历史 2.8.2，部分文件来自 2.10-era（HEAD），其余 34 个文件既非历史也非 HEAD。rollback FPK 仅把 `VERSION` 改为 `2.8.2\r\n` 后打包 → **不能视为历史 2.8.2 的等价重建** |

### 3.4 `VERSION` CRLF 的影响（静态核对）

`install.sh:33` 为 `FNMUSIC_VERSION="$(head -n 1 "${BASE_DIR}/VERSION" 2>/dev/null | tr -d '[:space:]' || true)"` → `\r` 属于 `[:space:]`，会被去掉，**版本解析为 `2.8.2`，不会因 CRLF 变成 `2.8.2\r`**。因此 CRLF 本身不是功能阻断点，但它是“非原生构建/交叉平台编辑”的强指纹；其它直接读取 `VERSION` 原始字节的消费者未逐一核对（unknown 边界）。

### 3.5 ③ 结论

- **“受支持回滚目标”仍是 unknown。** 本次只读证据进一步确认：`fnmusic-ext-2.8.2-rollback.fpk` 是从来源未识别的混合树重建的包（含 23 个 HEAD-era 文件 + 34 个两代皆非的文件 + only-in 文件差异），与用户历史安装过的 2.8.2 包不等价；而历史 2.8.2 包（`a6aa0662…`）又与升级前整树备份匹配（`webui_gateway.py` 哈希 `b87e39fd…`），说明**历史实际版本是下载包**，不是 rollback 包。
- 设备上的只读证据**不能**证明 fnOS 应用中心支持降级、也不能证明任一 2.8.2 包在当前 2.10.2 数据/配置上可安全回滚；按检查单 §6，不得用“安装旧 FPK 试探”。
- **建议：** ① 明确 rollback 包来源（谁在 2026-11-01 用 `/tmp/build/282` 构建、基于哪个 commit/工作树）；② 在隔离副本上验证目标回滚包与当前 `.env`/数据兼容性；③ 在来源和验证完成前继续保持 §6 回滚门 BLOCKED。

---

## 4. 剩余 unknown

| # | 项 | 原因 |
| --- | --- | --- |
| 1 | app.js 补丁的执行者/意图 | 设备上无打补丁脚本、无日志、无历史记录；只有 mtime 与备份命名线索 |
| 2 | `run_proxy.sh` 为何在 16:44:44 被重写（内容未变） | 上一窗口实验痕迹（`.dsh-root/run_proxy.sh.new`），无操作日志 |
| 3 | `/tmp/build/282` 的源 commit/工作树 | 该目录无 `.git`、无构建元数据；只能确定它介于历史 2.8.2 与 HEAD 之间 |
| 4 | fnOS 应用中心是否支持降级 / 哪个 2.8.2 包可作为回滚目标 | 未执行任何安装/降级测试（只读边界） |
| 5 | 2.10.2 启动瞬间 stderr 是否有告警 | journal 为易失且只保留当前 boot；代码上无 DB 读取，但无法回读启动日志 |
| 6 | “发布结果不确定”情况下 target 是否会出现文件 | 只在真实发布异常时发生；未执行保存请求，无法观测 |
| 7 | app.js 补丁丢失后是否会造成实际 UI 缓存问题 | 取决于此前修复动机（unknown） |

---

## 5. 本次不证明

1. 不证明 §0.1/§0.2 任何一项 PASS；不改变 BLOCKED。
2. 不证明单曲保存“现在执行一定成功”（只证明无 DB 不阻断、写路径存在且历史成功过）；未执行保存请求。
3. 不证明发布结果不确定路径的实际表现（无 DB、无重试、无对账）。
4. 不证明任何回滚/降级可安全执行；未安装/未回滚/未执行包脚本。
5. 不证明 app.js 补丁应保留或删除；也不证明其来源方身份。
6. 不证明 2.10.2 启动日志内容（journal 易失）。
7. 本报告不授权任何后续写操作。

---

## 附录 A：关键命令（摘要）

```text
# ① app.js
tar -xOzf <FPK> app.tgz | tar -xOzf - repo/webui-service/static/app.js | sha256sum
diff -u <(FPK app.js 流) <安装树 app.js>
sha256sum 安装树/容器upperdir/镜像layer/proc-root 的 app.js
sed -n '1935,1975p' 安装树 app.js；grep -n 'static|StaticFiles' webui-service/app.py
/proc/1809118/cwd、/proc/1809118/root/srv/webui-service/static/app.js
curl -sS http://127.0.0.1:8774/app/fnmusic-ext/static/app.js | sha256sum（只读 GET）
# 全量 diff：解 114 文件到 /tmp 后逐文件 sha256sum vs 安装树；diff -rq；find -perm；cleanup
# ② 无 DB 行为
grep -cE 'sqlite|jobs|ledger|JOBS_DB' manual_save_route.py webui_gateway.py
sed -n 对应行：load_config/status/_validate_config/save_track/_save_locked/open_store/make_tempdir
python3 -B 纯函数调用 load_config()/_validate_config()/status()（PYTHONDONTWRITEBYTECODE=1）
manual_save_anonymous.py：O_TMPFILE/linkat(AT_EMPTY_PATH)/close 冻结语义
# ③ 回滚包
解 rollback 与历史 2.8.2 的 app.tgz → diff -rq（60 内容差异 + 3 only-in）
VERSION od -An -tx1；manifest checksum vs md5(app.tgz)
rollback vs /tmp/build/282、/tmp/build/head 逐文件 sha256sum
```

**执行方式：** 本机 `ssh -o BatchMode=yes kotomi-root 'bash -s'`（base64 stdin）；除 §0 声明的已清理 `/tmp` 分析副本外无设备写入。
