# 第三方组件与来源

本仓库发布 Skill 指令、配套 Python 脚本、说明和 JSON 示例。官方安装包不捆绑 Python 解释器、FFmpeg 二进制、模型权重、字体、视频素材或个人经验库。

运行时会根据系统与任务使用或下载以下独立项目。它们继续适用各自上游许可；CY 的 LICENSE 不覆盖或替代这些许可。再分发运行环境、模型或二进制时，需单独核对实际版本、构建选项和附带条款。

| 项目 | 用途 | 上游 |
| --- | --- | --- |
| uv / Python | 安装与隔离运行环境 | [uv](https://github.com/astral-sh/uv)、[Python](https://www.python.org/) |
| FFmpeg / imageio-ffmpeg | 媒体处理与工具获取 | [FFmpeg](https://ffmpeg.org/)、[imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) |
| PyAV | 媒体信息读取 | [PyAV](https://github.com/PyAV-Org/PyAV) |
| MLX Whisper / MLX | Apple Silicon 语音识别 | [mlx-examples](https://github.com/ml-explore/mlx-examples/tree/main/whisper)、[MLX](https://github.com/ml-explore/mlx) |
| faster-whisper / CTranslate2 | 语音识别与推理 | [faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[CTranslate2](https://github.com/OpenNMT/CTranslate2) |
| Hugging Face Hub 与所选模型 | 模型获取 | [Hub](https://github.com/huggingface/huggingface_hub)、[Whisper](https://github.com/openai/whisper)、[MLX 模型卡](https://huggingface.co/mlx-community/whisper-large-v3-turbo) |
| Microsoft Visual C++ Redistributable | 部分 Windows 依赖的运行时 | [Microsoft](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) |
| Skills CLI | 可选的 Skill 安装工具 | [vercel-labs/skills](https://github.com/vercel-labs/skills) |

脚本还会复用用户已安装的工具；系统字体由目标软件及系统提供。用户原片、转写与其他素材的权利不因使用本项目而变化。安装包中不附上述工具的完整依赖副本，传递依赖以实际安装环境为准。

FCPXML 的格式参考来自 [Apple Professional Video Applications 文档](https://developer.apple.com/documentation/professional-video-applications)。遵循 XML 字段定义不代表目标软件完整兼容。

许可安排参考了 [dontbesilent2025/dbskill 的公开条款](https://github.com/dontbesilent2025/dbskill/blob/main/LICENSE) 的有限使用思路。本项目未将 DBS 的 Skill、知识库、图书或第三方贡献打入安装包。

如发现应当保留而遗漏的第三方署名或许可，请通过官方仓库 Issues 或微信 chenype 联系作者，提供文件与来源，以便核实和修订。
