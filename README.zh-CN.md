# comsol-mcp（中文）

让 AI 助手直接驱动 Windows 上的 **COMSOL Multiphysics**：建几何、设参数、划网格、跑求解、提取场量、导出图片——同时你的 COMSOL Desktop 可以连到同一个会话，实时看到改动。

底层用 [MPh](https://github.com/MPh-py/MPh)（COMSOL Java API 的 Python 桥）和官方 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。

```
MCP 客户端（opencode / Claude Desktop / Cline / ...）
        │  stdio
        ▼
  comsol_mcp/server.py  ──MPh + JPype──▶  COMSOL Multiphysics Server (:2036)
                                                    ▲
                                            COMSOL Desktop（client 模式）
```

## 特性

- **实时共享会话**：Desktop 以 client 模式连上 server，AI 改模型时界面同步刷新。
- **完整流程**：参数、几何、物理场、材料、网格、研究、求解、求值、出图。
- **逃生舱**：`comsol_python` 可以直接操作原始 `com.comsol.model.Model` Java 对象。
- **长求解不阻塞**：后台求解 + 状态轮询。
- **安全保存**：覆盖前自动生成 `.bak-时间戳` 备份。

## 环境要求

| | |
|---|---|
| 系统 | Windows 10/11（已测试）。核心逻辑跨平台，截图等辅助功能仅 Windows。 |
| COMSOL | Multiphysics 6.x，有效许可证（测试于 6.4.0.429） |
| Python | 3.11+ |
| Java | 用 COMSOL 自带的，无需单独安装 JDK |

`COMSOL_ROOT` 可不设——MPh 会从 Windows 注册表自动发现安装位置。

## 安装

```powershell
git clone https://github.com/Aknpa-Kacyraho/comsol-mcp
cd comsol-mcp

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

验证：

```powershell
python -c "import comsol_mcp; print(comsol_mcp.__version__)"
python tests\smoke_test.py
python tests\modeling_test.py
python tests\full_test.py
python tests\edge_test.py
```

每个测试都会在随机端口起独立 server，跑完自动清理，不影响正在使用的 COMSOL。

## 配置 MCP 客户端

### opencode

```jsonc
{
  "mcp": {
    "comsol": {
      "type": "local",
      "command": [
        "C:\\path\\to\\comsol-mcp\\.venv\\Scripts\\python.exe",
        "C:\\path\\to\\comsol-mcp\\server.py"
      ],
      "cwd": "C:\\path\\to\\comsol-mcp",
      "environment": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8"
      },
      "enabled": true,
      "timeout": 3600000
    }
  }
}
```

### Claude Desktop

```json
{
  "mcpServers": {
    "comsol": {
      "command": "C:\\path\\to\\comsol-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\comsol-mcp\\server.py"],
      "env": { "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

### DeepSeek Harness (DSH)

在 `home/cordis.patch.yml` 的 `insert` 下加一段：

```yaml
    - id: mcp-comsol
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: comsol
        transport: stdio
        command: 'C:/path/to/comsol-mcp/.venv/Scripts/python.exe'
        args:
          - 'C:/path/to/comsol-mcp/server.py'
        cwd: 'C:/path/to/comsol-mcp'
        env:
          PYTHONUTF8: '1'
          PYTHONIOENCODING: 'utf-8'
        toolCallTimeoutMs: 3600000
```

工具在 DSH 里显示为 `mcp__comsol__*`，改完重启 DSH 生效。

### 其他 stdio 客户端（Cline 等）

命令和参数与 Claude Desktop 相同。任何支持 stdio MCP 的客户端都能用；
服务端只往 `logs/` 写日志，stdout 全部留给协议。

## 工具

| 工具 | 作用 |
|---|---|
| `comsol_status` | 连接状态、端口、已加载模型、当前模型、后台任务 |
| `comsol_start` | 连接已有 server 或启动新的（默认 2036） |
| `comsol_stop` | 断开并可选择关闭 server |
| `comsol_open` | 打开 `.mph` 或新建模型 |
| `comsol_use` | 多模型时切换当前操作对象 |
| `comsol_save` | 保存/另存，自动备份 |
| `comsol_inspect` | 模型结构树：参数、几何、物理场、选择、材料、网格、研究、结果 |
| `comsol_geometry` | 通用几何：Block/Cylinder/Sphere/Rectangle/Circle/Import/布尔运算的增删改与构建 |
| `comsol_physics` | 通用物理场：建接口、加特征、绑选择、设属性 |
| `comsol_selection` | 命名选择（Box/Ball/Explicit），供边界条件复用 |
| `comsol_diagnose` | 模型体检：COMSOL 报告的问题 + 缺失的几何/物理场/材料/网格/研究 |
| `comsol_help` | 离线速查：几何、传热、结构、流动、静电、选择、网格的属性名 |
| `comsol_material` | 材料：内置铜/铝/钢/硅/水/空气预设，或自定义物性 |
| `comsol_params` | 读写全局参数（支持 `200[A]`、`1.5[mm]`） |
| `comsol_sweep` | 参数扫描（参数名 + 取值列表，自动推断单位） |
| `comsol_mesh` | 生成网格，可选密度 1–9 |
| `comsol_solve` | 运行研究，支持后台模式 |
| `comsol_eval` | 表达式求值，返回 min/max/mean |
| `comsol_plot` | 任意表达式出表面图并导出 PNG |
| `comsol_export` | 场量导出 CSV（含 x/y/z 坐标） |
| `comsol_model` | 列出、卸载、重命名已加载模型 |
| `comsol_python` | 执行 Python，直接调 COMSOL Java API |
| `comsol_desktop` | 启动 Desktop 并连到当前 server |
| `comsol_screenshot` | 截 COMSOL 窗口，让 AI 看到当前界面 |

## 实时共享会话

COMSOL 支持 client–server 模式：server 持有模型，Desktop 只是客户端。

1. 让助手运行 `comsol_start`，server 监听 2036。
2. 启动客户端：
   ```powershell
   .\start_client.ps1
   ```
   或
   ```powershell
   & "<COMSOL_ROOT>\bin\win64\comsolmphclient.exe" -server localhost -port 2036
   ```
3. 弹窗里点 **Connect**。
4. 在 server 的文件列表里打开同一个模型。

之后通过 MCP 的修改会实时出现在 Desktop 的模型树里。

> 不想共享也没关系：跳过 Desktop，MCP 独立打开 `.mph`，保存后你重新打开即可。

## 逃生舱示例

24 个工具覆盖不到的操作，都可以用 `comsol_python` 完成（可用 `model`、`java`、`client`、`mph`、`np`）：

```python
ht = java.modelNode('comp1').physics('ht')
flux = ht.create('hf1', 'HeatFluxBoundary', 1)
flux.selection().set([2])
flux.set('q0', '1e4[W/m^2]')
```

常用 Java API 路径：

- 几何：`java.modelNode('comp1').geom('geom1')`
- 物理场：`java.modelNode('comp1').physics('ht')`
- 材料：`java.modelNode('comp1').material('mat1').propertyGroup('def')`
- 网格：`java.modelNode('comp1').mesh('mesh1')`
- 研究：`java.study('std1')`
- 结果：`java.result()`

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `COMSOL_ROOT` | 自动发现 | COMSOL 安装根目录 |
| `COMSOL_PORT` | `2036` | 默认 server 端口 |
| `COMSOL_MCP_HOME` | 当前目录 | `logs/`、`out/` 的存放位置 |
| `COMSOL_MCP_IDLE_TIMEOUT` | `1800` | 空闲多少秒后自动退出；`0` 表示禁用。防止客户端断开后进程堆积。 |

## 已知限制

- 一个进程只能创建一个 COMSOL client：`comsol_stop` 之后需要重启 MCP 进程才能重新连接（JVM 限制）。
- COMSOL Java API 没有暴露取消求解的接口：长求解只能等它结束，或重启 COMSOL 进程。
- 求解进度只有"运行中/已完成"，没有百分比。
- `comsol_screenshot` 会先把 COMSOL 窗口置前再截图，会短暂抢焦点。
- 截图、启动 Desktop 仅 Windows。

## 致谢

- [MPh](https://github.com/MPh-py/MPh) — Python 调用 COMSOL Java API 的桥梁。
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — server 框架。
- 设计参考：[wjc9011/COMSOL_Multiphysics_MCP](https://github.com/wjc9011/COMSOL_Multiphysics_MCP) 和 [Ching-Chiang/comsol-mcp](https://github.com/Ching-Chiang/comsol-mcp)。`comsol_geometry` / `comsol_physics` 的「feature type + properties」通用模式来自这两个项目。

## 自检

0.3.0 经过了用户 / 设计师 / 工程师三视角检查，清单和修复记录见
[docs/SELF-CHECK.md](docs/SELF-CHECK.md)。

## 免责声明

本项目与 COMSOL AB 无关，未获其背书或支持。"COMSOL" 和 "COMSOL Multiphysics" 是 COMSOL AB 的商标。使用本软件需要你自行拥有有效的 COMSOL 许可证。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
