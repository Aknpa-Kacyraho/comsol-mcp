"""端到端冒烟测试：建模 -> 网格 -> 求解 -> 求值 -> 出图 -> 导出 -> 保存。

独立进程、随机端口，不会影响正在运行的 COMSOL 会话。

    python tests/smoke_test.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 让日志和产物落在临时目录，不污染仓库
HOME = Path(tempfile.mkdtemp(prefix="comsol-mcp-test-"))
os.environ["COMSOL_MCP_HOME"] = str(HOME)

import comsol_mcp.server as s  # noqa: E402

FAILED = []


def call(tool, **kwargs):
    fn = getattr(s, tool)
    raw = fn(**kwargs)
    data = json.loads(raw)
    print(f"[{tool}] {json.dumps(data, ensure_ascii=False)[:300]}")
    return data


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label} {detail}")
    if not condition:
        FAILED.append(label)


def main():
    t0 = time.time()
    print(f"work dir: {HOME}")

    st = call("comsol_status")
    check("status before start", st["connected"] is False)

    started = call("comsol_start", port=0, cores=2)
    check("server started", started.get("connected") is True, f"port={started.get('port')}")

    created = call("comsol_open", new=True, name="smoke")
    check("model created", created.get("name") == "smoke")

    build = call(
        "comsol_python",
        code="""
j = java
j.modelNode().create('comp1')
j.modelNode('comp1').geom().create('geom1', 2)
g = j.modelNode('comp1').geom('geom1')
r = g.create('r1', 'Rectangle')
r.set('size', ['0.1', '0.2'])
g.run()
ht = j.modelNode('comp1').physics().create('ht', 'HeatTransfer', 'geom1')
mat = j.modelNode('comp1').material().create('mat1', 'Common')
mat.propertyGroup('def').set('thermalconductivity', '400')
mat.propertyGroup('def').set('density', '8960')
mat.propertyGroup('def').set('heatcapacity', '385')
b1 = j.modelNode('comp1').physics('ht').create('temp1', 'TemperatureBoundary', 1)
b1.selection().set([1])
b1.set('T0', '373.15[K]')
b2 = j.modelNode('comp1').physics('ht').create('temp2', 'TemperatureBoundary', 1)
b2.selection().set([3])
b2.set('T0', '293.15[K]')
j.study().create('std1').create('stat', 'Stationary')
'built'
""",
    )
    check("model built", build.get("result") == "built")

    mesh = call("comsol_mesh", size=5)
    check("mesh generated", (mesh.get("elements") or 0) > 0, f"elements={mesh.get('elements')}")

    solved = call("comsol_solve")
    check("solved", "elapsed_s" in solved, f"{solved.get('elapsed_s')}s")

    ev = call("comsol_eval", expression="T", unit="degC")
    check("eval range", ev.get("max", 0) > ev.get("min", 0), f"{ev.get('min')}..{ev.get('max')}")

    plot = call("comsol_plot", expression="T", unit="degC")
    check("plot exported", plot.get("exists") is True)

    export = call("comsol_export", expression="T", file="values.csv", unit="degC")
    check("csv exported", export.get("rows", 0) > 0)

    saved = call("comsol_save", path="smoke_out.mph")
    check("model saved", Path(saved.get("saved", "")).exists())

    print(f"\nfinished in {time.time()-t0:.1f}s")

    if FAILED:
        print(f"\n{len(FAILED)} check(s) failed: {FAILED}")
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
