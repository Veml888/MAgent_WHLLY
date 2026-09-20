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
    assert "打开已有项目" in again.json()["detail"]


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
