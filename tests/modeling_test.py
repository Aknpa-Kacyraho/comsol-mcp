"""验证通用建模工具：几何 / 选择 / 物理场 / 诊断 / 速查。

独立进程、随机端口，不影响正在运行的会话。
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOME = Path(tempfile.mkdtemp(prefix="comsol-mcp-modeling-"))
os.environ["COMSOL_MCP_HOME"] = str(HOME)

import comsol_mcp.server as s  # noqa: E402

FAILED = []


def call(tool, **kwargs):
    raw = getattr(s, tool)(**kwargs)
    data = json.loads(raw)
    print(f"[{tool}] {json.dumps(data, ensure_ascii=False)[:280]}")
    return data


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label} {detail}")
    if not condition:
        FAILED.append(label)


def main():
    t0 = time.time()

    call("comsol_start", port=0, cores=2)
    call("comsol_open", new=True, name="modeling_test")

    geo = call(
        "comsol_geometry",
        action="create",
        feature_type="Block",
        tag="blk1",
        properties={"size": ["0.1", "0.1", "0.1"], "pos": ["0", "0", "0"]},
        dimension=3,
    )
    check("geometry created", geo.get("created") == "blk1")

    built = call("comsol_geometry", action="run")
    check("geometry built", (built.get("domains") or 0) == 1, f"domains={built.get('domains')}")

    listed = call("comsol_geometry", action="list")
    check("geometry listed", len(listed.get("geometries", [])) == 1)

    top = call(
        "comsol_selection",
        action="create",
        tag="box_top",
        selection_type="Box",
        entity_dim=2,
        box=[-0.01, 0.11, -0.01, 0.11, 0.099, 0.101],
    )
    check("top selection", len(top.get("entities") or []) == 1, f"{top.get('entities')}")

    bottom = call(
        "comsol_selection",
        action="create",
        tag="box_bottom",
        selection_type="Box",
        entity_dim=2,
        box=[-0.01, 0.11, -0.01, 0.11, -0.001, 0.001],
    )
    check("bottom selection", len(bottom.get("entities") or []) == 1, f"{bottom.get('entities')}")

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
    check("hot boundary", len(hot.get("selection") or []) == 1, f"{hot.get('selection')}")

    cold = call(
        "comsol_physics",
        action="add_feature",
        physics="ht",
        feature_type="TemperatureBoundary",
        tag="temp_cold",
        named_selection="box_bottom",
        properties={"T0": "293.15[K]"},
    )
    check("cold boundary", len(cold.get("selection") or []) == 1, f"{cold.get('selection')}")

    plist = call("comsol_physics", action="list")
    check("physics listed", len(plist.get("physics", [])) == 1)

    s.comsol_python(
        code=(
            "mat = java.modelNode('comp1').material().create('mat1', 'Common')\n"
            "mat.propertyGroup('def').set('thermalconductivity', '400')\n"
            "mat.propertyGroup('def').set('density', '8960')\n"
            "mat.propertyGroup('def').set('heatcapacity', '385')\n"
            "java.study().create('std1').create('stat', 'Stationary')\n"
            "'ok'"
        )
    )

    mesh = call("comsol_mesh", size=5)
    check("mesh", (mesh.get("elements") or 0) > 0, f"elements={mesh.get('elements')}")

    diag = call("comsol_diagnose")
    check("diagnose clean", diag.get("ok") is True, f"checks={diag.get('checks')}")

    help_text = call("comsol_help", topic="heat")
    check("help", "TemperatureBoundary" in help_text.get("cheatsheet", ""))

    solved = call("comsol_solve")
    check("solved", "elapsed_s" in solved, f"{solved.get('elapsed_s')}s")

    ev = call("comsol_eval", expression="T", unit="degC")
    check("temperature gradient", ev.get("max", 0) > ev.get("min", 0), f"{ev.get('min')}..{ev.get('max')}")

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
