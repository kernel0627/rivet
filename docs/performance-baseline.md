# Rivet 本地性能基线

状态：初始离线基线

日期：2026-07-31
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

## 2. 首次快照

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

## 3. 使用规则

- 比较结果时保持 Python 主版本、操作系统、电源状态和 `--repeat` 数量一致；
- 先看通过率，再看耗时，失败执行不能作为有效性能改进；
- 本基线用于发现数量级回退，不将单次毫秒波动视为回归；
- 大型仓库应另建索引、检索、Token 和真实任务基线，不与这组微型 Fixture 混合。
