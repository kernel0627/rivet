# Rivet 评估数据集

## live_tasks_seed.jsonl

这是第一批真实 Provider 任务的结构种子，与
src/rivet/evaluation/baseline/cases.jsonl 的脚本化离线基线分开维护。

包含四类各一个任务：

- 只读失败分析；
- 单文件边界修复；
- 跨文件计算与序列化修复；
- 写权限暂停恢复。

这些 Case 使用 execution_mode: live_only，没有 offline_model。以下命令只做数据集结构
选择并会在执行前拒绝，确保离线 Fake 结果不会混入 live 报告：

    rivet eval --mode offline --dataset benchmarks/live_tasks_seed.jsonl

这四个任务只用于快速检查字段、选择、物化、验收和报告链路。

## live_tasks_v1.jsonl

这是第一套正式真实任务集，共 17 个任务：

- 4 个只读分析；
- 4 个单文件修复；
- 4 个跨文件修复；
- 5 个迭代与恢复任务，其中一个要求写权限暂停恢复。

所有任务都是 `live_only`，没有 Fake Model 轨迹。13 个写任务均固定了预期修改文件、保护
文件和验收命令，其初始 Fixture 已自动确认验收失败。任务难度分为 introductory、
intermediate 和 advanced。

先在本地列出任务契约；这个命令不会连接 Provider：

    rivet eval \
      --dataset benchmarks/live_tasks_v1.jsonl \
      --list-cases \
      --json

在 live 执行前生成预检报告，同样不会连接 Provider：

    rivet eval --mode live \
      --dataset benchmarks/live_tasks_v1.jsonl \
      --category read_only \
      --config-workspace . \
      --preflight \
      --max-model-calls 5 \
      --max-input-tokens 8000 \
      --max-output-tokens 1024 \
      --output reports/live-v1-read-only-preflight.json \
      --json

预检列出目的地 URL/主机、模型、目标文本、Fixture 路径/字节数/哈希和批次理论 Token
上限，但不会输出 API Key。输入上限使用 Rivet 的 Token 估算器，可能与 Provider 报告值
有差异。只读任务在 Runtime 中强制使用 `workspace_write = deny` 和
`process_execute = deny`，模型工具面同时收窄为文件读取、搜索和 Python 代码理解工具。

真实执行会发送所选 Case 的 objective 和 fixture_files 给配置的 Provider，并可能产生费用。
live 模式必须使用 `--case` 或 `--category` 显式选择任务。首批只读任务可以这样运行：

    rivet eval --mode live \
      --dataset benchmarks/live_tasks_v1.jsonl \
      --category read_only \
      --config-workspace . \
      --output reports/live-v1-read-only-01.json

需要更小的批次时重复传入 `--case`。只有在完整请求数量和费用均经过确认时，才使用
`--all-cases`。缺少 `--case`、`--category` 或 `--all-cases` 的 live 命令会在创建
Provider 执行器前拒绝，避免意外运行整套任务。

执行前必须明确确认 Provider、模型、Case 范围、Fixture 外发内容和预算。本文件的存在不代表
已经执行过这些任务。V1 数据集完成本地结构和初始失败验证后，下一步才是分小批真实执行并
形成正式成功率统计。
