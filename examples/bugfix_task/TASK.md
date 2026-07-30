# 任务

修复 `pricing.py` 中的折扣总价计算错误。

## 验收条件

- `calculate_total([100.0], 10)` 返回 `90.0`；
- 零折扣返回原始总价；
- 空价格列表返回 `0.0`；
- 小于 `0` 或大于 `100` 的折扣比例继续抛出 `ValueError`；
- `python pricing_checks.py` 全部通过；
- 不修改 `pricing_checks.py`。
