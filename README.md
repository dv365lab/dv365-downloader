# dv365-downloader

<img src="static/dv365-logo.png" width="180" alt="DV365 · 芯片验证日记">

基于 **yt-dlp** 的本机视频下载器。浏览器打开 Web 界面，粘贴视频链接，视频保存到当前电脑。

支持范围取决于 yt-dlp、目标网站及访问权限，并非所有网址都能下载。需要登录、受地域限制或带 DRM 的视频可能无法下载；请下载你有权保存的内容。

## 芯片验证日记｜把验证学明白、做扎实

从学习中的疑问，到工作中的难题，与你一起交流芯片验证、积累实践经验。

**微信公众号：芯片验证日记**，欢迎扫码关注，一起学习与成长。

<img src="static/wechat-qrcode.jpg" width="258" height="258" alt="扫码关注微信公众号：芯片验证日记">

**知识星球：7833526**，欢迎加入！一年365天答疑，为您的工作和学习保驾护航。

**全平台认准「芯片验证日记」**：自媒体各平台统一名称、统一 DV365 Logo，方便找到我们。

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

- 每次粘贴一个 `http://` 或 `https://` 视频地址，再加入下载队列。
- 可选择最佳画质、最高 1080p、最高 720p 或 MP3 音频；分辨率是上限，实际结果取决于源视频。
- 任务依次执行，界面显示状态、进度、速度与错误原因，可取消或重试任务。
- 下载文件默认保存在项目的 `downloads` 目录；任务记录保存在 `.data/downloads.db`。
- 可在网页设置中修改保存目录；每个任务使用独立子目录，避免同名文件相互覆盖。
- 默认按单个视频处理，粘贴播放列表地址不会批量下载整个列表。

应用仅监听本机地址 `127.0.0.1`，请在当前电脑的浏览器中访问。

## 启动选项

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

`start.command` 和 `start.bat` 会在首次运行、依赖缺失或 `requirements.txt` 变化时安装依赖，平时直接启动；不需要 Node 前端构建。

## 更新与排错

网站规则会变化，下载失败时先更新 yt-dlp：

macOS：

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

如果找不到 FFmpeg，确认已安装、已加入 PATH，并重新打开启动窗口。端口被占用时用 `-Port` 指定其他端口。登录或访问权限相关错误需要先确认该视频在浏览器中可访问。

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
