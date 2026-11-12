# 输入字段

| 文件 | 必填内容 | 自动生成或可省略 |
| --- | --- | --- |
| 方案 | candidates；reviewed_sources；每候选 title/thesis/unit_ids/excluded/reason；两种标题与章节 | mode、候选 id、估时、来源哈希；audience/purpose 可根据素材推断 |
| 剪辑计划 | build 生成，不手写 | 原片秒数、项目帧数、字幕、版本签名 |
| 本轮反馈 | message；items 内 text，范围默认 project | 本地消息号、登记表、MD、保存回执 |
| 经验采用 | decisions 的真实 experience_id、decision、reason；adopt 时 application | 其他新增项保持 pending；快照自动绑定 |
| 成片复查 | 当前 signature、read_all、五项 checks、每对疑点 keep/revise+reason | 模板由编译输出；旧签名失效 |

标题与章节写法见 [包装](packaging.md)。候选的 cuts 可写 `unit_id,start,end,reason`，秒数必须在该句内且为真实词边界；keep_pause_units 保护重要停顿。只有 SRT 时整句取舍，不靠“已核实”布尔值伪造细剪证据。

反馈 scope 使用 project/user/candidate。跨项目偏好必须有用户长期授权原话；更新、撤销需 target_ids 和 authorization。具体示例见 [反馈](feedback.md)。

每种示例在 [examples](../examples/README.md)。示例编号只用于说明，必须替换为当前项目实际返回值；用户无须手填。
