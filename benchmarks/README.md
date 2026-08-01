# Rivet 评估数据集

## live_tasks_seed.jsonl

这是第一批真实 Provider 任务的结构种子，与
src/rivet/evaluation/baseline/cases.jsonl 的脚本化离线基线分开维护。

当前包含四类各一个任务：

- 只读失败分析；
- 单文件边界修复；
- 跨文件计算与序列化修复；
- 写权限暂停恢复。

这些 Case 使用 execution_mode: live_only，没有 offline_model。以下命令只做数据集结构
选择并会在执行前拒绝，确保离线 Fake 结果不会混入 live 报告：

    rivet eval --mode offline --dataset benchmarks/live_tasks_seed.jsonl

真实执行会发送对应 Case 的 objective 和 fixture_files 给配置的 Provider，并可能产生费用：

    rivet eval --mode live \
      --dataset benchmarks/live_tasks_seed.jsonl \
      --config-workspace . \
      --output reports/live-seed.json

执行前必须明确确认 Provider、模型、Case 范围、Fixture 外发内容和预算。本文件的存在不代表
已经执行过这些任务。种子任务用于先验证字段、选择、物化、验收和报告链路；扩展到 15～20
个任务后，才进入正式成功率统计。
