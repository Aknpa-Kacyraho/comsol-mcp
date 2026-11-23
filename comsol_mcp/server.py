#!/usr/bin/env python
"""COMSOL Multiphysics MCP server.

通过 mph (MPh) + JPype 驱动本机 COMSOL 6.4，把建模、求解、出图、
结果导出暴露成 MCP 工具，供 opencode 之类的 MCP 客户端调用。

设计要点
--------
* COMSOL 的 Java API 只能在一个线程里使用，所有调用都排队进同一个
  worker 线程，避免 JPype/COMSOL 的线程问题。
* 后台求解不阻塞状态查询：求解作业提交给 worker，状态从共享状态读取。
* 默认连接 2036 端口（COMSOL 默认端口），这样 COMSOL Desktop 也能
  通过 File > COMSOL Multiphysics Server > Connect to Server 连上同一个
  server，看到并操作同一个模型。
"""

from __future__ import annotations

import ast
import csv
import json
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

BASE = Path(os.environ.get("COMSOL_MCP_HOME", Path.cwd())).resolve()
LOG_DIR = BASE / "logs"
OUT_DIR = BASE / "out"
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "comsol-mcp.log"),
    level=logging.INFO,
    encoding="utf-8",
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("comsol-mcp")

# COMSOL 安装根目录：优先环境变量，其次通过 mph 自动发现注册表里的安装
def _comsol_root() -> Path | None:
    env = os.environ.get("COMSOL_ROOT")
    if env:
        return Path(env)
    try:
        from mph import discovery

        return Path(discovery.backend()["root"])
    except Exception:
        return None


COMSOL_ROOT = _comsol_root()
DEFAULT_PORT = int(os.environ.get("COMSOL_PORT", "2036"))
# 空闲多久后自动退出（秒）。0 表示禁用。避免客户端遗弃后进程堆积。
IDLE_TIMEOUT = int(os.environ.get("COMSOL_MCP_IDLE_TIMEOUT", "1800"))


def _comsol_exe(name: str = "comsol.exe") -> Path:
    root = COMSOL_ROOT or Path(".")
    return root / "bin" / "win64" / name

import mph  # noqa: E402  (导入较慢，放在日志配置之后)

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer(
    "comsol",
    instructions=(
        "驱动本机 COMSOL Multiphysics。典型流程：comsol_start -> comsol_open "
        "-> comsol_inspect -> comsol_params/comsol_python 改模型 -> comsol_mesh "
        "-> comsol_solve -> comsol_eval/comsol_plot。"
    ),
)


# --------------------------------------------------------------------------
# 共享状态
# --------------------------------------------------------------------------

STATE: dict[str, Any] = {
    "connected": False,
    "host": None,
    "port": None,
    "version": None,
    "models": [],
    "current": None,
    "busy": False,
    "job": None,
    "job_started": None,
    "last_error": None,
    "last_solve": None,
    "started_by_us": False,
    "stopped": False,
}
STATE_LOCK = threading.Lock()


def _snapshot() -> dict[str, Any]:
    with STATE_LOCK:
        return dict(STATE)


def _set(**kwargs: Any) -> None:
    with STATE_LOCK:
        STATE.update(kwargs)


# --------------------------------------------------------------------------
# Worker 线程：所有 COMSOL 调用都在这条线程上串行执行
# --------------------------------------------------------------------------


class Worker:
    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="comsol-worker", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, box, done = item
            try:
                box["result"] = fn()
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc
                box["traceback"] = traceback.format_exc()
            finally:
                done.set()

    def call(self, fn, timeout: float | None = None):
        _touch()
        if threading.current_thread() is self._thread:
            return fn()
        box: dict[str, Any] = {}
        done = threading.Event()
        self._queue.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError(
                "COMSOL 操作超时；任务可能仍在后台执行，可用 comsol_status 查看。"
            )
        if "error" in box:
            raise box["error"]
        return box["result"]

    def submit(self, fn) -> None:
        """把任务丢进队列，不等待结果。"""
        box: dict[str, Any] = {}
        done = threading.Event()
        self._queue.put((fn, box, done))


WORKER = Worker()

# 最后一次工具调用时间：用于空闲退出，防止客户端断开后进程堆积
LAST_ACTIVITY = [time.time()]


def _touch() -> None:
    LAST_ACTIVITY[0] = time.time()


def _idle_watchdog() -> None:
    interval = 30 if IDLE_TIMEOUT <= 0 else max(1, min(30, IDLE_TIMEOUT))
    while True:
        time.sleep(interval)
        if IDLE_TIMEOUT <= 0:
            continue
        if time.time() - LAST_ACTIVITY[0] > IDLE_TIMEOUT:
            log.info("Idle for %ss, exiting to avoid a stale process.", IDLE_TIMEOUT)
            os._exit(0)


threading.Thread(target=_idle_watchdog, name="idle-watchdog", daemon=True).start()

# worker 线程里持有的真实对象
CLIENT = None
MODEL = None


# --------------------------------------------------------------------------
# 辅助函数
# --------------------------------------------------------------------------


def _json(data: Any, indent: int = 2) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent, default=_default)


def _default(obj: Any) -> Any:
    try:
        if np is not None and isinstance(obj, np.generic):
            return obj.item()
        if np is not None and isinstance(obj, np.ndarray):
            return obj.tolist()
        return str(obj)
    except Exception:
        return repr(obj)


def _resolve(path: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(str(path))))
    if not p.is_absolute():
        p = (BASE / p).resolve()
    return p


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def _need_client():
    if CLIENT is None:
        raise RuntimeError(
            "尚未连接 COMSOL。先调用 comsol_start（或 comsol_open，会自动启动）。"
        )
    return CLIENT


def _need_model():
    _need_client()
    if MODEL is None:
        raise RuntimeError("没有打开的模型。先调用 comsol_open 或 comsol_python 新建。")
    return MODEL


def _java():
    return _need_model().java


def _tags(node) -> list[str]:
    try:
        return [str(t) for t in node.tags()]
    except Exception:
        return []


def _label(node) -> str:
    try:
        return str(node.label())
    except Exception:
        return ""


def _safe(fn, **kwargs):
    """执行并忽略异常，用于取属性。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if kwargs.get("raise"):
            raise
        return None


def _status_dict() -> dict[str, Any]:
    with STATE_LOCK:
        data = dict(STATE)
        data["models"] = list(STATE["models"])
    if CLIENT is not None:
        try:
            data["models"] = [
                {"name": m.name(), "tag": str(m.java.tag())} for m in CLIENT.models()
            ]
            if MODEL is not None:
                data["current"] = {
                    "name": MODEL.name(),
                    "tag": str(MODEL.java.tag()),
                    "file": str(MODEL.file()) if _has_file(MODEL) else None,
                }
        except Exception as exc:  # noqa: BLE001
            data["state_error"] = str(exc)
    return data


def _has_file(model) -> bool:
    try:
        text = str(model.java.getFilePath())
        return bool(text) and text not in ("", "null")
    except Exception:
        return False


# --------------------------------------------------------------------------
# 连接管理
# --------------------------------------------------------------------------


def _connect_or_start(port: int, cores: int | None, version: str | None):
    global CLIENT
    if CLIENT is not None:
        return CLIENT

    if _snapshot().get("stopped"):
        raise RuntimeError(
            "本次 MCP 进程的 COMSOL 连接已停止，JVM 无法重启。"
            "请重启 opencode 中的 comsol MCP 服务（或重启 opencode）后再连接。"
        )

    if _port_open(port):
        log.info("Connecting to existing COMSOL server on port %s", port)
        CLIENT = mph.Client(port=port)
        _set(started_by_us=False, host="localhost", port=port)
    else:
        log.info("Starting COMSOL server on port %s", port)
        CLIENT = mph.start(cores=cores, port=port, version=version)
        _set(started_by_us=True, host="localhost", port=port)

    _set(
        connected=True,
        version=CLIENT.version,
        port=getattr(CLIENT, "port", None) or port,
        models=[],
    )
    return CLIENT


@mcp.tool(
    description=(
        "查看 COMSOL 连接状态：是否已连接、端口、COMSOL 版本、已加载模型、"
        "当前模型、是否正在后台求解、上次错误。不会启动任何东西。"
    )
)
def comsol_status() -> str:
    return _json(_status_dict())


@mcp.tool(
    description=(
        "连接或启动 COMSOL 后端。默认端口 2036：如果该端口已有 server 就连接它"
        "（例如 Desktop 已连上的 server），否则启动一个新的 server。"
        "cores 为 0 表示使用全部核心。首次启动约 10-20 秒。"
    )
)
def comsol_start(port: int = DEFAULT_PORT, cores: int = 0, version: str = "") -> str:
    def job():
        client = _connect_or_start(port, cores or None, version or None)
        return {
            "connected": True,
            "port": _snapshot().get("port") or port,
            "version": client.version,
            "started_by_us": _snapshot().get("started_by_us"),
            "models": [m.name() for m in client.models()],
        }

    try:
        return _json(WORKER.call(job, timeout=180))
    except Exception as exc:  # noqa: BLE001
        _set(last_error=str(exc))
        return _json({"error": str(exc)})


@mcp.tool(
    description=(
        "断开并停止 COMSOL 后端。默认只断开当前客户端；stop_server=true 时"
        "同时关闭由本工具启动的 server 进程。Desktop 若正连着该 server 会被断开。"
    )
)
def comsol_stop(stop_server: bool = True) -> str:
    global CLIENT, MODEL

    def job():
        global CLIENT, MODEL
        info: dict[str, Any] = {}
        if CLIENT is not None:
            try:
                CLIENT.disconnect()
            except Exception as exc:  # noqa: BLE001
                info["disconnect_error"] = str(exc)
            CLIENT = None
            MODEL = None
        if stop_server:
            try:
                import mph.session as session

                server = getattr(session, "server", None)
                if server is not None:
                    server.stop()
                    session.server = None
                    info["server_stopped"] = True
                else:
                    info["server_stopped"] = False
            except Exception as exc:  # noqa: BLE001
                info["server_error"] = str(exc)
        _set(
            connected=False,
            host=None,
            port=None,
            models=[],
            current=None,
            busy=False,
            job=None,
            stopped=True,
        )
        return info

    try:
        return _json(WORKER.call(job, timeout=60))
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------


@mcp.tool(
    description=(
        "打开一个 .mph 模型，或新建空模型（new=true）。path 支持中文和相对路径"
        "（相对 comsol-bridge 目录）。打开后该模型成为当前操作对象。"
        "对于大模型（>50MB）加载可能需要几十秒。"
    )
)
def comsol_open(path: str = "", new: bool = False, name: str = "") -> str:
    global MODEL

    def job():
        global MODEL
        client = _need_client() if CLIENT is not None else _connect_or_start(
            DEFAULT_PORT, None, None
        )
        if new:
            MODEL = client.create(name or "Model")
            source = None
        else:
            if not path:
                raise ValueError("new=false 时必须提供 path。")
            target = _resolve(path)
            if not target.exists():
                raise FileNotFoundError(f"找不到模型文件：{target}")
            MODEL = client.load(str(target))
            source = str(target)

        _set(models=[m.name() for m in client.models()])
        info = {
            "name": MODEL.name(),
            "tag": str(MODEL.java.tag()),
            "file": source,
            "components": MODEL.components(),
        }
        return info

    try:
        return _json(WORKER.call(job, timeout=600))
    except Exception as exc:  # noqa: BLE001
        _set(last_error=str(exc))
        return _json({"error": str(exc)})


@mcp.tool(
    description=(
        "切换当前操作的模型。model 传模型名或 tag（见 comsol_status 的 models 列表）。"
        "一个后端可以同时加载多个 .mph，用这个工具指定后续操作作用于哪一个。"
    )
)
def comsol_use(model: str) -> str:
    def job():
        global MODEL
        client = _need_client()
        target = None
        for item in client.models():
            if model in (item.name(), str(item.java.tag())):
                target = item
                break
        if target is None:
            names = [f"{m.name()}({m.java.tag()})" for m in client.models()]
            raise ValueError(f"找不到模型 {model}，现有：{names}")
        MODEL = target
        return {
            "current": MODEL.name(),
            "tag": str(MODEL.java.tag()),
            "file": str(MODEL.file()) if _has_file(MODEL) else None,
        }

    try:
        return _json(WORKER.call(job, timeout=60))
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


@mcp.tool(
    description=(
        "保存当前模型。path 为空时存回原文件；提供 path 时另存为。"
        "backup=true 时覆盖前会把旧文件备份为 .bak-时间戳，防止误覆盖。"
    )
)
def comsol_save(path: str = "", backup: bool = True) -> str:
    def job():
        model = _need_model()
        target = _resolve(path) if path else None
        if target is None:
            if not _has_file(model):
                raise ValueError("模型尚未保存过，请提供 path。")
            target = Path(str(model.file()))
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if backup and target.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup_path = target.with_name(f"{target.stem}.bak-{stamp}{target.suffix}")
            target.replace(backup_path)
        model.java.save(str(target))
        return {
            "saved": str(target),
            "backup": str(backup_path) if backup_path else None,
            "size_bytes": target.stat().st_size if target.exists() else None,
        }

    try:
        return _json(WORKER.call(job, timeout=300))
    except Exception as exc:  # noqa: BLE001
        _set(last_error=str(exc))
        return _json({"error": str(exc)})


@mcp.tool(
    description=(
        "查看当前模型的完整结构：参数、组件与维度、几何特征、物理场及其边界条件、"
        "材料、网格、研究、求解结果与数据集。做任何修改前先用它确认标签。"
    )
)
def comsol_inspect() -> str:
    def job():
        model = _need_model()
        j = model.java
        data: dict[str, Any] = {
            "name": model.name(),
            "tag": str(j.tag()),
            "version": _safe(model.version),
            "file": str(model.file()) if _has_file(model) else None,
            "parameters": _safe(lambda: model.parameters()) or {},
            "components": [],
            "studies": [],
            "solutions": _safe(model.solutions) or [],
            "datasets": _safe(model.datasets) or [],
            "results": [],
            "exports": [],
        }

        for ctag in _tags(j.modelNode()):
            comp_node = j.modelNode(ctag)
            comp: dict[str, Any] = {"tag": ctag, "label": _label(comp_node)}
            geoms = []
            for gtag in _tags(comp_node.geom()):
                g = comp_node.geom(gtag)
                geoms.append(
                    {
                        "tag": gtag,
                        "label": _label(g),
                        "dim": _safe(g.getSDim),
                        "domains": _safe(g.getNDomains),
                        "boundaries": _safe(g.getNBoundaries),
                        "features": [
                            {"tag": t, "type": _safe(lambda t=t: str(g.feature(t).getType()))}
                            for t in _tags(g.feature())
                        ],
                    }
                )
            comp["geometry"] = geoms

            physics = []
            for ptag in _tags(comp_node.physics()):
                p = comp_node.physics(ptag)
                feats = []
                for ftag in _tags(p.feature()):
                    f = p.feature(ftag)
                    ent = _safe(lambda: [int(x) for x in f.selection().entities()])
                    feats.append(
                        {
                            "tag": ftag,
                            "type": _safe(lambda f=f: str(f.getType())),
                            "selection": ent,
                        }
                    )
                physics.append(
                    {
                        "tag": ptag,
                        "type": _safe(lambda p=p: str(p.getType())),
                        "label": _label(p),
                        "selection": _safe(
                            lambda p=p: [int(x) for x in p.selection().entities()]
                        ),
                        "features": feats,
                    }
                )
            comp["physics"] = physics

            comp["materials"] = [
                {"tag": t, "label": _label(comp_node.material(t))}
                for t in _tags(comp_node.material())
            ]
            comp["meshes"] = []
            for mtag in _tags(comp_node.mesh()):
                m = comp_node.mesh(mtag)
                comp["meshes"].append(
                    {
                        "tag": mtag,
                        "elements": _safe(m.getNumElem),
                        "vertices": _safe(m.getNumVertex),
                    }
                )
            data["components"].append(comp)

        for stag in _tags(j.study()):
            s = j.study(stag)
            data["studies"].append(
                {
                    "tag": stag,
                    "label": _label(s),
                    "features": [
                        {"tag": t, "type": _safe(lambda t=t: str(s.feature(t).getType()))}
                        for t in _tags(s.feature())
                    ],
                }
            )

        for rtag in _tags(j.result()):
            r = j.result(rtag)
            data["results"].append(
                {
                    "tag": rtag,
                    "label": _label(r),
                    "type": _safe(lambda r=r: str(r.getType())),
                }
            )
        for etag in _tags(j.result().export()):
            e = j.result().export(etag)
            data["exports"].append(
                {
                    "tag": etag,
                    "type": _safe(lambda e=e: str(e.getType())),
                }
            )

        return data

    try:
        return _json(WORKER.call(job, timeout=120))
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------


@mcp.tool(
    description=(
        "读取或设置全局参数。values 为 {名称: 值} 字典时设置参数（值可带单位，"
        "如 '200[A]'、'1.5[mm]'），并返回设置后的全部参数；values 为空时只读取。"
        "evaluate=true 时返回数值（带单位换算到 SI）。"
    )
)
def comsol_params(values: dict[str, str] | None = None, evaluate: bool = False) -> str:
    def job():
        model = _need_model()
        j = model.java
        if values:
            for k, v in values.items():
                j.param().set(str(k), _to_java_value(v))
        out = {}
        for name in j.param().varnames():
            key = str(name)
            try:
                out[key] = str(j.param().get(key))
            except Exception:
                out[key] = None
        if evaluate:
            evaluated = {}
            for key in list(out):
                try:
                    evaluated[key] = float(j.param().evaluate(key))
                except Exception:
                    evaluated[key] = out[key]
            out = evaluated
        return {"parameters": out, "changed": list(values or {})}

    try:
        return _json(WORKER.call(job, timeout=120))
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


def _to_java_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


# --------------------------------------------------------------------------
# 网格 / 求解
# --------------------------------------------------------------------------


@mcp.tool(
    description=(
        "生成网格。component 为空时用第一个组件；mesh 为空时用 mesh1（不存在则创建）。"
        "size 为 1-9 的自动网格密度（越大越细，0 表示不改），返回单元数与顶点数。"
    )
)
def comsol_mesh(component: str = "", mesh: str = "", size: int = 0) -> str:
    def job():
        model = _need_model()
        j = model.java
        ctag = component or (_tags(j.modelNode())[0] if _tags(j.modelNode()) else None)
        if not ctag:
            raise RuntimeError("模型里没有组件。")
        comp = j.modelNode(ctag)
        if not _tags(comp.mesh()):
            comp.mesh().create("mesh1")
        mtag = mesh or _tags(comp.mesh())[0]
        m = comp.mesh(mtag)
        if size:
            m.autoMeshSize(int(size))
        t0 = time.time()
        m.run()
        return {
            "component": ctag,
            "mesh": mtag,
            "size_level": size or None,
            "elements": _safe(m.getNumElem),
            "vertices": _safe(m.getNumVertex),
            "elapsed_s": round(time.time() - t0, 2),
        }

    try:
        return _json(WORKER.call(job, timeout=1800))
    except Exception as exc:  # noqa: BLE001
        _set(last_error=str(exc))
        return _json({"error": str(exc)})


@mcp.tool(
    description=(
        "运行研究（求解）。study 为空时用第一个研究。background=true 时立即返回，"
        "求解在后台进行，之后用 comsol_status 查看是否完成；适合耗时长的瞬态/参数扫描。"
    )
)
def comsol_solve(study: str = "", background: bool = False) -> str:
    def job():
        model = _need_model()
        j = model.java
        tags = _tags(j.study())
        if not tags:
            raise RuntimeError("模型里没有研究（study），请先创建。")
        tag = study or tags[0]
        if tag not in tags:
            raise ValueError(f"没有研究 {tag}，现有：{tags}")
        t0 = time.time()
        j.study(tag).run()
        return {
            "study": tag,
            "elapsed_s": round(time.time() - t0, 2),
            "solutions": _safe(model.solutions),
        }

    if background:
        if _snapshot().get("busy"):
            return _json({"error": "已有求解任务在跑，等它结束再提交。"})
        _set(busy=True, job=study or "auto", job_started=time.time(), last_error=None)

        def bg():
            t0 = time.time()
            try:
                result = job()
                _set(
                    busy=False,
                    job=None,
                    last_solve={
                        "ok": True,
                        "result": result,
                        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                _set(
                    busy=False,
                    job=None,
                    last_error=str(exc),
                    last_solve={
                        "ok": False,
                        "error": str(exc),
                        "elapsed_s": round(time.time() - t0, 2),
                        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )

        WORKER.submit(bg)
        return _json({"submitted": True, "study": study or "auto", "background": True})

    try:
        return _json(WORKER.call(job, timeout=7200))
    except Exception as exc:  # noqa: BLE001
        _set(last_error=str(exc))
        return _json({"error": str(exc)})


# --------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------


@mcp.tool(
    description=(
        "对表达式求值并给出统计量（min/max/mean/size）。expression 例如 'T'、"
        "'es.normE'、'solid.mises'；unit 可选，如 'degC'。dataset 为空时用默认数据集。"
    )
)
def comsol_eval(expression: str, unit: str = "", dataset: str = "") -> str:
    def job():
        model = _need_model()
        kwargs: dict[str, Any] = {}
        if unit:
            kwargs["unit"] = unit
        if dataset:
            kwargs["dataset"] = dataset
        data = model.evaluate(expression, **kwargs)
        if np is not None:
            arr = np.asarray(data, dtype=float)
            if arr.size == 1:
                return {"expression": expression, "value": float(arr.ravel()[0])}
            return {
                "expression": expression,
                "unit": unit or None,
                "size": int(arr.size),
                "min": float(np.nanmin(arr)),
                "max": float(np.nanmax(arr)),
                "mean": float(np.nanmean(arr)),
            }
        return {"expression": expression, "values": str(data)}

    try:
        return _json(WORKER.call(job, timeout=300))
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "dataset" in message.lower():
            message += "（模型可能还没有求解结果，先运行 comsol_solve）"
        return _json({"error": message})


@mcp.tool(
    description=(
        "画结果图并导出 PNG。expression 例如 'T'；unit 如 'degC'；title 为图标题。"
        "自动按模型维度创建 2D/3D 表面图，file 为空时输出到 out/ 目录。返回图片路径。"
    )
)
def comsol_plot(
    expression: str,
    unit: str = "",
    title: str = "",
    file: str = "",
    width: int = 900,
    height: int = 650,
) -> str:
    def job():
        model = _need_model()
        j = model.java
        dim = _model_dim(j)
        plot_type = "PlotGroup3D" if dim == 3 else "PlotGroup2D"
        pg_tag = "pg_mcp"
        if pg_tag in _tags(j.result()):
            j.result().remove(pg_tag)
        pg = j.result().create(pg_tag, plot_type)
        try:
            pg.label(title or expression)
        except Exception:  # 标签重名时忽略，不影响导出的图
            pass
        surf = pg.create("surf1", "Surface")
        surf.set("expr", expression)
        if unit:
            surf.set("unit", unit)
        pg.run()

        target = _resolve(file) if file else OUT_DIR / f"plot_{_slug(expression)}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        img_tag = "img_mcp"
        if img_tag in _tags(j.result().export()):
            j.result().export().remove(img_tag)
        img = j.result().export().create(img_tag, "Image")
        img.set("plotgroup", pg_tag)
        img.set("pngfilename", str(target))
        img.set("width", str(width))
        img.set("height", str(height))
        img.run()
        return {
            "expression": expression,
            "unit": unit or None,
            "plot_group": pg_tag,
            "image": str(target),
            "exists": target.exists(),
            "size_bytes": target.stat().st_size if target.exists() else None,
        }

    try:
        return _json(WORKER.call(job, timeout=600))
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


def _model_dim(j) -> int:
    for ctag in _tags(j.modelNode()):
        for gtag in _tags(j.modelNode(ctag).geom()):
            d = _safe(j.modelNode(ctag).geom(gtag).getSDim)
            if d:
                return int(d)
    return 3


def _slug(text: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in text)
    return keep[:60] or "plot"


@mcp.tool(
    description=(
        "把表达式在网格上的取值导出为 CSV（含索引与数值）。expression 如 'T'，"
        "file 为输出路径（相对 comsol-bridge 目录）。适合后续用 Python/Excel 分析。"
    )
)
def comsol_export(expression: str, file: str, unit: str = "") -> str:
    def job():
        model = _need_model()
        kwargs: dict[str, Any] = {}
        if unit:
            kwargs["unit"] = unit
        data = model.evaluate(expression, **kwargs)
        arr = np.asarray(data, dtype=float).ravel() if np is not None else [data]
        target = _resolve(file)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["index", expression])
            for i, value in enumerate(arr):
                writer.writerow([i, float(value)])
        return {
            "expression": expression,
            "unit": unit or None,
            "rows": int(len(arr)),
            "file": str(target),
            "size_bytes": target.stat().st_size,
        }

    try:
        return _json(WORKER.call(job, timeout=600))
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


# --------------------------------------------------------------------------
# Python / Java API 逃生舱
# --------------------------------------------------------------------------


@mcp.tool(
    description=(
        "在当前 COMSOL 会话里执行 Python 代码（COMSOL Java API 逃生舱）。"
        "可用变量：model(mph Model)、java(model.java，com.comsol.model.Model)、"
        "client、mph、np、json。最后一行若是表达式会作为返回值。"
        "示例：java.modelNode('comp1').physics().create('ht','HeatTransfer','geom1')"
    )
)
def comsol_python(code: str) -> str:
    def job():
        j = MODEL.java if MODEL is not None else None
        env = {
            "model": MODEL,
            "java": j,
            "client": CLIENT,
            "mph": mph,
            "np": np,
            "json": json,
            "Path": Path,
            "BASE": BASE,
        }
        tree = ast.parse(code)
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        exec(compile(tree, "<comsol_python>", "exec"), env)  # noqa: S102
        if last is not None:
            value = eval(compile(last, "<comsol_python>", "eval"), env)  # noqa: S307
            return {"result": _serialize(value)}
        return {"result": None}

    try:
        return _json(WORKER.call(job, timeout=3600))
    except Exception as exc:  # noqa: BLE001
        _set(last_error=str(exc))
        return _json({"error": str(exc), "traceback": traceback.format_exc()})


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if np is not None:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    try:
        return str(value)
    except Exception:
        return repr(value)


# --------------------------------------------------------------------------
# 桌面联动
# --------------------------------------------------------------------------


@mcp.tool(
    description=(
        "启动 COMSOL Desktop 并连接当前后端 server（client-server 模式），"
        "让用户能在图形界面里实时看到同一模型。open_file 可选，启动后打开该 .mph。"
        "注意：会弹出 Connect to Server 窗口，需要用户点一下 Connect。"
    )
)
def comsol_desktop(open_file: str = "") -> str:
    state = _snapshot()
    if not state.get("connected"):
        return _json({"error": "还没有连接后端，先运行 comsol_start。"})
    port = state.get("port") or DEFAULT_PORT
    client_exe = _comsol_exe("comsolmphclient.exe")
    if client_exe.exists():
        args = [str(client_exe), "-server", "localhost", "-port", str(port)]
    else:
        args = [
            str(_comsol_exe("comsol.exe")),
            "mphclient",
            "-server",
            "localhost",
            "-port",
            str(port),
        ]
    if open_file:
        args += ["-open", str(_resolve(open_file))]
    try:
        proc = subprocess.Popen(args, close_fds=True)
        return _json(
            {
                "launched": True,
                "pid": proc.pid,
                "port": port,
                "command": " ".join(args),
                "hint": "在弹窗里点 Connect；之后 Desktop 与 MCP 共享同一个模型。",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})


@mcp.tool(
    description=(
        "截取 COMSOL Desktop 窗口为 PNG，让 AI 看到用户当前的界面和模型。"
        "file 为空时输出到 out/ 目录。窗口未找到时返回错误。"
    )
)
def comsol_screenshot(file: str = "") -> str:
    try:
        import win32gui
        from PIL import ImageGrab
    except Exception as exc:  # noqa: BLE001
        return _json({"error": f"缺少截图依赖：{exc}"})

    handles: list[tuple[int, str]] = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if "COMSOL" in title:
            handles.append((hwnd, title))
        return True

    win32gui.EnumWindows(callback, None)
    if not handles:
        return _json({"error": "没有找到 COMSOL 主窗口。"})

    # 优先选非最小化、且标题里带 .mph 的窗口
    def score(item):
        hwnd, title = item
        minimized = 1 if win32gui.IsIconic(hwnd) else 0
        return (minimized, 0 if ".mph" in title else 1, -int(hwnd))

    hwnd, title = sorted(handles, key=score)[0]
    restored = False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
            restored = True
            time.sleep(0.8)
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
        except Exception:
            pass
        rect = win32gui.GetWindowRect(hwnd)
        img = ImageGrab.grab(bbox=rect, all_screens=True)
        target = _resolve(file) if file else OUT_DIR / f"desktop_{time.strftime('%H%M%S')}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        img.save(target)
        return _json(
            {
                "window": title,
                "image": str(target),
                "width": img.width,
                "height": img.height,
                "was_minimized": restored,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _json({"error": str(exc)})
    finally:
        if restored:
            try:
                win32gui.ShowWindow(hwnd, 6)  # SW_MINIMIZE，恢复用户原来的状态
            except Exception:
                pass


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def main() -> None:
    log.info("Starting COMSOL MCP server (base=%s)", BASE)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
