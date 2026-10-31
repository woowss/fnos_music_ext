# NetEase-MusicBox 交付与调用路径证据

> 复核合同：`FNMEXT-MUSICBOX-ARTIFACT-AUDIT-20261025-01`。本文件记录固定 CI 运行所安装的 Python 发布物及静态源码路径，不代表新增保存功能、部署或设备验收。

## 结论

- GitHub Actions 运行 `40200632732` 对应源码提交 `fff98b24638999efa5c70e11deea8cb280a42238`，结论为 `success`。`production (musicbox, 3.11)` job `121190860533` 和 `production (musicbox, 3.13)` job `121190860514` 的安装日志都显示安装 `NetEase-MusicBox 0.5.3`，下载文件为 `netease_musicbox-0.5.3-py3-none-any.whl`。
- PyPI 官方 JSON 中同一发布文件的 SHA-256 为 `7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12`。本次把 wheel 字节读入内存并计算摘要，与 PyPI JSON 的 `digests.sha256` 一致；没有将 wheel 安装或执行。
- CI 安装日志记录了精确 wheel 文件名和解析版本，但没有打印所下载字节的 SHA-256；因此这里的摘要直接验证的是 PyPI 上同名发布物，不能声称 CI 下载副本做过字节级摘要比对。
- 固定版本源码包含账号歌单读取、歌曲播放 URL 解析、Cookie 持久化，以及独立 CLI 下载到指定路径的实现。仓库当前 `musicbox-service` 暴露了歌单元数据/曲目和播放 URL 路由；静态搜索没有发现这些路由调用 `musicbox download`。因此，上游 CLI 能写普通文件不等于当前扩展已实现获授权的 NAS 曲库交付。
- 本次没有使用真实网易云账号或 Cookie，没有调用网易云账户、歌单、音频或下载 API；没有触碰 NAS/G0、安装发布物或写入音乐文件。

## 固定版本与可复核来源

| 证据 | 值 |
| --- | --- |
| 仓库分支与基准 | `codex/fnmusic-prelaunch-20261025`，`fff98b24638999efa5c70e11deea8cb280a42238` |
| CI 运行 | [40200632732](https://github.com/woowss/fnos_music_ext/actions/runs/40200632732)，`headSha` 与基准相同，运行结论 `success` |
| Python 3.11 安装 job | [121190860533](https://github.com/woowss/fnos_music_ext/actions/runs/40200632732/job/121190860533)，安装日志记录 wheel 与 `Successfully installed NetEase-MusicBox-0.5.3` |
| Python 3.13 安装 job | [121190860514](https://github.com/woowss/fnos_music_ext/actions/runs/40200632732/job/121190860514)，安装日志记录 wheel 与 `Successfully installed NetEase-MusicBox-0.5.3` |
| 仓库依赖声明 | `musicbox-service/requirements.txt`：`NetEase-MusicBox>=0.5.3`；实际解析版本来自上述固定 CI 日志，而非由下界推断 |
| PyPI JSON | [NetEase-MusicBox 0.5.3 JSON](https://pypi.org/pypi/NetEase-MusicBox/0.5.3/json)，`info.name=NetEase-MusicBox`、`info.version=0.5.3` |
| PyPI 项目源位置 | [darknessomi/musicbox](https://github.com/darknessomi/musicbox)，来自该版本 PyPI metadata 的 `Repository`/`Homepage` 字段 |
| 精确发布物 | [netease_musicbox-0.5.3-py3-none-any.whl](https://files.pythonhosted.org/packages/72/5a/bc6a745a1c40874015ae7272a5adbdff1808b099c4babd6784c0b75bdaa3/netease_musicbox-0.5.3-py3-none-any.whl) |
| SHA-256 | `7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12`（PyPI JSON 与本次内存下载计算相同） |

复核命令与过程：

```powershell
gh run view 40200632732 --repo woowss/fnos_music_ext --json headSha,status,conclusion,jobs
gh run view 40200632732 --repo woowss/fnos_music_ext --job 121190860533 --log
gh run view 40200632732 --repo woowss/fnos_music_ext --job 121190860514 --log
```

从两个 job 日志仅筛出包含 `NetEase-MusicBox` 的行，可见 wheel 文件名和最终安装版本。随后请求 PyPI 官方 JSON，按 JSON 指定的 wheel URL 读取字节，计算 SHA-256，并以 `zipfile` 在内存中列举/读取 `.py` 成员。该静态检查没有调用导入、CLI 或 Python 包代码，也没有落盘归档。

## 固定 wheel 的静态调用路径

以下路径和行号对应 PyPI `0.5.3` wheel 内文件；只说明源码实现，不表示本次调用过这些网易云接口。

| 功能 | wheel 内文件与位置 | 可观察到的源码行为 |
| --- | --- | --- |
| Cookie 与本地状态位置 | `NEMbox/const.py:5-29` | 使用 `XDG_CONFIG_HOME`、`XDG_DATA_HOME`、`XDG_CACHE_HOME`；默认配置目录为 `~/.netease-musicbox`，Cookie 为 `cookie.txt`，下载缓存位于 cache/config 目录下。 |
| Cookie 加载与登录写入 | `NEMbox/api.py:329-359,386-400,547-553,590-593,641-672`；`NEMbox/cli.py:456-488` | `NetEase` 用 `MozillaCookieJar` 加载 Cookie；二维码登录流程会保存匿名 Cookie，成功码 `803` 时解析响应 Cookie 并保存；EAPI 请求会调用 `_persist_new_nmtid()`，发现新 `NMTID` 时可条件性写回 jar。`auth login` CLI 提供二维码登录分步流程。 |
| 账号歌单列表 | `NEMbox/api.py:674-686` | 先通过账号信息取得 UID；`user_playlist()` 向 `/weapi/user/playlist` 发出请求并返回歌单数据。 |
| 歌单曲目 | `NEMbox/api.py:841-844`；`NEMbox/cli.py:349-359` | `playlist_songlist()` 请求 `/api/v6/playlist/detail` 并提取 `trackIds`；CLI `playlist show` 再调用 `dig_info` 取歌曲详情。 |
| 歌曲播放 URL | `NEMbox/api.py:172-205,888-914`；`NEMbox/cli.py:314-346` | `songs_url()` 请求 `/api/song/enhance/player/url/v1`，无结果时回退 `/weapi/song/enhance/player/url`；`Parse.song_url()` 解析返回链接，缺少 URL 时可构造外链地址；`song url` CLI 将 URL 作为 JSON/文本结果输出。 |
| 独立下载与文件写入 | `NEMbox/cli.py:697-795` | `download --playlist/--songs/--album/--artist --path ...` 可选择目标目录，取歌曲 URL 后用 `requests.get(..., stream=True)` 并以 `open(filepath, "wb")` 写入响应块；扩展名按 URL 是否含 `.flac` 选择 `.flac` 或 `.mp3`。这是明确的文件写入行为，不是只读预览。 |
| 播放器缓存下载 | `NEMbox/cache.py:74-150`、`NEMbox/const.py:11-19` | 独立缓存路径通过 aria2c 或 wget 获取歌曲到 NetEase-MusicBox 的缓存目录；这不是扩展的目标曲库写入流程。 |

## 当前仓库适配层的边界

固定候选提交中的相关路径：

- `musicbox-service/app.py:223-232` 暴露歌曲 URL 解析路由；`musicbox-service/playback.py` 复用 wheel 的 API/加密函数获取 URL，必要时以 `musicbox song url ... --json` 作 CLI 兜底。播放代理 `proxy/app.py` 的 `_resolve_netease_url_uncached` 消费解析结果用于播放链路。
- `musicbox-service/app.py:487-544` 暴露 auth status、二维码登录和登录状态轮询路由，通过 `runner.run_musicbox()` 调用 `musicbox auth login`；扫码成功返回码 `803` 后调用 `reset_api()`，使常驻实例重新读取 CLI 已写盘的 Cookie。实际 Cookie 路径受该服务用户的 XDG 环境和 home 目录影响。
- `musicbox-service/app.py:278-295` 暴露账号歌单、临时预览和歌单曲目路由；`musicbox-service/netease_ext.py:457-515` 读取登录状态、账号 UID、歌单列表和曲目 ID。预览路径 `user_playlist_preview()` 在业务结果层读取歌单/歌曲元数据，不请求音频 URL、不下载或写入音乐文件，并以 `import_ready=false` 标记不可直接导入；具体预览快照在 `netease_ext.py:540-543` 调用 `api.eapi_request()`。这不等于整条上游调用链只读：`NEMbox/api.py:590-593` 的 EAPI 请求会调用 `_persist_new_nmtid()`，其 `547-553` 在满足条件时保存 Cookie jar；`NetEase` 初始化也加载本地 Cookie 并构造请求缓存/状态。 |
- `musicbox-service/playback.py:46-85` 对 wheel 提供的 Cookie/headers 做请求快照；播放 URL 请求可以将符合域名范围的响应 Cookie 合并并保存到 Cookie jar。该行为是播放协议状态维护，不是保存授权或曲库授权。
- 本次静态搜索 `rg -n -i 'musicbox.*download|exec_musicbox\(\[.*download|cmd_download|/api/v1/user/playlists|/api/v1/song/.*/url|download.*path' musicbox-service proxy webui-service` 找到了歌单和 URL 路由，没有找到生产适配器对 `cmd_download` 或 `musicbox download` 的调用。

## 不等同于上线验收的事项

- CI 通过只证明该提交上的 CI job 成功；不证明当前设备已安装同一候选代码、NetEase 登录能在目标环境工作或 URL 可用于真实播放。
- 上游 `musicbox download` 虽能把其响应字节写入给定文件系统路径，但没有证明目标为 NAS 音乐库、满足扩展的防覆盖/权限/生命周期要求，或能被飞牛音乐识别索引。不能把它直接视为已获授权的产品交付链路。
- 静态检查到的下载写入根据 URL 后缀命名 `.mp3`/`.flac`，不构成 `.ncm` 解密或 `.ncm` 转换为真正可解码 `.flac` 的证据。
- 产品计划要求的 G0 可信身份/服务生命周期、真实曲库落盘、索引和删除/回滚验收仍是单独门槛；本次没有补齐或验证它们。参见 [手动保存与整歌单导入计划](MANUAL_IMPORT_PLAN.md)。
