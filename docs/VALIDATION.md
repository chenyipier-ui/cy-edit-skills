# 开发者检查

本文件记录仓库可复现的技术检查。面向用户的操作见 [使用指南](USER_GUIDE.md)。

## 自动回归

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

覆盖自动更新的校验与回退、发布镜像的断点重试与冲突保护，以及字幕帧边界、横竖版布局、转写复查缓存、残留锁恢复和运行中任务的写锁保护。

## 合成剪辑流程

具备 Python 3.12、PyAV 16.0.1 与 FFmpeg 后执行：

```bash
python tests/run_checks.py --output /path/outside/repository/checks
```

输出目录必须为空且位于仓库外。检查包括脚本和文档结构、CLI 入口、许可和版本一致性、口播删留计划、长内容多项目工程、复查记录失效和反馈经验更新。指定 `CY_TEST_FCPXML_DTD` 可增加本地 FCPXML 1.10 DTD 校验。

检查使用隔离经验库，在输出目录保留结果及日志。GitHub Actions 同时执行合成流程、自动更新与镜像回归，并在 Windows 上解析 PowerShell 入口和运行剪辑组件回归。

[查看当前检查结果](https://github.com/chenyipier-ui/cy-edit-skills/actions/workflows/checks.yml)
