"""服务层接口：项目创建（multipart）、重复目录、元信息。"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest
from fastapi.testclient import TestClient

from magent import config as config_mod
from magent.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """隔离配置目录，避免写真实 ~/.magent/config.json。"""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    return TestClient(app)


def test_create_project_via_multipart(client, tmp_path):
    """前端以 multipart/form-data 提交（含题面文件）——曾因用 JSON 模型解析而 422。"""
    root = tmp_path / "proj-a"
    resp = client.post(
        "/api/project",
        data={"root": str(root), "title": "2026 A 题", "focus": "精度优先", "subproblems": "3"},
        files={"files": ("题目.txt", io.BytesIO("题面内容".encode()), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["project_title"] == "2026 A 题"
    assert len(payload["stages"]) == 8
    # 骨架与题面落盘
    assert (root / "project-manifest.json").is_file()
    assert (root / "data" / "题目.txt").read_text(encoding="utf-8") == "题面内容"


def test_create_project_without_files(client, tmp_path):
    root = tmp_path / "proj-b"
    resp = client.post("/api/project", data={"root": str(root), "title": "无附件"})
    assert resp.status_code == 200
    assert (root / "plan.md").is_file()


def test_duplicate_project_dir_gives_409(client, tmp_path):
    root = tmp_path / "proj-c"
    assert client.post("/api/project", data={"root": str(root), "title": "T"}).status_code == 200
    again = client.post("/api/project", data={"root": str(root), "title": "T2"})
    assert again.status_code == 409
    assert "已是项目" in again.json()["detail"]


def test_auto_naming_from_filename(client, tmp_path):
    """题号从上传文件名推断：B题.pdf → 2026-国赛B题（无需用户取名）。"""
    root = tmp_path / "auto-b"
    resp = client.post(
        "/api/project",
        data={"root": str(root)},
        files={"files": ("B题.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    title = resp.json()["project_title"]
    assert title.endswith("-国赛B题"), title
    assert title.startswith("20"), title


def test_auto_naming_placeholder_when_unknown(client, tmp_path):
    root = tmp_path / "auto-unknown"
    resp = client.post(
        "/api/project",
        data={"root": str(root)},
        files={"files": ("题目.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert resp.json()["project_title"].endswith("-国赛（待定）")


def test_empty_path_creates_in_default_dir(client, tmp_path):
    """路径留空 → 建在默认父目录下，并以自动名命名。"""
    cfg = config_mod.load()
    cfg["projects_dir"] = str(tmp_path / "默认位置")
    config_mod.save(cfg)
    resp = client.post(
        "/api/project",
        data={},
        files={"files": ("A题.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["root"].startswith(str(tmp_path / "默认位置"))
    assert payload["project_title"].endswith("-国赛A题")
    from pathlib import Path as P
    assert (P(payload["root"]) / "project-manifest.json").is_file()


def test_files_into_existing_project_instead_of_409(client, tmp_path):
    """目标已是项目时补文件进 data/，而不是报错（用户上传附件却不落盘的场景）。"""
    root = tmp_path / "existing"
    client.post("/api/project", data={"root": str(root), "title": "T"})
    resp = client.post(
        "/api/project",
        data={"root": str(root)},
        files=[
            ("files", ("附件/数据.csv", io.BytesIO("a,b\n1,2\n".encode()), "text/csv")),
        ],
    )
    assert resp.status_code == 200, resp.text
    assert "附件/数据.csv" in resp.json()["added_files"]
    assert (root / "data" / "附件" / "数据.csv").is_file()


def test_meta_lists_stages_and_recent(client, tmp_path):
    root = tmp_path / "proj-d"
    client.post("/api/project", data={"root": str(root), "title": "T"})
    meta = client.get("/api/meta").json()
    assert len(meta["stages"]) == 8
    assert meta["stages"][0]["key"] == "analysis"
    assert str(root) in meta["recent_projects"]


def test_recent_endpoint_records_open(client, tmp_path):
    root = tmp_path / "proj-e"
    client.post("/api/project", data={"root": str(root), "title": "T"})
    resp = client.post("/api/recent", json={"root": str(root)})
    assert resp.status_code == 200
    assert resp.json()["recent_projects"][0] == str(root)


def test_config_save_preserves_masked_key(client, tmp_path):
    """前端回传掩码 key 时必须保留原真实值（不能把 ***xxxx 存成 key）。"""
    r = client.post("/api/config", json={
        "providers": [{"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com",
                       "api_key": "sk-real-9999", "model": "deepseek-chat"}],
        "routing": {"default": "deepseek"},
    })
    assert r.status_code == 200
    masked = client.get("/api/config").json()
    assert masked["providers"][0]["api_key"] == "***9999"
    # 回传掩码值
    client.post("/api/config", json={"providers": masked["providers"], "routing": masked["routing"]})
    assert config_mod.load()["providers"][0]["api_key"] == "sk-real-9999"


def test_folder_upload_via_multipart_keeps_structure(client, tmp_path):
    """整文件夹上传：文件名带相对路径（附件/数据.csv）时按原结构落盘。"""
    root = tmp_path / "proj-folder"
    resp = client.post(
        "/api/project",
        data={"root": str(root), "title": "文件夹上传"},
        files=[
            ("files", ("B题.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")),
            ("files", ("附件/数据.csv", io.BytesIO("a,b\n1,2\n".encode()), "text/csv")),
            ("files", ("附件/子目录/readme.txt", io.BytesIO(b"hi"), "text/plain")),
        ],
    )
    assert resp.status_code == 200, resp.text
    assert (root / "data" / "附件" / "数据.csv").is_file()
    assert (root / "data" / "附件" / "子目录" / "readme.txt").is_file()
    assert (root / "data" / "B题.pdf").is_file()


def test_workspace_file_tree_and_preview(client, tmp_path):
    """侧边栏文件树：列出项目文件（跳过 .magent），文本可预览，越界被拒。"""
    root = tmp_path / "proj-tree"
    client.post("/api/project", data={"root": str(root), "title": "T"})
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "01-analysis-report.md").write_text("# 分析报告\n内容", encoding="utf-8")
    (root / "results").mkdir(exist_ok=True)
    (root / "results" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    tree = client.get("/api/files", params={"root": str(root)}).json()["tree"]
    names = {n["name"] for n in tree}
    assert "docs" in names and "data" in names
    assert ".magent" not in names  # 引擎内部目录不暴露
    docs = next(n for n in tree if n["name"] == "docs")
    assert docs["children"][0]["path"] == "docs/01-analysis-report.md"

    info = client.get("/api/file", params={"root": str(root), "path": "docs/01-analysis-report.md"}).json()
    assert info["kind"] == "text" and "分析报告" in info["content"]

    raw = client.get("/api/raw", params={"root": str(root), "path": "results/data.csv"})
    assert raw.status_code == 200 and "a,b" in raw.text

    # 越界访问被拒
    assert client.get("/api/file", params={"root": str(root), "path": "../outside.txt"}).status_code == 400
    assert client.get("/api/file", params={"root": str(root), "path": "nope.txt"}).status_code == 404


def test_workspaces_multi_project(client, tmp_path):
    """多工作区：可同时登记多个项目，各自状态独立呈现。"""
    a = tmp_path / "题A"
    b = tmp_path / "题B"
    client.post("/api/project", data={"root": str(a), "title": "2026 A 题"})
    client.post("/api/project", data={"root": str(b), "title": "2026 B 题"})

    ws = client.get("/api/workspaces").json()["workspaces"]
    titles = {w["title"] for w in ws}
    assert {"2026 A 题", "2026 B 题"} <= titles
    assert all(w["running"] is None for w in ws)
    assert all(len(w["stages"]) == 8 for w in ws)

    # 移除一个工作区不影响磁盘文件
    client.post("/api/workspaces/remove", json={"root": str(a)})
    ws2 = client.get("/api/workspaces").json()["workspaces"]
    assert all(w["root"] != str(a) for w in ws2)
    assert (a / "project-manifest.json").is_file()

    # 任意文件夹都可加入工作区（非项目时由界面引导初始化）
    plain = tmp_path / "普通文件夹"
    plain.mkdir()
    added = client.post("/api/workspaces/add", json={"root": str(plain)})
    assert added.status_code == 200 and added.json()["is_project"] is False
    ws3 = client.get("/api/workspaces").json()["workspaces"]
    assert any(w["root"] == str(plain) and w["is_project"] is False for w in ws3)
    # 目录不存在才拒绝
    assert client.post("/api/workspaces/add", json={"root": str(tmp_path / "不存在")}).status_code == 400


def test_stage_locks_are_per_workspace(client, tmp_path, monkeypatch):
    """两个工作区可各自独立运行阶段（运行锁互不干扰）。"""
    a = tmp_path / "A"
    b = tmp_path / "B"
    client.post("/api/project", data={"root": str(a), "title": "A"})
    client.post("/api/project", data={"root": str(b), "title": "B"})

    import magent.server as srv
    from magent import config as cm

    cfg = cm.load()
    cfg["providers"] = [{"id": "p", "name": "P", "base_url": "http://x", "api_key": "k", "model": "m"}]
    cfg["routing"] = {"default": "p"}
    cm.save(cfg)

    monkeypatch.setattr(srv.engine, "run_stage", lambda *a_, **k_: None)
    assert client.post("/api/stage/start", json={"root": str(a), "stage": "analysis"}).status_code == 200
    assert client.post("/api/stage/start", json={"root": str(b), "stage": "analysis"}).status_code == 200


def _ready_project(client, tmp_path, name="P"):
    """建一个项目并把模型服务配好（跑流程前需要）。"""
    root = tmp_path / name
    client.post("/api/project", data={"root": str(root), "title": name})
    cfg = config_mod.load()
    cfg["providers"] = [{"id": "p", "name": "P", "base_url": "http://x", "api_key": "k", "model": "m"}]
    cfg["routing"] = {"default": "p"}
    config_mod.save(cfg)
    return root


def _stub_engine(monkeypatch, calls, mark_complete=True):
    """把 engine.run_stage 换成打桩：记录调用并按需把阶段置为 complete。"""
    import magent.server as srv
    from magent.engine import StageResult
    from magent.manifest import ManifestStore

    def fake_run(root, stage_key, cfg_, log=None, llm=None, should_stop=None):
        calls.append(stage_key)
        if mark_complete:
            store = ManifestStore(root)
            store.load()
            store.set_status(stage_key, "in_progress")
            store.set_status(stage_key, "complete")
            store.save()
        if log:
            log({"type": "stage", "msg": f"[stub] {stage_key}"})
        return StageResult(stage_key, "complete", "stub")

    monkeypatch.setattr(srv.engine, "run_stage", fake_run)


def _wait_pipeline(srv_root, timeout=5.0):
    import time as _t

    import magent.server as srv
    end = _t.monotonic() + timeout
    while _t.monotonic() < end:
        rt = srv.RUNTIMES.get(str(srv_root))
        if rt and not rt.running_pipeline and rt.running_stage is None:
            return True
        _t.sleep(0.05)
    return False


def test_pipeline_step_mode_runs_exactly_one_stage(client, tmp_path, monkeypatch):
    root = _ready_project(client, tmp_path, "step")
    calls = []
    _stub_engine(monkeypatch, calls)

    assert client.post("/api/pipeline/start", json={"root": str(root), "mode": "step"}).status_code == 200
    assert _wait_pipeline(root)
    assert calls == ["analysis"], f"逐步模式应只跑一个阶段，实际 {calls}"
    # 日志里给出下一步提示
    events = client.get("/api/events", params={"root": str(root), "after": 0}).json()["events"]
    assert any("逐步模式" in (e.get("msg") or "") for e in events)


def test_pipeline_auto_mode_runs_until_done(client, tmp_path, monkeypatch):
    root = _ready_project(client, tmp_path, "auto")
    calls = []
    _stub_engine(monkeypatch, calls)

    assert client.post("/api/pipeline/start", json={"root": str(root), "mode": "auto"}).status_code == 200
    assert _wait_pipeline(root)
    assert calls == ["analysis", "modeling", "coding", "paper_plan", "figures", "graphics", "paper_final", "verification"]
    events = client.get("/api/events", params={"root": str(root), "after": 0}).json()["events"]
    assert any("全流程结束" in (e.get("msg") or "") for e in events)


def test_pipeline_stops_on_failure(client, tmp_path, monkeypatch):
    root = _ready_project(client, tmp_path, "fail")
    calls = []
    import magent.server as srv
    from magent.engine import StageResult

    def failing_run(root_, stage_key, cfg_, log=None, llm=None, should_stop=None):
        calls.append(stage_key)
        return StageResult(stage_key, "paused", "需要人工处理")

    monkeypatch.setattr(srv.engine, "run_stage", failing_run)
    client.post("/api/pipeline/start", json={"root": str(root), "mode": "auto"})
    assert _wait_pipeline(root)
    assert calls == ["analysis"]  # 首个阶段失败后停下，不继续
    events = client.get("/api/events", params={"root": str(root), "after": 0}).json()["events"]
    assert any("全流程暂停" in (e.get("msg") or "") for e in events)


def test_pipeline_requires_model_key(client, tmp_path):
    root = tmp_path / "nokey"
    client.post("/api/project", data={"root": str(root), "title": "无Key"})
    cfg = config_mod.load()
    cfg["providers"] = [{"id": "p", "name": "P", "base_url": "http://x", "api_key": "", "model": "m"}]
    cfg["routing"] = {"default": "p"}
    config_mod.save(cfg)
    resp = client.post("/api/pipeline/start", json={"root": str(root), "mode": "auto"})
    assert resp.status_code == 400 and "API Key" in resp.json()["detail"]


def test_init_existing_folder_as_project(client, tmp_path):
    """打开任意文件夹后一键初始化为项目：自动命名、保留原文件、题面收进 data/。"""
    folder = tmp_path / "我的比赛文件夹"
    folder.mkdir()
    (folder / "B题.pdf").write_bytes(b"%PDF-1.4")
    (folder / "附件").mkdir()
    (folder / "附件" / "数据.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (folder / "笔记.txt").write_text("我的笔记", encoding="utf-8")

    before = client.get("/api/state", params={"root": str(folder)}).json()
    assert before["manifest_ok"] is False  # 初始化前不是项目

    resp = client.post("/api/project/init", json={"root": str(folder)})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["manifest_ok"] is True
    assert payload["project_title"].endswith("-国赛B题")
    assert "B题.pdf" in payload["adopted_files"]
    assert (folder / "笔记.txt").read_text(encoding="utf-8") == "我的笔记"   # 原文件保留
    assert (folder / "data" / "B题.pdf").is_file()                          # 题面收进 data/
    ws = client.get("/api/workspaces").json()["workspaces"]
    assert any(w["root"] == str(folder) and w["is_project"] for w in ws)
