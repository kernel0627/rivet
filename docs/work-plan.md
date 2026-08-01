# Rivet 后续工作计划

状态：Active
最近更新：2026-08-01

这份文档只维护 Rivet 接下来真正要证明什么、先做什么、怎样验收。
[implementation-status.md](implementation-status.md) 记录已经实现和验证的能力，
[roadmap.md](roadmap.md) 保留长期里程碑。本计划不再用十几个编号把主线拆散。

## 1. 最终目标

> Rivet 是一个从零实现的、本地运行的 Python Coding Agent，重点展示安全执行、任务恢复、
> Checkpoint/Rewind 和可评估性。

最终用户路径应当是：

```text
用户给出真实代码任务
→ Rivet 查看、搜索并理解仓库
→ 提出修改并获得必要权限
→ 修改代码并运行测试
→ 根据失败结果继续修复
→ 完成验证
→ 用户检查 Trace、Checkpoint，并可恢复或回退
```

测试数量、Wheel、PyPI、MCP、Qdrant、更多语言和 Multi-Agent 都是手段或远期扩展，
不能替代这条主链的真实证据。

## 2. 最终必须形成的三类证据

### 2.1 真实代码任务

使用真实 Provider 执行 15～20 个 Python 任务，不使用脚本化 Fake Model 或预设工具轨迹。
任务至少覆盖：

- 3～4 个只读理解任务；
- 4～5 个单文件修改任务；
- 4～5 个跨文件修改任务；
- 4～6 个需要根据失败继续迭代、处理权限暂停或 Patch 冲突的任务。

第一阶段验收目标：

```text
真实执行 15～20 个任务
至少 10 个最终成功
至少 3 个跨文件任务成功
至少 2 个在首次测试失败后继续修复成功
至少 1 个权限暂停后恢复成功
每个失败都有可解释的 Event/Trace
```

每次执行必须记录：

- 最终是否完成、首次测试是否成功；
- 模型调用、工具调用和测试运行次数；
- 输入 Token、输出 Token、耗时和 Provider 返回的费用；
- 修改文件和非预期修改文件；
- 权限恢复次数和是否需要人工干预；
- Provider、模型、失败类别和可复查 Trace 标识。

### 2.2 Runtime 价值对照

在同一批任务上比较极简 Agent 与 Rivet。正常小任务的能力可以接近，重点验证 Rivet 在以下
场景的增量价值：

| 场景 | 极简 Agent | Rivet 要证明的能力 |
|---|---|---|
| 正常 Bugfix | 可以完成 | 可以完成并保留结构化证据 |
| 写权限确认 | 容易丢失上下文 | 原 Run 暂停并恢复 |
| 错误修改 | 依赖手工恢复 | Checkpoint 与 Rewind |
| 进程异常退出 | 状态可能丢失 | SQLite 状态与崩溃恢复 |
| 越界访问 | 依赖模型自律 | Workspace Boundary 拒绝 |
| 失败分析 | 只有文本日志 | Event、Trace 和错误分类 |

最终结论必须来自完成率、Token、耗时、无关修改、安全事件和恢复结果，不能只用架构复杂度
解释价值。

### 2.3 普通用户可用的终端闭环

用户运行：

```bash
rivet chat --workspace /path/to/repo
```

应能看懂任务目标、读取与搜索、待修改 Diff、权限请求、测试结果和继续修复过程，并能停止、
补充要求、查看 Diff/Checkpoint、恢复暂停任务和 Rewind。

## 3. 当前证据基线

截至 2026-08-01：

- 全量离线测试：`194 passed, 10 subtests passed`；
- 固定离线 Eval：`8/8 passed`，这些结果来自脚本化 Fake Model；
- DeepSeek live Eval：仅 `explain_entrypoint 1/1 passed`，Fixture 是一个打印 `hello` 的
  5 行文件，不能代表真实 Bugfix；
- 完整 live Bugfix、跨文件修改、失败后继续修复尚无正式真实 Provider 证据；
- Rivet 仓库检索基线已证明 Sparse Top-5 `5/5`，Hash Dense Top-5 `0/5`，因此 Hash
  Dense 默认关闭；
- GitHub Actions 已在提交 `18b555e` 上验证 Python 3.10/3.12/3.14、Ruff 和 Wheel 全部
  通过；
- Runtime 已具备 Permission、Checkpoint/Rewind、SQLite 状态、暂停恢复、Event、Verifier
  和 Workspace Boundary，但这些能力仍缺同一批真实任务上的对照数据。

这些数字只说明已有底座和最小证据，不能据此声称真实 Coding Agent 已经稳定。

## 4. 第一批：把真实 Coding Agent 做实

这是当前最高优先级，其他批次不能挤占它。

### 4.1 真实任务集

当前状态：进行中。

先完成以下本地工作，再申请一次边界明确的 live 执行授权：

1. 建立与离线 Fake 基线分离的真实任务清单；
2. 为每个任务标注只读、单文件、跨文件或迭代类别；
3. 固定 Fixture、保护文件、验收命令和预期修改范围；
4. 报告中补齐 Token、费用、测试次数、修改文件、首次测试结果和人工干预；
5. 先做数据集结构检查和离线验收，不发送 Fixture、不调用收费 Provider；
6. 获得授权后分小批执行，保存逐任务 JSON 报告和 Trace，不只保留终端文字。

### 4.2 根据真实失败调 Prompt、工具和 Loop

当前状态：等待真实任务数据。

所有改动必须对应已观察到的失败类别：

- 调查不足：调整 System Prompt，要求先读实现、测试和调用者；
- 工具误用：改 Tool Description、Schema 或默认选择；
- 过早完成：加强完成证据和 Stop Policy；
- 重复循环：改重复检测和失败结果回填，达到阈值后暂停。

没有真实失败证据时，不提前增加 Prompt 规则或新工具。

## 5. 第二批：补完整终端使用闭环

当前状态：底座存在，交互证据不足。

按用户价值顺序完成：

1. 把 Event 映射为稳定、可读的 `[Plan]`、`[Read]`、`[Search]`、`[Edit]`、`[Test]`、
   `[Result]`、`[Continue]` 状态；
2. 写入前或写入后立即展示简洁 Diff；
3. 权限选择支持 Allow、Deny、Allow for this run；
4. 支持 Ctrl-C 停止、暂停后继续和补充要求；
5. 在交互中查看 Diff、Checkpoint，并 Rewind 最近一次修改。

完成标准：不阅读内部架构文档的用户可以启动 Rivet、理解过程、批准修改、看到测试结果，
并在必要时回退。

## 6. 第三批：证明 Rivet 的差异化

当前状态：等待第一批形成稳定任务集。

### 6.1 Simple Agent Baseline

实现只包含 Model、`read_file`、`search_text`、`apply_patch`、`run_tests` 和简单循环的基线，
与 Rivet 使用同一任务、同一模型和相同预算，比较完成率、Token、耗时、无关修改、安全、
恢复、Trace 和 Rewind。

### 6.2 模块消融

逐步比较基础 Search/Read、AST、Sparse Retrieval、LSP 和 Reviewer。没有提升成功率、明显
增加 Token/延迟或模型很少正确使用的模块，应默认关闭或删除。

### 6.3 安全与恢复场景

加入提示注入、敏感文件诱导、路径逃逸、外部 Symlink、外部修改冲突、命令超时、Provider
中断、Patch 失败、崩溃恢复和 Rewind 冲突。每个场景记录 Rivet 防什么、状态如何变化以及
用户怎样恢复，不宣称绝对安全。

## 7. 第四批：工程收尾与低风险拆分

当前状态：部分完成，优先级低于真实任务。

- CI、Wheel、干净环境安装、安装后 `rivet --help` 和离线 Eval 继续作为发布回归；
- 只允许 Event 证据投影、模型调用执行器、SQLite Row/Snapshot Mapper 等低风险拆分；
- 工具串并行、Checkpoint 事务、Mutation Writer、Lease 和 Reviewer 完成流水线的大改，
  等真实任务回归集稳定后再做。

## 8. 当前冻结项

在真实任务基线完成前，不开展：

- Multi-Agent 与 A2A；
- Go/TypeScript 与 Tree-sitter 扩展；
- MCP HTTP/SSE；
- 真实 Qdrant；
- 自动多模型路由；
- 更复杂 Reviewer；
- 大规模 Runtime/Store 重写；
- PyPI 发布。

## 9. 当前执行批次

### 2026-08-01：真实任务评估底座

本批目标：让 live 任务在执行前就有明确范围，在执行后能回答成功率之外的问题。

本批工作：

- 重写本计划，确定四批主线和冻结项；
- 扩展 Eval 报告，记录 Token、费用、测试次数、修改文件、首次测试结果和权限干预；
- 增加独立真实任务清单的结构与校验，不给这些任务添加 Fake Model 轨迹；
- 先加入每类至少一个代表性任务，验证选择、物化、验收和报告链路；
- 全量离线测试、固定 Eval、Ruff、Wheel 和 GitHub Actions 继续保持通过。

本批不调用真实 Provider，不产生模型费用，不发送 Fixture。完成后再决定首批 live 任务的数量、
Fixture 内容、Provider、预算上限和外发授权。

当前进展：

- 已加入只读、单文件、跨文件和权限恢复四类 live-only 种子任务；
- live-only Case 禁止携带 Fake Model 轨迹，offline 模式会在执行前拒绝；
- 三个写任务的初始验收命令均已确认失败，避免把本来就通过的 Fixture 计为修复成功；
- Eval 报告已补 Token、费用可用状态、测试次数、首次测试结果、修改文件、非预期修改、
  权限干预、工具失败和不含 Payload 的 Event 序列；未知费用不会伪装成零费用；
- 当前全量回归为 `199 passed, 13 subtests passed`，固定离线 Eval 仍为 `8/8 passed`；
- 尚未调用真实 Provider，正式成功率仍为空。

## 10. 完成与维护规则

每个工作批次只有同时满足以下条件才算完成：

1. 代码或文档边界明确；
2. 相关测试和全量回归通过；
3. 实际结果与失败边界写回本计划和实现状态；
4. Git 提交、推送和远端 CI 分别核对，不能互相代替；
5. live 数据明确记录 Provider、模型、Token、费用和外发范围；
6. 失败任务与失败原因保留，不能只展示成功案例。
