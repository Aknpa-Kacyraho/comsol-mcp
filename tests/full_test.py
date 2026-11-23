"""全量回归：24 个工具、边界情况、错误处理。

独立进程、随机端口，不影响正在运行的会话。

    python tests/full_test.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOME = Path(tempfile.mkdtemp(prefix="comsol-mcp-full-"))
os.environ["COMSOL_MCP_HOME"] = str(HOME)
os.environ["COMSOL_MCP_IDLE_TIMEOUT"] = "0"

import comsol_mcp.server as s

FAILED = []


def call(tool, **kwargs):
    raw = getattr(s, tool)(**kwargs)
    data = json.loads(raw)
    print(f"[{tool}] {json.dumps(data, ensure_ascii=False)[:240]}")
    return data


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label} {detail}")
    if not condition:
        FAILED.append(label)


def main():
    t0 = time.time()

    st = call("comsol_status")
    check("status before start", st["connected"] is False)

    started = call("comsol_start", port=0, cores=2)
    check("started", started.get("connected") is True, f"port={started.get('port')}")

    created = call("comsol_open", new=True, name="full_test")
    check("new model", created.get("name") == "full_test")

    # --- 几何 ---
    geo = call(
        "comsol_geometry",
        action="create",
        feature_type="Block",
        tag="blk1",
        properties={"size": ["0.1", "0.1", "0.1"], "pos": ["0", "0", "0"]},
        dimension=3,
    )
    check("geometry created", geo.get("created") == "blk1")

    geo2 = call(
        "comsol_geometry",
        action="create",
        feature_type="Block",
        tag="blk2",
        properties={"size": ["0.05", "0.05", "0.05"], "pos": ["0.1", "0", "0"]},
    )
    check("second block", geo2.get("created") == "blk2")

    built = call("comsol_geometry", action="run")
    check("geometry built", (built.get("domains") or 0) == 2, f"domains={built.get('domains')}")

    geo_list = call("comsol_geometry", action="list")
    check("geometry listed", len(geo_list.get("geometries", [])) == 1)

    # --- 选择 ---
    top = call(
        "comsol_selection",
        action="create",
        tag="box_top",
        selection_type="Box",
        entity_dim=2,
        box=[-0.01, 0.11, -0.01, 0.11, 0.099, 0.101],
    )
    check("box selection", len(top.get("entities") or []) == 1, f"{top.get('entities')}")

    ball = call(
        "comsol_selection",
        action="create",
        tag="ball_mid",
        selection_type="Ball",
        entity_dim=3,
        center=[0.05, 0.05, 0.05],
        radius=0.03,
    )
    check("ball selection", len(ball.get("entities") or []) == 1, f"{ball.get('entities')}")

    sel_list = call("comsol_selection", action="list")
    check("selections listed", len(sel_list.get("selections", [])) == 2)

    # --- 物理场 ---
    iface = call("comsol_physics", action="add_interface", physics_type="HeatTransfer", tag="ht")
    check("physics interface", iface.get("interface") == "ht")

    hot = call(
        "comsol_physics",
        action="add_feature",
        physics="ht",
        feature_type="TemperatureBoundary",
        tag="temp_hot",
        named_selection="box_top",
        properties={"T0": "373.15[K]"},
    )
    check("hot boundary", (hot.get("selection") or []) == top.get("entities"), f"{hot.get('selection')}")

    # --- 材料 ---
    mat = call("comsol_material", action="create", preset="copper", tag="mat1")
    check("material preset", len(mat.get("properties_set") or []) >= 3)

    mat_list = call("comsol_material", action="list")
    check("material listed", len(mat_list.get("materials", [])) == 1)

    # --- 网格 ---
    mesh = call("comsol_mesh", size=5)
    check("mesh", (mesh.get("elements") or 0) > 0, f"elements={mesh.get('elements')}")

    # --- 参数与扫描 ---
    params = call("comsol_params", values={"T_top": "373.15[K]", "T_bot": "293.15[K]"})
    check("params set", "T_top" in (params.get("parameters") or {}))

    s.comsol_python(
        code=(
            "java.study().create('std1').create('stat', 'Stationary')\n'ok'"
        )
    )
    sweep = call(
        "comsol_sweep",
        action="add",
        parameters=["T_top"],
        values=["350 360 370"],
    )
    check("sweep added", sweep.get("parameters") == ["T_top"], f"moved={sweep.get('moved_first')}")

    sweep_list = call("comsol_sweep", action="list")
    param_types = [
        f["type"]
        for study in sweep_list.get("studies", [])
        for f in study.get("features", [])
    ]
    check("sweep listed", "Parametric" in param_types, f"{param_types}")

    diag = call("comsol_diagnose")
    check("diagnose", diag.get("ok") is True, f"{diag.get('checks')}")

    # --- 求解 ---
    solved = call("comsol_solve")
    check("solved", "elapsed_s" in solved, f"{solved.get('elapsed_s')}s")

    ev = call("comsol_eval", expression="T", unit="degC")
    check("eval", ev.get("max", 0) > ev.get("min", 0), f"{ev.get('min')}..{ev.get('max')}")

    # --- 结果 ---
    plot = call("comsol_plot", expression="T", unit="degC", title="Full test")
    check("plot", plot.get("exists") is True)

    export = call("comsol_export", expression="T", file="full_values.csv", unit="degC")
    check("export rows", export.get("rows", 0) > 0)
    check("export columns", "x" in (export.get("columns") or []), f"{export.get('columns')}")

    # --- 模型管理 ---
    models = call("comsol_model", action="list")
    check("model list", len(models.get("models", [])) == 1)

    renamed = call("comsol_model", action="rename", name="full_test", new_name="full_test_renamed")
    check("model rename", renamed.get("renamed") == "full_test_renamed")

    saved = call("comsol_save", path="full_test.mph")
    check("save", Path(saved.get("saved", "")).exists())

    inspect = call("comsol_inspect")
    comps = inspect.get("components", [])
    check("inspect selections", bool(comps and comps[0].get("selections")), f"{comps[0].get('selections') if comps else None}")

    # --- 帮助与错误处理 ---
    help_text = call("comsol_help", topic="heat")
    check("help", "TemperatureBoundary" in help_text.get("cheatsheet", ""))

    missing = call("comsol_open", path="no_such_model.mph")
    check("missing file error", "error" in missing)

    bad_action = call("comsol_geometry", action="nonsense")
    check("bad action error", "error" in bad_action)

    print(f"\nfinished in {time.time()-t0:.1f}s")
    if FAILED:
        print(f"\n{len(FAILED)} failed: {FAILED}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try:
            s.comsol_stop(stop_server=True)
        except Exception:
            pass
