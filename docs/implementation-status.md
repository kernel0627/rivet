# Rivet 实现状态与验收矩阵

状态：正式 V1 主链已实现  
基线：`PROJECT_DESIGN.md`  
最近验证：2026-08-03

## 1. 当前结论

Rivet 已形成可运行的单 Agent Coding Runtime。正式入口具备：

```text
任务
→ Context
→ 模型流
→ 多工具
→ 权限
→ Checkpoint
→ 修改
→ 测试与 diff 验证
→ Reviewer（可选）
→ 完成 / 暂停 / 恢复 / 取消 / Rewind
```

默认测试完全离线。本次验收：

```text
pytest: 246 passed, 103 subtests passed
offline eval: 8/8 passed
offline Rivet/Simple comparison: 8/8 passed on both scripted trajectories
offline tool profiles: basic/ast/sparse/lsp each 8/8 passed on scripted trajectories
offline Reviewer comparison: off/on both 8/8 passed; on adds 4 scripted calls
offline eval benchmark: 10/10 passed, median 1426.196 ms, p95 1559.593 ms
DeepSeek live eval: explain_entrypoint 1/1 passed, 2 model calls, 2 tool executions
DeepSeek live read-only V1: 4/4 passed, 12 model calls, 16 tool executions
ruff: all checks passed
```

## 2. 里程碑矩阵

| 里程碑 | 状态 | 已验证交付 |
|---|---|---|
| D0 | 完成 | 总设计、领域不变量、Ports、安全规则、验收清单 |
| M0 | 完成 | SQLite State、Event/Snapshot、Fake/OpenAI Gateway、Context、只读工具、CLI/Headless、暂停恢复 |
| M1 | 完成 | Permission、Patch、Command/Test、Git、Checkpoint/Rewind、Verifier、AST、取消与崩溃协调 |
| M2 | 完成 | LSP、Context Budget、Compaction、Artifact、TUI、流式 Event |
| M3 | 完成 | AST Chunk、SQLite Sparse、Dense、Qdrant、RRF、Reranker、增量索引、Retrieval Eval |
| M4 | 完成 | Session 多 Run、Chat、Trace 查询、固定离线/真实 Provider Eval 入口、GitHub Actions、可运行示例、安装与贡献说明、安全回归 |
| M5 基线 | 部分完成 | MCP Tool Adapter、Code Intelligence Service Core、Reviewer；具体 MCP transport、更多语言、Tree-sitter、Multi-Agent、A2A 保持扩展项 |

M5 在设计中属于后续扩展，不是正式 V1 的发布阻塞项。

## 3. Runtime 不变量实现

| 不变量 | 实现位置 | 验证 |
|---|---|---|
| 一个 Run 一个写入者 | SQLite lease + Runtime owner | lease contract tests |
| 一个非终态 Turn | SQLite partial unique index | state contract tests |
| 状态与 Event 原子提交 | `StateMutation` | revision/event tests |
| 写前权限与 Checkpoint | ToolExecutor preflight | executor/runtime tests |
| 权限绑定 prepared digest | PermissionDecision validation | executor tests |
| Execution Grant 一次消费 | grant digest + consumed set | executor tests |
| 副作用不确定停止后续写 | Runtime pause policy | recovery tests |
| 恢复不重放已完成写入 | persisted ToolExecution + cursor | runtime tests |
| 并行结果保持 ordinal | parallel read batch | integration test |
| 终态完成有正式回答 | Run domain invariant | domain/runtime tests |
| PAUSED 有 token/cursor/条件 | Run domain invariant | runtime tests |
| Trace 失败不改变事实 | best-effort Event publisher | event tests |

## 4. Adapter 与产品边界

### 已实现

- DeepSeek/OpenAI Provider Profile 与 OpenAI Chat Completions 完整响应、流式 Adapter；
- DeepSeek V4 thinking `reasoning_content` 多轮回传与 Provider token 参数差异；
- 本机 DeepSeek 固定只读 Fixture 的真实 API 两轮调用与工具闭环；
- Eval Suite/Benchmark 的带版本脱敏 JSON 报告落盘入口；
- 四工具 Simple Agent 基线、`--agent rivet|simple|both` 选择，以及同 Case 汇总/逐项差异报告；
- `--tool-profile basic|ast|sparse|lsp|all` 工具消融入口；四档逐级暴露工具，Sparse 档真正
  启用本地索引，同 Case 报告包含逐档汇总、逐项结果和精确工具面；
- Reviewer 独立调用预算、调用/Token 持久化、Provider request ID Event、预算耗尽暂停，
  以及 `--reviewer off|on|both` 同 Case 对照和 Reviewer-aware live 预检；
- Eval 报告记录 Token、费用可用状态、测试尝试、修改范围、权限干预、错误分类和脱敏
  Event 序列；未知费用明确标为 unavailable；
- 与 Fake 基线隔离的四类 live-only 任务种子，以及 17 任务 V1 数据集；
- live 数据集支持完全离线的 Case/Category 清单预览，真实执行必须显式选择 Case、Category
  或确认全量运行；
- live 预检报告目的地 URL/主机、模型、Fixture 哈希/字节数和理论 Token 上限，支持按本次
  Eval 收紧模型调用和输入/输出 Token；只读任务强制拒绝工作区写入和进程执行；
- 只读 Eval 仅向模型暴露文件读取、搜索和 Python 代码理解工具；预检拒绝可形成合法的
  终态 ToolExecution，报告记录回答长度/哈希及缺失验收片段；
- 真实 Rivet 仓库的可重复索引/检索基准 CLI 与固定查询集；
- Fake Model 条件驱动和脚本驱动；
- SQLite State 与内容寻址 Artifact；
- Qdrant `upsert/query_points/delete/count` Adapter；
- Python LSP 进程生命周期和协议；
- Python LSP 真实子进程/stdio 生命周期契约；
- 本机 `python-lsp-server 1.14.0` definition 协议验收；
- MCP transport-neutral Tool Adapter 与 Code Intelligence Service Core；
- Prompt Toolkit + Rich TUI。
- Runtime resume cursor 已提取为独立纯数据模块并有直接格式测试；

### 部署环境需要另行验证

- 其他 Provider、模型版本、凭据、配额和部署网络；
- 真实 Qdrant Server 的 TLS、认证和容量；
- 其他部署环境的 Python LSP Server 安装与插件差异；
- 大型仓库上的性能基线；
- 不同操作系统上的终端与进程树行为。

这些边界不会被离线测试冒充为已经完成的线上验证。

### 本机真实 Provider 验收

2026-08-01 使用配置中的 DeepSeek Provider 和 `deepseek-v4-flash`，只发送内置
`explain_entrypoint` 任务提示与一个打印 `hello` 的 5 行 Python Fixture。结果为
`1/1 passed`，Run 状态 `COMPLETED`，共 2 轮模型调用、2 次工具执行，无模型错误、
Completion blocker 或 Safety incident，耗时 5591.455 ms。仓库源码未发送。

Eval 会把 Fixture、SQLite 状态和 Event 放入同一个临时隔离目录并在结束后删除，
因此这项证据来自命令返回的结构化结果，不声称具备事后 `inspect` 或 Trace 回放能力。
它证明了当前配置下的最小真实 Provider 工具闭环，不代表完整 Bugfix、配额压力或其他
部署环境已经验收。

固定 Eval 的本地性能快照见
[performance-baseline.md](performance-baseline.md)。它用于发现 Runtime 基础设施回退，
不替代真实 Provider 和大型仓库基线。

当前 Rivet 仓库的 167 个 Python 文件、1,638 个 AST Chunk 和 Sparse/Dense/Hybrid
测量见 [retrieval-baseline.md](retrieval-baseline.md)。该基线显示 Hash Dense Top-5 为
`0/5`，因此 Dense 默认关闭；这项结果不替代真实 Embedding 和大型多语言仓库验收。

真实任务 V1 已完成 17 个任务的结构、修改边界、初始失败条件和本地参考解验证。2026-08-03
使用 DeepSeek 完成首批 4 个只读任务，结果为 `4/4 passed`：全部 Run 为 `COMPLETED`，合计
12 次模型调用、16 次只读工具执行、25563 输入 Token 和 4929 输出 Token；修改文件、安全
事件、模型错误和工具失败均为 0。Provider 未报告 USD 费用，因此费用为 unavailable。

2026-08-04 完成首个单文件任务 `live_fix_inventory_boundary`。首次执行暴露长模型流期间
60 秒 lease 过期，Runtime 已增加运行期 heartbeat，并用短 TTL 慢流测试覆盖。修复后的正式
结果为 `1/1 passed`：Run 为 `COMPLETED`，5 次模型调用、6 次工具执行、17576 输入 Token、
1002 输出 Token；仅修改 `inventory.py`，验收测试首次通过，1 个 Checkpoint，非预期修改、
模型错误、工具失败和安全事件均为 0。失败 Eval 现在也会保留 Provider 请求数、Event、工具、
Checkpoint 和 changed files 摘要。下一步扩展其余单文件任务，脱敏证据见
[reports](../reports/README.md)。

其余 3 个单文件任务已完成纯本地预检：外发候选范围为 523 字节目标文本与 1667 字节固定
Fixture，只允许分别修改 `slug.py`、`windows.py`、`batching.py`，每任务最多 7 次模型调用。
预检没有启动外部请求；V1 reference solution 回归已确认每个任务可在允许文件范围内完成。

同日完成上述 3 个任务的真实执行与复核，最终为 `3/3 passed`：合计 17 次模型调用、20 次
工具执行、4 次测试、62595 输入 Token、3269 输出 Token 和 3 个 Checkpoint；每项只修改
各自允许的生产文件，首次测试均通过，非预期修改、安全事件、模型错误和工具失败均为 0。
真实结果推动修复了最终摘要评分语义、常见测试缓存过滤，以及“成功 WRITE 掩盖其他非预期
路径”的范围漏洞。Provider 未报告费用，仍记为 unavailable。

跨文件阶段的 4 个任务已完成真实执行与复测，最终为 `4/4 passed`。首次批次为 `2/4`：
Order 在只分析未修改后过早结束；Status 在先跑失败测试后，被 Runtime 错误当成不确定副作用
而暂停；Pagination 同一 Run 内经历一次可恢复的 Provider 传输错误后完成。失败测试现在记为
命令已经完整执行的 `APPLIED` 结果，失败输出会回填给模型继续修复；写任务 System Prompt 也
明确禁止只分析后收尾。

最终成功证据合计 22 次模型调用、32 次工具执行、4 次测试、93247 输入 Token、5907 输出
Token 和 4 个 Checkpoint；非预期修改、安全事件和工具失败均为 0，包含 1 次已恢复的模型
传输错误。计入首次失败尝试后，整个批次共使用 28 次模型调用、112728 输入 Token 和 8071
输出 Token；Order 与 Status 各用满 8 次总预算。Provider 未报告费用，仍记为 unavailable。

剩余 5 个迭代与权限恢复任务已完成纯本地预检：候选外发范围为 1143 字节目标文本和 4790
字节固定 Fixture，每任务最多 10 次模型调用；其中 `live_resume_settings_write` 强制写权限
暂停后恢复，其余任务要求根据测试反馈继续修复。预检现在显式记录每个 Case 的
`automatic_resume_permissions`，不会只显示 `workspace_write = ask` 而隐藏自动恢复行为。预检的
`external_request_started` 为 `false`，目前没有该批次的 Provider 请求、Token、费用或成功率。

## 5. 测试覆盖

```text
unit
├── domain / context / config
├── tool contracts / executor / patch
├── AST / LSP / retrieval / Qdrant payload
├── verifier / reviewer / MCP / eval
└── redaction / event stream

contract
├── SQLite transaction / revision / lease / artifact
├── OpenAI adapter
├── LSP stdio subprocess
└── Qdrant adapter

integration
├── direct answer / tool observation
├── parallel safe reads
├── permission pause and resume
├── write / verify / reviewer
├── provider and budget pause
├── crash reconciliation
├── Session multi-Run context
├── Checkpoint conflict and Rewind
└── 八类固定 Eval Fixture → Runtime → 暂停/恢复 → 测试 → Completion/Safety

security
├── workspace escape / symlink
└── search option injection

e2e
├── CLI doctor / tools / packaged offline Eval
├── `python -m rivet` 子进程入口
├── TUI 权限暂停 / 确认 / 恢复 / 修改 / 验证
└── Headless schema and configuration failure
```

## 6. 后续扩展工作

可执行优先级、授权边界和逐项验收条件统一维护在
[work-plan.md](work-plan.md)。

以下工作继续遵守当前 Ports，不需要推翻 Runtime：

1. Tree-sitter 和更多语言；
2. MCP SDK 的具体 stdio/SSE/HTTP transport 装配；
3. Cross-Encoder 或 Provider Reranker；
4. 大型固定 Eval 数据集与性能门槛；
5. Multi-Agent 角色与远程 A2A；
6. Provider 多模型路由和成本策略；
7. 平台级安装包、签名和发布流水线。

每个扩展仍需遵守权限、Event、输出预算、取消和错误分类边界。
