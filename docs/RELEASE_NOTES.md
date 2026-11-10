## Palimpsest v0.1.0 · Two shores. One manuscript.

首个公开版本：将一篇本地 Markdown 与一个 Confluence 页面持续双向同步。

- Windows 原生界面，macOS Qt 界面。
- 首次运行配置自己的 HTTPS Confluence Server / Data Center 站点。
- 一对一关联、记住登录、退出账号、默认浏览器打开文档。
- 正文与图片同步；版本检查、冲突停止、备份及不完整转换保护。
- 修复复杂格式、编号、中文长文与链接中的 XML 实体转换。

下载对应系统的包；Mac 分 Apple Silicon（arm64）和 Intel（x64）。Windows 完整解压后运行 `Palimpsest.exe`；Mac 打开 DMG 并复制 `.app` 到 Applications。

这些构建未使用付费开发者证书签名，也未进行 Apple 公证。系统可能显示发行者验证提示。仅适用于允许用户名/密码 Basic Authentication 的 Server / Data Center；不支持 Cloud、纯 SSO 或需要验证码的登录流程。文件旁的 `.assets` 文件夹需要与 Markdown 一起保留。

每个平台提供 SHA256SUMS 文件。详细说明、限制和本地数据位置见 README。
