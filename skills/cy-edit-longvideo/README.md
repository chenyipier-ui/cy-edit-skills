# 长内容整理与剪辑

版本：**2.5.0**。作者：CY。

面向直播、访谈、对话、课程等多话题长内容，通读全文后整理一条完整精简版，或重组成多个各自讲完整的话题。

## 安装与使用

将整个 `cy-edit-longvideo` 文件夹放入 Agent 的 skills 目录，保留脚本、references、LICENSE 等文件。新开会话，让支持本地文件与脚本执行的 Agent 读取 SKILL.md：

> 用 cy-edit-longvideo，读完这场访谈，整理一条完整精简版，并建议独立切片。先说明主线和取舍，按确认方向完成剪辑与复查。

首次自动准备运行环境与模型。2.4.0 起，下次开工自动检查并使用官方新版；已经开始的任务保持原版本。

## 交付

交付目录只有一个 FCPXML；多条结果在文件内保留独立项目。字幕、内容标题、前 5 秒封面参考可编辑，超过两分钟的内容带底部进度条和章节，横竖版分别布局。原片与工程引用的工作目录资源都要保留。

在剪映专业版导入后继续审片、修改、包装和导出。Skill 专注内容精剪与复查，不做配乐、音效、转场、调色、花式包装或最终成品导出。两套 Skill 共用反馈经验，长期偏好可更新和撤销。

[GitHub 首页](https://github.com/chenyipier-ui/cy-edit-skills) · [国内仓库](https://gitee.com/chenyipier/cy-edit-skills) · [完整使用指南](https://gitee.com/chenyipier/cy-edit-skills/blob/main/docs/USER_GUIDE.md)

可以免费使用并接单，修改后自用；再分发、打包出售、公开修改版等需书面授权，完整条款见 LICENSE。第三方组件见 THIRD_PARTY.md。授权咨询微信 **chenype**。
