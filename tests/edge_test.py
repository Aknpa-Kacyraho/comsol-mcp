"""边界情况测试：未连接/无模型/无解/重复打开/删除当前模型。

独立进程、随机端口。
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOME = Path(tempfile.mkdtemp(prefix="comsol-mcp-edge-"))
os.environ["COMSOL_MCP_HOME"] = str(HOME)
os.environ["COMSOL_MCP_IDLE_TIMEOUT"] = "0"

import comsol_mcp.server as s  # noqa: E402

FAILED = []


def call(tool, **kwargs):
    raw = getattr(s, tool)(**kwargs)
    data = json.loads(raw)
    print(f"[{tool}] {json.dumps(data, ensure_ascii=False)[:220]}")
    return data


def check(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label} {detail}")
    if not condition:
        FAILED.append(label)


def main():
    t0 = time.time()

    # 未连接时的行为
    st = call("comsol_status")
    check("hint when disconnected", "comsol_start" in st.get("hint", ""))

    insp = call("comsol_inspect")
    check("inspect without connection", "error" in insp)

    started = call("comsol_start", port=0, cores=2)
    check("started", started.get("connected") is True)

    # 已连接但没有模型
    ev = call("comsol_eval", expression="T")
    check("eval without model", "error" in ev)

    mesh = call("comsol_mesh")
    check("mesh without model", "error" in mesh)

    # 新建并保存
    call("comsol_open", new=True, name="edge")
    saved = call("comsol_save", path="edge.mph")
    check("saved temp model", Path(saved.get("saved", "")).exists())

    # 无解时求值
    ev2 = call("comsol_eval", expression="T")
    check("eval without solution", "error" in ev2, ev2.get("error", "")[:60])

    # 重复打开同一文件应复用
    reopened = call("comsol_open", path="edge.mph")
    check("reopen reuses model", reopened.get("reused") is True, f"tag={reopened.get('tag')}")

    open_models = call("comsol_model", action="list")
    check("no duplicate model", len(open_models.get("models", [])) == 1, f"{len(open_models.get('models', []))}")

    # 再建一个模型，然后删掉当前的
    call("comsol_open", new=True, name="edge2")
    models = call("comsol_model", action="list")
    check("two models", len(models.get("models", [])) == 2)

    removed = call("comsol_model", action="remove", name="edge2")
    check("remove current falls back", removed.get("current") == "edge", f"{removed.get('current')}")

    # 非法参数
    bad = call("comsol_params", values={"p1": "not_a_value[[["})
    check("invalid parameter handled", "error" in bad or "p1" in json.dumps(bad))

    bad_geo = call("comsol_geometry", action="create", feature_type="NoSuchFeature")
    check("invalid feature type", "error" in bad_geo)

    # 空模型做诊断
    diag = call("comsol_diagnose")
    check("diagnose empty model", diag.get("ok") is False and diag.get("checks"), f"{diag.get('checks')}")

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
