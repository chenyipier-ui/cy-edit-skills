# 首次配置和运行速度

前提是用户已有能运行 Skill 的 Agent 宿主。日常统一用 run.sh / run.cmd；入口在用户目录准备 uv、Python 3.12、隔离环境、PyAV 与 FFmpeg，不依赖系统 Python 或让用户手工找 PATH。

本地识别首次才下载模型。新配置默认 large-v3-turbo：Apple Silicon 使用 MLX；Intel Mac 和 Windows 使用 faster-whisper。有效旧配置继续复用，显式 --model 可切换。prepare 会自动从媒体环境补到所需识别环境。

Windows --device auto 尝试可用的 CUDA；有显卡不等于驱动和推理库齐全。模型加载或推理失败时回退 CPU 并写明原因。不会静默安装系统级显卡驱动。CPU 是无显卡时的正常路线；用户也可选择 small，或用剪映识别后导出的原片 SRT。不要承诺某模型在所有中文素材都更准。

准备阶段的字幕导入路线用 --backend none，不下载识别模型；build 的成片复查仍默认配置模型，用户明确跳过时才用 --skip-recheck。剪映操作能力与版本权限要按实际情况处理，不把“提供导入 SRT 路线”说成已自动完成剪映识别。

Mac 数据目录默认 `~/Library/Application Support/cy-edit-longvideo/`，Windows 默认 `%LOCALAPPDATA%\cy-edit-longvideo\`。--state-dir 可指定。模型与依赖放运行数据目录；个人经验放两套 Skill 共用的经验目录（见 feedback.md）；素材分析、方案和交付放项目目录。不同用户用各自数据目录。

--offline 放在命令前；无缓存的首次离线配置不能凭空完成。网络、磁盘、权限不足时返回具体状态，保留下载与项目检查点。Windows VC++ 安装如触发 UAC，由用户在系统提示中处理。

目标平台为 macOS Apple Silicon / Intel 与 Windows 10/11 x64；特殊旧系统、原生 Windows ARM 需要另行适配。FCPXML 生成不依赖安装 Final Cut Pro。

## 速度怎么说

默认不转码、不反复全读原片，音频在项目本地复用。读取首帧、文件头和首尾样本仍有 NAS 开销。首轮模型下载、素材长度、缓存、硬件、编码与网络都会改变总耗时。不能把某次缓存命中的结果推广成固定 10–15 分钟。

prepare 记录实际准备耗时和分块进度；首次依赖下载另行记录。每块完成可以先读临时稿，最后仍须全片审阅。抽音频完成后，声图测量与识别任务可并行；不是声图还没取到声音就已经完成。

可选转码不在默认 prepare 链里，独立运行。先说明问题与资源成本，无实测速度时明确用时未知。

## 实现资料

- [uv 安装](https://docs.astral.sh/uv/getting-started/installation/) 与 [托管 Python](https://docs.astral.sh/uv/guides/install-python/)。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 与 [CTranslate2 安装](https://opennmt.net/CTranslate2/installation)。GPU 依赖随实际版本检查，不只凭显卡型号判断。
- [PyAV VideoFrame](https://github.com/PyAV-Org/PyAV/blob/master/av/video/frame.py)：读取首帧方向信息，不能代替导入后的画面检查。
- [Apple asset](https://developer.apple.com/documentation/professional-video-applications/asset) 与 [音频声道来源](https://developer.apple.com/documentation/professional-video-applications/audio-channel-source)：媒体与声道字段的含义，目标软件仍需实际核对。
- [Apple text-style](https://developer.apple.com/documentation/professional-video-applications/text-style)：文字样式表达，不能证明其他编辑器完整支持。

安装完成、实际识别完成、识别正确、工程生成与编辑器导入成功分别记录。
