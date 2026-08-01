# Rivet 实现状态与验收矩阵

状态：正式 V1 主链已实现  
基线：`PROJECT_DESIGN.md`  
最近验证：2026-08-01

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
pytest: 205 passed, 103 subtests passed
offline eval: 8/8 passed
offline eval benchmark: 10/10 passed, median 1426.196 ms, p95 1559.593 ms
DeepSeek live eval: explain_entrypoint 1/1 passed, 2 model calls, 2 tool executions
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
- Eval 报告记录 Token、费用可用状态、测试尝试、修改范围、权限干预、错误分类和脱敏
  Event 序列；未知费用明确标为 unavailable；
- 与 Fake 基线隔离的四类 live-only 任务种子，以及 17 任务 V1 数据集；
- live 数据集支持完全离线的 Case/Category 清单预览，真实执行必须显式选择 Case、Category
  或确认全量运行；
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

真实任务 V1 已完成 17 个任务的结构、修改边界、初始失败条件和本地参考解验证，尚未调用
Provider。它不构成真实 Bugfix 成功率证据；下一步是确认 Provider、模型、Case 小批次、Fixture
外发内容和预算后执行，指标和授权边界见 [work-plan.md](work-plan.md)。

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
