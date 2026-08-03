# Live Eval 证据

本目录保存需要在任务工作区清理后继续复查的脱敏结构化报告。报告不得包含 API Key、`.env`
内容或当前 Rivet 仓库源码。

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
