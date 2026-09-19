"""FastAPI 本地服务：配置、项目创建、阶段启动、SSE 日志流。

单用户本地软件：运行时按 PROJECT_ROOT 注册 Runtime（事件队列 + 工作线程锁）。
manifest 永远从磁盘即时读取（引擎是唯一写者，磁盘即真相）。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__, pipeline
from . import config as config_mod
from . import engine
from .config import resolve_skills_root
from .manifest import ManifestError, ManifestStore

app = FastAPI(title="MAgent")
WEB_DIR = Path(__file__).parent / "web"


class Runtime:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.events: deque[dict] = deque(maxlen=5000)
        self.event_id = 0
        self.cond = threading.Condition()
        self.running_stage: str | None = None

    def log(self, event: dict) -> None:
        with self.cond:
            self.event_id += 1
            entry = {"id": self.event_id, "ts": time.strftime("%H:%M:%S"), **event}
            self.events.append(entry)
            self.cond.notify_all()
            self._append_jsonl(entry)

    def _append_jsonl(self, entry: dict) -> None:
        try:
            log_dir = self.root / ".magent"
            log_dir.mkdir(exist_ok=True)
            with (log_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def wait_events(self, after_id: int, timeout: float = 25.0) -> list[dict]:
        with self.cond:
            if self.event_id <= after_id:
                self.cond.wait(timeout)
            return [e for e in self.events if e["id"] > after_id]


RUNTIMES: dict[str, Runtime] = {}


def _runtime(root: Path) -> Runtime:
    key = str(root)
    if key not in RUNTIMES:
        RUNTIMES[key] = Runtime(root)
    return RUNTIMES[key]


def _get_root(root: str) -> Path:
    path = Path(root).resolve() if root else None
    if path is None or not path.is_dir():
        raise HTTPException(404, f"项目目录不存在：{root}")
    return path


def _state_payload(root: Path) -> dict:
    payload: dict = {"root": str(root), "version": __version__}
    try:
        store = ManifestStore(root)
        store.load()
    except ManifestError as exc:
        payload["manifest_ok"] = False
        payload["error"] = str(exc)
        payload["stages"] = []
        return payload
    payload["manifest_ok"] = True
    payload["project_title"] = store.data.get("project", {}).get("title", "")
    payload["artifacts_count"] = len(store.data.get("artifacts", []))
    statuses = store.summary()["stages"]
    runtime = _runtime(root)
    stage_list = []
    for key in pipeline.ORDER:
        stage = pipeline.STAGES[key]
        stage_list.append(
            {
                "key": key,
                "title": stage.title,
                "skill": stage.skill,
                "status": statuses.get(key, "pending"),
                "running": runtime.running_stage == key,
            }
        )
    payload["stages"] = stage_list
    return payload


# ---------- 页面与配置 ----------

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/version")
def version():
    return {"version": __version__}


@app.get("/api/config")
def get_config():
    return config_mod.masked(config_mod.load())


@app.post("/api/config")
async def save_config(cfg: dict):
    current = config_mod.load()
    provider = cfg.get("provider", {})
    # 前端回传掩码 key 时不覆盖真实 key
    if provider.get("api_key", "").startswith("***"):
        provider["api_key"] = current["provider"]["api_key"]
    current.update({k: v for k, v in cfg.items() if k != "provider"})
    current["provider"].update(provider)
    config_mod.save(current)
    return {"ok": True}


@app.post("/api/config/test")
def test_config():
    from .llm import LLMClient, LLMError

    cfg = config_mod.load()
    try:
        latency = LLMClient(cfg["provider"]).ping()
        return {"ok": True, "latency_ms": latency}
    except LLMError as exc:
        return {"ok": False, "error": str(exc)[:300]}


# ---------- 项目 ----------

class ProjectIn(BaseModel):
    root: str
    title: str
    focus: str = "均衡"
    subproblems: str = "待赛题分析确定"


@app.post("/api/project")
async def create_project(project: ProjectIn, files: list[UploadFile] | None = None):
    cfg = config_mod.load()
    root = Path(project.root).resolve()
    if root.exists() and (root / "project-manifest.json").is_file():
        raise HTTPException(409, "该目录已存在 project-manifest.json，请换一个路径")
    problem_files = []
    for upload in files or []:
        content = await upload.read()
        if content:
            problem_files.append((upload.filename or "题目.bin", content))
    try:
        skills_root, _source = resolve_skills_root(cfg)
        engine.init_project(
            root=root,
            title=project.title or "未命名题目",
            prefs={"focus": project.focus, "subproblems": project.subproblems},
            problem_files=problem_files,
            skills_root=skills_root,
        )
    except Exception as exc:
        raise HTTPException(500, f"项目初始化失败：{exc}")
    config_mod.add_recent_project(cfg, str(root))
    config_mod.save(cfg)
    return _state_payload(root)


@app.get("/api/state")
def state(root: str):
    return _state_payload(_get_root(root))


@app.get("/api/events")
def events(root: str, after: int = 0):
    runtime = _runtime(_get_root(root))
    return {"events": [e for e in runtime.events if e["id"] > after], "cursor": runtime.event_id}


@app.get("/api/stream")
def stream(root: str):
    runtime = _runtime(_get_root(root))

    def gen():
        cursor = runtime.event_id
        yield f"retry: 3000\n\n"
        while True:
            for event in runtime.wait_events(cursor):
                cursor = max(cursor, event["id"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 阶段控制 ----------

class StageIn(BaseModel):
    root: str
    stage: str


def _spawn_stage(root: Path, stage_key: str) -> None:
    runtime = _runtime(root)
    if runtime.running_stage is not None:
        raise HTTPException(409, f"阶段 {runtime.running_stage} 正在运行，请稍候")
    cfg = config_mod.load()
    if not cfg["provider"].get("api_key"):
        raise HTTPException(400, "尚未配置模型 API Key，请先在设置页保存")

    def worker():
        runtime.running_stage = stage_key
        runtime.log({"type": "stage", "msg": f"▶ 启动阶段：{pipeline.STAGES[stage_key].title}"})
        try:
            engine.run_stage(root, stage_key, cfg, log=runtime.log)
        except Exception as exc:  # 引擎外异常兜底
            runtime.log({"type": "error", "msg": f"引擎异常：{exc}"})
        finally:
            runtime.running_stage = None
            runtime.log({"type": "stage", "msg": "■ 本次运行结束"})

    threading.Thread(target=worker, daemon=True).start()


@app.post("/api/stage/start")
def start_stage(body: StageIn):
    root = _get_root(body.root)
    if body.stage not in pipeline.STAGES:
        raise HTTPException(400, f"未知阶段：{body.stage}")
    _spawn_stage(root, body.stage)
    return {"ok": True}


@app.post("/api/stage/fix")
def fix_stage(body: StageIn):
    root = _get_root(body.root)
    if body.stage not in pipeline.STAGES:
        raise HTTPException(400, f"未知阶段：{body.stage}")
    _spawn_stage(root, body.stage)  # run_stage 对 in_progress 阶段自动进入续跑模式
    return {"ok": True}


@app.post("/api/stage/na")
def na_stage(body: StageIn):
    root = _get_root(body.root)
    store = ManifestStore(root)
    store.load()
    stage = store.get_stage(body.stage)
    if stage.get("status") == "complete":
        raise HTTPException(400, "该阶段已完成，不能标记不适用")
    store.set_status(body.stage, "n_a")
    store.add_change_log(by="MAgent/user", action=f"{body.stage} n_a（手动标记）")
    store.save()
    return {"ok": True}
