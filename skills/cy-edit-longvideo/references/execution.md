# 执行命令

Mac：`sh "Skill目录/run.sh"`；Windows：`"Skill目录\run.cmd"`。下列 RUN 由该入口替换。所有 JSON 由 Agent 写，用户只自然语言表达。

```text
RUN prepare --project "项目" --name "长内容优化" --media "素材目录"
RUN propose --project "项目" --input "方案.json"
RUN approve --project "项目" --selected main --reply-file "用户原话.txt"
RUN build --project "项目" --version v1
RUN review --records "项目/records/v1/main" --input "复查结论.json"
RUN feedback --project "项目" --input "本轮反馈.json"
```

多条：approve 的 selected 用逗号分隔候选编号，如 topic-01,topic-02。全部输出进入 `deliveries/v1/剪辑工程.fcpxml`；内部报告在 `records/v1/候选编号/成片复查.md`。工程的多条内容仍是独立项目，不串在同一主轨上。

已有原片 SRT：`RUN --backend none prepare --project "项目" --name "项目名" --media "素材目录" --transcripts "时间稿清单.json"`。清单和输入示例见 [examples](../examples/README.md)。模型切换用 prepare/build 的 --model；后端 --backend 放命令前。

build 默认需要实际成片再转写。只有用户明确要求跳过时加 --skip-recheck，报告保持未完成复查。生成待审工程与最终内容/声音确认不是同一状态。

反馈与经验采用：prepare 自动读取一次；新反馈、跨会话恢复或下一版生成前执行 `RUN experience --project "项目"` 刷新，实际选择用 --decisions-file 保存。每轮反馈一条命令，详见 [反馈](feedback.md)。

计划时间、字幕和来源身份由脚本生成。不要手改机械计划或补造质量开关。要改剪法，改方案原文编号/段内删减后重新 propose；沿用真实已有授权范围，新的结构取舍才再问用户。

其他恢复命令按需使用：packets、label-speaker、verify-sources、relink、conform、review-plan。默认流程不要求用户学这些命令。
