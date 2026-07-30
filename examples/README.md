# Rivet 示例

示例目录用于展示 Rivet 面对真实本地仓库任务时的完整工作流。每个示例都应该先复制
到临时目录运行，避免 Agent 的修改污染 Rivet 主仓库。

| 示例 | 目标 | 验证命令 |
|---|---|---|
| [bugfix_task](bugfix_task/README.md) | 修复一个带输入校验的折扣计算错误 | `python pricing_checks.py` |

示例不依赖收费服务以外的额外基础设施；实际 Agent 运行仍需配置一个模型 Provider。
