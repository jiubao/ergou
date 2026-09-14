# Ergou

在 Chrome 中发现网页视频，通过本地 Web 服务下载，在浏览器中管理任务。第一版面向 Windows，界面为中文。

## 功能

- Chrome 插件联合页面元素和网络请求发现视频，支持动态页面、iframe、直接文件与 HLS/DASH 清单。
- 插件显示候选视频，支持默认画质下载、解析清晰度、向已有中断任务提交新来源。
- 本地服务使用 yt-dlp 下载，FFmpeg 合并音画，ffprobe 检查完成文件。
- Web 页面提供任务、历史、搜索、进度、取消、重试、文件操作和设置。
- SQLite 保存任务和设置；重启后未完成任务标记为中断，由用户重试。仅在来源身份一致、协议允许时复用未完成数据。
- 登录视频按任务转交相关 Cookie 和必要请求头。凭据保留在内存中，不写入任务数据库；服务重启后需要重新从原页面获取。

## 首次运行

需要 Windows 10/11、Chrome、PowerShell 7、Node.js 24、pnpm 10、uv。uv 会为服务建立独立 Python 3.13 环境。

在仓库根目录运行：

```powershell
pnpm run setup
pwsh -File scripts/install-ffmpeg.ps1
pnpm start
```

`setup` 和 `doctor` 与 pnpm 自带命令重名，因此需要保留 `run`；其余项目脚本可以直接使用上面的简写。

`install-ffmpeg.ps1` 从 yt-dlp 的 FFmpeg-Builds 发布页下载 Windows x64 工具，验证上游 SHA256 后解压到 `.tools/ffmpeg`。已有 FFmpeg 时，可以跳过该步骤，将所在目录加入 PATH，或设置 `ERGOU_FFMPEG_DIR`。下载的归档和许可证保留在 `.tools`，不进入 Git。

启动脚本会在后台运行服务、显示访问令牌，并打开 <http://127.0.0.1:17890>。首次在网页中输入令牌即可连接。脚本启动后关闭管理网页或终端都不影响服务；手动运行 `ergou serve` 时，终端需要保持运行。

### 安装 Chrome 插件

1. 打开 Chrome 的扩展管理页面 `chrome://extensions`，开启开发者模式。
2. 点击“加载已解压的扩展程序”，选择仓库中的 `apps/extension/.output/chrome-mv3` 目录。旧版目录已经迁移，之前加载过插件时需要从这里重新加载。
3. 固定 Ergou 图标，打开插件的连接设置，输入本地服务地址和同一个访问令牌。
4. 打开普通网页并播放视频，等待插件图标出现数量提示，然后选择下载。

插件需要访问 HTTP/HTTPS 网站来自动发现请求，并在用户选择下载时读取相关 Cookie。Chrome 的网站访问设置必须允许目标网页和媒体 CDN；隐身模式、浏览器内部页面不在默认识别范围内。

修改插件代码并重新构建后，在扩展管理页面点击重新加载，再刷新待检测网页。

### 日常管理

```powershell
pnpm start                                  # 启动或打开已有服务
pnpm stop                                   # 停止由脚本启动的服务
pnpm run doctor                             # 检查依赖与路径
uv run --project apps/services ergou token  # 再次查看访问令牌
```

默认数据目录为 `%LOCALAPPDATA%/Ergou`；数据库、令牌和日志在其中保存。视频默认保存在当前用户下载目录下的 `Ergou` 文件夹。修改保存目录只影响新任务。

| 环境变量           | 用途                                               |
| ------------------ | -------------------------------------------------- |
| `ERGOU_DATA_DIR`   | 覆盖数据库、令牌、日志与任务临时文件目录           |
| `ERGOU_PORT`       | 服务端口，默认 `17890`；修改后同时更新插件连接设置 |
| `ERGOU_FFMPEG_DIR` | 同时包含 ffmpeg.exe 和 ffprobe.exe 的目录          |
| `ERGOU_WEB_DIR`    | Web 构建产物目录                                   |

## 开发

三个应用位于 `apps`，共享代码位于 `packages`。`contracts` 是共享类型与客户端库，不是独立服务。

| 目录                 | 内容                                             |
| -------------------- | ------------------------------------------------ |
| `apps/extension`     | WXT / React / TypeScript / Chrome Manifest V3    |
| `apps/services`      | FastAPI / SQLAlchemy / Alembic / SQLite / yt-dlp |
| `apps/web`           | React / Vite / CSS Modules                       |
| `packages/contracts` | OpenAPI、生成的 TypeScript 类型与 API 客户端     |
| `scripts`            | 环境初始化、构建辅助、启动停止与依赖检查         |
| `tests/browser`      | Web 与插件端到端测试                             |

在不同终端中运行：

```powershell
pnpm dev:services
pnpm dev:web
pnpm dev:extension
```

Web 开发地址为 <http://127.0.0.1:5173>，API 和 WebSocket 代理至默认本地服务。正式运行由服务托管 `apps/web/dist`。下载调试期间不要给服务开启自动重载；重载会中断执行任务。

```powershell
pnpm contracts        # 显式更新 OpenAPI 和前端类型
pnpm contracts:check  # 只读检查已提交契约
pnpm check            # 契约、类型和代码规范
pnpm test             # 服务端及全部 TypeScript 单元测试
pnpm exec playwright install chromium
pnpm test:e2e          # 构建并运行跨应用浏览器测试
pnpm verify            # 完整检查、单元测试和端到端测试
```

浏览器测试会在 `17894` 和 `17895` 端口启动隔离服务与媒体测试页面，在 `.local/browser-tests` 生成合成视频和任务数据。不会使用真实账号或真实下载目录。若已安装兼容的 Chromium，可通过 `ERGOU_CHROMIUM_EXE` 指定其完整可执行文件路径。

数据库迁移位于服务包内，服务启动时执行。更改 API 模型后运行 `pnpm contracts`，并同时验证两个前端。

## 实现边界

- 面向任意网页尝试通用识别，实际下载能力由媒体协议、浏览器可见请求和解析器决定。
- 第一版支持点播；直播、DRM、加密 HLS、显式暂停、安装器和系统自启动未提供。
- `blob:` 本身不是可下载的远程地址；应用会继续寻找媒体请求，或尝试解析原网页。WebRTC、仅有零散片段且缺少清单等情况会提示无法处理。
- HLS 主清单可读取时会归并其中的清晰度和音轨；无法确认关联时保留独立候选，不自动推断广告或正片。
- Cookie、签名地址或设备校验可能使资源在浏览器外不可用。地址或登录态失效时，回到原网页播放，使用插件“更新已有任务的来源”。
- 已取消、失败和中断任务的临时文件保留以供恢复。清理大量临时数据前应停止服务，并确认这些任务不再需要恢复。
- Web 管理界面没有公网部署或多用户账号体系。服务默认只绑定回环地址，API 需要访问令牌；不要分享令牌文件或包含敏感来源的数据库。

## 依赖和许可证

本项目代码采用 MIT 许可证。yt-dlp、FFmpeg 及其他第三方依赖适用其各自许可证。FFmpeg 安装脚本当前下载 GPL 构建；首次运行方式为本地安装依赖，本仓库不提交这些二进制文件。若后续制作包含依赖的发布包，应随包保留相应许可证与上游要求的材料。
