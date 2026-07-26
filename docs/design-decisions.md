# Rivet 设计决策

状态：Accepted and implemented

## 已有强共识

### 1. Clean-room 独立实现

Rivet 可以研究 MiniCode-Python 的行为、入口、工具类型和经验，但不复制其源码或目录结构。目标是实现同类产品能力，并重新划分职责。

### 2. 单 Agent 主循环优先

M0 到 M3 先完成单 Agent 闭环。Reviewer、Multi-Agent 和 A2A 不作为前置条件。

### 3. RAG 和 LSP 属于正式 V1

开发时可以在 Runtime 之后逐步加入。正式 V1 需要包含可用的 LSP 和 Code RAG 基线。

### 4. Trace 从 M0 开始

M0 记录结构化 Event 和基本指标。完整 Eval 后续建立，Runtime 不为 Eval 反复重写。

## 已确认

### 5. 状态默认保存在目标仓库外

结论：已采用。

原因：

- 只读检查不污染仓库；
- 多 workspace 状态可统一管理；
- Trace 和 Checkpoint 不进入用户 Git；
- CLI 查询和清理更方便。

### 6. SQLite 作为主状态存储

结论：已采用。

原因：

- 标准库可用；
- 支持事务和 schema migration；
- Run、Turn、Event、ToolExecution 查询方便；
- 崩溃恢复强于多个 JSON 文件。

Checkpoint blob 和大工具输出仍使用 artifact 文件。

### 7. M0 只保留 Run 与 Headless

结论：已采用。

当前 `chat` 每个输入都会新建 Session，语义不成立。真正实现 Session 多 Run 和恢复后再加入 Chat/TUI。

### 8. Runtime 使用规范化错误分类

结论：已采用。

Provider、Tool、Workspace、Store、Checkpoint 和 Verification 错误分别建模，不能统一落入 `MODEL_ERROR`。

### 9. Runtime Engine 使用 async 边界

结论：已采用。

原因：

- 模型需要流式响应和取消；
- 多个只读工具可以并发；
- 命令和 LSP 都是长生命周期异步资源；
- TUI 需要持续接收 Event。

纯状态转换、Stop Policy 和参数规范化仍保持同步纯函数。普通 CLI 使用 `asyncio.run()` 驱动 Runtime。

## 已落地选择

### 10. Provider Transport

候选：

1. 官方 Provider SDK；
2. `httpx` 自建统一 Transport；
3. 标准库 `urllib`。

推荐：

- Core 只定义 Model Gateway 协议；
- OpenAI Adapter 初期使用官方 SDK或 `httpx`；
- `urllib` 原型不作为长期实现；
- Provider 特有字段留在 Adapter 内部。

结论：Core 使用 Model Gateway；OpenAI Adapter 使用官方 SDK，Fake Model 负责默认
离线测试。

### 11. TUI 技术

候选：

1. `prompt_toolkit + Rich`；
2. Textual；
3. 自己维护 raw mode 和 ANSI；
4. 复用已有 TUI 源码。

结论：

- CLI/Headless 使用 argparse；
- TUI 使用 `prompt_toolkit + Rich`；
- 暂不自行维护跨平台 raw terminal；
- clean-room 原则下不直接复制 MiniCode-Python TUI。

### 12. Tool Schema 校验

候选：

1. JSON Schema 库；
2. Pydantic 模型；
3. 手写轻量校验。

结论：内置工具使用 Pydantic 参数模型并导出 JSON Schema。MCP 保留远端原始
Schema，同时在 Adapter 边界执行基础对象、必填项、类型、枚举和
`additionalProperties` 校验。
