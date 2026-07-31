# Rivet 测试策略

状态：Active

当前全量结果和覆盖矩阵见
[implementation-status.md](implementation-status.md)。

## 1. 测试目标

Rivet 的测试需要回答四个问题：

1. Runtime 状态是否正确转换？
2. 工具是否在权限和工作区边界内执行？
3. 中断、失败和恢复是否会重复操作或丢失状态？
4. Agent 最终是否完成了真实代码任务？

只检查模型生成的文字不能证明 Coding Agent 可用。

## 2. 测试分层

### 2.1 领域与状态机单元测试

纯内存、无文件、无网络：

- Run 状态转换；
- RuntimeDecision；
- Stop Policy；
- 重复调用判断；
- Error Category；
- Context Budget；
- Permission Policy；
- Event sequence；
- Snapshot reducer。

这些测试应占多数，并且执行很快。

### 2.2 Adapter 契约测试

Model Adapter 使用本地假 HTTP Server：

- 普通最终回答；
- 单 Tool Call；
- 并行 Tool Call；
- 空 content；
- 非法 arguments JSON；
- 缺失字段；
- 429、500 和超时；
- usage；
- 流式中断；
- 敏感 Header 不进入 Trace。

默认测试不能访问真实收费 API。

State Adapter 测试：

- transaction；
- schema migration；
- 并发读取；
- 崩溃后重新打开；
- Snapshot 与 Event sequence 一致；
- artifact 丢失或损坏。

Model 网络安全：

- Bearer token 不发送给非 loopback 的明文 HTTP；
- 跨主机重定向不能携带凭据；
- 响应体和错误体有读取上限；
- 错误、Session 和 Event 中不出现密钥；
- 畸形 Provider 响应统一映射为 Model Protocol Error。

### 2.3 Tool 单元与安全测试

文件读取：

- UTF-8、其他编码、空文件、二进制文件；
- 行号和截断；
- 超大文件；
- 文件在读取过程中变化。

工作区边界：

- `../`；
- 绝对路径；
- symlink 逃逸；
- 不存在路径；
- workspace 根目录本身是 symlink；
- 写入父目录；
- 大小写和规范化问题。

搜索进程：

- `-foo`、`--files` 和 `--pre=...` 必须作为查询文本；
- argv 必须使用 `--no-config` 和 `-e <query>`；
- 仓库内容不能改变 ripgrep 的执行选项；
- 空字符串和超长查询有明确限制。

命令：

- 参数化执行，避免 shell 拼接；
- timeout；
- stdout/stderr 上限；
- 子进程树终止；
- cwd 边界；
- 环境变量过滤；
- 高风险命令审批。

编辑：

- Patch 成功；
- 上下文不匹配；
- 原文件被外部改变；
- 原子写入；
- Checkpoint；
- Rewind；
- 多文件部分失败。

### 2.4 Runtime 集成测试

使用 Fake Model 和临时仓库：

```text
最终回答
搜索 → 读取 → 最终回答
非法工具参数 → 工具错误 → 模型修正
未知工具 → 模型修正
重复调用在执行前停止
模型超时 → 可重试或停止
需要写入 → 等待审批 → 恢复
编辑 → 测试失败 → 再编辑 → 通过
编辑 → 中断 → 恢复后不重复写入
Checkpoint → 外部修改 → RewindConflict
达到 token/turn/cost 预算
```

Fake Model 应按输入条件返回结果，不能只依赖一个简单的“响应队列”。这样才能验证 Runtime 是否发送了正确上下文。

### 2.5 CLI 与 Headless 端到端测试

通过安装后的命令和本地假 Provider 测试：

- exit code；
- stdout/stderr；
- JSON 输出 Schema；
- Ctrl-C；
- 配置优先级；
- 无模型配置；
- 恢复 Run；
- state 目录位置；
- 目标仓库无意外文件。

### 2.6 Eval

内置固定数据集通过两种执行模式复用同一批 Fixture 和验收条件：

- `rivet eval --mode offline` 使用脚本化模型，默认离线并可进入 CI；
- `rivet eval --mode live` 使用配置的真实 Provider，显式产生网络请求和费用。
- `rivet eval --mode offline --repeat 10 --json` 重复执行固定场景并输出
  min、mean、median、P95、max 和逐场景耗时。

当前固定基线覆盖：

- 只读入口解释；
- 单文件 Bugfix、受保护测试文件和确定性验收命令；
- 工作区逃逸拒绝。

后续扩展数据集继续覆盖：

- 符号定位；
- 调用链解释；
- 单文件 Bug；
- 跨文件 Bug；
- 新增测试；
- 重命名；
- 受限权限任务；
- 需要恢复的任务。

指标：

```text
Retrieval: Recall@K, MRR, nDCG
Trajectory: turns, tool errors, duplicate actions, token/cost
Completion: tests, expected diff, prohibited diff
Safety: unauthorized writes, boundary violations, rollback success
```

## 3. 首批必须有的测试

在 M0 宣布完成前：

1. 模型直接回答；
2. 工具调用后结果进入下一轮 Context；
3. 非法参数保留 `TOOL_ARGUMENT_ERROR`；
4. Provider 错误不被误标为其他错误；
5. `../` 和 symlink 逃逸被拒绝；
6. 重复工具调用在再次执行前停止；
7. 达到 turn 预算；
8. Snapshot 保存后可恢复；
9. 恢复不会重复已完成的工具；
10. `rivet run` 不在目标仓库创建状态文件。
11. 搜索查询不能成为 ripgrep 选项。
12. 大文件、超大目录和搜索输出在读取阶段受限。
13. Provider、Tool 和 Store 错误分类互不混淆。
14. Session、Event 和 Trace 中不泄漏密钥或原始敏感异常。

在 M1 宣布完成前：

1. 写入需要对应权限；
2. 写前 Checkpoint；
3. Patch 原子性；
4. 测试失败进入下一轮；
5. Rewind；
6. 外部修改导致 RewindConflict；
7. 命令 timeout 和子进程清理；
8. Ctrl-C 后 Run 可恢复；
9. diff 中无意外文件；
10. Session 内多个 Run 相互独立。

## 4. 固定 Eval 的定位

固定 Eval 证明 Fixture、Runtime、工具、验证命令和 Completion/Safety 评估可以
重复执行。离线 3/3 通过不证明真实 Provider 的模型行为；真实 Provider 结果必须单独
记录模型、日期、费用和失败边界。正式验收同时使用 `tests/unit`、`tests/contract`、
`tests/integration`、`tests/security`、`tests/e2e` 和固定 Eval。

## 5. 验证门禁

每个里程碑至少经过：

```text
静态检查
→ 单元测试
→ Adapter 契约测试
→ Runtime 集成测试
→ CLI 端到端测试
→ 安全场景
→ 固定离线 Eval
```

真实模型测试属于显式启用的 smoke test，不进入默认离线测试。
