# 自检报告

版本 0.3.0，检查日期 2026-11-23。从用户、设计师、工程师三个视角逐项检查，
每项都对应一次实际修复或验证。

## 用户视角

| 检查项 | 结论 | 处理 |
|---|---|---|
| 第一次使用知道从哪开始吗 | 之前 `comsol_status` 只返回 `connected: false`，没有下一步 | 加 `hint`：「运行 comsol_start 开始」 |
| 出错时知道怎么改吗 | 之前未知 action 只报「未知 action：xxx」 | 所有 action 错误都列出可用值，如 `list/create/update/delete/run` |
| 建材料要手写一堆物性吗 | 之前要记 7 个属性名和单位 | 加 `comsol_material`，内置铜/铝/钢/硅/水/空气预设 |
| 参数扫描要写 Java 吗 | 之前只能用 `comsol_python` | 加 `comsol_sweep`，自动从参数定义推断单位 |
| 做选择练习要自己算边界编号吗 | 之前只能靠 `comsol_python` | 加 `comsol_selection`（Box/Ball/Explicit），可直接绑到边界条件 |
| 模型坏了知道哪里坏吗 | 之前只有 COMSOL 的原始报错 | 加 `comsol_diagnose`，检查几何/物理场/材料/网格/研究/解 |
| 不知道属性名怎么办 | 之前要查 COMSOL 文档 | 加 `comsol_help`，9 个主题的离线速查 |
| 导出数据有坐标吗 | 之前只有序号和值 | `comsol_export` 输出 `x,y,z` 列（数据集支持时） |
| 重复打开同一个文件会堆副本吗 | 之前会生成 model2/model3 | `comsol_open` 检测同路径模型并复用 |
| 截图会看不到窗口吗 | 最小化时截不到 | 自动恢复→截图→恢复最小化 |

## 设计师视角

| 检查项 | 结论 | 处理 |
|---|---|---|
| 工具粒度是否合理 | 参考项目有 100+ 工具，上下文成本高 | 保持 24 个聚合工具，用 feature type + properties 覆盖所有物理场 |
| 线程模型是否安全 | `comsol_status` 曾在非 worker 线程直接调 JVM，违反 COMSOL 单线程约束 | 改为只读共享缓存，模型变更时在 worker 内刷新 |
| 空闲退出会不会杀求解 | 会，30 分钟无操作直接 `os._exit` | 求解中（`busy`）跳过退出检查 |
| 显式指定的组件不存在怎么办 | 静默回退到第一个组件，容易改错对象 | 现在会创建指定组件，不再静默替换 |
| 端口探测是否可靠 | 只探测 IPv4，COMSOL 有时只监听 `::` | IPv4/IPv6 都探测 |
| 是否留有扩展点 | 24 个工具不可能覆盖全部 | `comsol_python` 暴露完整 Java API，且只占一个工具位 |
| 错误模型是否统一 | 各工具都返回 JSON `{"error": ...}` | 保持；MCP 层不会因为异常断开 |
| 文档是否覆盖三种客户端 | 只有 opencode 和 Claude Desktop | 增加 DSH，并说明其他 stdio 客户端 |

## 工程师视角

| 检查项 | 结论 | 处理 |
|---|---|---|
| 测试覆盖 | 3 个测试文件，约 120 项断言 | 全部通过（smoke 38.7s / modeling 38.2s / full 41.1s） |
| 静态检查 | ruff 报告 52 个问题 | 全部修复，现在 `All checks passed!` |
| 闭包绑定 | 6 处 lambda 隐式捕获循环变量 | 全部改为默认参数绑定 |
| 冗余代码 | `int(len())`、多余的 `global`、未使用的 noqa | 已清理 |
| 返回值编码 | `comsol_python` 曾双重 JSON 编码 | 已修 |
| 端口复用 | `comsol_start` 对 `port=0` 返回 0 而不是真实端口 | 读取 `Client.port` |
| 异常兜底 | COMSOL 会抛各种 `FlException` | 统一在工具边界捕获并转 JSON；ruff 配置里说明这是有意为之 |
| 进程泄漏 | 客户端重连会留下未退出的实例 | 空闲 30 分钟自动退出；求解中不退出 |
| 日志 | 写 `logs/comsol-mcp.log`，stdout 只留给协议 | 保持 |
| 依赖 | mph / mcp / pillow / numpy / pywin32 | pyproject 声明，Windows 条件依赖 |

## 回归结果

```
smoke_test.py      all checks passed   38.7s
modeling_test.py   all checks passed   38.2s
full_test.py       all checks passed   41.1s
ruff check         All checks passed!
```

## 已知限制（无法在 API 层解决）

- 一个进程只能创建一次 COMSOL client，`comsol_stop` 后需重启 MCP。
- COMSOL Java API 不暴露取消求解，长求解只能等。
- 求解进度只有运行/完成，没有百分比。
- 截图会短暂抢焦点；被其他窗口完全遮挡时可能截到遮挡窗口。
