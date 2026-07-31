# Rivet 核心模块维护与拆分计划

状态：Active

最近更新：2026-08-01

## 1. 目标

这份计划约束 `RuntimeEngine` 与 `SQLiteStateStore` 的演进。拆分目的在于降低修改风险、
缩小测试定位范围和明确所有权；不以文件变小本身作为成功标准。

每次拆分必须满足：

- 领域状态和 Event Schema 不变；
- SQLite 原子事务和 Lease 语义不变；
- 暂停恢复、Checkpoint、验证和 Reviewer 顺序不变；
- 新模块有直接测试，原有全量测试通过；
- 每次只移动一个相对独立的职责。

## 2. 当前量化

### RuntimeEngine

```text
原始文件：2935 行
RuntimeEngine：35 个方法
```

最大职责块：

| 方法 | 行数 | 职责 |
|---|---:|---|
| `_try_parallel_read_batch` | 287 | 并行只读工具的准备、执行、结果提交与停止判断 |
| `_execute_tool_batch` | 276 | 串行工具批处理与权限暂停 |
| `_run_new_turn` | 243 | Context、模型调用、工具或最终回答路由 |
| `_complete_with_answer` | 234 | 最终回答、验证、Reviewer 与完成提交 |
| `recover_run` | 154 | 崩溃后未完成调用与副作用协调 |

当前类同时拥有：

- Run/Turn 生命周期；
- 模型调用与流式归一化；
- 工具串行、并行和权限恢复；
- Checkpoint、验证与 Reviewer；
- Event 查询和 UI/Context 证据整理；
- Cursor 编解码和领域记录映射。

### SQLiteStateStore

```text
文件：1065 行
SQLiteStateStore：42 个方法
```

职责可分成：

1. 连接、迁移和事务边界；
2. 原子 `StateMutation` 写入与成员关系校验；
3. Workspace/Session/Run/Turn/Call/Execution 等读取；
4. Event 分页与序列；
5. Lease 获取、续期和释放。

Store 拆分时必须共享同一个连接、锁和事务，不能为了文件结构把原子提交拆成多个连接。

## 3. 拆分顺序

| 阶段 | 工作 | 风险 | 验收 | 状态 |
|---|---|---:|---|---|
| R1 | 提取 resume cursor 编解码与 tool-batch cursor 构造 | 低 | 格式、Unicode、非法非对象、身份字段直接测试；Runtime 集成通过 | 完成 |
| R2 | 提取只读 Event 证据投影：changed paths、latest diff、diagnostics | 低 | 相同 Event 输入得到完全相同输出；不接触提交 | 待办 |
| R3 | 提取模型调用执行器：重试、流式事件、usage 和错误归一化 | 中 | Provider 契约与重试/取消测试保持通过 | 待办 |
| R4 | 提取工具批处理协调器，先串行后并行 | 高 | 权限暂停、prepared digest、Checkpoint、重复动作和并行 ordinal 全覆盖 | 待办 |
| R5 | 提取完成流水线：Verifier、Reviewer、最终回答提交 | 中 | 验证失败、Reviewer 阻塞与最终证据测试 | 待办 |
| S1 | 提取纯 Row/Snapshot Mapper | 低 | 所有领域对象序列化往返与损坏数据错误一致 | 待办 |
| S2 | 提取 Lease Repository，共享连接与锁 | 中 | 获取、续期、过期、代际和并发测试 | 待办 |
| S3 | 提取只读 Query Repository | 中 | 分页、排序、RecordNotFound 与线程安全测试 | 待办 |
| S4 | 保留单一 Mutation Writer 作为事务核心 | 高 | 原子提交、revision、membership、Event sequence 与故障回滚 | 待办 |

## 4. R1 已完成：Runtime Cursor

新增 `rivet.runtime.cursor`，拥有：

- `encode_cursor`；
- `decode_cursor`；
- `tool_cursor`。

Engine 只消费 Cursor API，不再定义序列化格式。直接测试覆盖：

- 空 Cursor 回到 `new_turn`；
- Unicode 与对象字段往返；
- 数组等非对象 JSON 被拒绝；
- tool-batch Cursor 保留 Turn、ModelCall、Execution 和 Proposal 身份。

这一步不改变数据库、Event、权限或工具执行逻辑。

## 5. 暂不采用的拆法

- 不按“每 300 行一个文件”机械切割；
- 不把一次 StateMutation 分散到多个 SQLite 连接；
- 不在同一提交中同时重写串行和并行工具执行；
- 不在拆分期间更改 Cursor 或 Event Schema；
- 不用 Mock 全部依赖后声称恢复语义已验证；
- 不先引入 Multi-Agent 来绕过单 Runtime 的维护问题。

## 6. 下一步

优先执行 R2。它只读取 Event 并生成 Context/Reviewer 使用的证据，边界清晰且可以用固定
Event Fixture 直接比较。R2 完成后再评估 R3，Store 从 S1 开始，不直接触碰事务核心。
