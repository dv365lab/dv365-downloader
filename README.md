# dv365-downloader

<img src="static/dv365-logo.png" width="180" alt="DV365 · 芯片验证日记">

基于 **yt-dlp** 的本机下载器。浏览器打开 Web 界面，粘贴链接，视频与网页内的 PDF 合集保存到当前电脑。

支持范围取决于 yt-dlp、目标网站及访问权限，并非所有网址都能下载。需要登录、受地域限制或带 DRM 的视频可能无法下载；请下载你有权保存的内容。

## Linux 快速使用：双击启动

在 Linux 文件管理器中打开项目文件夹，双击 **dv365-downloader.desktop**（可能显示为 **DV365 Downloader**）即可启动。

如果项目中还没有该文件，先在项目目录运行一次 `python3 install_linux_shortcut.py` 生成图标。应用需要 Python 3.10 或更新版本，完整准备步骤见下方“Linux 双击启动”。

1. 双击图标；如果文件管理器提示，选择“允许启动”或“标记为可执行”。启动器会打开终端并自动打开浏览器；首次运行需要联网安装 Python 依赖。
2. 如果浏览器没有自动打开，在当前电脑访问 **http://127.0.0.1:8765**。
3. 粘贴视频链接，选择画质或 MP3 音频，加入下载队列。文件默认保存在项目的 `downloads` 目录；合并音视频、导出 MP3 需要先安装 FFmpeg。
4. 下载期间保持终端窗口打开。再次双击可重新打开已有页面；结束使用时，在终端窗口按 `Ctrl+C` 停止服务。

## 芯片验证日记｜把验证学明白、做扎实

从学习中的疑问，到工作中的难题，与你一起交流芯片验证、积累实践经验。

**微信公众号：芯片验证日记**，欢迎扫码关注，一起学习与成长。

<img src="static/wechat-qrcode.jpg" width="258" height="258" alt="扫码关注微信公众号：芯片验证日记">

**知识星球：7833526**，欢迎加入！一年365天答疑，为您的工作和学习保驾护航。

**全平台认准「芯片验证日记」**：自媒体各平台统一名称、统一 DV365 Logo，方便找到我们。

## Linux 双击启动

1. 安装 **Python 3.10 或更新版本**（包含 `venv` 和 `pip`）。合并音视频、导出 MP3 还需要 **FFmpeg**；YouTube 下载建议安装 **Node.js 22 或更新版本**。
2. 首次使用，在项目目录运行一次以下命令，生成本机启动图标：

   ```bash
   python3 install_linux_shortcut.py
   ```

   生成图标的脚本可使用系统 Python 3.6 或更新版本；运行应用仍需要 Python 3.10 或更新版本。

3. 在文件管理器中双击 **dv365-downloader.desktop**（可能显示为 **DV365 Downloader**）。如果出现提示，选择“允许启动”或“标记为可执行”。启动器会打开终端，首次运行时创建 `.venv`、安装依赖并自动打开浏览器。

如果文件管理器把图标作为文本打开，请右键选择允许启动或作为程序运行；也可在项目目录运行 `bash start.sh` 启动。浏览器未自动打开时，手动访问 **http://127.0.0.1:8765**。

下载期间请保留终端窗口，按 `Ctrl+C` 关闭服务。再次双击会打开正在运行的应用，不会中断下载。首次安装依赖需要联网。

启动图标包含本机项目的绝对路径，不纳入 Git；可以复制到桌面使用。**移动项目文件夹后，请重新运行生成命令，并替换复制到桌面的旧图标。** 不要直接双击 `app.py` 或 `start.sh`，部分 Linux 文件管理器会将它们作为文本打开。

### Linux 安装 FFmpeg

YouTube 等网站可能只提供分离的视频和音频流，需要 **FFmpeg** 合并为一个带声音的视频文件；只安装 Python 依赖无法完成这一步。

可以通过发行版的软件包管理器安装 FFmpeg，也可以从 [yt-dlp 的 FFmpeg 构建页面](https://github.com/yt-dlp/FFmpeg-Builds/releases) 下载适合当前架构的 Linux 压缩包。Rocky Linux x86_64 使用 `linux64-gpl` 构建；解压后，将 `bin` 目录中的 `ffmpeg` 和 `ffprobe` 放入 `~/.local/bin`，然后运行：

```bash
chmod +x "$HOME/.local/bin/ffmpeg" "$HOME/.local/bin/ffprobe"
"$HOME/.local/bin/ffmpeg" -version
"$HOME/.local/bin/ffprobe" -version
```

Linux 启动器会自动将 `~/.local/bin` 加入 PATH，无需管理员权限。安装后重新打开应用，在“下载设置”中确认 **FFmpeg 已就绪**，再点击失败任务的“重新下载”。

## macOS 一键启动

1. 安装 **Python 3.10 或更新版本**。如果已安装 [Homebrew](https://brew.sh/)，可在终端运行：

   ```bash
   brew install python
   ```

2. 安装 **FFmpeg**，用于合并视频和音频、导出 MP3：

   ```bash
   brew install ffmpeg
   ```

3. 如需下载 YouTube，建议安装 **Node.js 22 或更新版本**，用于 yt-dlp 的 JavaScript 挑战处理：

   ```bash
   brew install node
   ```

4. 在 Finder 中双击 **start.command**。首次运行会创建 `.venv`、安装依赖并打开浏览器；之后访问 **http://127.0.0.1:8765** 即可。

下载期间请保留终端窗口。按 `Control+C` 关闭服务；下次运行仍可查看历史记录。
如果应用已经运行，再次双击 `start.command` 会打开现有页面并正常退出，不会中断下载或启动重复服务。

如果 macOS 首次打开时显示安全提示，请在 Finder 中按住 `Control` 点击 `start.command`，选择“打开”，确认后即可运行。如果文件因传输方式丢失了执行权限，可在项目目录运行 `chmod +x start.command` 修复。

## Windows 启动

1. 安装 **Python 3.10 或更新版本**，安装时勾选 **Add Python to PATH**。
2. 安装 **FFmpeg**，用于合并视频和音频、导出 MP3。可在 PowerShell 中运行：

   ```powershell
   winget install --id Gyan.FFmpeg -e
   ```

3. 如需下载 YouTube，建议安装 **Node.js 22 或更新版本**，用于 yt-dlp 的 JavaScript 挑战处理：

   ```powershell
   winget install --id OpenJS.NodeJS.LTS -e
   ```

4. 安装完成后重新打开终端，双击 **start.bat**。首次运行会创建 `.venv`、安装依赖并打开浏览器。之后访问 **http://127.0.0.1:8765** 即可。

下载期间请保留启动窗口。按 `Ctrl+C` 关闭服务；下次运行仍可查看历史记录。
如果应用已经运行，再次双击 `start.bat` 会打开现有页面并正常退出，不会中断下载或启动重复服务。

## 使用

- 每次粘贴一个 `http://` 或 `https://` 地址，再加入下载队列。
- 在「单个视频」模式下可选择最佳画质、最高 1080p、最高 720p 或 MP3 音频；分辨率是上限，实际结果取决于源视频。
- 在「网页 PDF 合集」模式下粘贴一个网页地址，应用会读取该页面并把它链接到的所有 PDF 一起下载。
- 任务依次执行，界面显示状态、进度、速度与错误原因，可取消或重试任务。
- 下载文件默认保存在项目的 `downloads` 目录；任务记录保存在 `.data/downloads.db`。
- 可在网页设置中修改保存目录；每个任务使用独立子目录，避免同名文件相互覆盖。
- 默认按单个视频处理，粘贴播放列表地址不会批量下载整个列表。

应用仅监听本机地址 `127.0.0.1`，请在当前电脑的浏览器中访问。

## 网页 PDF 批量抓取

在下载卡片顶部切换到「网页 PDF 合集」，粘贴一个网页地址即可。适合会议论文合集、课程资料目录这类一页列满 PDF 链接的页面，例如：

```text
http://staff.ustc.edu.cn/~wyu0725/FPGA/snug_collection/
```

行为说明：

- 只抓取 `.pdf` 结尾的链接（`report.pdf.txt` 这类近似文件名会被忽略），重复链接只下载一次。
- 文件名取自链接本身，会自动做 URL 解码与 Windows 文件名净化；重名时追加 `(2)`、`(3)`。
- 下载内容会先写入 `.part` 临时文件，确认是真正的 PDF 后才改名落盘；取消或失败不会留下半截文件。
- 勾选「同时进入子页面」后，会额外进入同一目录下的子页面（最多 25 个页面，仅跟随同站点、同目录范围的页面链接）。
- 单次任务最多抓取 400 个 PDF；达到上限时任务会给出提示。
- 取消或部分失败后再次点击「重新抓取」，已经下载好的 PDF 会被保留，只补齐缺失的部分。
- 单个 PDF 失败不会中断整个任务，失败的文件名与原因会列在任务卡片上。
- 出于安全考虑，从网页中发现的链接如果指向内网地址（如 `192.168.*`、`169.254.*`）会被跳过；如果粘贴的页面本身就在内网，则不做此限制。

「打开下载目录」按钮可以直接定位到本机的保存位置。

## 启动选项

Linux：

```bash
# 更换端口，不自动打开浏览器
./start.sh --port 8766 --no-browser

# 自定义应用数据目录
DV365_DOWNLOADER_DATA_DIR="$HOME/dv365-downloader-data" ./start.sh
```

macOS：

```bash
# 更换端口，不自动打开浏览器
./start.command --port 8766 --no-browser

# 已安装依赖时直接启动
./.venv/bin/python app.py --port 8765 --no-browser

# 自定义应用数据目录
DV365_DOWNLOADER_DATA_DIR="$HOME/dv365-downloader-data" ./start.command
```

Windows：

```powershell
# 更换端口，不自动打开浏览器
.\start.bat -Port 8766 -NoBrowser

# 已安装依赖时直接启动
.\.venv\Scripts\python.exe app.py --port 8765 --no-browser

# 自定义应用数据目录
$env:DV365_DOWNLOADER_DATA_DIR = 'D:\dv365-downloader-data'
.\start.bat
```

设置 `DV365_DOWNLOADER_DATA_DIR` 后，任务记录保存在该目录的 `downloads.db`，下载文件默认保存在该目录下的 `downloads` 子目录，便于分开保存不同实例的数据。

`start.sh`、`start.command` 和 `start.bat` 会在首次运行、依赖缺失或 `requirements.txt` 变化时安装依赖，平时直接启动；不需要 Node 前端构建。

## 更新与排错

如果出现 `Requested format is not available`，且“下载设置”显示 FFmpeg 未就绪，视频可能没有可直接下载的音画合一格式。请先按上方 Linux 安装说明安装 FFmpeg，再重新下载。FFmpeg 已就绪时仍出现该错误，再检查源视频提供的画质、访问权限和 yt-dlp 版本。

网站规则会变化，下载失败时先更新 yt-dlp：

Linux / macOS：

```bash
./.venv/bin/python -m pip install --upgrade 'yt-dlp[default]'
ffmpeg -version
node --version
```

Windows：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade 'yt-dlp[default]'
ffmpeg -version
node --version
```

如果找不到 FFmpeg，确认已安装、已加入 PATH，并重新打开启动窗口。端口被占用时，Linux/macOS 用 `--port`、Windows 用 `-Port` 指定其他端口（见“启动选项”）；手动访问时也要使用对应端口。登录或访问权限相关错误需要先确认该视频在浏览器中可访问。

## 开发与测试

后端为 Python / Flask，使用 Waitress 提供本机 HTTP 服务；前端为原生 HTML、CSS、JavaScript。

macOS：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python -m unittest discover -s tests -v
```

Windows：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认测试使用隔离的临时目录和测试子进程，不依赖外部视频网站。

下面的集成测试会用 FFmpeg 生成两秒的测试视频，通过本机 HTTP 服务交给真实 yt-dlp 下载，再验证 MP4 和 MP3 文件可读，不需要外网流量：

```powershell
$env:DV365_DOWNLOADER_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

真实网站可用性仍受网络与网站变化影响。参考 [yt-dlp 官方说明](https://github.com/yt-dlp/yt-dlp) 和 [站点列表](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)。
