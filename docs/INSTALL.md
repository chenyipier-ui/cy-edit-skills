# 安装与自动更新

当前版本：**2.5.0**。先安装 Skill，再由 Agent 准备运行环境。[团队使用指南](USER_GUIDE.md)

## 1. 安装到 AI Agent

已有支持 Skills、能读写本地文件并执行命令的 Agent，可以把官方仓库链接交给它，让它安装两套 Skill。也可以使用 [Skills CLI](https://github.com/vercel-labs/skills)，需要 Node.js 22.20 或更高版本：

```bash
# GitHub 来源，按提示选择 Skill 和 Agent
npx skills add chenyipier-ui/cy-edit-skills

# 国内来源，两条来源命令选一条即可
npx skills add https://gitee.com/chenyipier/cy-edit-skills.git
```

默认安装到当前项目；需要跨项目使用时加 `-g`。只安装其中一套时，加 `--skill cy-edit-shortvideo` 或 `--skill cy-edit-longvideo`。安装后新开会话。

## ZIP 安装

从 [Gitee 国内下载页](https://gitee.com/chenyipier/cy-edit-skills/releases/tag/v2.5.0) 或 [GitHub 发布页](https://github.com/chenyipier-ui/cy-edit-skills/releases/tag/v2.5.0) 下载：

| 文件 | 内容 |
| --- | --- |
| `cy-edit-shortvideo-v2.5.0.zip` | 单主题口播精剪 |
| `cy-edit-longvideo-v2.5.0.zip` | 长内容整理与剪辑 |
| `cy-edit-skills-v2.5.0.zip` | 两套 Skill 合集 |
| `SHA256SUMS` | 三个 ZIP 的校验值 |

解压后，将 `cy-edit-shortvideo`、`cy-edit-longvideo` 文件夹放进 Agent 的 skills 目录，确保 `SKILL.md` 在该文件夹第一层。也可以让 Agent 按本机配置完成安装。保留完整目录，不要只复制 SKILL.md。

macOS 用 `shasum -a 256 文件.zip`、Windows PowerShell 用 `Get-FileHash 文件.zip -Algorithm SHA256` 核对校验值。两边安装包由同一次构建产生；Gitee 使用其国内文件服务提供附件。

## 2. 准备环境并开始使用

把素材路径和目标交给 Agent，由它读取对应 Skill、准备环境并执行。常用说法：

> 用 cy-edit-shortvideo，精剪这个文件夹里的单主题口播，保留原话，交付可导入剪映的工程。

> 用 cy-edit-longvideo，通读这场直播，整理一条完整精简版，并挑出适合独立拆条的话题。

首次运行会准备专用 Python 3.12、媒体依赖和语音识别模型。无需先手动安装 Python、Homebrew 或系统级媒体软件。运行入口如下，日常由 Agent 调用：

| Skill | macOS | Windows |
| --- | --- | --- |
| 口播精剪 | `sh "Skill目录/scripts/run.sh"` | `"Skill目录\scripts\run.cmd"` |
| 长内容版 | `sh "Skill目录/run.sh"` | `"Skill目录\run.cmd"` |

默认使用 large-v3-turbo 系列识别模型；Apple Silicon 使用 MLX，Intel Mac 与 Windows x64 使用 faster-whisper，按可用环境选择 GPU 或 CPU。首次下载需要网络与磁盘空间，后续复用缓存。现成原片 SRT 可以用于导入转写，剪后复查再处理实际剪后的音频。

长内容版在官方依赖源连接失败时尝试国内下载源，也尊重用户已设置的下载地址。运行细节见 [口播环境](../skills/cy-edit-shortvideo/references/import-and-environment.md) 和 [长内容环境](../skills/cy-edit-longvideo/references/environment.md)。

## 自动更新（2.4.0 起）

**已经装过 2.4.0 或更新版本，无需重复下载安装。** AI 助手每次开工通过 `update-root` 检查官方已发布 Release，优先 Gitee，GitHub 备用。下载核对 SHA-256 后使用新版说明和脚本，无需再确认。已经启动的任务固定使用开工时的版本。

新版放在独立缓存 `~/.cy-edit/updates/`。原安装目录仍是入口，所以 Skills CLI 可能显示最初安装的版本；实际任务版本以 `update-root` 返回目录的 VERSION 为准。素材、项目、模型和经验库不受更新影响；旧版缓存保留，断网或校验失败继续使用可用版本。

直接调用脚本也会检查更新，普通命令的联网检查缓存一小时；`--help`、`--offline` 不联网。修改自用者可以设置 `CY_EDIT_AUTO_UPDATE=0` 固定自己的版本。

**2.3.0 用户需要先升级一次：**

```bash
npx skills update cy-edit-shortvideo cy-edit-longvideo
```

全局安装加 `-g`；也可以下载新版 ZIP 替换 Skill 目录。保留自己修改过的副本，升级后新开会话。

## 项目与经验放在哪里

原片、项目工作目录、FCPXML 引用资源都要保留。两套运行环境分别管理，经验库共享：macOS 默认 `~/.cy-edit/shared/experience/`，Windows 默认 `%APPDATA%\cy-edit\shared\experience\`。`CY_EDIT_EXPERIENCE_DIR` 可指定共享数据根目录。

个人经验与客户素材不放进公开仓库。媒体与语音识别在本地处理；文稿是否发送给云端模型取决于使用的 Agent 和配置。

GitHub 发布后自动同步 Gitee 的代码与附件；维护者可查看 [同步工作流](https://github.com/chenyipier-ui/cy-edit-skills/actions/workflows/mirror-gitee.yml)。
