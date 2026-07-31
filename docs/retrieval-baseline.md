# Rivet 检索性能基线

状态：Active

最近测量：2026-08-01

## 1. 目的

这组基准回答三个问题：

1. 当前 Python AST 索引真实仓库需要多少时间和内存；
2. Sparse、Hash Dense 和 Hybrid 在固定查询上的 Top-5 命中与延迟；
3. 哪些检索组件已有证据支持默认开启。

报告只保存路径、符号、排名和统计值，不保存源码内容，不访问网络，也不调用模型。

## 2. 复现方法

查询集：[retrieval_queries.json](../benchmarks/retrieval_queries.json)

命令：

```bash
TMPDIR=/private/tmp PYTHONPATH=src conda run -n agent python -m rivet.interfaces.cli \
  benchmark-retrieval --workspace . \
  --queries benchmarks/retrieval_queries.json \
  --repeat 20 --limit 5 \
  --output /private/tmp/rivet-retrieval-baseline.json
```

配置：

```text
Embedding: deterministic Hash, 256 dimensions
Queries: 5
Top-K: 5
Repeated searches: 20 per query and retriever
Index state: isolated temporary SQLite database
```

固定查询覆盖权限恢复、增量索引、DeepSeek 协议、Checkpoint/Rewind 和 Eval 报告。
真值允许命中直接生产实现或对应的高价值契约测试。

## 3. 仓库规模与索引

| 指标 | 结果 |
|---|---:|
| Python 文件 | 167 |
| Python 行数 | 27,671 |
| Python 字节 | 961,431 |
| AST Chunk | 1,638 |
| SQLite 索引 | 8,503,296 bytes |
| 冷索引 | 4,272.537 ms |
| 冷索引 Python 内存峰值 | 17.326 MiB |
| Warm refresh median | 674.990 ms |
| Warm refresh P95 | 778.291 ms |
| 解析失败 | 0 |

Warm refresh 报告 167 个文件均未变化，但当前实现仍需为 InMemory Dense 重建 Chunk 向量，
因此这个数字包含 Dense 重建成本，不能解释成纯 SQLite 增量刷新。

## 4. 检索质量与延迟

| 检索器 | Top-5 命中 | median | P95 |
|---|---:|---:|---:|
| Sparse FTS5 | 5/5 | 2.212 ms | 2.963 ms |
| Hash Dense | 0/5 | 23.035 ms | 26.640 ms |
| Hybrid + Lexical Reranker | 5/5 | 32.216 ms | 37.791 ms |

结论：

- Sparse 对这组带明确符号和行为词的仓库内查询全部命中，延迟最低；
- 确定性 Hash Dense 没有命中任何真值，不能作为真实语义检索能力；
- Hybrid 的命中与 Sparse 相同，但 median 延迟约为 Sparse 的 14.6 倍；
- 在接入并验证真实 Embedding 前，可靠默认应为 Sparse + Lexical Reranker；
- Hash Dense 保留为离线架构测试和实验选项，默认关闭。

## 5. 已采取的产品决策

- `RetrievalConfig.dense` 默认值改为 `false`；
- README 示例默认关闭 Dense；
- 仍可显式开启 InMemory Hash Dense 或配置 Qdrant；
- 后续接入真实 Embedding 时，必须在同一查询集上重新测量命中和延迟，不能沿用本结果。

## 6. 边界与下一步

这组基准是中型 Python 仓库的离线检索测量，没有覆盖：

- 更大型多语言仓库；
- 真实 Embedding 或 Cross-Encoder；
- Provider Token、费用和端到端任务耗时；
- 并发索引、长期文件监听和 Qdrant 网络延迟。

下一步应先把 warm refresh 的 Dense 全量重建与 Sparse 增量路径拆开测量，再决定是否优化
索引器；真实 Embedding 必须以命中改善超过延迟和资源成本为保留条件。
