## Palimpsest v0.3.0 · 一次，只处理一处

重新设计文档冲突窗口，让确认内容更简单、更专注。Windows 与 macOS 使用同一套交互。

- 只展示需要处理的冲突，每次一处；相同内容与自动合并的修改不再占据界面。
- 本地与网页并排对比，差异词句柔和高亮。选择“保留本地”或“保留网页”，再进入下一处；支持返回修改选择。
- “自己调整…”直接在当前窗口编辑，可从本地、网页或合并内容开始，无需切换多个窗口。
- 自动保存选择与未完成的编辑草稿，稍后回来继续处理。
- 浅灰背景、白色圆角卡片、细蓝选框和勾选状态，减少无关信息，让内容成为重点。
- 所有冲突确认后，点击“同步两端”才会更新文档；继续保留版本检查、自动备份和中断恢复保护。

### 下载与使用

- Windows x64：完整解压 `Palimpsest-windows-x64.zip`，运行 `Palimpsest.exe`，保留旁边的 `engine` 文件夹。
- macOS Apple Silicon：下载 `Palimpsest-macos-arm64.dmg`，将 `.app` 拖到 Applications。
- macOS Intel：下载 `Palimpsest-macos-x64.dmg`，将 `.app` 拖到 Applications。

Mac 同时提供 ZIP，各平台附有 SHA256SUMS 校验文件。更新应用前请退出旧版本；原有账号、关联与同步记录继续保留。

Mac 包尚未经过 Apple 公证，发行包未使用付费开发者证书签名。需要允许用户名 / 密码 Basic Authentication 的 Confluence Server / Data Center。
