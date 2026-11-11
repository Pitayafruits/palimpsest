## Palimpsest v0.3.2 · 让换行回到原位

修复网页同步到本地时，加粗的附件预览与换行组合导致转换中断的问题。Windows 和 macOS 均包含此修复。

- 修复 `Opening and ending tag mismatch: strong ... and p`：合法网页内容中的换行不再把行内格式标签拆到不同段落。
- 覆盖加粗、斜体、删除线，以及引用块中的附件预览和图片等场景。
- 保留正文、附件组件与图片，并继续执行严格格式解析、转换完整性检查、版本检查和本地备份。
- 新增格式与图片同步回归用例，验证下载图片、保留附件和备份流程。
- 保留 v0.3.1 的直接进入 Windows 首页与新图标，以及逐处冲突处理和双向图片同步。

### 下载与使用

- Windows x64：完整解压 `Palimpsest-windows-x64.zip`，运行 `Palimpsest.exe`，保留旁边的 `engine` 文件夹。
- macOS Apple Silicon：下载 `Palimpsest-macos-arm64.dmg`，将 `.app` 拖到 Applications。
- macOS Intel：下载 `Palimpsest-macos-x64.dmg`，将 `.app` 拖到 Applications。

Mac 同时提供 ZIP，各平台附有 SHA256SUMS 校验文件。更新应用前请退出旧版本；原有账号、关联与同步记录继续保留。

Mac 包尚未经过 Apple 公证，发行包未使用付费开发者证书签名。需要允许用户名 / 密码 Basic Authentication 的 Confluence Server / Data Center。
