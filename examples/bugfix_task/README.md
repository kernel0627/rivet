# Bugfix Task：折扣金额计算

这个小仓库包含一个确定性的业务逻辑错误：折扣函数返回了“优惠金额”，调用方需要的却是
“优惠后的应付金额”。任务要求保留已有输入校验，只修改实现并让全部检查通过。

## 1. 准备隔离工作区

在 Rivet 仓库根目录执行：

```bash
cp -R examples/bugfix_task /tmp/rivet-bugfix-demo
cd /tmp/rivet-bugfix-demo
```

Windows PowerShell 可以复制到任意临时目录：

```powershell
Copy-Item -Recurse examples\bugfix_task $env:TEMP\rivet-bugfix-demo
Set-Location $env:TEMP\rivet-bugfix-demo
```

## 2. 确认问题

```bash
python pricing_checks.py
```

初始状态应有两个失败检查，分别覆盖普通折扣和零折扣。

## 3. 运行 Rivet

确保当前 shell 已配置 `RIVET_MODEL` 和 `RIVET_API_KEY`，然后执行：

```bash
rivet run --workspace . \
  "阅读 TASK.md，修复折扣计算错误，运行验收命令，并说明修改与验证结果"
```

默认权限策略会在写文件和执行命令前暂停。按照 CLI 输出的 `run_id`、`pause_token`
和 `prepared_digest` 使用 `rivet resume` 授权即可。

## 4. 验收

```bash
python pricing_checks.py
```

预期结果：

```text
Ran 4 tests
OK
```

这个示例覆盖搜索/读取、权限暂停、Checkpoint、修改、命令执行、验证与最终回答。
