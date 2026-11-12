# CY 剪辑 Skills

**来自实战团队的内容精剪流程：让 AI 读完素材、判断取舍、剪后复查，再交给剪映继续制作。**

适合用剪映完成客户交付的剪辑团队、工作室和内容创作者。保留原话、原声与内容逻辑，把重复、重录、口癖和多余停顿处理好；长内容还可以重组主线、拆成完整话题。交付一个带可编辑字幕与标题的 **FCPXML 工程文件**，团队接着审片、修改、包装和导出。

**原始素材 → 全文理解与内容精剪 → 剪后复查 → 一个 FCPXML → 剪映继续制作**

当前版本：**2.5.0** · [本次更新](CHANGELOG.md) · [完整使用指南](docs/USER_GUIDE.md)

[GitHub 仓库](https://github.com/chenyipier-ui/cy-edit-skills) · [Gitee 国内仓库](https://gitee.com/chenyipier/cy-edit-skills) · [国内安装包](https://gitee.com/chenyipier/cy-edit-skills/releases/tag/v2.5.0)

## 两个 Skill 怎么选

| Skill | 适合什么内容 | 交给它做什么 |
| --- | --- | --- |
| **单主题口播精剪** `cy-edit-shortvideo` | 同一个人围绕一个主题录的一条或多条口播 | 清理重录、同义重复、密集口癖和多余停顿，默认保留原话、原速、原顺序，剪成一条 |
| **长内容整理与剪辑** `cy-edit-longvideo` | 直播、访谈、对话、课程等多话题长素材 | 通读全文、整理主线，做一条完整精简版，或拆成多个各自讲完整的话题 |

按**内容结构和目标**选择。单主题素材即使很长，也可以用口播精剪；需要重组、多话题拆条或处理访谈时用长内容版。

## 这套流程的重点

- **先理解内容，再决定删什么。** 结合全文区分重复与递进、举例、条件和纠正，保留原意、因果、指代与问答前提。
- **剪完再复查。** 对剪后的实际音频重新转写，检查全文、重复表达与逻辑，回听可疑剪口；返修后重新复查。
- **能接回剪映继续改。** 多条结果保存在同一个 FCPXML 的独立项目里。字幕、内容标题和封面标题参考都是可编辑文字，适合团队返修与商业交付。
- **把意见用于下一轮。** 区分“只改这条片”和“以后都这样剪”；长期偏好可以更新、撤销，两套 Skill 共用经验。
- **保留团队自己的制作方式。** 提供基础字幕、标题和长内容进度条；配乐、音效、转场、调色、花式包装及成品导出在剪映里完成。

需要能访问本地文件、执行脚本的 AI Agent。全文整理与复查有处理成本，适合愿意把时间花在内容质量上的团队；想立即一键出成品时，可选择更直接的工具。[使用条件与工具对比](docs/USER_GUIDE.md)

## 安装两步

**1. 把 Skill 安装到 AI Agent。** 使用 [Skills CLI](https://github.com/vercel-labs/skills)，在项目目录运行：

```bash
npx skills add chenyipier-ui/cy-edit-skills
```

GitHub 访问不便时：

```bash
npx skills add https://gitee.com/chenyipier/cy-edit-skills.git
```

按提示选两套 Skill 和你使用的 Agent。默认装到当前项目，跨项目使用加 `-g`。CLI 需要 Node.js 22.20 或更高版本。也可以从 [GitHub](https://github.com/chenyipier-ui/cy-edit-skills/releases/tag/v2.5.0) 或 [Gitee](https://gitee.com/chenyipier/cy-edit-skills/releases/tag/v2.5.0) 下载 ZIP，让 Agent 安装。

**2. 让 Agent 准备环境并开始剪辑。** 首次使用自动准备独立 Python、媒体工具和识别模型，后续复用。把素材路径和目标直接告诉它：

> 用 cy-edit-shortvideo，把「素材文件夹路径」里同一主题的口播剪成一条。保留原话和自然气口，清理重录、重复和多余停顿，交付可导入剪映的工程。

> 用 cy-edit-longvideo，读完「访谈文件路径」，整理一条完整精简版，并建议哪些话题适合单独切片。重组方向先给我看，确认后完成剪辑和复查。

[详细安装与自动更新](docs/INSTALL.md) · [六步工作流程、版式和返修](docs/USER_GUIDE.md)

## 交付与更新

交付目录只有一个 **FCPXML**，在剪映专业版通过工程导入入口打开。它引用原素材及工作目录中的资源，请一并保留；跨电脑交接时一并转交。导入后继续改字、调整布局、包装和导出。[工程与环境说明](docs/COMPATIBILITY.md)

**2.4.0 及以后安装的用户，下次开始新任务会自动使用新版。** Gitee 优先，GitHub 备用；下载核对后启用，断网继续用本地可用版本。2.3.0 用户先安装新版一次。已经开始的剪辑任务保持原版本。

GitHub 发布新版本后，代码、标签、版本说明、三个 ZIP 和校验清单自动同步 Gitee。[同步进度](https://github.com/chenyipier-ui/cy-edit-skills/actions/workflows/mirror-gitee.yml) · [维护发布说明](docs/PUBLISHING.md)

## 使用许可与联系

**免费使用，可以接单和交付客户，可以修改后自用。** 使用产生的作品无需向本项目署名或分成。

未经书面许可，不得搬运或再分发 Skill、打包进合集或产品、作为赠品提供、出售 Skill 本身、公开发布修改版、移除署名，或用于模型训练与微调。正常把 Skill 作为 AI 的任务指令属于允许的使用。可以分享官方链接；GitHub 平台允许的浏览和 fork 按平台条款处理。

采用自定义有限使用许可，完整条款见 [LICENSE](LICENSE)，第三方内容见 [THIRD_PARTY.md](THIRD_PARTY.md)。

作者：**CY** · 授权咨询：微信 **chenype** · 使用问题：[Issues](https://github.com/chenyipier-ui/cy-edit-skills/issues)

反馈问题时附版本、系统和脱敏日志，保留客户素材、私人逐字稿与个人经验库的隐私。贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。
