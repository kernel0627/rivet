# Rivet 开发路线

状态：M0–M4 completed; M5 extension baseline in progress

当前实现和验证证据见 [implementation-status.md](implementation-status.md)，具体执行顺序、
授权边界和验收队列见 [work-plan.md](work-plan.md)。

## D0：设计冻结

完成。

目标：先固定边界和验收标准，再重写原型。

交付：

- 总体架构；
- Runtime 状态模型；
- Error Category；
- Model/Tool/Store/Permission 协议；
- 状态存储决策；
- 测试矩阵；
- 内部里程碑和正式 V1 边界。

完成条件：

- Harness、Runtime、Context、Tool、State、Verifier 的职责无重叠；
- Session、Run、Turn、ToolExecution、Event、Checkpoint 定义清楚；
- 状态位置和修改事务达成一致；
- 首批测试场景明确。

## M0：只读内部闭环

完成。

目标：验证 Runtime 核心，不作为正式版本发布。

范围：

- `rivet run` 和 Headless；
- Run/Turn/Event/Decision；
- Model Gateway 协议；
- Fake Model；
- 一个 OpenAI-compatible Adapter；
- Tool Catalog 与 Tool Executor；
- list/read/search；
- Workspace Boundary；
- 外部 Run Store；
- 基础 Context Budget；
- 明确 Error Category 和 Stop Decision；
- 离线测试。

暂不包含：

- TUI；
- 编辑；
- Shell；
- Git 写操作；
- Checkpoint；
- AST/LSP/RAG；
- 正式 Eval。

## M1：可修改、可验证、可恢复的内核

完成。

目标：形成真正的 Coding Agent 主链。

范围：

- Permission Policy；
- Patch/Edit；
- Command/Test；
- Git status/diff；
- Checkpoint/Rewind；
- Run 暂停和恢复；
- 测试反馈 Loop；
- Python AST；
- Working Memory；
- Context 中的 diff 和诊断；
- 命令超时、取消和输出限制。

完成条件：

```text
查看代码
→ 提出修改
→ 获得授权
→ 创建 Checkpoint
→ 修改
→ 测试
→ 根据失败继续
→ 验证通过
→ 可恢复和回退
```

## M2：代码语义与上下文

完成。

范围：

- Python LSP；
- definition/references/hover/diagnostics；
- Context Budget 完整策略；
- Compaction；
- Artifact 引用；
- AST/LSP 结果去重和排序；
- 基础 TUI。

## M3：Code RAG

完成。

范围：

- AST-aware chunk；
- 增量索引；
- Sparse/BM25；
- Dense embedding；
- Qdrant 或本地向量后端；
- RRF 融合；
- Reranker；
- Retrieval Eval；
- 与 Context Engine 集成。

RAG 只负责候选代码检索，不接管 Runtime。

## M4：正式 V1

完成。

正式 V1 汇总 M0 到 M3，并补齐：

- 稳定 CLI/Headless/TUI；
- Session 管理；
- 配置和 provider 管理；
- Trace 查询与导出；
- Retrieval Eval；
- Trajectory Eval；
- Task Completion Eval；
- 固定离线 Fixture、真实 Provider Eval 模式与结构化报告；
- 安全回归测试；
- 安装、文档和示例。

V1 的产品能力：

```text
终端输入任务
→ 检索并理解 Python 仓库
→ 安全修改代码
→ 执行测试和诊断
→ 根据失败继续工作
→ 保存、恢复、回退
→ 给出带验证证据的结果
```

## M5：扩展能力

MCP Tool Adapter、transport-neutral Code Intelligence MCP Service Core 与
Reviewer 基线已实现；具体 transport、更多语言、Tree-sitter、Multi-Agent 和
A2A 继续作为扩展工作。

- MCP Client Tool Adapter（已实现）；
- Code Intelligence MCP Service Core（已实现）；
- MCP stdio/SSE/HTTP transport 装配；
- 更多语言与 Tree-sitter；
- 更完整 LSP；
- Reviewer Agent；
- 多模型路由；
- Multi-Agent；
- A2A；
- 更大规模 Eval。

Reviewer 的第一种形式可以是同进程第二次模型检查：

```text
主 Agent 完成修改
→ Verifier 通过
→ Reviewer 检查遗漏和无关修改
→ 主 Agent 修正或结束
```

远程独立 Agent 出现后再考虑 A2A。
