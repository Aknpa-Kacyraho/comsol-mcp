# comsol-mcp

An MCP server that lets AI assistants drive **COMSOL Multiphysics** on Windows: build geometry, set parameters, generate meshes, solve studies, evaluate fields, and export plots — while your COMSOL Desktop stays connected to the same live session.

Built on [MPh](https://github.com/MPh-py/MPh) (the Python bridge to the COMSOL Java API) and the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

```
MCP client (opencode / Claude Desktop / Cline / ...)
        │  stdio
        ▼
  comsol_mcp/server.py  ──MPh + JPype──▶  COMSOL Multiphysics server (:2036)
                                                    ▲
                                            COMSOL Desktop (client mode)
```

## Features

- **Live session sharing** — start the server, connect COMSOL Desktop in client mode, and watch changes appear in the GUI as the model is edited.
- **Full workflow** — parameters, geometry, physics, materials, mesh, study, solve, evaluate, plot.
- **Escape hatch** — `comsol_python` runs arbitrary Python against the raw `com.comsol.model.Model` Java object, so nothing is out of reach.
- **Long solves** — background solving with status polling instead of blocked tool calls.
- **Safe saves** — timestamped `.bak-*` backups before overwriting a model.

## Requirements

| | |
|---|---|
| OS | Windows 10/11 (tested). Core logic is cross-platform; screenshot and some helpers are Windows-only. |
| COMSOL | Multiphysics 6.x with a valid license (tested on 6.4.0.429). |
| Python | 3.11+ |
| Java | Bundled with COMSOL; no separate JDK needed. |

`COMSOL_ROOT` is optional — MPh discovers installed COMSOL versions from the Windows registry.

## Installation

```powershell
git clone https://github.com/Aknpa-Kacyraho/comsol-mcp
cd comsol-mcp

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Verify:

```powershell
python -c "import comsol_mcp; print(comsol_mcp.__version__)"
python tests\smoke_test.py
python tests\modeling_test.py
python tests\full_test.py
```

Each test starts its own COMSOL server on a random port and cleans up after itself.

## Configure your MCP client

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

`timeout` is in milliseconds. Keep it large so long solves are not cut off.

### Claude Desktop

```json
{
  "mcpServers": {
    "comsol": {
      "command": "C:\\path\\to\\comsol-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\comsol-mcp\\server.py"],
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

### DeepSeek Harness (DSH)

Add an entry to `home/cordis.patch.yml` (under `insert`):

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

Tools then appear as `mcp__comsol__*`. Restart DSH to pick up the change.

### Cline / other stdio clients

Use the same command and args as Claude Desktop. Any client that speaks
stdio MCP works; the server only writes logs to `logs/` and nothing to stdout
except protocol messages.

## Tools

| Tool | What it does |
|---|---|
| `comsol_status` | Connection state, port, loaded models, current model, background job status |
| `comsol_start` | Connect to an existing server or start one (default port 2036) |
| `comsol_stop` | Disconnect and optionally shut the server down |
| `comsol_open` | Open a `.mph` file or create a new model |
| `comsol_use` | Switch the active model when several are loaded |
| `comsol_save` | Save or save-as, with automatic backups |
| `comsol_inspect` | Structured model tree: parameters, geometry, physics, selections, materials, mesh, studies, results |
| `comsol_geometry` | Create/update/delete/build geometry features: Block, Cylinder, Sphere, Rectangle, Circle, Import, booleans |
| `comsol_physics` | Create physics interfaces and features, bind selections, set properties |
| `comsol_selection` | Named Box / Ball / Explicit selections, then reuse them in boundary conditions |
| `comsol_diagnose` | Model health check: COMSOL-reported problems plus missing geometry/physics/material/mesh/study |
| `comsol_help` | Offline cheat sheets for geometry, heat, solid, flow, electrostatics, selection, mesh |
| `comsol_material` | Materials from built-in presets (copper/aluminum/steel/silicon/water/air) or custom properties |
| `comsol_params` | Read or set global parameters (`200[A]`, `1.5[mm]`, ...) |
| `comsol_sweep` | Add a parametric sweep (parameter names + value lists, units inferred) |
| `comsol_mesh` | Build the mesh, optional density level 1–9 |
| `comsol_solve` | Run a study, foreground or background |
| `comsol_eval` | Evaluate an expression, return min/max/mean |
| `comsol_plot` | Render a surface plot of any expression and export PNG |
| `comsol_export` | Export field values (with x/y/z coordinates) to CSV |
| `comsol_model` | List, remove from memory, or rename loaded models |
| `comsol_python` | Execute Python against the COMSOL Java API |
| `comsol_desktop` | Launch COMSOL Desktop and connect it to the running server |
| `comsol_screenshot` | Capture the COMSOL window so the assistant can see the current UI |

## Live desktop sharing

COMSOL supports a client–server mode where the server owns the model and Desktop is just a viewer.

1. Ask the assistant to run `comsol_start`. A server starts on port 2036.
2. Launch the client:
   ```powershell
   .\start_client.ps1
   ```
   or
   ```powershell
   & "<COMSOL_ROOT>\bin\win64\comsolmphclient.exe" -server localhost -port 2036
   ```
3. Click **Connect** in the dialog.
4. Open the same model from the server's file list.

Edits made through MCP now appear in the Desktop tree as they happen.

> If you prefer not to share a session, skip the Desktop part: MCP opens the `.mph` file independently and saves results you can reopen.

## Examples

`examples/heat_transfer_2d.py` builds a 2D steady-state heat transfer model from scratch, solves it, evaluates the temperature field, and exports a PNG — a minimal end-to-end script using the same API the tools call.

```powershell
python examples\heat_transfer_2d.py
```

## Escape hatch

Anything the 24 tools do not cover can be done through `comsol_python`, which exposes `model`, `java`, `client`, `mph`, `np`:

```python
ht = java.modelNode('comp1').physics('ht')
flux = ht.create('hf1', 'HeatFluxBoundary', 1)
flux.selection().set([2])
flux.set('q0', '1e4[W/m^2]')
```

Useful Java API entry points:

- Geometry: `java.modelNode('comp1').geom('geom1')`
- Physics: `java.modelNode('comp1').physics('ht')`
- Materials: `java.modelNode('comp1').material('mat1').propertyGroup('def')`
- Mesh: `java.modelNode('comp1').mesh('mesh1')`
- Study: `java.study('std1')`
- Results: `java.result()`

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `COMSOL_ROOT` | auto-detected | COMSOL installation root |
| `COMSOL_PORT` | `2036` | Default server port |
| `COMSOL_MCP_HOME` | current directory | Where `logs/` and `out/` are written |
| `COMSOL_MCP_IDLE_TIMEOUT` | `1800` | Exit after this many idle seconds; `0` disables. Prevents stale processes piling up when a client disconnects. |

## Limitations

- One COMSOL client per process: after `comsol_stop`, the MCP process must be restarted before connecting again (JVM limitation).
- Cancelling a running solve is not exposed by the COMSOL Java API; a long solve must finish, or the COMSOL process must be restarted.
- Progress reporting during solve is coarse — the job is either running or finished, no percentage.
- `comsol_screenshot` brings the COMSOL window to the foreground before capturing; it will briefly steal focus.
- Windows-specific helpers: screenshot, and launching Desktop. Everything else works wherever MPh works.

## Acknowledgements

- [MPh](https://github.com/MPh-py/MPh) — the Python bridge to the COMSOL Java API that makes all of this possible.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — server framework.
- Prior art that informed the tool design: [wjc9011/COMSOL_Multiphysics_MCP](https://github.com/wjc9011/COMSOL_Multiphysics_MCP) and [Ching-Chiang/comsol-mcp](https://github.com/Ching-Chiang/comsol-mcp). Their generic "feature type + properties" pattern is what our `comsol_geometry` / `comsol_physics` tools follow.

## Self-check

The 0.3.0 release went through a three-perspective review (user / designer / engineer).
See [docs/SELF-CHECK.md](docs/SELF-CHECK.md) for the checklist and what was fixed.

## Disclaimer

This project is not affiliated with, endorsed by, or supported by COMSOL AB. "COMSOL" and "COMSOL Multiphysics" are trademarks of COMSOL AB. You need your own valid COMSOL license to use this software.

## License

MIT. See [LICENSE](LICENSE).
