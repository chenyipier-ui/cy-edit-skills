# 安装与首次使用

## 使用 Skills CLI

需要 Node.js 22.20 或更高版本。以下命令遵循 [Skills CLI 官方用法](https://github.com/vercel-labs/skills)。

```bash
# 选择 Skill 和 Agent；默认安装到当前项目
npx skills add chenyipier-ui/cy-edit-skills

# 只装口播精剪
npx skills add chenyipier-ui/cy-edit-skills --skill cy-edit-shortvideo

# 只装长内容版
npx skills add chenyipier-ui/cy-edit-skills --skill cy-edit-longvideo
```

需要所有项目可用时，加 `-g`。请按实际使用的 Agent 选择目标；安装成功不等于该 Agent 已完成真实素材剪辑验证。

GitHub 访问不便时，把来源换成完整的 Gitee URL：

```bash
npx skills add https://gitee.com/chenyipier/cy-edit-skills.git
```

同样可以加 `--skill cy-edit-shortvideo` 或 `--skill cy-edit-longvideo` 只安装其中一套。2026-11-12 已用 Skills CLI 1.8.1 从 Gitee 向隔离 Codex 项目安装两套 Skill，并核对 85 个文件一致。该检查不代表所有网络环境或 Agent 都已测试。

## 下载 ZIP 安装

从 [Gitee 国内下载页](https://gitee.com/chenyipier/cy-edit-skills/releases/tag/v2.4.0) 或 [GitHub 发布页](https://github.com/chenyipier-ui/cy-edit-skills/releases/tag/v2.4.0) 下载：

- `cy-edit-shortvideo-v2.4.0.zip`：口播精剪。
- `cy-edit-longvideo-v2.4.0.zip`：长内容整理与剪辑。
- `cy-edit-skills-v2.4.0.zip`：两套 Skill 合集。

把解压得到的 `cy-edit-shortvideo` 或 `cy-edit-longvideo` 文件夹放进 Agent 的 skills 目录，确保 `SKILL.md` 位于该文件夹第一层。保留完整目录，包括脚本、references、LICENSE 和 THIRD_PARTY.md。目录位置按 Agent 的安装说明选择；也可以让 Agent 按本机配置完成安装。

发布页的 `SHA256SUMS` 提供文件校验值。macOS 可用 `shasum -a 256 文件.zip`，Windows PowerShell 可用 `Get-FileHash 文件.zip -Algorithm SHA256`，与清单逐项比较。

Gitee 的三个 ZIP 和校验清单已通过未登录下载核对，与 GitHub v2.4.0 附件逐字节一致；下载实际来自 Gitee 文件服务，无需跳转 GitHub。后续新 Release 由发布工作流同步代码与附件，并重新下载核验；导入 Git 仓库本身不会自动复制 Release 附件。[同步状态](https://github.com/chenyipier-ui/cy-edit-skills/actions/workflows/mirror-gitee.yml)

## 运行环境

用户提供素材和要求，Agent 负责运行命令。两套 Skill 的入口不同：

| Skill | macOS | Windows |
| --- | --- | --- |
| 口播精剪 | `sh "Skill目录/scripts/run.sh"` | `"Skill目录\scripts\run.cmd"` |
| 长内容版 | `sh "Skill目录/run.sh"` | `"Skill目录\run.cmd"` |

首次缺依赖时自动准备专用 Python 3.12 环境和媒体工具。默认转写使用 large-v3-turbo 系列模型；Apple Silicon 可走 MLX，其他目标环境走 faster-whisper。具体入口、回退和离线接口见两套 Skill 自带的环境说明：

- [口播环境](../skills/cy-edit-shortvideo/references/import-and-environment.md)
- [长内容环境](../skills/cy-edit-longvideo/references/environment.md)

首次下载需要网络和磁盘空间，模型与长素材分析还会占用额外空间。已提供 SRT 可以减少源素材转写，但成片复查仍可能需要识别模型。不能把“已下载代码”视为“所有运行依赖已具备”。

代码提供本地媒体处理与语音识别路线；素材文稿是否发送给云端模型取决于你所使用的 Agent 和配置。本仓库不替代 Agent 的数据处理设置。

## 经验与更新

两套运行环境分别保存，两套 Skill 的经验库共享：macOS 默认在 `~/.cy-edit/shared/experience/`，Windows 默认在 `%APPDATA%\cy-edit\shared\experience\`。可用 `CY_EDIT_EXPERIENCE_DIR` 指定位置。不要把个人经验库提交到公开仓库。

更新前保留自己修改过的 Skill 副本，避免覆盖私人改动。项目工作目录、原片、生成的工程依赖和经验库均与本仓库分开保存。新版本以 GitHub Release 和标签为准；当前 2.4.0 是公开测试版。

## 自动更新（2.4.0 起）

首次升级到 2.4.0 后，AI 助手每次开工通过 `update-root` 自动检查正式发布的 Release（包括当前公开测试版），优先从 Gitee 下载，GitHub 备用。校验通过后自动使用新版本，无需确认。直接调用 editor.py 也有自动更新入口，普通命令的联网检查缓存一小时；`--help` 和 `--offline` 不联网。

新版保存在 `~/.cy-edit/updates/`，安装目录仍作为启动入口，所以 Skills CLI 的安装版本可能仍显示原版本；实际任务使用 update-root 返回的版本。不会覆盖原 Skill 的自定义内容，但执行的是官方新版；修改自用者可设 `CY_EDIT_AUTO_UPDATE=0` 固定原版。旧版本缓存保留，断网、权限或校验失败继续使用可用版本。已启动的进程保持原版本，Agent 在本次任务中固定解析出的目录。

2.3.0 没有更新器，需要先运行 `npx skills update cy-edit-shortvideo cy-edit-longvideo`（全局安装加 `-g`），或下载新版 ZIP 替换 Skill 目录一次。更新后新开会话使用。
