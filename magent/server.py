"""FastAPI 本地服务：配置、项目创建、阶段启动、SSE 日志流。

单用户本地软件：运行时按 PROJECT_ROOT 注册 Runtime（事件队列 + 工作线程锁）。
manifest 永远从磁盘即时读取（引擎是唯一写者，磁盘即真相）。
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import zipfile
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
        self.running_pipeline = False   # 全流程（自动按序跑）是否在运行
        self.pipeline_stop = False      # 请求：全流程在当前阶段结束后停止
        self.stop_requested = False     # 请求：立即中断当前 agent 会话
        self.stage_started_at: float | None = None   # 当前阶段开始时间（用于显示用时）

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


STAGE_TIME_FILE = ".magent/stage-times.json"


def _record_stage_time(root: Path, stage_key: str, started_at: float) -> None:
    """把某阶段的用时写入项目内 .magent/stage-times.json（供界面显示）。"""
    import json as _json

    seconds = max(0.0, time.time() - started_at)
    path = root / STAGE_TIME_FILE
    data: dict = {}
    if path.is_file():
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    prev = data.get(stage_key) or {}
    data[stage_key] = {
        "seconds": round(seconds, 1),
        "total_seconds": round(float(prev.get("total_seconds") or 0) + seconds, 1),
        "runs": int(prev.get("runs") or 0) + 1,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _read_stage_times(root: Path) -> dict:
    import json as _json

    path = root / STAGE_TIME_FILE
    if not path.is_file():
        return {}
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

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
    payload["pipeline_running"] = runtime.running_pipeline
    payload["running_stage"] = runtime.running_stage
    payload["stage_times"] = _read_stage_times(root)
    payload["stage_elapsed"] = (
        round(time.time() - runtime.stage_started_at, 1) if runtime.stage_started_at else None
    )
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
                "elapsed": (
                    round(time.time() - runtime.stage_started_at, 1)
                    if runtime.stage_started_at and runtime.running_stage == key else None
                ),
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
    """给前端用的元信息：阶段清单（设置页做路由用）+ 最近项目 + 默认项目位置。"""
    cfg = config_mod.load()
    return {
        "version": __version__,
        "stages": [
            {"key": key, "title": pipeline.STAGES[key].title, "skill": pipeline.STAGES[key].skill}
            for key in pipeline.ORDER
        ],
        "recent_projects": cfg.get("recent_projects", []),
        "default_projects_dir": _default_projects_dir(cfg),
    }


def _default_projects_dir(cfg: dict | None = None) -> str:
    """新项目默认父目录：配置优先；否则 D 盘根目录（可写时）；再退「文档/MAgent项目」。"""
    cfg = cfg or config_mod.load()
    configured = str(cfg.get("projects_dir") or "").strip()
    if configured:
        try:
            path = Path(configured)
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
        except OSError:
            pass  # 配置不可用时退回默认探测
    for drive in ("D:/", "E:/"):
        root = Path(drive)
        try:
            if root.is_dir() and os.access(root, os.W_OK):
                return str(root)
        except OSError:
            continue
    return str(Path.home() / "Documents" / "MAgent项目")


LETTER_RE = re.compile(r"([A-Ea-e])\s*[题卷]")


def _detect_problem_letter(names: list[str], blobs: list[tuple[str, bytes]]) -> str | None:
    """从上传文件名（含压缩包内文件名）推断赛题题号字母：B题.pdf → B。"""
    for name in names:
        match = LETTER_RE.search(Path(name).stem) or LETTER_RE.search(name)
        if match:
            return match.group(1).upper()
    for name, content in blobs:
        if not name.lower().endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for member in zf.namelist()[:200]:
                    match = LETTER_RE.search(Path(member).stem) or LETTER_RE.search(member)
                    if match:
                        return match.group(1).upper()
        except zipfile.BadZipFile:
            continue
    return None


def _auto_project_title(letter: str | None) -> str:
    """项目名：2026-国赛B题；题号未定则为 2026-国赛（待定），赛题分析后可自动改名。"""
    year = time.strftime("%Y")
    return f"{year}-国赛{letter}题" if letter else f"{year}-国赛（待定）"


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
    root: str = Form(""),
    title: str = Form(""),
    focus: str = Form("均衡"),
    subproblems: str = Form("待赛题分析确定"),
    files: list[UploadFile] | None = File(default=None),
):
    """创建项目：项目名由软件自动生成（年份-竞赛+题型）；路径留空则自动决定位置。

    目标目录已是项目且带文件时，把文件补进该项目的 data/（不报错）。
    """
    cfg = config_mod.load()
    problem_files: list[tuple[str, bytes]] = []
    for upload in files or []:
        content = await upload.read()
        if content:
            problem_files.append((upload.filename or "题目.bin", content))

    letter = _detect_problem_letter([name for name, _ in problem_files], problem_files)
    auto_title = _auto_project_title(letter)

    if root.strip():
        root_path = Path(root).expanduser().resolve()
    else:
        root_path = (Path(_default_projects_dir(cfg)) / auto_title).resolve()

    if root_path.exists() and (root_path / "project-manifest.json").is_file():
        if problem_files:  # 已是项目：把选中的文件补进去
            saved = engine.add_project_files(root_path, problem_files)
            config_mod.add_recent_project(cfg, str(root_path))
            if str(root_path) not in cfg.get("workspaces", []):
                cfg["workspaces"] = [str(root_path), *cfg.get("workspaces", [])][:12]
            config_mod.save(cfg)
            payload = _state_payload(root_path)
            payload["added_files"] = saved
            return payload
        raise HTTPException(
            409,
            f"该目录已是项目（{root_path.name}）。打开它：点左侧工作区「＋」或「打开其他项目」；"
            "或把「项目路径」清空，让软件自动新建一个项目。",
        )

    final_title = title.strip() or auto_title
    try:
        skills_root, _source = resolve_skills_root(cfg)
        engine.init_project(
            root=root_path,
            title=final_title,
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
        "is_project": False, "running": None, "stages": {},
    }
    runtime = RUNTIMES.get(str(root))
    if runtime and runtime.running_stage:
        entry["running"] = runtime.running_stage
    if (root / "project-manifest.json").is_file():
        try:
            store = ManifestStore(root)
            store.load()
            entry["is_project"] = True
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
    """把任意文件夹加入工作区（不要求它已是项目——非项目时由界面引导初始化）。"""
    root = Path(body.root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(400, f"文件夹不存在：{root}")
    cfg = config_mod.load()
    others = [w for w in cfg.get("workspaces", []) if Path(w) != root]
    cfg["workspaces"] = [str(root), *others][:12]
    config_mod.add_recent_project(cfg, str(root))
    config_mod.save(cfg)
    return {"ok": True, "is_project": (root / "project-manifest.json").is_file(), "workspaces": cfg["workspaces"]}


@app.post("/api/workspaces/remove")
def remove_workspace(body: WorkspaceIn):
    root = Path(body.root).expanduser().resolve()
    cfg = config_mod.load()
    cfg["workspaces"] = [w for w in cfg.get("workspaces", []) if Path(w) != root]
    config_mod.save(cfg)
    return {"ok": True}


DATA_LIKE_EXTS = {".pdf", ".docx", ".doc", ".md", ".txt", ".csv", ".xlsx", ".xls", ".zip", ".png", ".jpg", ".jpeg"}
SKELETON_NAMES = {"plan.md", "todo.md", "project-manifest.json", "project-manifest.schema.json"}


def _collect_loose_data_files(root: Path) -> list[tuple[str, bytes]]:
    """把文件夹根目录里现成的题面/数据文件收进来（作为项目 data/ 的初始内容）。"""
    items: list[tuple[str, bytes]] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return items
    for path in entries:
        if not path.is_file() or path.name in SKELETON_NAMES:
            continue
        if path.suffix.lower() not in DATA_LIKE_EXTS:
            continue
        try:
            if path.stat().st_size > 60 * 1024 * 1024:  # 单文件 60MB 上限
                continue
            items.append((path.name, path.read_bytes()))
        except OSError:
            continue
    return items


@app.post("/api/project/init")
def init_existing_folder(body: WorkspaceIn):
    """把一个已有文件夹初始化为 MAgent 项目（用户确认后才调用）。

    - 自动命名：优先从文件夹内的文件名推断题号（如 B题.pdf → 2026-国赛B题），否则先用（待定）
    - 已存在的文件全部保留；根目录里的题面/数据文件会复制一份进 data/ 供流水线使用
    """
    root = Path(body.root).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(400, f"文件夹不存在：{root}")
    if (root / "project-manifest.json").is_file():
        return _state_payload(root)  # 已经是项目，直接返回状态

    cfg = config_mod.load()
    loose = _collect_loose_data_files(root)
    letter = _detect_problem_letter([name for name, _ in loose], loose)
    title = _auto_project_title(letter)
    try:
        skills_root, _source = resolve_skills_root(cfg)
        engine.init_project(
            root=root,
            title=title,
            prefs={"focus": "均衡"},
            problem_files=loose,
            skills_root=skills_root,
        )
    except Exception as exc:
        raise HTTPException(500, f"初始化失败：{exc}")

    others = [w for w in cfg.get("workspaces", []) if Path(w) != root]
    cfg["workspaces"] = [str(root), *others][:12]
    config_mod.add_recent_project(cfg, str(root))
    config_mod.save(cfg)

    payload = _state_payload(root)
    payload["adopted_files"] = [name for name, _ in loose]
    return payload


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
        runtime.stop_requested = False
        runtime.stage_started_at = time.time()
        runtime.log({"type": "stage", "msg": f"▶ 启动阶段：{pipeline.STAGES[stage_key].title}"})
        try:
            result = engine.run_stage(root, stage_key, cfg, log=runtime.log,
                                      should_stop=lambda: runtime.stop_requested)
            if getattr(result, "outcome", "") == "stopped":
                runtime.log({"type": "stage", "msg": "⏹ 已停止（该阶段保持在「进行中」，可再点继续）"})
        except Exception as exc:  # 引擎外异常兜底
            runtime.log({"type": "error", "msg": f"引擎异常：{exc}"})
        finally:
            _record_stage_time(root, stage_key, runtime.stage_started_at or time.time())
            runtime.running_stage = None
            runtime.stage_started_at = None
            runtime.log({"type": "stage", "msg": f"■ 本次运行结束（用时 {_elapsed_text(root, stage_key)}）"})

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


# ---------- 全流程（两种模式） ----------

class PipelineIn(BaseModel):
    root: str
    mode: str = "auto"   # auto=一口气跑完；step=每跑完一个阶段停下等确认


def _pipeline_precheck(root: Path, runtime: Runtime, cfg: dict) -> str:
    """校验并返回本次要跑的阶段 key。"""
    if runtime.running_pipeline:
        raise HTTPException(409, "全流程已在运行中")
    if runtime.running_stage is not None:
        raise HTTPException(409, f"阶段 {runtime.running_stage} 正在运行，请稍候")
    store = ManifestStore(root)
    try:
        store.load()
    except ManifestError as exc:
        raise HTTPException(400, str(exc)) from exc
    stage_key = pipeline.next_stage(store.summary()["stages"])
    if stage_key is None:
        raise HTTPException(400, "没有待执行的阶段（全部已完成或不适用）")
    try:
        provider = config_mod.provider_for_stage(cfg, stage_key)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not provider.get("api_key"):
        title = pipeline.STAGES[stage_key].title
        raise HTTPException(
            400, f"阶段「{title}」使用的模型服务「{provider.get('name')}」尚未配置 API Key，请到设置页填写"
        )
    return stage_key


def _elapsed_text(root: Path, stage_key: str) -> str:
    info = _read_stage_times(root).get(stage_key) or {}
    seconds = float(info.get("seconds") or 0)
    return f"{int(seconds // 60)} 分 {int(seconds % 60)} 秒"

def _spawn_pipeline(root: Path, mode: str) -> None:
    runtime = _runtime(root)
    cfg = config_mod.load()
    _pipeline_precheck(root, runtime, cfg)  # 先做校验（HTTP 错误在请求阶段返回）

    def worker():
        runtime.running_pipeline = True
        runtime.pipeline_stop = False
        runtime.stop_requested = False
        label = "全自动" if mode == "auto" else "逐步"
        runtime.log({"type": "stage", "msg": f"▶ 全流程开始（{label}模式）"})
        try:
            while True:
                store = ManifestStore(root)
                store.load()
                stage_key = pipeline.next_stage(store.summary()["stages"])
                if stage_key is None:
                    runtime.log({"type": "stage", "msg": "🎉 全流程结束：所有阶段已完成或不适用"})
                    break
                stage = pipeline.STAGES[stage_key]
                runtime.running_stage = stage_key
                runtime.stage_started_at = time.time()
                runtime.log({"type": "stage", "msg": f"▶ 阶段：{stage.title}"})
                result = engine.run_stage(root, stage_key, cfg, log=runtime.log,
                                          should_stop=lambda: runtime.stop_requested)
                _record_stage_time(root, stage_key, runtime.stage_started_at)
                runtime.running_stage = None
                runtime.stage_started_at = None
                if result.outcome == "stopped":
                    runtime.log({"type": "stage", "msg": "⏹ 全流程已停止（已完成阶段保留）"})
                    break
                if result.outcome not in ("complete", "already_complete"):
                    runtime.log({
                        "type": "stage",
                        "msg": f"⏸ 全流程暂停于「{stage.title}」（{result.outcome}）：{result.detail or '需人工处理'}"
                               "——处理后点「继续」",
                    })
                    break
                if mode == "step":
                    nxt_store = ManifestStore(root)
                    nxt_store.load()
                    nxt = pipeline.next_stage(nxt_store.summary()["stages"])
                    if nxt is None:
                        runtime.log({"type": "stage", "msg": "🎉 全流程结束：所有阶段已完成或不适用"})
                    else:
                        runtime.log({
                            "type": "stage",
                            "msg": f"⏸ 逐步模式：「{stage.title}」已完成，下一步是「{pipeline.STAGES[nxt].title}」"
                                   "——确认后点「继续」",
                        })
                    break
                if runtime.pipeline_stop:
                    runtime.log({"type": "stage", "msg": "■ 已按请求停止（当前阶段已完成）"})
                    break
        except Exception as exc:  # 兜底，避免线程静默死掉
            runtime.log({"type": "error", "msg": f"全流程异常：{exc}"})
        finally:
            runtime.running_stage = None
            runtime.stage_started_at = None
            runtime.running_pipeline = False
            runtime.log({"type": "stage", "msg": "■ 全流程运行结束"})

    threading.Thread(target=worker, daemon=True).start()


@app.post("/api/pipeline/start")
def start_pipeline(body: PipelineIn):
    root = _get_root(body.root)
    if body.mode not in ("auto", "step"):
        raise HTTPException(400, f"未知模式：{body.mode}")
    _spawn_pipeline(root, body.mode)
    return {"ok": True, "mode": body.mode}


class StopIn(BaseModel):
    root: str


@app.post("/api/stop")
def stop_everything(body: StopIn):
    """停止当前工作区正在跑的一切：立即中断 agent 会话，或让全流程不再进入下一阶段。"""
    root = _get_root(body.root)
    runtime = _runtime(root)
    if runtime.running_stage is None and not runtime.running_pipeline:
        raise HTTPException(400, "当前没有在运行的任务")
    runtime.stop_requested = True
    runtime.pipeline_stop = True
    runtime.log({"type": "stage", "msg": "⏹ 已请求停止，正在中断当前步骤…"})
    return {"ok": True, "running_stage": runtime.running_stage, "pipeline": runtime.running_pipeline}


@app.post("/api/pipeline/stop")
def stop_pipeline(body: WorkspaceIn):
    """兼容旧入口：等价于 /api/stop。"""
    return stop_everything(StopIn(root=body.root))


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
