# 单主题口播精剪

版本：**2.5.0**。作者：CY。

同一个人围绕一个主题录制的一条或多条口播，默认保留原话、原速与原顺序，清理重录、同义重复、密集口癖和多余停顿。

## 安装与使用

将整个 `cy-edit-shortvideo` 文件夹放入 Agent 的 skills 目录，保留脚本、references、LICENSE 等文件。新开会话，让支持本地文件与脚本执行的 Agent 读取 SKILL.md：

> 用 cy-edit-shortvideo，把这个文件夹里的同主题口播精剪成一条，保留原话和自然气口，交付可导入剪映的 FCPXML。

首次自动准备运行环境与模型。2.4.0 起，下次开工自动检查并使用官方新版；已经开始的任务保持原版本。

## 交付

交付目录只有一个 FCPXML；多条结果在文件内保留独立项目。字幕、内容标题、前 5 秒封面参考可编辑，超过两分钟的内容带底部进度条和章节，横竖版分别布局。原片与工程引用的工作目录资源都要保留。

在剪映专业版导入后继续审片、修改、包装和导出。Skill 专注内容精剪与复查，不做配乐、音效、转场、调色、花式包装或最终成品导出。两套 Skill 共用反馈经验，长期偏好可更新和撤销。

[GitHub 首页](https://github.com/chenyipier-ui/cy-edit-skills) · [国内仓库](https://gitee.com/chenyipier/cy-edit-skills) · [完整使用指南](https://gitee.com/chenyipier/cy-edit-skills/blob/main/docs/USER_GUIDE.md)

可以免费使用并接单，修改后自用；再分发、打包出售、公开修改版等需书面授权，完整条款见 LICENSE。第三方组件见 THIRD_PARTY.md。授权咨询微信 **chenype**。
