# Eval 证据

本目录保存需要在任务工作区清理后继续复查的脱敏结构化报告。报告不得包含 API Key、`.env`
内容或当前 Rivet 仓库源码。

## 2026-08-04：Reviewer 计量与 off/on 对照

`offline-reviewer-comparison.json` 在 8 个固定 Case 上比较 Reviewer off/on。两侧均为
`8/8 passed`、28 次 Agent 模型调用和 23 次工具执行；on 侧对 4 个写任务各增加 1 次脚本化
Reviewer 调用，外部请求计数由 28 增至 32，并增加 8 个 Reviewer Event。Reviewer Token 为
0，因为 Fake Model 不生成 Usage。

离线 Reviewer 固定返回批准，只验证独立调用计数、预算、状态和报告结构，不能证明 Reviewer
能发现真实问题。`live-v1-iterative-reviewer-preflight.json` 为 5 个迭代任务生成 off/on
边界：共 10 个 Variant-Case，Agent 最多 100 次调用、Reviewer 最多 10 次，合计外部请求上限
110 次；两侧合计目标文本 2286 字节、Fixture 9580 字节。Reviewer 还会在运行后看到候选回答、
修改路径、Diff 和验证证据，其字节数在预检时未知并已明确披露。该报告未启动外部请求。

## 2026-08-04：离线工具消融合同与 live 预检

`offline-tool-ablation.json` 在同一组 8 个固定 Case 上依次运行 `basic`、`ast`、`sparse` 和
`lsp` 四档。每档均为 `8/8 passed`、28 次模型调用、23 次工具执行，且报告逐 Case 保存实际
模型可见工具名。Sparse/LSP 档各比前两档多 4 个索引刷新 Event，这是本地索引启用证据。

四档复用同一套预编排 Fake Model 轨迹，模型不会根据新增工具重新选择行动，所以这份报告只
证明 Profile 隔离、Fixture、预算和评分合同可比，不能证明新增模块有效。耗时差异同样只是
本机离线基础设施数据。

`live-v1-iterative-tool-ablation-preflight.json` 为 5 个迭代任务计算四档 live 边界：共 20 个
Profile-Case 执行，理论最多 200 次模型调用；四档合计目标文本 4572 字节、Fixture 19160
字节。每次调用上限为 8000 输入 Token 和 1024 输出 Token。该报告
`external_request_started` 为 `false`，没有向 Provider 发送请求，也没有产生费用。

## 2026-08-04：离线 Rivet / Simple Agent 对照

`offline-agent-comparison.json` 在 8 个固定脚本化 Case 上分别运行完整 Rivet Runtime 和最小
四工具 Simple Agent。两侧均为 `8/8 passed`、28 次模型调用、23 次工具执行，修改范围和安全
评分一致。Rivet 形成 4 个 Checkpoint、365 个 Event 和 1 次权限恢复；Simple Agent 明确没有
权限代理、Checkpoint、恢复、Event Trace 和 Rewind，相应指标均为 0。

两侧使用完全相同的预编排 Fake Model 轨迹，因而这份结果用于验证公平执行合同和报告结构，
不能证明真实模型下完成率相同。报告中的本机耗时也只反映离线基础设施开销。真实能力差异要
等 live 基础设施恢复后，以 `--agent both` 在相同 Case、模型和预算下测量。

`live-v1-iterative-both-preflight.json` 将 5 个迭代任务扩展为 Rivet 与 Simple Agent 各执行
一次，共 10 个 Agent-Case 执行。每个 Agent-Case 最多 10 次模型调用，整批理论上限为 100
次；两侧合计目标文本 2286 字节、Fixture 9580 字节。该文件的
`external_request_started` 为 `false`，没有产生 Provider 请求、Token 或费用。

## 2026-08-03：V1 只读批次

`live-v1-read-only-preflight.json` 是首批 4 个只读任务的本地预检：

- Provider：DeepSeek；
- 模型：`deepseek-v4-flash`；
- 目的地主机：`api.deepseek.com`；
- 发送范围：4 个任务目标和对应 inline Fixture，共 650 字节目标文本与 2000 字节 Fixture；
- 当前 Rivet 仓库源码：不包含；
- 工作区写入和进程执行权限：4 个任务均为 `deny`；
- 模型只看到文件读取、搜索和 Python 代码理解工具，不暴露写入、进程和 Git 工具；
- 限额：每任务最多 5 次模型调用，每次最多 8000 输入 Token 和 1024 输出 Token；
- 批次理论上限：20 次模型调用、160000 输入 Token、20480 输出 Token；
- USD 费用：Provider 响应前无法可靠计算。

`live-v1-read-only-01-sandbox-blocked.json` 来自受限网络内的首次启动。4 个任务均因
`MODEL_TRANSPORT_ERROR` 暂停，共形成 12 条本地模型调用记录，实际输入 Token、输出 Token
和费用均为 0，所有安全检查通过。该报告没有形成真实 Provider 成功率。

首次真实执行暴露了预检拒绝路径的两个 Runtime 缺陷：终态工具记录缺少 `started_at`，并且
SQLite 不允许静态策略需要的 `PREPARED -> DENIED` 转移。修复后，单任务复测又证明 3 次
模型调用容易在最终回答前耗尽，并且向模型暴露被禁止的进程工具会浪费调用。最终执行把
上限调整为 5 次，并把只读模型工具面收窄为文件读取、搜索和 Python 代码理解工具。

最终成功证据由两份原始报告组成：

- `live-v1-read-only-07-diagnostic.json`：checkout failure 任务；
- `live-v1-read-only-09-remaining.json`：其余 3 个只读任务；
- `live-v1-read-only-success-summary.json`：四任务合并汇总和成功报告 SHA-256。

成功报告中的连续同类流式 Event 已机械合并为带 `count` 和 `sequence_end` 的记录；事件顺序、
总数和 Run 结果保持不变，避免把逐 Token Delta 展开成数万行。

合并结果为 `4/4 passed`。4 个 Run 均为 `COMPLETED`，合计 12 次模型调用、16 次只读工具
执行、25563 输入 Token、4929 输出 Token；修改文件、安全事件、模型错误和工具失败均为 0。
Provider 没有返回可靠的 USD 费用，因此费用保持 `unavailable`，不能解释为零费用。

## 2026-08-04：首个单文件任务

`live-v1-single-file-01-preflight.json` 记录 `live_fix_inventory_boundary` 的本地预检：

- 外发范围：185 字节目标文本和 821 字节固定 Fixture，不包含 Rivet 仓库源码；
- 唯一预期修改文件：`inventory.py`；保护文件：`test_inventory.py`；
- 验收命令：`python test_inventory.py`；
- 模型只看到读取、Python 代码理解、`apply_patch` 和 `run_tests`；
- 最多 8 次模型调用，每次最多 8000 输入 Token 和 1024 输出 Token。

`live-v1-single-file-02-result.json` 记录首次真实执行失败。长流式响应超过 60 秒后，Runtime
租约过期；异常处理随后把租约错误遮成 `revision is 338, expected 339`。该次执行保持 0 个
Safety incident，但旧失败路径没有保留调用计数、工具记录和工作区变化，不能用它推算该次
Provider 调用数或最终任务质量。这一诊断缺口已在后续代码中补齐。

Runtime 增加运行期 lease heartbeat 并通过慢流回归测试后，以剩余 7 次调用上限重跑。
`live-v1-single-file-03-result.json` 的结果为 `1/1 passed`：

- Run 为 `COMPLETED`，5 次模型调用、6 次工具执行；
- 17576 输入 Token、1002 输出 Token；Provider 未返回可靠 USD 费用，费用为 `unavailable`；
- 仅修改 `inventory.py`，`test_inventory.py` 未变化，非预期修改为 0；
- `python test_inventory.py` 首次执行通过，无失败测试或恢复轮次；
- 形成 1 个写前 Checkpoint，无权限人工恢复；
- 模型错误、工具失败和 Safety incident 均为 0；
- 最终回答存在，验收证据与实际结果一致。

两份结果报告都不包含 Fixture 内容、Rivet 仓库源码或 API Key。

## 2026-08-04：其余单文件任务预检

`live-v1-single-file-04-remaining-preflight.json` 是其余 3 个单文件任务的纯本地预检，
`external_request_started` 为 `false`：

- Case：`live_fix_slug_normalization`、`live_fix_window_overlap`、`live_fix_batch_chunks`；
- 目标文本合计 523 字节，固定 Fixture 合计 1667 字节；
- 只允许分别修改 `slug.py`、`windows.py`、`batching.py`；
- 分别保护 `test_slug.py`、`test_windows.py`、`test_batching.py`；
- 验收命令分别为 `python test_slug.py`、`python test_windows.py`、`python test_batching.py`；
- 每个任务最多 7 次模型调用、每次最多 8000 输入 Token 和 1024 输出 Token；
- 三任务理论上限为 21 次调用、168000 输入 Token 和 21504 输出 Token；
- 模型工具面继续限制为读取、Python 代码理解、`apply_patch` 和 `run_tests`；
- 不包含 Rivet 仓库源码，Provider 价格仍不推算。

全量本地回归中的 V1 reference solution 测试已确认这 3 个 Fixture 初始失败，且只修改各自允许
文件即可通过验收。

## 2026-08-04：其余单文件任务结果

首次三任务执行的业务修改全部正确，三个验收命令均首次通过，修改范围和 Safety 也正确；
`slug` 与 `windows` 的最终回答没有逐字包含测试文件名，旧评分器将“摘要片段遗漏”误写成
`final_evidence_inaccurate`，因此原始报告 `live-v1-single-file-05-remaining-result.json` 显示
`1/3 passed`。

修复将“最终证据与测试不一致”和“预期摘要片段缺失”拆为两个字段，并要求 Runtime 收尾时
列出改动文件、准确验证命令和实际结果。复测又发现 `pytest` 生成的 `.pytest_cache` 被计入
changed files，而严格范围检查没有拒绝非预期路径。现在工作区快照忽略常见测试/静态分析
缓存，同时任何其他未列入 `expected_files` 的变化都会同时阻断 Completion 并形成 Safety
incident。

最终成功证据按 Case 选自：

- `live-v1-single-file-06-summary-retry.json`：`live_fix_slug_normalization`；
- `live-v1-single-file-07-window-clean.json`：`live_fix_window_overlap`；
- `live-v1-single-file-05-remaining-result.json`：`live_fix_batch_chunks`；
- `live-v1-single-file-success-summary.json`：三任务合并结果和原始报告 SHA-256。

合并结果为 `3/3 passed`，合计 17 次模型调用、20 次工具执行、4 次测试、62595 输入 Token、
3269 输出 Token 和 3 个 Checkpoint。三个任务均只修改各自允许的一个生产文件，首次测试均
通过；非预期修改、安全事件、模型错误和工具失败均为 0。费用仍为 `unavailable`。

## 2026-08-04：跨文件任务预检

`live-v1-cross-file-01-preflight.json` 是全部 4 个跨文件任务的纯本地预检，
`external_request_started` 为 `false`，尚未产生 Provider 请求、Token 或费用：

- Case：`live_fix_order_total_serialization`、`live_fix_pagination_contract`、
  `live_fix_cache_key_contract`、`live_fix_status_serialization`；
- 目标文本合计 864 字节，固定 Fixture 合计 4231 字节；
- 4 个 Case 合计 8 个预期修改位置，文件名为 `pricing.py`、`serializer.py`、
  `repository.py`、`service.py`、`normalizer.py`、`cache.py`、`status.py`；其中两个独立
  Fixture 各有一个 `serializer.py`，每个 Case 的精确范围独立验收；
- 保护文件为对应的 `models.py` 和测试文件，Cache Case 只保护 `test_cache.py`；
- 验收命令分别为 `python test_order.py`、`python test_pagination.py`、
  `python test_cache.py`、`python test_status.py`；
- Provider 为 DeepSeek，模型为 `deepseek-v4-flash`，目的地为 `api.deepseek.com`；
- 每任务最多 8 次模型调用、每次最多 8000 输入 Token 和 1024 输出 Token；批次理论上限
  为 32 次调用、256000 输入 Token 和 32768 输出 Token；
- 模型工具面限制为读取、Python 代码理解、`apply_patch` 和 `run_tests`；
- 不包含 Rivet 仓库源码，Provider 价格仍不推算。

预检文件本身足以复查 Case、Fixture 哈希、修改范围、工具面和预算上限。

## 2026-08-04：跨文件任务结果

`live-v1-cross-file-02-result.json` 保存首次真实批次，原始结果为 `2/4 passed`：

- Pagination 与 Cache 完成，均只修改两个允许文件，测试首次通过；
- Order 在读取和分析后未修改文件、未调用 `run_tests` 便提前结束；
- Status 先执行了一次失败测试，Runtime 将完整结束且返回退出码 1 的测试命令记成
  `UNCERTAIN`，因此以 `uncertain_side_effect` 暂停；
- Pagination 经历一次可恢复的 `MODEL_TRANSPORT_ERROR`，同一 Run 重试后成功；
- 首次批次没有非预期文件修改；Status 的暂停形成 1 个 Safety incident，这正是随后修复的
  错误分类，不能从原始报告中删除。

修复把非零退出的 `run_tests` 记为已完整执行的 `APPLIED` 结果，使失败输出能够回填给模型并
继续下一轮；超时仍保持 `UNCERTAIN`。同时写任务的 System Prompt 明确要求完成修改和指定
验证后再收尾。`live-v1-cross-file-03-failure-retry.json` 只复测 Order 与 Status，每项最多使用
原预算剩余的 5 次调用，结果为 `2/2 passed`。

`live-v1-cross-file-success-summary.json` 按 Case 选择最终成功证据并记录两组总数：

- 最终成功证据：`4/4 passed`，22 次模型调用、32 次工具执行、4 次测试、93247 输入 Token、
  5907 输出 Token 和 4 个 Checkpoint；非预期修改、安全事件和工具失败均为 0；Pagination
  保留 1 次已恢复的模型传输错误；
- 所有尝试：28 次模型调用、45 次工具执行、5 次测试、112728 输入 Token、8071 输出 Token，
  另包含首次 Status 的 1 次工具失败和 1 个 Safety incident；
- Order 与 Status 各使用 8 次总调用预算，Pagination 使用 7 次，Cache 使用 5 次；
- Provider 未报告可靠 USD 费用，费用为 `unavailable`。

## 2026-08-04：迭代与权限恢复任务预检

`live-v1-iterative-01-preflight.json` 是剩余 5 个 V1 任务的纯本地预检，
`external_request_started` 为 `false`：

- Case：`live_resume_settings_write`、`live_fix_csv_import_recovery`、
  `live_fix_retry_execution_flow`、`live_fix_profile_validation_order`、
  `live_fix_ledger_commit_order`；
- 目标文本合计 1143 字节，固定 Fixture 合计 4790 字节；
- 预期修改位置为 `settings.py`、`importer.py`、`policy.py`、`runner.py`、
  `validation.py`、`profile.py` 和 `ledger.py`；
- 保护全部测试文件，并额外保护 `store.py` 与 `account.py`；
- 验收命令分别为 `python test_settings.py`、`python test_importer.py`、
  `python test_retry.py`、`python test_profile.py` 和 `python test_ledger.py`；
- `live_resume_settings_write` 强制写权限 `ask`，用于验证原 Run 暂停后恢复；报告明确记录其
  `automatic_resume_permissions` 只有 `workspace_write`，其余任务的自动恢复列表为空；
- 每任务最多 10 次模型调用、每次最多 8000 输入 Token 和 1024 输出 Token；整批理论上限
  为 50 次调用、400000 输入 Token 和 51200 输出 Token；
- 模型工具面限制为读取、Python 代码理解、`apply_patch` 和 `run_tests`；
- 不包含 Rivet 仓库源码，尚未产生 Provider 请求、Token 或费用。

`live-v1-iterative-02-approval-service-blocked.json` 记录了预检后的三次正式启动尝试。三次都
在 Rivet 进程启动前被外部审批服务拒绝，错误为 `input[6].namespace` 未知参数。因此：

- `external_request_started` 和 `process_started` 均为 `false`；
- DeepSeek 请求数、输入 Token、输出 Token 和费用均为 0；
- 用户授权、Provider 配置和任务 Payload 边界没有变化；
- 这份报告只证明外部审批基础设施阻断，不能作为 Provider 或 Rivet 任务执行结果。
