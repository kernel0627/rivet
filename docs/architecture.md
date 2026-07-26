# Rivet 总体架构

状态：Implemented  
适用范围：内部 M0 到首个正式 V1

实现细节与当前验收结果见
[implementation-status.md](implementation-status.md)；发生冲突时以仓库根目录的
`PROJECT_DESIGN.md` 为准。

## 1. 产品目标

Rivet 是一个运行在终端里的 Coding Agent。它接收用户任务，能够逐步查看代码、定位问题、修改文件、运行验证，并把每一步保存为可检查、可恢复的运行记录。

核心闭环是：

```text
用户目标
→ 构造本轮上下文
→ 模型给出回答或工具请求
→ 校验权限并执行工具
→ 记录结果
→ 根据新状态继续或停止
```

模型只负责决策和生成。Rivet 负责上下文、工具、安全、状态、恢复和验证。

## 2. 设计原则

1. **Runtime 与具体基础设施解耦。** Runtime 只依赖协议和领域对象，不直接读取文件、调用 HTTP 或操作 Git。
2. **运行事实可追踪。** 模型请求、工具执行、权限决定、文件修改、验证结果和停止原因都形成结构化 Event。
3. **修改必须可恢复。** 写文件前先授权并建立 Checkpoint，写后必须验证 diff 或测试结果。
4. **目标仓库默认保持干净。** Session、Run、Trace 和 Checkpoint 默认保存在仓库外。
5. **单 Agent 主链先稳定。** Reviewer、Multi-Agent 和 A2A 不能成为核心闭环的前置条件。
6. **代码智能围绕 Runtime 提供能力。** ripgrep、AST、LSP 和 RAG 通过工具或服务参与 Context 选择，不进入主循环内部。
7. **内部里程碑与正式版本分开。** M0 可以很小，但不能把 M0 宣称为完整 Coding Agent。

## 3. 组件关系

```text
CLI / TUI / Headless
          │
          ▼
Application Harness
配置、依赖装配、启动、恢复、关闭
          │
          ▼
Runtime Engine
Run / Turn 状态机与停止决策
   ┌──────┼─────────┐
   ▼      ▼         ▼
Model   Context   Tool Executor
Gateway Engine         │
                       ▼
              Permission / Workspace
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   文件与搜索       编辑与命令       代码智能
                                  AST/LSP/RAG
          │
          ▼
Event Stream
          │
   ┌──────┼──────────┬──────────┐
   ▼      ▼          ▼          ▼
Run Store Trace    Checkpoint  Verifier/Eval
```

## 4. 组件职责

### 4.1 Application Harness

负责：

- 读取并校验配置；
- 创建 Model Gateway；
- 创建 Tool Catalog 和 Tool Executor；
- 创建 Permission Policy；
- 创建 Run Store、Event Sink 和 Checkpoint Store；
- 启动新 Run 或恢复已有 Run；
- 关闭网络、进程和持久化资源。

Harness 不负责：

- 逐轮判断；
- 工具参数解析；
- Context 内容选择；
- 文件修改细节；
- 测试通过与否的业务规则。

### 4.2 Runtime Engine

负责：

- 驱动 Run 状态机；
- 启动 Turn；
- 请求 Context；
- 调用 Model Gateway；
- 把 Tool Call 交给 Tool Executor；
- 接收标准化 Tool Result；
- 发出 Event；
- 请求 Stop Policy 给出决策；
- 保存 Snapshot。

Runtime 只依赖抽象协议，不导入具体文件工具或 Provider SDK。

### 4.3 Model Gateway

统一输入：

```text
ModelRequest
├── messages
├── tool schemas
├── model parameters
├── response budget
└── cancellation token
```

统一输出：

```text
ModelResult
├── assistant content
├── tool calls
├── finish reason
├── usage
├── provider metadata
└── model events
```

Provider 的 HTTP、流式协议、重试和错误映射留在 Adapter 内部。

### 4.4 Context Engine

根据 `RunState + ContextPolicy` 生成本轮 `ModelRequest`。

它负责：

- System Prompt；
- 用户原始目标；
- Working Memory；
- 最近 Turn；
- 相关工具结果；
- 当前 diff；
- 验证失败；
- AST/LSP/RAG 片段；
- token 预算和压缩。

它不修改 RunState，也不执行工具。

### 4.5 Tool Catalog 与 Tool Executor

Tool Catalog 负责：

- 工具名称、说明、输入 Schema；
- 工具能力等级；
- 模型可见工具列表。

Tool Executor 负责：

```text
查找工具
→ 校验参数
→ 判断权限
→ 建立超时和取消
→ 执行
→ 限制输出
→ 标准化错误
→ 返回 ToolResult
```

Registry 不直接执行工具。Executor 也不能吞掉所有异常后只返回一段字符串；错误需要保留类别。

### 4.6 Workspace 与 Mutation Transaction

只读能力：

- 工作区边界；
- symlink 逃逸检查；
- 文件读取；
- 文件和文本搜索；
- Git 状态读取。

修改能力采用事务：

```text
Propose
→ Authorize
→ Checkpoint
→ Apply
→ Verify
→ Accept / Rewind
```

任何写操作都必须关联 `run_id`、`turn_id` 和 `checkpoint_id`。

### 4.7 Verifier

Verifier 接收修改结果和验证策略，输出结构化 `VerificationResult`：

```text
status
commands
exit codes
diagnostics
changed files
unexpected changes
evidence
```

测试失败只是观察结果，由 Runtime 交回模型继续处理。Verifier 不自己规划下一步。

### 4.8 Code Intelligence

按能力递进：

```text
ripgrep：精确文本与符号名称
Python AST：函数、类、导入和结构
LSP：定义、引用、类型和诊断
Code RAG：仓库级语义检索
```

这些能力可以作为 Tool，也可以被 Context Engine 调用。两种入口复用同一服务实现。

## 5. 依赖方向

允许：

```text
interfaces → application → runtime protocols
application → adapters
adapters → runtime protocols
runtime → domain models
tools/workspace/state/model → domain models
```

禁止：

```text
runtime → concrete OpenAI adapter
runtime → concrete filesystem tool
domain → CLI
tool implementation → Session Store
Context Engine → Tool Executor
```

## 6. 目标代码结构

```text
src/rivet/
├── interfaces/
│   ├── cli.py
│   └── headless.py
├── application/
│   ├── harness.py
│   └── bootstrap.py
├── runtime/
│   ├── engine.py
│   ├── state.py
│   ├── events.py
│   ├── decisions.py
│   └── stop.py
├── model/
│   ├── protocol.py
│   ├── types.py
│   └── adapters/
├── context/
│   ├── engine.py
│   ├── policy.py
│   ├── budget.py
│   └── compaction.py
├── tools/
│   ├── protocol.py
│   ├── catalog.py
│   ├── executor.py
│   ├── results.py
│   └── builtins/
├── workspace/
│   ├── boundary.py
│   ├── permissions.py
│   ├── checkpoint.py
│   ├── patch.py
│   └── command.py
├── state/
│   ├── protocol.py
│   └── adapters/
├── code_intelligence/
│   ├── search.py
│   ├── python_ast.py
│   ├── lsp.py
│   └── retrieval/
└── verification/
    ├── protocol.py
    └── runner.py
```

这是责任地图，不要求一开始创建所有空文件。只有功能进入对应里程碑时才建立模块。

## 7. 状态位置

推荐默认值：

```text
macOS:
~/Library/Application Support/Rivet/

Linux:
$XDG_STATE_HOME/rivet/
或 ~/.local/state/rivet/
```

目录内部：

```text
rivet.db
checkpoints/
artifacts/
exports/
```

目标仓库可以有可选的 `.rivet/config.toml`，但运行日志和 Checkpoint 不默认写入仓库。

## 8. 首个正式版本的边界

内部 M0 只验证只读闭环。首个正式 V1 至少包含：

- CLI/Headless 和基础 TUI；
- 模型—工具循环；
- Session/Run 恢复；
- 读取、搜索、Patch、命令、Git 和测试工具；
- 权限、Checkpoint 和 Rewind；
- Python AST；
- LSP 基础能力；
- Code RAG 基础混合检索；
- Context Budget 和压缩；
- Trace；
- Retrieval、Trajectory 和 Task Completion 基础 Eval。
