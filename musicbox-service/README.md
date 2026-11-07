# musicbox-service

HTTP 包装 [darknessomi/musicbox](https://github.com/darknessomi/musicbox)（PyPI：`NetEase-MusicBox`），供 fnmusic-ext 作为网易云音源（默认 `127.0.0.1:8770`）。

部分曲目可能需要扫码登录：`GET /api/v1/auth/login/qr.png`。

### 当前账号歌单只读预览

`GET /api/v1/user/playlists/{playlist_id}/preview` 使用当前登录账号读取歌单成员并补充歌曲展示元数据。预览最多处理 1000 个位置，保留网易云源顺序和重复曲目；无效曲目 ID、缺失详情、详情请求失败或详情映射失败会在对应位置显式标记。服务会在元数据读取前后重新检查账号、歌单版本、计数和有序曲目 ID；只有这些来源信息一致且位置/计数吻合时，`enumeration_complete` 才为 `true`。`metadata_complete` 单独表示所有曲目详情是否齐全。

这是请求期间生成的临时预览，不是持久快照。所有响应的 `import_ready` 都是 `false`；该接口不会请求音频地址、下载文件或写入曲库。超出位置上限、源结构不支持或缺少可比较版本时返回 `status: "unsupported"` 和稳定的 `error_code`；未登录与上游读取失败使用不同状态。此接口只面向当前本地音乐服务的管理流程，不应把预览结果当成导入授权或下载保证。

本地运行：

```bash
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8770
```
