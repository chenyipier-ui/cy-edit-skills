# 第一次配置与导入

Mac 运行 `sh "Skill目录/scripts/run.sh"`；Windows 运行 `"Skill目录\scripts\run.cmd"`。后者调用 run.ps1，设置 UTF-8，安装失败会停止。Agent 执行，用户无需先装 Python。

入口自动准备 uv 和独立 Python 3.12。默认目录为 Mac `~/.cy-edit/venv`、Windows `%APPDATA%\cy-edit\venv`；CY_EDIT_DATA 可改位置。不会替换系统 Python。

媒体工具优先复用现有 FFmpeg/ffprobe。没有则安装用户目录的 imageio-ffmpeg 与 PyAV，复制自带 FFmpeg 可执行文件，PyAV 负责探测；不要求先安装 Homebrew 或 winget。

转写默认 large-v3-turbo。Apple Silicon 使用 MLX，Intel Mac、Windows x64 使用 faster-whisper；可用 GPU 时尝试，失败回退 CPU。模型首次自动下载，后续缓存复用。`--model small` 是资源紧张时的选择，不暗中降级。已有同名原片 SRT 则直接导入；成片复查仍默认准备模型并重新识别剪后的音频。

下载需要网络与足够磁盘。没有缓存的离线新机器无法凭空配置；安装或系统权限真的阻塞时只说明需要用户完成的那一件事。依赖支持的平台是 macOS 与 Windows x64；Windows ARM 与旧系统另查。

首次下载不承诺固定分钟数；报告当前步骤和实际进度。分析与复查需额外空间，大约每分钟 1.92 MB 的单声道 PCM，加上分块与版本缓存。

导入步骤及工程依赖见 [交付](review-and-delivery.md)。反馈数据独立存放，见 [反馈](feedback.md)。
