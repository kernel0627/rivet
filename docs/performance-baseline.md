# Rivet 本地性能基线

状态：Active

最近测量：2026-08-01
用途：发现 Runtime、SQLite、工具执行和 Eval 基础设施的明显性能回退

## 1. 测量范围

命令：

```bash
rivet eval --mode offline --repeat 10 --json
```

该命令顺序执行十轮固定 Eval。每轮均重新创建隔离工作区和状态目录，并覆盖：

- Context 和 Runtime 推进；
- SQLite 状态、Event 与 ModelCall 持久化；
- 读取、Patch、测试命令和工作区边界拒绝；
- Completion/Safety 评估；
- 临时资源关闭与清理。

脚本化 Fake Model 不访问网络，因此结果主要反映 Rivet 本地执行开销。该基线不代表真实
Provider 延迟、大型仓库索引性能或端到端模型任务耗时。

## 2. 2026-07-31 三场景快照

环境：

```text
macOS 15.5 (24F74)
Darwin arm64
Python 3.10.20
```

十轮均通过，套件耗时如下：

| 指标 | 毫秒 |
|---|---:|
| min | 171.981 |
| mean | 197.495 |
| median | 198.195 |
| p95 | 222.322 |
| max | 222.322 |

单场景结果：

| 场景 | 通过 | median | p95 |
|---|---:|---:|---:|
| `explain_entrypoint` | 10/10 | 27.547 ms | 44.548 ms |
| `fix_discount` | 10/10 | 138.456 ms | 163.618 ms |
| `reject_workspace_escape` | 10/10 | 26.209 ms | 31.320 ms |

## 3. 2026-08-01 五场景快照

本次增加符号定位和跨文件修复。十轮均通过，套件耗时如下：

| 指标 | 毫秒 |
|---|---:|
| min | 744.990 |
| mean | 772.643 |
| median | 769.082 |
| p95 | 817.164 |
| max | 817.164 |

单场景结果：

| 场景 | 通过 | median | p95 |
|---|---:|---:|---:|
| `explain_entrypoint` | 10/10 | 54.816 ms | 66.048 ms |
| `fix_discount` | 10/10 | 274.095 ms | 288.566 ms |
| `reject_workspace_escape` | 10/10 | 53.553 ms | 60.568 ms |
| `locate_invoice_symbol` | 10/10 | 107.312 ms | 116.605 ms |
| `fix_cross_file_total` | 10/10 | 278.587 ms | 288.682 ms |

这次用例数量和工具执行量均增加，不能把套件总耗时直接与三场景快照比较。后续回归应
使用同一份五场景数据集；如果数据集再次变化，应另建快照。

## 4. 2026-08-01 八场景快照

本次继续增加调用链解释、新增回归测试和权限暂停恢复。十轮均通过，套件耗时如下：

| 指标 | 毫秒 |
|---|---:|
| min | 1347.603 |
| mean | 1444.236 |
| median | 1426.196 |
| p95 | 1559.593 |
| max | 1559.593 |

单场景结果：

| 场景 | 通过 | median | p95 |
|---|---:|---:|---:|
| `explain_entrypoint` | 10/10 | 54.333 ms | 59.409 ms |
| `fix_discount` | 10/10 | 247.613 ms | 290.827 ms |
| `reject_workspace_escape` | 10/10 | 47.526 ms | 54.667 ms |
| `locate_invoice_symbol` | 10/10 | 99.138 ms | 112.567 ms |
| `fix_cross_file_total` | 10/10 | 263.310 ms | 301.488 ms |
| `trace_order_call_chain` | 10/10 | 167.503 ms | 196.522 ms |
| `add_slug_regression_test` | 10/10 | 251.496 ms | 289.736 ms |
| `resume_permission_write` | 10/10 | 260.140 ms | 296.878 ms |

暂停恢复场景的每轮验收同时要求一次 permission resume、一次 Checkpoint 和三次工具执行，
用于发现恢复后重复写入或重复创建 Checkpoint 的回归。

## 5. 使用规则

- 比较结果时保持 Python 主版本、操作系统、电源状态和 `--repeat` 数量一致；
- 先看通过率，再看耗时，失败执行不能作为有效性能改进；
- 本基线用于发现数量级回退，不将单次毫秒波动视为回归；
- 大型仓库应另建索引、检索、Token 和真实任务基线，不与这组微型 Fixture 混合。
