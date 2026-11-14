# 网易云音乐现代接入方案（B1：纯 Python 协议栈重构）

> 文档日期：2026-11-14
> 适用仓库：`woowss/fnos_music_ext`（基准 `fd6afe2` / `v2.13.0`）
> 状态：已立项，分三批实施

---

## 一、背景与真机诊断结论

在 `kotomi` 真机（`2.13.0-0`）针对「手动保存单曲」「手动保存歌单」做只读排查后，确认当前主要问题并非“网易云账号拿不到无损”，而是由老旧底座 `NetEase-MusicBox==0.5.3`（2018 年时代）及网关历史上限约束引起：

1. **账号实际可取超清母带（`jymaster`）与无损（`lossless`）**
   - 实测 `kotomi` 已登录账号（`uid: 13103193918`，含 VIP 权益）调 `/api/v1/song/{id}/url?quality=jymaster` 时：
     - 免费曲目 `1354451040` 返回 `192kHz / 5285kbps / ~193MB` `FLAC`（`level: "jymaster"`）；
     - VIP 曲目 `459925524`（`fee: 1`）返回 `192kHz / 5348kbps / ~225MB` `FLAC`（`level: "jymaster"`）。
2. **为什么体感“只能下 320k、和网易云很不同”**
   - **搜索预览假标 `HD 320k`**：`musicbox-service` 的 `/api/v1/search` 调用老 `musicbox` CLI 搜索，每行固定带 `"level": "exhigh", "quality": "HD 320k"`；前端 `webui-service/static/app.js` 直接展示 `HD 320k`，既看不到曲目真实最高音质，也不能在保存前按曲目自选音质。
   - **激进过滤导致原曲失踪**：`musicbox-service/netease_ext.py` 的 `filter_playable_song_ids()` 直接过滤掉无直链或仅试听的原唱，导致搜索热门版权歌（如周杰伦作品）时，列表前面全变成仅有 320k MP3 的第三方翻唱/钢琴版，甚至造成误下载。
3. **为什么「手动保存歌单」完全不可用（两道先后断点）**
   - **断点 1（32 位 ID 上限拦截）**：`proxy/webui_gateway.py` 中 `MANUAL_SAVE_PLAYLIST_MAX_PLAYLIST_ID = 0xFFFFFFFF`（`4,294,967,295`），`MANUAL_SAVE_PLAYLIST_DECIMAL_RE` 仅允许最多 `10` 位十进制数。`kotomi` 上用户 14 张网易歌单中有 11 张大于 `0xFFFFFFFF`（如 `7368514547`、`13283668114`、`18499998278`），点击「读取清单」即在网关被拒为 `invalid_playlist_id`（`歌单 ID 必须是正整数`）。（同源隐患：`proxy/manual_save_route.py` 中 `DIGITS_RE` 也限制 `1..10` 位）。
   - **断点 2（快照端点默认关闭 + 绑定旧 wheel）**：`musicbox-service/netease_playlist_snapshot.py` 要求进程环境变量 `FNMUSIC_PLAYLIST_MANIFEST_READONLY=true`（不联动 WebUI 的「整歌单保存」开关，真机默认返回 `{"ok":false,"error":"manifest_disabled"}`），且 `source_artifact` 硬编码校验 `NetEase-MusicBox == 0.5.3`。

---

## 二、方案边界与可解决范围

### 确定解决的问题
1. **修通全量网易歌单读取与批量保存**：支持 64 位正整数歌单/单曲 ID，开启 WebUI 整歌单保存后即刻可用，支持读取个人歌单与外部任意公开歌单链接/ID。
2. **还原真实音质展示与自选保存**：搜索与歌单列表基于现代 `cloudsearch` + `privilege` 直接显示真实最高可用档位（`超清母带` / `Hi-Res` / `无损 FLAC` / `320k`）与权限状态（`VIP 可下` / `VIP 需会员` / `仅试听` / `无版权`），支持单首保存前选择目标音质，保存后明确回显真实落盘格式、采样率与体积。
3. **阻止“搜原曲却下到 320k 翻唱”**：手动保存搜索模式保留原曲行并明确标识受限状态，避免静默隐身后误下翻唱。
4. **消灭老 `NEMbox` CLI 子进程与登录态脆断**：全部接口改为 `musicbox-service` 进程内纯异步 `httpx` + `cryptography` 请求，保持原有 `./sources-data/netease-musicbox/cookie.txt` 兼容，单次请求风控抖动绝不误清 Cookie jar。

### 客观边界（上游平台限制）
1. **网易云无版权曲目仍无法从网易云下到官方原唱无损**：本方案会如实标出「无版权/仅试听」，不会拿 320k 翻唱伪装；跨平台解灰仍应通过 `musicdl` / `lxmusic` 主音源完成。
2. **受网易云账号权益与频率控制约束**：未购买的数字专辑单曲仍将被保存安全层拦截（`trial_only`）；整歌单保存继续坚持「逐首串行」控频策略以保护账号风控安全。
3. **本方案聚焦在线抓取落盘**，不包含 PC 客户端 `.ncm` 本地加密文件离线解封。

---

## 三、技术选型：B1（纯 Python 现代协议栈）

在 **B1（纯 Python 现代 EAPI/WEAPI 协议客户端）** 与 **B2（镜像内置 Node.js `NeteaseCloudMusicApiEnhanced` Sidecar）** 之间选择 **B1 作为默认底座**，并保留可选外部 API URL 适配口：

- **依赖极简**：用 `cryptography>=42.0` 替代 `NetEase-MusicBox==0.5.3`（标准 Python `pow(m, e, n)` 做 WEAPI textbook RSA + `cryptography` 做 AES-128-CBC / AES-128-ECB），`linux/amd64`、`linux/arm64`、`win_amd64` 均有官方 wheel，完全适配国内离线构建与 `production` 离线 CI 契约。
- **低内存零额外常驻进程**：不增开 Node API 常驻进程（适合「网易云歌单常驻」长期后台挂机）。
- **可平滑扩展外部 API**：预留 `FNMUSIC_NETEASE_EXTERNAL_API_URL`（默认空走内置 B1 客户端；填写时可把协议请求转发到用户外部自建的 `NeteaseCloudMusicApi` 实例）。
- **稳定层不变**：继续复用 `proxy/webui_gateway.py` 安全边界与 `proxy/manual_save_route.py` 的原子落盘/转码/封面前后端流水线。

---

## 四、模块级改造设计

### 1. 现代纯 Python 网易云客户端（`musicbox-service/`）
- **`musicbox-service/netease_crypto.py`**：
  - `weapi_encrypt(payload)`：AES-128-CBC（固定盐 + 随机 16 字符密钥）+ 1024-bit 无填充 RSA 加密；
  - `eapi_encrypt(url_path, payload)` & `eapi_decrypt(cipher_bytes_or_hex)`：AES-128-ECB + MD5 digest；
  - 构造规范移动端 EAPI header Cookie（`os`, `appver`, `osver`, `deviceId`, `requestId`, `MUSIC_U`, `__csrf`）。
- **`musicbox-service/netease_client.py`**：
  - 异步 `httpx.AsyncClient(trust_env=False)` 单例管理（按世代 `_generation` 隔离，扫码成功刷新世代与连接池）；
  - Cookie 持久化兼容 `${XDG_DATA_HOME}/netease-musicbox/cookie.txt`（LWPCookieJar / Netscape 格式，`tmp + os.replace` 原子写盘，保留对已有用户登录态及 `entrypoint.sh` 的 100% 兼容）；
  - 防误清登录态：仅当显式注销或服务端账号校验返回明确未登录（`code=301` 且无 `account/profile`）时判定未登录，接口限流/网络超时绝不删 Cookie；
  - 提供完整异步与可测试方法：`account_info()`、`qr_unikey()`、`qr_check()`、`cloudsearch()`、`song_detail()`、`song_url_v1()`、`song_lyric()`、`playlist_detail()`、`user_playlists()`、`album_detail()`、`artist_top_songs()`、`recommend_songs()`、`toplist()`、`public_playlists_*()`。

### 2. 服务接口演进（`musicbox-service/app.py`, `playback.py`, `netease_ext.py`）
- **完全移除 `exec_musicbox` CLI 子进程调用与 `NEMbox` 导入**。
- **`/api/v1/search`**：
  - 底层切换为 `/eapi/cloudsearch/get/web`；
  - 新增 `mode=playback|manual_save`（默认 `playback`）：
    - `playback` 保持仅返回可完整播放曲目（保证飞牛音乐在线列表点击即播、不混入 30 秒试听）；
    - `manual_save` 保留原曲并附带 `max_quality`、`quality_label`、`playable_status`（`ok` / `vip_required` / `paid_album` / `trial_only` / `copyright`）、`fee`、`has_jm`、`has_hr`、`has_sq`；不再写死 `"HD 320k"`。
- **`/api/v1/song/{song_id}/url`**：
  - 全面通过 `netease_client` 调 `/eapi/song/enhance/player/url/v1`（按音质正确传 `encodeType`：`lossless/hires/jymaster/sky` 传 `flac`，有损档传 `mp3`），返回真实 `level`、`type`、`br`、`sr`、`size`、`md5`、`freeTrialInfo`。
- **`/api/v1/user/playlists/{playlist_id}/tracks` & `/api/v1/playlist/{playlist_id}`**：
  - 支持 64 位 `playlist_id`；从 `/api/v6/playlist/detail` 读取全部 `trackIds` 后分片调用 `/weapi/v3/song/detail` / `/eapi/v3/song/detail`，附带 `privilege` 真实音质字段，突破旧 CLI/预览截断限制。

### 3. 网关与手动保存引擎改造（`proxy/`）
- **`proxy/webui_gateway.py`**：
  - `MANUAL_SAVE_PLAYLIST_MAX_PLAYLIST_ID = 0x7FFFFFFFFFFFFFFF`；
  - `MANUAL_SAVE_PLAYLIST_DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,18}$")`；
  - `source_artifact` 校验从旧 `NetEase-MusicBox == 0.5.3` 切换为新协议模块标识（`fnmusic-netease-client`）；
  - `/api/manual-save/track` 请求体在保留 CSRF 与严格 allowlist 前提下，新增可选白名单字段 `quality`（仅限 `jymaster` / `hires` / `lossless` / `exhigh`）；
  - `/api/manual-save/playlist/snapshot` 与列表接口透传 64 位 ID，并新增公开歌单详情/曲目读取透传接口供 WebUI 展开浏览与部分勾选保存（如需）。
- **`proxy/manual_save_route.py`**：
  - `DIGITS_RE = re.compile(r"^[0-9]{1,18}$")`；
  - `save_track(song_id, *, quality_override=None, deps=None)` 支持单曲指定起始最高档位；
  - 返回数据增加 `br` / `sr`（真实比特率与采样率）等透传信息，供前端结果区展示。
- **`musicbox-service/netease_playlist_snapshot.py`**：
  - `producer_enabled()` 在保持 `FNMUSIC_PLAYLIST_MANIFEST_READONLY` 显式环境变量优先的基础上，支持回退读取 `/repo/.env` 中的 `FNMUSIC_PLAYLIST_MANIFEST_READONLY` 或 `FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED`，让 WebUI 打开「整歌单保存」即刻打通快照。

### 4. WebUI 体验重构（`webui-service/`）
- **单曲搜索列表**：真实音质徽章（`超清母带` / `Hi-Res` / `无损 FLAC` / `极高 320k`）+ 权限状态（`VIP` / `仅试听` / `无版权`）+ 单行目标音质下拉选择器 + 真实落盘细节回显。
- **歌单保存卡片**：
  - 直接支持 10~11 位及以上个人歌单 ID；
  - 支持粘贴任意网易云歌单链接（`https://music.163.com/...playlist?id=...`）或公开歌单 ID；
  - 支持展开所选歌单的曲目列表，查看每首歌音质与可用状态，支持挑单首保存、批量勾选顺序保存，或一键创建整歌单后台任务（超过 1000 首账本上限时明确提示分批勾选保存）。

---

## 五、分批实施与验收门禁

1. **第 1 批：B1 协议底座替代 + 64 位歌单链路打通**
   - 替换 `requirements.txt` 与 `musicbox-service` 全量协议代码（移除 `NetEase-MusicBox==0.5.3` 与 `exec_musicbox`），兼容现有 `cookie.txt`。
   - 放开 `webui_gateway.py` / `manual_save_route.py` 64 位 ID 上限，解除旧 wheel 绑定，打通 `manifest` 开关联动。
   - 更新单测并跑通本机 + GitHub Actions `CI`。
2. **第 2 批：现代 `cloudsearch` + 真实音质标识 + 单曲自选音质保存**
   - 落地 `mode=manual_save` 搜索、真实 `privilege` 音质字段与单曲 `quality` 覆盖保存，升级 WebUI 单曲搜索卡片与单测。
3. **第 3 批：WebUI 歌单明细展开勾选 + 任意歌单链接/ID 读取**
   - 落地歌单曲目展开/勾选/链接解析前后端与安全测试。
   - 全量 GitHub Actions `CI`（12 Job，含双架构镜像 smoke）全绿，发新版包并在 `kotomi` 实测验收（验证歌单 `7368514547`、`13283668114` 与母带/无损单曲落盘）。
