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
- 限额：每任务最多 3 次模型调用，每次最多 8000 输入 Token 和 1024 输出 Token；
- 批次理论上限：12 次模型调用、96000 输入 Token、12288 输出 Token；
- USD 费用：Provider 响应前无法可靠计算。

`live-v1-read-only-01-sandbox-blocked.json` 来自受限网络内的首次启动。4 个任务均因
`MODEL_TRANSPORT_ERROR` 暂停，共形成 12 条本地模型调用记录，实际输入 Token、输出 Token
和费用均为 0，所有安全检查通过。该报告没有形成真实 Provider 成功率。

随后申请的联网执行在进程启动前被审批层拒绝，因为新 Fixture 到 `api.deepseek.com` 的具体
外发仍需要用户明确授权。拒绝发生在外部请求启动前，没有新增 Provider 请求、Token 或费用。
