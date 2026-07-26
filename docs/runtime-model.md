# Rivet Runtime 状态模型

状态：Implemented

本文解释领域语义。当前字段、枚举和序列化不变量以 `src/rivet/domain/` 为准，
完整运行流程以根目录 `PROJECT_DESIGN.md` 为准。

## 1. 核心实体

### Session

表示用户可持续交互的一段会话。

```text
Session
├── session_id
├── workspace_id
├── created_at
├── active_run_id
└── run_ids
```

一个 Session 可以包含多个 Run。

### Run

表示一次明确任务，例如“修复登录测试失败”。

```text
Run
├── run_id
├── session_id
├── task
├── workspace
├── status
├── turn_index
├── budget
├── working_memory
├── pending_approval
├── stop_reason
└── final_answer
```

建议状态：

```text
CREATED
RUNNING
PAUSED
RECOVERING
COMPLETED
FAILED
CANCELLED
```

### Turn

表示模型的一次决策周期。

```text
Turn
├── turn_id
├── run_id
├── index
├── context_snapshot_id
├── model_call_id
├── tool_execution_ids
├── started_at
└── completed_at
```

一次 Turn 可以返回零个、一个或多个 Tool Call。

### ModelCall

保存模型请求和响应的结构化摘要：

```text
provider
model
request id
finish reason
usage
latency
tool calls
error category
```

密钥和敏感 Header 永远不能写入 Event 或 Trace。

一次逻辑 Turn 可以包含多次 ModelCall 尝试，例如首次请求遭遇 429 后重试。每次尝试单独记录，同一 Turn 最多只能有一个成功的 ModelCall。

```text
CREATED
IN_FLIGHT
SUCCEEDED
FAILED
INTERRUPTED
CANCELLED
```

流式响应中断时，尚未形成完整合法结构的 Tool Call 不能执行。

### ToolExecution

```text
ToolExecution
├── execution_id
├── run_id
├── turn_id
├── call_id
├── tool_name
├── arguments
├── capability
├── permission_decision
├── status
├── result
├── error
└── duration
```

状态：

```text
PROPOSED
PREPARED
WAITING_PERMISSION
READY
RUNNING
SUCCEEDED
FAILED
TIMED_OUT
CANCELLED
INTERRUPTED
```

ToolExecution 还需要记录：

```text
effect_class: READ / WRITE / EXECUTE / NETWORK
normalized_arguments
prepared_digest
workspace_revision_before
workspace_revision_after
side_effect_state
retry_of
```

执行采用两阶段接口：

```text
prepare
→ authorization
→ checkpoint
→ execute
```

用户授权必须绑定 `prepared_digest`。真正执行时的工具版本、规范化参数和目标路径必须与被批准内容一致。

### Event

Event 记录已经发生的事实。至少包含：

```text
event_id
event_type
session_id
run_id
turn_id
sequence
timestamp
payload
schema_version
```

Event 采用 append-only，同时维护 Run Snapshot 作为快速投影。状态更新和 Event 追加必须在同一个 State Store transaction 中提交。Trace 是 Event 的消费者，不能成为 Runtime 的事实来源。

### Checkpoint

```text
Checkpoint
├── checkpoint_id
├── run_id
├── turn_id
├── workspace fingerprint
├── affected paths
├── before blobs
├── after hashes
├── status
└── created_at
```

Checkpoint 必须能判断工作区是否已经被外部修改，防止错误回滚覆盖用户的新内容。

## 2. Runtime 输入与输出

Runtime Engine 不直接接受原始 Provider JSON。它接收规范化对象：

```text
start_run(task)
resume_run(run_id)
submit_user_input(run_id, input)
cancel_run(run_id)
```

每次 `step` 输出一个 `RuntimeDecision`：

```text
REQUEST_MODEL
EXECUTE_TOOLS
REQUEST_PERMISSION
VERIFY_CHANGES
WAIT_FOR_USER
CONTINUE
FINISH
FAIL
CANCEL
```

Runtime 面向 Harness 的正式接口采用 async：

```python
class RuntimeEngine(Protocol):
    async def start_run(self, command: StartRun) -> RunSnapshot: ...
    async def drive(self, run_id: str) -> RunOutcome: ...
    async def resume_run(self, command: ResumeRun) -> RunOutcome: ...
    async def cancel_run(self, command: CancelRun) -> RunSnapshot: ...
    async def recover_run(self, run_id: str) -> RunSnapshot: ...
```

普通 CLI 通过 `asyncio.run()` 调用。async 边界用于流式模型响应、工具并行、进程取消和 TUI 更新，纯状态转换和 Stop Policy 仍保持同步纯函数。

## 3. 主状态转换

```text
CREATED
→ RUNNING

RUNNING
→ REQUEST_MODEL
→ EXECUTE_TOOLS
→ RUNNING

RUNNING
→ WAITING_FOR_USER
→ RUNNING

RUNNING
→ VERIFYING
→ RUNNING / COMPLETED / FAILED

RUNNING
→ COMPLETED
RUNNING
→ FAILED
RUNNING
→ CANCELLED

RECOVERING
→ RUNNING / WAITING_FOR_USER / FAILED
```

## 4. 单轮流程

```text
1. 从 RunStore 读取 Run Snapshot
2. Stop Policy 做 turn 前判断
3. Context Engine 生成 Context Snapshot
4. 记录 ModelRequested
5. Model Gateway 返回 ModelResult
6. 记录 ModelCompleted 或 ModelFailed
7. 若无 Tool Call，评估是否完成
8. 若有 Tool Call，先生成 ToolExecution
9. Tool Executor 做校验、权限和执行
10. 记录每个 Tool Result
11. 更新 Working Memory 和预算
12. 保存 Run Snapshot
13. Stop Policy 给出下一步决策
```

重复调用检测必须发生在执行前。检测依据不能只看“历史累计次数”，还要考虑：

- 是否连续重复；
- 工作区或 Context 是否发生变化；
- 上一次结果是否相同；
- 工具是否天然允许轮询；
- 用户是否明确要求重复验证。

多个工具请求中，声明可并行且属于 `READ` 的工具可以并行。`WRITE` 和 `EXECUTE` 默认按模型给出的顺序串行。进入下一轮 Context 时，结果仍按原始调用顺序排列。

## 5. 错误分类

至少区分：

```text
MODEL_TRANSPORT_ERROR
MODEL_PROTOCOL_ERROR
MODEL_RATE_LIMIT
TOOL_NOT_FOUND
TOOL_ARGUMENT_ERROR
TOOL_PERMISSION_DENIED
TOOL_EXECUTION_ERROR
TOOL_TIMEOUT
WORKSPACE_VIOLATION
STORE_ERROR
CHECKPOINT_ERROR
VERIFICATION_FAILED
CONTEXT_OVERFLOW
USER_CANCELLED
INTERNAL_ERROR
```

所有异常不能统一映射成 `MODEL_ERROR`。

## 6. 暂停与恢复

在以下情况下 Run 进入 `WAITING_FOR_USER`：

- 写入或高风险命令需要确认；
- 任务存在会显著改变结果的歧义；
- 需要用户提供凭据或外部信息；
- 工作区状态和 Checkpoint 冲突。

恢复时：

```text
加载 Run Snapshot
→ 校验 workspace identity
→ 校验待处理审批
→ 校验 Checkpoint 和文件 hash
→ 追加 UserInputReceived
→ 继续 Runtime
```

恢复不能重新执行已经成功完成的 ToolExecution。

权限等待与用户澄清需要区分：

- 等待工具权限：当前 Turn 保持等待，授权后继续同一 Turn；
- 模型请求用户澄清：当前 Turn 正常结束，Run 暂停，用户回复后开启新 Turn。

## 7. 修改事务

```text
PatchProposed
→ PermissionRequested
→ PermissionGranted
→ CheckpointCreated
→ PatchApplied
→ VerificationStarted
→ VerificationCompleted
→ ChangesAccepted
```

失败路径：

```text
PatchApplyFailed
→ 保留 Checkpoint
→ 返回结构化错误

VerificationFailed
→ 继续下一 Turn
或用户选择 Rewind

RewindRequested
→ 检查当前文件 hash
→ RewindApplied / RewindConflict
```

## 8. 持久化建议

推荐 SQLite 作为 Run、Turn、Event 和 ToolExecution 的主要存储：

- Python 标准库自带；
- 事务和崩溃恢复强于多个独立 JSON 文件；
- 后续查询 Trace 和 Eval 更容易；
- 可以同时保存 Snapshot 与 append-only Event。

大文件、Checkpoint blob 和完整工具输出放入内容寻址的 artifact 目录，SQLite 只保存 hash、路径和摘要。

这一选择仍需在实现前正式确认。

## 9. Runtime 不变量

1. 一个 Run 同时只有一个 Runtime 写入者。
2. 一个 Run 同时最多有一个非终态 Turn。
3. 一个 Turn 最多有一个成功 ModelCall。
4. 终态 Run 不能原地恢复为 RUNNING。
5. 状态转换和对应 Event 原子提交。
6. 外部副作用前先持久化执行开始事实。
7. 写操作执行前已有授权和有效 Checkpoint。
8. 权限批准绑定 prepared digest。
9. 工作区边界在 prepare 和 execute 前分别检查。
10. 副作用状态不确定时，后续写操作全部暂停。
11. COMPLETED Run 必须存在正式最终回答和 COMPLETE 决策。
12. PAUSED Run 必须保存恢复要求和防重复使用的 pause token。

## 10. 崩溃恢复

```text
Context 构造中断：允许重新构造
ModelCall 中断：记录 INTERRUPTED，新建一次调用尝试
只读工具中断：按幂等策略重试
写工具中断：副作用未知，暂停并核对 Checkpoint
状态提交失败：停止继续产生副作用
Checkpoint 损坏：暂停或失败，禁止静默覆盖
```

只有明确声明为幂等、且工作区修订能够确认的动作才允许自动重试。
