# Contributing to Rivet

感谢你改进 Rivet。项目优先维护可恢复、安全、可验证的单 Agent Runtime；新功能需要
遵守现有 Domain、Runtime、Tool、State 和 Workspace 边界。

## 开发环境

项目要求 Python 3.10+。使用标准虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 提交前验证

```bash
python -m pytest -q
rivet eval --mode offline
ruff check src tests examples
python -m pip wheel --no-deps . -w /tmp/rivet-wheel
```

测试默认必须离线运行，不依赖真实 Provider、Qdrant Server 或收费 API。外部服务使用
fake 或 contract adapter；需要真实服务的验证应单独记录部署环境和凭据边界。

## 修改原则

- Runtime 是推进 Run 的唯一协调器；
- 模型只返回文本和工具提案，不直接修改工作区；
- 写操作必须经过权限、Checkpoint 和持久化的执行状态；
- 新状态变化必须有对应 Event，并遵守 Run revision；
- 目标仓库中不得产生 Rivet 状态、密钥或运行日志；
- 非只读副作用中断后必须停止自动重放；
- 新工具必须声明 Schema、Effect、Permission、Timeout 和输出预算；
- README 和公开开发文档使用 `venv + pip`，不写个人本机环境。

## Pull Request 检查表

- [ ] 修改范围与问题或目标一致；
- [ ] 新行为有对应测试；
- [ ] 全量测试和 Ruff 通过；
- [ ] 没有加入密钥、`.env`、运行状态或生成文件；
- [ ] 公共接口、配置或命令变化已更新文档；
- [ ] 没有把真实服务验证描述成离线测试结果；
- [ ] 破坏性兼容变化已经明确说明。
