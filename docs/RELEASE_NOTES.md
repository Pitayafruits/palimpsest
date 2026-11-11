## Palimpsest v0.3.1 · 让开始更简单

这次更新简化了 Windows 的启动流程，并为 Windows 和 macOS 换上统一的专属图标。

- Windows 启动直接进入首页，移除单独的“连接站点”前置窗口。
- Windows 首次使用时，从首页粘贴的完整 Confluence 文档链接自动识别并保存站点，支持 `/wiki` 等路径和非默认端口。
- 已有站点、账号及文档关联继续使用；站点配置无法读取时仍可进入首页，并通过完整文档链接重新配置。
- 修复先进入首页、后识别站点时的登录记忆绑定；保留跨站点检查，避免将账号凭证发送到其他站点。
- 新图标以蓝色旧稿与暖白新稿交叠，呼应“重写本”，接入 Windows 程序、主窗口、冲突窗口及 macOS 应用包。
- 保留 v0.3.0 的逐处冲突处理、编辑草稿、双向图片同步和写回保护。

### 下载与使用

- Windows x64：完整解压 `Palimpsest-windows-x64.zip`，运行 `Palimpsest.exe`，保留旁边的 `engine` 文件夹。
- macOS Apple Silicon：下载 `Palimpsest-macos-arm64.dmg`，将 `.app` 拖到 Applications。
- macOS Intel：下载 `Palimpsest-macos-x64.dmg`，将 `.app` 拖到 Applications。

Mac 同时提供 ZIP，各平台附有 SHA256SUMS 校验文件。更新应用前请退出旧版本；原有账号、关联与同步记录继续保留。

Mac 包尚未经过 Apple 公证，发行包未使用付费开发者证书签名。需要允许用户名 / 密码 Basic Authentication 的 Confluence Server / Data Center。
