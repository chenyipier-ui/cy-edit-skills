# 输入示例

全部为虚构示例。真实编号从最终阅读稿获取，不能把临时稿编号、示例范围或“已核实”的文字直接抄进项目。

| 文件 | 填什么 |
|---|---|
| brief.json | Agent 从用户已有要求推断主线与目的，可省略；不是开工问卷 |
| sources.json | 原片路径和编号；多音轨时明确选择 |
| transcripts.json | 来源编号对应原片 SRT；已有稿时跳过本地识别 |
| proposal.json | 单条方案：五项内容判断和标题章节，不填哈希 |
| proposal-multi.json | 多条独立话题，逐条完整 |
| proposal-with-cuts.json | 段内删减与重要停顿保护；时间必须来自实际证据 |
| corrections.json | 错字、依据；再次 build 自动复用匹配的修正 |
| feedback.json | 本轮原话和分项意见，日常只需一条 feedback |
| experience-decisions.json | 对具体经验采用、跳过或待判断；采用写 application |

候选五项：title 是标题；thesis 是一句话主线；unit_ids 是按成片顺序保留的编号；excluded 解释删了什么；reason 解释为什么这样组织。还要填写 content_title、cover_title（各两行）。超过两分钟填写 chapters；start 指向保留的段或原文编号。样例已给出这些字段。

顶层 reviewed_sources 是实际读完的来源编号，excluded_sources 是整份排除原因；不得由“转写结束”自动等同“Agent 已读完”。audience、purpose 可沿用 brief，也可在方案显式写入。

plan.json 仅展示脚本输出格式，包含虚构签名，不能直接导出。计划由 build 生成；回听状态默认 false，真实听过才登记。改变机器输出应回到方案或字幕修正表，不手改签名骗过检查。

turn.json、adoption.json、feedback_advanced.json 和 selection.json 仅用于理解旧底层接口，日常不要求填写。它们不表示另有四个必跑步骤。低层反馈的十项字段由日常 feedback 自动补默认；长期偏好仍须明确授权与稳定偏好键。

跨项目偏好例子：只改本片用 project；“以后都这样”才用 user，并附 authorization、preference_key。更新／撤销必须从 experience 取真实 target_ids；保存成功和效果验证分开。

成片复查输入不用从零编写：编译会生成 review-decisions-template.json。逐项完成后复制填写，再运行 review。每个疑点都写理由；未听过就保持 not_done。
