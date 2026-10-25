# Jianwei 发布策略

本仓使用“两阶段、双仓库、人工授权”的发布流程。源码仓负责生成和证明
Jianwei 发布物；Marketplace 仓只负责分发索引。两者不得由同一个自动化步骤同时
修改。

## 角色与不可变边界

- `main` 是唯一可发布分支，只能通过 PR、必需离线 QA 和 squash/rebase 更新。
- `vMAJOR.MINOR.PATCH` 必须指向 `main` 上已经通过门禁的提交。发布标签创建后不得
  移动或删除。
- GitHub Release 和附件启用不可变保护。发布错误通过新版本修复，不覆盖旧版本。
- `source-release` 环境是生产发布授权点：必须由指定 reviewer 明确批准，管理员
  不得绕过。
- `JIANWEI_RELEASE_TAG` 是一次性精确发布授权。平时必须不存在或为空；发布窗口只
  能设为本次 `vX.Y.Z`，不能授权其他标签。
- AI 评测、联网验收和本地研究质量证据可以影响人工发布决定，但永远不成为 PR、
  发布、安装或升级的自动门禁。

## 版本规则

- `PATCH`：兼容性修复、数据源修复、文档或安全加固，不改变已有公开使用方式。
- `MINOR`：向后兼容的新 skill、能力或公开接口。
- `MAJOR`：安装方式、配置、命令、skill 契约或发布布局的不兼容变化。

仓内 `jianwei/VERSION` 是 Jianwei 版本的唯一源码。版本、生成的 `plugin/`、清单和
用户文档必须在同一个 PR 中保持一致。不得手工修改 `plugin/`。

## 阶段一：发布候选

1. 在普通开发分支完成源码和测试，重新生成 `plugin/`，提交 PR。
2. 等待 `qa (3.10)` 与 `qa (3.12)` 通过，解决全部讨论，以 squash 或 rebase 合入
   `main`。
3. 在 `main` 上手动运行 `Source Release` workflow。`workflow_dispatch` 只运行发布
   QA 和确定性构建，不发布 GitHub Release。
4. 下载 `jianwei-source-release-<commit>` Actions artifact，核对
   `SHA256SUMS`、`release.json`、产品 ZIP 和 Marketplace candidate。
5. 按需在本地运行宿主验证、联网检查和 AI 质量评测。把结果记入发布记录；这些
   检查是人工判断材料，不改变自动门禁分类。

若候选失败，回到新的 PR 修复。不要创建版本标签。

## 阶段二：生产发布

仅在候选验收完成后执行：

1. 记录待发布的 `main` commit，并确认工作流候选正是该提交。
2. 将仓库变量 `JIANWEI_RELEASE_TAG` 临时设为本次精确 tag，例如 `v0.2.0`。
3. 由发布负责人在该提交创建并推送 annotated tag：

   ```bash
   git tag -a vX.Y.Z <verified-main-commit> -m "Jianwei vX.Y.Z"
   git push origin vX.Y.Z
   ```

4. `Source Release` 再次从标签提交运行完整 release gate 和无第三方依赖的 clean
   build。QA 与 build 都成功后，`publish` job 才会等待 `source-release` 环境审批。
5. reviewer 核对 tag、commit、版本和 artifact 摘要后批准。流水线创建不可变
   GitHub Release，并上传产品 ZIP、`release.json` 与 `SHA256SUMS`；Marketplace
   candidate 不作为 Release 附件。
6. publish job 进入 `source-release` 待审批状态后，先删除或清空
   `JIANWEI_RELEASE_TAG`，再批准环境。无论成功或失败，都确认变量未保留授权值。

环境审批之前可以拒绝或取消发布。标签一旦创建仍不得复用、移动或删除；若标签
后的流水线失败，修复后发布下一个版本号。

## Marketplace 发布

GitHub Release 成功后，才处理 Marketplace：

1. 从同一个 tag workflow 的 Actions artifact 取得
   `jianwei-marketplace-candidate.zip`。
2. 在 `Lanbasara/jianwei-marketplace` 的独立分支展开候选并审阅差异。
3. 运行 Marketplace contract、Claude strict validator 和官方本地 DSH validator。
4. 创建独立 Marketplace PR。确认索引指向同一不可变 tag 及 `plugin/` 子目录后，
   人工合并。

源码发布流程不得直接推送 Marketplace、创建 Marketplace PR 或自动合并。若
Marketplace 验证失败，GitHub Release 保持不变；修复分发元数据后重新走独立 PR，
产品内容需要变化时则发布新的补丁版本。

## 发布记录

每次发布至少保留：版本、`main` commit、tag、Source Release run、产品 ZIP SHA-256、
Marketplace candidate SHA-256、`release.json`、自动门禁结论，以及人工执行的本地/
AI/联网验收结论。不得在记录中保存凭据或原始第三方受限证据。

## Agent 单门禁模式

仓内维护 Skill `.agents/skills/jianwei-release/` 可以代发布负责人执行上述 GitHub
操作。它先完成只读预检并展示包含 PR、完整 commit、版本、tag、Marketplace base 和
全部远程变更的发布卡；负责人只确认这张卡一次。该确认可授权 Agent 随后合并源码
PR、创建 tag、代为批准本次 `source-release` deployment，以及创建并合并 Marketplace
PR。任一身份或保护状态变化都会使确认失效，必须停止并重新预检。

单门禁减少的是重复人工点击，不减少技术门禁：必需 CI、候选构建、环境保护、不可变
Release、Marketplace validator 和分仓 PR 都必须实际通过。维护 Skill 与其记录不得
进入 `plugin/`、产品 ZIP 或 Marketplace candidate。
