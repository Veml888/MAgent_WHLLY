"""FastAPI 本地服务：配置、项目创建、阶段启动、SSE 日志流。

单用户本地软件：运行时按 PROJECT_ROOT 注册 Runtime（事件队列 + 工作线程锁）。
manifest 永远从磁盘即时读取（引擎是唯一写者，磁盘即真相）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__, engine, pipeline
from . import config as config_mod
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


@app.get("/api/meta")
def meta():
    """给前端用的元信息：阶段清单（设置页做路由用）+ 最近项目。"""
    cfg = config_mod.load()
    return {
        "version": __version__,
        "stages": [
            {"key": key, "title": pipeline.STAGES[key].title, "skill": pipeline.STAGES[key].skill}
            for key in pipeline.ORDER
        ],
        "recent_projects": cfg.get("recent_projects", []),
        "default_projects_dir": str(Path.home() / "Documents" / "MAgent项目"),
    }


@app.get("/api/config")
def get_config():
    return config_mod.masked(config_mod.load())


@app.post("/api/config")
async def save_config(cfg: dict):
    current = config_mod.load()
    stored = {p["id"]: p for p in current.get("providers", []) if p.get("id")}

    incoming = cfg.get("providers")
    if isinstance(incoming, list) and incoming:
        merged: list[dict] = []
        seen: set[str] = set()
        for raw in incoming:
            if not isinstance(raw, dict):
                continue
            pid = str(raw.get("id") or "").strip() or config_mod.new_provider_id()
            while pid in seen:  # 兜底去重
                pid = config_mod.new_provider_id()
            seen.add(pid)
            old = stored.get(pid, {})
            api_key = str(raw.get("api_key") or "")
            merged.append(
                {
                    "id": pid,
                    "name": str(raw.get("name") or "未命名服务").strip(),
                    "base_url": str(raw.get("base_url") or "").strip(),
                    "api_key": old.get("api_key", "") if api_key.startswith("***") else api_key,
                    "model": str(raw.get("model") or "").strip(),
                    "temperature": raw.get("temperature", old.get("temperature")),
                }
            )
        if merged:
            current["providers"] = merged
    ids = {p["id"] for p in current["providers"]}

    routing = cfg.get("routing")
    if isinstance(routing, dict):
        cleaned = {"default": routing.get("default")}
        for key, value in routing.items():
            if key == "default":
                continue
            if value:  # 空 = 跟随默认
                cleaned[key] = value
        if cleaned["default"] not in ids:
            cleaned["default"] = current["providers"][0]["id"]
        # 清理指向已删除服务的路由
        cleaned = {k: v for k, v in cleaned.items() if k == "default" or v in ids}
        current["routing"] = cleaned

    for key, value in cfg.items():
        if key not in ("providers", "routing", "provider"):
            current[key] = value
    current.pop("provider", None)  # 1.x 残留字段
    config_mod.save(current)
    return {"ok": True}


@app.post("/api/config/test")
def test_config(body: dict | None = None):
    """测试连接。body 可带 provider_id（测已存服务），也可带内联字段（测未保存的编辑）。"""
    from .llm import LLMClient, LLMError

    body = body or {}
    cfg = config_mod.load()
    pid = body.get("provider_id") or body.get("id")
    stored = {p["id"]: p for p in cfg.get("providers", []) if p.get("id")}
    target = dict(stored.get(str(pid) or "", {}))
    for key in ("name", "base_url", "model"):
        if body.get(key):
            target[key] = str(body[key])
    api_key = str(body.get("api_key") or "")
    if api_key and not api_key.startswith("***"):
        target["api_key"] = api_key
    if not target.get("base_url"):
        return {"ok": False, "error": "缺少 Base URL"}
    if not target.get("api_key"):
        return {"ok": False, "error": "缺少 API Key"}
    try:
        latency = LLMClient(target).ping()
        return {"ok": True, "latency_ms": latency, "model": target.get("model", "")}
    except LLMError as exc:
        return {"ok": False, "error": str(exc)[:300]}


# ---------- 项目 ----------

@app.post("/api/project")
async def create_project(
    root: str = Form(...),
    title: str = Form(""),
    focus: str = Form("均衡"),
    subproblems: str = Form("待赛题分析确定"),
    files: list[UploadFile] | None = File(default=None),
):
    """浏览器以 multipart/form-data 提交（含题面文件），故用 Form/File 解析。"""
    cfg = config_mod.load()
    root_path = Path(root).expanduser().resolve()
    if root_path.exists() and (root_path / "project-manifest.json").is_file():
        raise HTTPException(409, "该目录已是一个 MAgent 项目（已存在 project-manifest.json）。"
                                 "想继续它请用「打开已有项目」，新建请换一个空目录。")
    problem_files = []
    for upload in files or []:
        content = await upload.read()
        if content:
            problem_files.append((upload.filename or "题目.bin", content))
    try:
        skills_root, _source = resolve_skills_root(cfg)
        engine.init_project(
            root=root_path,
            title=title or "未命名题目",
            prefs={"focus": focus, "subproblems": subproblems},
            problem_files=problem_files,
            skills_root=skills_root,
        )
    except Exception as exc:
        raise HTTPException(500, f"项目初始化失败：{exc}")
    config_mod.add_recent_project(cfg, str(root_path))
    others = [w for w in cfg.get("workspaces", []) if Path(w) != root_path]
    cfg["workspaces"] = [str(root_path), *others][:12]
    config_mod.save(cfg)
    return _state_payload(root_path)


@app.get("/api/state")
def state(root: str):
    return _state_payload(_get_root(root))


class RecentIn(BaseModel):
    root: str


@app.post("/api/recent")
def mark_recent(body: RecentIn):
    root = _get_root(body.root)
    cfg = config_mod.load()
    config_mod.add_recent_project(cfg, str(root))
    config_mod.save(cfg)
    return {"ok": True, "recent_projects": cfg["recent_projects"]}


# ---------- 工作区（可同时打开多个项目） ----------

def _workspace_entry(root: Path) -> dict:
    entry: dict = {
        "root": str(root), "title": root.name, "exists": root.is_dir(),
        "running": None, "stages": {},
    }
    runtime = RUNTIMES.get(str(root))
    if runtime and runtime.running_stage:
        entry["running"] = runtime.running_stage
    if (root / "project-manifest.json").is_file():
        try:
            store = ManifestStore(root)
            store.load()
            entry["title"] = store.data.get("project", {}).get("title") or root.name
            entry["stages"] = store.summary()["stages"]
        except ManifestError:
            pass
    return entry


@app.get("/api/workspaces")
def list_workspaces():
    cfg = config_mod.load()
    return {"workspaces": [_workspace_entry(Path(raw)) for raw in cfg.get("workspaces", [])]}


class WorkspaceIn(BaseModel):
    root: str


@app.post("/api/workspaces/add")
def add_workspace(body: WorkspaceIn):
    root = Path(body.root).expanduser().resolve()
    if not (root / "project-manifest.json").is_file():
        raise HTTPException(400, "该目录不是 MAgent 项目（缺少 project-manifest.json）")
    cfg = config_mod.load()
    others = [w for w in cfg.get("workspaces", []) if Path(w) != root]
    cfg["workspaces"] = [str(root), *others][:12]
    config_mod.add_recent_project(cfg, str(root))
    config_mod.save(cfg)
    return {"ok": True, "workspaces": cfg["workspaces"]}


@app.post("/api/workspaces/remove")
def remove_workspace(body: WorkspaceIn):
    root = Path(body.root).expanduser().resolve()
    cfg = config_mod.load()
    cfg["workspaces"] = [w for w in cfg.get("workspaces", []) if Path(w) != root]
    config_mod.save(cfg)
    return {"ok": True}


# ---------- 工作区文件浏览 ----------

SKIP_NAMES = {".magent", "__pycache__", ".git", ".pytest_cache"}
TEXT_EXTS = {
    ".md", ".txt", ".json", ".csv", ".py", ".tex", ".bib", ".log",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".m", ".r", ".sql",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_TREE_NODES = 600
MAX_PREVIEW_BYTES = 400_000


def _safe_child(base: Path, rel: str) -> Path:
    """把前端传来的相对路径解析到 base 之内，越界一律拒绝。"""
    if not rel or not str(rel).strip():
        return base
    target = (base / str(rel).replace("\\", "/")).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(400, "路径越界") from exc
    return target


def _walk_tree(base: Path, current: Path, depth: int, budget: list[int]) -> list[dict]:
    if depth > 4 or budget[0] <= 0:
        return []
    items: list[dict] = []
    try:
        entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return []
    for path in entries:
        if budget[0] <= 0:
            break
        if path.name in SKIP_NAMES or path.name.startswith("."):
            continue
        budget[0] -= 1
        rel = path.relative_to(base).as_posix()
        if path.is_dir():
            items.append({
                "name": path.name, "path": rel, "type": "dir",
                "children": _walk_tree(base, path, depth + 1, budget),
            })
        elif path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            items.append({"name": path.name, "path": rel, "type": "file", "size": size})
    return items


@app.get("/api/files")
def list_files(root: str):
    """侧边栏工作区文件树。"""
    base = _get_root(root)
    tree = _walk_tree(base, base, 0, [MAX_TREE_NODES])
    return {"root": str(base), "tree": tree}


@app.get("/api/file")
def file_info(root: str, path: str):
    """文件预览信息：文本直接返回内容，图片/PDF 返回标记（前端走 /api/raw）。"""
    base = _get_root(root)
    target = _safe_child(base, path)
    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{path}")
    ext = target.suffix.lower()
    size = target.stat().st_size
    if ext in IMAGE_EXTS:
        return {"kind": "image", "size": size, "ext": ext}
    if ext == ".pdf":
        return {"kind": "pdf", "size": size, "ext": ext}
    if ext in TEXT_EXTS or size <= 200_000:
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_PREVIEW_BYTES:
            text = text[:MAX_PREVIEW_BYTES] + "\n…（内容过长，已截断，可用系统程序打开查看全文）"
        return {"kind": "text", "content": text, "size": size, "ext": ext}
    return {"kind": "binary", "size": size, "ext": ext}


@app.get("/api/raw")
def raw_file(root: str, path: str):
    """原样返回文件（图片 / PDF 预览用）。"""
    base = _get_root(root)
    target = _safe_child(base, path)
    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{path}")
    return FileResponse(target)


class OpenIn(BaseModel):
    root: str
    path: str


@app.post("/api/open")
def open_external(body: OpenIn):
    """用系统默认程序打开文件（PDF 用阅读器、图片用看图器等）。"""
    base = _get_root(body.root)
    target = _safe_child(base, body.path)
    if not target.is_file():
        raise HTTPException(404, f"文件不存在：{body.path}")
    if not hasattr(os, "startfile"):
        raise HTTPException(400, "当前系统不支持直接打开文件")
    os.startfile(str(target))  # noqa: S606 - 本地软件，仅打开项目内文件
    return {"ok": True}


@app.get("/api/events")
def events(root: str, after: int = 0):
    runtime = _runtime(_get_root(root))
    return {"events": [e for e in runtime.events if e["id"] > after], "cursor": runtime.event_id}


@app.get("/api/stream")
def stream(root: str, after: int | None = None):
    """SSE 事件流；after 指定起始游标（用于切换工作区时回放已有日志）。"""
    runtime = _runtime(_get_root(root))

    def gen():
        cursor = runtime.event_id if after is None else int(after)
        yield "retry: 3000\n\n"
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
    try:
        provider = config_mod.provider_for_stage(cfg, stage_key)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not provider.get("api_key"):
        title = pipeline.STAGES[stage_key].title
        raise HTTPException(400, f"阶段「{title}」使用的模型服务「{provider.get('name')}」尚未配置 API Key，请到设置页填写")

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
