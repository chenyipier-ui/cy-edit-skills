# 最小计划

文件由 Agent 写，用户不填写。复制 prepare 的素材路径；只填有用信息，不填商业目的等无关表格。

```json
{
  "schema_version": 1,
  "kind": "jianying_multisource_plan",
  "project": {"name": "口播_v1"},
  "sources": {"A": {"source_meta":"A/source_meta.json", "transcript":"A/transcript.json"}},
  "content_title": ["先理清主线", "再删除重复"],
  "cover_title": ["剪短一点", "就讲清楚了吗"],
  "chapters": [],
  "segments": [{"source":"A", "evidence_unit_ids":["u000001","u000002"], "reason":"完整表达主线，删掉后面的同义复述"}]
}
```

| 字段 | 填法 |
| --- | --- |
| project.name | 文件与工程名称；其余目的字段可选 |
| sources | prepare 模板中的路径，按计划文件位置解析 |
| evidence_unit_ids | 实际审阅包里的保留原文编号，按原顺序 |
| reason | 这段为何保留、删了哪些冗余 |
| source_start_ms / source_end_ms | 可省略，脚本按原文与邻句算；确有精细依据才手填 |
| id / order / role / transcript | 可省略，自动补齐；需要章节定位时给 id |
| cut | 可选 `[[开始毫秒,结束毫秒]]`，须真实词边界或已回听非语音区间 |
| keep_pauses | true 保留该段有意停顿 |
| needs_confirmation / confirm_note | 真有未解决问题才填；不编造“已核实” |
| content_title / cover_title | 各两行，根据最终保留文稿写 |
| chapters | 超两分钟需给章节标题与 start；短片可 [] |

先 `validate-multi --plan` 可只检查，不生成交付。`compile-multi` 也自动做相同检查，不需重复跑命令。文字事实有矛盾时不要只靠脚本通过。

字幕错字放计划同目录的 `字幕校正.json`：`{"替换":{"错字":"正字"}}`。仅更正识别错误，不改说话者意思。完整示例见 [examples/plan.json](../examples/plan.json)。
