# `/vol1` 权限模型现场发现：POSIX mode 不是本地安全边界（2026-11-07）

> **设备现场证据，不是安全审计结论、也不是上线授权。** 本文只记录本机受控实验；**不**证明 fnOS 存在跨用户漏洞，也**不**排除管理员/服务账户属于产品设计内的特权。
> 执行者：Lead。窗口：2026-11-07 10:21–10:23（NAS 本地 CST）。所有实验文件只建在一次性 `/vol1` 探针目录与 `/tmp`，结束后清理。
> 关联：`docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md`（§0.2 身份链路）、`docs/BTRFS_RO_ROOTCAUSE_AND_MITIGATION_20261107.md`（trimacl 的 RO 绕过）。

## 1. 环境

`kotomi`，fnOS `1.2.0903`，内核 `6.18.18.c1168-trim`，`/vol1` btrfs 挂载 `rw,relatime,trimacl,space_cache=v2`。测试账户：`kurumi`（uid 1000，`admin:true`，组 `Users,Administrators`）、`www-data`（uid 712）、`nobody`（65534）、`trim-media`（999，`AppUsers`）。

## 2. 受控实验与结果

### 2.1 普通文件（root 属主、mode 0600，内容仅 7 字节合成串）

| 位置 | 账户 | 操作 | 结果 | 对照 |
| --- | --- | --- | --- | --- |
| `/vol1/1000/<probe>/root-600.txt` | `kurumi`（NAS 管理员） | 读 | **ACCEPTED**（7 B） | `/tmp` ext4 同权限文件：REJECTED EACCES |
| 同上 | `kurumi` | **追加写**（open ab） | **ACCEPTED**（文件 size 7→8，原 mode 600 未变） | — |
| 同上 | `www-data` | 读 | **ACCEPTED**（7 B） | `/tmp` ext4 同权限文件：REJECTED EACCES |
| 同上 | `nobody` / `trim-media` | 读 | REJECTED EACCES | — |
| `/vol1/1000/<probe>/root-700-dir` | `kurumi` | 创建文件 | **ACCEPTED** | — |
| 同上 | `www-data` / `nobody` / `trim-media` | 创建文件 | REJECTED EACCES | — |

### 2.2 Unix socket（root 属主，mode 0600）

| 位置 | 账户 | 结果 |
| --- | --- | --- |
| `/vol1/1000/<probe>/test-600.sock`（root 0600，测试 server） | `kurumi` | **connect ACCEPTED + send/recv 成功**（收到 `server-ok`） |
| 同上 | `www-data` | REJECTED EACCES（PermissionError 13） |
| `/run/trim_http_cgi.socket`（root:www-data 0770） | `kurumi` | REJECTED EACCES |
| 同上 | `www-data` | ACCEPTED（符合 0770 + www-data 组） |
| `/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock`（root:root 0600） | `kurumi` | **connect ACCEPTED**，随后 `recv ECONNRESET`（应用层拒绝）；`www-data` connect 被 EACCES 拒绝 |
| 同上 | root | ACCEPTED；无身份头 GET → 403（正常处理） |

## 3. 解释（诚实边界）

1. `/vol1` 挂载了 fnOS 的 `trimacl`；本机观测显示 **VFS/POSIX 权限位在 `/vol1` 上不是可靠的本地安全边界**：至少存在
   - **NAS 管理员（`kurumi`）对 `/vol1` 文件/目录/socket 具备近似 root 的读写/连接能力**（即使 mode 0600/0700、属主 root）；
   - **`www-data` 对 `/vol1` 下的普通文件可读**（但不可写 0700 目录、不可连 0600 socket）；
   - 其它普通账户（`nobody`、`trim-media`）被正常拒绝。
2. 这**可能**是 fnOS 设计：管理员本就被视为 NAS 的超级用户；Web 服务账户需要读用户文件来提供下载/预览。**本次实验不能区分"设计特权"与"ACL 缺陷"**，也未审计 `trimacl` 实现。
3. 因此，本项目**不能把 `/vol1` 上的文件 mode 或 socket mode 当作授权边界**：
   - socket `0600 root:root` 只挡住了非管理员（`www-data` 被内核拒绝），但**挡不住 NAS 管理员**；
   - 真正可依赖的边界是**内核 `SO_PEERCRED` + 应用层 peer uid allow-list**：`peer_uid_not_allowed` 是**代码层预期拒绝码**（部署版 `webui_gateway.py`=`48b51e93…` 与仓库版 `1b07f815…` 不同，本地不能逐行复核）；**实际观测**为非允许管理员连接被 **连接重置、无任何 HTTP 响应**（fail-closed）。
4. 对 secrets 的影响：`/vol1/@appcenter/fnmusic-ext/repo/.env`（`kurumi:Administrators 600`）理论上可被 NAS 管理员与（若 ACL 类似）`www-data` 读取；本次**未读取其内容**，只记录 stat。生产秘密若需更强隔离，应考虑平台 ACL/权限策略，而不是仅依赖 mode。

## 4. 对项目门禁的影响

- **§0.2 的 L1 表述需修正**：`0600 root:root` 不是"仅 root 可连"的证明；本机实测 NAS 管理员可连。安全论证必须落在 L2（peer allow-list，已观测 fail-closed）+ 上游 `trim_http_cgi` 的 token 校验 + 头剥离/覆盖（后者仍未验证，见 `docs/TRUSTED_INGRESS_READONLY_EVIDENCE_20261107.md`）。
- **staging/target 权限分离**：`staging root:root 0700` 与 `target kurumi:Users 0750` 的差异不能当作隔离证据；管理员/服务账户的访问能力需按平台 ACL 重新论证。
- **扫描器"非特权进程"约束**：其价值（防止 remount/thaw、限制自身能力）不变；但不能因此推断"非特权用户看不到/改不了文件"。
- **不改变** §0.1/§0.2 BLOCKED；本文件不构成安全审计或授权。

## 5. 复现

- 实验脚本（本地临时，未入库）：`perm_probe.sh`、`perm_probe2.sh`、`gateway_peer_probe.sh`；核心步骤已列于 §2。所有探针目录 `/vol1/1000/fnm-perm-probe*-20261107` 运行后已 `rm -rf`，socket server 已 kill。
- 对照：`/tmp`（ext4）/`/run`（tmpfs）上同权限对象均按 POSIX 拒绝，说明差异来自 `/vol1` 的 fnOS ACL 层而非测试写法。

## 6. 未证明 / 下一步

1. 未审计 `trimacl`/`trimafs2` 规则，无法断言"管理员特权"的准确范围（跨 uid、组、路径、继承）。
2. 未测试其它 NAS 管理员账户、AD/LDAP 用户、SMB 会话映射。
3. 未测试 `www-data` 对 `/vol1/1000/music` 等真实曲库文件的读取（避免不必要地读用户数据）；也未测写。
4. 未测试容器内进程（不同 uid/namespace）的等价行为。
5. 若要把本发现用于安全结论，需要 fnOS 官方文档/ACL 说明或独立安全审计；本文件仅作为本机实测记录。
6. 部署版 `webui_gateway.py`=`48b51e93…` 与仓库版 `1b07f815…` 不同，L2 的代码路径无法在本地逐行复核；`peer_uid_not_allowed` 只是代码层预期拒绝码，**实际观测**是非允许管理员连接被重置、无 HTTP 响应。
