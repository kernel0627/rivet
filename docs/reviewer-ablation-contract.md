# Reviewer 消融执行合同

状态：本地计量、预算、对照与预检已实现；尚未执行 live
日期：2026-08-04

## 1. 已修复的计量缺口

Reviewer 在 deterministic verification 通过后直接调用同一个 Model Gateway。旧实现只形成
`reviewer.started/completed/failed` Event，不累加 Run Usage，导致预检和结果少算外部请求。

现在 Run 独立持久化 `reviewer_calls`、`reviewer_input_tokens` 和
`reviewer_output_tokens`，Reviewer Token 同时计入 Run 总 Token。成功与无效响应都会把 Usage
和 Provider request ID 写入 Event；调用开始便计数，因此失败也不会被漏掉。Reviewer 不复用
`model_calls` 表的“每 Turn 一个成功 Agent 调用”约束。

## 2. 必须独立记录的指标

每次 Reviewer 调用至少记录：

- Reviewer 请求次数、成功、失败和重试次数；
- 输入 Token、输出 Token、Provider request ID 和费用可用状态；
- 是否批准、Finding 数量、阻断级别和触发的额外 Agent Turn；
- Reviewer 发送的目标、Diff、验证证据字节数；
- Agent 与 Reviewer 合计的外部请求、Token、耗时和费用。

Reviewer 结果仍需保留脱敏摘要和 Finding，不保存 API Key，不在公开报告中复制完整源码 Diff。

## 3. 预算与停止规则

Reviewer 必须使用独立的 `max_reviewer_calls` 上限，不能暗中消耗无限请求。预检分别显示 Agent、
Reviewer 和合计理论上限。Reviewer 返回阻断 Finding 后，额外 Agent Turn 继续受原 Agent 预算
约束；Reviewer 预算耗尽时 Run 应以可解释的暂停原因结束，不能继续调用或伪装为通过。

Reviewer 调用失败、返回无效 JSON 或 Provider 中断时，报告必须区分 Reviewer 不可用与任务
修改失败。是否允许关闭 Reviewer 后恢复同一个 Run，需要写入显式恢复条件和 Event。

## 4. 公平对照

Reviewer off/on 两侧使用同一 Case、模型、Agent 预算、工具 Profile、Fixture 和验收命令。
Reviewer on 侧额外获得固定 Reviewer 预算，并在报告中单独列出这部分成本。至少比较：

- 最终完成率和 Safety incident；
- Reviewer 捕获的真实问题与误报；
- 因 Finding 新增的 Agent Turn 和修复成功率；
- Agent-only 与总 Token、请求、耗时和费用；
- 非预期修改和首次测试失败后的恢复结果。

脚本化离线 Case 只用于验证计数、状态和报告结构。Reviewer 是否提高质量必须来自同一批真实
模型任务，且需要人工复核 Finding 是否正确。

## 5. 实现与验收状态

1. [x] 增加可持久化的 Reviewer 调用与 Token 状态，不复用现有 `model_calls` 唯一约束；
2. [x] 把 Reviewer Usage、错误和 Provider request ID 原子写入状态与 Event；
3. [x] 增加独立 Reviewer 预算和预算耗尽暂停测试；
4. [x] live 预检分别计算 Agent、Reviewer 和合计请求上限，并披露运行后 Reviewer Payload；
5. [x] 增加 Reviewer off/on 同 Case 对比报告；
6. [x] 通过离线脚本化计数回归并生成纯本地 live 预检；
7. [ ] 在既定 Fixture 外发边界内执行真实 Provider 对照，并人工复核 Finding 质量。
