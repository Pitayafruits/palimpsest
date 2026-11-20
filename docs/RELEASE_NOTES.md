## Palimpsest v0.3.3 · 直接从文档链接开始

- macOS 启动直接进入首页，首次粘贴完整的 Confluence 文档链接后自动识别并保存站点。支持 `/wiki` 等路径和自定义 HTTPS 端口。
- macOS 登录与 Windows、macOS 的文档同步使用系统证书信任库，继续验证证书链和域名。证书验证失败时，软件会提示检查站点证书和系统信任设置。

### 下载与使用

- Windows x64：完整解压 `Palimpsest-windows-x64.zip`，运行 `Palimpsest.exe`，保留旁边的 `engine` 文件夹。
- macOS Apple Silicon：下载 `Palimpsest-macos-arm64.dmg`，将 `.app` 拖到 Applications。
- macOS Intel：下载 `Palimpsest-macos-x64.dmg`，将 `.app` 拖到 Applications。

Mac 同时提供 ZIP，各平台附有 SHA256SUMS 校验文件。更新应用前请退出旧版本；原有账号、关联与同步记录继续保留。

Mac 包尚未经过 Apple 公证，发行包未使用付费开发者证书签名。需要允许用户名 / 密码 Basic Authentication 的 Confluence Server / Data Center。
