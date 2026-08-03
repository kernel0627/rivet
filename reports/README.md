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
