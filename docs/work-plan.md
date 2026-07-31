# Rivet 后续工作台账

状态：Active

最近更新：2026-08-01

这份文档维护“接下来具体做什么”。[roadmap.md](roadmap.md) 继续描述长期里程碑，
[implementation-status.md](implementation-status.md) 记录已经验证的能力；本文件负责把两者
之间的工作拆成可执行任务，并标明证据、前置条件和授权边界。

## 1. 当前基线

- 正式 V1 单 Agent 主链已经实现；
- 全量测试为 `194 passed, 10 subtests passed`，Ruff 通过；
- 固定离线 Eval 为 `8/8 passed`；
- DeepSeek `explain_entrypoint` 最小 live Eval 为 `1/1 passed`；
- 本机 `python-lsp-server 1.14.0` definition 协议验收通过；
- 真实 Qdrant、大型仓库性能、完整 live Bugfix 和远程 CI 仍缺少当前证据；
- 当前开发分支为 `codex/rivet-release-closeout`，正式提交点为 `f27e9ff`。

这些数字只代表最近一次已记录的验证。重新执行后，应同步更新本文件和
[implementation-status.md](implementation-status.md)。

## 2. 决策顺序

```mermaid
flowchart LR
    A["P0 发布证据闭环"] --> B["P1 真实任务质量"]
    B --> C["P2 平台与生态"]
    C --> D["P3 多 Agent 与学习闭环"]
```

工作选择遵守以下规则：

1. 优先补真实证据缺口，再扩展模块数量；
2. 每项工作必须有可执行验收条件，不能用“代码入口存在”代替完成；
3. 默认先做离线、可重复、无外发的工作；
4. 网络请求、模型费用、外部写入、Git 推送和发布必须单独标明；
5. Multi-Agent、A2A 和学习闭环放在单 Agent 质量稳定之后；
6. 大型重构必须由性能、故障率或维护证据驱动。

## 3. 状态定义

- `就绪`：依赖明确，可以直接开始；
- `进行中`：已经有未完成的实现或验证；
- `待授权`：需要模型外发、费用、Git 远程写入或其他外部动作；
- `受阻`：缺少服务、环境或第三方条件；
- `完成`：验收命令、结果和文档均已回填。

## 4. 执行队列

| ID | 优先级 | 工作 | 当前缺口 | 完成条件 | 前置条件与授权 | 状态 |
|---|---:|---|---|---|---|---|
| W-001 | P0 | 保存结构化 Eval 报告 | live Eval 的临时 Run 会随隔离目录删除，终端结果缺少稳定落盘入口 | `rivet eval --output` 原子写入带 Schema 版本的脱敏 JSON；普通和 benchmark 模式均有测试 | 纯本地 | 完成 |
| W-002 | P0 | 完整 DeepSeek Bugfix live Eval | 目前只证明只读解释任务；尚未证明真实模型修改、测试和收敛 | 内置 `fix_discount` Fixture 通过；记录调用、工具、测试、Diff、安全事件和费用可见性 | 需要明确同意发送该固定 Fixture并产生费用 | 待授权 |
| W-003 | P0 | 提交、推送并观察远程 CI | `f27e9ff` 与当前收尾改动尚未形成已验证远程状态 | 形成边界清晰的提交；推送成功；Python 3.10/3.12/3.14、Ruff、wheel 远程检查有实际结果 | 提交和 GitHub 远程写入需用户确认 | 待授权 |
| W-004 | P1 | 中型或大型仓库性能基线 | 已完成 Rivet 中型仓库索引/检索基线；仍缺更大仓库和真实任务/Token 数据 | 继续补大型仓库与任务级耗时；不虚构 Token 成本 | 当前中型基线纯本地；任务级 live 数据需 W-002 授权 | 进行中 |
| W-005 | P1 | 扩充固定 Eval 数据集 | 已覆盖解释、符号定位、调用链、单/跨文件修复、新增测试、权限恢复和逃逸拒绝 | 八类场景均有确定性 Completion/Safety 验收；恢复场景证明写入和 Checkpoint 不重复 | 纯本地 | 完成 |
| W-006 | P1 | 真实 Qdrant 契约验收 | Adapter 只有离线契约，Docker daemon 当前不可用 | 对真实服务完成建库、upsert、query、delete、认证失败和重连 smoke | 需要可用 Qdrant 服务；可能需要启动 Docker | 受阻 |
| W-007 | P1 | Runtime 与 SQLite Store 可维护性拆分 | 已完成量化、拆分序列和 R1 Cursor；Event 证据、模型、工具与 Store 仍待分步处理 | 按 R1–R5、S1–S4 分步拆分，每步直接测试与全量回归通过 | 纯本地；禁止整体重写 | 进行中 |
| W-008 | P2 | MCP stdio/SSE/HTTP transport | 只有 transport-neutral Core 和 Tool Adapter | 至少一种真实 transport 端到端通过，含取消、超时、认证和输出预算 | 可能需要第三方 SDK 或网络 | 就绪 |
| W-009 | P2 | Tree-sitter 与更多语言 | 正式语义能力以 Python 为主 | 先选 Go 或 TypeScript，完成解析、符号、Chunk、检索与固定 Eval | 可能需要新增依赖 | 就绪 |
| W-010 | P2 | 多模型路由与成本策略 | 单一 Provider/模型配置，缺少任务级路由证据 | 路由规则、预算、降级和对照 Eval 可复现；成本数字来自真实 usage | live 对照需要模型费用授权 | 就绪 |
| W-011 | P3 | Multi-Agent 与 A2A | 单 Agent 成功率和成本基线尚不足以证明并发收益 | 隔离工作区、结构化交付、冲突合并、统一验证；与单 Agent 对照后有收益 | W-004/W-005/W-010 提供基线 | 受阻 |

## 5. 当前执行批次

### 2026-08-01：W-001 Eval 报告持久化

目标：让一次昂贵或不可复现的 live Eval 在临时工作区清理后仍保留脱敏结构化证据。

计划变更：

- 为 `rivet eval` 增加显式 `--output PATH`；
- 以 `0600` 权限原子写入 JSON，避免部分文件；
- 普通 Suite 报告补充 `schema_version`；
- 每个 Case 的 metadata 记录实际 Provider 和模型，不记录 API Key；
- 覆盖普通 Eval 与 `--repeat` benchmark 两条 CLI 路径；
- 更新 README、测试策略和实现状态。

验收：

```text
offline Eval 写入报告并与 stdout JSON 一致
benchmark 写入报告并与 stdout JSON 一致
全量 pytest 通过
Ruff 通过
Markdown 与 Git diff 检查通过
```

实际结果：

- `--output` 同时覆盖普通 Suite 和 `--repeat` Benchmark；
- JSON 报告包含 `schema_version: 1`、逐 Case 验收、Provider、模型和脱敏错误分类；
- 离线执行明确标记为 `scripted_fake/scripted_eval`，不会伪装成真实 Provider；
- 报告文件权限验证为 `0600`，内容与 stdout JSON 一致；
- 针对性测试通过；
- 全量结果为 `187 passed, 10 subtests passed`；
- Ruff 与 `git diff --check` 通过。

当前状态：W-001 完成。此次没有重新调用收费 Provider；随后选择 W-005 继续执行，
结果记录在下一批次。

### 2026-08-01：W-005 第一批固定 Eval 扩充

新增场景：

- `locate_invoice_symbol`：先搜索符号，再并行读取定义文件和直接调用者；不产生 Diff；
- `fix_cross_file_total`：同时修复税率换算与金额精度，保护 `invoice_checks.py` 并运行
  确定性检查。

实际结果：

- 两个新增场景均通过 Completion 与 Safety 验收；
- 完整离线基线由 `3/3` 增加为 `5/5`；
- 10 轮五场景 Benchmark 全部通过，median 769.082 ms，P95 817.164 ms；
- 全量结果维持 `187 passed, 10 subtests passed`；
- Ruff 通过；
- 没有调用真实 Provider，也没有外发 Fixture。

当前状态：W-005 进行中。下一批仍需增加调用链解释、新增测试和暂停恢复场景。

### 2026-08-01：W-005 第二批与关闭

新增场景：

- `trace_order_call_chain`：追踪 `post_order → handle_order → save_order` 的三文件调用链；
- `add_slug_regression_test`：创建新测试文件，保护实现文件并执行新增测试；
- `resume_permission_write`：写权限设为 `ask`，真实进入 `PAUSED`，读取 prepared digest，
  再使用原 Run 与 pause token 恢复。

恢复场景验收要求：

- `permission_resumes = 1`；
- `checkpoint_count = 1`；
- `tool_executions = 3`；
- 目标文件修改、保护文件未修改、检查命令通过。

实际结果：

- 完整离线基线为 `8/8 passed`；
- 10 轮八场景 Benchmark 全部通过，median 1426.196 ms，P95 1559.593 ms；
- 全量结果为 `188 passed, 10 subtests passed`；
- Ruff 通过；
- 没有调用真实 Provider，也没有外发 Fixture。

当前状态：W-005 完成。下一项转入 W-004，为当前 Rivet 仓库建立索引与检索性能基线。

### 2026-08-01：W-004 Rivet 仓库索引与检索基线

新增可重复入口 `rivet benchmark-retrieval` 和五条固定查询，测量 167 个 Python 文件、
27,671 行、1,638 个 AST Chunk。

关键结果：

- 冷索引 4272.537 ms，Python 内存峰值 17.326 MiB；
- Warm refresh median 674.990 ms，P95 778.291 ms；
- Sparse Top-5 `5/5`，median 2.212 ms；
- Hash Dense Top-5 `0/5`，median 23.035 ms；
- Hybrid Top-5 `5/5`，median 32.216 ms。

据此将 Hash Dense 默认关闭，Sparse + Lexical Reranker 作为当前可靠默认。详细方法、真值
和边界见 [retrieval-baseline.md](retrieval-baseline.md)。

当前状态：W-004 进行中。中型仓库离线部分完成；更大仓库和任务级 Token/耗时仍待后续。

本批实现增加 3 个回归测试，全量结果更新为 `191 passed, 10 subtests passed`，Ruff 通过。

### 2026-08-01：W-007 量化与 R1 Cursor 拆分

量化确认 `RuntimeEngine` 有 35 个方法，`SQLiteStateStore` 有 42 个方法；工具批处理、
模型调用、完成流水线、Event 证据和事务/Lease 需要分阶段处理。

第一步已将 resume cursor 编解码和 tool-batch cursor 构造提取到
`rivet.runtime.cursor`，并增加空值、Unicode、非法格式和身份字段测试。详细拆分顺序与
禁止事项见 [maintainability-plan.md](maintainability-plan.md)。

当前状态：W-007 进行中；R1 完成，下一步为只读 Event 证据投影 R2。

R1 完成后的全量结果为 `194 passed, 10 subtests passed`，Ruff 与 compileall 通过。

## 6. 暂不开展

- 不在真实任务基线不足时直接扩张 Multi-Agent；
- 不把一次最小 live smoke 宣称为完整生产验证；
- 不为展示效果伪造成功率、Token、费用或大型仓库性能；
- 不在没有职责图和回归切片时整体重写 Runtime 或 Store；
- 不自动推送、发布或扩大模型外发范围。

## 7. 维护方式

每完成一项工作：

1. 更新队列表中的状态；
2. 在“当前执行批次”记录实际变更和验收结果；
3. 同步 [implementation-status.md](implementation-status.md) 的已验证能力；
4. 如果长期顺序改变，同步 [roadmap.md](roadmap.md)；
5. 保留失败、受阻原因和下一次恢复条件；
6. Git 提交、推送和发布状态分别记录，不能相互代替。
