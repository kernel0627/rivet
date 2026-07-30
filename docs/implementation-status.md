# Rivet 实现状态与验收矩阵

状态：正式 V1 主链已实现  
基线：`PROJECT_DESIGN.md`  
最近验证：2026-07-31

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
pytest: 176 passed, 10 subtests passed
offline eval: 3/3 passed
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
- Fake Model 条件驱动和脚本驱动；
- SQLite State 与内容寻址 Artifact；
- Qdrant `upsert/query_points/delete/count` Adapter；
- Python LSP 进程生命周期和协议；
- MCP transport-neutral Tool Adapter 与 Code Intelligence Service Core；
- Prompt Toolkit + Rich TUI。

### 部署环境需要另行验证

- 真实 Provider 凭据、配额和网络；
- 真实 Qdrant Server 的 TLS、认证和容量；
- 本机具体 Python LSP Server 的安装状态；
- 大型仓库上的性能基线；
- 不同操作系统上的终端与进程树行为。

这些边界不会被离线测试冒充为已经完成的线上验证。

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
└── 固定 Eval Fixture → Runtime → 测试 → Completion/Safety

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

以下工作继续遵守当前 Ports，不需要推翻 Runtime：

1. Tree-sitter 和更多语言；
2. MCP SDK 的具体 stdio/SSE/HTTP transport 装配；
3. Cross-Encoder 或 Provider Reranker；
4. 大型固定 Eval 数据集与性能门槛；
5. Multi-Agent 角色与远程 A2A；
6. Provider 多模型路由和成本策略；
7. 平台级安装包、签名和发布流水线。

每个扩展仍需遵守权限、Event、输出预算、取消和错误分类边界。
