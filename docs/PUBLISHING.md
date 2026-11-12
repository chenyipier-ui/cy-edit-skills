# 维护者：更新与两站同步

GitHub 是维护来源，Gitee 提供国内代码与安装包下载。只在维护者确认发布后对外更新；本地修改、测试和普通提交不会自动生成公开新版本。

## 当前启用状态

**已启用。** GitHub 本仓库已配置专用 `GITEE_TOKEN` Actions secret。2026-11-12 使用现有 v2.3.0 完成一次实际重跑：主分支推送、版本说明更新、标签对象及四个附件的公开回读校验均通过。[实际运行记录](https://github.com/chenyipier-ui/cy-edit-skills/actions/runs/43170165144)

本次重跑复用了已发布且校验一致的附件，没有替换安装包。新建 Release、缺失附件上传、断点重试、内容冲突停止等分支通过 14 项模拟回归；新版本的完整新建与上传还需在下一次正式发版中实际验证。当前 Skill 版本仍为 2.3.0。

## 每次更新怎么做

1. 在本地开发目录继续优化、测试两套 Skill。整理本次改动和已完成的实际验证；不把客户素材、逐字稿、经验库、缓存和开发测试产物加入发布目录。
2. 将确认要发布的内容整理进仓库 `skills/` 下的对应目录。保留每套 Skill 的 README、LICENSE、THIRD_PARTY.md 和独立安装结构。核对差异后，统一更新根目录与两套 Skill 的 `VERSION`、`CHANGELOG.md` 和首页／安装说明中的版本及下载链接。
3. 运行仓库检查和实际素材测试，记录验证范围。修正问题通常递增最后一位（例如 2.3.0 → 2.3.1），功能变化递增中间一位；有不兼容变化再考虑大版本。不覆盖已公开版本的标签或安装包。
4. 提交代码到 GitHub `main`，等 `Release checks` 通过后，在这个通过检查的提交上创建版本标签。用 `tools/build_release.py` 生成三个 ZIP 和 `SHA256SUMS`。
5. **先创建 GitHub 草稿 Release，上传全部四个附件，再发布草稿。** 这样触发自动同步时安装包已齐全。是否继续标为公开测试版，以实际验证结果决定。
6. 发布事件触发 `Mirror release to Gitee`。确认 Actions 成功，并从 Gitee 检查代码、版本说明与附件。工作流会保存四个文件的校验值及两边的提交信息。

日常可以直接让维护助手执行：“两套 Skill 已测试好，请检查改动并发布新版本。”若已有指定版本号或发布说明，一并提供即可。

## 自动同步的内容

- GitHub `main` 在同步开始时的代码，以及本次 Release 对应的标签；旧版本重试也不会让 `main` 倒退。
- Release 名称、说明、公开测试版标记。
- 三个安装 ZIP 与 `SHA256SUMS`，直接复制 GitHub 已上传的原文件。
- 上传后从 Gitee 公开链接重新下载，逐项核对 SHA-256；检查 `main` 和标签对象一致后才报告成功。

普通推送不触发镜像发布，也不会公开本地未提交的文件。只更新本次发布标签，不删除 Gitee 的其他分支、标签或历史版本。主分支只能快进；同名标签或附件内容不同会停止并要求维护者检查，不会强制覆盖。

GitHub 的 `published` 事件包含正式版和公开测试版。仅编辑一个已发布 Release 的说明或后来补充附件，不属于本工作流的自动触发条件；请用下面的重跑方式同步。GitHub Actions 自带的 `GITHUB_TOKEN` 发布 Release 通常不会继续触发另一个工作流；以后若把上游发布也改成 Actions，须显式调用镜像流程。

## 首次授权

1. 在 [Gitee 私人令牌](https://gitee.com/profile/personal_access_tokens) 新建专用令牌，例如 `cy-edit-skills GitHub release mirror`。
2. 选择**仓库级私人令牌 → 选择仓库 → chenyipier/cy-edit-skills**。仅勾选 `projects`；平台强制的基础权限按页面保留。无需开启 Pull Requests、Issues 或 Webhook 管理。按自己的维护周期设置过期时间，到期前更换令牌。
3. 把令牌直接保存到 [GitHub 本仓库 Actions secrets](https://github.com/chenyipier-ui/cy-edit-skills/settings/secrets/actions)，名称为 **`GITEE_TOKEN`**。不要发到聊天、Issues、代码或 Release 说明中。
4. 在 [镜像工作流](https://github.com/chenyipier-ui/cy-edit-skills/actions/workflows/mirror-gitee.yml) 选择 `Run workflow`，填写已发布标签 `v2.3.0`，模式 `sync`。首次实际成功后，再将启用状态记录为已验证。

GitHub 登录状态与 Gitee 浏览器登录不能代替后台工作流授权。这个令牌让本仓库的 GitHub Actions 能向指定 Gitee 仓库推送代码、创建版本和上传附件；工作流只在仓库本身发布或维护者手动触发时使用它，不在外部 PR 测试中使用。

## 重跑与排查

网络中断、授权过期或附件不完整时，GitHub 发布仍然保留，镜像工作流会显示失败。修复原因后可选择 `Re-run failed jobs`，或用 `Run workflow` 指定同一个版本重跑。已经一致的附件会跳过，只上传缺失文件；不会重复生成新版本。

`check` 模式只读取两站、核对 GitHub 安装包和已有 Gitee 文件、显示待同步清单，不写入网站。它不证明令牌有推送和上传权限；启用验收必须使用 `sync`。

```bash
# 在仓库中，只读检查指定的已发布版本
python tools/sync_gitee_release.py --tag v2.3.0 --mode check --report /tmp/gitee-check.json

# 维护者通过已登录的 GitHub CLI 手动补同步
gh workflow run mirror-gitee.yml --repo chenyipier-ui/cy-edit-skills -f tag=v2.3.0 -f mode=sync
```

GitHub CLI 发布示例（须先完成代码审查和测试；目录与版本按当次发布替换）：

```bash
python tools/build_release.py --output /tmp/cy-edit-release-v2.3.1
git tag -a v2.3.1 -m 'CY edit skills 2.3.1'
git push origin main refs/tags/v2.3.1
gh release create v2.3.1 /tmp/cy-edit-release-v2.3.1/*.zip /tmp/cy-edit-release-v2.3.1/SHA256SUMS --draft --prerelease --latest=false --verify-tag --title 'CY 剪辑 Skills 2.3.1 · 公开测试版' --notes-file /tmp/release-notes.md
gh release edit v2.3.1 --draft=false
```

依据：[GitHub Release 触发事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release)、[GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)、[Gitee Release 与附件 API](https://gitee.com/sdk/gitee5j/blob/main/docs/RepositoriesApi.md)。
